"""Viewer (preview) de Kut-Studio (refonte UI/UX).

Le viewer combine :

- une zone d'aperçu ``QGraphicsView`` qui rend la vidéo en cours ;
- en rendu GPU, une surface ``QRhiWidget`` **sous** cette vue (rendue
  transparente) : décodage → transform → effets → composition → affichage sur
  le GPU, les poignées et calques restant dessinés par Qt au-dessus
  (voir ``ui/gpu_preview.py``) ; le rendu CPU (``QGraphicsVideoItem``) reste
  complet et sert de repli ;
- un overlay de sous-titres ;
- un overlay d'indication de transition ;
- une barre d'outils de transport (lecture / coupe) ;
- un état vide explicite pour les nouveaux projets.

Toutes les commandes utilisent des icônes SVG cohérentes.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPixmap, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGridLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui.design_system import Sizes, Spacing, Typography, Weights
from ui.empty_state import EmptyState
from ui.icons import IconButton, IconName
from ui.panel_header import PanelHeader
from ui.theme import COLORS, monospace_font_family, set_role
from ui.tracking_overlay import TrackingOverlay
from ui.viewer_overlay import ViewerOverlay
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)

CANVAS_MARGIN = 12
"""Marge (pixels) entre le cadre de la séquence et les bords du viewer."""


class _ViewportWatcher(QObject):
    """Relaie les redimensionnements du viewport (le cadre suit sa taille)."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 (API Qt)
        if event.type() == QEvent.Resize:
            self._callback()
        return False


PLAYBACK_DRIFT_SECONDS = 0.20
"""Écart toléré entre le lecteur et la timeline pendant la lecture.

Repositionner le lecteur à chaque tick (40 ms) relance le décodage depuis
l'image clé précédente : mesuré sur un H.264 à GOP long, **aucune** image
n'était affichée pendant la lecture. Le lecteur suit donc sa propre horloge et
n'est recalé que s'il dérive de plus de 200 ms (ou au changement de source)."""


class PreviewPanel(QWidget):
    canvas_changed = Signal()
    """Le cadre affiché a changé de taille (re-rendre l'aperçu des calques)."""
    gpu_failed = Signal(str, str)
    """Le moniteur GPU a échoué (``type, détail``) ; le panneau est déjà revenu au CPU."""
    gpu_ready = Signal(str)
    """Première image GPU réussie (API et périphérique)."""

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
        # Le calque vidéo (taille du cadre, transform du clip) rogne son média : en cadrage « remplir », le média
        # agrandi dépasse du calque et seule la fenêtre de pan est visible, comme le ``crop`` de l'export.
        self.video_frame_item = QGraphicsRectItem()
        self.video_frame_item.setPen(Qt.NoPen)
        self.video_frame_item.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)
        self.graphics_scene.addItem(self.video_frame_item)
        self.video_item = QGraphicsVideoItem(self.video_frame_item)
        self.video_item.nativeSizeChanged.connect(lambda _size: self._reapply_transform())
        self.player.setVideoOutput(self.video_item)
        self.graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Cadre de la séquence : le repère commun de la vidéo, de l'aperçu des
        # calques motion graphics et de la surcouche interactive.
        self._canvas_size: tuple[int, int] = (1920, 1080)
        self._canvas_rect = QRectF(0, 0, 1, 1)
        # Fond noir du cadre (comme le fond de l'export) : on voit où est le
        # cadre même sans vidéo sous la tête de lecture.
        self.canvas_item = QGraphicsRectItem()
        self.canvas_item.setBrush(QColor(0, 0, 0))
        self.canvas_item.setPen(QColor(COLORS["border"]))
        self.canvas_item.setZValue(-10)
        self.graphics_scene.addItem(self.canvas_item)
        self.mograph_item = QGraphicsPixmapItem()
        self.mograph_item.setZValue(5)
        self.mograph_item.setTransformationMode(Qt.SmoothTransformation)
        self.graphics_scene.addItem(self.mograph_item)
        self.overlay = ViewerOverlay()
        self.graphics_scene.addItem(self.overlay)
        # Trackers (points, zones, trajectoires) : au-dessus des poignées de calque.
        self.tracking_overlay = TrackingOverlay()
        self.graphics_scene.addItem(self.tracking_overlay)
        self._viewport_watcher = _ViewportWatcher(self._layout_canvas)
        self.graphics_view.viewport().installEventFilter(self._viewport_watcher)

        # Position / échelle courantes appliquées à ``QGraphicsVideoItem``.
        self._applied_pos_x: float = 0.0
        self._applied_pos_y: float = 0.0
        self._applied_scale: float = 1.0
        self._applied_rotation: float = 0.0
        self._applied_opacity: float = 1.0
        self._applied_advanced: dict[str, float] = {}

        # Suivi interne de la source affichée pour les deux modes.
        self._timeline_preview_path: str | None = None
        self._library_preview_path: str | None = None
        # Diviseur de prévisualisation (1, 2, 4, 8). Le lecteur Qt
        # décode encore l'image native : ce chiffre est le contrat que
        # le futur chemin de proxies devra respecter. On ne réduit pas
        # l'image à la main, ce qui fausserait le cadrage.
        self.preview_divisor: int = 1
        # Moniteur GPU (désactivé par défaut : voir ``enable_gpu``).
        self.gpu_view = None
        self._video_sink: QVideoSink | None = None
        self._gpu_effects: tuple = ()
        self._gpu_blend = None
        self._gpu_matte: tuple[str, object] | None = None
        self._gpu_source_size: tuple[int, int] | None = None
        self._gpu_adjustments: tuple = ()
        self._gpu_grade = None
        # Calques graphiques à mode de fusion (Addition, Écran…) composés par le GPU : (clé de contenu, image, mode).
        self._gpu_blend_layers: tuple = ()
        self._gpu_effect_time = 0.0          # temps du clip affiché (effets qui bougent : heat haze)
        self._gpu_blend_pushed: dict[str, str] = {}
        self.playback_seeks = 0

        # Overlays ------------------------------------------------------------
        # L'état vide de la visionneuse : icône, titre (première ligne du message) et texte d'aide, sur le noir de l'image.
        self.empty_state = EmptyState(translate("preview.no_clip"), icon=IconName.MEDIA)
        self._empty_state_name: str | None = None  # ``None`` : pas de clip ; sinon nom du clip sans média
        # La visibilité de l'état vide se déduit de deux intentions (voir ``_sync_empty_state``) : aucun clip vidéo à
        # montrer, et des calques dessinés. Les afficher/cacher depuis des endroits différents le faisait osciller à
        # chaque tick de lecture (affiché par la synchronisation, recaché par le rafraîchissement différé des calques).
        self._empty_requested = True
        self._graphics_present = False
        self.empty_state.setMaximumWidth(390)

        self.preview_subtitle_overlay = QLabel()
        self.preview_subtitle_overlay.setAlignment(Qt.AlignCenter)
        self.preview_subtitle_overlay.setWordWrap(True)
        self.preview_subtitle_overlay.setStyleSheet(
            f"background: rgba(0, 0, 0, 190); color: {COLORS['text']}; "
            f"border-radius: 6px; padding: 8px 14px; font-size: {Typography.title}px; font-weight: {Weights.bold};"
        )
        self.preview_subtitle_overlay.hide()
        self._current_subtitle_alignment = Qt.AlignCenter | Qt.AlignBottom

        self.preview_effects_overlay = QLabel()
        self.preview_effects_overlay.setAlignment(Qt.AlignCenter)
        self.preview_effects_overlay.setStyleSheet(
            f"background: rgba(15, 118, 110, 210); color: {COLORS['text']};"
            " border-radius: 6px; padding: 4px 8px; font-size: 11px; font-weight: 700;"
        )
        self.preview_effects_overlay.hide()

        # Avis discret quand l'aperçu a été réduit temporairement (qualité Auto).
        self.preview_quality_notice = QLabel()
        self.preview_quality_notice.setAlignment(Qt.AlignCenter)
        self.preview_quality_notice.setStyleSheet(
            f"background: rgba(120, 80, 0, 200); color: {COLORS['text']};"
            " border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 600;"
        )
        self.preview_quality_notice.hide()

        # Entête ---------------------------------------------------------------
        top_header = PanelHeader(translate("preview.title"), icon=IconName.MEDIA)
        self._title_label = top_header.title_label

        status = QLabel()
        self.format_status = status                                # cadre de la séquence active (set_frame_format)
        set_role(status, "meta")
        status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)       # sans le VCenter, le libellé remontait au bord haut du bandeau
        top_header.add_trailing(status)

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
            f" font-size: {Typography.body_lg}px; font-weight: {Weights.bold}; letter-spacing: 1px;"
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
            tooltip=translate("preview.back"),
            size=Sizes.icon_button,
        )
        rewind.clicked.connect(lambda: seek_relative(-2))
        self.play_button = IconButton(
            icon=IconName.PLAY,
            tooltip=translate("shortcuts.command.play_pause"),
            accent=True,
            size=Sizes.icon_button + 4,
        )
        self.play_button.clicked.connect(toggle_play)
        stop = IconButton(
            icon=IconName.STOP,
            tooltip=translate("tracking.stop"),
            size=Sizes.icon_button,
        )
        stop.clicked.connect(stop_playback)
        forward = IconButton(
            icon=IconName.FORWARD,
            tooltip=translate("preview.forward"),
            size=Sizes.icon_button,
        )
        forward.clicked.connect(lambda: seek_relative(2))

        for button in (rewind, self.play_button, stop, forward):
            toolbar_layout.addWidget(button)
        # Boutons à infobulle traduite : (bouton, clé i18n), réécrits par ``update_translations``.
        self._tooltip_buttons = [(rewind, "preview.back"), (self.play_button, "shortcuts.command.play_pause"),
                                 (stop, "tracking.stop"), (forward, "preview.forward")]

        toolbar_layout.addStretch()

        # Qualité / actions à droite (discrètes).
        cut_button = IconButton(
            icon=IconName.CUT,
            tooltip=translate("tooltip.cut"),
            size=Sizes.icon_button,
        )
        cut_button.clicked.connect(cut_callback)
        import_button = IconButton(
            icon=IconName.IMPORT,
            tooltip=translate("preview.import"),
            size=Sizes.icon_button,
        )
        import_button.clicked.connect(open_file_callback)
        toolbar_layout.addWidget(cut_button)
        toolbar_layout.addWidget(import_button)
        self._tooltip_buttons += [(cut_button, "tooltip.cut"), (import_button, "preview.import")]

        # Zone d'aperçu ---------------------------------------------------------
        preview_container = QWidget()
        preview_layout = QGridLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        preview_layout.addWidget(self.graphics_view, 0, 0)
        preview_layout.addWidget(self.empty_state, 0, 0, Qt.AlignCenter)
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
        self._empty_requested = False
        self._sync_empty_state()
        self._library_preview_path = path
        self._timeline_preview_path = None
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def preview_at(self, path, source_time_seconds, *, playing: bool = False):
        """Affiche ``path`` à la position ``source_time_seconds``.

        Pendant la lecture (``playing``), le lecteur garde son horloge : il n'est
        recalé que s'il dérive de plus de :data:`PLAYBACK_DRIFT_SECONDS` ou si la
        source change (voir la constante : un recalage par tick figeait l'image).
        """
        self._library_preview_path = None
        if not path:
            self.show_empty()
            return
        self._empty_requested = False
        self._sync_empty_state()
        target_ms = int(source_time_seconds * 1000)
        if self._timeline_preview_path != path:
            self._timeline_preview_path = path
            self.player.setSource(QUrl.fromLocalFile(path))
        elif playing and self.player.playbackState() == QMediaPlayer.PlayingState:
            if abs(self.player.position() - target_ms) <= PLAYBACK_DRIFT_SECONDS * 1000:
                return
        if playing:
            self.playback_seeks += 1
        self.player.setPosition(target_ms)

    def set_silenced(self, silenced: bool) -> None:
        """Coupe (ou rétablit) le son du lecteur du moniteur : le son vient alors d'ailleurs (enregistreur Multicam)."""
        self.audio_output.setMuted(bool(silenced))

    def set_frame_format(self, width: int, height: int, fps: float) -> None:
        """En-tête de la visionneuse : cadre et cadence de la séquence active (« 1080 × 1920 · 30 i/s · 9:16 »)."""
        from math import gcd

        divisor = gcd(int(width), int(height)) or 1
        self.format_status.setText(translate("preview.frame_format", width=int(width), height=int(height),
                                             fps=f"{float(fps):g}", ratio=f"{int(width) // divisor}:{int(height) // divisor}"))

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
                % (COLORS["accent_dark"], COLORS["accent"])
            )
            badge.hide()
            self.preview_render_badge = badge
            try:
                layout = container.layout()
                if layout is not None:
                    layout.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignHCenter)
            except Exception:
                LOGGER.debug(
                    "Insertion du badge de rendu dans le moniteur en échec : badge non affiché",
                    exc_info=True,
                )
        if computing:
            badge.setText(label or translate("preview.computing"))
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
                "color: %s; padding: 2px 8px;" % COLORS["muted"]
            )
            pill.hide()
            self.preview_cache_pill = pill
            try:
                layout = container.layout()
                if layout is not None:
                    layout.addWidget(pill, 0, 0, Qt.AlignBottom | Qt.AlignHCenter)
            except Exception:
                LOGGER.debug(
                    "Insertion de la pastille de cache dans le moniteur en échec : pastille non affichée",
                    exc_info=True,
                )
        pill.setText(label or (translate("preview.cached") if cached else ""))
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
        self._gpu_effects = tuple(active)
        if self.gpu_view is not None:
            self._update_gpu_composite()
            # Le GPU montre ces effets en direct : la pastille ne signale que le reste.
            from core.gpu_effects import program_for

            active = [e for e in active if e.type.value in program_for(active).unsupported]
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
            LOGGER.debug("Mise à jour du timecode du moniteur en échec : affichage non actualisé", exc_info=True)

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
            LOGGER.debug(
                "Libération du lecteur du moniteur en échec : le décodeur peut rester attaché au fichier précédent",
                exc_info=True,
            )
        if self.gpu_view is not None:
            self.gpu_view.forget_source("main")
            self.gpu_view.release_gpu_cache()

    def show_empty(self):
        """Affiche l'état vide : aucun clip vidéo actif."""
        self._library_preview_path = None
        self._timeline_preview_path = None
        try:
            self.player.stop()
        except Exception:  # pragma: no cover
            LOGGER.debug(
                "Arrêt du lecteur du moniteur en échec : la lecture peut continuer derrière l'état vide",
                exc_info=True,
            )
        self._empty_requested = True
        self._sync_empty_state()
        self.preview_effects_overlay.hide()
        if self.gpu_view is not None:
            self.gpu_view.forget_source("main")

    def show_missing_media(self, clip_name: str = "") -> None:
        """Distingue un média absent d'un trou réel dans la timeline."""
        self.show_empty()
        self._empty_state_name = clip_name.strip()
        self.empty_state.setText(translate("preview.no_source", name=self._empty_state_name
                                           or translate("preview.selected_clip")))

    def show_no_active_clip(self) -> None:
        """Restaure le message destiné aux espaces vides de la timeline."""
        self.show_empty()
        self._empty_state_name = None
        self.empty_state.setText(translate("preview.no_clip"))

    def update_translations(self) -> None:
        """Textes de la visionneuse dans la langue courante (appelé par ``MainWindow._retranslate_ui``)."""
        self._title_label.setText(translate("preview.title"))
        for button, key in self._tooltip_buttons:
            button.setToolTip(translate(key))
        if self._empty_state_name is None:
            self.empty_state.setText(translate("preview.no_clip"))
        else:
            self.empty_state.setText(translate("preview.no_source", name=self._empty_state_name
                                               or translate("preview.selected_clip")))
        pill = getattr(self, "preview_cache_pill", None)
        if pill is not None and pill.text():
            pill.setText(translate("preview.cached"))

    # ------------------------------------------------------------------
    # Application du transform courant (tâche 13)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cadre de la séquence
    # ------------------------------------------------------------------

    def set_canvas_size(self, width: int, height: int) -> None:
        """Taille (pixels) de la séquence affichée : fixe le rapport du cadre."""
        size = (max(2, int(width)), max(2, int(height)))
        if size != self._canvas_size:
            self._canvas_size = size
            self._layout_canvas()

    def canvas_size(self) -> tuple[int, int]:
        return self._canvas_size

    def canvas_rect(self) -> QRectF:
        """Rectangle (repère de la scène = pixels du viewport) du cadre."""
        return QRectF(self._canvas_rect)

    def _layout_canvas(self) -> None:
        viewport = self.graphics_view.viewport().rect()
        vw, vh = max(viewport.width(), 1), max(viewport.height(), 1)
        self.graphics_scene.setSceneRect(QRectF(0, 0, vw, vh))
        cw, ch = self._canvas_size
        available_w = max(1.0, vw - 2 * CANVAS_MARGIN)
        available_h = max(1.0, vh - 2 * CANVAS_MARGIN)
        k = min(available_w / cw, available_h / ch)
        width, height = cw * k, ch * k
        rect = QRectF((vw - width) / 2.0, (vh - height) / 2.0, width, height)
        changed = rect != self._canvas_rect
        self._canvas_rect = rect
        self.overlay.set_canvas(rect, (cw, ch))
        self.tracking_overlay.set_canvas(rect, (cw, ch))
        self.canvas_item.setRect(rect)
        self.mograph_item.setPos(rect.topLeft())
        if self.gpu_view is not None:
            self.gpu_view.set_canvas_rect(
                (rect.x(), rect.y(), rect.width(), rect.height()), _rgb(COLORS["background"])
            )
        self._reapply_transform()
        if changed:
            self.canvas_changed.emit()

    def set_mograph_image(self, image) -> None:
        """Calques motion graphics rendus à la taille du cadre (``None`` = rien)."""
        if image is None or image.isNull():
            self.mograph_item.setPixmap(QPixmap())
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > 0:
            # L'image peut être rendue plus petite (lecture) : elle couvre le cadre.
            self.mograph_item.setScale(self._canvas_rect.width() / pixmap.width())
        self.mograph_item.setPixmap(pixmap)

    def set_mograph_visible(self, visible: bool) -> None:
        self.mograph_item.setVisible(bool(visible))

    def set_graphics_present(self, present: bool) -> None:
        """Des calques sont visibles : le message « aucun clip » ne doit pas les recouvrir.

        Quand ils disparaissent (vrai trou dans la timeline), le message revient si aucun clip vidéo n'est affiché.
        """
        self._graphics_present = bool(present)
        self._sync_empty_state()

    def _sync_empty_state(self) -> None:
        """Seul endroit qui décide de la visibilité de l'état vide (voir ``_empty_requested``)."""
        self.empty_state.setVisible(self._empty_requested and not self._graphics_present)

    def _reapply_transform(self) -> None:
        self.apply_transform(
            position_x=self._applied_pos_x,
            position_y=self._applied_pos_y,
            scale=self._applied_scale,
            rotation=self._applied_rotation,
            opacity=self._applied_opacity,
            **self._applied_advanced,
        )

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
        anchor_x: float = 0.5,
        anchor_y: float = 0.5,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        flip_h: bool = False,
        flip_v: bool = False,
        fill: bool = False,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
        **_ignored,
    ) -> None:
        """Place la vidéo dans le cadre comme l'export (voir ``core.mograph_scene``).

        Le média occupe le cadre (adapté sans déformation, comme ``scale`` +
        ``pad`` de l'export, ou agrandi et rogné en cadrage « remplir »), puis
        pivote et s'échelonne autour du point d'ancrage ; la position (fraction
        du cadre) désigne l'ancrage.
        """
        opacity = max(0.0, min(1.0, float(opacity)))
        scale = max(0.01, float(scale))
        rect = self._canvas_rect
        width, height = rect.width(), rect.height()
        self.video_frame_item.setRect(QRectF(0.0, 0.0, width, height))
        native = self.video_item.nativeSize()
        if fill and native.width() > 0 and native.height() > 0:
            from core.tracking_motion import fit_box

            canvas_w, canvas_h = self._canvas_size
            box = fit_box(int(native.width()), int(native.height()), canvas_w, canvas_h,
                          fill=True, pan_x=float(pan_x), pan_y=float(pan_y))
            k = width / max(1.0, float(canvas_w))
            self.video_item.setPos(box.offset_x * k, box.offset_y * k)
            self.video_item.setSize(QSizeF(box.width * k, box.height * k))
        else:
            self.video_item.setPos(0.0, 0.0)
            self.video_item.setSize(QSizeF(width, height))
        self.video_item.setScale(1.0)
        sx = scale * float(scale_x) * (-1.0 if flip_h else 1.0)
        sy = scale * float(scale_y) * (-1.0 if flip_v else 1.0)
        transform = QTransform()
        transform.translate(
            rect.x() + width / 2.0 + float(position_x) * width,
            rect.y() + height / 2.0 + float(position_y) * height,
        )
        # Sens horaire pour un angle positif, comme le filtre ``rotate`` de
        # FFmpeg : l'aperçu et l'export tournent dans le même sens.
        transform.rotate(float(rotation))
        transform.scale(sx, sy)
        transform.translate(-float(anchor_x) * width, -float(anchor_y) * height)
        self.video_frame_item.setTransform(transform)
        self.video_item.setOpacity(opacity)

        self._applied_pos_x = float(position_x)
        self._applied_pos_y = float(position_y)
        self._applied_scale = scale
        self._applied_rotation = float(rotation)
        self._applied_opacity = opacity
        self._applied_advanced = {
            "anchor_x": float(anchor_x), "anchor_y": float(anchor_y),
            "scale_x": float(scale_x), "scale_y": float(scale_y),
            "flip_h": bool(flip_h), "flip_v": bool(flip_v),
            "fill": bool(fill), "pan_x": float(pan_x), "pan_y": float(pan_y),
        }
        if self.gpu_view is not None:
            self._update_gpu_composite()

    # ------------------------------------------------------------------
    # Moniteur GPU
    # ------------------------------------------------------------------

    @property
    def gpu_active(self) -> bool:
        return self.gpu_view is not None

    def enable_gpu(self, api: str, *, cache_budget: int | None = None) -> bool:
        """Bascule le moniteur sur le GPU (``QRhiWidget``) ; ``False`` si impossible ici.

        Le GPU n'est confirmé qu'à la première image (:attr:`gpu_ready`) ; un
        échec ultérieur émet :attr:`gpu_failed` **après** le retour au CPU.
        """
        if self.gpu_view is not None:
            return True
        try:
            from ui.gpu_preview import GpuPreviewWidget

            view = GpuPreviewWidget(api=api, cache_budget=cache_budget)
        except Exception as error:  # module Qt absent, API inconnue…
            self.gpu_failed.emit("init", str(error))
            return False
        self.gpu_view = view
        container = self.graphics_view.parentWidget()
        layout = container.layout() if container is not None else None
        if layout is not None:
            layout.addWidget(view, 0, 0)
        view.lower()  # sous la vue des poignées et calques
        view.failed.connect(self._on_gpu_failed)
        view.ready.connect(self.gpu_ready.emit)
        self._set_scene_transparent(True)
        sink = QVideoSink(self)
        sink.videoFrameChanged.connect(self._on_gpu_frame)
        self._video_sink = sink
        self.video_item.hide()
        self.player.setVideoSink(sink)
        rect = self._canvas_rect
        view.set_canvas_rect((rect.x(), rect.y(), rect.width(), rect.height()), _rgb(COLORS["background"]))
        self._update_gpu_composite()
        self.set_effects(self._gpu_effects)
        view.show()
        return True

    def disable_gpu(self) -> None:
        """Retour au moniteur CPU (``QGraphicsVideoItem``), ressources GPU libérées."""
        view = self.gpu_view
        if view is None:
            return
        self.gpu_view = None
        position = self.player.position()
        self.player.setVideoOutput(self.video_item)
        self.player.setPosition(position)  # redonne une image au moniteur CPU
        self.video_item.show()
        if self._video_sink is not None:
            try:
                self._video_sink.videoFrameChanged.disconnect(self._on_gpu_frame)
            except (RuntimeError, TypeError):
                pass
            self._video_sink.deleteLater()
            self._video_sink = None
        self._set_scene_transparent(False)
        try:
            view.release_gpu()
        except Exception:
            LOGGER.debug(
                "Libération des ressources GPU du moniteur en échec : ressources laissées au pilote",
                exc_info=True,
            )
        view.hide()
        view.deleteLater()
        self.set_effects(self._gpu_effects)

    def set_layer_compositing(self, blend_mode=None, matte: tuple[str, object] | None = None) -> None:
        """Mode de fusion et matte (clé, ``QImage``) du clip affiché (GPU seulement)."""
        self._gpu_blend = blend_mode
        self._gpu_matte = matte
        if self.gpu_view is not None:
            self._update_gpu_composite()

    def set_color_grade(self, grade) -> None:
        """Étalonnage du clip affiché (GPU seulement) : le moniteur le montre en direct par une LUT cuite par la
        chaîne de l'export (:mod:`core.gpu_grade`) ; ``None`` ou un étalonnage neutre : rien."""
        from core.gpu_grade import grade_is_active

        self._gpu_grade = grade if grade_is_active(grade) else None
        if self.gpu_view is not None:
            self._update_gpu_composite()

    def set_adjustments(self, adjustments) -> None:
        """Calques d'effets actifs : ``[(clé, effets, couverture QImage | None, opacité[, étalonnage])]`` (GPU
        seulement)."""
        self._gpu_adjustments = tuple(adjustments or ())
        if self.gpu_view is not None:
            self._update_gpu_composite()

    def set_effect_time(self, seconds: float) -> None:
        """Temps local du clip affiché, pour les effets animés du moniteur GPU (pris en compte au prochain rendu)."""
        self._gpu_effect_time = float(seconds)

    def set_blend_layers(self, layers) -> None:
        """Calques graphiques à mode de fusion, du bas vers le haut : ``[(clé, QImage, mode)]`` (GPU seulement).

        Chaque image (taille de rendu, RGBA droit) devient une source du moniteur, envoyée seulement quand son contenu
        change ; son alpha sert de matte. Le shader de composition applique le mode comme pour un clip vidéo."""
        from PySide6.QtMultimedia import QVideoFrame

        self._gpu_blend_layers = tuple(layers or ())
        view = self.gpu_view
        if view is None:
            return
        used = set()
        for index, (key, image, _blend) in enumerate(self._gpu_blend_layers):
            source = f"graphics{index}"
            used.add(source)
            if self._gpu_blend_pushed.get(source) != key:
                view.set_video_frame(source, QVideoFrame(image))
                self._gpu_blend_pushed[source] = key
        for source in [s for s in self._gpu_blend_pushed if s not in used]:
            view.forget_source(source)
            del self._gpu_blend_pushed[source]
        self._update_gpu_composite()

    def _gpu_graphics_layers(self, cw: int, ch: int, mattes: dict) -> tuple:
        """Calques à mode de fusion, au-dessus de la vidéo (cadre entier, alpha de l'image en matte)."""
        from core.blend_modes import coerce_blend_mode
        from core.gpu_composite import CompositeLayer
        from core.gpu_effects import program_for

        layers = []
        for index, (key, image, blend) in enumerate(self._gpu_blend_layers):
            matte = f"blend:{key}"
            mattes[matte] = image
            layers.append(CompositeLayer(
                source=f"graphics{index}", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, float(cw), float(ch)),
                blend=coerce_blend_mode(blend), program=program_for(()), matte=matte,
            ))
        return tuple(layers)

    def gpu_render_size(self) -> tuple[int, int]:
        """Taille de rendu du cadre (pixels physiques), pour rastériser la matte."""
        cw, ch = self._canvas_size
        scale = self._gpu_render_scale()
        return (max(2, int(round(cw * scale))), max(2, int(round(ch * scale))))

    def _gpu_render_scale(self) -> float:
        cw, _ch = self._canvas_size
        dpr = self.devicePixelRatioF() or 1.0
        shown = max(2.0, self._canvas_rect.width() * dpr)
        return max(0.05, min(1.0, shown / float(cw)) / max(1, int(self.preview_divisor)))

    def refresh_theme(self) -> None:
        """Après un changement de thème : le pinceau de la scène, le liseré du canevas et le fond du canevas GPU sont des valeurs
        (des ``QBrush`` / ``QPen`` créés à la construction), pas des styles : la re-teinte des styles ne les atteint pas."""
        self.canvas_item.setPen(QColor(COLORS["border"]))
        if self.graphics_view.backgroundBrush().style() != Qt.NoBrush:
            self.graphics_view.setBackgroundBrush(QColor(COLORS["background"]))
        if self.gpu_view is not None:
            self._layout_canvas()

    def _set_scene_transparent(self, transparent: bool) -> None:
        if transparent:
            self.graphics_view.setBackgroundBrush(Qt.NoBrush)
            self.graphics_view.setStyleSheet("background: transparent; border: none;")
            self.graphics_view.viewport().setAutoFillBackground(False)
            self.canvas_item.setBrush(QBrush(Qt.NoBrush))
        else:
            self.graphics_view.setBackgroundBrush(QColor(COLORS["background"]))
            self.graphics_view.setStyleSheet(f"background: {COLORS['background']}; border: none;")
            self.canvas_item.setBrush(QColor(0, 0, 0))

    def _on_gpu_frame(self, frame) -> None:
        view = self.gpu_view
        if view is None or not frame.isValid():
            return
        size = (int(frame.width()), int(frame.height()))
        if size != self._gpu_source_size:
            self._gpu_source_size = size
            self._update_gpu_composite()
        view.set_video_frame("main", frame)

    def _on_gpu_failed(self, kind: str, detail: str) -> None:
        self.disable_gpu()
        self.gpu_failed.emit(kind, detail)

    def _update_gpu_composite(self) -> None:
        view = self.gpu_view
        if view is None:
            return
        from types import SimpleNamespace

        from core.blend_modes import coerce_blend_mode
        from core.gpu_composite import CompositeFrame, CompositeLayer
        from core.gpu_effects import program_for
        from core.tracking_motion import fit_box, video_layer_matrix

        cw, ch = self._canvas_size
        layers = ()
        if self._gpu_source_size is not None and (self._timeline_preview_path or self._library_preview_path):
            advanced = self._applied_advanced
            values = SimpleNamespace(
                position_x=self._applied_pos_x, position_y=self._applied_pos_y,
                scale=self._applied_scale, rotation=self._applied_rotation, skew=0.0,
                anchor_x=advanced.get("anchor_x", 0.5), anchor_y=advanced.get("anchor_y", 0.5),
                scale_x=advanced.get("scale_x", 1.0), scale_y=advanced.get("scale_y", 1.0),
                flip_h=advanced.get("flip_h", False), flip_v=advanced.get("flip_v", False),
            )
            box = fit_box(self._gpu_source_size[0], self._gpu_source_size[1], cw, ch,
                          fill=bool(advanced.get("fill", False)),
                          pan_x=advanced.get("pan_x", 0.0), pan_y=advanced.get("pan_y", 0.0))
            matte_key = ""
            mattes = {}
            if self._gpu_matte is not None:
                matte_key, image = self._gpu_matte
                mattes[matte_key] = image
            from core.gpu_effects import VIGNETTE_EXPORT

            try:
                program = program_for(self._gpu_effects, vignette_extent=VIGNETTE_EXPORT, time=self._gpu_effect_time)
            except ValueError:
                program = program_for(())
            layers = (CompositeLayer(
                source="main",
                matrix=tuple(video_layer_matrix(values, cw, ch)),
                fit=box.rect,
                opacity=self._applied_opacity,
                blend=coerce_blend_mode(self._gpu_blend),
                program=program,
                effect_scale=(values.scale * values.scale_x, values.scale * values.scale_y),
                matte=matte_key,
                grade=self._gpu_grade,
            ),)
            adjustments = self._gpu_adjustment_layers(mattes)
            layers = layers + self._gpu_graphics_layers(cw, ch, mattes)
            view.set_composite(CompositeFrame(cw, ch, self._gpu_render_scale(), layers,
                                              adjustments=adjustments), mattes)
            return
        mattes = {}
        view.set_composite(CompositeFrame(cw, ch, self._gpu_render_scale(), self._gpu_graphics_layers(cw, ch, mattes)),
                           mattes)

    def _gpu_adjustment_layers(self, mattes: dict) -> tuple:
        from core.gpu_composite import AdjustmentLayer
        from core.gpu_effects import program_for

        layers = []
        for key, effects, coverage, opacity, *rest in self._gpu_adjustments:
            try:
                program = program_for(effects)
            except ValueError:
                continue
            if coverage is not None:
                mattes[key] = coverage
            grade = rest[0] if rest else None
            layers.append(AdjustmentLayer(program, key if coverage is not None else "", float(opacity), grade=grade))
        return tuple(layers)

    def current_applied_transform(self) -> dict[str, float]:
        return {
            "position_x": self._applied_pos_x,
            "position_y": self._applied_pos_y,
            "scale": self._applied_scale,
            "rotation": self._applied_rotation,
            "opacity": self._applied_opacity,
        }


def _rgb(color: str) -> tuple[float, float, float]:
    qcolor = QColor(color)
    return (qcolor.redF(), qcolor.greenF(), qcolor.blueF())


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
    bg = style.background_color
    if bg:
        bg_rule = f"background: {bg}; opacity: 1;"
        bg_alpha_rule = (
            f"background-color: rgba({_hex_to_rgb(bg)}, {style.background_opacity});"
        )
    else:
        bg_rule = ""
        bg_alpha_rule = ""
    text_rule = f"color: rgba({_hex_to_rgb(color)}, {opacity});"
    outline = style.outline_width
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
