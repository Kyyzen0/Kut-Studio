"""Champ numérique qui accepte le point ET la virgule comme séparateur décimal, quelle que soit la locale du champ.

``QDoubleSpinBox`` n'accepte que le séparateur de la locale : sur un système français, taper « 0.5 » donnait « 0 », et sur
une locale anglaise ou C la virgule est lue comme un séparateur de milliers. Ces champs (position, échelle, rotation,
opacité : intervalles ≤ 3600) n'ont aucun usage d'un groupement des milliers. La règle est donc simple : le champ n'affiche
jamais de séparateur de milliers, et à la saisie « , » et « . » valent tous deux le séparateur décimal de la locale du
champ ; les espaces (y compris insécables) sont ignorés. Un texte à deux séparateurs (« 1,2,3 », « 3,600.0 ») reste invalide
et est refusé par le validateur.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QDoubleSpinBox

_SPACES = re.compile(r"[\s  ]+")


class TolerantDoubleSpinBox(QDoubleSpinBox):
    """``QDoubleSpinBox`` pour lequel « . » et « , » valent le séparateur décimal de la locale du champ."""

    def _normalised(self, text: str) -> str:
        point = self.locale().decimalPoint()
        return _SPACES.sub("", text).replace(",", point).replace(".", point)

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt
        locale = QLocale(self.locale())
        locale.setNumberOptions(locale.numberOptions() | QLocale.OmitGroupSeparator)
        return locale.toString(float(value), "f", self.decimals())

    def validate(self, text: str, pos: int):  # noqa: A003 - Qt
        state, fixed, position = super().validate(self._normalised(text), pos)
        return state, fixed, position

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt
        return super().valueFromText(self._normalised(text))


__all__ = ["TolerantDoubleSpinBox"]
