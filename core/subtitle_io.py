"""Lecture et écriture de sous-titres au format SRT et ASS (tâche 24).

Ce module implémente un sous-ensemble strict du format SubRip :

- timecodes ``HH:MM:SS,mmm`` (virgule décimale, précision milliseconde) ;
- indices incrémentés à partir de 1 ;
- texte multi-ligne ;
- encodage UTF-8.

Les fonctions exposées sont volontairement minimales :

- :func:`parse_srt` : parse une chaîne SRT en :class:`SubtitleCue` ;
- :func:`format_srt` : reconstruit une chaîne SRT à partir des cues ;
- :func:`load_srt` / :func:`save_srt` : lecture et écriture de fichiers ;
- :func:`format_ass` : génère un sous-titre ASS pour préserver les
  styles typographiques au moment de l'export FFmpeg.

Aucune dépendance PySide6 ni FFmpeg : ce module est utilisable hors
d'un contexte Qt, en CLI ou dans les tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubtitleCue:
    """Un sous-titre SRT : intervalle demi-ouvert ``[start, end)`` + texte.

    Les bornes sont exprimées en secondes flottantes. La précision est
    la milliseconde lors du rendu (``HH:MM:SS,mmm``) ; à l'intérieur du
    modèle, la précision arbitraire du ``float`` Python est conservée.
    """

    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        """Vérifie que le cue respecte les invariants du format SRT."""
        if self.start < 0.0:
            raise ValueError(
                f"Le début d'un SubtitleCue doit être positif ou nul "
                f"(reçu : {self.start})."
            )
        if self.end <= self.start:
            raise ValueError(
                f"La fin d'un SubtitleCue doit être strictement supérieure à "
                f"son début (reçu : start={self.start}, end={self.end})."
            )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})$"
)


def parse_srt(content: str) -> list[SubtitleCue]:
    """Parse une chaîne SRT en liste de :class:`SubtitleCue`.

    Le parser tolère :

    - les sauts de ligne ``\\r\\n`` et ``\\n`` ;
    - les indices manquants (régénérés automatiquement) ;
    - les timecodes avec ``.`` ou ``,`` comme séparateur décimal ;
    - les espaces autour des flèches ``-->``.

    Le résultat est trié par ``(start, end)`` puis par ordre de
    rencontre ; les cues invalides lèvent une :class:`ValueError`.

    Raises:
        ValueError: si un timecode est mal formé ou si ``end <= start``.
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _split_blocks(text)
    raw_cues: list[SubtitleCue] = []
    for block in blocks:
        cue = _parse_block(block)
        if cue is not None:
            raw_cues.append(cue)
    # Tri stable : conserve l'ordre d'apparition pour les doublons.
    return sorted(raw_cues, key=lambda c: (c.start, c.end))


def _split_blocks(content: str) -> list[str]:
    """Découpe le contenu SRT en blocs séparés par une ligne vide."""
    blocks: list[str] = []
    current: list[str] = []
    for line in content.split("\n"):
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current).strip("\n"))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip("\n"))
    return [b for b in blocks if b]


def _parse_block(block: str) -> SubtitleCue | None:
    """Parse un bloc SRT (1 cue) et retourne :class:`SubtitleCue` ou ``None``."""
    lines = block.split("\n")
    # L'indice est optionnel : on l'ignore.
    if lines and lines[0].strip().isdigit():
        lines = lines[1:]
    if not lines:
        return None
    # La ligne suivante contient les timecodes.
    arrow_line = lines[0]
    if "-->" not in arrow_line:
        raise ValueError(
            f"Bloc SRT mal formé : séparateur '-->' manquant dans {arrow_line!r}."
        )
    start_str, end_str = arrow_line.split("-->", 1)
    start = _parse_timecode(start_str.strip())
    end = _parse_timecode(end_str.strip())
    body = "\n".join(lines[1:]).rstrip()
    return SubtitleCue(start=start, end=end, text=body)


def _parse_timecode(value: str) -> float:
    """Convertit un timecode ``HH:MM:SS,mmm`` ou ``HH:MM:SS.mmm`` en secondes."""
    match = _TIME_RE.match(value)
    if not match:
        raise ValueError(f"Timecode invalide : {value!r}.")
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    milliseconds = int(match.group("ms").ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_srt(cues: list[SubtitleCue]) -> str:
    """Sérialise une liste de cues en chaîne SRT.

    Les cues sont d'abord triés par ``(start, end)`` pour produire un
    SRT ordonné. Les timecodes sont formatés avec une précision
    milliseconde ; le texte est conservé tel quel (UTF-8).
    """
    ordered = sorted(cues, key=lambda c: (c.start, c.end))
    parts: list[str] = []
    for index, cue in enumerate(ordered, 1):
        parts.append(str(index))
        parts.append(
            f"{_format_timecode(cue.start)} --> {_format_timecode(cue.end)}"
        )
        parts.append(cue.text.rstrip("\n"))
        parts.append("")  # ligne vide entre les blocs
    return "\n".join(parts).rstrip("\n") + "\n"


def _format_timecode(seconds: float) -> str:
    """Formate une durée en secondes en timecode ``HH:MM:SS,mmm``."""
    if seconds < 0:
        raise ValueError(
            f"Impossible de formater un timecode négatif : {seconds}."
        )
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


# ---------------------------------------------------------------------------
# I/O fichier
# ---------------------------------------------------------------------------


def load_srt(file_path: str) -> list[SubtitleCue]:
    """Charge un fichier SRT UTF-8 et retourne la liste des cues."""
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    return parse_srt(content)


def save_srt(cues: list[SubtitleCue], file_path: str) -> None:
    """Sérialise ``cues`` dans ``file_path`` au format SRT UTF-8."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = format_srt(cues)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# ASS : préservation des styles typographiques
# ---------------------------------------------------------------------------


# Nom logique du style ASS par défaut. Limite ASS : « Alphanumeric only »,
# ce qui exclut les espaces et les tirets dans le nom de style.
_DEFAULT_ASS_STYLE_NAME = "Default"


def _escape_ass_text(text: str) -> str:
    """Échapper le texte pour un fichier ASS.

    Les sauts de ligne sont transformés en ``\\N`` (la séquence ASS pour
    passer à la ligne suivante). Les retours chariots Windows sont
    normalisés avant l'échappement.
    """
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", r"\N")


def _ass_style_line(style, *, name: str = _DEFAULT_ASS_STYLE_NAME) -> str:
    """Formate une ligne ``Style:`` ASS pour un :class:`TextStyle`.

    L'ordre des colonnes est celui imposé par le bloc ``[V4+ Styles]`` :

    ``Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
    OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,
    ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,
    Alignment, MarginL, MarginR, MarginV, Encoding``

    Points de correspondance avec :class:`~core.text_style.TextStyle` :

    - ``PrimaryColour`` porte la couleur **et** l'opacité du texte ;
    - ``BackColour`` porte le fond et son opacité (0 si aucun fond) ;
    - ``Outline`` / ``Shadow`` reprennent l'épaisseur du contour et le
      décalage de l'ombre ;
    - ``Alignment`` reprend notre grille 3×3 via
      :func:`~core.text_style.alignment_to_ass` ;
    - ``MarginL/R`` reprennent la marge horizontale, ``MarginV`` la
      marge verticale ;
    - ``BorderStyle`` vaut 1 (« outline + shadow ») quand le style
      demande un contour ou une ombre, 0 sinon.
    """
    from .text_style import alignment_to_ass, ass_color

    alignment_value = alignment_to_ass(style.alignment)
    primary = ass_color(style.color, style.opacity)
    secondary = ass_color(style.outline_color, 1.0)
    outline_colour = ass_color(style.outline_color, 1.0)
    if style.background_color is not None:
        back = ass_color(style.background_color, style.background_opacity)
    else:
        back = ass_color("#000000", 0.0)
    border_style = 1 if (style.outline_width > 0 or style.shadow_offset > 0) else 0
    return (
        f"Style: {name},{style.font_family},{int(round(style.font_size))},"
        f"{primary},{secondary},{outline_colour},{back},"
        # Bold, Italic, Underline, StrikeOut : non utilisés par Kut-Studio.
        "0,0,0,0,"
        # ScaleX, ScaleY, Spacing, Angle.
        "100,100,0,0,"
        f"{border_style},"
        f"{style.outline_width:.1f},"
        f"{style.shadow_offset:.1f},"
        f"{alignment_value},"
        f"{int(round(style.margin_x))},"
        f"{int(round(style.margin_x))},"
        f"{int(round(style.margin_y))},"
        "1"
    )


def _ass_dialogue_line(
    cue,
    style_name: str = _DEFAULT_ASS_STYLE_NAME,
    style=None,
) -> str:
    """Formate une ligne ``Dialogue:`` ASS pour un :class:`SubtitleCue`.

    Les marges de la ligne reprennent celles du style (libass lit les
    marges de la ligne *et* celles du style ; les reporter ici évite
    toute ambiguïté selon la version de libass).
    """
    start = _format_ass_timecode(cue.start)
    end = _format_ass_timecode(cue.end)
    text = _escape_ass_text(cue.text)
    if style is None:
        margin_l = margin_r = margin_v = 0
    else:
        margin_l = margin_r = int(round(style.margin_x))
        margin_v = int(round(style.margin_y))
    return (
        f"Dialogue: 0,{start},{end},{style_name},,"
        f"{margin_l},{margin_r},{margin_v},,{text}"
    )


def _format_ass_timecode(seconds: float) -> str:
    """Formate ``seconds`` en timecode ``H:MM:SS.cc`` (centièmes ASS)."""
    if seconds < 0:
        raise ValueError(
            f"Impossible de formater un timecode ASS négatif : {seconds}."
        )
    total_cs = int(round(seconds * 100))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def format_ass(cues: list[SubtitleCue], style) -> str:
    """Génère un sous-titre ASS qui applique ``style`` à tous les cues.

    Tous les sous-titres partagent le même style ASS nommé ``Default`` :
    c'est la limite du filtre ``subtitles=`` de FFmpeg, qui ne
    référence qu'un fichier unique. Pour des styles *par cue*, on
    s'appuiera sur ``force_style`` côté export (voir
    :mod:`core.export_engine`).
    """
    style_lines = [_ass_style_line(style)]
    dialogue_lines = [
        _ass_dialogue_line(cue, style=style) for cue in cues
    ]
    return _ass_template(style_lines, dialogue_lines)


def format_ass_with_styles(
    entries: list, default_style
) -> str:
    """Génère un ASS où chaque :class:`SubtitleCue` porte son propre style.

    Args:
        entries: liste de tuples ``(SubtitleCue, TextStyle)`` triés.
        default_style: :class:`TextStyle` de repli pour les entrées
            dont le style est ``None``. Les clips sans style utilisent
    explicitement le style nommé ``Default`` dans l'ASS produit.
    """
    style_lines: list[str] = []
    dialogues: list[str] = []
    # Le style par défaut est inséré tel quel, sous le nom ``Default``.
    style_lines.append(_ass_style_line(default_style, name=_DEFAULT_ASS_STYLE_NAME))
    counter = 0
    for cue, style in entries:
        if style is None or style == default_style:
            # Style inchangé : on réutilise ``Default`` sans dupliquer
            # une ligne ``Style:`` identique.
            style_name = _DEFAULT_ASS_STYLE_NAME
        else:
            counter += 1
            style_name = f"S{counter}"
            style_lines.append(_ass_style_line(style, name=style_name))
        dialogues.append(
            _ass_dialogue_line(cue, style_name=style_name, style=style or default_style)
        )
    return _ass_template(style_lines, dialogues)


def _ass_template(style_lines: list[str], dialogue_lines: list[str]) -> str:
    """Génère le contenu complet d'un fichier ASS avec styles + dialogues."""
    header: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
    ]
    header.extend(style_lines)
    header.extend(
        [
            "",
            "[Events]",
            (
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
                "MarginV, Effect, Text"
            ),
        ]
    )
    header.extend(dialogue_lines)
    return "\n".join(header).rstrip() + "\n"