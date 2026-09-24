"""Tests d'intégration UI ↔ Project (Qt offscreen).

Ces tests vérifient que ``MainWindow`` utilise bien ``Project`` comme
source de vérité pour la timeline, et que les opérations UI (couper,
supprimer, éditer un sous-titre) sont propagées au modèle métier.
"""

import json
import pathlib

import pytest

from core.project_model import Project
from core.timeline_operations import find_clip
from core.timeline_view_model import build_clip_views


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_window(qtbot, monkeypatch):
    """Construit une MainWindow configurée pour les tests offscreen."""
    from ui.main_window import MainWindow

    # On neutralise les boîtes de dialogue pour ne pas bloquer les tests.
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    window = MainWindow()
    qtbot.addWidget(window)
    return window


# ---------------------------------------------------------------------------
# Project comme source de vérité
# ---------------------------------------------------------------------------


def test_main_window_owns_a_real_project(qtbot, monkeypatch) -> None:
    """MainWindow expose un vrai Project métier au démarrage."""
    window = _build_window(qtbot, monkeypatch)

    assert isinstance(window.project, Project)
    assert window.project.name == "Projet sans titre"


def test_main_window_timeline_reflects_project(qtbot, monkeypatch) -> None:
    """La timeline de MainWindow est une projection de self.project."""
    window = _build_window(qtbot, monkeypatch)

    assert window.timeline_panel.project is window.project
    expected_views = build_clip_views(window.project)
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    expected_ids = {v.id for v in expected_views}
    assert rendered_ids == expected_ids
    # Au moins les clips de démo sont présents.
    assert {"intro", "plan_a", "b_roll", "subtitle_01"}.issubset(rendered_ids)


# ---------------------------------------------------------------------------
# Opérations métier déclenchées par MainWindow
# ---------------------------------------------------------------------------


def test_main_window_cut_modifies_project_and_refreshes(qtbot, monkeypatch) -> None:
    """Une coupe via MainWindow modifie self.project et la timeline se reconstruit."""
    window = _build_window(qtbot, monkeypatch)

    initial_clip_count = sum(len(t.clips) for t in window.project.tracks)
    intro = find_clip(window.project, "intro")
    cut_at = intro.timeline_start + intro.duration / 2

    window.cut_selected_clip("intro", cut_at)

    new_clip_count = sum(len(t.clips) for t in window.project.tracks)
    assert new_clip_count == initial_clip_count + 1

    # La projection est rafraîchie : on doit voir l'id généré "-split-2".
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    assert "intro-split-2" in rendered_ids


def test_main_window_delete_modifies_project(qtbot, monkeypatch) -> None:
    """Une suppression via MainWindow retire le clip du Project."""
    window = _build_window(qtbot, monkeypatch)

    window.delete_selected_clip("b_roll")

    with pytest.raises(KeyError):
        find_clip(window.project, "b_roll")
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    assert "b_roll" not in rendered_ids


def test_main_window_subtitle_editor_updates_project(qtbot, tmp_path, monkeypatch) -> None:
    """Éditer le sous-titre dans l'inspecteur met à jour Clip.text et la vue.

    Le fichier ``.srt`` est écrit dans ``tmp_path`` pour ne pas polluer
    la racine du dépôt.
    """
    window = _build_window(qtbot, monkeypatch)
    window.subtitle_file = str(tmp_path / "subtitles.srt")

    # Sélectionner le clip sous-titre : on simule le clic en passant l'ID.
    window.on_clip_selected("subtitle_01")

    new_text = "Bienvenue dans la nouvelle version"
    window.properties_panel.subtitle_editor.setPlainText(new_text)

    # Clip.text dans le Project est mis à jour.
    updated = find_clip(window.project, "subtitle_01")
    assert updated.text == new_text

    # La projection reflète aussi la modification.
    view = window.timeline_panel.find_view_by_id("subtitle_01")
    assert view is not None
    assert view.text == new_text

    # Le ``.srt`` a bien été écrit dans le dossier temporaire.
    assert (tmp_path / "subtitles.srt").exists()


def test_main_window_subtitle_save_failure_does_not_break_ui(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Une ``OSError`` pendant la sauvegarde ``.srt`` ne casse ni le modèle ni la vue."""
    window = _build_window(qtbot, monkeypatch)
    window.subtitle_file = str(tmp_path / "subtitles.srt")

    window.on_clip_selected("subtitle_01")

    def boom(*args, **kwargs):
        raise OSError("disk full simulation")

    # On remplace la fonction locale ``save_subtitles`` utilisée par MainWindow.
    monkeypatch.setattr("ui.main_window.save_subtitles", boom)

    # L'édition du sous-titre ne doit lever aucune exception Qt.
    new_text = "Sauvegarde impossible"
    window.properties_panel.subtitle_editor.setPlainText(new_text)

    # Le modèle et la vue reflètent quand même la modification.
    updated = find_clip(window.project, "subtitle_01")
    assert updated.text == new_text

    view = window.timeline_panel.find_view_by_id("subtitle_01")
    assert view is not None
    assert view.text == new_text


def test_subtitles_srt_is_not_tracked_in_git() -> None:
    """Le fichier ``subtitles.srt`` ne doit plus apparaître dans l'index Git."""
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "ls-files", "subtitles.srt"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    assert result.stdout.strip() == "", (
        f"subtitles.srt est encore suivi par git : {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Aucune dépendance à l'ancien module timeline_model dans l'UI
# ---------------------------------------------------------------------------


def test_ui_does_not_import_legacy_timeline_model() -> None:
    """L'interface ne référence plus ``core.timeline_model`` (provisoirement gardé pour ses tests)."""
    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "ui"
    offenders: list[str] = []
    for py_file in sorted(ui_dir.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        if "timeline_model" in content:
            offenders.append(py_file.name)
    assert not offenders, (
        "Les fichiers ui/ suivants importent encore timeline_model : "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Tâche 5 — Nouveau / Ouvrir / Enregistrer (.kut)
# ---------------------------------------------------------------------------


def _fake_save_dialog(path: str):
    """Construit un stub pour ``QFileDialog.getSaveFileName``."""
    return (path, "Projets Kut-Studio (*.kut)")


def _fake_open_dialog(path: str):
    """Construit un stub pour ``QFileDialog.getOpenFileName``."""
    return (path, "Projets Kut-Studio (*.kut)")


def test_new_project_resets_state(qtbot, monkeypatch) -> None:
    """``new_project`` réinitialise le Project et ``current_project_path``."""
    from core.project_factory import create_default_project

    window = _build_window(qtbot, monkeypatch)

    # On simule un projet précédemment chargé et modifié.
    window.current_project_path = "/tmp/fake.kut"
    window.on_move_clip_requested("intro", 1.0)
    assert window.project_dirty is True

    window.new_project()

    assert isinstance(window.project, Project)
    assert window.project.name == "Projet sans titre"
    assert window.current_project_path is None
    assert window.project_dirty is False
    assert window.timeline_panel.project is window.project
    assert window.timeline_panel.selected_clip_id is None
    # L'inspecteur est remis à zéro.
    assert window.properties_panel.selected_clip is None
    # Le nouveau projet est bien celui issu du factory.
    fresh = create_default_project()
    assert [v.id for v in window.timeline_panel.clip_views] == [
        v.id for v in fresh.tracks for v in build_clip_views(fresh) if v.track_id
    ] or sum(len(t.clips) for t in window.project.tracks) == sum(
        len(t.clips) for t in fresh.tracks
    )


def test_save_as_creates_a_valid_kut_file(qtbot, tmp_path, monkeypatch) -> None:
    """``save_project_as`` écrit un fichier ``.kut`` valide et mémorise le chemin."""
    from core.project_io import load_project

    window = _build_window(qtbot, monkeypatch)
    target = tmp_path / "my_project.kut"

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )

    window.save_project_as()

    assert target.exists()
    assert window.current_project_path == str(target)
    assert window.project_dirty is False

    # Le fichier est un ``.kut`` re-chargeable.
    loaded = load_project(str(target))
    assert loaded.name == window.project.name
    assert [t.id for t in loaded.tracks] == [t.id for t in window.project.tracks]
    # Les clips et sous-titres sont conservés.
    src_sub = next(c for t in window.project.tracks for c in t.clips if c.id == "subtitle_01")
    dst_sub = next(c for t in loaded.tracks for c in t.clips if c.id == "subtitle_01")
    assert dst_sub.text == src_sub.text


def test_save_as_adds_kut_extension_if_missing(qtbot, tmp_path, monkeypatch) -> None:
    """Si l'utilisateur omet ``.kut``, l'extension est ajoutée automatiquement."""
    window = _build_window(qtbot, monkeypatch)
    target_without_ext = tmp_path / "no_extension"

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target_without_ext)),
    )

    window.save_project_as()

    assert (tmp_path / "no_extension.kut").exists()
    assert window.current_project_path == str(tmp_path / "no_extension.kut")


def test_save_project_file_reuses_existing_path(
    qtbot, tmp_path, monkeypatch
) -> None:
    """``save_project_file`` n'ouvre pas de dialogue si un chemin est déjà connu."""
    from core.project_io import load_project

    window = _build_window(qtbot, monkeypatch)
    target = tmp_path / "reused.kut"

    # On prépare un premier enregistrement pour définir ``current_project_path``.
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )
    window.save_project_as()
    assert target.exists()

    # On dirty le projet, puis on appelle ``save_project_file`` : aucun
    # dialogue ne doit être ouvert, et le fichier doit être réécrit.
    window.on_move_clip_requested("intro", 2.0)
    assert window.project_dirty is True

    dialog_calls = []
    def fail_dialog(*args, **kwargs):
        dialog_calls.append(args)
        return _fake_save_dialog(str(target))

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName", fail_dialog
    )

    window.save_project_file()

    assert dialog_calls == [], (
        "save_project_file ne doit pas rouvrir de dialogue quand "
        "current_project_path est déjà défini."
    )
    assert window.project_dirty is False
    # Le fichier est bien celui écrit par save_project_as.
    assert load_project(str(target)).name == window.project.name


def test_open_project_loads_complete_state(qtbot, tmp_path, monkeypatch) -> None:
    """``open_project_file`` recharge un projet complet, clips et sous-titres inclus."""
    from core.project_factory import create_default_project
    from core.project_io import save_project

    window = _build_window(qtbot, monkeypatch)

    # On crée un fichier ``.kut`` contenant un projet connu.
    source = create_default_project()
    source.name = "Chargé depuis .kut"
    sub = next(c for t in source.tracks for c in t.clips if c.id == "subtitle_01")
    sub.text = "Sous-titre rechargé"
    target = tmp_path / "loadable.kut"
    save_project(source, str(target))

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: _fake_open_dialog(str(target)),
    )

    window.open_project_file()

    assert window.project.name == "Chargé depuis .kut"
    assert window.current_project_path == str(target)
    assert window.project_dirty is False
    assert window.timeline_panel.project is window.project

    # Clips et sous-titres sont intégralement rechargés.
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    assert {"intro", "plan_a", "b_roll", "subtitle_01"} == rendered_ids
    loaded_sub = next(
        c for t in window.project.tracks for c in t.clips if c.id == "subtitle_01"
    )
    assert loaded_sub.text == "Sous-titre rechargé"


def test_open_invalid_file_keeps_current_project(qtbot, tmp_path, monkeypatch) -> None:
    """L'ouverture d'un fichier invalide conserve le projet affiché intact."""
    window = _build_window(qtbot, monkeypatch)
    initial_name = window.project.name
    initial_clips = sum(len(t.clips) for t in window.project.tracks)

    invalid = tmp_path / "broken.kut"
    invalid.write_text("{this is not valid json", encoding="utf-8")

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: _fake_open_dialog(str(invalid)),
    )

    window.open_project_file()

    # Le projet courant n'a pas été remplacé.
    assert window.project.name == initial_name
    assert sum(len(t.clips) for t in window.project.tracks) == initial_clips
    assert window.current_project_path is None
    assert window.project_dirty is False


def test_open_structurally_invalid_kut_keeps_current_project(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Un ``.kut`` au bon format mais avec un clip incomplet est rejeté proprement.

    Le fichier contient un ``format`` et une ``version`` valides, une
    section ``project`` correctement formée en apparence, mais un clip
    réduit à ``{}``. La désérialisation lève alors un ``TypeError``
    (champs requis manquants sur le dataclass ``Clip``). L'interface doit
    intercepter cette erreur, ne pas remplacer le projet courant et
    signaler le problème via ``QMessageBox.critical``.
    """
    window = _build_window(qtbot, monkeypatch)
    initial_name = window.project.name
    initial_clip_count = sum(len(t.clips) for t in window.project.tracks)
    initial_track_count = len(window.project.tracks)
    initial_asset_count = len(window.project.media_assets)

    # On capture les appels à ``QMessageBox.critical`` pour vérifier
    # que le mécanisme d'erreur en place a bien été déclenché.
    critical_calls: list[tuple] = []
    def fake_critical(parent, title, message, *args, **kwargs):
        critical_calls.append((title, message))

    monkeypatch.setattr("ui.main_window.QMessageBox.critical", fake_critical)

    broken = tmp_path / "broken_structure.kut"
    payload = {
        "format": "kut-studio-project",
        "version": 1,
        "project": {
            "name": "Projet cassé",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [
                {
                    "id": "V1",
                    "name": "V1",
                    "type": "video",
                    "clips": [{}],  # Clip complètement vide → TypeError
                },
            ],
        },
    }
    broken.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: _fake_open_dialog(str(broken)),
    )

    # L'appel ne doit lever aucune exception Qt (pas de propagation).
    window.open_project_file()

    # Le projet courant est strictement identique à son état initial.
    assert window.project.name == initial_name
    assert sum(len(t.clips) for t in window.project.tracks) == initial_clip_count
    assert len(window.project.tracks) == initial_track_count
    assert len(window.project.media_assets) == initial_asset_count
    assert window.current_project_path is None
    assert window.project_dirty is False

    # Une erreur a été signalée via le mécanisme en place.
    assert critical_calls, (
        "QMessageBox.critical aurait dû être appelé pour signaler "
        "le fichier structurellement invalide."
    )
    title, message = critical_calls[0]
    assert "projet" in title.lower() or "ouvrir" in title.lower()
    assert str(broken) in message


def test_timeline_modification_marks_project_dirty(qtbot, monkeypatch) -> None:
    """Toute modification de timeline marque le projet comme non enregistré."""
    window = _build_window(qtbot, monkeypatch)
    assert window.project_dirty is False

    window.on_move_clip_requested("intro", 2.0)
    assert window.project_dirty is True


def test_subtitle_edit_marks_project_dirty(qtbot, tmp_path, monkeypatch) -> None:
    """L'édition du sous-titre marque le projet comme non enregistré."""
    window = _build_window(qtbot, monkeypatch)
    window.subtitle_file = str(tmp_path / "subtitles.srt")
    window.on_clip_selected("subtitle_01")

    assert window.project_dirty is False
    window.properties_panel.subtitle_editor.setPlainText("Nouveau texte")
    assert window.project_dirty is True


def test_save_project_resets_dirty_state(qtbot, tmp_path, monkeypatch) -> None:
    """Après une sauvegarde réussie, l'indicateur revient à « Enregistré »."""
    window = _build_window(qtbot, monkeypatch)
    target = tmp_path / "saved.kut"

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )

    # On dirty le projet via une opération.
    window.on_move_clip_requested("intro", 2.0)
    assert window.project_dirty is True

    window.save_project_as()

    assert window.project_dirty is False
    assert "Enregistré" in window.saved_indicator.text()
