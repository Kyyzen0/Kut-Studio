"""Export social : presets réseaux, copie d'aperçu légère (< 30 Mo) et image de couverture (vrai FFmpeg)."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from core.export_engine import ExportEngine
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_job import RenderJob
from core.render_presets import builtin_presets, custom_preset, get_preset, with_deliverables
from core.render_queue import RenderQueue
from core.render_queue_store import RenderQueueStore
from core.social_deliverables import (
    PREVIEW_MAX_BYTES, cover_path, cover_seek, make_preview_copy, preview_copy_path, preview_size, preview_video_kbps,
)
from core.timeline_editing import set_cover_marker
from render_probe import needs_ffmpeg

W, H, FPS, SECONDS = 96, 160, 10, 3.0


# --- Presets et réglages ----------------------------------------------------------------------------------------


def test_social_presets_deliver_the_platform_frame_with_loudness_copy_and_cover():
    # Cadence ``None`` : celle de la séquence (seul « TikTok 60 fps » l'impose, il l'annonce dans son nom).
    expected = {"tiktok": (1080, 1920, None), "tiktok_60": (1080, 1920, 60.0), "reels": (1080, 1920, None),
                "shorts": (1080, 1920, None), "instagram_feed_4_5": (1080, 1350, None), "square": (1080, 1080, None)}
    for preset_id, (width, height, fps) in expected.items():
        spec = get_preset(preset_id)
        assert (spec.width, spec.height, spec.fps) == (width, height, fps), preset_id
        assert spec.loudness_lufs == -14.0 and spec.preview_copy and spec.cover, preset_id
    plain = get_preset("youtube")
    assert not (plain.preview_copy or plain.cover)
    assert len({spec.id for spec in builtin_presets()}) == len(builtin_presets())


def test_deliverable_options_survive_the_queue_file_and_old_jobs_load_without_them():
    spec = with_deliverables(get_preset("youtube"), preview_copy=True, cover=False)
    job = RenderJob.create(project_fps=30.0, spec=spec, snapshot_path="s", output_path="o.mp4", cover_seconds=2.5)
    job.extras, job.extras_error = ["o-couverture.jpg"], "x"
    again = RenderJob.from_dict(json.loads(json.dumps(job.to_dict())))
    assert (again.preview_copy, again.cover, again.cover_seconds, again.extras, again.extras_error) == (
        True, False, 2.5, ["o-couverture.jpg"], "x")
    old = {key: value for key, value in job.to_dict().items()
           if key not in {"preview_copy", "cover", "cover_seconds", "extras", "extras_error"}}
    legacy = RenderJob.from_dict(old)
    assert (legacy.preview_copy, legacy.cover, legacy.cover_seconds, legacy.extras) == (False, False, 0.0, [])
    garbage = RenderJob.from_dict({**old, "extras": "nope", "cover": "yes", "cover_seconds": -3})
    assert (garbage.extras, garbage.cover, garbage.cover_seconds) == ([], False, 0.0)
    again.mark_waiting()                                    # une relance efface les livrables de l'essai précédent
    assert again.extras == [] and again.extras_error == ""


def test_the_preview_copy_is_720p_and_its_bitrate_fits_the_duration():
    assert preview_size(1080, 1920) == (720, 1280)
    assert preview_size(1920, 1080) == (1280, 720)
    assert preview_size(1080, 1350) == (720, 900)
    assert preview_size(640, 360) == (640, 360)              # jamais agrandie
    assert preview_video_kbps(35.0) == 5000                  # plafonné : 720p n'en demande pas plus
    assert preview_video_kbps(600.0) == 264                  # 30 Mo × 0,9 sur 10 min, moins l'audio
    assert preview_video_kbps(3600.0) == 250
    assert preview_copy_path("/x/run.mp4").name == "run-apercu.mp4"
    assert cover_path("/x/run.mp4").name == "run-couverture.jpg"


def test_the_cover_seek_lands_inside_the_wanted_frame():
    assert cover_seek(1.25, 3.0, 10) == pytest.approx(1.175)  # image 12 : on vise 1/4 d'image avant son début
    assert cover_seek(1.2, 3.0, 10) == pytest.approx(1.175)   # pile sur le début de l'image 12
    assert cover_seek(9.0, 3.0, 10) == pytest.approx(2.875)   # au-delà de la fin : la dernière image
    assert cover_seek(0.0, 3.0, 10) == 0.0


# --- Vrai FFmpeg --------------------------------------------------------------------------------------------------


def _counting_video(path) -> None:
    """Image k : gris uniforme de luminance 16 + 8·k (chaque image se reconnaît à sa moyenne)."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"nullsrc=s={W}x{H}:r={FPS}:d={SECONDS},geq=lum=16+N*8:cb=128:cr=128",
                    "-f", "lavfi", "-i", f"sine=f=330:sample_rate=48000:duration={SECONDS}",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, timeout=60)


def _gray_means(path, frames: int | None = None) -> list[float]:
    command = ["ffmpeg", "-v", "error", "-i", str(path)]
    if frames:
        command += ["-frames:v", str(frames)]
    raw = subprocess.run([*command, "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True,
                         timeout=60).stdout
    images = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    return [float(image.mean()) for image in images]


def _probe(path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


@needs_ffmpeg
def test_a_social_export_delivers_the_cover_at_its_marker_and_a_light_copy(qtbot, tmp_path):
    source = tmp_path / "count.mp4"
    _counting_video(source)
    asset = MediaAsset("v", str(source), "v", SECONDS, W, H, float(FPS), "video", True)
    project = Project("deliver", width=W, height=H, fps=float(FPS), media_assets=[asset], tracks=[
        Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, SECONDS)])])
    set_cover_marker(project, 0.4)
    set_cover_marker(project, 1.25)                         # un seul marqueur de couverture : le dernier posé
    assert [marker.time_seconds for marker in project.markers if marker.category == "cover"] == [1.25]
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    try:
        spec = with_deliverables(custom_preset(width=W, height=H, fps=FPS, quality=0), preview_copy=True, cover=True)
        job = queue.enqueue(project, spec, str(tmp_path / "run.mp4"), playhead_seconds=2.6)
        assert job.cover_seconds == 1.25                    # le marqueur l'emporte sur la tête de lecture
        queue.start_job(job.id)
        qtbot.waitUntil(lambda: not queue.is_running, timeout=120000)
        assert job.error_message == "" and job.extras_error == "", job.extras_error
        cover, copy = cover_path(job.output_path), preview_copy_path(job.output_path)
        assert sorted(job.extras) == sorted([str(cover), str(copy)])
        frames = _gray_means(job.output_path)
        (cover_mean,) = _gray_means(cover)
        assert int(np.argmin([abs(mean - cover_mean) for mean in frames])) == 12
        info = _probe(copy)
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        assert (video["width"], video["height"]) == (W, H)  # déjà sous 720 px : taille gardée
        assert any(stream["codec_type"] == "audio" for stream in info["streams"])
        assert copy.stat().st_size < PREVIEW_MAX_BYTES
    finally:
        queue.shutdown()


@needs_ffmpeg
def test_the_preview_copy_stays_under_its_size_cap_on_hard_footage(tmp_path):
    """Du bruit plein cadre (le pire cas d'un encodeur) en 1080×1920 : la copie respecte le plafond qu'on lui donne."""
    source = tmp_path / "noise.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=1080x1920:r=30:d=3,geq=lum=random(1)*255:cb=128:cr=128",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p", str(source)],
                   check=True, timeout=120)
    cap = 400_000
    copy = make_preview_copy(str(source), 1080, 1920, 3.0, max_bytes=cap)
    assert copy.stat().st_size <= cap
    video = next(stream for stream in _probe(copy)["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)


def test_a_deliverable_never_takes_an_existing_file_or_a_queued_output(tmp_path):
    from core.social_deliverables import free_path

    wanted = tmp_path / "run-apercu.mp4"
    assert free_path(wanted) == wanted
    wanted.write_bytes(b"export precedent")
    assert free_path(wanted).name == "run-apercu-2.mp4"
    assert free_path(wanted, [str(tmp_path / "run-apercu-2.mp4")]).name == "run-apercu-3.mp4"


@needs_ffmpeg
def test_an_existing_preview_named_file_is_kept_intact(tmp_path):
    source = tmp_path / "run.mp4"
    _counting_video(source)
    previous = tmp_path / "run-apercu.mp4"
    previous.write_bytes(b"un autre export, deja termine")
    copy = make_preview_copy(str(source), W, H, SECONDS)
    assert copy.name == "run-apercu-2.mp4" and copy.stat().st_size > 1000
    assert previous.read_bytes() == b"un autre export, deja termine"
