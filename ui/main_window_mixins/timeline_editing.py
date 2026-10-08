"""Méthodes de ``MainWindow`` regroupées : timeline_editing."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QProgressDialog
from core.timeline_editing import (
    add_marker,
    delete_clips,
    move_clips,
    neighbor_marker,
    ripple_trim_left,
    roll_edit,
    shift_track_after,
    slide_clip,
    slip_clip,
)
from core.timeline_operations import (
    cut_clip,
    delete_clip,
    duplicate_clip,
    find_clip,
    move_clip,
    ripple_delete_clip,
    set_clip_enabled,
    trim_clip_left,
    trim_clip_right,
)
from core.scene_detection import SceneDetectionJob, cut_clip_at_scenes, scene_cut_refusal
from ui.i18n import translate


LOGGER = logging.getLogger(__name__)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class TimelineEditingMixin:
    """Mixin de ``MainWindow`` (timeline_editing)."""

    def duplicate_selected_clip(self) -> None:
        """Duplique le clip sélectionné juste après sa fin."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            new_clip = duplicate_clip(self.project, clip_id)
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                translate("dialog.clip.duplicate_failed_title"),
                translate("dialog.clip.duplicate_failed_text", error=exc),
            )
            return
        self._record_history(translate("menu.item.duplicate_clip"))
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    def _report_edit_refused(self, reason: object) -> None:
        """Dit à l'utilisateur qu'une opération est refusée (barre d'état) et la journalise.

        Un ``print`` ne se voit pas dans l'application empaquetée (sans console) : l'action semblait alors
        simplement ne rien faire. Utilisée par tous les gestionnaires d'édition de la fenêtre principale.
        """
        LOGGER.info("Opération refusée : %s", reason)
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(str(reason), 5000)

    def ripple_delete_selected_clip(self) -> None:
        """Supprime le clip sélectionné et ramène à gauche les clips suivants."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        followers = self._tracking_followers_of([clip_id])
        try:
            ripple_delete_clip(self.project, clip_id)
        except KeyError as exc:
            _main_window().QMessageBox.critical(
                self,
                translate("dialog.clip.delete_failed_title"),
                translate("dialog.clip.delete_failed_text", error=exc),
            )
            return
        except ValueError as exc:  # piste verrouillée : refus normal, pas une panne
            self._report_edit_refused(exc)
            return
        self._record_history(translate("action.ripple_delete"))
        if followers:
            self._announce_tracking_followers(followers, cut=False)
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        # Sélectionner le clip précédent sur V1 s'il existe.
        previous_clip = self._find_previous_v1_clip()
        if previous_clip is not None:
            self.timeline_panel.select_clip(previous_clip.id)
            self.on_clip_selected(previous_clip.id)
        self._mark_dirty()

    def delete_selected_clip_with_check(self) -> None:
        """Supprime la sélection. En mode ripple, les clips suivants se rapprochent."""
        ids = list(self.timeline_panel.selected_clip_ids)
        if not ids and self.timeline_panel.selected_clip_id:
            ids = [self.timeline_panel.selected_clip_id]
        if not ids:
            return
        if len(ids) == 1 and not self.timeline_panel.ripple_enabled:
            self.delete_selected_clip(ids[0])
            return
        followers = self._tracking_followers_of(ids)
        try:
            delete_clips(self.project, ids, ripple=self.timeline_panel.ripple_enabled)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.delete_selection"))
        if followers:
            self._announce_tracking_followers(followers, cut=False)
        self.timeline_panel.selected_clip_id = None
        self.timeline_panel.selected_clip_ids = set()
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def toggle_selected_clip_enabled(self) -> None:
        """Bascule l'état ``enabled`` du clip sélectionné."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        was_enabled = clip.enabled
        try:
            set_clip_enabled(self.project, clip_id, not was_enabled)
        except KeyError:
            return
        except ValueError as exc:  # piste verrouillée
            self._report_edit_refused(exc)
            return
        self._record_history(
            translate("history.clip.disable") if was_enabled else translate("history.clip.enable")
        )
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_move_clip_requested(self, clip_id: str, new_timeline_start: float) -> None:
        """Applique un déplacement demandé par la timeline."""
        try:
            move_clip(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.move"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            if self.timeline_panel.ripple_enabled:
                ripple_trim_left(self.project, clip_id, new_timeline_start)
            else:
                trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.trim_left"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            clip = None
        old_end = None if clip is None else clip.timeline_start + clip.duration
        track_id = None if clip is None else clip.track_id
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        if self.timeline_panel.ripple_enabled and old_end is not None and track_id:
            shift_track_after(
                self.project,
                track_id,
                old_end,
                new_timeline_end - old_end,
                exclude_ids={clip_id},
            )
        self._record_history(translate("history.clip.trim_right"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def cut_at_playhead(self):
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            self._report_edit_refused(translate("status.clip.cut_none"))
            return
        self.cut_selected_clip(clip_id, self.timeline_panel.playhead_seconds)

    def cut_at_scene_changes(self):
        """Découpe le clip sélectionné à ses changements de plan (détection FFmpeg sur la file d'analyse)."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            self._report_edit_refused(translate("status.clip.cut_none"))
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError as exc:
            self._report_edit_refused(exc)
            return
        asset = next((item for item in self.project.media_assets if item.id == clip.asset_id), None)
        reason = scene_cut_refusal(clip) or ("" if asset is not None else translate("status.clip.cut_none"))
        if reason:
            self._report_edit_refused(reason)
            return
        detections = getattr(self, "_scene_detections", None)
        if detections is None:
            detections = self._scene_detections = {}
        key = f"scenes:{clip_id}"
        if key in detections:
            self._show_sequence_status(translate("scenes.running"))
            return
        from core.export_engine import require_ffmpeg

        try:
            job = SceneDetectionJob(
                require_ffmpeg(), asset.path, start=clip.source_in,
                duration=clip.source_out - clip.source_in, session_id=self.runtime.session_id,
            )
        except (ImportError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        dialog = QProgressDialog(translate("scenes.progress.title"), translate("scenes.progress.cancel"), 0, 100, self)
        dialog.setWindowTitle(translate("scenes.progress.title"))
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(job.cancel)
        timer = QTimer(self)
        timer.setInterval(100)
        timer.timeout.connect(lambda: self._poll_scene_detection(key))
        detections[key] = (job, dialog, timer, clip_id)
        self.runtime.schedule_analysis(key, job.run)
        timer.start()
        dialog.show()

    def _poll_scene_detection(self, key: str) -> None:
        pending = getattr(self, "_scene_detections", {}).get(key)
        if pending is None:
            return
        job, dialog, timer, clip_id = pending
        if job.session_id != self.runtime.session_id:  # le projet a changé entre-temps : résultat périmé
            self._finish_scene_detection(key, dialog, timer)
            return
        snapshot = job.snapshot()
        dialog.setValue(int(snapshot.progress * 100))
        if snapshot.state not in {"finished", "cancelled", "failed"}:
            return
        self._finish_scene_detection(key, dialog, timer)
        if snapshot.state == "cancelled":
            self._show_sequence_status(translate("scenes.cancelled"))
        elif snapshot.state == "failed":
            self._report_edit_refused(translate("scenes.failed", error=snapshot.message))
        else:
            self._apply_scene_cuts(clip_id, snapshot.result or ())

    def _finish_scene_detection(self, key: str, dialog, timer) -> None:
        timer.stop()
        dialog.close()
        getattr(self, "_scene_detections", {}).pop(key, None)

    def _apply_scene_cuts(self, clip_id: str, times) -> None:
        if not times:
            self._show_sequence_status(translate("scenes.none"))
            return
        followers = self._tracking_followers_of([clip_id])
        try:
            created = cut_clip_at_scenes(self.project, clip_id, times)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.scene_cut"))
        if followers:
            self._announce_tracking_followers(followers, cut=True)
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self._restore_clip_selection(clip_id)
        self._show_sequence_status(translate("scenes.done", count=len(created) + 1))

    def cut_selected_clip(self, clip_id, playhead_pos):
        followers = self._tracking_followers_of([clip_id])
        try:
            cut_clip(self.project, clip_id, playhead_pos)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.cut"))
        if followers:
            self._announce_tracking_followers(followers, cut=True)
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        # Tenter de conserver la sélection : si l'ancien id existe encore
        # (clip gauche de la coupe), on le re-sélectionne, sinon on prend
        # le clip V1 actif autour du playhead.
        new_id = clip_id
        if self.timeline_panel.find_view_by_id(new_id) is None:
            view_at_playhead = next(
                (
                    v
                    for v in self.timeline_panel.clip_views
                    if v.track_id == "V1" and v.start <= playhead_pos <= v.end
                ),
                None,
            )
            new_id = view_at_playhead.id if view_at_playhead is not None else None
        if new_id is not None:
            self._restore_clip_selection(new_id)
        else:
            self.properties_panel.set_clip(None, "")
            self.timeline_panel.selected_clip_id = None

    def _tracking_followers_of(self, clip_ids) -> list[str]:
        """Clips (hors de ``clip_ids``) dont une liaison suit l'un de ces clips : à relever **avant** l'opération."""
        from core.tracking_ops import tracking_dependents

        removed = set(clip_ids)
        followers: list[str] = []
        for clip_id in clip_ids:
            for follower in tracking_dependents(self.project, clip_id):
                if follower not in removed and follower not in followers:
                    followers.append(follower)
        return followers

    def _announce_tracking_followers(self, followers, *, cut: bool) -> None:
        """Dit ce qu'il advient des clips qui suivaient le tracking du clip qu'on vient de couper ou de supprimer.

        Une coupe étend leur liaison aux deux moitiés (le mouvement continue) ; une suppression laisse les parties
        restantes. S'il reste un défaut (source introuvable, partie de leur durée que la source ne couvre pas), il
        est nommé : jamais un mouvement figé en silence.
        """
        from core.tracking_bindings import TrackingContext, link_issues
        from core.tracking_ops import find_clip_and_track

        context = TrackingContext(self.project)
        issues: dict[str, set[str]] = {}
        for follower_id in followers:
            try:
                clip, _track = find_clip_and_track(self.project, follower_id)
            except ValueError:               # TrackingError : le clip lié a lui-même été supprimé
                continue
            found = {code for link in clip.tracking.links for code in link_issues(context, clip, link)}
            if found:
                issues[follower_id] = found
        orphaned = [follower for follower, codes in issues.items() if "missing_source" in codes]
        if orphaned:
            message = translate("tracking.link.delete_orphaned", count=len(orphaned))
        elif issues:
            detail = ", ".join(translate(f"tracking.link.issue.{code}") for code in sorted(set().union(*issues.values())))
            message = translate("tracking.link.cut_issue", count=len(issues), detail=detail)
        elif cut:
            message = translate("tracking.link.cut_followed", count=len(followers))
        else:
            return
        self.statusBar().showMessage(message, 10000)

    def delete_selected_clip(self, clip_id):
        followers = self._tracking_followers_of([clip_id])
        try:
            delete_clip(self.project, clip_id)
        except KeyError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("menu.item.delete_clip"))
        if followers:
            self._announce_tracking_followers(followers, cut=False)
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_transition_selected(self, transition_id: str) -> None:
        """Affiche les réglages de la transition choisie sur la timeline."""
        transition = next(
            (item for item in self.project.transitions if item.id == transition_id), None
        )
        if transition is None:
            self.properties_panel.clear_transition()
            return
        outgoing = self.timeline_panel.find_view_by_id(transition.from_clip_id)
        incoming = self.timeline_panel.find_view_by_id(transition.to_clip_id)
        if outgoing is None or incoming is None:
            self.properties_panel.clear_transition()
            return
        track = next((item for item in self.project.tracks if item.id == outgoing.track_id), None)
        self.properties_panel.show_transition(
            transition, outgoing, incoming, track.name if track is not None else outgoing.track_id
        )

    def on_transition_type_changed(self, transition_id: str, transition_type: str) -> None:
        self._update_transition(transition_id, transition_type=transition_type)

    def on_transition_duration_changed(self, transition_id: str, duration: float) -> None:
        self._update_transition(transition_id, duration=duration)

    def _update_transition(
        self, transition_id: str, *, transition_type: str | None = None,
        duration: float | None = None,
    ) -> None:
        from core.transitions import TransitionType, update_transition

        try:
            update_transition(
                self.project,
                transition_id,
                transition_type=TransitionType(transition_type) if transition_type else None,
                duration=duration,
            )
        except (KeyError, ValueError) as error:
            self.statusBar().showMessage(translate("status.transition.edit_refused", error=error), 6000)
            self.on_transition_selected(transition_id)
            return
        self._record_history(translate("history.transition.edit"))
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.timeline_panel.select_transition(transition_id)

    def remove_selected_transition(self, transition_id: str) -> None:
        from core.transitions import remove_transition

        try:
            remove_transition(self.project, transition_id)
        except KeyError:
            return
        self._record_history(translate("history.transition.delete"))
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.properties_panel.clear_transition()
        self.statusBar().showMessage(translate("status.transition.deleted"), 3000)

    def on_slip_requested(self, clip_id: str, delta: float) -> None:
        try:
            slip_clip(self.project, clip_id, delta)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self._reload_timeline_preserving_selection()
            return
        self._record_history(translate("history.tool.slip"))
        self._reload_timeline_preserving_selection(clip_id)
        self._mark_dirty()

    def on_slide_requested(self, clip_id: str, new_start: float) -> None:
        try:
            slide_clip(self.project, clip_id, new_start)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self._reload_timeline_preserving_selection()
            return
        self._record_history(translate("history.tool.slide"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_roll_requested(self, clip_id: str, edge: str, new_time: float) -> None:
        try:
            roll_edit(self.project, clip_id, edge, new_time)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self._reload_timeline_preserving_selection()
            return
        self._record_history(translate("history.tool.roll"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_clips_move_requested(self, placements) -> None:
        try:
            move_clips(self.project, list(placements))
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self._reload_timeline_preserving_selection()
            return
        self._record_history(translate("history.clip.move_many"))
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._mark_dirty()

    def on_blade_cut_requested(self, clip_id: str, instant: float) -> None:
        self.cut_selected_clip(clip_id, instant)

    def add_marker_at(self, seconds: float) -> None:
        marker = add_marker(self.project, seconds)
        self._record_history(translate("shortcuts.command.marker_add"))
        self._reload_timeline_preserving_selection()
        self._mark_dirty()
        del marker

    def rename_marker(self, marker_id: str) -> None:
        marker = next((item for item in self.project.markers if item.id == marker_id), None)
        if marker is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            translate("menu.item.marker"),
            translate("dialog.marker.name_label"),
            text=marker.name,
        )
        if not accepted:
            return
        marker.name = name.strip()
        self._record_history(translate("history.marker.rename"))
        self._reload_timeline_preserving_selection()
        self._mark_dirty()

    def goto_marker(self, direction: int) -> None:
        marker = neighbor_marker(self.project, self.playhead_seconds, direction)
        if marker is not None:
            self.seek_to_position(marker.time_seconds)
