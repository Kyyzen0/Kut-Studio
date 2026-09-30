"""Rasterisation portable des titres graphiques.

Certaines builds FFmpeg (notamment Homebrew minimal) n'exposent pas
``drawtext``. Kut-Studio génère donc un PNG RGBA déterministe avec Qt, puis
le compose via ``overlay``. Le cache est hors du projet ``.kut`` et peut être
recréé à tout moment.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def _qcolor(value: str):
    from PySide6.QtGui import QColor

    text = str(value or "#FFFFFF")
    if text.startswith("#") and len(text) == 9:  # RRGGBBAA -> AARRGGBB
        text = f"#{text[7:9]}{text[1:7]}"
    return QColor(text)


def rasterize_text_graphic(graphic) -> str:
    """Retourne le PNG RGBA en cache correspondant exactement au titre."""
    payload = repr(graphic).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    configured = os.environ.get("KUT_STUDIO_CACHE_DIR")
    root = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "kut-studio-cache"
    directory = root / "graphics"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"text-{digest}.png"
    if target.is_file() and target.stat().st_size > 0:
        return str(target)

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QPen

    image = QImage(
        int(graphic.width), int(graphic.height), QImage.Format_ARGB32_Premultiplied
    )
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    font = QFont(str(graphic.font_family or "Sans Serif"), int(graphic.font_size))
    painter.setFont(font)
    rect = QRectF(0, 0, image.width(), image.height())
    flags = Qt.AlignCenter | Qt.TextWordWrap

    if graphic.shadow_color and (graphic.shadow_offset_x or graphic.shadow_offset_y):
        painter.setPen(_qcolor(graphic.shadow_color))
        painter.drawText(
            rect.translated(graphic.shadow_offset_x, graphic.shadow_offset_y),
            flags,
            graphic.text,
        )

    if "\n" not in graphic.text:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(graphic.text)
        baseline = (image.height() + metrics.ascent() - metrics.descent()) / 2
        path = QPainterPath()
        path.addText((image.width() - width) / 2, baseline, font, graphic.text)
        if graphic.stroke_width > 0:
            painter.setPen(
                QPen(
                    _qcolor(graphic.stroke_color),
                    max(1, int(graphic.stroke_width) * 2),
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            painter.setBrush(_qcolor(graphic.fill_color))
            painter.drawPath(path)
        else:
            painter.fillPath(path, _qcolor(graphic.fill_color))
    else:
        painter.setPen(_qcolor(graphic.fill_color))
        painter.drawText(rect, flags, graphic.text)
    painter.end()
    if not image.save(str(target), "PNG"):
        raise OSError(f"Impossible de rasteriser le titre graphique : {target}")
    return str(target)


__all__ = ["rasterize_text_graphic"]
