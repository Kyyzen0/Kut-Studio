"""Métadonnées de tournage lues par la sonde (timecode, BWF, bobine, caméra, création).

Deux niveaux : des sorties ``ffprobe`` **fabriquées** (conteneurs que la machine de test ne sait pas toujours écrire,
balises mal formées) et de **vrais** petits fichiers générés par FFmpeg (``-timecode``, ``-write_bext``…). Les
formes de sortie viennent d'une mesure avec FFmpeg 9 sur fichiers synthétiques ; les vraies caméras n'ont pas pu être
testées (voir ``docs/multicam.md``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from core.media_probe import MediaMetadata, extract_media_metadata, probe_media
from core.project_model import MediaAsset
from core.timecode import asset_start_seconds


def _video(rate="25/1", **tags):
    stream = {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": rate, "r_frame_rate": rate}
    if tags:
        stream["tags"] = tags
    return stream


def _tmcd(rate="12800/512", **tags):
    return {"codec_type": "data", "codec_tag_string": "tmcd", "avg_frame_rate": rate, "r_frame_rate": "0/0",
            "tags": {"language": "eng", "handler_name": "TimeCodeHandler", **tags}}


def _audio(sample_rate="48000", **tags):
    stream = {"codec_type": "audio", "sample_rate": sample_rate}
    if tags:
        stream["tags"] = tags
    return stream


def _payload(streams, **format_tags):
    return {"streams": streams, "format": {"duration": "10.0", **({"tags": format_tags} if format_tags else {})}}


# --- Sorties ffprobe fabriquées -------------------------------------------------------------------------------------


def test_mp4_timecode_comes_from_the_video_stream_and_the_tmcd_track():
    meta = extract_media_metadata(_payload([_video(timecode="01:02:03:04"), _audio(), _tmcd(timecode="01:02:03:04")]))
    assert meta.timecode == "01:02:03:04"
    assert meta.timecode_fps == 0.0                       # la piste timecode a la cadence de l'image : rien à noter
    assert meta == MediaMetadata(timecode="01:02:03:04")


def test_the_tmcd_track_alone_is_enough():
    meta = extract_media_metadata(_payload([_video(), _tmcd(timecode="10:00:00;02", reel_name="B002")], timecode=""))
    assert (meta.timecode, meta.reel) == ("10:00:00;02", "B002")


def test_mov_reel_name_and_camera_model():
    meta = extract_media_metadata(_payload(
        [_video(timecode="01:02:03:04"), _audio(), _tmcd(timecode="01:02:03:04", reel_name="A001")],
        timecode="01:02:03:04", model="Sony A7S III", major_brand="qt  ",
    ))
    assert (meta.timecode, meta.reel, meta.camera) == ("01:02:03:04", "A001", "Sony A7S III")


def test_drop_frame_keeps_its_semicolon():
    meta = extract_media_metadata(_payload([_video("30000/1001", timecode="01:02:03;04")]))
    assert meta.timecode == "01:02:03;04"


def test_the_dot_variant_is_normalised_to_the_drop_frame_semicolon():
    assert extract_media_metadata(_payload([_video()], timecode=" 1:02:03.04 ")).timecode == "01:02:03;04"


def test_mxf_has_its_timecode_in_the_container_tags_only():
    meta = extract_media_metadata(_payload([_video(file_package_umid="0x06"), _audio("48000")], timecode="10:00:00:00",
                                           product_name="OP1a Muxer"))
    assert meta.timecode == "10:00:00:00"


def test_matroska_uppercase_tag_is_found():
    meta = extract_media_metadata(_payload([_video(ENCODER="Lavc", DURATION="00:00:01.0"), _audio()],
                                           TIMECODE="00:00:10:00", ENCODER="Lavf"))
    assert meta.timecode == "00:00:10:00"


def test_the_video_stream_timecode_wins_over_the_container_one():
    meta = extract_media_metadata(_payload([_video(timecode="01:00:00:00")], timecode="02:00:00:00"))
    assert meta.timecode == "01:00:00:00"


def test_a_malformed_first_candidate_does_not_hide_a_valid_later_one():
    meta = extract_media_metadata(_payload([_video(timecode="garbage")], timecode="02:00:00:00"))
    assert meta.timecode == "02:00:00:00"


def test_a_video_timecode_track_at_another_rate_is_recorded():
    """59,94 i/s dont le timecode compte à 29,97 : sans cette cadence l'étiquette serait lue au mauvais rythme."""
    meta = extract_media_metadata(_payload(
        [_video("60000/1001", timecode="00:00:01;15"), _tmcd("30000/1001", timecode="00:00:01;15")]))
    assert meta.timecode_fps == pytest.approx(30000 / 1001)
    asset = MediaAsset("v", "/m/v.mp4", "v", 10.0, 1920, 1080, 60000 / 1001, "video", True, **meta.asset_fields())
    assert asset_start_seconds(asset) == pytest.approx(45 * 1001 / 30000)


@pytest.mark.parametrize("rate", ["0/0", "100/1", "", None, "abc", "30/0", 25])
def test_an_unreadable_or_unknown_timecode_rate_is_ignored(rate):
    meta = extract_media_metadata(_payload([_video("60000/1001"), _tmcd(rate, timecode="00:00:01;15")]))
    assert meta.timecode == "00:00:01;15" and meta.timecode_fps == 0.0


def test_bwf_time_reference_is_samples_divided_by_the_sample_rate():
    meta = extract_media_metadata(_payload([_audio("48000")], time_reference="172800000"))
    assert meta.time_reference == 3600.0
    meta = extract_media_metadata(_payload([_audio("44100")], time_reference="158760000"))
    assert meta.time_reference == 3600.0
    meta = extract_media_metadata(_payload([_audio("48000")], time_reference="48000"))
    assert meta.time_reference == 1.0
    precise = extract_media_metadata(_payload([_audio("48000")], time_reference="1234567891"))
    assert precise.time_reference == pytest.approx(1234567891 / 48000, abs=1e-9)


@pytest.mark.parametrize(
    "streams, value",
    [
        ([_audio("48000")], "0"),                         # 0 = « l'horloge n'était pas réglée » (défaut de FFmpeg)
        ([_audio("48000")], "-5"), ([_audio("48000")], "12.5"), ([_audio("48000")], "abc"), ([_audio("48000")], ""),
        ([_audio("48000")], str(48000 * 86400)),          # 24 h pile : hors journée
        ([_audio("0")], "172800000"), ([_audio("abc")], "172800000"), ([{"codec_type": "audio"}], "172800000"),
        ([_video()], "172800000"),                        # pas de flux audio : pas de fréquence d'échantillonnage
    ],
)
def test_an_unusable_time_reference_is_dropped(streams, value):
    assert extract_media_metadata(_payload(streams, time_reference=value)).time_reference is None


def test_no_tags_at_all_gives_the_defaults():
    assert extract_media_metadata(_payload([_video(), _audio()])) == MediaMetadata()
    assert extract_media_metadata({}) == MediaMetadata()


@pytest.mark.parametrize(
    "payload",
    [
        {"streams": None, "format": None}, {"streams": "x", "format": []}, {"streams": [None, 3, "a"], "format": 5},
        {"streams": [{"codec_type": "video", "tags": "oops"}], "format": {"tags": ["a"]}},
        {"streams": [{"codec_type": "video", "tags": {"timecode": 12, "model": None, "reel_name": [1]}}],
         "format": {"tags": {1: "x", None: "y", "creation_time": 5, "time_reference": 172800000}}},
        {"streams": [{"codec_type": None}, {}], "format": {"tags": {}}},
    ],
)
def test_malformed_tags_never_raise_and_give_the_defaults(payload):
    assert extract_media_metadata(payload) == MediaMetadata()


@pytest.mark.parametrize("text", ["24:00:00:00", "00:60:00:00", "00:00:00", "01:02:03:04:05", "", "  ", "a:b:c:d",
                                  "01:02:03,04", "٠١:٠٢:٠٣:٠٤"])
def test_a_malformed_timecode_string_is_dropped(text):
    assert extract_media_metadata(_payload([_video(timecode=text)], timecode=text)).timecode == ""


def test_text_tags_are_cleaned_and_bounded():
    meta = extract_media_metadata(_payload([_video()], model="  Alpha\u0000\n  A7S\t III  ", make=" Sony\x07 "))
    assert meta.camera == "Sony Alpha A7S III"
    long = extract_media_metadata(_payload([_video()], model="x" * 500, reel_name="y" * 500))
    assert len(long.camera) == len(long.reel) == 120
    unicode_name = extract_media_metadata(_payload([_video()], model="Canon EOS C70 — 日本語", reel_name="Bobine é"))
    assert (unicode_name.camera, unicode_name.reel) == ("Canon EOS C70 — 日本語", "Bobine é")


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"model": "iPhone 15 Pro", "make": "Apple"}, "Apple iPhone 15 Pro"),
        ({"com.apple.quicktime.model": "iPhone 15 Pro", "com.apple.quicktime.make": "Apple"}, "Apple iPhone 15 Pro"),
        ({"model": "SONY ILCE-7SM3", "make": "SONY"}, "SONY ILCE-7SM3"),
        ({"model": "Sony A7S III"}, "Sony A7S III"),
        ({"make": "Blackmagic"}, "Blackmagic"),
        ({"model": "  ", "make": ""}, ""),
    ],
)
def test_camera_combines_make_and_model_without_repeating_the_brand(tags, expected):
    assert extract_media_metadata(_payload([_video()], **tags)).camera == expected


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"creation_time": "2026-03-14T09:26:53.000000Z"}, "2026-03-14T09:26:53Z"),
        ({"creation_time": "2026-03-14T09:26:53.000000Z;2026-03-14T09:26:53.000000Z"}, "2026-03-14T09:26:53Z"),
        ({"creation_time": "2026-03-14T09:26:53.250000+02:00"}, "2026-03-14T09:26:53.250000+02:00"),
        ({"creation_time": "2026-03-14 09:26:53"}, "2026-03-14T09:26:53"),
        ({"creation_time": "08:15:30", "date": "2026-03-14"}, "2026-03-14T08:15:30"),
        ({"creation_time": "08:15:30", "date": "2026:03:14"}, "2026-03-14T08:15:30"),
        ({"creation_time": "08:15:30"}, ""),                         # une heure seule n'est pas une date de création
        ({"creation_time": "hier"}, ""), ({"creation_time": ""}, ""), ({}, ""),
    ],
)
def test_creation_time_is_a_readable_iso_date(tags, expected):
    assert extract_media_metadata(_payload([_audio()], **tags)).creation_time == expected


def test_creation_time_falls_back_to_the_streams():
    meta = extract_media_metadata(_payload([_video(creation_time="2026-03-14T09:26:53.000000Z")]))
    assert meta.creation_time == "2026-03-14T09:26:53Z"


# --- probe_media de bout en bout (ffprobe simulé) ------------------------------------------------------------------


def _probe_with(monkeypatch, tmp_path, payload, name="clip.mp4") -> MediaAsset:
    path = tmp_path / name
    path.write_bytes(b"\x00")
    monkeypatch.setattr("core.media_probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    completed = MagicMock(stdout=json.dumps(payload), stderr="", returncode=0)
    monkeypatch.setattr("core.media_probe.supervised_run", lambda *args, **kwargs: completed)
    return probe_media(str(path))


def test_probe_media_puts_the_metadata_on_a_video_asset(monkeypatch, tmp_path):
    payload = _payload(
        [_video("25/1", timecode="01:02:03:04"), _audio(), _tmcd(timecode="01:02:03:04", reel_name="A001")],
        model="Sony A7S III", creation_time="2026-03-14T09:26:53.000000Z",
    )
    asset = _probe_with(monkeypatch, tmp_path, payload)
    assert asset.media_type == "video"
    assert (asset.timecode, asset.reel, asset.camera, asset.creation_time) == (
        "01:02:03:04", "A001", "Sony A7S III", "2026-03-14T09:26:53Z")
    assert asset_start_seconds(asset) == pytest.approx(3600 + 2 * 60 + 3 + 4 / 25)


def test_probe_media_puts_the_time_reference_on_an_audio_asset(monkeypatch, tmp_path):
    asset = _probe_with(monkeypatch, tmp_path, _payload([_audio("48000")], time_reference="172800000"), name="r.wav")
    assert asset.media_type == "audio" and asset.time_reference == 3600.0
    assert asset_start_seconds(asset) == 3600.0


def test_probe_media_without_tags_leaves_the_asset_unchanged(monkeypatch, tmp_path):
    asset = _probe_with(monkeypatch, tmp_path, _payload([_video(), _audio()]))
    assert (asset.timecode, asset.timecode_fps, asset.time_reference, asset.reel, asset.camera,
            asset.creation_time) == ("", 0.0, None, "", "", "")
    assert asset_start_seconds(asset) is None


# --- Vrais fichiers FFmpeg -----------------------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="FFmpeg / ffprobe indisponibles")


def _ffmpeg(*arguments: str) -> None:
    completed = subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments], capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-500:]


def _clip(target, *, rate="25", timecode=None, audio=True, extra=(), codec=("-c:v", "libx264", "-pix_fmt", "yuv420p")):
    args = ["-f", "lavfi", "-i", f"testsrc=size=160x90:rate={rate}:d=1"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=d=1:r=48000", "-c:a", "aac"]
    args += list(codec)
    if timecode:
        args += ["-timecode", timecode]
    _ffmpeg(*args, *extra, str(target))
    return str(target)


@needs_ffmpeg
def test_a_real_mp4_with_a_timecode(tmp_path):
    asset = probe_media(_clip(tmp_path / "tc.mp4", timecode="01:02:03:04"))
    assert asset.timecode == "01:02:03:04" and asset.timecode_fps == 0.0
    assert asset_start_seconds(asset) == pytest.approx(3723.16)


@needs_ffmpeg
def test_a_real_drop_frame_mp4_keeps_the_semicolon_and_29_97(tmp_path):
    asset = probe_media(_clip(tmp_path / "df.mp4", rate="30000/1001", timecode="01:02:03;04"))
    assert asset.timecode == "01:02:03;04"
    assert asset.fps == pytest.approx(30000 / 1001)
    # 01:02:03;04 en drop-frame = (3 723 s × 30 + 4) − 2 × (62 − 6) = 111 582 images : 62 minutes écoulées, dont 6
    # dixièmes qui ne sautent rien
    assert asset_start_seconds(asset) == pytest.approx(111582 * 1001 / 30000)


@needs_ffmpeg
def test_a_real_23_976_and_59_94_clip(tmp_path):
    slow = probe_media(_clip(tmp_path / "f2398.mp4", rate="24000/1001", timecode="00:59:59:23"))
    assert slow.timecode == "00:59:59:23" and slow.fps == pytest.approx(24000 / 1001)
    assert asset_start_seconds(slow) == pytest.approx((59 * 60 * 24 + 59 * 24 + 23) * 1001 / 24000)
    fast = probe_media(_clip(tmp_path / "f5994.mp4", rate="60000/1001", timecode="00:01:00;04", audio=False))
    assert fast.timecode == "00:01:00;04" and fast.timecode_fps == 0.0


@needs_ffmpeg
def test_a_real_mp4_without_a_timecode_has_none(tmp_path):
    asset = probe_media(_clip(tmp_path / "plain.mp4"))
    assert asset.timecode == "" and asset_start_seconds(asset) is None


@needs_ffmpeg
def test_a_real_mov_with_reel_and_camera_model(tmp_path):
    path = _clip(tmp_path / "cam.mov", timecode="01:02:03:04",
                 extra=["-movflags", "use_metadata_tags", "-metadata:s:v:0", "reel_name=A001",
                        "-metadata", "model=Sony A7S III"])
    asset = probe_media(path)
    assert asset.timecode == "01:02:03:04"
    assert asset.camera == "Sony A7S III"
    assert asset.reel == "A001"


@needs_ffmpeg
def test_a_real_matroska_uppercase_timecode(tmp_path):
    asset = probe_media(_clip(tmp_path / "t.mkv", extra=["-metadata", "TIMECODE=00:00:10:00"]))
    assert asset.timecode == "00:00:10:00"
    assert asset_start_seconds(asset) == 10.0


@needs_ffmpeg
def test_a_real_mxf_timecode_is_in_the_container_tags(tmp_path):
    muxers = subprocess.run(["ffmpeg", "-v", "error", "-muxers"], capture_output=True, text=True, timeout=60).stdout
    if " mxf " not in muxers:
        pytest.skip("ce FFmpeg ne sait pas écrire de MXF")
    path = tmp_path / "t.mxf"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:d=1", "-f", "lavfi", "-i", "sine=d=1:r=48000",
            "-c:v", "mpeg2video", "-c:a", "pcm_s16le", "-timecode", "10:00:00:00", str(path))
    asset = probe_media(str(path))
    assert asset.timecode == "10:00:00:00"
    assert asset_start_seconds(asset) == 36000.0


@needs_ffmpeg
def test_a_real_creation_time_is_read_without_fraction(tmp_path):
    asset = probe_media(_clip(tmp_path / "ct.mp4", extra=["-metadata", "creation_time=2026-03-14T09:26:53Z"]))
    assert asset.creation_time == "2026-03-14T09:26:53Z"


def _bwf(target, time_reference=None, sample_rate=48000, extra=()):
    args = ["-f", "lavfi", "-i", f"sine=d=1:r={sample_rate}", "-c:a", "pcm_s24le", "-write_bext", "1"]
    if time_reference is not None:
        args += ["-metadata", f"time_reference={time_reference}"]
    _ffmpeg(*args, *extra, str(target))
    return str(target)


@needs_ffmpeg
def test_a_real_bwf_time_reference_in_seconds_from_samples(tmp_path):
    asset = probe_media(_bwf(tmp_path / "a.wav", 172800000))
    assert asset.media_type == "audio" and asset.time_reference == 3600.0
    other_rate = probe_media(_bwf(tmp_path / "b.wav", 158760000, sample_rate=44100))
    assert other_rate.time_reference == 3600.0
    assert asset_start_seconds(asset) == 3600.0


@needs_ffmpeg
def test_a_real_bwf_with_the_default_zero_time_reference_has_none(tmp_path):
    """FFmpeg écrit ``time_reference=0`` par défaut : ce n'est pas minuit, c'est « horloge non réglée »."""
    asset = probe_media(_bwf(tmp_path / "zero.wav"))
    assert asset.time_reference is None and asset_start_seconds(asset) is None


@needs_ffmpeg
def test_a_real_bwf_date_and_originator(tmp_path):
    asset = probe_media(_bwf(tmp_path / "d.wav", 172800000, extra=["-metadata", "date=2026-03-14"]))
    assert asset.time_reference == 3600.0
    assert asset.creation_time == ""                       # une date sans heure d'origine n'est pas une création lisible
