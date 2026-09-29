"""Moteur d'export FFmpeg piloté par un :class:`~core.render_plan.RenderPlan`.

Le moteur construit un ``-filter_complex`` complet qui décrit la
timeline :

1. **Vidéo** : un fond noir de la taille d'export et de durée
   ``timeline_duration``, puis pour chaque :class:`RenderLayer` :
   ``trim``, ``setpts=PTS-STARTPTS``, ``scale`` qui préserve le ratio,
   ``pad``, ``fps``, ``setpts=PTS+timeline_start/TB`` ; chaînage des
   overlays successifs (``[bg][v0]overlay -> [v1]overlay -> ...``) pour
   respecter l'ordre des pistes.

2. **Audio** : une source silencieuse de référence (stéréo, 48 kHz)
   couvrant toute la timeline, puis pour chaque :class:`AudioLayer` :
   ``atrim``, ``asetpts=PTS-STARTPTS``, ``aformat`` pour normaliser en
   stéréo / 48 kHz, ``asetpts=PTS+timeline_start/TB`` pour décaler ;
   toutes les sources sont mixées via ``amix`` avec
   ``duration=first`` (la base silencieuse) et ``dropout_transition=0``.

Le résultat est un fichier ``mp4`` / ``mov`` contenant à la fois la
vidéo H.264 / ProRes et une piste audio AAC stéréo 48 kHz. Si le
projet ne porte aucun média vidéo, l'export échoue avec un message
clair. S'il porte uniquement de la vidéo sans flux audio exploitable,
la sortie contient néanmoins une piste audio silencieuse pour respecter
la cohérence du conteneur.

L'export reste asynchrone : ``ExportEngine`` est un ``QObject`` qui
pilote un ``QProcess`` et publie sa progression via les signaux
``progress_changed``, ``status_changed``, ``finished_ok``, ``failed``
et ``cancelled``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .effects_model import ClipEffect, EffectType
from .render_plan import AudioLayer, RenderLayer, RenderPlan, RenderTransition
from .subtitle_io import format_ass_with_styles, format_srt
from .text_style import default_text_style, is_default_style as _is_default_style
from .transitions import TransitionType
from .time_remapping import (
    MAX_REVERSE_DURATION_SECONDS,
    FreezeFrameMode,
    get_ffmpeg_freeze_filter,
    get_ffmpeg_reverse_filter,
    get_ffmpeg_speed_filter,
)
from .visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    build_ffmpeg_expression,
    escape_filter_complex_commas,
)


_ffmpeg_path = shutil.which("ffmpeg")


def require_ffmpeg() -> str:
    """Retourne le chemin FFmpeg ou lève un :class:`ImportError` explicite.

    La résolution est paresseuse : importer ``core.export_engine`` (donc
    ouvrir l'application) ne doit jamais planter sur une machine sans
    FFmpeg — seul le démarrage d'un export l'exige.
    """
    path = _ffmpeg_path or shutil.which("ffmpeg")
    if path is None:
        raise ImportError(
            "ffmpeg est requis pour l'export Kut-Studio mais est introuvable dans le PATH."
        )
    return path


def _ffmpeg_supports_subtitles() -> bool:
    """Retourne ``True`` si le binaire ``ffmpeg`` supporte le filtre ``subtitles``.

    Le filtre ``subtitles`` n'est disponible que si FFmpeg a été compilé
    avec ``--enable-libass``. Le résultat est mis en cache pour éviter
    de relancer ``ffmpeg -filters`` à chaque export.
    """
    if hasattr(_ffmpeg_supports_subtitles, "_cached"):
        return _ffmpeg_supports_subtitles._cached  # type: ignore[attr-defined]
    ffmpeg = _ffmpeg_path or shutil.which("ffmpeg")
    if ffmpeg is None:
        _ffmpeg_supports_subtitles._cached = False  # type: ignore[attr-defined]
        return False
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        supported = False
    else:
        supported = " subtitles " in f" {completed.stdout} "
    _ffmpeg_supports_subtitles._cached = supported  # type: ignore[attr-defined]
    return supported


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
        self._temporary_files: list[str] = []

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def start(self, request: ExportRequest) -> None:
        """Démarre un export asynchrone pour ``request``.

        Lève (via le signal ``failed``) si :
        - un export est déjà en cours ;
        - le plan de rendu ne contient aucun clip vidéo ;
        - le dossier de sortie est introuvable ;
        - la build FFmpeg ne supporte pas ``subtitles`` (libass requis).
        """
        if self._process.state() != QProcess.NotRunning:
            self.failed.emit("Un export est déjà en cours.")
            return

        try:
            self._duration_seconds = request.render_plan.duration
            if not request.render_plan.video_layers:
                raise ValueError("Aucun média vidéo à exporter.")
            if request.render_plan.subtitle_cues and not _ffmpeg_supports_subtitles():
                raise RuntimeError(
                    "La build FFmpeg ne supporte pas le filtre 'subtitles' "
                    "(libass requis). Installez un FFmpeg avec libass pour "
                    "incruster les sous-titres."
                )
            self._prepare_temporary_files(request.render_plan)
            command = self._build_command(request)
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            self._cleanup_temporary_files()
            self.failed.emit(str(error))
            return

        self._request = request
        self._error_output = ""
        self._progress_buffer = ""
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
        # ``_process_finished`` se chargera du nettoyage des fichiers
        # temporaires une fois le slot appelé par Qt.

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

        srt_path = self._current_srt_path
        filter_complex, video_label, audio_label, input_paths = (
            self._build_filter_complex(plan, width, height, request.fps, srt_path)
        )

        command: list[str] = [
            require_ffmpeg(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
        ]

        for path in input_paths:
            command.extend(["-i", path])

        command.extend(["-filter_complex", filter_complex])
        command.extend(["-map", f"[{video_label}]"])
        command.extend(["-map", f"[{audio_label}]"])

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

        # Sortie audio : AAC stéréo 48 kHz pour MP4 et MOV.
        # ``ExportPreset`` porte toujours ce champ, mais le moteur reste
        # compatible avec les petits préréglages utilisés par des appels
        # programmatiques historiques.
        audio_bitrate = getattr(request.preset, "audio_bitrate", "192k")
        command.extend(
            [
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                audio_bitrate,
            ]
        )

        if request.format.container == "mp4":
            command.extend(["-movflags", "+faststart"])

        command.append(str(output_path))
        return command

    # ------------------------------------------------------------------
    # Fichiers temporaires (SRT) et nettoyage
    # ------------------------------------------------------------------

    def _prepare_temporary_files(self, plan: RenderPlan) -> None:
        """Écrit un fichier temporaire (SRT ou ASS) si le projet porte des sous-titres.

        L'ASS est privilégié dès qu'au moins un clip porte un style non
        standard ; sinon on conserve le SRT historique pour préserver la
        compatibilité avec les builds FFmpeg sans libass.
        """
        self._cleanup_temporary_files()
        if not plan.subtitle_cues:
            return
        has_custom_style = any(
            not _is_default_style(style) for style in plan.subtitle_styles
        )
        if has_custom_style:
            suffix = ".ass"
            content = format_ass_with_styles(
                list(zip(plan.subtitle_cues, plan.subtitle_styles)),
                default_style=default_text_style(),
            )
        else:
            suffix = ".srt"
            content = format_srt(list(plan.subtitle_cues))
        fd, tmp_path = tempfile.mkstemp(
            prefix="kut-studio-subtitles-",
            suffix=suffix,
        )
        # Enregistrer le chemin avant l'écriture afin que le nettoyage
        # du gestionnaire couvre aussi un échec d'écriture sur disque.
        self._temporary_files.append(tmp_path)
        os.close(fd)
        Path(tmp_path).write_text(content, encoding="utf-8")

    def _cleanup_temporary_files(self) -> None:
        """Supprime tous les fichiers temporaires créés pour cet export."""
        for path in self._temporary_files:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temporary_files.clear()

    @property
    def _current_srt_path(self) -> str | None:
        """Retourne le chemin du fichier de sous-titres courant (SRT ou ASS)."""
        for path in self._temporary_files:
            if path.endswith((".srt", ".ass")):
                return path
        return None

    @staticmethod
    def _build_filter_complex(
        plan: RenderPlan,
        output_width: int,
        output_height: int,
        fps: int,
        srt_path: str | None,
    ) -> tuple[str, str, str, list[str]]:
        """Génère le ``-filter_complex`` complet + labels + liste d'inputs.

        Si ``plan.subtitle_cues`` est non vide, le filtre ``subtitles``
        est appliqué après la composition vidéo pour incruster les
        sous-titres via libass.

        Returns:
            filter_complex: chaîne complète à passer à ``-filter_complex``.
            video_label: label du flux vidéo final (après incrustation).
            audio_label: label du flux audio final.
            input_paths: liste dédupliquée des chemins à passer en ``-i``.
        """
        width, height = output_width, output_height
        duration = plan.duration

        # Inputs dédupliqués : on assigne un index à chaque chemin unique.
        input_paths, path_to_index = _build_input_list(plan)

        parts: list[str] = []

        # ---------------- Vidéo ----------------
        bg_duration = f":d={_format_seconds(duration)}" if duration > 0 else ""
        parts.append(
            f"color=c=black:s={width}x{height}:r={fps}{bg_duration}[bg]"
        )

        for layer_index, layer in enumerate(plan.video_layers):
            input_index = path_to_index[layer.source_path]
            parts.append(
                _build_layer_filter(layer_index, layer, input_index, width, height, fps)
            )

        if plan.video_layers:
            display_layers = _build_transition_layers(parts, plan)
            previous_label = "bg"
            for layer_index, (label, layer) in enumerate(display_layers):
                is_last = layer_index == len(display_layers) - 1
                next_label = "vout" if is_last else f"o{layer_index}"
                overlay_args = _build_overlay_args(layer, width, height)
                parts.append(
                    f"[{previous_label}][{label}]"
                    f"overlay={overlay_args}[{next_label}]"
                )
                previous_label = next_label
            video_label = "vout"
        else:
            video_label = "bg"

        # ---------------- Audio ----------------
        silent_base_filter = (
            f"aevalsrc=0|0:channel_layout=stereo:sample_rate=48000:"
            f"duration={_format_seconds(duration)}[silent_base]"
        )
        parts.append(silent_base_filter)

        if plan.audio_layers:
            for audio_index, layer in enumerate(plan.audio_layers):
                input_index = path_to_index[layer.source_path]
                parts.append(
                    _build_audio_filter(audio_index, layer, input_index, duration)
                )
            n_inputs = len(plan.audio_layers) + 1
            mixed_inputs = "".join(f"[a{i}]" for i in range(len(plan.audio_layers)))
            # Le gain Master est appliqué après l'amix : il doit
            # piloter l'ensemble du mixage, pas chaque couche.
            master_filter = _build_master_filter(plan)
            tail = f",{master_filter}" if master_filter else ""
            parts.append(
                f"[silent_base]{mixed_inputs}"
                f"amix=inputs={n_inputs}:duration=first:dropout_transition=0"
                f"{tail},"
                f"aformat=channel_layouts=stereo:sample_rates=48000[aout]"
            )
            audio_label = "aout"
        else:
            # Aucun clip audio : on renomme la base silencieuse en ``aout``.
            parts.append(
                f"[silent_base]aformat=channel_layouts=stereo:sample_rates=48000[aout]"
            )
            audio_label = "aout"

        # ---------------- Sous-titres ----------------
        # L'incrustation se fait via libass (``subtitles=``), appliquée
        # après la composition vidéo. Le chemin du SRT est préparé par
        # ``_prepare_temporary_files`` avant le lancement de FFmpeg.
        if plan.subtitle_cues:
            if not srt_path:
                raise RuntimeError(
                    "Le plan contient des sous-titres actifs mais aucun "
                    "fichier SRT temporaire n'a été préparé."
                )
            parts.append(
                f"[{video_label}]subtitles={_escape_filter_path(srt_path)}"
                f":fontsdir={_escape_filter_path(_subtitle_fontsdir())}"
                f":force_style={_SUBTITLE_FORCE_STYLE_FORCE}[vfinal]"
            )
            video_label = "vfinal"

        return ";".join(parts), video_label, audio_label, input_paths

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
            self._cleanup_temporary_files()
            return
        if exit_status != QProcess.NormalExit or exit_code != 0:
            self.failed.emit(self._error_output or "L'export ffmpeg a échoué.")
            self._cleanup_temporary_files()
            return
        if request is None:
            self.failed.emit("La requête d'export est introuvable.")
            self._cleanup_temporary_files()
            return
        self.progress_changed.emit(100)
        self.status_changed.emit("Export terminé")
        self._cleanup_temporary_files()
        self.finished_ok.emit(request.output_path)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        """Émet ``failed`` pour une erreur de niveau QProcess."""
        if self._cancel_requested:
            return
        if error == QProcess.FailedToStart:
            self._cleanup_temporary_files()
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


def _build_input_list(plan: RenderPlan) -> tuple[list[str], dict[str, int]]:
    """Construit la liste dédupliquée d'inputs et un mapping ``path → index``."""
    input_paths: list[str] = []
    path_to_index: dict[str, int] = {}
    for layer in list(plan.video_layers) + list(plan.audio_layers):
        if layer.source_path in path_to_index:
            continue
        path_to_index[layer.source_path] = len(input_paths)
        input_paths.append(layer.source_path)
    return input_paths, path_to_index


def _build_transition_layers(parts: list[str], plan: RenderPlan) -> list[tuple[str, RenderLayer]]:
    """Remplace deux couches liées par leur flux ``xfade`` FFmpeg."""
    by_id = {layer.clip_id: (index, layer) for index, layer in enumerate(plan.video_layers)}
    replacements: dict[int, tuple[str, RenderLayer]] = {}
    hidden: set[int] = set()
    for transition_index, transition in enumerate(plan.transitions):
        source = by_id.get(transition.from_clip_id)
        target = by_id.get(transition.to_clip_id)
        if source is None or target is None:
            continue
        from_index, from_layer = source
        to_index, to_layer = target
        if from_layer.track_id != to_layer.track_id or from_index in hidden or to_index in hidden:
            continue
        name = _ffmpeg_transition_name(transition)
        offset = max(0.0, from_layer.timeline_end - from_layer.timeline_start - transition.duration)
        label = f"transition{transition_index}"
        parts.append(
            f"[v{from_index}]setpts=PTS-STARTPTS[ta{transition_index}];"
            f"[v{to_index}]setpts=PTS-STARTPTS[tb{transition_index}];"
            f"[ta{transition_index}][tb{transition_index}]"
            f"xfade=transition={name}:duration={_format_seconds(transition.duration)}:"
            f"offset={_format_seconds(offset)},"
            f"setpts=PTS+{_format_seconds(from_layer.timeline_start)}/TB[{label}]"
        )
        replacements[min(from_index, to_index)] = (label, from_layer)
        hidden.update({from_index, to_index})
    result: list[tuple[str, RenderLayer]] = []
    for index, layer in enumerate(plan.video_layers):
        if index in replacements:
            result.append(replacements[index])
        if index in hidden:
            continue
        result.append((f"v{index}", layer))
    return result


def _ffmpeg_transition_name(transition: RenderTransition) -> str:
    return {
        TransitionType.CROSSFADE: "fade",
        TransitionType.FADE_BLACK: "fadeblack",
        TransitionType.WIPE_LEFT: "wipeleft",
        TransitionType.WIPE_RIGHT: "wiperight",
    }[transition.type]


def _build_layer_filter(
    layer_index: int,
    layer: RenderLayer,
    input_index: int,
    width: int,
    height: int,
    fps: int,
) -> str:
    """Construit la chaîne de filtres FFmpeg pour une couche vidéo.

    La chaîne applique successivement :

    1. ``trim`` sur la portion ``[source_in, source_out]`` du média ;
    2. ``setpts=PTS-STARTPTS`` pour recaler les PTS à 0 ;
    3. ``scale`` qui préserve le ratio ;
    4. ``pad`` qui ajoute des bandes noires si nécessaire ;
    5. ``fps`` qui force la fréquence d'images cible ;
    6. ``scale`` animé (variation autour de la valeur de base) ;
    7. ``rotate`` animé ;
    8. effets visuels activés du clip, dans leur ordre ;
    9. ``format=rgba`` pour permettre la composition alpha ;
    10. ``colorchannelmixer`` pour l'opacité animée ;
    11. ``setpts=PTS+timeline_start/TB`` qui décale la couche à sa
       position sur la timeline.

    Le temps utilisé dans les expressions est local au clip : après le
    ``setpts=PTS-STARTPTS``, les filtres ``scale`` et ``rotate``
    exposent la variable ``t`` (en secondes), qui repart donc de zéro.
    """
    source_in = _format_seconds(layer.source_in)
    source_out = _format_seconds(layer.source_out)
    timeline_start = _format_seconds(layer.timeline_start)

    # ``scale`` et ``rotate`` attendent la variable temporelle
    # minuscule ``t``. ``T`` n'est définie que par certains filtres,
    # notamment ``geq`` utilisé pour l'opacité.
    transform = layer.transform
    kfs = layer.transform_keyframes
    scale_expr = _build_animated_scale_expr(transform, kfs, width, height)
    rotation_expr = _build_animated_rotation_expr(transform, kfs)
    opacity_expr = _build_animated_opacity_expr(transform, kfs)
    effect_filters = _build_clip_effect_filters(layer.effects)

    # Filtres de remappage temporel (freeze, reverse, speed)
    time_remapping_filter = _build_time_remapping_video_filter(layer)

    parts = [
        f"[{input_index}:v]",
        f"trim=start={source_in}:end={source_out},",
        f"setpts=PTS-STARTPTS,",
    ]

    # Freeze frame: appliqué immédiatement après trim/setpts
    # car il sélectionne une frame spécifique
    if layer.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        parts.append(f"{time_remapping_filter},")
        parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,")
        parts.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,")
        parts.append(f"fps={fps},")
        if effect_filters:
            parts.append(f"{effect_filters},")
        parts.append(f"format=rgba,")
        parts.append(f"{opacity_expr},")
        parts.append(f"setpts=PTS+{timeline_start}/TB[v{layer_index}]")
    else:
        # Cas normal: appliquer scale/pad/fps avant le time_remapping
        # Le time_remapping (reverse/speed) doit être appliqué AVANT setpts
        # pour que le décalage timeline soit correct
        parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,")
        parts.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,")
        parts.append(f"fps={fps},")
        
        # Appliquer le time_remapping (reverse/speed) ici
        if time_remapping_filter:
            parts.append(f"{time_remapping_filter},")
        
        parts.append(f"setpts=PTS-STARTPTS,")
        parts.append(f"{scale_expr},")
        parts.append(f"{rotation_expr},")
        if effect_filters:
            parts.append(f"{effect_filters},")
        parts.append(f"format=rgba,")
        parts.append(f"{opacity_expr},")
        parts.append(f"setpts=PTS+{timeline_start}/TB[v{layer_index}]")

    return "".join(parts)


def _build_clip_effect_filters(effects: tuple[ClipEffect, ...]) -> str:
    """Construit les filtres FFmpeg des effets actifs, dans leur ordre.

    Chaque valeur vient du modèle validé : on ne concatène donc jamais
    d'expression fournie par l'utilisateur. Les filtres s'exécutent avant
    l'alpha du calque afin de conserver une composition ``rgba`` fiable.
    """
    filters: list[str] = []
    for effect in effects:
        if not effect.enabled:
            continue
        params = effect.params
        if effect.type is EffectType.COLOR_CORRECTION:
            filters.append(
                "eq="
                f"brightness={_format_seconds(params['brightness'])}:"
                f"contrast={_format_seconds(params['contrast'])}:"
                f"saturation={_format_seconds(params['saturation'])}"
            )
        elif effect.type is EffectType.BLUR:
            filters.append(
                f"gblur=sigma={_format_seconds(params['intensity'])}"
            )
        elif effect.type is EffectType.SHARPEN:
            filters.append(
                "unsharp=luma_msize_x=5:luma_msize_y=5:"
                f"luma_amount={_format_seconds(params['intensity'])}"
            )
        elif effect.type is EffectType.VIGNETTE:
            angle = float(params["intensity"]) * 0.7853981633974483
            filters.append(f"vignette=angle={_format_seconds(angle)}")
        elif effect.type is EffectType.BLACK_AND_WHITE:
            filters.append("hue=s=0")
        elif effect.type is EffectType.SEPIA:
            filters.append(
                "colorchannelmixer="
                ".393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"
            )
    return ",".join(filters)


def _build_animated_scale_expr(
    transform: ClipTransform,
    keyframes: tuple[TransformKeyframe, ...],
    width: int,
    height: int,
) -> str:
    """Génère un filtre ``scale`` animé autour de la taille du canvas."""
    expr = build_ffmpeg_expression(
        "scale",
        transform.scale,
        [kf for kf in keyframes if kf.property_name == "scale"],
        time_var="t",
    )
    # L'échelle s'applique à la dimension : on multiplie par la base
    # du canvas pour que ``scale=1.0`` couvre tout.
    base_w = float(width)
    base_h = float(height)
    w_expr = f"({expr})*{_format_seconds(base_w)}"
    h_expr = f"({expr})*{_format_seconds(base_h)}"
    return (
        f"scale=w='trunc(iw*{w_expr}/iw)':h='trunc(ih*{h_expr}/ih)':"
        f"eval=frame"
    )


def _build_animated_rotation_expr(
    transform: ClipTransform,
    keyframes: tuple[TransformKeyframe, ...],
) -> str:
    """Génère un filtre ``rotate`` animé (degrés)."""
    expr = build_ffmpeg_expression(
        "rotation",
        transform.rotation,
        [kf for kf in keyframes if kf.property_name == "rotation"],
        time_var="t",
    )
    # ``rotate`` accepte une expression en radians via ``a=...``. On
    # multiplie l'angle (en degrés) par ``PI/180``. L'extension du
    # canvas est calculée via ``hypot(iw,ih)`` pour garantir que les
    # rotations même importantes restent entièrement visibles ; un
    # overlay final tronquera à la taille du canvas.
    return (
        f"rotate=a='{expr}*0.017453292519943295':"
        f"c=black@0:ow=hypot(iw\\,ih):oh=hypot(iw\\,ih):"
        f"fillcolor=black@0"
    )


def _build_animated_opacity_expr(
    transform: ClipTransform,
    keyframes: tuple[TransformKeyframe, ...],
) -> str:
    """Génère le filtre qui applique l'opacité alpha sur le layer vidéo.

    Deux cas :
    - Pas d'image-clé : ``colorchannelmixer`` accepte la valeur
      littérale ; rendu rapide.
    - Avec images-clés : on passe par ``geq`` car ``colorchannelmixer``
      n'accepte PAS d'expressions dépendant du temps (``T``). Le
      ``geq`` filtre chaque pixel et préserve RGB via ``r(X,Y)`` etc.
    """
    opacity_keyframes = [kf for kf in keyframes if kf.property_name == "opacity"]
    if not opacity_keyframes:
        # Cas statique : literal accepté par colorchannelmixer, plus
        # performant que ``geq``.
        return f"colorchannelmixer=aa={_format_seconds(transform.opacity)}"

    # Cas animé : ``geq`` avec une expression ``T``-dépendante. La
    # formule d'interpolation linéaire est produite par
    # ``build_ffmpeg_expression``, puis les virgules qu'elle utilise
    # à l'intérieur (séparateurs d'arguments ``if(...,...,...)``)
    # sont échappées pour ne pas être confondues avec le séparateur
    # d'options de ``-filter_complex``.
    expr = build_ffmpeg_expression(
        "opacity",
        transform.opacity,
        opacity_keyframes,
    )
    escaped = escape_filter_complex_commas(expr)
    # ``geq`` doit voir l'expression de l'alpha ; on garde les
    # composantes RGB identiques au pixel d'origine.
    return (
        "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
        f"a='{escaped}'"
    )


def _build_overlay_args(
    layer: RenderLayer,
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Construit la liste d'arguments ``key=value`` du filtre overlay.

    - ``x`` et ``y`` expriment la position animée, normalisée par
      rapport au canvas.
    - ``eof_action=pass`` permet à la couche sous-jacente de rester
      visible après la fin du clip courant.
    """
    # À ce stade du graphe, le filtre ``overlay`` voit le temps absolu
    # de la timeline. Les keyframes sont locales au clip : on soustrait
    # donc son point de départ. Comme ``overlay`` utilise ``t`` (et non
    # ``T``), on passe explicitement cette expression à l'interpolateur.
    local_time = f"(t-{_format_seconds(layer.timeline_start)})"
    px_expr = build_ffmpeg_expression(
        "position_x",
        layer.transform.position_x,
        [kf for kf in layer.transform_keyframes if kf.property_name == "position_x"],
        time_var=local_time,
    )
    py_expr = build_ffmpeg_expression(
        "position_y",
        layer.transform.position_y,
        [kf for kf in layer.transform_keyframes if kf.property_name == "position_y"],
        time_var=local_time,
    )
    x_expr = f"({px_expr})*{_format_seconds(canvas_width)}"
    y_expr = f"({py_expr})*{_format_seconds(canvas_height)}"
    return (
        f"x='{x_expr}':y='{y_expr}':eval=frame:eof_action=pass"
    )


def _build_audio_filter(
    audio_index: int,
    layer: AudioLayer,
    input_index: int,
    timeline_duration: float,
) -> str:
    """Construit la chaîne de filtres FFmpeg pour une couche audio.

    Chaîne appliquée, dans cet ordre :

    1. ``atrim`` sur la portion ``[source_in, source_out]`` ;
    2. ``asetpts=PTS-STARTPTS`` pour recaler les PTS à 0 ;
    3. ``aformat`` en stéréo 48 kHz (avant tout traitement de gain, pour
       que ``pan`` et ``afade``/opèrent sur un format connu) ;
    4. ``volume`` : gain du clip **et** volume de piste, additionnés en
       décibels ;
    5. ``afade`` d'entrée puis de sortie, seulement si non nuls ;
    6. ``pan`` stéréo, seulement si le panoramique n'est pas centré ;
    7. ``asetpts=PTS+timeline_start/TB`` qui décale la couche à sa
       position sur la timeline.

    Chaque filtre est **omis** s'il n'a rien à faire : une chaîne
    neutre n'est pas émise. Toutes les valeurs sont bornées avant
    formatage pour qu'aucun caractère de séparation de filtre ne
    puisse s'injecter dans la commande.
    """
    from .audio_mixer import pan_needs_filter

    source_in = _format_seconds(layer.source_in)
    source_out = _format_seconds(layer.source_out)
    timeline_start = _format_seconds(layer.timeline_start)
    _ = timeline_duration  # conservé pour traçabilité / évolutions futures

    steps: list[str] = [
        f"atrim=start={source_in}:end={source_out}",
        "asetpts=PTS-STARTPTS",
        "aformat=channel_layouts=stereo:sample_rates=48000",
    ]

    # Filtres de remappage temporel (freeze, reverse, speed)
    time_remapping_filter = _build_time_remapping_audio_filter(layer)
    if time_remapping_filter:
        # Appliquer le time_remapping avant les effets audio
        steps.append(time_remapping_filter)

    total_db = _clamp_db(layer.total_gain_db)
    if abs(total_db) > 1e-6:
        steps.append(f"volume={_format_db(total_db)}dB")

    fade_in = max(0.0, float(layer.fade_in))
    fade_out = max(0.0, float(layer.fade_out))
    duration = layer.duration
    if fade_in > 1e-6 and duration > 0.0:
        start = _format_seconds(0.0)
        stop = _format_seconds(min(fade_in, duration))
        steps.append(
            f"afade=t=in:st={start}:d={stop}:curve=tri"
        )
    if fade_out > 1e-6 and duration > 0.0:
        start = _format_seconds(max(0.0, duration - fade_out))
        length = _format_seconds(min(fade_out, duration))
        steps.append(
            f"afade=t=out:st={start}:d={length}:curve=tri"
        )

    if pan_needs_filter(layer.total_pan):
        steps.append(_build_pan_filter(layer.total_pan))

    steps.append(f"asetpts=PTS+{timeline_start}/TB")
    return f"[{input_index}:a]" + ",".join(steps) + f"[a{audio_index}]"


def _clamp_db(value: float) -> float:
    """Borne un gain pour qu'il ne puisse jamais casser la commande."""
    from .audio_mixer import MAX_GAIN_DB, MIN_GAIN_DB

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, number))


def _format_db(value: float) -> str:
    """Formate un gain en dB pour un filtre FFmpeg (point décimal sûr)."""
    text = f"{value:.3f}"
    # FFmpeg attend un point décimal ; une locale française donnerait « , ».
    return text.replace(",", ".")


def _build_pan_filter(pan: float) -> str:
    """Filtre ``stereotools`` appliquant un panoramique constant-power.

    On passe par la balance de puissance de :mod:`core.audio_mixer` : le
    gain total reste cohérent quand le son glisse d'un côté à l'autre,
    et un panoramique centré n'émet aucun filtre.

    Aucun caractère ``|`` ici : c'est le séparateur du graphe de
    filtres, il ferait échouer le parsing de la commande.
    """
    from .audio_mixer import pan_to_gains

    left, right = pan_to_gains(pan)
    return (
        "stereotools=in_channels=2:out_channels=2"
        f":balance_out={_format_ratio(left)}:{_format_ratio(right)}"
    )


def _format_ratio(value: float) -> str:
    """Formate un gain de balance stereo (0 → 1) pour ``stereotools``."""
    ratio = max(0.0, min(1.0, float(value)))
    return f"{ratio:.4f}"


def _build_master_filter(plan) -> str:
    """Filtre appliqué au mixage final : gain Master et coupure globale.

    Retourne une chaîne vide si aucun réglage Master n'est actif, pour
    ne pas alourdir la commande d'un filtre sans effet.
    """
    from .audio_mixer import MAX_GAIN_DB, MIN_GAIN_DB

    if getattr(plan, "master_muted", False):
        return "volume=0dB"
    master = float(getattr(plan, "master_gain_db", 0.0) or 0.0)
    if abs(master) > 1e-6:
        bounded = max(MIN_GAIN_DB, min(MAX_GAIN_DB, master))
        return f"volume={_format_db(bounded)}dB"
    return ""


# ---------------------------------------------------------------------------
# Filtres de remappage temporel
# ---------------------------------------------------------------------------


def _build_time_remapping_video_filter(layer: RenderLayer) -> str:
    """Construit les filtres de remappage temporel pour une couche vidéo.

    Applique dans l'ordre :
    1. Freeze frame (si activé) - sélectionne une seule image
    2. Reverse (si activé) - inverse la lecture
    3. Speed (si != 1.0) - change la vitesse via atempo

    Returns:
        Chaîne de filtres à insérer dans le filter_complex, ou chaîne vide
        si aucun remappage n'est actif.
    """
    tr = layer.time_remapping
    parts: list[str] = []

    # Freeze frame: on sélectionne une seule image
    if tr.freeze_mode == FreezeFrameMode.FREEZE:
        # Freeze: on utilise select pour garder une seule frame
        # L'audio est silencieux, géré séparément
        # Calculer le frame number à partir du temps source et du FPS source
        freeze_filters = get_ffmpeg_freeze_filter(
            tr.freeze_source_time,
            layer.source_fps if layer.source_fps > 0 else 30.0,
        )
        parts.extend(freeze_filters)
        # Après un freeze, on n'applique ni reverse ni speed
        return ",".join(parts)

    # Reverse: on applique le filtre reverse
    if tr.reverse:
        # Vérifier la durée pour éviter les problèmes de mémoire
        source_duration = layer.source_out - layer.source_in
        if source_duration > MAX_REVERSE_DURATION_SECONDS:
            raise ValueError(
                f"Impossible d'appliquer reverse : clip trop long "
                f"({source_duration:.0f}s > {MAX_REVERSE_DURATION_SECONDS:.0f}s). "
                f"Limitation FFmpeg pour éviter une consommation mémoire excessive."
            )
        parts.append("reverse")

    # Speed: on applique les filtres atempo
    if tr.speed != 1.0:
        speed_filters = get_ffmpeg_speed_filter(tr.speed)
        parts.extend(speed_filters)

    return ",".join(parts)


def _build_time_remapping_audio_filter(layer: AudioLayer) -> str:
    """Construit les filtres de remappage temporel pour une couche audio.

    Applique dans l'ordre :
    1. Freeze frame: l'audio est silencieux (volume=0)
    2. Reverse: areverse
    3. Speed: atempo

    Note: Le freeze frame pour l'audio se traduit par un silence total.
    La durée timeline est déjà correcte dans le RenderPlan.

    Returns:
        Chaîne de filtres à insérer dans le filter_complex, ou chaîne vide
        si aucun remappage n'est actif.
    """
    tr = layer.time_remapping
    parts: list[str] = []

    # Freeze frame: l'audio est silencieux
    if tr.freeze_mode == FreezeFrameMode.FREEZE:
        # Pour le freeze, on rend l'audio silencieux mais on garde la bonne durée
        # Le trim et asetpts sont déjà appliqués avant
        return "volume=0"

    # Reverse
    if tr.reverse:
        # Vérifier la durée pour éviter les problèmes de mémoire
        source_duration = layer.source_out - layer.source_in
        if source_duration > MAX_REVERSE_DURATION_SECONDS:
            raise ValueError(
                f"Impossible d'appliquer reverse : clip trop long "
                f"({source_duration:.0f}s > {MAX_REVERSE_DURATION_SECONDS:.0f}s). "
                f"Limitation FFmpeg pour éviter une consommation mémoire excessive."
            )
        parts.append("areverse")

    # Speed
    if tr.speed != 1.0:
        speed_filters = get_ffmpeg_speed_filter(tr.speed)
        # Pour l'audio, atempo fonctionne directement
        parts.extend(speed_filters)

    return ",".join(parts)


# ---------------------------------------------------------------------------
# Helpers pour l'incrustation de sous-titres
# ---------------------------------------------------------------------------


def _escape_filter_path(path: str) -> str:
    """Échappe les caractères spéciaux d'un chemin pour un filtre FFmpeg.

    La syntaxe des filtres FFmpeg considère ``:`` et ``\\`` comme
    séparateurs ; on les neutralise par échappement ``\\`` puis on
    échappe les apostrophes pour les expressions ``force_style``.
    """
    escaped = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return escaped


def _subtitle_fontsdir() -> str:
    """Chemin d'un dossier de polices générique disponible partout.

    On pointe sur ``/System/Library/Fonts`` sur macOS et sur
    ``/usr/share/fonts/truetype/dejavu`` sur Linux ; ``/etc`` est un
    fallback inoffensif qui n'existe pas. Le ``fontsdir`` est fourni à
    libass pour qu'il résolve les familles de polices génériques.
    """
    candidates = [
        "/System/Library/Fonts",
        "/Library/Fonts",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts",
        "/etc",
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return "/"


_SUBTITLE_FORCE_STYLE_RAW = (
    "FontName=DejaVu Sans,"
    "FontSize=22,"
    "PrimaryColour=&H00FFFFFF&,"
    "OutlineColour=&H00000000&,"
    "BorderStyle=1,"
    "Outline=2,"
    "Shadow=0,"
    "Alignment=2,"
    "MarginV=24"
)

# Valeur destinée au ``filter_complex`` : les virgules sont échappées
# pour la syntaxe du filtergraph FFmpeg.
_SUBTITLE_FORCE_STYLE_FORCE = _SUBTITLE_FORCE_STYLE_RAW.replace(",", "\\,")
