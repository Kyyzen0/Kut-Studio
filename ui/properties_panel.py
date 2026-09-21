from PySide6.QtCore import Qt
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


class PropertiesPanel(QWidget):
    def __init__(self, update_color_effect, update_volume, save_subtitles, parent=None):
        super().__init__(parent)
        self.update_color_effect_callback = update_color_effect
        self.setObjectName("properties_panel")
        self.setStyleSheet(
            "QWidget#properties_panel { background: #181818; border: 1px solid #2d2d2d; border-radius: 12px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Propriétés")
        title.setStyleSheet("color: #e6e6e6; font-weight: 700; font-size: 13px; margin-bottom: 6px;")
        layout.addWidget(title)

        clip_group = QGroupBox("Clip sélectionné")
        clip_group.setStyleSheet(self.group_style())
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(12, 16, 12, 12)
        clip_form.setSpacing(9)
        self.clip_name = QLabel("Aucun clip sélectionné")
        self.clip_duration = QLabel("--")
        self.clip_track = QLabel("--")
        self.clip_start = QLabel("--")
        self.clip_end = QLabel("--")
        for label in (self.clip_name, self.clip_duration, self.clip_track, self.clip_start, self.clip_end):
            label.setStyleSheet("color: #d7dff7; font-size: 12px;")
        self.clip_name.setStyleSheet("color: #f5f5f5; font-size: 13px; font-weight: 700;")
        clip_form.addRow("Nom", self.clip_name)
        clip_form.addRow("Durée", self.clip_duration)
        clip_form.addRow("Piste", self.clip_track)
        clip_form.addRow("Début", self.clip_start)
        clip_form.addRow("Fin", self.clip_end)
        layout.addWidget(clip_group)

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
            "QGroupBox { color: #dfe8ff; border: 1px solid #2d2d2d; border-radius: 10px; margin-top: 10px; padding-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #dfe8ff; }"
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
        value_label.setStyleSheet("color: #b9c7d9; font-size: 11px;")
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

    def set_clip(self, clip, track_name):
        duration = clip["end"] - clip["start"]
        self.clip_name.setText(f"Nom: {clip['label']}")
        self.clip_duration.setText(f"Durée: {duration:.2f}s")
        self.clip_track.setText(f"Piste: {track_name}")
        self.clip_start.setText(f"Début: {clip['start']:.2f}s")
        self.clip_end.setText(f"Fin: {clip['end']:.2f}s")
        is_subtitle = clip["track"] == 2
        self.subtitle_group.setVisible(is_subtitle)
        if is_subtitle:
            self.subtitle_editor.blockSignals(True)
            self.subtitle_editor.setPlainText(clip.get("text", ""))
            self.subtitle_editor.blockSignals(False)
