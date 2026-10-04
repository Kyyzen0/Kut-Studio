"""Le niveau audio exporté est celui de la source : le mixage **additionne**, il ne moyenne pas.

Régression mesurée le 2026-10-04 : ``amix`` divise chaque entrée par leur nombre. Avec la base silencieuse, un clip seul
sortait exactement 6 dB sous sa source (WAV à −13,2 LUFS exporté à −19,2 LUFS) ; et comme ``dropout_transition=0``
renormalise dès qu'une entrée se termine, trois clips bout à bout sortaient à −11,7, −9,3 et −5,8 dB, chacun à un niveau
différent. Les tests rendent de vrais fichiers et relisent **les échantillons** (flottants : ni la conversion en 16 bits de
``volumedetect`` ni l'encodeur ne masquent un dépassement de 0 dBFS).

Les tests de graphe et de sonde n'exécutent pas FFmpeg : ils tiennent aussi sur une machine sans binaire.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core import export_engine
from core.export_engine import SAFETY_LIMITER, ExportEngine, _ffmpeg_command_prefix, _ffmpeg_filter_has_option
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

W, H, FPS = 160, 90, 25
SECONDS = 6.0
TOLERANCE_DB = 0.5


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]


def _ffmpeg(*arguments: str) -> list[str]:
    return [*_ffmpeg_command_prefix(), "-v", "error", "-y", *arguments]


# ---------------------------------------------------------------------------
# Rendus réels
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Sinus stéréo d'amplitude connue (WAV 16 bits, comme la source de la mesure) et une vidéo muette."""
    folder = tmp_path_factory.mktemp("levels")
    paths: dict[str, Path] = {}
    # (nom, fréquence, amplitude) : 0,5 = −6 dBFS ; 0,35 laisse la somme de deux couches sous 1 ; 0,9 la fait dépasser 0 dBFS.
    for name, hertz, amplitude in (("half", 440, 0.5), ("half_b", 660, 0.5), ("quiet_a", 440, 0.35), ("quiet_b", 660, 0.35),
                                   ("loud", 440, 0.9)):
        path = folder / f"{name}.wav"
        wave = f"{amplitude}*sin(2*PI*{hertz}*t)"
        _run(_ffmpeg("-f", "lavfi", "-i", f"aevalsrc={wave}|{wave}:sample_rate=48000:duration={SECONDS}",
                     "-c:a", "pcm_s16le", str(path)))
        paths[name] = path
    video = folder / "video.mp4"
    _run(_ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d={SECONDS}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 str(video)))
    paths["video"] = video
    return paths


def _project(media, *audio_tracks: list[Clip]) -> Project:
    """Une piste vidéo (muette) et une piste audio par liste de clips : les clips qui se chevauchent vont sur des pistes distinctes."""
    assets = [MediaAsset("video", str(media["video"]), "video", SECONDS, W, H, float(FPS), "video", False)]
    for name in ("half", "half_b", "quiet_a", "quiet_b", "loud"):
        assets.append(MediaAsset(name, str(media[name]), name, SECONDS, 0, 0, 0.0, "audio", True))
    tracks = [Track("V1", "V1", "video", clips=[Clip("v", "video", "V1", 0.0, 0.0, SECONDS)])]
    for index, clips in enumerate(audio_tracks, start=1):
        tracks.append(Track(f"A{index}", f"A{index}", "audio", clips=clips))
    return Project("levels", width=W, height=H, fps=float(FPS), media_assets=assets, tracks=tracks)


def _clip(asset: str, track: int, start: float = 0.0, length: float = SECONDS) -> Clip:
    return Clip(f"{asset}-{track}-{start}", asset, f"A{track}", start, 0.0, length)


def _export(plan, path: Path, codec: str = "aac") -> Path:
    """Rend le graphe de l'export réel ; ``codec='pcm_f32le'`` (en ``.mkv``) garde les échantillons flottants intacts."""
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    command = _ffmpeg()
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", codec, str(path)]
    _run(command)
    return path


def _samples(path: Path, start: float = 0.0, length: float | None = None) -> np.ndarray:
    window = ["-ss", f"{start}"] + (["-t", f"{length}"] if length is not None else [])
    command = [*_ffmpeg_command_prefix(), "-v", "error", *window, "-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "-"]
    raw = subprocess.run(command, capture_output=True, timeout=60).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _level_db(path: Path, start: float, length: float) -> float:
    """Niveau efficace (dBFS) de la fenêtre ``[start, start + length]`` : la source et l'export se mesurent pareil."""
    data = _samples(path, start, length).astype(np.float64)
    assert data.size, f"aucun échantillon lu dans {path}"
    return float(20 * np.log10(np.sqrt(np.mean(data ** 2))))


def _peak(path: Path) -> float:
    return float(np.abs(_samples(path)).max())


@requires_ffmpeg
def test_a_single_audio_clip_is_exported_at_its_source_level(media, tmp_path):
    """Le cas mesuré : avant le correctif, −6,0 dB exactement."""
    out = _export(build_render_plan(_project(media, [_clip("half", 1)])), tmp_path / "one.mp4")
    assert _level_db(out, 1.0, 4.0) == pytest.approx(_level_db(media["half"], 1.0, 4.0), abs=TOLERANCE_DB)


@requires_ffmpeg
def test_every_clip_of_a_sequence_keeps_its_own_source_level(media, tmp_path):
    """Trois clips bout à bout : ``amix`` renormalisait à chaque fin d'entrée, chacun sortait à un niveau différent."""
    clips = [_clip("half", 1, start, 2.0) for start in (0.0, 2.0, 4.0)]
    out = _export(build_render_plan(_project(media, clips)), tmp_path / "three.mp4")
    expected = _level_db(media["half"], 0.4, 1.2)
    heard = [_level_db(out, start + 0.4, 1.2) for start in (0.0, 2.0, 4.0)]
    assert heard == pytest.approx([expected] * 3, abs=TOLERANCE_DB), heard


@requires_ffmpeg
def test_overlapping_clips_add_up_instead_of_being_averaged(media, tmp_path):
    """Deux sons sans rapport entre eux s'additionnent en puissance : +3,01 dB sur chacun seul."""
    plan = build_render_plan(_project(media, [_clip("quiet_a", 1)], [_clip("quiet_b", 2)]))
    out = _export(plan, tmp_path / "two.mp4")
    alone = _level_db(media["quiet_a"], 1.0, 4.0)
    assert _level_db(out, 1.0, 4.0) == pytest.approx(alone + 3.01, abs=TOLERANCE_DB)


@requires_ffmpeg
def test_master_gain_still_applies_to_the_whole_mix(media, tmp_path):
    out = _export(build_render_plan(_project(media, [_clip("half", 1)]), master_gain_db=-6.0), tmp_path / "master.mp4")
    assert _level_db(out, 1.0, 4.0) == pytest.approx(_level_db(media["half"], 1.0, 4.0) - 6.0, abs=TOLERANCE_DB)


@requires_ffmpeg
def test_a_sum_beyond_full_scale_is_limited_to_0_dbfs(media, tmp_path):
    """Deux couches à 0,9 s'additionnent à 1,8 (+5,1 dBFS) : sans limiteur le mixage final dépasse la pleine échelle."""
    plan = build_render_plan(_project(media, [_clip("loud", 1)], [_clip("loud", 2)]))
    out = _export(plan, tmp_path / "loud.mkv", codec="pcm_f32le")
    assert _peak(out) <= 1.0 + 1e-3
    assert _level_db(out, 1.0, 4.0) > -6.0, "le limiteur doit plafonner, pas écraser le mixage"


@requires_ffmpeg
def test_a_peak_below_full_scale_passes_the_limiter_untouched(media, tmp_path):
    out = _export(build_render_plan(_project(media, [_clip("loud", 1)])), tmp_path / "loud_one.mkv", codec="pcm_f32le")
    assert _peak(out) == pytest.approx(0.9, abs=0.01)


@requires_ffmpeg
def test_the_limiter_does_not_delay_the_sound(media, tmp_path):
    """Sans ``latency=1``, ``alimiter`` retarde de 239 échantillons (~5 ms) : tout le son glisserait derrière l'image."""
    out = _export(build_render_plan(_project(media, [_clip("half", 1, 2.0, 3.0)])), tmp_path / "late.mkv", codec="pcm_f32le")
    data = np.abs(_samples(out)).max(axis=1)
    onset = int(np.argmax(data > 0.05)) / 48000
    assert onset == pytest.approx(2.0, abs=0.001)


@requires_ffmpeg
def test_the_fallback_for_an_ffmpeg_without_normalize_gives_the_same_levels(media, tmp_path, monkeypatch):
    """FFmpeg < 4.4 : ``apad`` + ``volume=N`` doit rattraper le diviseur *aussi* quand une couche en suit une autre.

    Un FFmpeg récent se comporte comme un ancien dès qu'on n'écrit pas ``normalize=0`` : on force donc le repli sur le
    FFmpeg de la machine, et on relit les mêmes niveaux que ci-dessus.
    """
    real = export_engine._ffmpeg_filter_has_option
    monkeypatch.setattr(export_engine, "_ffmpeg_filter_has_option",
                        lambda name, option: False if (name, option) == ("amix", "normalize") else real(name, option))
    one = _export(build_render_plan(_project(media, [_clip("half", 1)])), tmp_path / "f1.mp4")
    assert _level_db(one, 1.0, 4.0) == pytest.approx(_level_db(media["half"], 1.0, 4.0), abs=TOLERANCE_DB)

    clips = [_clip("half", 1, start, 2.0) for start in (0.0, 2.0, 4.0)]
    three = _export(build_render_plan(_project(media, clips)), tmp_path / "f3.mp4")
    expected = _level_db(media["half"], 0.4, 1.2)
    assert [_level_db(three, s + 0.4, 1.2) for s in (0.0, 2.0, 4.0)] == pytest.approx([expected] * 3, abs=TOLERANCE_DB)

    two = _export(build_render_plan(_project(media, [_clip("quiet_a", 1)], [_clip("quiet_b", 2)])), tmp_path / "f2.mp4")
    assert _level_db(two, 1.0, 4.0) == pytest.approx(_level_db(media["quiet_a"], 1.0, 4.0) + 3.01, abs=TOLERANCE_DB)


# ---------------------------------------------------------------------------
# Forme du graphe (sans FFmpeg)
# ---------------------------------------------------------------------------


def _assets(*names: str) -> list[MediaAsset]:
    return [MediaAsset(name, f"/nowhere/{name}.wav", name, 10.0, 0, 0, 0.0, "audio", True) for name in names]


def _graph(project: Project, **plan_options) -> str:
    plan = build_render_plan(project, **plan_options)
    return ExportEngine._build_filter_complex(plan, W, H, FPS, None)[0]


def _support(monkeypatch, *, normalize: bool = True, latency: bool = True) -> None:
    known = {("amix", "normalize"): normalize, ("alimiter", "latency"): latency}
    monkeypatch.setattr(export_engine, "_ffmpeg_filter_has_option", lambda name, option: known.get((name, option), False))


def _two_layer_project() -> Project:
    return Project("p", width=W, height=H, fps=float(FPS), media_assets=_assets("m", "n"), tracks=[
        Track("A1", "A1", "audio", clips=[Clip("c1", "m", "A1", 0.0, 0.0, 4.0)]),
        Track("A2", "A2", "audio", clips=[Clip("c2", "n", "A2", 1.0, 0.0, 4.0)]),
    ])


def test_the_final_mix_sums_then_applies_master_then_limits(monkeypatch):
    _support(monkeypatch)
    graph = _graph(_two_layer_project(), master_gain_db=-3.0)
    assert (
        "[silent_base][a0][a1]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
        f"volume=-3.000dB,{SAFETY_LIMITER},aformat=channel_layouts=stereo:sample_rates=48000[aout]"
    ) in graph


def test_the_safety_limiter_asks_for_no_latency_and_no_auto_level():
    assert "latency=1" in SAFETY_LIMITER, "sans compensation le limiteur retarde le son de 5 ms"
    assert "level=0" in SAFETY_LIMITER, "l'auto-level remonterait un mixage faible"
    assert "limit=1" in SAFETY_LIMITER


def test_the_ducking_voice_submix_also_sums(monkeypatch):
    """Le détecteur du ducking entend la voix à son niveau réel, pas divisée par le nombre de clips de la piste."""
    from core.audio_automation import AudioAutomationService, DuckingConfig, TrackRole

    _support(monkeypatch)
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=_assets("m", "v"), tracks=[
        Track("M1", "M1", "audio", clips=[Clip("music", "m", "M1", 0.0, 0.0, 8.0)]),
        Track("V1", "V1", "audio", clips=[Clip("v1", "v", "V1", 0.0, 0.0, 2.0), Clip("v2", "v", "V1", 3.0, 0.0, 2.0)]),
    ])
    service = AudioAutomationService()
    service.set_track_role(project, "M1", TrackRole.MUSIC)
    service.set_track_role(project, "V1", TrackRole.VOICE)
    service.add_ducking_sidechain(project, "M1", "V1", DuckingConfig())
    graph = _graph(project)
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aformat=channel_layouts=stereo:sample_rates=48000[av0]" in graph
    assert "sidechaincompress" in graph


def test_without_normalize_the_layers_are_padded_and_the_mix_is_compensated(monkeypatch):
    _support(monkeypatch, normalize=False, latency=False)
    graph = _graph(_two_layer_project())
    assert "normalize" not in graph
    assert "[a0]apad[a0_pad]" in graph and "[a1]apad[a1_pad]" in graph
    assert "[silent_base][a0_pad][a1_pad]amix=inputs=3:duration=first:dropout_transition=0,volume=3," in graph


def test_without_alimiter_latency_no_limiter_is_emitted(monkeypatch):
    """Mieux vaut un mixage sans plafond qu'un son décalé de 5 ms derrière l'image."""
    _support(monkeypatch, normalize=True, latency=False)
    graph = _graph(_two_layer_project())
    assert "alimiter" not in graph
    assert "normalize=0" in graph


def test_a_project_without_audio_has_no_mix_to_limit(monkeypatch):
    _support(monkeypatch)
    graph = _graph(Project("p", width=W, height=H, fps=float(FPS), media_assets=[], tracks=[]))
    assert "amix" not in graph and "alimiter" not in graph


def test_a_nested_sequence_sums_but_only_the_timeline_limits(monkeypatch):
    """Une séquence imbriquée additionne aussi ; seul le mixage final de la timeline porte le limiteur."""
    _support(monkeypatch)
    assets = _assets("m", "n")
    inner = Sequence("inner", "Inner", W, H, float(FPS), tracks=[
        Track("A1", "A1", "audio", clips=[Clip("i1", "m", "A1", 0.0, 0.0, 3.0)]),
        Track("A2", "A2", "audio", clips=[Clip("i2", "n", "A2", 0.0, 0.0, 3.0)]),
    ])
    main = Sequence("main", "Main", W, H, float(FPS), tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    project = Project("p", media_assets=assets, sequences=[main, inner], active_sequence_id="main")
    insert_sequence_clip(project, "inner", "A1", 0.0)
    graph = _graph(project)
    assert graph.count("normalize=0") == 2
    assert graph.count("alimiter") == 1
    assert graph.rindex("alimiter") > graph.rindex("amix")


# ---------------------------------------------------------------------------
# Sonde des options de filtre (sans FFmpeg)
# ---------------------------------------------------------------------------

AMIX_HELP = """Filter amix
  Audio mixing.
amix AVOptions:
   inputs            <int>        ..F.A...... Number of inputs. (from 1 to 32767) (default 2)
   duration          <int>        ..F.A...... How to determine the end-of-stream. (from 0 to 2) (default longest)
   normalize         <boolean>    ..F.A....T. Scale inputs (default true)
"""
AMIX_HELP_BEFORE_4_4 = AMIX_HELP.replace("   normalize         <boolean>    ..F.A....T. Scale inputs (default true)\n", "")


@pytest.fixture
def probe(monkeypatch):
    """Un « ffmpeg » factice dont chaque lancement est compté, et un cache de sonde vierge avant comme après."""
    monkeypatch.setattr(export_engine, "_ffmpeg_path", ("fake-ffmpeg",))
    cache = _ffmpeg_filter_has_option.__dict__
    cache.pop("_cache", None)

    class Fake:
        calls: list[list[str]] = []
        answer: subprocess.CompletedProcess | Exception = subprocess.CompletedProcess([], 0, AMIX_HELP, "")

        @classmethod
        def run(cls, command, **_options):
            cls.calls.append(list(command))
            if isinstance(cls.answer, Exception):
                raise cls.answer
            return cls.answer

    Fake.calls = []
    monkeypatch.setattr(export_engine.process_supervisor, "supervised_run", Fake.run)
    yield Fake
    cache.pop("_cache", None)


def test_the_probe_reads_the_option_from_the_filter_help(probe):
    assert _ffmpeg_filter_has_option("amix", "normalize") is True
    assert probe.calls == [["fake-ffmpeg", "-hide_banner", "-h", "filter=amix"]]


def test_the_probe_says_no_for_an_ffmpeg_that_lacks_the_option(probe):
    probe.answer = subprocess.CompletedProcess([], 0, AMIX_HELP_BEFORE_4_4, "")
    assert _ffmpeg_filter_has_option("amix", "normalize") is False


def test_the_probe_does_not_match_an_option_name_inside_a_description(probe):
    probe.answer = subprocess.CompletedProcess([], 0, "   inputs  <int>  ..F.A...... Do not normalize <boolean> anything\n", "")
    assert _ffmpeg_filter_has_option("amix", "normalize") is False


def test_the_probe_asks_ffmpeg_only_once_per_option(probe):
    for _ in range(3):
        assert _ffmpeg_filter_has_option("amix", "normalize") is True
    assert len(probe.calls) == 1


@pytest.mark.parametrize("failure", [
    OSError("introuvable"),
    subprocess.TimeoutExpired("fake-ffmpeg", 10),
    subprocess.CompletedProcess([], 1, "", "boom"),
])
def test_an_ffmpeg_that_cannot_answer_reads_as_no_and_is_asked_again_later(probe, failure):
    probe.answer = failure
    assert _ffmpeg_filter_has_option("amix", "normalize") is False
    probe.answer = subprocess.CompletedProcess([], 0, AMIX_HELP, "")
    assert _ffmpeg_filter_has_option("amix", "normalize") is True, "un échec passager ne doit pas être mémorisé"


def test_without_any_ffmpeg_the_probe_reads_as_no(monkeypatch):
    monkeypatch.setattr(export_engine, "_ffmpeg_path", None)
    monkeypatch.setattr(export_engine, "find_media_tool", lambda name: None)
    _ffmpeg_filter_has_option.__dict__.pop("_cache", None)
    assert _ffmpeg_filter_has_option("amix", "normalize") is False
