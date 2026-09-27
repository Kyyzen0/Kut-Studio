"""Comportement de l'inspecteur Mouvement et des sous-titres."""

import pytest
from PySide6.QtWidgets import QGroupBox

from core.timeline_operations import add_subtitle_clip, find_clip
from core.timeline_view_model import TimelineClipView
from core.track_operations import add_track
from core.visual_effects import ClipTransform, TransformKeyframe
from ui.properties_panel import PropertiesPanel
from ui.project_panel import ProjectPanel


def _panel(qtbot) -> PropertiesPanel:
    panel = PropertiesPanel(lambda *_args: None, lambda *_args: None)
    qtbot.addWidget(panel)
    return panel


def _view(**overrides) -> TimelineClipView:
    fields = {
        "id": "clip",
        "track_id": "V1",
        "track_index": 0,
        "start": 10.0,
        "end": 14.0,
        "label": "Plan",
        "text": "",
        "source_path": "",
        "color_key": "#ffffff",
        "track_type": "video",
        "enabled": True,
        "keyframes": (),
        "transform": ClipTransform(),
    }
    fields.update(overrides)
    return TimelineClipView(**fields)


def test_properties_panel_omits_redundant_timeline_actions(qtbot) -> None:
    """Les actions de montage restent dans la timeline, pas l'inspecteur."""
    panel = _panel(qtbot)

    assert all(group.title() != "Actions" for group in panel.findChildren(QGroupBox))


def test_subtitle_library_scrolls_in_a_short_side_panel(qtbot) -> None:
    """Une fenêtre basse ne doit plus superposer les contrôles de sous-titres."""
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.resize(280, 360)
    panel.navigation.setCurrentRow(2)
    panel.show()
    qtbot.wait(20)

    subtitles = panel.subtitle_view
    assert subtitles.scroll_area.verticalScrollBar().maximum() > 0
    assert subtitles.import_button.text() == ""
    assert subtitles.export_button.text() == ""


def test_clearing_selection_restores_each_motion_default(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(
        ClipTransform(scale=2.0, opacity=0.4, rotation=720.0),
        [],
        playhead_seconds=10.0,
    )

    panel.show_clip(None)

    assert panel._spin_boxes["scale"].value() == pytest.approx(1.0)
    assert panel._spin_boxes["opacity"].value() == pytest.approx(1.0)
    assert panel._spin_boxes["position_x"].value() == pytest.approx(0.0)
    assert panel._slider_widgets["scale"].value() == 100
    assert panel._slider_widgets["opacity"].value() == 100
    assert panel._slider_widgets["rotation"].value() == 0
    assert panel._slider_widgets["rotation"].minimum() == -3600
    assert panel._slider_widgets["rotation"].maximum() == 3600


def test_fields_show_evaluated_value_and_follow_the_playhead(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    transform = ClipTransform(scale=1.0)
    keyframes = [TransformKeyframe("scale", 1.0, 0.5)]

    panel.update_transform_from_clip(transform, keyframes, playhead_seconds=10.0)
    assert panel._spin_boxes["scale"].value() == pytest.approx(1.0)
    assert panel._diamonds["scale"].isChecked() is False

    panel.refresh_keyframe_diamonds(keyframes, 11.0, transform=transform)
    assert panel._spin_boxes["scale"].value() == pytest.approx(0.5)
    assert panel._diamonds["scale"].isChecked() is True


def test_editing_near_a_keyframe_rewrites_its_stored_time(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    keyframes = [
        TransformKeyframe("scale", 1.0, 0.5),
        TransformKeyframe("scale", 1.002, 1.0),
    ]
    panel.update_transform_from_clip(
        ClipTransform(scale=1.0),
        keyframes,
        playhead_seconds=11.0008,
    )
    assert panel._spin_boxes["scale"].value() == pytest.approx(0.5)
    assert panel._diamonds["scale"].isChecked() is True
    added: list[tuple] = []
    panel.keyframe_added.connect(lambda *args: added.append(args))

    panel._spin_boxes["scale"].setValue(0.25)

    assert added == [("clip", "scale", pytest.approx(1.0), pytest.approx(0.25))]


def test_editing_before_the_first_keyframe_changes_the_base(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(
        ClipTransform(scale=1.0),
        [TransformKeyframe("scale", 2.0, 0.4)],
        playhead_seconds=10.5,
    )
    added: list[tuple] = []
    changed: list[tuple] = []
    panel.keyframe_added.connect(lambda *args: added.append(args))
    panel.transform_changed.connect(lambda *args: changed.append(args))

    panel._spin_boxes["scale"].setValue(1.2)

    assert added == []
    assert changed == [("clip", "scale", pytest.approx(1.2))]


def test_editing_between_keyframes_inserts_one_at_the_playhead(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(
        ClipTransform(scale=1.0),
        [
            TransformKeyframe("scale", 0.0, 1.0),
            TransformKeyframe("scale", 2.0, 0.2),
        ],
        playhead_seconds=11.0,
    )
    assert panel._spin_boxes["scale"].value() == pytest.approx(0.6)
    added: list[tuple] = []
    panel.keyframe_added.connect(lambda *args: added.append(args))

    panel._spin_boxes["scale"].setValue(0.8)

    assert added == [("clip", "scale", pytest.approx(1.0), pytest.approx(0.8))]


def test_shift_click_removes_the_matched_keyframe_time(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(
        ClipTransform(),
        [TransformKeyframe("opacity", 1.0, 0.2)],
        playhead_seconds=11.0004,
    )
    removed: list[tuple] = []
    panel.keyframe_removed.connect(lambda *args: removed.append(args))

    panel._apply_diamond_action("opacity", shift=True)

    assert removed == [("clip", "opacity", pytest.approx(1.0))]


def test_diamond_click_outside_the_clip_does_not_emit(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(ClipTransform(), [], playhead_seconds=0.0)
    added: list[tuple] = []
    panel.keyframe_added.connect(lambda *args: added.append(args))

    panel._apply_diamond_action("scale", shift=False)

    assert added == []
    assert panel._diamonds["scale"].isChecked() is False


def test_rotation_slider_accepts_the_full_spin_range(qtbot) -> None:
    panel = _panel(qtbot)
    panel.show_clip(_view())
    panel.update_transform_from_clip(
        ClipTransform(rotation=720.0),
        [],
        playhead_seconds=10.0,
    )

    assert panel._slider_widgets["rotation"].value() == 720
    assert panel._spin_boxes["rotation"].value() == pytest.approx(720.0)


def test_movement_follows_track_type_and_subtitles_any_track(qtbot) -> None:
    panel = _panel(qtbot)

    panel.show_clip(_view(track_type="audio", track_id="V9"))
    assert panel.movement_group.isEnabled() is False
    assert panel.subtitle_group.isVisibleTo(panel) is False
    assert panel._spin_boxes["scale"].value() == pytest.approx(1.0)

    panel.show_clip(
        _view(track_type="subtitle", track_id="S2", text="Bonjour", id="sub")
    )
    assert panel.subtitle_group.isVisibleTo(panel) is True
    assert panel.subtitle_editor.toPlainText() == "Bonjour"
    assert panel.movement_group.isEnabled() is False


def test_s2_subtitle_editor_updates_the_project(qtbot, monkeypatch) -> None:
    from test_ui_integration import _build_window

    window = _build_window(qtbot, monkeypatch)
    track = add_track(window.project, "subtitle")
    clip = add_subtitle_clip(
        window.project, "Bonjour", 0.0, 2.0, track_id=track.id
    )
    window.timeline_panel.set_project(window.project)

    window.on_clip_selected(clip.id)

    assert window.active_subtitle_clip is not None
    assert window.active_subtitle_clip.id == clip.id
    assert window.properties_panel.subtitle_group.isVisibleTo(
        window.properties_panel
    )
    window.properties_panel.subtitle_editor.setPlainText("Salut")
    window.update_subtitle_from_editor()
    assert find_clip(window.project, clip.id).text == "Salut"


def test_keyframe_drags_share_one_history_entry(qtbot, monkeypatch) -> None:
    from test_ui_integration import _build_window

    window = _build_window(qtbot, monkeypatch)
    before = len(window.history)

    window.on_transform_keyframe_added("intro", "scale", 0.5, 0.4)
    window.on_transform_keyframe_added("intro", "scale", 0.5, 0.7)

    assert len(window.history) == before
    window._finalize_transform_session()
    assert len(window.history) == before + 1
    scales = [
        keyframe.value
        for keyframe in find_clip(window.project, "intro").transform_keyframes
        if keyframe.property_name == "scale"
    ]
    assert scales == [pytest.approx(0.7)]
