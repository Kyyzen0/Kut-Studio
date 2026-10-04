"""Panneau « Suivi » de l'inspecteur : trackers, analyse, liaisons, stabilisation.

Vue passive : la fenêtre (:mod:`ui.main_window_mixins.tracking`) lui donne
un état complet (:meth:`TrackingPanel.set_state`) et traduit ses signaux en
opérations de :mod:`core.tracking_ops`, une entrée d'historique par action.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.tracking_model import AdaptMode, BorderMode, Precision, Smoothing, StabilizationMode
from core.tracking_panel_state import TrackingPanelState
from ui import i18n
from ui.adaptive_layout import FlowLayout, WrappingCheckBox, allow_shrinking, make_shrinkable
from ui.design_system import Spacing
from ui.theme import COLORS


def _swatch(color: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def _tr(key: str, **values) -> str:
    return i18n.translate(key, **values)


class TrackingPanel(QGroupBox):
    """Trackers du clip sélectionné et ce qu'on peut en faire."""

    tracker_selection_changed = Signal(list)
    add_tracker_requested = Signal()
    remove_trackers_requested = Signal(list)
    rename_tracker_requested = Signal(str, str)
    tracker_visibility_changed = Signal(str, bool)
    reset_requested = Signal(str)
    track_requested = Signal(int)
    stop_requested = Signal()
    settings_changed = Signal(dict)
    show_paths_changed = Signal(bool)
    link_requested = Signal(dict, bool)
    link_enabled_changed = Signal(str, bool)
    link_bake_requested = Signal(str)
    link_remove_requested = Signal(str)
    stabilization_changed = Signal(dict)
    stabilization_toggled = Signal(bool)
    auto_stabilize_requested = Signal()

    def __init__(self, group_style: str = "", parent=None) -> None:
        super().__init__(_tr("tracking.title"), parent)
        self.setObjectName("tracking_group")
        if group_style:
            self.setStyleSheet(group_style)
        self._updating = False
        self._state: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.sm)

        self.message = QLabel(objectName="tracking_message")
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {COLORS['muted']};")
        root.addWidget(self.message)

        # --- Trackers -------------------------------------------------------------------------
        self.tracker_box = QWidget()
        tracker_layout = QVBoxLayout(self.tracker_box)
        tracker_layout.setContentsMargins(0, 0, 0, 0)
        tracker_layout.setSpacing(Spacing.xs)
        self.tracker_list = QListWidget(objectName="tracker_list")
        self.tracker_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tracker_list.itemSelectionChanged.connect(self._on_selection)
        self.tracker_list.itemChanged.connect(self._on_item_changed)
        self.tracker_list.itemDoubleClicked.connect(lambda item: self.tracker_list.editItem(item))
        tracker_layout.addWidget(self.tracker_list)
        # Les rangées de boutons passent à la ligne : à 280 px d'inspecteur, trois boutons côte à côte dépassent.
        row = FlowLayout(spacing=Spacing.xs)
        self.add_button = QPushButton(_tr("tracking.add"), objectName="tracker_add")
        self.add_button.setToolTip(_tr("tracking.add.tip"))
        self.add_button.clicked.connect(self.add_tracker_requested.emit)
        self.remove_button = QPushButton(_tr("tracking.remove"), objectName="tracker_remove")
        self.remove_button.clicked.connect(lambda: self.remove_trackers_requested.emit(self.selected_ids()))
        self.reset_button = QToolButton(objectName="tracker_reset")
        self.reset_button.setText(_tr("tracking.reset"))
        self.reset_button.setPopupMode(QToolButton.InstantPopup)
        reset_menu = QMenu(self.reset_button)
        for mode in ("all", "after", "before"):
            action = reset_menu.addAction(_tr(f"tracking.reset.{mode}"))
            action.triggered.connect(lambda _checked=False, m=mode: self.reset_requested.emit(m))
        self.reset_button.setMenu(reset_menu)
        for widget in (self.add_button, self.remove_button, self.reset_button):
            row.addWidget(widget)
        tracker_layout.addLayout(row)
        self.summary = QLabel(objectName="tracker_summary")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {COLORS['muted']};")
        tracker_layout.addWidget(self.summary)

        # --- Analyse --------------------------------------------------------------------------
        controls = FlowLayout(spacing=Spacing.xs)
        self.backward_button = QPushButton("◀◀ " + _tr("tracking.backward"), objectName="track_backward")
        self.stop_button = QPushButton("■ " + _tr("tracking.stop"), objectName="track_stop")
        self.forward_button = QPushButton(_tr("tracking.forward") + " ▶▶", objectName="track_forward")
        self.backward_button.clicked.connect(lambda: self.track_requested.emit(-1))
        self.forward_button.clicked.connect(lambda: self.track_requested.emit(1))
        self.stop_button.clicked.connect(self.stop_requested.emit)
        for widget in (self.backward_button, self.stop_button, self.forward_button):
            controls.addWidget(widget)
        tracker_layout.addLayout(controls)
        self.progress = QProgressBar(objectName="tracking_progress")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        tracker_layout.addWidget(self.progress)
        self.status = QLabel(objectName="tracking_status")
        self.status.setWordWrap(True)
        tracker_layout.addWidget(self.status)
        root.addWidget(self.tracker_box)

        # --- Zones et réglages ---------------------------------------------------------------
        self.zones_box = QGroupBox(_tr("tracking.zones"))
        zones = QFormLayout(self.zones_box)
        zones.setRowWrapPolicy(QFormLayout.WrapLongRows)  # libellé au-dessus du champ quand la ligne est trop longue
        self.pattern_w, self.pattern_h = self._size_pair(zones, "tracking.pattern", "pattern")
        self.search_w, self.search_h = self._size_pair(zones, "tracking.search", "search")
        self.min_confidence = QDoubleSpinBox(objectName="tracker_min_confidence")
        self.min_confidence.setRange(0.05, 0.95)
        self.min_confidence.setSingleStep(0.05)
        self.min_confidence.valueChanged.connect(lambda v: self._emit_settings(min_confidence=float(v)))
        zones.addRow(_tr("tracking.min_confidence"), self.min_confidence)
        self.adapt = make_shrinkable(QComboBox(objectName="tracker_adapt"))
        for mode in AdaptMode.ALL:
            self.adapt.addItem(_tr(f"tracking.adapt.{mode}"), mode)
        self.adapt.currentIndexChanged.connect(lambda _i: self._emit_settings(adapt=self.adapt.currentData()))
        zones.addRow(_tr("tracking.adapt"), self.adapt)
        self.precision = make_shrinkable(QComboBox(objectName="tracker_precision"))
        for value in Precision.ALL:
            self.precision.addItem(_tr(f"tracking.precision.{value}"), value)
        self.precision.currentIndexChanged.connect(
            lambda _i: self._emit_settings(precision=self.precision.currentData())
        )
        zones.addRow(_tr("tracking.precision"), self.precision)
        self.stop_on_loss = WrappingCheckBox(_tr("tracking.stop_on_loss"))
        self.stop_on_loss.setObjectName("tracker_stop_on_loss")
        self.stop_on_loss.toggled.connect(lambda v: self._emit_settings(stop_on_loss=bool(v)))
        zones.addRow(self.stop_on_loss)
        self.use_proxy = WrappingCheckBox(_tr("tracking.use_proxy"))
        self.use_proxy.setObjectName("tracker_use_proxy")
        zones.addRow(self.use_proxy)
        self.show_paths = WrappingCheckBox(_tr("tracking.show_paths"))
        self.show_paths.setObjectName("tracker_show_paths")
        self.show_paths.setChecked(True)
        self.show_paths.toggled.connect(lambda v: not self._updating and self.show_paths_changed.emit(bool(v)))
        zones.addRow(self.show_paths)
        root.addWidget(self.zones_box)

        # --- Appliquer ------------------------------------------------------------------------
        self.apply_box = QGroupBox(_tr("tracking.apply"))
        apply_layout = QVBoxLayout(self.apply_box)
        self.target = make_shrinkable(QComboBox(objectName="tracking_target"))
        apply_layout.addWidget(self.target)
        components = FlowLayout(spacing=Spacing.md)
        self.apply_position = QCheckBox(_tr("tracking.component.position"), objectName="apply_position")
        self.apply_position.setChecked(True)
        self.apply_rotation = QCheckBox(_tr("tracking.component.rotation"), objectName="apply_rotation")
        self.apply_scale = QCheckBox(_tr("tracking.component.scale"), objectName="apply_scale")
        for widget in (self.apply_position, self.apply_rotation, self.apply_scale):
            components.addWidget(widget)
        apply_layout.addLayout(components)
        self.multi_hint = QLabel(_tr("tracking.multi_hint"))
        self.multi_hint.setWordWrap(True)
        self.multi_hint.setStyleSheet(f"color: {COLORS['muted']};")
        apply_layout.addWidget(self.multi_hint)
        buttons = FlowLayout(spacing=Spacing.xs)
        self.link_button = QPushButton(_tr("tracking.link"), objectName="tracking_link")
        self.link_button.setToolTip(_tr("tracking.link.tip"))
        self.bake_button = QPushButton(_tr("tracking.bake"), objectName="tracking_bake")
        self.bake_button.setToolTip(_tr("tracking.bake.tip"))
        allow_shrinking(self.bake_button, 120)  # le libellé le plus long de l'onglet : dernier recours, il est alors coupé
        self.link_button.clicked.connect(lambda: self._emit_link(False))
        self.bake_button.clicked.connect(lambda: self._emit_link(True))
        buttons.addWidget(self.link_button)
        buttons.addWidget(self.bake_button)
        apply_layout.addLayout(buttons)
        root.addWidget(self.apply_box)

        # --- Liaisons reçues ------------------------------------------------------------------
        self.links_box = QGroupBox(_tr("tracking.links"))
        links_layout = QVBoxLayout(self.links_box)
        self.links_list = QListWidget(objectName="tracking_links")
        self.links_list.setMaximumHeight(90)
        self.links_list.itemChanged.connect(self._on_link_item_changed)
        links_layout.addWidget(self.links_list)
        link_buttons = FlowLayout(spacing=Spacing.xs)
        self.freeze_button = QPushButton(_tr("tracking.link.freeze"), objectName="tracking_link_bake")
        self.unlink_button = QPushButton(_tr("tracking.link.unlink"), objectName="tracking_unlink")
        self.freeze_button.clicked.connect(lambda: self._emit_for_link(self.link_bake_requested))
        self.unlink_button.clicked.connect(lambda: self._emit_for_link(self.link_remove_requested))
        link_buttons.addWidget(self.freeze_button)
        link_buttons.addWidget(self.unlink_button)
        links_layout.addLayout(link_buttons)
        root.addWidget(self.links_box)

        # --- Stabilisation --------------------------------------------------------------------
        self.stab_box = QGroupBox(_tr("tracking.stabilization"))
        stab = QFormLayout(self.stab_box)
        stab.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.stab_enabled = WrappingCheckBox(_tr("tracking.stab.enable"))
        self.stab_enabled.setObjectName("stab_enabled")
        self.stab_enabled.toggled.connect(lambda v: not self._updating and self.stabilization_toggled.emit(bool(v)))
        stab.addRow(self.stab_enabled)
        self.stab_mode = make_shrinkable(QComboBox(objectName="stab_mode"))
        for mode in StabilizationMode.ALL:
            self.stab_mode.addItem(_tr(f"tracking.stab.mode.{mode}"), mode)
        self.stab_mode.currentIndexChanged.connect(lambda _i: self._emit_stab(mode=self.stab_mode.currentData()))
        stab.addRow(_tr("tracking.stab.mode"), self.stab_mode)
        smoothing_row = QHBoxLayout()
        self.stab_smoothing = make_shrinkable(QComboBox(objectName="stab_smoothing"))
        for value in (Smoothing.LOW, Smoothing.MEDIUM, Smoothing.HIGH, Smoothing.CUSTOM, Smoothing.LOCKED):
            self.stab_smoothing.addItem(_tr(f"tracking.stab.smoothing.{value}"), value)
        self.stab_smoothing.currentIndexChanged.connect(
            lambda _i: self._emit_stab(smoothing=self.stab_smoothing.currentData())
        )
        self.stab_frames = QDoubleSpinBox(objectName="stab_frames")
        self.stab_frames.setRange(0.5, 600.0)
        allow_shrinking(self.stab_frames)
        self.stab_frames.setSuffix(" " + _tr("tracking.stab.frames"))
        self.stab_frames.valueChanged.connect(lambda v: self._emit_stab(smoothing_frames=float(v)))
        smoothing_row.addWidget(self.stab_smoothing, 1)
        smoothing_row.addWidget(self.stab_frames)
        stab.addRow(_tr("tracking.stab.smoothing"), smoothing_row)
        self.stab_borders = make_shrinkable(QComboBox(objectName="stab_borders"))
        for value in BorderMode.ALL:
            self.stab_borders.addItem(_tr(f"tracking.stab.borders.{value}"), value)
        self.stab_borders.currentIndexChanged.connect(
            lambda _i: self._emit_stab(borders=self.stab_borders.currentData())
        )
        stab.addRow(_tr("tracking.stab.borders"), self.stab_borders)
        self.stab_info = QLabel(objectName="stab_info")
        self.stab_info.setWordWrap(True)
        stab.addRow(self.stab_info)
        self.auto_stab = QPushButton(_tr("tracking.stab.auto"), objectName="stab_auto")
        self.auto_stab.setToolTip(_tr("tracking.stab.auto.tip"))
        allow_shrinking(self.auto_stab, 120)
        self.auto_stab.clicked.connect(self.auto_stabilize_requested.emit)
        stab.addRow(self.auto_stab)
        root.addWidget(self.stab_box)
        self.set_state({})

    # -- construction -------------------------------------------------------------------------

    def _size_pair(self, form: QFormLayout, label_key: str, name: str):
        row = QHBoxLayout()
        width = QSpinBox(objectName=f"tracker_{name}_w")
        height = QSpinBox(objectName=f"tracker_{name}_h")
        for spin, field in ((width, f"{name}_width"), (height, f"{name}_height")):
            spin.setRange(8, 4096)
            spin.setSuffix(" px")
            spin.setKeyboardTracking(False)
            allow_shrinking(spin, 72)  # deux champs côte à côte : leur somme ne doit pas élargir l'inspecteur
            spin.valueChanged.connect(lambda v, f=field: self._emit_settings(**{f: float(v)}))
            row.addWidget(spin)
        form.addRow(_tr(label_key), row)
        return width, height

    # -- lecture ---------------------------------------------------------------------------------

    def selected_ids(self) -> list[str]:
        return [item.data(Qt.UserRole) for item in self.tracker_list.selectedItems()]

    def selected_link_id(self) -> str:
        item = self.links_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else ""

    # -- émission --------------------------------------------------------------------------------

    # Les signaux issus d'un élément de liste sont émis **après** le retour du
    # signal Qt : l'opération déclenchée reconstruit la liste, et Qt utilise
    # encore l'élément juste après l'avoir signalé.

    def _later(self, signal, *args) -> None:
        QTimer.singleShot(0, lambda: signal.emit(*args))

    def _on_selection(self) -> None:
        if not self._updating:
            self._later(self.tracker_selection_changed, self.selected_ids())

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        tracker_id = item.data(Qt.UserRole)
        known = {t["id"]: t for t in self._state.get("trackers", ())}
        current = known.get(tracker_id)
        if current is None:
            return
        name = item.text().strip()
        if name and name != current["name"]:
            current["name"] = name
            self._later(self.rename_tracker_requested, tracker_id, name)
        visible = item.checkState() == Qt.Checked
        if visible != current["visible"]:
            current["visible"] = visible
            self._later(self.tracker_visibility_changed, tracker_id, visible)

    def _on_link_item_changed(self, item: QListWidgetItem) -> None:
        if not self._updating:
            self._later(self.link_enabled_changed, item.data(Qt.UserRole), item.checkState() == Qt.Checked)

    def _emit_settings(self, **changes) -> None:
        if not self._updating:
            self.settings_changed.emit(changes)

    def _emit_stab(self, **changes) -> None:
        if not self._updating:
            self.stabilization_changed.emit(changes)

    def _emit_link(self, bake: bool) -> None:
        data = self.target.currentData()
        if not data:
            return
        spec = dict(data)
        spec.update(
            position=self.apply_position.isChecked() or spec.get("target") == "anchor",
            rotation=self.apply_rotation.isChecked() and self.apply_rotation.isEnabled(),
            scale=self.apply_scale.isChecked() and self.apply_scale.isEnabled(),
        )
        self.link_requested.emit(spec, bake)

    def _emit_for_link(self, signal) -> None:
        link_id = self.selected_link_id()
        if link_id:
            signal.emit(link_id)

    # -- état ------------------------------------------------------------------------------------

    def set_state(self, state: TrackingPanelState) -> None:
        """Affiche ``state`` (voir :meth:`TrackingMixin._tracking_panel_state`)."""
        self._updating = True
        try:
            self._apply_state(state or {})
        finally:
            self._updating = False

    def _apply_state(self, state: TrackingPanelState) -> None:
        self._state = state
        kind = state.get("kind", "")
        video = kind == "video"
        self.message.setText(state.get("message", "") or ("" if kind else _tr("tracking.no_clip")))
        self.message.setVisible(bool(self.message.text()))
        for box in (self.tracker_box, self.zones_box, self.apply_box, self.stab_box):
            box.setVisible(video)
        self.links_box.setVisible(bool(kind))
        busy = bool(state.get("busy"))
        available = bool(state.get("available", True))
        # Trackers
        selected = set(state.get("selected", ()))
        trackers = list(state.get("trackers", ()))
        current_ids = [self.tracker_list.item(i).data(Qt.UserRole) for i in range(self.tracker_list.count())]
        if current_ids != [t["id"] for t in trackers]:
            self.tracker_list.clear()
            for tracker in trackers:
                item = QListWidgetItem(tracker["name"])
                item.setData(Qt.UserRole, tracker["id"])
                item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
                self.tracker_list.addItem(item)
        rows = max(2, min(5, len(trackers)))
        self.tracker_list.setFixedHeight(rows * 22 + 8)  # hauteur ajustée au contenu
        for row, tracker in enumerate(trackers):  # mise à jour en place
            item = self.tracker_list.item(row)
            if item.text() != tracker["name"]:
                item.setText(tracker["name"])
            item.setIcon(_swatch(tracker["color"]))
            item.setCheckState(Qt.Checked if tracker["visible"] else Qt.Unchecked)
            item.setToolTip(tracker.get("summary", ""))
            item.setSelected(tracker["id"] in selected)
        primary = state.get("primary")
        self.summary.setText(primary.get("summary", "") if primary else "")
        has_selection = bool(selected)
        self.remove_button.setEnabled(has_selection and not busy)
        self.reset_button.setEnabled(has_selection and not busy)
        self.add_button.setEnabled(video and not busy)
        for button in (self.backward_button, self.forward_button):
            button.setEnabled(has_selection and not busy and available)
        self.stop_button.setEnabled(busy)
        self.progress.setValue(int(round(float(state.get("progress", 0.0)) * 1000)))
        self.progress.setVisible(busy or float(state.get("progress", 0.0)) > 0)
        self.status.setText(state.get("status", ""))
        # Réglages du tracker principal
        settings = primary.get("settings") if primary else None
        self.zones_box.setEnabled(settings is not None and not busy)
        if settings is not None:
            self.pattern_w.setValue(int(round(settings["pattern_width"])))
            self.pattern_h.setValue(int(round(settings["pattern_height"])))
            self.search_w.setValue(int(round(settings["search_width"])))
            self.search_h.setValue(int(round(settings["search_height"])))
            self.min_confidence.setValue(float(settings["min_confidence"]))
            self.adapt.setCurrentIndex(max(0, self.adapt.findData(settings["adapt"])))
            self.precision.setCurrentIndex(max(0, self.precision.findData(settings["precision"])))
            self.stop_on_loss.setChecked(bool(settings["stop_on_loss"]))
        self.show_paths.setChecked(bool(state.get("show_paths", True)))
        # Appliquer
        current = self.target.currentData()
        self.target.clear()
        for label, spec in state.get("targets", ()):
            self.target.addItem(label, spec)
        if self.target.count() == 0:
            self.target.addItem(_tr("tracking.target.none"), None)
        elif current is not None:
            index = next((i for i in range(self.target.count()) if self.target.itemData(i) == current), -1)
            if index >= 0:
                self.target.setCurrentIndex(index)
        multi = len(selected) >= 2
        self.apply_rotation.setEnabled(multi)
        self.apply_scale.setEnabled(multi)
        self.multi_hint.setVisible(not multi)
        can_apply = has_selection and bool(state.get("analyzed")) and self.target.currentData() is not None
        self.link_button.setEnabled(can_apply and not busy)
        self.bake_button.setEnabled(can_apply and not busy)
        # Liaisons reçues
        links = list(state.get("links", ()))
        previous = self.selected_link_id()
        self.links_list.clear()
        for link in links:
            item = QListWidgetItem(link["label"])
            item.setData(Qt.UserRole, link["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if link["enabled"] else Qt.Unchecked)
            if link.get("warning"):
                item.setForeground(QColor(COLORS["warning"]))
                item.setToolTip(link["warning"])
            self.links_list.addItem(item)
        if links:
            rows = [i for i, link in enumerate(links) if link["id"] == previous]
            self.links_list.setCurrentRow(rows[0] if rows else 0)
        else:
            self.links_list.addItem(QListWidgetItem(_tr("tracking.links.empty")))
            self.links_list.item(0).setFlags(Qt.NoItemFlags)
        has_links = bool(state.get("links"))
        self.freeze_button.setEnabled(has_links and not busy)
        self.unlink_button.setEnabled(has_links and not busy)
        # Stabilisation
        stab = state.get("stabilization") or {}
        enabled = bool(stab.get("enabled"))
        self.stab_enabled.setChecked(enabled)
        self.stab_enabled.setEnabled(bool(state.get("analyzed")) and not busy)
        for widget in (self.stab_mode, self.stab_smoothing, self.stab_borders):
            widget.setEnabled(enabled and not busy)
        self.stab_mode.setCurrentIndex(max(0, self.stab_mode.findData(stab.get("mode", StabilizationMode.POSITION))))
        self.stab_smoothing.setCurrentIndex(
            max(0, self.stab_smoothing.findData(stab.get("smoothing", Smoothing.MEDIUM)))
        )
        self.stab_frames.setValue(float(stab.get("smoothing_frames", 12.0)))
        self.stab_frames.setVisible(stab.get("smoothing") == Smoothing.CUSTOM)
        self.stab_frames.setEnabled(enabled and not busy)
        self.stab_borders.setCurrentIndex(max(0, self.stab_borders.findData(stab.get("borders", BorderMode.ZOOM))))
        info = stab.get("info", "")
        self.stab_info.setText(info)
        self.stab_info.setVisible(bool(info))
        warning = bool(stab.get("warning"))
        self.stab_info.setStyleSheet(f"color: {COLORS['warning'] if warning else COLORS['muted']};")
        self.auto_stab.setEnabled(video and not busy and available)


__all__ = ["TrackingPanel"]
