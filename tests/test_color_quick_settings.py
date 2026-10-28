"""Page Couleur : les quatre réglages rapides sous les roues (Exposition, Contraste, Saturation, Température).

Ce sont des **contrôles neufs liés au même modèle** que les champs de l'inspecteur : même commande
(``on_color_grade_field_changed``), même pile d'annulation, mêmes images clés (ADR-0008). Ces tests vérifient la synchro dans les
deux sens, l'annulation sans écho d'historique, les verrous, l'animation, et la lisibilité (1222 x 738, polices plus larges).
"""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs
from PySide6.QtCore import QPoint
from test_ui_legibility import SIZE_IDS, SIZES, _fully_visible, _wheel_problems, windows  # noqa: F401 - fixture et aides communes

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.workspace_state import PAGE_COLOR
from tools import ui_audit as audit
from tools import ui_legibility as legibility
from ui.color_page.quick_settings import QUICK_FIELDS

NAMES = tuple(row[0] for row in QUICK_FIELDS)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    monkeypatch.setattr(win, "_present_cached_preview_at", lambda *_a, **_k: False)
    win.set_usage_level("advanced")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _prepared(win, *, grade=None, locked: bool = False):
    track = win.project.tracks[0]
    clip = track.clips[0]
    clip.color_grade = grade
    clip.animation = []
    track.locked = locked
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    win.switch_page(PAGE_COLOR)
    return track, clip


def _clip(win):
    return win.project.tracks[0].clips[0]


def _row(win, name):
    return win.color_panel.wheels.quick.rows[name]


def _inspector(win, name):
    return win.properties_panel.color_field_spins[name]


def _steps(win) -> int:
    win._finalize_color_history()
    return len(win.history)


def _kf(prop, time, value):
    return Keyframe(prop, time, value, InterpolationType.LINEAR)


# ---------------------------------------------------------------- construction et synchro


def test_there_are_four_rows_with_new_widgets_bound_to_the_inspector_bounds(window):
    _prepared(window)
    quick = window.color_panel.wheels.quick
    assert tuple(quick.rows) == NAMES and quick.isVisibleTo(window.color_panel)
    for name, row in quick.rows.items():
        inspector = _inspector(window, name)
        assert row.spin is not inspector and row.parentWidget() is quick                   # nouveau widget, jamais déplacé
        assert (row.spin.minimum(), row.spin.maximum()) == (inspector.minimum(), inspector.maximum())
        assert row.spin.accessibleName() and row.slider.accessibleName() and row.diamond.accessibleName()


def test_a_row_edit_reaches_the_model_and_the_inspector_in_one_history_step(window):
    _track, clip = _prepared(window)
    entries = _steps(window)
    row, inspector = _row(window, "exposure"), _inspector(window, "exposure")
    row.spin.setValue(0.4)
    row.spin.setValue(0.6)                                                                  # une rafale
    assert inspector.value() == pytest.approx(0.6)                                          # tout de suite, avant la fin de la rafale
    assert _steps(window) == entries + 1
    assert _clip(window).color_grade.exposure == pytest.approx(0.6)
    assert inspector.value() == pytest.approx(0.6) and row.spin.value() == pytest.approx(0.6)
    assert _steps(window) == entries + 1, "aucune étape d'historique en écho"


def test_the_slider_edits_the_same_value(window):
    _prepared(window)
    row = _row(window, "temperature")
    row.slider.setValue(row.slider.maximum())
    window._finalize_color_history()
    assert _clip(window).color_grade.temperature == pytest.approx(100.0)
    assert row.spin.value() == pytest.approx(100.0) and _inspector(window, "temperature").value() == pytest.approx(100.0)


def test_an_inspector_edit_shows_in_the_row_without_a_second_step(window):
    _track, clip = _prepared(window)
    entries = _steps(window)
    _inspector(window, "saturation").setValue(1.5)
    row = _row(window, "saturation")
    assert row.spin.value() == pytest.approx(1.5) and row.slider.value() == round((1.5 - row.minimum) / row.unit)
    assert _steps(window) == entries + 1
    assert _clip(window).color_grade.saturation == pytest.approx(1.5)


def test_undo_and_redo_restore_both_series_with_one_step_each(window):
    _prepared(window)
    entries = _steps(window)
    _row(window, "contrast").spin.setValue(0.5)
    assert _steps(window) == entries + 1
    window.undo_last()
    assert _clip(window).color_grade is None or _clip(window).color_grade.contrast == 0.0
    assert _row(window, "contrast").spin.value() == 0.0 and _inspector(window, "contrast").value() == 0.0
    window.redo_last()
    assert _clip(window).color_grade.contrast == pytest.approx(0.5)
    assert _row(window, "contrast").spin.value() == pytest.approx(0.5) and _inspector(window, "contrast").value() == pytest.approx(0.5)


def test_the_row_follows_the_current_node_of_a_node_graph(window):
    _track, clip = _prepared(window)
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.2)), ColorNode("n2", ColorGrade(exposure=-0.5))))
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.on_color_node_selected("n2")
    assert _row(window, "exposure").spin.value() == pytest.approx(-0.5)
    _row(window, "exposure").spin.setValue(-0.25)
    window._finalize_color_history()
    graph = _clip(window).color_grade
    assert graph.correctors[1].grade.exposure == pytest.approx(-0.25) and graph.correctors[0].grade.exposure == pytest.approx(0.2)
    assert _inspector(window, "exposure").value() == pytest.approx(-0.25)


# ---------------------------------------------------------------- verrous et refus


def test_a_locked_track_disables_the_rows_and_a_signal_that_reaches_the_model_changes_nothing(window):
    _track, clip = _prepared(window, grade=ColorGrade(exposure=0.3), locked=True)
    entries = len(window.history)
    quick = window.color_panel.wheels.quick
    assert not window.color_panel.wheels.isEnabled()
    window.color_panel.field_changed.emit("exposure", 1.5)                                 # un signal peut atteindre le modèle
    window.on_color_quick_keys_toggled("exposure")
    window._finalize_color_history()
    assert _clip(window).color_grade.exposure == pytest.approx(0.3) and not _clip(window).animation
    assert quick.rows["exposure"].spin.value() == pytest.approx(0.3), "la ligne reprend la valeur du modèle"
    assert len(window.history) == entries


# ---------------------------------------------------------------- animation (ADR-0008)


def test_an_animated_setting_writes_the_key_and_the_row_shows_the_value_at_the_playhead(window):
    _track, clip = _prepared(window)
    clip.animation = [_kf("color.exposure", 0.0, 0.0), _kf("color.exposure", 4.0, 1.0)]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(2.0)
    row = _row(window, "exposure")
    assert row.spin.value() == pytest.approx(0.5) and row.diamond.animated and not row.diamond.isChecked()
    row.spin.setValue(0.9)
    window._finalize_color_history()
    assert any(abs(k.time_seconds - 2.0) < 1e-3 and k.value == pytest.approx(0.9) for k in _clip(window).animation)
    assert _clip(window).color_grade is None or _clip(window).color_grade.exposure == 0.0       # la base ne bouge pas
    assert _inspector(window, "exposure").value() == pytest.approx(0.9)
    window.seek_to_position(0.0)
    assert row.spin.value() == pytest.approx(0.0) and _inspector(window, "exposure").value() == pytest.approx(0.0)


def test_the_diamond_keys_the_setting_and_a_second_click_removes_it(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    entries = len(window.history)
    row = _row(window, "contrast")
    row.diamond.click()
    assert [k.property_name for k in _clip(window).animation] == ["color.contrast"] and len(window.history) == entries + 1
    assert row.diamond.isChecked() and window.properties_panel.color_keyframe_widgets()["contrast"].diamond.isChecked()
    row.diamond.click()
    assert not _clip(window).animation and len(window.history) == entries + 2
    window.undo_last()
    assert len(_clip(window).animation) == 1


def test_a_node_graph_does_not_animate_so_the_diamonds_are_off(window):
    _track, clip = _prepared(window)
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    assert all(not row.diamond.isEnabled() for row in window.color_panel.wheels.quick.rows.values())


def test_there_is_no_clip_no_value_and_nothing_to_edit(window):
    window.switch_page(PAGE_COLOR)
    window.color_panel.set_target(None, None, editable=False)
    assert not window.color_panel.wheels.isEnabled()
    window.on_color_quick_field_changed("exposure", 1.0)                                    # sans clip à l'écran : sans effet


# ---------------------------------------------------------------- lisibilité


def _quick_problems(panel, label: str) -> list[str]:
    """Si les lignes sont posées : texte >= 12 px, lignes >= 30 px, libellés entiers, champs non écrasés, tout visible sans défiler."""
    quick = panel.wheels.quick
    if not quick.isVisibleTo(panel):
        return []
    problems: list[str] = []
    viewport = panel.tabs.currentWidget().parentWidget().parentWidget().viewport()
    for name, row in quick.rows.items():
        if row.height() < 30:
            problems.append(f"{label} {name} : ligne de {row.height()} px")
        for widget in (row.caption, row.spin):
            size = widget.font().pixelSize() if widget.font().pixelSize() > 0 else round(widget.font().pointSizeF() * 96 / 72)
            if size < 12:
                problems.append(f"{label} {name} : texte de {size} px")
        if row.caption.width() < row.caption.sizeHint().width() or row.spin.width() < row.spin.sizeHint().width():
            problems.append(f"{label} {name} : libellé ou champ écrasé")
        if not _fully_visible(row, viewport):
            problems.append(f"{label} {name} : ligne coupée {row.mapTo(viewport, QPoint(0, 0))} {row.size()}")
    return problems


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_the_rows_never_squeeze_the_wheels(windows, size):
    """Roues entières et >= 120 px comme avant (#278), avec ou sans les lignes ; rien ne se chevauche ni ne défile."""
    window = windows(size)
    window.switch_page("color")
    window.clip_strip.timeline_button.setChecked(False)
    window.color_panel.tabs.setCurrentIndex(0)
    audit.settle(window)
    panel = window.color_panel
    try:
        assert not _wheel_problems(panel, f"{size[0]}x{size[1]}"), _wheel_problems(panel, "")
        assert not _quick_problems(panel, f"{size[0]}x{size[1]}")
        assert not [f for f in legibility.overlapping_siblings(window)]
        if size in ((1222, 738), (1440, 900), (1440, 790)):
            assert panel.wheels.quick.isVisibleTo(panel), f"{size} : les lignes devraient tenir sous les roues"
    finally:
        window.switch_page("edit")
        audit.settle(window)


def test_one_line_each_when_the_height_allows_two_by_two_otherwise(windows):
    window = windows((1440, 900))
    window.switch_page("color")
    audit.settle(window)
    quick = window.color_panel.wheels.quick
    try:
        assert quick.columns == 1
        assert len({row.y() for row in quick.rows.values()}) == 4
        window.resize(1222, 738)
        audit.settle(window)
        assert quick.isVisibleTo(window.color_panel) and quick.columns == 2
        assert len({row.y() for row in quick.rows.values()}) == 2
    finally:
        window.resize(1440, 900)
        window.switch_page("edit")
        audit.settle(window)


def test_the_rows_hide_instead_of_squeezing_the_wheels_in_a_low_window(windows):
    """Fenêtre basse : les lignes se masquent proprement ; les roues gardent leur taille et l'onglet ne défile pas."""
    window = windows((1222, 738))
    window.switch_page("color")
    audit.settle(window)
    panel = window.color_panel
    try:
        window.resize(1222, 690)
        audit.settle(window)
        assert not _wheel_problems(panel, "bas")
        window.clip_strip.timeline_button.setChecked(True)                                  # la timeline rouverte prend de la hauteur
        audit.settle(window)
        assert not panel.wheels.quick.isVisibleTo(panel)
    finally:
        window.clip_strip.timeline_button.setChecked(False)
        window.resize(1222, 738)
        window.switch_page("edit")
        audit.settle(window)


@pytest.mark.parametrize("scale", [1.2, 1.45], ids=["police+20%", "police+45%"])
def test_the_rows_stay_legible_or_hide_with_wider_fonts(scale):
    """Polices plus larges (Linux, Windows) : jamais de texte élidé ni de ligne coupée ; elles se masquent plutôt que de s'écraser."""
    from PySide6.QtWidgets import QApplication
    from qt_cleanup import deliver_deferred_deletes

    window = audit.make_main_window(1222, 738, scopes=False, font_scale=scale)
    try:
        audit.select_first_clip(window)
        window.switch_page("color")
        window.clip_strip.timeline_button.setChecked(False)
        audit.settle(window)
        panel = window.color_panel
        problems = _quick_problems(panel, f"x{scale}") + _wheel_problems(panel, f"x{scale}")
        assert not problems, "\n".join(problems[:10])
    finally:
        window.close()
        window.deleteLater()
        deliver_deferred_deletes()
        base = QApplication.instance().property("_kut_audit_base_stylesheet")
        if base is not None:
            QApplication.instance().setStyleSheet(base)


def test_without_sliders_the_rows_keep_label_field_and_diamond_and_still_edit(window):
    """Colonne étroite ou polices larges (Linux, Windows) : sans curseurs, la ligne reste réglable et animable."""
    _prepared(window)
    quick = window.color_panel.wheels.quick
    quick.arrange(2, sliders=False)
    assert all(not row.slider.isVisibleTo(quick) and row.spin.isVisibleTo(quick) and row.diamond.isVisibleTo(quick)
               for row in quick.rows.values())
    quick.rows["saturation"].spin.setValue(1.3)
    window._finalize_color_history()
    assert _clip(window).color_grade.saturation == pytest.approx(1.3)
    assert quick.required(2, sliders=False)[0] < quick.required(2)[0]
