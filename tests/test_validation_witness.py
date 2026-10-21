"""Témoin CPU du rapport de validation : H.264 et HEVC partagent le MP4, mais pas leur témoin.

Régression : le témoin était indexé par conteneur seul. Le témoin HEVC, exécuté en dernier, écrasait celui de H.264 :
le repli H.264 annonçait alors le résultat du témoin HEVC.
"""

from __future__ import annotations

from core.hardware_encoding import HardwareCapabilities, HardwareEncoder
from core.hardware_validation import EncoderValidation, Outcome, ValidationReport, format_report


def _run(backend, codec, outcome, encoder, fallback=""):
    return EncoderValidation(
        backend=backend, container="mp4", codec=codec, encoder=encoder,
        outcome=outcome, fallback=fallback, returncode=0,
    )


def _fallback_line(text: str) -> str:
    return next(line for line in text.splitlines() if "repli si cet encodeur échoue" in line)


def test_the_h264_fallback_is_judged_by_the_h264_witness_not_by_the_hevc_one():
    report = ValidationReport(
        HardwareCapabilities(),
        runs=(
            _run(HardwareEncoder.CPU, "h264", Outcome.PASSED, "libx264"),
            _run(HardwareEncoder.CPU, "hevc", Outcome.FAILED, "libx265"),
            _run(HardwareEncoder.VIDEOTOOLBOX, "h264", Outcome.FAILED, "h264_videotoolbox", fallback="libx264"),
        ),
    )
    assert "le témoin CPU a réussi" in _fallback_line(format_report(report))


def test_the_hevc_fallback_is_judged_by_the_hevc_witness():
    report = ValidationReport(
        HardwareCapabilities(),
        runs=(
            _run(HardwareEncoder.CPU, "h264", Outcome.PASSED, "libx264"),
            _run(HardwareEncoder.CPU, "hevc", Outcome.FAILED, "libx265"),
            _run(HardwareEncoder.VIDEOTOOLBOX, "hevc", Outcome.FAILED, "hevc_videotoolbox", fallback="libx265"),
        ),
    )
    assert "le témoin CPU a lui aussi échoué" in _fallback_line(format_report(report))


def test_the_codec_is_part_of_the_exported_run():
    run = _run(HardwareEncoder.CPU, "hevc", Outcome.PASSED, "libx265")
    assert run.to_dict()["codec"] == "hevc"
