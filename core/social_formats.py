"""Formats des réseaux sociaux et création d'un projet vertical prêt à monter.

Un format est un **cadre de séquence** (largeur × hauteur) : tout le reste du montage s'y rapporte (positions en fraction
du cadre, zones de sécurité, cadrage « remplir »). La cadence est choisie à part (30 ou 60 i/s).

Les identifiants sont stables (ils servent de clés de traduction côté interface : ``social.format.<id>``) ; aucun texte
affiché ne vit ici.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .project_model import Project, Track


@dataclass(frozen=True)
class SocialFormat:
    """Cadre d'un format social.

    Attributes:
        id: identifiant stable (``vertical``, ``portrait``, ``square``, ``landscape``).
        width / height: taille du cadre en pixels.
        ratio: rapport affiché (``9:16``…).
        platforms: plateformes dont ce format est le cadre natif (zones de sécurité proposées).
    """

    id: str
    width: int
    height: int
    ratio: str
    platforms: tuple[str, ...] = ()


SOCIAL_FORMATS: tuple[SocialFormat, ...] = (
    SocialFormat("vertical", 1080, 1920, "9:16", ("tiktok", "reels", "shorts")),
    SocialFormat("portrait", 1080, 1350, "4:5", ("instagram_feed",)),
    SocialFormat("square", 1080, 1080, "1:1", ("instagram_feed",)),
    SocialFormat("landscape", 1920, 1080, "16:9", ()),
)

SOCIAL_FRAME_RATES: tuple[int, ...] = (30, 60)

DEFAULT_FORMAT_ID = "vertical"

TRACK_ROLES: tuple[tuple[str, str, str], ...] = (
    # (identifiant de piste, type, rôle audio)
    ("V1", "video", "other"),
    ("G1", "graphics", "other"),
    ("A1", "audio", "music"),
    ("A2", "audio", "voice"),
    ("A3", "audio", "sfx"),
)
"""Pistes d'un projet social : images, titres, musique, voix, effets sonores. Le rôle audio sert au ducking et au
placement des SFX (la musique baisse sous la voix ; les SFX vont sur la piste ``sfx``)."""


def social_format(format_id: str) -> SocialFormat:
    """Format par identifiant ; ``KeyError`` s'il n'existe pas."""
    for entry in SOCIAL_FORMATS:
        if entry.id == format_id:
            return entry
    raise KeyError(f"Format social inconnu : {format_id!r}.")


def format_for_frame(width: int, height: int) -> SocialFormat | None:
    """Format social dont le cadre a exactement ce rapport (``None`` si aucun)."""
    for entry in SOCIAL_FORMATS:
        if entry.width * int(height) == entry.height * int(width):
            return entry
    return None


def create_social_project(
    format_id: str = DEFAULT_FORMAT_ID,
    fps: float = 30,
    *,
    name: str = "",
    track_names: Mapping[str, str] | None = None,
) -> Project:
    """Projet vide au cadre du format, avec les pistes d'un montage social.

    ``track_names`` : noms affichés des pistes par identifiant (l'interface les traduit) ; par défaut, l'identifiant.
    Les identifiants (``V1``, ``G1``, ``A1``…) restent ceux de toute la timeline.
    """
    entry = social_format(format_id)
    if float(fps) not in {float(rate) for rate in SOCIAL_FRAME_RATES}:
        raise ValueError(f"Cadence non proposée pour un format social : {fps}.")
    names = dict(track_names or {})
    tracks = [
        Track(id=track_id, name=names.get(track_id, track_id), type=track_type, audio_role=role)
        for track_id, track_type, role in TRACK_ROLES
    ]
    return Project(name=name or entry.ratio, width=entry.width, height=entry.height, fps=float(fps), tracks=tracks)


__all__ = [
    "DEFAULT_FORMAT_ID",
    "SOCIAL_FORMATS",
    "SOCIAL_FRAME_RATES",
    "SocialFormat",
    "TRACK_ROLES",
    "create_social_project",
    "format_for_frame",
    "social_format",
]
