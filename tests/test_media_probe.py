"""Tests unitaires pour les sondes ``core.media_probe``.

Tous les tests mockent ``subprocess.run`` et ``shutil.which`` : ils ne
dépendent ni d'un vrai ``ffprobe`` ni d'un fichier vidéo réel.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.media_probe import MediaProbeError, probe_media, probe_video


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


def _video_stream(
    *,
    width=1920,
    height=1080,
    fps_num=30,
    fps_den=1,
    duration=None,
    has_audio_separately=False,
):
    """Construit un flux vidéo ffprobe."""
    stream = {
        "codec_type": "video",
        "width": width,
        "height": height,
        "avg_frame_rate": f"{fps_num}/{fps_den}",
        "r_frame_rate": f"{fps_num}/{fps_den}",
    }
    if duration is not None:
        stream["duration"] = str(duration)
    return stream


def _audio_stream():
    """Construit un flux audio ffprobe."""
    return {
        "codec_type": "audio",
    }


def _payload(*, streams, duration):
    """Construit un payload JSON ffprobe générique."""
    return {
        "streams": streams,
        "format": {"duration": str(duration)},
    }


# ---------------------------------------------------------------------------
# probe_video (compatibilité)
# ---------------------------------------------------------------------------


def test_probe_video_returns_a_valid_media_asset(tmp_path: Path, monkeypatch):
    """Tous les champs attendus sont lus depuis ffprobe."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    payload = _payload(
        streams=[_video_stream(width=1280, height=720, fps_num=60000, fps_den=1001)],
        duration=42.0,
    )
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
    # Pas de flux audio séparé dans le payload → has_audio=False.
    assert asset.has_audio is False


def test_probe_video_detects_separate_audio_stream(tmp_path: Path, monkeypatch):
    """Une vidéo avec un flux audio séparé est marquée ``has_audio=True``."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00")

    payload = _payload(
        streams=[_video_stream(), _audio_stream()],
        duration=10.0,
    )
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    asset = probe_video(str(video_path))
    assert asset.media_type == "video"
    assert asset.has_audio is True


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
    # Un développeur (ou le job libass de la CI) peut désigner un ffprobe par ces variables : elles priment sur le PATH.
    monkeypatch.delenv("KUT_STUDIO_FFPROBE", raising=False)
    monkeypatch.delenv("KUT_STUDIO_FFMPEG_DIR", raising=False)
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
        lambda *args, **kwargs: _completed_process(
            json.dumps(_payload(streams=[_audio_stream()], duration=5.0))
        ),
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
            json.dumps(_payload(
                streams=[_video_stream(width=0, height=0)],
                duration=1.0,
            ))
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
            json.dumps(_payload(
                streams=[_video_stream(fps_num=0, fps_den=1)],
                duration=1.0,
            ))
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


# ---------------------------------------------------------------------------
# probe_media — audio seul
# ---------------------------------------------------------------------------


def test_probe_media_audio_only_returns_audio_asset(tmp_path: Path, monkeypatch):
    """Un fichier ne contenant que de l'audio est reconnu comme tel."""
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"\x00")

    payload = _payload(streams=[_audio_stream()], duration=180.5)
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    asset = probe_media(str(audio_path))

    assert asset.path == str(audio_path)
    assert asset.name == "song.mp3"
    assert asset.media_type == "audio"
    assert asset.has_audio is True
    assert asset.width == 0
    assert asset.height == 0
    assert asset.fps == 0.0
    assert asset.duration == pytest.approx(180.5)


def test_probe_media_raises_when_neither_audio_nor_video(tmp_path: Path, monkeypatch):
    """Un fichier sans flux exploitable lève une ``MediaProbeError``."""
    media_path = tmp_path / "weird.dat"
    media_path.write_bytes(b"\x00")

    payload = {"streams": [{"codec_type": "subtitle"}], "format": {"duration": "1.0"}}
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    with pytest.raises(MediaProbeError, match="flux vidéo"):
        probe_media(str(media_path))


def test_probe_media_raises_on_invalid_audio_duration(tmp_path: Path, monkeypatch):
    """Un audio avec une durée nulle ou négative est rejeté."""
    audio_path = tmp_path / "empty.mp3"
    audio_path.write_bytes(b"\x00")

    payload = _payload(streams=[_audio_stream()], duration=0.0)
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    with pytest.raises(MediaProbeError, match="Durée audio"):
        probe_media(str(audio_path))


def test_probe_media_handles_video_only_with_no_audio(tmp_path: Path, monkeypatch):
    """Une vidéo sans flux audio séparé reste ``has_audio=False``."""
    video_path = tmp_path / "silent.mp4"
    video_path.write_bytes(b"\x00")
    payload = _payload(streams=[_video_stream()], duration=4.0)
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "core.media_probe.subprocess.run",
        lambda *args, **kwargs: _completed_process(json.dumps(payload)),
    )

    asset = probe_media(str(video_path))
    assert asset.media_type == "video"
    assert asset.has_audio is False