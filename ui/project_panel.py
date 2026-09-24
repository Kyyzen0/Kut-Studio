from PySide6.QtCore import Qt
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
    """Bibliothèque de médias du projet courant.

    Le panneau est désormais une simple **vue** sur
    ``self.project.media_assets`` : il ne possède plus sa propre liste
    métier de chemins. La mise à jour est déclenchée par ``MainWindow``
    via ``set_assets``.
    """

    def __init__(
        self,
        on_asset_selected,
        on_add_to_timeline,
        on_import_requested,
        parent=None,
    ):
        super().__init__(parent)
        self.on_asset_selected = on_asset_selected
        self.on_add_to_timeline = on_add_to_timeline
        self.on_import_requested = on_import_requested
        # Mémorise l'identifiant du média actuellement sélectionné dans la
        # bibliothèque. Mis à jour dès que la ligne courante de la liste
        # change (clic utilisateur ou ``setCurrentRow`` programmatique).
        self._selected_asset_id: str | None = None
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

        self.bin = QListWidget()
        self.bin.setAcceptDrops(True)
        self.bin.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 5px; }}"
            f"QListWidget::item {{ padding: 10px 8px; border-radius: 5px; color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
        self.bin.itemClicked.connect(self._on_item_clicked)
        # ``currentRowChanged`` couvre à la fois la sélection par clic
        # utilisateur et la sélection programmatique (``setCurrentRow``).
        self.bin.currentRowChanged.connect(self._on_current_row_changed)

        self.navigation = QListWidget()
        self.navigation.setMinimumHeight(190)
        self.navigation.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.navigation.setSpacing(2)
        self.navigation.addItems(["▣   Médias", "♪   Audio", "T   Texte", "✦   Effets", "◇   Transitions"])
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
        media_title = QLabel("MÉDIAS DU PROJET")
        media_title.setStyleSheet(label_style(10, "muted", 800))
        self.media_count = QLabel("0 média")
        self.media_count.setStyleSheet(label_style(11, "muted", 500))
        media_header.addWidget(media_title)
        media_header.addWidget(self.media_count)
        layout.addLayout(media_header)

        # Conteneur empilé pour les différents contenus de bibliothèque.
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.bin)

        self._audio_placeholder = self._make_placeholder_label(
            "Bibliothèque audio\n\nGlissez vos fichiers .mp3 / .wav ici\n(à implémenter)"
        )
        self._text_placeholder = self._make_placeholder_label(
            "Modèles de texte\n\nSous-titres, titres, call-outs\n(à implémenter)"
        )
        self._effects_placeholder = self._make_placeholder_label(
            "Effets visuels\n\nCouleur, recadrage, filtres\n(à implémenter)"
        )
        self._transitions_placeholder = self._make_placeholder_label(
            "Transitions\n\nFondu, volets, glissements\n(à implémenter)"
        )
        self.content_stack.addWidget(self._audio_placeholder)
        self.content_stack.addWidget(self._text_placeholder)
        self.content_stack.addWidget(self._effects_placeholder)
        self.content_stack.addWidget(self._transitions_placeholder)
        layout.addWidget(self.content_stack)

        self.import_button = QPushButton("+  Importer des médias")
        self.import_button.setStyleSheet(
            f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; padding: 9px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_dark']}; border-color: {COLORS['accent']}; }}"
        )
        self.import_button.clicked.connect(self.on_import_requested)
        layout.addWidget(self.import_button)

        # Bouton « Ajouter à la timeline ». Désactivé tant qu'aucun média
        # n'est sélectionné ; activé automatiquement dès qu'une ligne de
        # la bibliothèque devient la sélection courante (cf. slot
        # ``_on_current_row_changed`` plus bas).
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
        """Reconstruit la liste à partir des ``MediaAsset`` du Project."""
        self.bin.clear()
        for asset in assets:
            item = QListWidgetItem(asset.name)
            # On stocke l'identifiant métier dans le ``UserRole`` pour le
            # retrouver lors d'un clic sans jamais détenir de chemin.
            item.setData(Qt.UserRole, asset.id)
            self.bin.addItem(item)
        self._refresh_count()

    def select_asset(self, asset_id: str) -> None:
        """Sélectionne le média correspondant à ``asset_id`` dans la liste."""
        for row in range(self.bin.count()):
            item = self.bin.item(row)
            if item.data(Qt.UserRole) == asset_id:
                self.bin.setCurrentRow(row)
                return

    def asset_ids(self) -> list[str]:
        """Retourne la liste des identifiants actuellement affichés."""
        return [
            self.bin.item(row).data(Qt.UserRole)
            for row in range(self.bin.count())
        ]

    @property
    def selected_asset_id(self) -> str | None:
        """Identifiant du média actuellement sélectionné, ou ``None``."""
        return self._selected_asset_id

    # ------------------------------------------------------------------
    # Slots internes
    # ------------------------------------------------------------------

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        asset_id = item.data(Qt.UserRole)
        if asset_id is not None:
            self.on_asset_selected(asset_id)

    def _on_current_row_changed(self, row: int) -> None:
        """Met à jour l'état du bouton « Ajouter à la timeline ».

        Centralise l'activation du bouton en fonction de la présence ou
        non d'un média sélectionné dans la bibliothèque. Toute la logique
        dépend uniquement de l'état de la ``QListWidget`` ; aucun chemin
        d'appel ne stocke de valeur dérivée.
        """
        has_selection = 0 <= row < self.bin.count()
        self._selected_asset_id = (
            self.bin.item(row).data(Qt.UserRole) if has_selection else None
        )
        self.add_to_timeline_button.setEnabled(has_selection)

    def _on_add_to_timeline_clicked(self) -> None:
        """Transmet au ``MainWindow`` l'identifiant du média sélectionné.

        Le bouton est désactivé tant qu'aucun média n'est sélectionné
        (cf. ``_on_current_row_changed``). Ce slot ne fait rien si la
        sélection a été perdue entre-temps (sécurité défensive).
        """
        if self._selected_asset_id is None:
            self.add_to_timeline_button.setEnabled(False)
            return
        self.on_add_to_timeline(self._selected_asset_id)

    def _refresh_count(self) -> None:
        count = self.bin.count()
        if count <= 1:
            self.media_count.setText(f"{count} média")
        else:
            self.media_count.setText(f"{count} médias")

    def _make_placeholder_label(self, message):
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(label_style(13, "muted", 500))
        return label

    def on_tab_changed(self, index):
        self.content_stack.setCurrentIndex(index)
