import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, 
                               QHBoxLayout, QVBoxLayout, QListWidget, 
                               QLabel, QSplitter, QPushButton)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl

class PremiereSimple(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Premiere Simple - v0.1")
        self.setGeometry(100, 100, 1200, 700)

        # Player
        self.player = QMediaPlayer()
        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)

        # GAUCHE - Bin / Fichiers importés
        self.bin = QListWidget()
        self.bin.setAcceptDrops(True)
        self.bin.setStyleSheet("background: #1e1e1e; color: white;")
        self.bin.addItem("Glisse tes vidéos ici (.mp4)")
        self.bin.setFixedWidth(250)
        self.bin.itemClicked.connect(self.load_video)

        # CENTRE - Viewer
        viewer_layout = QVBoxLayout()
        viewer_layout.addWidget(self.video_widget)
        
        controls = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play / Pause")
        self.btn_play.clicked.connect(self.toggle_play)
        controls.addWidget(self.btn_play)
        viewer_layout.addLayout(controls)
        
        viewer_container = QWidget()
        viewer_container.setLayout(viewer_layout)

        # BAS - Timeline (vide pour l'instant)
        self.timeline = QLabel("TIMELINE v0.1 - On va la construire après")
        self.timeline.setStyleSheet("background: #2a2a2a; color: #888; border-top: 2px solid #444;")
        self.timeline.setFixedHeight(150)
        self.timeline.setAlignment(Qt.AlignCenter)

        # Layout principal
        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(self.bin)
        top_split.addWidget(viewer_container)

        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline)

        self.setCentralWidget(main_split)
        self.setStyleSheet("QMainWindow { background: #121212; }")

    def load_video(self, item):
        path = item.text()
        if path.endswith((".mp4", ".mov", ".avi")):
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            self.bin.addItem(url.toLocalFile())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PremiereSimple()
    window.show()
    sys.exit(app.exec())