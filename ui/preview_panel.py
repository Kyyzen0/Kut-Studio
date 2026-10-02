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
from ui.theme import COLORS, label_style, monospace_font_family


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
        # Diviseur de prévisualisation (1, 2, 4, 8). Le lecteur Qt
        # décode encore l'image native : ce chiffre est le contrat que
        # le futur chemin de proxies devra respecter. On ne réduit pas
        # l'image à la main, ce qui fausserait le cadrage.
        self.preview_divisor: int = 1

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
            "Aucun clip sous la tête de lecture\n"
            "Déplacez la tête de lecture ou sélectionnez un clip dans la timeline."
        )
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setStyleSheet(
            f"color: {COLORS['muted_strong']}; background: {COLORS['panel']};"
            f" border: 1px solid {COLORS['border_strong']}; border-radius: 8px;"
            f" padding: 18px 24px; font-size: 12px; font-weight: 600;"
        )
        self.empty_state.setWordWrap(True)
        self.empty_state.setMaximumWidth(390)

        self.preview_subtitle_overlay = QLabel()
        self.preview_subtitle_overlay.setAlignment(Qt.AlignCenter)
        self.preview_subtitle_overlay.setWordWrap(True)
        self.preview_subtitle_overlay.setStyleSheet(
            f"background: rgba(0, 0, 0, 190); color: {COLORS['text']}; "
            f"border-radius: 6px; padding: 8px 14px; font-size: 16px; font-weight: 700;"
        )
        self.preview_subtitle_overlay.hide()
        self._current_subtitle_alignment = Qt.AlignCenter | Qt.AlignBottom

        self.preview_effects_overlay = QLabel()
        self.preview_effects_overlay.setAlignment(Qt.AlignCenter)
        self.preview_effects_overlay.setStyleSheet(
            f"background: rgba(15, 118, 110, 210); color: {COLORS['text']};"
            " border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: 700;"
        )
        self.preview_effects_overlay.hide()

        # Avis discret quand l'aperçu a été réduit temporairement (qualité Auto).
        self.preview_quality_notice = QLabel()
        self.preview_quality_notice.setAlignment(Qt.AlignCenter)
        self.preview_quality_notice.setStyleSheet(
            f"background: rgba(120, 80, 0, 200); color: {COLORS['text']};"
            " border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600;"
        )
        self.preview_quality_notice.hide()

        # Entête ---------------------------------------------------------------
        top_header = QWidget()
        top_header.setFixedHeight(40)
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
        title = QLabel("VISIONNEUSE")
        title.setStyleSheet(label_style(10, "muted", 800))
        title_layout.addWidget(title)
        header_layout.addWidget(title_box)

        header_layout.addStretch()

        status = QLabel("1920 × 1080 · 30 fps · 16:9")
        status.setStyleSheet(label_style(11, "muted", 600))
        status.setAlignment(Qt.AlignRight)
        header_layout.addWidget(status)

        # Barre d'outils de transport -----------------------------------------
        toolbar = QWidget()
        toolbar.setFixedHeight(48)
        toolbar.setStyleSheet(
            f"background: {COLORS['panel']}; border-top: 1px solid {COLORS['border']};"
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(Spacing.md, Spacing.xs, Spacing.md, Spacing.xs)
        toolbar_layout.setSpacing(Spacing.sm)

        # Timecode turquoise à gauche.
        self.timecode_label = QLabel("00:00:00:00")
        self.timecode_label.setStyleSheet(
            f"color: {COLORS['accent']}; {monospace_font_family()}"
            f" font-size: 14px; font-weight: 700; letter-spacing: 1px;"
            f" padding: 0 8px;"
        )
        self.duration_label = QLabel("00:00:00")
        self.duration_label.setStyleSheet(
            f"color: {COLORS['muted']}; {monospace_font_family()}"
            f" font-size: 12px; font-weight: 600;"
        )
        toolbar_layout.addWidget(self.timecode_label)
        toolbar_layout.addSpacing(Spacing.xs)
        toolbar_layout.addWidget(QLabel("/"))
        toolbar_layout.addSpacing(Spacing.xs)
        toolbar_layout.addWidget(self.duration_label)

        toolbar_layout.addStretch()

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

        for button in (rewind, self.play_button, stop, forward):
            toolbar_layout.addWidget(button)

        toolbar_layout.addStretch()

        # Qualité / actions à droite (discrètes).
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
        toolbar_layout.addWidget(cut_button)
        toolbar_layout.addWidget(import_button)

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
        preview_layout.addWidget(
            self.preview_effects_overlay,
            0, 0,
            Qt.AlignRight | Qt.AlignTop,
        )
        preview_layout.addWidget(
            self.preview_quality_notice,
            0, 0,
            Qt.AlignLeft | Qt.AlignTop,
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

    def set_preview_divisor(self, divisor: int) -> None:
        """Mémorise le niveau d'aperçu effectif (profil, choix ou adaptation)."""
        self.preview_divisor = max(1, int(divisor))

    def set_quality_notice(self, text: str | None) -> None:
        """Affiche (ou masque) l'avis « aperçu réduit » du mode Auto."""
        if text:
            self.preview_quality_notice.setText(text)
            self.preview_quality_notice.show()
            self.preview_quality_notice.raise_()
        else:
            self.preview_quality_notice.hide()

    def set_render_state(self, computing: bool, label: str = "") -> None:
        """Indicateur 'Calcul de l'aperçu' (tache 30, non bloquant)."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        from ui.theme import COLORS

        badge = getattr(self, "preview_render_badge", None)
        if badge is None:
            container = self.graphics_view.parentWidget() or self
            badge = QLabel("", container)
            badge.setObjectName("preview_render_badge")
            badge.setStyleSheet(
                "background: %s; color: %s; border-radius: 6px; padding: 4px 10px;"
                % (COLORS.get("accent_soft", "#1c2b2b"), COLORS.get("accent", "#36E6C3"))
            )
            badge.hide()
            self.preview_render_badge = badge
            try:
                layout = container.layout()
                if layout is not None:
                    layout.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignHCenter)
            except Exception:
                pass
        if computing:
            badge.setText(label or "Calcul de l'aperçu…")
            badge.show()
            badge.raise_()
        else:
            badge.hide()

    def set_cache_state(self, cached: bool, label: str = "") -> None:
        """Etat du cache sur le moniteur (tache 30)."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        from ui.theme import COLORS

        pill = getattr(self, "preview_cache_pill", None)
        if pill is None:
            container = self.graphics_view.parentWidget() or self
            pill = QLabel("", container)
            pill.setObjectName("preview_cache_pill")
            pill.setStyleSheet(
                "color: %s; padding: 2px 8px;" % COLORS.get("muted", "#888888")
            )
            pill.hide()
            self.preview_cache_pill = pill
            try:
                layout = container.layout()
                if layout is not None:
                    layout.addWidget(pill, 0, 0, Qt.AlignBottom | Qt.AlignHCenter)
            except Exception:
                pass
        pill.setText(label or ("Aperçu en cache" if cached else ""))
        if label or cached:
            pill.show()
            pill.raise_()
        else:
            pill.hide()

    def set_effects(self, effects) -> None:
        """Affiche l'état des effets actifs du clip prévisualisé.

        Le décodage natif de ``QMediaPlayer`` ne permet pas de chaîner les
        filtres FFmpeg image par image. Cette pastille maintient donc un
        retour fidèle sur l'état qui sera appliqué au rendu final.
        """
        active = [effect for effect in effects or () if effect.enabled]
        if not active:
            self.preview_effects_overlay.hide()
            return
        names = ", ".join(effect.type.value.replace("_", " ") for effect in active)
        self.preview_effects_overlay.setText(f"FX · {names}")
        self.preview_effects_overlay.show()

    def set_timecode(self, current_seconds: float, total_seconds: float) -> None:
        """Met à jour le timecode turquoise et la durée totale."""
        try:
            self.timecode_label.setText(_format_timecode(current_seconds))
            self.duration_label.setText(_format_duration(total_seconds))
        except Exception:  # pragma: no cover - cosmetic
            pass

    # ----- Sous-titre (tâche 24) -------------------------------------

    def set_subtitle(self, text: str, style) -> None:
        """Affiche un sous-titre ``text`` sur le preview en appliquant ``style``.

        Un texte vide (ou un clip sans contenu) masque l'overlay.
        """
        if not text or text.strip() == "":
            self.preview_subtitle_overlay.hide()
            return
        self.preview_subtitle_overlay.setText(text)
        self.preview_subtitle_overlay.setStyleSheet(
            _subtitle_overlay_style_sheet(style)
        )
        self.preview_subtitle_overlay.setAlignment(
            _subtitle_alignment_to_qt(style.alignment)
        )
        self.preview_subtitle_overlay.setMaximumWidth(
            _subtitle_max_width(self.preview_subtitle_overlay.parentWidget())
        )
        self.preview_subtitle_overlay.show()

    def clear_subtitle(self) -> None:
        """Cache le sous-titre courant."""
        self.preview_subtitle_overlay.hide()

    def release_media(self) -> None:
        """Lâche la source décodée.

        À appeler quand le projet change ou que la fenêtre se ferme,
        sinon le décodeur reste attaché au fichier précédent.
        """
        self._library_preview_path = None
        self._timeline_preview_path = None
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:  # pragma: no cover
            pass

    def show_empty(self):
        """Affiche l'état vide : aucun clip vidéo actif."""
        self._library_preview_path = None
        self._timeline_preview_path = None
        try:
            self.player.stop()
        except Exception:  # pragma: no cover
            pass
        self.empty_state.show()
        self.preview_effects_overlay.hide()

    def show_missing_media(self, clip_name: str = "") -> None:
        """Distingue un média absent d'un trou réel dans la timeline."""
        self.show_empty()
        name = clip_name.strip() or "Le clip sélectionné"
        self.empty_state.setText(
            f"{name} n’a pas encore de média source\n"
            "Importez ou reliez le fichier pour l’afficher dans la visionneuse."
        )

    def show_no_active_clip(self) -> None:
        """Restaure le message destiné aux espaces vides de la timeline."""
        self.show_empty()
        self.empty_state.setText(
            "Aucun clip sous la tête de lecture\n"
            "Déplacez la tête de lecture ou sélectionnez un clip dans la timeline."
        )

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
        # Sens horaire pour un angle positif, comme le filtre ``rotate`` de
        # FFmpeg : l'aperçu et l'export tournent dans le même sens.
        transform.rotate(float(rotation))
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


def _format_timecode(seconds: float) -> str:
    """Formate un timecode au format ``HH:MM:SS:FF`` (24 fps par défaut)."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_frames = int(round(seconds * 24))
    frames = total_frames % 24
    total_seconds = total_frames // 24
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{total_seconds % 60:02d}:{frames:02d}"


def _format_duration(seconds: float) -> str:
    """Formate une durée simple en ``HH:MM:SS``."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
# Sous-titre : application d'un :class:`TextStyle` au QLabel overlay
# ---------------------------------------------------------------------------


def _subtitle_overlay_style_sheet(style) -> str:
    """Construit la feuille de style Qt pour l'overlay sous-titre."""
    color = style.color
    opacity = max(0.0, min(1.0, style.opacity))
    text_alpha = int(round(opacity * 255))
    bg = style.background_color
    if bg:
        bg_alpha = int(round(max(0.0, min(1.0, style.background_opacity)) * 255))
        bg_rule = f"background: {bg}; opacity: 1;"
        bg_alpha_rule = (
            f"background-color: rgba({_hex_to_rgb(bg)}, {style.background_opacity});"
        )
    else:
        bg_rule = ""
        bg_alpha_rule = ""
    text_rule = f"color: rgba({_hex_to_rgb(color)}, {opacity});"
    outline = style.outline_width
    shadow = style.shadow_offset
    padding_x = int(round(style.padding_x))
    padding_y = int(round(style.padding_y))
    font_family = style.font_family
    font_size = int(round(style.font_size))
    font_weight = 700 if style.font_size >= 36 else 500
    border_radius = 6
    return (
        "QLabel { "
        f"{text_rule} "
        f"{bg_alpha_rule} "
        f"border: {outline:.1f}px solid {style.outline_color}; "
        f"border-radius: {border_radius}px; "
        f"padding: {padding_y}px {padding_x}px; "
        f"font-family: '{font_family}'; "
        f"font-size: {font_size}px; "
        f"font-weight: {font_weight}; "
        f"{bg_rule} "
        "}"
    )


def _subtitle_alignment_to_qt(alignment) -> Qt.Alignment:
    """Convertit un :class:`TextAlignment` en flag Qt d'alignement."""
    from core.text_style import TextAlignment

    row, col = alignment_to_qt_row_col(TextAlignment(alignment))
    vertical = {
        0: Qt.AlignTop,
        1: Qt.AlignVCenter,
        2: Qt.AlignBottom,
    }[row]
    horizontal = {
        0: Qt.AlignLeft,
        1: Qt.AlignHCenter,
        2: Qt.AlignRight,
    }[col]
    return vertical | horizontal


def alignment_to_qt_row_col(alignment) -> tuple[int, int]:
    """Retourne la position (row, col) dans la grille 3×3."""
    from core.text_style import TextAlignment

    if alignment in (
        TextAlignment.TOP_LEFT,
        TextAlignment.TOP_CENTER,
        TextAlignment.TOP_RIGHT,
    ):
        row = 0
    elif alignment in (
        TextAlignment.MIDDLE_LEFT,
        TextAlignment.MIDDLE_CENTER,
        TextAlignment.MIDDLE_RIGHT,
    ):
        row = 1
    else:
        row = 2
    if alignment in (
        TextAlignment.TOP_LEFT,
        TextAlignment.MIDDLE_LEFT,
        TextAlignment.BOTTOM_LEFT,
    ):
        col = 0
    elif alignment in (
        TextAlignment.TOP_CENTER,
        TextAlignment.MIDDLE_CENTER,
        TextAlignment.BOTTOM_CENTER,
    ):
        col = 1
    else:
        col = 2
    return row, col


def _hex_to_rgb(hex_color: str) -> str:
    """Convertit ``#rrggbb`` en chaîne ``r, g, b`` pour CSS Qt."""
    if not hex_color.startswith("#") or len(hex_color) != 7:
        return "255, 255, 255"
    return f"{int(hex_color[1:3], 16)}, {int(hex_color[3:5], 16)}, {int(hex_color[5:7], 16)}"


def _subtitle_max_width(parent: QWidget | None) -> int:
    """Calcule une largeur max relative au conteneur du preview."""
    if parent is None:
        return 720
    width = parent.width()
    if width <= 0:
        return 720
    return max(280, int(width * 0.85))
