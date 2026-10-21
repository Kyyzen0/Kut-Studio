"""État d'un document de scène éditable : valeurs immuables, identités stables, plafonds du fichier (ADR-0010).

Une `Scene` est immuable ; un document d'édition (`kut3d.document.SceneDocument`) est une suite d'ÉTATS immuables (`DocumentState`)
reliés par des commandes (`kut3d.document_commands`). Annuler, c'est revenir à l'état précédent : les `Model` (maillages,
parfois des millions de sommets) sont PARTAGÉS entre états, jamais copiés ; seul le petit tuple d'objets l'est.

**Identités.** Chaque objet (`o<N>`) et chaque lumière (`l<N>`) a un identifiant donné à sa création, jamais réutilisé dans une
session (le compteur `next_id` fait partie de l'état : annuler puis rétablir une création redonne le même identifiant). Les
identifiants ne s'écrivent PAS dans le `.kut3d` (le format ne change pas) : à la lecture ils sont redonnés dans l'ordre du fichier.
Le NOM (`Instance.name`) est une étiquette libre, non unique : jamais une clé, jamais corrigé en silence.

Les plafonds du fichier (`MAX_INSTANCES`, `MAX_LIGHTS`, `MAX_MODELS`, triangles de formes de base) sont vérifiés à CHAQUE état :
un document n'est jamais dans un état que `save_scene` refuserait pour une raison de taille.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from kut3d.errors import short
from kut3d.mathutil import Array
from kut3d.model import Material, Model
from kut3d.primitive_models import _rotation as euler_rotation
from kut3d.primitives import MAX_SCENE_PRIMITIVE_TRIANGLES
from kut3d.scene import (
    MAX_INSTANCES,
    MAX_LIGHTS,
    Camera,
    Instance,
    Light,
    Scene,
)
from kut3d.scene_io_fields import MAX_MODELS, MAX_NAME_CHARS


class DocumentError(ValueError):
    """Une édition est refusée (message en français, sans recopier l'entrée entière). Le document reste inchangé."""


@dataclass(frozen=True, eq=False)
class SceneObject:
    """Un modèle posé, avec son identifiant stable. `instance` est la valeur validée de la scène (transformation, animation, nom)."""

    id: str
    instance: Instance

    @property
    def name(self) -> str:
        return self.instance.name

    @property
    def model(self) -> Model:
        return self.instance.model

    @property
    def transform(self) -> Array:
        return self.instance.transform  # type: ignore[no-any-return]


@dataclass(frozen=True, eq=False)
class LightEntry:
    """Une lumière avec son identifiant stable."""

    id: str
    light: Light


@dataclass(frozen=True, eq=False)
class DocumentState:
    """Tout ce que le fichier `.kut3d` décrit : objets, lumières, caméra de la scène, fond. Immuable."""

    objects: tuple[SceneObject, ...]
    lights: tuple[LightEntry, ...]
    camera: Camera
    background: Any = None
    next_id: int = 1

    def to_scene(self) -> Scene:
        return Scene(
            instances=[o.instance for o in self.objects],
            lights=[e.light for e in self.lights],
            camera=self.camera,
            background=self.background,
        )

    def find_object(self, object_id: str) -> SceneObject:
        for item in self.objects:
            if item.id == object_id:
                return item
        raise DocumentError(f"objet inconnu : {short(object_id, 30)}.")

    def find_light(self, light_id: str) -> LightEntry:
        for item in self.lights:
            if item.id == light_id:
                return item
        raise DocumentError(f"lumière inconnue : {short(light_id, 30)}.")

    def ids(self) -> tuple[str, ...]:
        return (*(o.id for o in self.objects), *(e.id for e in self.lights))


def state_from_scene(scene: Scene) -> DocumentState:
    """Le premier état d'un document : identifiants `o1..oN` dans l'ordre des instances, `l1..lM` dans celui des lumières."""
    counter = 1
    objects = []
    for inst in scene.instances:
        objects.append(SceneObject(f"o{counter}", inst))
        counter += 1
    lights = []
    for light in scene.lights:
        lights.append(LightEntry(f"l{counter}", light))
        counter += 1
    state = DocumentState(tuple(objects), tuple(lights), scene.camera, scene.background, counter)
    check_state(state)
    return state


def check_name(value: Any, what: str = "nom") -> str:
    """Un nom d'objet : texte d'au plus `MAX_NAME_CHARS` caractères. Les mêmes règles valent pour un nom saisi et pour un nom
    par défaut (jamais raccourci ni suffixé en silence)."""
    if not isinstance(value, str):
        raise DocumentError(f"{what} : un texte est attendu (reçu {type(value).__name__}).")
    if len(value) > MAX_NAME_CHARS:
        raise DocumentError(f"{what} : {len(value)} caractères, maximum {MAX_NAME_CHARS}.")
    return value


def triangle_total(models: list[Model]) -> int:
    """Triangles des formes de base, chaque modèle DISTINCT (par identité) une seule fois, comme le fichier les écrit."""
    seen: set[int] = set()
    total = 0
    for model in models:
        if model.primitive is not None and id(model) not in seen:
            seen.add(id(model))
            total += model.primitive.triangle_count
    return total


def check_triangle_budget(total: int) -> None:
    if total > MAX_SCENE_PRIMITIVE_TRIANGLES:
        raise DocumentError(
            f"trop de triangles pour l'ensemble des formes de base ({total}, maximum {MAX_SCENE_PRIMITIVE_TRIANGLES})."
        )


def check_state(state: DocumentState) -> None:
    """Refuse un état que le fichier ne pourrait pas contenir (plafonds) ou qui n'est pas cohérent."""
    if len(state.objects) > MAX_INSTANCES:
        raise DocumentError(f"trop d'objets ({len(state.objects)}, maximum {MAX_INSTANCES}).")
    if len(state.lights) > MAX_LIGHTS:
        raise DocumentError(f"trop de lumières ({len(state.lights)}, maximum {MAX_LIGHTS}).")
    ids = state.ids()
    if len(set(ids)) != len(ids):
        raise DocumentError("identifiants en double.")
    models = [o.model for o in state.objects]
    distinct = len({id(m) for m in models})
    if distinct > MAX_MODELS:
        raise DocumentError(f"trop de modèles distincts ({distinct}, maximum {MAX_MODELS}).")
    check_triangle_budget(triangle_total(models))
    for obj in state.objects:
        check_name(obj.name, "nom de l'objet")
        require_savable(obj.model)


def require_savable(model: Model) -> None:
    """Un modèle sans fichier source qui n'est pas une forme de base ne s'enregistrerait pas : refusé dès l'ajout."""
    if model.primitive is None and model.source == Path(""):
        raise DocumentError(
            "ce modèle n'a pas de fichier source (géométrie construite en code) : enregistrez-le d'abord dans un fichier "
            "OBJ, glTF, PLY ou STL, ou utilisez une forme de base."
        )


def require_storable_material(material: Material | None) -> None:
    """Les textures d'une forme de base ne s'enregistrent pas encore (`save_scene` les refuse) : refusées ici, à l'édition."""
    if material is None:
        return
    if not isinstance(material, Material):
        raise DocumentError("matériau : un kut3d.Material ou None est attendu.")
    if any(
        t is not None
        for t in (
            material.base_color_texture,
            material.normal_texture,
            material.metallic_roughness_texture,
            material.emissive_texture,
            material.occlusion_texture,
        )
    ):
        raise DocumentError("matériau : les textures d'une forme de base ne s'enregistrent pas encore.")


def _triple(value: Any, what: str) -> list[float]:
    try:
        arr = np.array(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise DocumentError(f"{what} : 3 nombres attendus.") from None
    if arr.shape != (3,) or not np.isfinite(arr).all():
        raise DocumentError(f"{what} : 3 nombres finis attendus.")
    return [float(x) for x in arr]


def pose_matrix(position: Any = (0.0, 0.0, 0.0), rotation_deg: Any = (0.0, 0.0, 0.0), scale: Any = 1.0) -> Array:
    """Matrice 4x4 d'une pose (translation, rotation X/Y/Z en degrés appliquée dans l'ordre Z, Y, X, échelle > 0) : celle des
    champs « Position / Rotation / Échelle » d'un panneau de propriétés. Même convention que `primitive_instance`."""
    pos = _triple(position, "position")
    angles = _triple(rotation_deg, "rotation")
    if isinstance(scale, bool):
        raise DocumentError("échelle : un ou trois nombres finis > 0 sont attendus.")
    factors = _triple([scale] * 3 if np.ndim(scale) == 0 else scale, "échelle")
    if not all(f > 0 for f in factors):
        raise DocumentError("échelle : un ou trois nombres finis > 0 sont attendus.")
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = euler_rotation(angles) * np.array(factors)[None, :]
    matrix[:3, 3] = pos
    return matrix


def with_instance(state: DocumentState, object_id: str, **changes: Any) -> DocumentState:
    """L'état où l'instance de l'objet est reconstruite avec `changes` (revalide : NaN, matrice dégénérée, clip inconnu...)."""
    target = state.find_object(object_id)
    try:
        instance = replace(target.instance, **changes)
    except ValueError as exc:
        raise DocumentError(short(str(exc), 160)) from exc
    objects = tuple(SceneObject(o.id, instance) if o.id == object_id else o for o in state.objects)
    return replace(state, objects=objects)
