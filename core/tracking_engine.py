"""Analyse de tracking : requête, exécution annulable, cache de résultats.

:func:`run_tracking` est **synchrone** et pur vis-à-vis du projet : il reçoit
une :class:`TrackingRequest` figée (trackers immuables, plage, média) et
retourne un :class:`TrackingResult`. L'interface l'exécute hors du thread
principal via :class:`TrackingJob`, sur la file d'analyse du runtime
(:meth:`core.studio_runtime.StudioRuntime.schedule_analysis`), puis fusionne le
résultat dans le projet en **une** opération d'historique
(:func:`core.tracking_ops.apply_tracking_result`).

Règles
------

- l'analyse part d'une image où le tracker a une position valide et va
  vers ``end_index`` (avant ou arrière), image par image ;
- elle s'arrête pour un tracker sur une correction manuelle (jamais
  écrasée : on reprend depuis elle), sur une perte (confiance trop faible,
  point hors de l'image) si ``stop_on_loss``, en fin de plage ou de média ;
- l'annulation est vérifiée entre deux images et arrête FFmpeg ; les
  images déjà suivies sont conservées (reprise possible) ;
- plusieurs trackers d'un même clip partagent le décodage.

Cache
-----

Le résultat d'un tracker est mis en cache disque sous une clé qui couvre
média (chemin + signature), variante lue (original / proxy), cadence,
résolution d'analyse, image de départ et position de départ, image de fin,
corrections manuelles de la plage, réglages et version de l'algorithme.
Relancer la même analyse (après un undo, un reset, sur un autre clip du
même média) est instantané. Le projet garde les données : il reste
complet sans le cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .tracking_frames import (
    AnalysisGeometry,
    FrameReader,
    FrameReadError,
    MediaOffline,
    analysis_geometry,
)
from .tracking_model import (
    TRACKING_ALGORITHM_VERSION,
    Precision,
    Sample,
    SampleStatus,
    TrackData,
    Tracker,
    quantize,
)

CACHE_KIND = "tracking"


# ---------------------------------------------------------------------------
# Requête et résultat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackingRequest:
    """Ce qu'il faut analyser (figé au lancement)."""

    clip_id: str
    media_path: str
    media_size: tuple[int, int]
    rate: float
    trackers: tuple[Tracker, ...]
    start_index: int
    end_index: int
    decode_path: str = ""
    """Fichier lu (proxy) ; vide = le média original."""
    source_token: str = ""
    """Signature du fichier lu (date + taille) : entre dans la clé de cache."""

    @property
    def direction(self) -> int:
        return 1 if self.end_index >= self.start_index else -1

    @property
    def path_to_read(self) -> str:
        return self.decode_path or self.media_path

    @property
    def variant(self) -> str:
        return "proxy" if self.decode_path and self.decode_path != self.media_path else "original"

    @property
    def frame_count(self) -> int:
        return abs(self.end_index - self.start_index)

    @property
    def precision(self) -> str:
        """La plus fine des précisions demandées (un seul décodage partagé)."""
        order = {Precision.FAST: 0, Precision.AUTO: 1, Precision.FULL: 2}
        values = [t.settings.precision for t in self.trackers] or [Precision.AUTO]
        return max(values, key=lambda value: order.get(value, 1))


class StopReason:
    RANGE_END = "range_end"
    LOST = "lost"
    OUT_OF_FRAME = "out_of_frame"
    FLAT = "flat"
    MANUAL = "manual"
    MEDIA_END = "media_end"
    CANCELLED = "cancelled"
    NO_START = "no_start"
    FAILED = "failed"


@dataclass
class TrackerOutcome:
    """Résultat d'un tracker : nouvelles images (pixels du média original) et arrêt."""

    tracker_id: str
    samples: dict[int, Sample] = field(default_factory=dict)
    reason: str = StopReason.RANGE_END
    stop_index: int | None = None
    from_cache: bool = False

    @property
    def last_index(self) -> int | None:
        return max(self.samples) if self.samples else None


@dataclass
class TrackingResult:
    request: TrackingRequest
    outcomes: dict[str, TrackerOutcome]
    state: str = "finished"
    """``finished``, ``cancelled`` ou ``failed``."""
    message: str = ""
    frames_analyzed: int = 0
    elapsed: float = 0.0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_directory() -> Path:
    configured = os.environ.get("KUT_STUDIO_CACHE_DIR")
    root = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "kut-studio-cache"
    directory = root / CACHE_KIND
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def cache_key(request: TrackingRequest, tracker: Tracker, geometry: AnalysisGeometry) -> str:
    start = tracker.data.sample(request.start_index)
    low, high = sorted((request.start_index, request.end_index))
    manual = [
        index for index in range(low, high + 1)
        if tracker.data.status_at(index) is SampleStatus.MANUAL
    ] if tracker.data.count else []
    payload = {
        "algorithm": TRACKING_ALGORITHM_VERSION,
        "path": os.path.abspath(request.path_to_read),
        "token": request.source_token,
        "variant": request.variant,
        "rate": round(request.rate, 6),
        "media": list(request.media_size),
        "analysis": [geometry.width, geometry.height],
        "start": request.start_index,
        "end": request.end_index,
        "position": [round(start.x, 3), round(start.y, 3)],
        "manual": manual,
        "settings": tracker.settings.to_dict(),
        "kind": tracker.kind,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class TrackingCache:
    """Résultats d'analyse sur disque (recalculables), vus par :mod:`core.cache_manager`."""

    def __init__(self, directory: str | os.PathLike | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory
        return cache_directory()

    def _path(self, key: str) -> Path:
        return self.directory / f"t-{key}.json"

    def load(self, key: str, rate: float) -> TrackerOutcome | None:
        path = self._path(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            os.utime(path)  # dernier usage (éviction LRU)
        except (OSError, ValueError):
            return None
        data = TrackData.from_dict(raw.get("data"))
        samples = {
            index: sample for index, sample in data.samples() if sample.status is not SampleStatus.EMPTY
        }
        stop = raw.get("stop_index")
        return TrackerOutcome(
            tracker_id="", samples=samples, reason=str(raw.get("reason", StopReason.RANGE_END)),
            stop_index=int(stop) if stop is not None else None, from_cache=True,
        )

    def store(self, key: str, outcome: TrackerOutcome, rate: float) -> None:
        data = TrackData.from_samples(rate, outcome.samples)
        payload = {"data": data.to_dict(), "reason": outcome.reason, "stop_index": outcome.stop_index}
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    # -- vue « gestionnaire de cache » -------------------------------------------------------

    def _files(self) -> list[tuple[Path, int, float]]:
        result = []
        try:
            entries = list(os.scandir(self.directory))
        except OSError:
            return result
        for entry in entries:
            if not entry.name.startswith("t-") or not entry.is_file():
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


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[float, int, dict[str, dict[int, Sample]]], None]


def run_tracking(
    request: TrackingRequest,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
    cache: TrackingCache | None = None,
) -> TrackingResult:
    """Analyse ``request`` ; ne lève pas (l'échec est décrit dans le résultat)."""
    started = time.perf_counter()
    is_cancelled = cancelled or (lambda: False)
    outcomes: dict[str, TrackerOutcome] = {}
    geometry = analysis_geometry(*request.media_size, request.precision)
    active: list[Tracker] = []
    keys: dict[str, str] = {}
    for tracker in request.trackers:
        start = tracker.data.sample(request.start_index)
        if not start.valid:
            outcomes[tracker.id] = TrackerOutcome(tracker.id, reason=StopReason.NO_START)
            continue
        key = cache_key(request, tracker, geometry)
        keys[tracker.id] = key
        cached = cache.load(key, request.rate) if cache is not None else None
        if cached is not None:
            cached.tracker_id = tracker.id
            outcomes[tracker.id] = cached
            continue
        active.append(tracker)
    result = TrackingResult(request, outcomes)
    if not active or request.frame_count == 0:
        for tracker in active:
            outcomes[tracker.id] = TrackerOutcome(tracker.id, reason=StopReason.RANGE_END)
        result.elapsed = time.perf_counter() - started
        return result
    try:
        analyzed = _analyze(request, active, geometry, outcomes, is_cancelled, progress)
        result.frames_analyzed = analyzed
    except MediaOffline as exc:
        result.state, result.message = "failed", f"offline:{exc}"
    except (FrameReadError, OSError) as exc:
        result.state, result.message = "failed", f"read:{exc}"
    except Exception as exc:  # garde-fou : l'analyse ne fait jamais tomber l'interface
        result.state, result.message = "failed", f"error:{exc}"
    if result.state != "failed" and is_cancelled():
        result.state = "cancelled"
        for outcome in outcomes.values():
            if outcome.reason == StopReason.RANGE_END and not outcome.from_cache:
                outcome.reason = StopReason.CANCELLED
    if result.state == "finished" and cache is not None:
        for tracker in active:
            outcome = outcomes.get(tracker.id)
            if outcome is not None and outcome.reason not in (StopReason.CANCELLED, StopReason.FAILED):
                cache.store(keys[tracker.id], outcome, request.rate)
    if result.state == "failed":
        for tracker in active:
            outcomes.setdefault(tracker.id, TrackerOutcome(tracker.id, reason=StopReason.FAILED))
    result.elapsed = time.perf_counter() - started
    return result


def _analyze(request, active, geometry, outcomes, is_cancelled, progress) -> int:
    from .tracking_match import PointMatcher

    reader = FrameReader(request.path_to_read, geometry, request.rate, cancelled=is_cancelled)
    frames = reader.frames(request.start_index, request.end_index)
    first = next(frames, None)
    if first is None:
        raise FrameReadError(reader.stderr.strip() or "Image de départ illisible.")
    _index, frame = first
    matchers = {}
    for tracker in active:
        start = tracker.data.sample(request.start_index)
        settings = tracker.settings
        k = geometry.scale
        x, y = geometry.to_analysis(start.x, start.y)
        matcher = PointMatcher(
            frame, x, y,
            pattern=(settings.pattern_width * k, settings.pattern_height * k),
            search=(settings.search_width * k, settings.search_height * k),
            min_confidence=settings.min_confidence, good_confidence=settings.good_confidence,
            adapt=settings.adapt,
        )
        outcome = TrackerOutcome(tracker.id)
        outcomes[tracker.id] = outcome
        if not matcher.ready:
            outcome.reason, outcome.stop_index = StopReason.OUT_OF_FRAME, request.start_index
            continue
        matchers[tracker.id] = (tracker, matcher)
    total = max(1, request.frame_count)
    done = 0
    last_report = 0.0
    expected = request.start_index
    for index, frame in frames:
        expected += request.direction
        if index != expected:
            break
        if not matchers or is_cancelled():
            break
        for tracker_id, (tracker, matcher) in list(matchers.items()):
            outcome = outcomes[tracker_id]
            if tracker.data.status_at(index) is SampleStatus.MANUAL:
                outcome.reason, outcome.stop_index = StopReason.MANUAL, index
                del matchers[tracker_id]
                continue
            step = matcher.step(frame)
            x, y = geometry.to_media(step.x, step.y)
            # Valeurs telles qu'elles seront stockées : un résultat relu du cache
            # est identique à un résultat frais.
            outcome.samples[index] = Sample(
                quantize(x), quantize(y), round(step.confidence * 255) / 255.0, step.status,
            )
            if step.status is SampleStatus.LOST and tracker.settings.stop_on_loss:
                outcome.reason = {
                    "out_of_frame": StopReason.OUT_OF_FRAME, "flat": StopReason.FLAT,
                }.get(step.reason, StopReason.LOST)
                outcome.stop_index = index
                del matchers[tracker_id]
        done += 1
        now = time.perf_counter()
        if progress is not None and (now - last_report > 0.05 or done == total):
            last_report = now
            progress(done / total, index, {tid: dict(outcomes[tid].samples) for tid in outcomes})
    if matchers and not is_cancelled() and expected != request.end_index:
        for tracker_id in matchers:
            outcome = outcomes[tracker_id]
            outcome.reason, outcome.stop_index = StopReason.MEDIA_END, expected
    return done


# ---------------------------------------------------------------------------
# Tâche asynchrone
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSnapshot:
    state: str
    """``queued``, ``running``, ``finished``, ``cancelled`` ou ``failed``."""
    progress: float
    current_index: int
    partial: dict[str, dict[int, Sample]]
    result: TrackingResult | None
    message: str = ""


class TrackingJob:
    """Une analyse exécutée par un worker ; l'interface lit :meth:`snapshot`.

    Sûr entre threads : le worker écrit sous verrou, l'interface lit une
    copie. :meth:`cancel` est immédiat (FFmpeg est arrêté à l'image suivante).
    """

    def __init__(self, request: TrackingRequest, *, cache: TrackingCache | None = None) -> None:
        self.request = request
        self.cache = cache
        self._lock = threading.Lock()
        self._state = "queued"
        self._progress = 0.0
        self._index = request.start_index
        self._partial: dict[str, dict[int, Sample]] = {}
        self._result: TrackingResult | None = None
        self._cancelled = threading.Event()
        self._done = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            if self._state == "queued":
                self._state = "cancelled"
                self._result = TrackingResult(self.request, {}, state="cancelled")
                self._done.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def run(self, token=None) -> TrackingResult:
        """Corps de la tâche (worker). ``token`` : jeton de la file (annulation de session)."""
        with self._lock:
            if self._state != "queued":
                return self._result  # type: ignore[return-value]
            self._state = "running"

        def is_cancelled() -> bool:
            return self._cancelled.is_set() or bool(getattr(token, "cancelled", False))

        def on_progress(fraction: float, index: int, partial) -> None:
            with self._lock:
                self._progress = fraction
                self._index = index
                self._partial = partial

        result = run_tracking(self.request, cancelled=is_cancelled, progress=on_progress, cache=self.cache)
        with self._lock:
            self._result = result
            self._state = result.state
            self._progress = 1.0 if result.state == "finished" else self._progress
            self._partial = {tid: dict(o.samples) for tid, o in result.outcomes.items()}
        self._done.set()
        return result

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            return JobSnapshot(
                self._state, self._progress, self._index,
                {tid: dict(samples) for tid, samples in self._partial.items()},
                self._result, self._result.message if self._result is not None else "",
            )


__all__ = [
    "CACHE_KIND", "JobSnapshot", "StopReason", "TrackerOutcome", "TrackingCache", "TrackingJob",
    "TrackingRequest", "TrackingResult", "cache_directory", "cache_key", "run_tracking",
]
