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
from core.library_organization import (
    AssetUsage,
    LibraryOrganization,
    collect_missing_assets,
    is_asset_missing,
    usage_map,
)
from core.project_model import MediaAsset
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconLabel, IconName, make_icon
from ui.library_organization_widgets import (
    FILTER_ALL,
    FILTER_AUDIO,
    FILTER_IMAGE,
    FILTER_MISSING,
    FILTER_UNUSED,
    FILTER_USED,
    FILTER_VIDEO,
    AssetContextMenuBuilder,
    AssetUsageBadge,
    FilterChipBar,
    FolderTreeWidget,
    TagManagerDialog,
    compute_badges,
    prompt_for_folder_name,
)
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
    # Bibliothèque de transitions (tâche 23)
    transition_apply_requested = Signal(str, float)  # preset_id, duration
    transition_preset_save_requested = Signal()  # MainWindow ouvre le dialogue
    transition_preset_delete_requested = Signal(str)  # preset_id
    transition_favorite_toggled = Signal(str)  # preset_id
    # Bibliothèque de texte (tâche 24) — délégués vers SubtitleLibraryView
    preset_apply_requested = Signal(str)  # preset_id
    preset_new_clip_requested = Signal(str)  # preset_id
    preset_save_requested = Signal(str, object, object, str)
    preset_delete_requested = Signal(str)  # preset_id
    # Bibliothèque de calques graphiques (tâche 32)
    graphic_create_requested = Signal(str)  # text / rectangle / solid
    graphic_import_requested = Signal()  # image overlay
    # --- Organisation avancée de la bibliothèque (tâche 25) ---
    # Dossiers
    folder_create_requested = Signal(str, object, str)
    # (name, parent_id_or_none, color)
    folder_rename_requested = Signal(str, str)
    # (folder_id, new_name)
    folder_recolor_requested = Signal(str, str)
    # (folder_id, color)
    folder_delete_requested = Signal(str)
    # (folder_id,)
    # Tags
    tag_manager_requested = Signal()
    # Affectations
    asset_move_to_folder_requested = Signal(str, object)
    # (asset_id, folder_id_or_none)
    asset_tag_toggled = Signal(str, str, bool)
    # (asset_id, tag_id, assign)
    asset_relink_requested = Signal(str)
    # (asset_id,)
    asset_rename_requested = Signal(str, str)
    # (asset_id, new_name)
    asset_remove_requested = Signal(str)
    # (asset_id,)
    asset_occurrences_requested = Signal(str)
    # (asset_id,) — MainWindow sélectionne le premier clip dans la timeline
    library_changed = Signal()
    # émis après chaque mutation pour permettre au MainWindow de
    # rafraîchir l'état global (titre sale, autosave, ...).

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
        # --- Organisation de la bibliothèque (tâche 25) ---
        # Le panneau consomme une référence à ``LibraryOrganization``
        # injectée par le MainWindow via :meth:`set_library`. La
        # valeur est ``None`` tant que le MainWindow n'a pas eu
        # l'occasion d'attacher le projet courant.
        self._organization: LibraryOrganization | None = None
        # Filtres rapides : un seul actif à la fois.
        self._active_filter: str = FILTER_ALL
        # Dossier / portée de navigation courante (sélection dans
        # l'arborescence). ``None`` signifie « Tous » ou équivalent
        # synthétique (Racine / Manquants).
        self._selected_folder_id: str | None = None
        self._selected_kind: str = FILTER_ALL
        # Cache local des badges (usage + missing + tags) pour ne pas
        # recalculer à chaque mutation mineure de la vue.
        self._badges: dict[str, AssetUsageBadge] = {}
        # Compteurs par dossier pour l'arborescence.
        self._folder_counts: dict[str, int] = {}

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
        self.library_search_row = search_row
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
        self.library_browse_content = browse_content
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

        # -- Arborescence de dossiers (tâche 25) --
        folders_sep = QWidget()
        folders_sep.setFixedHeight(1)
        folders_sep.setStyleSheet(f"background: {COLORS['border']};")
        browse_layout.addSpacing(Spacing.xs)
        browse_layout.addWidget(folders_sep)
        browse_layout.addSpacing(Spacing.xs)

        # L'arborescence remplace l'ancien QListWidget à 3 entrées.
        # Elle supporte les dossiers personnalisés (création,
        # renommage, suppression via menu contextuel) et expose
        # trois racines synthétiques : Tous, Racine, Manquants.
        self.folder_tree = FolderTreeWidget()
        self.folder_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.folder_tree.setMinimumHeight(110)
        self.folder_tree.folder_selected.connect(self._on_folder_selected)
        self.folder_tree.folder_create_requested.connect(
            self._on_folder_create_requested
        )
        self.folder_tree.folder_rename_requested.connect(
            self.folder_rename_requested.emit
        )
        self.folder_tree.folder_delete_requested.connect(
            self.folder_delete_requested.emit
        )
        browse_layout.addWidget(self.folder_tree)

        # --- Filtres rapides (chips) ---
        self.filter_chips = FilterChipBar()
        self.filter_chips.filter_changed.connect(self._on_filter_changed)
        browse_layout.addWidget(self.filter_chips)

        # --- Bouton "Gérer les tags" ---
        tags_row = QWidget()
        tags_layout = QHBoxLayout(tags_row)
        tags_layout.setContentsMargins(0, 0, 0, 0)
        tags_layout.setSpacing(Spacing.xs)
        tags_layout.addStretch(1)
        self.manage_tags_button = QPushButton("Gérer les tags…")
        self.manage_tags_button.setObjectName("manageTagsButton")
        self.manage_tags_button.setCursor(Qt.PointingHandCursor)
        self.manage_tags_button.setFocusPolicy(Qt.NoFocus)
        self.manage_tags_button.setStyleSheet(
            f"QPushButton#manageTagsButton {{ background: transparent;"
            f" color: {COLORS['accent']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QPushButton#manageTagsButton:hover {{"
            f" background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']}; }}"
        )
        self.manage_tags_button.clicked.connect(self.tag_manager_requested.emit)
        tags_layout.addWidget(self.manage_tags_button)
        browse_layout.addWidget(tags_row)

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

        self.graphics_view = GraphicsLibraryView(self)
        self.content_stack.addWidget(self.graphics_view)
        # Chaque page est faite pour défiler : on neutralise leur
        # ``minimumSizeHint`` (l'éditeur de sous-titres réclame 360 px),
        # sinon la pile réserve cette hauteur et la grille de vignettes
        # disparaît. On ne fige pas la hauteur : le layout accorde le
        # surplus quand la colonne est haute.
        for page in (
            self.bin_videos, self.bin_audios, self.subtitle_view,
            self.effects_view, self.transition_view,
            self.graphics_view,
        ):
            page.setMinimumHeight(0)
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(self.content_stack, 1)

        # ----- Boutons d'action principaux -----------------------------
        # Une seule rangée : empilés, ils consommaient ~90 px de hauteur
        # qui revient à la grille de vignettes. L'action primaire garde
        # l'accent turquoise, l'import reste secondaire.
        actions = QWidget()
        self.library_actions = actions
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
        # Bibliothèque de texte (tâche 24) : on délègue les signaux.
        self.subtitle_view.preset_apply_requested.connect(
            self.preset_apply_requested
        )
        self.subtitle_view.preset_new_clip_requested.connect(
            self.preset_new_clip_requested
        )
        self.subtitle_view.preset_save_requested.connect(
            self.preset_save_requested
        )
        self.subtitle_view.preset_delete_requested.connect(
            self.preset_delete_requested
        )
        # Bibliothèque de transitions (tâche 23).
        self.transition_view.add_requested.connect(self.transition_apply_requested)
        self.transition_view.save_requested.connect(self.transition_preset_save_requested)
        self.transition_view.delete_requested.connect(self.transition_preset_delete_requested)
        self.transition_view.favorite_toggled.connect(self.transition_favorite_toggled)
        # Bibliothèque d'effets (tâche 22).
        self.effects_view.apply_requested.connect(self.effect_apply_requested)
        self.effects_view.save_requested.connect(self.effect_preset_save_requested)
        self.effects_view.delete_requested.connect(self.effect_preset_delete_requested)
        self.graphics_view.create_requested.connect(
            self.graphic_create_requested.emit
        )
        self.graphics_view.import_requested.connect(
            self.graphic_import_requested.emit
        )

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
        """Compatibilité ascendante : redirige vers les onglets historiques."""
        folder_sections = {
            "videos": "media",
            "audios": "audio",
            "subtitles": "text",
        }
        self.select_section(folder_sections.get(item.data(Qt.UserRole), "media"))

    # ------------------------------------------------------------------
    # Organisation de la bibliothèque (tâche 25)
    # ------------------------------------------------------------------

    def set_library(self, organization: LibraryOrganization | None) -> None:
        """Attache l'organisation de la bibliothèque au panneau.

        Le MainWindow appelle cette méthode après chaque mutation
        affectant ``project.library_folders`` / ``library_tags`` /
        ``library_assignments``. Le panneau reconstruit l'arborescence
        des dossiers et rafraîchit les badges sans toucher aux
        grilles de médias.
        """
        self._organization = organization
        has_missing = bool(organization) and bool(
            collect_missing_assets(organization.project)
        )
        self.folder_tree.set_organization(organization, has_missing=has_missing)
        self._refresh_folder_counts()
        self._refresh_badges()
        self._refresh_grids()

    def set_usage_for_assets(self) -> None:
        """Recalcule les compteurs d'occurrences et les badges.

        À appeler après toute opération modifiant les clips (ajout,
        suppression, déplacement) : les compteurs ``×3`` sur les
        cartes et le filtre « Utilisés » en dépendent.
        """
        self._refresh_badges()
        self._refresh_grids()

    def _refresh_badges(self) -> None:
        if self._organization is None:
            self._badges = {}
            return
        self._badges = compute_badges(
            self._organization.project, self._organization
        )
        # Met à jour les cartes déjà affichées.
        for bin_widget in (self.bin_videos, self.bin_audios):
            bin_widget.apply_badges(self._badges)

    def _refresh_folder_counts(self) -> None:
        if self._organization is None:
            self._folder_counts = {}
            self.folder_tree.set_folder_counts({})
            return
        project = self._organization.project
        all_count = len(project.media_assets)
        root_count = 0
        missing_count = 0
        per_folder: dict[str, int] = {}
        for asset in project.media_assets:
            assignment = self._organization.get_assignment(asset.id)
            if assignment.folder_id is None:
                root_count += 1
            else:
                per_folder[assignment.folder_id] = (
                    per_folder.get(assignment.folder_id, 0) + 1
                )
            if is_asset_missing(asset):
                missing_count += 1
        counts = {
            "__all__": all_count,
            "__root__": root_count,
            "__missing__": missing_count,
        }
        counts.update(per_folder)
        self._folder_counts = counts
        self.folder_tree.set_folder_counts(counts)

    def _on_folder_selected(self, folder_id_or_none: object) -> None:
        """Réagit au changement de sélection dans l'arborescence."""
        self._selected_folder_id = (
            str(folder_id_or_none) if folder_id_or_none is not None else None
        )
        self._selected_kind = self.folder_tree.selected_kind()
        self._refresh_grids()

    def _on_filter_changed(self, filter_id: str) -> None:
        """Réagit au changement de chip de filtre rapide."""
        self._active_filter = filter_id
        self._refresh_grids()

    def _on_folder_create_requested(self, parent_id: object) -> None:
        """Ouvre un dialogue pour créer un dossier."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
        )

        result = prompt_for_folder_name(
            title="Nouveau dossier",
            label="Nom du dossier :",
            parent=self,
        )
        if result is None:
            return
        name, color = result
        parent_id_str = str(parent_id) if parent_id is not None else None
        try:
            self.folder_create_requested.emit(name, parent_id_str, color)
        except (LibraryError, LibraryNameError) as exc:
            self._show_warning(str(exc))

    def show_event_for_asset(self, asset_id: str) -> None:
        """Slot public : demande à la timeline de sélectionner l'asset."""
        self.asset_occurrences_requested.emit(asset_id)

    def _show_warning(self, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Bibliothèque", message)

    def selected_asset_usage(self) -> AssetUsageBadge | None:
        """Retourne le badge du média sélectionné (``None`` si rien)."""
        asset_id = self.selected_asset_id
        if asset_id is None:
            return None
        return self._badges.get(asset_id)

    def request_asset_context_menu(
        self,
        asset_id: str,
        global_pos,
    ) -> None:
        """Construit et affiche le menu contextuel d'un asset."""
        if self._organization is None:
            return
        # Trouve le média et son badge.
        project = self._organization.project
        asset = next((a for a in project.media_assets if a.id == asset_id), None)
        if asset is None:
            return
        badge = self._badges.get(asset_id) or AssetUsageBadge(asset_id=asset_id)
        assignment = self._organization.get_assignment(asset_id)
        builder = AssetContextMenuBuilder(
            asset_id=asset_id,
            asset_name=asset.name,
            is_missing=badge.is_missing,
            usage_count=badge.usage_count,
            folders=self._organization.all_folders(),
            tags=self._organization.list_tags(),
            assigned_folder_id=assignment.folder_id,
            assigned_tag_ids=set(assignment.tag_ids),
            parent=self,
        )
        menu = builder.build(self)
        # Connecte les signaux du builder à des re-émissions vers le
        # MainWindow. Les callbacks ne sont pas appelés si l'utilisateur
        # ferme le menu sans choisir.
        builder.rename_requested.connect(self.asset_rename_requested.emit)
        builder.remove_requested.connect(self.asset_remove_requested.emit)
        builder.relink_requested.connect(self.asset_relink_requested.emit)
        builder.show_in_timeline_requested.connect(
            self.asset_occurrences_requested.emit
        )
        builder.move_to_folder_requested.connect(
            self.asset_move_to_folder_requested.emit
        )
        builder.tag_toggled.connect(self.asset_tag_toggled.emit)
        builder.manage_tags_requested.connect(self.tag_manager_requested.emit)
        menu.exec(global_pos)

    def select_section(self, section_id: str) -> None:
        """Affiche la bibliothèque demandée par la navigation globale."""
        page_index = {
            "media": 0,
            "audio": 1,
            "text": 2,
            "effects": 3,
            "transitions": 4,
            "graphics": 5,
        }.get(section_id)
        if page_index is None:
            return
        self._active_page_index = page_index
        self.content_stack.setCurrentIndex(page_index)
        show_asset_chrome = page_index in {0, 1}
        self.library_search_row.setVisible(show_asset_chrome)
        self.library_browse_content.setVisible(show_asset_chrome)
        self.library_actions.setVisible(show_asset_chrome)
        self._refresh_count()
        self._sync_add_button_for_active_tab()

    def _filter_assets(self, assets: list[MediaAsset]) -> list[MediaAsset]:
        """Applique les filtres actifs (recherche, dossier, type, statut).

        L'ordre d'application est : recherche textuelle → restriction
        par dossier / portée → filtres rapides (Vidéo, Audio,
        Utilisés, Manquants). Les filtres sont additifs : un média
        qui passe le filtre Vidéo ET Utilisés reste visible. Une
        sélection sur « Manquants » (kind) force la conservation des
        seuls médias sans fichier source.
        """
        filtered = list(assets)
        if self._search_text:
            needle = self._search_text
            filtered = [a for a in filtered if needle in a.name.lower()]
        # --- Filtre de portée (dossier / Tous / Racine / Manquants) ---
        filtered = self._apply_scope(filtered)
        # --- Filtre rapide par type ou statut ---
        filtered = self._apply_quick_filter(filtered)
        return filtered

    def _apply_scope(self, assets: list[MediaAsset]) -> list[MediaAsset]:
        """Filtre ``assets`` selon la sélection dans l'arborescence."""
        if self._organization is None:
            return assets
        kind = self._selected_kind
        if kind == "all" or self._selected_folder_id is not None and kind == "folder":
            if kind == "all":
                return assets
            # ``kind == "folder"`` : on garde les médias rangés dans
            # ce dossier (la racine « Racine » est gérée séparément
            # car ``folder_id is None``).
            folder_id = self._selected_folder_id
            return [
                a for a in assets
                if self._organization.get_assignment(a.id).folder_id == folder_id
            ]
        if kind == "root":
            return [
                a for a in assets
                if self._organization.get_assignment(a.id).folder_id is None
            ]
        if kind == "missing":
            return [a for a in assets if is_asset_missing(a)]
        return assets

    def _apply_quick_filter(self, assets: list[MediaAsset]) -> list[MediaAsset]:
        """Applique le chip de filtre rapide sélectionné."""
        from core.library_organization import (
            filter_assets_by_type,
            filter_assets_missing,
            filter_assets_unused,
            filter_assets_used,
        )

        f = self._active_filter
        if f == FILTER_VIDEO:
            return filter_assets_by_type(assets, "video")
        if f == FILTER_AUDIO:
            return filter_assets_by_type(assets, "audio")
        if f == FILTER_IMAGE:
            return filter_assets_by_type(assets, "image")
        if f == FILTER_USED:
            return filter_assets_used(assets, self._badges_to_usage())
        if f == FILTER_UNUSED:
            return filter_assets_unused(assets, self._badges_to_usage())
        if f == FILTER_MISSING:
            return filter_assets_missing(assets)
        return assets

    def _badges_to_usage(self) -> dict[str, AssetUsage]:
        """Convertit le mapping de badges en mapping d'usage."""
        result: dict[str, AssetUsage] = {}
        for asset_id, badge in self._badges.items():
            usage = AssetUsage(asset_id=asset_id)
            usage.clip_count = badge.usage_count
            result[asset_id] = usage
        return result

    def _refresh_grids(self) -> None:
        """Réaffiche les assets visibles selon le scope et la recherche.

        Le filtrage s'applique sur la liste de référence du projet
        (et non sur le contenu actuel des bins) : cela évite qu'un
        filtre actif efface définitivement un média des bins après
        un changement de portée.
        """
        selected = self.selected_asset_id
        if self._organization is None:
            videos = self.bin_videos.all_assets()
            audios = self.bin_audios.all_assets()
        else:
            all_assets = list(self._organization.project.media_assets)
            videos = [a for a in all_assets if a.media_type == "video"]
            audios = [a for a in all_assets if a.media_type == "audio"]
        if self._active_scope == "favorites":
            self.bin_videos.set_assets([])
            self.bin_audios.set_assets([])
            self.media_count.setText("Aucun favori")
            return
        self.bin_videos.set_assets(self._filter_assets(videos))
        self.bin_videos.apply_badges(self._badges)
        self.bin_audios.set_assets(self._filter_assets(audios))
        self.bin_audios.apply_badges(self._badges)
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
        # On remplit d'abord les bins avec la liste *brute* (tous
        # les assets), puis on ré-applique les filtres via
        # :meth:`_refresh_grids`. Cette séparation évite qu'un filtre
        # actif écrase la liste de référence pendant la mise à jour.
        self._populate_bin(self.bin_videos, videos)
        self._populate_bin(self.bin_audios, audios)
        self._refresh_count()
        self._sync_add_button_for_active_tab()
        if self._organization is not None:
            self._refresh_grids()

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

    # ------------------------------------------------------------------
    # Bibliothèque de transitions (tâche 23)
    # ------------------------------------------------------------------

    def set_transition_presets(
        self,
        presets: list,
        *,
        favorites: list[str] | None = None,
    ) -> None:
        """Met à jour la bibliothèque de transitions."""
        self.transition_view.set_presets(list(presets or []), favorites=favorites)
        if self._active_page_index == 4:
            self._refresh_count()

    def set_transition_favorites(self, favorites: list[str]) -> None:
        """Met à jour uniquement les favoris (sans toucher aux presets)."""
        self.transition_view.set_favorites(list(favorites or []))

    def update_transitions_clip_context(
        self, *, has_two_video_clips: bool
    ) -> None:
        """Synchronise l'état « deux clips vidéo sélectionnés » avec la bibliothèque."""
        self.transition_view.set_selection_context(
            has_two_video_clips=has_two_video_clips,
        )

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
        bin_widget = AssetBin(
            on_item_clicked=lambda asset_id: self.asset_selected.emit(asset_id),
            on_selection_changed=lambda asset_id: self._on_bin_selection_changed(asset_id),
        )
        bin_widget.asset_context_menu_requested.connect(
            self._on_asset_context_menu_requested
        )
        return bin_widget

    def _on_asset_context_menu_requested(
        self,
        asset_id: str,
        global_pos,
    ) -> None:
        """Délègue au panneau principal qui connaît ``LibraryOrganization``."""
        self.request_asset_context_menu(asset_id, global_pos)

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
        elif self._active_page_index == 4:
            # Bibliothèque de transitions : on annonce le total.
            count = self.transition_view.preset_count()
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
    - la durée formatée ;
    - un badge « ×N » (occurrences sur la timeline) et un point rouge
      pour les fichiers manquants (tâche 25) ;
    - un clic droit ouvre un menu contextuel (renommer, déplacer,
      taguer, relier, supprimer).

    La sélection est marquée par un filet turquoise fin et un fond
    vert foncé subtil — fidèle à la direction artistique premium.

    L'implémentation repose sur un ``QListWidget`` en mode ``ListMode``
    avec un délégué custom qui peint chaque ligne comme une carte
    horizontale (poster + nom + durée + badges). C'est plus simple et
    plus prévisible que ``IconMode``.
    """

    # Signal émis quand l'utilisateur fait un clic droit sur une carte.
    asset_context_menu_requested = Signal(str, object)
    # (asset_id, global_pos)

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

            # --- Badges (tâche 25) ------------------------------------
            # Lecture des données portées par l'item : badge usage +
            # drapeau manquant + couleurs de tags.
            badge = index.data(Qt.UserRole + 3)
            if badge is not None:
                self._paint_badges(painter, rect, badge)

            painter.restore()

        def _paint_badges(self, painter, rect, badge) -> None:
            """Dessine les badges (compteur d'usage, manquant, tags)."""
            # Compteur d'occurrences (« ×N ») à droite de la durée.
            usage_count = getattr(badge, "usage_count", 0)
            is_missing = getattr(badge, "is_missing", False)
            tag_colors = list(getattr(badge, "tag_colors", []) or [])
            right_edge = rect.right() - self.PADDING
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            # Badge « ×N » : collé à droite. On n'affiche rien si 0
            # pour ne pas surcharger la carte.
            if usage_count > 0:
                label = f"×{usage_count}"
                fm = painter.fontMetrics()
                width = fm.horizontalAdvance(label) + 10
                height = 16
                badge_rect = QRect(
                    right_edge - width,
                    rect.top() + (rect.height() - height) // 2,
                    width,
                    height,
                )
                right_edge = badge_rect.left() - 4
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(COLORS["accent"]))
                painter.drawRoundedRect(badge_rect, 8, 8)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(badge_rect, Qt.AlignCenter, label)
            # Pastilles de tags : 6 px de diamètre, à droite du badge
            # d'usage. On n'en affiche que 3 maximum.
            if tag_colors:
                chip_size = 8
                spacing = 4
                chips_total = min(3, len(tag_colors))
                chip_row = QRect(
                    0,
                    rect.top() + (rect.height() - chip_size) // 2,
                    chips_total * (chip_size + spacing) - spacing,
                    chip_size,
                )
                chip_row.moveRight(right_edge)
                painter.setPen(Qt.NoPen)
                x = chip_row.left()
                for color in tag_colors[:chips_total]:
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(x, chip_row.top(), chip_size, chip_size)
                    x += chip_size + spacing
            # Point d'avertissement « manquant » : à droite de la
            # ligne, plus visible que les pastilles.
            if is_missing:
                warn_size = 10
                warn_rect = QRect(
                    0,
                    rect.top() + (rect.height() - warn_size) // 2,
                    warn_size,
                    warn_size,
                )
                warn_rect.moveRight(rect.right() - self.PADDING)
                painter.setBrush(QColor(COLORS["danger"]))
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawEllipse(warn_rect)

    def __init__(self, on_item_clicked, on_selection_changed, parent=None) -> None:
        super().__init__(parent)
        self._assets: dict[str, MediaAsset] = {}
        # Mapping ``asset_id -> AssetUsageBadge`` consommé par le
        # délégué pour peindre les badges (compteur d'usage, tags,
        # fichier manquant). Réinjecté via ``apply_badges``.
        self._badges: dict[str, AssetUsageBadge] = {}
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
        # Clic droit : on l'attrape pour ouvrir le menu contextuel.
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
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
        item = self._make_item(asset)
        self._list.addItem(item)

    def set_assets(self, assets: list[MediaAsset]) -> None:
        """Remplace le contenu de la grille."""
        self._list.clear()
        self._assets = {a.id: a for a in assets}
        for asset in assets:
            self._list.addItem(self._make_item(asset))

    def _make_item(self, asset: MediaAsset) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, asset.id)
        item.setData(Qt.UserRole + 1, asset)
        item.setData(Qt.UserRole + 2, _format_duration(asset.duration))
        item.setData(Qt.UserRole + 3, self._badges.get(asset.id))
        item.setData(Qt.DisplayRole, asset.name)
        item.setToolTip(self._make_tooltip(asset))
        return item

    def _make_tooltip(self, asset: MediaAsset) -> str:
        """Tooltip enrichi : nom + durée + statut manquant."""
        base = f"{asset.name}\n{_format_duration(asset.duration)}"
        badge = self._badges.get(asset.id)
        if badge is None:
            return base
        extras: list[str] = []
        if badge.usage_count > 0:
            extras.append(f"Utilisé {badge.usage_count}× sur la timeline")
        if badge.is_missing:
            extras.append("⚠ Fichier source introuvable — utilisez Relier")
        if extras:
            base += "\n" + "\n".join(extras)
        return base

    def apply_badges(self, badges: dict[str, AssetUsageBadge]) -> None:
        """Réinjecte les badges et repeint les cartes (tâche 25)."""
        self._badges = dict(badges)
        for row in range(self._list.count()):
            item = self._list.item(row)
            asset_id = item.data(Qt.UserRole)
            item.setData(Qt.UserRole + 3, self._badges.get(asset_id))
            asset = item.data(Qt.UserRole + 1)
            if asset is not None:
                item.setToolTip(self._make_tooltip(asset))
        # ``viewport().update()`` force le délégué à repeindre même
        # sans changement de géométrie.
        self._list.viewport().update()

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
    # Menu contextuel
    # ------------------------------------------------------------------

    def _on_context_menu(self, position) -> None:
        item = self._list.itemAt(position)
        if item is None:
            return
        asset_id = item.data(Qt.UserRole)
        if asset_id is None:
            return
        global_pos = self._list.viewport().mapToGlobal(position)
        self.asset_context_menu_requested.emit(asset_id, global_pos)

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
    """Sous-panneau « Texte » (tâche 24).

    Rôle :

    - héberge la *bibliothèque* de modèles de sous-titres / titres
      (préréglages intégrés + modèles utilisateur) ;
    - héberge l'*éditeur courant* (contenu + durée) pour créer un
      nouveau clip au playhead ;
    - garde l'import / export SRT historique et la liste existante.
    """

    add_requested = Signal(str, float)
    preset_apply_requested = Signal(str)
    preset_new_clip_requested = Signal(str)
    preset_save_requested = Signal(str, object, object, str)
    preset_delete_requested = Signal(str)
    import_requested = Signal()
    export_requested = Signal()
    selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Toute la page (bibliothèque + éditeur) défile ensemble : une
        # colonne basse doit faire apparaître une barre de défilement
        # plutôt que tronquer les cartes.
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background: {COLORS['panel']}; border: none; }}"
        )
        outer_layout.addWidget(self.scroll_area, 1)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.scroll_area.setWidget(page)

        # --- Bibliothèque de modèles --------------------------------
        self.preset_view = TextPresetLibraryView(page)
        self.preset_view.preset_apply_requested.connect(
            self.preset_apply_requested
        )
        self.preset_view.preset_new_clip_requested.connect(
            self.preset_new_clip_requested
        )
        self.preset_view.preset_save_requested.connect(
            self.preset_save_requested
        )
        self.preset_view.preset_delete_requested.connect(
            self.preset_delete_requested
        )
        page_layout.addWidget(self.preset_view, 1)

        # --- Éditeur rapide (nouveau clip au playhead) ---------------
        editor_box = QFrame()
        editor_box.setFrameShape(QFrame.NoFrame)
        editor_box.setStyleSheet(
            f"QFrame {{ background: {COLORS['panel']};"
            f" border-top: 1px solid {COLORS['border']}; }}"
        )
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        editor_layout.setSpacing(Spacing.sm)

        title = QLabel(translate("text.library.new_subtitle"))
        title.setStyleSheet(label_style(12, "text", 700))
        editor_layout.addWidget(title)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Texte du sous-titre…\nVous pouvez écrire sur plusieurs lignes."
        )
        self.text_edit.setMinimumHeight(72)
        self.text_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 6px; }}"
        )
        editor_layout.addWidget(self.text_edit)

        duration_label = QLabel("Durée (secondes)")
        duration_label.setStyleSheet(label_style(11, "muted", 600))
        editor_layout.addWidget(duration_label)
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
        editor_layout.addWidget(self.duration_spin)

        self.add_button = IconButton(
            icon=IconName.PLUS,
            tooltip="Ajouter un sous-titre",
            size=Sizes.icon_button,
            square=False,
        )
        self.add_button.setText("  " + translate("text.library.new_subtitle"))
        self.add_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_button.setMinimumHeight(Sizes.button_md)
        self.add_button.clicked.connect(self._emit_add_requested)
        editor_layout.addWidget(self.add_button)

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
        editor_layout.addLayout(io_row)

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
        self.list_widget.setMinimumHeight(56)
        editor_layout.addWidget(self.list_widget, 0)

        page_layout.addWidget(editor_box, 0)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_clips(self, clips: list) -> None:
        self.list_widget.clear()
        for clip in clips:
            item = QListWidgetItem(self._format_label(clip))
            item.setData(Qt.UserRole, clip.id)
            self.list_widget.addItem(item)

    def set_presets(self, presets: list) -> None:
        """Met à jour la bibliothèque de modèles."""
        self.preset_view.set_presets(list(presets or []))

    def count(self) -> int:
        return self.list_widget.count()

    def selected_clip_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def select_clip_id(self, clip_id: str) -> bool:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.UserRole) == clip_id:
                self.list_widget.setCurrentRow(row)
                return True
        return False

    def selected_preset_id(self) -> str | None:
        return self.preset_view.selected_preset_id()

    def set_text(self, text: str, duration: float | None = None) -> None:
        """Pré-remplit l'éditeur rapide (suite à l'application d'un modèle)."""
        self.text_edit.setPlainText(text or "")
        if duration is not None:
            self.duration_spin.setValue(float(duration))

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


# ---------------------------------------------------------------------------
# Bibliothèque de modèles de texte (tâche 24)
# ---------------------------------------------------------------------------


class TextPresetLibraryView(QWidget):
    """Cartes des modèles de sous-titres intégrés + utilisateur."""

    preset_apply_requested = Signal(str)
    preset_new_clip_requested = Signal(str)
    preset_save_requested = Signal(str, object, object, str)
    preset_delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_presets: list = []
        self._user_preset_ids: set[str] = set()
        self._cards: dict[str, "TextPresetCard"] = {}
        self._selected_preset_id: str | None = None
        self._search_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, 0)
        layout.setSpacing(Spacing.xs)

        # --- Recherche libre ------------------------------------------
        self.search_field = QLineEdit()
        self.search_field.setObjectName("textPresetSearch")
        self.search_field.setPlaceholderText(
            translate("text.library.search")
        )
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(28)
        self.search_field.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_field)

        # --- Cartes des modèles ---------------------------------------
        # Pas de QScrollArea imbriqué : la page parente défile déjà,
        # et un QScrollArea dans un QScrollArea casse la propagation
        # de la taille dans une colonne étroite.
        self.cards_host = QWidget()
        self.cards_host.setObjectName("textPresetCardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(Spacing.xs)
        self.cards_layout.addStretch(1)
        layout.addWidget(self.cards_host, 1)

        # --- Actions : appliquer / nouveau / sauvegarder --------------
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm
        )
        actions_layout.setSpacing(Spacing.xs)

        self.apply_button = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("text.library.apply_to_clip"),
            size=Sizes.icon_button,
            square=False,
            accent=True,
        )
        self.apply_button.setText("  " + translate("text.library.apply_to_clip"))
        self.apply_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.apply_button.setMinimumHeight(Sizes.button_md)
        self.apply_button.clicked.connect(self._emit_apply_requested)
        actions_layout.addWidget(self.apply_button)

        self.new_clip_button = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("text.library.new_clip"),
            size=Sizes.icon_button,
            square=False,
        )
        self.new_clip_button.setText("  " + translate("text.library.new_clip"))
        self.new_clip_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.new_clip_button.setMinimumHeight(Sizes.button_md)
        self.new_clip_button.clicked.connect(self._emit_new_clip_requested)
        actions_layout.addWidget(self.new_clip_button)

        self.save_button = IconButton(
            icon=IconName.SAVE,
            tooltip=translate("text.library.save"),
            size=Sizes.icon_button,
            square=False,
        )
        self.save_button.setText("  " + translate("text.library.save"))
        self.save_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.save_button.setMinimumHeight(Sizes.button_md)
        self.save_button.clicked.connect(self._emit_save_requested)
        actions_layout.addWidget(self.save_button)

        layout.addWidget(actions)

    # ----- API publique -------------------------------------------------

    def set_presets(self, presets: list) -> None:
        self._all_presets = list(presets or [])
        self._user_preset_ids = {
            p.id for p in self._all_presets if not getattr(p, "builtin", False)
        }
        self._rebuild_cards()

    def selected_preset_id(self) -> str | None:
        return self._selected_preset_id

    def select_preset_id(self, preset_id: str) -> bool:
        if preset_id not in self._cards:
            return False
        self._select_preset(preset_id)
        return True

    # ----- Reconstruction ----------------------------------------------

    def _rebuild_cards(self) -> None:
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        from core.text_presets import filter_text_presets

        visible = filter_text_presets(
            self._all_presets, search=self._search_text
        )
        builtin_visible = [
            p for p in visible if getattr(p, "builtin", False)
        ]
        user_visible = [
            p for p in visible if not getattr(p, "builtin", False)
        ]

        if builtin_visible:
            self._render_section_title(
                translate("text.library.section.builtin")
            )
            for preset in builtin_visible:
                self._render_card(preset)
        if user_visible:
            if builtin_visible:
                self.cards_layout.addSpacing(Spacing.xs)
            self._render_section_title(
                translate("text.library.section.user")
            )
            for preset in user_visible:
                self._render_card(preset)
        if not visible:
            empty = QLabel(translate("text.library.no_results"))
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 11px; padding: 16px 8px;"
            )
            self.cards_layout.addWidget(empty)
        elif not user_visible:
            hint = QLabel(translate("text.library.user_empty"))
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 10px; padding: 4px 8px;"
            )
            self.cards_layout.addWidget(hint)

        ids = list(self._cards)
        if self._selected_preset_id in ids:
            self._apply_selection(self._selected_preset_id)
        elif ids:
            self._select_preset(ids[0])
        else:
            self._selected_preset_id = None

    def _render_section_title(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setStyleSheet(
            f"color: {COLORS['muted_strong']}; font-size: 10px;"
            f" font-weight: 800; letter-spacing: 1px;"
            f" padding: 6px 2px 2px 2px;"
        )
        self.cards_layout.addWidget(label)

    def _render_card(self, preset) -> None:
        card = TextPresetCard(
            preset,
            parent=self.cards_host,
            is_user=preset.id in self._user_preset_ids,
        )
        card.clicked.connect(self._select_preset)
        card.delete_requested.connect(self._emit_delete_requested)
        self._cards[preset.id] = card
        self.cards_layout.addWidget(card)

    def _select_preset(self, preset_id: str) -> None:
        self._selected_preset_id = preset_id
        self._apply_selection(preset_id)

    def _apply_selection(self, preset_id: str) -> None:
        for pid, card in self._cards.items():
            card.set_selected(pid == preset_id)

    # ----- Filtres / actions -------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip()
        self._rebuild_cards()

    def _emit_apply_requested(self) -> None:
        if not self._selected_preset_id:
            return
        self.preset_apply_requested.emit(self._selected_preset_id)

    def _emit_new_clip_requested(self) -> None:
        if not self._selected_preset_id:
            return
        self.preset_new_clip_requested.emit(self._selected_preset_id)

    def _emit_save_requested(self) -> None:
        if not self._selected_preset_id:
            return
        for preset in self._all_presets:
            if preset.id == self._selected_preset_id:
                self.preset_save_requested.emit(
                    preset.name,
                    preset.description,
                    preset.style,
                    preset.default_text,
                )
                return

    def _emit_delete_requested(self, preset_id: str) -> None:
        self.preset_delete_requested.emit(preset_id)


class TextPresetCard(QFrame):
    """Carte représentant un modèle de sous-titre dans la bibliothèque."""

    clicked = Signal(str)
    delete_requested = Signal(str)

    CARD_HEIGHT = 60

    def __init__(
        self,
        preset,
        *,
        parent: QWidget | None = None,
        is_user: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("textPresetCard")
        self.preset_id = preset.id
        self._is_user = is_user
        self._selected = False

        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.CARD_HEIGHT)
        accent_color = QColor("#8E7DFA")
        self._accent_color = accent_color
        self._base_style = (
            f"QFrame#textPresetCard {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._hover_style = (
            f"QFrame#textPresetCard {{ background: {COLORS['surface_hover']};"
            f" border: 1px solid {COLORS['border_strong']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._selected_style = (
            f"QFrame#textPresetCard {{ background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, 4, Spacing.sm, 4)
        layout.setSpacing(Spacing.sm)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = QLabel(preset.name)
        title.setStyleSheet(label_style(12, "text", 700))
        text_col.addWidget(title)

        description = preset.description or ""
        if len(description) > 90:
            description = description[:89].rstrip() + "…"
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(label_style(10, "muted", 500))
        text_col.addWidget(desc_label)
        layout.addLayout(text_col, 1)

        self.delete_button = QPushButton()
        self.delete_button.setObjectName("textPresetDelete")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setFixedSize(22, 22)
        self.delete_button.setToolTip(translate("text.library.delete"))
        self.delete_button.setIcon(make_icon(IconName.CLOSE, size=12))
        self.delete_button.setStyleSheet(
            f"QPushButton#textPresetDelete {{ background: transparent;"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 11px; }}"
            f"QPushButton#textPresetDelete:hover {{"
            f" background: {COLORS['danger_dark']};"
            f" border: 1px solid {COLORS['danger']}; }}"
        )
        self.delete_button.clicked.connect(
            lambda _checked=False, pid=preset.id: self.delete_requested.emit(pid)
        )
        self.delete_button.setVisible(self._is_user)
        layout.addWidget(self.delete_button, 0, Qt.AlignVCenter)

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


class TransitionLibraryView(QWidget):
    """Bibliothèque visuelle de transitions (tâche 23).

    Présente la bibliothèque sous forme de cartes cliquables
    (préréglages + transitions utilisateur), avec recherche libre,
    filtre de catégorie et favoris. La durée appliquée peut être
    préremplie depuis le preset puis ajustée avant l'ajout. Le
    composant ne touche pas au modèle : il publie des intentions via
    ses signaux, que :class:`MainWindow` traduit en appels
    :func:`core.transitions.add_transition`.
    """

    add_requested = Signal(str, float)        # preset_id, duration
    save_requested = Signal()                  # MainWindow ouvre le dialogue
    delete_requested = Signal(str)             # preset_id
    favorite_toggled = Signal(str)             # preset_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_presets: list = []          # bibliothèque complète
        self._cards: dict[str, "TransitionPresetCard"] = {}
        self._selected_preset_id: str | None = None
        self._favorites: set[str] = set()
        self._has_two_video_clips = False
        self._search_text = ""
        self._active_filter: str = "all"     # all | fade | wipe | favorites
        self._user_presets_ids: set[str] = set()

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
        self.search_field.setObjectName("transitionsSearch")
        self.search_field.setPlaceholderText(
            translate("transitions.library.search")
        )
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(28)
        self.search_field.textChanged.connect(self._on_search_changed)
        header_layout.addWidget(self.search_field)

        # --- Filtres catégorie / favoris -------------------------------
        self.filter_row = QWidget()
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(Spacing.xs)
        self.filter_buttons: list[QPushButton] = []
        # Catalogue étendu (tâche 26) : trois catégories supplémentaires
        # (« Formes », « Dissolutions », « Glissements fluides ») viennent
        # s'ajouter aux deux catégories historiques « Fondus » et
        # « Balayages ». Les favoris restent un filtre transverse.
        self._filter_keys: list[str] = [
            "all",
            "fade",
            "wipe",
            "shape",
            "dissolve",
            "smooth",
            "favorites",
        ]
        labels = {
            "all": translate("transitions.category.all").upper(),
            "fade": translate("transitions.category.fade").upper(),
            "wipe": translate("transitions.category.wipe").upper(),
            "shape": translate("transitions.category.shape").upper(),
            "dissolve": translate("transitions.category.dissolve").upper(),
            "smooth": translate("transitions.category.smooth").upper(),
            "favorites": translate("transitions.category.favorites").upper(),
        }
        for key in self._filter_keys:
            button = self._make_filter_button(labels[key], key)
            filter_layout.addWidget(button)
        filter_layout.addStretch(1)
        header_layout.addWidget(self.filter_row)
        layout.addWidget(header)

        # --- Astuce + état sélection clips -----------------------------
        self.selection_hint = QLabel(
            translate("transitions.library.apply_hint")
        )
        self.selection_hint.setWordWrap(True)
        self.selection_hint.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; padding: 4px 6px;"
            f" background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
        )
        hint_container = QWidget()
        hint_layout = QHBoxLayout(hint_container)
        hint_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        hint_layout.addWidget(self.selection_hint)
        layout.addWidget(hint_container)

        # --- Zone défilante : cartes preset ----------------------------
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("transitionsScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: transparent; }}"
        )
        self.cards_host = QWidget()
        self.cards_host.setObjectName("transitionsCardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        self.cards_layout.setSpacing(Spacing.xs)
        self.cards_layout.addStretch(1)
        self.scroll_area.setWidget(self.cards_host)
        layout.addWidget(self.scroll_area, 1)

        # --- Durée + bouton d'action principal -------------------------
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm
        )
        actions_layout.setSpacing(Spacing.xs)

        duration_row = QWidget()
        duration_layout = QHBoxLayout(duration_row)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(Spacing.sm)
        duration_label = QLabel(
            translate("transitions.library.duration_label")
        )
        duration_label.setStyleSheet(label_style(11, "muted", 600))
        duration_layout.addWidget(duration_label)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setObjectName("transitionDurationSpin")
        self.duration_spin.setRange(0.1, 5.0)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setValue(0.5)
        self.duration_spin.setMinimumWidth(82)
        duration_layout.addWidget(self.duration_spin, 1)
        actions_layout.addWidget(duration_row)

        self.add_button = self._make_wide_button(
            IconName.PLUS,
            translate("transitions.library.apply"),
            accent=True,
            tooltip=translate("transitions.library.apply"),
        )
        self.add_button.clicked.connect(self._emit_add_requested)
        actions_layout.addWidget(self.add_button)

        self.save_button = self._make_wide_button(
            IconName.SAVE,
            translate("transitions.library.save"),
            accent=False,
            tooltip=translate("transitions.library.save"),
        )
        self.save_button.clicked.connect(self._emit_save_requested)
        actions_layout.addWidget(self.save_button)
        layout.addWidget(actions)

        self._refresh_buttons()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_presets(
        self,
        presets: list,
        *,
        favorites: list[str] | None = None,
    ) -> None:
        """Met à jour la bibliothèque (intégrés + utilisateur + favoris)."""
        self._all_presets = list(presets or [])
        self._user_presets_ids = {
            p.id for p in self._all_presets if not getattr(p, "builtin", False)
        }
        if favorites is not None:
            self._favorites = set(favorites)
        self._rebuild_cards()

    def set_favorites(self, favorites: list[str]) -> None:
        """Met à jour l'ensemble des favoris sans toucher aux presets."""
        self._favorites = set(favorites or [])
        self._rebuild_cards()

    def set_selection_context(self, *, has_two_video_clips: bool) -> None:
        """Synchronise l'état de sélection timeline avec l'interface."""
        self._has_two_video_clips = has_two_video_clips
        self._refresh_buttons()

    def selected_preset_id(self) -> str | None:
        return self._selected_preset_id

    def select_preset(self, preset_id: str) -> bool:
        if preset_id not in self._cards:
            return False
        self._select_preset(preset_id)
        return True

    def preset_count(self) -> int:
        return len(self._all_presets)

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

    def _make_filter_button(self, label: str, key: str) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("transitionFilterTab")
        button.setCheckable(True)
        button.setChecked(key == "all")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            f"QPushButton#transitionFilterTab {{"
            f" background: transparent;"
            f" color: {COLORS['muted']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
            f" padding: 3px 8px;"
            f" font-weight: 600; font-size: 10px;"
            f" letter-spacing: 0.4px; }}"
            f"QPushButton#transitionFilterTab:hover {{"
            f" color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
            f"QPushButton#transitionFilterTab:checked {{"
            f" color: {COLORS['accent']};"
            f" background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']}; }}"
        )
        button.clicked.connect(
            lambda _checked=False, k=key: self._select_filter(k)
        )
        self.filter_buttons.append(button)
        return button

    # ----- Filtres -----------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip()
        self._rebuild_cards()

    def _select_filter(self, key: str) -> None:
        self._active_filter = key
        for button in self.filter_buttons:
            button.setChecked(False)
        if key in self._filter_keys:
            index = self._filter_keys.index(key)
            if 0 <= index < len(self.filter_buttons):
                self.filter_buttons[index].setChecked(True)
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

        # Import paresseux pour éviter une dépendance circulaire lors
        # des tests CLI qui n'instancient pas PySide6.
        from core.transition_presets import (
            TransitionPresetCategory,
            filter_transition_presets,
        )

        category: TransitionPresetCategory | None = None
        favorites_only = False
        if self._active_filter == "fade":
            category = TransitionPresetCategory.FADE
        elif self._active_filter == "wipe":
            category = TransitionPresetCategory.WIPE
        elif self._active_filter == "shape":
            category = TransitionPresetCategory.SHAPE
        elif self._active_filter == "dissolve":
            category = TransitionPresetCategory.DISSOLVE
        elif self._active_filter == "smooth":
            category = TransitionPresetCategory.SMOOTH
        elif self._active_filter == "favorites":
            favorites_only = True

        visible = filter_transition_presets(
            self._all_presets,
            search=self._search_text,
            category=category,
            favorites=list(self._favorites),
            favorites_only=favorites_only,
        )
        builtin_visible = [
            p for p in visible if getattr(p, "builtin", False)
        ]
        user_visible = [
            p for p in visible if not getattr(p, "builtin", False)
        ]

        if not visible:
            if self._active_filter == "favorites":
                empty_text = translate("transitions.library.favorites_empty")
            else:
                empty_text = translate("transitions.library.no_results")
            self._render_empty_message(empty_text)
        else:
            if builtin_visible:
                self._render_section_title(
                    translate("transitions.library.section.builtin")
                )
                for preset in builtin_visible:
                    self._render_card(preset)
            if user_visible:
                if builtin_visible:
                    self.cards_layout.addSpacing(Spacing.xs)
                self._render_section_title(
                    translate("transitions.library.section.user")
                )
                for preset in user_visible:
                    self._render_card(preset)
            if not user_visible and self._active_filter != "favorites":
                self._render_user_empty_hint()

        # Restaurer la sélection si possible, sinon prendre la première.
        ids = list(self._cards)
        if self._selected_preset_id in ids:
            self._apply_selection(self._selected_preset_id)
        elif ids:
            self._select_preset(ids[0])
        else:
            self._selected_preset_id = None
            self._refresh_buttons()
        self._refresh_buttons()

    def _render_empty_message(self, message: str) -> None:
        empty = QLabel(message)
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; padding: 16px 8px;"
        )
        self.cards_layout.addWidget(empty)

    def _render_user_empty_hint(self) -> None:
        if not self._user_presets_ids:
            hint = QLabel(translate("transitions.library.user_empty"))
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

    def _render_card(self, preset) -> None:
        card = TransitionPresetCard(
            preset,
            parent=self.cards_host,
            is_user=preset.id in self._user_presets_ids,
            is_favorite=preset.id in self._favorites,
        )
        card.clicked.connect(self._select_preset)
        card.delete_requested.connect(self._emit_delete_requested)
        card.favorite_toggled.connect(self._emit_favorite_toggled)
        self._cards[preset.id] = card
        self.cards_layout.addWidget(card)

    # ----- Sélection ---------------------------------------------------

    def _select_preset(self, preset_id: str) -> None:
        self._selected_preset_id = preset_id
        self._apply_selection(preset_id)
        self._sync_duration_from_preset()
        self._refresh_buttons()

    def _apply_selection(self, preset_id: str) -> None:
        for pid, card in self._cards.items():
            card.set_selected(pid == preset_id)

    def _sync_duration_from_preset(self) -> None:
        """Préremplit la durée avec celle du preset sélectionné."""
        if self._selected_preset_id is None:
            return
        for preset in self._all_presets:
            if preset.id == self._selected_preset_id:
                self.duration_spin.blockSignals(True)
                self.duration_spin.setValue(float(preset.default_duration))
                self.duration_spin.blockSignals(False)
                return

    # ----- Boutons d'action -------------------------------------------

    def _refresh_buttons(self) -> None:
        can_apply = (
            self._has_two_video_clips
            and self._selected_preset_id is not None
        )
        self.add_button.setEnabled(can_apply)
        # Sauvegarde possible dès qu'une transition existe dans le projet
        # ; l'activation côté MainWindow filtre déjà les cas invalides.
        self.save_button.setEnabled(self._has_two_video_clips)

    def _emit_add_requested(self) -> None:
        if (
            not self._has_two_video_clips
            or self._selected_preset_id is None
        ):
            return
        self.add_requested.emit(
            self._selected_preset_id,
            float(self.duration_spin.value()),
        )

    def _emit_save_requested(self) -> None:
        if not self._has_two_video_clips:
            return
        self.save_requested.emit()

    def _emit_delete_requested(self, preset_id: str) -> None:
        self.delete_requested.emit(preset_id)

    def _emit_favorite_toggled(self, preset_id: str) -> None:
        self.favorite_toggled.emit(preset_id)


# ---------------------------------------------------------------------------
# Cartes et dialogue de preset pour les transitions
# ---------------------------------------------------------------------------


_TRANSITION_CATEGORY_ACCENTS: dict[str, str] = {
    "fade": "#36E6C3",      # turquoise
    "wipe": "#8E7DFA",      # violet
}


class TransitionPresetCard(QFrame):
    """Carte représentant un preset de transition dans la bibliothèque."""

    clicked = Signal(str)
    delete_requested = Signal(str)
    favorite_toggled = Signal(str)

    CARD_HEIGHT = 60

    def __init__(
        self,
        preset,
        *,
        parent: QWidget | None = None,
        is_user: bool = False,
        is_favorite: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("transitionPresetCard")
        self.preset_id = preset.id
        self._is_builtin = bool(getattr(preset, "builtin", False))
        self._is_user = is_user
        self._is_favorite = is_favorite
        self._selected = False

        accent_key = preset.category.value
        accent = _TRANSITION_CATEGORY_ACCENTS.get(
            accent_key, COLORS["accent"]
        )
        accent_color = QColor(accent)
        self._accent_color = accent_color

        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.CARD_HEIGHT)
        self._base_style = (
            f"QFrame#transitionPresetCard {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._hover_style = (
            f"QFrame#transitionPresetCard {{ background: {COLORS['surface_hover']};"
            f" border: 1px solid {COLORS['border_strong']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self._selected_style = (
            f"QFrame#transitionPresetCard {{ background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']};"
            f" border-left: 3px solid {accent_color.name()};"
            f" border-radius: 8px; }}"
        )
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, 4, Spacing.sm, 4)
        layout.setSpacing(Spacing.sm)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(Spacing.xs)
        self._title = QLabel(preset.name)
        self._title.setStyleSheet(label_style(12, "text", 700))
        title_row.addWidget(self._title)
        badge = QLabel(
            translate(
                f"transitions.category.{preset.category.value}"
            ).upper()
        )
        badge.setStyleSheet(
            f"QLabel {{ color: {accent_color.name()};"
            f" background: transparent; font-size: 9px;"
            f" font-weight: 700; padding: 0; }}"
        )
        title_row.addWidget(badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)

        description = preset.description or ""
        if len(description) > 80:
            description = description[:79].rstrip() + "…"
        self._description = QLabel(description)
        self._description.setWordWrap(True)
        self._description.setStyleSheet(label_style(10, "muted", 500))
        text_col.addWidget(self._description)

        duration_label = QLabel(
            translate("transitions.library.duration_label")
            + f" : {preset.default_duration:.2f} s"
        )
        duration_label.setStyleSheet(label_style(10, "muted", 600))
        text_col.addWidget(duration_label)
        layout.addLayout(text_col, 1)

        # Boutons à droite : étoile favori + croix suppression.
        actions_col = QVBoxLayout()
        actions_col.setContentsMargins(0, 0, 0, 0)
        actions_col.setSpacing(2)
        self.favorite_button = QPushButton()
        self.favorite_button.setObjectName("transitionFavoriteBtn")
        self.favorite_button.setCursor(Qt.PointingHandCursor)
        self.favorite_button.setFocusPolicy(Qt.NoFocus)
        self.favorite_button.setFixedSize(22, 22)
        self._refresh_favorite_icon()
        self.favorite_button.clicked.connect(
            lambda _checked=False, pid=preset.id: self.favorite_toggled.emit(pid)
        )
        actions_col.addWidget(self.favorite_button, 0, Qt.AlignRight)

        self.delete_button = QPushButton()
        self.delete_button.setObjectName("transitionDeleteBtn")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setFixedSize(22, 22)
        self.delete_button.setToolTip(
            translate("transitions.library.delete")
        )
        delete_icon = make_icon(IconName.CLOSE, size=12)
        self.delete_button.setIcon(delete_icon)
        self.delete_button.setStyleSheet(
            f"QPushButton#transitionDeleteBtn {{ background: transparent;"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 11px; }}"
            f"QPushButton#transitionDeleteBtn:hover {{"
            f" background: {COLORS['danger_dark']};"
            f" border: 1px solid {COLORS['danger']}; }}"
        )
        self.delete_button.clicked.connect(
            lambda _checked=False, pid=preset.id: self.delete_requested.emit(pid)
        )
        self.delete_button.setVisible(self._is_user)
        actions_col.addWidget(self.delete_button, 0, Qt.AlignRight)
        layout.addLayout(actions_col, 0)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def set_favorite(self, favorite: bool) -> None:
        if favorite == self._is_favorite:
            return
        self._is_favorite = favorite
        self._refresh_favorite_icon()

    def set_user(self, is_user: bool) -> None:
        if is_user == self._is_user:
            return
        self._is_user = is_user
        self.delete_button.setVisible(self._is_user)

    def _refresh_favorite_icon(self) -> None:
        # Pas d'icône étoile dédiée : on retombe sur un caractère
        # unicode qui rend bien avec la police par défaut. Une icône
        # SVG dédiée pourrait être ajoutée plus tard.
        glyph = "★" if self._is_favorite else "☆"
        self.favorite_button.setText(glyph)
        color = COLORS["warning"] if self._is_favorite else COLORS["muted"]
        self.favorite_button.setStyleSheet(
            f"QPushButton#transitionFavoriteBtn {{"
            f" background: transparent;"
            f" color: {color};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 11px;"
            f" font-size: 12px; font-weight: 700; }}"
            f"QPushButton#transitionFavoriteBtn:hover {{"
            f" color: {COLORS['warning']};"
            f" background: {COLORS['surface_hover']}; }}"
        )
        self.favorite_button.setToolTip(
            translate("transitions.library.favorite_remove")
            if self._is_favorite
            else translate("transitions.library.favorite_add")
        )

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


class SaveTransitionPresetDialog(QDialog):
    """Boîte de dialogue « Enregistrer une transition » (tâche 23)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        default_name: str = "",
        default_type: str = "crossfade",
        default_duration: float = 0.5,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("saveTransitionDialog")
        self.setWindowTitle(
            translate("transitions.library.save_dialog.title")
        )
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.lg, Spacing.lg, Spacing.lg, Spacing.md)
        layout.setSpacing(Spacing.sm)

        intro = QLabel(
            translate("transitions.library.save_dialog.title")
        )
        intro.setStyleSheet(label_style(13, "text", 700))
        layout.addWidget(intro)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.sm)
        form.setLabelAlignment(Qt.AlignLeft)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            translate("transitions.library.save_dialog.name")
        )
        self.name_edit.setText(default_name)
        self.name_edit.setMaxLength(60)
        form.addRow(
            _form_label(translate("transitions.library.save_dialog.name")),
            self.name_edit,
        )

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText(
            translate("transitions.library.save_dialog.description")
        )
        self.description_edit.setFixedHeight(60)
        form.addRow(
            _form_label(
                translate("transitions.library.save_dialog.description")
            ),
            self.description_edit,
        )

        self.type_combo = QComboBox()
        self.type_combo.addItem(
            translate("transitions.preset.crossfade.name"), "crossfade"
        )
        self.type_combo.addItem(
            translate("transitions.preset.fade_black.name"), "fade_black"
        )
        self.type_combo.addItem(
            translate("transitions.preset.wipe_left.name"), "wipe_left"
        )
        self.type_combo.addItem(
            translate("transitions.preset.wipe_right.name"), "wipe_right"
        )
        index = self.type_combo.findData(default_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        form.addRow(
            _form_label(translate("transitions.library.save_dialog.type")),
            self.type_combo,
        )

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 5.0)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setValue(float(default_duration))
        form.addRow(
            _form_label(
                translate("transitions.library.save_dialog.duration")
            ),
            self.duration_spin,
        )
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText(
            translate("transitions.library.save_dialog.save")
        )
        buttons.button(QDialogButtonBox.Cancel).setText(
            translate("transitions.library.save_dialog.cancel")
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        self.accept()

    def result_data(self) -> tuple[str, str, str, float]:
        """Retourne ``(name, description, transition_type, duration)``."""
        name = self.name_edit.text().strip()
        description = self.description_edit.toPlainText().strip()
        transition_type = str(self.type_combo.currentData() or "crossfade")
        duration = float(self.duration_spin.value())
        return name, description, transition_type, duration


def _form_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(label_style(11, "muted", 600))
    return label


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


class GraphicsLibraryView(QWidget):
    """Bibliothèque compacte de générateurs de calques graphiques."""

    create_requested = Signal(str)
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.sm)

        title = QLabel("CALQUES GRAPHIQUES")
        title.setStyleSheet(label_style(11, "muted_strong", 800))
        layout.addWidget(title)
        intro = QLabel(
            "Ajoutez un titre, une forme, un aplat ou une image à la tête "
            "de lecture. Chaque élément devient un clip animable sur G1."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(intro)

        entries = (
            ("Titre", "Texte éditable avec contour et ombre", "text", IconName.TEXT),
            ("Rectangle", "Forme colorée redimensionnable", "rectangle", IconName.COLOR),
            ("Aplat", "Fond de couleur plein cadre", "solid", IconName.FILM),
        )
        self.create_buttons: dict[str, IconButton] = {}
        for name, description, kind, icon in entries:
            card = QFrame()
            card.setObjectName("graphicCard")
            card.setStyleSheet(
                f"QFrame#graphicCard {{ background: {COLORS['panel_alt']};"
                f" border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
            labels = QVBoxLayout()
            heading = QLabel(name)
            heading.setStyleSheet(label_style(12, "text", 700))
            detail = QLabel(description)
            detail.setWordWrap(True)
            detail.setStyleSheet(label_style(10, "muted", 500))
            labels.addWidget(heading)
            labels.addWidget(detail)
            row.addLayout(labels, 1)
            button = IconButton(
                icon=icon, text="Ajouter", tooltip=f"Ajouter : {name}",
                square=False, size=Sizes.icon_button,
            )
            button.clicked.connect(
                lambda _checked=False, value=kind: self.create_requested.emit(value)
            )
            self.create_buttons[kind] = button
            row.addWidget(button)
            layout.addWidget(card)

        self.import_image_button = IconButton(
            icon=IconName.IMPORT,
            text="Importer une image…",
            tooltip="Créer un calque graphique depuis une image",
            square=False,
            size=Sizes.icon_button,
        )
        self.import_image_button.clicked.connect(self.import_requested.emit)
        layout.addWidget(self.import_image_button)
        layout.addStretch(1)


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
