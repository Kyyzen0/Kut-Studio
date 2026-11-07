"""Rangée ‹ ◆ › d'images clés sous un réglage : construite à neuf par le builder qui la pose (jamais déplacée).

Partagée par les effets (``ui.properties_panel_mixins.effect_keyframes``, ADR-0008 étape 4), l'étalonnage de l'inspecteur
(``color_keyframes``, étape 8) et les roues de la page Couleur (``ui.color_page.wheel_keys``) : même apparence, mêmes noms
accessibles, même menu au clic droit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QHBoxLayout, QSizePolicy, QToolButton, QWidget

from core.usage_level import UsageLevel
from ui.design_system import Sizes
from ui import i18n
from ui.properties_widgets.diamond_button import KeyframeDiamondButton
from ui.properties_widgets.level_gate import LevelGate, gated


@dataclass
class KeyframeRow:
    """Les trois boutons d'un réglage animable et leur conteneur de niveau (``None`` : rangée sans niveau)."""

    gate: LevelGate | None
    previous: QToolButton
    diamond: KeyframeDiamondButton
    next: QToolButton
    inner: QWidget


def build_keyframe_row(
    property_id: str, label_text: str, *, on_previous: Callable[[], None], on_toggle: Callable[[], None],
    on_next: Callable[[], None], on_menu: Callable[[object], None] | None = None,
    form: QFormLayout | None = None, minimum: UsageLevel | None = None, beside: QHBoxLayout | None = None,
) -> KeyframeRow:
    """Rangée neuve. Avec ``form`` et ``minimum`` : posée dans le formulaire, derrière un conteneur de niveau (le niveau n'écrit
    que sa visibilité) ; sans : ``row.inner`` est à poser par l'appelant.

    ``beside`` (avec ``minimum``) : les boutons se posent **à droite du champ**, dans la rangée ``beside`` que l'appelant a déjà
    remplie du champ, sur la même ligne que le libellé du réglage (et non dans une ligne à eux, qui flottait entre ce réglage et le
    suivant) ; le conteneur de niveau ne cache alors que les boutons, jamais le champ."""
    inner = QWidget()
    layout = QHBoxLayout(inner)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    previous = QToolButton()
    previous.setObjectName("iconOnly")      # padding nul : sinon le thème rogne la flèche
    previous.setText("‹")
    previous.setFixedSize(18, Sizes.icon_button_sm)
    previous.clicked.connect(lambda _checked=False: on_previous())
    diamond = KeyframeDiamondButton(property_id)
    diamond.clicked.connect(lambda _checked=False: on_toggle())
    if on_menu is not None:
        diamond.setContextMenuPolicy(Qt.CustomContextMenu)
        diamond.customContextMenuRequested.connect(on_menu)
    next_button = QToolButton()
    next_button.setObjectName("iconOnly")
    next_button.setText("›")
    next_button.setFixedSize(18, Sizes.icon_button_sm)
    next_button.clicked.connect(lambda _checked=False: on_next())
    previous.setAccessibleName(i18n.translate("a11y.keyframe.previous", property=label_text))
    diamond.setAccessibleName(i18n.translate("a11y.keyframe.toggle", property=label_text))
    next_button.setAccessibleName(i18n.translate("a11y.keyframe.next", property=label_text))
    for widget in (previous, diamond, next_button):
        layout.addWidget(widget)
    gate = None
    if beside is not None and minimum is not None:
        gate = gated(inner, minimum)               # sans formulaire : ne cache que les boutons
        gate.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        beside.addWidget(gate)
        return KeyframeRow(gate, previous, diamond, next_button, inner)
    layout.addStretch(1)
    if form is not None and minimum is not None:
        gate = gated(inner, minimum, form)
        form.addRow(gate)
    return KeyframeRow(gate, previous, diamond, next_button, inner)


def field_with_keyframes(field: QWidget) -> tuple[QWidget, QHBoxLayout]:
    """Conteneur neuf ``[champ | ‹ ◆ ›]`` à poser comme champ d'une ligne de formulaire (jamais un widget déjà posé : ``field`` est
    créé juste avant). Le second résultat est la rangée où ``build_keyframe_row(beside=…)`` ajoute ses boutons."""
    holder = QWidget()
    holder.setObjectName("field_with_keys")
    line = QHBoxLayout(holder)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(2)
    field.setSizePolicy(QSizePolicy.Preferred, field.sizePolicy().verticalPolicy())
    field.setMinimumWidth(72)         # le champ cède de la place aux trois boutons : libellé, champ et losanges tiennent sur une ligne
    line.addWidget(field, 1)
    return holder, line


def set_spin_quietly(spin: QDoubleSpinBox, value: float) -> None:
    """Affiche ``value`` sans émettre ``valueChanged`` (le curseur suit une courbe, il ne l'édite pas)."""
    if abs(spin.value() - value) < 5e-4:
        return
    blocked = spin.blockSignals(True)
    try:
        spin.setValue(value)
    finally:
        spin.blockSignals(blocked)


def set_diamond_quietly(diamond: KeyframeDiamondButton, checked: bool, animated: bool) -> None:
    """Losange plein / vide et contour « animé » sans émettre de clic."""
    blocked = diamond.blockSignals(True)
    diamond.setChecked(checked)
    diamond.blockSignals(blocked)
    diamond.set_animated(animated)


__all__ = ["KeyframeRow", "build_keyframe_row", "field_with_keyframes", "set_diamond_quietly", "set_spin_quietly"]
