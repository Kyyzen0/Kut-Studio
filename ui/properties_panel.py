"""Inspecteur de propriétés (clip sélectionné + paramètres du projet).

Refonte UI/UX :

- tous les boutons d'action utilisent des icônes SVG cohérentes ;
- les espacements et les tailles passent par :mod:`ui.design_system` ;
- la hiérarchie visuelle est renforcée par un titre clair et un
  regroupement logique des contrôles.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_effects_model import (
    AudioEffect,
    AudioEffectType,
    parameter_specs as audio_parameter_specs,
)
from core.effects_model import (
    ClipEffect,
    EffectType,
    is_single_instance,
    parameter_specs,
)
from core.project_model import MAX_GAIN_DB, MIN_GAIN_DB
from core.time_remapping import FreezeFrameMode, TimeRemapping, MIN_SPEED, MAX_SPEED
from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    evaluate_transform,
)
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.text_style_editor import TextStyleEditor
from ui.theme import COLORS, label_style


# Tolérance pour considérer une image-clé comme « sous » le losange.
_KEYFRAME_MATCH_TOLERANCE = 1e-3

_PROPERTY_RANGES = {
    "position_x": (-4.0, 4.0, 0.01),
    "position_y": (-4.0, 4.0, 0.01),
    "scale": (0.05, 10.0, 0.01),
    "rotation": (-3600.0, 3600.0, 0.1),
    "opacity": (0.0, 1.0, 0.01),
}


class _DiamondButton(QToolButton):
    """Petit bouton losange pour ajouter / retirer une image-clé.

    État ``checked`` : image-clé présente au playhead courant.
    Clic simple : ajoute ou remplace l'image-clé.
    Maj+clic : retire l'image-clé présente sous la tête de lecture.
    """

    def __init__(self, property_name: str, parent=None):
        super().__init__(parent)
        self.property_name = property_name
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setToolTip(
            f"Image-clé « {property_name} » : clic pour ajouter ou remplacer, "
            "Maj+clic pour retirer"
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        center = QPointF(self.width() / 2, self.height() / 2)
        polygon = QPolygonF(
            [
                QPointF(center.x(), rect.top()),
                QPointF(rect.right(), center.y()),
                QPointF(center.x(), rect.bottom()),
                QPointF(rect.left(), center.y()),
            ]
        )
        if self.isChecked():
            painter.setBrush(QColor(COLORS["diamond_filled"]))
        else:
            painter.setBrush(QColor(COLORS["surface"]))
        painter.setPen(QColor(COLORS["diamond_outline"]))
        painter.drawPolygon(polygon)


class PropertiesPanel(QWidget):
    transform_changed = Signal(str, str, float)
    keyframe_added = Signal(str, str, float, float)
    keyframe_removed = Signal(str, str, float)
    transform_reset = Signal(str)
    audio_gain_changed = Signal(float)
    audio_pan_changed = Signal(float)
    audio_fade_changed = Signal(str, float)
    audio_fades_reset = Signal()
    # Time remapping signals
    speed_changed = Signal(str, float)
    reverse_toggled = Signal(str, bool)
    freeze_frame_created = Signal(str, float, float)
    freeze_frame_removed = Signal(str)
    freeze_duration_changed = Signal(str, float)
    time_remapping_reset = Signal(str)
    transition_type_changed = Signal(str, str)
    transition_duration_changed = Signal(str, float)
    transition_remove_requested = Signal(str)
    # Effets visuels d'un clip (tâche 21)
    effect_enabled_changed = Signal(str, str, bool)
    effect_add_requested = Signal(str, str)
    effect_removed = Signal(str, str)
    effect_moved = Signal(str, str, int)
    effect_parameter_changed = Signal(str, str, str, float)
    # Effets audio non destructifs (tâche 27)
    audio_effect_add_requested = Signal(str, str)
    # (clip_id, effect_type)
    audio_effect_removed = Signal(str, str)
    # (clip_id, effect_id)
    audio_effect_moved = Signal(str, str, int)
    # (clip_id, effect_id, delta)
    audio_effect_enabled_changed = Signal(str, str, bool)
    # (clip_id, effect_id, enabled)
    audio_effect_parameter_changed = Signal(str, str, str, float)
    # (clip_id, effect_id, name, value)

    def __init__(self, update_color_effect, update_volume, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.update_color_effect_callback = update_color_effect
        self.selected_clip = None
        self.selected_clip_track_type = None
        self.selected_transition_id: str | None = None
        self.timeline_panel = None
        self._signal_block_depth = 0
        self._allow_property_signals = False
        self._diamond_was_checked: dict[str, bool] = {}
        self._current_playhead_seconds = 0.0
        self._current_transform: ClipTransform | None = None
        self._current_keyframes: list[TransformKeyframe] = []
        # --- Effets (tâche 21) ---
        self._current_effects: list[ClipEffect] = []
        self._selected_effect_id: str | None = None
        self._effect_param_widgets: dict[str, QDoubleSpinBox] = {}
        self._allow_effect_signals = True
        # --- Effets audio (tâche 27) ---
        self._current_audio_effects: list[AudioEffect] = []
        self._selected_audio_effect_id: str | None = None
        self._audio_effect_param_widgets: dict[str, QDoubleSpinBox] = {}
        self._allow_audio_effect_signals = True
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            f"QWidget#properties_panel {{ background: {COLORS['panel']}; "
            f"border-left: 1px solid {COLORS['border']}; }}"
        )
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Titre du panneau (header) + barre d'onglets.
        header = QWidget()
        header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Spacing.lg, Spacing.sm, Spacing.lg, 0)
        header_layout.setSpacing(Spacing.sm)
        title = QLabel("INSPECTEUR")
        title.setStyleSheet(label_style(10, "muted", 800))
        header_layout.addWidget(title)

        # Onglets : Inspecteur / Couleur / Effets / Audio.
        # Utilisation de QPushButton ``checkable`` plutôt que
        # ``QListWidget`` pour garantir un affichage horizontal compact.
        self.inspector_tabs_row = QWidget()
        self.inspector_tabs_layout = QHBoxLayout(self.inspector_tabs_row)
        self.inspector_tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_tabs_layout.setSpacing(Spacing.xs)
        self.inspector_tab_buttons: list[QPushButton] = []
        for index, label in enumerate(("Inspecteur", "Couleur", "Effets", "Audio")):
            button = QPushButton(label)
            button.setObjectName("inspectorTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"QPushButton#inspectorTab {{ background: transparent;"
                f" color: {COLORS['muted']}; border: 1px solid transparent;"
                f" border-radius: 6px; padding: 5px 10px;"
                f" font-weight: 600; font-size: 11px; }}"
                f"QPushButton#inspectorTab:hover {{ color: {COLORS['text']};"
                f" background: {COLORS['surface_hover']}; }}"
                f"QPushButton#inspectorTab:checked {{ color: {COLORS['accent']};"
                f" background: {COLORS['accent_dark']};"
                f" border: 1px solid {COLORS['accent']}; }}"
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_inspector_tab(idx)
            )
            self.inspector_tab_buttons.append(button)
            self.inspector_tabs_layout.addWidget(button)
        self.inspector_tabs_layout.addStretch(1)
        header_layout.addWidget(self.inspector_tabs_row)
        outer_layout.addWidget(header)

        # Pile de contenu (les widgets existants sont ajoutés plus bas
        # par appel direct ; ici on prépare la coquille).
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("properties_scroll_area")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        # Le viewport est un widget distinct : sans fond explicite il
        # peint la couleur claire par défaut de la plateforme, ce qui
        # rendait les libellés de l'inspecteur illisibles sur fond clair.
        self.scroll_area.viewport().setStyleSheet(
            f"QWidget {{ background: {COLORS['panel']}; }}"
        )

        content = QWidget()
        content.setObjectName("properties_content")
        content.setStyleSheet(
            f"QWidget#properties_content {{ background: {COLORS['panel']}; "
            f"color: {COLORS['text']}; }}"
        )
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.lg, Spacing.md, Spacing.lg, Spacing.md)
        layout.setSpacing(Spacing.md)

        # ----- Paramètres du projet ------------------------------------
        project_group = QGroupBox("Paramètres du projet")
        project_group.setStyleSheet(self.group_style())
        project_layout = QVBoxLayout(project_group)
        project_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        project_layout.setSpacing(Spacing.xs)

        project_fields = [
            ("État", "Aucun clip sélectionné"),
            ("Résolution", "1920 × 1080"),
            ("Format", "16:9"),
            ("Fréquence", "30 fps"),
            ("Fond", "#000000"),
        ]
        for field, value in project_fields:
            lbl = QLabel(f"{field} : {value}")
            lbl.setMinimumHeight(20)
            lbl.setStyleSheet(label_style(12, "text", 500))
            project_layout.addWidget(lbl)
        layout.addWidget(project_group)

        # ----- Clip sélectionné ---------------------------------------
        clip_group = QGroupBox("Clip sélectionné")
        clip_group.setStyleSheet(self.group_style())
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        clip_form.setSpacing(Spacing.xs)
        clip_form.setLabelAlignment(Qt.AlignLeft)
        self.clip_name = QLabel("Aucun clip sélectionné")
        self.clip_duration = QLabel("--")
        self.clip_position = QLabel("--")
        for label in (self.clip_name, self.clip_duration, self.clip_position):
            label.setStyleSheet(label_style(12, "muted", 500))
        self.clip_name.setStyleSheet(label_style(13, "text", 700))
        clip_form.addRow("Nom", self.clip_name)
        clip_form.addRow("Durée", self.clip_duration)
        clip_form.addRow("Position", self.clip_position)
        layout.addWidget(clip_group)

        # ----- Transition sélectionnée --------------------------------
        self.transition_group = QGroupBox("Transition")
        self.transition_group.setStyleSheet(self.group_style())
        transition_form = QFormLayout(self.transition_group)
        transition_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        transition_form.setSpacing(Spacing.xs)
        self.transition_type_combo = QComboBox()
        self.transition_type_combo.addItem("Fondu enchaîné", "crossfade")
        self.transition_type_combo.addItem("Fondu au noir", "fade_black")
        self.transition_type_combo.addItem("Balayage gauche", "wipe_left")
        self.transition_type_combo.addItem("Balayage droite", "wipe_right")
        self.transition_duration_spin = QDoubleSpinBox()
        self.transition_duration_spin.setRange(0.1, 5.0)
        self.transition_duration_spin.setDecimals(2)
        self.transition_duration_spin.setSingleStep(0.1)
        self.transition_duration_spin.setSuffix(" s")
        self.transition_from_label = QLabel("--")
        self.transition_to_label = QLabel("--")
        self.transition_track_label = QLabel("--")
        for label in (
            self.transition_from_label,
            self.transition_to_label,
            self.transition_track_label,
        ):
            label.setStyleSheet(label_style(11, "muted", 500))
        self.remove_transition_button = self._make_action_button(
            IconName.REMOVE, "Supprimer la transition", "Supprimer uniquement la transition"
        )
        transition_form.addRow("Type", self.transition_type_combo)
        transition_form.addRow("Durée", self.transition_duration_spin)
        transition_form.addRow("Clip sortant", self.transition_from_label)
        transition_form.addRow("Clip entrant", self.transition_to_label)
        transition_form.addRow("Piste", self.transition_track_label)
        transition_form.addRow("", self.remove_transition_button)
        self.transition_type_combo.currentIndexChanged.connect(self._on_transition_type_changed)
        self.transition_duration_spin.valueChanged.connect(self._on_transition_duration_changed)
        self.remove_transition_button.clicked.connect(self._on_transition_remove)
        layout.addWidget(self.transition_group)

        # ----- Couleur --------------------------------------------------
        color_group = QGroupBox("Couleur")
        color_group.setStyleSheet(self.group_style())
        color_form = QFormLayout(color_group)
        color_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        color_form.setSpacing(Spacing.xs)
        self.brightness_slider, brightness_row, self.brightness_value = self.make_slider(
            -100, 100, 0
        )
        self.contrast_slider, contrast_row, self.contrast_value = self.make_slider(
            -100, 100, 0
        )
        self.saturation_slider, saturation_row, self.saturation_value = self.make_slider(
            -100, 100, 0
        )
        color_form.addRow("Luminosité", brightness_row)
        color_form.addRow("Contraste", contrast_row)
        color_form.addRow("Saturation", saturation_row)
        layout.addWidget(color_group)
        for slider in (
            self.brightness_slider,
            self.contrast_slider,
            self.saturation_slider,
        ):
            slider.valueChanged.connect(self.update_color_values)
            slider.valueChanged.connect(update_color_effect)

        # ----- Audio ---------------------------------------------------
        audio_group = QGroupBox("Audio")
        audio_group.setStyleSheet(self.group_style())
        audio_form = QFormLayout(audio_group)
        audio_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        self.volume_slider, volume_row, self.volume_value = self.make_slider(
            0, 200, 100, suffix=" %"
        )
        audio_form.addRow("Volume", volume_row)
        layout.addWidget(audio_group)
        self.volume_slider.valueChanged.connect(update_volume)

        # ----- Vitesse et durée (tâche 18) -----------------------------
        self.speed_group = QGroupBox(translate("group.speed_and_duration"))
        self.speed_group.setStyleSheet(self.group_style())
        speed_form = QFormLayout(self.speed_group)
        speed_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        speed_form.setSpacing(Spacing.xs)
        speed_form.setLabelAlignment(Qt.AlignLeft)

        # Vitesse numérique
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setDecimals(2)
        self.speed_spinbox.setRange(MIN_SPEED, MAX_SPEED)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setMinimumWidth(70)
        self.speed_spinbox.setEnabled(False)
        self.speed_spinbox.valueChanged.connect(self._on_speed_changed)
        speed_form.addRow(translate("field.speed"), self.speed_spinbox)

        # Boutons de preset de vitesse
        speed_presets = QWidget()
        speed_presets_layout = QHBoxLayout(speed_presets)
        speed_presets_layout.setContentsMargins(0, 0, 0, 0)
        speed_presets_layout.setSpacing(Spacing.xs)

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

        speed_form.addRow("Presets", speed_presets)

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
        speed_form.addRow(translate("field.reverse"), self.reverse_button)

        # Freeze frame
        self.freeze_frame_button = self._make_action_button(
            None, translate("field.freeze_frame"), translate("tooltip.freeze_frame")
        )
        self.freeze_frame_button.setEnabled(False)
        self.freeze_frame_button.clicked.connect(self._on_freeze_frame_clicked)
        speed_form.addRow(translate("field.freeze_frame"), self.freeze_frame_button)

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
        self.freeze_duration_label.setStyleSheet(label_style(11, "muted", 500))
        self.freeze_duration_label.setVisible(False)

        freeze_row = QWidget()
        freeze_row_layout = QHBoxLayout(freeze_row)
        freeze_row_layout.setContentsMargins(0, 0, 0, 0)
        freeze_row_layout.setSpacing(Spacing.sm)
        freeze_row_layout.addWidget(self.freeze_duration_spinbox)
        freeze_row_layout.addWidget(self.freeze_duration_label)
        freeze_row.setVisible(False)
        speed_form.addRow(translate("field.freeze_duration"), freeze_row)
        self.freeze_duration_row = freeze_row

        # Bouton de réinitialisation
        self.reset_speed_button = self._make_action_button(
            None, translate("action.reset_speed"), translate("tooltip.reset_speed")
        )
        self.reset_speed_button.setEnabled(False)
        self.reset_speed_button.clicked.connect(self._on_time_remapping_reset)
        speed_form.addRow("", self.reset_speed_button)

        # Affichage des durées source et timeline
        self.source_duration_label = QLabel("Source: --")
        self.source_duration_label.setStyleSheet(label_style(11, "muted", 500))
        self.timeline_duration_label = QLabel("Timeline: --")
        self.timeline_duration_label.setStyleSheet(label_style(11, "muted", 500))
        duration_info = QWidget()
        duration_layout = QHBoxLayout(duration_info)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(Spacing.md)
        duration_layout.addWidget(self.source_duration_label)
        duration_layout.addWidget(self.timeline_duration_label)
        duration_layout.addStretch()
        speed_form.addRow("", duration_info)

        layout.addWidget(self.speed_group)

        # ----- Mouvement (tâche 13) ------------------------------------
        self.movement_group = QGroupBox("Mouvement")
        self.movement_group.setStyleSheet(self.group_style())
        movement_layout = QVBoxLayout(self.movement_group)
        movement_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        movement_layout.setSpacing(Spacing.xs)
        self._spin_boxes: dict[str, QDoubleSpinBox] = {}
        self._diamonds: dict[str, _DiamondButton] = {}
        self._slider_widgets: dict[str, QSlider] = {}
        for property_name in ANIMATABLE_PROPERTIES:
            low, high, step = _PROPERTY_RANGES[property_name]
            default = self._default_value_for(property_name)
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Spacing.xs)
            label = QLabel(self._human_label(property_name))
            label.setStyleSheet(label_style(11, "muted", 600))
            label.setFixedWidth(76)
            row_layout.addWidget(label)

            spin = QDoubleSpinBox()
            spin.setDecimals(2 if property_name != "rotation" else 1)
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(82)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._make_value_changed_handler(property_name))
            self._spin_boxes[property_name] = spin
            row_layout.addWidget(spin)

            if property_name in {"opacity", "scale", "rotation"}:
                slider = QSlider(Qt.Horizontal)
                slider_min, slider_max = self._slider_range(property_name)
                slider.setRange(slider_min, slider_max)
                slider.setValue(self._slider_position(property_name, default))
                slider.setMinimumWidth(82)
                slider.setEnabled(False)
                slider.valueChanged.connect(self._make_slider_handler(property_name))
                self._slider_widgets[property_name] = slider
                row_layout.addWidget(slider, 1)

            diamond = _DiamondButton(property_name)
            diamond.clicked.connect(
                lambda _checked=False, name=property_name: self._on_diamond_clicked(name)
            )
            diamond.setEnabled(False)
            self._diamonds[property_name] = diamond
            row_layout.addWidget(diamond)
            movement_layout.addWidget(row)

        reset_button = IconButton(
            icon=IconName.RESET,
            tooltip="Réinitialiser le mouvement",
            size=Sizes.icon_button,
        )
        reset_button.setText("  Réinitialiser le mouvement")
        reset_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        reset_button.clicked.connect(self._emit_reset)
        reset_button.setEnabled(False)
        reset_button.setMinimumWidth(0)
        reset_button.setSizePolicy(reset_button.sizePolicy().horizontalPolicy(),
                                   reset_button.sizePolicy().verticalPolicy())
        self.reset_movement_button = reset_button
        movement_layout.addWidget(reset_button)
        self._diamond_was_checked = {name: False for name in self._diamonds}
        self._allow_property_signals = True

        layout.addWidget(self.movement_group)
        self.movement_group.setEnabled(False)

        # ----- Audio (mixage non destructif) ---------------------------
        self.audio_group = self._build_audio_group()
        layout.addWidget(self.audio_group)
        self.audio_group.setEnabled(False)

        # ----- Sous-titre (style + contenu, tâche 24) ------------------
        self.subtitle_group = QGroupBox("Sous-titre")
        self.subtitle_group.setStyleSheet(self.group_style())
        subtitle_layout = QVBoxLayout(self.subtitle_group)
        subtitle_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        self.subtitle_editor = TextStyleEditor(self)
        subtitle_layout.addWidget(self.subtitle_editor)
        self.subtitle_editor.content_changed.connect(
            self._on_subtitle_content_changed
        )
        self.subtitle_editor.style_changed.connect(
            self._on_subtitle_style_changed
        )
        self.subtitle_editor.reset_requested.connect(
            self._on_subtitle_style_reset
        )
        # La visibilité initiale est pilotée par la sélection de clip :
        # voir ``_set_group_condition`` / ``_apply_group_visibility``.
        layout.addWidget(self.subtitle_group)

        # ----- Effets du clip (tâche 21) -------------------------------
        self.effects_group = self._build_effects_group()
        layout.addWidget(self.effects_group)

        # ----- Effets audio du clip (tâche 27) -------------------------
        self.audio_effects_group = self._build_audio_effects_group()
        layout.addWidget(self.audio_effects_group)
        layout.addStretch()

        # ----- Onglets : filtrage par catégorie -----------------------
        # Le panneau gagne une barre d'onglets : Inspecteur (par
        # défaut, tout visible), Couleur (color_group + project),
        # Effets (transition + mouvement), Audio (audio_group +
        # volume).
        self._tab_groups: dict[int, list[QWidget]] = {
            0: [
                project_group,
                clip_group,
                self.transition_group,
                color_group,
                self.movement_group,
                self.speed_group,
                audio_group,
                self.audio_group,
                self.subtitle_group,
                self.effects_group,
                self.audio_effects_group,
            ],
            1: [project_group, color_group],  # Couleur
            2: [project_group, self.movement_group,
                self.transition_group, self.speed_group,
                self.effects_group],  # Effets
            3: [project_group, audio_group, self.audio_group,
                self.audio_effects_group],  # Audio
        }
        all_groups = [project_group, clip_group, self.transition_group,
                      color_group, self.movement_group, self.speed_group,
                      audio_group, self.audio_group, self.subtitle_group,
                      self.effects_group]
        self._all_inspector_groups = all_groups
        # Certains groupes ont en plus une visibilité *conditionnelle*
        # pilotée par la sélection (``show_clip`` / ``show_transition``) :
        # le groupe Transition n'a de sens qu'une transition sélectionnée,
        # le groupe Sous-titre qu'un clip de sous-titres. Cette
        # condition est indépendante de l'onglet ; on la stocke à part
        # pour que le filtre d'onglets ne l'écrase pas. Un groupe n'est
        # visible que si l'onglet le contient *et* que sa propre
        # condition est remplie.
        self._group_conditional: dict[QWidget, bool] = {
            self.transition_group: False,
            self.subtitle_group: False,
        }
        self._active_inspector_tab: int = 0
        self._on_inspector_tab_changed(0)

    def _select_inspector_tab(self, index: int) -> None:
        """Bascule l'onglet actif de l'inspecteur."""
        for i, button in enumerate(self.inspector_tab_buttons):
            button.setChecked(i == index)
        self._on_inspector_tab_changed(index)

    def _set_group_condition(self, group: QWidget, allowed: bool) -> None:
        """Déclare si un groupe conditionnel est pertinent.

        N'agit pas sur le widget : c'est ``_apply_group_visibility`` qui
        combine l'onglet courant et cette condition.
        """
        self._group_conditional[group] = bool(allowed)
        self._apply_group_visibility()

    def _apply_group_visibility(self) -> None:
        """Point unique d'écriture de la visibilité des groupes.

        visible = (groupe présent dans l'onglet actif) ET
                  (pas de condition, ou condition remplie)
        """
        in_tab = set(self._tab_groups.get(self._active_inspector_tab,
                                          self._tab_groups[0]))
        for group in self._all_inspector_groups:
            conditional = self._group_conditional.get(group, True)
            group.setVisible(group in in_tab and conditional)

    def _on_inspector_tab_changed(self, row: int) -> None:
        """Filtre les groupes visibles selon l'onglet choisi."""
        self._active_inspector_tab = row
        self._apply_group_visibility()

    def _build_audio_group(self) -> QGroupBox:
        """Groupe de mixage du clip sélectionné.

        N'affiche que des réglages **réels** : gain, panoramique, fondus.
        Le panneau émet des intentions ; ``MainWindow`` les applique au
        modèle, refuse les pistes verrouillées et enregistre l'historique.
        """
        from ui.i18n import translate

        group = QGroupBox(translate("audio.gain"))
        group.setObjectName("audioGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        self.audio_gain_slider, self._audio_gain_row, self.audio_gain_value = (
            self.make_slider(int(MIN_GAIN_DB), int(MAX_GAIN_DB), 0, suffix=" dB")
        )
        self.audio_gain_slider.valueChanged.connect(
            lambda value: self.audio_gain_changed.emit(float(value))
        )
        group_layout.addWidget(self._audio_gain_row)

        self.audio_pan_slider, self._audio_pan_row, self.audio_pan_value = (
            self.make_slider(-100, 100, 0)
        )
        self.audio_pan_slider.valueChanged.connect(
            lambda value: self.audio_pan_changed.emit(value / 100.0)
        )
        group_layout.addWidget(self._audio_pan_row)

        # Fondus, en secondes, pas de 0,1 s.
        self._fade_spins: dict[str, QDoubleSpinBox] = {}
        for which in ("in", "out"):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Spacing.sm)
            label = QLabel(
                translate("audio.fade_in")
                if which == "in"
                else translate("audio.fade_out")
            )
            label.setStyleSheet(label_style(11, "muted", 600))
            label.setMinimumWidth(84)
            row_layout.addWidget(label)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 600.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
            spin.setMinimumWidth(80)
            spin.valueChanged.connect(
                lambda value, w=which: self.audio_fade_changed.emit(w, float(value))
            )
            self._fade_spins[which] = spin
            row_layout.addWidget(spin, 1)
            group_layout.addWidget(row)

        self.reset_fades_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("audio.reset_fades"),
            size=Sizes.icon_button,
        )
        self.reset_fades_button.setText(f"  {translate('audio.reset_fades')}")
        self.reset_fades_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.reset_fades_button.setMinimumHeight(Sizes.button_sm)
        self.reset_fades_button.clicked.connect(
            lambda: self.audio_fades_reset.emit()
        )
        group_layout.addWidget(self.reset_fades_button)
        return group

    def set_audio_clip(self, model, *, locked: bool = False) -> None:
        """Affiche les réglages audio d'un clip, sans émettre de signal.

        Args:
            model: le :class:`~core.project_model.Clip` sélectionné, ou
                ``None`` pour masquer le groupe.
            locked: la piste est verrouillée : tout est désactivé.
        """
        from ui.i18n import translate

        if model is None:
            self.audio_group.setEnabled(False)
            return
        enabled = not locked
        self.audio_group.setEnabled(enabled)
        for widget in (
            self.audio_gain_slider,
            self.audio_pan_slider,
            self.reset_fades_button,
            *self._fade_spins.values(),
        ):
            widget.setEnabled(enabled)
        for widget in (self.audio_gain_slider, self.audio_pan_slider):
            widget.blockSignals(True)
        self.audio_gain_slider.setValue(int(round(float(model.gain_db))))
        self.audio_pan_slider.setValue(int(round(float(model.pan) * 100)))
        for widget in (self.audio_gain_slider, self.audio_pan_slider):
            widget.blockSignals(False)
        self.audio_gain_value.setText(f"{model.gain_db:+.1f} dB")
        self.audio_pan_value.setText(
            translate("mixer.pan_center")
            if abs(model.pan) < 0.02
            else f"{model.pan * 100:+.0f}"
        )
        for which, spin in self._fade_spins.items():
            spin.blockSignals(True)
            spin.setValue(float(getattr(model, f"fade_{which}", 0.0)))
            spin.blockSignals(False)

    # ------------------------------------------------------------------
    # Effets du clip (tâche 21)
    # ------------------------------------------------------------------

    def _build_effects_group(self) -> QGroupBox:
        """Groupe « Effets » : liste, actions et réglages du clip vidéo.

        L'inspecteur n'écrit jamais dans le modèle : il publie des
        intentions via ses signaux, que ``MainWindow`` traduit en
        opérations métier puis en entrée d'historique.
        """
        group = QGroupBox(translate("effects.section"))
        group.setObjectName("effectsGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        self.effects_hint = QLabel(translate("effects.no_clip"))
        self.effects_hint.setWordWrap(True)
        self.effects_hint.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.effects_hint)

        self.effect_add_row = QWidget()
        add_layout = QHBoxLayout(self.effect_add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(Spacing.xs)
        self.effect_type_combo = QComboBox()
        self.effect_type_combo.setObjectName("effectType")
        self.effect_type_combo.setFocusPolicy(Qt.NoFocus)
        self.effect_type_combo.setStyleSheet(
            f"QComboBox#effectType {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
        )
        self.effect_add_button = self._make_effect_button(
            translate("effects.add"), self._on_effect_add
        )
        add_layout.addWidget(self.effect_type_combo, 1)
        add_layout.addWidget(self.effect_add_button)
        group_layout.addWidget(self.effect_add_row)

        self.effects_list = QListWidget()
        self.effects_list.setObjectName("effectsList")
        self.effects_list.setFixedHeight(104)
        self.effects_list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']};"
            f" color: {COLORS['accent']}; }}"
        )
        self.effects_list.currentRowChanged.connect(
            self._on_effect_selection_changed
        )
        group_layout.addWidget(self.effects_list)

        self.effects_empty_label = QLabel(translate("effects.empty"))
        self.effects_empty_label.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.effects_empty_label)

        # --- Bandeau d'actions ---------------------------------------
        self.effect_buttons_row = QWidget()
        buttons_layout = QHBoxLayout(self.effect_buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(Spacing.xs)
        self.effect_toggle_button = self._make_effect_button(
            translate("effects.disable"), self._on_effect_toggle
        )
        self.effect_remove_button = self._make_effect_button(
            translate("effects.remove"), self._on_effect_remove
        )
        self.effect_up_button = self._make_effect_button(
            translate("effects.move_up"), lambda: self._emit_effect_move(-1)
        )
        self.effect_down_button = self._make_effect_button(
            translate("effects.move_down"), lambda: self._emit_effect_move(1)
        )
        for button in (
            self.effect_toggle_button,
            self.effect_remove_button,
            self.effect_up_button,
            self.effect_down_button,
        ):
            buttons_layout.addWidget(button)
        group_layout.addWidget(self.effect_buttons_row)

        # --- Réglages de l'effet sélectionné --------------------------
        self.effect_parameters_container = QWidget()
        self.effect_parameters_layout = QVBoxLayout(
            self.effect_parameters_container
        )
        self.effect_parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.effect_parameters_layout.setSpacing(Spacing.xs)
        group_layout.addWidget(self.effect_parameters_container)

        group.setEnabled(False)
        return group

    def _make_effect_button(self, text: str, handler) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("effectAction")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setMinimumHeight(Sizes.button_sm)
        button.setStyleSheet(
            f"QPushButton#effectAction {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QPushButton#effectAction:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton#effectAction:disabled {{ color: {COLORS['muted']}; }}"
        )
        button.clicked.connect(lambda _checked=False: handler())
        return button

    def update_effects_from_clip(self, effects, track_type=None) -> None:
        """Reconstruit la section Effets depuis l'état du ``Project``.

        Args:
            effects: liste des :class:`ClipEffect` du clip sélectionné.
            track_type: type de piste (``"video"``, ``"audio"``...). Toute
                valeur autre que ``"video"`` affiche un état non éditable.
        """
        self._current_effects = list(effects or [])
        if track_type != "video":
            self._selected_effect_id = None
            self._populate_effects_list()
            self._clear_effect_parameters()
            self.effects_hint.setText(
                translate("effects.no_clip")
                if track_type is None
                else translate("effects.video_only")
            )
            self.effects_hint.setVisible(True)
            self.effects_list.setVisible(False)
            self.effect_add_row.setVisible(False)
            self.effect_buttons_row.setVisible(False)
            self.effect_parameters_container.setVisible(False)
            self.effects_empty_label.setVisible(False)
            self.effects_group.setEnabled(False)
            return

        self.effects_hint.setVisible(False)
        self.effect_add_row.setVisible(True)
        self.effects_list.setVisible(True)
        self.effect_buttons_row.setVisible(True)
        self.effect_parameters_container.setVisible(True)
        self.effects_group.setEnabled(True)
        self._populate_effects_list()
        self._refresh_effect_catalog()

    def _refresh_effect_catalog(self) -> None:
        """Propose les effets compatibles qui ne sont pas déjà uniques."""
        current = self.effect_type_combo.currentData()
        existing = {effect.type for effect in self._current_effects}
        self.effect_type_combo.blockSignals(True)
        self.effect_type_combo.clear()
        for effect_type in EffectType:
            if is_single_instance(effect_type) and effect_type in existing:
                continue
            self.effect_type_combo.addItem(
                translate(f"effects.name.{effect_type.value}"), effect_type.value
            )
        if current:
            index = self.effect_type_combo.findData(current)
            if index >= 0:
                self.effect_type_combo.setCurrentIndex(index)
        self.effect_type_combo.blockSignals(False)
        self.effect_add_button.setEnabled(self.effect_type_combo.count() > 0)

    def _populate_effects_list(self) -> None:
        """Remplit la liste des effets et restaure la sélection."""
        self.effects_list.blockSignals(True)
        self.effects_list.clear()
        for effect in self._current_effects:
            name = translate(f"effects.name.{effect.type.value}")
            if not effect.enabled:
                name = f"{name} · {translate('effects.disabled_suffix')}"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, effect.id)
            self.effects_list.addItem(item)
        self.effects_list.blockSignals(False)

        ids = [effect.id for effect in self._current_effects]
        if self._selected_effect_id not in ids:
            self._selected_effect_id = ids[0] if ids else None
        self.effects_empty_label.setVisible(not ids)
        row = ids.index(self._selected_effect_id) if self._selected_effect_id else -1
        self.effects_list.blockSignals(True)
        self.effects_list.setCurrentRow(row)
        self.effects_list.blockSignals(False)
        self._refresh_effect_controls()

    def _selected_effect(self) -> ClipEffect | None:
        for effect in self._current_effects:
            if effect.id == self._selected_effect_id:
                return effect
        return None

    def _on_effect_selection_changed(self, row: int) -> None:
        item = self.effects_list.item(row)
        self._selected_effect_id = (
            item.data(Qt.UserRole) if item is not None else None
        )
        self._refresh_effect_controls()

    def _refresh_effect_controls(self) -> None:
        """Synchronise actions et paramètres avec l'effet sélectionné."""
        effect = self._selected_effect()
        has_effect = effect is not None
        for button in (
            self.effect_toggle_button,
            self.effect_remove_button,
            self.effect_up_button,
            self.effect_down_button,
        ):
            button.setEnabled(has_effect)
        if effect is None:
            self._clear_effect_parameters()
            return
        self.effect_toggle_button.setText(
            translate("effects.disable")
            if effect.enabled
            else translate("effects.enable")
        )
        index = next(
            i for i, e in enumerate(self._current_effects) if e.id == effect.id
        )
        self.effect_up_button.setEnabled(index > 0)
        self.effect_down_button.setEnabled(index < len(self._current_effects) - 1)
        self._build_effect_parameters(effect)

    def _clear_effect_parameters(self) -> None:
        """Vide la zone de réglages de l'effet sélectionné."""
        self._effect_param_widgets = {}
        while self.effect_parameters_layout.count():
            item = self.effect_parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _build_effect_parameters(self, effect: ClipEffect) -> None:
        """Construit un contrôle par paramètre déclaré pour ``effect``."""
        self._clear_effect_parameters()
        specs = parameter_specs(effect.type)
        if not specs:
            label = QLabel(translate("effects.no_parameters"))
            label.setStyleSheet(label_style(11, "muted", 500))
            self.effect_parameters_layout.addWidget(label)
            return
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        form.setLabelAlignment(Qt.AlignLeft)
        for spec in specs:
            spin = QDoubleSpinBox()
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setMinimumWidth(80)
            spin.setValue(float(effect.params.get(spec.name, spec.default)))
            spin.valueChanged.connect(
                lambda value, name=spec.name, fx_id=effect.id: (
                    self._on_effect_parameter_changed(fx_id, name, float(value))
                )
            )
            self._effect_param_widgets[spec.name] = spin
            label = QLabel(translate(f"effects.param.{spec.name}"))
            label.setStyleSheet(label_style(11, "muted", 600))
            form.addRow(label, spin)
        wrapper = QWidget()
        wrapper.setLayout(form)
        self.effect_parameters_layout.addWidget(wrapper)

    def _on_effect_parameter_changed(
        self, effect_id: str, name: str, value: float
    ) -> None:
        if not self._allow_effect_signals or self.selected_clip is None:
            return
        self.effect_parameter_changed.emit(
            self.selected_clip.id, effect_id, name, float(value)
        )

    def _on_effect_toggle(self) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_enabled_changed.emit(
            self.selected_clip.id, effect.id, not effect.enabled
        )

    def _on_effect_add(self) -> None:
        if self.selected_clip is None:
            return
        effect_type = self.effect_type_combo.currentData()
        if isinstance(effect_type, str):
            self.effect_add_requested.emit(self.selected_clip.id, effect_type)

    def _on_effect_remove(self) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_removed.emit(self.selected_clip.id, effect.id)

    def _emit_effect_move(self, delta: int) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_moved.emit(self.selected_clip.id, effect.id, int(delta))

    # ------------------------------------------------------------------
    # Effets audio non destructifs (tâche 27)
    # ------------------------------------------------------------------

    def _build_audio_effects_group(self) -> QGroupBox:
        """Groupe « Effets audio » : rack avec liste et paramètres.

        Le rack est strictement aligné sur la structure du rack visuel :
        même nomenclature de contrôles, mêmes signaux ``*_requested``
        (les noms sont préfixés ``audio_effect_*`` pour rester
        distincts en log et permettre un routage simple côté MainWindow).
        """
        from ui.i18n import translate as _tr

        group = QGroupBox(_tr("audio_effects.section"))
        group.setObjectName("audioEffectsGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        # Astuce si aucun média sélectionné.
        self.audio_effects_hint = QLabel(_tr("audio_effects.no_clip"))
        self.audio_effects_hint.setWordWrap(True)
        self.audio_effects_hint.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.audio_effects_hint)

        # Ligne d'ajout : combo + bouton.
        self.audio_effect_add_row = QWidget()
        add_layout = QHBoxLayout(self.audio_effect_add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(Spacing.xs)
        self.audio_effect_type_combo = QComboBox()
        self.audio_effect_type_combo.setObjectName("audioEffectType")
        self.audio_effect_type_combo.setFocusPolicy(Qt.NoFocus)
        self.audio_effect_type_combo.setStyleSheet(
            f"QComboBox#audioEffectType {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
        )
        self.audio_effect_add_button = self._make_audio_effect_button(
            _tr("audio_effects.add"), self._on_audio_effect_add
        )
        add_layout.addWidget(self.audio_effect_type_combo, 1)
        add_layout.addWidget(self.audio_effect_add_button)
        group_layout.addWidget(self.audio_effect_add_row)

        # Liste des effets.
        self.audio_effects_list = QListWidget()
        self.audio_effects_list.setObjectName("audioEffectsList")
        self.audio_effects_list.setFixedHeight(104)
        self.audio_effects_list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']};"
            f" color: {COLORS['accent']}; }}"
        )
        self.audio_effects_list.currentRowChanged.connect(
            self._on_audio_effect_selection_changed
        )
        group_layout.addWidget(self.audio_effects_list)

        # Placeholder "aucun effet audio".
        self.audio_effects_empty_label = QLabel(_tr("audio_effects.empty"))
        self.audio_effects_empty_label.setStyleSheet(
            label_style(11, "muted", 500)
        )
        group_layout.addWidget(self.audio_effects_empty_label)

        # Boutons d'action.
        self.audio_effect_buttons_row = QWidget()
        buttons_layout = QHBoxLayout(self.audio_effect_buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(Spacing.xs)
        self.audio_effect_toggle_button = self._make_audio_effect_button(
            _tr("audio_effects.disable"), self._on_audio_effect_toggle
        )
        self.audio_effect_remove_button = self._make_audio_effect_button(
            _tr("audio_effects.remove"), self._on_audio_effect_remove
        )
        self.audio_effect_up_button = self._make_audio_effect_button(
            _tr("audio_effects.move_up"), lambda: self._emit_audio_effect_move(-1)
        )
        self.audio_effect_down_button = self._make_audio_effect_button(
            _tr("audio_effects.move_down"), lambda: self._emit_audio_effect_move(1)
        )
        for button in (
            self.audio_effect_toggle_button,
            self.audio_effect_remove_button,
            self.audio_effect_up_button,
            self.audio_effect_down_button,
        ):
            buttons_layout.addWidget(button)
        group_layout.addWidget(self.audio_effect_buttons_row)

        # Zone de paramètres.
        self.audio_effect_parameters_container = QWidget()
        self.audio_effect_parameters_layout = QVBoxLayout(
            self.audio_effect_parameters_container
        )
        self.audio_effect_parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_effect_parameters_layout.setSpacing(Spacing.xs)
        group_layout.addWidget(self.audio_effect_parameters_container)

        group.setEnabled(False)
        return group

    def _make_audio_effect_button(self, text: str, handler) -> QPushButton:
        """Bouton d'action réutilisé dans le rack d'effets audio."""
        button = QPushButton(text)
        button.setObjectName("audioEffectAction")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setMinimumHeight(Sizes.button_sm)
        button.setStyleSheet(
            f"QPushButton#audioEffectAction {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QPushButton#audioEffectAction:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton#audioEffectAction:disabled {{ color: {COLORS['muted']}; }}"
        )
        button.clicked.connect(lambda _checked=False: handler())
        return button

    def update_audio_effects_from_clip(self, effects, track_type=None) -> None:
        """Synchronise le rack d'effets audio avec l'état du clip.

        Args:
            effects: liste des :class:`AudioEffect` du clip sélectionné.
            track_type: type de piste. Le rack reste éditable pour les
                pistes ``video`` (avec ``has_audio=True``) et ``audio`` ;
                toute autre valeur (notamment ``subtitle``) désactive
                l'édition.
        """
        from ui.i18n import translate as _tr

        editable = track_type in ("video", "audio")
        self._current_audio_effects = list(effects or [])
        if not editable:
            self._selected_audio_effect_id = None
            self._populate_audio_effects_list()
            self._clear_audio_effect_parameters()
            self.audio_effects_hint.setText(_tr("audio_effects.no_clip"))
            self.audio_effects_hint.setVisible(True)
            self.audio_effect_add_row.setVisible(False)
            self.audio_effects_list.setVisible(False)
            self.audio_effect_buttons_row.setVisible(False)
            self.audio_effect_parameters_container.setVisible(False)
            self.audio_effects_empty_label.setVisible(False)
            self.audio_effects_group.setEnabled(False)
            return

        self.audio_effects_hint.setVisible(False)
        self.audio_effect_add_row.setVisible(True)
        self.audio_effects_list.setVisible(True)
        self.audio_effect_buttons_row.setVisible(True)
        self.audio_effect_parameters_container.setVisible(True)
        self.audio_effects_group.setEnabled(True)
        self._populate_audio_effects_list()
        self._refresh_audio_effect_catalog()

    def _refresh_audio_effect_catalog(self) -> None:
        """Propose tous les types d'effets audio dans le combo d'ajout."""
        from ui.i18n import translate as _tr

        current = self.audio_effect_type_combo.currentData()
        self.audio_effect_type_combo.blockSignals(True)
        self.audio_effect_type_combo.clear()
        for audio_type in AudioEffectType:
            self.audio_effect_type_combo.addItem(
                _tr(f"audio_effects.preset.{audio_type.value}.name"),
                audio_type.value,
            )
        if current:
            index = self.audio_effect_type_combo.findData(current)
            if index >= 0:
                self.audio_effect_type_combo.setCurrentIndex(index)
        self.audio_effect_type_combo.blockSignals(False)
        self.audio_effect_add_button.setEnabled(
            self.audio_effect_type_combo.count() > 0
        )

    def _populate_audio_effects_list(self) -> None:
        """Remplit la liste des effets audio et restaure la sélection."""
        from ui.i18n import translate as _tr

        self.audio_effects_list.blockSignals(True)
        self.audio_effects_list.clear()
        for effect in self._current_audio_effects:
            name = _tr(f"audio_effects.preset.{effect.type.value}.name")
            if not effect.enabled:
                name = f"{name} · {_tr('effects.disabled_suffix')}"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, effect.id)
            self.audio_effects_list.addItem(item)
        self.audio_effects_list.blockSignals(False)

        ids = [effect.id for effect in self._current_audio_effects]
        if self._selected_audio_effect_id not in ids:
            self._selected_audio_effect_id = ids[0] if ids else None
        self.audio_effects_empty_label.setVisible(not ids)
        row = (
            ids.index(self._selected_audio_effect_id)
            if self._selected_audio_effect_id else -1
        )
        self.audio_effects_list.blockSignals(True)
        self.audio_effects_list.setCurrentRow(row)
        self.audio_effects_list.blockSignals(False)
        self._refresh_audio_effect_controls()

    def _selected_audio_effect(self) -> AudioEffect | None:
        for effect in self._current_audio_effects:
            if effect.id == self._selected_audio_effect_id:
                return effect
        return None

    def _on_audio_effect_selection_changed(self, row: int) -> None:
        item = self.audio_effects_list.item(row)
        self._selected_audio_effect_id = (
            item.data(Qt.UserRole) if item is not None else None
        )
        self._refresh_audio_effect_controls()

    def _refresh_audio_effect_controls(self) -> None:
        """Synchronise les actions et paramètres avec l'effet audio choisi."""
        from ui.i18n import translate as _tr

        effect = self._selected_audio_effect()
        has_effect = effect is not None
        for button in (
            self.audio_effect_toggle_button,
            self.audio_effect_remove_button,
            self.audio_effect_up_button,
            self.audio_effect_down_button,
        ):
            button.setEnabled(has_effect)
        if effect is None:
            self._clear_audio_effect_parameters()
            return
        self.audio_effect_toggle_button.setText(
            _tr("audio_effects.disable")
            if effect.enabled else _tr("audio_effects.enable")
        )
        index = next(
            i for i, e in enumerate(self._current_audio_effects)
            if e.id == effect.id
        )
        self.audio_effect_up_button.setEnabled(index > 0)
        self.audio_effect_down_button.setEnabled(
            index < len(self._current_audio_effects) - 1
        )
        self._build_audio_effect_parameters(effect)

    def _clear_audio_effect_parameters(self) -> None:
        """Vide la zone de réglages de l'effet audio sélectionné."""
        self._audio_effect_param_widgets = {}
        while self.audio_effect_parameters_layout.count():
            item = self.audio_effect_parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _build_audio_effect_parameters(self, effect: AudioEffect) -> None:
        """Construit un contrôle par paramètre déclaré pour ``effect``."""
        from ui.i18n import translate as _tr

        self._clear_audio_effect_parameters()
        specs = audio_parameter_specs(effect.type)
        if not specs:
            label = QLabel(_tr("effects.no_parameters"))
            label.setStyleSheet(label_style(11, "muted", 500))
            self.audio_effect_parameters_layout.addWidget(label)
            return
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        form.setLabelAlignment(Qt.AlignLeft)
        for spec in specs:
            spin = QDoubleSpinBox()
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setMinimumWidth(80)
            spin.setValue(float(effect.params.get(spec.name, spec.default)))
            spin.valueChanged.connect(
                lambda value, name=spec.name, fx_id=effect.id: (
                    self._on_audio_effect_parameter_changed(fx_id, name, float(value))
                )
            )
            self._audio_effect_param_widgets[spec.name] = spin
            label = QLabel(_tr(f"audio_effects.param.{spec.name}"))
            label.setStyleSheet(label_style(11, "muted", 600))
            form.addRow(label, spin)
        wrapper = QWidget()
        wrapper.setLayout(form)
        self.audio_effect_parameters_layout.addWidget(wrapper)

    def _on_audio_effect_parameter_changed(
        self, effect_id: str, name: str, value: float
    ) -> None:
        if (
            not self._allow_audio_effect_signals
            or self.selected_clip is None
        ):
            return
        self.audio_effect_parameter_changed.emit(
            self.selected_clip.id, effect_id, name, float(value)
        )

    def _on_audio_effect_toggle(self) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_enabled_changed.emit(
            self.selected_clip.id, effect.id, not effect.enabled
        )

    def _on_audio_effect_add(self) -> None:
        if self.selected_clip is None:
            return
        effect_type = self.audio_effect_type_combo.currentData()
        if isinstance(effect_type, str):
            self.audio_effect_add_requested.emit(
                self.selected_clip.id, effect_type
            )

    def _on_audio_effect_remove(self) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_removed.emit(self.selected_clip.id, effect.id)

    def _emit_audio_effect_move(self, delta: int) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_moved.emit(
            self.selected_clip.id, effect.id, int(delta)
        )

    # ------------------------------------------------------------------
    # Helpers privés : construction
    # ------------------------------------------------------------------

    def _make_action_button(
        self, icon: IconName, text: str, tooltip: str
    ) -> IconButton:
        """Construit un bouton d'action « pleine largeur » aligné sur la grille."""
        button = IconButton(
            icon=icon, tooltip=tooltip, size=Sizes.icon_button
        )
        button.setText(f"  {text}")
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        # On force une taille minimale confortable ; le bouton peut
        # s'étirer mais jamais descendre en dessous.
        button.setMinimumHeight(Sizes.button_md)
        button.setSizePolicy(
            button.sizePolicy().horizontalPolicy().Expanding
            if False
            else button.sizePolicy().horizontalPolicy(),
            button.sizePolicy().verticalPolicy(),
        )
        return button

    # ------------------------------------------------------------------
    # Helpers privés : MOUVEMENT
    # ------------------------------------------------------------------

    @staticmethod
    def _human_label(property_name: str) -> str:
        return {
            "position_x": "X",
            "position_y": "Y",
            "scale": "Échelle",
            "rotation": "Rotation",
            "opacity": "Opacité",
        }[property_name]

    @staticmethod
    def _default_value_for(property_name: str) -> float:
        return {
            "position_x": 0.0,
            "position_y": 0.0,
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
        }[property_name]

    def _make_value_changed_handler(self, property_name: str):
        def _handler(value: float) -> None:
            self._on_property_changed(property_name, float(value))
        return _handler

    def _push_signal_block(self) -> None:
        self._signal_block_depth += 1
        self._allow_property_signals = False

    def _pop_signal_block(self) -> None:
        self._signal_block_depth = max(0, self._signal_block_depth - 1)
        self._allow_property_signals = self._signal_block_depth == 0

    @staticmethod
    def _slider_range(property_name: str) -> tuple[int, int]:
        low, high, _step = _PROPERTY_RANGES[property_name]
        if property_name == "rotation":
            return int(low), int(high)
        return int(round(low * 100)), int(round(high * 100))

    @staticmethod
    def _slider_position(property_name: str, value: float) -> int:
        low, high = PropertiesPanel._slider_range(property_name)
        if property_name == "rotation":
            position = int(round(value))
        else:
            position = int(round(value * 100))
        return max(low, min(high, position))

    def _make_slider_handler(self, property_name: str):
        def _handler(value: int) -> None:
            if not self._allow_property_signals:
                return
            if property_name == "rotation":
                float_value = float(value)
            else:
                float_value = float(value) / 100.0
            spin = self._spin_boxes.get(property_name)
            if spin is not None:
                self._push_signal_block()
                try:
                    spin.setValue(float_value)
                finally:
                    self._pop_signal_block()
            self._on_property_changed(property_name, float_value)
        return _handler

    def _on_property_changed(self, property_name: str, value: float) -> None:
        if not self._allow_property_signals or self.selected_clip is None:
            return
        value = float(value)
        self._sync_sibling_widgets(property_name, value)
        local = self._display_local_time()
        mode, time_seconds = self._edit_target(property_name, local)
        clip_id = self.selected_clip.id
        if mode == "base":
            self.transform_changed.emit(clip_id, property_name, value)
            return
        self.keyframe_added.emit(clip_id, property_name, time_seconds, value)

    def _sync_sibling_widgets(self, property_name: str, value: float) -> None:
        slider = self._slider_widgets.get(property_name)
        if slider is None:
            return
        position = self._slider_position(property_name, value)
        if slider.value() == position:
            return
        self._push_signal_block()
        try:
            slider.setValue(position)
        finally:
            self._pop_signal_block()

    def _on_diamond_clicked(self, property_name: str) -> None:
        shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self._apply_diamond_action(property_name, shift=shift)

    def _apply_diamond_action(self, property_name: str, *, shift: bool) -> None:
        if self.selected_clip is None:
            return
        if not self._playhead_inside_clip():
            self._restore_diamond(property_name)
            return
        local = self._display_local_time()
        matched = self._matching_keyframe(property_name, local)
        if shift:
            if matched is None:
                self._restore_diamond(property_name)
                return
            self._set_diamond_checked(property_name, False)
            self.keyframe_removed.emit(
                self.selected_clip.id,
                property_name,
                float(matched.time_seconds),
            )
            return
        spin = self._spin_boxes.get(property_name)
        if spin is None:
            return
        time_seconds = float(matched.time_seconds) if matched is not None else float(local)
        self._set_diamond_checked(property_name, True)
        self.keyframe_added.emit(
            self.selected_clip.id,
            property_name,
            time_seconds,
            float(spin.value()),
        )

    def _set_diamond_checked(self, property_name: str, checked: bool) -> None:
        diamond = self._diamonds[property_name]
        diamond.blockSignals(True)
        diamond.setChecked(checked)
        diamond.blockSignals(False)
        self._diamond_was_checked[property_name] = checked

    def _restore_diamond(self, property_name: str) -> None:
        matched = self._matching_keyframe(property_name, self._display_local_time())
        self._set_diamond_checked(property_name, matched is not None)

    def _sync_diamond_state_memory(self) -> None:
        self._diamond_was_checked = {
            name: diamond.isChecked() for name, diamond in self._diamonds.items()
        }

    def _clip_bounds(self) -> tuple[float, float] | None:
        clip = self.selected_clip
        if clip is None:
            return None
        start = float(clip.start)
        end = float(clip.end)
        if end < start:
            end = start
        return start, end

    def _clip_duration(self) -> float:
        bounds = self._clip_bounds()
        if bounds is None:
            return 0.0
        return bounds[1] - bounds[0]

    def _display_local_time(self) -> float:
        bounds = self._clip_bounds()
        if bounds is None:
            return 0.0
        start, end = bounds
        playhead = float(self._current_playhead_seconds)
        if playhead < start:
            return 0.0
        if playhead > end:
            return end - start
        return playhead - start

    def _playhead_inside_clip(self) -> bool:
        bounds = self._clip_bounds()
        if bounds is None:
            return False
        start, end = bounds
        playhead = float(self._current_playhead_seconds)
        return start - 1e-6 <= playhead <= end + 1e-6

    def _matching_keyframe(
        self, property_name: str, local_time: float
    ) -> TransformKeyframe | None:
        best: TransformKeyframe | None = None
        best_distance = _KEYFRAME_MATCH_TOLERANCE
        for keyframe in self._current_keyframes:
            if keyframe.property_name != property_name:
                continue
            distance = abs(keyframe.time_seconds - local_time)
            if distance <= best_distance:
                best = keyframe
                best_distance = distance
        return best

    def _edit_target(self, property_name: str, local_time: float) -> tuple[str, float]:
        matched = self._matching_keyframe(property_name, local_time)
        if matched is not None:
            return "keyframe", float(matched.time_seconds)
        keyed = [
            keyframe.time_seconds
            for keyframe in self._current_keyframes
            if keyframe.property_name == property_name
        ]
        if not keyed or local_time < min(keyed) - 1e-9:
            return "base", local_time
        duration = self._clip_duration()
        if local_time > duration:
            local_time = duration
        return "keyframe", local_time

    def _emit_reset(self) -> None:
        if self.selected_clip is None:
            return
        self.transform_reset.emit(self.selected_clip.id)

    # ------------------------------------------------------------------
    # Helpers de mise à jour depuis l'extérieur
    # ------------------------------------------------------------------

    def update_transform_from_clip(
        self,
        transform: ClipTransform,
        keyframes: list[TransformKeyframe],
        playhead_seconds: float = 0.0,
    ) -> None:
        self._current_playhead_seconds = float(playhead_seconds)
        self._current_transform = transform
        self._current_keyframes = list(keyframes)
        if self.selected_clip is None:
            return
        self._apply_motion_fields(transform, self._current_keyframes)

    def _apply_motion_fields(
        self,
        transform: ClipTransform | None,
        keyframes: list[TransformKeyframe],
    ) -> None:
        if self.selected_clip is None:
            return
        self._push_signal_block()
        try:
            if transform is not None:
                local_time = self._display_local_time()
                evaluated = evaluate_transform(
                    transform,
                    keyframes,
                    local_time,
                    self._clip_duration(),
                )
                for property_name in ANIMATABLE_PROPERTIES:
                    matched = self._matching_keyframe(property_name, local_time)
                    if matched is not None:
                        value = float(matched.value)
                    else:
                        value = float(getattr(evaluated, property_name))
                    spin = self._spin_boxes.get(property_name)
                    if spin is not None:
                        spin.setValue(value)
                    slider = self._slider_widgets.get(property_name)
                    if slider is not None:
                        slider.setValue(self._slider_position(property_name, value))
            self._sync_diamonds()
        finally:
            self._pop_signal_block()

    def _sync_diamonds(self) -> None:
        local = self._display_local_time()
        for property_name, diamond in self._diamonds.items():
            diamond.blockSignals(True)
            diamond.setChecked(
                self._matching_keyframe(property_name, local) is not None
            )
            diamond.blockSignals(False)
        self._sync_diamond_state_memory()

    def refresh_keyframe_diamonds(
        self,
        keyframes: list[TransformKeyframe],
        playhead_seconds: float,
        transform: ClipTransform | None = None,
    ) -> None:
        self._current_playhead_seconds = float(playhead_seconds)
        self._current_keyframes = list(keyframes)
        if transform is not None:
            self._current_transform = transform
        if self.selected_clip is None:
            return
        self._apply_motion_fields(self._current_transform, self._current_keyframes)

    @staticmethod
    def group_style():
        return (
            f"QGroupBox {{ color: {COLORS['muted_strong']}; "
            f"border: 1px solid {COLORS['border']}; border-radius: 6px; "
            f"margin-top: 12px; padding-top: 12px; font-weight: 600; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; "
            f"padding: 0 6px; color: {COLORS['muted_strong']}; }}"
        )

    @staticmethod
    def make_slider(minimum, maximum, value, suffix=""):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setMinimumWidth(60)
        value_label = QLabel(f"{value}{suffix}")
        value_label.setFixedWidth(40)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setStyleSheet(label_style(11, "muted", 600))
        container = QWidget()
        container.setMinimumWidth(140)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.sm)
        row.addWidget(slider, 1)
        row.addWidget(value_label, 0)
        slider._value_label = value_label
        slider._suffix = suffix
        return slider, container, value_label

    def update_color_values(self):
        self.brightness_value.setText(str(self.brightness_slider.value()))
        self.contrast_value.setText(str(self.contrast_slider.value()))
        self.saturation_value.setText(str(self.saturation_slider.value()))

    def show_clip(self, view):
        pending_transform = None
        pending_keyframes: list[TransformKeyframe] | None = None
        self._push_signal_block()
        try:
            self.selected_transition_id = None
            # Changer de clip invalide toute transition affichée.
            self._set_group_condition(self.transition_group, False)
            if view is None:
                self.selected_clip = None
                self.selected_clip_track_type = None
                self._current_transform = None
                self._current_keyframes = []
                self.clip_name.setText("Aucun clip sélectionné")
                self.clip_duration.setText("--")
                self.clip_position.setText("--")
                self._set_group_condition(self.subtitle_group, False)
                self.movement_group.setEnabled(False)
                for name, spin in self._spin_boxes.items():
                    spin.setEnabled(False)
                    spin.setValue(self._default_value_for(name))
                for name, slider in self._slider_widgets.items():
                    slider.setEnabled(False)
                    slider.setValue(
                        self._slider_position(name, self._default_value_for(name))
                    )
                for name, diamond in self._diamonds.items():
                    diamond.setEnabled(False)
                    self._set_diamond_checked(name, False)
                self.reset_movement_button.setEnabled(False)
                # Time remapping
                self.speed_group.setEnabled(False)
                # Effets : aucun clip vidéo sélectionné.
                self.update_effects_from_clip([], None)
                return

            self.selected_clip = view
            self.selected_clip_track_type = getattr(view, "track_type", None)
            # Effets : la vue porte déjà la liste d'effets du clip.
            self.update_effects_from_clip(
                list(getattr(view, "effects", ()) or ()),
                self.selected_clip_track_type,
            )
            duration = view.end - view.start
            self.clip_name.setText(view.label)
            self.clip_duration.setText(f"{duration:.2f}s")
            self.clip_position.setText(f"{view.start:.2f}s")
            is_subtitle = getattr(view, "track_type", None) == "subtitle"
            self._set_group_condition(self.subtitle_group, is_subtitle)
            if is_subtitle:
                style = getattr(view, "text_style", None)
                self.subtitle_editor.set_state(getattr(view, "text", ""), style)

            is_video_clip = getattr(view, "track_type", None) == "video"
            self.movement_group.setEnabled(is_video_clip)
            for spin in self._spin_boxes.values():
                spin.setEnabled(is_video_clip)
            for slider in self._slider_widgets.values():
                slider.setEnabled(is_video_clip)
            for diamond in self._diamonds.values():
                diamond.setEnabled(is_video_clip)
            self.reset_movement_button.setEnabled(is_video_clip)
            if not is_video_clip:
                self._current_transform = None
                self._current_keyframes = []
                for name, spin in self._spin_boxes.items():
                    spin.setValue(self._default_value_for(name))
                for name, slider in self._slider_widgets.items():
                    slider.setValue(
                        self._slider_position(name, self._default_value_for(name))
                    )
                for name in self._diamonds:
                    self._set_diamond_checked(name, False)
            if is_video_clip and isinstance(getattr(view, "transform", None), ClipTransform):
                pending_transform = view.transform
                pending_keyframes = list(getattr(view, "keyframes", ()) or ())

            # Time remapping
            time_remapping = getattr(view, "time_remapping", None) or TimeRemapping()
            is_frozen = time_remapping.freeze_mode == FreezeFrameMode.FREEZE
            is_audio_clip = self.selected_clip_track_type == "audio"
            locked = getattr(view, "locked", False)
            enabled_tr = not locked
            
            # Vitesse
            self.speed_spinbox.blockSignals(True)
            self.speed_spinbox.setValue(time_remapping.speed)
            self.speed_spinbox.blockSignals(False)
            self.speed_spinbox.setEnabled(enabled_tr and not is_frozen)
            
            # Boutons preset
            for btn in [
                self.speed_0_25x_button,
                self.speed_0_5x_button,
                self.speed_1x_button,
                self.speed_2x_button,
                self.speed_4x_button,
            ]:
                btn.setEnabled(enabled_tr and not is_frozen)
            
            # Reverse
            self.reverse_button.blockSignals(True)
            self.reverse_button.setChecked(time_remapping.reverse)
            self.reverse_button.blockSignals(False)
            self.reverse_button.setEnabled(enabled_tr and not is_frozen)
            
            # Freeze frame
            self.freeze_frame_button.setEnabled(enabled_tr and not is_audio_clip)
            self.freeze_frame_button.setChecked(is_frozen)
            
            # Durée freeze frame
            self.freeze_duration_spinbox.blockSignals(True)
            self.freeze_duration_spinbox.setValue(time_remapping.freeze_duration)
            self.freeze_duration_spinbox.blockSignals(False)
            self.freeze_duration_row.setVisible(is_frozen)
            self.freeze_duration_spinbox.setEnabled(enabled_tr and is_frozen)
            self.freeze_duration_label.setText(f"{time_remapping.freeze_duration:.2f} s")
            self.freeze_duration_label.setVisible(is_frozen)
            
            # Bouton reset
            self.reset_speed_button.setEnabled(enabled_tr)
            
            # Affichage des durées
            source_duration = getattr(view, "source_duration", 0.0)
            timeline_duration = duration
            self.source_duration_label.setText(f"Source: {source_duration:.2f}s")
            self.timeline_duration_label.setText(f"Timeline: {timeline_duration:.2f}s")
            
            # Désactiver les contrôles incompatibles
            if is_audio_clip and is_frozen:
                self._on_time_remapping_reset()
                
            self.speed_group.setEnabled(enabled_tr)
        finally:
            self._pop_signal_block()
        if pending_transform is not None and pending_keyframes is not None:
            self.update_transform_from_clip(
                pending_transform,
                pending_keyframes,
                self._current_playhead_seconds,
            )

    def set_clip(self, view, track_name=None):
        self.show_clip(view)

    def show_transition(self, transition, outgoing_view, incoming_view, track_name: str) -> None:
        """Affiche l'édition d'une transition sans créer d'état métier local."""
        self.show_clip(None)
        self.selected_transition_id = transition.id
        self.transition_type_combo.blockSignals(True)
        index = self.transition_type_combo.findData(transition.type.value)
        self.transition_type_combo.setCurrentIndex(max(0, index))
        self.transition_type_combo.blockSignals(False)
        maximum = max(0.1, min(
            outgoing_view.end - outgoing_view.start,
            incoming_view.end - incoming_view.start,
        ) / 2.0)
        self.transition_duration_spin.blockSignals(True)
        self.transition_duration_spin.setMaximum(maximum)
        self.transition_duration_spin.setValue(min(transition.duration, maximum))
        self.transition_duration_spin.blockSignals(False)
        self.transition_from_label.setText(outgoing_view.label)
        self.transition_to_label.setText(incoming_view.label)
        self.transition_track_label.setText(track_name)
        self._set_group_condition(self.transition_group, True)

    def clear_transition(self) -> None:
        self.selected_transition_id = None
        self._set_group_condition(self.transition_group, False)

    def _on_transition_type_changed(self, _index: int) -> None:
        if self.selected_transition_id is None or self._signal_block_depth > 0:
            return
        self.transition_type_changed.emit(
            self.selected_transition_id, str(self.transition_type_combo.currentData())
        )

    def _on_transition_duration_changed(self, value: float) -> None:
        if self.selected_transition_id is None or self._signal_block_depth > 0:
            return
        self.transition_duration_changed.emit(self.selected_transition_id, float(value))

    def _on_transition_remove(self) -> None:
        if self.selected_transition_id is not None:
            self.transition_remove_requested.emit(self.selected_transition_id)

    # ----- Sous-titre (tâche 24) --------------------------------------

    subtitle_content_changed = Signal(str, str)  # clip_id, content
    subtitle_style_changed = Signal(str, object)  # clip_id, TextStyle
    subtitle_style_reset = Signal(str)  # clip_id

    def _on_subtitle_content_changed(self, content: str) -> None:
        """Émet le signal ``subtitle_content_changed`` avec l'id du clip."""
        if self.selected_clip is None:
            return
        self.subtitle_content_changed.emit(self.selected_clip.id, content)

    def _on_subtitle_style_changed(self, style) -> None:
        """Émet le signal ``subtitle_style_changed`` avec l'id du clip."""
        if self.selected_clip is None:
            return
        self.subtitle_style_changed.emit(self.selected_clip.id, style)

    def _on_subtitle_style_reset(self) -> None:
        """Émet le signal ``subtitle_style_reset`` pour réinitialiser le style."""
        if self.selected_clip is None:
            return
        self.subtitle_style_reset.emit(self.selected_clip.id)

    def _on_speed_changed(self, value: float) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.speed_changed.emit(self.selected_clip.id, float(value))

    def _on_reverse_toggled(self, checked: bool) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.reverse_toggled.emit(self.selected_clip.id, bool(checked))

    def _on_freeze_frame_clicked(self) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        # Créer un freeze frame au milieu du clip par défaut
        source_mid = self.selected_clip.source_in + (
            self.selected_clip.source_out - self.selected_clip.source_in
        ) / 2.0
        self.freeze_frame_created.emit(
            self.selected_clip.id, source_mid, self.freeze_duration_spinbox.value()
        )

    def _on_freeze_duration_changed(self, value: float) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.freeze_duration_changed.emit(self.selected_clip.id, float(value))

    def _on_time_remapping_reset(self) -> None:
        if self.selected_clip is None or self._signal_block_depth > 0:
            return
        self.time_remapping_reset.emit(self.selected_clip.id)
