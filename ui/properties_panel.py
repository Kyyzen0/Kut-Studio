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
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MAX_GAIN_DB, MIN_GAIN_DB
from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    evaluate_transform,
)
from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconButton, IconName
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
    cut_requested = Signal(str, float)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    ripple_delete_requested = Signal(str)
    enabled_changed = Signal(str, bool)
    transform_changed = Signal(str, str, float)
    keyframe_added = Signal(str, str, float, float)
    keyframe_removed = Signal(str, str, float)
    transform_reset = Signal(str)
    audio_gain_changed = Signal(float)
    audio_pan_changed = Signal(float)
    audio_fade_changed = Signal(str, float)
    audio_fades_reset = Signal()

    def __init__(self, update_color_effect, update_volume, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.update_color_effect_callback = update_color_effect
        self.selected_clip = None
        self.selected_clip_track_type = None
        self.timeline_panel = None
        self._signal_block_depth = 0
        self._allow_property_signals = False
        self._diamond_was_checked: dict[str, bool] = {}
        self._current_playhead_seconds = 0.0
        self._current_transform: ClipTransform | None = None
        self._current_keyframes: list[TransformKeyframe] = []
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            f"QWidget#properties_panel {{ background: {COLORS['panel']}; "
            f"border-left: 1px solid {COLORS['border']}; }}"
        )
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Titre du panneau (header).
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Spacing.lg, Spacing.sm, Spacing.lg, Spacing.sm)
        header_layout.setSpacing(0)
        title = QLabel("PROPRIÉTÉS")
        title.setStyleSheet(label_style(11, "muted", 800))
        header_layout.addWidget(title)
        outer_layout.addWidget(header)

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

        # ----- Actions rapides -----------------------------------------
        actions_group = QGroupBox("Actions")
        actions_group.setStyleSheet(self.group_style())
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        actions_layout.setSpacing(Spacing.xs)

        # Première rangée : Couper / Dupliquer.
        row_a = QWidget()
        row_a_layout = QVBoxLayout(row_a)
        row_a_layout.setContentsMargins(0, 0, 0, 0)
        row_a_layout.setSpacing(Spacing.xs)
        self.cut_button = self._make_action_button(
            IconName.CUT, "Couper", "Couper le clip à la tête de lecture"
        )
        self.duplicate_button = self._make_action_button(
            IconName.DUPLICATE, "Dupliquer", "Dupliquer le clip (Ctrl+D)"
        )
        row_a_layout.addWidget(self.cut_button)
        row_a_layout.addWidget(self.duplicate_button)
        actions_layout.addWidget(row_a)

        # Deuxième rangée : Supprimer / Ripple.
        row_b = QWidget()
        row_b_layout = QVBoxLayout(row_b)
        row_b_layout.setContentsMargins(0, 0, 0, 0)
        row_b_layout.setSpacing(Spacing.xs)
        self.delete_button = self._make_action_button(
            IconName.TRASH, "Supprimer", "Supprimer le clip"
        )
        self.ripple_button = self._make_action_button(
            IconName.SCISSORS,
            "Supprimer avec ripple",
            "Supprimer le clip et fermer le trou (Ctrl+Backspace)",
        )
        row_b_layout.addWidget(self.delete_button)
        row_b_layout.addWidget(self.ripple_button)
        actions_layout.addWidget(row_b)

        # Case « Clip activé ».
        self.enabled_checkbox = QCheckBox("Clip activé")
        self.enabled_checkbox.toggled.connect(self.emit_enabled_changed)
        self.enabled_checkbox.setEnabled(False)
        actions_layout.addWidget(self.enabled_checkbox)
        layout.addWidget(actions_group)

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

        # ----- Sous-titre ----------------------------------------------
        self.subtitle_group = QGroupBox("Sous-titre")
        self.subtitle_group.setStyleSheet(self.group_style())
        subtitle_layout = QVBoxLayout(self.subtitle_group)
        subtitle_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        self.subtitle_editor = QTextEdit()
        self.subtitle_editor.setPlaceholderText(
            "Texte affiché sur le preview…"
        )
        self.subtitle_editor.setFixedHeight(96)
        self.subtitle_editor.setStyleSheet(
            f"QTextEdit {{ background: {COLORS['panel_alt']}; color: {COLORS['text']}; "
            f"border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 6px; }}"
        )
        save_button = QPushButton("Enregistrer le .srt")
        save_button.setEnabled(False)
        save_button.setVisible(False)
        subtitle_layout.addWidget(self.subtitle_editor)
        subtitle_layout.addWidget(save_button)
        self.subtitle_group.hide()
        layout.addWidget(self.subtitle_group)
        layout.addStretch()

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
            if view is None:
                self.selected_clip = None
                self.selected_clip_track_type = None
                self._current_transform = None
                self._current_keyframes = []
                self.clip_name.setText("Aucun clip sélectionné")
                self.clip_duration.setText("--")
                self.clip_position.setText("--")
                self.cut_button.setEnabled(False)
                self.delete_button.setEnabled(False)
                self.duplicate_button.setEnabled(False)
                self.ripple_button.setEnabled(False)
                self.enabled_checkbox.blockSignals(True)
                self.enabled_checkbox.setChecked(False)
                self.enabled_checkbox.blockSignals(False)
                self.enabled_checkbox.setEnabled(False)
                self.subtitle_group.hide()
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
                return

            self.selected_clip = view
            self.selected_clip_track_type = getattr(view, "track_type", None)
            duration = view.end - view.start
            self.clip_name.setText(view.label)
            self.clip_duration.setText(f"{duration:.2f}s")
            self.clip_position.setText(f"{view.start:.2f}s")
            self.cut_button.setEnabled(True)
            self.delete_button.setEnabled(True)
            self.duplicate_button.setEnabled(True)
            self.ripple_button.setEnabled(True)

            enabled = getattr(view, "enabled", True)
            self.enabled_checkbox.blockSignals(True)
            self.enabled_checkbox.setChecked(bool(enabled))
            self.enabled_checkbox.blockSignals(False)
            self.enabled_checkbox.setEnabled(True)

            is_subtitle = getattr(view, "track_type", None) == "subtitle"
            self.subtitle_group.setVisible(is_subtitle)
            if is_subtitle:
                self.subtitle_editor.blockSignals(True)
                self.subtitle_editor.setPlainText(view.text)
                self.subtitle_editor.blockSignals(False)

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

    def emit_cut_requested(self):
        if self.selected_clip is None:
            return
        if self.timeline_panel is None:
            return
        playhead_seconds = self.timeline_panel.playhead_seconds
        self.cut_requested.emit(self.selected_clip.id, playhead_seconds)

    def emit_delete_requested(self):
        if self.selected_clip is None:
            return
        self.delete_requested.emit(self.selected_clip.id)

    def emit_duplicate_requested(self):
        if self.selected_clip is None:
            return
        self.duplicate_requested.emit(self.selected_clip.id)

    def emit_ripple_requested(self):
        if self.selected_clip is None:
            return
        self.ripple_delete_requested.emit(self.selected_clip.id)

    def emit_enabled_changed(self, checked: bool) -> None:
        if self.selected_clip is None:
            return
        self.enabled_changed.emit(self.selected_clip.id, bool(checked))