"""Widgets de la bibliothèque de médias : subtitle_library."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import COLORS, label_style
from ui.project_panel_widgets.text_presets_view import TextPresetLibraryView


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
        self.text_edit.setPlaceholderText(translate("library.subtitle.placeholder"))
        self.text_edit.setMinimumHeight(72)
        self.text_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 6px; }}"
        )
        editor_layout.addWidget(self.text_edit)

        duration_label = QLabel(translate("library.subtitle.duration"))
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
            tooltip=translate("history.subtitle.add"),
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
            tooltip=translate("library.subtitle.import_tip"),
            size=Sizes.icon_button,
            square=True,
        )
        self.import_button.setMinimumHeight(Sizes.button_md)
        self.import_button.clicked.connect(self.import_requested)
        self.export_button = IconButton(
            icon=IconName.EXPORT,
            tooltip=translate("library.subtitle.export_tip"),
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
