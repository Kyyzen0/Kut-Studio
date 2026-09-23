from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from core.export_engine import ExportFormat, ExportPreset, ExportRequest
from ui.theme import COLORS, label_style


_FORMAT_LABELS = {
    ExportFormat.MP4_H264: "MP4 · H.264",
    ExportFormat.MOV_PRORES: "MOV · ProRes",
    ExportFormat.MOV_H264: "MOV · H.264",
}

_RESOLUTION_CHOICES = [
    ("1920 × 1080 (Full HD)", (1920, 1080)),
    ("1280 × 720 (HD)", (1280, 720)),
    ("3840 × 2160 (4K UHD)", (3840, 2160)),
]

_QUALITY_PRESETS = {
    "Élevée": ExportPreset(name="Élevée", resolution=(1920, 1080), crf=18, audio_bitrate="192k"),
    "Standard": ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
    "Basse": ExportPreset(name="Basse", resolution=(1280, 720), crf=28, audio_bitrate="96k"),
}


class ExportPanel(QWidget):
    export_requested = Signal()
    cancel_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("export_panel")
        self.setStyleSheet(
            f"QWidget#export_panel {{ background: {COLORS['panel']}; border-left: 1px solid {COLORS['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("EXPORT DU PROJET")
        title.setStyleSheet(label_style(13, "muted", 700))
        close_button = QPushButton("×")
        close_button.setFixedSize(30, 30)
        close_button.setToolTip("Fermer l'export")
        close_button.clicked.connect(self.close_requested)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_button)
        layout.addLayout(header)

        subtitle = QLabel("Préparez les paramètres de sortie de votre montage.")
        subtitle.setStyleSheet(label_style(13, "text", 500))
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        settings = QFrame()
        settings.setStyleSheet(
            f"QFrame {{ background: {COLORS['panel_alt']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
        )
        form = QFormLayout(settings)
        form.setContentsMargins(16, 16, 16, 16)
        form.setVerticalSpacing(14)

        self.format_combo = QComboBox()
        for export_format in ExportFormat:
            self.format_combo.addItem(_FORMAT_LABELS[export_format], userData=export_format)
        form.addRow("Format", self.format_combo)

        self.resolution_combo = QComboBox()
        for label, resolution in _RESOLUTION_CHOICES:
            self.resolution_combo.addItem(label, userData=resolution)
        form.addRow("Résolution", self.resolution_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(_QUALITY_PRESETS.keys()))
        self.quality_combo.setCurrentText("Standard")
        form.addRow("Qualité", self.quality_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "60"])
        self.fps_combo.setCurrentText("30")
        form.addRow("Images/seconde", self.fps_combo)

        layout.addWidget(settings)

        self.status_label = QLabel("Prêt à exporter")
        self.status_label.setStyleSheet(label_style(12, "success", 600))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; padding: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton:disabled {{ color: #626875; background: {COLORS['panel_alt']}; }}"
        )
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.setEnabled(False)

        self.launch_button = QPushButton("LANCER L'EXPORT")
        self.launch_button.setCursor(Qt.PointingHandCursor)
        self.launch_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; border: none; font-weight: 700; padding: 11px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
            f"QPushButton:disabled {{ background: {COLORS['surface']}; color: #626875; }}"
        )
        self.launch_button.clicked.connect(self.export_requested)

        actions_layout.addWidget(self.cancel_button, 1)
        actions_layout.addWidget(self.launch_button, 2)
        layout.addLayout(actions_layout)
        layout.addStretch()

    def build_request(self, clips, output_path):
        export_format = self.format_combo.currentData()
        resolution = self.resolution_combo.currentData()
        base_preset = _QUALITY_PRESETS[self.quality_combo.currentText()]
        preset = ExportPreset(
            name=base_preset.name,
            resolution=resolution,
            crf=base_preset.crf,
            audio_bitrate=base_preset.audio_bitrate,
        )
        fps = int(self.fps_combo.currentText())
        return ExportRequest(
            clips=clips,
            output_path=output_path,
            format=export_format,
            preset=preset,
            fps=fps,
        )

    def set_status(self, message, state="ready"):
        color_map = {"ready": "success", "running": "accent", "done": "success", "error": "danger"}
        color = color_map.get(state, "muted")
        self.status_label.setText(message)
        self.status_label.setStyleSheet(label_style(12, color, 600))
        is_running = state == "running"
        self.progress_bar.setVisible(is_running)
        self.launch_button.setEnabled(not is_running)
        self.cancel_button.setEnabled(is_running)

    def mark_export_started(self):
        self.progress_bar.setValue(0)
        self.set_status("Export en cours...", "running")

    def mark_export_finished(self):
        self.progress_bar.setValue(100)
        self.progress_bar.show()
        self.set_status("Export terminé", "done")

    def mark_export_error(self, message):
        self.set_status(message, "error")

    def mark_export_cancelled(self):
        self.set_status("Export annulé", "ready")
