"""Groupe « Vitesse et durée » de l'inspecteur (remappage temporel)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QWidget

from core.time_remapping import MAX_SPEED, MIN_SPEED
from core.usage_level import UsageLevel
from ui.adaptive_layout import FlowLayout
from ui.design_system import Sizes, Spacing, Typography
from ui.i18n import translate
from ui.icons import IconButton
from ui.properties_widgets.level_gate import gated
from ui.properties_widgets.time_section import TimeSection
from ui.theme import label_style


class SpeedGroupMixin:
    """Mixin de ``PropertiesPanel`` : construction du groupe « Vitesse et durée »."""

    def _build_speed_group(self, layout):
        """Groupe « Vitesse et durée » (remappage temporel)."""
        # ----- Vitesse et durée (tâche 18) -----------------------------
        self.speed_group = self._titled_group("group.speed_and_duration")
        speed_form = QFormLayout(self.speed_group)
        speed_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        speed_form.setSpacing(Spacing.xs)
        speed_form.setLabelAlignment(Qt.AlignLeft)
        speed_form.setRowWrapPolicy(QFormLayout.WrapLongRows)  # libellé au-dessus du champ si la ligne est trop longue

        # Vitesse numérique
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setDecimals(2)
        self.speed_spinbox.setRange(MIN_SPEED, MAX_SPEED)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setMinimumWidth(70)
        self.speed_spinbox.setEnabled(False)
        self.speed_spinbox.valueChanged.connect(self._on_speed_changed)
        self._add_row(speed_form, "field.speed", self.speed_spinbox)

        # Boutons de preset de vitesse
        speed_presets = QWidget()
        speed_presets_layout = FlowLayout(speed_presets, spacing=Spacing.xs)  # cinq boutons : à la ligne si besoin

        self.speed_0_25x_button = self._make_action_button(
            None, translate("action.speed_0.25x"), translate("tooltip.speed_0.25x")
        )
        self.speed_0_5x_button = self._make_action_button(
            None, translate("action.speed_0.5x"), translate("tooltip.speed_0.5x")
        )
        self.speed_1x_button = self._make_action_button(
            None, translate("action.speed_1x"), translate("tooltip.speed_1x")
        )
        self.speed_2x_button = self._make_action_button(
            None, translate("action.speed_2x"), translate("tooltip.speed_2x")
        )
        self.speed_4x_button = self._make_action_button(
            None, translate("action.speed_4x"), translate("tooltip.speed_4x")
        )

        # Connecter les boutons de preset
        self.speed_0_25x_button.clicked.connect(lambda: self.speed_spinbox.setValue(0.25))
        self.speed_0_5x_button.clicked.connect(lambda: self.speed_spinbox.setValue(0.5))
        self.speed_1x_button.clicked.connect(lambda: self.speed_spinbox.setValue(1.0))
        self.speed_2x_button.clicked.connect(lambda: self.speed_spinbox.setValue(2.0))
        self.speed_4x_button.clicked.connect(lambda: self.speed_spinbox.setValue(4.0))
        
        for btn in [
            self.speed_0_25x_button,
            self.speed_0_5x_button,
            self.speed_1x_button,
            self.speed_2x_button,
            self.speed_4x_button,
        ]:
            btn.setMinimumWidth(40)
            btn.setEnabled(False)
            speed_presets_layout.addWidget(btn)

        self._add_stacked_row(speed_form, "mograph.layers.presets", speed_presets)

        # Images intermédiaires, son, courbe de vitesse, analyse du flux optique (widget autonome).
        self.time_section = TimeSection()
        self.time_section.command_requested.connect(self._on_time_section_command)

        # Bouton Reverse
        self.reverse_button = IconButton(
            icon=None, tooltip=translate("tooltip.reverse"), size=Sizes.icon_button
        )
        self.reverse_button.setText("  " + translate("field.reverse"))
        self.reverse_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.reverse_button.setCheckable(True)
        self.reverse_button.setMinimumHeight(Sizes.button_md)
        self.reverse_button.setEnabled(False)
        self.reverse_button.toggled.connect(self._on_reverse_toggled)
        self._add_row(speed_form, "field.reverse", self.reverse_button)

        # Freeze frame
        self.freeze_frame_button = self._make_action_button(
            None, translate("field.freeze_frame"), translate("tooltip.freeze_frame")
        )
        self.freeze_frame_button.setEnabled(False)
        self.freeze_frame_button.clicked.connect(self._on_freeze_frame_clicked)
        self._add_row(speed_form, "field.freeze_frame", self.freeze_frame_button)

        # Durée freeze frame (visible uniquement en mode freeze)
        self.freeze_duration_spinbox = QDoubleSpinBox()
        self.freeze_duration_spinbox.setDecimals(2)
        self.freeze_duration_spinbox.setRange(0.01, 3600.0)  # 0.01s à 1h
        self.freeze_duration_spinbox.setSingleStep(0.1)
        self.freeze_duration_spinbox.setValue(1.0)
        self.freeze_duration_spinbox.setMinimumWidth(70)
        self.freeze_duration_spinbox.setEnabled(False)
        self.freeze_duration_spinbox.valueChanged.connect(self._on_freeze_duration_changed)
        self.freeze_duration_label = QLabel("1,00 s")
        self.freeze_duration_label.setStyleSheet(label_style(Typography.caption, "muted", 500))
        self.freeze_duration_label.setVisible(False)

        freeze_row = QWidget()
        freeze_row_layout = QHBoxLayout(freeze_row)
        freeze_row_layout.setContentsMargins(0, 0, 0, 0)
        freeze_row_layout.setSpacing(Spacing.sm)
        freeze_row_layout.addWidget(self.freeze_duration_spinbox)
        freeze_row_layout.addWidget(self.freeze_duration_label)
        freeze_row.setVisible(False)
        self._add_row(speed_form, "field.freeze_duration", freeze_row)
        self.freeze_duration_row = freeze_row

        # Bouton de réinitialisation
        self.reset_speed_button = self._make_action_button(
            None, translate("action.reset_speed"), translate("tooltip.reset_speed")
        )
        self.reset_speed_button.setEnabled(False)
        self.reset_speed_button.clicked.connect(self._on_time_remapping_reset)
        speed_form.addRow("", self.reset_speed_button)

        # Affichage des durées source et timeline
        self.source_duration_label = QLabel(translate("inspector.source_empty"))
        self.source_duration_label.setStyleSheet(label_style(Typography.caption, "muted", 500))
        self.timeline_duration_label = QLabel(translate("inspector.timeline_empty"))
        self.timeline_duration_label.setStyleSheet(label_style(Typography.caption, "muted", 500))
        duration_info = QWidget()
        duration_layout = FlowLayout(duration_info, spacing=Spacing.md)  # deux libellés : à la ligne si la place manque
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.addWidget(self.source_duration_label)
        duration_layout.addWidget(self.timeline_duration_label)
        speed_form.addRow("", duration_info)
        # Niveau d'usage : la section « Temps » complète est avancée ; elle clôt le groupe, derrière son pliant « Avancé ».
        self.time_section_gate = gated(self.time_section, UsageLevel.ADVANCED)
        speed_form.addRow(self._new_fold(self.speed_group, UsageLevel.ADVANCED))
        speed_form.addRow(self.time_section_gate)

        layout.addWidget(self.speed_group)
