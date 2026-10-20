"""Projets déterministes pour les tests de ``describe`` : identifiants explicites, aucun fichier, aucun hasard."""

from __future__ import annotations

from dataclasses import replace

from core.animation import Keyframe
from core.beat_grid import BeatGrid
from core.canvas_guides import Guide, GuideOrientation
from core.audio_automation import AutomationPoint, DuckingConfig, DuckingSidechain, TrackAutomation
from core.audio_buses import add_bus, add_bus_insert, route_track
from core.audio_effects_model import add_audio_effect_to_clip, add_track_insert
from core.color_grading import ColorGrade, ColorGradingService, ColorPresetCategory, LUTResource, Wheel, make_color_preset
from core.composition import (
    Composition, CompositionGraph, EffectsNode, GradeNode, GraphicNode, KeyNode, MaskNode, MediaNode, MergeNode, OutputNode,
    SolidNode, TransformNode,
)
from core.composition_ops import create_composition
from core.compositing import ChromaKey, Compositing, Mask, MaskKeyframe, MaskShape
from core.effects_model import ClipEffect, EffectType, add_effect_to_clip
from core.graphics import GraphicOverlay, GraphicType, LayerLayout, ShapeKind, add_graphic_clip
from core.keyframe_editing import add_keyframe
from core.leaderboard import Leaderboard, LeaderboardRow, leaderboard_to_dict
from core.motion_blur import MotionBlurSettings
from core.node_graph import NodeLink
from core.project_model import MediaAsset, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script
from core.sequences import insert_sequence_clip
from core.time_ops import add_speed_point
from core.text_style import TextAlignment, TextStyle
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
                Track("S1", "S1", "subtitle"), Track("A2", "A2", "audio", audio_role="voice")],
    )
    add_clip_to_track(project, "m1", "V1", 0.0, clip_id="c1")
    add_clip_to_track(project, "m1", "V1", 10.0, clip_id="c2")
    add_clip_to_track(project, "m1", "V2", 0.0, clip_id="c3")
    add_clip_to_track(project, "m2", "A1", 0.0, clip_id="c4")
    add_transition(project, "c1", "c2", TransitionType.DISSOLVE, 0.5, transition_id="t1")
    add_subtitle_clip(project, "Bonjour", 1.0, 2.0, track_id="S1", clip_id="s1")
    add_graphic_clip(project, GraphicType.TEXT, timeline_start=1.0, duration=3.0, clip_id="g1")
    add_clip_to_track(project, "m1", "V1", 50.0, clip_id="c5")
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
    _add_effects_and_color(project)
    _add_composition(project)
    _add_audio_chain(project)
    _add_style(project)
    _add_tracking(project)
    _use_stable_ids(project)
    project.sequences.append(Sequence("seq-b", "Scène B", width=1080, height=1920, fps=30.0, tracks=[Track("V1", "V1", "video")],
                                      format_source="seq-main"))
    project.active_sequence_id = "seq-b"
    add_clip_to_track(project, "m1", "V1", 0.0, clip_id="b1")
    project.active_sequence_id = "seq-main"
    insert_sequence_clip(project, "seq-b", "V1", 30.0, clip_id="n1")
    _add_structure(project)                                                  # après seq-b : la source Multicam est la troisième séquence
    project.tracks[1].locked, project.tracks[1].visible = True, False          # après les ajouts : une piste verrouillée les refuse
    return project


def _add_effects_and_color(project: Project) -> None:
    """Effets de clip (dont un paramètre animé), compositing (masque, incrustation, mode de fusion), étalonnage par nœuds, préréglage."""
    add_effect_to_clip(project, "c1", EffectType.BLUR, effect_id="fx1", params={"intensity": 4.0})
    add_effect_to_clip(project, "c1", EffectType.VIGNETTE, effect_id="fx2", params={"intensity": 0.7}, enabled=False)
    add_keyframe(project, "c1", "fx.fx2.intensity", 0.5, 0.2)
    add_keyframe(project, "c1", "fx.fx2.intensity", 2.0, 0.9)
    clips = {clip.id: clip for track in project.tracks for clip in track.clips}
    clips["c1"].compositing = Compositing(
        masks=(Mask(shape=MaskShape.ELLIPSE, id="mk1", position_x=0.4, feather=0.1, inverted=True, name="Visage"),
               Mask(shape=MaskShape.POLYGON, id="mk2", points=((0.1, 0.1), (0.9, 0.1), (0.5, 0.9))),
               Mask(shape=MaskShape.PATH, id="mk3", points=((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)),
                    handles=((0.0, 0.0, 0.1, 0.0), (0.0, 0.0, 0.0, 0.1), (0.1, 0.0, 0.0, 0.0)))),
        chroma_key=ChromaKey(enabled=True, color="#0000FF", tolerance=0.2), blend_mode="screen")
    add_keyframe(project, "c1", "mask.mk1.feather", 0.0, 0.0)
    add_keyframe(project, "c1", "mask.mk1.feather", 1.0, 0.3)
    script = {"kut_script": 1, "ops": [
        {"op": "set_color_grade", "clip": "c1", "exposure": 0.5, "contrast": 0.1, "saturation": 1.2},
        {"op": "set_color_curve", "clip": "c1", "channel": "master", "index": 5, "output": 0.4},
        {"op": "set_color_wheel", "clip": "c1", "wheel": "lift", "r": 0.1, "y": -0.05},
        {"op": "add_color_node", "clip": "c1", "id": "n2", "label": "Peau"},
        {"op": "set_color_grade", "clip": "c1", "node": "n2", "temperature": 12.0},
        {"op": "set_color_qualifier", "clip": "c1", "node": "n2", "hue_center": 20.0, "hue_width": 40.0},
        {"op": "add_color_window", "clip": "c1", "node": "n2", "shape": "ellipse", "id": "w1", "width": 0.3},
        {"op": "set_color_detail", "clip": "c1", "node": "n2", "blur": 2.0, "sharpen": 0.5},
        {"op": "branch_color_node", "clip": "c1", "node": "n2", "kind": "layer", "id": "n3"},
    ]}
    session = Session(project)
    report = apply_script(session, parse_script(script))
    assert report.ok, report
    service = ColorGradingService()
    lut = LUTResource("luts/look.cube", "Look", "a" * 40, size=1234)
    service.set_grade(project, "c1", service.get_grade(project, "c1", "n1").with_lut(lut), "n1")
    project.color_presets.append(make_color_preset(
        preset_id="p1", name="Chaud", description="Tons chauds", category=ColorPresetCategory.WARM,
        grade=ColorGrade(temperature=30.0, gain=Wheel(r=0.1))))


def _add_composition(project: Project) -> None:
    clip = create_composition(project, "V2", 40.0, 4.0, "Compo")
    clip.id = "x1"
    nodes = (
        MediaNode("a", asset_id="m1", start=0.0, source_in=0.0, source_out=4.0, fill=True, pan_x=0.2, gain_db=-3.0),
        SolidNode("s", color="#112233"),
        GraphicNode("g", graphic=GraphicOverlay(type=GraphicType.TEXT, text="Titre"), start=1.0, duration=2.0,
                    transform_keyframes=(Keyframe("position_x", 0.0, 0.1, id="gk1"),),
                    animation=(Keyframe("graphic.width", 0.0, 500.0, id="gk2"),)),
        TransformNode("t", keyframes=(Keyframe("scale", 0.0, 1.0, id="tk1"),)),
        MergeNode("mg", blend="screen", opacity=0.7),
        EffectsNode("e", effects=(ClipEffect("fx9", EffectType.BLUR, params={"intensity": 3.0}),)),
        GradeNode("gr", grade=ColorGrade(exposure=0.3)),
        MaskNode("mk", masks=(Mask(id="cm1", shape=MaskShape.RECTANGLE, keyframes=(MaskKeyframe("feather", 0.5, 0.2),)),)),
        KeyNode("kn", key=ChromaKey(enabled=True)),
        OutputNode(),
    )
    links = (NodeLink("a", "t", 0), NodeLink("t", "mg", 0), NodeLink("s", "mg", 1), NodeLink("mg", "e", 0), NodeLink("e", "gr", 0),
             NodeLink("gr", "mk", 0), NodeLink("mk", "kn", 0), NodeLink("kn", "output", 0))
    clip.composition = Composition(graph=CompositionGraph(nodes=nodes, links=links), duration=4.0,
                                   animation=(Keyframe("mask.cm1.feather", 0.5, 0.2, id="ck1"),))


def _add_audio_chain(project: Project) -> None:
    """Effets audio de clip, inserts de piste et de bus, automation, ducking."""
    add_audio_effect_to_clip(project, "c4", "noise_reduce", effect_id="afx1")
    add_audio_effect_to_clip(project, "c4", "compressor", effect_id="afx2", params={"ratio": 3.0}, enabled=False)
    add_track_insert(project, "A1", "compressor", effect_id="ti1", params={"ratio": 4.0})
    add_bus_insert(project, "b1", "compressor", effect_id="bi1", params={"ratio": 2.0})
    track = next(item for item in project.tracks if item.id == "A1")
    track.automation = TrackAutomation(points=[AutomationPoint(0.0, 0.0), AutomationPoint(2.0, -6.0, 0.5)])
    track.ducking_config = DuckingConfig(threshold_db=-18.0, reduction_db=10.0)
    project.active_sequence.ducking_sidechains.append(DuckingSidechain("dk1", "A1", "A2", DuckingConfig(reduction_db=9.0)))


def _run(project: Project, ops: list[dict]) -> None:
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report


def _add_style(project: Project) -> None:
    """Le style des calques graphiques (texte animé, néon, ombre, forme) et celui des sous-titres."""
    clips = {clip.id: clip for track in project.tracks for clip in track.clips}
    clips["g1"].graphic = replace(
        clips["g1"].graphic, text="Titre", fill_color="#FFAA00", stroke_color="#112233", stroke_width=3, shadow_color="#00000080",
        shadow_offset_x=6, shadow_offset_y=-2, shadow_blur=12.5, font_family="Anton", font_size=72, motion_blur=True,
        align_h="left", align_v="top", tracking=4.5, line_spacing=1.25, bold=True, italic=True, box_text=False, autosize=True,
        background_enabled=True, background_color="#00112299", background_padding=24, background_radius=8.0, stroke_position="outside",
        word_reveal="karaoke", reveal=0.5, highlight_color="#FF00FF", highlight_words=(0, 2), word_times=(0.0, 0.5, 1.25),
        glow_color="#00FFAA", glow_radius=9.0, glow_strength=1.5, light_kind="sparks", light_seed=7, light_speed=2.0,
        light_angle=45.0, light_density=0.5)
    add_graphic_clip(project, GraphicType.SHAPE, timeline_start=2.0, duration=2.0, clip_id="g2", graphic=GraphicOverlay(
        type=GraphicType.SHAPE, shape=ShapeKind.POLYGON, polygon_sides=5, corner_radius=12.0, fill_enabled=False, fill_color="#33CC99",
        layout=LayerLayout.LEGACY, width=300, height=300))
    clips["s1"].text_style = TextStyle(
        font_family="Inter", font_size=40.0, color="#ffee00", opacity=0.9, outline_color="#101010", outline_width=3.0,
        shadow_color="#202020", shadow_offset=2.0, background_color="#112233", background_opacity=0.6, padding_x=10.0, padding_y=4.0,
        alignment=TextAlignment.TOP_LEFT, position_x=5.0, position_y=-5.0, margin_x=20.0, margin_y=30.0)


def _add_structure(project: Project) -> None:
    """Bibliothèque, guides, flou de mouvement, grille rythmique, classement et source Multicam."""
    _run(project, [
        {"op": "create_library_folder", "name": "Rushes", "id": "fld1", "color": "#AA3300"},
        {"op": "create_library_folder", "name": "Jour 1", "id": "fld2", "parent": "fld1"},
        {"op": "create_library_tag", "name": "Favori", "id": "tag1", "color": "#FFCC00"},
        {"op": "create_library_tag", "name": "Musique", "id": "tag2"},
        {"op": "move_media_to_folder", "asset": "m1", "folder": "fld2"},
        {"op": "tag_media", "asset": "m1", "tag": "tag1"},
        {"op": "tag_media", "asset": "m2", "tag": "tag1"},
        {"op": "tag_media", "asset": "m2", "tag": "tag2"},
        {"op": "add_guide", "orientation": "vertical", "position": 0.5, "id": "gd1"},
        {"op": "add_guide", "orientation": "horizontal", "position": 0.333333, "id": "gd2"},
        {"op": "set_beat_grid", "bpm": 120.0, "offset": 0.25, "beats_per_bar": 3},
        {"op": "create_multicam_source", "id": "seq_mc", "name": "Interview", "angles": [
            {"asset": "m1", "offset": 0.0, "name": "Caméra A"}, {"asset": "m2", "offset": 0.5, "name": "Son"}]},
        {"op": "insert_multicam_clip", "sequence": "seq_mc", "track": "V2", "at": 70.0, "angle": "angle-1", "source_out": 8.0, "id": "mc1"},
        {"op": "switch_multicam_angle", "at": 74.0, "angle": "angle-2", "clip": "mc1", "id": "mc2"},
        {"op": "set_multicam_audio", "sequence": "seq_mc", "mode": "fixed", "angles": ["angle-2"]},
    ])
    project.sequences[0].guides.append(Guide(GuideOrientation.VERTICAL, 0.75, locked=True, id="gd3"))
    project.sequences[0].motion_blur = MotionBlurSettings(enabled=True, shutter_angle=90.0, samples=6)
    board = Leaderboard((LeaderboardRow("1", "Alice", "9 800", "#FFD84D"), LeaderboardRow("2", "Bruno", "9 100")), start=1.0,
                        duration=5.0, top=-0.2, delay=0.4, id="lb1")
    project.sequences[0].generated_groups["lb1"] = {"kind": "leaderboard", "data": leaderboard_to_dict(board), "clips": ["g1", "g2"]}
    project.sequences[0].generated_groups["autre"] = {"kind": "inconnu", "data": {"x": 1}, "clips": []}
    source = next(item for item in project.sequences if item.id == "seq_mc")
    stable = {clip.id: f"mcs{index}" for index, clip in enumerate(c for t in source.tracks for c in t.clips)}
    for index, track in enumerate(source.tracks):                              # identifiants tirés au hasard : numérotés pour des octets stables
        for angle in source.multicam.angles:
            if angle.track_id == track.id:
                angle.track_id = f"MC{index}"
        track.id = f"MC{index}"
        for clip in track.clips:
            clip.id = stable[clip.id]


def _add_tracking(project: Project) -> None:
    """Deux trackers (points posés à la main), trois liaisons (transform, masque, ancrage) et une stabilisation, sur un plan de 10 s à 30 i/s."""
    _run(project, [
        {"op": "add_tracker", "clip": "c5", "at": 50.0, "x": 960.0, "y": 540.0, "id": "tr1", "name": "Œil"},
        {"op": "set_tracker_position", "clip": "c5", "tracker": "tr1", "at": 51.0, "x": 1000.0, "y": 540.0},
        {"op": "set_tracker_position", "clip": "c5", "tracker": "tr1", "at": 52.0, "x": 1040.0, "y": 560.0},
        {"op": "add_tracker", "clip": "c5", "at": 50.0, "x": 400.0, "y": 300.0, "id": "tr2"},
        {"op": "set_tracker_position", "clip": "c5", "tracker": "tr2", "at": 52.0, "x": 440.0, "y": 310.0},
        {"op": "update_tracker", "clip": "c5", "tracker": "tr2", "name": "Nez", "color": "#FF00AA", "show_path": False},
        {"op": "set_tracker_settings", "clip": "c5", "tracker": "tr2", "pattern_width": 64.0, "search_width": 200.0, "adapt": "fixed",
         "precision": "fast", "min_confidence": 0.5},
        {"op": "add_link", "clip": "c3", "source": "c5", "trackers": ["tr1", "tr2"], "at": 50.0, "rotation": True, "scale": True, "id": "lk1"},
        {"op": "add_link", "clip": "c1", "source": "c5", "trackers": ["tr1"], "at": 51.0, "target": "mask", "mask": "mk1", "id": "lk2"},
        {"op": "add_link", "clip": "c5", "source": "c5", "trackers": ["tr2"], "at": 50.0, "target": "anchor", "id": "lk3"},
        {"op": "set_link_enabled", "clip": "c1", "link": "lk2", "enabled": False},
        {"op": "set_stabilization", "clip": "c5", "trackers": ["tr1"], "mode": "position", "smoothing": "custom", "smoothing_frames": 8.0,
         "borders": "crop", "at": 51.0},
    ])


def _use_stable_ids(project: Project) -> None:
    """Les identifiants d'image clé sont tirés au hasard : on les numérote pour que deux projets riches donnent les mêmes octets."""
    counter = 0
    for track in project.tracks:
        for clip in track.clips:
            for name in ("transform_keyframes", "animation"):
                renumbered = []
                for keyframe in getattr(clip, name):
                    counter += 1
                    renumbered.append(replace(keyframe, id=f"kf{counter}"))
                setattr(clip, name, renumbered)
