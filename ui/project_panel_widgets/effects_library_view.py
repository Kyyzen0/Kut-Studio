"""Widgets de la bibliothèque de médias : effects_library_view."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
from core.effects_library import (
    EffectCategory,
    EffectPreset,
    builtin_presets,
    filter_presets,
)
from ui.adaptive_layout import ElidedLabel, FlowLayout, ShrinkableScrollArea
from ui.design_system import DIALOG_MARGINS, Radius, Sizes, Spacing, Typography, Weights
from ui.i18n import translate
from ui.icons import IconButton, IconName, make_icon
from ui.keyboard_navigation import let_tab_leave, set_single_default
from ui.theme import COLORS, active_palette, label_style
from ui.project_panel_widgets.wide_button import make_wide_button
from ui.search_field import SearchField


_CATEGORY_RANK: dict[EffectCategory, int] = {
    EffectCategory.COLOR: 0,       # bleu
    EffectCategory.CREATIVE: 1,    # ambre
    EffectCategory.STYLIZED: 2,    # corail
    EffectCategory.LOOK: 3,        # violet
}


def _category_accent(category: EffectCategory) -> str:
    """Teinte d'une catégorie d'effets : un rang de la palette catégorielle du thème actif (suit le thème, jamais figée)."""
    colors = active_palette().category_colors
    return colors[_CATEGORY_RANK.get(category, 0) % len(colors)]


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
        accent = _category_accent(preset.category)

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
        self._title = ElidedLabel(preset.name)  # un nom long ne doit pas pousser le bouton hors de la carte
        self._title.setStyleSheet(label_style(12, "text", 700))
        title_row.addWidget(self._title)
        # Badge catégorie : sobre, juste la couleur d'accent.
        self._badge = ElidedLabel(translate(
            f"effects.category.{preset.category.value}"
        ).upper())
        self._badge.setStyleSheet(
            f"QLabel {{ color: {accent_color.name()};"
            f" background: transparent; font-size: {Typography.caption}px;"
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
            f" border-radius: {Radius.pill}px; }}"
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
        suffix = translate("library.word.effect") if count <= 1 else translate("library.word.effect_many")
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
        self.search_field = SearchField(translate("effects.library.search"), object_name="effectsSearch")
        self.search_field.textChanged.connect(self._on_search_changed)
        header_layout.addWidget(self.search_field)

        # --- Filtres catégorie ------------------------------------------
        self.category_row = QWidget()
        # Les onglets de catégorie passent à la ligne : en rangée simple ils étaient écrasés à ~50 px (illisibles).
        cat_layout = FlowLayout(self.category_row, spacing=Spacing.xs)
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
        header_layout.addWidget(self.category_row)
        layout.addWidget(ShrinkableScrollArea(header, Sizes.library_header_min_height))

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
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
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
        self.apply_button = make_wide_button(
            IconName.PLUS, translate("effects.library.apply"), accent=True,
            tooltip=translate("effects.library.apply"),
        )
        self.apply_button.clicked.connect(self._emit_apply_requested)
        actions_layout.addWidget(self.apply_button)

        self.save_button = make_wide_button(
            IconName.SAVE, translate("effects.library.save"), accent=False,
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
            f" font-weight: {Weights.bold}; letter-spacing: 1px;"
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
        layout.setContentsMargins(*DIALOG_MARGINS)
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
        # Entrée valide (le nom), Échap annule ; Tab sort de la description au lieu d'y insérer une tabulation.
        set_single_default(self, buttons.button(QDialogButtonBox.Ok))
        let_tab_leave(self.description_edit)

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
