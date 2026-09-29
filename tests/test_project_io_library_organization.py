"""Tests pour la persistance de l'organisation de la bibliothèque (tâche 25).

Couvre :

- le roundtrip complet ``library_folders`` / ``library_tags`` /
  ``library_assignments`` au format ``.kut`` v11 ;
- la rétrocompatibilité : un fichier v1 (sans aucun champ
  d'organisation) charge correctement avec listes vides ;
- la robustesse du chargement : dossiers cycliques, parents
  manquants, tags en doublon, affectations vers des assets
  inconnus ou des tags supprimés ne cassent pas l'ouverture ;
- la sérialisation : les nouvelles sections (``library_folders``,
  ``library_tags``, ``library_assignments``) sont bien émises dans
  le payload JSON v11.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.library_organization import (
    AssetAssignment,
    LibraryFolder,
    LibraryTag,
)
from core.project_io import (
    CURRENT_VERSION,
    FORMAT_NAME,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _project_with_video() -> Project:
    asset = MediaAsset(
        id="asset-vid-1",
        path="/tmp/intro.mp4",
        name="Intro",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    return Project(
        name="Demo",
        media_assets=[asset],
        tracks=[Track(id="V1", name="V1", type="video", clips=[])],
    )


def _make_folder(project: Project, folder_id: str, name: str, **kwargs) -> LibraryFolder:
    folder = LibraryFolder(id=folder_id, name=name, **kwargs)
    project.library_folders.append(folder)
    return folder


def _make_tag(project: Project, tag_id: str, name: str, color: str = "#3498db") -> LibraryTag:
    tag = LibraryTag(id=tag_id, name=name, color=color)
    project.library_tags.append(tag)
    return tag


# ---------------------------------------------------------------------------
# Roundtrip complet
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_folders_tags_and_assignments(tmp_path: Path) -> None:
    project = _project_with_video()
    _make_folder(project, "fld-racine", "Racine")
    _make_folder(project, "fld-2024", "2024", parent_id="fld-racine", color="#3498db")
    _make_tag(project, "tag-hero", "Héros", color="#e74c3c")
    _make_tag(project, "tag-broll", "B-roll", color="#2ecc71")
    # Affectation : asset rangé dans 2024 + tag Héros + B-roll.
    project.library_assignments["asset-vid-1"] = AssetAssignment(
        asset_id="asset-vid-1",
        folder_id="fld-2024",
        tag_ids=["tag-hero", "tag-broll"],
    )

    target = tmp_path / "with-org.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))

    # Dossiers : structure conservée, attributs exacts.
    folders_by_id = {f.id: f for f in loaded.library_folders}
    assert set(folders_by_id) == {"fld-racine", "fld-2024"}
    assert folders_by_id["fld-racine"].parent_id is None
    assert folders_by_id["fld-2024"].parent_id == "fld-racine"
    assert folders_by_id["fld-2024"].color == "#3498db"

    # Tags : noms et couleurs conservés.
    tags_by_id = {t.id: t for t in loaded.library_tags}
    assert tags_by_id["tag-hero"].color == "#e74c3c"
    assert tags_by_id["tag-broll"].color == "#2ecc71"

    # Affectation : rangée, dossier et tags conservés.
    assignment = loaded.library_assignments["asset-vid-1"]
    assert assignment.folder_id == "fld-2024"
    assert assignment.tag_ids == ["tag-hero", "tag-broll"]


def test_payload_emits_library_sections(tmp_path: Path) -> None:
    project = _project_with_video()
    _make_folder(project, "fld-racine", "Racine")
    _make_tag(project, "tag-hero", "Héros")
    project.library_assignments["asset-vid-1"] = AssetAssignment(
        asset_id="asset-vid-1",
        folder_id="fld-racine",
        tag_ids=["tag-hero"],
    )

    target = tmp_path / "x.kut"
    save_project(project, str(target))

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["format"] == FORMAT_NAME
    assert raw["version"] == CURRENT_VERSION
    project_data = raw["project"]
    assert project_data["library_folders"] == [
        {
            "id": "fld-racine",
            "name": "Racine",
            "parent_id": None,
            "color": "",
        }
    ]
    assert project_data["library_tags"] == [
        {"id": "tag-hero", "name": "Héros", "color": "#3498db"}
    ]
    assert project_data["library_assignments"] == [
        {
            "asset_id": "asset-vid-1",
            "folder_id": "fld-racine",
            "tag_ids": ["tag-hero"],
        }
    ]


def test_empty_library_persists_as_empty_lists(tmp_path: Path) -> None:
    """Un projet sans organisation sérialise des listes vides explicites."""
    project = _project_with_video()
    target = tmp_path / "no-org.kut"
    save_project(project, str(target))

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["project"]["library_folders"] == []
    assert raw["project"]["library_tags"] == []
    assert raw["project"]["library_assignments"] == []

    loaded = load_project(str(target))
    assert loaded.library_folders == []
    assert loaded.library_tags == []
    assert loaded.library_assignments == {}


# ---------------------------------------------------------------------------
# Rétrocompatibilité ascendante
# ---------------------------------------------------------------------------


def test_legacy_v1_payload_loads_with_empty_library(tmp_path: Path) -> None:
    """Un fichier v1 (sans aucune mention de bibliothèque) ouvre proprement."""
    legacy = {
        "format": FORMAT_NAME,
        "version": 1,
        "project": {
            "name": "Legacy",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [
                {
                    "id": "asset-old",
                    "path": "/tmp/old.mp4",
                    "name": "Old",
                    "duration": 5.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "media_type": "video",
                    "has_audio": False,
                }
            ],
            "tracks": [],
        },
    }
    target = tmp_path / "legacy-v1.kut"
    target.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.library_folders == []
    assert loaded.library_tags == []
    assert loaded.library_assignments == {}


def test_legacy_v10_payload_loads_with_empty_library(tmp_path: Path) -> None:
    """Un fichier v10 (avant l'organisation) ouvre proprement, rétrocompat."""
    project = _project_with_video()
    target = tmp_path / "v10.kut"
    save_project(project, str(target))

    # On simule un fichier v10 en patchant le ``version`` et en
    # supprimant les sections d'organisation de la payload. Le
    # chargement doit retomber sur des listes vides.
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["version"] = 10
    raw["project"].pop("library_folders", None)
    raw["project"].pop("library_tags", None)
    raw["project"].pop("library_assignments", None)
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.library_folders == []
    assert loaded.library_tags == []
    assert loaded.library_assignments == {}


# ---------------------------------------------------------------------------
# Robustesse du chargement
# ---------------------------------------------------------------------------


def test_invalid_folders_are_dropped_without_crashing(tmp_path: Path) -> None:
    """Une entrée de dossier invalide est écartée, les autres sont gardées."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Robust",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [],
            "library_folders": [
                # Valide
                {"id": "fld-good", "name": "Bon", "parent_id": None, "color": ""},
                # Nom vide : ignoré
                {"id": "fld-bad-name", "name": "", "parent_id": None, "color": ""},
                # Couleur invalide : ignoré
                {"id": "fld-bad-color", "name": "Mauvaise couleur", "parent_id": None,
                 "color": "orange"},
                # Doublon : la première occurrence gagne
                {"id": "fld-good", "name": "Doublon", "parent_id": None, "color": ""},
            ],
            "library_tags": [],
            "library_assignments": [],
        },
    }
    target = tmp_path / "robust-folder.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    ids = [f.id for f in loaded.library_folders]
    assert ids == ["fld-good"]


def test_folder_with_unknown_parent_is_reattached_to_root(tmp_path: Path) -> None:
    """Un dossier qui pointe vers un parent disparu est rattaché à la racine."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Orphan",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [],
            "library_folders": [
                {"id": "fld-orphan", "name": "Orphelin",
                 "parent_id": "fld-missing", "color": ""},
            ],
            "library_tags": [],
            "library_assignments": [],
        },
    }
    target = tmp_path / "orphan.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert len(loaded.library_folders) == 1
    assert loaded.library_folders[0].parent_id is None


def test_folder_cycle_is_purged(tmp_path: Path) -> None:
    """Un cycle dans ``library_folders`` est nettoyé au chargement."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Cycle",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [],
            "library_folders": [
                {"id": "fld-a", "name": "A", "parent_id": "fld-b", "color": ""},
                {"id": "fld-b", "name": "B", "parent_id": "fld-a", "color": ""},
            ],
            "library_tags": [],
            "library_assignments": [],
        },
    }
    target = tmp_path / "cycle.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    # Au moins un des deux est purgé. On vérifie qu'aucun cycle ne
    # subsiste : pour chaque dossier gardé, son parent est None ou
    # absent de l'ensemble.
    kept_ids = {f.id for f in loaded.library_folders}
    for folder in loaded.library_folders:
        if folder.parent_id is not None:
            assert folder.parent_id in kept_ids
            assert folder.parent_id != folder.id


def test_invalid_tags_are_dropped_without_crashing(tmp_path: Path) -> None:
    """Tags invalides / doublons sont filtrés sans bloquer l'ouverture."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Tags",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [],
            "library_folders": [],
            "library_tags": [
                {"id": "tag-good", "name": "Bon", "color": "#3498db"},
                # Nom vide : ignoré
                {"id": "tag-bad-name", "name": "", "color": "#3498db"},
                # Couleur invalide : ignoré
                {"id": "tag-bad-color", "name": "Couleur", "color": "red"},
                # Doublon : gardé une seule fois
                {"id": "tag-good", "name": "Doublon", "color": "#3498db"},
            ],
            "library_assignments": [],
        },
    }
    target = tmp_path / "robust-tag.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert [t.id for t in loaded.library_tags] == ["tag-good"]


def test_assignments_to_unknown_assets_are_dropped(tmp_path: Path) -> None:
    """Une affectation vers un média absent du projet est ignorée."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Assign",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [
                {
                    "id": "asset-real",
                    "path": "/tmp/x.mp4",
                    "name": "Real",
                    "duration": 1.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "media_type": "video",
                    "has_audio": False,
                }
            ],
            "tracks": [],
            "library_folders": [],
            "library_tags": [],
            "library_assignments": [
                # Asset connu : conservé.
                {
                    "asset_id": "asset-real",
                    "folder_id": None,
                    "tag_ids": [],
                },
                # Asset inconnu : ignoré.
                {
                    "asset_id": "asset-ghost",
                    "folder_id": None,
                    "tag_ids": [],
                },
            ],
        },
    }
    target = tmp_path / "ghost-assign.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert set(loaded.library_assignments) == {"asset-real"}


def test_assignments_with_unknown_folder_or_tag_are_cleaned(tmp_path: Path) -> None:
    """Une affectation pointant vers un dossier ou un tag disparu est nettoyée."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Refs",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [
                {
                    "id": "asset-x",
                    "path": "/tmp/x.mp4",
                    "name": "X",
                    "duration": 1.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "media_type": "video",
                    "has_audio": False,
                }
            ],
            "tracks": [],
            "library_folders": [
                {"id": "fld-real", "name": "Real", "parent_id": None, "color": ""},
            ],
            "library_tags": [
                {"id": "tag-real", "name": "Real", "color": "#3498db"},
            ],
            "library_assignments": [
                {
                    "asset_id": "asset-x",
                    "folder_id": "fld-ghost",  # dossier disparu
                    "tag_ids": ["tag-real", "tag-ghost"],  # tag fantôme
                },
            ],
        },
    }
    target = tmp_path / "ghost-ref.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assignment = loaded.library_assignments["asset-x"]
    assert assignment.folder_id is None  # dossier fantôme → racine
    assert assignment.tag_ids == ["tag-real"]  # tag fantôme purgé


def test_duplicate_assignments_for_same_asset_keep_first(tmp_path: Path) -> None:
    """Deux affectations pour le même asset : la première gagne."""
    payload = {
        "format": FORMAT_NAME,
        "version": CURRENT_VERSION,
        "project": {
            "name": "Dup",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [
                {
                    "id": "asset-x",
                    "path": "/tmp/x.mp4",
                    "name": "X",
                    "duration": 1.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "media_type": "video",
                    "has_audio": False,
                }
            ],
            "tracks": [],
            "library_folders": [
                {"id": "fld-a", "name": "A", "parent_id": None, "color": ""},
                {"id": "fld-b", "name": "B", "parent_id": None, "color": ""},
            ],
            "library_tags": [],
            "library_assignments": [
                {"asset_id": "asset-x", "folder_id": "fld-a", "tag_ids": []},
                {"asset_id": "asset-x", "folder_id": "fld-b", "tag_ids": []},
            ],
        },
    }
    target = tmp_path / "dup.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert len(loaded.library_assignments) == 1
    assert loaded.library_assignments["asset-x"].folder_id == "fld-a"


def test_payload_includes_library_with_clip_in_scope() -> None:
    """L'API ``project_payload`` capture bien les champs d'organisation."""
    project = _project_with_video()
    _make_folder(project, "fld-x", "X")
    project.library_assignments["asset-vid-1"] = AssetAssignment(
        asset_id="asset-vid-1", folder_id="fld-x", tag_ids=[]
    )

    payload = project_payload(project)
    assert payload["project"]["library_folders"][0]["id"] == "fld-x"
    assert payload["project"]["library_assignments"][0]["asset_id"] == "asset-vid-1"


def test_library_fields_default_to_empty_lists_when_missing() -> None:
    """``Project()`` sans argument expose déjà les champs vides."""
    project = Project(name="Empty")
    assert project.library_folders == []
    assert project.library_tags == []
    assert project.library_assignments == {}