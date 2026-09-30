"""Sonde générique ``ffprobe`` pour les médias Kut-Studio.

Ce module encapsule l'appel à ``ffprobe`` pour récupérer les
métadonnées d'un fichier média (vidéo, audio seul, vidéo sans piste
son…) et construire un :class:`~core.project_model.MediaAsset`
correctement configuré :

- un fichier contenant au moins un flux **vidéo** est rapporté comme
  ``media_type="video"`` avec sa largeur / hauteur / fps ;
- un fichier contenant uniquement des flux **audio** est rapporté
  comme ``media_type="audio"`` avec ``width=0``, ``height=0`` et
  ``fps=0.0`` ;
- le booléen ``has_audio`` est positionné selon la présence d'un flux
  audio exploitable (toujours ``True`` pour un asset ``audio``).
- tout autre cas (image fixe, sous-titres seuls, fichier corrompu)
  lève :class:`MediaProbeError`.

Aucune dépendance à PySide6 : seul ``subprocess`` de la bibliothèque
standard est utilisé, ce qui rend la sonde utilisable hors contexte Qt
(tests, scripts).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from .project_model import MediaAsset
from .tool_paths import bundled_tool_path


class MediaProbeError(Exception):
    """Erreur levée par les sondes ``probe_media`` / ``probe_video``."""

    pass


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def probe_media(path: str) -> MediaAsset:
    """Analyse ``path`` via ``ffprobe`` et retourne un :class:`MediaAsset`.

    Le résultat distingue :

    - **vidéo** : au moins un flux vidéo exploitable, ``has_audio``
      reflète la présence d'un flux audio séparé ;
    - **audio seul** : uniquement des flux audio, ``width=0``,
      ``height=0``, ``fps=0.0`` et ``has_audio=True``.

    Raises:
        MediaProbeError: si le fichier est introuvable, si ``ffprobe``
            n'est pas disponible dans le ``PATH``, si ``ffprobe``
            renvoie un code de sortie non nul, si sa sortie JSON est
            invalide, ou si le média ne contient ni flux vidéo ni
            flux audio exploitable.
    """
    if not os.path.isfile(path):
        raise MediaProbeError(f"Fichier média introuvable : {path}")

    ffprobe_path = bundled_tool_path("ffprobe") or shutil.which("ffprobe")
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
                "-show_format",
                "-show_streams",
                "-print_format",
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
    format_data = payload.get("format") or {}
    duration = _parse_duration(format_data.get("duration"))

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if audio_streams and not video_streams:
        return _build_audio_asset(path, duration)
    if video_streams:
        return _build_video_asset(
            path, video_streams[0], duration, has_audio=bool(audio_streams)
        )
    raise MediaProbeError(
        f"Aucun flux vidéo ni audio exploitable détecté dans {path}."
    )


def probe_video(path: str) -> MediaAsset:
    """Compatibilité historique : sonde vidéo stricte.

    Délègue à :func:`probe_media` et ne retourne que les médias
    contenant un flux vidéo ; un fichier audio seul lève une
    :class:`MediaProbeError` explicite. Conservée pour les appels
    existants ; les nouveaux imports doivent utiliser
    :func:`probe_media`.
    """
    asset = probe_media(path)
    if asset.media_type != "video":
        raise MediaProbeError(
            f"Aucun flux vidéo détecté dans {path}."
        )
    return asset


# ---------------------------------------------------------------------------
# Construction des MediaAsset par type
# ---------------------------------------------------------------------------


def _build_video_asset(
    path: str,
    stream: dict,
    duration: float,
    *,
    has_audio: bool,
) -> MediaAsset:
    """Construit un :class:`MediaAsset` de type ``video``."""
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

    stream_duration = _parse_duration(stream.get("duration"))
    if duration <= 0.0:
        duration = stream_duration
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
        has_audio=has_audio,
    )


def _build_audio_asset(path: str, duration: float) -> MediaAsset:
    """Construit un :class:`MediaAsset` de type ``audio``."""
    if duration <= 0.0:
        raise MediaProbeError(
            f"Durée audio invalide pour {path} (durée={duration}s)."
        )
    return MediaAsset(
        id=f"asset-{uuid.uuid4().hex[:12]}",
        path=path,
        name=Path(path).name,
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
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
