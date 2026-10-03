"""Panneau « Scopes » de monitoring couleur (tâche 31).

Fournit les quatre vues professionnelles :

- **Histogramme** luminance + RGB superposés ;
- **Waveform** luminance (colonnes × niveaux) ;
- **Parade** RGB (trois waveform côte à côte) ;
- **Vectorscope** avec ligne de teinte de peau.

Chaque vue est un :class:`_ScopeCanvas` qui se dessine directement au
``QPainter``, sans image intermédiaire : le panneau ne redessine que
lorsque l'analyseur fournit un nouveau
:class:`~core.scopes.ScopeResult`, pas à chaque frame.

Le panneau est :

- **redimensionnable** (le widget expose une taille minimale et
  s'adapte au layout) ;
- **configurable** : on peut choisir le scope affiché ou la
  disposition à quatre vues ;
- **non bloquant** : l'analyse s'exécute dans
  :class:`~core.scopes_analyzer.ScopeAnalyzer` (thread de travail) et
  le résultat est réinjecté via un signal Qt.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.scopes import (
    SKIN_TONE_LINE_DEGREES,
    ColorSpace,
    ScopeResult,
    VideoLevels,
    levels_bounds,
)
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.theme import COLORS, label_style


# ---------------------------------------------------------------------------
# Disposition
# ---------------------------------------------------------------------------


class ScopeLayout(str, Enum):
    """Disposition du panneau de scopes.

    ``SINGLE`` n'affiche qu'un scope à la fois (choisi via la
    combo). ``QUAD`` affiche les quatre scopes en grille 2×2, comme
    dans un logiciel de montage professionnel.
    """

    SINGLE = "single"
    QUAD = "quad"


class ScopeView(str, Enum):
    """Scope individuel affichable en mode ``SINGLE``."""

    HISTOGRAM = "histogram"
    WAVEFORM = "waveform"
    PARADE = "parade"
    VECTORSCOPE = "vectorscope"


# ---------------------------------------------------------------------------
# Widgets de dessin
# ---------------------------------------------------------------------------


class _ScopeCanvas(QWidget):
    """Zone de dessin d'un scope (redessinée à chaque nouveau résultat)."""

    def __init__(self, view: ScopeView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._result: Optional[ScopeResult] = None
        self._levels = VideoLevels.VIDEO
        self._color_space = ColorSpace.REC709
        # Plancher bas : en quad deux rangées de scopes doivent tenir dans le plancher du panneau
        # (``Sizes.scopes_min_height``) sans déborder.
        self.setMinimumSize(96, 32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background: {COLORS['background']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
        )

    def set_result(self, result: ScopeResult | None) -> None:
        """Met à jour le résultat affiché et déclenche un repaint."""
        self._result = result
        self.update()

    def result(self) -> Optional[ScopeResult]:
        return self._result

    def set_levels(self, levels: VideoLevels) -> None:
        """Change le mode de niveaux (grille 16‑235 ou 0‑255)."""
        if levels is self._levels:
            return
        self._levels = levels
        self.update()

    def set_color_space(self, color_space: ColorSpace) -> None:
        """Change l'espace colorimétrique documenté sur la grille."""
        if color_space is self._color_space:
            return
        self._color_space = color_space
        self.update()

    def view(self) -> ScopeView:
        return self._view

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, False)
            rect = self.rect().adjusted(1, 1, -1, -1)
            painter.fillRect(rect, QColor(COLORS["background"]))
            if self._result is None:
                self._paint_placeholder(painter, rect)
                return
            low, high = levels_bounds(self._levels)
            self._paint_grid(painter, rect, low, high)
            if self._view is ScopeView.HISTOGRAM:
                self._paint_histogram(painter, rect, low, high)
            elif self._view is ScopeView.WAVEFORM:
                self._paint_waveform(painter, rect, low, high)
            elif self._view is ScopeView.PARADE:
                self._paint_parade(painter, rect, low, high)
            elif self._view is ScopeView.VECTORSCOPE:
                self._paint_vectorscope(painter, rect)
        finally:
            painter.end()

    def _paint_placeholder(self, painter: QPainter, rect) -> None:
        painter.setPen(QPen(QColor(COLORS["muted"])))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(
            rect, Qt.AlignCenter,
            translate("scopes.placeholder"),
        )

    def _paint_grid(self, painter: QPainter, rect, low: float, high: float) -> None:
        """Grille de repères (0 %, 50 %, 100 %)."""
        painter.setPen(QPen(QColor(COLORS["divider"]), 1, Qt.DotLine))
        for fraction in (0.25, 0.5, 0.75):
            y = rect.bottom() - int(rect.height() * fraction)
            painter.drawLine(rect.left(), y, rect.right(), y)

    def _paint_histogram(self, painter: QPainter, rect, low, high) -> None:
        result = self._result
        assert result is not None
        span = max(high - low, 1.0)
        # Traces RGB en arrière-plan, luminance au premier plan.
        traces = (
            (result.histogram_r, QColor(255, 96, 96, 140)),
            (result.histogram_g, QColor(96, 255, 96, 140)),
            (result.histogram_b, QColor(96, 140, 255, 140)),
        )
        for histogram, color in traces:
            self._draw_histogram_trace(
                painter, rect, histogram, low, high, span, color, fill=False
            )
        self._draw_histogram_trace(
            painter, rect, result.histogram_luma, low, high, span,
            QColor(COLORS["accent"]), fill=True,
        )

    def _draw_histogram_trace(
        self, painter, rect, histogram, low, high, span, color, *,
        fill: bool,
    ) -> None:
        # Le dénominateur est la somme des barres, pas
        # ``pixel_count`` : sur une grande image l'analyse travaille
        # sur un sous-ensemble d'échantillons, et normaliser par le
        # nombre total de pixels écraserait la trace d'un facteur
        # égal au pas d'échantillonnage.
        total = max(1, sum(histogram))
        width = rect.width()
        height = rect.height()
        span_bins = max(high - low, 1.0)
        painter.setPen(QPen(color, 1))
        points: list[tuple[float, float]] = []
        for level in range(256):
            # On ne trace que les bins dans la plage Legal.
            if level < low or level > high:
                continue
            value = histogram[level]
            # Normalisation logarithmique douce : sans elle, une
            # image sombre est invisible (toutes les barres à 0).
            normalized = math.log1p(value) / math.log1p(total)
            x = rect.left() + (level - low) / span_bins * width
            y = rect.bottom() - normalized * height
            points.append((x, y))
        if not points:
            return
        if fill:
            polygon = QPolygonFCompat(points)
            painter.setPen(QPen(color, 1))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 60))
            painter.drawPolygon(polygon.build())
        else:
            previous: Optional[tuple[float, float]] = None
            for point in points:
                if previous is not None:
                    painter.drawLine(
                        int(previous[0]), int(previous[1]),
                        int(point[0]), int(point[1]),
                    )
                previous = point

    def _paint_waveform(self, painter: QPainter, rect, low, high) -> None:
        result = self._result
        assert result is not None
        columns = len(result.waveform)
        if columns == 0:
            return
        width = rect.width()
        height = rect.height()
        span = max(high - low, 1.0)
        # On n'échantillonne la grille qu'aux niveaux réellement
        # occupés : une image naturelle en a une poignée par colonne,
        # une image de bruit beaucoup plus. Le curseur mémorise la
        # dernière opacité utilisée, car ``QColor`` / ``QPen`` sont
        # bien plus coûteux que le ``drawPoint`` lui-même.
        last_alpha = -1
        for column_index in range(columns):
            column = result.waveform[column_index]
            x = rect.left() + int(column_index * width / columns)
            for level in range(256):
                density = column[level]
                if density <= 0.0:
                    continue
                if level < low or level > high:
                    continue
                y = rect.bottom() - int((level - low) / span * height)
                # L'opacité du pixel reflète la densité : c'est le
                # rendu classique d'une waveform (plus dense = plus
                # opaque). La racine carrée évite qu'une image très
                # détaillée — donc une densité faible par niveau —
                # ne s'affiche comme un aplat quasi invisible.
                alpha = int((density ** 0.5) * 255.0)
                if alpha != last_alpha:
                    color = QColor(COLORS["accent"])
                    color.setAlpha(max(20, min(255, alpha)))
                    painter.setPen(QPen(color, 1))
                    last_alpha = alpha
                painter.drawPoint(x, y)

    def _paint_parade(self, painter: QPainter, rect, low, high) -> None:
        result = self._result
        assert result is not None
        columns = len(result.parade)
        if columns == 0:
            return
        width = rect.width()
        height = rect.height()
        span = max(high - low, 1.0)
        third = width / 3.0
        colors = (
            QColor(255, 96, 96),
            QColor(96, 255, 96),
            QColor(96, 140, 255),
        )
        for channel in range(3):
            offset = int(third * channel)
            channel_width = int(third)
            color = colors[channel]
            # Même optimisation que la waveform : on ne recrée le
            # stylo que lorsque l'opacité change.
            last_alpha = -1
            for column_index in range(columns):
                column = result.parade[column_index]
                x = rect.left() + offset + int(
                    column_index * channel_width / columns
                )
                for level in range(256):
                    density = column[channel][level]
                    if density <= 0.0 or level < low or level > high:
                        continue
                    y = rect.bottom() - int((level - low) / span * height)
                    # Même courbe racine que la waveform : une parade
                    # détaillée garde des densités faibles par niveau.
                    alpha = int((density ** 0.5) * 255.0)
                    if alpha != last_alpha:
                        pen_color = QColor(color)
                        pen_color.setAlpha(max(20, min(255, alpha)))
                        painter.setPen(QPen(pen_color, 1))
                        last_alpha = alpha
                    painter.drawPoint(x, y)

    def _paint_vectorscope(self, painter: QPainter, rect) -> None:
        result = self._result
        assert result is not None
        bins = len(result.vectorscope)
        if bins == 0:
            return
        size = min(rect.width(), rect.height()) - 8
        if size <= 0:
            return
        # Cercle centré.
        cx = rect.center().x()
        cy = rect.center().y()
        radius = size / 2.0
        painter.setPen(QPen(QColor(COLORS["border_strong"]), 1))
        painter.drawEllipse(
            int(cx - radius), int(cy - radius),
            int(radius * 2), int(radius * 2),
        )
        # Lignes de repère : axes horizontaux / verticaux.
        painter.setPen(QPen(QColor(COLORS["divider"]), 1, Qt.DotLine))
        painter.drawLine(
            int(cx - radius), cy, int(cx + radius), cy
        )
        painter.drawLine(cx, int(cy - radius), cx, int(cy + radius))
        # Ligne de teinte de peau (référence IREF, ~123°).
        # Le vecteur U/V est projeté comme dans ``compute_vectorscope`` :
        # U horizontal, V vertical (inversé car l'axe Y descend).
        angle = math.radians(SKIN_TONE_LINE_DEGREES)
        ux = math.cos(angle - math.pi / 2.0)
        uy = -math.sin(angle - math.pi / 2.0)
        painter.setPen(QPen(QColor(COLORS["marker"]), 1, Qt.DashLine))
        painter.drawLine(
            int(cx), int(cy),
            int(cx + ux * radius * 0.5), int(cy + uy * radius * 0.5),
        )
        # Trace des points. Le bin ``(row, col)`` correspond au point
        # ``(col / bins, row / bins)`` du carré unitaire ; on le
        # projette dans le cercle en retranchant 0.5 pour recentrer
        # sur le point neutre.
        painter.setPen(QPen(QColor(COLORS["accent"]), 1))
        for row in range(bins):
            for column_index in range(bins):
                density = result.vectorscope[row][column_index]
                if density <= 0.02:
                    continue
                # Coordonnées normalisées du bin, recentrées sur 0.
                nx = (column_index + 0.5) / bins - 0.5
                ny = (row + 0.5) / bins - 0.5
                x = int(cx + nx * radius * 2.0)
                y = int(cy + ny * radius * 2.0)
                alpha = max(30, min(255, int(density * 255)))
                color = QColor(COLORS["accent"])
                color.setAlpha(alpha)
                painter.setPen(QPen(color, 1))
                painter.drawPoint(x, y)


class QPolygonFCompat:
    """Construit un ``QPolygonF`` depuis une liste de paires ``(x, y)``."""

    def __init__(self, points: list[tuple[float, float]]) -> None:
        self._polygon = QPolygonF([QPointF(x, y) for x, y in points])

    def build(self) -> QPolygonF:
        return self._polygon


# ---------------------------------------------------------------------------
# Panneau principal
# ---------------------------------------------------------------------------


class ScopesPanel(QWidget):
    """Panneau de monitoring couleur avec les quatre scopes."""

    #: Émis quand la disposition change (persistance des préférences).
    layout_changed = Signal(str)
    #: Émis quand les niveaux (video / full) changent.
    levels_changed = Signal(str)
    #: Émis quand l'utilisateur veut rafraîchir immédiatement.
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scopes_panel")
        self.setStyleSheet(
            f"QWidget#scopes_panel {{ background: {COLORS['panel']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; }}"
        )
        # Taille minimale : le panneau doit rester lisible même
        # dans un layout serré.
        self.setMinimumSize(280, Sizes.scopes_min_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._layout_mode = ScopeLayout.QUAD
        self._single_view = ScopeView.WAVEFORM
        self._levels = VideoLevels.VIDEO
        self._color_space = ColorSpace.REC709
        self._result: Optional[ScopeResult] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        root.setSpacing(Spacing.xs)

        # --- Barre d'outils ------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(Spacing.xs)

        title = QLabel("SCOPES")
        title.setStyleSheet(label_style(10, "muted", 800))
        toolbar.addWidget(title)
        toolbar.addStretch(1)

        self.layout_combo = QComboBox()
        self.layout_combo.setObjectName("scopesLayoutCombo")
        self.layout_combo.setFocusPolicy(Qt.NoFocus)
        self.layout_combo.addItem(
            translate("scopes.layout.quad"), ScopeLayout.QUAD.value
        )
        self.layout_combo.addItem(
            translate("scopes.layout.single"), ScopeLayout.SINGLE.value
        )
        self.layout_combo.currentIndexChanged.connect(
            self._on_layout_changed
        )
        toolbar.addWidget(self.layout_combo)

        self.view_combo = QComboBox()
        self.view_combo.setObjectName("scopesViewCombo")
        self.view_combo.setFocusPolicy(Qt.NoFocus)
        for view in ScopeView:
            self.view_combo.addItem(
                translate(f"scopes.view.{view.value}"), view.value
            )
        self.view_combo.setCurrentIndex(
            list(ScopeView).index(self._single_view)
        )
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        toolbar.addWidget(self.view_combo)

        self.levels_combo = QComboBox()
        self.levels_combo.setObjectName("scopesLevelsCombo")
        self.levels_combo.setFocusPolicy(Qt.NoFocus)
        self.levels_combo.addItem(
            translate("scopes.levels.video"), VideoLevels.VIDEO.value
        )
        self.levels_combo.addItem(
            translate("scopes.levels.full"), VideoLevels.FULL.value
        )
        self.levels_combo.currentIndexChanged.connect(self._on_levels_changed)
        toolbar.addWidget(self.levels_combo)

        self.refresh_button = QPushButton(translate("scopes.refresh"))
        self.refresh_button.setObjectName("scopesRefresh")
        self.refresh_button.setCursor(Qt.PointingHandCursor)
        self.refresh_button.setFocusPolicy(Qt.NoFocus)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        toolbar.addWidget(self.refresh_button)

        root.addLayout(toolbar)

        # --- Zone des scopes ----------------------------------------------
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(Spacing.xs)
        root.addWidget(self._grid_host, 1)

        # Les quatre canvas sont créés une fois et repositionnés
        # selon la disposition : cela évite de reconstruire la
        # hiérarchie Qt à chaque bascule.
        self._canvases: dict[ScopeView, _ScopeCanvas] = {}
        for view in ScopeView:
            canvas = _ScopeCanvas(view)
            self._canvases[view] = canvas
        self._apply_layout()

        # --- Alertes d'écrêtage -------------------------------------------
        self._alert_bar = QFrame()
        self._alert_bar.setObjectName("scopesAlerts")
        self._alert_bar.setStyleSheet(
            f"QFrame#scopesAlerts {{ background: {COLORS['panel_alt']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
        )
        self._alert_layout = QHBoxLayout(self._alert_bar)
        self._alert_layout.setContentsMargins(
            Spacing.sm, 2, Spacing.sm, 2
        )
        self._alert_layout.setSpacing(Spacing.md)
        self._black_alert = QLabel(translate("scopes.alert.black"))
        self._black_alert.setStyleSheet(label_style(10, "muted", 600))
        self._white_alert = QLabel(translate("scopes.alert.white"))
        self._white_alert.setStyleSheet(label_style(10, "muted", 600))
        self._black_alert.setVisible(False)
        self._white_alert.setVisible(False)
        self._alert_layout.addWidget(self._black_alert)
        self._alert_layout.addWidget(self._white_alert)
        self._alert_layout.addStretch(1)
        self._alert_bar.setVisible(False)
        root.addWidget(self._alert_bar)

        # Alertes optionnelles (désactivées par défaut pour ne pas
        # importuner pendant l'étalonnage).
        self._alerts_enabled = False

    # ----- API publique ---------------------------------------------------

    def set_result(self, result: Optional[ScopeResult]) -> None:
        """Injecte un nouveau résultat d'analyse dans les quatre vues."""
        self._result = result
        for canvas in self._canvases.values():
            canvas.set_result(result)
        self._update_alerts(result)

    def result(self) -> Optional[ScopeResult]:
        return self._result

    def set_layout_mode(self, mode: ScopeLayout | str) -> None:
        """Bascule entre disposition quad et scope unique."""
        try:
            value = ScopeLayout(mode)
        except ValueError:
            return
        if value is self._layout_mode:
            return
        self._layout_mode = value
        index = self.layout_combo.findData(value.value)
        if index >= 0:
            self.layout_combo.blockSignals(True)
            self.layout_combo.setCurrentIndex(index)
            self.layout_combo.blockSignals(False)
        self._apply_layout()
        self.layout_changed.emit(value.value)

    def layout_mode(self) -> ScopeLayout:
        return self._layout_mode

    def set_single_view(self, view: ScopeView | str) -> None:
        """Choisit le scope affiché en mode ``SINGLE``."""
        try:
            value = ScopeView(view)
        except ValueError:
            return
        self._single_view = value
        index = self.view_combo.findData(value.value)
        if index >= 0:
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(index)
            self.view_combo.blockSignals(False)
        if self._layout_mode is ScopeLayout.SINGLE:
            self._apply_layout()

    def single_view(self) -> ScopeView:
        return self._single_view

    def set_levels(self, levels: VideoLevels | str) -> None:
        """Change le mode de niveaux (``video`` / ``full``)."""
        try:
            value = VideoLevels(levels)
        except ValueError:
            return
        if value is self._levels:
            return
        self._levels = value
        index = self.levels_combo.findData(value.value)
        if index >= 0:
            self.levels_combo.blockSignals(True)
            self.levels_combo.setCurrentIndex(index)
            self.levels_combo.blockSignals(False)
        for canvas in self._canvases.values():
            canvas.set_levels(value)
        self.levels_changed.emit(value.value)

    def levels(self) -> VideoLevels:
        return self._levels

    def set_color_space(self, color_space: ColorSpace | str) -> None:
        """Change l'espace colorimétrique utilisé pour la luminance."""
        try:
            value = ColorSpace(color_space)
        except ValueError:
            return
        self._color_space = value
        for canvas in self._canvases.values():
            canvas.set_color_space(value)

    def set_alerts_enabled(self, enabled: bool) -> None:
        """Active / désactive l'affichage des alertes d'écrêtage."""
        self._alerts_enabled = bool(enabled)
        self._update_alerts(self._result)

    def alerts_enabled(self) -> bool:
        return self._alerts_enabled

    # ----- Persistance des préférences ------------------------------------

    def preferences(self) -> dict[str, str]:
        """Retourne les préférences sérialisables du panneau."""
        return {
            "layout": self._layout_mode.value,
            "single_view": self._single_view.value,
            "levels": self._levels.value,
            "alerts_enabled": "1" if self._alerts_enabled else "0",
        }

    def apply_preferences(self, prefs: dict[str, str]) -> None:
        """Applique des préférences précédemment sérialisées.

        Les valeurs inconnues sont ignorées silencieusement : une
        préférence corrompue ne doit jamais empêcher le panneau de
        s'ouvrir.
        """
        if not isinstance(prefs, dict):
            return
        self.set_layout_mode(prefs.get("layout", self._layout_mode.value))
        self.set_single_view(
            prefs.get("single_view", self._single_view.value)
        )
        self.set_levels(prefs.get("levels", self._levels.value))
        alerts = prefs.get("alerts_enabled")
        if alerts is not None:
            self.set_alerts_enabled(alerts in ("1", "true", "True"))

    # ----- Interne ---------------------------------------------------------

    def _apply_layout(self) -> None:
        # On retire tous les canvas du grid puis on replace selon le
        # mode courant.
        for canvas in self._canvases.values():
            self._grid.removeWidget(canvas)
            canvas.setParent(None)
        if self._layout_mode is ScopeLayout.QUAD:
            positions = {
                ScopeView.WAVEFORM: (0, 0),
                ScopeView.HISTOGRAM: (0, 1),
                ScopeView.PARADE: (1, 0),
                ScopeView.VECTORSCOPE: (1, 1),
            }
        else:
            positions = {self._single_view: (0, 0)}
        for view, (row, column) in positions.items():
            canvas = self._canvases[view]
            self._grid.addWidget(canvas, row, column)
        # Le sélecteur de scope n'a de sens qu'en mode single.
        self.view_combo.setVisible(
            self._layout_mode is ScopeLayout.SINGLE
        )

    def _on_layout_changed(self, index: int) -> None:
        data = self.layout_combo.itemData(index)
        if not data:
            return
        self.set_layout_mode(data)

    def _on_view_changed(self, index: int) -> None:
        data = self.view_combo.itemData(index)
        if not data:
            return
        try:
            value = ScopeView(data)
        except ValueError:
            return
        self._single_view = value
        if self._layout_mode is ScopeLayout.SINGLE:
            self._apply_layout()

    def _on_levels_changed(self, index: int) -> None:
        data = self.levels_combo.itemData(index)
        if not data:
            return
        try:
            self.set_levels(VideoLevels(data))
        except ValueError:
            return

    def _update_alerts(self, result: Optional[ScopeResult]) -> None:
        if not self._alerts_enabled or result is None:
            self._set_alert(self._black_alert, "scopes.alert.black", None)
            self._set_alert(self._white_alert, "scopes.alert.white", None)
        else:
            self._set_alert(
                self._black_alert,
                "scopes.alert.black",
                result.alerts.black_clipping,
            )
            self._set_alert(
                self._white_alert,
                "scopes.alert.white",
                result.alerts.highlight_clipping,
            )
        # La barre vide ne mangeait que de la hauteur : elle n'apparaît que pour porter une alerte.
        self._alert_bar.setVisible(
            not (self._black_alert.isHidden() and self._white_alert.isHidden())
        )

    @staticmethod
    def _set_alert(
        label: QLabel, key: str, ratio: float | None,
    ) -> None:
        """Affiche (ou masque) une alerte d'écrêtage.

        Le libellé est systématiquement remis à son texte court quand
        l'alerte retombe à zéro : sinon le pourcentage de la frame
        précédente resterait affiché.
        """
        if ratio is None or ratio <= 0.0:
            label.setText(translate(key))
            label.setStyleSheet(label_style(10, "muted", 600))
            label.setVisible(False)
            return
        label.setText(translate(key) + f"  {ratio * 100:.1f}%")
        label.setStyleSheet(label_style(10, "danger", 700))
        label.setVisible(True)


__all__ = [
    "ScopeLayout",
    "ScopeView",
    "ScopesPanel",
]
