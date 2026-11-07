"""Où la chrominance 4:2:0 d'un calque passe en RVB co-sitée (:mod:`core.chroma_siting`), dans le graphe de l'export."""

from __future__ import annotations

import pytest

from core.chroma_siting import CHROMA_SITING_444, CHROMA_SITING_SCALE, CHROMA_SITING_TO_RGBA, siting_before_rgb
from core.effects_model import ClipEffect, EffectType
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform


def _layer_graph(transform=None, effects=()):
    project = Project(name="g", width=320, height=180, fps=10)
    project.media_assets.append(MediaAsset("a", "/x/s.mkv", "src", 1.0, 320, 180, 10, "video"))
    clip = Clip("v", "a", "V1", 0.0, 0.0, 1.0)
    clip.transform = ClipTransform(**(transform or {}))
    clip.effects = list(effects)
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[clip]))
    request = ExportRequest(render_plan=build_render_plan(project), output_path="/x/o.mp4", format=ExportFormat.MP4_H264,
                            preset=ExportPreset("T", (320, 180), 18, "64k"), fps=10)
    command = ExportEngine().build_frame_command(request, 0.25)
    return command[command.index("-filter_complex") + 1]


def _effect(kind, **params):
    return ClipEffect("e" + kind.value, kind, True, params)


EQ = _effect(EffectType.COLOR_CORRECTION, brightness=0.05, contrast=1.2, saturation=1.4)


@pytest.mark.parametrize("transform, effects", [
    ({}, []),                                                    # sans échelle : la conversion suit le scale animé
    ({"position_x": 0.1}, []),                                   # déplacé
    ({"rotation": 15.0}, []),                                    # tourné sans effet : converti avant rotate
    ({}, [EQ]),                                                  # effet YUV : converti devant le format=rgba final
    ({}, [_effect(EffectType.SEPIA)]),                           # filtre RVB : converti devant lui
    ({}, [_effect(EffectType.GLOW, threshold=0.5, radius=3.0, intensity=1.0)]),
])
def test_an_unresized_layer_converts_its_chroma_once_with_the_siting_rule(transform, effects):
    graph = _layer_graph(transform, effects)
    assert graph.count(CHROMA_SITING_SCALE) == 1, graph


@pytest.mark.parametrize("transform, effects", [
    ({"scale": 0.7}, []),                                        # redimensionné : le scale animé de l'export le traite
    ({"scale_y": 0.7}, []),
    ({"rotation": 15.0}, [EQ]),                                  # tourné avec effet : rotate avant la conversion
])
def test_layers_the_export_resamples_keep_their_own_conversion(transform, effects):
    assert CHROMA_SITING_SCALE not in _layer_graph(transform, effects)


def test_the_conversion_is_before_rotate_and_after_the_scale_of_the_clip():
    graph = _layer_graph({"rotation": 15.0})
    assert graph.index("eval=frame") < graph.index(CHROMA_SITING_SCALE) < graph.index("rotate=")


def test_the_heat_haze_converts_with_the_same_rule_and_nothing_converts_twice():
    haze = _effect(EffectType.HEAT_HAZE, amplitude=4.0, frequency=0.07, speed=0.0, top=0.1, span=0.5)
    graph = _layer_graph({}, [haze])
    assert graph.count(CHROMA_SITING_SCALE) == 1 and "neighbor" not in graph


def test_siting_goes_before_the_first_rgb_filter_only():
    chain = "lut=y=val,gblur=sigma=2,colorchannelmixer=.3:.7:.2,lutrgb=r=val,format=rgba"
    assert siting_before_rgb(chain) == (
        f"lut=y=val,gblur=sigma=2,{CHROMA_SITING_TO_RGBA},colorchannelmixer=.3:.7:.2,lutrgb=r=val,format=rgba")
    assert siting_before_rgb("format=rgba,split") == f"{CHROMA_SITING_TO_RGBA},split"
    assert siting_before_rgb("lut=y=val") == "lut=y=val"          # rien d'RVB : inchangé
    assert siting_before_rgb("format=rgba64le,eq=gamma=1") == "format=rgba64le,eq=gamma=1"


def test_only_this_chain_uses_accurate_rounding():
    """``accurate_rnd`` change l'arrondi de toute conversion YUV → RVB : la composition, l'étage de sortie et les
    conversions des effets ne le portent pas."""
    graph = _layer_graph({}, [EQ])
    assert graph.count("accurate_rnd") == 1


def test_the_conversion_goes_through_one_16_bit_444_step_and_never_a_bare_scale_before_rgba():
    """Défaut de swscale 6.1 / 7.1 (écart max 196, 0,9 % des pixels) : ``scale=…,format=rgba`` direct est faux ; le
    16 bits intermédiaire corrige. Non rejouable sur FFmpeg 9 (la chaîne directe y est juste) : on vérifie la forme."""
    # ``yuva`` et non ``yuv`` : un calque RVBA (fond transparent, composition vide) perdrait son alpha, rendu noir opaque.
    assert CHROMA_SITING_TO_RGBA == f"{CHROMA_SITING_SCALE},format=yuva444p16le,format=rgba"
    chains = [siting_before_rgb("format=rgba,split"), siting_before_rgb("lut=y=val,lutrgb=r=val"),
              siting_before_rgb("colorchannelmixer=.3:.7:.2,format=rgba"), _layer_graph({}, []),
              _layer_graph({}, [EQ]), _layer_graph({}, [_effect(EffectType.SEPIA)])]
    for chain in chains:
        assert chain.count("yuva444p16le") == 1, chain
        assert f"{CHROMA_SITING_SCALE},format=yuva444p16le,format=rgba" in chain, chain
        assert f"{CHROMA_SITING_SCALE},format=rgba" not in chain, chain
    assert siting_before_rgb("format=rgba,split").count("format=rgba") == 1             # pas de second format=rgba
    assert siting_before_rgb("lut=y=val,lutrgb=r=val").startswith(f"lut=y=val,{CHROMA_SITING_444},format=rgba,")
