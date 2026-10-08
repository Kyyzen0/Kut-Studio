"""Sous-titres automatiques : une transcription posée dans la timeline, en sous-titres ou en titres karaoké."""

from __future__ import annotations

import pytest

from core.auto_captions import MIN_LINE_SECONDS, add_karaoke_lines, add_subtitle_lines, caption_lines
from core.project_model import Clip, Project, Track
from core.text_runs import word_count
from core.timeline_operations import add_subtitle_clip
from core.transcription import Word


def _voice(**fields) -> Clip:
    values = {"id": "voix", "asset_id": "a", "track_id": "A1", "timeline_start": 10.0, "source_in": 2.0,
              "source_out": 8.0}
    return Clip(**{**values, **fields})


def _project(*tracks: Track) -> Project:
    return Project(name="Sous-titres", width=1080, height=1920, fps=30.0, tracks=list(tracks))


WORDS = [Word("Bonjour", 2.5, 2.9), Word("à", 2.9, 3.0), Word("tous.", 3.0, 3.4), Word("On", 4.5, 4.6),
         Word("y", 4.6, 4.7), Word("va", 4.7, 4.7)]


def test_words_move_from_media_time_to_the_timeline():
    lines = caption_lines(_voice(), WORDS)
    assert [line.text for line in lines] == ["Bonjour à tous.", "On y va"]
    first, second = lines
    assert (first.start, first.end) == pytest.approx((10.5, 11.4))        # 10 s + (2,5 s − 2 s source)
    assert first.word_starts == pytest.approx((0.0, 0.4, 0.5))
    assert second.start == pytest.approx(12.5)
    assert second.end - second.start == pytest.approx(MIN_LINE_SECONDS)   # « va » dit en un instant : lisible quand même


def test_a_line_never_runs_into_the_next_one_nor_past_the_clip():
    close = [Word("un.", 2.0, 2.05), Word("deux.", 2.1, 2.2), Word("trois", 7.95, 9.0)]
    lines = caption_lines(_voice(), close)
    assert lines[0].end <= lines[1].start
    assert lines[-1].end <= 16.0                                          # fin du clip de la voix


def test_a_recognised_word_with_a_space_counts_as_two_words():
    lines = caption_lines(_voice(), [Word("New York", 2.0, 2.6), Word("ce", 2.6, 2.8)])
    assert lines[0].text == "New York ce" and len(lines[0].word_starts) == word_count(lines[0].text)


def test_subtitles_go_on_a_free_subtitle_track():
    taken = Track(id="S1", name="S1", type="subtitle")
    project = _project(taken)
    add_subtitle_clip(project, "déjà là", 11.0, 2.0, track_id="S1")
    clips = add_subtitle_lines(project, caption_lines(_voice(), WORDS))
    assert [clip.text for clip in clips] == ["Bonjour à tous.", "On y va"]
    tracks = {clip.track_id for clip in clips}
    assert len(tracks) == 1 and tracks != {"S1"}                          # S1 occupé sur la plage : nouvelle piste
    assert add_subtitle_lines(project, []) == []


def test_karaoke_titles_light_each_word_when_it_is_said():
    project = _project()
    titles = add_karaoke_lines(project, caption_lines(_voice(), WORDS))
    first = titles[0]
    assert first.graphic.text == "Bonjour à tous." and first.graphic.word_reveal == "karaoke"
    assert first.graphic.word_times == pytest.approx((0.0, 0.4, 0.5))
    assert first.graphic.font_family == "Anton" and first.graphic.highlight_color == "#FFD84D"
    assert first.timeline_start == pytest.approx(10.5) and first.label == "Bonjour à tous."
    assert len({title.track_id for title in titles}) == 1


def test_karaoke_titles_avoid_a_locked_or_busy_title_track():
    locked = Track(id="G1", name="G1", type="graphics", locked=True)
    project = _project(locked)
    titles = add_karaoke_lines(project, caption_lines(_voice(), WORDS))
    assert titles[0].track_id != "G1" and locked.clips == []
