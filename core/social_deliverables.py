"""Fichiers livrés avec un export social : une copie d'aperçu légère (< 30 Mo) et une image de couverture.

Les deux sont tirés **du MP4 exporté** après le rendu, pas d'un second rendu du projet : la copie et la couverture
montrent exactement la vidéo livrée (normalisation du son comprise), et le travail coûte un transcodage court.

- **Copie d'aperçu** : petit côté ramené à 720 px, débit vidéo calculé sur la durée pour tenir sous
  :data:`PREVIEW_MAX_BYTES` avec 10 % de marge. La taille obtenue est vérifiée ; un dépassement (encodeur trop
  généreux sur une image très détaillée) relance un essai avec un débit réduit d'autant, puis échoue s'il persiste.
- **Couverture** : l'image qui contient l'instant demandé (marqueur de catégorie ``cover``, sinon la tête de lecture à
  l'ajout de l'export), en JPEG à la résolution de l'export.

Les deux commandes passent par :mod:`core.process_supervisor`.
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import process_supervisor

PREVIEW_MAX_BYTES = 30_000_000
"""Plafond de la copie d'aperçu : 30 Mo (décimaux, la lecture la plus stricte de « 30 Mo »)."""
PREVIEW_SHORT_SIDE = 720
PREVIEW_AUDIO_KBPS = 96
PREVIEW_MARGIN = 0.10
MIN_VIDEO_KBPS, MAX_VIDEO_KBPS = 250, 5000


class DeliverableError(RuntimeError):
    """Une copie d'aperçu ou une couverture n'a pas pu être produite."""


@dataclass(frozen=True)
class Deliverables:
    """Ce qu'il faut produire après un export : copie légère, et/ou couverture à ``cover_seconds``."""

    preview_copy: bool = False
    cover: bool = False
    cover_seconds: float = 0.0
    reserved: tuple[str, ...] = ()
    """Sorties d'autres exports de la file : un livrable ne prend jamais leur nom."""

    @property
    def any(self) -> bool:
        return self.preview_copy or self.cover


def preview_copy_path(output: str | os.PathLike[str]) -> Path:
    path = Path(output)
    return path.with_name(f"{path.stem}-apercu.mp4")


def cover_path(output: str | os.PathLike[str]) -> Path:
    path = Path(output)
    return path.with_name(f"{path.stem}-couverture.jpg")


def free_path(path: Path, reserved=()) -> Path:
    """``path`` s'il est libre, sinon ``<nom>-2``, ``<nom>-3``… : un livrable n'écrase jamais un fichier existant ni la
    sortie d'un autre export de la file (l'utilisateur n'a choisi que le chemin de la vidéo)."""
    taken = {os.path.normcase(os.path.abspath(str(item))) for item in reserved}
    candidate, index = path, 1
    while candidate.exists() or os.path.normcase(os.path.abspath(str(candidate))) in taken:
        index += 1
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
    return candidate


def preview_size(width: int, height: int) -> tuple[int, int]:
    """Taille de la copie : petit côté à 720 px (jamais agrandi), côtés pairs (H.264 4:2:0)."""
    scale = min(1.0, PREVIEW_SHORT_SIDE / max(1, min(width, height)))
    return max(2, int(round(width * scale / 2)) * 2), max(2, int(round(height * scale / 2)) * 2)


def preview_video_kbps(duration: float, max_bytes: int = PREVIEW_MAX_BYTES) -> int:
    """Débit vidéo (kbit/s) qui tient ``duration`` secondes sous ``max_bytes`` avec la marge et l'audio."""
    budget_kbps = max_bytes * 8 * (1.0 - PREVIEW_MARGIN) / max(0.1, float(duration)) / 1000.0
    return int(max(MIN_VIDEO_KBPS, min(MAX_VIDEO_KBPS, math.floor(budget_kbps - PREVIEW_AUDIO_KBPS))))


def _ffmpeg() -> list[str]:
    from .export_engine import _ffmpeg_command_prefix

    try:
        prefix = _ffmpeg_command_prefix()
    except ImportError as error:
        raise DeliverableError("FFmpeg est introuvable.") from error
    return [*prefix, "-nostdin", "-hide_banner", "-loglevel", "error", "-n"]     # -n : jamais d'écrasement


def preview_copy_command(source: str, dest: str, width: int, height: int, video_kbps: int) -> list[str]:
    out_w, out_h = preview_size(width, height)
    return [
        *_ffmpeg(), "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale={out_w}:{out_h}:flags=lanczos", "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{2 * video_kbps}k",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{PREVIEW_AUDIO_KBPS}k", "-movflags", "+faststart", str(dest),
    ]


def cover_seek(seconds: float, duration: float, fps: float) -> float:
    """Instant à passer à ``-ss`` pour obtenir l'image qui **contient** ``seconds``.

    ``-ss t`` rend la première image dont l'horodatage est ≥ t : on vise donc un quart d'image avant le début de
    l'image voulue, ce qui tolère les arrondis sans déborder sur la précédente."""
    fps = max(1.0, float(fps))
    last = max(0, math.ceil(float(duration) * fps) - 1)
    index = min(last, max(0, math.floor(float(seconds) * fps + 1e-6)))
    return max(0.0, (index - 0.25) / fps)


def cover_command(source: str, dest: str, seconds: float, duration: float, fps: float) -> list[str]:
    return [*_ffmpeg(), "-ss", f"{cover_seek(seconds, duration, fps):.6f}", "-i", str(source),
            "-frames:v", "1", "-update", "1", "-q:v", "2", str(dest)]


def _run(command: list[str], timeout: float) -> None:
    completed = process_supervisor.supervised_run(command, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        tail = " | ".join((completed.stderr or "").strip().splitlines()[-3:])
        raise DeliverableError(f"FFmpeg a refusé la commande : {tail}")


def make_preview_copy(source: str, width: int, height: int, duration: float,
                      max_bytes: int = PREVIEW_MAX_BYTES, *, reserved=()) -> Path:
    """Écrit la copie d'aperçu à côté de ``source`` (nom libre) et vérifie qu'elle tient sous ``max_bytes``."""
    dest = free_path(preview_copy_path(source), reserved)
    kbps = preview_video_kbps(duration, max_bytes)
    budget = 60.0 + 4.0 * float(duration)
    for _attempt in range(2):
        dest.unlink(missing_ok=True)                  # seulement notre propre essai précédent (le nom était libre)
        _run(preview_copy_command(source, str(dest), width, height, kbps), budget)
        size = dest.stat().st_size
        if size <= max_bytes:
            return dest
        kbps = max(MIN_VIDEO_KBPS, int(kbps * max_bytes * (1.0 - PREVIEW_MARGIN) / size))
    dest.unlink(missing_ok=True)
    raise DeliverableError(f"La copie d'aperçu dépasse {max_bytes // 1_000_000} Mo même à débit réduit.")


def make_cover(source: str, seconds: float, duration: float, fps: float, *, reserved=()) -> Path:
    dest = free_path(cover_path(source), reserved)
    _run(cover_command(source, str(dest), seconds, duration, fps), 60.0)
    if not dest.is_file():
        raise DeliverableError("FFmpeg n'a pas écrit l'image de couverture.")
    return dest


def make_deliverables(source: str, wanted: Deliverables, *, width: int, height: int, duration: float,
                      fps: float) -> tuple[list[str], str]:
    """Produit ce qui est demandé ; retourne ``(fichiers écrits, erreur éventuelle)``.

    Un échec ne retire rien à l'export principal (déjà terminé) : il est rapporté, pas levé."""
    written: list[str] = []
    errors: list[str] = []
    if wanted.cover:
        try:
            written.append(str(make_cover(source, wanted.cover_seconds, duration, fps, reserved=wanted.reserved)))
        except (DeliverableError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    if wanted.preview_copy:
        try:
            written.append(str(make_preview_copy(source, width, height, duration, reserved=wanted.reserved)))
        except (DeliverableError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    return written, " ; ".join(errors)


__all__ = [
    "DeliverableError", "Deliverables", "PREVIEW_MAX_BYTES", "cover_command", "cover_path", "cover_seek", "free_path",
    "make_cover", "make_deliverables", "make_preview_copy", "preview_copy_command", "preview_copy_path",
    "preview_size", "preview_video_kbps",
]
