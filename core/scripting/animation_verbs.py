"""Images clés des masques de compositing d'un clip : ``add_mask_keyframe``, ``remove_mask_keyframe`` (ADR-0005, étape 4).

Même chemin que l'interface : :func:`core.keyframe_editing.add_keyframe` / :func:`~core.keyframe_editing.remove_keyframe_at`
sur la propriété ``mask.<id>.<nom>`` (:func:`core.compositing.mask_property_id`). Les bornes sont celles du registre
d'animation (:func:`core.animation_targets.get_target`, donc ``MASK_PROPERTY_SPECS``), relues ici : une valeur hors bornes est
**refusée**, jamais ramenée. Le verrou de piste et de calque, et le genre de piste (vidéo ou graphiques), sont ceux des autres verbes.

Deux refus propres à ce domaine :

- une **fenêtre d'étalonnage** (aussi un ``Mask``, mais dans le graphe d'étalonnage) n'est pas animable par l'interface : le registre
  (:func:`core.animation_targets.targets_for_clip`) ne propose ``mask.<id>.*`` que pour les masques de ``Clip.compositing`` ;
- ``position_x`` / ``position_y`` d'un masque **suivi** (liaison de tracking) sont dérivés du suivi au rendu
  (:mod:`core.tracking_bindings`) : une image clé posée à la main serait écrasée.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..animation import InterpolationType
from ..animation_targets import get_target
from ..color_windows import window_ids
from ..compositing import MASK_PROPERTY_ORDER, Compositing, mask_property_id
from ..keyframe_editing import add_keyframe, clip_duration, remove_keyframe_at
from ..project_model import Project
from ..timeline_operations import find_clip
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _editable_clip, _pick

_RESERVED = ("crop", "trackcrop")
_TRACKS = ("video", "graphics")
_TRACKED = ("position_x", "position_y")

_MASK = Param("mask", Kind.SELECTOR, "Le masque du clip, par identifiant (pas le recadrage « crop », pas une fenêtre d'étalonnage).", of="mask")
_PROPERTY = Param("property", Kind.ENUM, "La propriété du masque animée.", choices=MASK_PROPERTY_ORDER)
_AT = Param("at", Kind.NUMBER, "Instant, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)


def _mask_property(project: Project, a: Mapping[str, Any], verb: str) -> tuple[str, Any, str]:
    """``(clip_id, clip, identifiant de la propriété animée)`` pour un masque du clip éditable (vidéo ou graphiques)."""
    clip_id = _editable_clip(project, a["clip"], track_types=_TRACKS)
    clip = find_clip(project, clip_id)
    selector = str(a["mask"])
    if selector in _RESERVED:
        raise ValueError(f"{verb} : le masque '{selector}' est le recadrage ou celui de la stabilisation (set_crop / clear_crop).")
    stack = getattr(clip, "compositing", None)
    masks = stack.masks if isinstance(stack, Compositing) else ()
    if not any(mask.id == selector for mask in masks) and selector in window_ids(getattr(clip, "color_grade", None)):
        raise ValueError(f"{verb} : '{selector}' est une fenêtre d'étalonnage ; l'interface n'anime que les masques de compositing.")
    mask = _pick(masks, selector, "Masque", names=False)
    return clip_id, clip, mask_property_id(mask.id, str(a["property"]))


def _add_mask_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, clip, property_id = _mask_property(project, a, "add_mask_keyframe")
        name = str(a["property"])
        mask_id = property_id.split(".")[1]
        tracking = getattr(clip, "tracking", None)
        # Une liaison désactivée n'écrase rien : le rendu la saute (« désactivée = valeurs saisies seules »).
        if name in _TRACKED and any(link.enabled and link.mask_id == mask_id for link in getattr(tracking, "links", ()) or ()):
            raise ValueError(f"{name} du masque '{mask_id}' est suivi (tracking) : sa position vient du suivi, une image clé serait écrasée.")
        spec = get_target(property_id).spec
        value = a["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} doit être un nombre fini.")
        low = float("-inf") if spec.minimum is None else spec.minimum
        high = float("inf") if spec.maximum is None else spec.maximum
        if not low <= value <= high:                              # le modèle ramènerait la valeur : un script ne reçoit que ce qu'il a écrit
            raise ValueError(f"{name} = {value:g} est hors bornes (de {low:g} à {high:g}).")
        at = float(a["at"])
        if at > clip_duration(clip) + 1e-9:
            raise ValueError(f"L'instant {at:g} s dépasse la durée du clip : les images clés sont relatives au clip.")
        return add_keyframe(project, clip_id, property_id, at, float(value), interpolation=a.get("interpolation"))

    return operation


def _remove_mask_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, _clip, property_id = _mask_property(project, a, "remove_mask_keyframe")
        if not remove_keyframe_at(project, clip_id, property_id, float(a["at"])):
            raise KeyError(f"Aucune image clé de {a['property']!r} à {a['at']} s sur ce masque.")
        return None

    return operation


ANIMATION_VERBS: tuple[Verb, ...] = (
    Verb("add_mask_keyframe", "Pose (ou remplace) une image clé d'une propriété d'un masque de compositing ; le temps est relatif au clip.",
         (_CLIP, _MASK, _PROPERTY, _AT,
          Param("value", Kind.NUMBER, "La valeur (dans les bornes de la propriété du masque).", minimum=-MAX_TIME, maximum=MAX_TIME),
          Param("interpolation", Kind.ENUM, "Interpolation vers l'image clé suivante.", required=False,
                choices=tuple(item.value for item in InterpolationType))),
         _add_mask_keyframe, group="animation"),
    Verb("remove_mask_keyframe", "Retire l'image clé d'une propriété d'un masque à un instant donné.",
         (_CLIP, _MASK, _PROPERTY, _AT), _remove_mask_keyframe, group="animation"),
)

__all__ = ["ANIMATION_VERBS"]
