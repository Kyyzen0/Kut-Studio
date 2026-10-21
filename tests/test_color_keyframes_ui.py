"""Étape 8 de l'ADR-0008 : losanges d'images clés sous les sept réglages de couleur de l'inspecteur (hors écran, vraie fenêtre).

Les roues de la page Couleur : ``tests/test_color_wheel_keyframes_ui.py``. Le modèle : ``tests/test_grade_keyframing.py``.
Le moniteur n'est pas vu à l'écran (le visualiseur est noir hors écran) : on lit la valeur évaluée que le moniteur reçoit.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph
from core.grade_targets import SCALAR_NAMES, effective_grade
from core.usage_level import UsageLevel
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes
from ui import i18n
from ui.properties_widgets.diamond_button import KeyframeDiamondButton

# Une valeur à l'intérieur des bornes de chaque réglage, différente du neutre.
SAMPLE = {
    "exposure": 0.6,
    "contrast": 0.3,
    "saturation": 1.4,
    "temperature": 25.0,
    "hue": 40.0,
    "shadows": 0.2,
    "highlights": -0.3,
}


def _prepared(win, *, locked: bool = False, grade=None, level: str = "advanced"):
    win.set_usage_level(level)
    track, clip = _add_track_clip(win, "video", "v", locked=locked)
    if grade is not None:
        clip.color_grade = grade
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    return track, clip


def _rows(win):
    return win.properties_panel.color_keyframe_widgets()


def _spin(win, name):
    return win.properties_panel.color_field_spins[name]


def _frames(clip, property_id):
    return sorted(
        (k.time_seconds, k.value)
        for k in clip.animation
        if k.property_name == property_id
    )


def _clip(win):
    """Le clip du projet courant (annuler / rétablir remplacent l'objet projet : on ne garde pas l'ancien clip)."""
    return win.project.tracks[-1].clips[0]


def _steps(win) -> int:
    win._finalize_color_history()
    return len(win.history)


def _nodes() -> ColorNodeGraph:
    return ColorNodeGraph.serial(
        (ColorNode("n1", ColorGrade(exposure=0.2)), ColorNode("n2", ColorGrade()))
    )


# ---------------------------------------------------------------- construction


def test_each_of_the_seven_settings_has_one_diamond_and_nothing_else_does(window):
    _prepared(window)
    assert tuple(_rows(window)) == SCALAR_NAMES
    group = window.properties_panel.color_group
    assert len(group.findChildren(KeyframeDiamondButton)) == 7
    for name, row in _rows(window).items():
        assert row.diamond.property_name == f"color.{name}"
        assert (
            row.diamond.accessibleName()
            and row.previous.accessibleName()
            and row.next.accessibleName()
        )


def test_without_a_keyframe_the_spin_edits_the_base_value(window):
    _track, clip = _prepared(window)
    _spin(window, "exposure").setValue(0.25)
    window._finalize_color_history()
    assert clip.color_grade.exposure == pytest.approx(0.25)
    assert not clip.animation


# ---------------------------------------------------------------- losange, une famille de réglages à la fois


@pytest.mark.parametrize("name", SCALAR_NAMES)
def test_the_diamond_adds_and_removes_a_keyframe_at_the_playhead(window, name):
    _track, clip = _prepared(window, grade=ColorGrade().with_field(name, SAMPLE[name]))
    window.seek_to_position(1.0)
    entries = len(window.history)
    row = _rows(window)[name]
    assert not row.diamond.isChecked() and not row.diamond.animated
    row.diamond.click()
    assert _frames(clip, f"color.{name}") == [
        (1.0, pytest.approx(SAMPLE[name]))
    ]  # la valeur montrée devient la première clé
    assert len(window.history) == entries + 1
    assert (
        _rows(window)[name].diamond.isChecked() and _rows(window)[name].diamond.animated
    )
    _rows(window)[name].diamond.click()  # sous la tête : retire l'image clé
    assert not clip.animation and len(window.history) == entries + 2
    assert getattr(clip.color_grade, name) == pytest.approx(SAMPLE[name]), (
        "le réglage garde la valeur qu'il montrait"
    )
    assert (
        not _rows(window)[name].diamond.isChecked()
        and not _rows(window)[name].diamond.animated
    )


def test_the_diamond_has_three_states_and_the_spin_follows_the_curve(window):
    _track, clip = _prepared(window)
    clip.animation = [
        Keyframe("color.exposure", 1.0, -1.0, InterpolationType.LINEAR),
        Keyframe("color.exposure", 3.0, 1.0, InterpolationType.LINEAR),
    ]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    entries = len(window.history)
    window.seek_to_position(0.0)
    row = _rows(window)["exposure"]
    assert (
        not row.diamond.isChecked() and row.diamond.animated
    )  # vide, animé : contour accentué
    window.seek_to_position(1.0)
    assert _rows(window)["exposure"].diamond.isChecked()  # plein : une clé sous la tête
    window.seek_to_position(2.0)
    assert (
        not _rows(window)["exposure"].diamond.isChecked()
        and _rows(window)["exposure"].diamond.animated
    )  # entre-deux
    assert _spin(window, "exposure").value() == pytest.approx(0.0), (
        "le curseur montre la valeur évaluée"
    )
    window.seek_to_position(2.5)
    assert _spin(window, "exposure").value() == pytest.approx(0.5)
    assert len(window.history) == entries, "suivre la tête de lecture n'écrit rien"
    assert clip.color_grade is None, "...et ne touche pas à la base"


def test_editing_an_animated_setting_writes_the_keyframe_at_the_playhead_and_leaves_the_base(
    window,
):
    _track, clip = _prepared(window, grade=ColorGrade(contrast=0.1))
    window.seek_to_position(1.0)
    _rows(window)["contrast"].diamond.click()
    window.seek_to_position(2.0)
    entries = _steps(window)
    _spin(window, "contrast").setValue(0.3)  # animé : image clé à 2 s
    assert _steps(window) == entries + 1
    assert _frames(clip, "color.contrast") == [
        (1.0, pytest.approx(0.1)),
        (2.0, pytest.approx(0.3)),
    ]
    assert clip.color_grade.contrast == pytest.approx(0.1), "la base ne bouge pas"
    _spin(window, "contrast").setValue(
        0.4
    )  # même instant : mise à jour, pas de doublon
    assert _steps(window) == entries + 2
    assert _frames(clip, "color.contrast") == [
        (1.0, pytest.approx(0.1)),
        (2.0, pytest.approx(0.4)),
    ]
    assert _rows(window)["contrast"].diamond.isChecked()


def test_a_burst_of_edits_is_one_undo_step_and_undo_redo_restore_both_states(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["hue"].diamond.click()
    entries = _steps(window)
    for value in (10.0, 20.0, 30.0):
        _spin(window, "hue").setValue(value)
    assert _steps(window) == entries + 1, "une rafale, une étape d'historique"
    assert _frames(clip, "color.hue") == [(1.0, 30.0)]
    window.undo_last()
    assert _frames(_clip(window), "color.hue") == [(1.0, 0.0)]
    assert _spin(window, "hue").value() == pytest.approx(0.0)
    window.undo_last()  # l'activation du losange
    assert not _clip(window).animation and not _rows(window)["hue"].diamond.isChecked()
    window.redo_last()
    window.redo_last()
    assert _frames(_clip(window), "color.hue") == [(1.0, 30.0)]
    assert (
        _spin(window, "hue").value() == pytest.approx(30.0)
        and _rows(window)["hue"].diamond.isChecked()
    )


def test_a_value_is_clamped_by_the_registry_like_the_inspector(window):
    _track, clip = _prepared(window)
    _rows(window)["exposure"].diamond.click()
    window.on_color_grade_field_changed(
        clip.id, "exposure", 7.0
    )  # un signal qui dépasse la borne du modèle
    window._finalize_color_history()
    assert _frames(clip, "color.exposure")[0][1] == 2.0


def test_previous_and_next_jump_between_keyframes(window):
    _track, clip = _prepared(window)
    clip.animation = [
        Keyframe("color.shadows", 1.0, 0.1, InterpolationType.LINEAR),
        Keyframe("color.shadows", 3.0, 0.5, InterpolationType.LINEAR),
    ]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(2.0)
    row = _rows(window)["shadows"]
    assert row.previous.isEnabled() and row.next.isEnabled()
    row.next.click()
    assert (
        window.playhead_seconds == pytest.approx(3.0)
        and not _rows(window)["shadows"].next.isEnabled()
    )
    _rows(window)["shadows"].previous.click()
    assert window.playhead_seconds == pytest.approx(1.0)
    assert not _rows(window)["exposure"].previous.isEnabled(), (
        "un réglage non animé n'a pas de voisin"
    )


def test_a_playhead_outside_the_clip_adds_nothing(window):
    _track, clip = _prepared(window)
    clip.timeline_start = 3.0
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(1.0)  # avant le clip
    entries = len(window.history)
    _rows(window)["exposure"].diamond.click()
    assert not clip.animation and len(window.history) == entries
    assert not _rows(window)["exposure"].diamond.isChecked()


# ---------------------------------------------------------------- étalonnage découpé en nœuds


def test_a_node_graph_disables_the_diamonds_says_why_and_keeps_writing_base_values(
    window,
):
    _track, clip = _prepared(window, grade=_nodes())
    panel = window.properties_panel
    assert (
        not panel.color_animation_label.isHidden()
        and i18n.translate("color.animation.nodes")
        in panel.color_animation_label.text()
    )
    assert all(not row.diamond.isEnabled() for row in _rows(window).values())
    assert panel.color_animation_clear_button.isHidden(), "aucune image clé à retirer"
    _rows(window)["exposure"].diamond.click()
    assert not clip.animation, "un losange désactivé n'écrit rien"
    window.on_color_keys_toggled(
        clip.id, ["color.exposure"]
    )  # un signal qui atteint tout de même la fenêtre
    assert not clip.animation
    _spin(window, "hue").setValue(12.0)
    window._finalize_color_history()
    assert not clip.animation and clip.color_grade.correctors[0].grade.hue == 12.0, (
        "valeur de base du nœud courant"
    )
    assert (
        panel.color_animation_label.toolTip() == ""
        and "nœud" in _rows(window)["exposure"].diamond.toolTip().lower()
    )


def test_ignored_keys_of_a_node_graph_are_announced_and_can_be_removed_in_one_undoable_step(
    window,
):
    _track, clip = _prepared(window)
    clip.animation = [
        Keyframe("color.exposure", 1.0, 0.5, InterpolationType.LINEAR),
        Keyframe("color.exposure", 2.0, 0.9, InterpolationType.LINEAR),
    ]
    clip.color_grade = _nodes()
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    panel = window.properties_panel
    assert (
        "2" in panel.color_animation_label.text()
        and not panel.color_animation_clear_button.isHidden()
    )
    entries = len(window.history)
    panel.color_animation_clear_button.click()
    assert not clip.animation and len(window.history) == entries + 1
    assert panel.color_animation_clear_button.isHidden()
    window.undo_last()
    assert (
        len(_clip(window).animation) == 2
        and not window.properties_panel.color_animation_clear_button.isHidden()
    )


def test_back_to_a_single_node_the_diamonds_work_again(window):
    _track, clip = _prepared(window, grade=_nodes())
    clip.color_grade = ColorGrade()
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    assert (
        _rows(window)["exposure"].diamond.isEnabled()
        and window.properties_panel.color_animation_label.isHidden()
    )
    _rows(window)["exposure"].diamond.click()
    assert len(clip.animation) == 1


# ---------------------------------------------------------------- réinitialisation et suppression


def test_reset_also_removes_the_color_keyframes_in_one_undoable_step(window):
    _track, clip = _prepared(window, grade=ColorGrade(saturation=1.3))
    clip.animation = [
        Keyframe("color.exposure", 0.0, 0.0, InterpolationType.LINEAR),
        Keyframe("color.exposure", 2.0, 1.0, InterpolationType.LINEAR),
        Keyframe("color.lift.r", 1.0, 0.2, InterpolationType.LINEAR),
        Keyframe("fx.e1.intensity", 1.0, 0.5, InterpolationType.LINEAR),
    ]
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    entries = len(window.history)
    window.properties_panel.color_reset_button.click()
    assert len(window.history) == entries + 1, (
        "réglage et images clés partent dans la même étape"
    )
    assert clip.color_grade == ColorGrade.identity()
    assert [k.property_name for k in clip.animation] == ["fx.e1.intensity"], (
        "les autres animations ne sont pas touchées"
    )
    assert (
        i18n.translate("status.color.reset_keys", count=2)
        == window.statusBar().currentMessage()
    )
    assert not any(row.diamond.animated for row in _rows(window).values())
    window.undo_last()
    assert _clip(window).color_grade.saturation == 1.3
    assert sorted(k.property_name for k in _clip(window).animation) == [
        "color.exposure",
        "color.exposure",
        "color.lift.r",
        "fx.e1.intensity",
    ]
    assert _rows(window)["exposure"].diamond.animated
    window.redo_last()
    assert [k.property_name for k in _clip(window).animation] == ["fx.e1.intensity"]


def test_reset_without_keys_says_nothing_more(window):
    _track, clip = _prepared(window, grade=ColorGrade(hue=10.0))
    window.statusBar().clearMessage()
    window.properties_panel.color_reset_button.click()
    assert (
        clip.color_grade == ColorGrade.identity()
        and window.statusBar().currentMessage() == ""
    )


def test_reset_of_a_node_graph_node_keeps_the_ignored_keys(window):
    _track, clip = _prepared(window)
    clip.animation = [Keyframe("color.exposure", 1.0, 0.5, InterpolationType.LINEAR)]
    clip.color_grade = _nodes()
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.properties_panel.color_reset_button.click()
    assert len(clip.animation) == 1, (
        "le clip reste découpé en nœuds : ses clés sont ignorées, pas perdues"
    )


def test_the_context_menu_offers_the_toggle_the_graph_editor_and_the_removal(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    panel = window.properties_panel
    menu = panel.color_animation_menu("color.exposure")
    assert [a.text() for a in menu.actions() if a.text()][0] == i18n.translate(
        "animation.menu.enable"
    )
    assert i18n.translate("animation.menu.clear_grade") not in [
        a.text() for a in menu.actions()
    ], "rien à supprimer"
    _rows(window)["exposure"].diamond.click()
    window.seek_to_position(2.0)
    menu = panel.color_animation_menu("color.exposure")
    texts = [a.text() for a in menu.actions() if a.text()]
    assert (
        i18n.translate("animation.menu.disable") in texts
        and i18n.translate("animation.menu.clear_grade") in texts
    )
    assert (
        i18n.translate("animation.menu.graph") in texts
        and i18n.translate("animation.menu.copy") in texts
    )
    entries = len(window.history)
    next(
        a
        for a in menu.actions()
        if a.text() == i18n.translate("animation.menu.clear_grade")
    ).trigger()
    assert not clip.animation and len(window.history) == entries + 1


def test_the_menu_entry_graph_editor_opens_on_the_color_property(window):
    _track, clip = _prepared(window)
    _rows(window)["exposure"].diamond.click()
    editor = window.open_graph_editor("color.exposure")
    assert editor.isVisible() and "color.exposure" in window._graph_properties(clip)
    assert window.active_animation_property == "color.exposure"


def test_the_graph_editor_lists_the_color_properties_only_for_an_animatable_grade(
    window,
):
    _track, clip = _prepared(window)
    listed = window._graph_properties(clip)
    assert (
        all(f"color.{name}" in listed for name in SCALAR_NAMES)
        and "color.lift.y" in listed
    )
    assert not any(
        p.startswith("color.") for p in window._animation_properties(clip)
    ), "les raccourcis ne les créent pas"
    clip.color_grade = _nodes()
    assert not any(p.startswith("color.") for p in window._graph_properties(clip))


# ---------------------------------------------------------------- verrous et genres de piste


def test_a_locked_track_disables_the_rows_and_every_signal_is_refused(window):
    _track, clip = _prepared(window, locked=True, grade=ColorGrade(exposure=0.2))
    clip.animation = [Keyframe("color.hue", 1.0, 5.0, InterpolationType.LINEAR)]
    snapshot = (list(clip.animation), clip.color_grade)
    entries = len(window.history)
    assert not window.properties_panel.color_group.isEnabled()
    assert all(
        not row.diamond.isEnabled() or not row.diamond.isEnabledTo(window)
        for row in _rows(window).values()
    )
    refused = i18n.translate("edit.refused.track_locked")
    for send in (
        lambda: window.on_color_keys_toggled(clip.id, ["color.exposure"]),
        lambda: window.on_color_grade_field_changed(
            clip.id, "hue", 33.0
        ),  # le signal d'un réglage animé
        lambda: window.on_color_animation_cleared(clip.id),
    ):
        window.statusBar().clearMessage()
        send()
        assert window.statusBar().currentMessage() == refused, (
            "refusé par la fenêtre, avec le message du verrou"
        )
    window.on_color_grade_field_changed(
        clip.id, "exposure", 1.0
    )  # base : ignoré, comme avant
    window._finalize_color_history()
    assert (list(clip.animation), clip.color_grade) == snapshot and len(
        window.history
    ) == entries


def test_an_audio_clip_refuses_the_grade_keys(window):
    _track, clip = _add_track_clip(window, "audio", "a")
    for send in (
        lambda: window.on_color_keys_toggled(clip.id, ["color.exposure"]),
        lambda: window.on_color_animation_cleared(clip.id),
    ):
        window.statusBar().clearMessage()
        send()
        assert window.statusBar().currentMessage() == i18n.translate(
            "edit.refused.not_visual"
        )
    assert not clip.animation


# ---------------------------------------------------------------- niveaux d'usage


def test_the_diamonds_are_studio_hidden_at_simple_and_shown_by_more_options(window):
    _prepared(window, level="simple")
    panel = window.properties_panel
    gates = [row.gate for row in _rows(window).values()]
    assert all(gate.minimum is UsageLevel.STUDIO for gate in gates)
    assert all(gate.isHidden() for gate in gates)
    panel.more_options_button.setChecked(True)
    assert all(not gate.isHidden() for gate in gates)
    panel.more_options_button.setChecked(False)
    window.set_usage_level("studio")
    assert all(not gate.isHidden() for gate in gates)
    window.set_usage_level("advanced")
    assert all(not gate.isHidden() for gate in gates)


def test_the_levels_only_show_or_hide_they_never_change_the_values(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["exposure"].diamond.click()
    before = (list(clip.animation), _spin(window, "exposure").value())
    for level in ("simple", "studio", "advanced"):
        window.set_usage_level(level)
        QApplication.processEvents()
        assert (list(clip.animation), _spin(window, "exposure").value()) == before


# ---------------------------------------------------------------- le moniteur et les presets


def test_the_monitor_value_follows_the_keyframe_the_inspector_wrote(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["exposure"].diamond.click()
    window.seek_to_position(3.0)
    _spin(window, "exposure").setValue(1.0)
    window._finalize_color_history()
    assert effective_grade(clip, 2.0).exposure == pytest.approx(0.5)
    assert effective_grade(clip, 3.0).exposure == pytest.approx(1.0)


def test_applying_a_preset_keeps_the_animated_settings_and_says_so(window):
    from core.color_grading import builtin_color_preset_ids

    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["exposure"].diamond.click()
    preset_id = sorted(builtin_color_preset_ids())[0]
    window.on_color_preset_applied(clip.id, preset_id)
    assert [k.property_name for k in clip.animation] == ["color.exposure"]
    assert window.statusBar().currentMessage() == i18n.translate(
        "status.color.preset_keeps_keys"
    )


def test_the_wheel_value_helper_keeps_base_wheels_distinct_from_keys(window):
    """Les sept réglages ne touchent pas aux roues : un losange de réglage ne crée aucune clé de roue."""
    _track, clip = _prepared(window, grade=ColorGrade(lift=Wheel(0.1, 0.0, -0.1, 0.0)))
    _rows(window)["exposure"].diamond.click()
    assert all(not k.property_name.startswith("color.lift") for k in clip.animation)


def test_the_spin_ranges_are_the_bounds_of_the_registry(window):
    """Aucune valeur affichée ou proposée que ``spec.clamp`` ramènerait à sa borne (point 2 de la liste de revue)."""
    from core.grade_targets import grade_spec, scalar_property_id

    _prepared(window)
    for name in SCALAR_NAMES:
        spec = grade_spec(scalar_property_id(name))
        spin = _spin(window, name)
        assert (spin.minimum(), spin.maximum()) == (spec.minimum, spec.maximum), name


def test_a_locked_graphic_layer_on_the_clip_refuses_every_entry(window):
    from core.graphics import GraphicOverlay

    _track, clip = _prepared(window, grade=ColorGrade(exposure=0.2))
    clip.animation = [Keyframe("color.hue", 1.0, 5.0, InterpolationType.LINEAR)]
    clip.graphic = GraphicOverlay(locked=True)
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    snapshot = (list(clip.animation), clip.color_grade)
    refused = i18n.translate("edit.refused.layer_locked")
    for send in (
        lambda: window.on_color_keys_toggled(clip.id, ["color.exposure"]),
        lambda: window.on_color_grade_field_changed(clip.id, "hue", 33.0),
        lambda: window.on_color_animation_cleared(clip.id),
    ):
        window.statusBar().clearMessage()
        send()
        assert window.statusBar().currentMessage() == refused
    window._finalize_color_history()
    assert (list(clip.animation), clip.color_grade) == snapshot and len(
        window.history
    ) == 1
    assert not window.properties_panel.color_group.isEnabled()


def test_the_graph_editor_combo_lists_the_color_settings_by_name(window):
    _track, clip = _prepared(window)
    _rows(window)["exposure"].diamond.click()
    editor = window.open_graph_editor("color.exposure")
    items = {
        editor.property_combo.itemData(i): editor.property_combo.itemText(i)
        for i in range(editor.property_combo.count())
    }
    assert items["color.exposure"] == i18n.translate("color.property.exposure")
    assert items["color.lift.r"] == i18n.translate("color.property.lift.r")
    assert editor.target().id == "color.exposure"


def test_editing_a_curve_in_the_graph_editor_moves_the_inspector_value(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["saturation"].diamond.click()
    key = next(k for k in clip.animation if k.property_name == "color.saturation")
    from core.keyframe_editing import KeyframeRef

    window.graph_set_keyframe(
        KeyframeRef(clip.id, "color.saturation", key.id), value=1.7
    )
    window.on_graph_edit("Graph Editor", clip.id, record=True)
    assert _spin(window, "saturation").value() == pytest.approx(1.7)


def test_every_animation_edit_refreshes_the_inspector_by_itself(window, monkeypatch):
    """Sans le rechargement de la timeline (qui ré-affiche le clip), le passage par ``_after_animation_edit`` suffit."""
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _rows(window)["saturation"].diamond.click()
    key = next(k for k in clip.animation if k.property_name == "color.saturation")
    monkeypatch.setattr(
        window, "_reload_timeline_preserving_selection", lambda *_a, **_k: None
    )
    from core.keyframe_editing import KeyframeRef

    window.graph_set_keyframe(
        KeyframeRef(clip.id, "color.saturation", key.id), value=1.7
    )
    window.on_graph_edit("Graph Editor", clip.id, record=False)
    assert _spin(window, "saturation").value() == pytest.approx(1.7)


def test_a_language_change_retranslates_rows_tooltips_notice_and_wheels(window):
    """Noms accessibles, infobulles, avis et bouton suivent la langue en cours d'exécution (inspecteur et page Couleur)."""
    from core.workspace_state import PAGE_COLOR

    try:  # abonnés de la fenêtre conservés : pas de reset_for_tests
        _track, clip = _prepared(window, grade=_nodes())
        clip.animation = [
            Keyframe("color.exposure", 1.0, 0.5, InterpolationType.LINEAR)
        ]
        window.history.reset(window.project)
        window._reload_timeline_preserving_selection(clip.id)
        window.on_clip_selected(clip.id)
        window.switch_page(PAGE_COLOR)
        panel = window.properties_panel
        for language in ("en", "es", "fr"):
            i18n.set_language(language)
            window._retranslate_ui()
            for name, row in _rows(window).items():
                label = i18n.translate(panel._color_field_keys[name])
                assert row.previous.accessibleName() == i18n.translate(
                    "a11y.keyframe.previous", property=label
                ), (language, name)
                assert row.diamond.accessibleName() == i18n.translate(
                    "a11y.keyframe.toggle", property=label
                )
                assert row.next.accessibleName() == i18n.translate(
                    "a11y.keyframe.next", property=label
                )
                assert row.diamond.toolTip() == i18n.translate(
                    "color.animation.disabled_tip"
                )
            assert panel.color_animation_label.text().startswith(
                i18n.translate("color.animation.nodes")
            )
            assert panel.color_animation_clear_button.text() == i18n.translate(
                "color.animation.clear"
            )
            page = window.color_panel
            assert page.animation_notice_label.text().startswith(
                i18n.translate("color.animation.nodes")
            )
            assert page.animation_clear_button.text() == i18n.translate(
                "color.animation.clear"
            )
            for wheel, cell in page.wheels.cells.items():
                title = i18n.translate(f"color.wheel.{wheel}")
                assert cell.keys.diamond.accessibleName() == i18n.translate(
                    "a11y.keyframe.toggle", property=title
                )
                assert cell.keys.diamond.toolTip() == i18n.translate(
                    "color.animation.disabled_tip"
                )
        clip.color_grade = ColorGrade()  # animable : l'infobulle nomme le réglage
        window._reload_timeline_preserving_selection(clip.id)
        window.on_clip_selected(clip.id)
        for language in ("en", "es"):
            i18n.set_language(language)
            window._retranslate_ui()
            assert (
                panel._color_field_label("exposure")
                in _rows(window)["exposure"].diamond.toolTip()
            )
            assert (
                i18n.translate("color.animation.wheel_tip", wheel="Lift")
                in window.color_panel.wheels.cells["lift"].keys.diamond.toolTip()
            )
    finally:
        i18n.set_language("fr")
