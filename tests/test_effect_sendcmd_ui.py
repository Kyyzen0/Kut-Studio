"""Étape 5 de l'ADR-0008 : losanges du flou, du rayon de la lueur et de l'aberration chromatique (hors écran).

Mêmes gestes que la vignette (``tests/test_effect_keyframes_ui.py``) : le losange pose ou retire une image clé, le curseur écrit
l'image clé quand le réglage est animé et sa base sinon. La netteté, le seuil et le gain de la lueur n'ont aucun losange.
"""

from __future__ import annotations

import pytest

from tests.test_effect_keyframes_ui import _frames, _prepared, _rows, _select, _spin
from tests.test_inspector_lock_guards import window  # noqa: F401 - fixture commune

CASES = [("bl", "intensity", 50.0), ("gl", "radius", 100.0), ("ca", "intensity", 40.0)]


@pytest.mark.parametrize("effect_id, name, maximum", CASES)
def test_the_diamond_and_the_spin_write_keyframes_for_the_new_parameter(
    window, effect_id, name, maximum
):
    _track, clip = _prepared(window)
    _select(window, effect_id)
    window.seek_to_position(1.0)
    _spin(window, name).setValue(6.0)  # sans image clé : la base
    effect = next(e for e in clip.effects if e.id == effect_id)
    assert effect.params[name] == pytest.approx(6.0) and not clip.animation
    _rows(window)[name].diamond.click()
    assert [t for t, _ in _frames(clip, f"fx.{effect_id}.{name}")] == [1.0]
    window.seek_to_position(2.0)
    _spin(window, name).setValue(9.0)  # animé : l'image clé de l'instant
    assert _frames(clip, f"fx.{effect_id}.{name}") == [
        (1.0, pytest.approx(6.0)),
        (2.0, pytest.approx(9.0)),
    ]
    assert effect.params[name] == pytest.approx(6.0), "la base n'a pas bougé"
    window.seek_to_position(1.5)
    assert _spin(window, name).value() == pytest.approx(7.5, abs=0.05)
    assert _spin(window, name).maximum() == maximum


def test_only_the_radius_of_the_glow_has_a_diamond_and_the_other_settings_stay_base_values(
    window,
):
    _track, clip = _prepared(window)
    _select(window, "gl")
    assert set(_rows(window)) == {"radius"}
    window.seek_to_position(1.0)
    _rows(window)["radius"].diamond.click()
    threshold = _spin(window, "threshold")
    threshold.setValue(0.3)
    gain = _spin(window, "intensity")
    gain.setValue(2.5)
    glow = next(e for e in clip.effects if e.id == "gl")
    assert glow.params["threshold"] == pytest.approx(0.3) and glow.params[
        "intensity"
    ] == pytest.approx(2.5)
    assert {k.property_name for k in clip.animation} == {"fx.gl.radius"}


def test_the_sharpen_has_no_diamond(window):
    _prepared(window)
    _select(window, "sh")
    assert _rows(window) == {}


@pytest.mark.parametrize("effect_id, name, maximum", CASES)
def test_a_locked_track_disables_the_new_rows_and_refuses_the_signals(
    window, effect_id, name, maximum
):
    track, clip = _prepared(window)
    track.locked = True
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    _select(window, effect_id)
    row = _rows(window)[name]
    assert not row.diamond.isEnabled() and not _spin(window, name).isEnabled()
    before = (
        list(clip.animation),
        len(window.history),
        [dict(e.params) for e in clip.effects],
    )
    window.on_effect_keyframe_set(clip.id, f"fx.{effect_id}.{name}", 2.0, 3.0)
    window.on_animation_toggled(clip.id, f"fx.{effect_id}.{name}", True)
    assert (
        list(clip.animation),
        len(window.history),
        [dict(e.params) for e in clip.effects],
    ) == before
