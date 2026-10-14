"""Poignées de masque dans le moniteur : liaison pile de l'éditeur de compositing → moniteur, et écriture du glisser.

ADR-0006, étape 3, lot 3a. Le signal ``mask_edit_changed`` de l'éditeur (identifiant du masque choisi, ``""`` : aucun)
est gardé dans ``_mask_edit_id`` ; la surcouche du moniteur montre alors ce masque à la place des poignées du clip.

Écriture : la mécanique du glisser du clip (:meth:`MotionGraphicsMixin._apply_property_values`) : image-clé à la tête de
lecture si la propriété est animée (``mask.<id>.<propriété>``), valeur statique sinon ; une seule étape d'historique à
la relâche. Un masque suivi par un lien de tracking est dessiné à sa position rendue, mais le glisser part des valeurs saisies
(``MaskGeometry.base``) : le suivi ne s'ajoute pas deux fois. Les sommets d'un polygone et les nœuds / tangentes d'une courbe
(lot 3b) ne s'animent pas : ils sont écrits dans le masque, validés avant (:func:`core.mask_path_handles.nodes_refusal`). Toute entrée repasse par
:func:`core.mask_handles.mask_edit_refusal` : un signal ne contourne ni le verrou de la piste, ni celui du calque.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from core import mask_handles
from core.mask_path_handles import nodes_refusal
from core.compositing import MASK_PROPERTIES, mask_property_id
from ui.i18n import translate
from ui.main_window_mixins.mask_path_keys import PathKeyFilter

LOGGER = logging.getLogger(__name__)


class MaskEditMixin:
    """Méthodes de :class:`ui.main_window.MainWindow` pour les poignées de masque du moniteur."""

    def _init_mask_edit(self) -> None:
        self._mask_edit_id = ""
        self._mask_edit_clip = ""        # le clip auquel appartient ce mode : un autre clip sélectionné en sort
        overlay = self.preview_panel.overlay
        overlay.mask_dragged.connect(self._on_mask_dragged)
        overlay.mask_released.connect(self._on_mask_released)
        self.properties_panel.mask_edit_changed.connect(self._on_mask_edit_selected)
        self._mask_path_keys = PathKeyFilter(self)
        self.preview_panel.graphics_view.installEventFilter(self._mask_path_keys)

    def _on_mask_edit_selected(self, mask_id: str) -> None:
        self._mask_edit_id = str(mask_id or "")
        self._mask_edit_clip = str(getattr(self.properties_panel.selected_clip, "id", "") or "") if self._mask_edit_id else ""
        self._refresh_viewer_overlay(float(self.playhead_seconds))

    def _refresh_mask_overlay(self, t: float, scene, selected) -> bool:
        """Montre dans le moniteur le masque choisi (évalué à ``t``) ; ``True`` : il remplace les poignées du clip.

        Le masque s'affiche même si le clip est verrouillé ou non rendu, mais sans poignées actives ; hors de la
        durée du clip (ou clip imbriqué, piste de mauvais genre, masque disparu), rien : les poignées du clip reprennent.
        """
        overlay = self.preview_panel.overlay
        mask_id = self._mask_edit_id
        clip_id = getattr(selected, "id", None)
        if mask_id and clip_id != self._mask_edit_clip:
            # Un autre clip est sélectionné (un doublon garde les mêmes identifiants de masque) : retour aux poignées du clip.
            self._mask_edit_id = self._mask_edit_clip = ""
            self.properties_panel.compositing_group.reset_edit()
            return False
        if not mask_id or clip_id is None:
            return False
        reason = mask_handles.mask_edit_refusal(self.project, clip_id, t, mask_id, scene=scene)
        if reason in ("missing", "kind", "nested", "outside"):
            return False
        evaluated = scene.evaluate(clip_id, t)
        mask = next((item for item in evaluated.masks if item.id == mask_id), None)
        if mask is None:
            return False
        # Dessin d'après le masque rendu (suivi compris) ; le glisser part du masque saisi, sans la dérivation du suivi.
        local = t - self._clip_start(clip_id)
        overlay.set_mask_edit(mask_handles.geometry_of(
            clip_id, mask, evaluated.world, evaluated.box, editable=reason is None,
            entered=mask_handles.entered_mask(self._find_clip_and_track(clip_id)[0], mask_id, local)))
        return True

    def _clip_start(self, clip_id: str) -> float:
        clip, _track = self._find_clip_and_track(clip_id)
        return float(clip.timeline_start) if clip is not None else 0.0

    def _on_mask_dragged(self, clip_id: str, mask_id: str, values: dict) -> None:
        """Un pas de glisser : aperçu en direct, sans historique (la relâche l'enregistre)."""
        reason = mask_handles.mask_edit_refusal(self.project, clip_id, float(self.playhead_seconds), mask_id)
        if reason is not None:
            self.statusBar().showMessage(translate("mask.edit.locked" if reason == "locked" else "mask.edit.unavailable"), 4000)
            return
        points, handles = values.get("points"), values.get("handles")
        numeric = {name: value for name, value in values.items() if name in MASK_PROPERTIES}
        if mask_handles.out_of_bounds(numeric, points, handles):   # la zone de dessin ne le propose pas ; garde côté modèle
            return
        if not numeric and points is None and handles is None:
            return
        if (points is not None or handles is not None) and self._mask_nodes_refused(clip_id, mask_id, points, handles):
            return
        if not self._viewer_dragging:
            self._finalize_pending_edit_sessions()
            self._viewer_dragging = True
        changed = False
        if numeric:
            changed = self._apply_property_values(clip_id, {mask_property_id(mask_id, name): value for name, value in numeric.items()})
        if points is not None or handles is not None:
            changed = self._write_mask_nodes(clip_id, mask_id, points, handles) or changed
        if changed:
            self._after_live_change(clip_id)

    def _mask_nodes_refused(self, clip_id: str, mask_id: str, points, handles) -> bool:
        """``True`` : les nœuds ne peuvent pas être écrits (message dans la barre d'état si c'est le nombre de nœuds)."""
        from core.timeline_operations import find_clip

        try:
            mask = find_clip(self.project, clip_id).compositing.mask_by_id(mask_id)
        except KeyError:
            return True
        if mask is None:
            return True
        reason = nodes_refusal(mask, None if points is None else tuple(points), None if handles is None else tuple(handles))
        if reason == "count":
            self.statusBar().showMessage(translate("mask.path.min_nodes"), 4000)
        return reason is not None

    def _write_mask_nodes(self, clip_id: str, mask_id: str, points, handles) -> bool:
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return False
        compositing = clip.compositing
        if compositing.mask_by_id(mask_id) is None:
            return False
        changes = {} if points is None else {"points": tuple(points)}
        if handles is not None:
            changes["handles"] = tuple(handles)
        clip.compositing = replace(compositing, masks=tuple(
            replace(mask, **changes, id=mask.id) if mask.id == mask_id else mask for mask in compositing.masks))
        return True

    def _on_mask_released(self, clip_id: str, _mask_id: str) -> None:
        """Fin du geste : une seule étape d'historique."""
        if not self._viewer_dragging:
            return
        self._viewer_dragging = False
        self._commit_layer_edit(translate("history.mask.drag"), select=clip_id)


__all__ = ["MaskEditMixin"]
