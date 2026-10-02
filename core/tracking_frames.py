"""Lecture des images d'analyse du tracking (FFmpeg → niveaux de gris).

Choix de la source
------------------

L'analyse lit **le média original**, réduit par FFmpeg à une résolution
d'analyse (:class:`core.tracking_model.Precision` : 1080p au plus par
défaut, un 4K est donc analysé en 1920 × 1080). Un proxy peut être lu à la
place pour aller plus vite (option) : FFmpeg le ramène à la **même**
résolution d'analyse, si bien que les coordonnées sont identiques et
toujours converties en pixels du média original. Le résultat est enregistré
dans le projet : le rendu et l'export ne dépendent ni du proxy, ni du média
d'analyse, ni du cache.

Temps
-----

L'image ``i`` est celle du média à ``i / rate`` secondes (``rate`` =
cadence nominale du média). Le filtre ``fps`` rééchantillonne une source à
cadence variable sur cette grille, comme l'export le fait sur la grille du
projet. Seules les images de la plage demandée sont décodées (recherche
précise ``-ss`` avant ``-i``) ; à rebours, la plage est lue par blocs, du
plus tardif au plus ancien.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .tool_paths import find_media_tool
from .tracking_model import Precision


class MediaOffline(RuntimeError):
    """Le média à analyser est introuvable."""


class FrameReadError(RuntimeError):
    """FFmpeg n'a pas pu lire le média."""


@dataclass(frozen=True)
class AnalysisGeometry:
    """Résolution d'analyse et facteur vers les pixels du média original."""

    media_width: int
    media_height: int
    width: int
    height: int

    @property
    def scale(self) -> float:
        """Pixels d'analyse par pixel original (≤ 1)."""
        return self.width / max(1, self.media_width)

    @property
    def scale_y(self) -> float:
        return self.height / max(1, self.media_height)

    def to_analysis(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale, y * self.scale_y)

    def to_media(self, x: float, y: float) -> tuple[float, float]:
        return (x / self.scale, y / self.scale_y)

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height


def analysis_geometry(media_width: int, media_height: int, precision: str = Precision.AUTO) -> AnalysisGeometry:
    """Taille d'analyse : largeur ≤ plafond de la précision, dimensions paires."""
    w, h = max(2, int(media_width)), max(2, int(media_height))
    limit = Precision.MAX_WIDTH.get(precision, Precision.MAX_WIDTH[Precision.AUTO])
    factor = min(1.0, limit / w)
    width = max(16, int(round(w * factor / 2.0)) * 2)
    height = max(16, int(round(h * factor / 2.0)) * 2)
    return AnalysisGeometry(w, h, min(width, w + (w % 2)), min(height, h + (h % 2)))


def _ffmpeg() -> str:
    tool = find_media_tool("ffmpeg")
    if not tool:
        raise FrameReadError("FFmpeg est introuvable.")
    return tool


def _seconds(value: float) -> str:
    return f"{max(0.0, float(value)):.6f}"


class FrameReader:
    """Décode une plage d'images en niveaux de gris (``numpy.ndarray`` 2D, uint8).

    ``cancelled`` est interrogé entre deux images : l'annulation arrête
    FFmpeg immédiatement.
    """

    def __init__(
        self,
        path: str,
        geometry: AnalysisGeometry,
        rate: float,
        *,
        cancelled: Callable[[], bool] | None = None,
        chunk_bytes: int = 96 * 1024 * 1024,
    ) -> None:
        if not path or not os.path.isfile(path):
            raise MediaOffline(f"Média introuvable : {path or '(vide)'}")
        if rate <= 0:
            raise FrameReadError("Cadence du média inconnue.")
        self.path = path
        self.geometry = geometry
        self.rate = float(rate)
        self.cancelled = cancelled or (lambda: False)
        self.chunk_frames = max(4, int(chunk_bytes // max(1, geometry.frame_bytes)))
        self._process: subprocess.Popen | None = None
        self.stderr = ""

    def command(self, start_index: int, count: int) -> list[str]:
        g = self.geometry
        return [
            _ffmpeg(), "-nostdin", "-hide_banner", "-v", "error",
            "-ss", _seconds(start_index / self.rate), "-i", self.path,
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"fps={self.rate:.6f},scale={g.width}:{g.height}:flags=area,format=gray",
            "-frames:v", str(int(count)), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ]

    def _run(self, start_index: int, count: int) -> Iterator[tuple[int, object]]:
        from .tracking_match import require_numpy

        np = require_numpy()
        size = self.geometry.frame_bytes
        shape = (self.geometry.height, self.geometry.width)
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            self.command(start_index, count), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, creationflags=creation,
        )
        self._process = process
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
        drain.start()
        produced = 0
        try:
            while produced < count:
                if self.cancelled():
                    break
                buffer = _read_exactly(process.stdout, size)
                if buffer is None:
                    break
                yield start_index + produced, np.frombuffer(buffer, dtype=np.uint8).reshape(shape)
                produced += 1
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.stdout.close()
            except OSError:
                pass
            process.wait()
            drain.join(timeout=2.0)
            self.stderr = b"".join(e for e in errors if e).decode("utf-8", "replace")[-2000:]
            self._process = None
        if produced == 0 and not self.cancelled() and self.stderr.strip():
            raise FrameReadError(self.stderr.strip().splitlines()[-1])

    def frames(self, start_index: int, end_index: int) -> Iterator[tuple[int, object]]:
        """Images ``start → end`` incluses, dans cet ordre (à rebours si ``end < start``)."""
        if end_index >= start_index:
            yield from self._run(start_index, end_index - start_index + 1)
            return
        high = start_index
        while high >= end_index and not self.cancelled():
            low = max(end_index, high - self.chunk_frames + 1)
            chunk = list(self._run(low, high - low + 1))
            if not chunk:
                return
            for item in reversed(chunk):
                if self.cancelled():
                    return
                yield item
            if chunk[-1][0] < high:
                return  # fin de média ou images illisibles : on s'arrête là
            high = low - 1

    def read(self, index: int):
        """Une seule image (``None`` si illisible)."""
        for _index, frame in self._run(index, 1):
            return frame
        return None

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()


def _read_exactly(stream, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining > 0:
        data = stream.read(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


__all__ = [
    "AnalysisGeometry", "FrameReadError", "FrameReader", "MediaOffline", "analysis_geometry",
]
