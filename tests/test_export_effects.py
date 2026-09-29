"""Tests du rendu FFmpeg des effets visuels de clip."""

from __future__ import annotations

from dataclasses import replace

from core.effects_model import EffectType, create_effect
from core.color_grading import ColorGrade
from core.export_engine import _build_layer_filter
from core.render_plan import RenderLayer


def _layer_with_effects():
    return RenderLayer(
        clip_id="clip-effects", asset_id="asset-1", track_id="V1", track_index=0,
        source_path="/tmp/source.mp4", source_in=0.0, source_out=3.0,
        timeline_start=0.0, timeline_end=3.0,
        effects=(
            create_effect(
                EffectType.COLOR_CORRECTION, effect_id="color",
                params={"brightness": 0.2, "contrast": 1.3, "saturation": 0.7},
            ),
            create_effect(EffectType.BLUR, effect_id="blur", params={"intensity": 4}),
            create_effect(EffectType.SHARPEN, effect_id="sharp", params={"intensity": 1.5}),
            create_effect(EffectType.VIGNETTE, effect_id="vignette", params={"intensity": 0.5}),
            create_effect(EffectType.BLACK_AND_WHITE, effect_id="bw"),
            create_effect(EffectType.SEPIA, effect_id="sepia"),
            create_effect(EffectType.BLUR, effect_id="disabled", enabled=False),
        ),
    )


def test_video_filter_applies_enabled_effects_in_model_order():
    filter_graph = _build_layer_filter(0, _layer_with_effects(), 0, 1280, 720, 30)

    expected = [
        "eq=brightness=0.2:contrast=1.3:saturation=0.7",
        "gblur=sigma=4.0",
        "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=1.5",
        "vignette=angle=0.392699",
        "hue=s=0",
        "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    ]
    positions = [filter_graph.index(fragment) for fragment in expected]

    assert positions == sorted(positions)
    assert "gblur=sigma=2.0" not in filter_graph
    assert positions[-1] < filter_graph.index("format=rgba")


def test_color_grade_runs_after_existing_visual_effects_deterministically():
    base = _layer_with_effects()
    layer = replace(
        base, color_grade=ColorGrade(exposure=0.5, saturation=1.2)
    )

    filter_graph = _build_layer_filter(0, layer, 0, 1280, 720, 30)

    visual_effect = filter_graph.index("gblur=sigma=4.0")
    color_grade = filter_graph.index("eq=contrast=1.0:saturation=1.2:gamma=")
    alpha_format = filter_graph.index("format=rgba")
    assert visual_effect < color_grade < alpha_format
