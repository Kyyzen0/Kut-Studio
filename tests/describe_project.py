"""Projets déterministes pour les tests de ``describe`` : identifiants explicites, aucun fichier, aucun hasard."""

from __future__ import annotations

from core.audio_buses import add_bus, route_track
from core.graphics import GraphicType, add_graphic_clip
from core.keyframe_editing import add_keyframe
from core.project_model import MediaAsset, Project, Sequence, Track
from core.sequences import insert_sequence_clip
from core.time_ops import add_speed_point
from core.time_remapping import TimeInterpolation, TimeRemapping
from core.timeline_editing import add_marker
from core.timeline_operations import add_clip_to_track, add_subtitle_clip, set_clip_freeze_frame
from core.transitions import TransitionType, add_transition
from core.visual_effects import ClipTransform


def small_project() -> Project:
    """Un petit projet fixe (le « golden » de ``tests/data/describe_golden.json``) : deux clips vidéo enchaînés avec une transition,
    un clip audio, un repère. Aucun média n'existe sur le disque."""
    project = Project(
        "Démo", width=1280, height=720, fps=25.0,
        media_assets=[
            MediaAsset("m1", "/media/a.mp4", "a", 4.0, 1280, 720, 25.0, "video", has_audio=True),
            MediaAsset("m2", "/media/musique.wav", "musique", 30.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio", volume_db=-3.0)],
    )
    add_clip_to_track(project, "m1", "V1", 0.0, clip_id="c1")
    add_clip_to_track(project, "m1", "V1", 4.0, clip_id="c2")
    add_clip_to_track(project, "m2", "A1", 0.0, clip_id="c3")
    add_transition(project, "c1", "c2", TransitionType.CROSSFADE, 0.5, transition_id="t1")
    add_marker(project, 2.0, "Idée", marker_id="k1")
    return project


def rich_project() -> Project:
    """Un projet qui exerce **toutes** les clés de ``describe`` : métadonnées de média, séquence imbriquée, version de format, calque
    graphique, sous-titre, arrêt sur image, courbe de vitesse, image clé, transition, repères, bus (non décrit)."""
    project = Project(
        "Riche", width=1920, height=1080, fps=30.0,
        media_assets=[
            MediaAsset("m1", "/media/a.mp4", "a", 10.0, 1920, 1080, 30.0, "video", has_audio=True, timecode="01:00:00:00",
                       timecode_fps=29.97, time_reference=3600.5, reel="A001", camera="Cam A", creation_time="2026-10-19T10:00:00Z",
                       color_matrix="bt709"),
            MediaAsset("m2", "/media/musique.wav", "musique", 30.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video", solo=True), Track("V2", "V2", "video"),
                Track("A1", "A1", "audio", muted=True, volume_db=-3.5, pan=0.25, audio_role="music"),
                Track("S1", "S1", "subtitle")],
    )
    add_clip_to_track(project, "m1", "V1", 0.0, clip_id="c1")
    add_clip_to_track(project, "m1", "V1", 10.0, clip_id="c2")
    add_clip_to_track(project, "m1", "V2", 0.0, clip_id="c3")
    add_clip_to_track(project, "m2", "A1", 0.0, clip_id="c4")
    add_transition(project, "c1", "c2", TransitionType.DISSOLVE, 0.5, transition_id="t1")
    add_subtitle_clip(project, "Bonjour", 1.0, 2.0, track_id="S1", clip_id="s1")
    add_graphic_clip(project, GraphicType.TEXT, timeline_start=1.0, duration=3.0, clip_id="g1")
    add_marker(project, 5.0, "Chapitre", "chapter", marker_id="k1")
    add_marker(project, 1.0, "À faire", "todo", marker_id="k2")
    bus = add_bus(project, "Voix", bus_id="b1")
    route_track(project, "A1", bus.id)
    clips = {clip.id: clip for track in project.tracks for clip in track.clips}
    first = clips["c1"]
    first.label = "Plan large"
    first.time_remapping = TimeRemapping(speed=2.0, reverse=True, interpolation=TimeInterpolation.BLENDING, preserve_pitch=False,
                                         remap_audio=False)
    first.transform = ClipTransform(position_x=0.1, scale=1.2, rotation=15.0, opacity=0.8, flip_h=True, fill=True, pan_x=0.05)
    first.gain_db, first.pan, first.fade_in, first.fade_out = -6.0, -0.5, 0.25, 0.5
    first.angle_id, first.template_slot = "angle-1", "slot-01"
    add_keyframe(project, "c1", "scale", 0.0, 1.0)
    add_keyframe(project, "c1", "scale", 1.0, 1.5)
    set_clip_freeze_frame(project, "c2", 1.0, 2.0)
    add_speed_point(project, "c3", 1.0, 2.0)
    project.sequences.append(Sequence("seq-b", "Scène B", width=1080, height=1920, fps=30.0, tracks=[Track("V1", "V1", "video")],
                                      format_source="seq-main"))
    project.active_sequence_id = "seq-b"
    add_clip_to_track(project, "m1", "V1", 0.0, clip_id="b1")
    project.active_sequence_id = "seq-main"
    insert_sequence_clip(project, "seq-b", "V1", 30.0, clip_id="n1")
    project.tracks[1].locked, project.tracks[1].visible = True, False          # après les ajouts : une piste verrouillée les refuse
    return project
