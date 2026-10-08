"""Tests de l'export des styles de texte (tâche 24).

Couvre :

- génération d'un ASS depuis :class:`SubtitleCue` + :class:`TextStyle` ;
- préservation de styles *par cue* via ``format_ass_with_styles`` ;
- persistance et lecture d'un projet ``.kut`` qui embarque des styles ;
- compatibilité ascendante : un ancien ``.kut`` lit avec un style
  standard et reste exportable en SRT ou ASS.
"""

from __future__ import annotations

import json
from pathlib import Path


from core.project_io import load_project, save_project
from core.project_model import (
    Clip,
    MediaAsset,
    Project,
    Track,
)
from core.render_plan import build_render_plan
from core.subtitle_io import (
    SubtitleCue,
    format_ass,
    format_ass_with_styles,
    format_srt,
    parse_srt,
)
from core.text_style import (
    TextAlignment,
    TextStyle,
    default_text_style,
)


# ---------------------------------------------------------------------------
# Export ASS
# ---------------------------------------------------------------------------


def _subtitle_track() -> Track:
    return Track(
        id="T1",
        name="Titres",
        type="subtitle",
        clips=[
            Clip(
                id="sub-1",
                asset_id="subtitle-asset",
                track_id="T1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=2.0,
                label="Titre",
                text="Hello",
                text_style=TextStyle(
                    font_size=64.0,
                    color="#ff0000",
                    alignment=TextAlignment.TOP_CENTER,
                ),
            ),
            Clip(
                id="sub-2",
                asset_id="subtitle-asset",
                track_id="T1",
                timeline_start=2.0,
                source_in=0.0,
                source_out=2.0,
                label="Carton",
                text="World",
                text_style=TextStyle(
                    font_size=28.0,
                    color="#ffffff",
                    background_color="#000000",
                    background_opacity=0.8,
                    alignment=TextAlignment.BOTTOM_LEFT,
                ),
            ),
        ],
    )


def _project_with_styles() -> Project:
    return Project(
        name="ASS test",
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
        tracks=[_subtitle_track()],
    )


def test_format_ass_contains_each_cue() -> None:
    cues = [
        SubtitleCue(start=0.0, end=1.0, text="A"),
        SubtitleCue(start=1.0, end=2.0, text="B"),
    ]
    style = default_text_style()
    output = format_ass(cues, style)
    assert "[V4+ Styles]" in output
    assert "[Events]" in output
    # Les marges du style sont reportées sur la ligne Dialogue.
    margin_x = int(style.margin_x)
    margin_y = int(style.margin_y)
    assert (
        f"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,"
        f"{margin_x},{margin_x},{margin_y},,A" in output
    )
    assert (
        f"Dialogue: 0,0:00:01.00,0:00:02.00,Default,,"
        f"{margin_x},{margin_x},{margin_y},,B" in output
    )


def test_format_ass_reports_outline_shadow_and_alignment() -> None:
    """Les colonnes Outline / Shadow / Alignment reprennent le style."""
    cues = [SubtitleCue(start=0.0, end=1.0, text="A")]
    style = TextStyle(
        font_family="Helvetica",
        font_size=48.0,
        color="#ff8800",
        outline_width=3.0,
        shadow_offset=2.0,
        alignment=TextAlignment.TOP_CENTER,
    )
    output = format_ass(cues, style)
    # Style: name, font, size, primary, secondary, outline, back, ...
    assert "Style: Default,Helvetica,48,&H000088FF&," in output
    # BorderStyle=1, Outline=3.0, Shadow=2.0, Alignment=8 (haut-centré)
    assert ",1,3.0,2.0,8," in output


def test_format_ass_reports_background_colour_when_set() -> None:
    cues = [SubtitleCue(start=0.0, end=1.0, text="A")]
    style = TextStyle(
        background_color="#0f766e",
        background_opacity=0.9,
    )
    output = format_ass(cues, style)
    # BackColour encode le fond + son opacité (ASS BGR + alpha).
    assert "&H196E760F&" in output


def test_format_ass_escapes_newlines_as_backslash_n() -> None:
    cues = [SubtitleCue(start=0.0, end=1.0, text="Ligne 1\nLigne 2")]
    style = default_text_style()
    output = format_ass(cues, style)
    assert r"Ligne 1\NLigne 2" in output


def test_format_ass_with_styles_produces_one_style_per_cue() -> None:
    cues = [
        SubtitleCue(start=0.0, end=1.0, text="A"),
        SubtitleCue(start=1.0, end=2.0, text="B"),
    ]
    styles = [
        TextStyle(alignment=TextAlignment.TOP_CENTER),
        TextStyle(alignment=TextAlignment.BOTTOM_LEFT),
    ]
    output = format_ass_with_styles(
        list(zip(cues, styles)), default_text_style()
    )
    # Les deux styles sont matérialisés.
    assert "Style: Default," in output
    assert "Style: S1," in output
    assert "Style: S2," in output
    # Les deux dialogues pointent vers leur propre style.
    assert ",S1,," in output
    assert ",S2,," in output
    # S1 est centré en haut (8), S2 en bas à gauche (1).
    assert ",1,2.0,1.5,8," in output
    assert ",1,2.0,1.5,1," in output


def test_format_ass_with_styles_falls_back_to_default_style() -> None:
    cues = [SubtitleCue(start=0.0, end=1.0, text="A")]
    styles = [None]
    output = format_ass_with_styles(
        list(zip(cues, styles)), default_text_style()
    )
    style = default_text_style()
    margin_x = int(style.margin_x)
    margin_y = int(style.margin_y)
    assert (
        f"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,"
        f"{margin_x},{margin_x},{margin_y},,A" in output
    )
    # Aucun style dupliqué : seulement ``Default``.
    assert output.count("Style: ") == 1


def test_format_ass_with_styles_reuses_default_for_standard_clips() -> None:
    """Un clip au style standard ne duplique pas une ligne ``Style:``."""
    cues = [
        SubtitleCue(start=0.0, end=1.0, text="A"),
        SubtitleCue(start=1.0, end=2.0, text="B"),
    ]
    styles = [default_text_style(), TextStyle(font_size=72.0)]
    output = format_ass_with_styles(
        list(zip(cues, styles)), default_text_style()
    )
    assert output.count("Style: ") == 2
    assert ",Default,," in output
    assert ",S1,," in output


def test_generated_ass_declares_consistent_column_counts() -> None:
    """Chaque ligne ``Style:`` / ``Dialogue:`` respecte les ``Format:``.

    Un décalage de colonne est la cause n°1 d'un ASS refusé par
    libass ; ce test verrouille la structure sans dépendre de FFmpeg.
    """
    from core.text_presets import builtin_text_presets

    presets = builtin_text_presets()
    cues = [
        SubtitleCue(i * 3.0, i * 3.0 + 3.0, preset.name)
        for i, preset in enumerate(presets)
    ]
    output = format_ass_with_styles(
        list(zip(cues, [p.style for p in presets])), default_text_style()
    )
    lines = output.split("\n")
    style_columns = len(
        next(l for l in lines if l.startswith("Format: Name"))
        .replace("Format: Name", "")
        .split(",")
    )
    event_columns = len(
        next(l for l in lines if l.startswith("Format: Layer"))
        .replace("Format: Layer", "")
        .split(",")
    )
    for line in lines:
        if line.startswith("Style: "):
            assert len(line.replace("Style: ", "").split(",")) == style_columns
        elif line.startswith("Dialogue: "):
            assert (
                len(line.replace("Dialogue: ", "").split(",")) == event_columns
            )


def test_format_srt_keeps_only_text_and_timing() -> None:
    """Le SRT reste l'export brut : pas de style, juste timing + texte."""
    cues = [
        SubtitleCue(start=0.0, end=1.5, text="Hi"),
        SubtitleCue(start=2.0, end=4.0, text="There"),
    ]
    output = format_srt(cues)
    assert "00:00:00,000 --> 00:00:01,500" in output
    assert "00:00:02,000 --> 00:00:04,000" in output
    assert "Hi" in output and "There" in output
    # Roundtrip parse → format donne le même contenu.
    reparsed = parse_srt(output)
    assert [(c.start, c.end, c.text) for c in reparsed] == [
        (0.0, 1.5, "Hi"),
        (2.0, 4.0, "There"),
    ]


# ---------------------------------------------------------------------------
# Render plan : alignement cues / styles
# ---------------------------------------------------------------------------


def test_render_plan_aligns_subtitle_styles_with_cues() -> None:
    project = _project_with_styles()
    plan = build_render_plan(project)
    assert len(plan.subtitle_cues) == 2
    assert len(plan.subtitle_styles) == 2
    assert plan.subtitle_cues[0].text == "Hello"
    assert plan.subtitle_styles[0].alignment is TextAlignment.TOP_CENTER
    assert plan.subtitle_styles[1].background_color == "#000000"


def test_render_plan_provides_standard_style_for_clips_without_style() -> None:
    track = Track(
        id="T1",
        name="Titres",
        type="subtitle",
        clips=[
            Clip(
                id="plain",
                asset_id="subtitle-asset",
                track_id="T1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=1.0,
                label="",
                text="Standard",
            )
        ],
    )
    project = Project(
        name="Plain",
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
    plan = build_render_plan(project)
    assert plan.subtitle_styles[0] == default_text_style()


# ---------------------------------------------------------------------------
# Persistance .kut et rétrocompatibilité
# ---------------------------------------------------------------------------


def test_project_io_round_trip_preserves_text_styles(tmp_path: Path) -> None:
    project = _project_with_styles()
    target = tmp_path / "styles.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].text_style.color == "#ff0000"
    assert loaded.tracks[0].clips[0].text_style.alignment is TextAlignment.TOP_CENTER
    assert loaded.tracks[0].clips[1].text_style.background_color == "#000000"


def test_project_io_backward_compat_reads_legacy_kut(tmp_path: Path) -> None:
    """Un .kut sans ``text_style`` lit avec le style standard."""
    legacy_payload = {
        "format": "kut-studio-project",
        "version": 9,
        "project": {
            "name": "Legacy",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "duration": 10.0,
            "media_assets": [],
            "tracks": [
                {
                    "id": "T1",
                    "name": "Titres",
                    "type": "subtitle",
                    "clips": [
                        {
                            "id": "sub-1",
                            "asset_id": "",
                            "track_id": "T1",
                            "timeline_start": 0.0,
                            "source_in": 0.0,
                            "source_out": 1.0,
                            "enabled": True,
                            "label": "Legacy",
                            "text": "Legacy",
                        }
                    ],
                }
            ],
        },
    }
    target = tmp_path / "legacy.kut"
    target.write_text(json.dumps(legacy_payload), encoding="utf-8")
    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].text == "Legacy"
    # Le style lit est le style standard.
    assert loaded.tracks[0].clips[0].text_style == default_text_style()


def test_project_io_corrupt_text_style_falls_back_to_default(
    tmp_path: Path,
) -> None:
    """Un bloc ``text_style`` corrompu ne fait pas échouer l'ouverture."""
    payload = {
        "format": "kut-studio-project",
        "version": 10,
        "project": {
            "name": "Corrupt",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "duration": 10.0,
            "media_assets": [],
            "tracks": [
                {
                    "id": "T1",
                    "name": "Titres",
                    "type": "subtitle",
                    "clips": [
                        {
                            "id": "sub-1",
                            "asset_id": "",
                            "track_id": "T1",
                            "timeline_start": 0.0,
                            "source_in": 0.0,
                            "source_out": 1.0,
                            "enabled": True,
                            "label": "Corrupt",
                            "text": "Hello",
                            "text_style": {"color": "banana"},
                        }
                    ],
                }
            ],
        },
    }
    target = tmp_path / "corrupt.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].text_style == default_text_style()


def test_save_project_atomic_no_tmp_files(tmp_path: Path) -> None:
    project = _project_with_styles()
    target = tmp_path / "atomic.kut"
    save_project(project, str(target))
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []