"""Tests unitaires pour ``core.media_probe.probe_video``.

Tous les tests mockent ``subprocess.run`` et ``shutil.which`` : ils ne
dépendent ni d'un vrai ``ffprobe`` ni d'un fichier vidéo réel.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.media_probe import MediaProbeError, probe_video


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Construit un objet ``CompletedProcess`` pour stubber ``subprocess.run``."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def _ffprobe_payload(width=1920, height=1080, fps_num=30, fps_den=1, duration=12.5):
    """Construit un payload JSON plausible pour ffprobe."""
    return {
        "streams": [
            {
                "width": width,
                "height": height,
                "avg_frame_rate": f"{fps_num}/{fps_den}",
                "r_frame_rate": f"{fps_num}/{fps_den}",
                "duration": str(duration),
            }
        ],
        "format": {"duration": str(duration)},
    }


# ---------------------------------------------------------------------------
# Cas nominal
# ---------------------------------------------------------------------------


def test_probe_video_returns_a_valid_media_asset(tmp_path: Path, monkeypatch):
    """Tous les champs attendus sont lus depuis ffprobe."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")  # un fichier qui existe suffit

    payload = _ffprobe_payload(width=1280, height=720, fps_num=60000, fps_den=1001, duration=42.0)
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    asset = probe_video(str(video_path))

    assert asset.path == str(video_path)
    assert asset.name == "clip.mp4"
    assert asset.media_type == "video"
    assert asset.width == 1280
    assert asset.height == 720
    assert asset.duration == pytest.approx(42.0)
    # 60000/1001 ≈ 59.94
    assert asset.fps == pytest.approx(59.94, rel=1e-3)
    assert asset.id.startswith("asset-")


def test_probe_video_falls_back_on_format_duration_when_stream_duration_is_missing(
    tmp_path: Path, monkeypatch
):
    """Si la durée n'est pas dans le flux, le conteneur ``format`` est utilisé."""
    video_path = tmp_path / "no_stream_duration.mov"
    video_path.write_bytes(b"\x00")

    payload = {
        "streams": [
            {
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "duration": None,
            }
        ],
        "format": {"duration": "17.5"},
    }
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    asset = probe_video(str(video_path))
    assert asset.duration == pytest.approx(17.5)


# ---------------------------------------------------------------------------
# Cas d'erreur
# ---------------------------------------------------------------------------


def test_probe_video_raises_when_file_is_missing(tmp_path: Path, monkeypatch):
    """Un fichier inexistant lève une ``MediaProbeError`` claire."""
    missing = tmp_path / "ghost.mp4"
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    # ``subprocess.run`` ne doit même pas être appelé.
    def fail(*args, **kwargs):
        raise AssertionError("subprocess.run ne doit pas être appelé")
    monkeypatch.setattr("core.media_probe.subprocess.run", fail)

    with pytest.raises(MediaProbeError, match="introuvable"):
        probe_video(str(missing))


def test_probe_video_raises_when_ffprobe_is_missing(tmp_path: Path, monkeypatch):
    """Si ``ffprobe`` n'est pas dans le PATH, l'erreur est explicite."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: None)

    with pytest.raises(MediaProbeError, match="ffprobe"):
        probe_video(str(video_path))


def test_probe_video_raises_on_ffprobe_failure(tmp_path: Path, monkeypatch):
    """Un code retour non nul de ffprobe produit une erreur claire."""
    video_path = tmp_path / "broken.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(
            stdout="", stderr="Invalid data found when processing input", returncode=1,
        ),
    )

    with pytest.raises(MediaProbeError, match="Invalid data"):
        probe_video(str(video_path))


def test_probe_video_raises_on_invalid_json_output(tmp_path: Path, monkeypatch):
    """Une sortie JSON invalide est refusée avec un message explicite."""
    video_path = tmp_path / "weird.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(stdout="{not valid json"),
    )

    with pytest.raises(MediaProbeError, match="invalide"):
        probe_video(str(video_path))


def test_probe_video_raises_when_no_video_stream(tmp_path: Path, monkeypatch):
    """Un média sans flux vidéo est explicitement rejeté."""
    video_path = tmp_path / "audio_only.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps({"streams": []})),
    )

    with pytest.raises(MediaProbeError, match="flux vidéo"):
        probe_video(str(video_path))


def test_probe_video_raises_on_invalid_dimensions(tmp_path: Path, monkeypatch):
    """Des dimensions à zéro sont rejetées."""
    video_path = tmp_path / "zero.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(
            json.dumps(_ffprobe_payload(width=0, height=0))
        ),
    )

    with pytest.raises(MediaProbeError, match="Dimensions"):
        probe_video(str(video_path))


def test_probe_video_raises_on_invalid_fps(tmp_path: Path, monkeypatch):
    """Un fps à zéro est rejeté (avg_frame_rate ``0/1``)."""
    video_path = tmp_path / "no_fps.mp4"
    video_path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(
            json.dumps(_ffprobe_payload(fps_num=0, fps_den=1))
        ),
    )

    with pytest.raises(MediaProbeError, match="Fréquence"):
        probe_video(str(video_path))


def test_probe_video_does_not_depend_on_pyside6():
    """Le module de sonde n'importe pas PySide6 (vérifié sur le code source)."""
    import re

    source = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "media_probe.py"
    ).read_text(encoding="utf-8")
    stripped = re.sub(r'^\s*""".*?"""\s*', "", source, count=1, flags=re.DOTALL)
    assert not re.search(r"^\s*(?:from|import)\s+PySide", stripped, flags=re.MULTILINE)
