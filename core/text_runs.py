"""Découpage d'un texte en mots, lettres et emojis, et état de chacun (visible, en couleur) pour le texte animé.

Sans Qt : le rastériseur (:mod:`core.mograph_raster`) place et dessine les morceaux, ce module décide **ce qui** est
visible à un ``reveal`` donné :

- ``word`` : les mots apparaissent l'un après l'autre (le mot en cours se fond en ``1 / n`` de la révélation) ;
- ``typewriter`` : lettre par lettre (les espaces ne comptent pas) ;
- ``karaoke`` : tout est visible, le mot courant prend ``highlight_color`` ;
- ``highlight_words`` : des mots toujours en couleur (« mot mis en couleur »), quel que soit le mode.

Les **emojis** sont repérés ici : un glyphe couleur n'a pas de contour vectoriel (``QPainterPath.addText`` le perd), le
rastériseur les dessine donc comme du texte, sans contour ni ombre.

Les mots sont comptés dans l'ordre du texte (une suite de caractères sans espace) : le retour à la ligne automatique ne
coupe jamais un mot, l'indice d'un mot ne dépend donc pas de la largeur du calque.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

WORD = re.compile(r"\S+")

WORD_FADE_SECONDS = 0.08
"""Fondu d'apparition d'un mot posé par ses temps (``word_times``) : assez court pour rester « sec »."""

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),   # pictogrammes, émoticônes, transports, symboles, drapeaux (indicateurs régionaux)
    (0x2600, 0x27BF),     # symboles divers, dingbats
    (0x2B00, 0x2BFF),     # flèches et étoiles (⭐)
    (0x2190, 0x21FF),     # flèches (↗)
    (0x2300, 0x23FF),     # technique (⌛ ⏱)
    (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3299),
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
)
_JOINERS = {0x200D, 0xFE0F, 0xFE0E, 0x20E3}           # liant (ZWJ), sélecteurs de variante, touche (keycap)
_MODIFIERS = (0x1F3FB, 0x1F3FF)                        # couleurs de peau
_TAGS = (0xE0020, 0xE007F)                             # drapeaux régionaux (Écosse…)


def is_emoji_char(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def _continues_emoji(char: str) -> bool:
    code = ord(char)
    return code in _JOINERS or _MODIFIERS[0] <= code <= _MODIFIERS[1] or _TAGS[0] <= code <= _TAGS[1]


def split_emoji(text: str) -> list[tuple[str, bool]]:
    """``[(morceau, est_un_emoji)]`` dans l'ordre ; une séquence ZWJ (👨‍👩‍👧), un drapeau ou une couleur de peau
    restent un seul emoji, un chiffre suivi de ⃣ aussi."""
    segments: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        char = text[index]
        starts_keycap = index + 1 < len(text) and text[index + 1] in "️⃣" and char in "0123456789#*"
        if is_emoji_char(char) or starts_keycap:
            end = index + 1
            while end < len(text) and (
                _continues_emoji(text[end])
                or (text[end - 1] == "‍" and is_emoji_char(text[end]))
                or (0x1F1E6 <= ord(text[end]) <= 0x1F1FF and 0x1F1E6 <= ord(text[end - 1]) <= 0x1F1FF
                    and (end - index) % 2 == 1)
            ):
                end += 1
            segments.append((text[index:end], True))
            index = end
            continue
        end = index + 1
        while end < len(text) and not is_emoji_char(text[end]) and not (
            end + 1 < len(text) and text[end + 1] in "️⃣" and text[end] in "0123456789#*"
        ):
            end += 1
        segments.append((text[index:end], False))
        index = end
    return segments


def has_emoji(text: str) -> bool:
    return any(is_emoji for _segment, is_emoji in split_emoji(text))


def word_count(text: str) -> int:
    return len(WORD.findall(text))


def letter_count(text: str) -> int:
    return sum(len(word) for word in WORD.findall(text))


@dataclass(frozen=True)
class RevealState:
    """Ce que montre un texte animé à un instant : opacité d'un mot ou d'une lettre, mot courant du karaoké."""

    mode: str
    reveal: float
    words: int
    letters: int
    highlighted: frozenset[int]

    def word_alpha(self, word: int) -> float:
        if self.mode != "word" or self.words == 0:
            return 1.0
        return max(0.0, min(1.0, self.reveal * self.words - word))

    def letter_alpha(self, letter: int) -> float:
        if self.mode != "typewriter" or self.letters == 0:
            return 1.0
        return max(0.0, min(1.0, self.reveal * self.letters - letter))

    def current_word(self) -> int | None:
        """Mot courant du karaoké (``None`` hors karaoké)."""
        if self.mode != "karaoke" or self.words == 0:
            return None
        return max(0, min(self.words - 1, int(self.reveal * self.words)))

    def is_highlighted(self, word: int) -> bool:
        return word in self.highlighted or word == self.current_word()


def reveal_state(text: str, mode: str, reveal: float, highlight_words: Sequence[int] = ()) -> RevealState:
    return RevealState(mode, max(0.0, min(1.0, float(reveal))), word_count(text), letter_count(text),
                       frozenset(int(word) for word in highlight_words))


def is_plain(text: str, mode: str, highlight_words: Sequence[int]) -> bool:
    """Texte « simple » : rendu par le chemin historique (un seul contour pour tout le texte, pixels inchangés)."""
    return mode == "none" and not highlight_words and not has_emoji(text)


def reveal_from_times(times: Sequence[float], local_time: float, words: int, mode: str) -> float | None:
    """``reveal`` qui correspond aux temps des mots (``word_times``, secondes du clip) à ``local_time`` ; ``None`` si les
    temps ne s'appliquent pas (pas de temps, mode sans apparition).

    ``word`` : un mot apparaît à son temps, en fondu de :data:`WORD_FADE_SECONDS` ; ``karaoke`` : le mot courant est
    le dernier dont le temps est passé (le premier avant son temps)."""
    if not times or words <= 0 or mode not in ("word", "karaoke"):
        return None
    started = sum(1 for t in times[:words] if t <= local_time + 1e-9)
    if mode == "karaoke":
        return (max(1, started) - 0.5) / words
    if started == 0:
        return 0.0
    fade = min(1.0, (local_time - times[started - 1]) / WORD_FADE_SECONDS)
    return min(1.0, (started - 1 + fade) / words)


__all__ = [
    "RevealState", "WORD", "WORD_FADE_SECONDS", "has_emoji", "is_emoji_char", "is_plain", "letter_count",
    "reveal_from_times", "reveal_state", "split_emoji", "word_count",
]
