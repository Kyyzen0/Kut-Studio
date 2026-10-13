"""Alignement du calque sélectionné sur le cadre et déplacement au clavier (ADR-0006, lot 1c).

Réutilise la mécanique du glisser du moniteur : :meth:`_apply_property_values` écrit une image-clé à la tête de
lecture si la position est animée (valeur statique sinon), puis une seule étape d'historique est enregistrée.
Seule ``position_x`` / ``position_y`` change : jamais l'échelle ni la rotation.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt

from core.frame_alignment import FrameEdge, align_to_frame, screen_delta_to_position_delta
from core.mograph_scene import map_box
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)

NUDGE_PIXELS = 1.0
NUDGE_PIXELS_SHIFT = 10.0
NUDGE_MERGE_PREFIX = "nudge:"
_ARROWS = {
    Qt.Key_Left: (-1.0, 0.0), Qt.Key_Right: (1.0, 0.0),
    Qt.Key_Up: (0.0, -1.0), Qt.Key_Down: (0.0, 1.0),
}


class _ViewerKeyFilter(QObject):
    """Flèches sur le moniteur (quand il a le focus) : déplacent le calque sélectionné, sinon ne font rien."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() != QEvent.KeyPress or event.key() not in _ARROWS:
            return False
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if modifiers not in (Qt.NoModifier, Qt.ShiftModifier):
            return False                      # Ctrl/Alt/Meta + flèche : raccourcis existants, on ne s'en mêle pas
        step = NUDGE_PIXELS_SHIFT if modifiers == Qt.ShiftModifier else NUDGE_PIXELS
        dx, dy = _ARROWS[event.key()]
        if self._window._nudge_selected_layer(dx * step, dy * step):
            event.accept()
            return True
        return False                          # rien à déplacer : la touche garde son rôle habituel


class FrameAlignmentMixin:
    """Méthodes de :class:`ui.main_window.MainWindow` pour l'alignement sur le cadre et le déplacement au clavier."""

    def _init_frame_alignment(self) -> None:
        self.properties_panel.frame_align.align_requested.connect(self.align_selected_layer_to_frame)
        self._viewer_key_filter = _ViewerKeyFilter(self)
        self.preview_panel.graphics_view.installEventFilter(self._viewer_key_filter)

    def _frame_alignment_shortcut_handlers(self) -> dict:
        return {
            f"layer_align_{edge.value}": (lambda e=edge: self.align_selected_layer_to_frame(e))
            for edge in FrameEdge
        }

    # ------------------------------------------------------------------

    def _editable_selection_geometry(self):
        """Géométrie à jour du calque sélectionné, ou ``None`` (message dans la barre d'état)."""
        t = float(self.playhead_seconds)
        self._refresh_viewer_overlay(t)
        selection = self.preview_panel.overlay.selection
        clip_id = self._primary_selected_id()
        clip, track = self._find_clip_and_track(clip_id) if clip_id else (None, None)
        if clip is not None and getattr(clip, "sequence_id", ""):
            # Un clip de séquence imbriquée n'entre pas dans la scène du moniteur (pas de poignées) : message précis, pas « aucun calque ».
            self.statusBar().showMessage(translate("mograph.align.nested"), 4000)
            return None
        if selection is None or not clip_id or selection.clip_id != clip_id:
            self.statusBar().showMessage(translate("mograph.align.no_layer"), 4000)
            return None
        graphic = getattr(clip, "graphic", None)
        if (
            not selection.editable or clip is None or track is None or track.locked
            or bool(getattr(graphic, "locked", False))
        ):
            self.statusBar().showMessage(translate("mograph.align.locked"), 4000)
            return None
        return selection

    @staticmethod
    def _out_of_range(values: dict) -> list[str]:
        """Propriétés dont la valeur visée sort des bornes du registre (``_apply_property_values`` la ramènerait en silence)."""
        from core.animation_targets import get_target

        refused = []
        for name, value in values.items():
            spec = get_target(name).spec
            if (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
                refused.append(name)
        return refused

    def _move_selected_layer_on_screen(
        self, selection, dx: float, dy: float, label: str, *, merge_key=None, swallow_refusal: bool = False,
    ) -> bool:
        """Déplace le calque de ``(dx, dy)`` pixels écran ; ``False`` si refusé.

        Une position hors bornes est refusée avec un message, sans historique (``swallow_refusal`` : la touche est tout de même
        consommée, pour qu'une flèche ne change pas d'image à la place).
        """
        canvas = (float(self.project.width), float(self.project.height))
        delta = screen_delta_to_position_delta(selection.parent_world, dx, dy, canvas)
        if delta is None:
            self.statusBar().showMessage(translate("mograph.align.degenerate"), 4000)
            return False
        if abs(delta[0]) < 1e-12 and abs(delta[1]) < 1e-12:
            return True                       # déjà en place : aucune étape d'historique
        values = {}
        if abs(delta[0]) >= 1e-12:
            values["position_x"] = selection.values.get("position_x", 0.0) + delta[0]
        if abs(delta[1]) >= 1e-12:
            values["position_y"] = selection.values.get("position_y", 0.0) + delta[1]
        if self._out_of_range(values):
            self.statusBar().showMessage(translate("mograph.align.out_of_range"), 4000)
            return swallow_refusal
        self._finalize_pending_edit_sessions()
        if not self._apply_property_values(selection.clip_id, values):
            return False
        self._after_live_change(selection.clip_id)
        self._commit_layer_edit(label, select=selection.clip_id, merge_key=merge_key)
        return True

    def align_selected_layer_to_frame(self, edge) -> bool:
        """Aligne la boîte englobante du clip sélectionné sur ``edge`` du cadre (une étape d'historique)."""
        try:
            edge = FrameEdge(edge)
        except ValueError:
            return False
        selection = self._editable_selection_geometry()
        if selection is None:
            return False
        w, h = selection.box
        bounds = map_box(selection.world, w, h)
        dx, dy = align_to_frame(bounds, (float(self.project.width), float(self.project.height)), edge)
        return self._move_selected_layer_on_screen(selection, dx, dy, translate("history.layer.align"))

    def _nudge_selected_layer(self, dx: float, dy: float) -> bool:
        """Déplace le calque sélectionné de ``(dx, dy)`` pixels du cadre ; ``False`` s'il n'y a rien à déplacer.

        Sans message d'erreur : une flèche sur un moniteur sans calque éditable garde son effet habituel.
        Les appuis rapides sur un même calque (touche maintenue) ne forment qu'une entrée d'historique.
        """
        clip_id = self._primary_selected_id()
        selection = self.preview_panel.overlay.selection
        if not clip_id or selection is None or selection.clip_id != clip_id or not selection.editable:
            return False
        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked or bool(getattr(getattr(clip, "graphic", None), "locked", False)):
            return False
        self._refresh_viewer_overlay(float(self.playhead_seconds))   # valeurs à jour après l'appui précédent
        selection = self.preview_panel.overlay.selection
        if selection is None or selection.clip_id != clip_id:
            return False
        return self._move_selected_layer_on_screen(
            selection, dx, dy, translate("history.layer.nudge"), merge_key=f"{NUDGE_MERGE_PREFIX}{clip_id}",
            swallow_refusal=True,
        )


__all__ = ["FrameAlignmentMixin", "NUDGE_PIXELS", "NUDGE_PIXELS_SHIFT"]
