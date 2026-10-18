"""Propriétés animables des effets de clip : ``fx.<effet>.<paramètre>`` (ADR-0008, étape 2).

Importé par :mod:`core.animation_targets`. Cette étape **enregistre les cibles seulement** : ni le rendu (export, moniteur),
ni l'interface (Graph Editor, inspecteur, commandes d'animation) ne les utilisent encore (étapes 3 et 4 de l'ADR). Les images
clés vivent dans ``Clip.animation``, comme ``graphic.*`` et ``mask.*``.

Pourquoi la description dépend du clip. Le même nom de paramètre a des bornes différentes selon le **type** de l'effet
(``intensity`` : flou 0 à 50, vignette 0 à 1, aberration 0 à 40), et le type n'est pas dans l'identifiant
``fx.<effet>.<paramètre>`` : seul le clip le connaît. La cible se résout donc avec le clip
(:func:`core.animation_targets.get_target` ``(property_id, clip)``) ; sans clip, ``KeyError``. Bornes et défaut sont lus dans
:data:`core.effects_model.EFFECT_PARAMETER_SPECS`, jamais recopiés.

Découpage du nom **par la droite** (:func:`core.effects_model.split_fx_property`) : un identifiant d'effet peut contenir des
points.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .animation import AnimatableProperty, AnimationCurve, ValueKind
from .animation_targets import PropertyTarget, generic_target, register_clip_dynamic_targets
from .effects_model import (
    FX_PREFIX,
    ClipEffect,
    EffectType,
    effect_by_id,
    fx_property_id,
    parameter_specs,
    set_clip_effects,
    split_fx_property,
    update_effect_parameters,
)

ANIMATABLE_EFFECT_PARAMS: dict[EffectType, tuple[str, ...]] = {
    EffectType.COLOR_CORRECTION: ("brightness", "contrast", "saturation"),
    EffectType.BLUR: ("intensity",),
    EffectType.VIGNETTE: ("intensity",),
    EffectType.GLOW: ("radius",),
    EffectType.CHROMATIC_ABERRATION: ("intensity",),
    EffectType.HEAT_HAZE: ("amplitude", "frequency", "speed", "top", "span"),
}
"""Paramètres d'effet animables : liste **provisoire**, une seule source (ADR-0008, décision 1 et plan d'action).

Elle sera ajustée par l'étape 1 de l'ADR (validation de ``sendcmd`` filtre par filtre) : un paramètre dont le filtre ne peut
pas varier par image n'y figure pas. Volontairement **exclus** : ``sharpen.intensity`` (``unsharp`` ne s'anime pas),
``glow.threshold`` et ``glow.intensity`` (``lutrgb``, trop coûteux : voir « Export » dans l'ADR). Aucun nom de paramètre ne
contient de point (garde : ``tests/test_effect_targets.py``), ce qui rend le découpage par la droite non ambigu."""

EFFECT_PARAM_GROUP = "effect"


def _video_track(track_type: str) -> bool:
    return track_type == "video"


def _step_for(span: float) -> float:
    return max(round(span / 100.0, 4), 0.001)


def _fx_spec(effect: ClipEffect, name: str, property_id: str) -> AnimatableProperty | None:
    """Description de ``name`` pour ``effect`` (bornes et défaut lus dans les spécifications du **type** de l'effet),
    ``None`` si ce paramètre n'est pas animable."""
    if name not in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
        return None
    base = next((spec for spec in parameter_specs(effect.type) if spec.name == name), None)
    if base is None:  # pragma: no cover - ANIMATABLE_EFFECT_PARAMS est vérifiée contre EFFECT_PARAMETER_SPECS (test)
        return None
    return AnimatableProperty(
        property_id, f"effects.param.{name}", ValueKind.FLOAT, float(base.default),
        float(base.minimum), float(base.maximum), _step_for(base.maximum - base.minimum), group=EFFECT_PARAM_GROUP,
    )


def _fx_target(clip: Any, property_id: str) -> PropertyTarget | None:
    """Cible de ``fx.<effet>.<paramètre>`` pour **ce** clip, ``None`` si l'effet ou le paramètre n'est pas animable."""
    parts = split_fx_property(property_id)
    if parts is None:
        return None
    effect_id, name = parts
    effect = effect_by_id(getattr(clip, "effects", ()) or (), effect_id)
    spec = _fx_spec(effect, name, property_id) if effect is not None else None
    if spec is None:
        return None
    base_default = float(spec.default)

    def get_static(target_clip) -> float:
        current = effect_by_id(target_clip.effects, effect_id)
        return float(current.params[name]) if current is not None else base_default

    def set_static(target_clip, value) -> None:
        if effect_by_id(target_clip.effects, effect_id) is None:
            raise KeyError(f"Effet introuvable : {effect_id!r}.")
        set_clip_effects(
            target_clip, update_effect_parameters(target_clip.effects, effect_id, {name: spec.clamp(value)}),
        )

    def applies_to_clip(target_clip) -> bool:
        return effect_by_id(getattr(target_clip, "effects", ()) or (), effect_id) is not None

    return generic_target(
        spec, get_static=get_static, set_static=set_static,
        applies_to=_video_track, applies_to_clip=applies_to_clip,
    )


def _fx_ids(clip: Any) -> Iterator[str]:
    for effect in getattr(clip, "effects", ()) or ():
        for name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
            yield fx_property_id(effect.id, name)


register_clip_dynamic_targets(FX_PREFIX, _fx_target, _fx_ids)


def effective_effect_parameters(clip: Any, effect: ClipEffect, local_time: float) -> dict[str, float]:
    """Paramètres de ``effect`` à l'instant local ``local_time`` du clip : valeur de base, remplacée par la courbe
    ``fx.<effet>.<paramètre>`` quand elle a au moins une image clé (évaluée et bornée comme l'inspecteur).

    Fonction pure (sans Qt, sans effet de bord) : l'export, le moniteur et l'aperçu fidèle l'appelleront (ADR-0008,
    décision 2). Sans image clé, **exactement** ``effect.params``. Seuls les paramètres de :data:`ANIMATABLE_EFFECT_PARAMS`
    sont lus ; une image clé d'un autre paramètre est ignorée.
    """
    result = dict(effect.params)
    animation = getattr(clip, "animation", None)
    if not animation:
        return result
    for name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
        property_id = fx_property_id(effect.id, name)
        frames = [kf for kf in animation if kf.property_name == property_id]
        if not frames or name not in result:
            continue
        spec = _fx_spec(effect, name, property_id)
        if spec is None:  # pragma: no cover - le paramètre est animable, vérifié juste au-dessus
            continue
        result[name] = float(spec.evaluate(AnimationCurve(frames, ValueKind.FLOAT), result[name], local_time))
    return result


__all__ = [
    "ANIMATABLE_EFFECT_PARAMS",
    "effective_effect_parameters",
]
