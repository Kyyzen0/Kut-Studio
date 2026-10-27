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
    ColorPresetStore,
)
from core.color_nodes import as_graph
from core.usage_level import UsageLevel
from ui.adaptive_layout import FlowLayout, make_shrinkable
from ui.design_system import Spacing
from ui.icons import IconName
from ui.theme import label_style
from ui.properties_panel_mixins.color_keyframes import ColorKeyframesMixin
from ui.properties_widgets.color_curve_editor import ColorCurveEditor
from ui.properties_widgets.level_gate import LevelGate
from ui.properties_widgets.keyframe_row import field_with_keyframes
from ui.i18n import translate

class ColorMixin(ColorKeyframesMixin):
    """Mixin de ``PropertiesPanel`` : groupe Couleur : étalonnage, courbes, presets et LUT."""

    def _build_color_group(self) -> QGroupBox:
        group = self._titled_group("inspector.color.title")
        group.setObjectName("colorGradingGroup")
        root = QVBoxLayout(group)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.sm)

        # Clip étalonné par nœuds : ces réglages sont ceux du nœud courant (page Couleur), on le dit.
        self.color_node_label = QLabel()
        self.color_node_label.setWordWrap(True)
        self.color_node_label.setStyleSheet(label_style(11, "accent", 600))
        self.color_node_label.hide()
        root.addWidget(self.color_node_label)
        self._build_color_animation_notice(root)       # avis « étalonnage découpé en nœuds » (ADR-0008, étape 8)

        self.color_enabled_check = QCheckBox(translate("history.color.enable"))
        self.color_enabled_check.setChecked(True)
        self.color_enabled_check.toggled.connect(self._on_color_enabled_toggled)
        root.addWidget(self.color_enabled_check)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        specs = (
            ("exposure", "inspector.color.exposure", -2.0, 2.0, 0.05, " EV"),
            ("contrast", "field.contrast", -1.0, 1.0, 0.05, ""),
            ("saturation", "inspector.color.saturation", 0.0, 2.0, 0.05, "×"),
            ("temperature", "inspector.color.temperature", -100.0, 100.0, 1.0, ""),
            ("hue", "inspector.color.hue", -180.0, 180.0, 1.0, "°"),
            ("shadows", "inspector.color.shadows", -1.0, 1.0, 0.05, ""),
            ("highlights", "inspector.color.highlights", -1.0, 1.0, 0.05, ""),
        )
        self.color_field_spins: dict[str, QDoubleSpinBox] = {}
        self._color_captions: dict[str, QLabel] = {}       # champ -> libellé de sa ligne (même largeur pour tous : ils passent à la ligne ensemble)
        self._color_field_keys: dict[str, str] = {}        # champ -> clé i18n du libellé (relu à chaque langue)
        for name, label_key, minimum, maximum, step, suffix in specs:
            label = translate(label_key)
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(2)
            spin.setSingleStep(step)
            spin.setSuffix(suffix)
            spin.valueChanged.connect(
                lambda value, field=name: self._on_color_field_changed(field, value)
            )
            self.color_field_spins[name] = spin
            self._color_field_keys[name] = label_key
            field, line = field_with_keyframes(spin)
            caption = QLabel(label)
            self._color_captions[name] = caption
            form.addRow(caption, field)
            self._add_color_keyframe_row(line, name, label)        # ‹ ◆ › à droite de chaque réglage animable (ADR-0008, étape 8)
        self._color_form = form
        self._equalize_color_captions()
        root.addLayout(form)

        # Courbes, préréglages et LUT 3D : réglages avancés (niveau d'usage), réunis dans un conteneur créé ici.
        self.color_advanced_gate = LevelGate()
        self.color_advanced_gate.minimum = UsageLevel.ADVANCED
        advanced = self.color_advanced_gate.layout()
        advanced.setSpacing(Spacing.sm)
        curves_title = QLabel(translate("inspector.color.curves"))
        curves_title.setStyleSheet(label_style(11, "muted", 700))
        advanced.addWidget(curves_title)
        self.color_curve_channel = make_shrinkable(QComboBox())
        self.color_curve_channel.addItem(translate("inspector.color.channel_master"), "master")
        self.color_curve_channel.addItem(translate("inspector.color.channel_red"), "red")
        self.color_curve_channel.addItem(translate("inspector.color.channel_green"), "green")
        self.color_curve_channel.addItem(translate("inspector.color.channel_blue"), "blue")
        self.color_curve_channel.currentIndexChanged.connect(
            self._on_color_curve_channel_changed
        )
        advanced.addWidget(self.color_curve_channel)
        self.color_curve_editor = ColorCurveEditor()
        self.color_curve_editor.points_changed.connect(self._on_color_curve_points_changed)
        advanced.addWidget(self.color_curve_editor)
        self.color_curve_reset_button = self._make_action_button(
            IconName.RESET, translate("inspector.color.curve_reset"), translate("inspector.color.curve_linear_tip")
        )
        self.color_curve_reset_button.clicked.connect(self._reset_active_color_curve)
        advanced.addWidget(self.color_curve_reset_button)

        preset_title = QLabel(translate("inspector.color.presets"))
        preset_title.setStyleSheet(label_style(11, "muted", 700))
        advanced.addWidget(preset_title)
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(Spacing.xs)
        self.color_preset_combo = make_shrinkable(QComboBox())
        self.color_preset_combo.setMinimumWidth(72)      # le bouton « Appliquer » garde son libellé entier : la liste cède la place
        self._refresh_color_presets()
        self.color_preset_apply_button = self._make_action_button(
            None, translate("tracking.apply"), translate("inspector.color.apply_tip")
        )
        self.color_preset_apply_button.clicked.connect(self._apply_selected_color_preset)
        preset_layout.addWidget(self.color_preset_combo, 1)
        preset_layout.addWidget(self.color_preset_apply_button)
        advanced.addWidget(preset_row)
        self.color_preset_save_button = self._make_action_button(
            None, translate("mograph.layers.save_preset"), translate("inspector.color.save_tip")
        )
        self.color_preset_save_button.clicked.connect(self._request_save_color_preset)
        advanced.addWidget(self.color_preset_save_button)

        lut_title = QLabel("LUT 3D (.cube)")
        lut_title.setStyleSheet(label_style(11, "muted", 700))
        advanced.addWidget(lut_title)
        self.color_lut_label = QLabel(translate("inspector.color.no_lut"))
        self.color_lut_label.setWordWrap(True)
        self.color_lut_label.setStyleSheet(label_style(11, "muted", 500))
        advanced.addWidget(self.color_lut_label)
        lut_row = QWidget()
        lut_layout = FlowLayout(lut_row, spacing=Spacing.xs)
        self.color_lut_import_button = self._make_action_button(
            None, translate("inspector.color.import"), translate("inspector.color.import_tip")
        )
        self.color_lut_remove_button = self._make_action_button(
            IconName.REMOVE, translate("shortcuts.clear"), translate("inspector.color.remove_tip")
        )
        self.color_lut_import_button.clicked.connect(self._request_color_lut_import)
        self.color_lut_remove_button.clicked.connect(self._request_color_lut_remove)
        lut_layout.addWidget(self.color_lut_import_button)
        lut_layout.addWidget(self.color_lut_remove_button)
        advanced.addWidget(lut_row)

        self.color_reset_button = self._make_action_button(
            IconName.RESET, translate("shortcuts.reset_all"), translate("inspector.color.reset_tip")
        )
        self.color_reset_button.clicked.connect(self._request_color_reset)
        root.addWidget(self.color_reset_button)
        # Courbes, préréglages et LUT : en fin de groupe, derrière leur pliant « Avancé » (« Réinitialiser » reste au-dessus).
        root.addWidget(self._new_fold(group, UsageLevel.ADVANCED))
        root.addWidget(self.color_advanced_gate)
        group.setEnabled(False)
        return group

    def _refresh_color_presets(self) -> None:
        current = self.color_preset_combo.currentData() if hasattr(self, "color_preset_combo") else None
        if not hasattr(self, "color_preset_combo"):
            return
        self.color_preset_combo.clear()
        for preset in self.color_preset_store.all_presets():
            label = preset.name if preset.builtin else translate("inspector.color.personal", name=preset.name)
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

    def set_color_node(self, node_id: str | None) -> None:
        """Nœud courant (page Couleur) : les réglages affichés deviennent les siens."""
        self._color_node_id = node_id
        self.update_color_grade_from_clip(self._color_value)

    def update_color_grade_from_clip(self, grade: object, keyframes: list | None = None) -> None:
        """Affiche l'étalonnage d'un clip : son ``ColorGrade``, ou le nœud courant de son graphe de nœuds.

        ``keyframes`` : images clés ``color.*`` du clip (ADR-0008, étape 8) ; ``None`` garde celles déjà connues."""
        self._color_value = grade
        if keyframes is not None:
            self._current_grade_keyframes = list(keyframes)
        graph = as_graph(grade)
        node = graph.node_or_first(self._color_node_id)
        self._current_color_grade = node.grade
        if len(graph.correctors) > 1:
            from ui.color_page.node_editor import node_number

            self.color_node_label.setText(translate(
                "inspector.color.node", number=node_number(graph.position(node.id)),
                count=node_number(len(graph.correctors) - 1),
                label=f" · {node.label}" if node.label else "",
            ))
        self.color_node_label.setVisible(len(graph.correctors) > 1)
        self._allow_color_signals = False
        try:
            self.color_enabled_check.setChecked(self._current_color_grade.enabled)
            for name, spin in self.color_field_spins.items():
                spin.setValue(float(getattr(self._current_color_grade, name)))
            self._sync_color_curve_editor()
            lut = self._current_color_grade.lut
            if lut is None:
                self.color_lut_label.setText(translate("inspector.color.no_lut"))
                self.color_lut_label.setStyleSheet(label_style(11, "muted", 500))
                self.color_lut_remove_button.setEnabled(False)
            elif lut.missing:
                self.color_lut_label.setText(translate("inspector.color.lut_missing", title=lut.title, path=lut.path))
                self.color_lut_label.setStyleSheet(label_style(11, "danger", 600))
                self.color_lut_remove_button.setEnabled(True)
            else:
                self.color_lut_label.setText(f"{lut.title}\n{lut.path}")
                self.color_lut_label.setStyleSheet(label_style(11, "text", 500))
                self.color_lut_remove_button.setEnabled(True)
        finally:
            self._allow_color_signals = True
        self._sync_color_keyframes()          # curseurs animés, losanges et avis de nœuds

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
        name, accepted = QInputDialog.getText(self, translate("dialog.title.color_preset"), translate("dialog.preset.name_label"))
        if accepted and name.strip():
            self.color_preset_save_requested.emit(self.selected_clip.id, name.strip())

    def _request_color_lut_import(self) -> None:
        if self.selected_clip is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, translate("dialog.lut.import_title"), "", "LUT 3D (*.cube)"
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
