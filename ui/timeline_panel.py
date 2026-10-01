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


from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.project_model import Project
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
)
from ui.timeline_ruler import TimelineRuler
from ui.design_system import Sizes
from ui.timeline_panel_mixins.toolbar import ToolbarMixin
from ui.timeline_panel_mixins.layout import LayoutMixin
from ui.timeline_panel_mixins.zoom_playhead import ZoomPlayheadMixin
from ui.timeline_panel_mixins.selection import SelectionMixin
from ui.timeline_panel_mixins.drag_tools import DragToolsMixin
from ui.timeline_panel_mixins.previews import PreviewsMixin
from ui.timeline_widgets.clip_widget import ClipWidget
from ui.timeline_widgets.common import (  # noqa: F401 - réexports de compatibilité
    _COLLAPSED_HEIGHT,
    _CONTENT_TOP,
    _HEIGHTS,
    _TRACK_TYPE_LABELS,
    _color_for_track_type,
    _current_palette,
    _minus_icon,
    _resolve_current_palette,
)
from ui.timeline_widgets.track_grid import _TrackGrid
from ui.timeline_widgets.track_header import TrackRowHeader
from ui.timeline_widgets.transition_marker import TransitionMarkerWidget


# ---------------------------------------------------------------------------
# TimelinePanel
# ---------------------------------------------------------------------------


class TimelinePanel(ToolbarMixin, LayoutMixin, ZoomPlayheadMixin, SelectionMixin, DragToolsMixin, PreviewsMixin, QWidget):
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

    # Reconnu par ``ClipWidget`` pour retrouver son panneau sans l'importer.
    _is_timeline_host = True

    seek_requested = Signal(float)
    clip_selected = Signal(str)
    transition_selected = Signal(str)
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
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    height_cycle_requested = Signal(str)
    collapse_toggled = Signal(str, bool)
    clips_move_requested = Signal(object)
    blade_cut_requested = Signal(str, float)
    selection_cleared = Signal()
    duplicate_requested = Signal()
    ripple_delete_requested = Signal()
    toggle_enabled_requested = Signal()
    marker_add_requested = Signal(float)
    marker_remove_requested = Signal(str)
    marker_rename_requested = Signal(str)
    slip_requested = Signal(str, float)
    slide_requested = Signal(str, float)
    roll_requested = Signal(str, str, float)
    fade_changed_requested = Signal(str, str, float)
    reset_clip_fades_requested = Signal(str)
    record_requested = Signal(bool)

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
        # Référence faible vers le panneau de prévisualisation : permet
        # au timecode turquoise de rester en phase avec la tête de
        # lecture. Aucune dépendance dure, juste un rappel best-effort.
        self._preview_panel = None

        # Dimensions configurables de la timeline.
        self.header_height = Sizes.timeline_header_height
        self.ruler_height = Sizes.timeline_ruler_height
        self.track_height = Sizes.timeline_track_height
        self.track_gap = 0
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
        self.markers: list = []
        self.clip_widgets: dict[str, ClipWidget] = {}
        self.track_header_widgets: dict[str, TrackRowHeader] = {}
        self.transition_widgets: dict[str, TransitionMarkerWidget] = {}
        # Marge autour de la zone visible, en pixels. Les clips hors de
        # cette fenêtre ne sont pas des widgets. Voir ``_sync_mounted_clips``.
        self._overscan_px = 720
        self._cached_header_signature: tuple | None = None
        self.selected_transition_id: str | None = None
        self._cull_guard = False
        self.dragging_playhead = False
        self.selected_clip_id: str | None = None
        self.selected_clip_ids: set[str] = set()
        self._selection_anchor: str | None = None
        self.tool = "select"
        self.ripple_enabled = False
        self._drag_delta = 0.0
        self._drag_track_delta = 0
        self._slip_delta = 0.0
        self._drag_anchor: str | None = None
        self._marquee: QRubberBand | None = None
        self._marquee_origin: QRect | None = None
        self._runtime = None
        self._preview_timer: QTimer | None = None
        self._pixmaps: dict[str, QPixmap] = {}
        self.fps = 30.0
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
        self.ruler = TimelineRuler(self)
        self.ruler.seek_requested.connect(self.seek_requested.emit)
        self.ruler.marker_rename_requested.connect(self.marker_rename_requested.emit)
        outer.addWidget(self.ruler)

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
        self.timeline_grid.host = self
        self.scroll.setWidget(self.timeline_grid)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._on_timeline_scrolled)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_timeline_scrolled)
        outer.addWidget(self.scroll, 1)
        self.refresh_clip_widgets()


    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        self.project = project
        self.fps = float(getattr(project, "fps", 30.0) or 30.0)
        self.markers = list(getattr(project, "markers", []))
        self._refresh_track_metadata()
        self.clip_views = build_clip_views(project)
        self.selected_clip_id = None
        self.selected_clip_ids = set()
        self.selected_transition_id = None
        self.refresh_clip_widgets()
        self.update()
        callback = getattr(self, "on_structure_changed", None)
        if callable(callback):
            callback()

    def find_view_by_id(self, clip_id: str) -> TimelineClipView | None:
        return self._views_by_id().get(clip_id)

    def select_clip(self, clip_id: str) -> None:
        self._set_selection([clip_id], clip_id, announce=True)
        self._selection_anchor = clip_id


    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        # Redimensionnement pur : on ne recrée pas les en-têtes.
        # La fenêtre visible change, donc certains clips peuvent
        # entrer ou sortir du montage.
        self._update_scroll_extent()
        self._sync_mounted_clips()
        self._layout_children()
        super().resizeEvent(event)


    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self.scroll, "viewport", lambda: None)() and event.type() == QEvent.Wheel:
            modifiers = event.modifiers()
            if modifiers & (Qt.ControlModifier | Qt.MetaModifier):
                steps = event.angleDelta().y() / 120 or (1 if event.pixelDelta().y() > 0 else -1)
                self._zoom_by(1.12 ** steps, event.position().x())
                return True
            if modifiers & Qt.ShiftModifier:
                delta = event.pixelDelta().x() or event.angleDelta().y() or event.angleDelta().x()
                bar = self.scroll.horizontalScrollBar()
                bar.setValue(bar.value() - int(delta))
                return True
        return super().eventFilter(watched, event)


    def paintEvent(self, event):
        painter = QPainter(self)
        palette = _current_palette()
        painter.fillRect(event.rect(), QColor(palette.timeline_bg))
        painter.end()


__all__ = [
    "ClipWidget",
    "TimelinePanel",
    "TrackRowHeader",
]
