"""Analyse d'une sortie : ``ffprobe`` (durée, flux, cadence, format de pixels) et recherche d'images noires ou gelées.

L'analyse d'image décode toute la vidéo une fois (à 2 images par seconde, en 160 pixels de large) avec ``blackdetect`` et
``freezedetect`` : une analyse simple, pas un contrôle qualité. Elle rend des **ratios** (part de la durée noire, part gelée) ;
le seuil qui en fait un défaut est dans le rapport, jamais dans un test de durée.
"""

from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

BLACK_RATIO_WARN = 0.10
FROZEN_RATIO_WARN = 0.50
DURATION_TOLERANCE = 0.03
"""Écart relatif toléré entre la durée de la sortie et celle de la timeline (correction du résultat, pas performance)."""
ANALYSIS_FPS = 2
_BLACK = re.compile(r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)\s+black_duration:(?P<duration>[\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*(?P<value>[\d.]+)")
_FREEZE_DURATION = re.compile(r"freeze_duration:\s*(?P<value>[\d.]+)")


def _fraction(text: str | None) -> float | None:
    try:
        value = Fraction(text or "")
    except (ValueError, ZeroDivisionError):
        return None
    return float(value) if value > 0 else None


def parse_ffprobe(raw: dict[str, Any]) -> dict[str, Any]:
    """Résumé stable d'une sortie ``ffprobe -of json`` (durée du format, flux utiles)."""
    streams: list[dict[str, Any]] = []
    for stream in raw.get("streams", []) or []:
        kind = stream.get("codec_type")
        if kind not in ("video", "audio"):
            continue
        entry: dict[str, Any] = {"type": kind, "codec": stream.get("codec_name"), "duration": _number(stream.get("duration"))}
        if kind == "video":
            entry.update(width=stream.get("width"), height=stream.get("height"), pix_fmt=stream.get("pix_fmt"),
                         fps=_fraction(stream.get("avg_frame_rate")) or _fraction(stream.get("r_frame_rate")),
                         frames=_integer(stream.get("nb_frames")), color_range=stream.get("color_range"),
                         color_space=stream.get("color_space"))
        else:
            entry.update(sample_rate=_integer(stream.get("sample_rate")), channels=stream.get("channels"))
        streams.append(entry)
    form = raw.get("format", {}) or {}
    return {"duration": _number(form.get("duration")), "size_bytes": _integer(form.get("size")), "format": form.get("format_name"),
            "bit_rate": _integer(form.get("bit_rate")), "streams": streams}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ffprobe(ffprobe_path: str | None, media: Path) -> dict[str, Any]:
    if not ffprobe_path:
        return {"error": "ffprobe introuvable"}
    try:
        done = subprocess.run([ffprobe_path, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(media)],
                              capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return {"error": type(error).__name__}
    if done.returncode != 0:
        return {"error": f"ffprobe code {done.returncode}: {done.stderr.strip()[-200:]}"}
    try:
        return parse_ffprobe(json.loads(done.stdout))
    except ValueError:
        return {"error": "sortie ffprobe illisible"}


def parse_detection(log: str) -> dict[str, Any]:
    """Intervalles noirs et gelés d'un journal FFmpeg (``blackdetect`` / ``freezedetect``)."""
    black = [(float(m["start"]), float(m["end"])) for m in _BLACK.finditer(log)]
    starts = [float(m["value"]) for m in _FREEZE_START.finditer(log)]
    durations = [float(m["value"]) for m in _FREEZE_DURATION.finditer(log)]
    frozen = [(start, start + length) for start, length in zip(starts, durations, strict=False)]
    return {"black": black, "frozen": frozen}


def detection_command(ffmpeg: str, media: Path) -> list[str]:
    graph = f"fps={ANALYSIS_FPS},scale=160:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2"
    return [ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-i", str(media), "-an", "-vf", graph, "-f", "null", "-"]


def analyse_frames(ffmpeg: str | None, media: Path, duration: float | None) -> dict[str, Any]:
    """Part de la durée noire / gelée. ``black_ratio`` et ``frozen_ratio`` valent ``None`` si l'analyse n'a pas pu tourner."""
    if not ffmpeg:
        return {"error": "ffmpeg introuvable", "black_ratio": None, "frozen_ratio": None}
    try:
        done = subprocess.run(detection_command(ffmpeg, media), capture_output=True, text=True, timeout=3600, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return {"error": type(error).__name__, "black_ratio": None, "frozen_ratio": None}
    if done.returncode != 0:
        return {"error": f"ffmpeg code {done.returncode}", "black_ratio": None, "frozen_ratio": None}
    found = parse_detection(done.stderr)
    return {"black_ratio": ratio(found["black"], duration), "frozen_ratio": ratio(found["frozen"], duration),
            "black_intervals": [list(item) for item in found["black"][:20]],
            "frozen_intervals": [list(item) for item in found["frozen"][:20]]}


def ratio(intervals: list[tuple[float, float]], duration: float | None) -> float | None:
    """Part (0 à 1) de ``duration`` couverte par ``intervals`` (recouvrements fusionnés)."""
    if not duration or duration <= 0:
        return None
    covered, end = 0.0, 0.0
    for start, stop in sorted(intervals):
        start, stop = max(start, end), min(stop, duration)
        if stop > start:
            covered += stop - start
            end = stop
    return round(min(1.0, covered / duration), 4)


def warnings_for(frames: dict[str, Any]) -> list[str]:
    """Signes à regarder sans faire échouer le parcours : toute plage noire ou gelée repérée (échantillonnage à 2 images/s)."""
    notes: list[str] = []
    for label, key in (("noire", "black_intervals"), ("gelée", "frozen_intervals")):
        spans = frames.get(key) or []
        if spans:
            shown = ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in spans[:5])
            notes.append(f"{len(spans)} plage(s) {label} : {shown}" + (" …" if len(spans) > 5 else ""))
    return notes


def check_output(summary: dict[str, Any], frames: dict[str, Any], *, expected_duration: float | None, expected_size: tuple[int, int] | None,
                 expect_audio: bool = True) -> list[str]:
    """Défauts constatés sur la sortie (liste vide = rien à signaler). Écart de durée en ratio, jamais en secondes absolues."""
    problems: list[str] = []
    if "error" in summary:
        return [f"ffprobe : {summary['error']}"]
    video = [s for s in summary["streams"] if s["type"] == "video"]
    sound = [s for s in summary["streams"] if s["type"] == "audio"]
    if len(video) != 1:
        problems.append(f"{len(video)} flux vidéo (attendu : 1)")
    if expect_audio and not sound:
        problems.append("aucun flux audio")
    if video and expected_size and (video[0].get("width"), video[0].get("height")) != tuple(expected_size):
        problems.append(f"taille {video[0].get('width')}x{video[0].get('height')} (attendu : {expected_size[0]}x{expected_size[1]})")
    if video and video[0].get("pix_fmt") != "yuv420p":
        problems.append(f"format de pixels {video[0].get('pix_fmt')} (attendu : yuv420p)")
    duration = summary.get("duration")
    if expected_duration and duration is not None and abs(duration / expected_duration - 1.0) > DURATION_TOLERANCE:
        problems.append(f"durée {duration:.2f} s pour {expected_duration:.2f} s de timeline")
    if frames.get("black_ratio") is not None and frames["black_ratio"] > BLACK_RATIO_WARN:
        problems.append(f"{frames['black_ratio']:.0%} de la durée est noire")
    if frames.get("frozen_ratio") is not None and frames["frozen_ratio"] > FROZEN_RATIO_WARN:
        problems.append(f"{frames['frozen_ratio']:.0%} de la durée est gelée")
    return problems
