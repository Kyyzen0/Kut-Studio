"""Réglages rapides sous les roues : Exposition, Contraste, Saturation, Température, une ligne chacun.

Ce sont les **mêmes** réglages que ceux de l'inspecteur (``ColorGrade.exposure``…) : une seconde série de contrôles, neufs, liée au
même modèle. Un changement de ligne émet :attr:`QuickSettings.changed` ; la fenêtre l'envoie à
``on_color_grade_field_changed``, le chemin du champ de l'inspecteur (même pile d'annulation, même regroupement des rafales,
même règle « animé : image clé de la tête », mêmes refus de verrou). Dans l'autre sens, la fenêtre reflète la valeur du modèle
avec :meth:`QuickSettings.set_value`, qui n'émet jamais (pas de boucle, pas d'étape d'historique en écho).

Chaque ligne : libellé, curseur (absent si la largeur manque), champ numérique, losange d'images clés (ADR-0008). Le losange pose ou retire l'image clé sous la
tête ; un clic droit propose l'éditeur de courbes et le retrait de l'animation. Les flèches ‹ › restent à l'inspecteur : la ligne
doit tenir en 30 px sur une colonne de 514 px.

La place est comptée par :class:`~ui.color_page.wheels.ColorWheels` (sous les roues, jamais à leur place) : une ligne chacun si la
hauteur le permet, sinon deux par ligne (sans curseur si la largeur manque), sinon rien (:meth:`required`).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QSlider, QWidget

from core.color_grading import ColorGrade
from core.grade_keyframing import GradeAnimationInfo, KeyState
from core.grade_targets import scalar_property_id
from ui import i18n
from ui.design_system import Sizes, Spacing
from ui.properties_widgets.diamond_button import KeyframeDiamondButton
from ui.properties_widgets.keyframe_row import set_diamond_quietly

#: (champ, minimum, maximum, pas du champ, suffixe, pas du curseur) : les bornes de l'inspecteur (``ColorMixin._build_color_group``).
QUICK_FIELDS: tuple[tuple[str, float, float, float, str, float], ...] = (
    ("exposure", -2.0, 2.0, 0.05, " EV", 0.01),
    ("contrast", -1.0, 1.0, 0.05, "", 0.01),
    ("saturation", 0.0, 2.0, 0.05, "×", 0.01),
    ("temperature", -100.0, 100.0, 1.0, "", 1.0),
)
ROW_HEIGHT = Sizes.control_min
_SLIDER_MIN = 40


class QuickRow(QWidget):
    """Une ligne : libellé, curseur, champ, losange. :attr:`changed` (champ, valeur) à chaque saisie de l'utilisateur."""

    changed = Signal(str, float)
    keys_toggled = Signal(str)
    graph_requested = Signal(str)
    cleared = Signal()

    def __init__(self, name: str, minimum: float, maximum: float, step: float, suffix: str, unit: float) -> None:
        super().__init__()
        self.name = name
        self.setObjectName(f"color_quick_{name}")
        self.minimum, self.unit = minimum, unit
        self._has_keys = False
        self._animatable = True
        self.setMinimumHeight(ROW_HEIGHT)
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(Spacing.xs)
        self.caption = QLabel()
        self.caption.setMinimumHeight(ROW_HEIGHT)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, round((maximum - minimum) / unit))
        self.slider.setMinimumWidth(_SLIDER_MIN)
        self.slider.setMinimumHeight(ROW_HEIGHT)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(2)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)           # le curseur règle, le champ précise : sans flèches, la ligne tient dans 250 px
        self.spin.setFixedHeight(ROW_HEIGHT)
        self.spin.setStyleSheet("QDoubleSpinBox { padding: 0px 6px; min-height: 0px; }")        # le thème donne 43 px au champ de l'inspecteur
        self.spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.diamond = KeyframeDiamondButton(scalar_property_id(name))
        self.diamond.clicked.connect(lambda _checked=False: self.keys_toggled.emit(self.name))
        self.diamond.setContextMenuPolicy(Qt.CustomContextMenu)
        self.diamond.customContextMenuRequested.connect(self._open_menu)
        for widget in (self.caption, self.slider, self.spin, self.diamond):
            line.addWidget(widget, 1 if widget is self.slider else 0)
        self._line = line
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)
        self.value = getattr(ColorGrade.identity(), name)
        self.set_value(self.value)
        self.retranslate()

    def set_slider_shown(self, shown: bool) -> None:
        """Sans curseur (colonne étroite, polices larges) : le libellé prend la place et le champ reste à droite, près du losange."""
        self.slider.setVisible(shown)
        self._line.setStretchFactor(self.caption, 0 if shown else 1)

    def cell_width(self, *, slider: bool) -> int:
        """Largeur minimale de la ligne : libellé, champ et losange, plus le curseur à ``_SLIDER_MIN`` px s'il est montré."""
        parts = [self.caption.sizeHint().width(), self.spin.sizeHint().width(), self.diamond.width()] + ([_SLIDER_MIN] if slider else [])
        return sum(parts) + (len(parts) - 1) * self._line.spacing()

    # -- modèle -> ligne ----------------------------------------------------------------------------------------

    def set_value(self, value: float) -> None:
        """Affiche ``value`` sans rien émettre (le modèle a changé ailleurs). Ignoré pendant que le curseur est tenu."""
        if self.slider.isSliderDown():
            return
        self.value = float(value)
        if abs(self.spin.value() - value) < 5e-4 and self.slider.value() == round((value - self.minimum) / self.unit):
            return                                    # déjà affiché : ne pas réécrire le texte que l'utilisateur est en train de taper
        for widget in (self.spin, self.slider):
            widget.blockSignals(True)
        try:
            self.spin.setValue(value)
            self.slider.setValue(round((value - self.minimum) / self.unit))
        finally:
            for widget in (self.spin, self.slider):
                widget.blockSignals(False)

    def set_state(self, state: KeyState | None, *, animatable: bool) -> None:
        """Losange plein (image clé sous la tête), contour (animé, entre-deux), grisé si l'étalonnage est découpé en nœuds."""
        state = state or KeyState()
        self._has_keys = state.animated
        self._animatable = animatable
        set_diamond_quietly(self.diamond, state.keyed_all, state.animated and animatable)
        self.diamond.setEnabled(animatable)
        self._tooltip(animatable)

    def retranslate(self) -> None:
        label = i18n.translate(f"color.property.{self.name}")
        self.caption.setText(label)
        for widget in (self.spin, self.slider):
            widget.setAccessibleName(label)
        self.diamond.setAccessibleName(i18n.translate("a11y.keyframe.toggle", property=label))
        self._tooltip(self._animatable)

    def _tooltip(self, animatable: bool) -> None:
        label = i18n.translate(f"color.property.{self.name}")
        self.diamond.setToolTip(i18n.translate("animation.diamond.tooltip", name=label) if animatable
                                else i18n.translate("color.animation.disabled_tip"))

    # -- ligne -> modèle ----------------------------------------------------------------------------------------

    def _slider_moved(self, position: int) -> None:
        self.spin.setValue(round(self.minimum + position * self.unit, 4))          # le champ émet : un seul chemin vers le modèle

    def _spin_changed(self, value: float) -> None:
        self.value = float(value)
        self.slider.blockSignals(True)
        self.slider.setValue(round((value - self.minimum) / self.unit))
        self.slider.blockSignals(False)
        self.changed.emit(self.name, float(value))

    def _open_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        graph = QAction(i18n.translate("animation.menu.graph"), menu)
        graph.triggered.connect(lambda: self.graph_requested.emit(self.name))
        menu.addAction(graph)
        clear = QAction(i18n.translate("animation.menu.clear_grade"), menu)
        clear.setEnabled(self._has_keys)
        clear.triggered.connect(self.cleared)
        menu.addAction(clear)
        menu.exec(self.diamond.mapToGlobal(position))


class QuickSettings(QWidget):
    """Les quatre lignes, en colonne ou deux par ligne. Ne décide pas de sa place : :meth:`required` la chiffre, la page choisit."""

    changed = Signal(str, float)
    keys_toggled = Signal(str)
    graph_requested = Signal(str)
    animation_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_quick_settings")
        self.rows: dict[str, QuickRow] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, 0)          # l'air entre le losange de la roue et la première ligne
        self._grid.setHorizontalSpacing(Spacing.md)
        self._grid.setVerticalSpacing(Spacing.xs)
        for name, minimum, maximum, step, suffix, unit in QUICK_FIELDS:
            row = QuickRow(name, minimum, maximum, step, suffix, unit)
            row.changed.connect(self.changed)
            row.keys_toggled.connect(self.keys_toggled)
            row.graph_requested.connect(self.graph_requested)
            row.cleared.connect(self.animation_cleared)
            self.rows[name] = row
        self._columns = 0
        self._sliders = True
        self._equalize()
        self.arrange(1)

    # -- disposition --------------------------------------------------------------------------------------------

    def _equalize(self) -> None:
        """Libellés et champs ont la largeur du plus large : curseurs et champs alignés d'une ligne à l'autre. Relu à chaque mesure
        (:meth:`required`) : la police ou la langue ont pu changer depuis la construction (``sizeHint`` ne dépend pas du minimum)."""
        widest = max(row.caption.sizeHint().width() for row in self.rows.values())
        field = max(row.spin.sizeHint().width() for row in self.rows.values())
        for row in self.rows.values():
            row.caption.setMinimumWidth(widest)
            row.spin.setMinimumWidth(field)

    def arrange(self, columns: int, *, sliders: bool = True) -> None:
        """``columns`` colonnes ; ``sliders`` faux : lignes sans curseur (libellé, champ, losange) quand la largeur manque."""
        if sliders != self._sliders:
            self._sliders = sliders
            for row in self.rows.values():
                row.set_slider_shown(sliders)
        if columns == self._columns:
            return
        self._columns = columns
        for row in self.rows.values():
            self._grid.removeWidget(row)
        for index, row in enumerate(self.rows.values()):
            self._grid.addWidget(row, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    @property
    def columns(self) -> int:
        return self._columns

    def row_height(self) -> int:
        return max([ROW_HEIGHT, *(row.minimumSizeHint().height() for row in self.rows.values())])

    def required(self, columns: int, *, sliders: bool = True) -> tuple[int, int]:
        """(largeur, hauteur) minimales pour ``columns`` colonnes : les lignes à la taille de leurs contrôles, jamais rognées."""
        self._equalize()
        count = len(self.rows)
        lines = -(-count // columns)
        margins = self._grid.contentsMargins()
        width = columns * max(row.cell_width(slider=sliders) for row in self.rows.values())
        width += (columns - 1) * self._grid.horizontalSpacing() + margins.left() + margins.right()
        return width, margins.top() + lines * self.row_height() + (lines - 1) * self._grid.verticalSpacing()

    # -- modèle -> lignes ---------------------------------------------------------------------------------------

    def set_values(self, values: dict[str, float]) -> None:
        for name, row in self.rows.items():
            row.set_value(values.get(name, getattr(ColorGrade.identity(), name)))

    def set_value(self, name: str, value: float) -> None:
        row = self.rows.get(name)
        if row is not None:
            row.set_value(value)

    def set_animation(self, info: GradeAnimationInfo | None) -> None:
        """Losanges et valeurs évaluées à la tête de lecture (``None`` : pas de clip, rien d'animé)."""
        animatable = info is None or info.animatable
        for name, row in self.rows.items():
            row.set_state(info.fields.get(name) if info is not None else None, animatable=animatable)
            if info is not None and name in info.scalars:
                row.set_value(info.scalars[name])

    def retranslate(self) -> None:
        for row in self.rows.values():
            row.retranslate()
        self._equalize()


__all__ = ["QUICK_FIELDS", "QuickRow", "QuickSettings"]
