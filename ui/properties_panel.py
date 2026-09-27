from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
)
from ui.theme import COLORS, label_style


# Minima / maxima exposés à l'UI. Doivent rester compatibles avec les
# bornes de ``core.visual_effects``.
_PROPERTY_RANGES = {
    "position_x": (-4.0, 4.0, 0.01),
    "position_y": (-4.0, 4.0, 0.01),
    "scale": (0.05, 10.0, 0.01),
    "rotation": (-3600.0, 3600.0, 0.1),
    "opacity": (0.0, 1.0, 0.01),
}


class _DiamondButton(QToolButton):
    """Petit bouton losange utilisé pour ajouter / retirer une keyframe.

    État ``checked`` : image-clé présente au playhead courant.
    Clic simple : ajoute ou remplace une keyframe.
    Shift+clic : retire la keyframe existante (sans quoi on ne fait
    que la mettre à jour, ce qui ne supprime jamais rien).
    """

    def __init__(self, property_name: str, parent=None):
        super().__init__(parent)
        self.property_name = property_name
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(18, 18)
        self.setToolTip(f"Ajouter / retirer une image-clé pour « {property_name} »")

    def paintEvent(self, event):  # noqa: D401 - redéfinition Qt
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
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
            painter.setBrush(QColor(COLORS["accent"]))
        else:
            painter.setBrush(QColor(COLORS["surface"]))
        painter.setPen(QColor(COLORS["border"]))
        painter.drawPolygon(polygon)


class PropertiesPanel(QWidget):
    cut_requested = Signal(str, float)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    ripple_delete_requested = Signal(str)
    enabled_changed = Signal(str, bool)
    # Nouveaux signaux pour la tâche 13.
    transform_changed = Signal(str, str, float)  # (clip_id, property_name, value)
    keyframe_added = Signal(str, str, float, float)  # (clip_id, prop, t, value)
    keyframe_removed = Signal(str, str, float)  # (clip_id, prop, t)
    transform_reset = Signal(str)  # clip_id

    def __init__(self, update_color_effect, update_volume, parent=None):
        super().__init__(parent)
        self.update_color_effect_callback = update_color_effect
        self.selected_clip = None
        self.selected_clip_track_type = None  # type: str | None
        self.timeline_panel = None
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            f"QWidget#properties_panel {{ background: {COLORS['panel']}; border-left: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(6)
        title = QLabel("PROPRIÉTÉS")
        title.setStyleSheet(label_style(10, "muted", 800))
        layout.addWidget(title)

        project_group = QGroupBox("Paramètres du projet")
        project_group.setStyleSheet(self.group_style())
        project_layout = QVBoxLayout(project_group)
        project_layout.setContentsMargins(12, 10, 12, 6)
        project_layout.setSpacing(3)

        project_fields = [
            ("État", "Aucun clip sélectionné"),
            ("Résolution", "1920 × 1080"),
            ("Format", "16:9"),
            ("Fréquence", "30 fps"),
            ("Fond", "#000000"),
        ]
        for field, value in project_fields:
            lbl = QLabel(f"{field} : {value}")
            lbl.setStyleSheet(label_style(12, "text", 500))
            project_layout.addWidget(lbl)
        layout.addWidget(project_group)

        clip_group = QGroupBox("Clip sélectionné")
        clip_group.setStyleSheet(self.group_style())
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(12, 10, 12, 6)
        clip_form.setSpacing(4)
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

        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(6)
        self.cut_button = QPushButton("✂️ Couper à la tête de lecture")
        self.delete_button = QPushButton("🗑️ Supprimer")
        self.duplicate_button = QPushButton("🧬 Dupliquer (Ctrl+D)")
        self.ripple_button = QPushButton("✂️ Supprimer avec ripple (Ctrl+Backspace)")
        for button in (
            self.cut_button,
            self.delete_button,
            self.duplicate_button,
            self.ripple_button,
        ):
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 7px 12px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {COLORS['surface_hover']}; }}"
                f"QPushButton:disabled {{ color: #626875; background: {COLORS['panel_alt']}; border-color: {COLORS['border']}; }}"
            )
        self.cut_button.clicked.connect(self.emit_cut_requested)
        self.delete_button.clicked.connect(self.emit_delete_requested)
        self.duplicate_button.clicked.connect(self.emit_duplicate_requested)
        self.ripple_button.clicked.connect(self.emit_ripple_requested)
        self.cut_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.duplicate_button.setEnabled(False)
        self.ripple_button.setEnabled(False)
        actions_layout.addWidget(self.cut_button)
        actions_layout.addWidget(self.delete_button)
        actions_layout.addWidget(self.duplicate_button)
        actions_layout.addWidget(self.ripple_button)
        layout.addLayout(actions_layout)

        # Case à cocher « Clip activé ».
        self.enabled_checkbox = QCheckBox("Clip activé")
        self.enabled_checkbox.toggled.connect(self.emit_enabled_changed)
        self.enabled_checkbox.setEnabled(False)
        layout.addWidget(self.enabled_checkbox)

        color_group = QGroupBox("Couleur")
        color_group.setStyleSheet(self.group_style())
        color_form = QFormLayout(color_group)
        color_form.setContentsMargins(12, 10, 12, 6)
        color_form.setSpacing(4)
        self.brightness_slider, brightness_row, self.brightness_value = self.make_slider(-100, 100, 0)
        self.contrast_slider, contrast_row, self.contrast_value = self.make_slider(-100, 100, 0)
        self.saturation_slider, saturation_row, self.saturation_value = self.make_slider(-100, 100, 0)
        color_form.addRow("Luminosité", brightness_row)
        color_form.addRow("Contraste", contrast_row)
        color_form.addRow("Saturation", saturation_row)
        layout.addWidget(color_group)
        for slider in (self.brightness_slider, self.contrast_slider, self.saturation_slider):
            slider.valueChanged.connect(self.update_color_values)
            slider.valueChanged.connect(update_color_effect)

        audio_group = QGroupBox("Audio")
        audio_group.setStyleSheet(self.group_style())
        audio_form = QFormLayout(audio_group)
        audio_form.setContentsMargins(12, 10, 12, 6)
        self.volume_slider, volume_row, self.volume_value = self.make_slider(0, 200, 100, suffix=" %")
        audio_form.addRow("Volume", volume_row)
        layout.addWidget(audio_group)
        self.volume_slider.valueChanged.connect(update_volume)

        # ---------------- Section MOUVEMENT (tâche 13) ----------------
        self.movement_group = QGroupBox("Mouvement")
        self.movement_group.setStyleSheet(self.group_style())
        movement_layout = QVBoxLayout(self.movement_group)
        movement_layout.setContentsMargins(12, 12, 12, 12)
        movement_layout.setSpacing(6)
        self._spin_boxes: dict[str, QDoubleSpinBox] = {}
        self._diamonds: dict[str, _DiamondButton] = {}
        self._slider_widgets: dict[str, QSlider] = {}
        for property_name in ANIMATABLE_PROPERTIES:
            low, high, step = _PROPERTY_RANGES[property_name]
            default = self._default_value_for(property_name)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            label = QLabel(self._human_label(property_name))
            label.setStyleSheet(label_style(11, "muted", 600))
            label.setFixedWidth(58)
            row_layout.addWidget(label)

            spin = QDoubleSpinBox()
            spin.setDecimals(2 if property_name != "rotation" else 1)
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(80)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._make_value_changed_handler(property_name))
            self._spin_boxes[property_name] = spin
            row_layout.addWidget(spin)

            # Slider d'appoint (opacité / scale particulièrement utiles).
            if property_name in {"opacity", "scale", "rotation"}:
                slider = QSlider(Qt.Horizontal)
                if property_name == "rotation":
                    slider.setRange(-360, 360)
                    slider.setValue(0)
                else:
                    slider.setRange(int(low * 100), int(high * 100))
                    slider.setValue(int(default * 100))
                slider.setMinimumWidth(80)
                slider.setEnabled(False)
                slider.valueChanged.connect(self._make_slider_handler(property_name))
                self._slider_widgets[property_name] = slider
                row_layout.addWidget(slider, 1)

            diamond = _DiamondButton(property_name)
            diamond.clicked.connect(self._make_diamond_clicked_handler(property_name))
            diamond.setEnabled(False)
            self._diamonds[property_name] = diamond
            row_layout.addWidget(diamond)
            movement_layout.addWidget(row)

        reset_button = QPushButton("↺ Réinitialiser le mouvement")
        reset_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 6px 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton:disabled {{ color: #626875; background: {COLORS['panel_alt']}; }}"
        )
        reset_button.clicked.connect(self._emit_reset)
        reset_button.setEnabled(False)
        self.reset_movement_button = reset_button
        movement_layout.addWidget(reset_button)

        layout.addWidget(self.movement_group)
        # Désactivé par défaut : un clip non sélectionné ne doit rien
        # pouvoir modifier.
        self.movement_group.setEnabled(False)
        # ---------------- Fin section MOUVEMENT ----------------

        self.subtitle_group = QGroupBox("Sous-titre S1")
        self.subtitle_group.setStyleSheet(self.group_style())
        subtitle_layout = QVBoxLayout(self.subtitle_group)
        subtitle_layout.setContentsMargins(12, 16, 12, 12)
        self.subtitle_editor = QTextEdit()
        self.subtitle_editor.setPlaceholderText("Texte affiché sur le preview...")
        self.subtitle_editor.setFixedHeight(72)
        self.subtitle_editor.setStyleSheet(
            "QTextEdit { background: #222222; color: white; border: 1px solid #3b3b3b; border-radius: 6px; padding: 5px; }"
        )
        save_button = QPushButton("Enregistrer le .srt")
        save_button.setEnabled(False)
        save_button.setVisible(False)
        subtitle_layout.addWidget(self.subtitle_editor)
        subtitle_layout.addWidget(save_button)
        self.subtitle_group.hide()
        layout.addWidget(self.subtitle_group)
        layout.addStretch()

    # ------------------------------------------------------------------
    # Helpers privés : MOUVEMENT
    # ------------------------------------------------------------------

    @staticmethod
    def _human_label(property_name: str) -> str:
        return {
            "position_x": "X",
            "position_y": "Y",
            "scale": "Échelle",
            "rotation": "Rot°",
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
                self._allow_property_signals = False
                spin.setValue(float_value)
                self._allow_property_signals = True
            self._on_property_changed(property_name, float_value)
        return _handler

    def _make_diamond_clicked_handler(self, property_name: str):
        def _handler(checked: bool) -> None:
            self._on_diamond_toggled(property_name, checked)
        return _handler

    def _on_property_changed(self, property_name: str, value: float) -> None:
        if not self._allow_property_signals:
            return
        if self.selected_clip is None:
            return
        self.transform_changed.emit(self.selected_clip.id, property_name, float(value))

    def _on_diamond_toggled(self, property_name: str, checked: bool) -> None:
        if self.selected_clip is None:
            return
        spin = self._spin_boxes.get(property_name)
        if spin is None:
            return
        value = float(spin.value())
        # On distingue l'ajout de la suppression grâce à l'état précédent :
        # ``_diamond_was_checked`` mémorise l'état avant le clic, et le
        # toggle Qt met à jour ``isChecked`` en premier.
        was_checked = self._diamond_was_checked.get(property_name, False)
        if checked and not was_checked:
            # Ajout : on émet ``keyframe_added`` au temps local du playhead.
            if hasattr(self, "_current_playhead_seconds"):
                clip_local_time = max(
                    0.0,
                    self._current_playhead_seconds - self.selected_clip.start,
                )
            else:
                clip_local_time = 0.0
            self.keyframe_added.emit(
                self.selected_clip.id, property_name, clip_local_time, value
            )
        elif not checked and was_checked:
            # Suppression.
            if hasattr(self, "_current_playhead_seconds"):
                clip_local_time = max(
                    0.0,
                    self._current_playhead_seconds - self.selected_clip.start,
                )
            else:
                clip_local_time = 0.0
            self.keyframe_removed.emit(
                self.selected_clip.id, property_name, clip_local_time
            )
        # Mémorise l'état pour le prochain toggle (clic ou refresh).
        self._diamond_was_checked[property_name] = checked

    def _sync_diamond_state_memory(self) -> None:
        """Initialise ``_diamond_was_checked`` à partir de l'état Qt courant."""
        self._diamond_was_checked = {
            name: diamond.isChecked() for name, diamond in self._diamonds.items()
        }

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
        """Synchronise les champs MOUVEMENT avec l'état du clip.

        Cette méthode est appelée par ``MainWindow`` après chaque
        modification de transform ou lors d'une sélection, pour que
        l'inspecteur reflète la réalité métier.
        """
        self._current_playhead_seconds = float(playhead_seconds)
        if self.selected_clip is None:
            return
        self._allow_property_signals = False
        try:
            for property_name in ANIMATABLE_PROPERTIES:
                spin = self._spin_boxes.get(property_name)
                if spin is None:
                    continue
                spin.setValue(float(getattr(transform, property_name)))
                if property_name in self._slider_widgets:
                    if property_name == "rotation":
                        self._slider_widgets[property_name].setValue(int(getattr(transform, property_name)))
                    else:
                        self._slider_widgets[property_name].setValue(int(round(getattr(transform, property_name) * 100)))
            for property_name, diamond in self._diamonds.items():
                diamond.blockSignals(True)
                diamond.setChecked(
                    self._has_keyframe_at_playhead(
                        keyframes, playhead_seconds, self.selected_clip.start
                    ).get(property_name, False)
                )
                diamond.blockSignals(False)
            self._sync_diamond_state_memory()
        finally:
            self._allow_property_signals = True

    @staticmethod
    def _has_keyframe_at_playhead(
        keyframes: list[TransformKeyframe],
        timeline_seconds: float,
        clip_timeline_start: float,
        tolerance: float = 1e-3,
    ) -> dict[str, bool]:
        """Retourne un mapping ``property_name -> True si une kf existe à l'instant``.

        ``timeline_seconds`` est la position du playhead ; on calcule
        le temps local du clip ``timeline_seconds - clip_timeline_start``.
        """
        clip_local = max(0.0, timeline_seconds - clip_timeline_start)
        result: dict[str, bool] = {}
        for kf in keyframes:
            if abs(kf.time_seconds - clip_local) <= tolerance:
                result[kf.property_name] = True
        return result

    def refresh_keyframe_diamonds(
        self,
        keyframes: list[TransformKeyframe],
        playhead_seconds: float,
    ) -> None:
        """Met à jour uniquement l'état des diamants (sans toucher aux valeurs)."""
        self._current_playhead_seconds = float(playhead_seconds)
        if self.selected_clip is None:
            return
        active = self._has_keyframe_at_playhead(
            keyframes, playhead_seconds, self.selected_clip.start
        )
        for property_name, diamond in self._diamonds.items():
            diamond.blockSignals(True)
            diamond.setChecked(active.get(property_name, False))
            diamond.blockSignals(False)
        self._sync_diamond_state_memory()

    @staticmethod
    def group_style():
        return (
            f"QGroupBox {{ color: {COLORS['muted']}; border: 1px solid {COLORS['border']}; border-radius: 6px; margin-top: 8px; padding-top: 8px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COLORS['muted']}; }}"
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
        row.setSpacing(6)
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
        self._allow_property_signals = False
        try:
            if view is None:
                self.selected_clip = None
                self.selected_clip_track_type = None
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
                for spin in self._spin_boxes.values():
                    spin.setEnabled(False)
                    spin.setValue(self._default_value_for("position_x"))
                for slider in self._slider_widgets.values():
                    slider.setEnabled(False)
                for diamond in self._diamonds.values():
                    diamond.setEnabled(False)
                    diamond.setChecked(False)
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

            # État de la case « Clip activé » : reflète ``view.enabled`` si
            # l'attribut est disponible, sinon True par défaut.
            enabled = getattr(view, "enabled", True)
            self.enabled_checkbox.blockSignals(True)
            self.enabled_checkbox.setChecked(bool(enabled))
            self.enabled_checkbox.blockSignals(False)
            self.enabled_checkbox.setEnabled(True)

            is_subtitle = view.track_id == "S1"
            self.subtitle_group.setVisible(is_subtitle)
            if is_subtitle:
                self.subtitle_editor.blockSignals(True)
                self.subtitle_editor.setPlainText(view.text)
                self.subtitle_editor.blockSignals(False)

            # Les transformations visuelles ne s'appliquent qu'aux
            # clips vidéo. On désactive proprement la section MOUVEMENT
            # pour les clips audio et les sous-titres.
            is_video_clip = (
                getattr(view, "track_type", None) == "video"
                or view.track_id.startswith("V")
            )
            self.movement_group.setEnabled(is_video_clip)
            for spin in self._spin_boxes.values():
                spin.setEnabled(is_video_clip)
            for slider in self._slider_widgets.values():
                slider.setEnabled(is_video_clip)
            for diamond in self._diamonds.values():
                diamond.setEnabled(is_video_clip)
            self.reset_movement_button.setEnabled(is_video_clip)
        finally:
            self._allow_property_signals = True

    # Initialisation du drapeau anti-rétroaction : évite que les
    # ``setValue`` lors d'un rafraîchissement externe re-émettent des
    # ``valueChanged``.
    _allow_property_signals = False

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
