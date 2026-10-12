"""Entrées / sorties média in-process (ADR-0001, checklist item 2).

Ouvre un fichier, saute à un instant et décode une image. Préfère **PyAV**
quand il est installé (build FFmpeg LGPL embarqué) ; sinon replie sur la ligne
de commande FFmpeg via :mod:`core.process_supervisor` (même contrat, plus lent
au saut).

Le moniteur et les outils de perf passent par ce module pour les décodages
multi-sources ; l'export de référence garde son graphe FFmpeg.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .process_supervisor import supervised_run
from .tool_paths import find_media_tool

LOGGER = logging.getLogger(__name__)

try:
    import av as _av
except ImportError:  # PyAV optionnel (roue GPL publique ; build LGPL du paquet)
    _av = None


@dataclass(frozen=True)
class VideoFrame:
    """Image décodée : RVBA serré, taille, instant source."""

    width: int
    height: int
    rgba: bytes
    time_seconds: float


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float


def pyav_available() -> bool:
    return _av is not None


def probe(path: str | Path) -> MediaInfo:
    """Métadonnées minimales (durée, taille, cadence) via ffprobe."""
    ffprobe = find_media_tool("ffprobe")
    if not ffprobe:
        raise OSError("ffprobe introuvable")
    completed = supervised_run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr or f"ffprobe a échoué sur {path}")
    line = (completed.stdout or "").strip().splitlines()[0]
    parts = line.split(",")
    width = int(float(parts[0]))
    height = int(float(parts[1]))
    rate = parts[2]
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / max(float(den), 1e-9)
    else:
        fps = float(rate or 30)
    duration = float(parts[3] or parts[4] or 0.0)
    return MediaInfo(str(path), duration, width, height, fps)


class MediaHandle:
    """Fichier média ouvert pour décodage image par image."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.info = probe(self.path)
        self._container = None
        if _av is not None:
            try:
                self._container = _av.open(self.path)
            except Exception:  # noqa: BLE001 - repli CLI
                LOGGER.debug("PyAV n'a pas ouvert %s : repli FFmpeg CLI", self.path, exc_info=True)
                self._container = None

    @property
    def backend(self) -> str:
        return "pyav" if self._container is not None else "ffmpeg-cli"

    def close(self) -> None:
        if self._container is not None:
            self._container.close()
            self._container = None

    def __enter__(self) -> MediaHandle:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def decode_at(self, seconds: float) -> VideoFrame:
        """Image la plus proche de ``seconds`` (début du média = 0)."""
        t = max(0.0, float(seconds))
        if self._container is not None:
            return self._decode_pyav(t)
        return self._decode_cli(t)

    def _decode_pyav(self, seconds: float) -> VideoFrame:
        assert self._container is not None and _av is not None
        stream = self._container.streams.video[0]
        target = int(seconds / stream.time_base) if stream.time_base else 0
        self._container.seek(target, stream=stream)
        for frame in self._container.decode(stream):
            image = frame.to_ndarray(format="rgba")
            h, w, _ = image.shape
            return VideoFrame(w, h, image.tobytes(), float(frame.time or seconds))
        return self._decode_cli(seconds)

    def _decode_cli(self, seconds: float) -> VideoFrame:
        ffmpeg = find_media_tool("ffmpeg")
        if not ffmpeg:
            raise OSError("ffmpeg introuvable")
        width, height = self.info.width, self.info.height
        with tempfile.NamedTemporaryFile(suffix=".rgba", delete=False) as handle:
            target = Path(handle.name)
        try:
            target.unlink(missing_ok=True)
            completed = supervised_run(
                [
                    ffmpeg, "-y", "-v", "error", "-ss", f"{seconds:.6f}", "-i", self.path,
                    "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", str(target),
                ],
                capture_output=True, text=True, check=False,
            )
            if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                raise OSError(completed.stderr or f"décodage CLI en échec pour {self.path}")
            data = target.read_bytes()
            expected = width * height * 4
            if len(data) < expected:
                # ffprobe peut mentir ; recalcule depuis la taille.
                if len(data) % 4 != 0:
                    raise OSError(f"tampon RVBA incomplet ({len(data)} octets)")
                pixels = len(data) // 4
                height = max(1, pixels // max(1, width))
                data = data[: width * height * 4]
            return VideoFrame(width, height, data[: width * height * 4], seconds)
        finally:
            try:
                target.unlink()
            except OSError:
                pass


def open_media(path: str | Path) -> MediaHandle:
    return MediaHandle(path)


def decode_preview_frame(path: str | Path, seconds: float) -> VideoFrame:
    """Raccourci : une image, le fichier est refermé ensuite."""
    with open_media(path) as handle:
        return handle.decode_at(seconds)


def prefer_proxy(original: str, proxy: str | None) -> str:
    """Choisit le proxy s'il existe, sinon l'original (scrubbing GOP long)."""
    if proxy and Path(proxy).is_file():
        return proxy
    return original


__all__ = [
    "MediaHandle", "MediaInfo", "VideoFrame", "decode_preview_frame", "open_media",
    "prefer_proxy", "probe", "pyav_available",
]
