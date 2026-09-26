"""Moteur d'export FFmpeg piloté par un :class:`~core.render_plan.RenderPlan`.

Cette itération abandonne le demuxer ``concat`` au profit d'un
``-filter_complex`` qui compose réellement la timeline :

1. un fond noir de la taille d'export et de durée ``timeline_duration`` ;
2. pour chaque ``RenderLayer`` : trim, reset PTS, scale + bandes noires,
   ``fps=``, décalage temporel via ``setpts=PTS+timeline_start/TB`` ;
3. chaînage des overlays : ``[bg][v0]overlay -> [v1]overlay -> ...``.
4. sortie muette (``-an``) — le mixage audio sera traité dans une
   tâche ultérieure.

L'export reste asynchrone : ``ExportEngine`` est un ``QObject`` qui
pilote un ``QProcess`` et publie sa progression via les signaux
``progress_changed``, ``status_changed``, ``finished_ok``, ``failed``
et ``cancelled``.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, Signal

from .render_plan import RenderPlan, RenderLayer


_ffmpeg_path = shutil.which("ffmpeg")
if _ffmpeg_path is None:
    raise ImportError("ffmpeg est requis pour l'export Kut-Studio mais est introuvable dans le PATH.")


# ---------------------------------------------------------------------------
# Formats et préréglages
# ---------------------------------------------------------------------------


class ExportFormat(Enum):
    """Décrit les combinaisons conteneur / codec prises en charge."""

    MP4_H264 = ("mp4", "h264", "medium", 18)
    MOV_PRORES = ("mov", "prores_ks", "", 3)
    MOV_H264 = ("mov", "h264", "medium", 18)

    @property
    def container(self) -> str:
        """Retourne l'extension du conteneur de sortie."""
        return self.value[0]

    @property
    def codec(self) -> str:
        """Retourne le nom du codec vidéo FFmpeg."""
        return self.value[1]

    @property
    def preset(self) -> str:
        """Retourne le préréglage FFmpeg par défaut pour ce format."""
        return self.value[2]

    @property
    def quality_value(self) -> int:
        """Retourne la valeur de qualité (CRF ou profil) par défaut."""
        return self.value[3]


@dataclass(frozen=True)
class ExportPreset:
    """Décrit la résolution, la qualité et le débit audio d'un export."""

    name: str
    resolution: tuple[int, int]
    crf: int
    audio_bitrate: str


@dataclass(frozen=True)
class ExportRequest:
    """Décrit une opération d'export complète, ancrée sur un :class:`RenderPlan`.

    Attributes:
        render_plan: Plan de rendu à exécuter.
        output_path: Chemin du fichier de sortie.
        format: Format / codec cible.
        preset: Préréglage de résolution et de qualité.
        fps: Fréquence d'images cible de la sortie.
    """

    render_plan: RenderPlan
    output_path: str
    format: ExportFormat
    preset: ExportPreset
    fps: int = 30

    def __post_init__(self) -> None:
        """Rejette les paramètres invalides avant le lancement de FFmpeg."""
        if self.fps <= 0:
            raise ValueError("La fréquence d'images doit être supérieure à zéro.")
        width, height = self.preset.resolution
        if width <= 0 or height <= 0:
            raise ValueError("La résolution d'export doit être positive.")


# ---------------------------------------------------------------------------
# Moteur d'export asynchrone
# ---------------------------------------------------------------------------


class ExportEngine(QObject):
    """Lance un export FFmpeg non bloquant et publie son état via Qt."""

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
        self._request: ExportRequest | None = None
        self._error_output = ""
        self._progress_buffer = ""
        self._duration_seconds = 0.0
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def start(self, request: ExportRequest) -> None:
        """Démarre un export asynchrone pour ``request``.

        Lève (via le signal ``failed``) si :
        - un export est déjà en cours ;
        - le plan de rendu ne contient aucun clip vidéo ;
        - le dossier de sortie est introuvable.
        """
        if self._process.state() != QProcess.NotRunning:
            self.failed.emit("Un export est déjà en cours.")
            return

        try:
            self._duration_seconds = request.render_plan.duration
            if not request.render_plan.video_layers:
                raise ValueError("Aucun média vidéo à exporter.")
            command = self._build_command(request)
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))
            return

        self._request = request
        self._error_output = ""
        self._cancel_requested = False
        self.progress_changed.emit(0)
        self.status_changed.emit("Export en cours...")
        self._process.start(command[0], command[1:])

    def cancel(self) -> None:
        """Termine le processus FFmpeg actif et émet ``cancelled``."""
        if self._process.state() == QProcess.NotRunning:
            return
        self._cancel_requested = True
        self.status_changed.emit("Annulation de l'export...")
        self._process.kill()

    # ------------------------------------------------------------------
    # Construction de la commande FFmpeg
    # ------------------------------------------------------------------

    def _build_command(self, request: ExportRequest) -> list[str]:
        """Construit la commande FFmpeg pour un export basé ``RenderPlan``."""
        plan = request.render_plan
        width, height = request.preset.resolution
        output_path = Path(request.output_path).expanduser()
        if not output_path.parent.exists():
            raise ValueError(
                f"Le dossier de sortie est introuvable : {output_path.parent}"
            )

        filter_complex, output_label = self._build_filter_complex(
            plan, width, height, request.fps
        )

        command: list[str] = [
            _ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
        ]

        # Une entrée par couche vidéo.
        for layer in plan.video_layers:
            command.extend(["-i", layer.source_path])

        command.extend(["-filter_complex", filter_complex])
        command.extend(["-map", f"[{output_label}]"])

        if request.format.codec == "h264":
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    request.format.preset,
                    "-crf",
                    str(request.preset.crf),
                ]
            )
        else:
            command.extend(
                [
                    "-c:v",
                    request.format.codec,
                    "-profile:v",
                    str(request.format.quality_value),
                ]
            )

        # Pas d'audio dans cette itération : le mixage audio complet
        # sera traité dans une tâche ultérieure.
        command.extend(["-an"])

        if request.format.container == "mp4":
            command.extend(["-movflags", "+faststart"])

        command.append(str(output_path))
        return command

    @staticmethod
    def _build_filter_complex(
        plan: RenderPlan,
        output_width: int,
        output_height: int,
        fps: int,
    ) -> tuple[str, str]:
        """Génère le ``-filter_complex`` complet et le label de sortie."""
        width, height = output_width, output_height
        parts: list[str] = []

        # 1. Fond noir, calé sur la taille d'export et la durée totale.
        bg_duration = (
            f":d={_format_seconds(plan.duration)}" if plan.duration > 0 else ""
        )
        parts.append(
            f"color=c=black:s={width}x{height}:r={fps}{bg_duration}[bg]"
        )

        # 2. Une chaîne de filtres par couche vidéo.
        for index, layer in enumerate(plan.video_layers):
            parts.append(
                _build_layer_filter(index, layer, width, height, fps)
            )

        # 3. Chaînage des overlays : bg → bg+v0 → ... → final.
        if plan.video_layers:
            previous_label = "bg"
            for index in range(len(plan.video_layers)):
                is_last = index == len(plan.video_layers) - 1
                next_label = "vout" if is_last else f"o{index}"
                parts.append(
                    f"[{previous_label}][v{index}]"
                    f"overlay=eof_action=pass[{next_label}]"
                )
                previous_label = next_label
            output_label = "vout"
        else:
            output_label = "bg"

        return ";".join(parts), output_label

    # ------------------------------------------------------------------
    # Progression et cycle de vie du processus
    # ------------------------------------------------------------------

    def _parse_progress(self, line: str) -> int | None:
        """Convertit une ligne ``out_time_*`` de FFmpeg en pourcentage."""
        if not line.startswith("out_time_ms=") or self._duration_seconds <= 0:
            return None
        try:
            elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
        return max(0, min(100, int(elapsed_seconds / self._duration_seconds * 100)))

    def _read_progress(self) -> None:
        """Parse la sortie standard et émet ``progress_changed`` bornée."""
        output = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._progress_buffer += output
        lines = self._progress_buffer.split("\n")
        self._progress_buffer = lines.pop()
        for line in lines:
            progress = self._parse_progress(line.strip())
            if progress is not None:
                self.progress_changed.emit(min(99, progress))

    def _read_error(self) -> None:
        """Collecte les diagnostics FFmpeg pour un éventuel message d'erreur."""
        error = bytes(self._process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        self._error_output = (self._error_output + error).strip()

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Traite la fin normale ou anormale du processus FFmpeg."""
        request = self._request
        self._request = None
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
        """Émet ``failed`` pour une erreur de niveau QProcess."""
        if self._cancel_requested:
            return
        if error == QProcess.FailedToStart:
            self.failed.emit("Impossible de démarrer ffmpeg.")
        else:
            self.failed.emit(f"Erreur ffmpeg ({error.name}) : voir logs.")


# ---------------------------------------------------------------------------
# Helpers de construction du filter_complex
# ---------------------------------------------------------------------------


def _format_seconds(value: float) -> str:
    """Formate une durée en secondes pour un argument FFmpeg.

    On évite la notation scientifique de Python (``1e-05``) qui n'est
    pas toujours bien tolérée par FFmpeg, et on retire les zéros
    inutiles en queue (``2.5`` plutôt que ``2.500000``) tout en
    conservant un ``.0`` pour les valeurs entières (``2.0``).
    """
    text = f"{value:.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{float(text):.1f}" if "." not in text else text


def _build_layer_filter(
    index: int, layer: RenderLayer, width: int, height: int, fps: int
) -> str:
    """Construit la chaîne de filtres FFmpeg pour une couche vidéo.

    La chaîne applique successivement :

    1. ``trim`` sur la portion ``[source_in, source_out]`` du média ;
    2. ``setpts=PTS-STARTPTS`` pour recaler les PTS à 0 ;
    3. ``scale`` qui préserve le ratio ;
    4. ``pad`` qui ajoute des bandes noires si nécessaire ;
    5. ``fps`` qui force la fréquence d'images cible ;
    6. ``setpts=PTS+timeline_start/TB`` qui décale la couche à sa
       position sur la timeline.
    """
    source_in = _format_seconds(layer.source_in)
    source_out = _format_seconds(layer.source_out)
    timeline_start = _format_seconds(layer.timeline_start)
    return (
        f"[{index}:v]"
        f"trim=start={source_in}:end={source_out},"
        f"setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},"
        f"setpts=PTS+{timeline_start}/TB[v{index}]"
    )


def _export_engine_module_attributes() -> dict[str, Any]:
    """Expose un snapshot minimal des attributs utiles pour les tests."""
    return {
        "ffmpeg_path": _ffmpeg_path,
    }