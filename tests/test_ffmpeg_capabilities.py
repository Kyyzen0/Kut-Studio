"""Lecture des encodeurs, décodeurs et formats employés (``tools/ffmpeg_capabilities.py``, ADR-0013) : sans FFmpeg, sur des sources et des listes simulées."""

from __future__ import annotations

from pathlib import Path

from tools import ffmpeg_capabilities as caps


def test_the_reader_finds_codecs_formats_and_hwaccels_in_command_lists(tmp_path: Path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "demo.py").write_text(
        'ARGS = ["-c:v", "libx264", "-c:a", "aac", "-f", "matroska", "-f", "lavfi", "-hwaccel", "videotoolbox", "-vcodec", "png"]\n'
        'NOT_A_COMMAND = ["-c:v", "pas un nom"]\n', encoding="utf-8")
    cited = {(item.category, item.name): item.where for item in caps.used_items(tmp_path)}
    assert cited[("encoder", "libx264")] == ("core/demo.py",)
    assert ("encoder", "aac") in cited and ("encoder", "png") in cited
    assert ("muxer", "matroska") in cited and ("demuxer", "lavfi") in cited and ("hwaccel", "videotoolbox") in cited
    assert ("encoder", "pas un nom") not in cited
    assert ("decoder", "h264") in cited, "les décodeurs des médias importés sont déclarés, pas cités"


def test_classification_distinguishes_present_gpl_and_missing():
    offered = {"encoder": {"aac"}, "decoder": set(), "muxer": set(), "demuxer": {"s16le"}, "hwaccel": set()}
    assert caps.classify(caps.Item("encoder", "aac", "", ()), offered) == "présent"
    assert caps.classify(caps.Item("encoder", "libx264", "", ()), offered) == "absent, GPL"
    assert caps.classify(caps.Item("encoder", "libsvtav1", "", ()), offered) == "absent"
    assert caps.classify(caps.Item("muxer", "s16le", "", ()), offered) == "présent", "un format brut est aussi un format d'entrée"


def test_the_gpl_encoders_are_not_in_the_declared_store_list():
    declared = {name for (category, name) in caps.DECLARED if category == "encoder"}
    assert not declared & caps.GPL_CODECS
