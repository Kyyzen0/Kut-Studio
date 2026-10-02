"""L'empreinte d'un segment d'aperçu couvre tout ce qui change le rendu (jamais de cache périmé).

Le cache d'aperçu est persistant : un paramètre oublié dans l'empreinte fait réentendre ou revoir
l'ancien rendu après une édition. Chaque cas modifie UN réglage qui atteint le plan de rendu et exige
que l'empreinte change ; le cas témoin exige l'inverse (deux projets identiques, même empreinte).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.audio_automation import AutomationPoint
from core.audio_effects_model import AudioEffectType, create_audio_effect
from core.filter_graph import fingerprint_plan
from core.preview_segments import build_segment_job
from core.project_model import Clip, MediaAsset, Project, Track


def _project(tmp_path) -> Project:
    media = tmp_path / "m.mp4"
    if not media.exists():  # l'identité du fichier (mtime, taille) fait partie de l'empreinte : ne pas la changer
        media.write_bytes(b"x" * 100)
    asset = MediaAsset(id="a", path=str(media), name="m", duration=100.0, width=1920, height=1080,
                       fps=25.0, media_type="video", has_audio=True)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    cue = Clip(id="s1", asset_id="", track_id="S1", timeline_start=0.0, source_in=0.0, source_out=10.0, text="Hello")
    tracks = [Track(id="V1", name="V1", type="video", clips=[clip]),
              Track(id="S1", name="S1", type="subtitle", clips=[cue])]
    return Project(name="p", width=1280, height=720, fps=25.0, media_assets=[asset], tracks=tracks)


def _hash(project, **master) -> str:
    return build_segment_job(project, 0, quality="standard", **master).key.params_hash


def _video(project):
    return project.tracks[0]


def _cue(project):
    return project.tracks[1].clips[0]


MIX_EDITS = {
    "clip pan": lambda p: setattr(_video(p).clips[0], "pan", 0.7),
    "clip fade in": lambda p: setattr(_video(p).clips[0], "fade_in", 1.0),
    "clip fade out": lambda p: setattr(_video(p).clips[0], "fade_out", 1.0),
    "clip gain": lambda p: setattr(_video(p).clips[0], "gain_db", -6.0),
    "track volume": lambda p: _video(p).set_volume_db(-12.0),
    "track pan": lambda p: _video(p).set_pan(-1.0),
    "track mute": lambda p: setattr(_video(p), "muted", True),
    "audio effect": lambda p: _video(p).clips[0].audio_effects.append(
        create_audio_effect(next(iter(AudioEffectType)), effect_id="afx-test")),
    "track automation": lambda p: _video(p).automation.append(AutomationPoint(time_seconds=1.0, gain_db=-9.0)),
}

SUBTITLE_EDITS = {
    "subtitle text": lambda p: setattr(_cue(p), "text", "Bye"),
    "subtitle size": lambda p: setattr(_cue(p), "text_style", replace(_cue(p).text_style, font_size=90.0)),
    "subtitle color": lambda p: setattr(_cue(p), "text_style", replace(_cue(p).text_style, color="#ff0000")),
}


@pytest.mark.parametrize("label", list({**MIX_EDITS, **SUBTITLE_EDITS}))
def test_an_edit_that_changes_the_render_changes_the_segment_hash(tmp_path, label):
    edit = {**MIX_EDITS, **SUBTITLE_EDITS}[label]
    before = _hash(_project(tmp_path))
    edited = _project(tmp_path)
    edit(edited)
    assert _hash(edited) != before, f"« {label} » ne change pas l'empreinte : le cache servirait un rendu périmé"


@pytest.mark.parametrize("master", [{"master_gain_db": -6.0}, {"master_muted": True}])
def test_the_master_fader_and_mute_change_the_segment_hash(tmp_path, master):
    project = _project(tmp_path)
    assert _hash(project, **master) != _hash(project)


def test_identical_projects_share_one_segment_hash(tmp_path):
    """Témoin : l'empreinte ne dépend ni de l'instance ni de l'ordre de construction."""
    assert _hash(_project(tmp_path)) == _hash(_project(tmp_path))


def test_the_engine_version_is_part_of_the_hash(tmp_path, monkeypatch):
    """Un changement de rendu sans changement de plan invalide le cache persistant en incrémentant la version."""
    import core.filter_graph as graph

    plan = build_segment_job(_project(tmp_path), 0, quality="standard").plan
    before = fingerprint_plan(plan, width=1280, height=720, fps=25.0)
    monkeypatch.setattr(graph, "RENDER_ENGINE_VERSION", graph.RENDER_ENGINE_VERSION + 1, raising=False)
    assert fingerprint_plan(plan, width=1280, height=720, fps=25.0) != before
