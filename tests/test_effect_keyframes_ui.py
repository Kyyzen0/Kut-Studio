"""Étape 4 de l'ADR-0008 : losanges d'images clés, menu et éditeur de courbes pour les paramètres d'effets animables (hors écran).

Les paramètres animables sont ``ANIMATABLE_EFFECT_PARAMS`` (correction de couleur : luminosité, contraste, saturation ;
vignette : intensité) ; flou, netteté, lueur, aberration chromatique et brume de chaleur n'ont aucun losange.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from core.animation import Keyframe
from core.composition_ops import CompositionError, convert_to_composition
from core.effects_model import EffectType, create_effect
from core.project_io import project_payload
from core.usage_level import UsageLevel
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes

ORDER = [
    ("cc1", EffectType.COLOR_CORRECTION), ("cc2", EffectType.COLOR_CORRECTION), ("vg", EffectType.VIGNETTE),
    ("sh", EffectType.SHARPEN), ("bl", EffectType.BLUR), ("gl", EffectType.GLOW),
    ("ca", EffectType.CHROMATIC_ABERRATION), ("hh", EffectType.HEAT_HAZE),
]


def _prepared(win, *, locked: bool = False, level: str = "advanced"):
    win.set_usage_level(level)
    track, clip = _add_track_clip(win, "video", "v", locked=locked)
    clip.effects = [create_effect(kind, effect_id=eid) for eid, kind in ORDER]
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    return track, clip


def _select(win, effect_id: str) -> None:
    ids = [e.id for e in win.properties_panel._current_effects]
    win.properties_panel.effects_list.setCurrentRow(ids.index(effect_id))
    QApplication.processEvents()


def _rows(win):
    return win.properties_panel.effect_keyframe_widgets()


def _frames(clip, property_id):
    return sorted((k.time_seconds, k.value) for k in clip.animation if k.property_name == property_id)


def _spin(win, name):
    return win.properties_panel._effect_param_widgets[name]


def test_only_the_animatable_parameters_have_diamonds(window):
    _prepared(window)
    expected = {"cc1": {"brightness", "contrast", "saturation"}, "cc2": {"brightness", "contrast", "saturation"},
                "vg": {"intensity"}, "sh": set(), "bl": set(), "gl": set(), "ca": set(), "hh": set()}
    from ui.properties_widgets.diamond_button import KeyframeDiamondButton

    for effect_id, names in expected.items():
        _select(window, effect_id)
        assert set(_rows(window)) == names, effect_id
        assert len(window.properties_panel.effect_parameters_container.findChildren(KeyframeDiamondButton)) == len(names)


def test_without_a_keyframe_the_spin_edits_the_base_value(window):
    _track, clip = _prepared(window)
    _select(window, "cc1")
    _spin(window, "brightness").setValue(0.25)
    assert clip.effects[0].params["brightness"] == pytest.approx(0.25)
    assert not clip.animation


def test_the_diamond_adds_and_removes_a_keyframe_at_the_playhead(window):
    _track, clip = _prepared(window)
    _select(window, "cc1")
    window.seek_to_position(1.0)
    row = _rows(window)["brightness"]
    entries = len(window.history)
    row.diamond.click()
    assert _frames(clip, "fx.cc1.brightness") == [(1.0, 0.0)]
    assert len(window.history) == entries + 1
    assert _rows(window)["brightness"].diamond.isChecked()
    _rows(window)["brightness"].diamond.click()                  # sous la tête : retire l'image clé
    assert not _frames(clip, "fx.cc1.brightness")
    assert len(window.history) == entries + 2
    assert clip.effects[0].params["brightness"] == pytest.approx(0.0)


def test_editing_an_animated_parameter_writes_the_keyframe_at_the_playhead(window):
    _track, clip = _prepared(window)
    _select(window, "cc1")
    window.seek_to_position(1.0)
    _rows(window)["brightness"].diamond.click()
    window.seek_to_position(2.0)
    entries = len(window.history)
    _spin(window, "brightness").setValue(0.3)                    # (c) animé : image clé à 2 s, la base ne bouge pas
    assert _frames(clip, "fx.cc1.brightness") == [(1.0, 0.0), (2.0, pytest.approx(0.3))]
    assert clip.effects[0].params["brightness"] == pytest.approx(0.0)
    assert len(window.history) == entries + 1
    _spin(window, "brightness").setValue(0.4)                    # même instant : mise à jour, pas de doublon
    assert _frames(clip, "fx.cc1.brightness") == [(1.0, 0.0), (2.0, pytest.approx(0.4))]
    assert len(window.history) == entries + 2


def test_previous_and_next_jump_between_keyframes(window):
    _track, clip = _prepared(window)
    _select(window, "cc1")
    clip.animation = [Keyframe("fx.cc1.brightness", 1.0, 0.1, id="a"), Keyframe("fx.cc1.brightness", 3.0, 0.5, id="b")]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    _select(window, "cc1")
    window.seek_to_position(2.0)
    row = _rows(window)["brightness"]
    assert row.previous.isEnabled() and row.next.isEnabled()
    row.next.click()
    assert window.playhead_seconds == pytest.approx(3.0)
    assert not _rows(window)["brightness"].next.isEnabled()
    _rows(window)["brightness"].previous.click()
    assert window.playhead_seconds == pytest.approx(1.0)
    assert not _rows(window)["brightness"].previous.isEnabled()


def test_the_spin_shows_the_evaluated_value_and_follows_the_playhead(window):
    _track, clip = _prepared(window)
    clip.animation = [Keyframe("fx.cc1.brightness", 1.0, 0.0, id="a"), Keyframe("fx.cc1.brightness", 3.0, 1.0, id="b")]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    _select(window, "cc1")
    window.seek_to_position(2.0)
    assert _spin(window, "brightness").value() == pytest.approx(0.5, abs=1e-3)
    window.seek_to_position(3.0)
    assert _spin(window, "brightness").value() == pytest.approx(1.0, abs=1e-3)
    assert _rows(window)["brightness"].diamond.isChecked()
    assert _spin(window, "contrast").value() == pytest.approx(clip.effects[0].params["contrast"])    # non animé : sa base


def test_undo_and_redo_restore_the_keyframes(window):
    _track, clip = _prepared(window)
    _select(window, "vg")
    window.seek_to_position(1.0)
    _rows(window)["intensity"].diamond.click()
    window.seek_to_position(2.0)
    _spin(window, "intensity").setValue(0.8)
    assert len(_frames(clip, "fx.vg.intensity")) == 2
    window.undo_last()
    clip = window.project.tracks[0].clips[0]
    assert len(_frames(clip, "fx.vg.intensity")) == 1
    window.undo_last()
    assert not window.project.tracks[0].clips[0].animation
    window.redo_last()
    window.redo_last()
    assert len(_frames(window.project.tracks[0].clips[0], "fx.vg.intensity")) == 2


@pytest.mark.parametrize("kind", ["track", "layer"])
def test_a_locked_clip_refuses_without_writing(window, kind):
    from dataclasses import replace

    from core.graphics import GraphicOverlay

    track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    assert _frames(clip, "fx.cc1.brightness")
    if kind == "track":
        track.locked = True
    else:
        clip.graphic = replace(clip.graphic or GraphicOverlay(), locked=True)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    _select(window, "cc1")
    before = (list(clip.animation), len(window.history), [dict(e.params) for e in clip.effects])
    row = _rows(window)["brightness"]
    assert not row.diamond.isEnabled() and not row.next.isEnabled() and not _spin(window, "brightness").isEnabled()
    for call in (
        lambda: window.on_effect_keyframe_set(clip.id, "fx.cc1.brightness", 2.0, 0.9),
        lambda: window.on_effect_keyframe_removed(clip.id, "fx.cc1.brightness", 1.0),
        lambda: window.on_effect_animation_cleared(clip.id, "cc1"),
        lambda: window.on_animation_toggled(clip.id, "fx.cc1.brightness", False),
    ):
        call()
    assert (list(clip.animation), len(window.history), [dict(e.params) for e in clip.effects]) == before


def test_two_instances_of_the_same_effect_keep_separate_curves(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    _select(window, "cc2")
    assert not _rows(window)["brightness"].diamond.isChecked()          # l'autre instance n'a pas de courbe
    _rows(window)["contrast"].diamond.click()
    assert {k.property_name for k in clip.animation} == {"fx.cc1.brightness", "fx.cc2.contrast"}
    window.seek_to_position(2.0)
    _spin(window, "contrast").setValue(1.5)
    assert len(_frames(clip, "fx.cc2.contrast")) == 2 and len(_frames(clip, "fx.cc1.brightness")) == 1
    assert not any(k.property_name == "fx.cc1.contrast" for k in clip.animation)


def test_the_menu_removes_the_animation_and_the_conversion_becomes_possible_again(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    _rows(window)["contrast"].diamond.click()
    with pytest.raises(CompositionError, match="effets animés"):
        convert_to_composition(window.project, [clip.id])
    panel = window.properties_panel
    texts = [a.text() for a in panel.effect_animation_menu("cc1", "brightness").actions() if not a.isSeparator()]
    assert any("Supprimer l'animation de l'effet" in t for t in texts) and any("Désactiver l'animation" in t for t in texts)
    entries = len(window.history)
    clear = next(a for a in panel.effect_animation_menu("cc1", "brightness").actions() if "Supprimer l'animation de l'effet" in a.text())
    clear.trigger()
    assert not [k for k in clip.animation if k.property_name.startswith("fx.")]
    assert len(window.history) == entries + 1                     # une seule étape pour tout l'effet
    assert not _rows(window)["brightness"].diamond.isChecked()
    convert_to_composition(window.project, [clip.id])             # ne lève plus


def test_the_menu_entry_for_one_parameter_keeps_the_others_and_the_shown_value(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _select(window, "cc1")
    for name in ("brightness", "contrast"):
        _rows(window)[name].diamond.click()
    window.seek_to_position(2.0)
    _spin(window, "brightness").setValue(0.6)
    disable = next(a for a in window.properties_panel.effect_animation_menu("cc1", "brightness").actions() if "Désactiver" in a.text())
    disable.trigger()
    assert not _frames(clip, "fx.cc1.brightness") and _frames(clip, "fx.cc1.contrast")
    assert clip.effects[0].params["brightness"] == pytest.approx(0.6)       # la valeur montrée est gardée


def test_the_saved_file_is_version_20_only_with_a_curve(window):
    _track, clip = _prepared(window)
    plain = json.dumps(project_payload(window.project), sort_keys=True)
    assert project_payload(window.project)["version"] == 17
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    assert project_payload(window.project)["version"] == 20
    window.on_effect_animation_cleared(clip.id, "cc1")
    assert json.dumps(project_payload(window.project), sort_keys=True) == plain      # octet pour octet comme avant


# ---------------------------------------------------------------- niveaux d'usage


def _levels(window, level: str):
    window.set_usage_level(level)
    QApplication.processEvents()


def test_the_diamonds_follow_the_usage_level_and_a_hot_switch_loses_nothing(window):
    _track, clip = _prepared(window, level="studio")
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    window.seek_to_position(2.0)
    _spin(window, "brightness").setValue(0.7)
    window.properties_panel.effects_group.show()
    snapshot = (list(clip.animation), [dict(e.params) for e in clip.effects])
    _levels(window, "simple")
    assert all(row.gate.isHidden() for row in _rows(window).values())
    _levels(window, "studio")
    assert all(not row.gate.isHidden() for row in _rows(window).values())
    _levels(window, "advanced")
    assert all(not row.gate.isHidden() for row in _rows(window).values())
    _levels(window, "studio")
    assert (list(clip.animation), [dict(e.params) for e in clip.effects]) == snapshot
    assert _spin(window, "brightness").value() == pytest.approx(0.7, abs=1e-3)     # la valeur évaluée reste affichée


def test_the_menu_is_advanced_beyond_the_toggle_and_the_effect_clear(window):
    _track, clip = _prepared(window, level="studio")
    window.seek_to_position(1.0)
    _select(window, "cc1")
    _rows(window)["brightness"].diamond.click()
    panel = window.properties_panel

    def entries():
        return [a.text() for a in panel.effect_animation_menu("cc1", "brightness").actions() if not a.isSeparator()]

    studio = entries()
    assert len(studio) == 2                                       # désactiver ; supprimer l'animation de l'effet
    _levels(window, "advanced")
    advanced = entries()
    assert len(advanced) > len(studio) and any("Interpolation" in t or "interpolation" in t for t in advanced)
    assert panel.usage_level is UsageLevel.ADVANCED


def test_no_level_ever_disables_the_rows(window):
    _prepared(window, level="studio")
    _select(window, "cc1")
    for level in ("simple", "studio", "advanced"):
        _levels(window, level)
        for row in _rows(window).values():
            assert row.diamond.isEnabled() and row.gate.isEnabled()      # un niveau montre ou cache, jamais setEnabled


# ---------------------------------------------------------------- éditeur de courbes


def test_the_graph_editor_lists_the_animatable_parameters_with_readable_labels(window):
    _track, clip = _prepared(window)
    editor = window.open_graph_editor()
    labels = {editor.property_combo.itemData(i): editor.property_combo.itemText(i) for i in range(editor.property_combo.count())}
    assert labels["fx.cc1.brightness"] == "Correction couleur — Luminosité"
    assert labels["fx.cc2.brightness"] == "Correction couleur n° 2 — Luminosité"
    assert labels["fx.vg.intensity"].endswith("Intensité") and "Vignette" in labels["fx.vg.intensity"]
    assert not [pid for pid in labels if pid.startswith("fx.") and pid.split(".")[1] in {"sh", "bl", "gl", "ca", "hh"}]
    assert len([pid for pid in labels if pid.startswith("fx.")]) == 7


def test_a_curve_drawn_from_the_diamond_menu_opens_in_the_graph_editor(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _select(window, "vg")
    _rows(window)["intensity"].diamond.click()
    graph = next(a for a in window.properties_panel.effect_animation_menu("vg", "intensity").actions() if "ourbe" in a.text() or "raph" in a.text())
    graph.trigger()
    assert window.graph_editor.property_id == "fx.vg.intensity"
    assert len(window.graph_editor.curve().keyframes) == 1


def test_the_shortcuts_without_an_active_property_do_not_create_effect_curves(window):
    _track, clip = _prepared(window)
    window.active_animation_property = None
    window.seek_to_position(1.0)
    window.add_keyframe_at_playhead()
    assert not [k for k in clip.animation if k.property_name.startswith("fx.")]


def test_show_more_options_reveals_the_diamonds_at_simple(window):
    """Le lien « Afficher plus d'options » révèle le groupe Effets (Studio) au niveau Simple : ses losanges aussi."""
    _prepared(window, level="simple")
    _select(window, "cc1")
    panel = window.properties_panel
    assert all(row.gate.isHidden() for row in _rows(window).values())
    panel.more_options_button.setChecked(True)
    assert all(not row.gate.isHidden() for row in _rows(window).values())
    assert panel.usage_level is UsageLevel.SIMPLE
    panel.more_options_button.setChecked(False)
    assert all(row.gate.isHidden() for row in _rows(window).values())


def test_clearing_an_effect_animation_in_the_model_keeps_the_shown_values_and_drops_orphans():
    from core.keyframe_editing import clear_effect_animation
    from core.project_model import Clip, Project, Track

    clip = Clip(id="c", asset_id="a", track_id="t", timeline_start=0.0, source_in=0.0, source_out=4.0,
                effects=[create_effect(EffectType.COLOR_CORRECTION, effect_id="e1"),
                                         create_effect(EffectType.COLOR_CORRECTION, effect_id="other")])
    clip.animation = [Keyframe("fx.e1.brightness", 0.0, 0.0, id="a"), Keyframe("fx.e1.brightness", 2.0, 0.5, id="b"),
                      Keyframe("fx.e1.radius", 1.0, 3.0, id="o"), Keyframe("fx.other.brightness", 1.0, 1.0, id="k")]
    track = Track(id="t", name="T", type="video")
    track.clips.append(clip)
    project = Project(name="P")
    project.tracks.append(track)
    assert clear_effect_animation(project, "c", "e1", 1.0) == 2
    assert [k.id for k in clip.animation] == ["k"]          # l'orpheline part, l'autre effet reste
    assert clip.effects[0].params["brightness"] == pytest.approx(0.25)             # valeur montrée à 1 s
