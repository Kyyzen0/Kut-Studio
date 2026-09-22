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

from ui.theme import COLORS, label_style


class ExportPanel(QWidget):
    export_requested = Signal()
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
        self.format_value = QLabel("MP4 · H.264")
        self.resolution_value = QLabel("1920 × 1080")
        self.quality_value = QComboBox()
        self.quality_value.addItems(["Élevée", "Standard", "Basse"])
        for value in (self.format_value, self.resolution_value):
            value.setStyleSheet(label_style(12, "text", 600))
        form.addRow("Format", self.format_value)
        form.addRow("Résolution", self.resolution_value)
        form.addRow("Qualité", self.quality_value)
        layout.addWidget(settings)

        self.status_label = QLabel("Prêt à exporter")
        self.status_label.setStyleSheet(label_style(12, "success", 600))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        self.launch_button = QPushButton("LANCER L'EXPORT")
        self.launch_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; border: none; font-weight: 700; padding: 11px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
        )
        self.launch_button.clicked.connect(self.export_requested)
        layout.addWidget(self.launch_button)
        layout.addStretch()

    def set_status(self, message, state="ready"):
        color = {"ready": "success", "running": "accent", "done": "success", "error": "danger"}.get(state, "muted")
        self.status_label.setText(message)
        self.status_label.setStyleSheet(label_style(12, color, 600))
        self.progress_bar.setVisible(state == "running")
        self.launch_button.setEnabled(state != "running")

    def mark_export_started(self):
        self.progress_bar.setValue(0)
        self.set_status("Export en cours...", "running")

    def mark_export_finished(self):
        self.progress_bar.setValue(100)
        self.progress_bar.show()
        self.set_status("Export terminé", "done")

    def mark_export_error(self, message):
        self.set_status(message, "error")
