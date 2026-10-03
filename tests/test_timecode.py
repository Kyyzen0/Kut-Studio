"""``core.timecode`` : la seule abstraction du timecode SMPTE (cadences fractionnaires et *drop-frame* compris).

Le contrôle d'ensemble compare le module à un **générateur de timecode indépendant** (un compteur qui avance image par
image et saute les étiquettes comme le fait une caméra), sur plusieurs heures de chaque cadence : les mathématiques du
module ne sont donc pas vérifiées par elles-mêmes.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from core.project_model import MediaAsset
from core.timecode import (
    SUPPORTED_FRAME_RATES,
    FrameRate,
    InvalidTimecode,
    Timecode,
    UnsupportedFrameRate,
    asset_start_seconds,
    format_fps,
    normalize_timecode_text,
    seconds_between,
)

NDF_2997 = FrameRate.from_fps(29.97)
DF_2997 = FrameRate.from_fps(29.97, drop_frame=True)
NDF_5994 = FrameRate.from_fps(59.94)
DF_5994 = FrameRate.from_fps(59.94, drop_frame=True)
NDF_2398 = FrameRate.from_fps(23.976)
NDF_25 = FrameRate.from_fps(25)


def _generator(rate: FrameRate, count: int):
    """Compteur SMPTE de référence : ``(h, m, s, f)`` de chaque image, une à une, depuis ``00:00:00:00``."""
    hours = minutes = seconds = frames = 0
    drop = rate.drop_per_minute
    for _ in range(count):
        yield hours, minutes, seconds, frames
        frames += 1
        if frames == rate.nominal:
            frames = 0
            seconds += 1
            if seconds == 60:
                seconds = 0
                minutes += 1
                if minutes == 60:
                    minutes = 0
                    hours += 1
                if rate.drop_frame and minutes % 10 != 0:
                    frames = drop                         # les étiquettes 0..drop-1 n'existent pas cette minute


# --- Cadences -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (23.976, Fraction(24000, 1001)), (23.98, Fraction(24000, 1001)), (24000 / 1001, Fraction(24000, 1001)),
        (24, Fraction(24)), (24.0, Fraction(24)), (25, Fraction(25)), (25.0, Fraction(25)),
        (29.97, Fraction(30000, 1001)), (29.970029970029973, Fraction(30000, 1001)),
        (Fraction(30000, 1001), Fraction(30000, 1001)), (30, Fraction(30)), (30.0, Fraction(30)),
        (50.0, Fraction(50)), (59.94, Fraction(60000, 1001)), (59.94005994, Fraction(60000, 1001)),
        (60, Fraction(60)), (60.0, Fraction(60)),
    ],
)
def test_a_tolerant_float_snaps_to_the_exact_rational(value, expected):
    assert FrameRate.from_fps(value).fps == expected


def test_29_97_is_never_truncated_to_29_nor_rounded_to_30():
    """L'ancien défaut du dépôt : 29,97 lu comme 29 (ou 30) faisait dériver chaque calcul de timecode."""
    rate = FrameRate.from_fps(29.97)
    assert rate.fps == Fraction(30000, 1001) and rate.nominal == 30
    assert rate.fps != 30 and rate.fps != 29
    assert FrameRate.from_fps(30.0).fps == 30
    assert FrameRate.from_fps(23.976).fps != 24


@pytest.mark.parametrize("value", [0, 0.0, -25.0, 29.0, 29.9, 31.0, 47.952, 100.0, math.nan, math.inf, -math.inf, True, "25", None])
def test_an_unknown_rate_is_refused_or_none_never_rounded(value):
    with pytest.raises(UnsupportedFrameRate):
        FrameRate.from_fps(value)                         # type: ignore[arg-type]
    assert FrameRate.try_from_fps(value) is None          # type: ignore[arg-type]


@pytest.mark.parametrize("fps", [23.976, 24, 25, 30, 50, 60])
def test_drop_frame_only_exists_at_29_97_and_59_94(fps):
    with pytest.raises(UnsupportedFrameRate):
        FrameRate.from_fps(fps, drop_frame=True)
    assert FrameRate.try_from_fps(fps, drop_frame=True) is None


def test_the_supported_rates_are_exactly_the_documented_ten():
    assert [rate.label for rate in SUPPORTED_FRAME_RATES] == [
        "23.976", "24", "25", "29.97", "29.97 DF", "30", "50", "59.94", "59.94 DF", "60",
    ]
    assert [rate.nominal for rate in SUPPORTED_FRAME_RATES] == [24, 24, 25, 30, 30, 30, 50, 60, 60, 60]
    assert DF_2997.drop_per_minute == 2 and DF_5994.drop_per_minute == 4 and NDF_2997.drop_per_minute == 0


def test_a_frame_rate_value_that_is_not_supported_cannot_be_built_directly():
    with pytest.raises(UnsupportedFrameRate):
        FrameRate(Fraction(29))
    with pytest.raises(UnsupportedFrameRate):
        FrameRate(Fraction(25), drop_frame=True)


@pytest.mark.parametrize(
    "fps, decimal, expected",
    [(29.97, ".", "29.97"), (30000 / 1001, ",", "29,97"), (23.976023976, ".", "23.976"), (25.0, ".", "25"),
     (59.94, ",", "59,94"), (30, ".", "30"), (47.952, ".", "47.952"), (12.5, ",", "12,5"),
     (0.0, ".", ""), (-3.0, ".", ""), (math.nan, ".", ""), (True, ".", "")],
)
def test_fps_is_shown_without_truncation(fps, decimal, expected):
    assert format_fps(fps, decimal) == expected


# --- Référence : générateur indépendant ------------------------------------------------------------------------------


@pytest.mark.parametrize("rate", SUPPORTED_FRAME_RATES, ids=lambda rate: rate.label)
def test_every_frame_of_several_hours_round_trips_against_an_independent_counter(rate):
    """Étiquette ↔ numéro d'image ↔ secondes, image par image, avec franchissement de chaque minute et de l'heure."""
    count = 2 * 3600 * rate.nominal + 11 * 60 * rate.nominal             # un peu plus de 2 h : minutes 9→10, 59→0, 1 h
    for index, (hours, minutes, seconds, frames) in enumerate(_generator(rate, count)):
        timecode = Timecode(hours, minutes, seconds, frames, rate)
        assert timecode.to_frames() == index
        assert Timecode.from_frames(index, rate) == timecode
        assert Timecode.from_seconds(timecode.to_seconds(), rate) == timecode


@pytest.mark.parametrize("rate", [DF_2997, DF_5994], ids=lambda rate: rate.label)
def test_drop_frame_matches_the_counter_across_the_whole_day_at_every_minute_boundary(rate):
    """Les 24 h : on teste les images autour de chaque début de minute (là où le saut se produit) sans tout énumérer."""
    per_minute = rate.nominal * 60 - rate.drop_per_minute
    for minute in range(0, 24 * 60, 1):
        label_frames = Timecode(minute // 60, minute % 60, 0, 0 if minute % 10 == 0 else rate.drop_per_minute, rate)
        index = label_frames.to_frames()
        assert Timecode.from_frames(index, rate) == label_frames
        if index:
            previous = Timecode.from_frames(index - 1, rate)
            assert (previous.seconds, previous.frames) == (59, rate.nominal - 1)   # la minute précédente finit pleine
        assert index == sum(per_minute if m % 10 else per_minute + rate.drop_per_minute for m in range(minute))


# --- Valeurs de référence ---------------------------------------------------------------------------------------------


def test_one_hour_of_drop_frame_is_107892_frames_and_3599_9964_seconds():
    timecode = Timecode.parse("01:00:00;00", DF_2997)
    assert timecode.to_frames() == 107892
    assert timecode.to_seconds() == pytest.approx(3599.9964, abs=1e-9)
    assert timecode.to_seconds_exact() == Fraction(107892 * 1001, 30000)


def test_ten_minutes_of_drop_frame_is_17982_frames():
    assert Timecode.parse("00:10:00;00", DF_2997).to_frames() == 17982
    assert Timecode.parse("00:01:00;02", DF_2997).to_frames() == 1800          # 1re étiquette existante de la minute 1
    assert Timecode.parse("00:00:59;29", DF_2997).to_frames() == 1799


def test_drop_frame_has_the_documented_boundaries():
    assert str(Timecode.from_frames(1799, DF_2997)) == "00:00:59;29"
    assert str(Timecode.from_frames(1800, DF_2997)) == "00:01:00;02"             # 00 et 01 sautées
    assert str(Timecode.from_frames(17981, DF_2997)) == "00:09:59;29"
    assert str(Timecode.from_frames(17982, DF_2997)) == "00:10:00;00"            # la dixième minute ne saute rien
    assert str(Timecode.from_frames(107892 - 1, DF_2997)) == "00:59:59;29"
    assert str(Timecode.from_frames(107892, DF_2997)) == "01:00:00;00"


def test_non_drop_29_97_runs_slower_than_the_wall_clock_by_the_expected_amount():
    """Sans saut, l'étiquette 01:00:00:00 est atteinte après 3 603,6 s écoulées (108 000 images à 29,97)."""
    assert Timecode.parse("01:00:00:00", NDF_2997).to_seconds() == pytest.approx(3603.6)
    assert Timecode.parse("01:00:00:00", FrameRate.from_fps(30)).to_seconds() == 3600.0


def test_23_976_counts_24_labels_per_second_at_a_slower_pace():
    assert NDF_2398.nominal == 24
    assert Timecode.parse("00:00:01:00", NDF_2398).to_frames() == 24
    assert Timecode.parse("01:00:00:00", NDF_2398).to_seconds() == pytest.approx(3603.6)
    with pytest.raises(InvalidTimecode):
        Timecode.parse("00:00:00:24", NDF_2398)                                  # 0..23 seulement


def test_59_94_drop_frame_skips_four_labels_per_minute():
    assert Timecode.parse("00:10:00;00", DF_5994).to_frames() == 35964
    assert Timecode.parse("01:00:00;00", DF_5994).to_frames() == 215784
    assert str(Timecode.from_frames(3599, DF_5994)) == "00:00:59;59"
    assert str(Timecode.from_frames(3600, DF_5994)) == "00:01:00;04"             # 00 à 03 sautées
    with pytest.raises(InvalidTimecode):
        Timecode.parse("00:01:00;03", DF_5994)
    assert Timecode.parse("00:01:00;04", DF_5994).to_frames() == 3600


def test_a_skipped_drop_frame_label_does_not_exist():
    for text in ("00:01:00;00", "00:01:00;01", "00:19:00;01", "01:59:00;00"):
        with pytest.raises(InvalidTimecode, match="drop-frame"):
            Timecode.parse(text, DF_2997)
    assert Timecode.parse("00:10:00;00", DF_2997)                                 # dixième minute : existe
    assert Timecode.parse("00:01:01;00", DF_2997)                                 # seconde 1 : existe
    assert Timecode.parse("00:01:00;02", DF_2997)


# --- Texte ------------------------------------------------------------------------------------------------------------


def test_parse_and_format_use_the_drop_frame_separator():
    assert str(Timecode.parse("01:02:03:04", NDF_2997)) == "01:02:03:04"
    assert str(Timecode.parse("01:02:03;04", NDF_2997)) == "01:02:03;04"          # le « ; » rend la numérotation DF
    assert Timecode.parse("01:02:03;04", NDF_2997).rate == DF_2997
    assert Timecode.parse("01:02:03.04", NDF_2997).rate == DF_2997                # « . » : variante ffprobe
    assert str(Timecode.parse("01:02:03.04", 29.97)) == "01:02:03;04"
    assert Timecode.parse("01:02:03:04", DF_2997).rate == DF_2997                 # un taux DF explicite reste DF
    assert Timecode.parse("00:01:00:05", DF_2997).to_frames() == 1803


def test_a_drop_frame_separator_at_a_rate_without_drop_frame_is_read_as_non_drop():
    timecode = Timecode.parse("01:02:03;04", NDF_25)
    assert timecode.rate == NDF_25 and str(timecode) == "01:02:03:04"


@pytest.mark.parametrize("text", ["  01:02:03:04", "01:02:03:04  ", "\t01:02:03:04\n", " 01:02:03;04 "])
def test_parse_is_tolerant_on_surrounding_whitespace(text):
    assert Timecode.parse(text, 29.97).to_frames() == Timecode.parse(text.strip(), 29.97).to_frames()


@pytest.mark.parametrize(
    "text",
    ["", "01:02:03", "01:02:03:04:05", "01-02-03-04", "01:02:03,04", "ab:cd:ef:gh", "01 :02:03:04", "01:02: 03:04",
     "1:2:3:4:", "001:02:03:04", "01:02:03:0004", "-1:02:03:04", "01:02:03:", ":02:03:04", "01.02.03.04.05"],
)
def test_parse_is_strict_on_structure(text):
    with pytest.raises(InvalidTimecode):
        Timecode.parse(text, 25)
    assert Timecode.try_parse(text, 25) is None


@pytest.mark.parametrize("text", ["24:00:00:00", "00:60:00:00", "00:00:60:00", "00:00:00:25", "99:99:99:99"])
def test_out_of_range_labels_are_refused(text):
    with pytest.raises(InvalidTimecode):
        Timecode.parse(text, 25)


def test_try_parse_never_raises():
    assert Timecode.try_parse(None, 25) is None
    assert Timecode.try_parse(12, 25) is None
    assert Timecode.try_parse("01:02:03:04", None) is None
    assert Timecode.try_parse("01:02:03:04", 29.0) is None                         # cadence inconnue
    assert Timecode.try_parse("01:02:03:04", "25") is None                          # type: ignore[arg-type]
    assert Timecode.try_parse("01:02:03:04", 25) is not None


def test_a_timecode_value_rejects_impossible_fields():
    with pytest.raises(InvalidTimecode):
        Timecode(24, 0, 0, 0, NDF_25)
    with pytest.raises(InvalidTimecode):
        Timecode(0, 0, 0, 25, NDF_25)
    with pytest.raises(InvalidTimecode):
        Timecode(0, 0, 0, -1, NDF_25)
    with pytest.raises(InvalidTimecode):
        Timecode(0, 0, 0, 1.0, NDF_25)                     # type: ignore[arg-type]
    with pytest.raises(InvalidTimecode):
        Timecode(0, 0, 0, True, NDF_25)                    # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("01:02:03:04", "01:02:03:04"), ("01:02:03;04", "01:02:03;04"), ("01:02:03.04", "01:02:03;04"),
        ("1:2:3:4", "01:02:03:04"), ("  10:00:00:00 ", "10:00:00:00"), ("23:59:59;29", "23:59:59;29"),
        ("00:00:00:100", "00:00:00:100"),
        ("24:00:00:00", None), ("00:60:00:00", None), ("00:00:60:00", None), ("garbage", None), ("", None),
        ("01:02:03", None), (None, None), (1234, None), (b"01:02:03:04", None), ("٠١:٠٢:٠٣:٠٤", None),
    ],
)
def test_normalize_timecode_text(text, expected):
    assert normalize_timecode_text(text) == expected


# --- Secondes, images, 24 h -------------------------------------------------------------------------------------------


def test_seconds_between_is_exact_signed_and_cross_rate():
    first, second = Timecode.parse("01:00:00:00", 25), Timecode.parse("01:00:10:12", 25)
    assert seconds_between(first, second) == pytest.approx(10.48)
    assert seconds_between(second, first) == pytest.approx(-10.48)
    drop_a, drop_b = Timecode.parse("00:00:00;00", DF_2997), Timecode.parse("00:10:00;00", DF_2997)
    assert seconds_between(drop_a, drop_b) == pytest.approx(17982 * 1001 / 30000)
    mixed = seconds_between(Timecode.parse("00:00:01:00", 25), Timecode.parse("00:00:01:15", 30))
    assert mixed == pytest.approx(0.5)                                              # 1 s + 15/30 vs 1 s


def test_labels_wrap_at_midnight_but_seconds_between_does_not():
    last = Timecode.parse("23:59:59:24", 25)
    assert last.to_frames() == 25 * 86400 - 1
    assert str(Timecode.from_frames(25 * 86400, NDF_25)) == "00:00:00:00"
    assert str(Timecode.from_frames(-1, NDF_25)) == "23:59:59:24"
    assert str(Timecode.from_seconds(86400.0 + 1.0, NDF_25)) == "00:00:01:00"
    assert str(Timecode.from_frames(DF_2997.frames_per_day, DF_2997)) == "00:00:00;00"
    assert DF_2997.frames_per_day == 2589408 and DF_5994.frames_per_day == 5178816
    midnight = Timecode.parse("00:00:01:00", 25)
    assert seconds_between(last, midnight) == pytest.approx(-86398.96)              # pas de repli : à gérer par l'appelant


def test_from_seconds_takes_the_frame_that_contains_the_instant():
    assert str(Timecode.from_seconds(1.0, NDF_25)) == "00:00:01:00"
    assert str(Timecode.from_seconds(1.039, NDF_25)) == "00:00:01:00"
    assert str(Timecode.from_seconds(1.04, NDF_25)) == "00:00:01:01"
    assert str(Timecode.from_seconds(Fraction(3600), DF_2997)) == "01:00:00;00"      # 3600 s = 107 892,1 images


# --- Départ d'un média ---------------------------------------------------------------------------------------------


def _asset(**fields) -> MediaAsset:
    base = {"id": "a", "path": "/m/a.mp4", "name": "a", "duration": 10.0, "width": 1920, "height": 1080,
            "fps": 25.0, "media_type": "video", "has_audio": True}
    base.update(fields)
    return MediaAsset(**base)


def test_the_start_of_a_video_comes_from_its_timecode_at_its_own_rate():
    assert asset_start_seconds(_asset(timecode="01:00:00:00")) == 3600.0
    assert asset_start_seconds(_asset(timecode="01:00:00;00", fps=30000 / 1001)) == pytest.approx(3599.9964)
    assert asset_start_seconds(_asset(timecode="00:00:01:12", fps=24000 / 1001)) == pytest.approx(36 * 1001 / 24000)


def test_a_timecode_track_with_its_own_rate_wins_over_the_video_rate():
    """Vidéo 59,94 dont le timecode compte à 29,97 : les images 0 à 29 ne sont pas des 1/60 s."""
    video = _asset(timecode="00:00:01:15", fps=60000 / 1001, timecode_fps=30000 / 1001)
    assert asset_start_seconds(video) == pytest.approx(45 * 1001 / 30000)
    assert asset_start_seconds(_asset(timecode="00:00:01:15", fps=60000 / 1001)) == pytest.approx(75 * 1001 / 60000)


def test_the_start_of_a_bwf_recording_is_its_time_reference():
    audio = MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True, time_reference=3600.5)
    assert asset_start_seconds(audio) == 3600.5
    midnight = MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True, time_reference=0.0)
    assert asset_start_seconds(midnight) == 0.0               # minuit est un départ, pas une absence


def test_timecode_synchronisation_offsets_are_start_minus_the_earliest_start():
    camera_1 = _asset(timecode="10:00:00:00")
    camera_2 = _asset(timecode="10:00:02:12")
    recorder = MediaAsset("r", "/m/r.wav", "r", 60.0, 0, 0, 0.0, "audio", True, time_reference=36001.0)
    starts = [asset_start_seconds(item) for item in (camera_1, camera_2, recorder)]
    assert all(start is not None for start in starts)
    earliest = min(start for start in starts if start is not None)
    assert [round(start - earliest, 4) for start in starts if start is not None] == [0.0, 2.48, 1.0]


@pytest.mark.parametrize(
    "fields",
    [
        {},                                                                       # aucun timecode
        {"timecode": "garbage"}, {"timecode": "24:00:00:00"}, {"timecode": "00:00:00:30"},
        {"timecode": "00:01:00;00", "fps": 30000 / 1001},                         # étiquette sautée
        {"timecode": "01:00:00:00", "fps": 29.0},                                 # cadence inconnue
        {"timecode": "01:00:00:00", "fps": 31.0},
    ],
)
def test_a_media_without_a_usable_timecode_returns_none_and_never_raises(fields):
    assert asset_start_seconds(_asset(**fields)) is None


def test_an_audio_file_with_a_timecode_string_but_no_rate_is_not_interpreted():
    audio = MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True, timecode="01:00:00:00")
    assert asset_start_seconds(audio) is None
    assert asset_start_seconds(MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True,
                                          timecode="01:00:00:00", timecode_fps=25.0)) == 3600.0


def test_an_unreadable_timecode_falls_back_to_the_time_reference_then_none():
    audio = MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True, timecode="garbage", time_reference=12.0)
    assert asset_start_seconds(audio) == 12.0
    out_of_day = MediaAsset("w", "/m/w.wav", "w", 60.0, 0, 0, 0.0, "audio", True, time_reference=86400.0)
    assert asset_start_seconds(out_of_day) is None                                 # un BWF ne dépasse pas 24 h
