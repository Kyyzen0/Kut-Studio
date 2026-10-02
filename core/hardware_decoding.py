"""Capacités de **décodage** matériel réellement disponibles avec ce FFmpeg.

Complète :mod:`core.hardware_encoding` (même service, même cache, même
diagnostic) : il n'existe qu'**un** système de détection matérielle. Ce module
décrit les décodeurs ; :func:`core.hardware_encoding.detect_capabilities`
les détecte en même temps que les encodeurs et les range dans
``HardwareCapabilities.decoders``.

Rien n'est supposé à partir du GPU présent : on lit ``ffmpeg -hwaccels``, puis
chaque couple *(backend, codec)* est validé par un **décodage strict** d'un
court échantillon :

``-hwaccel X -hwaccel_output_format <format matériel> … -vf hwdownload,format=<nv12|p010le|p210le>``

En mode strict, une image décodée en logiciel ne peut pas traverser
``hwdownload`` : la commande échoue. C'est indispensable, car en usage normal
FFmpeg retombe **silencieusement** sur le décodage logiciel quand le matériel ne
sait pas lire un profil (H.264 4:4:4 par exemple) — sans aucun message, même en
``-loglevel warning``. Une validation non stricte déclarerait donc « OK » un
décodeur qui ne décode rien.

Le module est pur (aucun Qt) ; les processus passent par le ``runner`` injecté
par :mod:`core.hardware_encoding` (les tests le simulent).
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

DECODE_VALIDATION_TIMEOUT_SECONDS = 20.0


class DecodeMode(str, Enum):
    """Mode de décodage demandé. La valeur est sérialisée dans les préférences.

    ``AUTO`` choisit parmi les backends **validés** (et mesurés, voir
    :mod:`core.decode_policy`), ``CPU`` est le décodage logiciel universel, les
    autres valeurs sont des backends explicites.
    """

    CPU = "cpu"
    AUTO = "auto"
    VIDEOTOOLBOX = "videotoolbox"
    CUDA = "cuda"
    QSV = "qsv"
    D3D11VA = "d3d11va"
    DXVA2 = "dxva2"
    VAAPI = "vaapi"


DECODE_BACKENDS: tuple[DecodeMode, ...] = (
    DecodeMode.VIDEOTOOLBOX,
    DecodeMode.CUDA,
    DecodeMode.QSV,
    DecodeMode.D3D11VA,
    DecodeMode.DXVA2,
    DecodeMode.VAAPI,
)
"""Les backends de décodage matériel (hors ``CPU`` et ``AUTO``)."""

DECODE_LABELS: dict[DecodeMode, str] = {
    DecodeMode.CPU: "CPU",
    DecodeMode.AUTO: "Auto",
    DecodeMode.VIDEOTOOLBOX: "Apple VideoToolbox",
    DecodeMode.CUDA: "NVIDIA NVDEC (CUDA)",
    DecodeMode.QSV: "Intel Quick Sync",
    DecodeMode.D3D11VA: "Direct3D 11 (D3D11VA)",
    DecodeMode.DXVA2: "DXVA2",
    DecodeMode.VAAPI: "VAAPI",
}

HWACCEL_NAMES: dict[DecodeMode, str] = {
    DecodeMode.VIDEOTOOLBOX: "videotoolbox",
    DecodeMode.CUDA: "cuda",
    DecodeMode.QSV: "qsv",
    DecodeMode.D3D11VA: "d3d11va",
    DecodeMode.DXVA2: "dxva2",
    DecodeMode.VAAPI: "vaapi",
}
"""Nom ``-hwaccel`` (et type de périphérique FFmpeg, utilisé aussi par Qt Multimedia)."""

HW_OUTPUT_FORMATS: dict[DecodeMode, str] = {
    DecodeMode.VIDEOTOOLBOX: "videotoolbox_vld",
    DecodeMode.CUDA: "cuda",
    DecodeMode.QSV: "qsv",
    DecodeMode.D3D11VA: "d3d11",
    DecodeMode.DXVA2: "dxva2_vld",
    DecodeMode.VAAPI: "vaapi",
}
"""Format d'image matériel, pour la validation stricte."""

DECODE_AUTO_ORDER: dict[str, tuple[DecodeMode, ...]] = {
    "darwin": (DecodeMode.VIDEOTOOLBOX,),
    # D3D11VA marche avec tous les GPU Windows (NVIDIA, Intel, AMD) et tous les
    # décodeurs natifs ; CUDA passe devant quand il est validé (sessions NVDEC).
    "win32": (DecodeMode.CUDA, DecodeMode.D3D11VA, DecodeMode.QSV, DecodeMode.DXVA2),
    "linux": (DecodeMode.CUDA, DecodeMode.VAAPI, DecodeMode.QSV),
}
"""Ordre de préférence d'Auto. Ne sert qu'à **départager** des backends déjà validés."""


def coerce_decode_mode(value: object) -> DecodeMode:
    """Lit une valeur stockée ; toute valeur inconnue donne ``AUTO``."""
    if isinstance(value, DecodeMode):
        return value
    try:
        return DecodeMode(str(value).strip().lower())
    except ValueError:
        return DecodeMode.AUTO


def decode_auto_order(platform_name: str | None = None) -> tuple[DecodeMode, ...]:
    name = (platform_name or sys.platform).lower()
    for prefix, order in DECODE_AUTO_ORDER.items():
        if name.startswith(prefix):
            return order
    return DECODE_AUTO_ORDER["linux"]


# --- Classes de codecs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodeCodec:
    """Une famille de flux que le matériel décode (ou non) comme un tout.

    Attributes:
        id: identifiant stable (``h264``, ``hevc10``…).
        label: nom affiché.
        codec_names: valeurs ``codec_name`` de ffprobe couvertes.
        bit_depth: profondeur des échantillons (8 ou 10).
        chroma: sous-échantillonnage accepté (``420``, ``422``).
        download_format: format logiciel de ``hwdownload`` (validation stricte).
        sample_args: encodeur logiciel qui fabrique l'échantillon de validation
            (le codec n'est validé que si cet encodeur existe dans ce FFmpeg).
        sample_extension: conteneur de l'échantillon.
    """

    id: str
    label: str
    codec_names: tuple[str, ...]
    bit_depth: int
    chroma: str
    download_format: str
    sample_args: tuple[str, ...]
    sample_extension: str = "mp4"

    @property
    def sample_encoder(self) -> str:
        return self.sample_args[self.sample_args.index("-c:v") + 1]


DECODE_CODECS: tuple[DecodeCodec, ...] = (
    DecodeCodec("h264", "H.264", ("h264",), 8, "420", "nv12",
                ("-c:v", "libx264", "-pix_fmt", "yuv420p")),
    DecodeCodec("hevc", "HEVC", ("hevc",), 8, "420", "nv12",
                ("-c:v", "libx265", "-pix_fmt", "yuv420p", "-x265-params", "log-level=error")),
    DecodeCodec("hevc10", "HEVC 10 bits", ("hevc",), 10, "420", "p010le",
                ("-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error")),
    DecodeCodec("prores", "ProRes 422", ("prores",), 10, "422", "p210le",
                ("-c:v", "prores_ks", "-profile:v", "0"), "mov"),
    DecodeCodec("vp9", "VP9", ("vp9",), 8, "420", "nv12",
                ("-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8"), "webm"),
    DecodeCodec("av1", "AV1", ("av1",), 8, "420", "nv12",
                ("-c:v", "libsvtav1", "-preset", "12")),
)
"""Familles validées. En ajouter une = une entrée (et son encodeur d'échantillon)."""

CODEC_BY_ID: dict[str, DecodeCodec] = {codec.id: codec for codec in DECODE_CODECS}

QSV_DECODERS: dict[str, str] = {
    "h264": "h264_qsv", "hevc": "hevc_qsv", "hevc10": "hevc_qsv",
    "vp9": "vp9_qsv", "av1": "av1_qsv",
}
"""QSV n'est pas un *hwaccel* des décodeurs natifs : il passe par ses propres décodeurs."""

_PIX_FMT_DEPTH = {
    "yuv420p": (8, "420"), "yuvj420p": (8, "420"), "nv12": (8, "420"),
    "yuv420p10le": (10, "420"), "yuv420p10be": (10, "420"), "p010le": (10, "420"),
    "yuv422p": (8, "422"), "yuvj422p": (8, "422"),
    "yuv422p10le": (10, "422"), "yuv422p10be": (10, "422"),
    "yuv444p": (8, "444"), "yuv444p10le": (10, "444"), "yuva444p10le": (10, "444"),
}


def codec_class(codec_name: str, pix_fmt: str = "") -> DecodeCodec | None:
    """Famille de décodage d'un flux (``None`` : à décoder en CPU, rien de validé ne couvre ce profil).

    La profondeur et le sous-échantillonnage comptent : un H.264 4:4:4 ou
    10 bits n'est **pas** un H.264 que le matériel sait lire, et FFmpeg le
    décoderait en logiciel sans le dire.
    """
    name = str(codec_name or "").strip().lower()
    depth, chroma = _PIX_FMT_DEPTH.get(str(pix_fmt or "").strip().lower(), (0, ""))
    for codec in DECODE_CODECS:
        if name not in codec.codec_names:
            continue
        if not depth:  # profil inconnu : on n'accepte que la famille 8 bits 4:2:0 par défaut
            if codec.bit_depth == 8 and codec.chroma == "420":
                return codec
            continue
        if codec.id == "prores":  # ProRes 422 (toutes variantes 4:2:2 10 bits)
            return codec if chroma == "422" else None
        if codec.bit_depth == depth and codec.chroma == chroma:
            return codec
    return None


# --- Initialisation propre à chaque backend -----------------------------------------------------------


def vaapi_render_node(environment: dict[str, str] | None = None) -> str | None:
    """Même nœud que l'encodage (``KUT_STUDIO_VAAPI_DEVICE`` ou premier ``renderD*``)."""
    from .hardware_encoding import vaapi_device

    return vaapi_device(environment)


def decode_prerequisite_error(backend: DecodeMode) -> str | None:
    """Raison pour laquelle ``backend`` ne peut pas démarrer ici, sans lancer FFmpeg."""
    if backend is DecodeMode.VAAPI and vaapi_render_node() is None:
        return "aucun nœud de rendu VAAPI (/dev/dri/renderD*)"
    return None


def decode_input_args(backend: DecodeMode, codec: DecodeCodec | None = None) -> tuple[str, ...]:
    """Options à placer **juste avant** ``-i`` pour décoder avec ``backend``.

    Les images sont **téléchargées automatiquement** en mémoire système (NV12,
    P010…) : le graphe de filtres existant (CPU) les reçoit sans changement.
    """
    if backend not in HWACCEL_NAMES:
        return ()
    args: list[str] = ["-hwaccel", HWACCEL_NAMES[backend]]
    if backend is DecodeMode.VAAPI:
        node = vaapi_render_node()
        if node:
            args += ["-hwaccel_device", node]
    if backend is DecodeMode.QSV and codec is not None and codec.id in QSV_DECODERS:
        args += ["-c:v", QSV_DECODERS[codec.id]]
    return tuple(args)


# --- Modèle ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecoderCapability:
    """Un couple *(backend, famille de codec)* tel que mesuré sur cette installation.

    Attributes:
        codec: identifiant de :data:`DECODE_CODECS`.
        backend: backend de décodage.
        listed: FFmpeg expose ce *hwaccel* (``-hwaccels``).
        validated: ``True`` décodage strict réussi, ``False`` échec, ``None`` non
            testé (encodeur d'échantillon absent de ce FFmpeg).
        detail: raison courte d'un échec (diagnostics).
    """

    codec: str
    backend: DecodeMode
    listed: bool = True
    validated: bool | None = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Validé par un décodage strict (Auto n'utilise **que** ceux-là)."""
        return self.listed and self.validated is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "backend": self.backend.value,
            "listed": self.listed,
            "validated": self.validated,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: object) -> DecoderCapability | None:
        if not isinstance(data, dict):
            return None
        backend = coerce_decode_mode(data.get("backend"))
        codec = str(data.get("codec") or "")
        if backend not in DECODE_BACKENDS or codec not in CODEC_BY_ID:
            return None
        validated = data.get("validated")
        return cls(
            codec=codec,
            backend=backend,
            listed=bool(data.get("listed", True)),
            validated=validated if isinstance(validated, bool) else None,
            detail=str(data.get("detail") or ""),
        )


# --- Détection ------------------------------------------------------------------------------------------


def parse_hwaccel_list(text: str) -> set[str]:
    """Noms d'une sortie ``ffmpeg -hwaccels`` (l'en-tête est ignoré)."""
    names: set[str] = set()
    for line in (text or "").splitlines():
        word = line.strip()
        if not word or word.endswith(":") or " " in word:
            continue
        names.add(word.lower())
    return names


def listed_backends(hwaccels: set[str]) -> tuple[DecodeMode, ...]:
    return tuple(b for b in DECODE_BACKENDS if HWACCEL_NAMES[b] in hwaccels)


def sample_command(command: Sequence[str], codec: DecodeCodec, output: str) -> list[str]:
    """Fabrique un échantillon 256×144 de 5 images avec un encodeur **logiciel**."""
    return [
        *command, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=10:duration=0.5",
        *codec.sample_args, output,
    ]


def strict_validation_command(
    command: Sequence[str], backend: DecodeMode, codec: DecodeCodec, sample: str
) -> list[str]:
    """Décodage strict : échoue si une seule image ne vient pas du matériel."""
    args = list(decode_input_args(backend, codec))
    args += ["-hwaccel_output_format", HW_OUTPUT_FORMATS[backend]]
    return [
        *command, "-hide_banner", "-loglevel", "error", "-nostdin",
        *args, "-i", sample, "-frames:v", "3",
        "-vf", f"hwdownload,format={codec.download_format}", "-f", "null", "-",
    ]


@dataclass(frozen=True)
class _Failed:
    stderr: str
    returncode: int = -1
    stdout: str = ""


def _short_error(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:160]


def detect_decoders(
    command: Sequence[str],
    runner: Callable[[Sequence[str], float], Any],
    *,
    encoder_names: set[str],
    validate: bool = True,
    max_workers: int = 4,
    sample_dir: str | None = None,
) -> tuple[tuple[str, ...], tuple[DecoderCapability, ...]]:
    """``(hwaccels listés, capacités de décodage)`` ; ne lève jamais.

    Args:
        command: préfixe FFmpeg.
        runner: exécuteur ``(commande, délai) -> RunOutput``.
        encoder_names: encodeurs de ce FFmpeg (``-encoders``) : un codec n'est
            testable que si son encodeur d'échantillon est présent.
        validate: faire le décodage strict (sinon ``validated=None``).
    """
    from concurrent.futures import ThreadPoolExecutor

    def run(arguments: list[str]):
        try:
            return runner(arguments, DECODE_VALIDATION_TIMEOUT_SECONDS)
        except Exception as error:  # un runner défaillant n'interrompt jamais la détection
            return _Failed(str(error))

    listing = run([*command, "-hide_banner", "-hwaccels"])
    if getattr(listing, "returncode", 1) != 0:
        return (), ()
    hwaccels = parse_hwaccel_list(getattr(listing, "stdout", ""))
    backends = listed_backends(hwaccels)
    if not backends:
        return tuple(sorted(hwaccels)), ()
    items = [DecoderCapability(codec.id, backend) for backend in backends for codec in DECODE_CODECS]
    if not validate:
        return tuple(sorted(hwaccels)), tuple(items)

    owned = sample_dir is None
    directory = sample_dir or tempfile.mkdtemp(prefix="kut-decode-check-")
    try:
        samples: dict[str, str | None] = {}
        for codec in DECODE_CODECS:
            if codec.sample_encoder not in encoder_names:
                samples[codec.id] = None
                continue
            path = os.path.join(directory, f"{codec.id}.{codec.sample_extension}")
            made = run(sample_command(command, codec, path))
            samples[codec.id] = path if getattr(made, "returncode", 1) == 0 else None

        def check(item: DecoderCapability) -> DecoderCapability:
            codec = CODEC_BY_ID[item.codec]
            problem = decode_prerequisite_error(item.backend)
            if problem:
                return replace(item, validated=False, detail=problem)
            sample = samples.get(item.codec)
            if not sample:
                return replace(item, validated=None, detail="échantillon impossible (encodeur absent)")
            output = run(strict_validation_command(command, item.backend, codec, sample))
            if getattr(output, "returncode", 1) == 0:
                return replace(item, validated=True, detail="")
            return replace(item, validated=False, detail=_short_error(getattr(output, "stderr", "")) or "échec")

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items)))) as pool:
            checked = tuple(pool.map(check, items))
    finally:
        if owned:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)
    return tuple(sorted(hwaccels)), checked


__all__ = [
    "CODEC_BY_ID",
    "DECODE_AUTO_ORDER",
    "DECODE_BACKENDS",
    "DECODE_CODECS",
    "DECODE_LABELS",
    "HWACCEL_NAMES",
    "HW_OUTPUT_FORMATS",
    "DecodeCodec",
    "DecodeMode",
    "DecoderCapability",
    "codec_class",
    "coerce_decode_mode",
    "decode_auto_order",
    "decode_input_args",
    "decode_prerequisite_error",
    "detect_decoders",
    "listed_backends",
    "parse_hwaccel_list",
    "sample_command",
    "strict_validation_command",
]
