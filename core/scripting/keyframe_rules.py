"""Règles communes des verbes d'images clés et de points de vitesse (lot 8 de l'extension 0.8.0).

Aucun verbe ici : des fonctions qui lisent le projet et refusent, sans importer la table complète des verbes.

- **une image clé se désigne par sa propriété et son instant** (``property_name`` et ``time_seconds`` de la description, comme ``add_keyframe`` /
  ``remove_keyframe``) : les identifiants d'image clé sont tirés au hasard par le modèle, un script rejoué ne les retrouverait pas. L'instant est
  comparé à la microseconde (:data:`core.animation.TIME_EPSILON`), la précision de la description ;
- **le registre d'animation décide** (:func:`core.animation_targets.get_target`, avec le clip pour les propriétés ``fx.*``) : bornes, genre de piste,
  clip concerné ; la liste des propriétés n'est jamais recopiée. Sont refusés ici : la vitesse (``time.speed`` : ses verbes ont la transaction du
  temps), les propriétés qui ne sont pas des nombres (miroirs), le recadrage et la position d'un masque *suivi* (dérivée du suivi au rendu) ;
- **rien n'est ramené en silence** : une valeur hors bornes, un déplacement qui sortirait du clip ou arriverait sur une autre image clé (le modèle la
  remplacerait), une pente que le rendu ignorerait, deux pentes liées différentes sont refusés.
"""

from __future__ import annotations

import math
from typing import Any

from ..animation import TIME_EPSILON, AnimationCurve, InterpolationType, Keyframe, TangentMode, ValueKind, normalize_time
from ..animation_targets import PropertyTarget, get_target
from ..compositing import Compositing
from ..project_model import Clip, Project
from ..timeline_operations import find_clip
from .verbs import _editable_clip, _owner_track, require_animatable_grade

MAX_BLOCK = 64
"""Images clés ou points de vitesse cités par une seule opération."""
MAX_SLOPE = 1e6
"""Pente Bézier maximale, en unités de la propriété par seconde : au-delà, ce n'est plus une tangente mais un saut."""

_RESERVED_MASKS = ("crop", "trackcrop")
_TRACKED = ("position_x", "position_y")


def resolve_keyframe_target(project: Project, selector: str, property_name: str, verb: str) -> tuple[str, Clip, PropertyTarget]:
    """``(identifiant du clip, clip, cible)`` pour une propriété animable d'un clip éditable (piste et calque déverrouillés). Les propriétés d'étalonnage ``color.*`` sont résolues comme les paramètres d'effet : avec le clip."""
    clip_id = _editable_clip(project, selector)
    clip = find_clip(project, clip_id)
    require_animatable_grade(clip, property_name, verb)      # ``color.*`` : seul le réglage simple du clip s'anime (ADR-0008, étape 9)
    if property_name.startswith("time."):
        raise ValueError(f"{verb} : la vitesse a ses propres verbes (add_speed_point, set_speed_point_value, set_speed_tangents…).")
    try:
        target = get_target(property_name, clip)
    except KeyError:
        raise ValueError(f"{verb} : le clip '{clip_id}' n'a pas de propriété animable {property_name!r}.") from None
    if target.spec.kind is not ValueKind.FLOAT:
        raise ValueError(f"{verb} : {property_name!r} n'est pas un nombre (un miroir ne s'anime pas par image clé).")
    owner = _owner_track(project, clip_id)
    if not target.applies_to(owner.type) or (target.applies_to_clip is not None and not target.applies_to_clip(clip)):
        raise ValueError(f"{verb} : la propriété {property_name!r} ne s'applique pas à ce clip (piste '{owner.type}').")
    if property_name.startswith("mask."):
        _, mask_id, name = property_name.split(".")
        if mask_id in _RESERVED_MASKS:
            raise ValueError(f"{verb} : le masque '{mask_id}' est le recadrage ou celui de la stabilisation (set_crop / clear_crop).")
        tracking = getattr(clip, "tracking", None)
        stack = clip.compositing
        if (
            name in _TRACKED and isinstance(stack, Compositing)
            and any(link.enabled and link.mask_id == mask_id for link in getattr(tracking, "links", ()) or ())
        ):
            raise ValueError(f"{verb} : {name} du masque '{mask_id}' est suivi (tracking) : sa position vient du suivi, une image clé serait écrasée.")
    return clip_id, clip, target


def keyframe_at(clip: Clip, target: PropertyTarget, at: float, property_name: str) -> Keyframe:
    """L'image clé de la propriété à l'instant ``at`` (à la microseconde), ou ``KeyError``."""
    found = target.curve(clip).keyframe_at(normalize_time(at))
    if found is None:
        raise KeyError(f"Aucune image clé de {property_name!r} à {at:g} s sur ce clip.")
    return found


def require_value(target: PropertyTarget, value: Any, property_name: str) -> float:
    """Le nombre, dans les bornes de la propriété : le modèle le ramènerait, un script le reçoit refusé."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{property_name} doit être un nombre fini.")
    spec = target.spec
    low = float("-inf") if spec.minimum is None else spec.minimum
    high = float("inf") if spec.maximum is None else spec.maximum
    if not low <= value <= high:
        raise ValueError(f"{property_name} = {value:g} est hors bornes (de {low:g} à {high:g}).")
    return float(value)


def require_within(clip: Clip, at: float, what: str) -> None:
    """``at`` tombe dans le clip (les images clés sont relatives au clip)."""
    duration = normalize_time(float(clip.duration))
    if not math.isfinite(at):                                                  # NaN passerait les deux comparaisons ci-dessous
        raise ValueError(f"{what} : l'instant doit être un nombre fini.")
    if at < -TIME_EPSILON or at > duration + TIME_EPSILON:
        raise ValueError(f"{what} : l'instant {at:g} s est hors du clip (de 0 à {duration:g} s), les images clés sont relatives au clip.")


def require_free_arrivals(curve: AnimationCurve, moving: set[str], arrivals: dict[str, float], what: str) -> None:
    """Aucune image clé déplacée n'arrive sur une autre qui reste (le modèle la remplacerait en silence)."""
    for other in curve.keyframes:
        if other.id in moving:
            continue
        for arrival in arrivals.values():
            if abs(other.time_seconds - arrival) <= 2 * TIME_EPSILON:
                raise ValueError(f"{what} : une image clé arriverait sur celle de {other.time_seconds:g} s, qu'elle remplacerait.")


def require_effective_tangents(
    curve: AnimationCurve, keyframe: Keyframe, *, in_slope: float | None, out_slope: float | None,
    mode: str | None, auto: bool, what: str,
) -> None:
    """Les contrôles de l'éditeur de courbes pour des pentes : ce que l'écran n'accepte pas, ou que le rendu ignorerait, est refusé.

    - au moins un réglage ; ``auto`` (pentes automatiques) exclut les pentes ;
    - une pente sortante ne compte que si l'image clé est « Bézier » et suivie d'une autre ; une pente entrante que si la précédente est « Bézier » ;
    - en mode **lié** les deux pentes sont égales (le modèle ne garderait que la sortante : deux valeurs différentes sont refusées) et il suffit
      qu'un des deux côtés compte, car la description d'une image clé liée porte les deux pentes.
    """
    if in_slope is None and out_slope is None and mode is None and not auto:
        raise ValueError(f"{what} : donnez au moins une pente, un mode ou auto.")
    if auto and (in_slope is not None or out_slope is not None):
        raise ValueError(f"{what} : auto rend les pentes automatiques, il exclut in_slope et out_slope.")
    index = curve.index_at(keyframe.time_seconds)
    frames = curve.keyframes
    if index is None:                                    # pragma: no cover - l'image clé vient d'être lue dans cette courbe
        raise KeyError("Image clé introuvable.")
    out_counts = index + 1 < len(frames) and keyframe.interpolation is InterpolationType.BEZIER
    in_counts = index > 0 and frames[index - 1].interpolation is InterpolationType.BEZIER
    resulting = TangentMode(mode) if mode is not None else keyframe.tangent_mode
    if resulting is TangentMode.LINKED:
        # Lié : le modèle écrit la même pente des deux côtés (celle de sortie si elle est donnée) ; la description d'une image clé liée porte donc
        # les deux, même quand un côté n'existe pas (première ou dernière image clé) : il suffit qu'un côté compte.
        if in_slope is not None and out_slope is not None and abs(in_slope - out_slope) > 1e-12:
            raise ValueError(f"{what} : en mode « linked » les deux pentes sont égales ; donnez mode « broken » pour des pentes différentes.")
        if (in_slope is not None or out_slope is not None) and not (in_counts or out_counts):
            raise ValueError(f"{what} : aucune pente ne compte ici : il faut une image clé « bezier » suivie d'une autre, ou précédée d'une « bezier » (le rendu l'ignorerait).")
        return
    if out_slope is not None and not out_counts:
        raise ValueError(f"{what} : out_slope ne compte que pour une image clé « bezier » suivie d'une autre (le rendu l'ignorerait).")
    if in_slope is not None and not in_counts:
        raise ValueError(f"{what} : in_slope ne compte que si l'image clé précédente est « bezier » (le rendu l'ignorerait).")


__all__ = [
    "MAX_BLOCK", "MAX_SLOPE", "keyframe_at", "require_effective_tangents", "require_free_arrivals", "require_value", "require_within",
    "require_animatable_grade",
    "resolve_keyframe_target",
]
