"""Méthodes de ``MainWindow`` regroupées : recording."""

from __future__ import annotations

from core.audio_recorder import AudioRecorderError, pcm_duration, write_wav
from core.project_model import MediaAsset
from core.timeline_operations import add_clip_to_track


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class RecordingMixin:
    """Mixin de ``MainWindow`` (recording)."""

    def on_record_toggled(self, checked: bool) -> None:
        """Démarre ou arrête le microphone sur les pistes audio armées."""
        if checked:
            armed = [
                track.id
                for track in self.project.tracks
                if track.type == "audio" and track.armed and not track.locked
            ]
            if not armed:
                self._set_record_button(False)
                _main_window().QMessageBox.information(
                    self,
                    "Enregistrement",
                    "Armez une piste audio avant d'enregistrer.",
                )
                return
            try:
                self._audio_recorder.start()
            except AudioRecorderError as exc:
                self._set_record_button(False)
                _main_window().QMessageBox.critical(self, "Enregistrement", str(exc))
                return
            self._record_origin = float(self.playhead_seconds)
            self._record_tracks = armed
            return
        if not self._audio_recorder.is_recording:
            return
        pcm, rate, channels = self._audio_recorder.stop()
        self._place_recording(pcm, rate, channels)

    def _set_record_button(self, checked: bool) -> None:
        button = self.timeline_panel.record_button
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)

    def _place_recording(
        self, pcm: bytes, sample_rate: int, channels: int, *, quiet: bool = False
    ) -> None:
        import uuid
        from pathlib import Path

        duration = pcm_duration(pcm, sample_rate, channels)
        if duration < 0.05:
            if not quiet:
                _main_window().QMessageBox.information(
                    self,
                    "Enregistrement",
                    "L'enregistrement est trop court pour devenir un clip.",
                )
            return
        if self.current_project_path:
            folder = Path(self.current_project_path).parent / "enregistrements"
        else:
            folder = Path.home() / "Movies" / "Kut-Studio"
        target = folder / f"prise-{uuid.uuid4().hex[:8]}.wav"
        try:
            write_wav(str(target), pcm, sample_rate, channels)
        except OSError as exc:
            if not quiet:
                _main_window().QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        asset = MediaAsset(
            id=f"rec-{uuid.uuid4().hex[:8]}",
            path=str(target),
            name=target.stem,
            duration=duration,
            width=0,
            height=0,
            fps=0.0,
            media_type="audio",
            has_audio=True,
        )
        self.project.media_assets.append(asset)
        for track_id in self._record_tracks:
            try:
                add_clip_to_track(self.project, asset.id, track_id, self._record_origin)
            except (KeyError, ValueError) as exc:
                self._report_edit_refused(exc)
        self._record_history("Enregistrer une prise")
        self._refresh_project_library()
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._mark_dirty()
