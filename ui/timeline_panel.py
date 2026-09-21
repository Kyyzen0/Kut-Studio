from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from core.timeline_model import MARKERS, TRACK_LABELS, TRACK_NAMES, default_clips, transition_gap_pixels, v1_transition_pairs


class TimelinePanel(QWidget):
    seek_requested = Signal(float)
    clip_clicked = Signal(object)
    transition_clicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(270)
        self.setStyleSheet("background: #1e1e1e; color: white;")
        self.header_height = 40
        self.ruler_height = 34
        self.track_height = 56
        self.left_margin = 90
        self.zoom = 1.0
        self.duration_seconds = 30.0
        self.playhead_seconds = 0.0
        self.pixels_per_second = 120.0
        self.track_names = TRACK_NAMES
        self.track_labels = TRACK_LABELS
        self.markers = MARKERS
        self.clips = default_clips()
        self.dragging_playhead = False
        self.selected_clip = None
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = 0.0
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
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #dfe7ff; font-weight: 700; min-width: 48px; font-size: 11px;")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(26)
        for button in (self.zoom_out_btn, self.zoom_in_btn):
            button.setStyleSheet("QPushButton { background: #2a2a2a; color: white; border: 1px solid #3a3a3a; border-radius: 5px; }")
        self.header = QWidget(self)
        self.header.setStyleSheet("background: #202020; border-bottom: 1px solid #2f2f2f;")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 6, 10, 6)
        header_layout.addWidget(self.play_button)
        header_layout.addWidget(self.time_label)
        header_layout.addStretch()
        header_layout.addWidget(self.total_time_label)
        header_layout.addWidget(self.zoom_out_btn)
        header_layout.addWidget(self.zoom_label)
        header_layout.addWidget(self.zoom_in_btn)
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_in_btn.clicked.connect(self.zoom_in)

    def resizeEvent(self, event):
        self.header.resize(self.width(), self.header_height)
        super().resizeEvent(event)

    @staticmethod
    def format_time(seconds):
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
        major_ticks = max(1, int(self.duration_seconds) + 1)
        for second in range(major_ticks):
            x = self.left_margin + second * self.pixels_per_second * self.zoom
            if x < self.width() - 10:
                painter.setPen(QPen(QColor("#303030"), 1))
                painter.drawLine(int(x), ruler_bottom, int(x), self.height())
                if second % 5 == 0:
                    painter.setPen(QPen(QColor("#f0f0f0"), 1))
                    painter.drawLine(int(x), ruler_top, int(x), ruler_bottom)
                    painter.drawText(int(x) + 5, ruler_top + 20, self.format_time(second))
                else:
                    painter.setPen(QPen(QColor("#8b8b8b"), 1))
                    painter.drawLine(int(x), ruler_top + 10, int(x), ruler_bottom)

        for row in range(3):
            y = ruler_bottom + 8 + row * (self.track_height + 8)
            painter.fillRect(0, y, self.width(), self.track_height, QColor("#171717"))
            painter.setPen(QPen(QColor("#3a3a3a"), 1))
            painter.drawLine(self.left_margin, y, self.width(), y)
            painter.drawLine(self.left_margin, y, self.left_margin, y + self.track_height)
            painter.setBrush(QColor("#232323"))
            painter.drawRoundedRect(10, y + 10, 28, 24, 5, 5)
            painter.setPen(QPen(QColor("#f4f4f4"), 1))
            painter.drawText(16, y + 27, self.track_names[row])
            painter.setPen(QPen(QColor("#8f8f8f"), 1))
            painter.drawText(45, y + 25, self.track_labels[row])
            painter.setPen(QPen(QColor("#555555"), 1))
            painter.drawText(13, y + 47, "M   S   🔒")
            painter.setBrush(Qt.NoBrush)
            for clip in self.clips:
                if clip["track"] != row:
                    continue
                start_x = self.left_margin + clip["start"] * self.pixels_per_second * self.zoom
                end_x = self.left_margin + clip["end"] * self.pixels_per_second * self.zoom
                clip_x = max(start_x, self.left_margin)
                clip_w = max(28, end_x - start_x)
                clip_y = y + 8
                clip_h = self.track_height - 16
                is_selected = clip is self.selected_clip
                painter.setPen(QPen(QColor("#ffffff"), 1 if not is_selected else 2))
                painter.setBrush(clip["color"])
                painter.drawRoundedRect(int(clip_x), int(clip_y), int(clip_w), int(clip_h), 8, 8)
                if is_selected:
                    painter.setPen(QPen(QColor("#9ec0ff"), 2))
                    painter.drawRoundedRect(int(clip_x) - 2, int(clip_y) - 2, int(clip_w) + 4, int(clip_h) + 4, 10, 10)
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawText(int(clip_x) + 8, int(clip_y) + 22, clip["label"])
                painter.setPen(QPen(QColor(255, 255, 255, 175), 1))
                painter.drawText(int(clip_x) + 8, int(clip_y) + 38, self.format_time(clip["end"] - clip["start"]))
                if is_selected:
                    painter.setPen(QPen(QColor("#ffffff"), 2))
                    painter.drawLine(int(clip_x) + 5, int(clip_y) + 6, int(clip_x) + 5, int(clip_y) + clip_h - 6)
                    painter.drawLine(int(clip_x + clip_w) - 5, int(clip_y) + 6, int(clip_x + clip_w) - 5, int(clip_y) + clip_h - 6)
                painter.setBrush(Qt.NoBrush)

        for previous, following in v1_transition_pairs(self.clips, self.pixels_per_second, self.zoom):
            gap_pixels = transition_gap_pixels(previous, following, self.pixels_per_second, self.zoom)
            transition_x = self.left_margin + following["start"] * self.pixels_per_second * self.zoom - gap_pixels / 2
            transition_y = ruler_bottom + 8 + self.track_height - 20
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QColor("#e26d5c"))
            painter.drawRoundedRect(int(transition_x) - 9, int(transition_y), 18, 18, 4, 4)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawLine(int(transition_x) - 5, int(transition_y) + 9, int(transition_x) + 5, int(transition_y) + 9)
            painter.drawLine(int(transition_x), int(transition_y) + 4, int(transition_x), int(transition_y) + 14)
            painter.setBrush(Qt.NoBrush)

        for marker in self.markers:
            marker_x = self.left_margin + marker * self.pixels_per_second * self.zoom
            if marker_x < self.width() - 8:
                painter.setPen(QPen(QColor("#f7c948"), 1))
                painter.drawLine(int(marker_x), ruler_top, int(marker_x), self.height())
                painter.setBrush(QColor("#f7c948"))
                painter.drawPolygon([QPoint(int(marker_x) - 5, ruler_top), QPoint(int(marker_x) + 5, ruler_top), QPoint(int(marker_x), ruler_top + 8)])
                painter.setBrush(Qt.NoBrush)
        playhead_x = self.left_margin + self.playhead_seconds * self.pixels_per_second * self.zoom
        painter.setPen(QPen(QColor("#ff3b30"), 2))
        painter.drawLine(int(playhead_x), self.header_height, int(playhead_x), self.height())
        painter.fillRect(int(playhead_x) - 7, self.header_height, 14, 18, QColor("#ff3b30"))
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
        if event.modifiers() == Qt.ControlModifier and event.angleDelta().y():
            factor = 1.0 + abs(event.angleDelta().y()) / 1200.0
            if event.angleDelta().y() < 0:
                factor = 1.0 / factor
            self.zoom = max(0.5, min(3.0, self.zoom * factor))
            self.update_zoom_label()
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def find_transition_at(self, x, y):
        track_top = self.header_height + self.ruler_height + 8
        track_bottom = track_top + self.track_height
        if not track_top <= y <= track_bottom:
            return None
        for previous, following in v1_transition_pairs(self.clips, self.pixels_per_second, self.zoom):
            gap_pixels = transition_gap_pixels(previous, following, self.pixels_per_second, self.zoom)
            transition_x = self.left_margin + following["start"] * self.pixels_per_second * self.zoom - gap_pixels / 2
            if abs(x - transition_x) <= 12:
                return following["start"]
        return None

    def find_clip_at(self, x, y):
        for row in range(3):
            track_top = self.header_height + self.ruler_height + 8 + row * (self.track_height + 8)
            if track_top <= y <= track_top + self.track_height:
                for clip in self.clips:
                    if clip["track"] != row:
                        continue
                    start_x = self.left_margin + clip["start"] * self.pixels_per_second * self.zoom
                    end_x = self.left_margin + clip["end"] * self.pixels_per_second * self.zoom
                    if start_x <= x <= end_x:
                        return clip
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            y = event.position().y()
            if x >= self.left_margin:
                transition_time = self.find_transition_at(x, y)
                if transition_time is not None:
                    self.transition_clicked.emit(transition_time)
                    event.accept()
                    return
                clip = self.find_clip_at(x, y)
                if clip is not None:
                    self.selected_clip = clip
                    self.drag_mode = "clip"
                    self.drag_start_x = x
                    self.drag_original_start = clip["start"]
                    self.clip_clicked.emit(clip)
                    self.update()
                    event.accept()
                    return
                self.drag_mode = "playhead"
                self.dragging_playhead = True
                self.update_playhead_from_x(x)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            if self.drag_mode == "clip" and self.selected_clip is not None:
                delta_seconds = (event.position().x() - self.drag_start_x) / (self.pixels_per_second * self.zoom)
                clip = self.selected_clip
                duration = clip["end"] - clip["start"]
                clip["start"] = max(0.0, self.drag_original_start + delta_seconds)
                clip["end"] = clip["start"] + duration
                self.clip_clicked.emit(clip)
                self.update()
                event.accept()
                return
            if self.drag_mode == "playhead":
                self.update_playhead_from_x(event.position().x())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging_playhead = False
            self.drag_mode = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
