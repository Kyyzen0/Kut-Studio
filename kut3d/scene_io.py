"""Fichier de scène `.kut3d` : enveloppe JSON `{"format": "kut3d", "version": 1, "scene": {...}}`.

`load_scene` lit et valide strictement (voir `docs/kut3d-scene.md`), `save_scene` écrit de façon atomique et
déterministe, `dependency_files` liste les fichiers dont dépend la scène (pour la copie ou le suivi de projet).
Les modèles sont confinés sous le dossier du `.kut3d` sauf `allow_outside=True`.
"""

from __future__ import annotations

from pathlib import Path

from kut3d.errors import SceneFileError, SceneVersionError, short
from kut3d.importers._common import finalize_dependencies, material_texture_paths
from kut3d.scene import Scene
from kut3d.scene_io_paths import filesystem_errors
from kut3d.scene_io_read import (
    PRIMITIVES_FORMAT_VERSION,
    SCENE_FORMAT_VERSION,
    build_scene,
    build_scene_with_models,
    read_document,
)
from kut3d.scene_io_write import checked_bytes, write_atomic

__all__ = [
    "PRIMITIVES_FORMAT_VERSION",
    "SCENE_FORMAT_VERSION",
    "SceneFileError",
    "SceneVersionError",
    "dependency_files",
    "load_scene",
    "save_scene",
]


def load_scene(path: str | Path, *, allow_outside: bool = False) -> Scene:
    """Charge un `.kut3d`. Lève `SceneFileError` (`SceneVersionError` si le format est plus récent)."""
    p = Path(path)
    return build_scene(read_document(p), p.parent, allow_outside)


def save_scene(scene: Scene, path: str | Path, *, allow_outside: bool = False) -> None:
    """Écrit la scène (clés triées, ASCII, `\\n`, UTF-8) de façon atomique.

    Chaque modèle doit venir d'un fichier source, sauf les formes de base (`primitive_model`, enregistrées par leurs
    paramètres : le fichier passe alors en version 2) ; `root_transform` est toujours écrite (aller-retour exact).
    Un modèle hors du dossier du fichier est refusé sauf `allow_outside=True`. Le contenu est relu par les validateurs
    du lecteur AVANT de remplacer la destination : ce qui serait refusé à la lecture l'est à l'écriture."""
    p = Path(path)
    with filesystem_errors(short(p.name)):
        is_dir = p.parent.is_dir()
    if not is_dir:
        raise SceneFileError(
            f"{short(p.name)} : le dossier de destination n'existe pas."
        )
    write_atomic(p, checked_bytes(scene, p.parent, allow_outside=allow_outside))


def dependency_files(
    path: str | Path, *, allow_outside: bool = False
) -> tuple[Path, ...]:
    """Le `.kut3d`, puis (trié, sans doublon) chaque fichier de modèle déclaré, ses MTL / .bin et ses textures :
    chemins absolus résolus."""
    p = Path(path)
    _, models = build_scene_with_models(
        read_document(p), p.parent, allow_outside
    )  # tous les modèles déclarés, même sans instance : la lecture les exige
    found: list[Path] = []
    for model in models:
        if model.primitive is not None:  # une forme de base n'a aucun fichier
            continue
        found.append(model.source)
        found.extend(model.dependencies)
        for mat in model.materials:
            found.extend(material_texture_paths([mat]))
    with filesystem_errors(short(p.name)):
        own = p.resolve()
    return (own, *(f for f in finalize_dependencies(found) if f != own))
