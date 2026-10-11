"""Vumètre : crête et RMS d'un signal réel, en dBFS (mixage en direct, :mod:`core.live_mix`).

Une barre de crête (verte, jaune au-dessus de :data:`CAUTION_DB`, rouge au-dessus de :data:`DANGER_DB`), le RMS plus
sombre à l'intérieur, et un trait de crête maintenue (:data:`HOLD_SECONDS`, puis il retombe). Sans mesure (lecture
arrêtée), tout retombe à :data:`FALL_DB_PER_SECOND` : un vumètre ne saute jamais à vide.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.theme import COLORS

FLOOR_DB = -60.0
CAUTION_DB = -18.0
DANGER_DB = -6.0
HOLD_SECONDS = 1.5
FALL_DB_PER_SECOND = 24.0


def _fraction(db: float) -> float:
    return max(0.0, min(1.0, (float(db) - FLOOR_DB) / -FLOOR_DB))


class LevelMeter(QWidget):
    """Barre de niveau verticale (tranche de piste) ou horizontale (Master)."""

    def __init__(self, orientation=Qt.Vertical, parent: QWidget | None = None, *, clock=time.monotonic) -> None:
        super().__init__(parent)
        self.orientation = orientation
        self._clock = clock
        self.peak_db = FLOOR_DB
        self.rms_db = FLOOR_DB
        self.hold_db = FLOOR_DB
        self._hold_since = 0.0
        self._last = clock()
        if orientation == Qt.Vertical:
            self.setFixedWidth(8)
            self.setMinimumHeight(72)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            self.setFixedHeight(8)
            self.setMinimumWidth(96)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_levels(self, peak_db: float | None, rms_db: float | None = None) -> None:
        """Nouvelle mesure ; ``None`` : aucune (le niveau retombe)."""
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        fallen = self.peak_db - FALL_DB_PER_SECOND * elapsed
        if peak_db is None:
            self.peak_db = max(FLOOR_DB, fallen)
            self.rms_db = max(FLOOR_DB, self.rms_db - FALL_DB_PER_SECOND * elapsed)
        else:
            self.peak_db = max(FLOOR_DB, float(peak_db))
            self.rms_db = max(FLOOR_DB, float(rms_db if rms_db is not None else peak_db))
        if self.peak_db >= self.hold_db:
            self.hold_db, self._hold_since = self.peak_db, now
        elif now - self._hold_since > HOLD_SECONDS:
            self.hold_db = max(self.peak_db, self.hold_db - FALL_DB_PER_SECOND * elapsed)
        self.update()

    def _color(self, db: float) -> QColor:
        key = "danger" if db >= DANGER_DB else "warning" if db >= CAUTION_DB else "success"
        return QColor(COLORS[key])

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(COLORS["border"]))
        vertical = self.orientation == Qt.Vertical

        def span(db: float) -> QRectF:
            fraction = _fraction(db)
            if vertical:
                height = rect.height() * fraction
                return QRectF(rect.left(), rect.bottom() - height, rect.width(), height)
            return QRectF(rect.left(), rect.top(), rect.width() * fraction, rect.height())

        if self.peak_db > FLOOR_DB:
            painter.fillRect(span(self.peak_db), self._color(self.peak_db))
            painter.fillRect(span(self.rms_db), self._color(self.rms_db).darker(140))
        if self.hold_db > FLOOR_DB:
            painter.setPen(self._color(self.hold_db))
            fraction = _fraction(self.hold_db)
            if vertical:
                y = rect.bottom() - rect.height() * fraction
                painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            else:
                x = rect.left() + rect.width() * fraction
                painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
        painter.end()


__all__ = ["CAUTION_DB", "DANGER_DB", "FLOOR_DB", "LevelMeter"]
