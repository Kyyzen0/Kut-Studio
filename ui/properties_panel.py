from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.theme import COLORS, label_style


class PropertiesPanel(QWidget):
    cut_requested = Signal(str, float)
    delete_requested = Signal(str)

    def __init__(self, update_color_effect, update_volume, save_subtitles, parent=None):
        super().__init__(parent)
        self.update_color_effect_callback = update_color_effect
        self.selected_clip = None
        self.timeline_panel = None
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            f"QWidget#properties_panel {{ background: {COLORS['panel']}; border-left: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(10)
        title = QLabel("PROPRIÉTÉS")
        title.setStyleSheet(label_style(10, "muted", 800))
        layout.addWidget(title)

        project_group = QGroupBox("Paramètres du projet")
        project_group.setStyleSheet(self.group_style())
        project_form = QFormLayout(project_group)
        project_form.setContentsMargins(12, 16, 12, 12)
        project_form.setSpacing(8)
        project_form.addRow("État", QLabel("Aucun clip sélectionné"))
        project_form.addRow("Résolution", QLabel("1920 × 1080"))
        project_form.addRow("Format", QLabel("16:9"))
        project_form.addRow("Fréquence", QLabel("30 fps"))
        project_form.addRow("Fond", QLabel("#000000"))
        layout.addWidget(project_group)

        clip_group = QGroupBox("Clip sélectionné")
        clip_group.setStyleSheet(self.group_style())
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(12, 16, 12, 12)
        clip_form.setSpacing(9)
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
        actions_layout.setSpacing(8)
        self.cut_button = QPushButton("✂️ Couper à la tête de lecture")
        self.delete_button = QPushButton("🗑️ Supprimer")
        for button in (self.cut_button, self.delete_button):
            button.setStyleSheet(
                f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
                f"QPushButton:hover {{ background: {COLORS['surface_hover']}; }}"
            )
        self.cut_button.clicked.connect(self.emit_cut_requested)
        self.delete_button.clicked.connect(self.emit_delete_requested)
        self.cut_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        actions_layout.addWidget(self.cut_button)
        actions_layout.addWidget(self.delete_button)
        layout.addLayout(actions_layout)

        color_group = QGroupBox("Couleur")
        color_group.setStyleSheet(self.group_style())
        color_form = QFormLayout(color_group)
        color_form.setContentsMargins(12, 16, 12, 12)
        color_form.setSpacing(8)
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
        audio_form.setContentsMargins(12, 16, 12, 12)
        self.volume_slider, volume_row, self.volume_value = self.make_slider(0, 200, 100, suffix=" %")
        audio_form.addRow("Volume", volume_row)
        layout.addWidget(audio_group)
        self.volume_slider.valueChanged.connect(update_volume)

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
        save_button.clicked.connect(save_subtitles)
        subtitle_layout.addWidget(self.subtitle_editor)
        subtitle_layout.addWidget(save_button)
        self.subtitle_group.hide()
        layout.addWidget(self.subtitle_group)
        layout.addStretch()

    @staticmethod
    def group_style():
        return (
            f"QGroupBox {{ color: {COLORS['muted']}; border: 1px solid {COLORS['border']}; border-radius: 6px; margin-top: 10px; padding-top: 10px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COLORS['muted']}; }}"
        )

    @staticmethod
    def make_slider(minimum, maximum, value, suffix=""):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setMinimumWidth(90)
        value_label = QLabel(f"{value}{suffix}")
        value_label.setMinimumWidth(38)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setStyleSheet(label_style(11, "muted", 600))
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(slider)
        row.addWidget(value_label)
        slider._value_label = value_label
        slider._suffix = suffix
        return slider, container, value_label

    def update_color_values(self):
        self.brightness_value.setText(str(self.brightness_slider.value()))
        self.contrast_value.setText(str(self.contrast_slider.value()))
        self.saturation_value.setText(str(self.saturation_slider.value()))

    def show_clip(self, clip):
        if clip is None:
            self.selected_clip = None
            self.clip_name.setText("Aucun clip sélectionné")
            self.clip_duration.setText("--")
            self.clip_position.setText("--")
            self.cut_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.subtitle_group.hide()
            return

        self.selected_clip = clip
        duration = clip["end"] - clip["start"]
        self.clip_name.setText(clip["label"])
        self.clip_duration.setText(f"{duration:.2f}s")
        self.clip_position.setText(f"{clip['start']:.2f}s")
        self.cut_button.setEnabled(True)
        self.delete_button.setEnabled(True)

        is_subtitle = clip["track"] == 2
        self.subtitle_group.setVisible(is_subtitle)
        if is_subtitle:
            self.subtitle_editor.blockSignals(True)
            self.subtitle_editor.setPlainText(clip.get("text", ""))
            self.subtitle_editor.blockSignals(False)

    def set_clip(self, clip, track_name=None):
        self.show_clip(clip)

    def emit_cut_requested(self):
        if self.selected_clip is None:
            return
        if self.timeline_panel is None:
            return
        playhead_seconds = self.timeline_panel.playhead_seconds
        self.cut_requested.emit(self.selected_clip.get("id"), playhead_seconds)

    def emit_delete_requested(self):
        if self.selected_clip is None:
            return
        self.delete_requested.emit(self.selected_clip.get("id"))
