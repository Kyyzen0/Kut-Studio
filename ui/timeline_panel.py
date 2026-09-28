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

import os

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.media_previews import (
    extract_thumbnail,
    extract_waveform_peaks,
    thumbnail_cache_key,
    thumbnail_slots,
    thumbnail_source_times,
    waveform_bins,
    waveform_cache_key,
)
from core.project_model import Project, Track
from core.task_queue import PRIORITY_VISIBLE
from core.timeline_editing import (
    ClipPlacement,
    clip_ids_in_range,
    shifted_track_index,
    snap_edit_position,
)
from core.timeline_navigation import (
    clamp_zoom,
    fit_zoom,
    format_timecode,
    scroll_for_anchor,
)
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
)
from ui.timeline_ruler import TimelineRuler
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import ThemePalette, label_style


_TRACK_TYPE_LABELS = {
    "video": "Vidéo",
    "audio": "Audio",
    "subtitle": "Sous-titres",
}
_CONTENT_TOP = 8
_HEIGHTS = {"compact": 40, "normal": 68, "large": 112}
_COLLAPSED_HEIGHT = 28


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
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    height_cycle_requested = Signal(str)
    collapse_toggled = Signal(str, bool)
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
        if getattr(track, "solo", False):
            state_parts.append("Solo")
        if getattr(track, "collapsed", False):
            state_parts.append("Réduite")
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

        solo_btn = _btn(
            IconName.SOLO,
            "Solo",
            lambda checked: self.solo_toggled.emit(track.id, checked),
            checkable=True,
            checked=bool(getattr(track, "solo", False)),
        )
        layout.addWidget(solo_btn)
        if track.type == "audio":
            arm_btn = _btn(
                IconName.MARKER,
                "Armer la piste",
                lambda checked: self.arm_toggled.emit(track.id, checked),
                checkable=True,
                checked=bool(getattr(track, "armed", False)),
            )
            layout.addWidget(arm_btn)
        layout.addWidget(
            _btn(
                IconName.HEIGHT,
                "Hauteur de piste",
                lambda: self.height_cycle_requested.emit(track.id),
            )
        )
        layout.addWidget(
            _btn(
                IconName.ARROW_DOWN if not getattr(track, "collapsed", False) else IconName.ARROW_UP,
                "Réduire ou développer",
                lambda: self.collapse_toggled.emit(
                    track.id, not bool(getattr(track, "collapsed", False))
                ),
            )
        )

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
        self.pending_track_index = view.track_index
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
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
        selected = parent is not None and parent._is_selected(self.view.id)
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
        index = self.pending_track_index
        rect = parent.clip_rect(
            self.view,
            self.pending_start,
            self.pending_end,
            track_index=index,
        )
        self.setGeometry(*rect)
        self.duration_label.setText(parent.format_time(self.pending_end - self.pending_start))

    # ------------------------------------------------------------------
    # Fondus audio
    # ------------------------------------------------------------------

    def clip_model(self):
        """Le :class:`Clip` métier correspondant, ou ``None``.

        La vue est immuable ; c'est le modèle qui porte les réglages
        audio. On l'atteint par le panneau pour ne jamais dupliquer
        l'état.
        """
        parent = self.parent_timeline
        if parent is None:
            return None
        return parent.clip_model(self.view.id)

    @property
    def is_audio_clip(self) -> bool:
        """Le clip porte-t-il des réglages de fondu ?"""
        if getattr(self.view, "track_type", None) == "audio":
            return True
        return self.view.track_id.startswith("A")

    def fade_seconds(self, which: str) -> float:
        """Durée de fondu du clip (``"in"`` ou ``"out"``), 0 si absent."""
        model = self.clip_model()
        if model is None:
            return 0.0
        return float(getattr(model, f"fade_{which}", 0.0))

    def _fade_hit_rect(self, which: str) -> QRectF:
        """Zone cliquable d'une poignée de fondu, en coordonnées clip.

        La zone est volontairement plus haute que la courbe : la cible
        reste confortable même sur une piste compacte.
        """
        width = max(self.width(), 1)
        height = self.height()
        grab = 14.0
        if which == "in":
            seconds = self.fade_seconds("in")
        else:
            seconds = self.fade_seconds("out")
        parent = self.parent_timeline
        if parent is None or parent.pixels_per_second * parent.zoom <= 0:
            return QRectF()
        pixels = min(seconds, max(self.view.end - self.view.start, 0.0)) * (
            parent.pixels_per_second * parent.zoom
        )
        pixels = max(0.0, min(pixels, width))
        if which == "in":
            return QRectF(0.0, 0.0, max(pixels, 0.0) + grab, height)
        return QRectF(width - max(pixels, 0.0) - grab, 0.0, max(pixels, 0.0) + grab, height)

    def _fade_handle_center_x(self, which: str) -> float:
        """Abscisse du sommet de la courbe de fondu."""
        parent = self.parent_timeline
        if parent is None or parent.pixels_per_second * parent.zoom <= 0:
            return 0.0
        seconds = self.fade_seconds(which)
        pixels = seconds * (parent.pixels_per_second * parent.zoom)
        if which == "out":
            return max(0.0, self.width() - pixels)
        return min(float(self.width()), pixels)

    def _paint_fade_handles(self) -> None:
        """Dessine les courbes de fondu et leurs poignées."""
        if not self.is_audio_clip:
            return
        fade_in = self.fade_seconds("in")
        fade_out = self.fade_seconds("out")
        if fade_in <= 0.0 and fade_out <= 0.0:
            return
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = float(self.width())
        height = float(self.height())
        stroke = QColor(palette.clip_text_dim)
        stroke.setAlpha(200)
        painter.setPen(QPen(stroke, 1.4))

        if fade_in > 0.0:
            peak = self._fade_handle_center_x("in")
            line = QPolygonF()
            steps = 12
            for step in range(steps + 1):
                ratio = step / steps
                line.append(
                    QPointF(peak * ratio, height - 2 - (height - 4) * ratio)
                )
            painter.drawPolyline(line)

        if fade_out > 0.0:
            peak = self._fade_handle_center_x("out")
            line = QPolygonF()
            steps = 12
            for step in range(steps + 1):
                ratio = step / steps
                x = peak + (width - peak) * ratio
                line.append(QPointF(x, 2 + (height - 4) * ratio))
            painter.drawPolyline(line)

        # Poignées : petits losanges cliquables.
        painter.setBrush(QColor(palette.clip_text))
        painter.setPen(QPen(QColor(palette.clip_border), 1))
        for which in ("in", "out"):
            seconds = self.fade_seconds(which)
            if seconds <= 0.0:
                continue
            x = self._fade_handle_center_x(which)
            diamond = QPolygonF(
                [
                    QPointF(x, 6),
                    QPointF(x + 5, 11),
                    QPointF(x, 16),
                    QPointF(x - 5, 11),
                ]
            )
            painter.drawPolygon(diamond)
        painter.end()

    def _paint_time_remapping_badges(self) -> None:
        """Dessine les badges de remappage temporel (vitesse, reverse, freeze)."""
        from core.time_remapping import FreezeFrameMode, TimeRemapping
        
        time_remapping = getattr(self.view, "time_remapping", None) or TimeRemapping()
        
        # Pas de badge si tout est par défaut
        if time_remapping.is_normal:
            return
        
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Position du badge : coin supérieur droit
        badge_x = self.width() - 45
        active_effects = [
            effect for effect in getattr(self.view, "effects", ())
            if getattr(effect, "enabled", False)
        ]
        badge_y = 26 if active_effects else 4
        badge_width = 40
        badge_height = 18
        
        # Couleurs
        bg_color = QColor(palette.clip_text_dim)
        bg_color.setAlpha(220)
        text_color = QColor(palette.clip_text)
        
        # Dessiner le fond du badge
        painter.setBrush(bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(
            badge_x, badge_y, badge_width, badge_height, 4, 4
        )
        
        # Texte du badge
        painter.setPen(text_color)
        painter.setFont(self.font())
        
        # Déterminer le texte à afficher
        badge_text = ""
        if time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
            badge_text = "F"
        elif time_remapping.reverse:
            badge_text = "R"
        elif time_remapping.speed != 1.0:
            badge_text = f"{time_remapping.speed:.1f}x"
        
        if badge_text:
            # Dessiner le texte centré dans le badge
            text_metrics = painter.fontMetrics()
            text_width = text_metrics.horizontalAdvance(badge_text)
            text_height = text_metrics.height()
            text_x = badge_x + (badge_width - text_width) / 2
            text_y = badge_y + (badge_height + text_height) / 2 - 2
            painter.drawText(int(text_x), int(text_y), badge_text)
        
        painter.end()

    def _paint_effect_badge(self) -> None:
        """Signale les effets actifs sans masquer le nom du clip."""
        active_effects = [
            effect for effect in getattr(self.view, "effects", ())
            if getattr(effect, "enabled", False)
        ]
        if not active_effects:
            return
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        badge_width, badge_height = 38, 18
        badge_x = self.width() - badge_width - 5
        painter.setBrush(QColor("#0F766E"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_x, 4, badge_width, badge_height, 4, 4)
        painter.setPen(QColor(palette.clip_text))
        painter.setFont(self.font())
        painter.drawText(
            QRect(badge_x, 4, badge_width, badge_height),
            Qt.AlignCenter,
            f"FX {len(active_effects)}",
        )
        painter.end()

    def mouseDoubleClickEvent(self, event):
        """Double-clic sur une poignée : remet le fondu correspondant à zéro."""
        if not self.is_audio_clip:
            super().mouseDoubleClickEvent(event)
            return
        position = event.position()
        for which in ("in", "out"):
            if self._fade_hit_rect(which).contains(position):
                parent = self.parent_timeline
                if parent is not None:
                    parent.reset_clip_fades_requested.emit(self.view.id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        parent = self.parent_timeline
        if parent is None:
            return super().mousePressEvent(event)
        locked = parent.track_is_locked(self.view.track_id)
        x = event.position().x()
        scale = parent.pixels_per_second * parent.zoom
        # Les poignées de fondu priment sur les autres manipulations :
        # elles occupent les coins du clip, zone sinon ambiguë.
        if self.is_audio_clip and not locked:
            for which in ("in", "out"):
                if self._fade_hit_rect(which).contains(event.position()):
                    self.drag_mode = f"fade-{which}"
                    self.drag_start_x = event.globalPos().x()
                    self.drag_original_fade = self.fade_seconds(which)
                    parent.begin_drag(self.view.id)
                    parent._select_from_pointer(
                        self.view.id, event.modifiers(), drag=False
                    )
                    event.accept()
                    return
        if parent.tool == "slip" and not locked:
            self.drag_mode = "slip"
            self.drag_start_x = event.globalPos().x()
            parent._select_from_pointer(self.view.id, event.modifiers(), drag=False)
            event.accept()
            return
        if parent.tool == "slide" and not locked:
            self.drag_mode = "slide"
            self.drag_start_x = event.globalPos().x()
            self.drag_original_start = self.view.start
            self.drag_original_end = self.view.end
            self.pending_start = self.view.start
            self.pending_end = self.view.end
            self.pending_track_index = self.view.track_index
            parent._select_from_pointer(self.view.id, event.modifiers(), drag=False)
            event.accept()
            return
        if parent.tool == "roll" and not locked:
            self.drag_mode = "roll-left" if x <= self.handle_width else "roll-right"
            self.drag_start_x = event.globalPos().x()
            self.drag_original_start = self.view.start
            self.drag_original_end = self.view.end
            parent._select_from_pointer(self.view.id, event.modifiers(), drag=False)
            event.accept()
            return
        if parent.tool == "blade" and self.handle_width < x < self.width() - self.handle_width:
            parent._select_from_pointer(self.view.id, event.modifiers(), drag=False)
            if not locked and scale > 0:
                instant = self.view.start + (x / scale)
                parent.blade_cut_requested.emit(self.view.id, instant)
            event.accept()
            return
        parent._select_from_pointer(self.view.id, event.modifiers(), drag=not locked)
        if locked:
            event.accept()
            return
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
        self.pending_track_index = self.view.track_index
        parent.begin_drag(self.view.id)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_mode is None:
            return
        parent = self.parent_timeline
        if parent is None:
            return
        scale = parent.pixels_per_second * parent.zoom
        if scale <= 0:
            return
        delta_seconds = (event.globalPos().x() - self.drag_start_x) / scale
        if self.drag_mode in {"fade-in", "fade-out"}:
            which = self.drag_mode.split("-", 1)[1]
            parent.preview_fade(self, which, self.drag_original_fade + delta_seconds)
        elif self.drag_mode == "move":
            proposed = max(0.0, self.drag_original_start + delta_seconds)
            proposed = parent.snap_time(proposed, anchor_id=self.view.id)
            delta = proposed - self.drag_original_start
            parent.preview_group_move(self.view.id, delta, event.globalPos().y())
        elif self.drag_mode == "slip":
            parent.preview_slip(self, delta_seconds)
        elif self.drag_mode == "slide":
            proposed = max(0.0, self.drag_original_start + delta_seconds)
            parent.preview_slide(self, proposed)
        elif self.drag_mode in {"roll-left", "roll-right"}:
            edge_time = self.drag_original_start if self.drag_mode == "roll-left" else self.drag_original_end
            parent.preview_roll(self, self.drag_mode, edge_time + delta_seconds)
        elif self.drag_mode == "trim-right":
            proposed = max(
                self.drag_original_start + 0.1,
                self.drag_original_end + delta_seconds,
            )
            proposed = parent.snap_time(proposed, anchor_id=self.view.id)
            self.pending_end = max(self.drag_original_start + 0.1, proposed)
            self._apply_pending_geometry()
        elif self.drag_mode == "trim-left":
            proposed = min(
                self.drag_original_end - 0.1,
                max(0.0, self.drag_original_start + delta_seconds),
            )
            proposed = parent.snap_time(proposed, anchor_id=self.view.id)
            self.pending_start = min(self.drag_original_end - 0.1, max(0.0, proposed))
            self._apply_pending_geometry()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        parent = self.parent_timeline
        if parent is not None and self.drag_mode is not None:
            if self.drag_mode == "move":
                parent.finish_group_move(self.view.id)
            elif self.drag_mode == "trim-right":
                parent.trim_clip_right_requested.emit(self.view.id, self.pending_end)
            elif self.drag_mode == "trim-left":
                parent.trim_clip_left_requested.emit(self.view.id, self.pending_start)
            elif self.drag_mode == "slip":
                parent.slip_requested.emit(self.view.id, parent._slip_delta)
            elif self.drag_mode == "slide":
                parent.slide_requested.emit(self.view.id, self.pending_start)
            elif self.drag_mode == "roll-left":
                parent.roll_requested.emit(self.view.id, "left", self.pending_start)
            elif self.drag_mode == "roll-right":
                parent.roll_requested.emit(self.view.id, "right", self.pending_end)
            elif self.drag_mode in {"fade-in", "fade-out"}:
                which = self.drag_mode.split("-", 1)[1]
                parent.fade_changed_requested.emit(
                    self.view.id, which, self.pending_fade
                )
            parent.snap_line_x = None
        self.drag_mode = None
        event.accept()

    def contextMenuEvent(self, event) -> None:
        parent = self.parent_timeline
        if parent is None:
            return super().contextMenuEvent(event)
        parent.open_clip_menu(self.view.id, event.globalPos())
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        self._paint_media_preview()
        self._paint_fade_handles()
        self._paint_effect_badge()
        self._paint_time_remapping_badges()
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

    def _paint_media_preview(self) -> None:
        """Dessine une waveform ou des vignettes déjà en cache.

        Rien n'est calculé ici. L'absence de cache laisse le clip plat.
        """
        parent = self.parent_timeline
        if parent is None or self.width() < 24:
            return
        if parent.track_is_collapsed(self.view.track_id):
            return
        runtime = getattr(parent, "_runtime", None)
        path = self.view.source_path or ""
        painter = QPainter(self)
        painter.setClipRect(self.rect().adjusted(2, 2, -2, -2))
        if self.view.track_type == "audio":
            mode = parent.track_height_mode(self.view.track_id)
            bins = waveform_bins(self.width(), mode)
            peaks = None
            if runtime is not None and path and os.path.isfile(path):
                peaks = runtime.cache.get(waveform_cache_key(path, bins))
            if not peaks:
                from core.media_previews import synthetic_peaks

                peaks = synthetic_peaks(self.view.id, bins)
            if peaks:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 90))
                step = max(1, self.width() / max(1, len(peaks)))
                mid = self.height() / 2
                for index, peak in enumerate(peaks):
                    bar = max(1.0, float(peak) * (self.height() - 10))
                    painter.drawRect(
                        int(4 + index * step),
                        int(mid - bar / 2),
                        max(1, int(step) - 1),
                        int(bar),
                    )
        elif self.view.track_type == "video":
            filmstrips = True if runtime is None else runtime.resolved_profile().filmstrips
            if filmstrips:
                self._paint_thumbnails(painter, parent, runtime, path)
        painter.end()

    def _paint_thumbnails(self, painter, parent, runtime, path: str) -> None:
        clip = parent.clip_model(self.view.id)
        if clip is None:
            return
        slots = thumbnail_slots(self.width(), enabled=True)
        times = thumbnail_source_times(clip.source_in, clip.source_out, slots)
        if not times:
            return
        cell = max(1, (self.width() - 8) // len(times))
        for index, instant in enumerate(times):
            key = thumbnail_cache_key(path, instant, 160) if path else ""
            data = runtime.cache.get(key) if runtime is not None and key else None
            if isinstance(data, (bytes, bytearray)) and data:
                pixmap = parent.pixmap_for(key, data)
            else:
                pixmap = parent.synthetic_thumb(self.view, index, len(times))
            if pixmap is None or pixmap.isNull():
                continue
            target_h = max(8, self.height() - 16)
            painter.drawPixmap(6 + index * cell, 8, cell - 2, target_h, pixmap)


class TransitionMarkerWidget(QLabel):
    """Marqueur interactif projeté depuis une transition du projet."""

    def __init__(self, transition_id: str, timeline: "TimelinePanel") -> None:
        super().__init__(timeline.timeline_grid)
        self.transition_id = transition_id
        self.timeline = timeline
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Cliquer pour modifier cette transition")

    def refresh_style(self, label: str) -> None:
        palette = _current_palette()
        selected = self.timeline.selected_transition_id == self.transition_id
        background = palette.accent if selected else palette.panel_alt
        border = palette.clip_border_selected if selected else palette.clip_border
        self.setText(label)
        self.setStyleSheet(
            f"QLabel {{ background: {background}; color: {palette.clip_text}; "
            f"border: 1px solid {border}; border-radius: 4px; "
            "font-size: 10px; font-weight: 700; padding: 1px 4px; }}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.timeline.select_transition(self.transition_id)
            event.accept()
            return
        super().mousePressEvent(event)


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
        self.lanes: list[tuple[int, int]] = []
        self.playhead_x: float | None = None
        self.snap_x: float | None = None
        self.host = None

    def configure(
        self,
        *,
        track_count: int,
        track_height: int,
        track_gap: int,
        ruler_height: int,
        left_margin: int,
        lanes: list[tuple[int, int]] | None = None,
    ) -> None:
        self.track_count = max(0, track_count)
        self.track_height = track_height
        self.track_gap = track_gap
        self.ruler_height = ruler_height
        self.left_margin = left_margin
        if lanes is not None:
            self.lanes = lanes
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
        first_row = _CONTENT_TOP
        lane_left = self.left_margin
        lanes = self.lanes or [
            (first_row + index * pitch, self.track_height)
            for index in range(self.track_count)
        ]

        for index, (top, height) in enumerate(lanes):
            if top > self.height():
                break
            if index % 2 == 1:
                painter.fillRect(
                    lane_left,
                    int(top),
                    self.width() - lane_left,
                    int(height),
                    QColor(alt),
                )
            painter.setPen(QPen(QColor(divider), 1))
            line_y = int(top + height)
            painter.drawLine(lane_left, line_y, self.width(), line_y)
        if self.snap_x is not None:
            painter.setPen(QPen(QColor(palette.snap_line), 1))
            painter.drawLine(int(self.snap_x), 0, int(self.snap_x), self.height())
        if self.playhead_x is not None:
            painter.setPen(QPen(QColor(palette.playhead), 2))
            painter.drawLine(int(self.playhead_x), 0, int(self.playhead_x), self.height())
        painter.end()

    def mousePressEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and event.button() == Qt.LeftButton:
            host.begin_marquee(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and host._marquee is not None:
            host.update_marquee(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and host._marquee is not None and event.button() == Qt.LeftButton:
            host.finish_marquee(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        time_layout.setSpacing(2)
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(
            f"color: {palette.accent}; font-weight: 800; font-size: 14px;"
            f" font-family: 'SF Mono', 'Menlo', monospace; letter-spacing: 1px;"
        )
        # Rangée combinée : timecode + durée totale séparées par un slash.
        self.timecode_label = QLabel("00:00:00")
        self.timecode_label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px;"
            f" font-family: 'SF Mono', 'Menlo', monospace;"
        )
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px;"
            f" font-family: 'SF Mono', 'Menlo', monospace;"
        )
        time_row = QWidget()
        time_row_layout = QHBoxLayout(time_row)
        time_row_layout.setContentsMargins(0, 0, 0, 0)
        time_row_layout.setSpacing(4)
        time_row_layout.addWidget(self.timecode_label)
        time_row_layout.addWidget(self.total_time_label)
        time_row_layout.addStretch(1)
        time_layout.addWidget(self.time_label)
        time_layout.addWidget(time_row)
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
        self.blade_button = IconButton(
            icon=IconName.SCISSORS,
            tooltip="Outil lame (B)",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.blade_button.toggled.connect(lambda checked: self.set_tool("blade" if checked else "select"))
        left_layout.addWidget(self.blade_button)
        self.roll_button = IconButton(
            icon=IconName.CUT,
            tooltip="Roll (R) : déplace la coupe entre deux clips",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.slip_button = IconButton(
            icon=IconName.REWIND,
            tooltip="Slip (Y) : change le contenu sans bouger le clip",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.slide_button = IconButton(
            icon=IconName.FORWARD,
            tooltip="Slide (U) : glisse le clip et ajuste ses voisins",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.roll_button.toggled.connect(lambda checked: self.set_tool("roll" if checked else "select"))
        self.slip_button.toggled.connect(lambda checked: self.set_tool("slip" if checked else "select"))
        self.slide_button.toggled.connect(lambda checked: self.set_tool("slide" if checked else "select"))
        left_layout.addWidget(self.roll_button)
        left_layout.addWidget(self.slip_button)
        left_layout.addWidget(self.slide_button)
        self.record_button = IconButton(
            # Icône distincte de ``marker_button`` : les deux boutons sont
            # voisins dans la barre d'outils et partageaient auparavant le
            # même glyphe de marqueur, ce qui les rendait indiscernables.
            icon=IconName.MIC,
            tooltip="Enregistrer sur les pistes audio armées",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.record_button.toggled.connect(self.record_requested.emit)
        left_layout.addWidget(self.record_button)
        self.ripple_button = IconButton(
            icon=IconName.FORWARD,
            tooltip="Ripple (N) : referme le trou après un trim droit ou une suppression",
            checkable=True,
            size=Sizes.icon_button,
        )
        self.ripple_button.toggled.connect(self._on_ripple_toggled)
        left_layout.addWidget(self.ripple_button)
        self.marker_button = IconButton(
            icon=IconName.MARKER,
            tooltip="Marqueur au playhead (M)",
            size=Sizes.icon_button,
        )
        self.marker_button.clicked.connect(
            lambda: self.marker_add_requested.emit(self.playhead_seconds)
        )
        left_layout.addWidget(self.marker_button)

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
        self.zoom_fit_btn = IconButton(
            icon=IconName.PANEL_RESTORE,
            tooltip="Voir toute la timeline",
            size=Sizes.icon_button_sm,
        )
        self.zoom_fit_btn.clicked.connect(self.fit_timeline)
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self.zoom_in_btn)
        zoom_layout.addWidget(self.zoom_fit_btn)
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

    def unsubscribe_from_theme(self) -> None:
        """Retire l'abonnement thème. À appeler à la fermeture de la fenêtre."""
        manager = self._theme_manager
        if manager is None:
            return
        manager.unsubscribe(self._on_palette_changed)
        self._theme_manager = None

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
        self._sync_transition_widgets()
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
        for view in self.clip_views:
            if view.id == clip_id:
                return view
        return None

    def select_clip(self, clip_id: str) -> None:
        self._set_selection([clip_id], clip_id, announce=True)
        self._selection_anchor = clip_id

    def set_culling_overscan(self, pixels: int) -> None:
        """Règle la marge de montage des clips autour de la zone visible.

        Appelé quand le profil de performance change. Une marge plus
        petite monte moins de widgets sur une petite machine.
        """
        pixels = max(0, int(pixels))
        if pixels == self._overscan_px:
            return
        self._overscan_px = pixels
        if self._sync_mounted_clips():
            self._layout_children()

    @property
    def mounted_clip_count(self) -> int:
        """Nombre de clips réellement instanciés, pas le nombre du projet."""
        return len(self.clip_widgets)

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
        # Redimensionnement pur : on ne recrée pas les en-têtes.
        # La fenêtre visible change, donc certains clips peuvent
        # entrer ou sortir du montage.
        self._update_scroll_extent()
        self._sync_mounted_clips()
        self._layout_children()
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

    def refresh_headers(self) -> None:
        """Recrée les en-têtes depuis le projet, sans toucher à la sélection.

        Le cache de signature est oublié : un bouton qui vient d'être
        basculé alors que le modèle a refusé le changement (piste
        verrouillée) retrouve ainsi l'état réel.
        """
        self._cached_header_signature = None
        self.refresh_clip_widgets()

    def refresh_clip_widgets(self):
        """Met à jour en-têtes et clips visibles, puis repositionne.

        Les en-têtes ne sont recréés que si une piste a changé (nom,
        verrou, ordre). Les clips hors de la fenêtre visible ne sont
        pas des widgets : un projet long ne monte pas un ``QWidget``
        par clip. On pourra retirer ce filtrage le jour où la timeline
        sera dessinée dans un seul ``paintEvent`` plutôt qu'avec un
        widget par clip.
        """
        count = len(self.clip_views)
        self.clip_count_label.setText(
            f"{count} clip" if count == 1 else f"{count} clips"
        )
        self._update_scroll_extent()
        self._configure_grid()
        signature = self._header_signature()
        if signature != self._cached_header_signature:
            self._rebuild_track_headers()
            self._cached_header_signature = signature
        self._sync_mounted_clips(refresh_views=True)
        self._layout_children()
        self._sync_transition_widgets()

    def _header_signature(self) -> tuple:
        if self.project is None:
            return tuple()
        return tuple(
            (
                index,
                track.id,
                track.name,
                track.type,
                bool(track.locked),
                bool(track.visible),
                bool(track.muted),
                bool(getattr(track, "solo", False)),
                bool(getattr(track, "armed", False)),
                getattr(track, "height_mode", "normal"),
                bool(getattr(track, "collapsed", False)),
            )
            for index, track in enumerate(self.project.tracks)
        )

    def _configure_grid(self) -> None:
        track_count = max(len(self.project.tracks) if self.project else 1, 1)
        rows_height = self.rows_span()
        self.timeline_grid.setMinimumHeight(int(rows_height))
        lanes = []
        if self.project is not None:
            lanes = [
                (self.row_top(index), self.row_height_of(track))
                for index, track in enumerate(self.project.tracks)
            ]
        self.timeline_grid.configure(
            track_count=track_count,
            track_height=self.track_height,
            track_gap=self.track_gap,
            ruler_height=0,
            left_margin=self.left_margin,
            lanes=lanes,
        )

    def _update_scroll_extent(self) -> None:
        """Donne à la grille la largeur réelle de la timeline.

        L'ancienne largeur fixe (1600 px) empêchait de faire défiler
        un montage plus long que quelques secondes. La largeur suit
        la durée et le zoom. Elle peut être retirée si la grille
        devient un canevas virtuel qui ne grandit plus avec le temps.
        """
        if not hasattr(self, "timeline_grid"):
            return
        pixels = self.pixels_per_second * self.zoom
        width = int(self.left_margin + max(self.duration_seconds, 1.0) * pixels + 120)
        viewport = self.scroll.viewport().width() if hasattr(self, "scroll") else 0
        self._cull_guard = True
        try:
            self.timeline_grid.setMinimumWidth(max(width, viewport, 400))
        finally:
            self._cull_guard = False

    def _rebuild_track_headers(self) -> None:
        for header in list(self.track_header_widgets.values()):
            header.setParent(None)
            header.deleteLater()
        self.track_header_widgets.clear()
        if self.project is None:
            return
        for index, track in enumerate(self.project.tracks):
            header = TrackRowHeader(track, self.timeline_grid)
            row_h = self.row_height_of(track)
            header.setFixedHeight(row_h)
            header.setGeometry(
                0,
                int(self.row_top(index)),
                self.left_margin,
                row_h,
            )
            header.show()
            header.lock_toggled.connect(self.toggle_track_lock_requested)
            header.visible_toggled.connect(self.toggle_track_visible_requested)
            header.mute_toggled.connect(self.toggle_track_muted_requested)
            header.solo_toggled.connect(self.solo_toggled.emit)
            header.arm_toggled.connect(self.arm_toggled.emit)
            header.height_cycle_requested.connect(self.height_cycle_requested.emit)
            header.collapse_toggled.connect(self.collapse_toggled.emit)
            header.move_up_requested.connect(self.move_track_up_requested)
            header.move_down_requested.connect(self.move_track_down_requested)
            header.remove_requested.connect(self.remove_track_requested)
            header.rename_requested.connect(self._on_rename_requested)
            self.track_header_widgets[track.id] = header

    def _visibility_window(
        self,
    ) -> tuple[tuple[float, float] | None, tuple[int, int] | None]:
        """Fenêtre temps / pistes à monter.

        ``None`` signifie « tout monter ». C'est le cas tant que le
        viewport n'a pas de taille réelle (tests, premier layout) pour
        ne pas cacher les clips d'un petit projet avant affichage.
        Le culling vertical ne démarre qu'à partir de 12 pistes : en
        dessous, le coût des en-têtes reste négligeable et les projets
        de démonstration gardent tous leurs clips.
        """
        if not hasattr(self, "scroll"):
            return None, None
        viewport = self.scroll.viewport()
        pixels = self.pixels_per_second * self.zoom
        time_range = None
        if viewport.width() >= 48 and pixels > 0:
            scroll_x = self.scroll.horizontalScrollBar().value()
            left_px = scroll_x - self._overscan_px
            right_px = scroll_x + viewport.width() + self._overscan_px
            time_range = (
                (left_px - self.left_margin) / pixels,
                (right_px - self.left_margin) / pixels,
            )
        row_range = None
        track_count = len(self.project.tracks) if self.project is not None else 0
        if track_count >= 12 and viewport.height() >= 32 and self.project is not None:
            scroll_y = self.scroll.verticalScrollBar().value()
            top_visible = scroll_y - 80
            bottom_visible = scroll_y + viewport.height() + 80
            first = None
            last = None
            for index, track in enumerate(self.project.tracks):
                top = self.row_top(index)
                bottom = top + self.row_height_of(track)
                if bottom >= top_visible and top <= bottom_visible:
                    first = index if first is None else first
                    last = index
            if first is not None and last is not None:
                row_range = (first, last)
        return time_range, row_range

    def _clip_in_window(self, view: TimelineClipView, time_range, row_range) -> bool:
        if time_range is not None and (
            view.end < time_range[0] or view.start > time_range[1]
        ):
            return False
        if row_range is not None and not (row_range[0] <= view.track_index <= row_range[1]):
            return False
        return True

    def _sync_mounted_clips(self, *, refresh_views: bool = False) -> bool:
        """Monte les clips de la fenêtre visible et démonte les autres.

        Retourne ``True`` si l'ensemble des widgets a changé. Un clip
        en cours de glisser reste monté, sinon le geste serait coupé
        dès qu'il sort de l'écran.
        """
        if self._cull_guard:
            return False
        time_range, row_range = self._visibility_window()
        wanted: dict[str, TimelineClipView] = {}
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            dragging = widget is not None and widget.drag_mode is not None
            if dragging or self._clip_in_window(view, time_range, row_range):
                wanted[view.id] = view
        previous_ids = set(self.clip_widgets)
        changed = previous_ids != set(wanted)
        if not changed and not refresh_views:
            return False
        for clip_id in previous_ids - set(wanted):
            widget = self.clip_widgets.pop(clip_id)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        for view in wanted.values():
            widget = self.clip_widgets.get(view.id)
            if widget is None:
                widget = ClipWidget(view, self.timeline_grid)
                self.clip_widgets[view.id] = widget
            elif widget.drag_mode is None:
                # Le widget réutilisé doit voir le clip à jour, sinon
                # le prochain glisser repart de l'ancienne géométrie.
                widget.view = view
                widget.pending_start = view.start
                widget.pending_end = view.end
            widget.refresh_style()
            widget.show()
        return changed

    def _on_timeline_scrolled(self, _value: int = 0) -> None:
        if self._cull_guard:
            return
        if self._sync_mounted_clips():
            self._layout_children()
        self._sync_ruler()

    def _restyle_clip(self, clip_id: str | None) -> None:
        if not clip_id:
            return
        widget = self.clip_widgets.get(clip_id)
        if widget is not None:
            widget.refresh_style()

    def _sync_transition_widgets(self) -> None:
        """Projette les transitions persistantes dans la timeline."""
        transitions = list(getattr(self.project, "transitions", [])) if self.project else []
        wanted = {transition.id: transition for transition in transitions}
        for transition_id in set(self.transition_widgets) - set(wanted):
            widget = self.transition_widgets.pop(transition_id)
            widget.hide()
            widget.deleteLater()
        for transition_id, transition in wanted.items():
            widget = self.transition_widgets.get(transition_id)
            if widget is None:
                widget = TransitionMarkerWidget(transition_id, self)
                self.transition_widgets[transition_id] = widget
            widget.refresh_style(self._transition_label(transition))
            widget.show()
            widget.raise_()
        if self.selected_transition_id not in wanted:
            self.selected_transition_id = None
        self._layout_transition_widgets()

    @staticmethod
    def _transition_label(transition) -> str:
        labels = {
            "crossfade": "FONDU",
            "fade_black": "NOIR",
            "wipe_left": "BALAYAGE ←",
            "wipe_right": "BALAYAGE →",
        }
        return f"{labels.get(transition.type.value, 'TRANSITION')} · {transition.duration:.1f}s"

    def _layout_transition_widgets(self) -> None:
        if self.project is None:
            return
        views = {view.id: view for view in self.clip_views}
        pixels = self.pixels_per_second * self.zoom
        for transition in self.project.transitions:
            widget = self.transition_widgets.get(transition.id)
            outgoing = views.get(transition.from_clip_id)
            incoming = views.get(transition.to_clip_id)
            if widget is None or outgoing is None or incoming is None:
                continue
            width = max(54, int(transition.duration * pixels))
            x = int(self.left_margin + (outgoing.end - transition.duration) * pixels)
            y = int(self.row_top(outgoing.track_index) + 3)
            widget.setGeometry(x, y, width, 20)

    def select_transition(self, transition_id: str) -> None:
        if self.project is None or not any(
            item.id == transition_id for item in self.project.transitions
        ):
            return
        self.selected_transition_id = transition_id
        self._set_selection([], None, announce=False)
        self._sync_transition_widgets()
        self.transition_selected.emit(transition_id)

    def clear_transition_selection(self) -> None:
        if self.selected_transition_id is None:
            return
        self.selected_transition_id = None
        self._sync_transition_widgets()

    def _layout_children(self) -> None:
        """Repositionne en-têtes et clips — sans rien recréer.

        Utilisé par le redimensionnement : aucun widget n'est alloué ni
        détruit, ce qui garde le drag du séparateur fluide même avec
        beaucoup de clips.
        """
        for header in self.track_header_widgets.values():
            index = self._track_index(header.track.id)
            if index is None or self.project is None:
                continue
            height = self.row_height_of(self.project.tracks[index])
            header.setFixedHeight(height)
            header.setGeometry(0, int(self.row_top(index)), self.left_margin, height)
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None or widget.drag_mode is not None:
                continue
            widget.setGeometry(*self.clip_rect(view, view.start, view.end))
            widget.raise_()
        self._layout_transition_widgets()
        self._publish_overlay()
        self._schedule_previews()
        self._sync_ruler()

    def _track_index(self, track_id: str) -> int | None:
        """Index d'une piste dans le projet, ou ``None`` si absente."""
        if self.project is None:
            return None
        for index, track in enumerate(self.project.tracks):
            if track.id == track_id:
                return index
        return None

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
        self._zoom_by(1 / 1.25, self.scroll.viewport().width() / 2)

    def zoom_in(self) -> None:
        self._zoom_by(1.25, self.scroll.viewport().width() / 2)

    def fit_timeline(self) -> None:
        """Cale le zoom pour voir tout le montage. Le playhead ne bouge pas."""
        self.zoom = fit_zoom(
            self.duration_seconds,
            self.scroll.viewport().width(),
            self.left_margin,
            self.pixels_per_second,
        )
        self._apply_zoom()
        self.scroll.horizontalScrollBar().setValue(0)

    def _apply_zoom(self) -> None:
        """Le zoom change la géométrie, pas les pistes.

        Recréer les en-têtes ici faisait clignoter la barre de pistes
        et réallouait tous les boutons à chaque cran.
        """
        self._update_zoom_label()
        self._update_scroll_extent()
        self._sync_mounted_clips()
        self._layout_children()
        self._sync_transition_widgets()
        self.update()

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
        self._update_scroll_extent()
        self._sync_ruler()
        self._publish_overlay()
        self.update()

    def setPlaybackPosition(self, position_ms):
        self.set_playhead_seconds(float(position_ms))

    def set_playhead_seconds(self, position_seconds: float) -> None:
        previous = self.playhead_seconds
        self.playhead_seconds = min(
            max(float(position_seconds), 0.0), self.duration_seconds
        )
        self.time_label.setText(self.format_time(self.playhead_seconds))
        if hasattr(self, "timecode_label"):
            self.timecode_label.setText(format_timecode(self.playhead_seconds, self.fps))
        # Synchronise le timecode turquoise du panneau de prévisualisation.
        preview_panel = getattr(self, "_preview_panel", None)
        if preview_panel is not None and hasattr(preview_panel, "set_timecode"):
            preview_panel.set_timecode(self.playhead_seconds, self.duration_seconds)
        if abs(previous - self.playhead_seconds) < 1e-6:
            self._sync_ruler()
            return
        # Seules les deux bandes de la tête sont invalidées, dans la
        # grille qui défile avec les clips. La règle, elle, est une
        # fine bande indépendante.
        self._invalidate_playhead_at(previous)
        self._invalidate_playhead_at(self.playhead_seconds)
        self._sync_ruler()

    def _invalidate_playhead_at(self, seconds: float) -> None:
        x = int(self.left_margin + seconds * self.pixels_per_second * self.zoom)
        if hasattr(self, "timeline_grid"):
            self.timeline_grid.update(x - 8, 0, 16, max(self.timeline_grid.height(), 1))

    def setPlayState(self, is_playing):
        from ui.icons import make_icon
        if is_playing:
            self.play_button.setIcon(make_icon(IconName.PAUSE, size=Iconography.md))
        else:
            self.play_button.setIcon(make_icon(IconName.PLAY, size=Iconography.md))

    def row_height_of(self, track) -> int:
        if getattr(track, "collapsed", False):
            return _COLLAPSED_HEIGHT
        return _HEIGHTS.get(getattr(track, "height_mode", "normal"), self.track_height)

    def row_top(self, index: int) -> int:
        top = _CONTENT_TOP
        if self.project is None:
            return top + index * (self.track_height + self.track_gap)
        for cursor, track in enumerate(self.project.tracks):
            if cursor == index:
                return top
            top += self.row_height_of(track) + self.track_gap
        return top

    def rows_span(self) -> int:
        if self.project is None or not self.project.tracks:
            return _CONTENT_TOP + self.track_height + 16
        total = _CONTENT_TOP
        for track in self.project.tracks:
            total += self.row_height_of(track) + self.track_gap
        return total + 16

    def clip_rect(self, view, start: float, end: float, track_index: int | None = None):
        index = view.track_index if track_index is None else track_index
        height = self.track_height
        if self.project is not None and 0 <= index < len(self.project.tracks):
            height = self.row_height_of(self.project.tracks[index])
        scale = self.pixels_per_second * self.zoom
        x = int(self.left_margin + start * scale)
        width = max(40, int((end - start) * scale))
        return (x, int(self.row_top(index)), width, height)

    def track_is_locked(self, track_id: str) -> bool:
        if self.project is None:
            return False
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return bool(track and track.locked)

    def track_is_collapsed(self, track_id: str) -> bool:
        if self.project is None:
            return False
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return bool(track and getattr(track, "collapsed", False))

    def track_height_mode(self, track_id: str) -> str:
        if self.project is None:
            return "normal"
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        return getattr(track, "height_mode", "normal") if track else "normal"

    def clip_model(self, clip_id: str):
        if self.project is None:
            return None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
        return None

    def _is_selected(self, clip_id: str) -> bool:
        return clip_id in self.selected_clip_ids or clip_id == self.selected_clip_id

    def _set_selection(self, ids, primary: str | None, announce: bool) -> None:
        if ids:
            self.clear_transition_selection()
        previous = set(self.selected_clip_ids)
        if self.selected_clip_id:
            previous.add(self.selected_clip_id)
        self.selected_clip_ids = {clip_id for clip_id in ids if clip_id}
        self.selected_clip_id = primary if primary in self.selected_clip_ids else (
            next(iter(self.selected_clip_ids), None)
        )
        for clip_id in previous.symmetric_difference(self.selected_clip_ids):
            self._restyle_clip(clip_id)
        if self.selected_clip_id:
            self._restyle_clip(self.selected_clip_id)
        if announce and self.selected_clip_id:
            self.clip_selected.emit(self.selected_clip_id)
        elif announce and not self.selected_clip_ids:
            self.selection_cleared.emit()

    def _select_from_pointer(self, clip_id: str, modifiers, drag: bool) -> None:
        ctrl = bool(modifiers & (Qt.ControlModifier | Qt.MetaModifier))
        shift = bool(modifiers & Qt.ShiftModifier)
        if shift and self._selection_anchor:
            ids = clip_ids_in_range(self.clip_views, self._selection_anchor, clip_id)
            self._set_selection(ids, clip_id, announce=True)
            return
        if ctrl:
            ids = set(self.selected_clip_ids)
            if clip_id in ids:
                ids.remove(clip_id)
            else:
                ids.add(clip_id)
            self._set_selection(ids, clip_id if clip_id in ids else next(iter(ids), None), announce=True)
            return
        if drag and clip_id in self.selected_clip_ids and len(self.selected_clip_ids) > 1:
            self.selected_clip_id = clip_id
            self.clip_selected.emit(clip_id)
            return
        self._selection_anchor = clip_id
        self._set_selection([clip_id], clip_id, announce=True)

    def begin_drag(self, clip_id: str) -> None:
        self._drag_anchor = clip_id
        self._drag_delta = 0.0

    def snap_time(self, seconds: float, anchor_id: str) -> float:
        if not self.snap_enabled or self.project is None:
            self.snap_line_x = None
            return seconds
        scale = self.pixels_per_second * self.zoom
        threshold = self.snap_threshold_pixels / scale if scale else 0.0
        excluded = set(self.selected_clip_ids)
        excluded.add(anchor_id)
        snapped = snap_edit_position(
            self.project,
            seconds,
            threshold,
            excluded_clip_ids=excluded,
            playhead_seconds=self.playhead_seconds,
        )
        if abs(snapped - seconds) > 1e-6:
            self.snap_line_x = self.left_margin + snapped * scale
        else:
            self.snap_line_x = None
        self._publish_overlay()
        return snapped

    def preview_group_move(self, anchor_id: str, delta: float, global_y: int) -> None:
        """Déplace tout le groupe du même écart de temps et de pistes."""
        self._drag_delta = delta
        self._drag_track_delta = 0
        anchor = self.clip_widgets.get(anchor_id)
        if anchor is not None and self.project is not None:
            target = self._track_index_at_global_y(global_y, anchor.view.track_type)
            if target is not None:
                self._drag_track_delta = target - anchor.view.track_index
        for view in self.clip_views:
            if view.id != anchor_id and view.id not in self.selected_clip_ids:
                continue
            widget = self.clip_widgets.get(view.id)
            if widget is None or widget.drag_mode in {"trim-left", "trim-right"}:
                continue
            widget.pending_start = max(0.0, view.start + delta)
            widget.pending_end = widget.pending_start + (view.end - view.start)
            widget.pending_track_index = self._destination_index(view.track_index)
            widget._apply_pending_geometry()

    def _destination_index(self, origin: int) -> int:
        if self.project is None:
            return origin
        return shifted_track_index(self.project.tracks, origin, self._drag_track_delta)

    def finish_group_move(self, anchor_id: str) -> None:
        placements = []
        for view in self.clip_views:
            if view.id != anchor_id and view.id not in self.selected_clip_ids:
                continue
            widget = self.clip_widgets.get(view.id)
            index = widget.pending_track_index if widget is not None else self._destination_index(view.track_index)
            track_id = view.track_id
            if self.project is not None and 0 <= index < len(self.project.tracks):
                track_id = self.project.tracks[index].id
            placements.append(
                ClipPlacement(
                    clip_id=view.id,
                    timeline_start=max(0.0, view.start + self._drag_delta),
                    track_id=track_id,
                )
            )
        if placements:
            self.clips_move_requested.emit(placements)

    def preview_slip(self, widget: ClipWidget, delta: float) -> None:
        self._slip_delta = delta
        widget.duration_label.setText(f"slip {delta:+.2f}s")

    def preview_slide(self, widget: ClipWidget, new_start: float) -> None:
        widget.pending_start = new_start
        widget.pending_end = new_start + (widget.drag_original_end - widget.drag_original_start)
        widget._apply_pending_geometry()

    def preview_roll(self, widget: ClipWidget, mode: str, edge_time: float) -> None:
        if mode == "roll-left":
            widget.pending_start = min(widget.drag_original_end - 0.1, max(0.0, edge_time))
        else:
            widget.pending_end = max(widget.drag_original_start + 0.1, edge_time)
        widget._apply_pending_geometry()

    def preview_fade(self, widget: ClipWidget, which: str, seconds: float) -> None:
        """Prévisualise un fondu pendant le glisser de sa poignée.

        La valeur est bornée à la durée du clip et ne peut pas empiéter
        sur le fondu opposé : c'est le modèle qui applique la contrainte
        finale, l'aperçu doit juste rester plausible.
        """
        duration = max(widget.view.end - widget.view.start, 0.0)
        bounded = max(0.0, min(float(seconds), duration))
        other = widget.fade_seconds("out" if which == "in" else "in")
        bounded = max(0.0, min(bounded, duration - other))
        widget.pending_fade = bounded
        model = widget.clip_model()
        if model is not None:
            if which == "in":
                model.set_fade_in(bounded)
            else:
                model.set_fade_out(bounded)
        widget.update()

    def synthetic_thumb(self, view: TimelineClipView, index: int, slots: int) -> QPixmap:
        """Vignette de secours, peinte sans décoder le média."""
        key = f"synth:{view.id}:{index}:{slots}"
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        image = QImage(160, 90, QImage.Format_RGB32)
        color = QColor(view.color_key)
        image.fill(color.darker(110 + index * 18))
        painter = QPainter(image)
        painter.setPen(QColor("white"))
        painter.drawText(image.rect(), Qt.AlignCenter, view.label or str(index + 1))
        painter.end()
        pixmap = QPixmap.fromImage(image)
        if len(self._pixmaps) > 48:
            self._pixmaps.clear()
        self._pixmaps[key] = pixmap
        return pixmap

    def set_tool(self, name: str) -> None:
        """Outil actif : select, blade, roll, slip ou slide. Un seul à la fois."""
        if name not in {"select", "blade", "roll", "slip", "slide"}:
            name = "select"
        self.tool = name
        buttons = {
            "blade": getattr(self, "blade_button", None),
            "roll": getattr(self, "roll_button", None),
            "slip": getattr(self, "slip_button", None),
            "slide": getattr(self, "slide_button", None),
        }
        for tool, button in buttons.items():
            if button is None:
                continue
            button.blockSignals(True)
            button.setChecked(tool == name)
            button.blockSignals(False)

    def _track_index_at_global_y(self, global_y: int, track_type: str) -> int | None:
        if self.project is None:
            return None
        from PySide6.QtCore import QPoint

        y = self.timeline_grid.mapFromGlobal(QPoint(0, int(global_y))).y()
        for index, track in enumerate(self.project.tracks):
            top = self.row_top(index)
            if top <= y <= top + self.row_height_of(track) and track.type == track_type and not track.locked:
                return index
        return None

    def begin_marquee(self, origin) -> None:
        if self._marquee is None:
            self._marquee = QRubberBand(QRubberBand.Rectangle, self.timeline_grid)
        self._marquee_origin = origin
        self._marquee.setGeometry(QRect(origin, origin))
        self._marquee.show()

    def update_marquee(self, pos) -> None:
        if self._marquee is None or self._marquee_origin is None:
            return
        self._marquee.setGeometry(QRect(self._marquee_origin, pos).normalized())

    def finish_marquee(self, pos) -> None:
        if self._marquee is None or self._marquee_origin is None:
            return
        rect = QRect(self._marquee_origin, pos).normalized()
        self._marquee.hide()
        self._marquee_origin = None
        if rect.width() < 4 and rect.height() < 4:
            self._set_selection([], None, announce=True)
            return
        ids = []
        for view in self.clip_views:
            x, y, width, height = self.clip_rect(view, view.start, view.end)
            if rect.intersects(QRect(x, y, width, height)):
                ids.append(view.id)
        primary = ids[-1] if ids else None
        self._selection_anchor = primary
        self._set_selection(ids, primary, announce=True)

    def open_clip_menu(self, clip_id: str, global_pos) -> None:
        if clip_id not in self.selected_clip_ids:
            self.select_clip(clip_id)
        menu = QMenu(self)
        cut = menu.addAction("Couper au playhead")
        duplicate = menu.addAction("Dupliquer")
        toggle = menu.addAction("Activer / désactiver")
        ripple = menu.addAction("Supprimer et refermer")
        remove = menu.addAction("Supprimer")
        chosen = menu.exec(global_pos)
        if chosen is cut:
            self.blade_cut_requested.emit(clip_id, self.playhead_seconds)
        elif chosen is duplicate:
            self.duplicate_requested.emit()
        elif chosen is toggle:
            self.toggle_enabled_requested.emit()
        elif chosen is ripple:
            self.ripple_delete_requested.emit()
        elif chosen is remove:
            window = self.window()
            if hasattr(window, "delete_selected_clip_with_check"):
                window.delete_selected_clip_with_check()

    def attach_runtime(self, runtime) -> None:
        self._runtime = runtime

    def attach_preview_panel(self, preview_panel) -> None:
        """Référence faible vers le panneau de prévisualisation.

        Le timecode turquoise de la barre de transport se met à jour
        à chaque changement de tête de lecture. On garde une référence
        faible (champ ``_preview_panel``) pour pouvoir la nettoyer si
        le panneau est détruit avant la timeline.
        """
        self._preview_panel = preview_panel
        # Synchronisation immédiate pour aligner les deux horloges.
        if hasattr(preview_panel, "set_timecode"):
            preview_panel.set_timecode(self.playhead_seconds, self.duration_seconds)

    def pixmap_for(self, key: str, data: bytes):
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if len(self._pixmaps) > 48:
                self._pixmaps.clear()
            self._pixmaps[key] = pixmap
        return pixmap

    def _schedule_previews(self) -> None:
        runtime = self._runtime
        if runtime is None or self.project is None:
            return
        filmstrips = runtime.resolved_profile().filmstrips
        for view in self.clip_views:
            widget = self.clip_widgets.get(view.id)
            if widget is None or not view.source_path or not os.path.isfile(view.source_path):
                continue
            if self.track_is_collapsed(view.track_id):
                continue
            if view.track_type == "audio":
                bins = waveform_bins(max(widget.width(), 16), self.track_height_mode(view.track_id))
                key = waveform_cache_key(view.source_path, bins)
                if runtime.cache.get(key) is None:
                    self._submit_preview(
                        key,
                        lambda token, path=view.source_path, count=bins, cache_key=key: self._waveform_job(
                            token, path, count, cache_key
                        ),
                    )
            elif view.track_type == "video" and filmstrips:
                clip = self.clip_model(view.id)
                if clip is None:
                    continue
                slots = thumbnail_slots(widget.width(), enabled=True)
                for instant in thumbnail_source_times(clip.source_in, clip.source_out, slots):
                    thumb_key = thumbnail_cache_key(view.source_path, instant, 160)
                    if runtime.cache.get(thumb_key) is None:
                        self._submit_preview(
                            thumb_key,
                            lambda token, path=view.source_path, time=instant, cache_key=thumb_key: self._thumb_job(
                                token, path, time, cache_key
                            ),
                        )

    def _submit_preview(self, key: str, fn) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.schedule(key, fn, priority=PRIORITY_VISIBLE)
        if self._preview_timer is None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setInterval(300)
            self._preview_timer.timeout.connect(self._drain_previews)
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _waveform_job(self, token, path: str, bins: int, key: str) -> None:
        if token.cancelled or self._runtime is None:
            return
        peaks = extract_waveform_peaks(path, bins)
        self._runtime.mailbox.push(
            key,
            peaks if peaks else (),
            max(32, bins * 8),
            session_id=self._runtime.session_id,
        )

    def _thumb_job(self, token, path: str, instant: float, key: str) -> None:
        if token.cancelled or self._runtime is None:
            return
        image = extract_thumbnail(path, instant, 160)
        self._runtime.mailbox.push(
            key,
            image if image else b"",
            len(image) if image else 1,
            session_id=self._runtime.session_id,
        )

    def _drain_previews(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        # Le filtre de session est appliqué par la mailbox : un résultat
        # produit pour un projet déjà remplacé n'atteint jamais le cache.
        items = runtime.mailbox.drain(runtime.session_id)
        for key, value, size, namespace in items:
            runtime.cache.put(key, value, size_bytes=size, namespace=namespace)
        if items:
            for widget in self.clip_widgets.values():
                widget.update()
        if runtime.tasks.pending == 0 and not items and self._preview_timer is not None:
            self._preview_timer.stop()

    def _zoom_by(self, factor: float, viewport_x: float) -> None:
        old = self.zoom
        new = clamp_zoom(old * factor)
        if abs(new - old) < 1e-4:
            return
        scroll = self.scroll.horizontalScrollBar().value()
        new_scroll = scroll_for_anchor(
            old,
            new,
            viewport_x,
            scroll,
            self.left_margin,
            self.pixels_per_second,
        )
        self.zoom = new
        self._apply_zoom()
        self.scroll.horizontalScrollBar().setValue(new_scroll)

    def _on_ripple_toggled(self, checked: bool) -> None:
        self.ripple_enabled = bool(checked)

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

    def _sync_ruler(self) -> None:
        if not hasattr(self, "ruler"):
            return
        palette = _current_palette()
        scroll = self.scroll.horizontalScrollBar().value() if hasattr(self, "scroll") else 0
        markers = list(getattr(self.project, "markers", [])) if self.project is not None else []
        self.markers = markers
        self.ruler.sync(
            scroll_x=scroll,
            zoom=self.zoom,
            pixels_per_second=self.pixels_per_second,
            duration=self.duration_seconds,
            fps=self.fps,
            playhead=self.playhead_seconds,
            origin=self.left_margin,
            markers=markers,
            background=palette.ruler_bg,
            tick=palette.ruler_line,
            text=palette.muted,
            playhead_color=palette.playhead,
            marker_color=palette.marker,
        )

    def _publish_overlay(self) -> None:
        if not hasattr(self, "timeline_grid"):
            return
        scale = self.pixels_per_second * self.zoom
        self.timeline_grid.playhead_x = self.left_margin + self.playhead_seconds * scale
        self.timeline_grid.snap_x = self.snap_line_x
        self.timeline_grid.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = _current_palette()
        painter.fillRect(event.rect(), QColor(palette.timeline_bg))
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
