"""Profils de proxies média : résolution, codec, qualité, audio.

Un **proxy** est une copie légère d'un média lourd (4K, H.265, ProRes…)
utilisée **uniquement pour l'aperçu**. L'export utilise toujours le média
original (voir :meth:`core.proxy_manager.ProxyManager.resolve_for_export`).

Un profil décrit le proxy ; il ne contient aucune logique de lancement :
:meth:`ProxyProfile.ffmpeg_output_args` retourne les arguments FFmpeg, et
c'est tout. Le gestionnaire s'occupe du reste.

Pas lié à un codec unique
-------------------------

Les codecs sont dans une table (:data:`PROXY_CODECS`) : chaque entrée sait
construire ses arguments vidéo et indique son conteneur. H.264 et ProRes
Proxy sont fournis ; en ajouter un (DNxHR LB, MJPEG…) = une entrée de
table, sans toucher au gestionnaire.

Les arguments d'encodage vidéo passent par
:func:`core.video_encoders.resolve_video_encoder`. Les proxies restent
**toujours encodés en CPU** : l'accélération matérielle ne concerne pour
l'instant que l'export (le champ ``hardware`` du profil est conservé et
sérialisé pour un futur chantier, mais ne change pas la commande).

Ajouter un profil
-----------------

::

    from core.proxy_profiles import ProxyProfile, register_profile
    register_profile(ProxyProfile(id="ultra_light", name_key="proxy.profile.ultra_light",
                                  max_height=270, quality=34, audio_bitrate="48k"))

puis ajouter ``proxy.profile.ultra_light`` (fr/en/es) dans ``ui/i18n.py``.
Un profil modifié change d'**empreinte** : les proxies déjà générés avec
l'ancien réglage sont reconnus comme obsolètes et régénérables.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .video_encoders import HardwareEncoder, coerce_hardware, resolve_video_encoder

DEFAULT_PROFILE_ID = "medium"


@dataclass(frozen=True)
class ProxyCodec:
    """Un codec de proxy : famille d'encodeur, conteneur et arguments."""

    id: str
    video_family: str  # clé de :mod:`core.video_encoders` ("h264", "prores_ks")
    container: str  # "mp4" ou "mov"
    pixel_format: str
    build_video_args: Callable[["ProxyProfile"], list[str]]


def _h264_args(profile: ProxyProfile) -> list[str]:
    choice = resolve_video_encoder(
        "h264",
        speed_preset=profile.speed_preset,
        quality=profile.quality,
        hardware=HardwareEncoder.CPU,  # proxies : CPU uniquement (voir le module)
    )
    # GOP court : le déplacement dans le proxy (scrubbing) ne doit pas
    # décoder des dizaines d'images depuis la dernière image clé.
    return [*choice.args, "-g", str(profile.gop), "-keyint_min", str(profile.gop)]


def _prores_proxy_args(profile: ProxyProfile) -> list[str]:
    choice = resolve_video_encoder(
        "prores_ks", quality=profile.quality, hardware=HardwareEncoder.CPU
    )
    return list(choice.args)


PROXY_CODECS: dict[str, ProxyCodec] = {
    "h264": ProxyCodec("h264", "h264", "mp4", "yuv420p", _h264_args),
    # Profil 0 = ProRes Proxy : intra-image, très fluide au scrubbing.
    "prores_proxy": ProxyCodec("prores_proxy", "prores_ks", "mov", "yuv422p10le", _prores_proxy_args),
}


def register_codec(codec: ProxyCodec) -> None:
    """Ajoute (ou remplace) un codec de proxy."""
    PROXY_CODECS[codec.id] = codec


@dataclass(frozen=True)
class ProxyProfile:
    """Réglages d'un niveau de proxy.

    Attributes:
        id: identifiant stable (nom de profil, stocké avec le proxy).
        name_key: clé i18n du nom affiché.
        max_height: hauteur maximale en pixels. **Jamais d'agrandissement** :
            un média plus petit garde sa taille.
        codec: clé de :data:`PROXY_CODECS`.
        quality: CRF (H.264) ou profil (ProRes Proxy = 0).
        speed_preset: préréglage de vitesse x264 (ignoré par ProRes).
        gop: intervalle d'images clés (H.264).
        audio_codec: ``"aac"`` ou ``None`` (proxy muet : l'aperçu garde alors
            l'audio du média original).
        audio_bitrate: débit audio FFmpeg.
        hardware: famille d'encodeur demandée (:class:`HardwareEncoder`).
    """

    id: str
    name_key: str
    max_height: int
    codec: str = "h264"
    quality: int = 28
    speed_preset: str = "veryfast"
    gop: int = 12
    audio_codec: str | None = "aac"
    audio_bitrate: str = "96k"
    hardware: str = HardwareEncoder.CPU.value

    def __post_init__(self) -> None:
        if self.codec not in PROXY_CODECS:
            raise ValueError(f"Codec de proxy inconnu : {self.codec!r}")
        if self.max_height < 2:
            raise ValueError("La hauteur d'un proxy doit être d'au moins 2 pixels.")
        if self.audio_codec not in (None, "aac"):
            raise ValueError(f"Codec audio de proxy non pris en charge : {self.audio_codec!r}")
        object.__setattr__(self, "hardware", coerce_hardware(self.hardware).value)

    @property
    def extension(self) -> str:
        return PROXY_CODECS[self.codec].container

    @property
    def keeps_audio(self) -> bool:
        return self.audio_codec is not None

    def fingerprint(self) -> str:
        """Empreinte des réglages **qui changent le fichier produit**.

        Exclut le nom affiché : renommer un profil ne rend pas ses proxies
        obsolètes ; changer sa résolution ou sa qualité, si.
        """
        payload = {
            "max_height": self.max_height,
            "codec": self.codec,
            "quality": self.quality,
            "speed_preset": self.speed_preset,
            "gop": self.gop,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "hardware": self.hardware,
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def ffmpeg_output_args(self) -> list[str]:
        """Arguments FFmpeg placés entre ``-i <source>`` et le fichier de sortie."""
        codec = PROXY_CODECS[self.codec]
        args = [
            "-map", "0:v:0",
            "-vf", f"scale=-2:'min(ih,{self.max_height})'",
            "-pix_fmt", codec.pixel_format,
            *codec.build_video_args(self),
        ]
        if self.audio_codec:
            args += ["-map", "0:a:0?", "-c:a", self.audio_codec, "-ac", "2",
                     "-ar", "48000", "-b:a", self.audio_bitrate]
        else:
            args += ["-an"]
        if codec.container == "mp4":
            args += ["-movflags", "+faststart"]
        return args


_PROFILES: dict[str, ProxyProfile] = {
    "low": ProxyProfile("low", "proxy.profile.low", 480, quality=30, audio_bitrate="64k"),
    "medium": ProxyProfile("medium", "proxy.profile.medium", 720, quality=26, audio_bitrate="96k"),
    "high": ProxyProfile("high", "proxy.profile.high", 1080, quality=22, audio_bitrate="128k"),
}


_BUILTIN_IDS = ("low", "medium", "high")


def builtin_profiles() -> tuple[ProxyProfile, ...]:
    """Profils **fournis** (Faible, Moyen, Élevé), du plus léger au plus fidèle."""
    return tuple(
        sorted((_PROFILES[i] for i in _BUILTIN_IDS), key=lambda profile: profile.max_height)
    )


def available_profiles() -> tuple[ProxyProfile, ...]:
    """Profils fournis **et** ajoutés par :func:`register_profile`, du plus léger au plus lourd."""
    return tuple(sorted(_PROFILES.values(), key=lambda profile: profile.max_height))


def get_profile(profile_id: str | None) -> ProxyProfile:
    """Profil par identifiant ; repli sur le profil par défaut si inconnu."""
    return _PROFILES.get(str(profile_id), _PROFILES[DEFAULT_PROFILE_ID])


def register_profile(profile: ProxyProfile) -> None:
    """Ajoute (ou remplace) un profil. Voir le module pour la marche à suivre."""
    _PROFILES[profile.id] = profile


__all__ = [
    "DEFAULT_PROFILE_ID",
    "PROXY_CODECS",
    "ProxyCodec",
    "ProxyProfile",
    "available_profiles",
    "builtin_profiles",
    "get_profile",
    "register_codec",
    "register_profile",
]
