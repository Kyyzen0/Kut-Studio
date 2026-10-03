"""Moteur d'export FFmpeg piloté par un :class:`~core.render_plan.RenderPlan`.

Le moteur construit un ``-filter_complex`` complet qui décrit la
timeline :

1. **Vidéo et graphiques** : un fond noir de la taille d'export et de durée
   ``timeline_duration``, puis pour chaque :class:`RenderLayer` :
   ``trim``, ``setpts=PTS-STARTPTS``, ``scale`` qui préserve le ratio,
   ``pad``, ``fps``, ``setpts=PTS+timeline_start/TB`` ; chaînage des
   overlays successifs (``[bg][v0]overlay -> [v1]overlay -> ...``) pour
   respecter l'ordre des pistes.

2. **Audio** : une source silencieuse de référence (stéréo, 48 kHz)
   couvrant toute la timeline, puis pour chaque :class:`AudioLayer` :
   ``atrim``, ``asetpts=PTS-STARTPTS``, ``aformat`` pour normaliser en
   stéréo / 48 kHz, ``adelay`` pour décaler à ``timeline_start`` ;
   toutes les sources sont mixées via ``amix`` avec
   ``duration=first`` (la base silencieuse) et ``dropout_transition=0``.

Le résultat est un fichier ``mp4`` / ``mov`` contenant à la fois la
vidéo H.264 / ProRes et une piste audio AAC stéréo 48 kHz. Si le
projet ne porte aucun média visuel (vidéo ou graphique), l'export échoue
avec un message clair. S'il porte uniquement de la vidéo ou des graphiques
sans flux audio exploitable,
la sortie contient néanmoins une piste audio silencieuse pour respecter
la cohérence du conteneur.

L'export reste asynchrone : ``ExportEngine`` est un ``QObject`` qui
pilote un ``QProcess`` et publie sa progression via les signaux
``progress_changed``, ``status_changed``, ``finished_ok``, ``failed``
et ``cancelled``.
"""

from __future__ import annotations

import math
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from . import process_supervisor
from .blend_modes import BlendMode, coerce_blend_mode
from .effects_model import ClipEffect, EffectType
from .mograph_ffmpeg import blend_onto, compose_graphics, video_matte_label
from .render_plan import (
    AudioLayer,
    GraphicLayer,
    RenderLayer,
    RenderPlan,
    RenderTransition,
)
from .subtitle_io import format_ass_with_styles, format_srt
from .text_style import default_text_style, is_default_style as _is_default_style
from .transitions import TransitionType
from .time_remapping import (
    MAX_REVERSE_DURATION_SECONDS,
    FreezeFrameMode,
    clamp_speed,
    get_ffmpeg_freeze_filter,
    get_ffmpeg_reverse_filter,
    get_ffmpeg_speed_filter,
)
from .tool_paths import find_media_tool
from .hardware_encoding import looks_like_encoder_failure, redact_command
from .video_encoders import (
    EncoderChoice,
    EncoderUnavailableError,
    HardwareEncoder,
    cpu_choice,
    resolve_video_encoder,
)
from .visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    build_ffmpeg_expression,
    escape_filter_complex_commas,
)


LOGGER = logging.getLogger("kut_studio.encoding")

_ffmpeg_path = find_media_tool("ffmpeg")


OUTPUT_COLOR_STAGE = (
    "scale=out_color_matrix=bt709:out_range=tv,"
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
)
"""Dernière étape du graphe vidéo : conversion RVB → YUV en BT.709, plage limitée, puis propriétés des images.

``setparams`` pose primaires et transfert sur les images elles-mêmes : selon la version de FFmpeg, les
options de ligne de commande (:data:`OUTPUT_COLOR_TAGS`) ne suffisent pas (le flux sortait balisé
« bt709 » pour la matrice seulement, primaires et transfert « unknown », sous macOS)."""

OUTPUT_COLOR_TAGS = (
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
)
"""Balises posées sur le flux encodé : un lecteur décode alors avec la matrice utilisée à l'encodage."""


def with_output_color_stage(filter_complex: str, video_label: str) -> tuple[str, str]:
    """Ajoute au graphe l'étape de conversion BT.709 ; renvoie ``(graphe, nouvelle étiquette vidéo)``.

    Partagée par l'export et l'aperçu : les deux restent « le même graphe », y compris pour la couleur.
    """
    return f"{filter_complex};[{video_label}]{OUTPUT_COLOR_STAGE}[vcolor]", "vcolor"


def _ffmpeg_command_prefix() -> list[str]:
    """Retourne la commande qui lance FFmpeg.

    En production il s'agit d'un seul chemin. Accepter une séquence rend
    aussi le moteur testable sur Windows : le faux FFmpeg Python est alors
    lancé via ``sys.executable`` au lieu d'un script shell non exécutable.
    """
    path = _ffmpeg_path or find_media_tool("ffmpeg")
    if path is None:
        raise ImportError(
            "ffmpeg est requis pour l'export Kut-Studio mais est introuvable dans le PATH."
        )
    if isinstance(path, (tuple, list)):
        command = [str(item) for item in path if str(item)]
    else:
        command = [str(path)]
    if not command:
        raise ImportError(
            "ffmpeg est requis pour l'export Kut-Studio mais est introuvable dans le PATH."
        )
    return command


def require_ffmpeg() -> str:
    """Retourne le chemin FFmpeg ou lève un :class:`ImportError` explicite.

    La résolution est paresseuse : importer ``core.export_engine`` (donc
    ouvrir l'application) ne doit jamais planter sur une machine sans
    FFmpeg — seul le démarrage d'un export l'exige.
    """
    return _ffmpeg_command_prefix()[0]


FILTER_SCRIPT_THRESHOLD = 20_000
"""Au-delà (en caractères), le graphe de filtres passe par un fichier.

Une animation de plusieurs centaines d'images-clés produit des expressions
longues ; Windows limite une ligne de commande à 32 767 caractères."""


def _ffmpeg_major_version() -> int | None:
    """Version majeure du FFmpeg utilisé (``None`` si inconnue, ex. build Git)."""
    prefix = tuple(_ffmpeg_command_prefix())
    cache = _ffmpeg_major_version.__dict__.setdefault("_cache", {})
    if prefix in cache:
        return cache[prefix]
    major: int | None = None
    try:
        completed = process_supervisor.supervised_run(
            [*prefix, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10, check=False,
        )
        from .hardware_encoding import parse_version

        head = parse_version(completed.stdout).lstrip("n").split(".")[0]
        major = int(head) if head.isdigit() else None
    except (OSError, subprocess.TimeoutExpired):
        major = None
    cache[prefix] = major
    return major


def filter_graph_arguments(filter_complex: str, temporary_files: list[str]) -> list[str]:
    """Arguments FFmpeg du graphe : en ligne, ou via un fichier s'il est trop long.

    FFmpeg ≥ 7 lit un fichier avec ``-/filter_complex`` (``-filter_complex_script``
    a disparu des versions récentes) ; les versions antérieures n'ont que
    ``-filter_complex_script``. Le fichier est ajouté à ``temporary_files``
    (supprimé avec les autres temporaires de l'export).
    """
    if len(filter_complex) <= FILTER_SCRIPT_THRESHOLD:
        return ["-filter_complex", filter_complex]
    descriptor, path = tempfile.mkstemp(prefix="kut-studio-graph-", suffix=".txt")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(filter_complex)
    temporary_files.append(path)
    major = _ffmpeg_major_version()
    option = "-filter_complex_script" if major is not None and major < 7 else "-/filter_complex"
    return [option, path]


def _ffmpeg_supports_subtitles() -> bool:
    """Retourne ``True`` si le binaire ``ffmpeg`` supporte le filtre ``subtitles``.

    Le filtre ``subtitles`` n'est disponible que si FFmpeg a été compilé
    avec ``--enable-libass``. Le résultat est mis en cache pour éviter
    de relancer ``ffmpeg -filters`` à chaque export.
    """
    if hasattr(_ffmpeg_supports_subtitles, "_cached"):
        return _ffmpeg_supports_subtitles._cached  # type: ignore[attr-defined]
    try:
        command_prefix = _ffmpeg_command_prefix()
    except ImportError:
        _ffmpeg_supports_subtitles._cached = False  # type: ignore[attr-defined]
        return False
    try:
        completed = process_supervisor.supervised_run(
            [*command_prefix, "-hide_banner", "-filters"],
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
# Fichier de sous-titres temporaire (partagé export / aperçu)
# ---------------------------------------------------------------------------


def write_subtitle_file(plan: RenderPlan) -> str | None:
    """Écrit un fichier SRT ou ASS temporaire pour les sous-titres du plan.

    L'ASS est privilégié dès qu'au moins un clip porte un style non
    standard ; sinon on conserve le SRT historique pour préserver la
    compatibilité avec les builds FFmpeg sans libass.

    Returns:
        Le chemin du fichier écrit, ou ``None`` si le plan n'a aucun
        sous-titre actif. L'appelant supprime le fichier une fois FFmpeg
        terminé.
    """
    if not plan.subtitle_cues:
        return None
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
    os.close(fd)
    try:
        Path(tmp_path).write_text(content, encoding="utf-8")
    except OSError:
        # Pas de fichier à moitié écrit laissé derrière soi.
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return tmp_path


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
        hardware: famille d'encodeur demandée (``"cpu"``, ``"auto"``,
            ``"videotoolbox"``…, voir :mod:`core.video_encoders`). ``auto``
            retombe sur le CPU si l'encodeur matériel échoue au lancement ;
            un encodeur explicite qui échoue est signalé, jamais masqué.
    """

    render_plan: RenderPlan
    output_path: str
    format: ExportFormat
    preset: ExportPreset
    fps: int = 30
    hardware: str = "cpu"

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
    encoder_selected = Signal(object)
    """Émis à chaque lancement (et après un repli) avec l'``EncoderChoice`` utilisé."""
    encoder_fallback = Signal(str)
    """Émis quand ``auto`` abandonne l'encodeur matériel pour le CPU (raison lisible)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_progress)
        self._process.readyReadStandardError.connect(self._read_error)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)
        # FFmpeg est enregistré auprès du superviseur dès son démarrage : il meurt avec l'application, même tuée
        # brutalement (voir core/process_supervisor.py).
        self._process.started.connect(self._supervise_started)
        self._supervision: process_supervisor.Registration | None = None
        self._request: ExportRequest | None = None
        self._error_output = ""
        self._progress_buffer = ""
        self._duration_seconds = 0.0
        self._cancel_requested = False
        self._temporary_files: list[str] = []
        self.last_encoder_choice = None
        """Dernier :class:`~core.video_encoders.EncoderChoice` construit."""
        self.last_error_kind = ""
        """``"encoder"`` si le dernier échec vient d'un encodeur choisi explicitement."""
        self.last_diagnostics = ""
        """Fin de la sortie d'erreur du dernier essai matériel abandonné (support)."""
        self._launch_request: ExportRequest | None = None
        self._fallback_used = False
        self._progress_seen = False

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """``True`` tant qu'un processus FFmpeg tourne pour cet export."""
        return self._process.state() != QProcess.NotRunning

    @property
    def process_id(self) -> int:
        """PID du FFmpeg en cours (``0`` s'il n'y en a pas)."""
        return int(self._process.processId()) if self.is_running else 0

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Arrête FFmpeg de façon synchrone et libère les fichiers temporaires.

        Destiné à la fermeture de l'application : après l'appel aucun
        processus FFmpeg ne reste en vie. Retourne ``False`` si le
        processus n'a pas pu être confirmé comme terminé.
        """
        if self._process.state() == QProcess.NotRunning:
            self._release_supervision()
            self._cleanup_temporary_files()
            return True
        self._cancel_requested = True
        self._process.kill()
        stopped = self._process.waitForFinished(timeout_ms)
        if stopped:
            self._release_supervision()
        self._cleanup_temporary_files()
        return bool(stopped)

    def start(self, request: ExportRequest) -> None:
        """Démarre un export asynchrone pour ``request``.

        Lève (via le signal ``failed``) si :
        - un export est déjà en cours ;
        - le plan de rendu ne contient aucun clip visuel ;
        - le dossier de sortie est introuvable ;
        - la build FFmpeg ne supporte pas ``subtitles`` (libass requis).
        """
        if self._process.state() != QProcess.NotRunning:
            self.failed.emit("Un export est déjà en cours.")
            return

        try:
            self._duration_seconds = request.render_plan.duration
            if not (
                request.render_plan.video_layers
                or getattr(request.render_plan, "graphics_layers", ())
            ):
                raise ValueError("Aucun média vidéo à exporter.")
            if request.render_plan.subtitle_cues and not _ffmpeg_supports_subtitles():
                raise RuntimeError(
                    "La build FFmpeg ne supporte pas le filtre 'subtitles' "
                    "(libass requis). Installez un FFmpeg avec libass pour "
                    "incruster les sous-titres."
                )
            self._prepare_temporary_files(request.render_plan)
            command = self._build_command(request)
        except EncoderUnavailableError as error:
            self._cleanup_temporary_files()
            self.last_error_kind = "encoder"
            LOGGER.warning("Export refusé : %s", error)
            self.failed.emit(str(error))
            return
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            self._cleanup_temporary_files()
            self.failed.emit(str(error))
            return

        self._request = request
        self._launch_request = request
        self._error_output = ""
        self._progress_buffer = ""
        self._cancel_requested = False
        self._fallback_used = False
        self._progress_seen = False
        self.last_error_kind = ""
        self.last_diagnostics = ""
        self.progress_changed.emit(0)
        self.status_changed.emit("Export en cours...")
        self._launch(command)

    def _launch(self, command: list[str]) -> None:
        """Démarre FFmpeg ; l'encodeur choisi est annoncé et journalisé."""
        choice = self.last_encoder_choice
        if choice is not None:
            LOGGER.info("Encodeur : %s (demandé : %s)", choice.label, choice.requested.value)
            self.encoder_selected.emit(choice)
        LOGGER.info("Commande FFmpeg : %s", redact_command(command))
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

    def build_frame_command(
        self, request: ExportRequest, playhead: float,
    ) -> list[str]:
        """Construit une commande FFmpeg rendant **une seule frame**.

        La commande réutilise exactement le même graphe de filtres que
        l'export (effets, étalonnage couleur, LUT, courbes, sous-titres)
        : c'est ce qui permet aux scopes de lire l'image réellement
        composée, et pas une approximation.

        Différences avec :meth:`_build_command` :

        - la sortie du graphe est rognée à ``playhead`` (``trim`` sur le flux
          composé : c'est le seul moyen exact, un ``-ss`` posé sur une entrée
          ignore la position du clip, sa vitesse et son point d'entrée) ;
        - un seul flux est mappé (le vidéo) ;
        - la sortie est un PNG unique écrit sur ``stdout``
          (``-f image2pipe``), ce qui évite tout fichier temporaire.

        Tout ce qui précède ``playhead`` traverse le graphe avant d'être jeté
        (coût O(playhead)) : pour une image rapide, passer un plan déjà ramené
        à l'origine (:func:`core.playhead_snapshot.project_at_playhead`) et
        ``playhead=0``.

        Args:
            request: la requête d'export, pour sa résolution / son fps ;
            playhead: position à extraire dans le plan, en secondes. L'image
                retenue est celle qui contient cet instant.

        Returns:
            La commande FFmpeg complète.
        """
        plan = request.render_plan
        width, height = request.preset.resolution
        self._prepare_temporary_files(plan)
        filter_complex, video_label, audio_label, input_paths = (
            self._build_filter_complex(
                plan, width, height, request.fps, self._current_srt_path
            )
        )
        # Seule l'image est extraite : l'audio du graphe est consommé par un
        # puits, sinon FFmpeg refuse un graphe dont une sortie n'est pas reliée.
        filter_complex = f"{filter_complex};[{audio_label}]anullsink"
        position = max(0.0, float(playhead))
        mapped = video_label
        if position > 0.0:
            # L'image qui contient l'instant : on vise une demi-image avant son horodatage, pour que
            # l'arrondi des timestamps ne retienne jamais sa voisine.
            rate = max(1.0, float(request.fps))
            index = math.floor(position * rate + 1e-6)
            start = max(0.0, (index - 0.5) / rate)
            mapped = "kut_frame"
            filter_complex += f";[{video_label}]trim=start={start:.6f},setpts=PTS-STARTPTS[{mapped}]"
        command: list[str] = [
            *_ffmpeg_command_prefix(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        for path in input_paths:
            command.extend(["-i", path])
        command.extend(filter_graph_arguments(filter_complex, self._temporary_files))
        command.extend(["-map", f"[{mapped}]"])
        command.extend([
            "-frames:v", "1",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ])
        return command

    def _build_command(self, request: ExportRequest) -> list[str]:
        """Construit la commande FFmpeg pour un export basé ``RenderPlan``."""
        plan = request.render_plan
        width, height = request.preset.resolution
        if plan.missing_media:
            # À l'écran un clip sans média est simplement vide ; dans un fichier exporté ce serait un
            # trou muet que personne ne verrait avant la livraison : on refuse, clairement.
            raise ValueError(
                "Média introuvable : " + ", ".join(plan.missing_media)
                + ". Reconnectez ou supprimez les clips concernés avant d'exporter."
            )
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
            *_ffmpeg_command_prefix(),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
        ]

        # L'encodeur est choisi avant les entrées : certains (VAAPI) demandent une
        # initialisation matérielle placée avant ``-i`` et un filtre final.
        is_h264 = request.format.codec == "h264"
        encoder = resolve_video_encoder(
            request.format.codec,
            speed_preset=request.format.preset,
            quality=request.preset.crf if is_h264 else request.format.quality_value,
            hardware=request.hardware,
            width=width,
            height=height,
            fps=request.fps,
        )
        self.last_encoder_choice = encoder
        command.extend(encoder.pre_input_args)

        for path in input_paths:
            command.extend(["-i", path])

        # Conversion RVB → YUV explicite en BT.709, avant le filtre propre à l'encodeur (VAAPI y ajoute
        # hwupload) : laissée à FFmpeg elle se faisait en BT.601 sans balise, et un lecteur qui décode
        # un fichier HD comme du 709 affichait des couleurs décalées (rouge +11 niveaux mesurés).
        filter_complex, video_label = with_output_color_stage(filter_complex, video_label)
        if encoder.video_filter:
            filter_complex = f"{filter_complex};[{video_label}]{encoder.video_filter}[vencoded]"
            video_label = "vencoded"
        command.extend(filter_graph_arguments(filter_complex, self._temporary_files))
        command.extend(["-map", f"[{video_label}]"])
        command.extend(["-map", f"[{audio_label}]"])
        command.extend(encoder.args)
        command.extend(OUTPUT_COLOR_TAGS)

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

        Le contenu et le choix de format sont produits par
        :func:`write_subtitle_file`, partagé avec le moteur d'aperçu :
        les deux rendus incrustent donc exactement les mêmes sous-titres.
        """
        self._cleanup_temporary_files()
        path = write_subtitle_file(plan)
        # Enregistrer le chemin pour que le nettoyage du gestionnaire
        # couvre aussi un échec ultérieur de l'export.
        if path:
            self._temporary_files.append(path)

    def _cleanup_temporary_files(self) -> None:
        """Supprime tous les fichiers temporaires créés pour cet export."""
        for path in self._temporary_files:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temporary_files.clear()

    def take_temporary_files(self) -> tuple[str, ...]:
        """Transfère la responsabilité des temporaires à un appelant.

        L'analyse des scopes utilise une instance courte durée de ce moteur
        pour bâtir sa commande. Elle doit pouvoir supprimer son SRT une fois
        la frame extraite, sans qu'un export régulier puisse toucher ce
        fichier entre-temps.
        """
        files = tuple(self._temporary_files)
        self._temporary_files.clear()
        return files

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
        quality: str = "export",
    ) -> tuple[str, str, str, list[str]]:
        """Génère le ``-filter_complex`` complet + labels + liste d'inputs.

        ``quality`` (``draft`` / ``standard`` / ``high`` / ``export``) ne règle
        que le flou de mouvement des calques motion graphics : un segment
        d'aperçu brouillon s'en passe, l'export utilise le réglage complet.

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

        # Inputs dédupliqués : on assigne un index à chaque chemin unique
        # (séquences imbriquées comprises).
        input_paths, path_to_index = _build_input_list(plan)

        def add_input(path: str) -> int:
            """Entrée supplémentaire (flux de calques, matte) ; dédupliquée."""
            index = path_to_index.get(path)
            if index is None:
                index = path_to_index[path] = len(input_paths)
                input_paths.append(path)
            return index

        parts: list[str] = []

        # Séquences imbriquées : chaque sous-plan est composé une fois, en
        # amont, puis distribué (``split``) à ses instances.
        sources = _build_nested_sources(
            parts, plan, path_to_index, width, height, add_input=add_input, quality=quality,
        )
        video_label, audio_label = _compose_plan_graph(
            parts, plan, width, height, fps, path_to_index, sources,
            add_input=add_input, quality=quality,
        )

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
            fonts_dir = _subtitle_fontsdir()
            fonts_option = (
                f":fontsdir='{_escape_filter_path(fonts_dir)}'"
                if fonts_dir
                else ""
            )
            # Un SRT n'a pas de style : ``force_style`` lui donne celui par défaut. Un ASS porte déjà le sien,
            # champ par champ (corps, couleur, position, contour) : y ajouter ``force_style`` l'écraserait
            # (mesuré avec libass : un style 48 pt jaune en haut ressortait en 22 pt blanc en bas).
            force_option = (
                "" if srt_path.lower().endswith(".ass") else f":force_style={_SUBTITLE_FORCE_STYLE_FORCE}"
            )
            parts.append(
                f"[{video_label}]subtitles=filename='{_escape_filter_path(srt_path)}'"
                f"{fonts_option}{force_option}[vfinal]"
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
                if progress > 0:
                    self._progress_seen = True
                self.progress_changed.emit(min(99, progress))

    def _read_error(self) -> None:
        """Collecte les diagnostics FFmpeg pour un éventuel message d'erreur."""
        error = bytes(self._process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        self._error_output = (self._error_output + error).strip()

    def _supervise_started(self) -> None:
        """Signal ``started`` : le PID de FFmpeg rejoint le registre du superviseur (protection après un crash)."""
        self._release_supervision()
        self._supervision = process_supervisor.register_pid(int(self._process.processId()))

    def _release_supervision(self) -> None:
        registration, self._supervision = self._supervision, None
        process_supervisor.release(registration)

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Traite la fin normale ou anormale du processus FFmpeg."""
        self._release_supervision()
        self._read_error()  # sortie d'erreur restante : la classification de l'échec en dépend
        request = self._request
        self._request = None
        if self._cancel_requested:
            self._cancel_requested = False
            self.cancelled.emit()
            self._cleanup_temporary_files()
            return
        if exit_status != QProcess.NormalExit or exit_code != 0:
            if request is not None and self._should_fall_back():
                self._fall_back_to_cpu(request)
                return
            self._mark_encoder_failure()
            LOGGER.error(
                "Export échoué (code %s, %s) : %s", exit_code, exit_status.name, _last_line(self._error_output) or "sans détail"
            )
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
            LOGGER.error("Export : FFmpeg n'a pas pu démarrer")
            self._cleanup_temporary_files()
            self.failed.emit("Impossible de démarrer ffmpeg.")
        elif not self._should_fall_back():  # sinon ``_process_finished`` bascule en CPU
            self._mark_encoder_failure()
            LOGGER.error("Export : erreur du processus FFmpeg (%s)", error.name)
            self.failed.emit(f"Erreur ffmpeg ({error.name}) : voir logs.")

    # ------------------------------------------------------------------
    # Repli sur le CPU (mode Auto uniquement)
    # ------------------------------------------------------------------

    def _should_fall_back(self) -> bool:
        """``True`` si l'échec est celui d'un encodeur matériel choisi par ``auto``.

        Un seul repli par export (pas de boucle entre encodeurs) et seulement
        tant qu'aucune image n'a été encodée : une initialisation matérielle
        qui échoue se voit au lancement, pas à 90 % du rendu.
        """
        choice = self.last_encoder_choice
        return (
            choice is not None
            and choice.is_hardware
            and choice.requested is HardwareEncoder.AUTO
            and not self._fallback_used
            and not self._progress_seen
            and not self._cancel_requested
        )

    def _fall_back_to_cpu(self, request: ExportRequest) -> None:
        choice = self.last_encoder_choice
        detail = _last_line(self._error_output)
        reason = (
            f"{choice.label} n'a pas pu démarrer"
            + (f" ({detail})" if detail else "")
            + " : rendu CPU utilisé."
        )
        LOGGER.warning("Repli CPU : %s", reason)
        self.last_diagnostics = self._error_output[-800:]
        self._fallback_used = True
        is_h264 = request.format.codec == "h264"
        cpu = cpu_choice(
            request.format.codec,
            speed_preset=request.format.preset,
            quality=request.preset.crf if is_h264 else request.format.quality_value,
            requested=choice.requested,
            fallback_reason=reason,
        )
        cpu_request = replace(request, hardware=HardwareEncoder.CPU.value)
        self._request = request  # le rendu CPU se termine comme l'export demandé
        self._error_output = ""
        self._progress_buffer = ""
        try:
            command = self._build_command(cpu_request)
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))
            self._cleanup_temporary_files()
            return
        self.last_encoder_choice = cpu
        self.status_changed.emit(reason)
        self.encoder_fallback.emit(reason)
        self._launch(command)

    def _mark_encoder_failure(self) -> None:
        """Un encodeur matériel demandé explicitement a échoué : le signaler comme tel."""
        choice = self.last_encoder_choice
        if (
            choice is not None
            and choice.is_hardware
            and choice.requested not in (HardwareEncoder.AUTO, HardwareEncoder.CPU)
            and looks_like_encoder_failure(self._error_output, choice.encoder, choice.args)
        ):
            self.last_error_kind = "encoder"
            self.last_diagnostics = self._error_output[-800:]


# ---------------------------------------------------------------------------
# Composition d'un plan (racine ou séquence imbriquée)
# ---------------------------------------------------------------------------


class _NestedSources:
    """Labels FFmpeg des rendus de séquences imbriquées, à consommer.

    Un label de ``-filter_complex`` ne peut être lu qu'une fois : un rendu
    utilisé par N instances est dupliqué par ``split`` / ``asplit`` et
    chaque couche consomme l'un des labels.
    """

    def __init__(self) -> None:
        self.video: dict[str, list[str]] = {}
        self.audio: dict[str, list[str]] = {}

    def take_video(self, key: str) -> str:
        labels = self.video.get(key)
        if not labels:
            raise ValueError(f"Rendu de séquence imbriquée introuvable : {key}.")
        return labels.pop(0)

    def take_audio(self, key: str) -> str:
        labels = self.audio.get(key)
        if not labels:
            raise ValueError(f"Mixage de séquence imbriquée introuvable : {key}.")
        return labels.pop(0)


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _nested_demand(plan: RenderPlan) -> tuple[dict[str, int], dict[str, int]]:
    """Nombre de lectures vidéo / audio de chaque sous-plan.

    Parcours des parents vers les enfants (ordre inverse du registre) : un
    sous-plan dont la vidéo n'est lue par personne ne réclame pas la vidéo
    de ses propres enfants. Ainsi aucun label n'est produit sans lecteur
    (FFmpeg refuserait ou mapperait un flux en trop).
    """
    need_video: dict[str, int] = {}
    need_audio: dict[str, int] = {}

    def count(layers, demand: dict[str, int]) -> None:
        for layer in layers:
            key = getattr(layer, "nested_key", "")
            if key:
                demand[key] = demand.get(key, 0) + 1

    count(plan.video_layers, need_video)
    count(plan.audio_layers, need_audio)
    for entry in reversed(getattr(plan, "nested_sequences", ())):
        if need_video.get(entry.key):
            count(entry.plan.video_layers, need_video)
        if need_audio.get(entry.key):
            count(entry.plan.audio_layers, need_audio)
    return need_video, need_audio


def _build_nested_sources(
    parts: list[str],
    plan: RenderPlan,
    path_to_index: dict[str, int],
    output_width: int,
    output_height: int,
    *,
    add_input=None,
    quality: str = "export",
) -> _NestedSources:
    """Compose chaque séquence imbriquée et prépare ses labels de sortie.

    Une séquence imbriquée est rendue à **sa** résolution, mise à l'échelle
    du rapport export / séquence racine (un aperçu au quart de la taille
    rend aussi ses séquences imbriquées au quart), sur un fond
    **transparent** : là où elle est vide, la piste parente en dessous
    reste visible. Son image est ensuite traitée comme celle d'un média
    (cadrage, transform, effets, opacité, fusion du clip imbriqué) :
    rendu interne → composite → effets du clip → timeline parente.
    """
    sources = _NestedSources()
    entries = tuple(getattr(plan, "nested_sequences", ()) or ())
    if not entries:
        return sources
    need_video, need_audio = _nested_demand(plan)
    scale_x = output_width / max(1, int(plan.width))
    scale_y = output_height / max(1, int(plan.height))
    for index, entry in enumerate(entries):
        want_video = need_video.get(entry.key, 0)
        want_audio = need_audio.get(entry.key, 0)
        if not (want_video or want_audio):
            continue
        inner = entry.plan
        prefix = f"n{index}_"
        width = _even(inner.width * scale_x)
        height = _even(inner.height * scale_y)
        fps = inner.fps if inner.fps > 0 else 30.0
        video_label, audio_label = _compose_plan_graph(
            parts, inner, width, height, fps, path_to_index, sources,
            prefix=prefix, nested=True,
            want_video=bool(want_video), want_audio=bool(want_audio),
            add_input=add_input, quality=quality,
        )
        if want_video:
            sources.video[entry.key] = _fan_out(parts, video_label, want_video, "split")
        if want_audio:
            sources.audio[entry.key] = _fan_out(parts, audio_label, want_audio, "asplit")
    return sources


def _fan_out(parts: list[str], label: str, count: int, filter_name: str) -> list[str]:
    """``count`` labels lisant le flux ``label`` (``split`` si plusieurs)."""
    if count <= 1:
        return [label]
    outputs = [f"{label}_{i}" for i in range(count)]
    parts.append(
        f"[{label}]{filter_name}={count}" + "".join(f"[{out}]" for out in outputs)
    )
    return outputs


def _compose_plan_graph(
    parts: list[str],
    plan: RenderPlan,
    width: int,
    height: int,
    fps,
    path_to_index: dict[str, int],
    sources: _NestedSources,
    *,
    prefix: str = "",
    nested: bool = False,
    want_video: bool = True,
    want_audio: bool = True,
    add_input=None,
    quality: str = "export",
) -> tuple[str, str]:
    """Ajoute à ``parts`` la composition vidéo + audio d'un plan.

    La racine (``prefix=""``) produit exactement les labels historiques
    (``bg``, ``v0``, ``vout``, ``aout``…). Une séquence imbriquée reçoit un
    préfixe unique (``n0_``…), un fond transparent et pas de gain Master.

    Returns:
        ``(label vidéo, label audio)`` finaux du plan (chaîne vide pour un
        flux non demandé).
    """
    duration = plan.duration
    if nested:
        # Au moins une image : une séquence vide reste un flux fini.
        duration = max(duration, 1.0 / float(fps or 30.0))
    p = prefix
    video_label = ""
    audio_label = ""
    fps_text = fps if isinstance(fps, int) else _format_seconds(float(fps))
    if add_input is None:
        def add_input(path: str) -> int:
            raise ValueError("Ce graphe n'accepte pas d'entrée supplémentaire.")

    # ---------------- Vidéo ----------------
    if want_video:
        bg_duration = f":d={_format_seconds(duration)}" if duration > 0 else ""
        if nested:
            parts.append(
                f"color=c=black@0:s={width}x{height}:r={fps_text}{bg_duration},"
                f"format=yuva420p[{p}bg]"
            )
        else:
            parts.append(
                f"color=c=black:s={width}x{height}:r={fps_text}{bg_duration}[{p}bg]"
            )

        for layer_index, layer in enumerate(plan.video_layers):
            if layer.nested_key:
                parts.append(
                    _build_layer_filter(
                        layer_index, layer, None, width, height, fps,
                        source=sources.take_video(layer.nested_key),
                        label=f"{p}v{layer_index}",
                        pad_color="black@0",
                        add_input=add_input,
                    )
                )
            else:
                input_index = path_to_index[layer.source_path]
                parts.append(
                    _build_layer_filter(
                        layer_index, layer, input_index, width, height, fps,
                        label=f"{p}v{layer_index}" if p else None,
                        add_input=add_input,
                    )
                )

        video_label = f"{p}bg"
        if plan.video_layers:
            display_layers = _build_transition_layers(parts, plan, prefix=p)
            previous_label = f"{p}bg"
            for layer_index, (label, layer) in enumerate(display_layers):
                is_last = layer_index == len(display_layers) - 1
                next_label = f"{p}vout" if is_last else f"{p}o{layer_index}"
                overlay_args = _build_overlay_args(layer, width, height)
                blend_mode = coerce_blend_mode(
                    getattr(getattr(layer, "compositing", None), "blend_mode", "normal")
                )
                if blend_mode is not BlendMode.NORMAL:
                    # Le calque est posé sur un cadre transparent (même taille
                    # que le fond), puis fusionné avec l'alpha (voir
                    # ``core.mograph_ffmpeg.blend_onto``).
                    canvas_duration = _format_seconds(max(duration, 1.0 / float(fps or 30)))
                    parts.append(
                        f"color=c=black@0:s={width}x{height}:r={fps_text}:d={canvas_duration},"
                        f"format=rgba[{p}bc{layer_index}];"
                        f"[{p}bc{layer_index}][{label}]overlay={overlay_args}:format=rgb[{p}bt{layer_index}]"
                    )
                    blend_onto(
                        parts, previous_label, f"{p}bt{layer_index}", blend_mode, next_label,
                        f"{p}vb{layer_index}", transparent_bottom=nested,
                    )
                else:
                    parts.append(
                        f"[{previous_label}][{label}]"
                        f"overlay={overlay_args}[{next_label}]"
                    )
                previous_label = next_label
            video_label = f"{p}vout"

        # ---------------- Motion graphics ----------------
        # Composés après les pistes vidéo et avant les sous-titres, par le
        # rastériseur partagé avec le viewer (``core.mograph_*``) : l'aperçu
        # fidèle et l'export appellent ce même code.
        bg_seconds = duration if duration > 0 else 1.0 / float(fps or 30)
        video_label = compose_graphics(
            parts, plan, width, height, fps, video_label, add_input,
            prefix=p, quality=quality, duration=bg_seconds, nested=nested,
        )

    if not want_audio:
        return video_label, ""

    # ---------------- Audio ----------------
    silent_base_filter = (
        f"aevalsrc=0|0:channel_layout=stereo:sample_rate=48000:"
        f"duration={_format_seconds(duration)}[{p}silent_base]"
    )
    parts.append(silent_base_filter)

    if plan.audio_layers:
        for audio_index, layer in enumerate(plan.audio_layers):
            if layer.nested_key:
                parts.append(
                    _build_audio_filter(
                        audio_index, layer, None, duration,
                        source=sources.take_audio(layer.nested_key),
                        label=f"{p}a{audio_index}",
                    )
                )
            else:
                input_index = path_to_index[layer.source_path]
                parts.append(
                    _build_audio_filter(
                        audio_index, layer, input_index, duration,
                        label=f"{p}a{audio_index}" if p else None,
                    )
                )

        # --- Ducking (tâche 28) ------------------------------------
        # Pour chaque AudioLayer ciblée par un ducking, on émet
        # des filtres ``sidechaincompress`` chaînés sur la
        # couche musique ; chaque sidechain tire d'une voix
        # mixée au préalable. Si plusieurs sidechains ciblent
        # la même musique, on les empile : le plus profond
        # l'emporte. Les labels sont tenus dans des tables locales
        # (jamais posés sur les couches ou le modèle) : un même plan
        # peut être composé plusieurs fois avec des préfixes différents.
        voice_mixes_by_track: dict = {}
        voice_mix_counter = 0
        ducked_labels: dict[int, str] = {}
        for audio_index, layer in enumerate(plan.audio_layers):
            sidechains = list(
                getattr(layer, "ducking_sidechains", []) or []
            )
            active_sidechains = [
                s for s in sidechains if getattr(s, "enabled", True)
            ]
            if not active_sidechains:
                continue
            voice_tracks_involved: dict = {}
            for sc in active_sidechains:
                voice_tracks_involved.setdefault(
                    sc.voice_track_id, []
                ).append(sc)
            for voice_track_id, sidechains_for_voice in (
                voice_tracks_involved.items()
            ):
                voice_mix_label = voice_mixes_by_track.get(voice_track_id)
                if voice_mix_label is None:
                    voice_mix_label = f"{p}av{voice_mix_counter}"
                    voice_mixes_by_track[voice_track_id] = voice_mix_label
                    voice_mix_counter += 1
                    voice_layer_indices = [
                        i for i, lay in enumerate(plan.audio_layers)
                        if lay.track_id == voice_track_id
                    ]
                    if voice_layer_indices:
                        voice_inputs = "".join(
                            f"[{p}a{i}]" for i in voice_layer_indices
                        )
                        parts.append(
                            f"{voice_inputs}"
                            f"amix=inputs={len(voice_layer_indices)}:"
                            f"duration=first:dropout_transition=0,"
                            f"aformat=channel_layouts=stereo:"
                            f"sample_rates=48000[{voice_mix_label}]"
                        )
                chain_parts = [
                    _build_ducking_chain(sc, voice_label=voice_mix_label)
                    for sc in sidechains_for_voice
                ]
                chain = ",".join(chain_parts)
                parts.append(
                    f"[{p}a{audio_index}]{chain}[{p}a{audio_index}_duck]"
                )
                ducked_labels[audio_index] = f"[{p}a{audio_index}_duck]"

        n_inputs = len(plan.audio_layers) + 1
        mixed_inputs = "".join(
            ducked_labels.get(i, f"[{p}a{i}]")
            for i in range(len(plan.audio_layers))
        )
        # Le gain Master est appliqué après l'amix : il doit
        # piloter l'ensemble du mixage, pas chaque couche. Une séquence
        # imbriquée n'a pas de Master : c'est le clip qui règle son gain.
        master_filter = "" if nested else _build_master_filter(plan)
        tail = f",{master_filter}" if master_filter else ""
        parts.append(
            f"[{p}silent_base]{mixed_inputs}"
            f"amix=inputs={n_inputs}:duration=first:dropout_transition=0"
            f"{tail},"
            f"aformat=channel_layouts=stereo:sample_rates=48000[{p}aout]"
        )
    else:
        # Aucun clip audio : on renomme la base silencieuse en ``aout``.
        parts.append(
            f"[{p}silent_base]aformat=channel_layouts=stereo:sample_rates=48000[{p}aout]"
        )
    audio_label = f"{p}aout"
    return video_label, audio_label


# ---------------------------------------------------------------------------
# Helpers de construction du filter_complex
# ---------------------------------------------------------------------------


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:160] if lines else ""


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
    """Construit la liste dédupliquée d'inputs et un mapping ``path → index``.

    Les médias des séquences imbriquées sont inclus : un fichier utilisé à
    la fois dans la séquence racine et dans une séquence imbriquée n'est
    ouvert qu'une fois. Une couche imbriquée n'a pas de fichier propre.
    """
    input_paths: list[str] = []
    path_to_index: dict[str, int] = {}
    plans = [plan] + [entry.plan for entry in getattr(plan, "nested_sequences", ()) or ()]
    for current in plans:
        for layer in list(current.video_layers) + list(current.audio_layers):
            if getattr(layer, "nested_key", ""):
                continue
            if layer.source_path in path_to_index:
                continue
            path_to_index[layer.source_path] = len(input_paths)
            input_paths.append(layer.source_path)
    # Les calques motion graphics (images, textes, formes) et les mattes de
    # masques ne sont pas des fichiers du projet : leurs flux sont produits
    # pendant la construction du graphe (``core.mograph_ffmpeg``).
    return input_paths, path_to_index


def _build_transition_layers(
    parts: list[str], plan: RenderPlan, prefix: str = ""
) -> list[tuple[str, RenderLayer]]:
    """Remplace deux couches liées par leur flux ``xfade`` FFmpeg."""
    p = prefix
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
        label = f"{p}transition{transition_index}"
        parts.append(
            f"[{p}v{from_index}]setpts=PTS-STARTPTS[{p}ta{transition_index}];"
            f"[{p}v{to_index}]setpts=PTS-STARTPTS[{p}tb{transition_index}];"
            f"[{p}ta{transition_index}][{p}tb{transition_index}]"
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
        result.append((f"{p}v{index}", layer))
    return result


def _ffmpeg_transition_name(transition: RenderTransition) -> str:
    """Convertit un :class:`TransitionType` en nom de filtre ``xfade``.

    Le mapping suit la nomenclature officielle de FFmpeg (``fade``,
    ``fadeblack``, ``wipeleft``, ``wiperight``, ``wipeup``, ``wipedown``,
    ``slideleft``, ``slideright``, ``slideup``, ``slidedown``, etc.). Les
    transitions qui ne sont pas couvertes par ``xfade`` (``pixelize``,
    ``radial``, ``smooth_left`` / ``smooth_right``, ``circle_open`` /
    ``circle_close``) sont émulées par un fondu enchaîné standard — un
    filtre dédié serait possible mais nécessiterait un ``geq`` /
    ``vstack`` coûteux. À ce niveau, le rendu reste visuellement proche
    d'un fondu doux, ce qui est acceptable pour la prévisualisation.

    Les types inconnus retombent sur ``fade`` plutôt que de planter :
    un projet corrompu ou une future valeur doit produire un export
    lisible, pas une exception.
    """
    # Le mapping est volontairement *explicite* : aucune magie sur la
    # valeur enum. Les futures ajouts n'ont qu'à ajouter une entrée.
    mapping: dict[TransitionType, str] = {
        # Historiques (tâche 23)
        TransitionType.CROSSFADE: "fade",
        TransitionType.FADE_BLACK: "fadeblack",
        TransitionType.WIPE_LEFT: "wipeleft",
        TransitionType.WIPE_RIGHT: "wiperight",
        # Balayages verticaux (tâche 26)
        TransitionType.WIPE_UP: "wipeup",
        TransitionType.WIPE_DOWN: "wipedown",
        # Glissements (tâche 26) : le second clip « pousse » l'ancien.
        TransitionType.SLIDE_UP: "slideup",
        TransitionType.SLIDE_DOWN: "slidedown",
        TransitionType.SLIDE_LEFT: "slideleft",
        TransitionType.SLIDE_RIGHT: "slideright",
        # Dissolutions (tâche 26)
        TransitionType.DISSOLVE: "dissolve",
        TransitionType.FADE_WHITE: "fadewhite",
    }
    name = mapping.get(transition.type)
    if name is not None:
        return name
    # Types non couverts nativement par ``xfade`` : repli sur ``fade``.
    # On garde la liste à jour pour faciliter la maintenance future.
    fallback = {
        TransitionType.PIXELIZE,
        TransitionType.RADIAL,
        TransitionType.CIRCLE_OPEN,
        TransitionType.CIRCLE_CLOSE,
        TransitionType.SMOOTH_LEFT,
        TransitionType.SMOOTH_RIGHT,
    }
    if transition.type in fallback:
        return "fade"
    # Type totalement inconnu : on retombe sur ``fade`` par sécurité
    # pour ne pas casser l'export d'un fichier corrompu.
    return "fade"


def _build_layer_filter(
    layer_index: int,
    layer: RenderLayer,
    input_index: int | None,
    width: int,
    height: int,
    fps: int,
    *,
    source: str | None = None,
    label: str | None = None,
    pad_color: str = "black",
    add_input=None,
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

    ``source`` remplace le flux ``[N:v]`` d'un fichier par un label du
    graphe (rendu d'une séquence imbriquée) ; ``pad_color`` permet alors
    un cadrage transparent. ``label`` renomme la sortie (``vN`` par défaut).

    Masques : avec ``add_input``, ils sont rastérisés en matte
    (:func:`core.mograph_ffmpeg.video_matte_label`) et multipliés à l'alpha
    **en espace calque**, avant échelle et rotation (ils suivent le clip).
    Miroirs et échelle X/Y (transform avancé) s'ajoutent à l'échelle ; le
    point d'ancrage est appliqué par :func:`_build_overlay_args`.
    """
    source_label = source if source is not None else f"{input_index}:v"
    output_label = label if label is not None else f"v{layer_index}"
    source_in = _format_seconds(layer.source_in)
    source_out = _format_seconds(layer.source_out)
    timeline_start = _format_seconds(layer.timeline_start)

    # ``scale`` et ``rotate`` attendent la variable temporelle
    # minuscule ``t``. ``T`` n'est définie que par certains filtres,
    # notamment ``geq`` utilisé pour l'opacité.
    transform = layer.transform
    kfs = layer.transform_keyframes
    scale_expr = _build_animated_scale_expr(transform, kfs, width, height)
    flip_filters = _build_flip_filters(transform, kfs)
    rotation_expr = _build_animated_rotation_expr(transform, kfs)
    opacity_expr = _build_animated_opacity_expr(transform, kfs)
    effect_filters = _build_clip_effect_filters(layer.effects)

    # Étalonnage couleur non destructif (tâche 29) : on génère les
    # filtres ``eq`` (exposition/contraste/saturation), ``colorbalance``
    # (température/teinte), ``curves`` (R/V/B) et ``lut3d`` (LUT
    # optionnel). Les filtres s'appliquent *après* les effets visuels
    # (ordre déterministe : effets créatifs d'abord, étalonnage ensuite)
    # afin de garantir un rendu stable quel que soit l'ordre des
    # opérations demandé par l'utilisateur.
    color_grade_filters = _build_color_grade_filters(layer.color_grade)
    from .compositing import build_ffmpeg_filters, chroma_key_filters
    matte_parts: list[str] = []
    matte_label = None
    compositing = layer.compositing
    if compositing is not None and getattr(compositing, "masks", ()) and add_input is not None:
        matte_label = video_matte_label(
            matte_parts, layer, width, height, fps, add_input, f"{output_label}_matte"
        )
        compositing_filters = ["format=rgba", *chroma_key_filters(compositing)]
    elif compositing is not None:
        compositing_filters = build_ffmpeg_filters(compositing, width, height)
    else:
        compositing_filters = ["format=rgba"]

    def apply_matte() -> str:
        """Multiplie l'alpha du calque (taille du cadre) par la matte."""
        if matte_label is None:
            return ""
        o = output_label
        return (
            f"format=rgba[{o}_pm];"
            f"[{o}_pm]split[{o}_c][{o}_a];[{o}_a]alphaextract[{o}_al];"
            f"[{o}_al][{matte_label}]blend=all_mode=multiply:shortest=0:repeatlast=1[{o}_na];"
            f"[{o}_c][{o}_na]alphamerge,"
        )

    # Filtres de remappage temporel (freeze, reverse, speed)
    time_remapping_filter = _build_time_remapping_video_filter(layer, fps)

    parts = [
        f"[{source_label}]",
        f"trim=start={source_in}:end={source_out},",
        "setpts=PTS-STARTPTS,",
    ]

    if layer.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        # Une seule image est retenue (avant tout traitement : un seul calcul),
        # mise au format du cadre, puis tenue pendant la durée du freeze.
        hold = float(layer.time_remapping.freeze_duration)
        if hold <= 0.0:
            hold = max(0.0, float(layer.timeline_end - layer.timeline_start))
        hold = max(hold, 1.0 / float(fps))
        parts.append(f"{time_remapping_filter},")
        parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,")
        parts.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{pad_color},")
        parts.append(f"tpad=stop_mode=clone:stop_duration={hold:.6f},")
        parts.append(f"fps={fps},")
        parts.append(f"trim=duration={hold:.6f},")
        parts.append("setpts=PTS-STARTPTS,")
    else:
        # Cas normal : scale/pad/fps avant le time_remapping, qui (reverse/speed)
        # doit précéder le dernier setpts pour que le décalage timeline soit correct.
        parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,")
        parts.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{pad_color},")
        parts.append(f"fps={fps},")
        if time_remapping_filter:
            parts.append(f"{time_remapping_filter},")
        parts.append("setpts=PTS-STARTPTS,")

    # Fin de chaîne commune : un arrêt sur image garde l'échelle, la rotation, les effets
    # et l'opacité animés comme n'importe quel clip (la branche freeze les ignorait).
    parts.append(apply_matte())
    parts.append(f"{scale_expr},")
    if flip_filters:
        parts.append(f"{flip_filters},")
    parts.append(f"{rotation_expr},")
    if effect_filters:
        parts.append(f"{effect_filters},")
    if color_grade_filters:
        parts.append(f"{color_grade_filters},")
    parts.append(f"{','.join(compositing_filters)},")
    parts.append(f"{opacity_expr},")
    parts.append(f"setpts=PTS+{timeline_start}/TB[{output_label}]")

    return ";".join([*matte_parts, "".join(parts)])


def _build_color_grade_filters(grade) -> str:
    """Construit la chaîne de filtres FFmpeg pour un :class:`ColorGrade`.

    L'ordre est déterministe et identique pour tous les clips :

    1. ``eq`` — exposition + contraste + saturation (toujours émis,
       même avec les valeurs par défaut, pour garantir une chaîne
       stable quand l'utilisateur active puis désactive un champ ;
       les valeurs sont au défaut, FFmpeg traite le neutre comme
       une no‑op visuelle).
    2. ``colorbalance`` — température + teinte + ombres + hautes
       lumières. Émis uniquement si non neutres, pour ne pas allonger
       la chaîne inutilement.
    3. ``curves`` — courbes master / R / V / B. Émises uniquement si
       la courbe s'écarte de l'identité (tolérance 1e‑3).
    4. ``lut3d`` — application du LUT ``.cube``. Émise uniquement si
       un LUT est attaché et que le fichier source existe ; un LUT
       ``missing`` est ignoré pour ne pas planter l'export.

    Les filtres sont séparés par des virgules : FFmpeg les compose
    dans l'ordre, ce qui correspond à l'ordre naturel d'un pipeline
    d'étalonnage (eq → colorbalance → courbes → LUT).

    Args:
        grade: instance de :class:`ColorGrade` ou ``None`` (identité).

    Returns:
        Chaîne prête à être concaténée dans un pipeline, ou
        chaîne vide si l'identité totale.
    """
    # Import paresseux pour éviter les cycles d'imports.
    from .color_grading import ColorGrade, ColorGradingError

    if grade is None or not isinstance(grade, ColorGrade):
        return ""
    if not grade.enabled:
        return ""
    # Optimisation : aucun filtre émis si tout est neutre, sauf ``eq``
    # qui reste toujours présent pour préserver la parité du pipeline
    # (cf. note dans la docstring). On continue à optimiser les autres
    # filtres.
    filters: list[str] = []
    # 1. eq : l'exposition en stops est traduite en gamma (2**EV),
    # ce qui reste dans les bornes réelles du filtre même à ±2 EV.
    # Contraste et saturation sont des multiplicateurs centrés sur 1.
    eq_contrast = _format_seconds(1.0 + float(grade.contrast))
    eq_saturation = _format_seconds(float(grade.saturation))
    eq_gamma = _format_seconds(2.0 ** float(grade.exposure))
    filters.append(
        f"eq=contrast={eq_contrast}:saturation={eq_saturation}:"
        f"gamma={eq_gamma}"
    )
    # 2. colorbalance : température/teinte agissent sur les tons moyens ;
    # ombres et hautes lumières utilisent leurs options FFmpeg dédiées.
    rm, gm, bm = _compute_colorbalance_offsets(
        float(grade.temperature), float(grade.hue)
    )
    shadow = max(-1.0, min(1.0, float(grade.shadows)))
    highlight = max(-1.0, min(1.0, float(grade.highlights)))
    if (
        any(abs(v) > 1e-3 for v in (rm, gm, bm))
        or abs(shadow) > 1e-3
        or abs(highlight) > 1e-3
    ):
        filters.append(
            "colorbalance="
            f"rs={_format_seconds(shadow)}:gs={_format_seconds(shadow)}:"
            f"bs={_format_seconds(shadow)}:"
            f"rm={_format_seconds(rm)}:gm={_format_seconds(gm)}:"
            f"bm={_format_seconds(bm)}:"
            f"rh={_format_seconds(highlight)}:gh={_format_seconds(highlight)}:"
            f"bh={_format_seconds(highlight)}:pl=1"
        )
    # 3. courbes par canal : on émet un filtre ``curves`` par canal
    # actif (s'écarte de l'identité). Les courbes master / R / V / B
    # sont composées : FFmpeg applique la première au signal
    # d'origine, puis les suivantes au résultat.
    for channel in ("master", "red", "green", "blue"):
        curve = getattr(grade.curves, channel)
        if not _curve_is_identity(curve):
            filters.append(_build_curves_filter(channel, curve))
    # 4. LUT : on ne l'émet que si le fichier existe sur disque. La
    # détection ``missing`` est gérée par la couche d'I/O ; ici on
    # s'assure juste que le chemin est valide et non vide.
    if grade.lut is not None:
        lut_filter = _build_lut3d_filter(grade.lut)
        if lut_filter:
            filters.append(lut_filter)
    return ",".join(filters)


def _curve_is_identity(curve, *, tolerance: float = 1e-3) -> bool:
    """``True`` si la courbe est numériquement égale à l'identité."""
    if not curve.is_identity():
        # ``ColorCurve.is_identity`` est strict ; ici on tolère une
        # marge plus large (les UIs écrivent souvent de petites
        # variations parasites).
        for index, (x, y) in enumerate(curve.points):
            expected = index / (len(curve.points) - 1)
            if abs(x - expected) > tolerance or abs(y - expected) > tolerance:
                return False
    return True


def _build_curves_filter(channel: str, curve) -> str:
    """Émet un filtre ``curves`` pour un canal donné.

    La syntaxe FFmpeg ``curves=`` accepte des presets (``preset=darker``)
    ou une suite de points ``x0/y0 x1/y1 ...``. On choisit la seconde
    forme pour traduire fidèlement les 16 points de notre modèle.
    """
    parts = [
        f"{_format_seconds(x)}/{_format_seconds(y)}"
        for x, y in curve.points
    ]
    expr = " ".join(parts)
    return f"curves={channel}='{expr}'"


def _build_lut3d_filter(lut) -> str | None:
    """Construit un filtre ``lut3d`` à partir d'un :class:`LUTResource`.

    Retourne ``None`` si le chemin du LUT est vide (cas dégradé : on
    ne produit pas de filtre cassé qui ferait planter l'export).
    La résolution d'un chemin absolu est laissée à l'appelant : ici
    on émet le chemin tel quel, avec une séquence d'échappement
    minimale (caractères non‑ASCII protégés par ``_escape_filter_path``).
    """
    if getattr(lut, "missing", False):
        return None
    path = getattr(lut, "source_path", None) or getattr(lut, "path", "")
    if not path:
        return None
    safe = _escape_filter_path(path)
    return f"lut3d=file='{safe}'"


def _compute_colorbalance_offsets(
    temperature: float, hue: float,
) -> tuple[float, float, float]:
    """Convertit (température, teinte) en offsets RGB ``colorbalance``.

    Le delta de température module le canal rouge vs bleu ; la teinte
    applique une légère rotation cyan/magenta. On reste conservateur :
    un delta de 100 ≈ ±0.20 sur le canal concerné.
    """
    # Température : +1 rouge, +0 vert, -1 bleu (simplification).
    red = max(-0.5, min(0.5, temperature / 200.0))
    blue = -red
    green = 0.0
    # Teinte : applique une dominante cyan/magenta. FFmpeg
    # ``colorbalance`` attend des deltas par canal primaire.
    if hue > 0:
        # Vers le magenta : rouge +, bleu -.
        red += hue / 360.0
        blue -= hue / 360.0
    elif hue < 0:
        # Vers le cyan : rouge -, bleu +.
        red += hue / 360.0
        blue -= hue / 360.0
    # On borne chaque canal à [-0.5, 0.5] : FFmpeg applique sans
    # broncher des deltas plus larges mais l'UX resterait illisible.
    return (
        max(-0.5, min(0.5, red)),
        max(-0.5, min(0.5, green)),
        max(-0.5, min(0.5, blue)),
    )


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
    axis_x = _axis_scale_expr(transform, keyframes, "scale_x")
    axis_y = _axis_scale_expr(transform, keyframes, "scale_y")
    if axis_x is None and axis_y is None:
        return (
            f"scale=w='trunc(iw*{w_expr}/iw)':h='trunc(ih*{h_expr}/ih)':"
            f"eval=frame"
        )
    # Échelle X/Y (transform avancé) : au moins 2 px, une échelle nulle
    # voudrait dire « taille d'origine » pour ``scale``.
    w_expr = f"{w_expr}*({axis_x or '1'})"
    h_expr = f"{h_expr}*({axis_y or '1'})"
    return (
        f"scale=w='max(2,trunc({w_expr}))':h='max(2,trunc({h_expr}))':"
        f"eval=frame"
    )


def _axis_scale_expr(transform: ClipTransform, keyframes, name: str) -> str | None:
    """Expression d'une échelle d'axe, ``None`` si neutre et non animée."""
    frames = [kf for kf in keyframes if kf.property_name == name]
    if not frames and float(getattr(transform, name)) == 1.0:
        return None
    return build_ffmpeg_expression(name, float(getattr(transform, name)), frames, time_var="t")


def _build_flip_filters(transform: ClipTransform, keyframes) -> str:
    """``hflip`` / ``vflip`` (statiques, ou activés par l'animation du miroir)."""
    filters = []
    for name, filter_name in (("flip_h", "hflip"), ("flip_v", "vflip")):
        frames = [kf for kf in keyframes if kf.property_name == name]
        if frames:
            expr = build_ffmpeg_expression(name, float(getattr(transform, name)), frames, time_var="t")
            filters.append(f"{filter_name}=enable='gte({expr},0.5)'")
        elif getattr(transform, name):
            filters.append(filter_name)
    return ",".join(filters)


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
    # composantes RGB identiques au pixel d'origine. L'alpha est sur
    # 0–255 : l'opacité (0–1) **multiplie** l'alpha du pixel, comme
    # ``colorchannelmixer=aa`` dans le cas statique (avant : ``a=opacité``,
    # soit un clip presque transparent dès que l'opacité était animée).
    return (
        "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
        f"a='alpha(X\\,Y)*({escaped})'"
    )


def _anchor_offset_exprs(layer, canvas_width: int, canvas_height: int, time_var: str):
    """Décalage du centre de l'image dû au point d'ancrage (``None`` si centré).

    Le calque vidéo (taille du cadre) pivote et s'échelonne autour de son
    ancrage ``A`` : son centre arrive en ``P + R·S·(c − A)`` (voir
    :func:`core.mograph_scene.local_matrix`). ``overlay`` centre l'image
    tournée ; on ajoute donc ``R·S·(c − A)``.
    """
    transform = layer.transform
    keyframes = layer.transform_keyframes

    def expr(name: str) -> str:
        frames = [kf for kf in keyframes if kf.property_name == name]
        return build_ffmpeg_expression(name, float(getattr(transform, name)), frames, time_var=time_var)

    animated = {kf.property_name for kf in keyframes}
    if (
        transform.anchor_x == 0.5 and transform.anchor_y == 0.5
        and "anchor_x" not in animated and "anchor_y" not in animated
    ):
        return None
    sx = f"({expr('scale')})*({expr('scale_x')})*(1-2*({expr('flip_h')}))"
    sy = f"({expr('scale')})*({expr('scale_y')})*(1-2*({expr('flip_v')}))"
    dx = f"({sx})*(0.5-({expr('anchor_x')}))*{_format_seconds(canvas_width)}"
    dy = f"({sy})*(0.5-({expr('anchor_y')}))*{_format_seconds(canvas_height)}"
    angle = f"({expr('rotation')})*0.017453292519943295"
    return (
        f"({dx})*cos({angle})-({dy})*sin({angle})",
        f"({dx})*sin({angle})+({dy})*cos({angle})",
    )


def _build_overlay_args(
    layer: RenderLayer | GraphicLayer,
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Construit la liste d'arguments ``key=value`` du filtre overlay.

    - ``x`` et ``y`` expriment la position animée, normalisée par
      rapport au canvas.
    - Clip vidéo : le calque transformé est **centré** (``(W-w)/2``), puis
      décalé de ``position × canvas`` — le modèle de l'aperçu. Le filtre
      ``rotate`` agrandit le calque à ``hypot(w, h)`` pour ne rien rogner ;
      poser ce calque par son coin décalait tout l'export (un clip plein
      cadre n'en couvrait que la moitié). ``w``/``h`` sont relus à chaque
      image : une échelle animée reste centrée.
    - Calque graphique : coin supérieur gauche, comme dans l'éditeur.
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
    if isinstance(layer, RenderLayer):
        x_expr = f"(W-w)/2+{x_expr}"
        y_expr = f"(H-h)/2+{y_expr}"
        anchor = _anchor_offset_exprs(layer, canvas_width, canvas_height, local_time)
        if anchor is not None:
            x_expr = f"{x_expr}+({anchor[0]})"
            y_expr = f"{y_expr}+({anchor[1]})"
    return (
        f"x='{x_expr}':y='{y_expr}':eval=frame:eof_action=pass"
    )


def _build_audio_filter(
    audio_index: int,
    layer: AudioLayer,
    input_index: int | None,
    timeline_duration: float,
    *,
    source: str | None = None,
    label: str | None = None,
) -> str:
    """Construit la chaîne de filtres FFmpeg pour une couche audio.

    Chaîne appliquée, dans cet ordre :

    1. ``atrim`` sur la portion ``[source_in, source_out]`` ;
    2. ``asetpts=PTS-STARTPTS`` pour recaler les PTS à 0 ;
    3. ``aformat`` en stéréo 48 kHz (avant tout traitement de gain, pour
       que ``pan`` et ``afade``/opèrent sur un format connu) ;
    4. Effets audio non destructifs du clip (tâche 27) : chaque
       effet activé est appliqué dans l'ordre de sa liste, ce qui
       correspond à l'ordre choisi par l'utilisateur dans le rack ;
    5. ``volume`` : gain du clip **et** volume de piste, additionnés en
       décibels ;
    6. ``afade`` d'entrée puis de sortie, seulement si non nuls ;
    7. ``pan`` stéréo, seulement si le panoramique n'est pas centré ;
    8. ``adelay`` qui décale la couche à sa position sur la timeline (rien si
       elle commence à 0). ``amix`` **ignore les horodatages** de ses entrées :
       un ``asetpts=PTS+début/TB`` ne retarde rien, le clip jouait depuis le
       début et se mélangeait au premier ; ``adelay`` retarde les échantillons.

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

    # Effets audio non destructifs du clip (tâche 27). On les applique
    # *avant* le volume / panoramique / fondus, conformément à la
    # spécification : les effets traitent le signal brut, puis le mix
    # (gain, pan, fade) finalise la couche.
    audio_effect_filters = _build_clip_audio_effect_filters(layer.audio_effects)
    if audio_effect_filters:
        steps.extend(audio_effect_filters)

    total_db = _clamp_db(layer.total_gain_db)
    if abs(total_db) > 1e-6:
        steps.append(f"volume={_format_db(total_db)}dB")

    # Automation de volume par piste (tâche 28). On applique
    # l'enveloppe via ``volume`` avec une expression ``between(t,...)``
    # linéaire entre les points, plus ``enable`` pour figer le gain
    # avant le premier / après le dernier point. Les points sont
    # ordonnés ; ``layer.timeline_start`` est ajouté pour rester dans
    # le référentiel de la timeline (l'automation est stockée dans ce
    # référentiel, pas dans celui de la source).
    envelope = _build_track_volume_envelope(
        layer.track_automation, layer.timeline_start, layer.duration
    )
    if envelope:
        steps.append(envelope)

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

    if layer.timeline_start > 0.0:
        # ``amix`` ne lit pas les horodatages : seul un retard des échantillons place le clip sur la timeline.
        delay_ms = f"{layer.timeline_start * 1000.0:.3f}"
        steps.append(f"adelay={delay_ms}|{delay_ms}")
    else:
        steps.append(f"asetpts=PTS+{timeline_start}/TB")
    source_label = source if source is not None else f"{input_index}:a"
    output_label = label if label is not None else f"a{audio_index}"
    return f"[{source_label}]" + ",".join(steps) + f"[{output_label}]"


def _build_track_volume_envelope(
    automation: tuple,
    timeline_start: float,
    duration: float,
) -> str | None:
    """Construit un filtre ``volume`` qui anime le gain via une enveloppe.

    On utilise la syntaxe ``volume=enable='between(t,start,end)':
    volume='linear_interp(t, t0, v0, t1, v1, ...)':eval=frame`` pour
    interpoler linéairement entre les points sur la durée du clip.

    Args:
        automation: tuple de :class:`AutomationPoint` (peut être vide).
        timeline_start: décalage du clip sur la timeline (s).
        duration: durée du clip sur la timeline (s).

    Returns:
        Une chaîne de filtre prête à être concaténée dans un pipeline,
        ou ``None`` si l'automation est vide.
    """
    if not automation:
        return None
    # On borne les points au segment `[range(overlay_start, overlay_end)`
    # et on émet un seul filtre ``volume``.
    points = sorted(automation, key=lambda p: p.time_seconds)
    # Bornes du clip sur la timeline (en secondes absolues).
    clip_start = float(timeline_start)
    clip_end = clip_start + float(duration)
    # Filtrer les points qui tombent dans le segment du clip (à
    # ±1 ms pour absorber les erreurs d'arrondi).
    in_segment = [
        p for p in points
        if p.time_seconds <= clip_end + 1e-3
        and p.time_seconds >= clip_start - 1e-3
    ]
    # Bord gauche : on garde le premier point du segment ; s'il n'est
    # pas dans le segment (automatisation définie avant le clip), on
    # conserve le point le plus proche (en valeur absolue).
    if not in_segment:
        # Aucun point ne touche le segment : on prend le point le plus
        # proche et on l'étend sur toute la durée du clip.
        if not points:
            return None
        first = points[0]
        last = points[-1]
        linear = (
            f"volume=enable='between(t,{clip_start:.6f},{clip_end:.6f})':"
            f"volume='{_format_db(first.gain_db)}':eval=frame"
        )
        return linear
    # Au moins un point dans le segment : on construit un
    # ``linear_interp`` avec le premier point (éventuellement répété
    # au début du clip pour figer le gain avant le premier point), les
    # points internes, et le dernier point (répété à la fin du clip).
    first = in_segment[0]
    last = in_segment[-1]
    seq: list[tuple[float, float]] = []
    # Bord gauche : si le premier point est après le début du clip,
    # on l'injecte au début du clip pour figer le gain.
    if first.time_seconds > clip_start + 1e-3:
        seq.append((clip_start, first.gain_db))
    else:
        seq.append((first.time_seconds, first.gain_db))
    seq.extend((p.time_seconds, p.gain_db) for p in in_segment)
    # Bord droit : idem pour le dernier point.
    if last.time_seconds < clip_end - 1e-3:
        seq.append((clip_end, last.gain_db))
    # Expression FFmpeg : `t0 v0 t2 v2 t4 v4` (paires temps/valeur).
    expr_parts: list[str] = []
    for t, db in seq:
        expr_parts.append(f"{t:.6f}")
        expr_parts.append(f"{db:.4f}")
    expr = " ".join(expr_parts)
    return (
        f"volume=enable='between(t,{clip_start:.6f},{clip_end:.6f})':"
        f"volume='linear_interp({expr})':eval=frame"
    )


def _build_ducking_chain(sidechain, voice_label: str | None = None) -> str:
    """Construit le filtre ``sidechaincompress`` pour un ducking.

    La couche musique est passée via ``[aN]`` au niveau de
    l'appelant (cette fonction ne fait qu'émettre la chaîne de
    filtres qui manipule ``[aN]`` en sortie). Le sidechain est
    nommé ``sidechain_<voice_track_id>`` et défini par
    l'appelant (``[avM]``).

    Les paramètres ``threshold``, ``ratio``, ``attack`` et
    ``release`` sont dérivés de :class:`DuckingConfig` :
    ``ratio`` est figé à 20:1 (pour atteindre la réduction visée
    avec un dépassement modéré du seuil), ``attack`` et ``release``
    sont convertis en secondes (FFmpeg les attend en secondes).
    """
    # Import paresseux pour éviter une dépendance forte au module
    # ``audio_automation`` (lui-même sans dépendance Qt).
    from .audio_automation import DuckingConfig

    config = getattr(sidechain, "config", None) or DuckingConfig()
    if voice_label is None:
        voice_label = getattr(sidechain, "_voice_mix_label", "voice")
    threshold = _format_db(float(config.threshold_db))
    ratio = _format_seconds(float(config.ratio))
    attack = _format_seconds(float(config.attack_seconds))
    release = _format_seconds(float(config.release_seconds))
    # ``makeup`` est laissé à 0 : la réduction n'est pas compensée,
    # c'est exactement ce que veut un ducking. Si l'utilisateur
    # veut compenser, il peut augmenter ``reduction_db`` via le
    # seuil et le ratio.
    return (
        f"sidechaincompress=threshold={threshold}:ratio={ratio}:"
        f"attack={attack}:release={release}:makeup=0:"
        f"sidechain=[{voice_label}]"
    )


def _build_clip_audio_effect_filters(effects: tuple) -> list[str]:
    """Construit la liste de filtres FFmpeg pour les effets audio activés.

    Chaque effet traduit sa catégorie en un filtre dédié
    (``loudnorm``, ``afftdn``, ``acompressor``, ``alimiter``, ``bass``,
    ``treble``, ``highpass``, ``bandpass``, ``aecho``). Tous les
    paramètres numériques viennent du modèle validé : on ne concatène
    donc jamais d'expression fournie par l'utilisateur.

    L'ordre des filtres suit l'ordre de la liste du clip, ce qui
    reflète l'ordre choisi dans le rack par l'utilisateur. La couche
    d'export ne réordonne pas : la sémantique d'un compresseur suivi
    d'un limiteur n'est pas la même que celle d'un limiteur suivi
    d'un compresseur.
    """
    filters: list[str] = []
    for effect in effects:
        if not effect.enabled:
            continue
        params = effect.params
        ftype = effect.type
        # ``loudnorm`` : on utilise les paramètres I / LRA / TP
        # directement ; un mode two-pass n'est pas nécessaire ici car
        # la cible reste intra-clip.
        if ftype.value == "normalize":
            filters.append(
                "loudnorm="
                f"I={_format_db(params['integrated_loudness'])}:"
                f"LRA={_format_seconds(params['loudness_range'])}:"
                f"TP={_format_db(params['true_peak'])}"
            )
        elif ftype.value == "voice_enhance":
            # Renforce la voix en supprimant les basses fréquences
            # parasites sous la fréquence déclarée, avec une intensité
            # proportionnelle (0 = neutre, 1 = coupe haute).
            cutoff = _format_seconds(params["frequency"])
            intensity = float(params["intensity"])
            pole = max(1, int(round(1 + intensity * 5)))
            filters.append(
                f"highpass=frequency={cutoff}:poles={pole}"
            )
        elif ftype.value == "noise_reduce":
            filters.append(
                "afftdn="
                f"nf={_format_db(params['noise_floor_db'])}:"
                f"w={_format_seconds(params['strength'])}"
            )
        elif ftype.value == "compressor":
            filters.append(
                "acompressor="
                f"threshold={_format_db(params['threshold_db'])}:"
                f"ratio={_format_seconds(params['ratio'])}:"
                f"attack={_format_seconds(params['attack_ms'])}:"
                f"release={_format_seconds(params['release_ms'])}:"
                f"makeup={_format_db(params['makeup_db'])}"
            )
        elif ftype.value == "limiter":
            filters.append(
                f"alimiter=limit={_format_db(params['limit_db'])}"
            )
        elif ftype.value == "bass_boost":
            filters.append(
                "bass="
                f"gain={_format_db(params['gain_db'])}:"
                f"frequency={_format_seconds(params['frequency_hz'])}"
            )
        elif ftype.value == "treble_boost":
            filters.append(
                "treble="
                f"gain={_format_db(params['gain_db'])}:"
                f"frequency={_format_seconds(params['frequency_hz'])}"
            )
        elif ftype.value == "phone_effect":
            center = _format_seconds(params["center_hz"])
            width = _format_seconds(params["bandwidth_hz"])
            mix = _format_seconds(params["mix"])
            filters.append(
                f"bandpass=frequency={center}:width_type=h:"
                f"width={width}:mix={mix}"
            )
        elif ftype.value == "reverb_light":
            # ``aecho`` est utilisé pour une réverbération légère en
            # empilant trois retards courts et décroissants.
            filters.append(
                _build_aecho_filter(
                    params, delays=(0.06, 0.04, 0.025), decays=(0.3, 0.25, 0.18)
                )
            )
        elif ftype.value == "echo_light":
            # Écho plus marqué : un seul retard plus long.
            filters.append(
                _build_aecho_filter(params, delays=(0.3,), decays=(0.4,))
            )
        # Les types inconnus sont silencieusement ignorés : le projet
        # a déjà été validé à la désérialisation, on reste robuste si
        # une future version ajoute un type non encore câblé ici.
    return filters


def _build_aecho_filter(
    params: dict[str, float], *, delays: tuple[float, ...], decays: tuple[float, ...]
) -> str:
    """Construit un filtre ``aecho`` à retards multiples pour réverb/écho.

    ``aecho`` ne supporte officiellement qu'un seul couple ``d / s``
    par filtre : on empile donc N filtres successifs en réutilisant
    ``in_gain`` et ``out_gain`` (l'entrée du premier est le signal
    brut, la sortie du dernier est le signal traité final).
    """
    in_gain = _format_seconds(params["in_gain"])
    out_gain = _format_seconds(params["out_gain"])
    user_delay_ms = float(params["delays_ms"])
    user_decay = float(params["decays"])
    parts: list[str] = []
    for index, (delay, decay) in enumerate(zip(delays, decays)):
        # On met à l'échelle le retard et la décroissance utilisateur
        # par le ratio ``delay / index_max_delay`` pour conserver une
        # sensation homogène entre un seul écho et trois.
        scale = delay / max(delays)
        d_ms = user_delay_ms * scale
        d_decay = user_decay * (decay / max(decays))
        parts.append(
            f"aecho="
            f"in_gain={in_gain}:"
            f"out_gain={out_gain}:"
            f"delays={_format_seconds(d_ms / 1000.0)}:"
            f"decays={_format_seconds(d_decay)}"
        )
        # À partir du 2e écho, ``in_gain`` / ``out_gain`` valent 1 pour
        # ne pas atténuer / amplifier plusieurs fois.
        in_gain = "1"
        out_gain = "1"
    return ",".join(parts)


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


def _build_time_remapping_video_filter(layer: RenderLayer, fps: float | None = None) -> str:
    """Construit les filtres de remappage temporel pour une couche vidéo.

    Applique dans l'ordre :
    1. Freeze frame (si activé) : ne garde qu'une image (``trim`` sans virgule
       interne) ; l'appelant la tient pendant ``freeze_duration``
    2. Reverse (si activé) : inverse la lecture
    3. Speed (si != 1.0) : ``setpts=PTS/vitesse`` puis ``fps`` pour revenir à
       la cadence de sortie. ``atempo`` est un filtre **audio** : dans la chaîne
       vidéo FFmpeg refuse le graphe (« Media type mismatch »).

    Args:
        layer: Couche vidéo.
        fps: Cadence de sortie, pour rééchantillonner après un changement de vitesse.

    Returns:
        Chaîne de filtres à insérer dans le filter_complex, ou chaîne vide
        si aucun remappage n'est actif.
    """
    tr = layer.time_remapping
    parts: list[str] = []

    # Freeze frame : on ne garde qu'une image, celle de l'instant figé.
    if tr.freeze_mode == FreezeFrameMode.FREEZE:
        parts.extend(
            get_ffmpeg_freeze_filter(
                tr.freeze_source_time,
                layer.source_fps if layer.source_fps > 0 else 30.0,
                source_in=layer.source_in,
            )
        )
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

    # Speed : on resserre ou on étire les timestamps, puis on rééchantillonne.
    if tr.speed != 1.0:
        parts.append(f"setpts=PTS/{clamp_speed(tr.speed):.9g}")
        if fps:
            parts.append(f"fps={fps}")

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
    """Prépare un chemin pour une valeur de filtre FFmpeg entre apostrophes.

    Le nom explicite ``filename='…'`` du filtre ``subtitles`` est important
    sous Windows : sans lui, la lettre de lecteur est traitée comme l'option
    positionnelle ``original_size``. Même dans une valeur entre apostrophes,
    FFmpeg sépare ses options sur ``:`` : le ``C:`` doit donc rester
    ``C\\:``. Les slashs sont acceptés par les trois plateformes et évitent
    aussi un double niveau d'échappement.

    Une apostrophe ne peut pas être échappée *dans* une chaîne entre apostrophes :
    ``\\'`` y est lu comme une apostrophe qui se ferme puis un caractère perdu, et
    FFmpeg ouvrait ``Jean dArc/…``. Il faut fermer la chaîne, écrire l'apostrophe
    échappée, puis la rouvrir (``'\\\\\\''``), comme en shell.
    """
    return (
        str(path)
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "'\\\\\\''")
    )


def _subtitle_fontsdir(*, platform_name: str | None = None) -> str | None:
    """Chemin d'un dossier de polices générique disponible partout.

    On pointe sur ``/System/Library/Fonts`` sur macOS et sur
    ``/usr/share/fonts/truetype/dejavu`` sur Linux. Sous Windows,
    ``fontsdir`` est volontairement omis : les chemins avec lettre de
    lecteur nécessitent un double niveau d'échappement dans le filtre
    ``subtitles`` et échouent selon les builds FFmpeg. Libass y utilise
    alors son fournisseur de polices système, ce qui est plus fiable.
    """
    from .platform_paths import system_font_dirs

    active_platform = platform_name or sys.platform
    if active_platform.startswith("win"):
        return None
    for path in system_font_dirs(platform_name=active_platform):
        if path.is_dir():
            return str(path)
    # Libass conserve son fournisseur système si aucun dossier usuel
    # n'existe (installation Windows/Linux minimale).
    return str(Path.home())


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
