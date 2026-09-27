from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
    QFrame,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
)

from core.project_model import Project
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
    transition_gap_pixels,
    v1_transition_pairs,
)
from ui.i18n import translate
from ui.theme import COLORS, label_style


_TRACK_TYPE_LABELS = {
    "video": "Vidéo",
    "audio": "Audio",
    "subtitle": "Sous-titres",
}
_DEMO_MARKERS = (4.0, 9.0, 14.0)


class ClipWidget(QWidget):
    """Widget visuel représentant un TimelineClipView immuable."""

    def __init__(self, view: TimelineClipView, parent: "TimelinePanel | None" = None):
        super().__init__(parent)
        self.view = view
        # ``parent_timeline`` désigne toujours le panneau, même si
        # nous sommes hébergés par ``self.timeline_grid`` (la zone
        # scrollable interne). On remonte dans l'arbre Qt pour le
        # retrouver ; cela conserve la géométrie historique.
        cursor = parent
        while cursor is not None and not isinstance(cursor, TimelinePanel):
            cursor = cursor.parent()
        self.parent_timeline = cursor if isinstance(cursor, TimelinePanel) else None
        self.handle_width = 5
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = view.start
        self.drag_original_end = view.end
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
        parent = self.parent_timeline
        selected = (
            parent is not None and parent.selected_clip_id == self.view.id
        )
        border = COLORS["accent_hover"] if selected else "#59616F"
        self.setStyleSheet(
            f"QWidget {{ background: {self.view.color_key}; "
            f"border: 2px solid {border}; border-radius: 6px; color: white; }}"
            f"QWidget::hover {{ border-color: {COLORS['accent_hover']}; }}"
        )
        self.label.setText(self.view.label)
        self.duration_label.setText(
            parent.format_time(self.view.end - self.view.start) if parent else ""
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
        # ``self`` peut être hébergé par ``self.timeline_grid`` (zone
        # scrollable), auquel cas ``Y`` est mesuré depuis le coin haut
        # gauche de la grille (qui ne contient pas l'en-tête global).
        # On détecte ce cas en regardant le parent immédiat.
        track_top_within_self = (
            row * (parent.track_height + 8) + 8
        )
        if self.parent() is parent:
            track_top_within_self += (
                parent.header_height + parent.ruler_height + 8
            )
        self.setGeometry(
            int(start_x),
            int(track_top_within_self),
            max(int(width), 40),
            parent.track_height - 16,
        )

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        parent = self.parent_timeline
        if parent is None:
            return super().mousePressEvent(event)
        track = getattr(self.view, "track", None)
        if track and getattr(track, "locked", False):
            event.ignore()
            return
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

    def paintEvent(self, event):
        super().paintEvent(event)
        keyframes = getattr(self.view, "keyframes", None) or []
        if not keyframes:
            return
        track_type = getattr(self.view, "track_type", None)
        if track_type not in {"video", None} and not self.view.track_id.startswith("V"):
            return
        duration = max(self.view.end - self.view.start, 1e-6)
        parent = self.parent_timeline
        if parent is None:
            return
        pixels_per_second = parent.pixels_per_second * parent.zoom
        grouped: dict[float, list] = {}
        for kf in keyframes:
            grouped.setdefault(round(kf.time_seconds, 4), []).append(kf)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 4
        diamond_size = 8
        for time_seconds, items in grouped.items():
            local = max(0.0, min(duration, time_seconds))
            x = int(local * pixels_per_second)
            if x < margin or x > self.width() - margin:
                continue
            for index, kf in enumerate(sorted(items, key=lambda k: k.property_name)):
                y = (
                    self.height()
                    - margin
                    - diamond_size
                    - index * (diamond_size - 2)
                )
                polygon = QPolygonF(
                    [
                        QPointF(x, y),
                        QPointF(x + diamond_size / 2, y + diamond_size / 2),
                        QPointF(x, y + diamond_size),
                        QPointF(x - diamond_size / 2, y + diamond_size / 2),
                    ]
                )
                painter.setBrush(QColor(COLORS["accent"]))
                painter.setPen(QPen(QColor("#FFFFFF"), 1))
                painter.drawPolygon(polygon)
        painter.end()


class TrackRowHeader(QFrame):
    """En-tête visuel d'une piste, à gauche de la timeline.

    Affiche le nom, le numéro et les boutons d'action. Chaque bouton
    émet un signal haute niveau que :class:`TimelinePanel` relaie à
    ``MainWindow``. La classe dérive son style de la palette en cours
    via ``ThemeManager.apply_to``.
    """

    lock_toggled = Signal(str, bool)
    visible_toggled = Signal(str, bool)
    mute_toggled = Signal(str, bool)
    rename_requested = Signal(str)
    move_up_requested = Signal(str)
    move_down_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, track, parent=None):
        super().__init__(parent)
        self.track = track
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(56)
        self.setStyleSheet(
            f"QFrame {{ background: {COLORS['panel']}; border-right: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(2)
        prefix = "V" if track.type == "video" else (
            "A" if track.type == "audio" else "S"
        )
        title = QLabel(f"{prefix}  ·  {track.name}")
        title.setStyleSheet(label_style(11, "text", 700))
        layout.addWidget(title)
        state_parts = []
        if getattr(track, "locked", False):
            state_parts.append("🔒")
        if not getattr(track, "visible", True):
            state_parts.append("hide")
        if getattr(track, "muted", False):
            state_parts.append("🔇")
        state_label = QLabel(" ".join(state_parts) or "─")
        state_label.setStyleSheet(label_style(10, "muted", 500))
        layout.addWidget(state_label)
        # Boutons compacts en bas : on les superpose horizontalement.
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(2)
        button_row.addWidget(self._make_button(
            "🔒", lambda checked: self.lock_toggled.emit(track.id, checked),
            toggle=True, checked=getattr(track, "locked", False),
        ))
        if track.type in {"video", "subtitle"}:
            button_row.addWidget(self._make_button(
                "👁",
                lambda checked: self.visible_toggled.emit(track.id, checked),
                toggle=True,
                checked=getattr(track, "visible", True),
            ))
        if track.type == "audio":
            button_row.addWidget(self._make_button(
                "🔊",
                lambda checked: self.mute_toggled.emit(track.id, not checked),
                toggle=True,
                checked=not getattr(track, "muted", False),
            ))
        button_row.addWidget(self._make_button(
            "↑", lambda: self.move_up_requested.emit(track.id),
        ))
        button_row.addWidget(self._make_button(
            "↓", lambda: self.move_down_requested.emit(track.id),
        ))
        button_row.addWidget(self._make_button(
            "✎", lambda: self.rename_requested.emit(track.id),
        ))
        button_row.addWidget(self._make_button(
            "✕", lambda: self.remove_requested.emit(track.id),
        ))
        layout.addLayout(button_row)
        layout.addStretch()

    @staticmethod
    def _make_button(label, callback, toggle: bool = False, checked: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QToolButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; "
            f"border: 1px solid {COLORS['border']}; border-radius: 4px; font-size: 11px; }}"
            f"QToolButton:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QToolButton:checked {{ background: {COLORS['accent']}; }}"
        )
        if toggle:
            btn.setCheckable(True)
            btn.setChecked(checked)
        btn.clicked.connect(callback)
        return btn


class TimelinePanel(QWidget):
    """Timeline de Kut-Studio, pilotée par un Project.

    Architecture :

    - en-tête global (lecture, zoom, snap, compteurs, boutons
      d'ajout de piste) ;
    - zone centrale : une ``QScrollArea`` verticale unique qui contient
      la pile des pistes empilées, plus une barre d'outils flottante à
      droite pour les actions globales ;
    - chaque piste est rendue par une :class:`TrackRowHeader` (en-tête
      à gauche) et ses clips (widgets :class:`ClipWidget`).
    """

    seek_requested = Signal(float)
    clip_selected = Signal(str)
    transition_clicked = Signal(float)
    move_clip_requested = Signal(str, float)
    trim_clip_left_requested = Signal(str, float)
    trim_clip_right_requested = Signal(str, float)
    asset_dropped = Signal(str, str, float)
    add_track_requested = Signal(str)
    remove_track_requested = Signal(str)
    rename_track_requested = Signal(str, str)
    toggle_track_lock_requested = Signal(str, bool)
    toggle_track_visible_requested = Signal(str, bool)
    toggle_track_muted_requested = Signal(str, bool)
    move_track_up_requested = Signal(str)
    move_track_down_requested = Signal(str)

    def __init__(self, project: Project | None = None, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(270)
        self.setStyleSheet(f"background: {COLORS['panel_alt']}; color: {COLORS['text']};")
        self.header_height = 40
        self.ruler_height = 34
        self.track_height = 56
        self.left_margin = 110
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
        self.track_header_widgets: dict[str, TrackRowHeader] = {}
        self.dragging_playhead = False
        self.selected_clip_id: str | None = None
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = 0.0
        self.snap_enabled: bool = True
        self.snap_threshold_pixels: float = 8.0
        self.snap_line_x: float | None = None
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # En-tête global
        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(42)
        self.play_button.setToolTip(translate("tooltip.snap"))
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(
            "color: #f0f0f0; font-weight: 700; font-size: 12px;"
        )
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet("color: #a0a0a0; font-size: 12px;")
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedWidth(26)
        self.zoom_out_btn.setToolTip(translate("tooltip.zoom_out"))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet(
            "color: #dfe7ff; font-weight: 700; min-width: 48px; font-size: 11px;"
        )
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(26)
        self.zoom_in_btn.setToolTip(translate("tooltip.zoom_in"))
        for button in (self.zoom_out_btn, self.zoom_in_btn):
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['surface']}; "
                f"color: {COLORS['text']}; border: 1px solid {COLORS['border']}; "
                f"border-radius: 5px; }}"
            )
        # Boutons "Ajouter une piste".
        self.add_video_btn = QPushButton(translate("tracks.add_video"))
        self.add_video_btn.setToolTip(translate("tooltip.add_video"))
        self.add_audio_btn = QPushButton(translate("tracks.add_audio"))
        self.add_audio_btn.setToolTip(translate("tooltip.add_audio"))
        self.add_subtitle_btn = QPushButton(translate("tracks.add_subtitle"))
        self.add_subtitle_btn.setToolTip(translate("tooltip.add_subtitle"))
        for btn in (
            self.add_video_btn,
            self.add_audio_btn,
            self.add_subtitle_btn,
        ):
            btn.setStyleSheet(
                f"QPushButton {{ background: {COLORS['accent_dark']}; "
                f"color: {COLORS['text']}; border: 1px solid {COLORS['accent']}; "
                f"border-radius: 5px; padding: 4px 8px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {COLORS['accent']}; }}"
            )
        self.snap_button = QPushButton("🧲")
        self.snap_button.setCheckable(True)
        self.snap_button.setChecked(True)
        self.snap_button.setFixedWidth(34)
        self.snap_button.setToolTip(translate("tooltip.snap"))
        self.snap_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent_dark']}; "
            f"color: {COLORS['text']}; border: 1px solid {COLORS['accent']}; "
            f"border-radius: 5px; }}"
            f"QPushButton:checked {{ background: {COLORS['accent']}; }}"
            f"QPushButton:hover {{ background: {COLORS['accent']}; }}"
        )
        self.snap_button.toggled.connect(self.set_snap_enabled)
        self.header = QWidget(self)
        self.header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 6, 10, 6)
        header_layout.addWidget(self.play_button)
        header_layout.addWidget(self.time_label)
        header_layout.addSpacing(12)
        header_layout.addWidget(self.snap_button)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.add_video_btn)
        header_layout.addWidget(self.add_audio_btn)
        header_layout.addWidget(self.add_subtitle_btn)
        header_layout.addStretch()
        header_layout.addWidget(self.total_time_label)
        header_layout.addSpacing(8)
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
        self.add_video_btn.clicked.connect(
            lambda: self.add_track_requested.emit("video")
        )
        self.add_audio_btn.clicked.connect(
            lambda: self.add_track_requested.emit("audio")
        )
        self.add_subtitle_btn.clicked.connect(
            lambda: self.add_track_requested.emit("subtitle")
        )
        outer.addWidget(self.header)

        # Zone centrale : QScrollArea verticale contenant la grille.
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.timeline_grid = QWidget()
        self.timeline_grid.setMinimumWidth(self.left_margin + 1600)
        self.timeline_grid.setStyleSheet(
            f"background: {COLORS['panel_alt']};"
        )
        self.scroll.setWidget(self.timeline_grid)
        outer.addWidget(self.scroll, 1)
        self.refresh_clip_widgets()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        self.project = project
        self._refresh_track_metadata()
        self.clip_views = build_clip_views(project)
        self.selected_clip_id = None
        self.refresh_clip_widgets()
        self.update()

    def find_view_by_id(self, clip_id: str) -> TimelineClipView | None:
        for view in self.clip_views:
            if view.id == clip_id:
                return view
        return None

    def select_clip(self, clip_id: str) -> None:
        self.selected_clip_id = clip_id
        self.clip_selected.emit(clip_id)
        self.refresh_clip_widgets()

    # ------------------------------------------------------------------
    # Snapping magnétique
    # ------------------------------------------------------------------

    def set_snap_enabled(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)
        if not enabled:
            self.snap_line_x = None
            self.update()

    def snap_position(
        self,
        proposed_position: float,
        excluded_clip_id: str | None = None,
    ) -> tuple[float, float | None]:
        from core.timeline_operations import snap_timeline_position

        self.snap_line_x = None
        if not self.snap_enabled:
            return proposed_position, None
        threshold_seconds = self.snap_threshold_pixels / (
            self.pixels_per_second * self.zoom
        )
        snapped = snap_timeline_position(
            self.project,
            proposed_position,
            threshold_seconds,
            excluded_clip_id=excluded_clip_id,
            playhead_seconds=self.playhead_seconds,
        )
        if abs(snapped - proposed_position) > 1e-6:
            self.snap_line_x = (
                self.left_margin + snapped * self.pixels_per_second * self.zoom
            )
        return snapped, self.snap_line_x

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
        # Nettoyer les anciens headers de pistes.
        for header in list(self.track_header_widgets.values()):
            header.setParent(None)
            header.deleteLater()
        self.track_header_widgets.clear()
        # Nettoyer les anciens clips retirés du projet.
        current_ids = {view.id for view in self.clip_views}
        for clip_id, widget in list(self.clip_widgets.items()):
            if clip_id not in current_ids:
                widget.deleteLater()
                del self.clip_widgets[clip_id]
        # Dimensionner la grille intérieure pour au moins toutes les pistes.
        track_count = max(
            len(self.project.tracks) if self.project else 1, 1
        )
        rows_height = (self.track_height + 8) * track_count + 24
        self.timeline_grid.setMinimumHeight(int(rows_height))
        # Positionner les en-têtes de pistes dans la grille.
        if self.project is not None:
            for index, track in enumerate(self.project.tracks):
                header = TrackRowHeader(track, self.timeline_grid)
                # La grille n'a pas d'en-tête global : on inclut
                # directement la ruler_height dans l'offset vertical.
                header_top = (
                    self.ruler_height
                    + 8
                    + index * (self.track_height + 8)
                )
                header.setGeometry(
                    0,
                    int(header_top),
                    self.left_margin,
                    self.track_height,
                )
                header.show()
                header.lock_toggled.connect(self.toggle_track_lock_requested)
                header.visible_toggled.connect(self.toggle_track_visible_requested)
                header.mute_toggled.connect(self.toggle_track_muted_requested)
                header.move_up_requested.connect(self.move_track_up_requested)
                header.move_down_requested.connect(self.move_track_down_requested)
                header.remove_requested.connect(self.remove_track_requested)
                header.rename_requested.connect(self._on_rename_requested)
                self.track_header_widgets[track.id] = header
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None:
                widget = ClipWidget(view, self.timeline_grid)
                self.clip_widgets[view.id] = widget
            row = view.track_index
            track_top = (
                self.ruler_height
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
        self.timeline_grid.update()

    def _on_rename_requested(self, track_id: str) -> None:
        """Demande un nouveau nom à l'utilisateur et relaie vers MainWindow."""
        if self.project is None:
            return
        track = next((t for t in self.project.tracks if t.id == track_id), None)
        if track is None:
            return
        from PySide6.QtWidgets import QInputDialog

        new_name, accepted = QInputDialog.getText(
            self,
            translate("action.preferences"),
            translate("tracks.rename"),
            text=track.name,
        )
        if accepted and new_name and new_name != track.name:
            self.rename_track_requested.emit(track_id, new_name.strip())

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def zoom_out(self) -> None:
        self.zoom = max(0.25, self.zoom / 1.25)
        self._update_zoom_label()
        self.refresh_clip_widgets()

    def zoom_in(self) -> None:
        self.zoom = min(8.0, self.zoom * 1.25)
        self._update_zoom_label()
        self.refresh_clip_widgets()

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        if event.key() == Qt.Key_Minus:
            self.zoom_out()
            event.accept()
            return
        super().keyPressEvent(event)

    def setDuration(self, duration_ms):
        self.set_timeline_duration(float(duration_ms))

    def set_timeline_duration(self, duration_seconds: float) -> None:
        duration_seconds = max(0.0, float(duration_seconds))
        self.duration_seconds = max(duration_seconds, 1.0)
        self.total_time_label.setText(f"/ {self.format_time(self.duration_seconds)}")
        self.update()

    def setPlaybackPosition(self, position_ms):
        self.set_playhead_seconds(float(position_ms))

    def set_playhead_seconds(self, position_seconds: float) -> None:
        self.playhead_seconds = min(
            max(float(position_seconds), 0.0), self.duration_seconds
        )
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
        painter.fillRect(
            0, ruler_top, self.width(), self.ruler_height, QColor(COLORS["surface"])
        )
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(self.left_margin, ruler_top, self.width(), ruler_top)
        painter.drawLine(self.left_margin, ruler_bottom, self.width(), ruler_bottom)
        major_ticks = max(1, int(self.duration_seconds) + 1)
        painter.setPen(QPen(QColor(COLORS["muted"]), 1))
        font_metrics = QFontMetrics(painter.font())
        for second in range(0, major_ticks + 1):
            x = self.left_margin + second * self.pixels_per_second * self.zoom
            if x > self.width():
                break
            painter.drawLine(int(x), int(ruler_bottom - 8), int(x), int(ruler_bottom))
            label = self.format_time(second)
            painter.setPen(QPen(QColor(COLORS["muted"]), 1))
            painter.drawText(int(x) + 3, int(ruler_top + 12), label)
        if self.snap_line_x is not None:
            sx = int(self.snap_line_x)
            painter.setPen(QPen(QColor(COLORS["accent"]), 1))
            painter.drawLine(sx, int(ruler_top + 4), sx, self.height())
        for marker_seconds in self.markers:
            if marker_seconds > self.duration_seconds:
                break
            mx = self.left_margin + marker_seconds * self.pixels_per_second * self.zoom
            if mx > self.width():
                break
            painter.setPen(QPen(QColor(COLORS["accent"]), 1))
            painter.drawLine(int(mx), ruler_bottom, int(mx), self.height())
        playhead_x = (
            self.left_margin
            + self.playhead_seconds * self.pixels_per_second * self.zoom
        )
        painter.setPen(QPen(QColor(COLORS["success"]), 2))
        painter.drawLine(int(playhead_x), ruler_top - 4, int(playhead_x), self.height())
        gap_pixels = transition_gap_pixels(self.project, self.pixels_per_second, self.zoom)
        if gap_pixels is not None:
            x_gap_start, x_gap_end = gap_pixels
            painter.setPen(QPen(QColor(COLORS["success"]), 2))
            painter.drawLine(int(x_gap_start), int(ruler_top - 6), int(x_gap_start), self.height())
            painter.drawLine(int(x_gap_end), int(ruler_top - 6), int(x_gap_end), self.height())
            painter.setPen(QPen(QColor(COLORS["success"]), 1))
            painter.drawText(
                int(x_gap_start + 6), int(ruler_top + 14), "FONDO"
            )
        for pair in v1_transition_pairs(self.project):
            pass
