"""``describe``, partie B (ADR-0005 étape 5) : effets, étalonnage, compositing, composition, images clés, bus et audio avancé.

Ce que prouve ce fichier : la **forme** de chaque section (un extrait fixe, ``tests/data/describe_golden_sections.json``, pour la
changer exprès : ``KUT_UPDATE_DESCRIBE_GOLDEN=1``), les identifiants que les verbes attendent, les valeurs limites (JSON strict), la
robustesse (un graphe très profond ne récurse pas, une valeur illisible est ``null``) et la lecture seule. L'aller-retour avec les
verbes est dans ``tests/test_describe_roundtrip.py``.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from describe_project import rich_project, small_project

from core.animation import Keyframe
from core.audio_automation import DuckingSidechain
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.effects_model import ClipEffect, EffectType
from core.composition import Composition, CompositionGraph, OutputNode, TransformNode
from core.node_graph import NodeLink
from core.project_io import project_payload
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting.describe import describe, describe_json
from core.visual_effects import TransformKeyframe

GOLDEN = Path(__file__).parent / "data" / "describe_golden_sections.json"


def _clips(data: dict) -> dict[str, dict]:
    return {
        clip["id"]: clip
        for sequence in data["sequences"]
        for track in sequence["tracks"]
        for clip in track["clips"]
    }


def _tracks(data: dict) -> dict[str, dict]:
    return {track["id"]: track for track in data["sequences"][0]["tracks"]}


def _without_curves(node):
    """Les courbes de transfert (16 points × 4 canaux par réglage) rendraient l'extrait illisible : testées à part."""
    if isinstance(node, dict):
        return {
            key: _without_curves(value)
            for key, value in node.items()
            if key != "curves"
        }
    if isinstance(node, list):
        return [_without_curves(item) for item in node]
    return node


# --- Format : un extrait fixe ----------------------------------------------------------------------------------------------


def test_golden_excerpt_of_the_sections_of_part_b():
    data = describe(rich_project())
    clips, tracks, sequence = _clips(data), _tracks(data), data["sequences"][0]
    excerpt = {
        "c1": {
            key: clips["c1"][key]
            for key in (
                "effects",
                "color_grade",
                "compositing",
                "transform_keyframes",
                "animation",
            )
        },
        "c4": {"audio_effects": clips["c4"]["audio_effects"]},
        "x1": {"composition": clips["x1"]["composition"]},
        "A1": {
            key: tracks["A1"][key]
            for key in ("bus_id", "inserts", "automation", "ducking_config")
        },
        "buses": sequence["buses"],
        "ducking_sidechains": sequence["ducking_sidechains"],
        "color_presets": data["color_presets"],
    }
    text = (
        json.dumps(
            _without_curves(excerpt), ensure_ascii=False, allow_nan=False, indent=2
        )
        + "\n"
    )
    if os.environ.get("KUT_UPDATE_DESCRIBE_GOLDEN") == "1":
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8"), (
        "Le format des sections décrites en partie B a changé. Si c'est voulu : montez DESCRIBE_VERSION si une clé disparaît ou change "
        "de sens, annoncez-le dans le CHANGELOG, puis régénérez avec KUT_UPDATE_DESCRIBE_GOLDEN=1."
    )


def test_a_clip_without_any_of_it_has_empty_values_not_missing_keys():
    clip = _clips(describe(small_project()))["c1"]
    assert (
        clip["effects"] == []
        and clip["audio_effects"] == []
        and clip["animation"] == []
        and clip["transform_keyframes"] == []
    )
    assert (
        clip["color_grade"] is None and clip["composition"] is None
    )  # « aucun » est null
    assert (
        clip["compositing"]["masks"] == []
        and clip["compositing"]["blend_mode"] == "normal"
    )
    assert clip["compositing"]["chroma_key"]["enabled"] is False
    track = _tracks(describe(small_project()))["A1"]
    assert (
        track["bus_id"],
        track["inserts"],
        track["ducking_config"],
        track["automation"],
    ) == (None, [], None, {"points": []})
    assert (
        describe(small_project())["color_presets"] == []
        and describe(small_project())["sequences"][0]["buses"] == []
    )


# --- Effets et audio : les identifiants des verbes -----------------------------------------------------------------------------


def test_visual_effects_keep_their_order_ids_and_parameters():
    effects = _clips(describe(rich_project()))["c1"]["effects"]
    assert [(e["id"], e["type"], e["enabled"]) for e in effects] == [
        ("fx1", "blur", True),
        ("fx2", "vignette", False),
    ]
    assert effects[0]["params"] == {"intensity": 4.0} and effects[1]["params"] == {
        "intensity": 0.7
    }


def test_audio_effects_inserts_and_buses():
    data = describe(rich_project())
    effects = _clips(data)["c4"]["audio_effects"]
    assert [(e["id"], e["type"], e["enabled"]) for e in effects] == [
        ("afx1", "noise_reduce", True),
        ("afx2", "compressor", False),
    ]
    assert effects[1]["params"]["ratio"] == 3.0
    track = _tracks(data)["A1"]
    assert track["bus_id"] == "b1" and [
        (i["id"], i["type"]) for i in track["inserts"]
    ] == [("ti1", "compressor")]
    assert track["inserts"][0]["params"]["ratio"] == 4.0
    bus = data["sequences"][0]["buses"][0]
    assert (bus["id"], bus["name"], bus["volume_db"], bus["muted"]) == (
        "b1",
        "Voix",
        0.0,
        False,
    )
    assert [(i["id"], i["params"]["ratio"]) for i in bus["inserts"]] == [("bi1", 2.0)]


def test_automation_and_ducking():
    data = describe(rich_project())
    track = _tracks(data)["A1"]
    assert track["automation"]["points"] == [
        {"time_seconds": 0.0, "gain_db": 0.0, "fade_seconds": 0.0},
        {"time_seconds": 2.0, "gain_db": -6.0, "fade_seconds": 0.5},
    ]
    assert track["ducking_config"] == {
        "threshold_db": -18.0,
        "reduction_db": 10.0,
        "attack_seconds": 0.05,
        "release_seconds": 0.4,
    }
    assert data["sequences"][0]["ducking_sidechains"] == [
        {
            "id": "dk1",
            "music_track_id": "A1",
            "voice_track_id": "A2",
            "enabled": True,
            "config": {
                "threshold_db": -20.0,
                "reduction_db": 9.0,
                "attack_seconds": 0.05,
                "release_seconds": 0.4,
            },
        }
    ]


def test_a_legacy_ducking_config_written_as_a_dictionary_is_described():
    project = rich_project()
    project.tracks[2].ducking_config = {
        "threshold_db": -30.0,
        "reduction_db": 8.0,
    }  # le mode « raw » des anciens fichiers
    config = _tracks(describe(project))["A1"]["ducking_config"]
    assert config == {
        "threshold_db": -30.0,
        "reduction_db": 8.0,
        "attack_seconds": 0.05,
        "release_seconds": 0.4,
    }


def test_buses_of_another_sequence_are_described_under_their_own_sequence():
    project = rich_project()
    project.sequences[1].buses = deepcopy(project.sequences[0].buses)
    project.sequences[1].buses[0].id = "b2"
    data = describe(project)
    assert [bus["id"] for bus in data["sequences"][0]["buses"]] == ["b1"] and [
        bus["id"] for bus in data["sequences"][1]["buses"]
    ] == ["b2"]


# --- Images clés -------------------------------------------------------------------------------------------------------------


def test_keyframes_carry_id_time_value_interpolation_and_tangents():
    clip = _clips(describe(rich_project()))["c1"]
    assert [
        (k["property_name"], k["time_seconds"], k["value"], k["id"])
        for k in clip["transform_keyframes"]
    ] == [("scale", 0.0, 1.0, "kf1"), ("scale", 1.0, 1.5, "kf2")]
    assert list(clip["transform_keyframes"][0]) == [
        "property_name",
        "time_seconds",
        "value",
        "interpolation",
        "in_slope",
        "out_slope",
        "tangent_mode",
        "id",
    ]
    assert clip["has_keyframes"] is True
    dynamic = [
        k for k in clip["animation"] if k["property_name"].startswith(("fx.", "mask."))
    ]
    assert [(k["property_name"], k["time_seconds"], k["value"]) for k in dynamic] == [
        ("fx.fx2.intensity", 0.5, 0.2),
        ("fx.fx2.intensity", 2.0, 0.9),
        ("mask.mk1.feather", 0.0, 0.0),
        ("mask.mk1.feather", 1.0, 0.3),
    ]


def test_bezier_slopes_booleans_and_pairs_are_described():
    project = rich_project()
    clip = next(c for t in project.tracks for c in t.clips if c.id == "c1")
    first = clip.transform_keyframes[0]
    clip.transform_keyframes[0] = replace(
        first,
        interpolation="bezier",
        in_slope=0.5,
        out_slope=-1.25,
        tangent_mode="broken",
    )
    data = _clips(describe(project))["c1"]["transform_keyframes"][0]
    assert (
        data["interpolation"],
        data["in_slope"],
        data["out_slope"],
        data["tangent_mode"],
    ) == ("bezier", 0.5, -1.25, "broken")
    clip.animation.append(
        Keyframe("graphic.test_pair", 3.0, (0.25, 0.5), in_slope=(1.0, 2.0), id="kp")
    )
    pair = next(
        k for k in _clips(describe(project))["c1"]["animation"] if k["id"] == "kp"
    )
    assert (
        pair["value"] == [0.25, 0.5]
        and pair["in_slope"] == [1.0, 2.0]
        and pair["out_slope"] is None
    )


# --- Étalonnage ----------------------------------------------------------------------------------------------------------------


def test_color_grade_is_always_a_graph_with_the_ids_of_the_verbs():
    grade = _clips(describe(rich_project()))["c1"]["color_grade"]
    assert [(n["id"], n["type"]) for n in grade["nodes"]] == [
        ("n1", "corrector"),
        ("n2", "corrector"),
        ("n3", "corrector"),
        ("m1", "mixer"),
    ]
    assert grade["links"] == [
        {"source": "n1", "target": "n2", "port": 0},
        {"source": "n1", "target": "n3", "port": 0},
        {"source": "n2", "target": "m1", "port": 0},
        {"source": "n3", "target": "m1", "port": 1},
    ]
    first, second, _third, mixer = grade["nodes"]
    assert mixer == {"id": "m1", "type": "mixer", "kind": "layer"}
    assert (
        first["grade"]["exposure"],
        first["grade"]["saturation"],
        first["grade"]["lift"]["r"],
    ) == (0.5, 1.2, 0.1)
    assert first["grade"]["lut"] == {
        "path": "luts/look.cube",
        "title": "Look",
        "sha1": "a" * 40,
        "size": 1234,
        "missing": False,
    }
    assert len(first["grade"]["curves"]["master"]) == 16 and first["grade"]["curves"][
        "master"
    ][5] == [0.333333, 0.4]
    assert first["grade"]["curves"]["red"][5] == [0.333333, 0.333333]
    assert (
        second["label"],
        second["grade"]["temperature"],
        second["qualifier"]["hue_center"],
        second["blur"],
    ) == ("Peau", 12.0, 20.0, 2.0)
    assert (
        second["windows"][0]["id"] == "w1"
        and second["windows"][0]["shape"] == "ellipse"
        and second["windows"][0]["width"] == 0.3
    )
    assert first["qualifier"] is None and first["windows"] == []


def test_a_single_grade_is_the_node_n1_and_an_ungraded_clip_is_null():
    project = small_project()
    clip = project.tracks[0].clips[0]
    clip.color_grade = ColorGrade(exposure=0.25)
    graded = _clips(describe(project))["c1"]["color_grade"]
    assert (
        [n["id"] for n in graded["nodes"]] == ["n1"]
        and graded["links"] == []
        and graded["nodes"][0]["grade"]["exposure"] == 0.25
    )
    clip.color_grade = "n'importe quoi"  # une valeur illisible n'est pas inventée
    assert _clips(describe(project))["c1"]["color_grade"] is None
    clip.color_grade = None
    assert _clips(describe(project))["c1"]["color_grade"] is None


def test_color_presets_are_described_with_their_grade():
    preset = describe(rich_project())["color_presets"][0]
    assert (preset["id"], preset["name"], preset["category"], preset["builtin"]) == (
        "p1",
        "Chaud",
        "warm",
        False,
    )
    assert (
        preset["grade"]["temperature"] == 30.0 and preset["grade"]["gain"]["r"] == 0.1
    )


# --- Compositing -----------------------------------------------------------------------------------------------------------------


def test_compositing_masks_chroma_key_and_blend_mode():
    compositing = _clips(describe(rich_project()))["c1"]["compositing"]
    assert compositing["blend_mode"] == "screen"
    assert compositing["chroma_key"] == {
        "enabled": True,
        "color": "#0000FF",
        "tolerance": 0.2,
        "softness": 0.05,
        "spill_suppression": 0.0,
    }
    ellipse, polygon, path = compositing["masks"]
    assert (
        ellipse["id"],
        ellipse["shape"],
        ellipse["inverted"],
        ellipse["name"],
        ellipse["nodes"],
    ) == ("mk1", "ellipse", True, "Visage", None)
    assert (
        polygon["points"]
        == [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.5, "y": 0.9}]
        and polygon["nodes"] is None
    )
    assert polygon["keyframes"] == [] and polygon["nodes"] is None
    assert path["nodes"][0] == {
        "x": 0.1,
        "y": 0.1,
        "in_dx": 0.0,
        "in_dy": 0.0,
        "out_dx": 0.1,
        "out_dy": 0.0,
    }
    assert len(path["nodes"]) == len(path["points"]) == len(path["handles"]) == 3


# --- Composition -------------------------------------------------------------------------------------------------------------------


def test_composition_nodes_links_and_animation():
    composition = _clips(describe(rich_project()))["x1"]["composition"]
    nodes = {node["id"]: node for node in composition["graph"]["nodes"]}
    assert [node["type"] for node in composition["graph"]["nodes"]] == [
        "media",
        "solid",
        "graphic",
        "transform",
        "merge",
        "effects",
        "grade",
        "mask",
        "key",
        "output",
    ]
    assert (
        nodes["a"]["asset_id"],
        nodes["a"]["fill"],
        nodes["a"]["gain_db"],
        nodes["a"]["pan_x"],
    ) == ("m1", True, -3.0, 0.2)
    assert (nodes["mg"]["blend"], nodes["mg"]["opacity"]) == ("screen", 0.7)
    assert nodes["e"]["effects"][0] == {
        "id": "fx9",
        "type": "blur",
        "enabled": True,
        "params": {"intensity": 3.0},
    }
    assert (
        nodes["gr"]["grade"]["nodes"][0]["grade"]["exposure"] == 0.3
        and nodes["g"]["graphic"]["text"] == "Titre"
    )
    assert (
        nodes["mk"]["masks"][0]["id"] == "cm1" and nodes["kn"]["key"]["enabled"] is True
    )
    assert nodes["mk"]["masks"][0]["keyframes"] == [
        {"property_name": "feather", "time_seconds": 0.5, "value": 0.2}
    ]  # image clé historique
    assert composition["graph"]["links"][2] == {
        "source": "s",
        "target": "mg",
        "port": 1,
    }
    assert (
        composition["duration"] == 4.0
        and composition["animation"][0]["property_name"] == "mask.cm1.feather"
    )
    assert _clips(describe(rich_project()))["x1"]["kind"] == "composition"


def test_a_very_deep_graph_is_described_without_recursion():
    """Profondeur : un graphe de 3 000 nœuds en série (étalonnage) et 3 000 transformations (composition) se lisent en deux listes."""
    depth = 3000
    project = small_project()
    clip = project.tracks[0].clips[0]
    clip.color_grade = ColorNodeGraph.serial(
        ColorNode(f"n{index}") for index in range(1, depth + 1)
    )
    nodes = tuple(TransformNode(f"t{index}") for index in range(depth)) + (
        OutputNode(),
    )
    links = tuple(
        NodeLink(f"t{index}", f"t{index + 1}" if index + 1 < depth else "output", 0)
        for index in range(depth)
    )
    clip.composition = Composition(
        graph=CompositionGraph(nodes=nodes, links=links), duration=2.0
    )
    data = _clips(json.loads(describe_json(project, indent=None)))["c1"]
    assert (
        len(data["color_grade"]["nodes"]) == depth
        and len(data["color_grade"]["links"]) == depth - 1
    )
    assert (
        len(data["composition"]["graph"]["nodes"]) == depth + 1
        and len(data["composition"]["graph"]["links"]) == depth
    )


# --- JSON strict, lecture seule, taille -----------------------------------------------------------------------------------------


def test_non_finite_values_in_the_new_sections_are_null_and_the_json_is_strict():
    project = rich_project()
    clip = next(c for t in project.tracks for c in t.clips if c.id == "c1")
    clip.effects[0].params["intensity"] = float("nan")
    clip.color_grade = ColorGrade(exposure=0.1)
    track = next(t for t in project.tracks if t.id == "A1")
    track.inserts[0].params["ratio"] = float("inf")
    project.sequences[0].buses[0].volume_db = float("-inf")
    data = json.loads(
        describe_json(project),
        parse_constant=lambda name: pytest.fail(f"constante non standard : {name}"),
    )
    assert _clips(data)["c1"]["effects"][0]["params"]["intensity"] is None
    assert (
        _tracks(data)["A1"]["inserts"][0]["params"]["ratio"] is None
        and data["sequences"][0]["buses"][0]["volume_db"] is None
    )


def test_values_are_rounded_to_six_decimals_in_the_new_sections():
    project = small_project()
    project.tracks[0].clips[0].transform_keyframes.append(
        TransformKeyframe("opacity", 1 / 3, 2 / 3, id="k")
    )
    keyframe = _clips(describe(project))["c1"]["transform_keyframes"][0]
    assert (keyframe["time_seconds"], keyframe["value"]) == (0.333333, 0.666667)


def test_the_rich_project_is_not_modified_and_is_deterministic():
    project = rich_project()
    before = json.dumps(project_payload(project), sort_keys=True)
    snapshot = deepcopy(project)
    first = describe_json(project)
    assert describe_json(project) == first == describe_json(rich_project())
    assert (
        json.dumps(project_payload(project), sort_keys=True) == before
        and project == snapshot
    )


def test_ten_thousand_clips_with_effects_and_grades_stay_fast():
    graded = ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.1)),))
    clips = [
        Clip(
            f"c{index}",
            "m",
            "V1",
            float(index),
            0.0,
            1.0,
            effects=[ClipEffect("fx1", EffectType.BLUR)],
            color_grade=graded,
        )
        for index in range(10_000)
    ]
    project = Project(
        "gros",
        width=64,
        height=36,
        fps=30.0,
        media_assets=[MediaAsset("m", "/x/m.mp4", "m", 1.0, 64, 36, 30.0, "video")],
        sequences=[
            Sequence(
                "seq-main",
                "S",
                64,
                36,
                30.0,
                tracks=[Track("V1", "V1", "video", clips=clips)],
            )
        ],
    )
    started = time.perf_counter()
    text = describe_json(project, indent=None)
    elapsed = time.perf_counter() - started
    assert len(json.loads(text)["sequences"][0]["tracks"][0]["clips"]) == 10_000
    assert elapsed < 20.0, (
        f"describe a pris {elapsed:.1f} s pour 10 000 clips étalonnés"
    )


def test_a_sidechain_of_an_unknown_track_is_still_described():
    project = rich_project()
    project.active_sequence.ducking_sidechains.append(
        DuckingSidechain("dk2", "gone", "away")
    )
    assert [
        item["id"] for item in describe(project)["sequences"][0]["ducking_sidechains"]
    ] == ["dk1", "dk2"]
