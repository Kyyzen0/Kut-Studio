"""Courbes de volume dans la timeline : une bande sous les clips des pistes audio, et le menu audio des pistes.

La bande (:class:`ui.timeline_widgets.automation_lane.AutomationLane`) s'affiche sous les clips d'une piste audio dès
que sa courbe a des points, ou quand l'utilisateur la demande (menu ⋯ de la piste). Ce choix est une préférence
d'affichage de la session : il ne va ni dans le ``.kut`` ni dans l'historique. Les gestes de la bande et du menu
(rôle de la piste, « Baisser sous… ») passent par les signaux du panneau, que la fenêtre applique.
"""

from __future__ import annotations

from PySide6.QtGui import QActionGroup

from core.audio_automation import TrackRole
from ui.i18n import translate
from ui.timeline_widgets.automation_lane import LANE_HEIGHT, AutomationLane

ROLE_ORDER = (TrackRole.VOICE, TrackRole.MUSIC, TrackRole.SFX, TrackRole.OTHER)


class AutomationTimelineMixin:
    """Mixin de ``TimelinePanel`` : bandes d'automation et menu audio des pistes."""

    def _init_automation_state(self) -> None:
        self.automation_lanes: dict[str, AutomationLane] = {}
        self._automation_choice: dict[str, bool] = {}

    # -- visibilité ----------------------------------------------------------------------------------------------------

    def automation_shown(self, track) -> bool:
        """Bande visible : choix de l'utilisateur, sinon dès que la courbe a des points (jamais repliée)."""
        if getattr(track, "type", None) != "audio" or getattr(track, "collapsed", False):
            return False
        choice = self._automation_choice.get(track.id)
        if choice is not None:
            return choice
        return not track.automation.is_empty()

    def automation_lane_height(self, track) -> int:
        return LANE_HEIGHT if self.automation_shown(track) else 0

    def set_automation_visible(self, track_id: str, visible: bool) -> None:
        """Affiche ou masque la courbe de volume d'une piste (la hauteur des pistes change : tout est replacé)."""
        self._automation_choice[track_id] = bool(visible)
        self.refresh_clip_widgets()

    def keep_automation_visible(self, track_id: str) -> None:
        """Après une édition de sa courbe, la bande d'une piste reste affichée, même vidée (sans tout replacer)."""
        self._automation_choice.setdefault(track_id, True)

    # -- bandes --------------------------------------------------------------------------------------------------------

    def _sync_automation_lanes(self) -> None:
        """Une bande par piste qui en montre une, à jour du modèle (après un ``set_project``, une annulation…)."""
        wanted = {}
        if self.project is not None:
            wanted = {track.id: track for track in self.project.tracks if self.automation_shown(track)}
        for track_id in [key for key in self.automation_lanes if key not in wanted]:
            lane = self.automation_lanes.pop(track_id)
            lane.setParent(None)
            lane.deleteLater()
        for track_id, track in wanted.items():
            lane = self.automation_lanes.get(track_id)
            if lane is None:
                lane = AutomationLane(track_id, self.timeline_grid)
                lane.point_added.connect(self.automation_point_added.emit)
                lane.point_removed.connect(self.automation_point_removed.emit)
                lane.point_updated.connect(self.automation_point_updated.emit)
                lane.point_moved.connect(self.automation_point_moved.emit)
                lane.cleared.connect(self.automation_cleared.emit)
                self.automation_lanes[track_id] = lane
                lane.show()
            lane.set_state(track.automation.points, locked=bool(track.locked))

    def _layout_automation_lanes(self) -> None:
        if self.project is None:
            return
        width = max(self.timeline_grid.width(), self.timeline_grid.minimumWidth()) - self.left_margin
        for index, track in enumerate(self.project.tracks):
            lane = self.automation_lanes.get(track.id)
            if lane is None:
                continue
            top = int(self.row_top(index)) + self.clip_height_of(track)
            lane.setGeometry(self.left_margin, top, max(1, width), LANE_HEIGHT)
            lane.raise_()
            lane.update()

    # -- menu ⋯ d'une piste audio --------------------------------------------------------------------------------------

    def extend_track_menu(self, menu, track_id: str) -> None:
        """Ajoute au menu ⋯ d'une piste audio : sa courbe de volume, son rôle, et « Baisser sous… » (ducking)."""
        if self.project is None:
            return
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None or track.type != "audio":
            return
        menu.addSeparator()
        curve = menu.addAction(translate("timeline.automation.show"))
        curve.setCheckable(True)
        curve.setChecked(self.automation_shown(track))
        curve.toggled.connect(lambda checked, tid=track_id: self.set_automation_visible(tid, checked))

        roles = menu.addMenu(translate("timeline.track.role"))
        roles.setEnabled(not track.locked)
        group = QActionGroup(roles)
        group.setExclusive(True)
        current = str(getattr(track.audio_role, "value", track.audio_role))
        for role in ROLE_ORDER:
            action = roles.addAction(translate(f"timeline.track.role.{role.value}"))
            action.setCheckable(True)
            action.setChecked(current == role.value)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, tid=track_id, r=role.value: self.track_role_changed.emit(tid, r))

        others = [item for item in self.project.tracks if item.type == "audio" and item.id != track_id]
        if not others:
            return
        ducking = menu.addMenu(translate("timeline.track.duck_under"))
        ducking.setEnabled(not track.locked)
        pairs = {(item.music_track_id, item.voice_track_id) for item in getattr(self.project, "ducking_sidechains", [])}
        for other in others:
            action = ducking.addAction(other.name or other.id)
            action.setCheckable(True)
            action.setChecked((track_id, other.id) in pairs)
            action.toggled.connect(
                lambda checked, tid=track_id, vid=other.id: self.ducking_pair_toggled.emit(tid, vid, checked))


__all__ = ["AutomationTimelineMixin"]
