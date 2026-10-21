"""Découvrabilité de la navigation (hors écran) : le rail dit ce que fait chaque bouton, tous les onglets de l'inspecteur sont à un
clic, et chaque losange d'images clés est sur la ligne de son réglage.

Les assertions sont structurelles (pas de pixels) : les polices diffèrent d'une plateforme à l'autre.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QToolButton

from core.effects_model import EffectType, create_effect
from core.usage_level import UsageLevel
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes
from ui import i18n
from ui.side_rail import DEFAULT_SECTIONS

LEVELS = pytest.mark.parametrize("level", [UsageLevel.SIMPLE.value, UsageLevel.STUDIO.value, UsageLevel.ADVANCED.value])
LANGUAGES = pytest.mark.parametrize("language", ["fr", "en", "es"])


@pytest.fixture
def french(window):  # noqa: F811
    yield window
    i18n.set_language("fr")


def _select_clip(win, level: str):
    win.set_usage_level(level)
    track, clip = _add_track_clip(win, "video", "v")
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    QApplication.processEvents()
    return clip


# --- le rail de gauche : le nom est écrit sous chaque icône --------------------------------------------------------------

@LEVELS
@LANGUAGES
def test_every_visible_rail_button_shows_its_whole_name(french, level, language):
    win = french
    i18n.set_language(language)
    win.set_usage_level(level)
    QApplication.processEvents()
    rail = win.side_rail
    seen = 0
    for section in DEFAULT_SECTIONS:
        button = rail._buttons[section.id]
        if not button.isVisibleTo(rail):
            continue
        seen += 1
        text = button.text()
        assert text and text == i18n.translate(section.label_key)
        assert button.toolButtonStyle() == Qt.ToolButtonTextUnderIcon
        assert button.accessibleName() == text and button.toolTip() == text
        # Le texte tient dans le bouton, sans élision : une troncature est un échec.
        advance = QFontMetrics(button.font()).horizontalAdvance(text)
        assert advance <= button.width() - 4, (language, text, advance, button.width())
        assert rail.rect().contains(button.geometry()), (text, button.geometry(), rail.rect())
        assert not button.icon().isNull()
    assert seen >= 5


def test_the_rail_buttons_do_not_overlap_each_other(french):
    french.set_usage_level("advanced")
    QApplication.processEvents()
    rail = french.side_rail
    rects = [b.geometry() for b in rail._buttons.values() if b.isVisibleTo(rail)]
    for i, first in enumerate(rects):
        for second in rects[i + 1:]:
            assert not first.intersects(second)


def test_clicking_a_rail_button_still_switches_section(french):
    french.set_usage_level("advanced")
    rail = french.side_rail
    emitted: list[str] = []
    rail.section_changed.connect(emitted.append)
    rail._buttons["audio"].click()
    assert emitted == ["audio"] and rail.active() == "audio"


# --- l'inspecteur : sept onglets, tous visibles -------------------------------------------------------------------------

@LEVELS
def test_all_inspector_tabs_are_reachable_without_an_overflow_menu(french, level):
    win = french
    _select_clip(win, level)
    panel = win.properties_panel
    assert not hasattr(panel, "inspector_more_button")
    assert panel.findChildren(QToolButton, "inspectorMore") == []
    assert len(panel.inspector_tab_buttons) == 7
    names = [panel.inspector_tab_buttons[i].text() for i in range(7)]
    assert "Suivi" in names and "Compositing" in names and "Graphiques" in names
    viewport = panel.rect()
    for index, button in enumerate(panel.inspector_tab_buttons):
        assert button.isVisibleTo(panel), names[index]
        assert button.width() >= QFontMetrics(button.font()).horizontalAdvance(button.text())
        assert viewport.contains(button.mapTo(panel, button.rect().topLeft())), names[index]
        assert viewport.contains(button.mapTo(panel, button.rect().bottomRight())), names[index]
        button.click()                                   # un clic suffit, jamais de menu
        assert panel._active_inspector_tab == index and button.isChecked()
        assert [b.isChecked() for b in panel.inspector_tab_buttons].count(True) == 1


@LANGUAGES
def test_inspector_tabs_follow_the_language(french, language):
    win = french
    _select_clip(win, "advanced")
    panel = win.properties_panel
    i18n.set_language(language)
    for button, key in zip(panel.inspector_tab_buttons, panel._TAB_KEYS):
        assert button.text() == i18n.translate(key) == button.accessibleName()
    i18n.set_language("fr")
    assert panel.inspector_tab_buttons[6].text() == "Suivi"


def test_the_rail_follows_the_language_fr_en_es(french):
    win = french
    texts = {}
    for language in ("fr", "en", "es"):
        i18n.set_language(language)
        texts[language] = tuple(b.text() for b in win.side_rail._buttons.values())
    assert len(set(texts.values())) == 3


# --- les losanges : sur la ligne de leur réglage ------------------------------------------------------------------------

def _y_range(widget, panel):
    top = widget.mapTo(panel, widget.rect().topLeft()).y()
    return top, top + widget.height()


def _same_line(widget, field, panel) -> bool:
    (a0, a1), (b0, b1) = _y_range(widget, panel), _y_range(field, panel)
    return a0 < b1 and b0 < a1 and abs((a0 + a1) - (b0 + b1)) <= max(field.height(), widget.height())


def test_color_diamonds_sit_on_the_line_of_their_field(french):
    win = french
    _select_clip(win, "advanced")
    panel = win.properties_panel
    panel._select_inspector_tab(0)
    QApplication.processEvents()
    rows = panel.color_keyframe_widgets()
    names = list(panel.color_field_spins)
    assert set(rows) == set(names)
    for position, name in enumerate(names):
        row, spin = rows[name], panel.color_field_spins[name]
        assert row.diamond.isVisibleTo(panel) and spin.isVisibleTo(panel)
        for button in (row.previous, row.diamond, row.next):
            assert _same_line(button, spin, panel), (name, _y_range(button, panel), _y_range(spin, panel))
            assert button.mapTo(panel, button.rect().topLeft()).x() >= spin.mapTo(panel, spin.rect().topLeft()).x()
        # Jamais à hauteur du libellé du réglage suivant.
        if position + 1 < len(names):
            following = panel._color_captions[names[position + 1]]
            assert _y_range(row.diamond, panel)[1] <= _y_range(following, panel)[0] + 1, name
            assert not _same_line(row.diamond, following, panel), name


def test_effect_diamonds_sit_on_the_line_of_their_parameter(french):
    win = french
    win.set_usage_level("advanced")
    _track, clip = _add_track_clip(win, "video", "v")
    clip.effects = [create_effect(EffectType.COLOR_CORRECTION, effect_id="cc")]
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    panel = win.properties_panel
    panel._select_inspector_tab(2)
    panel.effects_list.setCurrentRow(0)
    QApplication.processEvents()
    rows = panel.effect_keyframe_widgets()
    assert rows
    for name, row in rows.items():
        spin = panel._effect_param_widgets[name]
        assert spin.isVisibleTo(panel)
        assert _same_line(row.diamond, spin, panel), name
        assert row.gate.parentWidget() is spin.parentWidget()      # même conteneur : même ligne de formulaire


def test_the_level_still_hides_only_the_diamonds_not_the_field(french):
    win = french
    _select_clip(win, "simple")
    panel = win.properties_panel
    for name, row in panel.color_keyframe_widgets().items():
        assert row.gate is not None and not row.gate.is_shown()
        assert not row.diamond.isVisibleTo(panel)
    win.set_usage_level("studio")
    QApplication.processEvents()
    assert all(row.diamond.isVisibleTo(panel) for row in panel.color_keyframe_widgets().values())


def test_color_captions_have_the_same_width_and_follow_the_language(french):
    win = french
    _select_clip(win, "advanced")
    panel = win.properties_panel
    for language in ("fr", "en", "es"):
        i18n.set_language(language)
        QApplication.processEvents()
        widths = {c.minimumWidth() for c in panel._color_captions.values()}
        assert len(widths) == 1 and widths.pop() >= max(c.sizeHint().width() for c in panel._color_captions.values()) - 1
        assert panel._color_captions["contrast"].text() == i18n.translate("field.contrast")


def test_usage_level_chip_says_what_it_is(french):
    win = french
    win.set_usage_level("advanced")
    chip = win.usage_level_chip
    assert chip.full_text() == "Niveau : Avancé"
    i18n.set_language("en")
    assert chip.full_text() == "Level: Advanced"
    assert isinstance(win.settings_button.text(), str) and win.settings_button.text().strip() == "Settings"
    assert not win.settings_button.icon().isNull()
