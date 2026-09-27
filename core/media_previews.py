"""Prévisualisations de médias pour la timeline.

Les waveforms et les vignettes ne sont jamais calculées pendant le
dessin. Ce module décide combien il en faut, réduit des échantillons
déjà lus, et sait extraire des pics ou une image via FFmpeg lorsque
l'appelant le demande explicitement.

Le profil léger coupe les vignettes (``filmstrips=False``). Les
waveforms restent disponibles, avec moins de colonnes sur une piste
compacte. Un fichier absent ou un FFmpeg manquant retourne ``None`` :
l'interface affiche alors le clip sans prévisualisation.
"""

from __future__ import annotations

import os
import shutil
import subprocess


def waveform_bins(pixel_width: int, height_mode: str) -> int:
    """Nombre de colonnes, quantifié pour ne pas recalculer à chaque pixel."""
    caps = {"compact": 64, "normal": 192, "large": 384}
    cap = caps.get(height_mode, 192)
    raw = max(16, min(cap, int(pixel_width)))
    return max(16, (raw // 16) * 16)


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


def peaks_from_samples(samples, bins: int) -> tuple[float, ...]:
    """Réduit une suite d'échantillons en pics normalisés entre 0 et 1."""
    count = len(samples)
    if bins <= 0 or count <= 0:
        return ()
    peaks: list[float] = []
    for index in range(bins):
        start = int(index * count / bins)
        end = max(start + 1, int((index + 1) * count / bins))
        chunk = samples[start:end]
        if not chunk:
            continue
        peaks.append(min(1.0, max(abs(float(value)) for value in chunk)))
    return tuple(peaks)


def _media_stamp(path: str) -> str:
    try:
        stat = os.stat(path)
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def waveform_cache_key(path: str, bins: int) -> str:
    """Clé stable tant que le fichier et le nombre de colonnes ne changent pas."""
    return f"wave:{os.path.abspath(path)}:{_media_stamp(path)}:{int(bins)}"


def thumbnail_cache_key(path: str, time_seconds: float, width: int) -> str:
    """Clé stable d'une vignette. Le temps est quantifié au dixième."""
    quantized = round(float(time_seconds), 1)
    return f"thumb:{os.path.abspath(path)}:{_media_stamp(path)}:{quantized}:{int(width)}"


def extract_waveform_peaks(path: str, bins: int) -> tuple[float, ...] | None:
    """Décode un aperçu audio mono à 200 Hz et le réduit en pics.

    Le décodage est borné par un timeout. Il doit être appelé hors du
    thread d'interface.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not os.path.isfile(path):
        return None
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                path,
                "-ac",
                "1",
                "-ar",
                "200",
                "-f",
                "f32le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = completed.stdout
    if completed.returncode != 0 or not raw:
        return None
    import array

    usable = len(raw) - (len(raw) % 4)
    if usable <= 0:
        return None
    samples = array.array("f")
    samples.frombytes(raw[:usable])
    peaks = peaks_from_samples(samples, bins)
    return peaks or None


def extract_thumbnail(path: str, time_seconds: float, width: int = 160) -> bytes | None:
    """Extrait une image PNG à ``time_seconds``. À appeler hors de l'interface."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not os.path.isfile(path):
        return None
    safe_width = max(32, min(320, int(width)))
    try:
        completed = subprocess.run(
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
