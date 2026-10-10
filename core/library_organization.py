"""Organisation avancée de la bibliothèque de médias (tâche 25).

Ce module regroupe :

- les modèles de données (``LibraryFolder``, ``LibraryTag``,
  ``AssetAssignment``) qui complètent :class:`~core.project_model.Project`
  pour organiser la bibliothèque par dossiers et tags ;
- un service :class:`LibraryOrganization` qui applique les opérations
  CRUD (création, renommage, suppression, déplacement) avec des
  garde-fous (anti-cycle, anti-doublon, libellés vides...) ;
- un analyseur d'utilisation :class:`MediaUsageAnalyzer` qui compte
  les occurrences d'un média sur la timeline et détecte les fichiers
  manquants (chemin vide, fichier introuvable) ;
- des helpers de filtrage (par type, par usage, par statut) utilisés
  par la bibliothèque et les tests.

Aucune dépendance à PySide6 : tout reste compatible avec un usage
scripté et les tests purs. La persistance est gérée par
:mod:`core.project_io` qui sérialise les nouveaux champs du projet
(format ``.kut`` v11).

Règles métier principales :

- les dossiers forment un arbre (chaque dossier a un parent optionnel) ;
- un média est rangé dans **un** dossier (ou la racine si ``folder_id``
  est ``None``) et porte une liste de tags ;
- un média sans chemin (``path == ""``) ou dont le fichier n'existe
  plus sur disque est considéré comme **manquant** : le fichier
  source a disparu et l'utilisateur doit le relier ;
- les opérations de mutation prennent un ``project`` et opèrent
  *en place* sur ses champs ``library_folders``,
  ``library_tags`` et ``library_assignments``. L'undo/redo se fait
  naturellement via :class:`~core.edit_history.ProjectHistory` qui
  capture une copie profonde du projet entier.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

from .media_describe import describe_media
from .media_probe import MediaProbeError, probe_media
from .project_model import MediaAsset, Project


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


# Couleurs prédéfinies proposées dans le menu de tag et de dossier. Une
# application peut continuer à utiliser une couleur hexadécimale
# arbitraire : la palette est juste un confort UX.
TAG_COLOR_PALETTE: tuple[str, ...] = (
    "#3498db",  # bleu
    "#e67e22",  # orange
    "#2ecc71",  # vert
    "#9b59b6",  # violet
    "#e74c3c",  # rouge
    "#f1c40f",  # jaune
    "#1abc9c",  # turquoise
    "#95a5a6",  # gris
)


FOLDER_COLOR_PALETTE: tuple[str, ...] = (
    "#3498db",
    "#e67e22",
    "#2ecc71",
    "#9b59b6",
    "#e74c3c",
    "#f1c40f",
    "#1abc9c",
)


DEFAULT_TAG_COLOR: str = "#3498db"
"""Couleur par défaut d'un nouveau tag."""


# Longueur max d'un libellé de dossier ou de tag : on préfère un nom
# court, lisible dans une colonne étroite.
MAX_NAME_LENGTH: int = 48


# ``Folder.id`` ou ``Tag.id`` valides : caractères ASCII imprimables
# sauf les séparateurs et espaces réservés. Les valeurs vides sont
# également rejetées.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]+$")


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


_TYPE_NAMES = {"audio": "un fichier audio", "video": "une vidéo"}


def relink_differences(before: MediaAsset, after: MediaAsset) -> list[str]:
    """Ce qui a changé entre l'ancien et le nouveau fichier d'un média relié (phrases lisibles).

    Durée, résolution, cadence, présence d'audio, et les repères de synchronisation (timecode, heure BWF).
    """
    changes: list[str] = []
    if abs(before.duration - after.duration) > 0.04:
        changes.append(f"durée {before.duration:.2f} s → {after.duration:.2f} s")
    if (before.width, before.height) != (after.width, after.height):
        changes.append(f"résolution {before.width}×{before.height} → {after.width}×{after.height}")
    if abs(before.fps - after.fps) > 0.01:
        changes.append(f"cadence {before.fps:g} → {after.fps:g} i/s")
    if before.has_audio != after.has_audio:
        changes.append("avec audio" if after.has_audio else "sans audio")
    # Le timecode (et l'heure d'un fichier BWF) fonde la synchronisation Multicam : un fichier relié qui le perd ou le
    # change (copie transcodée, autre prise) ne se synchronise plus comme l'original.
    old, new = dict(describe_media(before)), dict(describe_media(after))
    for key, label in (("timecode", "timecode"), ("time_reference", "heure BWF")):
        if old.get(key) != new.get(key):
            changes.append(f"{label} {old.get(key, 'aucun')} → {new.get(key, 'aucun')}")
    return changes


class LibraryError(ValueError):
    """Erreur métier relative à l'organisation de la bibliothèque.

    Toutes les fonctions de :class:`LibraryOrganization` lèvent cette
    exception (ou une sous-classe) lorsqu'une opération est refusée :
    nom vide, identifiant inconnu, dossier parent introuvable, cycle...
    Les messages restent en français pour rester cohérents avec le
    reste du projet.
    """


class DuplicateLibraryItemError(LibraryError):
    """Deux dossiers ou deux tags portent le même identifiant."""


class UnknownLibraryItemError(LibraryError):
    """L'identifiant fourni ne correspond à aucun dossier / tag existant."""


class LibraryCycleError(LibraryError):
    """L'opération créerait un cycle dans l'arborescence des dossiers."""


class LibraryNameError(LibraryError):
    """Le nom fourni est vide ou trop long."""


# ---------------------------------------------------------------------------
# Modèles de données
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Génère un identifiant court, lisible et unique.

    On utilise un préfixe déterministe pour différencier dossiers /
    tags dans les logs et l'inspection visuelle (``fld-...``,
    ``tag-...``). Le suffixe ``uuid`` évite toute collision même en
    cas d'appels concurrents.
    """
    return f"lib-{uuid.uuid4().hex[:12]}"


@dataclass
class LibraryFolder:
    """Un dossier personnalisable de la bibliothèque.

    Les dossiers forment un arbre (libre, non binaire) : chaque dossier
    pointe vers un parent via ``parent_id``. La racine est identifiée
    par ``parent_id is None``. Le nom est affiché dans la colonne de
    gauche et peut être coloré via ``color`` (chaîne hexadécimale
    ``#RRGGBB``). Une couleur vide (``""``) est traitée comme « pas de
    couleur personnalisée ».

    Attributes:
        id: Identifiant unique et stable du dossier.
        name: Libellé humain, affiché dans l'arborescence.
        parent_id: ``None`` pour un dossier racine, sinon l'identifiant
            du dossier parent.
        color: Couleur hexadécimale (``#RRGGBB``) ou chaîne vide.
    """

    id: str
    name: str
    parent_id: str | None = None
    color: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise LibraryError("L'identifiant d'un dossier ne peut pas être vide.")
        if not _ID_PATTERN.match(self.id):
            raise LibraryError(
                f"Identifiant de dossier invalide : {self.id!r}."
            )
        if not isinstance(self.name, str):
            raise LibraryError("Le nom du dossier doit être une chaîne.")
        if not self.name.strip():
            raise LibraryError("Le nom du dossier ne peut pas être vide.")
        if len(self.name) > MAX_NAME_LENGTH:
            raise LibraryError(
                f"Le nom du dossier est trop long "
                f"({len(self.name)} > {MAX_NAME_LENGTH})."
            )
        if self.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise LibraryError(
                f"Couleur de dossier invalide : {self.color!r}. "
                "Attendu : '#RRGGBB'."
            )
        if self.parent_id == self.id:
            raise LibraryError(
                "Un dossier ne peut pas être son propre parent."
            )


@dataclass
class LibraryTag:
    """Un tag (couleur / étiquette) applicable à un média.

    Les tags sont indépendants des dossiers : un média peut avoir
    plusieurs tags, ou aucun, qu'il soit à la racine ou dans un
    dossier. La couleur est obligatoire pour le rendu visuel (pastille
    colorée à côté du nom) ; un nom vide est rejeté à la construction.

    Attributes:
        id: Identifiant unique et stable du tag.
        name: Libellé humain (court).
        color: Couleur hexadécimale (``#RRGGBB``).
    """

    id: str
    name: str
    color: str = DEFAULT_TAG_COLOR

    def __post_init__(self) -> None:
        if not self.id:
            raise LibraryError("L'identifiant d'un tag ne peut pas être vide.")
        if not _ID_PATTERN.match(self.id):
            raise LibraryError(f"Identifiant de tag invalide : {self.id!r}.")
        if not isinstance(self.name, str):
            raise LibraryError("Le nom du tag doit être une chaîne.")
        if not self.name.strip():
            raise LibraryError("Le nom du tag ne peut pas être vide.")
        if len(self.name) > MAX_NAME_LENGTH:
            raise LibraryError(
                f"Le nom du tag est trop long "
                f"({len(self.name)} > {MAX_NAME_LENGTH})."
            )
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise LibraryError(
                f"Couleur de tag invalide : {self.color!r}. "
                "Attendu : '#RRGGBB'."
            )


@dataclass
class AssetAssignment:
    """Position et tags d'un média dans la bibliothèque.

    Représente l'organisation *logique* d'un média : dans quel dossier
    il est rangé, et quels tags lui sont appliqués. Le ``folder_id``
    vaut ``None`` quand le média est à la racine (utile quand aucun
    dossier personnalisé n'a encore été créé).

    Attributes:
        asset_id: Identifiant du :class:`~core.project_model.MediaAsset`
            concerné.
        folder_id: Identifiant du dossier parent ou ``None`` (racine).
        tag_ids: Liste des identifiants de tags appliqués. L'ordre
            n'est pas significatif mais est conservé pour l'affichage.
    """

    asset_id: str
    folder_id: str | None = None
    tag_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service d'organisation
# ---------------------------------------------------------------------------


class LibraryOrganization:
    """Service de gestion de l'organisation de la bibliothèque.

    Toutes les opérations de mutation modifient le :class:`Project`
    passé en argument *en place*. Le service garde un état implicite :
    les listes ``library_folders``, ``library_tags`` et le mapping
    ``library_assignments`` du projet sont la source de vérité.

    Le service expose également des méthodes de lecture (liste des
    dossiers d'un parent, recherche d'un tag par nom...) consommées
    par l'interface et par les tests.
    """

    def __init__(self, project: Project) -> None:
        if project is None:
            raise LibraryError("Un projet est requis pour organiser la bibliothèque.")
        self.project = project
        # Garde-fous : si un ancien projet (v10 ou antérieur) ne porte
        # pas les nouveaux champs, on les initialise avec des valeurs
        # neutres plutôt que de laisser une AttributeError remonter.
        if not hasattr(project, "library_folders") or project.library_folders is None:
            project.library_folders = []
        if not hasattr(project, "library_tags") or project.library_tags is None:
            project.library_tags = []
        if not hasattr(project, "library_assignments") or project.library_assignments is None:
            project.library_assignments = {}

    # ------------------------------------------------------------------
    # Dossiers
    # ------------------------------------------------------------------

    def list_folders(self, parent_id: str | None = None) -> list[LibraryFolder]:
        """Retourne les dossiers dont le parent est ``parent_id``.

        ``parent_id=None`` filtre les dossiers racines. L'ordre de la
        liste est déterministe : tri par ``name`` (insensible à la
        casse) puis par ``id`` pour briser les égalités.
        """
        folders = [f for f in self.project.library_folders if f.parent_id == parent_id]
        folders.sort(key=lambda f: (f.name.lower(), f.id))
        return folders

    def all_folders(self) -> list[LibraryFolder]:
        """Retourne tous les dossiers du projet, triés par ``name``."""
        folders = list(self.project.library_folders)
        folders.sort(key=lambda f: (f.name.lower(), f.id))
        return folders

    def get_folder(self, folder_id: str) -> LibraryFolder | None:
        """Recherche un dossier par identifiant ; ``None`` si absent."""
        for folder in self.project.library_folders:
            if folder.id == folder_id:
                return folder
        return None

    def create_folder(
        self,
        name: str,
        *,
        parent_id: str | None = None,
        color: str = "",
        folder_id: str | None = None,
    ) -> LibraryFolder:
        """Crée un dossier et le retourne.

        Args:
            name: Libellé humain (non vide, longueur limitée).
            parent_id: Dossier parent, ``None`` pour la racine.
            color: Couleur hexadécimale ou chaîne vide.
            folder_id: Identifiant explicite (test / import). Un
                identifiant généré est utilisé si ``None``.

        Raises:
            LibraryNameError: nom vide ou trop long.
            UnknownLibraryItemError: ``parent_id`` ne correspond à
                aucun dossier existant.
            DuplicateLibraryItemError: ``folder_id`` déjà utilisé.
        """
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise LibraryNameError("Le nom du dossier ne peut pas être vide.")
        if len(cleaned_name) > MAX_NAME_LENGTH:
            raise LibraryNameError(
                f"Le nom du dossier est trop long "
                f"({len(cleaned_name)} > {MAX_NAME_LENGTH})."
            )
        if parent_id is not None and self.get_folder(parent_id) is None:
            raise UnknownLibraryItemError(
                f"Dossier parent '{parent_id}' introuvable."
            )
        new_id = folder_id or _new_id()
        if self.get_folder(new_id) is not None:
            raise DuplicateLibraryItemError(
                f"Un dossier avec l'identifiant '{new_id}' existe déjà."
            )
        folder = LibraryFolder(
            id=new_id,
            name=cleaned_name,
            parent_id=parent_id,
            color=color or "",
        )
        self.project.library_folders.append(folder)
        return folder

    def rename_folder(self, folder_id: str, new_name: str) -> LibraryFolder:
        """Renomme un dossier. Lève ``LibraryNameError`` si invalide."""
        folder = self.get_folder(folder_id)
        if folder is None:
            raise UnknownLibraryItemError(f"Dossier '{folder_id}' introuvable.")
        cleaned = (new_name or "").strip()
        if not cleaned:
            raise LibraryNameError("Le nom du dossier ne peut pas être vide.")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise LibraryNameError(
                f"Le nom du dossier est trop long "
                f"({len(cleaned)} > {MAX_NAME_LENGTH})."
            )
        folder.name = cleaned
        return folder

    def recolor_folder(self, folder_id: str, color: str) -> LibraryFolder:
        """Change la couleur d'un dossier (chaîne vide acceptée)."""
        folder = self.get_folder(folder_id)
        if folder is None:
            raise UnknownLibraryItemError(f"Dossier '{folder_id}' introuvable.")
        if color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise LibraryError(
                f"Couleur de dossier invalide : {color!r}. "
                "Attendu : '#RRGGBB' ou chaîne vide."
            )
        folder.color = color or ""
        return folder

    def move_folder(self, folder_id: str, new_parent_id: str | None) -> LibraryFolder:
        """Déplace un dossier sous un nouveau parent.

        Args:
            folder_id: Dossier à déplacer.
            new_parent_id: Nouveau parent (``None`` pour la racine) ou
                identifiant d'un dossier existant.

        Raises:
            UnknownLibraryItemError: dossier ou parent introuvable.
            LibraryCycleError: si ``new_parent_id`` est un descendant
                de ``folder_id`` (le déplacement créerait un cycle).
        """
        folder = self.get_folder(folder_id)
        if folder is None:
            raise UnknownLibraryItemError(f"Dossier '{folder_id}' introuvable.")
        if new_parent_id is not None:
            if self.get_folder(new_parent_id) is None:
                raise UnknownLibraryItemError(
                    f"Dossier parent '{new_parent_id}' introuvable."
                )
            if self._is_descendant(new_parent_id, folder_id):
                raise LibraryCycleError(
                    "Impossible de déplacer un dossier dans son propre descendant."
                )
            if new_parent_id == folder_id:
                raise LibraryCycleError(
                    "Un dossier ne peut pas être son propre parent."
                )
        folder.parent_id = new_parent_id
        return folder

    def delete_folder(self, folder_id: str, *, cascade: bool = True) -> None:
        """Supprime un dossier et (par défaut) ses descendants.

        Args:
            folder_id: Dossier à supprimer.
            cascade: Si ``True``, supprime aussi les descendants
                plutôt que de les rattacher à la racine. Si ``False``,
                les descendants deviennent des dossiers racine.

        Side Effects:
            Tous les médias rangés dans le dossier (et ses
            descendants si ``cascade=True``) sont ramenés à la racine
            (``folder_id=None``) et restent référencés par le projet.
            Les tags ne sont pas affectés.
        """
        folder = self.get_folder(folder_id)
        if folder is None:
            raise UnknownLibraryItemError(f"Dossier '{folder_id}' introuvable.")
        if cascade:
            # Récupère ``folder_id`` + tous ses descendants.
            to_remove_ids = {folder_id}
            changed = True
            while changed:
                changed = False
                for candidate in list(self.project.library_folders):
                    if candidate.parent_id in to_remove_ids and candidate.id not in to_remove_ids:
                        to_remove_ids.add(candidate.id)
                        changed = True
        else:
            to_remove_ids = {folder_id}
            for candidate in list(self.project.library_folders):
                if (
                    candidate.parent_id == folder_id
                    and candidate.id not in to_remove_ids
                ):
                    candidate.parent_id = None

        # Les médias qui pointaient vers un dossier supprimé reviennent
        # à la racine (organisation logique sans perte).
        for assignment in self.project.library_assignments.values():
            if assignment.folder_id in to_remove_ids:
                assignment.folder_id = None

        self.project.library_folders = [
            f for f in self.project.library_folders if f.id not in to_remove_ids
        ]

    def _is_descendant(self, candidate_id: str, ancestor_id: str) -> bool:
        """``True`` si ``candidate_id`` est un descendant de ``ancestor_id``.

        On parcourt l'arbre en remontant les parents depuis
        ``candidate_id``. Si on croise ``ancestor_id``, alors
        ``candidate_id`` est plus profond que ``ancestor_id`` et donc
        en est un descendant.
        """
        current_id: str | None = candidate_id
        seen: set[str] = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            if current_id == ancestor_id:
                return True
            folder = self.get_folder(current_id)
            if folder is None:
                return False
            current_id = folder.parent_id
        return False

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def list_tags(self) -> list[LibraryTag]:
        """Retourne tous les tags triés par ``name``."""
        tags = list(self.project.library_tags)
        tags.sort(key=lambda t: (t.name.lower(), t.id))
        return tags

    def get_tag(self, tag_id: str) -> LibraryTag | None:
        """Recherche un tag par identifiant ; ``None`` si absent."""
        for tag in self.project.library_tags:
            if tag.id == tag_id:
                return tag
        return None

    def create_tag(
        self,
        name: str,
        *,
        color: str = DEFAULT_TAG_COLOR,
        tag_id: str | None = None,
    ) -> LibraryTag:
        """Crée un tag et le retourne."""
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise LibraryNameError("Le nom du tag ne peut pas être vide.")
        if len(cleaned_name) > MAX_NAME_LENGTH:
            raise LibraryNameError(
                f"Le nom du tag est trop long "
                f"({len(cleaned_name)} > {MAX_NAME_LENGTH})."
            )
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color or ""):
            raise LibraryError(
                f"Couleur de tag invalide : {color!r}. "
                "Attendu : '#RRGGBB'."
            )
        new_id = tag_id or _new_id()
        if self.get_tag(new_id) is not None:
            raise DuplicateLibraryItemError(
                f"Un tag avec l'identifiant '{new_id}' existe déjà."
            )
        tag = LibraryTag(id=new_id, name=cleaned_name, color=color)
        self.project.library_tags.append(tag)
        return tag

    def rename_tag(self, tag_id: str, new_name: str) -> LibraryTag:
        """Renomme un tag."""
        tag = self.get_tag(tag_id)
        if tag is None:
            raise UnknownLibraryItemError(f"Tag '{tag_id}' introuvable.")
        cleaned = (new_name or "").strip()
        if not cleaned:
            raise LibraryNameError("Le nom du tag ne peut pas être vide.")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise LibraryNameError(
                f"Le nom du tag est trop long "
                f"({len(cleaned)} > {MAX_NAME_LENGTH})."
            )
        tag.name = cleaned
        return tag

    def recolor_tag(self, tag_id: str, color: str) -> LibraryTag:
        """Change la couleur d'un tag."""
        tag = self.get_tag(tag_id)
        if tag is None:
            raise UnknownLibraryItemError(f"Tag '{tag_id}' introuvable.")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color or ""):
            raise LibraryError(
                f"Couleur de tag invalide : {color!r}. "
                "Attendu : '#RRGGBB'."
            )
        tag.color = color
        return tag

    def delete_tag(self, tag_id: str) -> None:
        """Supprime un tag et le retire de toutes les affectations."""
        tag = self.get_tag(tag_id)
        if tag is None:
            raise UnknownLibraryItemError(f"Tag '{tag_id}' introuvable.")
        self.project.library_tags = [
            t for t in self.project.library_tags if t.id != tag_id
        ]
        for assignment in self.project.library_assignments.values():
            if tag_id in assignment.tag_ids:
                assignment.tag_ids = [tid for tid in assignment.tag_ids if tid != tag_id]

    # ------------------------------------------------------------------
    # Affectations média
    # ------------------------------------------------------------------

    def get_assignment(self, asset_id: str) -> AssetAssignment:
        """Retourne l'affectation d'un média (créée par défaut si besoin).

        On *retourne toujours* une affectation (créée à la volée si
        nécessaire) plutôt que ``None`` : l'interface peut alors
        demander le dossier et les tags sans devoir tester ``None``.
        """
        assignment = self.project.library_assignments.get(asset_id)
        if assignment is not None:
            return assignment
        assignment = AssetAssignment(asset_id=asset_id)
        self.project.library_assignments[asset_id] = assignment
        return assignment

    def move_asset(self, asset_id: str, folder_id: str | None) -> AssetAssignment:
        """Range ``asset_id`` dans ``folder_id`` (``None`` = racine)."""
        if not self._asset_exists(asset_id):
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        if folder_id is not None and self.get_folder(folder_id) is None:
            raise UnknownLibraryItemError(
                f"Dossier '{folder_id}' introuvable."
            )
        assignment = self.get_assignment(asset_id)
        assignment.folder_id = folder_id
        return assignment

    def add_tag_to_asset(self, asset_id: str, tag_id: str) -> AssetAssignment:
        """Applique ``tag_id`` au média ``asset_id`` (idempotent)."""
        if not self._asset_exists(asset_id):
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        if self.get_tag(tag_id) is None:
            raise UnknownLibraryItemError(f"Tag '{tag_id}' introuvable.")
        assignment = self.get_assignment(asset_id)
        if tag_id not in assignment.tag_ids:
            assignment.tag_ids.append(tag_id)
        return assignment

    def remove_tag_from_asset(self, asset_id: str, tag_id: str) -> AssetAssignment:
        """Retire ``tag_id`` du média (no-op s'il n'était pas appliqué)."""
        if not self._asset_exists(asset_id):
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        assignment = self.get_assignment(asset_id)
        if tag_id in assignment.tag_ids:
            assignment.tag_ids = [tid for tid in assignment.tag_ids if tid != tag_id]
        return assignment

    def rename_asset(self, asset_id: str, new_name: str) -> None:
        """Renomme un média du projet (utilisé par la bibliothèque)."""
        asset = self._get_asset(asset_id)
        if asset is None:
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        cleaned = (new_name or "").strip()
        if not cleaned:
            raise LibraryNameError("Le nom du média ne peut pas être vide.")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise LibraryNameError(
                f"Le nom du média est trop long "
                f"({len(cleaned)} > {MAX_NAME_LENGTH})."
            )
        asset.name = cleaned

    def relink_asset(
        self,
        asset_id: str,
        new_path: str,
        *,
        probe: Callable[[str], MediaAsset] = probe_media,
    ) -> MediaAsset:
        """Re-lie un média à un nouveau fichier et **relit ses métadonnées**.

        Le fichier relié peut n'avoir ni la même durée, ni la même résolution, ni la même cadence, ni
        les mêmes pistes que l'ancien : sans relecture, le rendu travaillerait avec des valeurs fausses
        (un ``has_audio`` périmé fait échouer l'export, les données de tracking sont rapportées à
        l'ancienne taille). Il en va de même des métadonnées de tournage (timecode, heure BWF, bobine, caméra,
        date de création) : celles du nouveau fichier remplacent les anciennes, et un fichier qui n'en porte
        pas les efface (un timecode périmé fausserait la synchronisation). Le chemin est normalisé via
        ``os.path.normpath``.

        Raises:
            LibraryError: chemin vide, fichier illisible (le média garde alors son ancien chemin), ou
                fichier d'un autre type (vidéo à la place d'un audio, et inversement).
        """
        asset = self._get_asset(asset_id)
        if asset is None:
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        cleaned = (new_path or "").strip()
        if not cleaned:
            raise LibraryError(
                "Le nouveau chemin ne peut pas être vide : "
                "la disparition resterait silencieuse."
            )
        path = os.path.normpath(cleaned)
        try:
            fresh = probe(path)
        except MediaProbeError as exc:
            raise LibraryError(f"Ce fichier ne peut pas être relié : {exc}") from exc
        if {asset.media_type, fresh.media_type} == {"audio", "video"}:
            raise LibraryError(
                f"Ce fichier est {_TYPE_NAMES.get(fresh.media_type, fresh.media_type)}, "
                f"le média d'origine est {_TYPE_NAMES.get(asset.media_type, asset.media_type)}."
            )
        asset.path = path
        asset.duration = fresh.duration
        asset.width = fresh.width
        asset.height = fresh.height
        asset.fps = fresh.fps
        asset.has_audio = fresh.has_audio
        asset.timecode = fresh.timecode
        asset.timecode_fps = fresh.timecode_fps
        asset.time_reference = fresh.time_reference
        asset.reel = fresh.reel
        asset.camera = fresh.camera
        asset.creation_time = fresh.creation_time
        return asset

    def remove_asset(self, asset_id: str, *, keep_orphan_clips: bool = True) -> None:
        """Supprime un média du projet.

        Args:
            asset_id: Média à supprimer.
            keep_orphan_clips: Si ``True`` (par défaut), les clips qui
                référencent ce média sont conservés (leur ``asset_id``
                pointera dans le vide, ce qui correspond au
                comportement historique de l'import : un média
                déplacé ne supprime pas la timeline). Si ``False``,
                les clips orphelins sont également retirés de leurs
                pistes.

        Raises:
            UnknownLibraryItemError: si ``asset_id`` n'existe pas.
        """
        asset = self._get_asset(asset_id)
        if asset is None:
            raise UnknownLibraryItemError(
                f"Média '{asset_id}' introuvable dans le projet."
            )
        # Retrait du projet
        self.project.media_assets = [
            a for a in self.project.media_assets if a.id != asset_id
        ]
        # Nettoyage de l'affectation
        self.project.library_assignments.pop(asset_id, None)
        # Nettoyage des clips orphelins (optionnel)
        if not keep_orphan_clips:
            for track in self.project.all_tracks():
                track.clips = [c for c in track.clips if c.asset_id != asset_id]

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _asset_exists(self, asset_id: str) -> bool:
        return self._get_asset(asset_id) is not None

    def _get_asset(self, asset_id: str) -> MediaAsset | None:
        for asset in self.project.media_assets:
            if asset.id == asset_id:
                return asset
        return None


# ---------------------------------------------------------------------------
# Analyse d'utilisation et statut manquant
# ---------------------------------------------------------------------------


@dataclass
class AssetUsage:
    """Occurrences d'un média sur la timeline.

    Attributes:
        asset_id: Identifiant du média analysé.
        clip_count: Nombre total de clips qui le référencent.
        clip_ids: Identifiants des clips, dans l'ordre d'apparition
            (piste puis position timeline).
        track_ids: Identifiants des pistes qui portent au moins un
            clip utilisant ce média (sans doublon).
    """

    asset_id: str
    clip_count: int = 0
    clip_ids: list[str] = field(default_factory=list)
    track_ids: list[str] = field(default_factory=list)


def _is_asset_missing(asset: MediaAsset) -> bool:
    """``True`` si le média n'est plus joignable sur disque.

    Un média sans chemin (``path == ""``) ou dont ``os.path.exists``
    renvoie ``False`` est considéré comme manquant. La détection
    volontaire se limite à ces deux conditions : on ne tente pas de
    tester une permission, un partage réseau ou un volume démonté.
    """
    # Les titres, formes et aplats sont des médias techniques générés au
    # rendu. Ils n'ont volontairement aucun fichier source à relier.
    if asset.media_type == "graphic" and not asset.path:
        return False
    if not asset.path:
        return True
    return not os.path.exists(asset.path)


def is_asset_missing(asset: MediaAsset) -> bool:
    """API publique : ``True`` si ``asset`` est considéré comme manquant."""
    return _is_asset_missing(asset)


def collect_missing_assets(project: Project) -> list[str]:
    """Retourne les identifiants des médias dont le fichier a disparu."""
    return [a.id for a in project.media_assets if _is_asset_missing(a)]


def compute_usage(project: Project, asset_id: str) -> AssetUsage:
    """Calcule l'utilisation d'un média sur la timeline du projet.

    L'algorithme parcourt les pistes dans l'ordre du projet puis les
    clips dans l'ordre interne (qui est l'ordre de placement sur la
    timeline). Les ``clip_ids`` et ``track_ids`` sont collectés en
    conservant l'ordre de première occurrence.
    """
    usage = AssetUsage(asset_id=asset_id)
    seen_clip_ids: set[str] = set()
    seen_track_ids: set[str] = set()
    for track in project.all_tracks():
        for clip in track.clips:
            if asset_id not in clip.media_ids():          # le média du clip, ou ceux de sa composition
                continue
            usage.clip_count += 1
            if clip.id not in seen_clip_ids:
                usage.clip_ids.append(clip.id)
                seen_clip_ids.add(clip.id)
            if track.id not in seen_track_ids:
                usage.track_ids.append(track.id)
                seen_track_ids.add(track.id)
    return usage


def usage_map(project: Project) -> Mapping[str, AssetUsage]:
    """Calcule l'usage de *tous* les médias en une passe.

    Le résultat est indexé par ``asset_id`` ; un média jamais utilisé
    figure quand même avec ``clip_count == 0`` (cohérence de
    consommation pour l'interface).
    """
    usages: dict[str, AssetUsage] = {
        asset.id: AssetUsage(asset_id=asset.id) for asset in project.media_assets
    }
    seen_clip_ids: dict[str, set[str]] = {asset.id: set() for asset in project.media_assets}
    seen_track_ids: dict[str, set[str]] = {asset.id: set() for asset in project.media_assets}
    for track in project.all_tracks():
        for clip in track.clips:
            for asset_id in clip.media_ids():            # le média du clip, ou ceux de sa composition
                usage = usages.get(asset_id)
                if usage is None:
                    # Clip pointant vers un média inconnu : on l'ignore.
                    continue
                usage.clip_count += 1
                if clip.id not in seen_clip_ids[asset_id]:
                    usage.clip_ids.append(clip.id)
                    seen_clip_ids[asset_id].add(clip.id)
                if track.id not in seen_track_ids[asset_id]:
                    usage.track_ids.append(track.id)
                    seen_track_ids[asset_id].add(track.id)
    return usages


# ---------------------------------------------------------------------------
# Filtres rapides
# ---------------------------------------------------------------------------


# Types de médias reconnus par les filtres rapides. ``"image"`` est
# accepté pour cohérence avec les projets existants qui marquent déjà
# certaines images (``media_type == "image"``).
QUICK_FILTER_TYPES: frozenset[str] = frozenset({"video", "audio", "image"})


def filter_assets_by_type(
    assets: Iterable[MediaAsset],
    media_type: str,
) -> list[MediaAsset]:
    """Filtre ``assets`` en ne gardant que ceux de type ``media_type``."""
    if media_type not in QUICK_FILTER_TYPES:
        return list(assets)
    return [a for a in assets if a.media_type == media_type]


def filter_assets_used(
    assets: Iterable[MediaAsset],
    usages: Mapping[str, AssetUsage],
) -> list[MediaAsset]:
    """Ne garde que les médias ayant au moins une occurrence."""
    return [a for a in assets if usages.get(a.id) and usages[a.id].clip_count > 0]


def filter_assets_unused(
    assets: Iterable[MediaAsset],
    usages: Mapping[str, AssetUsage],
) -> list[MediaAsset]:
    """Ne garde que les médias jamais utilisés."""
    return [
        a for a in assets
        if not usages.get(a.id) or usages[a.id].clip_count == 0
    ]


def filter_assets_missing(assets: Iterable[MediaAsset]) -> list[MediaAsset]:
    """Ne garde que les médias dont le fichier source a disparu."""
    return [a for a in assets if _is_asset_missing(a)]


def filter_assets_present(assets: Iterable[MediaAsset]) -> list[MediaAsset]:
    """Ne garde que les médias dont le fichier est encore présent."""
    return [a for a in assets if not _is_asset_missing(a)]


__all__ = [
    # Modèles
    "AssetAssignment",
    "AssetUsage",
    "LibraryFolder",
    "LibraryTag",
    # Erreurs
    "DuplicateLibraryItemError",
    "LibraryCycleError",
    "LibraryError",
    "LibraryNameError",
    "UnknownLibraryItemError",
    # Service
    "LibraryOrganization",
    # Constantes
    "DEFAULT_TAG_COLOR",
    "FOLDER_COLOR_PALETTE",
    "MAX_NAME_LENGTH",
    "QUICK_FILTER_TYPES",
    "TAG_COLOR_PALETTE",
    # Helpers
    "collect_missing_assets",
    "compute_usage",
    "filter_assets_by_type",
    "filter_assets_missing",
    "filter_assets_present",
    "filter_assets_unused",
    "filter_assets_used",
    "is_asset_missing",
    "usage_map",
]
