"""Emplacements de template : carte rendue tant que le média manque, remplissage qui garde timing et animation."""

from __future__ import annotations

import numpy as np
import pytest

from core.animation import InterpolationType
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.template_slots import SlotError, empty_slots, fill_slot, slot_at
from core.visual_effects import TransformKeyframe
from render_probe import lavfi_video, needs_ffmpeg, render_frame

W, H = 108, 192


def _project(tmp_path) -> Project:
    slots = [Clip(id=f"s{n}", asset_id="", track_id="V1", timeline_start=float(n), source_in=0.0, source_out=1.0,
                  label=f"{n + 1:02d}", template_slot=f"slot-{n + 1:02d}") for n in range(2)]
    slots[1].transform_keyframes = [TransformKeyframe("scale", 0.0, 1.14, InterpolationType.EASE_OUT),
                                    TransformKeyframe("scale", 0.3, 1.0, InterpolationType.LINEAR)]
    return Project(name="t", width=W, height=H, fps=25.0, media_assets=[], tracks=[
        Track(id="V1", name="V1", type="video", clips=slots)])


def test_an_empty_slot_is_a_card_in_the_plan_not_a_missing_media(tmp_path):
    plan = build_render_plan(_project(tmp_path))
    assert plan.missing_media == () and plan.empty_slots == ("s0", "s1")
    cards = [layer for layer in plan.graphics_layers if layer.role == "draw"]
    assert [(layer.clip_id, layer.graphic.text) for layer in cards] == [("s0", "01"), ("s1", "02")]
    assert (cards[0].graphic.width, cards[0].graphic.height) == (W, H)
    assert len(cards[1].transform_keyframes) == 2                       # le zoom d'impact du template s'y applique


def test_filling_a_slot_keeps_its_place_animation_and_frames_it(tmp_path):
    project = _project(tmp_path)
    project.media_assets.append(MediaAsset("v", str(tmp_path / "v.mp4"), "v", 10.0, 1920, 1080, 25.0, "video"))
    project.media_assets.append(MediaAsset("short", str(tmp_path / "s.mp4"), "s", 0.4, 1920, 1080, 25.0, "video"))
    project.media_assets.append(MediaAsset("a", str(tmp_path / "a.wav"), "a", 9.0, 0, 0, 0.0, "audio", True))
    assert slot_at(project, "V1", 1.5).id == "s1" and slot_at(project, "V1", 2.5) is None
    clip = fill_slot(project, "s1", "v")
    assert (clip.timeline_start, clip.duration, clip.transform.fill) == (1.0, 1.0, True)
    assert len(clip.transform_keyframes) == 2 and clip.template_slot == "slot-02"
    assert [slot.id for slot in empty_slots(project)] == ["s0"]
    assert fill_slot(project, "s0", "short").duration == pytest.approx(0.4)   # trop court : le plan raccourcit
    with pytest.raises(SlotError):
        fill_slot(project, "s1", "a")
    assert build_render_plan(project).empty_slots == ()


def test_slots_survive_the_kut_file_and_old_clips_have_none(tmp_path):
    project = _project(tmp_path)
    project.tracks[0].clips.append(Clip(id="plain", asset_id="x", track_id="V1", timeline_start=3.0, source_in=0.0,
                                        source_out=1.0))
    path = tmp_path / "p.kut"
    save_project(project, path)
    text = path.read_text(encoding="utf-8")
    assert text.count('"template_slot"') == 2                          # écrit seulement pour les emplacements
    loaded = load_project(path)
    assert [clip.template_slot for clip in loaded.tracks[0].clips] == ["slot-01", "slot-02", ""]


@needs_ffmpeg
def test_the_card_and_then_the_media_are_what_the_export_renders(tmp_path):
    project = _project(tmp_path)
    blank = render_frame(build_render_plan(project), W, H, 0.5)
    middle = blank[H // 2 - 4:H // 2 + 4, W // 2 - 20:W // 2 + 20].reshape(-1, 3).astype(int)
    assert (middle[:, 2] - middle[:, 0]).max() > 80                    # le numéro, en bleu électrique
    assert blank[2:8, 2:8].max() < 12                                   # hors du panneau : le noir du cadre
    video = lavfi_video(tmp_path / "red.mp4", "color=c=red", size=(160, 90), seconds=2.0)
    project.media_assets.append(MediaAsset("v", str(video), "v", 2.0, 160, 90, 25.0, "video"))
    fill_slot(project, "s0", "v")
    filled = render_frame(build_render_plan(project), W, H, 0.5).astype(int)
    assert filled[..., 0].mean() > 200 and filled[..., 2].mean() < 40   # le plan remplit le cadre (« remplir »)
    assert np.abs(filled[2:8, 2:8] - filled[H // 2, W // 2]).max() < 12


def test_a_locked_track_protects_its_slots(tmp_path):
    project = _project(tmp_path)
    project.media_assets.append(MediaAsset("v", str(tmp_path / "v.mp4"), "v", 10.0, 1920, 1080, 25.0, "video"))
    project.tracks[0].locked = True
    with pytest.raises(ValueError):
        fill_slot(project, "s0", "v")
    assert project.tracks[0].clips[0].asset_id == ""
