"""Relier un média relit ses métadonnées : le fichier relié peut être différent de l'ancien.

Avant : seul ``asset.path`` changeait. Un fichier relié plus court, plus petit, d'une autre cadence ou sans
audio gardait les valeurs de l'ancien : trims au-delà de la fin, tracking rapporté à la mauvaise taille,
export en échec sur un flux audio inexistant.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import pytest

from core.library_organization import LibraryError, LibraryOrganization, relink_differences
from core.media_probe import probe_media
from core.project_model import MediaAsset, Project

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")


def _ffmpeg(*arguments: str) -> None:
    completed = subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr[-500:]


@pytest.fixture
def files(tmp_path):
    big = tmp_path / "big.mp4"          # 640×360, 30 i/s, 5 s, avec audio
    small = tmp_path / "small.mp4"      # 320×180, 25 i/s, 2 s, sans audio
    sound = tmp_path / "sound.wav"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:d=5", "-f", "lavfi", "-i", "sine=d=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(big))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:d=2", "-pix_fmt", "yuv420p", str(small))
    _ffmpeg("-f", "lavfi", "-i", "sine=d=1", str(sound))
    return {"big": str(big), "small": str(small), "sound": str(sound), "text": str(tmp_path / "notes.txt")}


def _project(files) -> tuple[Project, MediaAsset]:
    asset = probe_media(files["big"])
    return Project(name="Relink", media_assets=[asset]), asset


def test_relinking_to_a_different_file_refreshes_every_metadata(files):
    project, asset = _project(files)
    name = asset.name
    LibraryOrganization(project).relink_asset(asset.id, files["small"])
    real = probe_media(files["small"])
    assert asset.path == files["small"]
    assert (asset.width, asset.height, asset.fps, asset.has_audio) == (320, 180, 25.0, False)
    assert asset.duration == pytest.approx(real.duration)
    assert asset.id == project.media_assets[0].id and asset.name == name            # l'identité du média reste


def test_an_unreadable_file_is_refused_and_the_asset_keeps_its_old_path(files):
    project, asset = _project(files)
    (open(files["text"], "w", encoding="utf-8")).write("pas un média")
    before = replace(asset)
    with pytest.raises(LibraryError, match="ne peut pas être relié"):
        LibraryOrganization(project).relink_asset(asset.id, files["text"])
    with pytest.raises(LibraryError):
        LibraryOrganization(project).relink_asset(asset.id, files["small"].replace("small", "absent"))
    assert asset == before


def test_a_video_cannot_be_relinked_to_an_audio_file_and_back(files):
    project, asset = _project(files)
    before = replace(asset)
    with pytest.raises(LibraryError, match="audio"):
        LibraryOrganization(project).relink_asset(asset.id, files["sound"])
    assert asset == before
    sound = probe_media(files["sound"])
    project.media_assets.append(sound)
    with pytest.raises(LibraryError, match="vidéo"):
        LibraryOrganization(project).relink_asset(sound.id, files["big"])


def test_differences_are_described_in_plain_words(files):
    project, asset = _project(files)
    before = replace(asset)
    LibraryOrganization(project).relink_asset(asset.id, files["small"])
    changes = relink_differences(before, asset)
    assert any(text.startswith("durée 5.00 s → 2.0") for text in changes)
    assert "résolution 640×360 → 320×180" in changes
    assert "cadence 30 → 25 i/s" in changes
    assert "sans audio" in changes


def test_an_identical_file_reports_no_difference(files):
    project, asset = _project(files)
    before = replace(asset)
    LibraryOrganization(project).relink_asset(asset.id, files["big"])
    assert relink_differences(before, asset) == []


def test_the_relink_dialog_tells_the_user_what_changed(qtbot, monkeypatch, files):
    from test_scopes import _window

    import ui.main_window as main_window

    window = _window(qtbot, monkeypatch)
    asset = probe_media(files["big"])
    window.project.media_assets.append(asset)
    monkeypatch.setattr(
        main_window.QFileDialog, "getOpenFileName", staticmethod(lambda *args, **kwargs: (files["small"], ""))
    )
    window.statusBar().clearMessage()

    window._on_asset_relink_requested(asset.id)

    assert (asset.width, asset.height) == (320, 180)
    message = window.statusBar().currentMessage()
    assert "différent" in message and "320×180" in message and "sans audio" in message
