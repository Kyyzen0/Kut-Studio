"""Formes de base et matériaux dans un fichier `.kut3d` (format 2) : lecture bornée, écriture déterministe.

Une entrée de `models[]` est soit un fichier (`path`), soit une forme paramétrique (`primitive`), jamais les deux ::

    {"id": "sol", "primitive": {"kind": "plane", "width": 10.0, "depth": 10.0, "width_segments": 1, "depth_segments": 1},
     "material": {"base_color": [0.8, 0.2, 0.2, 1.0], "roughness": 0.5}, "root_transform": [...]}

Aucun sommet n'est lu ni écrit : la géométrie est générée après validation des paramètres, et le total de triangles des formes
d'un fichier est borné AVANT toute génération (`MAX_SCENE_PRIMITIVE_TRIANGLES`).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from kut3d.errors import SceneFileError, short
from kut3d.model import ALPHA_MODES, Material
from kut3d.primitives import PRIMITIVE_KINDS, PrimitiveSpec
from kut3d.scene_io_fields import (
    as_object,
    boolean,
    check_keys,
    floats,
    integer,
    number,
    text,
)

MAX_EMISSIVE = 16.0  # composante émissive : au-delà, aucun usage raisonnable (le rendu écrête de toute façon)
_MATERIAL_KEYS = (
    "name",
    "base_color",
    "metallic",
    "roughness",
    "emissive",
    "alpha_mode",
    "alpha_cutoff",
    "double_sided",
)
_FIELD_READERS = {"bool": boolean, "int": integer, "float": number}
_DEFAULT_TEXT_LIMIT = 64


def _field_kinds(cls: type[PrimitiveSpec]) -> dict[str, str]:
    """Nom du champ -> genre de valeur JSON, d'après le défaut de la description."""
    probe = cls()
    return {
        name: "bool"
        if isinstance(value, bool)
        else "str"
        if isinstance(value, str)
        else "int"
        if isinstance(value, int)
        else "float"
        for name, value in probe.params().items()
    }


def _unit(value: float, where: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise SceneFileError(
            f"{where} : un nombre dans [0, 1] est attendu (reçu {value:g})."
        )
    return value


def read_material(value: Any, where: str) -> Material:
    obj = as_object(value, where)
    check_keys(obj, where, (), _MATERIAL_KEYS)
    kwargs: dict[str, Any] = {}
    if "name" in obj:
        kwargs["name"] = text(obj["name"], f"{where}.name")
    if "base_color" in obj:
        color = floats(obj["base_color"], 4, f"{where}.base_color")
        kwargs["base_color"] = [_unit(c, f"{where}.base_color") for c in color]
    for key in ("metallic", "roughness", "alpha_cutoff"):
        if key in obj:
            kwargs[key] = _unit(number(obj[key], f"{where}.{key}"), f"{where}.{key}")
    if "emissive" in obj:
        emissive = floats(obj["emissive"], 3, f"{where}.emissive")
        if any(not 0.0 <= c <= MAX_EMISSIVE for c in emissive):
            raise SceneFileError(
                f"{where}.emissive : composantes dans [0, {MAX_EMISSIVE:g}] attendues."
            )
        kwargs["emissive"] = emissive
    if "alpha_mode" in obj:
        mode = text(obj["alpha_mode"], f"{where}.alpha_mode", 16)
        if mode not in ALPHA_MODES:
            raise SceneFileError(
                f"{where}.alpha_mode : {short(mode, 20)} inconnu (attendu {', '.join(ALPHA_MODES)})."
            )
        kwargs["alpha_mode"] = mode
    if "double_sided" in obj:
        kwargs["double_sided"] = boolean(obj["double_sided"], f"{where}.double_sided")
    return Material(**kwargs)


def read_primitive(value: Any, where: str) -> PrimitiveSpec:
    obj = as_object(value, where)
    kind = obj.get("kind")
    cls = PRIMITIVE_KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise SceneFileError(
            f"{where}.kind : genre de forme inconnu {short(kind, 30)} (attendu : {', '.join(PRIMITIVE_KINDS)})."
        )
    kinds = _field_kinds(cls)
    check_keys(obj, where, ("kind",), tuple(kinds))
    kwargs = {
        name: (
            text(
                obj[name],
                f"{where}.{name}",
                cls.str_limits.get(name, _DEFAULT_TEXT_LIMIT),
            )
            if kinds[name] == "str"
            else _FIELD_READERS[kinds[name]](obj[name], f"{where}.{name}")
        )
        for name in kinds
        if name in obj
    }
    try:
        return cls(**kwargs)
    except ValueError as exc:
        raise SceneFileError(f"{where} : {short(str(exc), 160)}") from exc


def material_document(material: Material | None, where: str) -> dict[str, Any] | None:
    """Le matériau tel qu'écrit, ou None. Une texture n'est pas (encore) enregistrable : refusée, jamais perdue en silence."""
    if material is None:
        return None
    textures = (
        material.base_color_texture,
        material.normal_texture,
        material.metallic_roughness_texture,
        material.emissive_texture,
        material.occlusion_texture,
    )
    if any(t is not None for t in textures):
        raise SceneFileError(
            f"{where} : un matériau avec texture n'est pas encore enregistrable dans un .kut3d (couleur, métal, rugosité, "
            "émission et transparence seulement)."
        )
    return {
        "name": material.name,
        "base_color": [float(x) for x in np.asarray(material.base_color).reshape(-1)],
        "metallic": float(material.metallic),
        "roughness": float(material.roughness),
        "emissive": [float(x) for x in np.asarray(material.emissive).reshape(-1)],
        "alpha_mode": material.alpha_mode,
        "alpha_cutoff": float(material.alpha_cutoff),
        "double_sided": bool(material.double_sided),
    }


def primitive_document(spec: PrimitiveSpec) -> dict[str, Any]:
    return {"kind": spec.kind, **spec.params()}
