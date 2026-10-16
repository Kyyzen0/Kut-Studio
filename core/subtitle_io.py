"""Lecture et écriture de sous-titres au format SRT et ASS (tâche 24).

Ce module implémente un sous-ensemble strict du format SubRip :

- timecodes ``HH:MM:SS,mmm`` (virgule décimale, précision milliseconde) ;
- indices incrémentés à partir de 1 ;
- texte multi-ligne ;
- écriture en UTF-8 ; lecture tolérante (BOM UTF-8/UTF-16, Windows-1252, voir :mod:`core.text_decoding`).

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

from .atomic_io import atomic_write_text
from .text_decoding import decode_text_bytes


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
    text = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
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
    """Charge un fichier SRT et retourne la liste des cues.

    L'encodage est deviné par :func:`~core.text_decoding.decode_text_bytes` : UTF-8 avec ou sans BOM, UTF-16 avec
    BOM (« Unicode » du Bloc-notes) et Windows-1252 des vieux SRT.
    """
    return parse_srt(decode_text_bytes(Path(file_path).read_bytes()))


def save_srt(cues: list[SubtitleCue], file_path: str) -> None:
    """Sérialise ``cues`` dans ``file_path`` au format SRT UTF-8, de façon atomique (le dossier est créé)."""
    atomic_write_text(file_path, format_srt(cues))


# ---------------------------------------------------------------------------
# ASS : préservation des styles typographiques
# ---------------------------------------------------------------------------


# Nom logique du style ASS par défaut. Limite ASS : « Alphanumeric only »,
# ce qui exclut les espaces et les tirets dans le nom de style.
_DEFAULT_ASS_STYLE_NAME = "Default"


def _escape_ass_text(text: str) -> str:
    """Échapper le texte pour un fichier ASS.

    Les sauts de ligne deviennent ``\\N``. Les accolades sont échappées pour
    qu'un sous-titre ou un ``.kut`` forgé n'injecte pas de balises d'override
    (``{\\an8}``, dessins, etc.). Les autres antislashs restent tels quels :
    libass ne les consomme que devant ``N``/``n``/``h``/``{``/``}``, et
    doubler tous les ``\\`` affichait ``C:\\\\Temp`` à l'écran.
    """
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _ass_font_name(font_family: str) -> str:
    """Nom de police sûr pour une ligne ``Style:`` ASS (CSV).

    Une virgule ou un saut de ligne décale les colonnes ; les accolades
    ouvriraient des overrides. Un nom vide retombe sur Arial.
    """
    cleaned = (
        str(font_family or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace(",", " ")
        .replace("{", "")
        .replace("}", "")
        .strip()
    )
    return cleaned or "Arial"


PLAY_RES_X = 1920
PLAY_RES_Y = 1080
"""Résolution du script ASS : les marges, la position et les épaisseurs s'y expriment, libass les ramène au cadre."""


def style_has_box(style) -> bool:
    """Le style demande-t-il un fond derrière le texte ? Une couleur de fond **et** une opacité non nulle."""
    return style is not None and style.background_color is not None and style.background_opacity > 0.0


def ass_position(style, *, force: bool = False) -> tuple[float, float] | None:
    """Point d'ancrage du texte (``\\pos``) en unités du script ASS, ou ``None`` sans décalage.

    Le point de base est celui que donnent l'alignement et les marges : le bord (à la marge) pour la gauche, la droite,
    le haut et le bas, le centre pour le milieu ; ``position_x`` / ``position_y`` le décalent en **pour cent du cadre**
    (positif : vers la droite, vers le bas). Sans décalage, rien n'est écrit : libass place le texte avec le style et
    les marges, exactement comme avant.

    ``force`` : renvoie le point même sans décalage. Une ligne **sans** ``\\pos`` est déplacée par la gestion des
    collisions de libass quand deux sous-titres se recouvrent ; son fond (calque 0) et son texte (calque 1) ne sont
    pas dans le même groupe de collision et pouvaient se séparer. Une ligne avec ``\\pos`` ne bouge pas."""
    from .text_style import alignment_to_ass

    if not force and not style.position_x and not style.position_y:
        return None
    code = alignment_to_ass(style.alignment) - 1
    column, row = code % 3, code // 3                       # 0 gauche / centre / droite ; 0 bas / milieu / haut
    base_x = (style.margin_x, PLAY_RES_X / 2.0, PLAY_RES_X - style.margin_x)[column]
    base_y = (PLAY_RES_Y - style.margin_y, PLAY_RES_Y / 2.0, style.margin_y)[row]
    return (base_x + style.position_x / 100.0 * PLAY_RES_X, base_y + style.position_y / 100.0 * PLAY_RES_Y)


def _ass_number(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _ass_style_line(style, *, name: str = _DEFAULT_ASS_STYLE_NAME, box: bool = False) -> str:
    """Formate une ligne ``Style:`` ASS pour un :class:`TextStyle`.

    ``box`` : le style du **calque de fond** (voir :func:`_ass_dialogue_lines`), pas celui du texte.

    L'ordre des colonnes est celui imposé par le bloc ``[V4+ Styles]`` :

    ``Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
    OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,
    ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,
    Alignment, MarginL, MarginR, MarginV, Encoding``

    Points de correspondance avec :class:`~core.text_style.TextStyle` :

    - ``PrimaryColour`` porte la couleur **et** l'opacité du texte ;
    - ``BackColour`` est la couleur de l'**ombre** en ASS (``shadow_color``) : avant, on y écrivait le fond, qui ne
      s'est donc jamais affiché, et la couleur d'ombre du style n'était jamais utilisée ;
    - le **fond** (``background_color`` / ``background_opacity``) est un calque à part : ``BorderStyle`` 3 (boîte
      opaque) dont ``OutlineColour`` est la couleur de la boîte et ``Outline`` sa marge interne ;
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
    back = ass_color(style.shadow_color, 1.0)
    border_style = 1 if (style.outline_width > 0 or style.shadow_offset > 0) else 0
    outline_width, shadow_offset = style.outline_width, style.shadow_offset
    if box:
        # Calque de fond : le texte y est invisible (seule la boîte se dessine), sans ombre.
        primary = secondary = ass_color(style.color, 0.0)
        outline_colour = ass_color(style.background_color or "#000000", style.background_opacity)
        back = ass_color("#000000", 0.0)
        border_style, outline_width, shadow_offset = 3, max(style.padding_x, style.padding_y), 0.0
    return (
        f"Style: {name},{_ass_font_name(style.font_family)},{int(round(style.font_size))},"
        f"{primary},{secondary},{outline_colour},{back},"
        # Bold, Italic, Underline, StrikeOut : non utilisés par Kut-Studio.
        "0,0,0,0,"
        # ScaleX, ScaleY, Spacing, Angle.
        "100,100,0,0,"
        f"{border_style},"
        f"{outline_width:.1f},"
        f"{shadow_offset:.1f},"
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
    *,
    layer: int = 0,
    overrides: str = "",
) -> str:
    """Formate une ligne ``Dialogue:`` ASS pour un :class:`SubtitleCue`.

    Les marges de la ligne reprennent celles du style (libass lit les
    marges de la ligne *et* celles du style ; les reporter ici évite
    toute ambiguïté selon la version de libass). ``overrides`` : balises
    ASS écrites avant le texte (``{\\pos(…)}``).
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
        f"Dialogue: {layer},{start},{end},{style_name},,"
        f"{margin_l},{margin_r},{margin_v},,{overrides}{text}"
    )


def _ass_dialogue_lines(cue, style_name: str, style) -> list[str]:
    """Les lignes ``Dialogue:`` d'un cue : son texte, précédé de son **fond** quand le style en demande un.

    Le fond est le même texte, invisible, sur un calque en dessous, dans le style ``<nom>Box`` (boîte opaque) : il suit
    donc la taille du texte, avec les marges internes ``padding_x`` / ``padding_y`` (``\\xbord`` / ``\\ybord``).
    Le texte, sur le calque du dessus, garde son contour et son ombre. Un décalage (``position_x`` / ``position_y``)
    s'écrit ``\\pos`` sur les deux, et un fond en écrit toujours un : libass décale les cues qui se recouvrent et
    séparait la boîte (calque 0) du texte (calque 1), qui sont dans des groupes de collision différents. Sans fond ni
    décalage : une seule ligne, comme avant."""
    # Un fond impose un ``\\pos`` (même sans décalage) : la boîte et le texte restent ensemble quand deux cues se recouvrent.
    position = ass_position(style, force=style_has_box(style)) if style is not None else None
    placement = f"\\pos({_ass_number(position[0])},{_ass_number(position[1])})" if position else ""
    if not style_has_box(style):
        return [_ass_dialogue_line(cue, style_name, style, overrides=f"{{{placement}}}" if placement else "")]
    padding = f"\\xbord{_ass_number(style.padding_x)}\\ybord{_ass_number(style.padding_y)}"
    return [
        _ass_dialogue_line(cue, f"{style_name}Box", style, layer=0, overrides=f"{{{padding}{placement}}}"),
        _ass_dialogue_line(cue, style_name, style, layer=1, overrides=f"{{{placement}}}" if placement else ""),
    ]


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
    if style_has_box(style):
        style_lines.append(_ass_style_line(style, name=f"{_DEFAULT_ASS_STYLE_NAME}Box", box=True))
    dialogue_lines = [
        line for cue in cues for line in _ass_dialogue_lines(cue, _DEFAULT_ASS_STYLE_NAME, style)
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
    if style_has_box(default_style):
        style_lines.append(_ass_style_line(default_style, name=f"{_DEFAULT_ASS_STYLE_NAME}Box", box=True))
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
            if style_has_box(style):
                style_lines.append(_ass_style_line(style, name=f"{style_name}Box", box=True))
        dialogues.extend(_ass_dialogue_lines(cue, style_name, style or default_style))
    return _ass_template(style_lines, dialogues)


def _ass_template(style_lines: list[str], dialogue_lines: list[str]) -> str:
    """Génère le contenu complet d'un fichier ASS avec styles + dialogues."""
    header: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        f"PlayResX: {PLAY_RES_X}",
        f"PlayResY: {PLAY_RES_Y}",
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