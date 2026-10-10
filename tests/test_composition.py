"""Composition nodale (étape 4 de l'ADR-0002) : modèle, fichier, rendu exact par l'export, conversion des calques.

Le rendu est mesuré sur le vrai FFmpeg, par le même chemin que l'export (``render_probe.render_frame``) ; la conversion
est jugée sur l'image : celle des calques, puis celle de leur composition.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade
from core.compositing import ChromaKey, Compositing, Mask, MaskShape
from core.composition import (
    OUTPUT_ID,
    Composition,
    CompositionGraph,
    EffectsNode,
    GradeNode,
    KeyNode,
    MaskNode,
    MediaNode,
    MergeNode,
    SolidNode,
    TransformNode,
)
from core.composition_ops import CompositionError, convert_to_composition, create_composition
from core.effects_model import ClipEffect, EffectType
from core.node_graph import NodeGraphError
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform

W, H = 64, 36


def _graph(*nodes, links=()) -> CompositionGraph:
    graph = CompositionGraph.empty()
    for node in nodes:
        graph = graph.with_added(node)
    for source, target, *port in links:
        graph = graph.connected(source, target, port[0] if port else 0)
    return graph


def _project(media, graph, *, duration=1.0, has_audio=False, **clip) -> Project:
    source_in = clip.pop("source_in", 0.0)
    comp = Clip("c", "", "V1", 0.0, source_in, source_in + duration, **clip)
    comp.composition = Composition(graph, max(duration, 1.0))
    assets = [MediaAsset("v", str(media), "v", 2.0, W, H, 25.0, "video", has_audio=has_audio)] if media else []
    return Project("p", width=W, height=H, fps=25.0, media_assets=assets,
                   tracks=[Track("V1", "V1", "video", clips=[comp])])


@pytest.fixture
def red(tmp_path):
    return lavfi_video(tmp_path / "red.mp4", "color=c=0xC81E1E:d=2", size=(W, H), seconds=2.0)


# --- modèle ----------------------------------------------------------------------------------------------------------


def test_a_composition_graph_has_one_output_and_typed_inputs():
    graph = _graph(SolidNode("s"), MergeNode("f"), TransformNode("t"))
    with pytest.raises(NodeGraphError):
        graph.connected("s", "t", 1)                       # une transformation n'a qu'une entrée
    with pytest.raises(NodeGraphError):
        graph.connected("t", "s")                          # une source n'en a aucune
    with pytest.raises(NodeGraphError):
        graph.connected("t", "f").connected("f", "t")      # cycle
    with pytest.raises(NodeGraphError):
        graph.with_added(SolidNode(OUTPUT_ID))
    linked = graph.connected("s", "f", 0).connected("t", "f", 0)
    assert linked.input_of("f", 0) == "t", "relier une entrée remplace ce qui y était"
    chain = _graph(SolidNode("s"), TransformNode("t"), MaskNode("k"), links=(("s", "t"), ("t", "k"), ("k", OUTPUT_ID)))
    assert chain.without("t").input_of("k") == "s", "retirer un nœud garde la chaîne reliée"
    with pytest.raises(NodeGraphError):
        chain.without(OUTPUT_ID)
    assert [node.id for node in chain.rendered()] == ["s", "t", "k", OUTPUT_ID]
    assert [node.id for node in _graph(SolidNode("s"), SolidNode("seul"),
                                       links=(("s", OUTPUT_ID),)).rendered()] == ["s", OUTPUT_ID]


def test_a_composition_survives_the_project_file(tmp_path):
    from core.project_io import CURRENT_VERSION, load_project, save_project

    mask = Mask(shape=MaskShape.ELLIPSE, width=0.4)
    graph = _graph(
        MediaNode("m", "v", 0.25, 0.5, 1.5, "plan", gain_db=-6.0, fill=True, pan_x=0.3), SolidNode("s", "#336699"),
        TransformNode("t", ClipTransform(scale=0.5, rotation=10.0)), MaskNode("k", (mask,)),
        KeyNode("i", ChromaKey(enabled=True, tolerance=0.2)),
        EffectsNode("e", (ClipEffect("fx", EffectType.BLUR, True, {"intensity": 3.0}),)),
        GradeNode("c", ColorGrade(exposure=0.4)), MergeNode("f", "screen", 0.7),
        links=(("m", "k"), ("k", "t"), ("t", "e"), ("e", "c"), ("c", "i"), ("s", "f", 0), ("i", "f", 1),
               ("f", OUTPUT_ID)),
    )
    project = _project(tmp_path / "absent.mp4", graph, duration=2.0)
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    assert f'"version": {CURRENT_VERSION}' in path.read_text() and CURRENT_VERSION == 17
    loaded = load_project(str(path)).tracks[0].clips[0]
    assert loaded.composition == project.tracks[0].clips[0].composition


def test_a_damaged_composition_opens_with_what_still_reads():
    from core.project_io import _composition_codec
    from core.composition import composition_from_dict, composition_to_dict

    graph = _graph(SolidNode("s"), TransformNode("t"), links=(("s", "t"), ("t", OUTPUT_ID)))
    raw = composition_to_dict(Composition(graph, 2.0), _composition_codec())
    raw["nodes"].append({"id": "x", "kind": "inconnu"})
    raw["nodes"][1]["kind"] = "merge"                          # « s » devient une fusion : ses liens restent valides
    raw["links"].append(["fantôme", OUTPUT_ID, 0])
    back = composition_from_dict(raw, _composition_codec())
    assert back is not None and back.graph.output.id == OUTPUT_ID and not back.graph.has_node("x")
    assert back.graph.input_of(OUTPUT_ID) == "t" and isinstance(back.graph.node("s"), MergeNode)
    raw["nodes"][0] = {"id": OUTPUT_ID, "kind": "solid"}      # la sortie perdue, son identifiant repris
    lost = composition_from_dict(raw, _composition_codec())
    assert lost.graph.output.id != OUTPUT_ID and lost.graph.has_node(OUTPUT_ID)


# --- rendu -----------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_transformed_source_merged_over_a_solid(red):
    graph = _graph(SolidNode("bg", "#1E1EC8"), MediaNode("m", "v", 0.0, 0.0, 1.0),
                   TransformNode("t", ClipTransform(scale=0.5, position_x=0.25)), MergeNode("f"),
                   links=(("m", "t"), ("bg", "f", 0), ("t", "f", 1), ("f", OUTPUT_ID)))
    frame = render_frame(build_render_plan(_project(red, graph)), W, H, 0.5).astype(int)
    assert abs(frame[18, 48] - [200, 30, 30]).max() <= 6, "la source, réduite de moitié et décalée à droite"
    assert abs(frame[18, 8] - [30, 30, 200]).max() <= 6, "le fond, à gauche"


@needs_ffmpeg
def test_merge_modes_opacity_and_keys(red):
    grey = SolidNode("bg", "#808080")
    multiply = _graph(grey, MediaNode("m", "v", 0.0, 0.0, 1.0), MergeNode("f", "multiply", 0.5),
                      links=(("bg", "f", 0), ("m", "f", 1), ("f", OUTPUT_ID)))
    pixel = render_frame(build_render_plan(_project(red, multiply)), W, H, 0.5).astype(float)[18, 32]
    produit = np.array([200, 30, 30]) * 128 / 255.0
    assert np.abs(pixel - (0.5 * 128 + 0.5 * produit)).max() <= 4, pixel
    keyed = _graph(grey, MediaNode("m", "v", 0.0, 0.0, 1.0), KeyNode("i", ChromaKey(True, "#C81E1E", 0.3, 0.05)),
                   MergeNode("f"), links=(("m", "i"), ("bg", "f", 0), ("i", "f", 1), ("f", OUTPUT_ID)))
    pixel = render_frame(build_render_plan(_project(red, keyed)), W, H, 0.5).astype(float)[18, 32]
    assert np.abs(pixel - 128).max() <= 6, "incrustée : le fond passe"


@needs_ffmpeg
def test_masks_effects_and_grades_keep_the_alpha(red):
    left = Mask(shape=MaskShape.RECTANGLE, position_x=0.25, position_y=0.5, width=0.5, height=1.0)
    graph = _graph(SolidNode("bg", "#000000"), MediaNode("m", "v", 0.0, 0.0, 1.0), MaskNode("k", (left,)),
                   EffectsNode("e", (ClipEffect("bw", EffectType.BLACK_AND_WHITE, True, {}),)),
                   GradeNode("c", ColorGrade(exposure=1.0)), MergeNode("f"),
                   links=(("m", "k"), ("k", "e"), ("e", "c"), ("bg", "f", 0), ("c", "f", 1), ("f", OUTPUT_ID)))
    frame = render_frame(build_render_plan(_project(red, graph)), W, H, 0.5).astype(int)
    inside, outside = frame[18, 10], frame[18, 54]
    assert inside.max() - inside.min() <= 6 and inside.mean() > 70, "masquée à gauche : grise et éclaircie"
    assert outside.max() <= 6, "à droite : le fond noir (l'alpha du masque a traversé effets et étalonnage)"


@needs_ffmpeg
def test_an_unconnected_or_empty_graph_is_transparent_and_a_trimmed_clip_reads_its_own_range(red):
    lower = Clip("low", "v", "V1", 0.0, 0.0, 1.0)
    graph = _graph(MediaNode("m", "v", 0.0, 0.0, 1.0), TransformNode("t"))      # rien relié à la sortie
    project = _project(red, graph)
    project.tracks.insert(0, Track("V0", "V0", "video", clips=[lower]))
    frame = render_frame(build_render_plan(project), W, H, 0.5).astype(int)
    assert abs(frame[18, 32] - [200, 30, 30]).max() <= 6, "la composition vide laisse voir dessous"
    late = _graph(SolidNode("bg", "#1E1EC8"), MediaNode("m", "v", 1.0, 0.0, 1.0), MergeNode("f"),
                  links=(("bg", "f", 0), ("m", "f", 1), ("f", OUTPUT_ID)))
    trimmed = _project(red, late, duration=0.8, source_in=1.0)                   # le clip montre 1,0 à 1,8 s
    trimmed.tracks[0].clips[0].composition = replace(trimmed.tracks[0].clips[0].composition, duration=2.0)
    frame = render_frame(build_render_plan(trimmed), W, H, 0.3).astype(int)
    assert abs(frame[18, 32] - [200, 30, 30]).max() <= 6, "à 0,3 s du clip : 1,3 s de la composition, le média"


def test_slip_and_roll_are_bounded_by_the_composition():
    """Un clip de composition n'a pas de média : glisser et rouler prennent la durée de la composition pour source."""
    from core.timeline_editing import roll_edit, slip_clip

    graph = _graph(SolidNode("s"), links=(("s", OUTPUT_ID),))
    comp, after = Clip("c", "", "V1", 0.0, 1.0, 2.0), Clip("d", "", "V1", 1.0, 0.0, 1.0)
    comp.composition = after.composition = Composition(graph, 4.0)
    project = Project("p", width=W, height=H, fps=25.0, tracks=[Track("V1", "V1", "video", clips=[comp, after])])
    slip_clip(project, "c", 10.0)
    assert (comp.source_in, comp.source_out) == pytest.approx((3.0, 4.0)), "borné par la fin de la composition"
    slip_clip(project, "c", -2.5)
    roll_edit(project, "c", "right", 1.5)
    assert comp.source_out == pytest.approx(2.0) and after.timeline_start == pytest.approx(1.5)
    assert after.source_in == pytest.approx(0.5)


def test_a_key_colour_is_six_hexadecimal_digits():
    assert ChromaKey(color="#00ff7f").color == "#00FF7F"
    assert ChromaKey(color="#ZZZZZZ").color == "#00FF00", "« chromakey=0xZZZZZZ » ferait échouer FFmpeg"


@needs_ffmpeg
def test_the_composition_plays_the_sound_of_its_media(tmp_path):
    media = tmp_path / "av.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={W}x{H}:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(media)], check=True)
    graph = _graph(MediaNode("m", "v", 0.0, 0.0, 1.0, gain_db=-6.0), links=(("m", OUTPUT_ID),))
    plan = build_render_plan(_project(media, graph, has_audio=True))
    audio = [layer for layer in plan.audio_layers if layer.nested_key]
    assert len(audio) == 1 and audio[0].nested_key.endswith(":audio")
    sub = plan.nested(audio[0].nested_key)
    assert sub.plan.audio_layers[0].gain_db == pytest.approx(-6.0)
    muted = _graph(MediaNode("m", "v", 0.0, 0.0, 1.0, muted=True), links=(("m", OUTPUT_ID),))
    assert not [layer for layer in build_render_plan(_project(media, muted, has_audio=True)).audio_layers
                if layer.nested_key]
    shown = _graph(MediaNode("m", "v", 0.0, 0.0, 1.0, gain_db=-6.0), SolidNode("s"), MergeNode("f"),
                   MediaNode("d", "v", 0.0, 0.0, 1.0), links=(("m", "f", 0), ("s", "f", 1), ("f", OUTPUT_ID)))
    for views in (None, {"c": "s"}):                       # montrer la couleur unie ne coupe pas le son du média
        plan = build_render_plan(_project(media, shown, has_audio=True), composition_views=views)
        sub = plan.nested(next(layer.nested_key for layer in plan.audio_layers if layer.nested_key))
        assert [layer.gain_db for layer in sub.plan.audio_layers] == pytest.approx([-6.0]), "« d », détaché, se tait"


@needs_ffmpeg
def test_a_preview_segment_renders_the_same_picture(red):
    from core.preview_segments import segment_plan

    graph = _graph(SolidNode("bg", "#1E1EC8"), MediaNode("m", "v", 0.0, 0.0, 1.0),
                   TransformNode("t", ClipTransform(scale=0.5)), MergeNode("f"),
                   links=(("m", "t"), ("bg", "f", 0), ("t", "f", 1), ("f", OUTPUT_ID)))
    project = _project(red, graph)
    whole = render_frame(build_render_plan(project), W, H, 0.6).astype(float)
    segment = segment_plan(project, 0.4, 0.8)
    assert any("@" in entry.key for entry in segment.nested_sequences), "la composition est fenêtrée"
    assert np.abs(render_frame(segment, W, H, 0.6).astype(float) - whole).max() <= 1


# --- conversion ------------------------------------------------------------------------------------------------------


def _layers(tmp_path) -> Project:
    from core.graphics import add_graphic_clip

    base = lavfi_video(tmp_path / "base.mp4", "testsrc2=d=2", size=(96, 54), seconds=2.0)
    top = lavfi_video(tmp_path / "top.mp4", "smptebars=d=2", size=(96, 54), seconds=2.0)
    project = Project("p", width=96, height=54, fps=25.0,
                      media_assets=[MediaAsset("a", str(base), "a", 2.0, 96, 54, 25.0, "video"),
                                    MediaAsset("b", str(top), "b", 2.0, 96, 54, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("low", "a", "V1", 0.0, 0.0, 1.5)]),
                              Track("V2", "V2", "video", clips=[Clip("high", "b", "V2", 0.25, 0.25, 1.25)])])
    high = project.tracks[1].clips[0]
    high.transform = ClipTransform(scale=0.6, position_x=0.15, rotation=12.0, opacity=0.85)
    high.compositing = Compositing(masks=(Mask(shape=MaskShape.ELLIPSE, width=0.8, height=0.9, feather=0.05),),
                                   blend_mode="screen")
    high.color_grade = ColorGrade(exposure=0.3, saturation=1.3)
    shape = add_graphic_clip(project, "shape", timeline_start=0.1, duration=1.0)
    shape.compositing = replace(shape.compositing, blend_mode="multiply")
    return project


@needs_ffmpeg
def test_converting_layers_keeps_their_picture(tmp_path):
    """Deux plans (le second réduit, tourné, masqué, étalonné, en Écran) et une forme en Produit : avant et après la
    conversion, la même image, au rééchantillonnage près."""
    project = _layers(tmp_path)
    before = [render_frame(build_render_plan(project), 96, 54, t).astype(float) for t in (0.3, 0.9)]
    ids = [clip.id for track in project.tracks for clip in track.clips]
    result = convert_to_composition(project, ids, "Plans")
    comp = result.clip
    assert [clip.id for track in project.tracks for clip in track.clips] == [comp.id]
    assert comp.track_id == "V1" and comp.timeline_start == 0.0 and comp.source_out == pytest.approx(1.5)
    kinds = sorted(type(node).__name__ for node in comp.composition.graph.rendered())
    assert kinds.count("MergeNode") == 2 and "MaskNode" in kinds and "TransformNode" in kinds
    for t, image in zip((0.3, 0.9), before):
        after = render_frame(build_render_plan(project), 96, 54, t).astype(float)
        error = np.abs(after - image)
        assert error.mean() < 1.5 and np.percentile(error, 99) < 12, (t, error.mean(), np.percentile(error, 99))


def test_conversion_refuses_what_a_composition_cannot_carry(tmp_path):
    project = _layers(tmp_path)
    low = project.tracks[0].clips[0]
    low.time_remapping = replace(low.time_remapping, speed=2.0)
    with pytest.raises(CompositionError, match="vitesse"):
        convert_to_composition(project, [low.id])
    shape = project.tracks[2].clips[0] if len(project.tracks) > 2 else None
    graphics_only = [clip.id for track in project.tracks if track.type == "graphics" for clip in track.clips]
    with pytest.raises(CompositionError, match="vidéo"):
        convert_to_composition(project, graphics_only)
    assert shape is None or shape in project.tracks[2].clips, "rien n'est modifié"


def test_an_empty_composition_is_created_where_the_track_is_free(tmp_path):
    project = Project("p", width=W, height=H, fps=25.0, tracks=[Track("V1", "V1", "video")])
    clip = create_composition(project, "V1", 2.0, 3.0, "Vide")
    assert clip.composition.duration == pytest.approx(3.0) and clip.composition.graph.rendered()[0].id == OUTPUT_ID
    with pytest.raises(CompositionError):
        create_composition(project, "V1", 4.0, 1.0)


@needs_ffmpeg
def test_the_preview_can_show_any_node_instead_of_the_output(red):
    """Page Composition : le viewer montre le nœud choisi (comme le viewer de Fusion) ; jamais à l'export."""
    from core.composition import CompositionView
    from core.preview_segments import segment_plan

    graph = _graph(SolidNode("bg", "#1E1EC8"), MediaNode("m", "v", 0.0, 0.0, 1.0),
                   TransformNode("t", ClipTransform(scale=0.5)), MergeNode("f"),
                   links=(("m", "t"), ("bg", "f", 0), ("t", "f", 1), ("f", OUTPUT_ID)))
    project = _project(red, graph)
    source = segment_plan(project, 0.0, 1.0, grade_overrides={"c": CompositionView("m")})
    frame = render_frame(source, W, H, 0.5).astype(int)
    assert abs(frame[2, 2] - [200, 30, 30]).max() <= 6, "la source seule, plein cadre (avant sa transformation)"
    solid = render_frame(segment_plan(project, 0.0, 1.0, grade_overrides={"c": CompositionView("bg")}),
                         W, H, 0.5).astype(int)
    assert abs(solid[18, 32] - [30, 30, 200]).max() <= 6, "le fond seul"
    assert not any("#" in entry.key for entry in build_render_plan(project).nested_sequences), "export : la sortie"


def test_adding_a_node_puts_it_where_it_belongs():
    """Comme dans Fusion : un traitement se glisse après le nœud choisi, une source vient par-dessus par une fusion."""
    graph = CompositionGraph.empty().inserted(MediaNode("m", "v", 0.0, 0.0, 1.0))
    assert graph.input_of(OUTPUT_ID) == "m", "composition vide : la source va à la sortie"
    graph = graph.inserted(TransformNode("t"), after="m")
    assert graph.input_of("t") == "m" and graph.input_of(OUTPUT_ID) == "t"
    graph = graph.inserted(SolidNode("s"))
    merge = graph.input_of(OUTPUT_ID)
    assert isinstance(graph.node(merge), MergeNode)
    assert graph.input_of(merge, 0) == "t" and graph.input_of(merge, 1) == "s", "la source au premier plan"
    graph = graph.inserted(EffectsNode("e"))
    assert graph.input_of(OUTPUT_ID) == "e" and graph.input_of("e") == merge, "sans nœud choisi : avant la sortie"
    graph = graph.inserted(MaskNode("k"), after="m")
    assert graph.input_of("k") == "m" and graph.input_of("t") == "k"
