"""Libellé de bouton qui passe à la ligne au lieu d'être tronqué (« Affi...tion »).

Un ``QPushButton`` / ``QToolButton`` coupe son texte avec « ... » quand le layout lui donne moins que son ``sizeHint``.
Dans une colonne de ~216 px (inspecteur d'un portable) ou avec des polices de repli plus larges (Linux), c'est fréquent.
:class:`WrapTextMixin` coupe le texte **peint** en lignes selon la largeur disponible ; ``text()`` rend toujours le libellé
d'origine (manifeste des niveaux d'usage, accessibilité, tests). La largeur minimale devient celle du mot le plus long :
le bouton est plus haut, jamais élidé.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QAbstractButton, QPushButton, QToolButton


class WrapTextMixin:
    """À placer **avant** la classe Qt : ``class MyButton(WrapTextMixin, QPushButton)``."""

    _wrap_text = False
    _logical_text: str | None = None
    _rewrapping = False
    _wrap_base_min: int | None = None

    def set_text_wrapping(self, enabled: bool = True) -> None:
        self._wrap_text = bool(enabled)
        self._rewrap()

    def _wrap_overhead(self) -> int:
        """Largeur prise autour du texte (icône, flèche, espace, padding du style), lue sur le ``sizeHint`` réel du bouton."""
        widget: QAbstractButton = self                                  # type: ignore[assignment]
        shown = QAbstractButton.text(widget)
        widest_line = max((widget.fontMetrics().horizontalAdvance(line) for line in shown.split("\n")), default=0)
        return max(0, widget.sizeHint().width() - widest_line)

    def text(self) -> str:
        logical = self._logical_text
        return super().text() if logical is None else logical          # type: ignore[misc, no-any-return]

    def setText(self, text: str) -> None:  # noqa: N802 - API Qt
        self._logical_text = text
        super().setText(text)                                           # type: ignore[misc]
        self._rewrap()

    def _rewrap(self) -> None:
        if self._rewrapping or not self._wrap_text:
            return
        widget: QAbstractButton = self                                  # type: ignore[assignment]
        if self._logical_text is None:
            self._logical_text = QAbstractButton.text(widget)           # texte donné au constructeur
        logical = self._logical_text
        if self._wrap_base_min is None:
            self._wrap_base_min = widget.minimumWidth()
        metrics = widget.fontMetrics()
        overhead = self._wrap_overhead()
        prefix = metrics.horizontalAdvance("  ")
        widest = max((metrics.horizontalAdvance(word) + prefix for word in logical.split()), default=0)
        widget.setMinimumWidth(max(self._wrap_base_min, widest + overhead))
        shown = logical
        if widget.width() > 0:
            room = max(20, widget.width() - overhead - prefix)
            lines: list[str] = []
            current = ""
            for word in logical.split():
                trial = f"{current} {word}".strip()
                if current and metrics.horizontalAdvance(trial) > room:
                    lines.append(current)
                    current = word
                else:
                    current = trial
            lines.append(current)
            if len(lines) > 1:
                shown = "  " + "\n".join(lines)
        if QAbstractButton.text(widget) != shown:
            self._rewrapping = True
            try:
                QAbstractButton.setText(widget, shown)
            finally:
                self._rewrapping = False

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)                                      # type: ignore[misc]
        if self._wrap_text and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.Polish):
            self._rewrap()                                              # police ou feuille de style changée : largeurs à refaire

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)                                      # type: ignore[misc]
        if self._wrap_text:
            self._rewrap()


class WrappingPushButton(WrapTextMixin, QPushButton):
    """``QPushButton`` dont le libellé passe à la ligne."""

    def __init__(self, text: str = "", parent=None, **properties) -> None:
        super().__init__(text, parent, **properties)
        self.set_text_wrapping()


class WrappingToolButton(WrapTextMixin, QToolButton):
    """``QToolButton`` dont le libellé passe à la ligne."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.set_text_wrapping()
