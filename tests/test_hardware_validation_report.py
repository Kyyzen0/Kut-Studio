"""Logique de la validation matérielle : verdicts, raisons et rapport, avec FFmpeg et ffprobe simulés.

Aucun GPU n'est nécessaire : la détection est celle de l'application nourrie par une sortie FFmpeg simulée
(``FakeFFmpeg``), les mini exports, la relecture ffprobe et le décodage sont simulés par ``FakeMedia``. Les
vrais exports sont dans ``test_hardware_color_validation.py`` ; le repli du moteur d'export lui-même est
testé dans ``test_hardware_fallback.py`` (ici, seul ce que le rapport en dit).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_hardware_encoding import FakeFFmpeg

from core.export_engine import ExportFormat
from core.hardware_cache import CapabilityService, set_default_service
from core.hardware_encoding import HardwareEncoder, RunOutput
from core.hardware_validation import (
    PATCHES,
    REQUIRE_VARIABLE,
    VALIDATION_SIZE,
    MediaTools,
    Outcome,
    expected_color_tags,
    format_report,
    media_tools,
    patch_widths,
    required_backends,
    run_validation,
)

FFMPEG, FFPROBE = "fake-ffmpeg", "fake-ffprobe"
TOOLS = MediaTools(FFMPEG, FFPROBE)
GOOD_TAGS = {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv"}
BT601_DECODED = [(232, 100, 21), (28, 159, 56), (26, 66, 207)]
"""Ce que donne un export converti en BT.601 et lu en BT.709 (mesuré avec le vrai FFmpeg)."""


def _frame(colours) -> bytes:
    width, height = VALIDATION_SIZE
    row = b"".join(bytes(colour) * bar for colour, bar in zip(colours, patch_widths(width)))
    return row * height


class FakeMedia:
    """Faux FFmpeg / ffprobe de la validation : mini exports, relecture et décodage selon un scénario.

    Args:
        tags: balises relues par encodeur (défaut : BT.709 complet).
        colours: couleurs décodées par encodeur (défaut : celles de la source).
        failures: ``encodeur -> (code, sortie d'erreur)`` d'un export qui échoue.
        probe: ``(code, sortie)`` brute de ffprobe, pour tous les fichiers.
    """

    def __init__(self, *, tags=None, colours=None, failures=None, probe=None):
        self.tags, self.colours = tags or {}, colours or {}
        self.failures, self.probe = failures or {}, probe
        self.exports: list[str] = []
        self._made_by: dict[str, str] = {}

    def __call__(self, command, timeout):
        command = [str(item) for item in command]
        output = command[-1]
        if command[0] == FFPROBE:
            if self.probe is not None:
                return RunOutput(self.probe[0], self.probe[1], "ffprobe : erreur simulée")
            tags = self.tags.get(self._made_by[output], GOOD_TAGS)
            return RunOutput(0, json.dumps({"streams": [{"codec_name": "h264", **tags}]}))
        if output.endswith(".mkv"):                                         # la source de test
            Path(output).write_bytes(b"source")
            return RunOutput(0)
        if output.endswith(".rgb"):                                         # une image décodée en RVB
            encoder = self._made_by[command[command.index("-i") + 1]]
            Path(output).write_bytes(_frame(self.colours.get(encoder, [patch.rgb for patch in PATCHES])))
            return RunOutput(0)
        encoder = command[command.index("-c:v") + 1]                        # un mini export
        self.exports.append(encoder)
        if encoder in self.failures:
            code, stderr = self.failures[encoder]
            return RunOutput(code, "", stderr)
        Path(output).write_bytes(b"\0" * 2048)
        self._made_by[output] = encoder
        return RunOutput(0)


@pytest.fixture
def detected(tmp_path, monkeypatch):
    """Détection de l'application sur une sortie FFmpeg simulée ; l'export lit ce même service."""
    monkeypatch.setattr("core.export_engine._ffmpeg_path", FFMPEG)

    def make(*families, failing=()):
        service = CapabilityService(
            command_provider=lambda: [FFMPEG], cache_path=tmp_path / "caps.json",
            runner=FakeFFmpeg(*families, failing=failing), environment={},
        )
        set_default_service(service)
        return service.capabilities()

    return make


def _validate(capabilities, media, tmp_path, backends, required=()):
    return run_validation(capabilities, tools=TOOLS, workdir=tmp_path, backends=backends,
                          formats=(ExportFormat.MP4_H264,), required=required, runner=media)


def _run(report, backend):
    (run,) = [item for item in report.runs if item.backend is backend]
    return run


def test_the_expected_tags_come_from_the_export_command_line_options():
    assert expected_color_tags() == GOOD_TAGS


def test_a_mistagged_file_is_a_failure_that_names_each_wrong_tag(detected, tmp_path):
    """Le défaut constaté sous macOS avant ``setparams`` : seule la matrice balisée, primaires et transfert absents."""
    media = FakeMedia(tags={"h264_nvenc": {"color_space": "bt709", "color_range": "tv"}})
    report = _validate(detected("nvenc"), media, tmp_path, [HardwareEncoder.NVENC])
    nvenc = _run(report, HardwareEncoder.NVENC)
    assert _run(report, HardwareEncoder.CPU).outcome is Outcome.PASSED
    assert nvenc.outcome is Outcome.FAILED and nvenc.returncode == 0 and nvenc.size_bytes > 0
    assert "color_primaries : attendu bt709, obtenu absent" in nvenc.reason
    assert "color_transfer : attendu bt709, obtenu absent" in nvenc.reason
    assert "color_space" not in nvenc.reason and "color_range" not in nvenc.reason
    assert not report.passed and report.exit_code == 1 and report.validated_backends() == ()
    assert "[ÉCHEC] NVIDIA NVENC (h264_nvenc) · MP4" in format_report(report)


def test_wrong_colours_are_a_failure_even_with_correct_tags(detected, tmp_path):
    media = FakeMedia(colours={"h264_nvenc": BT601_DECODED})
    nvenc = _run(_validate(detected("nvenc"), media, tmp_path, [HardwareEncoder.NVENC]), HardwareEncoder.NVENC)
    assert nvenc.outcome is Outcome.FAILED and nvenc.obtained_tags == {"codec_name": "h264", **GOOD_TAGS}
    assert "couleur vert : attendu (40, 180, 60), obtenu (28, 159, 56) (écart 21 > 6)" in nvenc.reason
    assert [pixel.obtained for pixel in nvenc.pixels] == BT601_DECODED


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        ((0, "Invalid data found when processing input"), "sortie de ffprobe illisible"),
        ((0, ""), "sortie de ffprobe illisible (sortie vide)"),
        ((0, '{"streams": []}'), "aucun flux vidéo"),
        ((1, ""), "ffprobe a échoué (code 1) : ffprobe : erreur simulée"),
    ],
    ids=["texte", "vide", "sans-flux", "code-1"],
)
def test_an_unreadable_file_is_a_failure_never_a_success(detected, tmp_path, probe, message):
    media = FakeMedia(probe=probe)
    report = _validate(detected(), media, tmp_path, [HardwareEncoder.CPU])
    (cpu,) = report.runs
    assert cpu.outcome is Outcome.FAILED and message in cpu.reason
    assert report.exit_code == 1


def test_absent_and_refused_backends_are_skipped_with_a_reason_and_never_counted_as_passed(detected, tmp_path):
    media = FakeMedia()
    report = _validate(detected("amf", failing=("amf",)), media, tmp_path, None)
    assert media.exports == ["libx264"]                                      # rien n'est lancé pour un saut
    amf, nvenc = _run(report, HardwareEncoder.AMF), _run(report, HardwareEncoder.NVENC)
    assert amf.outcome is nvenc.outcome is Outcome.SKIPPED
    assert amf.reason == "AMD AMF : présent mais refusé à la validation : Error initializing the encoder"
    assert nvenc.reason == "NVIDIA NVENC : encodeur absent de ce FFmpeg (h264_nvenc)"
    assert amf.returncode is None and amf.pixels == () and amf.obtained_tags == {}
    # Sauté ≠ réussi : le verdict est vert, mais aucun backend matériel n'est déclaré vérifié.
    assert report.passed and report.exit_code == 0 and report.validated_backends() == ()
    assert (report.count(Outcome.PASSED), report.count(Outcome.SKIPPED)) == (1, 5)
    text = format_report(report)
    assert "Backends matériels réellement vérifiés : aucun" in text and "sauté ne vaut pas réussi" in text
    assert "h264_amf [AMD AMF] : refusé à la validation (Error initializing the encoder)" in text
    data = json.loads(json.dumps(report.to_dict()))
    assert data["summary"] == {"passed": 1, "failed": 0, "skipped": 5} and data["validated_hardware"] == []
    assert data["detected_encoders"][0]["encoder"] == "h264_amf" and data["detected_encoders"][0]["validated"] is False


def test_a_machine_without_any_hardware_encoder_says_so(detected, tmp_path):
    report = _validate(detected(), FakeMedia(), tmp_path, None)
    assert "aucun : ce FFmpeg n'expose aucun encodeur matériel sur cette machine" in format_report(report)
    assert report.exit_code == 0 and report.count(Outcome.SKIPPED) == 5


def test_a_required_backend_that_is_missing_is_a_failure(detected, tmp_path):
    required = required_backends({REQUIRE_VARIABLE: " nvenc, VideoToolbox "})
    assert required == {HardwareEncoder.NVENC, HardwareEncoder.VIDEOTOOLBOX}
    assert required_backends({}) == frozenset() and required_backends({REQUIRE_VARIABLE: ""}) == frozenset()
    with pytest.raises(ValueError, match="backend inconnu « nvidia »"):
        required_backends({REQUIRE_VARIABLE: "nvidia"})               # une faute de frappe n'est jamais muette
    report = _validate(detected(), FakeMedia(), tmp_path, [HardwareEncoder.NVENC], required=required)
    nvenc = _run(report, HardwareEncoder.NVENC)
    assert nvenc.outcome is Outcome.FAILED
    assert nvenc.reason == "NVIDIA NVENC : encodeur absent de ce FFmpeg (h264_nvenc) — exigé par " + REQUIRE_VARIABLE
    assert report.exit_code == 1


@pytest.mark.parametrize(
    ("failures", "detail", "witness"),
    [
        ({"h264_nvenc": (1, "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed\nError initializing output stream")},
         "l'export a échoué (code 1) : Error initializing output stream", "le témoin CPU a réussi"),
        ({"h264_nvenc": (-1, "délai dépassé")}, "l'export a échoué (code -1) : délai dépassé",
         "le témoin CPU a réussi"),
        ({"h264_nvenc": (1, "No space left on device"), "libx264": (1, "No space left on device")},
         "l'export a échoué (code 1) : No space left on device", "le témoin CPU a lui aussi échoué"),
    ],
    ids=["initialisation", "delai", "cpu-aussi"],
)
def test_a_failing_hardware_export_reports_its_error_and_the_cpu_fallback(detected, tmp_path, failures, detail, witness):
    report = _validate(detected("nvenc"), FakeMedia(failures=failures), tmp_path, [HardwareEncoder.NVENC])
    nvenc = _run(report, HardwareEncoder.NVENC)
    assert nvenc.outcome is Outcome.FAILED and nvenc.reason == detail
    assert nvenc.fallback == "libx264" and _run(report, HardwareEncoder.CPU).fallback == ""
    text = format_report(report)
    assert "repli si cet encodeur échoue : Auto relance une fois en CPU (libx264)" in text and witness in text
    assert report.exit_code == 1


def test_the_diagnostic_tool_runs_without_any_gpu_and_reports_in_text_and_json(tmp_path, monkeypatch, capsys):
    """Outil réel (vrai FFmpeg pour le témoin CPU), sur une détection sans aucun encodeur matériel."""
    from tools.perf.hardware_validation import main

    tools = media_tools()
    if tools is None:
        pytest.skip("FFmpeg ou ffprobe absent : l'outil ne peut rien exporter")
    monkeypatch.delenv(REQUIRE_VARIABLE, raising=False)
    set_default_service(CapabilityService(
        command_provider=lambda: [tools.ffmpeg], cache_path=tmp_path / "caps.json", runner=FakeFFmpeg(),
        environment={},
    ))
    target = tmp_path / "rapport.json"
    assert main(["--json", str(target)]) == 0
    text = capsys.readouterr().out
    assert "aucun : ce FFmpeg n'expose aucun encodeur matériel" in text and "[RÉUSSI] CPU (libx264) · MP4" in text
    data = json.loads(target.read_text(encoding="utf-8"))
    cpu = [run for run in data["runs"] if run["backend"] == "cpu"]
    assert [run["outcome"] for run in cpu] == ["passed", "passed"] and [run["container"] for run in cpu] == ["mp4", "mov"]
    assert cpu[0]["obtained_tags"] == GOOD_TAGS and len(cpu[0]["pixels"]) == 3
    assert {run["outcome"] for run in data["runs"] if run["backend"] != "cpu"} == {"skipped"}
    assert data["exit_code"] == 0 and data["validated_hardware"] == []

    monkeypatch.setenv(REQUIRE_VARIABLE, "nvenc")
    assert main(["--encoder", "h264_nvenc"]) == 1                     # exigé et absent : échec
    assert "exigé par KUT_STUDIO_REQUIRE_HARDWARE" in capsys.readouterr().out
