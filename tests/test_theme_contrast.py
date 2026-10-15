"""Contraste WCAG de la palette sombre par défaut (« Contraste+ »)."""

from ui.theme import ThemePalette


def _lin(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")[:6]
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


P = ThemePalette()


def test_text_contrasts_on_panel() -> None:
    assert contrast(P.text, P.panel) >= 4.5
    assert contrast(P.muted, P.panel) >= 4.5
    assert contrast(P.disabled_text, P.panel) >= 4.5


def test_on_accent_readable_on_accent() -> None:
    assert contrast(P.on_accent, P.accent) >= 4.5


def test_borders_and_markers_are_visible() -> None:
    assert contrast(P.border_strong, P.panel) >= 3
    assert contrast(P.playhead, P.timeline_bg) >= 3
    assert contrast(P.selection_line, P.panel) >= 3


def test_playhead_is_distinct_from_accent() -> None:
    assert P.playhead.lower() != P.accent.lower()


def test_painter_only_alpha_tokens_are_argb() -> None:
    """``QColor`` lit « #aarrggbb » : l'alpha doit venir en premier, sur la couleur du playhead."""
    for token in (P.playhead_dim, P.snap_line):
        assert len(token) == 9
        assert token[3:].lower() == P.playhead[1:].lower()
