"""Méthodes de ``MainWindow`` regroupées : couche de performance.

Proxies média, gestionnaire de cache, qualité d'aperçu adaptative. Ce
mixin ne contient que le **câblage** : la logique vit dans
``core.proxy_manager``, ``core.cache_manager`` et
``core.preview_adaptive`` (testables sans interface).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from core.cache_manager import CacheManager
from core.platform_paths import user_cache_dir
from core.proxy_manager import ProxyInfo, ProxyManager, ProxyState
from ui import i18n

LOGGER = logging.getLogger(__name__)

GIB = 1024 ** 3
CACHE_ENFORCE_INTERVAL = 5.0  # secondes entre deux contrôles de budget non forcés


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class _ProxyEvents(QObject):
    """Pont thread de génération → thread Qt (connexion en file automatique)."""

    changed = Signal(str, object)


class PerformanceMixin:
    """Mixin de ``MainWindow`` (proxies, caches, qualité adaptative)."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_proxies(self, settings) -> None:
        """Crée le gestionnaire de proxies (avant les panneaux qui affichent leur état)."""
        directory = os.environ.get("KUT_STUDIO_PROXY_DIR") or (user_cache_dir() / "proxies")
        self.proxies = ProxyManager(
            directory,
            profile_id=settings.proxy_profile,
            enabled=settings.proxies_enabled,
        )
        # Un arrêt brutal peut laisser un ``.partial`` : on le nettoie au démarrage.
        try:
            self.proxies.cleanup_orphans()
        except OSError:
            pass
        self._proxy_events = _ProxyEvents(self)
        self.proxies.subscribe(self._proxy_events.changed.emit)
        self._proxy_events.changed.connect(self._on_proxy_changed)
        self._proxy_badge_timer = QTimer(self)
        self._proxy_badge_timer.setSingleShot(True)
        self._proxy_badge_timer.setInterval(200)
        self._proxy_badge_timer.timeout.connect(self._refresh_proxy_badges)

    def _init_cache_manager(self, settings) -> None:
        """Gestionnaire de cache global (après le moteur d'aperçu)."""
        engine = getattr(self, "preview_engine", None)
        self.cache_manager = CacheManager(
            memory=self.runtime.cache,
            previews=getattr(engine, "cache", None),
            proxies=self.proxies,
            max_bytes=int(settings.cache_max_gb * GIB),
            pinned_sources=self._project_source_paths,
            mograph=_mograph_frame_cache(),
            tracking=_tracking_cache(),
            multicam=_multicam_cache(),
            flow=_flow_cache(),
        )
        try:
            self.cache_manager.enforce()
            self.cache_manager.flow.cleanup_orphans()      # écritures de flux d'images abandonnées par un arrêt brutal
        except OSError:
            pass

    def _enforce_cache_budget(self, *, force: bool = False) -> None:
        """Ramène le cache sous son budget ; limité à un passage toutes les 5 s sauf ``force``.

        Appelé après chaque proxy terminé et chaque rendu de segment : sans cela
        le budget n'était vérifié qu'au démarrage et au changement de réglage.
        """
        manager = getattr(self, "cache_manager", None)
        if manager is None or getattr(self, "_proxies_closed", False):
            return
        now = time.monotonic()
        if not force and now - getattr(self, "_last_cache_enforce", 0.0) < CACHE_ENFORCE_INTERVAL:
            return
        self._last_cache_enforce = now
        try:
            manager.enforce()
        except OSError:
            pass

    def _project_source_paths(self) -> list[str]:
        return [a.path for a in self.project.media_assets if a.path]

    # ------------------------------------------------------------------
    # État des proxies (bibliothèque, aperçu)
    # ------------------------------------------------------------------

    def _proxy_state_for_asset(self, asset):
        """``(état, progression, erreur)`` pour la pastille de la bibliothèque, ou ``None``."""
        if asset.media_type != "video" or not asset.path:
            return None
        info = self.proxies.info(asset.path)
        return (info.state.value, info.progress, info.error)

    def _refresh_proxy_badges(self) -> None:
        panel = getattr(self, "project_panel", None)
        if panel is not None:
            panel.set_usage_for_assets()

    def _shutdown_proxies(self) -> None:
        """Fermeture : plus aucun événement de proxy ne doit atteindre la fenêtre.

        Un événement déjà posté dans la file Qt serait sinon livré **après**
        la libération du média (relecture de l'aperçu sur une fenêtre fermée).
        Les générations en cours sont arrêtées et FFmpeg est tué.
        """
        if not getattr(self, "_proxies_closed", False):
            self._proxies_closed = True
            try:
                self._proxy_events.changed.disconnect(self._on_proxy_changed)
            except (RuntimeError, TypeError):
                pass
            self._proxy_badge_timer.stop()
        self.proxies.shutdown()  # idempotent

    def _on_proxy_changed(self, source: str, info: ProxyInfo) -> None:
        """Un proxy a changé d'état (thread Qt) : badges, et source d'aperçu si utile."""
        if getattr(self, "_proxies_closed", False):
            return
        self._proxy_badge_timer.start()  # regroupe les rafales de progression
        if info.state in (ProxyState.GENERATING, ProxyState.PENDING):
            return
        if info.state == ProxyState.READY:
            # Un proxy vient de s'ajouter au disque : respecter le budget tout de suite.
            self._enforce_cache_budget(force=True)
        # Le proxy vient d'arriver, de partir ou d'échouer : l'aperçu en pause
        # doit relire la bonne source (original ou proxy).
        if not getattr(self, "is_playing", False):
            try:
                self._sync_preview_to_timeline()
            except Exception:
                LOGGER.warning(
                    "Rafraîchissement de l'aperçu en échec après un changement de proxy : le moniteur peut lire l'ancienne source",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Commandes de génération
    # ------------------------------------------------------------------

    def _proxy_candidates(self, assets) -> list:
        return [
            asset for asset in assets
            if asset.media_type == "video" and asset.path and os.path.isfile(asset.path)
        ]

    def _warn_if_ffmpeg_missing(self) -> bool:
        from core.tool_paths import find_media_tool

        if find_media_tool("ffmpeg"):
            return False
        _main_window().QMessageBox.warning(
            self, i18n.translate("perf.proxies.title"), i18n.translate("proxy.error.ffmpeg")
        )
        return True

    def _request_proxies(self, assets) -> int:
        candidates = self._proxy_candidates(assets)
        if not candidates:
            return 0
        if self._warn_if_ffmpeg_missing():
            return 0
        for asset in candidates:
            self.proxies.request(asset.path, duration=asset.duration)
        return len(candidates)

    def generate_proxy_for_asset(self, asset_id: str) -> int:
        asset = next((a for a in self.project.media_assets if a.id == asset_id), None)
        return self._request_proxies([asset] if asset is not None else [])

    def generate_proxies_for_project(self) -> int:
        return self._request_proxies(self.project.media_assets)

    def generate_proxies_for_selection(self) -> int:
        """Proxies des médias des clips sélectionnés dans la timeline."""
        timeline = self.timeline_panel
        selected = set(timeline.selected_clip_ids)
        if timeline.selected_clip_id:
            selected.add(timeline.selected_clip_id)
        asset_ids = []
        for clip_id in selected:
            clip = timeline.clip_model(clip_id)
            if clip is not None and clip.asset_id not in asset_ids:
                asset_ids.append(clip.asset_id)
        assets = [a for a in self.project.media_assets if a.id in asset_ids]
        return self._request_proxies(assets)

    def cancel_all_proxies(self) -> int:
        return self.proxies.cancel_all()

    def _on_proxy_action_requested(self, asset_id: str, action: str) -> None:
        asset = next((a for a in self.project.media_assets if a.id == asset_id), None)
        if action == "generate_project":
            self.generate_proxies_for_project()
        elif asset is None:
            return
        elif action == "generate":
            self.generate_proxy_for_asset(asset_id)
        elif action == "regenerate":
            if not self._warn_if_ffmpeg_missing():
                self.proxies.regenerate(asset.path, duration=asset.duration)
        elif action == "delete":
            self.proxies.delete(asset.path)
        elif action == "cancel":
            self.proxies.cancel(asset.path)

    # ------------------------------------------------------------------
    # Préférences : proxies et cache
    # ------------------------------------------------------------------

    def set_proxies_enabled(self, enabled: bool) -> None:
        """Active ou coupe les proxies **pour l'aperçu** (jamais pour l'export)."""
        self._apply_settings(replace(self._settings_snapshot(), proxies_enabled=bool(enabled)))

    def set_proxy_profile(self, profile_id: str) -> None:
        self._apply_settings(replace(self._settings_snapshot(), proxy_profile=profile_id))

    def set_cache_max_gb(self, gigabytes: float) -> None:
        self._apply_settings(replace(self._settings_snapshot(), cache_max_gb=float(gigabytes)))

    def _apply_performance_settings(self, settings) -> None:
        """Pousse les réglages de performance vers le gestionnaire (idempotent)."""
        proxies = getattr(self, "proxies", None)
        if proxies is None:
            return
        changed_view = (
            proxies.enabled != bool(settings.proxies_enabled)
            or proxies.profile.id != settings.proxy_profile
        )
        proxies.set_enabled(settings.proxies_enabled)
        proxies.set_profile(settings.proxy_profile)
        manager = getattr(self, "cache_manager", None)
        if manager is not None:
            budget = int(settings.cache_max_gb * GIB)
            if budget != manager.max_bytes:
                manager.set_max_bytes(budget)
        action = getattr(self, "proxies_action", None)
        if action is not None and action.isChecked() != bool(settings.proxies_enabled):
            action.blockSignals(True)
            action.setChecked(bool(settings.proxies_enabled))
            action.blockSignals(False)
        if changed_view:
            self._proxy_badge_timer.start()
            if not getattr(self, "is_playing", False):
                try:
                    self._sync_preview_to_timeline()
                except Exception:
                    LOGGER.warning(
                        "Rafraîchissement de l'aperçu en échec après le changement des réglages de proxy : le moniteur peut lire l'ancienne source",
                        exc_info=True,
                    )

    def cache_summary(self) -> dict:
        """Occupation des caches pour l'affichage (octets par couche + budget)."""
        return self.cache_manager.stats()

    def purge_caches(self, kind: str = "all", *, confirm: bool = True) -> int:
        """Purge une couche de cache après confirmation ; retourne les octets libérés.

        Sans danger : tout est recalculé à la demande, aucun projet n'est modifié.
        """
        if confirm:
            answer = _main_window().QMessageBox.question(
                self, i18n.translate("perf.cache.title"), i18n.translate("perf.cache.confirm")
            )
            if answer != QMessageBox.Yes:
                return 0
        freed = self.cache_manager.purge(kind)
        self._proxy_badge_timer.start()
        return freed

    def purge_project_cache(self, *, confirm: bool = True) -> int:
        if confirm:
            answer = _main_window().QMessageBox.question(
                self, i18n.translate("perf.cache.title"), i18n.translate("perf.cache.confirm")
            )
            if answer != QMessageBox.Yes:
                return 0
        freed = self.cache_manager.purge_project(self.project)
        self._proxy_badge_timer.start()
        return freed

    # ------------------------------------------------------------------
    # Qualité d'aperçu adaptative
    # ------------------------------------------------------------------

    def _observe_playback_quality(self, now: float) -> None:
        """Signale un tick de lecture au contrôleur de qualité (mode Auto seulement).

        En plus de la cadence des ticks, le moniteur GPU signale ses images
        perdues et un temps de rendu hors budget (voir ``core.preview_governor``).
        """
        stats_of = getattr(self, "preview_frame_stats", None)
        stats = stats_of() if stats_of is not None else None
        if stats is not None:
            from core.preview_governor import gpu_overloaded

            self.runtime.preview.adaptive.note_load(gpu_overloaded(stats))
        if self.runtime.preview.observe_tick(now) is not None:
            self._on_adaptive_quality_changed()

    def _reset_adaptive_quality(self) -> None:
        """Pause ou arrêt : l'aperçu revient à son niveau de base."""
        stats_of = getattr(self, "preview_frame_stats", None)
        stats = stats_of() if stats_of is not None else None
        if stats is not None:
            stats.reset()  # les pertes d'une lecture ne pèsent pas sur la suivante
        if self.runtime.preview.reset_adaptation():
            self._on_adaptive_quality_changed()
        else:
            preview = getattr(self, "preview_panel", None)
            if preview is not None:
                preview.set_quality_notice(None)

    def _request_lighter_proxies(self) -> None:
        """Qualité réduite : prépare (en tâche de fond) un proxy plus léger des médias actifs.

        Sans proxy plus léger prêt, la réduction ne diminue pas le décodage ; on
        le génère donc pour que la lecture suivante en profite. Jamais bloquant.
        """
        try:
            divisor = int(self.runtime.preview_divisor())
            active = self._ensure_timeline_index().active_at(
                self.project, float(self.playhead_seconds)
            )
        except Exception:
            LOGGER.debug("Clips actifs non déterminés : pas de proxy plus léger demandé", exc_info=True)
            return
        asset_by_path = {a.path: a for a in self.project.media_assets if a.path}
        for clip in active:
            if clip.track_type != "video" or not clip.source_path:
                continue
            asset = asset_by_path.get(clip.source_path)
            duration = float(getattr(asset, "duration", 0.0) or 0.0)
            self.proxies.request_lighter(clip.source_path, divisor, duration=duration)

    def _on_adaptive_quality_changed(self) -> None:
        self._apply_runtime_hints()
        if self.runtime.preview.degraded:
            self._request_lighter_proxies()
        # L'avis du moniteur réunit le niveau d'aperçu réduit et « aperçu simplifié » d'un clip interpolé.
        self._update_preview_notice()


def _tracking_cache():
    """Résultats d'analyse de tracking (budget disque global)."""
    from core.tracking_engine import TrackingCache

    return TrackingCache()


def _multicam_cache():
    """Enveloppes audio de la synchronisation Multicam (budget disque global)."""
    from core.audio_sync_cache import AudioSyncCache

    return AudioSyncCache()


def _flow_cache():
    """Vecteurs de mouvement du flux optique (budget disque global)."""
    from core.flow_cache import FlowCache

    return FlowCache()


def _mograph_frame_cache():
    """Cache des images de calques motion graphics (budget disque global)."""
    from core.mograph_stream import MographFrameCache

    return MographFrameCache()
