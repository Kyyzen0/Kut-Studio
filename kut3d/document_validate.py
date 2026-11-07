"""Un état de document est toujours enregistrable : chaque valeur passe par le lecteur `.kut3d` lui-même (ADR-0010).

Plutôt que de recopier les bornes du fichier (matériau dans [0, 1], émission <= 16, lumières, caméra, instance...), on écrit la valeur
comme `save_scene` l'écrirait puis on la relit avec les validateurs de `load_scene` : ce que le lecteur refuserait est refusé à
l'ÉDITION, avec son message, et ne peut jamais rendre `save()` impossible pour une raison de valeur. Seul ce qui dépend de la
DESTINATION (modèle importé hors du dossier du fichier, dossier inexistant) reste refusé à l'écriture.

`validate_changes(avant, après)` ne relit que ce qui est NOUVEAU (par identité) : un geste sur une scène de 2 048 objets ne revalide
pas les 2 047 autres.
"""

from __future__ import annotations

from typing import Any

from kut3d.document_state import DocumentError, DocumentState
from kut3d.errors import SceneFileError, short
from kut3d.scene_io_primitives import (
    material_document,
    read_material,
    read_primitive,
    primitive_document,
)
from kut3d.scene_io_read import _background, _camera, _instance, _light
from kut3d.scene_io_write import _light as _write_light
from kut3d.scene_io_write import _vec


def _refused(where: str, exc: SceneFileError) -> DocumentError:
    return DocumentError(f"{where} : {short(str(exc), 160)}")


def _validate_model(model: Any) -> None:
    if model.primitive is None:
        return
    try:
        read_primitive(primitive_document(model.primitive), "forme")
        document = material_document(
            model.materials[0] if model.materials else None, "matériau"
        )
        if document is not None:
            read_material(document, "matériau")
    except SceneFileError as exc:
        raise _refused("forme ou matériau", exc) from exc


def _validate_instance(obj: Any) -> None:
    inst = obj.instance
    entry = {
        "model": "m",
        "transform": [float(x) for x in inst.transform.reshape(-1)],
        "clip": inst.clip,
        "time_offset": float(inst.time_offset),
        "loop": bool(inst.loop),
        "name": inst.name,
    }
    try:
        _instance(entry, 0, {"m": inst.model})
    except SceneFileError as exc:
        raise _refused("objet", exc) from exc


def validate_changes(before: DocumentState | None, after: DocumentState) -> None:
    """Refuse (`DocumentError`) toute valeur NOUVELLE de `after` que le lecteur de fichier refuserait."""
    old_instances = (
        set() if before is None else {id(o.instance) for o in before.objects}
    )
    old_models = set() if before is None else {id(o.model) for o in before.objects}
    old_lights = set() if before is None else {id(e.light) for e in before.lights}
    checked: set[int] = set()
    for obj in after.objects:
        if id(obj.model) not in old_models and id(obj.model) not in checked:
            checked.add(id(obj.model))
            _validate_model(obj.model)
        if id(obj.instance) not in old_instances:
            _validate_instance(obj)
    for entry in after.lights:
        if id(entry.light) not in old_lights:
            try:
                _light(_write_light(entry.light), 0)
            except SceneFileError as exc:
                raise _refused("lumière", exc) from exc
    if before is None or after.camera is not before.camera:
        cam = after.camera
        try:
            _camera(
                {
                    "position": _vec(cam.position),
                    "target": _vec(cam.target),
                    "up": _vec(cam.up),
                    "fov_y_deg": float(cam.fov_y_deg),
                    "near": float(cam.near),
                    "far": float(cam.far),
                }
            )
        except SceneFileError as exc:
            raise _refused("caméra", exc) from exc
    if (
        before is None or after.background is not before.background
    ) and after.background is not None:
        try:
            _background(_vec(after.background))
        except SceneFileError as exc:
            raise _refused("fond", exc) from exc
