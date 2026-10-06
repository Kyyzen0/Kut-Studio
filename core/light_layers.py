"""Calques de lumière procéduraux (light leak, flare anamorphique, lignes de vitesse, traînées, étincelles, flash).

Dessinés par le rastériseur partagé (:mod:`core.mograph_raster`) : le viewer, l'aperçu fidèle et l'export montrent les
mêmes pixels, et le moniteur GPU les compose avec le mode de fusion du calque (Addition par défaut). Chaque calque est
**déterministe** : son dessin ne dépend que de ses réglages, de sa graine (``light_seed``) et de son temps local
(``light_time``, posé par la scène à chaque image) ; deux rendus du même instant sont identiques au pixel près.

Technique reprise des plans néon de l'edit F1 (``footage.py``) : chaque primitive lumineuse est peinte en mode
additif sur l'image nette **et** sur une couche de halo huit fois plus petite, agrandie ensuite (bloom bon marché,
identique partout puisque c'est le même code Qt).
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QRadialGradient, QTransform

from .graphics import LIGHT_KINDS


STATIC_KINDS = frozenset({"flash"})
"""Sans mouvement propre : l'image ne dépend pas du temps (seule l'opacité du calque s'anime)."""

GLOW_DIVISOR = 8


def _color(value: str, alpha: float) -> QColor:
    from .mograph_raster import qcolor

    color = qcolor(value)
    color.setAlphaF(max(0.0, min(1.0, color.alphaF() * alpha)))
    return color


class _Lights:
    """Peint chaque primitive sur l'image nette et sur la couche de halo (réduite)."""

    def __init__(self, painter: QPainter, width: float, height: float) -> None:
        self.painter = painter
        self.width, self.height = width, height
        size = (max(2, int(width / GLOW_DIVISOR)), max(2, int(height / GLOW_DIVISOR)))
        self.glow = QImage(size[0], size[1], QImage.Format_ARGB32_Premultiplied)
        self.glow.fill(Qt.transparent)
        self.glow_painter = QPainter(self.glow)
        self.glow_painter.setRenderHint(QPainter.Antialiasing, True)
        self.glow_painter.setCompositionMode(QPainter.CompositionMode_Plus)
        self.glow_painter.scale(size[0] / width, size[1] / height)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)

    def streak(self, x1, y1, x2, y2, width: float, color: str, intensity: float, *, fade: bool = True) -> None:
        """Traînée : dégradé le long d'un trait à bouts ronds, tête blanche ; halo trois fois et demie plus large."""
        if intensity <= 0.0:
            return
        for target, scale, white in ((self.glow_painter, 3.5, False), (self.painter, 1.0, True)):
            gradient = QLinearGradient(QPointF(x1, y1), QPointF(x2, y2))
            gradient.setColorAt(0.0, _color(color, 0.0 if fade else intensity * 0.9))
            gradient.setColorAt(0.8, _color(color, 0.9 * intensity))
            gradient.setColorAt(1.0, _color("#FFFFFF" if white else color, intensity))
            pen = QPen(gradient, max(0.5, width * scale), Qt.SolidLine, Qt.RoundCap)
            target.setPen(pen)
            target.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def dot(self, x, y, radius: float, color: str, intensity: float, *, glow: float = 3.0) -> None:
        if intensity <= 0.0 or radius <= 0.0:
            return
        core = QRadialGradient(QPointF(x, y), radius)
        core.setColorAt(0.0, _color("#FFFFFF", intensity))
        core.setColorAt(0.35, _color(color, 0.86 * intensity))
        core.setColorAt(1.0, _color(color, 0.0))
        self.painter.setPen(Qt.NoPen)
        self.painter.setBrush(core)
        self.painter.drawEllipse(QPointF(x, y), radius, radius)
        halo = QRadialGradient(QPointF(x, y), radius * glow)
        halo.setColorAt(0.0, _color(color, intensity))
        halo.setColorAt(1.0, _color(color, 0.0))
        self.glow_painter.setPen(Qt.NoPen)
        self.glow_painter.setBrush(halo)
        self.glow_painter.drawEllipse(QPointF(x, y), radius * glow, radius * glow)

    def finish(self, bloom: float = 1.0) -> None:
        """Halo agrandi (lissé) ajouté à l'image nette."""
        self.glow_painter.end()
        painter = self.painter
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(painter.opacity() * min(1.0, bloom))
        painter.drawImage(QRectF(0, 0, self.width, self.height), self.glow)


def draw_light(painter: QPainter, graphic, width: float, height: float) -> None:
    """Dessine un calque de lumière dans sa boîte (espace calque), en mode additif."""
    kind = graphic.light_kind
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if kind == "flash":
        painter.fillRect(QRectF(0, 0, width, height), _color(graphic.fill_color, 1.0))
        painter.restore()
        return
    t = float(graphic.light_time) * float(graphic.light_speed)
    rng = random.Random(int(graphic.light_seed))
    if kind == "leak":
        _leak(painter, graphic, width, height, t, rng)
    else:
        lights = _Lights(painter, width, height)
        {"anamorphic_flare": _flare, "speed_lines": _speed_lines, "light_trails": _trails,
         "sparks": _sparks}[kind](lights, graphic, width, height, t, rng)
        lights.finish()
    painter.restore()


def _leak(painter: QPainter, graphic, width: float, height: float, t: float, rng: random.Random) -> None:
    """Light leak : trois nappes de lumière chaude qui dérivent lentement et respirent."""
    painter.setCompositionMode(QPainter.CompositionMode_Plus)
    painter.setPen(Qt.NoPen)
    span = max(width, height)
    for index in range(3):
        bx, by, phase = rng.uniform(-0.1, 1.1), rng.uniform(-0.1, 1.1), rng.uniform(0, 2 * math.pi)
        radius = span * rng.uniform(0.35, 0.7)
        x = (bx + 0.15 * math.sin(0.4 * t + phase)) * width
        y = (by + 0.12 * math.cos(0.33 * t + 1.7 * phase)) * height
        breath = 0.6 + 0.25 * math.sin(0.9 * t + 2.0 * phase) + 0.1 * math.sin(2.3 * t + phase)
        color = graphic.fill_color if index % 2 == 0 else graphic.glow_color
        gradient = QRadialGradient(QPointF(x, y), radius)
        gradient.setColorAt(0.0, _color(color, 0.78 * breath))
        gradient.setColorAt(0.45, _color(color, 0.3 * breath))
        gradient.setColorAt(1.0, _color(color, 0.0))
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(x, y), radius, radius)


def _flare(lights: _Lights, graphic, width: float, height: float, t: float, rng: random.Random) -> None:
    """Flare anamorphique : un trait horizontal fin et brûlé, un halo très étiré, un cœur et quelques reflets."""
    flicker = 0.85 + 0.1 * math.sin(13.0 * t + rng.uniform(0, 6)) + 0.05 * math.sin(29.0 * t)
    cx, cy = width / 2.0, height / 2.0
    painter = lights.painter
    painter.translate(cx, cy)
    painter.rotate(float(graphic.light_angle))
    painter.translate(-cx, -cy)
    lights.glow_painter.translate(cx, cy)
    lights.glow_painter.rotate(float(graphic.light_angle))
    lights.glow_painter.translate(-cx, -cy)
    halo = QRadialGradient(QPointF(0.0, 0.0), 1.0)
    halo.setColorAt(0.0, _color(graphic.fill_color, 0.55 * flicker))
    halo.setColorAt(1.0, _color(graphic.fill_color, 0.0))
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setTransform(QTransform().translate(cx, cy).scale(width * 0.48, height * 0.035), True)
    painter.setBrush(halo)
    painter.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
    painter.restore()
    thickness = max(1.0, height * 0.004)
    lights.streak(0.0, cy, cx, cy, thickness, graphic.fill_color, flicker, fade=True)
    lights.streak(width, cy, cx, cy, thickness, graphic.fill_color, flicker, fade=True)
    lights.dot(cx, cy, min(width, height) * 0.035, graphic.fill_color, flicker, glow=4.0)
    for _index in range(4):                               # reflets (ghosts) alignés sur l'axe optique
        offset = rng.uniform(-0.45, 0.45) * width
        lights.dot(cx + offset, cy, min(width, height) * rng.uniform(0.008, 0.02), graphic.glow_color, 0.35 * flicker)


def _speed_lines(lights: _Lights, graphic, width: float, height: float, t: float, rng: random.Random) -> None:
    """Lignes de vitesse : des traînées qui jaillissent du centre et s'allongent en approchant du bord (warp)."""
    cx, cy = width / 2.0, height / 2.0
    reach = math.hypot(width, height) * 0.6
    count = int(120 * max(0.1, float(graphic.light_density)))
    for index in range(count):
        angle, d0 = rng.uniform(0, 2 * math.pi), rng.random()
        color = graphic.fill_color if index % 3 else graphic.glow_color
        d = (d0 + 0.8 * t) % 1.0
        r1 = reach * (0.04 + d ** 2.2 * 1.1)
        r0 = r1 * (0.55 + 0.2 * (1.0 - d))
        dx, dy = math.cos(angle), math.sin(angle)
        lights.streak(cx + dx * r0, cy + dy * r0, cx + dx * r1, cy + dy * r1, (1.0 + 7.0 * d) * height / 1920.0,
                      color, min(1.0, 0.25 + d))


def _trails(lights: _Lights, graphic, width: float, height: float, t: float, rng: random.Random) -> None:
    """Traînées de lumière : des feux qui filent dans la direction ``light_angle``, chacun sur son couloir."""
    angle = math.radians(float(graphic.light_angle))
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy, dx
    length = abs(width * dx) + abs(height * dy)
    cx, cy = width / 2.0, height / 2.0
    count = max(1, int(6 * max(0.1, float(graphic.light_density))))
    for index in range(count):
        lane = (index - (count - 1) / 2.0) / max(1, count) * (abs(width * nx) + abs(height * ny)) * 0.8
        lane += rng.uniform(-0.04, 0.04) * height
        tail = length * rng.uniform(0.25, 0.45)
        head = ((0.5 * t + index * 0.37 + rng.random()) % 1.0) * (length + tail) - length / 2.0
        hx, hy = cx + nx * lane + dx * head, cy + ny * lane + dy * head
        color = graphic.fill_color if index % 2 == 0 else graphic.glow_color
        lights.streak(hx - dx * tail, hy - dy * tail, hx, hy, max(1.0, height * 0.006), color, 0.9)


def _sparks(lights: _Lights, graphic, width: float, height: float, t: float, rng: random.Random) -> None:
    """Étincelles : des gerbes qui jaillissent du bas du calque, retombent et changent de couleur en refroidissant."""
    rate = 400.0 * max(0.1, float(graphic.light_density))             # naissances par seconde (comme les plans F1)
    seed = int(graphic.light_seed)
    origin = (width * 0.5, height * 0.85)
    spray = math.radians(float(graphic.light_angle) - 90.0)
    scale = height / 1920.0
    first = int(math.floor(max(0.0, t - 0.9) * rate))
    for birth in range(first, int(math.floor(t * rate)) + 1):
        local = random.Random(seed * 1_000_003 + birth)
        if math.sin(9 * birth / rate) + math.sin(23 * birth / rate) < -0.3:
            continue                                        # gerbes : des trous dans l'émission
        age = t - birth / rate
        life = local.uniform(0.35, 0.9)
        if age < 0.0 or age > life:
            continue
        direction = spray + local.uniform(-0.6, 0.6)
        speed = local.uniform(700.0, 1900.0) * scale
        vx, vy = math.cos(direction) * speed, math.sin(direction) * speed
        x = origin[0] + vx * age
        y = origin[1] + vy * age + 0.5 * 2600.0 * scale * age * age
        vy_now = vy + 2600.0 * scale * age
        ratio = age / life
        color = "#FFFFFF" if ratio < 0.2 else (graphic.glow_color if ratio < 0.6 else graphic.fill_color)
        lights.streak(x - vx * 0.03, y - vy_now * 0.03, x, y, max(1.5, 5.0 * scale), color, 1.0 - ratio, fade=True)
        lights.dot(x, y, max(1.0, 4.0 * scale), color, 1.0 - ratio, glow=4.0)


__all__ = ["LIGHT_KINDS", "STATIC_KINDS", "draw_light"]
