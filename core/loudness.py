"""Loudness d'un export : mesure EBU R 128 du mixage final et normalisation statique (−14 LUFS pour les réseaux).

Deux passes, déterministes :

1. **mesure** — le mixage du plan (le même graphe que l'export, sans l'image) passe par ``ebur128`` : loudness intégrée,
   plage (LRA), crête ;
2. **gain** — l'export ajoute ``cible − mesure`` dB après le Master, puis un limiteur à −1 dBFS
   (:data:`core.export_engine.LOUDNESS_LIMITER`).

Un gain statique, et non ``loudnorm`` dynamique : la dynamique du montage (le drop, le silence avant l'impact) est
gardée telle quelle, seul le niveau d'ensemble bouge. Une crête qui dépasserait −1 dBFS est rattrapée par le limiteur,
ce qui baisse à peine la loudness intégrée (testé : un mixage qu'il faut beaucoup remonter, ou presque saturé, sort à
moins de 0,5 LU de la cible, crête sous −1 dBFS).

La normalisation est un **réglage d'export** (comme le codec) : le projet et l'aperçu ne changent pas.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from . import process_supervisor
from .tool_paths import find_media_tool

SOCIAL_TARGET_LUFS = -14.0
"""Loudness visée par TikTok, Instagram et YouTube (normalisation des plateformes)."""

MIN_GAIN_DB, MAX_GAIN_DB = -40.0, 40.0
_SUMMARY = re.compile(
    r"Integrated loudness:\s*I:\s*(?P<i>-?[\d.]+|-inf)\s*LUFS.*?"
    r"Loudness range:\s*LRA:\s*(?P<lra>-?[\d.]+)\s*LU.*?"
    r"(?:True peak|Sample peak):\s*Peak:\s*(?P<peak>-?[\d.]+|-inf)\s*dBFS",
    re.S,
)


class LoudnessError(RuntimeError):
    """La mesure a échoué (FFmpeg absent, graphe refusé)."""


@dataclass(frozen=True)
class LoudnessMeasure:
    """Mesure EBU R 128 : loudness intégrée (LUFS), plage (LU), crête vraie (dBFS)."""

    integrated: float
    lra: float
    true_peak: float

    @property
    def silent(self) -> bool:
        return not math.isfinite(self.integrated) or self.integrated < -69.0


def parse_ebur128(text: str) -> LoudnessMeasure:
    """Lit le résumé final d'``ebur128`` (sortie d'erreur de FFmpeg)."""
    matches = list(_SUMMARY.finditer(text or ""))
    if not matches:
        raise LoudnessError("Résumé ebur128 introuvable dans la sortie de FFmpeg.")
    found = matches[-1]

    def number(value: str) -> float:
        return float("-inf") if value == "-inf" else float(value)

    return LoudnessMeasure(number(found["i"]), float(found["lra"]), number(found["peak"]))


def measure_command(plan, fps: int, *, width: int | None = None, height: int | None = None) -> list[str]:
    """Commande FFmpeg qui mesure le mixage du plan (graphe de l'export, sans l'image)."""
    from .export_engine import ExportEngine, _ffmpeg_command_prefix

    ffmpeg = find_media_tool("ffmpeg")
    if ffmpeg is None:
        raise LoudnessError("FFmpeg est introuvable : mesure de loudness impossible.")
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        plan, int(width or plan.width), int(height or plan.height), int(fps), None, audio_only=True,
    )
    # FFmpeg 9 refuse un graphe sans sortie : la mesure sort vers le muxeur ``null`` (rien n'est écrit).
    graph += f";[{audio}]ebur128=peak=true:framelog=quiet[lm]"
    command = [*_ffmpeg_command_prefix(), "-nostdin", "-hide_banner", "-nostats"]
    for path in inputs:
        command += ["-i", path]
    return command + ["-filter_complex", graph, "-map", "[lm]", "-f", "null", "-"]


def measure_plan(plan, fps: int, *, timeout: float | None = None) -> LoudnessMeasure:
    """Loudness du mixage final d'un plan (Master compris, sans la normalisation demandée)."""
    plan = replace(plan, loudness_gain_db=None)
    command = measure_command(plan, fps)
    budget = timeout if timeout is not None else 60.0 + 2.0 * float(getattr(plan, "duration", 0.0))
    completed = process_supervisor.supervised_run(command, capture_output=True, text=True, timeout=budget)
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        raise LoudnessError("Mesure de loudness refusée par FFmpeg : " + " | ".join(tail))
    return parse_ebur128(completed.stderr)


def normalization_gain(measure: LoudnessMeasure, target: float = SOCIAL_TARGET_LUFS) -> float | None:
    """Gain (dB) qui amène ``measure`` à ``target`` ; ``None`` pour un mixage silencieux (rien à normaliser)."""
    if measure.silent:
        return None
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, round(float(target) - measure.integrated, 2)))


def normalized_plan(plan, fps: int, target: float = SOCIAL_TARGET_LUFS):
    """``(plan avec son gain de loudness, mesure)`` : la première passe d'un export normalisé."""
    measure = measure_plan(plan, fps)
    return replace(plan, loudness_gain_db=normalization_gain(measure, target)), measure


__all__ = [
    "LoudnessError", "LoudnessMeasure", "SOCIAL_TARGET_LUFS", "measure_command", "measure_plan", "normalization_gain",
    "normalized_plan", "parse_ebur128",
]
