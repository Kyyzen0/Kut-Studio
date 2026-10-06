"""Templates « Night » et classement animé : projets complets, calés sur la grille, réeditables, rendus (vrai FFmpeg)."""

from __future__ import annotations

import pytest

from core.leaderboard import Leaderboard, LeaderboardError, LeaderboardRow, build_leaderboard, leaderboard_of, update_leaderboard
from core.project_io import load_project, save_project
from core.project_templates import NIGHT_PALETTE, TEMPLATES, TEXT_DEFAULTS, create_from_template
from core.render_plan import build_render_plan
from core.social_formats import create_social_project
from render_probe import needs_ffmpeg, render_frame


@pytest.fixture(autouse=True)
def _fonts(qapp):
    from core.bundled_fonts import register_bundled_fonts

    register_bundled_fonts()


@pytest.mark.parametrize("template", [template.id for template in TEMPLATES])
@pytest.mark.parametrize("format_id", ["vertical", "portrait", "square"])
def test_every_template_builds_a_complete_project_on_its_canvas(template, format_id):
    project = create_from_template(template, format_id, 30)
    plan = build_render_plan(project)
    spec = next(item for item in TEMPLATES if item.id == template)
    assert plan.missing_media == () and plan.warnings == ()
    assert plan.duration == pytest.approx(spec.duration)
    slots = [clip for clip in project.tracks[0].clips if clip.template_slot]
    assert slots and plan.empty_slots == tuple(clip.id for clip in slots)
    beat = 60.0 / project.active_sequence.beat_grid.bpm
    for clip in slots:                                                  # les plans tombent sur la grille
        assert clip.timeline_start / beat == pytest.approx(round(clip.timeline_start / beat))
        assert clip.transform.fill
    music = next(track for track in project.tracks if track.id == "A1").clips
    assert sum(clip.duration for clip in music) == pytest.approx(spec.duration)
    assert project.ducking_sidechains and project.ducking_sidechains[0].voice_track_id == "A2"
    sfx = [clip for track in project.tracks if track.audio_role == "sfx" for clip in track.clips]
    assert len(sfx) >= len(slots) - 1                                   # un son sur chaque cut
    texts = [clip.graphic for track in project.tracks if track.type == "graphics" for clip in track.clips
             if clip.graphic.type.value == "text"]
    assert texts and all(graphic.width <= project.width for graphic in texts)


def test_night_race_follows_the_f1_edit():
    project = create_from_template("night_race", "vertical", 30)
    slots = project.tracks[0].clips
    assert len(slots) == 19 and [clip.label for clip in slots[:3]] == ["01", "02", "03"]
    assert max(kf.value for kf in slots[1].transform_keyframes if kf.property_name == "scale") == pytest.approx(1.14)
    cues = [(marker.time_seconds, marker.category) for marker in project.markers]
    assert cues == [(0.0, "music_cue"), (14.0, "music_cue"), (30.0, "music_cue")]
    effects = [clip for track in project.tracks if track.type == "graphics" for clip in track.clips
               if clip.graphic.type.value == "adjustment"]
    assert [effect.type.value for effect in effects[0].effects] == ["color_correction", "glow", "vignette"]
    colors = {clip.graphic.fill_color for track in project.tracks if track.type == "graphics" for clip in track.clips
              if clip.graphic.type.value == "text"}
    assert {NIGHT_PALETTE["blue"], NIGHT_PALETTE["red"]} <= colors
    assert len(project.active_sequence.generated_groups) == 1


def test_titles_come_from_the_interface_and_shrink_to_fit():
    long = "a very long translated hook that would never fit on a phone screen"
    project = create_from_template("cta_comments", "vertical", 30, texts={"question": long})
    texts = {clip.graphic.text: clip.graphic for track in project.tracks if track.type == "graphics"
             for clip in track.clips if clip.graphic.type.value == "text"}
    assert long in texts and texts[TEXT_DEFAULTS["live"]]
    assert texts[long].width <= project.width and texts[long].font_size < 92


def test_a_template_project_survives_the_kut_file(tmp_path):
    project = create_from_template("leaderboard", "vertical", 30)
    path = tmp_path / "lb.kut"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.active_sequence.generated_groups == project.active_sequence.generated_groups
    assert [clip.template_slot for clip in loaded.tracks[0].clips] == ["slot-01"]
    assert loaded.active_sequence.beat_grid.bpm == 120.0


def test_a_leaderboard_is_rebuilt_in_place_when_its_table_changes():
    project = create_social_project("vertical", 30)
    rows = (LeaderboardRow("1", "A", "10"), LeaderboardRow("2", "B", "8", "#FF2433"))
    clips = build_leaderboard(project, Leaderboard(rows, start=2.0, duration=5.0))
    assert len(clips) == 10
    assert sorted({clip.timeline_start for clip in clips}) == [pytest.approx(2.3), pytest.approx(2.55)]
    board = leaderboard_of(project, clips[7].id)
    assert board is not None and board.rows == rows
    assets_before = len(project.media_assets)
    new = update_leaderboard(project, Leaderboard((*rows, LeaderboardRow("3", "C", "5")), 2.0, 5.0, id=board.id))
    graphics = [clip.id for track in project.tracks for clip in track.clips]
    assert len(new) == 15 and not set(clip.id for clip in clips) & set(graphics)
    assert len(project.media_assets) == assets_before + 5               # les calques remplacés ne laissent rien
    assert list(project.active_sequence.generated_groups) == [board.id]
    with pytest.raises(LeaderboardError):
        Leaderboard(())
    with pytest.raises(LeaderboardError):
        Leaderboard(rows, duration=0.4)


@needs_ffmpeg
def test_the_leaderboard_rows_slide_in_on_the_exported_frames():
    project = create_from_template("leaderboard", "vertical", 30)
    plan = build_render_plan(project)
    w, h = project.width // 6, project.height // 6
    before, after = render_frame(plan, w, h, 0.2, fps=30), render_frame(plan, w, h, 4.0, fps=30)
    rows = slice(int(h * 0.30), int(h * 0.62))                          # là où se posent les cinq lignes
    accent = after[rows, int(w * 0.10):int(w * 0.13)].reshape(-1, 3).astype(int)
    assert (accent[:, 2] - accent[:, 0]).max() > 120                    # la barre bleue de la première ligne
    assert abs(after[rows].astype(int) - before[rows].astype(int)).mean() > 8
