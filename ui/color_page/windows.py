"""Fenêtres, flou et netteté du nœud courant.

Une **fenêtre** est une forme de masque (rectangle, ellipse, forme libre) posée sur l'image du clip : elle limite la
correction du nœud, avec son qualifieur (la clé est leur produit). On l'ajoute ici ; on la déplace, l'agrandit et la
tourne dans le viewer (poignées), et ses valeurs exactes sont dans les champs. Plusieurs fenêtres se combinent comme
les masques (ajouter, soustraire, intersection) ; la douceur adoucit le bord ; *Inverser* corrige l'extérieur. Pour
qu'une fenêtre suive un objet : panneau Tracking, *Lier à*, la fenêtre.

Le **flou** et la **netteté** agissent sur la correction du nœud, là où sa clé la sélectionne : une fenêtre inversée
floute l'arrière-plan.

:attr:`WindowsEditor.changed` (les fenêtres du nœud, la clé d'historique) et :attr:`DetailEditor.changed` (flou,
netteté) à chaque réglage : la fenêtre regroupe une rafale en une étape d'historique, comme pour les roues.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.color_nodes import MAX_BLUR, MAX_SHARPEN
from core.compositing import Mask, MaskMode, MaskShape
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName, make_icon
from ui.theme import set_role

_SHAPES = ((MaskShape.RECTANGLE, IconName.WINDOW_RECT), (MaskShape.ELLIPSE, IconName.WINDOW_ELLIPSE),
           (MaskShape.POLYGON, IconName.WINDOW_POLYGON))
_FIELDS = (("position_x", -100.0, 200.0, "%"), ("position_y", -100.0, 200.0, "%"), ("width", 0.0, 400.0, "%"),
           ("height", 0.0, 400.0, "%"), ("rotation", -360.0, 360.0, "°"), ("feather", 0.0, 100.0, "%"))
DEFAULT_SIZE = 0.4
DEFAULT_FEATHER = 0.03


def _shown(name: str, value: float) -> float:
    return value if name == "rotation" else value * 100.0


def _stored(name: str, value: float) -> float:
    return value if name == "rotation" else value / 100.0


def window_label(window: Mask, index: int) -> str:
    text = window.name or translate("color.window.name", index=index + 1)
    text = f"{text} · {translate(f'color.window.shape.{window.shape.value}')}"
    return f"{text} · {translate('color.window.inverted')}" if window.inverted else text


class WindowsEditor(QWidget):
    """Les fenêtres du nœud courant ; :attr:`selected` : la fenêtre que le viewer édite ("" : aucune)."""

    changed = Signal(object, str)                     # (fenêtres du nœud, clé d'historique)
    selected = Signal(str)
    highlight_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_windows")
        self._windows: tuple[Mask, ...] = ()
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        root.setSpacing(Spacing.sm)

        top = QHBoxLayout()
        self.add_buttons: dict[MaskShape, QToolButton] = {}
        for shape, icon in _SHAPES:
            button = QToolButton()
            button.setObjectName("chipButton")
            button.setIcon(make_icon(icon, size=Iconography.sm))
            button.clicked.connect(lambda _checked=False, value=shape: self._add(value))
            self.add_buttons[shape] = button
            top.addWidget(button)
        top.addStretch(1)
        self.remove_button = IconButton(icon=IconName.TRASH, tooltip=translate("color.window.remove"),
                                        size=Sizes.icon_button_sm)
        self.remove_button.clicked.connect(self._remove)
        self.highlight_button = QToolButton()
        self.highlight_button.setObjectName("chipButton")
        self.highlight_button.setCheckable(True)
        self.highlight_button.toggled.connect(self.highlight_toggled)
        top.addWidget(self.remove_button)
        top.addWidget(self.highlight_button)
        root.addLayout(top)

        self.list = QListWidget()
        self.list.setObjectName("color_windows_list")
        self.list.setMaximumHeight(64)
        self.list.currentRowChanged.connect(self._on_row)
        root.addWidget(self.list)
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        set_role(self.empty, "label-secondary")
        root.addWidget(self.empty)

        self.form_host = QWidget()
        form = QGridLayout(self.form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(Spacing.sm)
        form.setVerticalSpacing(Spacing.xs)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        options = QHBoxLayout()
        self.invert_check = QCheckBox()
        self.invert_check.toggled.connect(lambda checked: self._edit(inverted=bool(checked)))
        self.mode_combo = QComboBox()
        for mode in MaskMode:
            self.mode_combo.addItem("", mode.value)
        self.mode_combo.currentIndexChanged.connect(
            lambda _index: self._edit(mode=MaskMode(self.mode_combo.currentData())))
        options.addWidget(self.invert_check)
        options.addStretch(1)
        options.addWidget(self.mode_combo)
        form.addLayout(options, 0, 0, 1, 4)
        self.field_labels: dict[str, QLabel] = {}
        self.spins: dict[str, QDoubleSpinBox] = {}
        for index, (name, low, high, suffix) in enumerate(_FIELDS):    # deux champs par ligne : X / Y, L / H, …
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(1)
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(72)
            spin.valueChanged.connect(lambda value, field=name: self._edit(**{field: _stored(field, value)}))
            label = QLabel()
            set_role(label, "label-secondary")
            self.field_labels[name], self.spins[name] = label, spin
            form.addWidget(label, 1 + index // 2, 2 * (index % 2))
            form.addWidget(spin, 1 + index // 2, 2 * (index % 2) + 1)
        root.addWidget(self.form_host)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        set_role(self.hint, "label-secondary")
        root.addWidget(self.hint)
        root.addStretch(1)
        self.retranslate()
        self.set_windows(())

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_windows(self, windows, current_id: str | None = None) -> None:
        """Les fenêtres du nœud courant, sans émettre ; garde la fenêtre choisie si elle existe encore."""
        windows = tuple(windows or ())
        current = current_id if current_id is not None else self.current_id()
        self._windows = windows
        self._updating = True
        try:
            self.list.clear()
            for index, window in enumerate(windows):
                item = QListWidgetItem(window_label(window, index), self.list)
                item.setData(Qt.UserRole, window.id)
            ids = [window.id for window in windows]
            row = ids.index(current) if current in ids else (len(ids) - 1 if ids else -1)
            self.list.setCurrentRow(row)
        finally:
            self._updating = False
        self._load()

    def current_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def current_window(self) -> Mask | None:
        current = self.current_id()
        return next((window for window in self._windows if window.id == current), None)

    def set_highlight(self, shown: bool) -> None:
        self.highlight_button.blockSignals(True)
        self.highlight_button.setChecked(shown)
        self.highlight_button.blockSignals(False)

    def _load(self) -> None:
        window = self.current_window()
        self.form_host.setVisible(window is not None)
        self.list.setVisible(bool(self._windows))
        self.empty.setVisible(not self._windows)
        self.remove_button.setEnabled(window is not None)
        self.mode_combo.setVisible(len(self._windows) > 1)
        if window is None:
            return
        self._updating = True
        try:
            self.invert_check.setChecked(window.inverted)
            self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(window.mode.value)))
            for name, *_rest in _FIELDS:
                self.spins[name].setValue(_shown(name, getattr(window, name)))
        finally:
            self._updating = False

    def retranslate(self) -> None:
        for shape, _icon in _SHAPES:
            button = self.add_buttons[shape]
            button.setToolTip(translate(f"color.window.add_{shape.value}"))
            button.setAccessibleName(translate(f"color.window.add_{shape.value}"))
        self.remove_button.setToolTip(translate("color.window.remove"))
        self.highlight_button.setText(translate("color.qualifier.highlight"))
        self.highlight_button.setToolTip(translate("color.window.highlight_tip"))
        self.invert_check.setText(translate("color.window.invert"))
        self.mode_combo.setToolTip(translate("color.window.operation"))
        for index, mode in enumerate(MaskMode):
            self.mode_combo.setItemText(index, translate(f"color.window.mode.{mode.value}"))
        for name, *_rest in _FIELDS:
            self.field_labels[name].setText(translate(f"color.window.{name}"))
        self.empty.setText(translate("color.window.empty"))
        self.hint.setText(translate("color.window.hint"))
        for index in range(self.list.count()):
            self.list.item(index).setText(window_label(self._windows[index], index))

    # -- réglages -----------------------------------------------------------------------------------------------

    def _on_row(self, _row: int) -> None:
        if self._updating:
            return
        self._load()
        self.selected.emit(self.current_id())

    def _add(self, shape: MaskShape) -> None:
        window = Mask(shape=shape, width=DEFAULT_SIZE, height=DEFAULT_SIZE, feather=DEFAULT_FEATHER)
        windows = self._windows + (window,)
        self.set_windows(windows, window.id)
        self.changed.emit(windows, "history.color.window_add")
        self.selected.emit(window.id)

    def _remove(self) -> None:
        current = self.current_id()
        windows = tuple(window for window in self._windows if window.id != current)
        if windows == self._windows:
            return
        self.set_windows(windows, "")
        self.changed.emit(windows, "history.color.window_remove")
        self.selected.emit(self.current_id())

    def _edit(self, **changes) -> None:
        window = self.current_window()
        if self._updating or window is None:
            return
        updated = replace(window, **changes, id=window.id)
        if updated == window:
            return
        windows = tuple(updated if item.id == window.id else item for item in self._windows)
        self.set_windows(windows, window.id)
        self.changed.emit(windows, "history.color.window")


class DetailEditor(QWidget):
    """Flou et netteté du nœud courant (:attr:`changed` : flou σ en pixels de la séquence, force de la netteté)."""

    changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("color_detail")
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        root.setSpacing(Spacing.sm)
        form = QFormLayout()
        form.setSpacing(Spacing.xs)
        self.labels: dict[str, QLabel] = {}
        self.sliders: dict[str, QSlider] = {}
        self.spins: dict[str, QDoubleSpinBox] = {}
        for name, high, step, suffix in (("blur", MAX_BLUR, 0.5, " px"), ("sharpen", MAX_SHARPEN, 0.05, "")):
            row = QHBoxLayout()
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, int(round(high / step)))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, high)
            spin.setSingleStep(step)
            spin.setDecimals(2)
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            spin.setFixedWidth(104)
            slider.valueChanged.connect(lambda value, s=spin, k=step: s.setValue(value * k))
            spin.valueChanged.connect(lambda value, sl=slider, k=step: self._on_value(sl, value, k))
            row.addWidget(slider, 1)
            row.addWidget(spin)
            label = QLabel()
            self.labels[name], self.sliders[name], self.spins[name] = label, slider, spin
            form.addRow(label, row)
        root.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        set_role(self.hint, "label-secondary")
        root.addWidget(self.hint)
        root.addStretch(1)
        self.retranslate()

    def set_detail(self, blur: float, sharpen: float) -> None:
        """Valeurs du nœud courant, sans émettre."""
        self._updating = True
        try:
            for name, value, step in (("blur", blur, 0.5), ("sharpen", sharpen, 0.05)):
                self.spins[name].setValue(value)
                self.sliders[name].setValue(int(round(value / step)))
        finally:
            self._updating = False

    def retranslate(self) -> None:
        for name in ("blur", "sharpen"):
            self.labels[name].setText(translate(f"color.detail.{name}"))
        self.hint.setText(translate("color.detail.hint"))

    def _on_value(self, slider: QSlider, value: float, step: float) -> None:
        slider.blockSignals(True)
        slider.setValue(int(round(value / step)))
        slider.blockSignals(False)
        if not self._updating:
            self.changed.emit(float(self.spins["blur"].value()), float(self.spins["sharpen"].value()))
