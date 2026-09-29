"""Tests de l'aperçu fidele et du cache de rendu (tache 30)."""

from __future__ import annotations


def _project_with_clip():
    from core.project_factory import create_default_project
    from core.render_plan import build_render_plan

    project = create_default_project()
    plan = build_render_plan(project)
    return project, plan


def test_filter_graph_matches_export():
    """L'apercu utilise exactement le filter_complex de l'export."""
    import tempfile

    from core.export_engine import ExportEngine
    from core.filter_graph import build_filter_complex

    _project, plan = _project_with_clip()
    tmp = tempfile.NamedTemporaryFile(suffix=".srt", delete=False)
    tmp.write(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    tmp.close()
    expected = ExportEngine._build_filter_complex(plan, 960, 540, 30, tmp.name)
    actual = build_filter_complex(plan, 960, 540, 30, tmp.name)
    assert actual == expected


def test_fingerprint_stable_and_sensitive():
    from core.filter_graph import fingerprint_plan

    _project, plan = _project_with_clip()
    base = {"width": 1920, "height": 1080, "fps": 30, "quality": "standard"}
    assert fingerprint_plan(plan, **base) == fingerprint_plan(plan, **base)
    other = dict(base, quality="draft")
    assert fingerprint_plan(plan, **base) != fingerprint_plan(plan, **other)


def test_effect_change_changes_fingerprint():
    from core.effects_model import EffectType, create_effect
    from core.filter_graph import fingerprint_plan
    from core.render_plan import RenderLayer

    _project, plan = _project_with_clip()
    base = {"width": 1920, "height": 1080, "fps": 30, "quality": "standard"}
    before = fingerprint_plan(plan, **base)
    layer = plan.video_layers[0]
    effect = create_effect(EffectType.BLUR, params={"intensity": 5.0})
    patched = RenderLayer(
        clip_id=layer.clip_id,
        asset_id=layer.asset_id,
        track_id=layer.track_id,
        track_index=layer.track_index,
        source_path=layer.source_path,
        source_in=layer.source_in,
        source_out=layer.source_out,
        timeline_start=layer.timeline_start,
        timeline_end=layer.timeline_end,
        source_fps=layer.source_fps,
        transform=layer.transform,
        transform_keyframes=layer.transform_keyframes,
        time_remapping=layer.time_remapping,
        effects=(effect,),
        color_grade=layer.color_grade,
    )
    from dataclasses import replace

    plan2 = replace(plan, video_layers=(patched,) + tuple(plan.video_layers[1:]))
    assert fingerprint_plan(plan2, **base) != before
