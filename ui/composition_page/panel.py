"""Panneau Composition : les nœuds du clip de composition choisi et les réglages du nœud choisi.

En colonne (page Composition, à droite), l'éditeur de nœuds est au-dessus de l'inspecteur du nœud ; en bande large, à
gauche. L'en-tête ajoute un nœud après le nœud choisi (menu des types), retire le nœud choisi, et bascule le viewer
sur l'image du nœud choisi (à l'arrêt). Sans clip de composition, le panneau dit comment en obtenir un et propose d'en
créer un vide. Le panneau ne lit ni ne modifie le projet : la fenêtre lui donne la composition
(:meth:`CompositionPanel.set_target`) et applique ses demandes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QPushButton, QSplitter, QToolButton, QVBoxLayout, QWidget

from core.composition import OutputNode
from ui.composition_page.inspector import NodeInspector
from ui.composition_page.node_view import CompositionNodeView, add_menu
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.panel_header import PanelHeader
from ui.theme import set_role


class CompositionPanel(QWidget):
    """Nœuds et inspecteur d'une composition ; relaie les demandes de la vue et de l'inspecteur."""

    view_toggled = Signal(bool)
    new_requested = Signal()
    shown = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composition_panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PanelHeader(translate("comp.panel.title"), icon=IconName.NODES)
        self.view_button = self._header_button(IconName.EYE, "comp.panel.view_node", checkable=True)
        self.view_button.toggled.connect(self.view_toggled)
        self.add_button = self._header_button(IconName.PLUS, "comp.panel.add")
        self.add_button.setPopupMode(QToolButton.InstantPopup)
        self.add_menu = QMenu(self.add_button)
        self.add_actions = add_menu(self.add_menu)
        self.add_button.setMenu(self.add_menu)
        self.remove_button = self._header_button(IconName.TRASH, "comp.panel.remove")
        layout.addWidget(self.header)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        set_role(self.hint, "label-secondary")
        self.new_button = QPushButton()
        self.new_button.clicked.connect(self.new_requested)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.hint)
        empty_layout.addWidget(self.new_button, 0, Qt.AlignCenter)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty)

        self.nodes = CompositionNodeView()
        self.inspector = NodeInspector()
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.nodes)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 4)
        self._sized = False
        layout.addWidget(self.splitter, 1)

        for action, kind in self.add_actions.items():
            action.triggered.connect(lambda _checked=False, value=kind: self.nodes.add_requested.emit(
                value, self.nodes.current_id or ""))
        self.remove_button.clicked.connect(self._remove_current)
        self.retranslate()
        self.set_target(None, None, editable=False)

    def _header_button(self, icon: IconName, tooltip_key: str, *, checkable: bool = False) -> IconButton:
        button = IconButton(icon=icon, tooltip=translate(tooltip_key), size=Sizes.icon_button_sm, checkable=checkable)
        button.setProperty("tooltip_key", tooltip_key)
        self.header.add_trailing(button)
        return button

    def set_target(self, composition, current_id: str | None, *, editable: bool, assets=()) -> None:
        """La composition du clip choisi (``None`` : aucun clip de composition) et son nœud choisi."""
        self.empty.setVisible(composition is None)
        self.splitter.setVisible(composition is not None)
        for button in (self.add_button, self.view_button):
            button.setEnabled(composition is not None and editable)
        if composition is None:
            self.nodes.set_graph(None, None)
            self.inspector.set_node(None)
            self.remove_button.setEnabled(False)
            return
        graph = composition.graph
        node_id = current_id if current_id and graph.has_node(current_id) else graph.output.id
        self.nodes.set_graph(graph, node_id, editable=editable)
        self.inspector.set_assets(list(assets))
        node = graph.node(node_id)
        self.inspector.set_node(node, editable=editable)
        self.remove_button.setEnabled(editable and not isinstance(node, OutputNode))

    def set_viewing(self, shown: bool) -> None:
        self.view_button.blockSignals(True)
        self.view_button.setChecked(shown)
        self.view_button.blockSignals(False)

    def retranslate(self) -> None:
        self.header.title_label.setText(translate("comp.panel.title"))
        for button in (self.view_button, self.add_button, self.remove_button):
            button.setToolTip(translate(str(button.property("tooltip_key"))))
        self.hint.setText(translate("comp.panel.no_clip"))
        self.new_button.setText(translate("comp.panel.new"))
        self.add_menu.clear()
        self.add_actions = add_menu(self.add_menu)
        for action, kind in self.add_actions.items():
            action.triggered.connect(lambda _checked=False, value=kind: self.nodes.add_requested.emit(
                value, self.nodes.current_id or ""))
        self.inspector.retranslate()

    def _remove_current(self) -> None:
        graph = self.nodes.graph
        current = self.nodes.current_id
        if graph is not None and current and graph.has_node(current) and \
                not isinstance(graph.node(current), OutputNode):
            self.nodes.remove_requested.emit(current)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.shown.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # Les nœuds ont besoin de largeur : en colonne tant que le panneau n'est pas une bande très large.
        orientation = Qt.Vertical if self.height() >= self.width() * 0.45 else Qt.Horizontal
        if self.splitter.orientation() != orientation or not self._sized:
            self.splitter.setOrientation(orientation)
            total = self.splitter.height() if orientation == Qt.Vertical else self.splitter.width()
            if total > 0:
                self.splitter.setSizes([int(total * 0.55), int(total * 0.45)])
                self._sized = True


__all__ = ["CompositionPanel"]
