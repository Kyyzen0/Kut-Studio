"""Choix du décodeur, repli CPU, mesures et intégration aux commandes FFmpeg."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from core.decode_policy import (
    FAILURES_BEFORE_BLOCK,
    QT_DEVICE_VARIABLE,
    DecodeContext,
    DecodeHealth,
    DecodeMeasurement,
    DecodeProfile,
    DecodePurpose,
    SourceOption,
    StreamInfo,
    StreamProbe,
    choose_decoder,
    choose_preview_source,
    configure_qt_decoding,
    default_context,
    looks_like_decode_failure,
    measure_decode,
    parse_stream_json,
    qt_hw_device_types,
    run_with_decode_fallback,
    set_default_context,
)
from core.hardware_decoding import CODEC_BY_ID, DecodeMode, DecoderCapability
from core.hardware_encoding import HardwareCapabilities

HEVC10_4K = StreamInfo("hevc", "yuv420p10le", 3840, 2160)
H264_HD = StreamInfo("h264", "yuv420p", 1280, 720)
H264_444 = StreamInfo("h264", "yuv444p", 1920, 1080)


def caps(*pairs, platform="darwin", **kwargs) -> HardwareCapabilities:
    decoders = tuple(DecoderCapability(codec, backend, True, True) for backend, codec in pairs)
    return HardwareCapabilities(ffmpeg_path="ffmpeg", platform=platform, decoders=decoders, **kwargs)


VT = caps(*[(DecodeMode.VIDEOTOOLBOX, c) for c in ("h264", "hevc", "hevc10")])


@pytest.fixture(autouse=True)
def _cpu_context():
    set_default_context(None)
    yield
    set_default_context(None)


# --- Règles -------------------------------------------------------------------------------------------


def test_export_and_thumbnails_always_decode_on_the_cpu():
    for purpose in (DecodePurpose.EXPORT, DecodePurpose.THUMBNAIL):
        choice = choose_decoder("videotoolbox", capabilities=VT, stream=HEVC10_4K, purpose=purpose)
        assert choice.used is DecodeMode.CPU and choice.input_args == ()


def test_cpu_mode_and_unknown_capabilities_stay_on_the_cpu():
    assert choose_decoder("cpu", capabilities=VT, stream=HEVC10_4K).reason == "requested_cpu"
    assert choose_decoder("auto", capabilities=None, stream=HEVC10_4K).reason == "capabilities_unknown"
    disabled = HardwareCapabilities(decoding_disabled=True)
    assert choose_decoder("auto", capabilities=disabled, stream=HEVC10_4K).used is DecodeMode.CPU


def test_auto_uses_only_validated_backends_and_supported_profiles():
    choice = choose_decoder("auto", capabilities=VT, stream=HEVC10_4K)
    assert choice.used is DecodeMode.VIDEOTOOLBOX and choice.input_args == ("-hwaccel", "videotoolbox")
    assert choose_decoder("auto", capabilities=VT, stream=H264_444).reason == "profile_unsupported"
    vp9 = StreamInfo("vp9", "yuv420p", 3840, 2160)
    assert choose_decoder("auto", capabilities=VT, stream=vp9).reason == "no_validated_backend"


def test_auto_prior_keeps_light_h264_on_the_cpu_until_measured():
    assert choose_decoder("auto", capabilities=VT, stream=H264_HD).reason == "cpu_prior"
    assert choose_decoder("auto", capabilities=VT, stream=HEVC10_4K).reason == "hardware_prior"


def test_measurements_override_the_prior(tmp_path):
    profile = DecodeProfile(tmp_path / "p.json")
    # Matériel lent et sous le seuil de confort : le CPU l'emporte.
    profile.put(DecodeMode.CPU, "hevc10", "uhd", DecodeMeasurement(200.0))
    profile.put(DecodeMode.VIDEOTOOLBOX, "hevc10", "uhd", DecodeMeasurement(40.0))
    assert choose_decoder("auto", capabilities=VT, stream=HEVC10_4K, profile=profile).reason == "cpu_measured_faster"
    # Matériel moins rapide mais assez (≥ 60 i/s) : il est gardé, il libère le CPU.
    profile.put(DecodeMode.VIDEOTOOLBOX, "hevc10", "uhd", DecodeMeasurement(88.0))
    assert choose_decoder("auto", capabilities=VT, stream=HEVC10_4K, profile=profile).reason == "hardware_measured"
    # Mesure du H.264 léger : le matériel suffit, la mesure remplace le choix provisoire.
    profile.put(DecodeMode.CPU, "h264", "hd", DecodeMeasurement(700.0))
    profile.put(DecodeMode.VIDEOTOOLBOX, "h264", "hd", DecodeMeasurement(210.0))
    assert choose_decoder("auto", capabilities=VT, stream=H264_HD, profile=profile).used is DecodeMode.VIDEOTOOLBOX


def test_explicit_unavailable_backend_falls_back_with_a_visible_reason():
    choice = choose_decoder("cuda", capabilities=VT, stream=HEVC10_4K)
    assert choice.used is DecodeMode.CPU and choice.requested is DecodeMode.CUDA
    assert choice.fallback_reason and "CUDA" in choice.fallback_reason


def test_repeated_runtime_failures_block_a_backend_for_the_session():
    health = DecodeHealth()
    choice = choose_decoder("auto", capabilities=VT, stream=HEVC10_4K, health=health)
    for _ in range(FAILURES_BEFORE_BLOCK):
        health.record_failure(choice, DecodePurpose.SEGMENT, "line\nhwaccel init failed")
    assert health.blocked(DecodeMode.VIDEOTOOLBOX, "hevc10")
    assert health.fallbacks == FAILURES_BEFORE_BLOCK
    assert health.events()[-1].detail == "hwaccel init failed"
    assert choose_decoder("auto", capabilities=VT, stream=HEVC10_4K, health=health).used is DecodeMode.CPU
    explicit = choose_decoder("videotoolbox", capabilities=VT, stream=HEVC10_4K, health=health)
    assert explicit.used is DecodeMode.CPU and "session" in explicit.fallback_reason
    health.reset()
    assert not health.blocked(DecodeMode.VIDEOTOOLBOX, "hevc10")


def test_failure_logs_are_not_spammed(caplog):
    health = DecodeHealth()
    choice = choose_decoder("auto", capabilities=VT, stream=HEVC10_4K)
    with caplog.at_level("WARNING", logger="kut_studio.decode"):
        for _ in range(10):
            health.record_failure(choice, DecodePurpose.SEGMENT, "boom")
    # Premier échec + bannissement : deux avertissements, pas dix.
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2


def test_decode_failure_markers():
    assert looks_like_decode_failure("Device creation failed: -12.")
    assert looks_like_decode_failure("Failed setup for format videotoolbox_vld")
    assert not looks_like_decode_failure("No such file or directory")


# --- Qt Multimedia -----------------------------------------------------------------------------------


def test_qt_device_types_follow_the_mode():
    assert qt_hw_device_types("cpu", VT) == "none"
    assert qt_hw_device_types("auto", None) is None  # pas encore détecté : Qt choisit et se replie seul
    assert qt_hw_device_types("auto", VT) == "videotoolbox"
    assert qt_hw_device_types("auto", caps()) == "none"
    win = caps((DecodeMode.CUDA, "h264"), (DecodeMode.D3D11VA, "h264"), platform="win32")
    assert qt_hw_device_types("auto", win) == "cuda,d3d11va"
    assert qt_hw_device_types("d3d11va", win) == "d3d11va"
    assert qt_hw_device_types("vaapi", win) == "none"


def test_configure_qt_decoding_respects_a_user_override():
    env = {QT_DEVICE_VARIABLE: "cuda"}
    assert configure_qt_decoding("cpu", VT, env) == ("cuda", "user")
    env = {}
    assert configure_qt_decoding("cpu", VT, env) == ("none", "kut")
    assert env[QT_DEVICE_VARIABLE] == "none"
    assert configure_qt_decoding("auto", VT, env) == ("videotoolbox", "kut")  # notre valeur : remplaçable
    assert configure_qt_decoding("auto", None, env) == (None, "qt-default")
    assert QT_DEVICE_VARIABLE not in env


# --- Repli à l'exécution -----------------------------------------------------------------------------------


class _Context(DecodeContext):
    def __init__(self, choice_stream=HEVC10_4K, **kwargs):
        super().__init__(mode=DecodeMode.AUTO, capabilities_provider=lambda: VT, **kwargs)
        self.probe = StreamProbe(probe=lambda path: choice_stream)


def test_hardware_failure_is_rerun_on_the_cpu(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    context = _Context()
    commands = []

    def build(args_for):
        return ["ffmpeg", *args_for(str(media)), "-i", str(media), *args_for("graph.ffconcat"), "out"]

    def run(command):
        commands.append(command)
        return (1, "hwaccel initialisation returned error") if "-hwaccel" in command else (0, "")

    code, _stderr, fell_back = run_with_decode_fallback(
        build, run, paths=[str(media)], purpose=DecodePurpose.SEGMENT, context=context)
    assert code == 0 and fell_back
    assert "-hwaccel" in commands[0] and "-hwaccel" not in commands[1]
    assert context.health.fallbacks == 1
    # Une entrée qui n'est pas un média (flux de calques) ne reçoit jamais d'option.
    assert commands[0].count("-hwaccel") == 1


def test_a_cpu_failure_is_not_retried(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    context = DecodeContext()  # CPU par défaut
    calls = []
    code, _stderr, fell_back = run_with_decode_fallback(
        lambda args_for: ["ffmpeg", *args_for(str(media))], lambda c: (calls.append(c), (1, "bad"))[1],
        paths=[str(media)], purpose=DecodePurpose.SEGMENT, context=context)
    assert code == 1 and not fell_back and len(calls) == 1


def test_default_context_is_cpu_without_an_interface(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    choice = default_context().choice_for(str(media), DecodePurpose.SEGMENT)
    assert choice.used is DecodeMode.CPU and choice.reason == "requested_cpu"


# --- Mesures ------------------------------------------------------------------------------------------


def test_profile_persists_and_is_tied_to_the_installation(tmp_path):
    path = tmp_path / "decode-profile.json"
    profile = DecodeProfile(path, fingerprint="abc")
    profile.put(DecodeMode.CPU, "h264", "fhd", DecodeMeasurement(500.0, 0.004, 90, 1.0))
    assert DecodeProfile(path, fingerprint="abc").get(DecodeMode.CPU, "h264", "fhd").fps == 500.0
    assert DecodeProfile(path, fingerprint="other").get(DecodeMode.CPU, "h264", "fhd") is None
    path.write_text("{not json", encoding="utf-8")
    assert DecodeProfile(path, fingerprint="abc").items() == {}


def test_measure_decode_uses_the_real_frames_and_reports_cpu_time():
    seen = []

    def runner(command):
        seen.append(command)
        return 0, 0.5, 0.25

    result = measure_decode(["ffmpeg"], "a.mp4", DecodeMode.VIDEOTOOLBOX, CODEC_BY_ID["h264"], frames=50,
                            runner=runner, clock=lambda: 7.0)
    assert result.fps == pytest.approx(100.0) and result.cpu_seconds_per_frame == pytest.approx(0.005)
    assert "-hwaccel" in seen[0] and seen[0][seen[0].index("-frames:v") + 1] == "50"
    assert measure_decode(["ffmpeg"], "a.mp4", DecodeMode.CPU, CODEC_BY_ID["h264"],
                          runner=lambda c: (1, 0.1, None)) is None


def test_stream_probe_parses_and_memoizes(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    calls = []
    probe = StreamProbe(probe=lambda path: (calls.append(path), H264_HD)[1])
    assert probe.peek(str(media)) is None
    assert probe.get(str(media)) == H264_HD and probe.get(str(media)) == H264_HD
    assert len(calls) == 1
    text = json.dumps({"streams": [{"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le",
                                    "width": 3840, "height": 2160}]})
    assert parse_stream_json(text) == HEVC10_4K
    assert parse_stream_json("garbage") is None
    assert HEVC10_4K.resolution_class == "uhd" and H264_HD.resolution_class == "hd"


# --- Original ou proxy ------------------------------------------------------------------------------------


def test_a_sharp_enough_proxy_is_used_when_it_is_cheaper():
    original = SourceOption("orig.mov", HEVC10_4K)
    proxy = SourceOption("proxy.mp4", StreamInfo("h264", "yuv420p", 1280, 720), is_proxy=True)
    decision = choose_preview_source(original, proxy, needed_height=540, mode="auto", capabilities=VT)
    assert decision.is_proxy and decision.reason == "proxy_cheaper"


def test_the_original_wins_when_the_proxy_is_not_cheaper(tmp_path):
    """« Ne suppose pas qu'un proxy est toujours plus rapide » : mesures à l'appui."""
    profile = DecodeProfile(tmp_path / "p.json")
    profile.put(DecodeMode.CPU, "h264", "fhd", DecodeMeasurement(700.0))
    profile.put(DecodeMode.VIDEOTOOLBOX, "h264", "fhd", DecodeMeasurement(900.0))  # original HD très rapide
    profile.put(DecodeMode.CPU, "prores", "hd", DecodeMeasurement(150.0))            # proxy ProRes lent ici
    original = SourceOption("orig.mp4", StreamInfo("h264", "yuv420p", 1920, 1080))
    proxy = SourceOption("proxy.mov", StreamInfo("prores", "yuv422p10le", 1280, 720), is_proxy=True)
    caps_vt = caps((DecodeMode.VIDEOTOOLBOX, "h264"))
    decision = choose_preview_source(original, proxy, needed_height=540, mode="auto", capabilities=caps_vt,
                                     profile=profile)
    assert not decision.is_proxy and decision.decoder.used is DecodeMode.VIDEOTOOLBOX


def test_a_too_small_proxy_needs_to_be_much_cheaper():
    original = SourceOption("orig.mp4", StreamInfo("h264", "yuv420p", 1920, 1080))
    proxy = SourceOption("proxy.mp4", StreamInfo("h264", "yuv420p", 854, 480), is_proxy=True)
    full = choose_preview_source(original, proxy, needed_height=1080, mode="cpu", capabilities=VT)
    assert full.is_proxy and full.reason == "proxy_much_cheaper"  # 4,5× moins de pixels
    close = SourceOption("proxy.mp4", StreamInfo("h264", "yuv420p", 1600, 900), is_proxy=True)
    assert not choose_preview_source(original, close, needed_height=1080, mode="cpu", capabilities=VT).is_proxy
    assert not choose_preview_source(original, None, needed_height=1080, mode="cpu", capabilities=VT).is_proxy


# --- Intégration aux commandes ------------------------------------------------------------------------------


def test_preview_segments_put_hwaccel_before_media_inputs_only(tmp_path, monkeypatch):
    from core.preview_engine import PreviewEngine, PreviewJob, _plan_media_paths
    from core.render_plan import RenderLayer, RenderPlan

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    layer = RenderLayer("c", "a", "V1", 0, str(media), 0.0, 1.0, 0.0, 1.0)
    plan = RenderPlan(video_layers=(layer,), audio_layers=(), subtitle_cues=(), transitions=(),
                      duration=1.0, width=64, height=36, fps=10)
    assert _plan_media_paths(plan) == {str(media)}
    set_default_context(_Context())
    commands = []

    def fake_run(command, token, *, timeout):
        commands.append(list(command))
        if "-hwaccel" in command:
            return 1, "Failed setup for format videotoolbox_vld"
        open(command[-1], "wb").write(b"segment")
        return 0, ""

    monkeypatch.setattr("core.preview_engine._run_cancellable", fake_run)
    engine = PreviewEngine(cache=None)
    output = engine._default_render(PreviewJob(key="k", plan=plan, width=64, height=36, fps=10), None)
    assert output and os.path.exists(output)
    first = commands[0]
    assert first[first.index("-hwaccel") + 2] == "-i" and first[first.index("-hwaccel") + 3] == str(media)
    assert "-hwaccel" not in commands[1]  # relancé en CPU
    assert default_context().health.fallbacks == 1
    os.remove(output)


def test_proxy_generation_retries_on_the_cpu(tmp_path):
    from core.proxy_manager import ProxyManager, RunResult

    source = tmp_path / "src.mp4"
    source.write_bytes(b"source")
    set_default_context(_Context())
    commands = []

    def runner(command, cancel, on_progress, on_start=None):
        commands.append(command)
        if "-hwaccel" in command:
            return RunResult(1, "Hardware device setup failed for decoder", False)
        open(command[-1], "wb").write(b"proxy")
        return RunResult(0, "", False)

    manager = ProxyManager(tmp_path / "proxies", runner=runner, ffmpeg_command=lambda: ["ffmpeg"])
    manager.request(str(source), duration=1.0)
    assert manager._wait_idle(5.0)
    assert manager.info(str(source)).valid
    assert "-hwaccel" in commands[0] and "-hwaccel" not in commands[1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
def test_tracking_frames_retry_on_the_cpu_when_hardware_produces_nothing(tmp_path):
    from core.tracking_frames import AnalysisGeometry, FrameReader

    media = tmp_path / "gray.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10",
                    "-t", "1", "-pix_fmt", "yuv420p", str(media)], check=True)
    context = _Context(choice_stream=StreamInfo("h264", "yuv420p", 3840, 2160))
    context.capabilities_provider = lambda: caps((DecodeMode.CUDA, "h264"), platform="linux")  # absent ici
    set_default_context(context)
    pytest.importorskip("numpy")
    reader = FrameReader(str(media), AnalysisGeometry(64, 36, 64, 36), 10.0)
    assert reader.decode_choice.is_hardware
    frames = list(reader.frames(0, 3))
    if not frames:
        pytest.skip("ce FFmpeg n'a pas échoué sans CUDA")
    assert len(frames) == 4 and not reader.decode_choice.is_hardware


def test_a_cancelled_segment_is_not_a_decoder_failure(tmp_path, monkeypatch):
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.render_plan import RenderLayer, RenderPlan

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    layer = RenderLayer("c", "a", "V1", 0, str(media), 0.0, 1.0, 0.0, 1.0)
    plan = RenderPlan(video_layers=(layer,), duration=1.0, width=64, height=36, fps=10)
    set_default_context(_Context())

    class Token:
        cancelled = False

    token = Token()
    calls = []

    def fake_run(command, token_, *, timeout):
        calls.append(command)
        token_.cancelled = True  # la tête de lecture est partie pendant le rendu
        return 1, "rendu interrompu"

    monkeypatch.setattr("core.preview_engine._run_cancellable", fake_run)
    assert PreviewEngine(cache=None)._default_render(PreviewJob(key="k", plan=plan), token) is None
    assert len(calls) == 1 and default_context().health.fallbacks == 0
