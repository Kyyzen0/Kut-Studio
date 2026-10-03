"""``describe_media`` : la matière des infobulles, avec des clés (jamais de libellé) et des valeurs non tronquées."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.media_describe import MEDIA_DESCRIPTION_KEYS, describe_media
from core.project_model import MediaAsset


def _video(**fields) -> MediaAsset:
    base = {"id": "v", "path": "/media/shoot/A001_C002.mp4", "name": "A001_C002.mp4", "duration": 12.0, "width": 3840,
            "height": 2160, "fps": 30000 / 1001, "media_type": "video", "has_audio": True}
    base.update(fields)
    return MediaAsset(**base)


def test_a_fully_described_video_lists_every_key_in_display_order():
    asset = _video(timecode="01:02:03;04", reel="A001", camera="Sony A7S III", creation_time="2026-03-14T09:26:53Z")
    assert describe_media(asset) == (
        ("file", "A001_C002.mp4"), ("resolution", "3840×2160"), ("fps", "29.97"), ("camera", "Sony A7S III"),
        ("reel", "A001"), ("timecode", "01:02:03;04"), ("creation_time", "2026-03-14T09:26:53Z"),
    )
    assert [key for key, _ in describe_media(asset)] == [key for key in MEDIA_DESCRIPTION_KEYS if key != "time_reference"]


def test_empty_values_are_skipped():
    assert describe_media(_video()) == (("file", "A001_C002.mp4"), ("resolution", "3840×2160"), ("fps", "29.97"))
    subtitle = MediaAsset("s", "", "Sous-titres", 5.0, 0, 0, 0.0, "subtitle", False)
    assert describe_media(subtitle) == ()


@pytest.mark.parametrize(
    "fps, decimal, expected",
    [(30000 / 1001, ".", "29.97"), (30000 / 1001, ",", "29,97"), (24000 / 1001, ",", "23,976"), (25.0, ",", "25"),
     (60000 / 1001, ".", "59.94"), (50.0, ".", "50"), (47.952, ".", "47.952")],
)
def test_fps_is_never_truncated(fps, decimal, expected):
    values = dict(describe_media(_video(fps=fps), decimal=decimal))
    assert values["fps"] == expected
    assert values["fps"] not in {"29", "23", "59"}


def test_an_audio_recording_shows_its_bwf_start_as_a_time_of_day():
    audio = MediaAsset("a", "C:\\Rushes\\Zoom\\ZOOM0007.wav", "ZOOM0007", 60.0, 0, 0, 0.0, "audio", True,
                       time_reference=3600.5, camera="Zoom F6")
    assert describe_media(audio) == (("file", "ZOOM0007.wav"), ("camera", "Zoom F6"), ("time_reference", "01:00:00.500"))


@pytest.mark.parametrize(
    "seconds, expected",
    [(0.0, "00:00:00.000"), (3600.0, "01:00:00.000"), (86399.9996, "00:00:00.000"), (45296.789, "12:34:56.789"),
     (0.0004, "00:00:00.000"), (0.0005, "00:00:00.001")],
)
def test_the_time_of_day_is_rounded_to_the_millisecond_and_never_reaches_24h(seconds, expected):
    audio = MediaAsset("a", "/m/a.wav", "a", 1.0, 0, 0, 0.0, "audio", True, time_reference=seconds)
    values = dict(describe_media(audio))
    assert values["time_reference"] == expected


def test_the_file_name_is_extracted_from_a_path_of_either_platform():
    assert dict(describe_media(_video(path="C:\\Users\\a\\Films\\x.mov")))["file"] == "x.mov"
    assert dict(describe_media(_video(path="/Users/a/Films/x.mov")))["file"] == "x.mov"


def test_core_descriptions_hold_no_hard_coded_label():
    """Les libellés (« Caméra », « Bobine »…) appartiennent à l'interface : le module ne contient que des clés."""
    source = (Path(__file__).resolve().parent.parent / "core" / "media_describe.py").read_text(encoding="utf-8")
    words = {
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isalpha()
    }
    assert not {"Caméra", "Camera", "Bobine", "Reel", "Fichier", "File", "Timecode", "Résolution"} & words
