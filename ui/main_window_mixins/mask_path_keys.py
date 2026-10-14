"""Suppr / Retour arrière sur le moniteur : retire le nœud de courbe survolé (ADR-0006, lot 3b).

Ces touches sont aussi des raccourcis de la fenêtre (supprimer le clip sélectionné). Quand un nœud de la courbe éditée est
survolé, le moniteur les réclame (``ShortcutOverride``) : sinon c'est le clip qui disparaîtrait. Sans nœud survolé, le
filtre ne fait rien et la touche garde son rôle habituel.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt

from ui.i18n import translate

_KEYS = (Qt.Key_Delete, Qt.Key_Backspace)


class PathKeyFilter(QObject):
    """Filtre d'événements du moniteur (``graphics_view``)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window

    def _node_hovered(self) -> bool:
        masks = self._window.preview_panel.overlay.masks
        geometry = masks.geometry
        return geometry is not None and geometry.shape == "path" and geometry.editable and masks.hovered_node() is not None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress) or event.key() not in _KEYS:
            return False
        if event.modifiers() & ~Qt.KeypadModifier or not self._node_hovered():
            return False
        if event.type() == QEvent.KeyPress and self._window.preview_panel.overlay.masks.remove_hovered_node() is False:
            self._window.statusBar().showMessage(translate("mask.path.min_nodes"), 4000)
        event.accept()
        return True


__all__ = ["PathKeyFilter"]
