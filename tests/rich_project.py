"""Projet synthétique complet, construit uniquement par les API de ``core`` (aucun média réel).

Il touche tous les sous-systèmes sérialisés : médias, pistes vidéo / audio / sous-titres, keyframes de
transform et d'animation générique, effets vidéo et audio, étalonnage, masques, remappage temporel, rôles
et automation audio avec ducking, calques de motion graphics (texte, forme, groupe, nul, adjustment) avec
parentage, tracking avec liaison et stabilisation, repère, guide, séquence supplémentaire, bibliothèque
et presets. Sert aux tests d'aller-retour ``.kut``, de mutation du format et d'intégration.
"""

from __future__ import annotations

from dataclasses import replace

from core import keyframe_editing as keyframes
from core import mograph_layers as layers
from core import timeline_operations as ops
from core import tracking_ops as tracking
from core.audio_automation import AudioAutomationService, DuckingConfig, TrackRole
from core.audio_effects_model import add_audio_effect_to_clip
from core.canvas_guides import add_guide
from core.color_grading import ColorGrade, ColorGradingService, make_user_color_preset
from core.compositing import Compositing, Mask, MaskShape
from core.effects_model import add_effect_to_clip, create_effect
from core.library_organization import LibraryOrganization
from core.project_model import MediaAsset, Marker, Project, Track
from core.sequences import create_sequence
from core.tracking_model import Sample, SampleStatus


def _video_asset(index: int) -> MediaAsset:
    return MediaAsset(f"av{index}", f"/nonexistent/v{index}.mp4", f"V{index}", 20.0, 1920, 1080, 30.0, "video", True)


def build_rich_project() -> Project:
    project = Project(name="Riche", width=1920, height=1080, fps=30.0)
    project.media_assets += [
        _video_asset(1), _video_asset(2),
        MediaAsset("aa1", "/nonexistent/a1.wav", "A1", 30.0, 0, 0, 0.0, "audio", True),
        MediaAsset("as1", "", "S1", 5.0, 0, 0, 0.0, "subtitle", False),
    ]
    project.tracks += [Track("V1", "V1", "video"), Track("V2", "V2", "video"),
                       Track("A1", "A1", "audio"), Track("A2", "A2", "audio"), Track("S1", "S1", "subtitle")]
    first = ops.add_clip_to_track(project, "av1", "V1", 0.0)
    second = ops.add_clip_to_track(project, "av2", "V1", 25.0)
    third = ops.add_clip_to_track(project, "av2", "V2", 2.0)
    music = ops.add_clip_to_track(project, "aa1", "A1", 0.0)
    ops.add_clip_to_track(project, "aa1", "A2", 0.0)
    ops.add_subtitle_clip(project, "Bonjour", 1.0, 3.0, "S1")

    ops.set_transform_keyframe(project, first.id, "scale", 0.0, 1.0)
    ops.set_transform_keyframe(project, first.id, "scale", 2.0, 1.5)
    ops.set_transform_keyframe(project, first.id, "opacity", 1.0, 0.5)
    add_effect_to_clip(project, first.id, "blur")
    add_audio_effect_to_clip(project, music.id, "compressor")
    ColorGradingService().set_grade(project, first.id, ColorGrade.identity().with_field("exposure", 0.3))

    compositing = Compositing(masks=(Mask(shape=MaskShape.ELLIPSE, feather=0.1, name="m1"),))
    third.compositing = compositing
    mask_id = compositing.masks[0].id
    keyframes.add_keyframe(project, third.id, f"mask.{mask_id}.feather", 0.0, 0.1)
    keyframes.add_keyframe(project, third.id, f"mask.{mask_id}.feather", 2.0, 0.4)
    ops.set_clip_speed(project, second.id, 2.0)

    audio = AudioAutomationService()
    audio.set_track_role(project, "A1", TrackRole.MUSIC)
    audio.set_track_role(project, "A2", TrackRole.VOICE)
    audio.add_automation_point(project, "A1", 1.0, -6.0, 0.2)
    audio.add_ducking_sidechain(project, "A1", "A2", config=DuckingConfig())

    title = layers.add_layer(project, "text", at=1.0, duration=4.0)
    shape = layers.add_layer(project, "shape", at=1.0, duration=4.0, shape="ellipse")
    adjustment = layers.add_layer(project, "adjustment", at=0.0, duration=6.0)
    controller = layers.add_layer(project, "null", at=0.0, duration=6.0)
    layers.set_parent(project, title.id, controller.id, at_time=1.0)
    layers.group_layers(project, [shape.id], name="G")
    adjustment.effects = [create_effect("blur")]

    tracker = tracking.add_tracker(project, first.id, timeline_time=0.0, x=500.0, y=300.0)
    samples = {i: Sample(500.0 + 2 * i, 300.0 + i, 0.9, SampleStatus.TRACKED) for i in range(1, 60)}
    clip = tracking.find_clip_and_track(project, first.id)[0]
    tracking._put_tracker(clip, replace(tracker, data=tracker.data.with_samples(samples)))
    tracking.add_link(project, title.id, first.id, [tracker.id], timeline_time=0.0)
    tracking.set_stabilization(project, first.id, enabled=True, tracker_ids=(tracker.id,))

    project.markers.append(Marker("m-1", 3.0, "M", "todo"))
    add_guide(project.active_sequence, "vertical", 0.5)
    create_sequence(project, "Seq2")

    library = LibraryOrganization(project)
    folder = library.create_folder("F")
    tag = library.create_tag("T")
    library.move_asset("av1", folder.id)
    library.add_tag_to_asset("av1", tag.id)
    project.color_presets.append(make_user_color_preset(
        name="P", description="d", grade=ColorGrade.identity().with_field("contrast", 0.2)))
    return project
