"""Presets d'export centralisés.

Un preset est une **description** (conteneur, codecs, résolution,
fréquence d'images, qualité, encodeur) : il ne contient aucune logique
FFmpeg. :meth:`RenderPresetSpec.export_parts` le traduit en objets que
le moteur existant (:mod:`core.export_engine`) consomme tels quels.
C'est le seul endroit où l'on ajoute ou modifie un preset.

Les valeurs de qualité suivent le moteur : ``quality`` est un CRF pour
H.264 et le numéro de profil pour ProRes (3 = HQ). ``Custom`` n'est pas
un preset figé : :func:`custom_preset` valide des réglages libres.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .export_engine import ExportFormat, ExportPreset
from .video_encoders import HardwareEncoder, coerce_hardware

CUSTOM_PRESET_ID = "custom"
DEFAULT_PRESET_ID = "h264_1080p"

SUPPORTED_AUDIO_CODECS: tuple[str, ...] = ("aac",)
"""Codecs audio que le moteur sait écrire (PCM viendra avec ProRes)."""

_VIDEO_CODEC_ALIASES = {"h264": "h264", "libx264": "h264", "prores": "prores_ks", "prores_ks": "prores_ks"}


def export_format_for(container: str, video_codec: str) -> ExportFormat:
    """Retrouve le :class:`ExportFormat` d'un couple conteneur / codec.

    Raises:
        ValueError: combinaison non prise en charge par le moteur.
    """
    codec = _VIDEO_CODEC_ALIASES.get(str(video_codec).lower())
    for candidate in ExportFormat:
        if candidate.container == str(container).lower() and candidate.codec == codec:
            return candidate
    raise ValueError(
        f"Combinaison conteneur / codec non prise en charge : {container} / {video_codec}"
    )


@dataclass(frozen=True)
class RenderPresetSpec:
    """Description immuable d'un preset d'export.

    Attributes:
        id: identifiant stable (stocké dans les jobs).
        name: nom affichable par défaut (l'interface peut le traduire).
        container: ``"mp4"`` ou ``"mov"``.
        video_codec: ``"h264"`` ou ``"prores_ks"``.
        audio_codec: voir :data:`SUPPORTED_AUDIO_CODECS`.
        width / height: résolution de sortie.
        fps: images par seconde.
        quality: CRF (H.264) ou profil (ProRes).
        audio_bitrate: débit audio FFmpeg (``"192k"``).
        hardware: famille d'encodeur demandée.
        description: phrase d'aide.
    """

    id: str
    name: str
    container: str
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: int
    quality: int
    audio_bitrate: str = "192k"
    hardware: str = HardwareEncoder.CPU.value
    description: str = ""
    loudness_lufs: float | None = None
    """Loudness visée à l'export (−14 LUFS pour les réseaux), ``None`` : le mixage tel quel."""
    preview_copy: bool = False
    """Écrire aussi une copie d'aperçu légère (< 30 Mo, :mod:`core.social_deliverables`)."""
    cover: bool = False
    """Écrire aussi l'image de couverture (marqueur ``cover``, sinon la tête de lecture)."""

    def __post_init__(self) -> None:
        export_format_for(self.container, self.video_codec)  # valide la combinaison
        if self.audio_codec not in SUPPORTED_AUDIO_CODECS:
            raise ValueError(f"Codec audio non pris en charge : {self.audio_codec}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("La résolution d'export doit être positive.")
        if self.fps <= 0:
            raise ValueError("La fréquence d'images doit être supérieure à zéro.")
        object.__setattr__(self, "hardware", coerce_hardware(self.hardware).value)
        if self.loudness_lufs is not None and not -40.0 <= float(self.loudness_lufs) <= -5.0:
            raise ValueError(f"Loudness visée hors bornes : {self.loudness_lufs} LUFS.")

    @property
    def export_format(self) -> ExportFormat:
        return export_format_for(self.container, self.video_codec)

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    def export_parts(self) -> tuple[ExportFormat, ExportPreset, int]:
        """Configuration consommée par ``ExportRequest`` : format, preset, fps."""
        return (
            self.export_format,
            ExportPreset(
                name=self.name,
                resolution=self.resolution,
                crf=self.quality,
                audio_bitrate=self.audio_bitrate,
            ),
            self.fps,
        )

    def summary(self) -> str:
        """Résumé court, ex. ``MP4 · H.264 · 1920×1080 · 30 fps``."""
        codec = {"h264": "H.264", "prores_ks": "ProRes"}.get(self.video_codec, self.video_codec)
        return f"{self.container.upper()} · {codec} · {self.width}×{self.height} · {self.fps} fps"


def _h264(preset_id, name, width, height, fps, quality, audio="192k", description="", loudness=None):
    return RenderPresetSpec(
        preset_id, name, "mp4", "h264", "aac", width, height, fps, quality, audio,
        description=description, loudness_lufs=loudness,
    )


def _social(preset_id, name, width, height, fps, description):
    """Preset réseau social : H.264 qualité 20, son normalisé à −14 LUFS, copie légère et couverture."""
    return replace(_h264(preset_id, name, width, height, fps, 20, description=description, loudness=-14.0),
                   preview_copy=True, cover=True)


_BUILTIN: tuple[RenderPresetSpec, ...] = (
    _h264("h264_1080p", "H.264 1080p", 1920, 1080, 30, 20, description="Full HD, bon équilibre taille / qualité."),
    _h264("h264_1440p", "H.264 1440p", 2560, 1440, 30, 20, description="QHD pour les écrans haute résolution."),
    _h264("h264_4k", "H.264 4K", 3840, 2160, 30, 20, description="UHD ; rendu long et fichier volumineux."),
    _h264("youtube", "YouTube", 1920, 1080, 30, 18, description="MP4 H.264 1080p, qualité élevée, démarrage rapide."),
    _social("tiktok", "TikTok / Vertical", 1080, 1920, 30, "Vidéo verticale 1080×1920."),
    _social("tiktok_60", "TikTok 60 fps", 1080, 1920, 60, "Vidéo verticale 1080×1920 à 60 images/s."),
    _social("reels", "Instagram Reels", 1080, 1920, 30, "Reels 1080×1920."),
    _social("shorts", "YouTube Shorts", 1080, 1920, 60, "Shorts 1080×1920 à 60 images/s."),
    _social("instagram_feed_4_5", "Instagram 4:5", 1080, 1350, 30, "Fil Instagram en portrait 1080×1350."),
    _social("square", "Carré 1:1", 1080, 1080, 30, "Format carré 1080×1080."),
    RenderPresetSpec(
        "prores_master", "ProRes Master", "mov", "prores_ks", "aac",
        1920, 1080, 30, 3, "256k",
        description="Intermédiaire de qualité maximale (ProRes 422 HQ).",
    ),
)


def builtin_presets() -> tuple[RenderPresetSpec, ...]:
    """Presets fournis, dans l'ordre d'affichage (hors Custom)."""
    return _BUILTIN


def get_preset(preset_id: str) -> RenderPresetSpec | None:
    """Preset fourni par identifiant, ``None`` s'il n'existe pas (ou pour Custom)."""
    for spec in _BUILTIN:
        if spec.id == preset_id:
            return spec
    return None


def default_preset() -> RenderPresetSpec:
    spec = get_preset(DEFAULT_PRESET_ID)
    if spec is None:  # erreur de programmation : un ``assert`` disparaîtrait sous ``python -O``
        raise RuntimeError(f"DEFAULT_PRESET_ID absent de _BUILTIN : {DEFAULT_PRESET_ID!r}")
    return spec


def custom_preset(
    *,
    container: str = "mp4",
    video_codec: str = "h264",
    audio_codec: str = "aac",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    quality: int = 20,
    audio_bitrate: str = "192k",
    hardware: str = HardwareEncoder.CPU.value,
) -> RenderPresetSpec:
    """Construit un preset ``custom`` après validation (lève ``ValueError``)."""
    return RenderPresetSpec(
        CUSTOM_PRESET_ID, "Custom", container, video_codec, audio_codec,
        int(width), int(height), int(fps), int(quality), audio_bitrate, hardware,
        description="Réglages libres.",
    )


def with_hardware(spec: RenderPresetSpec, hardware: object) -> RenderPresetSpec:
    """Copie de ``spec`` avec une autre famille d'encodeur."""
    return replace(spec, hardware=coerce_hardware(hardware).value)


def with_deliverables(spec: RenderPresetSpec, *, preview_copy: bool, cover: bool) -> RenderPresetSpec:
    """Copie de ``spec`` qui écrit (ou non) la copie d'aperçu et la couverture."""
    return replace(spec, preview_copy=bool(preview_copy), cover=bool(cover))


def with_loudness(spec: RenderPresetSpec, lufs: float | None) -> RenderPresetSpec:
    """Copie de ``spec`` avec une autre loudness visée (``None`` : pas de normalisation)."""
    return replace(spec, loudness_lufs=None if lufs is None else float(lufs))


__all__ = [
    "CUSTOM_PRESET_ID",
    "DEFAULT_PRESET_ID",
    "SUPPORTED_AUDIO_CODECS",
    "RenderPresetSpec",
    "builtin_presets",
    "custom_preset",
    "default_preset",
    "export_format_for",
    "get_preset",
    "with_deliverables",
    "with_hardware",
    "with_loudness",
]
