"""Éditeur de nœuds d'étalonnage : la chaîne d'un clip, de la source à la sortie.

Une scène ``QGraphicsView`` (la composition nodale réutilisera la même vue, avec des nœuds placés librement) : un nœud
par réglage, numéroté dans l'ordre de la chaîne, relié au précédent et au suivant. Le nœud courant (bordure d'accent)
est celui que les roues et l'inspecteur modifient ; un nœud contourné est en pointillé, atténué ; une pastille
signale un nœud qui change l'image.

L'éditeur ne modifie rien lui-même : il émet des demandes (sélection, ajout, suppression, contournement, nom,
déplacement, remise à zéro) que la fenêtre applique au projet, avec une étape d'historique, puis réaffiche le graphe
(:meth:`NodeEditor.set_graph`). Une demande née d'un geste sur un nœud part après la fin de l'événement : la scène
reconstruite détruit le nœud, qui ne doit plus être en train de le traiter. Au clavier, tant qu'il a le focus : Alt+S
ajoute un nœud après le nœud courant, Ctrl+D le contourne, Suppr le supprime (et jamais le clip de la timeline),
Entrée le renomme.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QWidget,
)

from core.color_nodes import MAX_LABEL_LENGTH, ColorNode, ColorNodeGraph
from ui.design_system import Radius, Typography, Weights
from ui.i18n import translate
from ui.theme import COLORS

NODE_WIDTH = 96.0
NODE_HEIGHT = 56.0
NODE_GAP = 44.0
TERMINAL_RADIUS = 7.0
PORT_RADIUS = 4.0
MIN_ZOOM = 0.5


def node_number(position: int) -> str:
    """Numéro affiché d'un nœud (``01``, ``02``…) : son rang dans la chaîne."""
    return f"{position + 1:02d}"


class _NodeItem(QGraphicsItem):
    """Un nœud de la chaîne ; glissé à l'horizontale pour changer sa place."""

    def __init__(self, editor: "NodeEditor", node: ColorNode, position: int) -> None:
        super().__init__()
        self.editor = editor
        self.node = node
        self.position = position
        self._press_x: float | None = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(translate("color.node.tip"))

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return QRectF(-PORT_RADIUS - 1, -3, NODE_WIDTH + 2 * PORT_RADIUS + 2, NODE_HEIGHT + 6)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        current = self.editor.current_id == self.node.id
        enabled = self.node.enabled
        rect = QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT)
        border = QPen(QColor(COLORS["accent"] if current else COLORS["border_strong"]), 2.0 if current else 1.0)
        if not enabled:
            border.setStyle(Qt.DashLine)
        painter.setPen(border)
        painter.setBrush(QColor(COLORS["panel_elevated"] if enabled else COLORS["panel_alt"]))
        painter.drawRoundedRect(rect, Radius.md, Radius.md)

        number_font = QFont(painter.font())
        number_font.setPixelSize(Typography.caption)
        number_font.setWeight(QFont.Weight(Weights.bold))
        painter.setFont(number_font)
        painter.setPen(QColor(COLORS["muted_strong"] if enabled else COLORS["disabled_text"]))
        painter.drawText(QRectF(8, 5, NODE_WIDTH - 16, 14), Qt.AlignLeft | Qt.AlignVCenter, node_number(self.position))
        if self.node.is_active():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawEllipse(QPointF(NODE_WIDTH - 11, 12), 3.5, 3.5)

        label_font = QFont(painter.font())
        label_font.setPixelSize(Typography.small)
        label_font.setWeight(QFont.Weight(Weights.medium))
        painter.setFont(label_font)
        painter.setPen(QColor(COLORS["text"] if enabled else COLORS["disabled_text"]))
        label = self.node.label or (translate("color.node.bypassed") if not enabled else translate("color.node.default"))
        elided = QFontMetricsF(label_font).elidedText(label, Qt.ElideRight, NODE_WIDTH - 16)
        painter.drawText(QRectF(8, 22, NODE_WIDTH - 16, NODE_HEIGHT - 28), Qt.AlignLeft | Qt.AlignVCenter, elided)

        painter.setPen(QPen(QColor(COLORS["panel"]), 1))
        painter.setBrush(QColor(COLORS["muted_strong"]))
        for x in (0.0, NODE_WIDTH):
            painter.drawEllipse(QPointF(x, NODE_HEIGHT / 2), PORT_RADIUS, PORT_RADIUS)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.LeftButton:
            self._press_x = event.scenePos().x() - self.x()
            self.setCursor(Qt.ClosedHandCursor)
            self.editor.request_select(self.node.id)
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._press_x is None:
            return
        self.setX(event.scenePos().x() - self._press_x)
        self.setZValue(1)
        self.editor.update_links()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._press_x is None:
            return
        self._press_x = None
        self.setZValue(0)
        self.setCursor(Qt.OpenHandCursor)
        self.editor.drop(self)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.editor.request_rename(self.node.id)


class NodeEditor(QGraphicsView):
    """Chaîne de nœuds d'un clip ; n'émet que des demandes, la fenêtre les applique."""

    node_selected = Signal(str)
    add_requested = Signal(str)                    # après ce nœud ("" : en fin de chaîne)
    remove_requested = Signal(str)
    toggle_requested = Signal(str)
    rename_requested = Signal(str, str)
    move_requested = Signal(str, int)
    reset_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_node_editor")
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(int(NODE_HEIGHT) + 56)
        self.graph: ColorNodeGraph | None = None
        self.current_id: str | None = None
        self._items: list[_NodeItem] = []
        self._links: list[QGraphicsPathItem] = []
        self._terminals: tuple[QPointF, QPointF] = (QPointF(), QPointF())
        self.setAccessibleName(translate("color.nodes.title"))
        self._paint_background()

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_graph(self, graph: ColorNodeGraph | None, current_id: str | None) -> None:
        """Réaffiche ``graph`` (``None`` : aucun clip étalonnable) avec ``current_id`` mis en avant ; un graphe
        inchangé n'est pas reconstruit (changer de nœud courant ne fait que repeindre)."""
        unchanged = graph is not None and graph == self.graph and bool(self._items)
        self.graph = graph
        self.current_id = current_id
        if unchanged:
            self.scene().update()
            return
        scene = self.scene()
        scene.clear()
        self._items, self._links = [], []
        if graph is None:
            return
        chain = graph.order()
        start_x = 2 * TERMINAL_RADIUS + NODE_GAP
        for position, node in enumerate(chain):
            item = _NodeItem(self, node, position)
            item.setPos(start_x + position * (NODE_WIDTH + NODE_GAP), 0.0)
            scene.addItem(item)
            self._items.append(item)
        end_x = start_x + len(chain) * (NODE_WIDTH + NODE_GAP)          # bord droit du dernier nœud + un écart
        self._terminals = (QPointF(TERMINAL_RADIUS, NODE_HEIGHT / 2), QPointF(end_x + TERMINAL_RADIUS, NODE_HEIGHT / 2))
        for _link in range(len(chain) + 1):
            link = QGraphicsPathItem()
            link.setPen(QPen(QColor(COLORS["muted"]), 1.5))
            link.setZValue(-1)
            scene.addItem(link)
            self._links.append(link)
        self._add_terminals()
        self.update_links()
        bounds = scene.itemsBoundingRect().adjusted(-12, -12, 12, 12)
        scene.setSceneRect(bounds)
        self._fit()

    def _fit(self) -> None:
        """Toute la chaîne visible : réduite jusqu'à :data:`MIN_ZOOM` si la place manque (puis défilement), jamais
        agrandie."""
        bounds = self.scene().sceneRect()
        viewport = self.viewport().rect()
        if bounds.isEmpty() or viewport.isEmpty():
            return
        zoom = max(MIN_ZOOM, min(1.0, viewport.width() / bounds.width(), viewport.height() / bounds.height()))
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(bounds.center())

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._fit()

    def _add_terminals(self) -> None:
        scene = self.scene()
        caption_font = QFont(self.font())
        caption_font.setPixelSize(Typography.caption)
        for point, key in zip(self._terminals, ("color.nodes.source", "color.nodes.output")):
            dot = scene.addEllipse(QRectF(point.x() - TERMINAL_RADIUS, point.y() - TERMINAL_RADIUS,
                                          2 * TERMINAL_RADIUS, 2 * TERMINAL_RADIUS),
                                   QPen(QColor(COLORS["panel"]), 1), QColor(COLORS["success"]))
            dot.setToolTip(translate(key))
            caption = scene.addSimpleText(translate(key), caption_font)
            caption.setBrush(QColor(COLORS["muted"]))
            width = caption.boundingRect().width()
            caption.setPos(point.x() - width / 2, point.y() + TERMINAL_RADIUS + 3)

    def update_links(self) -> None:
        """Retrace les liens (source → nœuds → sortie), dans l'ordre affiché des nœuds."""
        if not self._links:
            return
        ordered = sorted(self._items, key=lambda item: item.x())
        points = [self._terminals[0]]
        for item in ordered:
            points += [QPointF(item.x(), NODE_HEIGHT / 2), QPointF(item.x() + NODE_WIDTH, NODE_HEIGHT / 2)]
        points.append(self._terminals[1])
        for index, link in enumerate(self._links):
            start, end = points[2 * index], points[2 * index + 1]
            path = QPainterPath(start)
            bend = max(12.0, abs(end.x() - start.x()) / 2)
            path.cubicTo(QPointF(start.x() + bend, start.y()), QPointF(end.x() - bend, end.y()), end)
            link.setPath(path)

    def _paint_background(self) -> None:
        self.setBackgroundBrush(QColor(COLORS["panel_alt"]))

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._paint_background()

    # -- demandes -----------------------------------------------------------------------------------------------

    def request_select(self, node_id: str) -> None:
        if node_id != self.current_id:
            self.current_id = node_id
            self.scene().update()
            self.node_selected.emit(node_id)

    def request_rename(self, node_id: str) -> None:
        if self.graph is None or not self.isEnabled():
            return
        label = self._ask_label(self.graph.node(node_id).label)
        if label is not None:
            cleaned = label.strip()[:MAX_LABEL_LENGTH]
            QTimer.singleShot(0, lambda: self.rename_requested.emit(node_id, cleaned))

    def _ask_label(self, current: str) -> str | None:
        """Nom saisi (``None`` : annulé) ; isolé pour que les tests le remplacent."""
        text, accepted = QInputDialog.getText(self, translate("color.node.rename_title"),
                                              translate("color.node.rename_label"), text=current)
        return text if accepted else None

    def drop(self, item: _NodeItem) -> None:
        """Fin d'un glisser : la place du nœud parmi les autres, d'après son centre."""
        center = item.x() + NODE_WIDTH / 2
        index = sum(1 for other in self._items if other is not item and other.x() + NODE_WIDTH / 2 < center)
        if index != item.position:
            node_id = item.node.id
            QTimer.singleShot(0, lambda: self.move_requested.emit(node_id, index))
        else:
            item.setX(2 * TERMINAL_RADIUS + NODE_GAP + item.position * (NODE_WIDTH + NODE_GAP))
            self.update_links()

    def _node_at(self, view_pos) -> _NodeItem | None:
        for item in self.items(view_pos):
            if isinstance(item, _NodeItem):
                return item
        return None

    # -- menu et clavier ----------------------------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.graph is None or not self.isEnabled():
            return
        item = self._node_at(event.pos())
        if item is not None:
            self.request_select(item.node.id)
        menu = self.build_menu(item.node.id if item is not None else None)
        menu.exec(event.globalPos())

    def build_menu(self, node_id: str | None) -> QMenu:
        """Menu contextuel d'un nœud (``None`` : fond de l'éditeur, on ajoute en fin de chaîne)."""
        menu = QMenu(self)
        add = menu.addAction(translate("color.node.add"))
        add.triggered.connect(lambda: self.add_requested.emit(node_id or ""))
        if node_id is None or self.graph is None:
            return menu
        enabled = self.graph.node(node_id).enabled
        toggle = menu.addAction(translate("color.node.bypass" if enabled else "color.node.enable"))
        toggle.triggered.connect(lambda: self.toggle_requested.emit(node_id))
        rename = menu.addAction(translate("color.node.rename"))
        rename.triggered.connect(lambda: self.request_rename(node_id))
        reset = menu.addAction(translate("color.node.reset"))
        reset.triggered.connect(lambda: self.reset_requested.emit(node_id))
        menu.addSeparator()
        remove = menu.addAction(translate("color.node.remove"))
        remove.setEnabled(len(self.graph.nodes) > 1)
        remove.triggered.connect(lambda: self.remove_requested.emit(node_id))
        return menu

    def event(self, event) -> bool:  # noqa: A003 - API Qt
        # Suppr, Ctrl+D… sont aussi des raccourcis de la fenêtre (supprimer, dupliquer le clip) : tant que l'éditeur
        # a le focus, ils agissent sur le nœud.
        if event.type() == QEvent.ShortcutOverride and self._command_for(event) is not None:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        command = self._command_for(event)
        if command is None:
            super().keyPressEvent(event)
            return
        command()

    def _command_for(self, event):
        if self.graph is None or not self.isEnabled():
            return None
        current = self.current_id or self.graph.nodes[0].id
        key, modifiers = event.key(), event.modifiers()
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and not modifiers:
            return (lambda: self.remove_requested.emit(current)) if len(self.graph.nodes) > 1 else (lambda: None)
        if key in (Qt.Key_Return, Qt.Key_Enter) and not modifiers:
            return lambda: self.request_rename(current)
        if key == Qt.Key_S and modifiers == Qt.AltModifier:
            return lambda: self.add_requested.emit(current)
        if key == Qt.Key_D and modifiers == Qt.ControlModifier:      # Cmd+D sur macOS
            return lambda: self.toggle_requested.emit(current)
        return None
