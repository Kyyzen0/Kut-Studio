"""Paramètres d'effet animés rendus **par expression FFmpeg** (ADR-0008, étape 3).

Deux filtres de l'export acceptent une expression du temps pour leurs options et la réévaluent à chaque image :

- ``vignette`` (``eval=frame``) : ``angle``, qui vaut ``intensité × π/4`` ;
- ``geq`` : la brume de chaleur (``amplitude``, ``frequency``, ``top``, ``span``). Rendu écrit et vérifié, mais **pas
  enregistré** : voir :data:`core.effect_targets.PENDING_EFFECT_PARAMS` (coût de ``geq`` par pixel). ``speed`` n'y figure pas
  (saut de phase).

La correction colorimétrique (``brightness``, ``contrast``, ``saturation``) ne passe plus ici : son filtre ``eq`` (``eval=frame``) est GPL et
ne peut pas figurer dans le FFmpeg LGPL de la version App Store (ADR-0013). Elle est rendue par ``lut`` et ``sendcmd``
(:mod:`core.eq_lut`) ; le registre (:data:`core.effect_targets.EXPRESSION_EFFECT_PARAMS`) la garde dans ce groupe, qui décide aussi
de la version du fichier ``.kut`` : la changer de groupe obligerait à relever le format.

Seuls les paramètres de :data:`core.effect_targets.ANIMATABLE_EFFECT_PARAMS` produisent une expression, hors ceux de
:data:`core.effect_targets.SENDCMD_EFFECT_PARAMS` (flou, aberration chromatique, rayon de la lueur : fichier de commandes,
:mod:`core.effect_sendcmd`) : le registre décide.

**Base de temps.** Les images clés d'un effet sont en temps local du clip. L'export remet ``PTS`` à zéro au début du clip
(``setpts=PTS-STARTPTS``, plus le temps déjà écoulé à l'origine d'un segment d'aperçu) *avant* les effets ; le décalage de
la timeline (``PTS+timeline_start``) n'est posé qu'après. ``t`` (``vignette``) et ``T`` (``geq``) sont donc le
temps local du clip, comme ``t`` pour l'échelle et la rotation animées.

Aucun fichier d'effet sans image clé ne passe par ici : :func:`animated_effect_expressions` renvoie alors ``{}`` et les
constructeurs de ``core.export_engine`` gardent leur chaîne d'origine, caractère pour caractère.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .animation import AnimationCurve, ValueKind
from .animation_ffmpeg import curve_expression
from .effects_model import ClipEffect, EffectType, fx_property_id

EQ_TIME = "t"
"""Variable de temps de ``vignette`` (``eval=frame``)."""
GEQ_TIME = "T"
"""Variable de temps de ``geq``."""
VIGNETTE_ANGLE_PER_INTENSITY = 0.7853981633974483
"""Radians d'angle de la vignette par unité d'intensité (π/4) : même facteur que l'export statique et le moniteur."""


def animated_effect_expressions(
    effect: ClipEffect, animation: Iterable[Any] | None, time_var: str,
) -> dict[str, str]:
    """``{paramètre: expression}`` des paramètres **rendus** de ``effect`` qui ont des images clés.

    L'expression est bornée par ``clip()`` aux bornes du paramètre (:data:`core.effects_model.EFFECT_PARAMETER_SPECS`),
    comme :meth:`core.animation.AnimatableProperty.evaluate` côté Python. ``{}`` sans image clé : le filtre statique est
    alors construit comme avant.
    """
    from .effect_targets import ANIMATABLE_EFFECT_PARAMS, SENDCMD_EFFECT_PARAMS, _fx_spec

    if not animation or effect.type is EffectType.COLOR_CORRECTION:      # lut + sendcmd (core.eq_lut), jamais une expression
        return {}
    keyframes = list(animation)
    result: dict[str, str] = {}
    by_command = SENDCMD_EFFECT_PARAMS.get(effect.type, ())      # rendus par ``sendcmd`` (core.effect_sendcmd), pas ici
    for name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
        if name in by_command:
            continue
        property_id = fx_property_id(effect.id, name)
        frames = [kf for kf in keyframes if kf.property_name == property_id]
        if not frames or name not in effect.params:
            continue
        spec = _fx_spec(effect, name, property_id)
        if spec is None:  # pragma: no cover - ANIMATABLE_EFFECT_PARAMS est animable par construction
            continue
        curve = AnimationCurve(frames, ValueKind.FLOAT)
        result[name] = curve_expression(curve, time_var=time_var, minimum=spec.minimum, maximum=spec.maximum)
    return result


def vignette_filter(params: Mapping[str, float], expressions: Mapping[str, str]) -> str:
    """``vignette`` dont l'intensité est animée : l'angle est l'expression de la courbe × π/4 (borné par FFmpeg à
    ``[0, π/2]``, l'intensité ne dépasse pas 1)."""
    del params  # l'intensité n'a pas de constante ici : elle est animée
    return f"vignette=angle='({expressions['intensity']})*{VIGNETTE_ANGLE_PER_INTENSITY}':eval=frame"


def heat_haze_shift(
    params: Mapping[str, float], expressions: Mapping[str, str], pixel_scale: float, fmt: Callable[[float], str],
) -> str:
    """Décalage ``floor(A·sin(Y·f + T·v)·rampe)`` d'une brume dont ``amplitude``, ``frequency``, ``top`` ou ``span`` est
    animé : même formule que :func:`core.export_engine._heat_haze_filter`, la courbe remplaçant la constante (ramenée à la
    résolution de rendu comme elle)."""
    if "amplitude" in expressions:
        amplitude = f"({expressions['amplitude']})*{fmt(pixel_scale)}"
    else:
        amplitude = fmt(float(params["amplitude"]) * pixel_scale)
    if "frequency" in expressions:
        frequency = f"({expressions['frequency']})/{fmt(max(1e-6, pixel_scale))}"
    else:
        frequency = fmt(float(params["frequency"]) / max(1e-6, pixel_scale))
    top = f"({expressions['top']})" if "top" in expressions else fmt(params["top"])
    span = f"({expressions['span']})" if "span" in expressions else fmt(params["span"])
    ramp = f"clip((Y/H-{top})/{span},0,1)"
    return f"floor({amplitude}*sin(Y*{frequency}+T*{fmt(params['speed'])})*{ramp})"


__all__ = [
    "EQ_TIME",
    "GEQ_TIME",
    "animated_effect_expressions",
    "heat_haze_shift",
    "vignette_filter",
]
