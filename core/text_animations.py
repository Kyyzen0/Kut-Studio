"""Presets d'animation de texte « style TikTok », posés en images-clés.

Un preset n'est pas un nouveau moteur : il écrit des images-clés de transform (échelle, position, opacité) et de
``graphic.reveal`` que l'aperçu, le moniteur et l'export évaluent comme toute animation, et que l'utilisateur peut
retoucher ensuite (losanges, éditeur de courbes).

Le **dépassement** du pop-in est une courbe « back » exacte : sur un segment ``[t0, t1]``, la cubique d'Hermite de pente
``4,70158 × Δ / durée`` au départ et nulle à l'arrivée **est** l'easeOutBack classique (``c1 = 1,70158``) ; elle monte à
110 % de l'écart avant de se poser. Aucun nouveau type d'interpolation : le segment reste un polynôme exact dans FFmpeg.
"""

from __future__ import annotations

from dataclasses import replace

from .animation import InterpolationType, Keyframe, TangentMode
from .project_model import Clip
from .text_runs import letter_count, word_count
from .visual_effects import TransformKeyframe

BACK_SLOPE = 4.70158
"""Pente de départ (× Δ / durée) de l'easeOutBack : ``3·(c1 + 1) − 2·c1`` avec ``c1 = 1,70158``."""

TEXT_ANIMATIONS: tuple[str, ...] = (
    "pop_in", "slide_left", "slide_right", "slide_up", "slide_down", "fade_up", "bounce", "word_by_word",
    "typewriter", "karaoke",
)
"""Identifiants stables (clés de traduction ``text_animation.<id>`` côté interface)."""

REVEAL = "graphic.reveal"
_SLIDE = {"slide_left": ("position_x", 0.6), "slide_right": ("position_x", -0.6),
          "slide_up": ("position_y", 0.25), "slide_down": ("position_y", -0.25)}


def back_out(prop: str, t0: float, t1: float, v0: float, v1: float) -> list[TransformKeyframe]:
    """Deux images-clés : de ``v0`` à ``v1`` avec dépassement (easeOutBack exact)."""
    span = max(1e-3, t1 - t0)
    return [
        TransformKeyframe(prop, t0, v0, InterpolationType.BEZIER, out_slope=BACK_SLOPE * (v1 - v0) / span,
                          tangent_mode=TangentMode.BROKEN),
        TransformKeyframe(prop, t1, v1, InterpolationType.LINEAR, in_slope=0.0, tangent_mode=TangentMode.BROKEN),
    ]


def _fade_in(t0: float, length: float, opacity: float) -> list[TransformKeyframe]:
    return [TransformKeyframe("opacity", t0, 0.0, InterpolationType.LINEAR),
            TransformKeyframe("opacity", t0 + length, opacity, InterpolationType.LINEAR)]


def text_animation_keyframes(clip: Clip, preset: str, *, start: float = 0.0) -> tuple[list[TransformKeyframe], list[Keyframe], str]:
    """``(images-clés de transform, images-clés génériques, mode d'apparition)`` d'un preset sur ``clip``.

    ``start`` : instant de départ (temps local du clip). Les valeurs d'arrivée sont celles du transform statique du clip.
    """
    if preset not in TEXT_ANIMATIONS:
        raise ValueError(f"Animation de texte inconnue : {preset!r}.")
    base = clip.transform
    duration = float(clip.duration)
    if duration <= start:
        raise ValueError("Le calque est trop court pour cette animation.")
    t0 = float(start)
    graphic = getattr(clip, "graphic", None)
    text = getattr(graphic, "text", "") or ""
    transform: list[TransformKeyframe] = []
    generic: list[Keyframe] = []
    mode = "none"
    if preset == "pop_in":
        end = min(duration, t0 + 0.22)
        transform += back_out("scale", t0, end, base.scale * 0.3, base.scale)
        transform += _fade_in(t0, min(0.06, end - t0), base.opacity)
    elif preset in _SLIDE:
        prop, distance = _SLIDE[preset]
        end = min(duration, t0 + 0.28)
        target = getattr(base, prop)
        transform += [TransformKeyframe(prop, t0, target + distance, InterpolationType.EASE_OUT),
                      TransformKeyframe(prop, end, target, InterpolationType.LINEAR)]
        transform += _fade_in(t0, min(0.1, end - t0), base.opacity)
    elif preset == "fade_up":
        end = min(duration, t0 + 0.35)
        transform += [TransformKeyframe("position_y", t0, base.position_y + 0.04, InterpolationType.EASE_OUT),
                      TransformKeyframe("position_y", end, base.position_y, InterpolationType.LINEAR)]
        transform += _fade_in(t0, end - t0, base.opacity)
    elif preset == "bounce":
        # Rebond en boucle sur toute la durée : creux tous les 0,5 s (mesuré sur le CTA de l'edit F1).
        t, down = t0, False
        while t <= duration + 1e-9:
            value = base.position_y + (0.025 if down else 0.0)
            transform.append(TransformKeyframe("position_y", round(t, 6), value, InterpolationType.EASE_IN_OUT))
            t += 0.25
            down = not down
    else:
        mode = {"word_by_word": "word", "typewriter": "typewriter", "karaoke": "karaoke"}[preset]
        if preset == "word_by_word":
            length = min(duration - t0, max(0.15, 0.22 * max(1, word_count(text))))
        elif preset == "typewriter":
            length = min(duration - t0, max(0.15, 0.045 * max(1, letter_count(text))))
        else:
            length = duration - t0                             # karaoké : la phrase dure tout le calque
        generic = [Keyframe(REVEAL, t0, 0.0, InterpolationType.LINEAR), Keyframe(REVEAL, t0 + length, 1.0)]
    return transform, generic, mode


def apply_text_animation(clip: Clip, preset: str, *, start: float = 0.0) -> None:
    """Pose le preset : les images-clés des propriétés qu'il anime sont remplacées, les autres gardées."""
    transform, generic, mode = text_animation_keyframes(clip, preset, start=start)
    touched = {kf.property_name for kf in transform}
    kept = [kf for kf in clip.transform_keyframes if kf.property_name not in touched]
    clip.transform_keyframes = sorted([*kept, *transform], key=lambda kf: (kf.property_name, kf.time_seconds))
    graphic = getattr(clip, "graphic", None)
    if generic or (graphic is not None and mode != "none"):
        clip.animation = [kf for kf in clip.animation if kf.property_name != REVEAL] + generic
    if graphic is not None and mode != "none":
        clip.graphic = replace(graphic, word_reveal=mode, reveal=0.0)


__all__ = ["BACK_SLOPE", "TEXT_ANIMATIONS", "apply_text_animation", "back_out", "text_animation_keyframes"]
