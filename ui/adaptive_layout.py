"""Briques de mise en page pour les petites tailles de fenêtre.

L'interface doit tenir à 1180 × 720 (minimum de la fenêtre principale) ; l'inspecteur n'y fait que 280 px. Quand la
place manque, un bloc ne doit ni se faire écraser sous sa taille minimale (texte superposé à son voisin) ni sortir de
son conteneur (contrôles rognés) : il doit **céder** proprement. Ce module regroupe les façons de céder :

* :class:`ShrinkableScrollArea` : un bloc de hauteur naturelle qui accepte d'être plus bas et défile alors ;
* :class:`FlowLayout` : une rangée de boutons qui passe à la ligne au lieu de forcer la largeur ;
* :class:`WrappingCheckBox` : une case à cocher dont le libellé passe à la ligne ;
* :class:`ElidedLabel` : un libellé d'une ligne qui se tronque avec « … » au lieu d'imposer sa largeur ;
* :func:`make_shrinkable` : une liste déroulante qui accepte d'être plus étroite que son plus long libellé ;
* :func:`allow_shrinking` : un bouton qui accepte d'être comprimé (libellé coupé) en dernier recours.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLayout,
    QLayoutItem,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QWidget,
)


class ShrinkableScrollArea(QScrollArea):
    """Zone défilante verticale qui réclame la hauteur de son contenu, mais accepte d'être plus basse.

    ``QScrollArea`` mémorise la taille de son contenu et ne la remet pas à jour quand le contenu change (une barre
    de puces qui passe à la ligne, par exemple) : le parent garderait une hauteur périmée et ses enfants se
    chevaucheraient. Ici le ``sizeHint`` est relu à chaque demande, et un changement de layout du contenu prévient
    les layouts parents.

    Args:
        content: widget défilant (il reste le propriétaire de son style).
        min_height: hauteur plancher (px) en dessous de laquelle le bloc ne descend pas ; il défile au-delà.
    """

    def __init__(self, content: QWidget, min_height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_height = int(min_height)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Sans fond explicite, le viewport peint la couleur claire par défaut de la plateforme.
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setWidget(content)
        content.setAutoFillBackground(False)  # ``setWidget`` l'active : le contenu peindrait le fond clair du système
        content.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self.widget() and event.type() == QEvent.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        content = self.widget()
        if content is None:
            return super().sizeHint()
        hint = content.sizeHint()
        # Un contenu dont la hauteur dépend de la largeur (rangée de puces) : on la lit à la largeur réelle.
        width = self.viewport().width()
        if content.hasHeightForWidth() and width > 0:
            hint.setHeight(max(hint.height(), content.heightForWidth(width)))
        return hint + QSize(2 * self.frameWidth(), 2 * self.frameWidth())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        content = self.widget()
        width = content.minimumSizeHint().width() if content is not None else 0
        return QSize(width + 2 * self.frameWidth(), self._min_height)


class FlowLayout(QLayout):
    """Rangée de widgets à leur taille naturelle qui passe à la ligne quand la largeur manque.

    Contrairement à une ``QHBoxLayout`` (qui comprime les boutons sous leur taille naturelle, ou impose une largeur
    minimale égale à leur somme), la largeur minimale est celle du plus large élément. La hauteur dépend de la
    largeur (``heightForWidth``) : tous les layouts parents d'un panneau défilant la propagent.

    Args:
        spacing: espace entre éléments, horizontal et vertical (px).
    """

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - API Qt
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - API Qt
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - API Qt
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - API Qt
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - API Qt
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - API Qt
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - API Qt
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - API Qt
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _place(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            # Un élément plus large que la ligne est borné à la ligne (il se comprime au lieu de déborder).
            width = min(hint.width(), max(area.width(), item.minimumSize().width()))
            if x > area.x() and x + width > area.right() + 1:
                x = area.x()
                y += line_height + spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class WrappingCheckBox(QCheckBox):
    """Case à cocher dont le libellé passe à la ligne (``QCheckBox`` coupe ou impose sa largeur).

    C'est une vraie ``QCheckBox`` (état, signaux, nom accessible, focus) : seuls le dessin du libellé et les
    tailles changent. Sa largeur minimale est celle du mot le plus long ; sa hauteur dépend de la largeur.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def _label_geometry(self) -> tuple[int, int]:
        """Largeur occupée par l'indicateur et l'espace qui le suit, et hauteur minimale de la case."""
        option = QStyleOptionButton()
        self.initStyleOption(option)
        style = self.style()
        indicator = style.pixelMetric(QStyle.PM_IndicatorWidth, option, self)
        spacing = style.pixelMetric(QStyle.PM_CheckBoxLabelSpacing, option, self)
        height = style.pixelMetric(QStyle.PM_IndicatorHeight, option, self)
        return indicator + spacing, height

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        lead, indicator_height = self._label_geometry()
        metrics = self.fontMetrics()
        return QSize(lead + metrics.horizontalAdvance(self.text()), max(indicator_height, metrics.height()))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        lead, indicator_height = self._label_geometry()
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(word) for word in self.text().split()), default=0)
        return QSize(lead + widest, max(indicator_height, metrics.height()))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - API Qt
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - API Qt
        lead, indicator_height = self._label_geometry()
        box = self.fontMetrics().boundingRect(QRect(0, 0, max(1, width - lead), 10_000), Qt.TextWordWrap, self.text())
        return max(indicator_height, box.height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        painter.drawControl(QStyle.CE_CheckBox, option)  # l'indicateur (et son focus), sans libellé
        lead, _ = self._label_geometry()
        area = QRect(lead, 0, max(1, self.width() - lead), self.height())
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        painter.setPen(self.palette().color(group, QPalette.WindowText))
        painter.drawText(area, int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter), text)


class ElidedLabel(QLabel):
    """Libellé d'une ligne qui se tronque avec « … » quand la place manque (le texte complet est en infobulle).

    Un ``QLabel`` impose la largeur de son texte : un titre long poussait la carte qui le porte au-delà de sa
    colonne, et ses boutons d'action sortaient de la zone visible.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 - API Qt
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return QSize(self.fontMetrics().horizontalAdvance("…") * 3, super().minimumSizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        painter.setPen(self.palette().color(QPalette.Active if self.isEnabled() else QPalette.Disabled,
                                            QPalette.WindowText))
        painter.drawText(self.contentsRect(), int(self.alignment()), text)


def make_shrinkable(combo: QComboBox, characters: int = 8) -> QComboBox:
    """Autorise une liste déroulante à rétrécir (le libellé est alors coupé) plutôt que de forcer sa largeur.

    Par défaut sa largeur minimale est celle de son plus long libellé : dans un inspecteur de 280 px, une rangée
    « liste + bouton » ne tient plus. ``setMinimumContentsLength`` ne borne que la largeur *minimale* : la largeur
    préférée reste celle du plus long libellé (rien ne change quand la place ne manque pas), et la liste ouverte
    garde la largeur de ses entrées.

    À appeler à la construction, avant que la taille du contrôle ait été lue : avec la politique par défaut, Qt ne
    vide pas ses tailles mises en cache et un appel tardif resterait sans effet.
    """
    combo.setMinimumContentsLength(characters)
    return combo


def allow_shrinking(widget: QWidget, minimum: int | None = None) -> QWidget:
    """Autorise un contrôle à être plus étroit que sa taille préférée (dernier recours : son contenu est alors coupé).

    Les boutons et les champs numériques ont une politique de taille « Minimum » : leur largeur minimale *dans un
    layout* est leur ``sizeHint`` et non leur ``minimumSizeHint``. Dans une rangée de deux champs à 280 px, leur
    somme force le conteneur à dépasser.

    * ``minimum=None`` : la politique passe à « Preferred », le layout peut descendre jusqu'au
      ``minimumSizeHint`` du contrôle (rien n'est coupé : pour les champs numériques) ;
    * ``minimum=N`` : largeur minimale explicite de N px (pour un bouton, dont le ``minimumSizeHint`` est déjà
      son ``sizeHint`` : le libellé est coupé en dessous).

    Préférer :class:`FlowLayout` quand les libellés ne doivent jamais être coupés.
    """
    if minimum is not None:
        widget.setMinimumWidth(minimum)
    else:
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Preferred)
        widget.setSizePolicy(policy)
    return widget


__all__ = [
    "ElidedLabel",
    "FlowLayout",
    "ShrinkableScrollArea",
    "WrappingCheckBox",
    "allow_shrinking",
    "make_shrinkable",
]
