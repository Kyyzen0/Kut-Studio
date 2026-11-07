"""Parité de rendu du correctif D-002, sur le vrai FFmpeg : un montage rendu **avec** les entrées positionnées / les instances par
fenêtre (``core/export_inputs.py``) et **sans** (le graphe d'avant, ``max_pipelines`` ramené à 0) donne les mêmes images, image par
image, et le même son, échantillon par échantillon.

La comparaison se fait avant tout encodeur : ``framemd5`` de chaque image et ``md5`` des échantillons flottants du mixage. Les médias
exercent ce qui a fait diverger le rendu pendant la mise au point : GOP long et images B, cadence variable, conteneur Matroska
dont l'audio commence avant la vidéo (FFmpeg 6.1 et 7.1 décalent alors toutes les images de +21 ms, pas 9), AAC en bruit (son
bit-à-bit sensible au positionnement), cadence du projet différente de celle des médias.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import core.export_inputs as export_inputs
from core.export_engine import ExportEngine, _ffmpeg_command_prefix, decoder_threads, input_arguments
from core.animation import InterpolationType, Keyframe
from core.effects_model import EffectType, create_effect, fx_property_id
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.scripting import Session, apply_script, parse_script
from core.sequences import insert_sequence_clip
from core.time_remapping import TimeRemapping
from core.visual_effects import ClipTransform, TransformKeyframe

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg / ffprobe indisponibles"
)

W, H, FPS = 160, 90, 25
SECONDS = 24


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stderr[-1500:]


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, str]:
    folder = tmp_path_factory.mktemp("parity")
    out: dict[str, str] = {}
    for name, container, seed, gop, vfr in (
        ("a", "mp4", 7, 250, False), ("mkv", "mkv", 13, 50, False), ("vfr", "mp4", 11, 250, True), ("ts", "ts", 17, 60, False),
    ):
        path = folder / f"{name}.{container}"
        video = f"testsrc2=s={W}x{H}:r={FPS}:d={SECONDS}"
        filters = "format=yuv420p" + (",select='not(eq(mod(n,7),3))'" if vfr else "")
        command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i",
                   f"anoisesrc=d={SECONDS}:c=pink:r=48000:a=0.3:seed={seed}", "-vf", filters, "-c:v", "libx264", "-preset", "veryfast",
                   "-bf", "3", "-g", str(gop), "-crf", "30", *(["-fps_mode", "vfr"] if vfr else []), "-c:a", "aac", "-b:a", "96k",
                   "-shortest", str(path)]
        _run(command)
        out[name] = str(path)
    return out


def _assets(media, fps: float = float(FPS)) -> list[MediaAsset]:
    return [MediaAsset(name, path, name, float(SECONDS), W, H, fps, "video", has_audio=True) for name, path in media.items()]


def _project(media, clips_by_track: dict[str, list[Clip]], fps: float = float(FPS)) -> Project:
    tracks = [Track(name, name, "video", clips=clips) for name, clips in clips_by_track.items()]
    return Project("p", width=W, height=H, fps=fps, media_assets=_assets(media), tracks=tracks)


def _render(project: Project, tmp_path: Path, tag: str, *, origin: float = 0.0, fps: float = float(FPS)) -> tuple[str, str]:
    """Empreintes (images, son) du rendu de ``project`` : le graphe de l'export, sans encodeur."""
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, fps, None, origin=origin)
    threads = decoder_threads(inputs)
    command = [*_ffmpeg_command_prefix(), "-y", "-v", "error", "-nostdin"]
    for item in inputs:
        command += input_arguments(item, threads=threads)
    frames, sound = tmp_path / f"{tag}.v.md5", tmp_path / f"{tag}.a.md5"
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-c:v", "rawvideo", "-pix_fmt", "yuv420p", "-f", "framemd5", str(frames),
                "-map", f"[{audio}]", "-c:a", "pcm_f32le", "-f", "md5", str(sound)]
    _run(command)
    lines = [line.split(",")[-1].strip() for line in frames.read_text().splitlines() if line and not line.startswith("#")]
    return hashlib.md5("".join(lines).encode()).hexdigest() + f":{len(lines)}", sound.read_text().strip()


def _assert_parity(monkeypatch, project: Project, tmp_path: Path, **kwargs) -> int:
    """Rend ``project`` avec la nouvelle structure et avec le graphe d'avant ; renvoie le nombre d'entrées positionnées."""
    plan = build_render_plan(project)
    _graph, _v, _a, inputs = ExportEngine._build_filter_complex(plan, W, H, kwargs.get("fps", float(FPS)), None, origin=kwargs.get("origin", 0.0))
    new = _render(project, tmp_path, "new", **kwargs)
    with monkeypatch.context() as legacy:
        legacy.setattr(export_inputs, "max_pipelines", lambda *_size: 0)
        old = _render(project, tmp_path, "old", **kwargs)
    assert new[0] == old[0], "les images diffèrent du rendu d'avant"
    assert new[1] == old[1], "le son diffère du rendu d'avant"
    assert int(old[0].split(":")[1]) > 0
    return sum(hasattr(item, "seek_start") for item in inputs)


def test_a_cut_clip_renders_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    project = _project(media, {"V1": [Clip("c0", "a", "V1", 0.0, 0.0, 9.0), Clip("c1", "a", "V1", 9.0, 9.0, 24.0)]})
    assert _assert_parity(monkeypatch, project, tmp_path) == 2


def test_pieces_cut_between_frames_render_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    cuts = [0.0, 3.37, 7.04, 11.5, 17.333, 24.0]
    clips = [Clip(f"c{i}", "a", "V1", cuts[i], cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}), tmp_path) == 5


def test_reordered_pieces_with_a_gap_and_a_scaled_layer_above(media, monkeypatch, tmp_path):
    base = [Clip("c0", "a", "V1", 0.0, 12.0, 16.0), Clip("c1", "a", "V1", 5.0, 2.0, 6.0), Clip("c2", "a", "V1", 10.0, 18.0, 23.0)]
    top = [Clip("t0", "a", "V2", 3.0, 8.0, 11.0, transform=ClipTransform(scale=0.5, position_x=0.2))]
    assert _assert_parity(monkeypatch, _project(media, {"V1": base, "V2": top}), tmp_path) >= 3


def test_other_containers_and_a_variable_frame_rate(media, monkeypatch, tmp_path):
    """Matroska (audio en avance de 21 ms sur FFmpeg 6.1 / 7.1) et cadence variable, coupés puis repris plus loin."""
    clips = [
        Clip("c0", "vfr", "V1", 0.0, 0.0, 6.0), Clip("c1", "vfr", "V1", 6.0, 13.0, 19.0),
        Clip("c2", "mkv", "V1", 12.0, 3.5, 8.5), Clip("c3", "mkv", "V1", 17.0, 15.0, 21.0),
    ]
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}), tmp_path) == 4


def test_animated_clips_render_the_same_frames(media, monkeypatch, tmp_path):
    """Image clé de transformation (échelle, rotation) et paramètre d'effet animé par ``sendcmd`` : le temps local du clip est
    celui d'avant, que le clip lise l'entrée partagée ou la sienne."""
    blur = create_effect(EffectType.BLUR)
    clips = [Clip(f"c{i}", "a", "V1", i * 6.0, 4.0 + i * 6.0, 10.0 + i * 6.0) for i in range(3)]
    for clip in clips:
        clip.transform_keyframes = [
            TransformKeyframe("scale", 0.0, 0.6, InterpolationType.LINEAR), TransformKeyframe("scale", 3.0, 1.2, InterpolationType.EASE_IN_OUT),
            TransformKeyframe("rotation", 0.0, 0.0, InterpolationType.LINEAR), TransformKeyframe("rotation", 5.0, 25.0, InterpolationType.LINEAR),
        ]
        clip.effects = [blur]
        clip.animation = [
            Keyframe(fx_property_id(blur.id, "intensity"), 0.0, 1.0, InterpolationType.LINEAR),
            Keyframe(fx_property_id(blur.id, "intensity"), 4.0, 6.0, InterpolationType.LINEAR),
        ]
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}), tmp_path) == 3


def test_an_mpeg_transport_stream_keeps_the_shared_input(media, monkeypatch, tmp_path):
    """MPEG-TS déduit ses points d'accès du flux : positionné sur FFmpeg 6.1 et 7.1, le décodage reprend sur une image qui n'est pas
    de rafraîchissement (« co located POCs unavailable ») et les images diffèrent. Il garde donc l'entrée partagée, et le rendu
    est celui d'avant."""
    clips = [Clip("c0", "ts", "V1", 0.0, 0.0, 7.0), Clip("c1", "ts", "V1", 7.0, 12.4, 20.0), Clip("c2", "ts", "V1", 14.6, 3.2, 9.9)]
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}), tmp_path) == 0


def test_the_project_frame_rate_may_differ_from_the_media(media, monkeypatch, tmp_path):
    clips = [Clip("c0", "a", "V1", 0.0, 0.0, 8.0), Clip("c1", "a", "V1", 8.0, 8.0, 16.0)]
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}, fps=30.0), tmp_path, fps=30.0) == 2


def test_a_retimed_clip_keeps_the_shared_input_and_the_others_still_match(media, monkeypatch, tmp_path):
    clips = [Clip("c0", "a", "V1", 0.0, 0.0, 6.0), Clip("c1", "a", "V1", 6.0, 8.0, 16.0), Clip("c2", "a", "V1", 11.0, 16.0, 22.0)]
    clips[1].time_remapping = TimeRemapping(speed=2.0)
    assert _assert_parity(monkeypatch, _project(media, {"V1": clips}), tmp_path) == 2


def test_a_preview_segment_renders_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    project = _project(media, {"V1": [Clip("c0", "a", "V1", 0.0, 0.0, 9.0), Clip("c1", "a", "V1", 9.0, 9.0, 24.0)]})
    assert _assert_parity(monkeypatch, project, tmp_path, origin=5.0) == 2


def _multicam(media, total: float = 24.0, switches: int = 7) -> Project:
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=_assets(media), tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    names = ["a", "mkv", "vfr"]
    step = total / (switches + 1)
    ops = [
        {"op": "create_multicam_source", "as": "plateau", "name": "Plateau",
         "angles": [{"asset": name, "name": f"A{i}", "offset": 0.0} for i, name in enumerate(names)]},
        {"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "A0", "source_in": 0.0, "source_out": total},
        *({"op": "switch_multicam_angle", "at": step * k, "angle": f"A{k % 3}"} for k in range(1, switches + 1)),
    ]
    report = apply_script(Session(project), parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report.error
    return project


def test_a_multicam_montage_renders_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    """Trois angles (GOP long, Matroska, cadence variable), huit segments aux instants qui ne tombent pas sur une image."""
    positioned = _assert_parity(monkeypatch, _multicam(media), tmp_path)
    assert positioned >= 8                      # une entrée positionnée par segment


def test_a_multicam_preview_segment_renders_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    assert _assert_parity(monkeypatch, _multicam(media), tmp_path, origin=3.5) == 5


def test_a_generic_nested_sequence_read_twice_renders_the_same_frames_and_sound(media, monkeypatch, tmp_path):
    inner = Sequence("inner", "Inner", W, H, float(FPS), tracks=[Track("V1", "V1", "video", clips=[Clip("r", "a", "V1", 0.0, 0.0, 24.0)])])
    main = Sequence("main", "Master", W, H, float(FPS), tracks=[Track("V1", "V1", "video")])
    project = Project("p", media_assets=_assets(media), sequences=[main, inner], active_sequence_id="main")
    insert_sequence_clip(project, "inner", "V1", 0.0, source_in=0.0, source_out=6.0)
    insert_sequence_clip(project, "inner", "V1", 6.0, source_in=14.0, source_out=20.0)
    assert _assert_parity(monkeypatch, project, tmp_path) == 2


def test_an_animated_clip_inside_a_nested_sequence_keeps_its_local_time(media, monkeypatch, tmp_path):
    """Les expressions d'échelle, de rotation et d'opacité lisent le temps local du clip (``t``) : une instance qui ne part pas du
    début du clip garde le même temps que le rendu depuis 0 (temps absolu, pas remis à 0 sur la première image gardée)."""
    clip = Clip("r", "mkv", "V1", 0.0, 0.0, 24.0)
    clip.transform_keyframes = [
        TransformKeyframe("scale", 0.0, 0.5, InterpolationType.LINEAR), TransformKeyframe("scale", 20.0, 1.3, InterpolationType.EASE_IN_OUT),
        TransformKeyframe("rotation", 0.0, 0.0, InterpolationType.LINEAR), TransformKeyframe("rotation", 24.0, 40.0, InterpolationType.LINEAR),
        TransformKeyframe("opacity", 0.0, 1.0, InterpolationType.LINEAR), TransformKeyframe("opacity", 24.0, 0.4, InterpolationType.LINEAR),
    ]
    inner = Sequence("inner", "Inner", W, H, float(FPS), tracks=[Track("V1", "V1", "video", clips=[clip])])
    main = Sequence("main", "Master", W, H, float(FPS), tracks=[Track("V1", "V1", "video")])
    project = Project("p", media_assets=_assets(media), sequences=[main, inner], active_sequence_id="main")
    insert_sequence_clip(project, "inner", "V1", 0.0, source_in=2.0, source_out=7.0)
    insert_sequence_clip(project, "inner", "V1", 5.0, source_in=11.3, source_out=16.9)
    assert _assert_parity(monkeypatch, project, tmp_path) == 2


def test_the_mkv_audio_offset_is_the_reason_for_the_probed_first_frame(media):
    """La première image d'un conteneur dont l'audio commence avant la vidéo : sondée avec le ``ffprobe`` de l'installation, c'est
    l'horodatage que voit ``trim`` (décalé de ``-start_time``), pas le point d'entrée."""
    shown = export_inputs.first_frame_time(media["mkv"], 0.0)
    assert shown is not None and 0.0 <= shown < 0.1
    again = export_inputs.first_frame_time(media["mkv"], 0.0)
    assert again == shown
