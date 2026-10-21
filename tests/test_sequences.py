"""Séquences multiples et séquences imbriquées : modèle, règles, rendu, format.

Couvre la logique pure (``core.sequences``, ``core.render_plan``,
``core.timeline_evaluator``, ``core.project_io``, ``core.edit_history``).
Les tests d'interface sont dans ``test_sequences_ui.py`` ; les rendus FFmpeg
réels dans ``test_sequences_export.py``.
"""

from __future__ import annotations

import json
import time

import pytest

from core.edit_history import ProjectHistory
from core.effects_model import ClipEffect, EffectType
from core.filter_graph import fingerprint_plan
from core.export_engine import ExportEngine
from core.preview_segments import build_segment_job, segment_params_hash, segment_plan
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import (
    MAIN_SEQUENCE_ID,
    Clip,
    Marker,
    MediaAsset,
    Project,
    Sequence,
    Track,
)
from core.render_plan import build_render_plan
from core.sequence_navigation import SequenceNavigator
from core.sequences import (
    MAX_NESTING_DEPTH,
    SequenceCycleError,
    SequenceError,
    SequenceInUseError,
    clamp_nested_clips,
    create_sequence,
    create_sequence_from_selection,
    delete_sequence,
    dependent_nested_clip_ids,
    duplicate_sequence,
    find_cycles,
    insert_sequence_clip,
    nested_clip_status,
    nesting_depth,
    rename_sequence,
    sequence_issues,
    sequence_usages,
    would_create_cycle,
)
from core.timeline_evaluator import evaluate_timeline, timeline_duration
from core.timeline_index import build_timeline_index
from core.timeline_operations import cut_clip, duplicate_clip, trim_clip_left, trim_clip_right
from core.transitions import Transition
from core.visual_effects import ClipTransform, TransformKeyframe


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


def _video(asset_id: str = "red", duration: float = 10.0, has_audio: bool = True) -> MediaAsset:
    return MediaAsset(asset_id, f"/media/{asset_id}.mp4", asset_id, duration, 1920, 1080, 30.0, "video", has_audio)


def _audio(asset_id: str = "music", duration: float = 20.0) -> MediaAsset:
    return MediaAsset(asset_id, f"/media/{asset_id}.wav", asset_id, duration, 0, 0, 0.0, "audio", True)


def _clip(clip_id: str, asset_id: str, track_id: str, start: float, length: float, **kwargs) -> Clip:
    return Clip(clip_id, asset_id, track_id, start, 0.0, length, label=clip_id, **kwargs)


def _two_level_project() -> Project:
    """``main`` (V1 bleu, V2 vide, A1) + ``intro`` (V1 rouge 0–4 s, A1 musique 0–4 s)."""
    intro = Sequence(
        "intro", "Intro",
        tracks=[
            Track("V1", "V1", "video", clips=[_clip("r1", "red", "V1", 0.0, 4.0)]),
            Track("A1", "A1", "audio", clips=[_clip("m1", "music", "A1", 0.0, 4.0)]),
        ],
    )
    main = Sequence(
        "main", "Master",
        tracks=[
            Track("V1", "V1", "video", clips=[_clip("b1", "blue", "V1", 0.0, 10.0)]),
            Track("V2", "V2", "video"),
            Track("A1", "A1", "audio"),
            Track("S1", "S1", "subtitle"),
        ],
    )
    return Project(
        "Episode 01",
        media_assets=[_video("red"), _video("blue", has_audio=False), _audio()],
        sequences=[main, intro],
        active_sequence_id="main",
    )


def _chain(levels: int) -> Project:
    """``seq0 ⊃ seq1 ⊃ … ⊃ seq{levels}`` ; la feuille contient un média."""
    sequences = []
    for index in range(levels + 1):
        clips = []
        if index == levels:
            clips = [_clip(f"leaf{index}", "red", "V1", 0.0, 4.0)]
        else:
            clips = [Clip(f"n{index}", "", "V1", 0.0, 0.0, 4.0, sequence_id=f"seq{index + 1}")]
        sequences.append(Sequence(f"seq{index}", f"S{index}", tracks=[Track("V1", "V1", "video", clips=clips)]))
    return Project("chain", media_assets=[_video("red")], sequences=sequences, active_sequence_id="seq0")


# ---------------------------------------------------------------------------
# Modèle multi-séquence
# ---------------------------------------------------------------------------


def test_legacy_constructor_creates_a_main_sequence_and_delegates():
    track = Track("V1", "V1", "video")
    project = Project("p", width=1280, height=720, fps=25.0, tracks=[track])
    assert [s.id for s in project.sequences] == [MAIN_SEQUENCE_ID]
    assert project.active_sequence_id == MAIN_SEQUENCE_ID
    assert project.tracks == [track] and project.width == 1280 and project.fps == 25.0
    project.markers = [Marker("m", 1.0)]
    assert project.active_sequence.markers[0].id == "m"


def test_project_properties_follow_the_active_sequence():
    project = _two_level_project()
    assert [t.id for t in project.tracks] == ["V1", "V2", "A1", "S1"]
    project.active_sequence_id = "intro"
    assert [t.id for t in project.tracks] == ["V1", "A1"]
    assert timeline_duration(project) == pytest.approx(4.0)


def test_unknown_active_sequence_falls_back_instead_of_crashing():
    project = _two_level_project()
    project.active_sequence_id = "ghost"
    assert project.active_sequence.id == "main"
    assert project.get_sequence("ghost") is None


def test_ambiguous_constructor_is_rejected():
    with pytest.raises(ValueError):
        Project("p", tracks=[Track("V1", "V1", "video")], sequences=[Sequence("s", "S")])


def test_sequence_validates_its_canvas():
    with pytest.raises(ValueError, match="largeur"):
        Sequence("s", "S", width=0)
    with pytest.raises(ValueError):
        Sequence("", "S")


def test_media_are_shared_between_sequences_not_duplicated():
    project = _two_level_project()
    dup = duplicate_sequence(project, "intro")
    assert len(project.media_assets) == 3
    assert {c.asset_id for t in dup.tracks for c in t.clips} == {"red", "music"}


# ---------------------------------------------------------------------------
# Création, insertion, renommage, duplication, suppression
# ---------------------------------------------------------------------------


def test_create_sequence_copies_settings_and_track_layout_without_clips():
    project = _two_level_project()
    sequence = create_sequence(project, "Scene 01")
    assert sequence.id.startswith("seq-") and sequence.name == "Scene 01"
    assert [t.id for t in sequence.tracks] == ["V1", "V2", "A1", "S1"]
    assert all(not t.clips for t in sequence.tracks)
    assert (sequence.width, sequence.height, sequence.fps) == (1920, 1080, 30.0)
    assert create_sequence(project, "Scene 01").name == "Scene 01 2"


def test_insert_nested_clip_references_the_sequence_without_copying_it():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 2.0)
    assert clip.sequence_id == "intro" and clip.asset_id == ""
    assert (clip.source_in, clip.source_out, clip.timeline_start) == (0.0, 4.0, 2.0)
    assert clip.label == "Intro" and clip.is_nested
    assert not hasattr(clip, "tracks")


@pytest.mark.parametrize("track_id", ["S1"])
def test_nested_clip_is_refused_on_incompatible_tracks(track_id):
    project = _two_level_project()
    with pytest.raises(SequenceError):
        insert_sequence_clip(project, "intro", track_id, 0.0)


def test_nested_clip_is_refused_on_locked_track_and_for_empty_sequences():
    project = _two_level_project()
    project.tracks[1].locked = True
    with pytest.raises(SequenceError, match="verrouillée"):
        insert_sequence_clip(project, "intro", "V2", 0.0)
    empty = create_sequence(project, "Vide")
    with pytest.raises(SequenceError, match="vide"):
        insert_sequence_clip(project, empty.id, "V1", 0.0)


def test_rename_updates_the_single_source_of_truth():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 0.0)
    rename_sequence(project, "intro", "  Générique  ")
    assert project.get_sequence("intro").name == "Générique"
    with pytest.raises(SequenceError):
        rename_sequence(project, "intro", "   ")


def test_duplicate_gives_an_independent_timeline_with_shared_references():
    project = _two_level_project()
    inner = project.get_sequence("intro")
    inner.transitions.append(Transition("t", "r1", "m1"))
    inner.tracks[0].clips[0].effects.append(ClipEffect("fx", EffectType.SEPIA))
    insert_sequence_clip(project, "intro", "V2", 0.0)
    copy = duplicate_sequence(project, "main", "Master v2")
    assert copy.id not in {"main", "intro"} and copy.name == "Master v2"
    original_ids = {c.id for t in project.get_sequence("main").tracks for c in t.clips}
    copy_ids = {c.id for t in copy.tracks for c in t.clips}
    assert not original_ids & copy_ids
    nested = [c for t in copy.tracks for c in t.clips if c.sequence_id]
    assert [c.sequence_id for c in nested] == ["intro"]  # pas de copie profonde récursive
    assert len(project.sequences) == 3
    copy.tracks[0].clips[0].timeline_start = 5.0
    assert project.get_sequence("main").tracks[0].clips[0].timeline_start == 0.0
    inner_copy = duplicate_sequence(project, "intro")
    transition = inner_copy.transitions[0]
    assert transition.from_clip_id != "r1" and transition.from_clip_id in {
        c.id for t in inner_copy.tracks for c in t.clips
    }
    assert inner_copy.tracks[0].clips[0].effects[0].type == EffectType.SEPIA


def test_deleting_a_used_sequence_lists_usages_and_requires_force():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 0.0)
    insert_sequence_clip(project, "intro", "V2", 6.0)
    with pytest.raises(SequenceInUseError) as info:
        delete_sequence(project, "intro")
    assert len(info.value.usages) == 2
    assert {u.parent_sequence_name for u in info.value.usages} == {"Master"}
    assert project.get_sequence("intro") is not None
    usages = delete_sequence(project, "intro", force=True)
    assert len(usages) == 2 and project.get_sequence("intro") is None
    broken = [c for t in project.tracks for c in t.clips if c.sequence_id]
    assert len(broken) == 2  # rien n'est supprimé : clips hors ligne
    assert nested_clip_status(project, broken[0]) == "missing"
    assert {issue.kind for issue in sequence_issues(project)} == {"missing"}


def test_cannot_delete_the_last_sequence_and_active_is_reassigned():
    project = Project("p")
    with pytest.raises(SequenceError):
        delete_sequence(project, MAIN_SEQUENCE_ID)
    project = _two_level_project()
    project.active_sequence_id = "intro"
    delete_sequence(project, "intro")
    assert project.active_sequence_id == "main"


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def test_direct_cycle_is_refused():
    project = _two_level_project()
    assert would_create_cycle(project, "main", "main")
    with pytest.raises(SequenceCycleError):
        insert_sequence_clip(project, "main", "V2", 0.0)


def test_indirect_cycles_are_refused():
    project = _chain(2)  # seq0 ⊃ seq1 ⊃ seq2
    assert would_create_cycle(project, "seq2", "seq0")
    assert would_create_cycle(project, "seq1", "seq0")
    project.active_sequence_id = "seq2"
    with pytest.raises(SequenceCycleError, match="circulaire"):
        insert_sequence_clip(project, "seq0", "V1", 5.0)
    project.active_sequence_id = "seq1"
    with pytest.raises(SequenceCycleError):
        insert_sequence_clip(project, "seq0", "V1", 5.0)
    assert not would_create_cycle(project, "seq0", "seq2")


def _cyclic_project() -> Project:
    """Projet corrompu (A ⊃ B ⊃ C ⊃ A), comme un fichier retouché à la main."""
    project = _chain(2)
    project.get_sequence("seq2").tracks[0].clips.append(
        Clip("back", "", "V1", 4.0, 0.0, 4.0, sequence_id="seq0")
    )
    return project


def test_cycles_in_loaded_data_are_detected_and_never_recurse_forever():
    project = _cyclic_project()
    assert find_cycles(project) == [("seq0", "seq1", "seq2")]
    assert any(issue.kind == "cycle" for issue in sequence_issues(project))
    started = time.perf_counter()
    plan = build_render_plan(project)
    assert time.perf_counter() - started < 2.0
    assert any("circulaire" in warning for warning in plan.warnings)
    assert evaluate_timeline(project, 1.0)  # la branche saine reste évaluée
    project.active_sequence_id = "seq2"
    assert [c.clip_id for c in evaluate_timeline(project, 5.0)] == []
    assert nesting_depth(project, "seq0") == 2


def test_self_reference_is_a_cycle_of_one():
    project = _two_level_project()
    project.get_sequence("intro").tracks[0].clips.append(
        Clip("self", "", "V1", 0.0, 0.0, 1.0, sequence_id="intro")
    )
    assert ("intro",) in find_cycles(project)
    project.active_sequence_id = "intro"
    plan = build_render_plan(project)
    assert [layer.clip_id for layer in plan.video_layers] == ["r1"]
    assert plan.warnings


def test_unknown_reference_never_crashes_evaluation_or_rendering():
    project = _two_level_project()
    project.tracks[1].clips.append(Clip("ghost", "", "V2", 0.0, 0.0, 3.0, sequence_id="nope"))
    plan = build_render_plan(project)
    assert all(layer.clip_id != "ghost" for layer in plan.video_layers)
    assert any("introuvable" in warning for warning in plan.warnings)
    assert [c.clip_id for c in evaluate_timeline(project, 1.0)] == ["b1"]
    assert build_timeline_index(project).active_at(project, 1.0)[0].clip_id == "b1"


# ---------------------------------------------------------------------------
# Créer une séquence à partir de la sélection
# ---------------------------------------------------------------------------


def test_nest_selection_preserves_relative_positions_and_replaces_the_selection():
    project = _two_level_project()
    main = project.get_sequence("main")
    main.tracks[1].clips.append(_clip("t1", "red", "V2", 3.0, 2.0, effects=[ClipEffect("fx", EffectType.BLUR)]))
    main.tracks[2].clips.append(_clip("a1", "music", "A1", 4.0, 3.0, gain_db=-6.0))
    main.markers.extend([Marker("in", 3.5, "dans"), Marker("out", 9.0, "dehors")])
    result = create_sequence_from_selection(project, ["t1", "a1"], "Scene 01")
    inner = result.sequence
    assert inner.name == "Scene 01" and [t.id for t in inner.tracks] == ["V2", "A1"]
    moved = {c.id: c for t in inner.tracks for c in t.clips}
    assert moved["t1"].timeline_start == 0.0 and moved["a1"].timeline_start == pytest.approx(1.0)
    assert moved["t1"].effects[0].type == EffectType.BLUR and moved["a1"].gain_db == -6.0
    assert [m.time_seconds for m in inner.markers] == [pytest.approx(0.5)]
    assert len(main.markers) == 2  # les repères du parent sont copiés, pas déplacés
    nested = result.clip
    assert nested.track_id == "V2" and nested.timeline_start == 3.0
    assert nested.source_out == pytest.approx(4.0) and nested.sequence_id == inner.id
    remaining = {c.id for t in main.tracks for c in t.clips}
    assert "t1" not in remaining and "a1" not in remaining and nested.id in remaining
    assert inner.duration == pytest.approx(4.0)


def test_nest_selection_moves_inner_transitions_and_drops_crossing_ones():
    project = _two_level_project()
    main = project.get_sequence("main")
    main.tracks[1].clips.extend([_clip("x", "red", "V2", 0.0, 2.0), _clip("y", "red", "V2", 2.0, 2.0)])
    main.tracks[0].clips.append(_clip("z", "red", "V1", 10.0, 2.0))
    main.transitions.extend([Transition("inside", "x", "y"), Transition("crossing", "b1", "z")])
    result = create_sequence_from_selection(project, ["x", "y", "b1"], "Nest")
    assert [t.id for t in result.sequence.transitions] == ["inside"]
    assert result.dropped_transition_ids == ("crossing",)
    assert main.transitions == []


def test_nest_selection_rejects_locked_tracks_and_unknown_clips():
    project = _two_level_project()
    project.tracks[0].locked = True
    with pytest.raises(SequenceError):
        create_sequence_from_selection(project, ["b1"])
    with pytest.raises(KeyError):
        create_sequence_from_selection(project, ["nope"])
    with pytest.raises(SequenceError):
        create_sequence_from_selection(project, [])


def test_nesting_keeps_the_rendered_layers_equivalent():
    project = _two_level_project()
    before = build_render_plan(project)
    create_sequence_from_selection(project, ["b1"], "Wrap")
    after = build_render_plan(project)
    assert len(after.nested_sequences) == 1
    inner = after.nested_sequences[0].plan
    assert [l.clip_id for l in inner.video_layers] == [l.clip_id for l in before.video_layers]
    assert after.duration == before.duration


# ---------------------------------------------------------------------------
# Durée dynamique, trims
# ---------------------------------------------------------------------------


def test_trim_right_is_bounded_by_the_sequence_duration():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 0.0)
    trim_clip_right(project, clip.id, 3.0)
    assert clip.source_out == pytest.approx(3.0)
    with pytest.raises(ValueError, match="séquence 'Intro'"):
        trim_clip_right(project, clip.id, 5.0)
    trim_clip_left(project, clip.id, 1.0)
    assert (clip.source_in, clip.timeline_start) == (pytest.approx(1.0), pytest.approx(1.0))


def test_lengthened_source_keeps_clip_in_out():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 0.0)
    project.get_sequence("intro").tracks[0].clips[0].source_out = 8.0
    assert clamp_nested_clips(project) == []
    assert clip.source_out == 4.0
    trim_clip_right(project, clip.id, 8.0)  # l'utilisateur peut maintenant étendre
    assert clip.source_out == pytest.approx(8.0)


def test_shortened_source_clamps_overflowing_clips_but_never_deletes():
    project = _two_level_project()
    whole = insert_sequence_clip(project, "intro", "V2", 0.0)
    tail = insert_sequence_clip(project, "intro", "V2", 5.0, source_in=3.0, source_out=4.0)
    inner = project.get_sequence("intro")
    for track in inner.tracks:
        track.clips[0].source_out = 2.5
    assert nested_clip_status(project, whole) == "overflow"
    adjustments = clamp_nested_clips(project)
    assert [(a.clip_id, a.new_source_out) for a in adjustments] == [(whole.id, 2.5)]
    assert whole.source_out == 2.5
    assert tail.source_out == 4.0 and tail in project.tracks[1].clips  # hors source, conservé


def test_overflowing_clip_renders_empty_not_frozen():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 0.0)
    for track in project.get_sequence("intro").tracks:
        track.clips[0].source_out = 2.0
    plan = build_render_plan(project)
    entry = plan.nested_sequences[0]
    # Le sous-plan couvre tout ce que lisent ses instances : au-delà de la
    # fin de la séquence, l'image est transparente (fond), jamais figée.
    assert entry.plan.duration == pytest.approx(4.0)
    assert evaluate_timeline(project, 3.0)[-1].clip_id == "b1"


def test_split_and_duplicate_keep_the_sequence_reference():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 0.0)
    clip.effects.append(ClipEffect("fx", EffectType.SEPIA))
    left, right = cut_clip(project, clip.id, 1.5)
    assert left.sequence_id == right.sequence_id == "intro"
    assert right.source_in == pytest.approx(1.5) and right.effects[0].type == EffectType.SEPIA
    assert right.effects is not left.effects
    assert duplicate_clip(project, right.id).sequence_id == "intro"


# ---------------------------------------------------------------------------
# Plan de rendu
# ---------------------------------------------------------------------------


def test_multiple_instances_share_one_nested_plan():
    project = _two_level_project()
    for start in (0.0, 4.0, 8.0):
        insert_sequence_clip(project, "intro", "V2", start)
    plan = build_render_plan(project)
    assert [entry.key for entry in plan.nested_sequences] == ["intro"]
    nested_layers = [l for l in plan.video_layers if l.nested_key]
    assert len(nested_layers) == 3 and {l.nested_key for l in nested_layers} == {"intro"}
    assert all(l.source_path == "" for l in nested_layers)
    graph, *_ = ExportEngine._build_filter_complex(plan, 1920, 1080, 30, None)
    assert graph.count("[0:v]") + graph.count("[1:v]") >= 1
    assert "split=3" in graph and "asplit=3" in graph
    assert graph.count("color=c=black@0") == 1  # Intro composée une seule fois


def test_changes_in_the_source_propagate_to_every_instance():
    project = _two_level_project()
    for start in (0.0, 5.0):
        insert_sequence_clip(project, "intro", "V2", start)
    before = fingerprint_plan(build_render_plan(project))
    project.get_sequence("intro").tracks[0].clips[0].transform = ClipTransform(scale=0.5)
    plan = build_render_plan(project)
    assert fingerprint_plan(plan) != before
    assert plan.nested_sequences[0].plan.video_layers[0].transform.scale == 0.5


def test_nested_audio_follows_parent_track_rules():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 1.0)
    clip.gain_db = -3.0
    plan = build_render_plan(project)
    nested_audio = [l for l in plan.audio_layers if l.nested_key]
    assert len(nested_audio) == 1 and nested_audio[0].gain_db == -3.0
    assert nested_audio[0].timeline_start == 1.0
    inner = plan.nested_sequences[0].plan
    assert [l.clip_id for l in inner.audio_layers] == ["r1", "m1"]
    project.tracks[1].muted = True
    assert not [l for l in build_render_plan(project).audio_layers if l.nested_key]
    project.tracks[1].muted = False
    project.get_sequence("intro").tracks[1].muted = True  # mute interne respecté
    inner = build_render_plan(project).nested_sequences[0].plan
    assert [l.clip_id for l in inner.audio_layers] == ["r1"]


def test_nested_clip_on_an_audio_track_brings_sound_only():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "A1", 0.0)
    plan = build_render_plan(project)
    assert not [l for l in plan.video_layers if l.nested_key]
    assert [l.nested_key for l in plan.audio_layers if l.nested_key] == ["intro"]
    graph, *_ = ExportEngine._build_filter_complex(plan, 640, 360, 30, None)
    assert "color=c=black@0" not in graph  # pas de vidéo imbriquée construite


def test_nested_effects_and_transforms_apply_after_the_inner_composite():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 0.0)
    clip.transform = ClipTransform(scale=0.5, opacity=0.8)
    clip.effects.append(ClipEffect("fx", EffectType.SEPIA))
    plan = build_render_plan(project)
    layer = next(l for l in plan.video_layers if l.nested_key)
    assert layer.transform.scale == 0.5 and layer.effects[0].type == EffectType.SEPIA
    graph, *_ = ExportEngine._build_filter_complex(plan, 1920, 1080, 30, None)
    chain = next(part for part in graph.split(";") if part.startswith("[n0_vout]"))
    assert "colorchannelmixer" in chain and "black@0" in chain


def test_nested_and_inner_keyframes_stay_independent():
    project = _two_level_project()
    inner_clip = project.get_sequence("intro").tracks[0].clips[0]
    inner_clip.transform_keyframes = [
        TransformKeyframe("opacity", 0.0, 0.0), TransformKeyframe("opacity", 1.0, 1.0)
    ]
    clip = insert_sequence_clip(project, "intro", "V2", 2.0)
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 2.0, 0.5)]
    plan = build_render_plan(project)
    outer = next(l for l in plan.video_layers if l.nested_key)
    inner = plan.nested_sequences[0].plan.video_layers[0]
    assert {kf.property_name for kf in outer.transform_keyframes} == {"scale"}
    assert {kf.property_name for kf in inner.transform_keyframes} == {"opacity"}
    graph, *_ = ExportEngine._build_filter_complex(plan, 640, 360, 30, None)
    assert "geq=" in graph  # opacité animée interne
    assert graph.count("eval=frame") >= 2


def test_nested_subtitles_are_lifted_into_the_parent_timeline():
    project = _two_level_project()
    inner = project.get_sequence("intro")
    inner.tracks.append(Track("S1", "S1", "subtitle", clips=[_clip("s", "red", "S1", 1.0, 2.0, text="Bonjour")]))
    insert_sequence_clip(project, "intro", "V2", 5.0)
    plan = build_render_plan(project)
    assert [(c.start, c.end, c.text) for c in plan.subtitle_cues] == [(6.0, 8.0, "Bonjour")]


def test_nested_sequence_with_its_own_resolution_is_scaled_like_a_media():
    project = _two_level_project()
    inner = project.get_sequence("intro")
    inner.width, inner.height, inner.fps = 1280, 720, 25.0
    insert_sequence_clip(project, "intro", "V2", 0.0)
    plan = build_render_plan(project)
    graph, *_ = ExportEngine._build_filter_complex(plan, 960, 540, 30, None)
    assert "color=c=black@0:s=640x360:r=25:" in graph


def test_render_plan_can_target_any_sequence():
    project = _two_level_project()
    plan = build_render_plan(project, sequence_id="intro")
    assert plan.sequence_id == "intro" and plan.duration == pytest.approx(4.0)
    with pytest.raises(KeyError):
        build_render_plan(project, sequence_id="nope")


# ---------------------------------------------------------------------------
# Évaluation récursive (aperçu temps réel)
# ---------------------------------------------------------------------------


def test_evaluator_descends_into_nested_sequences_with_time_mapping():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 2.0, source_in=1.0, source_out=4.0)
    active = evaluate_timeline(project, 3.0)
    nested = [a for a in active if a.root_clip_id == clip.id]
    assert {a.clip_id for a in nested} == {"r1", "m1"}
    red = next(a for a in nested if a.clip_id == "r1")
    assert red.source_time == pytest.approx(2.0)  # 1.0 (in) + 1.0 dans le clip
    assert red.track_id == "V2" and red.timeline_start == 2.0 and red.timeline_end == 5.0
    assert red.owner_clip_id == clip.id and red.nested_path == (clip.id,) and red.sequence_id == "intro"
    indexed = build_timeline_index(project).active_at(project, 3.0)
    assert [(a.clip_id, a.source_time) for a in indexed] == [(a.clip_id, a.source_time) for a in active]


def test_evaluator_honours_speed_and_inner_solo():
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 0.0)
    clip.time_remapping = clip.time_remapping.__class__(speed=2.0)
    red = next(a for a in evaluate_timeline(project, 1.0) if a.clip_id == "r1")
    assert red.source_time == pytest.approx(2.0)
    project.get_sequence("intro").tracks[0].solo = True
    assert {a.clip_id for a in evaluate_timeline(project, 0.5) if a.root_clip_id} == {"r1", "m1"}


# ---------------------------------------------------------------------------
# Récursion profonde
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("levels", [1, 3, 10])
def test_deep_nesting_renders_and_evaluates(levels):
    project = _chain(levels)
    plan = build_render_plan(project)
    assert len(plan.nested_sequences) == levels and not plan.warnings
    assert plan.nested_sequences[-1].sequence_id == "seq1"  # enfants avant parents
    assert plan.nested_sequences[0].plan.video_layers[0].clip_id == f"leaf{levels}"
    active = evaluate_timeline(project, 1.0)
    assert [a.clip_id for a in active] == [f"leaf{levels}"]
    assert len(active[0].nested_path) == levels
    graph, *_ = ExportEngine._build_filter_complex(plan, 640, 360, 30, None)
    assert graph.count("color=c=black@0") == levels


def test_nesting_beyond_the_limit_stops_with_a_warning():
    project = _chain(MAX_NESTING_DEPTH + 3)
    plan = build_render_plan(project)
    assert len(plan.nested_sequences) <= MAX_NESTING_DEPTH
    assert any("niveaux" in warning for warning in plan.warnings)
    assert any(issue.kind == "depth" for issue in sequence_issues(project))
    assert evaluate_timeline(project, 1.0) == []


def test_inserting_beyond_the_depth_limit_is_refused():
    project = _chain(MAX_NESTING_DEPTH)
    top = create_sequence(project, "Top", tracks=[Track("V1", "V1", "video")])
    project.active_sequence_id = top.id
    with pytest.raises(SequenceError, match="profonde"):
        insert_sequence_clip(project, "seq0", "V1", 0.0)


# ---------------------------------------------------------------------------
# Aperçu : segments et invalidation
# ---------------------------------------------------------------------------


def test_preview_segments_window_the_nested_plan():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 2.0)
    plan = segment_plan(project, 2.0, 4.0)
    assert [e.key.split("@")[0] for e in plan.nested_sequences] == ["intro"]
    assert "@" in plan.nested_sequences[0].key
    job = build_segment_job(project, 1, quality="draft")
    assert job is not None and job.plan.nested_sequences[0].sequence_id == "intro"


def test_editing_a_sequence_only_invalidates_dependent_segments():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 2.0)  # occupe 2–6 s

    def hashes():
        return {
            start: segment_params_hash(segment_plan(project, start, start + 2.0), project, "draft", start + 2.0)
            for start in (0.0, 2.0, 4.0, 8.0)
        }

    before = hashes()
    project.get_sequence("intro").tracks[0].clips[0].effects.append(ClipEffect("fx", EffectType.SEPIA))
    after = hashes()
    assert after[0.0] == before[0.0] and after[8.0] == before[8.0]
    assert after[2.0] != before[2.0] and after[4.0] != before[4.0]


def test_dependent_nested_clip_ids_are_transitive():
    project = _chain(3)
    assert dependent_nested_clip_ids(project, "seq3") == {"n0", "n1", "n2"}
    assert dependent_nested_clip_ids(project, "seq1") == {"n0"}
    assert dependent_nested_clip_ids(project, "seq0") == set()


def test_proxies_never_replace_a_nested_layer():
    from core.preview_segments import apply_path_resolver

    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 0.0)
    plan = apply_path_resolver(build_render_plan(project), lambda path, need_audio=False: f"proxy:{path}")
    nested = next(l for l in plan.video_layers if l.nested_key)
    assert nested.source_path == ""
    assert plan.nested_sequences[0].plan.video_layers[0].source_path == "proxy:/media/red.mp4"


# ---------------------------------------------------------------------------
# Sérialisation .kut
# ---------------------------------------------------------------------------


def test_kut_roundtrip_keeps_sequences_ids_active_and_nested_clips(tmp_path):
    project = _two_level_project()
    clip = insert_sequence_clip(project, "intro", "V2", 1.0)
    project.active_sequence_id = "intro"
    path = tmp_path / "multi.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == CURRENT_VERSION == 17
    assert raw["project"]["active_sequence_id"] == "intro"
    assert [s["id"] for s in raw["project"]["sequences"]] == ["main", "intro"]
    nested_raw = raw["project"]["sequences"][0]["tracks"][1]["clips"][0]
    assert nested_raw["sequence_id"] == "intro" and nested_raw["asset_id"] == ""
    assert "sequence_id" not in raw["project"]["sequences"][0]["tracks"][0]["clips"][0]
    loaded = load_project(str(path))
    assert [s.id for s in loaded.sequences] == ["main", "intro"]
    assert loaded.active_sequence_id == "intro"
    reloaded_clip = loaded.get_sequence("main").tracks[1].clips[0]
    assert reloaded_clip.id == clip.id and reloaded_clip.sequence_id == "intro"
    assert loaded == project


def test_legacy_single_timeline_project_loads_as_a_main_sequence(tmp_path):
    payload = {
        "format": "kut-studio-project",
        "version": 13,
        "project": {
            "name": "Ancien", "width": 1280, "height": 720, "fps": 25.0,
            "media_assets": [{"id": "a", "path": "/m/a.mp4", "name": "a", "duration": 5.0,
                              "width": 1280, "height": 720, "fps": 25.0, "media_type": "video"}],
            "markers": [{"id": "m", "time_seconds": 1.0}],
            "transitions": [],
            "tracks": [{"id": "V1", "name": "V1", "type": "video", "clips": [
                {"id": "c", "asset_id": "a", "track_id": "V1", "timeline_start": 0.0,
                 "source_in": 0.0, "source_out": 5.0}]}],
        },
    }
    path = tmp_path / "old.kut"
    path.write_text(json.dumps(payload), encoding="utf-8")
    project = load_project(str(path))
    assert [s.id for s in project.sequences] == [MAIN_SEQUENCE_ID]
    assert (project.width, project.height, project.fps) == (1280, 720, 25.0)
    assert project.tracks[0].clips[0].id == "c" and project.markers[0].id == "m"
    save_project(project, str(tmp_path / "migrated.kut"))
    assert load_project(str(tmp_path / "migrated.kut")) == project


def test_version_one_project_still_loads(tmp_path):
    payload = {"format": "kut-studio-project", "version": 1,
               "project": {"name": "v1", "width": 640, "height": 360, "fps": 24.0,
                           "media_assets": [], "tracks": []}}
    path = tmp_path / "v1.kut"
    path.write_text(json.dumps(payload), encoding="utf-8")
    project = load_project(str(path))
    assert project.active_sequence.name and project.width == 640


def test_broken_references_and_cycles_load_without_modification(tmp_path):
    project = _cyclic_project()
    project.tracks[0].clips.append(Clip("ghost", "", "V1", 9.0, 0.0, 1.0, sequence_id="deleted"))
    path = tmp_path / "corrupt.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    kinds = {issue.kind for issue in sequence_issues(loaded)}
    assert kinds == {"cycle", "missing"}
    assert loaded == project  # rien n'est « réparé » en silence
    build_render_plan(loaded)


def test_invalid_sequence_entries_are_reported_clearly(tmp_path):
    path = tmp_path / "bad.kut"
    path.write_text(json.dumps({"format": "kut-studio-project", "version": 14,
                                "project": {"name": "x", "sequences": ["nope"]}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Séquence invalide"):
        load_project(str(path))
    path.write_text(json.dumps({"format": "kut-studio-project", "version": 14,
                                "project": {"name": "x", "sequences": [
                                    {"id": "s", "name": "A", "tracks": []},
                                    {"id": "s", "name": "B", "tracks": []}],
                                    "active_sequence_id": "ghost"}}), encoding="utf-8")
    loaded = load_project(str(path))
    assert len({s.id for s in loaded.sequences}) == 2 and loaded.active_sequence_id == "s"


# ---------------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------------


def test_nesting_is_undone_and_redone_in_one_step():
    project = _two_level_project()
    history = ProjectHistory()
    history.reset(project)
    create_sequence_from_selection(project, ["b1"], "Wrap")
    history.record(project, "Imbriquer")
    restored = history.undo()
    assert [s.id for s in restored.sequences] == ["main", "intro"]
    assert restored.get_sequence("main").tracks[0].clips[0].id == "b1"
    again = history.redo()
    assert len(again.sequences) == 3
    assert any(c.sequence_id for c in again.get_sequence("main").tracks[0].clips)


def test_snapshots_share_unchanged_sequences():
    project = _two_level_project()
    for index in range(5):
        create_sequence(project, f"S{index}")
    history = ProjectHistory()
    history.reset(project)
    project.tracks[0].clips[0].timeline_start = 1.0
    history.record(project, "Déplacer")
    first, second = history._undo_stack[0].project, history._undo_stack[1].project
    shared = [a is b for a, b in zip(first.sequences, second.sequences)]
    assert shared[0] is False and all(shared[1:])
    restored = history.undo()
    assert restored.sequences[1] is not first.sequences[1]  # la restauration est une copie


def test_undo_stays_in_the_sequence_where_the_change_happened():
    project = _two_level_project()
    history = ProjectHistory()
    history.reset(project)
    project.active_sequence_id = "intro"
    project.tracks[0].clips[0].timeline_start = 0.5
    history.record(project, "Déplacer dans Intro")
    restored = history.undo()
    assert restored.active_sequence_id == "intro"
    assert restored.get_sequence("intro").tracks[0].clips[0].timeline_start == 0.0


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_navigator_breadcrumb_parent_back_and_forward():
    project = _chain(2)
    navigator = SequenceNavigator()
    navigator.reset(project)
    navigator.descend("seq1")
    navigator.descend("seq2")
    assert [name for _id, name in navigator.breadcrumb(project)] == ["S0", "S1", "S2"]
    assert navigator.go_parent(project) == "seq1" and navigator.path == ["seq0", "seq1"]
    assert navigator.back() == "seq2" and navigator.path == ["seq0", "seq1", "seq2"]
    assert navigator.forward() == "seq1"
    navigator.open("seq2")
    assert navigator.path == ["seq2"]
    assert navigator.parent_target(project) == "seq1"  # remonte via les usages


def test_navigator_resyncs_after_deletion_and_remembers_playheads():
    project = _chain(2)
    navigator = SequenceNavigator()
    navigator.reset(project)
    navigator.descend("seq1")
    navigator.remember_playhead("seq1", 3.5)
    project.active_sequence_id = "seq0"
    delete_sequence(project, "seq1", force=True)
    navigator.sync(project)
    assert navigator.path == ["seq0"] and "seq1" not in navigator.playheads
    navigator.remember_playhead("seq0", 2.0)
    assert navigator.playhead_for("seq0") == 2.0 and navigator.playhead_for("seq2") == 0.0


def test_usages_report_where_a_sequence_is_used():
    project = _two_level_project()
    insert_sequence_clip(project, "intro", "V2", 3.0)
    usages = sequence_usages(project, "intro")
    assert [(u.parent_sequence_name, u.track_id, u.timeline_start) for u in usages] == [("Master", "V2", 3.0)]


# ---------------------------------------------------------------------------
# Performances : invariants (pas de durées absolues)
# ---------------------------------------------------------------------------


def _median_per_call(fn, calls: int = 40, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(calls):
            fn()
        samples.append((time.perf_counter() - started) / calls)
    samples.sort()
    return samples[len(samples) // 2]


def test_nested_lookup_cost_does_not_grow_with_the_inner_sequence():
    from tools.perf.nested_bench import heavy_project

    costs = {}
    for size in (150, 3000):
        project = heavy_project(size)
        index = build_timeline_index(project)
        index.active_at(project, 2.5)  # construit les index imbriqués
        costs[size] = _median_per_call(lambda: index.active_at(project, 2.5))
        segment_plan(project, 2.0, 4.0, timeline_index=index)
        costs[f"segment{size}"] = _median_per_call(
            lambda: segment_plan(project, 2.0, 4.0, timeline_index=index), calls=10
        )
    assert costs[3000] < costs[150] * 5
    assert costs["segment3000"] < costs["segment150"] * 5


def test_each_sequence_is_composed_once_whatever_the_instance_count():
    from tools.perf.nested_bench import instances_project

    for count in (1, 5, 40):
        plan = build_render_plan(instances_project(count))
        graph, *_ = ExportEngine._build_filter_complex(plan, 320, 180, 25, None)
        assert len(plan.nested_sequences) == 1
        assert graph.count("color=c=black@0") == 1


def test_nested_bench_runs_on_tiny_scenarios():
    from tools.perf.nested_bench import run

    report = run(chains=(1,), instances=(2,), heavy=(30,))
    assert set(report["scenarios"]) == {"chain:1", "instances:2", "heavy:30"}
    assert report["scenarios"]["instances:2"]["nested_compositions"] == 1.0
