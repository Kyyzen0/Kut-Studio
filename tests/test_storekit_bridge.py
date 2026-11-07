"""Le contrat Python du pont (``core/entitlements/storekit_bridge.py``) : réponses bornées, ABI, bibliothèque absente ou invalide, arrêt.

Les tests de la bibliothèque Swift réelle (compilation, chargement, arguments, arrêt) sautent hors macOS ou sans ``swiftc`` ; aucun
ne parle à StoreKit (pas de compte, pas d'achat). Les autres tournent partout, avec une fausse bibliothèque.
"""

from __future__ import annotations

import ast
import ctypes
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.entitlements import storekit_bridge as sb

ROOT = Path(__file__).resolve().parent.parent
SPIKE = ROOT / "packaging" / "store-spike" / "storekit"
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="StoreKit n'existe que sous macOS")


# -- fausse bibliothèque : lecture bornée et libération unique ----------------------------------------------------------------


class FakeLib:
    """Imite ``kutstore_free`` ; garde ce qui est libéré."""

    def __init__(self):
        self.freed = []

        class _Free:
            def __call__(inner, pointer):                       # noqa: N805
                self.freed.append(pointer.value)
        self.kutstore_free = _Free()


def take(text: bytes, status: int = sb.OK):
    lib = FakeLib()
    bridge = sb.StoreKitBridge(lib, ctypes.CDLL(None))
    buffer = ctypes.create_string_buffer(text)
    pointer = ctypes.c_void_p(ctypes.addressof(buffer))
    result = bridge._take(pointer, status)
    assert lib.freed == [ctypes.addressof(buffer)]               # libérée une fois, jamais deux
    return result


def test_take_decodes_and_frees_once():
    result = take(b'{"entitlements": [], "rejected": 0}')
    assert result.ok and result.payload == {"entitlements": [], "rejected": 0}


def test_take_on_a_null_pointer_is_empty_and_frees_nothing():
    lib = FakeLib()
    bridge = sb.StoreKitBridge(lib, ctypes.CDLL(None))
    assert bridge._take(ctypes.c_void_p(), sb.OK).payload == {} and lib.freed == []


def test_an_oversized_response_is_dropped_but_still_freed():
    result = take(b'{"x":"' + b"a" * (sb.MAX_RESPONSE_BYTES + 10) + b'"}')
    assert not result.ok and result.payload == {"error": "response_too_large"}


@pytest.mark.parametrize("raw", [b"pas du json", b"[1, 2]", b'"texte"', b"\xff\xfe{"])
def test_invalid_json_is_an_internal_error(raw):
    result = take(raw)
    assert result.status == sb.INTERNAL and result.payload == {"error": "invalid_json"}


def test_error_payloads_are_reduced_to_a_short_word_without_user_data():
    detail = '{"error": "storekit", "detail": "compte alice@example.com, produit com.kutstudio.secret"}'.encode()
    result = take(detail, sb.STOREKIT_ERROR)
    assert result.payload == {"error": "storekit"}
    weird = take(b'{"error": "alice@example.com the long sentence"}', sb.STOREKIT_ERROR)
    assert weird.payload == {"error": "error"}


def test_bridge_errors_have_fixed_messages(tmp_path):
    secret = tmp_path / "alice@example.com" / "libkutstore.dylib"
    with pytest.raises(sb.StoreBridgeError) as info:
        sb.StoreKitBridge.load(secret)
    assert "alice" not in str(info.value) and str(tmp_path) not in str(info.value)


def test_calls_are_bounded_before_the_native_boundary():
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("la frontière native ne doit pas être atteinte")
    bridge = sb.StoreKitBridge.__new__(sb.StoreKitBridge)
    bridge._lib = Boom()
    bridge._libc = None
    assert bridge.call(99).status == sb.BAD_ARGUMENT
    assert bridge.call(sb.OP_PRODUCTS, "x" * (sb.MAX_ARG_BYTES + 1)).payload == {"error": "bad_argument"}
    assert bridge.call(sb.OP_PRODUCTS, b"bytes").status == sb.BAD_ARGUMENT        # type: ignore[arg-type]


def test_python_constants_match_the_c_header():
    header = (SPIKE / "kutstore.h").read_text(encoding="utf-8")
    assert int(re.search(r"#define KUTSTORE_ABI_VERSION (\d+)", header).group(1)) == sb.ABI_VERSION
    names = dict(re.findall(r"KUTSTORE_([A-Z_]+)\s*=\s*(\d+)", header))
    for name in ("OK", "TIMEOUT", "BAD_ARGUMENT", "STOREKIT_ERROR", "UNVERIFIED", "SHUTDOWN", "INTERNAL", "POLL_TIMEOUT"):
        assert int(names[name]) == getattr(sb, name)
    for name in ("ENTITLEMENTS", "APP_TRANSACTION", "SYNC", "PRODUCTS", "START_UPDATES"):
        assert int(names["OP_" + name]) == getattr(sb, "OP_" + name)


def test_the_dylib_path_comes_from_the_bundle_never_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DYLD_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setenv("KUTSTORE_DYLIB", str(tmp_path / "evil.dylib"))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sb.bundled_dylib_path("/Applications/Kut-Studio.app/Contents/MacOS/Kut-Studio", frozen=False) is None
    path = sb.bundled_dylib_path("/Applications/Kut-Studio.app/Contents/MacOS/Kut-Studio", frozen=True)
    assert path == Path("/Applications/Kut-Studio.app/Contents/Frameworks/libkutstore.dylib")
    monkeypatch.setattr(sys, "platform", "linux")
    assert sb.bundled_dylib_path("/x/y/z", frozen=True) is None
    source = (ROOT / "core" / "entitlements" / "storekit_bridge.py").read_text(encoding="utf-8")
    names = {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
    assert not names & {"environ", "getenv"}                       # le module ne lit aucune variable d'environnement


# -- la bibliothèque Swift réelle ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dylib(tmp_path_factory) -> Path:
    if sys.platform != "darwin":
        pytest.skip("StoreKit n'existe que sous macOS")
    if shutil.which("swiftc") is None:
        pytest.skip("swiftc absent (outils Xcode non installés)")
    out = tmp_path_factory.mktemp("kutstore")
    done = subprocess.run(["sh", str(SPIKE / "build.sh"), str(out)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, f"build.sh a échoué :\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}"
    return out / "libkutstore.dylib"


@darwin_only
def test_real_dylib_loads_through_the_production_contract(dylib):
    bridge = sb.StoreKitBridge.load(dylib)
    assert bridge.abi_version == sb.ABI_VERSION
    assert bridge.call(99).status == sb.BAD_ARGUMENT
    assert bridge.call(sb.OP_PRODUCTS, "").status == sb.BAD_ARGUMENT                 # refusé côté Swift (opération qui exige un argument)
    assert bridge.call(sb.OP_PRODUCTS, ",".join(f"id{i}" for i in range(40))).status == sb.BAD_ARGUMENT
    assert bridge.poll_update(50).status == sb.POLL_TIMEOUT


@darwin_only
def test_real_dylib_other_abi_is_refused(tmp_path):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("compilateur C absent")
    source = tmp_path / "fake.c"
    source.write_text("int kutstore_abi_version(void) { return 99; }\n", encoding="utf-8")
    fake = tmp_path / "libfake.dylib"
    subprocess.run([cc, "-dynamiclib", str(source), "-o", str(fake)], check=True)
    with pytest.raises(sb.StoreBridgeError, match="version d'interface"):
        sb.StoreKitBridge.load(fake)


@darwin_only
def test_real_missing_invalid_or_incomplete_library_is_refused(tmp_path):
    with pytest.raises(sb.StoreBridgeError):
        sb.StoreKitBridge.load(tmp_path / "absent.dylib")
    (tmp_path / "pas_une_bibliotheque.dylib").write_bytes(b"not mach-o")
    with pytest.raises(sb.StoreBridgeError):
        sb.StoreKitBridge.load(tmp_path / "pas_une_bibliotheque.dylib")
    with pytest.raises(sb.StoreBridgeError):
        sb.StoreKitBridge.load("/usr/lib/libSystem.B.dylib")                         # existe, sans kutstore_abi_version


@darwin_only
def test_real_shutdown_is_idempotent_and_wakes_the_poller(dylib):
    script = (
        "import sys, threading, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from core.entitlements import storekit_bridge as c\n"
        f"b = c.StoreKitBridge.load({str(dylib)!r})\n"
        "box = {}\n"
        "t = threading.Thread(target=lambda: box.update(r=b.poll_update(60000)))\n"
        "t.start(); time.sleep(0.3)\n"
        "b.shutdown(); b.shutdown(); t.join(5)\n"
        "assert not t.is_alive(), 'le fil en attente n a pas été réveillé'\n"
        "assert box['r'].status == c.SHUTDOWN, box\n"
        "assert b.call(c.OP_ENTITLEMENTS).status == c.SHUTDOWN\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-500:]


@darwin_only
def test_real_dylib_without_account_yields_unknown_through_the_verifier(dylib, tmp_path):
    """Sans compte ni configuration StoreKit (cas CI / développement), le vérificateur ne publie rien (jamais un essai neuf)."""
    from core.entitlements.storekit_verifier import StoreKitVerifier
    from tests.entitlements_support import Clock

    bridge = sb.StoreKitBridge.load(dylib)
    outcome = StoreKitVerifier(tmp_path / "key", Clock()).fetch(bridge)
    if outcome.record is not None:                                                    # un compte de développement a répondu : pas un essai neuf
        assert outcome.record.trial_started_at is None or outcome.record.trial_started_at > 1_167_609_600
    else:
        assert outcome.reason in {"storekit_unavailable", "untrusted_trial_date", "unverified_transaction"}
