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

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from core.color_grading import WHEELS, Wheel
from core.color_wheels import puck_from_wheel, wheel_from_puck
from core.grade_keyframing import GradeAnimationInfo
from ui.color_page.quick_settings import QuickSettings
from ui.color_page.wheel_keys import WheelCell
from ui.design_system import Spacing, Typography, Weights
from ui.i18n import translate
from ui.theme import COLORS

HUE_SCREEN_ANGLE = 103.0
"""Angle à l'écran (degrés, sens trigonométrique, 0 à droite) de la teinte 0 (rouge), comme sur un vectorscope."""

FINE_FACTOR = 0.25
_TITLE_HEIGHT = 20
_DIAL_HEIGHT = 14
_READOUT_LINE = Typography.caption + 4
_READOUT_HEIGHT = 2 * _READOUT_LINE                       # deux lignes : « R V » puis « B Y » (jamais coupées, même à 120 px)
_RING_WIDTH = 5.0
_MIN_DISC = 120                                           # une roue plus petite n'est plus réglable à la souris (barème « Air d'abord »)
_MAX_DISC = 150
_QUICK_SLACK = 8                                          # le disque garde 8 px de plus que ``_MIN_DISC`` quand les réglages rapides sont posés
_SIDE = 2                                                 # air autour du disque : à 1222 px la colonne n'a que 514 px pour quatre roues


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
        chrome = _TITLE_HEIGHT + _DIAL_HEIGHT + _READOUT_HEIGHT + Spacing.xs
        self.setMinimumSize(_MIN_DISC, _MIN_DISC + chrome)
        self.setMaximumHeight(_MAX_DISC + chrome)            # jamais plus haute que nécessaire : le reste de l'onglet reste vide
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.CrossCursor)
        self.retranslate()

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        """Le disque entier au diamètre maximal : sans cela la layout ne lui donnerait que son minimum."""
        return QSize(_MAX_DISC + 2 * _SIDE, _MAX_DISC + _TITLE_HEIGHT + _DIAL_HEIGHT + _READOUT_HEIGHT + Spacing.xs)

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
        radius = max(8.0, min(self.width() - 2 * _SIDE, height, _MAX_DISC) / 2.0)
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
        parts = text.split("  ")                                     # « R +0.05 », « V +0.00 »… (le libellé traduit les lettres)
        lines = ["  ".join(parts[:2]), "  ".join(parts[2:])] if len(parts) > 2 else [text, ""]
        for index, line in enumerate(lines):
            painter.drawText(QRectF(0, dial.bottom() + 2 + index * _READOUT_LINE, self.width(), _READOUT_LINE),
                             Qt.AlignCenter, line)

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
    keys_toggled = Signal(str)                 # losange d'une roue (ADR-0008, étape 8)
    keys_navigated = Signal(str, int)
    graph_requested = Signal(str)
    animation_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_wheels")
        self.wheels: dict[str, ColorWheel] = {}
        self.cells: dict[str, WheelCell] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(_SIDE, Spacing.sm, _SIDE, Spacing.sm)
        self._grid.setSpacing(0)
        self._grid.setSizeConstraint(QGridLayout.SetNoConstraint)        # le minimum est celui de ``minimumSizeHint`` (deux roues)
        for name in WHEELS:
            wheel = ColorWheel(name)
            wheel.changed.connect(lambda value, wheel_name=name: self.wheel_changed.emit(wheel_name, value))
            self.wheels[name] = wheel
            cell = WheelCell(wheel)                   # la roue et son losange, construits ensemble
            cell.keys_toggled.connect(self.keys_toggled)
            cell.navigated.connect(self.keys_navigated)
            cell.graph_requested.connect(self.graph_requested)
            cell.cleared.connect(self.animation_cleared)
            self.cells[name] = cell
        self.quick = QuickSettings()                  # sous les roues : mêmes réglages que l'inspecteur (voir ``quick_settings``)
        self._columns = 0
        self._quick_at: tuple[int, int] | None = None
        self._arrange(4)
        self.quick.hide()

    def set_wheels(self, wheels: dict[str, Wheel]) -> None:
        for name, widget in self.wheels.items():
            widget.set_wheel(wheels.get(name, Wheel()))

    def set_animation(self, info: GradeAnimationInfo | None) -> None:
        """Losanges des roues et valeurs évaluées à la tête de lecture (``None`` : pas de clip, rien d'animé)."""
        animatable = info is None or info.animatable
        for name, cell in self.cells.items():
            cell.set_state(info.wheels.get(name) if info is not None else None, animatable=animatable)
        if info is not None and info.shown:
            self.set_wheels({**{name: widget.wheel for name, widget in self.wheels.items()}, **info.shown})
        self.quick.set_animation(info)

    def retranslate(self) -> None:
        for wheel in self.wheels.values():
            wheel.retranslate()
        for cell in self.cells.values():
            cell.retranslate()
        self.quick.retranslate()
        self._fit_quick()

    def _quick_height(self, hint: QSize) -> int:
        """Hauteur de ``hint`` sans la place des réglages rapides : ils ne comptent ni dans le minimum de l'onglet (il défilerait) ni
        dans la taille souhaitée (le gestionnaire de panneaux partage la hauteur d'après elle) ; ils se posent dans ce qui reste."""
        return hint.height() - (self.quick.sizeHint().height() if self.quick.isVisibleTo(self) else 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = super().sizeHint()
        return QSize(hint.width(), self._quick_height(hint))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        """Largeur de deux roues seulement : la page prend la largeur de l'onglet et choisit 4 ou 2 colonnes (sans cela, les
        quatre colonnes imposaient leur largeur et restaient, quitte à couper la dernière roue)."""
        hint = super().minimumSizeHint()
        floor = self.quick.minimumSizeHint().height() if self.quick.isVisibleTo(self) else 0
        return QSize(2 * _MIN_DISC, hint.height() - floor)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        # Quatre roues côte à côte si chacune garde un disque entier d'au moins ``_MIN_DISC`` px, sinon 2 × 2.
        margins = self._grid.contentsMargins()
        room = (self.width() - margins.left() - margins.right() - 3 * self._grid.horizontalSpacing()) / 4
        self._arrange(4 if room >= _MIN_DISC else 2)       # (une barre de défilement qui apparaît ôte ~8 px : pas de va-et-vient)
        self._fit_quick()

    def _fit_quick(self) -> None:
        """Pose les réglages rapides sous les roues s'il reste la place : une ligne chacun, sinon deux par ligne (sans curseur si la
        largeur manque), sinon rien.

        Les roues passent d'abord : on compte leur minimum (disque de ``_MIN_DISC`` + ``_QUICK_SLACK`` px) et 8 px de marge ; si les
        lignes ne tiennent pas dans le reste, elles se cachent plutôt que d'écraser une roue ou de faire défiler l'onglet.
        Ne dépend que de la taille de l'onglet, jamais de ce qu'il vient de cacher : pas de va-et-vient."""
        margins = self._grid.contentsMargins()
        rows = -(-len(self.cells) // max(1, self._columns))
        cell = max(widget.minimumSizeHint().height() for widget in self.cells.values()) + _QUICK_SLACK
        room_height = self.height() - margins.top() - margins.bottom() - rows * cell - Spacing.sm
        room_width = self.width() - margins.left() - margins.right()
        for columns, sliders in ((1, True), (2, True), (2, False)):
            width, height = self.quick.required(columns, sliders=sliders)
            if height <= room_height and width <= room_width:
                self.quick.arrange(columns, sliders=sliders)
                self._place_quick()
                self.quick.show()
                return
        self.quick.hide()

    def _arrange(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for cell in self.cells.values():
            self._grid.removeWidget(cell)
        for index, cell in enumerate(self.cells.values()):
            self._grid.addWidget(cell, index // columns, index % columns)
        self._place_quick()

    def _place_quick(self) -> None:
        """Réglages rapides : une rangée sous les roues, sur toute la largeur ; l'espace en trop reste sous eux."""
        rows = -(-len(self.cells) // self._columns)
        if self._quick_at != (rows, self._columns):
            self._quick_at = (rows, self._columns)
            self._grid.removeWidget(self.quick)
            self._grid.addWidget(self.quick, rows, 0, 1, self._columns)
        for row in range(4):
            self._grid.setRowStretch(row, 1 if row == rows + 1 else 0)
