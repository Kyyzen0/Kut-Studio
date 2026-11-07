"""Mode nuit : transformation des couleurs, palette sans état partagé, jetons d'identité intacts."""

from __future__ import annotations

from dataclasses import fields

import pytest

from ui.night_palette import UNTOUCHED_TOKENS, night_color, night_palette
from ui.theme import THEMES, ThemeManager, ThemePalette, active_palette, set_active_palette


def _snapshot(palette: ThemePalette) -> dict:
    return {f.name: getattr(palette, f.name) for f in fields(palette)}


def test_intensity_zero_changes_nothing():
    assert night_color("#2EF2D0", 0) == "#2EF2D0"
    assert night_palette(THEMES["dark"], 0) is THEMES["dark"]


def test_full_intensity_removes_most_blue_and_some_green():
    r, g, b = (int(night_color("#808080", 100)[i:i + 2], 16) for i in (1, 3, 5))
    assert b < g < r and b <= 128 * 0.5


def test_each_channel_is_monotone_in_the_intensity():
    previous = (255, 255, 255)
    for intensity in range(0, 101, 10):
        color = night_color("#FFFFFF", intensity)
        channels = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        assert all(c <= p for c, p in zip(channels, previous))
        previous = channels


def test_black_stays_black_and_the_alpha_is_kept():
    assert night_color("#000000", 100) == "#000000"
    assert night_color("#2EF2D026", 100).endswith("26")
    assert len(night_color("#2EF2D026", 60)) == 9


def test_an_already_warm_color_keeps_its_hue_family():
    warm = night_color("#FFB347", 100)
    r, g, b = (int(warm[i:i + 2], 16) for i in (1, 3, 5))
    assert r >= g >= b and r > 200 * 0.9


def test_out_of_range_intensity_is_clamped():
    assert night_color("#FFFFFF", 500) == night_color("#FFFFFF", 100)
    assert night_color("#FFFFFF", -4) == "#FFFFFF"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_base_palette_is_never_modified(theme):
    before = _snapshot(THEMES[theme])
    night = night_palette(THEMES[theme], 100)
    assert night is not THEMES[theme] and _snapshot(THEMES[theme]) == before


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_identity_tokens_are_untouched(theme):
    base, night = THEMES[theme], night_palette(THEMES[theme], 100)
    for name in UNTOUCHED_TOKENS:
        assert getattr(night, name) == getattr(base, name), name
    assert {"channel_red", "channel_green", "channel_blue", "angle_colors"} <= UNTOUCHED_TOKENS


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_painter_only_alpha_first_tokens_keep_their_alpha_in_front(theme):
    base, night = THEMES[theme], night_palette(THEMES[theme], 100)
    for name in ("playhead_dim", "snap_line"):
        assert len(getattr(night, name)) == 9
        assert getattr(night, name)[:3] == getattr(base, name)[:3]
        assert getattr(night, name)[3:].lower() == night.playhead[1:].lower()      # reste la couleur du playhead


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("intensity", [1, 30, 60, 100])
def test_two_distinct_tokens_never_share_a_color(theme, intensity):
    """``retheme_application`` revient en arrière par la couleur : deux couleurs d'origine distinctes restent distinctes."""
    base, night = THEMES[theme], night_palette(THEMES[theme], intensity)
    seen: dict[str, str] = {}
    for f in fields(base):
        for old, new in zip(*(v if isinstance(v, tuple) else (v,) for v in (getattr(base, f.name), getattr(night, f.name)))):
            if not (isinstance(old, str) and old.startswith("#")) or f.name in ("playhead_dim", "snap_line"):
                continue
            old6, new6 = old[:7].upper(), new[:7].upper()
            assert seen.setdefault(new6, old6) == old6, (f.name, old6, new6)


def test_a_palette_is_not_shared_state_between_managers():
    a, b = ThemeManager("dark"), ThemeManager("dark")
    assert a.set_night(80) is True
    assert b.effective_palette is THEMES["dark"] and b.night_intensity == 0       # le mode nuit d'a n'est qu'à a
    assert ThemeManager("dark").effective_palette is THEMES["dark"]               # un nouveau manager repart sans mode nuit
    assert active_palette() is THEMES["dark"]
    assert THEMES["dark"].background == "#080C11"                                 # la palette de référence n'a pas bougé


def test_manager_publishes_night_and_keeps_pure_colors():
    from ui.theme import COLORS, PURE_COLORS

    manager = ThemeManager("dark")
    base_background = THEMES["dark"].background
    assert manager.set_night(100) is True
    assert COLORS["background"] != base_background and PURE_COLORS["background"] == base_background
    assert manager.set_night(100) is False                          # rien ne change : pas de nouvelle publication
    assert manager.set_night(0) is True
    assert COLORS["background"] == base_background
    set_active_palette(THEMES["dark"])


def test_set_mode_with_night_publishes_once():
    manager = ThemeManager("dark")
    calls = []
    manager.subscribe(calls.append)
    manager.set_mode("light", night=70)
    assert len(calls) == 1 and manager.effective_palette is not THEMES["light"] and manager.base_palette is THEMES["light"]
    set_active_palette(THEMES["dark"])
