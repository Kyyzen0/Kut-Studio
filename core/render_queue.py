"""File de rendu : exécute des :class:`RenderJob` l'un après l'autre.

La file **réutilise** :class:`~core.export_engine.ExportEngine` : un seul
FFmpeg à la fois, piloté par ``QProcess`` (événementiel, jamais bloquant
pour la boucle Qt). Elle ne reconstruit aucune commande FFmpeg : pour
chaque job elle recharge l'instantané du projet, construit le
:class:`~core.render_plan.RenderPlan`, puis passe
:meth:`RenderJob.to_request` au moteur.

Garanties
---------

- **Fichier final atomique** : le rendu écrit dans ``.<nom>.<id>.partial.<ext>``,
  renommé en fichier final seulement après une sortie FFmpeg réussie.
  Un échec ou une annulation ne détruit donc jamais un ancien fichier
  de même nom, et ne laisse aucun fichier partiel.
- **Interruption** : un job ``RENDERING`` relu au démarrage (crash,
  arrêt forcé) devient ``FAILED`` avec ``error_kind == "interrupted"`` ;
  il n'est jamais présenté comme terminé.
- **Fermeture** : :meth:`shutdown` tue FFmpeg et attend sa fin ; aucun
  processus ne survit à l'application. Limite : après un arrêt brutal
  (``SIGKILL``, coupure de courant) FFmpeg peut finir seul son fichier
  ``.partial``, que le prochain démarrage supprime.
- **Pas de ré-entrance** : l'enchaînement vers le job suivant est différé
  d'un tour de boucle d'événements, après que le moteur a fini de
  nettoyer ses fichiers temporaires.

Pas de « pause » : voir :mod:`core.render_job`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .export_engine import ExportEngine, require_ffmpeg
from .project_io import load_project
from .project_model import Project
from .render_job import ErrorKind, JobStatus, RenderJob, RenderResult
from .render_plan import build_render_plan
from .render_presets import RenderPresetSpec
from .render_queue_store import RenderQueueStore
from .video_encoders import HardwareEncoder

MAX_ERROR_CHARS = 4000
"""Longueur conservée d'un message d'erreur FFmpeg (la fin est la plus utile)."""

INTERRUPTED_MESSAGE = (
    "Rendu interrompu : Kut-Studio s'est arrêté avant la fin. "
    "Relancez ce job pour le recommencer."
)
CLOSED_MESSAGE = "Rendu annulé : fermeture de Kut-Studio."


def partial_path_for(job: RenderJob) -> str:
    """Fichier temporaire de rendu d'un job (même dossier, même extension)."""
    final = Path(job.output_path)
    return str(final.with_name(f".{final.stem}.{job.id}.partial{final.suffix}"))


class RenderQueue(QObject):
    """File de jobs persistante, pilotée par un :class:`ExportEngine`.

    Signals:
        jobs_changed: la liste (ordre, ajout, suppression, statut) a changé.
        job_updated: ``job_id`` a changé (progression, statut).
        overall_progress_changed: progression globale de la file (0–100).
        run_state_changed: une exécution démarre ou s'arrête (pour griser
            les boutons) ; ``is_running`` donne le nouvel état.
        run_finished: la file est redevenue inactive ; résumé
            ``{"completed": n, "failed": n, "cancelled": n, "outputs": [...]}``.
    """

    jobs_changed = Signal()
    job_updated = Signal(str)
    overall_progress_changed = Signal(int)
    run_state_changed = Signal()
    run_finished = Signal(dict)
    encoder_fallback = Signal(str, str)
    """``(job_id, raison)`` : le mode Auto a basculé ce job de l'encodeur matériel vers le CPU."""

    def __init__(
        self,
        engine: ExportEngine,
        store: RenderQueueStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._store = store if store is not None else RenderQueueStore()
        self._jobs: list[RenderJob] = []
        self._current: RenderJob | None = None
        self._launched = False  # le moteur a-t-il été démarré pour _current ?
        self._partial: str | None = None
        self._mode: str | None = None  # None, "all" ou "single"
        self._targets: set[str] = set()  # jobs d'une exécution « single »
        self._batch: set[str] = set()
        self._counts = {"completed": 0, "failed": 0, "cancelled": 0}
        self._outputs: list[str] = []
        self._closing = False
        self._last_overall = 0
        self.last_persist_error: str = ""
        engine.progress_changed.connect(self._on_progress)
        engine.finished_ok.connect(self._on_finished)
        engine.failed.connect(self._on_failed)
        engine.cancelled.connect(self._on_cancelled)
        engine.encoder_selected.connect(self._on_encoder_selected)
        engine.encoder_fallback.connect(self._on_encoder_fallback)

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    @property
    def store(self) -> RenderQueueStore:
        return self._store

    @property
    def jobs(self) -> tuple[RenderJob, ...]:
        return tuple(self._jobs)

    def job(self, job_id: str) -> RenderJob | None:
        return next((job for job in self._jobs if job.id == job_id), None)

    @property
    def current_job(self) -> RenderJob | None:
        return self._current

    @property
    def is_busy(self) -> bool:
        """``True`` tant qu'un job est en cours de rendu."""
        return self._current is not None

    @property
    def is_running(self) -> bool:
        """``True`` tant qu'une exécution (toute la file ou un job) est active."""
        return self._mode is not None or self._current is not None

    @staticmethod
    def ffmpeg_available() -> bool:
        try:
            require_ffmpeg()
        except ImportError:
            return False
        return True

    @property
    def run_jobs(self) -> tuple[RenderJob, ...]:
        """Jobs de l'exécution en cours (ou de la dernière), dans l'ordre de la file."""
        return tuple(job for job in self._jobs if job.id in self._batch)

    def overall_progress(self) -> int:
        """Progression globale de l'exécution en cours (ou de la dernière).

        Moyenne des jobs de l'exécution pondérée par la durée du montage ;
        un job terminé compte 100, un job en attente 0 ; échoués et
        annulés ne comptent plus.
        """
        counted = [
            job for job in self._jobs
            if job.id in self._batch
            and job.status not in (JobStatus.FAILED, JobStatus.CANCELLED)
        ]
        if not counted:
            return 0
        weights = [max(job.duration_seconds, 0.0) for job in counted]
        if sum(weights) <= 0:
            weights = [1.0] * len(counted)
        total = sum(weights)
        done = sum(
            weight * (100 if job.status is JobStatus.COMPLETED else job.progress)
            for weight, job in zip(weights, counted)
        )
        return max(0, min(100, int(done / total)))

    # ------------------------------------------------------------------
    # Ajout
    # ------------------------------------------------------------------

    def enqueue(
        self,
        project: Project,
        spec: RenderPresetSpec,
        output_path: str,
        *,
        master_gain_db: float = 0.0,
        master_muted: bool = False,
        name: str | None = None,
    ) -> RenderJob:
        """Ajoute un export en attente et retourne son job.

        Valide tout de suite ce qui peut l'être (dossier de sortie, média
        exportable, sortie non déjà réservée) pour que l'utilisateur
        soit prévenu à l'ajout et non des minutes plus tard.

        Raises:
            ValueError: réglage ou projet inexploitable.
            OSError: l'instantané n'a pas pu être écrit.
        """
        output = Path(output_path).expanduser()
        if not output.parent.exists():
            raise ValueError(f"Le dossier de sortie est introuvable : {output.parent}")
        if output.suffix.lower().lstrip(".") != spec.container:
            output = output.with_suffix(f".{spec.container}")
        wanted = _same_path_key(output)
        for other in self._jobs:
            if not other.is_finished and _same_path_key(other.output_path) == wanted:
                raise ValueError(
                    f"Un export de la file écrit déjà dans ce fichier : {output.name}"
                )
        try:
            plan = build_render_plan(
                project, master_gain_db=master_gain_db, master_muted=master_muted
            )
        except KeyError as error:
            raise ValueError(f"Média introuvable dans le projet : {error}") from error
        if not (plan.video_layers or getattr(plan, "graphics_layers", ())):
            raise ValueError("Aucun média vidéo à exporter.")
        job = RenderJob.create(
            spec=spec,
            snapshot_path="",
            output_path=str(output),
            project_name=getattr(project, "name", "") or "",
            master_gain_db=master_gain_db,
            master_muted=master_muted,
            duration_seconds=plan.duration,
            name=name,
        )
        job.snapshot_path = str(self._store.write_snapshot(job.id, project))
        self._jobs.append(job)
        self._persist()
        self.jobs_changed.emit()
        return job

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------

    def start_all(self) -> bool:
        """Lance tous les jobs en attente, dans l'ordre. ``False`` s'il n'y en a pas."""
        if self._closing or self._mode is not None:
            return False
        if not any(job.status is JobStatus.WAITING for job in self._jobs):
            return False
        self._begin_run("all", None)
        return True

    def start_job(self, job_id: str) -> bool:
        """Lance un job précis, puis s'arrête (sauf si la file tourne déjà).

        Si un rendu est en cours, le job est promu en tête des jobs en
        attente : il sera le prochain.
        """
        job = self.job(job_id)
        if self._closing or job is None or job.status is not JobStatus.WAITING:
            return False
        if self._mode is None:
            self._begin_run("single", job_id)
            return True
        self._promote(job_id)
        if self._mode == "single":
            # Le job promu fait partie de l'exécution : sans cela elle
            # s'arrêterait après la cible initiale et le laisserait en attente.
            self._targets.add(job_id)
            self._batch.add(job_id)
            self._emit_overall()
        return True

    def stop(self) -> None:
        """Arrête l'exécution : annule le rendu en cours, laisse les autres en attente."""
        self._mode = None
        self._targets.clear()
        self.run_state_changed.emit()
        if self._current is not None:
            self.cancel(self._current.id)

    def cancel(self, job_id: str) -> bool:
        """Annule un job en attente ou en cours. ``False`` s'il est déjà terminé."""
        job = self.job(job_id)
        if job is None or job.is_finished:
            return False
        if job is self._current:
            if not self._launched:
                # Rendu préparé mais pas encore lancé : rien à tuer.
                self._finish_current(lambda: job.mark_cancelled())
                self._schedule_next()
                return True
            if self._engine.is_running:
                self._engine.cancel()
            else:  # le moteur a déjà fini : ``cancelled`` ne viendra plus
                self._finish_current(lambda: job.mark_cancelled())
                self._schedule_next()
            return True
        job.mark_cancelled()
        self._counts["cancelled"] += 1 if job.id in self._batch else 0
        self._after_change(job)
        return True

    def remove(self, job_id: str) -> bool:
        """Supprime un job (et son instantané). Refusé pendant son rendu."""
        job = self.job(job_id)
        if job is None or job is self._current:
            return False
        self._jobs.remove(job)
        self._batch.discard(job_id)
        self._store.delete_snapshot(job_id)
        self._persist()
        self.jobs_changed.emit()
        self._emit_overall()
        return True

    def retry_on_cpu(self, job_id: str) -> bool:
        """Relance un job dont l'encodeur explicite a échoué, avec l'encodeur CPU.

        Le choix de l'utilisateur n'est jamais modifié en silence : cette
        action est proposée par l'interface après l'échec et ne change que ce
        job. Retourne ``False`` si le job n'est pas relançable.
        """
        job = self.job(job_id)
        if job is None or not job.can_retry:
            return False
        job.hardware = HardwareEncoder.CPU.value
        return self.retry(job_id)

    def retry(self, job_id: str) -> bool:
        """Remet un job échoué, annulé ou terminé en attente.

        Un job terminé est re-rendu à l'identique (le fichier de sortie
        sera remplacé au succès). Retourne ``False`` si le job n'est pas
        terminé ou si son instantané a disparu.
        """
        job = self.job(job_id)
        if job is None or not job.can_retry:
            return False
        if not Path(job.snapshot_path).is_file():
            job.mark_failed(
                "L'instantané du projet est introuvable : ajoutez de nouveau l'export.",
                ErrorKind.SNAPSHOT_MISSING,
            )
            self._after_change(job)
            return False
        job.mark_waiting()
        self._after_change(job)
        if self._mode == "all" and self._current is None:
            self._batch.add(job.id)
            self._schedule_next()
        return True

    def clear_finished(self, *, include_failed: bool = False) -> int:
        """Retire les jobs terminés (et annulés). Les échecs restent, sauf demande."""
        removable = {JobStatus.COMPLETED, JobStatus.CANCELLED}
        if include_failed:
            removable.add(JobStatus.FAILED)
        victims = [job for job in self._jobs if job.status in removable and job is not self._current]
        for job in victims:
            self._jobs.remove(job)
            self._batch.discard(job.id)
            self._store.delete_snapshot(job.id)
        if victims:
            self._persist()
            self.jobs_changed.emit()
            self._emit_overall()
        return len(victims)

    def move(self, job_id: str, offset: int) -> bool:
        """Décale un job **en attente** de ``offset`` places parmi les jobs en attente."""
        job = self.job(job_id)
        if job is None or job.status is not JobStatus.WAITING or offset == 0:
            return False
        waiting_positions = [i for i, item in enumerate(self._jobs) if item.status is JobStatus.WAITING]
        rank = next(r for r, i in enumerate(waiting_positions) if self._jobs[i] is job)
        target = max(0, min(len(waiting_positions) - 1, rank + offset))
        if target == rank:
            return False
        waiting_jobs = [self._jobs[i] for i in waiting_positions]
        waiting_jobs.insert(target, waiting_jobs.pop(rank))
        for position, item in zip(waiting_positions, waiting_jobs):
            self._jobs[position] = item
        self._persist()
        self.jobs_changed.emit()
        return True

    def _promote(self, job_id: str) -> None:
        """Place un job en attente devant tous les autres jobs en attente."""
        job = self.job(job_id)
        waiting = [item for item in self._jobs if item.status is JobStatus.WAITING]
        if job is None or not waiting or waiting[0] is job:
            return
        index = self._jobs.index(waiting[0])  # ``job`` est après : l'index reste valable
        self._jobs.remove(job)
        self._jobs.insert(index, job)
        self._persist()
        self.jobs_changed.emit()

    # -- Cycle d'exécution -------------------------------------------------------------------------

    def _begin_run(self, mode: str, target: str | None) -> None:
        self._mode = mode
        self._targets = {target} if (mode == "single" and target) else set()
        self._counts = {"completed": 0, "failed": 0, "cancelled": 0}
        self._outputs = []
        if mode == "all":
            self._batch = {job.id for job in self._jobs if job.status is JobStatus.WAITING}
        else:
            self._batch = {target} if target else set()
        self._emit_overall()
        self.run_state_changed.emit()
        self._schedule_next()

    def _schedule_next(self) -> None:
        QTimer.singleShot(0, self._start_next)

    def _start_next(self) -> None:
        if self._closing or self._current is not None:
            return
        if self._mode is None:  # arrêt demandé : on clôt l'exécution
            self._emit_summary()
            return
        if self._mode == "single":
            job = next(
                (j for j in self._jobs
                 if j.id in self._targets and j.status is JobStatus.WAITING),
                None,
            )
        else:
            job = next((j for j in self._jobs if j.status is JobStatus.WAITING), None)
        if job is None:
            self._finish_run()
            return
        self._batch.add(job.id)
        self._current = job
        self._launched = False
        job.mark_rendering()
        self._after_change(job)
        # Préparation différée : l'appelant (clic, signal du moteur) a fini.
        QTimer.singleShot(0, self._launch_current)

    def _launch_current(self) -> None:
        job = self._current
        if job is None or self._launched or self._closing:
            return
        if not self.ffmpeg_available():
            self._finish_current(
                lambda: job.mark_failed(
                    "FFmpeg est introuvable. Installez FFmpeg puis relancez ce job.",
                    ErrorKind.FFMPEG_MISSING,
                )
            )
            self._schedule_next()
            return
        try:
            project = load_project(job.snapshot_path)
            plan = build_render_plan(
                project,
                master_gain_db=job.master_gain_db,
                master_muted=job.master_muted,
            )
            self._partial = partial_path_for(job)
            request = job.to_request(plan, self._partial)
        except FileNotFoundError:
            self._finish_current(
                lambda: job.mark_failed(
                    "L'instantané du projet est introuvable.", ErrorKind.SNAPSHOT_MISSING
                )
            )
            self._schedule_next()
            return
        except (OSError, ValueError, KeyError, TypeError) as error:
            message = str(error)
            self._finish_current(lambda: job.mark_failed(message, ErrorKind.INVALID))
            self._schedule_next()
            return
        # ``True`` avant ``start`` : le moteur peut émettre ``failed`` de façon synchrone.
        self._launched = True
        self._engine.start(request)

    def _finish_current(self, mutate) -> None:
        """Applique la transition finale du job courant et libère la file."""
        job = self._current
        if job is None:
            return
        mutate()
        self._discard_partial()
        self._current = None
        self._launched = False
        if job.status is JobStatus.COMPLETED:
            self._counts["completed"] += 1
            self._outputs.append(job.output_path)
        elif job.status is JobStatus.CANCELLED:
            self._counts["cancelled"] += 1
        elif job.status is JobStatus.FAILED:
            self._counts["failed"] += 1
        self._after_change(job)

    def _finish_run(self) -> None:
        self._mode = None
        self._targets.clear()
        self._emit_overall()
        self.run_state_changed.emit()
        self._emit_summary()

    def _emit_summary(self) -> None:
        """Émet ``run_finished`` une seule fois par exécution."""
        if not any(self._counts.values()):
            return
        summary = {**self._counts, "outputs": list(self._outputs)}
        self._counts = {"completed": 0, "failed": 0, "cancelled": 0}
        self._outputs = []
        self.run_finished.emit(summary)

    def _discard_partial(self) -> None:
        partial, self._partial = self._partial, None
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass

    # -- Signaux du moteur ----------------------------------------------------------------------------------

    def _owns_engine_event(self) -> bool:
        return self._current is not None and self._launched

    def _on_progress(self, value: int) -> None:
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        value = max(0, min(100, int(value)))
        if value == job.progress:
            return
        job.progress = value
        self.job_updated.emit(job.id)
        self._emit_overall()

    def _on_finished(self, _output: str) -> None:
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        partial = self._partial or ""

        def finish() -> None:
            try:
                if not os.path.isfile(partial):
                    raise OSError("le fichier rendu est introuvable")
                os.replace(partial, job.output_path)
                size = os.path.getsize(job.output_path)
            except OSError as error:
                job.mark_failed(
                    f"Le rendu est terminé mais le fichier n'a pas pu être écrit : {error}",
                    ErrorKind.IO,
                )
                return
            choice = getattr(self._engine, "last_encoder_choice", None)
            now = time.time()
            job.mark_completed(
                RenderResult(
                    output_bytes=size,
                    render_seconds=max(0.0, now - (job.started_at or now)),
                    timeline_seconds=job.duration_seconds,
                    encoder=getattr(choice, "encoder", ""),
                    hardware_used=getattr(getattr(choice, "used", None), "value", "cpu"),
                    fallback_reason=getattr(choice, "fallback_reason", None),
                ),
                now,
            )

        self._finish_current(finish)
        self._schedule_next()

    def _on_encoder_selected(self, choice) -> None:
        """Mémorise l'encodeur réellement lancé (visible dès le début du rendu)."""
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        job.encoder = getattr(choice, "encoder", "")
        job.hardware_used = getattr(getattr(choice, "used", None), "value", "cpu")
        job.fallback_reason = getattr(choice, "fallback_reason", None) or job.fallback_reason
        self.job_updated.emit(job.id)

    def _on_encoder_fallback(self, reason: str) -> None:
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        job.fallback_reason = reason
        job.diagnostics = getattr(self._engine, "last_diagnostics", "")[-800:]
        self.encoder_fallback.emit(job.id, reason)

    def _on_failed(self, message: str) -> None:
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        text = (message or "Le rendu a échoué.").strip()[-MAX_ERROR_CHARS:]
        kind = (
            ErrorKind.ENCODER
            if getattr(self._engine, "last_error_kind", "") == "encoder"
            else ErrorKind.FFMPEG
        )
        diagnostics = getattr(self._engine, "last_diagnostics", "") or job.diagnostics

        def fail() -> None:
            job.mark_failed(text, kind)
            job.diagnostics = diagnostics[-800:]

        self._finish_current(fail)
        self._schedule_next()

    def _on_cancelled(self) -> None:
        if not self._owns_engine_event():
            return
        job = self._current
        assert job is not None
        reason = CLOSED_MESSAGE if self._closing else ""
        self._finish_current(lambda: job.mark_cancelled(reason))
        self._schedule_next()

    # ------------------------------------------------------------------
    # Persistance, reprise, fermeture
    # ------------------------------------------------------------------

    def restore(self) -> int:
        """Relit la file persistée ; retourne le nombre de jobs restaurés.

        Un job ``RENDERING`` devient ``FAILED`` (interrompu) et son
        fichier partiel est supprimé. Un job en attente dont
        l'instantané a disparu devient ``FAILED`` aussi. Les instantanés
        orphelins sont nettoyés.
        """
        self._jobs = self._store.load()
        changed = False
        for job in self._jobs:
            if job.status is JobStatus.RENDERING:
                job.mark_failed(INTERRUPTED_MESSAGE, ErrorKind.INTERRUPTED)
                try:
                    os.unlink(partial_path_for(job))
                except OSError:
                    pass
                changed = True
            elif job.status is JobStatus.WAITING and not Path(job.snapshot_path).is_file():
                job.mark_failed(
                    "L'instantané du projet est introuvable : ajoutez de nouveau l'export.",
                    ErrorKind.SNAPSHOT_MISSING,
                )
                changed = True
        self._store.prune_orphans({job.id for job in self._jobs})
        if changed:
            self._persist()
        self.jobs_changed.emit()
        return len(self._jobs)

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Arrête proprement la file : FFmpeg tué et attendu, état enregistré.

        Retourne ``False`` si FFmpeg n'a pas pu être confirmé comme arrêté.
        À appeler à la fermeture de l'application ; les jobs en attente
        restent en attente et seront restaurés au prochain démarrage.
        """
        self._closing = True
        self._mode = None
        self._targets.clear()
        stopped = True
        if self._launched:
            stopped = self._engine.shutdown(timeout_ms)
        if self._current is not None:  # ``cancelled`` n'est pas passé par le moteur
            job = self._current
            self._finish_current(lambda: job.mark_cancelled(CLOSED_MESSAGE))
        self._persist()
        return stopped

    def _after_change(self, job: RenderJob) -> None:
        self._persist()
        self.job_updated.emit(job.id)
        self.jobs_changed.emit()
        self._emit_overall()

    def _persist(self) -> None:
        try:
            self._store.save(self._jobs)
            self.last_persist_error = ""
        except OSError as error:
            # Un disque en lecture seule ne doit pas interrompre un rendu.
            self.last_persist_error = str(error)

    def _emit_overall(self) -> None:
        value = self.overall_progress()
        if value != self._last_overall:
            self._last_overall = value
            self.overall_progress_changed.emit(value)


def _same_path_key(path: str | os.PathLike[str]) -> str:
    """Clé de comparaison de chemins (insensible à la casse hors POSIX)."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


__all__ = [
    "CLOSED_MESSAGE",
    "INTERRUPTED_MESSAGE",
    "RenderQueue",
    "partial_path_for",
]
