"""Méthodes de ``MainWindow`` regroupées : audio de la vidéo sociale (SFX de la bibliothèque, ducking prêt à l'emploi).

Chaque action passe par une fonction pure de ``core`` (``core.sfx_placement``, ``core.audio_automation``) puis
enregistre **une** entrée d'historique : poser douze whooshes sur douze cuts s'annule d'un seul Ctrl+Z.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

from core.audio_automation import DUCKING_PRESETS, AudioAutomationError, duck_music_under_voice
from core.sfx_placement import place_sfx, place_sfx_on_cuts
from ui import i18n
from ui.sfx_library import ON_CUTS_DEFAULT

DEFAULT_DUCKING = "voice_over_music"


class SocialAudioMixin:
    """Bibliothèque SFX (poser, poser à chaque cut) et presets de ducking."""

    def _social_audio_shortcut_handlers(self) -> dict:
        return {
            "sfx_on_cuts": lambda: self.place_sfx_on_cuts_of_track(list(ON_CUTS_DEFAULT)),
            "duck_music": lambda: self.duck_music_under_voice(DEFAULT_DUCKING),
        }

    def _connect_sfx_library(self) -> None:
        self.project_panel.sfx_add_requested.connect(self.add_sfx_at_playhead)
        self.project_panel.sfx_on_cuts_requested.connect(self.place_sfx_on_cuts_of_track)

    def _build_social_audio_menu(self, menu: QMenu) -> None:
        audio = menu.addMenu(i18n.translate("social.menu.audio"))
        audio.addAction(self._command_action("sfx_on_cuts", "sfx.menu.on_cuts"))
        ducking = audio.addMenu(i18n.translate("ducking.menu.title"))
        for preset in DUCKING_PRESETS:
            if preset == DEFAULT_DUCKING:                   # le preset par défaut porte la commande (raccourci possible)
                ducking.addAction(self._command_action("duck_music", f"ducking.preset.{preset}"))
                continue
            action = self._labelled_action(f"ducking.preset.{preset}")
            action.triggered.connect(lambda _checked=False, p=preset: self.duck_music_under_voice(p))
            ducking.addAction(action)

    # -- SFX -----------------------------------------------------------------------------------------------------------

    def add_sfx_at_playhead(self, sfx_id: str) -> None:
        """Pose le SFX pour que son ancre (centre du whoosh, pic du passage…) tombe sur la tête de lecture."""
        clip = place_sfx(self.project, sfx_id, float(self.playhead_seconds))
        self._after_sfx_edit(i18n.translate("history.sfx.add", name=i18n.translate(f"sfx.name.{sfx_id}")), clip.id)

    def place_sfx_on_cuts_of_track(self, sfx_ids: list) -> None:
        """Un SFX à chaque cut de la piste vidéo du clip sélectionné (V1 sinon), les sons tournant."""
        selected = self._video_clips_of_selection()
        track_id = selected[0].track_id if selected else next(
            (track.id for track in self.project.tracks if track.type == "video"), None)
        if track_id is None:
            self._show_social_status("social.message.no_video")
            return
        try:
            clips = place_sfx_on_cuts(self.project, track_id, [str(sfx_id) for sfx_id in sfx_ids])
        except ValueError:
            self._show_social_status("impact.message.no_cut")
            return
        except KeyError as error:
            self._report_edit_refused(error)
            return
        self._after_sfx_edit(i18n.translate("history.sfx.on_cuts", count=len(clips)), None)

    def _after_sfx_edit(self, label: str, clip_id: str | None) -> None:
        self._record_history(label)
        self._refresh_project_library()
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._mark_dirty()

    # -- ducking -------------------------------------------------------------------------------------------------------

    def duck_music_under_voice(self, preset: str) -> None:
        """La musique baisse sous la voix (chaque piste « musique » sous chaque piste « voix »), avec le preset."""
        try:
            duck_music_under_voice(self.project, preset)
        except AudioAutomationError:
            self._show_social_status("ducking.message.no_roles")
            return
        self._record_audio_change(i18n.translate("history.ducking", preset=i18n.translate(f"ducking.preset.{preset}")))
        self._sync_preview_to_timeline()
        self._mark_dirty()


__all__ = ["SocialAudioMixin"]
