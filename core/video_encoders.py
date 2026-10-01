"""Choix de l'encodeur vidéo FFmpeg : point d'extension de l'accélération matérielle.

Le moteur d'export ne connaît plus les noms d'encodeurs : il demande ici
les arguments ``-c:v`` à utiliser. Aujourd'hui seul le CPU (``libx264``,
``prores_ks``) est implémenté ; les autres valeurs de
:class:`HardwareEncoder` sont déjà **acceptées et sérialisées** (presets,
jobs de la file, fichiers de préférences) et retombent proprement sur le
CPU, avec une raison lisible dans :attr:`EncoderChoice.fallback_reason`.

Ajouter un encodeur matériel
----------------------------

1. écrire un constructeur ``_build_<encodeur>(codec, speed_preset, quality)``
   qui retourne un :class:`EncoderChoice` (nom FFmpeg, arguments de
   débit/qualité propres à cet encodeur) ;
2. l'enregistrer dans :data:`_BUILDERS` sous la clé
   ``(codec, HardwareEncoder.X)`` ;
3. l'ajouter à :data:`AVAILABLE_BY_DEFAULT` (ou le détecter à l'exécution
   dans :func:`is_available`, avec ``ffmpeg -encoders``) ;
4. gérer ``AUTO`` dans :func:`_auto_choice` (ordre de préférence par
   plateforme).

Rien d'autre ne change : ``ExportRequest.hardware``, ``RenderJob.hardware``
et les presets transportent déjà la valeur jusqu'ici.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class HardwareEncoder(str, Enum):
    """Famille d'encodeur demandée. La valeur est ce qui est sérialisé."""

    CPU = "cpu"
    AUTO = "auto"
    VIDEOTOOLBOX = "videotoolbox"
    NVENC = "nvenc"
    QSV = "qsv"
    AMF = "amf"
    VAAPI = "vaapi"


def coerce_hardware(value: object) -> HardwareEncoder:
    """Lit une valeur stockée ; toute valeur inconnue donne ``CPU``."""
    if isinstance(value, HardwareEncoder):
        return value
    try:
        return HardwareEncoder(str(value).strip().lower())
    except ValueError:
        return HardwareEncoder.CPU


@dataclass(frozen=True)
class EncoderChoice:
    """Encodeur effectivement retenu et ses arguments FFmpeg.

    Attributes:
        requested: ce qui a été demandé (preset, job).
        used: ce qui sera réellement exécuté.
        encoder: nom FFmpeg (``libx264``, ``prores_ks``…).
        args: arguments complets à placer dans la commande.
        fallback_reason: pourquoi ``used`` diffère de ``requested``
            (``None`` quand tout est conforme, y compris ``AUTO`` → CPU).
    """

    requested: HardwareEncoder
    used: HardwareEncoder
    encoder: str
    args: tuple[str, ...]
    fallback_reason: str | None = None


_Builder = Callable[[str, str, int], EncoderChoice]


def _build_cpu_h264(codec: str, speed_preset: str, quality: int) -> EncoderChoice:
    return EncoderChoice(
        HardwareEncoder.CPU,
        HardwareEncoder.CPU,
        "libx264",
        ("-c:v", "libx264", "-preset", speed_preset, "-crf", str(quality)),
    )


def _build_cpu_prores(codec: str, speed_preset: str, quality: int) -> EncoderChoice:
    return EncoderChoice(
        HardwareEncoder.CPU,
        HardwareEncoder.CPU,
        codec,
        ("-c:v", codec, "-profile:v", str(quality)),
    )


_BUILDERS: dict[tuple[str, HardwareEncoder], _Builder] = {
    ("h264", HardwareEncoder.CPU): _build_cpu_h264,
    ("prores_ks", HardwareEncoder.CPU): _build_cpu_prores,
}
"""Table ``(codec, famille) -> constructeur`` : seul endroit à étendre."""

AVAILABLE_BY_DEFAULT: frozenset[HardwareEncoder] = frozenset({HardwareEncoder.CPU})
"""Familles utilisables sans détection (uniquement le CPU pour l'instant)."""


def is_available(codec: str, hardware: HardwareEncoder) -> bool:
    """``True`` si ``hardware`` peut encoder ``codec`` ici et maintenant."""
    return hardware in AVAILABLE_BY_DEFAULT and (codec, hardware) in _BUILDERS


def _auto_choice(codec: str) -> HardwareEncoder:
    """Famille retenue pour ``AUTO`` ; aujourd'hui toujours le CPU."""
    return HardwareEncoder.CPU


def resolve_video_encoder(
    codec: str,
    *,
    speed_preset: str = "medium",
    quality: int = 18,
    hardware: object = HardwareEncoder.CPU,
) -> EncoderChoice:
    """Retourne l'encodeur à utiliser, avec repli CPU explicite.

    Args:
        codec: famille de codec du format (``"h264"`` ou ``"prores_ks"``).
        speed_preset: préréglage de vitesse x264 (ignoré par ProRes).
        quality: CRF pour H.264, profil pour ProRes.
        hardware: famille demandée (:class:`HardwareEncoder` ou sa valeur).

    Raises:
        ValueError: ``codec`` n'est pris en charge par aucun encodeur.
    """
    requested = coerce_hardware(hardware)
    target = _auto_choice(codec) if requested is HardwareEncoder.AUTO else requested
    builder = _BUILDERS.get((codec, target)) if is_available(codec, target) else None
    reason: str | None = None
    if builder is None:
        builder = _BUILDERS.get((codec, HardwareEncoder.CPU))
        if builder is None:
            raise ValueError(f"Codec vidéo non pris en charge : {codec}")
        if requested is not HardwareEncoder.AUTO:
            reason = (
                f"Encodeur matériel « {requested.value} » non disponible : "
                "rendu CPU utilisé."
            )
    built = builder(codec, speed_preset, quality)
    return EncoderChoice(
        requested, built.used, built.encoder, built.args, reason
    )


__all__ = [
    "AVAILABLE_BY_DEFAULT",
    "EncoderChoice",
    "HardwareEncoder",
    "coerce_hardware",
    "is_available",
    "resolve_video_encoder",
]
