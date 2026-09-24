"""Sonde vidéo minimaliste basée sur ``ffprobe``.

Ce module encapsule l'appel à ``ffprobe`` pour récupérer les métadonnées
d'un fichier vidéo (durée, résolution, fps) et construire un
``MediaAsset`` valide. Aucune dépendance à PySide6 : seul ``subprocess``
de la bibliothèque standard est utilisé, ce qui rend la sonde
utilisable hors contexte Qt (tests, scripts).

Périmètre volontairement limité :

- Fichiers contenant au moins un flux vidéo exploitable.
- Pas d'audio seul, pas d'image fixe, pas de proxy, pas de cache.
- Le chemin source est conservé tel quel ; aucune copie ni migration.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from .project_model import MediaAsset


class MediaProbeError(Exception):
    """Erreur levée par ``probe_video`` quand la sonde échoue."""

    pass


def probe_video(path: str) -> MediaAsset:
    """Analyse ``path`` via ``ffprobe`` et retourne un ``MediaAsset``.

    Raises:
        MediaProbeError: si le fichier est introuvable, si ``ffprobe``
            n'est pas disponible dans le ``PATH``, si ``ffprobe`` renvoie
            un code de sortie non nul, si sa sortie JSON est invalide,
            ou si le média ne contient aucun flux vidéo exploitable.
    """
    if not os.path.isfile(path):
        raise MediaProbeError(f"Fichier vidéo introuvable : {path}")

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        raise MediaProbeError(
            "ffprobe est introuvable dans le PATH. "
            "Installez FFmpeg pour pouvoir importer des médias."
        )

    try:
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,r_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(
            f"ffprobe a expiré en analysant {path} (>{exc.timeout}s)."
        ) from exc
    except OSError as exc:
        raise MediaProbeError(
            f"Impossible d'exécuter ffprobe sur {path} : {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        message = stderr or f"ffprobe a échoué (code {completed.returncode})"
        raise MediaProbeError(f"{message} ({path}).")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(
            f"Sortie ffprobe invalide pour {path} : {exc.msg}."
        ) from exc

    streams = payload.get("streams") or []
    if not streams:
        raise MediaProbeError(
            f"Aucun flux vidéo détecté dans {path}."
        )

    stream = streams[0]
    width = _safe_int(stream.get("width"))
    height = _safe_int(stream.get("height"))
    if width <= 0 or height <= 0:
        raise MediaProbeError(
            f"Dimensions vidéo invalides pour {path} ({width}x{height})."
        )

    fps = _parse_frame_rate(
        stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
    )
    if fps <= 0.0:
        raise MediaProbeError(
            f"Fréquence d'images invalide pour {path} (fps={fps})."
        )

    duration = _parse_duration(stream.get("duration"))
    if duration <= 0.0:
        duration = _parse_duration((payload.get("format") or {}).get("duration"))
    if duration <= 0.0:
        raise MediaProbeError(
            f"Durée vidéo invalide pour {path} (durée={duration}s)."
        )

    return MediaAsset(
        id=f"asset-{uuid.uuid4().hex[:12]}",
        path=path,
        name=Path(path).name,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        media_type="video",
    )


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_frame_rate(value) -> float:
    """Convertit un rationnel ffprobe ``"N/D"`` en fps ``float``."""
    if not value or not isinstance(value, str):
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        num = float(numerator)
        den = float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0
    if den == 0:
        return 0.0
    return num / den


def _parse_duration(value) -> float:
    """Convertit une durée ffprobe en secondes ``float``."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
