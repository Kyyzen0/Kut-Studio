"""Format déclaratif : effets d'impact et Ken Burns (lot 5 de l'extension 0.8.0) : `apply_impact_zoom`, `apply_camera_shake`, `add_flash`,
`apply_impact_on_cuts`, `apply_ken_burns`.

Chaque verbe appelle la fonction du modèle que le menu « Vidéo sociale » appelle : le test de parité fait le même appel sur une copie et compare
les projets. Là où le script serait moins protégé que l'interface (images clés posées à la main, effet plus long que le clip, verrous, bornes du
transform), il est refusé et le projet reste strictement inchangé.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, replace

import pytest

from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip, update_graphic
from core.impact_fx import add_flash, apply_camera_shake, apply_impact_on_cuts, apply_impact_zoom
from core.ken_burns import apply_ken_burns
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting import auto_rules
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from core.timeline_operations import find_clip
from core.visual_effects import ClipTransform, TransformKeyframe
from ui import i18n

NEW_VERBS = ("apply_impact_zoom", "apply_camera_shake", "add_flash", "apply_impact_on_cuts", "apply_ken_burns")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 60.0, 320, 180, 30.0, "video", has_audio=True),
                      MediaAsset("s", "/x/son.wav", "son", 60.0, 0, 0, 0.0, "audio", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 5.0), Clip("b", "m", "V1", 5.0, 0.0, 3.0), Clip("c", "m", "V1", 8.0, 0.0, 2.0)]),
            Track("V2", "V2", "video", clips=[Clip("e", "m", "V2", 1.0, 0.0, 2.0)]),
            Track("V3", "V3", "video", locked=True, clips=[Clip("d", "m", "V3", 20.0, 0.0, 4.0)]),
            Track("A1", "A1", "audio", clips=[Clip("au", "s", "A1", 0.0, 0.0, 5.0)]),
            Track("S1", "S1", "subtitle", clips=[Clip("sub", "", "S1", 0.0, 0.0, 2.0, text="x")]),
            Track("G1", "G1", "graphics"),
        ],
    )
    add_graphic_clip(project, "text", timeline_start=0.0, duration=4.0, track=project.tracks[5], clip_id="t")
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _refused(project: Project, *ops: dict):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert _snapshot(session.project) == before
    return report


def _shape(project: Project):
    """Le projet sans les identifiants tirés au hasard par le modèle (clips, médias techniques, images clés d'animation)."""
    def clip(c):
        data = {k: v for k, v in asdict(c).items() if k not in ("id", "asset_id")}
        for field in ("animation", "transform_keyframes"):
            data[field] = [{k: v for k, v in kf.items() if k != "id"} for kf in data[field]]
        return data

    return ([(t.id, t.type, [clip(c) for c in t.clips]) for t in project.tracks],
            sorted((a.media_type, a.name, a.duration, a.width, a.height) for a in project.media_assets))


def _parity(op: dict, direct, factory=_project, shape=_snapshot) -> Project:
    scripted = factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert shape(session.project) == shape(reference)
    return session.project


def _keyframes(project: Project, clip_id: str, name: str):
    return [kf for kf in find_clip(project, clip_id).transform_keyframes if kf.property_name == name]


def test_the_verbs_are_in_the_table_and_in_the_schema():
    from core.scripting.schema import json_schema

    assert set(NEW_VERBS) <= set(VERBS)
    assert VERBS["add_flash"].creates == "clip" and {"id", "as"} <= set(VERBS["add_flash"].accepts)
    for name in set(NEW_VERBS) - {"add_flash"}:
        assert VERBS[name].creates is None and VERBS[name].doc and not VERBS[name].needs_context
    assert set(NEW_VERBS) <= {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}


# --- Zoom d'impact ---------------------------------------------------------------------------------------------------------


def test_impact_zoom_matches_the_model():
    _parity(_op("apply_impact_zoom", clip="a"), lambda p: apply_impact_zoom(find_clip(p, "a")))
    _parity(_op("apply_impact_zoom", clip="a", strength=0.3, start=1.0, length=0.5),
            lambda p: apply_impact_zoom(find_clip(p, "a"), strength=0.3, start=1.0, length=0.5))
    _parity(_op("apply_impact_zoom", clip="t"), lambda p: apply_impact_zoom(find_clip(p, "t")))                    # un calque graphique


def test_impact_zoom_on_a_scaled_clip_and_next_to_other_keyframes():
    def scaled() -> Project:
        project = _project()
        find_clip(project, "a").transform = ClipTransform(scale=1.5)
        find_clip(project, "a").transform_keyframes = [TransformKeyframe("position_x", 0.0, 0.0), TransformKeyframe("position_x", 4.0, 0.5),
                                                       TransformKeyframe("scale", 3.0, 2.0), TransformKeyframe("scale", 4.0, 1.0)]
        return project

    project = _parity(_op("apply_impact_zoom", clip="a", length=0.3), lambda p: apply_impact_zoom(find_clip(p, "a")), scaled)
    assert [(kf.time_seconds, kf.value) for kf in _keyframes(project, "a", "scale")][:2] == [(0.0, pytest.approx(1.5 * 1.08)), (0.3, 1.5)]
    assert len(_keyframes(project, "a", "scale")) == 4 and len(_keyframes(project, "a", "position_x")) == 2     # celles d'ailleurs restent


@pytest.mark.parametrize("operation", [
    _op("apply_impact_zoom", clip="a", length=5.5),                  # plus long que le clip : le modèle l'écourterait
    _op("apply_impact_zoom", clip="a", start=4.9),                   # la longueur par défaut (0,3 s) dépasse aussi
    _op("apply_impact_zoom", clip="a", start=5.0, length=0.01),
    _op("apply_impact_zoom", clip="a", start=9.0),
    _op("apply_impact_zoom", clip="d"),                              # piste verrouillée
    _op("apply_impact_zoom", clip="au"),                             # une piste audio n'a pas de transform
    _op("apply_impact_zoom", clip="sub"),
    _op("apply_impact_zoom", clip="inconnu"),
])
def test_impact_zoom_refusals_leave_nothing(operation):
    _refused(_project(), operation)


def test_impact_zoom_refuses_a_value_beyond_the_models_bounds_instead_of_clamping():
    def big() -> Project:
        project = _project()
        find_clip(project, "a").transform = ClipTransform(scale=2.0)
        return project

    report = _refused(big(), _op("apply_impact_zoom", clip="a", strength=9.0))                  # 2 × 10 = 20 > 10
    assert "scale" in report.error or "borne" in report.error.lower() or "10" in report.error
    session, report = _apply(big(), _op("apply_impact_zoom", clip="a", strength=4.0))           # 2 × 5 = 10 : à la borne
    assert report.ok


def test_impact_zoom_never_replaces_hand_made_keyframes():
    def keyed(at: float) -> Project:
        project = _project()
        find_clip(project, "a").transform_keyframes = [TransformKeyframe("scale", at, 1.2), TransformKeyframe("scale", at + 1.0, 1.0)]
        return project

    report = _refused(keyed(0.1), _op("apply_impact_zoom", clip="a"))
    assert "images clés" in report.error
    session, report = _apply(keyed(2.0), _op("apply_impact_zoom", clip="a"))                    # hors de la plage 0–0,3 s : rien d'écrasé
    assert report.ok and len(_keyframes(session.project, "a", "scale")) == 4


def test_impact_zoom_refuses_locked_layers_and_light_layers():
    locked = _project()
    update_graphic(find_clip(locked, "t"), "locked", True)
    _refused(locked, _op("apply_impact_zoom", clip="t"))
    flashed = _project()
    flash = add_flash(flashed, 1.0)
    _refused(flashed, _op("apply_impact_zoom", clip=flash.id, length=0.1))                      # un calque de lumière : l'interface l'écarte aussi
    session, report = _apply(flashed, _op("apply_camera_shake", clip=flash.id, length=0.1))      # mais la secousse est permise (comme l'interface)
    assert report.ok


def test_huge_and_invalid_numbers_are_refused_at_parse_time():
    for fields in ({"strength": -0.1}, {"strength": 10}, {"strength": float("nan")}, {"length": 0}, {"length": 1e12}, {"start": -1}):
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [_op("apply_impact_zoom", clip="a", **fields)]})
    for fields in ({"amplitude": 2}, {"rotation": 361}, {"seed": -1}, {"seed": 2**32}, {"seed": 1.5}):
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [_op("apply_camera_shake", clip="a", **fields)]})
    for fields in ({"strength": 0.0}, {"strength": 0.6}):
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [_op("apply_ken_burns", clip="a", **fields)]})
    for fields in ({"at": -1}, {"at": 1, "length": 0}, {"at": 1, "color": "rouge"}, {"at": 1, "id": "a b"}):
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [_op("add_flash", **fields)]})


# --- Secousse --------------------------------------------------------------------------------------------------------------


def test_camera_shake_matches_the_model_with_a_fixed_or_derived_seed():
    _parity(_op("apply_camera_shake", clip="a", seed=7), lambda p: apply_camera_shake(find_clip(p, "a"), seed=7))
    _parity(_op("apply_camera_shake", clip="a"), lambda p: apply_camera_shake(find_clip(p, "a")))                 # graine dérivée de l'identifiant
    _parity(_op("apply_camera_shake", clip="b", start=0.5, length=1.0, amplitude=0.05, rotation=3.0, seed=0),
            lambda p: apply_camera_shake(find_clip(p, "b"), start=0.5, length=1.0, amplitude=0.05, rotation=3.0, seed=0))
    _parity(_op("apply_camera_shake", clip="t", seed=1), lambda p: apply_camera_shake(find_clip(p, "t"), seed=1))


@pytest.mark.parametrize("operation", [
    _op("apply_camera_shake", clip="c"),                               # 2 s : tient (0,45 s)
    _op("apply_camera_shake", clip="a", length=6.0),
    _op("apply_camera_shake", clip="a", start=4.8),
    _op("apply_camera_shake", clip="d"),
    _op("apply_camera_shake", clip="au"),
])
def test_camera_shake_refusals(operation):
    if operation["clip"] == "c":
        session, report = _apply(_project(), operation)
        assert report.ok
        return
    _refused(_project(), operation)


def test_camera_shake_duration_and_keyframe_count_are_bounded_before_sampling():
    import time

    huge = _project()
    find_clip(huge, "a").source_out = 1_000_000.0
    with pytest.raises(ScriptInvalid):                                                            # 45 millions d'images clés : refusé à la lecture
        parse_script({"kut_script": 1, "ops": [_op("apply_camera_shake", clip="a", length=1_000_000)]})
    start = time.perf_counter()
    report = _refused(huge, _op("apply_camera_shake", clip="a", length=auto_rules.MAX_SHAKE_SECONDS))   # 60 s : 2 700 images clés > plafond
    assert time.perf_counter() - start < 2.0 and "images clés" in report.error
    crowded = _project()
    find_clip(crowded, "a").transform_keyframes = [TransformKeyframe("opacity", float(n) / 100, 0.5) for n in range(auto_rules.MAX_KEYFRAMES_PER_CLIP - 5)]
    _refused(crowded, _op("apply_camera_shake", clip="a"))                                        # celles déjà présentes comptent
    assert auto_rules.shake_keyframe_count(0.45) == 3 * (7 + 1)
    session, report = _apply(_project(), _op("apply_camera_shake", clip="a", length=4.0, seed=1))
    assert report.ok and len(find_clip(session.project, "a").transform_keyframes) == auto_rules.shake_keyframe_count(4.0)


def test_camera_shake_never_replaces_position_or_rotation_keyframes_and_respects_bounds():
    for name in ("position_x", "position_y", "rotation"):
        project = _project()
        find_clip(project, "a").transform_keyframes = [TransformKeyframe(name, 0.2, 0.1)]
        _refused(project, _op("apply_camera_shake", clip="a"))
    project = _project()
    find_clip(project, "a").transform = ClipTransform(position_x=4.0, position_y=4.0)           # au bord : un tirage positif sort des bornes
    _refused(project, _op("apply_camera_shake", clip="a", amplitude=1.0, seed=1))                # refusé, pas ramené à 4


# --- Flash -----------------------------------------------------------------------------------------------------------------


def test_add_flash_matches_the_model_and_creates_the_cited_layer():
    project = _parity(_op("add_flash", at=2.0, id="flash1"), lambda p: add_flash(p, 2.0, clip_id="flash1"))
    flash = find_clip(project, "flash1")
    assert flash.label == "Flash" and flash.timeline_start == 2.0 and flash.graphic.light_kind == "flash"
    _parity(_op("add_flash", at=1.0, length=0.5, color="#FF0000AA", id="f2"), lambda p: add_flash(p, 1.0, length=0.5, color="#FF0000AA", clip_id="f2"))


def test_add_flash_without_id_keeps_the_generated_name_and_the_interface_is_unchanged():
    project = _project()
    flash = add_flash(project, 1.0)                                                             # l'interface : aucun paramètre neuf
    assert flash.id.startswith("graphic-") and flash.duration == pytest.approx(0.18)


def test_add_flash_is_cited_by_alias_then_edited_end_to_end():
    session, report = _apply(_project(), _op("add_flash", at=1.0, **{"as": "fl"}), _op("set_graphic", clip={"ref": "fl"}, width=100),
                             _op("move_clip", clip={"ref": "fl"}, at=2.0))
    assert report.ok and find_clip(session.project, "fl").timeline_start == 2.0
    script = parse_script({"kut_script": 1, "ops": [_op("add_flash", at=1.0, **{"as": "fl"})]})
    session, report = _apply(_project(), _op("add_flash", at=1.0, **{"as": "fl"}))
    assert created_ids(script, report) == [{"op": "0:add_flash", "id": "fl"}]
    assert session.undo() and not any(clip.id == "fl" for track in session.project.tracks for clip in track.clips)


def test_add_flash_refusals():
    taken = _project()
    _refused(taken, _op("add_flash", at=1.0, id="a"))                                           # un identifiant de clip déjà pris
    _refused(_project(), _op("add_flash", at=1.0, length=0.01))                                  # moins qu'une image (1/30 s) : le modèle l'allongerait
    assets = _project()
    assets.media_assets.append(MediaAsset("graphic-asset-zz", "", "x", 1.0, 1, 1, 30.0, "graphic"))
    _refused(assets, _op("add_flash", at=1.0, id="zz"))                                          # le média technique dérivé est pris


def test_add_flash_lands_on_a_free_graphics_track_never_a_locked_one():
    project = _project()
    project.tracks[5].locked = True
    session, report = _apply(project, _op("add_flash", at=1.0, id="f"))
    assert report.ok and next(t.id for t in session.project.tracks if any(c.id == "f" for c in t.clips)) != "G1"


def test_add_flash_counts_against_the_whole_project():
    crowded = _project()
    other = create_sequence(crowded, "autre")
    room = auto_rules.MAX_CLIPS_IN_PROJECT - auto_rules.total_clips(crowded)
    other.tracks.append(Track("X1", "X1", "video", clips=[Clip(f"x{n}", "m", "X1", float(n), 0.0, 0.5) for n in range(room)]))
    assert auto_rules.total_clips(crowded) == auto_rules.MAX_CLIPS_IN_PROJECT
    report = _refused(crowded, _op("add_flash", at=1.0))
    assert "limite" in report.error


# --- Impact sur chaque cut -------------------------------------------------------------------------------------------------


def test_impact_on_cuts_matches_the_model():
    _parity(_op("apply_impact_on_cuts", track="V1"), lambda p: apply_impact_on_cuts(p, "V1"))
    _parity(_op("apply_impact_on_cuts", track="V1", zoom=0.2, flash=True, shake=True), lambda p: apply_impact_on_cuts(p, "V1", zoom=0.2, flash=True, shake=True),
            shape=_shape)
    project = _parity(_op("apply_impact_on_cuts", track="V1", zoom=0.0, flash=True), lambda p: apply_impact_on_cuts(p, "V1", zoom=0.0, flash=True), shape=_shape)
    assert not _keyframes(project, "b", "scale") and sum(1 for c in project.tracks[-1].clips if c.label == "Flash") == 2


def test_impact_on_cuts_returns_the_number_of_cuts_and_is_one_step():
    session, report = _apply(_project(), _op("apply_impact_on_cuts", track="V1"))
    assert report.ok and report.values == (2,)                                                 # b et c arrivent bord à bord
    assert session.undo() and not _keyframes(session.project, "b", "scale")


@pytest.mark.parametrize("operation", [
    _op("apply_impact_on_cuts", track="V2"),                          # un seul clip : aucun cut
    _op("apply_impact_on_cuts", track="A1"),                          # pas une piste vidéo
    _op("apply_impact_on_cuts", track="G1"),
    _op("apply_impact_on_cuts", track="V3"),                          # verrouillée
    _op("apply_impact_on_cuts", track="inconnue"),
    _op("apply_impact_on_cuts", track="V1", zoom=0.0),                # rien à faire
])
def test_impact_on_cuts_refusals(operation):
    _refused(_project(), operation)


def test_impact_on_cuts_targets_a_video_track_whatever_the_cuts():
    audio = _project()
    audio.tracks[3].clips.append(Clip("au2", "s", "A1", 5.0, 0.0, 3.0))                          # deux clips bord à bord : un cut, mais sur une piste audio
    assert "piste" in _refused(audio, _op("apply_impact_on_cuts", track="A1")).error
    graphics = _project()
    add_graphic_clip(graphics, "text", timeline_start=4.0, duration=2.0, track=graphics.tracks[5], clip_id="t2")
    assert "piste" in _refused(graphics, _op("apply_impact_on_cuts", track="G1")).error


def test_impact_on_cuts_refuses_a_shake_that_would_overfill_a_clip():
    crowded = _project()
    find_clip(crowded, "c").transform_keyframes = [TransformKeyframe("opacity", float(n) / 100, 0.5) for n in range(auto_rules.MAX_KEYFRAMES_PER_CLIP - 5)]
    report = _refused(crowded, _op("apply_impact_on_cuts", track="V1", shake=True))
    assert "images clés" in report.error


def test_impact_on_cuts_bounds_the_number_of_incoming_clips():
    many = _project()
    many.tracks.append(Track("V9", "V9", "video", clips=[Clip(f"m{n}", "m", "V9", float(n), 0.0, 1.0) for n in range(auto_rules.MAX_NEW_CLIPS_PER_OPERATION + 2)]))
    report = _refused(many, _op("apply_impact_on_cuts", track="V9"))
    assert "limite" in report.error


def test_impact_on_cuts_refuses_a_locked_track_that_has_cuts():
    locked = _project()
    locked.tracks[2].clips.append(Clip("d2", "m", "V3", 24.0, 0.0, 2.0))                          # deux clips bord à bord sur V3, verrouillée
    report = _refused(locked, _op("apply_impact_on_cuts", track="V3"))
    assert "verrouill" in report.error


def test_impact_on_cuts_checks_every_incoming_clip_before_the_first_write():
    locked = _project()
    find_clip(locked, "c").graphic = GraphicOverlay(type=GraphicType.TEXT, text="x", locked=True)            # le second plan entrant : calque verrouillé
    report = _refused(locked, _op("apply_impact_on_cuts", track="V1", flash=True))
    assert "verrouill" in report.error
    short = _project()
    find_clip(short, "c").source_out = 0.2                                                     # 0,2 s : l'effet (0,3 s) ne tient pas
    report = _refused(short, _op("apply_impact_on_cuts", track="V1"))
    assert "ne tient pas" in report.error
    keyed = _project()
    find_clip(keyed, "c").transform_keyframes = [TransformKeyframe("scale", 0.0, 1.3)]
    _refused(keyed, _op("apply_impact_on_cuts", track="V1"))
    shaken = _project()
    find_clip(shaken, "c").transform_keyframes = [TransformKeyframe("position_y", 0.1, 0.2)]
    _refused(shaken, _op("apply_impact_on_cuts", track="V1", shake=True))
    session, report = _apply(shaken, _op("apply_impact_on_cuts", track="V1"))                  # sans secousse : la position n'est pas touchée
    assert report.ok


def test_impact_on_cuts_counts_the_flashes_against_the_whole_project():
    crowded = _project()
    other = create_sequence(crowded, "autre")
    spare = 1                                                                                   # une place seulement pour deux flashs
    room = auto_rules.MAX_CLIPS_IN_PROJECT - auto_rules.total_clips(crowded) - spare
    other.tracks.append(Track("X1", "X1", "video", clips=[Clip(f"x{n}", "m", "X1", float(n), 0.0, 0.5) for n in range(room)]))
    report = _refused(crowded, _op("apply_impact_on_cuts", track="V1", flash=True))
    assert "limite" in report.error
    session, report = _apply(crowded, _op("apply_impact_on_cuts", track="V1"))                  # sans flash : aucun clip créé
    assert report.ok


# --- Ken Burns -------------------------------------------------------------------------------------------------------------


def _image_layer_project() -> Project:
    project = _project()
    clip = find_clip(project, "t")
    clip.graphic = replace(clip.graphic, type=GraphicType.IMAGE, source_path="/x/photo.png")
    return project


def test_ken_burns_matches_the_model_on_a_video_clip_and_an_image_layer():
    _parity(_op("apply_ken_burns", clip="a"), lambda p: apply_ken_burns([find_clip(p, "a")]))
    _parity(_op("apply_ken_burns", clip="b", strength=0.3), lambda p: apply_ken_burns([find_clip(p, "b")], strength=0.3))
    _parity(_op("apply_ken_burns", clip="t"), lambda p: apply_ken_burns([find_clip(p, "t")]), _image_layer_project)


def test_ken_burns_keeps_the_clips_base_transform_and_other_keyframes():
    def tuned() -> Project:
        project = _project()
        clip = find_clip(project, "a")
        clip.transform = ClipTransform(scale=1.2, position_x=0.1)
        clip.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 2.0, 5.0)]
        return project

    project = _parity(_op("apply_ken_burns", clip="a"), lambda p: apply_ken_burns([find_clip(p, "a")]), tuned)
    assert len(_keyframes(project, "a", "rotation")) == 2


def test_ken_burns_refusals():
    assert "ni une vidéo" in _refused(_project(), _op("apply_ken_burns", clip="t")).error        # un texte n'est ni une vidéo ni une image
    empty = _project()
    find_clip(empty, "a").source_out = 0.0                                                        # durée nulle : le modèle n'anime rien
    assert "durée" in _refused(empty, _op("apply_ken_burns", clip="a")).error
    _refused(_project(), _op("apply_ken_burns", clip="d"))                                      # piste verrouillée
    _refused(_project(), _op("apply_ken_burns", clip="au"))
    _refused(_project(), _op("apply_ken_burns", clip="sub"))
    locked = _image_layer_project()
    update_graphic(find_clip(locked, "t"), "locked", True)
    _refused(locked, _op("apply_ken_burns", clip="t"))
    keyed = _project()
    find_clip(keyed, "a").transform_keyframes = [TransformKeyframe("position_x", 1.0, 0.3)]
    report = _refused(keyed, _op("apply_ken_burns", clip="a"))
    assert "images clés" in report.error


def test_ken_burns_refuses_a_move_that_leaves_the_models_bounds():
    big = _project()
    find_clip(big, "a").transform = ClipTransform(scale=9.5)
    _refused(big, _op("apply_ken_burns", clip="a"))                                              # 9,5 × 1,12 > 10 : pas ramené à 10
    edge = _project()
    find_clip(edge, "b").transform = ClipTransform(position_y=4.0)                               # « b » balaie vers le bas (pan_down) : 4 + 0,2 > 4
    _refused(edge, _op("apply_ken_burns", clip="b", strength=0.5))
