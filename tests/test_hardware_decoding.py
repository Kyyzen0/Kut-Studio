"""Détection des décodeurs matériels (FFmpeg simulé : aucun GPU requis).

Couvre macOS (VideoToolbox), Windows (CUDA, D3D11VA, QSV, DXVA2) et Linux
(VAAPI, CUDA) par des sorties ``-hwaccels`` et des décodages stricts simulés.
"""

from __future__ import annotations

import json
from pathlib import PureWindowsPath

import pytest

from core.hardware_cache import CapabilityService, hardware_decoding_disabled
from core.hardware_decoding import (
    CODEC_BY_ID,
    DECODE_CODECS,
    DecodeMode,
    DecoderCapability,
    codec_class,
    coerce_decode_mode,
    decode_input_args,
    parse_hwaccel_list,
    strict_validation_command,
)
from core.hardware_encoding import (
    SCHEMA_VERSION,
    HardwareCapabilities,
    RunOutput,
    detect_capabilities,
)

ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D prores_ks            Apple ProRes (iCodec Pro) (codec prores)
 V....D libvpx-vp9           libvpx VP9 (codec vp9)
 V....D libsvtav1            SVT-AV1 (codec av1)
"""


class FakeDecodeFFmpeg:
    """``-encoders``, ``-hwaccels``, fabrication d'échantillons et décodages stricts simulés.

    ``working`` : couples ``(hwaccel, codec)`` dont le décodage strict réussit.
    """

    def __init__(self, hwaccels=("videotoolbox",), working=None, encoders=ENCODERS, broken_hwaccels=False):
        self.hwaccels = hwaccels
        self.working = set(working or ())
        self.encoders = encoders
        self.broken_hwaccels = broken_hwaccels
        self.calls: list[list[str]] = []

    def __call__(self, command, timeout):
        command = list(command)
        self.calls.append(command)
        if "-encoders" in command:
            return RunOutput(0, self.encoders)
        if "-version" in command:
            return RunOutput(0, "ffmpeg version 7.1.1 Copyright")
        if "-hwaccels" in command:
            if self.broken_hwaccels:
                return RunOutput(1, "", "boom")
            return RunOutput(0, "Hardware acceleration methods:\n" + "\n".join(self.hwaccels) + "\n\n")
        if "lavfi" in command:  # échantillon
            return RunOutput(0)
        if "-hwaccel" in command:
            hwaccel = command[command.index("-hwaccel") + 1]
            sample = command[command.index("-i") + 1]
            # PureWindowsPath comprend « \ » et « / » : le chemin est fabriqué par l'OS qui exécute le test.
            codec = PureWindowsPath(sample).name.split(".")[0]
            if (hwaccel, codec) in self.working:
                return RunOutput(0)
            return RunOutput(234, "", "Nothing was written into output file")
        return RunOutput(0)


def detect(runner, platform_name="darwin", **kwargs) -> HardwareCapabilities:
    return detect_capabilities(["ffmpeg"], runner=runner, platform_name=platform_name, machine="x", **kwargs)


# --- Modèle -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("sample", [
    "/tmp/kut-hw/h264.mp4",
    r"C:\Users\runneradmin\AppData\Local\Temp\kut-hw\h264.mp4",
])
def test_the_fake_decoder_reads_the_codec_from_posix_and_windows_paths(sample):
    """Régression CI Windows : le faux FFmpeg ne comprenait que « / » et refusait tout décodage."""
    runner = FakeDecodeFFmpeg(working={("cuda", "h264")})
    assert runner(["ffmpeg", "-hwaccel", "cuda", "-i", sample, "-f", "null", "-"], 5).returncode == 0
    assert runner(["ffmpeg", "-hwaccel", "dxva2", "-i", sample, "-f", "null", "-"], 5).returncode != 0


def test_parse_hwaccel_list_ignores_the_header():
    text = "Hardware acceleration methods:\nvideotoolbox\ncuda\n\n"
    assert parse_hwaccel_list(text) == {"videotoolbox", "cuda"}
    assert parse_hwaccel_list("") == set()


@pytest.mark.parametrize("codec_name, pix_fmt, expected", [
    ("h264", "yuv420p", "h264"),
    ("h264", "yuvj420p", "h264"),
    ("h264", "yuv444p", None),          # 4:4:4 : FFmpeg retomberait en logiciel sans le dire
    ("h264", "yuv420p10le", None),      # H.264 10 bits : rarement décodé par le matériel
    ("hevc", "yuv420p", "hevc"),
    ("hevc", "yuv420p12le", None),      # 12 bits : hors de ce que le matériel est validé pour lire
    ("h264", "gray", None),
    ("hevc", "", "hevc"),               # sonde sans pix_fmt : profil 8 bits 4:2:0 par défaut (choix documenté)
    ("hevc", "yuv420p10le", "hevc10"),
    ("hevc", "yuv422p10le", None),
    ("prores", "yuv422p10le", "prores"),
    ("prores", "yuva444p10le", None),
    ("vp9", "yuv420p", "vp9"),
    ("av1", "yuv420p", "av1"),
    ("mpeg2video", "yuv420p", None),
    ("h264", "", "h264"),               # profil inconnu : famille 8 bits 4:2:0
    ("hevc", "", "hevc"),
])
def test_codec_class_takes_depth_and_chroma_into_account(codec_name, pix_fmt, expected):
    found = codec_class(codec_name, pix_fmt)
    assert (found.id if found else None) == expected


def test_decode_mode_coercion_defaults_to_auto():
    assert coerce_decode_mode("CUDA") is DecodeMode.CUDA
    assert coerce_decode_mode("nonsense") is DecodeMode.AUTO
    assert coerce_decode_mode(None) is DecodeMode.AUTO


def test_input_arguments_per_backend(monkeypatch):
    assert decode_input_args(DecodeMode.VIDEOTOOLBOX) == ("-hwaccel", "videotoolbox")
    assert decode_input_args(DecodeMode.D3D11VA) == ("-hwaccel", "d3d11va")
    assert decode_input_args(DecodeMode.CPU) == ()
    # QSV passe par ses propres décodeurs.
    assert decode_input_args(DecodeMode.QSV, CODEC_BY_ID["hevc10"]) == ("-hwaccel", "qsv", "-c:v", "hevc_qsv")
    monkeypatch.setenv("KUT_STUDIO_VAAPI_DEVICE", __file__)  # un fichier qui existe
    assert decode_input_args(DecodeMode.VAAPI) == ("-hwaccel", "vaapi", "-hwaccel_device", __file__)


def test_strict_validation_downloads_hardware_frames():
    command = strict_validation_command(["ffmpeg"], DecodeMode.VIDEOTOOLBOX, CODEC_BY_ID["hevc10"], "s.mp4")
    assert command[command.index("-hwaccel_output_format") + 1] == "videotoolbox_vld"
    assert "hwdownload,format=p010le" in command  # une image logicielle ne passe pas hwdownload


# --- Détection ---------------------------------------------------------------------------------------


def test_videotoolbox_is_validated_codec_by_codec():
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "h264"), ("videotoolbox", "hevc10")})
    caps = detect(runner)
    assert caps.hwaccels == ("videotoolbox",)
    assert caps.is_decode_usable("h264", DecodeMode.VIDEOTOOLBOX)
    assert caps.is_decode_usable("hevc10", DecodeMode.VIDEOTOOLBOX)
    assert not caps.is_decode_usable("hevc", DecodeMode.VIDEOTOOLBOX)
    assert caps.usable_decode_backends("h264") == (DecodeMode.VIDEOTOOLBOX,)
    assert caps.decode_backends() == (DecodeMode.VIDEOTOOLBOX,)
    assert "ne décode pas HEVC" in caps.decode_unavailable_reason("hevc", DecodeMode.VIDEOTOOLBOX)


def test_no_gpu_means_no_decoder_and_no_validation_run():
    runner = FakeDecodeFFmpeg(hwaccels=())
    caps = detect(runner)
    assert caps.decoders == () and caps.decode_backends() == ()
    assert not any("-hwaccel" in call for call in runner.calls)


def test_listed_but_broken_backend_is_never_usable():
    runner = FakeDecodeFFmpeg(hwaccels=("cuda",), working=set())
    caps = detect(runner, platform_name="linux")
    assert all(item.validated is False for item in caps.decoders)
    assert caps.decode_backends() == ()
    assert "Nothing was written" in caps.decoder("h264", DecodeMode.CUDA).detail


def test_windows_simulation_orders_validated_backends():
    working = {(h, c) for h in ("cuda", "d3d11va", "dxva2") for c in ("h264", "hevc")}
    runner = FakeDecodeFFmpeg(hwaccels=("cuda", "dxva2", "d3d11va", "qsv"), working=working)
    caps = detect(runner, platform_name="win32")
    assert caps.usable_decode_backends("h264") == (DecodeMode.CUDA, DecodeMode.D3D11VA, DecodeMode.DXVA2)
    assert caps.usable_decode_backends("hevc10") == ()  # rien de validé en 10 bits


def test_linux_vaapi_without_render_node_is_reported(monkeypatch):
    monkeypatch.setattr("core.hardware_encoding.vaapi_device", lambda environment=None: None)
    runner = FakeDecodeFFmpeg(hwaccels=("vaapi",), working={("vaapi", "h264")})
    caps = detect(runner, platform_name="linux")
    item = caps.decoder("h264", DecodeMode.VAAPI)
    assert item.validated is False and "VAAPI" in item.detail


def test_codecs_without_a_sample_encoder_stay_unverified():
    encoders = ENCODERS.replace(" V....D libsvtav1            SVT-AV1 (codec av1)\n", "")
    runner = FakeDecodeFFmpeg(working={("videotoolbox", c.id) for c in DECODE_CODECS}, encoders=encoders)
    caps = detect(runner)
    assert caps.decoder("av1", DecodeMode.VIDEOTOOLBOX).validated is None
    assert not caps.is_decode_usable("av1", DecodeMode.VIDEOTOOLBOX)  # Auto n'utilise que le validé


def test_a_failing_hwaccels_listing_or_runner_never_breaks_detection():
    caps = detect(FakeDecodeFFmpeg(broken_hwaccels=True))
    assert caps.decoders == () and caps.encoders == ()

    def exploding(command, timeout):
        if "-hwaccels" in command:
            raise RuntimeError("runner cassé")
        return FakeDecodeFFmpeg()(command, timeout)

    caps = detect(exploding)
    assert caps.decoders == ()


def test_decoding_can_be_disabled_separately():
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "h264")})
    caps = detect(runner, decode=False)
    assert caps.decoding_disabled and caps.decoders == ()
    assert not any("-hwaccels" in call for call in runner.calls)
    assert hardware_decoding_disabled({"KUT_STUDIO_HARDWARE_DECODING": "off"})
    assert hardware_decoding_disabled({"KUT_STUDIO_HARDWARE_ENCODING": "off"})
    assert not hardware_decoding_disabled({})


def test_describe_lists_decoders_without_raw_ffmpeg_output():
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "h264")})
    text = detect(runner).describe()
    assert "Décodeurs matériels" in text and "H.264 OK" in text and "HEVC non" in text
    assert "Copyright" not in text


# --- Cache ------------------------------------------------------------------------------------------


def test_capabilities_round_trip_with_decoders():
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "hevc10")})
    caps = detect(runner)
    again = HardwareCapabilities.from_dict(json.loads(json.dumps(caps.to_dict())))
    assert again == caps
    assert DecoderCapability.from_dict({"codec": "nope", "backend": "cuda"}) is None
    assert DecoderCapability.from_dict({"codec": "h264", "backend": "cpu"}) is None


def test_a_schema_1_cache_is_ignored_and_rescanned(tmp_path):
    path = tmp_path / "caps.json"
    path.write_text(json.dumps({"schema": 1, "encoders": []}), encoding="utf-8")
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "h264")})
    service = CapabilityService(command_provider=lambda: ["ffmpeg"], cache_path=path, runner=runner,
                                environment={})
    assert service.cached() is None  # ancien schéma : jamais relu
    caps = service.capabilities()
    assert caps.is_decode_usable("h264", DecodeMode.VIDEOTOOLBOX)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA_VERSION
    assert service.cached() == caps


def test_the_decoding_switch_is_part_of_the_installation_fingerprint(tmp_path):
    runner = FakeDecodeFFmpeg(working={("videotoolbox", "h264")})
    path = tmp_path / "caps.json"
    on = CapabilityService(command_provider=lambda: ["ffmpeg"], cache_path=path, runner=runner, environment={})
    assert on.capabilities().decoders
    off = CapabilityService(command_provider=lambda: ["ffmpeg"], cache_path=path, runner=runner,
                            environment={"KUT_STUDIO_HARDWARE_DECODING": "off"})
    assert off.cached() is None  # empreinte différente : redétection
    assert off.capabilities().decoding_disabled
