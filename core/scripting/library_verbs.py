"""Bibliothèque de médias : dossiers, étiquettes, rangement, renommer, relier, retirer (lot 9 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la méthode de :class:`~core.library_organization.LibraryOrganization` que l'interface
appelle (``ui/main_window_mixins/library_organization.py``). L'organisation (dossiers, étiquettes, affectations) est un champ du
**projet**, enregistré dans le ``.kut`` : ce sont des verbes normaux (voir :mod:`core.scripting.library_rules`).

Règles communes :

- **bornes** : nombre de dossiers et d'étiquettes, profondeur de l'arbre (calculée sur l'arbre résultant), noms de 48 caractères au plus
  (le modèle refuse au-delà) ; un nom qui est l'identifiant d'un autre objet est refusé (un script désigne par l'identifiant d'abord) ;
- **médias** : seuls les médias de la bibliothèque (vidéo, son, image) sont visés, pas les médias techniques des calques ;
- **``relink_media``** lit un fichier : mêmes règles de chemin que ``import_media`` (dossier du script, ``--allow-outside`` décidé par
  l'appelant) et même sonde ``ffprobe`` en lecture seule ; il refuse en plus ce que l'interface ne ferait que signaler (autre genre de
  média, fichier plus court que ce que les clips lisent, média d'un clip verrouillé, suivi lié à l'ancienne taille) ;
- **``remove_media``** refuse un média encore utilisé par un clip de n'importe quelle séquence (l'interface le retire et laisse des clips
  orphelins).
"""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from ..library_organization import LibraryOrganization, MAX_NAME_LENGTH, compute_usage
from ..media_probe import MediaProbeError, probe_media
from ..project_model import MediaAsset, Project
from ..template_slots import PHOTO_EXTENSIONS
from .image_layer import read_image_size
from .library_rules import (
    library_asset, require_depth, require_folder_room, require_name_not_an_id, require_tag_room,
)
from .verbs import Kind, Operation, Param, ScriptContext, Verb, _pick, _require_unlocked

_TOLERANCE = 5e-7
"""Secondes : l'arrondi à 6 décimales de ``describe`` (une durée relue au microseconde près n'est pas « plus courte »)."""
_FOLDER = Param("folder", Kind.SELECTOR, "Le dossier : son identifiant ou son nom exact (unique).", of="library_folder")
_TAG = Param("tag", Kind.SELECTOR, "L'étiquette : son identifiant ou son nom exact (unique).", of="library_tag")
_ASSET = Param("asset", Kind.SELECTOR, "Le média du projet : identifiant ou nom exact (unique).", of="asset")
_NAME = Param("name", Kind.STRING, f"Nom ({MAX_NAME_LENGTH} caractères au plus).", max_length=MAX_NAME_LENGTH)
_FOLDER_COLOR = Param("color", Kind.COLOR, "Couleur « #RRGGBB » ; absente, le dossier n'a pas de couleur propre.", required=False)


def _folder_id(project: Project, selector: str) -> str:
    return str(_pick(project.library_folders, selector, "Dossier").id)


def _tag_id(project: Project, selector: str) -> str:
    return str(_pick(project.library_tags, selector, "Étiquette").id)


def _asset(project: Project, selector: str) -> MediaAsset:
    asset: MediaAsset = _pick(project.media_assets, selector, "Média")
    return library_asset(asset)


def _optional_folder(project: Project, a: Mapping[str, Any], key: str) -> str | None:
    return _folder_id(project, a[key]) if a.get(key) is not None else None


# --- Dossiers ----------------------------------------------------------------------------------------------------------


def _create_folder(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        parent = _optional_folder(project, a, "parent")
        require_folder_room(project)
        require_depth(project, parent)
        require_name_not_an_id(a["name"], (folder.id for folder in project.library_folders), "dossier")
        return LibraryOrganization(project).create_folder(a["name"], parent_id=parent, color=a.get("color", ""), folder_id=a.get("id"))

    return operation


def _rename_folder(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        folder_id = _folder_id(project, a["folder"])
        require_name_not_an_id(a["name"], (folder.id for folder in project.library_folders if folder.id != folder_id), "dossier")
        return LibraryOrganization(project).rename_folder(folder_id, a["name"])

    return operation


def _recolor_folder(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).recolor_folder(_folder_id(p, a["folder"]), a.get("color", ""))


def _move_folder(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        folder_id = _folder_id(project, a["folder"])
        parent = _optional_folder(project, a, "parent")
        require_depth(project, parent, folder_id)         # ancêtres du nouveau parent ET sous-arbre déplacé ; le cycle, c'est le modèle
        return LibraryOrganization(project).move_folder(folder_id, parent)

    return operation


def _delete_folder(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).delete_folder(_folder_id(p, a["folder"]), cascade=True)


# --- Étiquettes --------------------------------------------------------------------------------------------------------


def _create_tag(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        require_tag_room(project)
        require_name_not_an_id(a["name"], (tag.id for tag in project.library_tags), "étiquette")
        organization = LibraryOrganization(project)
        if "color" in a:
            return organization.create_tag(a["name"], color=a["color"], tag_id=a.get("id"))
        return organization.create_tag(a["name"], tag_id=a.get("id"))

    return operation


def _rename_tag(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        tag_id = _tag_id(project, a["tag"])
        require_name_not_an_id(a["name"], (tag.id for tag in project.library_tags if tag.id != tag_id), "étiquette")
        return LibraryOrganization(project).rename_tag(tag_id, a["name"])

    return operation


def _recolor_tag(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).recolor_tag(_tag_id(p, a["tag"]), a["color"])


def _delete_tag(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).delete_tag(_tag_id(p, a["tag"]))


# --- Médias ------------------------------------------------------------------------------------------------------------


def _move_media(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        asset = _asset(project, a["asset"])
        return LibraryOrganization(project).move_asset(asset.id, _optional_folder(project, a, "folder"))

    return operation


def _tag_media(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).add_tag_to_asset(_asset(p, a["asset"]).id, _tag_id(p, a["tag"]))


def _untag_media(a: Mapping[str, Any]) -> Operation:
    return lambda p: LibraryOrganization(p).remove_tag_from_asset(_asset(p, a["asset"]).id, _tag_id(p, a["tag"]))


def _rename_media(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        asset = _asset(project, a["asset"])
        require_name_not_an_id(a["name"], (item.id for item in project.media_assets if item.id != asset.id), "média")
        LibraryOrganization(project).rename_asset(asset.id, a["name"])
        return asset

    return operation


def _using_clips(project: Project, asset_id: str) -> list[Any]:
    """Les clips de TOUTES les séquences qui montrent ce média (le sien, ou celui d'un nœud de leur composition)."""
    return [clip for track in project.all_tracks() for clip in track.clips if asset_id in clip.media_ids()]


def _remove_media(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        asset = _asset(project, a["asset"])
        usage = compute_usage(project, asset.id)
        if usage.clip_count:
            shown = ", ".join(usage.clip_ids[:3]) + ("…" if len(usage.clip_ids) > 3 else "")
            raise ValueError(f"Le média '{asset.id}' est utilisé par {usage.clip_count} clip(s) ({shown}) : retirez-les d'abord (delete_clip).")
        LibraryOrganization(project).remove_asset(asset.id, keep_orphan_clips=True)
        return None

    return operation


def _relink_media(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Project) -> Any:
        if context is None:
            raise ValueError("relink_media lit un fichier : l'appelant doit fournir le dossier du script (contexte de fichiers).")
        asset = _asset(project, a["asset"])
        path = context.resolve_media(a["path"])
        normalized = os.path.normpath(os.path.abspath(path))
        for other in project.media_assets:
            if other.id != asset.id and other.path and os.path.normpath(os.path.abspath(other.path)) == normalized:
                raise ValueError(f"Ce fichier est déjà le média '{other.id}' du projet.")
        users = _using_clips(project, asset.id)
        for sequence in project.sequences:                  # relier change l'image ou le son de ces clips : l'interface ne regarde pas les verrous, un script oui
            for track in sequence.tracks:
                if track.locked and any(asset.id in clip.media_ids() for clip in track.clips):
                    _require_unlocked(track)
        longest = max((clip.source_out for clip in users if clip.asset_id == asset.id), default=0.0)
        tracked = any(clip.tracking is not None and not clip.tracking.is_empty for clip in users)

        def checked_probe(text: str) -> MediaAsset:
            still = Path(text).suffix.lower() in PHOTO_EXTENSIONS
            if asset.media_type == "image":
                # ``probe_media`` classe toute image fixe en « video » : une image se relit comme l'import de photos (Qt, extension
                # confrontée au contenu), la durée et la cadence d'origine sont gardées.
                if not still:
                    raise MediaProbeError("ce fichier n'est pas une image : le média d'origine en est une.")
                try:
                    width, height = read_image_size(Path(text))
                except ValueError as error:
                    raise MediaProbeError(str(error)) from error
                return MediaAsset(asset.id, text, asset.name, asset.duration, width, height, asset.fps, "image", has_audio=False)
            if still:
                raise MediaProbeError(f"ce fichier est une image, le média d'origine est de genre '{asset.media_type}'.")
            fresh = probe_media(text)                      # la sonde de l'import : lecture seule
            if fresh.media_type != asset.media_type:
                raise MediaProbeError(f"ce fichier est de genre '{fresh.media_type}', le média d'origine est de genre '{asset.media_type}'.")
            if fresh.duration < longest - _TOLERANCE:
                raise MediaProbeError(f"ce fichier dure {fresh.duration:g} s : les clips lisent jusqu'à {longest:g} s (l'interface le signalerait, un script le refuse).")
            if tracked and ((fresh.width, fresh.height) != (asset.width, asset.height) or abs(fresh.fps - asset.fps) > 0.01):
                raise MediaProbeError("des clips de ce média portent des données de suivi, rapportées à l'ancienne taille ou cadence du fichier.")
            return fresh

        return LibraryOrganization(project).relink_asset(asset.id, str(path), probe=checked_probe)

    return operation


LIBRARY_VERBS: tuple[Verb, ...] = (
    Verb("create_library_folder", "Crée un dossier de la bibliothèque, à la racine ou sous un autre dossier (au plus 32 niveaux) ; le dossier créé est l'objet visé par `id` / `as`.",
         (_NAME, Param("parent", Kind.SELECTOR, "Le dossier parent ; absent, la racine.", required=False, of="library_folder"), _FOLDER_COLOR),
         _create_folder, creates="library_folder", group="library"),
    Verb("rename_library_folder", "Renomme un dossier de la bibliothèque.", (_FOLDER, _NAME), _rename_folder, group="library"),
    Verb("recolor_library_folder", "Change la couleur d'un dossier ; sans `color`, il n'en a plus.", (_FOLDER, _FOLDER_COLOR), _recolor_folder, group="library"),
    Verb("move_library_folder", "Déplace un dossier (et son contenu) sous un autre dossier, ou à la racine sans `parent` ; un cycle ou plus de 32 niveaux est refusé.",
         (_FOLDER, Param("parent", Kind.SELECTOR, "Le nouveau parent ; absent, la racine.", required=False, of="library_folder")),
         _move_folder, group="library"),
    Verb("delete_library_folder", "Supprime un dossier et ses sous-dossiers, comme l'interface : les médias qu'ils rangeaient reviennent à la racine (aucun média n'est retiré).",
         (_FOLDER,), _delete_folder, group="library"),
    Verb("create_library_tag", "Crée une étiquette de la bibliothèque (couleur par défaut du modèle sans `color`).",
         (_NAME, Param("color", Kind.COLOR, "Couleur « #RRGGBB ».", required=False)), _create_tag, creates="library_tag", group="library"),
    Verb("rename_library_tag", "Renomme une étiquette.", (_TAG, _NAME), _rename_tag, group="library"),
    Verb("recolor_library_tag", "Change la couleur d'une étiquette.", (_TAG, Param("color", Kind.COLOR, "Couleur « #RRGGBB ».")), _recolor_tag, group="library"),
    Verb("delete_library_tag", "Supprime une étiquette et la retire de tous les médias.", (_TAG,), _delete_tag, group="library"),
    Verb("move_media_to_folder", "Range un média de la bibliothèque dans un dossier, ou à la racine sans `folder`.",
         (_ASSET, Param("folder", Kind.SELECTOR, "Le dossier ; absent, la racine.", required=False, of="library_folder")),
         _move_media, group="library"),
    Verb("tag_media", "Pose une étiquette sur un média (sans effet si elle y est déjà).", (_ASSET, _TAG), _tag_media, group="library"),
    Verb("untag_media", "Retire une étiquette d'un média (sans effet si elle n'y est pas).", (_ASSET, _TAG), _untag_media, group="library"),
    Verb("rename_media", "Renomme un média de la bibliothèque (48 caractères au plus) ; les clips gardent leur propre nom.",
         (_ASSET, _NAME), _rename_media, group="library"),
    Verb("relink_media", "Relie un média à un autre fichier, **lu en lecture seule** (`path`, dossier du script) : durée, cadre, cadence et métadonnées de tournage sont relus. "
                         "Refusé : autre genre de média, fichier plus court que ce que les clips lisent, média d'un clip verrouillé, suivi lié à une autre taille.",
         (_ASSET, Param("path", Kind.PATH, "Le nouveau fichier, relatif au dossier du script.")), _relink_media, group="library", needs_context=True),
    Verb("remove_media", "Retire de la bibliothèque un média **inutilisé** (un média encore sur une timeline, dans n'importe quelle séquence, est refusé).",
         (_ASSET,), _remove_media, group="library"),
)

__all__ = ["LIBRARY_VERBS"]
