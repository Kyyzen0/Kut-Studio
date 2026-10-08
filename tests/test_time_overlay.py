"""Courbe de vitesse et badges sur le clip de la timeline : le calcul (pur) et le dessin (fenêtre offscreen)."""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.time_ops import add_speed_point
from core.time_remapping import FreezeFrameMode, TimeInterpolation, TimeRemapping
from core.timeline_view_model import build_clip_views
from ui.timeline_widgets.time_overlay import SAMPLES, badge_texts, curve_samples, level_of


def _view(remapping=None, *, ramp=False):
    asset = MediaAsset("a", "/m/a.mp4", "a", 60.0, 1920, 1080, 30.0, "video", True)
    clip = Clip("c1", "a", "V1", 0.0, 0.0, 10.0, time_remapping=remapping or TimeRemapping())
    project = Project("p", media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip])])
    if ramp:
        add_speed_point(project, "c1", 0.0, 1.0)
        add_speed_point(project, "c1", 4.0, 0.25)
    return build_clip_views(project)[0]


def test_the_vertical_scale_is_logarithmic_with_normal_speed_in_the_middle():
    assert level_of(1.0) == pytest.approx(0.5)
    assert level_of(10.0) == pytest.approx(1.0) and level_of(0.1) == pytest.approx(0.0)
    assert level_of(2.0) > level_of(1.0) > level_of(0.5)
    assert level_of(2.0) - level_of(1.0) == pytest.approx(level_of(1.0) - level_of(0.5))      # doubler = même hauteur que diviser par 2
    assert level_of(0.0) == 0.0 and level_of(-3.0) == 0.0                                      # un arrêt (ou un recul) touche le bas
    assert level_of(1000.0) == 1.0 and level_of(0.0001) == 0.0                                 # les bords bornent


def test_the_curve_is_sampled_from_start_to_end_and_matches_the_keyframes():
    view = _view(ramp=True)
    duration = view.end - view.start
    samples = curve_samples(view.speed_points, duration)
    assert len(samples) == SAMPLES and samples[0][0] == 0.0 and samples[-1][0] == 1.0
    assert samples[0][1] == pytest.approx(1.0)
    assert min(speed for _part, speed in samples) == pytest.approx(0.25, abs=0.01)
    assert [part for part, _speed in samples] == sorted(part for part, _speed in samples)


def test_a_clip_without_a_curve_has_no_curve_samples():
    assert curve_samples((), 10.0) == [] and curve_samples(_view(ramp=True).speed_points, 0.0) == []


def test_the_view_carries_the_speed_curve_points():
    assert _view().speed_points == () and len(_view(ramp=True).speed_points) == 2


@pytest.mark.parametrize(
    ("remapping", "ramp", "expected"),
    [
        (TimeRemapping(), False, []),
        (TimeRemapping(speed=0.5), False, ["0.5x"]),
        (TimeRemapping(reverse=True), False, ["R"]),
        (TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=2.0, freeze_duration=1.0), False, ["F"]),
        (TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING), False, ["0.5x", "MIX"]),
        (TimeRemapping(speed=0.25, interpolation=TimeInterpolation.OPTICAL_FLOW), False, ["0.2x", "FLUX"]),
        (TimeRemapping(interpolation=TimeInterpolation.OPTICAL_FLOW), False, ["FLUX"]),
    ],
)
def test_the_badges_tell_the_speed_the_direction_the_freeze_and_the_interpolation(remapping, ramp, expected):
    assert badge_texts(_view(remapping, ramp=ramp)) == expected


def test_a_clip_with_a_curve_shows_its_average_speed():
    texts = badge_texts(_view(ramp=True))
    assert len(texts) == 1 and texts[0].startswith("~") and texts[0].endswith("x")
    assert float(texts[0][1:-1]) < 1.0                                                          # une rampe vers 25 % : plus lent que 100 %


def test_a_frozen_clip_never_shows_an_interpolation_badge():
    frozen = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=2.0, freeze_duration=1.0,
                           interpolation=TimeInterpolation.OPTICAL_FLOW)
    assert badge_texts(_view(frozen)) == ["F"]


def _painted(widget):
    image = widget.grab().toImage()
    return bytes(image.constBits())


def test_the_curve_is_actually_painted_on_the_clip(qtbot):
    from ui.timeline_widgets.clip_widget import ClipWidget

    plain, curved = ClipWidget(_view()), ClipWidget(_view(ramp=True))
    for widget in (plain, curved):
        qtbot.addWidget(widget)
        widget.resize(400, 60)
    assert _painted(plain) != _painted(curved)


def test_the_overlay_painting_never_fails_on_degenerate_clips(qtbot):
    from ui.timeline_widgets.clip_widget import ClipWidget

    view = _view(ramp=True)
    for width in (1, 8, 3000):
        widget = ClipWidget(view)
        qtbot.addWidget(widget)
        widget.resize(width, 40)
        assert widget.grab().width() == width
    reverse = ClipWidget(_view(TimeRemapping(reverse=True, interpolation=TimeInterpolation.BLENDING), ramp=True))
    qtbot.addWidget(reverse)
    reverse.resize(300, 50)
    assert reverse.grab().height() == 50


def test_the_interpolation_badges_follow_the_interface_language():
    from ui import i18n

    view = _view(TimeRemapping(speed=0.5, interpolation=TimeInterpolation.OPTICAL_FLOW))
    previous = i18n.current_language()
    try:
        assert i18n.set_language("en") or i18n.current_language() == "en"
        assert badge_texts(view) == ["0.5x", "FLOW"]
        assert i18n.set_language("fr") or i18n.current_language() == "fr"
        assert badge_texts(view) == ["0.5x", "FLUX"]
    finally:
        i18n.set_language(previous)
