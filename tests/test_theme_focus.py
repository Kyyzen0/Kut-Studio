"""Le focus clavier doit se voir : chaque type de contrôle se dessine différemment quand il a le focus.

Comparaison *relative* d'un même contrôle focalisé / non focalisé (jamais de pixels de référence) : indépendante des
polices et de la plateforme. Sans règle ``:focus`` dans la feuille de style du thème, un bouton ou une case atteints
avec Tab ne montrent rien.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ui.adaptive_layout import WrappingCheckBox
from ui.icons import IconButton, IconName
from ui.theme import THEMES, ThemeManager, global_stylesheet

_MARKER = "_kut_studio_theme_stylesheet"


def _list() -> QListWidget:
    widget = QListWidget()
    widget.addItems(["Premier", "Deuxième"])
    return widget


def _tree() -> QTreeWidget:
    widget = QTreeWidget()
    widget.setHeaderHidden(True)
    widget.addTopLevelItem(__import__("PySide6.QtWidgets", fromlist=["QTreeWidgetItem"]).QTreeWidgetItem(["Racine"]))
    return widget


def _tool_button() -> QToolButton:
    button = QToolButton()
    button.setText("Outil")
    button.setFocusPolicy(Qt.TabFocus)
    return button


def _icon_button() -> IconButton:
    button = IconButton(icon=IconName.PLUS, tooltip="Plus")
    button.setFocusPolicy(Qt.TabFocus)
    return button


def _accent_text_button() -> IconButton:
    button = IconButton(icon=IconName.PLUS, text="Exporter", accent=True)
    button.setFocusPolicy(Qt.TabFocus)
    return button


def _slider() -> QSlider:
    slider = QSlider(Qt.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(40)
    slider.setFixedWidth(160)
    return slider


CONTROLS = {
    "bouton": lambda: QPushButton("Bouton"),
    "bouton-outil": _tool_button,
    "bouton-icône": _icon_button,
    "bouton-accent": _accent_text_button,
    "case": lambda: QCheckBox("Case"),
    "case-multiligne": lambda: WrappingCheckBox("Case à libellé multiligne"),
    "radio": lambda: QRadioButton("Radio"),
    "liste-déroulante": lambda: QComboBox(),
    "champ": lambda: QLineEdit("texte"),
    "nombre": lambda: QSpinBox(),
    "nombre-décimal": lambda: QDoubleSpinBox(),
    "texte-multiligne": lambda: QTextEdit("texte"),
    "texte-brut": lambda: QPlainTextEdit("texte"),
    "liste": _list,
    "arbre": _tree,
    "curseur": _slider,
}


@pytest.fixture
def themed_application():
    """La feuille de style réelle du thème sombre, sur l'application seulement pour la durée du test."""
    app = QApplication.instance()
    previous, marker = app.styleSheet(), app.property(_MARKER)
    ThemeManager("dark").apply_to(app)
    yield app
    app.setStyleSheet(previous)
    app.setProperty(_MARKER, marker)


@pytest.mark.parametrize("kind", sorted(CONTROLS))
def test_a_control_looks_different_when_it_has_the_keyboard_focus(qtbot, themed_application, kind):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    control = CONTROLS[kind]()
    other = QPushButton("Autre")  # prend le focus pour que le contrôle le perde
    layout.addWidget(control)
    layout.addWidget(other)
    host.resize(260, 220)
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    other.setFocus(Qt.TabFocusReason)
    qtbot.waitUntil(other.hasFocus, timeout=2000)
    unfocused = control.grab().toImage()
    control.setFocus(Qt.TabFocusReason)
    qtbot.waitUntil(control.hasFocus, timeout=2000)
    focused = control.grab().toImage()
    assert focused != unfocused, f"« {kind} » ne montre pas qu'il a le focus"


@pytest.mark.parametrize("name", sorted(THEMES))
def test_every_theme_declares_a_focus_rule_for_each_control_family(name):
    sheet = global_stylesheet(THEMES[name])
    for selector in ("QPushButton:focus", "QToolButton:focus", "QCheckBox::indicator:focus", "QListWidget:focus",
                     "QTextEdit:focus", "QSlider::handle:horizontal:focus", "QLineEdit:focus"):
        assert selector in sheet, f"{name} : pas de règle {selector}"
