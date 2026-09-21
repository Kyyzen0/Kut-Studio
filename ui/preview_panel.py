from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QGraphicsColorizeEffect,
)


class PreviewPanel(QWidget):
    def __init__(self, toggle_play, stop_playback, seek_relative, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)

        self.color_effect = QGraphicsColorizeEffect(self.video_widget)
        self.color_effect.setColor(QColor("#ffffff"))
        self.color_effect.setStrength(0.0)
        self.video_widget.setGraphicsEffect(self.color_effect)

        self.preview_transition_overlay = QLabel("Fondu enchaîné · 0.5 s")
        self.preview_transition_overlay.setAlignment(Qt.AlignCenter)
        self.preview_transition_overlay.setStyleSheet(
            "background: rgba(20, 20, 20, 210); color: #f7c948; border: 1px solid #f7c948;"
            "border-radius: 8px; padding: 8px 14px; font-weight: 700;"
        )
        self.preview_transition_overlay.hide()

        self.preview_subtitle_overlay = QLabel()
        self.preview_subtitle_overlay.setAlignment(Qt.AlignCenter)
        self.preview_subtitle_overlay.setWordWrap(True)
        self.preview_subtitle_overlay.setStyleSheet(
            "background: rgba(0, 0, 0, 190); color: white; border-radius: 5px;"
            "padding: 6px 12px; font-size: 16px; font-weight: 700;"
        )
        self.preview_subtitle_overlay.hide()

        top_header = QWidget()
        top_header.setFixedHeight(54)
        top_header.setStyleSheet("background: #171717; border: 1px solid #2d2d2d; border-radius: 12px;")
        header_layout = QHBoxLayout(top_header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        title = QLabel("Kut Studio / Sequence 01")
        title.setStyleSheet("color: #f2f2f2; font-size: 15px; font-weight: 700;")
        status = QLabel("EDIT MODE • 4K • 24fps")
        status.setStyleSheet("color: #9ec0ff; font-size: 11px; font-weight: 600;")
        status.setAlignment(Qt.AlignRight)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(status)

        toolbar = QWidget()
        toolbar.setFixedHeight(90)
        toolbar.setStyleSheet("background: #1a1a1a; border: 1px solid #2d2d2d; border-radius: 12px;")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 8)
        toolbar_layout.setSpacing(8)

        file_group = QWidget()
        file_layout = QHBoxLayout(file_group)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(6)
        for label in ("Import", "New", "Open"):
            file_layout.addWidget(self.make_tool_button(label))

        transport_group = QWidget()
        transport_layout = QHBoxLayout(transport_group)
        transport_layout.setContentsMargins(0, 0, 0, 0)
        transport_layout.setSpacing(6)
        rewind = self.make_tool_button("⏪")
        rewind.setFixedWidth(42)
        rewind.clicked.connect(lambda: seek_relative(-2))
        self.play_button = self.make_tool_button("▶ Play", accent=True)
        self.play_button.clicked.connect(toggle_play)
        stop = self.make_tool_button("■")
        stop.setFixedWidth(42)
        stop.clicked.connect(stop_playback)
        forward = self.make_tool_button("⏩")
        forward.setFixedWidth(42)
        forward.clicked.connect(lambda: seek_relative(2))
        for button in (rewind, self.play_button, stop, forward):
            transport_layout.addWidget(button)

        edit_group = QWidget()
        edit_layout = QHBoxLayout(edit_group)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.setSpacing(6)
        for label in ("Mark In", "Mark Out", "Cut", "Split"):
            edit_layout.addWidget(self.make_tool_button(label))

        toolbar_layout.addWidget(file_group)
        toolbar_layout.addWidget(transport_group)
        toolbar_layout.addWidget(edit_group)
        toolbar_layout.addStretch()

        preview_container = QWidget()
        preview_layout = QGridLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.video_widget, 0, 0)
        preview_layout.addWidget(self.preview_transition_overlay, 0, 0, Qt.AlignCenter)
        preview_layout.addWidget(self.preview_subtitle_overlay, 0, 0, Qt.AlignHCenter | Qt.AlignBottom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(top_header)
        layout.addWidget(toolbar)
        layout.addWidget(preview_container)
        self.setObjectName("viewer_panel")
        self.setStyleSheet("QWidget#viewer_panel { background: #151515; border: 1px solid #2b2b2b; border-radius: 12px; }")

    @staticmethod
    def make_tool_button(label, bg="#2a2a2a", accent=False):
        button = QPushButton(label)
        if accent:
            button.setStyleSheet(
                "QPushButton { background: #2f6fec; color: white; border: none; border-radius: 8px; padding: 9px 14px; font-weight: 700; }"
                "QPushButton:hover { background: #3d7ef3; }"
            )
        else:
            button.setStyleSheet(
                "QPushButton { background: %s; color: white; border: 1px solid #3a3a3a; border-radius: 8px; padding: 9px 12px; font-weight: 600; }"
                "QPushButton:hover { background: #373737; }" % bg
            )
        return button

    def load_video(self, path):
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
