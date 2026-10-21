"""Presets d'animation de texte : pop-in avec dépassement exact, glissés, rebond, mot par mot, karaoké."""

from __future__ import annotations

import numpy as np
import pytest

from core.animation import AnimationCurve
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.text_animations import TEXT_ANIMATIONS, apply_text_animation
from core.visual_effects import ClipTransform, evaluate_transform
from render_probe import lavfi_video, needs_ffmpeg, render_frame


def _title(project=None, text="FULL SEND"):
    project = project or Project(name="t", width=1080, height=1920, fps=30.0)
    clip = add_graphic_clip(project, "text", timeline_start=0.0, duration=2.0)
    update_graphic(clip, "text", text)
    return project, clip


def _scale_at(clip, t):
    return evaluate_transform(clip.transform, clip.transform_keyframes, t, clip.duration).scale


def test_pop_in_overshoots_like_ease_out_back_then_settles():
    _project, clip = _title()
    apply_text_animation(clip, "pop_in")
    samples = [_scale_at(clip, t) for t in np.linspace(0.0, 0.22, 45)]
    assert samples[0] == pytest.approx(0.3) and samples[-1] == pytest.approx(1.0)
    assert max(samples) == pytest.approx(1.0 + 0.1 * 0.7, abs=0.004)     # easeOutBack : +10 % de l'écart
    assert _scale_at(clip, 1.5) == pytest.approx(1.0)


@pytest.mark.parametrize("preset", TEXT_ANIMATIONS)
def test_every_preset_ends_on_the_static_values(preset):
    _project, clip = _title()
    clip.transform = ClipTransform(position_x=0.1, position_y=-0.2, opacity=0.9)
    apply_text_animation(clip, preset)
    end = evaluate_transform(clip.transform, clip.transform_keyframes, 1.99, clip.duration)
    if preset != "bounce":
        assert (end.position_x, end.position_y, end.scale, end.opacity) == pytest.approx((0.1, -0.2, 1.0, 0.9))
    if preset in ("word_by_word", "typewriter", "karaoke"):
        assert clip.graphic.word_reveal in ("word", "typewriter", "karaoke")
        curve = AnimationCurve([kf for kf in clip.animation if kf.property_name == "graphic.reveal"])
        assert curve.evaluate(0.0) == 0.0 and curve.evaluate(1.99) == pytest.approx(1.0, abs=0.01)


def test_reapplying_replaces_only_the_properties_it_animates():
    _project, clip = _title()
    apply_text_animation(clip, "word_by_word")
    apply_text_animation(clip, "pop_in")
    names = {kf.property_name for kf in clip.transform_keyframes}
    assert names == {"scale", "opacity"} and any(kf.property_name == "graphic.reveal" for kf in clip.animation)
    apply_text_animation(clip, "karaoke")
    assert len([kf for kf in clip.animation if kf.property_name == "graphic.reveal"]) == 2


@needs_ffmpeg
def test_the_export_follows_the_overshoot_of_a_pop_in(tmp_path):
    """La courbe « back » passe telle quelle dans l'expression FFmpeg : l'image exportée dépasse puis se pose."""
    media = lavfi_video(tmp_path / "w.mp4", "color=c=white", size=(64, 64), seconds=1.0)
    asset = MediaAsset(id="a", path=str(media), name="w", duration=1.0, width=64, height=64, fps=25.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0)
    clip.transform = ClipTransform(scale=0.5)
    apply_text_animation(clip, "pop_in")                      # marche aussi sur un clip : mêmes images-clés
    project = Project(name="p", width=200, height=200, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    plan = build_render_plan(project)

    def width_at(t):
        image = render_frame(plan, 200, 200, t)
        return int((image[100, :, 0] > 128).sum())

    peak = max(width_at(t) for t in (0.12, 0.16))
    settled = width_at(0.6)
    assert settled == pytest.approx(100, abs=2)
    assert peak >= settled + 4                                 # ~107 px au sommet du dépassement
