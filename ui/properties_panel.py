"""Inspecteur de propriétés (clip sélectionné + paramètres du projet).

Refonte UI/UX :

- tous les boutons d'action utilisent des icônes SVG cohérentes ;
- les espacements et les tailles passent par :mod:`ui.design_system` ;
- la hiérarchie visuelle est renforcée par un titre clair et un
  regroupement logique des contrôles.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_effects_model import (
    AudioEffect,
)
from core.color_grading import (
    ColorGrade,
    ColorPresetStore,
)
from core.effects_model import (
    ClipEffect,
)
from core.time_remapping import MIN_SPEED, MAX_SPEED
from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
)
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.graphics_editor import GraphicsEditor
from ui.compositing_editor import CompositingEditor
from ui.text_style_editor import TextStyleEditor
from ui.theme import COLORS, label_style
from ui.properties_panel_mixins.tabs import TabsMixin
from ui.properties_panel_mixins.color import ColorMixin
from ui.properties_panel_mixins.audio import AudioMixin
from ui.properties_panel_mixins.effects import EffectsMixin
from ui.properties_panel_mixins.audio_effects import AudioEffectsMixin
from ui.properties_panel_mixins.keyframes import KeyframesMixin
from ui.properties_panel_mixins.clip_context import ClipContextMixin
from ui.properties_panel_mixins.transition_subtitle import TransitionSubtitleMixin
from ui.properties_panel_mixins.time_remapping import TimeRemappingMixin
from ui.properties_widgets.common import (  # noqa: F401 - réexports de compatibilité
    _KEYFRAME_MATCH_TOLERANCE,
    _PROPERTY_RANGES,
)
from ui.properties_widgets.diamond_button import _DiamondButton


class PropertiesPanel(TabsMixin, ColorMixin, AudioMixin, EffectsMixin, AudioEffectsMixin, KeyframesMixin, ClipContextMixin, TransitionSubtitleMixin, TimeRemappingMixin, QWidget):
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
    # Étalonnage couleur non destructif (tâche 29)
    color_grade_field_changed = Signal(str, str, float)
    color_grade_enabled_changed = Signal(str, bool)
    color_curve_changed = Signal(str, str, object)
    color_preset_applied = Signal(str, str)
    color_preset_save_requested = Signal(str, str)
    color_lut_import_requested = Signal(str, str)
    color_lut_remove_requested = Signal(str)
    color_grade_reset_requested = Signal(str)
    # Calques graphiques (tâche 32)
    graphic_property_changed = Signal(str, str, object)
    compositing_changed = Signal(str, object)

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
        self._current_color_grade = ColorGrade.identity()
        self._allow_color_signals = True
        self.color_preset_store = ColorPresetStore()
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
        title.hide()

        # Onglets principaux : Clip / Couleur / Audio / Effets. Les outils
        # spécialisés restent disponibles dans un menu compact afin que
        # l'inspecteur conserve une vraie largeur de travail.
        # Utilisation de QPushButton ``checkable`` plutôt que
        # ``QListWidget`` pour garantir un affichage horizontal compact.
        self.inspector_tabs_row = QWidget()
        self.inspector_tabs_layout = QHBoxLayout(self.inspector_tabs_row)
        self.inspector_tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_tabs_layout.setSpacing(Spacing.xs)
        self.inspector_tab_buttons: list[QPushButton] = []
        labels = ("Clip", "Couleur", "Effets", "Audio", "Graphiques", "Compositing")
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("inspectorTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"QPushButton#inspectorTab {{ background: transparent;"
                f" color: {COLORS['muted']}; border: 1px solid transparent;"
                f" border-radius: 0; padding: 7px 7px;"
                f" font-weight: 600; font-size: 11px; }}"
                f"QPushButton#inspectorTab:hover {{ color: {COLORS['text']};"
                f" background: transparent; }}"
                f"QPushButton#inspectorTab:checked {{ color: {COLORS['accent']};"
                f" background: transparent; border: none;"
                f" border-bottom: 2px solid {COLORS['accent']}; }}"
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_inspector_tab(idx)
            )
            self.inspector_tab_buttons.append(button)

        # L'ordre visuel suit le geste attendu dans la maquette tout en
        # conservant les indices historiques utilisés par le contrôleur.
        for index in (0, 1, 3, 2):
            self.inspector_tabs_layout.addWidget(self.inspector_tab_buttons[index])
        # Graphiques et Compositing passent par le menu « ••• » : leurs
        # boutons gardent l'état coché mais restent rattachés au panneau
        # pour ne pas devenir des fenêtres orphelines.
        for index in (4, 5):
            self.inspector_tab_buttons[index].setParent(self.inspector_tabs_row)
            self.inspector_tab_buttons[index].hide()

        self.inspector_more_button = QToolButton()
        self.inspector_more_button.setObjectName("inspectorMore")
        self.inspector_more_button.setText("•••")
        self.inspector_more_button.setToolTip("Outils spécialisés")
        self.inspector_more_button.setAccessibleName("Outils spécialisés")
        self.inspector_more_button.setStyleSheet(
            f"QToolButton {{ color: {COLORS['muted']}; background: transparent;"
            f" border: none; border-radius: 4px; padding: 5px 6px; }}"
            f"QToolButton:hover {{ color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
            f"QToolButton[active='true'] {{ color: {COLORS['accent']}; }}"
        )
        self.inspector_more_button.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.inspector_more_button)
        graphics_action = more_menu.addAction("Graphiques")
        graphics_action.triggered.connect(
            lambda _checked=False: self._select_inspector_tab(4)
        )
        compositing_action = more_menu.addAction("Compositing")
        compositing_action.triggered.connect(
            lambda _checked=False: self._select_inspector_tab(5)
        )
        self.inspector_more_button.setMenu(more_menu)
        self.inspector_tabs_layout.addWidget(self.inspector_more_button)
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

        # ----- Couleur (tâche 29) --------------------------------------
        self.color_group = self._build_color_group()
        layout.addWidget(self.color_group)

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

        # ----- Calque graphique (tâche 32) ----------------------------
        self.graphics_group = GraphicsEditor(self.group_style())
        self.graphics_group.field_changed.connect(self._emit_graphic_property)
        layout.insertWidget(layout.indexOf(self.movement_group), self.graphics_group)

        # ----- Compositing (tâche 33) -------------------------------
        self.compositing_group = CompositingEditor(self.group_style())
        self.compositing_group.value_changed.connect(
            lambda value: self.selected_clip is not None and self.compositing_changed.emit(self.selected_clip.id, value))
        layout.addWidget(self.compositing_group)

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
        # volume), Graphiques (géométrie + sous-titres).
        self._tab_groups: dict[int, list[QWidget]] = {
            0: [
                project_group,
                clip_group,
                self.transition_group,
                self.color_group,
                self.movement_group,
                self.speed_group,
                self.graphics_group,
                audio_group,
                self.audio_group,
                self.subtitle_group,
                self.effects_group,
                self.audio_effects_group,
                self.compositing_group,
            ],
            1: [project_group, clip_group, self.color_group],  # Couleur
            2: [project_group, self.movement_group,
                self.transition_group, self.speed_group,
                self.effects_group],  # Effets
            3: [project_group, audio_group, self.audio_group,
                self.audio_effects_group],  # Audio
            4: [project_group, clip_group, self.movement_group,
                self.graphics_group, self.subtitle_group],  # Graphiques
            5: [project_group, clip_group, self.compositing_group],
        }
        all_groups = [project_group, clip_group, self.transition_group,
                      self.color_group, self.movement_group, self.speed_group,
                      self.graphics_group, audio_group, self.audio_group,
                      self.subtitle_group,
                      self.effects_group, self.audio_effects_group,
                      self.compositing_group]
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
            self.graphics_group: False,
        }
        self._active_inspector_tab: int = 0
        self._on_inspector_tab_changed(0)


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


    # ----- Sous-titre (tâche 24) --------------------------------------

    subtitle_content_changed = Signal(str, str)  # clip_id, content
    subtitle_style_changed = Signal(str, object)  # clip_id, TextStyle
    subtitle_style_reset = Signal(str)  # clip_id


