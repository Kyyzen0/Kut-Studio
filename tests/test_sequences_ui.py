"""Séquences imbriquées dans l'interface : commandes, navigation, historique.

Fenêtre réelle (offscreen) : les commandes sont appelées comme le feraient
le menu, le menu contextuel, la bibliothèque ou un raccourci, puis on lit
l'état du projet, de la timeline et de la barre de navigation.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QMouseEvent

from core.project_model import Clip
from core.sequences import sequence_issues
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *_, **__: QMessageBox.Yes)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_, **__: QMessageBox.Yes)
    main = MainWindow()
    qtbot.addWidget(main)
    if getattr(main, "timeline_timer", None) is not None:
        main.timeline_timer.stop()
    return main


def _select(window, *clip_ids):
    window.timeline_panel._set_selection(list(clip_ids), clip_ids[0], announce=False)


def _nested_clip(window):
    return next(c for t in window.project.tracks for c in t.clips if c.sequence_id)


def test_nest_selection_replaces_clips_and_is_undoable(window):
    _select(window, "intro", "b_roll")
    result = window.nest_selected_clips(name="Scene 01")
    assert result is not None and result.sequence.name == "Scene 01"
    ids = {c.id for t in window.project.tracks for c in t.clips}
    assert "intro" not in ids and "b_roll" not in ids and result.clip.id in ids
    view = window.timeline_panel.find_view_by_id(result.clip.id)
    assert view.sequence_id == result.sequence.id and view.label == "Scene 01"
    assert window.history.undo_label.startswith("Créer la séquence imbriquée")
    window.undo_last()
    ids = {c.id for t in window.project.tracks for c in t.clips}
    assert {"intro", "b_roll"} <= ids and len(window.project.sequences) == 1
    window.redo_last()
    assert len(window.project.sequences) == 2


def test_nest_without_selection_does_nothing(window):
    window.timeline_panel._set_selection([], None, announce=False)
    assert window.nest_selected_clips(name="X") is None
    assert len(window.project.sequences) == 1


def test_open_nested_clip_navigates_with_breadcrumb_parent_back_forward(window):
    _select(window, "intro")
    result = window.nest_selected_clips(name="Intro nest")
    main_id = window.project.sequences[0].id
    window.playhead_seconds = 1.0
    window.open_nested_clip(result.clip.id)
    bar = window.timeline_panel.sequence_bar
    assert window.project.active_sequence_id == result.sequence.id
    assert bar.breadcrumb_names == [window.project.get_sequence(main_id).name, "Intro nest"]
    assert bar.active_name == "Intro nest"
    assert window.playhead_seconds == pytest.approx(1.0)  # même image dans la séquence
    assert "Intro nest" in window.project_label.text()
    window.go_to_parent_sequence()
    assert window.project.active_sequence_id == main_id
    window.navigate_sequence_back()
    assert window.project.active_sequence_id == result.sequence.id
    window.navigate_sequence_forward()
    assert window.project.active_sequence_id == main_id
    assert not bar.forward_button.isEnabled()


def test_double_click_on_a_nested_clip_opens_it(window, qtbot):
    _select(window, "plan_a")
    result = window.nest_selected_clips(name="Plan nest")
    panel = window.timeline_panel
    window.show()
    panel.scroll.horizontalScrollBar().setValue(0)
    panel._sync_mounted_clips(refresh_views=True)
    from ui.timeline_widgets.clip_widget import ClipWidget

    widget = next(w for w in panel.findChildren(ClipWidget) if w.view.id == result.clip.id)
    center = QPointF(widget.width() / 2, widget.height() / 2)
    event = QMouseEvent(
        QMouseEvent.MouseButtonDblClick, center, widget.mapToGlobal(center),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )
    widget.mouseDoubleClickEvent(event)
    assert window.project.active_sequence_id == result.sequence.id


def test_editing_the_source_updates_every_instance(window):
    _select(window, "intro")
    result = window.nest_selected_clips(name="Intro nest")
    window.insert_sequence_into_active(result.sequence.id, "V2", 8.0)
    instances = [c for t in window.project.tracks for c in t.clips if c.sequence_id]
    assert len(instances) == 2
    window.open_sequence(result.sequence.id)
    window.rename_sequence_command(result.sequence.id, "Générique")
    window.go_to_parent_sequence()
    labels = {window.timeline_panel.find_view_by_id(c.id).label for c in instances}
    assert labels == {"Générique"}


def test_shortening_the_source_clamps_parent_clips_in_the_same_undo_step(window):
    _select(window, "intro")  # 0–4 s
    result = window.nest_selected_clips(name="Intro nest")
    window.open_sequence(result.sequence.id)
    inner_clip = window.project.tracks[0].clips[0]
    window.on_trim_right_requested(inner_clip.id, 2.0)
    parent = window.project.sequences[0]
    parent_clip = next(c for t in parent.tracks for c in t.clips if c.sequence_id)
    assert parent_clip.source_out == pytest.approx(2.0)
    assert i18n.translate("sequence.history.clamped", count=1) in window.history.undo_label
    window.undo_last()
    restored = next(c for s in window.project.sequences for t in s.tracks for c in t.clips if c.sequence_id)
    assert restored.source_out == pytest.approx(4.0)


def test_library_lists_creates_duplicates_and_deletes(window):
    library = window.project_panel.sequence_view
    assert len(library.entries()) == 1
    created = window.create_new_sequence("Scene 02")
    assert window.project.active_sequence_id == created.id
    assert [e.name for e in library.entries()][-1] == "Scene 02"
    assert next(e for e in library.entries() if e.id == created.id).active
    clone = window.duplicate_sequence_command(created.id)
    assert clone.name == "Scene 02 copie" and len(library.entries()) == 3
    assert window.delete_sequence_command(clone.id, confirmed=True)
    assert len(window.project.sequences) == 2
    window.undo_last()
    assert len(window.project.sequences) == 3


def test_deleting_a_used_sequence_marks_clips_offline(window):
    _select(window, "intro")
    result = window.nest_selected_clips(name="Intro nest")
    window.project_panel.sequence_view.select_sequence(result.sequence.id)
    assert window.delete_sequence_command(result.sequence.id, confirmed=False) is False
    assert window.project.get_sequence(result.sequence.id) is not None
    assert window.delete_sequence_command(result.sequence.id, confirmed=True)
    view = window.timeline_panel.find_view_by_id(result.clip.id)
    assert view.nested_status == "missing"
    assert {issue.kind for issue in sequence_issues(window.project)} == {"missing"}
    window.undo_last()
    assert window.timeline_panel.find_view_by_id(result.clip.id).nested_status == ""


def test_cycles_are_refused_from_the_interface(window):
    _select(window, "intro")
    result = window.nest_selected_clips(name="Intro nest")
    parent_id = window.project.sequences[0].id
    window.open_sequence(result.sequence.id)
    before = sum(len(t.clips) for t in window.project.tracks)
    assert window.insert_sequence_into_active(parent_id) is None
    assert window.insert_sequence_into_active(result.sequence.id) is None
    assert sum(len(t.clips) for t in window.project.tracks) == before


def test_dropping_a_sequence_on_a_track_inserts_a_nested_clip(window, qtbot):
    created = window.create_new_sequence("Bumper", open_it=False)
    window.project.get_sequence(created.id).tracks[0].clips.append(
        Clip("bumper-clip", "asset-intro", "V1", 0.0, 0.0, 2.0)
    )
    panel = window.timeline_panel
    window.show()
    track_index = next(i for i, t in enumerate(window.project.tracks) if t.id == "V2")
    grid_point = panel.timeline_grid.mapTo(
        panel, QPoint(
            int(panel.left_margin + 3.0 * panel.pixels_per_second * panel.zoom) + 1,
            panel.row_top(track_index) + 5,
        )
    )
    mime = QMimeData()
    mime.setData("application/x-kut-studio-sequence-id", created.id.encode("utf-8"))
    event = QDropEvent(QPointF(grid_point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.dropEvent(event)
    clip = _nested_clip(window)
    assert clip.sequence_id == created.id and clip.track_id == "V2"
    assert clip.timeline_start == pytest.approx(3.0, abs=0.05)


def test_live_preview_resolves_nested_clips_without_crashing(window):
    _select(window, "intro")
    window.nest_selected_clips(name="Intro nest")
    window.playhead_seconds = 1.0
    active = window._sync_preview_to_timeline()
    assert any(entry.root_clip_id for entry in active)


def test_render_queue_job_remembers_the_sequence(window, tmp_path):
    _select(window, "intro")
    result = window.nest_selected_clips(name="Intro nest")
    spec = window.export_panel.current_spec()
    job = window.render_queue.enqueue(window.project, spec, str(tmp_path / "out.mp4"))
    assert job.sequence_id == window.project.active_sequence_id
    assert job.sequence_name
    from core.render_job import RenderJob

    assert RenderJob.from_dict(job.to_dict()).sequence_id == job.sequence_id
    assert result.sequence.id != job.sequence_id


def test_sequence_translations_are_complete():
    keys = [key for key in i18n._TRANSLATIONS if key.startswith("sequence.")]
    keys += [key for key in i18n._TRANSLATIONS if key.startswith("shortcuts.command.sequence_")]
    assert len(keys) > 40
    for key in keys:
        entry = i18n._TRANSLATIONS[key]
        assert set(entry) == {"fr", "en", "es"}, key
        assert all(value.strip() for value in entry.values()), key


def test_sequence_shortcuts_are_bound_to_menu_actions(window):
    from core.shortcuts import COMMANDS, Category

    sequence_commands = [c.id for c in COMMANDS if c.category is Category.SEQUENCES]
    assert set(sequence_commands) >= {
        "sequence_nest_selection", "sequence_parent", "sequence_back", "sequence_forward",
    }
    assert window.shortcuts.missing_handlers() == []
    assert window.nest_selection_action.shortcut().toString() == "Ctrl+Shift+N"


def test_sequence_widgets_follow_the_language(window):
    try:
        i18n.set_language("en")
        window._retranslate_ui()
        assert window.timeline_panel.sequence_bar.sequences_button.text() == "Sequences ▾"
        assert window.project_panel.sequence_view.title.text() == "PROJECT SEQUENCES"
    finally:
        i18n.set_language("fr")


@pytest.mark.parametrize(("language", "plain", "nested"), [
    ("fr", "Séquence", "Séquence imbriquée"),
    ("en", "Sequence", "Nested sequence"),
    ("es", "Secuencia", "Secuencia anidada"),
])
def test_default_sequence_names_are_offered_in_the_current_language(window, monkeypatch, language, plain, nested):
    """Le nom proposé dans la boîte de dialogue suit la langue (il était écrit en dur en français)."""
    offered = []

    def accept_default(_parent, _title, _label, text=""):
        offered.append(text)
        return text, True

    monkeypatch.setattr("ui.main_window.QInputDialog.getText", accept_default)
    try:
        i18n.set_language(language)
        window.create_new_sequence(open_it=False)
        _select(window, "intro", "b_roll")
        window.nest_selected_clips()
    finally:
        i18n.set_language("fr")
    assert offered == [plain, nested]
    assert {sequence.name for sequence in window.project.sequences} >= {plain, nested}
