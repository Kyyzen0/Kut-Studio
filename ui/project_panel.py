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
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.effects_library import (
    EffectCategory,
    EffectPreset,
    UserPresetStore,
    builtin_presets,
    filter_presets,
)
from core.project_model import MediaAsset
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
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
    # Bibliothèque d'effets (tâche 22)
    effect_apply_requested = Signal(str)  # preset_id
    effect_preset_save_requested = Signal()  # MainWindow ouvre le dialogue
    effect_preset_delete_requested = Signal(str)  # preset_id

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

        self.effects_view = EffectsLibraryView(self)
        self.content_stack.addWidget(self.effects_view)

        self.transition_view = TransitionLibraryView(self)
        self.content_stack.addWidget(self.transition_view)
        # Chaque page est faite pour défiler : on neutralise leur
        # ``minimumSizeHint`` (l'éditeur de sous-titres réclame 360 px),
        # sinon la pile réserve cette hauteur et la grille de vignettes
        # disparaît. On ne fige pas la hauteur : le layout accorde le
        # surplus quand la colonne est haute.
        for page in (
            self.bin_videos, self.bin_audios, self.subtitle_view,
            self.effects_view, self.transition_view,
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
        # Bibliothèque d'effets (tâche 22).
        self.effects_view.apply_requested.connect(self.effect_apply_requested)
        self.effects_view.save_requested.connect(self.effect_preset_save_requested)
        self.effects_view.delete_requested.connect(self.effect_preset_delete_requested)

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

    # ------------------------------------------------------------------
    # Bibliothèque d'effets (tâche 22)
    # ------------------------------------------------------------------

    def set_user_effect_presets(self, presets: list) -> None:
        """Met à jour la liste des presets utilisateur dans la bibliothèque."""
        self.effects_view.set_user_presets(list(presets or []))
        if self._active_page_index == 3:
            self._refresh_count()

    def update_effects_clip_context(
        self,
        *,
        has_video_clip: bool,
        clip_has_effects: bool,
    ) -> None:
        """Synchronise l'état « clip sélectionné » avec la bibliothèque.

        Args:
            has_video_clip: un clip vidéo est sélectionné dans la timeline.
            clip_has_effects: le clip sélectionné porte au moins un effet.
        """
        self.effects_view.set_clip_context(
            has_video_clip=has_video_clip,
            clip_has_effects=clip_has_effects,
        )

    def selected_effect_preset_id(self) -> str | None:
        """Identifiant du preset sélectionné dans la bibliothèque, ou ``None``."""
        return self.effects_view.selected_preset_id()

    def select_effect_preset(self, preset_id: str) -> bool:
        """Sélectionne un preset dans la bibliothèque ; ``True`` si trouvé."""
        return self.effects_view.select_preset(preset_id)

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
        elif self._active_page_index == 3:
            # Bibliothèque d'effets : on annonce le total (intégrés +
            # utilisateur). Le détail reste dans la bibliothèque.
            count = self.effects_view.preset_count()
            label_word = "preset" if count <= 1 else "presets"
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
# Bibliothèque d'effets (tâche 22)
# ---------------------------------------------------------------------------


_CATEGORY_ACCENTS: dict[EffectCategory, str] = {
    EffectCategory.COLOR: "#36E6C3",      # turquoise
    EffectCategory.CREATIVE: "#F7C948",   # ambre
    EffectCategory.STYLIZED: "#F27686",   # rose / corail
    EffectCategory.LOOK: "#8E7DFA",       # violet
}


class EffectPresetCard(QFrame):
    """Carte compacte représentant un preset d'effets dans la bibliothèque.

    Affiche nom, description courte, catégorie et nombre d'effets. Une
    croix « supprimer » est visible uniquement pour les presets
    utilisateur. Le clic sur la carte sélectionne le preset (utile
    pour l'action « Appliquer » de la barre d'actions).
    """

    clicked = Signal(str)
    delete_requested = Signal(str)

    CARD_HEIGHT = 64

    def __init__(self, preset: EffectPreset, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("effectPresetCard")
        self.preset_id = preset.id
        self._is_builtin = preset.builtin
        self._selected = False
        accent = _CATEGORY_ACCENTS.get(preset.category, COLORS["accent"])

        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.CARD_HEIGHT)
        accent_color = QColor(accent)
        self._accent_color = accent_color
        self._base_style = (
            f"QFrame#effectPresetCard {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._hover_style = (
            f"QFrame#effectPresetCard {{ background: {COLORS['surface_hover']};"
            f" border: 1px solid {COLORS['border_strong']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._selected_style = (
            f"QFrame#effectPresetCard {{ background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, 6, Spacing.sm, 6)
        layout.setSpacing(Spacing.sm)

        # Titre + description.
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(Spacing.xs)
        self._title = QLabel(preset.name)
        self._title.setStyleSheet(label_style(12, "text", 700))
        title_row.addWidget(self._title)
        # Badge catégorie : sobre, juste la couleur d'accent.
        self._badge = QLabel(translate(
            f"effects.category.{preset.category.value}"
        ).upper())
        self._badge.setStyleSheet(
            f"QLabel {{ color: {accent_color.name()};"
            f" background: transparent; font-size: 9px;"
            f" font-weight: 700; padding: 0; }}"
        )
        title_row.addWidget(self._badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)
        description = preset.description or ""
        if len(description) > 90:
            description = description[:89].rstrip() + "…"
        self._description = QLabel(description)
        self._description.setWordWrap(True)
        self._description.setStyleSheet(label_style(10, "muted", 500))
        text_col.addWidget(self._description)

        # Sous-titre (compte d'effets + état intégré/utilisateur).
        self._effects_label = QLabel(self._format_subtitle(preset))
        self._effects_label.setStyleSheet(label_style(10, "muted", 600))
        text_col.addWidget(self._effects_label)
        layout.addLayout(text_col, 1)

        # Bouton supprimer pour les presets utilisateur uniquement.
        self.delete_button = QPushButton()
        self.delete_button.setObjectName("presetDelete")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setFixedSize(22, 22)
        self.delete_button.setToolTip(translate("effects.library.delete"))
        delete_icon = make_icon(IconName.CLOSE, size=12)
        self.delete_button.setIcon(delete_icon)
        self.delete_button.setStyleSheet(
            f"QPushButton#presetDelete {{ background: transparent;"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 11px; }}"
            f"QPushButton#presetDelete:hover {{"
            f" background: {COLORS['danger_dark']};"
            f" border: 1px solid {COLORS['danger']}; }}"
        )
        self.delete_button.clicked.connect(
            lambda _checked=False, pid=preset.id: self.delete_requested.emit(pid)
        )
        self.delete_button.setVisible(not self._is_builtin)
        layout.addWidget(self.delete_button, 0, Qt.AlignVCenter)

    @staticmethod
    def _format_subtitle(preset: EffectPreset) -> str:
        count = len(preset.effects)
        suffix = "effet" if count <= 1 else "effets"
        return f"{count} {suffix}"

    # ----- Sélection visuelle --------------------------------------------

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(self._selected_style)
        elif self.underMouse():
            self.setStyleSheet(self._hover_style)
        else:
            self.setStyleSheet(self._base_style)

    # ----- Événements ---------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: D401 - Qt
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: D401 - Qt
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: D401 - Qt
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.preset_id)
        super().mousePressEvent(event)

    def update_texts(self) -> None:
        """Met à jour les libellés localisés."""
        preset = self._preset()
        if preset is None:
            return
        self._title.setText(preset.name)
        self._badge.setText(
            translate(f"effects.category.{preset.category.value}").upper()
        )


class EffectsLibraryView(QWidget):
    """Bibliothèque d'effets : recherche, catégories, presets, actions.

    Affiche en deux sections (« Préréglages » et « Mes presets ») des
    cartes cliquables. L'état de sélection suit la dernière carte
    cliquée ; un bouton « Appliquer au clip » publie un signal vers
    :class:`MainWindow` qui appelle le modèle d'effets.

    L'état « clip vidéo sélectionné » est fourni par l'extérieur via
    :meth:`set_clip_context` et pilote l'activation du bouton d'action
    ainsi que la visibilité d'un message d'aide.
    """

    apply_requested = Signal(str)        # preset_id (clip résolu par MainWindow)
    save_requested = Signal()            # MainWindow ouvre le dialogue
    delete_requested = Signal(str)       # preset_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._builtin_presets: list[EffectPreset] = list(builtin_presets())
        self._user_presets: list[EffectPreset] = []
        self._cards: dict[str, EffectPresetCard] = {}
        self._selected_preset_id: str | None = None
        self._has_video_clip = False
        self._clip_has_effects = False
        self._search_text = ""
        self._active_category: EffectCategory | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)

        # --- Titre + recherche ------------------------------------------
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, 0
        )
        header_layout.setSpacing(Spacing.xs)
        self.search_field = QLineEdit()
        self.search_field.setObjectName("effectsSearch")
        self.search_field.setPlaceholderText(
            translate("effects.library.search")
        )
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(28)
        self.search_field.textChanged.connect(self._on_search_changed)
        header_layout.addWidget(self.search_field)

        # --- Filtres catégorie ------------------------------------------
        self.category_row = QWidget()
        cat_layout = QHBoxLayout(self.category_row)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(Spacing.xs)
        self.category_buttons: list[QPushButton] = []
        self._category_buttons_data: list[EffectCategory | None] = [None]
        all_label = translate("effects.category.all").upper()
        cat_button = self._make_category_button(all_label, None)
        cat_layout.addWidget(cat_button)
        for category in EffectCategory:
            label = translate(f"effects.category.{category.value}").upper()
            button = self._make_category_button(label, category)
            cat_layout.addWidget(button)
            self._category_buttons_data.append(category)
        cat_layout.addStretch(1)
        header_layout.addWidget(self.category_row)
        layout.addWidget(header)

        # --- Astuce + état sélection clip ------------------------------
        self.clip_hint = QLabel(translate("effects.library.apply_hint"))
        self.clip_hint.setWordWrap(True)
        self.clip_hint.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; padding: 4px 6px;"
            f" background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
        )
        clip_hint_container = QWidget()
        clip_hint_layout = QHBoxLayout(clip_hint_container)
        clip_hint_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        clip_hint_layout.addWidget(self.clip_hint)
        layout.addWidget(clip_hint_container)

        # --- Zone défilante : cartes preset ----------------------------
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("effectsScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: transparent; }}"
        )
        self.cards_host = QWidget()
        self.cards_host.setObjectName("effectsCardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        self.cards_layout.setSpacing(Spacing.xs)
        self.cards_layout.addStretch(1)
        self.scroll_area.setWidget(self.cards_host)
        layout.addWidget(self.scroll_area, 1)

        # --- Actions principales ---------------------------------------
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        actions_layout.setSpacing(Spacing.xs)
        self.apply_button = self._make_wide_button(
            IconName.PLUS, "Appliquer au clip", accent=True,
            tooltip=translate("effects.library.apply"),
        )
        self.apply_button.clicked.connect(self._emit_apply_requested)
        actions_layout.addWidget(self.apply_button)

        self.save_button = self._make_wide_button(
            IconName.SAVE, "Enregistrer comme preset", accent=False,
            tooltip=translate("effects.library.save"),
        )
        self.save_button.clicked.connect(self._emit_save_requested)
        actions_layout.addWidget(self.save_button)
        layout.addWidget(actions)

        self._refresh_buttons()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_user_presets(self, presets: list) -> None:
        """Met à jour la liste des presets utilisateur."""
        self._user_presets = list(presets or [])
        self._rebuild_cards()

    def set_clip_context(
        self,
        *,
        has_video_clip: bool,
        clip_has_effects: bool,
    ) -> None:
        """Synchronise l'état du clip sélectionné avec l'interface."""
        self._has_video_clip = has_video_clip
        self._clip_has_effects = clip_has_effects
        self._refresh_buttons()

    def selected_preset_id(self) -> str | None:
        """Identifiant du preset sélectionné."""
        return self._selected_preset_id

    def select_preset(self, preset_id: str) -> bool:
        """Sélectionne un preset dans la bibliothèque."""
        if preset_id not in self._cards:
            return False
        self._select_preset(preset_id)
        return True

    def preset_count(self) -> int:
        """Nombre total de presets visibles (avant filtre)."""
        return len(self._builtin_presets) + len(self._user_presets)

    # ------------------------------------------------------------------
    # Helpers privés
    # ------------------------------------------------------------------

    def _make_wide_button(
        self,
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
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return button

    def _make_category_button(
        self,
        label: str,
        category: EffectCategory | None,
    ) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("effectsCategoryTab")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            f"QPushButton#effectsCategoryTab {{"
            f" background: transparent;"
            f" color: {COLORS['muted']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
            f" padding: 3px 8px;"
            f" font-weight: 600; font-size: 10px;"
            f" letter-spacing: 0.4px; }}"
            f"QPushButton#effectsCategoryTab:hover {{"
            f" color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
            f"QPushButton#effectsCategoryTab:checked {{"
            f" color: {COLORS['accent']};"
            f" background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']}; }}"
        )
        button.clicked.connect(
            lambda _checked=False, cat=category: self._select_category(cat)
        )
        self.category_buttons.append(button)
        return button

    # ----- Filtres -----------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip()
        self._rebuild_cards()

    def _select_category(self, category: EffectCategory | None) -> None:
        self._active_category = category
        for button in self.category_buttons:
            button.setChecked(False)
        index = self._category_buttons_data.index(category)
        if 0 <= index < len(self.category_buttons):
            self.category_buttons[index].setChecked(True)
        self._rebuild_cards()

    # ----- Reconstruction des cartes ----------------------------------

    def _rebuild_cards(self) -> None:
        # Nettoie les cartes existantes.
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        # Effacer le layout, sauf le stretch final.
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        library = self._builtin_presets + self._user_presets
        visible = filter_presets(
            library,
            search=self._search_text,
            category=self._active_category,
        )
        builtin_visible = [p for p in visible if p.builtin]
        user_visible = [p for p in visible if not p.builtin]

        if not visible:
            self._render_empty_message()
        else:
            if builtin_visible:
                self._render_section_title(
                    translate("effects.library.section.builtin")
                )
                for preset in builtin_visible:
                    self._render_card(preset)
            if user_visible:
                if builtin_visible:
                    # Séparateur visuel entre les deux sections.
                    self.cards_layout.addSpacing(Spacing.xs)
                self._render_section_title(
                    translate("effects.library.section.user")
                )
                for preset in user_visible:
                    self._render_card(preset)
            if not user_visible:
                self._render_user_empty_hint()

        # Restaurer la sélection si possible, sinon sélectionner la
        # première carte visible.
        ids = list(self._cards)
        if self._selected_preset_id in ids:
            self._apply_selection(self._selected_preset_id)
        elif ids:
            self._select_preset(ids[0])
        else:
            self._selected_preset_id = None
            self._refresh_buttons()
        self._refresh_buttons()

    def _render_empty_message(self) -> None:
        empty = QLabel(translate("effects.library.no_results"))
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; padding: 16px 8px;"
        )
        self.cards_layout.addWidget(empty)

    def _render_user_empty_hint(self) -> None:
        if not self._user_presets:
            hint = QLabel(translate("effects.library.user_empty"))
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 10px; padding: 6px 8px;"
                f" background: transparent;"
            )
            self.cards_layout.addWidget(hint)

    def _render_section_title(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setStyleSheet(
            f"color: {COLORS['muted_strong']}; font-size: 10px;"
            f" font-weight: 800; letter-spacing: 1px;"
            f" padding: 6px 2px 2px 2px;"
        )
        self.cards_layout.addWidget(label)

    def _render_card(self, preset: EffectPreset) -> None:
        card = EffectPresetCard(preset, parent=self.cards_host)
        card.clicked.connect(self._select_preset)
        card.delete_requested.connect(self._emit_delete_requested)
        self._cards[preset.id] = card
        self.cards_layout.addWidget(card)

    # ----- Sélection ---------------------------------------------------

    def _select_preset(self, preset_id: str) -> None:
        self._selected_preset_id = preset_id
        self._apply_selection(preset_id)
        self._refresh_buttons()

    def _apply_selection(self, preset_id: str) -> None:
        for pid, card in self._cards.items():
            card.set_selected(pid == preset_id)

    # ----- Boutons d'action -------------------------------------------

    def _refresh_buttons(self) -> None:
        can_apply = self._has_video_clip and self._selected_preset_id is not None
        self.apply_button.setEnabled(can_apply)
        self.save_button.setEnabled(
            self._has_video_clip and self._clip_has_effects
        )

    def _emit_apply_requested(self) -> None:
        if not self._has_video_clip or not self._selected_preset_id:
            return
        # ``clip_id`` est résolu côté ``MainWindow`` (qui connaît la
        # sélection de la timeline) ; la bibliothèque ne porte que
        # l'identifiant du preset.
        self.apply_requested.emit(self._selected_preset_id)

    def _emit_save_requested(self) -> None:
        # La bibliothèque demande à ``MainWindow`` d'ouvrir le dialogue
        # de capture : c'est lui qui dispose du clip sélectionné et
        # donc de la chaîne d'effets à enregistrer.
        if not self._has_video_clip or not self._clip_has_effects:
            return
        self.save_requested.emit()

    def _emit_delete_requested(self, preset_id: str) -> None:
        self.delete_requested.emit(preset_id)


class SavePresetDialog(QDialog):
    """Boîte de dialogue « Enregistrer un preset » (tâche 22).

    Demande un nom (obligatoire), une description (optionnelle) et une
    catégorie. Le résultat est lu via :meth:`result_data` après un
    :meth:`exec` réussi.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        default_name: str = "",
        default_category: EffectCategory = EffectCategory.LOOK,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("savePresetDialog")
        self.setWindowTitle(translate("effects.library.dialog.title"))
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.lg, Spacing.lg, Spacing.lg, Spacing.md)
        layout.setSpacing(Spacing.sm)

        intro = QLabel(translate("effects.library.dialog.title"))
        intro.setStyleSheet(label_style(13, "text", 700))
        layout.addWidget(intro)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.sm)
        form.setLabelAlignment(Qt.AlignLeft)

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("presetNameEdit")
        self.name_edit.setPlaceholderText(
            translate("effects.library.dialog.name")
        )
        self.name_edit.setText(default_name)
        self.name_edit.setMaxLength(60)
        form.addRow(
            self._make_form_label(translate("effects.library.dialog.name")),
            self.name_edit,
        )

        self.description_edit = QTextEdit()
        self.description_edit.setObjectName("presetDescriptionEdit")
        self.description_edit.setPlaceholderText(
            translate("effects.library.dialog.description")
        )
        self.description_edit.setFixedHeight(72)
        self.description_edit.setAcceptRichText(False)
        form.addRow(
            self._make_form_label(
                translate("effects.library.dialog.description")
            ),
            self.description_edit,
        )

        self.category_combo = QComboBox()
        self.category_combo.setObjectName("presetCategoryCombo")
        for category in EffectCategory:
            label = translate(f"effects.category.{category.value}")
            self.category_combo.addItem(label, category.value)
        # Catégorie par défaut.
        index = self.category_combo.findData(default_category.value)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        form.addRow(
            self._make_form_label(translate("effects.library.dialog.category")),
            self.category_combo,
        )
        layout.addLayout(form)

        # Boutons OK / Annuler.
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText(
            translate("effects.library.dialog.save")
        )
        buttons.button(QDialogButtonBox.Cancel).setText(
            translate("effects.library.dialog.cancel")
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(label_style(11, "muted", 600))
        return label

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            # L'IHM ne laisse pas fermer la dialogue tant que le nom
            # est vide : on remet le focus sur le champ.
            self.name_edit.setFocus()
            return
        self.accept()

    def result_data(self) -> tuple[str, str, EffectCategory]:
        """Retourne ``(name, description, category)`` du preset à créer."""
        name = self.name_edit.text().strip()
        description = self.description_edit.toPlainText().strip()
        raw_category = self.category_combo.currentData()
        try:
            category = EffectCategory(raw_category)
        except (TypeError, ValueError):
            category = EffectCategory.LOOK
        return name, description, category


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
