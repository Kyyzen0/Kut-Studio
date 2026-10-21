"""Mémoire d'un export (défaut D-002 de la recette) : l'estimation de ce que FFmpeg retient, et le refus avant de le lancer.

Le mécanisme est celui de ``core/export_memory.py`` : plusieurs morceaux d'un même média (ou d'un même angle multicam) partagent
un flux, et ``overlay`` n'émet rien avant la première image de chaque étage, donc FFmpeg garde en RGBA pleine trame tout ce
qui sépare le premier morceau du dernier. Ces tests le prouvent sur le plan (formule exacte), sur le vrai FFmpeg (mesure par
**rapport** entre deux tailles, jamais un seuil absolu de mémoire ou de durée) et sur les trois chemins d'export (script,
interface, exécuteur).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import core.export_engine as engine_module
import core.export_memory as export_memory
import core.export_runner as runner_module
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, build_export_command, check_export_request
from core.export_memory import MEMORY_SHARE, UNKNOWN_MEMORY_BYTES, check_export_memory, estimate_export_queue
from core.export_runner import ExportStatus, run_export
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.scripting import Session, apply_script, parse_script
from core.visual_effects import ClipTransform

FRAME = 1920 * 1080 * 4
GIB = 1024**3


def _project(pieces, *, size=(1920, 1080), fps=25.0, transform=None, extra_assets=()) -> Project:
    """Un média de 130 s découpé en ``pieces`` : [(début sur la timeline, entrée source, sortie source), ...]."""
    assets = [MediaAsset("a", "/x/a.mp4", "A", 130.0, size[0], size[1], fps, "video", has_audio=False), *extra_assets]
    clips = [Clip(f"c{i}", "a", "V1", start, s_in, s_out) for i, (start, s_in, s_out) in enumerate(pieces)]
    if transform is not None:
        for clip in clips:
            clip.transform = transform
    return Project("p", width=size[0], height=size[1], fps=fps, media_assets=assets, tracks=[Track("V1", "V1", "video", clips=clips)])


def _request(project: Project, output: str = "/out/x.mp4", size=(1920, 1080), fps=25.0) -> ExportRequest:
    return ExportRequest(
        render_plan=build_render_plan(project), output_path=output, format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=size, crf=30, audio_bitrate="64k"), fps=fps,
    )


def _cut(at: float) -> list[tuple[float, float, float]]:
    """Un clip de 130 s coupé en deux morceaux bout à bout, à ``at`` secondes."""
    return [(0.0, 0.0, at), (at, at, 130.0)]


# --- L'estimation ---------------------------------------------------------------------------------------------------------


def test_a_single_clip_retains_nothing():
    assert estimate_export_queue(_request(_project([(0.0, 0.0, 130.0)]))).bytes == 0


def test_a_cut_clip_retains_everything_before_the_second_piece_in_full_rgba_frames():
    """Mesuré : une coupe à 20 s retient 20 s × 25 i/s d'images RGBA 1080p (≈ 4,1 Go), à 60 s trois fois plus."""
    near = estimate_export_queue(_request(_project(_cut(20.0))))
    far = estimate_export_queue(_request(_project(_cut(60.0))))
    assert near.bytes == 20 * 25 * FRAME
    assert far.bytes == 60 * 25 * FRAME
    assert near.families == far.families == 1


def test_the_queue_does_not_grow_with_the_number_of_pieces_only_with_the_media_before_the_last_one():
    """Dix morceaux de 6 s ou deux de 30 s couvrant les mêmes 60 s : la même file (tout sauf le dernier morceau)."""
    ten = [(i * 6.0, i * 6.0, (i + 1) * 6.0) for i in range(10)]
    two = [(0.0, 0.0, 54.0), (54.0, 54.0, 60.0)]
    assert estimate_export_queue(_request(_project(ten))).bytes == estimate_export_queue(_request(_project(two))).bytes


def test_pieces_of_different_files_retain_nothing():
    other = MediaAsset("b", "/x/b.mp4", "B", 130.0, 1920, 1080, 25.0, "video", has_audio=False)
    project = _project([(0.0, 0.0, 60.0)], extra_assets=(other,))
    project.tracks[0].clips.append(Clip("c9", "b", "V1", 60.0, 60.0, 120.0))
    assert estimate_export_queue(_request(project)).bytes == 0


def test_a_piece_taken_from_the_same_place_retains_nothing():
    """Deux morceaux qui commencent au même instant source n'ont rien entre eux à garder."""
    assert estimate_export_queue(_request(_project([(0.0, 5.0, 25.0), (20.0, 5.0, 25.0)]))).bytes == 0


def test_the_estimate_follows_the_output_size_and_the_clip_scale():
    half_frame = estimate_export_queue(_request(_project(_cut(20.0)), size=(960, 540)))
    assert half_frame.bytes == 20 * 25 * 960 * 540 * 4
    small = estimate_export_queue(_request(_project(_cut(20.0), transform=ClipTransform(scale=0.5))))
    assert small.bytes == 20 * 25 * FRAME // 4


def test_a_zoomed_clip_is_never_counted_larger_than_the_frame():
    """On sous-estime plutôt que de refuser à tort : un agrandissement compte plein cadre."""
    big = estimate_export_queue(_request(_project(_cut(20.0), transform=ClipTransform(scale=3.0))))
    assert big.bytes == 20 * 25 * FRAME


def test_a_retimed_or_still_layer_is_left_out_of_the_estimate():
    from core.time_remapping import TimeRemapping

    project = _project(_cut(20.0))
    project.tracks[0].clips[0].time_remapping = TimeRemapping(speed=2.0)
    assert estimate_export_queue(_request(project)).bytes == 0


def _multicam(switches: list[tuple[float, str]], total: float) -> Project:
    assets = [MediaAsset(f"cam{i}", f"/x/cam{i}.mp4", f"Cam{i}", total + 10.0, 1920, 1080, 25.0, "video", has_audio=True) for i in range(3)]
    project = Project("p", width=1920, height=1080, fps=25.0, media_assets=assets, tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    ops = [
        {"op": "create_multicam_source", "as": "plateau", "name": "Plateau",
         "angles": [{"asset": f"cam{i}", "name": f"A{i}", "offset": 0.0} for i in range(3)]},
        {"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "A0", "source_in": 0.0, "source_out": total},
        *({"op": "switch_multicam_angle", "at": at, "angle": angle} for at, angle in switches),
    ]
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report.error
    return project


def test_a_multicam_montage_retains_the_angles_it_leaves_before_the_last_switch():
    """Trois angles, un changement toutes les 10 s sur 60 s : chaque angle retient ses segments sauf le dernier."""
    switches = [(10.0, "A1"), (20.0, "A2"), (30.0, "A0"), (40.0, "A1"), (50.0, "A2")]
    queue = estimate_export_queue(_request(_multicam(switches, 60.0)))
    # A0 : segments 0-10 et 30-40 (le dernier à 30 s) -> 10 s ; A1 : 10-20 et 40-50 -> 10 s ; A2 : 20-30 et 50-60 -> 10 s.
    assert queue.bytes == 30 * 25 * FRAME
    assert queue.families == 3


# --- Le refus, avant FFmpeg ----------------------------------------------------------------------------------------------


def test_an_export_that_cannot_fit_in_memory_is_refused_in_french_with_the_estimate():
    request = _request(_project(_cut(60.0)))      # 60 s × 25 i/s × 8,3 Mo ≈ 11,6 Go
    with pytest.raises(ValueError) as refused:
        check_export_memory(request, total_bytes=8 * GIB)
    message = str(refused.value)
    assert "trop complexe pour être exporté d'un seul tenant" in message
    assert "mémoire estimée 12 Go" in message
    assert "8 Go" in message
    assert "/x/a.mp4" not in message


def test_the_same_montage_is_accepted_on_a_machine_with_enough_memory():
    request = _request(_project(_cut(60.0)))
    check_export_memory(request, total_bytes=64 * GIB)


def test_the_limit_is_a_share_of_the_physical_memory():
    request = _request(_project(_cut(20.0)))       # 20 s × 25 × FRAME
    queue = 20 * 25 * FRAME
    check_export_memory(request, total_bytes=int(queue / MEMORY_SHARE) + 1)
    with pytest.raises(ValueError):
        check_export_memory(request, total_bytes=int(queue / MEMORY_SHARE) - 1)


def test_an_unknown_memory_size_only_refuses_gigantic_queues(monkeypatch):
    monkeypatch.setattr(export_memory, "physical_memory_bytes", lambda: None)
    check_export_memory(_request(_project(_cut(60.0))))
    assert 130 * 25 * FRAME < UNKNOWN_MEMORY_BYTES * MEMORY_SHARE
    huge = Project("p", width=1920, height=1080, fps=25.0,
                   media_assets=[MediaAsset("a", "/x/a.mp4", "A", 3600.0, 1920, 1080, 25.0, "video", has_audio=False)],
                   tracks=[Track("V1", "V1", "video", clips=[Clip("c0", "a", "V1", 0.0, 0.0, 1500.0), Clip("c1", "a", "V1", 1500.0, 1500.0, 1600.0)])])
    with pytest.raises(ValueError, match="trop complexe"):
        check_export_memory(_request(huge))


def test_the_d002_montage_is_refused_on_a_24_gb_machine():
    """Le montage de la recette : 20 min de multicam à 3 angles et ≈ 30 changements d'angle."""
    switches = [(40.0 * i, f"A{i % 3}") for i in range(1, 30)]
    request = _request(_multicam(switches, 1200.0))
    assert estimate_export_queue(request).bytes > 150 * GIB
    with pytest.raises(ValueError, match="mémoire estimée"):
        check_export_memory(request, total_bytes=24 * GIB)


def test_every_export_entry_point_applies_the_refusal(monkeypatch, tmp_path):
    """Script (``core.cli`` -> ``run_export``), interface (``ExportEngine.start``) et ``check_export_request`` : le même refus."""
    monkeypatch.setattr(export_memory, "physical_memory_bytes", lambda: 8 * GIB)
    request = _request(_project(_cut(60.0)), output=str(tmp_path / "out.mp4"))

    with pytest.raises(ValueError, match="trop complexe"):
        check_export_request(request)

    launched: list[object] = []
    monkeypatch.setattr(runner_module, "_run_ffmpeg", lambda *a, **k: launched.append(a))
    result = run_export(request)
    assert result.status is ExportStatus.FAILED
    assert "trop complexe" in result.message
    assert not Path(request.output_path).exists()

    engine = ExportEngine()
    failures: list[str] = []
    engine.failed.connect(failures.append)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(engine_module, "_ffmpeg_path", "/usr/bin/ffmpeg")
    engine.start(request)
    assert len(failures) == 1 and "trop complexe" in failures[0]
    assert launched == []


def test_ordinary_montages_are_never_refused(monkeypatch):
    """Pas de faux positif : un clip seul, des clips de médias différents, des morceaux contigus de petits médias."""
    monkeypatch.setattr(export_memory, "physical_memory_bytes", lambda: 8 * GIB)
    check_export_memory(_request(_project([(0.0, 0.0, 130.0)])))
    check_export_memory(_request(_project(_cut(2.0))))               # 2 s de file : 0,4 Go
    check_export_memory(_request(_multicam([(10.0, "A1"), (20.0, "A2")], 30.0)))


# --- La loi, sur le vrai FFmpeg ----------------------------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
needs_ps = pytest.mark.skipif(sys.platform.startswith("win") or shutil.which("ps") is None, reason="mesure de la mémoire par ps indisponible")
SOURCE_SECONDS = 60
W, H, FPS = 480, 270, 25


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent")
    path = tmp_path_factory.mktemp("memory") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={SOURCE_SECONDS},format=yuv420p",
         "-c:v", "libx264", "-crf", "35", "-preset", "ultrafast", str(path)], check=True, timeout=120)
    return str(path)


def _peak_rss_mb(arguments: list[str]) -> float:
    """Pic de mémoire résidente de FFmpeg (``ps`` toutes les 10 ms) ; ne mesure que ce processus."""
    process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    peak = 0.0
    try:
        while process.poll() is None:
            done = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True, timeout=5, check=False)
            text = done.stdout.strip()
            if text.isdigit():
                peak = max(peak, int(text) / 1024.0)
            time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)
    assert process.returncode == 0
    return peak


def _measure(path: str, at: float | None, tmp_path: Path, tag: str) -> tuple[float, int]:
    asset = MediaAsset("a", path, "m", float(SOURCE_SECONDS), W, H, float(FPS), "video", has_audio=False)
    pieces = [(0.0, 0.0, float(SOURCE_SECONDS))] if at is None else [(0.0, 0.0, at), (at, at, float(SOURCE_SECONDS))]
    clips = [Clip(f"c{i}", "a", "V1", start, s_in, s_out) for i, (start, s_in, s_out) in enumerate(pieces)]
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=clips)])
    request = ExportRequest(
        render_plan=build_render_plan(project), output_path=str(tmp_path / f"{tag}.mp4"), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=(W, H), crf=35, audio_bitrate="64k"), fps=float(FPS),
    )
    temporary: list[str] = []
    command = build_export_command(request, temporary_files=temporary)
    try:
        peak = _peak_rss_mb(list(command.arguments))
    finally:
        for name in temporary:
            Path(name).unlink(missing_ok=True)
    return peak, estimate_export_queue(request).bytes


@needs_ffmpeg
@needs_ps
def test_ffmpeg_memory_grows_with_the_media_before_the_last_piece_as_the_estimate_says(media, tmp_path):
    """Le rapport des surcoûts mesurés (coupe à 45 s / coupe à 15 s) est celui de l'estimation (3), à FFmpeg près : le seul
    montage découpé plus loin retient trois fois plus. Sans coupe (référence), rien ne s'accumule."""
    # Le pic le plus bas de trois mesures : une mesure ne peut qu'être gonflée par la charge de la machine.
    base = min(_measure(media, None, tmp_path, f"base{i}")[0] for i in range(3))
    near = min(_measure(media, 15.0, tmp_path, f"near{i}")[0] for i in range(3))
    far = min(_measure(media, 45.0, tmp_path, f"far{i}")[0] for i in range(3))
    estimates = _measure(media, 15.0, tmp_path, "e1")[1], _measure(media, 45.0, tmp_path, "e2")[1]
    assert estimates[1] == pytest.approx(3 * estimates[0])
    extra_near, extra_far = near - base, far - base
    assert extra_near > 0.0, "une coupe doit retenir des images (FFmpeg a changé de comportement ?)"
    assert 2.0 <= extra_far / extra_near <= 4.5
