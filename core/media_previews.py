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
import subprocess

from .cache_keys import audio_envelope_key, thumbnail_key
from .color_matrix import asset_ffmpeg_override, asset_matrix_inputs, cache_token, setparams_filter
from .process_supervisor import supervised_run
from .tool_paths import find_media_tool


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


def thumbnail_cache_key(path: str, time_seconds: float, width: int, asset=None) -> str:
    """Clé stable d'une vignette. Le temps est quantifié au dixième.

    ``asset`` (optionnel) : le média, dont la matrice couleur (étiquette, taille, choix de l'utilisateur) entre dans la clé.
    Calculée sans sonde (appelée à chaque dessin de la timeline) : un média HD pas encore sondé est supposé non étiqueté ; la
    sonde de :func:`extract_thumbnail` mémorise l'étiquette réelle et la clé s'ajuste alors, au pire par un nouveau calcul."""
    token = cache_token(*asset_matrix_inputs(asset, probe=False)) if asset is not None else ""
    return thumbnail_key(path, time_seconds, width, matrix=token)


def extract_thumbnail(path: str, time_seconds: float, width: int = 160, asset=None) -> bytes | None:
    """Extrait une image PNG à ``time_seconds``. À appeler hors de l'interface.

    ``asset`` (optionnel) : le média ; sa matrice couleur est dite à FFmpeg quand elle n'est pas celle qu'il lirait seul
    (:func:`core.color_matrix.asset_ffmpeg_override`), comme à l'export : la vignette a les couleurs du clip."""
    ffmpeg = find_media_tool("ffmpeg")
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
                ",".join(filter(None, (setparams_filter(asset_ffmpeg_override(asset)), f"scale={safe_width}:-1"))),
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
