"""Enveloppe de crête d'un média audio : la forme d'onde des clips de la timeline.

Calculée **une fois par fichier** (en tâche de fond, cache mémoire), lue ensuite à toutes les échelles : à la largeur
affichée d'un clip, sur sa seule portion de média (``source_in`` → ``source_out``, vitesse et sens compris), au gain du
clip. On aligne donc une coupe sur le coup de caisse claire qu'on entend à cet endroit, à 2 ms près, au zoom maximal
comme au plus large.

L'ancienne forme d'onde réduisait **tout le fichier** à 64 à 384 colonnes étirées sur le clip (un morceau coupé à 30 s
montrait son début), d'après un son rééchantillonné à 200 Hz : le filtre anti-repliement ne laissait que les basses.

- :data:`DECODE_RATE` : le son est décodé en stéréo 16 kHz ; chaque crête est le plus grand ``|échantillon|`` des deux
  canaux sur sa fenêtre (une transitoire d'un seul canal compte).
- :data:`ENVELOPE_RATE` : 500 crêtes par seconde de média (une toutes les 2 ms), sur un octet chacune (1,8 Mo pour une
  heure). Lecture en flux : la mémoire ne dépend pas de la durée du fichier.
- Un média sans son, absent, ou que FFmpeg ne sait pas lire donne une enveloppe vide : rien n'est dessiné (jamais de
  forme d'onde inventée).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .process_supervisor import supervised_popen
from .tool_paths import bundled_tool_path

DECODE_RATE = 16_000
ENVELOPE_RATE = 500
WINDOW = DECODE_RATE // ENVELOPE_RATE
"""Échantillons par crête."""
EXTRACT_TIMEOUT = 300.0
"""Au-delà, le décodage est abandonné (FFmpeg bloqué) : l'enveloppe reste vide."""

_CHUNK_WINDOWS = 4096


@dataclass(frozen=True)
class AudioEnvelope:
    """Crêtes d'un média : ``peaks[i]`` (0 à 255, 255 = pleine échelle) couvre ``[i / rate, (i + 1) / rate)``."""

    rate: int
    peaks: bytes

    @property
    def duration(self) -> float:
        return len(self.peaks) / float(self.rate) if self.rate > 0 else 0.0

    def __bool__(self) -> bool:
        return bool(self.peaks)


EMPTY_ENVELOPE = AudioEnvelope(ENVELOPE_RATE, b"")


def extract_envelope(
    path: str, *, timeout: float = EXTRACT_TIMEOUT, cancelled: Callable[[], bool] | None = None,
) -> AudioEnvelope:
    """Décode le son de ``path`` et en tire l'enveloppe ; à appeler hors du fil de l'interface.

    ``cancelled`` est consulté entre deux blocs : vrai, le décodage s'arrête (enveloppe vide)."""
    import numpy as np

    ffmpeg = bundled_tool_path("ffmpeg") or shutil.which("ffmpeg")
    if ffmpeg is None or not os.path.isfile(path):
        return EMPTY_ENVELOPE
    command = [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-vn", "-sn", "-dn", "-ac", "2", "-ar", str(DECODE_RATE),
               "-f", "s16le", "pipe:1"]
    frame_bytes = 2 * 2                                    # stéréo, 16 bits
    chunk_bytes = _CHUNK_WINDOWS * WINDOW * frame_bytes
    pieces: list[bytes] = []
    pending = b""
    try:
        with supervised_popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            watchdog = threading.Timer(timeout, process.kill)
            watchdog.daemon = True
            watchdog.start()
            try:
                stream = process.stdout
                while stream is not None:
                    if cancelled is not None and cancelled():
                        process.kill()
                        return EMPTY_ENVELOPE
                    block = stream.read(chunk_bytes)
                    if not block:
                        break
                    data = pending + block
                    usable = len(data) - len(data) % (WINDOW * frame_bytes)
                    pending = data[usable:]
                    if usable:
                        pieces.append(_peaks(np.frombuffer(data[:usable], dtype="<i2")))
                if pending:
                    whole = len(pending) - len(pending) % frame_bytes
                    if whole:
                        pieces.append(_peaks(np.frombuffer(pending[:whole], dtype="<i2"), partial=True))
                code = process.wait()
            finally:
                watchdog.cancel()
    except OSError:
        return EMPTY_ENVELOPE
    if code != 0:
        return EMPTY_ENVELOPE
    return AudioEnvelope(ENVELOPE_RATE, b"".join(pieces))


def _peaks(samples, *, partial: bool = False) -> bytes:
    """Crête de chaque fenêtre de :data:`WINDOW` images stéréo, ramenée sur un octet (arrondi vers le haut : un son
    à peine audible reste visible)."""
    import numpy as np

    values = np.abs(samples.astype(np.int32))
    if partial:
        return bytes([min(255, math.ceil(int(values.max()) * 255 / 32768))]) if values.size else b""
    windows = values.reshape(-1, WINDOW * 2).max(axis=1)
    return np.minimum(255, -(-windows * 255 // 32768)).astype(np.uint8).tobytes()


def column_peaks(envelope: AudioEnvelope, edges: Sequence[float], *, gain: float = 1.0) -> list[float]:
    """Crête (0 à 1) de chaque colonne dont ``edges`` donne les bornes en secondes du média (``len(edges) - 1``
    colonnes). Les bornes vont dans un seul sens (un clip inversé les donne décroissantes) ; une colonne plus étroite
    qu'une crête prend la crête qui la contient ; au-delà de la fin du média, 0 (rien d'inventé). ``gain`` : facteur
    linéaire du clip, plafonné à la pleine échelle."""
    import numpy as np

    if len(edges) < 2 or not envelope:
        return []
    peaks = np.frombuffer(envelope.peaks, dtype=np.uint8)
    times = np.asarray(edges, dtype=np.float64)
    reverse = bool(times[-1] < times[0])
    if reverse:
        times = times[::-1]
    # Crêtes ``[i, i + 1)`` de chaque colonne : ``reduceat`` prend le maximum de chaque tranche, et la crête de départ
    # quand la tranche est vide (colonne plus fine qu'une crête). La sentinelle nulle, juste après la dernière crête,
    # est ce que voit une colonne au-delà de la fin du média.
    index = np.clip(np.floor(times * envelope.rate).astype(np.int64), 0, len(peaks))
    values = np.maximum.reduceat(np.append(peaks, np.uint8(0)), index)[:-1]
    result = np.minimum(1.0, values.astype(np.float64) / 255.0 * max(0.0, float(gain)))
    return (result[::-1] if reverse else result).tolist()


__all__ = [
    "DECODE_RATE", "EMPTY_ENVELOPE", "ENVELOPE_RATE", "AudioEnvelope", "column_peaks", "extract_envelope",
]
