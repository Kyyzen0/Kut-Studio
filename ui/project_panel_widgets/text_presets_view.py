"""Widgets de la bibliothèque de médias : text_presets_view."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from ui.design_system import Radius, Sizes, Spacing, Weights
from ui.i18n import translate
from ui.icons import IconButton, IconName, make_icon
from ui.theme import COLORS, active_palette, label_style
from ui.search_field import SearchField


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
        self.search_field = SearchField(translate("text.library.search"), object_name="textPresetSearch")
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
            f" font-weight: {Weights.bold}; letter-spacing: 1px;"
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
        accent_color = QColor(active_palette().category_colors[3])
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
            f" border-radius: {Radius.pill}px; }}"
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
