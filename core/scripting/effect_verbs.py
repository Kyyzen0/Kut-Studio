"""Effets visuels d'un clip vidéo : ``add_effect``, ``remove_effect``, ``set_effect_enabled``, ``update_effect``, ``move_effect``.

Aucune sémantique propre : ces verbes appellent les fonctions que le panneau d'effets de l'interface appelle
(``ui/main_window_mixins/transform_and_clip_effects.py``), dans :mod:`core.effects_model` — ``add_effect_to_clip``,
``remove_effect_from_clip``, ``set_clip_effect_enabled``, ``update_clip_effect_parameters``, ``move_clip_effect``.

Les trois règles de la revue de l'étape 4 :

- **bornes lues dans le modèle** : les paramètres sont validés par :func:`core.effects_model.validate_parameters`
  (``EFFECT_PARAMETER_SPECS``), qui *refuse* l'inconnu et le hors bornes ; rien n'est écrêté. ``move_effect`` refuse un
  déplacement qui sortirait de la chaîne, alors que le modèle (pour un bouton) le bornerait en silence ;
- **verrou** : piste verrouillée et calque verrouillé sont refusés (par le verbe, puis par le modèle) ;
- **genre de piste** : seuls les clips d'une piste *vidéo* portent des effets (graphiques, audio et sous-titres sont refusés).

Un effet est désigné par son identifiant, local au clip. ``add_effect`` accepte ``id`` / ``as`` (identifiant libre vérifié par
:mod:`core.object_ids`) ; sans identifiant, le modèle en génère un (``fx-…``), comme l'interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effects_model import (
    EffectType, add_effect_to_clip, clip_effects, move_clip_effect, remove_effect_from_clip, set_clip_effect_enabled,
    update_clip_effect_parameters,
)
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import Project
from ..timeline_operations import find_clip
from .resource_limits import require_effect_room
from .verbs import Kind, Operation, Param, Verb, _CLIP, _editable_clip, _pick

_MAX_DELTA = 1000
_EFFECT = Param("effect", Kind.SELECTOR, "L'effet du clip, par identifiant.", of="effect")
_PARAMS = Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres) ; ceux du modèle (noms et bornes) les valident.")


def _video_clip(project: Project, selector: str) -> str:
    return _editable_clip(project, selector, track_types=("video",))


def _effect_id(project: Project, clip_id: str, selector: str) -> str:
    return str(_pick(clip_effects(project, clip_id), selector, "Effet", names=False).id)


def _add_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        requested = a.get("id")
        if requested is not None:
            try:
                requested = resolve_object_id(requested, prefix="fx", kind="effet", taken={item.id for item in clip_effects(project, clip_id)})
            except InvalidObjectId as error:
                raise ValueError(str(error)) from None
        require_effect_room(find_clip(project, clip_id))
        return add_effect_to_clip(
            project, clip_id, a["type"], effect_id=requested, params=a.get("params"), enabled=bool(a.get("enabled", True)),
        )

    return operation


def _remove_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        return remove_effect_from_clip(project, clip_id, _effect_id(project, clip_id, a["effect"]))

    return operation


def _set_effect_enabled(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        return set_clip_effect_enabled(project, clip_id, _effect_id(project, clip_id, a["effect"]), bool(a["enabled"]))

    return operation


def _update_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        if not a["params"]:
            raise ValueError("update_effect : donnez au moins un paramètre à modifier.")
        return update_clip_effect_parameters(project, clip_id, _effect_id(project, clip_id, a["effect"]), a["params"])

    return operation


def _move_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        effect_id = _effect_id(project, clip_id, a["effect"])
        chain = clip_effects(project, clip_id)
        target = next(index for index, item in enumerate(chain) if item.id == effect_id) + int(a["delta"])
        if int(a["delta"]) == 0 or not 0 <= target < len(chain):
            raise ValueError(f"move_effect : déplacement impossible (la chaîne compte {len(chain)} effet(s)) ; le modèle le borne, un script le refuse.")
        return move_clip_effect(project, clip_id, effect_id, int(a["delta"]))

    return operation


EFFECT_VERBS: tuple[Verb, ...] = (
    Verb("add_effect", "Ajoute un effet visuel à un clip vidéo (en fin de chaîne) ; paramètres absents : valeurs par défaut du modèle.",
         (_CLIP, Param("type", Kind.ENUM, "Le type d'effet.", choices=tuple(item.value for item in EffectType)),
          Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres) ; absents, les valeurs par défaut du modèle.", required=False),
          Param("enabled", Kind.BOOLEAN, "Actif ou non (défaut : actif).", required=False)),
         _add_effect, creates="effect", creates_scope="clip", group="effects"),
    Verb("remove_effect", "Retire un effet d'un clip vidéo.", (_CLIP, _EFFECT), _remove_effect, group="effects"),
    Verb("set_effect_enabled", "Active ou désactive un effet d'un clip vidéo.",
         (_CLIP, _EFFECT, Param("enabled", Kind.BOOLEAN, "Actif ou non.")), _set_effect_enabled, group="effects"),
    Verb("update_effect", "Modifie des paramètres d'un effet (noms et bornes du modèle, rien n'est écrêté) ; au moins un.",
         (_CLIP, _EFFECT, _PARAMS), _update_effect, group="effects"),
    Verb("move_effect", "Déplace un effet dans la chaîne (-1 : monter, 1 : descendre) ; refusé s'il sortirait de la chaîne.",
         (_CLIP, _EFFECT, Param("delta", Kind.INTEGER, "Nombre de crans (non nul).", minimum=-_MAX_DELTA, maximum=_MAX_DELTA)),
         _move_effect, group="effects"),
)

__all__ = ["EFFECT_VERBS"]
