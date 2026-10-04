"""Tests pour la persistance du remappage temporel dans les projets Kut-Studio."""

import json
from pathlib import Path
import tempfile

import pytest

from core.project_io import (
    CURRENT_VERSION,
    FORMAT_NAME,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_remapping import FreezeFrameMode, TimeRemapping


def _project_with_time_remapping() -> Project:
    """Crée un projet avec des clips ayant différents réglages de remappage temporel."""
    asset = MediaAsset(
        id="asset-1",
        path="/tmp/clip.mp4",
        name="Test Clip",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )

    # Clip avec vitesse normale
    clip_normal = Clip(
        id="clip-normal",
        asset_id="asset-1",
        track_id="track-v1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=10.0,
    )

    # Clip avec vitesse 2x
    clip_fast = Clip(
        id="clip-fast",
        asset_id="asset-1",
        track_id="track-v1",
        timeline_start=10.0,
        source_in=0.0,
        source_out=10.0,
        time_remapping=TimeRemapping(speed=2.0),
    )

    # Clip avec reverse
    clip_reverse = Clip(
        id="clip-reverse",
        asset_id="asset-1",
        track_id="track-v1",
        timeline_start=15.0,
        source_in=0.0,
        source_out=10.0,
        time_remapping=TimeRemapping(reverse=True),
    )

    # Clip avec freeze frame
    clip_freeze = Clip(
        id="clip-freeze",
        asset_id="asset-1",
        track_id="track-v1",
        timeline_start=25.0,
        source_in=0.0,
        source_out=10.0,
        time_remapping=TimeRemapping(
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=5.0,
            freeze_duration=3.0,
        ),
    )

    # Clip avec vitesse + reverse
    clip_complex = Clip(
        id="clip-complex",
        asset_id="asset-1",
        track_id="track-v1",
        timeline_start=28.0,
        source_in=0.0,
        source_out=10.0,
        time_remapping=TimeRemapping(speed=0.5, reverse=True),
    )

    track = Track(
        id="track-v1",
        name="V1",
        type="video",
        clips=[clip_normal, clip_fast, clip_reverse, clip_freeze, clip_complex],
    )

    return Project(
        name="Test Time Remapping",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset],
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# Tests de sérialisation
# ---------------------------------------------------------------------------


class TestTimeRemappingSerialization:
    def test_version_is_incremented(self):
        """La version courante est 15 (motion graphics)."""
        assert CURRENT_VERSION == 16

    def test_payload_includes_time_remapping(self):
        """Le payload doit inclure les champs de time_remapping."""
        project = _project_with_time_remapping()
        payload = project_payload(project)

        assert payload["format"] == FORMAT_NAME
        assert payload["version"] == CURRENT_VERSION

        # Vérifier que time_remapping est présent dans les clips
        clips_data = payload["project"]["sequences"][0]["tracks"][0]["clips"]
        assert len(clips_data) == 5

        # Vérifier le clip normal (valeurs par défaut)
        normal_clip = next(c for c in clips_data if c["id"] == "clip-normal")
        assert "time_remapping" in normal_clip
        assert normal_clip["time_remapping"]["speed"] == 1.0
        assert normal_clip["time_remapping"]["reverse"] is False
        assert normal_clip["time_remapping"]["freeze_mode"] == "none"

        # Vérifier le clip rapide
        fast_clip = next(c for c in clips_data if c["id"] == "clip-fast")
        assert fast_clip["time_remapping"]["speed"] == 2.0
        assert fast_clip["time_remapping"]["reverse"] is False

        # Vérifier le clip reverse
        reverse_clip = next(c for c in clips_data if c["id"] == "clip-reverse")
        assert reverse_clip["time_remapping"]["speed"] == 1.0
        assert reverse_clip["time_remapping"]["reverse"] is True

        # Vérifier le clip freeze
        freeze_clip = next(c for c in clips_data if c["id"] == "clip-freeze")
        assert freeze_clip["time_remapping"]["freeze_mode"] == "freeze"
        assert freeze_clip["time_remapping"]["freeze_source_time"] == 5.0
        assert freeze_clip["time_remapping"]["freeze_duration"] == 3.0

        # Vérifier le clip complexe
        complex_clip = next(c for c in clips_data if c["id"] == "clip-complex")
        assert complex_clip["time_remapping"]["speed"] == 0.5
        assert complex_clip["time_remapping"]["reverse"] is True


# ---------------------------------------------------------------------------
# Tests de désérialisation
# ---------------------------------------------------------------------------


class TestTimeRemappingDeserialization:
    def test_load_version_7_project(self):
        """Charger un projet version 7 avec time_remapping."""
        project = _project_with_time_remapping()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kut", delete=False) as f:
            save_project(project, f.name)

        try:
            loaded = load_project(f.name)

            # Vérifier le projet
            assert loaded.name == project.name
            assert len(loaded.tracks) == 1
            assert len(loaded.tracks[0].clips) == 5

            # Vérifier les clips
            clips_by_id = {c.id: c for c in loaded.tracks[0].clips}

            # Clip normal
            assert clips_by_id["clip-normal"].speed == 1.0
            assert clips_by_id["clip-normal"].is_reversed is False
            assert clips_by_id["clip-normal"].is_frozen is False

            # Clip rapide
            assert clips_by_id["clip-fast"].speed == 2.0
            assert clips_by_id["clip-fast"].is_reversed is False

            # Clip reverse
            assert clips_by_id["clip-reverse"].speed == 1.0
            assert clips_by_id["clip-reverse"].is_reversed is True

            # Clip freeze
            assert clips_by_id["clip-freeze"].is_frozen is True
            assert clips_by_id["clip-freeze"].time_remapping.freeze_source_time == 5.0
            assert clips_by_id["clip-freeze"].time_remapping.freeze_duration == 3.0

            # Clip complexe
            assert clips_by_id["clip-complex"].speed == 0.5
            assert clips_by_id["clip-complex"].is_reversed is True

        finally:
            Path(f.name).unlink(missing_ok=True)

    def test_load_legacy_project_without_time_remapping(self):
        """Charger un ancien projet (version 6) sans time_remapping."""
        # Créer un payload version 6 sans time_remapping
        legacy_payload = {
            "format": FORMAT_NAME,
            "version": 6,
            "project": {
                "name": "Legacy Project",
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "media_assets": [
                    {
                        "id": "asset-1",
                        "path": "/tmp/clip.mp4",
                        "name": "Test",
                        "duration": 10.0,
                        "width": 1920,
                        "height": 1080,
                        "fps": 30.0,
                        "media_type": "video",
                        "has_audio": False,
                    }
                ],
                "tracks": [
                    {
                        "id": "track-v1",
                        "name": "V1",
                        "type": "video",
                        "locked": False,
                        "visible": True,
                        "muted": False,
                        "solo": False,
                        "armed": False,
                        "height_mode": "normal",
                        "collapsed": False,
                        "volume_db": 0.0,
                        "pan": 0.0,
                        "clips": [
                            {
                                "id": "clip-1",
                                "asset_id": "asset-1",
                                "track_id": "track-v1",
                                "timeline_start": 0.0,
                                "source_in": 0.0,
                                "source_out": 10.0,
                                "enabled": True,
                                "label": "",
                                "text": "",
                                "gain_db": 0.0,
                                "pan": 0.0,
                                "fade_in": 0.0,
                                "fade_out": 0.0,
                                "transform": {
                                    "position_x": 0.0,
                                    "position_y": 0.0,
                                    "scale": 1.0,
                                    "rotation": 0.0,
                                    "opacity": 1.0,
                                },
                                "transform_keyframes": [],
                            }
                        ],
                    }
                ],
                "markers": [],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kut", delete=False) as f:
            json.dump(legacy_payload, f, indent=2)

        try:
            loaded = load_project(f.name)

            # Vérifier que le clip a un time_remapping par défaut
            clip = loaded.tracks[0].clips[0]
            assert clip.speed == 1.0
            assert clip.is_reversed is False
            assert clip.is_frozen is False
            assert clip.time_remapping.is_normal is True

        finally:
            Path(f.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests de round-trip
# ---------------------------------------------------------------------------


class TestTimeRemappingRoundTrip:
    def test_roundtrip_preserves_time_remapping(self):
        """Un projet sauvegardé puis chargé conserve le time_remapping."""
        project = _project_with_time_remapping()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kut", delete=False) as f:
            save_project(project, f.name)

        try:
            loaded = load_project(f.name)

            # Vérifier tous les clips
            original_clips = {c.id: c for c in project.tracks[0].clips}
            loaded_clips = {c.id: c for c in loaded.tracks[0].clips}

            for clip_id in original_clips:
                orig = original_clips[clip_id]
                load = loaded_clips[clip_id]

                # Vérifier les propriétés de base
                assert orig.id == load.id
                assert orig.asset_id == load.asset_id
                assert orig.timeline_start == load.timeline_start
                assert orig.source_in == load.source_in
                assert orig.source_out == load.source_out

                # Vérifier le time_remapping
                assert orig.speed == load.speed
                assert orig.is_reversed == load.is_reversed
                assert orig.is_frozen == load.is_frozen

                if orig.is_frozen:
                    assert (
                        orig.time_remapping.freeze_source_time
                        == load.time_remapping.freeze_source_time
                    )
                    assert (
                        orig.time_remapping.freeze_duration
                        == load.time_remapping.freeze_duration
                    )

        finally:
            Path(f.name).unlink(missing_ok=True)

    def test_roundtrip_duration_calculation(self):
        """La durée calculée est préservée après round-trip."""
        project = _project_with_time_remapping()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kut", delete=False) as f:
            save_project(project, f.name)

        try:
            loaded = load_project(f.name)

            loaded_clips = {c.id: c for c in loaded.tracks[0].clips}

            # Clip normal: durée = 10.0
            assert loaded_clips["clip-normal"].duration == 10.0

            # Clip 2x: durée = 10.0 / 2.0 = 5.0
            assert loaded_clips["clip-fast"].duration == pytest.approx(5.0)

            # Clip reverse: durée = 10.0 / 1.0 = 10.0
            assert loaded_clips["clip-reverse"].duration == 10.0

            # Clip freeze: durée = 3.0 (freeze_duration)
            assert loaded_clips["clip-freeze"].duration == 3.0

            # Clip complexe: durée = 10.0 / 0.5 = 20.0
            assert loaded_clips["clip-complex"].duration == pytest.approx(20.0)

        finally:
            Path(f.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests d'atomicité
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_atomic_write_no_partial_file(self):
        """En cas d'erreur, aucun fichier partiel ne doit rester."""
        project = _project_with_time_remapping()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kut", delete=False) as f:
            filepath = f.name

        try:
            # Écrire le projet
            save_project(project, filepath)

            # Vérifier que le fichier existe et est valide
            assert Path(filepath).exists()

            # Charger et vérifier
            loaded = load_project(filepath)
            assert len(loaded.tracks[0].clips) == 5

            # Vérifier qu'il n'y a pas de fichier .tmp résiduel
            tmp_files = list(Path(filepath).parent.glob(f"{Path(filepath).name}.*.tmp"))
            assert len(tmp_files) == 0

        finally:
            Path(filepath).unlink(missing_ok=True)


# --- Courbe de vitesse, interpolation, audio : formes sérialisées et compatibilité ------------------------------------------


def _roundtrip(project: Project) -> Project:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "p.kut"
        save_project(project, str(path))
        return load_project(str(path))


def _find_clip_dict(payload: dict, clip_id: str) -> dict:
    """Le dictionnaire du clip ``clip_id`` **dans** ce payload (modifiable en place)."""
    for sequence in payload["project"].get("sequences", [payload["project"]]):
        for track in sequence["tracks"]:
            for clip in track["clips"]:
                if clip["id"] == clip_id:
                    return clip
    raise KeyError(clip_id)


def _clip_payload(project: Project, clip_id: str) -> dict:
    return _find_clip_dict(project_payload(project), clip_id)


def test_a_clip_without_the_new_time_settings_writes_exactly_the_historical_keys():
    """Un clip qui n'utilise rien de nouveau garde la forme d'avant : aucune clé de plus."""
    project = _project_with_time_remapping()
    assert set(_clip_payload(project, "clip-fast")["time_remapping"]) == {
        "speed", "reverse", "freeze_mode", "freeze_source_time", "freeze_duration"
    }


def test_the_new_time_settings_are_written_only_when_they_differ_from_the_default():
    from dataclasses import replace

    project = _project_with_time_remapping()
    clip = project.tracks[0].clips[0]
    clip.time_remapping = replace(
        clip.time_remapping, interpolation="optical_flow", flow_quality="best", preserve_pitch=False,
        remap_audio=False, anchor=4.0, duration=3.0,
    )
    written = _clip_payload(project, clip.id)["time_remapping"]
    assert written["interpolation"] == "optical_flow" and written["flow_quality"] == "best"
    assert written["preserve_pitch"] is False and written["remap_audio"] is False
    assert written["anchor"] == 4.0 and written["duration"] == 3.0


def test_the_time_settings_and_the_speed_curve_round_trip_exactly():
    from core.time_ops import add_speed_point, set_clip_interpolation, set_clip_preserve_pitch, set_clip_remap_audio
    from core.time_map import SPEED_PROPERTY

    project, clip = _ramp_project()
    for moment, speed in ((0.0, 1.0), (2.0, 1.0), (3.0, 0.25), (6.0, 0.25), (7.0, 2.0)):
        add_speed_point(project, "c1", moment, speed)
    set_clip_interpolation(project, "c1", "blending", "draft")
    set_clip_preserve_pitch(project, "c1", False)
    set_clip_remap_audio(project, "c1", False)
    loaded = _roundtrip(project).tracks[0].clips[0]
    assert loaded.time_remapping == clip.time_remapping
    assert [(k.time_seconds, k.value, k.interpolation) for k in loaded.animation if k.property_name == SPEED_PROPERTY] == [
        (k.time_seconds, k.value, k.interpolation) for k in clip.animation if k.property_name == SPEED_PROPERTY
    ]
    assert loaded.duration == clip.duration and loaded.time_map.source_time(5.0) == clip.time_map.source_time(5.0)


def _ramp_project():
    asset = MediaAsset("a", "/tmp/x.mp4", "x", 200.0, 1920, 1080, 25.0, "video", True)
    clip = Clip("c1", "a", "V1", 3.0, 10.0, 60.0)
    project = Project("p", width=1280, height=720, fps=25.0, media_assets=[asset],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    return project, clip


def test_an_old_project_with_a_constant_speed_gives_exactly_the_same_edit():
    """``speed = 2.0`` dans un fichier ancien : mêmes bornes, même durée, même image montrée, au bit près."""
    project = _project_with_time_remapping()
    payload = project_payload(project)
    for sequence in payload["project"].get("sequences", [payload["project"]]):
        for track in sequence["tracks"]:
            for clip in track["clips"]:
                clip["time_remapping"] = {k: v for k, v in clip["time_remapping"].items()
                                          if k in {"speed", "reverse", "freeze_mode", "freeze_source_time", "freeze_duration"}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.kut"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_project(str(path))
    original = {c.id: c for t in project.tracks for c in t.clips}
    for clip in (c for t in loaded.tracks for c in t.clips):
        before = original[clip.id]
        assert clip.duration == before.duration and clip.time_remapping == before.time_remapping
        assert not clip.has_speed_curve
        for fraction in (0.0, 0.3, 0.77):
            t = before.duration * fraction
            assert clip.time_map.source_time(t) == before.time_map.source_time(t)


def test_an_unknown_interpolation_falls_back_to_sampling_without_losing_the_speed():
    """Fichier d'une version plus récente ou édité à la main : seul le champ fautif retombe sur son défaut sûr."""
    project, clip = _ramp_project()
    clip.time_remapping = TimeRemapping(speed=2.0, reverse=True)
    payload = project_payload(project)
    raw = _find_clip_dict(payload, "c1")["time_remapping"]
    raw.update({"interpolation": "tricubic-from-the-future", "flow_quality": "ultra", "anchor": "x", "duration": -3})
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "future.kut"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_project(str(path)).tracks[0].clips[0]
    remapping = loaded.time_remapping
    assert remapping.speed == 2.0 and remapping.reverse is True
    assert remapping.interpolation.value == "sampling" and remapping.flow_quality.value == "auto"
    assert remapping.anchor is None and remapping.duration is None

