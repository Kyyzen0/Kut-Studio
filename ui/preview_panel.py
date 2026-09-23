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

from ui.theme import COLORS, label_style


class PreviewPanel(QWidget):
    def __init__(self, toggle_play, stop_playback, seek_relative, cut_callback, open_file_callback, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget()
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
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

        self.empty_state = QLabel("Votre histoire commence ici\n\nImportez vos médias, puis déposez-les sur la timeline.")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setStyleSheet(label_style(14, "muted", 500))

        self.preview_subtitle_overlay = QLabel()
        self.preview_subtitle_overlay.setAlignment(Qt.AlignCenter)
        self.preview_subtitle_overlay.setWordWrap(True)
        self.preview_subtitle_overlay.setStyleSheet(
            f"background: rgba(0, 0, 0, 190); color: {COLORS['text']}; border-radius: 5px;"
            "padding: 6px 12px; font-size: 16px; font-weight: 700;"
        )
        self.preview_subtitle_overlay.hide()

        top_header = QWidget()
        top_header.setFixedHeight(54)
        top_header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QHBoxLayout(top_header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        title = QLabel("ESPACE DE TRAVAIL\nMontage vidéo")
        title.setStyleSheet(label_style(13, "text", 700))
        status = QLabel("PREVIEW   ·   1920 × 1080 · 30 fps")
        status.setStyleSheet(label_style(11, "muted", 600))
        status.setAlignment(Qt.AlignRight)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(status)

        toolbar = QWidget()
        toolbar.setFixedHeight(90)
        toolbar.setStyleSheet(f"background: {COLORS['panel']}; border-top: 1px solid {COLORS['border']};")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 8)
        toolbar_layout.setSpacing(8)

        file_group = QWidget()
        file_layout = QHBoxLayout(file_group)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(6)
        import_button = self.make_tool_button("Import")
        import_button.clicked.connect(open_file_callback)
        new_button = self.make_tool_button("New")
        new_button.clicked.connect(lambda: self._notify_placeholder("Nouveau projet"))
        open_button = self.make_tool_button("Open")
        open_button.clicked.connect(open_file_callback)
        file_layout.addWidget(import_button)
        file_layout.addWidget(new_button)
        file_layout.addWidget(open_button)

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
        mark_in_button = self.make_tool_button("Mark In")
        mark_in_button.clicked.connect(lambda: self._notify_placeholder("Mark In"))
        mark_out_button = self.make_tool_button("Mark Out")
        mark_out_button.clicked.connect(lambda: self._notify_placeholder("Mark Out"))
        cut_button = self.make_tool_button("Cut")
        cut_button.clicked.connect(cut_callback)
        split_button = self.make_tool_button("Split")
        split_button.clicked.connect(lambda: self._notify_placeholder("Split"))
        edit_layout.addWidget(mark_in_button)
        edit_layout.addWidget(mark_out_button)
        edit_layout.addWidget(cut_button)
        edit_layout.addWidget(split_button)

        toolbar_layout.addWidget(file_group)
        toolbar_layout.addWidget(transport_group)
        toolbar_layout.addWidget(edit_group)
        toolbar_layout.addStretch()

        preview_container = QWidget()
        preview_layout = QGridLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.video_widget, 0, 0)
        preview_layout.addWidget(self.empty_state, 0, 0, Qt.AlignCenter)
        preview_layout.addWidget(self.preview_transition_overlay, 0, 0, Qt.AlignCenter)
        preview_layout.addWidget(self.preview_subtitle_overlay, 0, 0, Qt.AlignHCenter | Qt.AlignBottom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(top_header)
        layout.addWidget(toolbar)
        layout.addWidget(preview_container)
        self.setObjectName("viewer_panel")
        self.setStyleSheet(f"QWidget#viewer_panel {{ background: {COLORS['background']}; }}")

    @staticmethod
    def make_tool_button(label, bg="#2a2a2a", accent=False):
        button = QPushButton(label)
        button.setCursor(Qt.PointingHandCursor)
        if accent:
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['accent']}; color: white; border: none; border-radius: 6px; padding: 9px 14px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
            )
        else:
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 9px 12px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {COLORS['surface_hover']}; }}"
            )
        return button

    def _notify_placeholder(self, feature_name):
        print(f"[PreviewPanel] {feature_name} : à implémenter")

    def load_video(self, path):
        self.empty_state.hide()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
