"""Transform d'un clip : ``set_transform``, ``reset_transform`` (lot 2 de l'extension 0.8.0).

Aucune sémantique propre : ces verbes font ce que l'inspecteur « Mouvement » fait
(``ui/main_window_mixins/transform_and_clip_effects.py``) : ``ClipTransform.with_property`` champ par champ, puis
:func:`core.timeline_operations.set_clip_transform` ; ``reset_transform`` est :func:`core.timeline_operations.reset_clip_transform`.
``add_keyframe`` / ``remove_keyframe`` posent déjà les images clés du transform ; ce module complète avec la **valeur de base**.

- **bornes lues dans le modèle** : celles de :data:`core.visual_effects.TRANSFORM_PROPERTIES` (celles du registre d'animation) ; hors bornes,
  le script est refusé à la lecture, rien n'est ramené ;
- **une propriété animée n'est pas modifiable** : sa valeur de base serait masquée par la courbe (même règle que ``set_graphic``) ;
  ``reset_transform`` retire, lui, toutes les images clés du transform, comme le bouton « Réinitialiser » ;
- **genre de piste** : vidéo et graphiques, déverrouillés ; une propriété qui n'existe que pour un genre est refusée sur l'autre
  (``skew`` : graphiques ; ``pan_x`` / ``pan_y`` / ``fill`` : vidéo), car elle serait enregistrée mais ignorée au rendu ;
- **suivi et stabilisation** : ils *composent* la valeur de base (``core.tracking_bindings._compose_left`` part de la valeur évaluée du
  transform) ; la valeur écrite est donc bien celle de la base, pas le résultat rendu.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..animation import ValueKind
from ..animation_targets import get_target
from ..project_model import Project
from ..timeline_operations import find_clip, reset_clip_transform, set_clip_transform
from ..visual_effects import STATIC_TRANSFORM_PROPERTIES, TRANSFORM_PROPERTIES, TRANSFORM_PROPERTY_NAMES
from .verbs import Kind, Operation, Param, Verb, _CLIP, _editable_clip, _owner_track

_TRACKS = ("video", "graphics")

_DOC = {
    "position_x": "Décalage horizontal, en largeurs de cadre (0 = centré).",
    "position_y": "Décalage vertical, en hauteurs de cadre (0 = centré).",
    "scale": "Échelle uniforme (1 = taille naturelle).",
    "rotation": "Rotation en degrés.",
    "opacity": "Opacité, de 0 (transparent) à 1.",
    "anchor_x": "Ancre horizontale, en fraction de la largeur du calque (0,5 = centre).",
    "anchor_y": "Ancre verticale, en fraction de la hauteur du calque (0,5 = centre).",
    "scale_x": "Multiplicateur horizontal de l'échelle (1 = aucun).",
    "scale_y": "Multiplicateur vertical de l'échelle (1 = aucun).",
    "skew": "Inclinaison horizontale en degrés (calques graphiques seulement).",
    "flip_h": "Miroir horizontal.",
    "flip_v": "Miroir vertical.",
    "fill": "Remplir le cadre sans déformation, l'excédent est rogné (clips vidéo seulement).",
    "pan_x": "Fenêtre du cadrage « remplir », de -1 (bord gauche) à 1 (bord droit) (clips vidéo seulement).",
    "pan_y": "Fenêtre du cadrage « remplir », de -1 (bord haut) à 1 (bord bas) (clips vidéo seulement).",
}


def _param(name: str) -> Param:
    spec = TRANSFORM_PROPERTIES[name]
    if spec.kind is ValueKind.BOOL:
        return Param(name, Kind.BOOLEAN, _DOC[name], required=False)
    return Param(name, Kind.NUMBER, f"{_DOC[name]} Défaut : {spec.default:g}.", required=False, minimum=spec.minimum, maximum=spec.maximum)


def _applies(name: str, track_type: str) -> bool:
    """Le registre d'animation dit sur quels genres de piste une propriété existe ; « fill » (statique, donc hors registre) est vidéo seulement."""
    if name in STATIC_TRANSFORM_PROPERTIES:
        return track_type == "video"
    return bool(get_target(name).applies_to(track_type))


def _set_transform(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in TRANSFORM_PROPERTY_NAMES if name in a]
        if not given:
            raise ValueError("set_transform : donnez au moins un champ à modifier.")
        clip_id = _editable_clip(project, a["clip"], track_types=_TRACKS)
        clip = find_clip(project, clip_id)
        kind = _owner_track(project, clip_id).type
        animated = {str(keyframe.property_name) for keyframe in clip.transform_keyframes}
        transform = clip.transform
        for name in given:
            if not _applies(name, kind):
                raise ValueError(f"{name} ne s'applique pas à un clip d'une piste '{kind}'.")
            if name in animated:
                raise ValueError(f"{name} du clip '{clip_id}' est animé (images clés) : une valeur de base serait masquée, retirez les images clés d'abord.")
            transform = transform.with_property(name, a[name])          # valide les bornes (le décodeur l'a déjà fait), n'écrête rien
        return set_clip_transform(project, clip_id, transform)

    return operation


def _reset_transform(a: Mapping[str, Any]) -> Operation:
    return lambda p: reset_clip_transform(p, _editable_clip(p, a["clip"], track_types=_TRACKS))


TRANSFORM_VERBS: tuple[Verb, ...] = (
    Verb("set_transform", "Règle la valeur de base du transform d'un clip vidéo ou d'un calque (au moins un champ) ; les autres champs et les images clés restent. "
                          "Une propriété animée est refusée.",
         (_CLIP, *(_param(name) for name in TRANSFORM_PROPERTY_NAMES)), _set_transform, group="transform"),
    Verb("reset_transform", "Remet le transform d'un clip à l'identité et retire toutes ses images clés de transform.",
         (_CLIP,), _reset_transform, group="transform"),
)

__all__ = ["TRANSFORM_VERBS"]
