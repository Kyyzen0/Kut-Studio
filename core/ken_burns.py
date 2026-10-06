"""Effet Ken Burns : un mouvement lent (zoom ou balayage) qui anime une photo, posé en images-clés de transform.

Le mouvement n'est qu'une courbe de ``scale`` et de ``position_x`` / ``position_y`` : l'aperçu, le moniteur GPU et
l'export le lisent comme toute animation (polynômes exacts), et l'utilisateur peut le retoucher ensuite. Le sens est tiré
de l'identifiant du clip, de façon **déterministe** : une suite de photos alterne les mouvements, et le même projet
donne toujours le même montage.

Le mouvement ne découvre jamais le bord : un balayage se fait à échelle agrandie et ne parcourt que l'excédent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .animation import InterpolationType
from .project_model import Clip
from .tracking_motion import cover_size
from .visual_effects import ClipTransform, TransformKeyframe

KEN_BURNS_MOVES: tuple[str, ...] = ("zoom_in", "pan_left", "zoom_out", "pan_right", "pan_up", "pan_down")
"""Mouvements, dans l'ordre où l'identifiant d'un clip les choisit."""

DEFAULT_STRENGTH = 0.12
"""Agrandissement maximal (12 %) : visible sans paraître forcé, l'usage courant des diaporamas sociaux."""

_ANIMATED = ("scale", "position_x", "position_y")


def photo_size(image_width: int, image_height: int, frame_width: int, frame_height: int, *, fill: bool) -> tuple[int, int]:
    """Taille d'un calque photo : elle **remplit** le cadre (``fill``, l'excédent sort du cadre) ou y tient entière sans
    jamais être agrandie au-delà de sa taille d'origine."""
    w, h = max(1, int(image_width)), max(1, int(image_height))
    if fill:
        return cover_size(w, h, frame_width, frame_height)
    ratio = min(1.0, frame_width / w, frame_height / h)
    return max(1, round(w * ratio)), max(1, round(h * ratio))


def move_for(clip_id: str) -> str:
    """Mouvement choisi pour un clip (stable d'une session à l'autre : pas de ``hash()`` salé)."""
    digest = hashlib.sha1(str(clip_id).encode("utf-8")).digest()
    return KEN_BURNS_MOVES[digest[0] % len(KEN_BURNS_MOVES)]


def ken_burns_keyframes(
    duration: float, move: str, *, strength: float = DEFAULT_STRENGTH, base: ClipTransform | None = None,
) -> list[TransformKeyframe]:
    """Images-clés (temps local du clip) d'un mouvement sur ``duration`` secondes.

    ``base`` : transform statique du clip (position de départ) ; le mouvement s'y ajoute.
    """
    if move not in KEN_BURNS_MOVES:
        raise ValueError(f"Mouvement Ken Burns inconnu : {move!r}.")
    duration = float(duration)
    if duration <= 0.0:
        raise ValueError("Le clip doit avoir une durée positive.")
    strength = max(0.01, min(0.5, float(strength)))
    base = base or ClipTransform()
    zoomed = base.scale * (1.0 + strength)
    # Excédent d'un calque agrandi de ``strength`` : il peut glisser de ±strength/2 cadre sans montrer le bord ; on
    # n'en parcourt que 80 % (le rendu arrondit au pixel).
    travel = 0.4 * strength
    start: dict[str, float] = {"scale": zoomed, "position_x": base.position_x, "position_y": base.position_y}
    end = dict(start)
    if move == "zoom_in":
        start["scale"] = base.scale
    elif move == "zoom_out":
        end["scale"] = base.scale
    elif move in ("pan_left", "pan_right"):
        sign = -1.0 if move == "pan_left" else 1.0
        start["position_x"], end["position_x"] = base.position_x - sign * travel, base.position_x + sign * travel
    else:
        sign = -1.0 if move == "pan_up" else 1.0
        start["position_y"], end["position_y"] = base.position_y - sign * travel, base.position_y + sign * travel
    frames = []
    for name in _ANIMATED:
        if abs(start[name] - end[name]) < 1e-9 and abs(start[name] - getattr(base, name)) < 1e-9:
            continue
        frames.append(TransformKeyframe(name, 0.0, start[name], InterpolationType.LINEAR))
        frames.append(TransformKeyframe(name, duration, end[name], InterpolationType.LINEAR))
    return frames


def apply_ken_burns(clips: Iterable[Clip], *, strength: float = DEFAULT_STRENGTH) -> int:
    """Pose un Ken Burns sur chaque clip (vidéo ou calque image) ; retourne le nombre de clips animés.

    L'animation précédente d'échelle et de position est remplacée ; les autres propriétés animées sont gardées.
    """
    count = 0
    for clip in clips:
        graphic = getattr(clip, "graphic", None)
        if graphic is not None and str(getattr(graphic.type, "value", graphic.type)) != "image":
            continue
        if clip.duration <= 0.0:
            continue
        kept = [kf for kf in clip.transform_keyframes if kf.property_name not in _ANIMATED]
        frames = ken_burns_keyframes(clip.duration, move_for(clip.id), strength=strength, base=clip.transform)
        clip.transform_keyframes = sorted([*kept, *frames], key=lambda kf: (kf.property_name, kf.time_seconds))
        count += 1
    return count


__all__ = [
    "DEFAULT_STRENGTH", "KEN_BURNS_MOVES", "apply_ken_burns", "ken_burns_keyframes", "move_for", "photo_size",
]
