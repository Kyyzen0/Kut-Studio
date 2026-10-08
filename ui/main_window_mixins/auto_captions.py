"""Méthodes de ``MainWindow`` regroupées : sous-titres automatiques par reconnaissance vocale locale (whisper.cpp).

« Sous-titres automatiques » et « Titres karaoké automatiques » transcrivent la voix (le clip sélectionné s'il porte du
son, sinon le premier clip de la piste Voix) sur la file d'analyse (:class:`core.transcription.TranscriptionJob`, boîte
de progression annulable), puis posent les lignes dans la timeline (:mod:`core.auto_captions`) en une seule entrée
d'historique. Rien ne quitte la machine : whisper.cpp et son modèle sont locaux, réglés dans les préférences.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu, QMessageBox, QProgressDialog

from core.auto_captions import add_karaoke_lines, add_subtitle_lines, caption_lines
from core.beat_detection import plays_at_media_speed
from core.timeline_operations import find_clip
from core.transcription import TranscriptionJob, find_whisper, find_whisper_model, model_directory
from ui import i18n

LOGGER = logging.getLogger(__name__)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window

CAPTION_MODES = ("subtitles", "karaoke")
"""``subtitles`` : piste de sous-titres ; ``karaoke`` : titres animés mot à mot."""


class _PendingTranscription(NamedTuple):
    """Transcription en cours, et ce qu'il faut poser à son arrivée."""

    job: TranscriptionJob
    dialog: QProgressDialog
    timer: QTimer
    sequence_id: str
    voice_id: str
    mode: str
    window: tuple[str, float, float]
    """Média et fenêtre source transcrits : si la voix change pendant la transcription, le résultat ne lui va plus."""


def _source_window(clip) -> tuple[str, float, float]:
    return (clip.asset_id, float(clip.source_in), float(clip.source_out))


class AutoCaptionsMixin:
    """Transcription locale de la voix, en sous-titres ou en titres karaoké."""

    def _init_auto_captions(self, settings) -> None:
        self._whisper_path = str(settings.whisper_path)
        self._whisper_model = str(settings.whisper_model)
        self._transcription: _PendingTranscription | None = None

    def _auto_captions_settings_fields(self) -> dict:
        return {
            "whisper_path": getattr(self, "_whisper_path", ""),
            "whisper_model": getattr(self, "_whisper_model", ""),
        }

    def _auto_captions_shortcut_handlers(self) -> dict:
        return {
            "auto_subtitles": lambda: self.transcribe_voice("subtitles"),
            "auto_karaoke": lambda: self.transcribe_voice("karaoke"),
        }

    def _build_auto_captions_menu(self, parent: QMenu) -> QMenu:
        menu = parent.addMenu(i18n.translate("transcription.menu.title"))
        menu.addAction(self._command_action("auto_subtitles", "transcription.menu.subtitles"))
        menu.addAction(self._command_action("auto_karaoke", "transcription.menu.karaoke"))
        return menu

    # -- préférences -------------------------------------------------------------------------------------------------

    def set_whisper_path(self, path: str) -> None:
        self._whisper_path = str(path or "").strip()
        self._save_auto_captions_settings()

    def set_whisper_model(self, path: str) -> None:
        self._whisper_model = str(path or "").strip()
        self._save_auto_captions_settings()

    def _save_auto_captions_settings(self) -> None:
        try:
            _main_window().save_user_settings(self._settings_snapshot())
        except OSError:
            LOGGER.warning("Transcription : préférences non enregistrées", exc_info=True)

    # -- transcription -----------------------------------------------------------------------------------------------

    def transcribe_voice(self, mode: str = "subtitles") -> None:
        """Transcrit la voix puis pose ses lignes en sous-titres (``subtitles``) ou en titres karaoké (``karaoke``)."""
        if mode not in CAPTION_MODES:
            raise ValueError(f"Mode de sous-titres inconnu : {mode!r}")  # i18n-ignore: erreur de programmation
        if getattr(self, "_transcription", None) is not None:
            self._show_social_status("transcription.message.running")
            return
        voice = self._voice_for_transcription()
        if voice is None:
            self._show_social_status("transcription.message.no_voice")
            return
        clip, asset = voice
        if not plays_at_media_speed(clip):
            self._show_social_status("transcription.message.voice_retimed")
            return
        whisper = find_whisper(getattr(self, "_whisper_path", ""))
        model = find_whisper_model(getattr(self, "_whisper_model", ""))
        if whisper is None or model is None:
            self._explain_missing_whisper(whisper, model)
            return
        from core.export_engine import require_ffmpeg

        try:
            job = TranscriptionJob(
                require_ffmpeg(), whisper, model, asset.path, start=float(clip.source_in),
                duration=float(clip.source_out - clip.source_in), session_id=self.runtime.session_id,
            )
        except (ImportError, ValueError) as error:
            self._report_edit_refused(error)
            return
        dialog = QProgressDialog(
            i18n.translate("transcription.progress.label"), i18n.translate("transcription.progress.cancel"), 0, 100, self,
        )
        dialog.setWindowTitle(i18n.translate("transcription.progress.title"))
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(job.cancel)
        timer = QTimer(self)
        timer.setInterval(100)
        timer.timeout.connect(self._poll_transcription)
        self._transcription = _PendingTranscription(
            job, dialog, timer, self.project.active_sequence.id, clip.id, mode, _source_window(clip),
        )
        self.runtime.schedule_analysis(f"transcription:{clip.id}", job.run)
        timer.start()
        dialog.show()

    def _voice_for_transcription(self):
        """``(clip, média)`` de la voix : le clip sélectionné s'il porte du son, sinon le premier de la piste Voix."""
        assets = {asset.id: asset for asset in self.project.media_assets}

        def audible(clip, track):
            asset = assets.get(clip.asset_id)
            if clip.sequence_id or asset is None or not asset.path:
                return None
            if track.type == "audio" or (track.type == "video" and asset.has_audio):
                return clip, asset
            return None

        selected = self.timeline_panel.selected_clip_id
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == selected:
                    return audible(clip, track)
        voices = [track for track in self.project.tracks if track.type == "audio" and track.audio_role == "voice"]
        for track in voices:
            for clip in sorted(track.clips, key=lambda item: item.timeline_start):
                found = audible(clip, track)
                if found is not None:
                    return found
        return None

    def _explain_missing_whisper(self, whisper: str | None, model: str | None) -> None:
        lines = [i18n.translate("transcription.missing.intro")]
        if whisper is None:
            lines.append(i18n.translate("transcription.missing.tool"))
        if model is None:
            lines.append(i18n.translate("transcription.missing.model", folder=str(model_directory())))
        lines.append(i18n.translate("transcription.missing.preferences"))
        QMessageBox.information(self, i18n.translate("transcription.missing.title"), "\n\n".join(lines))

    def _poll_transcription(self) -> None:
        pending = getattr(self, "_transcription", None)
        if pending is None:
            return
        snapshot = pending.job.snapshot()
        stale = (pending.job.session_id != self.runtime.session_id
                 or self.project.active_sequence.id != pending.sequence_id)
        pending.dialog.setValue(int(snapshot.progress * 100))
        if snapshot.state not in {"finished", "cancelled", "failed"} and not stale:
            return
        pending.timer.stop()
        pending.dialog.close()
        pending.timer.deleteLater()
        pending.dialog.deleteLater()
        self._transcription = None
        if stale:                                          # autre projet ou autre séquence : résultat périmé
            pending.job.cancel()
            self._show_social_status("transcription.message.cancelled")
        elif snapshot.state == "cancelled":
            self._show_social_status("transcription.message.cancelled")
        elif snapshot.state == "failed" or snapshot.result is None:
            self._report_edit_refused(i18n.translate("transcription.message.failed", error=snapshot.message))
        else:
            self._apply_transcription(pending, snapshot.result)

    def _apply_transcription(self, pending: _PendingTranscription, transcript) -> None:
        try:
            voice = find_clip(self.project, pending.voice_id)
        except KeyError as error:
            self._report_edit_refused(error)
            return
        # La boîte n'est pas modale : la voix a pu être rognée, remplacée ou retimée pendant la transcription. Ses mots
        # ne tomberaient plus sur le son (des mots coupés seraient rabattus sur les bords du clip) : on ne pose rien.
        if _source_window(voice) != pending.window or not plays_at_media_speed(voice):
            self._show_social_status("transcription.message.voice_changed")
            return
        lines = caption_lines(voice, transcript.words)
        if not lines:
            self._show_social_status("transcription.message.nothing")
            return
        add = add_subtitle_lines if pending.mode == "subtitles" else add_karaoke_lines
        try:
            created = add(self.project, lines)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        self._record_history(i18n.translate(f"history.transcription.{pending.mode}", count=len(created)))
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._refresh_project_library()
        self._sync_preview_to_timeline()
        self._mark_dirty()
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(i18n.translate(
                "transcription.message.done", count=len(created), code=transcript.language or "?",
            ), 6000)


__all__ = ["AutoCaptionsMixin", "CAPTION_MODES"]
