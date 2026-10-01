"""Géométrie des pistes, en-têtes, culling des clips et projection du modèle."""

from __future__ import annotations



from core.timeline_view_model import (
    TimelineClipView,
)
from ui.i18n import translate
from ui.timeline_widgets.clip_widget import ClipWidget
from ui.timeline_widgets.common import (
    _COLLAPSED_HEIGHT,
    _CONTENT_TOP,
    _HEIGHTS,
    _TRACK_TYPE_LABELS,
    _current_palette,
)
from ui.timeline_widgets.track_header import TrackRowHeader
from ui.timeline_widgets.transition_marker import TransitionMarkerWidget
from ui.timeline_widgets.common import _COLLAPSED_HEIGHT, _CONTENT_TOP, _HEIGHTS, _TRACK_TYPE_LABELS, _current_palette
from ui.timeline_widgets.clip_widget import ClipWidget
from ui.timeline_widgets.track_header import TrackRowHeader
from ui.timeline_widgets.transition_marker import TransitionMarkerWidget

class LayoutMixin:
    """Mixin de ``TimelinePanel`` : géométrie des pistes, en-têtes, culling des clips et projection du modèle."""

    def set_culling_overscan(self, pixels: int) -> None:
        """Règle la marge de montage des clips autour de la zone visible.

        Appelé quand le profil de performance change. Une marge plus
        petite monte moins de widgets sur une petite machine.
        """
        pixels = max(0, int(pixels))
        if pixels == self._overscan_px:
            return
        self._overscan_px = pixels
        if self._sync_mounted_clips():
            self._layout_children()

    @property
    def mounted_clip_count(self) -> int:
        """Nombre de clips réellement instanciés, pas le nombre du projet."""
        return len(self.clip_widgets)

    @staticmethod
    def format_time(seconds):
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _refresh_track_metadata(self) -> None:
        if self.project is None:
            self.track_names: list[str] = []
            self.track_labels: list[str] = []
            return
        self.track_names = [track.name for track in self.project.tracks]
        self.track_labels = [
            _TRACK_TYPE_LABELS.get(track.type, track.type)
            for track in self.project.tracks
        ]

    def refresh_headers(self) -> None:
        """Recrée les en-têtes depuis le projet, sans toucher à la sélection.

        Le cache de signature est oublié : un bouton qui vient d'être
        basculé alors que le modèle a refusé le changement (piste
        verrouillée) retrouve ainsi l'état réel.
        """
        self._cached_header_signature = None
        self.refresh_clip_widgets()

    def refresh_clip_widgets(self):
        """Met à jour en-têtes et clips visibles, puis repositionne.

        Les en-têtes ne sont recréés que si une piste a changé (nom,
        verrou, ordre). Les clips hors de la fenêtre visible ne sont
        pas des widgets : un projet long ne monte pas un ``QWidget``
        par clip. On pourra retirer ce filtrage le jour où la timeline
        sera dessinée dans un seul ``paintEvent`` plutôt qu'avec un
        widget par clip.
        """
        count = len(self.clip_views)
        self.clip_count_label.setText(
            f"{count} clip" if count == 1 else f"{count} clips"
        )
        self._update_scroll_extent()
        self._configure_grid()
        signature = self._header_signature()
        if signature != self._cached_header_signature:
            self._rebuild_track_headers()
            self._cached_header_signature = signature
        self._sync_mounted_clips(refresh_views=True)
        self._layout_children()
        self._sync_transition_widgets()

    def _header_signature(self) -> tuple:
        if self.project is None:
            return tuple()
        return tuple(
            (
                index,
                track.id,
                track.name,
                track.type,
                bool(track.locked),
                bool(track.visible),
                bool(track.muted),
                bool(getattr(track, "solo", False)),
                bool(getattr(track, "armed", False)),
                getattr(track, "height_mode", "normal"),
                bool(getattr(track, "collapsed", False)),
            )
            for index, track in enumerate(self.project.tracks)
        )

    def _configure_grid(self) -> None:
        track_count = max(len(self.project.tracks) if self.project else 1, 1)
        rows_height = self.rows_span()
        self.timeline_grid.setMinimumHeight(int(rows_height))
        lanes = []
        if self.project is not None:
            lanes = [
                (self.row_top(index), self.row_height_of(track))
                for index, track in enumerate(self.project.tracks)
            ]
        self.timeline_grid.configure(
            track_count=track_count,
            track_height=self.track_height,
            track_gap=self.track_gap,
            ruler_height=0,
            left_margin=self.left_margin,
            lanes=lanes,
        )

    def _update_scroll_extent(self) -> None:
        """Donne à la grille la largeur réelle de la timeline.

        L'ancienne largeur fixe (1600 px) empêchait de faire défiler
        un montage plus long que quelques secondes. La largeur suit
        la durée et le zoom. Elle peut être retirée si la grille
        devient un canevas virtuel qui ne grandit plus avec le temps.
        """
        if not hasattr(self, "timeline_grid"):
            return
        pixels = self.pixels_per_second * self.zoom
        width = int(self.left_margin + max(self.duration_seconds, 1.0) * pixels + 120)
        viewport = self.scroll.viewport().width() if hasattr(self, "scroll") else 0
        self._cull_guard = True
        try:
            self.timeline_grid.setMinimumWidth(max(width, viewport, 400))
        finally:
            self._cull_guard = False

    def _rebuild_track_headers(self) -> None:
        for header in list(self.track_header_widgets.values()):
            header.setParent(None)
            header.deleteLater()
        self.track_header_widgets.clear()
        if self.project is None:
            return
        for index, track in enumerate(self.project.tracks):
            header = TrackRowHeader(track, self.timeline_grid)
            row_h = self.row_height_of(track)
            header.setFixedHeight(row_h)
            header.setGeometry(
                0,
                int(self.row_top(index)),
                self.left_margin,
                row_h,
            )
            header.show()
            header.lock_toggled.connect(self.toggle_track_lock_requested)
            header.visible_toggled.connect(self.toggle_track_visible_requested)
            header.mute_toggled.connect(self.toggle_track_muted_requested)
            header.solo_toggled.connect(self.solo_toggled.emit)
            header.arm_toggled.connect(self.arm_toggled.emit)
            header.height_cycle_requested.connect(self.height_cycle_requested.emit)
            header.collapse_toggled.connect(self.collapse_toggled.emit)
            header.move_up_requested.connect(self.move_track_up_requested)
            header.move_down_requested.connect(self.move_track_down_requested)
            header.remove_requested.connect(self.remove_track_requested)
            header.rename_requested.connect(self._on_rename_requested)
            self.track_header_widgets[track.id] = header

    def _visibility_window(
        self,
    ) -> tuple[tuple[float, float] | None, tuple[int, int] | None]:
        """Fenêtre temps / pistes à monter.

        ``None`` signifie « tout monter ». C'est le cas tant que le
        viewport n'a pas de taille réelle (tests, premier layout) pour
        ne pas cacher les clips d'un petit projet avant affichage.
        Le culling vertical ne démarre qu'à partir de 12 pistes : en
        dessous, le coût des en-têtes reste négligeable et les projets
        de démonstration gardent tous leurs clips.
        """
        if not hasattr(self, "scroll"):
            return None, None
        # Sur les projets courts, instancier tous les clips reste
        # négligeable et rend la vue stable pendant un détachement ou un
        # re-docking : Qt peut alors rapporter un viewport provisoirement
        # étroit et démonter à tort un clip pourtant bien présent dans le
        # modèle. Le culling horizontal est réservé aux longues timelines,
        # son vrai cas d'usage.
        if len(self.clip_views) <= 64:
            return None, None
        viewport = self.scroll.viewport()
        pixels = self.pixels_per_second * self.zoom
        time_range = None
        if viewport.width() >= 48 and pixels > 0:
            scroll_x = self.scroll.horizontalScrollBar().value()
            left_px = scroll_x - self._overscan_px
            right_px = scroll_x + viewport.width() + self._overscan_px
            time_range = (
                (left_px - self.left_margin) / pixels,
                (right_px - self.left_margin) / pixels,
            )
        row_range = None
        track_count = len(self.project.tracks) if self.project is not None else 0
        if track_count >= 12 and viewport.height() >= 32 and self.project is not None:
            scroll_y = self.scroll.verticalScrollBar().value()
            top_visible = scroll_y - 80
            bottom_visible = scroll_y + viewport.height() + 80
            first = None
            last = None
            for index, track in enumerate(self.project.tracks):
                top = self.row_top(index)
                bottom = top + self.row_height_of(track)
                if bottom >= top_visible and top <= bottom_visible:
                    first = index if first is None else first
                    last = index
            if first is not None and last is not None:
                row_range = (first, last)
        return time_range, row_range

    def _clip_in_window(self, view: TimelineClipView, time_range, row_range) -> bool:
        if time_range is not None and (
            view.end < time_range[0] or view.start > time_range[1]
        ):
            return False
        if row_range is not None and not (row_range[0] <= view.track_index <= row_range[1]):
            return False
        return True

    def _sync_mounted_clips(self, *, refresh_views: bool = False) -> bool:
        """Monte les clips de la fenêtre visible et démonte les autres.

        Retourne ``True`` si l'ensemble des widgets a changé. Un clip
        en cours de glisser reste monté, sinon le geste serait coupé
        dès qu'il sort de l'écran.
        """
        if self._cull_guard:
            return False
        time_range, row_range = self._visibility_window()
        wanted: dict[str, TimelineClipView] = {}
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            dragging = widget is not None and widget.drag_mode is not None
            if dragging or self._clip_in_window(view, time_range, row_range):
                wanted[view.id] = view
        previous_ids = set(self.clip_widgets)
        changed = previous_ids != set(wanted)
        if not changed and not refresh_views:
            return False
        for clip_id in previous_ids - set(wanted):
            widget = self.clip_widgets.pop(clip_id)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        for view in wanted.values():
            widget = self.clip_widgets.get(view.id)
            if widget is None:
                widget = ClipWidget(view, self.timeline_grid)
                self.clip_widgets[view.id] = widget
            elif widget.drag_mode is None:
                # Le widget réutilisé doit voir le clip à jour, sinon
                # le prochain glisser repart de l'ancienne géométrie.
                widget.view = view
                widget.pending_start = view.start
                widget.pending_end = view.end
            widget.refresh_style()
            widget.show()
        return changed

    def _on_timeline_scrolled(self, _value: int = 0) -> None:
        if self._cull_guard:
            return
        if self._sync_mounted_clips():
            self._layout_children()
        self._sync_ruler()

    def _restyle_clip(self, clip_id: str | None) -> None:
        if not clip_id:
            return
        widget = self.clip_widgets.get(clip_id)
        if widget is not None:
            widget.refresh_style()

    def _sync_transition_widgets(self) -> None:
        """Projette les transitions persistantes dans la timeline."""
        transitions = list(getattr(self.project, "transitions", [])) if self.project else []
        wanted = {transition.id: transition for transition in transitions}
        for transition_id in set(self.transition_widgets) - set(wanted):
            widget = self.transition_widgets.pop(transition_id)
            widget.hide()
            widget.deleteLater()
        for transition_id, transition in wanted.items():
            widget = self.transition_widgets.get(transition_id)
            if widget is None:
                widget = TransitionMarkerWidget(transition_id, self)
                self.transition_widgets[transition_id] = widget
            widget.refresh_style(self._transition_label(transition))
            widget.show()
            widget.raise_()
        if self.selected_transition_id not in wanted:
            self.selected_transition_id = None
        self._layout_transition_widgets()

    @staticmethod
    def _transition_label(transition) -> str:
        labels = {
            "crossfade": "FONDU",
            "fade_black": "NOIR",
            "wipe_left": "BALAYAGE ←",
            "wipe_right": "BALAYAGE →",
        }
        return f"{labels.get(transition.type.value, 'TRANSITION')} · {transition.duration:.1f}s"

    def _layout_transition_widgets(self) -> None:
        if self.project is None:
            return
        views = {view.id: view for view in self.clip_views}
        pixels = self.pixels_per_second * self.zoom
        for transition in self.project.transitions:
            widget = self.transition_widgets.get(transition.id)
            outgoing = views.get(transition.from_clip_id)
            incoming = views.get(transition.to_clip_id)
            if widget is None or outgoing is None or incoming is None:
                continue
            width = max(54, int(transition.duration * pixels))
            x = int(self.left_margin + (outgoing.end - transition.duration) * pixels)
            y = int(self.row_top(outgoing.track_index) + 3)
            widget.setGeometry(x, y, width, 20)

    def _layout_children(self) -> None:
        """Repositionne en-têtes et clips — sans rien recréer.

        Utilisé par le redimensionnement : aucun widget n'est alloué ni
        détruit, ce qui garde le drag du séparateur fluide même avec
        beaucoup de clips.
        """
        for header in self.track_header_widgets.values():
            index = self._track_index(header.track.id)
            if index is None or self.project is None:
                continue
            height = self.row_height_of(self.project.tracks[index])
            header.setFixedHeight(height)
            header.setGeometry(0, int(self.row_top(index)), self.left_margin, height)
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None or widget.drag_mode is not None:
                continue
            widget.setGeometry(*self.clip_rect(view, view.start, view.end))
            widget.raise_()
        self._layout_transition_widgets()
        self._publish_overlay()
        self._schedule_previews()
        self._sync_ruler()

    def _track_index(self, track_id: str) -> int | None:
        """Index d'une piste dans le projet, ou ``None`` si absente."""
        if self.project is None:
            return None
        for index, track in enumerate(self.project.tracks):
            if track.id == track_id:
                return index
        return None

    def _on_rename_requested(self, track_id: str) -> None:
        """Demande un nouveau nom à l'utilisateur et relaie vers MainWindow."""
        if self.project is None:
            return
        track = next((t for t in self.project.tracks if t.id == track_id), None)
        if track is None:
            return
        from PySide6.QtWidgets import QInputDialog

        new_name, accepted = QInputDialog.getText(
            self,
            translate("action.preferences"),
            translate("tracks.rename"),
            text=track.name,
        )
        if accepted and new_name and new_name != track.name:
            self.rename_track_requested.emit(track_id, new_name.strip())

    def row_height_of(self, track) -> int:
        if getattr(track, "collapsed", False):
            return _COLLAPSED_HEIGHT
        return _HEIGHTS.get(getattr(track, "height_mode", "normal"), self.track_height)

    def row_top(self, index: int) -> int:
        top = _CONTENT_TOP
        if self.project is None:
            return top + index * (self.track_height + self.track_gap)
        for cursor, track in enumerate(self.project.tracks):
            if cursor == index:
                return top
            top += self.row_height_of(track) + self.track_gap
        return top

    def rows_span(self) -> int:
        if self.project is None or not self.project.tracks:
            return _CONTENT_TOP + self.track_height + 16
        total = _CONTENT_TOP
        for track in self.project.tracks:
            total += self.row_height_of(track) + self.track_gap
        return total + 16

    def clip_rect(self, view, start: float, end: float, track_index: int | None = None):
        index = view.track_index if track_index is None else track_index
        height = self.track_height
        if self.project is not None and 0 <= index < len(self.project.tracks):
            height = self.row_height_of(self.project.tracks[index])
        scale = self.pixels_per_second * self.zoom
        x = int(self.left_margin + start * scale)
        width = max(40, int((end - start) * scale))
        return (x, int(self.row_top(index)), width, height)

    def track_is_locked(self, track_id: str) -> bool:
        if self.project is None:
            return False
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return bool(track and track.locked)

    def track_is_collapsed(self, track_id: str) -> bool:
        if self.project is None:
            return False
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return bool(track and getattr(track, "collapsed", False))

    def track_height_mode(self, track_id: str) -> str:
        if self.project is None:
            return "normal"
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return getattr(track, "height_mode", "normal") if track else "normal"

    def clip_model(self, clip_id: str):
        if self.project is None:
            return None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
        return None

    def _track_index_at_global_y(self, global_y: int, track_type: str) -> int | None:
        if self.project is None:
            return None
        from PySide6.QtCore import QPoint

        y = self.timeline_grid.mapFromGlobal(QPoint(0, int(global_y))).y()
        for index, track in enumerate(self.project.tracks):
            top = self.row_top(index)
            if top <= y <= top + self.row_height_of(track) and track.type == track_type and not track.locked:
                return index
        return None

    def _sync_ruler(self) -> None:
        if not hasattr(self, "ruler"):
            return
        palette = _current_palette()
        scroll = self.scroll.horizontalScrollBar().value() if hasattr(self, "scroll") else 0
        markers = list(getattr(self.project, "markers", [])) if self.project is not None else []
        self.markers = markers
        self.ruler.sync(
            scroll_x=scroll,
            zoom=self.zoom,
            pixels_per_second=self.pixels_per_second,
            duration=self.duration_seconds,
            fps=self.fps,
            playhead=self.playhead_seconds,
            origin=self.left_margin,
            markers=markers,
            background=palette.ruler_bg,
            tick=palette.ruler_line,
            text=palette.muted,
            playhead_color=palette.playhead,
            marker_color=palette.marker,
        )

    def _publish_overlay(self) -> None:
        if not hasattr(self, "timeline_grid"):
            return
        scale = self.pixels_per_second * self.zoom
        self.timeline_grid.playhead_x = self.left_margin + self.playhead_seconds * scale
        self.timeline_grid.snap_x = self.snap_line_x
        self.timeline_grid.update()
