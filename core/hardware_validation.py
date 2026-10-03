"""Validation de bout en bout des encodeurs : un mini export par encodeur, relu avec ``ffprobe``.

La détection (:mod:`core.hardware_encoding`) établit qu'un encodeur *s'initialise*. Elle ne dit pas
que le fichier exporté est juste. Ce module le vérifie, encodeur par encodeur, avec la **vraie
chaîne d'export de l'application** (plan de rendu, graphe de filtres, étape de couleur
:data:`~core.export_engine.OUTPUT_COLOR_STAGE`, arguments d'encodeur de
:mod:`core.video_encoders`, balises :data:`~core.export_engine.OUTPUT_COLOR_TAGS`) :

1. une source synthétique de trois bandes (rouge, vert, bleu connus), BT.709 plage limitée, sans perte ;
2. un mini export de 1 s en 320×180, commande construite par :class:`~core.export_engine.ExportEngine` ;
3. le fichier produit est **relu** avec ``ffprobe`` : matrice, primaires, transfert et plage doivent être
   ceux de ``OUTPUT_COLOR_TAGS`` (BT.709, plage limitée) ;
4. une image est décodée comme le fait un lecteur (BT.709, plage limitée) : chaque bande doit revenir à sa
   couleur, à :data:`PIXEL_TOLERANCE` niveaux près.

Trois issues, jamais confondues : **réussi** (le fichier a été produit et relu), **échec** (un backend
disponible a produit un fichier faux ou n'en a pas produit), **sauté** (le backend n'existe pas ici, ou la
détection l'a refusé : rien n'a été vérifié). Un backend exigé par :data:`REQUIRE_VARIABLE` et absent est
un échec.

Les capacités viennent du système unique de l'application (:mod:`core.hardware_cache`) : ce module ne
détecte rien lui-même. La commande d'export lit le service global
(:func:`core.hardware_cache.default_service`) : les capacités passées ici doivent en venir.

Utilisé par ``tests/test_hardware_color_validation.py`` et par l'outil de diagnostic
``python -m tools.perf.hardware_validation``. Aucun processus n'est lancé directement : tout passe par un
``runner`` injectable (les tests le simulent).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .export_engine import OUTPUT_COLOR_TAGS, ExportEngine, ExportFormat, ExportPreset, ExportRequest
from .hardware_encoding import (
    BACKEND_LABELS,
    FFMPEG_ENCODER_NAMES,
    HARDWARE_BACKENDS,
    HardwareCapabilities,
    HardwareEncoder,
    Runner,
    coerce_hardware,
    default_runner,
    redact_path,
)
from .project_model import Clip, MediaAsset, Project, Track
from .render_plan import RenderPlan, build_render_plan
from .tool_paths import find_media_tool
from .video_encoders import EncoderChoice, EncoderUnavailableError, cpu_choice

REQUIRE_VARIABLE = "KUT_STUDIO_REQUIRE_HARDWARE"
"""Backends exigés (``videotoolbox,nvenc``…) : leur absence devient un **échec** (machines dédiées)."""

VALIDATION_SIZE = (320, 180)
VALIDATION_FPS = 25
VALIDATION_SECONDS = 1.0
VALIDATION_CRF = 23
"""CRF du mini export (intention « Medium » pour les encodeurs matériels)."""
RUN_TIMEOUT_SECONDS = 60.0
PIXEL_TOLERANCE = 6
"""Écart admis par composante (encodage avec perte : ±2 mesuré avec libx264 et VideoToolbox). Mesuré en
cassant l'étape de couleur : une matrice BT.601 décale le vert de 21 niveaux, une plage pleine lue comme
limitée décale chaque bande de 11 à 15."""
SAMPLE_RADIUS = 4
"""Les couleurs sont moyennées sur un carré de 9×9 pixels au centre de chaque bande."""

VALIDATED_BACKENDS: tuple[HardwareEncoder, ...] = (HardwareEncoder.CPU, *HARDWARE_BACKENDS)
"""Ordre du rapport : le témoin logiciel d'abord, puis chaque backend matériel."""
VALIDATED_FORMATS: tuple[ExportFormat, ...] = (ExportFormat.MP4_H264, ExportFormat.MOV_H264)
"""Les formats H.264 de l'application : MP4 et MOV n'écrivent pas les balises de la même façon."""

_FFPROBE_FIELDS = {
    "-colorspace": "color_space",
    "-color_primaries": "color_primaries",
    "-color_trc": "color_transfer",
    "-color_range": "color_range",
}
"""Option de ligne de commande FFmpeg → champ de ``ffprobe`` qui la relit."""

_SOURCE_COLOR_STAGE = (
    "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p,"
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
)
"""Conversion de la source, écrite à part : la source ne dépend pas de l'étape qu'elle sert à vérifier."""

_DECODE_AS_PLAYER = "scale=in_color_matrix=bt709:in_range=tv:flags=accurate_rnd+full_chroma_int,format=rgb24"
"""Décodage d'un lecteur pour du HD BT.709. ``full_chroma_int`` : sans lui, l'interpolation rapide de la
chrominance retire elle-même 3 niveaux, ce qui mangerait la moitié de la tolérance."""


@dataclass(frozen=True)
class ColorPatch:
    """Une bande de la source : nom affiché et couleur RVB 8 bits attendue."""

    name: str
    rgb: tuple[int, int, int]


PATCHES: tuple[ColorPatch, ...] = (
    ColorPatch("rouge", (221, 92, 29)),  # la couleur de la régression BT.601 du rapport de stabilisation
    ColorPatch("vert", (40, 180, 60)),
    ColorPatch("bleu", (30, 60, 200)),
)


class Outcome(str, Enum):
    """Issue d'une vérification. « Sauté » ne vaut jamais « réussi »."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.PASSED: "RÉUSSI",
    Outcome.FAILED: "ÉCHEC",
    Outcome.SKIPPED: "SAUTÉ",
}


class ProbeError(ValueError):
    """Sortie de ``ffprobe`` ou image décodée illisible."""


# --- Attendus ---------------------------------------------------------------------------------------------------


def expected_color_tags() -> dict[str, str]:
    """Balises attendues à la relecture, dérivées de ``OUTPUT_COLOR_TAGS`` (la source unique)."""
    options = OUTPUT_COLOR_TAGS[0::2]
    values = OUTPUT_COLOR_TAGS[1::2]
    return {_FFPROBE_FIELDS[option]: value for option, value in zip(options, values)}


def required_backends(environment: Mapping[str, str] | None = None) -> frozenset[HardwareEncoder]:
    """Backends exigés par :data:`REQUIRE_VARIABLE` (vide si la variable est absente).

    Raises:
        ValueError: un nom inconnu (une faute de frappe ne doit pas rendre l'exigence muette).
    """
    env = environment if environment is not None else os.environ
    raw = str(env.get(REQUIRE_VARIABLE, "") or "")
    names = [part.strip().lower() for part in raw.replace(";", ",").replace(" ", ",").split(",")]
    allowed = {backend.value: backend for backend in VALIDATED_BACKENDS}
    required: set[HardwareEncoder] = set()
    for name in names:
        if not name:
            continue
        if name not in allowed:
            raise ValueError(
                f"{REQUIRE_VARIABLE} : backend inconnu « {name} » "
                f"(attendus : {', '.join(allowed)})"
            )
        required.add(allowed[name])
    return frozenset(required)


def skip_reason(
    capabilities: HardwareCapabilities, backend: HardwareEncoder, codec: str = "h264"
) -> str | None:
    """Pourquoi ``backend`` ne peut pas être vérifié ici (``None`` : il doit l'être).

    Le témoin logiciel n'est jamais sauté quand FFmpeg existe : c'est le chemin universel de l'export,
    son échec est un échec.
    """
    label = BACKEND_LABELS.get(backend, backend.value)
    if not capabilities.ffmpeg_available:
        return f"{label} : FFmpeg introuvable"
    if backend is HardwareEncoder.CPU:
        return None
    name = FFMPEG_ENCODER_NAMES.get((codec, backend), "")
    if capabilities.error == "disabled":
        return f"{label} : détection matérielle désactivée (KUT_STUDIO_HARDWARE_ENCODING=off)"
    item = capabilities.capability(codec, backend)
    if item is None:
        return f"{label} : encodeur absent de ce FFmpeg ({name})"
    if item.validated is False:
        return f"{label} : présent mais refusé à la validation : {item.detail or 'échec sans message'}"
    return None


# --- Lecture des résultats --------------------------------------------------------------------------------------


def parse_color_probe(text: str) -> dict[str, str]:
    """Champs du premier flux vidéo d'une sortie ``ffprobe -of json``.

    Raises:
        ProbeError: sortie vide, JSON invalide ou sans flux vidéo.
    """
    try:
        data = json.loads(text or "")
    except ValueError as error:
        excerpt = (text or "").strip().splitlines()[0][:80] if (text or "").strip() else "sortie vide"
        raise ProbeError(f"sortie de ffprobe illisible ({excerpt})") from error
    streams = data.get("streams") if isinstance(data, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise ProbeError("ffprobe ne trouve aucun flux vidéo dans le fichier")
    return {str(key): str(value) for key, value in streams[0].items() if not isinstance(value, (dict, list))}


def compare_color_tags(expected: Mapping[str, str], obtained: Mapping[str, str]) -> tuple[str, ...]:
    """Écarts lisibles (``color_transfer : attendu bt709, obtenu unknown``), vide si tout concorde."""
    problems = []
    for name, value in expected.items():
        actual = obtained.get(name, "absent")
        if actual != value:
            problems.append(f"{name} : attendu {value}, obtenu {actual}")
    return tuple(problems)


def patch_widths(width: int) -> tuple[int, ...]:
    """Largeur de chaque bande (la dernière prend le reste)."""
    base = width // len(PATCHES)
    return (*([base] * (len(PATCHES) - 1)), width - base * (len(PATCHES) - 1))


def sample_patches(frame: bytes, width: int, height: int) -> tuple[tuple[int, int, int], ...]:
    """Couleur moyenne au centre de chaque bande d'une image RVB 24 bits brute.

    Raises:
        ProbeError: l'image n'a pas la taille attendue (décodage tronqué, mauvaise dimension).
    """
    if len(frame) != width * height * 3:
        raise ProbeError(
            f"image décodée de taille inattendue ({len(frame)} octets au lieu de {width * height * 3})"
        )
    samples = []
    left = 0
    y_center = height // 2
    for bar in patch_widths(width):
        x_center = left + bar // 2
        totals = [0, 0, 0]
        count = 0
        for y in range(y_center - SAMPLE_RADIUS, y_center + SAMPLE_RADIUS + 1):
            for x in range(x_center - SAMPLE_RADIUS, x_center + SAMPLE_RADIUS + 1):
                offset = (y * width + x) * 3
                for channel in range(3):
                    totals[channel] += frame[offset + channel]
                count += 1
        samples.append((round(totals[0] / count), round(totals[1] / count), round(totals[2] / count)))
        left += bar
    return tuple(samples)


# --- Modèle du rapport ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PixelCheck:
    """Une bande relue : couleur attendue et couleur décodée."""

    name: str
    expected: tuple[int, int, int]
    obtained: tuple[int, int, int]

    @property
    def deviation(self) -> int:
        return max(abs(a - b) for a, b in zip(self.obtained, self.expected))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "expected": list(self.expected), "obtained": list(self.obtained),
                "deviation": self.deviation}


@dataclass(frozen=True)
class EncoderValidation:
    """Résultat d'un mini export pour un ``(backend, format)``.

    Attributes:
        backend: famille d'encodeur vérifiée (``CPU`` = témoin logiciel).
        container: ``mp4`` ou ``mov``.
        encoder: encodeur FFmpeg attendu pour ce backend (``h264_videotoolbox``…).
        outcome: réussi, échec ou sauté.
        reason: pourquoi c'est un échec ou un saut (vide si réussi).
        encoder_used: encodeur réellement placé dans la commande par l'application.
        returncode: code de sortie de FFmpeg (``None`` : export non lancé).
        seconds: durée du mini export.
        size_bytes: taille du fichier produit.
        expected_tags / obtained_tags: balises attendues et relues avec ``ffprobe``.
        pixels: bandes décodées.
        fallback: encodeur du repli CPU qu'Auto utiliserait si ce backend échouait (vide pour le CPU).
        output_path: fichier produit.
    """

    backend: HardwareEncoder
    container: str
    encoder: str
    outcome: Outcome
    reason: str = ""
    encoder_used: str = ""
    returncode: int | None = None
    seconds: float = 0.0
    size_bytes: int = 0
    expected_tags: dict[str, str] = field(default_factory=dict)
    obtained_tags: dict[str, str] = field(default_factory=dict)
    pixels: tuple[PixelCheck, ...] = ()
    fallback: str = ""
    output_path: str = ""

    @property
    def label(self) -> str:
        return f"{BACKEND_LABELS.get(self.backend, self.backend.value)} ({self.encoder}) · {self.container.upper()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend.value,
            "container": self.container,
            "encoder": self.encoder,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "encoder_used": self.encoder_used,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "size_bytes": self.size_bytes,
            "expected_tags": dict(self.expected_tags),
            "obtained_tags": {key: self.obtained_tags[key] for key in self.expected_tags if key in self.obtained_tags},
            "pixels": [pixel.to_dict() for pixel in self.pixels],
            "pixel_tolerance": PIXEL_TOLERANCE,
            "fallback": self.fallback,
            "output_path": redact_path(self.output_path),
        }


@dataclass(frozen=True)
class ValidationReport:
    """Rapport complet : capacités détectées, mini exports, exigences, verdict."""

    capabilities: HardwareCapabilities
    runs: tuple[EncoderValidation, ...] = ()
    required: frozenset[HardwareEncoder] = frozenset()
    error: str = ""

    @property
    def failures(self) -> tuple[EncoderValidation, ...]:
        return tuple(run for run in self.runs if run.outcome is Outcome.FAILED)

    @property
    def passed(self) -> bool:
        """Aucun échec. Des sauts sont permis (sauf exigence) : ils ne sont jamais comptés comme réussis."""
        return not self.error and not self.failures

    @property
    def exit_code(self) -> int:
        """``0`` réussi, ``1`` un backend disponible (ou exigé) a échoué, ``2`` validation impossible."""
        if self.error:
            return 2
        return 0 if self.passed else 1

    def validated_backends(self) -> tuple[HardwareEncoder, ...]:
        """Backends **matériels** dont chaque mini export a réussi (jamais un backend sauté)."""
        result = []
        for backend in HARDWARE_BACKENDS:
            outcomes = {run.outcome for run in self.runs if run.backend is backend}
            if outcomes == {Outcome.PASSED}:
                result.append(backend)
        return tuple(result)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for run in self.runs if run.outcome is outcome)

    def to_dict(self) -> dict[str, Any]:
        caps = self.capabilities
        return {
            "ffmpeg": {"version": caps.ffmpeg_version, "path": redact_path(caps.ffmpeg_path), "error": caps.error},
            "platform": caps.platform,
            "machine": caps.machine,
            "detected_encoders": [item.to_dict() for item in caps.encoders],
            "software_encoders": list(caps.software),
            "auto_h264": caps.auto_backend("h264").value,
            "hwaccels": list(caps.hwaccels),
            "detected_decoders": [item.to_dict() for item in caps.decoders],
            "decoding_disabled": caps.decoding_disabled,
            "sequence": {"size": list(VALIDATION_SIZE), "fps": VALIDATION_FPS, "seconds": VALIDATION_SECONDS,
                         "crf": VALIDATION_CRF, "codec": "h264"},
            "required": sorted(backend.value for backend in self.required),
            "runs": [run.to_dict() for run in self.runs],
            "validated_hardware": [backend.value for backend in self.validated_backends()],
            "summary": {outcome.value: self.count(outcome) for outcome in Outcome},
            "passed": self.passed,
            "exit_code": self.exit_code,
            "error": self.error,
        }


# --- Commandes ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaTools:
    """Binaires utilisés pour la source, la relecture et le décodage (l'export garde ceux de l'application)."""

    ffmpeg: str
    ffprobe: str


def media_tools() -> MediaTools | None:
    """FFmpeg et ffprobe tels que l'application les trouve (``None`` si l'un manque)."""
    ffmpeg, ffprobe = find_media_tool("ffmpeg"), find_media_tool("ffprobe")
    return MediaTools(ffmpeg, ffprobe) if ffmpeg and ffprobe else None


def source_command(ffmpeg: str, path: str) -> list[str]:
    """Source sans perte (FFV1) : trois bandes de couleur connue, BT.709 plage limitée, balisée."""
    width, height = VALIDATION_SIZE
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for patch, bar in zip(PATCHES, patch_widths(width)):
        red, green, blue = patch.rgb
        command += ["-f", "lavfi", "-i",
                    f"color=c=0x{red:02X}{green:02X}{blue:02X}:s={bar}x{height}:r={VALIDATION_FPS}"
                    f":d={VALIDATION_SECONDS}"]
    stack = "".join(f"[{index}]" for index in range(len(PATCHES)))
    graph = f"{stack}hstack=inputs={len(PATCHES)},format=rgb24,{_SOURCE_COLOR_STAGE}"
    return [*command, "-filter_complex", graph, "-c:v", "ffv1", *OUTPUT_COLOR_TAGS, path]


def probe_command(ffprobe: str, path: str) -> list[str]:
    return [
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,pix_fmt,width,height," + ",".join(_FFPROBE_FIELDS.values()),
        "-of", "json", path,
    ]


def decode_command(ffmpeg: str, video: str, frame: str) -> list[str]:
    """Première image du fichier, décodée comme un lecteur (BT.709, plage limitée), en RVB brut."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video,
        "-vf", _DECODE_AS_PLAYER, "-frames:v", "1", "-f", "rawvideo", frame,
    ]


def validation_plan(source: str) -> RenderPlan:
    """Plan de rendu d'un projet d'un seul clip : la source, sur toute sa durée."""
    width, height = VALIDATION_SIZE
    asset = MediaAsset(id="validation", path=source, name="validation", duration=VALIDATION_SECONDS,
                       width=width, height=height, fps=float(VALIDATION_FPS), media_type="video",
                       has_audio=False)
    clip = Clip(id="validation-clip", asset_id=asset.id, track_id="V1", timeline_start=0.0, source_in=0.0,
                source_out=VALIDATION_SECONDS)
    project = Project(name="validation", width=width, height=height, fps=float(VALIDATION_FPS),
                      media_assets=[asset], tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return build_render_plan(project)


def build_export_command(
    source: str, output: str, backend: HardwareEncoder, export_format: ExportFormat = ExportFormat.MP4_H264,
) -> tuple[list[str], EncoderChoice, tuple[str, ...]]:
    """Commande d'export **de l'application** : ``(commande, encodeur retenu, fichiers temporaires)``.

    Raises:
        EncoderUnavailableError: l'application refuse ce backend (capacités du service global).
        ValueError, OSError, RuntimeError, ImportError: comme un vrai export.
    """
    request = ExportRequest(
        render_plan=validation_plan(source), output_path=output, format=export_format,
        preset=ExportPreset("Validation matérielle", VALIDATION_SIZE, VALIDATION_CRF, "96k"),
        fps=VALIDATION_FPS, hardware=backend.value,
    )
    engine = ExportEngine()
    command = engine._build_command(request)
    choice = engine.last_encoder_choice
    temporary = engine.take_temporary_files()
    if not isinstance(choice, EncoderChoice):  # garde-fou : le moteur le renseigne toujours
        raise RuntimeError("le moteur d'export n'a pas annoncé d'encodeur")
    return command, choice, temporary


# --- Exécution ------------------------------------------------------------------------------------------------------


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]


def prepare_source(
    tools: MediaTools, workdir: str | os.PathLike[str], *, runner: Runner = default_runner,
    timeout: float = RUN_TIMEOUT_SECONDS,
) -> str:
    """Fabrique la source de test dans ``workdir``.

    Raises:
        RuntimeError: FFmpeg n'a pas pu la produire (message de FFmpeg).
    """
    path = str(Path(workdir) / "source.mkv")
    result = runner(source_command(tools.ffmpeg, path), timeout)
    if result.returncode != 0:
        raise RuntimeError(f"source de test impossible : {_last_line(result.stderr) or 'échec'}")
    return path


def _frame_path(workdir: str | os.PathLike[str], backend: HardwareEncoder, export_format: ExportFormat) -> str:
    return str(Path(workdir) / f"frame-{backend.value}-{export_format.container}.rgb")


def _not_verified(base: EncoderValidation, reason: str, required: Collection[HardwareEncoder]) -> EncoderValidation:
    if base.backend in required:
        return replace(base, outcome=Outcome.FAILED, reason=f"{reason} — exigé par {REQUIRE_VARIABLE}")
    return replace(base, outcome=Outcome.SKIPPED, reason=reason)


def validate_encoder(
    backend: HardwareEncoder,
    capabilities: HardwareCapabilities,
    *,
    tools: MediaTools,
    source: str,
    workdir: str | os.PathLike[str],
    export_format: ExportFormat = ExportFormat.MP4_H264,
    required: Collection[HardwareEncoder] = (),
    runner: Runner = default_runner,
    timeout: float = RUN_TIMEOUT_SECONDS,
) -> EncoderValidation:
    """Mini export de ``source`` avec ``backend``, puis relecture ; ne lève jamais."""
    codec = export_format.codec
    expected = expected_color_tags()
    fallback = ""
    if backend is not HardwareEncoder.CPU:
        # Le repli d'Auto est construit par la même fonction que celle du moteur d'export.
        fallback = cpu_choice(codec, speed_preset=export_format.preset, quality=VALIDATION_CRF,
                              requested=HardwareEncoder.AUTO).encoder
    base = EncoderValidation(
        backend=backend, container=export_format.container,
        encoder=FFMPEG_ENCODER_NAMES.get((codec, backend), ""), outcome=Outcome.SKIPPED,
        expected_tags=expected, fallback=fallback,
    )
    reason = skip_reason(capabilities, backend, codec)
    if reason:
        return _not_verified(base, reason, required)

    output = str(Path(workdir) / f"export-{backend.value}.{export_format.container}")
    try:
        command, choice, temporary = build_export_command(source, output, backend, export_format)
    except EncoderUnavailableError as error:
        label = BACKEND_LABELS.get(backend, backend.value)
        return _not_verified(base, f"{label} : refusé par l'application : {error.reason}", required)
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        return replace(base, outcome=Outcome.FAILED, reason=f"commande d'export non construite : {error}")
    base = replace(base, encoder_used=choice.encoder, output_path=output)
    if choice.used is not backend or choice.encoder != base.encoder:
        return replace(base, outcome=Outcome.FAILED,
                       reason=f"l'application a retenu {choice.encoder} au lieu de {base.encoder}")

    for stale in (output, _frame_path(workdir, backend, export_format)):
        Path(stale).unlink(missing_ok=True)  # un fichier d'un essai précédent ne compte jamais
    started = time.perf_counter()
    try:
        result = runner(command, timeout)
    finally:
        for path in temporary:
            try:
                os.remove(path)
            except OSError:
                pass
    elapsed = time.perf_counter() - started
    size = os.path.getsize(output) if os.path.isfile(output) else 0
    base = replace(base, returncode=result.returncode, seconds=elapsed, size_bytes=size)
    if result.returncode != 0:
        detail = _last_line(result.stderr) or "aucun message"
        return replace(base, outcome=Outcome.FAILED, reason=f"l'export a échoué (code {result.returncode}) : {detail}")
    if size <= 0:
        return replace(base, outcome=Outcome.FAILED, reason="l'export n'a produit aucun fichier (ou un fichier vide)")

    problems: list[str] = []
    probe = runner(probe_command(tools.ffprobe, output), timeout)
    obtained: dict[str, str] = {}
    if probe.returncode != 0:
        problems.append(f"ffprobe a échoué (code {probe.returncode}) : {_last_line(probe.stderr) or 'aucun message'}")
    else:
        try:
            obtained = parse_color_probe(probe.stdout)
        except ProbeError as error:
            problems.append(str(error))
        else:
            if obtained.get("codec_name", codec) != codec:
                problems.append(f"codec relu : attendu {codec}, obtenu {obtained.get('codec_name')}")
            problems.extend(f"balise {item}" for item in compare_color_tags(expected, obtained))
    base = replace(base, obtained_tags=obtained)

    frame_path = _frame_path(workdir, backend, export_format)
    decoded = runner(decode_command(tools.ffmpeg, output, frame_path), timeout)
    pixels: tuple[PixelCheck, ...] = ()
    if decoded.returncode != 0:
        problems.append(f"décodage impossible (code {decoded.returncode}) : {_last_line(decoded.stderr) or 'aucun message'}")
    else:
        try:
            frame = Path(frame_path).read_bytes()
            samples = sample_patches(frame, *VALIDATION_SIZE)
        except (OSError, ProbeError) as error:
            problems.append(f"image décodée illisible : {error}")
        else:
            pixels = tuple(PixelCheck(patch.name, patch.rgb, sample) for patch, sample in zip(PATCHES, samples))
            problems.extend(
                f"couleur {pixel.name} : attendu {pixel.expected}, obtenu {pixel.obtained} "
                f"(écart {pixel.deviation} > {PIXEL_TOLERANCE})"
                for pixel in pixels if pixel.deviation > PIXEL_TOLERANCE
            )
    base = replace(base, pixels=pixels)
    if problems:
        return replace(base, outcome=Outcome.FAILED, reason=" ; ".join(problems))
    return replace(base, outcome=Outcome.PASSED, reason="")


def run_validation(
    capabilities: HardwareCapabilities,
    *,
    tools: MediaTools,
    workdir: str | os.PathLike[str],
    backends: Sequence[HardwareEncoder] | None = None,
    formats: Sequence[ExportFormat] = VALIDATED_FORMATS,
    required: Collection[HardwareEncoder] = frozenset(),
    runner: Runner = default_runner,
    timeout: float = RUN_TIMEOUT_SECONDS,
) -> ValidationReport:
    """Témoin CPU puis chaque backend demandé (tous par défaut), pour chaque format ; ne lève jamais."""
    selected = list(backends) if backends else list(VALIDATED_BACKENDS)
    if HardwareEncoder.CPU not in selected:
        selected.insert(0, HardwareEncoder.CPU)  # le témoin logiciel situe tout échec matériel
    required_set = frozenset(required)
    if not capabilities.ffmpeg_available:
        return ValidationReport(capabilities, required=required_set, error="FFmpeg introuvable")
    try:
        source = prepare_source(tools, workdir, runner=runner, timeout=timeout)
    except RuntimeError as error:
        return ValidationReport(capabilities, required=required_set, error=str(error))
    runs = tuple(
        validate_encoder(backend, capabilities, tools=tools, source=source, workdir=workdir,
                         export_format=export_format, required=required_set, runner=runner, timeout=timeout)
        for backend in selected
        for export_format in formats
    )
    return ValidationReport(capabilities, runs=runs, required=required_set)


# --- Rapport lisible ----------------------------------------------------------------------------------------------


def _tags_text(tags: Mapping[str, str], names: Sequence[str]) -> str:
    return "/".join(tags.get(name, "?") for name in names)


def format_validation(run: EncoderValidation) -> str:
    """Lignes d'un mini export (utilisées aussi comme message d'échec des tests)."""
    head = f"[{OUTCOME_LABELS[run.outcome]}] {run.label}"
    if run.outcome is Outcome.SKIPPED:
        prefix = f"{BACKEND_LABELS.get(run.backend, run.backend.value)} : "
        return f"{head} : {run.reason.removeprefix(prefix)}"  # le nom figure déjà en tête de ligne
    lines = [head]
    if run.returncode is not None:
        lines.append(f"    export : {run.seconds:.2f} s, {run.size_bytes / 1024:.0f} Ko, code {run.returncode}"
                     f" (encodeur de la commande : {run.encoder_used})")
    names = list(run.expected_tags)
    if run.obtained_tags:
        lines.append(f"    balises ({'/'.join(names)}) : attendu {_tags_text(run.expected_tags, names)}, "
                     f"obtenu {_tags_text(run.obtained_tags, names)}")
    if run.pixels:
        colours = " ; ".join(f"{pixel.name} {pixel.expected} → {pixel.obtained}" for pixel in run.pixels)
        lines.append(f"    couleurs (±{PIXEL_TOLERANCE}) : {colours}")
    if run.reason:
        lines.append(f"    problème : {run.reason}")
    return "\n".join(lines)


def _fallback_text(run: EncoderValidation, witness: Mapping[str, Outcome]) -> str:
    """Ce que l'application ferait si cet encodeur échouait, et si ce repli marcherait ici."""
    state = witness.get(run.container)
    if state is Outcome.PASSED:
        verdict = "le témoin CPU a réussi : le repli produirait un fichier correct"
    elif state is Outcome.FAILED:
        verdict = "le témoin CPU a lui aussi échoué"
    else:
        verdict = "témoin CPU non exécuté"
    return (f"    repli si cet encodeur échoue : Auto relance une fois en CPU ({run.fallback}) ; un choix explicite "
            f"échoue avec « Relancer en CPU » — {verdict}")


def format_report(report: ValidationReport, *, workdir: str = "") -> str:
    """Rapport texte complet (détection, mini exports, repli, verdict)."""
    caps = report.capabilities
    width, height = VALIDATION_SIZE
    lines = [
        "Validation matérielle de l'export (Kut-Studio)",
        f"FFmpeg : {caps.ffmpeg_version or 'inconnu'} ({redact_path(caps.ffmpeg_path) or '—'}) · "
        f"{caps.platform or '—'} ({caps.machine or '—'})",
        "",
        "Encodeurs matériels détectés :",
    ]
    if caps.error == "disabled":
        lines.append("  détection désactivée (KUT_STUDIO_HARDWARE_ENCODING=off) : aucun backend matériel vérifié")
    elif not caps.ffmpeg_available:
        lines.append("  FFmpeg introuvable")
    elif not caps.encoders:
        lines.append("  aucun : ce FFmpeg n'expose aucun encodeur matériel sur cette machine (normal sans GPU) ;"
                     " seul le témoin CPU est exporté")
    for item in caps.encoders:
        if item.validated is True:
            status = "validé par l'application"
        elif item.validated is False:
            status = f"refusé à la validation ({item.detail or 'sans message'})"
        else:
            status = "listé, non validé"
        lines.append(f"  - {item.encoder} [{BACKEND_LABELS[item.backend]}] : {status}")
    if caps.software:
        lines.append(f"  logiciel : {', '.join(caps.software)}")
    lines.append(f"  Auto pour H.264 : {BACKEND_LABELS[caps.auto_backend('h264')]}")
    lines.append("Décodeurs :")
    lines.extend(f"  {line}" for line in caps.describe_decoders())
    lines += [
        "",
        f"Mini exports ({width}×{height}, {VALIDATION_SECONDS:g} s, {VALIDATION_FPS} i/s, H.264, chaîne d'export "
        f"de l'application ; balises relues avec ffprobe, couleurs décodées en BT.709) :",
    ]
    if report.error:
        lines.append(f"  impossible : {report.error}")
    witness = {run.container: run.outcome for run in report.runs if run.backend is HardwareEncoder.CPU}
    for run in report.runs:
        lines.extend(f"  {line}" for line in format_validation(run).splitlines())
        if run.outcome is not Outcome.SKIPPED and run.fallback:
            lines.append(f"  {_fallback_text(run, witness)}")
    validated = report.validated_backends()
    lines += [
        "",
        f"Résultat : {'RÉUSSI' if report.passed else 'ÉCHEC'} — {report.count(Outcome.PASSED)} réussi(s), "
        f"{report.count(Outcome.FAILED)} échec(s), {report.count(Outcome.SKIPPED)} sauté(s) ; code de sortie "
        f"{report.exit_code}.",
        "Backends matériels réellement vérifiés : "
        + (", ".join(BACKEND_LABELS[backend] for backend in validated) if validated else "aucun"),
    ]
    if report.required:
        lines.append(f"Exigés ({REQUIRE_VARIABLE}) : {', '.join(sorted(b.value for b in report.required))}")
    if report.count(Outcome.SKIPPED):
        lines.append("Un backend « sauté » n'a pas été vérifié sur cette machine : sauté ne vaut pas réussi.")
    if workdir:
        lines.append(f"Fichiers conservés : {redact_path(workdir)}")
    return "\n".join(lines)


def parse_backend_name(name: str) -> HardwareEncoder:
    """Backend désigné par son nom (``nvenc``) ou par son encodeur FFmpeg (``h264_nvenc``).

    Raises:
        ValueError: nom inconnu.
    """
    text = str(name or "").strip().lower()
    for (_codec, backend), encoder in FFMPEG_ENCODER_NAMES.items():
        if text == encoder:
            return backend
    backend = coerce_hardware(text)
    if backend.value != text or backend is HardwareEncoder.AUTO:
        allowed = ", ".join(item.value for item in VALIDATED_BACKENDS)
        raise ValueError(f"encodeur inconnu « {name} » (attendus : {allowed}, ou un encodeur FFmpeg comme h264_nvenc)")
    return backend


__all__ = [
    "OUTCOME_LABELS",
    "PATCHES",
    "PIXEL_TOLERANCE",
    "REQUIRE_VARIABLE",
    "RUN_TIMEOUT_SECONDS",
    "VALIDATED_BACKENDS",
    "VALIDATED_FORMATS",
    "VALIDATION_SIZE",
    "ColorPatch",
    "EncoderValidation",
    "MediaTools",
    "Outcome",
    "PixelCheck",
    "ProbeError",
    "ValidationReport",
    "build_export_command",
    "compare_color_tags",
    "decode_command",
    "expected_color_tags",
    "format_report",
    "format_validation",
    "media_tools",
    "parse_backend_name",
    "parse_color_probe",
    "patch_widths",
    "prepare_source",
    "probe_command",
    "required_backends",
    "run_validation",
    "sample_patches",
    "skip_reason",
    "source_command",
    "validate_encoder",
    "validation_plan",
]
