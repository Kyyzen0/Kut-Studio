"""Format déclaratif : audio d'un clip (lot 2 de l'extension 0.8.0) : gain, panoramique, fondus, effets audio du clip, ducking.

Chaque verbe fait ce que l'inspecteur audio fait ; le test de parité fait le même appel (ou la même écriture de champ) directement.
Les valeurs hors bornes (gain, fondu trop long, paramètre d'effet) sont refusées, jamais ramenées ; les verrous valent pour toutes les entrées.
"""

from __future__ import annotations

import copy
import math

import pytest

from core.audio_automation import DUCKING_PRESETS, duck_music_under_voice
from core.audio_effects_model import (
    AUDIO_EFFECT_PARAMETER_SPECS, AudioEffectType, add_audio_effect_to_clip, move_clip_audio_effect, remove_audio_effect_from_clip,
    set_clip_audio_effect_enabled, update_clip_audio_effect_parameters,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from ui import i18n

CLIP_AUDIO_VERBS = (
    "set_clip_gain", "set_clip_pan", "set_clip_fade_in", "set_clip_fade_out", "reset_clip_fades", "add_clip_audio_effect",
    "remove_clip_audio_effect", "set_clip_audio_effect_enabled", "update_clip_audio_effect", "move_clip_audio_effect", "duck_music_under_voice",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked_a1: bool = False, locked_music: bool = False) -> Project:
    voice = Track("A2", "Voix", "audio", clips=[Clip("voix", "s", "A2", 0.0, 0.0, 4.0)])
    voice.audio_role = "voice"
    music = Track("A1", "Musique", "audio", locked=locked_a1 or locked_music, clips=[Clip("son", "s", "A1", 0.0, 0.0, 4.0)])
    music.audio_role = "music"
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True),
                      MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("V2", "V2", "video", locked=True, clips=[Clip("verrou", "m", "V2", 0.0, 0.0, 2.0)]),
            music, voice,
            Track("S1", "S1", "subtitle"),
            Track("G1", "G1", "graphics"),
        ],
    )


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return (copy.deepcopy(project.tracks), copy.deepcopy(project.ducking_sidechains))


def _compare(first: Project, second: Project) -> bool:
    """Les pistes et les associations de ducking, sans les identifiants générés (`id` d'une association)."""
    one, two = _snapshot(first), _snapshot(second)
    strip = lambda items: [(x.music_track_id, x.voice_track_id, x.config, x.enabled) for x in items]    # noqa: E731
    return one[0] == two[0] and strip(one[1]) == strip(two[1])


def _parity(project_factory, op: dict, direct) -> Project:
    scripted = project_factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = project_factory()
    direct(reference)
    assert _compare(session.project, reference)
    return session.project


def _refused(project: Project, *ops: dict):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    after = _snapshot(session.project)
    assert after[0] == before[0] and [(x.music_track_id, x.voice_track_id) for x in after[1]] == [(x.music_track_id, x.voice_track_id) for x in before[1]]
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(CLIP_AUDIO_VERBS) <= set(VERBS)
    assert VERBS["add_clip_audio_effect"].creates == "audio_effect" and VERBS["add_clip_audio_effect"].creates_scope == "clip"
    assert "id" in VERBS["add_clip_audio_effect"].accepts and "as" in VERBS["add_clip_audio_effect"].accepts
    for name in set(CLIP_AUDIO_VERBS) - {"add_clip_audio_effect"}:
        assert VERBS[name].creates is None and VERBS[name].doc and VERBS[name].group == "audio"
    assert set(CLIP_AUDIO_VERBS) <= {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}


# --- Gain, panoramique ---------------------------------------------------------------------------------------------------


def test_gain_and_pan_write_the_fields_like_the_inspector():
    def inspector(project: Project) -> None:
        find_clip(project, "son").gain_db = -6.5          # `on_clip_gain_changed` / `on_clip_pan_changed` : l'écriture directe du champ
        find_clip(project, "son").pan = 0.25

    project = _parity(_project, _op("set_clip_gain", clip="son", gain_db=-6.5), lambda p: setattr(find_clip(p, "son"), "gain_db", -6.5))
    assert find_clip(project, "son").gain_db == -6.5
    session, report = _apply(_project(), _op("set_clip_gain", clip="son", gain_db=-6.5), _op("set_clip_pan", clip="son", pan=0.25))
    assert report.ok, report.error
    reference = _project()
    inspector(reference)
    assert _compare(session.project, reference)
    assert session.undo() and find_clip(session.project, "son").gain_db == 0.0 and find_clip(session.project, "son").pan == 0.0
    for value in (-60.0, 12.0):
        assert _apply(_project(), _op("set_clip_gain", clip="son", gain_db=value))[1].ok
    for value in (-1.0, 1.0):
        assert _apply(_project(), _op("set_clip_pan", clip="son", pan=value))[1].ok


@pytest.mark.parametrize("verb,field,value", [
    ("set_clip_gain", "gain_db", 12.01), ("set_clip_gain", "gain_db", -60.01), ("set_clip_gain", "gain_db", math.nan),
    ("set_clip_gain", "gain_db", math.inf), ("set_clip_gain", "gain_db", True), ("set_clip_gain", "gain_db", "3"),
    ("set_clip_pan", "pan", 1.01), ("set_clip_pan", "pan", -1.01), ("set_clip_pan", "pan", math.nan), ("set_clip_pan", "pan", -math.inf),
])
def test_gain_and_pan_out_of_bounds_are_refused_at_reading(verb, field, value):
    _invalid(_op(verb, clip="son", **{field: value}))


# --- Fondus ------------------------------------------------------------------------------------------------------------


def test_fades_match_the_model():
    project = _parity(_project, _op("set_clip_fade_in", clip="son", seconds=1.5), lambda p: find_clip(p, "son").set_fade_in(1.5))
    assert find_clip(project, "son").fade_in == 1.5
    _parity(_project, _op("set_clip_fade_out", clip="son", seconds=0.75), lambda p: find_clip(p, "son").set_fade_out(0.75))
    _parity(_project, _op("set_clip_fade_in", clip="son", seconds=4.0), lambda p: find_clip(p, "son").set_fade_in(4.0))      # toute la durée

    def faded() -> Project:
        project = _project()
        find_clip(project, "son").set_fade_in(1.0)
        find_clip(project, "son").set_fade_out(2.0)
        return project

    project = _parity(faded, _op("reset_clip_fades", clip="son"), lambda p: find_clip(p, "son").reset_fades())
    assert (find_clip(project, "son").fade_in, find_clip(project, "son").fade_out) == (0.0, 0.0)
    project = _parity(faded, _op("set_clip_fade_in", clip="son", seconds=2.0), lambda p: find_clip(p, "son").set_fade_in(2.0))     # 2 + 2 = durée
    assert find_clip(project, "son").fade_out == 2.0                           # l'autre fondu n'est pas touché
    _parity(faded, _op("set_clip_fade_in", clip="son", seconds=0.0), lambda p: find_clip(p, "son").set_fade_in(0.0))


def test_a_fade_that_would_be_clamped_is_refused():
    def faded() -> Project:
        project = _project()
        find_clip(project, "son").set_fade_out(3.0)
        return project

    report = _refused(_project(), _op("set_clip_fade_in", clip="son", seconds=4.5))               # plus long que le clip (4 s)
    assert "tient pas" in str(report.error)
    report = _refused(faded(), _op("set_clip_fade_in", clip="son", seconds=1.5))                  # 1,5 + 3 > 4 : le modèle l'aurait ramené à 1
    assert "au plus 1 s" in str(report.error)
    _refused(faded(), _op("set_clip_fade_out", clip="son", seconds=5.0))
    _session, report = _apply(faded(), _op("set_clip_fade_in", clip="son", seconds=1.0))
    assert report.ok, report.error
    for bad in (-0.1, 1e9, math.nan, math.inf):
        _invalid(_op("set_clip_fade_in", clip="son", seconds=bad))
    # Le fondu tient compte de la durée DÉRIVÉE du clip (vitesse).
    fast = _project()
    from core.timeline_operations import set_clip_speed
    set_clip_speed(fast, "son", 2.0)
    _refused(fast, _op("set_clip_fade_out", clip="son", seconds=3.0))                              # le clip dure 2 s


# --- Effets audio du clip ----------------------------------------------------------------------------------------------


def test_add_clip_audio_effect_matches_the_model_and_creates_the_effect():
    project = _parity(_project, _op("add_clip_audio_effect", clip="son", type="compressor", id="comp"),
                      lambda p: add_audio_effect_to_clip(p, "son", AudioEffectType.COMPRESSOR, effect_id="comp"))
    assert [e.id for e in find_clip(project, "son").audio_effects] == ["comp"]
    _parity(_project, _op("add_clip_audio_effect", clip="son", type="bass_boost", id="bb", params={"gain_db": 6}, enabled=False),
            lambda p: add_audio_effect_to_clip(p, "son", "bass_boost", effect_id="bb", params={"gain_db": 6}, enabled=False))
    _parity(_project, _op("add_clip_audio_effect", clip="a", type="limiter", id="lim"),         # un clip d'une piste vidéo porte aussi des effets audio
            lambda p: add_audio_effect_to_clip(p, "a", "limiter", effect_id="lim"))
    script = parse_script({"kut_script": 1, "ops": [_op("add_clip_audio_effect", clip="son", type="compressor", id="comp")]})
    session = Session(_project())
    report = apply_script(session, script)
    assert created_ids(script, report) == [{"op": report.results[0].name, "id": "comp"}]


def test_add_clip_audio_effect_without_id_keeps_the_models_generated_name_and_defaults():
    session, report = _apply(_project(), _op("add_clip_audio_effect", clip="son", type="compressor"))
    assert report.ok, report.error
    effect = find_clip(session.project, "son").audio_effects[0]
    assert effect.id.startswith("afx-")
    assert effect.params == {spec.name: spec.default for spec in AUDIO_EFFECT_PARAMETER_SPECS[AudioEffectType.COMPRESSOR]}


def test_add_clip_audio_effect_errors():
    _invalid(_op("add_clip_audio_effect", clip="son", type="compressor", id="x"), _op("add_clip_audio_effect", clip="son", type="limiter", id="x"))   # doublon dans le script
    _refused(_with_effects(), _op("add_clip_audio_effect", clip="son", type="echo_light", id="comp"))                                              # doublon dans le clip
    for bad_id in ("angle-1 ; drawtext", "a b", "x" * 65, ""):
        _invalid(_op("add_clip_audio_effect", clip="son", type="compressor", id=bad_id))
    _invalid(_op("add_clip_audio_effect", clip="son", type="reverb_hall"))
    _refused(_project(), _op("add_clip_audio_effect", clip="son", type="compressor", params={"ratio": 1000}))             # hors bornes
    _refused(_project(), _op("add_clip_audio_effect", clip="son", type="compressor", params={"inconnu": 1}))
    _refused(_project(), _op("add_clip_audio_effect", clip="son", type="compressor", params={"ratio": "fort"}))
    _refused(_project(), _op("add_clip_audio_effect", clip="son", type="compressor", params={"ratio": True}))
    _invalid(_op("add_clip_audio_effect", clip="son", type="compressor", params={"ratio": math.nan}))
    _invalid(_op("add_clip_audio_effect", clip="son", type="compressor", params={"ratio": math.inf}))


def _with_effects() -> Project:
    project = _project()
    add_audio_effect_to_clip(project, "son", "compressor", effect_id="comp")
    add_audio_effect_to_clip(project, "son", "limiter", effect_id="lim")
    add_audio_effect_to_clip(project, "son", "bass_boost", effect_id="bb")
    return project


def test_remove_enable_update_move_match_the_model():
    project = _parity(_with_effects, _op("remove_clip_audio_effect", clip="son", effect="lim"), lambda p: remove_audio_effect_from_clip(p, "son", "lim"))
    assert [e.id for e in find_clip(project, "son").audio_effects] == ["comp", "bb"]
    _parity(_with_effects, _op("set_clip_audio_effect_enabled", clip="son", effect="comp", enabled=False),
            lambda p: set_clip_audio_effect_enabled(p, "son", "comp", False))
    _parity(_with_effects, _op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": 6}),
            lambda p: update_clip_audio_effect_parameters(p, "son", "comp", {"ratio": 6}))
    project = _parity(_with_effects, _op("move_clip_audio_effect", clip="son", effect="bb", delta=-2), lambda p: move_clip_audio_effect(p, "son", "bb", -2))
    assert [e.id for e in find_clip(project, "son").audio_effects] == ["bb", "comp", "lim"]
    _parity(_with_effects, _op("move_clip_audio_effect", clip="son", effect="comp", delta=1), lambda p: move_clip_audio_effect(p, "son", "comp", 1))


def test_effect_parameters_are_refused_not_clamped():
    spec = next(item for item in AUDIO_EFFECT_PARAMETER_SPECS[AudioEffectType.COMPRESSOR] if item.name == "ratio")
    for value in (spec.minimum - 0.5, spec.maximum + 0.5):
        _refused(_with_effects(), _op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": value}))
    for value in (spec.minimum, spec.maximum):
        _session, report = _apply(_with_effects(), _op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": value}))
        assert report.ok, report.error
    _refused(_with_effects(), _op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": 6}, ), _op("update_clip_audio_effect", clip="son", effect="comp", params={"nom": 1}))
    _refused(_with_effects(), _op("update_clip_audio_effect", clip="son", effect="comp", params={}))
    _invalid(_op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": math.nan}))
    _invalid(_op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": [1]}))
    _invalid(_op("update_clip_audio_effect", clip="son", effect="comp", params={"x" * 100: 1} | {f"p{i}": 1 for i in range(40)}))


def test_every_audio_effect_type_accepts_its_defaults_and_refuses_each_bound():
    for effect in AudioEffectType:
        _session, report = _apply(_project(), _op("add_clip_audio_effect", clip="son", type=effect.value))
        assert report.ok, (effect, report.error)
        for spec in AUDIO_EFFECT_PARAMETER_SPECS[effect]:
            for bad in (spec.minimum - 1.0, spec.maximum + 1.0):
                _refused(_project(), _op("add_clip_audio_effect", clip="son", type=effect.value, params={spec.name: bad}))


def test_effect_chain_errors():
    _refused(_with_effects(), _op("remove_clip_audio_effect", clip="son", effect="nope"))
    _refused(_with_effects(), _op("set_clip_audio_effect_enabled", clip="son", effect="nope", enabled=True))
    _refused(_with_effects(), _op("move_clip_audio_effect", clip="son", effect="comp", delta=-1))          # déjà en tête : le modèle ne bougerait pas
    _refused(_with_effects(), _op("move_clip_audio_effect", clip="son", effect="bb", delta=1))
    _refused(_with_effects(), _op("move_clip_audio_effect", clip="son", effect="comp", delta=5))           # le modèle l'aurait ramené en fin de chaîne
    _refused(_with_effects(), _op("move_clip_audio_effect", clip="son", effect="comp", delta=0))
    _invalid(_op("move_clip_audio_effect", clip="son", effect="comp", delta=10_000))
    _invalid(_op("move_clip_audio_effect", clip="son", effect="comp", delta=1.5))


def test_effects_can_be_cited_by_alias_end_to_end():
    session, report = _apply(
        _project(),
        _op("add_clip_audio_effect", clip="son", type="compressor", **{"as": "comp"}),
        _op("update_clip_audio_effect", clip="son", effect={"ref": "comp"}, params={"ratio": 6}),
        _op("set_clip_audio_effect_enabled", clip="son", effect={"ref": "comp"}, enabled=False),
        _op("set_clip_gain", clip="son", gain_db=-3.0),
        _op("set_clip_fade_out", clip="son", seconds=1.0),
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "son")
    assert clip.audio_effects[0].id == "comp" and not clip.audio_effects[0].enabled and clip.gain_db == -3.0 and clip.fade_out == 1.0
    assert session.undo() and find_clip(session.project, "son").audio_effects == []
    # Un alias de clip n'est pas un alias d'effet audio.
    _invalid(_op("cut_clip", clip="a", at=1.0, **{"as": "droite"}), _op("remove_clip_audio_effect", clip="son", effect={"ref": "droite"}))


def test_scoped_ids_two_clips_can_each_have_their_own_effect_id():
    session, report = _apply(
        _project(), _op("add_clip_audio_effect", clip="son", type="compressor", id="fx1"),
        _op("add_clip_audio_effect", clip="voix", type="compressor", id="fx1"),
    )
    assert report.ok, report.error


# --- Verrous, genres de piste -------------------------------------------------------------------------------------------

_AUDIO_SAMPLES = [
    _op("set_clip_gain", clip="CLIP", gain_db=-3.0), _op("set_clip_pan", clip="CLIP", pan=0.5), _op("set_clip_fade_in", clip="CLIP", seconds=0.5),
    _op("set_clip_fade_out", clip="CLIP", seconds=0.5), _op("reset_clip_fades", clip="CLIP"),
    _op("add_clip_audio_effect", clip="CLIP", type="compressor"),
]


def _aimed(operation: dict, clip: str) -> dict:
    return {key: (clip if value == "CLIP" else value) for key, value in operation.items()}


@pytest.mark.parametrize("operation", _AUDIO_SAMPLES, ids=lambda item: item["op"])
def test_a_locked_track_refuses_every_clip_audio_verb(operation):
    assert "verrouill" in str(_refused(_project(locked_a1=True), _aimed(operation, "son")).error)


def test_a_locked_track_refuses_the_effect_chain_verbs():
    for operation in (
        _op("remove_clip_audio_effect", clip="son", effect="comp"), _op("set_clip_audio_effect_enabled", clip="son", effect="comp", enabled=False),
        _op("update_clip_audio_effect", clip="son", effect="comp", params={"ratio": 6}), _op("move_clip_audio_effect", clip="son", effect="comp", delta=1),
    ):
        project = _with_effects()
        project.tracks[2].locked = True
        assert "verrouill" in str(_refused(project, operation).error)


def test_gain_pan_and_fades_only_exist_on_audio_tracks_effects_also_on_video():
    for operation in _AUDIO_SAMPLES[:5]:
        _refused(_project(), _aimed(operation, "a"))                              # piste vidéo : pas de groupe audio dans l'inspecteur
    assert _apply(_project(), _aimed(_AUDIO_SAMPLES[5], "a"))[1].ok               # mais son rack d'effets audio, si
    project = _project()
    session, report = _apply(project, _op("add_subtitle", text="x", at=0, duration=1, track="S1", id="sub"),
                             _op("add_graphic", graphic="text", at=0, duration=2, id="t1", text="A", track="G1"))
    assert report.ok, report.error
    for operation in _AUDIO_SAMPLES:
        _refused(session.project, _aimed(operation, "sub"))
        _refused(session.project, _aimed(operation, "t1"))                           # un calque graphique n'a pas d'audio : le modèle, lui, l'accepterait
    assert "verrouill" in str(_refused(_project(), _aimed(_AUDIO_SAMPLES[5], "verrou")).error)


# --- Ducking -----------------------------------------------------------------------------------------------------------


def test_duck_music_under_voice_matches_the_model_for_every_preset():
    _parity(_project, _op("duck_music_under_voice"), lambda p: duck_music_under_voice(p))                          # le défaut de l'interface
    for preset in DUCKING_PRESETS:
        project = _parity(_project, _op("duck_music_under_voice", preset=preset), lambda p, preset=preset: duck_music_under_voice(p, preset))
        assert len(project.ducking_sidechains) == 1 and project.ducking_sidechains[0].config == DUCKING_PRESETS[preset]


def test_duck_music_under_voice_replaces_an_existing_association_and_is_undoable():
    session, report = _apply(_project(), _op("duck_music_under_voice", preset="gentle"), _op("duck_music_under_voice", preset="social_punchy"))
    assert report.ok, report.error
    assert len(session.project.ducking_sidechains) == 1 and session.project.ducking_sidechains[0].config == DUCKING_PRESETS["social_punchy"]
    assert session.undo() and session.project.ducking_sidechains == []


def test_duck_music_under_voice_errors():
    project = _project()
    project.tracks[2].audio_role = "other"
    assert "musique" in str(_refused(project, _op("duck_music_under_voice")).error)                 # pas de piste « musique »
    _invalid(_op("duck_music_under_voice", preset="enorme"))
    report = _refused(_project(locked_music=True), _op("duck_music_under_voice"))                      # la piste musique est verrouillée
    assert "verrouill" in str(report.error)
    voice_locked = _project()
    voice_locked.tracks[3].locked = True
    assert _apply(voice_locked, _op("duck_music_under_voice"))[1].ok                                   # la voix n'est pas modifiée : permis


def test_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("set_clip_gain", clip="x" * 5_000_000, gain_db=0)]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("add_clip_audio_effect", clip="son", type="compressor", params={f"p{i}": 1 for i in range(100)}))
    _invalid(_op("set_clip_gain", clip="son", gain_db=0, extra=1))
