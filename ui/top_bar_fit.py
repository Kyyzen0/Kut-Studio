"""« Réglages » de la barre du haut : dernier élément qui cède de la place, après la puce du niveau et « Rechercher… ».

Avec une police de repli plus large (DejaVu Sans sous Linux, ou +45 % de police) la barre du haut ne tient plus dans une fenêtre de
1280 px même sans la puce du niveau ni le bouton de recherche : le sélecteur de pages (« Montage », « Couleur », « Composition »)
était alors comprimé et ses libellés tronqués. Le bouton passe donc à l'icône seule (l'infobulle et le nom accessible gardent
le libellé ; le texte du bouton reste posé, seul le style change).

La décision ne dépend ni de la puce ni du bouton de recherche (ils sont comptés comme absents) : les trois ajusteurs ne peuvent
pas se renvoyer la balle. Elle ne dépend pas non plus de la forme courante du bouton (marge mesurée seulement sous la forme texte).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QToolButton


class FitSettingsButton(QObject):
    """Passe le bouton de texte + icône à l'icône seule quand la barre ne tient pas, même sans les éléments facultatifs."""

    _EVENTS = (QEvent.Show, QEvent.Resize, QEvent.StyleChange, QEvent.FontChange, QEvent.LayoutRequest)

    def __init__(self, bar, button: QToolButton, optional) -> None:
        """``optional`` : widgets qui cèdent avant le bouton (puce du niveau, bouton de recherche) ; comptés comme absents."""
        super().__init__(bar)
        self._bar, self._button, self._optional, self._busy = bar, button, list(optional), False
        opt_out = getattr(button, "set_text_wrapping", None)
        if opt_out is not None:
            opt_out(False)        # un seul mot : pas de retour à la ligne, et ``WrapTextMixin`` ne doit pas rétablir la largeur minimale
        self._text_minimum = button.minimumWidth()   # ``IconButton`` impose « taille de l'icône + 60 px » à un bouton à libellé
        self._chrome = 0          # largeur du bouton moins son texte, mesurée sous la forme texte seulement
        bar.installEventFilter(self)

    def icon_only(self) -> bool:
        return self._button.toolButtonStyle() == Qt.ToolButtonIconOnly

    def fit(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            button = self._button
            text = button.text()
            if not self.icon_only():
                self._chrome = button.sizeHint().width() - button.fontMetrics().horizontalAdvance(text)
            wide = self._chrome + button.fontMetrics().horizontalAdvance(text)
            layout = self._bar.layout()
            margins = layout.contentsMargins()
            skipped = {id(button), *(id(w) for w in self._optional)}
            items = [layout.itemAt(i) for i in range(layout.count())]
            widgets = [it.widget() for it in items if it.widget() is not None]
            widgets = [w for w in widgets if id(w) not in skipped and not w.isHidden()]
            spacers = sum(1 for it in items if it.widget() is None)           # les ressorts ont aussi leurs espaces
            used = sum(w.sizeHint().width() for w in widgets) + margins.left() + margins.right()
            gaps = layout.spacing() * (len(widgets) + spacers + 1)             # y compris l'espace propre au bouton
            style = Qt.ToolButtonTextBesideIcon if used + gaps + wide <= self._bar.width() else Qt.ToolButtonIconOnly
            minimum = 0 if style == Qt.ToolButtonIconOnly else self._text_minimum
            if button.minimumWidth() != minimum:      # ``IconButton.setText`` (retraduction) rétablit sa largeur minimale
                button.setMinimumWidth(minimum)
            if style != button.toolButtonStyle():
                button.setToolButtonStyle(style)
        finally:
            self._busy = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt
        if event.type() in self._EVENTS:
            self.fit()
        return False


__all__ = ["FitSettingsButton"]
