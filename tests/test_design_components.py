"""Les composants communs du système de design : ce qu'ils promettent, vérifié.

Ces tests ne jugent pas le rendu. Ils gardent des **propriétés** : la palette Qt suit le thème, un repère reste visible sur une image
claire, tous les bandeaux ont la même hauteur, tous les dialogues les mêmes marges, un état n'est jamais porté par la seule couleur.
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtGui import QColor, QImage, QPainter, QPalette

from tools import ui_audit as audit
from ui.design_system import DIALOG_MARGINS, Sizes
from ui.overlay_paint import halo_stroke
from ui.panel_header import PanelHeader
from ui.search_field import SearchField
from ui.theme import OVERLAY, THEMES, OverlayColors, overlay_qcolor, qt_palette


def _luminance(color: QColor) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def _contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# ---------------------------------------------------------------------------
# La palette Qt : ce que prennent les widgets que la feuille de style ne décrit pas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_qt_palette_carries_the_theme_so_unstyled_widgets_do_not_fall_back_to_the_system(theme):
    palette, qt = THEMES[theme], qt_palette(THEMES[theme])
    assert qt.color(QPalette.Window) == QColor(palette.background)
    assert qt.color(QPalette.Base) == QColor(palette.input_bg)
    assert qt.color(QPalette.Highlight) == QColor(palette.accent)
    assert qt.color(QPalette.Disabled, QPalette.Text) == QColor(palette.disabled_text)
    # Le texte posé sur ces surfaces reste lisible : c'est ce que prend, par défaut, tout widget non décrit.
    assert _contrast(qt.color(QPalette.WindowText), qt.color(QPalette.Window)) >= 7
    assert _contrast(qt.color(QPalette.Text), qt.color(QPalette.Base)) >= 7
    assert _contrast(qt.color(QPalette.HighlightedText), qt.color(QPalette.Highlight)) >= 4.5


# ---------------------------------------------------------------------------
# Les repères posés sur l'image
# ---------------------------------------------------------------------------


def test_every_overlay_colour_is_a_plain_hex_and_alpha_is_always_explicit():
    values = vars(OverlayColors()).values()
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", value) for value in values), "une couleur de repère doit être « #rrggbb »"
    colour = overlay_qcolor(OVERLAY.guide, 110)
    assert colour.alpha() == 110 and colour.name().upper() == OVERLAY.guide.upper()   # pas « #rrggbbaa » lu comme « #aarrggbb »


def test_a_white_overlay_line_stays_visible_on_a_white_image_thanks_to_its_halo():
    image = QImage(40, 20, QImage.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    halo_stroke(painter, overlay_qcolor(OVERLAY.centre), 1.0, lambda: painter.drawLine(5, 10, 35, 10))
    painter.end()
    darkest = min(image.pixelColor(20, y).lightness() for y in range(7, 14))
    assert darkest < 160, "sans halo, un trait blanc disparaît sur une image blanche"


# ---------------------------------------------------------------------------
# Bandeau de panneau, champ de recherche
# ---------------------------------------------------------------------------


def test_the_panel_header_has_the_shared_height_title_role_and_trailing_slot(qtbot):
    from PySide6.QtWidgets import QLabel

    header = PanelHeader("Visionneuse")
    qtbot.addWidget(header)
    assert header.height() == Sizes.panel_header or header.maximumHeight() == Sizes.panel_header
    assert header.title_label.text() == "Visionneuse" and header.title_label.property("role") == "panel-title"
    extra = QLabel("1920 × 1080")
    header.add_trailing(extra)
    assert extra.parent() is header


def test_the_search_field_names_itself_after_its_placeholder_in_every_language(qtbot):
    field = SearchField("Rechercher…")
    qtbot.addWidget(field)
    assert field.accessibleName() == "Rechercher…" and field.height() == Sizes.search_field
    field.setPlaceholderText("Search…")                       # un changement de langue
    assert field.accessibleName() == "Search…" and field.isClearButtonEnabled()


# ---------------------------------------------------------------------------
# Ce qui dépend d'une fenêtre complète
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def window():
    window = audit.make_main_window(1280, 720, scopes=False)
    audit.select_first_clip(window)
    yield window
    window.close()


def test_every_dialog_uses_the_shared_margins(window):
    for name, factory in audit.dialog_factories(window):
        dialog = factory()
        try:
            margins = dialog.layout().contentsMargins()
            assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == DIALOG_MARGINS, name
        finally:
            dialog.close()


def test_the_library_title_follows_the_page_that_is_shown(window):
    from ui import i18n

    panel = window.project_panel
    for section, key in (("transitions", "rail.transitions"), ("effects", "rail.effects"), ("media", "rail.media")):
        panel.select_section(section)
        assert panel._title_label.text() == i18n.translate(key)       # noqa: SLF001


def test_a_tracker_swatch_tells_its_health_by_shape_and_not_by_colour_alone():
    from ui.tracking_panel import _swatch

    def pixels(health: str) -> bytes:
        return bytes(_swatch("#3366FF", health).pixmap(14, 14).toImage().constBits())

    good, uncertain, lost = pixels("good"), pixels("uncertain"), pixels("lost")
    assert len({good, uncertain, lost}) == 3, "trois états, trois formes"
    assert good == pixels(""), "un suivi correct garde la pastille pleine"


# ---------------------------------------------------------------------------
# Le coût du style : Qt reparse la feuille à chaque setStyleSheet, même identique
# ---------------------------------------------------------------------------


def test_a_style_sheet_is_only_written_when_it_changed(qtbot):
    from PySide6.QtWidgets import QLabel

    from ui.theme import set_stylesheet_if_changed

    label = QLabel("x")
    qtbot.addWidget(label)
    assert set_stylesheet_if_changed(label, "color: red;") is True
    assert set_stylesheet_if_changed(label, "color: red;") is False        # identique : rien n'est écrit, rien n'est reparsé
    assert set_stylesheet_if_changed(label, "color: blue;") is True and label.styleSheet() == "color: blue;"


def test_refreshing_an_unchanged_clip_does_not_rewrite_its_style_sheets(window, monkeypatch):
    """Trois ``setStyleSheet`` par clip à chaque rafraîchissement représentaient 87 % de ``refresh_clip_widgets`` (+45 % au banc)."""
    from PySide6.QtWidgets import QWidget

    widget = window.timeline_panel.clip_widgets[next(iter(window.timeline_panel.clip_widgets))]
    widget.refresh_style()                                                  # la première écriture est légitime
    writes: list[int] = []
    original = QWidget.setStyleSheet
    monkeypatch.setattr(QWidget, "setStyleSheet", lambda self, sheet: (writes.append(1), original(self, sheet))[1])
    widget.refresh_style()
    assert writes == [], f"{len(writes)} feuille(s) de style réécrite(s) alors que rien n'a changé"
