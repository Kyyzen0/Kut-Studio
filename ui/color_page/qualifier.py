"""Qualifieur du nœud courant : les plages de teinte, saturation et luminance que le nœud corrige.

Une bande par composante, peinte (aucune image) : le dégradé de la composante, la plage choisie en clair, sa douceur
à demi, le reste assombri. On glisse la plage sur la bande (la teinte tourne, les bornes de saturation et de
luminance se prennent par le bord le plus proche) ; trois champs donnent les valeurs exactes. *Afficher la sélection*
montre dans le moniteur ce que la clé choisit (le reste en gris) ; l'export n'en tient jamais compte.

:attr:`QualifierEditor.changed` (le :class:`~core.color_qualifier.Qualifier` ; décoché, il est gardé mais désactivé)
à chaque réglage : la fenêtre regroupe la rafale en une étape d'historique, comme pour les roues.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.color_qualifier import Qualifier
from ui.design_system import Iconography, Spacing
from ui.i18n import translate
from ui.icons import IconName, make_icon
from ui.theme import COLORS, set_role

_CHANNELS = ("hue", "sat", "lum")
_FIELDS = {
    "hue": (("hue_center", 0.0, 360.0, "°"), ("hue_width", 0.0, 360.0, "°"), ("hue_soft", 0.0, 180.0, "°")),
    "sat": (("sat_low", 0.0, 100.0, "%"), ("sat_high", 0.0, 100.0, "%"), ("sat_soft", 0.0, 100.0, "%")),
    "lum": (("lum_low", 0.0, 100.0, "%"), ("lum_high", 0.0, 100.0, "%"), ("lum_soft", 0.0, 100.0, "%")),
}


def _shown(name: str, value: float) -> float:
    """Valeur affichée (degrés, ou pourcentage pour les plages 0..1)."""
    return value if name.startswith("hue") else value * 100.0


def _stored(name: str, value: float) -> float:
    return value if name.startswith("hue") else value / 100.0


class RangeBar(QWidget):
    """La bande d'une composante : son dégradé, la plage choisie ; glisser la déplace (:attr:`dragged`)."""

    dragged = Signal(object)                      # nouveau Qualifier

    def __init__(self, channel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.channel = channel
        self.qualifier = Qualifier()
        self._grab: tuple[str, float, Qualifier] | None = None
        self.setMinimumHeight(18)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.SizeHorCursor)

    def set_qualifier(self, qualifier: Qualifier) -> None:
        self.qualifier = qualifier
        self.update()

    def _span(self) -> tuple[float, float]:
        """Plage (bas, haut) en fraction 0..1 de la bande (la teinte peut passer par 0 : bas > haut)."""
        q = self.qualifier
        if self.channel == "hue":
            half = min(180.0, q.hue_width / 2.0)
            return ((q.hue_center - half) % 360.0) / 360.0, ((q.hue_center + half) % 360.0) / 360.0
        if self.channel == "sat":
            return q.sat_low, q.sat_high
        return q.lum_low, q.lum_high

    def _soft(self) -> float:
        q = self.qualifier
        return {"hue": q.hue_soft / 360.0, "sat": q.sat_soft, "lum": q.lum_soft}[self.channel]

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bar = QRectF(1, 3, self.width() - 2, self.height() - 6)
        gradient = QLinearGradient(bar.topLeft(), bar.topRight())
        for step in range(13):
            fraction = step / 12.0
            if self.channel == "hue":
                color = QColor.fromHsvF(fraction % 1.0, 0.85, 0.95)
            elif self.channel == "sat":
                color = QColor.fromHsvF(0.58, fraction, 0.85)
            else:
                color = QColor.fromHsvF(0.0, 0.0, fraction)
            gradient.setColorAt(fraction, color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(bar, 3, 3)
        used = getattr(self.qualifier, f"use_{self.channel}")
        whole = self.channel == "hue" and self.qualifier.hue_width >= 360.0
        if used and not whole:
            self._shade(painter, bar)
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(bar, 3, 3)

    def _shade(self, painter: QPainter, bar: QRectF) -> None:
        """Assombrit hors de la plage (à demi dans la douceur), sur une teinte qui peut faire le tour."""
        low, high = self._span()
        soft = self._soft()
        steps = max(2, int(bar.width()))
        for index in range(steps):
            fraction = index / (steps - 1)
            if low <= high:
                distance = max(low - fraction, fraction - high, 0.0)
            else:                                              # plage de teinte qui passe par 0°
                distance = 0.0 if (fraction >= low or fraction <= high) else min(fraction - high, low - fraction)
            if distance <= 0.0:
                continue
            alpha = 215 if soft <= 0.0 or distance >= soft else int(215 * distance / soft)
            shade = QColor(COLORS["panel"])
            shade.setAlpha(alpha)
            painter.setPen(QPen(shade, 1))
            x = bar.left() + fraction * bar.width()
            painter.drawLine(QPointF(x, bar.top()), QPointF(x, bar.bottom()))
        painter.setPen(QPen(QColor(COLORS["text_strong"]), 1.5))
        for edge in (low, high):                                # les bornes de la plage, nettes
            x = bar.left() + edge * bar.width()
            painter.drawLine(QPointF(x, bar.top() - 2), QPointF(x, bar.bottom() + 2))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        fraction = event.position().x() / max(1, self.width())
        low, high = self._span()
        if self.channel == "hue":
            grip = "move"
        elif abs(fraction - low) < 0.06 or fraction < low:
            grip = "low"
        elif abs(fraction - high) < 0.06 or fraction > high:
            grip = "high"
        else:
            grip = "move"
        self._grab = (grip, fraction, self.qualifier)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._grab is None:
            return
        grip, start, base = self._grab
        delta = event.position().x() / max(1, self.width()) - start
        if self.channel == "hue":
            updated = replace(base, use_hue=True, hue_center=(base.hue_center + 360.0 * delta) % 360.0)
        else:
            low_name, high_name = f"{self.channel}_low", f"{self.channel}_high"
            low, high = getattr(base, low_name), getattr(base, high_name)
            if grip == "low":
                low = min(high, max(0.0, low + delta))
            elif grip == "high":
                high = max(low, min(1.0, high + delta))
            else:
                shift = max(-low, min(1.0 - high, delta))
                low, high = low + shift, high + shift
            updated = replace(base, **{f"use_{self.channel}": True, low_name: low, high_name: high})
        if updated != self.qualifier:
            self.qualifier = updated
            self.update()
            self.dragged.emit(updated)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._grab = None


class QualifierEditor(QWidget):
    """Qualifieur du nœud courant ; :attr:`changed` (Qualifier), :attr:`highlight_toggled` (bool)."""

    changed = Signal(object)
    highlight_toggled = Signal(bool)
    pick_toggled = Signal(bool)                       # pipette : le prochain clic dans le viewer prend la couleur

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_qualifier")
        self._qualifier: Qualifier | None = None
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        root.setSpacing(Spacing.sm)

        top = QHBoxLayout()
        self.enabled_check = QCheckBox()
        self.enabled_check.toggled.connect(self._on_enabled)
        self.invert_check = QCheckBox()
        self.invert_check.toggled.connect(lambda checked: self._edit(invert=bool(checked)))
        self.highlight_button = QToolButton()
        self.highlight_button.setObjectName("chipButton")
        self.highlight_button.setCheckable(True)
        self.highlight_button.toggled.connect(self.highlight_toggled)
        self.pick_button = QToolButton()
        self.pick_button.setObjectName("chipButton")
        self.pick_button.setCheckable(True)
        self.pick_button.setIcon(make_icon(IconName.PIPETTE, size=Iconography.sm))
        self.pick_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.pick_button.toggled.connect(self.pick_toggled)
        top.addWidget(self.enabled_check)
        top.addStretch(1)
        top.addWidget(self.invert_check)
        top.addWidget(self.pick_button)
        top.addWidget(self.highlight_button)
        root.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(Spacing.sm)
        grid.setVerticalSpacing(Spacing.xs)
        for column in (1, 3, 5):
            grid.setColumnStretch(column, 1)
        self.channel_checks: dict[str, QCheckBox] = {}
        self.bars: dict[str, RangeBar] = {}
        self.spins: dict[str, QDoubleSpinBox] = {}
        self.field_labels: dict[str, QLabel] = {}
        for row, channel in enumerate(_CHANNELS):
            check = QCheckBox()
            check.toggled.connect(lambda checked, name=channel: self._edit(**{f"use_{name}": bool(checked)}))
            bar = RangeBar(channel)
            bar.dragged.connect(self._on_dragged)
            self.channel_checks[channel], self.bars[channel] = check, bar
            grid.addWidget(check, 3 * row, 0, 1, 6)
            grid.addWidget(bar, 3 * row + 1, 0, 1, 6)
            for column, (name, low, high, suffix) in enumerate(_FIELDS[channel]):
                label = QLabel()
                set_role(label, "label-secondary")
                spin = QDoubleSpinBox()
                spin.setRange(low, high)
                spin.setDecimals(0)
                spin.setSuffix(suffix)
                spin.setMinimumWidth(64)
                spin.setKeyboardTracking(False)
                spin.valueChanged.connect(lambda value, field=name: self._edit(**{field: _stored(field, value)}))
                self.field_labels[name], self.spins[name] = label, spin
                grid.addWidget(label, 3 * row + 2, 2 * column)
                grid.addWidget(spin, 3 * row + 2, 2 * column + 1)
        root.addLayout(grid)
        root.addStretch(1)
        self.retranslate()
        self.set_qualifier(None)

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_qualifier(self, qualifier: Qualifier | None) -> None:
        """Affiche le qualifieur du nœud courant (``None`` : le nœud corrige toute l'image), sans émettre."""
        self._qualifier = qualifier
        shown = qualifier or Qualifier(enabled=False)
        self._updating = True
        try:
            self.enabled_check.setChecked(qualifier is not None and qualifier.enabled)
            self.invert_check.setChecked(shown.invert)
            for channel in _CHANNELS:
                self.channel_checks[channel].setChecked(getattr(shown, f"use_{channel}"))
                self.bars[channel].set_qualifier(shown)
                for name, *_rest in _FIELDS[channel]:
                    self.spins[name].setValue(_shown(name, getattr(shown, name)))
        finally:
            self._updating = False
        self._sync_enabled()

    def set_picking(self, picking: bool) -> None:
        """État du bouton *Pipette* (la fenêtre l'arrête après une prise), sans émettre."""
        self.pick_button.blockSignals(True)
        self.pick_button.setChecked(picking)
        self.pick_button.blockSignals(False)

    def _sync_enabled(self) -> None:
        active = self._qualifier is not None and self._qualifier.enabled
        for widget in (self.invert_check, *self.channel_checks.values(), *self.bars.values(), *self.spins.values()):
            widget.setEnabled(active)

    def retranslate(self) -> None:
        self.enabled_check.setText(translate("color.qualifier.enable"))
        self.invert_check.setText(translate("color.qualifier.invert"))
        self.highlight_button.setText(translate("color.qualifier.highlight"))
        self.pick_button.setText(translate("color.qualifier.pick"))
        self.pick_button.setToolTip(translate("color.qualifier.pick_tip"))
        self.highlight_button.setToolTip(translate("color.qualifier.highlight_tip"))
        for channel in _CHANNELS:
            self.channel_checks[channel].setText(translate(f"color.qualifier.{channel}"))
            for name, *_rest in _FIELDS[channel]:
                self.field_labels[name].setText(translate(f"color.qualifier.{name.split('_')[1]}"))

    # -- réglages -----------------------------------------------------------------------------------------------

    def _on_enabled(self, checked: bool) -> None:
        if self._updating:
            return
        base = self._qualifier or Qualifier()
        self._emit(replace(base, enabled=bool(checked)))

    def _on_dragged(self, qualifier: Qualifier) -> None:
        if not self._updating:
            self._emit(qualifier)

    def _edit(self, **changes) -> None:
        if self._updating or self._qualifier is None:
            return
        values = {**self._qualifier.to_dict(), **changes}
        for low, high in (("sat_low", "sat_high"), ("lum_low", "lum_high")):
            if values[low] > values[high]:                       # une borne poussée au-delà de l'autre l'entraîne
                values[high if low in changes else low] = values[low if low in changes else high]
        self._emit(Qualifier(**values))

    def _emit(self, qualifier: Qualifier) -> None:
        if qualifier == self._qualifier:
            return
        self.set_qualifier(qualifier)
        self.changed.emit(qualifier)
