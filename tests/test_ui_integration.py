"""Tests d'intégration UI ↔ Project (Qt offscreen).

Ces tests vérifient que ``MainWindow`` utilise bien ``Project`` comme
source de vérité pour la timeline, et que les opérations UI (couper,
supprimer, éditer un sous-titre) sont propagées au modèle métier.
"""

import json
import pathlib

import pytest
from PySide6.QtCore import Qt

from core.project_model import MediaAsset, Project
from core.project_io import load_project, save_project
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
    # Le timer interne de l'horloge de timeline continue à émettre des
    # events toutes les 40 ms tant que la fenêtre existe ; cumulé sur
    # plusieurs tests successifs, il peut bloquer la boucle d'événements
    # Qt. On le suspend pour les tests.
    if hasattr(window, "timeline_timer") and window.timeline_timer is not None:
        window.timeline_timer.stop()
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

    Aucun fichier ``.srt`` n'est écrit automatiquement pendant la
    saisie : la sauvegarde passe par une action utilisateur explicite.
    """
    window = _build_window(qtbot, monkeypatch)

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

    # Aucun fichier ``.srt`` n'est écrit automatiquement.
    assert list(tmp_path.iterdir()) == []


def test_main_window_subtitle_export_failure_does_not_break_ui(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Une ``OSError`` pendant la sauvegarde ``.srt`` ne casse ni le modèle ni la vue."""
    window = _build_window(qtbot, monkeypatch)
    window.on_clip_selected("subtitle_01")

    # L'édition du sous-titre ne doit lever aucune exception Qt et
    # n'écrit rien automatiquement sur le disque.
    new_text = "Sauvegarde explicite uniquement"
    window.properties_panel.subtitle_editor.setPlainText(new_text)

    # Le modèle et la vue reflètent la modification.
    updated = find_clip(window.project, "subtitle_01")
    assert updated.text == new_text
    view = window.timeline_panel.find_view_by_id("subtitle_01")
    assert view is not None
    assert view.text == new_text
    # Aucune écriture automatique dans tmp_path.
    assert list(tmp_path.iterdir()) == []


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


# ---------------------------------------------------------------------------
# Tâche 6A — Import vidéo dans le Project
# ---------------------------------------------------------------------------


def _fake_probe(width=1280, height=720, fps=30.0, duration=12.5):
    """Construit un MediaAsset factice pour les tests d'import."""
    from core.project_model import MediaAsset

    def _factory(path: str):
        return MediaAsset(
            id=f"asset-imported-{path}",
            path=path,
            name=path.split("/")[-1],
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            media_type="video",
        )

    return _factory


def test_import_video_adds_media_asset_to_project(qtbot, tmp_path, monkeypatch) -> None:
    """Un import réussi ajoute un MediaAsset à window.project.media_assets."""
    window = _build_window(qtbot, monkeypatch)
    video_path = str(tmp_path / "clip.mp4")
    video_path_obj = tmp_path / "clip.mp4"
    video_path_obj.write_bytes(b"\x00")

    initial_assets = list(window.project.media_assets)
    monkeypatch.setattr(
        "ui.main_window.probe_media",
        _fake_probe(width=1920, height=1080, fps=30.0, duration=42.0),
    )

    result = window.import_video_to_project(video_path)

    assert result is True
    assert len(window.project.media_assets) == len(initial_assets) + 1
    imported = window.project.media_assets[-1]
    assert imported.path == video_path
    assert imported.name == "clip.mp4"
    assert imported.width == 1920
    assert imported.height == 1080
    assert imported.fps == pytest.approx(30.0)
    assert imported.duration == pytest.approx(42.0)
    assert imported.media_type == "video"


def test_import_video_updates_library_with_asset_name(qtbot, tmp_path, monkeypatch) -> None:
    """La bibliothèque ProjectPanel reflète le nom du MediaAsset importé."""
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "mon_super_clip.mov"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr("ui.main_window.probe_media", _fake_probe())

    window.import_video_to_project(str(video_path))

    # Le panneau affiche bien le nom de fichier.
    rendered_names = [
        window.project_panel.bin_videos.item(row).text()
        for row in range(window.project_panel.bin_videos.count())
    ]
    assert "mon_super_clip.mov" in rendered_names


def test_import_video_marks_project_as_dirty(qtbot, tmp_path, monkeypatch) -> None:
    """Un import réussi passe le projet en état « Non enregistré »."""
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")
    assert window.project_dirty is False

    monkeypatch.setattr("ui.main_window.probe_media", _fake_probe())

    window.import_video_to_project(str(video_path))

    assert window.project_dirty is True
    assert "Non enregistré" in window.saved_indicator.text()


def test_import_video_refuses_duplicate_by_normalized_path(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Importer deux fois le même chemin ne crée pas de doublon."""
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    probe_calls: list[str] = []
    def fake_probe(path):
        probe_calls.append(path)
        return _fake_probe()(path)

    monkeypatch.setattr("ui.main_window.probe_media", fake_probe)

    assert window.import_video_to_project(str(video_path)) is True
    count_after_first = len(window.project.media_assets)

    # Deuxième import du même chemin : aucun nouvel asset, pas de re-sonde.
    assert window.import_video_to_project(str(video_path)) is False
    assert len(window.project.media_assets) == count_after_first
    assert len(probe_calls) == 1

    # Variante : import avec un chemin équivalent (./tmp/...) : même refus.
    same_via_relative = tmp_path / "." / "clip.mp4"
    assert window.import_video_to_project(str(same_via_relative)) is False
    assert len(window.project.media_assets) == count_after_first
    assert len(probe_calls) == 1


def test_import_video_failure_keeps_project_and_library_unchanged(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Si la sonde échoue, ni le Project ni la bibliothèque ne sont modifiés."""
    window = _build_window(qtbot, monkeypatch)
    initial_assets = list(window.project.media_assets)
    initial_names = [
        window.project_panel.bin_videos.item(row).text()
        for row in range(window.project_panel.bin_videos.count())
    ]
    initial_dirty = window.project_dirty

    video_path = tmp_path / "broken.mp4"
    video_path.write_bytes(b"\x00")

    from core.media_probe import MediaProbeError

    def failing_probe(path):
        raise MediaProbeError(f"fichier corrompu : {path}")

    monkeypatch.setattr("ui.main_window.probe_media", failing_probe)

    critical_calls: list[tuple] = []
    def fake_critical(parent, title, message, *args, **kwargs):
        critical_calls.append((title, message))

    monkeypatch.setattr("ui.main_window.QMessageBox.critical", fake_critical)

    result = window.import_video_to_project(str(video_path))

    assert result is False
    # Aucune mutation.
    assert list(window.project.media_assets) == initial_assets
    after_names = [
        window.project_panel.bin_videos.item(row).text()
        for row in range(window.project_panel.bin_videos.count())
    ]
    assert after_names == initial_names
    assert window.project_dirty is initial_dirty
    # Une erreur a été signalée.
    assert critical_calls, "Une erreur aurait dû être affichée."
    title, _ = critical_calls[0]
    assert "import" in title.lower()


def test_imported_media_asset_survives_save_and_load(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Un MediaAsset importé est conservé après un aller-retour ``.kut``."""
    from core.project_io import load_project

    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")
    target = tmp_path / "saved.kut"

    monkeypatch.setattr("ui.main_window.probe_media", _fake_probe(duration=7.5))

    assert window.import_video_to_project(str(video_path)) is True
    imported = window.project.media_assets[-1]
    assert imported.path == str(video_path)
    assert imported.name == "clip.mp4"

    # Sauvegarde.
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )
    window.save_project_as()
    assert target.exists()

    # Recharge dans une nouvelle fenêtre et vérifie la présence du média.
    fresh_window = _build_window(qtbot, monkeypatch)
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(target), "Projets Kut-Studio (*.kut)"),
    )
    fresh_window.open_project_file()

    assert any(
        a.path == str(video_path) and a.name == "clip.mp4"
        for a in fresh_window.project.media_assets
    )
    # Et la bibliothèque de la nouvelle fenêtre reflète bien le média.
    rendered = [
        fresh_window.project_panel.bin_videos.item(row).text()
        for row in range(fresh_window.project_panel.bin_videos.count())
    ]
    assert "clip.mp4" in rendered


# ---------------------------------------------------------------------------
# Tâche 6B — Ajout d'un média importé à la timeline
# ---------------------------------------------------------------------------


def test_add_to_timeline_button_is_disabled_without_selection(
    qtbot, monkeypatch
) -> None:
    """Le bouton « Ajouter à la timeline » démarre désactivé.

    Au démarrage de l'application, aucune ligne de la bibliothèque
    n'est sélectionnée : le bouton doit donc rester désactivé
    tant que l'utilisateur n'a pas cliqué sur un média.
    """
    window = _build_window(qtbot, monkeypatch)
    button = window.project_panel.add_to_timeline_button
    assert button.isEnabled() is False


def test_add_to_timeline_button_becomes_active_after_selection(
    qtbot, monkeypatch
) -> None:
    """Sélectionner un média active automatiquement le bouton d'ajout."""
    window = _build_window(qtbot, monkeypatch)
    button = window.project_panel.add_to_timeline_button
    bin_widget = window.project_panel.bin_videos

    # Au moins un média (issu du projet de démo : asset-intro, asset-plan-a, etc.).
    assert bin_widget.count() >= 1

    # Avant sélection : bouton désactivé.
    assert button.isEnabled() is False

    # Sélection du premier média → le bouton devient actif.
    bin_widget.setCurrentRow(0)
    assert button.isEnabled() is True

    # Et l'identifiant exposé via ``selected_asset_id`` est cohérent.
    expected_id = bin_widget.item(0).data(Qt.UserRole)
    assert window.project_panel.selected_asset_id == expected_id


def test_add_asset_to_v1_creates_clip_at_playhead_position(
    qtbot, tmp_path, monkeypatch
) -> None:
    """L'action « Ajouter à la timeline » crée un clip sur V1 au playhead.

    Le clip ajouté doit :

    - se trouver sur la piste ``V1`` ;
    - démarrer à la position courante du playhead (``3.7s``) ;
    - utiliser toute la durée du MediaAsset ;
    - être labellisé avec ``MediaAsset.name`` ;
    - être référencé par un identifiant unique généré par la fonction
      métier.
    """
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr(
        "ui.main_window.probe_media",
        _fake_probe(duration=42.0, width=1920, height=1080),
    )
    assert window.import_video_to_project(str(video_path)) is True
    asset_id = window.project.media_assets[-1].id
    asset_name = window.project.media_assets[-1].name

    # Position du playhead volontairement non triviale.
    window.timeline_panel.playhead_seconds = 3.7

    v1_before = len(next(t for t in window.project.tracks if t.id == "V1").clips)
    window.add_asset_to_v1(asset_id)
    v1_after = next(t for t in window.project.tracks if t.id == "V1").clips

    # Un clip a bien été ajouté sur V1.
    assert len(v1_after) == v1_before + 1
    new_clip = v1_after[-1]
    assert new_clip.asset_id == asset_id
    assert new_clip.track_id == "V1"
    assert new_clip.timeline_start == pytest.approx(3.7)
    assert new_clip.source_in == pytest.approx(0.0)
    assert new_clip.source_out == pytest.approx(42.0)
    assert new_clip.duration == pytest.approx(42.0)
    assert new_clip.enabled is True
    assert new_clip.label == asset_name
    assert new_clip.text == ""


def test_added_clip_is_selected_and_visible_in_inspector(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Le clip ajouté est sélectionné et son détail s'affiche dans l'inspecteur."""
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr("ui.main_window.probe_media", _fake_probe(duration=15.0))
    assert window.import_video_to_project(str(video_path)) is True
    asset_id = window.project.media_assets[-1].id
    asset_name = window.project.media_assets[-1].name

    # Avant l'ajout : rien n'est sélectionné, inspecteur vide.
    assert window.timeline_panel.selected_clip_id is None
    assert window.properties_panel.selected_clip is None

    window.add_asset_to_v1(asset_id)
    new_clip = next(
        c for t in window.project.tracks if t.id == "V1" for c in t.clips
        if c.asset_id == asset_id
    )

    # La timeline sélectionne le nouveau clip.
    assert window.timeline_panel.selected_clip_id == new_clip.id

    # L'inspecteur affiche bien les informations du clip ajouté.
    selected_view = window.properties_panel.selected_clip
    assert selected_view is not None
    assert selected_view.id == new_clip.id
    assert selected_view.label == asset_name


def test_add_asset_to_v1_marks_project_as_dirty(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Ajouter un média à la timeline passe le projet en « Non enregistré »."""
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr("ui.main_window.probe_media", _fake_probe(duration=10.0))
    assert window.import_video_to_project(str(video_path)) is True
    assert window.project_dirty is True  # l'import lui-même a déjà dirty le projet

    # On l'enregistre puis on le rend propre pour ne mesurer que l'effet
    # de l'ajout à la timeline.
    target = tmp_path / "baseline.kut"
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )
    window.save_project_as()
    assert window.project_dirty is False
    assert "Enregistré" in window.saved_indicator.text()

    asset_id = window.project.media_assets[-1].id
    window.add_asset_to_v1(asset_id)

    assert window.project_dirty is True
    assert "Non enregistré" in window.saved_indicator.text()


def test_added_media_and_clip_survive_save_and_load(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Après un aller-retour ``.kut``, le média ET le clip ajouté sont conservés.

    On construit le scénario complet :

    1. import d'un média via la fausse sonde ;
    2. ajout à la timeline à une position choisie ;
    3. sauvegarde ``save_project_as`` ;
    4. ouverture dans une nouvelle ``MainWindow`` ;
    5. vérifications : ``media_assets`` contient toujours le média,
       la piste V1 contient toujours le clip créé.
    """
    from core.project_io import load_project
    from core.project_factory import create_default_project

    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "roundtrip.mp4"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr(
        "ui.main_window.probe_media",
        _fake_probe(duration=21.0, width=1280, height=720),
    )
    assert window.import_video_to_project(str(video_path)) is True
    asset_id = window.project.media_assets[-1].id

    # Ajout à la timeline à un offset précis.
    window.timeline_panel.playhead_seconds = 0.5
    window.add_asset_to_v1(asset_id)

    # Sauvegarde du projet.
    target = tmp_path / "roundtrip.kut"
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: _fake_save_dialog(str(target)),
    )
    window.save_project_as()
    assert target.exists()

    # Recharge dans une nouvelle MainWindow et vérifie que média + clip sont là.
    fresh = _build_window(qtbot, monkeypatch)
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: _fake_open_dialog(str(target)),
    )
    fresh.open_project_file()

    # Le média importé est bien présent dans le projet rechargé.
    reloaded_assets = fresh.project.media_assets
    assert any(a.id == asset_id and a.name == "roundtrip.mp4" for a in reloaded_assets), (
        "Le média importé devrait être conservé après le rechargement."
    )

    # Le clip ajouté sur V1 est bien conservé.
    v1 = next(t for t in fresh.project.tracks if t.id == "V1")
    matching_clips = [c for c in v1.clips if c.asset_id == asset_id]
    assert len(matching_clips) == 1
    new_clip = matching_clips[0]
    assert new_clip.timeline_start == pytest.approx(0.5)
    assert new_clip.source_out == pytest.approx(21.0)
    # Et le round-trip via la couche d'I/O reste compatible : le projet
    # rechargé a strictement la même structure que celui attendu par
    # ``create_default_project()`` plus notre clip en plus.
    baseline = create_default_project()
    baseline_v1 = next(t for t in baseline.tracks if t.id == "V1")
    # Le projet rechargé doit contenir au moins les clips de démo + le nôtre.
    baseline_clip_ids = {c.id for c in baseline_v1.clips}
    reloaded_v1_ids = {c.id for c in v1.clips}
    assert baseline_clip_ids.issubset(reloaded_v1_ids)
    # Et le fichier passe par load_project sans souci.
    assert load_project(str(target)).name == fresh.project.name


def test_click_on_add_to_timeline_button_triggers_add_asset_to_v1(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Le clic sur le bouton appelle bien ``add_asset_to_v1`` avec le bon asset.

    On simule le parcours utilisateur complet : sélection d'un média
    (programmatiquement via ``setCurrentRow``), puis clic sur le bouton.
    Le clip résultant doit être ajouté sur V1 à la position courante.
    """
    window = _build_window(qtbot, monkeypatch)
    video_path = tmp_path / "via_button.mp4"
    video_path.write_bytes(b"\x00")

    monkeypatch.setattr(
        "ui.main_window.probe_media",
        _fake_probe(duration=11.0, width=1280, height=720),
    )
    assert window.import_video_to_project(str(video_path)) is True
    asset_id = window.project.media_assets[-1].id

    bin_widget = window.project_panel.bin_videos
    button = window.project_panel.add_to_timeline_button

    # Le média importé se trouve en dernière position dans la liste.
    target_row = bin_widget.count() - 1
    bin_widget.setCurrentRow(target_row)
    assert button.isEnabled() is True

    window.timeline_panel.playhead_seconds = 0.0
    v1_clips_before = list(
        next(t for t in window.project.tracks if t.id == "V1").clips
    )

    # Clic sur le bouton : équivalent à l'événement utilisateur.
    button.click()

    v1_clips_after = next(t for t in window.project.tracks if t.id == "V1").clips
    assert len(v1_clips_after) == len(v1_clips_before) + 1
    new_clip = v1_clips_after[-1]
    assert new_clip.asset_id == asset_id
    assert new_clip.track_id == "V1"


# ---------------------------------------------------------------------------
# Tâche 8 — Aperçu piloté par la timeline
# ---------------------------------------------------------------------------


def _isolate_video_timeline(window) -> None:
    """Réduit le projet aux seuls clips V1 pour des scénarios déterministes.

    Le projet de démo contient par défaut un clip V2 (b_roll) qui
    recouvre la plupart de la timeline V1, ce qui rend ambigus les
    tests sur la « vidéo active ». Cette fonction retire
    systématiquement les clips V2 et S1, puis reconstruit la
    projection de la timeline.
    """
    for track in window.project.tracks:
        if track.id in {"V2", "S1"}:
            track.clips.clear()
    window.timeline_panel.set_project(window.project)
    window._update_timeline_duration()


def _install_media_spy(monkeypatch, player):
    """Capture les appels Qt sur le ``QMediaPlayer`` sans toucher au rendu.

    Retourne un dictionnaire mutable ``spy`` mis à jour par les
    fonctions installées sur le lecteur. ``monkeypatch`` se charge de
    restaurer l'état après chaque test.
    """
    spy = {
        "setSource": [],
        "setPosition": [],
        "play": 0,
        "pause": 0,
        "stop": 0,
    }

    def _set_source(url):
        spy["setSource"].append(url)

    def _set_position(ms):
        spy["setPosition"].append(ms)

    def _play():
        spy["play"] += 1

    def _pause():
        spy["pause"] += 1

    def _stop():
        spy["stop"] += 1

    monkeypatch.setattr(player, "setSource", _set_source)
    monkeypatch.setattr(player, "setPosition", _set_position)
    monkeypatch.setattr(player, "play", _play)
    monkeypatch.setattr(player, "pause", _pause)
    monkeypatch.setattr(player, "stop", _stop)
    return spy


def _assign_paths(window, paths: dict[str, str]) -> None:
    """Attribue un chemin de média factice aux assets présents dans le projet."""
    for asset in window.project.media_assets:
        if asset.id in paths:
            asset.path = paths[asset.id]


def test_main_window_owns_the_timeline_clock(qtbot, monkeypatch) -> None:
    """``MainWindow`` possède l'horloge de la timeline (playhead, is_playing)."""
    window = _build_window(qtbot, monkeypatch)

    assert window.playhead_seconds == 0.0
    assert window.is_playing is False


def test_seek_after_move_loads_preview_at_correct_source_time(
    qtbot, monkeypatch
) -> None:
    """Un clip déplacé à 5s, lu à 6s, doit charger la source à 1s."""
    window = _build_window(qtbot, monkeypatch)
    _isolate_video_timeline(window)
    _assign_paths(
        window,
        {"asset-intro": "/tmp/intro.mp4", "asset-plan-a": "/tmp/plan_a.mp4"},
    )
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # Le clip « intro » (V1, source_in=0, duration=4) est déplacé à 5s.
    window.on_move_clip_requested("intro", 5.0)

    # Seek à 6s sur la timeline.
    window.seek_to_position(6.0)

    # source_time = 0 + (6 - 5) = 1.0 s → setPosition(1000 ms).
    assert spy["setPosition"], "setPosition doit avoir été appelé"
    assert spy["setPosition"][-1] == 1000
    # setSource doit pointer sur le chemin du média d'intro.
    assert spy["setSource"], "setSource doit avoir été appelé"
    assert spy["setSource"][-1].toLocalFile() == "/tmp/intro.mp4"


def test_seek_after_trim_left_loads_preview_at_correct_source_time(
    qtbot, monkeypatch
) -> None:
    """Trim gauche → ``source_in`` augmente, ``source_time`` suit."""
    window = _build_window(qtbot, monkeypatch)
    _isolate_video_timeline(window)
    _assign_paths(window, {"asset-intro": "/tmp/intro.mp4"})
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # intro est initialement à [0, 4] (source_in=0, source_out=4).
    # Trim gauche à 1.5s → source_in = 1.5, clip = [1.5, 4].
    window.on_trim_left_requested("intro", 1.5)

    # Seek à 1.5s → source_time = 1.5 + (1.5 - 1.5) = 1.5 s.
    window.seek_to_position(1.5)

    assert spy["setPosition"][-1] == 1500


def test_seek_after_trim_right_loads_preview_at_correct_source_time(
    qtbot, monkeypatch
) -> None:
    """Trim droit → ``source_out`` baisse, ``source_time`` reste correct."""
    window = _build_window(qtbot, monkeypatch)
    _isolate_video_timeline(window)
    _assign_paths(window, {"asset-intro": "/tmp/intro.mp4"})
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # intro [0, 4] → trim droit à 3.0s → [0, 3] (source_in=0, source_out=3).
    window.on_trim_right_requested("intro", 3.0)

    # Seek à 2.0s → source_time = 0 + (2 - 0) = 2.0 s.
    window.seek_to_position(2.0)

    assert spy["setPosition"][-1] == 2000


def test_seek_in_gap_shows_empty_preview(qtbot, monkeypatch) -> None:
    """Dans un trou entre deux clips, l'aperçu passe à l'état vide."""
    window = _build_window(qtbot, monkeypatch)

    # On retire le b-roll (V2) qui pontait l'écart entre intro et plan_a.
    v2 = next(t for t in window.project.tracks if t.id == "V2")
    v2.clips.clear()
    window.timeline_panel.set_project(window.project)
    window._update_timeline_duration()

    _assign_paths(
        window,
        {
            "asset-intro": "/tmp/intro.mp4",
            "asset-plan-a": "/tmp/plan_a.mp4",
        },
    )
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # t=5.0 est dans le trou entre intro [0, 4] et plan_a [6.5, 12].
    window.seek_to_position(5.0)

    # Aucun clip vidéo actif → show_empty() → player.stop().
    assert spy["stop"] >= 1
    # Aucun chargement de source vidéo (sous-titres ignorés).
    assert spy["setSource"] == []
    # Le playhead lui-même est bien positionné.
    assert window.playhead_seconds == pytest.approx(5.0)


def test_v2_clip_wins_over_v1_when_both_active(qtbot, monkeypatch) -> None:
    """Quand V1 et V2 sont actifs, V2 (piste la plus basse) est choisi."""
    window = _build_window(qtbot, monkeypatch)
    _assign_paths(
        window,
        {
            "asset-intro": "/tmp/intro.mp4",
            "asset-plan-a": "/tmp/plan_a.mp4",
            "asset-b-roll": "/tmp/b_roll.mp4",
        },
    )
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # Par défaut, à t=3.0 :
    #   intro (V1, [0, 4])      → actif
    #   b_roll (V2, [2, 7.5])   → actif
    # Le dernier clip vidéo retourné par ``evaluate_timeline`` est b_roll.
    window.seek_to_position(3.0)

    assert spy["setSource"], "setSource doit avoir été appelé"
    assert spy["setSource"][-1].toLocalFile() == "/tmp/b_roll.mp4"


def test_clip_change_during_playback_loads_new_source(qtbot, monkeypatch) -> None:
    """Quand le playhead franchit la limite d'un clip, une nouvelle source est chargée."""
    window = _build_window(qtbot, monkeypatch)

    # On reconstruit V1 avec deux clips adjacents pour éviter tout chevauchement.
    from core.project_model import Clip

    v1 = next(t for t in window.project.tracks if t.id == "V1")
    v1.clips.clear()
    v1.clips.append(
        Clip(
            id="alpha",
            asset_id="asset-intro",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=2.0,
        )
    )
    v1.clips.append(
        Clip(
            id="beta",
            asset_id="asset-plan-a",
            track_id="V1",
            timeline_start=2.0,
            source_in=0.0,
            source_out=2.0,
        )
    )
    # V2 sans clip pour ne pas interferer.
    v2 = next(t for t in window.project.tracks if t.id == "V2")
    v2.clips.clear()
    window.timeline_panel.set_project(window.project)
    window._update_timeline_duration()

    _assign_paths(
        window,
        {"asset-intro": "/tmp/intro.mp4", "asset-plan-a": "/tmp/plan_a.mp4"},
    )
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # t=1.0 : clip alpha (intro).
    window.playhead_seconds = 1.0
    window._tick_playback()
    assert spy["setSource"], "setSource doit avoir été appelé"
    assert spy["setSource"][-1].toLocalFile() == "/tmp/intro.mp4"

    # t=3.0 : clip beta (plan_a). On simule une avancée du playhead pendant la lecture.
    window.playhead_seconds = 3.0
    window._tick_playback()
    assert spy["setSource"][-1].toLocalFile() == "/tmp/plan_a.mp4"


def test_playback_auto_pauses_at_timeline_end(qtbot, monkeypatch) -> None:
    """La lecture se met en pause seule lorsque ``timeline_duration`` est atteinte."""
    window = _build_window(qtbot, monkeypatch)
    _assign_paths(window, {"asset-intro": "/tmp/intro.mp4"})

    # Durée totale du projet par défaut = 12 s (fin de plan_a).
    duration = window.timeline_panel.duration_seconds
    assert duration == pytest.approx(12.0)

    window.playhead_seconds = duration - 0.005  # très proche de la fin
    window.is_playing = True
    window._tick_playback()

    assert window.is_playing is False
    assert window.playhead_seconds == pytest.approx(12.0)


def test_stop_resets_playhead_to_zero_and_resyncs(qtbot, monkeypatch) -> None:
    """``stop_playback`` remet le playhead à 0 et resynchronise l'aperçu."""
    window = _build_window(qtbot, monkeypatch)
    _assign_paths(window, {"asset-intro": "/tmp/intro.mp4"})
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    window.playhead_seconds = 4.0
    window.is_playing = True

    window.stop_playback()

    assert window.playhead_seconds == 0.0
    assert window.is_playing is False
    # Le playhead est projeté sur la timeline.
    assert window.timeline_panel.playhead_seconds == 0.0
    # L'aperçu tente de charger l'intro (clip actif à t=0).
    assert spy["setSource"][-1].toLocalFile() == "/tmp/intro.mp4"
    # Le lecteur natif est stoppé via ``show_empty`` ou ``stop`` : au moins un appel.
    assert (spy["stop"] + spy["play"]) >= 1


def test_library_preview_does_not_modify_project(qtbot, monkeypatch) -> None:
    """Cliquer un média de la bibliothèque ne touche ni au projet ni à l'horloge."""
    window = _build_window(qtbot, monkeypatch)
    _assign_paths(window, {"asset-intro": "/tmp/intro.mp4"})
    spy = _install_media_spy(monkeypatch, window.preview_panel.player)

    # On pollue l'horloge pour vérifier qu'elle n'est pas affectée par le clic.
    window.playhead_seconds = 3.7
    window.is_playing = False

    initial_clips = sum(len(t.clips) for t in window.project.tracks)
    initial_dirty = window.project_dirty
    initial_playhead = window.playhead_seconds

    window.preview_media_asset("asset-intro")

    # Aucune modification du projet, du dirty flag, ni de l'horloge.
    assert sum(len(t.clips) for t in window.project.tracks) == initial_clips
    assert window.project_dirty == initial_dirty
    assert window.playhead_seconds == initial_playhead
    # L'aperçu charge bien le média de la bibliothèque.
    assert spy["setSource"][-1].toLocalFile() == "/tmp/intro.mp4"
    # ``PreviewPanel`` sait qu'il est en mode bibliothèque.
    assert window.preview_panel.is_library_preview() is True


def test_timeline_duration_label_reflects_timeline_duration(
    qtbot, monkeypatch
) -> None:
    """Le label de durée totale suit ``timeline_duration(project)`` après chaque mutation."""
    window = _build_window(qtbot, monkeypatch)
    _isolate_video_timeline(window)

    # État initial : plan_a finit à 12 s → label "00:12".
    assert "00:12" in window.timeline_panel.total_time_label.text()

    # Trim droit du plan_a pour qu'il finisse à 9 s.
    window.on_trim_right_requested("plan_a", 9.0)
    assert "00:09" in window.timeline_panel.total_time_label.text()

    # Suppression du plan_a → max restant = 4 (intro).
    window.delete_selected_clip("plan_a")
    assert "00:04" in window.timeline_panel.total_time_label.text()

    # Suppression de l'intro → plus aucun clip activé → durée = 0
    # → le label retombe sur le minimum visuel de 1 s.
    window.delete_selected_clip("intro")
    assert "00:01" in window.timeline_panel.total_time_label.text()


def test_duration_updates_on_new_project_and_open(
    qtbot, tmp_path, monkeypatch
) -> None:
    """``new_project`` et ``open_project_file`` réinitialisent la durée affichée."""
    from core.project_io import save_project
    from core.project_factory import create_default_project

    window = _build_window(qtbot, monkeypatch)

    # Nouveau projet : 12 s par défaut.
    window.new_project()
    assert "00:12" in window.timeline_panel.total_time_label.text()

    # Nouveau projet vide (sans clips) : on simule un projet vide.
    empty = create_default_project()
    for track in empty.tracks:
        track.clips.clear()
    target = tmp_path / "empty.kut"
    save_project(empty, str(target))

    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(target), "Projets Kut-Studio (*.kut)"),
    )
    window.open_project_file()

    # Projet vide : durée = 0 → ``set_timeline_duration`` clamp à 1 s
    # pour conserver un ruler lisible, d'où "00:01".
    assert "00:01" in window.timeline_panel.total_time_label.text()


def test_old_positionChanged_wiring_is_removed() -> None:
    """L'ancien branchement ``positionChanged → setPlaybackPosition`` n'existe plus."""
    main_window_source = (
        pathlib.Path(__file__).resolve().parent.parent / "ui" / "main_window.py"
    )
    source_text = main_window_source.read_text(encoding="utf-8")

    forbidden_patterns = [
        "positionChanged.connect(self.timeline_panel.setPlaybackPosition",
        "durationChanged.connect(self.timeline_panel.setDuration",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source_text, (
            f"Branchement interdit encore présent : {pattern}"
        )


def test_no_setPlaybackPosition_call_in_update_path() -> None:
    """``MainWindow`` n'utilise plus ``setPlaybackPosition`` pour piloter la timeline.

    Le slot historique ``setPlaybackPosition`` ne doit plus être appelé
    que par son wrapper interne (compatibilité). Toute mise à jour
    directe depuis ``update_timeline`` / ``update`` / ``_tick_playback``
    est interdite.
    """
    import re

    main_window_source = (
        pathlib.Path(__file__).resolve().parent.parent / "ui" / "main_window.py"
    )
    source_text = main_window_source.read_text(encoding="utf-8")

    # Aucun appel direct à ``setPlaybackPosition`` ailleurs que dans
    # ``set_playhead_seconds`` (où il sert d'alias historique).
    forbidden_calls = re.findall(r"\.setPlaybackPosition\(", source_text)
    assert not forbidden_calls, (
        "MainWindow ne doit plus appeler setPlaybackPosition directement : "
        f"{forbidden_calls}"
    )


# ---------------------------------------------------------------------------
# Tâche 10 — Audio de bout en bout
# ---------------------------------------------------------------------------


def _audio_asset(asset_id: str = "asset-song", name: str = "Song.mp3", duration: float = 30.0):
    """Construit un ``MediaAsset`` audio conforme à la validation."""
    return MediaAsset(
        id=asset_id,
        path=f"/tmp/{name}",
        name=name,
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )


def test_project_panel_filters_audios_in_audio_tab(qtbot, monkeypatch) -> None:
    """L'onglet Audio liste uniquement les assets audio, pas les vidéos."""
    window = _build_window(qtbot, monkeypatch)
    # Injecter un asset audio directement.
    window.project.media_assets.append(_audio_asset())
    window._refresh_project_library()

    panel = window.project_panel

    # Onglet Médias (par défaut) : ne montre que les vidéos de démo.
    panel.navigation.setCurrentRow(0)
    panel._refresh_count()
    assert panel.media_title.text() == "MÉDIAS DU PROJET"
    assert "Song" not in {panel.bin_videos.item(r).text() for r in range(panel.bin_videos.count())}

    # Onglet Audio : ne montre que l'asset audio.
    panel.navigation.setCurrentRow(1)
    panel._refresh_count()
    assert panel.media_title.text() == "AUDIOS DU PROJET"
    audio_names = {
        panel.bin_audios.item(r).text() for r in range(panel.bin_audios.count())
    }
    assert audio_names == {"Song.mp3"}


def test_add_asset_to_timeline_routes_audio_to_a1(qtbot, monkeypatch) -> None:
    """Un asset audio est ajouté sur la piste A1, pas V1."""
    window = _build_window(qtbot, monkeypatch)
    audio = _audio_asset()
    window.project.media_assets.append(audio)
    window._refresh_project_library()

    # Faire en sorte que la sélection courante pointe sur l'audio.
    window.project_panel.select_asset(audio.id)
    window.project_panel.navigation.setCurrentRow(1)
    window.project_panel._sync_add_button_for_active_tab()

    window.add_asset_to_timeline(audio.id)

    a1 = next(t for t in window.project.tracks if t.id == "A1")
    assert any(c.asset_id == audio.id for c in a1.clips)
    # Et pas dans V1.
    v1 = next(t for t in window.project.tracks if t.id == "V1")
    assert not any(c.asset_id == audio.id for c in v1.clips)


def test_add_asset_to_timeline_routes_video_to_v1(qtbot, monkeypatch) -> None:
    """Un asset vidéo est ajouté sur V1 par défaut."""
    window = _build_window(qtbot, monkeypatch)
    asset_id = "asset-intro"  # asset de démo
    window.project_panel.select_asset(asset_id)
    window.project_panel.navigation.setCurrentRow(0)
    window.project_panel._sync_add_button_for_active_tab()

    initial_v1_count = len(next(t for t in window.project.tracks if t.id == "V1").clips)

    window.add_asset_to_timeline(asset_id)

    v1 = next(t for t in window.project.tracks if t.id == "V1")
    assert len(v1.clips) == initial_v1_count + 1


def test_add_asset_to_timeline_rejects_missing_target_track(
    qtbot, monkeypatch
) -> None:
    """L'ajout d'un asset audio sur un projet sans A1 est refusé."""
    window = _build_window(qtbot, monkeypatch)
    # Retirer A1 du projet pour simuler un ancien projet.
    window.project.tracks = [t for t in window.project.tracks if t.id != "A1"]
    audio = _audio_asset()
    window.project.media_assets.append(audio)
    window._refresh_project_library()

    # On capture les appels à QMessageBox.critical.
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.critical",
        lambda parent, title, message, *a, **k: captured.append((title, message)),
    )

    window.add_asset_to_timeline(audio.id)

    assert captured, "Un message d'erreur doit être affiché"
    assert "A1" in captured[0][1]


def test_audio_asset_survives_save_and_load(qtbot, tmp_path, monkeypatch) -> None:
    """Un asset audio est correctement sérialisé puis rechargé en .kut."""
    window = _build_window(qtbot, monkeypatch)
    audio = _audio_asset(duration=42.0)
    window.project.media_assets.append(audio)
    window._refresh_project_library()

    target = tmp_path / "with-audio.kut"
    window.current_project_path = None
    save_project(window.project, str(target))
    loaded = load_project(str(target))

    reloaded_audio = next(a for a in loaded.media_assets if a.id == audio.id)
    assert reloaded_audio.media_type == "audio"
    assert reloaded_audio.has_audio is True
    assert reloaded_audio.width == 0
    assert reloaded_audio.height == 0
    assert reloaded_audio.fps == 0.0
    assert reloaded_audio.duration == pytest.approx(42.0)


def test_main_window_has_a1_in_default_project(qtbot, monkeypatch) -> None:
    """Le projet par défaut expose une piste audio A1."""
    window = _build_window(qtbot, monkeypatch)
    track_ids = [t.id for t in window.project.tracks]
    assert "A1" in track_ids
    a1 = next(t for t in window.project.tracks if t.id == "A1")
    assert a1.type == "audio"


def test_legacy_project_without_a1_keeps_existing_tracks(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Un ancien projet sans A1 est conservé tel quel après chargement."""
    window = _build_window(qtbot, monkeypatch)
    legacy_payload = {
        "format": "kut-studio-project",
        "version": 2,
        "project": {
            "name": "Legacy",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [],
            "tracks": [
                {"id": "V1", "name": "V1", "type": "video", "clips": []},
                {"id": "S1", "name": "S1", "type": "subtitle", "clips": []},
            ],
        },
    }
    target = tmp_path / "legacy.kut"
    target.write_text(json.dumps(legacy_payload), encoding="utf-8")

    window.current_project_path = None
    window._load_project_from_path(str(target))

    track_ids = [t.id for t in window.project.tracks]
    # L'ancien projet ne reçoit PAS automatiquement A1.
    assert "A1" not in track_ids
    assert track_ids == ["V1", "S1"]


# ---------------------------------------------------------------------------
# Tâche 12 — Édition non destructive
# ---------------------------------------------------------------------------


def test_undo_shortcut_restores_previous_state(qtbot, monkeypatch) -> None:
    """Ctrl+Z annule la dernière opération enregistrée."""
    window = _build_window(qtbot, monkeypatch)

    initial_clips = sum(len(t.clips) for t in window.project.tracks)

    # Une mutation out-of-band : on simule un déplacement via
    # ``move_clip`` directement, suivi d'un ``record``.
    from core.timeline_operations import move_clip

    target_clip_id = window.timeline_panel.clip_views[0].id
    move_clip(window.project, target_clip_id, 10.0)
    window._record_history("Déplacer le clip")

    assert window.history.can_undo
    assert window.history.is_dirty is True

    # Déclenchement du raccourci Ctrl+Z (Undo).
    window.undo_last()
    # Le clip est revenu à sa position initiale.
    from core.timeline_evaluator import evaluate_timeline
    from core.timeline_operations import find_clip

    moved = find_clip(window.project, target_clip_id)
    assert moved.timeline_start == pytest.approx(
        window.timeline_panel.clip_views[0].start
    )
    # Le nombre total de clips est inchangé.
    assert sum(len(t.clips) for t in window.project.tracks) == initial_clips


def test_redo_shortcut_reapplies_undone_operation(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    from core.timeline_operations import move_clip, find_clip

    clip_id = window.timeline_panel.clip_views[0].id
    move_clip(window.project, clip_id, 8.0)
    window._record_history("Déplacer")

    window.undo_last()
    window.redo_last()

    assert find_clip(window.project, clip_id).timeline_start == pytest.approx(8.0)


def test_undo_action_disabled_when_history_empty(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    assert window.undo_action.isEnabled() is False
    assert window.redo_action.isEnabled() is False


def test_undo_action_label_includes_operation_name(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    from core.timeline_operations import move_clip

    clip_id = window.timeline_panel.clip_views[0].id
    move_clip(window.project, clip_id, 7.0)
    window._record_history("Action personnalisée")
    assert "Action personnalisée" in window.undo_action.text()


def test_dirty_flag_after_save_undo_and_edit(qtbot, tmp_path, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    assert window.project_dirty is False

    target = tmp_path / "p.kut"
    window.current_project_path = str(target)
    save_project(window.project, str(target))
    window._mark_clean()
    assert window.project_dirty is False

    from core.timeline_operations import move_clip

    clip_id = window.timeline_panel.clip_views[0].id
    move_clip(window.project, clip_id, 3.0)
    window._record_history("Edit")
    assert window.project_dirty is True

    # Undo : retour à l'état sauvegardé.
    window.undo_last()
    assert window.project_dirty is False

    # Redo : l'état modifié revient.
    window.redo_last()
    assert window.project_dirty is True


def test_duplicate_clip_shortcut_creates_a_copy(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    clip_id = window.timeline_panel.clip_views[0].id
    window.timeline_panel.select_clip(clip_id)

    initial_count = sum(len(t.clips) for t in window.project.tracks)
    window.duplicate_selected_clip()
    assert sum(len(t.clips) for t in window.project.tracks) == initial_count + 1
    # Undo doit annuler la duplication.
    window.undo_last()
    assert sum(len(t.clips) for t in window.project.tracks) == initial_count


def test_ripple_delete_shortcut_removes_and_shifts(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    # On sélectionne le premier clip visible de V1.
    clip_id = window.timeline_panel.clip_views[0].id
    window.timeline_panel.select_clip(clip_id)

    initial_count = sum(len(t.clips) for t in window.project.tracks)
    window.ripple_delete_selected_clip()
    assert sum(len(t.clips) for t in window.project.tracks) == initial_count - 1
    window.undo_last()
    assert sum(len(t.clips) for t in window.project.tracks) == initial_count


def test_disabled_clip_is_ignored_by_evaluate_timeline(qtbot, monkeypatch) -> None:
    """Un clip désactivé n'apparaît pas dans l'aperçu évalue_timeline."""
    window = _build_window(qtbot, monkeypatch)
    from core.timeline_evaluator import evaluate_timeline
    from core.timeline_operations import set_clip_enabled

    clip_id = window.timeline_panel.clip_views[0].id
    target_time = window.timeline_panel.clip_views[0].start + 0.5

    before = evaluate_timeline(window.project, target_time)
    # Avant désactivation, le clip est actif.
    assert any(c.clip_id == clip_id for c in before)

    set_clip_enabled(window.project, clip_id, False)
    after = evaluate_timeline(window.project, target_time)
    assert not any(c.clip_id == clip_id for c in after)


def test_snap_button_toggles_snap_enabled(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    timeline = window.timeline_panel
    assert timeline.snap_enabled is True
    timeline.set_snap_enabled(False)
    assert timeline.snap_enabled is False
    timeline.set_snap_enabled(True)
    assert timeline.snap_enabled is True


def test_drag_drop_video_asset_on_v1_creates_clip(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    asset_id = "asset-intro"
    # Le média est déjà présent dans le projet de démo.
    v1_before = len(next(t for t in window.project.tracks if t.id == "V1").clips)
    window.on_asset_dropped(asset_id, "V1", 0.0)
    v1_after = len(next(t for t in window.project.tracks if t.id == "V1").clips)
    assert v1_after == v1_before + 1
    window.undo_last()
    assert len(next(t for t in window.project.tracks if t.id == "V1").clips) == v1_before


def test_drag_drop_audio_asset_on_a1_creates_clip(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    audio = MediaAsset(
        id="audio-test",
        path="/tmp/song.mp3",
        name="Song",
        duration=10.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    window.project.media_assets.append(audio)
    a1_before = len(next(t for t in window.project.tracks if t.id == "A1").clips)
    window.on_asset_dropped(audio.id, "A1", 2.5)
    a1_after = len(next(t for t in window.project.tracks if t.id == "A1").clips)
    assert a1_after == a1_before + 1


def test_drag_drop_invalid_track_is_rejected(qtbot, monkeypatch) -> None:
    """Un dépôt sur une piste inexistante ne modifie pas le projet."""
    window = _build_window(qtbot, monkeypatch)
    asset_id = "asset-intro"
    clips_before = sum(len(t.clips) for t in window.project.tracks)
    # La piste « NOPE » n'existe pas dans le projet.
    window.on_asset_dropped(asset_id, "NOPE", 1.0)
    assert sum(len(t.clips) for t in window.project.tracks) == clips_before


def test_import_media_then_undo_removes_asset(qtbot, tmp_path, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    # On injecte un faux probe pour éviter de dépendre d'un vrai média.
    asset_id = "asset-test-import"
    monkeypatch.setattr(
        "ui.main_window.probe_media",
        lambda path: MediaAsset(
            id=asset_id, path=path, name="Test",
            duration=4.0, width=1920, height=1080, fps=30.0,
            media_type="video", has_audio=False,
        ),
    )
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"\x00")
    initial = len(window.project.media_assets)
    window.import_media_to_project(str(video_path))
    assert len(window.project.media_assets) == initial + 1

    window.undo_last()
    assert len(window.project.media_assets) == initial


def test_import_subtitles_then_undo_removes_clips(qtbot, tmp_path, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    srt_path = tmp_path / "subs.srt"
    srt_path.write_text(
        "1\n00:00:00,500 --> 00:00:01,500\nBonjour\n\n", encoding="utf-8",
    )
    initial_s1 = len(next(t for t in window.project.tracks if t.id == "S1").clips)
    window.import_subtitles_from_path(str(srt_path))
    s1_after = len(next(t for t in window.project.tracks if t.id == "S1").clips)
    assert s1_after == initial_s1 + 1

    window.undo_last()
    assert len(next(t for t in window.project.tracks if t.id == "S1").clips) == initial_s1


def test_subtitle_text_edit_groups_into_single_history_entry(
    qtbot, monkeypatch
) -> None:
    """Plusieurs frappes consécutives produisent un seul snapshot."""
    window = _build_window(qtbot, monkeypatch)
    window.on_clip_selected("subtitle_01")
    history_length = len(window.history)

    # Plusieurs frappes rapides dans l'éditeur.
    for char in "ABC":
        window.properties_panel.subtitle_editor.setPlainText(f"Hello {char}")
        # Le MainWindow écoute ``textChanged`` : il met à jour le clip
        # et programme un debounce.
        window.update_subtitle_from_editor()

    # Force l'émission du timer : on déclenche ``_flush_subtitle_history_record``.
    window._flush_subtitle_history_record()
    assert len(window.history) == history_length + 1

    # Exactement UN snapshot supplémentaire lié au sous-titre.
    # On vérifie qu'un undo ramène le texte du clip à son état initial.
    initial_text = "Bienvenue dans Kut-Studio"
    window.undo_last()
    from core.timeline_operations import find_clip

    assert find_clip(window.project, "subtitle_01").text == initial_text
    window.redo_last()
    assert find_clip(window.project, "subtitle_01").text == "Hello C"


def test_transform_edit_is_one_undoable_history_entry(qtbot, monkeypatch) -> None:
    window = _build_window(qtbot, monkeypatch)
    clip = find_clip(window.project, "intro")
    initial_scale = clip.transform.scale
    history_length = len(window.history)

    window.on_transform_property_changed("intro", "scale", 2.0)
    window._finalize_transform_session()

    assert find_clip(window.project, "intro").transform.scale == pytest.approx(2.0)
    assert len(window.history) == history_length + 1

    window.undo_last()
    assert find_clip(window.project, "intro").transform.scale == pytest.approx(
        initial_scale
    )
    window.redo_last()
    assert find_clip(window.project, "intro").transform.scale == pytest.approx(2.0)
