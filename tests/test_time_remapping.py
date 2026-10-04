"""Tests pour le module core/time_remapping.py."""

from __future__ import annotations

import pytest

from core.time_remapping import (
    DEFAULT_SPEED,
    MAX_REVERSE_DURATION_SECONDS,
    MAX_SPEED,
    MIN_SPEED,
    FreezeFrameMode,
    TimeRemapping,
    can_be_frozen,
    clamp_speed,
    compute_source_duration,
    compute_timeline_duration,
    create_freeze_frame,
    estimate_ffmpeg_memory_usage,
    get_ffmpeg_freeze_filter,
    get_ffmpeg_reverse_filter,
    get_ffmpeg_speed_filter,
    get_speed_from_preset,
    remove_freeze_frame,
    set_reverse,
    set_speed,
    source_to_timeline_time,
    timeline_to_source_time,
    toggle_reverse,
    validate_time_remapping,
)


# ---------------------------------------------------------------------------
# TimeRemapping dataclass
# ---------------------------------------------------------------------------


class TestTimeRemappingDefaults:
    def test_default_values(self):
        tr = TimeRemapping()
        assert tr.speed == 1.0
        assert tr.reverse is False
        assert tr.freeze_mode == FreezeFrameMode.NONE
        assert tr.freeze_source_time == 0.0
        assert tr.freeze_duration == 1.0

    def test_is_normal_default(self):
        tr = TimeRemapping()
        assert tr.is_normal is True

    def test_is_frozen_default(self):
        tr = TimeRemapping()
        assert tr.is_frozen is False

    def test_default_factory_method(self):
        tr = TimeRemapping.default()
        assert tr.speed == 1.0
        assert tr.reverse is False
        assert tr.freeze_mode == FreezeFrameMode.NONE


class TestTimeRemappingSpeedValidation:
    def test_valid_speed_0_1(self):
        tr = TimeRemapping(speed=0.1)
        assert tr.speed == 0.1

    def test_valid_speed_8_0(self):
        tr = TimeRemapping(speed=8.0)
        assert tr.speed == 8.0

    def test_valid_speed_1_0(self):
        tr = TimeRemapping(speed=1.0)
        assert tr.speed == 1.0

    def test_invalid_speed_too_low(self):
        with pytest.raises(ValueError) as exc_info:
            TimeRemapping(speed=0.04)
        assert "La vitesse doit être entre" in str(exc_info.value)

    def test_invalid_speed_too_high(self):
        with pytest.raises(ValueError) as exc_info:
            TimeRemapping(speed=10.5)
        assert "La vitesse doit être entre" in str(exc_info.value)

    def test_the_extreme_speeds_of_the_brief_are_valid(self):
        """5 % (ralenti extrême) et 1 000 % (accéléré extrême) sont des vitesses constantes valides."""
        assert TimeRemapping(speed=0.05).speed == 0.05
        assert TimeRemapping(speed=10.0).speed == 10.0

    def test_invalid_speed_negative(self):
        with pytest.raises(ValueError) as exc_info:
            TimeRemapping(speed=-1.0)
        assert "La vitesse doit être entre" in str(exc_info.value)


class TestTimeRemappingFreezeValidation:
    def test_valid_freeze(self):
        tr = TimeRemapping(
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=5.0,
            freeze_duration=2.0,
        )
        assert tr.is_frozen is True

    def test_invalid_freeze_duration_zero(self):
        with pytest.raises(ValueError) as exc_info:
            TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_duration=0.0)
        assert "durée d'un arrêt sur image doit être strictement positive" in str(
            exc_info.value
        )

    def test_invalid_freeze_duration_negative(self):
        with pytest.raises(ValueError) as exc_info:
            TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_duration=-1.0)
        assert "durée d'un arrêt sur image doit être strictement positive" in str(
            exc_info.value
        )


class TestTimeRemappingIsProperties:
    def test_is_normal_true(self):
        tr = TimeRemapping(speed=1.0, reverse=False, freeze_mode=FreezeFrameMode.NONE)
        assert tr.is_normal is True

    def test_is_normal_false_speed(self):
        tr = TimeRemapping(speed=2.0)
        assert tr.is_normal is False

    def test_is_normal_false_reverse(self):
        tr = TimeRemapping(reverse=True)
        assert tr.is_normal is False

    def test_is_normal_false_freeze(self):
        tr = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE)
        assert tr.is_normal is False

    def test_is_frozen_true(self):
        tr = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE)
        assert tr.is_frozen is True

    def test_is_frozen_false(self):
        tr = TimeRemapping()
        assert tr.is_frozen is False


# ---------------------------------------------------------------------------
# clamp_speed
# ---------------------------------------------------------------------------


class TestClampSpeed:
    def test_clamp_valid_speed(self):
        assert clamp_speed(1.0) == 1.0
        assert clamp_speed(0.5) == 0.5
        assert clamp_speed(2.0) == 2.0

    def test_clamp_min_speed(self):
        assert clamp_speed(0.05) == MIN_SPEED
        assert clamp_speed(0.0) == MIN_SPEED
        assert clamp_speed(-1.0) == MIN_SPEED

    def test_clamp_max_speed(self):
        assert clamp_speed(10.0) == MAX_SPEED
        assert clamp_speed(100.0) == MAX_SPEED

    def test_clamp_non_numeric(self):
        assert clamp_speed(None) == DEFAULT_SPEED
        assert clamp_speed("invalid") == DEFAULT_SPEED
        assert clamp_speed([1, 2]) == DEFAULT_SPEED

    def test_clamp_nan(self):
        assert clamp_speed(float("nan")) == DEFAULT_SPEED


# ---------------------------------------------------------------------------
# source_to_timeline_time
# ---------------------------------------------------------------------------


class TestSourceToTimelineTime:
    def test_normal_speed(self):
        # source_in=0, source_out=10, speed=1.0
        # source_time=5.0 -> timeline_time=5.0
        assert source_to_timeline_time(5.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.NONE, 0.0) == 5.0

    def test_double_speed(self):
        # source_in=0, source_out=10, speed=2.0
        # source_time=5.0 -> timeline_time=2.5 (le clip passe 2x plus vite)
        assert source_to_timeline_time(5.0, 0.0, 10.0, 2.0, False, FreezeFrameMode.NONE, 0.0) == 2.5

    def test_half_speed(self):
        # source_in=0, source_out=10, speed=0.5
        # source_time=5.0 -> timeline_time=10.0
        assert source_to_timeline_time(5.0, 0.0, 10.0, 0.5, False, FreezeFrameMode.NONE, 0.0) == 10.0

    def test_reverse(self):
        # source_in=0, source_out=10, speed=1.0, reverse=True
        # source_time=2.0 -> timeline_time=8.0 (inversé: 10-2=8)
        assert source_to_timeline_time(2.0, 0.0, 10.0, 1.0, True, FreezeFrameMode.NONE, 0.0) == 8.0

    def test_reverse_with_speed(self):
        # source_in=0, source_out=10, speed=2.0, reverse=True
        # source_time=2.0 -> relative=2, reversed=8, timeline=8/2=4
        assert source_to_timeline_time(2.0, 0.0, 10.0, 2.0, True, FreezeFrameMode.NONE, 0.0) == 4.0

    def test_freeze_frame(self):
        # En freeze, tout donne 0.0
        assert source_to_timeline_time(
            5.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.FREEZE, 5.0
        ) == 0.0

    def test_freeze_frame_wrong_source_time(self):
        with pytest.raises(ValueError) as exc_info:
            source_to_timeline_time(3.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.FREEZE, 5.0)
        assert "source_time doit être" in str(exc_info.value)

    def test_out_of_bounds_source_time(self):
        with pytest.raises(ValueError) as exc_info:
            source_to_timeline_time(-1.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.NONE, 0.0)
        assert "source_time" in str(exc_info.value)

    def test_source_in_not_zero(self):
        # source_in=5, source_out=15, speed=1.0
        # source_time=10.0 -> relative=5, timeline=5.0
        assert source_to_timeline_time(10.0, 5.0, 15.0, 1.0, False, FreezeFrameMode.NONE, 0.0) == 5.0


# ---------------------------------------------------------------------------
# timeline_to_source_time
# ---------------------------------------------------------------------------


class TestTimelineToSourceTime:
    def test_normal_speed(self):
        assert timeline_to_source_time(5.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.NONE, 0.0) == 5.0

    def test_double_speed(self):
        # timeline_time=5.0, speed=2.0 -> source_time=10.0
        assert timeline_to_source_time(5.0, 0.0, 10.0, 2.0, False, FreezeFrameMode.NONE, 0.0) == pytest.approx(10.0)

    def test_half_speed(self):
        # timeline_time=10.0, speed=0.5 -> source_time=5.0
        assert timeline_to_source_time(10.0, 0.0, 10.0, 0.5, False, FreezeFrameMode.NONE, 0.0) == pytest.approx(5.0)

    def test_reverse(self):
        # timeline_time=8.0, reverse=True -> relative=8, reversed=2, source=2.0
        assert timeline_to_source_time(8.0, 0.0, 10.0, 1.0, True, FreezeFrameMode.NONE, 0.0) == pytest.approx(2.0)

    def test_freeze_frame(self):
        # En freeze, tout donne freeze_source_time
        assert timeline_to_source_time(
            5.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.FREEZE, 7.5
        ) == 7.5

    def test_out_of_bounds_timeline_time(self):
        with pytest.raises(ValueError) as exc_info:
            timeline_to_source_time(20.0, 0.0, 10.0, 1.0, False, FreezeFrameMode.NONE, 0.0)
        assert "timeline_time" in str(exc_info.value)


# ---------------------------------------------------------------------------
# compute_timeline_duration
# ---------------------------------------------------------------------------


class TestComputeTimelineDuration:
    def test_normal_speed(self):
        assert compute_timeline_duration(0.0, 10.0, 1.0, FreezeFrameMode.NONE, 0.0) == 10.0

    def test_double_speed(self):
        # source_duration=10, speed=2 -> timeline_duration=5
        assert compute_timeline_duration(0.0, 10.0, 2.0, FreezeFrameMode.NONE, 0.0) == 5.0

    def test_half_speed(self):
        # source_duration=10, speed=0.5 -> timeline_duration=20
        assert compute_timeline_duration(0.0, 10.0, 0.5, FreezeFrameMode.NONE, 0.0) == 20.0

    def test_freeze_frame(self):
        # En freeze, la durée timeline = freeze_duration
        assert compute_timeline_duration(
            0.0, 10.0, 1.0, FreezeFrameMode.FREEZE, 5.0
        ) == 5.0
        assert compute_timeline_duration(
            0.0, 10.0, 2.0, FreezeFrameMode.FREEZE, 3.0
        ) == 3.0


# ---------------------------------------------------------------------------
# validate_time_remapping
# ---------------------------------------------------------------------------


class TestValidateTimeRemapping:
    def test_valid_normal(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=False,
            freeze_mode=FreezeFrameMode.NONE,
            freeze_source_time=0.0,
            freeze_duration=1.0,
            source_in=0.0,
            source_out=10.0,
            media_type="video",
        )
        assert errors == []

    def test_valid_freeze(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=False,
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=5.0,
            freeze_duration=2.0,
            source_in=0.0,
            source_out=10.0,
            media_type="video",
        )
        assert errors == []

    def test_invalid_speed(self):
        errors = validate_time_remapping(
            speed=0.04,
            reverse=False,
            freeze_mode=FreezeFrameMode.NONE,
            freeze_source_time=0.0,
            freeze_duration=1.0,
            source_in=0.0,
            source_out=10.0,
            media_type="video",
        )
        assert len(errors) == 1
        assert "vitesse doit être entre" in errors[0]

    def test_freeze_duration_zero(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=False,
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=5.0,
            freeze_duration=0.0,
            source_in=0.0,
            source_out=10.0,
            media_type="video",
        )
        assert len(errors) == 1
        assert "durée d'un arrêt sur image doit être strictement positive" in errors[0]

    def test_freeze_source_time_out_of_bounds(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=False,
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=15.0,
            freeze_duration=2.0,
            source_in=0.0,
            source_out=10.0,
            media_type="video",
        )
        assert len(errors) == 1
        assert "doit être dans" in errors[0]

    def test_audio_cannot_be_frozen(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=False,
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=5.0,
            freeze_duration=2.0,
            source_in=0.0,
            source_out=10.0,
            media_type="audio",
        )
        assert len(errors) == 1
        assert "audio ne peuvent pas être en mode arrêt sur image" in errors[0]

    def test_reverse_too_long(self):
        errors = validate_time_remapping(
            speed=1.0,
            reverse=True,
            freeze_mode=FreezeFrameMode.NONE,
            freeze_source_time=0.0,
            freeze_duration=1.0,
            source_in=0.0,
            source_out=7201.0,  # > MAX_REVERSE_DURATION_SECONDS
            media_type="video",
        )
        assert len(errors) == 1
        assert "dépasse la limite" in errors[0]

    def test_multiple_errors(self):
        errors = validate_time_remapping(
            speed=0.05,
            reverse=True,
            freeze_mode=FreezeFrameMode.FREEZE,
            freeze_source_time=15.0,
            freeze_duration=0.0,
            source_in=0.0,
            source_out=10.0,
            media_type="audio",
        )
        assert len(errors) >= 3


# ---------------------------------------------------------------------------
# create_freeze_frame / remove_freeze_frame
# ---------------------------------------------------------------------------


class TestCreateFreezeFrame:
    def test_default_freeze_source_time(self):
        tr = create_freeze_frame(source_in=0.0, source_out=10.0, freeze_duration=2.0)
        assert tr.freeze_mode == FreezeFrameMode.FREEZE
        assert tr.freeze_source_time == 5.0  # Milieu du clip
        assert tr.freeze_duration == 2.0

    def test_custom_freeze_source_time(self):
        tr = create_freeze_frame(
            source_in=0.0, source_out=10.0, freeze_source_time=3.0, freeze_duration=2.0
        )
        assert tr.freeze_source_time == 3.0

    def test_invalid_freeze_duration(self):
        with pytest.raises(ValueError) as exc_info:
            create_freeze_frame(source_in=0.0, source_out=10.0, freeze_duration=0.0)
        assert "durée d'un arrêt sur image doit être strictement positive" in str(
            exc_info.value
        )

    def test_freeze_source_time_out_of_bounds(self):
        with pytest.raises(ValueError) as exc_info:
            create_freeze_frame(
                source_in=0.0, source_out=10.0, freeze_source_time=15.0, freeze_duration=2.0
            )
        assert "doit être dans" in str(exc_info.value)


class TestRemoveFreezeFrame:
    def test_remove_freeze_frame(self):
        tr = remove_freeze_frame()
        assert tr.freeze_mode == FreezeFrameMode.NONE
        assert tr.speed == 1.0
        assert tr.reverse is False


# ---------------------------------------------------------------------------
# set_speed / set_reverse / toggle_reverse
# ---------------------------------------------------------------------------


class TestSetSpeed:
    def test_set_valid_speed(self):
        tr = set_speed(2.0)
        assert tr.speed == 2.0
        assert tr.reverse is False

    def test_set_clamped_speed(self):
        tr = set_speed(10.0)
        assert tr.speed == MAX_SPEED


class TestSetReverse:
    def test_set_reverse_true(self):
        tr = set_reverse(True)
        assert tr.reverse is True
        assert tr.speed == 1.0

    def test_set_reverse_false(self):
        tr = set_reverse(False)
        assert tr.reverse is False


class TestToggleReverse:
    def test_toggle_false_to_true(self):
        tr = TimeRemapping(reverse=False)
        new_tr = toggle_reverse(tr)
        assert new_tr.reverse is True
        assert new_tr.speed == tr.speed

    def test_toggle_true_to_false(self):
        tr = TimeRemapping(reverse=True)
        new_tr = toggle_reverse(tr)
        assert new_tr.reverse is False


# ---------------------------------------------------------------------------
# compute_source_duration
# ---------------------------------------------------------------------------


class TestComputeSourceDuration:
    def test_normal(self):
        assert compute_source_duration(0.0, 10.0, 1.0, FreezeFrameMode.NONE) == 10.0

    def test_with_speed(self):
        # timeline_duration=10, speed=2 -> source_duration=20
        assert compute_source_duration(0.0, 10.0, 2.0, FreezeFrameMode.NONE) == pytest.approx(20.0)

    def test_freeze(self):
        # En freeze, source_duration=0
        assert compute_source_duration(0.0, 10.0, 1.0, FreezeFrameMode.FREEZE) == 0.0


# ---------------------------------------------------------------------------
# can_be_frozen
# ---------------------------------------------------------------------------


class TestCanBeFrozen:
    def test_video_can_be_frozen(self):
        assert can_be_frozen("video") is True

    def test_audio_cannot_be_frozen(self):
        assert can_be_frozen("audio") is False

    def test_image_can_be_frozen(self):
        assert can_be_frozen("image") is True

    def test_subtitle_cannot_be_frozen(self):
        assert can_be_frozen("subtitle") is False


# ---------------------------------------------------------------------------
# get_speed_from_preset
# ---------------------------------------------------------------------------


class TestGetSpeedFromPreset:
    def test_all_presets(self):
        assert get_speed_from_preset("0.25x") == 0.25
        assert get_speed_from_preset("0.5x") == 0.5
        assert get_speed_from_preset("1x") == 1.0
        assert get_speed_from_preset("2x") == 2.0
        assert get_speed_from_preset("4x") == 4.0

    def test_unknown_preset(self):
        with pytest.raises(ValueError) as exc_info:
            get_speed_from_preset("8x")
        assert "Preset inconnu" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_ffmpeg_speed_filter
# ---------------------------------------------------------------------------


class TestGetFfmpegSpeedFilter:
    def test_speed_1x(self):
        assert get_ffmpeg_speed_filter(1.0) == []

    def test_speed_2x(self):
        assert get_ffmpeg_speed_filter(2.0) == ["atempo=2.0"]

    def test_speed_0_5x(self):
        assert get_ffmpeg_speed_filter(0.5) == ["atempo=0.5"]

    def test_speed_0_25x(self):
        # 0.25 = 0.5 * 0.5, donc deux filtres atempo=0.5
        filters = get_ffmpeg_speed_filter(0.25)
        assert len(filters) == 2
        assert all(f == "atempo=0.5" for f in filters)

    def test_speed_4x(self):
        # 4.0 = 2.0 * 2.0, donc deux filtres atempo=2.0
        filters = get_ffmpeg_speed_filter(4.0)
        assert len(filters) == 2
        assert all(f == "atempo=2.0" for f in filters)

    def test_speed_8x(self):
        # 8.0 = 2.0 * 2.0 * 2.0
        filters = get_ffmpeg_speed_filter(8.0)
        assert len(filters) == 3
        assert all(f == "atempo=2.0" for f in filters)

    def test_speed_0_1x(self):
        # 0.1 = 0.5 * 0.5 * 0.5 * 0.8 (environ)
        filters = get_ffmpeg_speed_filter(0.1)
        assert len(filters) >= 1


# ---------------------------------------------------------------------------
# get_ffmpeg_reverse_filter
# ---------------------------------------------------------------------------


class TestGetFfmpegReverseFilter:
    def test_video_only(self):
        filters = get_ffmpeg_reverse_filter(has_audio=False, has_video=True)
        assert filters == ["reverse"]

    def test_audio_only(self):
        filters = get_ffmpeg_reverse_filter(has_audio=True, has_video=False)
        assert filters == ["areverse"]

    def test_both(self):
        filters = get_ffmpeg_reverse_filter(has_audio=True, has_video=True)
        assert filters == ["reverse", "areverse"]

    def test_neither(self):
        filters = get_ffmpeg_reverse_filter(has_audio=False, has_video=False)
        assert filters == []


# ---------------------------------------------------------------------------
# get_ffmpeg_freeze_filter
# ---------------------------------------------------------------------------


class TestGetFfmpegFreezeFilter:
    def test_freeze_filter(self):
        filters = get_ffmpeg_freeze_filter(freeze_source_time=5.0, asset_fps=30.0)
        # Image 150 (5,0 × 30) : une demi-image avant, pour ne jamais retenir sa voisine.
        assert filters == ["trim=start=4.983333", "setpts=PTS-STARTPTS", "trim=end_frame=1"]

    def test_the_instant_is_counted_from_the_clip_in_point(self):
        filters = get_ffmpeg_freeze_filter(freeze_source_time=5.0, asset_fps=25.0, source_in=2.0)
        assert filters[0] == "trim=start=2.980000"          # 3,0 s depuis l'entrée = image 75

    def test_the_first_frame_and_a_zero_fps_are_safe(self):
        assert get_ffmpeg_freeze_filter(0.0, 30.0)[0] == "trim=start=0.000000"
        assert get_ffmpeg_freeze_filter(1.0, 0.0)[0] == "trim=start=0.983333"      # 30 i/s par défaut

    def test_no_filter_contains_a_comma(self):
        """Une virgule interne casse un filter_complex (« No such filter: '75)' »)."""
        assert not any("," in item for item in get_ffmpeg_freeze_filter(3.0, 25.0))


# ---------------------------------------------------------------------------
# estimate_ffmpeg_memory_usage
# ---------------------------------------------------------------------------


class TestEstimateFfmpegMemoryUsage:
    def test_no_media(self):
        assert estimate_ffmpeg_memory_usage(
            source_duration=100.0, has_audio=False, has_video=False, width=1920, height=1080, fps=30.0
        ) == 0

    def test_video_only(self):
        usage = estimate_ffmpeg_memory_usage(
            source_duration=10.0, has_audio=False, has_video=True, width=1920, height=1080, fps=30.0
        )
        assert usage > 0

    def test_audio_only(self):
        usage = estimate_ffmpeg_memory_usage(
            source_duration=10.0, has_audio=True, has_video=False, width=0, height=0, fps=0.0
        )
        assert usage > 0
