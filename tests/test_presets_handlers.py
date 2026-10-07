"""Presets d'effets, d'effets audio, de transitions et de texte depuis l'interface (``ui/main_window_mixins/presets.py``).

Chaque action part du signal de la bibliothèque (``project_panel.*``, branchés dans ``MainWindow``), comme un clic. On
vérifie l'effet sur le projet (et ce qu'annuler en fait), le refus présenté à l'utilisateur, et les fichiers de presets
écrits dans le dossier de configuration (isolé dans ``tmp_path``). Les dialogues d'enregistrement gardent leurs vrais
champs : seul ``exec()`` est remplacé, pour les remplir et les valider sans fenêtre modale. Quand un test a besoin d'un
preset utilisateur existant, il le crée par l'API du cœur (mise en place), puis l'action testée passe par le signal.
"""

from __future__ import annotations

import json

import pytest
from main_window_harness import build_window, install_dialogs
from PySide6.QtWidgets import QDialog

from core.audio_effects_library import AUDIO_EFFECT_PRESETS_FILE
from core.effects_library import USER_PRESETS_FILE, EffectCategory, snapshot_clip_preset
from core.text_presets import TEXT_PRESETS_FILE, builtin_text_presets
from core.timeline_operations import find_clip
from core.transition_presets import TRANSITION_PRESETS_FILE, make_user_transition_preset
from ui import i18n
from ui.project_panel_widgets.effects_library_view import SavePresetDialog
from ui.project_panel_widgets.transition_library import SaveTransitionPresetDialog

ACCEPTED_ON_INSTANCE = (
    "Bogue : presets.py compare le résultat du dialogue à « dialog.Accepted » (lignes 222 et 387). Avec le PySide6 "
    "installé (constaté avec 6.11 ; requirements.txt : >=6.6,<7), une énumération ne se lit plus sur une instance : "
    "AttributeError dès que le dialogue se ferme, et le preset n'est jamais enregistré. « QDialog.Accepted » (sur la "
    "classe) fonctionne."
)


@pytest.fixture
def dialogs(monkeypatch):
    return install_dialogs(monkeypatch)


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "config"


@pytest.fixture
def window(qtbot, monkeypatch, config_dir, dialogs):
    return build_window(qtbot, monkeypatch, config_dir)


@pytest.fixture
def xfail_if_enums_are_not_read_on_instances(qapp, request):
    """xfail strict **seulement** si le PySide6 installé refuse ``instance.Accepted`` (le bogue est alors présent).

    ``requirements.txt`` admet des versions où ``dialog.Accepted`` fonctionne encore : le test y doit passer, et un
    xfail strict inconditionnel l'y ferait échouer (XPASS). On constate le comportement au lieu de deviner la version.
    """
    probe = QDialog()
    broken = not hasattr(probe, "Accepted")
    probe.deleteLater()
    if broken:
        request.applymarker(pytest.mark.xfail(strict=True, reason=ACCEPTED_ON_INSTANCE))


def _accept_dialog(monkeypatch, dialog_class, fill) -> None:
    """Remplace ``exec`` : ``fill(dialog)`` lit et remplit les vrais champs, puis le dialogue est accepté."""

    def exec_(dialog):
        fill(dialog)
        return type(dialog).Accepted

    monkeypatch.setattr(dialog_class, "exec", exec_)


def _saved(config_dir, file_name: str) -> str:
    path = config_dir / file_name
    assert path.is_file(), f"{file_name} n'a pas été écrit"
    return path.read_text(encoding="utf-8")


def _status(window) -> str:
    return window.statusBar().currentMessage()


def _select(window, *clip_ids: str) -> None:
    """Sélection comme à la souris (un clic, puis Maj+clic) : la timeline annonce la sélection."""
    window.timeline_panel.select_clip(clip_ids[0])
    if len(clip_ids) > 1:
        window.timeline_panel._set_selection(list(clip_ids), clip_ids[-1], announce=True)


def _make_intro_and_plan_a_adjacent(window) -> None:
    """Le projet d'exemple laisse un trou entre intro (0–4 s) et plan_a (6,5 s) : on colle plan_a à intro."""
    find_clip(window.project, "plan_a").timeline_start = 4.0
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)


def _user_look(window, name: str = "Mon look"):
    """Preset utilisateur pris sur intro, après lui avoir appliqué « cinema » (comme le ferait l'enregistrement)."""
    _select(window, "intro")
    window.project_panel.effect_apply_requested.emit("cinema")
    preset = snapshot_clip_preset(find_clip(window.project, "intro"), name=name, description="",
                                  category=EffectCategory.LOOK)
    window.user_preset_store.add(preset)
    return preset


# --- presets d'effets vidéo ---------------------------------------------------------------------------------------


def test_applying_a_builtin_look_to_the_selected_clip_is_one_undo_step(window):
    _select(window, "intro")
    assert find_clip(window.project, "intro").effects == []

    window.project_panel.effect_apply_requested.emit("cinema")

    assert find_clip(window.project, "intro").effects, "le look pose ses effets sur le clip"
    assert find_clip(window.project, "plan_a").effects == []
    assert window.history.undo_label == i18n.translate("history.preset.effects_apply")
    window.undo_last()
    assert find_clip(window.project, "intro").effects == []


def test_an_unknown_effect_preset_is_reported_in_the_status_bar(window):
    _select(window, "intro")

    window.project_panel.effect_apply_requested.emit("does-not-exist")

    assert _status(window) == i18n.translate("status.preset.not_found")
    assert find_clip(window.project, "intro").effects == []
    assert not window.history.can_undo


def test_applying_a_look_without_a_video_clip_selected_does_nothing(window):
    window.project_panel.effect_apply_requested.emit("cinema")

    assert all(not clip.effects for track in window.project.tracks for clip in track.clips)
    assert not window.history.can_undo


def test_saving_a_clip_without_effects_explains_and_writes_nothing(window, dialogs, config_dir):
    _select(window, "intro")

    window.project_panel.effect_preset_save_requested.emit()

    assert dialogs.of_kind("information") == [(
        i18n.translate("effects.library.dialog.title"), i18n.translate("effects.library.no_effects_to_save"))]
    assert window.user_preset_store.all() == []
    assert not (config_dir / USER_PRESETS_FILE).exists()


@pytest.mark.usefixtures("xfail_if_enums_are_not_read_on_instances")
def test_saving_the_clip_look_from_the_dialog_writes_a_user_preset(window, monkeypatch, qtbot, config_dir):
    _select(window, "intro")
    window.project_panel.effect_apply_requested.emit("cinema")
    proposed: list[str] = []

    def fill(dialog):
        proposed.append(dialog.name_edit.text())
        dialog.name_edit.setText("  Mon look  ")

    _accept_dialog(monkeypatch, SavePresetDialog, fill)
    with qtbot.capture_exceptions() as exceptions:
        window.project_panel.effect_preset_save_requested.emit()

    assert not exceptions, [str(error) for _type, error, _tb in exceptions]
    assert proposed == [i18n.translate("history.layer.preset", name=find_clip(window.project, "intro").label)]
    [preset] = window.user_preset_store.all()
    assert preset.name == "Mon look"
    assert "Mon look" in _saved(config_dir, USER_PRESETS_FILE)


def test_a_user_look_applies_the_saved_effects_to_another_clip(window):
    preset = _user_look(window)
    effects = [(effect.type, effect.params) for effect in find_clip(window.project, "intro").effects]
    _select(window, "plan_a")

    window.project_panel.effect_apply_requested.emit(preset.id)

    assert [(effect.type, effect.params) for effect in find_clip(window.project, "plan_a").effects] == effects
    assert window.history.undo_label == i18n.translate("history.preset.effects_apply")


def test_deleting_a_user_look_asks_first_and_honours_no(window, dialogs, config_dir):
    preset = _user_look(window)
    assert "Mon look" in _saved(config_dir, USER_PRESETS_FILE)

    dialogs.answer = dialogs.answer.No
    window.project_panel.effect_preset_delete_requested.emit(preset.id)
    assert window.user_preset_store.get(preset.id) is not None
    assert len(dialogs.of_kind("question")) == 1 and "Mon look" in dialogs.of_kind("question")[0][1]

    dialogs.answer = dialogs.answer.Yes
    window.project_panel.effect_preset_delete_requested.emit(preset.id)
    assert window.user_preset_store.get(preset.id) is None
    assert "Mon look" not in _saved(config_dir, USER_PRESETS_FILE)


def _transition_preset(window):
    preset = make_user_transition_preset(name="Fondu maison", description="", transition_type="crossfade",
                                         default_duration=0.75)
    window.transition_preset_store.add_user_preset(preset)
    return preset


# Corrigé par le commit « Presets : enregistrer ou supprimer un preset n'entre plus dans l'historique » (lot 6) :
# ce test était un xfail strict qui documentait le bogue.
@pytest.mark.parametrize(("make", "signal"), [
    (_user_look, "effect_preset_delete_requested"),
    (_transition_preset, "transition_preset_delete_requested"),
])
def test_deleting_a_preset_neither_adds_an_undo_step_nor_drops_the_redo_stack(window, dialogs, make, signal):
    """Un preset vit dans le dossier de l'utilisateur, pas dans le projet : sa suppression ne s'annule pas par
    Ctrl+Z, n'ajoute pas d'entrée vide, et laisse « Rétablir » disponible."""
    preset = make(window)
    window.timeline_panel.rename_track_requested.emit("V2", "B-roll")
    window.undo_last()
    assert window.history.can_redo
    undo_label = window.history.undo_label

    getattr(window.project_panel, signal).emit(preset.id)

    store = window.user_preset_store if signal.startswith("effect") else window.transition_preset_store
    lookup = store.get if signal.startswith("effect") else store.get_preset
    assert lookup(preset.id) is None, "le preset est bien supprimé"
    assert window.history.undo_label == undo_label
    assert window.history.can_redo


# --- presets d'effets audio ---------------------------------------------------------------------------------------


def test_applying_an_audio_preset_adds_its_effect_to_the_selected_clip(window):
    _select(window, "intro")
    preset = window.audio_effect_preset_store.get_preset("normalize")

    window.project_panel.audio_effect_apply_requested.emit("normalize")

    effects = find_clip(window.project, "intro").audio_effects
    assert [effect.type for effect in effects] == [preset.effect_type]
    assert effects[0].params == preset.resolved_params()
    assert window.history.undo_label == i18n.translate("history.preset.audio_apply")
    window.undo_last()
    assert find_clip(window.project, "intro").audio_effects == []


def test_an_audio_preset_needs_a_clip_that_can_carry_sound(window):
    _select(window, "subtitle_01")

    window.project_panel.audio_effect_apply_requested.emit("normalize")

    assert all(not clip.audio_effects for track in window.project.tracks for clip in track.clips)
    assert not window.history.can_undo


def test_an_unknown_audio_preset_is_reported(window):
    _select(window, "intro")

    window.project_panel.audio_effect_apply_requested.emit("does-not-exist")

    assert _status(window) == i18n.translate("status.preset.audio_not_found")
    assert find_clip(window.project, "intro").audio_effects == []


def test_saving_an_audio_effect_as_a_preset_asks_its_name_and_writes_it(window, dialogs, config_dir):
    _select(window, "intro")
    window.project_panel.audio_effect_preset_save_requested.emit()
    assert [text for _title, text in dialogs.of_kind("information")] == [
        i18n.translate("dialog.preset.no_audio_effect")]

    window.project_panel.audio_effect_apply_requested.emit("compressor")
    dialogs.text_reply = ("  Voix radio  ", True)
    window.project_panel.audio_effect_preset_save_requested.emit()

    [preset] = window.audio_effect_preset_store.all_user_presets()
    assert preset.name == "Voix radio" and not preset.builtin
    assert preset.effect_type == find_clip(window.project, "intro").audio_effects[0].type
    assert "Voix radio" in _saved(config_dir, AUDIO_EFFECT_PRESETS_FILE)


def test_a_cancelled_audio_preset_name_saves_nothing(window, dialogs):
    _select(window, "intro")
    window.project_panel.audio_effect_apply_requested.emit("compressor")
    dialogs.text_reply = ("Voix radio", False)

    window.project_panel.audio_effect_preset_save_requested.emit()

    assert window.audio_effect_preset_store.all_user_presets() == []


def test_deleting_audio_presets_spares_builtins_and_asks_for_user_ones(window, dialogs, config_dir):
    _select(window, "intro")
    window.project_panel.audio_effect_apply_requested.emit("compressor")
    dialogs.text_reply = ("Voix radio", True)
    window.project_panel.audio_effect_preset_save_requested.emit()
    [preset] = window.audio_effect_preset_store.all_user_presets()

    window.project_panel.audio_effect_preset_delete_requested.emit("normalize")
    assert window.audio_effect_preset_store.get_preset("normalize") is not None
    assert dialogs.of_kind("question") == [], "un preset intégré n'est même pas proposé à la suppression"

    dialogs.answer = dialogs.answer.No
    window.project_panel.audio_effect_preset_delete_requested.emit(preset.id)
    assert len(dialogs.of_kind("question")) == 1
    assert window.audio_effect_preset_store.get_preset(preset.id) is not None, "« Non » garde le preset"

    dialogs.answer = dialogs.answer.Yes
    window.project_panel.audio_effect_preset_delete_requested.emit(preset.id)
    assert window.audio_effect_preset_store.get_preset(preset.id) is None
    assert "Voix radio" not in _saved(config_dir, AUDIO_EFFECT_PRESETS_FILE)


def test_an_audio_favorite_is_persisted(window, config_dir):
    window.project_panel.audio_effect_favorite_toggled.emit("limiter")

    assert "limiter" in window.audio_effect_preset_store.favorites()
    assert "limiter" in _saved(config_dir, AUDIO_EFFECT_PRESETS_FILE)


# --- presets de transitions ---------------------------------------------------------------------------------------


def test_a_transition_preset_goes_between_the_two_selected_clips_of_a_track(window):
    _make_intro_and_plan_a_adjacent(window)
    _select(window, "intro", "plan_a")

    window.project_panel.transition_apply_requested.emit("crossfade", 0.5)

    [transition] = window.project.transitions
    assert (transition.from_clip_id, transition.to_clip_id) == ("intro", "plan_a")
    assert transition.duration == pytest.approx(0.5)
    assert _status(window) == i18n.translate(
        "status.transition.added_named", name=i18n.translate("transitions.preset.crossfade.name"))
    assert window.history.undo_label == i18n.translate("history.transition.add")
    window.undo_last()
    assert window.project.transitions == []


def test_the_legacy_library_signal_adds_the_same_transition(window):
    _make_intro_and_plan_a_adjacent(window)
    _select(window, "intro", "plan_a")

    window.project_panel.add_transition_requested.emit("fade_black", 1.0)

    [transition] = window.project.transitions
    assert transition.type.value == window.transition_preset_store.get_preset("fade_black").transition_type.value
    assert transition.duration == pytest.approx(1.0)


def test_clips_that_do_not_touch_refuse_a_transition_and_say_why(window):
    _select(window, "intro", "plan_a")

    window.project_panel.transition_apply_requested.emit("crossfade", 0.5)

    assert window.project.transitions == []
    assert _status(window).startswith(i18n.translate("status.transition.refused", error="").rstrip())
    assert not window.history.can_undo


@pytest.mark.parametrize(("selection", "message_key"), [
    (("intro",), "transitions.library.two_clips_required"),
    (("intro", "b_roll"), "status.transition.same_track"),
])
def test_a_transition_needs_two_clips_of_the_same_track(window, selection, message_key):
    _make_intro_and_plan_a_adjacent(window)
    _select(window, *selection)

    window.project_panel.transition_apply_requested.emit("crossfade", 0.5)
    assert window.project.transitions == []
    assert _status(window) == i18n.translate(message_key)

    window.statusBar().clearMessage()
    window.project_panel.transition_preset_save_requested.emit()
    assert _status(window) == i18n.translate(message_key), "l'enregistrement applique la même règle"
    assert window.transition_preset_store.all_user_presets() == []
    assert not window.history.can_undo


def test_an_unknown_transition_preset_is_reported(window):
    _make_intro_and_plan_a_adjacent(window)
    _select(window, "intro", "plan_a")

    window.project_panel.transition_apply_requested.emit("does-not-exist", 0.5)

    assert window.project.transitions == []
    assert _status(window) == i18n.translate("transitions.library.no_results")


@pytest.mark.usefixtures("xfail_if_enums_are_not_read_on_instances")
def test_saving_a_transition_preset_prefills_from_the_clips_and_writes_it(window, monkeypatch, qtbot, config_dir):
    _make_intro_and_plan_a_adjacent(window)
    _select(window, "intro", "plan_a")
    window.project_panel.transition_apply_requested.emit("fade_black", 1.25)
    _select(window, "intro", "plan_a")          # poser la transition l'a sélectionnée : on reprend les deux clips
    proposed: list[tuple[str, float]] = []

    def fill(dialog):
        proposed.append((dialog.name_edit.text(), dialog.duration_spin.value()))
        dialog.name_edit.setText("Fondu maison")

    _accept_dialog(monkeypatch, SaveTransitionPresetDialog, fill)
    with qtbot.capture_exceptions() as exceptions:
        window.project_panel.transition_preset_save_requested.emit()

    assert not exceptions, [str(error) for _type, error, _tb in exceptions]
    intro, plan_a = (find_clip(window.project, clip_id).label or "Plan" for clip_id in ("intro", "plan_a"))
    assert proposed == [(f"{intro} → {plan_a}", pytest.approx(1.25))], "nom et durée repris des clips et de la transition"
    [preset] = window.transition_preset_store.all_user_presets()
    assert preset.name == "Fondu maison" and preset.default_duration == pytest.approx(1.25)
    assert "Fondu maison" in _saved(config_dir, TRANSITION_PRESETS_FILE)


def test_deleting_transition_presets_locks_builtins_and_confirms_user_ones(window, dialogs, config_dir):
    preset = _transition_preset(window)

    window.project_panel.transition_preset_delete_requested.emit("crossfade")
    assert window.transition_preset_store.get_preset("crossfade") is not None
    assert [text for _t, text in dialogs.of_kind("information")] == [
        i18n.translate("transitions.library.user_builtin_lock")]

    dialogs.answer = dialogs.answer.No
    window.project_panel.transition_preset_delete_requested.emit(preset.id)
    assert window.transition_preset_store.get_preset(preset.id) is not None

    dialogs.answer = dialogs.answer.Yes
    window.project_panel.transition_preset_delete_requested.emit(preset.id)
    assert window.transition_preset_store.get_preset(preset.id) is None
    assert "Fondu maison" not in _saved(config_dir, TRANSITION_PRESETS_FILE)


def test_a_transition_favorite_is_toggled_and_persisted(window, config_dir):
    window.project_panel.transition_favorite_toggled.emit("wipe_left")
    assert "wipe_left" in window.transition_preset_store.favorites()
    assert "wipe_left" in json.dumps(json.loads(_saved(config_dir, TRANSITION_PRESETS_FILE)))

    window.project_panel.transition_favorite_toggled.emit("wipe_left")
    assert "wipe_left" not in window.transition_preset_store.favorites()


# Corrigé par le commit « Transitions : basculer un favori le dit, au lieu d'annoncer une transition » (lot 6) :
# ce test était un xfail strict qui documentait le bogue.
def test_toggling_a_favorite_says_so_instead_of_claiming_a_transition_was_added(window):
    name = i18n.translate("transitions.preset.wipe_left.name")

    window.project_panel.transition_favorite_toggled.emit("wipe_left")
    assert window.project.transitions == []
    assert _status(window) == i18n.translate("status.transition.favorite_added", name=name)

    window.project_panel.transition_favorite_toggled.emit("wipe_left")
    assert _status(window) == i18n.translate("status.transition.favorite_removed", name=name)


def test_favorite_messages_name_builtins_in_the_interface_language_and_users_as_typed(window):
    """Revue de la PR #51 : en anglais, un intégré s'appelait encore « Balayage gauche » (nom français du cœur)."""
    previous = i18n.current_language()
    i18n.set_language("en")
    try:
        window.project_panel.transition_favorite_toggled.emit("wipe_left")
        assert _status(window) == "“Wipe left” added to favorites."
        user = _transition_preset(window)
        window.project_panel.transition_favorite_toggled.emit(user.id)
        assert _status(window) == "“Fondu maison” added to favorites."
    finally:
        i18n.set_language(previous)


# --- modèles de texte ---------------------------------------------------------------------------------------------


def test_a_text_template_restyles_the_selected_subtitle_and_undo_restores_it(window):
    _select(window, "subtitle_01")
    before = find_clip(window.project, "subtitle_01").text_style
    preset = next(p for p in builtin_text_presets() if p.id == "lower_third")

    window.project_panel.preset_apply_requested.emit("lower_third")

    assert find_clip(window.project, "subtitle_01").text_style == preset.style
    assert window.history.undo_label == i18n.translate("history.subtitle.apply_template")
    window.undo_last()
    assert find_clip(window.project, "subtitle_01").text_style == before


def test_without_a_selection_a_text_template_goes_to_the_first_visible_subtitle(window):
    preset = next(p for p in builtin_text_presets() if p.id == "title")

    window.project_panel.preset_apply_requested.emit("title")

    assert find_clip(window.project, "subtitle_01").text_style == preset.style


def test_a_text_template_without_any_visible_subtitle_explains_where_to_click(window):
    window.timeline_panel.toggle_track_visible_requested.emit("S1", False)
    steps = len(window.history._undo_stack)
    before = find_clip(window.project, "subtitle_01").text_style

    window.project_panel.preset_apply_requested.emit("lower_third")

    assert _status(window) == i18n.translate("effects.library.apply_hint")
    assert find_clip(window.project, "subtitle_01").text_style == before
    assert len(window.history._undo_stack) == steps


def test_a_text_template_can_create_a_styled_subtitle_at_the_playhead(window):
    preset = next(p for p in builtin_text_presets() if p.id == "title")
    subtitles_before = {clip.id for t in window.project.tracks if t.type == "subtitle" for clip in t.clips}

    window.project_panel.preset_new_clip_requested.emit("title")

    created = [clip for t in window.project.tracks if t.type == "subtitle" for clip in t.clips
               if clip.id not in subtitles_before]
    assert len(created) == 1
    assert created[0].text == (preset.default_text or preset.name)
    assert created[0].text_style == preset.style


def test_saving_and_deleting_a_user_text_template(window, dialogs, config_dir):
    style = find_clip(window.project, "subtitle_01").text_style
    dialogs.text_reply = ("Sous-titre jaune", True)

    window.project_panel.preset_save_requested.emit("Proposé", "desc", style, "Bonjour")

    [preset] = window.text_preset_store.all()
    assert preset.name == "Sous-titre jaune" and preset.default_text == "Bonjour"
    assert dialogs.inputs[-1][1] == "Proposé", "le dialogue propose le nom reçu"
    assert _status(window) == i18n.translate("status.template.saved", name="Sous-titre jaune")
    assert "Sous-titre jaune" in _saved(config_dir, TEXT_PRESETS_FILE)

    dialogs.answer = dialogs.answer.No
    window.project_panel.preset_delete_requested.emit(preset.id)
    assert window.text_preset_store.get(preset.id) is not None

    dialogs.answer = dialogs.answer.Yes
    window.project_panel.preset_delete_requested.emit(preset.id)
    assert window.text_preset_store.all() == []
    assert "Sous-titre jaune" not in _saved(config_dir, TEXT_PRESETS_FILE)


def test_a_builtin_text_template_cannot_be_deleted(window, dialogs):
    window.project_panel.preset_delete_requested.emit("standard_subtitle")

    assert any(p.id == "standard_subtitle" for p in window.text_preset_store.all_presets())
    assert dialogs.of_kind("question") == []


def test_a_cancelled_text_template_name_saves_nothing(window, dialogs, config_dir):
    dialogs.text_reply = ("Sous-titre jaune", False)

    window.project_panel.preset_save_requested.emit("Proposé", "", find_clip(window.project, "subtitle_01").text_style, "")

    assert window.text_preset_store.all() == []
    assert not (config_dir / TEXT_PRESETS_FILE).exists()
