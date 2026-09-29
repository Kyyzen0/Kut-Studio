"""Tests du modèle :mod:`core.text_style` (tâche 24).

Couvre :

- valeurs par défaut et validation ;
- alignement et conversion ASS ;
- conversion de couleurs ;
- sérialisation JSON avec rétrocompatibilité ;
- application d'un style à un clip.
"""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.text_style import (
    DEFAULT_TEXT_STYLE,
    TextAlignment,
    TextStyle,
    alignment_rows,
    alignment_to_ass,
    ass_color,
    default_text_style,
    is_default_style,
)


# ---------------------------------------------------------------------------
# Valeurs par défaut et validation
# ---------------------------------------------------------------------------


def test_default_style_is_immutable_and_neutral() -> None:
    style = default_text_style()
    assert style == DEFAULT_TEXT_STYLE
    assert style.font_family == "Arial"
    assert style.alignment is TextAlignment.BOTTOM_CENTER
    assert style.background_color is None


def test_text_style_is_frozen() -> None:
    style = default_text_style()
    with pytest.raises(Exception):
        style.font_size = 12.0  # type: ignore[misc]


def test_text_style_validates_opacity() -> None:
    """L'opacité est bornée silencieusement : ``TextStyle`` clampe."""
    too_high = TextStyle(opacity=2.0)
    assert too_high.opacity == 1.0
    too_low = TextStyle(opacity=-0.5)
    assert too_low.opacity == 0.0


def test_text_style_rejects_non_finite_opacity() -> None:
    """Les valeurs non finies restent une erreur."""
    import math
    with pytest.raises(ValueError):
        TextStyle(opacity=math.nan)
    with pytest.raises(ValueError):
        TextStyle(opacity=math.inf)


def test_text_style_clamps_font_size() -> None:
    over = TextStyle(font_size=999.0)
    assert over.font_size == 240.0
    under = TextStyle(font_size=0.0)
    assert under.font_size == 6.0


def test_text_style_normalizes_short_hex_color() -> None:
    s = TextStyle(color="#fff")
    assert s.color == "#ffffff"


def test_text_style_rejects_unknown_color_name() -> None:
    with pytest.raises(ValueError):
        TextStyle(color="banana")


def test_text_style_accepts_ass_named_colors() -> None:
    s = TextStyle(color="white")
    assert s.color == "#ffffff"


def test_text_style_rejects_duplicate_unique_categories_via_alignment() -> None:
    # Pas de règle forte, mais le constructeur doit accepter tout alignement.
    for alignment in TextAlignment:
        style = TextStyle(alignment=alignment)
        assert style.alignment is alignment


def test_text_style_clamps_thickness() -> None:
    s = TextStyle(outline_width=100.0)
    assert s.outline_width == 20.0


# ---------------------------------------------------------------------------
# Alignement et ASS
# ---------------------------------------------------------------------------


def test_alignment_rows_returns_three_by_three() -> None:
    rows = alignment_rows()
    assert len(rows) == 3
    assert all(len(row) == 3 for row in rows)
    # La première ligne commence par TOP_LEFT.
    assert rows[0][0] is TextAlignment.TOP_LEFT
    # La dernière ligne se termine par BOTTOM_RIGHT.
    assert rows[2][-1] is TextAlignment.BOTTOM_RIGHT


@pytest.mark.parametrize(
    ("alignment", "ass_value"),
    [
        (TextAlignment.BOTTOM_LEFT, 1),
        (TextAlignment.BOTTOM_CENTER, 2),
        (TextAlignment.BOTTOM_RIGHT, 3),
        (TextAlignment.MIDDLE_LEFT, 4),
        (TextAlignment.MIDDLE_CENTER, 5),
        (TextAlignment.MIDDLE_RIGHT, 6),
        (TextAlignment.TOP_LEFT, 7),
        (TextAlignment.TOP_CENTER, 8),
        (TextAlignment.TOP_RIGHT, 9),
    ],
)
def test_alignment_to_ass(alignment, ass_value) -> None:
    assert alignment_to_ass(alignment) == ass_value


def test_ass_color_encodes_bbggrr_with_alpha() -> None:
    """ASS : ``&H00`` = opaque, ``&HFF`` = transparent.

    Notre convention : ``opacity=1.0`` (totalement opaque) → ASS alpha 00.
    """
    assert ass_color("#ffffff", 1.0) == "&H00FFFFFF&"
    assert ass_color("#ff0000", 0.0) == "&HFF0000FF&"
    # Alpha = 0.5 → 0x80.
    assert ass_color("#00ff00", 0.5) == "&H8000FF00&"


def test_ass_color_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        ass_color("not-a-color", 1.0)


# ---------------------------------------------------------------------------
# Sérialisation JSON
# ---------------------------------------------------------------------------


def test_to_dict_roundtrip() -> None:
    style = TextStyle(
        font_family="Helvetica",
        font_size=48.0,
        color="#ff8800",
        opacity=0.85,
        alignment=TextAlignment.TOP_LEFT,
        background_color="#222222",
        background_opacity=0.6,
        position_x=12.5,
        position_y=-3.0,
    )
    restored = TextStyle.from_dict(style.to_dict())
    assert restored == style


def test_from_dict_returns_default_on_empty_input() -> None:
    restored = TextStyle.from_dict({})
    assert is_default_style(restored)


def test_from_dict_returns_default_on_none() -> None:
    assert is_default_style(TextStyle.from_dict(None))


def test_from_dict_returns_default_on_garbage() -> None:
    """Une valeur corrompue ne doit pas faire échouer l'ouverture du projet."""
    restored = TextStyle.from_dict({"font_size": "oops", "color": "potato"})
    assert is_default_style(restored)


def test_from_dict_partial_payload_uses_defaults() -> None:
    """Un payload partiel (ancien format) est fusionné avec les défauts."""
    restored = TextStyle.from_dict({"font_size": 48.0})
    assert restored.font_size == 48.0
    assert restored.alignment is DEFAULT_TEXT_STYLE.alignment


def test_from_dict_accepts_named_alignment() -> None:
    style = TextStyle.from_dict({"alignment": "middle_center"})
    assert style.alignment is TextAlignment.MIDDLE_CENTER


def test_from_dict_handles_unknown_alignment() -> None:
    style = TextStyle.from_dict({"alignment": "wat"})
    assert style.alignment is DEFAULT_TEXT_STYLE.alignment


# ---------------------------------------------------------------------------
# Intégration avec le modèle de clip
# ---------------------------------------------------------------------------


def _project_with_subtitle_clip(text_style: TextStyle | None = None) -> Project:
    track = Track(
        id="T1",
        name="Titres",
        type="subtitle",
        clips=[
            Clip(
                id="sub-1",
                asset_id="subtitle-asset",
                track_id="T1",
                timeline_start=1.0,
                source_in=0.0,
                source_out=2.5,
                label="Titre",
                text="Bonjour",
                text_style=text_style or default_text_style(),
            )
        ],
    )
    return Project(
        name="Sub test",
        media_assets=[
            MediaAsset(
                id="subtitle-asset",
                path="/tmp/s.srt",
                name="Sous-titres",
                duration=10.0,
                width=0,
                height=0,
                fps=0,
                media_type="subtitle",
            )
        ],
        tracks=[track],
    )


def test_clip_defaults_to_standard_text_style() -> None:
    project = _project_with_subtitle_clip()
    assert is_default_style(project.tracks[0].clips[0].text_style)


def test_clip_can_carry_a_custom_text_style() -> None:
    style = TextStyle(
        font_size=64.0,
        color="#ff8800",
        alignment=TextAlignment.TOP_CENTER,
    )
    project = _project_with_subtitle_clip(text_style=style)
    assert project.tracks[0].clips[0].text_style.font_size == 64.0
    assert project.tracks[0].clips[0].text_style.color == "#ff8800"


def test_text_style_is_independent_between_clips() -> None:
    a = TextStyle(font_size=40.0)
    b = a.with_updates(font_size=60.0)
    assert a.font_size == 40.0
    assert b.font_size == 60.0