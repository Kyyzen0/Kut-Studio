"""Section pliable d'un panneau : un titre cliquable (avec chevron) au-dessus d'un contenu qui se replie.

``SectionBox`` est un ``QGroupBox`` : tout le code existant qui y pose un layout (``QVBoxLayout(group)``) ou y cherche des
champs continue de fonctionner. Elle y ajoute :

* un **chevron** peint au bord gauche du titre (ouvert ``˅``, fermé ``>``), de la couleur du texte secondaire du thème ;
* le **pliage** : un clic sur l'en-tête, ou Espace / Entrée quand il a le focus, masque le contenu. Le focus clavier se voit
  (un filet d'anneau de focus autour de l'en-tête), comme pour tout contrôle atteint avec Tab ;
* la **mémoire de session** : une section repliée le reste quand l'inspecteur est reconstruit pour un autre clip.

Le contenu est masqué en *cachant* ses widgets directs (et non en rognant la hauteur : un layout dont le minimum dépasse le
maximum du groupe gagnerait toujours). On retient quels widgets étaient visibles pour ne rouvrir que ceux-là : la logique qui
montre ou cache un champ selon le clip (par exemple la qualité du flux optique) n'est jamais contredite.
"""

from __future__ import annotations

from functools import lru_cache

import shiboken6
from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGroupBox, QStyle, QStyleOptionGroupBox, QWidget

from ui.design_system import Iconography, Radius
from ui.icons import IconName, make_icon
from ui.theme import active_palette


@lru_cache(maxsize=16)
def _chevron(opened: bool, color: str):
    """Le pixmap du chevron (ouvert : vers le bas ; fermé : vers la droite), en cache : peint à chaque repeint de l'en-tête."""
    icon = make_icon(IconName.CHEVRON_DOWN if opened else IconName.CHEVRON_RIGHT, size=Iconography.sm, color=QColor(color))
    return icon.pixmap(Iconography.sm, Iconography.sm)


class SectionBox(QGroupBox):
    """``QGroupBox`` repliable. ``key`` (la clé de traduction du titre) identifie la section pour la mémoire de session."""

    open_changed = Signal(bool)

    is_section_header = True
    """Reconnu par :func:`ui.keyboard_navigation.follow_layout_order` : l'en-tête est un arrêt de Tab, avant le contenu."""

    _remembered: dict[str, bool] = {}
    """État ouvert / fermé des sections, par clé, pour la durée de la session (jamais écrit sur disque)."""

    def __init__(self, title: str = "", parent: QWidget | None = None, *, key: str = "", open_by_default: bool = True) -> None:
        super().__init__(title, parent)
        self.setProperty("collapsible", True)          # la feuille de style réserve la place du chevron à gauche du titre
        # Atteint avec Tab, jamais au clic : un contrôle qui prend le focus au clic vole les touches de lecture (Espace).
        self.setFocusPolicy(Qt.TabFocus)
        self._key = key
        self._open = True
        self._folded: list[QWidget] = []
        self._busy = False
        self._margins = None                           # marges du contenu avant le pliage
        wanted = self._remembered.get(key, open_by_default) if key else open_by_default
        if not wanted:
            QTimer.singleShot(0, lambda: self.set_open(False))   # une fois le contenu construit

    # -- état ---------------------------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, value: bool) -> None:
        """Ouvre ou replie la section ; sans effet si elle est déjà dans cet état."""
        value = bool(value)
        if value == self._open:
            return
        self._open = value
        self._busy = True
        try:
            if not value:
                self._folded = [w for w in self.findChildren(QWidget, options=Qt.FindDirectChildrenOnly) if not w.isHidden()]
                for widget in self._folded:
                    widget.installEventFilter(self)
                    widget.hide()
            else:
                for widget in self._folded:
                    if shiboken6.isValid(widget):
                        widget.removeEventFilter(self)
                        widget.show()
                self._folded = []
        finally:
            self._busy = False
        # Repliée, la section se réduit à son en-tête : plus de marges de contenu ni de remplissage (la feuille de style lit ``folded``).
        layout = self.layout()
        if layout is not None:
            if not value:
                self._margins = layout.contentsMargins()
                layout.setContentsMargins(0, 0, 0, 0)
            elif self._margins is not None:
                layout.setContentsMargins(self._margins)
                self._margins = None
        self.setProperty("folded", not value)
        self.style().unpolish(self)
        self.style().polish(self)
        if self._key:
            self._remembered[self._key] = value
        self.updateGeometry()
        self.update()
        self.open_changed.emit(value)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt
        """Replié, un contenu que la logique réaffiche est recaché ; qu'elle cache, il ne sera pas rouvert."""
        if not self._open and not self._busy and watched in self._folded:
            if event.type() == QEvent.Show:
                QTimer.singleShot(0, watched.hide)
            elif event.type() == QEvent.HideToParent:
                self._folded = [w for w in self._folded if w is not watched]
        return False

    # -- en-tête ------------------------------------------------------------------------------------------

    def _label_rect(self) -> QRect:
        option = QStyleOptionGroupBox()
        self.initStyleOption(option)
        return self.style().subControlRect(QStyle.CC_GroupBox, option, QStyle.SC_GroupBoxLabel, self)

    def header_rect(self) -> QRect:
        """La zone qui replie la section : toute la largeur, à hauteur du titre."""
        label = self._label_rect()
        return QRect(0, 0, self.width(), max(label.bottom() + 6, 20))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.button() == Qt.LeftButton and self.header_rect().contains(event.position().toPoint()):
            self.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        super().paintEvent(event)
        palette = active_palette()
        label = self._label_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pixmap = _chevron(self._open, palette.muted_strong)
        painter.drawPixmap(0, label.center().y() - pixmap.height() // 2 + 1, pixmap)
        if self.hasFocus():
            painter.setPen(QPen(QColor(palette.focus_ring), 1))
            painter.setBrush(Qt.NoBrush)
            header = self.header_rect()
            painter.drawRoundedRect(header.adjusted(0, 0, -1, -1), Radius.sm, Radius.sm)
        painter.end()


__all__ = ["SectionBox"]
