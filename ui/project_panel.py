"""Panneau bibliothèque de Kut-Studio.

Refonte UI/UX :

- en-tête compact : onglets Projet / Favoris + compteur ;
- champ de recherche global filtrant les assets ;
- arborescence de dossiers (aplatie en sous-sections) ;
- vignettes média en grille avec miniature, nom court et durée ;
- la sélection est marquée par un filet turquoise fin.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QMimeData, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MediaAsset
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconLabel, IconName, make_icon
from ui.theme import COLORS, label_style


class ProjectPanel(QWidget):
    """Bibliothèque de médias et de sous-titres du projet courant."""

    asset_selected = Signal(str)
    add_to_timeline_requested = Signal(str)
    import_requested = Signal()
    add_subtitle_requested = Signal(str, float)
    import_subtitles_requested = Signal()
    export_subtitles_requested = Signal()
    subtitle_selected = Signal(str)
    add_transition_requested = Signal(str, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("project_panel")
        self.setStyleSheet(
            f"QWidget#project_panel {{ background: {COLORS['panel']}; "
            f"border-right: 1px solid {COLORS['border']}; }}"
        )
        # ``_search_text`` filtre les assets affichés dans la grille.
        self._search_text: str = ""
        self._active_scope: str = "project"  # ou "favorites"
        # La section active est choisie par le rail global ou la barre
        # supérieure. Le panneau ne duplique pas cette navigation.
        self._active_page_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ----- En-tête compact : onglets Projet / Favoris --------------
        header = QWidget()
        header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.xs)
        header_layout.setSpacing(Spacing.xs)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(Spacing.sm)
        title = QLabel("BIBLIOTHÈQUE")
        title.setStyleSheet(label_style(10, "muted", 800))
        title_row.addWidget(title)
        title_row.addStretch(1)
        # Compteur global (mis à jour à chaque mutation).
        self.media_count = QLabel("0 média")
        self.media_count.setStyleSheet(label_style(10, "muted", 500))
        title_row.addWidget(self.media_count)
        header_layout.addLayout(title_row)

        # Onglets Projet / Favoris : boutons ``checkable`` dans une
        # rangée horizontale pour un rendu segmented compact.
        scope_tabs_row = QWidget()
        scope_tabs_layout = QHBoxLayout(scope_tabs_row)
        scope_tabs_layout.setContentsMargins(0, 0, 0, 0)
        scope_tabs_layout.setSpacing(Spacing.xs)
        self.scope_tab_buttons: list[QPushButton] = []
        for index, label in enumerate(("Projet", "Favoris")):
            button = QPushButton(label)
            button.setObjectName("scopeTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"QPushButton#scopeTab {{ background: transparent;"
                f" color: {COLORS['muted']}; border: 1px solid {COLORS['border']};"
                f" border-radius: 6px; padding: 4px 12px;"
                f" font-weight: 600; font-size: 11px; }}"
                f"QPushButton#scopeTab:hover {{ color: {COLORS['text']};"
                f" background: {COLORS['surface_hover']}; }}"
                f"QPushButton#scopeTab:checked {{ color: {COLORS['accent']};"
                f" background: {COLORS['accent_dark']};"
                f" border: 1px solid {COLORS['accent']}; }}"
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_scope(idx)
            )
            self.scope_tab_buttons.append(button)
            scope_tabs_layout.addWidget(button)
        scope_tabs_layout.addStretch(1)
        header_layout.addWidget(scope_tabs_row)
        layout.addWidget(header)

        # ----- Champ de recherche --------------------------------------
        search_row = QWidget()
        search_row.setStyleSheet(f"background: {COLORS['panel']};")
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.sm)
        search_layout.setSpacing(Spacing.sm)
        self.search_field = QLineEdit()
        self.search_field.setObjectName("librarySearch")
        self.search_field.setPlaceholderText("Rechercher dans la bibliothèque…")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(28)
        self.search_field.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.search_field)
        layout.addWidget(search_row)

        # ----- Arborescence de la bibliothèque -------------------------
        # Les modes globaux (Médias, Audio, Texte, Effets, Transitions)
        # vivent déjà dans le rail et les menus du haut : les répéter ici
        # encombrait la colonne sans offrir d'action supplémentaire.
        browse_content = QWidget()
        browse_content.setObjectName("libraryBrowse")
        browse_content.setStyleSheet(
            f"background: {COLORS['panel']};"
            f" border-top: 1px solid {COLORS['border']};"
        )
        browse_layout = QVBoxLayout(browse_content)
        browse_layout.setContentsMargins(
            Spacing.xs, Spacing.xs, Spacing.xs, Spacing.xs
        )
        browse_layout.setSpacing(0)

        # -- Arborescence de dossiers (aplatie) --
        folders_sep = QWidget()
        folders_sep.setFixedHeight(1)
        folders_sep.setStyleSheet(f"background: {COLORS['border']};")
        browse_layout.addSpacing(Spacing.xs)
        browse_layout.addWidget(folders_sep)
        browse_layout.addSpacing(Spacing.xs)

        self.folder_list = QListWidget()
        self.folder_list.setObjectName("folderList")
        self.folder_list.setFocusPolicy(Qt.NoFocus)
        self.folder_list.setFixedHeight(70)
        self.folder_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.folder_list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none;"
            f" outline: 0; }}"
            f"QListWidget::item {{ color: {COLORS['muted']};"
            f" padding: 2px 6px; border-radius: 4px;"
            f" font-size: 11px; }}"
            f"QListWidget::item:hover {{ color: {COLORS['text']}; }}"
            f"QListWidget::item:selected {{ color: {COLORS['accent']};"
            f" background: transparent; }}"
        )
        folders = (
            ("▶  Vidéos du projet", "videos"),
            ("▶  Audio", "audios"),
            ("▶  Sous-titres", "subtitles"),
        )
        for label, folder_id in folders:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, folder_id)
            self.folder_list.addItem(item)
        self.folder_list.itemClicked.connect(self._open_folder)
        browse_layout.addWidget(self.folder_list)
        browse_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(browse_content, 0)

        # ----- Contenu empilé (grilles + placeholders) ----------------
        # Seule zone élastique du panneau : elle absorbe toute la
        # hauteur restante, les blocs au-dessus étant figés.
        self.content_stack = QStackedWidget()
        self.content_stack.setMinimumHeight(120)
        self.content_stack.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self.bin_videos = self._make_grid_bin()
        self.content_stack.addWidget(self.bin_videos)

        self.bin_audios = self._make_grid_bin()
        self.content_stack.addWidget(self.bin_audios)

        self.subtitle_view = SubtitleLibraryView(self)
        self.content_stack.addWidget(self.subtitle_view)

        self._effects_placeholder = self._make_placeholder_label(
            "Effets visuels\n\nCouleur, recadrage, filtres\n(à implémenter)"
        )
        self.transition_view = TransitionLibraryView(self)
        self.content_stack.addWidget(self._effects_placeholder)
        self.content_stack.addWidget(self.transition_view)
        # Chaque page est faite pour défiler : on neutralise leur
        # ``minimumSizeHint`` (l'éditeur de sous-titres réclame 360 px),
        # sinon la pile réserve cette hauteur et la grille de vignettes
        # disparaît. On ne fige pas la hauteur : le layout accorde le
        # surplus quand la colonne est haute.
        for page in (
            self.bin_videos, self.bin_audios, self.subtitle_view,
            self._effects_placeholder, self.transition_view,
        ):
            page.setMinimumHeight(0)
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(self.content_stack, 1)

        # ----- Boutons d'action principaux -----------------------------
        # Une seule rangée : empilés, ils consommaient ~90 px de hauteur
        # qui revient à la grille de vignettes. L'action primaire garde
        # l'accent turquoise, l'import reste secondaire.
        actions = QWidget()
        actions.setStyleSheet(
            f"background: {COLORS['panel']}; border-top: 1px solid {COLORS['border']};"
        )
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.md)
        actions_layout.setSpacing(Spacing.xs)
        self.import_button = self._make_wide_button(
            IconName.IMPORT, "Importer",
            accent=False,
            tooltip="Importer des médias dans le projet",
        )
        self.import_button.clicked.connect(self.import_requested)
        actions_layout.addWidget(self.import_button, 1)

        self.add_to_timeline_button = self._make_wide_button(
            IconName.PLUS, "Timeline",
            accent=True,
            tooltip="Ajouter le média sélectionné à la timeline",
        )
        self.add_to_timeline_button.setEnabled(False)
        self.add_to_timeline_button.clicked.connect(self._on_add_to_timeline_clicked)
        actions_layout.addWidget(self.add_to_timeline_button, 1)
        layout.addWidget(actions)

        # Câblage des signaux du composant Sous-titres.
        self.subtitle_view.add_requested.connect(self._on_add_subtitle_clicked)
        self.subtitle_view.import_requested.connect(self.import_subtitles_requested)
        self.subtitle_view.export_requested.connect(self.export_subtitles_requested)
        self.subtitle_view.selected.connect(self.subtitle_selected)
        self.transition_view.add_requested.connect(self.add_transition_requested)

    # ------------------------------------------------------------------
    # Filtres et scopes
    # ------------------------------------------------------------------

    def _select_scope(self, index: int) -> None:
        """Bascule l'onglet actif et applique le filtre."""
        for i, button in enumerate(self.scope_tab_buttons):
            button.setChecked(i == index)
        self._active_scope = "favorites" if index == 1 else "project"
        self._refresh_grids()

    def _on_scope_changed(self, row: int) -> None:
        """Bascule entre les scopes Projet et Favoris."""
        self._active_scope = "favorites" if row == 1 else "project"
        self._refresh_grids()

    def _on_search_changed(self, text: str) -> None:
        """Filtre les assets par nom au fil de la saisie."""
        self._search_text = text.strip().lower()
        self._refresh_grids()

    def _open_folder(self, item: QListWidgetItem) -> None:
        """Ouvre le contenu associé à un dossier de la bibliothèque."""
        folder_sections = {
            "videos": "media",
            "audios": "audio",
            "subtitles": "text",
        }
        self.select_section(folder_sections.get(item.data(Qt.UserRole), "media"))

    def select_section(self, section_id: str) -> None:
        """Affiche la bibliothèque demandée par la navigation globale."""
        page_index = {
            "media": 0,
            "audio": 1,
            "text": 2,
            "effects": 3,
            "transitions": 4,
        }.get(section_id)
        if page_index is None:
            return
        self._active_page_index = page_index
        self.content_stack.setCurrentIndex(page_index)
        self._refresh_count()
        self._sync_add_button_for_active_tab()

    def _filter_assets(self, assets: list[MediaAsset]) -> list[MediaAsset]:
        """Applique le filtre de recherche courant."""
        if not self._search_text:
            return assets
        needle = self._search_text
        return [a for a in assets if needle in a.name.lower()]

    def _refresh_grids(self) -> None:
        """Réaffiche les assets visibles selon le scope et la recherche."""
        # On garde la sélection courante si possible.
        selected = self.selected_asset_id
        videos = self.bin_videos.all_assets()
        audios = self.bin_audios.all_assets()
        if self._active_scope == "favorites":
            # Pour l'instant, le projet ne gère pas de favoris : on
            # montre un placeholder honnête plutôt que de simuler des
            # données.
            self.bin_videos.set_assets([])
            self.bin_audios.set_assets([])
            self.media_count.setText("Aucun favori")
            return
        self.bin_videos.set_assets(self._filter_assets(videos))
        self.bin_audios.set_assets(self._filter_assets(audios))
        self._refresh_count()
        if selected:
            for bin_widget in (self.bin_videos, self.bin_audios):
                for row in range(bin_widget.count()):
                    if bin_widget.item(row).data(Qt.UserRole) == selected:
                        bin_widget.setCurrentRow(row)
                        return

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @staticmethod
    def _make_wide_button(
        icon: IconName,
        text: str,
        *,
        accent: bool = False,
        tooltip: str | None = None,
    ) -> IconButton:
        button = IconButton(
            icon=icon,
            tooltip=tooltip or text,
            size=Sizes.icon_button,
            accent=accent,
            square=False,
        )
        button.setText(f"  {text}")
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setMinimumHeight(Sizes.button_md)
        # Le bouton s'étend pour suivre la largeur du panneau parent.
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return button

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_assets(self, assets: list[MediaAsset]) -> None:
        videos = [a for a in assets if a.media_type == "video"]
        audios = [a for a in assets if a.media_type == "audio"]
        self._populate_bin(self.bin_videos, videos)
        self._populate_bin(self.bin_audios, audios)
        self._refresh_count()
        self._sync_add_button_for_active_tab()

    def set_subtitle_clips(self, clips: list) -> None:
        sorted_clips = sorted(clips, key=lambda c: c.timeline_start)
        self.subtitle_view.set_clips(sorted_clips)
        if self._active_page_index == 2:
            self._refresh_count()

    def select_asset(self, asset_id: str) -> None:
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                item = bin_widget.item(row)
                if item.data(Qt.UserRole) == asset_id:
                    self.select_section(
                        "media" if bin_widget is self.bin_videos else "audio"
                    )
                    bin_widget.setCurrentRow(row)
                    return

    def asset_ids(self) -> list[str]:
        ids: list[str] = []
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                ids.append(bin_widget.item(row).data(Qt.UserRole))
        return ids

    @property
    def selected_asset_id(self) -> str | None:
        return self._current_bin().selected_asset_id

    @property
    def selected_media_type(self) -> str | None:
        asset_id = self.selected_asset_id
        if asset_id is None:
            return None
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                if bin_widget.item(row).data(Qt.UserRole) == asset_id:
                    return "video" if bin_widget is self.bin_videos else "audio"
        return None

    # ------------------------------------------------------------------
    # Helpers privés
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers privés
    # ------------------------------------------------------------------

    def _make_grid_bin(self) -> "AssetBin":
        return AssetBin(
            on_item_clicked=lambda asset_id: self.asset_selected.emit(asset_id),
            on_selection_changed=lambda asset_id: self._on_bin_selection_changed(asset_id),
        )

    def _make_bin(self) -> "AssetBin":
        # Conservé pour compatibilité ascendante : la grille utilise
        # désormais ``_make_grid_bin`` ; ``_make_bin`` reste un alias.
        return self._make_grid_bin()

    def _populate_bin(self, bin_widget: "AssetBin", assets: list[MediaAsset]) -> None:
        bin_widget.clear()
        for asset in assets:
            bin_widget.add_asset(asset)

    def _current_bin(self) -> "AssetBin":
        widget = self.content_stack.currentWidget()
        if isinstance(widget, AssetBin):
            return widget
        return self.bin_videos

    def _on_bin_selection_changed(self, asset_id: str | None) -> None:
        self.add_to_timeline_button.setEnabled(asset_id is not None)

    def _sync_add_button_for_active_tab(self) -> None:
        self.add_to_timeline_button.setEnabled(
            self._current_bin().selected_asset_id is not None
        )

    def _on_add_to_timeline_clicked(self) -> None:
        asset_id = self._current_bin().selected_asset_id
        if asset_id is None:
            self.add_to_timeline_button.setEnabled(False)
            return
        self.add_to_timeline_requested.emit(asset_id)

    def _on_add_subtitle_clicked(self, text: str, duration: float) -> None:
        self.add_subtitle_requested.emit(text, duration)

    def _refresh_count(self) -> None:
        # Le titre statique ``BIBLIOTHÈQUE`` dans l'en-tête suffit : le
        # nombre d'éléments reflète la section courante.
        if self._active_scope == "favorites":
            self.media_count.setText("Aucun favori")
            return
        if self._active_page_index == 0:
            count = self.bin_videos.count()
            label_word = "média" if count <= 1 else "médias"
        elif self._active_page_index == 1:
            count = self.bin_audios.count()
            label_word = "audio" if count <= 1 else "audios"
        elif self._active_page_index == 2:
            count = self.subtitle_view.count()
            label_word = "sous-titre" if count <= 1 else "sous-titres"
        else:
            count = 0
            label_word = "média"
        self.media_count.setText(f"{count} {label_word}")

    def _make_placeholder_label(self, message: str) -> QLabel:
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(label_style(13, "muted", 500))
        return label


class AssetBin(QWidget):
    """Sous-widget : grille d'assets filtrée avec son propre état de sélection.

    Affiche chaque asset comme une vignette :

    - une miniature générée à partir de l'icône du type de média ;
    - le nom court ;
    - la durée formatée.

    La sélection est marquée par un filet turquoise fin et un fond
    vert foncé subtil — fidèle à la direction artistique premium.

    L'implémentation repose sur un ``QListWidget`` en mode ``ListMode``
    avec un délégué custom qui peint chaque ligne comme une carte
    horizontale (poster + nom + durée). C'est plus simple et plus
    prévisible que ``IconMode``.
    """

    # Délégué custom : dessine une carte horizontale par ligne.
    class _CardDelegate(QStyledItemDelegate):
        CARD_HEIGHT = 52
        POSTER = 40
        PADDING = 5

        def sizeHint(self, option, index):  # noqa: D401 - Qt
            width = option.rect.width() if option.rect.width() > 0 else 280
            return QSize(width, self.CARD_HEIGHT + self.PADDING)

        def paint(self, painter, option, index):  # noqa: D401 - Qt
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)

            rect = option.rect.adjusted(2, 2, -2, -2)
            radius = 8
            selected = bool(option.state & QStyle.State_Selected)
            hovered = bool(option.state & QStyle.State_MouseOver)
            base = COLORS["panel_alt"]
            if selected:
                base = COLORS["accent_dark"]
            elif hovered:
                base = COLORS["surface_hover"]
            border_color = QColor(COLORS["accent"] if selected else COLORS["border"])

            path = QPainterPath()
            path.addRoundedRect(rect.toRectF(), radius, radius)
            painter.fillPath(path, QColor(base))
            painter.setPen(QPen(border_color, 1 if not selected else 1.4))
            painter.drawPath(path)

            # Poster carré à gauche, à la taille exacte du widget pour
            # éviter toute déformation du film.
            side = min(self.POSTER, rect.height() - 2 * self.PADDING)
            poster_rect = QRect(
                rect.left() + self.PADDING,
                rect.top() + (rect.height() - side) // 2,
                side,
                side,
            )
            asset = index.data(Qt.UserRole + 1)
            if asset is not None:
                poster = _make_asset_thumbnail(asset, size=side)
                painter.drawPixmap(poster_rect, poster.pixmap(side, side))

            # Nom + durée à droite du poster.
            text_left = poster_rect.right() + self.PADDING * 2
            text_rect = QRect(text_left, rect.top() + self.PADDING,
                              max(10, rect.right() - self.PADDING - text_left),
                              rect.height() - 2 * self.PADDING)
            name = index.data(Qt.DisplayRole) or ""
            duration = index.data(Qt.UserRole + 2) or ""
            name_pen = QColor(COLORS["text"] if selected else COLORS["text"])
            painter.setPen(name_pen)
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            name_rect = text_rect.adjusted(0, 0, 0, -text_rect.height() // 2)
            painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                             painter.fontMetrics().elidedText(
                                 str(name), Qt.ElideRight, name_rect.width()
                             ))
            font.setBold(False)
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QColor(COLORS["accent"] if selected else COLORS["muted"]))
            duration_rect = text_rect.adjusted(0, text_rect.height() // 2, 0, 0)
            painter.drawText(duration_rect, Qt.AlignVCenter | Qt.AlignLeft,
                             str(duration))

            painter.restore()

    def __init__(self, on_item_clicked, on_selection_changed, parent=None) -> None:
        super().__init__(parent)
        self._assets: dict[str, MediaAsset] = {}
        # Sans cette politique, le QStackedWidget plafonne la page à sa
        # taille naturelle et la grille n'affiche qu'une vignette.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.xs, Spacing.xs, Spacing.xs, Spacing.xs)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setAcceptDrops(False)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none;"
            f" outline: 0; padding: 2px; }}"
            f"QListWidget::item {{ background: transparent;"
            f" border: none; padding: 0; margin: 2px 0; }}"
        )
        self._delegate = self._CardDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.itemClicked.connect(
            lambda item: on_item_clicked(item.data(Qt.UserRole))
        )
        self._list.mousePressEvent = self._wrap_mouse_press(self._list.mousePressEvent)
        self._list.mouseMoveEvent = self._wrap_mouse_move(self._list.mouseMoveEvent)
        self._list.currentRowChanged.connect(
            lambda row: on_selection_changed(
                self._list.item(row).data(Qt.UserRole)
                if 0 <= row < self._list.count()
                else None
            )
        )
        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def add_asset(self, asset: MediaAsset) -> None:
        self._assets[asset.id] = asset
        item = QListWidgetItem()
        item.setData(Qt.UserRole, asset.id)
        item.setData(Qt.UserRole + 1, asset)
        item.setData(Qt.UserRole + 2, _format_duration(asset.duration))
        item.setData(Qt.DisplayRole, asset.name)
        item.setToolTip(f"{asset.name}\n{_format_duration(asset.duration)}")
        self._list.addItem(item)

    def set_assets(self, assets: list[MediaAsset]) -> None:
        """Remplace le contenu de la grille."""
        self._list.clear()
        self._assets = {a.id: a for a in assets}
        for asset in assets:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, asset.id)
            item.setData(Qt.UserRole + 1, asset)
            item.setData(Qt.UserRole + 2, _format_duration(asset.duration))
            item.setData(Qt.DisplayRole, asset.name)
            item.setToolTip(f"{asset.name}\n{_format_duration(asset.duration)}")
            self._list.addItem(item)

    def all_assets(self) -> list[MediaAsset]:
        """Retourne tous les assets connus (sans filtre)."""
        return list(self._assets.values())

    def clear(self) -> None:
        self._list.clear()
        self._assets.clear()

    def count(self) -> int:
        return self._list.count()

    def item(self, row: int) -> QListWidgetItem:
        return self._list.item(row)

    def setCurrentRow(self, row: int) -> None:
        self._list.setCurrentRow(row)

    def currentRow(self) -> int:
        return self._list.currentRow()

    @property
    def selected_asset_id(self) -> str | None:
        row = self._list.currentRow()
        if not (0 <= row < self._list.count()):
            return None
        return self._list.item(row).data(Qt.UserRole)

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def _wrap_mouse_press(self, original):
        outer = self

        def handler(event):
            outer._drag_origin = event.position()
            outer._dragging = False
            return original(event)

        return handler

    def _wrap_mouse_move(self, original):
        outer = self

        def handler(event):
            if (
                event.buttons() & Qt.LeftButton
                and getattr(outer, "_drag_origin", None) is not None
                and not getattr(outer, "_dragging", False)
            ):
                start = outer._drag_origin
                distance = (
                    (event.position().x() - start.x()) ** 2
                    + (event.position().y() - start.y()) ** 2
                ) ** 0.5
                if distance >= QApplication.startDragDistance():
                    item = outer._list.currentItem()
                    if item is not None:
                        asset_id = item.data(Qt.UserRole)
                        outer._start_drag(asset_id)
            return original(event)

        return handler

    def _start_drag(self, asset_id: str) -> None:
        mime = QMimeData()
        mime.setData("application/x-kut-studio-asset-id", asset_id.encode("utf-8"))
        drag = QDrag(self._list)
        drag.setMimeData(mime)
        self._dragging = True
        drag.exec(Qt.CopyAction, Qt.CopyAction)


class SubtitleLibraryView(QWidget):
    """Sous-panneau « Texte » : édition + bibliothèque des sous-titres du projet."""

    add_requested = Signal(str, float)
    import_requested = Signal()
    export_requested = Signal()
    selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background: {COLORS['panel']}; border: none; }}"
        )
        content = QWidget()
        content.setMinimumHeight(280)
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Texte du sous-titre…\nVous pouvez écrire sur plusieurs lignes."
        )
        self.text_edit.setMinimumHeight(80)
        self.text_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 6px; }}"
        )
        layout.addWidget(self.text_edit)

        duration_row = QVBoxLayout()
        duration_row.setSpacing(2)
        duration_label = QLabel("Durée (secondes)")
        duration_label.setStyleSheet(label_style(11, "muted", 600))
        duration_row.addWidget(duration_label)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 600.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setValue(3.0)
        self.duration_spin.setStyleSheet(
            f"QDoubleSpinBox {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 4px; }}"
        )
        duration_row.addWidget(self.duration_spin)
        layout.addLayout(duration_row)

        self.add_button = IconButton(
            icon=IconName.PLUS,
            tooltip="Ajouter un sous-titre",
            size=Sizes.icon_button,
            square=False,
        )
        self.add_button.setText("  Ajouter un sous-titre")
        self.add_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_button.setMinimumHeight(Sizes.button_md)
        self.add_button.clicked.connect(self._emit_add_requested)
        layout.addWidget(self.add_button)

        io_row = QHBoxLayout()
        io_row.setSpacing(Spacing.sm)
        self.import_button = IconButton(
            icon=IconName.IMPORT,
            tooltip="Importer un fichier SRT",
            size=Sizes.icon_button,
            square=True,
        )
        self.import_button.setMinimumHeight(Sizes.button_md)
        self.import_button.clicked.connect(self.import_requested)
        self.export_button = IconButton(
            icon=IconName.EXPORT,
            tooltip="Exporter les sous-titres en SRT",
            size=Sizes.icon_button,
            square=True,
        )
        self.export_button.setMinimumHeight(Sizes.button_md)
        self.export_button.clicked.connect(self.export_requested)
        io_row.addWidget(self.import_button)
        io_row.addWidget(self.export_button)
        layout.addLayout(io_row)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 6px; border-radius: 4px; "
            f"color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; "
            f"color: {COLORS['text']}; }}"
        )
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.list_widget.setMinimumHeight(72)
        layout.addWidget(self.list_widget, 1)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_clips(self, clips: list) -> None:
        self.list_widget.clear()
        for clip in clips:
            item = QListWidgetItem(self._format_label(clip))
            item.setData(Qt.UserRole, clip.id)
            self.list_widget.addItem(item)

    def count(self) -> int:
        return self.list_widget.count()

    def selected_clip_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def select_clip_id(self, clip_id: str) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.UserRole) == clip_id:
                self.list_widget.setCurrentRow(row)
                return

    @staticmethod
    def _format_label(clip) -> str:
        start = clip.timeline_start
        duration = getattr(clip, "duration", 0.0)
        text = (clip.text or "").replace("\n", " ").strip()
        if len(text) > 36:
            text = text[:35].rstrip() + "…"
        return f"{start:>6.2f}s  →  {text}"

    def _emit_add_requested(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            return
        duration = float(self.duration_spin.value())
        self.add_requested.emit(text, duration)
        self.text_edit.clear()

    def _on_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        clip_id = current.data(Qt.UserRole)
        if clip_id is not None:
            self.selected.emit(clip_id)


class TransitionLibraryView(QWidget):
    """Choix compact d'une transition à poser entre deux clips sélectionnés."""

    add_requested = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)
        hint = QLabel("Sélectionnez deux clips vidéo consécutifs dans la timeline.")
        hint.setWordWrap(True)
        hint.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(hint)
        self.type_combo = QComboBox()
        self.type_combo.addItem("Fondu enchaîné", "crossfade")
        self.type_combo.addItem("Fondu au noir", "fade_black")
        self.type_combo.addItem("Balayage gauche", "wipe_left")
        self.type_combo.addItem("Balayage droite", "wipe_right")
        layout.addWidget(self.type_combo)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 5.0)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setValue(0.5)
        self.duration_spin.setSuffix(" s")
        layout.addWidget(self.duration_spin)
        self.add_button = ProjectPanel._make_wide_button(
            IconName.TRANSITIONS, "Ajouter la transition", accent=True,
            tooltip="Ajouter la transition entre les deux clips sélectionnés",
        )
        self.add_button.clicked.connect(
            lambda: self.add_requested.emit(
                str(self.type_combo.currentData()), float(self.duration_spin.value())
            )
        )
        layout.addWidget(self.add_button)
        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Helpers privés : vignettes et libellés
# ---------------------------------------------------------------------------


def _format_duration(seconds: float | None) -> str:
    """Formate une durée en ``mm:ss`` (ou ``--`` si inconnue)."""
    if seconds is None or seconds <= 0:
        return "--:--"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_asset_caption(asset: MediaAsset) -> str:
    """Construit un libellé court (nom + durée) pour la grille."""
    duration = _format_duration(asset.duration)
    name = (asset.name or "Sans nom").strip()
    if len(name) > 24:
        name = name[:23] + "…"
    return f"{name}\n{duration}"


def _make_asset_thumbnail(asset: MediaAsset, size: int = 40) -> QIcon:
    """Génère une vignette carrée stylisée pour un asset.

    Pas d'extraction d'image vidéo : on dessine un poster sobre —
    bandeau de couleur typé + pictogramme — sur un fond vert-noir. La
    vignette reste donc honnête (aucune fausse preview) tout en gardant
    une identité visuelle constante et un repère de type lisible d'un
    coup d'œil.

    Le pixmap est produit à la taille exacte demandée : le délégué le
    redimensionne ensuite sans étirement.
    """
    is_audio = asset.media_type == "audio"
    icon_name = IconName.AUDIO if is_audio else IconName.FILM
    accent = COLORS["track_audio"] if is_audio else COLORS["track_video"]

    side = max(16, int(size))
    pixmap = QPixmap(side, side)
    pixmap.fill(QColor(COLORS["panel_alt"]))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Bandeau coloré à gauche, façon étui de pellicule.
    band = max(2, side // 8)
    painter.fillRect(0, 0, band, side, QColor(accent))
    # Pictogramme centré dans la zone restante.
    icon_side = max(8, int(side * 0.42))
    icon = make_icon(icon_name, size=icon_side)
    icon_rect = QRect(
        band + (side - band - icon_side) // 2,
        (side - icon_side) // 2,
        icon_side,
        icon_side,
    )
    painter.drawPixmap(icon_rect, icon.pixmap(icon_side, icon_side))
    painter.end()
    return QIcon(pixmap)
