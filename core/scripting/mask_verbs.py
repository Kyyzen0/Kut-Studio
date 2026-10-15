"""Verbes des masques de compositing d'un clip : ``add_mask``, ``set_mask``, ``set_mask_points``, ``remove_mask`` (ADR-0006 étape 3b).

Aucune sémantique propre : ces verbes écrivent ``Clip.compositing.masks`` comme le fait l'inspecteur Compositing
(``ui/compositing_editor.py``) et l'éditeur du moniteur (``ui/main_window_mixins/mask_edit.py``) :

- ``add_mask`` = ``Mask(shape=…)`` ajouté à la pile, le recadrage (``crop``) restant en dernier (:func:`core.clip_crop.keep_crop_last`) ;
- ``set_mask`` = ``replace(mask, …, id=mask.id)`` ; l'identifiant et la forme d'un masque ne changent pas ;
- ``set_mask_points`` = ``replace(mask, points=…, handles=…)`` (sommets d'un polygone, nœuds d'une courbe) ;
- ``remove_mask`` = retrait de la pile, avec les images-clés ``mask.<id>.*`` (comme la fenêtre principale) et les liaisons de suivi.

Le masque de recadrage (:data:`core.clip_crop.CROP_MASK_ID`) est le domaine de ``set_crop`` / ``clear_crop`` : ces verbes le refusent.
Les fenêtres d'étalonnage (aussi des ``Mask``) ont leurs verbes (``*_color_window``) et vivent dans le graphe d'étalonnage, pas ici.

Rien n'est ramené en silence : ``Mask`` écrête les nombres hors bornes, remplace un polygone de moins de trois sommets par un
pentagone et ignore un sommet mal formé, un script les voit donc refusés. Une propriété animée (images-clés ``mask.<id>.<nom>``)
est refusée aussi : une valeur statique serait masquée par la courbe, et un script n'a pas de tête de lecture où poser une
image-clé (``add_keyframe`` n'accepte d'ailleurs pas les propriétés ``mask.<id>.*`` : ses choix sont fixes) ; les images-clés d'un
masque se règlent dans l'interface, ou partent avec ``remove_mask``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
from typing import Any

from .. import mask_path
from ..clip_crop import CROP_MASK_ID, keep_crop_last
from ..color_windows import forget_windows, window_ids
from ..compositing import (
    MASK_PROPERTY_ORDER, _MASK_BOUNDS, Compositing, Mask, MaskMode, MaskShape, mask_property_id, new_mask_id,
)
from ..mask_path_handles import nodes_refusal
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import Project
from ..timeline_operations import find_clip
from .path_nodes import nodes_to_arrays
from .verbs import MAX_TEXT, Kind, Operation, Param, Verb, _CLIP, _editable_clip, _nodes_param, _pick

TRACKING_CROP_ID = "trackcrop"
"""Identifiant réservé au recadrage de la stabilisation (:data:`core.tracking_bindings.CROP_MASK_ID`) : jamais un masque de script."""

_RESERVED = (CROP_MASK_ID, TRACKING_CROP_ID)
_NUMBERS = MASK_PROPERTY_ORDER
_TRACKS = ("video", "graphics")
_SHAPES = tuple(item.value for item in MaskShape)
_MODES = tuple(item.value for item in MaskMode)
_VERTEX_LIMIT = 2.0
"""Bornes des sommets d'un polygone : celles de ``Mask`` (``core.compositing._points``), au-delà desquelles il écrête."""


# --- Validation (la façade Python n'est pas toujours passée par le décodeur) ---------------------------------------------------


def _number(name: str, value: Any) -> float:
    """Un nombre fini, hors booléen, dans les bornes du modèle ; sinon ``ValueError`` (jamais ramené)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} doit être un nombre fini.")
    low, high = _MASK_BOUNDS[name]
    if not low <= value <= high:
        raise ValueError(f"{name} = {value:g} est hors bornes (de {low:g} à {high:g}).")
    return float(value)


def _enum(kind: type[Enum], name: str, value: Any) -> Any:
    try:
        return kind(value)
    except ValueError:
        raise ValueError(f"{name} : valeur inconnue (attendues : {', '.join(str(item.value) for item in kind)}).") from None


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} doit être un texte court, sans espace en bordure ni caractère de contrôle (au plus {MAX_TEXT}).")
    return value


def _flag(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} doit être vrai ou faux.")
    return value


def _vertices(points: Any) -> tuple[tuple[float, float], ...]:
    """Sommets d'un polygone : de 3 à ``MAX_NODES``, ``{x, y}`` finis dans ±2. Nombre borné AVANT toute lecture des éléments."""
    if not isinstance(points, (list, tuple)):
        raise ValueError("Un polygone se décrit par une liste de sommets {x, y}.")
    if not 3 <= len(points) <= mask_path.MAX_NODES:
        raise ValueError(f"Un polygone porte de 3 à {mask_path.MAX_NODES} sommets.")
    result: list[tuple[float, float]] = []
    for vertex in points:
        if not isinstance(vertex, Mapping) or set(vertex) != {"x", "y"}:
            raise ValueError("Un sommet est un objet {x, y}.")
        pair = []
        for key in ("x", "y"):
            value = vertex[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Sommet : '{key}' doit être un nombre fini.")
            if abs(value) > _VERTEX_LIMIT:
                raise ValueError(f"Sommet : '{key}' est hors bornes (de {-_VERTEX_LIMIT:g} à {_VERTEX_LIMIT:g}).")
            pair.append(float(value))
        result.append((pair[0], pair[1]))
    return tuple(result)


def _refuse_reserved(mask_id: str, verb: str) -> None:
    if mask_id in _RESERVED:
        raise ValueError(f"{verb} : l'identifiant '{mask_id}' est réservé au recadrage (set_crop / clear_crop).")


# --- Accès à la pile ------------------------------------------------------------------------------------------------------------


def _visual_clip(project: Project, selector: str) -> Any:
    """Le clip (piste vidéo ou graphique, déverrouillée, calque déverrouillé : la règle de ``core.compositing_guard``)."""
    return find_clip(project, _editable_clip(project, selector, track_types=_TRACKS))


def _stack(clip: Any) -> Compositing:
    value = getattr(clip, "compositing", None)
    return value if isinstance(value, Compositing) else Compositing()


def _mask_of(clip: Any, selector: str, verb: str) -> Mask:
    _refuse_reserved(selector, verb)
    return _pick(_stack(clip).masks, selector, "Masque", names=False)


def _animated(clip: Any, mask_id: str, name: str) -> bool:
    target = mask_property_id(mask_id, name)
    return any(str(getattr(kf, "property_name", "")) == target for kf in getattr(clip, "animation", ()) or ())


def _taken_ids(clip: Any) -> set[str]:
    """Identifiants déjà pris : masques, fenêtres d'étalonnage, et ceux qu'une animation ``mask.<id>.*`` orpheline réclamerait."""
    taken = {mask.id for mask in _stack(clip).masks} | window_ids(getattr(clip, "color_grade", None))
    taken |= {str(kf.property_name).split(".")[1] for kf in getattr(clip, "animation", ()) or ()
              if str(kf.property_name).startswith("mask.") and str(kf.property_name).count(".") >= 2}
    return taken


def _write(clip: Any, masks: tuple[Mask, ...]) -> None:
    clip.compositing = replace(_stack(clip), masks=masks)


# --- Verbes ----------------------------------------------------------------------------------------------------------------------


def _add_mask(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip = _visual_clip(project, a["clip"])
        shape = _enum(MaskShape, "shape", a["shape"])
        requested = a.get("id")
        if requested is not None:
            _refuse_reserved(str(requested), "add_mask")
        taken = _taken_ids(clip)
        try:
            if requested is not None:
                mask_id = resolve_object_id(requested, prefix="m", kind="masque", taken=taken)
            else:                                                 # comme l'inspecteur : 8 chiffres hexadécimaux (``new_mask_id``)
                mask_id = new_mask_id()
                while mask_id in taken:
                    mask_id = new_mask_id()
        except InvalidObjectId as error:
            raise ValueError(str(error)) from None
        values: dict[str, Any] = {name: _number(name, a[name]) for name in _NUMBERS if name in a}
        if "inverted" in a:
            values["inverted"] = _flag("inverted", a["inverted"])
        if "mode" in a:
            values["mode"] = _enum(MaskMode, "mode", a["mode"])
        if "name" in a:
            values["name"] = _text("name", a["name"])
        if "nodes" in a:
            if shape is not MaskShape.PATH:
                raise ValueError("add_mask : 'nodes' ne s'applique qu'à la forme 'path'.")
            values["points"], values["handles"] = nodes_to_arrays(a["nodes"])
        if "points" in a:
            if shape is not MaskShape.POLYGON:
                raise ValueError("add_mask : 'points' ne s'applique qu'à la forme 'polygon'.")
            values["points"] = _vertices(a["points"])
        mask = Mask(shape=shape, id=mask_id, **values)
        for key in ("points", "handles"):                         # le modèle ne doit rien avoir ramené en silence
            if key in values and getattr(mask, key) != values[key]:
                raise ValueError("Masque refusé : le modèle aurait modifié les sommets ou les nœuds.")
        _write(clip, keep_crop_last((*_stack(clip).masks, mask)))
        return mask

    return operation


def _set_mask(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in (*_NUMBERS, "inverted", "mode", "name") if name in a]
        if not given:
            raise ValueError("set_mask : donnez au moins un champ à modifier.")
        clip = _visual_clip(project, a["clip"])
        base = _mask_of(clip, a["mask"], "set_mask")
        changes: dict[str, Any] = {}
        for name in given:
            if name in _MASK_BOUNDS:
                if _animated(clip, base.id, name):
                    raise ValueError(
                        f"{name} du masque '{base.id}' est animé (images-clés) : un script ne le modifie pas, réglez-le dans l'interface.")
                changes[name] = _number(name, a[name])
            elif name == "inverted":
                changes[name] = _flag(name, a[name])
            elif name == "mode":
                changes[name] = _enum(MaskMode, name, a[name])
            else:
                changes[name] = _text(name, a[name])
        updated = replace(base, **changes, id=base.id)
        _write(clip, tuple(updated if mask.id == base.id else mask for mask in _stack(clip).masks))
        return updated

    return operation


def _set_mask_points(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if ("nodes" in a) == ("points" in a):
            raise ValueError("set_mask_points : donnez 'nodes' (courbe) ou 'points' (polygone), un seul.")
        clip = _visual_clip(project, a["clip"])
        base = _mask_of(clip, a["mask"], "set_mask_points")
        if "nodes" in a:
            if base.shape is not MaskShape.PATH:
                raise ValueError(f"Le masque '{base.id}' n'est pas une courbe de Bézier (forme '{base.shape.value}').")
            points, handles = nodes_to_arrays(a["nodes"])
            if nodes_refusal(base, points, handles) is not None:
                raise ValueError("Courbe refusée : nombre de nœuds ou valeurs hors des bornes du modèle.")
            updated = replace(base, points=points, handles=handles, id=base.id)      # ce que écrit l'éditeur du moniteur
            if updated.points != points or updated.handles != handles:  # pragma: no cover - le modèle ne doit rien ramener
                raise ValueError("Courbe refusée : le modèle aurait modifié les nœuds.")
        else:
            if base.shape is not MaskShape.POLYGON:
                raise ValueError(f"Le masque '{base.id}' n'est pas un polygone (forme '{base.shape.value}').")
            points = _vertices(a["points"])
            updated = replace(base, points=points, id=base.id)
            if updated.points != points:  # pragma: no cover - les sommets sont déjà validés
                raise ValueError("Polygone refusé : le modèle aurait modifié les sommets.")
        _write(clip, tuple(updated if mask.id == base.id else mask for mask in _stack(clip).masks))
        return updated

    return operation


def _remove_mask(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip = _visual_clip(project, a["clip"])
        base = _mask_of(clip, a["mask"], "remove_mask")
        _write(clip, tuple(mask for mask in _stack(clip).masks if mask.id != base.id))
        forget_windows(clip, {base.id})        # images-clés ``mask.<id>.*`` et liaisons de suivi : ils partent avec le masque
        return None

    return operation


# --- Table -----------------------------------------------------------------------------------------------------------------------


def _field(name: str, doc: str, *, required: bool = False) -> Param:
    low, high = _MASK_BOUNDS[name]
    return Param(name, Kind.NUMBER, doc, required=required, minimum=low, maximum=high)


def _style_params(prefix: str) -> tuple[Param, ...]:
    return (
        _field("position_x", f"{prefix}Centre X, fraction du calque (défaut 0,5)."),
        _field("position_y", f"{prefix}Centre Y, fraction du calque (défaut 0,5)."),
        _field("width", f"{prefix}Largeur, fraction du calque (défaut 0,5)."),
        _field("height", f"{prefix}Hauteur, fraction du calque (défaut 0,5)."),
        _field("rotation", f"{prefix}Rotation, en degrés."),
        _field("feather", f"{prefix}Adoucissement du contour (défaut 0)."),
        _field("expansion", f"{prefix}Dilatation (+) ou contraction (−) de la forme."),
        _field("opacity", f"{prefix}Opacité du masque (défaut 1)."),
        Param("inverted", Kind.BOOLEAN, f"{prefix}Masque inversé.", required=False),
        Param("mode", Kind.ENUM, f"{prefix}add (union), subtract (retire) ou intersect (intersection).", required=False, choices=_MODES),
        Param("name", Kind.STRING, f"{prefix}Nom du masque.", required=False),
    )


_MASK = Param("mask", Kind.SELECTOR, "Le masque du clip, par identifiant (pas le recadrage « crop »).", of="mask")
_POINTS = Param("points", Kind.LIST, f"Sommets du polygone, de 3 à {mask_path.MAX_NODES} (fractions de la boîte du masque, ±2).",
                required=False, minimum=3, maximum=mask_path.MAX_NODES,
                fields=(Param("x", Kind.NUMBER, "Sommet X (±2).", minimum=-_VERTEX_LIMIT, maximum=_VERTEX_LIMIT),
                        Param("y", Kind.NUMBER, "Sommet Y (±2).", minimum=-_VERTEX_LIMIT, maximum=_VERTEX_LIMIT)))

MASK_VERBS: tuple[Verb, ...] = (
    Verb("add_mask", "Ajoute un masque de compositing à la pile d'un clip vidéo ou d'un calque (en dernier, avant le recadrage) ; mêmes valeurs par défaut que l'inspecteur.",
         (_CLIP, Param("shape", Kind.ENUM, "rectangle, ellipse, polygon ou path (courbe de Bézier : voir « nodes »).", choices=_SHAPES),
          *_style_params(""),
          _POINTS, _nodes_param(required=False)),
         _add_mask, creates="mask", creates_scope="clip", group="edit"),
    Verb("set_mask", "Modifie un masque de compositing (pas le recadrage) ; au moins un champ, refusé si une propriété modifiée est animée.",
         (_CLIP, _MASK, *_style_params("")), _set_mask, group="edit"),
    Verb("set_mask_points", "Remplace les sommets d'un masque polygone (« points ») ou les nœuds d'un masque courbe (« nodes »).",
         (_CLIP, _MASK, _POINTS, _nodes_param(required=False)), _set_mask_points, group="edit"),
    Verb("remove_mask", "Retire un masque de compositing (pas le recadrage) avec ses images-clés et ses liaisons de suivi.",
         (_CLIP, _MASK), _remove_mask, group="edit"),
)

__all__ = ["MASK_VERBS", "TRACKING_CROP_ID"]
