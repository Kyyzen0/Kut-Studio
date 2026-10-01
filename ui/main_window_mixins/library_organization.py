"""Méthodes de ``MainWindow`` regroupées : library_organization."""

from __future__ import annotations

import os
from PySide6.QtWidgets import QDialog


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class LibraryOrganizationMixin:
    """Mixin de ``MainWindow`` (library_organization)."""

    def _on_folder_create_requested(
        self,
        name: str,
        parent_id: object,
        color: str,
    ) -> None:
        """Crée un dossier (à la racine ou sous un parent)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            parent_id_str = (
                str(parent_id) if parent_id not in (None, "") else None
            )
            folder = org.create_folder(
                name,
                parent_id=parent_id_str,
                color=color or "",
            )
        except (LibraryError, LibraryNameError) as exc:
            _main_window().QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Créer le dossier « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_folder_rename_requested(
        self,
        folder_id: str,
        new_name: str,
    ) -> None:
        """Renomme un dossier (déclenché via menu contextuel)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            folder = org.rename_folder(folder_id, new_name)
        except (LibraryError, LibraryNameError) as exc:
            _main_window().QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Renommer le dossier en « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_folder_delete_requested(self, folder_id: str) -> None:
        """Supprime un dossier et rattache ses médias à la racine."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        folder = org.get_folder(folder_id)
        if folder is None:
            return
        confirm = _main_window().QMessageBox.question(
            self,
            "Supprimer le dossier",
            f"Supprimer le dossier « {folder.name} » et ramener ses "
            "médias à la racine ?",
            _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
            _main_window().QMessageBox.No,
        )
        if confirm != _main_window().QMessageBox.Yes:
            return
        try:
            org.delete_folder(folder_id, cascade=True)
        except LibraryError as exc:
            _main_window().QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Supprimer le dossier « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_tag_manager_requested(self) -> None:
        """Ouvre le dialogue de gestion des tags."""
        from core.library_organization import LibraryOrganization
        from ui.library_organization_widgets import TagManagerDialog

        org = LibraryOrganization(self.project)
        dialog = TagManagerDialog(org, parent=self)
        result = dialog.exec()
        if result == QDialog.Accepted:
            # Le dialogue n'utilise pas Accept ; tout est appliqué en
            # place. On enregistre l'historique dès qu'il y a au moins
            # une modification.
            self._record_history("Modifier les tags de la bibliothèque")
            self._refresh_project_library()
            self._mark_dirty()
        # Même sur rejet, le dialogue peut avoir été modifié : on
        # rafraîchit pour rester cohérent avec le projet.
        self._refresh_project_library()

    def _on_asset_move_to_folder(
        self,
        asset_id: str,
        folder_id: object,
    ) -> None:
        """Range un média dans un dossier (ou remet à la racine)."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        folder_id_str = str(folder_id) if folder_id not in (None, "") else None
        try:
            org.move_asset(asset_id, folder_id_str)
        except LibraryError as exc:
            _main_window().QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        # On n'enregistre l'historique que si le déplacement est
        # effectif (le service est idempotent).
        self._record_history(
            "Déplacer le média dans un dossier"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_tag_toggled(
        self,
        asset_id: str,
        tag_id: str,
        assign: bool,
    ) -> None:
        """Ajoute ou retire un tag sur un média."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            if assign:
                org.add_tag_to_asset(asset_id, tag_id)
                self._record_history("Tagger un média")
            else:
                org.remove_tag_from_asset(asset_id, tag_id)
                self._record_history("Retirer un tag d'un média")
        except LibraryError as exc:
            _main_window().QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_relink_requested(self, asset_id: str) -> None:
        """Ouvre un dialogue de sélection de fichier pour relier."""
        from core.library_organization import LibraryError

        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        start_path = (
            os.path.dirname(asset.path) if asset.path
            else os.path.expanduser("~")
        )
        if not os.path.isdir(start_path):
            start_path = os.path.expanduser("~")
        path, _ = _main_window().QFileDialog.getOpenFileName(
            self,
            "Relier le média…",
            start_path,
            "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg)",
        )
        if not path:
            return
        from core.library_organization import LibraryOrganization
        org = LibraryOrganization(self.project)
        try:
            org.relink_asset(asset_id, path)
        except LibraryError as exc:
            _main_window().QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Relier le fichier d'un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_rename_requested(
        self,
        asset_id: str,
        new_name: str,
    ) -> None:
        """Renomme un média (déclenché via menu contextuel)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            org.rename_asset(asset_id, new_name)
        except (LibraryError, LibraryNameError) as exc:
            _main_window().QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Renommer un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_remove_requested(self, asset_id: str) -> None:
        """Supprime un média du projet (les clips restent par défaut)."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        confirm = _main_window().QMessageBox.question(
            self,
            "Supprimer le média",
            f"Supprimer « {asset.name} » de la bibliothèque ?\n"
            "Les clips qui l'utilisent resteront sur la timeline "
            "(ils pointeront vers un média absent).",
            _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
            _main_window().QMessageBox.No,
        )
        if confirm != _main_window().QMessageBox.Yes:
            return
        try:
            org.remove_asset(asset_id, keep_orphan_clips=True)
        except LibraryError as exc:
            _main_window().QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Supprimer un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_occurrences_requested(self, asset_id: str) -> None:
        """Sélectionne la première occurrence du média dans la timeline."""
        from core.library_organization import compute_usage
        usage = compute_usage(self.project, asset_id)
        if not usage.clip_ids:
            return
        clip_id = usage.clip_ids[0]
        # On cherche la piste du clip.
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    self.playhead_seconds = clip.timeline_start
                    self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
                    self.timeline_panel.select_clip(clip_id)
                    self._sync_preview_to_timeline()
                    return
