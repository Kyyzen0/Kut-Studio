"""Widgets d'organisation avancée de la bibliothèque (tâche 25).

Ce module regroupe les nouveaux composants Qt qui complètent
:class:`~ui.project_panel.ProjectPanel` :

- :class:`FolderTreeWidget` : arborescence des dossiers
  personnalisables (création / renommage / suppression / déplacement) ;
- :class:`FilterChipBar` : barre de filtres rapides (Vidéo, Audio,
  Images, Utilisés, Non utilisés, Manquants) ;
- :class:`TagManagerDialog` : dialogue de gestion des tags (création,
  renommage, changement de couleur, suppression) ;
- :class:`AssetContextMenuBuilder` : fabrique du menu contextuel d'un
  asset (Renommer, Déplacer, Tags, Relier, Supprimer) ;
- :class:`AssetUsageBadge` : value-object consommé par la carte média
  pour afficher ses badges (compteur d'occurrences, indicateur de
  fichier manquant, pastilles de tags).

Aucune logique métier : les widgets publient leurs intentions via des
signaux ``*_requested`` et l'orchestration reste dans
:class:`~ui.project_panel.ProjectPanel` et
:class:`~ui.main_window.MainWindow`. Cela permet de tester chaque
widget isolément sans toucher au projet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core.library_organization import (
    FOLDER_COLOR_PALETTE,
    LibraryFolder,
    LibraryOrganization,
    LibraryTag,
    TAG_COLOR_PALETTE,
    is_asset_missing,
)
from core.workspace_state import MIN_SIZE, PanelId
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName, make_icon
from ui.theme import COLORS, label_style


# Identifiants Qt ``UserRole`` utilisés pour mapper les entrées
# d'arborescence à leurs modèles métier.
_FOLDER_ID_ROLE = Qt.UserRole + 1
_FOLDER_KIND_ROLE = Qt.UserRole + 2  # "root" ou "folder"
_FOLDER_NAME_ROLE = Qt.UserRole + 3


# ---------------------------------------------------------------------------
# Badge d'utilisation (consommé par la carte média)
# ---------------------------------------------------------------------------


@dataclass
class AssetUsageBadge:
    """Information de statut affichée sur la carte d'un média.

    Attributes:
        asset_id: Identifiant du média.
        usage_count: Nombre d'occurrences sur la timeline (``0``
            si inutilisé).
        is_missing: ``True`` si le fichier source a disparu.
        tag_colors: Couleurs hexadécimales des tags appliqués, dans
            l'ordre de la bibliothèque.
        proxy_state: état du proxy (``"ready"``, ``"generating"``,
            ``"pending"``, ``"error"``, ``"stale"``, ``"none"``), ou ``""``
            pour un média sans proxy possible (audio, image).
        proxy_progress: avancement 0–100 pendant la génération.
        proxy_error: message lisible si le proxy est en erreur.
    """

    asset_id: str
    usage_count: int = 0
    is_missing: bool = False
    tag_colors: list[str] = field(default_factory=list)
    proxy_state: str = ""
    proxy_progress: int = 0
    proxy_error: str = ""


def compute_badges(
    project,
    organization: LibraryOrganization | None = None,
    proxy_state_for=None,
) -> dict[str, AssetUsageBadge]:
    """Calcule les badges pour chaque média du projet.

    Cette fonction encapsule le couplage entre
    :class:`LibraryOrganization` et l'analyse d'usage : on importe
    :func:`usage_map` paresseusement pour éviter les boucles
    d'imports. ``proxy_state_for(asset) -> (état, progression) | None``
    renseigne l'état des proxies sans que ce module connaisse le
    gestionnaire.
    """
    from core.library_organization import usage_map

    usages = usage_map(project)
    badges: dict[str, AssetUsageBadge] = {}
    for asset in project.media_assets:
        usage = usages.get(asset.id)
        usage_count = usage.clip_count if usage is not None else 0
        assignment = organization.get_assignment(asset.id)
        tag_colors: list[str] = []
        for tag_id in assignment.tag_ids:
            tag = organization.get_tag(tag_id)
            if tag is not None:
                tag_colors.append(tag.color)
        proxy = proxy_state_for(asset) if proxy_state_for is not None else None
        badges[asset.id] = AssetUsageBadge(
            asset_id=asset.id,
            usage_count=usage_count,
            is_missing=is_asset_missing(asset),
            tag_colors=tag_colors,
            proxy_state=proxy[0] if proxy else "",
            proxy_progress=int(proxy[1]) if proxy else 0,
            proxy_error=str(proxy[2]) if proxy and len(proxy) > 2 else "",
        )
    return badges


# ---------------------------------------------------------------------------
# Arborescence des dossiers
# ---------------------------------------------------------------------------


class FolderTreeWidget(QWidget):
    """Arborescence des dossiers personnalisables de la bibliothèque.

    Le widget héberge un :class:`QTreeWidget` qui présente la
    hiérarchie des dossiers. Trois entrées synthétiques de premier
    niveau — ``Tous``, ``Racine`` (médias sans dossier) et
    ``Manquants`` — facilitent la navigation sans masquer les dossiers
    personnalisés. Ces entrées racines ne sont pas des ``LibraryFolder``
    : elles ne déclenchent aucune action CRUD.

    Signaux publiés (tous relayés par :class:`ProjectPanel`) :

    - ``folder_selected(folder_id_or_none)`` : ``None`` pour
      « Tous » / « Manquants ».
    - ``folder_create_requested(parent_id)``
    - ``folder_rename_requested(folder_id)``
    - ``folder_delete_requested(folder_id)``
    - ``folder_move_requested(folder_id, new_parent_id)``
    """

    folder_selected = Signal(object)
    folder_create_requested = Signal(object)  # parent_id or None
    folder_rename_requested = Signal(str)
    folder_delete_requested = Signal(str)
    folder_move_requested = Signal(str, object)

    _KIND_ALL = "all"
    _KIND_ROOT = "root"
    _KIND_MISSING = "missing"
    _KIND_FOLDER = "folder"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._organization: LibraryOrganization | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Petit entête : libellé de la section + bouton "+".
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(Spacing.xs)
        self.header_label = QLabel("DOSSIERS")
        self.header_label.setStyleSheet(label_style(10, "muted", 800))
        header_row.addWidget(self.header_label)
        header_row.addStretch(1)
        self.add_button = QToolButton()
        self.add_button.setObjectName("folderAddButton")
        self.add_button.setIcon(make_icon(IconName.PLUS, size=12))
        self.add_button.setToolTip("Nouveau dossier")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setFocusPolicy(Qt.NoFocus)
        self.add_button.setFixedSize(20, 20)
        self.add_button.setStyleSheet(
            f"QToolButton#folderAddButton {{ background: transparent;"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 10px; }}"
            f"QToolButton#folderAddButton:hover {{"
            f" background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']}; }}"
        )
        self.add_button.clicked.connect(self._on_add_button_clicked)
        header_row.addWidget(self.add_button)
        layout.addLayout(header_row)

        self.tree = QTreeWidget()
        self.tree.setObjectName("libraryFolderTree")
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: transparent; border: none;"
            f" outline: 0; }}"
            f"QTreeWidget::item {{ color: {COLORS['muted']};"
            f" padding: 2px 4px; border-radius: 4px;"
            f" font-size: 11px; }}"
            f"QTreeWidget::item:hover {{ color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
            f"QTreeWidget::item:selected {{ color: {COLORS['accent']};"
            f" background: transparent; }}"
        )
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._open_context_menu)
        layout.addWidget(self.tree, 1)

        # Identifiant de dossier actuellement sélectionné (``None``
        # pour « Tous » / « Racine » / « Manquants »). Sert à
        # restaurer la sélection après un ``set_organization``.
        self._selected_folder_id: str | None = None
        self._selected_kind: str = self._KIND_ALL
        self._has_missing: bool = False

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_organization(
        self,
        organization: LibraryOrganization | None,
        *,
        has_missing: bool = False,
    ) -> None:
        """Reconstruit l'arborescence pour refléter ``organization``."""
        self._organization = organization
        self._has_missing = has_missing
        self.tree.blockSignals(True)
        self.tree.clear()
        if organization is None:
            self.tree.blockSignals(False)
            return

        # Entrée « Tous » — affiche l'intégralité des médias.
        all_item = QTreeWidgetItem([_label_with_count("Tous", 0)])
        all_item.setData(0, _FOLDER_KIND_ROLE, self._KIND_ALL)
        all_item.setData(0, _FOLDER_NAME_ROLE, "Tous")
        self.tree.addTopLevelItem(all_item)

        # Entrée « Racine » — médias sans dossier.
        root_item = QTreeWidgetItem([_label_with_count("Racine", 0)])
        root_item.setData(0, _FOLDER_KIND_ROLE, self._KIND_ROOT)
        root_item.setData(0, _FOLDER_NAME_ROLE, "Racine")
        self.tree.addTopLevelItem(root_item)

        # Entrée « Manquants » — apparaît seulement si le projet a au
        # moins un média sans fichier source.
        if has_missing:
            missing_item = QTreeWidgetItem(
                [_label_with_count("Manquants", 0)]
            )
            missing_item.setData(0, _FOLDER_KIND_ROLE, self._KIND_MISSING)
            missing_item.setData(0, _FOLDER_NAME_ROLE, "Manquants")
            self.tree.addTopLevelItem(missing_item)

        # Dossiers personnalisés : on parcourt récursivement les
        # racines, puis les enfants. ``_populate_folder_item`` est
        # appelé pour chaque dossier de la bibliothèque.
        for root_folder in organization.list_folders(None):
            folder_item = self._make_folder_item(root_folder, count=0)
            self.tree.addTopLevelItem(folder_item)
            self._populate_children(folder_item, root_folder)

        # Restaure la sélection si possible, sinon sélectionne « Tous ».
        target = self._find_item_for_selection()
        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self.tree.setCurrentItem(all_item)
        self.tree.blockSignals(False)
        self._on_current_item_changed(self.tree.currentItem(), None)

    def set_folder_counts(self, counts: dict[str, int]) -> None:
        """Met à jour le nombre de médias affichés à côté de chaque dossier.

        Args:
            counts: mapping ``folder_id -> nombre``. ``"__root__"`` est
                la clé virtuelle pour la racine (médias sans dossier),
                ``"__all__"`` pour « Tous », ``"__missing__"`` pour
                « Manquants ».
        """
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            kind = item.data(0, _FOLDER_KIND_ROLE)
            folder_id = item.data(0, _FOLDER_ID_ROLE)
            if kind == self._KIND_ALL:
                count = counts.get("__all__", 0)
                item.setText(0, _label_with_count("Tous", count))
            elif kind == self._KIND_ROOT:
                count = counts.get("__root__", 0)
                item.setText(0, _label_with_count("Racine", count))
            elif kind == self._KIND_MISSING:
                count = counts.get("__missing__", 0)
                item.setText(0, _label_with_count("Manquants", count))
            elif folder_id is not None:
                count = counts.get(folder_id, 0)
                item.setText(0, _label_with_count(_folder_display_name(item), count))

    def selected_folder_id(self) -> str | None:
        """``folder_id`` sélectionné, ou ``None`` pour les racines synthétiques."""
        item = self.tree.currentItem()
        if item is None:
            return None
        kind = item.data(0, _FOLDER_KIND_ROLE)
        if kind == self._KIND_FOLDER:
            return item.data(0, _FOLDER_ID_ROLE)
        return None

    def selected_kind(self) -> str:
        """Catégorie de l'entrée sélectionnée (``all``/``root``/...)."""
        item = self.tree.currentItem()
        if item is None:
            return self._KIND_ALL
        kind = item.data(0, _FOLDER_KIND_ROLE)
        if kind is None:
            return self._KIND_ALL
        return str(kind)

    def select_folder(self, folder_id: str | None) -> None:
        """Sélectionne programmatiquement un dossier (ou racine)."""
        self._selected_folder_id = folder_id
        self._selected_kind = self._KIND_ALL
        if folder_id is None:
            # L'appelant veut la racine synthétique.
            return
        item = self._find_folder_item(folder_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    # ------------------------------------------------------------------
    # Construction interne
    # ------------------------------------------------------------------

    def _make_folder_item(
        self,
        folder: LibraryFolder,
        *,
        count: int,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([_label_with_count(folder.name, count)])
        item.setData(0, _FOLDER_ID_ROLE, folder.id)
        item.setData(0, _FOLDER_KIND_ROLE, self._KIND_FOLDER)
        item.setData(0, _FOLDER_NAME_ROLE, folder.name)
        item.setToolTip(0, folder.name)
        return item

    def _populate_children(
        self,
        parent_item: QTreeWidgetItem,
        parent_folder: LibraryFolder,
    ) -> None:
        if self._organization is None:
            return
        for child in self._organization.list_folders(parent_folder.id):
            child_item = self._make_folder_item(child, count=0)
            parent_item.addChild(child_item)
            self._populate_children(child_item, child)

    def _find_folder_item(
        self,
        folder_id: str,
    ) -> QTreeWidgetItem | None:
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            found = self._find_folder_item_recursive(item, folder_id)
            if found is not None:
                return found
        return None

    def _find_folder_item_recursive(
        self,
        item: QTreeWidgetItem | None,
        folder_id: str,
    ) -> QTreeWidgetItem | None:
        if item is None:
            return None
        if item.data(0, _FOLDER_KIND_ROLE) == self._KIND_FOLDER:
            if item.data(0, _FOLDER_ID_ROLE) == folder_id:
                return item
        for index in range(item.childCount()):
            found = self._find_folder_item_recursive(item.child(index), folder_id)
            if found is not None:
                return found
        return None

    def _find_item_for_selection(self) -> QTreeWidgetItem | None:
        if self._selected_folder_id is not None:
            item = self._find_folder_item(self._selected_folder_id)
            if item is not None:
                return item
        # Sinon, on cherche l'entrée correspondant à ``_selected_kind``.
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            kind = item.data(0, _FOLDER_KIND_ROLE)
            if kind == self._selected_kind:
                return item
        # Repli : la première entrée.
        if self.tree.topLevelItemCount() > 0:
            return self.tree.topLevelItem(0)
        return None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_current_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        kind = current.data(0, _FOLDER_KIND_ROLE)
        if kind == self._KIND_FOLDER:
            self._selected_folder_id = current.data(0, _FOLDER_ID_ROLE)
            self._selected_kind = self._KIND_FOLDER
            self.folder_selected.emit(self._selected_folder_id)
        else:
            self._selected_folder_id = None
            self._selected_kind = str(kind)
            self.folder_selected.emit(None)

    def _on_add_button_clicked(self) -> None:
        # Nouveau dossier : on le crée comme enfant du dossier
        # sélectionné si possible, sinon à la racine.
        parent_id = self.selected_folder_id()
        self.folder_create_requested.emit(parent_id)

    def _open_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            # Menu vide : seul « Nouveau dossier » est proposé.
            menu = QMenu(self)
            action_new = menu.addAction("Nouveau dossier…")
            action_new.triggered.connect(
                lambda: self.folder_create_requested.emit(None)
            )
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return

        kind = item.data(0, _FOLDER_KIND_ROLE)
        menu = QMenu(self)
        if kind == self._KIND_FOLDER:
            folder_id = item.data(0, _FOLDER_ID_ROLE)
            action_rename = menu.addAction("Renommer le dossier…")
            action_rename.triggered.connect(
                lambda: self.folder_rename_requested.emit(folder_id)
            )
            action_new_child = menu.addAction("Nouveau sous-dossier…")
            action_new_child.triggered.connect(
                lambda: self.folder_create_requested.emit(folder_id)
            )
            menu.addSeparator()
            action_delete = menu.addAction("Supprimer le dossier…")
            action_delete.triggered.connect(
                lambda: self.folder_delete_requested.emit(folder_id)
            )
        else:
            # Entrées synthétiques : seul « Nouveau dossier » est
            # proposé. Pas de renommage / suppression.
            action_new = menu.addAction("Nouveau dossier…")
            action_new.triggered.connect(
                lambda: self.folder_create_requested.emit(None)
            )
        menu.exec(self.tree.viewport().mapToGlobal(position))


def _label_with_count(label: str, count: int) -> str:
    """Formate un libellé de dossier : ``"Nom  (3)"``."""
    if count <= 0:
        return label
    return f"{label}  ({count})"


def _folder_display_name(item: QTreeWidgetItem) -> str:
    """Récupère le nom humain d'un item dossier (sans le compteur)."""
    name = item.data(0, _FOLDER_NAME_ROLE)
    if name is None:
        text = item.text(0)
        # Retire un suffixe "  (n)" éventuel si ``_FOLDER_NAME_ROLE``
        # n'a pas été renseigné.
        if "  (" in text:
            return text.rsplit("  (", 1)[0]
        return text
    return str(name)


# ---------------------------------------------------------------------------
# Barre de filtres rapides
# ---------------------------------------------------------------------------


# Identifiants des filtres rapides consommés par :class:`ProjectPanel`.
FILTER_ALL = "all"
FILTER_VIDEO = "video"
FILTER_AUDIO = "audio"
FILTER_IMAGE = "image"
FILTER_USED = "used"
FILTER_UNUSED = "unused"
FILTER_MISSING = "missing"


class _ChipFlowLayout(QLayout):
    """Layout qui aligne ses widgets à leur taille naturelle et passe à la ligne."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: N802 - API Qt
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - API Qt
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - API Qt
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - API Qt
        return Qt.Orientation(0)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - API Qt
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - API Qt
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - API Qt
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class FilterChipBar(QWidget):
    """Barre de chips horizontale pour les filtres rapides.

    Un seul filtre est actif à la fois (style segmented). Le widget
    émet ``filter_changed(filter_id)`` à chaque clic. Les chips sont
    volontairement compacts : un libellé court, pas d'icône.
    """

    filter_changed = Signal(str)

    _CHIPS: tuple[tuple[str, str], ...] = (
        (FILTER_ALL, "Tous"),
        (FILTER_VIDEO, "Vidéo"),
        (FILTER_AUDIO, "Audio"),
        (FILTER_IMAGE, "Images"),
        (FILTER_USED, "Utilisés"),
        (FILTER_UNUSED, "Non utilisés"),
        (FILTER_MISSING, "Manquants"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_id: str = FILTER_ALL
        self._buttons: dict[str, QToolButton] = {}

        # Les chips passent à la ligne quand la colonne est étroite : dans
        # une rangée horizontale simple, Qt les écrase jusqu'à masquer
        # leur libellé.
        layout = _ChipFlowLayout(self, spacing=Spacing.xs)
        # Largeur plancher : celle de la colonne Médias la plus étroite, moins ses marges (une barre plus
        # large que la colonne débordait de 4 à 6 px à 1180 px de fenêtre).
        # La hauteur suit la largeur réelle via ``resizeEvent`` plutôt que
        # ``heightForWidth``, qui ferait réclamer au panneau parent la
        # hauteur préférée de tous ses voisins.
        self.setMinimumWidth(MIN_SIZE[PanelId.MEDIA] - 4 * Spacing.xs - 1)

        self.header_label = QLabel("FILTRES")
        self.header_label.setStyleSheet(label_style(10, "muted", 800))
        layout.addWidget(self.header_label)

        for chip_id, label in self._CHIPS:
            button = self._make_chip(chip_id, label)
            self._buttons[chip_id] = button
            layout.addWidget(button)
        self._fit_height(self.minimumWidth())

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._fit_height(event.size().width())

    def _fit_height(self, width: int) -> None:
        height = self.layout().heightForWidth(max(width, self.minimumWidth()))
        if height != self.minimumHeight():
            self.setMinimumHeight(height)

    def _make_chip(self, chip_id: str, label: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(f"libraryFilterChip_{chip_id}")
        button.setText(label)
        button.setCheckable(True)
        button.setChecked(chip_id == self._active_id)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setStyleSheet(self._chip_style(active=chip_id == self._active_id))
        button.clicked.connect(lambda _checked=False, cid=chip_id: self._select(cid))
        return button

    def _chip_style(self, *, active: bool) -> str:
        if active:
            return (
                f"QToolButton {{ background: {COLORS['accent_dark']};"
                f" color: {COLORS['accent']};"
                f" border: 1px solid {COLORS['accent']};"
                f" border-radius: 10px; padding: 2px 10px;"
                f" font-size: 11px; font-weight: 600; }}"
            )
        return (
            f"QToolButton {{ background: transparent;"
            f" color: {COLORS['muted']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 10px; padding: 2px 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QToolButton:hover {{ color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
        )

    def _select(self, chip_id: str) -> None:
        if chip_id == self._active_id:
            return
        self._active_id = chip_id
        for cid, button in self._buttons.items():
            active = cid == self._active_id
            button.setChecked(active)
            button.setStyleSheet(self._chip_style(active=active))
        self.filter_changed.emit(chip_id)

    def active_filter(self) -> str:
        return self._active_id

    def set_active_filter(self, chip_id: str) -> None:
        """Sélectionne un filtre programmatiquement (émet ``filter_changed``)."""
        if chip_id not in self._buttons:
            return
        if chip_id == self._active_id:
            return
        self._active_id = chip_id
        for cid, button in self._buttons.items():
            active = cid == self._active_id
            button.setChecked(active)
            button.setStyleSheet(self._chip_style(active=active))
        self.filter_changed.emit(chip_id)


# ---------------------------------------------------------------------------
# Menu contextuel d'un asset
# ---------------------------------------------------------------------------


class AssetContextMenuBuilder(QWidget):
    """Construit le menu contextuel appliqué sur une carte média.

    Le builder ne fait *que* composer le menu : il injecte les
    actions (créées par l'appelant) et configure leur visibilité. Le
    panneau principal (ou un test) instancie le builder, appelle
    :meth:`build` et récupère un :class:`QMenu` prêt à afficher.

    La logique métier (renommage, déplacement, tags…) reste dans
    :class:`MainWindow` qui écoute les signaux ``*_requested`` émis
    par les actions du menu.
    """

    # Signaux publiés par ``build``. L'orchestrateur (ProjectPanel)
    # les relaie ensuite vers le MainWindow. On les déclare comme
    # attributs de classe : PySide6 les attache automatiquement à
    # l'instance.
    rename_requested = Signal(str)
    remove_requested = Signal(str)
    relink_requested = Signal(str)
    show_in_timeline_requested = Signal(str)
    move_to_folder_requested = Signal(str, object)
    tag_toggled = Signal(str, str, bool)
    manage_tags_requested = Signal()
    # (identifiant du média, action) : ``generate``, ``regenerate``,
    # ``delete``, ``cancel`` ou ``generate_project``.
    proxy_action_requested = Signal(str, str)

    def __init__(
        self,
        *,
        asset_id: str,
        asset_name: str,
        is_missing: bool,
        usage_count: int,
        folders: Iterable[LibraryFolder],
        tags: Iterable[LibraryTag],
        assigned_folder_id: str | None,
        assigned_tag_ids: set[str],
        parent: QWidget | None = None,
        proxy_state: str = "",
        proxy_progress: int = 0,
    ) -> None:
        super().__init__(parent)
        self.proxy_state = proxy_state
        self.proxy_progress = proxy_progress
        self.asset_id = asset_id
        self.asset_name = asset_name
        self.is_missing = is_missing
        self.usage_count = usage_count
        self.folders = list(folders)
        self.tags = list(tags)
        self.assigned_folder_id = assigned_folder_id
        self.assigned_tag_ids = set(assigned_tag_ids)

    # ------------------------------------------------------------------

    def build(self, parent: QWidget | None = None) -> QMenu:
        menu = QMenu(parent)

        # En-tête visuel : nom + statut.
        header = QWidgetAction(menu)
        header_widget = QLabel(self._make_header_text())
        header_widget.setStyleSheet(
            f"QLabel {{ color: {COLORS['text']}; padding: 6px 12px;"
            f" font-weight: 700; font-size: 12px; }}"
        )
        header.setDefaultWidget(header_widget)
        menu.addAction(header)
        menu.addSeparator()

        # Renommer.
        action_rename = menu.addAction("Renommer…")
        action_rename.triggered.connect(
            lambda: self.rename_requested.emit(self.asset_id)
        )

        # Sélectionner dans la timeline (si utilisé au moins une fois).
        if self.usage_count > 0:
            action_focus = menu.addAction(
                f"Sélectionner dans la timeline ({self.usage_count})"
            )
            action_focus.triggered.connect(
                lambda: self.show_in_timeline_requested.emit(self.asset_id)
            )

        menu.addSeparator()

        # Déplacer vers un dossier.
        move_menu = menu.addMenu("Déplacer vers")
        self._populate_move_menu(move_menu)

        # Tags.
        tags_menu = menu.addMenu("Tags")
        self._populate_tags_menu(tags_menu)

        # Relier le fichier (visible surtout si manquant, mais autorisé
        # pour tous les médias : on peut relier un média présent pour
        # corriger son chemin).
        if self.is_missing:
            action_relink = menu.addAction("Relier le fichier…")
            action_relink.setText("⚠ Relier le fichier…")
            action_relink.triggered.connect(
                lambda: self.relink_requested.emit(self.asset_id)
            )
        else:
            action_relink = menu.addAction("Relier le fichier…")
            action_relink.triggered.connect(
                lambda: self.relink_requested.emit(self.asset_id)
            )

        menu.addSeparator()
        self._populate_proxy_menu(menu)

        # Supprimer.
        action_remove = menu.addAction("Supprimer de la bibliothèque")
        action_remove.triggered.connect(
            lambda: self.remove_requested.emit(self.asset_id)
        )

        return menu

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_header_text(self) -> str:
        prefix = "⚠ " if self.is_missing else ""
        usage = f"  ·  ×{self.usage_count}" if self.usage_count > 0 else "  ·  inutilisé"
        return f"{prefix}{self.asset_name}{usage}"

    def _populate_proxy_menu(self, menu: QMenu) -> None:
        """Sous-menu « Proxy » : actions selon l'état (vidéo uniquement)."""
        if not self.proxy_state:
            return
        from ui.i18n import translate

        proxy_menu = menu.addMenu(translate("proxy.menu.title"))
        status = proxy_menu.addAction(
            translate(f"proxy.state.{self.proxy_state}", progress=self.proxy_progress)
        )
        status.setEnabled(False)
        proxy_menu.addSeparator()
        state = self.proxy_state

        def add(label_key: str, action: str) -> None:
            entry = proxy_menu.addAction(translate(label_key))
            entry.triggered.connect(
                lambda _checked=False, a=action: self.proxy_action_requested.emit(self.asset_id, a)
            )

        if state in ("pending", "generating"):
            add("proxy.action.cancel", "cancel")
        elif state == "ready":
            add("proxy.action.regenerate", "regenerate")
            add("proxy.action.delete", "delete")
        else:  # none, error, stale
            add("proxy.action.generate", "generate")
            if state == "stale":
                add("proxy.action.delete", "delete")
        proxy_menu.addSeparator()
        add("proxy.action.generate_project", "generate_project")

    def _populate_move_menu(self, menu: QMenu) -> None:
        # Option racine : « Pas de dossier ».
        root_action = menu.addAction("Racine")
        root_action.setCheckable(True)
        root_action.setChecked(self.assigned_folder_id is None)
        root_action.triggered.connect(
            lambda: self.move_to_folder_requested.emit(self.asset_id, None)
        )
        if not self.folders:
            placeholder = menu.addAction("(aucun dossier personnalisé)")
            placeholder.setEnabled(False)
            return
        menu.addSeparator()
        self._populate_move_menu_recursive(menu, None, 0)

    def _populate_move_menu_recursive(
        self,
        menu: QMenu,
        parent_id: str | None,
        depth: int,
    ) -> None:
        children = [f for f in self.folders if f.parent_id == parent_id]
        # Tri alphabétique pour cohérence avec l'arborescence.
        children.sort(key=lambda f: f.name.lower())
        for folder in children:
            indent = "    " * depth
            action = menu.addAction(f"{indent}{folder.name}")
            action.setCheckable(True)
            action.setChecked(self.assigned_folder_id == folder.id)
            action.triggered.connect(
                lambda _checked=False, fid=folder.id: self.move_to_folder_requested.emit(
                    self.asset_id, fid
                )
            )
            # Récursion : on ajoute directement dans le même menu, ce
            # qui aplatit l'affichage. Une vraie hiérarchie serait plus
            # lisible mais consomme plus de surface ; pour des projets
            # de taille raisonnable (≤ 5 niveaux), l'aplatissement est
            # acceptable.
            self._populate_move_menu_recursive(menu, folder.id, depth + 1)

    def _populate_tags_menu(self, menu: QMenu) -> None:
        if not self.tags:
            placeholder = menu.addAction("(aucun tag — ouvrez le gestionnaire)")
            placeholder.setEnabled(False)
        else:
            for tag in self.tags:
                action = menu.addAction(f"  {tag.name}")
                action.setCheckable(True)
                assigned = tag.id in self.assigned_tag_ids
                action.setChecked(assigned)
                # Petite pastille de couleur : on laisse Qt afficher le
                # texte, l'icône colorée demanderait un setIcon().
                action.triggered.connect(
                    lambda _checked=False, tid=tag.id, was_assigned=assigned: (
                        self.tag_toggled.emit(self.asset_id, tid, not was_assigned)
                    )
                )
        menu.addSeparator()
        action_manage = menu.addAction("Gérer les tags…")
        action_manage.triggered.connect(self.manage_tags_requested.emit)


# ---------------------------------------------------------------------------
# Dialogue de gestion des tags
# ---------------------------------------------------------------------------


class TagManagerDialog(QDialog):
    """Dialogue de gestion des tags (créer, renommer, recolorer, supprimer).

    Le dialogue fonctionne avec une référence à
    :class:`LibraryOrganization` : toutes les modifications sont
    appliquées *en place*. Les erreurs (nom vide, doublon) sont
    affichées via :class:`QMessageBox` sans fermer la fenêtre, pour
    permettre à l'utilisateur de corriger sans tout recommencer.
    """

    def __init__(
        self,
        organization: LibraryOrganization,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gestionnaire de tags")
        self.resize(420, 360)
        self._organization = organization
        self._tag_rows: dict[str, "_TagRow"] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        layout.setSpacing(Spacing.sm)

        intro = QLabel(
            "Créez et organisez les tags à appliquer aux médias. Les "
            "couleurs sont facultatives et purement visuelles."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(intro)

        # --- Liste scrollable des tags existants -------------------------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ background: {COLORS['panel']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
        )
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(Spacing.xs)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        layout.addWidget(self.scroll, 1)

        # --- Formulaire de création --------------------------------------
        form_box = QFrame()
        form_box.setStyleSheet(
            f"QFrame {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
        )
        form_layout = QFormLayout(form_box)
        form_layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm
        )
        form_layout.setSpacing(Spacing.xs)
        self.name_field = QLineEdit()
        self.name_field.setPlaceholderText("Nom du tag…")
        self.name_field.setMaxLength(48)
        self.color_button = QPushButton()
        self.color_button.setText("Choisir une couleur…")
        self.color_button.setCursor(Qt.PointingHandCursor)
        self._new_tag_color: str = TAG_COLOR_PALETTE[0]
        self.color_button.clicked.connect(self._pick_color)
        self._apply_color_to_button()
        self.create_button = QPushButton("Créer")
        self.create_button.setCursor(Qt.PointingHandCursor)
        self.create_button.clicked.connect(self._create_tag)
        form_layout.addRow("Nom", self.name_field)
        form_layout.addRow("Couleur", self.color_button)
        form_layout.addRow("", self.create_button)
        layout.addWidget(form_box)

        # --- Boutons OK / Annuler ----------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._rebuild_list()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reconstruit la liste après une modification externe."""
        self._rebuild_list()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _rebuild_list(self) -> None:
        # Purge l'existant.
        for row in list(self._tag_rows.values()):
            row.setParent(None)
            row.deleteLater()
        self._tag_rows.clear()
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        tags = self._organization.list_tags()
        if not tags:
            empty = QLabel("Aucun tag pour l'instant.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(label_style(11, "muted", 500))
            self.list_layout.insertWidget(0, empty)
            return

        for tag in tags:
            row = _TagRow(
                tag=tag,
                on_rename=self._rename_tag,
                on_recolor=self._recolor_tag,
                on_delete=self._delete_tag,
            )
            self._tag_rows[tag.id] = row
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _pick_color(self) -> None:
        initial = QColor(self._new_tag_color)
        chosen = QColorDialog.getColor(
            initial, self, "Couleur du tag", QColorDialog.DontUseNativeDialog
        )
        if chosen.isValid():
            self._new_tag_color = chosen.name()
            self._apply_color_to_button()

    def _apply_color_to_button(self) -> None:
        self.color_button.setStyleSheet(
            f"QPushButton {{ background: {self._new_tag_color};"
            f" color: white; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 6px; }}"
        )

    def _create_tag(self) -> None:
        name = self.name_field.text().strip()
        if not name:
            _show_warning(self, "Le nom du tag ne peut pas être vide.")
            return
        try:
            self._organization.create_tag(name, color=self._new_tag_color)
        except Exception as exc:  # noqa: BLE001 - on remonte tout
            _show_warning(self, str(exc))
            return
        self.name_field.clear()
        self._rebuild_list()

    def _rename_tag(self, tag_id: str) -> None:
        tag = self._organization.get_tag(tag_id)
        if tag is None:
            return
        new_name, accepted = QInputDialog.getText(
            self,
            "Renommer le tag",
            "Nouveau nom :",
            text=tag.name,
        )
        if not accepted:
            return
        try:
            self._organization.rename_tag(tag_id, new_name)
        except Exception as exc:  # noqa: BLE001
            _show_warning(self, str(exc))
            return
        self._rebuild_list()

    def _recolor_tag(self, tag_id: str) -> None:
        tag = self._organization.get_tag(tag_id)
        if tag is None:
            return
        chosen = QColorDialog.getColor(
            QColor(tag.color), self, "Couleur du tag",
            QColorDialog.DontUseNativeDialog,
        )
        if not chosen.isValid():
            return
        try:
            self._organization.recolor_tag(tag_id, chosen.name())
        except Exception as exc:  # noqa: BLE001
            _show_warning(self, str(exc))
            return
        self._rebuild_list()

    def _delete_tag(self, tag_id: str) -> None:
        try:
            self._organization.delete_tag(tag_id)
        except Exception as exc:  # noqa: BLE001
            _show_warning(self, str(exc))
            return
        self._rebuild_list()


class _TagRow(QFrame):
    """Ligne éditable d'un tag dans le dialogue de gestion."""

    def __init__(
        self,
        *,
        tag: LibraryTag,
        on_rename,
        on_recolor,
        on_delete,
    ) -> None:
        super().__init__()
        self.setObjectName("tagManagerRow")
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QFrame#tagManagerRow {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, 4, Spacing.sm, 4)
        layout.setSpacing(Spacing.xs)

        color_chip = QLabel()
        color_chip.setFixedSize(14, 14)
        color_chip.setStyleSheet(
            f"background: {tag.color}; border-radius: 7px;"
            f" border: 1px solid {COLORS['border']};"
        )
        layout.addWidget(color_chip)

        name_label = QLabel(tag.name)
        name_label.setStyleSheet(label_style(11, "text", 600))
        layout.addWidget(name_label, 1)

        rename_button = QToolButton()
        rename_button.setIcon(make_icon(IconName.EDIT, size=12))
        rename_button.setToolTip("Renommer")
        rename_button.setCursor(Qt.PointingHandCursor)
        rename_button.setFocusPolicy(Qt.NoFocus)
        rename_button.clicked.connect(lambda: on_rename(tag.id))
        layout.addWidget(rename_button)

        recolor_button = QToolButton()
        recolor_button.setIcon(make_icon(IconName.EDIT, size=12))
        recolor_button.setToolTip("Changer la couleur")
        recolor_button.setCursor(Qt.PointingHandCursor)
        recolor_button.setFocusPolicy(Qt.NoFocus)
        recolor_button.clicked.connect(lambda: on_recolor(tag.id))
        layout.addWidget(recolor_button)

        delete_button = QToolButton()
        delete_button.setIcon(make_icon(IconName.CLOSE, size=12))
        delete_button.setToolTip("Supprimer")
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.setFocusPolicy(Qt.NoFocus)
        delete_button.clicked.connect(lambda: on_delete(tag.id))
        layout.addWidget(delete_button)


def _show_warning(parent: QWidget, message: str) -> None:
    """Affiche un QMessageBox d'avertissement sans fermer le parent."""
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.warning(parent, "Tags", message)


# ---------------------------------------------------------------------------
# Dialogue de création / renommage d'un dossier
# ---------------------------------------------------------------------------


def prompt_for_folder_name(
    *,
    title: str,
    label: str,
    initial: str = "",
    colors: Iterable[str] = FOLDER_COLOR_PALETTE,
    parent: QWidget | None = None,
) -> tuple[str, str] | None:
    """Demande un nom de dossier + couleur. Retourne ``(name, color)``.

    Retourne ``None`` si l'utilisateur annule. Le nom est strippé ;
    la couleur peut être la chaîne vide (pas de couleur personnalisée).
    """
    name, accepted = QInputDialog.getText(
        parent, title, label, text=initial,
    )
    if not accepted:
        return None
    cleaned = (name or "").strip()
    if not cleaned:
        _show_warning(parent, "Le nom du dossier ne peut pas être vide.")
        return None
    if len(cleaned) > 48:
        _show_warning(parent, "Le nom du dossier est trop long (max 48 caractères).")
        return None
    color_list = list(colors)
    if not color_list:
        return cleaned, ""
    chosen = QColorDialog.getColor(
        QColor(color_list[0]), parent, "Couleur du dossier",
        QColorDialog.DontUseNativeDialog,
    )
    if not chosen.isValid():
        return cleaned, ""
    return cleaned, chosen.name()


__all__ = [
    "AssetContextMenuBuilder",
    "AssetUsageBadge",
    "FILTER_ALL",
    "FILTER_AUDIO",
    "FILTER_IMAGE",
    "FILTER_MISSING",
    "FILTER_UNUSED",
    "FILTER_USED",
    "FILTER_VIDEO",
    "FilterChipBar",
    "FolderTreeWidget",
    "TagManagerDialog",
    "compute_badges",
    "prompt_for_folder_name",
]