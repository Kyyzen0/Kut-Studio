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
import math
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


def _probe_number(text: str) -> float | None:
    """Un flottant fini, ou ``None`` pour vide, ``N/A`` ou tout texte non numérique.

    ffprobe écrit ``N/A`` quand une durée de flux est absente (Matroska, WebM). La chaîne est truthy : ``float(text or
    repli)`` lève ``ValueError`` au lieu d'utiliser la durée du conteneur, qui est sur la ligne suivante.
    """
    token = text.strip()
    if not token or token.upper() == "N/A":
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def _parse_probe_csv(stdout: str) -> tuple[int, int, float, float]:
    """``(largeur, hauteur, cadence, durée)`` depuis ``ffprobe -of csv=p=0``.

    La durée de flux est préférée ; ``N/A`` cède la place à la durée du conteneur (ligne suivante, un seul nombre).
    """
    stream: tuple[int, int, str, float | None] | None = None
    container_duration: float | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        width = _probe_number(parts[0]) if parts else None
        height = _probe_number(parts[1]) if len(parts) > 1 else None
        if width is not None and height is not None and width >= 1 and height >= 1 and len(parts) >= 3:
            duration = None
            for extra in parts[3:]:
                duration = _probe_number(extra)
                if duration is not None:
                    break
            stream = (int(width), int(height), parts[2], duration)
            continue
        if len(parts) == 1:
            number = _probe_number(parts[0])
            if number is not None:
                container_duration = number
    if stream is None:
        raise OSError("ffprobe n'a pas renvoyé de flux vidéo")
    width_px, height_px, rate, duration = stream
    if "/" in rate:
        num_text, den_text = rate.split("/", 1)
        num = _probe_number(num_text)
        den = _probe_number(den_text)
        fps = (num / max(den, 1e-9)) if num is not None and den is not None else 30.0
    else:
        parsed = _probe_number(rate)
        fps = parsed if parsed is not None and parsed > 0 else 30.0
    if duration is None:
        duration = container_duration if container_duration is not None else 0.0
    return width_px, height_px, fps, duration


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
    width, height, fps, duration = _parse_probe_csv(completed.stdout or "")
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
        container = self._container
        if container is None or _av is None:
            return self._decode_cli(seconds)
        stream = container.streams.video[0]
        target = int(seconds / stream.time_base) if stream.time_base else 0
        container.seek(target, stream=stream)
        # ``seek`` s'arrête sur une clé : avancer jusqu'au PTS demandé. Ne convertir
        # en RVBA que la frame retenue (GOP long / haute résol. : sinon coût prohibitif).
        best_frame = None
        best_stamp: float | None = None
        for frame in container.decode(stream):
            stamp = float(frame.time) if frame.time is not None else None
            if stamp is None:
                return self._pyav_frame_to_video(frame, seconds)
            if stamp + 1e-6 >= seconds:
                if best_frame is not None and best_stamp is not None \
                        and abs(best_stamp - seconds) <= abs(stamp - seconds):
                    return self._pyav_frame_to_video(best_frame, best_stamp)
                return self._pyav_frame_to_video(frame, stamp)
            best_frame = frame
            best_stamp = stamp
        if best_frame is not None and best_stamp is not None:
            return self._pyav_frame_to_video(best_frame, best_stamp)
        return self._decode_cli(seconds)

    @staticmethod
    def _pyav_frame_to_video(frame, seconds: float) -> VideoFrame:
        image = frame.to_ndarray(format="rgba")
        h, w, _ = image.shape
        stamp = float(frame.time) if getattr(frame, "time", None) is not None else float(seconds)
        return VideoFrame(w, h, image.tobytes(), stamp)

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
