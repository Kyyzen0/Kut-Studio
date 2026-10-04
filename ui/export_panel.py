from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.export_engine import ExportFormat, ExportRequest
from core.render_job import JobStatus
from core.render_plan import RenderPlan
from core.render_presets import (
    CUSTOM_PRESET_ID,
    RenderPresetSpec,
    builtin_presets,
    custom_preset,
    default_preset,
    get_preset,
    with_hardware,
)
from core.hardware_encoding import HardwareCapabilities, HardwareEncoder
from core.video_encoders import encoder_options
from ui import i18n
from ui.design_system import ButtonVariant, Sizes, Spacing, StatusKind
from ui.icons import IconButton, IconName
from ui.theme import COLORS, set_role, set_state, set_variant

_FORMAT_LABELS = {
    ExportFormat.MP4_H264: "MP4 · H.264",
    ExportFormat.MOV_PRORES: "MOV · ProRes",
    ExportFormat.MOV_H264: "MOV · H.264",
}

_RESOLUTION_CHOICES = [
    ("1920 × 1080 (Full HD)", (1920, 1080)),
    ("1280 × 720 (HD)", (1280, 720)),
    ("2560 × 1440 (QHD)", (2560, 1440)),
    ("3840 × 2160 (4K UHD)", (3840, 2160)),
    ("1080 × 1920 (Vertical)", (1080, 1920)),
]

# Qualité du preset « Custom » : identifiant -> (CRF, débit audio) ; le libellé affiché est traduit (voir ci-dessous).
_QUALITY_PRESETS = {
    "high": (18, "192k"),
    "standard": (23, "128k"),
    "low": (28, "96k"),
}
_QUALITY_LABEL_KEYS = {
    "high": "render.export.quality.high",
    "standard": "render.export.quality.standard",
    "low": "render.export.quality.low",
}


class ExportPanel(QWidget):
    """Page Export : choisir un preset, puis lancer ou ajouter à la file.

    ``LANCER L'EXPORT`` demande le fichier de sortie, ajoute le job à la
    file de rendu et le lance aussitôt : le parcours Projet → Export →
    preset → lancer reste en une étape. ``Ajouter à la file`` prépare un
    export sans le lancer ; la file (en dessous) permet ensuite de
    réordonner, lancer, annuler ou relancer.
    """

    export_requested = Signal()
    add_to_queue_requested = Signal()
    cancel_requested = Signal()
    close_requested = Signal()

    encoder_changed = Signal(str)
    """L'utilisateur a choisi un autre encodeur (valeur sérialisable)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capabilities: HardwareCapabilities | None = None
        self._encoder_choice = HardwareEncoder.AUTO.value
        self.setObjectName("export_panel")
        self.setStyleSheet(
            f"QWidget#export_panel {{ background: {COLORS['panel']}; border-left: 1px solid {COLORS['border']}; }}"
        )
        self._queue = None
        # Le contenu défile : avec la file de rendu, la page dépasse la
        # hauteur d'une petite fenêtre et ne doit jamais être écrasée.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("exportScroll")
        scroll.setStyleSheet(
            "QScrollArea#exportScroll, QScrollArea#exportScroll > QWidget > QWidget"
            " { background: transparent; }"
        )
        content = QWidget()
        scroll.setWidget(content)
        root.addWidget(scroll)
        self._layout = QVBoxLayout(content)
        layout = self._layout
        layout.setContentsMargins(Spacing.xl, Spacing.xl, Spacing.xl, Spacing.xl)
        layout.setSpacing(Spacing.lg)

        header = QHBoxLayout()
        self.title_label = QLabel(i18n.translate("render.export.title"))
        set_role(self.title_label, "app-title")
        close_button = IconButton(icon=IconName.CLOSE, size=Sizes.icon_button)
        self.close_button = close_button
        close_button.setToolTip(i18n.translate("render.export.close_tooltip"))
        close_button.setAccessibleName(i18n.translate("a11y.export.close"))  # le libellé visible n'est qu'un « × »
        close_button.clicked.connect(self.close_requested)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(close_button)
        layout.addLayout(header)

        self.subtitle_label = QLabel(i18n.translate("render.export.subtitle"))
        set_role(self.subtitle_label, "label-secondary")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        # Le preset est la première décision de la page : il ouvre la carte, juste sous le titre.
        settings = QFrame()
        settings.setObjectName("card")                          # la carte du thème : surface + filet, rayon des conteneurs
        form = QFormLayout(settings)
        form.setContentsMargins(Spacing.lg, Spacing.lg, Spacing.lg, Spacing.lg)
        form.setVerticalSpacing(Spacing.md)
        self.preset_label = QLabel()
        self.preset_combo = QComboBox()
        for spec in builtin_presets():
            self.preset_combo.addItem("", userData=spec.id)
        self.preset_combo.addItem("", userData=CUSTOM_PRESET_ID)
        form.addRow(self.preset_label, self.preset_combo)
        self.preset_summary = QLabel()
        self.preset_summary.setWordWrap(True)
        set_role(self.preset_summary, "label-secondary")
        form.addRow(self.preset_summary)
        self.encoder_label = QLabel()
        self.encoder_combo = QComboBox()
        self.encoder_combo.setObjectName("exportEncoderCombo")
        form.addRow(self.encoder_label, self.encoder_combo)

        # Réglages libres : visibles seulement pour « Custom ».
        self.custom_frame = QWidget()
        custom_form = QFormLayout(self.custom_frame)
        custom_form.setContentsMargins(0, 0, 0, 0)
        custom_form.setVerticalSpacing(Spacing.md)
        self.format_combo = QComboBox()
        for export_format in ExportFormat:
            self.format_combo.addItem(_FORMAT_LABELS[export_format], userData=export_format)
        self.resolution_combo = QComboBox()
        for label, resolution in _RESOLUTION_CHOICES:
            self.resolution_combo.addItem(label, userData=resolution)
        self.quality_combo = QComboBox()
        for quality_id in _QUALITY_PRESETS:
            self.quality_combo.addItem(i18n.translate(_QUALITY_LABEL_KEYS[quality_id]), quality_id)
        self.quality_combo.setCurrentIndex(self.quality_combo.findData("standard"))
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "60"])
        self.fps_combo.setCurrentText("30")
        self._custom_labels = {
            "format": QLabel(), "resolution": QLabel(), "quality": QLabel(), "fps": QLabel(),
        }
        custom_form.addRow(self._custom_labels["format"], self.format_combo)
        custom_form.addRow(self._custom_labels["resolution"], self.resolution_combo)
        custom_form.addRow(self._custom_labels["quality"], self.quality_combo)
        custom_form.addRow(self._custom_labels["fps"], self.fps_combo)
        form.addRow(self.custom_frame)
        layout.addWidget(settings)

        self.status_label = QLabel()
        set_role(self.status_label, "label-secondary")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(Spacing.sm)
        # Une action principale (exporter), une normale (ajouter à la file), une légère (annuler) : la hiérarchie des boutons.
        self.cancel_button = QPushButton()
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        set_variant(self.cancel_button, ButtonVariant.GHOST)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.setEnabled(False)

        self.add_button = QPushButton()
        self.add_button.setCursor(Qt.PointingHandCursor)
        set_variant(self.add_button, ButtonVariant.SECONDARY)
        self.add_button.clicked.connect(self.add_to_queue_requested)

        self.launch_button = QPushButton()
        self.launch_button.setCursor(Qt.PointingHandCursor)
        set_variant(self.launch_button, ButtonVariant.PRIMARY)
        self.launch_button.clicked.connect(self.export_requested)
        for button in (self.cancel_button, self.add_button, self.launch_button):
            button.setMinimumHeight(Sizes.button_lg)

        actions_layout.addWidget(self.cancel_button, 1)
        actions_layout.addWidget(self.add_button, 2)
        actions_layout.addWidget(self.launch_button, 2)
        layout.addLayout(actions_layout)

        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.encoder_combo.activated.connect(self._on_encoder_activated)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(default_preset().id))
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(callback))
        self.retranslate()
        self._on_preset_changed()
        self.set_status(i18n.translate("render.export.ready"), "ready")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Échap ferme la page d'export, comme la croix : c'est un dialogue plein écran."""
        if event.key() == Qt.Key_Escape and not event.modifiers():
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # File de rendu
    # ------------------------------------------------------------------

    def set_queue(self, queue) -> None:
        """Branche la file de rendu : panneau intégré et état synchronisé."""
        from ui.render_queue_panel import RenderQueuePanel

        self._queue = queue
        self.queue_panel = RenderQueuePanel(queue)
        self._layout.addWidget(self.queue_panel, 1)
        queue.job_updated.connect(self._on_queue_changed)
        queue.jobs_changed.connect(self._on_queue_changed)

    def _on_queue_changed(self, *_args) -> None:
        """Reflète le job en cours dans le statut et la barre de progression."""
        queue = self._queue
        if queue is None:
            return
        job = queue.current_job
        if job is not None:
            self.set_status(
                i18n.translate("render.export.running", name=job.name), "running"
            )
            self.progress_bar.setValue(job.progress)
            self.progress_bar.show()
            self.cancel_button.setEnabled(True)
            return
        self.cancel_button.setEnabled(False)
        self.progress_bar.setVisible(False)

    # ------------------------------------------------------------------
    # Texte et preset courant
    # ------------------------------------------------------------------

    def _on_language_changed(self, _code: str) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        tr = i18n.translate
        self.title_label.setText(tr("render.export.title"))
        self.subtitle_label.setText(tr("render.export.subtitle"))
        self.close_button.setToolTip(tr("render.export.close_tooltip"))
        for index in range(self.quality_combo.count()):
            self.quality_combo.setItemText(index, tr(_QUALITY_LABEL_KEYS[self.quality_combo.itemData(index)]))
        self.preset_label.setText(tr("render.export.preset"))
        self.encoder_label.setText(tr("render.export.encoder"))
        self._rebuild_encoder_options()
        for index in range(self.preset_combo.count()):
            self.preset_combo.setItemText(
                index, tr(f"render.preset.{self.preset_combo.itemData(index)}")
            )
        for key, label in self._custom_labels.items():
            label.setText(tr(f"render.export.{key}"))
        self.cancel_button.setText(tr("render.export.cancel"))
        self.add_button.setText(tr("render.export.add"))
        self.launch_button.setText(tr("render.export.launch"))
        self._update_summary()

    def current_preset_id(self) -> str:
        return self.preset_combo.currentData() or default_preset().id

    def current_spec(self) -> RenderPresetSpec:
        """Preset choisi, avec l'encodeur sélectionné (Automatique, CPU ou matériel)."""
        return with_hardware(self._base_spec(), self.current_encoder())

    def _base_spec(self) -> RenderPresetSpec:
        """Preset choisi ; pour « Custom », construit depuis les réglages libres."""
        preset_id = self.current_preset_id()
        if preset_id != CUSTOM_PRESET_ID:
            return get_preset(preset_id) or default_preset()
        export_format = self.format_combo.currentData()
        width, height = self.resolution_combo.currentData()
        crf, audio_bitrate = _QUALITY_PRESETS[self.quality_combo.currentData()]
        return custom_preset(
            container=export_format.container,
            video_codec=export_format.codec,
            width=width,
            height=height,
            fps=int(self.fps_combo.currentText()),
            quality=crf,
            audio_bitrate=audio_bitrate,
        )

    def _on_preset_changed(self, *_args) -> None:
        self.custom_frame.setVisible(self.current_preset_id() == CUSTOM_PRESET_ID)
        self._rebuild_encoder_options()
        self._update_summary()

    def _update_summary(self) -> None:
        preset_id = self.current_preset_id()
        spec = self.current_spec()
        description = i18n.translate(f"render.preset.desc.{preset_id}")
        self.preset_summary.setText(f"{spec.summary()}\n{description}")

    # ------------------------------------------------------------------
    # Encodeur (Automatique, CPU, matériel détecté)
    # ------------------------------------------------------------------

    def set_capabilities(self, capabilities: HardwareCapabilities | None) -> None:
        """Capacités détectées : seules les options réellement disponibles sont proposées."""
        self._capabilities = capabilities
        self._rebuild_encoder_options()

    def set_default_encoder(self, value: str) -> None:
        """Choix mémorisé dans les préférences (sans émettre ``encoder_changed``)."""
        self._encoder_choice = value
        self._rebuild_encoder_options()

    def current_encoder(self) -> str:
        """Valeur sérialisable de l'encodeur sélectionné (``auto``, ``cpu``, ``videotoolbox``…)."""
        data = self.encoder_combo.currentData()
        return data if isinstance(data, str) else self._encoder_choice

    def _codec_family(self) -> str:
        codec = self._base_spec().video_codec
        return "prores_ks" if codec == "prores_ks" else "h264"

    def _rebuild_encoder_options(self) -> None:
        options = encoder_options(self._codec_family(), self._capabilities or HardwareCapabilities())
        wanted = self._encoder_choice
        if wanted not in {backend.value for backend, _label in options}:
            # Choix indisponible pour ce format (ex. ProRes : CPU seulement) : on affiche
            # une valeur valable sans modifier la préférence enregistrée.
            wanted = HardwareEncoder.AUTO.value if options[0][0] is HardwareEncoder.AUTO else HardwareEncoder.CPU.value
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()
        for backend, label in options:
            text = i18n.translate("render.encoder.auto") if backend is HardwareEncoder.AUTO else label
            self.encoder_combo.addItem(text, userData=backend.value)
        self.encoder_combo.setCurrentIndex(max(0, self.encoder_combo.findData(wanted)))
        self.encoder_combo.blockSignals(False)

    def _on_encoder_activated(self, _index: int) -> None:
        self._encoder_choice = self.encoder_combo.currentData()
        self.encoder_changed.emit(self._encoder_choice)

    def build_request(self, render_plan: RenderPlan, output_path: str):
        """Construit un :class:`ExportRequest` à partir d'un :class:`RenderPlan`.

        ``render_plan`` doit provenir de
        :func:`core.render_plan.build_render_plan`. Le preset courant
        décrit la sortie (voir :mod:`core.render_presets`) ; la
        composition vidéo est entièrement décrite par le plan de rendu.
        """
        spec = self.current_spec()
        export_format, preset, fps = spec.export_parts()
        return ExportRequest(
            render_plan=render_plan,
            output_path=output_path,
            format=export_format,
            preset=preset,
            fps=fps,
            hardware=spec.hardware,
        )

    # ------------------------------------------------------------------
    # Statut (API historique conservée)
    # ------------------------------------------------------------------

    _STATE_FOR = {
        "ready": StatusKind.IDLE, "running": StatusKind.WORKING, "done": StatusKind.SUCCESS, "error": StatusKind.ERROR,
    }

    def set_status(self, message, state="ready"):
        kind = self._STATE_FOR.get(state, StatusKind.IDLE)
        self.status_label.setText(message)
        set_state(self.status_label, kind)                 # la couleur vient de la feuille de style : même sens partout
        set_state(self.progress_bar, kind)
        is_running = state == "running"
        self.progress_bar.setVisible(is_running)
        self.cancel_button.setEnabled(is_running)

    def mark_export_started(self):
        self.progress_bar.setValue(0)
        self.set_status(i18n.translate("render.export.running", name=""), "running")

    def mark_export_finished(self):
        self.progress_bar.setValue(100)
        self.progress_bar.show()
        self.set_status(i18n.translate("render.export.done"), "done")

    def mark_export_error(self, message):
        self.set_status(message, "error")

    def mark_export_cancelled(self):
        self.set_status(i18n.translate("render.export.cancelled"), "ready")

    def mark_job_failed(self, name: str) -> None:
        self.set_status(i18n.translate("render.export.failed", name=name), "error")

    def job_status_text(self, status: JobStatus) -> str:
        return i18n.translate(f"render.status.{status.value}")
