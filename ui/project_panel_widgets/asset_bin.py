"""Widgets de la bibliothèque de médias : asset_bin."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from core.project_model import MediaAsset
from ui.design_system import Spacing
from ui.icons import IconName, make_icon
from ui.library_organization_widgets import AssetUsageBadge
from ui.theme import COLORS


class AssetBin(QWidget):
    """Sous-widget : grille d'assets filtrée avec son propre état de sélection.

    Affiche chaque asset comme une vignette :

    - une miniature générée à partir de l'icône du type de média ;
    - le nom court ;
    - la durée formatée ;
    - un badge « ×N » (occurrences sur la timeline) et un point rouge
      pour les fichiers manquants (tâche 25) ;
    - un clic droit ouvre un menu contextuel (renommer, déplacer,
      taguer, relier, supprimer).

    La sélection est marquée par un filet turquoise fin et un fond
    vert foncé subtil — fidèle à la direction artistique premium.

    L'implémentation repose sur un ``QListWidget`` en mode ``ListMode``
    avec un délégué custom qui peint chaque ligne comme une carte
    horizontale (poster + nom + durée + badges). C'est plus simple et
    plus prévisible que ``IconMode``.
    """

    # Signal émis quand l'utilisateur fait un clic droit sur une carte.
    asset_context_menu_requested = Signal(str, object)
    # (asset_id, global_pos)

    # Délégué custom : dessine une carte horizontale par ligne.
    class _CardDelegate(QStyledItemDelegate):
        CARD_HEIGHT = 52
        POSTER = 40
        PADDING = 5

        def sizeHint(self, option, index):  # noqa: D401 - Qt
            width = option.rect.width() if option.rect.width() > 0 else 280
            return QSize(width, self.CARD_HEIGHT + self.PADDING)

        def paint(self, painter, option, index):  # noqa: D401 - Qt
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)

            rect = option.rect.adjusted(2, 2, -2, -2)
            radius = 8
            selected = bool(option.state & QStyle.State_Selected)
            hovered = bool(option.state & QStyle.State_MouseOver)
            base = COLORS["panel_alt"]
            if selected:
                base = COLORS["accent_dark"]
            elif hovered:
                base = COLORS["surface_hover"]
            border_color = QColor(COLORS["accent"] if selected else COLORS["border"])

            path = QPainterPath()
            path.addRoundedRect(rect.toRectF(), radius, radius)
            painter.fillPath(path, QColor(base))
            painter.setPen(QPen(border_color, 1 if not selected else 1.4))
            painter.drawPath(path)

            # Poster carré à gauche, à la taille exacte du widget pour
            # éviter toute déformation du film.
            side = min(self.POSTER, rect.height() - 2 * self.PADDING)
            poster_rect = QRect(
                rect.left() + self.PADDING,
                rect.top() + (rect.height() - side) // 2,
                side,
                side,
            )
            asset = index.data(Qt.UserRole + 1)
            if asset is not None:
                poster = _make_asset_thumbnail(asset, size=side)
                painter.drawPixmap(poster_rect, poster.pixmap(side, side))

            # Nom + durée à droite du poster.
            text_left = poster_rect.right() + self.PADDING * 2
            text_rect = QRect(text_left, rect.top() + self.PADDING,
                              max(10, rect.right() - self.PADDING - text_left),
                              rect.height() - 2 * self.PADDING)
            name = index.data(Qt.DisplayRole) or ""
            duration = index.data(Qt.UserRole + 2) or ""
            name_pen = QColor(COLORS["text"] if selected else COLORS["text"])
            painter.setPen(name_pen)
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            name_rect = text_rect.adjusted(0, 0, 0, -text_rect.height() // 2)
            painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                             painter.fontMetrics().elidedText(
                                 str(name), Qt.ElideRight, name_rect.width()
                             ))
            font.setBold(False)
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QColor(COLORS["accent"] if selected else COLORS["muted"]))
            duration_rect = text_rect.adjusted(0, text_rect.height() // 2, 0, 0)
            painter.drawText(duration_rect, Qt.AlignVCenter | Qt.AlignLeft,
                             str(duration))

            # --- Badges (tâche 25) ------------------------------------
            # Lecture des données portées par l'item : badge usage +
            # drapeau manquant + couleurs de tags.
            badge = index.data(Qt.UserRole + 3)
            if badge is not None:
                self._paint_badges(painter, rect, badge)

            painter.restore()

        def _paint_badges(self, painter, rect, badge) -> None:
            """Dessine les badges (compteur d'usage, manquant, tags)."""
            # Compteur d'occurrences (« ×N ») à droite de la durée.
            usage_count = getattr(badge, "usage_count", 0)
            is_missing = getattr(badge, "is_missing", False)
            tag_colors = list(getattr(badge, "tag_colors", []) or [])
            right_edge = rect.right() - self.PADDING
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            # Badge « ×N » : collé à droite. On n'affiche rien si 0
            # pour ne pas surcharger la carte.
            if usage_count > 0:
                label = f"×{usage_count}"
                fm = painter.fontMetrics()
                width = fm.horizontalAdvance(label) + 10
                height = 16
                badge_rect = QRect(
                    right_edge - width,
                    rect.top() + (rect.height() - height) // 2,
                    width,
                    height,
                )
                right_edge = badge_rect.left() - 4
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(COLORS["accent"]))
                painter.drawRoundedRect(badge_rect, 8, 8)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(badge_rect, Qt.AlignCenter, label)
            # Pastille « PX » : état du proxy (prêt, en cours, erreur…).
            proxy_state = getattr(badge, "proxy_state", "")
            if proxy_state and proxy_state != "none":
                progress = int(getattr(badge, "proxy_progress", 0))
                label, color = {
                    "ready": ("PX", COLORS["accent"]),
                    "generating": (f"PX {progress}%", COLORS["warning"]),
                    "pending": ("PX…", COLORS["muted"]),
                    "error": ("PX!", COLORS["danger"]),
                    "stale": ("PX↻", COLORS["warning"]),
                }.get(proxy_state, ("", COLORS["muted"]))
                if label:
                    fm = painter.fontMetrics()
                    width = fm.horizontalAdvance(label) + 8
                    proxy_rect = QRect(
                        right_edge - width,
                        rect.top() + (rect.height() - 14) // 2,
                        width,
                        14,
                    )
                    right_edge = proxy_rect.left() - 4
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(color))
                    painter.drawRoundedRect(proxy_rect, 6, 6)
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(proxy_rect, Qt.AlignCenter, label)
            # Pastilles de tags : 6 px de diamètre, à droite du badge
            # d'usage. On n'en affiche que 3 maximum.
            if tag_colors:
                chip_size = 8
                spacing = 4
                chips_total = min(3, len(tag_colors))
                chip_row = QRect(
                    0,
                    rect.top() + (rect.height() - chip_size) // 2,
                    chips_total * (chip_size + spacing) - spacing,
                    chip_size,
                )
                chip_row.moveRight(right_edge)
                painter.setPen(Qt.NoPen)
                x = chip_row.left()
                for color in tag_colors[:chips_total]:
                    painter.setBrush(QColor(color))
                    painter.drawEllipse(x, chip_row.top(), chip_size, chip_size)
                    x += chip_size + spacing
            # Point d'avertissement « manquant » : à droite de la
            # ligne, plus visible que les pastilles.
            if is_missing:
                warn_size = 10
                warn_rect = QRect(
                    0,
                    rect.top() + (rect.height() - warn_size) // 2,
                    warn_size,
                    warn_size,
                )
                warn_rect.moveRight(rect.right() - self.PADDING)
                painter.setBrush(QColor(COLORS["danger"]))
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.drawEllipse(warn_rect)

    def __init__(self, on_item_clicked, on_selection_changed, parent=None) -> None:
        super().__init__(parent)
        self._assets: dict[str, MediaAsset] = {}
        # Mapping ``asset_id -> AssetUsageBadge`` consommé par le
        # délégué pour peindre les badges (compteur d'usage, tags,
        # fichier manquant). Réinjecté via ``apply_badges``.
        self._badges: dict[str, AssetUsageBadge] = {}
        # Sans cette politique, le QStackedWidget plafonne la page à sa
        # taille naturelle et la grille n'affiche qu'une vignette.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.xs, Spacing.xs, Spacing.xs, Spacing.xs)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setAcceptDrops(False)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none;"
            f" outline: 0; padding: 2px; }}"
            f"QListWidget::item {{ background: transparent;"
            f" border: none; padding: 0; margin: 2px 0; }}"
        )
        self._delegate = self._CardDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.itemClicked.connect(
            lambda item: on_item_clicked(item.data(Qt.UserRole))
        )
        self._list.mousePressEvent = self._wrap_mouse_press(self._list.mousePressEvent)
        self._list.mouseMoveEvent = self._wrap_mouse_move(self._list.mouseMoveEvent)
        # Clic droit : on l'attrape pour ouvrir le menu contextuel.
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentRowChanged.connect(
            lambda row: on_selection_changed(
                self._list.item(row).data(Qt.UserRole)
                if 0 <= row < self._list.count()
                else None
            )
        )
        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def add_asset(self, asset: MediaAsset) -> None:
        self._assets[asset.id] = asset
        item = self._make_item(asset)
        self._list.addItem(item)

    def set_assets(self, assets: list[MediaAsset]) -> None:
        """Remplace le contenu de la grille."""
        self._list.clear()
        self._assets = {a.id: a for a in assets}
        for asset in assets:
            self._list.addItem(self._make_item(asset))

    def _make_item(self, asset: MediaAsset) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, asset.id)
        item.setData(Qt.UserRole + 1, asset)
        item.setData(Qt.UserRole + 2, _format_duration(asset.duration))
        item.setData(Qt.UserRole + 3, self._badges.get(asset.id))
        item.setData(Qt.DisplayRole, asset.name)
        item.setToolTip(self._make_tooltip(asset))
        return item

    def _make_tooltip(self, asset: MediaAsset) -> str:
        """Tooltip enrichi : nom + durée + statut manquant."""
        base = f"{asset.name}\n{_format_duration(asset.duration)}"
        badge = self._badges.get(asset.id)
        if badge is None:
            return base
        extras: list[str] = []
        if badge.usage_count > 0:
            extras.append(f"Utilisé {badge.usage_count}× sur la timeline")
        if badge.is_missing:
            extras.append("⚠ Fichier source introuvable — utilisez Relier")
        if badge.proxy_state and badge.proxy_state != "none":
            from ui.i18n import translate

            line = translate(f"proxy.state.{badge.proxy_state}", progress=badge.proxy_progress)
            if badge.proxy_state == "error" and badge.proxy_error:
                line += f" — {badge.proxy_error}"
            extras.append(line)
        if extras:
            base += "\n" + "\n".join(extras)
        return base

    def apply_badges(self, badges: dict[str, AssetUsageBadge]) -> None:
        """Réinjecte les badges et repeint les cartes (tâche 25)."""
        self._badges = dict(badges)
        for row in range(self._list.count()):
            item = self._list.item(row)
            asset_id = item.data(Qt.UserRole)
            item.setData(Qt.UserRole + 3, self._badges.get(asset_id))
            asset = item.data(Qt.UserRole + 1)
            if asset is not None:
                item.setToolTip(self._make_tooltip(asset))
        # ``viewport().update()`` force le délégué à repeindre même
        # sans changement de géométrie.
        self._list.viewport().update()

    def all_assets(self) -> list[MediaAsset]:
        """Retourne tous les assets connus (sans filtre)."""
        return list(self._assets.values())

    def clear(self) -> None:
        self._list.clear()
        self._assets.clear()

    def count(self) -> int:
        return self._list.count()

    def item(self, row: int) -> QListWidgetItem:
        return self._list.item(row)

    def setCurrentRow(self, row: int) -> None:
        self._list.setCurrentRow(row)

    def currentRow(self) -> int:
        return self._list.currentRow()

    @property
    def selected_asset_id(self) -> str | None:
        row = self._list.currentRow()
        if not (0 <= row < self._list.count()):
            return None
        return self._list.item(row).data(Qt.UserRole)

    # ------------------------------------------------------------------
    # Menu contextuel
    # ------------------------------------------------------------------

    def _on_context_menu(self, position) -> None:
        item = self._list.itemAt(position)
        if item is None:
            return
        asset_id = item.data(Qt.UserRole)
        if asset_id is None:
            return
        global_pos = self._list.viewport().mapToGlobal(position)
        self.asset_context_menu_requested.emit(asset_id, global_pos)

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def _wrap_mouse_press(self, original):
        outer = self

        def handler(event):
            outer._drag_origin = event.position()
            outer._dragging = False
            return original(event)

        return handler

    def _wrap_mouse_move(self, original):
        outer = self

        def handler(event):
            if (
                event.buttons() & Qt.LeftButton
                and getattr(outer, "_drag_origin", None) is not None
                and not getattr(outer, "_dragging", False)
            ):
                start = outer._drag_origin
                distance = (
                    (event.position().x() - start.x()) ** 2
                    + (event.position().y() - start.y()) ** 2
                ) ** 0.5
                if distance >= QApplication.startDragDistance():
                    item = outer._list.currentItem()
                    if item is not None:
                        asset_id = item.data(Qt.UserRole)
                        outer._start_drag(asset_id)
            return original(event)

        return handler

    def _start_drag(self, asset_id: str) -> None:
        mime = QMimeData()
        mime.setData("application/x-kut-studio-asset-id", asset_id.encode("utf-8"))
        drag = QDrag(self._list)
        drag.setMimeData(mime)
        self._dragging = True
        drag.exec(Qt.CopyAction, Qt.CopyAction)


def _format_duration(seconds: float | None) -> str:
    """Formate une durée en ``mm:ss`` (ou ``--`` si inconnue)."""
    if seconds is None or seconds <= 0:
        return "--:--"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_asset_caption(asset: MediaAsset) -> str:
    """Construit un libellé court (nom + durée) pour la grille."""
    duration = _format_duration(asset.duration)
    name = (asset.name or "Sans nom").strip()
    if len(name) > 24:
        name = name[:23] + "…"
    return f"{name}\n{duration}"


def _make_asset_thumbnail(asset: MediaAsset, size: int = 40) -> QIcon:
    """Génère une vignette carrée stylisée pour un asset.

    Pas d'extraction d'image vidéo : on dessine un poster sobre —
    bandeau de couleur typé + pictogramme — sur un fond vert-noir. La
    vignette reste donc honnête (aucune fausse preview) tout en gardant
    une identité visuelle constante et un repère de type lisible d'un
    coup d'œil.

    Le pixmap est produit à la taille exacte demandée : le délégué le
    redimensionne ensuite sans étirement.
    """
    is_audio = asset.media_type == "audio"
    icon_name = IconName.AUDIO if is_audio else IconName.FILM
    accent = COLORS["track_audio"] if is_audio else COLORS["track_video"]

    side = max(16, int(size))
    pixmap = QPixmap(side, side)
    pixmap.fill(QColor(COLORS["panel_alt"]))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Bandeau coloré à gauche, façon étui de pellicule.
    band = max(2, side // 8)
    painter.fillRect(0, 0, band, side, QColor(accent))
    # Pictogramme centré dans la zone restante.
    icon_side = max(8, int(side * 0.42))
    icon = make_icon(icon_name, size=icon_side)
    icon_rect = QRect(
        band + (side - band - icon_side) // 2,
        (side - icon_side) // 2,
        icon_side,
        icon_side,
    )
    painter.drawPixmap(icon_rect, icon.pixmap(icon_side, icon_side))
    painter.end()
    return QIcon(pixmap)
