"""Méthodes de ``MainWindow`` regroupées : vidéo sociale (format vertical, cadrage, Ken Burns, zones de plateforme).

Le menu « Réseaux sociaux » rassemble ce qu'il faut pour monter une vidéo verticale sans quitter l'application. Chaque
action passe par une fonction pure de ``core`` puis enregistre **une** entrée d'historique.
"""

from __future__ import annotations

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QDialog, QMenu

from core.canvas_guides import PLATFORMS
from core.ken_burns import apply_ken_burns, photo_size
from core.project_model import Clip
from core.sequences import set_sequence_format
from core.social_formats import create_social_project
from core.timeline_operations import find_clip
from ui import i18n
from ui.social_dialogs import SequenceSettingsDialog, SocialProjectDialog, platform_items


class SocialMixin:
    """Projet social, réglages de séquence, zones de plateforme, cadrage « remplir », Ken Burns."""

    # -- préférences -----------------------------------------------------------------------------------------------

    def _init_social(self, settings) -> None:
        self._photo_duration = float(settings.photo_duration)
        self._photo_fill = bool(settings.photo_fill)
        self._photo_ken_burns = bool(settings.photo_ken_burns)
        self._platform_zones = str(settings.platform_zones)

    def _social_settings_fields(self) -> dict:
        return {
            "photo_duration": getattr(self, "_photo_duration", 5.0),
            "photo_fill": getattr(self, "_photo_fill", False),
            "photo_ken_burns": getattr(self, "_photo_ken_burns", False),
            "platform_zones": getattr(self, "_platform_zones", ""),
        }

    def _social_shortcut_handlers(self) -> dict:
        return {
            "social_new_project": self.new_social_project,
            "sequence_settings": self.edit_sequence_settings,
            "social_fill_frame": self.toggle_fill_frame_for_selection,
            "social_ken_burns": self.apply_ken_burns_to_selection,
        }

    # -- menu --------------------------------------------------------------------------------------------------------

    def _build_social_menu(self, menu_bar) -> QMenu:
        menu = QMenu(i18n.translate("menu.social"), self)
        menu.setObjectName("social_menu")
        menu.addAction(self._command_action("social_new_project", "social.menu.new_project"))
        menu.addAction(self._command_action("sequence_settings", "social.menu.sequence_settings"))
        menu.addSeparator()
        zones = menu.addMenu(i18n.translate("social.menu.zones"))
        group = QActionGroup(self)
        group.setExclusive(True)
        self._platform_zone_actions = {}
        for platform, _label in platform_items():
            action = self._labelled_action(f"social.platform.{platform or 'none'}")
            action.setCheckable(True)
            action.setChecked(platform == self._platform_zones)
            action.triggered.connect(lambda _checked=False, p=platform: self.set_platform_zones(p))
            group.addAction(action)
            zones.addAction(action)
            self._platform_zone_actions[platform] = action
        menu.addSeparator()
        self._build_beat_menu(menu)
        menu.addSeparator()
        menu.addAction(self._command_action("social_fill_frame", "social.menu.fill_frame"))
        menu.addAction(self._command_action("social_ken_burns", "social.menu.ken_burns"))
        photos = menu.addMenu(i18n.translate("social.menu.photos"))
        for key, attribute in (("social.menu.photo_fill", "_photo_fill"), ("social.menu.photo_ken_burns", "_photo_ken_burns")):
            action = self._labelled_action(key)
            action.setCheckable(True)
            action.setChecked(bool(getattr(self, attribute)))
            action.toggled.connect(lambda checked, name=attribute: self._set_photo_option(name, checked))
            photos.addAction(action)
        self.social_menu = menu
        menu_bar.addMenu(menu)
        return menu

    def _set_photo_option(self, attribute: str, value: bool) -> None:
        setattr(self, attribute, bool(value))
        self._persist_social_preferences()

    def _persist_social_preferences(self) -> None:
        """Préférences de la vidéo sociale (hors du ``.kut``) : un échec d'écriture n'interrompt pas l'édition."""
        import ui.main_window as main_window

        try:
            main_window.save_user_settings(self._settings_snapshot())
        except OSError:
            pass

    # -- zones de plateforme ---------------------------------------------------------------------------------------

    def set_platform_zones(self, platform: str) -> None:
        """Montre (ou cache, ``""``) les zones masquées par l'interface d'une plateforme dans le viewer."""
        platform = platform if platform in PLATFORMS else ""
        changed = platform != self._platform_zones
        self._platform_zones = platform
        panel = getattr(self, "preview_panel", None)                # le menu est construit avant le viewer
        if panel is not None:
            panel.overlay.set_platform_zones(platform)
        action = getattr(self, "_platform_zone_actions", {}).get(platform)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        if changed:
            self._persist_social_preferences()

    # -- nouveau projet, séquence ----------------------------------------------------------------------------------

    def new_social_project(self) -> None:
        """« Nouveau projet réseaux sociaux… » : format vertical (ou autre), cadence, zones de plateforme."""
        dialog = SocialProjectDialog(self, templates=self._social_template_entries())
        if dialog.exec() != QDialog.Accepted:
            return
        choice = dialog.choice()
        if not self._confirm_discard_changes():
            return
        names = {track_id: i18n.translate(f"social.track.{track_id.lower()}") for track_id in ("V1", "G1", "A1", "A2", "A3")}
        project = self._build_social_project(choice, names)
        self._install_new_project(project)
        self.set_platform_zones(choice.platform)

    def _social_template_entries(self) -> list[tuple[str, str]]:
        """Templates proposés à la création (aucun tant qu'aucun n'est déclaré)."""
        return []

    def _build_social_project(self, choice, names):
        return create_social_project(choice.format_id, choice.fps, name=choice.name, track_names=names)

    def edit_sequence_settings(self) -> None:
        """« Réglages de la séquence… » : taille du cadre et cadence de la séquence active."""
        sequence = self.project.active_sequence
        dialog = SequenceSettingsDialog(sequence.width, sequence.height, sequence.fps, self)
        if dialog.exec() != QDialog.Accepted:
            return
        width, height, fps = dialog.values()
        if (width, height, float(fps)) == (sequence.width, sequence.height, float(sequence.fps)):
            return
        try:
            set_sequence_format(self.project, sequence.id, width, height, fps)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        self._record_history(i18n.translate("history.social.sequence_settings"))
        engine = getattr(self, "preview_engine", None)
        if engine is not None:
            try:
                engine.cancel_all()
            except Exception:
                pass
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._mark_dirty()

    # -- cadrage et Ken Burns sur la sélection ---------------------------------------------------------------------

    def _selected_clips(self) -> list[Clip]:
        timeline = self.timeline_panel
        ids = list(timeline.selected_clip_ids) or ([timeline.selected_clip_id] if timeline.selected_clip_id else [])
        clips = []
        for clip_id in ids:
            try:
                clips.append(find_clip(self.project, clip_id))
            except KeyError:
                continue
        return clips

    def _video_clips_of_selection(self) -> list[Clip]:
        video_ids = {clip.id for track in self.project.tracks if track.type == "video" for clip in track.clips}
        return [clip for clip in self._selected_clips() if clip.id in video_ids and not clip.sequence_id]

    def toggle_fill_frame_for_selection(self) -> None:
        """« Remplir le cadre » : bascule le cadrage des clips vidéo sélectionnés (tous suivent le premier)."""
        clips = self._video_clips_of_selection()
        if not clips:
            self._show_social_status("social.message.no_video")
            return
        fill = not clips[0].transform.fill
        for clip in clips:
            clip.transform = clip.transform.with_property("fill", fill)
        self._after_social_edit(clips, "history.social.fill_on" if fill else "history.social.fill_off")

    def apply_ken_burns_to_selection(self) -> None:
        """« Ken Burns » : un mouvement lent sur les clips vidéo et photos sélectionnés."""
        clips = self._selected_clips()
        if not apply_ken_burns(clips):
            self._show_social_status("social.message.no_photo")
            return
        self._after_social_edit(clips, "history.social.ken_burns")

    def _after_social_edit(self, clips: list[Clip], history_key: str) -> None:
        self._record_history(i18n.translate(history_key))
        for clip in clips:
            self._invalidate_preview_for_clip(clip.id)
        self._reload_timeline_preserving_selection(clips[0].id)
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def _show_social_status(self, key: str) -> None:
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(i18n.translate(key), 4000)

    # -- photos importées ------------------------------------------------------------------------------------------

    def _place_imported_photo(self, clip: Clip, image_width: int, image_height: int) -> None:
        """Taille d'une photo importée (remplir ou tenir dans le cadre) puis Ken Burns si demandé."""
        from core.graphics import update_graphic

        width, height = photo_size(image_width, image_height, self.project.width, self.project.height,
                                   fill=self._photo_fill)
        update_graphic(clip, "width", width)
        update_graphic(clip, "height", height)
        if self._photo_ken_burns:
            apply_ken_burns([clip])


__all__ = ["SocialMixin"]
