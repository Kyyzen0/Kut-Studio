"""Poignées de masque dans le moniteur : liaison pile de l'éditeur de compositing → moniteur, et écriture du glisser.

ADR-0006, étape 3, lot 3a. Le signal ``mask_edit_changed`` de l'éditeur (identifiant du masque choisi, ``""`` : aucun)
est gardé dans ``_mask_edit_id`` ; la surcouche du moniteur montre alors ce masque à la place des poignées du clip.

Écriture : la mécanique du glisser du clip (:meth:`MotionGraphicsMixin._apply_property_values`) : image-clé à la tête de
lecture si la propriété est animée (``mask.<id>.<propriété>``), valeur statique sinon ; une seule étape d'historique à
la relâche. Un masque suivi par un lien de tracking est dessiné à sa position rendue, mais le glisser part des valeurs saisies
(``MaskGeometry.base``) : le suivi ne s'ajoute pas deux fois. Les sommets d'un polygone ne s'animent pas : ils sont écrits dans le masque. Toute entrée repasse par
:func:`core.mask_handles.mask_edit_refusal` : un signal ne contourne ni le verrou de la piste, ni celui du calque.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from core import mask_handles
from core.compositing import MASK_PROPERTIES, mask_property_id
from ui.i18n import translate

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
        points = values.get("points")
        numeric = {name: value for name, value in values.items() if name in MASK_PROPERTIES}
        if mask_handles.out_of_bounds(numeric, points):     # la zone de dessin ne le propose pas ; garde côté modèle
            return
        if not numeric and points is None:
            return
        if not self._viewer_dragging:
            self._finalize_pending_edit_sessions()
            self._viewer_dragging = True
        changed = False
        if numeric:
            changed = self._apply_property_values(clip_id, {mask_property_id(mask_id, name): value for name, value in numeric.items()})
        if points is not None:
            changed = self._write_mask_points(clip_id, mask_id, tuple(points)) or changed
        if changed:
            self._after_live_change(clip_id)

    def _write_mask_points(self, clip_id: str, mask_id: str, points: tuple) -> bool:
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return False
        compositing = clip.compositing
        if compositing.mask_by_id(mask_id) is None:
            return False
        clip.compositing = replace(compositing, masks=tuple(
            replace(mask, points=points, id=mask.id) if mask.id == mask_id else mask for mask in compositing.masks))
        return True

    def _on_mask_released(self, clip_id: str, _mask_id: str) -> None:
        """Fin du geste : une seule étape d'historique."""
        if not self._viewer_dragging:
            return
        self._viewer_dragging = False
        self._commit_layer_edit(translate("history.mask.drag"), select=clip_id)


__all__ = ["MaskEditMixin"]
