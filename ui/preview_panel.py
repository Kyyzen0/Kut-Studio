"""Viewer (preview) de Kut-Studio (refonte UI/UX).

Le viewer combine :

- une zone d'aperçu ``QGraphicsView`` qui rend la vidéo en cours ;
- un overlay de sous-titres ;
- un overlay d'indication de transition ;
- une barre d'outils de transport (lecture / coupe) ;
- un état vide explicite pour les nouveaux projets.

Toutes les commandes utilisent des icônes SVG cohérentes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGridLayout,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconButton, IconLabel, IconName
from ui.theme import COLORS, label_style


class PreviewPanel(QWidget):
    def __init__(
        self,
        toggle_play,
        stop_playback,
        seek_relative,
        cut_callback,
        open_file_callback,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)

        # Aperçu basé ``QGraphicsView`` + ``QGraphicsVideoItem`` afin de
        # pouvoir appliquer des transformations graphiques (position,
        # échelle, rotation, opacité) au média rendu sans modifier le
        # ``Project`` sous-jacent.
        self.graphics_scene = QGraphicsScene(self)
        self.graphics_view = QGraphicsView(self.graphics_scene, self)
        self.graphics_view.setRenderHints(self.graphics_view.renderHints())
        self.graphics_view.setBackgroundBrush(QColor(COLORS["background"]))
        self.graphics_view.setStyleSheet(
            f"background: {COLORS['background']}; border: none;"
        )
        self.video_item = QGraphicsVideoItem()
        self.graphics_scene.addItem(self.video_item)
        self.player.setVideoOutput(self.video_item)

        # Position / échelle courantes appliquées à ``QGraphicsVideoItem``.
        self._applied_pos_x: float = 0.0
        self._applied_pos_y: float = 0.0
        self._applied_scale: float = 1.0
        self._applied_rotation: float = 0.0
        self._applied_opacity: float = 1.0

        # Suivi interne de la source affichée pour les deux modes.
        self._timeline_preview_path: str | None = None
        self._library_preview_path: str | None = None

        # Overlays ------------------------------------------------------------
        self.preview_transition_overlay = QLabel("Fondu enchaîné · 0.5 s")
        self.preview_transition_overlay.setAlignment(Qt.AlignCenter)
        self.preview_transition_overlay.setStyleSheet(
            f"background: {COLORS['transition_overlay_bg']}; color: {COLORS['transition_overlay']}; "
            f"border: 1px solid {COLORS['transition_overlay']}; border-radius: 6px; "
            f"padding: 8px 14px; font-weight: 700;"
        )
        self.preview_transition_overlay.hide()

        self.empty_state = QLabel(
            "Votre histoire commence ici\n\n"
            "Importez vos médias, puis déposez-les sur la timeline."
        )
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setStyleSheet(label_style(14, "muted", 500))
        self.empty_state.setWordWrap(True)

        self.preview_subtitle_overlay = QLabel()
        self.preview_subtitle_overlay.setAlignment(Qt.AlignCenter)
        self.preview_subtitle_overlay.setWordWrap(True)
        self.preview_subtitle_overlay.setStyleSheet(
            f"background: rgba(0, 0, 0, 190); color: {COLORS['text']}; "
            f"border-radius: 6px; padding: 8px 14px; font-size: 16px; font-weight: 700;"
        )
        self.preview_subtitle_overlay.hide()

        # Entête ---------------------------------------------------------------
        top_header = QWidget()
        top_header.setFixedHeight(48)
        top_header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QHBoxLayout(top_header)
        header_layout.setContentsMargins(Spacing.lg, 0, Spacing.lg, 0)
        header_layout.setSpacing(Spacing.md)

        title_box = QWidget()
        title_layout = QHBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(Spacing.sm)
        title_icon = IconLabel(IconName.MEDIA, size=Iconography.md)
        title_icon.set_color(QColor(COLORS["muted_strong"]))
        title_layout.addWidget(title_icon)
        title = QLabel("VIEWER")
        title.setStyleSheet(label_style(11, "muted", 800))
        title_layout.addWidget(title)
        header_layout.addWidget(title_box)

        header_layout.addStretch()

        status = QLabel("1920 × 1080 · 30 fps")
        status.setStyleSheet(label_style(11, "muted", 600))
        status.setAlignment(Qt.AlignRight)
        header_layout.addWidget(status)

        # Barre d'outils de transport -----------------------------------------
        toolbar = QWidget()
        toolbar.setFixedHeight(56)
        toolbar.setStyleSheet(
            f"background: {COLORS['panel']}; border-top: 1px solid {COLORS['border']};"
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.sm)
        toolbar_layout.setSpacing(Spacing.xs)

        # Transport : retour / play / stop / avance.
        rewind = IconButton(
            icon=IconName.REWIND,
            tooltip="Reculer de 2 s",
            size=Sizes.icon_button,
        )
        rewind.clicked.connect(lambda: seek_relative(-2))
        self.play_button = IconButton(
            icon=IconName.PLAY,
            tooltip="Lecture / Pause",
            accent=True,
            size=Sizes.icon_button + 4,
        )
        self.play_button.clicked.connect(toggle_play)
        stop = IconButton(
            icon=IconName.STOP,
            tooltip="Stop",
            size=Sizes.icon_button,
        )
        stop.clicked.connect(stop_playback)
        forward = IconButton(
            icon=IconName.FORWARD,
            tooltip="Avancer de 2 s",
            size=Sizes.icon_button,
        )
        forward.clicked.connect(lambda: seek_relative(2))

        # Séparateur visuel.
        separator = QWidget()
        separator.setFixedWidth(1)
        separator.setStyleSheet(f"background: {COLORS['border']};")

        # Coupe / split / import.
        cut_button = IconButton(
            icon=IconName.CUT,
            tooltip="Couper le clip à la tête de lecture",
            size=Sizes.icon_button,
        )
        cut_button.clicked.connect(cut_callback)
        import_button = IconButton(
            icon=IconName.IMPORT,
            tooltip="Importer un média",
            size=Sizes.icon_button,
        )
        import_button.clicked.connect(open_file_callback)

        for button in (rewind, self.play_button, stop, forward, separator,
                       cut_button, import_button):
            toolbar_layout.addWidget(button)
        toolbar_layout.addStretch()

        # Zone d'aperçu ---------------------------------------------------------
        preview_container = QWidget()
        preview_layout = QGridLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        preview_layout.addWidget(self.graphics_view, 0, 0)
        preview_layout.addWidget(self.empty_state, 0, 0, Qt.AlignCenter)
        preview_layout.addWidget(
            self.preview_transition_overlay, 0, 0, Qt.AlignCenter
        )
        preview_layout.addWidget(
            self.preview_subtitle_overlay,
            0, 0,
            Qt.AlignHCenter | Qt.AlignBottom,
        )

        # Layout principal ------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(top_header)
        layout.addWidget(preview_container, 1)
        layout.addWidget(toolbar)

        self.setObjectName("viewer_panel")
        self.setStyleSheet(
            f"QWidget#viewer_panel {{ background: {COLORS['background']}; }}"
        )

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def is_library_preview(self) -> bool:
        return bool(self._library_preview_path)

    def library_preview_path(self) -> str | None:
        return self._library_preview_path

    def load_video(self, path):
        """Prévisualisation libre déclenchée par la bibliothèque."""
        if not path:
            return
        self.empty_state.hide()
        self._library_preview_path = path
        self._timeline_preview_path = None
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def preview_at(self, path, source_time_seconds):
        """Affiche ``path`` à la position ``source_time_seconds``."""
        self._library_preview_path = None
        if not path:
            self.show_empty()
            return
        self.empty_state.hide()
        if self._timeline_preview_path != path:
            self._timeline_preview_path = path
            self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(int(source_time_seconds * 1000))

    def show_empty(self):
        """Affiche l'état vide : aucun clip vidéo actif."""
        self._library_preview_path = None
        self._timeline_preview_path = None
        try:
            self.player.stop()
        except Exception:  # pragma: no cover
            pass
        self.empty_state.show()

    # ------------------------------------------------------------------
    # Application du transform courant (tâche 13)
    # ------------------------------------------------------------------

    def apply_transform(
        self,
        *,
        position_x: float = 0.0,
        position_y: float = 0.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        opacity: float = 1.0,
        canvas_width: int | None = None,
        canvas_height: int | None = None,
    ) -> None:
        opacity = max(0.0, min(1.0, float(opacity)))
        scale = max(0.01, float(scale))

        view_rect = self.graphics_view.viewport().rect()
        if canvas_width is None or canvas_width <= 0:
            canvas_width = max(view_rect.width(), 1)
        if canvas_height is None or canvas_height <= 0:
            canvas_height = max(view_rect.height(), 1)
        scene_w = float(canvas_width)
        scene_h = float(canvas_height)
        center_x = self.graphics_view.mapToScene(view_rect.center()).x()
        center_y = self.graphics_view.mapToScene(view_rect.center()).y()

        native = self.video_item.nativeSize()
        item_w = max(float(native.width()), 1.0)
        item_h = max(float(native.height()), 1.0)
        target_w = item_w * scale
        target_h = item_h * scale
        self.video_item.setScale(scale)

        delta_x = float(position_x) * scene_w
        delta_y = float(position_y) * scene_h
        transform = QTransform()
        transform.translate(center_x + delta_x, center_y + delta_y)
        transform.rotate(-float(rotation))
        transform.translate(-target_w / 2.0, -target_h / 2.0)
        self.video_item.setTransform(transform)
        self.video_item.setOpacity(opacity)

        self._applied_pos_x = float(position_x)
        self._applied_pos_y = float(position_y)
        self._applied_scale = scale
        self._applied_rotation = float(rotation)
        self._applied_opacity = opacity

    def current_applied_transform(self) -> dict[str, float]:
        return {
            "position_x": self._applied_pos_x,
            "position_y": self._applied_pos_y,
            "scale": self._applied_scale,
            "rotation": self._applied_rotation,
            "opacity": self._applied_opacity,
        }