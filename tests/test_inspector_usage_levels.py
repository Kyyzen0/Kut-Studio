"""Inspecteur et niveaux d'usage (ADR-0006, étape 4, lot 3).

Un niveau ne montre ou ne cache que : jamais ``setEnabled``, jamais une valeur. Au niveau Avancé, l'inspecteur est celui
d'avant les niveaux (``tests/data/inspector_visible_before_usage_levels.json``, produit par l'inspecteur de ``origin/main``
avant ce lot).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QListView, QWidget
from test_inspector_lock_guards import _add_track_clip, _lock, _prepare, _unlock, window  # noqa: F401 - fixture `window`

from core.audio_effects_model import AudioEffectType
from core.blend_modes import BlendMode
from core.graphics import GraphicOverlay
from core.text_style import default_text_style
from core.time_remapping import TimeRemapping
from core.timeline_view_model import TimelineClipView
from core.usage_level import UsageLevel
from core.visual_effects import ClipTransform
from ui import i18n
from ui.properties_panel import PropertiesPanel

SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED
GOLDEN = Path(__file__).parent / "data" / "inspector_visible_before_usage_levels.json"
IGNORED = ("studio_block", "level_gate", "advanced_fold", "more_options_button", "fill_frame_quick")


def _panel(qtbot, level=ADVANCED) -> PropertiesPanel:
    panel = PropertiesPanel(lambda *_args: None)
    qtbot.addWidget(panel)
    panel.resize(320, 900)
    panel.show()
    panel.set_usage_level(level)
    QApplication.processEvents()
    return panel


def _view(**overrides) -> TimelineClipView:
    fields = dict(id="c", track_id="T", track_index=0, start=0.0, end=4.0, label="Plan", text="", source_path="",
                  color_key="#fff", track_type="video", enabled=True, keyframes=(), transform=ClipTransform())
    fields.update(overrides)
    return TimelineClipView(**fields)


def _shown(panel, widget) -> bool:
    return widget.isVisibleTo(panel)


def _groups(panel) -> dict[str, QWidget]:
    return {
        "project": panel._all_inspector_groups[0], "clip": panel._all_inspector_groups[1], "transition": panel.transition_group,
        "color": panel.color_group, "movement": panel.movement_group, "speed": panel.speed_group,
        "graphics": panel.graphics_group, "volume": panel._all_inspector_groups[7], "audio": panel.audio_group,
        "subtitle": panel.subtitle_group, "effects": panel.effects_group, "audio_effects": panel.audio_effects_group,
        "compositing": panel.compositing_group, "tracking": panel.tracking_group,
    }


# ---------------------------------------------------------------- groupes par niveau


def _visible_groups(panel, tab: int) -> set[str]:
    panel._select_inspector_tab(tab)
    QApplication.processEvents()
    return {name for name, group in _groups(panel).items() if _shown(panel, group)}


def test_the_clip_tab_shows_per_level_the_groups_of_the_table(qtbot):
    simple = {"project", "clip", "movement", "volume", "audio", "compositing"}
    studio = simple | {"color", "speed", "effects", "audio_effects"}
    panel = _panel(qtbot, SIMPLE)
    assert _visible_groups(panel, 0) == simple
    panel.set_usage_level(STUDIO)
    assert _visible_groups(panel, 0) == studio
    panel.set_usage_level(ADVANCED)
    assert _visible_groups(panel, 0) == studio                  # le suivi a son onglet ; les groupes conditionnels restent cachés
    assert _visible_groups(panel, 6) == {"clip", "tracking"}


@pytest.mark.parametrize("level", [SIMPLE, STUDIO, ADVANCED])
def test_conditional_groups_stay_hidden_without_their_context_at_every_level(qtbot, level):
    panel = _panel(qtbot, level)
    for name in ("transition", "subtitle", "graphics"):
        assert not _shown(panel, _groups(panel)[name])


def test_a_subtitle_and_a_graphic_layer_show_at_every_level_but_a_transition_needs_studio(qtbot):
    panel = _panel(qtbot, SIMPLE)
    panel.show_clip(_view(track_type="subtitle", text="Salut", text_style=default_text_style()))
    assert _shown(panel, panel.subtitle_group)
    panel.show_clip(_view(track_type="graphics", graphic=GraphicOverlay()))
    panel._select_inspector_tab(4)
    assert _shown(panel, panel.graphics_group)
    panel._set_group_condition(panel.transition_group, True)
    assert not _shown(panel, panel.transition_group)            # niveau Studio
    panel.set_usage_level(STUDIO)
    panel._select_inspector_tab(0)
    assert _shown(panel, panel.transition_group)


def test_the_crop_group_is_visible_in_simple_and_alone_in_its_group(qtbot):
    panel = _panel(qtbot, SIMPLE)
    panel.show_clip(_view())
    comp = panel.compositing_group
    assert _shown(panel, comp) and _shown(panel, comp.crop_section) and _shown(panel, comp.crop_editor)
    assert not any(_shown(panel, section) for section in (comp.blend_section, comp.mask_section, comp.chroma_section))
    panel.set_usage_level(STUDIO)
    assert all(_shown(panel, section) for section in comp.sections())


# ---------------------------------------------------------------- lien « Afficher plus d'options »


def test_the_link_reveals_the_hidden_groups_and_a_level_change_resets_it_without_rebuilding_widgets(qtbot):
    panel = _panel(qtbot, SIMPLE)
    button = panel.more_options_button
    assert _shown(panel, button) and button.text() == i18n.translate("usage.inspector.more")
    assert not _shown(panel, panel.color_group)
    widgets_before = set(map(id, panel.findChildren(QWidget)))
    button.click()
    assert panel.more_groups_shown() and _shown(panel, panel.color_group) and _shown(panel, panel.effects_group)
    assert button.text() == i18n.translate("usage.inspector.fewer")
    panel._select_inspector_tab(6)
    assert _shown(panel, panel.tracking_group)                  # un groupe avancé, d'un geste
    button.click()
    assert not panel.more_groups_shown() and not _shown(panel, panel.tracking_group)
    button.click()
    panel.set_usage_level(STUDIO)                               # changer de niveau repart du niveau
    assert not panel.more_groups_shown() and not button.isChecked()
    panel.set_usage_level(ADVANCED)
    assert not _shown(panel, button)
    assert set(map(id, panel.findChildren(QWidget))) == widgets_before


def test_a_hidden_control_keeps_its_value_across_levels(qtbot):
    panel = _panel(qtbot, ADVANCED)
    panel.show_clip(_view(time_remapping=TimeRemapping()))
    panel.advanced_transform.spins["anchor_x"].setValue(25.0)
    panel.color_field_spins["exposure"].setValue(1.0)
    panel.compositing_group.spins["tolerance"].setValue(0.6)
    emitted = []
    panel.advanced_transform_changed.connect(lambda *a: emitted.append(a))
    panel.color_grade_field_changed.connect(lambda *a: emitted.append(a))
    for level in (SIMPLE, STUDIO, SIMPLE, ADVANCED):
        panel.set_usage_level(level)
        assert panel.advanced_transform.spins["anchor_x"].value() == 25.0
        assert panel.color_field_spins["exposure"].value() == 1.0
        assert panel.compositing_group.spins["tolerance"].value() == pytest.approx(0.6)
    assert emitted == []                                        # changer de niveau n'écrit rien


def test_the_link_and_the_folds_have_translated_accessible_names(qtbot):
    panel = _panel(qtbot, SIMPLE)
    for code in ("fr", "en", "es"):
        i18n.set_language(code)
        assert panel.more_options_button.text() == i18n.translate("usage.inspector.more")
        assert panel.more_options_button.accessibleName() == panel.more_options_button.text()
        for group, fold in panel._level_folds.items():
            assert fold.text() == i18n.translate("usage.inspector.fold")
            assert group.title() in fold.accessibleName()
    i18n.set_language("fr")
    assert i18n.translate("usage.inspector.more", ) == "Afficher plus d'options"


# ---------------------------------------------------------------- sous-blocs « Avancé »


def test_simple_and_studio_hide_the_higher_settings_of_a_visible_group_behind_a_fold(qtbot):
    panel = _panel(qtbot, SIMPLE)
    panel.show_clip(_view(time_remapping=TimeRemapping()))
    fold = panel._level_folds[panel.movement_group]
    assert _shown(panel, fold) and not fold.isChecked()
    diamonds = [*panel._diamonds.values(), panel.frame_align, panel.advanced_transform]
    assert not any(_shown(panel, w) for w in diamonds)
    assert all(_shown(panel, spin) for spin in panel._spin_boxes.values())     # position, échelle, rotation, opacité : Simple
    fold.click()
    assert all(_shown(panel, w) for w in diamonds)
    panel.set_usage_level(STUDIO)
    assert all(_shown(panel, w) for w in [*panel._diamonds.values(), panel.frame_align])     # Studio : directs
    assert fold.isChecked()                                     # le sous-bloc ouvert le reste (pas de recréation)


def test_studio_hides_the_advanced_settings_of_each_group(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.show_clip(_view(time_remapping=TimeRemapping()))
    assert _shown(panel, panel.speed_spinbox) and _shown(panel, panel.reverse_button) and _shown(panel, panel.freeze_frame_button)
    assert not _shown(panel, panel.time_section)
    assert _shown(panel, panel.color_field_spins["exposure"]) and not _shown(panel, panel.color_curve_editor)
    assert not any(_shown(panel, w) for w in (panel.color_curve_channel, panel.color_preset_combo, panel.color_lut_import_button))
    assert _shown(panel, panel.color_reset_button)
    assert _shown(panel, panel.effect_toggle_button) and not _shown(panel, panel.effect_up_button)
    assert not _shown(panel, panel.effect_down_button)
    assert _shown(panel, panel._audio_pan_row)
    panel.set_usage_level(SIMPLE)
    assert not _shown(panel, panel._audio_pan_row) and _shown(panel, panel._audio_gain_row)


def test_masks_and_blend_choices_follow_the_table(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.show_clip(_view())
    comp = panel.compositing_group
    blend_view = comp.blend_mode.view()
    assert isinstance(blend_view, QListView)
    hidden = {comp.blend_mode.itemData(i) for i in range(comp.blend_mode.count()) if blend_view.isRowHidden(i)}
    assert hidden == {BlendMode.ADD.value, BlendMode.DIFFERENCE.value}
    comp.blend_mode.setCurrentIndex(comp.blend_mode.findData(BlendMode.ADD.value))      # un mode déjà choisi reste listé
    assert not blend_view.isRowHidden(comp.blend_mode.findData(BlendMode.ADD.value))
    assert blend_view.isRowHidden(comp.blend_mode.findData(BlendMode.DIFFERENCE.value))
    shapes = {comp.mask_shape.itemData(i) for i in range(comp.mask_shape.count()) if comp.mask_shape.view().isRowHidden(i)}
    assert shapes == {"polygon", "path"}
    comp.add_mask.click()                                       # un masque sélectionné : son formulaire apparaît
    spins = comp.mask_spins
    form = comp.mask_form_host.layout()
    assert not _shown(panel, spins["expansion"]) and not _shown(panel, spins["rotation"]) and not _shown(panel, comp.edit_in_monitor)
    assert _shown(panel, spins["feather"]) and _shown(panel, comp.inverted)
    assert form.labelForField(spins["feather"]) is not None
    panel.set_usage_level(ADVANCED)
    assert not any(comp.blend_mode.view().isRowHidden(i) for i in range(comp.blend_mode.count()))
    assert _shown(panel, spins["expansion"]) and _shown(panel, comp.edit_in_monitor)


def test_audio_effect_types_are_listed_by_level_and_the_clip_effects_stay(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.update_audio_effects_from_clip([], "audio")
    combo = panel.audio_effect_type_combo
    view = combo.view()
    listed = {combo.itemData(i) for i in range(combo.count()) if not view.isRowHidden(i)}
    assert listed == {"normalize", "bass_boost", "treble_boost"}
    assert combo.count() == len(AudioEffectType)               # rien n'est retiré
    assert combo.itemData(combo.currentIndex()) in listed
    fold = panel._level_folds[panel.audio_effects_group]
    fold.click()
    assert {combo.itemData(i) for i in range(combo.count()) if not view.isRowHidden(i)} == {t.value for t in AudioEffectType}


def test_subtitle_and_graphic_blocks_follow_the_table(qtbot):
    panel = _panel(qtbot, SIMPLE)
    panel.show_clip(_view(track_type="subtitle", text="Salut", text_style=default_text_style()))
    editor = panel.subtitle_editor
    assert _shown(panel, editor.content_editor) and _shown(panel, editor.font_size_spin) and _shown(panel, editor.text_color_btn)
    assert not any(_shown(panel, w) for w in (editor.outline_color_btn, editor.shadow_color_btn, editor.background_color_btn,
                                              editor.outline_width_spin, editor.position_x_spin, editor.padding_x_spin))
    panel.set_usage_level(STUDIO)
    assert all(_shown(panel, w) for w in (editor.outline_color_btn, editor.shadow_color_btn, editor.background_color_btn,
                                          editor.outline_width_spin, editor.position_x_spin, editor.padding_x_spin))
    panel.show_clip(_view(track_type="graphics", graphic=GraphicOverlay()))
    graphics = panel.graphics_group
    assert _shown(panel, graphics.type_label) and _shown(panel, graphics.text_section)
    assert not _shown(panel, graphics.glow_section) and not _shown(panel, graphics.layer_section)
    panel.set_usage_level(SIMPLE)
    assert not _shown(panel, graphics.stroke_section) and not _shown(panel, graphics.animation_section)


def test_the_keyframe_menu_keeps_the_advanced_entries_for_the_advanced_level(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.show_clip(_view())
    assert len(panel.animation_menu("opacity").actions()) == 1          # activer / désactiver l'animation
    panel.set_usage_level(ADVANCED)
    texts = [a.text() for a in panel.animation_menu("opacity").actions() if not a.isSeparator()]
    assert len(texts) == 5                                              # + interpolation, copier, coller, courbes


def test_each_fold_sits_before_the_first_setting_it_reveals(qtbot):
    """Ouvrir le sous-bloc déplie en dessous du bouton : il précède, dans son groupe, le premier conteneur de niveau."""
    from PySide6.QtWidgets import QFormLayout

    panel = _panel(qtbot, SIMPLE)
    assert panel._level_folds
    for group, fold in panel._level_folds.items():
        layout = group.layout()
        items = [layout.itemAt(i).widget() for i in range(layout.count())]
        anchors = panel._level_anchors(group)
        assert anchors, group.title()
        first = next(i for i, w in enumerate(items) if w is not None and (w in anchors or any(w.isAncestorOf(a) for a in anchors)))
        assert fold in items, group.title()
        if isinstance(layout, QFormLayout):         # l'index d'un QFormLayout est l'ordre d'insertion, pas l'ordre des lignes
            assert layout.getWidgetPosition(fold)[0] < layout.getItemPosition(first)[0], group.title()
        else:
            assert items.index(fold) < first, group.title()


# ---------------------------------------------------------------- « Remplir le cadre »


def test_fill_the_frame_is_a_simple_level_action_that_relays_the_advanced_check(qtbot):
    panel = _panel(qtbot, SIMPLE)
    panel.show_clip(_view())
    quick = panel.fill_frame_quick
    assert isinstance(quick, QCheckBox) and _shown(panel, quick) and not _shown(panel, panel.advanced_transform)
    emitted = []
    panel.advanced_transform_changed.connect(lambda *a: emitted.append(a))
    quick.click()
    assert emitted == [("c", "fill", True)] and panel.advanced_transform.checks["fill"].isChecked()
    panel.advanced_transform.set_values({"fill": False})
    assert not quick.isChecked()                                        # suit la valeur du modèle
    panel.show_clip(_view(track_type="graphics", graphic=GraphicOverlay()))
    assert not _shown(panel, quick)                                     # sans objet pour un calque graphique
    panel.show_clip(_view())
    panel._level_folds[panel.movement_group].click()
    assert _shown(panel, panel.advanced_transform) and not _shown(panel, quick)       # jamais deux fois la même case
    panel.set_usage_level(ADVANCED)
    assert not _shown(panel, quick)


# ---------------------------------------------------------------- verrous


@pytest.mark.parametrize("level", [SIMPLE, STUDIO, ADVANCED])
def test_a_locked_clip_stays_locked_at_every_level_and_the_level_never_enables(window, level):  # noqa: F811
    panel = window.properties_panel
    track, clip = _prepare(window, "track")
    _lock(window, "track", track, clip)
    window.set_usage_level(level)
    assert panel.usage_level is level
    assert not panel.movement_group.isEnabled() and not panel.speed_group.isEnabled() and not panel.effects_group.isEnabled()
    before = len(window.history)
    window.properties_panel.transform_changed.emit(clip.id, "position_x", 0.3)
    window._finalize_transform_session()
    assert clip.transform.position_x != pytest.approx(0.3) and len(window.history) == before
    assert not panel.fill_frame_quick.isEnabled()                       # le relais est dans un groupe désactivé
    for other in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(other)
        assert not panel.movement_group.isEnabled()
    _unlock(window, "track", track, clip)
    assert panel.movement_group.isEnabled() and panel.fill_frame_quick.isEnabled()


def test_the_window_drives_the_inspector_live(window):  # noqa: F811
    panel = window.properties_panel
    window.set_usage_level(SIMPLE)
    assert panel.usage_level is SIMPLE and not panel.color_group.isVisibleTo(panel)
    window.set_usage_level(ADVANCED)
    assert panel.usage_level is ADVANCED and panel.color_group.isVisibleTo(panel)


# ---------------------------------------------------------------- non-régression au niveau Avancé


def _visible_counts(panel) -> dict[str, dict[str, int]]:
    states = {
        "none": None, "video": _view(time_remapping=TimeRemapping()), "video_locked": _view(time_remapping=TimeRemapping(), locked=True),
        "audio": _view(track_type="audio", time_remapping=TimeRemapping()),
        "subtitle": _view(track_type="subtitle", text="Salut", text_style=default_text_style()),
        "graphics": _view(track_type="graphics", graphic=GraphicOverlay()),
    }
    result = {}
    for state, view in states.items():
        panel.show_clip(view)
        for tab in range(7):
            panel._select_inspector_tab(tab)
            QApplication.processEvents()
            counts: Counter[str] = Counter()
            for widget in panel.findChildren(QWidget):
                name = widget.objectName()
                if name in IGNORED or name.startswith("qt_") or type(widget).__name__ == "QScrollBar":
                    continue
                if widget.isVisibleTo(panel):
                    counts[f"{type(widget).__name__}|{name}"] += 1
            result[f"{state}/{tab}"] = dict(sorted(counts.items()))
    return result


def test_the_advanced_level_is_the_inspector_from_before_the_levels(qtbot):
    panel = _panel(qtbot, ADVANCED)
    assert _visible_counts(panel) == json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert not any(_shown(panel, fold) for fold in panel._level_folds.values())
    assert not _shown(panel, panel.more_options_button)
    assert all(not spec.gate.isHidden() or spec.gate.inverse_of for spec in panel._level_gate_specs)


def test_a_panel_without_a_window_is_the_advanced_level(qtbot):
    panel = PropertiesPanel(lambda *_args: None)
    qtbot.addWidget(panel)
    assert panel.usage_level is ADVANCED


def test_an_unknown_level_is_ignored(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.set_usage_level("expert")
    panel.set_usage_level(None)
    assert panel.usage_level is STUDIO
    panel.set_usage_level("simple")                              # la chaîne du signal ``usage_level_changed``
    assert panel.usage_level is SIMPLE


def test_hidden_combo_choices_never_change_the_stored_value(qtbot):
    panel = _panel(qtbot, STUDIO)
    panel.show_clip(_view())
    combo: QComboBox = panel.compositing_group.blend_mode
    combo.setCurrentIndex(combo.findData(BlendMode.DIFFERENCE.value))
    panel.set_usage_level(SIMPLE)
    panel.set_usage_level(STUDIO)
    assert combo.currentData() == BlendMode.DIFFERENCE.value
