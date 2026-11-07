"""Mode nuit : les textes restent lisibles à 100 % d'intensité, sur tous les thèmes (mêmes seuils que ``test_theme_contrast``)."""

from __future__ import annotations

import pytest
from test_theme_contrast import contrast

from ui.night_palette import CONTRAST_PAIRS, night_palette
from ui.theme import THEMES

THEME_NAMES = ["dark", "light"]


@pytest.mark.parametrize("theme", THEME_NAMES)
@pytest.mark.parametrize("intensity", [30, 60, 100])
def test_every_pair_keeps_its_threshold_or_its_original_ratio(theme, intensity):
    base, night = THEMES[theme], night_palette(THEMES[theme], intensity)
    for foreground, background, threshold in CONTRAST_PAIRS:
        original = contrast(getattr(base, foreground), getattr(base, background))
        wanted = min(threshold, original)
        got = contrast(getattr(night, foreground), getattr(night, background))
        assert got >= wanted, (theme, intensity, foreground, background, round(got, 2), round(wanted, 2))


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_text_on_the_panel_stays_at_4_5_at_full_intensity(theme):
    night = night_palette(THEMES[theme], 100)
    assert contrast(night.text, night.panel) >= 4.5
    assert contrast(night.muted, night.panel) >= 4.5
    assert contrast(night.on_accent, night.accent) >= 4.5
    assert contrast(night.clip_text, night.clip_video_fill) >= 4.5


def test_the_guard_actually_guards(monkeypatch):
    """Sans la réparation des contrastes, le réchauffement seul fait tomber des paires sous leur seuil (le test ci-dessus le voit)."""
    import ui.night_palette as module

    monkeypatch.setattr(module, "_repair", lambda foreground, background, target: foreground)
    night = night_palette(THEMES["dark"], 100)
    failing = [pair for pair in CONTRAST_PAIRS
               if contrast(getattr(night, pair[0]), getattr(night, pair[1]))
               < min(pair[2], contrast(getattr(THEMES["dark"], pair[0]), getattr(THEMES["dark"], pair[1])))]
    assert failing
