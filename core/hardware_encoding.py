"""Capacités d'encodage matériel réellement disponibles avec ce FFmpeg.

Rien n'est supposé à partir du système ou du GPU : on **interroge FFmpeg**
(``-encoders``) puis, pour chaque encodeur matériel listé, on vérifie
qu'il s'initialise en encodant quelques images synthétiques vers ``null``.
Un encodeur listé mais inutilisable (pas de GPU, pilote absent, session
refusée) n'apparaît donc jamais comme disponible.

Ce module est pur (aucun Qt) et ne lance de processus que par l'intermédiaire
d'un ``runner`` injectable : les tests l'alimentent avec des sorties FFmpeg
simulées. Le cache disque et le service global sont dans
:mod:`core.hardware_cache` ; le choix de l'encodeur et la traduction de la
qualité dans :mod:`core.video_encoders`.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any

LOGGER = logging.getLogger("kut_studio.encoding")

SCHEMA_VERSION = 1
VALIDATION_TIMEOUT_SECONDS = 20.0
LIST_TIMEOUT_SECONDS = 20.0


class HardwareEncoder(str, Enum):
    """Famille d'encodeur demandée (« backend »). La valeur est sérialisée.

    ``AUTO`` et ``CPU`` ne sont pas des GPU : ``AUTO`` laisse Kut-Studio
    choisir parmi ce qui est réellement disponible, ``CPU`` est le chemin
    logiciel universel.
    """

    CPU = "cpu"
    AUTO = "auto"
    VIDEOTOOLBOX = "videotoolbox"
    NVENC = "nvenc"
    QSV = "qsv"
    AMF = "amf"
    VAAPI = "vaapi"


HARDWARE_BACKENDS: tuple[HardwareEncoder, ...] = (
    HardwareEncoder.VIDEOTOOLBOX,
    HardwareEncoder.NVENC,
    HardwareEncoder.QSV,
    HardwareEncoder.AMF,
    HardwareEncoder.VAAPI,
)
"""Les backends matériels (hors ``CPU`` et ``AUTO``)."""

BACKEND_LABELS: dict[HardwareEncoder, str] = {
    HardwareEncoder.CPU: "CPU",
    HardwareEncoder.AUTO: "Auto",
    HardwareEncoder.VIDEOTOOLBOX: "Apple VideoToolbox",
    HardwareEncoder.NVENC: "NVIDIA NVENC",
    HardwareEncoder.QSV: "Intel Quick Sync",
    HardwareEncoder.AMF: "AMD AMF",
    HardwareEncoder.VAAPI: "VAAPI",
}

CODEC_LABELS: dict[str, str] = {"h264": "H.264", "hevc": "HEVC", "prores_ks": "ProRes"}


def coerce_hardware(value: object) -> HardwareEncoder:
    """Lit une valeur stockée ; toute valeur inconnue donne ``CPU``."""
    if isinstance(value, HardwareEncoder):
        return value
    try:
        return HardwareEncoder(str(value).strip().lower())
    except ValueError:
        return HardwareEncoder.CPU


FFMPEG_ENCODER_NAMES: dict[tuple[str, HardwareEncoder], str] = {
    ("h264", HardwareEncoder.CPU): "libx264",
    ("hevc", HardwareEncoder.CPU): "libx265",
    ("h264", HardwareEncoder.VIDEOTOOLBOX): "h264_videotoolbox",
    ("hevc", HardwareEncoder.VIDEOTOOLBOX): "hevc_videotoolbox",
    ("h264", HardwareEncoder.NVENC): "h264_nvenc",
    ("hevc", HardwareEncoder.NVENC): "hevc_nvenc",
    ("h264", HardwareEncoder.QSV): "h264_qsv",
    ("hevc", HardwareEncoder.QSV): "hevc_qsv",
    ("h264", HardwareEncoder.AMF): "h264_amf",
    ("hevc", HardwareEncoder.AMF): "hevc_amf",
    ("h264", HardwareEncoder.VAAPI): "h264_vaapi",
    ("hevc", HardwareEncoder.VAAPI): "hevc_vaapi",
}
"""``(codec, backend) -> nom FFmpeg``. Seul endroit qui relie un backend à ses encodeurs."""

AUTO_ORDER: dict[str, tuple[HardwareEncoder, ...]] = {
    "darwin": (HardwareEncoder.VIDEOTOOLBOX,),
    "win32": (HardwareEncoder.NVENC, HardwareEncoder.QSV, HardwareEncoder.AMF),
    "linux": (HardwareEncoder.NVENC, HardwareEncoder.QSV, HardwareEncoder.VAAPI),
}
"""Ordre de préférence du mode Auto. Ne sert qu'à **départager** des encodeurs déjà validés."""


def auto_order(platform_name: str | None = None) -> tuple[HardwareEncoder, ...]:
    """Ordre de préférence d'Auto pour la plateforme (``sys.platform`` par défaut)."""
    name = (platform_name or sys.platform).lower()
    for prefix, order in AUTO_ORDER.items():
        if name.startswith(prefix):
            return order
    return AUTO_ORDER["linux"]


# --- Initialisation propre à chaque backend -----------------------------------------------------------


def vaapi_device(environment: dict[str, str] | None = None) -> str | None:
    """Nœud de rendu VAAPI (variable ``KUT_STUDIO_VAAPI_DEVICE`` ou premier ``renderD*``)."""
    env = environment if environment is not None else os.environ
    explicit = env.get("KUT_STUDIO_VAAPI_DEVICE")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    directory = "/dev/dri"
    try:
        names = sorted(n for n in os.listdir(directory) if n.startswith("renderD"))
    except OSError:
        return None
    return os.path.join(directory, names[0]) if names else None


def backend_input_args(backend: HardwareEncoder) -> tuple[str, ...]:
    """Options à placer **avant** les entrées pour initialiser le matériel."""
    if backend is HardwareEncoder.VAAPI:
        device = vaapi_device()
        return ("-vaapi_device", device) if device else ()
    return ()


def backend_video_filter(backend: HardwareEncoder) -> str | None:
    """Filtre final qui amène les images au format attendu par l'encodeur."""
    if backend is HardwareEncoder.VAAPI:
        return "format=nv12,hwupload"
    if backend is HardwareEncoder.QSV:
        return "format=nv12"
    return None


def backend_prerequisite_error(backend: HardwareEncoder) -> str | None:
    """Raison pour laquelle ``backend`` ne peut pas démarrer ici, sans lancer FFmpeg."""
    if backend is HardwareEncoder.VAAPI and vaapi_device() is None:
        return "aucun nœud de rendu VAAPI (/dev/dri/renderD*)"
    return None


# --- Modèle -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EncoderCapability:
    """Un encodeur ``(codec, backend)`` tel que mesuré sur cette installation.

    Attributes:
        codec: ``"h264"`` ou ``"hevc"``.
        backend: famille d'encodeur.
        encoder: nom FFmpeg (``h264_videotoolbox``…).
        listed: FFmpeg l'expose dans ``-encoders``.
        validated: ``True`` si un mini-encodage a réussi, ``False`` s'il a échoué,
            ``None`` si la validation n'a pas été faite.
        detail: raison courte d'un échec de validation (pour les diagnostics).
    """

    codec: str
    backend: HardwareEncoder
    encoder: str
    listed: bool = True
    validated: bool | None = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Listé **et** non invalidé par le mini-encodage."""
        return self.listed and self.validated is not False

    def to_dict(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "backend": self.backend.value,
            "encoder": self.encoder,
            "listed": self.listed,
            "validated": self.validated,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: object) -> EncoderCapability | None:
        if not isinstance(data, dict):
            return None
        backend = coerce_hardware(data.get("backend"))
        codec = str(data.get("codec") or "")
        encoder = str(data.get("encoder") or "")
        if backend in (HardwareEncoder.CPU, HardwareEncoder.AUTO) or not codec or not encoder:
            return None
        validated = data.get("validated")
        return cls(
            codec=codec,
            backend=backend,
            encoder=encoder,
            listed=bool(data.get("listed", True)),
            validated=validated if isinstance(validated, bool) else None,
            detail=str(data.get("detail") or ""),
        )


@dataclass(frozen=True)
class HardwareCapabilities:
    """Résultat d'une détection : ce que ce FFmpeg sait encoder, et sait *vraiment* faire.

    Attributes:
        ffmpeg_path: binaire interrogé (vide si FFmpeg est introuvable).
        ffmpeg_version: première ligne de ``ffmpeg -version`` (courte).
        platform: ``sys.platform`` à la détection.
        machine: architecture (``arm64``, ``x86_64``…).
        encoders: encodeurs matériels **listés** par FFmpeg (utilisables ou non).
        software: codecs logiciels (libx264, libx265) présents dans la liste.
        scanned_at: horodatage Unix de la détection.
        fingerprint: identité de l'installation au moment de la détection.
        error: ``""`` ou ``"ffmpeg_missing"`` / ``"ffmpeg_failed"`` / ``"disabled"``.
    """

    ffmpeg_path: str = ""
    ffmpeg_version: str = ""
    platform: str = ""
    machine: str = ""
    encoders: tuple[EncoderCapability, ...] = ()
    software: tuple[str, ...] = ()
    scanned_at: float = 0.0
    fingerprint: str = ""
    error: str = ""
    validated: bool = True

    @property
    def ffmpeg_available(self) -> bool:
        return self.error not in ("ffmpeg_missing", "ffmpeg_failed")

    def capability(self, codec: str, backend: HardwareEncoder) -> EncoderCapability | None:
        for item in self.encoders:
            if item.codec == codec and item.backend is backend:
                return item
        return None

    def is_usable(self, codec: str, backend: HardwareEncoder) -> bool:
        item = self.capability(codec, backend)
        return item is not None and item.usable

    def usable_backends(self, codec: str) -> tuple[HardwareEncoder, ...]:
        """Backends matériels utilisables pour ``codec``, dans l'ordre de préférence d'Auto."""
        found = {item.backend for item in self.encoders if item.codec == codec and item.usable}
        ordered = [b for b in auto_order(self.platform) if b in found]
        ordered.extend(b for b in HARDWARE_BACKENDS if b in found and b not in ordered)
        return tuple(ordered)

    def codecs_for(self, backend: HardwareEncoder) -> tuple[str, ...]:
        return tuple(
            item.codec for item in self.encoders if item.backend is backend and item.usable
        )

    def auto_backend(self, codec: str) -> HardwareEncoder:
        """Backend qu'Auto retiendrait pour ``codec`` (``CPU`` s'il n'y en a aucun)."""
        usable = self.usable_backends(codec)
        return usable[0] if usable else HardwareEncoder.CPU

    def unavailable_reason(self, codec: str, backend: HardwareEncoder) -> str:
        """Phrase courte expliquant pourquoi ``backend`` ne peut pas encoder ``codec`` ici."""
        label = BACKEND_LABELS.get(backend, backend.value)
        codec_label = CODEC_LABELS.get(codec, codec)
        if self.error == "disabled":
            return "L'encodage matériel est désactivé (KUT_STUDIO_HARDWARE_ENCODING=off)."
        if not self.ffmpeg_available:
            return "FFmpeg est introuvable."
        if (codec, backend) not in FFMPEG_ENCODER_NAMES:
            return f"{label} ne prend pas en charge {codec_label} dans Kut-Studio."
        item = self.capability(codec, backend)
        if item is None:
            return f"{label} n'est pas fourni par ce FFmpeg pour {codec_label}."
        if item.validated is False:
            extra = f" ({item.detail})" if item.detail else ""
            return f"{label} est listé par FFmpeg mais ne s'initialise pas{extra}."
        return f"{label} n'est pas disponible pour {codec_label}."

    # -- Sérialisation (cache disque) ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "ffmpeg_path": self.ffmpeg_path,
            "ffmpeg_version": self.ffmpeg_version,
            "platform": self.platform,
            "machine": self.machine,
            "encoders": [item.to_dict() for item in self.encoders],
            "software": list(self.software),
            "scanned_at": self.scanned_at,
            "fingerprint": self.fingerprint,
            "error": self.error,
            "validated": self.validated,
        }

    @classmethod
    def from_dict(cls, data: object) -> HardwareCapabilities | None:
        """Relit un cache ; ``None`` si le contenu est d'un autre schéma ou corrompu."""
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
            return None
        encoders = tuple(
            item
            for item in (EncoderCapability.from_dict(raw) for raw in data.get("encoders") or [])
            if item is not None
        )
        try:
            scanned = float(data.get("scanned_at") or 0.0)
        except (TypeError, ValueError):
            return None
        return cls(
            ffmpeg_path=str(data.get("ffmpeg_path") or ""),
            ffmpeg_version=str(data.get("ffmpeg_version") or ""),
            platform=str(data.get("platform") or ""),
            machine=str(data.get("machine") or ""),
            encoders=encoders,
            software=tuple(str(s) for s in data.get("software") or []),
            scanned_at=scanned,
            fingerprint=str(data.get("fingerprint") or ""),
            error=str(data.get("error") or ""),
            validated=bool(data.get("validated", True)),
        )

    # -- Diagnostic ----------------------------------------------------------------------------------------

    def describe(self) -> str:
        """Texte court, collable dans un rapport de bug (jamais la sortie brute de FFmpeg)."""
        lines = [
            f"FFmpeg : {self.ffmpeg_version or 'inconnu'}",
            f"Chemin : {redact_path(self.ffmpeg_path) or '—'}",
            f"Plateforme : {self.platform or '—'} ({self.machine or '—'})",
        ]
        if self.error:
            lines.append(f"État : {self.error}")
        lines.append(f"Logiciel : {', '.join(self.software) if self.software else 'aucun'}")
        if not self.encoders:
            lines.append("Encodeurs matériels : aucun")
        else:
            lines.append("Encodeurs matériels :")
            for item in self.encoders:
                if item.validated is True:
                    status = "OK"
                elif item.validated is False:
                    status = f"échec ({item.detail})" if item.detail else "échec"
                else:
                    status = "non validé"
                lines.append(f"  - {item.encoder} [{BACKEND_LABELS[item.backend]}] : {status}")
        for codec in ("h264", "hevc"):
            lines.append(f"Auto pour {CODEC_LABELS[codec]} : {self.auto_backend(codec).value}")
        return "\n".join(lines)


NO_FFMPEG = HardwareCapabilities(error="ffmpeg_missing")


# --- Confidentialité des logs --------------------------------------------------------------------------------


def redact_path(path: str) -> str:
    """Remplace le dossier personnel par ``~`` pour les journaux et diagnostics de support."""
    text = str(path or "")
    home = os.path.expanduser("~")
    if home and home != "~" and text.startswith(home):
        text = "~" + text[len(home):]
    return text


def redact_command(command: Sequence[str]) -> str:
    """Commande FFmpeg lisible pour un journal : fichiers réduits à leur nom.

    Les chemins complets (donc le nom de l'utilisateur et l'arborescence des
    projets) ne sont jamais écrits dans les logs destinés au support.
    """
    parts: list[str] = []
    for item in command:
        text = str(item)
        if len(text) > 200:  # filtre complexe : inutile et verbeux
            text = text[:80] + "…"
        elif (os.sep in text or "/" in text) and not text.startswith("-") and "=" not in text:
            text = os.path.basename(text.rstrip("/\\")) or text
        parts.append(text)
    return " ".join(parts)


# --- Détection --------------------------------------------------------------------------------------------------


Runner = Callable[[Sequence[str], float], "RunOutput"]


@dataclass(frozen=True)
class RunOutput:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def default_runner(command: Sequence[str], timeout: float) -> RunOutput:
    """Exécute ``command`` sans fenêtre ni stdin ; ne lève jamais (échec = code ``-1``)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return RunOutput(-1, "", "délai dépassé")
    except OSError as error:
        return RunOutput(-1, "", str(error))
    return RunOutput(completed.returncode, completed.stdout or "", completed.stderr or "")


_ENCODER_LINE = re.compile(r"^\s*V[\w.]{5}\s+(\S+)\s")
_VERSION_LINE = re.compile(r"^(?:ffmpeg|ffprobe) version\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def parse_encoder_list(text: str) -> set[str]:
    """Noms d'encodeurs **vidéo** d'une sortie ``ffmpeg -encoders``.

    Les lignes d'en-tête (``Encoders:``, ``------``) et les encodeurs audio ou
    sous-titres sont ignorés.
    """
    names: set[str] = set()
    for line in text.splitlines():
        match = _ENCODER_LINE.match(line)
        if match:
            names.add(match.group(1))
    return names


def parse_version(text: str) -> str:
    """Version courte (``7.1.1``, ``N-123-gabc``…) d'une sortie ``ffmpeg -version``."""
    match = _VERSION_LINE.search(text)
    return match.group(1) if match else ""


def validation_command(
    command: Sequence[str], capability: EncoderCapability
) -> list[str]:
    """Mini-encodage de quelques images synthétiques vers ``null`` pour un encodeur."""
    backend = capability.backend
    video_filter = backend_video_filter(backend)
    result = [
        *command,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        *backend_input_args(backend),
        "-f",
        "lavfi",
        "-i",
        "color=c=black:size=256x256:rate=10:duration=1",
        "-frames:v",
        "3",
    ]
    if video_filter:
        result.extend(["-vf", video_filter])
    from .video_encoders import validation_encoder_args  # import tardif : évite le cycle

    # Mêmes réglages de qualité qu'un vrai rendu : une option refusée se voit à la détection.
    result.extend(["-c:v", capability.encoder, *validation_encoder_args(capability), "-f", "null", "-"])
    return result


def _short_error(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:160]


def validate_capability(
    command: Sequence[str],
    capability: EncoderCapability,
    runner: Runner = default_runner,
    timeout: float = VALIDATION_TIMEOUT_SECONDS,
) -> EncoderCapability:
    """Retourne ``capability`` avec ``validated`` renseigné (jamais d'exception)."""
    from dataclasses import replace

    problem = backend_prerequisite_error(capability.backend)
    if problem:
        return replace(capability, validated=False, detail=problem)
    output = runner(validation_command(command, capability), timeout)
    if output.returncode == 0:
        return replace(capability, validated=True, detail="")
    return replace(capability, validated=False, detail=_short_error(output.stderr) or "échec")


def detect_capabilities(
    command: Sequence[str] | None,
    *,
    runner: Runner = default_runner,
    validate: bool = True,
    platform_name: str | None = None,
    machine: str | None = None,
    fingerprint: str = "",
    now: float | None = None,
    max_workers: int = 4,
) -> HardwareCapabilities:
    """Interroge FFmpeg et construit les capacités, sans jamais lever.

    Args:
        command: commande FFmpeg (préfixe) ou ``None`` si introuvable.
        runner: exécuteur de commandes (injectable pour les tests).
        validate: valider chaque encodeur matériel listé par un mini-encodage.
        fingerprint: identité de l'installation, stockée avec le résultat.
    """
    import platform as platform_module

    platform_value = platform_name or sys.platform
    machine_value = machine or platform_module.machine()
    stamp = time.time() if now is None else now
    base = dict(
        platform=platform_value, machine=machine_value, scanned_at=stamp,
        fingerprint=fingerprint, validated=validate,
    )
    if not command:
        LOGGER.info("Détection matérielle : FFmpeg introuvable")
        return HardwareCapabilities(error="ffmpeg_missing", **base)
    command = list(command)
    path = str(command[-1])  # un lanceur éventuel (python faux ffmpeg) précède le script
    listing = runner([*command, "-hide_banner", "-encoders"], LIST_TIMEOUT_SECONDS)
    if listing.returncode != 0:
        LOGGER.warning("Détection matérielle : ffmpeg -encoders a échoué (%s)", _short_error(listing.stderr))
        return HardwareCapabilities(ffmpeg_path=path, error="ffmpeg_failed", **base)
    version_output = runner([*command, "-hide_banner", "-version"], LIST_TIMEOUT_SECONDS)
    names = parse_encoder_list(listing.stdout)

    listed = [
        EncoderCapability(codec, backend, name)
        for (codec, backend), name in FFMPEG_ENCODER_NAMES.items()
        if backend in HARDWARE_BACKENDS and name in names
    ]
    if validate and listed:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(listed)))) as pool:
            listed = list(pool.map(lambda item: validate_capability(command, item, runner), listed))
    software = tuple(
        name for (codec, backend), name in FFMPEG_ENCODER_NAMES.items()
        if backend is HardwareEncoder.CPU and name in names
    )
    result = HardwareCapabilities(
        ffmpeg_path=path,
        ffmpeg_version=parse_version(version_output.stdout) if version_output.returncode == 0 else "",
        encoders=tuple(listed),
        software=software,
        **base,
    )
    LOGGER.info(
        "Détection matérielle : FFmpeg %s, encodeurs utilisables : %s",
        result.ffmpeg_version or "?",
        ", ".join(f"{i.encoder}" for i in result.encoders if i.usable) or "aucun",
    )
    for item in result.encoders:
        if item.validated is False:
            LOGGER.info("Validation échouée : %s (%s)", item.encoder, item.detail)
    return result


__all__ = [
    "AUTO_ORDER",
    "BACKEND_LABELS",
    "CODEC_LABELS",
    "FFMPEG_ENCODER_NAMES",
    "HARDWARE_BACKENDS",
    "LOGGER",
    "NO_FFMPEG",
    "EncoderCapability",
    "HardwareCapabilities",
    "HardwareEncoder",
    "RunOutput",
    "auto_order",
    "backend_input_args",
    "backend_prerequisite_error",
    "backend_video_filter",
    "coerce_hardware",
    "default_runner",
    "detect_capabilities",
    "parse_encoder_list",
    "parse_version",
    "redact_command",
    "redact_path",
    "validate_capability",
    "validation_command",
]
