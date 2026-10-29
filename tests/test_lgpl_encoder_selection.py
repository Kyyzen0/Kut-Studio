"""Un FFmpeg sans libx264 / libx265 (construction LGPL du Mac App Store, ADR-0013 étape 3) : le choix d'encodeur se fait par capacités.

FFmpeg est simulé (aucun GPU, aucune installation requise). Deux constructions sont comparées partout :

* **GPL** (Homebrew, hors Store) : la liste ``-encoders`` contient libx264 / libx265. Tout est **identique** à ce que l'application
  construisait avant cette étape (mêmes arguments, mêmes options proposées) ;
* **LGPL** : ni libx264 ni libx265. H.264 / HEVC passent par VideoToolbox, ou sont **refusés clairement** s'il n'y a aucun
  encodeur matériel validé. Un état refusé vaut pour tous les chemins (export, repli, film, aperçu, livrables, file de rendu).
"""

from __future__ import annotations

import uuid

import pytest
from test_hardware_decoding import ENCODERS as DECODE_ENCODERS
from test_hardware_decoding import FakeDecodeFFmpeg
from test_hardware_encoding import FakeFFmpeg

from core import film_render as FILM
from core import social_deliverables
from core.export_engine import hardware_fallback_applies
from core.hardware_cache import CapabilityService, set_default_service
from core.hardware_decoding import CODEC_BY_ID, DecodeMode, sample_command
from core.hardware_encoding import (
    HardwareCapabilities,
    HardwareEncoder,
    detect_capabilities,
)
from core.hardware_validation import skip_reason
from core.render_job import ErrorKind, JobStatus
from core.video_encoders import (
    MAX_BITRATE_KBPS,
    EncoderUnavailableError,
    auxiliary_h264_args,
    cpu_choice,
    encoder_options,
    resolve_video_encoder,
    software_fallback_possible,
    validation_encoder_args,
)


def _fake(*families: str, lgpl: bool) -> FakeFFmpeg:
    runner = FakeFFmpeg(*families)
    if lgpl:
        runner.listing = "".join(
            line for line in runner.listing.splitlines(keepends=True) if " libx264 " not in line and " libx265 " not in line
        )
    return runner


def caps(*families: str, lgpl: bool) -> HardwareCapabilities:
    return detect_capabilities(
        ["ffmpeg"], runner=_fake(*families, lgpl=lgpl), platform_name="darwin", machine="arm64"
    )


GPL = caps("videotoolbox", lgpl=False)
LGPL = caps("videotoolbox", lgpl=True)
LGPL_NO_HARDWARE = caps(lgpl=True)
GPL_NO_HARDWARE = caps(lgpl=False)


def install(tmp_path, monkeypatch, *families: str, lgpl: bool) -> CapabilityService:
    """Service global de capacités simulé (``environment={}`` : la variable d'arrêt de ``conftest.py`` ne s'applique pas)."""
    service = CapabilityService(
        command_provider=lambda: ["fake-ffmpeg"], cache_path=tmp_path / f"caps-{uuid.uuid4().hex}.json",     # un cache par installation simulée
        runner=_fake(*families, lgpl=lgpl), environment={},
    )
    service.capabilities()
    set_default_service(service)
    return service


# --- Ce que l'on sait --------------------------------------------------------------------------------------------


def test_the_absence_of_libx264_is_a_fact_only_when_the_encoder_list_was_read():
    assert GPL.software_known and not GPL.software_missing("h264") and not GPL.software_missing("hevc")
    assert LGPL.software_known and LGPL.software_missing("h264") and LGPL.software_missing("hevc")
    assert not LGPL.software_missing("prores_ks")                 # ProRes : encodeur natif
    for unknown in (HardwareCapabilities(), HardwareCapabilities(error="disabled")):
        assert not unknown.software_missing("h264")               # inconnu = présent, comme avant


def test_the_knowledge_survives_the_disk_cache():
    again = HardwareCapabilities.from_dict(LGPL.to_dict())
    assert again is not None and again.software_missing("h264")
    stale = LGPL.to_dict() | {"schema": 2}                        # un cache d'avant : ignoré, la détection est refaite
    assert HardwareCapabilities.from_dict(stale) is None


# --- resolve_video_encoder / cpu_choice ---------------------------------------------------------------------------


@pytest.mark.parametrize(("hardware", "build"), [("cpu", GPL), ("auto", GPL_NO_HARDWARE)])
def test_gpl_build_is_unchanged(hardware, build):
    h264 = resolve_video_encoder("h264", speed_preset="slow", quality=20, hardware=hardware, capabilities=build)
    assert h264.args == ("-c:v", "libx264", "-preset", "slow", "-crf", "20") and not h264.is_hardware
    assert h264.fallback_reason is None
    hevc = resolve_video_encoder("hevc", speed_preset="slow", quality=20, hardware=hardware, capabilities=build)
    assert hevc.args == ("-c:v", "libx265", "-preset", "slow", "-crf", "24", "-tag:v", "hvc1")


@pytest.mark.parametrize("hardware", ["cpu", "auto"])
def test_lgpl_build_uses_videotoolbox_in_place_of_libx264_and_says_so(hardware):
    choice = resolve_video_encoder("h264", quality=18, hardware=hardware, width=1920, height=1080, fps=30, capabilities=LGPL)
    assert choice.encoder == "h264_videotoolbox" and choice.is_hardware
    assert "libx264" not in choice.args and "-preset" not in choice.args and "-crf" not in choice.args
    assert choice.requested is HardwareEncoder(hardware)
    if hardware == "cpu":                                         # substitution d'une demande « CPU » : jamais silencieuse
        assert choice.fallback_reason and "libx264" in choice.fallback_reason
    hevc = resolve_video_encoder("hevc", quality=18, hardware=hardware, capabilities=LGPL)
    assert hevc.encoder == "hevc_videotoolbox" and hevc.args[-2:] == ("-tag:v", "hvc1")


def test_lgpl_quality_is_translated_to_a_bounded_documented_bitrate():
    rates = {}
    for crf in (28, 23, 18, 14):
        choice = resolve_video_encoder("h264", quality=crf, hardware="cpu", width=1920, height=1080, fps=30, capabilities=LGPL)
        rates[crf] = int(choice.args[choice.args.index("-b:v") + 1].rstrip("k"))
    assert rates[28] < rates[23] < rates[18] < rates[14]          # CRF plus bas = plus de débit
    huge = resolve_video_encoder("h264", quality=14, hardware="cpu", width=7680, height=4320, fps=240, capabilities=LGPL)
    assert int(huge.args[huge.args.index("-b:v") + 1].rstrip("k")) == MAX_BITRATE_KBPS
    tiny = resolve_video_encoder("h264", quality=28, hardware="cpu", width=16, height=16, fps=1, capabilities=LGPL)
    assert int(tiny.args[tiny.args.index("-b:v") + 1].rstrip("k")) == 500


@pytest.mark.parametrize("hardware", ["cpu", "auto"])
@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_lgpl_without_any_hardware_encoder_refuses_clearly(hardware, codec):
    with pytest.raises(EncoderUnavailableError) as error:
        resolve_video_encoder(codec, hardware=hardware, capabilities=LGPL_NO_HARDWARE)
    assert "libx26" in error.value.reason and "aucun encodeur matériel" in error.value.reason


def test_prores_and_explicit_hardware_are_unchanged_on_lgpl():
    prores = resolve_video_encoder("prores_ks", quality=3, hardware="cpu", capabilities=LGPL_NO_HARDWARE)
    assert prores.args == ("-c:v", "prores_ks", "-profile:v", "3")
    vt = resolve_video_encoder("h264", hardware="videotoolbox", capabilities=LGPL)
    assert vt.encoder == "h264_videotoolbox" and vt.fallback_reason is None
    with pytest.raises(EncoderUnavailableError):
        resolve_video_encoder("h264", hardware="nvenc", capabilities=LGPL)


def test_cpu_choice_follows_the_same_rules():
    assert cpu_choice("h264", quality=20, capabilities=GPL).args == ("-c:v", "libx264", "-preset", "medium", "-crf", "20")
    assert cpu_choice("h264", quality=20, capabilities=LGPL).encoder == "h264_videotoolbox"
    with pytest.raises(EncoderUnavailableError):
        cpu_choice("hevc", capabilities=LGPL_NO_HARDWARE)
    assert cpu_choice("prores_ks", quality=2, capabilities=LGPL_NO_HARDWARE).encoder == "prores_ks"


def test_a_cpu_request_never_triggers_a_detection(tmp_path, monkeypatch):
    service = CapabilityService(command_provider=lambda: ["fake-ffmpeg"], cache_path=tmp_path / "c.json",
                                runner=_fake("videotoolbox", lgpl=True), environment={})
    set_default_service(service)                                  # rien n'a été détecté
    assert resolve_video_encoder("h264", hardware="cpu").encoder == "libx264"
    assert service.scan_count == 0


# --- Les options proposées par l'interface -----------------------------------------------------------------------


def test_options_offered_are_only_the_possible_ones():
    assert [b for b, _ in encoder_options("h264", GPL)] == [HardwareEncoder.AUTO, HardwareEncoder.CPU, HardwareEncoder.VIDEOTOOLBOX]
    assert [b for b, _ in encoder_options("h264", LGPL)] == [HardwareEncoder.AUTO, HardwareEncoder.VIDEOTOOLBOX]
    assert encoder_options("h264", LGPL_NO_HARDWARE) == [] and encoder_options("hevc", LGPL_NO_HARDWARE) == []
    assert [b for b, _ in encoder_options("prores_ks", LGPL_NO_HARDWARE)] == [HardwareEncoder.CPU]
    assert [b for b, _ in encoder_options("h264", HardwareCapabilities())] == [HardwareEncoder.AUTO, HardwareEncoder.CPU]


def test_export_panel_shows_a_disabled_notice_instead_of_an_impossible_list(qtbot):
    from ui import i18n
    from ui.export_panel import ExportPanel

    panel = ExportPanel()
    qtbot.addWidget(panel)
    panel.set_capabilities(LGPL)
    assert [panel.encoder_combo.itemData(i) for i in range(panel.encoder_combo.count())] == ["auto", "videotoolbox"]
    panel.set_capabilities(LGPL_NO_HARDWARE)
    assert panel.encoder_combo.count() == 1
    assert panel.encoder_combo.itemText(0) == i18n.translate("render.encoder.none")
    assert not panel.encoder_combo.model().item(0).isEnabled()
    assert panel.current_encoder() == "auto"                      # la préférence enregistrée n'est pas écrasée
    panel.set_capabilities(GPL)
    assert [panel.encoder_combo.itemData(i) for i in range(panel.encoder_combo.count())][:2] == ["auto", "cpu"]


# --- Les autres chemins qui encodent ----------------------------------------------------------------------------


def test_auxiliary_h264_is_libx264_unchanged_on_gpl_and_videotoolbox_on_lgpl():
    assert auxiliary_h264_args(speed_preset="veryfast", crf=10, capabilities=GPL) == ("-c:v", "libx264", "-preset", "veryfast", "-crf", "10")
    assert auxiliary_h264_args(speed_preset="veryfast", capabilities=GPL) == ("-c:v", "libx264", "-preset", "veryfast")
    master = auxiliary_h264_args(speed_preset="veryfast", crf=10, width=1280, height=720, fps=25, capabilities=LGPL)
    assert master[:2] == ("-c:v", "h264_videotoolbox") and "-b:v" in master and "-crf" not in master
    assert auxiliary_h264_args(speed_preset="veryfast", capabilities=LGPL) == (
        "-c:v", "h264_videotoolbox", "-allow_sw", "0", "-profile:v", "high")
    with pytest.raises(EncoderUnavailableError):
        auxiliary_h264_args(speed_preset="veryfast", crf=10, capabilities=LGPL_NO_HARDWARE)


def test_social_preview_copy_command(tmp_path, monkeypatch):
    monkeypatch.setattr(social_deliverables, "_ffmpeg", lambda: ["ffmpeg"])
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    gpl = social_deliverables.preview_copy_command("a.mp4", "b.mp4", 1920, 1080, 2000)
    assert gpl[gpl.index("-c:v"):gpl.index("-c:v") + 4] == ["-c:v", "libx264", "-preset", "veryfast"]
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    lgpl = social_deliverables.preview_copy_command("a.mp4", "b.mp4", 1920, 1080, 2000)
    assert "libx264" not in lgpl and "-preset" not in lgpl and lgpl[lgpl.index("-c:v") + 1] == "h264_videotoolbox"
    assert lgpl[lgpl.index("-b:v") + 1] == "2000k" and lgpl[lgpl.index("-maxrate") + 1] == "2000k"
    install(tmp_path, monkeypatch, lgpl=True)
    with pytest.raises(social_deliverables.DeliverableError, match="libx264"):
        social_deliverables.preview_copy_command("a.mp4", "b.mp4", 1920, 1080, 2000)


def test_film_background_command(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    def fake_run(arguments, what, **_):
        seen.append(arguments)
        (tmp_path / ".bg.partial.mp4").write_bytes(b"x")          # ce que FFmpeg aurait écrit

    monkeypatch.setattr(FILM, "run_ffmpeg", fake_run)
    spec = FILM.BackgroundSpec(color=(0.1, 0.2, 0.3))
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    FILM.make_background_video(spec, (320, 180), 25.0, 10, tmp_path / "bg.mp4")
    assert seen[-1][seen[-1].index("-frames:v") + 2:][:6] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10"]
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    FILM.make_background_video(spec, (320, 180), 25.0, 10, tmp_path / "bg.mp4")
    assert "libx264" not in seen[-1] and "h264_videotoolbox" in seen[-1] and "-b:v" in seen[-1]
    install(tmp_path, monkeypatch, lgpl=True)
    with pytest.raises(FILM.FilmError, match="libx264"):
        FILM.make_background_video(spec, (320, 180), 25.0, 10, tmp_path / "bg.mp4")


def test_preview_command(tmp_path, monkeypatch):
    from core.filter_graph import build_preview_command
    from core.project_model import Project
    from core.render_plan import build_render_plan

    plan = build_render_plan(Project("p", width=320, height=180, fps=25.0))
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    gpl = build_preview_command(plan, width=320, height=180, fps=25, quality="standard", output_path="o.mp4")
    assert gpl[gpl.index("-c:v") + 1] == "libx264" and "-crf" in gpl
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    lgpl = build_preview_command(plan, width=320, height=180, fps=25, quality="standard", output_path="o.mp4")
    assert lgpl[lgpl.index("-c:v") + 1] == "h264_videotoolbox" and "-crf" not in lgpl and "-preset" not in lgpl
    install(tmp_path, monkeypatch, lgpl=True)
    with pytest.raises(EncoderUnavailableError):
        build_preview_command(plan, width=320, height=180, fps=25, quality="standard", output_path="o.mp4")


def test_proxy_profiles_go_through_the_same_choice(tmp_path, monkeypatch):
    from core.proxy_profiles import PROXY_CODECS, get_profile

    profile = get_profile("medium")
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    assert PROXY_CODECS["h264"].build_video_args(profile)[:2] == ["-c:v", "libx264"]
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    assert PROXY_CODECS["h264"].build_video_args(profile)[:2] == ["-c:v", "h264_videotoolbox"]
    install(tmp_path, monkeypatch, lgpl=True)
    with pytest.raises(EncoderUnavailableError):
        PROXY_CODECS["h264"].build_video_args(profile)


# --- Repli, file de rendu, validation -----------------------------------------------------------------------------


def test_hardware_fallback_to_software_only_when_software_exists(tmp_path, monkeypatch):
    choice = resolve_video_encoder("h264", hardware="auto", capabilities=LGPL)
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    assert software_fallback_possible("h264")
    assert hardware_fallback_applies(choice, progress_seen=False, fallback_used=False)
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    assert not software_fallback_possible("h264") and not software_fallback_possible("hevc")
    assert not hardware_fallback_applies(choice, progress_seen=False, fallback_used=False)   # rien vers quoi se replier


def test_validation_witness_is_skipped_not_silently_replaced():
    assert skip_reason(GPL, HardwareEncoder.CPU) is None
    assert "libx264" in (skip_reason(LGPL, HardwareEncoder.CPU) or "")
    assert skip_reason(HardwareCapabilities(error="disabled"), HardwareEncoder.CPU) is None


def test_validation_encoder_args_for_videotoolbox_are_unchanged():
    item = LGPL.capability("h264", HardwareEncoder.VIDEOTOOLBOX)
    assert item is not None and validation_encoder_args(item)[0] == "-b:v"


def test_retry_on_cpu_is_not_offered_without_a_software_encoder(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=True)
    job = _failed_encoder_job("videotoolbox")
    assert not job.can_retry_on_cpu
    install(tmp_path, monkeypatch, "videotoolbox", lgpl=False)
    assert job.can_retry_on_cpu


def _failed_encoder_job(hardware: str):
    from core.render_job import RenderJob

    job = RenderJob.__new__(RenderJob)
    job.status = JobStatus.FAILED
    job.error_kind = ErrorKind.ENCODER
    job.hardware = hardware
    job.video_codec = "h264"
    return job


# --- Décodage matériel : des échantillons faits par VideoToolbox ----------------------------------------------------


LGPL_DECODE_ENCODERS = (
    DECODE_ENCODERS.replace(" V....D libx264              libx264 H.264 / AVC (codec h264)\n", "")
    .replace(" V....D libx265              libx265 H.265 / HEVC (codec hevc)\n", "")
    .replace(" V....D libvpx-vp9           libvpx VP9 (codec vp9)\n", "")
    .replace(" V....D libsvtav1            SVT-AV1 (codec av1)\n", "")
    + " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n"
    + " V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)\n"
)


def _decode_caps(encoders: str):
    runner = FakeDecodeFFmpeg(working={("videotoolbox", c.id) for c in CODEC_BY_ID.values()}, encoders=encoders)
    return detect_capabilities(["ffmpeg"], runner=runner, platform_name="darwin", machine="arm64"), runner


def test_decode_samples_use_software_encoders_on_a_full_build():
    capabilities, runner = _decode_caps(DECODE_ENCODERS)
    samples = {c[c.index("-c:v") + 1] for c in runner.calls if "lavfi" in c and "-c:v" in c}
    assert samples == {"libx264", "libx265", "prores_ks", "libvpx-vp9", "libsvtav1"}
    assert all(capabilities.is_decode_usable(c, DecodeMode.VIDEOTOOLBOX) for c in CODEC_BY_ID)


def test_decode_samples_fall_back_to_videotoolbox_encoders_on_a_lgpl_build():
    capabilities, runner = _decode_caps(LGPL_DECODE_ENCODERS)
    samples = {c[c.index("-c:v") + 1] for c in runner.calls if "lavfi" in c and "-c:v" in c}
    assert samples == {"h264_videotoolbox", "hevc_videotoolbox", "prores_ks"}
    for usable in ("h264", "hevc", "hevc10", "prores"):
        assert capabilities.is_decode_usable(usable, DecodeMode.VIDEOTOOLBOX), usable
    for unverifiable in ("vp9", "av1"):                           # aucun encodeur : non vérifiable, pas « inutilisable »
        item = capabilities.decoder(unverifiable, DecodeMode.VIDEOTOOLBOX)
        assert item is not None and item.validated is None and not item.usable and "non vérifiable" in item.detail
        assert "n'a pas pu être vérifié" in capabilities.decode_unavailable_reason(unverifiable, DecodeMode.VIDEOTOOLBOX)


def test_a_failing_videotoolbox_sample_keeps_the_codec_unverified_never_validated():
    class NoSample(FakeDecodeFFmpeg):
        def __call__(self, command, timeout):
            if "lavfi" in command and "hevc_videotoolbox" in command:
                from core.hardware_encoding import RunOutput

                return RunOutput(1, "", "encoder failed")
            return super().__call__(command, timeout)

    runner = NoSample(working={("videotoolbox", c.id) for c in CODEC_BY_ID.values()}, encoders=LGPL_DECODE_ENCODERS)
    capabilities = detect_capabilities(["ffmpeg"], runner=runner, platform_name="darwin", machine="arm64")
    assert capabilities.decoder("hevc", DecodeMode.VIDEOTOOLBOX).validated is None
    assert capabilities.is_decode_usable("h264", DecodeMode.VIDEOTOOLBOX)


def test_sample_command_keeps_the_preferred_encoder_when_it_exists():
    names = {"libx264", "h264_videotoolbox"}
    assert "libx264" in sample_command(["ffmpeg"], CODEC_BY_ID["h264"], "o.mp4", names)
    assert "h264_videotoolbox" in sample_command(["ffmpeg"], CODEC_BY_ID["h264"], "o.mp4", {"h264_videotoolbox"})
    assert "libx264" in sample_command(["ffmpeg"], CODEC_BY_ID["h264"], "o.mp4")           # signature d'avant
