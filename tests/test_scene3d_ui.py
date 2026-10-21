"""Interface d'un clip « scène 3D » (lot B3) : menu, glisser-déposer, aperçu du moniteur, sonde GPU, inspecteur, niveaux d'usage.

Fenêtre réelle hors écran (``tests/main_window_harness.py``) : boîtes de dialogue enregistrées, configuration isolée. Le moniteur
est noir dans une capture hors écran (QtMultimedia) : l'aperçu se prouve par le chemin ``MographRenderer`` / ``kut3d.render_frame``
(espion) et par l'état du badge, pas par une capture.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
from PySide6.QtWidgets import QFileDialog

from core import scene3d, scene3d_interactive
from core.graphics import GraphicType
from core.mograph_layers import layer_clips
from core.usage_level import UsageLevel
from tests.main_window_harness import build_window, install_dialogs, menu_action
from tests.scene3d_preview_support import Spy
from tests.scene3d_render_samples import write_static_scene
from ui import i18n
from ui.i18n_scene3d import SCENE3D_TRANSLATIONS
from ui.main_window_mixins.menu_levels import MENU_GATES
from ui.scene3d_preview import BADGE_NAME, GpuProbe, controller_of

KEY = "menu.item.add_scene3d"


@pytest.fixture(autouse=True)
def _fresh_caches():
    scene3d.clear_signature_cache()
    scene3d_interactive.clear_preview_cache()
    scene3d_interactive.set_backend_provider(None)
    yield
    scene3d_interactive.set_backend_provider(None)
    scene3d_interactive.clear_preview_cache()
    scene3d.clear_signature_cache()


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.resize(1400, 900)
    win.show()
    return win


@pytest.fixture
def scene(tmp_path) -> Path:
    folder = tmp_path / "scene"
    folder.mkdir()
    return write_static_scene(folder)


def _scene_clips(win):
    return [
        clip
        for _i, _t, clip in layer_clips(win.project)
        if clip.graphic.type == GraphicType.SCENE3D
    ]


def _pick(monkeypatch, path):
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(path), "")
    )


class _Drop:
    """Un évènement de dépôt minimal : la liste des fichiers et le point (coordonnées de la fenêtre)."""

    def __init__(self, paths, point: QPoint) -> None:
        self._mime = QMimeData()
        self._mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        self._point = point

    def mimeData(self):
        return self._mime

    def position(self):
        return QPointF(self._point)

    def acceptProposedAction(self):
        pass


def _graphics_track_first(win):
    """Ajoute un titre (donc une piste G) et remonte cette piste en tête : sa ligne est visible dans la petite timeline hors écran."""
    win.add_layer_at_playhead("text", "")
    track = next(t for t in win.project.tracks if t.type == "graphics")
    win.project.tracks.remove(track)
    win.project.tracks.insert(0, track)
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    return track


def _timeline_point(win, track_id: str, seconds: float) -> QPoint:
    """Point de la fenêtre qui est sur la piste ``track_id`` à ``seconds`` de la timeline."""
    timeline = win.timeline_panel
    index = next(
        i for i, track in enumerate(win.project.tracks) if track.id == track_id
    )
    scale = timeline.pixels_per_second * timeline.zoom
    in_grid = QPoint(
        int(timeline.left_margin + scale * seconds), int(timeline.row_top(index)) + 4
    )
    return timeline.mapTo(win, timeline.timeline_grid.mapTo(timeline, in_grid))


# --- (a) Menu Calques > Ajouter > Scène 3D… ---------------------------------------------------------------------------------


def test_the_menu_entry_is_a_studio_item_of_the_add_submenu(window):
    gate = MENU_GATES["layers_menu"]["common.add>"]
    assert gate[1][KEY] is UsageLevel.STUDIO
    action = menu_action(window, KEY)
    assert action.shortcut().isEmpty()  # pas de raccourci
    layers = next(
        a.menu()
        for a in window.menuBar().actions()
        if a.menu() and a.menu().objectName() == "layers_menu"
    )
    add_menu = next(a.menu() for a in layers.actions() if a.menu() is not None)
    window.set_usage_level("simple")
    assert action not in add_menu.actions()
    window.set_usage_level("studio")
    assert action in add_menu.actions()
    window.set_usage_level("advanced")
    assert action in add_menu.actions()


def test_the_menu_adds_a_scene_clip_at_the_playhead_and_it_can_be_undone(
    window, scene, monkeypatch
):
    window.playhead_seconds = 2.0
    entries = len(window.history)
    _pick(monkeypatch, scene)
    menu_action(window, KEY).trigger()
    (clip,) = _scene_clips(window)
    track = next(t for t in window.project.tracks if t.id == clip.track_id)
    assert (
        track.type == "graphics"
        and clip.timeline_start == 2.0
        and clip.graphic.source_path == str(scene.resolve())
    )
    assert len(window.history) == entries + 1  # UNE entrée d'historique
    assert (
        window.properties_panel.graphics_group.isEnabled()
    )  # sélectionné, inspecteur ouvert
    window.undo_last()
    assert _scene_clips(window) == []
    window.redo_last()
    assert len(_scene_clips(window)) == 1


def test_cancelling_the_file_dialog_creates_nothing(window, monkeypatch):
    entries = len(window.history)
    _pick(monkeypatch, "")
    menu_action(window, KEY).trigger()
    assert _scene_clips(window) == [] and len(window.history) == entries


def test_an_invalid_scene_file_gives_a_clear_message_and_creates_nothing(
    window, tmp_path, monkeypatch
):
    dialogs = install_dialogs(monkeypatch)
    bad = tmp_path / "bad.kut3d"
    bad.write_text("{ pas du json")
    entries, tracks = len(window.history), [t.id for t in window.project.tracks]
    _pick(monkeypatch, bad)
    menu_action(window, KEY).trigger()
    assert _scene_clips(window) == [] and len(window.history) == entries
    assert [t.id for t in window.project.tracks] == tracks  # pas même une piste G
    ((title, text),) = dialogs.of_kind("warning")
    assert "bad.kut3d" in text or "scène" in text.lower()
    assert len(text) <= 400
    assert window.statusBar().currentMessage() == text


def test_a_missing_scene_file_and_a_wrong_extension_are_refused(
    window, tmp_path, monkeypatch
):
    dialogs = install_dialogs(monkeypatch)
    other = tmp_path / "model.obj"
    other.write_text("v 0 0 0\n")
    for path in (tmp_path / "absent.kut3d", other):
        _pick(monkeypatch, path)
        menu_action(window, KEY).trigger()
    assert _scene_clips(window) == [] and len(dialogs.of_kind("warning")) == 2


def test_a_huge_error_message_is_cut(window, monkeypatch):
    from ui.scene3d_actions import _message

    assert len(_message("x" * 100000)) == 300 and "\n" not in _message("a\nb")


# --- (b) Glisser-déposer ----------------------------------------------------------------------------------------------------


def test_dropping_a_scene_file_on_the_timeline_creates_the_clip_where_it_was_dropped(
    window, scene
):
    track = _graphics_track_first(window)
    window.playhead_seconds = 9.0
    window.dropEvent(_Drop([scene], _timeline_point(window, track.id, 3.0)))
    (clip,) = _scene_clips(window)
    assert clip.track_id == track.id and clip.timeline_start == pytest.approx(
        3.0, abs=0.2
    )


def test_dropping_a_scene_elsewhere_uses_the_playhead(window, scene):
    window.playhead_seconds = 4.0
    window.dropEvent(_Drop([scene], QPoint(2, 2)))
    (clip,) = _scene_clips(window)
    assert clip.timeline_start == 4.0


def test_dropping_on_a_video_track_puts_the_scene_on_a_graphics_track_at_that_time(
    window, scene
):
    window.dropEvent(_Drop([scene], _timeline_point(window, "V1", 2.0)))
    (clip,) = _scene_clips(window)
    assert clip.track_id != "V1" and clip.timeline_start == pytest.approx(2.0, abs=0.2)
    assert (
        next(t for t in window.project.tracks if t.id == clip.track_id).type
        == "graphics"
    )


def test_dropping_on_a_locked_graphics_track_is_refused(window, scene):
    track = _graphics_track_first(window)
    track.locked = True
    before = len(window.history)
    window.dropEvent(_Drop([scene], _timeline_point(window, track.id, 3.0)))
    assert _scene_clips(window) == [] and len(window.history) == before
    assert track.name in window.statusBar().currentMessage()


def test_another_extension_is_left_to_the_media_import(
    window, scene, tmp_path, monkeypatch
):
    imported = []
    monkeypatch.setattr(window, "import_video_to_project", imported.append)
    clip = tmp_path / "movie.mp4"
    clip.write_bytes(b"")
    window.dropEvent(_Drop([clip, scene], QPoint(2, 2)))
    # Qt donne le chemin d'un fichier déposé avec des « / » même sous Windows (« C:/Users/… ») : on compare des chemins, pas des chaînes.
    assert [Path(item) for item in imported] == [
        clip
    ]  # la scène n'est PAS importée comme un média
    assert len(_scene_clips(window)) == 1


def test_dropping_an_invalid_scene_says_so_and_creates_nothing(
    window, tmp_path, monkeypatch
):
    imported = []
    monkeypatch.setattr(window, "import_video_to_project", imported.append)
    dialogs = install_dialogs(monkeypatch)
    bad = tmp_path / "bad.kut3d"
    bad.write_text("nope")
    window.dropEvent(_Drop([bad], QPoint(2, 2)))
    assert (
        _scene_clips(window) == [] and imported == [] and dialogs.shown == []
    )  # pas de boîte bloquante pour un dépôt
    assert window.statusBar().currentMessage()


# --- (c) Aperçu du moniteur -------------------------------------------------------------------------------------------------


def _with_scene(window, scene, monkeypatch, start=0.0):
    _pick(monkeypatch, scene)
    window.playhead_seconds = start
    menu_action(window, KEY).trigger()
    (clip,) = _scene_clips(window)
    return clip


def test_the_monitor_renders_the_still_image_when_stopped_and_never_while_playing(
    window, scene, monkeypatch
):
    _with_scene(window, scene, monkeypatch)
    spy = Spy(monkeypatch)
    scene3d.clear_signature_cache()
    scene3d_interactive.clear_preview_cache()
    window.is_playing = True
    window._refresh_viewer_graphics()
    assert spy.calls == []  # aucun rendu 3D en lecture
    window.is_playing = False
    window._refresh_viewer_graphics()
    assert len(spy.calls) == 1 and max(spy.calls[0]["size"]) <= scene3d.INTERACTIVE_SIDE
    window.is_playing = True
    window._refresh_viewer_graphics()
    assert len(spy.calls) == 1  # l'image figée est resservie, sans nouveau rendu


def test_the_badge_shows_while_the_still_image_is_on_screen_and_hides_otherwise(
    window, scene, monkeypatch
):
    _with_scene(window, scene, monkeypatch, start=1.0)
    panel = window.preview_panel
    window.playhead_seconds = 2.0
    window._refresh_viewer_graphics()
    badge = panel.findChild(type(panel.scene3d_badge), BADGE_NAME)
    assert badge is panel.scene3d_badge and not badge.isHidden()
    assert badge.text() == i18n.translate("scene3d.preview.approximate")
    window.playhead_seconds = 0.5  # avant le clip : plus de 3D à l'écran
    window._refresh_viewer_graphics()
    assert badge.isHidden()
    window.playhead_seconds = 2.0
    window._refresh_viewer_graphics()
    assert not badge.isHidden()
    window._viewer_composited = (
        True  # un segment fidèle prend le relais : plus d'approximation
    )
    window._refresh_viewer_graphics()
    assert badge.isHidden()


def test_a_hidden_track_or_a_disabled_clip_shows_no_badge(window, scene, monkeypatch):
    clip = _with_scene(window, scene, monkeypatch)
    window.playhead_seconds = 1.0
    track = next(t for t in window.project.tracks if t.id == clip.track_id)
    track.visible = False
    window._refresh_viewer_graphics()
    badge = getattr(window.preview_panel, "scene3d_badge", None)
    assert badge is None or badge.isHidden()
    track.visible = True
    window._refresh_viewer_graphics()
    assert not window.preview_panel.scene3d_badge.isHidden()


def test_a_scene_that_fails_to_render_never_breaks_the_monitor(
    window, scene, monkeypatch, caplog
):
    _with_scene(window, scene, monkeypatch)
    Spy(monkeypatch, fail=RuntimeError("boom"))
    scene3d.clear_signature_cache()
    scene3d_interactive.clear_preview_cache()
    window._refresh_viewer_graphics()  # pas d'exception
    assert any("boom" in record.getMessage() for record in caplog.records)


# --- (d) Sonde GPU en arrière-plan ---------------------------------------------------------------------------------------


class _Result:
    def __init__(self, ok: bool) -> None:
        self.ok, self.reason = ok, "" if ok else "pas de GPU"


def test_the_probe_runs_in_the_background_and_never_blocks(window, scene, monkeypatch):
    release = threading.Event()
    seen = []

    def slow_probe(*, platform=None, **_kw):
        seen.append(threading.current_thread() is threading.main_thread())
        release.wait(timeout=10)
        return _Result(True)

    monkeypatch.setattr("kut3d.gpu.availability.probe_qrhi", slow_probe)
    _with_scene(window, scene, monkeypatch)
    started = time.perf_counter()
    window._refresh_viewer_graphics()  # la sonde est « bloquée » : l'aperçu rend la main
    controller = controller_of(window)
    assert (
        controller.probe.status == GpuProbe.RUNNING
    )  # toujours en cours à ce stade : rien n'a attendu
    assert time.perf_counter() - started < 8
    assert controller.probe.choice() == (
        "numpy",
        scene3d_interactive.INTERIM_SIDE,
    )  # numpy petit en attendant
    assert scene3d_interactive.preview_backend() == (
        "numpy",
        scene3d_interactive.INTERIM_SIDE,
    )
    window._refresh_viewer_graphics()  # un second appel ne relance pas la sonde
    assert len(seen) <= 1
    refreshed = []
    monkeypatch.setattr(
        window, "_schedule_viewer_graphics", lambda: refreshed.append(1)
    )
    release.set()
    qtbot_wait(controller)
    assert (
        controller.probe.status == GpuProbe.READY and refreshed
    )  # le moniteur est redessiné avec le moteur définitif
    assert scene3d_interactive.preview_backend() == ("auto", None)
    assert seen[0] is False  # la sonde tourne hors du fil principal


def qtbot_wait(controller, timeout: float = 10.0) -> None:
    from PySide6.QtWidgets import QApplication

    deadline = time.perf_counter() + timeout
    while controller.timer.isActive() and time.perf_counter() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)


def test_an_unavailable_gpu_keeps_numpy(window, scene, monkeypatch):
    monkeypatch.setattr(
        "kut3d.gpu.availability.probe_qrhi", lambda **_k: _Result(False)
    )
    _with_scene(window, scene, monkeypatch)
    window._refresh_viewer_graphics()
    controller = controller_of(window)
    qtbot_wait(controller)
    assert controller.probe.status == GpuProbe.UNAVAILABLE
    assert scene3d_interactive.preview_backend() == (
        "numpy",
        scene3d_interactive.INTERIM_SIDE,
    )


def test_a_crashing_probe_never_takes_the_application_down(monkeypatch):
    def crash(**_k):
        raise RuntimeError("pilote")

    probe = GpuProbe(crash)
    assert (
        probe.start(None) is True and probe.start(None) is False
    )  # lancée une seule fois
    deadline = time.perf_counter() + 5
    while not probe.done and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert probe.status == GpuProbe.UNAVAILABLE and "pilote" in probe.reason


def test_no_probe_without_a_3d_clip(window, monkeypatch):
    called = []
    monkeypatch.setattr(
        "kut3d.gpu.availability.probe_qrhi",
        lambda **_k: called.append(1) or _Result(True),
    )
    window.add_layer_at_playhead("text", "")
    window._refresh_viewer_graphics()
    assert called == [] and getattr(window, "_scene3d_preview", None) is None


# --- (e) Inspecteur ------------------------------------------------------------------------------------------------------


def test_the_inspector_shows_the_file_and_hides_the_controls_a_scene_does_not_have(
    window, scene, monkeypatch
):
    clip = _with_scene(window, scene, monkeypatch)
    editor = window.properties_panel.graphics_group
    window.on_clip_selected(clip.id)
    assert not editor.scene3d_section.isHidden()
    assert editor.scene3d_section.file_label.text() == "static.kut3d"
    assert editor.scene3d_section.file_label.toolTip() == str(scene.resolve())
    assert editor.type_label.text() == i18n.translate("mograph.type.scene3d")
    hidden = (
        editor.text_edit,
        editor.text_label,
        editor.source_label,
        editor.width_spin,
        editor.fill_field,
        editor.shape_section,
        editor.stroke_section,
        editor.text_section,
        editor.style_section,
        editor.animation_section,
        editor.glow_section,
        editor.light_section,
    )
    assert all(widget.isHidden() or not widget.isVisibleTo(editor) for widget in hidden)


def test_other_layers_do_not_show_the_scene_section(window):
    window.add_layer_at_playhead("text", "")
    editor = window.properties_panel.graphics_group
    assert editor.scene3d_section.isHidden()
    window.properties_panel.show_clip(None)
    assert editor.scene3d_section.isHidden()


def test_reload_re_reads_the_files_and_redraws_the_monitor(window, scene, monkeypatch):
    import os

    clip = _with_scene(window, scene, monkeypatch)
    window.on_clip_selected(clip.id)
    before = scene3d.scene_signature(scene)
    mtl = scene.parent / "cube.mtl"
    info = mtl.stat()
    mtl.write_text(mtl.read_text().replace("Kd 0.9 0.1 0.2", "Kd 0.1 0.9 0.2"))
    os.utime(mtl, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert (
        scene3d.scene_signature(scene) == before
    )  # le mémo (date, taille) n'y voit rien
    scheduled = []
    monkeypatch.setattr(
        window, "_schedule_viewer_graphics", lambda: scheduled.append(1)
    )
    window.properties_panel.graphics_group.scene3d_section.reload_button.click()
    assert scene3d.scene_signature(scene) != before  # « Recharger » a invalidé le mémo
    assert scheduled and window.statusBar().currentMessage() == i18n.translate(
        "scene3d.reloaded"
    )


def test_reload_is_disabled_on_a_locked_layer(window, scene, monkeypatch):
    clip = _with_scene(window, scene, monkeypatch)
    window._on_layer_lock(
        clip.id, True
    )  # le chemin du panneau Calques (recharge la timeline)
    window.on_clip_selected(clip.id)
    section = window.properties_panel.graphics_group.scene3d_section
    called = []
    monkeypatch.setattr(
        "ui.scene3d_actions.reload_scene", lambda path: called.append(path)
    )
    assert not window.properties_panel.graphics_group.isEnabled()
    section.reload_requested.connect(lambda path: called.append(path))
    section._on_reload()  # même un appel direct (signal) ne fait rien
    assert called == []


def test_the_speed_controls_are_greyed_with_an_explanation_for_a_scene(
    window, scene, monkeypatch
):
    clip = _with_scene(window, scene, monkeypatch)
    panel = window.properties_panel
    window.on_clip_selected(clip.id)
    tip = i18n.translate("scene3d.speed.tooltip")
    assert not panel.speed_group.isEnabled()
    assert panel.speed_group.toolTip() == tip and panel.speed_spinbox.toolTip() == tip
    assert (
        panel.reverse_button.toolTip() == tip
        and panel.freeze_frame_button.toolTip() == tip
    )
    assert (
        not panel.speed_1x_button.isEnabled()
        and not panel.reset_speed_button.isEnabled()
    )
    window.add_layer_at_playhead(
        "text", ""
    )  # un calque ordinaire retrouve ses contrôles et ses infobulles
    assert panel.speed_group.isEnabled() and panel.speed_spinbox.isEnabled()
    assert panel.speed_group.toolTip() == "" and panel.speed_spinbox.toolTip() != tip
    assert panel.reverse_button.toolTip() != tip


def test_the_model_still_refuses_what_the_inspector_no_longer_offers(
    window, scene, monkeypatch
):
    from core.timeline_operations import set_clip_speed

    clip = _with_scene(window, scene, monkeypatch)
    with pytest.raises(ValueError):
        set_clip_speed(window.project, clip.id, 2.0)


# --- (g) Textes ----------------------------------------------------------------------------------------------------------


def test_every_new_text_exists_in_french_english_and_spanish_with_the_same_fields():
    import string

    assert SCENE3D_TRANSLATIONS
    for key, entry in SCENE3D_TRANSLATIONS.items():
        assert set(entry) == {"fr", "en", "es"} and all(
            entry[code].strip() for code in entry
        ), key
        fields = {
            code: {
                name
                for _l, name, _s, _c in string.Formatter().parse(entry[code])
                if name
            }
            for code in entry
        }
        assert fields["fr"] == fields["en"] == fields["es"], key
    previous = i18n.current_language()
    try:
        for code in ("fr", "en", "es"):
            i18n.set_language(code)
            for key in SCENE3D_TRANSLATIONS:
                assert i18n.translate(
                    key, error="e", track="t", ext="x"
                ) == SCENE3D_TRANSLATIONS[key][code].format(
                    error="e", track="t", ext="x"
                )
    finally:
        i18n.set_language(previous)


def test_the_scene_section_follows_the_usage_level(window, scene, monkeypatch):
    clip = _with_scene(window, scene, monkeypatch)
    window.on_clip_selected(clip.id)
    section = window.properties_panel.graphics_group.scene3d_section
    window.set_usage_level("simple")
    assert not section.isVisibleTo(
        window.properties_panel
    )  # un utilisateur Simple ne voit aucun réglage 3D
    window.set_usage_level("studio")
    assert section.isVisibleTo(window.properties_panel)
    window.set_usage_level("advanced")
    assert section.isVisibleTo(window.properties_panel)
