"""Mixin de ``TimelinePanel`` : séquences (navigation, dépôt d'une séquence).

- construit la :class:`~ui.timeline_widgets.sequence_bar.SequenceNavigationBar`
  et relaie ses signaux ;
- accepte le dépôt d'une séquence de la bibliothèque (et d'un média) sur une
  piste : la position et la piste sont calculées sous le pointeur, la
  fenêtre principale valide et applique l'opération ;
- accepte des **photos** glissées du Finder / de l'explorateur, mais seulement
  au-dessus d'un emplacement de template (elles le remplissent, lui et les
  emplacements vides qui suivent) ; ailleurs, les fichiers vont à la fenêtre,
  qui les importe.

Les signaux sont déclarés sur :class:`ui.timeline_panel.TimelinePanel`.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint

from core.template_slots import is_photo_path, slot_at
from ui.timeline_widgets.sequence_bar import SequenceNavigationBar

SEQUENCE_MIME = "application/x-kut-studio-sequence-id"
"""Type MIME d'une séquence glissée depuis la bibliothèque."""

ASSET_MIME = "application/x-kut-studio-asset-id"
"""Type MIME d'un média glissé depuis la bibliothèque."""


class SequencesTimelineMixin:
    """Barre de navigation des séquences et cible de dépôt."""

    def _build_sequence_bar(self) -> SequenceNavigationBar:
        bar = SequenceNavigationBar(self)
        bar.open_requested.connect(self.sequence_open_requested.emit)
        bar.back_requested.connect(self.sequence_back_requested.emit)
        bar.forward_requested.connect(self.sequence_forward_requested.emit)
        bar.parent_requested.connect(self.sequence_parent_requested.emit)
        self.sequence_bar = bar
        return bar

    def set_sequence_navigation(
        self,
        crumbs: list[tuple[str, str]],
        sequences: list[tuple[str, str]],
        *,
        can_back: bool,
        can_forward: bool,
        can_parent: bool,
    ) -> None:
        """Affiche la séquence active, son fil d'Ariane et l'état des boutons."""
        self.sequence_bar.set_state(
            crumbs, sequences, can_back=can_back, can_forward=can_forward, can_parent=can_parent
        )

    # ------------------------------------------------------------------
    # Dépôt
    # ------------------------------------------------------------------

    @staticmethod
    def _dropped_payload(mime) -> tuple[str, object] | None:
        for kind, mime_type in (("sequence", SEQUENCE_MIME), ("asset", ASSET_MIME)):
            if mime.hasFormat(mime_type):
                value = bytes(mime.data(mime_type)).decode("utf-8", "replace").strip()
                if value:
                    return kind, value
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths and all(is_photo_path(path) for path in paths):
                return "photos", paths
        return None

    def _photo_slot_under(self, position: QPoint) -> tuple[str, float] | None:
        """``(piste, temps)`` d'un emplacement de template sous ``position``, sinon ``None``."""
        target = self.drop_target_at(position)
        if target is None or self.project is None or slot_at(self.project, *target) is None:
            return None
        return target

    def drop_target_at(self, position: QPoint) -> tuple[str, float] | None:
        """``(piste, temps)`` sous ``position`` (coordonnées du panneau)."""
        if self.project is None:
            return None
        point = self.timeline_grid.mapFrom(self, position)
        scale = self.pixels_per_second * self.zoom
        if scale <= 0:
            return None
        seconds = max(0.0, (point.x() - self.left_margin) / scale)
        for index, track in enumerate(self.project.tracks):
            top = self.row_top(index)
            if top <= point.y() <= top + self.row_height_of(track):
                return track.id, seconds
        return None

    def dragEnterEvent(self, event) -> None:
        if self._dropped_payload(event.mimeData()) is None:
            event.ignore()  # fichiers : la fenêtre principale les importe
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        payload = self._dropped_payload(event.mimeData())
        if payload is None:
            event.ignore()
            return
        position = event.position().toPoint()
        target = self._photo_slot_under(position) if payload[0] == "photos" else self.drop_target_at(position)
        if target is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        payload = self._dropped_payload(event.mimeData())
        position = event.position().toPoint()
        if payload is None:
            target = None
        elif payload[0] == "photos":
            target = self._photo_slot_under(position)
        else:
            target = self.drop_target_at(position)
        if payload is None or target is None:
            event.ignore()
            return
        kind, value = payload
        track_id, seconds = target
        if kind == "sequence":
            self.sequence_dropped.emit(value, track_id, seconds)
        elif kind == "photos":
            self.photos_dropped.emit(list(value), track_id, seconds)
        else:
            self.asset_dropped.emit(value, track_id, seconds)
        event.acceptProposedAction()


__all__ = ["ASSET_MIME", "SEQUENCE_MIME", "SequencesTimelineMixin"]
