"""Méthodes de ``MainWindow`` regroupées : décodage matériel et moniteur GPU.

Câblage seulement : la décision vit dans ``core.decode_policy`` (décodeur),
``core.gpu_backend`` (rendu CPU/GPU), ``core.memory_monitor`` (pression
mémoire) ; l'exécution GPU dans ``ui.gpu_preview``. Tout est testable sans GPU :
en plateforme ``offscreen`` (CI), le rendu Auto est le CPU.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal

from core.decode_policy import (
    DecodeContext,
    DecodeHealth,
    DecodeProfile,
    DecodePurpose,
    StreamProbe,
    configure_qt_decoding,
    measure_decode,
    set_default_context,
)
from core.gpu_backend import FrameStats, GpuCrashGuard, GpuHealth, resolve_preview_backend
from core.gpu_cache import default_budget
from core.hardware_cache import default_service
from core.hardware_decoding import DECODE_LABELS, CODEC_BY_ID, DecodeMode, coerce_decode_mode
from core.memory_monitor import MemoryWatch
from ui import i18n

LOGGER = logging.getLogger("kut_studio.gpu")

MEMORY_POLL_MS = 5000
MAX_MEASUREMENTS_PER_SESSION = 4


class _MeasureEvents(QObject):
    done = Signal()


class HardwarePreviewMixin:
    """Mixin de ``MainWindow`` (décodage, rendu de l'aperçu, mémoire, diagnostics)."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_hardware_preview(self, settings) -> None:
        """À appeler **avant** la création du premier ``QMediaPlayer`` (variable Qt)."""
        self._decode_mode = coerce_decode_mode(settings.decode_mode)
        self._preview_backend_request = settings.preview_backend
        self._gpu_health = GpuHealth()
        self._gpu_guard = GpuCrashGuard()
        self._gpu_guard.begin_session()        # un marqueur resté en place = plantage de la session précédente
        self._decode_context = DecodeContext(
            mode=self._decode_mode,
            capabilities_provider=lambda: default_service().cached(),
            health=DecodeHealth(),
            probe=StreamProbe(),
        )
        set_default_context(self._decode_context)
        known = default_service().cached()
        from core.hardware_cache import hardware_decoding_disabled

        # Décodage coupé par l'environnement : le lecteur Qt est forcé en logiciel
        # dès ce démarrage, sans attendre la détection.
        qt_mode = DecodeMode.CPU if hardware_decoding_disabled() else self._decode_mode
        self._qt_decode = configure_qt_decoding(qt_mode, known)
        self._qt_decode_mode_at_start = self._decode_mode
        self._resolved_preview = None
        self._gpu_device_label = ""
        self._memory_watch = MemoryWatch()
        self._measurements_done = 0
        self._measuring = False
        self._hardware_closed = False
        self._measure_events = _MeasureEvents()
        self._measure_events.done.connect(self._refresh_encoding_settings_tab)
        if known is not None:
            self._attach_decode_profile(known)

    def _start_hardware_preview(self) -> None:
        """Après la création du viewer : rendu de l'aperçu et surveillance mémoire."""
        panel = self.preview_panel
        panel.gpu_failed.connect(self._on_gpu_failed)
        panel.gpu_ready.connect(self._on_gpu_ready)
        self._apply_preview_backend()
        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(MEMORY_POLL_MS)
        self._memory_timer.timeout.connect(self._poll_memory)
        self._memory_timer.start()

    # ------------------------------------------------------------------
    # Rendu de l'aperçu (CPU / GPU)
    # ------------------------------------------------------------------

    def _apply_preview_backend(self) -> None:
        from PySide6.QtGui import QGuiApplication

        resolved = resolve_preview_backend(
            self._preview_backend_request, health=self._gpu_health, guard=self._gpu_guard,
            platform_plugin=QGuiApplication.platformName(),
        )
        panel = self.preview_panel
        if resolved.is_gpu:
            budget = default_budget(self._memory_watch.last.total_bytes or _system_memory())
            if panel.enable_gpu(resolved.api, cache_budget=budget):
                self._gpu_guard.arm()          # retiré à l'arrêt propre ou au repli CPU
            else:
                resolved = replace(resolved, kind="cpu", fallback_reason="initialisation impossible")
        else:
            panel.disable_gpu()
            self._gpu_guard.disarm()
        self._resolved_preview = resolved
        LOGGER.info("Rendu de l'aperçu : %s (%s)", resolved.label, resolved.reason)
        if not getattr(self, "is_playing", False):
            try:
                self._sync_preview_to_timeline()
            except Exception:
                pass

    def _on_gpu_ready(self, label: str) -> None:
        self._gpu_device_label = label
        LOGGER.info("Aperçu GPU confirmé : %s", label)

    def _on_gpu_failed(self, kind: str, detail: str) -> None:
        """Le panneau est déjà revenu au CPU : on note, on prévient discrètement, on continue."""
        self._gpu_health.record(kind, detail)
        self._gpu_guard.disarm()               # le GPU n'est plus actif : un plantage ultérieur n'est pas le sien
        resolved = self._resolved_preview
        if resolved is not None:
            self._resolved_preview = replace(resolved, kind="cpu", fallback_reason=f"{kind} : {detail}")
        try:
            self.statusBar().showMessage(i18n.translate("preview.gpu_fallback", detail=kind), 6000)
        except Exception:
            pass
        if not self._gpu_health.disabled and kind == "device_lost":
            # Un périphérique perdu (pilote réinitialisé) peut revenir : un seul nouvel essai.
            QTimer.singleShot(1500, self._apply_preview_backend)
        elif not getattr(self, "is_playing", False):
            try:
                self._sync_preview_to_timeline()
            except Exception:
                pass

    def set_preview_backend(self, value: str) -> None:
        """Préférence « Rendu de l'aperçu » (Auto / CPU / GPU), appliquée tout de suite."""
        from core.gpu_backend import coerce_preview_backend

        value = coerce_preview_backend(value).value
        self._preview_backend_request = value
        self._gpu_health.reset()  # un choix explicite redonne sa chance au GPU
        self._gpu_guard.reset()
        self._apply_preview_backend()
        self._apply_settings(replace(self._settings_snapshot(), preview_backend=value))

    def set_decode_mode(self, value: str) -> None:
        """Préférence « Décodage vidéo ». Aperçu fidèle, proxies, tracking : immédiat ;
        moniteur temps réel (Qt) : au prochain démarrage."""
        mode = coerce_decode_mode(value)
        self._decode_mode = mode
        self._decode_context.mode = mode
        self._decode_context.health.reset()
        self._apply_settings(replace(self._settings_snapshot(), decode_mode=mode.value))
        engine = getattr(self, "preview_engine", None)
        if engine is not None:
            try:
                engine.cancel_all()  # les segments en vol suivaient l'ancien réglage
            except Exception:
                pass

    def decode_mode_options(self) -> list[tuple[str, str]]:
        """``(valeur, libellé)`` : Auto, CPU, puis chaque backend **validé** ici."""
        options = [(DecodeMode.AUTO.value, i18n.translate("perf.decode.auto")),
                   (DecodeMode.CPU.value, i18n.translate("perf.decode.cpu"))]
        capabilities = default_service().cached()
        if capabilities is not None:
            for backend in capabilities.decode_backends():
                options.append((backend.value, DECODE_LABELS[backend]))
        current = self._decode_mode
        if current not in (DecodeMode.AUTO, DecodeMode.CPU) and current.value not in {v for v, _ in options}:
            options.append((current.value, f"{DECODE_LABELS[current]} (indisponible)"))
        return options

    def _sync_gpu_compositing(self, clip, timeline_time: float) -> None:
        """Mode de fusion et matte des masques du clip affiché (moniteur GPU)."""
        compositing = getattr(clip, "compositing", None)
        blend = getattr(compositing, "blend_mode", None)
        matte = None
        has_masks = compositing is not None and bool(getattr(compositing, "masks", ()))
        # Le tracking peut ajouter un masque dérivé (recadrage de stabilisation).
        if has_masks or getattr(clip, "tracking", None) is not None:
            matte = self._clip_matte(clip, timeline_time)
        self.preview_panel.set_layer_compositing(blend, matte)

    def _clip_matte(self, clip, timeline_time: float):
        """``(clé, QImage)`` de la matte du clip, rastérisée comme à l'export (même code)."""
        try:
            from core.mograph_layers import scene_for_project
            from core.mograph_raster import render_layer_matte

            scene = scene_for_project(self.project)
            evaluated = scene.evaluate(clip.id, timeline_time)
            if not evaluated.masks:
                return None
            width, height = self.preview_panel.gpu_render_size()
            key = f"{clip.id}:{width}x{height}:{evaluated.masks!r}"
            return key, render_layer_matte(scene, clip.id, timeline_time, width, height)
        except Exception as error:  # un masque fautif ne bloque jamais le viewer
            LOGGER.debug("Matte indisponible : %s", error)
            return None

    # ------------------------------------------------------------------
    # Original ou proxy (sans supposer que le proxy gagne)
    # ------------------------------------------------------------------

    def preview_source_for(self, path: str, *, divisor: int = 1, purpose: DecodePurpose = DecodePurpose.SEGMENT,
                           need_audio: bool = False) -> str:
        """Fichier à lire pour l'aperçu : original ou proxy prêt, selon le coût de décodage.

        Ne lance jamais de processus dans le thread de l'interface : un flux pas
        encore sondé l'est en tâche de fond, et d'ici là le proxy prêt est pris
        (comportement historique).
        """
        from core.decode_policy import SourceOption, choose_preview_source

        proxy = self.proxies.resolve(path, divisor=divisor, need_audio=need_audio)
        if not proxy or proxy == path:
            return proxy
        probe = self._decode_context.probe
        original_stream, proxy_stream = probe.peek(path), probe.peek(proxy)
        if original_stream is None or proxy_stream is None:
            self._probe_later((path, proxy))
            return proxy
        mode = self._decode_mode
        if purpose is DecodePurpose.REALTIME and self._qt_decode[0] == "none":
            mode = DecodeMode.CPU  # le lecteur Qt décode en logiciel cette session
        needed = max(2, int(self.project.height) // max(1, int(divisor)))
        decision = choose_preview_source(
            SourceOption(path, original_stream), SourceOption(proxy, proxy_stream, is_proxy=True),
            needed_height=needed, mode=mode, capabilities=default_service().cached(), purpose=purpose,
            health=self._decode_context.health, profile=self._decode_context.profile,
        )
        return decision.path

    def _probe_later(self, paths) -> None:
        pending = getattr(self, "_probing", None)
        if pending is None:
            pending = self._probing = set()
        todo = [p for p in paths if p and p not in pending]
        if not todo:
            return
        pending.update(todo)
        probe = self._decode_context.probe

        def work() -> None:
            for path in todo:
                try:
                    probe.get(path)
                finally:
                    pending.discard(path)

        threading.Thread(target=work, name="kut-stream-probe", daemon=True).start()

    # ------------------------------------------------------------------
    # Mesures de décodage (Auto fondé sur des chiffres)
    # ------------------------------------------------------------------

    def _attach_decode_profile(self, capabilities) -> None:
        if self._decode_context.profile is None or getattr(self, "_profile_fingerprint", None) != capabilities.fingerprint:
            self._decode_context.profile = DecodeProfile(fingerprint=capabilities.fingerprint)
            self._profile_fingerprint = capabilities.fingerprint

    def _on_hardware_capabilities(self, capabilities) -> None:
        """Détection terminée : mesures, et réglage Qt si la détection arrive au premier lancement."""
        if getattr(self, "_hardware_closed", True):
            return
        self._attach_decode_profile(capabilities)
        self._schedule_decode_measurements()

    def _schedule_decode_measurements(self) -> None:
        """Mesure CPU contre matériel sur les vrais médias du projet (une fois par classe)."""
        if self._measuring or self._measurements_done >= MAX_MEASUREMENTS_PER_SESSION:
            return
        capabilities = default_service().cached()
        profile = self._decode_context.profile
        if capabilities is None or profile is None or self._decode_mode is DecodeMode.CPU:
            return
        paths = [a.path for a in self.project.media_assets if a.media_type == "video" and a.path]
        if not paths:
            return
        from core.export_engine import _ffmpeg_command_prefix

        try:
            command = _ffmpeg_command_prefix()
        except ImportError:
            return
        self._measuring = True
        context = self._decode_context
        events = self._measure_events

        def work() -> None:
            seen = set()
            try:
                for path in paths:
                    if self._hardware_closed or self._measurements_done >= MAX_MEASUREMENTS_PER_SESSION:
                        break
                    stream = context.probe.get(path)
                    codec = stream.codec if stream is not None else None
                    if codec is None:
                        continue
                    backends = capabilities.usable_decode_backends(codec.id)
                    if not backends:
                        continue
                    key = (codec.id, stream.resolution_class)
                    if key in seen or profile.measured(backends[0], *key):
                        continue
                    seen.add(key)
                    for backend in (DecodeMode.CPU, backends[0]):
                        result = measure_decode(command, path, backend, codec)
                        if result is not None:
                            profile.put(backend, codec.id, stream.resolution_class, result)
                            LOGGER.info("Décodage mesuré %s/%s/%s : %.0f i/s", backend.value, codec.id,
                                        stream.resolution_class, result.fps)
                    self._measurements_done += 1
            finally:
                self._measuring = False
                try:
                    events.done.emit()
                except RuntimeError:
                    pass

        threading.Thread(target=work, name="kut-decode-measure", daemon=True).start()

    # ------------------------------------------------------------------
    # Mémoire
    # ------------------------------------------------------------------

    def _poll_memory(self) -> None:
        panel = getattr(self, "preview_panel", None)
        actions = self._memory_watch.poll(gpu_active=bool(panel is not None and panel.gpu_active))
        if not actions.any:
            return
        LOGGER.warning("Pression mémoire (%s) : %s", self._memory_watch.last.pressure, actions)
        if actions.purge_gpu_cache and panel is not None and panel.gpu_view is not None:
            panel.gpu_view.release_gpu_cache()
        if actions.purge_memory_cache:
            try:
                self.runtime.cache.clear()
            except Exception:
                pass
        if actions.reduce_quality:
            try:
                if self.runtime.requested_quality == "auto":
                    self.runtime.preview.adaptive.force_degrade()
                    self._on_adaptive_quality_changed()
            except Exception:
                pass
        if actions.disable_gpu and panel is not None and panel.gpu_active:
            panel.disable_gpu()
            self._gpu_health.record("out_of_memory", "pression mémoire critique")
        try:
            self.statusBar().showMessage(i18n.translate("preview.memory_pressure"), 6000)
        except Exception:
            pass

    def _release_gpu_for_project_change(self) -> None:
        """Nouveau projet : aucune texture de l'ancien ne doit survivre."""
        panel = getattr(self, "preview_panel", None)
        if panel is not None and panel.gpu_view is not None:
            panel.gpu_view.forget_source("main")
            panel.gpu_view.release_gpu_cache()
            panel.gpu_view.stats.reset()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def preview_frame_stats(self) -> FrameStats | None:
        panel = getattr(self, "preview_panel", None)
        view = getattr(panel, "gpu_view", None)
        return view.stats if view is not None else None

    def hardware_diagnostics_text(self) -> str:
        """Tout le diagnostic matériel, copiable : capacités, décodage, aperçu, mémoire."""
        capabilities = default_service().cached()
        lines: list[str] = []
        if capabilities is None:
            lines.append(i18n.translate("encoding.detecting"))
        else:
            lines.append(capabilities.describe())
        lines.append("")
        lines.append("— Décodage —")
        lines.append(f"Mode : {self._decode_mode.value}")
        qt_value, origin = self._qt_decode
        lines.append(f"Moniteur temps réel (Qt) : {qt_value or 'défaut de Qt'} ({origin})")
        if self._decode_mode is not self._qt_decode_mode_at_start:
            lines.append("  (nouveau mode appliqué au moniteur au prochain démarrage)")
        health = self._decode_context.health
        lines.append(f"Décodages matériels réussis : {health.hardware_runs} · replis CPU : {health.fallbacks}")
        for pair in health.blocked_pairs():
            lines.append(f"  banni pour la session : {pair[0]}/{pair[1]}")
        for event in health.events()[-3:]:
            lines.append(f"  repli {event.backend}/{event.codec} ({event.purpose}) : {event.detail}")
        profile = self._decode_context.profile
        if profile is not None:
            for key, item in sorted(profile.items().items()):
                backend, codec, size = key.split("/")
                label = CODEC_BY_ID[codec].label if codec in CODEC_BY_ID else codec
                cpu = f", {item.cpu_seconds_per_frame * 1000:.1f} ms CPU/img" if item.cpu_seconds_per_frame else ""
                lines.append(f"  mesuré {backend} · {label} · {size} : {item.fps:.0f} i/s{cpu}")
        lines.append("Export : décodage CPU (résultat déterministe)")
        lines.append("")
        lines.append("— Aperçu —")
        resolved = self._resolved_preview
        panel = self.preview_panel
        active = "GPU" if panel.gpu_active else "CPU"
        lines.append(f"Rendu demandé : {self._preview_backend_request} · actif : {active}")
        if resolved is not None and resolved.reason and not panel.gpu_active:
            lines.append(f"Raison : {_REASONS.get(resolved.reason, resolved.reason)}")
        if resolved is not None and resolved.fallback_reason:
            lines.append(f"Repli : {resolved.fallback_reason}")
        if self._gpu_device_label:
            lines.append(f"GPU : {self._gpu_device_label}")
        for event in self._gpu_health.events()[-3:]:
            lines.append(f"  échec GPU ({event.kind}) : {event.detail}")
        stats = self.preview_frame_stats()
        if stats is not None:
            average = stats.average_render_ms()
            p95 = stats.p95_render_ms()
            lines.append(
                f"Images reçues {stats.received} · affichées {stats.presented} · perdues {stats.dropped}"
            )
            if average is not None:
                lines.append(f"Rendu moyen {average:.2f} ms (p95 {p95:.2f} ms)")
            view = panel.gpu_view
            cache = view.cache.stats()
            textures = view.executor.texture_bytes if view.executor is not None else 0
            lines.append(
                f"Cache GPU {cache.entries} · {cache.bytes / 2**20:.1f} / {cache.budget_bytes / 2**20:.0f} Mo"
                f" · textures de travail {textures / 2**20:.1f} Mo"
            )
            if view.fallback_frames:
                lines.append(f"Images converties par Qt (format non lu par le shader) : {view.fallback_frames}")
        lines.append(f"Recalages du lecteur pendant la lecture : {panel.playback_seeks}")
        memory = self._memory_watch.last
        if not memory.source:
            from core.memory_monitor import read_memory_status

            memory = read_memory_status()
        lines.append("")
        lines.append("— Mémoire —")
        if memory.total_bytes:
            free = f"{memory.available_bytes / 2**30:.1f} Go libres / " if memory.available_bytes else ""
            lines.append(f"{free}{memory.total_bytes / 2**30:.0f} Go · pression {memory.pressure}")
        else:
            lines.append("non lisible sur ce système")
        return "\n".join(lines)

    def _shutdown_hardware_preview(self) -> None:
        self._hardware_closed = True
        guard = getattr(self, "_gpu_guard", None)
        if guard is not None:
            guard.disarm()                     # arrêt propre : la prochaine session peut réessayer le GPU
        timer = getattr(self, "_memory_timer", None)
        if timer is not None:
            timer.stop()
        panel = getattr(self, "preview_panel", None)
        if panel is not None:
            panel.disable_gpu()
        set_default_context(None)


_REASONS = {
    "requested_cpu": "rendu CPU demandé",
    "disabled": "aperçu GPU désactivé (KUT_STUDIO_GPU_PREVIEW=off)",
    "no_window_system": "pas de contexte graphique (plateforme Qt sans fenêtre)",
    "gpu_failed": "le GPU a échoué pendant la session",
    "gpu": "GPU en attente de sa première image",
}


def _system_memory() -> int | None:
    from core.runtime_profile import detect_resources

    return detect_resources().memory_bytes


__all__ = ["HardwarePreviewMixin", "DecodePurpose"]
