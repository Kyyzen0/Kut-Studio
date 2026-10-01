"""Méthodes de ``MainWindow`` regroupées : media_import."""

from __future__ import annotations

import os
from core.media_cache import cached_probe
from core.media_probe import MediaProbeError
from core.timeline_operations import add_clip_to_track


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class MediaImportMixin:
    """Mixin de ``MainWindow`` (media_import)."""

    def on_asset_dropped(self, asset_id: str, track_id: str, timeline_start: float) -> None:
        """Ajoute le média glissé sur la piste ciblée à la position donnée.

        La compatibilité asset / piste est vérifiée ; un dépôt invalide
        n'est pas appliqué au projet.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        try:
            clip = add_clip_to_track(self.project, asset_id, track_id, timeline_start)
        except (KeyError, ValueError):
            return
        self._record_history(
            f"Déposer « {asset.name} » sur {track_id}"
        )
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self._mark_dirty()

    def import_media_via_dialog(self) -> None:
        """Ouvre un dialogue d'import et importe chaque fichier sélectionné.

        Les filtres du dialogue couvrent les formats vidéo et audio
        acceptés par ``probe_media``. Chaque fichier est analysé pour
        déterminer son type (vidéo, audio) avant d'être ajouté au projet.
        """
        paths, _ = _main_window().QFileDialog.getOpenFileNames(
            self,
            "Importer des médias",
            os.path.expanduser("~/Movies"),
            "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        )
        if not paths:
            return
        for path in paths:
            self.import_media_to_project(path)

    def import_media_to_project(self, path: str) -> bool:
        """Importe ``path`` comme ``MediaAsset`` réel (vidéo ou audio).

        L'opération est idempotente pour un même chemin normalisé : un
        doublon est ignoré silencieusement. En cas d'échec de la sonde,
        ni le projet ni la bibliothèque ne sont modifiés ; une boîte de
        dialogue claire est affichée à l'utilisateur.
        """
        normalized = os.path.normpath(os.path.abspath(path))
        for asset in self.project.media_assets:
            if os.path.normpath(os.path.abspath(asset.path)) == normalized:
                # Doublon silencieux : on conserve le projet intact et on
                # met le focus sur l'asset existant dans la bibliothèque.
                self.project_panel.select_asset(asset.id)
                if asset.media_type == "video":
                    self.preview_panel.load_video(asset.path)
                return False

        try:
            asset = cached_probe(self.runtime.cache, path, _main_window().probe_media)
        except MediaProbeError as exc:
            _main_window().QMessageBox.critical(
                self,
                "Import impossible",
                f"Impossible d'importer le média :\n\n{path}\n\n{exc}",
            )
            return False

        # La sonde réelle fournit déjà le nom de fichier, mais les
        # intégrations externes peuvent retourner un ``MediaAsset`` avec
        # le chemin complet comme libellé. Le nom affiché ne dépend jamais
        # du séparateur POSIX : sous Windows, ``split('/')`` laisserait le
        # chemin complet dans la bibliothèque et dans les .kut.
        display_name = os.path.basename(str(asset.path).replace("\\", "/"))
        if not display_name:
            display_name = os.path.basename(str(path).replace("\\", "/"))
        if display_name:
            asset.name = display_name
        self.project.media_assets.append(asset)
        self._refresh_project_library()
        self.project_panel.select_asset(asset.id)
        if asset.media_type == "video":
            self.preview_panel.load_video(asset.path)
        self._record_history(f"Importer le média « {asset.name} »")
        self._mark_dirty()
        return True

    def import_video_to_project(self, path: str) -> bool:
        """Délègue à :meth:`import_media_to_project` (alias historique)."""
        return self.import_media_to_project(path)

    def preview_media_asset(self, asset_id: str) -> None:
        """Prévisualise le ``MediaAsset`` identifié par ``asset_id``.

        Les assets audio ne déclenchent pas de prévisualisation vidéo.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        if asset.media_type == "video":
            self.preview_panel.load_video(asset.path)

    def add_asset_to_timeline(self, asset_id: str) -> None:
        """Ajoute le média sélectionné à la piste adaptée à son type.

        Routing :

        - asset ``video`` → piste ``V1`` (créée si absente) ;
        - asset ``audio`` → piste ``A1`` (créée si absente) ;
        - autres types → erreur claire.

        Le clip est créé à la position du playhead ; si le playhead est
        hors limites (typiquement après un reset à zéro sur un projet
        vide), il est ramené à ``0.0``.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            _main_window().QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Média '{asset_id}' introuvable dans le projet.",
            )
            return

        if asset.media_type == "video":
            target_track_id = "V1"
        elif asset.media_type == "audio":
            target_track_id = "A1"
        else:
            _main_window().QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Le type de média '{asset.media_type}' ne peut pas être "
                "ajouté à la timeline depuis le panneau de bibliothèque.",
            )
            return

        if not any(track.id == target_track_id for track in self.project.tracks):
            _main_window().QMessageBox.critical(
                self,
                "Ajout impossible",
                f"La piste '{target_track_id}' est absente du projet courant.",
            )
            return

        timeline_start = self.timeline_panel.playhead_seconds
        try:
            new_clip = add_clip_to_track(
                self.project,
                asset_id,
                target_track_id,
                timeline_start,
            )
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Impossible d'ajouter le média à la timeline :\n\n{exc}",
            )
            return

        # Rafraîchit la projection (qui inclut le nouveau clip).
        self._record_history(f"Ajouter le clip « {new_clip.label or new_clip.id} »")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    def add_asset_to_v1(self, asset_id: str) -> None:
        """Délègue à :meth:`add_asset_to_timeline` (alias historique)."""
        self.add_asset_to_timeline(asset_id)

    def _refresh_project_library(self) -> None:
        """Synchronise ``ProjectPanel`` avec ``self.project.media_assets``.

        Met également à jour la bibliothèque de sous-titres de l'onglet
        Texte avec les clips activés des pistes ``subtitle``, l'organisation
        de la bibliothèque (dossiers / tags / affectations) et les badges
        d'utilisation des médias (tâche 25).
        """
        # Les médias techniques des calques G vivent dans le projet pour
        # garantir l'intégrité des clips, mais leurs modèles se choisissent
        # dans la bibliothèque Graphiques : ne pas les dupliquer dans Médias.
        self.project_panel.set_assets(
            [
                asset for asset in self.project.media_assets
                if asset.media_type != "graphic"
            ]
        )
        subtitle_clips = [
            clip
            for track in self.project.tracks
            if track.type == "subtitle"
            for clip in track.clips
            if clip.enabled and (clip.text or "").strip()
        ]
        self.project_panel.set_subtitle_clips(subtitle_clips)
        # Organisation de la bibliothèque (tâche 25) : on attache le
        # service au panneau et on rafraîchit les badges d'usage.
        if not hasattr(self, "_library_organization") or (
            self._library_organization is not None
            and self._library_organization.project is not self.project
        ):
            from core.library_organization import LibraryOrganization
            self._library_organization = LibraryOrganization(self.project)
        else:
            from core.library_organization import LibraryOrganization
            self._library_organization = LibraryOrganization(self.project)
        self.project_panel.set_library(self._library_organization)
        self.project_panel.set_usage_for_assets()
        # La bibliothèque d'effets suit la sélection courante.
        self._sync_effects_library_context()
