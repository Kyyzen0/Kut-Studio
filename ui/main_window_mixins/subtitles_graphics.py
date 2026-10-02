"""Méthodes de ``MainWindow`` regroupées : subtitles_graphics."""

from __future__ import annotations

import os
from PySide6.QtCore import QTimer
from core.project_model import Clip
from core.subtitle_io import load_srt, save_srt
from core.text_style import TextStyle, default_text_style
from core.timeline_editing import apply_solo
from core.timeline_operations import (
    add_subtitle_clip,
    find_clip,
    subtitle_cues_from_project,
)
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class SubtitlesGraphicsMixin:
    """Mixin de ``MainWindow`` (subtitles_graphics)."""

    def _schedule_subtitle_history_record(
        self, clip_id: str, new_text: str | None = None
    ) -> None:
        """Programme l'enregistrement d'un snapshot après 500 ms d'inactivité.

        ``history_before_text`` capture l'état du projet juste avant la
        saisie courante : si plusieurs frappes surviennent avant le
        déclenchement du timer, on ne crée qu'un seul snapshot couvrant
        toute la saisie.
        """
        if not hasattr(self, "_subtitle_edit_timer"):
            from PySide6.QtCore import QTimer

            self._subtitle_edit_timer = QTimer(self)
            self._subtitle_edit_timer.setSingleShot(True)
            self._subtitle_edit_timer.setInterval(500)
            self._subtitle_edit_timer.timeout.connect(
                self._flush_subtitle_history_record
            )
            self._subtitle_edit_pending = None
            self._subtitle_edit_history_before = None
        if self._subtitle_edit_pending not in (None, clip_id):
            self._flush_subtitle_history_record()
        if self._subtitle_edit_pending != clip_id:
            # Le snapshot final sera capturé au flush, après la saisie.
            self._subtitle_edit_history_before = None
            self._subtitle_edit_pending = clip_id
        self._subtitle_edit_timer.start()

    def _flush_subtitle_history_record(self) -> None:
        """Enregistre le texte final après le debounce."""
        if getattr(self, "_subtitle_edit_pending", None) is None:
            return
        timer = getattr(self, "_subtitle_edit_timer", None)
        if timer is not None:
            timer.stop()
        self._subtitle_edit_history_before = None
        self._subtitle_edit_pending = None
        self.history.record(self.project, "Modifier un sous-titre")
        self._refresh_undo_redo_state()

    def update_subtitle_from_editor(self, new_text: str | None = None):
        """Applique le texte saisi dans l'éditeur de l'inspecteur.

        Delegue à :meth:`on_subtitle_content_changed` qui porte la
        logique d'historique (debounce) — ce handler reste pour la
        compatibilité avec les appels historiques.
        """
        if self.active_subtitle_clip is None:
            return
        if new_text is None:
            new_text = self.properties_panel.subtitle_editor.content()
        self.on_subtitle_content_changed(self.active_subtitle_clip.id, new_text)

    def update_subtitle_overlay(self, seconds, active_clips=None):
        """Affiche le sous-titre actif (borne demi-ouverte ``start <= t < end``).

        Plusieurs sous-titres superposés sont départagés par leur
        ordre dans la timeline : le dernier gagne (comportement
        déterministe). ``active_clips`` évite une seconde évaluation
        quand l'appelant vient déjà de synchroniser l'aperçu.
        """
        if active_clips is None:
            try:
                active_clips = self._ensure_timeline_index().active_at(
                    self.project, seconds
                )
            except (ValueError, KeyError):
                active_clips = []
        active_clips = apply_solo(self.project, active_clips)
        subtitle_clips = [c for c in active_clips if c.track_type == "subtitle"]
        if not subtitle_clips:
            self.preview_panel.clear_subtitle()
            return
        view = subtitle_clips[-1]
        text = view.text.strip() if view.text else ""
        if not text:
            self.preview_panel.clear_subtitle()
            return
        # La vue de timeline porte le style du clip : on l'applique tel
        # quel pour que le preview reflète l'inspecteur sans relance.
        style = getattr(view, "text_style", None) or default_text_style()
        self.preview_panel.set_subtitle(text, style)

    def export_subtitles_to_path(self, file_path: str) -> None:
        """Exporte tous les sous-titres actifs vers ``file_path`` (.srt)."""
        cues = subtitle_cues_from_project(self.project)
        save_srt(cues, file_path)
        self.subtitle_file = file_path

    def import_subtitles_from_path(self, file_path: str) -> list[Clip]:
        """Importe les sous-titres d'un .srt et crée les clips dans S1.

        Les cues non vides sont ajoutés après les sous-titres déjà
        présents ; la liste des clips créés est retournée.
        """
        cues = [cue for cue in load_srt(file_path) if cue.text.strip()]
        if not cues:
            return []
        created_clips: list[Clip] = []
        try:
            for cue in cues:
                clip = add_subtitle_clip(
                    self.project,
                    text=cue.text,
                    timeline_start=cue.start,
                    duration=cue.end - cue.start,
                )
                created_clips.append(clip)
        except (KeyError, ValueError):
            if created_clips:
                self._record_history(
                    f"Importer le SRT ({len(created_clips)} sous-titres)"
                )
                self._reload_timeline_preserving_selection()
                self._update_timeline_duration()
                self._refresh_project_library()
                self._mark_dirty()
            raise
        self._record_history(f"Importer le SRT ({len(cues)} sous-titres)")
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._refresh_project_library()
        self._mark_dirty()
        return created_clips

    def add_subtitle_at_playhead(self, text: str, duration: float) -> None:
        """Ajoute un sous-titre au playhead courant et le sélectionne."""
        try:
            clip = add_subtitle_clip(
                self.project,
                text=text,
                timeline_start=self.playhead_seconds,
                duration=duration,
            )
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                "Sous-titre impossible",
                f"Impossible d'ajouter le sous-titre :\n\n{exc}",
            )
            return
        self._record_history("Ajouter un sous-titre")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self._mark_dirty()

    def add_graphic_at_playhead(self, graphic_type: str) -> None:
        """Ajoute un calque généré sur G1 et ouvre son inspecteur."""
        from core.graphics import add_graphic_clip

        try:
            clip = add_graphic_clip(
                self.project,
                graphic_type,
                timeline_start=self.playhead_seconds,
                duration=5.0,
            )
        except (FileNotFoundError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, "Graphique", str(exc))
            return
        self._record_history("Ajouter un calque graphique")
        self._reload_timeline_preserving_selection(clip.id)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self.on_clip_selected(clip.id)
        self.properties_panel._select_inspector_tab(4)
        self._invalidate_preview_for_clip(clip.id)
        self._mark_dirty()

    def import_graphic_image(self) -> None:
        """Importe une image comme calque graphique animable."""
        path, _ = _main_window().QFileDialog.getOpenFileName(
            self,
            "Importer une image graphique",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
        )
        if not path:
            return
        from core.graphics import add_graphic_clip

        try:
            clip = add_graphic_clip(
                self.project,
                "image",
                timeline_start=self.playhead_seconds,
                duration=5.0,
                source_path=path,
            )
            # Conserve le ratio et la taille intrinsèque de l'image, sans
            # dépasser le cadre du projet. Le modèle reste indépendant de Qt ;
            # cette lecture de métadonnées appartient donc à la couche UI.
            from PySide6.QtGui import QImageReader

            image_size = QImageReader(path).size()
            if image_size.isValid():
                source_width = max(1, image_size.width())
                source_height = max(1, image_size.height())
                ratio = min(
                    1.0,
                    self.project.width / source_width,
                    self.project.height / source_height,
                )
                from core.graphics import update_graphic

                update_graphic(clip, "width", round(source_width * ratio))
                update_graphic(clip, "height", round(source_height * ratio))
                asset = next(
                    item for item in self.project.media_assets
                    if item.id == clip.asset_id
                )
                asset.width = clip.graphic.width
                asset.height = clip.graphic.height
        except (FileNotFoundError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, "Graphique", str(exc))
            return
        self._record_history("Importer une image graphique")
        self._reload_timeline_preserving_selection(clip.id)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self.on_clip_selected(clip.id)
        self.properties_panel._select_inspector_tab(4)
        self._invalidate_preview_for_clip(clip.id)
        self._mark_dirty()

    def on_graphic_property_changed(
        self, clip_id: str, field_name: str, value: object
    ) -> None:
        """Applique une propriété intrinsèque du calque avec Undo/Redo."""
        from core.graphics import update_graphic

        if not bool(getattr(self, "_graphic_session_active", False)):
            self._finalize_transform_session()
        clip, track = self._find_clip_and_track(clip_id)
        if (
            clip is None
            or track is None
            or track.type != "graphics"
            or bool(track.locked)
        ):
            return
        try:
            before = clip.graphic
            # Propriété animée (corps, approche…) : la valeur saisie devient
            # l'image-clé à la tête de lecture, comme pour le transform.
            from core import keyframe_editing
            from core.mograph_targets import GRAPHIC_PROPERTY_SPECS, graphic_property_id

            property_id = graphic_property_id(field_name)
            if property_id in GRAPHIC_PROPERTY_SPECS and keyframe_editing.is_animated(clip, property_id):
                local = keyframe_editing.snap_to_frame(
                    clip, float(self.playhead_seconds) - clip.timeline_start, float(self.project.fps)
                )
                keyframe_editing.set_value_at(self.project, clip_id, property_id, local, float(value))
                self._schedule_graphic_history(clip_id)
                self._invalidate_preview_for_clip(clip_id)
                self._schedule_graphic_preview_refresh()
                return
            updated = update_graphic(clip, field_name, value)
        except (TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"Modification graphique refusée : {exc}", 5000)
            return
        if updated is before:
            return
        self._schedule_graphic_history(clip_id)
        self._invalidate_preview_for_clip(clip_id)
        self._schedule_graphic_preview_refresh()

    def _schedule_graphic_history(self, clip_id: str) -> None:
        """Regroupe une saisie ou une rafale de spinbox en un seul Undo."""
        active = bool(getattr(self, "_graphic_session_active", False))
        previous_clip = getattr(self, "_graphic_session_clip_id", None)
        if active and previous_clip != clip_id:
            self._finalize_graphic_history()
            active = False
        self._graphic_session_active = True
        self._graphic_session_clip_id = clip_id
        if not active:
            self._graphic_session_label = "Modifier un calque graphique"
        timer = getattr(self, "_graphic_session_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._finalize_graphic_history)
            self._graphic_session_timer = timer
        timer.start(400)
        self._mark_dirty()

    def _finalize_graphic_history(self) -> None:
        if not bool(getattr(self, "_graphic_session_active", False)):
            return
        timer = getattr(self, "_graphic_session_timer", None)
        if timer is not None:
            timer.stop()
        self._graphic_session_active = False
        clip_id = getattr(self, "_graphic_session_clip_id", None)
        label = getattr(
            self, "_graphic_session_label", "Modifier un calque graphique"
        )
        self._record_history(label)
        if clip_id:
            self._reload_timeline_preserving_selection(clip_id)

    def _schedule_graphic_preview_refresh(self) -> None:
        """Évite de relancer FFmpeg à chaque caractère saisi."""
        timer = getattr(self, "_graphic_preview_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._sync_preview_to_timeline)
            self._graphic_preview_timer = timer
        timer.start(120)

    def import_subtitles_via_dialog(self) -> None:
        path, _ = _main_window().QFileDialog.getOpenFileName(
            self,
            "Importer des sous-titres",
            os.path.expanduser("~"),
            "Sous-titres (*.srt)",
        )
        if not path:
            return
        try:
            self.import_subtitles_from_path(path)
        except (OSError, ValueError, KeyError) as exc:
            _main_window().QMessageBox.critical(
                self,
                "Import SRT impossible",
                f"Impossible d'importer les sous-titres :\n\n{exc}",
            )

    def export_subtitles_via_dialog(self) -> None:
        path, _ = _main_window().QFileDialog.getSaveFileName(
            self,
            "Enregistrer les sous-titres",
            os.path.expanduser("~/subtitles.srt"),
            "Sous-titres (*.srt)",
        )
        if not path:
            return
        try:
            self.export_subtitles_to_path(path)
        except OSError as exc:
            _main_window().QMessageBox.critical(
                self,
                "Export SRT impossible",
                f"Impossible d'enregistrer les sous-titres :\n\n{exc}",
            )

    def on_subtitle_clip_selected(self, clip_id: str) -> None:
        """Sélectionne un sous-titre depuis la bibliothèque et synchronise le playhead."""
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        # Le playhead se positionne au début du sous-titre sélectionné.
        self.seek_to_position(clip.timeline_start)
        # On rafraîchit la sélection dans le panneau Propriétés.
        self.timeline_panel.select_clip(clip_id)
        self.on_clip_selected(clip_id)
        # Synchronise l'overlay du preview avec le style du clip.
        self._refresh_subtitle_overlay()

    def on_subtitle_content_changed(self, clip_id: str, content: str) -> None:
        """Capture le contenu du sous-titre dans l'historique.

        Le regroupement des frappes est assuré par le debounce existant
        (``_schedule_subtitle_history_record``) : une session de saisie
        ne produit qu'une entrée d'historique.
        """
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text == content:
            return
        clip.text = content
        self._schedule_subtitle_history_record(clip_id)
        self._mark_dirty()
        # ``set_project`` efface la sélection. On la repose sans
        # ``show_clip`` : recharger l'inspecteur pendant la frappe
        # remettrait le curseur de l'éditeur au début.
        playhead = self.playhead_seconds
        self.timeline_panel.set_project(self.project)
        if self.timeline_panel.find_view_by_id(clip_id) is not None:
            self.timeline_panel._set_selection([clip_id], clip_id, announce=False)
            view = self.timeline_panel.find_view_by_id(clip_id)
            if getattr(view, "track_type", None) == "subtitle":
                self.active_subtitle_clip = view
        if self.playhead_seconds != playhead:
            self.playhead_seconds = playhead
        self.update_subtitle_overlay(self.playhead_seconds)

    def on_subtitle_style_changed(self, clip_id: str, style: TextStyle) -> None:
        """Applique immédiatement le style (Undo/Redo friendly)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text_style == style:
            return
        clip.text_style = style
        self._record_history("Modifier le style du sous-titre")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()

    def on_subtitle_style_reset(self, clip_id: str) -> None:
        """Réinitialise le style au standard (Undo/Redo friendly)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text_style == default_text_style():
            return
        clip.text_style = default_text_style()
        self._record_history("Réinitialiser le style du sous-titre")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()
        self.statusBar().showMessage(
            i18n.translate("text.library.reset_style"), 3000
        )

    def _refresh_subtitle_overlay(self) -> None:
        """Synchronise l'overlay preview avec le sous-titre courant."""
        preview = getattr(self, "preview_panel", None)
        if preview is None:
            return
        clip = self.active_subtitle_clip
        if clip is None:
            preview.clear_subtitle()
            return
        from core.timeline_operations import find_clip

        try:
            model_clip = find_clip(self.project, clip.id)
        except KeyError:
            preview.clear_subtitle()
            return
        text = (model_clip.text or "").strip()
        if not text:
            preview.clear_subtitle()
            return
        preview.set_subtitle(text, model_clip.text_style)

    def _resolve_subtitle_target_clip_id(self) -> str | None:
        """Identifiant du clip cible pour appliquer un modèle."""
        if self.active_subtitle_clip is not None:
            return self.active_subtitle_clip.id
        # Sinon, premier clip de sous-titre visible.
        for track in self.project.tracks:
            if track.type != "subtitle" or not track.visible:
                continue
            for clip in track.clips:
                if clip.enabled:
                    return clip.id
        return None
