"""Lecture d'un fichier `.kut3d` (format 1) : JSON borné et strict -> `Scene`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import numpy as np

from kut3d.errors import SceneFileError, SceneVersionError, short
from kut3d.importers import load_model
from kut3d.model import Model
from kut3d.primitive_models import primitive_model
from kut3d.scene import (
    MAX_INSTANCES,
    MAX_LIGHTS,
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    Light,
    PointLight,
    Scene,
)
from kut3d.scene_io_fields import (
    MAX_MODELS,
    MAX_SCENE_BYTES,
    as_list,
    as_object,
    boolean,
    check_keys,
    floats,
    model_id,
    number,
    optional_number,
    text,
)
from kut3d.primitives import MAX_SCENE_PRIMITIVE_TRIANGLES
from kut3d.scene_io_paths import check_relative_path, resolve_model_path
from kut3d.scene_io_primitives import read_material, read_primitive

SCENE_FORMAT_VERSION = 1  # version écrite tant qu'aucune fonction ne demande mieux : un fichier sans forme reste en version 1
PRIMITIVES_FORMAT_VERSION = 2  # `models[]` peut contenir des formes de base (`primitive`) ; écrite SEULEMENT dans ce cas
TEXT_FORMAT_VERSION = (
    3  # une forme « text » (texte 3D extrudé) y figure ; écrite SEULEMENT dans ce cas
)
MAX_READ_VERSION = TEXT_FORMAT_VERSION  # la plus récente que ce lecteur sache lire
FORMAT_NAME = "kut3d"


def _refuse_constant(name: str) -> NoReturn:
    raise ValueError(f"constante JSON interdite : {name}")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"clé en double : {short(key, 30)}")
        out[key] = value
    return out


def read_document(path: Path) -> dict[str, Any]:
    """Lit et analyse le JSON (taille vérifiée AVANT la lecture) ; ne contrôle pas encore le contenu."""
    try:
        size = path.stat().st_size
    except (OSError, ValueError):  # ValueError : octet nul dans le chemin
        raise SceneFileError(
            f"{path.name} : fichier introuvable ou illisible."
        ) from None
    if not path.is_file():
        raise SceneFileError(f"{path.name} : ce n'est pas un fichier.")
    if size > MAX_SCENE_BYTES:
        raise SceneFileError(
            f"{path.name} : fichier trop volumineux ({size} octets, maximum {MAX_SCENE_BYTES})."
        )
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SCENE_BYTES + 1)
    except OSError as exc:
        raise SceneFileError(
            f"{path.name} : lecture impossible ({short(str(exc), 80)})."
        ) from exc
    if len(raw) > MAX_SCENE_BYTES:
        raise SceneFileError(
            f"{path.name} : fichier trop volumineux (maximum {MAX_SCENE_BYTES} octets)."
        )
    return parse_document(raw, path.name)


def parse_document(raw: bytes, name: str) -> dict[str, Any]:
    """Analyse les octets d'un `.kut3d` (JSON strict) ; `name` ne sert qu'aux messages."""
    try:
        doc = json.loads(
            raw.decode("utf-8-sig"),
            parse_constant=_refuse_constant,
            object_pairs_hook=_no_duplicates,
        )
    except RecursionError:
        raise SceneFileError(f"{name} : JSON trop profondément imbriqué.") from None
    except (
        ValueError
    ) as exc:  # JSONDecodeError et UnicodeDecodeError en sont des sous-classes
        raise SceneFileError(
            f"{name} : JSON invalide ({short(str(exc), 80)})."
        ) from exc
    return as_object(doc, name)


def _camera(value: Any) -> Camera:
    obj = as_object(value, "camera")
    check_keys(
        obj, "camera", ("position", "target"), ("up", "fov_y_deg", "near", "far")
    )
    kwargs: dict[str, Any] = {
        "position": floats(obj["position"], 3, "camera.position"),
        "target": floats(obj["target"], 3, "camera.target"),
    }
    if "up" in obj:
        kwargs["up"] = floats(obj["up"], 3, "camera.up")
    for key in ("fov_y_deg", "near", "far"):
        if key in obj:
            kwargs[key] = number(obj[key], f"camera.{key}")
    return _build("camera", Camera, kwargs)


def _build(where: str, cls: Any, kwargs: dict[str, Any]) -> Any:
    try:
        return cls(**kwargs)
    except ValueError as exc:
        raise SceneFileError(f"{where} : {short(str(exc), 160)}") from exc


def _light(value: Any, index: int) -> Light:
    where = f"lights[{index}]"
    obj = as_object(value, where)
    kind = obj.get("type")
    common = ("color", "intensity")
    if kind == "ambient":
        check_keys(obj, where, ("type",), common)
        cls: Any = AmbientLight
    elif kind == "directional":
        check_keys(obj, where, ("type", "direction"), common)
        cls = DirectionalLight
    elif kind == "point":
        check_keys(obj, where, ("type", "position"), (*common, "range"))
        cls = PointLight
    else:
        raise SceneFileError(
            f"{where} : type de lumière inconnu {short(kind, 30)} (ambient, directional, point)."
        )
    kwargs: dict[str, Any] = {}
    if "direction" in obj:
        kwargs["direction"] = floats(obj["direction"], 3, f"{where}.direction")
    if "position" in obj:
        kwargs["position"] = floats(obj["position"], 3, f"{where}.position")
    if "color" in obj:
        kwargs["color"] = floats(obj["color"], 3, f"{where}.color")
    if "intensity" in obj:
        kwargs["intensity"] = number(obj["intensity"], f"{where}.intensity")
    if "range" in obj:
        kwargs["range"] = optional_number(obj["range"], f"{where}.range")
    return _build(where, cls, kwargs)  # type: ignore[no-any-return]


def _background(value: Any) -> list[float] | None:
    return None if value is None else floats(value, 4, "background")


def _model_matrix(value: Any, where: str) -> Any:
    flat = floats(value, 16, where)
    return np.array(flat, dtype=np.float64).reshape(4, 4)


def _normalize_options(value: Any, where: str) -> dict[str, Any]:
    obj = as_object(value, where)
    check_keys(obj, where, (), ("up_axis", "unit_scale", "target_size", "center"))
    out: dict[str, Any] = {}
    if "up_axis" in obj:
        out["up_axis"] = text(obj["up_axis"], f"{where}.up_axis", 8)
    if "unit_scale" in obj:
        out["unit_scale"] = number(obj["unit_scale"], f"{where}.unit_scale")
    if "target_size" in obj:
        out["target_size"] = optional_number(obj["target_size"], f"{where}.target_size")
    if "center" in obj:
        out["center"] = boolean(obj["center"], f"{where}.center")
    return out


def _model_entry(value: Any, index: int) -> tuple[str, str, dict[str, Any], Any]:
    """Valide une entrée de `models[]` sans toucher au disque : (id, chemin brut, options de `load_model`, matrice)."""
    where = f"models[{index}]"
    obj = as_object(value, where)
    check_keys(obj, where, ("id", "path"), ("normalize", "root_transform"))
    mid = model_id(obj["id"], f"{where}.id")
    raw_path = text(obj["path"], f"{where}.path", 1 << 12)
    options = (
        _normalize_options(obj["normalize"], f"{where}.normalize")
        if "normalize" in obj
        else {}
    )
    matrix = (
        _model_matrix(obj["root_transform"], f"{where}.root_transform")
        if "root_transform" in obj
        else None
    )
    if matrix is not None:
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3, :3])) < 1e-18:
            raise SceneFileError(
                f"{where}.root_transform : matrice dégénérée (déterminant nul)."
            )
        # `root_transform` prime sur `normalize` : on charge sans normalisation puis on remplace la matrice.
        options = {"center": False}
    return mid, raw_path, options, matrix


def _primitive_entry(value: Any, index: int) -> tuple[str, Any, Any, Any]:
    """Valide une entrée `models[]` de forme de base sans rien générer : (id, description, matériau ou None, matrice)."""
    where = f"models[{index}]"
    obj = as_object(value, where)
    check_keys(obj, where, ("id", "primitive"), ("material", "root_transform"))
    mid = model_id(obj["id"], f"{where}.id")
    spec = read_primitive(obj["primitive"], f"{where}.primitive")
    material = (
        read_material(obj["material"], f"{where}.material")
        if "material" in obj
        else None
    )
    matrix = None
    if "root_transform" in obj:
        matrix = _model_matrix(obj["root_transform"], f"{where}.root_transform")
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3, :3])) < 1e-18:
            raise SceneFileError(
                f"{where}.root_transform : matrice dégénérée (déterminant nul)."
            )
    return mid, spec, material, matrix


def _primitive_entries(
    items: list[Any], version: int
) -> dict[int, tuple[str, Any, Any, Any]]:
    """Les entrées de forme de base de `models[]` (indice -> (id, description, matériau, matrice)), toutes validées, avec le
    total de triangles borné par `MAX_SCENE_PRIMITIVE_TRIANGLES` : une entrée hostile ne fait générer aucun maillage."""
    out: dict[int, tuple[str, Any, Any, Any]] = {}
    total = 0
    for i, item in enumerate(items):
        if not (isinstance(item, dict) and "primitive" in item):
            continue
        if version < PRIMITIVES_FORMAT_VERSION:
            raise SceneFileError(
                f"models[{i}] : une forme de base exige le format {PRIMITIVES_FORMAT_VERSION} "
                f"(ce fichier est au format {version})."
            )
        entry = _primitive_entry(item, i)
        if entry[1].kind == "text" and version < TEXT_FORMAT_VERSION:
            raise SceneFileError(
                f"models[{i}] : un texte 3D exige le format {TEXT_FORMAT_VERSION} "
                f"(ce fichier est au format {version})."
            )
        total += entry[1].triangle_count
        if total > MAX_SCENE_PRIMITIVE_TRIANGLES:
            raise SceneFileError(
                f"models[{i}] : trop de triangles pour l'ensemble des formes de base "
                f"(maximum {MAX_SCENE_PRIMITIVE_TRIANGLES})."
            )
        out[i] = entry
    return out


def _load_entry(
    index: int,
    raw_path: str,
    options: dict[str, Any],
    matrix: Any,
    base_dir: Path,
    allow_outside: bool,
) -> Model:
    where = f"models[{index}]"
    file = resolve_model_path(
        base_dir, raw_path, f"{where}.path", allow_outside=allow_outside
    )
    try:
        model = load_model(file, **options)
    except (
        ValueError,
        OSError,
    ) as exc:  # ModelImportError et UnsupportedFormatError sont des ValueError
        raise SceneFileError(f"{where} : {short(str(exc), 200)}") from exc
    if matrix is not None:
        model = model.with_root_transform(matrix)
    return model


def _instance(value: Any, index: int, models: dict[str, Model]) -> Instance:
    where = f"instances[{index}]"
    obj = as_object(value, where)
    check_keys(
        obj, where, ("model",), ("transform", "clip", "time_offset", "loop", "name")
    )
    mid = model_id(obj["model"], f"{where}.model")
    if mid not in models:
        raise SceneFileError(f"{where} : modèle inconnu {short(mid, 40)}.")
    kwargs: dict[str, Any] = {"model": models[mid]}
    if "transform" in obj:
        kwargs["transform"] = _model_matrix(obj["transform"], f"{where}.transform")
    clip = obj.get("clip")
    if isinstance(clip, bool) or not (clip is None or isinstance(clip, (str, int))):
        raise SceneFileError(f"{where}.clip : un nom, un indice ou null est attendu.")
    if isinstance(clip, str):
        text(clip, f"{where}.clip")
    kwargs["clip"] = clip
    if "time_offset" in obj:
        kwargs["time_offset"] = number(obj["time_offset"], f"{where}.time_offset")
    if "loop" in obj:
        kwargs["loop"] = boolean(obj["loop"], f"{where}.loop")
    if "name" in obj:
        kwargs["name"] = text(obj["name"], f"{where}.name")
    return _build(where, Instance, kwargs)  # type: ignore[no-any-return]


def build_scene(
    doc: dict[str, Any],
    base_dir: Path,
    allow_outside: bool,
    *,
    models: dict[str, Model] | None = None,
) -> Scene:
    """Valide l'enveloppe puis construit la `Scene` ; chaque modèle est chargé une seule fois.

    `models` (écrivain) : modèles déjà en mémoire par identifiant ; le fichier n'est alors pas relu mais l'entrée
    et le chemin sont validés par les mêmes règles que pour un fichier."""
    return build_scene_with_models(doc, base_dir, allow_outside, models=models)[0]


def build_scene_with_models(
    doc: dict[str, Any],
    base_dir: Path,
    allow_outside: bool,
    *,
    models: dict[str, Model] | None = None,
) -> tuple[Scene, tuple[Model, ...]]:
    """Comme `build_scene`, avec en plus TOUS les modèles déclarés (même sans instance), dans l'ordre du fichier."""
    if doc.get("format") != FORMAT_NAME:
        raise SceneFileError(
            f"format inconnu : {short(doc.get('format'), 30)} (« {FORMAT_NAME} » attendu)."
        )
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SceneFileError(f"version invalide : {short(version, 30)}.")
    if version > MAX_READ_VERSION:
        raise SceneVersionError(
            f"ce fichier est au format {version}, plus récent que le format {MAX_READ_VERSION} "
            "connu ici : mettez Kut-Studio à jour."
        )
    check_keys(doc, "fichier", ("format", "version", "scene"))
    scene = as_object(doc["scene"], "scene")
    check_keys(
        scene, "scene", ("camera",), ("lights", "background", "models", "instances")
    )
    camera = _camera(scene["camera"])
    lights = [
        _light(v, i)
        for i, v in enumerate(as_list(scene.get("lights", []), "lights", MAX_LIGHTS))
    ]
    background = _background(scene.get("background"))
    model_items = as_list(scene.get("models", []), "models", MAX_MODELS)
    instance_items = as_list(scene.get("instances", []), "instances", MAX_INSTANCES)
    loaded: dict[str, Model] = {}
    shapes = _primitive_entries(
        model_items, version
    )  # validées et comptées AVANT toute génération ou lecture de fichier
    for i, item in enumerate(model_items):
        if i in shapes:
            mid, spec, material, matrix = shapes[i]
            if mid in loaded:
                raise SceneFileError(
                    f"models[{i}] : identifiant {short(mid, 40)} en double."
                )
            if models is None:
                try:
                    model = primitive_model(spec, material)
                except ValueError as exc:  # police introuvable, biseau trop grand, caractère sans glyphe...
                    raise SceneFileError(
                        f"models[{i}] : {short(str(exc), 200)}"
                    ) from exc
                loaded[mid] = (
                    model if matrix is None else model.with_root_transform(matrix)
                )
            else:
                known = models.get(mid)
                if known is None or known.primitive != spec:
                    raise SceneFileError(
                        f"models[{i}] : forme {short(mid, 40)} absente ou différente."
                    )
                loaded[mid] = known
            continue
        mid, raw_path, options, matrix = _model_entry(item, i)
        if mid in loaded:
            raise SceneFileError(
                f"models[{i}] : identifiant {short(mid, 40)} en double."
            )
        if models is None:
            loaded[mid] = _load_entry(
                i, raw_path, options, matrix, base_dir, allow_outside
            )
        else:
            check_relative_path(
                raw_path, f"models[{i}].path", allow_outside=allow_outside
            )
            if mid not in models:
                raise SceneFileError(f"models[{i}] : modèle {short(mid, 40)} absent.")
            loaded[mid] = models[mid]
    instances = [_instance(v, i, loaded) for i, v in enumerate(instance_items)]
    try:
        built = Scene(
            instances=instances, lights=lights, camera=camera, background=background
        )
    except ValueError as exc:
        raise SceneFileError(f"scene : {short(str(exc), 160)}") from exc
    return built, tuple(loaded.values())
