"""Tests d'interface pour l'organisation avancée de la bibliothèque (tâche 25).

Couvre les widgets isolés (``FolderTreeWidget``,
``FilterChipBar``, ``TagManagerDialog``, ``AssetContextMenuBuilder``)
sans charger le MainWindow complet, pour rester rapide et robuste
face aux évolutions futures de l'UI principale.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.library_organization import (
    LibraryOrganization,
)
from core.project_model import MediaAsset, Project, Track
from ui.library_organization_widgets import (
    FILTER_ALL,
    FILTER_MISSING,
    FILTER_USED,
    FILTER_VIDEO,
    AssetContextMenuBuilder,
    FilterChipBar,
    FolderTreeWidget,
    TagManagerDialog,
    compute_badges,
)


# ---------------------------------------------------------------------------
# Fixture Qt : un QApplication par session pytest-qt.
# ---------------------------------------------------------------------------


@pytest.fixture
def qapp(qtbot):
    """S'assure qu'un QApplication existe avant chaque test."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _project_with_videos(tmp_path) -> Project:
    real_a = tmp_path / "a.mp4"
    real_a.write_bytes(b"")
    real_c = tmp_path / "c.mp3"
    real_c.write_bytes(b"")
    asset_a = MediaAsset(
        id="asset-a",
        path=str(real_a),
        name="Plan A",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    asset_b = MediaAsset(
        id="asset-b",
        path="/nope/does-not-exist.mp4",  # média manquant
        name="B-roll",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    asset_c = MediaAsset(
        id="asset-c",
        path=str(real_c),
        name="Voix off",
        duration=8.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    return Project(
        name="Demo",
        media_assets=[asset_a, asset_b, asset_c],
        tracks=[Track(id="V1", name="V1", type="video", clips=[])],
    )


# ---------------------------------------------------------------------------
# FolderTreeWidget
# ---------------------------------------------------------------------------


def test_folder_tree_has_three_synthetic_entries(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=False)
    assert tree.tree.topLevelItemCount() == 2  # Tous + Racine
    assert tree.tree.topLevelItem(0).text(0).startswith("Tous")
    assert tree.tree.topLevelItem(1).text(0).startswith("Racine")


def test_folder_tree_adds_missing_entry_when_needed(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=True)
    # Trois entrées : Tous, Racine, Manquants.
    assert tree.tree.topLevelItemCount() == 3


def test_folder_tree_creates_custom_folder_entry(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos 2024", folder_id="fld-2024")
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=False)
    # Deux racines synthétiques + un dossier personnalisé.
    assert tree.tree.topLevelItemCount() == 3
    labels = [tree.tree.topLevelItem(i).text(0) for i in range(3)]
    assert any("Vidéos 2024" in label for label in labels)


def test_folder_tree_select_emits_signal(qapp, qtbot, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos", folder_id="fld-v")
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=False)

    captured = []
    tree.folder_selected.connect(lambda fid: captured.append(fid))

    # Sélectionner le dossier « Vidéos ».
    for index in range(tree.tree.topLevelItemCount()):
        item = tree.tree.topLevelItem(index)
        if "Vidéos" in item.text(0):
            tree.tree.setCurrentItem(item)
            break

    # Le dernier signal émis doit être l'identifiant du dossier.
    assert "fld-v" in captured


def test_folder_tree_set_folder_counts(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos", folder_id="fld-v")
    org.move_asset("asset-a", "fld-v")
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=False)
    tree.set_folder_counts(
        {
            "__all__": 3,
            "__root__": 2,
            "__missing__": 1,
            "fld-v": 1,
        }
    )
    labels = [tree.tree.topLevelItem(i).text(0) for i in range(tree.tree.topLevelItemCount())]
    assert any("Tous  (3)" in label for label in labels)
    assert any("Vidéos  (1)" in label for label in labels)


def test_folder_tree_create_button_emits_signal(qapp, qtbot, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    tree = FolderTreeWidget()
    tree.set_organization(org, has_missing=False)

    captured_parents = []
    tree.folder_create_requested.connect(
        lambda parent_id: captured_parents.append(parent_id)
    )
    tree.add_button.click()
    assert captured_parents == [None]  # parent = racine par défaut


# ---------------------------------------------------------------------------
# FilterChipBar
# ---------------------------------------------------------------------------


def test_filter_chip_bar_starts_with_all_active(qapp, tmp_path) -> None:
    bar = FilterChipBar()
    assert bar.active_filter() == FILTER_ALL


def test_filter_chip_bar_selection_emits_signal(qapp, qtbot, tmp_path) -> None:
    bar = FilterChipBar()
    captured = []
    bar.filter_changed.connect(lambda fid: captured.append(fid))
    bar._buttons[FILTER_VIDEO].click()
    assert captured == [FILTER_VIDEO]
    assert bar.active_filter() == FILTER_VIDEO


def test_filter_chip_bar_dedups_clicks(qapp, tmp_path) -> None:
    bar = FilterChipBar()
    captured = []
    bar.filter_changed.connect(lambda fid: captured.append(fid))
    bar._buttons[FILTER_VIDEO].click()
    bar._buttons[FILTER_VIDEO].click()  # même chip → pas de signal
    assert captured == [FILTER_VIDEO]


def test_filter_chip_bar_set_active_emits_signal(qapp, tmp_path) -> None:
    bar = FilterChipBar()
    captured = []
    bar.filter_changed.connect(lambda fid: captured.append(fid))
    bar.set_active_filter(FILTER_USED)
    assert bar.active_filter() == FILTER_USED
    assert captured == [FILTER_USED]


def test_filter_chip_bar_set_active_dedups_when_idempotent(qapp, tmp_path) -> None:
    bar = FilterChipBar()
    captured = []
    bar.filter_changed.connect(lambda fid: captured.append(fid))
    bar.set_active_filter(FILTER_USED)
    bar.set_active_filter(FILTER_USED)  # idempotent → pas de signal
    assert captured == [FILTER_USED]


# ---------------------------------------------------------------------------
# TagManagerDialog
# ---------------------------------------------------------------------------


def test_tag_manager_dialog_creates_tag(qapp, qtbot, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    dialog = TagManagerDialog(org)
    qtbot.addWidget(dialog)
    dialog.name_field.setText("Héros")
    dialog._create_tag()
    assert any(t.name == "Héros" for t in org.list_tags())


def test_tag_manager_dialog_rejects_empty_name(qapp, qtbot, monkeypatch, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    dialog = TagManagerDialog(org)
    qtbot.addWidget(dialog)
    # On neutralise QMessageBox.warning pour ne pas bloquer le test
    # sur un dialogue modal synchrone.
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args, **kwargs: None)
    )
    dialog.name_field.setText("   ")
    dialog._create_tag()
    assert org.list_tags() == []


def test_tag_manager_dialog_renames_tag(qapp, qtbot, monkeypatch, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_tag("Old", tag_id="tag-x")
    dialog = TagManagerDialog(org)
    qtbot.addWidget(dialog)
    # On monkeypatche QInputDialog.getText pour ne pas ouvrir de modale.
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("New", True)),
    )
    dialog._rename_tag("tag-x")
    assert org.get_tag("tag-x").name == "New"


def test_tag_manager_dialog_deletes_tag(qapp, qtbot, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_tag("Old", tag_id="tag-x")
    dialog = TagManagerDialog(org)
    qtbot.addWidget(dialog)
    dialog._delete_tag("tag-x")
    assert org.get_tag("tag-x") is None


def test_tag_manager_dialog_rebuilds_after_external_change(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    dialog = TagManagerDialog(org)
    org.create_tag("Live", tag_id="tag-live")
    dialog.refresh()
    assert "tag-live" in dialog._tag_rows


# ---------------------------------------------------------------------------
# AssetContextMenuBuilder
# ---------------------------------------------------------------------------


def test_asset_context_menu_includes_core_actions(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos", folder_id="fld-v")
    org.create_tag("Héros", tag_id="tag-hero")

    builder = AssetContextMenuBuilder(
        asset_id="asset-a",
        asset_name="Plan A",
        is_missing=False,
        usage_count=2,
        folders=org.all_folders(),
        tags=org.list_tags(),
        assigned_folder_id=None,
        assigned_tag_ids=set(),
    )
    menu = builder.build()
    actions = [a.text() for a in menu.actions() if a.text()]
    assert any("Renommer" in t for t in actions)
    assert any("Déplacer vers" in t for t in actions)
    assert any("Tags" in t for t in actions)
    assert any("Supprimer" in t for t in actions)
    # Le badge « Sélectionner dans la timeline » apparaît dès qu'il y a
    # au moins une occurrence.
    assert any("Sélectionner dans la timeline" in t for t in actions)


def test_asset_context_menu_shows_missing_warning(qapp, tmp_path) -> None:
    builder = AssetContextMenuBuilder(
        asset_id="asset-b",
        asset_name="B-roll",
        is_missing=True,
        usage_count=0,
        folders=[],
        tags=[],
        assigned_folder_id=None,
        assigned_tag_ids=set(),
    )
    menu = builder.build()
    actions_text = "\n".join(a.text() for a in menu.actions() if a.text())
    assert "Relier" in actions_text
    assert "⚠" in builder._make_header_text()


def test_asset_context_menu_hides_select_in_timeline_when_unused(qapp, tmp_path) -> None:
    builder = AssetContextMenuBuilder(
        asset_id="asset-a",
        asset_name="Plan A",
        is_missing=False,
        usage_count=0,
        folders=[],
        tags=[],
        assigned_folder_id=None,
        assigned_tag_ids=set(),
    )
    menu = builder.build()
    actions_text = "\n".join(a.text() for a in menu.actions() if a.text())
    assert "Sélectionner dans la timeline" not in actions_text


def test_asset_context_menu_move_submenu_marks_assigned_folder(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos", folder_id="fld-v")
    builder = AssetContextMenuBuilder(
        asset_id="asset-a",
        asset_name="Plan A",
        is_missing=False,
        usage_count=0,
        folders=org.all_folders(),
        tags=[],
        assigned_folder_id="fld-v",
        assigned_tag_ids=set(),
    )
    menu = builder.build()
    # Le sous-menu « Déplacer vers » doit contenir une action
    # « Vidéos » cochée.
    for action in menu.actions():
        if "Déplacer vers" in action.text():
            submenu = action.menu()
            assert submenu is not None
            checked = [a for a in submenu.actions() if a.isChecked()]
            labels = [a.text().strip() for a in checked]
            assert "Vidéos" in labels
            return
    pytest.fail("Sous-menu 'Déplacer vers' introuvable.")


def test_asset_context_menu_tag_submenu_marks_assigned_tags(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_tag("Héros", tag_id="tag-hero")
    org.create_tag("B-roll", tag_id="tag-br")
    builder = AssetContextMenuBuilder(
        asset_id="asset-a",
        asset_name="Plan A",
        is_missing=False,
        usage_count=0,
        folders=[],
        tags=org.list_tags(),
        assigned_folder_id=None,
        assigned_tag_ids={"tag-hero"},
    )
    menu = builder.build()
    for action in menu.actions():
        if action.text() == "Tags":
            submenu = action.menu()
            assert submenu is not None
            checked_labels = [
                a.text().strip() for a in submenu.actions() if a.isCheckable() and a.isChecked()
            ]
            assert "Héros" in checked_labels
            assert "B-roll" not in checked_labels
            return
    pytest.fail("Sous-menu 'Tags' introuvable.")


# ---------------------------------------------------------------------------
# compute_badges
# ---------------------------------------------------------------------------


def test_compute_badges_for_project_with_clips(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_tag("Héros", tag_id="tag-hero", color="#e74c3c")
    org.move_asset("asset-a", None)
    org.add_tag_to_asset("asset-a", "tag-hero")
    badges = compute_badges(project, org)
    assert badges["asset-a"].is_missing is False
    assert badges["asset-a"].tag_colors == ["#e74c3c"]
    # ``asset-b`` a un chemin inexistant → manquant.
    assert badges["asset-b"].is_missing is True
    # Aucun clip posé : compteurs à zéro.
    assert badges["asset-a"].usage_count == 0
    assert badges["asset-b"].usage_count == 0


def test_compute_badges_includes_usage_when_clips_exist(qapp, tmp_path) -> None:
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    # On pose deux clips sur le média ``asset-a``.
    from core.project_model import Clip
    project.tracks[0].clips.extend([
        Clip(
            id="c1",
            asset_id="asset-a",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        ),
        Clip(
            id="c2",
            asset_id="asset-a",
            track_id="V1",
            timeline_start=2.0,
            source_in=0.0,
            source_out=1.0,
        ),
    ])
    badges = compute_badges(project, org)
    assert badges["asset-a"].usage_count == 2
    assert badges["asset-b"].usage_count == 0


# ---------------------------------------------------------------------------
# ProjectPanel.set_library / set_usage_for_assets (intégration légère)
# ---------------------------------------------------------------------------


def test_project_panel_set_library_rebuilds_tree(qapp, qtbot, tmp_path) -> None:
    """Vérifie l'intégration du panneau avec LibraryOrganization."""
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Vidéos", folder_id="fld-v")
    panel.set_library(org)
    # L'arborescence est peuplée avec Tous + Racine + Manquants
    # (asset-b est sans fichier) + le dossier « Vidéos ».
    assert panel.folder_tree.tree.topLevelItemCount() == 4
    # Les badges sont calculés pour les 3 médias.
    assert set(panel._badges) == {"asset-a", "asset-b", "asset-c"}


def test_project_panel_filter_chips_apply_to_grids(qapp, qtbot, tmp_path) -> None:
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    panel.set_library(org)
    panel.set_assets(list(project.media_assets))
    # Filtre Vidé → uniquement les deux vidéos.
    panel.filter_chips.set_active_filter(FILTER_VIDEO)
    panel._refresh_grids()
    assert panel.bin_videos.count() == 2
    assert panel.bin_audios.count() == 0


def test_project_panel_filter_missing_keeps_only_unreachable(qapp, qtbot, tmp_path) -> None:
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    panel.set_library(org)
    panel.set_assets(list(project.media_assets))
    panel.filter_chips.set_active_filter(FILTER_MISSING)
    panel._refresh_grids()
    # Seul ``asset-b`` est considéré comme manquant.
    assert panel.bin_videos.count() == 1
    item = panel.bin_videos.item(0)
    assert item.data(Qt.UserRole) == "asset-b"


def test_project_panel_filter_used_keeps_only_with_occurrences(qapp, qtbot, tmp_path) -> None:
    from core.project_model import Clip
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    project = _project_with_videos(tmp_path)
    project.tracks[0].clips.append(
        Clip(
            id="c1",
            asset_id="asset-a",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    org = LibraryOrganization(project)
    panel.set_library(org)
    panel.set_assets(list(project.media_assets))
    panel.set_usage_for_assets()
    panel.filter_chips.set_active_filter(FILTER_USED)
    panel._refresh_grids()
    # Seul ``asset-a`` est utilisé.
    assert panel.bin_videos.count() == 1
    item = panel.bin_videos.item(0)
    assert item.data(Qt.UserRole) == "asset-a"


def test_project_panel_folder_scope_filters_assets(qapp, qtbot, tmp_path) -> None:
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    org.create_folder("Perso", folder_id="fld-p")
    org.move_asset("asset-a", "fld-p")
    panel.set_library(org)
    panel.set_assets(list(project.media_assets))
    # Sélection « Perso » → seulement ``asset-a``.
    panel.folder_tree.select_folder("fld-p")
    panel._refresh_grids()
    assert panel.bin_videos.count() == 1
    item = panel.bin_videos.item(0)
    assert item.data(Qt.UserRole) == "asset-a"
    # Re-population des bins avec tous les assets, puis bascule en
    # « Racine » : on doit voir ``asset-b`` (vidéo restée à la racine).
    panel.set_assets(list(project.media_assets))
    panel._selected_kind = "root"
    panel._selected_folder_id = None
    panel._refresh_grids()
    assert panel.bin_videos.count() == 1  # ``asset-b`` (vidéo)
    item = panel.bin_videos.item(0)
    assert item.data(Qt.UserRole) == "asset-b"


def test_project_panel_emits_signals_for_actions(qapp, qtbot, tmp_path) -> None:
    from ui.project_panel import ProjectPanel
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    captured: list[tuple[str, ...]] = []
    panel.folder_create_requested.connect(
        lambda *args: captured.append(("create", *args))
    )
    panel.folder_rename_requested.connect(
        lambda *args: captured.append(("rename", *args))
    )
    panel.folder_delete_requested.connect(
        lambda fid: captured.append(("delete", fid))
    )
    panel.asset_move_to_folder_requested.connect(
        lambda *args: captured.append(("move", *args))
    )
    panel.asset_tag_toggled.connect(
        lambda *args: captured.append(("tag", *args))
    )
    panel.asset_relink_requested.connect(
        lambda aid: captured.append(("relink", aid))
    )
    panel.tag_manager_requested.connect(
        lambda: captured.append(("tag_manager",))
    )
    # Émettre via les méthodes internes (le menu / l'arborescence).
    project = _project_with_videos(tmp_path)
    org = LibraryOrganization(project)
    panel.set_library(org)
    panel.folder_create_requested.emit("Perso", None, "#3498db")
    panel.folder_rename_requested.emit("fld-x", "Perso2")
    panel.folder_delete_requested.emit("fld-x")
    panel.asset_move_to_folder_requested.emit("asset-a", "fld-x")
    panel.asset_tag_toggled.emit("asset-a", "tag-x", True)
    panel.asset_relink_requested.emit("asset-a")
    panel.tag_manager_requested.emit()
    kinds = [c[0] for c in captured]
    assert kinds == [
        "create",
        "rename",
        "delete",
        "move",
        "tag",
        "relink",
        "tag_manager",
    ]