"""Aller-retour ``describe`` -> script -> ``describe`` (ADR-0005 étape 5, partie C) : bibliothèque, guides, grille rythmique, classement,
Multicam, suivi et style des calques graphiques.

Comme pour la partie B (``tests/test_describe_roundtrip.py``) : on retire la famille d'une copie du projet riche, on écrit un script **à partir
de la description seule** (ses identifiants et ses valeurs), on l'applique, et la description de la famille est celle d'origine. Ce qu'aucun
verbe ne sait écrire (style des calques au-delà de ``set_graphic``, guide verrouillé, flou de mouvement de la séquence, style de texte)
est listé dans ``GAPS_C`` et vérifié.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from describe_project import rich_project, small_project

from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.describe import describe
from core.scripting.verbs import VERBS

GAPS_C = ("graphic style beyond set_graphic", "locked guide", "sequence motion_blur", "text_style")
_SET_GRAPHIC = ("text", "fill_color", "stroke_color", "stroke_width", "font_size", "width", "height")


def _apply(project, ops: list[dict]) -> None:
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report


def _refused(project, op: dict) -> bool:
    try:
        parse_script({"kut_script": 1, "ops": [op]})
    except ScriptInvalid:
        return True
    return not apply_script(Session(project), parse_script({"kut_script": 1, "ops": [op]})).ok


def _unlocked_copy(project):
    copy = deepcopy(project)
    for track in copy.tracks:
        track.locked = False
    return copy


def _clips(data: dict, sequence: int = 0) -> dict[str, dict]:
    return {clip["id"]: clip for track in data["sequences"][sequence]["tracks"] for clip in track["clips"]}


def _sequence(data: dict, sequence_id: str) -> dict:
    return next(item for item in data["sequences"] if item["id"] == sequence_id)


# --- Bibliothèque --------------------------------------------------------------------------------------------------------------


def _library_ops(data: dict) -> list[dict]:
    library = data["library"]
    ops: list[dict] = []
    pending = list(library["folders"])
    created: set[str | None] = {None}
    while pending:                                          # un dossier se crée après son parent, quel que soit l'ordre de la liste
        folder = next(item for item in pending if item["parent_id"] in created)
        pending.remove(folder)
        created.add(folder["id"])
        op = {"op": "create_library_folder", "name": folder["name"], "id": folder["id"]}
        if folder["parent_id"]:
            op["parent"] = folder["parent_id"]
        if folder["color"]:
            op["color"] = folder["color"]
        ops.append(op)
    ops += [{"op": "create_library_tag", "name": tag["name"], "id": tag["id"], "color": tag["color"]} for tag in library["tags"]]
    for item in library["assignments"]:
        if item["folder_id"]:
            ops.append({"op": "move_media_to_folder", "asset": item["asset_id"], "folder": item["folder_id"]})
        ops += [{"op": "tag_media", "asset": item["asset_id"], "tag": tag} for tag in item["tag_ids"]]
    return ops


def test_the_library_is_rebuilt_from_its_description():
    original = rich_project()
    wanted = describe(original)["library"]
    assert wanted["folders"] and wanted["tags"] and wanted["assignments"]
    rebuilt = deepcopy(original)
    rebuilt.library_folders, rebuilt.library_tags, rebuilt.library_assignments = [], [], {}
    assert describe(rebuilt)["library"] != wanted
    _apply(rebuilt, _library_ops(describe(original)))
    assert describe(rebuilt)["library"] == wanted


def test_a_library_script_written_from_the_description_replays_by_identifier_and_changes_nothing():
    project = rich_project()
    before = describe(project)
    library = before["library"]
    ops = [{"op": "rename_library_folder", "folder": f["id"], "name": f["name"]} for f in library["folders"]]
    ops += [{"op": "recolor_library_tag", "tag": t["id"], "color": t["color"]} for t in library["tags"]]
    ops += [{"op": "tag_media", "asset": a["asset_id"], "tag": tag} for a in library["assignments"] for tag in a["tag_ids"]]
    ops += [{"op": "move_library_folder", "folder": f["id"]} | ({"parent": f["parent_id"]} if f["parent_id"] else {})
            for f in library["folders"]]
    _apply(project, ops)
    assert describe(project) == before


# --- Guides, grille rythmique, classement --------------------------------------------------------------------------------------


def test_guides_and_the_beat_grid_are_rebuilt_from_their_description():
    original = rich_project()
    data = describe(original)["sequences"][0]
    rebuilt = deepcopy(original)
    rebuilt.sequences[0].guides, rebuilt.sequences[0].beat_grid = [], None
    ops = [{"op": "add_guide", "orientation": g["orientation"], "position": g["position"], "id": g["id"]}
           for g in data["guides"] if not g["locked"]]
    ops.append({"op": "set_beat_grid", **data["beat_grid"]})
    _apply(rebuilt, ops)
    again = describe(rebuilt)["sequences"][0]
    assert again["beat_grid"] == data["beat_grid"]
    assert again["guides"] == [g for g in data["guides"] if not g["locked"]]            # le guide verrouillé : voir GAPS_C
    moved = [{"op": "move_guide", "guide": g["id"], "position": g["position"]} for g in again["guides"]]
    _apply(rebuilt, moved)
    assert describe(rebuilt)["sequences"][0]["guides"] == again["guides"]


def _leaderboard_ops(groups: list[dict]) -> list[dict]:
    return [{"op": "add_leaderboard", "id": g["id"], "rows": g["data"]["rows"],
             **{key: g["data"][key] for key in ("start", "duration", "top", "delay")}}
            for g in groups if g["kind"] == "leaderboard" and g["data"] is not None]


def test_a_leaderboard_is_rebuilt_and_edited_from_its_description():
    original = small_project()
    rows = [{"rank": "1", "name": "Alice", "value": "9 800", "color": "#FFD84D"}, {"rank": "2", "name": "Bruno", "value": "9 100", "color": "#22B8FF"}]
    _apply(original, [{"op": "add_leaderboard", "id": "lb1", "rows": rows, "start": 1.0, "duration": 5.0, "top": -0.2, "delay": 0.4}])
    wanted = describe(original)["sequences"][0]["generated_groups"]
    assert wanted[0]["id"] == "lb1" and wanted[0]["data"]["rows"] == rows and len(wanted[0]["clips"]) == 10
    rebuilt = small_project()
    _apply(rebuilt, _leaderboard_ops(wanted))
    again = describe(rebuilt)["sequences"][0]["generated_groups"]
    assert [(g["id"], g["kind"], g["data"]) for g in again] == [(g["id"], g["kind"], g["data"]) for g in wanted]
    _apply(rebuilt, [{"op": "update_leaderboard", "leaderboard": "lb1", "rows": rows[:1], "duration": 6.0}])
    edited = describe(rebuilt)["sequences"][0]["generated_groups"][0]
    assert edited["data"]["rows"] == rows[:1] and edited["data"]["duration"] == 6.0 and len(edited["clips"]) == 5


# --- Multicam ------------------------------------------------------------------------------------------------------------------


def _multicam_ops(data: dict) -> list[dict]:
    source = _sequence(data, "seq_mc")
    multicam = source["multicam"]
    angles = []
    for angle in multicam["angles"]:
        track = next(item for item in source["tracks"] if item["id"] == angle["track_id"])
        angles.append({"asset": track["clips"][0]["asset_id"], "offset": angle["offset"], "name": angle["name"]})
    ops: list[dict] = [{"op": "create_multicam_source", "id": source["id"], "name": source["name"], "angles": angles}]
    audio = multicam["audio"]
    ops.append({"op": "set_multicam_audio", "sequence": source["id"], "mode": audio["mode"]}
               | ({"angles": audio["angle_ids"]} if audio["angle_ids"] else {}))
    ops += [{"op": "set_multicam_angle_color", "sequence": source["id"], "angle": a["id"], "color_index": a["color_index"]}
            for a in multicam["angles"]]
    for track in data["sequences"][0]["tracks"]:
        for clip in track["clips"]:
            if clip["sequence_id"] == source["id"]:
                ops.append({"op": "insert_multicam_clip", "sequence": source["id"], "track": track["id"], "at": clip["timeline_start"],
                            "source_in": clip["source_in"], "source_out": clip["source_out"], "id": clip["id"],
                            **({"angle": clip["angle_id"]} if clip["angle_id"] else {})})
    return ops


def _multicam_view(data: dict) -> dict:
    """La source (sans ses identifiants de piste, tirés au hasard à la création) et ses segments."""
    source = _sequence(data, "seq_mc")["multicam"]
    angles = [{key: value for key, value in angle.items() if key != "track_id"} for angle in source["angles"]]
    segments = {cid: {key: clip[key] for key in ("sequence_id", "angle_id", "timeline_start", "duration", "source_in", "source_out")}
                for cid, clip in _clips(data).items() if clip["sequence_id"] == "seq_mc"}
    return {"angles": angles, "audio": source["audio"], "sync_method": source["sync_method"], "segments": segments}


def test_a_multicam_source_and_its_segments_are_rebuilt_from_the_description():
    original = _unlocked_copy(rich_project())
    wanted = describe(original)
    view = _multicam_view(wanted)
    assert [a["offset"] for a in view["angles"]] == [0.0, 0.5] and sorted(view["segments"]) == ["mc1", "mc2"]
    assert view["segments"]["mc2"]["angle_id"] == "angle-2" and view["audio"] == {"mode": "fixed", "angle_ids": ["angle-2"]}
    rebuilt = deepcopy(original)
    _apply(rebuilt, [{"op": "delete_clip", "clip": "mc1"}, {"op": "delete_clip", "clip": "mc2"},
                     {"op": "delete_sequence", "sequence": "seq_mc"}])
    assert all(item["id"] != "seq_mc" for item in describe(rebuilt)["sequences"])
    _apply(rebuilt, _multicam_ops(wanted))
    assert _multicam_view(describe(rebuilt)) == view


# --- Suivi ---------------------------------------------------------------------------------------------------------------------


def _tracking_ops(data: dict) -> list[dict]:
    clips = _clips(data)
    ops: list[dict] = []
    for clip_id, clip in clips.items():
        for tracker in (clip["tracking"] or {}).get("trackers", ()):
            points = tracker["data"]["manual_points"]
            assert not tracker["data"]["manual_points_truncated"] and points
            ops.append({"op": "add_tracker", "clip": clip_id, "at": points[0]["timeline_time"], "x": points[0]["x"], "y": points[0]["y"],
                        "id": tracker["id"], "name": tracker["name"]})
            ops += [{"op": "set_tracker_position", "clip": clip_id, "tracker": tracker["id"], "at": p["timeline_time"], "x": p["x"],
                     "y": p["y"]} for p in points[1:]]
            ops.append({"op": "update_tracker", "clip": clip_id, "tracker": tracker["id"], "color": tracker["color"],
                        "visible": tracker["visible"], "show_path": tracker["show_path"]})
            ops.append({"op": "set_tracker_settings", "clip": clip_id, "tracker": tracker["id"], **tracker["settings"]})
    for clip_id, clip in clips.items():
        for link in (clip["tracking"] or {}).get("links", ()):
            ops.append({"op": "add_link", "clip": clip_id, "source": link["source_clip_id"] or clip_id, "trackers": link["tracker_ids"],
                        "at": link["reference_time"], "target": link["target"], "position": link["position"],
                        "rotation": link["rotation"], "scale": link["scale"], "id": link["id"],
                        **({"mask": link["mask_id"]} if link["mask_id"] else {})})
            if not link["enabled"]:
                ops.append({"op": "set_link_enabled", "clip": clip_id, "link": link["id"], "enabled": False})
        stab = (clip["tracking"] or {}).get("stabilization")
        if stab:
            ops.append({"op": "set_stabilization", "clip": clip_id, "trackers": stab["tracker_ids"], "enabled": stab["enabled"],
                        "mode": stab["mode"], "smoothing": stab["smoothing"], "borders": stab["borders"], "at": stab["reference_time"],
                        **({"smoothing_frames": stab["smoothing_frames"]} if stab["smoothing"] == "custom" else {})})
    return ops


def test_trackers_links_and_stabilization_are_rebuilt_from_the_description():
    original = _unlocked_copy(rich_project())
    wanted = describe(original)
    tracking = {cid: clip["tracking"] for cid, clip in _clips(wanted).items() if clip["tracking"]}
    assert sorted(tracking) == ["c1", "c3", "c5"]
    rebuilt = deepcopy(original)
    for track in rebuilt.tracks:
        for clip in track.clips:
            clip.tracking = None
    assert not any(clip["tracking"] for clip in _clips(describe(rebuilt)).values())
    _apply(rebuilt, _tracking_ops(wanted))
    again = {cid: clip["tracking"] for cid, clip in _clips(describe(rebuilt)).items() if clip["tracking"]}
    assert again == tracking                                  # trackers, réglages, points, liaisons (dont masque et ancrage), stabilisation
    assert tracking["c5"]["trackers"][0]["data"]["manual_points"][1]["timeline_time"] == 51.016667


def _tracked_project(rate: float, frames: list[int]):
    from core.project_model import MediaAsset, Project, Track
    from core.timeline_operations import add_clip_to_track

    project = Project("t", width=1920, height=1080, fps=rate, media_assets=[MediaAsset("m1", "/m/a.mp4", "a", 20.0, 1920, 1080, rate, "video")],
                      tracks=[Track("V1", "V1", "video")])
    add_clip_to_track(project, "m1", "V1", 3.0, clip_id="c1")
    first, *others = frames
    _apply(project, [{"op": "add_tracker", "clip": "c1", "at": 3.0 + (first + 0.5) / rate, "id": "t1", "x": 10.0, "y": 20.0}]
           + [{"op": "set_tracker_position", "clip": "c1", "tracker": "t1", "at": 3.0 + (index + 0.5) / rate, "x": 100.0 + index, "y": 50.0}
              for index in others])
    return project


@pytest.mark.parametrize("rate", [24.0, 29.97, 59.94, 120.0])
def test_the_frame_time_of_a_point_selects_the_same_frame_at_any_rate(rate):
    """``timeline_time`` vise le milieu de l'image : arrondi à 6 décimales, il retombe sur la même image. Le début de l'image, lui, tombait sur
    la précédente (le modèle arrondit vers l'image avec 1e-6 de tolérance, l'arrondi de la sortie vaut jusqu'à 5e-7 s × la cadence)."""
    frames = [0, 1, 7, 29, 30, 59, 61, 100, 299]
    original = _tracked_project(rate, frames)
    data = describe(original)["sequences"][0]["tracks"][0]["clips"][0]["tracking"]["trackers"][0]["data"]
    assert [p["index"] for p in data["manual_points"]] == sorted(frames)
    first, *others = data["manual_points"]
    rebuilt = _tracked_project(rate, [frames[0]])
    for clip in rebuilt.tracks[0].clips:
        clip.tracking = None
    _apply(rebuilt, [{"op": "add_tracker", "clip": "c1", "at": first["timeline_time"], "id": "t1", "x": first["x"], "y": first["y"]}]
           + [{"op": "set_tracker_position", "clip": "c1", "tracker": "t1", "at": p["timeline_time"], "x": p["x"], "y": p["y"]} for p in others])
    assert describe(rebuilt) == describe(original)


# --- Style des calques graphiques ----------------------------------------------------------------------------------------------


def test_the_graphic_fields_a_verb_knows_are_rebuilt_from_the_description():
    wanted = _clips(describe(rich_project()))["g1"]["graphic"]
    project = rich_project()
    clip = next(c for t in project.tracks for c in t.clips if c.id == "g1")
    from dataclasses import replace

    clip.graphic = replace(clip.graphic, text="x", fill_color="#000000", stroke_color="#FFFFFF", stroke_width=0, font_size=10, width=20, height=20)
    _apply(project, [{"op": "set_graphic", "clip": "g1", **{name: wanted[name] for name in _SET_GRAPHIC}}])
    after = _clips(describe(project))["g1"]["graphic"]
    assert {name: after[name] for name in _SET_GRAPHIC} == {name: wanted[name] for name in _SET_GRAPHIC}
    assert wanted["word_reveal"] == "karaoke" and wanted["highlight_words"] == [0, 2] and wanted["word_times"] == [0.0, 0.5, 1.25]
    assert wanted["glow_radius"] == 9.0 and wanted["shape"] == "rectangle" and wanted["layout"] == "anchor"


# --- Ce qu'aucun verbe n'écrit encore ------------------------------------------------------------------------------------------


def test_the_gaps_of_part_c_are_real_and_named():
    """Chaque manque est une décision écrite : le jour où un verbe l'écrit, ce test échoue et ``GAPS_C`` (et la documentation) se mettent à jour."""
    assert len(GAPS_C) == 4
    project = rich_project()
    style = [name for name in _clips(describe(project))["g1"]["graphic"] if name not in {*_SET_GRAPHIC, "type", "source_path", "visible", "locked",
                                                                                         "parent_id", "group_id", "z_order", "shape"}]
    assert "shadow_blur" in style and "glow_radius" in style and len(style) > 25
    for name in style:
        assert _refused(project, {"op": "set_graphic", "clip": "g1", name: 1}), name         # aucun champ de style hors de set_graphic
    assert _refused(project, {"op": "add_guide", "orientation": "vertical", "position": 0.1, "locked": True})
    assert not any("motion_blur" in n and n != "set_layer_motion_blur" for n in VERBS)
    assert not any("style" in n for n in VERBS)


# --- Média relié à une autre résolution, source coupée avant l'image de référence -----------------------------------------------


def _small_tracked(width: int = 100, height: int = 50):
    from core.project_model import MediaAsset, Project, Track
    from core.timeline_operations import add_clip_to_track

    project = Project("t", width=1920, height=1080, fps=30.0, media_assets=[MediaAsset("m1", "/m/a.mp4", "a", 20.0, width, height, 30.0, "video")],
                      tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video")])
    add_clip_to_track(project, "m1", "V1", 3.0, clip_id="c1")
    add_clip_to_track(project, "m1", "V2", 3.0, clip_id="c2")
    _apply(project, [
        {"op": "add_tracker", "clip": "c1", "at": 3.5, "x": 50.0, "y": 25.0, "id": "t1"},
        {"op": "set_tracker_position", "clip": "c1", "tracker": "t1", "at": 5.0, "x": 60.0, "y": 20.0},
    ])
    return project


def _points(data: dict) -> list[tuple]:
    tracker = _clips(data)["c1"]["tracking"]["trackers"][0]["data"]
    return [(p["index"], p["timeline_time"], p["x"], p["y"]) for p in tracker["manual_points"]]


def test_points_are_described_in_the_pixels_of_the_current_media_after_a_relink():
    """Un point est stocké dans la taille d'origine des données ; ``set_tracker_position`` lit des pixels du média courant. Le centre (50, 25) d'un
    100x50 est, après un relien en 300x200, (150, 100) : le rejouer tel quel ne le déplace pas."""
    project = _small_tracked()
    assert _points(describe(project))[0][2:] == (50.0, 25.0)
    project.media_assets[0].width, project.media_assets[0].height = 300, 200           # ce que fait relink_media (autre résolution, non carré)
    data = describe(project)
    tracker = _clips(data)["c1"]["tracking"]["trackers"][0]["data"]
    assert (tracker["source_width"], tracker["source_height"], tracker["points_width"], tracker["points_height"]) == (100, 50, 300, 200)
    assert [point[2:] for point in _points(data)] == [(150.0, 100.0), (180.0, 80.0)]
    replay = [{"op": "set_tracker_position", "clip": "c1", "tracker": "t1", "at": p[1], "x": p[2], "y": p[3]} for p in _points(data)]
    _apply(project, replay)
    assert describe(project) == data                                              # rejouer sur le projet lui-même ne change rien
    fresh = deepcopy(project)
    fresh.tracks[0].clips[0].tracking = None
    _apply(fresh, [{"op": "add_tracker", "clip": "c1", "at": _points(data)[0][1], "x": 150.0, "y": 100.0, "id": "t1"}] + replay[1:])
    assert _points(describe(fresh)) == _points(data)                              # et sur une copie sans le tracker (données à la taille courante)


def test_a_missing_media_does_not_raise_and_points_stay_as_stored():
    project = _small_tracked()
    project.media_assets.clear()
    tracker = _clips(describe(project))["c1"]["tracking"]["trackers"][0]["data"]
    assert tracker["points_width"] is None and tracker["manual_points"][0]["x"] == 50.0


def test_the_reference_of_a_link_is_resolved_through_the_cut_halves():
    project = _small_tracked(1920, 1080)
    _apply(project, [{"op": "add_link", "clip": "c2", "source": "c1", "trackers": ["t1"], "at": 5.0, "id": "lk1", "target": "transform"}])
    before = _clips(describe(project))["c2"]["tracking"]["links"][0]
    assert before["reference_index"] == 60 and before["reference_clip_id"] == "c1" and before["reference_time"] == 5.016667
    _apply(project, [{"op": "cut_clip", "clip": "c1", "at": 4.0}])                      # la source est coupée AVANT l'image de référence
    data = describe(project)
    link = _clips(data)["c2"]["tracking"]["links"][0]
    right = link["continuation_ids"][0]
    assert link["source_clip_id"] == "c1" and link["reference_index"] == 60
    assert link["reference_clip_id"] == right and link["reference_time"] == 5.016667      # la moitié droite montre encore l'image
    rebuilt = deepcopy(project)
    _apply(rebuilt, [{"op": "remove_link", "clip": "c2", "link": "lk1"}])
    _apply(rebuilt, [{"op": "add_link", "clip": "c2", "source": link["reference_clip_id"], "trackers": link["tracker_ids"],
                      "at": link["reference_time"], "target": link["target"], "id": "lk1"}])
    again = _clips(describe(rebuilt))["c2"]["tracking"]["links"][0]
    assert again["reference_index"] == 60 and again["reference_time"] == 5.016667 and again["reference_clip_id"] == right


def test_a_source_in_another_sequence_is_not_a_source_for_the_renderer_either():
    """Le moteur ne cherche les sources d'une liaison que dans la séquence rendue : la description fait pareil (``null``), sans lever."""
    project = _small_tracked(1920, 1080)
    _apply(project, [{"op": "add_link", "clip": "c2", "source": "c1", "trackers": ["t1"], "at": 5.0, "id": "lk1"}])
    from dataclasses import replace

    c2 = project.tracks[1].clips[0]
    link = replace(c2.tracking.links[0], source_clip_id="ailleurs")
    c2.tracking = replace(c2.tracking, links=(link,))
    described = _clips(describe(project))["c2"]["tracking"]["links"][0]
    assert described["reference_clip_id"] is None and described["reference_time"] is None
