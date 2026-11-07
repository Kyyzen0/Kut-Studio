"""Commandes d'édition d'un document de scène (ADR-0010) : chacune transforme un `DocumentState` en un autre, sans rien muter.

Une commande est une valeur (`frozen`) : `apply(state)` renvoie le NOUVEL état ou lève `DocumentError` ; elle ne touche ni disque ni
fenêtre. Annuler n'a besoin d'aucune commande inverse : le document garde les états. `merge_key` (facultatif) regroupe les
commandes d'un même geste (glisser un objet) en une seule entrée d'historique.

Toute valeur est revalidée par les constructeurs de `kut3d.scene` / `kut3d.primitives` (NaN, infini, bornes), les plafonds du
fichier par `check_state`, et le budget de triangles AVANT de générer un maillage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from kut3d.document_state import (
    DocumentError,
    DocumentState,
    LightEntry,
    SceneObject,
    check_name,
    check_state,
    check_triangle_budget,
    require_savable,
    require_storable_material,
    triangle_total,
    with_instance,
)
from kut3d.model import Material, Model
from kut3d.primitive_models import pristine_material, primitive_model
from kut3d.primitives import PrimitiveSpec
from kut3d.scene import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
)


@dataclass(frozen=True)
class Command:
    """Base : un libellé (pour le menu Annuler) et, pour un geste continu, une clé de regroupement."""

    @property
    def label(self) -> str:
        raise NotImplementedError

    @property
    def merge_key(self) -> str | None:
        return None

    def apply(self, state: DocumentState) -> DocumentState:
        raise NotImplementedError


def _with_objects(
    state: DocumentState, objects: tuple[SceneObject, ...], **changes: Any
) -> DocumentState:
    return replace(state, objects=objects, **changes)


def _instance(model: Model, transform: Any, name: str) -> Instance:
    try:
        return Instance(model, transform=transform, name=name)
    except ValueError as exc:
        raise DocumentError(str(exc)[:160]) from exc


@dataclass(frozen=True)
class AddPrimitive(Command):
    """Ajoute une forme de base. `name=None` : le nom par défaut est le genre (`"sphere"`), sans suffixe ni unicité forcée."""

    spec: PrimitiveSpec
    material: Material | None = None
    transform: Any = None
    name: str | None = None

    @property
    def label(self) -> str:
        return f"Ajouter {self.spec.kind}"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.spec, PrimitiveSpec):
            raise DocumentError("spec doit être une forme de kut3d.primitives.")
        name = check_name(self.spec.kind if self.name is None else self.name)
        require_storable_material(self.material)
        check_triangle_budget(
            triangle_total([o.model for o in state.objects]) + self.spec.triangle_count
        )
        try:
            model = primitive_model(self.spec, self.material)
        except ValueError as exc:
            raise DocumentError(str(exc)[:160]) from exc
        matrix = np.eye(4) if self.transform is None else self.transform
        obj = SceneObject(f"o{state.next_id}", _instance(model, matrix, name))
        out = _with_objects(state, (*state.objects, obj), next_id=state.next_id + 1)
        check_state(out)
        return out


@dataclass(frozen=True)
class AddModel(Command):
    """Ajoute un modèle déjà chargé (`load_model`). Un modèle sans fichier source est refusé : il ne s'enregistrerait pas."""

    model: Model
    transform: Any = None
    name: str | None = None

    @property
    def label(self) -> str:
        return "Ajouter un modèle"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.model, Model):
            raise DocumentError("model doit être un kut3d.Model.")
        require_savable(self.model)
        default = (
            self.model.primitive.kind
            if self.model.primitive is not None
            else self.model.source.stem
        )
        name = check_name(default if self.name is None else self.name)
        matrix = np.eye(4) if self.transform is None else self.transform
        obj = SceneObject(f"o{state.next_id}", _instance(self.model, matrix, name))
        out = _with_objects(state, (*state.objects, obj), next_id=state.next_id + 1)
        check_state(out)
        return out


@dataclass(frozen=True)
class DuplicateObject(Command):
    """Copie un objet : même modèle (partagé, pas copié), même pose, même nom (jamais suffixé en silence), nouvel identifiant."""

    object_id: str

    @property
    def label(self) -> str:
        return "Dupliquer"

    def apply(self, state: DocumentState) -> DocumentState:
        source = state.find_object(self.object_id)
        position = state.objects.index(source) + 1
        copy = SceneObject(f"o{state.next_id}", source.instance)
        out = _with_objects(
            state,
            (*state.objects[:position], copy, *state.objects[position:]),
            next_id=state.next_id + 1,
        )
        check_state(out)
        return out


@dataclass(frozen=True)
class RemoveObject(Command):
    object_id: str

    @property
    def label(self) -> str:
        return "Supprimer"

    def apply(self, state: DocumentState) -> DocumentState:
        state.find_object(self.object_id)
        return _with_objects(
            state, tuple(o for o in state.objects if o.id != self.object_id)
        )


@dataclass(frozen=True)
class SetTransform(Command):
    """Pose un objet (matrice 4x4 monde). `gesture=True` : regroupée avec les `SetTransform` du même objet qui la suivent."""

    object_id: str
    transform: Any
    gesture: bool = False

    @property
    def label(self) -> str:
        return "Déplacer"

    @property
    def merge_key(self) -> str | None:
        return f"transform:{self.object_id}" if self.gesture else None

    def apply(self, state: DocumentState) -> DocumentState:
        return with_instance(state, self.object_id, transform=self.transform)


@dataclass(frozen=True)
class Rename(Command):
    object_id: str
    name: str

    @property
    def label(self) -> str:
        return "Renommer"

    def apply(self, state: DocumentState) -> DocumentState:
        return with_instance(state, self.object_id, name=check_name(self.name))


@dataclass(frozen=True)
class SetAnimation(Command):
    """Animation d'un objet : clip (nom, indice ou None), décalage de temps, boucle."""

    object_id: str
    clip: str | int | None = None
    time_offset: float = 0.0
    loop: bool = False

    @property
    def label(self) -> str:
        return "Animation"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.loop, bool):
            raise DocumentError("loop : un booléen est attendu.")
        return with_instance(
            state,
            self.object_id,
            clip=self.clip,
            time_offset=self.time_offset,
            loop=self.loop,
        )


def _editable_shape(
    state: DocumentState, object_id: str
) -> tuple[SceneObject, PrimitiveSpec, Material | None]:
    """L'objet, sa forme et son matériau, après avoir vérifié que c'est une forme de base INTACTE : un modèle importé, ou une
    forme dont on a changé le maillage à la main, n'est pas éditable par ses paramètres (refusé, jamais réécrit)."""
    obj = state.find_object(object_id)
    spec = obj.model.primitive
    if spec is None:
        raise DocumentError(
            "cet objet est un modèle importé : seule sa pose se modifie ici."
        )
    try:
        material = pristine_material(obj.model)
    except ValueError as exc:
        raise DocumentError(str(exc)[:160]) from exc
    return obj, spec, material


def _replace_model(
    state: DocumentState, obj: SceneObject, model: Model
) -> DocumentState:
    # Copie à l'écriture : seul cet objet reçoit le nouveau modèle ; un autre objet qui partageait l'ancien le garde.
    root = obj.model.root_transform
    model = model.with_root_transform(root)
    return with_instance(state, obj.id, model=model)


@dataclass(frozen=True)
class SetPrimitive(Command):
    """Change les paramètres de la forme de base d'un objet (rayon, segments, texte 3D...), en gardant matériau et pose."""

    object_id: str
    spec: PrimitiveSpec

    @property
    def label(self) -> str:
        return "Modifier la forme"

    @property
    def merge_key(self) -> str | None:
        return f"shape:{self.object_id}"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.spec, PrimitiveSpec):
            raise DocumentError("spec doit être une forme de kut3d.primitives.")
        obj, _, material = _editable_shape(state, self.object_id)
        others = [o.model for o in state.objects if o.id != obj.id]
        # Budget vérifié AVANT de générer : le nombre de triangles se calcule par formule.
        check_triangle_budget(triangle_total(others) + self.spec.triangle_count)
        try:
            model = primitive_model(self.spec, material)
        except ValueError as exc:
            raise DocumentError(str(exc)[:160]) from exc
        out = _replace_model(state, obj, model)
        check_state(out)
        return out


@dataclass(frozen=True)
class SetMaterial(Command):
    """Change le matériau d'une forme de base (sans texture : elles ne s'enregistrent pas encore)."""

    object_id: str
    material: Material | None

    @property
    def label(self) -> str:
        return "Modifier le matériau"

    @property
    def merge_key(self) -> str | None:
        return f"material:{self.object_id}"

    def apply(self, state: DocumentState) -> DocumentState:
        require_storable_material(self.material)
        obj, spec, _ = _editable_shape(state, self.object_id)
        try:
            model = primitive_model(spec, self.material)
        except ValueError as exc:
            raise DocumentError(str(exc)[:160]) from exc
        return _replace_model(state, obj, model)


@dataclass(frozen=True)
class SetCamera(Command):
    """Remplace la caméra DE LA SCÈNE (celle du film). La caméra de la fenêtre d'édition n'est pas dans le document."""

    camera: Camera

    @property
    def label(self) -> str:
        return "Caméra"

    @property
    def merge_key(self) -> str | None:
        return "camera"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.camera, Camera):
            raise DocumentError("camera doit être une kut3d.Camera.")
        return replace(state, camera=self.camera)


@dataclass(frozen=True)
class SetBackground(Command):
    """Fond : None (transparent) ou RGBA dans [0, 1]."""

    background: Any

    @property
    def label(self) -> str:
        return "Fond"

    def apply(self, state: DocumentState) -> DocumentState:
        # Validation par la Scene elle-même (mêmes bornes que le fichier).
        try:
            checked = Scene(
                instances=[], lights=[], camera=state.camera, background=self.background
            ).background
        except ValueError as exc:
            raise DocumentError(str(exc)[:160]) from exc
        return replace(state, background=checked)


@dataclass(frozen=True)
class AddLight(Command):
    light: AmbientLight | DirectionalLight | PointLight

    @property
    def label(self) -> str:
        return "Ajouter une lumière"

    def apply(self, state: DocumentState) -> DocumentState:
        if not isinstance(self.light, (AmbientLight, DirectionalLight, PointLight)):
            raise DocumentError("light doit être une lumière de kut3d.scene.")
        entry = LightEntry(f"l{state.next_id}", self.light)
        out = replace(state, lights=(*state.lights, entry), next_id=state.next_id + 1)
        check_state(out)
        return out


@dataclass(frozen=True)
class SetLight(Command):
    light_id: str
    light: AmbientLight | DirectionalLight | PointLight

    @property
    def label(self) -> str:
        return "Modifier la lumière"

    @property
    def merge_key(self) -> str | None:
        return f"light:{self.light_id}"

    def apply(self, state: DocumentState) -> DocumentState:
        state.find_light(self.light_id)
        if not isinstance(self.light, (AmbientLight, DirectionalLight, PointLight)):
            raise DocumentError("light doit être une lumière de kut3d.scene.")
        return replace(
            state,
            lights=tuple(
                LightEntry(e.id, self.light) if e.id == self.light_id else e
                for e in state.lights
            ),
        )


@dataclass(frozen=True)
class RemoveLight(Command):
    light_id: str

    @property
    def label(self) -> str:
        return "Supprimer la lumière"

    def apply(self, state: DocumentState) -> DocumentState:
        state.find_light(self.light_id)
        return replace(
            state, lights=tuple(e for e in state.lights if e.id != self.light_id)
        )


@dataclass(frozen=True)
class Batch(Command):
    """Plusieurs commandes en UNE entrée d'historique (déplacer une sélection). Tout ou rien : la première qui échoue annule le lot."""

    title: str
    commands: Sequence[Command]

    def __post_init__(self) -> None:
        object.__setattr__(self, "commands", tuple(self.commands))

    @property
    def label(self) -> str:
        return self.title

    def apply(self, state: DocumentState) -> DocumentState:
        for command in self.commands:
            state = command.apply(state)
        return state
