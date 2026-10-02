"""Décor et interactions propres aux clips imbriqués (séquence dans une séquence).

Séparé de :mod:`ui.timeline_widgets.clip_widget` pour garder ce dernier
petit. Fonctions sans état, appelées par le widget de clip :

- :func:`paint_nested_decoration` dessine le badge « séquence » (deux cadres
  superposés), la zone hachurée au-delà de la fin de la séquence source et
  l'état hors ligne / circulaire ;
- :func:`handle_nested_double_click` ouvre la séquence d'un double-clic.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen

from ui import i18n

_BADGE = 14


def nested_status_text(status: str) -> str:
    """Info-bulle de l'état d'un clip imbriqué."""
    if status == "missing":
        return i18n.translate("sequence.status.missing")
    if status == "cycle":
        return i18n.translate("sequence.status.cycle")
    if status == "overflow":
        return i18n.translate("sequence.status.overflow")
    return i18n.translate("sequence.status.ok")


def paint_nested_decoration(widget) -> None:
    """Badge, débordement et état d'un clip imbriqué (aucun effet sinon)."""
    view = widget.view
    if not getattr(view, "sequence_id", ""):
        return
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing, True)
    status = getattr(view, "nested_status", "")
    overflow_start = getattr(view, "nested_overflow_start", None)
    parent = widget.parent_timeline
    if overflow_start is not None and parent is not None:
        scale = parent.pixels_per_second * parent.zoom
        x = max(0.0, (overflow_start - view.start) * scale)
        if x < widget.width():
            area = QRectF(x, 0, widget.width() - x, widget.height())
            painter.fillRect(area, QBrush(QColor(0, 0, 0, 110), Qt.BDiagPattern))
            painter.fillRect(area, QColor(0, 0, 0, 60))
    if status in {"missing", "cycle"}:
        painter.fillRect(
            QRectF(0, 0, widget.width(), widget.height()),
            QBrush(QColor(255, 255, 255, 60), Qt.DiagCrossPattern),
        )
    # Badge : deux cadres décalés, en haut à droite.
    right = widget.width() - _BADGE - 6
    if right > 40:
        pen = QPen(QColor(255, 255, 255, 230))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(right + 3, 5, _BADGE - 4, _BADGE - 5), 2, 2)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawRoundedRect(QRectF(right, 8, _BADGE - 4, _BADGE - 5), 2, 2)
        if status in {"missing", "cycle", "overflow"}:
            painter.setPen(QPen(QColor("#FFD166"), 2))
            painter.drawText(QRectF(right - 12, 4, 10, 14), Qt.AlignCenter, "!")
    painter.end()
    widget.setToolTip(f"{view.label} — {nested_status_text(status)}")


def handle_nested_double_click(widget, event) -> bool:
    """Ouvre la séquence d'un clip imbriqué ; ``True`` si l'événement est traité."""
    view = widget.view
    parent = widget.parent_timeline
    if not getattr(view, "sequence_id", "") or parent is None:
        return False
    if event.button() != Qt.LeftButton:
        return False
    parent.nested_open_requested.emit(view.id)
    event.accept()
    return True


__all__ = ["handle_nested_double_click", "nested_status_text", "paint_nested_decoration"]
