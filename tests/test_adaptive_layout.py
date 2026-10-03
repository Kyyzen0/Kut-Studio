"""Briques de mise en page des petites tailles (:mod:`ui.adaptive_layout`)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.adaptive_layout import (
    ElidedLabel,
    FlowLayout,
    ShrinkableScrollArea,
    WrappingCheckBox,
    allow_shrinking,
    make_shrinkable,
)


def _settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


def test_flow_layout_wraps_buttons_instead_of_overflowing(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    flow = FlowLayout(host, spacing=4)
    buttons = [QPushButton(text) for text in ("Ajouter", "Supprimer", "Réinitialiser", "Stop")]
    for button in buttons:
        flow.addWidget(button)
    natural = sum(b.sizeHint().width() for b in buttons) + 4 * 3
    host.resize(natural, 60)
    host.show()
    _settle()
    assert len({b.y() for b in buttons}) == 1  # assez large : une seule rangée
    width = max(b.sizeHint().width() for b in buttons) + 20
    host.resize(width, 200)
    _settle()
    assert len({b.y() for b in buttons}) > 1  # étroit : plusieurs rangées
    for button in buttons:
        assert button.geometry().right() <= width
    assert flow.heightForWidth(width) >= 2 * buttons[0].sizeHint().height()
    # La largeur minimale est celle du plus large élément, pas la somme.
    assert flow.minimumSize().width() == max(b.minimumSizeHint().width() for b in buttons)


def test_flow_layout_height_grows_in_a_parent_layout(qtbot):
    """Une rangée qui passe à la ligne agrandit son parent au lieu de se faire écraser."""
    host = QWidget()
    qtbot.addWidget(host)
    outer = QVBoxLayout(host)
    row = QWidget()
    flow = FlowLayout(row, spacing=4)
    for text in ("Un libellé assez long", "Un autre libellé long", "Et un troisième"):
        flow.addWidget(QPushButton(text))
    outer.addWidget(row)
    outer.addWidget(QLabel("suite"))
    host.resize(180, 300)
    host.show()
    _settle()
    assert row.height() >= flow.heightForWidth(180)


def test_wrapping_check_box_wraps_its_label_and_stays_a_check_box(qtbot):
    box = WrappingCheckBox("Analyser le proxy s'il existe (plus rapide)")
    qtbot.addWidget(box)
    box.resize(box.sizeHint().width(), box.sizeHint().height())
    narrow = 120
    assert box.hasHeightForWidth()
    assert box.heightForWidth(narrow) > box.heightForWidth(box.sizeHint().width())
    assert box.minimumSizeHint().width() < box.sizeHint().width() / 2
    box.show()
    box.resize(narrow, box.heightForWidth(narrow))
    toggled = []
    box.toggled.connect(toggled.append)
    box.click()
    assert box.isChecked() and toggled == [True]
    assert box.text().startswith("Analyser")  # nom accessible et lecture du texte inchangés
    box.grab()  # le dessin du libellé multiligne ne doit pas lever


def test_wrapping_check_box_makes_a_form_row_fit(qtbot):
    """Régression : le libellé « Analyser le proxy s'il existe (plus rapide) » imposait ~260 px à l'inspecteur."""
    host = QWidget()
    qtbot.addWidget(host)
    form = QFormLayout(host)
    long_text = "Analyser le proxy s'il existe (plus rapide), même sur une très longue ligne"
    form.addRow(WrappingCheckBox(long_text))
    plain = QWidget()
    qtbot.addWidget(plain)
    from PySide6.QtWidgets import QCheckBox

    QFormLayout(plain).addRow(QCheckBox(long_text))
    assert host.minimumSizeHint().width() < plain.minimumSizeHint().width() / 2


def test_shrinkable_scroll_area_follows_its_content_and_can_shrink(qtbot):
    content = QWidget()
    layout = QVBoxLayout(content)
    for index in range(6):
        layout.addWidget(QLabel(f"ligne {index}"))
    area = ShrinkableScrollArea(content, min_height=60)
    qtbot.addWidget(area)
    assert area.minimumSizeHint().height() == 60
    assert area.sizeHint().height() >= content.sizeHint().height()
    area.resize(200, 60)
    area.show()
    _settle()
    assert area.verticalScrollBar().maximum() > 0  # trop bas pour tout montrer : il défile
    layout.addWidget(QLabel("une ligne de plus"))
    _settle()
    assert area.sizeHint().height() >= content.sizeHint().height()  # le hint suit le contenu


def test_shrinkable_scroll_area_does_not_paint_the_system_background(qtbot):
    content = QWidget()
    area = ShrinkableScrollArea(content, min_height=10)
    qtbot.addWidget(area)
    assert not content.autoFillBackground()
    assert not area.viewport().autoFillBackground()


def test_elided_label_shrinks_and_keeps_the_full_text_in_its_tooltip(qtbot):
    label = ElidedLabel("Un titre de transition beaucoup trop long pour la carte")
    qtbot.addWidget(label)
    assert label.minimumSizeHint().width() < label.sizeHint().width() / 3
    assert label.toolTip() == label.text()
    label.resize(60, label.sizeHint().height())
    label.show()
    label.grab()  # le texte tronqué se dessine sans erreur
    label.setText("Autre")
    assert label.toolTip() == "Autre"


def test_make_shrinkable_only_lowers_the_minimum_width(qtbot):
    items = ["Un libellé extrêmement long, choisi pour élargir la liste", "court"]
    plain = QComboBox()
    qtbot.addWidget(plain)
    plain.addItems(items)
    shrinkable = make_shrinkable(QComboBox(), 6)  # à la construction, avant toute lecture de taille
    qtbot.addWidget(shrinkable)
    shrinkable.addItems(items)
    assert shrinkable.sizeHint().width() == plain.sizeHint().width()  # rien ne change quand la place ne manque pas
    assert shrinkable.minimumSizeHint().width() < plain.minimumSizeHint().width() / 2


def test_allow_shrinking_lets_spin_boxes_go_down_to_their_minimum_hint(qtbot):
    spin = QDoubleSpinBox()
    qtbot.addWidget(spin)
    spin.setSuffix(" px")
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    layout.addWidget(spin)
    before = layout.minimumSize().width()
    allow_shrinking(spin)
    assert layout.minimumSize().width() <= before
    button = QPushButton("Un bouton au libellé assez long")
    qtbot.addWidget(button)
    allow_shrinking(button, 60)
    assert button.minimumWidth() == 60
    assert spin.sizePolicy().horizontalPolicy() == spin.sizePolicy().Policy.Preferred
