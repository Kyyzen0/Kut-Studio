"""Intégrité des données entre le modèle, le fichier ``.kut`` et l'historique.

Chaque test décrit une perte de données ou un faux état constatés pendant l'audit de stabilisation :
l'automation effacée au premier réglage après réouverture, le faux « Enregistré » après annulation,
les fichiers corrompus qui lèvent autre chose qu'un refus propre.
"""

from __future__ import annotations

import json

import pytest

from core.audio_automation import AudioAutomationService
from core.edit_history import MAX_HISTORY, ProjectHistory
from core.project_io import load_project, project_payload, save_project
from core.project_model import Marker, MediaAsset, Project, Track

from rich_project import build_rich_project


def _audio_project() -> Project:
    asset = MediaAsset(id="a", path="/tmp/a.mp3", name="A", duration=10.0, width=0, height=0, fps=0.0,
                       media_type="audio", has_audio=True)
    return Project(name="mix", media_assets=[asset], tracks=[Track(id="M1", name="Musique", type="audio")])


def _points(project: Project) -> list[tuple[float, float]]:
    """Points d'automation de la 1re piste. Le modèle les porte en liste après un chargement et en
    ``TrackAutomation`` une fois le service passé : deux représentations, les deux sont lues."""
    automation = project.tracks[0].automation
    return [(p.time_seconds, p.gain_db) for p in getattr(automation, "points", automation)]


# --- Automation audio --------------------------------------------------------------------------------------------


def test_automation_points_survive_the_first_edit_after_reopening(tmp_path):
    """Avant : le premier réglage après l'ouverture remplaçait toute la courbe par une courbe vide."""
    service = AudioAutomationService()
    project = _audio_project()
    service.add_automation_point(project, "M1", 1.0, -6.0)
    service.add_automation_point(project, "M1", 3.0, -12.0, 0.2)
    path = tmp_path / "a.kut"
    save_project(project, str(path))

    loaded = load_project(str(path))
    service.add_automation_point(loaded, "M1", 5.0, -3.0)
    assert _points(loaded) == [(1.0, -6.0), (3.0, -12.0), (5.0, -3.0)]


def test_a_reloaded_automation_can_be_updated_and_points_removed(tmp_path):
    service = AudioAutomationService()
    project = _audio_project()
    service.add_automation_point(project, "M1", 1.0, -6.0)
    service.add_automation_point(project, "M1", 3.0, -12.0)
    path = tmp_path / "a.kut"
    save_project(project, str(path))

    loaded = load_project(str(path))
    service.update_automation_point(loaded, "M1", 1.0, gain_db=-9.0)
    service.remove_automation_point(loaded, "M1", 3.0)
    assert _points(loaded) == [(1.0, -9.0)]


def test_the_edited_automation_round_trips_again(tmp_path):
    service = AudioAutomationService()
    project = _audio_project()
    service.add_automation_point(project, "M1", 1.0, -6.0)
    path = tmp_path / "a.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    service.add_automation_point(loaded, "M1", 2.0, -2.0)
    save_project(loaded, str(path))
    assert _points(load_project(str(path))) == [(1.0, -6.0), (2.0, -2.0)]


# --- « Enregistré » ne ment jamais -------------------------------------------------------------------------------


def _history() -> tuple[ProjectHistory, Project]:
    project = Project(name="v0")
    history = ProjectHistory()
    history.reset(project)
    return history, project


def _edit(history: ProjectHistory, project: Project, name: str) -> None:
    project.name = name
    history.record(project, name)


def test_a_fresh_project_is_clean_and_an_edit_makes_it_dirty():
    history, project = _history()
    assert not history.is_dirty
    _edit(history, project, "v1")
    assert history.is_dirty
    history.mark_saved()
    assert not history.is_dirty


def test_undoing_to_the_saved_state_is_clean_again():
    history, project = _history()
    _edit(history, project, "v1")
    history.mark_saved()
    _edit(history, project, "v2")
    assert history.is_dirty
    history.undo()
    assert not history.is_dirty


def test_editing_after_undoing_past_the_saved_state_is_never_reported_saved():
    """Avant : « Enregistré » alors que le contenu différait du fichier (l'index sauvegardé était réutilisé)."""
    history, project = _history()
    _edit(history, project, "v1")
    history.mark_saved()                       # le fichier contient v1
    history.undo()                             # retour à v0 : l'état v1 est dans la branche « redo »
    assert history.is_dirty
    _edit(history, project, "autre")           # la branche redo (donc v1) est jetée
    assert history.is_dirty


def test_a_saved_state_pushed_out_of_the_history_is_never_reported_saved():
    history, project = _history()
    for index in range(MAX_HISTORY + 5):       # l'état initial (enregistré) est évincé
        _edit(history, project, f"n{index}")
    while history.can_undo:
        history.undo()
    assert history.is_dirty, "la plus ancienne entrée restante n'est pas l'état du fichier"


# --- Aller-retour et fichiers abîmés -------------------------------------------------------------------------------


def test_the_rich_project_round_trips_to_an_identical_payload(tmp_path):
    project = build_rich_project()
    path = tmp_path / "rich.kut"
    save_project(project, str(path))
    once = project_payload(load_project(str(path)))
    assert once == project_payload(project)
    save_project(load_project(str(path)), str(path))
    assert project_payload(load_project(str(path))) == once       # idempotent : rien ne dérive à chaque cycle


def test_a_project_opens_without_any_cache_or_proxy(tmp_path, monkeypatch):
    """Les caches ne sont jamais nécessaires pour ouvrir un projet."""
    for name in ("KUT_STUDIO_CACHE_DIR", "KUT_STUDIO_PROXY_DIR", "KUT_STUDIO_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "absent" / name.lower()))
    path = tmp_path / "rich.kut"
    save_project(build_rich_project(), str(path))
    assert load_project(str(path)).name == "Riche"


@pytest.fixture(scope="module")
def rich_text(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("kut") / "rich.kut"
    save_project(build_rich_project(), str(path))
    return path.read_text(encoding="utf-8")


def _load_text(tmp_path, text: str) -> Project:
    path = tmp_path / "m.kut"
    path.write_text(text, encoding="utf-8")
    return load_project(str(path))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_non_finite_numbers_are_refused_with_a_clear_message(tmp_path, rich_text, token):
    """Avant : le projet s'ouvrait, puis ne pouvait plus être enregistré (Ctrl+S sans le moindre message)."""
    mutated = rich_text.replace('"duration": 20.0', f'"duration": {token}', 1)
    assert mutated != rich_text
    with pytest.raises(ValueError, match="non finie|hors limites"):
        _load_text(tmp_path, mutated)


def test_a_deeply_nested_file_is_refused_not_crashed(tmp_path):
    with pytest.raises(ValueError):
        _load_text(tmp_path, '{"format": "kut-studio", "version": 16, "project": ' + "[" * 100_000 + "]" * 100_000 + "}")


def _dicts(node, path=()):
    """Tous les objets JSON du document, avec leur chemin."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _dicts(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _dicts(value, path + (index,))


def _every_key_deletion(document: dict):
    """Liste de ``(chemin, clé)`` : chaque clé de chaque objet, supprimée une à une."""
    return [(path, key) for path, node in _dicts(document) for key in node]


def test_deleting_any_single_key_never_raises_an_unhandled_exception(tmp_path, rich_text):
    """Mutation systématique : le chargement réussit ou refuse par ValueError / TypeError, jamais autrement.

    Avant : un repère sans ``id``, une keyframe sans ``property_name``, etc. levaient KeyError, que l'interface
    n'attrapait pas (aucun message, état à moitié chargé).
    """
    document = json.loads(rich_text)
    failures = []
    for path, key in _every_key_deletion(document):
        mutated = json.loads(rich_text)
        node = mutated
        for step in path:
            node = node[step]
        del node[key]
        try:
            _load_text(tmp_path, json.dumps(mutated))
        except (ValueError, TypeError):
            pass
        except Exception as error:  # noqa: BLE001 - c'est précisément ce qu'on traque
            failures.append((path, key, type(error).__name__, str(error)[:60]))
    assert not failures, f"{len(failures)} suppressions lèvent une exception interne, ex. {failures[:5]}"


def test_a_project_that_cannot_be_written_says_why(tmp_path):
    project = Project(name="NaN")
    project.markers.append(Marker("m", 1.0))
    object.__setattr__(project.markers[0], "time_seconds", float("nan"))
    with pytest.raises(ValueError, match="valeur non finie"):
        save_project(project, str(tmp_path / "x.kut"))
    assert not list(tmp_path.glob("*.tmp")), "aucun fichier temporaire résiduel"
