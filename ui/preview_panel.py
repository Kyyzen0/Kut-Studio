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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import COLORS, label_style


class PreviewPanel(QWidget):
    def __init__(self, toggle_play, stop_playback, seek_relative, cut_callback, open_file_callback, parent=None):
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
        self.graphics_view.setBackgroundBrush(QColor("#000000"))
        self.graphics_view.setStyleSheet(f"background: {COLORS['background']}; border: none;")
        self.video_item = QGraphicsVideoItem()
        # Taille native par défaut : on laisse Qt décider de la taille de
        # la vidéo à venir ; en cas de scène vide, ``QGraphicsVideoItem``
        # occupe un rectangle nul que l'on recentre à la première pose.
        self.graphics_scene.addItem(self.video_item)
        self.player.setVideoOutput(self.video_item)

        # Position / échelle courantes appliquées à ``QGraphicsVideoItem``.
        # Elles sont déduites de ``EvaluatedTransform`` à chaque tick.
        self._applied_pos_x: float = 0.0
        self._applied_pos_y: float = 0.0
        self._applied_scale: float = 1.0
        self._applied_rotation: float = 0.0
        self._applied_opacity: float = 1.0

        # Suivi interne de la source affichée pour les deux modes :
        # - ``_timeline_preview_path`` : pilote par la timeline ;
        # - ``_library_preview_path`` : prévisualisation libre (bibliothèque).
        self._timeline_preview_path: str | None = None
        self._library_preview_path: str | None = None

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
        preview_layout.addWidget(self.graphics_view, 0, 0)
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

    # ------------------------------------------------------------------
    # Pilotage par la timeline (tâche 8)
    # ------------------------------------------------------------------

    # Indique si la prévisualisation courante a été déclenchée par un
    # clic dans la bibliothèque de médias (lecture libre, hors timeline)
    # ou par le moteur de lecture piloté par ``MainWindow``. Cette
    # distinction permet de garder une prévisualisation de bibliothèque
    # stable tant que la timeline ne l'écrase pas explicitement.
    def is_library_preview(self) -> bool:
        return bool(self._library_preview_path)

    def library_preview_path(self) -> str | None:
        return self._library_preview_path

    def load_video(self, path):
        """Prévisualisation libre déclenchée par la bibliothèque.

        Le média est lu immédiatement, indépendamment de la timeline,
        et ne modifie pas le ``Project``.
        """
        if not path:
            return
        self.empty_state.hide()
        self._library_preview_path = path
        self._timeline_preview_path = None
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def preview_at(self, path, source_time_seconds):
        """Affiche ``path`` à la position ``source_time_seconds``.

        Mode piloté par la timeline : la source est chargée si elle
        diffère de celle déjà en mémoire, puis la tête de lecture est
        repositionnée. La lecture (``player.play()``) n'est PAS
        déclenchée ici : c'est ``MainWindow`` qui orchestre la lecture
        globale via ``toggle_play``.

        Si ``path`` est vide (média de démonstration sans fichier
        réel), on bascule immédiatement sur l'état vide.
        """
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
        except Exception:  # pragma: no cover - Qt peut lever si pas initialisé
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
        """Applique un transform à la vidéo affichée sans muter le Project.

        Conventions :
        - ``position_x`` et ``position_y`` sont normalisées par rapport à
          la taille du canvas : ``+1.0`` décale d'une largeur / hauteur.
        - ``scale`` multiplie la taille native de la vidéo dans la scène.
        - ``rotation`` est en degrés, sens antihoraire (positif ``QGraphicsView``).
        - ``opacity`` est dans ``[0, 1]``.
        """
        # Bornes défensives : on ne tolère pas une opacité hors plage et
        # un scale nul / négatif qui rendrait l'élément invisible.
        opacity = max(0.0, min(1.0, float(opacity)))
        scale = max(0.01, float(scale))

        view_rect = self.graphics_view.viewport().rect()
        if canvas_width is None or canvas_width <= 0:
            canvas_width = max(view_rect.width(), 1)
        if canvas_height is None or canvas_height <= 0:
            canvas_height = max(view_rect.height(), 1)
        # Centre de la zone d'affichage (en pixels scène).
        scene_w = float(canvas_width)
        scene_h = float(canvas_height)
        center_x = self.graphics_view.mapToScene(view_rect.center()).x()
        center_y = self.graphics_view.mapToScene(view_rect.center()).y()

        # Taille native du média : on conserve la taille courante si
        # ``QGraphicsVideoItem`` n'a pas encore reçu de frame.
        native = self.video_item.nativeSize()
        item_w = max(float(native.width()), 1.0)
        item_h = max(float(native.height()), 1.0)
        target_w = item_w * scale
        target_h = item_h * scale
        self.video_item.setScale(scale)

        # Translation de la position : on convertit le delta normalisé
        # en pixels de scène (la scène est mappée 1:1 par défaut).
        delta_x = float(position_x) * scene_w
        delta_y = float(position_y) * scene_h
        # ``QGraphicsItem.setTransform`` réinitialise la transformation
        # ; on l'utilise pour combiner rotation et translation via la
        # séquence ``translate → rotate → translate(-w/2, -h/2)``.
        transform = QTransform()
        transform.translate(center_x + delta_x, center_y + delta_y)
        transform.rotate(-float(rotation))  # Qt : positif = horaire.
        transform.translate(-target_w / 2.0, -target_h / 2.0)
        self.video_item.setTransform(transform)  # noqa: F841 - gardé pour clarté
        self.video_item.setOpacity(opacity)

        # Mise à jour de l'état interne pour les inspections futures.
        self._applied_pos_x = float(position_x)
        self._applied_pos_y = float(position_y)
        self._applied_scale = scale
        self._applied_rotation = float(rotation)
        self._applied_opacity = opacity

    def current_applied_transform(self) -> dict[str, float]:
        """Retourne le transform actuellement appliqué (inspection / tests)."""
        return {
            "position_x": self._applied_pos_x,
            "position_y": self._applied_pos_y,
            "scale": self._applied_scale,
            "rotation": self._applied_rotation,
            "opacity": self._applied_opacity,
        }
