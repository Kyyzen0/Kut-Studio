"""Timeline de Kut-Studio (refonte UI/UX majeure).

Architecture (cohérente avec le moteur existant) :

- en-tête global (lecture, zoom, snap, ajout de pistes) ;
- zone centrale : une ``QScrollArea`` qui contient la grille
  (``timeline_grid``). La grille gère elle-même ses pistes, ses
  en-têtes et ses clips en tant qu'enfants positionnés manuellement.

Tous les labels et boutons d'action utilisent des icônes SVG
cohérentes (:mod:`ui.icons`) ; aucun emoji n'apparaît dans
l'interface.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.project_model import Project, Track
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
    v1_transition_pairs,
)
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import ThemePalette, label_style


_TRACK_TYPE_LABELS = {
    "video": "Vidéo",
    "audio": "Audio",
    "subtitle": "Sous-titres",
}
_DEMO_MARKERS = (4.0, 9.0, 14.0)


def _color_for_track_type(track_type: str, palette: ThemePalette) -> str:
    """Couleur d'accent utilisée pour la pastille de type de piste."""
    mapping = {
        "video": palette.track_video,
        "audio": palette.track_audio,
        "subtitle": palette.track_subtitle,
    }
    return mapping.get(track_type, palette.accent)


# ---------------------------------------------------------------------------
# En-tête de piste (à gauche des pistes)
# ---------------------------------------------------------------------------


class TrackRowHeader(QFrame):
    """En-tête visuel d'une piste, à gauche de la timeline.

    Affiche le nom, le type de piste et un jeu de boutons d'action
    essentiels (``lock``, ``visible``, ``mute``, ``up``, ``down``,
    ``rename``, ``remove``). Chaque bouton émet un signal
    haute-niveau relayé par :class:`TimelinePanel` à ``MainWindow``.
    """

    lock_toggled = Signal(str, bool)
    visible_toggled = Signal(str, bool)
    mute_toggled = Signal(str, bool)
    rename_requested = Signal(str)
    move_up_requested = Signal(str)
    move_down_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, track: Track, parent=None) -> None:
        super().__init__(parent)
        self.track = track
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(Sizes.timeline_track_height + 6)
        # Fond du panneau + filet de séparation bas : donne une limite
        # franche à chaque piste même quand les pistes sont serrées.
        self.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
            f"QFrame#trackHeader {{ background: {_current_palette().track_header_bg};"
            f" border: none; border-bottom: 1px solid"
            f" {_current_palette().track_divider}; }}"
        )
        self.setObjectName("trackHeader")

        from PySide6.QtWidgets import QHBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            Spacing.md, Spacing.sm, Spacing.md, Spacing.sm
        )
        outer.setSpacing(Spacing.xs)

        # ----- Ligne 1 : pastille + nom + état ------------------------
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(Spacing.sm)

        track_color = _color_for_track_type(track.type, _current_palette())
        self._swatch = QWidget()
        self._swatch.setProperty("track_swatch", True)
        self._swatch.setFixedSize(4, 32)
        self._swatch.setStyleSheet(
            f"background: {track_color}; border-radius: 2px;"
        )
        title_layout.addWidget(self._swatch)

        name_box = QWidget()
        name_layout = QVBoxLayout(name_box)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(0)

        prefix = self._prefix_for_type(track.type)
        title = QLabel(f"{prefix} · {track.name}")
        title.setStyleSheet(label_style(13, "text", 700))
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_layout.addWidget(title)

        state_parts: list[str] = []
        if getattr(track, "locked", False):
            state_parts.append("Verrouillée")
        if not getattr(track, "visible", True):
            state_parts.append("Masquée")
        if getattr(track, "muted", False):
            state_parts.append("Muette")
        state_label = QLabel(" · ".join(state_parts) or "Active")
        state_label.setStyleSheet(label_style(10, "muted", 500))
        name_layout.addWidget(state_label)
        title_layout.addWidget(name_box, 1)

        outer.addWidget(title_row)

        # ----- Ligne 2 : boutons d'action -----------------------------
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(Spacing.xs)
        button_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._add_action_buttons(button_layout, track)
        outer.addWidget(button_row)

    def _add_action_buttons(self, layout, track) -> None:
        """Ajoute les boutons d'action dans ``layout`` (horizontal)."""

        def _btn(
            icon: IconName,
            tooltip: str,
            callback,
            *,
            checkable: bool = False,
            checked: bool = False,
        ) -> IconButton:
            button = IconButton(
                icon=icon,
                tooltip=tooltip,
                checkable=checkable,
                checked=checked,
                size=Sizes.icon_button_sm,
            )
            button.clicked.connect(callback)
            return button

        # Bouton "état" du type de piste (œil / son / sous-titre).
        if track.type == "video":
            state_btn = _btn(
                IconName.EYE if getattr(track, "visible", True) else IconName.EYE_OFF,
                translate("tracks.visible_tooltip"),
                lambda checked: self.visible_toggled.emit(track.id, checked),
                checkable=True,
                checked=getattr(track, "visible", True),
            )
        elif track.type == "audio":
            state_btn = _btn(
                IconName.SPEAKER if not getattr(track, "muted", False) else IconName.MUTE,
                translate("tracks.mute_tooltip"),
                lambda checked: self.mute_toggled.emit(track.id, not checked),
                checkable=True,
                checked=not getattr(track, "muted", False),
            )
        else:
            state_btn = _btn(
                IconName.SUBTITLE,
                translate("tracks.visible_tooltip"),
                lambda checked: None,
            )
            state_btn.setEnabled(False)
        layout.addWidget(state_btn)

        # Bouton de verrouillage.
        lock_btn = _btn(
            IconName.LOCK if getattr(track, "locked", False) else IconName.UNLOCK,
            translate("tracks.lock_tooltip"),
            lambda checked: self.lock_toggled.emit(track.id, checked),
            checkable=True,
            checked=getattr(track, "locked", False),
        )
        layout.addWidget(lock_btn)

        layout.addSpacing(Spacing.sm)

        # Boutons de réorganisation (haut / bas).
        up_btn = _btn(
            IconName.ARROW_UP,
            translate("tracks.up_tooltip"),
            lambda: self.move_up_requested.emit(track.id),
        )
        down_btn = _btn(
            IconName.ARROW_DOWN,
            translate("tracks.down_tooltip"),
            lambda: self.move_down_requested.emit(track.id),
        )
        layout.addWidget(up_btn)
        layout.addWidget(down_btn)

        layout.addSpacing(Spacing.sm)

        # Renommer + supprimer (boutons secondaires).
        rename_btn = _btn(
            IconName.EDIT,
            translate("tracks.rename_tooltip"),
            lambda: self.rename_requested.emit(track.id),
        )
        remove_btn = _btn(
            IconName.TRASH,
            translate("tracks.delete_tooltip"),
            lambda: self.remove_requested.emit(track.id),
        )
        layout.addWidget(rename_btn)
        layout.addWidget(remove_btn)

    @staticmethod
    def _prefix_for_type(track_type: str) -> str:
        return {"video": "V", "audio": "A", "subtitle": "S"}.get(track_type, "T")

    def refresh_state(self, palette: ThemePalette) -> None:
        """Met à jour la pastille de type si la palette change."""
        swatch_color = _color_for_track_type(self.track.type, palette)
        if hasattr(self, "_swatch") and self._swatch is not None:
            self._swatch.setStyleSheet(
                f"background: {swatch_color}; border-radius: 2px;"
            )


# Petit helper pour accéder à la palette courante (utilisée par les
# en-têtes de piste pour récupérer les couleurs d'accent sans avoir à
# leur passer une dépendance explicite).
def _current_palette() -> ThemePalette:
    """Retourne la palette active de l'application.

    La résolution passe toujours par :mod:`ui.theme`, ce qui garantit que
    la timeline reste cohérente avec le thème courant (et pas figée sur
    le thème sombre au premier construit).
    """
    return _resolve_current_palette()


# ---------------------------------------------------------------------------
# Clip widget
# ---------------------------------------------------------------------------


class ClipWidget(QWidget):
    """Widget visuel représentant un :class:`TimelineClipView` immuable."""

    def __init__(self, view: TimelineClipView, parent: "TimelinePanel | None" = None):
        super().__init__(parent)
        self.view = view
        cursor = parent
        while cursor is not None and not isinstance(cursor, TimelinePanel):
            cursor = cursor.parent()
        self.parent_timeline = cursor if isinstance(cursor, TimelinePanel) else None
        self.handle_width = 8
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_original_start = view.start
        self.drag_original_end = view.end
        self.pending_start = view.start
        self.pending_end = view.end
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Label du clip (nom).
        self.label = QLabel(self.view.label, self)
        self.label.setStyleSheet(
            "color: white; font-weight: 700; font-size: 12px; background: transparent;"
        )
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.duration_label = QLabel(
            self.parent_timeline.format_time(self.view.end - self.view.start) if self.parent_timeline else "",
            self,
        )
        self.duration_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.78); font-size: 11px; background: transparent;"
        )
        self.duration_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.refresh_style()

    def refresh_style(self) -> None:
        parent = self.parent_timeline
        palette = _current_palette()
        selected = (
            parent is not None and parent.selected_clip_id == self.view.id
        )
        border = palette.clip_border_selected if selected else palette.clip_border
        track_type = getattr(self.view, "track_type", None)
        base_color = _color_for_track_type(track_type or "video", palette)
        # Le sélecteur est limité au corps du clip : un ``QWidget`` nu
        # peindrait aussi les libellés enfants (nom, durée), qui
        # apparaissaient alors comme des blocs colorés.
        self.setObjectName("clipBody")
        self.setStyleSheet(
            f"QWidget#clipBody {{ background: {self.view.color_key}; "
            f"border: 2px solid {border}; border-radius: 6px; }}"
            f"QLabel {{ background: transparent; border: none; "
            f"color: {palette.clip_text}; }}"
        )
        # Mémorise la couleur de la pastille de type pour le rendu.
        self._track_accent = base_color
        self.label.setText(self.view.label)
        self.duration_label.setText(
            parent.format_time(self.view.end - self.view.start) if parent else ""
        )
        # Repositionne les labels au cas où la géométrie a changé.
        self._layout_labels()

    def _layout_labels(self) -> None:
        if not hasattr(self, "label") or self.label is None:
            return
        self.label.move(10, 6)
        self.duration_label.move(10, self.height() - 18)
        self.label.resize(min(self.label.sizeHint().width(), self.width() - 20), 16)
        self.duration_label.resize(
            min(self.duration_label.sizeHint().width(), self.width() - 20), 14
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_labels()

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
        track_top_within_self = row * (parent.track_height + parent.track_gap) + parent.ruler_height + parent.track_gap
        if self.parent() is parent:
            track_top_within_self += parent.header_height + 8
        self.setGeometry(
            int(start_x),
            int(track_top_within_self),
            max(int(width), 40),
            parent.track_height,
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
        margin = 5
        diamond_size = 10
        palette = _current_palette()
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
                painter.setBrush(QColor(palette.diamond_filled))
                painter.setPen(QPen(QColor(palette.diamond_border), 1))
                painter.drawPolygon(polygon)
        painter.end()


# ---------------------------------------------------------------------------
# Grille de pistes
# ---------------------------------------------------------------------------


class _TrackGrid(QWidget):
    """Surface portant les pistes.

    Peint les bandes de piste en alternance et un filet de séparation
    sous chaque piste. Le rendu se fait ici — et non dans
    :class:`TimelinePanel` — pour rester synchrone avec le défilement
    vertical et horizontal de la zone.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.track_count = 0
        self.track_height = 0
        self.track_gap = 0
        self.ruler_height = 0
        self.left_margin = 0

    def configure(
        self,
        *,
        track_count: int,
        track_height: int,
        track_gap: int,
        ruler_height: int,
        left_margin: int,
    ) -> None:
        self.track_count = max(0, track_count)
        self.track_height = track_height
        self.track_gap = track_gap
        self.ruler_height = ruler_height
        self.left_margin = left_margin
        self.update()

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(palette.timeline_grid))

        base = palette.timeline_grid
        alt = palette.track_alt_bg
        divider = palette.track_divider
        pitch = self.track_height + self.track_gap
        first_row = self.ruler_height + 8
        lane_left = self.left_margin

        for index in range(self.track_count):
            top = first_row + index * pitch
            if top > self.height():
                break
            height = min(self.track_height, self.height() - top)
            if height <= 0:
                break
            if index % 2 == 1:
                painter.fillRect(
                    lane_left,
                    int(top),
                    self.width() - lane_left,
                    int(height),
                    QColor(alt),
                )
            # Filet bas : sépare nettement chaque piste.
            painter.setPen(QPen(QColor(divider), 1))
            line_y = int(top + self.track_height)
            painter.drawLine(
                lane_left, line_y, self.width(), line_y
            )
        painter.end()


# ---------------------------------------------------------------------------
# TimelinePanel
# ---------------------------------------------------------------------------


class TimelinePanel(QWidget):
    """Timeline de Kut-Studio, pilotée par un ``Project``.

    Le panneau orchestre :

    - une barre d'outils supérieure (lecture, zoom, snap, ajout de pistes) ;
    - une barre de statut (compteurs, durée totale, version) ;
    - une zone défilante avec les en-têtes de pistes à gauche et les
      clips à droite.

    L'API publique est compatible avec l'existant (signaux, méthodes,
    attributs ``play_button``, ``time_label``, ``total_time_label``,
    ``clip_count_label``, ``zoom_label``, ``playhead_seconds``,
    ``duration_seconds``...).
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
        self.setMinimumHeight(Sizes.timeline_min_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"QWidget#timeline_panel {{ background: "
            f"{_current_palette().timeline_bg}; color: "
            f"{_current_palette().text}; }}"
        )
        self.setObjectName("timeline_panel")

        # Dimensions configurables de la timeline.
        self.header_height = Sizes.timeline_header_height
        self.ruler_height = Sizes.timeline_ruler_height
        self.track_height = Sizes.timeline_track_height
        self.track_gap = 6
        self.left_margin = Sizes.timeline_left_margin
        self.zoom = 1.0
        self.duration_seconds = 30.0
        self.playhead_seconds = 0.0
        self.pixels_per_second = 120.0

        # État métier.
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

        # Les couleurs sont résolues à la demande via
        # ``_current_palette()`` : aucun cache à invalider lors d'un
        # changement de thème. L'abonnement se fait depuis MainWindow
        # via ``subscribe_to_theme``.
        self._theme_manager = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_toolbar())

        # Zone défilante centrale.
        # Le fond doit être posé explicitement sur la grille ET sur le
        # viewport : sans cela Qt utilise le fond clair par défaut de la
        # plateforme et la zone des pistes apparaît blanchie.
        surface = _current_palette().timeline_grid
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.viewport().setStyleSheet(
            f"QWidget {{ background: {surface}; }}"
        )
        self.timeline_grid = _TrackGrid()
        self.timeline_grid.setMinimumWidth(self.left_margin + 1600)
        self.timeline_grid.setStyleSheet(
            f"QWidget {{ background: {surface}; }}"
        )
        self.scroll.setWidget(self.timeline_grid)
        outer.addWidget(self.scroll, 1)
        self.refresh_clip_widgets()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        """Construit la barre d'outils supérieure (transport + actions)."""
        from PySide6.QtWidgets import QHBoxLayout
        from ui.theme import ThemePalette
        palette = _current_palette()

        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setObjectName("timeline_toolbar")
        bar.setStyleSheet(
            f"QWidget#timeline_toolbar {{ background: {palette.panel}; "
            f"border-bottom: 1px solid {palette.border}; }}"
        )
        # Layout principal : horizontal. Une rangée unique, dense et lisible.
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.lg, Spacing.sm, Spacing.lg, Spacing.sm)
        layout.setSpacing(Spacing.md)
        layout.setAlignment(Qt.AlignVCenter)

        # --- Bloc gauche : transport + horloge -------------------------
        left_block = QWidget()
        left_layout = QHBoxLayout(left_block)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(Spacing.sm)
        left_layout.setAlignment(Qt.AlignVCenter)

        self.play_button = IconButton(
            icon=IconName.PLAY,
            tooltip="Lecture / Pause",
            accent=True,
            size=Sizes.icon_button,
        )
        self.play_button.clicked.connect(self._on_play_clicked)
        left_layout.addWidget(self.play_button)

        time_box = QWidget()
        time_layout = QVBoxLayout(time_box)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(
            f"color: {palette.text}; font-weight: 700; font-size: 14px;"
        )
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px;"
        )
        time_layout.addWidget(self.time_label)
        time_layout.addWidget(self.total_time_label)
        left_layout.addWidget(time_box)

        # Petit séparateur vertical pour aérer visuellement.
        left_layout.addSpacing(Spacing.sm)

        self.snap_button = IconButton(
            icon=IconName.SNAP,
            tooltip=translate("tooltip.snap"),
            checkable=True,
            checked=True,
            size=Sizes.icon_button,
        )
        self.snap_button.toggled.connect(self.set_snap_enabled)
        left_layout.addWidget(self.snap_button)

        layout.addWidget(left_block)

        # --- Bloc central : ajout de pistes ------------------------------
        center_block = QWidget()
        center_layout = QHBoxLayout(center_block)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(Spacing.sm)
        center_layout.setAlignment(Qt.AlignVCenter)

        self.add_video_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_video"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_video_btn.setText(f"  {translate('tracks.add_video')}")
        self.add_video_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_audio_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_audio"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_audio_btn.setText(f"  {translate('tracks.add_audio')}")
        self.add_audio_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_subtitle_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_subtitle"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_subtitle_btn.setText(f"  {translate('tracks.add_subtitle')}")
        self.add_subtitle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        center_layout.addWidget(self.add_video_btn)
        center_layout.addWidget(self.add_audio_btn)
        center_layout.addWidget(self.add_subtitle_btn)
        layout.addWidget(center_block)

        # --- Bloc extensible (vide pour l'instant) ----------------------
        layout.addStretch(1)

        # --- Bloc droite : statut + zoom ---------------------------------
        right_block = QWidget()
        right_layout = QHBoxLayout(right_block)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(Spacing.md)
        right_layout.setAlignment(Qt.AlignVCenter)

        status_box = QWidget()
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        status_layout.setAlignment(Qt.AlignRight)
        self.clip_count_label = QLabel()
        self.clip_count_label.setStyleSheet(label_style(11, "muted", 500))
        self.clip_count_label.setAlignment(Qt.AlignRight)
        status_layout.addWidget(self.clip_count_label)
        self.version_label = QLabel("KUT-STUDIO")
        self.version_label.setStyleSheet(label_style(10, "muted", 700))
        self.version_label.setAlignment(Qt.AlignRight)
        status_layout.addWidget(self.version_label)
        right_layout.addWidget(status_box)

        zoom_box = QWidget()
        zoom_layout = QHBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(Spacing.xs)
        zoom_layout.setAlignment(Qt.AlignVCenter)
        self.zoom_out_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.zoom_out"),
            size=Sizes.icon_button_sm,
        )
        self.zoom_out_btn.setIcon(_minus_icon())
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet(
            f"color: {palette.text}; font-weight: 700; min-width: 48px; "
            f"font-size: 11px;"
        )
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.zoom_in"),
            size=Sizes.icon_button_sm,
        )
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self.zoom_in_btn)
        right_layout.addWidget(zoom_box)

        layout.addWidget(right_block)
        return bar

    def _on_play_clicked(self) -> None:
        # Émet le signal handled by MainWindow.
        if hasattr(self, "play_requested"):
            self.play_requested.emit()

    def subscribe_to_theme(self, manager) -> None:
        """Abonne la timeline aux changements de palette de ``manager``.

        Appelé par :class:`~ui.main_window.MainWindow` avec *son*
        gestionnaire de thème. On ne crée volontairement pas de
        ``ThemeManager`` ici : une instance parasite publierait une
        palette par défaut (sombre) et écraserait le thème réel de
        l'application.
        """
        self._theme_manager = manager
        manager.subscribe(self._on_palette_changed)

    def _on_palette_changed(self, manager) -> None:
        # La palette active est déjà publiée par ``ThemeManager`` :
        # on se contente de rafraîchir ce qui est peint à la main.
        try:
            palette = manager.effective_palette
        except Exception:
            palette = _current_palette()
        # Rafraîchit les en-têtes de pistes (pastilles type) et les clips.
        for header in self.track_header_widgets.values():
            header.refresh_state(palette)
        for widget in self.clip_widgets.values():
            widget.refresh_style()
        self._restyle_toolbar(palette)
        self.update()

    def _restyle_toolbar(self, palette) -> None:
        bar = self.findChild(QWidget, "timeline_toolbar")
        if bar is not None:
            bar.setStyleSheet(
                f"QWidget#timeline_toolbar {{ background: {palette.panel}; "
                f"border-bottom: 1px solid {palette.border}; }}"
            )

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

        # Dimensionner la grille intérieure pour toutes les pistes.
        track_count = max(
            len(self.project.tracks) if self.project else 1, 1
        )
        ruler_and_padding = self.ruler_height + 8
        rows_height = ruler_and_padding + (self.track_height + self.track_gap) * track_count + 16
        self.timeline_grid.setMinimumHeight(int(rows_height))
        # La grille doit connaître la géométrie des pistes pour peindre
        # les bandes alternées et les séparateurs.
        self.timeline_grid.configure(
            track_count=track_count,
            track_height=self.track_height,
            track_gap=self.track_gap,
            ruler_height=self.ruler_height,
            left_margin=self.left_margin,
        )

        # Positionner les en-têtes de pistes (à gauche, dans la grille).
        if self.project is not None:
            for index, track in enumerate(self.project.tracks):
                header = TrackRowHeader(track, self.timeline_grid)
                header_top = (
                    self.ruler_height
                    + 8
                    + index * (self.track_height + self.track_gap)
                )
                header.setGeometry(
                    0,
                    int(header_top),
                    self.left_margin,
                    self.track_height + self.track_gap,
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

        # Positionner les clips (dans la zone défilante de la timeline).
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None:
                widget = ClipWidget(view, self.timeline_grid)
                self.clip_widgets[view.id] = widget
            row = view.track_index
            track_top = (
                self.ruler_height
                + 8
                + row * (self.track_height + self.track_gap)
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
                self.track_height,
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
        from ui.icons import make_icon
        if is_playing:
            self.play_button.setIcon(make_icon(IconName.PAUSE, size=Iconography.md))
        else:
            self.play_button.setIcon(make_icon(IconName.PLAY, size=Iconography.md))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = _current_palette()
        painter.fillRect(self.rect(), QColor(palette.timeline_bg))

        # Bande d'outils en haut.
        painter.fillRect(0, 0, self.width(), self.header_height, QColor(palette.panel))
        painter.setPen(QPen(QColor(palette.border), 1))
        painter.drawLine(0, self.header_height, self.width(), self.header_height)

        # Règle temporelle sous la toolbar (en haut de la zone scrollable).
        ruler_top = self.header_height + 8
        ruler_bottom = ruler_top + self.ruler_height
        painter.fillRect(
            0, ruler_top, self.width(), self.ruler_height,
            QColor(palette.ruler_bg),
        )
        painter.setPen(QPen(QColor(palette.ruler_line), 1))
        painter.drawLine(self.left_margin, ruler_top, self.width(), ruler_top)
        painter.drawLine(self.left_margin, ruler_bottom, self.width(), ruler_bottom)
        major_ticks = max(1, int(self.duration_seconds) + 1)
        font_metrics = QFontMetrics(painter.font())
        for second in range(0, major_ticks + 1):
            x = self.left_margin + second * self.pixels_per_second * self.zoom
            if x > self.width():
                break
            painter.drawLine(int(x), int(ruler_bottom - 8), int(x), int(ruler_bottom))
            label = self.format_time(second)
            painter.setPen(QPen(QColor(palette.muted), 1))
            painter.drawText(int(x) + 4, int(ruler_top + 12), label)

        # Ligne de snap.
        if self.snap_line_x is not None:
            sx = int(self.snap_line_x)
            painter.setPen(QPen(QColor(palette.snap_line), 1))
            painter.drawLine(sx, int(ruler_top + 4), sx, self.height())

        # Marqueurs.
        for marker_seconds in self.markers:
            if marker_seconds > self.duration_seconds:
                break
            mx = self.left_margin + marker_seconds * self.pixels_per_second * self.zoom
            if mx > self.width():
                break
            painter.setPen(QPen(QColor(palette.marker), 1))
            painter.drawLine(int(mx), ruler_bottom, int(mx), self.height())

        # Tête de lecture.
        playhead_x = (
            self.left_margin
            + self.playhead_seconds * self.pixels_per_second * self.zoom
        )
        painter.setPen(QPen(QColor(palette.playhead), 2))
        painter.drawLine(int(playhead_x), ruler_top - 4, int(playhead_x), self.height())

        # Paires de transitions (fondu entre clips sur V1).
        for previous, following in v1_transition_pairs(
            self.clip_views, self.pixels_per_second, self.zoom
        ):
            x_gap_start = (
                self.left_margin
                + previous.end * self.pixels_per_second * self.zoom
            )
            x_gap_end = (
                self.left_margin
                + following.start * self.pixels_per_second * self.zoom
            )
            painter.setPen(QPen(QColor(palette.transition_overlay), 1))
            painter.drawLine(int(x_gap_start), int(ruler_top - 6), int(x_gap_start), self.height())
            painter.drawLine(int(x_gap_end), int(ruler_top - 6), int(x_gap_end), self.height())
            painter.setPen(QPen(QColor(palette.transition_overlay), 1))
            painter.drawText(
                int(x_gap_start + 6), int(ruler_top + 14), "FONDU"
            )
        painter.end()


# Petite icône « − » réutilisée pour le bouton Zoom-.
def _minus_icon():
    from PySide6.QtGui import QIcon
    from ui.icons import make_icon
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 12h14"/></svg>'
    )
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    icon = QIcon()
    for dpr in (1.0, 2.0):
        s = max(1, int(round(Iconography.md * dpr)))
        pixmap = QPixmap(s, s)
        pixmap.fill(Qt.transparent)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _resolve_current_palette():
    """Retourne la palette active de l'application.

    La palette publiée par :func:`ui.theme.set_active_palette` est la
    source de vérité : la timeline suit ainsi le thème courant au lieu
    d'être figée sur le thème sombre.
    """
    try:
        from ui.theme import active_palette

        return active_palette()
    except Exception:  # pragma: no cover - garde-fou
        from ui.theme import ThemePalette

        return ThemePalette()


__all__ = [
    "ClipWidget",
    "TimelinePanel",
    "TrackRowHeader",
]