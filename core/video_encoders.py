"""Choix de l'encodeur vidéo FFmpeg : CPU universel et encodeurs matériels.

Le moteur d'export ne connaît pas les noms d'encodeurs : il demande ici les
arguments à utiliser pour un codec, une qualité et une famille d'encodeur
(:class:`HardwareEncoder`). Trois règles :

1. **Le CPU est de première classe** : ``libx264`` / ``prores_ks`` sont disponibles
   et leurs arguments sont identiques à ceux d'avant l'accélération matérielle (le
   CRF d'un preset est appliqué tel quel). **Sauf** dans une construction LGPL (Mac App
   Store, ADR-0013) dont la liste ``-encoders`` prouve que ``libx264`` / ``libx265``
   n'existent pas : le choix passe alors par le meilleur encodeur matériel validé
   (``h264_videotoolbox``…), avec ``fallback_reason`` renseigné, ou est refusé
   (:class:`EncoderUnavailableError`) quand il n'y en a aucun. Tant que l'on ne sait
   pas (détection coupée, capacités vides), ``libx264`` est supposé présent.
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
MIN_BITRATE_KBPS = 500
MAX_BITRATE_KBPS = 500_000
"""Bornes du débit cible (kbit/s) : un 8K à 120 i/s ne demande pas un débit que le matériel refuserait."""
_HEVC_BITRATE_FACTOR = 0.65  # HEVC atteint une qualité comparable avec moins de débit


def _bitrate_kbps(params: EncodeParams, intent: QualityIntent) -> int:
    rate = params.width * params.height * max(1.0, params.fps) * _BITS_PER_PIXEL[intent]
    if params.codec == "hevc":
        rate *= _HEVC_BITRATE_FACTOR
    return max(MIN_BITRATE_KBPS, min(MAX_BITRATE_KBPS, int(rate / 1000)))


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


def _capabilities_or_current(capabilities: HardwareCapabilities | None) -> HardwareCapabilities:
    if capabilities is not None:
        return capabilities
    from .hardware_cache import current_capabilities

    return current_capabilities()


def _known_capabilities(capabilities: HardwareCapabilities | None) -> HardwareCapabilities:
    """Capacités **déjà connues**, sans jamais lancer de détection (``HardwareCapabilities()`` vide sinon).

    Les chemins « logiciel » (CPU explicite, repli, film, aperçu, livrables) ne dépendent d'aucune détection : leur
    seule question est « libx264 existe-t-il ? ». Inconnu = oui, comme avant. L'application détecte au démarrage
    (cache disque ensuite) ; les chemins qui choisissent un encodeur matériel (Auto, explicite) détectent, eux, à la demande.
    """
    if capabilities is not None:
        return capabilities
    from .hardware_cache import default_service

    return default_service().cached() or HardwareCapabilities()


def _software_unavailable_error(codec: str, requested: HardwareEncoder, capabilities: HardwareCapabilities) -> EncoderUnavailableError:
    return EncoderUnavailableError(requested, codec, capabilities.unavailable_reason(codec, HardwareEncoder.CPU))


def _software_or_hardware_choice(
    params: EncodeParams, requested: HardwareEncoder, capabilities: HardwareCapabilities
) -> EncoderChoice:
    """Le choix « logiciel » : libx264 / libx265, ou son remplaçant matériel si ce FFmpeg ne les a pas.

    Raises:
        EncoderUnavailableError: encodeur logiciel absent **et** aucun encodeur matériel validé.
    """
    if params.codec not in ("h264", "hevc") or not capabilities.software_missing(params.codec):
        return _cpu_choice(params, requested)
    backend = capabilities.auto_backend(params.codec)
    if backend is HardwareEncoder.CPU or backend not in _QUALITY_ARGS:
        raise _software_unavailable_error(params.codec, requested, capabilities)
    software = capabilities.software_name(params.codec)
    reason = f"{software} n'existe pas dans ce FFmpeg : {BACKEND_LABELS[backend]} est utilisé à la place."
    LOGGER.warning("Encodeur logiciel absent (%s) : %s", software, BACKEND_LABELS[backend])
    return replace(_hardware_choice(params, backend, requested), fallback_reason=reason)


def cpu_choice(
    codec: str,
    *,
    speed_preset: str = "medium",
    quality: int = 18,
    requested: HardwareEncoder = HardwareEncoder.CPU,
    fallback_reason: str | None = None,
    capabilities: HardwareCapabilities | None = None,
) -> EncoderChoice:
    """Choix « logiciel » direct (repli après un échec matériel, ou demande ``CPU``).

    ``capabilities`` (``None`` : celles du service global) ne sert qu'à savoir si libx264 / libx265 existent ; sans
    information, ou avec un FFmpeg complet, le résultat est celui d'avant. Voir :func:`resolve_video_encoder`.
    """
    params = EncodeParams(codec, speed_preset, quality)
    if codec in ("h264", "hevc"):
        choice = _software_or_hardware_choice(params, requested, _known_capabilities(capabilities))
    else:
        choice = _cpu_choice(params, requested)
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
            (consultées, avec détection si besoin, pour ``AUTO`` et un matériel explicite ; pour ``CPU`` seulement
            si déjà connues : ``CPU`` ne lance jamais de détection, et ProRes n'en a pas besoin). Elles disent aussi si
            ``libx264`` / ``libx265`` existent : sinon ``CPU`` et ``AUTO`` sans matériel passent par le meilleur encodeur
            matériel validé (``fallback_reason`` renseigné) ou lèvent :class:`EncoderUnavailableError`.

    Raises:
        ValueError: ``codec`` n'est pris en charge par aucun encodeur.
        EncoderUnavailableError: un encodeur explicite n'est pas utilisable ici.
    """
    requested = coerce_hardware(hardware)
    params = EncodeParams(codec, speed_preset, quality, int(width), int(height), float(fps))
    if requested is HardwareEncoder.CPU and codec not in ("h264", "hevc"):
        return _cpu_choice(params, requested)       # ProRes : encodeur natif, rien à détecter
    if requested is HardwareEncoder.CPU:
        return _software_or_hardware_choice(params, requested, _known_capabilities(capabilities))
    capabilities = _capabilities_or_current(capabilities)
    if requested is HardwareEncoder.AUTO:
        backend = capabilities.auto_backend(codec) if codec in ("h264", "hevc") else HardwareEncoder.CPU
        if backend is HardwareEncoder.CPU:
            LOGGER.info("Auto : aucun encodeur matériel validé pour %s, CPU utilisé", codec)
            return _software_or_hardware_choice(params, requested, capabilities)
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

    ``CPU`` et, sauf pour ProRes (CPU seulement), ``Auto`` (qui vaut CPU sans matériel) ; puis chaque backend matériel
    validé pour ce codec. Quand ce FFmpeg n'a pas l'encodeur logiciel (construction LGPL du Mac App Store), ``CPU``
    n'est pas proposé, et ``Auto`` seulement s'il y a un encodeur matériel validé : la liste peut alors être **vide**
    (l'interface l'indique, au lieu de proposer un réglage qui échouerait à l'export).
    """
    capabilities = _capabilities_or_current(capabilities)
    options: list[tuple[HardwareEncoder, str]] = []
    cpu_name = FFMPEG_ENCODER_NAMES.get((codec, HardwareEncoder.CPU), codec)
    hardware = capabilities.usable_backends(codec) if codec in ("h264", "hevc") else ()
    software = not capabilities.software_missing(codec)
    if codec in ("h264", "hevc") and (software or hardware):
        options.append((HardwareEncoder.AUTO, ""))  # sans matériel, Auto = CPU
    if software:
        options.append((HardwareEncoder.CPU, f"CPU – {cpu_name}"))
    for backend in hardware:
        options.append((backend, f"{BACKEND_LABELS[backend]} – {CODEC_LABELS.get(codec, codec)}"))
    return options


# --- Autres chemins qui encodent du H.264 hors de l'export (film, livrables sociaux, aperçu) -----------------------

_AUXILIARY_RATE_ARGS: dict[HardwareEncoder, tuple[str, ...]] = {
    HardwareEncoder.VIDEOTOOLBOX: ("-allow_sw", "0", "-profile:v", "high"),
    HardwareEncoder.NVENC: ("-preset", "p5"),
    HardwareEncoder.AMF: (),
}
"""Options propres à l'encodeur matériel d'un H.264 à **débit** imposé par l'appelant (``-b:v`` ajouté par lui).
Seuls les backends sans initialisation préalable (ni ``hwupload`` ni filtre final) conviennent ici : ces chemins
construisent leur propre ligne de commande."""


def _auxiliary_backend(capabilities: HardwareCapabilities) -> HardwareEncoder | None:
    for backend in capabilities.usable_backends("h264"):
        if backend in _AUXILIARY_RATE_ARGS and not backend_input_args(backend) and backend_video_filter(backend) is None:
            return backend
    return None


def auxiliary_h264_args(
    *,
    speed_preset: str,
    crf: int | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    capabilities: HardwareCapabilities | None = None,
) -> tuple[str, ...]:
    """Arguments ``-c:v …`` d'un H.264 **secondaire** (fond de film, aperçu, copie d'aperçu d'un livrable).

    Avec libx264 (FFmpeg complet, ou capacités inconnues) : exactement ``-c:v libx264 -preset P [-crf N]``, comme
    avant. Sans libx264 (construction LGPL) : l'encodeur matériel validé qui n'a pas besoin d'initialisation
    (VideoToolbox, NVENC, AMF), avec le débit tiré de l'intention de qualité du CRF (:func:`_bitrate_kbps`, voir
    ``_videotoolbox_args``) ; sans ``crf``, l'appelant impose son ``-b:v`` et ne reçoit que les options de l'encodeur.

    Raises:
        EncoderUnavailableError: ni libx264 ni encodeur matériel utilisable.
    """
    capabilities = _known_capabilities(capabilities)
    if not capabilities.software_missing("h264"):
        return ("-c:v", "libx264", "-preset", speed_preset, *(("-crf", str(crf)) if crf is not None else ()))
    backend = _auxiliary_backend(capabilities)
    if backend is None:
        raise _software_unavailable_error("h264", HardwareEncoder.CPU, capabilities)
    name = FFMPEG_ENCODER_NAMES[("h264", backend)]
    if crf is None:
        return ("-c:v", name, *_AUXILIARY_RATE_ARGS[backend])
    params = EncodeParams("h264", speed_preset, int(crf), int(width), int(height), float(fps))
    return ("-c:v", name, *_QUALITY_ARGS[backend](params, params.intent))


def prime_capabilities() -> None:
    """Détecte les capacités (cache disque, puis mémoire) si ce n'est pas déjà fait.

    L'interface détecte au démarrage. Un export **hors interface** (ligne de commande, film) doit l'appeler avant de
    construire sa requête : un choix « CPU » ne lance jamais de détection lui-même, et ne saurait donc pas que
    ``libx264`` manque (construction LGPL). Sans effet quand la détection est coupée (``KUT_STUDIO_HARDWARE_ENCODING=off``).
    """
    _capabilities_or_current(None)


def software_fallback_possible(codec: str) -> bool:
    """Un échec d'encodeur matériel peut-il être repris par le logiciel ? (``False`` si libx264 / libx265 n'existent pas.)

    Lecture du cache seulement : le choix de l'encodeur vient de s'y référer, aucun processus n'est lancé ici.
    """
    from .hardware_cache import default_service

    known = default_service().cached()
    return known is None or not known.software_missing(codec)


__all__ = [
    "EncodeParams",
    "EncoderChoice",
    "EncoderUnavailableError",
    "HardwareEncoder",
    "QualityIntent",
    "auxiliary_h264_args",
    "coerce_hardware",
    "cpu_choice",
    "crf_for_intent",
    "encoder_label",
    "encoder_options",
    "intent_for_crf",
    "prime_capabilities",
    "resolve_video_encoder",
    "software_fallback_possible",
    "validation_encoder_args",
]
