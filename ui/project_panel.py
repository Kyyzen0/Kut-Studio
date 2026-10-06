"""Panneau bibliothèque de Kut-Studio.

Refonte UI/UX :

- en-tête compact : onglets Projet / Favoris + compteur ;
- champ de recherche global filtrant les assets ;
- arborescence de dossiers (aplatie en sous-sections) ;
- vignettes média en grille avec miniature, nom court et durée ;
- la sélection est marquée par un filet turquoise fin.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.effects_library import UserPresetStore
from core.library_organization import (
    AssetUsage,
    LibraryOrganization,
    collect_missing_assets,
    is_asset_missing,
    usage_map,
)
from core.project_model import MediaAsset
from ui.adaptive_layout import ShrinkableScrollArea
from ui.keyboard_navigation import let_tab_leave_in
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconLabel, IconName
from ui.audio_effects_library import AudioEffectsLibraryView
from ui.graphics_library import GraphicsLibraryView
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
from ui.theme import COLORS, label_style, set_role
from ui.project_panel_widgets.asset_bin import (
    AssetBin,
    _format_asset_caption,
    _format_duration,
    _make_asset_thumbnail,
)  # noqa: F401
from ui.project_panel_widgets.subtitle_library import SubtitleLibraryView  # noqa: F401
from ui.project_panel_widgets.sequence_library import SequenceLibraryView
from ui.project_panel_widgets.text_presets_view import (
    TextPresetCard,
    TextPresetLibraryView,
)  # noqa: F401
from ui.project_panel_widgets.transition_library import (
    SaveTransitionPresetDialog,
    TransitionLibraryView,
    TransitionPresetCard,
    _transition_accent,
    _form_label,
)  # noqa: F401
from ui.search_field import SearchField
from ui.sfx_library import SfxLibraryView
from ui.project_panel_widgets.effects_library_view import (
    EffectPresetCard,
    EffectsLibraryView,
    SavePresetDialog,
    _category_accent,
)  # noqa: F401
from ui.i18n import translate


BROWSE_MIN_HEIGHT = 84
"""Hauteur plancher (px) du bloc dossiers / filtres / tags : en dessous il défile (voir ``ShrinkableScrollArea``)."""


def _audio_modes() -> tuple[tuple[str, str], ...]:
    """Onglets de la section Audio : fichiers du projet, effets audio, bibliothèque SFX."""
    return (("files", translate("library.audio_files")), ("effects", translate("audio_effects.section")),
            ("sfx", translate("sfx.section")))


class ProjectPanel(QWidget):
    """Bibliothèque de médias et de sous-titres du projet courant."""

    asset_selected = Signal(str)
    multicam_create_requested = Signal(list)  # médias sélectionnés : « Créer une séquence Multicam… »
    add_to_timeline_requested = Signal(str)
    import_requested = Signal()
    add_subtitle_requested = Signal(str, float)
    import_subtitles_requested = Signal()
    export_subtitles_requested = Signal()
    subtitle_selected = Signal(str)
    add_transition_requested = Signal(str, float)
    # Bibliothèque d'effets (tâche 22)
    effect_apply_requested = Signal(str)  # preset_id
    audio_effect_apply_requested = Signal(str)  # preset_id
    audio_effect_preset_save_requested = Signal()
    audio_effect_preset_delete_requested = Signal(str)
    audio_effect_favorite_toggled = Signal(str)
    sfx_add_requested = Signal(str)  # identifiant du SFX, posé à la tête de lecture
    sfx_on_cuts_requested = Signal(list)  # identifiants du SFX, un par cut de la piste vidéo
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
    proxy_action_requested = Signal(str, str)
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
        # Sous-mode de la section Audio : fichiers (page 1) ou effets (page 6).
        self._audio_mode = "files"
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
        # ``asset -> (état, progression)`` : fourni par la fenêtre principale.
        self.proxy_state_provider = None
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
        self._section_id = "media"
        title = QLabel(translate("rail.media"))
        self._title_label = title
        set_role(title, "panel-title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        # Compteur global (mis à jour à chaque mutation).
        self.media_count = QLabel(f"0 {translate('library.word.media')}")
        self.media_count.setStyleSheet(label_style(10, "muted", 500))
        title_row.addWidget(self.media_count)
        quick_import = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("preview.import"),
            size=Sizes.icon_button_sm,
        )
        quick_import.setAccessibleName(translate("preview.import"))
        self._quick_import = quick_import
        quick_import.clicked.connect(self.import_requested.emit)
        title_row.addWidget(quick_import)
        header_layout.addLayout(title_row)

        # Onglets Projet / Favoris : boutons ``checkable`` dans une
        # rangée horizontale pour un rendu segmented compact.
        scope_tabs_row = QWidget()
        scope_tabs_layout = QHBoxLayout(scope_tabs_row)
        scope_tabs_layout.setContentsMargins(0, 0, 0, 0)
        scope_tabs_layout.setSpacing(Spacing.xs)
        self.scope_tab_buttons: list[QPushButton] = []
        for index, label in enumerate((translate("panel.project"), translate("transitions.category.favorites"))):
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
        self.search_field = SearchField(translate("library.search_placeholder"), object_name="librarySearch")
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
        # Le bloc de navigation est à hauteur fixe (``sizeHint``) : sans
        # plafond, l'arbre réclame ~210 px, surtout vides, et repousse la
        # timeline sous le bord d'une fenêtre 1440 × 900. Au-delà, il défile.
        self.folder_tree.setMaximumHeight(156)
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
        self.manage_tags_button = QPushButton(translate("library.manage_tags"))
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

        # Dossiers, filtres et tags tiennent à hauteur naturelle quand la colonne est haute. Quand elle ne
        # l'est pas (720 px de fenêtre : ~400 px pour toute la bibliothèque), le bloc défile au lieu d'être
        # écrasé sous son minimum, ce qui superposait les puces de filtre à la liste des dossiers.
        browse_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        browse_scroll = ShrinkableScrollArea(browse_content, min_height=BROWSE_MIN_HEIGHT)
        browse_scroll.setObjectName("libraryBrowseScroll")
        self.library_browse_content = browse_scroll
        layout.addWidget(browse_scroll, 0)

        # ----- Contenu empilé (grilles + placeholders) ----------------
        # Seule zone élastique du panneau : elle absorbe toute la
        # hauteur restante, les blocs au-dessus étant figés.
        # Bascule Fichiers / Effets, visible uniquement dans la section Audio.
        self.audio_mode_row = QWidget()
        audio_mode_layout = QHBoxLayout(self.audio_mode_row)
        audio_mode_layout.setContentsMargins(Spacing.sm, Spacing.xs, Spacing.sm, 0)
        audio_mode_layout.setSpacing(Spacing.xs)
        self.audio_mode_buttons: dict[str, QPushButton] = {}
        for mode, label in _audio_modes():
            button = QPushButton(label)
            button.setObjectName("scopeTab")
            button.setCheckable(True)
            button.setChecked(mode == "files")
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
                lambda _checked=False, m=mode: self.set_audio_mode(m)
            )
            self.audio_mode_buttons[mode] = button
            audio_mode_layout.addWidget(button)
        audio_mode_layout.addStretch(1)
        self.audio_mode_row.setVisible(False)
        layout.addWidget(self.audio_mode_row)

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

        self.audio_effects_view = AudioEffectsLibraryView(self)
        self.content_stack.addWidget(self.audio_effects_view)

        # Séquences du projet (page 7) : créer, ouvrir, imbriquer.
        self.sequence_view = SequenceLibraryView(self)
        self.content_stack.addWidget(self.sequence_view)

        # Bibliothèque SFX synthétisés (page 8), troisième onglet de la section Audio.
        self.sfx_view = SfxLibraryView(self)
        self.content_stack.addWidget(self.sfx_view)
        self.sfx_view.add_requested.connect(self.sfx_add_requested)
        self.sfx_view.on_cuts_requested.connect(self.sfx_on_cuts_requested)
        # Chaque page est faite pour défiler : on neutralise leur
        # ``minimumSizeHint`` (l'éditeur de sous-titres réclame 360 px),
        # sinon la pile réserve cette hauteur et la grille de vignettes
        # disparaît. On ne fige pas la hauteur : le layout accorde le
        # surplus quand la colonne est haute.
        for page in (
            self.bin_videos, self.bin_audios, self.subtitle_view,
            self.effects_view, self.transition_view,
            self.graphics_view, self.audio_effects_view, self.sequence_view,
            self.sfx_view,
        ):
            page.setMinimumHeight(0)
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(self.content_stack, 1)
        self.audio_effects_view.apply_requested.connect(
            self.audio_effect_apply_requested
        )
        self.audio_effects_view.save_requested.connect(
            self.audio_effect_preset_save_requested
        )
        self.audio_effects_view.delete_requested.connect(
            self.audio_effect_preset_delete_requested
        )
        self.audio_effects_view.favorite_toggled.connect(
            self.audio_effect_favorite_toggled
        )

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
        # Marges latérales réduites : à 1180 px de fenêtre la colonne ne fait que ~230 px, et les libellés
        # « Importer » / « Timeline » étaient tronqués (« Im…rter »).
        actions_layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.md)
        actions_layout.setSpacing(Spacing.xs)
        self.import_button = self._make_wide_button(
            IconName.IMPORT, translate("library.import"),
            accent=False,
            tooltip=translate("library.import_tip"),
        )
        self.import_button.clicked.connect(self.import_requested)
        actions_layout.addWidget(self.import_button, 1)

        self.add_to_timeline_button = self._make_wide_button(
            IconName.PLUS, translate("panel.timeline"),
            accent=True,
            tooltip=translate("library.add_tip"),
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
        let_tab_leave_in(self)  # Tab sort des éditeurs multilignes (sous-titres) au lieu d'y insérer une tabulation

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
            self._organization.project,
            self._organization,
            proxy_state_for=self.proxy_state_provider,
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
            title=translate("dialog.folder.new_title"),
            label=translate("dialog.folder.name_label"),
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
        QMessageBox.warning(self, translate("panel.library"), message)

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
            proxy_state=badge.proxy_state,
            proxy_progress=badge.proxy_progress,
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
        builder.proxy_action_requested.connect(self.proxy_action_requested.emit)
        builder.show_in_timeline_requested.connect(
            self.asset_occurrences_requested.emit
        )
        builder.move_to_folder_requested.connect(
            self.asset_move_to_folder_requested.emit
        )
        builder.tag_toggled.connect(self.asset_tag_toggled.emit)
        builder.manage_tags_requested.connect(self.tag_manager_requested.emit)
        selected = self._current_bin().selected_asset_ids
        if len(selected) >= 2 and asset_id in selected:
            menu.addSeparator()
            create_multicam = menu.addAction(translate("multicam.menu.create"))
            create_multicam.triggered.connect(lambda: self.multicam_create_requested.emit(list(selected)))
        menu.exec(global_pos)

    def set_audio_mode(self, mode: str) -> None:
        """Bascule la section Audio entre fichiers et préréglages d'effets."""
        if mode not in self.audio_mode_buttons:
            return
        self._audio_mode = mode
        for key, button in self.audio_mode_buttons.items():
            button.setChecked(key == mode)
        self.select_section("audio")

    def set_user_audio_effect_presets(self, presets: list) -> None:
        """Met à jour les presets d'effets audio utilisateur."""
        self.audio_effects_view.set_user_presets(list(presets or []))
        if self._active_page_index == 6:
            self._refresh_count()

    def set_audio_effect_favorites(self, favorites: list) -> None:
        """Met à jour les favoris de la bibliothèque d'effets audio."""
        self.audio_effects_view.set_favorites(list(favorites or []))

    def update_audio_effects_clip_context(
        self, *, has_audio_clip: bool, clip_has_audio_effects: bool = False
    ) -> None:
        """Synchronise l'état « clip audio sélectionné » avec la bibliothèque."""
        self.audio_effects_view.set_clip_context(
            has_audio_clip=has_audio_clip,
            clip_has_audio_effects=clip_has_audio_effects,
        )

    def set_sequences(self, entries) -> None:
        """Liste des séquences du projet (page « Séquences »)."""
        self.sequence_view.set_sequences(entries)

    def select_section(self, section_id: str) -> None:
        """Affiche la bibliothèque demandée par la navigation globale."""
        page_index = {
            "media": 0,
            "audio": 1,
            "text": 2,
            "effects": 3,
            "transitions": 4,
            "graphics": 5,
            "sequences": 7,
        }.get(section_id)
        if page_index is None:
            return
        self._section_id = section_id
        self._title_label.setText(translate(f"rail.{section_id}"))      # le titre du panneau dit la page affichée
        if page_index == 1:
            page_index = {"effects": 6, "sfx": 8}.get(self._audio_mode, 1)
        self._active_page_index = page_index
        self.content_stack.setCurrentIndex(page_index)
        self.audio_mode_row.setVisible(page_index in {1, 6, 8})
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
            self.media_count.setText(translate("library.no_favorites"))
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

    def retranslate(self) -> None:
        """En-tête, onglets, recherche et boutons dans la langue courante (changement de langue à chaud)."""
        self._title_label.setText(translate(f"rail.{self._section_id}"))
        self._quick_import.setToolTip(translate("preview.import"))
        self._quick_import.setAccessibleName(translate("preview.import"))
        for button, key in zip(self.scope_tab_buttons, ("panel.project", "transitions.category.favorites")):
            button.setText(translate(key))
        for mode, label in _audio_modes():
            self.audio_mode_buttons[mode].setText(label)
        self.sfx_view.retranslate()
        self.search_field.setPlaceholderText(translate("library.search_placeholder"))
        self.manage_tags_button.setText(translate("library.manage_tags"))
        self.import_button.setText("  " + translate("library.import"))
        self.import_button.setToolTip(translate("library.import_tip"))
        self.add_to_timeline_button.setText("  " + translate("panel.timeline"))
        self.add_to_timeline_button.setToolTip(translate("library.add_tip"))
        self.folder_tree.retranslate()
        self.filter_chips.retranslate()
        self._refresh_count()

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
        button.setStyleSheet("QToolButton { padding: 4px 4px; }")
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
                    if bin_widget is self.bin_videos:
                        self.select_section("media")
                    else:
                        # Révèle le fichier même si la bascule est sur « Effets ».
                        self.set_audio_mode("files")
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
            self.media_count.setText(translate("library.no_favorites"))
            return
        if self._active_page_index == 0:
            count = self.bin_videos.count()
            label_word = translate("library.word.media") if count <= 1 else translate("library.word.media_many")
        elif self._active_page_index == 1:
            count = self.bin_audios.count()
            label_word = translate("library.word.audio") if count <= 1 else translate("library.word.audio_many")
        elif self._active_page_index == 2:
            count = self.subtitle_view.count()
            label_word = translate("library.word.subtitle") if count <= 1 else translate("library.word.subtitle_many")
        elif self._active_page_index == 3:
            # Bibliothèque d'effets : on annonce le total (intégrés +
            # utilisateur). Le détail reste dans la bibliothèque.
            count = self.effects_view.preset_count()
            label_word = translate("library.word.preset") if count <= 1 else translate("library.word.preset_many")
        elif self._active_page_index == 4:
            # Bibliothèque de transitions : on annonce le total.
            count = self.transition_view.preset_count()
            label_word = translate("library.word.preset") if count <= 1 else translate("library.word.preset_many")
        elif self._active_page_index == 6:
            count = self.audio_effects_view.preset_count()
            label_word = translate("library.word.preset") if count <= 1 else translate("library.word.preset_many")
        elif self._active_page_index == 8:
            count = self.sfx_view.list.count()
            label_word = translate("library.word.sound") if count <= 1 else translate("library.word.sound_many")
        elif self._active_page_index == 7:
            count = len(self.sequence_view.entries())
            label_word = translate("library.word.sequence") if count <= 1 else translate("library.word.sequence_many")
        else:
            count = 0
            label_word = translate("library.word.media")
        self.media_count.setText(f"{count} {label_word}")

    def _make_placeholder_label(self, message: str) -> QLabel:
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(label_style(13, "muted", 500))
        return label


# ---------------------------------------------------------------------------
# Bibliothèque de modèles de texte (tâche 24)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cartes et dialogue de preset pour les transitions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bibliothèque d'effets (tâche 22)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers privés : vignettes et libellés
# ---------------------------------------------------------------------------


