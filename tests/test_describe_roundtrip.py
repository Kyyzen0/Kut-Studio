"""Aller-retour ``describe`` -> script -> ``describe`` (ADR-0005 étape 5, partie B) : la description contient les identifiants et les
valeurs que les verbes attendent.

Deux preuves, pour chaque famille décrite (effets, effets audio, inserts, bus, images clés, masques, étalonnage) :

* **reconstruction** : on retire la famille d'une copie du projet riche, on écrit un script **à partir de la description seule** (ses
  identifiants et ses valeurs), on l'applique, et la nouvelle description de la famille est celle d'origine ;
* **rejeu** : un script de « réglages » qui vise chaque objet décrit par son identifiant, avec ses valeurs, réussit sur le projet
  lui-même et ne change rien (la description reste identique).

Ce qu'aucun verbe ne sait encore écrire (images clés d'effet ``fx.*``, LUT, composition, mode de fusion, incrustation, réglage de ducking
``Track.ducking_config``, courbe de volume d'une piste vidéo) est listé dans ``GAPS`` et vérifié : le jour où un verbe existe, le test le dit.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from describe_project import rich_project

from core.audio_automation import TrackAutomation
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.describe import describe
from core.scripting.verbs import VERBS

GAPS = ("fx.* keyframes", "LUT", "composition", "blend_mode", "chroma_key", "ducking_config", "video track automation")
_IDENTITY_X = [index / 15 for index in range(16)]


def _clip(data: dict, clip_id: str) -> dict:
    return next(c for t in data["sequences"][0]["tracks"] for c in t["clips"] if c["id"] == clip_id)


def _track(data: dict, track_id: str) -> dict:
    return next(t for t in data["sequences"][0]["tracks"] if t["id"] == track_id)


def _apply(project, ops: list[dict]):
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report
    return report


def _clip_of(project, clip_id: str):
    return next(c for t in project.tracks for c in t.clips if c.id == clip_id)


# --- Reconstruction : le script est écrit depuis la description ----------------------------------------------------------------


def _strip(project):
    """Une copie du projet riche sans les familles décrites par la partie B (ce que les verbes savent récrire)."""
    copy = deepcopy(project)
    for clip in (c for t in copy.tracks for c in t.clips):
        clip.effects, clip.audio_effects, clip.transform_keyframes, clip.animation = [], [], [], []
        clip.compositing = replace(clip.compositing, masks=())
        if isinstance(clip.color_grade, ColorNodeGraph):
            clip.color_grade = replace(clip.color_grade, nodes=tuple(
                replace(node, grade=ColorGrade(lut=node.grade.lut), label="", qualifier=None, windows=(), blur=0.0, sharpen=0.0)
                if isinstance(node, ColorNode) else node for node in clip.color_grade.nodes))
    for track in copy.tracks:
        track.inserts, track.bus_id = [], ""
        if track.type == "audio":                                            # la courbe d'une piste vidéo n'a pas de verbe (GAPS)
            track.audio_role, track.automation = "other", TrackAutomation()
    copy.active_sequence.buses = []
    copy.active_sequence.ducking_sidechains = []
    return copy


_GRADE_NUMBERS = ("exposure", "contrast", "saturation", "temperature", "hue", "shadows", "highlights")
_MASK_NUMBERS = ("position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity")
_QUALIFIER = ("enabled", "use_hue", "hue_center", "hue_width", "hue_soft", "use_sat", "sat_low", "sat_high", "sat_soft", "use_lum",
              "lum_low", "lum_high", "lum_soft", "invert")


def _mask_op(clip_id: str, mask: dict, op: str = "add_mask", **extra: Any) -> dict:
    fields = {name: mask[name] for name in (*_MASK_NUMBERS, "inverted", "mode", "name")}
    shape = {"shape": mask["shape"], "id": mask["id"]}
    if mask["shape"] == "polygon":
        shape["points"] = mask["points"]
    if mask["shape"] == "path":
        shape["nodes"] = mask["nodes"]
    return {"op": op, "clip": clip_id, **shape, **fields, **extra}


def _effect_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    for effect in _clip(data, "c1")["effects"]:
        ops.append({"op": "add_effect", "clip": "c1", "type": effect["type"], "id": effect["id"], "params": effect["params"],
                    "enabled": effect["enabled"]})
    for effect in _clip(data, "c4")["audio_effects"]:
        ops.append({"op": "add_clip_audio_effect", "clip": "c4", "type": effect["type"], "id": effect["id"], "params": effect["params"],
                    "enabled": effect["enabled"]})
    for insert in _track(data, "A1")["inserts"]:
        ops.append({"op": "add_track_insert", "track": "A1", "effect": insert["type"], "id": insert["id"], "params": insert["params"],
                    "enabled": insert["enabled"]})
    return ops


def _bus_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    for bus in data["sequences"][0]["buses"]:
        ops += [{"op": "add_bus", "id": bus["id"], "name": bus["name"]},
                {"op": "set_bus_volume", "bus": bus["id"], "volume_db": bus["volume_db"]},
                {"op": "set_bus_muted", "bus": bus["id"], "muted": bus["muted"]}]
        ops += [{"op": "add_bus_insert", "bus": bus["id"], "effect": i["type"], "id": i["id"], "params": i["params"], "enabled": i["enabled"]}
                for i in bus["inserts"]]
    ops += [{"op": "route_track", "track": t["id"], "bus": t["bus_id"]} for t in data["sequences"][0]["tracks"] if t["bus_id"]]
    return ops


def _audio_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    audio = [t for t in data["sequences"][0]["tracks"] if t["type"] == "audio"]
    for track in audio:
        if track["audio_role"] != "other":
            ops.append({"op": "set_track_role", "track": track["id"], "role": track["audio_role"]})
        if track["automation"]["points"]:
            ops.append({"op": "set_automation_curve", "track": track["id"], "points": track["automation"]["points"]})
    for sidechain in data["sequences"][0]["ducking_sidechains"]:
        ops.append({"op": "add_ducking_sidechain", "music": sidechain["music_track_id"], "voice": sidechain["voice_track_id"],
                    "id": sidechain["id"], **sidechain["config"]})
        if not sidechain["enabled"]:
            ops.append({"op": "set_ducking_enabled", "ducking": sidechain["id"], "enabled": False})
    return ops


def _mask_ops(data: dict) -> list[dict]:
    return [_mask_op("c1", mask) for mask in _clip(data, "c1")["compositing"]["masks"]]


def _keyframe_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    clip = _clip(data, "c1")
    for keyframe in clip["transform_keyframes"] + clip["animation"]:
        name = keyframe["property_name"]
        common = {"clip": "c1", "property": name, "at": keyframe["time_seconds"], "value": keyframe["value"],
                  "interpolation": keyframe["interpolation"]}
        if name.startswith("fx."):
            continue                                                   # GAPS : aucun verbe n'écrit une image clé d'effet
        if name.startswith("mask."):
            _, mask, prop = name.split(".")
            ops.append({"op": "add_mask_keyframe", **common, "mask": mask, "property": prop})
        else:
            ops.append({"op": "add_keyframe", **common})
    return ops


def _color_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    for node in _clip(data, "c1")["color_grade"]["nodes"]:
        if node["type"] != "corrector":
            continue
        base = {"clip": "c1", "node": node["id"]}
        grade = node["grade"]
        ops.append({"op": "set_color_grade", **base, **{name: grade[name] for name in _GRADE_NUMBERS}, "enabled": grade["enabled"]})
        for channel, points in grade["curves"].items():
            ops += [{"op": "set_color_curve", **base, "channel": channel, "index": index, "output": y}
                    for index, (x, y) in enumerate(points) if abs(x - y) > 1e-5]
        ops += [{"op": "set_color_wheel", **base, "wheel": wheel, **grade[wheel]} for wheel in ("lift", "gamma", "gain", "offset")]
        if node["label"]:
            ops.append({"op": "rename_color_node", **base, "label": node["label"]})
        if node["qualifier"]:
            ops.append({"op": "set_color_qualifier", **base, **{name: node["qualifier"][name] for name in _QUALIFIER}})
        if node["blur"] or node["sharpen"]:
            ops.append({"op": "set_color_detail", **base, "blur": node["blur"], "sharpen": node["sharpen"]})
        for window in node["windows"]:
            ops.append(_mask_op("c1", window, "add_color_window", node=node["id"]))   # le nom vide de la description est rejoué tel quel
    return ops


FAMILIES = {
    "effects": _effect_ops, "buses": _bus_ops, "audio": _audio_ops, "masks": _mask_ops, "keyframes": _keyframe_ops, "color": _color_ops,
}


def _family_view(data: dict) -> dict:
    """Ce que le script reconstruit, tel que décrit : les identifiants d'image clé sont tirés au hasard, les tangentes par défaut."""
    clip = _clip(data, "c1")
    keyframes = [(k["property_name"], k["time_seconds"], k["value"], k["interpolation"])
                 for k in clip["transform_keyframes"] + clip["animation"] if not k["property_name"].startswith("fx.")]
    masks = [{key: value for key, value in mask.items() if key != "keyframes"} for mask in clip["compositing"]["masks"]]
    return {
        "effects": clip["effects"], "audio_effects": _clip(data, "c4")["audio_effects"], "inserts": _track(data, "A1")["inserts"],
        "bus_id": _track(data, "A1")["bus_id"], "buses": data["sequences"][0]["buses"], "masks": masks,
        "keyframes": sorted(keyframes), "color_grade": clip["color_grade"],
        "roles": {t["id"]: t["audio_role"] for t in data["sequences"][0]["tracks"]},
        "automation": {t["id"]: t["automation"] for t in data["sequences"][0]["tracks"] if t["type"] == "audio"},
        "ducking_sidechains": data["sequences"][0]["ducking_sidechains"],
    }


def test_a_script_written_from_the_description_rebuilds_every_family():
    original = rich_project()
    wanted = describe(original)
    rebuilt = _strip(original)
    assert _family_view(describe(rebuilt)) != _family_view(wanted)           # le retrait a bien eu lieu
    ops = [op for family in ("effects", "buses", "audio", "masks", "keyframes", "color") for op in FAMILIES[family](wanted)]
    assert {op["op"] for op in ops} >= {"add_effect", "add_clip_audio_effect", "add_track_insert", "add_bus", "add_bus_insert",
                                       "route_track", "add_mask", "add_keyframe", "add_mask_keyframe", "set_color_grade",
                                       "set_color_curve", "set_color_wheel", "set_color_qualifier", "add_color_window",
                                       "set_color_detail", "rename_color_node", "set_track_role", "set_automation_curve",
                                       "add_ducking_sidechain"}
    _apply(rebuilt, ops)
    assert _family_view(describe(rebuilt)) == _family_view(wanted)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_each_family_alone_is_rebuilt_from_its_own_description(family):
    original = rich_project()
    wanted = describe(original)
    rebuilt = _strip(original)
    if family == "keyframes":                                             # les images clés de masque supposent les masques
        _apply(rebuilt, FAMILIES["masks"](wanted))
    _apply(rebuilt, FAMILIES[family](wanted))
    view, target = _family_view(describe(rebuilt)), _family_view(wanted)
    keys = {"effects": ("effects", "audio_effects", "inserts"), "buses": ("buses", "bus_id"), "masks": ("masks",),
            "keyframes": ("keyframes",), "color": ("color_grade",),
            "audio": ("roles", "automation", "ducking_sidechains")}[family]
    for key in keys:
        assert view[key] == target[key], key


# --- Rejeu : des réglages visés par identifiant ne changent rien ----------------------------------------------------------------


def _replay_ops(data: dict) -> list[dict]:
    ops: list[dict] = []
    clip = _clip(data, "c1")
    animated = {tuple(k["property_name"].split(".")[1:]) for k in clip["animation"] if k["property_name"].startswith("mask.")}
    for effect in clip["effects"]:
        ops += [{"op": "update_effect", "clip": "c1", "effect": effect["id"], "params": effect["params"]},
                {"op": "set_effect_enabled", "clip": "c1", "effect": effect["id"], "enabled": effect["enabled"]}]
    for effect in _clip(data, "c4")["audio_effects"]:
        ops += [{"op": "update_clip_audio_effect", "clip": "c4", "effect": effect["id"], "params": effect["params"]},
                {"op": "set_clip_audio_effect_enabled", "clip": "c4", "effect": effect["id"], "enabled": effect["enabled"]}]
    track = _track(data, "A1")
    for insert in track["inserts"]:
        ops += [{"op": "update_track_insert", "track": "A1", "insert": insert["id"], "params": insert["params"]},
                {"op": "set_track_insert_enabled", "track": "A1", "insert": insert["id"], "enabled": insert["enabled"]}]
    ops += [{"op": "route_track", "track": "A1", "bus": track["bus_id"]},
            {"op": "set_track_volume", "track": "A1", "volume_db": track["volume_db"]}]
    for bus in data["sequences"][0]["buses"]:
        ops += [{"op": "rename_bus", "bus": bus["id"], "name": bus["name"]},
                {"op": "set_bus_volume", "bus": bus["id"], "volume_db": bus["volume_db"]},
                {"op": "set_bus_muted", "bus": bus["id"], "muted": bus["muted"]}]
        for insert in bus["inserts"]:
            ops += [{"op": "update_bus_insert", "bus": bus["id"], "insert": insert["id"], "params": insert["params"]},
                    {"op": "set_bus_insert_enabled", "bus": bus["id"], "insert": insert["id"], "enabled": insert["enabled"]}]
    for audio_track in (t for t in data["sequences"][0]["tracks"] if t["type"] == "audio"):
        ops.append({"op": "set_track_role", "track": audio_track["id"], "role": audio_track["audio_role"]})
        for point in audio_track["automation"]["points"]:
            ops.append({"op": "update_automation_point", "track": audio_track["id"], "at": point["time_seconds"],
                        "gain_db": point["gain_db"], "fade_seconds": point["fade_seconds"]})
    for sidechain in data["sequences"][0]["ducking_sidechains"]:
        ops += [{"op": "update_ducking_config", "ducking": sidechain["id"], **sidechain["config"]},
                {"op": "set_ducking_enabled", "ducking": sidechain["id"], "enabled": sidechain["enabled"]}]
    for mask in clip["compositing"]["masks"]:
        fields = {name: mask[name] for name in (*_MASK_NUMBERS, "inverted", "mode", "name") if (mask["id"], name) not in animated}
        ops.append({"op": "set_mask", "clip": "c1", "mask": mask["id"], **fields})
    ops += [op for op in _keyframe_ops(data)]                               # même instant : remplace en gardant l'identifiant
    for node in clip["color_grade"]["nodes"]:
        if node["type"] == "corrector":
            grade = node["grade"]
            ops.append({"op": "set_color_grade", "clip": "c1", "node": node["id"], **{n: grade[n] for n in _GRADE_NUMBERS}})
            ops += [{"op": "set_color_window", "clip": "c1", "window": w["id"], "width": w["width"], "feather": w["feather"]}
                    for w in node["windows"]]
    return ops


def test_replaying_the_description_by_identifier_succeeds_and_changes_nothing():
    project = rich_project()
    before = describe(project)
    ops = _replay_ops(before)
    assert len(ops) > 20
    _apply(project, ops)
    assert describe(project) == before


def test_a_modification_written_from_the_description_is_visible_in_the_next_description():
    project = rich_project()
    data = describe(project)
    fx = _clip(data, "c1")["effects"][0]
    bus = data["sequences"][0]["buses"][0]
    node = _clip(data, "c1")["color_grade"]["nodes"][1]
    _apply(project, [
        {"op": "update_effect", "clip": "c1", "effect": fx["id"], "params": {"intensity": fx["params"]["intensity"] + 1.0}},
        {"op": "set_bus_volume", "bus": bus["id"], "volume_db": bus["volume_db"] - 3.0},
        {"op": "set_color_grade", "clip": "c1", "node": node["id"], "exposure": node["grade"]["exposure"] + 0.5},
    ])
    after = describe(project)
    assert _clip(after, "c1")["effects"][0]["params"]["intensity"] == 5.0
    assert after["sequences"][0]["buses"][0]["volume_db"] == -3.0
    assert _clip(after, "c1")["color_grade"]["nodes"][1]["grade"]["exposure"] == 0.5


# --- Ce qu'aucun verbe n'écrit encore --------------------------------------------------------------------------------------------


def _apply_refused(project, op: dict) -> bool:
    return not apply_script(Session(project), parse_script({"kut_script": 1, "ops": [op]})).ok


def test_the_gaps_are_real_and_named():
    """Chaque manque est une décision écrite : le jour où un verbe l'écrit, ce test échoue et ``GAPS`` (et la documentation) se mettent à jour."""
    with pytest.raises(ScriptInvalid):                    # une image clé d'effet : ``property`` n'accepte que le registre statique
        parse_script({"kut_script": 1, "ops": [{"op": "add_keyframe", "clip": "c1", "property": "fx.fx2.intensity", "at": 0, "value": 0.5}]})
    names = set(VERBS)
    assert [n for n in names if any(word in n for word in ("composition", "chroma", "blend"))] == []
    assert {"set_track_role", "set_automation_curve", "add_ducking_sidechain"} <= names                     # ces familles s'écrivent (voir « audio » ci-dessus)
    assert {n for n in names if "lut" in n} == {"import_lut", "remove_lut"}              # un LUT s'importe depuis un fichier, pas depuis une description
    assert not any("ducking_config" in n and n != "update_ducking_config" for n in names)                    # ``Track.ducking_config`` : aucun consommateur, aucun éditeur
    assert _apply_refused(rich_project(), {"op": "set_automation_curve", "track": "V1", "points": []})     # la courbe d'une piste vidéo : l'interface n'en a pas
    project = rich_project()
    assert _clip_of(project, "x1").composition is not None and GAPS                       # la composition se lit, elle ne s'écrit pas
