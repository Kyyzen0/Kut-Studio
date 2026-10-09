"""Bande d'automation de volume d'une piste audio, sous ses clips.

La courbe tracée est celle que l'export applique : les morceaux viennent de
:func:`core.audio_automation.automation_pieces`, la fonction dont l'export tire
son expression ``volume``. Une ligne droite relie deux points (maintien nul, le
défaut) ; un point posé avec un maintien garde son palier.

Gestes (piste déverrouillée) :

- double-clic sur la bande : un point, au gain et à l'instant pointés (aimanté
  comme un clip, puis calé sur une image) ;
- glisser un point : temps et gain, sans jamais dépasser ses voisins ; Maj ne
  change que le gain ;
- Suppr / Retour arrière, ou le menu contextuel : supprimer le point ; le menu
  remet aussi un point à 0 dB ou efface la courbe.

La bande ne modifie jamais le projet : elle émet une intention, que la fenêtre
applique avec une entrée d'historique par geste (comme le mixeur).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QToolTip, QWidget

from core.audio_automation import (
    DEFAULT_GAIN_MAX_DB,
    DEFAULT_GAIN_MIN_DB,
    AutomationPoint,
    automation_pieces,
)
from ui.i18n import translate
from ui.timeline_widgets.common import _current_palette

LANE_HEIGHT = 48
"""Hauteur (px) de la bande sous les clips d'une piste audio."""

_PAD = 5
"""Marge verticale : un point à +12 ou −24 dB reste entier dans la bande."""

_POINT_RADIUS = 3.5
_HIT_RADIUS = 7.0


def _format_gain(gain_db: float) -> str:
    """``−6,0`` / ``+1,5`` / ``0,0`` : signe typographique, virgule décimale."""
    text = f"{abs(gain_db):.1f}".replace(".", ",")
    if gain_db <= -0.05:
        return "−" + text
    if gain_db >= 0.05:
        return "+" + text
    return text


class AutomationLane(QWidget):
    """Courbe de volume éditable d'une piste audio (temps de la timeline)."""

    point_added = Signal(str, float, float, float)
    """``(piste, temps, gain_db, maintien)`` : un point posé à la main a un maintien nul (ligne droite)."""
    point_removed = Signal(str, float)
    point_updated = Signal(str, float, float, float)
    """``(piste, temps, gain_db, maintien)`` : le point garde son instant."""
    point_moved = Signal(str, float, float, float)
    """``(piste, ancien temps, nouveau temps, gain_db)``."""
    cleared = Signal(str)

    def __init__(self, track_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        cursor = parent
        while cursor is not None and not getattr(cursor, "_is_timeline_host", False):
            cursor = cursor.parent()
        self.host = cursor
        self.track_id = track_id
        self.points: list[AutomationPoint] = []
        self.locked = False
        self.selected: float | None = None
        """Instant du point sélectionné (clé du point dans le modèle)."""
        self._hover: int | None = None
        self._drag_index: int | None = None
        self._drag_origin: AutomationPoint | None = None
        self._drag_press = QPointF()
        self._drag_value: tuple[float, float] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setAccessibleName(translate("timeline.automation.lane"))

    # -- état ----------------------------------------------------------------------------------------------------------

    def set_state(self, points, *, locked: bool) -> None:
        """Points du modèle (triés) et verrou de la piste ; un geste en cours est abandonné."""
        self.points = sorted(points, key=lambda point: point.time_seconds)
        self.locked = bool(locked)
        self._drag_index = None
        self._drag_value = None
        if self.selected is not None and not any(abs(p.time_seconds - self.selected) < 1e-9 for p in self.points):
            self.selected = None
        self.setToolTip("" if self.locked else translate("timeline.automation.hint"))
        self.update()

    def shown_points(self) -> list[AutomationPoint]:
        """Points tels qu'affichés : ceux du modèle, avec le point en cours de glisser à sa position provisoire."""
        if self._drag_index is None or self._drag_value is None:
            return self.points
        time, gain = self._drag_value
        shown = list(self.points)
        shown[self._drag_index] = AutomationPoint(time, gain, self.points[self._drag_index].fade_seconds)
        return sorted(shown, key=lambda point: point.time_seconds)

    # -- géométrie -----------------------------------------------------------------------------------------------------

    def _scale(self) -> float:
        host = self.host
        return float(host.pixels_per_second * host.zoom) if host is not None else 100.0

    def x_of(self, seconds: float) -> float:
        return seconds * self._scale()

    def time_of(self, x: float) -> float:
        scale = self._scale()
        return max(0.0, x / scale) if scale > 0 else 0.0

    def y_of(self, gain_db: float) -> float:
        span = DEFAULT_GAIN_MAX_DB - DEFAULT_GAIN_MIN_DB
        ratio = (DEFAULT_GAIN_MAX_DB - max(DEFAULT_GAIN_MIN_DB, min(DEFAULT_GAIN_MAX_DB, gain_db))) / span
        return _PAD + ratio * max(1.0, self.height() - 2 * _PAD)

    def gain_of(self, y: float) -> float:
        span = DEFAULT_GAIN_MAX_DB - DEFAULT_GAIN_MIN_DB
        ratio = (y - _PAD) / max(1.0, self.height() - 2 * _PAD)
        gain = DEFAULT_GAIN_MAX_DB - ratio * span
        return round(max(DEFAULT_GAIN_MIN_DB, min(DEFAULT_GAIN_MAX_DB, gain)), 1)

    def _frame(self, seconds: float) -> float:
        fps = float(getattr(self.host, "fps", 0.0) or 0.0)
        return round(seconds * fps) / fps if fps > 0 else seconds

    def _snapped(self, seconds: float) -> float:
        """Aimantation de la timeline (clips, marqueurs, tête de lecture, temps), puis image entière."""
        host = self.host
        if host is not None and hasattr(host, "snap_time"):
            seconds = host.snap_time(max(0.0, seconds), anchor_id=f"automation:{self.track_id}")
        return self._frame(max(0.0, seconds))

    def point_at(self, position: QPointF) -> int | None:
        """Index du point sous ``position`` (le plus proche dans le rayon de saisie)."""
        best, best_distance = None, _HIT_RADIUS
        for index, point in enumerate(self.points):
            dx = self.x_of(point.time_seconds) - position.x()
            dy = self.y_of(point.gain_db) - position.y()
            distance = (dx * dx + dy * dy) ** 0.5
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    # -- peinture ------------------------------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        visible = event.rect()
        painter.fillRect(visible, QColor(palette.track_alt_bg))
        painter.setPen(QPen(QColor(palette.track_divider), 1))
        painter.drawLine(visible.left(), 0, visible.right(), 0)

        unity = self.y_of(0.0)
        guide = QColor(palette.muted)
        guide.setAlpha(110)
        painter.setPen(QPen(guide, 1, Qt.DashLine))
        painter.drawLine(QPointF(visible.left(), unity), QPointF(visible.right() + 1, unity))

        color = QColor(palette.track_audio)
        if self.locked:
            color.setAlpha(120)
        points = self.shown_points()
        left, right = self.time_of(visible.left()), self.time_of(visible.right() + 1)
        path = self._curve_path(points, left, right)
        fill = QPainterPath(path)
        fill.lineTo(self.x_of(right), self.height())
        fill.lineTo(self.x_of(left), self.height())
        fill.closeSubpath()
        shade = QColor(color)
        shade.setAlpha(38)
        painter.fillPath(fill, shade)
        painter.setPen(QPen(color, 1.6))
        painter.drawPath(path)

        selected = self.selected
        for index, point in enumerate(points):
            x = self.x_of(point.time_seconds)
            if x < visible.left() - _HIT_RADIUS or x > visible.right() + _HIT_RADIUS:
                continue
            active = (selected is not None and abs(point.time_seconds - selected) < 1e-9) or index == self._hover
            radius = _POINT_RADIUS + (1.5 if active else 0.0)
            painter.setPen(QPen(QColor(palette.timeline_bg), 1.2))
            painter.setBrush(QColor(palette.text_strong) if active else color)
            painter.drawEllipse(QPointF(x, self.y_of(point.gain_db)), radius, radius)
        painter.end()

    def _curve_path(self, points, left: float, right: float) -> QPainterPath:
        """Tracé de la courbe sur ``[left, right]`` (temps) : 0 dB sans point, sinon les morceaux de l'export."""
        path = QPainterPath()
        if not points:
            path.moveTo(self.x_of(left), self.y_of(0.0))
            path.lineTo(self.x_of(right), self.y_of(0.0))
            return path
        started = False
        for lo, hi, a, b in automation_pieces(points):
            if hi < left or lo > right:
                continue
            start, end = max(lo, left), min(hi, right)
            if end < start:
                continue

            def gain(t: float, lo=lo, hi=hi, a=a, b=b) -> float:
                return a if a == b else a + (b - a) * (t - lo) / (hi - lo)

            if not started:
                path.moveTo(self.x_of(start), self.y_of(gain(start)))
                started = True
            else:
                path.lineTo(self.x_of(start), self.y_of(gain(start)))
            path.lineTo(self.x_of(end), self.y_of(gain(end)))
        return path

    # -- souris et clavier ---------------------------------------------------------------------------------------------

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt
        if self.locked or event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self.point_at(event.position()) is None:
            time = self._snapped(self.time_of(event.position().x()))
            self.selected = time
            self.point_added.emit(self.track_id, time, self.gain_of(event.position().y()), 0.0)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        index = self.point_at(event.position())
        self.selected = self.points[index].time_seconds if index is not None else None
        if index is not None and not self.locked:
            self._drag_index = index
            self._drag_origin = self.points[index]
            self._drag_press = event.position()
            self._drag_value = None
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt
        if self._drag_index is None or self._drag_origin is None:
            hover = self.point_at(event.position())
            if hover != self._hover:
                self._hover = hover
                self.update()
            return
        origin = self._drag_origin
        if self._drag_value is None and (event.position() - self._drag_press).manhattanLength() < 3:
            return
        gain = self.gain_of(self.y_of(origin.gain_db) + event.position().y() - self._drag_press.y())
        time = origin.time_seconds
        if not event.modifiers() & Qt.ShiftModifier:
            proposed = origin.time_seconds + (event.position().x() - self._drag_press.x()) / max(self._scale(), 1e-9)
            time = self._clamped_between_neighbours(self._snapped(proposed))
        self._drag_value = (time, gain)
        self._show_value(event, time, gain)
        self.update()
        event.accept()

    def _clamped_between_neighbours(self, time: float) -> float:
        """Un point glissé reste strictement entre ses voisins (une image d'écart au moins)."""
        index = self._drag_index
        fps = float(getattr(self.host, "fps", 0.0) or 0.0)
        step = 1.0 / fps if fps > 0 else 1e-3
        low = self.points[index - 1].time_seconds + step if index > 0 else 0.0
        high = self.points[index + 1].time_seconds - step if index + 1 < len(self.points) else float("inf")
        if high < low:
            return self.points[index].time_seconds
        return max(low, min(high, time))

    def _show_value(self, event, time: float, gain: float) -> None:
        host = self.host
        clock = host.format_time(time) if host is not None and hasattr(host, "format_time") else f"{time:.2f}"
        QToolTip.showText(
            event.globalPosition().toPoint(),
            translate("timeline.automation.point_tip", gain=_format_gain(gain), time=clock),
            self,
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        origin, value = self._drag_origin, self._drag_value
        self._drag_index = None
        self._drag_origin = None
        self._drag_value = None
        if self.host is not None:
            self.host.snap_line_x = None
        QToolTip.hideText()
        if origin is not None and value is not None:
            time, gain = value
            self.selected = time
            if abs(time - origin.time_seconds) < 1e-9:
                if abs(gain - origin.gain_db) > 1e-9:
                    self.point_updated.emit(self.track_id, origin.time_seconds, gain, origin.fade_seconds)
            else:
                self.point_moved.emit(self.track_id, origin.time_seconds, time, gain)
        self.update()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt
        super().leaveEvent(event)
        if self._hover is not None:
            self._hover = None
            self.update()

    def event(self, event) -> bool:  # noqa: D401 - Qt
        # Suppr / Retour arrière sont aussi des raccourcis de la fenêtre (supprimer le clip sélectionné) : avec un
        # point sélectionné, la bande les réclame, sinon c'est le clip qui disparaîtrait.
        if (event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Delete, Qt.Key_Backspace)
                and self.selected is not None and not self.locked):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected is not None and not self.locked:
            time, self.selected = self.selected, None
            self.point_removed.emit(self.track_id, time)
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt
        if self.locked:
            return
        menu = self.build_menu(QPointF(event.pos()))
        menu.exec(event.globalPos())
        menu.deleteLater()

    def build_menu(self, position: QPointF) -> QMenu:
        """Menu du point sous ``position`` (supprimer, 0 dB) ou de la bande (ajouter ici, effacer la courbe)."""
        menu = QMenu(self)
        index = self.point_at(position)
        if index is not None:
            point = self.points[index]
            menu.addAction(translate("timeline.automation.remove_point")).triggered.connect(
                lambda _checked=False, t=point.time_seconds: self.point_removed.emit(self.track_id, t))
            unity = menu.addAction(translate("timeline.automation.reset_point"))
            unity.setEnabled(abs(point.gain_db) > 1e-9)
            unity.triggered.connect(lambda _checked=False, p=point: self.point_updated.emit(
                self.track_id, p.time_seconds, 0.0, p.fade_seconds))
        else:
            time = self._frame(self.time_of(position.x()))
            gain = self.gain_of(position.y())
            menu.addAction(translate("timeline.automation.add_point")).triggered.connect(
                lambda _checked=False: self.point_added.emit(self.track_id, time, gain, 0.0))
        menu.addSeparator()
        clear = menu.addAction(translate("timeline.automation.clear"))
        clear.setEnabled(bool(self.points))
        clear.triggered.connect(lambda _checked=False: self.cleared.emit(self.track_id))
        return menu


__all__ = ["LANE_HEIGHT", "AutomationLane"]
