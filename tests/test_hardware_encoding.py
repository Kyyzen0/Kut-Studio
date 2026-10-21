"""Détection, cache, choix de l'encodeur et qualité : FFmpeg simulé, aucun GPU requis."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.hardware_cache import (
    CapabilityService,
    hardware_encoding_disabled,
    installation_fingerprint,
)
from core.hardware_encoding import (
    HardwareCapabilities,
    HardwareEncoder,
    RunOutput,
    auto_order,
    detect_capabilities,
    parse_encoder_list,
    parse_version,
    redact_command,
    validation_command,
)
from core.video_encoders import (
    EncoderUnavailableError,
    QualityIntent,
    cpu_choice,
    crf_for_intent,
    encoder_options,
    intent_for_crf,
    resolve_video_encoder,
)

# --- Sorties FFmpeg simulées ---------------------------------------------------------------------

HEADER = """Encoders:
 V..... = Video
 A..... = Audio
 ------
"""
BASE = (
    " V....D libx264              libx264 H.264 / AVC (codec h264)\n"
    " V....D libx265              libx265 H.265 / HEVC (codec hevc)\n"
    " VFS... prores_ks            Apple ProRes (iCodec Pro) (codec prores)\n"
    " A....D aac                  AAC (Advanced Audio Coding)\n"
    " S..... srt                  SubRip subtitle\n"
)
HARDWARE_LINES = {
    "videotoolbox": (
        " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n"
        " V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)\n"
    ),
    "nvenc": (
        " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
        " V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)\n"
    ),
    "qsv": (
        " V....D h264_qsv             H.264 / AVC (Intel Quick Sync Video) (codec h264)\n"
        " V....D hevc_qsv             HEVC (Intel Quick Sync Video) (codec hevc)\n"
    ),
    "amf": (
        " V....D h264_amf             AMD AMF H.264 Encoder (codec h264)\n"
        " V....D hevc_amf             AMD AMF HEVC encoder (codec hevc)\n"
    ),
    "vaapi": (
        " V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)\n"
        " V....D hevc_vaapi           H.265/HEVC (VAAPI) (codec hevc)\n"
    ),
}


def encoders_output(*families: str) -> str:
    return HEADER + BASE + "".join(HARDWARE_LINES[f] for f in families)


class FakeFFmpeg:
    """Runner : ``-encoders`` / ``-version`` simulés, mini-encodages réussis ou non."""

    def __init__(self, *families: str, failing: tuple[str, ...] = (), present: bool = True):
        self.listing = encoders_output(*families)
        self.failing = failing
        self.calls: list[list[str]] = []
        self.present = present

    def __call__(self, command, timeout):
        command = list(command)
        self.calls.append(command)
        if not self.present:
            return RunOutput(-1, "", "No such file")
        if "-encoders" in command:
            return RunOutput(0, self.listing)
        if "-version" in command:
            return RunOutput(0, "ffmpeg version 7.1.1 Copyright (c) 2000-2025\nbuilt with clang")
        encoder = command[command.index("-c:v") + 1]
        if any(name in encoder for name in self.failing):
            return RunOutput(1, "", "Cannot load libcuda.so.1\nError initializing the encoder")
        return RunOutput(0)

    @property
    def validations(self) -> list[str]:
        return [c[c.index("-c:v") + 1] for c in self.calls if "-c:v" in c]


def detect(*families, failing=(), platform_name="darwin", **kwargs) -> HardwareCapabilities:
    runner = FakeFFmpeg(*families, failing=failing)
    return detect_capabilities(
        ["ffmpeg"], runner=runner, platform_name=platform_name, machine="arm64", **kwargs
    )


# --- Parsing ------------------------------------------------------------------------------------------------------


def test_parse_encoder_list_keeps_video_encoders_only():
    names = parse_encoder_list(encoders_output("videotoolbox", "nvenc"))
    assert {"libx264", "libx265", "prores_ks", "h264_videotoolbox", "hevc_nvenc"} <= names
    assert "aac" not in names and "srt" not in names          # audio et sous-titres ignorés
    assert "Encoders:" not in names and "------" not in names  # en-tête ignoré
    assert parse_encoder_list("") == set()
    assert parse_encoder_list("garbage\n\n  \n") == set()


def test_parse_version():
    assert parse_version("ffmpeg version 7.1.1 Copyright") == "7.1.1"
    assert parse_version("ffmpeg version N-118000-gabcdef built with gcc") == "N-118000-gabcdef"
    assert parse_version("nothing here") == ""


# --- Détection par backend ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "backend", "platform_name"),
    [
        ("videotoolbox", HardwareEncoder.VIDEOTOOLBOX, "darwin"),
        ("nvenc", HardwareEncoder.NVENC, "win32"),
        ("qsv", HardwareEncoder.QSV, "win32"),
        ("amf", HardwareEncoder.AMF, "win32"),
    ],
)
def test_each_listed_and_validated_backend_is_detected(family, backend, platform_name):
    caps = detect(family, platform_name=platform_name)
    assert caps.is_usable("h264", backend) and caps.is_usable("hevc", backend)
    assert caps.usable_backends("h264") == (backend,)
    assert caps.auto_backend("h264") is backend
    assert caps.software == ("libx264", "libx265")
    assert caps.ffmpeg_version == "7.1.1"


def test_vaapi_is_detected_when_a_render_node_exists(monkeypatch):
    monkeypatch.setattr("core.hardware_encoding.vaapi_device", lambda environment=None: "/dev/dri/renderD128")
    caps = detect("vaapi", platform_name="linux")
    assert caps.is_usable("h264", HardwareEncoder.VAAPI)


def test_vaapi_is_rejected_without_a_render_node(monkeypatch):
    monkeypatch.setattr("core.hardware_encoding.vaapi_device", lambda environment=None: None)
    runner = FakeFFmpeg("vaapi")
    caps = detect_capabilities(["ffmpeg"], runner=runner, platform_name="linux", machine="x86_64")
    assert not caps.is_usable("h264", HardwareEncoder.VAAPI)
    assert "renderD" in caps.capability("h264", HardwareEncoder.VAAPI).detail
    assert runner.validations == []                              # aucun processus inutile


def test_videotoolbox_absent_means_not_available():
    caps = detect("nvenc", platform_name="darwin")
    assert not caps.is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)
    assert caps.capability("h264", HardwareEncoder.VIDEOTOOLBOX) is None
    assert "n'est pas fourni" in caps.unavailable_reason("h264", HardwareEncoder.VIDEOTOOLBOX)


def test_ffmpeg_without_any_hardware_acceleration():
    caps = detect()
    assert caps.encoders == () and caps.ffmpeg_available
    assert caps.software == ("libx264", "libx265")
    assert caps.auto_backend("h264") is HardwareEncoder.CPU
    assert "aucun" in caps.describe()


def test_missing_ffmpeg_gives_an_explicit_state_without_exception():
    caps = detect_capabilities(None, platform_name="darwin")
    assert caps.error == "ffmpeg_missing" and not caps.ffmpeg_available
    assert caps.auto_backend("h264") is HardwareEncoder.CPU
    broken = detect_capabilities(["ffmpeg"], runner=FakeFFmpeg(present=False))
    assert broken.error == "ffmpeg_failed" and broken.encoders == ()


def test_a_listed_encoder_that_cannot_initialise_is_never_offered():
    caps = detect("nvenc", "qsv", failing=("nvenc",), platform_name="win32")
    assert not caps.is_usable("h264", HardwareEncoder.NVENC)
    item = caps.capability("h264", HardwareEncoder.NVENC)
    assert item.listed and item.validated is False and "Error initializing" in item.detail
    assert caps.usable_backends("h264") == (HardwareEncoder.QSV,)
    assert "ne s'initialise pas" in caps.unavailable_reason("h264", HardwareEncoder.NVENC)
    assert [o[0] for o in encoder_options("h264", caps)] == [
        HardwareEncoder.AUTO, HardwareEncoder.CPU, HardwareEncoder.QSV,
    ]


def test_validation_is_a_tiny_synthetic_null_encode_with_the_real_quality_options():
    runner = FakeFFmpeg("videotoolbox")
    detect_capabilities(["ffmpeg"], runner=runner, platform_name="darwin")
    command = next(c for c in runner.calls if "-c:v" in c and "h264_videotoolbox" in c)
    assert command[command.index("-f", command.index("-c:v")) + 1] == "null"
    assert "lavfi" in command and "-frames:v" in command
    assert "-b:v" in command                                    # mêmes options que le rendu réel


def test_validation_can_be_skipped():
    runner = FakeFFmpeg("videotoolbox")
    caps = detect_capabilities(["ffmpeg"], runner=runner, validate=False, platform_name="darwin")
    assert runner.validations == []
    assert caps.capability("h264", HardwareEncoder.VIDEOTOOLBOX).validated is None
    assert caps.is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)   # non invalidé = utilisable


def test_auto_order_depends_on_the_platform_only_to_rank_validated_encoders():
    assert auto_order("darwin") == (HardwareEncoder.VIDEOTOOLBOX,)
    assert auto_order("win32")[0] is HardwareEncoder.NVENC
    caps = detect("nvenc", "qsv", "amf", platform_name="win32")
    assert caps.usable_backends("h264") == (
        HardwareEncoder.NVENC, HardwareEncoder.QSV, HardwareEncoder.AMF,
    )


# --- Sérialisation et diagnostics -----------------------------------------------------------------------------


def test_capabilities_roundtrip_and_reject_foreign_schemas():
    caps = detect("videotoolbox", failing=("hevc",))
    again = HardwareCapabilities.from_dict(json.loads(json.dumps(caps.to_dict())))
    assert again == caps
    assert HardwareCapabilities.from_dict({"schema": 99}) is None
    assert HardwareCapabilities.from_dict("nope") is None
    broken = caps.to_dict()
    broken["encoders"].append({"backend": "cpu", "codec": "h264", "encoder": "libx264"})  # ignoré
    broken["encoders"].append("junk")
    assert len(HardwareCapabilities.from_dict(broken).encoders) == len(caps.encoders)


def test_diagnostics_are_short_and_never_expose_the_home_directory(monkeypatch):
    home = os.path.expanduser("~")
    caps = detect_capabilities(
        [home + "/bin/ffmpeg"], runner=FakeFFmpeg("videotoolbox"), platform_name="darwin"
    )
    text = caps.describe()
    assert home not in text and "~/bin/ffmpeg" in text
    assert "7.1.1" in text and "h264_videotoolbox" in text and "Auto pour H.264" in text
    assert len(text.splitlines()) < 15                          # pas de sortie brute de FFmpeg


def test_command_logging_reduces_paths_to_file_names():
    command = ["/Users/someone/bin/ffmpeg", "-i", "/Users/someone/Movies/secret.mp4", "-c:v", "libx264",
               "-filter_complex", "x" * 500, "/Users/someone/out.mp4"]
    logged = redact_command(command)
    assert "/Users/someone" not in logged
    assert "secret.mp4" in logged and "libx264" in logged and len(logged) < 200


# --- Cache des capacités ---------------------------------------------------------------------------------------------


def make_service(tmp_path, runner, binary=None, **kwargs):
    binary = binary or tmp_path / "ffmpeg"
    if not binary.exists():
        binary.write_bytes(b"\x7fELF-v1")
    return CapabilityService(
        command_provider=lambda: [str(binary)],
        cache_path=tmp_path / "caps.json",
        runner=runner,
        environment={},
        **kwargs,
    ), binary


def test_capabilities_are_detected_once_then_read_from_memory_and_disk(tmp_path):
    runner = FakeFFmpeg("videotoolbox")
    service, binary = make_service(tmp_path, runner)
    first = service.capabilities()
    calls = len(runner.calls)
    assert service.capabilities() is first and len(runner.calls) == calls and service.scan_count == 1
    # Nouveau processus : le cache disque évite toute nouvelle détection.
    again, _ = make_service(tmp_path, FakeFFmpeg("nvenc"), binary=binary)
    assert again.capabilities().is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)
    assert again.scan_count == 0


def test_cache_is_invalidated_when_the_ffmpeg_binary_changes(tmp_path):
    service, binary = make_service(tmp_path, FakeFFmpeg("videotoolbox"))
    service.capabilities()
    binary.write_bytes(b"\x7fELF-v2-longer")                    # mise à jour de FFmpeg
    other, _ = make_service(tmp_path, FakeFFmpeg("nvenc"), binary=binary)
    caps = other.capabilities()
    assert other.scan_count == 1 and caps.is_usable("h264", HardwareEncoder.NVENC)
    assert not caps.is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)


def test_cache_is_invalidated_when_the_ffmpeg_path_changes(tmp_path):
    service, _ = make_service(tmp_path, FakeFFmpeg("videotoolbox"))
    service.capabilities()
    elsewhere = tmp_path / "other" / "ffmpeg"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"\x7fELF-v1")
    other, _ = make_service(tmp_path, FakeFFmpeg(), binary=elsewhere)
    assert other.capabilities().encoders == () and other.scan_count == 1


def test_cache_is_invalidated_when_the_environment_changes(tmp_path):
    service, binary = make_service(tmp_path, FakeFFmpeg("videotoolbox"))
    service.capabilities()
    assert installation_fingerprint([str(binary)], {}) != installation_fingerprint(
        [str(binary)], {"KUT_STUDIO_HARDWARE_ENCODING": "off"}
    )
    assert hardware_encoding_disabled({"KUT_STUDIO_HARDWARE_ENCODING": "OFF"})
    assert not hardware_encoding_disabled({})


def test_user_rescan_and_invalidate_force_a_new_detection(tmp_path):
    runner = FakeFFmpeg("videotoolbox")
    service, _ = make_service(tmp_path, runner)
    service.capabilities()
    runner.listing = encoders_output()                           # le pilote a changé entre-temps
    assert service.capabilities().is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)  # cache conservé
    assert not service.rescan().is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)
    assert service.scan_count == 2
    service.invalidate()
    assert service.cached() is None and not service.cache_path.exists()
    service.capabilities()
    assert service.scan_count == 3


def test_stale_cache_is_ignored(tmp_path):
    clock = {"t": 1_000_000.0}
    service, binary = make_service(tmp_path, FakeFFmpeg("videotoolbox"), clock=lambda: clock["t"])
    service.capabilities()
    clock["t"] += 31 * 24 * 3600
    later, _ = make_service(tmp_path, FakeFFmpeg(), binary=binary, clock=lambda: clock["t"])
    assert later.capabilities().encoders == () and later.scan_count == 1


def test_corrupt_or_unwritable_cache_never_breaks_detection(tmp_path):
    service, _ = make_service(tmp_path, FakeFFmpeg("videotoolbox"))
    service.cache_path.write_text("{ not json", encoding="utf-8")
    assert service.capabilities().is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)
    blocked, _ = make_service(tmp_path, FakeFFmpeg("nvenc"))
    blocked._cache_path = tmp_path / "caps.json" / "impossible" / "cache.json"  # parent = fichier
    assert blocked.capabilities().is_usable("h264", HardwareEncoder.NVENC)


def test_missing_ffmpeg_is_reported_and_not_cached_on_disk(tmp_path):
    service = CapabilityService(command_provider=lambda: None, cache_path=tmp_path / "c.json", environment={})
    assert service.capabilities().error == "ffmpeg_missing"
    assert service.cached().error == "ffmpeg_missing"           # l'interface n'attend pas indéfiniment
    assert not (tmp_path / "c.json").exists()


def test_disabled_switch_skips_every_process(tmp_path):
    runner = FakeFFmpeg("videotoolbox")
    service = CapabilityService(
        command_provider=lambda: ["ffmpeg"], cache_path=tmp_path / "c.json", runner=runner,
        environment={"KUT_STUDIO_HARDWARE_ENCODING": "off"},
    )
    caps = service.cached()
    assert caps.error == "disabled" and runner.calls == []
    assert caps.auto_backend("h264") is HardwareEncoder.CPU
    assert "désactivé" in caps.unavailable_reason("h264", HardwareEncoder.VIDEOTOOLBOX)


# --- Choix de l'encodeur ----------------------------------------------------------------------------------------------------


def test_cpu_selection_is_unchanged_whatever_the_machine():
    caps = detect("videotoolbox", "nvenc")
    choice = resolve_video_encoder("h264", speed_preset="slow", quality=17, hardware="cpu", capabilities=caps)
    assert choice.args == ("-c:v", "libx264", "-preset", "slow", "-crf", "17")
    assert choice.used is HardwareEncoder.CPU and choice.fallback_reason is None
    assert choice.label == "H.264 · CPU"
    prores = resolve_video_encoder("prores_ks", quality=3, hardware="cpu", capabilities=caps)
    assert prores.args == ("-c:v", "prores_ks", "-profile:v", "3")


def test_auto_selects_the_validated_hardware_encoder():
    caps = detect("videotoolbox")
    choice = resolve_video_encoder("h264", quality=20, hardware="auto", capabilities=caps,
                                   width=1920, height=1080, fps=30)
    assert choice.used is HardwareEncoder.VIDEOTOOLBOX and choice.requested is HardwareEncoder.AUTO
    assert choice.encoder == "h264_videotoolbox" and choice.label == "H.264 · VideoToolbox"
    assert choice.args[:2] == ("-c:v", "h264_videotoolbox") and "libx264" not in choice.args
    assert choice.is_hardware and choice.fallback_reason is None


def test_auto_falls_back_to_cpu_without_noise_when_nothing_is_validated():
    for caps in (detect(), detect("nvenc", failing=("nvenc",), platform_name="win32"),
                 detect_capabilities(None)):
        choice = resolve_video_encoder("h264", quality=20, hardware="auto", capabilities=caps)
        assert choice.used is HardwareEncoder.CPU and choice.fallback_reason is None
        assert choice.args[:2] == ("-c:v", "libx264")


def test_auto_ignores_an_encoder_merely_listed_by_ffmpeg():
    caps = detect("nvenc", "videotoolbox", failing=("nvenc", "videotoolbox"))
    assert resolve_video_encoder("h264", hardware="auto", capabilities=caps).used is HardwareEncoder.CPU


def test_auto_keeps_prores_on_the_cpu():
    caps = detect("videotoolbox")
    choice = resolve_video_encoder("prores_ks", quality=3, hardware="auto", capabilities=caps)
    assert choice.used is HardwareEncoder.CPU and choice.encoder == "prores_ks"


def test_explicit_backend_is_used_when_available():
    caps = detect("nvenc", "qsv", platform_name="win32")
    choice = resolve_video_encoder("h264", quality=20, hardware="qsv", capabilities=caps)
    assert choice.used is HardwareEncoder.QSV and choice.video_filter == "format=nv12"


def test_explicit_backend_unavailable_raises_instead_of_silently_using_the_cpu():
    caps = detect("nvenc", platform_name="win32")
    with pytest.raises(EncoderUnavailableError) as raised:
        resolve_video_encoder("h264", hardware="videotoolbox", capabilities=caps)
    assert raised.value.backend is HardwareEncoder.VIDEOTOOLBOX and "VideoToolbox" in str(raised.value)
    failing = detect("nvenc", failing=("nvenc",), platform_name="win32")
    with pytest.raises(EncoderUnavailableError, match="ne s'initialise pas"):
        resolve_video_encoder("h264", hardware="nvenc", capabilities=failing)


def test_codec_incompatible_with_a_backend_is_refused_explicitly():
    caps = detect("videotoolbox")
    with pytest.raises(EncoderUnavailableError, match="ne prend pas en charge"):
        resolve_video_encoder("prores_ks", quality=3, hardware="videotoolbox", capabilities=caps)


def test_hevc_is_supported_by_the_same_tables():
    caps = detect("videotoolbox", "nvenc", platform_name="darwin")
    vt = resolve_video_encoder("hevc", quality=20, hardware="videotoolbox", capabilities=caps)
    assert vt.encoder == "hevc_videotoolbox" and ("-tag:v", "hvc1") == vt.args[-2:]
    assert resolve_video_encoder("hevc", quality=20, hardware="cpu", capabilities=caps).encoder == "libx265"
    assert resolve_video_encoder("hevc", hardware="auto", capabilities=caps).used is HardwareEncoder.VIDEOTOOLBOX


def test_unknown_codec_is_rejected_for_every_mode():
    caps = detect("videotoolbox")
    for mode in ("cpu", "auto"):
        with pytest.raises(ValueError):
            resolve_video_encoder("vp9", hardware=mode, capabilities=caps)


def test_encoder_options_only_offer_what_exists():
    none = HardwareCapabilities()
    assert [o[0] for o in encoder_options("h264", none)] == [HardwareEncoder.AUTO, HardwareEncoder.CPU]
    caps = detect("videotoolbox")
    options = dict(encoder_options("h264", caps))
    assert options[HardwareEncoder.CPU] == "CPU – libx264"
    assert options[HardwareEncoder.VIDEOTOOLBOX] == "Apple VideoToolbox – H.264"
    assert HardwareEncoder.NVENC not in options
    assert [o[0] for o in encoder_options("prores_ks", caps)] == [HardwareEncoder.CPU]


def test_cpu_choice_helper_carries_the_fallback_reason():
    choice = cpu_choice("h264", quality=20, requested=HardwareEncoder.AUTO, fallback_reason="raison")
    assert choice.used is HardwareEncoder.CPU and choice.requested is HardwareEncoder.AUTO
    assert choice.fallback_reason == "raison" and choice.args[:2] == ("-c:v", "libx264")


# --- Intention de qualité ---------------------------------------------------------------------------------------------------------


def test_quality_intent_is_derived_from_the_stored_crf():
    assert [intent_for_crf(c) for c in (12, 15, 16, 18, 19, 20, 23, 24, 25, 28, 35)] == [
        QualityIntent.MASTER, QualityIntent.MASTER, QualityIntent.HIGH, QualityIntent.HIGH,
        QualityIntent.HIGH, QualityIntent.MEDIUM, QualityIntent.MEDIUM, QualityIntent.MEDIUM,
        QualityIntent.LOW, QualityIntent.LOW, QualityIntent.LOW,
    ]
    for intent in QualityIntent:
        assert intent_for_crf(crf_for_intent(intent)) is intent   # aller-retour stable


def _args(backend, crf, **kwargs):
    caps = detect("videotoolbox", "nvenc", "qsv", "amf", platform_name="darwin") if backend != "vaapi" else None
    if backend == "vaapi":
        caps = detect("vaapi", platform_name="linux")
    return resolve_video_encoder("h264", quality=crf, hardware=backend, capabilities=caps, **kwargs)


def test_each_backend_translates_the_intent_with_its_own_parameters(monkeypatch):
    monkeypatch.setattr("core.hardware_encoding.vaapi_device", lambda environment=None: "/dev/dri/renderD128")
    high, low = 18, 28
    nvenc_high, nvenc_low = _args("nvenc", high).args, _args("nvenc", low).args
    assert nvenc_high[nvenc_high.index("-cq") + 1] == "23" and nvenc_low[nvenc_low.index("-cq") + 1] == "33"
    qsv = _args("qsv", high).args
    assert qsv[qsv.index("-global_quality") + 1] == "21"
    amf = _args("amf", high).args
    assert amf[amf.index("-qp_i") + 1] == "22" and "cqp" in amf
    vaapi = _args("vaapi", high)
    assert vaapi.args[vaapi.args.index("-qp") + 1] == "22"
    assert vaapi.video_filter == "format=nv12,hwupload" and vaapi.pre_input_args == ("-vaapi_device", "/dev/dri/renderD128")


def test_videotoolbox_targets_a_bitrate_that_grows_with_quality_and_resolution():
    def kbps(crf, w=1920, h=1080, fps=30):
        args = _args("videotoolbox", crf, width=w, height=h, fps=fps).args
        return int(args[args.index("-b:v") + 1].rstrip("k"))

    assert kbps(14) > kbps(18) > kbps(23) > kbps(28)
    assert kbps(18, 3840, 2160) > kbps(18) > kbps(18, 1280, 720)
    assert kbps(18, fps=60) > kbps(18, fps=30)
    assert "-allow_sw" in _args("videotoolbox", 18).args           # pas de repli logiciel déguisé


def test_hardware_encoders_ignore_the_x264_speed_preset():
    choice = resolve_video_encoder("h264", speed_preset="veryslow", quality=18, hardware="videotoolbox",
                                   capabilities=detect("videotoolbox"))
    assert "veryslow" not in choice.args


def test_validation_command_matches_the_encoder_and_is_bounded():
    caps = detect("nvenc", platform_name="win32")
    item = caps.capability("h264", HardwareEncoder.NVENC)
    command = validation_command(["ffmpeg"], item)
    assert command[0] == "ffmpeg" and "h264_nvenc" in command and command[-3:] == ["-f", "null", "-"]
    assert "3" == command[command.index("-frames:v") + 1]
    assert Path(command[0]).name == "ffmpeg"


# --- Retours de revue : verrou de détection, classification des échecs ---------------------------------------


def test_cached_never_blocks_behind_a_running_detection(tmp_path):
    import threading
    import time

    gate, started = threading.Event(), threading.Event()
    inner = FakeFFmpeg("videotoolbox")

    def slow(command, timeout):
        started.set()
        gate.wait(10)
        return inner(command, timeout)

    service, _ = make_service(tmp_path, slow)
    results = []
    worker = threading.Thread(target=lambda: results.append(service.capabilities()))
    worker.start()
    assert started.wait(5)
    begin = time.monotonic()
    assert service.cached() is None                               # l'interface ne reste pas bloquée
    assert time.monotonic() - begin < 1.0
    waiter = threading.Thread(target=lambda: results.append(service.capabilities()))
    waiter.start()                                                # un export attend le résultat…
    gate.set()
    worker.join(10)
    waiter.join(10)
    assert len(results) == 2 and results[0] == results[1]
    assert service.scan_count == 1                                # …sans relancer FFmpeg


def test_unrelated_ffmpeg_errors_are_not_encoder_failures():
    from core.hardware_encoding import looks_like_encoder_failure

    assert looks_like_encoder_failure("Error initializing the encoder h264_nvenc", "h264_nvenc")
    assert looks_like_encoder_failure("Cannot load libcuda.so.1")
    assert looks_like_encoder_failure("hwupload: failed", "")
    assert not looks_like_encoder_failure("Error opening input file media.mp4: Invalid data", "h264_nvenc")
    assert not looks_like_encoder_failure("Invalid filtergraph: unknown filter", "h264_nvenc")
    assert not looks_like_encoder_failure("", "h264_nvenc")
    assert looks_like_encoder_failure("Unrecognized option 'rc'.", "h264_nvenc", ("-c:v", "h264_nvenc", "-rc", "vbr"))
    assert not looks_like_encoder_failure("Unrecognized option 'foo'.", "h264_nvenc", ("-rc", "vbr"))
