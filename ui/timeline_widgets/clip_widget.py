"""Widget visuel d'un clip de la timeline."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QWidget

from core.media_previews import (
    audio_envelope_cache_key,
    thumbnail_cache_key,
    thumbnail_slots,
    thumbnail_source_times,
)
from core.timeline_view_model import TimelineClipView
from ui.theme import BLACK, mix_colors, set_stylesheet_if_changed
from ui.timeline_widgets.clip_style import (
    cache_dot_color,
    clip_body_style,
    clip_fill,
    duration_style,
    title_style,
)
from ui.timeline_widgets.common import _color_for_track_type, _current_palette
from ui.timeline_widgets.nested_clip import handle_nested_double_click, paint_nested_decoration
from ui.timeline_widgets.time_overlay import paint_time_overlays
from ui.timeline_widgets.waveform import paint_waveform

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:  # import de typage seul : évite le cycle clip -> panneau
    from ui.timeline_panel import TimelinePanel

# ---------------------------------------------------------------------------
# Clip widget
# ---------------------------------------------------------------------------



_TITLE_BAND = 16
"""Hauteur (px) de la bande du nom, en haut d'un clip : la forme d'onde d'un clip audio commence en dessous."""

class ClipWidget(QWidget):
    """Widget visuel représentant un :class:`TimelineClipView` immuable."""

    def __init__(self, view: TimelineClipView, parent: "TimelinePanel | None" = None):
        super().__init__(parent)
        self.view = view
        cursor = parent
        # Le panneau se reconnaît à ``_is_timeline_host`` (duck-typing) : importer
        # ``TimelinePanel`` ici recréerait le cycle d'import clip <-> panneau.
        while cursor is not None and not getattr(cursor, "_is_timeline_host", False):
            cursor = cursor.parent()
        self.parent_timeline = cursor
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
        self._hover_body = False
        self._hover_keyframe: str | None = None                 # identifiant de l'image-clé survolée (jamais plus de deux repeints)

        # Label du clip (nom).
        self.label = QLabel(self.view.label, self)
        set_stylesheet_if_changed(self.label, title_style(_current_palette()))
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.duration_label = QLabel(
            self.parent_timeline.format_time(self.view.end - self.view.start) if self.parent_timeline else "",
            self,
        )
        set_stylesheet_if_changed(self.duration_label, duration_style(_current_palette()))
        self.duration_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._cache_dot = None
        self.refresh_style()

    def set_cache_state(self, state: str) -> None:
        """Etat du cache sur la timeline : 'cached' / 'pending' / 'none'."""
        self._cache_state = state
        color = cache_dot_color(state, _current_palette())
        try:
            from PySide6.QtWidgets import QLabel

            if self._cache_dot is None:
                self._cache_dot = QLabel("●", self)
                self._cache_dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            set_stylesheet_if_changed(self._cache_dot, "color: %s; font-size: 10px; background: transparent;" % color)
            self._cache_dot.move(max(0, self.width() - 18), 2)
            self._cache_dot.resize(16, 14)
            self._cache_dot.setVisible(state in ("cached", "pending"))
        except Exception:
            LOGGER.debug("Mise à jour du témoin de cache du clip en échec : témoin non actualisé", exc_info=True)

    def refresh_style(self) -> None:
        parent = self.parent_timeline
        palette = _current_palette()
        selected = parent is not None and parent._is_selected(self.view.id)
        track_type = getattr(self.view, "track_type", None)
        base_color = _color_for_track_type(track_type or "video", palette)
        # Le fond dit la catégorie du clip (vidéo, audio, titre, calque, imbriqué) ; la sélection éclaircit le fond et passe
        # la bordure à l'accent. Le sélecteur est limité au corps du clip : un ``QWidget`` nu peindrait aussi les libellés.
        self.setObjectName("clipBody")
        set_stylesheet_if_changed(self, clip_body_style(self.view, palette, selected=selected, hovered=self._hover_body))
        set_stylesheet_if_changed(self.label, title_style(palette))
        set_stylesheet_if_changed(self.duration_label, duration_style(palette))
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
        """Courbe de vitesse et badges de remappage temporel (voir :mod:`ui.timeline_widgets.time_overlay`)."""
        paint_time_overlays(self)

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
        painter.setBrush(QColor(mix_colors(clip_fill(self.view, palette), BLACK, 0.4)))
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
        """Double-clic : ouvre une séquence imbriquée, ou remet un fondu à zéro (poignée)."""
        if handle_nested_double_click(self, event):
            return
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
                    self.drag_start_x = int(event.globalPosition().x())
                    self.drag_original_fade = self.fade_seconds(which)
                    parent.begin_drag(self.view.id)
                    parent._select_from_pointer(
                        self.view.id, event.modifiers(), drag=False
                    )
                    event.accept()
                    return
        if parent.tool == "slip" and not locked:
            self.drag_mode = "slip"
            self.drag_start_x = int(event.globalPosition().x())
            parent._select_from_pointer(self.view.id, event.modifiers(), drag=False)
            event.accept()
            return
        if parent.tool == "slide" and not locked:
            self.drag_mode = "slide"
            self.drag_start_x = int(event.globalPosition().x())
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
            self.drag_start_x = int(event.globalPosition().x())
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
        hit = None if locked or parent.tool != "select" else self._keyframe_hit(event.position())
        if hit:
            if parent.selected_clip_id != self.view.id:
                parent._select_from_pointer(self.view.id, Qt.NoModifier, drag=False)
            additive = bool(event.modifiers() & Qt.ShiftModifier)
            parent.press_keyframes(self._keyframe_refs(hit), additive)
            self.drag_mode = "keyframes"
            self.drag_start_x = int(event.globalPosition().x())
            self._keyframe_anchor_time = self.view.start + hit[0].time_seconds
            event.accept()
            return
        if getattr(parent, "selected_keyframes", None):
            parent.keyframes_selected.emit([], False)
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
        self.drag_start_x = int(event.globalPosition().x())
        self.drag_original_start = self.view.start
        self.drag_original_end = self.view.end
        self.pending_start = self.view.start
        self.pending_end = self.view.end
        self.pending_track_index = self.view.track_index
        parent.begin_drag(self.view.id)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_mode is None:
            self._update_hover(event.position())
            return
        parent = self.parent_timeline
        if parent is None:
            return
        scale = parent.pixels_per_second * parent.zoom
        if scale <= 0:
            return
        delta_seconds = (int(event.globalPosition().x()) - self.drag_start_x) / scale
        if self.drag_mode == "keyframes":
            parent.preview_keyframe_drag(self._keyframe_anchor_time, delta_seconds, self.view.id)
            event.accept()
            return
        if self.drag_mode in {"fade-in", "fade-out"}:
            which = self.drag_mode.split("-", 1)[1]
            parent.preview_fade(self, which, self.drag_original_fade + delta_seconds)
        elif self.drag_mode == "move":
            proposed = max(0.0, self.drag_original_start + delta_seconds)
            proposed = parent.snap_time(proposed, anchor_id=self.view.id)
            delta = proposed - self.drag_original_start
            parent.preview_group_move(self.view.id, delta, int(event.globalPosition().y()))
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

    def _update_hover(self, position) -> None:
        """Suit le losange sous le pointeur ; ne repeint que lorsque le survol change (aucun coût au repos)."""
        hit = self._keyframe_hit(position)
        hovered = self._keyframe_refs(hit)[0].keyframe_id if hit else None
        if hovered != self._hover_keyframe:
            self._hover_keyframe = hovered
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt
        super().enterEvent(event)
        if not self._hover_body:
            self._hover_body = True
            self.refresh_style()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt
        super().leaveEvent(event)
        if self._hover_body or self._hover_keyframe is not None:
            self._hover_body = False
            self._hover_keyframe = None
            self.refresh_style()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        parent = self.parent_timeline
        if parent is not None and self.drag_mode is not None:
            if self.drag_mode == "keyframes":
                parent.finish_keyframe_drag()
            elif self.drag_mode == "move":
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
        self._paint_media_preview(event.rect())
        self._paint_fade_handles()
        self._paint_effect_badge()
        self._paint_time_remapping_badges()
        self._paint_keyframes()
        paint_nested_decoration(self)

    # ------------------------------------------------------------------
    # Images-clés (affichage, sélection, glisser temporel)
    # ------------------------------------------------------------------

    _KEYFRAME_SIZE = 10
    _KEYFRAME_MARGIN = 5

    def _keyframe_items(self) -> list[tuple[QRectF, list]]:
        """Losanges dessinés : ``(rectangle, images-clés)``, un par instant et propriété."""
        keyframes = list(getattr(self.view, "keyframes", None) or [])
        parent = self.parent_timeline
        track_type = getattr(self.view, "track_type", None)
        if track_type not in {"video", "graphics", None} and not self.view.track_id.startswith(("V", "G")):
            keyframes = []                                                  # l'audio n'a pas de transformation à animer
        # Les points de vitesse (``time.speed``) se manipulent comme les autres : sélection, glisser, suppression.
        keyframes += list(getattr(self.view, "speed_points", None) or [])
        if not keyframes or parent is None:
            return []
        duration = max(self.view.end - self.view.start, 1e-6)
        pixels_per_second = parent.pixels_per_second * parent.zoom
        grouped: dict[float, list] = {}
        for kf in keyframes:
            grouped.setdefault(round(kf.time_seconds, 4), []).append(kf)
        size, margin = self._KEYFRAME_SIZE, self._KEYFRAME_MARGIN
        items: list[tuple[QRectF, list]] = []
        for time_seconds, frames in grouped.items():
            local = max(0.0, min(duration, time_seconds))
            x = local * pixels_per_second
            if x < margin - size or x > self.width() - margin + size:
                continue
            for index, kf in enumerate(sorted(frames, key=lambda k: k.property_name)):
                y = self.height() - margin - size - index * (size - 2)
                items.append((QRectF(x - size / 2, y, size, size), [kf]))
        return items

    def _keyframe_refs(self, frames) -> list:
        from core.keyframe_editing import KeyframeRef

        return [KeyframeRef(self.view.id, kf.property_name, kf.id) for kf in frames]

    def _keyframe_hit(self, position) -> list | None:
        """Losange sous le pointeur ; le plus proche quand des losanges se chevauchent."""
        best, best_distance = None, None
        for rect, frames in self._keyframe_items():
            if rect.adjusted(-3, -3, 3, 3).contains(position):
                center = rect.center()
                distance = (center.x() - position.x()) ** 2 + (center.y() - position.y()) ** 2
                if best_distance is None or distance < best_distance:
                    best, best_distance = frames, distance
        return best

    def _paint_keyframes(self) -> None:
        items = self._keyframe_items()
        if not items:
            return
        parent = self.parent_timeline
        pixels_per_second = parent.pixels_per_second * parent.zoom
        palette = _current_palette()
        drag = getattr(parent, "keyframe_drag_delta", 0.0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for rect, frames in items:
            ref = self._keyframe_refs(frames)[0]
            selected = parent.keyframe_selected(ref)
            hovered = self._hover_keyframe == ref.keyframe_id
            offset = drag * pixels_per_second if selected else 0.0
            x, y, size = rect.center().x() + offset, rect.top(), rect.width()
            polygon = QPolygonF(
                [
                    QPointF(x, y),
                    QPointF(x + size / 2, y + size / 2),
                    QPointF(x, y + size),
                    QPointF(x - size / 2, y + size / 2),
                ]
            )
            # Un seul langage pour tous les losanges : presque blanc au repos, accent éclairci au survol, accent plein quand il
            # est sélectionné ; toujours cerclé de l'anneau sombre (lisible sur un clip clair comme sombre).
            fill = palette.accent if selected else (palette.accent_hover if hovered else palette.diamond_filled)
            painter.setBrush(QColor(fill))
            painter.setPen(QPen(QColor(palette.diamond_border), 2 if selected or hovered else 1))
            painter.drawPolygon(polygon)
        painter.end()

    def _paint_media_preview(self, exposed: QRect | None = None) -> None:
        """Dessine une forme d'onde ou des vignettes déjà en cache.

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
            envelope = None
            if runtime is not None and path and os.path.isfile(path):
                envelope = runtime.cache.get(audio_envelope_cache_key(path))
            if envelope:
                paint_waveform(self, painter, parent, envelope, exposed or self.rect(), title_band=_TITLE_BAND)
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
