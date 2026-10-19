"""``describe`` (``core/scripting/describe.py``, ADR-0005 étape 5, partie A) : la structure d'un projet en JSON versionné.

Les promesses, chacune prouvée :

* **le format est un choix conscient** : un projet fixe donne le JSON de ``tests/data/describe_golden.json`` (pour le régénérer
  exprès : ``KUT_UPDATE_DESCRIBE_GOLDEN=1``) ;
* **déterministe** : même projet, mêmes octets ; JSON strict (pas de ``NaN``), flottants arrondis ;
* **lecture seule** : le projet n'est pas modifié (même un identifiant de séquence active inconnu n'est pas « réparé »), aucun
  fichier n'est créé, aucun accès disque sans ``check_media`` ;
* **robuste** : médias manquants, valeurs non finies, 10 000 clips, séquences imbriquées citées et jamais développées.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path

import pytest
from describe_project import rich_project, small_project

from core.project_io import project_payload
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import api
from core.scripting import describe as describe_module
from core.scripting.describe import DESCRIBE_VERSION, describe, describe_json
from core.scripting.describe_coverage import NOT_DESCRIBED_SECTIONS
from core.scripting.session import Session

GOLDEN = Path(__file__).parent / "data" / "describe_golden.json"


def _fingerprint(project: Project) -> str:
    return json.dumps(project_payload(project), sort_keys=True)


def _clips(data: dict) -> dict[str, dict]:
    return {clip["id"]: clip for sequence in data["sequences"] for track in sequence["tracks"] for clip in track["clips"]}


# --- Format -------------------------------------------------------------------------------------------------------------


def test_golden_output_of_a_small_fixed_project():
    text = describe_json(small_project()) + "\n"
    if os.environ.get("KUT_UPDATE_DESCRIBE_GOLDEN") == "1":
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8"), (
        "Le format de describe a changé. Si c'est voulu : montez DESCRIBE_VERSION si une clé disparaît ou change de sens, "
        "annoncez-le dans le CHANGELOG, puis régénérez avec KUT_UPDATE_DESCRIBE_GOLDEN=1."
    )


def test_top_level_shape_and_versions():
    data = describe(small_project())
    assert list(data) == ["describe_version", "project", "media", "sequences", "not_described"]
    assert data["describe_version"] == DESCRIBE_VERSION == api.DESCRIBE_VERSION and isinstance(DESCRIBE_VERSION, int)
    assert data["not_described"] == list(NOT_DESCRIBED_SECTIONS) and data["not_described"] == sorted(data["not_described"])
    assert data["project"] == {"name": "Démo", "width": 1280, "height": 720, "fps": 25.0, "duration": 30.0,
                               "active_sequence": "seq-main"}


def _all_keys(node, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            _all_keys(value, found)
    elif isinstance(node, list):
        for item in node:
            _all_keys(item, found)
    return found


def test_nothing_is_said_about_what_is_not_described():
    """Pas de clé vide à la place d'une donnée non lue : ``effects``, ``color_grade``… ne sont pas des clés de la sortie (seule la
    liste ``not_described`` les nomme)."""
    keys = _all_keys(describe(rich_project()))
    for forbidden in ("effects", "audio_effects", "color_grade", "compositing", "composition", "tracking", "buses", "inserts",
                      "automation", "bus_id", "transform_keyframes", "animation", "text_style", "ducking_config"):
        assert forbidden not in keys, forbidden


def test_the_clip_carries_its_place_source_speed_and_content():
    clips = _clips(describe(rich_project()))
    first = clips["c1"]
    assert (first["kind"], first["asset_id"], first["label"], first["enabled"]) == ("media", "m1", "Plan large", True)
    assert first["time_remapping"]["speed"] == 2.0 and first["time_remapping"]["reverse"] is True
    assert first["time_remapping"]["interpolation"] == "blending" and first["time_remapping"]["preserve_pitch"] is False
    assert (first["source_in"], first["source_out"]) == (0.0, 10.0) and first["duration"] == 5.0 and first["timeline_end"] == 5.0
    assert first["angle_id"] == "angle-1" and first["template_slot"] == "slot-01" and first["sequence_id"] is None
    assert first["transform"]["scale"] == 1.2 and first["transform"]["flip_h"] is True and first["transform"]["fill"] is True
    assert (first["gain_db"], first["pan"], first["fade_in"], first["fade_out"]) == (-6.0, -0.5, 0.25, 0.5)
    assert first["has_keyframes"] is True and first["has_speed_curve"] is False
    frozen = clips["c2"]["time_remapping"]
    assert frozen["freeze_mode"] == "freeze" and frozen["freeze_source_time"] == 1.0 and frozen["freeze_duration"] == 2.0
    assert clips["c3"]["has_speed_curve"] is True
    assert clips["s1"]["text"] == "Bonjour"
    graphic = clips["g1"]
    assert graphic["kind"] == "graphic" and graphic["graphic"]["type"] == "text" and graphic["graphic"]["locked"] is False
    assert graphic["graphic"]["source_path"] is None and graphic["graphic"]["parent_id"] is None


def test_tracks_sequences_media_transitions_and_markers():
    data = describe(rich_project(), check_media=False)
    tracks = {track["id"]: track for track in data["sequences"][0]["tracks"]}
    assert (tracks["V1"]["solo"], tracks["V2"]["locked"], tracks["V2"]["visible"]) == (True, True, False)
    assert tracks["A1"]["muted"] is True and tracks["A1"]["volume_db"] == -3.5 and tracks["A1"]["pan"] == 0.25
    assert tracks["A1"]["audio_role"] == "music" and tracks["S1"]["type"] == "subtitle"
    assert [(s["id"], s["active"], s["format_source"]) for s in data["sequences"]] == [("seq-main", True, None), ("seq-b", False, "seq-main")]
    assert data["sequences"][1]["width"] == 1080 and data["sequences"][1]["height"] == 1920
    assert data["sequences"][0]["transitions"] == [
        {"id": "t1", "from_clip_id": "c1", "to_clip_id": "c2", "type": "dissolve", "duration": 0.5}]
    assert [(m["id"], m["time_seconds"], m["category"]) for m in data["sequences"][0]["markers"]] == [
        ("k2", 1.0, "todo"), ("k1", 5.0, "chapter")]
    media = {item["id"]: item for item in data["media"]}
    assert media["m1"]["metadata"] == {"timecode": "01:00:00:00", "timecode_fps": 29.97, "time_reference": 3600.5, "reel": "A001",
                                       "camera": "Cam A", "creation_time": "2026-10-19T10:00:00Z"}
    assert media["m1"]["color_matrix"] == "bt709" and "metadata" not in media["m2"] and media["m2"]["has_audio"] is True


def test_sequence_duration_ignores_disabled_clips_and_follows_the_model():
    project = small_project()
    assert describe(project)["sequences"][0]["duration"] == project.active_sequence.duration
    project.tracks[0].clips[1].enabled = False
    data = describe(project)
    assert data["sequences"][0]["duration"] == project.active_sequence.duration
    assert _clips(data)["c2"]["enabled"] is False


def test_a_nested_sequence_is_cited_never_expanded():
    data = describe(rich_project())
    nested = _clips(data)["n1"]
    assert nested["kind"] == "sequence" and nested["sequence_id"] == "seq-b" and nested["asset_id"] is None
    assert _clips(data)["b1"] in [c for t in data["sequences"][1]["tracks"] for c in t["clips"]]   # décrit une fois, dans sa séquence
    assert sum(1 for sequence in data["sequences"] for t in sequence["tracks"] for c in t["clips"] if c["id"] == "b1") == 1


def test_a_cyclic_nesting_does_not_loop():
    project = rich_project()
    cyclic = next(clip for track in project.sequences[1].tracks for clip in track.clips if clip.id == "b1")
    cyclic.sequence_id, cyclic.asset_id = "seq-main", ""
    assert _clips(describe(project))["b1"]["sequence_id"] == "seq-main"


# --- Déterminisme, JSON strict ------------------------------------------------------------------------------------------


def test_same_project_same_bytes_and_equal_projects_too():
    assert describe_json(rich_project()) == describe_json(rich_project())
    project = rich_project()
    assert describe_json(project) == describe_json(project) == describe_json(deepcopy(project))
    assert api.describe_json(project, indent=None) == json.dumps(describe(project), ensure_ascii=False, allow_nan=False, indent=None)


def test_strict_json_non_finite_values_are_null_and_floats_are_rounded():
    project = small_project()
    asset = project.media_assets[0]
    asset.duration, asset.fps = float("nan"), float("inf")
    clip = project.tracks[0].clips[0]
    clip.timeline_start = -0.0
    clip.source_in = 1 / 3
    text = describe_json(project)                       # allow_nan=False : un NaN ferait lever ici
    data = json.loads(text, parse_constant=lambda name: pytest.fail(f"constante JSON non standard : {name}"))
    media = {item["id"]: item for item in data["media"]}
    assert media["m1"]["duration"] is None and media["m1"]["fps"] is None
    assert _clips(data)["c1"]["source_in"] == 0.333333 and repr(_clips(data)["c1"]["timeline_start"]) == "0.0"


def test_a_clip_whose_duration_is_unknown_is_null_and_so_is_the_sequence_duration():
    project = small_project()
    project.tracks[0].clips[0].source_out = float("inf")
    data = json.loads(describe_json(project))
    assert _clips(data)["c1"]["duration"] is None and _clips(data)["c1"]["timeline_end"] is None
    assert data["sequences"][0]["duration"] is None and data["project"]["duration"] is None


def test_text_with_a_lone_surrogate_is_still_writable():
    project = small_project()
    project.name = "avant\ud800après"
    text = describe_json(project)
    text.encode("utf-8")
    assert json.loads(text)["project"]["name"].startswith("avant") and json.loads(text)["project"]["name"].endswith("après")


def test_keys_keep_the_model_field_names_and_order_is_stable():
    clip = _clips(describe(small_project()))["c1"]
    assert list(clip)[:6] == ["id", "kind", "asset_id", "sequence_id", "angle_id", "template_slot"]
    assert list(describe(small_project())["media"][0])[:4] == ["id", "name", "path", "media_type"]


# --- Lecture seule ------------------------------------------------------------------------------------------------------


def test_describe_never_modifies_the_project():
    project = rich_project()
    before = _fingerprint(project)
    snapshot = deepcopy(project)
    describe(project)
    describe(project, check_media=True)
    describe_json(project)
    assert _fingerprint(project) == before and project == snapshot


def test_an_unknown_active_sequence_is_read_but_not_repaired():
    project = small_project()
    project.active_sequence_id = "gone"
    data = describe(project)
    assert project.active_sequence_id == "gone"                 # ``Project.active_sequence`` l'aurait réécrit
    assert data["project"]["active_sequence"] == "seq-main" and data["sequences"][0]["active"] is True


def test_a_project_without_any_sequence_is_described_without_adding_one():
    project = small_project()
    project.sequences.clear()
    data = describe(project)
    assert project.sequences == [] and data["sequences"] == []
    assert data["project"] == {"name": "Démo", "width": None, "height": None, "fps": None, "duration": None, "active_sequence": None}


def test_describe_reads_inside_a_session_and_agrees_with_the_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = small_project()
    session = Session(project)
    assert session.describe() == describe(project) == api.describe(project)
    with session.transaction("lit pendant l'édition") as tx:
        tx.run(lambda p: setattr(p.tracks[0], "locked", True))
        assert session.describe()["sequences"][0]["tracks"][0]["locked"] is True       # l'état à cet instant
    assert not session.can_undo or session.describe()["sequences"][0]["tracks"][0]["locked"] is True
    assert list(tmp_path.iterdir()) == []                                              # ni verrou ni cache


# --- Médias : aucun accès disque sans demande ----------------------------------------------------------------------------


def test_media_presence_is_unknown_by_default_and_explicit_on_request(tmp_path, monkeypatch):
    present = tmp_path / "ici.mp4"
    present.write_bytes(b"x")
    project = Project("p", width=64, height=36, fps=30.0, media_assets=[
        MediaAsset("ok", str(present), "ok", 1.0, 64, 36, 30.0, "video"),
        MediaAsset("lost", str(tmp_path / "absent.mp4"), "lost", 1.0, 64, 36, 30.0, "video"),
        MediaAsset("sub", "", "sous-titres", 0.0, 0, 0, 0.0, "subtitle"),
    ])

    def boom(*_args, **_kwargs):
        raise AssertionError("accès disque sans check_media")

    monkeypatch.setattr(describe_module.os.path, "exists", boom)
    assert [item["present"] for item in describe(project)["media"]] == [None, None, None]
    monkeypatch.undo()
    assert [item["present"] for item in describe(project, check_media=True)["media"]] == [True, False, None]


def test_a_failing_presence_test_means_missing_and_never_raises(monkeypatch):
    project = Project("p", width=64, height=36, fps=30.0,
                      media_assets=[MediaAsset("m", "/x/m.mp4", "m", 1.0, 64, 36, 30.0, "video")])

    def refuse(_path):
        raise OSError("disque absent")

    monkeypatch.setattr(describe_module.os.path, "exists", refuse)
    assert describe(project, check_media=True)["media"][0]["present"] is False


# --- Gros projets -------------------------------------------------------------------------------------------------------


def test_ten_thousand_clips_are_described_in_reasonable_time():
    clips = [Clip(f"c{index}", "m", "V1", float(index), 0.0, 1.0) for index in range(10_000)]
    project = Project(
        "gros", width=64, height=36, fps=30.0,
        media_assets=[MediaAsset("m", "/x/m.mp4", "m", 1.0, 64, 36, 30.0, "video")],
        sequences=[Sequence("seq-main", "S", 64, 36, 30.0, tracks=[Track("V1", "V1", "video", clips=clips)])],
    )
    started = time.perf_counter()
    text = describe_json(project, indent=None)
    elapsed = time.perf_counter() - started
    data = json.loads(text)
    assert len(data["sequences"][0]["tracks"][0]["clips"]) == 10_000 and data["sequences"][0]["duration"] == 10_000.0
    assert elapsed < 10.0, f"describe a pris {elapsed:.1f} s pour 10 000 clips"
