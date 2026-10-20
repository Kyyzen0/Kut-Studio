"""``describe``, partie C (ADR-0005 étape 5) : bibliothèque, guides, flou de mouvement, grille rythmique, classements, Multicam, suivi,
style des calques graphiques et style de texte.

Ce que prouve ce fichier : la **forme** de chaque section (un extrait fixe, ``tests/data/describe_golden_c.json``, pour le changer exprès :
``KUT_UPDATE_DESCRIBE_GOLDEN=1``), la robustesse (valeurs illisibles, données de suivi énormes, 10 000 clips, séquence sans piste d'angle),
et la lecture seule (aucun cache posé sur les données du suivi). L'aller-retour avec les verbes est dans
``tests/test_describe_roundtrip_c.py``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

from describe_project import rich_project, small_project

from core.project_io import project_payload
from core.project_model import MediaAsset, Project, Track
from core.scripting.describe import describe, describe_json
from core.scripting.describe_tracking import MAX_MANUAL_POINTS
from core.timeline_operations import add_clip_to_track
from core.tracking_model import ClipTracking, Sample, SampleStatus, TrackData, TrackLink, Tracker

GOLDEN = Path(__file__).parent / "data" / "describe_golden_c.json"


def _clips(data: dict, sequence: int = 0) -> dict[str, dict]:
    return {clip["id"]: clip for track in data["sequences"][sequence]["tracks"] for clip in track["clips"]}


def _excerpt(data: dict) -> dict:
    clips = _clips(data)
    sequences = {item["id"]: item for item in data["sequences"]}
    return {
        "library": data["library"],
        "sequence": {key: sequences["seq-main"][key] for key in ("guides", "motion_blur", "multicam", "beat_grid", "generated_groups")},
        "multicam_source": sequences["seq_mc"]["multicam"],
        "c5.tracking": clips["c5"]["tracking"],
        "c1.tracking": clips["c1"]["tracking"],
        "g1.graphic": clips["g1"]["graphic"],
        "g2.graphic": clips["g2"]["graphic"],
        "s1.text_style": clips["s1"]["text_style"],
        "not_described": data["not_described"],
    }


def test_golden_excerpt_of_the_sections_of_part_c():
    text = json.dumps(_excerpt(describe(rich_project())), ensure_ascii=False, indent=2) + "\n"
    if os.environ.get("KUT_UPDATE_DESCRIBE_GOLDEN") == "1":
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8"), (
        "La forme des sections de la partie C a changé. Si c'est voulu : montez DESCRIBE_VERSION si une clé disparaît ou change "
        "de sens, annoncez-le dans le CHANGELOG, puis régénérez avec KUT_UPDATE_DESCRIBE_GOLDEN=1."
    )


def test_the_identifiers_the_verbs_expect_are_in_the_description():
    data = describe(rich_project())
    assert [item["id"] for item in data["library"]["folders"]] == ["fld1", "fld2"]
    assert [item["id"] for item in data["sequences"][0]["guides"]] == ["gd1", "gd2", "gd3"]
    clips = _clips(data)
    assert [item["id"] for item in clips["c5"]["tracking"]["trackers"]] == ["tr1", "tr2"]
    assert [item["id"] for item in clips["c3"]["tracking"]["links"]] == ["lk1"]
    assert clips["c3"]["tracking"]["links"][0]["source_clip_id"] == "c5"
    assert clips["c1"]["tracking"]["links"][0]["mask_id"] == "mk1" and clips["c5"]["tracking"]["links"][0]["source_clip_id"] is None
    assert [item["id"] for item in data["sequences"][2]["multicam"]["angles"]] == ["angle-1", "angle-2"]
    assert clips["mc2"]["angle_id"] == "angle-2" and clips["mc2"]["sequence_id"] == "seq_mc" and clips["mc2"]["kind"] == "sequence"


def test_the_description_stays_read_only_and_leaves_no_cache_on_the_tracking_data():
    project = rich_project()
    before = json.dumps(project_payload(project), sort_keys=True)
    fresh = TrackData.from_samples(30.0, {index: Sample(1.0, 2.0, 1.0, SampleStatus.MANUAL) for index in (0, 5)}, source_size=(1920, 1080))
    unread = _grid_project(2, fresh)
    keys = set(fresh.__dict__)                                                      # ni ``_cached_arrays`` ni ``_cached_valid``
    describe(project)
    describe(unread)
    assert json.dumps(project_payload(project), sort_keys=True) == before
    assert set(fresh.__dict__) == keys, "describe ne pose aucune mémoire sur les données du suivi"


def test_a_plain_clip_has_a_default_text_style_and_no_tracking():
    clip = _clips(describe(small_project()))["c1"]
    assert clip["tracking"] is None and clip["text_style"]["alignment"] == "bottom_center" and clip["text_style"]["background_color"] is None
    assert clip["graphic"] is None
    assert describe(small_project())["library"] == {"folders": [], "tags": [], "assignments": []}


# --- Valeurs illisibles et données démesurées ---------------------------------------------------------------------------------


def test_unreadable_values_become_null_and_nothing_raises():
    project = rich_project()
    sequence = project.sequences[0]
    object.__setattr__(sequence.guides[0], "position", float("nan"))
    sequence.motion_blur = object()                                   # un réglage que le modèle ne sait pas lire : les défauts
    sequence.generated_groups["lb1"]["data"] = {"rows": [["1", "a", "b", "#fff"]] * 11}      # plus de lignes que le modèle n'en écrit
    sequence.generated_groups["bad"] = {"kind": "leaderboard", "data": "pas un dictionnaire", "clips": "pas une liste"}
    data = describe(project)
    sequence_data = data["sequences"][0]
    assert sequence_data["guides"][0]["position"] is None
    assert sequence_data["motion_blur"] == {"enabled": True, "shutter_angle": 180.0, "samples": 8}
    groups = {group["id"]: group for group in sequence_data["generated_groups"]}
    assert groups["lb1"]["data"] is None and groups["bad"]["data"] is None and groups["bad"]["clips"] == []
    json.loads(describe_json(project))


def test_a_link_to_a_missing_source_and_data_without_a_rate_have_a_null_time():
    project = rich_project()
    c5 = next(clip for track in project.tracks for clip in track.clips if clip.id == "c5")
    link = TrackLink(id="orphan", source_clip_id="absent", tracker_ids=("tr1",))
    c5.tracking = replace(c5.tracking, links=(link,), trackers=(replace(c5.tracking.trackers[0], data=TrackData()),))
    tracking = _clips(describe(project))["c5"]["tracking"]
    assert tracking["links"][0]["reference_time"] is None and tracking["links"][0]["source_clip_id"] == "absent"
    assert tracking["trackers"][0]["data"]["valid_range"] is None and tracking["trackers"][0]["data"]["manual_points"] == []
    assert tracking["stabilization"]["reference_time"] is None                 # plus de cadence: le tracker n'a plus de données


def test_a_multicam_angle_without_clips_has_a_null_offset_and_an_empty_track_is_kept():
    project = rich_project()
    source = next(item for item in project.sequences if item.id == "seq_mc")
    source.tracks[1].clips.clear()
    angle = describe(project)["sequences"][2]["multicam"]["angles"][1]
    assert angle["offset"] is None and angle["id"] == "angle-2"


def _grid_project(clips: int, data: TrackData) -> Project:
    project = Project("grand", width=1920, height=1080, fps=30.0, media_assets=[MediaAsset("m1", "/m/a.mp4", "a", 20.0, 1920, 1080, 30.0, "video")],
                      tracks=[Track("V1", "V1", "video")])
    for index in range(clips):
        clip = add_clip_to_track(project, "m1", "V1", float(index), clip_id=f"c{index}")
        clip.tracking = ClipTracking(trackers=(Tracker(id=f"t{index}", data=data),))
    return project


def test_manual_points_are_bounded_per_tracker_and_the_total_stays_visible():
    samples = {index: Sample(10.0 + index, 20.0, 1.0, SampleStatus.MANUAL) for index in range(100)}
    samples |= {index: Sample(5.0, 5.0, 0.9, SampleStatus.TRACKED) for index in range(100, 400)}
    data = TrackData.from_samples(30.0, samples, source_size=(1920, 1080))
    described = _clips(describe(_grid_project(1, data)))["c0"]["tracking"]["trackers"][0]["data"]
    assert len(described["manual_points"]) == MAX_MANUAL_POINTS and described["manual_points_truncated"] is True
    assert described["counts"] == {"empty": 0, "tracked": 300, "uncertain": 0, "lost": 0, "manual": 100}
    assert described["valid_range"] == [0, 399] and described["count"] == 400
    assert not {"x", "y", "xs", "ys", "status", "confidence"} & set(described)        # jamais les échantillons bruts


def test_a_huge_sample_grid_is_summarised_quickly():
    count = 5_000_000
    status = bytearray(count)
    status[10] = status[count - 10] = int(SampleStatus.MANUAL)
    status[100:200] = bytes([int(SampleStatus.TRACKED)]) * 100
    data = TrackData(rate=30.0, first=3, xs=bytes(4 * count), ys=bytes(4 * count), confidence=bytes(count), status=bytes(status),
                     source_width=1920, source_height=1080)
    started = time.perf_counter()
    described = _clips(describe(_grid_project(1, data)))["c0"]["tracking"]["trackers"][0]["data"]
    assert time.perf_counter() - started < 5.0
    assert described["count"] == count and described["valid_range"] == [13, 3 + count - 10]
    assert described["counts"]["manual"] == 2 and described["counts"]["tracked"] == 100 and len(json.dumps(described)) < 4000


def test_ten_thousand_tracked_clips_are_described_in_reasonable_time_with_a_bounded_output():
    samples = {index: Sample(10.0, 20.0, 1.0, SampleStatus.MANUAL if index % 10 == 0 else SampleStatus.TRACKED) for index in range(3000)}
    shared = TrackData.from_samples(30.0, samples, source_size=(1920, 1080))          # immuable : partagée sans copie, comme l'historique
    project = _grid_project(10_000, shared)
    started = time.perf_counter()
    data = describe(project)
    elapsed = time.perf_counter() - started
    assert elapsed < 60.0, f"10 000 clips suivis : {elapsed:.1f} s"
    clips = _clips(data)
    assert len(clips) == 10_000
    per_tracker = {len(json.dumps(clips[name]["tracking"])) for name in ("c0", "c5000", "c9999")}
    assert max(per_tracker) < 12_000                                                  # borné par tracker, pas par la grille (3 000 images)
