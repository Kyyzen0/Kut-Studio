"""Règles communes des verbes de la bibliothèque (lot 9 de l'extension 0.8.0) : bornes, profondeur de l'arbre de dossiers, médias visés.

Aucun verbe ici : ces fonctions travaillent sur le projet et des identifiants, sans importer la table des verbes.

- **Où vit l'organisation** : dossiers, étiquettes et affectations sont des champs du **projet** (``Project.library_folders`` /
  ``library_tags`` / ``library_assignments``, enregistrés dans le ``.kut`` par ``core.project_io``), pas de la configuration de
  l'utilisateur : des verbes normaux, annulables avec le reste du script.
- **Bornes** : le nombre de dossiers et d'étiquettes d'un projet et la profondeur de l'arbre sont bornés avant de créer ou de déplacer
  (le panneau de la bibliothèque construit l'arbre récursivement). La profondeur se calcule sur l'arbre **résultant** : ancêtres du
  nouveau parent compris, et sous-arbre déplacé compris.
- **Médias** : la bibliothèque ne montre que vidéos, sons et images ; les médias techniques des calques et des sous-titres n'en font pas partie.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..project_model import MediaAsset, Project

MAX_LIBRARY_FOLDERS = 1_000
"""Dossiers d'un projet au-delà desquels un script n'en crée plus."""
MAX_LIBRARY_TAGS = 500
"""Étiquettes d'un projet au-delà desquelles un script n'en crée plus."""
MAX_FOLDER_DEPTH = 32
"""Niveaux de dossiers au plus (la racine n'en est pas un) : le panneau de la bibliothèque parcourt l'arbre récursivement."""
LIBRARY_MEDIA_TYPES = frozenset({"video", "audio", "image"})
"""Genres de médias que la bibliothèque montre (``_refresh_project_library`` écarte les médias « graphic » ; les sous-titres sont ceux des pistes)."""


def folder_depth(project: Project, folder_id: str | None) -> int:
    """Niveau d'un dossier (1 : à la racine ; 0 pour la racine elle-même), en remontant ses ancêtres. Une boucle d'un fichier retouché s'arrête."""
    parents = {folder.id: folder.parent_id for folder in project.library_folders}
    depth, seen, current = 0, set(), folder_id
    while current is not None and current in parents and current not in seen:
        seen.add(current)
        depth += 1
        current = parents[current]
    return depth


def subtree_height(project: Project, folder_id: str) -> int:
    """Niveaux sous ``folder_id`` (0 : sans enfant), cycles ignorés."""
    children: dict[str | None, list[str]] = {}
    for folder in project.library_folders:
        children.setdefault(folder.parent_id, []).append(folder.id)
    best = 0
    stack: list[tuple[str, tuple[str, ...]]] = [(folder_id, (folder_id,))]
    while stack:
        node, chain = stack.pop()
        best = max(best, len(chain) - 1)
        if len(chain) > MAX_FOLDER_DEPTH + 1:
            continue
        for child in children.get(node, ()):
            if child not in chain:
                stack.append((child, chain + (child,)))
    return best


def require_depth(project: Project, new_parent_id: str | None, moved_id: str | None = None) -> None:
    """Refuse, AVANT d'écrire, un arbre de plus de :data:`MAX_FOLDER_DEPTH` niveaux (``moved_id`` : le dossier déplacé, avec tout son sous-arbre)."""
    resulting = folder_depth(project, new_parent_id) + 1 + (subtree_height(project, moved_id) if moved_id else 0)
    if resulting > MAX_FOLDER_DEPTH:
        raise ValueError(f"L'arbre de dossiers aurait {resulting} niveaux : au plus {MAX_FOLDER_DEPTH}.")


def require_folder_room(project: Project) -> None:
    if len(project.library_folders) >= MAX_LIBRARY_FOLDERS:
        raise ValueError(f"Le projet compte déjà {len(project.library_folders)} dossiers : la limite est de {MAX_LIBRARY_FOLDERS}.")


def require_tag_room(project: Project) -> None:
    if len(project.library_tags) >= MAX_LIBRARY_TAGS:
        raise ValueError(f"Le projet compte déjà {len(project.library_tags)} étiquettes : la limite est de {MAX_LIBRARY_TAGS}.")


def require_name_not_an_id(name: str, other_ids: Iterable[str], what: str) -> None:
    """Un script désigne un objet par son identifiant, sinon par son nom exact : un nom qui est l'identifiant d'un autre objet le ferait
    désigner à la place de celui qui le porte (l'identifiant l'emporte)."""
    if name in set(other_ids):
        raise ValueError(f"Le nom {name!r} est l'identifiant d'un autre {what} : un script le désignerait à sa place, choisissez un autre nom.")


def library_asset(asset: MediaAsset) -> MediaAsset:
    """Le média, s'il fait partie de la bibliothèque ; un média technique (calque graphique, sous-titre) n'en est pas un."""
    if asset.media_type not in LIBRARY_MEDIA_TYPES:
        raise ValueError(f"Le média '{asset.id}' est un média technique ('{asset.media_type}') : il ne fait pas partie de la bibliothèque.")
    return asset


__all__ = [
    "LIBRARY_MEDIA_TYPES", "MAX_FOLDER_DEPTH", "MAX_LIBRARY_FOLDERS", "MAX_LIBRARY_TAGS", "folder_depth", "library_asset",
    "require_depth", "require_folder_room", "require_name_not_an_id", "require_tag_room", "subtree_height",
]
