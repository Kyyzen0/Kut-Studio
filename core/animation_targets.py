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

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from .animation import AnimatableProperty, AnimationCurve, Keyframe, ValueKind, normalize_time
from .visual_effects import (
    TRANSFORM_PROPERTIES,
    TRANSFORM_PROPERTY_NAMES,
    TransformKeyframe,
)


@dataclass(frozen=True)
class PropertyTarget:
    """Une propriété animable et la façon de lire / écrire ses données.

    Attributes:
        spec: description de la propriété.
        get_static / set_static: valeur sans animation.
        get_keyframes / set_keyframes: keyframes de **cette** propriété.
        make_keyframe: constructeur ``(property_name, time, value, ...)``.
        applies_to: la propriété existe-t-elle pour ce type de piste (``video``…) ?
        applies_to_clip: filtre optionnel sur le clip lui-même (un
            interlignage n'existe que pour un texte, un rayon d'angle que
            pour une forme). ``None`` = tous les clips de la piste.
    """

    spec: AnimatableProperty
    get_static: Callable[[Any], Any]
    set_static: Callable[[Any, Any], None]
    get_keyframes: Callable[[Any], list[Keyframe]]
    set_keyframes: Callable[[Any, list[Keyframe]], None]
    make_keyframe: Callable[..., Keyframe]
    applies_to: Callable[[str], bool]
    applies_to_clip: Callable[[Any], bool] | None = None

    @property
    def id(self) -> str:
        return self.spec.id

    def curve(self, clip) -> AnimationCurve:
        return AnimationCurve(self.get_keyframes(clip), self.spec.kind)

    def value_at(self, clip, local_time: float) -> Any:
        """Valeur effective (statique ou animée, bornée) à ``local_time``."""
        return self.spec.evaluate(self.curve(clip), self.get_static(clip), local_time)


_REGISTRY: dict[str, PropertyTarget] = {}

DynamicResolver = Callable[[str], "PropertyTarget | None"]
"""Construit la cible d'un identifiant dynamique (``mask.<id>.feather``…)."""

_DYNAMIC: dict[str, tuple[DynamicResolver, Callable[[Any], Iterable[str]]]] = {}


def register_target(target: PropertyTarget) -> None:
    """Ajoute (ou remplace) une propriété animable."""
    _REGISTRY[target.id] = target


def register_dynamic_targets(
    prefix: str,
    resolver: DynamicResolver,
    ids_for_clip: Callable[[Any], Iterable[str]],
) -> None:
    """Famille de propriétés dont l'identifiant dépend du clip.

    Un masque porte un identifiant stable : ``mask.<id>.feather`` est une
    propriété distincte pour chaque masque. ``resolver`` construit la cible
    d'un identifiant ``prefix…`` ; ``ids_for_clip`` liste celles d'un clip.
    """
    _DYNAMIC[prefix] = (resolver, ids_for_clip)


def get_target(property_id: str) -> PropertyTarget:
    target = _REGISTRY.get(property_id)
    if target is not None:
        return target
    for prefix, (resolver, _ids) in _DYNAMIC.items():
        if property_id.startswith(prefix):
            target = resolver(property_id)
            if target is not None:
                return target
    raise KeyError(f"Propriété animable inconnue : {property_id!r}.")


def all_targets() -> tuple[PropertyTarget, ...]:
    return tuple(_REGISTRY.values())


def targets_for(track_type: str) -> tuple[PropertyTarget, ...]:
    """Propriétés animables d'un clip de piste ``track_type``, dans l'ordre d'enregistrement."""
    return tuple(target for target in _REGISTRY.values() if target.applies_to(track_type))


def targets_for_clip(clip, track_type: str) -> tuple[PropertyTarget, ...]:
    """Propriétés animables de **ce** clip : statiques applicables + dynamiques (masques…)."""
    result = [
        target for target in targets_for(track_type)
        if target.applies_to_clip is None or target.applies_to_clip(clip)
    ]
    for _prefix, (resolver, ids_for_clip) in _DYNAMIC.items():
        for property_id in ids_for_clip(clip):
            target = resolver(property_id)
            if target is not None and target.applies_to(track_type):
                result.append(target)
    return tuple(result)


# ---------------------------------------------------------------------------
# Animation générique d'un clip (``Clip.animation``)
# ---------------------------------------------------------------------------


def generic_target(
    spec: AnimatableProperty,
    *,
    get_static: Callable[[Any], Any],
    set_static: Callable[[Any, Any], None],
    applies_to: Callable[[str], bool],
    applies_to_clip: Callable[[Any], bool] | None = None,
) -> PropertyTarget:
    """Cible dont les keyframes vivent dans ``clip.animation`` (déjà sérialisé).

    Toute propriété hors transform (forme, texte, masque…) passe par ici :
    il suffit de décrire la valeur statique ; keyframes, undo, copier /
    coller, Graph Editor et sérialisation suivent.
    """
    name = spec.id

    def get_keyframes(clip) -> list[Keyframe]:
        return [kf for kf in getattr(clip, "animation", ()) if kf.property_name == name]

    def set_keyframes(clip, keyframes: list[Keyframe]) -> None:
        others = [kf for kf in getattr(clip, "animation", ()) if kf.property_name != name]
        merged = others + [
            kf if kf.property_name == name else replace(kf, property_name=name, id=kf.id)
            for kf in keyframes
        ]
        merged.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
        clip.animation = merged

    def make_keyframe(property_name, time_seconds, value, *args, **kwargs) -> Keyframe:
        return Keyframe(property_name, time_seconds, spec.clamp(value), *args, **kwargs)

    return PropertyTarget(
        spec=spec,
        get_static=get_static,
        set_static=set_static,
        get_keyframes=get_keyframes,
        set_keyframes=set_keyframes,
        make_keyframe=make_keyframe,
        applies_to=applies_to,
        applies_to_clip=applies_to_clip,
    )


def animation_curves(clip, *, prefix: str = "") -> dict[str, AnimationCurve]:
    """Courbes de ``clip.animation`` (filtrées par préfixe d'identifiant)."""
    grouped: dict[str, list[Keyframe]] = {}
    for kf in getattr(clip, "animation", ()):
        if prefix and not kf.property_name.startswith(prefix):
            continue
        grouped.setdefault(kf.property_name, []).append(kf)
    curves: dict[str, AnimationCurve] = {}
    for name, frames in grouped.items():
        try:
            kind = get_target(name).spec.kind
        except KeyError:
            kind = ValueKind.FLOAT
        curves[name] = AnimationCurve(frames, kind)
    return curves


def _clamp_for(property_id: str):
    try:
        return get_target(property_id).spec.clamp
    except KeyError:
        return None


def split_animation(keyframes: Iterable[Keyframe], cut_local_time: float) -> tuple[list[Keyframe], list[Keyframe]]:
    """Coupe des keyframes génériques en deux **sans changer l'animation**.

    Même règle que :func:`core.visual_effects.split_transform_keyframes` :
    chaque courbe est scindée exactement, la partie droite recalée à 0.
    """
    grouped: dict[str, list[Keyframe]] = {}
    for kf in keyframes:
        grouped.setdefault(kf.property_name, []).append(kf)
    left: list[Keyframe] = []
    right: list[Keyframe] = []
    for name, frames in grouped.items():
        try:
            kind = get_target(name).spec.kind
        except KeyError:
            kind = ValueKind.FLOAT
        curve = AnimationCurve(frames, kind)
        a, b = curve.split(cut_local_time, clamp=_clamp_for(name))
        left.extend(a.keyframes)
        right.extend(b.keyframes)
    left.sort(key=lambda k: (k.property_name, k.time_seconds))
    right.sort(key=lambda k: (k.property_name, k.time_seconds))
    return left, right


def retime_animation(
    keyframes: Iterable[Keyframe], *, start_offset: float, new_duration: float
) -> list[Keyframe]:
    """Keyframes génériques après un trim : l'animation visible ne bouge pas."""
    frames = list(keyframes)
    if start_offset > 0:
        frames = split_animation(frames, start_offset)[1]
    elif start_offset < 0:
        frames = [
            replace(k, time_seconds=normalize_time(k.time_seconds - start_offset), id=k.id) for k in frames
        ]
    if any(k.time_seconds > new_duration + 1e-6 for k in frames):
        frames = split_animation(frames, new_duration)[0]
    return frames


# ---------------------------------------------------------------------------
# Transform du clip (position, échelle, rotation, opacité)
# ---------------------------------------------------------------------------


def _has_transform(track_type: str) -> bool:
    return track_type in ("video", "graphics")


def _graphics_only(track_type: str) -> bool:
    return track_type == "graphics"


# L'inclinaison n'existe que dans le rastériseur motion graphics : les clips
# vidéo passent par ``scale``/``rotate`` de FFmpeg, qui ne savent pas cisailler.
_TRANSFORM_APPLIES: dict[str, Callable[[str], bool]] = {"skew": _graphics_only}


def _transform_target(name: str) -> PropertyTarget:
    spec = TRANSFORM_PROPERTIES[name]

    def get_static(clip):
        value = getattr(clip.transform, name)
        return bool(value) if spec.kind is ValueKind.BOOL else float(value)

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
        applies_to=_TRANSFORM_APPLIES.get(name, _has_transform),
    )


for _name in TRANSFORM_PROPERTY_NAMES:
    register_target(_transform_target(_name))


__all__ = [
    "PropertyTarget",
    "all_targets",
    "animation_curves",
    "generic_target",
    "get_target",
    "register_dynamic_targets",
    "register_target",
    "retime_animation",
    "split_animation",
    "targets_for",
    "targets_for_clip",
]


# Propriétés des calques motion graphics et des masques (s'enregistrent ici).
from . import mograph_targets as _mograph_targets  # noqa: E402,F401

# Vitesse du clip (remappage temporel : la courbe de vitesse est une propriété comme les autres).
from . import time_targets as _time_targets  # noqa: E402,F401
