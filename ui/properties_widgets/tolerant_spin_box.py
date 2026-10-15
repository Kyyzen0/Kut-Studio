"""Champ numérique qui accepte le point ET la virgule comme séparateur décimal, quelle que soit la langue du système.

``QDoubleSpinBox`` n'accepte que le séparateur de la locale : sur un système français, taper « 0.5 » donnait « 0 ». On
tape souvent l'un ou l'autre (clavier, pavé numérique, valeur collée) : les deux sont acceptés, et le champ affiche
toujours le séparateur de sa locale.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox


class TolerantDoubleSpinBox(QDoubleSpinBox):
    """``QDoubleSpinBox`` pour lequel « . » et « , » valent le séparateur décimal de la locale."""

    def _normalised(self, text: str) -> str:
        point = self.locale().decimalPoint()
        other = "." if point != "." else ","
        group = self.locale().groupSeparator()
        if other == group:      # « , » est aussi le séparateur de milliers de la locale (anglais) : on ne le remplace pas
            return text
        return text.replace(other, point)

    def validate(self, text: str, pos: int):  # noqa: A003 - Qt
        state, fixed, position = super().validate(self._normalised(text), pos)
        return state, fixed, position

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt
        return super().valueFromText(self._normalised(text))


__all__ = ["TolerantDoubleSpinBox"]
