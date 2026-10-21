"""Bus audio : sous-mixages de la séquence entre les pistes et le Master.

Le modèle et le fichier ``.kut`` sont testés sans FFmpeg ; le graphe est exécuté par le vrai FFmpeg (un test qui ne lit
que le texte du filtre a déjà laissé passer des effets cassés).
"""

from __future__ import annotations

import json
import math
import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.audio_buses import (
    MAX_BUSES,
    add_bus,
    add_bus_insert,
    find_bus,
    move_bus_insert,
    remove_bus,
    remove_bus_insert,
    rename_bus,
    route_track,
    set_bus_insert_enabled,
    set_bus_muted,
    set_bus_volume,
    update_bus_insert_parameters,
)
from core.audio_effects_model import AudioEffectType
from core.export_engine import ExportEngine, filter_graph_arguments, input_arguments
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import create_sequence_from_selection

RATE = 48000


def _project(tmp_path, *, tracks: int = 2, amplitude: float = 0.4) -> Project:
    """``tracks`` pistes audio portant chacune un clip : le même sinus de 440 Hz, en phase (les sommes s'additionnent)."""
    seconds = 2.0
    tone = amplitude * np.sin(
        2 * math.pi * 440.0 * np.arange(int(seconds * RATE)) / RATE
    )
    path = write_wav(tmp_path / "tone.wav", np.stack([tone, tone], axis=1), rate=RATE)
    asset = MediaAsset("a", str(path), "a", seconds, 0, 0, 0.0, "audio", has_audio=True)
    built = [
        Track(
            f"A{n + 1}",
            f"A{n + 1}",
            "audio",
            clips=[Clip(f"c{n + 1}", "a", f"A{n + 1}", 0.0, 0.0, seconds)],
        )
        for n in range(tracks)
    ]
    return Project(
        "p", width=64, height=36, fps=25.0, media_assets=[asset], tracks=built
    )


def _graph(project, **options):
    return ExportEngine._build_filter_complex(
        build_render_plan(project), 64, 36, 25.0, None, audio_only=True, **options
    )


def _render(project, label_of=lambda audio, taps: audio, **options) -> np.ndarray:
    graph, _video, audio, inputs = _graph(project, **options)
    label = label_of(audio, options.get("track_taps"))
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for source in inputs:
        command += input_arguments(source)
    command += filter_graph_arguments(graph, [])
    # Un graphe n'accepte pas de sortie non connectée : le mixage et les autres points de mesure partent dans le vide.
    for other in [audio, *(options.get("track_taps") or {}).values()]:
        if other != label:
            command += ["-map", f"[{other}]", "-f", "null", "-"]
    command += ["-map", f"[{label}]", "-c:a", "pcm_f32le", "-f", "f32le", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return np.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2)


def _peak(signal: np.ndarray) -> float:
    return float(np.abs(signal).max())


# --- Modèle -----------------------------------------------------------------------------------------------------------


def test_a_bus_is_added_renamed_set_and_removed_and_its_tracks_return_to_the_master(
    tmp_path,
):
    project = _project(tmp_path)
    first = add_bus(project)
    second = add_bus(project)
    assert [b.name for b in project.buses] == ["Bus 1", "Bus 2"]
    rename_bus(project, first.id, "Voix")
    set_bus_volume(project, first.id, -6.0)
    set_bus_muted(project, first.id, True)
    assert (
        find_bus(project, first.id).name,
        find_bus(project, first.id).volume_db,
        find_bus(project, first.id).muted,
    ) == ("Voix", -6.0, True)
    with pytest.raises(ValueError):
        rename_bus(project, first.id, "  ")
    route_track(project, "A1", first.id)
    route_track(project, "A2", second.id)
    assert remove_bus(project, first.id) == ["A1"]
    assert project.tracks[0].bus_id == "" and project.tracks[1].bus_id == second.id
    assert [b.id for b in project.buses] == [second.id]
    with pytest.raises(KeyError):
        remove_bus(project, first.id)
    assert add_bus(project).name == "Bus 1", "le premier numéro libre"


def test_routing_refuses_unknown_buses_locked_and_subtitle_tracks_and_the_number_of_buses_is_bounded(
    tmp_path,
):
    project = _project(tmp_path)
    project.tracks.append(Track("S1", "S1", "subtitle"))
    bus = add_bus(project)
    with pytest.raises(KeyError):
        route_track(project, "A1", "nope")
    with pytest.raises(KeyError):
        route_track(project, "nope", bus.id)
    with pytest.raises(ValueError, match="subtitle"):
        route_track(project, "S1", bus.id)
    project.tracks[0].locked = True
    with pytest.raises(ValueError, match="verrouill"):
        route_track(project, "A1", bus.id)
    assert project.tracks[0].bus_id == "", "un refus ne modifie rien"
    for _ in range(MAX_BUSES - 1):
        add_bus(project)
    with pytest.raises(ValueError, match="plus de"):
        add_bus(project)


def test_bus_inserts_are_edited_like_track_inserts(tmp_path):
    project = _project(tmp_path)
    bus = add_bus(project)
    limiter = add_bus_insert(project, bus.id, AudioEffectType.LIMITER)
    compressor = add_bus_insert(project, bus.id, AudioEffectType.COMPRESSOR)
    move_bus_insert(project, bus.id, compressor.id, -1)
    assert [e.id for e in bus.inserts] == [compressor.id, limiter.id]
    update_bus_insert_parameters(project, bus.id, limiter.id, {"limit_db": -3.0})
    assert bus.inserts[1].params["limit_db"] == -3.0
    with pytest.raises(ValueError):
        update_bus_insert_parameters(project, bus.id, limiter.id, {"limit_db": 6.0})
    assert set_bus_insert_enabled(project, bus.id, limiter.id, False).enabled is False
    remove_bus_insert(project, bus.id, compressor.id)
    assert [e.id for e in bus.inserts] == [limiter.id]
    with pytest.raises(KeyError):
        remove_bus_insert(project, bus.id, compressor.id)


def _tracks_json(raw: dict) -> list[dict]:
    body = raw["project"]
    return body["sequences"][0]["tracks"] if "sequences" in body else body["tracks"]


def _sequence_json(raw: dict) -> dict:
    body = raw["project"]
    return body["sequences"][0] if "sequences" in body else body


def test_buses_round_trip_through_the_project_file_and_a_project_without_bus_adds_no_key(
    tmp_path,
):
    plain = _project(tmp_path)
    save_project(plain, str(tmp_path / "plain.kut"))
    raw = json.loads((tmp_path / "plain.kut").read_text(encoding="utf-8"))
    assert "buses" not in _sequence_json(raw) and all(
        "bus_id" not in t for t in _tracks_json(raw)
    )

    project = _project(tmp_path)
    bus = add_bus(project, "Voix")
    set_bus_volume(project, bus.id, -4.5)
    add_bus_insert(project, bus.id, AudioEffectType.COMPRESSOR, params={"ratio": 5.0})
    route_track(project, "A2", bus.id)
    save_project(project, str(tmp_path / "bus.kut"))
    loaded = load_project(str(tmp_path / "bus.kut"))
    assert [(b.id, b.name, b.volume_db, b.muted) for b in loaded.buses] == [
        (bus.id, "Voix", -4.5, False)
    ]
    assert loaded.buses[0].inserts[0].params["ratio"] == 5.0
    assert [t.bus_id for t in loaded.tracks] == ["", bus.id]


def test_invalid_buses_in_a_file_are_dropped_alone_and_a_dangling_route_returns_to_the_master(
    tmp_path,
):
    project = _project(tmp_path)
    keep = add_bus(project, "Garde")
    route_track(project, "A1", keep.id)
    route_track(project, "A2", keep.id)
    path = tmp_path / "bus.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    sequence = _sequence_json(raw)
    sequence["buses"] = [
        "junk",
        {"id": ""},
        {"name": "sans id"},
        *sequence["buses"],
        dict(sequence["buses"][0]),
    ]
    _tracks_json(raw)[1]["bus_id"] = "disparu"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_project(str(path))
    assert [b.id for b in loaded.buses] == [keep.id], (
        "les entrées invalides et le doublon sont écartés seuls"
    )
    assert [t.bus_id for t in loaded.tracks] == [keep.id, ""], (
        "un bus absent renvoie la piste au Master"
    )


def test_the_render_plan_carries_the_buses_and_each_layers_route(tmp_path):
    project = _project(tmp_path)
    bus = add_bus(project)
    set_bus_volume(project, bus.id, -3.0)
    route_track(project, "A1", bus.id)
    plan = build_render_plan(project)
    assert [(b.id, b.volume_db) for b in plan.audio_buses] == [(bus.id, -3.0)]
    assert [(layer.track_id, layer.bus_id) for layer in plan.audio_layers] == [
        ("A1", bus.id),
        ("A2", ""),
    ]


# --- Graphe -----------------------------------------------------------------------------------------------------------


def test_a_project_whose_buses_receive_no_track_keeps_the_graph_it_had(tmp_path):
    project = _project(tmp_path)
    before = _graph(project)[0]
    bus = add_bus(project)
    assert _graph(project)[0] == before, "un bus vide n'est pas construit"
    route_track(project, "A1", bus.id)
    assert "bus0" in _graph(project)[0]


@needs_ffmpeg
def test_a_limiter_on_a_bus_limits_the_sum_of_its_tracks_and_leaves_the_others_alone(
    tmp_path,
):
    project = _project(tmp_path, tracks=3, amplitude=0.3)
    assert abs(_peak(_render(project)) - 0.9) < 0.02
    bus = add_bus(project)
    add_bus_insert(project, bus.id, AudioEffectType.LIMITER, params={"limit_db": -6.0})
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    # A1 + A2 = 0,6 sont limitées à 0,5 ; A3 (0,3) passe directement : 0,5 + 0,3.
    assert abs(_peak(_render(project)) - 0.8) < 0.03


@needs_ffmpeg
def test_the_bus_fader_scales_its_tracks_and_a_muted_bus_is_silent(tmp_path):
    project = _project(tmp_path, tracks=2, amplitude=0.4)
    bus = add_bus(project)
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    assert abs(_peak(_render(project)) - 0.8) < 0.02
    set_bus_volume(project, bus.id, -6.0)
    assert abs(_peak(_render(project)) - 0.8 * 10 ** (-6 / 20)) < 0.02
    set_bus_muted(project, bus.id, True)
    assert _peak(_render(project)) < 1e-4


@needs_ffmpeg
def test_a_route_to_a_missing_bus_plays_at_the_master(tmp_path):
    project = _project(tmp_path)
    project.tracks[0].bus_id = "disparu"
    assert abs(_peak(_render(project)) - 0.8) < 0.02


@needs_ffmpeg
def test_track_meters_read_before_the_bus_and_the_bus_has_its_own_meter(tmp_path):
    project = _project(tmp_path, tracks=2, amplitude=0.4)
    bus = add_bus(project)
    set_bus_volume(project, bus.id, -12.0)
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    taps: dict[str, str] = {}
    track_level = _peak(
        _render(project, label_of=lambda audio, t: t["A1"], track_taps=taps)
    )
    assert set(taps) == {"A1", "A2", f"bus:{bus.id}"}
    assert abs(track_level - 0.4) < 0.01, "la piste est mesurée avant le fader du bus"
    taps2: dict[str, str] = {}
    bus_level = _peak(
        _render(project, label_of=lambda audio, t: t[f"bus:{bus.id}"], track_taps=taps2)
    )
    assert abs(bus_level - 0.8 * 10 ** (-12 / 20)) < 0.02, (
        "le bus est mesuré après son fader"
    )


@needs_ffmpeg
def test_a_bus_works_with_track_inserts_in_front_of_it(tmp_path):
    from core.audio_effects_model import add_track_insert

    project = _project(tmp_path, tracks=2, amplitude=0.4)
    add_track_insert(
        project, "A1", AudioEffectType.LIMITER, params={"limit_db": -6.0}
    )  # 0,4 < 0,5 : sans effet
    bus = add_bus(project)
    add_bus_insert(project, bus.id, AudioEffectType.LIMITER, params={"limit_db": -6.0})
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    assert 0.4 < _peak(_render(project)) < 0.56


def _inner(project):
    return next(sequence for sequence in project.sequences if sequence.name == "Scène")


def test_nesting_under_a_video_host_keeps_the_audio_buses_inside_the_new_sequence(
    tmp_path,
):
    """L'hôte est la piste vidéo : aucun bus du parent ne traite le clip imbriqué. Les bus audio doivent donc
    être dans la nouvelle séquence, sinon une piste coupée redevient audible."""
    project = _project(tmp_path, tracks=2)
    project.tracks.insert(
        0, Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, 2.0)])
    )
    bus = add_bus(project, "Voix")
    set_bus_muted(project, bus.id, True)
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    create_sequence_from_selection(project, ["v", "c1", "c2"], "Scène")
    inner = _inner(project)
    assert [track.id for track in inner.tracks][0] == "V1"
    assert [(bus_item.id, bus_item.muted) for bus_item in inner.buses] == [
        (bus.id, True)
    ]
    assert {track.id: track.bus_id for track in inner.tracks} == {
        "V1": "",
        "A1": bus.id,
        "A2": bus.id,
    }
    assert project.tracks[0].bus_id == ""


def test_nesting_copies_a_bus_that_is_not_the_hosts_and_leaves_the_host_bus_on_the_parent(
    tmp_path,
):
    """Le bus de la piste hôte reste sur le parent (une seule application). Un autre bus suit ses pistes : sinon
    sa coupure reste sur une piste vidée et le son muet redevient audible."""
    project = _project(tmp_path, tracks=2, amplitude=0.4)
    voices = add_bus(project, "Voix")
    music = add_bus(project, "Musique")
    set_bus_muted(project, voices.id, True)
    set_bus_volume(project, music.id, -6.0)
    route_track(project, "A1", music.id)
    route_track(project, "A2", voices.id)
    create_sequence_from_selection(project, ["c1", "c2"], "Scène")
    inner = _inner(project)
    assert [(bus.id, bus.muted) for bus in inner.buses] == [(voices.id, True)]
    assert [(track.id, track.bus_id) for track in inner.tracks] == [
        ("A1", ""),
        ("A2", voices.id),
    ]
    assert project.tracks[0].bus_id == music.id and project.tracks[0].clips, (
        "le bus de l'hôte reste sur le parent"
    )
    assert project.tracks[1].clips == [] and project.tracks[1].bus_id == voices.id


@needs_ffmpeg
def test_a_muted_bus_stays_silent_after_the_selection_is_nested(tmp_path):
    project = _project(tmp_path, tracks=2, amplitude=0.4)
    bus = add_bus(project, "Voix")
    set_bus_muted(project, bus.id, True)
    route_track(project, "A2", bus.id)
    assert abs(_peak(_render(project)) - 0.4) < 0.02
    create_sequence_from_selection(project, ["c1", "c2"], "Scène")
    assert abs(_peak(_render(project)) - 0.4) < 0.02, (
        "A2 coupé par son bus ne doit pas revenir dans le mixage"
    )


@needs_ffmpeg
def test_nesting_onto_the_host_bus_does_not_apply_that_bus_twice(tmp_path):
    project = _project(tmp_path, tracks=2, amplitude=0.4)
    bus = add_bus(project)
    set_bus_volume(project, bus.id, -6.0)
    route_track(project, "A1", bus.id)
    route_track(project, "A2", bus.id)
    before = _peak(_render(project))
    create_sequence_from_selection(project, ["c1", "c2"], "Scène")
    assert _inner(project).buses == [] and all(
        track.bus_id == "" for track in _inner(project).tracks
    )
    assert abs(_peak(_render(project)) - before) < 0.02
