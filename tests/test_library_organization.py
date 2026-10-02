"""Tests pour le module :mod:`core.library_organization`.

Couvre :

- la construction et la validation des modèles ;
- les opérations CRUD sur les dossiers (création, renommage,
  suppression, déplacement, anti-cycle) ;
- les opérations CRUD sur les tags ;
- les affectations (dossier + tags par média) ;
- le relink d'un média et la suppression d'un média ;
- l'analyse d'utilisation (:func:`compute_usage`, :func:`usage_map`) ;
- les filtres rapides (par type, par usage, par statut manquant) ;
- la détection des médias manquants ;
- l'intégration minimale avec :class:`~core.edit_history.ProjectHistory`
  pour valider l'undo/redo sur des actions d'organisation.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.edit_history import ProjectHistory
from core.library_organization import (
    AssetAssignment,
    AssetUsage,
    DEFAULT_TAG_COLOR,
    DuplicateLibraryItemError,
    FOLDER_COLOR_PALETTE,
    LibraryCycleError,
    LibraryError,
    LibraryFolder,
    LibraryNameError,
    LibraryOrganization,
    LibraryTag,
    MAX_NAME_LENGTH,
    TAG_COLOR_PALETTE,
    UnknownLibraryItemError,
    collect_missing_assets,
    compute_usage,
    filter_assets_by_type,
    filter_assets_missing,
    filter_assets_present,
    filter_assets_unused,
    filter_assets_used,
    is_asset_missing,
    usage_map,
)
from core.project_factory import create_default_project
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _project_with_video() -> Project:
    """Projet minimal contenant une vidéo et une piste vide.

    Le média a un chemin qui pointe vers un fichier inexistant pour
    valider les scénarios « manquant » sans dépendre du système de
    fichiers.
    """
    asset = MediaAsset(
        id="asset-vid-1",
        path="/chemin/qui/nexiste/pas/intro.mp4",
        name="Intro",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    track = Track(id="V1", name="V1", type="video", clips=[])
    return Project(name="Org", media_assets=[asset], tracks=[track])


def _audio_asset() -> MediaAsset:
    return MediaAsset(
        id="asset-aud-1",
        path="/chemin/audio.mp3",
        name="Voix off",
        duration=8.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )


def _image_asset() -> MediaAsset:
    return MediaAsset(
        id="asset-img-1",
        path="/chemin/img.png",
        name="Logo",
        duration=0.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="image",
        has_audio=False,
    )


# ---------------------------------------------------------------------------
# Modèles : validation à la construction
# ---------------------------------------------------------------------------


def test_library_folder_rejects_empty_id() -> None:
    with pytest.raises(LibraryError):
        LibraryFolder(id="", name="Sans id")


def test_library_folder_rejects_empty_name() -> None:
    with pytest.raises(LibraryError):
        LibraryFolder(id="fld-x", name="   ")


def test_library_folder_rejects_too_long_name() -> None:
    with pytest.raises(LibraryError):
        LibraryFolder(id="fld-x", name="x" * (MAX_NAME_LENGTH + 1))


def test_library_folder_rejects_invalid_color() -> None:
    with pytest.raises(LibraryError):
        LibraryFolder(id="fld-x", name="ok", color="not-a-color")


def test_library_folder_rejects_self_as_parent() -> None:
    with pytest.raises(LibraryError):
        LibraryFolder(id="fld-x", name="ok", parent_id="fld-x")


def test_library_tag_rejects_empty_name() -> None:
    with pytest.raises(LibraryError):
        LibraryTag(id="tag-x", name="")


def test_library_tag_rejects_invalid_color() -> None:
    with pytest.raises(LibraryError):
        LibraryTag(id="tag-x", name="ok", color="oops")


def test_library_tag_default_color_is_turquoise() -> None:
    tag = LibraryTag(id="tag-x", name="ok")
    assert tag.color == DEFAULT_TAG_COLOR


def test_asset_assignment_defaults_to_root_with_no_tags() -> None:
    a = AssetAssignment(asset_id="asset-x")
    assert a.folder_id is None
    assert a.tag_ids == []


# ---------------------------------------------------------------------------
# Dossiers : CRUD
# ---------------------------------------------------------------------------


def test_create_folder_returns_folder_with_unique_id() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    folder = lib.create_folder("Vidéos 2024", color="#3498db")
    assert folder.name == "Vidéos 2024"
    assert folder.color == "#3498db"
    assert folder.parent_id is None
    assert folder.id  # non vide
    assert lib.get_folder(folder.id) is folder


def test_create_folder_rejects_empty_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(LibraryNameError):
        lib.create_folder("")


def test_create_folder_rejects_unknown_parent() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.create_folder("Enfants", parent_id="fld-missing")


def test_create_folder_with_explicit_id_works() -> None:
    lib = LibraryOrganization(_project_with_video())
    folder = lib.create_folder("Perso", folder_id="fld-perso")
    assert folder.id == "fld-perso"


def test_create_folder_rejects_duplicate_id() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("A", folder_id="fld-dup")
    with pytest.raises(DuplicateLibraryItemError):
        lib.create_folder("B", folder_id="fld-dup")


def test_list_folders_filters_and_sorts() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_folder("B-roll", folder_id="fld-a")
    lib.create_folder("Plans", folder_id="fld-b")
    lib.create_folder("2024", folder_id="fld-c", parent_id="fld-a")
    roots = lib.list_folders(None)
    assert [f.id for f in roots] == ["fld-a", "fld-b"]  # tri alphabétique
    children_of_a = lib.list_folders("fld-a")
    assert [f.id for f in children_of_a] == ["fld-c"]


def test_rename_folder_updates_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("Old", folder_id="fld-r")
    lib.rename_folder("fld-r", "New")
    assert lib.get_folder("fld-r").name == "New"


def test_rename_folder_rejects_empty_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-r")
    with pytest.raises(LibraryNameError):
        lib.rename_folder("fld-r", "")


def test_rename_folder_rejects_unknown() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.rename_folder("fld-missing", "x")


def test_recolor_folder_accepts_valid_hex() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-r")
    lib.recolor_folder("fld-r", "#e67e22")
    assert lib.get_folder("fld-r").color == "#e67e22"


def test_recolor_folder_clears_with_empty_string() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-r", color="#e67e22")
    lib.recolor_folder("fld-r", "")
    assert lib.get_folder("fld-r").color == ""


def test_recolor_folder_rejects_invalid_hex() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-r")
    with pytest.raises(LibraryError):
        lib.recolor_folder("fld-r", "orange")


def test_move_folder_changes_parent() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("Root A", folder_id="fld-a")
    lib.create_folder("Root B", folder_id="fld-b")
    lib.create_folder("Enfant", folder_id="fld-c", parent_id="fld-a")
    lib.move_folder("fld-c", "fld-b")
    assert lib.get_folder("fld-c").parent_id == "fld-b"


def test_move_folder_to_root() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("Root", folder_id="fld-a")
    lib.create_folder("Enfant", folder_id="fld-c", parent_id="fld-a")
    lib.move_folder("fld-c", None)
    assert lib.get_folder("fld-c").parent_id is None


def test_move_folder_rejects_cycle() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("Parent", folder_id="fld-p")
    lib.create_folder("Enfant", folder_id="fld-e", parent_id="fld-p")
    # Tenter de déplacer le parent dans son enfant = cycle.
    with pytest.raises(LibraryCycleError):
        lib.move_folder("fld-p", "fld-e")


def test_move_folder_rejects_self() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("Self", folder_id="fld-s")
    with pytest.raises(LibraryCycleError):
        lib.move_folder("fld-s", "fld-s")


def test_move_folder_rejects_unknown_parent() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-x")
    with pytest.raises(UnknownLibraryItemError):
        lib.move_folder("fld-x", "fld-missing")


def test_move_folder_rejects_unknown_folder() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("ok", folder_id="fld-x")
    with pytest.raises(UnknownLibraryItemError):
        lib.move_folder("fld-missing", "fld-x")


def test_delete_folder_cascade_removes_descendants() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("R", folder_id="fld-r")
    lib.create_folder("C1", folder_id="fld-c1", parent_id="fld-r")
    lib.create_folder("C2", folder_id="fld-c2", parent_id="fld-c1")
    lib.delete_folder("fld-r")
    assert lib.get_folder("fld-r") is None
    assert lib.get_folder("fld-c1") is None
    assert lib.get_folder("fld-c2") is None


def test_delete_folder_without_cascade_reattaches_children_to_root() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("R", folder_id="fld-r")
    lib.create_folder("C1", folder_id="fld-c1", parent_id="fld-r")
    lib.delete_folder("fld-r", cascade=False)
    assert lib.get_folder("fld-r") is None
    assert lib.get_folder("fld-c1").parent_id is None


def test_delete_folder_reparents_assets_to_none() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_folder("R", folder_id="fld-r")
    lib.move_asset("asset-vid-1", "fld-r")
    lib.delete_folder("fld-r", cascade=True)
    assert lib.get_assignment("asset-vid-1").folder_id is None


def test_delete_folder_unknown_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.delete_folder("fld-missing")


# ---------------------------------------------------------------------------
# Tags : CRUD
# ---------------------------------------------------------------------------


def test_create_tag_returns_tag_with_default_color() -> None:
    lib = LibraryOrganization(_project_with_video())
    tag = lib.create_tag("Héros")
    assert tag.name == "Héros"
    assert tag.color == DEFAULT_TAG_COLOR


def test_create_tag_with_explicit_color() -> None:
    lib = LibraryOrganization(_project_with_video())
    tag = lib.create_tag("Héros", color="#e74c3c")
    assert tag.color == "#e74c3c"


def test_create_tag_rejects_empty_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(LibraryNameError):
        lib.create_tag("")


def test_create_tag_rejects_invalid_color() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(LibraryError):
        lib.create_tag("Héros", color="red")


def test_list_tags_sorts_alphabetically() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_tag("Zeta", tag_id="tag-z")
    lib.create_tag("Alpha", tag_id="tag-a")
    lib.create_tag("Mu", tag_id="tag-m")
    assert [t.id for t in lib.list_tags()] == ["tag-a", "tag-m", "tag-z"]


def test_rename_tag_updates_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_tag("Old", tag_id="tag-r")
    lib.rename_tag("tag-r", "New")
    assert lib.get_tag("tag-r").name == "New"


def test_recolor_tag_updates_color() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_tag("ok", tag_id="tag-c", color="#3498db")
    lib.recolor_tag("tag-c", "#2ecc71")
    assert lib.get_tag("tag-c").color == "#2ecc71"


def test_delete_tag_removes_from_assignments() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_tag("A", tag_id="tag-a")
    lib.create_tag("B", tag_id="tag-b")
    lib.add_tag_to_asset("asset-vid-1", "tag-a")
    lib.add_tag_to_asset("asset-vid-1", "tag-b")
    lib.delete_tag("tag-a")
    assert lib.get_tag("tag-a") is None
    assert lib.get_assignment("asset-vid-1").tag_ids == ["tag-b"]


def test_delete_tag_unknown_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.delete_tag("tag-missing")


# ---------------------------------------------------------------------------
# Affectations média
# ---------------------------------------------------------------------------


def test_get_assignment_creates_default_for_unknown_asset() -> None:
    lib = LibraryOrganization(_project_with_video())
    a = lib.get_assignment("asset-vid-1")
    assert a.folder_id is None
    assert a.tag_ids == []


def test_move_asset_to_folder() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_folder("R", folder_id="fld-r")
    lib.move_asset("asset-vid-1", "fld-r")
    assert lib.get_assignment("asset-vid-1").folder_id == "fld-r"


def test_move_asset_to_unknown_folder_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.move_asset("asset-vid-1", "fld-missing")


def test_move_asset_to_unknown_asset_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_folder("R", folder_id="fld-r")
    with pytest.raises(UnknownLibraryItemError):
        lib.move_asset("asset-missing", "fld-r")


def test_add_tag_to_asset_is_idempotent() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_tag("Hero", tag_id="tag-hero")
    lib.add_tag_to_asset("asset-vid-1", "tag-hero")
    lib.add_tag_to_asset("asset-vid-1", "tag-hero")
    assert lib.get_assignment("asset-vid-1").tag_ids == ["tag-hero"]


def test_add_tag_unknown_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.add_tag_to_asset("asset-vid-1", "tag-missing")


def test_remove_tag_from_asset_works() -> None:
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_tag("Hero", tag_id="tag-hero")
    lib.create_tag("Boop", tag_id="tag-boop")
    lib.add_tag_to_asset("asset-vid-1", "tag-hero")
    lib.add_tag_to_asset("asset-vid-1", "tag-boop")
    lib.remove_tag_from_asset("asset-vid-1", "tag-hero")
    assert lib.get_assignment("asset-vid-1").tag_ids == ["tag-boop"]


def test_remove_tag_noop_when_absent() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.create_tag("Hero", tag_id="tag-hero")
    lib.remove_tag_from_asset("asset-vid-1", "tag-hero")
    assert lib.get_assignment("asset-vid-1").tag_ids == []


def test_rename_asset_updates_name() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.rename_asset("asset-vid-1", "Nouveau nom")
    asset = next(a for a in lib.project.media_assets if a.id == "asset-vid-1")
    assert asset.name == "Nouveau nom"


def test_rename_asset_rejects_empty() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(LibraryNameError):
        lib.rename_asset("asset-vid-1", "   ")


# ---------------------------------------------------------------------------
# Relink et suppression d'un média
# ---------------------------------------------------------------------------


def test_relink_updates_path() -> None:
    lib = LibraryOrganization(_project_with_video())
    lib.relink_asset("asset-vid-1", "/nouveau/chemin/intro.mp4", probe=_fake_probe)
    asset = next(a for a in lib.project.media_assets if a.id == "asset-vid-1")
    assert asset.path.endswith("intro.mp4")
    # Normalisé via ``os.path.normpath`` ; on n'impose pas la forme
    # exacte mais on vérifie qu'il n'est pas vide.


def _fake_probe(path: str) -> MediaAsset:
    """Sonde simulée : le fichier relié existe et ressemble à l'ancien (relink testé sans ffprobe)."""
    return MediaAsset("ignoré", path, "ignoré", 10.0, 1920, 1080, 30.0, "video", True)


def test_relink_rejects_empty_path() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(LibraryError):
        lib.relink_asset("asset-vid-1", "")


def test_relink_unknown_asset_raises() -> None:
    lib = LibraryOrganization(_project_with_video())
    with pytest.raises(UnknownLibraryItemError):
        lib.relink_asset("asset-missing", "/x.mp4")


def test_remove_asset_drops_from_project_and_assignment() -> None:
    project = _project_with_video()
    project.media_assets.append(_audio_asset())
    lib = LibraryOrganization(project)
    lib.move_asset("asset-vid-1", None)
    lib.move_asset("asset-aud-1", None)
    lib.remove_asset("asset-vid-1")
    assert [a.id for a in project.media_assets] == ["asset-aud-1"]
    assert "asset-vid-1" not in project.library_assignments


def test_remove_asset_drops_orphan_clips_when_requested() -> None:
    project = _project_with_video()
    # Un clip qui utilise le média : on l'ajoute puis on supprime avec
    # ``keep_orphan_clips=False``.
    clip = Clip(
        id="clip-1",
        asset_id="asset-vid-1",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=1.0,
    )
    project.tracks[0].clips.append(clip)
    lib = LibraryOrganization(project)
    lib.remove_asset("asset-vid-1", keep_orphan_clips=False)
    assert project.tracks[0].clips == []


def test_remove_asset_keeps_orphan_clips_by_default() -> None:
    project = _project_with_video()
    clip = Clip(
        id="clip-1",
        asset_id="asset-vid-1",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=1.0,
    )
    project.tracks[0].clips.append(clip)
    lib = LibraryOrganization(project)
    lib.remove_asset("asset-vid-1")
    assert project.tracks[0].clips == [clip]


# ---------------------------------------------------------------------------
# Détection des médias manquants
# ---------------------------------------------------------------------------


def test_is_asset_missing_when_path_empty(tmp_path) -> None:
    asset = MediaAsset(
        id="x",
        path="",
        name="vide",
        duration=1.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    assert is_asset_missing(asset) is True


def test_is_asset_missing_when_file_does_not_exist() -> None:
    asset = MediaAsset(
        id="x",
        path="/tmp/definitely-not-here-xyz.mp4",
        name="introuvable",
        duration=1.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    assert is_asset_missing(asset) is True


def test_is_asset_not_missing_when_file_exists(tmp_path) -> None:
    file = tmp_path / "real.mp4"
    file.write_bytes(b"")
    asset = MediaAsset(
        id="x",
        path=str(file),
        name="présent",
        duration=1.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    assert is_asset_missing(asset) is False


def test_collect_missing_assets(tmp_path) -> None:
    real = tmp_path / "real.mp4"
    real.write_bytes(b"")
    a1 = MediaAsset(
        id="missing-1", path="/nope.mp4", name="m1",
        duration=1.0, width=1920, height=1080, fps=30.0,
        media_type="video", has_audio=False,
    )
    a2 = MediaAsset(
        id="present", path=str(real), name="p",
        duration=1.0, width=1920, height=1080, fps=30.0,
        media_type="video", has_audio=False,
    )
    a3 = MediaAsset(
        id="missing-2", path="", name="m2",
        duration=1.0, width=1920, height=1080, fps=30.0,
        media_type="video", has_audio=False,
    )
    project = Project(
        name="x",
        media_assets=[a1, a2, a3],
        tracks=[Track(id="V1", name="V1", type="video", clips=[])],
    )
    assert collect_missing_assets(project) == ["missing-1", "missing-2"]


# ---------------------------------------------------------------------------
# Analyse d'utilisation
# ---------------------------------------------------------------------------


def test_compute_usage_counts_clips_and_track_ids() -> None:
    project = _project_with_video()
    project.tracks.append(Track(id="V2", name="V2", type="video", clips=[]))
    project.tracks.append(Track(id="S1", name="S1", type="subtitle", clips=[]))
    project.tracks[0].clips.append(
        Clip(
            id="c1",
            asset_id="asset-vid-1",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    project.tracks[1].clips.append(
        Clip(
            id="c2",
            asset_id="asset-vid-1",
            track_id="V2",
            timeline_start=0.0,
            source_in=0.0,
            source_out=2.0,
        )
    )
    usage = compute_usage(project, "asset-vid-1")
    assert usage.clip_count == 2
    assert usage.clip_ids == ["c1", "c2"]
    assert usage.track_ids == ["V1", "V2"]


def test_compute_usage_returns_zero_for_unused_asset() -> None:
    project = _project_with_video()
    usage = compute_usage(project, "asset-vid-1")
    assert usage.clip_count == 0
    assert usage.clip_ids == []
    assert usage.track_ids == []


def test_usage_map_includes_all_assets_even_unused() -> None:
    project = _project_with_video()
    project.media_assets.append(_audio_asset())
    project.media_assets.append(_image_asset())
    usages = usage_map(project)
    assert set(usages) == {"asset-vid-1", "asset-aud-1", "asset-img-1"}
    assert all(isinstance(v, AssetUsage) for v in usages.values())


def test_usage_map_skips_orphan_clips() -> None:
    project = _project_with_video()
    # Clip vers un média inconnu : doit être ignoré sans crash.
    project.tracks[0].clips.append(
        Clip(
            id="c-orphan",
            asset_id="asset-unknown",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    usages = usage_map(project)
    assert "asset-unknown" not in usages


# ---------------------------------------------------------------------------
# Filtres rapides
# ---------------------------------------------------------------------------


def test_filter_assets_by_type_video() -> None:
    project = _project_with_video()
    project.media_assets.extend([_audio_asset(), _image_asset()])
    videos = filter_assets_by_type(project.media_assets, "video")
    assert [a.id for a in videos] == ["asset-vid-1"]


def test_filter_assets_by_type_unknown_returns_all() -> None:
    project = _project_with_video()
    project.media_assets.append(_audio_asset())
    audios = filter_assets_by_type(project.media_assets, "bogus-type")
    assert len(audios) == 2


def test_filter_assets_used_keeps_only_with_occurrences() -> None:
    project = _project_with_video()
    project.media_assets.append(_audio_asset())
    project.tracks[0].clips.append(
        Clip(
            id="c1",
            asset_id="asset-vid-1",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    usages = usage_map(project)
    used = filter_assets_used(project.media_assets, usages)
    assert [a.id for a in used] == ["asset-vid-1"]


def test_filter_assets_unused_keeps_only_without_occurrences() -> None:
    project = _project_with_video()
    project.media_assets.append(_audio_asset())
    project.tracks[0].clips.append(
        Clip(
            id="c1",
            asset_id="asset-vid-1",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    usages = usage_map(project)
    unused = filter_assets_unused(project.media_assets, usages)
    assert [a.id for a in unused] == ["asset-aud-1"]


def test_filter_assets_missing_keeps_only_unreachable(tmp_path) -> None:
    project = _project_with_video()  # asset-vid-1 est manquant
    real = tmp_path / "real.mp3"
    real.write_bytes(b"")
    present_audio = MediaAsset(
        id="asset-aud-1",
        path=str(real),
        name="présent",
        duration=8.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    project.media_assets.append(present_audio)
    missing = filter_assets_missing(project.media_assets)
    assert [a.id for a in missing] == ["asset-vid-1"]


def test_filter_assets_present_keeps_only_reachable(tmp_path) -> None:
    project = _project_with_video()  # asset-vid-1 est manquant
    real = tmp_path / "real.mp3"
    real.write_bytes(b"")
    present_audio = MediaAsset(
        id="asset-aud-1",
        path=str(real),
        name="présent",
        duration=8.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    project.media_assets.append(present_audio)
    present = filter_assets_present(project.media_assets)
    assert [a.id for a in present] == ["asset-aud-1"]


# ---------------------------------------------------------------------------
# Constantes exposées
# ---------------------------------------------------------------------------


def test_tag_color_palette_is_non_empty() -> None:
    assert len(TAG_COLOR_PALETTE) >= 4
    assert all(c.startswith("#") and len(c) == 7 for c in TAG_COLOR_PALETTE)


def test_folder_color_palette_is_non_empty() -> None:
    assert len(FOLDER_COLOR_PALETTE) >= 4
    assert all(c.startswith("#") and len(c) == 7 for c in FOLDER_COLOR_PALETTE)


# ---------------------------------------------------------------------------
# Intégration : undo/redo via ProjectHistory
# ---------------------------------------------------------------------------


def test_undo_redo_round_trip_for_folder_create() -> None:
    project = _project_with_video()
    history = ProjectHistory()
    history.reset(project)

    lib = LibraryOrganization(project)
    lib.create_folder("Perso", folder_id="fld-perso")
    history.record(project, "Créer un dossier")

    assert "fld-perso" in {f.id for f in project.library_folders}

    # ``ProjectHistory.undo()`` *retourne* le projet restauré : c'est
    # sur cette référence qu'il faut vérifier l'état, comme le fait
    # ``MainWindow._apply_history_snapshot``.
    snapshot = history.undo()
    assert snapshot is not None
    assert {f.id for f in snapshot.library_folders} == set()

    snapshot_redo = history.redo()
    assert snapshot_redo is not None
    assert {f.id for f in snapshot_redo.library_folders} == {"fld-perso"}


def test_undo_redo_round_trip_for_tag_and_assign() -> None:
    project = _project_with_video()
    history = ProjectHistory()
    history.reset(project)

    lib = LibraryOrganization(project)
    lib.create_tag("Hero", tag_id="tag-hero", color="#e74c3a")
    history.record(project, "Créer un tag")
    lib.add_tag_to_asset("asset-vid-1", "tag-hero")
    history.record(project, "Taguer un média")

    assert lib.get_assignment("asset-vid-1").tag_ids == ["tag-hero"]

    # Premier ``undo`` : défait le tag, garde le tag en banque.
    # Avant l'ajout du tag, il n'y avait pas d'affectation pour le
    # média (créée paresseusement par ``add_tag_to_asset``). Le
    # snapshot restauré doit donc exposer un mapping vide.
    snapshot = history.undo()
    assert snapshot is not None
    assert snapshot.library_assignments == {}
    assert {t.id for t in snapshot.library_tags} == {"tag-hero"}

    # Second ``undo`` : défait la création du tag.
    snapshot2 = history.undo()
    assert snapshot2 is not None
    assert {t.id for t in snapshot2.library_tags} == set()


def test_deepcopy_of_project_does_not_share_library_lists() -> None:
    """Garde-fou : un snapshot profond ne doit pas partager ses listes."""
    project = _project_with_video()
    lib = LibraryOrganization(project)
    lib.create_folder("R", folder_id="fld-r")
    lib.create_tag("t", tag_id="tag-t")
    snapshot = deepcopy(project)
    assert snapshot.library_folders is not project.library_folders
    assert snapshot.library_tags is not project.library_tags
    assert snapshot.library_assignments is not project.library_assignments


def test_default_factory_initializes_library_fields() -> None:
    """Les champs d'organisation existent dès la construction par défaut."""
    project = Project(name="x")
    assert project.library_folders == []
    assert project.library_tags == []
    assert project.library_assignments == {}


def test_default_project_has_library_fields() -> None:
    """Le projet créé par la fabrique expose aussi les champs vides."""
    project = create_default_project()
    assert project.library_folders == []
    assert project.library_tags == []
    assert project.library_assignments == {}


def test_library_organization_initializes_missing_fields() -> None:
    """Un projet pré-v11 (sans champs) reste utilisable via le service."""
    project = Project(name="legacy")
    # On simule un projet ancien en effaçant les champs après
    # construction ; le service doit les recréer vides.
    del project.library_folders
    del project.library_tags
    del project.library_assignments
    lib = LibraryOrganization(project)
    folder = lib.create_folder("Vidéos", folder_id="fld-v")
    assert folder.parent_id is None
    assert project.library_folders == [folder]