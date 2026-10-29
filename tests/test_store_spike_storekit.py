"""Spike ADR-0013 étape 13 : le pont StoreKit 2 (Swift + ctypes) se compile, se charge et tient son contrat C.

Tourne en CI macOS SANS compte Apple ni StoreKit : seules les voies qui ne parlent pas à storekitd sont couvertes
(version d'interface, arguments refusés, arrêt). Les appels StoreKit réels sont dans packaging/store-spike/storekit/XCTestHost
(XCTest + SKTestSession), sautés ici sauf demande explicite.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parent.parent / "packaging" / "store-spike" / "storekit"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="StoreKit n'existe que sous macOS")


def _contract():
    spec = importlib.util.spec_from_file_location("storekit_contract", SPIKE / "storekit_contract.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["storekit_contract"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dylib(tmp_path_factory) -> Path:
    if shutil.which("swiftc") is None:
        pytest.skip("swiftc absent (outils Xcode non installés)")
    out = tmp_path_factory.mktemp("kutstore")
    done = subprocess.run(["sh", str(SPIKE / "build.sh"), str(out)], capture_output=True, text=True, timeout=300)
    # Seule l'absence de chaîne d'outils justifie un saut ; une compilation qui échoue est une régression à voir.
    assert done.returncode == 0, f"build.sh a échoué (code {done.returncode}) :\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    return out / "libkutstore.dylib"


def test_header_and_python_contract_agree():
    header = (SPIKE / "kutstore.h").read_text(encoding="utf-8")
    contract = _contract()
    assert int(re.search(r"#define KUTSTORE_ABI_VERSION (\d+)", header).group(1)) == contract.ABI_VERSION
    statuses = dict(re.findall(r"KUTSTORE_([A-Z_]+)\s*=\s*(\d+)", header))
    for name in ("OK", "TIMEOUT", "BAD_ARGUMENT", "STOREKIT_ERROR", "UNVERIFIED", "SHUTDOWN", "INTERNAL", "POLL_TIMEOUT"):
        assert int(statuses[name]) == getattr(contract, name)
    for name in ("ENTITLEMENTS", "APP_TRANSACTION", "SYNC", "PRODUCTS", "START_UPDATES"):
        assert int(statuses["OP_" + name]) == getattr(contract, "OP_" + name)


def test_swift_dylib_loads_and_reports_the_abi_version(dylib):
    contract = _contract()
    bridge = contract.StoreBridge(dylib)
    assert bridge.abi_version == contract.ABI_VERSION


def test_dynamic_dependencies_are_system_only(dylib):
    deps = subprocess.run(["otool", "-L", str(dylib)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    allowed = ("@rpath/libkutstore.dylib", "/usr/lib/", "/System/Library/")
    assert all(line.strip().startswith(allowed) for line in deps), deps      # aucune dépendance embarquée à signer en plus


def test_other_abi_version_is_refused(tmp_path):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("compilateur C absent")
    source = tmp_path / "fake.c"
    source.write_text("int kutstore_abi_version(void) { return 99; }\n", encoding="utf-8")
    fake = tmp_path / "libfake.dylib"
    subprocess.run([cc, "-dynamiclib", str(source), "-o", str(fake)], check=True)
    contract = _contract()
    with pytest.raises(contract.StoreBridgeError, match="ABI 99"):
        contract.StoreBridge(fake)


def test_missing_library_or_symbol_is_a_bridge_error(tmp_path):
    contract = _contract()
    with pytest.raises(contract.StoreBridgeError):
        contract.StoreBridge(tmp_path / "absent.dylib")
    with pytest.raises(contract.StoreBridgeError):
        contract.StoreBridge("/usr/lib/libSystem.B.dylib")                  # existe, mais sans kutstore_abi_version


def test_bad_arguments_are_refused_before_storekit_is_called(dylib):
    contract = _contract()
    bridge = contract.StoreBridge(dylib)
    assert bridge.call(99)[0] == contract.BAD_ARGUMENT                       # opération inconnue
    assert bridge.call(contract.OP_PRODUCTS, "")[0] == contract.BAD_ARGUMENT
    assert bridge.call(contract.OP_PRODUCTS, ",".join(f"id{i}" for i in range(40)))[0] == contract.BAD_ARGUMENT
    status, payload = bridge.call(contract.OP_PRODUCTS, "x" * (contract.MAX_ARG_BYTES + 1))   # refusé côté Python, avant la frontière
    assert status == contract.BAD_ARGUMENT and "x" * 50 not in str(payload)                   # l'entrée n'est pas recopiée


def test_shutdown_wakes_a_waiting_poller_and_refuses_later_calls(dylib):
    # Dans un sous-processus : l'état d'arrêt du pont est global au processus.
    script = (
        "import sys, threading, time\n"
        f"sys.path.insert(0, {str(SPIKE)!r})\n"
        "import storekit_contract as c\n"
        f"b = c.StoreBridge({str(dylib)!r})\n"
        "box = {}\n"
        "t = threading.Thread(target=lambda: box.update(r=b.poll_update(60000)))\n"
        "t.start(); time.sleep(0.3)\n"
        "b.shutdown(); t.join(5)\n"
        "assert not t.is_alive(), 'le fil en attente n a pas été réveillé'\n"
        "assert box['r'][0] == c.SHUTDOWN, box\n"
        "assert b.call(c.OP_ENTITLEMENTS)[0] == c.SHUTDOWN\n"
        "b.shutdown()\n"                                                       # idempotent
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-500:]


def test_poll_times_out_without_events(dylib):
    contract = _contract()
    bridge = contract.StoreBridge(dylib)
    status, _ = bridge.poll_update(50)
    assert status == contract.POLL_TIMEOUT


def test_swift_side_bounds_an_oversized_argument_by_itself(dylib):
    # On contourne la garde Python : la frontière C doit se défendre seule, même pour une opération qui ignore son argument.
    import ctypes

    contract = _contract()
    bridge = contract.StoreBridge(dylib)
    out = ctypes.c_void_p()
    status = bridge._lib.kutstore_call(contract.OP_ENTITLEMENTS, b"x" * 9000, 1000, ctypes.byref(out))
    payload = bridge._take(out)
    assert status == contract.BAD_ARGUMENT, payload
