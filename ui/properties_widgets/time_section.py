"""Section « Temps » de l'inspecteur : images intermédiaires, son, courbe de vitesse, analyse du flux optique.

Compacte et sans logique : elle n'exécute rien, elle émet ``command_requested(commande, argument)`` (les commandes de
:mod:`core.time_commands`) ; le panneau y ajoute le clip et la fenêtre les applique. Elle prolonge le groupe « Vitesse et durée »
(vitesse, préréglages, sens inverse, arrêt sur image) sans le remplacer : une vitesse constante se règle comme avant, la courbe
et les images intermédiaires sont à portée de main quand on en a besoin.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.time_remapping import FlowQuality, FreezeFrameMode, TimeInterpolation
from ui.adaptive_layout import FlowLayout, allow_shrinking
from ui.design_system import Spacing
from ui.i18n import translate
from ui.theme import label_style


class TimeSection(QWidget):
    """Réglages du temps d'un clip qui prolongent la vitesse constante."""

    command_requested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._analysis_running = False
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, Spacing.xs, 0, 0)
        self._form.setSpacing(Spacing.xs)
        self._form.setLabelAlignment(Qt.AlignLeft)
        self._form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.interpolation_combo = QComboBox()
        for mode in TimeInterpolation:
            self.interpolation_combo.addItem("", mode.value)
        self.interpolation_combo.currentIndexChanged.connect(self._on_interpolation)
        allow_shrinking(self.interpolation_combo, 90)
        self.quality_combo = QComboBox()
        for level in FlowQuality:
            self.quality_combo.addItem("", level.value)
        self.quality_combo.currentIndexChanged.connect(self._on_quality)
        allow_shrinking(self.quality_combo, 90)

        # Cases et boutons passent à la ligne dans une largeur étroite : jamais ils ne fixent le minimum de l'inspecteur.
        self.pitch_check = QCheckBox()
        self.pitch_check.toggled.connect(lambda checked: self._emit("preserve_pitch", bool(checked)))
        self.audio_check = QCheckBox()
        self.audio_check.toggled.connect(lambda checked: self._emit("remap_audio", bool(checked)))
        audio_row = QWidget()
        audio_layout = FlowLayout(audio_row, spacing=Spacing.sm)
        audio_layout.addWidget(self.pitch_check)
        audio_layout.addWidget(self.audio_check)

        self.curve_label = QLabel()
        self.curve_label.setStyleSheet(label_style(11, "muted", 500))
        self.curve_label.setWordWrap(True)
        self.add_point_button = QPushButton()
        self.add_point_button.clicked.connect(lambda: self._emit("add_point", None))
        self.clear_curve_button = QPushButton()
        self.clear_curve_button.clicked.connect(lambda: self._emit("clear_curve", None))
        for button in (self.add_point_button, self.clear_curve_button):
            allow_shrinking(button, 40)
        curve_row = QWidget()
        curve_layout = QVBoxLayout(curve_row)
        curve_layout.setContentsMargins(0, 0, 0, 0)
        curve_layout.setSpacing(Spacing.xs)
        curve_layout.addWidget(self.curve_label)
        curve_buttons = QWidget()
        curve_buttons_layout = FlowLayout(curve_buttons, spacing=Spacing.xs)
        curve_buttons_layout.addWidget(self.add_point_button)
        curve_buttons_layout.addWidget(self.clear_curve_button)
        curve_layout.addWidget(curve_buttons)

        self.analyze_button = QPushButton()
        self.analyze_button.clicked.connect(self._on_analyze)
        allow_shrinking(self.analyze_button, 40)

        self._rows = {
            "interpolation": (self.interpolation_combo, "time.field.interpolation"),
            "quality": (self.quality_combo, "time.field.quality"),
            "audio": (audio_row, "time.field.audio"),
            "curve": (curve_row, "time.field.curve"),
            "analysis": (self.analyze_button, ""),
        }
        for widget, key in self._rows.values():
            self._form.addRow(translate(key) if key else "", widget)
        self.retranslate()
        self.set_clip(None)

    # ------------------------------------------------------------------
    # Textes
    # ------------------------------------------------------------------

    def retranslate(self) -> None:
        """Libellés, infobulles et choix dans la langue courante."""
        for index, mode in enumerate(TimeInterpolation):
            self.interpolation_combo.setItemText(index, translate(f"time.interpolation.{mode.value}"))
            self.interpolation_combo.setItemData(index, translate(f"time.interpolation.{mode.value}.tip"), Qt.ToolTipRole)
        for index, level in enumerate(FlowQuality):
            self.quality_combo.setItemText(index, translate(f"time.quality.{level.value}"))
        self.pitch_check.setText(translate("time.check.preserve_pitch"))
        self.pitch_check.setToolTip(translate("time.check.preserve_pitch.tip"))
        self.audio_check.setText(translate("time.check.remap_audio"))
        self.audio_check.setToolTip(translate("time.check.remap_audio.tip"))
        self.add_point_button.setText(translate("time.button.add_point"))
        self.add_point_button.setToolTip(translate("time.button.add_point.tip"))
        self.clear_curve_button.setText(translate("time.button.clear_curve"))
        self._set_analyze_text()
        for widget, key in self._rows.values():
            label = self._form.labelForField(widget)
            if label is not None and key:
                label.setText(translate(key))

    def _set_analyze_text(self) -> None:
        self.analyze_button.setText(
            translate("time.button.cancel_analysis" if self._analysis_running else "time.button.analyze")
        )

    # ------------------------------------------------------------------
    # État
    # ------------------------------------------------------------------

    def _show_row(self, name: str, visible: bool) -> None:
        widget, _key = self._rows[name]
        widget.setVisible(visible)
        label = self._form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def set_clip(self, view, track_type: str | None = None) -> None:
        """Reflète le clip affiché (``None`` : rien de sélectionné). ``view`` : :class:`~core.timeline_view_model.TimelineClipView`."""
        self._updating = True
        try:
            remapping = getattr(view, "time_remapping", None)
            kind = track_type or getattr(view, "track_type", None)
            usable = view is not None and remapping is not None and kind in ("video", "audio")
            frozen = bool(remapping) and remapping.freeze_mode == FreezeFrameMode.FREEZE
            locked = bool(getattr(view, "locked", False))
            nested = bool(getattr(view, "sequence_id", ""))
            editable = usable and not frozen and not locked
            is_video = kind == "video"
            if remapping is not None:
                self.interpolation_combo.setCurrentIndex(max(0, self.interpolation_combo.findData(remapping.interpolation.value)))
                self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(remapping.flow_quality.value)))
                self.pitch_check.setChecked(remapping.preserve_pitch)
                self.audio_check.setChecked(remapping.remap_audio)
            flowing = bool(remapping) and remapping.interpolation is TimeInterpolation.OPTICAL_FLOW
            self._show_row("interpolation", usable and is_video)
            self._show_row("quality", usable and is_video and flowing)
            self._show_row("analysis", usable and is_video and flowing)
            self._show_row("audio", usable)
            self._show_row("curve", usable)
            self.interpolation_combo.setEnabled(editable)
            for index, mode in enumerate(TimeInterpolation):
                item = self.interpolation_combo.model().item(index)
                if item is not None:
                    item.setEnabled(not (nested and mode is not TimeInterpolation.SAMPLING))
            self.quality_combo.setEnabled(editable)
            self.pitch_check.setEnabled(editable)
            self.audio_check.setEnabled(editable)
            points = len(getattr(view, "speed_points", ()) or ())
            self.add_point_button.setEnabled(editable)
            self.clear_curve_button.setEnabled(editable and points > 0)
            if points == 0:
                self.curve_label.setText(translate("time.status.constant"))
            else:
                reverse = bool(remapping) and remapping.reverse
                self.curve_label.setText(translate("time.status.reverse_curve" if reverse else "time.status.points", count=points))
            self.analyze_button.setEnabled(editable or self._analysis_running)
        finally:
            self._updating = False

    def set_analysis_running(self, running: bool) -> None:
        """Le bouton devient « Annuler l'analyse » tant que le calcul tourne."""
        self._analysis_running = bool(running)
        self._set_analyze_text()
        self.analyze_button.setEnabled(True if running else self.analyze_button.isEnabled())

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------

    def _emit(self, command: str, argument: object) -> None:
        if not self._updating:
            self.command_requested.emit(command, argument)

    def _on_interpolation(self, index: int) -> None:
        if index >= 0:
            self._emit("interpolation", self.interpolation_combo.itemData(index))

    def _on_quality(self, index: int) -> None:
        if index >= 0:
            self._emit("quality", self.quality_combo.itemData(index))

    def _on_analyze(self) -> None:
        self.command_requested.emit("cancel_analysis" if self._analysis_running else "analyze", None)
