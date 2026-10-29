"""``libkutscope.dylib`` (Swift) se compile, se charge et tient son contrat C (ADR-0013, étape 5).

Sans bac à sable (CI) : on exerce le vrai code Swift hors sandbox, ce qui prouve la compilation, l'ABI, les bornes, la libération
mémoire et le comptage des accès ; l'effet du sandbox lui-même ne se mesure que dans une construction signée (docs/sandbox-access.md).
Seule l'absence de ``swiftc`` justifie un saut : une compilation qui échoue est une régression.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.security_scope import bridge as sb

SCOPE = Path(__file__).resolve().parent.parent / "packaging" / "store-spike" / "scope"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="les signets de sécurité n'existent que sous macOS")


@pytest.fixture(scope="module")
def dylib(tmp_path_factory) -> Path:
    if shutil.which("swiftc") is None:
        pytest.skip("swiftc absent (outils Xcode non installés)")
    out = tmp_path_factory.mktemp("kutscope")
    done = subprocess.run(["sh", str(SCOPE / "build.sh"), str(out)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, f"build.sh a échoué (code {done.returncode}) :\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    return out / sb.LIBRARY_NAME


@pytest.fixture
def bridge(dylib):
    return sb.ScopeBridge.load(dylib)


def test_header_and_python_contract_agree():
    header = (SCOPE / "kutscope.h").read_text(encoding="utf-8")
    assert int(re.search(r"#define KUTSCOPE_ABI_VERSION (\d+)", header).group(1)) == sb.ABI_VERSION
    statuses = dict(re.findall(r"KUTSCOPE_([A-Z_]+)\s*=\s*(\d+)", header))
    assert {name: int(value) for name, value in statuses.items()} == {
        "OK": sb.OK, "BAD_ARGUMENT": sb.BAD_ARGUMENT, "DENIED": sb.DENIED, "UNRESOLVABLE": sb.UNRESOLVABLE, "INTERNAL": sb.INTERNAL}


def test_kutstore_abi_is_untouched():
    header = (SCOPE.parent / "storekit" / "kutstore.h").read_text(encoding="utf-8")
    assert re.search(r"#define KUTSTORE_ABI_VERSION 1\b", header)           # la nouvelle bibliothèque n'a pas touché l'ABI 1 de kutstore


def test_dylib_loads_and_reports_the_abi(bridge):
    assert bridge.abi_version == sb.ABI_VERSION and bridge.open_count() == 0


def test_dynamic_dependencies_are_system_only(dylib):
    deps = subprocess.run(["otool", "-L", str(dylib)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    assert all(line.strip().startswith(("@rpath/libkutscope.dylib", "/usr/lib/", "/System/Library/")) for line in deps), deps


def test_bookmark_open_close_roundtrip_and_counter(bridge, tmp_path):
    target = tmp_path / "clip.mov"
    target.write_bytes(b"x")
    bookmark = bridge.create_bookmark(str(target))
    assert bookmark and bookmark.isascii()
    status, opened = bridge.open(bookmark)
    assert status == sb.OK and opened is not None
    assert Path(opened.path).resolve() == target.resolve() and opened.stale is False and opened.refreshed is None
    assert bridge.open_count() == 1
    assert bridge.close(opened.handle) is True and bridge.open_count() == 0
    assert bridge.close(opened.handle) is False                              # fermé une seule fois


def test_open_balances_with_several_handles(bridge, tmp_path):
    target = tmp_path / "a.wav"
    target.write_bytes(b"x")
    bookmark = bridge.create_bookmark(str(target))
    handles = [bridge.open(bookmark)[1].handle for _ in range(3)]            # type: ignore[union-attr]
    assert len(set(handles)) == 3 and bridge.open_count() == 3
    for handle in handles:
        assert bridge.close(handle)
    assert bridge.open_count() == 0


def test_missing_file_has_no_bookmark_and_a_deleted_file_is_unresolvable(bridge, tmp_path):
    assert bridge.create_bookmark(str(tmp_path / "absent")) is None
    target = tmp_path / "gone.mov"
    target.write_bytes(b"x")
    bookmark = bridge.create_bookmark(str(target))
    target.unlink()
    status, opened = bridge.open(bookmark)
    assert opened is None and status == sb.UNRESOLVABLE and bridge.open_count() == 0


def test_inputs_are_bounded_and_garbage_is_refused(bridge):
    assert bridge.create_bookmark("") is None
    assert bridge.create_bookmark("/" + "a" * (sb.MAX_PATH_BYTES + 1)) is None
    assert bridge.create_bookmark("/tmp/x\0y") is None
    assert bridge.open("")[0] == sb.BAD_ARGUMENT
    assert bridge.open("A" * (sb.MAX_BOOKMARK_CHARS + 1))[0] == sb.BAD_ARGUMENT
    assert bridge.open("pas du base64 !!")[0] == sb.BAD_ARGUMENT
    assert bridge.open("aGVsbG8=")[0] == sb.UNRESOLVABLE                      # du base64 qui n'est pas un signet
    assert bridge.open_count() == 0


def _compile_c(tmp_path, name: str, source: str) -> Path:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("compilateur C absent")
    path = tmp_path / f"{name}.c"
    path.write_text(source, encoding="utf-8")
    out = tmp_path / f"lib{name}.dylib"
    subprocess.run([cc, "-dynamiclib", str(path), "-o", str(out)], check=True)
    return out


def test_other_abi_version_is_refused(tmp_path):
    fake = _compile_c(tmp_path, "abi99", "int kutscope_abi_version(void) { return 99; }\n")
    with pytest.raises(sb.ScopeBridgeError, match="différente"):
        sb.ScopeBridge.load(fake)


def test_library_without_symbols_or_missing_file_is_a_bridge_error(tmp_path):
    with pytest.raises(sb.ScopeBridgeError, match="absente"):
        sb.ScopeBridge.load(tmp_path / "nulle.dylib")
    no_version = _compile_c(tmp_path, "nover", "int autre(void) { return 1; }\n")
    with pytest.raises(sb.ScopeBridgeError, match="sans version"):
        sb.ScopeBridge.load(no_version)
    incomplete = _compile_c(tmp_path, "incomplete", "int kutscope_abi_version(void) { return 1; }\n")
    with pytest.raises(sb.ScopeBridgeError, match="incomplète"):
        sb.ScopeBridge.load(incomplete)
    garbage = tmp_path / "garbage.dylib"
    garbage.write_bytes(b"pas une bibliotheque")
    with pytest.raises(sb.ScopeBridgeError, match="non chargeable") as raised:
        sb.ScopeBridge.load(garbage)
    assert str(tmp_path) not in str(raised.value)                              # aucun chemin dans le message


def test_bridge_errors_never_carry_the_path(tmp_path):
    secret = tmp_path / "Mes Vacances Privées.dylib"
    with pytest.raises(sb.ScopeBridgeError) as raised:
        sb.ScopeBridge.load(secret)
    assert "Vacances" not in str(raised.value)


def test_a_real_manager_over_the_real_dylib(dylib, tmp_path):
    """Gestionnaire + vrai pont : création, résolution au lancement, accès compté et refermé."""
    from core.security_scope.manager import ScopeManager
    from core.security_scope.store import BookmarkStore

    target = tmp_path / "real.mov"
    target.write_bytes(b"x")
    real = sb.ScopeBridge.load(dylib)
    manager = ScopeManager(real, BookmarkStore(tmp_path / "container"))
    assert manager.remember(str(target))
    assert manager.resolve_all().resolved == 1 and real.open_count() == 0
    with manager.access(str(target)) as report:
        assert report.opened and real.open_count() == 1
    assert real.open_count() == 0
