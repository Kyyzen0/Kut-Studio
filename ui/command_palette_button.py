"""Bouton « Rechercher… ⌘K » de la barre du haut, à trois formes selon la place disponible.

La barre du haut est pleine à 1180 px (et plus encore avec des polices plus larges) : le bouton cède de la place plutôt que de
comprimer « Réglages », « Exporter » ou la puce du niveau d'usage. Formes : **absente** en dernier recours (le raccourci et le menu des fonctions restent), **complète** (loupe, « Rechercher… » et raccourci),
**texte** (loupe et « Rechercher… »), **icône** (la loupe seule ; l'infobulle et le nom accessible gardent le libellé et le
raccourci). Le bouton réserve toujours la largeur *complète* de la puce du niveau : il ne dépend donc pas de la forme
courante de la puce (pas d'oscillation entre les deux ajusteurs).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QToolButton

FULL, TEXT, ICON, HIDDEN = "full", "text", "icon", "hidden"
_FORMS = (FULL, TEXT, ICON, HIDDEN)


class PaletteButton(QToolButton):
    """``QToolButton`` de la palette ; ``set_texts`` donne le libellé et le raccourci, ``set_form`` la forme."""

    def __init__(self) -> None:
        super().__init__()
        self._label = ""
        self._shortcut = ""
        self._form = FULL

    def form(self) -> str:
        return self._form

    def set_texts(self, label: str, shortcut: str) -> None:
        self._label, self._shortcut = label, shortcut
        self._show_form()

    def set_form(self, form: str) -> None:
        if form in _FORMS and form != self._form:
            self._form = form
            self._show_form()

    def _text_for(self, form: str) -> str:
        if form in (ICON, HIDDEN):
            return ""
        return f" {self._label}   {self._shortcut}".rstrip() if form == FULL and self._shortcut else f" {self._label}"

    def _show_form(self) -> None:
        self.setToolButtonStyle(Qt.ToolButtonIconOnly if self._form == ICON else Qt.ToolButtonTextBesideIcon)
        self.setText(self._text_for(self._form))
        self.setVisible(self._form != HIDDEN)

    def width_for(self, form: str) -> int:
        """Largeur idéale sous la forme demandée (indépendante de la forme courante)."""
        if form == HIDDEN:
            return 0
        chrome = self.iconSize().width() + 24
        return chrome + self.fontMetrics().horizontalAdvance(self._text_for(form))


class FitPaletteButton(QObject):
    """Choisit la forme du bouton selon la place restante, en réservant la largeur complète de la puce du niveau."""

    _EVENTS = (QEvent.Show, QEvent.Resize, QEvent.StyleChange, QEvent.FontChange, QEvent.LayoutRequest)

    def __init__(self, bar, button: PaletteButton, reserved) -> None:
        """``reserved`` : widgets dont la largeur complète est réservée (la puce du niveau, qui a ``width_for(\"full\")``)."""
        super().__init__(bar)
        self._bar, self._button, self._reserved, self._busy = bar, button, reserved, False
        bar.installEventFilter(self)

    def fit(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            layout = self._bar.layout()
            margins = layout.contentsMargins()
            skipped = {id(self._button), *(id(w) for w in self._reserved)}
            widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
            widgets = [w for w in widgets if w is not None and id(w) not in skipped and not w.isHidden()]
            used = sum(w.sizeHint().width() for w in widgets) + margins.left() + margins.right()
            reserved = sum(w.width_for("full") for w in self._reserved)
            gaps = layout.spacing() * (len(widgets) + 1 + len(self._reserved))
            room = self._bar.width() - used - reserved - gaps
            form = next((f for f in _FORMS if self._button.width_for(f) <= room), HIDDEN)
            self._button.set_form(form)
        finally:
            self._busy = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt
        if event.type() in self._EVENTS:
            self.fit()
        return False


__all__ = ["FULL", "HIDDEN", "ICON", "TEXT", "FitPaletteButton", "PaletteButton"]
