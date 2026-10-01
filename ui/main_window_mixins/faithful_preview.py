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

    def _preview_segment_jobs(self, center: float) -> list:
        """Segments proches de la tête de lecture (préchargement)."""
        try:
            from core.filter_graph import fingerprint_plan
            from core.preview_cache import PreviewSegmentKey, SEGMENT_SECONDS
            from core.preview_engine import PreviewJob
            from core.render_plan import build_render_plan
        except Exception:
            return []
        try:
            plan = build_render_plan(self.project)
        except Exception:
            return []
        if not (plan.video_layers or getattr(plan, "graphics_layers", ())):
            return []
        quality = self._render_quality
        params = fingerprint_plan(
            plan, width=self.project.width, height=self.project.height,
            fps=self.project.fps, quality=quality,
        )
        jobs = []
        cursor = max(0.0, float(center) - SEGMENT_SECONDS)
        end = float(center) + 2 * SEGMENT_SECONDS
        while cursor < end:
            seg_end = min(end, cursor + SEGMENT_SECONDS)
            clip_id = "timeline"
            for layer in plan.video_layers:
                start = float(layer.timeline_start)
                stop = float(layer.timeline_end)
                if start <= cursor < stop:
                    clip_id = layer.clip_id
                    break
            if clip_id == "timeline":
                for layer in getattr(plan, "graphics_layers", ()):
                    if layer.timeline_start <= cursor < layer.timeline_end:
                        clip_id = layer.clip_id
                        break
            key = PreviewSegmentKey(
                clip_id=clip_id, start=cursor, end=seg_end,
                quality=quality, params_hash=params,
            )
            jobs.append(
                PreviewJob(
                    key=key, plan=plan, width=self.project.width,
                    height=self.project.height, fps=int(self.project.fps),
                    quality=quality, start=cursor, duration=seg_end - cursor,
                )
            )
            cursor = seg_end
        return jobs

    def _schedule_preview_around(self, center: float) -> None:
        """Précharge les segments proches de la tête de lecture."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.set_playing(bool(self.is_playing))
        except Exception:
            pass
        if bool(self.is_playing):
            return  # lecture : on limite le travail CPU/GPU
        jobs = self._preview_segment_jobs(center)
        self._last_preview_jobs = list(jobs)
        if jobs:
            try:
                engine.prefetch_around(float(center), jobs)
            except Exception:
                pass

    def _cached_preview_at(self, timeline_time: float):
        """Retourne ``(chemin, début)`` pour le segment fidèle actif."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return None
        current_params = self._preview_params_hash()
        if current_params is None:
            return None
        jobs = getattr(self, "_last_preview_jobs", ())
        for job in jobs:
            if getattr(getattr(job, "key", None), "params_hash", None) != current_params:
                # Les média, trims ou effets ont changé depuis la
                # planification. Un segment de cache antérieur ne doit
                # jamais remplacer la source courante.
                continue
            start = float(getattr(job, "start", 0.0))
            duration = float(getattr(job, "duration", 0.0))
            if start <= timeline_time < start + duration:
                try:
                    path = engine.cache.lookup(job.key)
                except Exception:
                    path = None
                if path is not None:
                    return str(path), start
        return None

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
