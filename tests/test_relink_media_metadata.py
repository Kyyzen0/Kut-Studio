"""Relier un média relit aussi ses métadonnées de tournage, et prévient quand le timecode ne correspond plus.

Un timecode périmé fausserait la synchronisation Multicam : le fichier relié (copie transcodée qui a perdu ses balises,
autre prise) remplace les valeurs de l'ancien, y compris pour les effacer.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import pytest

from core.library_organization import LibraryOrganization, relink_differences
from core.media_probe import probe_media
from core.project_model import MediaAsset, Project
from core.timecode import asset_start_seconds


def _clip(**fields) -> MediaAsset:
    base = {"id": "v", "path": "/old/a.mp4", "name": "a.mp4", "duration": 5.0, "width": 640, "height": 360, "fps": 25.0,
            "media_type": "video", "has_audio": True}
    base.update(fields)
    return MediaAsset(**base)


def _relink(before: MediaAsset, fresh: MediaAsset) -> MediaAsset:
    project = Project(name="r", media_assets=[before])
    LibraryOrganization(project).relink_asset(before.id, "/new/b.mp4", probe=lambda path: replace(fresh, path=path))
    return project.media_assets[0]


def test_relinking_replaces_the_metadata_with_the_new_files():
    before = _clip(timecode="01:00:00:00", reel="A001", camera="Sony A7S III", creation_time="2026-03-14T09:00:00Z")
    fresh = _clip(timecode="02:30:00:12", reel="B007", camera="Canon C70", creation_time="2026-03-15T10:00:00Z",
                  timecode_fps=0.0)
    linked = _relink(before, fresh)
    assert (linked.timecode, linked.reel, linked.camera, linked.creation_time) == (
        "02:30:00:12", "B007", "Canon C70", "2026-03-15T10:00:00Z")
    assert asset_start_seconds(linked) == pytest.approx(2 * 3600 + 30 * 60 + 12 / 25)


def test_relinking_to_a_file_without_timecode_clears_every_metadata():
    before = _clip(timecode="01:00:00;02", timecode_fps=30000 / 1001, reel="A001", camera="Sony", creation_time="2026-01-01T00:00:00Z")
    linked = _relink(before, _clip())
    assert (linked.timecode, linked.timecode_fps, linked.time_reference, linked.reel, linked.camera,
            linked.creation_time) == ("", 0.0, None, "", "", "")
    assert asset_start_seconds(linked) is None


def test_relinking_a_bwf_recording_follows_its_time_reference():
    before = MediaAsset("a", "/old/a.wav", "a", 30.0, 0, 0, 0.0, "audio", True, time_reference=3600.0)
    project = Project(name="r", media_assets=[before])
    LibraryOrganization(project).relink_asset(
        "a", "/new/a.wav", probe=lambda path: MediaAsset("x", path, "a", 30.0, 0, 0, 0.0, "audio", True, time_reference=7200.0))
    assert project.media_assets[0].time_reference == 7200.0
    LibraryOrganization(project).relink_asset(
        "a", "/new/b.wav", probe=lambda path: MediaAsset("x", path, "a", 30.0, 0, 0, 0.0, "audio", True))
    assert project.media_assets[0].time_reference is None


def test_a_relink_keeps_the_identity_of_the_asset_while_swapping_the_metadata():
    before = _clip(timecode="01:00:00:00", name="Prise 1")
    linked = _relink(before, _clip(timecode="09:00:00:00", name="autre"))
    assert (linked.id, linked.name) == ("v", "Prise 1")


def test_differences_report_a_changed_or_lost_timecode():
    before = _clip(timecode="01:00:00:00")
    assert relink_differences(before, _clip(timecode="01:00:00:00")) == []
    assert relink_differences(before, _clip(timecode="02:00:00:00")) == ["timecode 01:00:00:00 → 02:00:00:00"]
    assert relink_differences(before, _clip()) == ["timecode 01:00:00:00 → aucun"]
    assert relink_differences(_clip(), before) == ["timecode aucun → 01:00:00:00"]


def test_differences_report_a_changed_bwf_time_reference():
    audio = MediaAsset("a", "/m/a.wav", "a", 30.0, 0, 0, 0.0, "audio", True, time_reference=3600.0)
    other = replace(audio, time_reference=3601.25)
    assert relink_differences(audio, other) == ["heure BWF 01:00:00.000 → 01:00:01.250"]
    assert relink_differences(audio, replace(audio, time_reference=None)) == ["heure BWF 01:00:00.000 → aucun"]


def test_differences_still_report_a_29_97_to_30_change_without_truncation():
    changes = relink_differences(_clip(fps=30000 / 1001), _clip(fps=30.0))
    assert changes == ["cadence 29.97 → 30 i/s"]


def test_reel_camera_and_creation_date_alone_are_not_a_warning():
    """Une copie transcodée garde la même prise : seuls les repères de synchronisation justifient l'avertissement."""
    before = _clip(timecode="01:00:00:00", reel="A001", camera="Sony", creation_time="2026-01-01T00:00:00Z")
    after = _clip(timecode="01:00:00:00", reel="", camera="", creation_time="")
    assert relink_differences(before, after) == []


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="FFmpeg / ffprobe indisponibles")


def _make(path, *, timecode=None):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if timecode:
        args += ["-timecode", timecode]
    completed = subprocess.run([*args, str(path)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr[-300:]
    return str(path)


@needs_ffmpeg
def test_relinking_real_files_moves_and_then_clears_the_timecode(tmp_path):
    with_timecode = _make(tmp_path / "tc.mp4", timecode="01:02:03:04")
    without = _make(tmp_path / "plain.mp4")
    asset = probe_media(with_timecode)
    project = Project(name="r", media_assets=[asset])
    before = replace(asset)
    LibraryOrganization(project).relink_asset(asset.id, without)
    assert asset.timecode == "" and asset_start_seconds(asset) is None
    assert relink_differences(before, asset) == ["timecode 01:02:03:04 → aucun"]
    LibraryOrganization(project).relink_asset(asset.id, with_timecode)
    assert asset.timecode == "01:02:03:04"
