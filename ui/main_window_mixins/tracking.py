"""Méthodes de ``MainWindow`` : tracking 2D (panneau Suivi, viewer, analyses).

Relie le panneau (:mod:`ui.tracking_panel`), la surcouche du viewer
(:mod:`ui.tracking_overlay`) et la voie d'analyse du runtime aux opérations
pures de :mod:`core.tracking_ops`.

Règles :

- une action = une entrée d'historique ; un geste dans le viewer modifie
  le projet en direct et n'enregistre qu'au relâchement ;
- une analyse tourne hors du thread de l'interface (une par clip) ; elle
  est suivie par une minuterie (progression, trajectoire partielle) puis
  fusionnée en **une** entrée d'historique, quelle que soit sa longueur ;
- changer de projet annule les analyses en cours.
"""

from __future__ import annotations

import importlib.util
import logging
import threading

from PySide6.QtCore import QTimer

from core.tracking_panel_state import LinkRow, TrackerRow, TrackingPanelState
from ui import i18n

LOGGER = logging.getLogger(__name__)


def _tr(key: str, **values) -> str:
    return i18n.translate(key, **values)


class TrackingMixin:
    """Mixin de ``MainWindow`` (tracking 2D)."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_tracking(self) -> None:
        from core.tracking_engine import TrackingCache

        self._tracking_jobs: dict = {}
        self._tracking_status: dict[str, str] = {}
        self._tracking_selection: dict[str, list[str]] = {}
        self._tracking_followups: dict[str, list] = {}
        self._tracking_show_paths = True
        self._tracking_dragging = False
        self._tracking_cache = TrackingCache()
        self._tracking_available = importlib.util.find_spec("numpy") is not None
        self._tracking_poll = QTimer(self)
        self._tracking_poll.setInterval(100)
        self._tracking_poll.timeout.connect(self._poll_tracking_jobs)
        panel = self.tracking_panel
        panel.tracker_selection_changed.connect(self._on_tracker_selection)
        panel.add_tracker_requested.connect(lambda: self.add_tracker_at_playhead())
        panel.remove_trackers_requested.connect(self._remove_trackers)
        panel.rename_tracker_requested.connect(self._rename_tracker)
        panel.tracker_visibility_changed.connect(self._set_tracker_visible)
        panel.reset_requested.connect(self._reset_trackers)
        panel.track_requested.connect(self.track_selected)
        panel.stop_requested.connect(self.stop_tracking)
        panel.settings_changed.connect(self._on_tracker_settings)
        panel.show_paths_changed.connect(self._set_tracking_paths_visible)
        panel.link_requested.connect(self._link_selected_trackers)
        panel.link_enabled_changed.connect(self._set_link_enabled)
        panel.link_bake_requested.connect(self._bake_link)
        panel.link_remove_requested.connect(self._remove_link)
        panel.stabilization_toggled.connect(self._toggle_stabilization)
        panel.stabilization_changed.connect(self._on_stabilization_changed)
        panel.auto_stabilize_requested.connect(self.auto_stabilize_selected)
        overlay = self.preview_panel.tracking_overlay
        overlay.tracker_clicked.connect(self._on_overlay_tracker_clicked)
        overlay.point_dragged.connect(self._on_overlay_point_dragged)
        overlay.zone_dragged.connect(self._on_overlay_zone_dragged)
        overlay.drag_finished.connect(self._on_overlay_drag_finished)
        overlay.add_requested.connect(lambda x, y: self.add_tracker_at_playhead(x, y))
        self.properties_panel.inspector_tab_changed.connect(lambda _index: self._refresh_tracking_ui())
        self._refresh_tracking_ui()

    @property
    def tracking_panel(self):
        return self.properties_panel.tracking_group

    def show_tracking_panel(self) -> None:
        """Ouvre l'onglet Suivi de l'inspecteur."""
        from ui.properties_panel_mixins.construction import TRACKING_TAB

        self.properties_panel._select_inspector_tab(TRACKING_TAB)

    # ------------------------------------------------------------------
    # Contexte
    # ------------------------------------------------------------------

    def _tracking_selected_clip(self):
        """``(clip, type de piste)`` du clip sélectionné, ou ``(None, "")``."""
        from core.timeline_operations import find_clip

        view = getattr(self.properties_panel, "selected_clip", None)
        clip_id = getattr(view, "id", None)
        track_type = getattr(view, "track_type", "") or ""
        if not clip_id or track_type not in ("video", "graphics"):
            return None, ""
        try:
            return find_clip(self.project, clip_id), track_type
        except KeyError:
            return None, ""

    def _tracking_mode_active(self) -> bool:
        """Onglet Suivi ouvert sur un clip vidéo : le viewer montre les trackers."""
        from ui.properties_panel_mixins.construction import TRACKING_TAB

        if getattr(self.properties_panel, "_active_inspector_tab", 0) != TRACKING_TAB:
            return False
        clip, track_type = self._tracking_selected_clip()
        return clip is not None and track_type == "video" and not clip.sequence_id

    def _selected_tracker_ids(self, clip) -> list[str]:
        from core.tracking_ops import tracking_of

        trackers = tracking_of(clip).trackers
        known = {t.id for t in trackers}
        selected = [t for t in self._tracking_selection.get(clip.id, []) if t in known]
        if not selected and trackers:
            selected = [trackers[0].id]
        return selected

    def _tracking_operation(self, label_key: str, operation, *, refresh: bool = True):
        """Exécute une opération puis enregistre une entrée d'historique."""
        from core.tracking_ops import TrackingError

        self._finalize_pending_edit_sessions()
        try:
            result = operation()
        except (TrackingError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 6000)
            return None
        self._commit_layer_edit(_tr(label_key))
        if refresh:
            self._refresh_tracking_ui()
        return result

    # ------------------------------------------------------------------
    # Rafraîchissements
    # ------------------------------------------------------------------

    def _refresh_tracking_ui(self) -> None:
        if not hasattr(self, "_tracking_jobs"):
            return
        if not getattr(self, "is_playing", False):
            self.tracking_panel.set_state(self._tracking_panel_state())
        self._refresh_tracking_overlay()

    def _tracking_panel_state(self) -> TrackingPanelState:
        from core.tracking_ops import TrackingError, link_targets, stabilization_report, tracking_of

        clip, track_type = self._tracking_selected_clip()
        if clip is None:
            return {}
        state: TrackingPanelState = {"kind": track_type, "available": self._tracking_available}
        if not self._tracking_available:
            state["message"] = _tr("tracking.unavailable")
        tracking = tracking_of(clip)
        state["links"] = [self._describe_link(clip, link) for link in tracking.links]
        if track_type != "video" or clip.sequence_id:
            return state
        try:
            from core.tracking_ops import trackable_clip

            trackable_clip(self.project, clip.id)
        except TrackingError as exc:
            state["kind"] = ""
            state["message"] = str(exc)
            return state
        selected = self._selected_tracker_ids(clip)
        trackers: list[TrackerRow] = []
        for tracker in tracking.trackers:
            trackers.append({
                "id": tracker.id, "name": tracker.name, "color": tracker.color,
                "visible": tracker.visible, "summary": self._tracker_summary(tracker),
                "health": self._tracker_health(tracker),
            })
        state["trackers"] = trackers
        state["selected"] = selected
        primary = tracking.tracker(selected[0]) if selected else None
        if primary is not None:
            state["primary"] = {
                "summary": self._tracker_summary(primary), "settings": primary.settings.to_dict(),
            }
        job = self._tracking_jobs.get(clip.id)
        state["busy"] = job is not None
        if job is not None:
            snapshot = job.snapshot()
            state["progress"] = snapshot.progress
            state["status"] = _tr(
                "tracking.state.running" if snapshot.state == "running" else "tracking.state.queued",
                index=snapshot.current_index, percent=int(round(snapshot.progress * 100)),
            )
        else:
            state["status"] = self._tracking_status.get(clip.id, "")
        state["show_paths"] = self._tracking_show_paths
        state["analyzed"] = any(
            len(t.data.valid_indices()) >= 2 for t in tracking.trackers if t.id in selected
        )
        state["targets"] = self._tracking_targets(clip, link_targets(self.project, clip.id))
        stab = tracking.stabilization
        if stab is not None:
            report = stabilization_report(self.project, clip.id)
            info, warning = "", False
            if stab.enabled and not report["active"]:
                info, warning = _tr("tracking.stab.no_data"), True
            elif report.get("message"):
                info, warning = _tr(report["message"]), True
            elif report["zoom"] > 1.0005:
                percent = int(round((report["zoom"] - 1.0) * 100))
                info, warning = _tr("tracking.stab.zoom", percent=percent), percent >= 10
            elif report.get("crop"):
                x0, y0, x1, y1 = report["crop"]
                fit_area = max(1.0, self.project.width * self.project.height)
                percent = int(round(100 * (x1 - x0) * (y1 - y0) / fit_area))
                info = _tr("tracking.stab.crop", percent=percent)
            names = ", ".join(t.name for t in tracking.trackers if t.id in stab.tracker_ids)
            info = (info + "\n" if info else "") + _tr("tracking.stab.uses", names=names)
            state["stabilization"] = {**stab.to_dict(), "info": info, "warning": warning}
        return state

    @staticmethod
    def _tracker_health(tracker) -> str:
        """État d'ensemble des mesures d'un tracker : ``lost`` / ``uncertain`` / ``good``, ou ``""`` s'il n'est pas analysé."""
        from core.tracking_model import SampleStatus

        counts = tracker.data.counts()
        if counts[SampleStatus.LOST]:
            return "lost"
        if counts[SampleStatus.UNCERTAIN]:
            return "uncertain"
        if counts[SampleStatus.TRACKED] + counts[SampleStatus.MANUAL] > 1:
            return "good"
        return ""

    @staticmethod
    def _tracker_summary(tracker) -> str:
        from core.tracking_model import SampleStatus

        counts = tracker.data.counts()
        frames = counts[SampleStatus.TRACKED] + counts[SampleStatus.UNCERTAIN] + counts[SampleStatus.MANUAL]
        if frames <= 1 and counts[SampleStatus.LOST] == 0:
            return _tr("tracking.summary.empty")
        return _tr(
            "tracking.summary", frames=frames, uncertain=counts[SampleStatus.UNCERTAIN] + counts[SampleStatus.LOST],
            manual=counts[SampleStatus.MANUAL],
        )

    def _tracking_targets(self, clip, entries) -> list:
        from core.tracking_model import TrackTarget

        result = []
        for entry in entries:
            for kind, mask_id in entry["targets"]:
                spec = {"clip_id": entry["clip_id"], "target": kind, "mask_id": mask_id}
                label = entry["label"] if entry["clip_id"] != clip.id else _tr("tracking.target.this_clip")
                if kind == TrackTarget.TRANSFORM:
                    text = _tr("tracking.target.transform", label=label)
                elif kind == TrackTarget.ANCHOR:
                    text = _tr("tracking.target.anchor")
                else:
                    text = _tr("tracking.target.mask", label=label, mask=entry["masks"].get(mask_id, mask_id))
                result.append((text, spec))
        return result

    def _describe_link(self, clip, link) -> LinkRow:
        from core.timeline_operations import find_clip
        from core.tracking_model import SELF_CLIP, TrackTarget

        try:
            source = clip if link.source_clip_id == SELF_CLIP else find_clip(self.project, link.source_clip_id)
        except KeyError:
            source = None
        tracking = getattr(source, "tracking", None)
        names = [
            (tracking.tracker(t).name if tracking is not None and tracking.tracker(t) is not None else "?")
            for t in link.tracker_ids
        ]
        if link.target == TrackTarget.MASK:
            mask = clip.compositing.mask_by_id(link.mask_id) if clip.compositing is not None else None
            target = _tr("tracking.target.mask", label="", mask=getattr(mask, "name", "") or link.mask_id).strip(" ·")
        elif link.target == TrackTarget.ANCHOR:
            target = _tr("tracking.target.anchor")
        else:
            target = _tr("tracking.component.position")
            extras = [k for k, on in (("rotation", link.rotation), ("scale", link.scale)) if on]
            if extras:
                target += " + " + " + ".join(_tr(f"tracking.component.{k}") for k in extras)
        source_label = getattr(source, "label", "") or ("?" if source is None else source.id)
        label = _tr("tracking.link.describe", target=target, trackers=", ".join(names))
        if source is not None and source is not clip:
            label += f" ({source_label})"
        warning = "" if source is not None and "?" not in names else "?"
        if source is not None:
            from core.tracking_bindings import TrackingContext, link_issues

            issues = link_issues(TrackingContext(self.project), clip, link)
            if issues:
                warning = ", ".join(_tr(f"tracking.link.issue.{code}") for code in issues)
        return {"id": link.id, "label": label, "enabled": link.enabled, "warning": warning}

    def _refresh_tracking_overlay(self) -> None:
        from core.tracking_model import SampleStatus
        from ui.tracking_overlay import OverlayTracker, decimate

        overlay = self.preview_panel.tracking_overlay
        if not self._tracking_mode_active():
            overlay.set_state(active=False, mapping=overlay.mapping, trackers=[], show_paths=self._tracking_show_paths)
            return
        clip, _kind = self._tracking_selected_clip()
        t = float(self.playhead_seconds)
        local = t - clip.timeline_start
        if not (-1e-9 <= local <= clip.duration + 1e-9):
            overlay.set_state(active=False, mapping=overlay.mapping, trackers=[], show_paths=self._tracking_show_paths)
            return
        mapping = self._tracking_media_mapping(clip, local)
        if mapping is None:
            overlay.set_state(active=False, mapping=overlay.mapping, trackers=[], show_paths=self._tracking_show_paths)
            return
        from core.tracking_motion import clip_source_indices
        from core.tracking_ops import media_of, media_rate, source_index_at, tracking_of

        asset = media_of(self.project, clip)
        rate = media_rate(asset)
        index = source_index_at(clip, rate, t)
        selected = set(self._selected_tracker_ids(clip))
        job = self._tracking_jobs.get(clip.id)
        partial = job.snapshot().partial if job is not None else {}
        first, last = clip_source_indices(clip, rate)
        items = []
        for tracker in tracking_of(clip).trackers:
            if not tracker.visible:
                continue
            data = tracker.data
            if partial.get(tracker.id):
                data = data.with_samples(partial[tracker.id])
            fx, fy = data.scaled_to(asset.width, asset.height)
            sample = data.sample(index)
            if sample.status is not SampleStatus.EMPTY:
                point, status = (sample.x * fx, sample.y * fy), sample.status
            else:
                position = data.position_at_index(index)
                point = None if position is None else (position[0] * fx, position[1] * fy)
                status = SampleStatus.EMPTY
            path = [
                (s.x * fx, s.y * fy, int(s.status)) for i, s in data.samples()
                if first <= i <= last and s.status is not SampleStatus.EMPTY
            ]
            items.append(OverlayTracker(
                id=tracker.id, name=tracker.name, color=tracker.color, selected=tracker.id in selected,
                point=point, status=status,
                pattern=(tracker.settings.pattern_width, tracker.settings.pattern_height),
                search=(tracker.settings.search_width, tracker.settings.search_height),
                path=decimate(path), show_path=tracker.show_path,
            ))
        overlay.set_state(active=True, mapping=mapping, trackers=items, show_paths=self._tracking_show_paths)
        # Les poignées de transform du clip céderaient la place aux trackers.
        self.preview_panel.overlay.set_selection(None)

    def _tracking_media_mapping(self, clip, local: float):
        """Pixels du média → cadre, à ``local`` (placement + transform + stabilisation)."""
        from core.mograph_scene import mat_mul
        from core.tracking_bindings import TrackingContext

        context = TrackingContext(self.project)
        fit = context.fit(clip)
        if fit is None:
            return None
        return mat_mul(context.layer_matrix(clip, local), fit.matrix)

    # ------------------------------------------------------------------
    # Trackers
    # ------------------------------------------------------------------

    def add_tracker_at_playhead(self, x: float | None = None, y: float | None = None) -> None:
        from core.tracking_ops import add_tracker

        clip, kind = self._tracking_selected_clip()
        if clip is None or kind != "video":
            self.statusBar().showMessage(_tr("tracking.no_clip"), 5000)
            return
        if not self._tracking_mode_active():
            self.show_tracking_panel()
        tracker = self._tracking_operation(
            "tracking.history.add",
            lambda: add_tracker(self.project, clip.id, timeline_time=float(self.playhead_seconds), x=x, y=y),
            refresh=False,
        )
        if tracker is not None:
            self._tracking_selection[clip.id] = [tracker.id]
        self._refresh_tracking_ui()

    def _on_tracker_selection(self, ids: list) -> None:
        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_selection[clip.id] = list(ids)
            self._refresh_tracking_ui()

    def _on_overlay_tracker_clicked(self, tracker_id: str) -> None:
        clip, _kind = self._tracking_selected_clip()
        if clip is not None and self._tracking_selection.get(clip.id) != [tracker_id]:
            self._tracking_selection[clip.id] = [tracker_id]
            self.tracking_panel.set_state(self._tracking_panel_state())

    def _remove_trackers(self, ids: list) -> None:
        from core.tracking_ops import remove_tracker

        clip, _kind = self._tracking_selected_clip()
        if clip is None or not ids:
            return

        def operation():
            for tracker_id in ids:
                remove_tracker(self.project, clip.id, tracker_id)

        self._tracking_operation("tracking.history.remove", operation)

    def _rename_tracker(self, tracker_id: str, name: str) -> None:
        from core.tracking_ops import update_tracker

        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_operation(
                "tracking.history.rename", lambda: update_tracker(self.project, clip.id, tracker_id, name=name)
            )

    def _set_tracker_visible(self, tracker_id: str, visible: bool) -> None:
        from core.tracking_ops import update_tracker

        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_operation(
                "tracking.history.visibility",
                lambda: update_tracker(self.project, clip.id, tracker_id, visible=visible),
            )

    def _reset_trackers(self, mode: str) -> None:
        from core.tracking_ops import reset_tracker

        clip, _kind = self._tracking_selected_clip()
        if clip is None:
            return
        direction = {"all": 0, "after": 1, "before": -1}.get(mode, 0)
        ids = self._selected_tracker_ids(clip)

        def operation():
            for tracker_id in ids:
                reset_tracker(
                    self.project, clip.id, tracker_id, timeline_time=float(self.playhead_seconds), direction=direction,
                )

        self._tracking_operation("tracking.history.reset", operation)

    def _on_tracker_settings(self, changes: dict) -> None:
        from core.tracking_ops import set_tracker_settings

        clip, _kind = self._tracking_selected_clip()
        if clip is None or not changes:
            return
        ids = self._selected_tracker_ids(clip)

        def operation():
            for tracker_id in ids:
                set_tracker_settings(self.project, clip.id, tracker_id, **changes)

        self._tracking_operation("tracking.history.settings", operation)

    def _set_tracking_paths_visible(self, visible: bool) -> None:
        self._tracking_show_paths = bool(visible)
        self._refresh_tracking_overlay()

    # ------------------------------------------------------------------
    # Viewer : corrections directes
    # ------------------------------------------------------------------

    def _begin_tracking_drag(self) -> None:
        if not self._tracking_dragging:
            self._finalize_pending_edit_sessions()
            self._tracking_dragging = True

    def _on_overlay_point_dragged(self, tracker_id: str, x: float, y: float) -> None:
        from core.tracking_ops import TrackingError, set_tracker_position

        clip, _kind = self._tracking_selected_clip()
        if clip is None or clip.id in self._tracking_jobs:
            return
        self._begin_tracking_drag()
        try:
            set_tracker_position(self.project, clip.id, tracker_id, float(self.playhead_seconds), x, y)
        except TrackingError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self._after_tracking_live_change(clip.id)

    def _on_overlay_zone_dragged(self, tracker_id: str, kind: str, width: float, height: float) -> None:
        from core.tracking_ops import TrackingError, set_tracker_settings

        clip, _kind = self._tracking_selected_clip()
        if clip is None:
            return
        self._begin_tracking_drag()
        changes = {f"{kind}_width": width, f"{kind}_height": height}
        try:
            set_tracker_settings(self.project, clip.id, tracker_id, **changes)
        except TrackingError:
            return
        self._refresh_tracking_overlay()

    def _after_tracking_live_change(self, clip_id: str) -> None:
        """Retour immédiat d'un glisser (cibles liées comprises), sans historique."""
        self._mark_dirty()
        self._refresh_tracking_overlay()
        try:
            self._invalidate_preview_for_clip(clip_id)
        except Exception:
            LOGGER.debug(
                "Invalidation de l'aperçu en échec pendant le glisser du tracking sur le clip %s : le moniteur peut rester sur l'ancienne image",
                clip_id, exc_info=True,
            )
        self._schedule_viewer_graphics()

    def _on_overlay_drag_finished(self, _tracker_id: str, kind: str) -> None:
        self._tracking_dragging = False
        self._commit_layer_edit(_tr("tracking.history.move" if kind == "point" else "tracking.history.zone"))
        self._refresh_tracking_ui()

    # ------------------------------------------------------------------
    # Analyse
    # ------------------------------------------------------------------

    def track_selected(self, direction: int, *, tracker_ids=None, clip_id: str | None = None) -> bool:
        """Lance l'analyse des trackers sélectionnés depuis la tête de lecture."""
        from core.timeline_operations import find_clip
        from core.tracking_engine import TrackingJob
        from core.tracking_ops import TrackingError, analysis_request, media_of

        if clip_id is not None:
            try:
                clip, kind = find_clip(self.project, clip_id), "video"
            except KeyError:
                return False
        else:
            clip, kind = self._tracking_selected_clip()
        if clip is None or kind != "video" or clip.id in self._tracking_jobs:
            return False
        if not self._tracking_available:
            self.statusBar().showMessage(_tr("tracking.unavailable"), 6000)
            return False
        self._finalize_pending_edit_sessions()
        ids = list(tracker_ids) if tracker_ids else self._selected_tracker_ids(clip)
        proxy_path = ""
        if self.tracking_panel.use_proxy.isChecked():
            asset = media_of(self.project, clip)
            resolved = self.proxies.resolve(asset.path, divisor=2) if asset is not None else ""
            if resolved and asset is not None and resolved != asset.path:
                proxy_path = resolved
        try:
            request = analysis_request(
                self.project, clip.id, ids, timeline_time=float(self.playhead_seconds),
                direction=1 if direction >= 0 else -1, proxy_path=proxy_path,
            )
        except TrackingError as exc:
            self.statusBar().showMessage(str(exc), 6000)
            self._tracking_status[clip.id] = str(exc)
            self._refresh_tracking_ui()
            return False
        job = TrackingJob(request, cache=self._tracking_cache)
        job.session_id = self.runtime.session_id  # type: ignore[attr-defined]
        self._tracking_jobs[clip.id] = job
        self.runtime.schedule_analysis(f"tracking:{clip.id}", job.run)
        self._tracking_poll.start()
        self._refresh_tracking_ui()
        return True

    def stop_tracking(self) -> None:
        clip, _kind = self._tracking_selected_clip()
        job = self._tracking_jobs.get(clip.id) if clip is not None else None
        if job is not None:
            job.cancel()
            self._tracking_followups.pop(clip.id, None)

    def _cancel_tracking_jobs(self) -> None:
        """Projet fermé ou remplacé : les analyses en cours sont abandonnées."""
        for job in list(getattr(self, "_tracking_jobs", {}).values()):
            job.cancel()
        if hasattr(self, "_tracking_jobs"):
            self._tracking_jobs.clear()
            self._tracking_followups.clear()
            self._tracking_status.clear()

    def _poll_tracking_jobs(self) -> None:
        finished = []
        for clip_id, job in list(self._tracking_jobs.items()):
            snapshot = job.snapshot()
            if snapshot.state in ("queued", "running"):
                continue
            finished.append((clip_id, job, snapshot))
        for clip_id, job, snapshot in finished:
            self._tracking_jobs.pop(clip_id, None)
            if getattr(job, "session_id", None) != self.runtime.session_id:
                continue
            self._finish_tracking_job(clip_id, job, snapshot)
        if not self._tracking_jobs:
            self._tracking_poll.stop()
        if not finished and self._tracking_mode_active():
            # Progression et trajectoire partielle (aucune écriture dans le projet).
            self.tracking_panel.set_state(self._tracking_panel_state())
            self._refresh_tracking_overlay()

    def _finish_tracking_job(self, clip_id: str, job, snapshot) -> None:
        from core.tracking_ops import apply_tracking_result

        result = snapshot.result
        status = self._describe_result(result)
        self._tracking_status[clip_id] = status
        written = apply_tracking_result(self.project, result) if result is not None else {}
        if written:
            names = ", ".join(t.name for t in job.request.trackers if t.id in written)
            direction = _tr("tracking.direction.forward" if job.request.direction > 0 else "tracking.direction.backward")
            self._commit_layer_edit(_tr("tracking.history.analysis", direction=direction, trackers=names))
        self.statusBar().showMessage(status, 8000)
        followups = self._tracking_followups.get(clip_id)
        if followups and result is not None and result.state == "finished":
            self._run_tracking_followup(clip_id)
        else:
            self._tracking_followups.pop(clip_id, None)
        self._refresh_tracking_ui()

    def _describe_result(self, result) -> str:
        if result is None:
            return ""
        if result.state == "failed":
            kind, _, detail = (result.message or "").partition(":")
            text = _tr(f"tracking.error.{kind}") if kind in ("offline", "read") else kind
            return _tr("tracking.state.failed", detail=f"{text} — {detail}".strip(" —"))
        if result.state == "cancelled":
            return _tr("tracking.state.cancelled")
        details = []
        for outcome in result.outcomes.values():
            index = outcome.stop_index if outcome.stop_index is not None else outcome.last_index
            details.append(_tr(f"tracking.reason.{outcome.reason}", index=index if index is not None else "—"))
        return _tr("tracking.state.finished", detail="; ".join(dict.fromkeys(details)))

    # ------------------------------------------------------------------
    # Liaisons
    # ------------------------------------------------------------------

    def _link_selected_trackers(self, spec: dict, bake: bool) -> None:
        from core.tracking_ops import add_link, bake_link

        clip, _kind = self._tracking_selected_clip()
        if clip is None or not spec:
            return
        ids = self._selected_tracker_ids(clip)
        t = float(self.playhead_seconds)

        def operation():
            link = add_link(
                self.project, spec["clip_id"], clip.id, ids, target=spec["target"],
                mask_id=spec.get("mask_id", ""), position=bool(spec.get("position", True)),
                rotation=bool(spec.get("rotation")), scale=bool(spec.get("scale")), timeline_time=t,
            )
            if bake:
                bake_link(self.project, spec["clip_id"], link.id)
            return link

        self._tracking_operation("tracking.history.bake" if bake else "tracking.history.link", operation)

    def _set_link_enabled(self, link_id: str, enabled: bool) -> None:
        from core.tracking_ops import set_link_enabled

        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_operation(
                "tracking.history.link_toggle", lambda: set_link_enabled(self.project, clip.id, link_id, enabled)
            )

    def _bake_link(self, link_id: str) -> None:
        from core.tracking_ops import bake_link

        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_operation("tracking.history.bake", lambda: bake_link(self.project, clip.id, link_id))

    def _remove_link(self, link_id: str) -> None:
        from core.tracking_ops import remove_link

        clip, _kind = self._tracking_selected_clip()
        if clip is not None:
            self._tracking_operation("tracking.history.unlink", lambda: remove_link(self.project, clip.id, link_id))

    # ------------------------------------------------------------------
    # Stabilisation
    # ------------------------------------------------------------------

    def _toggle_stabilization(self, enabled: bool) -> None:
        from core.tracking_model import StabilizationMode
        from core.tracking_ops import set_stabilization, stabilization_reference, tracking_of

        clip, _kind = self._tracking_selected_clip()
        if clip is None:
            return
        tracking = tracking_of(clip)

        def operation():
            if not enabled:
                return set_stabilization(self.project, clip.id, enabled=False)
            analyzed = [t.id for t in tracking.trackers if len(t.data.valid_indices()) >= 2]
            chosen = [t for t in self._selected_tracker_ids(clip) if t in analyzed] or analyzed[:2]
            fields = {"enabled": True, "tracker_ids": tuple(chosen)}
            if tracking.stabilization is None:
                fields["mode"] = (
                    StabilizationMode.POSITION if len(chosen) < 2 else StabilizationMode.POSITION_ROTATION
                )
                fields["reference_index"] = stabilization_reference(
                    self.project, clip.id, float(self.playhead_seconds)
                )
            return set_stabilization(self.project, clip.id, **fields)

        self._tracking_operation("tracking.history.stabilization", operation)

    def _on_stabilization_changed(self, changes: dict) -> None:
        from core.tracking_ops import set_stabilization

        clip, _kind = self._tracking_selected_clip()
        if clip is not None and changes:
            self._tracking_operation(
                "tracking.history.stabilization", lambda: set_stabilization(self.project, clip.id, **changes)
            )

    def auto_stabilize_selected(self) -> None:
        """Deux trackers sur des détails contrastés, analysés dans les deux sens, puis stabilisation."""
        from core.tracking_frames import FrameReader, analysis_geometry
        from core.tracking_ops import media_of, media_rate, source_index_at

        clip, kind = self._tracking_selected_clip()
        if clip is None or kind != "video" or clip.id in self._tracking_jobs or not self._tracking_available:
            return
        asset = media_of(self.project, clip)
        if asset is None:
            return
        index = source_index_at(clip, media_rate(asset), float(self.playhead_seconds))
        geometry = analysis_geometry(asset.width, asset.height)
        holder: dict = {}

        def work(_token) -> None:
            try:
                from core.tracking_match import suggest_features

                frame = FrameReader(asset.path, geometry, media_rate(asset)).read(index)
                # Quatre points : l'ajustement robuste écarte celui qui bougerait
                # seul (eau, passant) ; deux suffiraient sur un décor parfaitement rigide.
                points = suggest_features(frame, 4, min_distance=0.18) if frame is not None else []
                holder["points"] = [geometry.to_media(x, y) for x, y in points]
            except Exception as exc:
                holder["error"] = str(exc)
            holder["done"] = True

        thread = threading.Thread(target=work, args=(None,), name="kut-features", daemon=True)
        thread.start()
        timer = QTimer(self)
        timer.setInterval(50)

        def check() -> None:
            if not holder.get("done"):
                return
            timer.stop()
            timer.deleteLater()
            self._auto_stabilize_with(clip.id, holder.get("points") or [], holder.get("error", ""))

        timer.timeout.connect(check)
        timer.start()

    def _auto_stabilize_with(self, clip_id: str, points: list, error: str) -> None:
        from core.tracking_ops import add_tracker

        clip, _kind = self._tracking_selected_clip()
        if clip is None or clip.id != clip_id:
            return
        if error or not points:
            self.statusBar().showMessage(error or _tr("tracking.reason.flat", index="—"), 6000)
            return
        t = float(self.playhead_seconds)

        def operation():
            return [
                add_tracker(self.project, clip_id, timeline_time=t, x=x, y=y, name=f"Stab {chr(65 + i)}")
                for i, (x, y) in enumerate(points)
            ]

        trackers = self._tracking_operation("tracking.history.add", operation, refresh=False)
        if not trackers:
            return
        ids = [tracker.id for tracker in trackers]
        self._tracking_selection[clip_id] = ids
        self._tracking_followups[clip_id] = [("track", -1, ids), ("stabilize", ids)]
        self.track_selected(1, tracker_ids=ids, clip_id=clip_id)

    def _run_tracking_followup(self, clip_id: str) -> None:
        from core.tracking_model import BorderMode, StabilizationMode
        from core.tracking_ops import TrackingError, set_stabilization, stabilization_reference

        queue = self._tracking_followups.get(clip_id) or []
        if not queue:
            return
        step = queue.pop(0)
        if not queue:
            self._tracking_followups.pop(clip_id, None)
        if step[0] == "track":
            self.track_selected(step[1], tracker_ids=step[2], clip_id=clip_id)
            return
        if step[0] == "stabilize":
            ids = tuple(step[1])
            mode = StabilizationMode.POSITION_ROTATION if len(ids) >= 2 else StabilizationMode.POSITION
            try:
                reference = stabilization_reference(self.project, clip_id, float(self.playhead_seconds))
                set_stabilization(
                    self.project, clip_id, enabled=True, tracker_ids=ids, mode=mode,
                    borders=BorderMode.ZOOM, reference_index=reference,
                )
            except TrackingError as exc:
                self.statusBar().showMessage(str(exc), 6000)
                return
            self._commit_layer_edit(_tr("tracking.history.stabilization"))


__all__ = ["TrackingMixin"]
