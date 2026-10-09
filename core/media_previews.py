"""Prévisualisations de médias pour la timeline.

Les formes d'onde et les vignettes ne sont jamais calculées pendant le
dessin. Ce module décide combien de vignettes il faut et sait extraire
une image via FFmpeg lorsque l'appelant le demande explicitement ; la
forme d'onde vient de l'enveloppe de crête du fichier
(:mod:`core.audio_envelope`), une par fichier, lue à toutes les échelles.

Le profil léger coupe les vignettes (``filmstrips=False``) ; les formes
d'onde restent. Un fichier absent ou un FFmpeg manquant retourne ``None``
(une enveloppe vide) : l'interface affiche alors le clip sans prévisualisation.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .cache_keys import audio_envelope_key, thumbnail_key
from .process_supervisor import supervised_run
from .tool_paths import bundled_tool_path


def thumbnail_slots(pixel_width: int, *, enabled: bool) -> int:
    """Nombre de vignettes utiles pour la largeur affichée du clip."""
    if not enabled or pixel_width < 96:
        return 0
    return max(1, min(6, int(pixel_width) // 96))


def thumbnail_source_times(source_in: float, source_out: float, slots: int) -> list[float]:
    """Instants source, au centre de chaque tranche du clip."""
    if slots <= 0:
        return []
    span = float(source_out) - float(source_in)
    if span <= 0.0:
        return []
    return [float(source_in) + span * (index + 0.5) / slots for index in range(slots)]


def audio_envelope_cache_key(path: str) -> str:
    """Clé de l'enveloppe de crête du fichier (forme d'onde, :mod:`core.audio_envelope`)."""
    return audio_envelope_key(path)


def thumbnail_cache_key(path: str, time_seconds: float, width: int) -> str:
    """Clé stable d'une vignette. Le temps est quantifié au dixième."""
    return thumbnail_key(path, time_seconds, width)


def extract_thumbnail(path: str, time_seconds: float, width: int = 160) -> bytes | None:
    """Extrait une image PNG à ``time_seconds``. À appeler hors de l'interface."""
    ffmpeg = bundled_tool_path("ffmpeg") or shutil.which("ffmpeg")
    if ffmpeg is None or not os.path.isfile(path):
        return None
    safe_width = max(32, min(320, int(width)))
    try:
        completed = supervised_run(
            [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                f"{max(0.0, float(time_seconds)):.3f}",
                "-i",
                path,
                "-frames:v",
                "1",
                "-vf",
                f"scale={safe_width}:-1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout
