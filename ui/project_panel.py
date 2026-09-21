from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QListWidget, QVBoxLayout, QWidget


class ProjectPanel(QWidget):
    def __init__(self, load_video, parent=None):
        super().__init__(parent)
        self.setObjectName("project_panel")
        self.setStyleSheet(
            "QWidget#project_panel { background: #181818; border: 1px solid #2d2d2d; border-radius: 12px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QLabel("Projet")
        title.setStyleSheet("color: #e6e6e6; font-weight: 700; font-size: 13px; padding-bottom: 6px;")
        self.bin = QListWidget()
        self.bin.setAcceptDrops(True)
        self.bin.setStyleSheet(
            "QListWidget { background: #1d1d1d; color: white; border: 1px solid #313131; border-radius: 10px; padding: 6px; }"
            "QListWidget::item { padding: 10px 8px; border-radius: 6px; color: #efefef; }"
            "QListWidget::item:selected { background: #2d4d77; color: white; }"
        )
        self.bin.addItem("Glisse tes vidéos ici (.mp4)")
        self.bin.setFixedWidth(250)
        self.bin.itemClicked.connect(load_video)
        layout.addWidget(title)
        layout.addWidget(self.bin)

    def add_file(self, path):
        self.bin.addItem(path)
