"""Flux d'images des calques motion graphics pour FFmpeg.

Un élément de rendu (:mod:`core.mograph_program`) devient un fichier
``.ffconcat`` : une liste d'images PNG RGBA avec leur durée. FFmpeg le lit
comme une vidéo (démultiplexeur ``concat``), puis ``fps`` la cale sur la
cadence du rendu.

Cache
-----

Chaque image est nommée par l'**empreinte de son état** (valeurs évaluées
des calques, matrices du monde, masques, échantillons de flou…) :

- une suite d'images identiques (calque immobile) n'est rendue et écrite
  qu'**une** fois, avec une durée longue ;
- modifier un calque ne change que les images de **son** élément : les
  autres éléments, et les segments d'aperçu qui ne le montrent pas,
  restent en cache ;
- un aperçu puis un export réutilisent les mêmes images à résolution égale ;
- la version du dessin (:data:`core.mograph_raster.RASTER_VERSION`) entre
  dans chaque nom : un dessin modifié ne ressert jamais une image ancienne.

Le dossier est hors du projet (``KUT_STUDIO_CACHE_DIR`` ou le dossier
temporaire) et peut être vidé à tout moment ; il est déclaré au
gestionnaire de cache (:mod:`core.cache_manager`) sous le nom ``mograph``.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Callable
from pathlib import Path

from .platform_paths import user_cache_dir

CACHE_KIND = "mograph"

_BLANK = "blank"


def cache_directory() -> Path:
    root = user_cache_dir()
    directory = root / CACHE_KIND
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_qt_gui() -> None:
    """Le texte exige une ``QGuiApplication`` (polices) : en créer une hors interface."""
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        from PySide6.QtGui import QGuiApplication

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        ensure_qt_gui._app = QGuiApplication([])  # type: ignore[attr-defined]


def _digest(value: object) -> str:
    return hashlib.sha1(repr(value).encode("utf-8")).hexdigest()[:24]


def _touch(path: Path) -> None:
    """Marque une image comme récemment utilisée (éviction LRU)."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def _write_png(image, target: Path) -> None:
    if target.is_file() and target.stat().st_size > 0:
        _touch(target)
        return
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp.png")
    if not image.save(str(temporary), "PNG", 80):
        raise OSError(f"Impossible d'écrire l'image de calque : {temporary}")
    os.replace(temporary, target)


def _blank_png(width: int, height: int, directory: Path) -> str:
    name = f"{_BLANK}-{width}x{height}.png"
    target = directory / name
    if not target.is_file():
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage

        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        _write_png(image, target)
    return name


def write_stream(
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    start: float,
    end: float,
    frame_key: Callable[[float], object | None],
    render: Callable[[float], object],
    time_offset: float = 0.0,
    salt: str = "",
) -> str:
    """Écrit (ou réutilise) le flux et retourne le chemin du ``.ffconcat``.

    Args:
        width / height / fps / duration: géométrie du flux (``duration`` en s).
        start / end: plage utile ; ailleurs le flux est transparent.
        frame_key: état de l'image au temps ``t`` (``None`` = transparente).
        render: ``t → QImage`` de l'image.
        time_offset: temps de la première image dans le repère de
            ``frame_key`` / ``render`` (0 = début de la timeline).
        salt: distingue deux flux de même état mais de nature différente.
    """
    from .mograph_raster import RASTER_VERSION

    directory = cache_directory()
    fps = float(fps) if fps and fps > 0 else 30.0
    count = max(1, int(math.ceil(duration * fps - 1e-6)))
    blank = _blank_png(width, height, directory)
    runs: list[list] = []  # [nom de fichier, nombre d'images]
    first_frames: dict[str, float] = {}
    low = max(0, int(math.floor((start - time_offset) * fps - 1e-6)))
    high = min(count, int(math.ceil((end - time_offset) * fps + 1e-6)))
    if low > 0:
        runs.append([blank, low])
    for index in range(low, high):
        t = time_offset + index / fps
        state = frame_key(t)
        if state is None:
            name = blank
        else:
            name = f"f-{_digest((RASTER_VERSION, salt, width, height, state))}.png"
            first_frames.setdefault(name, t)
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])
    if high < count:
        if runs and runs[-1][0] == blank:
            runs[-1][1] += count - high
        else:
            runs.append([blank, count - high])
    for name, t in first_frames.items():
        target = directory / name
        if target.is_file() and target.stat().st_size > 0:
            _touch(target)
        else:
            _write_png(render(t), target)
    lines = ["ffconcat version 1.0"]
    for name, frames in runs:
        lines.append(f"file '{name}'")
        lines.append(f"duration {frames / fps:.9f}")
    lines.append(f"file '{runs[-1][0]}'")  # la dernière durée est ainsi toujours appliquée
    text = "\n".join(lines) + "\n"
    playlist = directory / f"s-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:24]}.ffconcat"
    if not playlist.is_file():
        temporary = playlist.with_name(f".{playlist.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, playlist)
    return str(playlist)


def stream_input_filter(fps, duration: float) -> str:
    """Filtres qui calent un flux ``.ffconcat`` sur la cadence et la durée du rendu."""
    from .export_engine import _format_seconds

    fps_text = fps if isinstance(fps, int) else _format_seconds(float(fps))
    return (
        f"fps={fps_text},format=rgba,tpad=stop=-1:stop_mode=clone,"
        f"trim=duration={_format_seconds(max(duration, 1.0 / float(fps or 30)))},setpts=PTS-STARTPTS"
    )


def still_playlist(playlist: str, t: float) -> str:
    """Liste ``.ffconcat`` d'**une** image : celle que montre ``playlist`` à ``t``.

    Sert à l'extraction d'une seule image (scopes) : FFmpeg, avec ``-ss`` en
    entrée, jetterait l'image d'une entrée commencée avant ``t`` même si sa
    durée couvre ``t``. Le flux fixe résultant se lit sans recherche.
    """
    source = Path(playlist)
    lines = source.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[str, float]] = []
    pending = None
    for line in lines:
        if line.startswith("file "):
            if pending is not None:
                entries.append((pending, 0.0))
            pending = line[len("file "):].strip().strip("'")
        elif line.startswith("duration ") and pending is not None:
            entries.append((pending, float(line.split()[1])))
            pending = None
    elapsed = 0.0
    chosen = entries[-1][0] if entries else _BLANK
    for name, duration in entries:
        if elapsed + duration > t + 1e-9:
            chosen = name
            break
        elapsed += duration
    text = f"ffconcat version 1.0\nfile '{chosen}'\nduration 3600\nfile '{chosen}'\n"
    target = source.with_name(f"still-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:24]}.ffconcat")
    if not target.is_file():
        target.write_text(text, encoding="utf-8")
    return str(target)


class MographFrameCache:
    """Vue « gestionnaire de cache » des images de calques (:mod:`core.cache_manager`).

    Même contrat que le cache des segments d'aperçu : statistiques, éviction
    des moins récemment utilisées, purge. Tout est recalculable.
    """

    def __init__(self, directory: str | os.PathLike | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else cache_directory()

    def _files(self) -> list[tuple[Path, int, float]]:
        result = []
        try:
            entries = list(os.scandir(self.directory))
        except OSError:
            return result
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            result.append((Path(entry.path), stat.st_size, stat.st_mtime))
        return result

    def stats(self) -> dict:
        files = self._files()
        return {"entries": len(files), "bytes": sum(size for _p, size, _t in files)}

    def evict_bytes(self, amount: int) -> int:
        """Supprime les images les moins récemment utilisées jusqu'à ``amount`` octets."""
        freed = 0
        for path, size, _mtime in sorted(self._files(), key=lambda item: item[2]):
            if freed >= amount:
                break
            try:
                path.unlink()
            except OSError:
                continue
            freed += size
        return freed

    def purge(self) -> int:
        return self.evict_bytes(1 << 62)


__all__ = [
    "CACHE_KIND", "MographFrameCache", "cache_directory", "ensure_qt_gui", "still_playlist",
    "stream_input_filter", "write_stream",
]
