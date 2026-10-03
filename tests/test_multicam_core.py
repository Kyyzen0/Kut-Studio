"""Multicam (cœur) : modèle, `.kut`, filtre de pistes dans le plan et l'évaluateur, opérations d'édition.

Un segment du montage est un clip imbriqué ordinaire portant ``angle_id`` ; ces tests vérifient que seul l'angle actif
est rendu, que la politique audio se traduit en couches, que couper / dupliquer / annuler gardent l'angle et que le
format de fichier reste compatible. Les rendus FFmpeg réels sont dans ``test_multicam_export.py``.
"""

from __future__ import annotations

import json

import pytest

from core.edit_history import ProjectHistory
from core.filter_graph import fingerprint_plan
from core.multicam import (
    NO_FILTER,
    angle_offset,
    multicam_issues,
    resolve_angle,
    track_filter_for,
)
from core.multicam_model import (
    AudioMode,
    MulticamAudio,
    SyncMethod,
    SyncStatus,
    multicam_from_dict,
    multicam_to_dict,
)
from core.multicam_ops import (
    AngleSpec,
    MulticamError,
    SyncOutcome,
    add_angle,
    angle_has_audio,
    angle_usages,
    apply_sync,
    create_multicam_from_clips,
    create_multicam_source,
    flatten_multicam_clip,
    insert_multicam_clip,
    multicam_segment_at,
    normalize_offsets,
    remove_angle,
    rename_angle,
    replace_angle,
    set_angle_color,
    set_angle_offset,
    set_audio_policy,
    suggest_audio_policy,
    switch_angle,
)
from core.preview_segments import segment_plan
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.sequences import duplicate_sequence
from core.timeline_evaluator import evaluate_timeline
from core.timeline_index import build_timeline_index
from core.timeline_operations import cut_clip, duplicate_clip
from core.timeline_view_model import build_clip_views


def _video(asset_id: str, *, has_audio: bool = True, duration: float = 60.0) -> MediaAsset:
    return MediaAsset(asset_id, f"/media/{asset_id}.mp4", asset_id, duration, 1920, 1080, 30.0, "video", has_audio)


def _audio(asset_id: str, duration: float = 70.0) -> MediaAsset:
    return MediaAsset(asset_id, f"/media/{asset_id}.wav", asset_id, duration, 0, 0, 0.0, "audio", True)


def _project() -> tuple[Project, Sequence]:
    """Montage vide + source à quatre angles : A (0 s), B (+2 s), C (+5 s, sans son), enregistreur (+0,5 s)."""
    project = Project(
        "Concert",
        media_assets=[
            _video("camA"), _video("camB"), _video("camC", has_audio=False), _audio("rec"),
        ],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio")],
    )
    source = create_multicam_source(
        project,
        [
            AngleSpec(asset_id="camA", offset=0.0, name="Wide"),
            AngleSpec(asset_id="camB", offset=2.0, name="Close-up"),
            AngleSpec(asset_id="camC", offset=5.0, name="Drone"),
            AngleSpec(asset_id="rec", offset=0.5, name="Recorder"),
        ],
        name="Concert",
    )
    return project, source


def _with_segment(angle_id: str = "angle-1") -> tuple[Project, Sequence, Clip]:
    project, source = _project()
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id=angle_id)
    return project, source, segment


def _sub_plan(plan, index: int = 0):
    return plan.nested_sequences[index].plan


def _asset_ids(plan) -> set[str]:
    return {layer.asset_id for layer in plan.video_layers}, {layer.asset_id for layer in plan.audio_layers}


# --- modèle et création --------------------------------------------------------------------------------------------


def test_creation_builds_one_track_per_angle_with_normalised_offsets():
    project, source = _project()
    assert source.multicam is not None
    assert [angle.name for angle in source.multicam.angles] == ["Wide", "Close-up", "Drone", "Recorder"]
    assert [track.type for track in source.tracks] == ["video", "video", "video", "audio"]
    offsets = [angle_offset(source, angle) for angle in source.multicam.angles]
    assert offsets == [0.0, 2.0, 5.0, 0.5]
    assert (source.width, source.height, source.fps) == (project.width, project.height, project.fps)
    # une source Multicam est une séquence du projet, listée comme les autres
    assert source in project.sequences


def test_offsets_are_normalised_so_the_earliest_angle_starts_at_zero():
    assert normalize_offsets({"a": 12.0, "b": 14.5, "c": 11.25}) == {"a": 0.75, "b": 3.25, "c": 0.0}
    project = Project("p", media_assets=[_video("a"), _video("b")], tracks=[Track("V1", "V1", "video")])
    source = create_multicam_source(
        project, [AngleSpec(asset_id="a", offset=100.0), AngleSpec(asset_id="b", offset=103.0)]
    )
    assert source.multicam is not None
    assert [angle_offset(source, angle) for angle in source.multicam.angles] == [0.0, 3.0]


def test_angle_names_are_unique_and_default_to_the_media_name():
    project = Project("p", media_assets=[_video("a"), _video("b")], tracks=[Track("V1", "V1", "video")])
    source = create_multicam_source(project, [AngleSpec(asset_id="a", name="Cam"), AngleSpec(asset_id="b", name="Cam")])
    assert source.multicam is not None
    assert [angle.name for angle in source.multicam.angles] == ["Cam", "Cam 2"]


def test_an_image_or_an_unknown_media_cannot_become_an_angle():
    project = Project("p", media_assets=[MediaAsset("img", "/x.png", "x", 5.0, 100, 100, 25.0, "image")],
                      tracks=[Track("V1", "V1", "video")])
    with pytest.raises(MulticamError):
        create_multicam_source(project, [AngleSpec(asset_id="img")])
    with pytest.raises(KeyError):
        create_multicam_source(project, [AngleSpec(asset_id="nope")])
    assert len(project.sequences) == 1  # rien n'est ajouté en cas de refus
    with pytest.raises(MulticamError):
        create_multicam_source(project, [])


def test_audio_policy_is_proposed_without_asking_only_when_unambiguous():
    project, source = _project()
    assert source.multicam is not None
    # enregistreur unique, synchronisation non mesurée (positions données) : son fixe, sans question
    assert source.multicam.audio == MulticamAudio(AudioMode.FIXED, ("angle-4",))
    angles = source.multicam.angles
    angles[3].sync_status = SyncStatus.UNCERTAIN
    suggestion = suggest_audio_policy(angles, ["angle-4"])
    assert suggestion.needs_choice and suggestion.mode is AudioMode.FOLLOW_VIDEO
    angles[3].sync_status = SyncStatus.EXCELLENT
    assert not suggest_audio_policy(angles, ["angle-4"]).needs_choice
    assert suggest_audio_policy(angles, []).reason == "single_source"
    assert suggest_audio_policy(angles, ["angle-3", "angle-4"]).needs_choice


# --- format .kut ----------------------------------------------------------------------------------------------------


def test_multicam_round_trips_through_the_kut_file(tmp_path):
    project, source, segment = _with_segment("angle-2")
    source.multicam.angles[1].sync_method = SyncMethod.AUDIO  # type: ignore[union-attr]
    source.multicam.angles[1].sync_status = SyncStatus.GOOD  # type: ignore[union-attr]
    source.multicam.angles[1].sync_confidence = 0.73  # type: ignore[union-attr]
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert loaded == project
    loaded_source = loaded.get_sequence(source.id)
    assert loaded_source is not None and loaded_source.multicam is not None
    assert loaded_source.multicam.angles[1].sync_status is SyncStatus.GOOD
    assert loaded_source.multicam.angles[1].sync_confidence == pytest.approx(0.73)
    clip = next(c for t in loaded.tracks for c in t.clips if c.id == segment.id)
    assert clip.angle_id == "angle-2"
    # une seconde écriture est identique : rien de recalculable ni d'instable n'est stocké
    save_project(loaded, str(tmp_path / "q.kut"))
    assert json.loads((tmp_path / "p.kut").read_text()) == json.loads((tmp_path / "q.kut").read_text())


def test_an_old_file_without_multicam_keys_loads_as_ordinary_sequences(tmp_path):
    project, _source, _segment = _with_segment()
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    data = json.loads(path.read_text())
    for sequence in data["project"]["sequences"]:
        sequence.pop("multicam", None)
        for track in sequence["tracks"]:
            for clip in track["clips"]:
                clip.pop("angle_id", None)
    path.write_text(json.dumps(data))
    loaded = load_project(str(path))
    assert all(sequence.multicam is None for sequence in loaded.sequences)
    assert all(clip.angle_id == "" for track in loaded.tracks for clip in track.clips)


def test_multicam_keys_are_only_written_when_used(tmp_path):
    project = Project("p", tracks=[Track("V1", "V1", "video")])
    path = tmp_path / "plain.kut"
    save_project(project, str(path))
    text = path.read_text()
    assert "multicam" not in text and "angle_id" not in text


@pytest.mark.parametrize(
    "bad",
    [[], "x", {"angles": "x"}, {"angles": [1]}, {"angles": [{"id": "", "track_id": "V1"}]},
     {"angles": [{"id": "a", "track_id": ""}]},
     {"angles": [{"id": "a", "track_id": "V1"}, {"id": "a", "track_id": "V2"}]}],
)
def test_a_corrupt_multicam_block_is_refused_not_half_loaded(bad):
    with pytest.raises(ValueError):
        multicam_from_dict(bad)


def test_unknown_enum_values_fall_back_instead_of_refusing_a_newer_file():
    source = multicam_from_dict({
        "angles": [{"id": "a", "name": "A", "track_id": "V1", "sync_method": "lidar", "sync_status": "wow",
                    "sync_confidence": "NaN"}],
        "audio": {"mode": "surround", "angle_ids": ["a"]},
    })
    assert source is not None
    angle = source.angles[0]
    assert angle.sync_method is None and angle.sync_status is SyncStatus.NONE and angle.sync_confidence is None
    assert source.audio.mode is AudioMode.FOLLOW_VIDEO
    assert multicam_to_dict(source)["angles"][0]["id"] == "a"


# --- RenderPlan : seul l'angle actif est rendu ----------------------------------------------------------------------


def test_the_plan_renders_only_the_active_angle_and_its_sound_when_audio_follows_video():
    project, source, _segment = _with_segment("angle-2")
    assert source.multicam is not None
    set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO)
    plan = build_render_plan(project)
    assert len(plan.nested_sequences) == 1
    videos, audios = _asset_ids(_sub_plan(plan))
    assert videos == {"camB"} and audios == {"camB"}


def test_a_fixed_audio_source_keeps_playing_when_the_video_angle_changes():
    project, source, _segment = _with_segment("angle-2")
    set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-4"])
    videos, audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camB"} and audios == {"rec"}


def test_a_mixed_audio_policy_mixes_the_chosen_sources():
    project, source, _segment = _with_segment("angle-1")
    set_audio_policy(project, source.id, AudioMode.MIX, ["angle-1", "angle-4"])
    videos, audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camA"} and audios == {"camA", "rec"}


def test_a_video_angle_without_sound_is_silent_when_audio_follows_it():
    project, source, _segment = _with_segment("angle-3")
    set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO)
    videos, audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camC"} and audios == set()


def test_two_segments_of_the_same_angle_share_one_sub_plan_and_two_angles_get_two():
    project, source, first = _with_segment("angle-1")
    second = insert_multicam_clip(project, source.id, "V1", 20.0, angle_id="angle-1")
    third = insert_multicam_clip(project, source.id, "V2", 0.0, angle_id="angle-2")
    plan = build_render_plan(project)
    keys = {layer.clip_id: layer.nested_key for layer in plan.video_layers}
    assert keys[first.id] == keys[second.id] != keys[third.id]
    assert len(plan.nested_sequences) == 2


def test_a_segment_without_angle_shows_the_first_angle_and_an_unknown_angle_renders_empty():
    project, source, segment = _with_segment("angle-1")
    segment.angle_id = ""
    videos, _audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camA"}
    segment.angle_id = "angle-99"
    videos, audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == set() and audios == set()
    issues = multicam_issues(project)
    assert [issue.code for issue in issues] == ["unknown_angle"]
    assert issues[0].clip_id == segment.id


def test_an_ordinary_nested_sequence_is_untouched_by_the_filter():
    project, source, _segment = _with_segment()
    source.multicam = None
    clip = next(c for t in project.tracks for c in t.clips)
    assert track_filter_for(source, clip) is NO_FILTER
    videos, audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camA", "camB", "camC"} and audios == {"camA", "camB", "rec"}


def test_non_angle_tracks_of_the_source_are_always_rendered():
    project, source, _segment = _with_segment("angle-2")
    source.tracks.append(Track("G1", "G1", "graphics"))
    source.tracks.append(Track("V9", "Logo", "video", clips=[Clip("logo", "camC", "V9", 0.0, 0.0, 10.0)]))
    videos, _audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camB", "camC"}


def test_a_solo_on_a_hidden_angle_does_not_blank_the_active_one():
    project, source, _segment = _with_segment("angle-2")
    source.tracks[0].solo = True  # piste de l'angle 1, masquée par le filtre
    videos, _audios = _asset_ids(_sub_plan(build_render_plan(project)))
    assert videos == {"camB"}


def test_a_hidden_angle_with_missing_media_does_not_make_the_plan_fail():
    project, source, _segment = _with_segment("angle-1")
    project.media_assets = [a for a in project.media_assets if a.id != "camB"]
    plan = build_render_plan(project)
    assert plan.missing_media == ()  # l'angle hors ligne n'est pas utilisé
    replace_angle(project, _segment.id, "angle-2")
    assert build_render_plan(project).missing_media == ("camB",)


def test_the_segment_plan_for_the_preview_applies_the_same_filter():
    project, source, segment = _with_segment("angle-2")
    set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-4"])
    plan = segment_plan(project, 1.0, 3.0)
    videos, audios = _asset_ids(_sub_plan(plan))
    assert videos == {"camB"} and audios == {"rec"}


def test_the_fingerprint_changes_with_the_angle_and_the_audio_policy_but_not_without():
    project, source, segment = _with_segment("angle-1")
    base = fingerprint_plan(build_render_plan(project))
    assert fingerprint_plan(build_render_plan(project)) == base
    segment.angle_id = "angle-2"
    other_angle = fingerprint_plan(build_render_plan(project))
    assert other_angle != base
    set_audio_policy(project, source.id, AudioMode.MIX, ["angle-1", "angle-2"])
    assert fingerprint_plan(build_render_plan(project)) != other_angle


# --- évaluateur et index de lecture -----------------------------------------------------------------------------------


def test_the_realtime_evaluator_resolves_only_the_active_angle():
    project, source, _segment = _with_segment("angle-2")
    set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO)
    entries = evaluate_timeline(project, 10.0)
    video = [e for e in entries if e.track_type == "video"]
    assert [e.source_path for e in video] == ["/media/camB.mp4"]
    assert not video[0].silent
    # l'index de lecture donne la même réponse (c'est lui que le moniteur interroge)
    indexed = build_timeline_index(project).active_at(project, 10.0)
    assert [e.source_path for e in indexed if e.track_type == "video"] == ["/media/camB.mp4"]


def test_the_active_video_is_marked_silent_when_another_source_provides_the_sound():
    project, source, _segment = _with_segment("angle-2")
    set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-4"])
    entries = evaluate_timeline(project, 10.0)
    video = next(e for e in entries if e.track_type == "video")
    audio = [e for e in entries if e.track_type == "audio"]
    assert video.silent and [e.source_path for e in audio] == ["/media/rec.wav"]


def test_the_angle_start_offset_maps_into_the_source_time():
    project, source, _segment = _with_segment("angle-2")
    # Angle B commence à 2 s dans la source : à 10 s du montage, le média est lu à 8 s.
    video = next(e for e in evaluate_timeline(project, 10.0) if e.track_type == "video")
    assert video.source_time == pytest.approx(8.0)


# --- Opérations : bascule, remplacement, réglages ----------------------------------------------------------------------


def test_switching_at_the_playhead_cuts_the_segment_and_changes_only_the_right_half():
    project, source, segment = _with_segment("angle-1")
    result = switch_angle(project, 10.0, "angle-3")
    assert result is not None and result.cut and result.previous_angle_id == "angle-1"
    clips = sorted((c for t in project.tracks for c in t.clips), key=lambda c: c.timeline_start)
    assert [(c.angle_id, round(c.timeline_start, 3)) for c in clips] == [("angle-1", 0.0), ("angle-3", 10.0)]
    assert clips[0].id == segment.id
    # le temps source est continu à la coupe : on ne perd ni ne répète aucune image
    assert clips[0].source_out == pytest.approx(clips[1].source_in)


def test_switching_to_the_angle_already_shown_creates_no_cut():
    project, _source, _segment = _with_segment("angle-2")
    assert switch_angle(project, 10.0, "angle-2") is None
    assert sum(len(t.clips) for t in project.tracks) == 1


def test_switching_exactly_at_the_start_of_a_segment_replaces_without_cutting():
    project, _source, segment = _with_segment("angle-1")
    result = switch_angle(project, 0.0, "angle-2")
    assert result is not None and not result.cut
    assert segment.angle_id == "angle-2" and sum(len(t.clips) for t in project.tracks) == 1


def test_a_live_switching_session_builds_the_edit():
    project, _source, _segment = _with_segment("angle-1")
    for time, angle in [(3.0, "angle-2"), (6.5, "angle-3"), (9.0, "angle-1"), (12.0, "angle-2")]:
        assert switch_angle(project, time, angle) is not None
    clips = sorted((c for t in project.tracks for c in t.clips), key=lambda c: c.timeline_start)
    assert [c.angle_id for c in clips] == ["angle-1", "angle-2", "angle-3", "angle-1", "angle-2"]
    assert [round(c.timeline_start, 2) for c in clips] == [0.0, 3.0, 6.5, 9.0, 12.0]


def test_switching_refuses_without_a_segment_with_an_unknown_angle_or_at_the_very_end():
    project, source, segment = _with_segment("angle-1")
    with pytest.raises(MulticamError):
        switch_angle(project, 100.0, "angle-2")  # hors de tout segment
    with pytest.raises(MulticamError):
        switch_angle(project, 10.0, "angle-99")
    end = segment.timeline_start + segment.duration - 0.001
    with pytest.raises(MulticamError):
        switch_angle(project, end, "angle-2")
    assert sum(len(t.clips) for t in project.tracks) == 1  # aucun refus ne modifie le montage


def test_the_topmost_segment_wins_and_a_track_or_clip_can_be_targeted():
    project, source, low = _with_segment("angle-1")
    top = insert_multicam_clip(project, source.id, "V2", 0.0, angle_id="angle-2")
    assert multicam_segment_at(project, 5.0) is top
    assert multicam_segment_at(project, 5.0, track_id="V1") is low
    assert multicam_segment_at(project, 5.0, clip_id=low.id) is low
    assert multicam_segment_at(project, 500.0) is None


def test_replace_angle_changes_a_segment_without_any_cut():
    project, source, segment = _with_segment("angle-1")
    switch_angle(project, 10.0, "angle-2")
    clips = sorted((c for t in project.tracks for c in t.clips), key=lambda c: c.timeline_start)
    replace_angle(project, clips[1].id, "angle-3")
    assert [c.angle_id for c in sorted((c for t in project.tracks for c in t.clips),
                                       key=lambda c: c.timeline_start)] == ["angle-1", "angle-3"]
    with pytest.raises(MulticamError):
        replace_angle(project, segment.id, "angle-99")


def test_cutting_and_duplicating_a_segment_with_ordinary_tools_keeps_its_angle():
    project, _source, segment = _with_segment("angle-3")
    left, right = cut_clip(project, segment.id, 10.0)
    assert left.angle_id == right.angle_id == "angle-3"
    copy = duplicate_clip(project, right.id, "V2", 30.0) if _accepts_copy_args() else None
    if copy is not None:
        assert copy.angle_id == "angle-3"


def _accepts_copy_args() -> bool:
    import inspect

    return len(inspect.signature(duplicate_clip).parameters) >= 4


def test_trimming_a_segment_keeps_its_angle_and_never_moves_the_source_offsets():
    project, source, segment = _with_segment("angle-2")
    before = [angle_offset(source, angle) for angle in source.multicam.angles]  # type: ignore[union-attr]
    from core.timeline_operations import trim_clip_left, trim_clip_right

    trim_clip_left(project, segment.id, 4.0)
    trim_clip_right(project, segment.id, 20.0)
    assert segment.angle_id == "angle-2"
    assert [angle_offset(source, angle) for angle in source.multicam.angles] == before  # type: ignore[union-attr]


def test_manual_offset_moves_the_clips_of_the_angle_and_marks_it_manual():
    project, source, _segment = _with_segment()
    angle = source.multicam.angles[1]  # type: ignore[union-attr]
    delta = set_angle_offset(project, source.id, angle.id, 3.25)
    assert delta == pytest.approx(1.25) and angle_offset(source, angle) == pytest.approx(3.25)
    assert angle.sync_status is SyncStatus.MANUAL and angle.sync_method is SyncMethod.MANUAL
    with pytest.raises(MulticamError):
        set_angle_offset(project, source.id, angle.id, -1.0)


def test_apply_sync_places_angles_and_never_claims_a_failed_measurement_is_good():
    project, source, _segment = _with_segment()
    applied = apply_sync(project, source.id, {
        "angle-1": SyncOutcome(10.0, SyncMethod.AUDIO, SyncStatus.EXCELLENT, 0.95),
        "angle-2": SyncOutcome(13.5, SyncMethod.AUDIO, SyncStatus.GOOD, 0.7),
        "angle-3": SyncOutcome(None, SyncMethod.AUDIO, SyncStatus.FAILED, 0.0),
    })
    angles = {angle.id: angle for angle in source.multicam.angles}  # type: ignore[union-attr]
    assert set(applied) == {"angle-1", "angle-2"}
    assert angle_offset(source, angles["angle-1"]) == 0.0 and angle_offset(source, angles["angle-2"]) == 3.5
    assert angle_offset(source, angles["angle-3"]) == 5.0  # resté en place
    assert angles["angle-3"].sync_status is SyncStatus.FAILED
    assert source.multicam.sync_method is SyncMethod.AUDIO  # type: ignore[union-attr]


def test_audio_policy_rules_and_renames():
    project, source, _segment = _with_segment()
    with pytest.raises(MulticamError):
        set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-1", "angle-2"])
    with pytest.raises(MulticamError):
        set_audio_policy(project, source.id, AudioMode.MIX, [])
    with pytest.raises(MulticamError):
        set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-99"])
    policy = set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO, ["angle-1"])
    assert policy == MulticamAudio(AudioMode.FOLLOW_VIDEO, ())
    rename_angle(project, source.id, "angle-2", "  Plan serré ")
    assert source.multicam.angles[1].name == "Plan serré" and source.tracks[1].name == "Plan serré"  # type: ignore[union-attr]
    with pytest.raises(MulticamError):
        rename_angle(project, source.id, "angle-2", "   ")
    set_angle_color(project, source.id, "angle-2", 7)
    assert source.multicam.angles[1].color_index == 7  # type: ignore[union-attr]


def test_adding_and_removing_angles_keeps_segments_and_audio_policy_consistent():
    project, source, segment = _with_segment("angle-2")
    project.media_assets.append(_video("camD"))
    new = add_angle(project, source.id, AngleSpec(asset_id="camD", offset=1.0, name="Handheld"))
    assert new.id == "angle-5" and [t.id for t in source.tracks][-1] == "V4"
    assert angle_offset(source, new) == 1.0
    with pytest.raises(MulticamError):
        remove_angle(project, source.id, "angle-2")  # un segment la montre encore
    assert angle_usages(project, source.id, "angle-2") == [segment]
    redirected = remove_angle(project, source.id, "angle-2", replace_with="angle-1")
    assert redirected == 1 and segment.angle_id == "angle-1"
    assert "angle-2" not in {angle.id for angle in source.multicam.angles}  # type: ignore[union-attr]
    assert "V2" not in {track.id for track in source.tracks}
    # retirer l'enregistreur désigné par un son fixe ramène la politique au son de l'image
    remove_angle(project, source.id, "angle-4")
    assert source.multicam.audio == MulticamAudio(AudioMode.FOLLOW_VIDEO, ())  # type: ignore[union-attr]
    assert multicam_issues(project) == []


def test_the_last_angle_cannot_be_removed():
    project = Project("p", media_assets=[_video("a")], tracks=[Track("V1", "V1", "video")])
    source = create_multicam_source(project, [AngleSpec(asset_id="a")])
    with pytest.raises(MulticamError):
        remove_angle(project, source.id, "angle-1")


def test_resolve_angle_defaults_to_the_first_and_reports_unknown_ones():
    _project_, source = _project()
    assert source.multicam is not None
    assert resolve_angle(source.multicam, "").id == "angle-1"  # type: ignore[union-attr]
    assert resolve_angle(source.multicam, "angle-3").name == "Drone"  # type: ignore[union-attr]
    assert resolve_angle(source.multicam, "zzz") is None


# --- création depuis la timeline, aplatir, dupliquer, historique -----------------------------------------------------------


def _timeline_project() -> Project:
    project = Project(
        "t", media_assets=[_video("camA"), _video("camB"), _audio("rec")],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio")],
    )
    project.tracks[0].clips.append(Clip("a", "camA", "V1", 10.0, 0.0, 20.0, label="Cam A"))
    project.tracks[1].clips.append(Clip("b", "camB", "V2", 12.0, 5.0, 25.0, label="Cam B"))
    project.tracks[2].clips.append(Clip("r", "rec", "A1", 9.5, 0.0, 30.0, label="Recorder"))
    return project


def test_creating_from_timeline_clips_keeps_their_current_positions_and_replaces_them_by_one_segment():
    project = _timeline_project()
    source, segment = create_multicam_from_clips(project, ["a", "b", "r"], name="Interview")
    assert source.multicam is not None
    assert [angle.name for angle in source.multicam.angles] == ["Cam A", "Cam B", "Recorder"]
    assert [angle_offset(source, angle) for angle in source.multicam.angles] == [0.5, 2.5, 0.0]
    assert all(angle.sync_method is SyncMethod.POSITIONS for angle in source.multicam.angles)
    remaining = [clip for track in project.tracks for clip in track.clips]
    assert remaining == [segment] and segment.timeline_start == 9.5 and segment.track_id == "V1"
    assert build_render_plan(project).nested_sequences  # il se rend comme n'importe quel clip imbriqué


def test_creating_from_the_timeline_is_atomic_when_a_track_is_locked():
    project = _timeline_project()
    project.tracks[1].locked = True
    with pytest.raises(MulticamError):
        create_multicam_from_clips(project, ["a", "b"])
    assert [len(t.clips) for t in project.tracks] == [1, 1, 1] and len(project.sequences) == 1
    with pytest.raises(MulticamError):
        create_multicam_from_clips(project, ["a"])


def test_a_multicam_segment_cannot_become_an_angle_of_another_source():
    project, source, segment = _with_segment()
    project.tracks[0].clips.append(Clip("x", "camA", "V1", 80.0, 0.0, 5.0))
    with pytest.raises(MulticamError):
        create_multicam_from_clips(project, [segment.id, "x"])


def test_flattening_replaces_a_segment_by_ordinary_clips_that_render_the_same_media():
    project, source, _segment = _with_segment("angle-1")
    set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO)
    switch_angle(project, 10.0, "angle-2")
    clips = sorted((c for t in project.tracks for c in t.clips), key=lambda c: c.timeline_start)
    result = flatten_multicam_clip(project, clips[1].id)
    assert [c.asset_id for c in result.video] == ["camB"]
    new = result.video[0]
    # camB commence à 2 s dans la source : le segment démarre à 10 s, donc le média est lu à partir de 8 s
    assert new.timeline_start == pytest.approx(10.0) and new.source_in == pytest.approx(8.0)
    assert not new.is_nested and not new.angle_id
    plan = build_render_plan(project)
    assert {layer.asset_id for layer in plan.video_layers if layer.asset_id} == {"camB"}


def test_flattening_with_a_fixed_recorder_adds_an_audio_track_and_silences_the_camera():
    project, source, segment = _with_segment("angle-2")  # politique créée : son fixe de l'enregistreur
    result = flatten_multicam_clip(project, segment.id)
    assert [c.asset_id for c in result.audio] == ["rec"]
    assert result.video[0].gain_db < -59.0
    audio_track = next(t for t in project.tracks if t.name == "Multicam audio")
    assert audio_track.clips == result.audio


def test_flattening_refuses_what_it_cannot_merge_without_changing_the_picture():
    project, _source, segment = _with_segment("angle-2")
    from dataclasses import replace

    segment.transform = replace(segment.transform, scale=1.2)
    with pytest.raises(MulticamError):
        flatten_multicam_clip(project, segment.id)
    assert segment in project.tracks[0].clips  # rien n'a changé
    plain = Project("p", media_assets=[_video("a")], tracks=[Track("V1", "V1", "video", clips=[Clip("c", "a", "V1", 0, 0, 5)])])
    with pytest.raises(MulticamError):
        flatten_multicam_clip(plain, "c")


def test_duplicating_the_source_is_independent_and_instances_keep_pointing_to_the_original():
    project, source, segment = _with_segment("angle-2")
    copy = duplicate_sequence(project, source.id)
    assert copy.id != source.id and copy.multicam == source.multicam
    rename_angle(project, copy.id, "angle-1", "Autre")
    assert source.multicam.angles[0].name == "Wide"  # type: ignore[union-attr]
    assert segment.sequence_id == source.id


def test_every_multicam_edit_is_undoable_with_the_project_history():
    project, source, segment = _with_segment("angle-1")
    history = ProjectHistory()
    history.reset(project)
    steps = [
        ("switch", lambda: switch_angle(project, 10.0, "angle-2")),
        ("policy", lambda: set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-4"])),
        ("offset", lambda: set_angle_offset(project, source.id, "angle-2", 4.0)),
        ("rename", lambda: rename_angle(project, source.id, "angle-3", "Grue")),
    ]
    snapshots = []
    for label, action in steps:
        snapshots.append(_snapshot(project))
        action()
        history.record(project, label)
    for before in reversed(snapshots):
        restored = history.undo()
        assert restored is not None and _snapshot(restored) == before
    for _label, _action in steps:
        assert history.redo() is not None


def _snapshot(project: Project):
    from copy import deepcopy

    return deepcopy(project.sequences)


def test_the_timeline_view_exposes_the_angle_for_each_segment_and_flags_unknown_ones():
    project, source, segment = _with_segment("angle-3")
    view = next(v for v in build_clip_views(project) if v.id == segment.id)
    assert view.is_multicam and view.angle_index == 2 and view.angle_name == "Drone"
    assert view.angle_count == 4 and view.angle_color_index == 2
    segment.angle_id = "angle-99"
    view = next(v for v in build_clip_views(project) if v.id == segment.id)
    assert view.nested_status == "angle_missing"
    plain = Project("p", tracks=[Track("V1", "V1", "video")])
    assert not any(v.is_multicam for v in build_clip_views(plain))


def test_a_segment_clipped_by_a_shorter_source_still_resolves_its_angle():
    project, source, segment = _with_segment("angle-2")
    cut_clip(project, segment.id, 30.0)
    plan = build_render_plan(project)
    assert len(plan.video_layers) == 2 and len(plan.nested_sequences) == 1


# --- imbrication, marqueurs, suivi, effets par angle ------------------------------------------------------------------------


def test_a_multicam_source_cannot_contain_another_multicam_source(tmp_path):
    from core.sequences import SequenceError, insert_sequence_clip

    project, first = _project()
    other = create_multicam_source(project, [AngleSpec(asset_id="camA"), AngleSpec(asset_id="camB")], name="Autre")
    project.active_sequence_id = first.id
    with pytest.raises(MulticamError):
        insert_multicam_clip(project, other.id, "V1", 0.0)                 # depuis les opérations Multicam…
    with pytest.raises(SequenceError):
        insert_sequence_clip(project, other.id, "V1", 0.0)                 # …comme depuis un glisser-déposer de séquence
    assert not any(clip.sequence_id for track in first.tracks for clip in track.clips)
    assert len(project.sequences) == 3


def test_a_multicam_segment_can_live_inside_an_ordinary_nested_sequence_and_still_renders_only_its_angle():
    from core.sequences import create_sequence_from_selection

    project, source, segment = _with_segment("angle-3")
    nested = create_sequence_from_selection(project, [segment.id], "Scène").clip
    assert nested.sequence_id and not nested.angle_id          # la séquence imbriquée est ordinaire
    plan = build_render_plan(project)
    inner = [entry for entry in plan.nested_sequences if entry.sequence_id == source.id]
    assert len(inner) == 1
    assert {layer.asset_id for layer in inner[0].plan.video_layers} == {"camC"}
    assert len(plan.nested_sequences) == 2                      # la source Multicam, puis la séquence qui la contient
    assert multicam_issues(project) == []


def test_markers_of_the_source_and_of_the_montage_never_mix():
    project, source, _segment = _with_segment("angle-1")
    from core.project_model import Marker

    source.markers.append(Marker("m-src", 12.0, "dans la source"))
    project.markers.append(Marker("m-main", 3.0, "dans le montage"))
    assert [m.id for m in project.markers] == ["m-main"]
    assert [m.id for m in source.markers] == ["m-src"]
    assert {m.id for m in duplicate_sequence(project, source.id).markers} != {"m-src"}   # une copie reçoit de nouveaux repères


def test_an_effect_on_an_angle_clip_applies_to_every_appearance_without_being_copied_per_cut():
    from core.effects_model import create_effect

    project, source, segment = _with_segment("angle-2")
    switch_angle(project, 10.0, "angle-1")
    switch_angle(project, 20.0, "angle-2")
    angle_clip = source.tracks[1].clips[0]
    angle_clip.effects = [create_effect("blur")]
    plan = build_render_plan(project)
    blur_layers = [
        layer for entry in plan.nested_sequences for layer in entry.plan.video_layers if layer.asset_id == "camB" and layer.effects
    ]
    assert len(blur_layers) == 1                          # un seul sous-plan pour l'angle B, deux apparitions dans le montage
    assert all(not layer.effects for layer in plan.video_layers)                        # rien n'est recopié sur les segments
    assert len(plan.video_layers) == 3 and len({layer.nested_key for layer in plan.video_layers}) == 2


def test_stabilization_on_an_angle_clip_survives_a_synchronisation_offset():
    """Les données de suivi sont en temps source : décaler l'angle dans la source ne les déplace pas."""
    from core import tracking_ops as tracking
    from core.tracking_model import Sample, SampleStatus
    from dataclasses import replace

    project, source, _segment = _with_segment("angle-2")
    project.active_sequence_id = source.id
    angle_clip = source.tracks[1].clips[0]
    tracker = tracking.add_tracker(project, angle_clip.id, timeline_time=angle_clip.timeline_start + 1.0, x=500.0, y=300.0)
    samples = {i: Sample(500.0 + 2 * i, 300.0 + i, 0.9, SampleStatus.TRACKED) for i in range(1, 40)}
    clip, _track = tracking.find_clip_and_track(project, angle_clip.id)
    tracking._put_tracker(clip, replace(tracker, data=tracker.data.with_samples(samples)))
    tracking.set_stabilization(project, angle_clip.id, enabled=True, tracker_ids=(tracker.id,))
    project.active_sequence_id = "seq-main"
    before = build_render_plan(project)
    layer_before = next(layer for entry in before.nested_sequences for layer in entry.plan.video_layers if layer.asset_id == "camB")
    set_angle_offset(project, source.id, "angle-2", 6.0)                       # on décale l'angle de 4 s
    after = build_render_plan(project)
    layer_after = next(layer for entry in after.nested_sequences for layer in entry.plan.video_layers if layer.asset_id == "camB")
    assert layer_before.transform_keyframes and layer_after.transform_keyframes
    assert len(layer_before.transform_keyframes) == len(layer_after.transform_keyframes)
    assert layer_after.timeline_start == pytest.approx(layer_before.timeline_start + 4.0)
    # Les images-clés sont en temps **local du clip** : décaler le clip dans la source ne les touche pas, c'est le clip qui bouge.
    assert list(layer_after.transform_keyframes) == list(layer_before.transform_keyframes)


def test_a_camera_whose_sound_is_kept_but_whose_picture_is_hidden_stays_audible_in_the_realtime_evaluation():
    project, source, _segment = _with_segment("angle-2")
    set_audio_policy(project, source.id, AudioMode.MIX, ["angle-1", "angle-4"])
    entries = evaluate_timeline(project, 10.0)
    video = [e for e in entries if e.track_type == "video"]
    audio = sorted(e.source_path for e in entries if e.track_type == "audio")
    assert [e.source_path for e in video] == ["/media/camB.mp4"] and video[0].silent       # l'image de B, sans son
    assert audio == ["/media/camA.mp4", "/media/rec.wav"]                                    # le son de A (image masquée) et l'enregistreur
    indexed = build_timeline_index(project).active_at(project, 10.0)
    assert sorted(e.source_path for e in indexed if e.track_type == "audio") == audio


# --- relecture de la PR : repères, minuit, aplatissement, sources muettes -----------------------------------------------


def test_a_clip_whose_sync_failed_keeps_its_gap_to_a_measured_clip_not_its_absolute_position():
    """Les décalages mesurés ont leur origine (le plus petit vaut 0), pas celle de la timeline : le clip dont la mesure a
    échoué doit rester au même écart qu'avant d'un clip mesuré, et non retomber à sa position absolue (110 s)."""
    project = Project(
        "t", media_assets=[_video("camA"), _video("camB"), _video("camC")],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("V3", "V3", "video")],
    )
    project.tracks[0].clips.append(Clip("a", "camA", "V1", 100.0, 0.0, 20.0, label="A"))
    project.tracks[1].clips.append(Clip("b", "camB", "V2", 103.0, 0.0, 20.0, label="B"))
    project.tracks[2].clips.append(Clip("c", "camC", "V3", 110.0, 0.0, 20.0, label="C"))
    outcomes = {
        "a": SyncOutcome(0.0, SyncMethod.AUDIO, SyncStatus.EXCELLENT, 0.9),
        "b": SyncOutcome(2.0, SyncMethod.AUDIO, SyncStatus.GOOD, 0.7),       # mesuré : 2 s après A (et non les 3 s de la timeline)
        "c": SyncOutcome(None, SyncMethod.AUDIO, SyncStatus.FAILED, 0.0),    # mesure échouée
    }
    source, _segment = create_multicam_from_clips(project, ["a", "b", "c"], outcomes=outcomes)
    assert source.multicam is not None
    placed = {angle.name: angle_offset(source, angle) for angle in source.multicam.angles}
    assert placed == {"A": 0.0, "B": 2.0, "C": 10.0}                         # C : ses 10 s d'écart avec A, pas 110 s
    failed = next(angle for angle in source.multicam.angles if angle.name == "C")
    assert failed.sync_status is SyncStatus.FAILED                            # et l'échec reste dit


def test_without_any_measured_offset_the_current_positions_are_used_as_they_are():
    project = _timeline_project()
    outcomes = {clip.id: SyncOutcome(None, SyncMethod.AUDIO, SyncStatus.FAILED, 0.0) for track in project.tracks for clip in track.clips}
    source, _segment = create_multicam_from_clips(project, ["a", "b", "r"], outcomes=outcomes)
    assert [angle_offset(source, angle) for angle in source.multicam.angles] == [0.5, 2.5, 0.0]


@pytest.mark.parametrize("control", ["gain_db", "pan", "fade_in", "fade_out", "audio_effects"])
def test_flattening_refuses_a_segment_that_carries_its_own_audio_controls(control):
    """Le rendu imbriqué applique gain, panoramique, fondus et effets audio du segment ; les clips remplaçants ne les
    reprendraient pas : l'export changerait de son à l'instant de l'aplatissement."""
    from core.audio_effects_model import AudioEffect, AudioEffectType

    project, _source, segment = _with_segment("angle-1")
    value = {"gain_db": -6.0, "pan": 0.5, "fade_in": 1.0, "fade_out": 1.0,
             "audio_effects": [AudioEffect("fx", next(iter(AudioEffectType)))]}[control]
    setattr(segment, control, value)
    before = _snapshot(project)
    with pytest.raises(MulticamError, match="gain|son"):
        flatten_multicam_clip(project, segment.id)
    assert _snapshot(project) == before                                       # rien n'a changé
    setattr(segment, control, [] if control == "audio_effects" else 0.0)
    assert flatten_multicam_clip(project, segment.id).video                  # sans réglage audio propre : aplati


def test_a_silent_camera_cannot_be_the_audio_source_but_a_recorder_or_a_camera_with_sound_can():
    project, source = _project()                                              # « Drone » : média sans son
    sequence = project.get_sequence(source.id)
    angles = {angle.name: angle for angle in sequence.multicam.angles}
    assert [angle_has_audio(project, sequence, angles[name]) for name in ("Wide", "Close-up", "Drone", "Recorder")] == [
        True, True, False, True,
    ]
    before = sequence.multicam.audio
    with pytest.raises(MulticamError, match="Drone"):
        set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-3"])
    with pytest.raises(MulticamError, match="Drone"):
        set_audio_policy(project, source.id, AudioMode.MIX, ["angle-1", "angle-3"])
    assert sequence.multicam.audio == before                                  # refusé : rien n'a changé
    assert set_audio_policy(project, source.id, AudioMode.FIXED, ["angle-2"]).angle_ids == ("angle-2",)
    assert set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO).mode is AudioMode.FOLLOW_VIDEO

