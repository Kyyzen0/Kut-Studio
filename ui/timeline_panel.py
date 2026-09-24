from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from core.project_model import Project
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
    transition_gap_pixels,
    v1_transition_pairs,
)
from ui.theme import COLORS, label_style


_TRACK_TYPE_LABELS = {"video": "Vidéo", "subtitle": "Sous-titres"}
_DEMO_MARKERS = (4.0, 9.0, 14.0)


class ClipWidget(QWidget):
    """Widget visuel représentant un TimelineClipView immuable.

    Le widget ne mute jamais le ``Project`` ni la vue : il mémorise
    uniquement des valeurs de drag temporaires et émet un signal
    d'intention au relâchement de la souris.
    """

    def __init__(self, view: TimelineClipView, parent: "TimelinePanel | None" = None):
        super().__init__(parent)
        self.view = view
        self.parent_timeline = parent
        self.handle_width = 5
        self.drag_mode = None  # type: str | None
        self.drag_start_x = 0
        self.drag_original_start = view.start
        self.drag_original_end = view.end
        # Valeurs pending pour le rendu pendant un drag : on n'écrit jamais
        # dans ``self.view`` (frozen) ni dans le ``Project``.
        self.pending_start = view.start
        self.pending_end = view.end
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.label = QLabel(self.view.label, self)
        self.label.setStyleSheet("color: white; font-weight: 700; font-size: 11px;")
        self.label.move(8, 8)
        self.duration_label = QLabel(
            self.parent_timeline.format_time(self.view.end - self.view.start),
            self,
        )
        self.duration_label.setStyleSheet("color: rgba(255,255,255,180); font-size: 10px;")
        self.duration_label.move(8, 26)
        self.refresh_style()

    def refresh_style(self):
        selected = (
            self.parent_timeline is not None
            and self.parent_timeline.selected_clip_id == self.view.id
        )
        border = COLORS["accent_hover"] if selected else "#59616F"
        self.setStyleSheet(
            f"QWidget {{ background: {self.view.color_key}; "
            f"border: 2px solid {border}; border-radius: 6px; color: white; }}"
            f"QWidget::hover {{ border-color: {COLORS['accent_hover']}; }}"
        )
        self.label.setText(self.view.label)
        self.duration_label.setText(
            self.parent_timeline.format_time(self.view.end - self.view.start)
        )

    def _apply_pending_geometry(self) -> None:
        parent = self.parent_timeline
        if parent is None:
            return
        start_x = (
            parent.left_margin
            + self.pending_start * parent.pixels_per_second * parent.zoom
        )
        width = max(
            40,
            (self.pending_end - self.pending_start)
            * parent.pixels_per_second
            * parent.zoom,
        )
        row = self.view.track_index
        track_top = (
            parent.header_height
            + parent.ruler_height
            + 8
            + row * (parent.track_height + 8)
            + 8
        )
        self.setGeometry(
            int(start_x),
            int(track_top),
            max(int(width), 40),
            parent.track_height - 16,
        )

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
        self.drag_original_start = self.view.start
        self.drag_original_end = self.view.end
        self.pending_start = self.view.start
        self.pending_end = self.view.end
        parent.selected_clip_id = self.view.id
        parent.clip_selected.emit(self.view.id)
        parent.refresh_clip_widgets()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_mode is None:
            return
        parent = self.parent_timeline
        if parent is None:
            return
        delta_seconds = (event.globalPos().x() - self.drag_start_x) / (
            parent.pixels_per_second * parent.zoom
        )
        if self.drag_mode == "move":
            self.pending_start = max(
                0.0, self.drag_original_start + delta_seconds
            )
            self.pending_end = self.pending_start + (
                self.drag_original_end - self.drag_original_start
            )
        elif self.drag_mode == "trim-right":
            self.pending_end = max(
                self.drag_original_start + 0.1,
                self.drag_original_end + delta_seconds,
            )
        elif self.drag_mode == "trim-left":
            self.pending_start = min(
                self.drag_original_end - 0.1,
                self.drag_original_start + delta_seconds,
            )
        self._apply_pending_geometry()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        parent = self.parent_timeline
        if parent is not None and self.drag_mode is not None:
            if self.drag_mode == "move":
                parent.move_clip_requested.emit(self.view.id, self.pending_start)
            elif self.drag_mode == "trim-right":
                parent.trim_clip_right_requested.emit(
                    self.view.id, self.pending_end
                )
            elif self.drag_mode == "trim-left":
                parent.trim_clip_left_requested.emit(
                    self.view.id, self.pending_start
                )
        self.drag_mode = None
        event.accept()


class TimelinePanel(QWidget):
    """Timeline de Kut-Studio, pilotée par un ``Project``.

    La timeline n'est qu'une projection : elle stocke des
    ``TimelineClipView`` immuables et émet des signaux d'intention
    (``move_clip_requested``, ``trim_clip_left_requested``,
    ``trim_clip_right_requested``). C'est ``MainWindow`` qui applique
    les opérations via ``core.timeline_operations`` puis demande un
    rafraîchissement via ``set_project``.
    """

    seek_requested = Signal(float)
    clip_selected = Signal(str)
    transition_clicked = Signal(float)
    move_clip_requested = Signal(str, float)
    trim_clip_left_requested = Signal(str, float)
    trim_clip_right_requested = Signal(str, float)

    def __init__(self, project: Project | None = None, parent=None):
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
        self.project = project
        self.clip_views: list[TimelineClipView] = (
            build_clip_views(project) if project is not None else []
        )
        self._refresh_track_metadata()
        self.markers = list(_DEMO_MARKERS)
        self.clip_widgets: dict[str, ClipWidget] = {}
        self.dragging_playhead = False
        self.selected_clip_id: str | None = None
        self.drag_mode: str | None = None
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

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        """Remplace le projet affiché et reconstruit la projection."""
        self.project = project
        self._refresh_track_metadata()
        self.clip_views = build_clip_views(project)
        self.selected_clip_id = None
        self.refresh_clip_widgets()
        self.update()

    def find_view_by_id(self, clip_id: str) -> TimelineClipView | None:
        """Retourne la vue correspondant à ``clip_id`` ou ``None``."""
        for view in self.clip_views:
            if view.id == clip_id:
                return view
        return None

    def select_clip(self, clip_id: str) -> None:
        """Sélectionne un clip par identifiant et émet ``clip_selected``."""
        self.selected_clip_id = clip_id
        self.clip_selected.emit(clip_id)
        self.refresh_clip_widgets()

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        self.header.resize(self.width(), self.header_height)
        self.refresh_clip_widgets()
        super().resizeEvent(event)

    @staticmethod
    def format_time(seconds):
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _refresh_track_metadata(self) -> None:
        if self.project is None:
            self.track_names: list[str] = []
            self.track_labels: list[str] = []
            return
        self.track_names = [track.name for track in self.project.tracks]
        self.track_labels = [
            _TRACK_TYPE_LABELS.get(track.type, track.type)
            for track in self.project.tracks
        ]

    def refresh_clip_widgets(self):
        count = len(self.clip_views)
        self.clip_count_label.setText(
            f"{count} clip" if count == 1 else f"{count} clips"
        )
        current_ids = {view.id for view in self.clip_views}
        for clip_id, widget in list(self.clip_widgets.items()):
            if clip_id not in current_ids:
                widget.deleteLater()
                del self.clip_widgets[clip_id]
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None:
                widget = ClipWidget(view, self)
                self.clip_widgets[view.id] = widget
            row = view.track_index
            track_top = (
                self.header_height
                + self.ruler_height
                + 8
                + row * (self.track_height + 8)
                + 8
            )
            start_x = (
                self.left_margin + view.start * self.pixels_per_second * self.zoom
            )
            width = max(
                40,
                (view.end - view.start) * self.pixels_per_second * self.zoom,
            )
            widget.setGeometry(
                int(start_x),
                int(track_top),
                max(int(width), 40),
                self.track_height - 16,
            )
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

        for row, (track_name, track_label) in enumerate(
            zip(self.track_names, self.track_labels)
        ):
            y = ruler_bottom + 8 + row * (self.track_height + 8)
            painter.fillRect(0, y, self.width(), self.track_height, QColor(COLORS["panel_alt"]))
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
            painter.drawLine(self.left_margin, y, self.width(), y)
            painter.drawLine(self.left_margin, y, self.left_margin, y + self.track_height)
            painter.setBrush(QColor(COLORS["surface"]))
            painter.drawRoundedRect(10, y + 10, 28, 24, 5, 5)
            painter.setPen(QPen(QColor(COLORS["text"]), 1))
            painter.drawText(16, y + 27, track_name)
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(45, y + 25, track_label)
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(13, y + 47, "M   S   LOCK")
            painter.setBrush(Qt.NoBrush)

        for previous, following in v1_transition_pairs(
            self.clip_views, self.pixels_per_second, self.zoom
        ):
            gap_pixels = transition_gap_pixels(
                previous, following, self.pixels_per_second, self.zoom
            )
            transition_x = (
                self.left_margin
                + following.start * self.pixels_per_second * self.zoom
                - gap_pixels / 2
            )
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
        if not self.clip_views:
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
        for previous, following in v1_transition_pairs(
            self.clip_views, self.pixels_per_second, self.zoom
        ):
            gap_pixels = transition_gap_pixels(
                previous, following, self.pixels_per_second, self.zoom
            )
            transition_x = (
                self.left_margin
                + following.start * self.pixels_per_second * self.zoom
                - gap_pixels / 2
            )
            if abs(x - transition_x) <= 12:
                return following.start
        return None

    def find_clip_at(self, x, y):
        for row in range(len(self.track_names)):
            track_top = (
                self.header_height
                + self.ruler_height
                + 8
                + row * (self.track_height + 8)
            )
            if track_top <= y <= track_top + self.track_height:
                for view in self.clip_views:
                    if view.track_index != row:
                        continue
                    start_x = (
                        self.left_margin
                        + view.start * self.pixels_per_second * self.zoom
                    )
                    end_x = (
                        self.left_margin
                        + view.end * self.pixels_per_second * self.zoom
                    )
                    if start_x <= x <= end_x:
                        return view
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
                view = self.find_clip_at(x, y)
                if view is not None:
                    self.selected_clip_id = view.id
                    self.drag_mode = "clip"
                    self.drag_start_x = x
                    self.drag_original_start = view.start
                    self.clip_selected.emit(view.id)
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
            if self.drag_mode == "clip" and self.selected_clip_id is not None:
                delta_seconds = (event.position().x() - self.drag_start_x) / (
                    self.pixels_per_second * self.zoom
                )
                view = self.find_view_by_id(self.selected_clip_id)
                if view is not None:
                    duration = view.end - view.start
                    new_start = max(0.0, self.drag_original_start + delta_seconds)
                    # On ne mute rien : on émet juste un signal
                    # d'intention au relâchement de la souris.
                    # Pour la fluidité visuelle, on repositionne le widget
                    # sous-jacent s'il existe.
                    widget = self.clip_widgets.get(view.id)
                    if widget is not None:
                        widget.pending_start = new_start
                        widget.pending_end = new_start + duration
                        widget._apply_pending_geometry()
                    self.clip_selected.emit(view.id)
                event.accept()
                return
            if self.drag_mode == "playhead":
                self.update_playhead_from_x(event.position().x())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if (
                self.drag_mode == "clip"
                and self.selected_clip_id is not None
            ):
                view = self.find_view_by_id(self.selected_clip_id)
                if view is not None:
                    widget = self.clip_widgets.get(view.id)
                    new_start = (
                        widget.pending_start
                        if widget is not None
                        else view.start
                    )
                    self.move_clip_requested.emit(view.id, new_start)
            self.dragging_playhead = False
            self.drag_mode = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
