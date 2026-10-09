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
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui import i18n
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
from core.visual_effects import (
    ClipTransform,
    TransformKeyframe,
)
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName
from ui.keyboard_navigation import let_tab_leave_in, tab_only_controls
from ui.graphics_editor import GraphicsEditor
from ui.compositing_editor import CompositingEditor
from ui.theme import COLORS, label_style
from ui.properties_panel_mixins.construction import ConstructionMixin
from ui.properties_panel_mixins.tabs import TabsMixin
from ui.properties_panel_mixins.color import ColorMixin
from ui.properties_panel_mixins.audio import AudioMixin
from ui.properties_panel_mixins.effects import EffectsMixin
from ui.properties_panel_mixins.audio_effects import AudioEffectsMixin
from ui.properties_panel_mixins.keyframes import KeyframesMixin
from ui.properties_panel_mixins.clip_context import ClipContextMixin
from ui.properties_panel_mixins.transition_subtitle import TransitionSubtitleMixin
from ui.properties_panel_mixins.time_remapping import TimeRemappingMixin
from ui.properties_widgets.color_curve_editor import ColorCurveEditor  # noqa: F401 - réexport
from ui.properties_widgets.common import (  # noqa: F401 - réexports de compatibilité
    _KEYFRAME_MATCH_TOLERANCE,
    _PROPERTY_RANGES,
)
from ui.properties_widgets.diamond_button import _DiamondButton  # noqa: F401 - réexport


class PropertiesPanel(ConstructionMixin, TabsMixin, ColorMixin, AudioMixin, EffectsMixin, AudioEffectsMixin, KeyframesMixin, ClipContextMixin, TransitionSubtitleMixin, TimeRemappingMixin, QWidget):
    transform_changed = Signal(str, str, float)
    text_animation_requested = Signal(str, str)        # (calque, preset) : core.text_animations
    voice_sync_requested = Signal(str)                 # calque texte : temps des mots depuis la voix
    keyframe_added = Signal(str, str, float, float)
    keyframe_removed = Signal(str, str, float)
    transform_reset = Signal(str)
    # Animation (moteur générique : core.animation / core.keyframe_editing)
    animation_toggled = Signal(str, str, bool)
    keyframe_navigation_requested = Signal(str, int)
    interpolation_requested = Signal(str, str)
    animation_copy_requested = Signal(str)
    animation_paste_requested = Signal(str)
    graph_editor_requested = Signal(str)
    active_property_changed = Signal(str)
    audio_gain_changed = Signal(float)
    audio_pan_changed = Signal(float)
    audio_fade_changed = Signal(str, float)
    audio_fades_reset = Signal()
    # Time remapping signals
    speed_changed = Signal(str, float)
    reverse_toggled = Signal(str, bool)
    freeze_frame_created = Signal(str, float, float)
    freeze_frame_removed = Signal(str)
    time_command_requested = Signal(str, str, object)   # (clip, commande, argument) : section « Temps » (core.time_commands)
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
    # Fin de ``show_clip`` : la page Couleur suit le clip que montre l'inspecteur.
    clip_shown = Signal()
    # Calques graphiques (tâche 32)
    graphic_property_changed = Signal(str, str, object)
    compositing_changed = Signal(str, object)
    advanced_transform_changed = Signal(str, str, object)
    advanced_keyframe_toggled = Signal(str, str)
    graphic_parent_changed = Signal(str, str)
    inspector_tab_changed = Signal(int)

    def __init__(self, update_volume, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
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
        self._color_value: object = None                 # ColorGrade ou graphe de nœuds du clip affiché
        self._color_node_id: str | None = None           # nœud courant (choisi sur la page Couleur)
        self._allow_color_signals = True
        self.color_preset_store = ColorPresetStore()
        self._group_titles: list[tuple] = []  # (QGroupBox, clé i18n) : réécrits par ``retranslate``
        self._row_labels: list[tuple] = []  # (QFormLayout, champ, clé i18n)
        self._stacked_labels: list[tuple] = []  # (QLabel, clé i18n) : libellés posés au-dessus de leur champ, voir _add_stacked_row
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            f"QWidget#properties_panel {{ background: {COLORS['panel']}; "
            f"border-left: 1px solid {COLORS['border']}; }}"
        )
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._build_header(outer_layout)

        layout = self._build_scroll_area(outer_layout)
        project_group = self._build_project_group(layout)

        clip_group = self._build_clip_group(layout)

        self._build_transition_group(layout)

        # ----- Couleur (tâche 29) --------------------------------------
        self.color_group = self._build_color_group()
        layout.addWidget(self.color_group)

        audio_group = self._build_volume_group(layout, update_volume)

        self._build_speed_group(layout)

        self._build_movement_group(layout)

        # ----- Calque graphique (tâche 32) ----------------------------
        self.graphics_group = GraphicsEditor(self.group_style())
        self.graphics_group.field_changed.connect(self._emit_graphic_property)
        self.graphics_group.parent_changed.connect(
            lambda parent_id: self.selected_clip is not None
            and self.graphic_parent_changed.emit(self.selected_clip.id, parent_id)
        )
        self.graphics_group.text_animation_requested.connect(
            lambda preset: self.selected_clip is not None
            and self.text_animation_requested.emit(self.selected_clip.id, preset)
        )
        self.graphics_group.voice_sync_requested.connect(
            lambda: self.selected_clip is not None and self.voice_sync_requested.emit(self.selected_clip.id)
        )
        layout.insertWidget(layout.indexOf(self.movement_group), self.graphics_group)

        # ----- Compositing (tâche 33) -------------------------------
        self.compositing_group = CompositingEditor(self.group_style())
        self.compositing_group.value_changed.connect(
            lambda value: self.selected_clip is not None and self.compositing_changed.emit(self.selected_clip.id, value))
        layout.addWidget(self.compositing_group)

        # ----- Suivi (tracking 2D) ------------------------------------
        from ui.tracking_panel import TrackingPanel

        self.tracking_group = TrackingPanel(self.group_style())
        layout.addWidget(self.tracking_group)

        # ----- Audio (mixage non destructif) ---------------------------
        self.audio_group = self._build_audio_group()
        layout.addWidget(self.audio_group)
        self.audio_group.setEnabled(False)

        self._build_subtitle_group(layout)

        # ----- Effets du clip (tâche 21) -------------------------------
        self.effects_group = self._build_effects_group()
        layout.addWidget(self.effects_group)

        # ----- Effets audio du clip (tâche 27) -------------------------
        self.audio_effects_group = self._build_audio_effects_group()
        layout.addWidget(self.audio_effects_group)
        layout.addStretch()

        self._register_inspector_groups(project_group, clip_group, audio_group)
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(callback))
        # Clavier : Tab atteint tous les contrôles, un clic n'en vole pas le focus (Espace continue de lire) ;
        # Tab sort des éditeurs multilignes. Voir ui.keyboard_navigation.
        tab_only_controls(self)
        let_tab_leave_in(self)
        self._sync_inspector_tab_order()



    def _on_language_changed(self, _code: str) -> None:
        self.retranslate()

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
        """Les groupes suivent la feuille de style globale (sections à plat, titre au-dessus d'un filet) : aucun style local.

        Conservé (vide) pour les éditeurs qui reçoivent encore un style de groupe à leur construction.
        """
        return ""

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


