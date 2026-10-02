"""Mixin de ``TimelinePanel`` : séquences (navigation, dépôt d'une séquence).

- construit la :class:`~ui.timeline_widgets.sequence_bar.SequenceNavigationBar`
  et relaie ses signaux ;
- accepte le dépôt d'une séquence de la bibliothèque (et d'un média) sur une
  piste : la position et la piste sont calculées sous le pointeur, la
  fenêtre principale valide et applique l'opération.

Les signaux sont déclarés sur :class:`ui.timeline_panel.TimelinePanel`.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint

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
    def _dropped_payload(mime) -> tuple[str, str] | None:
        for kind, mime_type in (("sequence", SEQUENCE_MIME), ("asset", ASSET_MIME)):
            if mime.hasFormat(mime_type):
                value = bytes(mime.data(mime_type)).decode("utf-8", "replace").strip()
                if value:
                    return kind, value
        return None

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
        if self._dropped_payload(event.mimeData()) is None:
            event.ignore()
            return
        target = self.drop_target_at(event.position().toPoint())
        if target is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        payload = self._dropped_payload(event.mimeData())
        target = self.drop_target_at(event.position().toPoint()) if payload else None
        if payload is None or target is None:
            event.ignore()
            return
        kind, value = payload
        track_id, seconds = target
        if kind == "sequence":
            self.sequence_dropped.emit(value, track_id, seconds)
        else:
            self.asset_dropped.emit(value, track_id, seconds)
        event.acceptProposedAction()


__all__ = ["ASSET_MIME", "SEQUENCE_MIME", "SequencesTimelineMixin"]
