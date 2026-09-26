"""Panneau bibliothèque de Kut-Studio.

Ce widget est une simple **vue** sur ``self.project.media_assets`` : il
ne possède plus sa propre liste métier de chemins. La mise à jour est
déclenchée par ``MainWindow`` via ``set_assets``.

L'onglet « Médias » liste les ``MediaAsset`` de type ``video`` ; l'onglet
« Audio » liste ceux de type ``audio``. Les autres onglets restent
des placeholders historiques (Texte, Effets, Transitions).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MediaAsset
from ui.theme import COLORS, label_style


class ProjectPanel(QWidget):
    """Bibliothèque de médias du projet courant."""

    asset_selected = Signal(str)
    """Émet l'identifiant du média sélectionné dans la bibliothèque visible."""

    add_to_timeline_requested = Signal(str)
    """Émet l'identifiant du média à ajouter à la timeline."""

    import_requested = Signal()
    """Émis lorsque l'utilisateur clique sur « Importer des médias »."""

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

        # Contenu empilé : onglets Médias / Audio (vrais) puis placeholders.
        self.content_stack = QStackedWidget()

        # Onglet Médias : liste les assets vidéo.
        self.bin_videos = self._make_bin()
        self.content_stack.addWidget(self.bin_videos)

        # Onglet Audio : liste les assets audio.
        self.bin_audios = self._make_bin()
        self.content_stack.addWidget(self.bin_audios)

        # Placeholders historiques pour Texte / Effets / Transitions.
        self._text_placeholder = self._make_placeholder_label(
            "Modèles de texte\n\nSous-titres, titres, call-outs\n(à implémenter)"
        )
        self._effects_placeholder = self._make_placeholder_label(
            "Effets visuels\n\nCouleur, recadrage, filtres\n(à implémenter)"
        )
        self._transitions_placeholder = self._make_placeholder_label(
            "Transitions\n\nFondu, volets, glissements\n(à implémenter)"
        )
        self.content_stack.addWidget(self._text_placeholder)
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

    def _refresh_count(self) -> None:
        if self.navigation.currentRow() == 0:
            count = self.bin_videos.count()
            label_word = "média" if count <= 1 else "médias"
            self.media_title.setText("MÉDIAS DU PROJET")
        elif self.navigation.currentRow() == 1:
            count = self.bin_audios.count()
            label_word = "audio" if count <= 1 else "audios"
            self.media_title.setText("AUDIOS DU PROJET")
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