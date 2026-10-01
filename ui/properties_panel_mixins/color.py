"""Groupe Couleur : étalonnage, courbes, presets et LUT."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QVBoxLayout,
    QWidget,
)

from core.color_grading import (
    ColorCurve,
    ColorGrade,
    ColorPresetStore,
)
from ui.design_system import Spacing
from ui.icons import IconName
from ui.theme import label_style
from ui.properties_widgets.color_curve_editor import ColorCurveEditor

class ColorMixin:
    """Mixin de ``PropertiesPanel`` : groupe Couleur : étalonnage, courbes, presets et LUT."""

    def _build_color_group(self) -> QGroupBox:
        group = QGroupBox("Étalonnage couleur")
        group.setObjectName("colorGradingGroup")
        group.setStyleSheet(self.group_style())
        root = QVBoxLayout(group)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.sm)

        self.color_enabled_check = QCheckBox("Activer l’étalonnage")
        self.color_enabled_check.setChecked(True)
        self.color_enabled_check.toggled.connect(self._on_color_enabled_toggled)
        root.addWidget(self.color_enabled_check)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        specs = (
            ("exposure", "Exposition", -2.0, 2.0, 0.05, " EV"),
            ("contrast", "Contraste", -1.0, 1.0, 0.05, ""),
            ("saturation", "Saturation", 0.0, 2.0, 0.05, "×"),
            ("temperature", "Température", -100.0, 100.0, 1.0, ""),
            ("hue", "Teinte", -180.0, 180.0, 1.0, "°"),
            ("shadows", "Ombres", -1.0, 1.0, 0.05, ""),
            ("highlights", "Hautes lumières", -1.0, 1.0, 0.05, ""),
        )
        self.color_field_spins: dict[str, QDoubleSpinBox] = {}
        for name, label, minimum, maximum, step, suffix in specs:
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(2)
            spin.setSingleStep(step)
            spin.setSuffix(suffix)
            spin.valueChanged.connect(
                lambda value, field=name: self._on_color_field_changed(field, value)
            )
            self.color_field_spins[name] = spin
            form.addRow(label, spin)
        root.addLayout(form)

        curves_title = QLabel("Courbes")
        curves_title.setStyleSheet(label_style(11, "muted", 700))
        root.addWidget(curves_title)
        self.color_curve_channel = QComboBox()
        self.color_curve_channel.addItem("Globale", "master")
        self.color_curve_channel.addItem("Rouge", "red")
        self.color_curve_channel.addItem("Verte", "green")
        self.color_curve_channel.addItem("Bleue", "blue")
        self.color_curve_channel.currentIndexChanged.connect(
            self._on_color_curve_channel_changed
        )
        root.addWidget(self.color_curve_channel)
        self.color_curve_editor = ColorCurveEditor()
        self.color_curve_editor.points_changed.connect(self._on_color_curve_points_changed)
        root.addWidget(self.color_curve_editor)
        self.color_curve_reset_button = self._make_action_button(
            IconName.RESET, "Réinitialiser cette courbe", "Courbe linéaire"
        )
        self.color_curve_reset_button.clicked.connect(self._reset_active_color_curve)
        root.addWidget(self.color_curve_reset_button)

        preset_title = QLabel("Grades prêts à l’emploi")
        preset_title.setStyleSheet(label_style(11, "muted", 700))
        root.addWidget(preset_title)
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(Spacing.xs)
        self.color_preset_combo = QComboBox()
        self._refresh_color_presets()
        self.color_preset_apply_button = self._make_action_button(
            None, "Appliquer", "Appliquer le grade sélectionné"
        )
        self.color_preset_apply_button.clicked.connect(self._apply_selected_color_preset)
        preset_layout.addWidget(self.color_preset_combo, 1)
        preset_layout.addWidget(self.color_preset_apply_button)
        root.addWidget(preset_row)
        self.color_preset_save_button = self._make_action_button(
            None, "Enregistrer comme preset…", "Sauvegarder les réglages actuels"
        )
        self.color_preset_save_button.clicked.connect(self._request_save_color_preset)
        root.addWidget(self.color_preset_save_button)

        lut_title = QLabel("LUT 3D (.cube)")
        lut_title.setStyleSheet(label_style(11, "muted", 700))
        root.addWidget(lut_title)
        self.color_lut_label = QLabel("Aucune LUT")
        self.color_lut_label.setWordWrap(True)
        self.color_lut_label.setStyleSheet(label_style(11, "muted", 500))
        root.addWidget(self.color_lut_label)
        lut_row = QWidget()
        lut_layout = QHBoxLayout(lut_row)
        lut_layout.setContentsMargins(0, 0, 0, 0)
        lut_layout.setSpacing(Spacing.xs)
        self.color_lut_import_button = self._make_action_button(
            None, "Importer…", "Importer une LUT Adobe .cube"
        )
        self.color_lut_remove_button = self._make_action_button(
            IconName.REMOVE, "Retirer", "Retirer la LUT du clip"
        )
        self.color_lut_import_button.clicked.connect(self._request_color_lut_import)
        self.color_lut_remove_button.clicked.connect(self._request_color_lut_remove)
        lut_layout.addWidget(self.color_lut_import_button)
        lut_layout.addWidget(self.color_lut_remove_button)
        root.addWidget(lut_row)

        self.color_reset_button = self._make_action_button(
            IconName.RESET, "Tout réinitialiser", "Retirer réglages, courbes et LUT"
        )
        self.color_reset_button.clicked.connect(self._request_color_reset)
        root.addWidget(self.color_reset_button)
        group.setEnabled(False)
        return group

    def _refresh_color_presets(self) -> None:
        current = self.color_preset_combo.currentData() if hasattr(self, "color_preset_combo") else None
        if not hasattr(self, "color_preset_combo"):
            return
        self.color_preset_combo.clear()
        for preset in self.color_preset_store.all_presets():
            label = preset.name if preset.builtin else f"{preset.name} · Personnel"
            self.color_preset_combo.addItem(label, preset.id)
        if current is not None:
            index = self.color_preset_combo.findData(current)
            if index >= 0:
                self.color_preset_combo.setCurrentIndex(index)

    def set_project_color_presets(self, presets: object) -> None:
        """Recharge les presets globaux puis fusionne ceux du projet courant."""
        self.color_preset_store = ColorPresetStore()
        self.color_preset_store.merge_user_presets(list(presets or []))
        self._refresh_color_presets()

    def update_color_grade_from_clip(self, grade: object) -> None:
        self._current_color_grade = grade if isinstance(grade, ColorGrade) else ColorGrade.identity()
        self._allow_color_signals = False
        try:
            self.color_enabled_check.setChecked(self._current_color_grade.enabled)
            for name, spin in self.color_field_spins.items():
                spin.setValue(float(getattr(self._current_color_grade, name)))
            self._sync_color_curve_editor()
            lut = self._current_color_grade.lut
            if lut is None:
                self.color_lut_label.setText("Aucune LUT")
                self.color_lut_label.setStyleSheet(label_style(11, "muted", 500))
                self.color_lut_remove_button.setEnabled(False)
            elif lut.missing:
                self.color_lut_label.setText(f"⚠ LUT manquante : {lut.title}\n{lut.path}")
                self.color_lut_label.setStyleSheet(label_style(11, "danger", 600))
                self.color_lut_remove_button.setEnabled(True)
            else:
                self.color_lut_label.setText(f"{lut.title}\n{lut.path}")
                self.color_lut_label.setStyleSheet(label_style(11, "text", 500))
                self.color_lut_remove_button.setEnabled(True)
        finally:
            self._allow_color_signals = True

    def _sync_color_curve_editor(self) -> None:
        channel = str(self.color_curve_channel.currentData() or "master")
        self.color_curve_editor.set_curve(channel, self._current_color_grade.curves.curve(channel))

    def _on_color_enabled_toggled(self, enabled: bool) -> None:
        if self._allow_color_signals and self.selected_clip is not None:
            self.color_grade_enabled_changed.emit(self.selected_clip.id, bool(enabled))

    def _on_color_field_changed(self, field: str, value: float) -> None:
        if self._allow_color_signals and self.selected_clip is not None:
            self.color_grade_field_changed.emit(self.selected_clip.id, field, float(value))

    def _on_color_curve_channel_changed(self, _index: int) -> None:
        self._sync_color_curve_editor()

    def _on_color_curve_points_changed(self, channel: str, points: object) -> None:
        if self._allow_color_signals and self.selected_clip is not None:
            self.color_curve_changed.emit(self.selected_clip.id, channel, points)

    def _reset_active_color_curve(self) -> None:
        if self.selected_clip is None:
            return
        channel = str(self.color_curve_channel.currentData() or "master")
        points = ColorCurve.identity().points
        self.color_curve_editor.set_curve(channel, ColorCurve.identity())
        self.color_curve_changed.emit(self.selected_clip.id, channel, points)

    def _apply_selected_color_preset(self) -> None:
        if self.selected_clip is not None and self.color_preset_combo.currentData():
            self.color_preset_applied.emit(
                self.selected_clip.id, str(self.color_preset_combo.currentData())
            )

    def _request_save_color_preset(self) -> None:
        if self.selected_clip is None:
            return
        name, accepted = QInputDialog.getText(self, "Preset couleur", "Nom du preset :")
        if accepted and name.strip():
            self.color_preset_save_requested.emit(self.selected_clip.id, name.strip())

    def _request_color_lut_import(self) -> None:
        if self.selected_clip is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer une LUT", "", "LUT 3D (*.cube)"
        )
        if path:
            self.color_lut_import_requested.emit(self.selected_clip.id, path)

    def _request_color_lut_remove(self) -> None:
        if self.selected_clip is not None:
            self.color_lut_remove_requested.emit(self.selected_clip.id)

    def _request_color_reset(self) -> None:
        if self.selected_clip is not None:
            self.color_grade_reset_requested.emit(self.selected_clip.id)

    def update_color_values(self):
        """Compatibilité avec l'ancien aperçu couleur (désormais modèle-first)."""
        return None
