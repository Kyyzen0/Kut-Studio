"""Roues lift / gamma / gain / offset : un palet pour la couleur, une molette pour le niveau.

Chaque roue est peinte (aucune image) : un disque, l'anneau des teintes, le palet. L'anneau suit le vectorscope des
scopes, rouge à 103° : pousser le palet vers une teinte de l'anneau pousse l'image vers elle, et la trace du
vectorscope part du même côté. Le palet se déplace **en relatif** (cliquer ne le fait pas sauter ; Maj : réglage fin)
et ne touche que la couleur (décalages de moyenne nulle, :func:`core.color_wheels.wheel_from_puck`) ; la molette
sous la roue règle le maître (les trois canaux ensemble). Double-clic : remise à zéro du palet ou de la molette.

:attr:`ColorWheel.changed` est émis à chaque mouvement : la fenêtre regroupe la rafale en une étape d'historique,
comme pour les curseurs de l'inspecteur.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from core.color_grading import WHEELS, Wheel
from core.color_wheels import puck_from_wheel, wheel_from_puck
from ui.design_system import Spacing, Typography, Weights
from ui.i18n import translate
from ui.theme import COLORS

HUE_SCREEN_ANGLE = 103.0
"""Angle à l'écran (degrés, sens trigonométrique, 0 à droite) de la teinte 0 (rouge), comme sur un vectorscope."""

FINE_FACTOR = 0.25
_TITLE_HEIGHT = 18
_DIAL_HEIGHT = 14
_READOUT_HEIGHT = 16
_RING_WIDTH = 5.0
_MIN_DISC = 72


class ColorWheel(QWidget):
    """Une roue : :attr:`changed` (:class:`~core.color_grading.Wheel`) à chaque mouvement du palet ou de la molette."""

    changed = Signal(object)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.setObjectName(f"color_wheel_{name}")
        self._wheel = Wheel()
        self._puck_drag: tuple[QPointF, float, float] | None = None
        self._dial_drag: tuple[float, float] | None = None
        self.setMinimumSize(_MIN_DISC + 2 * Spacing.sm, _MIN_DISC + _TITLE_HEIGHT + _DIAL_HEIGHT + _READOUT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CrossCursor)
        self.retranslate()

    # -- état ---------------------------------------------------------------------------------------------------

    @property
    def wheel(self) -> Wheel:
        return self._wheel

    def set_wheel(self, wheel: Wheel) -> None:
        """Affiche ``wheel`` sans émettre (le modèle a changé ailleurs)."""
        if self._puck_drag is None and self._dial_drag is None:
            self._wheel = wheel
            self.update()

    def retranslate(self) -> None:
        self.title = translate(f"color.wheel.{self.name}")
        self.setToolTip(translate(f"color.wheel.{self.name}.tip"))
        self.setAccessibleName(self.title)
        self.update()

    # -- géométrie ----------------------------------------------------------------------------------------------

    def _disc(self) -> tuple[QPointF, float]:
        """Centre et rayon du disque (anneau compris)."""
        height = self.height() - _TITLE_HEIGHT - _DIAL_HEIGHT - _READOUT_HEIGHT - Spacing.xs
        radius = max(8.0, min(self.width() - 2 * Spacing.sm, height) / 2.0)
        return QPointF(self.width() / 2.0, _TITLE_HEIGHT + Spacing.xs / 2 + height / 2.0), radius

    def _travel(self) -> float:
        """Rayon parcouru par le palet (jusqu'à l'intérieur de l'anneau)."""
        return max(4.0, self._disc()[1] - _RING_WIDTH - 4.0)

    def _dial_rect(self) -> QRectF:
        center, radius = self._disc()
        top = center.y() + radius + Spacing.xs
        width = max(24.0, 2.0 * radius)
        return QRectF(center.x() - width / 2.0, top, width, _DIAL_HEIGHT - 4)

    def _puck_unit(self) -> tuple[float, float]:
        """Position du palet dans le disque unité (y vers le haut)."""
        hue, radius = puck_from_wheel(self._wheel)
        angle = math.radians(hue + HUE_SCREEN_ANGLE)
        return radius * math.cos(angle), radius * math.sin(angle)

    def _with_puck(self, x: float, y: float) -> Wheel:
        radius = min(1.0, math.hypot(x, y))
        hue = math.degrees(math.atan2(y, x)) - HUE_SCREEN_ANGLE
        return wheel_from_puck(hue, radius, master=self._wheel.y)

    # -- peinture -----------------------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        center, radius = self._disc()

        title_font = QFont(self.font())
        title_font.setPixelSize(Typography.small)
        title_font.setWeight(QFont.Weight(Weights.semibold))
        painter.setFont(title_font)
        painter.setPen(QColor(COLORS["text"] if enabled else COLORS["disabled_text"]))
        painter.drawText(QRectF(0, 0, self.width(), _TITLE_HEIGHT), Qt.AlignCenter, self.title)

        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.setBrush(QColor(COLORS["panel_alt"]))
        painter.drawEllipse(center, radius, radius)
        ring = QConicalGradient(center, HUE_SCREEN_ANGLE)
        for step in range(13):                                   # teinte 0 → 360° dans le sens trigonométrique
            hue = (step / 12.0) % 1.0
            ring.setColorAt(step / 12.0, QColor.fromHsvF(hue, 0.7 if enabled else 0.15, 0.95 if enabled else 0.6))
        painter.setPen(QPen(ring, _RING_WIDTH))
        painter.setBrush(Qt.NoBrush)
        inner = radius - _RING_WIDTH / 2.0
        painter.drawEllipse(center, inner, inner)

        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        travel = self._travel()
        painter.drawLine(QPointF(center.x() - travel, center.y()), QPointF(center.x() + travel, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - travel), QPointF(center.x(), center.y() + travel))

        x, y = self._puck_unit()
        puck = QPointF(center.x() + x * travel, center.y() - y * travel)
        moved = not (x == 0.0 and y == 0.0)
        if moved:
            painter.setPen(QPen(QColor(COLORS["muted_strong"]), 1.5))
            painter.drawLine(center, puck)
        hue = (math.degrees(math.atan2(y, x)) - HUE_SCREEN_ANGLE) % 360.0
        fill = QColor.fromHsvF(hue / 360.0, 0.75, 1.0) if moved and enabled else QColor(COLORS["text"])
        painter.setPen(QPen(QColor(COLORS["text_strong"] if enabled else COLORS["disabled_text"]), 1.5))
        painter.setBrush(fill)
        painter.drawEllipse(puck, 5.5, 5.5)

        dial = self._dial_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["input_bg"]))
        painter.drawRoundedRect(dial, dial.height() / 2, dial.height() / 2)
        middle = dial.center().x()
        level = middle + self._wheel.y * dial.width() / 2.0
        if self._wheel.y:
            painter.setBrush(QColor(COLORS["accent"] if enabled else COLORS["muted"]))
            painter.drawRoundedRect(QRectF(min(middle, level), dial.top(), abs(level - middle), dial.height()), 2, 2)
        painter.setPen(QPen(QColor(COLORS["muted_strong"]), 1))
        painter.drawLine(QPointF(middle, dial.top() - 2), QPointF(middle, dial.bottom() + 2))

        readout_font = QFont(self.font())
        readout_font.setPixelSize(Typography.caption)
        painter.setFont(readout_font)
        painter.setPen(QColor(COLORS["muted"]))
        wheel = self._wheel
        text = translate("color.wheel.readout", r=f"{wheel.r:+.2f}", g=f"{wheel.g:+.2f}", b=f"{wheel.b:+.2f}",
                         y=f"{wheel.y:+.2f}")
        painter.drawText(QRectF(0, dial.bottom() + 2, self.width(), _READOUT_HEIGHT), Qt.AlignCenter, text)

    # -- souris -------------------------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        pos = event.position()
        if self._dial_hit(pos):
            self._dial_drag = (pos.x(), self._wheel.y)
            return
        center, radius = self._disc()
        if math.hypot(pos.x() - center.x(), pos.y() - center.y()) <= radius:
            self._puck_drag = (pos, *self._puck_unit())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        pos = event.position()
        fine = FINE_FACTOR if event.modifiers() & Qt.ShiftModifier else 1.0
        if self._puck_drag is not None:
            start, x0, y0 = self._puck_drag
            travel = self._travel()
            x = x0 + (pos.x() - start.x()) / travel * fine
            y = y0 - (pos.y() - start.y()) / travel * fine
            length = math.hypot(x, y)
            if length > 1.0:
                x, y = x / length, y / length
            self._emit(self._with_puck(x, y))
        elif self._dial_drag is not None:
            start_x, start_level = self._dial_drag
            half = max(1.0, self._dial_rect().width() / 2.0)
            level = max(-1.0, min(1.0, start_level + (pos.x() - start_x) / half * fine))
            self._emit(Wheel(self._wheel.r, self._wheel.g, self._wheel.b, level))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.LeftButton:
            self._puck_drag = None
            self._dial_drag = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        self._puck_drag = None
        self._dial_drag = None
        wheel = self._wheel
        if self._dial_hit(event.position()):
            self._emit(Wheel(wheel.r, wheel.g, wheel.b, 0.0))
        else:
            self._emit(Wheel(y=wheel.y))

    def _dial_hit(self, pos: QPointF) -> bool:
        return self._dial_rect().adjusted(-4, -4, 4, 4).contains(pos)

    def _emit(self, wheel: Wheel) -> None:
        if wheel == self._wheel:
            return
        self._wheel = wheel
        self.update()
        self.changed.emit(wheel)


class ColorWheels(QWidget):
    """Les quatre roues, sur une ligne si la place le permet, sinon en deux rangées ;
    :attr:`wheel_changed` (nom de la roue, :class:`~core.color_grading.Wheel`)."""

    wheel_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_wheels")
        self.wheels: dict[str, ColorWheel] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        self._grid.setSpacing(Spacing.sm)
        for name in WHEELS:
            wheel = ColorWheel(name)
            wheel.changed.connect(lambda value, wheel_name=name: self.wheel_changed.emit(wheel_name, value))
            self.wheels[name] = wheel
        self._columns = 0
        self._arrange(4)

    def set_wheels(self, wheels: dict[str, Wheel]) -> None:
        for name, widget in self.wheels.items():
            widget.set_wheel(wheels.get(name, Wheel()))

    def retranslate(self) -> None:
        for wheel in self.wheels.values():
            wheel.retranslate()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # Quatre roues côte à côte si chacune garde un disque lisible (et pas plus large que haute), sinon 2 × 2.
        row_fits = self.width() >= 4 * (_MIN_DISC + 3 * Spacing.sm) and self.width() / 4 >= self.height() * 0.55
        self._arrange(4 if row_fits else 2)

    def _arrange(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for wheel in self.wheels.values():
            self._grid.removeWidget(wheel)
        for index, wheel in enumerate(self.wheels.values()):
            self._grid.addWidget(wheel, index // columns, index % columns)
