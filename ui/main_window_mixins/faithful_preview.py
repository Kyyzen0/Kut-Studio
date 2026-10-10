"""Méthodes de ``MainWindow`` regroupées : faithful_preview."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from ui import i18n

LOGGER = logging.getLogger(__name__)


class _PreviewEvents(QObject):
    """Pont thread de rendu → thread Qt pour l'état du moteur d'aperçu."""

    state = Signal(object)


class _PreviewPump:
    """Exécute les rendus de segments **hors du thread de l'interface**.

    Avant, le minuteur appelait ``engine.pump`` dans le thread Qt : chaque segment
    (un FFmpeg de plusieurs centaines de millisecondes) gelait la fenêtre. Le
    minuteur ne fait plus que **réveiller** ce thread ; l'arrêter suspend donc
    toujours les rendus (les tests s'en servent).
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="kut-preview-render", daemon=True)
        self._thread.start()

    def kick(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        """Arrête le thread et l'**attend** (``timeout`` secondes) ; ``False`` s'il tourne encore.

        Sans attente, un segment en cours finissait après la fermeture et notifiait une fenêtre en cours de
        destruction : erreur de segmentation (CI Linux, run 37667588029).
        """
        self._stop.set()
        self._wake.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._engine.pump(1)
            except Exception:
                LOGGER.debug(
                    "Rendu d'un segment d'aperçu en échec dans le thread de travail : segment abandonné, la boucle continue",
                    exc_info=True,
                )


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
                task_queue=TaskQueue(), cache=DiskPreviewCache(directory=cache_dir), flow_preference=self._flow_preference(),
            )
            try:
                self.preview_engine.cache.evict_if_needed()
            except Exception:
                LOGGER.debug(
                    "Éviction du cache d'aperçu au démarrage en échec : le cache peut dépasser son budget jusqu'à la prochaine éviction",
                    exc_info=True,
                )
            # Les rendus tournent dans un thread : l'état revient au thread Qt par signal.
            self._preview_events = _PreviewEvents(self)
            self._preview_events.state.connect(self._on_preview_engine_state)
            # Gardé pour le désabonner par identité à la fermeture (voir _stop_preview_pump).
            self._preview_listener = self._preview_events.state.emit
            self.preview_engine.subscribe(self._preview_listener)
            self._preview_pump = _PreviewPump(self.preview_engine)
            self._preview_pump_timer = QTimer(self)
            self._preview_pump_timer.setInterval(150)
            self._preview_pump_timer.timeout.connect(self._pump_preview_queue)
            self._preview_pump_timer.start()
        except Exception:
            LOGGER.debug("Aperçu fidèle indisponible : moteur non démarré", exc_info=True)
            self.preview_engine = None

    def _pump_preview_queue(self) -> None:
        """Réveille le thread de rendu (1 tâche par réveil) ; ne rend jamais ici."""
        pump = getattr(self, "_preview_pump", None)
        if pump is not None:
            pump.kick()

    def _stop_preview_pump(self) -> None:
        """Fermeture : plus aucune notification vers la fenêtre, puis arrêt **attendu** du thread de rendu.

        Ordre voulu : désabonner la fenêtre (un segment qui finit ne la notifie plus), annuler les rendus (leur FFmpeg
        est tué, le thread rend la main vite), puis attendre le thread. Avant, il était seulement prié de s'arrêter :
        un segment en cours se terminait pendant la destruction de la fenêtre et émettait un signal sur un objet Qt
        détruit (erreur de segmentation, CI Linux, run 37667588029).
        """
        engine = getattr(self, "preview_engine", None)
        listener = getattr(self, "_preview_listener", None)
        if engine is not None:
            if listener is not None:
                engine.unsubscribe(listener)
            engine.cancel_all()
        pump = getattr(self, "_preview_pump", None)
        if pump is not None and not pump.stop():
            LOGGER.warning("Fermeture : le thread de rendu d'aperçu tourne encore après 5 s ; il est abandonné")
        timer = getattr(self, "_preview_pump_timer", None)
        if timer is not None:
            timer.stop()

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
            LOGGER.debug(
                "Mise à jour de l'indicateur de calcul / cache du moniteur en échec : indicateur non actualisé",
                exc_info=True,
            )

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
        if manager is None:
            return None
        chooser = getattr(self, "preview_source_for", None)
        if chooser is None:
            return manager.preview_resolver()
        return lambda path, need_audio=False: chooser(path, need_audio=need_audio)

    def _preview_segment_jobs(self, center: float, velocity: float | None = None) -> list:
        """Segments à pré-rendre autour de la tête de lecture (grille alignée).

        Le choix des segments (courant, devant, derrière) dépend de la
        vitesse de la tête : voir :class:`core.prefetch.PrefetchPlanner`.
        Chaque job porte la priorité de son rang. Le coût est celui d'un
        plan *fenêtré* par segment, pas du projet entier.
        """
        from core.preview_segments import build_segment_job

        planner = self._prefetch_planner()
        try:
            duration = float(self._ensure_timeline_index().duration)
            requests = planner.plan(center, duration=duration, velocity=velocity)
        except Exception:
            LOGGER.debug("Plan de préchargement non calculé à %.3f s : rien n'est préchargé", center, exc_info=True)
            return []
        resolver = self._preview_resolver()
        overrides = self._preview_grade_overrides()
        jobs = []
        for request in requests:
            try:
                job = build_segment_job(
                    self.project, request.index,
                    quality=self._render_quality, resolver=resolver,
                    timeline_index=self._ensure_timeline_index(), flow_preference=self._flow_preference(),
                    grade_overrides=overrides,
                )
            except Exception:
                LOGGER.debug("Segment %s non construit : préchargement sauté pour ce segment", request.index, exc_info=True)
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
            LOGGER.debug(
                "Transmission de l'état de lecture au moteur d'aperçu en échec : la file garde ses priorités précédentes",
                exc_info=True,
            )
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
            LOGGER.debug(
                "Abandon des segments hors de la fenêtre d'aperçu en échec : ils restent dans la file",
                exc_info=True,
            )
        for job in jobs:
            try:
                engine.request(job, job.priority)
            except Exception:
                LOGGER.debug("Demande de rendu d'un segment d'aperçu en échec : segment non planifié", exc_info=True)

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
        from core.preview_segments import (
            build_segment_job,
            segment_params_hash,
            segment_plan,
        )

        resolver = self._preview_resolver()
        overrides = self._preview_grade_overrides()
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
                                 timeline_index=self._ensure_timeline_index(), grade_overrides=overrides),
                    self.project, self._render_quality, end, flow_preference=self._flow_preference(),
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
                    timeline_index=self._ensure_timeline_index(), flow_preference=self._flow_preference(),
                    grade_overrides=overrides,
                )
                if job is None:
                    return None
        except Exception:
            LOGGER.debug("Segment de l'aperçu non construit à %.3f s : pas d'image en cache", timeline_time, exc_info=True)
            return None
        try:
            path = engine.cache.lookup(job.key)
        except Exception:
            LOGGER.debug("Cache d'aperçu illisible : segment traité comme absent", exc_info=True)
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
                flow_preference=self._flow_preference(),
            )
        except Exception:
            LOGGER.debug("Empreinte des réglages d'aperçu non calculée", exc_info=True)
            return None

    def _present_cached_preview_at(self, timeline_time: float) -> bool:
        """Affiche directement un segment composé, sans réévaluer la timeline."""
        cached_preview = self._cached_preview_at(timeline_time)
        if cached_preview is None:
            return False
        cached_path, segment_start = cached_preview
        # Le segment contient déjà les calques motion graphics : l'aperçu
        # interactif des calques est masqué (sinon il serait doublé).
        self._viewer_composited = True
        self.preview_panel.set_mograph_visible(False)
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
        self.preview_panel.set_color_grade(None)
        # Idem pour la fusion, le masque et les calques d'effets du moniteur GPU : le segment les contient
        # déjà. Ceux du clip précédemment affiché (multiply, overlay, masque...) étaient redessinés par-dessus,
        # et un fond noir sous un « multiply » donnait du noir.
        self.preview_panel.set_layer_compositing(None, None)
        self.preview_panel.set_adjustments(())
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
            LOGGER.debug(
                "Invalidation de l'aperçu en échec pour le clip %s : des segments périmés peuvent s'afficher",
                clip_id, exc_info=True,
            )
