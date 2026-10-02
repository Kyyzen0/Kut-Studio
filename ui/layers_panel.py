"""Panneau Calques : la pile motion graphics, lisible d'un coup d'œil.

La timeline reste l'outil du montage (début, fin, trim). Ce panneau montre
ce qu'elle ne montre pas bien : l'ordre de la pile, les groupes, les liens
de parenté, la visibilité et le verrou de chaque calque. Une barre de
durée par ligne indique **quand** le calque existe (et où est la tête de
lecture) sans changer de panneau.

Le panneau n'édite jamais le projet lui-même : il émet des demandes que la
fenêtre applique (une entrée d'historique chacune) puis le recharge.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.graphics import GraphicType, ShapeKind
from ui.design_system import Spacing
from ui.theme import COLORS, label_style

ID_ROLE = Qt.UserRole + 1
SPAN_ROLE = Qt.UserRole + 2

COL_VISIBLE, COL_LOCK, COL_NAME, COL_PARENT, COL_TIME = range(5)

TYPE_GLYPHS = {
    GraphicType.TEXT: "T",
    GraphicType.SHAPE: "◆",
    GraphicType.RECTANGLE: "▭",
    GraphicType.SOLID: "■",
    GraphicType.IMAGE: "▣",
    GraphicType.GROUP: "▤",
    GraphicType.ADJUSTMENT: "◐",
    GraphicType.NULL: "⊕",
}

SHAPE_CHOICES = (
    (ShapeKind.RECTANGLE, "Rectangle"),
    (ShapeKind.ROUNDED_RECTANGLE, "Rectangle arrondi"),
    (ShapeKind.ELLIPSE, "Ellipse / cercle"),
    (ShapeKind.LINE, "Ligne"),
    (ShapeKind.POLYGON, "Polygone"),
)


class _TimingDelegate(QStyledItemDelegate):
    """Barre « quand le calque existe » + tête de lecture."""

    def __init__(self, panel: "LayersPanel") -> None:
        super().__init__(panel)
        self._panel = panel

    def paint(self, painter: QPainter, option, index) -> None:
        span = index.data(SPAN_ROLE)
        rect: QRect = option.rect.adjusted(4, 6, -4, -6)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(rect, QColor(COLORS["panel_alt"]))
        duration = max(self._panel.duration, 1e-6)
        if span is not None:
            start, end = span
            x0 = rect.x() + rect.width() * max(0.0, start) / duration
            x1 = rect.x() + rect.width() * min(duration, end) / duration
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawRoundedRect(int(x0), rect.y(), max(2, int(x1 - x0)), rect.height(), 2, 2)
        playhead = rect.x() + rect.width() * min(max(self._panel.playhead, 0.0), duration) / duration
        painter.setPen(QPen(QColor(COLORS["text"]), 1))
        painter.drawLine(int(playhead), option.rect.top() + 2, int(playhead), option.rect.bottom() - 2)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (API Qt)
        return QSize(90, 22)


class _LayerTree(QTreeWidget):
    """Arbre dont le glisser-déposer est **demandé**, jamais appliqué par Qt."""

    move_requested = Signal(str, str, int)  # calque, groupe cible (""), rang (0 = bas)

    def dropEvent(self, event) -> None:  # noqa: N802 (API Qt)
        dragged = self.currentItem()
        if dragged is None:
            event.ignore()
            return
        clip_id = dragged.data(COL_NAME, ID_ROLE)
        target = self.itemAt(event.position().toPoint())
        position = self.dropIndicatorPosition()
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        if target is None:
            self.move_requested.emit(clip_id, "", 0)
            return
        target_id = target.data(COL_NAME, ID_ROLE)
        is_group = target.data(COL_NAME, Qt.UserRole + 3) == GraphicType.GROUP.value
        if position == QAbstractItemView.OnItem and is_group and target_id != clip_id:
            self.move_requested.emit(clip_id, target_id, -1)  # dans le groupe, en haut
            return
        parent = target.parent()
        container = parent.data(COL_NAME, ID_ROLE) if parent is not None else ""
        siblings = [
            (parent.child(i) if parent is not None else self.topLevelItem(i)).data(COL_NAME, ID_ROLE)
            for i in range(parent.childCount() if parent is not None else self.topLevelItemCount())
        ]
        siblings = [cid for cid in siblings if cid != clip_id]
        row = siblings.index(target_id) if target_id in siblings else 0
        if position == QAbstractItemView.BelowItem:
            row += 1
        # Affichage : haut de la pile en premier → rang « depuis le bas ».
        rank = len(siblings) - row
        self.move_requested.emit(clip_id, container, rank)


class LayersPanel(QWidget):
    layer_selected = Signal(str)
    visibility_toggled = Signal(str, bool)
    lock_toggled = Signal(str, bool)
    rename_requested = Signal(str, str)
    move_requested = Signal(str, str, int)
    parent_requested = Signal(str, str)
    group_requested = Signal(list)
    ungroup_requested = Signal(str)
    add_requested = Signal(str, str)  # type, forme
    import_image_requested = Signal()
    duplicate_requested = Signal(list)
    delete_requested = Signal(list)
    copy_attributes_requested = Signal(str)
    paste_attributes_requested = Signal(list)
    save_preset_requested = Signal(list)
    preset_apply_requested = Signal(object)
    motion_blur_toggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.duration = 1.0
        self.playhead = 0.0
        self._root_group = ""
        self._nodes = []
        self._parent_choices: dict[str, list[tuple[str, str]]] = {}
        self._presets: list = []
        self._motion_blur: dict[str, bool] = {}
        self._updating = False
        # Parents possibles d'un calque, demandés à l'ouverture du menu
        # (le calcul évite les cycles : inutile de le faire pour chaque ligne).
        self.parent_choices_provider = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)

        header = QHBoxLayout()
        title = QLabel("CALQUES")
        title.setStyleSheet(label_style(11, "muted_strong", 800))
        header.addWidget(title)
        self.breadcrumb = QLabel("")
        self.breadcrumb.setStyleSheet(label_style(11, "accent", 700))
        header.addWidget(self.breadcrumb, 1)
        self.exit_group_button = QToolButton()
        self.exit_group_button.setText("↩ Sortir du groupe")
        self.exit_group_button.clicked.connect(lambda: self.enter_group(""))
        self.exit_group_button.hide()
        header.addWidget(self.exit_group_button)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(Spacing.xs)
        self.add_button = QToolButton()
        self.add_button.setText("+ Ajouter")
        self.add_button.setPopupMode(QToolButton.InstantPopup)
        self.add_button.setMenu(self._build_add_menu())
        toolbar.addWidget(self.add_button)
        self.group_button = QToolButton()
        self.group_button.setText("Grouper")
        self.group_button.setToolTip("Grouper les calques sélectionnés (Ctrl+G)")
        self.group_button.clicked.connect(lambda: self.group_requested.emit(self.selected_ids()))
        toolbar.addWidget(self.group_button)
        self.presets_button = QToolButton()
        self.presets_button.setText("Presets")
        self.presets_button.setPopupMode(QToolButton.InstantPopup)
        self.presets_menu = QMenu(self.presets_button)
        self.presets_button.setMenu(self.presets_menu)
        toolbar.addWidget(self.presets_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.tree = _LayerTree()
        self.tree.setObjectName("layers_tree")
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["◉", "▣", "Calque", "Parent", "Durée"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(COL_VISIBLE, QHeaderView.Fixed)
        header_view.setSectionResizeMode(COL_LOCK, QHeaderView.Fixed)
        header_view.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header_view.setSectionResizeMode(COL_PARENT, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(COL_TIME, QHeaderView.Fixed)
        header_view.setStretchLastSection(False)
        self.tree.setColumnWidth(COL_VISIBLE, 28)
        self.tree.setColumnWidth(COL_LOCK, 28)
        self.tree.setColumnWidth(COL_TIME, 90)
        self.tree.setItemDelegateForColumn(COL_TIME, _TimingDelegate(self))
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.move_requested.connect(self.move_requested.emit)
        layout.addWidget(self.tree, 1)

        self.empty_hint = QLabel(
            "Aucun calque. Ajoutez un texte ou une forme : il apparaît dans le viewer, "
            "où vous pouvez le déplacer directement."
        )
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self.empty_hint)

    # -- menus ---------------------------------------------------------------------------------

    def _build_add_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Texte").triggered.connect(lambda: self.add_requested.emit("text", ""))
        shapes = menu.addMenu("Forme")
        for kind, label in SHAPE_CHOICES:
            shapes.addAction(label).triggered.connect(
                lambda _checked=False, value=kind.value: self.add_requested.emit("shape", value)
            )
        menu.addAction("Aplat de couleur").triggered.connect(lambda: self.add_requested.emit("solid", ""))
        menu.addAction("Image…").triggered.connect(self.import_image_requested.emit)
        menu.addSeparator()
        menu.addAction("Contrôleur (null)").triggered.connect(lambda: self.add_requested.emit("null", ""))
        menu.addAction("Calque d'effets (adjustment)").triggered.connect(
            lambda: self.add_requested.emit("adjustment", "")
        )
        return menu

    def set_presets(self, presets) -> None:
        self._presets = list(presets)
        self.presets_menu.clear()
        categories: dict[str, QMenu] = {}
        for preset in self._presets:
            submenu = categories.get(preset.category)
            if submenu is None:
                submenu = self.presets_menu.addMenu(preset.category)
                categories[preset.category] = submenu
            submenu.addAction(preset.name).triggered.connect(
                lambda _checked=False, value=preset: self.preset_apply_requested.emit(value)
            )
        self.presets_menu.addSeparator()
        save = self.presets_menu.addAction("Enregistrer la sélection comme preset…")
        save.triggered.connect(lambda: self.save_preset_requested.emit(self.selected_ids()))

    def _show_context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        ids = self.selected_ids()
        menu = QMenu(self)
        if item is not None:
            clip_id = item.data(COL_NAME, ID_ROLE)
            if clip_id not in ids:
                ids = [clip_id]
            node = next((n for n in self._nodes if n.clip_id == clip_id), None)
            menu.addAction("Renommer").triggered.connect(lambda: self.tree.editItem(item, COL_NAME))
            parent_menu = menu.addMenu("Parent")
            none = parent_menu.addAction("Aucun")
            none.triggered.connect(lambda: self.parent_requested.emit(clip_id, ""))
            choices = (
                self.parent_choices_provider(clip_id)
                if callable(self.parent_choices_provider)
                else self._parent_choices.get(clip_id, [])
            )
            for candidate_id, name in choices:
                action = parent_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(node is not None and node.parent_id == candidate_id)
                action.triggered.connect(
                    lambda _checked=False, value=candidate_id: self.parent_requested.emit(clip_id, value)
                )
            menu.addSeparator()
            menu.addAction("Grouper").triggered.connect(lambda: self.group_requested.emit(ids))
            if node is not None and node.is_group:
                menu.addAction("Dégrouper").triggered.connect(lambda: self.ungroup_requested.emit(clip_id))
                menu.addAction("Entrer dans le groupe").triggered.connect(lambda: self.enter_group(clip_id))
            menu.addAction("Dupliquer").triggered.connect(lambda: self.duplicate_requested.emit(ids))
            menu.addSeparator()
            menu.addAction("Copier transform, masques et effets").triggered.connect(
                lambda: self.copy_attributes_requested.emit(clip_id)
            )
            menu.addAction("Coller les attributs…").triggered.connect(
                lambda: self.paste_attributes_requested.emit(ids)
            )
            if node is not None and not node.is_group and node.type not in (GraphicType.NULL, GraphicType.ADJUSTMENT):
                blur = menu.addAction("Flou de mouvement")
                blur.setCheckable(True)
                blur.setChecked(bool(self._motion_blur.get(clip_id)))
                blur.triggered.connect(lambda checked: self.motion_blur_toggled.emit(clip_id, checked))
            menu.addAction("Enregistrer comme preset…").triggered.connect(
                lambda: self.save_preset_requested.emit(ids)
            )
            menu.addSeparator()
            menu.addAction("Supprimer").triggered.connect(lambda: self.delete_requested.emit(ids))
        else:
            menu.addMenu(self._build_add_menu()).setTitle("Ajouter")
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # -- contenu ---------------------------------------------------------------------------------

    def set_layers(
        self,
        nodes,
        *,
        selected_ids=(),
        duration: float = 1.0,
        playhead: float = 0.0,
        names: dict[str, str] | None = None,
        parent_choices: dict[str, list[tuple[str, str]]] | None = None,
        motion_blur: dict[str, bool] | None = None,
    ) -> None:
        """Recharge la pile (``nodes`` : :func:`core.mograph_layers.layer_tree`)."""
        self._updating = True
        try:
            self._nodes = list(nodes)
            self.duration = max(float(duration), 1e-6)
            self.playhead = float(playhead)
            self._parent_choices = dict(parent_choices or {})
            self._motion_blur = dict(motion_blur or {})
            names = dict(names or {})
            for node in self._nodes:
                names.setdefault(node.clip_id, node.name)
            expanded = {
                self.tree.topLevelItem(i).data(COL_NAME, ID_ROLE)
                for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).isExpanded()
            }
            self.tree.clear()
            items: dict[str, QTreeWidgetItem] = {}
            visible_ids = self._visible_ids()
            for node in self._nodes:
                if node.clip_id not in visible_ids:
                    continue
                parent_item = items.get(node.group_id) if node.group_id in visible_ids else None
                item = QTreeWidgetItem(parent_item) if parent_item is not None else QTreeWidgetItem(self.tree)
                item.setFlags(
                    Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable | Qt.ItemIsDragEnabled
                    | (Qt.ItemIsDropEnabled if node.is_group else Qt.NoItemFlags)
                )
                item.setData(COL_NAME, ID_ROLE, node.clip_id)
                item.setData(COL_NAME, Qt.UserRole + 3, node.type.value)
                item.setText(COL_VISIBLE, "●" if node.visible else "○")
                item.setToolTip(COL_VISIBLE, "Afficher / masquer")
                item.setText(COL_LOCK, "■" if node.locked else "□")
                item.setToolTip(COL_LOCK, "Verrouiller / déverrouiller")
                item.setText(COL_NAME, f"{TYPE_GLYPHS.get(node.type, '•')}  {node.name}")
                item.setToolTip(COL_NAME, f"{node.name} — double-clic pour renommer")
                if node.parent_id:
                    item.setText(COL_PARENT, f"↳ {names.get(node.parent_id, node.parent_id)}")
                item.setData(COL_TIME, SPAN_ROLE, (node.start, node.end))
                item.setToolTip(COL_TIME, f"{_clock(node.start)} → {_clock(node.end)}")
                if not node.visible:
                    for column in range(5):
                        item.setForeground(column, QColor(COLORS["muted"]))
                items[node.clip_id] = item
                if node.is_group:
                    item.setExpanded(node.clip_id in expanded or True)
            for clip_id in selected_ids:
                item = items.get(clip_id)
                if item is not None:
                    item.setSelected(True)
            self.empty_hint.setVisible(not self._nodes)
            self._update_breadcrumb(names)
        finally:
            self._updating = False

    def set_playhead(self, seconds: float) -> None:
        self.playhead = float(seconds)
        self.tree.viewport().update()

    def _visible_ids(self) -> set[str]:
        if not self._root_group:
            return {node.clip_id for node in self._nodes}
        by_group: dict[str, list[str]] = {}
        for node in self._nodes:
            by_group.setdefault(node.group_id, []).append(node.clip_id)
        result: set[str] = set()
        pending = list(by_group.get(self._root_group, []))
        while pending:
            cid = pending.pop()
            if cid in result:
                continue
            result.add(cid)
            pending.extend(by_group.get(cid, []))
        return result

    def enter_group(self, group_id: str) -> None:
        """Affiche seulement l'intérieur d'un groupe (``""`` = toute la pile)."""
        self._root_group = group_id
        self.set_layers(
            self._nodes, duration=self.duration, playhead=self.playhead,
            parent_choices=self._parent_choices, motion_blur=self._motion_blur,
        )

    def _update_breadcrumb(self, names: dict[str, str]) -> None:
        if self._root_group and any(n.clip_id == self._root_group for n in self._nodes):
            self.breadcrumb.setText(f"  ›  {names.get(self._root_group, '')}")
            self.exit_group_button.show()
        else:
            self._root_group = ""
            self.breadcrumb.setText("")
            self.exit_group_button.hide()

    def selected_ids(self) -> list[str]:
        return [item.data(COL_NAME, ID_ROLE) for item in self.tree.selectedItems()]

    # -- événements -------------------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        if self._updating:
            return
        current = self.tree.currentItem()
        ids = self.selected_ids()
        if current is not None and current.isSelected():
            self.layer_selected.emit(current.data(COL_NAME, ID_ROLE))
        elif ids:
            self.layer_selected.emit(ids[0])

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        clip_id = item.data(COL_NAME, ID_ROLE)
        node = next((n for n in self._nodes if n.clip_id == clip_id), None)
        if node is None:
            return
        if column == COL_VISIBLE:
            self.visibility_toggled.emit(clip_id, not node.visible)
        elif column == COL_LOCK:
            self.lock_toggled.emit(clip_id, not node.locked)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        clip_id = item.data(COL_NAME, ID_ROLE)
        node = next((n for n in self._nodes if n.clip_id == clip_id), None)
        if node is not None and node.is_group and column != COL_NAME:
            self.enter_group(clip_id)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != COL_NAME:
            return
        clip_id = item.data(COL_NAME, ID_ROLE)
        text = item.text(COL_NAME)
        glyph_end = text.find("  ")
        name = text[glyph_end + 2:] if glyph_end >= 0 and glyph_end <= 2 else text
        if name.strip():
            self.rename_requested.emit(clip_id, name.strip())


def _clock(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, float(seconds)), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


__all__ = ["LayersPanel", "SHAPE_CHOICES"]
