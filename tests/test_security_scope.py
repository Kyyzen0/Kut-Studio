"""Gestionnaire des signets à portée de sécurité (ADR-0013, étape 5) : création, résolution, périmé, compteur d'accès, exception."""

from __future__ import annotations

import json
import os
import sys

import pytest

from core import build_flavor, security_scope
from core.security_scope import bridge as sb
from core.security_scope import store as store_module
from core.security_scope.manager import ScopeManager
from core.security_scope.store import BookmarkStore, canonical, covers
from tests.scope_fakes import FakeScopeBridge, make_manager


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "user"
    folder.mkdir()
    files = []
    for name in ("a.mov", "b.mov"):
        path = folder / name
        path.write_bytes(b"x")
        files.append(str(path))
    return folder, files


@pytest.fixture
def store_mode(monkeypatch):
    monkeypatch.setattr(build_flavor, "STORE", True)
    yield
    security_scope.install_manager(None)


# -- création et persistance ---------------------------------------------------------------------------------------------------


def test_remember_creates_and_persists_a_bookmark_in_the_container(tmp_path, media):
    _folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    assert manager.remember(files[0]) is True
    assert bridge.created == [canonical(files[0])]
    reread = BookmarkStore(tmp_path / "container")                      # une autre instance : c'est bien le fichier qui parle
    assert [entry.path for entry in reread.entries()] == [canonical(files[0])]
    raw = json.loads((tmp_path / "container" / store_module.FILE_NAME).read_text(encoding="utf-8"))
    assert raw["version"] == 1 and list(raw["entries"]) == [canonical(files[0])]


def test_remember_fails_without_a_bookmark_and_never_stores_garbage(tmp_path):
    manager, bridge, store = make_manager(tmp_path)
    assert manager.remember(str(tmp_path / "absent.mov")) is False        # le pont ne peut pas signer un chemin inexistant
    assert manager.remember("") is False and manager.remember("a\0b") is False
    assert store.entries() == []


def test_a_folder_bookmark_covers_its_children_without_new_entries(tmp_path, media):
    folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    assert manager.remember(str(folder)) and manager.remember(files[0]) and manager.remember(files[1])
    assert [entry.path for entry in store.entries()] == [canonical(str(folder))]
    with manager.access(files[0], files[1]) as report:
        assert set(report.opened) == {canonical(files[0]), canonical(files[1])} and not report.needs_relink
        assert manager.open_count == 1 and bridge.open_count() == 1             # un seul accès natif pour le dossier


def test_covers_compares_components_not_text_prefixes():
    assert covers("/a/b", "/a/b/c") and covers("/a/b", "/a/b") and not covers("/a/b", "/a/bc") and not covers("/a/b/c", "/a/b")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="liens symboliques : droits particuliers sous Windows")
def test_canonical_rejects_invalid_paths(tmp_path):
    for bad in ("", "  ", "x\0y", "a" * (store_module.MAX_PATH_CHARS + 1)):
        with pytest.raises(ValueError):
            canonical(bad)
    link = tmp_path / "link"
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", link)
    assert canonical(str(link)) == canonical(str(tmp_path / "real"))


def test_store_is_bounded(tmp_path, monkeypatch):
    store = BookmarkStore(tmp_path)
    monkeypatch.setattr(store_module, "MAX_ENTRIES", 2)
    assert store.put("/x/1", "m1") and store.put("/x/2", "m2")
    assert store.put("/x/3", "m3") is False                              # table pleine : rien n'est évincé en silence
    assert store.put("/x/1", "m1bis") is True                            # remplacer reste possible
    assert store.put("/x/9", "z" * (store_module.MAX_BOOKMARK_CHARS + 1)) is False
    assert [entry.path for entry in store.entries()] == [canonical("/x/1"), canonical("/x/2")]


def test_a_huge_or_corrupt_store_file_reads_as_empty_and_is_not_rewritten(tmp_path):
    folder = tmp_path / "container"
    folder.mkdir()
    target = folder / store_module.FILE_NAME
    target.write_bytes(b"x" * (store_module.MAX_FILE_BYTES + 1))
    store = BookmarkStore(folder)
    assert store.entries() == [] and target.stat().st_size == store_module.MAX_FILE_BYTES + 1
    target.write_text("{pas du json", encoding="utf-8")
    assert store.entries() == []
    target.write_text(json.dumps({"version": 1, "entries": {"/a": {"bookmark": 5}, "/b": {"bookmark": "ok"}, "": {"bookmark": "z"}}}))
    assert [entry.path for entry in store.entries()] == ["/b"]           # les entrées mal formées sont ignorées une à une


# -- accès : ouverture, fermeture, compteur, exception -----------------------------------------------------------------------


def test_access_opens_then_closes_and_counts(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    assert manager.open_count == 0
    with manager.access(files[0]) as report:
        assert report.opened == (canonical(files[0]),) and manager.open_count == 1 and bridge.open_count() == 1
    assert manager.open_count == 0 and bridge.open_count() == 0 and len(bridge.closed_log) == 1


def test_access_closes_on_exception(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    with pytest.raises(RuntimeError, match="boum"):
        with manager.access(files[0]):
            assert bridge.open_count() == 1
            raise RuntimeError("boum")
    assert bridge.open_count() == 0 and manager.open_count == 0


def test_nested_and_concurrent_readers_share_one_native_access(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    with manager.access(files[0]):
        with manager.access(files[0]):
            assert bridge.open_count() == 1 and len(bridge.opened_log) == 1
        assert bridge.open_count() == 1                                      # le lecteur extérieur n'a pas perdu son accès
    assert bridge.open_count() == 0


def test_a_path_without_bookmark_is_reported_not_raised(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    with manager.access(files[0], "", "x\0y") as report:                       # les chemins invalides sont ignorés
        assert report.missing == (canonical(files[0]),) and report.needs_relink == (canonical(files[0]),)
        assert bridge.open_count() == 0


def test_denied_and_unresolvable_bookmarks_are_lost_and_unresolvable_ones_are_dropped(tmp_path, media):
    _folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    manager.remember(files[0])
    manager.remember(files[1])
    bridge.denied.add(canonical(files[0]))
    bridge.unresolvable.add(canonical(files[1]))
    with manager.access(*files) as report:
        assert set(report.lost) == {canonical(files[0]), canonical(files[1])} and bridge.open_count() == 0
    assert [entry.path for entry in store.entries()] == [canonical(files[0])]    # le signet qui ne désigne plus rien est retiré


def test_failure_halfway_closes_what_was_opened(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    manager.remember(files[1])
    with pytest.raises(ValueError):
        with manager.access(files[0], files[1]):
            assert bridge.open_count() == 2
            raise ValueError
    assert bridge.open_count() == 0


def test_close_failure_never_masks_the_body_exception(tmp_path, media, caplog):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    bridge.close = lambda handle: (_ for _ in ()).throw(OSError("secret " + files[0]))   # type: ignore[method-assign]
    with pytest.raises(KeyError):
        with manager.access(files[0]):
            raise KeyError("corps")
    assert files[0] not in caplog.text                                       # le chemin ne fuit pas dans le journal


def test_no_bridge_means_no_access_and_no_bypass(tmp_path, media):
    _folder, files = media
    manager = ScopeManager(None, BookmarkStore(tmp_path / "container"))
    assert manager.available is False and manager.remember(files[0]) is False
    with manager.access(files[0]) as report:
        assert report.opened == () and report.missing == (canonical(files[0]),)
    assert manager.resolve_all().resolved == 0


# -- périmé, résolution au lancement -----------------------------------------------------------------------------------------


def test_stale_bookmark_is_refreshed_silently_at_launch(tmp_path, media):
    _folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    manager.remember(files[0])
    old = store.entries()[0].bookmark
    bridge.stale.add(canonical(files[0]))
    report = manager.resolve_all()
    assert (report.resolved, report.refreshed, report.lost, report.stale) == (1, 1, (), ())
    assert store.entries()[0].bookmark != old and bridge.open_count() == 0


def test_stale_bookmark_that_cannot_be_refreshed_is_reported_for_reselection(tmp_path, media):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    manager.remember(files[0])
    bridge.stale.add(canonical(files[0]))
    bridge.refresh = False
    report = manager.resolve_all()
    assert report.stale == (canonical(files[0]),) and manager.stale_paths() == (canonical(files[0]),)
    manager.remember(files[0])                                               # l'utilisateur rechoisit : un signet neuf, plus de périmé
    assert manager.stale_paths() == ()


def test_launch_reports_lost_bookmarks_and_closes_every_probe_access(tmp_path, media):
    _folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    manager.remember(files[0])
    manager.remember(files[1])
    bridge.unresolvable.add(canonical(files[1]))
    report = manager.resolve_all()
    assert report.resolved == 1 and report.lost == (canonical(files[1]),) and bridge.open_count() == 0
    assert [entry.path for entry in store.entries()] == [canonical(files[0])]


def test_stale_bookmark_met_during_access_is_refreshed(tmp_path, media):
    _folder, files = media
    manager, bridge, store = make_manager(tmp_path)
    manager.remember(files[0])
    old = store.entries()[0].bookmark
    bridge.stale.add(canonical(files[0]))
    with manager.access(files[0]) as report:
        assert report.opened
    assert store.entries()[0].bookmark != old


# -- hors Store : rien ne change ---------------------------------------------------------------------------------------------


def test_outside_the_store_the_facade_is_inert(tmp_path, media, monkeypatch):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    security_scope.install_manager(manager)
    try:
        monkeypatch.setattr(build_flavor, "STORE", False)
        assert security_scope.current_manager() is None
        assert security_scope.remember_user_path(files[0]) is False and bridge.created == []
        with security_scope.scoped_access(files[0]) as report:
            assert report is security_scope.EMPTY_REPORT and bridge.opened_log == []
        with security_scope.command_scope(["ffmpeg", "-i", files[0]]), security_scope.plan_scope(object(), files[0]):
            assert bridge.opened_log == []
        assert security_scope.start_for_store(tmp_path, bridge_loader=lambda: pytest.fail("pont chargé hors Store")).resolved == 0
    finally:
        security_scope.install_manager(None)


def test_in_the_store_the_facade_uses_the_installed_manager(tmp_path, media, store_mode):
    _folder, files = media
    manager, bridge, _store = make_manager(tmp_path)
    security_scope.install_manager(manager)
    assert security_scope.remember_user_path(files[0]) is True
    with security_scope.scoped_access(files[0], None, "") as report:
        assert report.opened == (canonical(files[0]),) and bridge.open_count() == 1
    assert bridge.open_count() == 0


def test_start_for_store_without_a_bridge_installs_a_manager_that_opens_nothing(tmp_path, media, store_mode):
    _folder, files = media

    def missing():
        raise sb.ScopeBridgeError("absente")

    report = security_scope.start_for_store(tmp_path, bridge_loader=missing)
    manager = security_scope.current_manager()
    assert manager is not None and manager.available is False and report.resolved == 0
    assert security_scope.remember_user_path(files[0]) is False


def test_start_for_store_resolves_stored_bookmarks_with_the_loaded_bridge(tmp_path, media, store_mode):
    _folder, files = media
    store = BookmarkStore(tmp_path)
    store.put(files[0], "bm:1:" + canonical(files[0]))
    bridge = FakeScopeBridge()
    report = security_scope.start_for_store(tmp_path, bridge_loader=lambda: bridge)       # type: ignore[arg-type,return-value]
    assert report.resolved == 1 and bridge.open_count() == 0


def test_bundled_dylib_path_is_fixed_and_ignores_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DYLD_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setenv("KUT_STUDIO_SCOPE_LIB", str(tmp_path / "x.dylib"))
    assert sb.bundled_dylib_path(frozen=False) is None
    path = sb.bundled_dylib_path("/Applications/Kut-Studio.app/Contents/MacOS/Kut-Studio", frozen=True)
    if sys.platform == "darwin":
        assert path is not None and path.name == "libkutscope.dylib" and path.parent.name == "Frameworks"
    else:
        assert path is None
