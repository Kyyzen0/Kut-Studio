"""Moniteur Multicam : tous les angles d'un segment côte à côte, l'angle actif en évidence, et le programme.

Un clic sur une tuile montre cet angle à la tête de lecture (la fenêtre fait la coupe, voir ``MulticamMixin``) ; le
moniteur lui-même ne modifie jamais le projet. Il **ne décode rien dans le thread Qt** : chaque tuile lit la dernière
image disponible d'un :class:`~core.multicam_feed.AngleFeed` (petite image, proxy si possible) et garde la précédente tant
que la suivante n'est pas arrivée. L'image du **programme** est celle de l'angle actif, lue dans le *même* flux : aucun
second décodage de la source pour la montrer.

Grille adaptative (``core.multicam.grid_shape``) : 2 angles côte à côte, 3-4 en 2×2, 5-9 en 3×3, 10-16 en 4×4, pages
de 16 au-delà. La résolution des tuiles baisse avec leur nombre et avec le retard mesuré (``TileQualityGovernor``).
Les angles hors ligne affichent « MEDIA OFFLINE » ; un angle qui n'a pas encore commencé, « NO SIGNAL » : les autres
continuent normalement.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from core.multicam import PAGE_SIZE, AngleSample, AngleState, angle_samples_at, grid_shape, page_count
from core.multicam_ops import multicam_segment_at
from core.multicam_feed import AngleFeed, FeedPool, Frame, TileQualityGovernor, lag_ratio, tile_profile
from core.project_model import Project
from ui import i18n
from ui.design_system import Spacing
from ui.theme import active_palette, label_style, mix_colors, with_alpha

POLL_MS = 40
"""Cadence de rafraîchissement des tuiles tant que le moniteur est visible."""
START_BUDGET = 2
"""Flux d'angles non actifs démarrés par rafraîchissement : l'angle actif d'abord, les autres échelonnés."""


class _TileHost(QWidget):
    """Zone des tuiles : prévient quand sa taille **réelle** change (le layout du parent la fixe après coup)."""

    resized = Signal()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self.resized.emit()


class AngleTile(QWidget):
    """Une tuile : image de l'angle, nom, couleur, angle actif, états « hors ligne » / « pas de signal »."""

    clicked = Signal(int)
    context_requested = Signal(int, object)     # (rang de l'angle, position écran) : clic droit

    def __init__(self, index: int, parent: QWidget | None = None, *, program: bool = False) -> None:
        super().__init__(parent)
        self.index = index
        self.program = program
        self._image: QImage | None = None
        self._frame: Frame | None = None
        self._name = ""
        self._color = QColor(active_palette().muted)
        self._state = AngleState.NO_SIGNAL
        self._active = False
        self._audible = False
        self._audio_only = False
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(24, 14)
        self.setCursor(Qt.PointingHandCursor if not program else Qt.ArrowCursor)

    def set_content(
        self, *, name: str, color: str, state: AngleState, frame: Frame | None, active: bool, audible: bool,
        audio_only: bool, tooltip: str = "",
    ) -> None:
        changed = (
            frame is not self._frame or name != self._name or state is not self._state or active != self._active
            or audible != self._audible or color != self._color.name().upper()
        )
        self._name, self._state, self._active, self._audible = name, state, active, audible
        self._audio_only = audio_only
        self._color = QColor(color)
        if frame is not self._frame:
            self._frame = frame
            self._image = (
                QImage(frame.data, frame.width, frame.height, frame.width * 3, QImage.Format_RGB888)
                if frame is not None else None
            )
        self.setToolTip(tooltip)
        if changed:
            self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.program:
            return
        self.context_requested.emit(self.index, event.globalPos())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.LeftButton and not self.program:
            self.clicked.emit(self.index)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        palette = active_palette()
        painter = QPainter(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(palette.background))
        inner = rect.adjusted(2, 2, -2, -2)
        if self._image is not None and self._state is AngleState.LIVE:
            scale = min(inner.width() / self._image.width(), inner.height() / self._image.height())
            width, height = self._image.width() * scale, self._image.height() * scale
            target = QRectF(inner.center().x() - width / 2, inner.center().y() - height / 2, width, height)
            painter.drawImage(target, self._image)
        message = self._message()
        if message:
            painter.setPen(QColor(palette.muted))
            font = QFont(painter.font())
            font.setPointSizeF(max(7.0, min(11.0, self.width() / 22)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(inner, Qt.AlignCenter, message)
        # Trois rôles, trois bordures : le Programme (la sortie) est cerné de neutre fort, l'angle actif de l'accent, une source inactive
        # d'un filet discret à peine teinté de sa couleur. La couleur de l'angle ne fait plus le cadre : elle reste dans la pastille
        # et la bande du bord gauche, avec le numéro et le nom (jamais la couleur seule).
        if self.program:
            border = QPen(QColor(palette.text_strong))
            border.setWidthF(2.0)
        elif self._active:
            border = QPen(QColor(palette.accent))
            border.setWidthF(3.0)
        else:
            border = QPen(QColor(mix_colors(self._color.name(), palette.background, 0.5)))
            border.setWidthF(1.0)
        painter.setPen(border)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        if not self.program:
            painter.fillRect(QRectF(rect.left() + 3, rect.top() + 3, 3, rect.height() - 6), self._color)
        self._paint_label(painter, inner)
        painter.end()

    def _message(self) -> str:
        if self._state is AngleState.OFFLINE:
            return i18n.translate("multicam.viewer.offline")
        if self._state is AngleState.NO_SIGNAL:
            return i18n.translate("multicam.viewer.no_signal")
        if self._audio_only:
            return i18n.translate("multicam.viewer.audio_only")
        if self._image is None:
            return "…"
        return ""

    def _paint_label(self, painter: QPainter, inner: QRectF) -> None:
        palette = active_palette()
        text = i18n.translate("multicam.viewer.program") if self.program else f"{self.index + 1}  {self._name}"
        if self._active and not self.program:
            text += "  ·  " + i18n.translate("multicam.viewer.active")         # le mot, pas seulement la couleur du cadre
        font = QFont(painter.font())
        font.setPointSizeF(max(7.0, min(10.0, self.width() / 26)))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad = Spacing.xs
        box = QRectF(inner.left(), inner.bottom() - metrics.height() - pad, min(inner.width(), metrics.horizontalAdvance(text) + 2 * pad + (12 if self._audible and not self.program else 0)), metrics.height() + pad)
        # Le repère du Programme est inversé (clair sur sombre), celui de l'angle actif est en accent, les autres sont discrets.
        if self.program:
            background, foreground = palette.text_strong, palette.background
        elif self._active:
            background, foreground = palette.accent, palette.on_accent
        else:
            background, foreground = with_alpha(palette.background, 0.78), palette.clip_text
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRect(box)
        painter.setPen(QColor(foreground))
        painter.drawText(box.adjusted(pad, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        if self._audible and not self.program:
            painter.setBrush(self._color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(box.right() - 8, box.center().y()), 3, 3)  # pastille : son mixé


class MulticamViewer(QWidget):
    """Grille d'angles + programme ; ``angle_requested(rang)`` à chaque clic sur une tuile (0 = Angle 1)."""

    angle_requested = Signal(int)
    settings_requested = Signal()
    proxies_requested = Signal()
    open_source_requested = Signal(int)         # « Ouvrir la source sur cet angle » (étalonner, corriger, repositionner)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        provider: Callable[[], tuple[Project, float, bool]] | None = None,
        resolve_path: Callable[[str], str] | None = None,
        pool: FeedPool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("multicam_viewer")
        self._provider = provider
        self._resolve = resolve_path or (lambda path: path)
        self._pool = pool if pool is not None else FeedPool(AngleFeed)   # un pool vide est « faux » (__len__) : pas de ``or``
        self._clock = clock
        self._governor = TileQualityGovernor()
        self._page = 0
        self._tiles: list[AngleTile] = []
        self._samples: list[AngleSample] = []
        self._segment_id = ""
        self._pending_active: int | None = None
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        i18n.subscribe(self.retranslate)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(self.retranslate))

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("multicam_title")
        header.addWidget(self.title)
        header.addStretch(1)
        self.settings_button = QToolButton()
        self.settings_button.setAutoRaise(True)
        self.settings_button.setFocusPolicy(Qt.TabFocus)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        self.page_previous = QToolButton()
        self.page_previous.setText("‹")  # i18n-ignore: signe de pagination
        self.page_next = QToolButton()
        self.page_next.setText("›")  # i18n-ignore: signe de pagination
        self.page_label = QLabel()
        for button in (self.page_previous, self.page_next):
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.TabFocus)
        self.page_previous.clicked.connect(lambda: self._set_page(self._page - 1))
        self.page_next.clicked.connect(lambda: self._set_page(self._page + 1))
        header.addWidget(self.page_previous)
        header.addWidget(self.page_label)
        header.addWidget(self.page_next)
        root.addLayout(header)
        notice = QHBoxLayout()
        self.proxy_notice = QLabel()
        self.proxy_notice.setWordWrap(True)
        self.proxy_notice.setStyleSheet(label_style(11, "muted", 500))
        self.proxy_button = QToolButton()
        self.proxy_button.setFocusPolicy(Qt.TabFocus)
        self.proxy_button.clicked.connect(self.proxies_requested.emit)
        notice.addWidget(self.proxy_notice, 1)
        notice.addWidget(self.proxy_button)
        self._notice_widgets = (self.proxy_notice, self.proxy_button)
        root.addLayout(notice)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label, 1)
        self.body = _TileHost()
        self.body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.body, 1)
        self.program_tile = AngleTile(-1, self.body, program=True)
        self.body.resized.connect(self._relayout)
        self.retranslate()
        self._show_empty(True)

    def retranslate(self) -> None:
        self.title.setText(i18n.translate("multicam.viewer.title"))
        self.settings_button.setText(i18n.translate("multicam.settings.open"))
        self.settings_button.setToolTip(i18n.translate("multicam.menu.settings"))
        self.proxy_notice.setText(i18n.translate("multicam.viewer.proxy_notice"))
        self.proxy_button.setText(i18n.translate("multicam.viewer.proxy_generate"))
        self.empty_label.setText(i18n.translate("multicam.viewer.empty"))
        self.page_previous.setToolTip(i18n.translate("multicam.viewer.page_previous"))
        self.page_next.setToolTip(i18n.translate("multicam.viewer.page_next"))
        self.page_previous.setAccessibleName(i18n.translate("multicam.viewer.page_previous"))
        self.page_next.setAccessibleName(i18n.translate("multicam.viewer.page_next"))
        self.update()

    # ------------------------------------------------------------------ visibilité

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().hideEvent(event)
        self._timer.stop()
        self._pool.close_all()          # masqué : aucun FFmpeg ne tourne pour rien

    def shutdown(self) -> None:
        """Fermeture de la fenêtre : arrête le minuteur et tous les flux (aucun FFmpeg ne survit)."""
        self._timer.stop()
        self._pool.close_all()

    def set_provider(self, provider: Callable[[], tuple[Project, float, bool]]) -> None:
        self._provider = provider

    def set_active_hint(self, index: int) -> None:
        """Met l'angle cliqué en évidence **tout de suite** ; la prochaine lecture du projet fait foi."""
        self._pending_active = index
        self._apply_samples()

    # ------------------------------------------------------------------ rafraîchissement

    def refresh(self) -> None:
        """Relit la tête de lecture et met à jour tuiles et flux (appelé par le minuteur, après une édition ou un saut)."""
        if self._provider is None or not self.isVisible():
            return
        project, time_seconds, playing = self._provider()
        segment = multicam_segment_at(project, time_seconds)
        if segment is None:
            self._segment_id = ""
            self._samples = []
            self._pool.close_all()
            self._show_empty(True)
            return
        self._show_empty(False)
        self._pending_active = None
        self._segment_id = segment.id
        self._samples = angle_samples_at(project, segment, time_seconds)
        self._drive_feeds(playing)
        self._apply_samples()

    def _visible_samples(self) -> list[AngleSample]:
        start = self._page * PAGE_SIZE
        return self._samples[start:start + PAGE_SIZE]

    def _active_sample(self) -> AngleSample | None:
        """Angle du programme, **quelle que soit la page affichée** (au-delà de 16 angles il peut être sur une autre)."""
        if self._pending_active is not None:
            return next((sample for sample in self._samples if sample.index == self._pending_active), None)
        return next((sample for sample in self._samples if sample.active), None)

    def _drive_feeds(self, playing: bool) -> None:
        visible = self._visible_samples()
        driven = list(visible)
        active = self._active_sample()
        if active is not None and all(sample.index != active.index for sample in visible):
            driven.append(active)                       # le programme continue d'être alimenté si l'on change de page
        wanted: set = set()
        started = 0
        ordered = sorted(driven, key=lambda sample: not sample.active)   # angle actif d'abord, puis les autres
        for sample in ordered:
            if sample.state is not AngleState.LIVE or sample.audio_only:
                continue
            profile = tile_profile(len(visible), level=self._governor.level, active=sample.active)
            path = self._resolve(sample.path)
            feed = self._pool.feed(path, profile)
            wanted.add((path, profile))
            if not feed.running and not sample.active and started >= START_BUDGET:
                continue                                        # démarrage échelonné des angles non actifs
            if not feed.running:
                started += 1
            feed.update(sample.media_time, playing)
        self._pool.retain(wanted)

    def _frame_for(self, sample: AngleSample, visible_count: int) -> tuple[Frame | None, AngleFeed | None]:
        if sample.state is not AngleState.LIVE or sample.audio_only:
            return None, None
        profile = tile_profile(visible_count, level=self._governor.level, active=sample.active)
        feed = self._pool.feed(self._resolve(sample.path), profile)
        return feed.frame_at(sample.media_time), feed

    def _apply_samples(self) -> None:
        palette = active_palette()
        visible = self._visible_samples()
        self._ensure_tiles(len(visible))
        colors = palette.angle_colors
        shown: list[tuple[AngleFeed, float]] = []
        program: Frame | None = None
        program_sample: AngleSample | None = None
        for tile, sample in zip(self._tiles, visible):
            frame, feed = self._frame_for(sample, len(visible))
            active = sample.active if self._pending_active is None else sample.index == self._pending_active
            if active and sample.state is AngleState.LIVE:
                program, program_sample = frame, sample
            elif active and program_sample is None:
                program_sample = sample
            if feed is not None and frame is not None:
                shown.append((feed, feed.last_lag))
            tile.set_content(
                name=sample.angle.name, color=colors[sample.angle.color_index % len(colors)], state=sample.state,
                frame=frame, active=active, audible=sample.audible, audio_only=sample.audio_only,
                tooltip=self._tooltip(sample),
            )
        if program_sample is None:                       # l'angle actif est sur une autre page que celle des vignettes
            off_page = self._active_sample()
            if off_page is not None:
                frame_off, feed_off = self._frame_for(off_page, len(visible))
                program_sample = off_page
                if off_page.state is AngleState.LIVE:
                    program = frame_off
                if feed_off is not None and frame_off is not None:
                    shown.append((feed_off, feed_off.last_lag))
        self._governor.observe(lag_ratio(shown), self._clock())
        self.program_tile.set_content(
            name="", color=palette.accent, state=program_sample.state if program_sample else AngleState.NO_SIGNAL,
            frame=program, active=False, audible=False, audio_only=bool(program_sample and program_sample.audio_only),
        )
        self._update_pages()
        self._update_proxy_notice()

    def _update_proxy_notice(self) -> None:
        """Avec quatre angles vidéo ou plus qui lisent leurs originaux, des proxys fluidifient nettement le moniteur."""
        live = [s for s in self._samples if s.state is AngleState.LIVE and not s.audio_only]
        lacking = [s for s in live if self._resolve(s.path) == s.path]
        for widget in self._notice_widgets:
            widget.setVisible(len(live) >= 4 and bool(lacking))

    @staticmethod
    def _tooltip(sample: AngleSample) -> str:
        return f"{sample.index + 1}. {sample.angle.name}"

    def _ensure_tiles(self, count: int) -> None:
        while len(self._tiles) < count:
            tile = AngleTile(len(self._tiles), self.body)
            tile.clicked.connect(self._on_tile_clicked)
            tile.context_requested.connect(self._on_tile_menu)
            tile.show()
            self._tiles.append(tile)
        for position, tile in enumerate(self._tiles):
            tile.index = self._page * PAGE_SIZE + position
            tile.setVisible(position < count)
        self._layout_tiles(count)

    def _on_tile_menu(self, index: int, position) -> None:
        menu = QMenu(self)
        open_source = menu.addAction(i18n.translate("multicam.viewer.open_source_angle"))
        if self._run_menu(menu, position) is open_source:
            self.open_source_requested.emit(index)

    @staticmethod
    def _run_menu(menu, position):
        """Affiche le menu et rend l'action choisie (isolé : un test le remplace, un menu modal bloquerait)."""
        return menu.exec(position)

    def _on_tile_clicked(self, index: int) -> None:
        self.set_active_hint(index)
        self.angle_requested.emit(index)

    # ------------------------------------------------------------------ disposition

    def _show_empty(self, empty: bool) -> None:
        self.empty_label.setVisible(empty)
        self.body.setVisible(not empty)
        for widget in (self.page_previous, self.page_next, self.page_label):
            widget.setVisible(not empty and page_count(len(self._samples)) > 1)

    def _set_page(self, page: int) -> None:
        self._page = max(0, min(page, page_count(len(self._samples)) - 1))
        self._pool.close_all()
        self.refresh()

    def _update_pages(self) -> None:
        pages = page_count(len(self._samples))
        self._page = min(self._page, pages - 1)
        for widget in (self.page_previous, self.page_next, self.page_label):
            widget.setVisible(pages > 1)
        self.page_label.setText(i18n.translate("multicam.viewer.page", page=self._page + 1, pages=pages))
        self.page_previous.setEnabled(self._page > 0)
        self.page_next.setEnabled(self._page < pages - 1)

    def _relayout(self) -> None:
        self._layout_tiles(sum(1 for tile in self._tiles if not tile.isHidden()))

    def _layout_tiles(self, count: int) -> None:
        """Programme à gauche et grille à droite quand la place le permet, sinon programme au-dessus de la grille."""
        area = self.body.rect()
        if area.width() <= 0 or area.height() <= 0 or count == 0:
            return
        rows, columns = grid_shape(count)
        gap = 4
        wide = area.width() >= 520 and area.width() >= area.height() * 1.15
        if wide:
            program_width = int(area.width() * 0.42)
            program_rect = (0, 0, program_width, area.height())
            grid = (program_width + gap, 0, area.width() - program_width - gap, area.height())
        else:
            program_height = int(area.height() * 0.4)
            program_rect = (0, 0, area.width(), program_height)
            grid = (0, program_height + gap, area.width(), area.height() - program_height - gap)
        self.program_tile.setGeometry(*program_rect)
        self.program_tile.show()
        left, top, width, height = grid
        cell_width = (width - gap * (columns - 1)) / columns
        cell_height = (height - gap * (rows - 1)) / rows
        for position, tile in enumerate(self._tiles[:count]):
            row, column = divmod(position, columns)
            tile.setGeometry(
                int(left + column * (cell_width + gap)), int(top + row * (cell_height + gap)),
                int(cell_width), int(cell_height),
            )


__all__ = ["AngleTile", "MulticamViewer"]
