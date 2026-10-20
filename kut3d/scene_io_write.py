"""Écriture d'un fichier `.kut3d` (format 1) : déterministe (clés triées, ASCII, `\\n`) et atomique."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from kut3d.errors import SceneFileError, short
from kut3d.model import Model
from kut3d.primitive_models import pristine_material
from kut3d.scene import (
    AmbientLight,
    DirectionalLight,
    Instance,
    Light,
    PointLight,
    Scene,
)
from kut3d.scene_io_fields import MAX_MODELS, MAX_SCENE_BYTES, model_id
from kut3d.scene_io_paths import check_relative_path, filesystem_errors
from kut3d.scene_io_primitives import material_document, primitive_document
from kut3d.scene_io_read import (
    FORMAT_NAME,
    PRIMITIVES_FORMAT_VERSION,
    SCENE_FORMAT_VERSION,
    build_scene,
    parse_document,
)


def _vec(arr: Any) -> list[float]:
    return [float(x) for x in arr]


def _light(light: Light) -> dict[str, Any]:
    if isinstance(light, AmbientLight):
        return {
            "type": "ambient",
            "color": _vec(light.color),
            "intensity": float(light.intensity),
        }
    if isinstance(light, DirectionalLight):
        return {
            "type": "directional",
            "direction": _vec(light.direction),
            "color": _vec(light.color),
            "intensity": float(light.intensity),
        }
    assert isinstance(light, PointLight)
    return {
        "type": "point",
        "position": _vec(light.position),
        "color": _vec(light.color),
        "intensity": float(light.intensity),
        "range": None if light.range is None else float(light.range),
    }


def _relative_path(
    model: Model, base_dir: Path, allow_outside: bool, label: str
) -> str:
    if model.source == Path(""):
        raise SceneFileError(
            f"{label} : ce modèle n'a pas de fichier source (géométrie construite en code) : "
            "enregistrez-le d'abord dans un fichier OBJ, glTF, PLY ou STL."
        )
    with filesystem_errors(label):
        source = model.source.resolve()
        try:
            rel = Path(os.path.relpath(source, base_dir.resolve())).as_posix()
        except ValueError:  # Windows : autre lecteur
            raise SceneFileError(
                f"{label} : le modèle {short(source.name)} est sur un autre lecteur que le .kut3d."
            ) from None
        if rel.split("/")[0] == ".." and not allow_outside:
            raise SceneFileError(
                f"{label} : le modèle {short(source.name)} est hors du dossier du .kut3d "
                "(déplacez le fichier ou passez allow_outside=True)."
            )
        check_relative_path(rel, label, allow_outside=allow_outside)
        if not source.is_file():
            raise SceneFileError(
                f"{label} : le fichier du modèle est introuvable ({short(source.name)})."
            )
    return rel


def _primitive_entry(model: Model, mid: str, label: str) -> dict[str, Any]:
    assert model.primitive is not None
    try:
        material = pristine_material(model)
    except ValueError as exc:
        raise SceneFileError(f"{label} : {short(str(exc), 200)}") from exc
    entry: dict[str, Any] = {
        "id": mid,
        "primitive": primitive_document(model.primitive),
        "root_transform": [float(x) for x in model.root_transform.reshape(-1)],
    }
    document = material_document(material, label)
    if document is not None:
        entry["material"] = document
    return entry


def _unique_id(model: Model, taken: set[str]) -> str:
    base = model.primitive.kind if model.primitive is not None else model.source.stem
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:48] or "model"
    candidate, n = stem, 1
    while candidate in taken:
        n += 1
        candidate = f"{stem}-{n}"
    return model_id(candidate, "identifiant de modèle")


def scene_document(
    scene: Scene, base_dir: Path, *, allow_outside: bool = False
) -> dict[str, Any]:
    """Le dictionnaire JSON de la scène ; `base_dir` = dossier du futur `.kut3d`."""
    return _document_and_models(scene, base_dir, allow_outside)[0]


def checked_bytes(
    scene: Scene, base_dir: Path, *, allow_outside: bool = False
) -> bytes:
    """Les octets du fichier, après relecture de leur forme sérialisée par les validateurs du lecteur
    (mêmes bornes, mêmes messages) : on n'écrit jamais ce que `load_scene` refuserait."""
    doc, models = _document_and_models(scene, base_dir, allow_outside)
    data = serialize(doc)
    build_scene(
        parse_document(data, "scène à écrire"), base_dir, allow_outside, models=models
    )
    return data


def _document_and_models(
    scene: Scene, base_dir: Path, allow_outside: bool
) -> tuple[dict[str, Any], dict[str, Model]]:
    ids: dict[int, str] = {}
    by_id: dict[str, Model] = {}
    models: list[dict[str, Any]] = []
    taken: set[str] = set()
    for inst in scene.instances:
        assert isinstance(inst, Instance)
        model = inst.model
        if id(model) in ids:
            continue
        if len(models) >= MAX_MODELS:
            raise SceneFileError(f"trop de modèles distincts (maximum {MAX_MODELS}).")
        mid = _unique_id(model, taken)
        taken.add(mid)
        ids[id(model)] = mid
        by_id[mid] = model
        label = f"modèle {mid}"
        if model.primitive is not None:
            models.append(_primitive_entry(model, mid, label))
            continue
        models.append(
            {
                "id": mid,
                "path": _relative_path(model, base_dir, allow_outside, label),
                "root_transform": [float(x) for x in model.root_transform.reshape(-1)],
            }
        )
    camera = scene.camera
    doc = {
        "format": FORMAT_NAME,
        # Version 2 SEULEMENT si une forme de base y figure : une scène sans forme s'écrit octet pour octet comme en version 1.
        "version": PRIMITIVES_FORMAT_VERSION
        if any("primitive" in entry for entry in models)
        else SCENE_FORMAT_VERSION,
        "scene": {
            "camera": {
                "position": _vec(camera.position),
                "target": _vec(camera.target),
                "up": _vec(camera.up),
                "fov_y_deg": float(camera.fov_y_deg),
                "near": float(camera.near),
                "far": float(camera.far),
            },
            "lights": [_light(light) for light in scene.lights],
            "background": None if scene.background is None else _vec(scene.background),
            "models": models,
            "instances": [
                {
                    "model": ids[id(inst.model)],
                    "transform": [float(x) for x in inst.transform.reshape(-1)],
                    "clip": inst.clip,
                    "time_offset": float(inst.time_offset),
                    "loop": bool(inst.loop),
                    "name": inst.name,
                }
                for inst in scene.instances
            ],
        },
    }
    return doc, by_id


def _dump(value: Any, level: int, out: list[str]) -> None:
    pad = "  " * (level + 1)
    if isinstance(value, dict) and value:
        out.append("{\n")
        for i, key in enumerate(sorted(value)):
            out.append(f"{pad}{json.dumps(key, ensure_ascii=True)}: ")
            _dump(value[key], level + 1, out)
            out.append(",\n" if i < len(value) - 1 else "\n")
        out.append("  " * level + "}")
    elif (
        isinstance(value, list)
        and value
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    ):
        out.append(
            json.dumps(value, ensure_ascii=True, allow_nan=False)
        )  # tableau de nombres : sur une ligne
    elif isinstance(value, list) and value:
        out.append("[\n")
        for i, item in enumerate(value):
            out.append(pad)
            _dump(item, level + 1, out)
            out.append(",\n" if i < len(value) - 1 else "\n")
        out.append("  " * level + "]")
    else:
        out.append(json.dumps(value, ensure_ascii=True, allow_nan=False))


def serialize(document: dict[str, Any]) -> bytes:
    out: list[str] = []
    try:
        _dump(document, 0, out)
    except (
        ValueError,
        TypeError,
    ) as exc:  # NaN, infini ou type hors JSON glissé après construction
        raise SceneFileError(
            f"valeur non écrivable dans le fichier ({short(str(exc), 80)})."
        ) from exc
    data = ("".join(out) + "\n").encode("utf-8")
    if len(data) > MAX_SCENE_BYTES:
        raise SceneFileError(
            f"scène trop volumineuse ({len(data)} octets, maximum {MAX_SCENE_BYTES})."
        )
    return data


def write_atomic(path: Path, data: bytes) -> None:
    """Fichier temporaire dans le même dossier, puis `os.replace` : jamais de fichier partiel."""
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except (
        OSError,
        ValueError,
    ) as exc:  # ValueError : octet nul ou demi-paire dans le chemin
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise SceneFileError(
            f"{path.name} : écriture impossible ({short(str(exc), 80)})."
        ) from exc
