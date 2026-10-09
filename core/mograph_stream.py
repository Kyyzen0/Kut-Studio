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
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .atomic_io import atomic_write_text
from .platform_paths import user_cache_dir
from .timecode import ffmpeg_rate

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


SHARED_WRITE_ATTEMPTS = 5
"""Essais pour publier une entrée du cache qu'un autre thread publie au même instant (refus de Windows)."""


def _publish_shared(target: Path, publish: Callable[[], object]) -> None:
    """Publie une entrée du cache **nommée d'après son contenu**, que d'autres threads peuvent publier en même temps.

    Windows refuse (``PermissionError``) de remplacer un fichier qu'un autre thread remplace ou lit au même instant ;
    macOS et Linux remplacent sans erreur. Le nom étant l'empreinte du contenu, le fichier d'un écrivain concurrent est
    identique au nôtre : dès qu'il existe, l'entrée est publiée. Sinon on réessaie brièvement.
    """
    for attempt in range(SHARED_WRITE_ATTEMPTS):
        try:
            publish()
            return
        except PermissionError:
            if target.is_file():
                return
            if attempt == SHARED_WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def _write_png(image, target: Path) -> None:
    if target.is_file() and target.stat().st_size > 0:
        _touch(target)
        return
    # Processus **et** thread : l'aperçu fidèle et les scopes rastérisent chacun dans leur thread, parfois la même image.
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.{threading.get_ident()}.tmp.png")
    if not image.save(str(temporary), "PNG", 80):
        raise OSError(f"Impossible d'écrire l'image de calque : {temporary}")
    try:
        _publish_shared(target, lambda: os.replace(temporary, target))
    finally:
        if temporary.exists():         # un écrivain concurrent l'a emporté : notre copie identique est en trop
            try:
                temporary.unlink()
            except OSError:
                pass


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


WRITE_WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))
"""Fils d'encodage des images PNG. ``QImage.save`` libère le verrou de l'interpréteur : l'encodage (≈ 80 % du coût
d'une image pleine trame, 54 ms en 1080×1920) se fait en parallèle pendant que ce fil dessine la suivante. Le dessin
reste dans le fil appelant (le moteur de rendu n'est pas partagé), les octets écrits sont ceux d'une écriture en série."""

WRITE_BACKLOG = 2
"""Images dessinées en attente d'encodage, par fil : borne la mémoire (8 Mo par image pleine trame en 1080p)."""


def _write_frames(targets: list[tuple[Path, float]], render: Callable[[float], object]) -> None:
    """Dessine (dans ce fil) puis encode (en parallèle) les images ``targets`` : ``(fichier, instant)``.

    La première erreur d'écriture est relevée une fois les écritures déjà lancées terminées ; aucune n'est oubliée."""
    if len(targets) <= 1 or WRITE_WORKERS <= 1:
        for target, t in targets:
            _write_png(render(t), target)
        return
    pending: deque[Future] = deque()
    with ThreadPoolExecutor(max_workers=WRITE_WORKERS, thread_name_prefix="kut-png") as pool:
        try:
            for target, t in targets:
                while len(pending) >= WRITE_WORKERS * WRITE_BACKLOG:
                    pending.popleft().result()
                pending.append(pool.submit(_write_png, render(t), target))
            while pending:
                pending.popleft().result()
        finally:
            for future in pending:
                future.cancel()


@dataclass(frozen=True)
class SpanStream:
    """Flux d'images limité aux images où un élément est visible (:func:`write_span_stream`).

    ``first_frame`` : rang, sur la grille du rendu, de sa première image (instant ``first_frame / fps``) ;
    ``frames`` : nombre d'images. Avant et après, FFmpeg n'a rien à composer : le dessous passe tel quel."""

    playlist: str
    first_frame: int
    frames: int


def _frame_runs(
    *, width: int, height: int, fps: float, low: int, high: int, time_offset: float, salt: str,
    frame_key: Callable[[float], object | None], blank: str,
) -> tuple[list[list], dict[str, float]]:
    """Plages ``[fichier, nombre d'images]`` des images ``low`` à ``high`` (exclue), et l'instant de la première
    occurrence de chaque image à dessiner."""
    from .mograph_raster import RASTER_VERSION

    runs: list[list] = []
    first_frames: dict[str, float] = {}
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
    return runs, first_frames


def _publish_playlist(directory: Path, runs: list[list], fps: float, first_frames: dict[str, float],
                      render: Callable[[float], object]) -> str:
    """Écrit les images manquantes puis la liste ``.ffconcat`` ; retourne son chemin."""
    missing: list[tuple[Path, float]] = []
    for name, t in first_frames.items():
        target = directory / name
        if target.is_file() and target.stat().st_size > 0:
            _touch(target)
        else:
            missing.append((target, t))
    _write_frames(missing, render)
    # ``option framerate`` : une image PNG s'ouvre à 25 i/s par défaut, et la liste prend la base de temps de sa
    # première image. À 30 i/s, deux images tombaient sur le même tic de 40 ms : l'animation sautait une image sur six
    # (et en doublait une autre). À la cadence du rendu, chaque image a son propre tic.
    rate = ffmpeg_rate(fps)
    lines = ["ffconcat version 1.0"]
    for name, frames in runs:
        lines.extend((f"file '{name}'", f"option framerate {rate}", f"duration {frames / fps:.9f}"))
    # La dernière entrée est répétée : sa durée est ainsi toujours appliquée.
    lines.extend((f"file '{runs[-1][0]}'", f"option framerate {rate}"))
    text = "\n".join(lines) + "\n"
    playlist = directory / f"s-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:24]}.ffconcat"
    if not playlist.is_file():
        # Temporaire unique (``mkstemp``) : les scopes et l'aperçu fidèle peuvent écrire la même liste au même moment,
        # depuis deux threads ; un nom par processus faisait échouer l'un des deux ``os.replace``.
        _publish_shared(playlist, lambda: atomic_write_text(playlist, text, durable=False))
    return str(playlist)


def frame_grid(fps: float, duration: float, origin: float = 0.0) -> tuple[int, int]:
    """``(première image, fin exclue)`` d'une composition de ``duration`` s qui commence à ``origin`` (rangs d'images).

    Même compte que le fond de la composition (``color=…:d=duration``, puis ``trim=start=origin``)."""
    fps = float(fps) if fps and fps > 0 else 30.0
    count = max(1, int(math.ceil(duration * fps - 1e-6)))
    first = max(0, int(math.ceil(origin * fps - 1e-6))) if origin > 0 else 0
    return min(first, count), count


def _frame_range(fps: float, duration: float, start: float, end: float, time_offset: float) -> tuple[int, int, int]:
    """``(nombre d'images du flux, première image utile, fin exclue)`` sur la grille du rendu."""
    count = max(1, int(math.ceil(duration * fps - 1e-6)))
    low = max(0, int(math.floor((start - time_offset) * fps - 1e-6)))
    high = min(count, int(math.ceil((end - time_offset) * fps + 1e-6)))
    return count, low, high


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
    directory = cache_directory()
    fps = float(fps) if fps and fps > 0 else 30.0
    count, low, high = _frame_range(fps, duration, start, end, time_offset)
    blank = _blank_png(width, height, directory)
    runs, first_frames = _frame_runs(width=width, height=height, fps=fps, low=low, high=high,
                                     time_offset=time_offset, salt=salt, frame_key=frame_key, blank=blank)
    if low > 0:
        if runs and runs[0][0] == blank:
            runs[0][1] += low
        else:
            runs.insert(0, [blank, low])
    if high < count:
        if runs and runs[-1][0] == blank:
            runs[-1][1] += count - high
        else:
            runs.append([blank, count - high])
    return _publish_playlist(directory, runs, fps, first_frames, render)


def write_span_stream(
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    start: float,
    end: float,
    frame_key: Callable[[float], object | None],
    render: Callable[[float], object],
    salt: str = "",
    first_frame: int = 0,
) -> SpanStream | None:
    """Comme :func:`write_stream`, mais le flux ne couvre que les images visibles (``None`` : aucune).

    ``first_frame`` : première image de la composition (segment d'aperçu) ; le flux ne commence jamais avant.

    Un élément affiché 0,8 s sur 31 s n'est plus composé, image transparente après image transparente, pendant tout le
    rendu : le graphe le pose à son instant (:func:`span_input_filter`) et laisse passer le dessous ailleurs. Les images
    composées sont identiques à celles du flux complet (une image transparente ne change rien au dessous)."""
    directory = cache_directory()
    fps = float(fps) if fps and fps > 0 else 30.0
    _count, low, high = _frame_range(fps, duration, start, end, 0.0)
    low = max(low, first_frame)
    blank = _blank_png(width, height, directory)
    runs, first_frames = _frame_runs(width=width, height=height, fps=fps, low=low, high=high,
                                     time_offset=0.0, salt=salt, frame_key=frame_key, blank=blank)
    if runs and runs[0][0] == blank:
        low += runs.pop(0)[1]
    if runs and runs[-1][0] == blank:
        runs.pop()
    if not runs:
        return None
    playlist = _publish_playlist(directory, runs, fps, first_frames, render)
    return SpanStream(playlist, low, sum(frames for _name, frames in runs))


def stream_input_filter(fps, duration: float, origin: float = 0.0) -> str:
    """Filtres qui calent un flux ``.ffconcat`` sur la cadence et la durée du rendu.

    ``origin`` > 0 (segment d'aperçu) : les images d'avant ``origin`` sont écartées dès la sortie de ``fps``, avant
    toute conversion, avec les mêmes horodatages pour celles qui restent. ``0`` : la chaîne historique, inchangée.
    """
    from .export_engine import _format_seconds

    fps_text = ffmpeg_rate(fps)
    length = _format_seconds(max(duration, 1.0 / float(fps or 30)))
    if origin > 0:
        return (
            f"fps={fps_text},setpts=PTS-STARTPTS,trim=start={_format_seconds(origin)},format=rgba,"
            f"tpad=stop=-1:stop_mode=clone,trim=end={length}"
        )
    return f"fps={fps_text},format=rgba,tpad=stop=-1:stop_mode=clone,trim=duration={length},setpts=PTS-STARTPTS"


def span_input_filter(fps, span: SpanStream) -> str:
    """Filtres qui posent un flux limité (:class:`SpanStream`) à son instant, sur la cadence du rendu.

    Après ``fps``, une image vaut un tic de la base de temps : ``+first_frame`` place la première image exactement sur
    son rang, sans arrondi de secondes. Le flux s'arrête à sa dernière image ; ``overlay`` (``eof_action=pass``) et
    ``blend`` (``shortest=1``) laissent alors passer le dessous."""
    return (
        f"fps={ffmpeg_rate(fps)},format=rgba,tpad=stop=-1:stop_mode=clone,trim=end_frame={span.frames},"
        f"setpts=PTS-STARTPTS+{span.first_frame}"
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
    "CACHE_KIND", "MographFrameCache", "SpanStream", "cache_directory", "ensure_qt_gui", "frame_grid", "span_input_filter",
    "still_playlist", "stream_input_filter", "write_span_stream", "write_stream",
]
