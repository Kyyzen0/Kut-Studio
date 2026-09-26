"""Panneau bibliothèque de Kut-Studio.

Ce widget est une simple **vue** sur ``self.project.media_assets`` : il
ne possède plus sa propre liste métier de chemins. La mise à jour est
déclenchée par ``MainWindow`` via ``set_assets``.

L'onglet « Médias » liste les ``MediaAsset`` de type ``video`` ; l'onglet
« Audio » liste ceux de type ``audio``. L'onglet « Texte » est une
vraie bibliothèque de sous-titres, synchronisée avec la timeline.
Les autres onglets restent des placeholders historiques (Effets,
Transitions).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MediaAsset
from ui.theme import COLORS, label_style


class ProjectPanel(QWidget):
    """Bibliothèque de médias et de sous-titres du projet courant."""

    asset_selected = Signal(str)
    """Émet l'identifiant du média sélectionné dans la bibliothèque visible."""

    add_to_timeline_requested = Signal(str)
    """Émet l'identifiant du média à ajouter à la timeline."""

    import_requested = Signal()
    """Émis lorsque l'utilisateur clique sur « Importer des médias »."""

    add_subtitle_requested = Signal(str, float)
    """Émis lors d'un ajout de sous-titre : (texte, durée)."""

    import_subtitles_requested = Signal()
    """Émis lors d'un clic sur « Importer un SRT »."""

    export_subtitles_requested = Signal()
    """Émis lors d'un clic sur « Exporter les sous-titres SRT »."""

    subtitle_selected = Signal(str)
    """Émis avec l'identifiant d'un clip de sous-titre sélectionné."""

    def __init__(
        self,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("project_panel")
        self.setStyleSheet(
            f"QWidget#project_panel {{ background: {COLORS['panel']}; border-right: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(10)

        title = QLabel("BIBLIOTHÈQUE")
        title.setStyleSheet(label_style(10, "muted", 800))
        layout.addWidget(title)

        self.navigation = QListWidget()
        self.navigation.setMinimumHeight(190)
        self.navigation.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.navigation.setSpacing(2)
        self.navigation.addItems([
            "▣   Médias",
            "♪   Audio",
            "T   Texte",
            "✦   Effets",
            "◇   Transitions",
        ])
        self.navigation.setCurrentRow(0)
        self.navigation.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ color: {COLORS['muted']}; padding: 9px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
        self.navigation.currentRowChanged.connect(self.on_tab_changed)
        layout.addWidget(self.navigation)

        media_header = QVBoxLayout()
        media_header.setSpacing(3)
        self.media_title = QLabel("MÉDIAS DU PROJET")
        self.media_title.setStyleSheet(label_style(10, "muted", 800))
        self.media_count = QLabel("0 média")
        self.media_count.setStyleSheet(label_style(11, "muted", 500))
        media_header.addWidget(self.media_title)
        media_header.addWidget(self.media_count)
        layout.addLayout(media_header)

        # Contenu empilé : onglets Médias / Audio / Texte (vrais) puis placeholders.
        self.content_stack = QStackedWidget()

        # Onglet Médias : liste les assets vidéo.
        self.bin_videos = self._make_bin()
        self.content_stack.addWidget(self.bin_videos)

        # Onglet Audio : liste les assets audio.
        self.bin_audios = self._make_bin()
        self.content_stack.addWidget(self.bin_audios)

        # Onglet Texte : vraie bibliothèque de sous-titres.
        self.subtitle_view = SubtitleLibraryView(self)
        self.content_stack.addWidget(self.subtitle_view)

        # Placeholders historiques pour Effets / Transitions.
        self._effects_placeholder = self._make_placeholder_label(
            "Effets visuels\n\nCouleur, recadrage, filtres\n(à implémenter)"
        )
        self._transitions_placeholder = self._make_placeholder_label(
            "Transitions\n\nFondu, volets, glissements\n(à implémenter)"
        )
        self.content_stack.addWidget(self._effects_placeholder)
        self.content_stack.addWidget(self._transitions_placeholder)
        layout.addWidget(self.content_stack)

        self.import_button = QPushButton("+  Importer des médias")
        self.import_button.setStyleSheet(
            f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; padding: 9px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_dark']}; border-color: {COLORS['accent']}; }}"
        )
        self.import_button.clicked.connect(self.import_requested)
        layout.addWidget(self.import_button)

        # Bouton « Ajouter à la timeline ». Désactivé tant qu'aucun média
        # n'est sélectionné ; activé automatiquement dès qu'une ligne de
        # la bibliothèque devient la sélection courante.
        self.add_to_timeline_button = QPushButton("+  Ajouter à la timeline")
        self.add_to_timeline_button.setEnabled(False)
        self.add_to_timeline_button.setStyleSheet(
            f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['accent_dark']}; border: 1px solid {COLORS['accent']}; padding: 9px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['accent']}; }}"
            f"QPushButton:disabled {{ color: #626875; background: {COLORS['panel_alt']}; border-color: {COLORS['border']}; font-weight: 400; }}"
        )
        self.add_to_timeline_button.clicked.connect(self._on_add_to_timeline_clicked)
        layout.addWidget(self.add_to_timeline_button)

        # Câblage des signaux du composant Sous-titres.
        self.subtitle_view.add_requested.connect(self._on_add_subtitle_clicked)
        self.subtitle_view.import_requested.connect(self.import_subtitles_requested)
        self.subtitle_view.export_requested.connect(self.export_subtitles_requested)
        self.subtitle_view.selected.connect(self.subtitle_selected)

    # ------------------------------------------------------------------
    # API publique (vue sur le Project)
    # ------------------------------------------------------------------

    def set_assets(self, assets: list[MediaAsset]) -> None:
        """Reconstruit les listes à partir des ``MediaAsset`` du Project."""
        videos = [a for a in assets if a.media_type == "video"]
        audios = [a for a in assets if a.media_type == "audio"]
        self._populate_bin(self.bin_videos, videos)
        self._populate_bin(self.bin_audios, audios)
        self._refresh_count()
        # Si la sélection courante n'existe plus après le refresh, on
        # désactive le bouton d'ajout.
        self._sync_add_button_for_active_tab()

    def set_subtitle_clips(self, clips: list) -> None:
        """Met à jour la bibliothèque de sous-titres de l'onglet Texte.

        ``clips`` est une liste de :class:`~core.project_model.Clip`
        activés (déjà filtrés) ; on les affiche triés par
        ``timeline_start``.
        """
        sorted_clips = sorted(clips, key=lambda c: c.timeline_start)
        self.subtitle_view.set_clips(sorted_clips)
        if self.navigation.currentRow() == 2:
            self._refresh_count()

    def select_asset(self, asset_id: str) -> None:
        """Sélectionne le média dans l'onglet correspondant à son type."""
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                item = bin_widget.item(row)
                if item.data(Qt.UserRole) == asset_id:
                    # Bascule l'onglet pour rendre la sélection visible.
                    self.navigation.setCurrentRow(
                        0 if bin_widget is self.bin_videos else 1
                    )
                    bin_widget.setCurrentRow(row)
                    return

    def asset_ids(self) -> list[str]:
        """Retourne les identifiants des assets présents dans la bibliothèque."""
        ids: list[str] = []
        for bin_widget in (self.bin_videos, self.bin_audios):
            for row in range(bin_widget.count()):
                ids.append(bin_widget.item(row).data(Qt.UserRole))
        return ids

    @property
    def selected_asset_id(self) -> str | None:
        """Identifiant du média actuellement sélectionné, ou ``None``."""
        return self._current_bin().selected_asset_id

    @property
    def selected_media_type(self) -> str | None:
        """Type du média actuellement sélectionné (``"video"`` ou ``"audio"``)."""
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
            on_selection_changed=lambda asset_id: self._on_bin_selection_changed(
                asset_id
            ),
        )

    def _populate_bin(self, bin_widget: "AssetBin", assets: list[MediaAsset]) -> None:
        bin_widget.clear()
        for asset in assets:
            bin_widget.add_asset(asset)

    def _current_bin(self) -> "AssetBin":
        widget = self.content_stack.currentWidget()
        if isinstance(widget, AssetBin):
            return widget
        return self.bin_videos  # Fallback : aucun asset n'est sélectionnable.

    def _on_bin_selection_changed(self, asset_id: str | None) -> None:
        self.add_to_timeline_button.setEnabled(asset_id is not None)

    def _sync_add_button_for_active_tab(self) -> None:
        """Aligne l'état du bouton d'ajout sur la sélection courante."""
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
        """Slot interne relayant l'ajout d'un sous-titre au MainWindow."""
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

    def __init__(
        self,
        on_item_clicked,
        on_selection_changed,
        parent=None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._list = QListWidget()
        self._list.setAcceptDrops(False)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 5px; }}"
            f"QListWidget::item {{ padding: 10px 8px; border-radius: 5px; color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
        self._list.itemClicked.connect(
            lambda item: on_item_clicked(item.data(Qt.UserRole))
        )
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
        """Retourne l'item de la ligne ``row`` (compat ``QListWidget``)."""
        return self._list.item(row)

    def setCurrentRow(self, row: int) -> None:
        """Sélectionne la ligne ``row`` (compat ``QListWidget``)."""
        self._list.setCurrentRow(row)

    def currentRow(self) -> int:
        """Retourne la ligne courante (compat ``QListWidget``)."""
        return self._list.currentRow()

    @property
    def selected_asset_id(self) -> str | None:
        row = self._list.currentRow()
        if not (0 <= row < self._list.count()):
            return None
        return self._list.item(row).data(Qt.UserRole)


# ---------------------------------------------------------------------------
# Bibliothèque de sous-titres
# ---------------------------------------------------------------------------


class SubtitleLibraryView(QWidget):
    """Sous-panneau « Texte » : édition + bibliothèque des sous-titres du projet."""

    add_requested = Signal(str, float)
    """Émis lors d'un clic sur « Ajouter » : (texte, durée)."""

    import_requested = Signal()
    """Émis lors d'un clic sur « Importer un SRT »."""

    export_requested = Signal()
    """Émis lors d'un clic sur « Exporter les sous-titres SRT »."""

    selected = Signal(str)
    """Émis avec l'identifiant d'un clip de sous-titre sélectionné."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Éditeur : champ texte multi-ligne.
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Texte du sous-titre…\nVous pouvez écrire sur plusieurs lignes."
        )
        self.text_edit.setFixedHeight(110)
        self.text_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 6px; }}"
        )
        layout.addWidget(self.text_edit)

        # Durée éditable, par défaut 3 s.
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
            f"QDoubleSpinBox {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}"
        )
        duration_row.addWidget(self.duration_spin)
        layout.addLayout(duration_row)

        # Bouton « Ajouter un sous-titre ».
        self.add_button = QPushButton("+  Ajouter un sous-titre")
        self.add_button.setStyleSheet(
            f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['accent_dark']}; border: 1px solid {COLORS['accent']}; padding: 8px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['accent']}; }}"
        )
        self.add_button.clicked.connect(self._emit_add_requested)
        layout.addWidget(self.add_button)

        # Ligne des boutons Importer / Exporter.
        io_row = QHBoxLayout()
        io_row.setSpacing(6)
        self.import_button = QPushButton("Importer un SRT")
        self.export_button = QPushButton("Exporter les sous-titres SRT")
        for button in (self.import_button, self.export_button):
            button.setStyleSheet(
                f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; padding: 6px; }}"
                f"QPushButton:hover {{ background: {COLORS['accent_dark']}; border-color: {COLORS['accent']}; }}"
            )
        self.import_button.clicked.connect(self.import_requested)
        self.export_button.clicked.connect(self.export_requested)
        io_row.addWidget(self.import_button)
        io_row.addWidget(self.export_button)
        layout.addLayout(io_row)

        # Liste des sous-titres existants.
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 6px; border-radius: 4px; color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_clips(self, clips: list) -> None:
        """Affiche les sous-titres existants (déjà triés par l'appelant)."""
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
        """Sélectionne le clip correspondant à ``clip_id`` s'il existe."""
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.UserRole) == clip_id:
                self.list_widget.setCurrentRow(row)
                return

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    @staticmethod
    def _format_label(clip) -> str:
        """Formate l'affichage d'un sous-titre dans la liste."""
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
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        clip_id = current.data(Qt.UserRole)
        if clip_id is not None:
            self.selected.emit(clip_id)