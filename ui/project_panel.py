"""Panneau bibliothèque de Kut-Studio.

Refonte UI/UX :

- onglets avec icônes SVG cohérentes (Médias, Audio, Texte, Effets,
  Transitions) ;
- regroupement logique : titre de section, compteur, liste, boutons ;
- boutons d'action plus grands et plus clairs (avec icônes) ;
- suppression totale des emojis de l'interface.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QMimeData, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MediaAsset
from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconButton, IconLabel, IconName, make_icon
from ui.theme import COLORS, label_style


# Mapping onglet → icône.
_TAB_ICONS: dict[int, IconName] = {
    0: IconName.MEDIA,
    1: IconName.AUDIO,
    2: IconName.SUBTITLE,
    3: IconName.EFFECTS,
    4: IconName.TRANSITIONS,
}


class ProjectPanel(QWidget):
    """Bibliothèque de médias et de sous-titres du projet courant."""

    asset_selected = Signal(str)
    add_to_timeline_requested = Signal(str)
    import_requested = Signal()
    add_subtitle_requested = Signal(str, float)
    import_subtitles_requested = Signal()
    export_subtitles_requested = Signal()
    subtitle_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("project_panel")
        self.setStyleSheet(
            f"QWidget#project_panel {{ background: {COLORS['panel']}; "
            f"border-right: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        layout.setSpacing(Spacing.sm)

        # ----- Titre de la bibliothèque ---------------------------------
        title = QLabel("BIBLIOTHÈQUE")
        title.setStyleSheet(label_style(11, "muted", 800))
        layout.addWidget(title)

        # ----- Onglets de navigation (avec icônes) ---------------------
        self.navigation = QListWidget()
        # Cinq onglets doivent tenir sans défilement : la hauteur est
        # dimensionnée sur la hauteur réelle d'un item (voir le padding
        # appliqué plus bas), pas sur une valeur arbitraire.
        self.navigation.setMinimumHeight(92)
        self.navigation.setFixedHeight(154)
        self.navigation.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setSpacing(1)
        for index in range(5):
            item = QListWidgetItem()
            self.navigation.addItem(item)
        self.navigation.setCurrentRow(0)
        self.navigation.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ color: {COLORS['muted']}; padding: 5px 8px; "
            f"border-radius: 6px; font-size: 12px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; "
            f"color: {COLORS['text']}; }}"
        )
        # Stocke les widgets icône associés pour les rafraîchir ensemble.
        self._tab_icons: list[IconLabel] = []
        for row, icon_name in _TAB_ICONS.items():
            widget = self.navigation.itemWidget(self.navigation.item(row))
            label = IconLabel(icon_name, size=Iconography.md)
            self._tab_icons.append(label)
        # Les QListWidgetItem n'hébergent pas de widget custom par défaut ;
        # on préfère injecter l'icône via le mécanisme de décoration Qt
        # (setIcon). Cela reste cohérent avec un style compact.
        for row, icon_name in _TAB_ICONS.items():
            item = self.navigation.item(row)
            item.setIcon(make_icon(icon_name, size=Iconography.md))

        # Labels FR pour les onglets.
        self._tab_labels = ("Médias", "Audio", "Texte", "Effets", "Transitions")
        for row, text in enumerate(self._tab_labels):
            item = self.navigation.item(row)
            item.setText(f"   {text}")
        self.navigation.currentRowChanged.connect(self.on_tab_changed)
        layout.addWidget(self.navigation)

        # ----- Titre de la section courante -----------------------------
        # Une seule ligne (titre à gauche, compteur à droite) : gagne une
        # ligne verticale, dont la place est comptée quand la fenêtre est
        # basse.
        media_header = QWidget()
        media_header_layout = QHBoxLayout(media_header)
        media_header_layout.setContentsMargins(0, 0, 0, 0)
        media_header_layout.setSpacing(Spacing.sm)
        self.media_title = QLabel("MÉDIAS DU PROJET")
        self.media_title.setStyleSheet(label_style(10, "muted", 800))
        self.media_count = QLabel("0 média")
        self.media_count.setStyleSheet(label_style(10, "muted", 500))
        media_header_layout.addWidget(self.media_title)
        media_header_layout.addStretch(1)
        media_header_layout.addWidget(self.media_count)
        layout.addWidget(media_header)

        # ----- Contenu empilé -------------------------------------------
        # La pile est la seule zone réellement élastique du panneau : on
        # lui impose un minimum faible pour que la colonne centrale
        # puisse agrandir la timeline au lieu d'être bloquée ici.
        self.content_stack = QStackedWidget()
        self.content_stack.setMinimumHeight(60)
        self.content_stack.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self.bin_videos = self._make_bin()
        self.content_stack.addWidget(self.bin_videos)

        self.bin_audios = self._make_bin()
        self.content_stack.addWidget(self.bin_audios)

        self.subtitle_view = SubtitleLibraryView(self)
        self.content_stack.addWidget(self.subtitle_view)

        self._effects_placeholder = self._make_placeholder_label(
            "Effets visuels\n\nCouleur, recadrage, filtres\n(à implémenter)"
        )
        self._transitions_placeholder = self._make_placeholder_label(
            "Transitions\n\nFondu, volets, glissements\n(à implémenter)"
        )
        self.content_stack.addWidget(self._effects_placeholder)
        self.content_stack.addWidget(self._transitions_placeholder)
        layout.addWidget(self.content_stack, 1)

        # ----- Boutons d'action principaux -----------------------------
        self.import_button = self._make_wide_button(
            IconName.IMPORT, "Importer des médias",
            accent=False,
            tooltip="Importer des médias dans le projet",
        )
        self.import_button.clicked.connect(self.import_requested)
        layout.addWidget(self.import_button)

        self.add_to_timeline_button = self._make_wide_button(
            IconName.PLUS, "Ajouter à la timeline",
            accent=True,
            tooltip="Ajouter le média sélectionné à la timeline",
        )
        self.add_to_timeline_button.setEnabled(False)
        self.add_to_timeline_button.clicked.connect(self._on_add_to_timeline_clicked)
        layout.addWidget(self.add_to_timeline_button)

        # Câblage des signaux du composant Sous-titres.
        self.subtitle_view.add_requested.connect(self._on_add_subtitle_clicked)
        self.subtitle_view.import_requested.connect(self.import_subtitles_requested)
        self.subtitle_view.export_requested.connect(self.export_subtitles_requested)
        self.subtitle_view.selected.connect(self.subtitle_selected)

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
        if self.navigation.currentRow() == 2:
            self._refresh_count()

    def select_asset(self, asset_id: str) -> None:
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                item = bin_widget.item(row)
                if item.data(Qt.UserRole) == asset_id:
                    self.navigation.setCurrentRow(
                        0 if bin_widget is self.bin_videos else 1
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
    # Slots internes
    # ------------------------------------------------------------------

    def on_tab_changed(self, index: int) -> None:
        self.content_stack.setCurrentIndex(index)
        self._refresh_count()
        self._sync_add_button_for_active_tab()

    # ------------------------------------------------------------------
    # Helpers privés
    # ------------------------------------------------------------------

    def _make_bin(self) -> "AssetBin":
        return AssetBin(
            on_item_clicked=lambda asset_id: self.asset_selected.emit(asset_id),
            on_selection_changed=lambda asset_id: self._on_bin_selection_changed(asset_id),
        )

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
        if self.navigation.currentRow() == 0:
            count = self.bin_videos.count()
            label_word = "média" if count <= 1 else "médias"
            self.media_title.setText("MÉDIAS DU PROJET")
        elif self.navigation.currentRow() == 1:
            count = self.bin_audios.count()
            label_word = "audio" if count <= 1 else "audios"
            self.media_title.setText("AUDIOS DU PROJET")
        elif self.navigation.currentRow() == 2:
            count = self.subtitle_view.count()
            label_word = "sous-titre" if count <= 1 else "sous-titres"
            self.media_title.setText("SOUS-TITRES DU PROJET")
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
    """Sous-widget : liste filtrée d'assets avec son propre état de sélection."""

    def __init__(self, on_item_clicked, on_selection_changed, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._list = QListWidget()
        self._list.setAcceptDrops(False)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 5px; }}"
            f"QListWidget::item {{ padding: 10px 8px; border-radius: 4px; "
            f"color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; "
            f"color: {COLORS['text']}; }}"
        )
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

    def add_asset(self, asset: MediaAsset) -> None:
        item = QListWidgetItem(asset.name)
        item.setData(Qt.UserRole, asset.id)
        self._list.addItem(item)

    def clear(self) -> None:
        self._list.clear()

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
