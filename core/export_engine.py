import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal


_ffmpeg_path = shutil.which("ffmpeg")
if _ffmpeg_path is None:
    raise ImportError("ffmpeg est requis pour l'export Kut-Studio mais est introuvable dans le PATH.")


class ExportFormat(Enum):
    """Describe the supported video container and codec combinations."""

    MP4_H264 = ("mp4", "h264", "medium", 18)
    MOV_PRORES = ("mov", "prores_ks", "", 3)
    MOV_H264 = ("mov", "h264", "medium", 18)

    @property
    def container(self) -> str:
        """Return the output container extension."""
        return self.value[0]

    @property
    def codec(self) -> str:
        """Return the ffmpeg video codec name."""
        return self.value[1]

    @property
    def preset(self) -> str:
        """Return the default ffmpeg preset for the format."""
        return self.value[2]

    @property
    def quality_value(self) -> int:
        """Return the default CRF or codec profile value."""
        return self.value[3]


@dataclass(frozen=True)
class ExportPreset:
    """Describe the output resolution, quality, and audio bitrate."""

    name: str
    resolution: tuple[int, int]
    crf: int
    audio_bitrate: str


@dataclass(frozen=True)
class ExportRequest:
    """Describe one export operation and its timeline inputs."""

    clips: list[dict[str, object]]
    output_path: str
    format: ExportFormat
    preset: ExportPreset
    fps: int = 30

    def __post_init__(self) -> None:
        """Reject invalid export settings before an ffmpeg process is started."""
        if self.fps <= 0:
            raise ValueError("La fréquence d'images doit être supérieure à zéro.")
        width, height = self.preset.resolution
        if width <= 0 or height <= 0:
            raise ValueError("La résolution d'export doit être positive.")


class ExportEngine(QObject):
    """Run a non-blocking ffmpeg export and report its state through Qt signals."""

    progress_changed = Signal(int)
    status_changed = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_progress)
        self._process.readyReadStandardError.connect(self._read_error)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._request: ExportRequest | None = None
        self._error_output = ""
        self._progress_buffer = ""
        self._duration_seconds = 0.0
        self._cancel_requested = False

    def start(self, request: ExportRequest) -> None:
        """Start an asynchronous ffmpeg process for the requested export."""
        if self._process.state() != QProcess.NotRunning:
            self.failed.emit("Un export est déjà en cours.")
            return

        try:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="kut-studio-export-")
            exportable_clips = self._filter_exportable_clips(request.clips)
            self._duration_seconds = sum(
                max(0.0, float(clip.get("end", 0.0)) - float(clip.get("start", 0.0)))
                for clip in exportable_clips
            )
            command = self._build_command(request, exportable_clips)
        except (OSError, ValueError) as error:
            self._cleanup_temporary_directory()
            self.failed.emit(str(error))
            return

        self._request = request
        self._error_output = ""
        self._cancel_requested = False
        self.progress_changed.emit(0)
        self.status_changed.emit("Export en cours...")
        self._process.start(command[0], command[1:])

    def cancel(self) -> None:
        """Terminate the active ffmpeg process and emit the cancellation signal."""
        if self._process.state() == QProcess.NotRunning:
            return
        self._cancel_requested = True
        self.status_changed.emit("Annulation de l'export...")
        self._process.kill()

    def _build_command(
        self,
        request: ExportRequest,
        exportable_clips: list[dict[str, object]] | None = None,
    ) -> list[str]:
        """Build the ffmpeg command for an export request."""
        width, height = request.preset.resolution
        output_path = Path(request.output_path).expanduser()
        if not output_path.parent.exists():
            raise ValueError(f"Le dossier de sortie est introuvable : {output_path.parent}")
        if self._temporary_directory is None:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="kut-studio-export-")
        concat_path = self._create_concat_file(
            request.clips,
            self._temporary_directory.name,
            exportable_clips,
        )

        command = [
            _ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_path,
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={request.fps}",
        ]
        if request.format.codec == "h264":
            command.extend(["-c:v", "libx264", "-preset", request.format.preset, "-crf", str(request.preset.crf)])
        else:
            command.extend(["-c:v", request.format.codec, "-profile:v", str(request.format.quality_value)])
        command.extend(["-c:a", "aac", "-b:a", request.preset.audio_bitrate])
        if request.format.container == "mp4":
            command.extend(["-movflags", "+faststart"])
        command.append(str(output_path))
        return command

    def _create_concat_file(
        self,
        clips: list[dict[str, object]],
        tmp_dir: str,
        exportable_clips: list[dict[str, object]] | None = None,
    ) -> str:
        """Write the ffmpeg concat demuxer file and return its path."""
        if exportable_clips is None:
            exportable_clips = self._filter_exportable_clips(clips)
        concat_path = Path(tmp_dir) / "concat.txt"
        with concat_path.open("w", encoding="utf-8") as concat_file:
            for clip in exportable_clips:
                media_path = self._clip_media_path(clip)
                if media_path is None:
                    continue
                escaped_path = media_path.replace("'", "'\\''")
                concat_file.write(f"file '{escaped_path}'\n")
        return str(concat_path)

    def _filter_exportable_clips(self, clips: list[dict[str, object]]) -> list[dict[str, object]]:
        """Return the ordered list of clips that will actually be exported."""
        result: list[dict[str, object]] = []
        for clip in sorted(
            clips,
            key=lambda item: (float(item.get("start", 0.0)), str(item.get("id", ""))),
        ):
            if clip.get("text"):
                continue
            if not self._clip_media_path(clip):
                self.status_changed.emit(
                    f"Avertissement : le clip {clip.get('id', 'inconnu')} est ignoré, source_path manquant."
                )
                continue
            result.append(clip)
        if not result:
            raise ValueError("Aucun média vidéo à exporter.")
        return result

    def _parse_progress(self, line: str) -> int | None:
        """Convert one ffmpeg out_time_ms line into a percentage."""
        if not line.startswith("out_time_ms=") or self._duration_seconds <= 0:
            return None
        try:
            elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
        return max(0, min(100, int(elapsed_seconds / self._duration_seconds * 100)))

    def _read_progress(self) -> None:
        """Parse ffmpeg progress output and publish a bounded percentage."""
        output = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._progress_buffer += output
        lines = self._progress_buffer.split("\n")
        self._progress_buffer = lines.pop()
        for line in lines:
            progress = self._parse_progress(line.strip())
            if progress is not None:
                self.progress_changed.emit(min(99, progress))

    def _read_error(self) -> None:
        """Collect ffmpeg diagnostics for a possible failure message."""
        error = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        self._error_output = (self._error_output + error).strip()

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Handle normal and abnormal ffmpeg process termination."""
        request = self._request
        self._request = None
        self._cleanup_temporary_directory()
        if self._cancel_requested:
            self._cancel_requested = False
            self.cancelled.emit()
            return
        if exit_status != QProcess.NormalExit or exit_code != 0:
            self.failed.emit(self._error_output or "L'export ffmpeg a échoué.")
            return
        if request is None:
            self.failed.emit("La requête d'export est introuvable.")
            return
        self.progress_changed.emit(100)
        self.status_changed.emit("Export terminé")
        self.finished_ok.emit(request.output_path)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        """Report a QProcess-level error when ffmpeg cannot run."""
        if self._cancel_requested:
            return
        if error == QProcess.FailedToStart:
            self.failed.emit("Impossible de démarrer ffmpeg.")
        else:
            self.failed.emit(f"Erreur ffmpeg ({error.name}) : voir logs.")

    @staticmethod
    def _clip_media_path(clip: dict[str, object]) -> str | None:
        """Return the first supported media path found in a timeline clip."""
        value = clip.get("source_path")
        if isinstance(value, str) and value:
            return value
        return None

    def _cleanup_temporary_directory(self) -> None:
        """Release the temporary concat file directory."""
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
