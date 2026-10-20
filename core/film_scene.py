"""Poser une scène 3D dans un film (``Film.place_scene``, ADR-0009, lot B4) : écriture du ``.kut3d`` et copie confinée des modèles.

Un clip « scène 3D » référence un fichier ``.kut3d`` dont les modèles sont **confinés sous son dossier** (``kut3d.save_scene``
refuse un modèle hors de ce dossier). Les modèles d'une ``Scene`` de script viennent de n'importe où : :func:`write_scene` les
**copie**, avec leurs dépendances (MTL, ``.bin``, textures en fichier), dans ``<film>_assets/<nom>_models/<modèle>/`` en gardant
l'arborescence relative qui sert à les résoudre (OBJ -> MTL -> textures, glTF -> ``.bin`` -> images), puis écrit une scène dont
chaque modèle pointe sa copie. ``root_transform`` et les animations ne bougent pas : le modèle copié est le même objet
(``dataclasses.replace`` ne change que ``source`` et ``dependencies``).

Règles : une seule copie par fichier source (deux instances, ou deux ``Model`` du même fichier) ; un dossier par identité
(nom + empreinte du chemin) : deux fichiers de même nom ne se mélangent jamais ; copie bornée (nombre de fichiers, octets) ;
tout se fait dans un dossier temporaire du film, déplacé seulement en cas de succès : **aucun fichier partiel** après une erreur.

Les importeurs de ``kut3d`` confinent déjà MTL, ``.bin`` et textures au dossier du modèle (un chemin absolu ou un ``..`` qui en
sort est ramené au nom du fichier dans ce dossier) : la copie garde donc l'arborescence sous ce dossier. Limite : une géométrie
construite en code (sans fichier source) est refusée.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .film_render import FilmError, short

MAX_COPIED_FILES = 1000
"""Fichiers copiés au plus pour une scène (modèles, MTL, ``.bin``, textures)."""
MAX_COPIED_BYTES = 2 * 1024 ** 3
"""Octets copiés au plus pour une scène (2 Gio)."""
MAX_NAME_COMPONENT = 32
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ScenePlacement:
    """Une scène posée : le ``.kut3d`` écrit, sa position et sa durée (``frames / fps`` exactement), sur la piste de graphiques ``track``."""

    name: str
    path: Path
    start: float
    duration: float
    track: int


def _model_files(model: Any) -> list[Path]:
    """Le fichier source du modèle, ses dépendances et les textures en fichier de ses matériaux (chemins absolus résolus)."""
    from kut3d.importers._common import material_texture_paths

    found = [model.source, *model.dependencies, *material_texture_paths(model.materials)]
    seen: dict[str, Path] = {}
    for item in found:
        resolved = Path(item).resolve()
        seen.setdefault(resolved.as_posix(), resolved)
    return [seen[key] for key in sorted(seen)]


def _folder_key(source: Path) -> str:
    stem = _SAFE.sub("_", source.stem)[:MAX_NAME_COMPONENT] or "model"
    return f"{stem}-{hashlib.blake2b(source.as_posix().encode('utf-8', 'surrogatepass'), digest_size=4).hexdigest()}"


def _copy_model(model: Any, destination: Path, budget: list[int]) -> Any:
    """Copie le modèle et ses fichiers sous ``destination`` ; retourne le modèle pointant la copie. ``budget`` = [fichiers, octets] restants."""
    source = Path(model.source).resolve()
    files = _model_files(model)
    try:
        root = Path(os.path.commonpath([str(item.parent) for item in files]))
    except ValueError:                                           # Windows : fichiers sur des lecteurs différents
        raise FilmError(f"Les fichiers du modèle {short(source.name, 60)} ne sont pas sur un même lecteur.") from None
    copies: dict[Path, Path] = {}
    for item in files:
        try:
            if not item.is_file():
                raise FilmError(f"Fichier du modèle introuvable : {short(item.name, 60)}.")
            size = item.stat().st_size
        except OSError as error:
            raise FilmError(f"Fichier du modèle illisible ({short(item.name, 60)}) : {short(error.strerror or 'erreur', 80)}.") from error
        budget[0] -= 1
        budget[1] -= size
        if budget[0] < 0:
            raise FilmError(f"Trop de fichiers de modèle à copier (maximum {MAX_COPIED_FILES} par scène).")
        if budget[1] < 0:
            raise FilmError(f"Modèles trop volumineux à copier (maximum {MAX_COPIED_BYTES // 1024 ** 3} Gio par scène).")
        target = destination / item.relative_to(root)
        copies[item] = target
    for item, target in copies.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
    new_source = copies[source]
    others = tuple(copies[found] for found in (Path(item).resolve() for item in model.dependencies) if found in copies)
    return dataclasses.replace(model, source=new_source, dependencies=others)


def confined_scene(scene: Any, models_dir: Path) -> Any:
    """Une copie de ``scene`` dont chaque modèle est copié sous ``models_dir`` (un dossier par fichier source)."""
    budget = [MAX_COPIED_FILES, MAX_COPIED_BYTES]
    by_source: dict[Path, Any] = {}
    by_identity: dict[int, Any] = {}
    instances = []
    for instance in scene.instances:
        model = instance.model
        if id(model) not in by_identity:
            if Path(model.source) == Path(""):
                raise FilmError(
                    "Un modèle n'a pas de fichier source (géométrie construite en code) : enregistrez-le d'abord dans un fichier "
                    "OBJ, glTF, PLY ou STL, une scène 3D posée dans la timeline ne contient que des références à des fichiers."
                )
            source = Path(model.source).resolve()
            if source not in by_source:
                by_source[source] = _copy_model(model, models_dir / _folder_key(source), budget)
            copied = by_source[source]
            by_identity[id(model)] = dataclasses.replace(copied, root_transform=model.root_transform)   # chaque Model garde SA matrice
        instances.append(dataclasses.replace(instance, model=by_identity[id(model)]))
    return dataclasses.replace(scene, instances=tuple(instances))


def write_scene(scene: Any, assets_dir: Path, name: str, *, overwrite: bool) -> Path:
    """Écrit ``<assets_dir>/<name>.kut3d`` et ``<assets_dir>/<name>_models/`` ; retourne le chemin du ``.kut3d`` (absolu).

    Tout est préparé dans un dossier temporaire de ``assets_dir`` puis déplacé : en cas d'erreur, rien ne reste.
    """
    import kut3d
    from kut3d.errors import SceneFileError

    from .scene3d import Scene3DError, validate

    final = assets_dir / f"{name}.kut3d"
    final_models = assets_dir / f"{name}_models"
    if not overwrite and (final.exists() or final_models.exists()):
        raise FilmError(f"La scène {name!r} existe déjà sur le disque : choisissez un autre nom ou passez overwrite=True.")
    stage: Path | None = None
    moved = [False, False]                                       # [dossier des modèles, fichier .kut3d] déjà posés à l'emplacement final
    saved: list[tuple[Path, Path]] = []                          # (sauvegarde, emplacement d'origine) de l'ancienne scène
    backup_dir: Path | None = None
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(dir=assets_dir, prefix=f".{name}.", suffix=".tmp"))
        staged = confined_scene(scene, stage / f"{name}_models")
        kut3d.save_scene(staged, stage / f"{name}.kut3d")
        if final.exists() or final_models.exists():              # overwrite=True : on met l'ancienne scène de côté, on ne la détruit pas
            backup_dir = Path(tempfile.mkdtemp(dir=assets_dir, prefix=f".{name}.", suffix=".bak"))
            for original, kept in ((final_models, backup_dir / "models"), (final, backup_dir / "scene.kut3d")):
                if original.exists():
                    os.replace(original, kept)                   # l'emplacement d'origine est libre ensuite : pas de remplacement dossier sur dossier
                    saved.append((kept, original))
        if (stage / f"{name}_models").exists():
            os.replace(stage / f"{name}_models", final_models)
            moved[0] = True
        os.replace(stage / f"{name}.kut3d", final)
        moved[1] = True
        validate(final)                                          # ce que l'export relira doit se lire
    except BaseException as error:                               # y compris KeyboardInterrupt : l'ancien état revient toujours
        _discard(final, final_models, moved)
        _restore(saved, backup_dir)
        if isinstance(error, (SceneFileError, Scene3DError)):
            raise FilmError(f"Scène 3D refusée : {short(str(error), 300)}") from error
        if isinstance(error, OSError):
            raise FilmError(f"Écriture impossible : {short(str(error), 200)}") from error
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    if backup_dir is not None:                                   # point de non-retour unique : la nouvelle scène est installée ET validée
        shutil.rmtree(backup_dir, ignore_errors=True)
    return final.resolve()


def _restore(saved: list[tuple[Path, Path]], backup_dir: Path | None) -> None:
    """Remet l'ancienne scène à sa place puis retire le dossier de sauvegarde ; si la remise échoue, la sauvegarde est conservée et son emplacement indiqué."""
    if backup_dir is None:
        return
    try:
        for kept, original in reversed(saved):
            os.replace(kept, original)
    except OSError as error:
        raise FilmError(
            f"Écriture impossible et ancienne scène non restaurée ({short(str(error), 120)}) : "
            f"ses fichiers sont conservés dans {short(str(backup_dir), 160)}."
        ) from error
    shutil.rmtree(backup_dir, ignore_errors=True)


def _discard(final: Path, final_models: Path, moved: list[bool]) -> None:
    """Retire ce qu'un échec a déjà posé à l'emplacement final (jamais un fichier partiel)."""
    if moved[0]:
        shutil.rmtree(final_models, ignore_errors=True)
    if moved[1]:
        try:
            final.unlink()
        except OSError:
            pass
