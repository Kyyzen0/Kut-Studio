from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFileDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import COLORS, label_style


class ProjectPanel(QWidget):
    def __init__(self, load_video, parent=None):
        super().__init__(parent)
        self.load_video_callback = load_video
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
        self.navigation.addItems(["▣   Médias", "♪   Audio", "T   Texte", "✦   Effets", "◇   Transitions"])
        self.navigation.setCurrentRow(0)
        self.navigation.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ color: {COLORS['muted']}; padding: 9px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
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

        self.bin = QListWidget()
        self.bin.setAcceptDrops(True)
        self.bin.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 5px; }}"
            f"QListWidget::item {{ padding: 10px 8px; border-radius: 5px; color: {COLORS['muted']}; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']}; color: {COLORS['text']}; }}"
        )
        self.bin.itemClicked.connect(load_video)
        layout.addWidget(self.bin)

        self.import_button = QPushButton("+  Importer des médias")
        self.import_button.setStyleSheet(
            f"QPushButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; padding: 9px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_dark']}; border-color: {COLORS['accent']}; }}"
        )
        self.import_button.clicked.connect(self.import_media)
        layout.addWidget(self.import_button)

    def import_media(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Importer des médias",
            "",
            "Vidéos (*.mp4 *.mov *.avi);;Tous les fichiers (*)",
        )
        for path in paths:
            self.add_file(path)
        if paths:
            self.bin.setCurrentRow(self.bin.count() - 1)
            self.load_video_callback(self.bin.currentItem())

    def add_file(self, path):
        self.bin.addItem(path)
        self.media_count.setText(f"{self.bin.count()} média" if self.bin.count() == 1 else f"{self.bin.count()} médias")
