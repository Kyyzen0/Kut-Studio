"""Une section repliée ne montre rien de son contenu, même quand la logique de l'inspecteur réaffiche une de ses lignes.

L'inspecteur cache et montre des lignes selon l'état (la durée d'un gel n'existe que sur un clip figé). Une section repliée qui ne
surveillait que ce qui était visible au moment du pliage laissait réapparaître, en plein repli, une ligne cachée à ce moment-là.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout

from ui.properties_widgets.section_box import SectionBox


@pytest.fixture(autouse=True)
def _forget_remembered_sections():
    SectionBox._remembered.clear()      # noqa: SLF001 - la mémoire de session est partagée par toute la classe
    yield
    SectionBox._remembered.clear()      # noqa: SLF001


def _section(qtbot):
    box = SectionBox("Gel", key="")
    layout = QVBoxLayout(box)
    shown, conditional = QLabel("toujours là"), QLabel("durée du gel")
    layout.addWidget(shown)
    layout.addWidget(conditional)
    conditional.setVisible(False)                   # une ligne que la logique n'affiche que dans certains états
    qtbot.addWidget(box)
    box.show()
    QApplication.processEvents()
    return box, shown, conditional


def _settle():
    QApplication.processEvents()
    QApplication.processEvents()                    # le recachage est différé d'un tour de boucle


def test_folding_hides_what_was_visible_and_unfolding_restores_it_and_nothing_else(qtbot):
    box, shown, conditional = _section(qtbot)
    box.set_open(False)
    assert shown.isHidden() and conditional.isHidden()
    box.set_open(True)
    assert not shown.isHidden()
    assert conditional.isHidden()                   # elle était cachée avant le pliage : elle le reste


def test_a_row_the_logic_shows_while_the_section_is_folded_stays_hidden_until_it_opens(qtbot):
    box, _shown, conditional = _section(qtbot)
    box.set_open(False)
    conditional.setVisible(True)                    # ex. : on sélectionne un clip figé pendant que la section est repliée
    _settle()
    assert conditional.isHidden(), "la ligne réaffichée déborde de la section repliée"
    box.set_open(True)
    assert not conditional.isHidden(), "la logique voulait cette ligne visible : elle apparaît à l'ouverture"


def test_a_row_shown_while_folded_that_the_logic_then_hides_does_not_reopen(qtbot):
    box, _shown, conditional = _section(qtbot)
    box.set_open(False)
    conditional.setVisible(True)
    conditional.setVisible(False)                   # réaffichée puis recachée avant que le repli ne repasse
    _settle()
    box.set_open(True)
    assert conditional.isHidden()


def test_a_widget_added_while_the_section_is_folded_does_not_appear_inside_it(qtbot):
    box, _shown, _conditional = _section(qtbot)
    box.set_open(False)
    late = QLabel("ajoutée plus tard")
    box.layout().addWidget(late)
    late.setVisible(True)
    _settle()
    assert late.isHidden()
    box.set_open(True)
    assert not late.isHidden()


def test_unfolding_stops_watching_the_children(qtbot):
    box, _shown, conditional = _section(qtbot)
    box.set_open(False)
    box.set_open(True)
    conditional.setVisible(True)                    # une section ouverte laisse la logique faire
    _settle()
    assert not conditional.isHidden()
