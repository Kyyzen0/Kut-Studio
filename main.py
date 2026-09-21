import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget,
                               QHBoxLayout, QVBoxLayout, QListWidget,
                               QLabel, QSplitter, QPushButton)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPen


class TimelineWidget(QWidget):
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setStyleSheet("background: #1e1e1e; color: white;")

        self.header_height = 40
        self.ruler_height = 34
        self.track_height = 56
        self.left_margin = 90
        self.zoom = 1.0
        self.duration_seconds = 30.0
        self.playhead_seconds = 0.0
        self.pixels_per_second = 120.0

        self.clips = [
            {"track": 0, "start": 0.0, "end": 4.0, "label": "Intro", "color": QColor("#4da3ff")},
            {"track": 0, "start": 6.5, "end": 12.0, "label": "Plan A", "color": QColor("#58c4a7")},
            {"track": 1, "start": 2.0, "end": 7.5, "label": "VO", "color": QColor("#ff9f43")},
            {"track": 1, "start": 10.0, "end": 15.0, "label": "B-roll", "color": QColor("#b070ff")},
        ]
        self.dragging_playhead = False
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(42)
        self.play_button.setStyleSheet(
            "QPushButton { background: #2d2d2d; color: white; border: 1px solid #3b3b3b; border-radius: 7px; font-weight: 600; }"
            "QPushButton:hover { background: #3b3b3b; }"
        )

        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet("color: #f0f0f0; font-weight: 700; font-size: 12px;")
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet("color: #a0a0a0; font-size: 12px;")

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedWidth(26)
        self.zoom_out_btn.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 5px; }")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #dfe7ff; font-weight: 700; min-width: 48px; font-size: 11px;")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(26)
        self.zoom_in_btn.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 5px; }")

        self.header = QWidget(self)
        self.header.setStyleSheet("background: #202020; border-bottom: 1px solid #2f2f2f;")
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(8, 6, 10, 6)
        self.header_layout.addWidget(self.play_button)
        self.header_layout.addWidget(self.time_label)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.total_time_label)
        self.header_layout.addWidget(self.zoom_out_btn)
        self.header_layout.addWidget(self.zoom_label)
        self.header_layout.addWidget(self.zoom_in_btn)

        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_in_btn.clicked.connect(self.zoom_in)

    def resizeEvent(self, event):
        self.header.resize(self.width(), self.header_height)
        super().resizeEvent(event)

    def format_time(self, seconds):
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def setDuration(self, duration_ms):
        if duration_ms > 0:
            self.duration_seconds = max(1.0, duration_ms / 1000.0)
            self.total_time_label.setText(f"/ {self.format_time(self.duration_seconds)}")
            self.update()

    def setPlaybackPosition(self, position_ms):
        self.playhead_seconds = min(max(position_ms / 1000.0, 0.0), self.duration_seconds)
        self.time_label.setText(self.format_time(self.playhead_seconds))
        self.update()

    def setPlayState(self, is_playing):
        self.play_button.setText("❚❚" if is_playing else "▶")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        painter.fillRect(0, 0, self.width(), self.header_height, QColor("#222222"))

        ruler_top = self.header_height + 8
        ruler_bottom = ruler_top + self.ruler_height

        painter.fillRect(0, ruler_top, self.width(), self.ruler_height, QColor("#262626"))
        painter.setPen(QPen(QColor("#3d3d3d"), 1))
        painter.drawLine(self.left_margin, ruler_top, self.width(), ruler_top)
        painter.drawLine(self.left_margin, ruler_bottom, self.width(), ruler_bottom)

        painter.setPen(QPen(QColor("#d7d7d7"), 1))
        major_ticks = max(1, int(self.duration_seconds) + 1)
        for second in range(major_ticks):
            x = self.left_margin + (second * self.pixels_per_second * self.zoom)
            if x < self.width() - 10:
                tick_top = ruler_top
                tick_bottom = ruler_top + self.ruler_height
                if second % 5 == 0:
                    painter.setPen(QPen(QColor("#f0f0f0"), 1))
                    painter.drawLine(int(x), tick_top, int(x), tick_bottom)
                    painter.drawText(int(x) + 5, ruler_top + 20, self.format_time(second))
                else:
                    painter.setPen(QPen(QColor("#8b8b8b"), 1))
                    painter.drawLine(int(x), tick_top + 10, int(x), tick_bottom)

        for row in range(2):
            y = ruler_bottom + 8 + row * (self.track_height + 8)
            painter.fillRect(0, y, self.width(), self.track_height, QColor("#171717"))
            painter.setPen(QPen(QColor("#3a3a3a"), 1))
            painter.drawLine(self.left_margin, y, self.width(), y)
            painter.drawLine(self.left_margin, y, self.left_margin, y + self.track_height)
            painter.setPen(QPen(QColor("#e8e8e8"), 1))
            painter.drawText(12, y + 32, f"V{row + 1}")

            for clip in self.clips:
                if clip["track"] != row:
                    continue
                start_x = self.left_margin + clip["start"] * self.pixels_per_second * self.zoom
                end_x = self.left_margin + clip["end"] * self.pixels_per_second * self.zoom
                clip_x = max(start_x, self.left_margin)
                clip_w = max(28, end_x - start_x)
                clip_y = y + 8
                clip_h = self.track_height - 16
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.setBrush(clip["color"])
                painter.drawRoundedRect(int(clip_x), int(clip_y), int(clip_w), int(clip_h), 8, 8)
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawText(int(clip_x) + 8, int(clip_y) + 22, clip["label"])
                painter.setBrush(Qt.NoBrush)

        playhead_x = self.left_margin + self.playhead_seconds * self.pixels_per_second * self.zoom
        painter.setPen(QPen(QColor("#ff3b30"), 2))
        painter.drawLine(int(playhead_x), self.header_height, int(playhead_x), self.height())
        painter.fillRect(int(playhead_x) - 7, self.header_height, 14, 18, QColor("#ff3b30"))

        # subtle panel glow to feel more "Premiere-like"
        painter.setPen(QPen(QColor("#2a2a2a"), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def get_seconds_from_x(self, x):
        x = max(self.left_margin, min(x, self.width() - 10))
        seconds = (x - self.left_margin) / (self.pixels_per_second * self.zoom)
        return max(0.0, min(self.duration_seconds, seconds))

    def update_playhead_from_x(self, x):
        seconds = self.get_seconds_from_x(x)
        self.playhead_seconds = seconds
        self.time_label.setText(self.format_time(seconds))
        self.seek_requested.emit(seconds)
        self.update()

    def update_zoom_label(self):
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")

    def zoom_in(self):
        self.zoom = max(0.5, min(3.0, self.zoom * 1.2))
        self.update_zoom_label()
        self.update()

    def zoom_out(self):
        self.zoom = max(0.5, min(3.0, self.zoom / 1.2))
        self.update_zoom_label()
        self.update()

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                factor = 1.0 + abs(delta) / 1200.0
                if delta < 0:
                    factor = 1.0 / factor
                self.zoom = max(0.5, min(3.0, self.zoom * factor))
                self.update_zoom_label()
                self.update()
                event.accept()
                return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            if x >= self.left_margin:
                self.dragging_playhead = True
                self.update_playhead_from_x(x)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging_playhead and event.buttons() & Qt.LeftButton:
            self.update_playhead_from_x(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging_playhead = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PremiereSimple(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Premiere Simple - v0.3")
        self.setGeometry(100, 100, 1200, 700)

        # Player
        self.player = QMediaPlayer()
        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)

        # GAUCHE - Bin / Fichiers importés
        self.bin = QListWidget()
        self.bin.setAcceptDrops(True)
        self.bin.setStyleSheet(
            "QListWidget { background: #1d1d1d; color: white; border: 1px solid #313131; border-radius: 10px; padding: 6px; }"
            "QListWidget::item { padding: 10px 8px; border-radius: 6px; color: #efefef; }"
            "QListWidget::item:selected { background: #2d4d77; color: white; }"
        )
        self.bin.addItem("Glisse tes vidéos ici (.mp4)")
        self.bin.setFixedWidth(250)
        self.bin.itemClicked.connect(self.load_video)

        # CENTRE - Viewer
        top_header = QWidget()
        top_header.setFixedHeight(54)
        top_header.setStyleSheet("background: #171717; border: 1px solid #2d2d2d; border-radius: 12px;")
        header_layout = QHBoxLayout(top_header)
        header_layout.setContentsMargins(14, 8, 14, 8)

        project_title = QLabel("Premiere Simple / Sequence 01")
        project_title.setStyleSheet("color: #f2f2f2; font-size: 15px; font-weight: 700;")
        project_status = QLabel("EDIT MODE • 4K • 24fps")
        project_status.setStyleSheet("color: #9ec0ff; font-size: 11px; font-weight: 600;")
        project_status.setAlignment(Qt.AlignRight)

        header_layout.addWidget(project_title)
        header_layout.addStretch()
        header_layout.addWidget(project_status)

        toolbar = QWidget()
        toolbar.setFixedHeight(90)
        toolbar.setStyleSheet("background: #1a1a1a; border: 1px solid #2d2d2d; border-radius: 12px;")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 8)
        toolbar_layout.setSpacing(8)

        def make_tool_button(label, bg="#2a2a2a", accent=False):
            btn = QPushButton(label)
            if accent:
                btn.setStyleSheet(
                    "QPushButton { background: #2f6fec; color: white; border: none; border-radius: 8px; padding: 9px 14px; font-weight: 700; }"
                    "QPushButton:hover { background: #3d7ef3; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: %s; color: white; border: 1px solid #3a3a3a; border-radius: 8px; padding: 9px 12px; font-weight: 600; }"
                    "QPushButton:hover { background: #373737; }" % bg
                )
            return btn

        file_group = QWidget()
        file_group_layout = QHBoxLayout(file_group)
        file_group_layout.setContentsMargins(0, 0, 0, 0)
        file_group_layout.setSpacing(6)
        btn_import = make_tool_button("Import")
        btn_new = make_tool_button("New")
        btn_open = make_tool_button("Open")
        file_group_layout.addWidget(btn_import)
        file_group_layout.addWidget(btn_new)
        file_group_layout.addWidget(btn_open)

        transport_group = QWidget()
        transport_group_layout = QHBoxLayout(transport_group)
        transport_group_layout.setContentsMargins(0, 0, 0, 0)
        transport_group_layout.setSpacing(6)
        btn_rewind = QPushButton("⏪")
        btn_rewind.setFixedWidth(42)
        btn_rewind.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 7px; }")
        btn_rewind.clicked.connect(lambda: self.seek_relative(-2))
        self.btn_play = make_tool_button("▶ Play", accent=True)
        self.btn_play.clicked.connect(self.toggle_play)
        btn_stop = QPushButton("■")
        btn_stop.setFixedWidth(42)
        btn_stop.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 7px; }")
        btn_stop.clicked.connect(self.stop_playback)
        btn_forward = QPushButton("⏩")
        btn_forward.setFixedWidth(42)
        btn_forward.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 7px; }")
        btn_forward.clicked.connect(lambda: self.seek_relative(2))
        transport_group_layout.addWidget(btn_rewind)
        transport_group_layout.addWidget(self.btn_play)
        transport_group_layout.addWidget(btn_stop)
        transport_group_layout.addWidget(btn_forward)

        edit_group = QWidget()
        edit_group_layout = QHBoxLayout(edit_group)
        edit_group_layout.setContentsMargins(0, 0, 0, 0)
        edit_group_layout.setSpacing(6)
        btn_mark_in = make_tool_button("Mark In")
        btn_mark_out = make_tool_button("Mark Out")
        btn_cut = make_tool_button("Cut")
        btn_split = make_tool_button("Split")
        edit_group_layout.addWidget(btn_mark_in)
        edit_group_layout.addWidget(btn_mark_out)
        edit_group_layout.addWidget(btn_cut)
        edit_group_layout.addWidget(btn_split)

        toolbar_layout.addWidget(file_group)
        toolbar_layout.addWidget(transport_group)
        toolbar_layout.addWidget(edit_group)
        toolbar_layout.addStretch()

        viewer_layout = QVBoxLayout()
        viewer_layout.setContentsMargins(12, 12, 12, 12)
        viewer_layout.addWidget(top_header)
        viewer_layout.addWidget(toolbar)
        viewer_layout.addWidget(self.video_widget)

        viewer_container = QWidget()
        viewer_container.setObjectName("viewer_panel")
        viewer_container.setStyleSheet(
            "QWidget#viewer_panel { background: #151515; border: 1px solid #2b2b2b; border-radius: 12px; }"
        )
        viewer_container.setLayout(viewer_layout)

        # BAS - Timeline
        self.timeline_widget = TimelineWidget()
        self.timeline_widget.setMinimumHeight(190)
        self.timeline_widget.play_button.clicked.connect(self.toggle_play)
        self.timeline_widget.seek_requested.connect(self.seek_to_position)

        self.timeline_timer = QTimer(self)
        self.timeline_timer.setInterval(40)
        self.timeline_timer.timeout.connect(self.update_timeline)
        self.timeline_timer.start()

        # Connect medias to timeline
        self.player.durationChanged.connect(self.timeline_widget.setDuration)
        self.player.positionChanged.connect(self.timeline_widget.setPlaybackPosition)
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)

        # Layout principal
        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(self.bin)
        top_split.addWidget(viewer_container)

        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline_widget)
        main_split.setSizes([500, 180])

        self.setCentralWidget(main_split)
        self.setStyleSheet(
            "QMainWindow { background: #121212; color: white; }"
            "QWidget { color: white; }"
            "QSplitter::handle { background: #2a2a2a; }"
            "QSplitter::handle:vertical { width: 4px; }"
            "QSplitter::handle:horizontal { height: 4px; }"
            "QLabel { color: white; }"
            "QPushButton { background: #2d2d2d; color: white; border: 1px solid #3a3a3a; border-radius: 8px; padding: 7px 12px; }"
            "QPushButton:hover { background: #3a3a3a; }"
            "QListWidget::item { background: transparent; }"
        )

    def on_playback_state_changed(self, state):
        is_playing = state == QMediaPlayer.PlayingState
        self.timeline_widget.setPlayState(is_playing)
        self.btn_play.setText("❚❚ Pause" if is_playing else "▶ Play")

    def update_timeline(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.timeline_widget.setPlaybackPosition(self.player.position())

    def seek_to_position(self, seconds):
        self.player.setPosition(int(seconds * 1000))

    def load_video(self, item):
        path = item.text()
        if path.endswith((".mp4", ".mov", ".avi")):
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()

    def stop_playback(self):
        self.player.stop()
        self.timeline_widget.setPlaybackPosition(0)
        self.player.setPosition(0)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_relative(self, delta_seconds):
        current_ms = self.player.position()
        new_pos = max(0, min(self.player.duration(), current_ms + int(delta_seconds * 1000)))
        self.player.setPosition(new_pos)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_J):
            self.seek_relative(-2)
            event.accept()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_L):
            self.seek_relative(2)
            event.accept()
            return
        if event.key() == Qt.Key_K:
            self.toggle_play()
            event.accept()
            return
        super().keyPressEvent(event)

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