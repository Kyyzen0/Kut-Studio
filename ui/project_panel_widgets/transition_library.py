"""Widgets de la bibliothèque de médias : transition_library."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName, make_icon
from ui.theme import COLORS, label_style


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
