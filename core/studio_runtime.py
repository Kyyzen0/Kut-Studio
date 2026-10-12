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

import os
import sys
import threading
import time
from dataclasses import dataclass, field

from .cache_store import MemoryCache
from .power_state import PowerState, detect_power_state, parse_power_override
from .preview_quality import PreviewQualityController, quality_label
from .runtime_profile import (
    MachineResources,
    PerformanceProfile,
    detect_resources,
    project_weight,
    resolve_profile,
)
from .runtime_regulator import OnDemandGate, RegulatorPolicy, compute_policy
from .task_queue import PRIORITY_BACKGROUND, QueueWorker, TaskQueue

# L'état énergie / chauffe ne change pas à chaque tick : on le relit au plus
# toutes les 15 s (pmset / sysfs), sur un thread de travail (voir :meth:`StudioRuntime._ensure_power_fresh`), ou plus
# tôt via :meth:`refresh_power_state`.
_POWER_TTL_SECONDS = 15.0


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

    Chaque entrée mémorise la **session** qui l'a produite. Un résultat
    de projet clos est ignoré à la lecture : sans cela, une tâche déjà
    partie chez le worker pouvait écrire dans le cache du projet
    suivant, qui vient pourtant d'être vidé.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, object, int, str, int]] = []

    def push(
        self,
        key: str,
        value: object,
        size_bytes: int,
        namespace: str = "project",
        session_id: int = 0,
    ) -> None:
        with self._lock:
            self._items.append(
                (key, value, max(1, int(size_bytes)), namespace, int(session_id))
            )

    def drain(
        self, current_session: int | None = None
    ) -> list[tuple[str, object, int, str]]:
        """Vide la boîte et retourne les entrées de la session courante.

        Les entrées issues d'une session antérieure sont **écartées** :
        elles appartiennent à un projet remplacé. Passer
        ``current_session=None`` retire ce filtre (usage de test isolé).
        """
        with self._lock:
            items = self._items
            self._items = []
        if current_session is None:
            return [entry[:4] for entry in items]
        return [
            entry[:4] for entry in items if entry[4] == current_session
        ]

    def pending_sessions(self) -> tuple[int, ...]:
        """Sessions encore représentées dans la boîte (diagnostic)."""
        with self._lock:
            return tuple(entry[4] for entry in self._items)

    def clear(self) -> None:
        """Jette toutes les entrées en attente."""
        with self._lock:
            self._items.clear()


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
        # Analyses longues (tracking, stabilisation) : même file et même
        # worker que le reste, sur une voie à part pour ne jamais retarder
        # les miniatures et formes d'onde de la timeline.
        self.analysis = TaskQueue()
        self.analysis_worker = QueueWorker(self.analysis)
        self.mailbox = PreviewMailbox()
        self.diagnostics = RuntimeDiagnostics()
        self._power = PowerState(source="unavailable")
        self._power_checked_at = 0.0
        # Relevé d'énergie en arrière-plan : le thread de travail ne dépose qu'une valeur (``PowerState`` immuable)
        # sous ce verrou ; seul le thread d'interface l'applique à la politique.
        self._power_lock = threading.Lock()
        self._power_probe_running = False
        self._power_probe_result: PowerState | None = None
        self.ondemand = OnDemandGate()
        self._bench_refresh_lock = threading.Lock()
        self._bench_refresh_pending = False
        self._profile = resolve_profile(self.requested_profile, self.resources, self.weight)
        self.cache = MemoryCache(max_bytes=self._profile.cache_budget_bytes)
        self.preview.apply(self.requested_quality, self._profile)
        self._policy = compute_policy(self._profile, power=self._power)

    def resolved_profile(self) -> PerformanceProfile:
        return self._profile

    def regulator_policy(self) -> RegulatorPolicy:
        """Leviers d'aperçu (banc + énergie) ; jamais appliqués à l'export."""
        self._ensure_power_fresh()
        return self._policy

    def power_state(self) -> PowerState:
        self._ensure_power_fresh()
        return self._power

    def refresh_power_state(self, state: PowerState | None = None) -> PowerState:
        """Injecte un état (tests, outils) ou, sans argument, le relit **sur place** (bloquant : pas pour l'interface)."""
        self._apply_power(state if state is not None else detect_power_state())
        return self._power

    def _apply_power(self, state: PowerState) -> None:
        self._power = state
        self._power_checked_at = time.monotonic()
        self._refresh_policy()

    def preview_divisor(self) -> int:
        # Même rafraîchissement énergie que :meth:`neighborhood_scale` : sinon
        # le premier ``_apply_runtime_hints`` publie le diviseur profil seul,
        # puis le scale déclenche le cran batterie trop tard.
        self._ensure_power_fresh()
        return self.preview.divisor

    def preview_label(self) -> str:
        self._ensure_power_fresh()
        return quality_label(self.preview.divisor)

    def neighborhood_scale(self) -> float:
        return self.regulator_policy().neighborhood_scale

    def prefetch_allowed(self) -> bool:
        return self.regulator_policy().prefetch_enabled

    def prefetch_max_extra(self) -> int:
        return self.regulator_policy().prefetch_max_extra

    def set_requested_profile(self, profile: str) -> None:
        self.requested_profile = profile
        self._refresh_budgets()

    def set_preview_quality(self, quality: str) -> None:
        self.requested_quality = quality
        self.preview.apply(quality, self._profile)
        self._refresh_policy()

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
        """Invalide le travail du projet précédent et libère son cache.

        Les résultats déjà calculés par l'ancien projet mais pas encore
        appliqués sont également écartés : sans cela ils repeupleraient
        le cache fraîchement vidé.
        """
        previous = self.session_id
        self.session_id += 1
        self.tasks.cancel_session(previous)
        self.analysis.cancel_session(previous)
        self.mailbox.clear()
        self.cache.clear_namespace("project")
        self.weight = "light"
        self._refresh_budgets()

    def schedule(self, key: str, fn, *, priority: int = PRIORITY_BACKGROUND) -> None:
        """Planifie un travail de la session courante et démarre le worker."""
        self.tasks.submit(key, fn, priority=priority, session_id=self.session_id)
        self.worker.ensure_started()

    def schedule_analysis(self, key: str, fn, *, priority: int = PRIORITY_BACKGROUND) -> None:
        """Planifie une analyse longue de la session courante (voie d'analyse)."""
        self.analysis.submit(key, fn, priority=priority, session_id=self.session_id)
        self.analysis_worker.ensure_started()

    def schedule_machine_bench(self, *, force: bool = False) -> None:
        """Mesure le profil machine en fond (ADR-0001 étape 1) puis signale un rafraîchissement.

        Le banc tourne dans le worker ; les budgets (cache, qualité) sont
        appliqués ensuite sur le thread propriétaire via
        :meth:`apply_pending_machine_bench` — jamais depuis le worker.
        """
        from .machine_bench import bench_enabled, ensure_machine_bench, load_cached_bench

        if not force and not bench_enabled():
            return
        if not force and load_cached_bench(self.resources) is not None:
            return

        session = self.session_id

        def _run(token) -> None:
            if token.cancelled:
                return
            ensure_machine_bench(self.resources, force=force)
            if token.cancelled or session != self.session_id:
                return
            with self._bench_refresh_lock:
                self._bench_refresh_pending = True

        self.schedule("machine-bench", _run, priority=PRIORITY_BACKGROUND)

    def machine_bench_refresh_pending(self) -> bool:
        with self._bench_refresh_lock:
            return self._bench_refresh_pending

    def apply_pending_machine_bench(self) -> bool:
        """Applique le rafraîchissement Auto après le banc (thread propriétaire).

        Retourne ``True`` si les budgets ont été recalculés.
        """
        with self._bench_refresh_lock:
            if not self._bench_refresh_pending:
                return False
            self._bench_refresh_pending = False
        self._refresh_budgets()
        return True

    def shutdown(self) -> None:
        """Arrête la file et vide le cache à la fermeture de l'application."""
        self.tasks.cancel_all()
        self.worker.stop()
        self.analysis.cancel_all()
        self.analysis_worker.stop()
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
        # La qualité explicite reste prioritaire ; Auto suit le profil. Un
        # changement de budget remet aussi l'adaptation temporaire à zéro.
        self.preview.apply(self.requested_quality, self._profile)
        self._refresh_policy()

    def _ensure_power_fresh(self) -> None:
        """Tient l'état énergie à jour **sans jamais bloquer** l'appelant (thread d'interface, à chaque déplacement).

        La lecture réelle (``pmset`` : trois processus de 2 s au plus ; sysfs ; Windows) tourne sur un thread de
        travail, au plus une à la fois et au plus toutes les 15 s. Ce thread dépose son résultat ; il est appliqué ici,
        au prochain appel, par le seul thread qui touche à la politique. Tant qu'aucune lecture n'a abouti, l'état reste
        le dernier connu (au démarrage : « indisponible », donc sans cran énergie). Un forçage par
        ``KUT_STUDIO_POWER`` ne coûte rien à lire : il s'applique tout de suite, sans thread.
        """
        with self._power_lock:
            finished, self._power_probe_result = self._power_probe_result, None
        if finished is not None:
            self._apply_power(finished)
        forced = parse_power_override(os.environ.get("KUT_STUDIO_POWER", ""))
        if forced is not None:
            if forced != self._power or self._power_checked_at <= 0:
                self._apply_power(forced)
            return
        if self._power_checked_at > 0 and time.monotonic() - self._power_checked_at < _POWER_TTL_SECONDS:
            return
        with self._power_lock:
            if self._power_probe_running:
                return
            self._power_probe_running = True
        threading.Thread(target=self._probe_power, name="kut-power-probe", daemon=True).start()

    def _probe_power(self) -> None:
        """Thread de travail : lit l'énergie et dépose le résultat (jamais de Qt, jamais la politique)."""
        try:
            state = detect_power_state()
        except Exception:  # noqa: BLE001 — l'énergie ne doit jamais empêcher l'aperçu
            import logging

            logging.getLogger("kut_studio.studio_runtime").debug(
                "État énergie illisible : régulateur sans cran batterie/chauffe",
                exc_info=True,
            )
            state = PowerState(source="unavailable")
        with self._power_lock:
            self._power_probe_result = state
            self._power_probe_running = False

    def _refresh_policy(self) -> None:
        # Auto + profil Auto : le cran énergie monte le diviseur de base. Un
        # choix explicite (full / half / profil forcé) n'est pas rabaissé —
        # préchargement et voisinage suivent quand même la politique.
        energy_divisor = None
        if self.requested_quality == "auto" and self.requested_profile == "auto":
            energy_divisor = self._profile.preview_divisor
        previous = self._policy
        self._policy = compute_policy(
            self._profile,
            power=self._power,
            preview_divisor=energy_divisor,
        )
        if (
            self.requested_quality == "auto"
            and self.requested_profile == "auto"
            and self._policy.preview_divisor != self.preview.baseline
        ):
            previous_baseline = self.preview.baseline
            current = self.preview.divisor
            new_baseline = self._policy.preview_divisor
            self.preview.baseline = new_baseline
            self.preview.adaptive.set_baseline(new_baseline)
            # Conserve une surcharge adaptative (diviseur déjà sous l'ancienne
            # base). Un cran purement énergétique suit le nouveau baseline
            # (batterie → 2, retour secteur → 1), sans rester collé à l'arrêt.
            if current > previous_baseline:
                keep = max(current, new_baseline)
                self.preview.divisor = keep
                self.preview.adaptive.divisor = keep
            else:
                self.preview.divisor = new_baseline
        if (
            previous.prefetch_enabled != self._policy.prefetch_enabled
            or previous.prefetch_max_extra != self._policy.prefetch_max_extra
            or previous.preview_divisor != self._policy.preview_divisor
            or previous.neighborhood_half != self._policy.neighborhood_half
        ):
            self.ondemand.invalidate()
