"""Méthodes de ``MainWindow`` : vitesse, courbe de vitesse, images intermédiaires, analyse du flux optique.

Un geste = une commande (:mod:`core.time_commands`) = une entrée d'historique. Le menu « Vitesse », l'inspecteur et les raccourcis
émettent ``(clip, commande, argument)`` ; la fenêtre l'applique au clip et à **toute la sélection** d'un coup (une seule entrée
annulable). Les gestes continus du Graph Editor (glisser un point, une poignée) appellent eux aussi :mod:`core.time_ops`
(:mod:`ui.graph_editor_time`) : même transaction, même ripple, jamais les mutateurs génériques de keyframes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QTimer

from core.flow_analysis import AnalysisState, FlowAnalysisJob
from core.frame_interpolation import plan_interpolation
from core.optical_flow import BackendPreference, BackendUnavailable, select_backend
from core.retime_layers import layer_jobs
from core.time_commands import apply_time_command
from core.time_ops import RippleMode
from core.time_presets import TimeSnapshot, copy_time, paste_time
from core.time_remapping import TimeInterpolation
from core.timeline_operations import find_clip
from ui.i18n import translate

LOGGER = logging.getLogger("kut_studio.time")

POLL_INTERVAL_MS = 150
_PLAYHEAD_COMMANDS = {"add_point", "hold"}


class TimeEditingMixin:
    """Mixin de ``MainWindow`` : commandes du temps d'un clip et analyse du flux optique."""

    _flow_job: FlowAnalysisJob | None = None
    """L'analyse du flux optique en cours (une à la fois), ``None`` sinon."""
    _flow_timer: QTimer | None = None
    _notice_timer: QTimer | None = None
    _time_clipboard: TimeSnapshot | None = None
    """Le temps copié (« Copier le temps »), prêt à coller."""
    _flow_backend_request: str = "auto"
    """Réglage « Calcul du flux optique » (``auto``, ``cpu``, ``gpu``)."""
    _time_ripple_timeline: bool = False
    """Une édition de vitesse garde la durée sur la timeline (sinon la portion de média)."""

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    def _time_targets(self, clip_id: str) -> list[str]:
        """Le clip et, s'il fait partie de la sélection, toute la sélection (dans l'ordre de la timeline)."""
        selected = set(getattr(self.timeline_panel, "selected_clip_ids", ()) or ())
        if clip_id not in selected:
            return [clip_id]
        starts = {}
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id in selected:
                    starts[clip.id] = clip.timeline_start
        return sorted(starts, key=lambda identifier: starts[identifier]) or [clip_id]

    def _playhead_local_time(self, clip_id: str) -> float:
        """Temps local du clip à la tête de lecture ; ``ValueError`` si elle est hors du clip."""
        clip = find_clip(self.project, clip_id)
        local = float(self.playhead_seconds) - clip.timeline_start
        if local < -1e-6 or local > clip.duration + 1e-6:
            raise ValueError(translate("time.status.playhead_outside"))
        return max(0.0, min(local, clip.duration))

    def on_time_command(self, clip_id: str, command: str, argument: object = None) -> None:
        """Point d'entrée unique du menu « Vitesse », de l'inspecteur et des raccourcis."""
        targets = self._time_targets(clip_id)
        if command == "copy":
            self._copy_time(clip_id)
        elif command == "paste":
            self._paste_time(targets)
        elif command == "analyze":
            self.start_flow_analysis(targets)
        elif command == "cancel_analysis":
            self.cancel_flow_analysis()
        elif command == "ripple":
            self.set_time_ripple_timeline(bool(argument))
        else:
            needs_playhead = command in _PLAYHEAD_COMMANDS or (command == "preset" and argument == "freeze")

            def operate(target: str) -> str:
                local = self._playhead_local_time(target) if needs_playhead else 0.0
                return apply_time_command(
                    self.project, target, command, argument, local_time=local, fps=float(self.project.fps),
                    mode=self._ripple_mode(),
                )

            self._run_time_edit(targets, operate)

    def _run_time_edit(self, targets: list[str], operate: Callable[[str], str]) -> bool:
        """Applique ``operate`` à chaque clip ; une seule entrée d'historique, l'état d'un clip refusé n'est pas touché.

        Retourne ``True`` si **tous** les clips ont été modifiés ; sinon l'utilisateur a déjà été prévenu du refus."""
        labels: list[str] = []
        refused: list[Exception] = []
        for target in targets:
            try:
                labels.append(operate(target))
            except (KeyError, ValueError) as error:
                refused.append(error)
        if not labels:
            self._report_edit_refused(refused[0] if refused else translate("time.status.no_clip"))
            return False
        if refused:
            self._report_edit_refused(refused[0])
        self._record_history(translate(labels[0]))
        self._reload_timeline_preserving_selection(targets[0])
        self._update_timeline_duration()
        self._mark_dirty()
        self._update_preview_notice()
        return not refused

    def _ripple_mode(self) -> RippleMode:
        """La politique de ripple choisie par l'utilisateur pour les éditions de vitesse."""
        return RippleMode.TIMELINE if self._time_ripple_timeline else RippleMode.SOURCE

    def set_time_ripple_timeline(self, enabled: bool) -> None:
        """Une édition de vitesse garde la durée sur la timeline (``True``) ou la portion de média (``False``, défaut)."""
        self._time_ripple_timeline = bool(enabled)
        self.timeline_panel.time_ripple_timeline = self._time_ripple_timeline
        self._apply_settings(replace(self._settings_snapshot(), time_ripple_timeline=self._time_ripple_timeline))

    # ------------------------------------------------------------------
    # Copier / coller le temps
    # ------------------------------------------------------------------

    def _copy_time(self, clip_id: str) -> None:
        try:
            self._time_clipboard = copy_time(find_clip(self.project, clip_id))
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        self.statusBar().showMessage(translate("time.status.copied"), 3000)

    def _paste_time(self, targets: list[str]) -> None:
        snapshot = self._time_clipboard
        if snapshot is None:
            self.statusBar().showMessage(translate("time.status.nothing_copied"), 4000)
            return

        def operate(target: str) -> str:
            paste_time(self.project, target, snapshot)
            return "history.time.paste"

        if self._run_time_edit(targets, operate):                 # un refus a déjà dit pourquoi : « collé » ne le recouvre pas
            self.statusBar().showMessage(translate("time.status.pasted"), 3000)

    # ------------------------------------------------------------------
    # Raccourcis
    # ------------------------------------------------------------------

    def _time_shortcut_handlers(self) -> dict:
        return {
            "time_add_speed_point": lambda: self._time_shortcut("add_point"),
            "time_freeze_frame": lambda: self._time_shortcut("hold"),
        }

    def _time_shortcut(self, command: str) -> None:
        """Commande clavier : le clip sélectionné, sinon le clip vidéo sous la tête de lecture."""
        clip_id = getattr(self.timeline_panel, "selected_clip_id", None) or self._clip_under_playhead()
        if clip_id is None:
            self.statusBar().showMessage(translate("time.status.no_clip"), 3000)
            return
        self.on_time_command(clip_id, command)

    def _clip_under_playhead(self) -> str | None:
        position = float(self.playhead_seconds)
        for track in self.project.tracks:
            if track.type != "video":
                continue
            for clip in track.clips:
                if clip.timeline_start <= position < clip.timeline_start + clip.duration and clip.enabled:
                    return clip.id
        return None

    # ------------------------------------------------------------------
    # Analyse du flux optique (pré-calcul annulable)
    # ------------------------------------------------------------------

    def _flow_cache_for_analysis(self):
        manager = getattr(self, "cache_manager", None)
        cache = getattr(manager, "flow", None)
        if cache is None:
            from core.flow_cache import FlowCache

            cache = FlowCache()
        return cache

    def _analysis_geometry(self) -> tuple[int, int, float]:
        """Résolution et cadence de l'export choisi dans le panneau Export : c'est ce que l'export relira. La clé d'une paire contient
        la grille d'analyse, donc une analyse faite à une autre taille n'épargnerait aucun calcul à l'export."""
        panel = getattr(self, "export_panel", None)
        if panel is not None:
            _format, preset, fps = panel.current_spec().export_parts(self.project.fps)
            width, height = preset.resolution
            return int(width), int(height), float(fps)
        return int(self.project.width), int(self.project.height), float(self.project.fps)

    def start_flow_analysis(self, targets: list[str]) -> None:
        """Calcule à l'avance les vecteurs de mouvement des clips ``targets`` (flux optique) ; annulable, progression en direct."""
        job = self._flow_job
        if job is not None and not job.snapshot().finished:
            self.statusBar().showMessage(translate("time.status.analysis_running"), 4000)
            return
        width, height, fps = self._analysis_geometry()
        plan = self.get_render_plan()
        requests = [
            item.request for item in layer_jobs(plan, width, height, fps, self._flow_preference())
            if item.layer.clip_id in targets and item.request.interpolation is TimeInterpolation.OPTICAL_FLOW
        ]
        if not requests:
            optical = any(
                find_clip(self.project, target).time_remapping.interpolation is TimeInterpolation.OPTICAL_FLOW for target in targets
            )
            self.statusBar().showMessage(
                translate("time.status.nothing_to_analyze" if optical else "time.status.not_flow"), 5000
            )
            return
        job = FlowAnalysisJob(requests, self._flow_cache_for_analysis())
        self._flow_job = job
        timer = self._flow_timer
        if timer is None:
            timer = self._flow_timer = QTimer(self)
            timer.setInterval(POLL_INTERVAL_MS)
            timer.timeout.connect(self._poll_flow_analysis)
        job.start()
        timer.start()
        self._show_flow_analysis_state(True)
        self.statusBar().showMessage(translate("time.status.analyzing", done=0, total=job.snapshot().total))

    def _poll_flow_analysis(self) -> None:
        job = self._flow_job
        if job is None:
            if self._flow_timer is not None:
                self._flow_timer.stop()
            return
        snapshot = job.snapshot()
        if not snapshot.finished:
            self.statusBar().showMessage(translate("time.status.analyzing", done=snapshot.done, total=snapshot.total))
            return
        if self._flow_timer is not None:
            self._flow_timer.stop()
        self._flow_job = None
        self._show_flow_analysis_state(False)
        if snapshot.state is AnalysisState.DONE and snapshot.report is not None:
            message = translate(
                "time.status.analysis_done",
                pairs=snapshot.report.pairs_computed + snapshot.report.pairs_cached, cached=snapshot.report.pairs_cached,
            )
        elif snapshot.state is AnalysisState.CANCELLED:
            message = translate("time.status.analysis_cancelled")
        else:
            LOGGER.warning("Analyse du flux optique : %s", snapshot.message)
            message = translate("time.status.analysis_failed", reason=snapshot.message)
        self.statusBar().showMessage(message, 8000)

    def cancel_flow_analysis(self) -> None:
        """Interrompt l'analyse en cours ; les paires déjà calculées restent dans le cache."""
        if self._flow_job is not None:
            self._flow_job.cancel()

    def _cancel_flow_analysis(self) -> None:
        """Arrêt synchrone (fermeture de l'application, changement de projet) : le fil est attendu un instant."""
        job = self._flow_job
        timer = self._flow_timer
        if timer is not None:
            timer.stop()
        if job is not None:
            job.cancel()
            job.join(3.0)
        self._flow_job = None
        self._show_flow_analysis_state(False)

    def _show_flow_analysis_state(self, running: bool) -> None:
        section = getattr(getattr(self, "properties_panel", None), "time_section", None)
        if section is not None:
            section.set_analysis_running(running)

    # ------------------------------------------------------------------
    # Bilan des images intermédiaires d'un export
    # ------------------------------------------------------------------

    def _on_preparation_reported(self, report) -> None:
        """Ce que le moteur a fabriqué pour l'export (jamais un écart silencieux) : message dans la barre d'état et le panneau."""
        if report is None or not (report.synthesized or report.degraded):
            return
        if report.degraded:
            text = translate("time.notice.degraded", count=report.degraded)
        else:
            text = translate("time.notice.prepared", count=report.synthesized, confidence=round(100 * report.mean_confidence))
        self.statusBar().showMessage(text, 12000)
        panel = getattr(self, "export_panel", None)
        if panel is not None:
            panel.set_status(text, "running")

    # ------------------------------------------------------------------
    # Calcul du flux optique : backend demandé
    # ------------------------------------------------------------------

    def _flow_preference(self) -> BackendPreference:
        try:
            return BackendPreference(self._flow_backend_request)
        except ValueError:
            return BackendPreference.AUTO

    def set_flow_backend(self, value: str) -> None:
        """Réglage « Calcul du flux optique » (Auto / Processeur / GPU), appliqué tout de suite aux rendus suivants."""
        try:
            preference = BackendPreference(str(value))
        except ValueError:
            preference = BackendPreference.AUTO
        self._flow_backend_request = preference.value
        self.export_engine.flow_preference = preference
        preview = getattr(self, "preview_engine", None)
        if preview is not None:
            preview.flow_preference = preference
            try:
                preview.cancel_all()                       # les segments en vol suivaient l'ancien réglage
            except Exception:                              # noqa: BLE001 - le réglage est pris : un arrêt raté ne l'annule pas
                LOGGER.debug("Aperçu : arrêt des segments impossible", exc_info=True)
        self._apply_settings(replace(self._settings_snapshot(), flow_backend=preference.value))

    def flow_backend_options(self) -> list[tuple[str, str, bool]]:
        """``(valeur, libellé, disponible)`` : Auto, Processeur, GPU (grisé tant qu'aucun backend GPU n'existe ici)."""
        options = []
        for preference in BackendPreference:
            try:
                select_backend(preference)
                available = True
            except BackendUnavailable:
                available = False
            options.append((preference.value, translate(f"perf.flow_backend.{preference.value}"), available))
        return options

    def flow_diagnostics_text(self) -> str:
        """Ligne de diagnostic : backend demandé et backend réellement utilisé pour le flux optique."""
        preference = self._flow_preference()
        try:
            backend = select_backend(preference)
            used = f"{backend.name} ({backend.device}, v{backend.version})"
        except BackendUnavailable as error:
            used = str(error)
        return translate("diag.flow_backend", requested=preference.value, used=used)

    # ------------------------------------------------------------------
    # Moniteur : indication discrète « aperçu simplifié »
    # ------------------------------------------------------------------

    def _interpolation_at_playhead(self) -> TimeInterpolation | None:
        """Le mode d'images intermédiaires demandé par un clip sous la tête de lecture **et réellement simplifié** dans le moniteur.

        Le moniteur temps réel échantillonne toujours (l'image la plus proche) ; l'aperçu fidèle et l'export fabriquent les images
        demandées. Un clip dont aucune image n'est intermédiaire à cette vitesse n'est pas concerné : l'échantillonnage donne alors
        exactement ce que l'export donnera.
        """
        position = float(self.playhead_seconds)
        assets = {asset.id: asset for asset in self.project.media_assets}
        for track in self.project.tracks:
            if track.type != "video":
                continue
            for clip in track.clips:
                mode = clip.time_remapping.interpolation
                if mode is TimeInterpolation.SAMPLING or not clip.is_time_remapped or clip.is_nested \
                        or clip.is_composition:
                    continue
                if not clip.timeline_start <= position < clip.timeline_start + clip.duration:
                    continue
                asset = assets.get(clip.asset_id)
                rate = float(asset.fps) if asset is not None and asset.fps > 0 else 30.0
                frames = int(asset.duration * rate) if asset is not None else 0
                plan = plan_interpolation(
                    clip.time_map, fps=float(self.project.fps), source_fps=rate, last_frame=frames - 1 if frames > 0 else None,
                    interpolation=mode,
                )
                if plan.needs_synthesis:
                    return mode
        return None

    def _schedule_preview_notice(self, *_args: object) -> None:
        """Rafraîchit l'avis du moniteur sans le recalculer à chaque déplacement de la tête de lecture (minuterie de 120 ms)."""
        timer = self._notice_timer
        if timer is None:
            timer = self._notice_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._update_preview_notice)
        timer.start()

    def _update_preview_notice(self) -> None:
        """Avis du moniteur : niveau d'aperçu réduit (mode Auto) et/ou « aperçu simplifié » d'un clip interpolé."""
        preview = getattr(self, "preview_panel", None)
        if preview is None:
            return
        parts: list[str] = []
        runtime = getattr(self, "runtime", None)
        if runtime is not None and runtime.preview.degraded:
            parts.append(translate("preview.quality_reduced", quality=runtime.preview_label()))
        mode = self._interpolation_at_playhead()
        if mode is not None:
            parts.append(translate("time.notice.simplified", mode=translate(f"time.interpolation.{mode.value}")))
        preview.set_quality_notice(" · ".join(parts) if parts else None)
