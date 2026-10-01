"""Choix de l'encodeur vidéo FFmpeg : CPU universel et encodeurs matériels.

Le moteur d'export ne connaît pas les noms d'encodeurs : il demande ici les
arguments à utiliser pour un codec, une qualité et une famille d'encodeur
(:class:`HardwareEncoder`). Trois règles :

1. **Le CPU est de première classe** : ``libx264`` / ``prores_ks`` sont toujours
   disponibles, sans détection, et leurs arguments sont identiques à ceux d'avant
   l'accélération matérielle (le CRF d'un preset est appliqué tel quel).
2. **Auto choisit parmi ce qui a été validé** (:mod:`core.hardware_encoding`) :
   un encodeur seulement *listé* par FFmpeg n'est jamais retenu. Aucun candidat
   → CPU, sans bruit.
3. **Un choix explicite n'est jamais masqué** : si l'encodeur demandé n'est pas
   utilisable, :class:`EncoderUnavailableError` explique pourquoi (l'interface
   propose alors de relancer en CPU). Le repli automatique vers le CPU ne
   concerne que ``AUTO`` et se fait au lancement du rendu (voir le moteur).

Intention de qualité
--------------------

Les paramètres de qualité ne sont pas comparables d'un encodeur à l'autre
(CRF x264, ``-cq`` NVENC, ``-global_quality`` QSV, débit VideoToolbox…). Un
preset décrit donc une **intention** (:class:`QualityIntent`), traduite ici
vers le backend. Pour la compatibilité, la valeur stockée dans les presets,
jobs et fichiers reste le CRF x264 ; :func:`intent_for_crf` en déduit
l'intention. Ce n'est pas une équivalence mathématique : le but est un
résultat cohérent, des paramètres raisonnables et un comportement prévisible.

Ajouter un backend matériel
---------------------------

1. ajouter la valeur à :class:`~core.hardware_encoding.HardwareEncoder`, son nom
   d'affichage et ses noms FFmpeg à
   :data:`~core.hardware_encoding.FFMPEG_ENCODER_NAMES` (et à
   ``HARDWARE_BACKENDS`` / ``AUTO_ORDER``) ;
2. si l'encodeur a besoin d'une initialisation (périphérique, ``hwupload``),
   compléter ``backend_input_args`` / ``backend_video_filter`` ;
3. écrire sa fonction de qualité ``_<backend>_args(params, intent)`` et
   l'enregistrer dans :data:`_QUALITY_ARGS` ;
4. ajouter des cas dans ``tests/test_hardware_encoding.py`` (sortie ``-encoders``
   simulée) : détection, choix Auto, choix explicite.

La détection, le cache, le mode Auto, le repli et les diagnostics fonctionnent
alors sans autre changement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from .hardware_encoding import (
    BACKEND_LABELS,
    CODEC_LABELS,
    FFMPEG_ENCODER_NAMES,
    HARDWARE_BACKENDS,
    EncoderCapability,
    HardwareCapabilities,
    HardwareEncoder,
    backend_input_args,
    backend_video_filter,
    coerce_hardware,
)

LOGGER = logging.getLogger("kut_studio.encoding")


class QualityIntent(str, Enum):
    """Qualité visée, indépendante de l'encodeur."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MASTER = "master"


_CRF_BY_INTENT = {
    QualityIntent.LOW: 28,
    QualityIntent.MEDIUM: 23,
    QualityIntent.HIGH: 18,
    QualityIntent.MASTER: 14,
}


def intent_for_crf(crf: int) -> QualityIntent:
    """Intention correspondant à un CRF x264 (la valeur stockée dans les presets)."""
    if crf <= 15:
        return QualityIntent.MASTER
    if crf <= 19:
        return QualityIntent.HIGH
    if crf <= 24:
        return QualityIntent.MEDIUM
    return QualityIntent.LOW


def crf_for_intent(intent: QualityIntent) -> int:
    """CRF x264 représentatif d'une intention (pour construire un preset par intention)."""
    return _CRF_BY_INTENT[intent]


class EncoderUnavailableError(ValueError):
    """Un encodeur **explicitement** demandé n'est pas utilisable ici (message lisible)."""

    def __init__(self, backend: HardwareEncoder, codec: str, reason: str) -> None:
        super().__init__(reason)
        self.backend = backend
        self.codec = codec
        self.reason = reason


@dataclass(frozen=True)
class EncodeParams:
    """Ce dont un backend a besoin pour traduire l'intention de qualité."""

    codec: str
    speed_preset: str = "medium"
    quality: int = 18
    width: int = 1920
    height: int = 1080
    fps: float = 30.0

    @property
    def intent(self) -> QualityIntent:
        return intent_for_crf(self.quality)


@dataclass(frozen=True)
class EncoderChoice:
    """Encodeur effectivement retenu et ses arguments FFmpeg.

    Attributes:
        requested: ce qui a été demandé (preset, job).
        used: ce qui sera réellement exécuté.
        encoder: nom FFmpeg (``libx264``, ``h264_videotoolbox``…).
        args: arguments ``-c:v …`` complets.
        fallback_reason: pourquoi ``used`` diffère de ``requested`` après un échec
            (``None`` quand tout est conforme, y compris ``AUTO`` → CPU d'office).
        pre_input_args: options à placer avant les entrées (initialisation matérielle).
        video_filter: filtre à ajouter en fin de graphe (format, ``hwupload``).
        codec: famille de codec (``h264``, ``hevc``, ``prores_ks``).
        intent: intention de qualité traduite (``None`` pour ProRes).
    """

    requested: HardwareEncoder
    used: HardwareEncoder
    encoder: str
    args: tuple[str, ...]
    fallback_reason: str | None = None
    pre_input_args: tuple[str, ...] = ()
    video_filter: str | None = None
    codec: str = "h264"
    intent: QualityIntent | None = None

    @property
    def is_hardware(self) -> bool:
        return self.used in HARDWARE_BACKENDS

    @property
    def label(self) -> str:
        """Libellé discret : ``H.264 · VideoToolbox`` ou ``H.264 · CPU``."""
        return encoder_label(self.codec, self.used)


def encoder_label(codec: str, backend: HardwareEncoder) -> str:
    name = BACKEND_LABELS.get(backend, backend.value).replace("Apple ", "").replace("NVIDIA ", "")
    name = name.replace("Intel ", "").replace("AMD ", "")
    return f"{CODEC_LABELS.get(codec, codec)} · {name}"


# --- Traduction de l'intention de qualité ----------------------------------------------------------------------


_BITS_PER_PIXEL = {
    QualityIntent.LOW: 0.04,
    QualityIntent.MEDIUM: 0.07,
    QualityIntent.HIGH: 0.11,
    QualityIntent.MASTER: 0.18,
}
_HEVC_BITRATE_FACTOR = 0.65  # HEVC atteint une qualité comparable avec moins de débit


def _bitrate_kbps(params: EncodeParams, intent: QualityIntent) -> int:
    rate = params.width * params.height * max(1.0, params.fps) * _BITS_PER_PIXEL[intent]
    if params.codec == "hevc":
        rate *= _HEVC_BITRATE_FACTOR
    return max(500, int(rate / 1000))


def _hevc_tag(params: EncodeParams) -> tuple[str, ...]:
    # QuickTime / Safari n'ouvrent le HEVC en MP4 qu'avec l'étiquette ``hvc1``.
    return ("-tag:v", "hvc1") if params.codec == "hevc" else ()


def _videotoolbox_args(params: EncodeParams, intent: QualityIntent) -> tuple[str, ...]:
    # Débit plutôt que ``-q:v`` : la qualité constante n'existe pas sur les Mac Intel.
    args = ["-b:v", f"{_bitrate_kbps(params, intent)}k", "-allow_sw", "0"]
    if params.codec == "h264":
        args += ["-profile:v", "high"]
    return (*args, *_hevc_tag(params))


_NVENC_CQ = {QualityIntent.LOW: 33, QualityIntent.MEDIUM: 28, QualityIntent.HIGH: 23, QualityIntent.MASTER: 19}
_QSV_QUALITY = {QualityIntent.LOW: 30, QualityIntent.MEDIUM: 25, QualityIntent.HIGH: 21, QualityIntent.MASTER: 17}
_QP = {QualityIntent.LOW: 30, QualityIntent.MEDIUM: 26, QualityIntent.HIGH: 22, QualityIntent.MASTER: 18}


def _nvenc_args(params: EncodeParams, intent: QualityIntent) -> tuple[str, ...]:
    return ("-preset", "p5", "-rc", "vbr", "-cq", str(_NVENC_CQ[intent]), "-b:v", "0", *_hevc_tag(params))


def _qsv_args(params: EncodeParams, intent: QualityIntent) -> tuple[str, ...]:
    return ("-preset", "medium", "-global_quality", str(_QSV_QUALITY[intent]), *_hevc_tag(params))


def _amf_args(params: EncodeParams, intent: QualityIntent) -> tuple[str, ...]:
    qp = str(_QP[intent])
    return ("-quality", "quality", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp, *_hevc_tag(params))


def _vaapi_args(params: EncodeParams, intent: QualityIntent) -> tuple[str, ...]:
    return ("-qp", str(_QP[intent]), *_hevc_tag(params))


_QUALITY_ARGS: dict[HardwareEncoder, Callable[[EncodeParams, QualityIntent], tuple[str, ...]]] = {
    HardwareEncoder.VIDEOTOOLBOX: _videotoolbox_args,
    HardwareEncoder.NVENC: _nvenc_args,
    HardwareEncoder.QSV: _qsv_args,
    HardwareEncoder.AMF: _amf_args,
    HardwareEncoder.VAAPI: _vaapi_args,
}
"""Table ``backend -> traduction de l'intention``. Étendre ici pour un nouveau backend."""


def validation_encoder_args(capability: EncoderCapability) -> tuple[str, ...]:
    """Arguments de qualité du mini-encodage de validation (mêmes que ceux d'un vrai rendu)."""
    params = EncodeParams(codec=capability.codec, quality=_CRF_BY_INTENT[QualityIntent.MEDIUM],
                          width=256, height=256, fps=10.0)
    builder = _QUALITY_ARGS.get(capability.backend)
    return builder(params, params.intent) if builder else ()


# --- Constructeurs -------------------------------------------------------------------------------------------------


def _cpu_choice(params: EncodeParams, requested: HardwareEncoder) -> EncoderChoice:
    codec = params.codec
    if codec == "h264":
        return EncoderChoice(
            requested, HardwareEncoder.CPU, "libx264",
            ("-c:v", "libx264", "-preset", params.speed_preset, "-crf", str(params.quality)),
            codec=codec, intent=params.intent,
        )
    if codec == "hevc":
        return EncoderChoice(
            requested, HardwareEncoder.CPU, "libx265",
            ("-c:v", "libx265", "-preset", params.speed_preset, "-crf", str(params.quality + 4),
             "-tag:v", "hvc1"),
            codec=codec, intent=params.intent,
        )
    if codec == "prores_ks":
        return EncoderChoice(
            requested, HardwareEncoder.CPU, codec,
            ("-c:v", codec, "-profile:v", str(params.quality)),
            codec=codec,
        )
    raise ValueError(f"Codec vidéo non pris en charge : {codec}")


def _hardware_choice(
    params: EncodeParams, backend: HardwareEncoder, requested: HardwareEncoder
) -> EncoderChoice:
    name = FFMPEG_ENCODER_NAMES[(params.codec, backend)]
    intent = params.intent
    args = ("-c:v", name, *_QUALITY_ARGS[backend](params, intent))
    return EncoderChoice(
        requested, backend, name, args,
        pre_input_args=backend_input_args(backend),
        video_filter=backend_video_filter(backend),
        codec=params.codec, intent=intent,
    )


def cpu_choice(
    codec: str,
    *,
    speed_preset: str = "medium",
    quality: int = 18,
    requested: HardwareEncoder = HardwareEncoder.CPU,
    fallback_reason: str | None = None,
) -> EncoderChoice:
    """Choix CPU direct (repli après un échec matériel, ou demande ``CPU``)."""
    choice = _cpu_choice(EncodeParams(codec, speed_preset, quality), requested)
    return replace(choice, fallback_reason=fallback_reason) if fallback_reason else choice


def resolve_video_encoder(
    codec: str,
    *,
    speed_preset: str = "medium",
    quality: int = 18,
    hardware: object = HardwareEncoder.CPU,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    capabilities: HardwareCapabilities | None = None,
) -> EncoderChoice:
    """Retourne l'encodeur à utiliser.

    Args:
        codec: famille de codec du format (``"h264"``, ``"hevc"`` ou ``"prores_ks"``).
        speed_preset: préréglage de vitesse x264 (ignoré par ProRes et le matériel).
        quality: CRF x264 pour H.264/HEVC (c'est aussi l'intention de qualité),
            profil pour ProRes.
        hardware: famille demandée (:class:`HardwareEncoder` ou sa valeur).
        width / height / fps: sortie, pour les backends à débit cible.
        capabilities: capacités détectées ; par défaut celles du service global
            (consultées seulement si ``hardware`` n'est pas ``CPU``).

    Raises:
        ValueError: ``codec`` n'est pris en charge par aucun encodeur.
        EncoderUnavailableError: un encodeur explicite n'est pas utilisable ici.
    """
    requested = coerce_hardware(hardware)
    params = EncodeParams(codec, speed_preset, quality, int(width), int(height), float(fps))
    if requested is HardwareEncoder.CPU:
        return _cpu_choice(params, requested)
    if capabilities is None:
        from .hardware_cache import current_capabilities

        capabilities = current_capabilities()
    if requested is HardwareEncoder.AUTO:
        backend = capabilities.auto_backend(codec) if codec in ("h264", "hevc") else HardwareEncoder.CPU
        if backend is HardwareEncoder.CPU:
            LOGGER.info("Auto : aucun encodeur matériel validé pour %s, CPU utilisé", codec)
            return _cpu_choice(params, requested)
        LOGGER.info("Auto : %s sélectionné pour %s", BACKEND_LABELS[backend], codec)
        return _hardware_choice(params, backend, requested)
    if requested in _QUALITY_ARGS and capabilities.is_usable(codec, requested):
        LOGGER.info("Encodeur explicite : %s pour %s", BACKEND_LABELS[requested], codec)
        return _hardware_choice(params, requested, requested)
    reason = capabilities.unavailable_reason(codec, requested)
    LOGGER.warning("Encodeur demandé indisponible : %s", reason)
    raise EncoderUnavailableError(requested, codec, reason)


def encoder_options(
    codec: str, capabilities: HardwareCapabilities | None = None
) -> list[tuple[HardwareEncoder, str]]:
    """Choix d'encodeur à proposer pour ``codec``, **uniquement ceux utilisables ici**.

    Toujours ``CPU`` et, sauf pour ProRes (CPU seulement), ``Auto`` (qui vaut CPU
    sans matériel) ;
    puis chaque backend matériel validé pour ce codec.
    """
    if capabilities is None:
        from .hardware_cache import current_capabilities

        capabilities = current_capabilities()
    options: list[tuple[HardwareEncoder, str]] = []
    cpu_name = FFMPEG_ENCODER_NAMES.get((codec, HardwareEncoder.CPU), codec)
    hardware = capabilities.usable_backends(codec) if codec in ("h264", "hevc") else ()
    if codec in ("h264", "hevc"):
        options.append((HardwareEncoder.AUTO, ""))  # sans matériel, Auto = CPU
    options.append((HardwareEncoder.CPU, f"CPU – {cpu_name}"))
    for backend in hardware:
        options.append((backend, f"{BACKEND_LABELS[backend]} – {CODEC_LABELS.get(codec, codec)}"))
    return options


__all__ = [
    "EncodeParams",
    "EncoderChoice",
    "EncoderUnavailableError",
    "HardwareEncoder",
    "QualityIntent",
    "coerce_hardware",
    "cpu_choice",
    "crf_for_intent",
    "encoder_label",
    "encoder_options",
    "intent_for_crf",
    "resolve_video_encoder",
    "validation_encoder_args",
]
