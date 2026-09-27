"""Tests du modèle d'espace de travail (sérialisation, cohérence).

Ces tests ciblent :mod:`core.workspace_state`, volontairement pur (aucune
dépendance Qt) afin que l'état d'interface soit vérifiable sans démarrer
l'application. Ils garantissent notamment qu'un fichier de préférences
corrompu ne peut jamais empêcher l'application de démarrer.
"""

import json

import pytest

from core.workspace_state import (
    BUILTIN_WORKSPACES,
    DEFAULT_SIZE,
    MIN_SIZE,
    WORKSPACES_DIR_NAME,
    DockArea,
    FloatingGeometry,
    PanelId,
    PanelState,
    WorkspaceState,
    delete_named_workspace,
    list_named_workspaces,
    load_named_workspace,
    load_workspace_state,
    save_named_workspace,
    save_workspace_state,
    workspace_file_path,
)


# ---------------------------------------------------------------------------
# Construction par défaut
# ---------------------------------------------------------------------------


def test_default_state_exposes_every_panel_visible():
    state = WorkspaceState.default()
    for panel in PanelId:
        assert state.is_visible(panel)
        assert not state.is_floating(panel)


def test_default_state_places_panels_in_expected_areas():
    state = WorkspaceState.default()
    assert state.area_of(PanelId.MEDIA) is DockArea.LEFT
    assert state.area_of(PanelId.VIEWER) is DockArea.CENTER
    assert state.area_of(PanelId.INSPECTOR) is DockArea.RIGHT
    assert state.area_of(PanelId.TIMELINE) is DockArea.BOTTOM


def test_visible_panels_groups_by_area():
    state = WorkspaceState.default()
    assert state.visible_panels(DockArea.LEFT) == (PanelId.MEDIA,)
    assert state.visible_panels(DockArea.BOTTOM) == (PanelId.TIMELINE,)


def test_hidden_panel_disappears_from_its_area():
    state = WorkspaceState.default().with_panel(PanelId.INSPECTOR, visible=False)
    assert state.visible_panels(DockArea.RIGHT) == ()
    assert not state.is_visible(PanelId.INSPECTOR)


def test_floating_panel_leaves_its_area_but_keeps_return_zone():
    state = WorkspaceState.default().with_panel(PanelId.TIMELINE, floating=True)
    assert state.visible_panels(DockArea.BOTTOM) == ()
    assert state.area_of(PanelId.TIMELINE) is DockArea.BOTTOM


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_size_is_clamped_to_minimum():
    state = WorkspaceState.default().with_panel(PanelId.TIMELINE, size=1)
    assert state.get(PanelId.TIMELINE).size == MIN_SIZE[PanelId.TIMELINE]


def test_size_defaults_when_zero():
    state = WorkspaceState.default().with_panel(PanelId.TIMELINE, size=0)
    assert state.get(PanelId.TIMELINE).size == DEFAULT_SIZE[PanelId.TIMELINE]


def test_normalization_repairs_floating_panel_without_geometry():
    state = WorkspaceState.default().with_panel(PanelId.MEDIA, floating=True)
    assert state.is_floating(PanelId.MEDIA)
    assert state.floating_geometry(PanelId.MEDIA).width >= MIN_SIZE[PanelId.MEDIA]


def test_normalization_drops_geometry_of_non_floating_panel():
    state = WorkspaceState.default().with_floating_geometry(
        PanelId.VIEWER, FloatingGeometry(10, 10, 800, 600)
    )
    # Le panneau n'est pas flottant : sa géométrie ne doit pas survivre.
    assert state.floating_geometry(PanelId.VIEWER) is not None
    assert all(pid is not PanelId.VIEWER for pid, _ in state.floating)


def test_maximized_hidden_panel_is_cleared():
    state = (
        WorkspaceState.default()
        .with_panel(PanelId.MEDIA, visible=False)
        .with_maximized(PanelId.MEDIA)
    )
    assert state.maximized is None


def test_cannot_maximize_a_hidden_panel():
    state = WorkspaceState.default().with_panel(PanelId.MEDIA, visible=False)
    assert state.with_maximized(PanelId.MEDIA).maximized is None


def test_state_is_immutable():
    state = WorkspaceState.default()
    with pytest.raises(Exception):
        state.center_ratio = 0.9  # type: ignore[misc]


def test_with_panel_returns_a_new_object():
    state = WorkspaceState.default()
    updated = state.with_panel(PanelId.MEDIA, visible=False)
    assert updated is not state
    assert state.is_visible(PanelId.MEDIA)  # l'original est intact


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------


def test_round_trip_preserves_layout():
    state = (
        WorkspaceState.default()
        .with_panel(PanelId.INSPECTOR, size=345)
        .with_panel(PanelId.TIMELINE, floating=True, visible=True)
        .with_floating_geometry(
            PanelId.TIMELINE, FloatingGeometry(120, 90, 1400, 800)
        )
        .with_center_ratio(0.42)
    )
    assert WorkspaceState.from_dict(state.to_dict()) == state


def test_json_is_valid_and_versioned():
    payload = json.loads(WorkspaceState.default().to_json())
    assert payload["version"] >= 1
    assert {entry["panel"] for entry in payload["panels"]} == {
        p.value for p in PanelId
    }


def test_from_dict_ignores_unknown_panel():
    state = WorkspaceState.from_dict(
        {"panels": [{"panel": "scopes", "visible": True}, {"panel": "timeline"}]}
    )
    assert set(state.to_dict()["panels"][0]) >= {"panel"}
    # Le panneau inconnu est ignoré, les autres restent présents.
    assert {entry.panel for entry in state.panels} == set(PanelId)


def test_from_dict_falls_back_on_garbage():
    assert WorkspaceState.from_dict("nope") == WorkspaceState.default()
    assert WorkspaceState.from_dict(None) == WorkspaceState.default()
    assert WorkspaceState.from_dict({"panels": 12}) == WorkspaceState.default()


def test_from_dict_rejects_invalid_area():
    state = WorkspaceState.from_dict({"panels": [{"panel": "timeline", "area": "x"}]})
    assert state.area_of(PanelId.TIMELINE) is DockArea.BOTTOM


def test_from_dict_falls_back_on_out_of_range_center_ratio():
    # Un fichier corrompu retombe sur la valeur par défaut (convention
    # appliquée aux préférences utilisateur) au lieu d'être clampé.
    assert WorkspaceState.from_dict({"center_ratio": 5.0}).center_ratio == 0.5
    assert WorkspaceState.from_dict({"center_ratio": -3}).center_ratio == 0.5


def test_with_center_ratio_clamps_live_values():
    state = WorkspaceState.default()
    assert state.with_center_ratio(4.0).center_ratio == 0.9
    assert state.with_center_ratio(0.0).center_ratio == 0.1


def test_maximized_serializes_as_identifier():
    state = WorkspaceState.default().with_maximized(PanelId.VIEWER)
    assert json.loads(state.to_json())["maximized"] == "viewer"


# ---------------------------------------------------------------------------
# Persistance sur disque
# ---------------------------------------------------------------------------


def test_load_returns_default_when_file_absent(tmp_path):
    assert load_workspace_state(tmp_path) == WorkspaceState.default()


def test_load_returns_default_on_invalid_json(tmp_path):
    (tmp_path / "workspace.json").write_text("{ not json", encoding="utf-8")
    assert load_workspace_state(tmp_path) == WorkspaceState.default()


def test_save_then_load_round_trip(tmp_path):
    state = (
        WorkspaceState.default()
        .with_panel(PanelId.MEDIA, visible=False)
        .with_panel(PanelId.TIMELINE, floating=True)
        .with_floating_geometry(
            PanelId.TIMELINE, FloatingGeometry(0, 0, 1200, 700)
        )
    )
    path = save_workspace_state(state, tmp_path)
    assert path.exists()
    assert load_workspace_state(tmp_path) == state


def test_workspace_file_is_separate_from_user_settings(tmp_path):
    from core.user_settings import FILE_NAME as SETTINGS_FILE

    workspace_path = workspace_file_path(tmp_path)
    assert workspace_path.parent == tmp_path
    assert workspace_path.name != SETTINGS_FILE


def test_save_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "deeper"
    save_workspace_state(WorkspaceState.default(), target)
    assert (target / "workspace.json").exists()


def test_save_is_atomic(tmp_path, monkeypatch):
    import core.workspace_state as module

    original = module.os.replace

    def boom(*args, **kwargs):
        raise OSError("échec simulé")

    monkeypatch.setattr(module.os, "replace", boom)
    with pytest.raises(OSError):
        save_workspace_state(WorkspaceState.default(), tmp_path)
    # Aucun fichier temporaire ne doit être laissé derrière.
    assert list(tmp_path.glob("workspace.json.*")) == []
    monkeypatch.setattr(module.os, "replace", original)


# ---------------------------------------------------------------------------
# Contrat d'identifiants
# ---------------------------------------------------------------------------


def test_panel_ids_are_stable_strings():
    # Ces identifiants sont persistés : les changer casserait les
    # préférences existantes.
    assert PanelId.TIMELINE.value == "timeline"
    assert PanelId.VIEWER.value == "viewer"
    assert PanelId.MEDIA.value == "media"
    assert PanelId.INSPECTOR.value == "inspector"


def test_every_panel_has_a_label():
    for panel in PanelId:
        assert panel.label()


# ---------------------------------------------------------------------------
# Espaces de travail nommés
# ---------------------------------------------------------------------------


def test_builtin_workspaces_are_listed(tmp_path):
    names = list_named_workspaces(tmp_path)
    for key in BUILTIN_WORKSPACES:
        assert key in names


def test_builtin_workspace_hides_the_expected_panel(tmp_path):
    audio = load_named_workspace("audio", tmp_path)
    assert audio is not None
    assert not audio.is_visible(PanelId.VIEWER)
    editing = load_named_workspace("editing", tmp_path)
    assert editing == WorkspaceState.default()


def test_unknown_workspace_returns_none(tmp_path):
    assert load_named_workspace("inexistant", tmp_path) is None


def test_save_and_load_named_workspace(tmp_path):
    state = WorkspaceState.default().with_panel(
        PanelId.TIMELINE, floating=True
    )
    assert save_named_workspace("Mon montage", state, tmp_path).exists()
    assert load_named_workspace("Mon montage", tmp_path) == state


def test_named_workspace_appears_in_listing(tmp_path):
    save_named_workspace("Custom", WorkspaceState.default(), tmp_path)
    assert "custom" in list_named_workspaces(tmp_path)


def test_custom_workspace_overrides_builtin(tmp_path):
    custom = WorkspaceState.default().with_panel(PanelId.VIEWER, visible=False)
    save_named_workspace("audio", custom, tmp_path)
    assert load_named_workspace("audio", tmp_path) == custom


def test_delete_workspace(tmp_path):
    save_named_workspace("ASupprimer", WorkspaceState.default(), tmp_path)
    # L'interface manipule les identifiants listés (les slugs), pas le
    # nom saisi : la suppression doit suivre exactement cette convention.
    listed = [n for n in list_named_workspaces(tmp_path) if n == "asupprimer"]
    assert listed, "l'espace enregistré doit apparaître dans la liste"
    assert delete_named_workspace("asupprimer", tmp_path) is True
    assert load_named_workspace("asupprimer", tmp_path) is None


def test_delete_unknown_workspace_returns_false(tmp_path):
    assert delete_named_workspace("jamais-vu", tmp_path) is False


def test_builtin_workspaces_cannot_be_deleted(tmp_path):
    assert delete_named_workspace("editing", tmp_path) is False
    assert "editing" in list_named_workspaces(tmp_path)


def test_named_workspaces_live_in_their_own_directory(tmp_path):
    from core.user_settings import FILE_NAME as SETTINGS_FILE

    path = save_named_workspace("X", WorkspaceState.default(), tmp_path)
    assert path.parent.name == WORKSPACES_DIR_NAME
    assert path.name != SETTINGS_FILE
    # Ni le projet ni la disposition courante ne sont concernés.
    assert not (tmp_path / "workspace.json").exists()
