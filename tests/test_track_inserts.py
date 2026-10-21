"""Inserts de piste : une chaîne d'effets sur la *somme* des clips d'une piste.

Le modèle (opérations pures, historique, fichier ``.kut``) est testé sans FFmpeg ; le graphe est exécuté par le vrai
FFmpeg, parce que les tests qui ne lisent que le texte du filtre ont déjà laissé passer quatre effets cassés.
"""

from __future__ import annotations

import json
import math
import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.audio_effects_model import (
    AudioEffectType,
    add_track_insert,
    move_track_insert,
    remove_track_insert,
    set_track_insert_enabled,
    update_track_insert_parameters,
)
from core.export_engine import ExportEngine, filter_graph_arguments, input_arguments
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import create_sequence_from_selection

RATE = 48000


def _project(tmp_path, *, tracks: int = 1, clips_per_track: int = 2, amplitude: float = 0.6) -> Project:
    """``clips_per_track`` clips identiques (un sinus de 440 Hz) posés au même instant sur chaque piste audio."""
    seconds = 2.0
    time = np.arange(int(seconds * RATE)) / RATE
    tone = amplitude * np.sin(2 * math.pi * 440.0 * time)
    path = write_wav(tmp_path / "tone.wav", np.stack([tone, tone], axis=1), rate=RATE)
    asset = MediaAsset("a", str(path), "a", seconds, 0, 0, 0.0, "audio", has_audio=True)
    project_tracks = []
    for t in range(tracks):
        clips = [Clip(f"c{t}_{n}", "a", f"A{t + 1}", 0.0, 0.0, seconds) for n in range(clips_per_track)]
        project_tracks.append(Track(f"A{t + 1}", f"A{t + 1}", "audio", clips=clips))
    return Project("p", width=64, height=36, fps=25.0, media_assets=[asset], tracks=project_tracks)


def _graph(project, **options):
    return ExportEngine._build_filter_complex(
        build_render_plan(project), 64, 36, 25.0, None, audio_only=True, **options)


def _render(project, label_of=lambda audio, taps: audio, **options) -> np.ndarray:
    graph, _video, audio, inputs = _graph(project, **options)
    label = label_of(audio, options.get("track_taps"))
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for source in inputs:
        command += input_arguments(source)
    command += filter_graph_arguments(graph, [])
    if label != audio:  # un graphe n'accepte pas de sortie non connectée : le mixage part dans le vide
        command += ["-map", f"[{audio}]", "-f", "null", "-"]
    command += ["-map", f"[{label}]", "-c:a", "pcm_f32le", "-f", "f32le", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return np.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2)


def _peak(signal: np.ndarray) -> float:
    return float(np.abs(signal).max())


# --- Modèle -----------------------------------------------------------------------------------------------------------


def test_the_insert_chain_is_edited_in_order_and_refuses_what_a_clip_effect_would_refuse():
    project = Project("p", tracks=[Track("A1", "A1", "audio"), Track("S1", "S1", "subtitle")])
    limiter = add_track_insert(project, "A1", AudioEffectType.LIMITER)
    compressor = add_track_insert(project, "A1", AudioEffectType.COMPRESSOR)
    assert [e.id for e in project.tracks[0].inserts] == [limiter.id, compressor.id]

    move_track_insert(project, "A1", compressor.id, -1)
    assert [e.id for e in project.tracks[0].inserts] == [compressor.id, limiter.id]

    update_track_insert_parameters(project, "A1", limiter.id, {"limit_db": -3.0})
    assert project.tracks[0].inserts[1].params["limit_db"] == -3.0
    with pytest.raises(ValueError):
        update_track_insert_parameters(project, "A1", limiter.id, {"limit_db": 6.0})

    assert set_track_insert_enabled(project, "A1", limiter.id, False).enabled is False
    with pytest.raises(ValueError, match="existe déjà"):
        add_track_insert(project, "A1", AudioEffectType.LIMITER, effect_id=limiter.id)

    remove_track_insert(project, "A1", compressor.id)
    assert [e.id for e in project.tracks[0].inserts] == [limiter.id]
    with pytest.raises(KeyError):
        remove_track_insert(project, "A1", compressor.id)
    with pytest.raises(KeyError):
        add_track_insert(project, "nope", AudioEffectType.LIMITER)
    with pytest.raises(ValueError, match="subtitle"):
        add_track_insert(project, "S1", AudioEffectType.LIMITER)
    project.tracks[0].locked = True
    with pytest.raises(ValueError, match="verrouill"):
        add_track_insert(project, "A1", AudioEffectType.BASS_BOOST)


def _tracks_of(raw: dict) -> list[dict]:
    """Les pistes de la séquence principale dans le JSON du fichier."""
    body = raw["project"]
    return body["sequences"][0]["tracks"] if "sequences" in body else body["tracks"]


def test_inserts_round_trip_through_the_project_file_and_an_empty_chain_adds_no_key(tmp_path):
    project = Project("p", tracks=[Track("A1", "A1", "audio"), Track("A2", "A2", "audio")])
    first = add_track_insert(project, "A1", AudioEffectType.COMPRESSOR, params={"ratio": 6.0})
    add_track_insert(project, "A1", AudioEffectType.LIMITER, enabled=False)
    path = tmp_path / "inserts.kut"
    save_project(project, str(path))

    tracks = _tracks_of(json.loads(path.read_text(encoding="utf-8")))
    assert "inserts" in tracks[0] and "inserts" not in tracks[1]

    loaded = load_project(str(path))
    inserts = loaded.tracks[0].inserts
    assert [(e.id, e.type, e.enabled) for e in inserts] == [
        (first.id, AudioEffectType.COMPRESSOR, True), (inserts[1].id, AudioEffectType.LIMITER, False)]
    assert inserts[0].params["ratio"] == 6.0
    assert loaded.tracks[1].inserts == []


def test_an_invalid_insert_in_a_file_is_dropped_alone(tmp_path):
    project = Project("p", tracks=[Track("A1", "A1", "audio")])
    keep = add_track_insert(project, "A1", AudioEffectType.BASS_BOOST)
    path = tmp_path / "inserts.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    track = _tracks_of(raw)[0]
    track["inserts"] = [{"id": "x", "type": "nonexistent"}, "junk", *track["inserts"]]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert [e.id for e in load_project(str(path)).tracks[0].inserts] == [keep.id]


def test_every_layer_of_a_track_carries_the_track_chain_in_the_render_plan(tmp_path):
    project = _project(tmp_path)
    limiter = add_track_insert(project, "A1", AudioEffectType.LIMITER)
    layers = build_render_plan(project).audio_layers
    assert len(layers) == 2 and all(layer.track_inserts == (limiter,) for layer in layers)


# --- Graphe -----------------------------------------------------------------------------------------------------------


def test_a_track_without_active_inserts_keeps_the_graph_it_had(tmp_path):
    project = _project(tmp_path)
    before = _graph(project)[0]
    effect = add_track_insert(project, "A1", AudioEffectType.LIMITER, enabled=False)
    assert _graph(project)[0] == before, "un insert contourné ne crée aucun sous-mixage"
    set_track_insert_enabled(project, "A1", effect.id, True)
    assert "ti0" in _graph(project)[0]


@needs_ffmpeg
def test_a_limiter_insert_limits_the_sum_of_the_tracks_clips_not_each_clip(tmp_path):
    """Deux clips de 0,6 s'additionnent à 1,2 : le limiteur de piste (−6 dB = 0,5) voit la somme. Sur chaque clip il ne
    verrait que 0,6 et ferait 0,5 + 0,5 = 1,0."""
    project = _project(tmp_path)
    untouched = _peak(_render(project))
    add_track_insert(project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    limited = _peak(_render(project))
    assert untouched > 0.8
    assert limited < 0.56, f"crête {limited:.3f} : le limiteur n'a pas vu la somme"
    assert limited > 0.4, "la piste ne doit pas être éteinte"


@needs_ffmpeg
def test_a_bypassed_insert_leaves_the_sound_as_it_was(tmp_path):
    project = _project(tmp_path)
    reference = _render(project)
    effect = add_track_insert(project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    set_track_insert_enabled(project, "A1", effect.id, False)
    assert np.array_equal(_render(project), reference)


@needs_ffmpeg
def test_a_neighbour_track_without_inserts_is_not_touched(tmp_path):
    project = _project(tmp_path, tracks=2, clips_per_track=1, amplitude=0.8)
    add_track_insert(project, "A2", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    project.tracks[0].muted = True
    assert _peak(_render(project)) < 0.56, "A2 est limitée"
    project.tracks[0].muted = False
    project.tracks[1].muted = True
    assert abs(_peak(_render(project)) - 0.8) < 0.01, "A1, sans insert, sort comme avant"


@needs_ffmpeg
@pytest.mark.parametrize("kind", list(AudioEffectType), ids=lambda kind: kind.value)
def test_every_effect_works_as_a_track_insert(tmp_path, kind):
    project = _project(tmp_path, clips_per_track=1)
    add_track_insert(project, "A1", kind)
    rendered = _render(project)
    assert len(rendered) > RATE and np.isfinite(rendered).all()


@needs_ffmpeg
def test_the_chain_runs_in_order(tmp_path):
    """Un limiteur à −6 dB après un gain de +24 dB (basses) borne la crête ; dans l'autre ordre, la crête dépasse."""
    project = _project(tmp_path, clips_per_track=1, amplitude=0.2)
    bass = add_track_insert(project, "A1", AudioEffectType.BASS_BOOST, params={"gain_db": 24.0, "frequency_hz": 250.0})
    limiter = add_track_insert(project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    limited_last = _peak(_render(project))
    move_track_insert(project, "A1", limiter.id, -1)
    assert [e.id for e in project.tracks[0].inserts] == [limiter.id, bass.id]
    limited_first = _peak(_render(project))
    assert limited_last < 0.56
    assert limited_first > limited_last, "le gain suivant le limiteur n'est plus borné"


@needs_ffmpeg
def test_the_meter_of_a_track_reads_after_its_inserts(tmp_path):
    project = _project(tmp_path)
    add_track_insert(project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    taps: dict[str, str] = {}
    metered = _render(project, label_of=lambda audio, t: t["A1"], track_taps=taps)
    assert set(taps) == {"A1"}
    assert _peak(metered) < 0.56, "le vumètre de piste doit mesurer la sortie de la chaîne"


@needs_ffmpeg
def test_inserts_survive_a_nested_sequence(tmp_path):
    """La piste hôte garde ses inserts : ils traitent le clip imbriqué dans le parent. Les pistes recopiées dans la
    séquence imbriquée emportent les leurs, avec des labels préfixés par celle-ci."""
    project = _project(tmp_path, tracks=2, clips_per_track=1)
    add_track_insert(project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    add_track_insert(project, "A2", AudioEffectType.LIMITER, params={"limit_db": -6.0})
    create_sequence_from_selection(project, ["c0_0", "c1_0"], "Scène")
    rendered = _render(project)
    assert len(rendered) > RATE and np.isfinite(rendered).all()
    assert _peak(rendered) < 0.95
