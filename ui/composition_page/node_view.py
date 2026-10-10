"""Éditeur de nœuds d'une composition : les nœuds, leurs entrées et leurs liens, branchés à la souris.

Disposition automatique : une colonne par profondeur (un nœud à droite de tout ce qui l'alimente, les sources à gauche,
la sortie au bout), une rangée par nœud dans sa colonne, dans l'ordre de création. Chaque nœud montre son type, son nom,
ses entrées à gauche (une fusion en a deux : fond, premier plan) et sa sortie à droite. Le nœud choisi (bordure
d'accent) est celui que l'inspecteur règle ; ajouter un nœud le place après lui.

Gestes : clic pour choisir ; glisser la sortie d'un nœud sur l'entrée d'un autre pour les relier (l'entrée prend ce
nouveau lien) ; glisser un lien par son entrée pour le reporter sur une autre entrée, ou le lâcher dans le vide pour le
défaire ; Suppr retire le nœud choisi (pas la sortie) ; clic droit : ajouter, supprimer.

La vue ne modifie rien : elle émet des demandes que la fenêtre applique (une étape d'historique chacune), puis
réaffiche le graphe (:meth:`CompositionNodeView.set_graph`). Les demandes partent après la fin de l'événement : la scène
reconstruite détruit les éléments qui le traitaient.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu, QWidget

from core.composition import (
    CompositionGraph,
    MergeNode,
    OutputNode,
    input_count,
    kind_of,
)
from ui.design_system import Radius, Typography, Weights
from ui.i18n import translate
from ui.theme import COLORS

NODE_WIDTH = 116.0
NODE_HEIGHT = 50.0
COLUMN_GAP = 46.0
ROW_GAP = 18.0
PORT_RADIUS = 4.5
PORT_HIT = 9.0
MIN_ZOOM = 0.45

ADDABLE_KINDS = ("media", "graphic", "solid", "transform", "merge", "mask", "key", "effects", "grade")
"""Les nœuds que l'on peut ajouter (la sortie existe toujours, une seule)."""


def layout(graph: CompositionGraph) -> dict[str, tuple[int, int]]:
    """``{nœud: (colonne, rangée)}`` : colonne = plus long chemin depuis une source, rangée = rang dans la colonne. La
    sortie est seule dans la dernière colonne."""
    column: dict[str, int] = {}
    for node in graph.order():
        sources = [link.source for link in graph.inputs(node.id)]
        column[node.id] = max((column[source] + 1 for source in sources), default=0)
    output = graph.output.id
    others = [value for node_id, value in column.items() if node_id != output]
    column[output] = max(column[output], max(others, default=-1) + 1)
    rows: dict[int, int] = {}
    places: dict[str, tuple[int, int]] = {}
    for node in graph.nodes:
        col = column[node.id]
        places[node.id] = (col, rows.get(col, 0))
        rows[col] = rows.get(col, 0) + 1
    return places


def node_title(node) -> str:
    """Le nom d'un nœud, ou le nom de son type."""
    return getattr(node, "label", "") or translate(f"comp.node.{kind_of(node)}")


def port_names(node) -> tuple[str, ...]:
    if isinstance(node, MergeNode):
        return (translate("comp.port.background"), translate("comp.port.foreground"))
    return tuple(translate("comp.port.input") for _ in range(input_count(node)))


class _NodeItem(QGraphicsItem):
    """Un nœud : son cadre, son type et son nom, ses entrées (gauche) et sa sortie (droite)."""

    def __init__(self, view: "CompositionNodeView", node, home: QPointF) -> None:
        super().__init__()
        self.view = view
        self.node = node
        self.setPos(home)
        self.setToolTip(translate("comp.node.tip"))
        self.setCursor(Qt.PointingHandCursor)

    @property
    def is_output(self) -> bool:
        return isinstance(self.node, OutputNode)

    def input_port(self, port: int) -> QPointF:
        count = max(1, input_count(self.node))
        return self.pos() + QPointF(0.0, NODE_HEIGHT * (port + 1) / (count + 1))

    def output_port(self) -> QPointF:
        return self.pos() + QPointF(NODE_WIDTH, NODE_HEIGHT / 2)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return QRectF(-PORT_RADIUS - 2, -3, NODE_WIDTH + 2 * PORT_RADIUS + 4, NODE_HEIGHT + 6)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        current = self.view.current_id == self.node.id
        rect = QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT)
        border = QColor(COLORS["accent"] if current else (COLORS["success"] if self.is_output else COLORS["border_strong"]))
        painter.setPen(QPen(border, 2.0 if current else 1.0))
        painter.setBrush(QColor(COLORS["panel_elevated"]))
        painter.drawRoundedRect(rect, Radius.md, Radius.md)
        caption = QFont(painter.font())
        caption.setPixelSize(Typography.caption)
        caption.setWeight(QFont.Weight(Weights.bold))
        painter.setFont(caption)
        painter.setPen(QColor(COLORS["muted_strong"]))
        painter.drawText(QRectF(9, 5, NODE_WIDTH - 18, 14), Qt.AlignLeft | Qt.AlignVCenter,
                         translate(f"comp.node.{kind_of(self.node)}").upper())
        title = QFont(painter.font())
        title.setPixelSize(Typography.small)
        title.setWeight(QFont.Weight(Weights.medium))
        painter.setFont(title)
        painter.setPen(QColor(COLORS["text"]))
        text = QFontMetricsF(title).elidedText(node_title(self.node), Qt.ElideRight, NODE_WIDTH - 18)
        painter.drawText(QRectF(9, 20, NODE_WIDTH - 18, NODE_HEIGHT - 24), Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.setPen(QPen(QColor(COLORS["panel"]), 1))
        graph = self.view.graph
        for port in range(input_count(self.node)):
            linked = graph is not None and graph.input_of(self.node.id, port) is not None
            painter.setBrush(QColor(COLORS["accent"] if linked else COLORS["muted"]))
            painter.drawEllipse(self.input_port(port) - self.pos(), PORT_RADIUS, PORT_RADIUS)
        if not self.is_output:
            painter.setBrush(QColor(COLORS["muted_strong"]))
            painter.drawEllipse(self.output_port() - self.pos(), PORT_RADIUS, PORT_RADIUS)


class CompositionNodeView(QGraphicsView):
    """Le graphe d'une composition ; n'émet que des demandes, la fenêtre les applique."""

    node_selected = Signal(str)
    add_requested = Signal(str, str)                       # type, nœud après lequel l'ajouter ("" : avant la sortie)
    remove_requested = Signal(str)
    connect_requested = Signal(str, str, int)              # source, cible, entrée
    disconnect_requested = Signal(str, int)                # cible, entrée
    rewire_requested = Signal(str, str, int, str, int)     # source, ancienne cible / entrée, nouvelle cible / entrée

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composition_node_view")
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(int(NODE_HEIGHT) + 60)
        self.setAccessibleName(translate("comp.nodes.title"))
        self.graph: CompositionGraph | None = None
        self.current_id: str | None = None
        self.editable = True
        self._items: dict[str, _NodeItem] = {}
        self._wires: list[tuple[QGraphicsPathItem, str, str, int]] = []
        self._drag: dict | None = None
        self._preview: QGraphicsPathItem | None = None
        self._paint_background()

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_graph(self, graph: CompositionGraph | None, current_id: str | None, *, editable: bool = True) -> None:
        """Réaffiche ``graph`` (``None`` : aucune composition) ; un graphe inchangé n'est que repeint."""
        self.editable = editable
        unchanged = graph is not None and graph == self.graph and bool(self._items)
        self.graph = graph
        self.current_id = current_id
        if unchanged:
            self.scene().update()
            return
        scene = self.scene()
        scene.clear()
        self._items, self._wires, self._preview = {}, [], None
        if graph is None:
            return
        for node_id, (column, row) in layout(graph).items():
            item = _NodeItem(self, graph.node(node_id),
                             QPointF(column * (NODE_WIDTH + COLUMN_GAP), row * (NODE_HEIGHT + ROW_GAP)))
            scene.addItem(item)
            self._items[node_id] = item
        for link in graph.links:
            wire = QGraphicsPathItem()
            wire.setPen(QPen(QColor(COLORS["muted"]), 1.5))
            wire.setZValue(-1)
            scene.addItem(wire)
            self._wires.append((wire, link.source, link.target, link.port))
        self._trace_wires()
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-16, -16, 16, 16))
        self._fit()

    def _trace_wires(self) -> None:
        for wire, source, target, port in self._wires:
            wire.setPath(_curve(self._items[source].output_port(), self._items[target].input_port(port)))

    def _fit(self) -> None:
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

    def _paint_background(self) -> None:
        self.setBackgroundBrush(QColor(COLORS["panel_alt"]))

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._paint_background()

    # -- repérage -----------------------------------------------------------------------------------------------

    def item_at(self, scene_point: QPointF) -> _NodeItem | None:
        return next((item for item in self._items.values()
                     if item.sceneBoundingRect().contains(scene_point)), None)

    def output_at(self, scene_point: QPointF) -> str | None:
        """Le nœud dont la sortie est sous le point."""
        for node_id, item in self._items.items():
            if not item.is_output and _near(item.output_port(), scene_point):
                return node_id
        return None

    def input_at(self, scene_point: QPointF) -> tuple[str, int] | None:
        """``(nœud, entrée)`` sous le point."""
        for node_id, item in self._items.items():
            for port in range(input_count(item.node)):
                if _near(item.input_port(port), scene_point):
                    return node_id, port
        return None

    # -- gestes -------------------------------------------------------------------------------------------------

    def _later(self, signal, *args) -> None:
        QTimer.singleShot(0, self, lambda: signal.emit(*args))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        point = self.mapToScene(event.position().toPoint())
        if event.button() != Qt.LeftButton or self.graph is None:
            super().mousePressEvent(event)
            return
        if self.editable and (source := self.output_at(point)) is not None:
            self._drag = {"source": source, "start": self._items[source].output_port(), "picked": None}
        elif self.editable and (port := self.input_at(point)) is not None and \
                (source := self.graph.input_of(*port)) is not None:
            self._drag = {"source": source, "start": self._items[source].output_port(), "picked": port}
        elif (item := self.item_at(point)) is not None:
            self.current_id = item.node.id
            self.scene().update()
            self._later(self.node_selected, item.node.id)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        drag = self._drag
        if drag is None:
            super().mouseMoveEvent(event)
            return
        point = self.mapToScene(event.position().toPoint())
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            self._preview.setPen(QPen(QColor(COLORS["accent"]), 1.5, Qt.DashLine))
            self.scene().addItem(self._preview)
        self._preview.setPath(_curve(drag["start"], point))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        drag, self._drag = self._drag, None
        if self._preview is not None:
            self.scene().removeItem(self._preview)
            self._preview = None
        if drag is None:
            super().mouseReleaseEvent(event)
            return
        target = self.input_at(self.mapToScene(event.position().toPoint()))
        picked = drag["picked"]
        if target is not None and target[0] == drag["source"]:
            target = None                                       # un nœud ne s'alimente pas lui-même
        if picked is None:
            if target is not None:
                self._later(self.connect_requested, drag["source"], target[0], target[1])
        elif target is None:
            self._later(self.disconnect_requested, picked[0], picked[1])
        elif target != picked:
            self._later(self.rewire_requested, drag["source"], picked[0], picked[1], target[0], target[1])
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._removable(self.current_id):
            self._later(self.remove_requested, self.current_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def _removable(self, node_id: str | None) -> bool:
        return (self.editable and self.graph is not None and node_id is not None and self.graph.has_node(node_id)
                and not isinstance(self.graph.node(node_id), OutputNode))

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.graph is None or not self.editable:
            return
        item = self.item_at(self.mapToScene(event.pos()))
        if item is not None:
            self.current_id = item.node.id
            self._later(self.node_selected, item.node.id)
        menu = QMenu(self)
        actions = add_menu(menu)
        remove = menu.addAction(translate("comp.panel.remove"))
        remove.setEnabled(self._removable(self.current_id))
        chosen = menu.exec(event.globalPos())
        if chosen is remove:
            self._later(self.remove_requested, self.current_id or "")
        elif chosen in actions:
            self._later(self.add_requested, actions[chosen], self.current_id or "")


def add_menu(menu: QMenu) -> dict:
    """Le sous-menu « Ajouter » : ``{action: type de nœud}``."""
    submenu = menu.addMenu(translate("comp.panel.add"))
    return {submenu.addAction(translate(f"comp.node.{kind}")): kind for kind in ADDABLE_KINDS}


def _near(port: QPointF, point: QPointF) -> bool:
    return (port - point).manhattanLength() <= PORT_HIT * 1.5


def _curve(start: QPointF, end: QPointF) -> QPainterPath:
    path = QPainterPath(start)
    bend = max(14.0, abs(end.x() - start.x()) / 2)
    path.cubicTo(QPointF(start.x() + bend, start.y()), QPointF(end.x() - bend, end.y()), end)
    return path


__all__ = ["ADDABLE_KINDS", "CompositionNodeView", "add_menu", "layout", "node_title", "port_names"]
