"""Sélection des clips et des transitions, rectangle de sélection."""

from __future__ import annotations


from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QRubberBand,
)

from core.timeline_editing import (
    clip_ids_in_range,
)


class SelectionMixin:
    """Mixin de ``TimelinePanel`` : sélection des clips et des transitions, rectangle de sélection."""

    def select_transition(self, transition_id: str) -> None:
        if self.project is None or not any(
            item.id == transition_id for item in self.project.transitions
        ):
            return
        self.selected_transition_id = transition_id
        self._set_selection([], None, announce=False)
        self._sync_transition_widgets()
        self.transition_selected.emit(transition_id)

    def clear_transition_selection(self) -> None:
        if self.selected_transition_id is None:
            return
        self.selected_transition_id = None
        self._sync_transition_widgets()

    def _is_selected(self, clip_id: str) -> bool:
        return clip_id in self.selected_clip_ids or clip_id == self.selected_clip_id

    def _set_selection(self, ids, primary: str | None, announce: bool) -> None:
        if ids:
            self.clear_transition_selection()
        previous = set(self.selected_clip_ids)
        if self.selected_clip_id:
            previous.add(self.selected_clip_id)
        self.selected_clip_ids = {clip_id for clip_id in ids if clip_id}
        self.selected_clip_id = primary if primary in self.selected_clip_ids else (
            next(iter(self.selected_clip_ids), None)
        )
        for clip_id in previous.symmetric_difference(self.selected_clip_ids):
            self._restyle_clip(clip_id)
        if self.selected_clip_id:
            self._restyle_clip(self.selected_clip_id)
        if announce and self.selected_clip_id:
            self.clip_selected.emit(self.selected_clip_id)
        elif announce and not self.selected_clip_ids:
            self.selection_cleared.emit()

    def _select_from_pointer(self, clip_id: str, modifiers, drag: bool) -> None:
        ctrl = bool(modifiers & (Qt.ControlModifier | Qt.MetaModifier))
        shift = bool(modifiers & Qt.ShiftModifier)
        if shift and self._selection_anchor:
            ids = clip_ids_in_range(self.clip_views, self._selection_anchor, clip_id)
            self._set_selection(ids, clip_id, announce=True)
            return
        if ctrl:
            ids = set(self.selected_clip_ids)
            if clip_id in ids:
                ids.remove(clip_id)
            else:
                ids.add(clip_id)
            self._set_selection(ids, clip_id if clip_id in ids else next(iter(ids), None), announce=True)
            return
        if drag and clip_id in self.selected_clip_ids and len(self.selected_clip_ids) > 1:
            self.selected_clip_id = clip_id
            self.clip_selected.emit(clip_id)
            return
        self._selection_anchor = clip_id
        self._set_selection([clip_id], clip_id, announce=True)

    def begin_marquee(self, origin) -> None:
        if self._marquee is None:
            self._marquee = QRubberBand(QRubberBand.Rectangle, self.timeline_grid)
        self._marquee_origin = origin
        self._marquee.setGeometry(QRect(origin, origin))
        self._marquee.show()

    def update_marquee(self, pos) -> None:
        if self._marquee is None or self._marquee_origin is None:
            return
        self._marquee.setGeometry(QRect(self._marquee_origin, pos).normalized())

    def finish_marquee(self, pos) -> None:
        if self._marquee is None or self._marquee_origin is None:
            return
        rect = QRect(self._marquee_origin, pos).normalized()
        self._marquee.hide()
        self._marquee_origin = None
        if rect.width() < 4 and rect.height() < 4:
            self._set_selection([], None, announce=True)
            return
        ids = []
        # Candidats par requête d'intervalle (temps du rectangle, rangées
        # touchées), puis test géométrique exact : même résultat qu'un
        # balayage complet, sans parcourir les clips hors du rectangle.
        scale = self.pixels_per_second * self.zoom
        if scale > 0 and self.project is not None:
            margin = 40 / scale  # largeur minimale d'un clip (40 px)
            t0 = (rect.left() - self.left_margin) / scale - margin
            t1 = (rect.right() - self.left_margin) / scale
            rows = [
                index for index, track in enumerate(self.project.tracks)
                if self.row_top(index) <= rect.bottom()
                and self.row_top(index) + self.row_height_of(track) >= rect.top()
            ]
            row_range = (min(rows), max(rows)) if rows else (0, -1)
            candidates = self.clips_overlapping(t0, t1, row_range)
        else:
            candidates = self.clip_views
        for view in candidates:
            x, y, width, height = self.clip_rect(view, view.start, view.end)
            if rect.intersects(QRect(x, y, width, height)):
                ids.append(view.id)
        primary = ids[-1] if ids else None
        self._selection_anchor = primary
        self._set_selection(ids, primary, announce=True)
