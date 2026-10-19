"""Fichier de scène `.kut3d` : enveloppe JSON `{"format": "kut3d", "version": 1, "scene": {...}}`.

`load_scene` lit et valide strictement (voir `docs/kut3d-scene.md`), `save_scene` écrit de façon atomique et
déterministe, `dependency_files` liste les fichiers dont dépend la scène (pour la copie ou le suivi de projet).
Les modèles sont confinés sous le dossier du `.kut3d` sauf `allow_outside=True`.
"""

from __future__ import annotations

from pathlib import Path

from kut3d.errors import SceneFileError, SceneVersionError
from kut3d.importers._common import finalize_dependencies, material_texture_paths
from kut3d.scene import Scene
from kut3d.scene_io_read import SCENE_FORMAT_VERSION, build_scene, read_document
from kut3d.scene_io_write import scene_document, serialize, write_atomic

__all__ = [
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

    Chaque modèle doit venir d'un fichier source ; `root_transform` est toujours écrite (aller-retour exact).
    Un modèle hors du dossier du fichier est refusé sauf `allow_outside=True`."""
    p = Path(path)
    if not p.parent.is_dir():
        raise SceneFileError(f"{p.name} : le dossier de destination n'existe pas.")
    write_atomic(
        p, serialize(scene_document(scene, p.parent, allow_outside=allow_outside))
    )


def dependency_files(
    path: str | Path, *, allow_outside: bool = False
) -> tuple[Path, ...]:
    """Le `.kut3d`, puis (trié, sans doublon) chaque fichier de modèle, ses MTL / .bin et ses textures :
    chemins absolus résolus."""
    p = Path(path)
    scene = load_scene(p, allow_outside=allow_outside)
    found: list[Path] = []
    for inst in scene.instances:
        model = inst.model
        found.append(model.source)
        found.extend(model.dependencies)
        for mat in model.materials:
            found.extend(material_texture_paths([mat]))
    own = p.resolve()
    return (own, *(f for f in finalize_dependencies(found) if f != own))
