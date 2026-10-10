"""Panneau Couleur : les nœuds d'étalonnage du clip sélectionné ; les roues, le qualifieur, les fenêtres, le flou et
la netteté de son nœud courant.

En colonne (page Couleur, à droite) les nœuds sont au-dessus des onglets *Roues* / *Qualificateur* / *Fenêtres* /
*Flou* ; en bande large
(zone du bas) ils sont à gauche. Le bouton ``+`` ajoute un nœud en série ; sa flèche propose aussi un nœud parallèle
ou de calque. Le panneau ne lit ni ne modifie le projet : la fenêtre lui donne le graphe du clip
(:meth:`ColorPanel.set_target`) et applique ce qu'il demande (signaux de l'éditeur de nœuds, :attr:`wheel_changed`,
:attr:`qualifier_changed`, :attr:`highlight_toggled`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget

from core.color_grading import WHEELS
from core.color_nodes import ColorNodeGraph, MixerKind
from ui.color_page.node_editor import NodeEditor, node_number
from ui.color_page.qualifier import QualifierEditor
from ui.color_page.wheels import ColorWheels
from ui.color_page.windows import DetailEditor, WindowsEditor
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.panel_header import PanelHeader
from ui.theme import set_role


class ColorPanel(QWidget):
    """Nœuds, roues, qualifieur ; :attr:`wheel_changed` (roue, valeur) et :attr:`qualifier_changed` (qualifieur)
    pour le nœud courant, :attr:`highlight_toggled` pour le moniteur."""

    wheel_changed = Signal(str, object)
    qualifier_changed = Signal(object)
    highlight_toggled = Signal(bool)
    pick_toggled = Signal(bool)
    windows_changed = Signal(object, str)              # fenêtres du nœud courant, clé d'historique
    window_selected = Signal(str)                      # fenêtre éditée dans le viewer ("" : aucune)
    detail_changed = Signal(float, float)              # flou, netteté du nœud courant
    compare_toggled = Signal(bool)                     # comparaison avant / après dans le moniteur
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
        self.compare_button = self._header_button(IconName.COMPARE, "color.compare", checkable=True)
        self.compare_button.toggled.connect(self.compare_toggled)
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
        self.qualifier = QualifierEditor()
        self.qualifier.changed.connect(self.qualifier_changed)
        self.qualifier.highlight_toggled.connect(self.highlight_toggled)
        self.qualifier.pick_toggled.connect(self.pick_toggled)
        self.windows = WindowsEditor()
        self.windows.changed.connect(self.windows_changed)
        self.windows.selected.connect(lambda _window_id: self.window_selected.emit(self.editing_window()))
        self.windows.highlight_toggled.connect(self.highlight_toggled)
        self.detail = DetailEditor()
        self.detail.changed.connect(self.detail_changed)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("color_tabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.wheels, translate("color.tab.wheels"))
        self.tabs.addTab(self.qualifier, translate("color.tab.qualifier"))
        self.tabs.addTab(self.windows, translate("color.tab.windows"))
        self.tabs.addTab(self.detail, translate("color.tab.detail"))
        self.tabs.currentChanged.connect(lambda _index: self.window_selected.emit(self.editing_window()))
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.nodes)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 5)
        layout.addWidget(self.splitter, 1)

        self.add_button.clicked.connect(lambda: self.nodes.add_requested.emit(self.nodes.current_id or ""))
        self.add_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.add_button.setFixedWidth(Sizes.icon_button_sm + 14)            # la flèche du menu à côté du +
        self.add_menu = QMenu(self.add_button)
        self.add_actions = {"serial": self.add_menu.addAction(translate("color.node.add"))}
        self.add_actions["serial"].triggered.connect(self.add_button.click)
        for kind in (MixerKind.PARALLEL, MixerKind.LAYER):
            action = self.add_menu.addAction(translate(f"color.node.add_{kind.value}"))
            action.triggered.connect(lambda _checked=False, value=kind.value: self._branch(value))
            self.add_actions[kind.value] = action
        self.add_button.setMenu(self.add_menu)
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
        for widget in (self.nodes, self.wheels, self.qualifier, self.windows, self.detail, self.add_button,
                       self.bypass_button, self.reset_button):
            widget.setEnabled(active)
        if graph is None or current_id is None:
            self.node_caption.clear()
            self.remove_button.setEnabled(False)
            self.bypass_button.setChecked(False)
            self.wheels.set_wheels({})
            self.qualifier.set_qualifier(None)
            self.windows.set_windows(())
            self.detail.set_detail(0.0, 0.0)
            return
        node = graph.node_or_first(current_id)
        self.remove_button.setEnabled(active and len(graph.correctors) > 1)
        self.bypass_button.setChecked(not node.enabled)
        caption = translate("color.panel.node_caption", number=node_number(graph.position(node.id)),
                            count=node_number(len(graph.correctors) - 1))
        self.node_caption.setText(f"{caption} · {node.label}" if node.label else caption)
        self.wheels.set_wheels({name: getattr(node.grade, name) for name in WHEELS})
        self.qualifier.set_qualifier(node.qualifier)
        self.windows.set_windows(node.windows)
        self.detail.set_detail(node.blur, node.sharpen)

    def editing_window(self) -> str:
        """La fenêtre que le viewer édite : celle choisie dans l'onglet *Fenêtres*, s'il est affiché ("" sinon)."""
        return self.windows.current_id() if self.tabs.currentWidget() is self.windows else ""

    def set_compare(self, shown: bool) -> None:
        """État du bouton de comparaison avant / après (la fenêtre en garde la valeur), sans émettre."""
        self.compare_button.blockSignals(True)
        self.compare_button.setChecked(shown)
        self.compare_button.blockSignals(False)

    def set_highlight(self, shown: bool) -> None:
        """État du bouton *Afficher la sélection* (la fenêtre en garde la valeur), sans émettre."""
        button = self.qualifier.highlight_button
        button.blockSignals(True)
        button.setChecked(shown)
        button.blockSignals(False)
        self.windows.set_highlight(shown)

    def retranslate(self) -> None:
        self.header.title_label.setText(translate("color.panel.title"))
        self.hint.setText(translate("color.panel.no_clip"))
        for button in (self.compare_button, self.add_button, self.bypass_button, self.reset_button,
                       self.remove_button):
            button.setToolTip(translate(str(button.property("tooltip_key"))))
        self.nodes.setAccessibleName(translate("color.nodes.title"))
        self.wheels.retranslate()
        self.qualifier.retranslate()
        self.windows.retranslate()
        self.detail.retranslate()
        for index, key in enumerate(("color.tab.wheels", "color.tab.qualifier", "color.tab.windows",
                                     "color.tab.detail")):
            self.tabs.setTabText(index, translate(key))
        self.add_actions["serial"].setText(translate("color.node.add"))
        for kind in (MixerKind.PARALLEL, MixerKind.LAYER):
            self.add_actions[kind.value].setText(translate(f"color.node.add_{kind.value}"))
        graph, current = self.nodes.graph, self.nodes.current_id
        self.nodes.set_graph(None, None)                          # libellés des nœuds et des bornes redessinés
        self.set_target(graph, current, editable=self.wheels.isEnabled())

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.shown.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # Colonne (plus haute que large) : nœuds au-dessus des onglets ; bande : nœuds à gauche.
        orientation = Qt.Vertical if self.height() >= self.width() * 0.8 else Qt.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)

    # -- commandes ----------------------------------------------------------------------------------------------

    def _emit_for_current(self, signal) -> None:
        if self.nodes.graph is not None:
            signal.emit(self.nodes.graph.node_or_first(self.nodes.current_id).id)

    def _branch(self, kind: str) -> None:
        if self.nodes.graph is not None:
            self.nodes.branch_requested.emit(self.nodes.graph.node_or_first(self.nodes.current_id).id, kind)

    def _toggle_current(self) -> None:
        self._emit_for_current(self.nodes.toggle_requested)
