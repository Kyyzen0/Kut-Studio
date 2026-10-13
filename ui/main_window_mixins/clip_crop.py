"""Commande de menu « Réinitialiser le recadrage » (ADR-0006, étape 2) : retire le masque de recadrage du clip sélectionné."""

from __future__ import annotations

from core.clip_crop import clear_crop, crop_mask
from core.timeline_operations import find_clip
from ui.i18n import translate


class ClipCropMixin:
    """Méthode de :class:`ui.main_window.MainWindow` ; l'édition des quatre bords est dans l'inspecteur."""

    def reset_selected_crop(self) -> bool:
        """Retire le recadrage du clip sélectionné (une étape d'historique) ; ``False`` s'il n'y en a pas ou si c'est refusé."""
        clip_id = self._primary_selected_id()
        if not clip_id:
            return False
        try:
            if crop_mask(find_clip(self.project, clip_id).compositing) is None:
                return False
        except KeyError:
            return False
        done = self._layer_operation(translate("history.layer.reset_crop"), lambda: clear_crop(self.project, clip_id), select=clip_id)
        return done is None and crop_mask(find_clip(self.project, clip_id).compositing) is None


__all__ = ["ClipCropMixin"]
