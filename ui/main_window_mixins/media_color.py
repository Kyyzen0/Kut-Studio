"""Méthodes de ``MainWindow`` : espace colorimétrique d'un média (Auto / BT.601 / BT.709, :mod:`core.color_matrix`)."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from core.media_color_ops import MediaColorError, set_media_color_matrix
from ui.i18n import translate


class MediaColorMixin:
    """Mixin de ``MainWindow`` : le réglage vient du menu de la bibliothèque ; la règle (verrous, genre de média, valeur) est celle du
    cœur, la même que pour le verbe de script ``set_media_color_matrix``."""

    def _on_asset_color_matrix_requested(self, asset_id: str, value: str) -> None:
        """Fixe l'espace colorimétrique du média : une étape d'historique, moniteur et miniatures relus avec la nouvelle matrice."""
        try:
            changed = set_media_color_matrix(self.project, asset_id, value)
        except (MediaColorError, KeyError) as error:
            QMessageBox.warning(self, translate("panel.library"), str(error.args[0]) if isinstance(error, KeyError) else str(error))
            return
        if not changed:
            return
        self._record_history(translate("history.media.color_matrix"))
        self._mark_dirty()
        self._refresh_project_library()
        # Le moniteur relit l'image avec la nouvelle matrice ; les segments fidèles et les miniatures ont une autre clé de cache.
        self.timeline_panel.update()
        if not getattr(self, "is_playing", False):
            self._sync_preview_to_timeline()
