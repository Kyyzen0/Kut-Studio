"""Temps des mots d'un sous-titre karaoké, estimés depuis la voix (sans reconnaissance vocale).

Kut-Studio n'embarque pas de modèle de transcription. Le karaoké « synchronisé sur la voix » s'appuie donc sur ce que
le son dit sans le comprendre : **où** l'on parle. L'enveloppe d'énergie de la voix (10 ms, :func:`core.audio_sync.
raw_envelope`) donne les passages voisés ; les mots du texte s'y répartissent dans l'ordre, chacun selon son poids
syllabique. Une pause dans la voix tombe ainsi entre deux mots, et un mot long dure plus qu'un mot court.

C'est une **estimation** : elle suit le débit réel bien mieux qu'une répartition uniforme, mais un mot peut glisser
d'une syllabe. Les temps restent modifiables dans l'inspecteur (``GraphicOverlay.word_times``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from .text_runs import WORD

ENVELOPE_RATE = 100                  # trames par seconde (celles de core.audio_sync)
MIN_SEGMENT = 0.06                   # un passage voisé plus court est un clic, pas une syllabe
MAX_GAP = 0.12                       # deux passages séparés de moins se fondent (consonnes, respiration courte)
_VOWELS = re.compile(r"[aeiouyàâäéèêëîïôöùûüœæáíóúñ]+", re.IGNORECASE)


def syllable_weight(word: str) -> float:
    """Poids d'un mot : ses groupes de voyelles (au moins un) ; un sigle ou un nombre compte par caractère."""
    letters = re.sub(r"[^\w]", "", word)
    if not letters:
        return 0.5                                           # ponctuation isolée, emoji : un temps bref
    if letters.isupper() and len(letters) <= 4 or letters.isdigit():
        return float(len(letters))                          # « GP », « F1 », « 2026 » se prononcent lettre à lettre
    return float(max(1, len(_VOWELS.findall(letters))))


def voiced_segments(envelope: Sequence[float], rate: int = ENVELOPE_RATE) -> list[tuple[float, float]]:
    """Passages voisés ``(début, fin)`` en secondes d'une enveloppe d'énergie logarithmique."""
    np = _np()
    values = np.asarray(envelope, dtype=np.float64)
    if values.size == 0:
        return []
    floor, peak = np.percentile(values, 10), np.percentile(values, 95)
    if peak - floor < 1.0:                                   # moins de ~9 dB d'écart : rien ne se détache
        return []
    voiced = values > floor + 0.35 * (peak - floor)
    segments: list[list[float]] = []
    index = 0
    while index < values.size:
        if not voiced[index]:
            index += 1
            continue
        end = index
        while end < values.size and voiced[end]:
            end += 1
        start_s, end_s = index / rate, end / rate
        if segments and start_s - segments[-1][1] < MAX_GAP:
            segments[-1][1] = end_s
        else:
            segments.append([start_s, end_s])
        index = end
    return [(a, b) for a, b in segments if b - a >= MIN_SEGMENT]


def distribute_words(words: Sequence[str], segments: Sequence[tuple[float, float]]) -> list[float]:
    """Début de chaque mot : les mots remplissent les passages voisés dans l'ordre, au prorata de leur poids, puis les
    reprises de voix après une pause sont rattachées au mot le plus proche."""
    if not words:
        return []
    if not segments:
        raise ValueError("Aucune voix détectée sous ce texte.")
    weights = [syllable_weight(word) for word in words]
    voiced = sum(end - start for start, end in segments)
    unit = voiced / sum(weights)
    times: list[float] = []
    cursor = 0.0                                            # temps « voisé » cumulé
    for weight in weights:
        duration = weight * unit
        times.append(_voiced_to_real(segments, cursor))
        cursor += duration
    # Une reprise de voix après une pause est presque toujours un début de mot : les débuts de passage et les débuts
    # estimés sont appariés dans l'ordre (programmation dynamique, appariement le plus proche à moins de 0,8 mot
    # moyen), puis chaque mot apparié commence exactement à la reprise.
    times[0] = segments[0][0]
    for word, start in _align(times, [start for start, _end in segments[1:]], 0.8 * voiced / len(words)):
        times[word] = start
    return [round(t, 3) for t in times]


def _align(estimates: list[float], onsets: list[float], reach: float) -> list[tuple[int, float]]:
    """Appariement croissant (mot ≥ 1, reprise) qui maximise la somme des proximités ``1 − écart / reach``."""
    n, m = len(estimates), len(onsets)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(score[i - 1][j], score[i][j - 1])
            gap = abs(estimates[i - 1] - onsets[j - 1])
            if i > 1 and gap <= reach:                      # le premier mot est déjà posé sur le premier passage
                best = max(best, score[i - 1][j - 1] + 1.0 - gap / reach)
            score[i][j] = best
    pairs: list[tuple[int, float]] = []
    i, j = n, m
    while i > 0 and j > 0:
        gap = abs(estimates[i - 1] - onsets[j - 1])
        if i > 1 and gap <= reach and abs(score[i][j] - (score[i - 1][j - 1] + 1.0 - gap / reach)) < 1e-12:
            pairs.append((i - 1, onsets[j - 1]))
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def _voiced_to_real(segments, voiced_time: float) -> float:
    """Instant réel d'un temps « voisé » cumulé (les pauses ne comptent pas)."""
    elapsed = 0.0
    for start, end in segments:
        if voiced_time < elapsed + (end - start) - 1e-9:
            return start + voiced_time - elapsed
        elapsed += end - start
    return segments[-1][1]


def estimate_word_times(text: str, envelope: Sequence[float], *, rate: int = ENVELOPE_RATE) -> tuple[float, ...]:
    """Temps des mots de ``text`` (secondes depuis le début de l'enveloppe)."""
    return tuple(distribute_words(WORD.findall(text), voiced_segments(envelope, rate)))


def word_times_from_voice(
    text: str, media_path: str, *, media_start: float, duration: float, shift: float = 0.0,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[float, ...]:
    """Temps des mots de ``text`` lus sur la voix ``media_path`` (de ``media_start`` pendant ``duration``), décalés de
    ``shift`` (début de la voix dans le temps du calque texte). Les temps négatifs sont ramenés à 0."""
    from .audio_sync import raw_envelope

    envelope = raw_envelope(media_path, float(media_start), float(duration), cancelled)
    return tuple(max(0.0, round(t + shift, 3)) for t in estimate_word_times(text, envelope.tolist()))


def _np() -> Any:
    import numpy as np

    return np


__all__ = [
    "distribute_words", "estimate_word_times", "syllable_weight", "voiced_segments", "word_times_from_voice",
]
