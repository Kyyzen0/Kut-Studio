"""Repli du bloc « dossiers, filtres, tags » de la bibliothèque quand la colonne est trop basse.

Sur un portable (~250 px pour toute la colonne de gauche), l'en-tête, la recherche, ce bloc et la liste ne tiennent pas
ensemble : le bloc des dossiers passait avant la liste, qui restait sous le bord, sans une ligne visible. Le bloc se replie
donc de lui-même quand la colonne est basse ; un bouton à côté du champ de recherche le rouvre (ou le replie) à la main, et ce
choix est respecté ensuite, quelle que soit la hauteur.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractButton, QWidget

#: Hauteur de colonne (px) à partir de laquelle le bloc reste ouvert : l'en-tête, la recherche, le bloc (~200 px) et ~3 lignes de
#: médias (3 x 56 px).
EXPAND_FROM_HEIGHT = 480


class BrowseFold(QObject):
    """Montre ou cache ``block`` selon la hauteur du ``viewport`` de la colonne, ou selon le choix de l'utilisateur."""

    def __init__(self, toggle: QAbstractButton, block: QWidget, viewport: QWidget) -> None:
        super().__init__(toggle)
        self._toggle = toggle
        self._block = block
        self._viewport = viewport
        self._available = True                      # la section courante montre-t-elle ce bloc (Médias, Audio) ?
        self._manual: bool | None = None            # ``None`` : suit la hauteur ; sinon le choix de l'utilisateur
        toggle.setCheckable(True)
        toggle.clicked.connect(self._on_clicked)
        viewport.installEventFilter(self)
        self.sync()

    @property
    def expanded(self) -> bool:
        return not self._block.isHidden()

    def set_available(self, available: bool) -> None:
        """La section courante n'a pas de dossiers (Effets, Titres...) : bloc et bouton cachés."""
        self._available = bool(available)
        self.sync()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self.sync()
        return super().eventFilter(watched, event)

    def sync(self) -> None:
        """Aligne le bloc et le bouton sur le choix de l'utilisateur, sinon sur la hauteur de la colonne."""
        wanted = self._manual if self._manual is not None else self._viewport.height() >= EXPAND_FROM_HEIGHT
        self._block.setVisible(wanted and self._available)
        self._toggle.setVisible(self._available)
        self._toggle.blockSignals(True)
        self._toggle.setChecked(wanted)
        self._toggle.blockSignals(False)

    def set_expanded(self, expanded: bool) -> None:
        """Choix explicite (bouton) : il l'emporte sur la hauteur."""
        self._manual = bool(expanded)
        self.sync()

    def _on_clicked(self, checked: bool) -> None:
        self.set_expanded(checked)
