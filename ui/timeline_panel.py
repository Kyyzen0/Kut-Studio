from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from core.timeline_model import (
    MARKERS,
    TRACK_LABELS,
    TRACK_NAMES,
    default_clips,
    move_clip,
    trim_clip,
    transition_gap_pixels,
    v1_transition_pairs,
)
from ui.theme import COLORS, label_style


class ClipWidget(QWidget):
    def __init__(self, clip, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.parent_timeline = parent
        self.handle_width = 5
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = 0.0
        self.drag_original_end = 0.0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.label = QLabel(self.clip["label"], self)
        self.label.setStyleSheet("color: white; font-weight: 700; font-size: 11px;")
        self.label.move(8, 8)
        self.duration_label = QLabel(self.parent_timeline.format_time(self.clip["end"] - self.clip["start"]), self)
        self.duration_label.setStyleSheet("color: rgba(255,255,255,180); font-size: 10px;")
        self.duration_label.move(8, 26)
        self.refresh_style()

    def refresh_style(self):
        selected = self.parent_timeline is not None and self.parent_timeline.selected_clip is self.clip
        border = COLORS["accent_hover"] if selected else "#59616F"
        clip_color = self.clip.get("color", "#4da3ff")
        color = clip_color.name() if isinstance(clip_color, QColor) else str(clip_color)
        self.setStyleSheet(
            f"QWidget {{ background: {color}; border: 2px solid {border}; border-radius: 6px; color: white; }}"
            f"QWidget::hover {{ border-color: {COLORS['accent_hover']}; }}"
        )
        self.label.setText(self.clip["label"])
        self.duration_label.setText(self.parent_timeline.format_time(self.clip["end"] - self.clip["start"]))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        parent = self.parent_timeline
        if parent is None:
            return super().mousePressEvent(event)
        x = event.position().x()
        if x <= self.handle_width:
            self.drag_mode = "trim-left"
        elif x >= self.width() - self.handle_width:
            self.drag_mode = "trim-right"
        else:
            self.drag_mode = "move"
        self.drag_start_x = event.globalPos().x()
        self.drag_original_start = self.clip["start"]
        self.drag_original_end = self.clip["end"]
        parent.selected_clip = self.clip
        parent.clip_selected.emit(self.clip)
        parent.refresh_clip_widgets()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_mode is None:
            return
        parent = self.parent_timeline
        if parent is None:
            return
        delta_seconds = (event.globalPos().x() - self.drag_start_x) / (parent.pixels_per_second * parent.zoom)
        if self.drag_mode == "move":
            duration = self.drag_original_end - self.drag_original_start
            new_start = max(0.0, self.drag_original_start + delta_seconds)
            self.clip["start"] = new_start
            self.clip["end"] = new_start + duration
        elif self.drag_mode == "trim-right":
            new_end = max(self.drag_original_start + 0.1, self.drag_original_end + delta_seconds)
            self.clip["end"] = new_end
        elif self.drag_mode == "trim-left":
            new_start = min(self.drag_original_end - 0.1, self.drag_original_start + delta_seconds)
            self.clip["start"] = new_start
        parent.selected_clip = self.clip
        parent.clip_selected.emit(self.clip)
        parent.refresh_clip_widgets()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        parent = self.parent_timeline
        if parent is not None and self.drag_mode is not None:
            if self.drag_mode == "move":
                move_clip(parent.clips, self.clip["id"], self.clip["start"])
            elif self.drag_mode == "trim-right":
                trim_clip(parent.clips, self.clip["id"], new_end=self.clip["end"])
            elif self.drag_mode == "trim-left":
                trim_clip(parent.clips, self.clip["id"], new_start=self.clip["start"])
            parent.selected_clip = next((clip for clip in parent.clips if clip["id"] == self.clip["id"]), self.clip)
            parent.clip_selected.emit(parent.selected_clip)
            parent.refresh_clip_widgets()
        self.drag_mode = None
        event.accept()


class TimelinePanel(QWidget):
    seek_requested = Signal(float)
    clip_clicked = Signal(object)
    clip_selected = Signal(object)
    transition_clicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(270)
        self.setStyleSheet(f"background: {COLORS['panel_alt']}; color: {COLORS['text']};")
        self.header_height = 40
        self.ruler_height = 34
        self.track_height = 56
        self.left_margin = 90
        self.zoom = 1.0
        self.duration_seconds = 30.0
        self.playhead_seconds = 0.0
        self.pixels_per_second = 120.0
        self.track_names = list(TRACK_NAMES)
        self.track_labels = list(TRACK_LABELS)
        self.markers = MARKERS
        self.clips = default_clips()
        self.clip_widgets = {}
        self.dragging_playhead = False
        self.selected_clip = None
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = 0.0
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(42)
        self.play_button.setToolTip("Lecture / pause (Espace)")
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet("color: #f0f0f0; font-weight: 700; font-size: 12px;")
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet("color: #a0a0a0; font-size: 12px;")
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedWidth(26)
        self.zoom_out_btn.setToolTip("Réduire le zoom")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #dfe7ff; font-weight: 700; min-width: 48px; font-size: 11px;")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(26)
        self.zoom_in_btn.setToolTip("Augmenter le zoom")
        for button in (self.zoom_out_btn, self.zoom_in_btn):
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 5px; }}"
            )
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
        self.clip_count_label = QLabel()
        self.clip_count_label.setStyleSheet(label_style(11, "muted", 500))
        self.version_label = QLabel("KUT-STUDIO / v0.1")
        self.version_label.setStyleSheet(label_style(11, "muted", 500))
        header_layout.addSpacing(12)
        header_layout.addWidget(self.clip_count_label)
        header_layout.addWidget(self.version_label)
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        self.refresh_clip_widgets()

    def resizeEvent(self, event):
        self.header.resize(self.width(), self.header_height)
        self.refresh_clip_widgets()
        super().resizeEvent(event)

    @staticmethod
    def format_time(seconds):
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def select_clip(self, clip):
        self.selected_clip = clip
        self.clip_selected.emit(clip)
        self.refresh_clip_widgets()

    def refresh_clip_widgets(self):
        self.clip_count_label.setText(f"{len(self.clips)} clip" if len(self.clips) == 1 else f"{len(self.clips)} clips")
        current_ids = {clip["id"] for clip in self.clips}
        for clip_id, widget in list(self.clip_widgets.items()):
            if clip_id not in current_ids:
                widget.deleteLater()
                del self.clip_widgets[clip_id]
        for clip in self.clips:
            widget = self.clip_widgets.get(clip["id"])
            if widget is None:
                widget = ClipWidget(clip, self)
                self.clip_widgets[clip["id"]] = widget
            row = clip["track"]
            track_top = self.header_height + self.ruler_height + 8 + row * (self.track_height + 8) + 8
            start_x = self.left_margin + clip["start"] * self.pixels_per_second * self.zoom
            width = max(40, (clip["end"] - clip["start"]) * self.pixels_per_second * self.zoom)
            widget.setGeometry(int(start_x), int(track_top), max(int(width), 40), self.track_height - 16)
            widget.refresh_style()
            widget.raise_()
            widget.show()

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
        painter.fillRect(self.rect(), QColor(COLORS["panel_alt"]))
        painter.fillRect(0, 0, self.width(), self.header_height, QColor(COLORS["panel"]))
        ruler_top = self.header_height + 8
        ruler_bottom = ruler_top + self.ruler_height
        painter.fillRect(0, ruler_top, self.width(), self.ruler_height, QColor(COLORS["surface"]))
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(self.left_margin, ruler_top, self.width(), ruler_top)
        painter.drawLine(self.left_margin, ruler_bottom, self.width(), ruler_bottom)
        major_ticks = max(1, int(self.duration_seconds) + 1)
        for second in range(major_ticks):
            x = self.left_margin + second * self.pixels_per_second * self.zoom
            if x < self.width() - 10:
                painter.setPen(QPen(QColor(COLORS["border"]), 1))
                painter.drawLine(int(x), ruler_bottom, int(x), self.height())
                if second % 5 == 0:
                    painter.setPen(QPen(QColor(COLORS["text"]), 1))
                    painter.drawLine(int(x), ruler_top, int(x), ruler_bottom)
                    painter.drawText(int(x) + 5, ruler_top + 20, self.format_time(second))
                else:
                    painter.setPen(QPen(QColor(COLORS["muted"]), 1))
                    painter.drawLine(int(x), ruler_top + 10, int(x), ruler_bottom)

        for row in range(3):
            y = ruler_bottom + 8 + row * (self.track_height + 8)
            painter.fillRect(0, y, self.width(), self.track_height, QColor(COLORS["panel_alt"]))
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
            painter.drawLine(self.left_margin, y, self.width(), y)
            painter.drawLine(self.left_margin, y, self.left_margin, y + self.track_height)
            painter.setBrush(QColor(COLORS["surface"]))
            painter.drawRoundedRect(10, y + 10, 28, 24, 5, 5)
            painter.setPen(QPen(QColor(COLORS["text"]), 1))
            painter.drawText(16, y + 27, self.track_names[row])
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(45, y + 25, self.track_labels[row])
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(13, y + 47, "M   S   LOCK")
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
        if not self.clips:
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(self.left_margin + 24, ruler_bottom + 45, "Déposez votre premier clip ici")
        playhead_x = self.left_margin + self.playhead_seconds * self.pixels_per_second * self.zoom
        painter.setPen(QPen(QColor(COLORS["accent_hover"]), 2))
        painter.drawLine(int(playhead_x), self.header_height, int(playhead_x), self.height())
        painter.fillRect(int(playhead_x) - 7, self.header_height, 14, 18, QColor(COLORS["accent"]))
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
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
        self.refresh_clip_widgets()
        self.update()

    def zoom_out(self):
        self.zoom = max(0.5, min(3.0, self.zoom / 1.2))
        self.update_zoom_label()
        self.refresh_clip_widgets()
        self.update()

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier and event.angleDelta().y():
            factor = 1.0 + abs(event.angleDelta().y()) / 1200.0
            if event.angleDelta().y() < 0:
                factor = 1.0 / factor
            self.zoom = max(0.5, min(3.0, self.zoom * factor))
            self.update_zoom_label()
            self.refresh_clip_widgets()
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
                    self.clip_selected.emit(clip)
                    self.refresh_clip_widgets()
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
                self.clip_selected.emit(clip)
                self.refresh_clip_widgets()
                event.accept()
                return
            if self.drag_mode == "playhead":
                self.update_playhead_from_x(event.position().x())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.drag_mode == "clip":
                self.selected_clip = next((clip for clip in self.clips if clip["id"] == self.selected_clip["id"]), self.selected_clip)
                move_clip(self.clips, self.selected_clip["id"], self.selected_clip["start"])
            self.dragging_playhead = False
            self.drag_mode = None
            self.refresh_clip_widgets()
            event.accept()
            return
        super().mouseReleaseEvent(event)
