"""Runtime de performance de Kut-Studio.

Un seul objet regroupe ce qui ne doit pas vivre dans les widgets :
profil machine, qualité d'aperçu, cache, file de tâches, session de
projet et compteurs de diagnostic. ``MainWindow`` le possède. Les
panneaux ne font que lire les budgets qu'on leur transmet.

Fermer ou remplacer un projet incrémente la session, annule les tâches
encore en file et vide l'espace de cache ``project``. Les sondes de
fichiers (espace ``global``) restent : elles dépendent du fichier, pas
du montage.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field

from .cache_store import MemoryCache
from .preview_quality import PreviewQualityController, quality_label, resolve_divisor
from .runtime_profile import (
    MachineResources,
    PerformanceProfile,
    detect_resources,
    project_weight,
    resolve_profile,
)
from .task_queue import PRIORITY_BACKGROUND, QueueWorker, TaskQueue


def peak_rss_bytes() -> int | None:
    """Pic d'occupation rapporté par l'OS, ou ``None`` s'il est illisible.

    ``ru_maxrss`` est un maximum depuis le lancement, pas la consommation
    instantanée. Sur macOS la valeur est en octets, ailleurs en kilo-octets.
    """
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, AttributeError, OSError, ValueError):
        return None
    if rss <= 0:
        return None
    if sys.platform == "darwin":
        return int(rss)
    return int(rss) * 1024


@dataclass
class RuntimeDiagnostics:
    """Compteurs légers. Ils ne déclenchent aucun travail supplémentaire."""

    preview_sync_ms: float = 0.0
    active_clips: int = 0
    playback_ticks: int = 0
    _tick_marks: list[float] = field(default_factory=list)

    def note_preview_sync(self, elapsed_ms: float, active_clips: int) -> None:
        self.preview_sync_ms = float(elapsed_ms)
        self.active_clips = int(active_clips)

    def note_playback_tick(self, now: float) -> None:
        self.playback_ticks += 1
        self._tick_marks.append(now)
        if len(self._tick_marks) > 40:
            self._tick_marks = self._tick_marks[-40:]

    def viewer_fps(self) -> float | None:
        """Cadence des ticks de lecture réellement émis, ou ``None``."""
        if len(self._tick_marks) < 2:
            return None
        span = self._tick_marks[-1] - self._tick_marks[0]
        if span <= 0:
            return None
        return (len(self._tick_marks) - 1) / span


class PreviewMailbox:
    """Résultats calculés hors de l'interface, appliqués plus tard au cache.

    Le cache n'est pas protégé pour des écritures concurrentes. Le
    worker ne fait qu'empiler ; le thread Qt vide la boîte.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, object, int, str]] = []

    def push(self, key: str, value: object, size_bytes: int, namespace: str = "project") -> None:
        with self._lock:
            self._items.append((key, value, max(1, int(size_bytes)), namespace))

    def drain(self) -> list[tuple[str, object, int, str]]:
        with self._lock:
            items = self._items
            self._items = []
        return items


class StudioRuntime:
    """Profil, cache, file et session pour un processus Kut-Studio."""

    def __init__(
        self,
        *,
        profile: str = "auto",
        preview_quality: str = "auto",
        resources: MachineResources | None = None,
    ) -> None:
        self.resources = resources if resources is not None else detect_resources()
        self.requested_profile = profile if isinstance(profile, str) else "auto"
        self.requested_quality = preview_quality if isinstance(preview_quality, str) else "auto"
        self.weight = "light"
        self.session_id = 1
        self.preview = PreviewQualityController()
        self.tasks = TaskQueue()
        self.worker = QueueWorker(self.tasks)
        self.mailbox = PreviewMailbox()
        self.diagnostics = RuntimeDiagnostics()
        self._profile = resolve_profile(self.requested_profile, self.resources, self.weight)
        self.cache = MemoryCache(max_bytes=self._profile.cache_budget_bytes)
        self.preview.apply(self.requested_quality, self._profile)

    def resolved_profile(self) -> PerformanceProfile:
        return self._profile

    def preview_divisor(self) -> int:
        return self.preview.divisor

    def preview_label(self) -> str:
        return quality_label(self.preview.divisor)

    def set_requested_profile(self, profile: str) -> None:
        self.requested_profile = profile
        self._refresh_budgets()

    def set_preview_quality(self, quality: str) -> None:
        self.requested_quality = quality
        self.preview.apply(quality, self._profile)

    def note_gpu(self, gpu_name: str | None) -> None:
        """Enregistre un indice GPU fourni par l'interface, si elle en a un."""
        self.resources = self.resources.with_gpu(gpu_name)
        self._refresh_budgets()

    def note_project_size(
        self,
        *,
        media_count: int,
        clip_count: int,
        track_count: int,
    ) -> None:
        self.weight = project_weight(
            media_count=media_count,
            clip_count=clip_count,
            track_count=track_count,
        )
        self._refresh_budgets()

    def begin_project(self) -> None:
        """Invalide le travail du projet précédent et libère son cache."""
        previous = self.session_id
        self.session_id += 1
        self.tasks.cancel_session(previous)
        self.cache.clear_namespace("project")
        self.weight = "light"
        self._refresh_budgets()

    def schedule(self, key: str, fn, *, priority: int = PRIORITY_BACKGROUND) -> None:
        """Planifie un travail de la session courante et démarre le worker."""
        self.tasks.submit(key, fn, priority=priority, session_id=self.session_id)
        self.worker.ensure_started()

    def shutdown(self) -> None:
        """Arrête la file et vide le cache à la fermeture de l'application."""
        self.tasks.cancel_all()
        self.worker.stop()
        self.cache.clear()

    def output_size(self, width: int, height: int) -> tuple[int, int]:
        from .preview_quality import output_size

        return output_size(width, height, self.preview.divisor)

    def _refresh_budgets(self) -> None:
        self._profile = resolve_profile(
            self.requested_profile,
            self.resources,
            self.weight,
        )
        self.cache.set_budget(self._profile.cache_budget_bytes)
        # La qualité explicite reste prioritaire ; Auto suit le profil.
        self.preview.divisor = resolve_divisor(self.requested_quality, self._profile)
