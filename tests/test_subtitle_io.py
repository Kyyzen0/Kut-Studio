"""Tests pour ``core.subtitle_io`` : parsing et formatage SRT.

Aucune dépendance PySide6 ni FFmpeg : les tests ne manipulent que
des chaînes et des fichiers temporaires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.subtitle_io import (
    SubtitleCue,
    format_srt,
    load_srt,
    parse_srt,
    save_srt,
)


# ---------------------------------------------------------------------------
# Cas nominal
# ---------------------------------------------------------------------------


def test_parse_srt_simple():
    """Un SRT simple est correctement parsé."""
    content = (
        "1\n"
        "00:00:01,250 --> 00:00:03,500\n"
        "Bonjour le monde\n"
        "\n"
    )
    cues = parse_srt(content)
    assert len(cues) == 1
    cue = cues[0]
    assert cue.start == pytest.approx(1.25)
    assert cue.end == pytest.approx(3.5)
    assert cue.text == "Bonjour le monde"


def test_parse_srt_multiline_text():
    """Un cue avec plusieurs lignes est préservé tel quel."""
    content = (
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "Première ligne\n"
        "Deuxième ligne\n"
        "Troisième ligne\n"
        "\n"
    )
    cues = parse_srt(content)
    assert len(cues) == 1
    assert cues[0].text == "Première ligne\nDeuxième ligne\nTroisième ligne"


def test_format_srt_then_parse_roundtrip():
    """``format_srt`` produit un contenu réouvrable identique."""
    cues = [
        SubtitleCue(start=0.0, end=2.0, text="Salut"),
        SubtitleCue(start=3.5, end=5.75, text="À bientôt"),
    ]
    rendered = format_srt(cues)
    parsed = parse_srt(rendered)
    assert len(parsed) == 2
    assert parsed[0].start == pytest.approx(0.0)
    assert parsed[0].end == pytest.approx(2.0)
    assert parsed[0].text == "Salut"
    assert parsed[1].start == pytest.approx(3.5)
    assert parsed[1].end == pytest.approx(5.75)
    assert parsed[1].text == "À bientôt"


def test_format_srt_millisecond_precision():
    """Le formatage SRT utilise la précision milliseconde."""
    cues = [SubtitleCue(start=0.001, end=0.999, text="")]
    rendered = format_srt(cues)
    assert "00:00:00,001 --> 00:00:00,999" in rendered


def test_parse_srt_sorts_cues_chronologically():
    """Les cues sont retournés triés par ``(start, end)`` croissant."""
    content = (
        "2\n"
        "00:00:05,000 --> 00:00:07,000\n"
        "B\n"
        "\n"
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "A\n"
        "\n"
    )
    cues = parse_srt(content)
    assert [c.text for c in cues] == ["A", "B"]


def test_parse_srt_accepts_dot_decimal_separator():
    """Le séparateur décimal ``.`` est toléré en plus de ``,``."""
    content = (
        "1\n"
        "00:00:01.250 --> 00:00:03.500\n"
        "Dot separator\n"
        "\n"
    )
    cues = parse_srt(content)
    assert cues[0].start == pytest.approx(1.25)
    assert cues[0].end == pytest.approx(3.5)


def test_parse_srt_normalizes_line_endings():
    """Les fins de ligne ``\\r\\n`` sont normalisées."""
    content = "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n\r\n"
    cues = parse_srt(content)
    assert len(cues) == 1
    assert cues[0].text == "Hello"


def test_parse_srt_tolerates_missing_index():
    """Un cue sans ligne d'indice est tout de même parsé."""
    content = (
        "00:00:01,000 --> 00:00:02,000\n"
        "Sans indice\n"
        "\n"
    )
    cues = parse_srt(content)
    assert len(cues) == 1
    assert cues[0].text == "Sans indice"


def test_save_srt_writes_utf8(tmp_path: Path):
    """La sauvegarde produit un fichier UTF-8 réouvrable."""
    target = tmp_path / "subs.srt"
    cues = [
        SubtitleCue(start=0.0, end=1.0, text="Bonjour — café"),
        SubtitleCue(start=2.0, end=3.0, text="Accentué: àéè"),
    ]
    save_srt(cues, str(target))

    # Le fichier brut est bien encodé en UTF-8.
    raw = target.read_bytes()
    assert "café".encode("utf-8") in raw

    # Et load_srt le relit fidèlement.
    reloaded = load_srt(str(target))
    assert reloaded == cues


def test_subtitle_cue_rejects_end_before_start():
    """Un cue avec ``end <= start`` est refusé par ``__post_init__``."""
    with pytest.raises(ValueError, match="strictement supérieure"):
        SubtitleCue(start=2.0, end=2.0, text="x")
    with pytest.raises(ValueError, match="strictement supérieure"):
        SubtitleCue(start=3.0, end=2.0, text="x")


def test_subtitle_cue_rejects_negative_start():
    """Un cue avec un début négatif est rejeté."""
    with pytest.raises(ValueError, match="positif"):
        SubtitleCue(start=-0.1, end=1.0, text="x")


def test_parse_srt_rejects_invalid_timecode():
    """Un timecode mal formé lève ``ValueError``."""
    with pytest.raises(ValueError, match="Timecode invalide"):
        parse_srt("1\n00:00:1,250 --> 00:00:03,500\nx\n\n")


def test_parse_srt_rejects_missing_arrow():
    """Un bloc sans flèche ``-->`` lève ``ValueError``."""
    with pytest.raises(ValueError, match="-->"):
        parse_srt("1\n00:00:01,000 00:00:02,000\nx\n\n")


def test_format_srt_rejects_negative_timecode():
    """``_format_timecode`` refuse une valeur négative."""
    from core.subtitle_io import _format_timecode

    with pytest.raises(ValueError, match="négatif"):
        _format_timecode(-0.1)