"""Bibliothèque de préréglages d'effets audio (section Audio du panneau).

Équivalent audio de :class:`ui.project_panel.EffectsLibraryView` : recherche,
filtres par catégorie, cartes de préréglages intégrés et utilisateur, et
un bouton « Appliquer au clip » dont le clip cible est résolu par
``MainWindow``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.audio_effects_library import (
    CATEGORY_LABELS,
    AudioEffectPreset,
    AudioEffectPresetCategory,
    builtin_audio_effect_presets,
    filter_audio_effect_presets,
)
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName
from ui.theme import COLORS, label_style


class AudioEffectPresetCard(QFrame):
    """Carte compacte d'un préréglage audio ; un clic le sélectionne."""

    clicked = Signal(str)

    CARD_HEIGHT = 64

    def __init__(
        self, preset: AudioEffectPreset, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("audioEffectPresetCard")
        self.preset_id = preset.id
        self._selected = False
        accent = COLORS["track_audio"]
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.CARD_HEIGHT)
        self._base_style = self._style(COLORS["panel_alt"], COLORS["border"], accent)
        self._hover_style = self._style(
            COLORS["surface_hover"], COLORS["border_strong"], accent
        )
        self._selected_style = self._style(
            COLORS["accent_dark"], COLORS["accent"], accent
        )
        self.setStyleSheet(self._base_style)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, 6, Spacing.sm, 6)
        layout.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(Spacing.xs)
        title = QLabel(preset.name)
        title.setStyleSheet(label_style(12, "text", 700))
        title_row.addWidget(title)
        badge = QLabel(CATEGORY_LABELS[preset.category].upper())
        badge.setStyleSheet(
            f"QLabel {{ color: {accent}; background: transparent;"
            f" font-size: 9px; font-weight: 700; padding: 0; }}"
        )
        title_row.addWidget(badge)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        description = preset.description or ""
        if len(description) > 90:
            description = description[:89].rstrip() + "…"
        label = QLabel(description)
        label.setWordWrap(True)
        label.setStyleSheet(label_style(10, "muted", 500))
        layout.addWidget(label)

    @staticmethod
    def _style(background: str, border: str, accent: str) -> str:
        return (
            f"QFrame#audioEffectPresetCard {{ background: {background};"
            f" border: 1px solid {border}; border-left: 3px solid {accent};"
            f" border-radius: 8px; }}"
        )

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
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


class AudioEffectsLibraryView(QWidget):
    """Recherche, catégories et préréglages d'effets audio."""

    apply_requested = Signal(str)  # preset_id (clip résolu par MainWindow)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._builtin_presets: list[AudioEffectPreset] = list(
            builtin_audio_effect_presets()
        )
        self._user_presets: list[AudioEffectPreset] = []
        self._cards: dict[str, AudioEffectPresetCard] = {}
        self._selected_preset_id: str | None = None
        self._has_audio_clip = False
        self._search_text = ""
        self._active_category: AudioEffectPresetCategory | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, 0)
        header_layout.setSpacing(Spacing.xs)
        self.search_field = QLineEdit()
        self.search_field.setObjectName("audioEffectsSearch")
        self.search_field.setPlaceholderText("Rechercher un effet audio…")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(28)
        self.search_field.textChanged.connect(self._on_search_changed)
        header_layout.addWidget(self.search_field)

        category_row = QWidget()
        cat_layout = QHBoxLayout(category_row)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(Spacing.xs)
        self.category_buttons: list[QPushButton] = []
        self._category_buttons_data: list[AudioEffectPresetCategory | None] = []
        self._add_category_button(cat_layout, "TOUS", None)
        for category in AudioEffectPresetCategory:
            self._add_category_button(
                cat_layout, CATEGORY_LABELS[category].upper(), category
            )
        self.category_buttons[0].setChecked(True)
        cat_layout.addStretch(1)
        header_layout.addWidget(category_row)
        layout.addWidget(header)

        self.clip_hint = QLabel(
            "Sélectionnez un clip audio ou vidéo, puis appliquez un préréglage."
        )
        self.clip_hint.setWordWrap(True)
        self.clip_hint.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; padding: 4px 6px;"
            f" background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px;"
        )
        hint_container = QWidget()
        hint_layout = QHBoxLayout(hint_container)
        hint_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        hint_layout.addWidget(self.clip_hint)
        layout.addWidget(hint_container)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("audioEffectsScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(Spacing.sm, 0, Spacing.sm, 0)
        self.cards_layout.setSpacing(Spacing.xs)
        self.cards_layout.addStretch(1)
        self.scroll_area.setWidget(self.cards_host)
        layout.addWidget(self.scroll_area, 1)

        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(
            Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm
        )
        self.apply_button = IconButton(
            icon=IconName.PLUS,
            tooltip="Appliquer le préréglage au clip sélectionné",
            size=Sizes.icon_button,
            accent=True,
            square=False,
        )
        self.apply_button.setText("  Appliquer au clip")
        self.apply_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.apply_button.setMinimumHeight(Sizes.button_md)
        self.apply_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.apply_button.clicked.connect(self._emit_apply_requested)
        actions_layout.addWidget(self.apply_button)
        layout.addWidget(actions)

        self._rebuild_cards()

    # ----- API publique -----------------------------------------------

    def set_user_presets(self, presets: list) -> None:
        self._user_presets = list(presets or [])
        self._rebuild_cards()

    def set_clip_context(self, *, has_audio_clip: bool) -> None:
        """Active l'action quand un clip portant de l'audio est sélectionné."""
        self._has_audio_clip = has_audio_clip
        self._refresh_buttons()

    def selected_preset_id(self) -> str | None:
        return self._selected_preset_id

    def select_preset(self, preset_id: str) -> bool:
        if preset_id not in self._cards:
            return False
        self._select_preset(preset_id)
        return True

    def preset_count(self) -> int:
        return len(self._builtin_presets) + len(self._user_presets)

    # ----- Filtres -----------------------------------------------------

    def _add_category_button(
        self,
        layout: QHBoxLayout,
        label: str,
        category: AudioEffectPresetCategory | None,
    ) -> None:
        button = QPushButton(label)
        button.setObjectName("audioEffectsCategoryTab")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            f"QPushButton#audioEffectsCategoryTab {{ background: transparent;"
            f" color: {COLORS['muted']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 3px 8px; font-weight: 600;"
            f" font-size: 10px; letter-spacing: 0.4px; }}"
            f"QPushButton#audioEffectsCategoryTab:hover {{"
            f" color: {COLORS['text']}; background: {COLORS['surface_hover']}; }}"
            f"QPushButton#audioEffectsCategoryTab:checked {{"
            f" color: {COLORS['accent']}; background: {COLORS['accent_dark']};"
            f" border: 1px solid {COLORS['accent']}; }}"
        )
        button.clicked.connect(
            lambda _checked=False, cat=category: self._select_category(cat)
        )
        self.category_buttons.append(button)
        self._category_buttons_data.append(category)
        layout.addWidget(button)

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip()
        self._rebuild_cards()

    def _select_category(self, category: AudioEffectPresetCategory | None) -> None:
        self._active_category = category
        index = self._category_buttons_data.index(category)
        for position, button in enumerate(self.category_buttons):
            button.setChecked(position == index)
        self._rebuild_cards()

    # ----- Cartes ------------------------------------------------------

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

        visible = filter_audio_effect_presets(
            self._builtin_presets + self._user_presets,
            search=self._search_text,
            category=self._active_category,
        )
        builtin_visible = [p for p in visible if p.builtin]
        user_visible = [p for p in visible if not p.builtin]
        if not visible:
            empty = QLabel("Aucun préréglage ne correspond à la recherche.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 11px; padding: 16px 8px;"
            )
            self.cards_layout.insertWidget(0, empty)
        else:
            self._render_section("Préréglages", builtin_visible)
            self._render_section("Mes presets", user_visible)

        ids = list(self._cards)
        if self._selected_preset_id in ids:
            self._apply_selection(self._selected_preset_id)
        elif ids:
            self._select_preset(ids[0])
        else:
            self._selected_preset_id = None
        self._refresh_buttons()

    def _render_section(
        self, title: str, presets: list[AudioEffectPreset]
    ) -> None:
        if not presets:
            return
        label = QLabel(title.upper())
        label.setStyleSheet(
            f"color: {COLORS['muted_strong']}; font-size: 10px;"
            f" font-weight: 800; letter-spacing: 1px;"
            f" padding: 6px 2px 2px 2px;"
        )
        self._insert(label)
        for preset in presets:
            card = AudioEffectPresetCard(preset, parent=self.cards_host)
            card.clicked.connect(self._select_preset)
            self._cards[preset.id] = card
            self._insert(card)

    def _insert(self, widget: QWidget) -> None:
        # Garde le stretch final en dernière position.
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, widget)

    # ----- Sélection et actions ----------------------------------------

    def _select_preset(self, preset_id: str) -> None:
        self._selected_preset_id = preset_id
        self._apply_selection(preset_id)
        self._refresh_buttons()

    def _apply_selection(self, preset_id: str | None) -> None:
        for pid, card in self._cards.items():
            card.set_selected(pid == preset_id)

    def _refresh_buttons(self) -> None:
        self.apply_button.setEnabled(
            self._has_audio_clip and self._selected_preset_id is not None
        )

    def _emit_apply_requested(self) -> None:
        if self._has_audio_clip and self._selected_preset_id:
            self.apply_requested.emit(self._selected_preset_id)
