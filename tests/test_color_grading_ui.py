"""Intégration UI de l'espace Couleur (tâche 29)."""

from __future__ import annotations

import pytest

from core.color_grading import ColorGrade, LUTResource, builtin_color_preset_ids
from core.timeline_operations import find_clip
from core.timeline_view_model import TimelineClipView
from ui.properties_panel import PropertiesPanel


def _panel(qtbot, monkeypatch, tmp_path) -> PropertiesPanel:
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    panel = PropertiesPanel(lambda *_: None)
    qtbot.addWidget(panel)
    return panel


def _view(**overrides) -> TimelineClipView:
    values = dict(
        id="clip", track_id="V1", track_index=0, start=0.0, end=2.0,
        label="Plan", text="", source_path="", color_key="#ffffff",
        track_type="video", color_grade=ColorGrade.identity(),
    )
    values.update(overrides)
    return TimelineClipView(**values)


def test_color_tab_exposes_complete_grade_and_builtin_presets(
    qtbot, monkeypatch, tmp_path
) -> None:
    panel = _panel(qtbot, monkeypatch, tmp_path)
    panel.show_clip(_view(color_grade=ColorGrade(exposure=0.4, temperature=25.0)))
    panel._select_inspector_tab(1)

    assert set(panel.color_field_spins) == {
        "exposure", "contrast", "saturation", "temperature", "hue",
        "shadows", "highlights",
    }
    assert panel.color_field_spins["exposure"].value() == pytest.approx(0.4)
    assert panel.color_field_spins["temperature"].value() == pytest.approx(25.0)
    ids = {
        panel.color_preset_combo.itemData(index)
        for index in range(panel.color_preset_combo.count())
    }
    assert builtin_color_preset_ids().issubset(ids)
    assert panel.color_group.isEnabled()


def test_color_field_emits_model_intent(qtbot, monkeypatch, tmp_path) -> None:
    panel = _panel(qtbot, monkeypatch, tmp_path)
    panel.show_clip(_view())
    changes = []
    panel.color_grade_field_changed.connect(lambda *args: changes.append(args))

    panel.color_field_spins["saturation"].setValue(1.35)

    assert changes == [("clip", "saturation", pytest.approx(1.35))]


def test_missing_lut_is_visible_and_locked_track_disables_controls(
    qtbot, monkeypatch, tmp_path
) -> None:
    panel = _panel(qtbot, monkeypatch, tmp_path)
    grade = ColorGrade(
        lut=LUTResource(
            path="luts/absent.cube", title="Absent", sha1="0" * 40,
            missing=True,
        )
    )

    panel.show_clip(_view(color_grade=grade, locked=True))

    assert "LUT manquante" in panel.color_lut_label.text()
    assert not panel.color_group.isEnabled()


def test_main_window_color_change_is_undoable(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    from test_ui_integration import _build_window

    window = _build_window(qtbot, monkeypatch)
    window.on_clip_selected("intro")
    window.on_color_grade_field_changed("intro", "exposure", 0.5)
    assert find_clip(window.project, "intro").color_grade.exposure == pytest.approx(0.5)

    window.undo_last()

    restored = find_clip(window.project, "intro").color_grade
    assert restored is None or restored.exposure == pytest.approx(0.0)


def test_save_color_preset_embeds_it_in_project(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    from test_ui_integration import _build_window

    window = _build_window(qtbot, monkeypatch)
    window.on_color_grade_field_changed("intro", "contrast", 0.25)
    window.on_color_preset_save_requested("intro", "Mon contraste")

    assert any(preset.name == "Mon contraste" for preset in window.project.color_presets)
