"""Le rapport de chargement : ce qu'un ``.kut`` valide a perdu en silence (``load_project(path, report=...)``, ADR-0005).

Le chargement est strict pour la structure et tolérant pour les sous-objets décoratifs : un effet, un bus, une transition, une
association de ducking ou une entrée de bibliothèque invalide est ignoré **seul**. C'est voulu (un projet un peu abîmé reste ouvrable
dans l'interface), mais un fichier forgé pouvait être accepté en perdant du contenu sans le dire : ``validate`` ne peut pas se
contenter de « le chargement a réussi ». Ces tests injectent une entrée invalide dans chaque section comptée et vérifient le rapport.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.audio_buses import add_bus
from core.audio_effects_model import AudioEffectType, add_audio_effect_to_clip
from core.effects_model import EffectType, add_effect_to_clip
from core.project_io import LOAD_SECTIONS, LoadReport, load_project, save_project
from core.project_model import MediaAsset, Project, Track
from core.timeline_operations import add_clip_to_track


def _project() -> Project:
    project = Project(
        "p", width=64, height=36, fps=30.0,
        media_assets=[MediaAsset("m", "/x/m.mp4", "m", 10.0, 64, 36, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )
    clip = add_clip_to_track(project, "m", "V1", 0.0)
    add_effect_to_clip(project, clip.id, EffectType.BLUR)
    add_audio_effect_to_clip(project, clip.id, AudioEffectType.LIMITER)
    add_bus(project, "Voix")
    return project


def _save(tmp_path: Path, mutate=None) -> Path:
    path = tmp_path / "p.kut"
    save_project(_project(), str(path))
    if mutate is not None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        mutate(raw["project"])
        path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _clip(body: dict) -> dict:
    return body["sequences"][0]["tracks"][0]["clips"][0]


def _loaded(path: Path) -> LoadReport:
    report = LoadReport()
    load_project(str(path), report=report)
    return report


def test_an_intact_file_reports_nothing_and_loading_without_a_report_is_unchanged(tmp_path):
    path = _save(tmp_path)
    report = _loaded(path)
    assert report.intact and report.total == 0 and report.dropped == {}
    assert load_project(str(path)).name == "p"


def test_each_counted_section_reports_its_ignored_entries(tmp_path):
    junk = {"id": "x", "type": "nonexistent"}

    def corrupt(body: dict) -> None:
        clip = _clip(body)
        clip["effects"] += [junk, "texte", 3]                                  # 3 effets de clip invalides
        clip["audio_effects"] += [junk]                                        # 1 effet audio invalide
        sequence = body["sequences"][0]
        sequence["buses"] += [{"id": ""}, "x"]                                 # 2 bus invalides
        sequence["transitions"] += [{"id": "t", "from_clip_id": "?"}, 5]       # 2 transitions invalides
        sequence["ducking_sidechains"] += [{"voice_track_id": "A9"}]           # 1 association invalide
        body["library_folders"] += [{"id": ""}]                                # 1 dossier invalide
        body["library_tags"] += ["x"]                                          # 1 étiquette invalide
        body["color_presets"] += [{"nope": 1}]                                 # 1 préréglage invalide

    report = _loaded(_save(tmp_path, corrupt))
    assert report.dropped == {
        "clip_effects": 3, "audio_effects": 1, "buses": 2, "transitions": 2, "ducking": 1,
        "library_folders": 1, "library_tags": 1, "color_presets": 1,
    }
    assert report.total == 12 and not report.intact
    assert set(report.dropped) <= set(LOAD_SECTIONS), "chaque section comptée a un libellé"


def test_the_valid_entries_next_to_a_dropped_one_are_kept(tmp_path):
    def corrupt(body: dict) -> None:
        _clip(body)["effects"].insert(0, {"id": "x", "type": "nonexistent"})

    path = _save(tmp_path, corrupt)
    project = load_project(str(path))
    assert [effect.type for effect in project.tracks[0].clips[0].effects] == [EffectType.BLUR]


def test_insert_chains_of_a_bus_count_under_audio_effects(tmp_path):
    def corrupt(body: dict) -> None:
        body["sequences"][0]["buses"][0]["inserts"] = [{"id": "i", "type": "nonexistent"}]

    assert _loaded(_save(tmp_path, corrupt)).dropped == {"audio_effects": 1}


def test_the_report_is_per_call_and_does_not_leak_between_loads(tmp_path):
    def corrupt(body: dict) -> None:
        _clip(body)["effects"] += ["x"]

    broken, clean = _save(tmp_path, corrupt), tmp_path / "clean.kut"
    save_project(_project(), str(clean))
    first, second = LoadReport(), LoadReport()
    load_project(str(broken), report=first)
    load_project(str(clean), report=second)
    assert first.dropped == {"clip_effects": 1} and second.intact
    load_project(str(broken))                                  # sans rapport : ne touche aucun rapport existant
    assert first.dropped == {"clip_effects": 1}


def test_a_structurally_broken_file_is_still_refused_not_reported(tmp_path):
    def corrupt(body: dict) -> None:
        _clip(body)["source_out"] = -5.0                       # la structure est stricte : refus, pas « entrée ignorée »

    with pytest.raises(ValueError):
        load_project(str(_save(tmp_path, corrupt)), report=LoadReport())
