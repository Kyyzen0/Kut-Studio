"""Calques graphiques : hiérarchie, groupes, ordre, visibilité, verrou, doublons, presets, animation de texte (lot 4 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction du modèle que le panneau Calques et l'inspecteur appellent
(``ui/main_window_mixins/motion_graphics.py``, ``social.py``) : ``core/mograph_layers.py``, ``core/mograph_presets.py``,
``core/text_animations.py``. ``add_graphic`` (``verbs.py``) crée les calques ; il pose aussi dans un groupe (``group``).

Règles communes (voir :mod:`core.scripting.layer_rules` pour les contrôles) :

- **verrous** : le calque visé (et sa piste) doit être déverrouillé, sauf pour ``rename_layer``, ``set_layer_visible`` et
  ``set_layer_locked``, que le panneau Calques permet sur un calque verrouillé (il faut pouvoir le déverrouiller). Un verbe qui
  modifie des **tiers** (calques renumérotés par un changement d'ordre, enfants d'un parent supprimé, membres d'un groupe défait)
  refuse si l'un d'eux est verrouillé ; un groupe verrouillé ne gagne ni ne perd de membre. Une opération refusée ne laisse rien :
  la session restaure le projet ;
- **rien n'est ramené en silence** : le modèle bornerait un index d'ordre, une profondeur, et il garde « l'apparence » d'un calque
  qu'on reparente en recalculant son transform dans les bornes du transform ; le verbe refuse ce que le modèle ramènerait ;
- **identifiants créés** : ``group_layers`` crée le groupe, ``duplicate_layers`` la copie du **premier** calque cité (ses
  descendants ont des identifiants générés), ``apply_layer_preset`` le calque principal du preset ; ``id`` / ``as`` sont vérifiés
  par :mod:`core.object_ids` comme pour ``add_graphic``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..graphics import GraphicOverlay, GraphicType
from ..mograph_layers import (
    LayerClipboard, clear_parent, delete_layers, duplicate_layers, find_layer, group_layers, layer_clips, move_into_group,
    move_layer, rename_layer, reorder_layer, set_layer_locked, set_layer_visible, set_motion_blur, set_parent, siblings, ungroup,
)
from ..mograph_presets import MographPreset, apply_preset, builtin_preset_names, builtin_presets
from ..project_model import Project
from ..text_animations import TEXT_ANIMATIONS, apply_text_animation
from .layer_rules import (
    MAX_LAYERS_PER_OPERATION, appearance, frozen_layers, graphic_of, require_editable_container, require_layers_room,
    require_same_appearance, require_shallow, require_untouched,
)
from .resource_limits import require_keyframes_after
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _clip_id, _editable_clip

_MAX_INDEX = 10_000
_MIN_ROOM = 0.01
"""Secondes de calque qu'il faut au moins après le début d'une animation de texte (les temps d'images clés sont arrondis au millionième)."""
_MAX_BOUNCE_KEYFRAMES = 2_000
"""Le rebond d'``apply_text_animation`` pose une image clé tous les 0,25 s sur toute la durée : au plus 500 s de calque."""

_NO_LAYERS = LayerClipboard(clips=(), assets=(), track_ids=(), sequence_id="", base_time=0.0)
"""Le contenu d'un preset intégré vient de sa reconstruction sur la toile du projet, pas du presse-papiers : seul son nom compte."""
_CLIPS = Param("clips", Kind.LIST, f"Les calques, par leur identifiant (1 à {MAX_LAYERS_PER_OPERATION}, sans doublon).", minimum=1,
               maximum=MAX_LAYERS_PER_OPERATION, item=Param("clip", Kind.SELECTOR, "Un calque.", of="clip"))
_AT_TIME = Param("at", Kind.NUMBER, "Instant de la timeline, en secondes, où le calque garde exactement son apparence (défaut 0).",
                 required=False, minimum=0.0, maximum=MAX_TIME)


# --- Sélection --------------------------------------------------------------------------------------------------------


def _layer(project: Project, selector: str) -> str:
    """Le calque graphique visé (sans exiger qu'il soit déverrouillé)."""
    clip_id = _clip_id(project, selector)
    find_layer(project, clip_id)                         # LayerError (un ValueError) pour un clip qui n'est pas un calque
    return clip_id


def _editable_layer(project: Project, selector: str) -> str:
    """Le calque graphique visé, après avoir vérifié sa piste et son verrou (la règle de l'inspecteur)."""
    clip_id = _editable_clip(project, selector, track_types=("graphics",))
    find_layer(project, clip_id)
    return clip_id


def _editable_layers(project: Project, selectors: list[str]) -> list[str]:
    ids = [_editable_layer(project, selector) for selector in selectors]
    if len(set(ids)) != len(ids):
        raise ValueError("Un calque est cité plus d'une fois dans la liste.")
    return ids


def _protected(body: Callable[[Project], Any]) -> Operation:
    """Applique ``body`` puis refuse si un calque verrouillé a été modifié (tiers renumérotés, détachés…)."""

    def operation(project: Project) -> Any:
        frozen = frozen_layers(project)
        result = body(project)
        require_untouched(project, frozen)
        return result

    return operation


# --- Calques ----------------------------------------------------------------------------------------------------------


def _with_content(project: Project, ids: list[str]) -> list[str]:
    """Les calques cités et, pour un groupe, tout son contenu, dans l'ordre de la pile (du bas vers le haut) : ce que le modèle supprime
    ou copie."""
    wanted = set(ids)
    pending = list(ids)
    members: dict[str, list[str]] = {}
    for _index, _track, clip in layer_clips(project):
        members.setdefault(graphic_of(clip).group_id, []).append(clip.id)
    while pending:
        for member in members.get(pending.pop(), ()):
            if member not in wanted:
                wanted.add(member)
                pending.append(member)
    return [clip.id for _index, _track, clip in layer_clips(project) if clip.id in wanted]


def _delete_layers(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        ids = _editable_layers(project, a["clips"])
        doomed = frozenset(_with_content(project, ids))
        for clip_id in ids:
            require_editable_container(project, find_layer(project, clip_id)[1], ignoring=doomed)
        return delete_layers(project, ids)

    return _protected(body)


def _duplicate_layers(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        ids = _editable_layers(project, a["clips"])
        for clip_id in ids:
            require_editable_container(project, find_layer(project, clip_id)[1])
        copied = _with_content(project, ids)
        media = sum(1 for clip_id in copied if find_layer(project, clip_id)[1].asset_id in {asset.id for asset in project.media_assets})
        require_layers_room(project, len(copied), media=media)               # une copie n'a un média neuf que si l'original en a un
        # Le modèle rend les copies dans l'ordre de la pile : celle du premier calque cité s'y retrouve par son rang.
        created = duplicate_layers(project, ids, new_ids={ids[0]: a["id"]} if a.get("id") else None)
        return created[copied.index(ids[0])]

    return _protected(body)


def _rename_layer(a: Mapping[str, Any]) -> Operation:
    return lambda p: rename_layer(p, _layer(p, a["clip"]), a["name"])


def _set_layer_visible(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_layer_visible(p, _layer(p, a["clip"]), bool(a["visible"]))


def _set_layer_locked(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_layer_locked(p, _layer(p, a["clip"]), bool(a["locked"]))


def _set_layer_motion_blur(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_motion_blur(p, _editable_layer(p, a["clip"]), bool(a["enabled"]))


# --- Ordre ------------------------------------------------------------------------------------------------------------


def _reorder_layer(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        count = len(siblings(project, clip_id))
        index = int(a["index"])
        if not 0 <= index < count:                        # le modèle ramènerait l'index aux bords de la pile
            raise ValueError(f"L'index {index} est hors de la pile (de 0 à {count - 1}).")
        return reorder_layer(project, clip_id, index)

    return _protected(body)


def _move_layer(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        order = siblings(project, clip_id)
        index = next(i for i, clip in enumerate(order) if clip.id == clip_id)
        delta = int(a["delta"])
        if delta == 0:
            raise ValueError("Un déplacement de 0 rang ne fait rien : donnez un delta non nul.")
        if not 0 <= index + delta < len(order):
            raise ValueError(f"Un déplacement de {delta} sortirait de la pile (rang {index} sur {len(order)}).")
        return move_layer(project, clip_id, delta)

    return _protected(body)


# --- Groupes ----------------------------------------------------------------------------------------------------------


def _group_layers(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        ids = _editable_layers(project, a["clips"])
        for clip_id in ids:
            require_editable_container(project, find_layer(project, clip_id)[1])
        options: dict[str, Any] = {"group_id": a.get("id")}
        if a.get("name") is not None:
            options["name"] = a["name"]
        require_layers_room(project, 1)
        group = group_layers(project, ids, **options)
        require_shallow(project, group.id)
        return group

    return _protected(body)


def _ungroup_layers(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        group_id = _editable_layer(project, a["clip"])
        require_editable_container(project, find_layer(project, group_id)[1])
        return ungroup(project, group_id)

    return _protected(body)


def _move_into_group(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        require_editable_container(project, find_layer(project, clip_id)[1])
        group_id = ""
        if a.get("group") is not None:
            group_id = _layer(project, a["group"])
            track, group = find_layer(project, group_id)
            if track.locked or graphic_of(group).locked:
                raise ValueError(f"Le groupe '{group_id}' est verrouillé : son contenu ne change pas.")
        at = float(a.get("at", 0.0))
        before = appearance(project, clip_id, at)
        clip = move_into_group(project, clip_id, group_id, at_time=at)
        require_shallow(project, clip_id)
        require_same_appearance(before, appearance(project, clip_id, at), at)
        return clip

    return _protected(body)


# --- Parentage --------------------------------------------------------------------------------------------------------


def _set_parent(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        parent_id = _clip_id(project, a["parent"])
        if parent_id == clip_id:
            raise ValueError(f"Le calque '{clip_id}' ne peut pas être son propre parent.")
        at = float(a.get("at", 0.0))
        before = appearance(project, clip_id, at)
        clip = set_parent(project, clip_id, parent_id, at_time=at)
        require_shallow(project, clip_id)
        require_same_appearance(before, appearance(project, clip_id, at), at)
        return clip

    return _protected(body)


def _clear_parent(a: Mapping[str, Any]) -> Operation:
    def body(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        at = float(a.get("at", 0.0))
        before = appearance(project, clip_id, at)
        clip = clear_parent(project, clip_id, at_time=at)
        require_same_appearance(before, appearance(project, clip_id, at), at)
        return clip

    return _protected(body)


# --- Presets et animation de texte ------------------------------------------------------------------------------------


def _apply_layer_preset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        # Un preset intégré est reconstruit sur la toile du projet par ``apply_preset`` (qui ne regarde que son nom) : le
        # modèle porte donc un nom ici. Les presets de l'utilisateur sont des fichiers de sa configuration : un script n'en lit pas.
        named = MographPreset(str(a["preset"]), "", _NO_LAYERS, builtin=True)
        built = next(item for item in builtin_presets(project.width, project.height) if item.name == named.name)
        known = {raw["id"] for raw in built.clipboard.assets}
        require_layers_room(project, len(built.clipboard.clips), media=sum(1 for raw in built.clipboard.clips if raw.get("asset_id") in known))
        created = apply_preset(project, named, at=float(a["at"]), main_id=a.get("id"))
        return next(clip for clip in created if not graphic_of(clip).group_id)       # le calque principal : le premier hors groupe

    return _protected(operation)


def _apply_text_animation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_layer(project, a["clip"])
        _track, clip = find_layer(project, clip_id)
        graphic = clip.graphic
        if not isinstance(graphic, GraphicOverlay) or graphic.type != GraphicType.TEXT:
            raise ValueError(f"Le calque '{clip_id}' n'est pas un calque texte : les animations de texte n'existent que pour eux.")
        start = float(a.get("start", 0.0))
        if clip.duration - start < _MIN_ROOM:               # deux images clés arrondies au même instant se confondraient
            raise ValueError(f"Il reste moins de {_MIN_ROOM:g} s de calque après {start:g} s : trop court pour cette animation.")
        if a["animation"] == "bounce" and (clip.duration - start) / 0.25 > _MAX_BOUNCE_KEYFRAMES:
            raise ValueError(f"Le rebond pose une image clé tous les 0,25 s : au plus {_MAX_BOUNCE_KEYFRAMES * 0.25:g} s de calque.")
        require_keyframes_after(clip, lambda probe: apply_text_animation(probe, a["animation"], start=start), "apply_text_animation")
        return apply_text_animation(clip, a["animation"], start=start)

    return operation


LAYER_VERBS: tuple[Verb, ...] = (
    Verb("delete_layers", "Supprime des calques graphiques (un groupe emporte son contenu) ; les enfants d'un parent supprimé sont détachés. "
                          "Refusé si un calque supprimé, un enfant détaché ou le groupe d'un calque est verrouillé.",
         (_CLIPS,), _delete_layers, group="graphics"),
    Verb("duplicate_layers", "Duplique des calques au même instant, au-dessus des originaux (un groupe emporte son contenu, les liens entre "
                             "calques copiés ensemble sont refaits) ; le calque créé est la copie du premier calque cité (`id` / `as`).",
         (_CLIPS,), _duplicate_layers, creates="clip", group="graphics"),
    Verb("rename_layer", "Renomme un calque graphique (comme le panneau Calques, y compris un calque verrouillé).",
         (_CLIP, Param("name", Kind.STRING, "Nouveau nom.")), _rename_layer, group="graphics"),
    Verb("set_layer_visible", "Affiche ou masque un calque graphique (l'œil du panneau Calques, y compris un calque verrouillé).",
         (_CLIP, Param("visible", Kind.BOOLEAN, "Visible ou non.")), _set_layer_visible, group="graphics"),
    Verb("set_layer_locked", "Verrouille ou déverrouille un calque graphique.",
         (_CLIP, Param("locked", Kind.BOOLEAN, "Verrouillé ou non.")), _set_layer_locked, group="graphics"),
    Verb("set_layer_motion_blur", "Active ou désactive le flou de mouvement d'un calque graphique déverrouillé.",
         (_CLIP, Param("enabled", Kind.BOOLEAN, "Actif ou non.")), _set_layer_motion_blur, group="graphics"),
    Verb("reorder_layer", "Place un calque au rang `index` parmi ses frères (0 : le plus bas de la pile) ; un rang hors de la pile est refusé.",
         (_CLIP, Param("index", Kind.INTEGER, "Rang cible, de 0 au nombre de frères moins un.", minimum=0, maximum=_MAX_INDEX)),
         _reorder_layer, group="graphics"),
    Verb("move_layer", "Monte (positif) ou descend (négatif) un calque parmi ses frères ; un déplacement qui sortirait de la pile est refusé.",
         (_CLIP, Param("delta", Kind.INTEGER, "Nombre de rangs (non nul).", minimum=-_MAX_INDEX, maximum=_MAX_INDEX)),
         _move_layer, group="graphics"),
    Verb("group_layers", "Regroupe des calques de même niveau dans un nouveau groupe, qui prend la place du plus haut ; le calque créé est le "
                         "groupe (`id` / `as`, `name` : « Groupe » par défaut). Un calque d'effets ne se groupe pas.",
         (_CLIPS, Param("name", Kind.STRING, "Nom du groupe.", required=False)), _group_layers, creates="clip", group="graphics"),
    Verb("ungroup_layers", "Dissout un groupe : ses membres reviennent au niveau du groupe. Un groupe sans transform, opacité, masque ni effet "
                           "disparaît ; sinon il devient un contrôleur dont les membres héritent.",
         (_CLIP,), _ungroup_layers, group="graphics"),
    Verb("move_into_group", "Place un calque dans un groupe, en gardant son apparence à `at` ; sans `group`, le sort du groupe (niveau racine). "
                            "Un groupe qui se contiendrait lui-même, une cible qui n'est pas un groupe ou un calque d'effets sont refusés.",
         (_CLIP, Param("group", Kind.SELECTOR, "Le groupe cible ; absent, le niveau racine.", required=False, of="clip"), _AT_TIME),
         _move_into_group, group="graphics"),
    Verb("set_parent", "Fait suivre un parent (calque ou clip vidéo) à un calque, en gardant son apparence à `at` ; un parentage qui ferait une "
                       "boucle, ou plus de 32 niveaux, est refusé.",
         (_CLIP, Param("parent", Kind.SELECTOR, "Le parent : un calque graphique ou un clip vidéo.", of="clip"), _AT_TIME),
         _set_parent, group="graphics"),
    Verb("clear_parent", "Détache un calque de son parent, en gardant son apparence à `at` ; sans parent, ne fait rien.",
         (_CLIP, _AT_TIME), _clear_parent, group="graphics"),
    Verb("apply_layer_preset", "Pose un preset intégré (`Titre simple`, `Lower third`, `Call-out`…) à l'instant `at`, comme la bibliothèque de "
                               "presets, reconstruit sur le cadre du projet ; le calque créé est le calque principal (le premier hors groupe : "
                               "le groupe d'un bandeau, le contrôleur d'un call-out), `id` / `as`.",
         (Param("preset", Kind.ENUM, "Le preset intégré.", choices=builtin_preset_names()),
          Param("at", Kind.NUMBER, "Position sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME)),
         _apply_layer_preset, creates="clip", group="graphics"),
    Verb("apply_text_animation", "Pose une animation de texte (images clés) sur un calque **texte** déverrouillé ; les images clés des "
                                 "propriétés qu'elle anime sont remplacées, les autres restent.",
         (_CLIP, Param("animation", Kind.ENUM, "L'animation.", choices=TEXT_ANIMATIONS),
          Param("start", Kind.NUMBER, "Début de l'animation, en secondes depuis le début du calque (défaut 0) ; dans le calque.",
                required=False, minimum=0.0, maximum=MAX_TIME)),
         _apply_text_animation, group="graphics"),
)

__all__ = ["LAYER_VERBS"]
