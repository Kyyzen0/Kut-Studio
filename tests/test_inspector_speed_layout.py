"""La section « Vitesse et durée » de l'inspecteur ne se recouvre pas elle-même, quel que soit le chemin pour y arriver.

Ses préréglages de vitesse sont une rangée qui retourne à la ligne (``FlowLayout``). Posée à côté de son libellé dans un
``QFormLayout`` à retour automatique, elle recevait une colonne étroite (cinq boutons empilés) alors que la hauteur de la ligne était
calculée pour une autre largeur : après un changement d'onglet, la section suivante démarrait sous le milieu des préréglages et les
recouvrait. Le défaut existait déjà ; des boutons plus larges (ou la police plus large de Linux) l'ont fait apparaître en CI.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

from tools import ui_audit as audit
from ui import i18n

PRESETS = ("speed_0_25x_button", "speed_0_5x_button", "speed_1x_button", "speed_2x_button", "speed_4x_button")


@pytest.fixture(scope="module")
def window():
    window = audit.make_main_window(1280, 720, scopes=False)
    audit.select_first_clip(window)
    yield window
    window.close()


@pytest.fixture(autouse=True)
def _french():
    i18n.set_language("fr")
    yield
    i18n.set_language("fr")


def _show_effects_tab(window) -> None:
    panel = window.properties_panel
    tab = dict((name, index) for index, name in audit.INSPECTOR_TABS)["effets"]
    panel._select_inspector_tab(0)             # on en part, on y revient : c'est ce trajet qui laissait une mise en page périmée
    audit.settle(window)
    panel._select_inspector_tab(tab)
    audit.settle(window)


def _top(widget, content) -> int:
    return widget.mapTo(content, widget.rect().topLeft()).y()


def _bottom(widget, content) -> int:
    return widget.mapTo(content, widget.rect().bottomLeft()).y()


def test_the_speed_presets_never_overlap_the_next_section_after_switching_tabs(window):
    panel = window.properties_panel
    content = panel.scroll_area.widget()
    for _round in range(3):
        _show_effects_tab(window)
        presets_bottom = max(_bottom(getattr(panel, name), content) for name in PRESETS)
        assert _top(panel.time_section, content) >= presets_bottom, "la section qui suit recouvre les préréglages de vitesse"


def test_the_speed_presets_row_uses_the_full_width_under_its_label(window):
    panel = window.properties_panel
    _show_effects_tab(window)
    content = panel.scroll_area.widget()
    label = panel._stacked_labels[0][0]                       # noqa: SLF001
    first = min(_top(getattr(panel, name), content) for name in PRESETS)
    assert isinstance(label, QLabel) and _bottom(label, content) <= first
    form_width = panel.speed_group.width()
    row_right = max(getattr(panel, name).mapTo(panel.speed_group, getattr(panel, name).rect().topRight()).x() for name in PRESETS)
    assert row_right <= form_width


def test_the_stacked_label_follows_the_language(window):
    panel = window.properties_panel
    label, key = panel._stacked_labels[0]                     # noqa: SLF001
    assert label.text() == i18n.translate(key)
    i18n.set_language("en")
    panel.retranslate()
    assert label.text() == i18n.translate(key)
    assert label.text() != ""
