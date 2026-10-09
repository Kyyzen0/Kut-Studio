"""Bande des plans (page Couleur) : une vignette par clip vidéo de la séquence, dans l'ordre du montage.

Comme la bande de vignettes de DaVinci Resolve : on passe d'un plan à l'autre d'un clic (le clip est sélectionné et
la tête de lecture va à son début, comme un clic dans la timeline). Chaque vignette porte le numéro du plan, son nom
et une pastille quand il est étalonné (avec le nombre de nœuds s'il en a plusieurs) ; le plan affiché est encadré.

Les vignettes sont celles de la timeline (même cache, même tâche de fond, image du milieu du clip) : la bande les
demande à son ``thumbnail_provider`` et se repeint quand elles arrivent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ui.design_system import Radius, Spacing, Typography, Weights
from ui.i18n import translate
from ui.theme import COLORS

THUMB_WIDTH = 128
THUMB_HEIGHT = 72
CAPTION_HEIGHT = 30


@dataclass(frozen=True)
class StripClip:
    """Un plan de la bande."""

    id: str
    label: str
    graded: bool = False
    nodes: int = 1


class _Tiles(QWidget):
    """Les vignettes, côte à côte (peintes : aucune image chargée tant qu'elles ne sont pas en cache)."""

    def __init__(self, strip: "ClipStrip") -> None:
        super().__init__()
        self.strip = strip
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        count = len(self.strip.clips)
        return QSize(Spacing.sm + count * (THUMB_WIDTH + Spacing.sm), Spacing.sm + THUMB_HEIGHT + CAPTION_HEIGHT)

    def tile_rect(self, index: int) -> QRectF:
        return QRectF(Spacing.sm + index * (THUMB_WIDTH + Spacing.sm), Spacing.sm, THUMB_WIDTH,
                      THUMB_HEIGHT + CAPTION_HEIGHT - Spacing.xs)

    def index_at(self, x: float, y: float) -> int | None:
        for index in range(len(self.strip.clips)):
            if self.tile_rect(index).contains(x, y):
                return index
        return None

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        caption_font = QFont(self.font())
        caption_font.setPixelSize(Typography.caption)
        number_font = QFont(caption_font)
        number_font.setWeight(QFont.Weight(Weights.bold))
        metrics = QFontMetricsF(caption_font)
        exposed = event.rect()
        for index, clip in enumerate(self.strip.clips):
            tile = self.tile_rect(index)
            if not tile.intersects(QRectF(exposed)):
                continue
            current = clip.id == self.strip.current_id
            thumb = QRectF(tile.left(), tile.top(), THUMB_WIDTH, THUMB_HEIGHT)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["clip_video_fill"]))
            painter.drawRoundedRect(thumb, Radius.sm, Radius.sm)
            pixmap = self.strip.thumbnail(clip.id)
            if pixmap is not None and not pixmap.isNull():
                painter.drawPixmap(thumb.toRect(), pixmap)
            if clip.graded:
                painter.setBrush(QColor(COLORS["accent"]))
                painter.setPen(QPen(QColor(COLORS["panel"]), 1))
                painter.drawEllipse(thumb.topRight() + QPointF(-9, 9), 4.5, 4.5)
            painter.setFont(number_font)
            painter.setPen(QColor(COLORS["text_strong"] if current else COLORS["muted_strong"]))
            caption = QRectF(tile.left() + 2, thumb.bottom() + 2, THUMB_WIDTH - 4, CAPTION_HEIGHT - 8)
            number = f"{index + 1:02d}"
            painter.drawText(caption, Qt.AlignLeft | Qt.AlignTop, number)
            if clip.nodes > 1:
                painter.drawText(caption, Qt.AlignRight | Qt.AlignTop,
                                 translate("color.strip.nodes", count=clip.nodes))
            painter.setFont(caption_font)
            painter.setPen(QColor(COLORS["text"] if current else COLORS["muted"]))
            name = metrics.elidedText(clip.label, Qt.ElideRight, caption.width())
            painter.drawText(caption, Qt.AlignLeft | Qt.AlignBottom, name)
            border = QPen(QColor(COLORS["accent"] if current else COLORS["border"]), 2.0 if current else 1.0)
            painter.setPen(border)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), Radius.sm, Radius.sm)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() != Qt.LeftButton:
            return
        index = self.index_at(event.position().x(), event.position().y())
        if index is not None:
            self.strip.clip_requested.emit(self.strip.clips[index].id)


class ClipStrip(QWidget):
    """La bande des plans ; :attr:`clip_requested` (identifiant) au clic sur une vignette."""

    clip_requested = Signal(str)
    shown = Signal()                                    # affichée : la fenêtre la remet à jour

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("clip_strip")
        self.clips: list[StripClip] = []
        self.current_id: str | None = None
        self.thumbnail_provider: Callable[[str], QPixmap | None] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tiles = _Tiles(self)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.tiles)
        self.scroll.setWidgetResizable(False)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll)
        self.setMinimumHeight(Spacing.sm + THUMB_HEIGHT + CAPTION_HEIGHT + 14)
        self.setAccessibleName(translate("workspace.panel.clips"))

    def set_clips(self, clips: list[StripClip], current_id: str | None) -> None:
        """Les plans de la séquence et le plan affiché ; la bande défile pour le garder visible."""
        self.clips = list(clips)
        self.current_id = current_id
        self.tiles.resize(self.tiles.sizeHint())
        self.tiles.update()
        index = next((position for position, clip in enumerate(self.clips) if clip.id == current_id), None)
        if index is not None:
            rect = self.tiles.tile_rect(index)
            self.scroll.ensureVisible(int(rect.center().x()), int(rect.center().y()), int(rect.width()), 0)

    def thumbnail(self, clip_id: str) -> QPixmap | None:
        return self.thumbnail_provider(clip_id) if self.thumbnail_provider is not None else None

    def refresh_thumbnails(self) -> None:
        """Des vignettes sont arrivées dans le cache : on repeint."""
        if self.isVisible():
            self.tiles.update()

    def retranslate(self) -> None:
        self.setAccessibleName(translate("workspace.panel.clips"))
        self.tiles.update()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.shown.emit()
