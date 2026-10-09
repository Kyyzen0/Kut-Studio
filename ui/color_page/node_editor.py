"""Éditeur de nœuds d'étalonnage : le graphe d'un clip, de la source à la sortie.

Une scène ``QGraphicsView`` (la composition nodale réutilisera la même vue). Disposition : une colonne par
profondeur (un nœud après tout ce qui l'alimente), une rangée par branche ; les entrées basses d'un mélangeur sont
dessinées plus bas (en calques, la plus basse passe dessus, comme dans DaVinci Resolve). Les correcteurs sont
numérotés dans l'ordre de calcul ; le nœud courant (bordure d'accent) est celui que les roues, le qualifieur et
l'inspecteur modifient ; un nœud contourné est en pointillé ; une pastille signale un nœud qui change l'image, une
clé un nœud qualifié. Les mélangeurs (ronds : ``+`` parallèle, ``≡`` calques) ne se règlent pas.

L'éditeur ne modifie rien lui-même : il émet des demandes (sélection, ajout en série ou en branche, suppression,
contournement, nom, déplacement, remise à zéro) que la fenêtre applique au projet, avec une étape d'historique, puis
réaffiche le graphe (:meth:`NodeEditor.set_graph`). Une demande née d'un geste sur un nœud part après la fin de
l'événement : la scène reconstruite détruit le nœud, qui ne doit plus être en train de le traiter. Glisser un nœud le
déplace dans sa suite en série. Au clavier, tant qu'il a le focus : Alt+S ajoute un nœud en série, Alt+P en
parallèle, Alt+L en calque, Ctrl+D contourne, Suppr supprime (et jamais le clip de la timeline), Entrée renomme.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
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

from core.color_nodes import MAX_LABEL_LENGTH, ColorMixer, ColorNode, ColorNodeGraph, MixerKind
from ui.design_system import Iconography, Radius, Typography, Weights
from ui.i18n import translate
from ui.icons import IconName, make_icon
from ui.theme import COLORS

NODE_WIDTH = 96.0
NODE_HEIGHT = 56.0
NODE_GAP = 44.0
ROW_GAP = 22.0
MIXER_RADIUS = 13.0
TERMINAL_RADIUS = 7.0
PORT_RADIUS = 4.0
MIN_ZOOM = 0.5


def node_number(position: int) -> str:
    """Numéro affiché d'un nœud (``01``, ``02``…) : son rang dans l'ordre de calcul."""
    return f"{position + 1:02d}"


def layout(graph: ColorNodeGraph) -> dict[str, tuple[int, int]]:
    """``{nœud: (colonne, rangée)}`` : colonne = profondeur, rangée = branche (entrées d'un mélangeur de haut en bas)."""
    column: dict[str, int] = {}
    for node in graph.order():
        sources = [link.source for link in graph.inputs(node.id)]
        column[node.id] = max((column[source] + 1 for source in sources), default=0)
    row: dict[str, int] = {}

    def place(node_id: str, at: int) -> int:
        if node_id in row:
            return at
        row[node_id] = at
        lowest = at
        for index, link in enumerate(graph.inputs(node_id)):
            lowest = max(lowest, place(link.source, at if index == 0 else lowest + 1))
        return lowest

    place(graph.sink.id, 0)
    return {node_id: (column[node_id], row.get(node_id, 0)) for node_id in column}


def _cell(column: int, row: int) -> QPointF:
    return QPointF(2 * TERMINAL_RADIUS + NODE_GAP + column * (NODE_WIDTH + NODE_GAP), row * (NODE_HEIGHT + ROW_GAP))


class _NodeItem(QGraphicsItem):
    """Un correcteur ; glissé à l'horizontale pour changer sa place dans sa suite en série."""

    def __init__(self, editor: "NodeEditor", node: ColorNode, position: int, home: QPointF) -> None:
        super().__init__()
        self.editor = editor
        self.node = node
        self.position = position
        self.home = home
        self._press_x: float | None = None
        self.setPos(home)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(translate("color.node.tip"))

    def input_port(self, _port: int = 0) -> QPointF:
        return self.pos() + QPointF(0.0, NODE_HEIGHT / 2)

    def output_port(self) -> QPointF:
        return self.pos() + QPointF(NODE_WIDTH, NODE_HEIGHT / 2)

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
        if self.node.restricted():
            make_icon(IconName.KEY, size=Iconography.xs).paint(painter, QRect(int(NODE_WIDTH) - 34, 6, 12, 12))
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


class _MixerItem(QGraphicsItem):
    """Un mélangeur : un rond, ``+`` (parallèle) ou ``≡`` (calques) ; ses entrées arrivent sur sa gauche."""

    def __init__(self, mixer: ColorMixer, home: QPointF, inputs: int) -> None:
        super().__init__()
        self.mixer = mixer
        self.inputs = max(1, inputs)
        self.setPos(home + QPointF(NODE_WIDTH / 2 - MIXER_RADIUS, NODE_HEIGHT / 2 - MIXER_RADIUS))
        self.setToolTip(translate(f"color.mixer.{mixer.kind.value}"))

    def input_port(self, port: int = 0) -> QPointF:
        spread = (port - (self.inputs - 1) / 2) * min(8.0, 2 * MIXER_RADIUS / self.inputs)
        return self.pos() + QPointF(0.0, MIXER_RADIUS + spread)

    def output_port(self) -> QPointF:
        return self.pos() + QPointF(2 * MIXER_RADIUS, MIXER_RADIUS)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return QRectF(-2, -2, 2 * MIXER_RADIUS + 4, 2 * MIXER_RADIUS + 4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(QPen(QColor(COLORS["border_strong"]), 1.2))
        painter.setBrush(QColor(COLORS["surface"]))
        painter.drawEllipse(QRectF(0, 0, 2 * MIXER_RADIUS, 2 * MIXER_RADIUS))
        font = QFont(painter.font())
        font.setPixelSize(Typography.body)
        font.setWeight(QFont.Weight(Weights.bold))
        painter.setFont(font)
        painter.setPen(QColor(COLORS["muted_strong"]))
        symbol = "+" if self.mixer.kind is MixerKind.PARALLEL else "≡"
        painter.drawText(QRectF(0, 0, 2 * MIXER_RADIUS, 2 * MIXER_RADIUS), Qt.AlignCenter, symbol)


class NodeEditor(QGraphicsView):
    """Graphe de nœuds d'un clip ; n'émet que des demandes, la fenêtre les applique."""

    node_selected = Signal(str)
    add_requested = Signal(str)                    # en série après ce nœud ("" : après la sortie)
    branch_requested = Signal(str, str)            # à côté de ce nœud, mélangeur ``parallel`` ou ``layer``
    remove_requested = Signal(str)
    toggle_requested = Signal(str)
    rename_requested = Signal(str, str)
    move_requested = Signal(str, int)              # rang dans sa suite en série
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
        self._mixers: list[_MixerItem] = []
        self._links: list[tuple[QGraphicsPathItem, str | None, str | None, int]] = []
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
        self._items, self._mixers, self._links = [], [], []
        if graph is None:
            return
        places = layout(graph)
        numbers = {node.id: index for index, node in enumerate(node for node in graph.order()
                                                                   if isinstance(node, ColorNode))}
        for node in graph.order():
            column, row = places[node.id]
            if isinstance(node, ColorNode):
                item: _NodeItem | _MixerItem = _NodeItem(self, node, numbers[node.id], _cell(column, row))
                self._items.append(item)
            else:
                item = _MixerItem(node, _cell(column, row), len(graph.inputs(node.id)))
                self._mixers.append(item)
            scene.addItem(item)
        rightmost = max(item.output_port().x() for item in (*self._items, *self._mixers))
        self._terminals = (QPointF(TERMINAL_RADIUS, NODE_HEIGHT / 2),
                           QPointF(rightmost + NODE_GAP, self._item(graph.sink.id).output_port().y()))
        wires = [(None, node.id, 0) for node in graph.nodes if not graph.inputs(node.id)]
        wires += [(link.source, link.target, link.port) for link in graph.links]
        wires.append((graph.sink.id, None, 0))
        for source, target, port in wires:
            wire = QGraphicsPathItem()
            wire.setPen(QPen(QColor(COLORS["muted"]), 1.5))
            wire.setZValue(-1)
            scene.addItem(wire)
            self._links.append((wire, source, target, port))
        self._add_terminals()
        self.update_links()
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-12, -12, 12, 12))
        self._fit()

    def _fit(self) -> None:
        """Tout le graphe visible : réduit jusqu'à :data:`MIN_ZOOM` si la place manque (puis défilement), jamais
        agrandi."""
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

    def _item(self, node_id: str) -> _NodeItem | _MixerItem:
        return next(item for item in (*self._items, *self._mixers)
                    if (item.node.id if isinstance(item, _NodeItem) else item.mixer.id) == node_id)

    def update_links(self) -> None:
        """Retrace chaque lien, de la sortie de sa source (ou de l'image du clip) à l'entrée de sa cible (ou à la
        sortie)."""
        for wire, source, target, port in self._links:
            start = self._item(source).output_port() if source is not None else self._terminals[0]
            end = self._item(target).input_port(port) if target is not None else self._terminals[1]
            path = QPainterPath(start)
            bend = max(12.0, abs(end.x() - start.x()) / 2)
            path.cubicTo(QPointF(start.x() + bend, start.y()), QPointF(end.x() - bend, end.y()), end)
            wire.setPath(path)

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
        label = self._ask_label(self.graph.corrector(node_id).label)
        if label is not None:
            cleaned = label.strip()[:MAX_LABEL_LENGTH]
            QTimer.singleShot(0, lambda: self.rename_requested.emit(node_id, cleaned))

    def _ask_label(self, current: str) -> str | None:
        """Nom saisi (``None`` : annulé) ; isolé pour que les tests le remplacent."""
        text, accepted = QInputDialog.getText(self, translate("color.node.rename_title"),
                                              translate("color.node.rename_label"), text=current)
        return text if accepted else None

    def drop(self, item: _NodeItem) -> None:
        """Fin d'un glisser : la place du nœud dans sa suite en série, d'après son centre."""
        if self.graph is None:
            return
        run = self.graph.serial_run(item.node.id)
        centers = {other.node.id: other.x() + NODE_WIDTH / 2 for other in self._items if other.node.id in run}
        index = sum(1 for node_id, center in centers.items()
                    if node_id != item.node.id and center < centers[item.node.id])
        if index != run.index(item.node.id):
            node_id = item.node.id
            QTimer.singleShot(0, lambda: self.move_requested.emit(node_id, index))
        else:
            item.setPos(item.home)
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
        """Menu contextuel d'un nœud (``None`` : fond de l'éditeur, on ajoute après la sortie)."""
        menu = QMenu(self)
        add = menu.addAction(translate("color.node.add"))
        add.triggered.connect(lambda: self.add_requested.emit(node_id or ""))
        if node_id is None or self.graph is None:
            return menu
        for kind in (MixerKind.PARALLEL, MixerKind.LAYER):
            branch = menu.addAction(translate(f"color.node.add_{kind.value}"))
            branch.triggered.connect(lambda _checked=False, value=kind.value: self.branch_requested.emit(node_id, value))
        menu.addSeparator()
        enabled = self.graph.corrector(node_id).enabled
        toggle = menu.addAction(translate("color.node.bypass" if enabled else "color.node.enable"))
        toggle.triggered.connect(lambda: self.toggle_requested.emit(node_id))
        rename = menu.addAction(translate("color.node.rename"))
        rename.triggered.connect(lambda: self.request_rename(node_id))
        reset = menu.addAction(translate("color.node.reset"))
        reset.triggered.connect(lambda: self.reset_requested.emit(node_id))
        menu.addSeparator()
        remove = menu.addAction(translate("color.node.remove"))
        remove.setEnabled(len(self.graph.correctors) > 1)
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
        current = self.graph.node_or_first(self.current_id).id
        key, modifiers = event.key(), event.modifiers()
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and not modifiers:
            return (lambda: self.remove_requested.emit(current)) if len(self.graph.correctors) > 1 else (lambda: None)
        if key in (Qt.Key_Return, Qt.Key_Enter) and not modifiers:
            return lambda: self.request_rename(current)
        if modifiers == Qt.AltModifier and key in (Qt.Key_S, Qt.Key_P, Qt.Key_L):
            if key == Qt.Key_S:
                return lambda: self.add_requested.emit(current)
            kind = MixerKind.PARALLEL if key == Qt.Key_P else MixerKind.LAYER
            return lambda: self.branch_requested.emit(current, kind.value)
        if key == Qt.Key_D and modifiers == Qt.ControlModifier:      # Cmd+D sur macOS
            return lambda: self.toggle_requested.emit(current)
        return None
