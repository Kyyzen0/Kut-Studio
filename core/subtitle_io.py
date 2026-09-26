"""Lecture et écriture de sous-titres au format SRT.

Ce module implémente un sous-ensemble strict du format SubRip :

- timecodes ``HH:MM:SS,mmm`` (virgule décimale, précision milliseconde) ;
- indices incrémentés à partir de 1 ;
- texte multi-ligne ;
- encodage UTF-8.

Les fonctions exposées sont volontairement minimales :

- :func:`parse_srt` : parse une chaîne SRT en :class:`SubtitleCue` ;
- :func:`format_srt` : reconstruit une chaîne SRT à partir des cues ;
- :func:`load_srt` / :func:`save_srt` : lecture et écriture de fichiers.

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