"""Méthodes de ``MainWindow`` regroupées : faithful_preview."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from ui import i18n


class FaithfulPreviewMixin:
    """Mixin de ``MainWindow`` (faithful_preview)."""

    def _init_faithful_preview(self) -> None:
        """Moteur d'aperçu fidèle : cache disque hors .kut + rendus bg."""
        import os as _os

        try:
            from core.preview_cache import DiskPreviewCache
            from core.preview_engine import PreviewEngine
            from core.task_queue import TaskQueue

            cache_dir = _os.environ.get("KUT_STUDIO_CACHE_DIR")
            self.preview_engine = PreviewEngine(
                task_queue=TaskQueue(), cache=DiskPreviewCache(directory=cache_dir)
            )
            try:
                self.preview_engine.cache.evict_if_needed()
            except Exception:
                pass
            self.preview_engine.subscribe(self._on_preview_engine_state)
            self._preview_pump_timer = QTimer(self)
            self._preview_pump_timer.setInterval(150)
            self._preview_pump_timer.timeout.connect(self._pump_preview_queue)
            self._preview_pump_timer.start()
        except Exception:
            self.preview_engine = None

    def _pump_preview_queue(self) -> None:
        """Vide la file d'aperçu sans bloquer l'interface (1 tâche/tick)."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.pump(1)
        except Exception:
            pass

    def _on_preview_engine_state(self, state) -> None:
        """Indicateur 'Calcul de l'aperçu' + état du cache sur le moniteur."""
        panel = getattr(self, "preview_panel", None)
        if panel is None:
            return
        computing = bool(getattr(state, "pending", 0) or getattr(state, "running", 0))
        was_computing = bool(getattr(self, "_preview_was_computing", False))
        self._preview_was_computing = computing
        try:
            if computing:
                panel.set_render_state(True, i18n.translate("preview.computing"))
            else:
                panel.set_render_state(False)
                cached = int(getattr(state, "cached_segments", 0) or 0)
                if cached > 0:
                    if was_computing:
                        self._enforce_cache_budget()  # des segments viennent d'être écrits
                    panel.set_cache_state(True, i18n.translate("preview.cached"))
                    # Le rendu vient de se terminer : remplace immédiatement
                    # le média source par le segment composé, sans attendre un
                    # mouvement de la tête de lecture.
                    if was_computing and not bool(getattr(self, "is_playing", False)):
                        self._present_cached_preview_at(
                            float(getattr(self, "playhead_seconds", 0.0))
                        )
        except Exception:
            pass

    def _prefetch_planner(self):
        """Planificateur de préchargement (vitesse de la tête, grille de segments)."""
        planner = getattr(self, "_prefetch", None)
        if planner is None:
            from core.prefetch import PrefetchPlanner
            from core.preview_cache import SEGMENT_SECONDS

            planner = PrefetchPlanner(segment_seconds=SEGMENT_SECONDS)
            self._prefetch = planner
        return planner

    def _preview_resolver(self):
        """Résolveur de chemins pour l'aperçu (proxys), ``None`` sans gestionnaire."""
        manager = getattr(self, "proxies", None)
        return manager.preview_resolver() if manager is not None else None

    def _preview_segment_jobs(self, center: float, velocity: float | None = None) -> list:
        """Segments à pré-rendre autour de la tête de lecture (grille alignée).

        Le choix des segments (courant, devant, derrière) dépend de la
        vitesse de la tête : voir :class:`core.prefetch.PrefetchPlanner`.
        Chaque job porte la priorité de son rang. Le coût est celui d'un
        plan *fenêtré* par segment, pas du projet entier.
        """
        try:
            from core.preview_segments import build_segment_job
        except Exception:
            return []
        planner = self._prefetch_planner()
        try:
            duration = float(self._ensure_timeline_index().duration)
            requests = planner.plan(center, duration=duration, velocity=velocity)
        except Exception:
            return []
        resolver = self._preview_resolver()
        jobs = []
        for request in requests:
            try:
                job = build_segment_job(
                    self.project, request.index,
                    quality=self._render_quality, resolver=resolver,
                    timeline_index=self._ensure_timeline_index(),
                )
            except Exception:
                continue
            if job is not None:
                job.priority = request.priority
                jobs.append(job)
        return jobs

    def _schedule_preview_around(self, center: float) -> None:
        """Précharge les segments utiles autour de la tête de lecture.

        Pendant la lecture, rien n'est lancé (le CPU sert à la lecture).
        En pause, la vitesse récente de la tête décide de ce qui vaut la
        peine d'être rendu ; les demandes devenues lointaines sont
        abandonnées pour que la file ne se remplisse pas de segments qui
        ne serviront jamais.
        """
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.set_playing(bool(self.is_playing))
        except Exception:
            pass
        planner = self._prefetch_planner()
        if bool(self.is_playing):
            planner.reset()
            return  # lecture : on limite le travail CPU/GPU
        velocity = planner.note_position(float(center))
        jobs = self._preview_segment_jobs(center, velocity)
        self._last_preview_jobs = list(jobs)
        try:
            duration = float(self._ensure_timeline_index().duration)
            low, high = planner.keep_range(center, duration=duration, velocity=velocity)
            span = planner.segment_seconds
            engine.cancel_outside(low * span - 1e-6, (high + 1) * span)
        except Exception:
            pass
        for job in jobs:
            try:
                engine.request(job, job.priority)
            except Exception:
                pass

    def _cached_preview_at(self, timeline_time: float):
        """Retourne ``(chemin, début)`` pour le segment fidèle actif.

        Un segment planifié reste utilisable tant que l'empreinte de **ses**
        couches n'a pas changé ; une modification ailleurs dans le montage
        ne l'invalide pas. Un instant qui n'est couvert par aucun segment
        planifié (balayage) retombe sur le segment de la grille : s'il est
        déjà en cache, il est montré sans nouveau rendu.
        """
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return None
        try:
            from core.preview_segments import (
                build_segment_job,
                segment_params_hash,
                segment_plan,
            )
        except Exception:
            return None
        resolver = self._preview_resolver()
        job = None
        for candidate in getattr(self, "_last_preview_jobs", ()):
            start = float(getattr(candidate, "start", 0.0))
            duration = float(getattr(candidate, "duration", 0.0))
            if start <= timeline_time < start + duration:
                job = candidate
                break
        try:
            if job is not None:
                end = job.start + job.duration
                fresh = segment_params_hash(
                    segment_plan(self.project, job.start, end, resolver=resolver,
                                 timeline_index=self._ensure_timeline_index()),
                    self.project, self._render_quality, end,
                )
                if getattr(getattr(job, "key", None), "params_hash", None) != fresh:
                    # Les média, trims ou effets du segment ont changé depuis
                    # la planification : jamais remplacer la source courante.
                    return None
            else:
                planner = self._prefetch_planner()
                job = build_segment_job(
                    self.project, planner.index_of(timeline_time),
                    quality=self._render_quality, resolver=resolver,
                    timeline_index=self._ensure_timeline_index(),
                )
                if job is None:
                    return None
        except Exception:
            return None
        try:
            path = engine.cache.lookup(job.key)
        except Exception:
            path = None
        if path is None:
            return None
        return str(path), float(job.start)

    def _preview_params_hash(self) -> str | None:
        """Empreinte du plan courant, pour refuser un cache devenu obsolète."""
        try:
            from core.filter_graph import fingerprint_plan
            from core.render_plan import build_render_plan

            plan = build_render_plan(self.project)
            return fingerprint_plan(
                plan,
                width=self.project.width,
                height=self.project.height,
                fps=self.project.fps,
                quality=self._render_quality,
            )
        except Exception:
            return None

    def _present_cached_preview_at(self, timeline_time: float) -> bool:
        """Affiche directement un segment composé, sans réévaluer la timeline."""
        cached_preview = self._cached_preview_at(timeline_time)
        if cached_preview is None:
            return False
        cached_path, segment_start = cached_preview
        self.preview_panel.preview_at(
            cached_path, max(0.0, timeline_time - segment_start)
        )
        # Le segment inclut déjà transform et effets : les réappliquer dans
        # QGraphicsVideoItem doublerait le traitement.
        self.preview_panel.apply_transform(
            position_x=0.0,
            position_y=0.0,
            scale=1.0,
            rotation=0.0,
            opacity=1.0,
        )
        self.preview_panel.set_effects(())
        return True

    def _refresh_preview_cache_state(self) -> None:
        """Invalide/actualise l'état du cache après une modification."""
        self._schedule_preview_around(float(getattr(self, "playhead_seconds", 0.0)))

    def _invalidate_preview_for_clip(self, clip_id: str) -> None:
        """Invalide uniquement les segments affectés + annule l'obsolète."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.invalidate_clip(str(clip_id))
        except Exception:
            pass
