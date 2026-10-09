"""Panneau Couleur : les nœuds d'étalonnage du clip sélectionné et les roues de son nœud courant.

En colonne (page Couleur, à droite) les nœuds sont au-dessus des roues ; en bande large (zone du bas) ils sont à
gauche. Le panneau ne lit ni ne modifie le projet : la fenêtre lui donne le graphe du clip (:meth:`ColorPanel.set_target`)
et applique ce qu'il demande (signaux de l'éditeur de nœuds et :attr:`ColorPanel.wheel_changed`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from core.color_grading import WHEELS
from core.color_nodes import ColorNodeGraph
from ui.color_page.node_editor import NodeEditor, node_number
from ui.color_page.wheels import ColorWheels
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.panel_header import PanelHeader
from ui.theme import set_role


class ColorPanel(QWidget):
    """Nœuds + roues ; :attr:`wheel_changed` (roue, valeur) pour le nœud courant."""

    wheel_changed = Signal(str, object)
    shown = Signal()                                   # affiché : la fenêtre le remet sur le clip courant

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PanelHeader(translate("color.panel.title"), icon=IconName.COLOR)
        self.node_caption = QLabel()
        set_role(self.node_caption, "label-secondary")
        self.header.add_trailing(self.node_caption)
        self.add_button = self._header_button(IconName.PLUS, "color.node.add")
        self.bypass_button = self._header_button(IconName.EYE_OFF, "color.node.bypass", checkable=True)
        self.reset_button = self._header_button(IconName.RESET, "color.node.reset")
        self.remove_button = self._header_button(IconName.TRASH, "color.node.remove")
        layout.addWidget(self.header)

        self.hint = QLabel(translate("color.panel.no_clip"))
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        set_role(self.hint, "label-secondary")
        self.hint.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.sm)
        layout.addWidget(self.hint)

        self.nodes = NodeEditor()
        self.wheels = ColorWheels()
        self.wheels.wheel_changed.connect(self.wheel_changed)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.nodes)
        self.splitter.addWidget(self.wheels)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 5)
        layout.addWidget(self.splitter, 1)

        self.add_button.clicked.connect(lambda: self.nodes.add_requested.emit(self.nodes.current_id or ""))
        self.bypass_button.clicked.connect(self._toggle_current)
        self.reset_button.clicked.connect(lambda: self._emit_for_current(self.nodes.reset_requested))
        self.remove_button.clicked.connect(lambda: self._emit_for_current(self.nodes.remove_requested))
        self.set_target(None, None, editable=False)

    def _header_button(self, icon: IconName, tooltip_key: str, *, checkable: bool = False) -> IconButton:
        button = IconButton(icon=icon, tooltip=translate(tooltip_key), size=Sizes.icon_button_sm, checkable=checkable)
        button.setProperty("tooltip_key", tooltip_key)
        self.header.add_trailing(button)
        return button

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_target(self, graph: ColorNodeGraph | None, current_id: str | None, *, editable: bool) -> None:
        """Graphe du clip affiché (``None`` : aucun clip vidéo sélectionné) et son nœud courant."""
        self.nodes.set_graph(graph, current_id)
        self.hint.setVisible(graph is None)
        self.splitter.setVisible(graph is not None)
        active = graph is not None and editable
        for widget in (self.nodes, self.wheels, self.add_button, self.bypass_button, self.reset_button):
            widget.setEnabled(active)
        if graph is None or current_id is None:
            self.node_caption.clear()
            self.remove_button.setEnabled(False)
            self.bypass_button.setChecked(False)
            self.wheels.set_wheels({})
            return
        node = graph.node_or_first(current_id)
        self.remove_button.setEnabled(active and len(graph.nodes) > 1)
        self.bypass_button.setChecked(not node.enabled)
        caption = translate("color.panel.node_caption", number=node_number(graph.position(node.id)),
                            count=node_number(len(graph.nodes) - 1))
        self.node_caption.setText(f"{caption} · {node.label}" if node.label else caption)
        self.wheels.set_wheels({name: getattr(node.grade, name) for name in WHEELS})

    def retranslate(self) -> None:
        self.header.title_label.setText(translate("color.panel.title"))
        self.hint.setText(translate("color.panel.no_clip"))
        for button in (self.add_button, self.bypass_button, self.reset_button, self.remove_button):
            button.setToolTip(translate(str(button.property("tooltip_key"))))
        self.nodes.setAccessibleName(translate("color.nodes.title"))
        self.wheels.retranslate()
        graph, current = self.nodes.graph, self.nodes.current_id
        self.nodes.set_graph(None, None)                          # libellés des nœuds et des bornes redessinés
        self.set_target(graph, current, editable=self.wheels.isEnabled())

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.shown.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # Colonne (plus haute que large) : nœuds au-dessus des roues ; bande : nœuds à gauche.
        orientation = Qt.Vertical if self.height() >= self.width() * 0.8 else Qt.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)

    # -- commandes ----------------------------------------------------------------------------------------------

    def _emit_for_current(self, signal) -> None:
        if self.nodes.graph is not None:
            signal.emit(self.nodes.current_id or self.nodes.graph.nodes[0].id)

    def _toggle_current(self) -> None:
        self._emit_for_current(self.nodes.toggle_requested)
