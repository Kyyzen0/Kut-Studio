"""Registre des propriétés animables et accès à leurs données dans un clip.

Le moteur (:mod:`core.animation`) ne sait rien des clips. Ce module relie
chaque propriété animable à l'endroit où vivent sa valeur statique et ses
keyframes. Les opérations d'édition (:mod:`core.keyframe_editing`),
l'inspecteur, la timeline et le Graph Editor passent **uniquement** par ce
registre : rendre une nouvelle propriété animable revient à y ajouter une
:class:`PropertyTarget`.

Rendre une propriété animable
-----------------------------

1. décrire la propriété : :class:`~core.animation.AnimatableProperty`
   (identifiant stable, type de valeur, défaut, bornes) ;
2. écrire ses quatre accès (valeur statique, keyframes) et un constructeur
   de keyframe (une sous-classe de :class:`~core.animation.Keyframe` si la
   valeur doit être validée) ;
3. ``register_target(PropertyTarget(...))`` ;
4. au rendu, évaluer avec ``spec.evaluate(curve, static, t)`` et, pour
   FFmpeg, :func:`core.animation_ffmpeg.curve_expression`.

L'édition, l'undo, la sérialisation (si les keyframes sont stockés dans un
champ déjà sérialisé), le copier/coller et le Graph Editor suivent sans
autre changement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .animation import AnimatableProperty, AnimationCurve, Keyframe
from .visual_effects import ANIMATABLE_PROPERTIES, TRANSFORM_PROPERTIES, TransformKeyframe


@dataclass(frozen=True)
class PropertyTarget:
    """Une propriété animable et la façon de lire / écrire ses données.

    Attributes:
        spec: description de la propriété.
        get_static / set_static: valeur sans animation.
        get_keyframes / set_keyframes: keyframes de **cette** propriété.
        make_keyframe: constructeur ``(property_name, time, value, ...)``.
        applies_to: la propriété existe-t-elle pour ce type de piste (``video``…) ?
    """

    spec: AnimatableProperty
    get_static: Callable[[Any], Any]
    set_static: Callable[[Any, Any], None]
    get_keyframes: Callable[[Any], list[Keyframe]]
    set_keyframes: Callable[[Any, list[Keyframe]], None]
    make_keyframe: Callable[..., Keyframe]
    applies_to: Callable[[str], bool]

    @property
    def id(self) -> str:
        return self.spec.id

    def curve(self, clip) -> AnimationCurve:
        return AnimationCurve(self.get_keyframes(clip), self.spec.kind)

    def value_at(self, clip, local_time: float) -> Any:
        """Valeur effective (statique ou animée, bornée) à ``local_time``."""
        return self.spec.evaluate(self.curve(clip), self.get_static(clip), local_time)


_REGISTRY: dict[str, PropertyTarget] = {}


def register_target(target: PropertyTarget) -> None:
    """Ajoute (ou remplace) une propriété animable."""
    _REGISTRY[target.id] = target


def get_target(property_id: str) -> PropertyTarget:
    try:
        return _REGISTRY[property_id]
    except KeyError:
        raise KeyError(f"Propriété animable inconnue : {property_id!r}.") from None


def all_targets() -> tuple[PropertyTarget, ...]:
    return tuple(_REGISTRY.values())


def targets_for(track_type: str) -> tuple[PropertyTarget, ...]:
    """Propriétés animables d'un clip de piste ``track_type``, dans l'ordre d'enregistrement."""
    return tuple(target for target in _REGISTRY.values() if target.applies_to(track_type))


# ---------------------------------------------------------------------------
# Transform du clip (position, échelle, rotation, opacité)
# ---------------------------------------------------------------------------


def _has_transform(track_type: str) -> bool:
    return track_type in ("video", "graphics")


def _transform_target(name: str) -> PropertyTarget:
    def get_static(clip) -> float:
        return float(getattr(clip.transform, name))

    def set_static(clip, value) -> None:
        clip.transform = clip.transform.with_property(name, TRANSFORM_PROPERTIES[name].clamp(value))

    def get_keyframes(clip) -> list[Keyframe]:
        return [kf for kf in clip.transform_keyframes if kf.property_name == name]

    def set_keyframes(clip, keyframes: list[Keyframe]) -> None:
        others = [kf for kf in clip.transform_keyframes if kf.property_name != name]
        merged = others + list(keyframes)
        merged.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
        clip.transform_keyframes = merged

    return PropertyTarget(
        spec=TRANSFORM_PROPERTIES[name],
        get_static=get_static,
        set_static=set_static,
        get_keyframes=get_keyframes,
        set_keyframes=set_keyframes,
        make_keyframe=TransformKeyframe,
        applies_to=_has_transform,
    )


for _name in ANIMATABLE_PROPERTIES:
    register_target(_transform_target(_name))


__all__ = [
    "PropertyTarget",
    "all_targets",
    "get_target",
    "register_target",
    "targets_for",
]
