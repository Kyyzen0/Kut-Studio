"""Onglet « Performance » des Préférences : proxies et cache.

Interface volontairement sobre : un interrupteur, un profil, deux actions
de génération, le budget et l'occupation du cache, des purges. Aucune
règle ici : chaque contrôle appelle la fenêtre principale (``host``), qui
délègue à :class:`core.proxy_manager.ProxyManager` et
:class:`core.cache_manager.CacheManager`.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.hardware_encoding import HardwareCapabilities, HardwareEncoder
from core.proxy_profiles import available_profiles
from core.tool_paths import find_media_tool
from core.user_settings import MAX_CACHE_MAX_GB, MIN_CACHE_MAX_GB
from core.video_encoders import encoder_options
from ui import i18n
from ui.render_queue_panel import format_size
from ui.theme import label_style


class PerformanceSettingsTab(QWidget):
    """Proxies média et gestion du cache (voir le module)."""

    REFRESH_MS = 1500

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        # --- Proxies ---------------------------------------------------------
        self.proxies_box = QGroupBox()
        proxies_layout = QVBoxLayout(self.proxies_box)
        self.use_proxies = QCheckBox()
        self.use_proxies.toggled.connect(self._on_use_proxies)
        proxies_layout.addWidget(self.use_proxies)
        form = QFormLayout()
        self.profile_label = QLabel()
        self.profile_combo = QComboBox()
        for profile in available_profiles():
            self.profile_combo.addItem("", userData=profile.id)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        form.addRow(self.profile_label, self.profile_combo)
        proxies_layout.addLayout(form)
        row = QHBoxLayout()
        self.generate_button = QPushButton()
        self.generate_button.clicked.connect(self._on_generate_project)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._on_cancel_all)
        row.addWidget(self.generate_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        proxies_layout.addLayout(row)
        self.ffmpeg_warning = QLabel()
        self.ffmpeg_warning.setWordWrap(True)
        self.ffmpeg_warning.setStyleSheet(label_style(12, "danger", 600))
        proxies_layout.addWidget(self.ffmpeg_warning)
        layout.addWidget(self.proxies_box)

        # --- Cache ----------------------------------------------------------------
        self.cache_box = QGroupBox()
        cache_layout = QVBoxLayout(self.cache_box)
        cache_form = QFormLayout()
        self.max_label = QLabel()
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setDecimals(1)
        self.max_spin.setSingleStep(0.5)
        self.max_spin.setRange(MIN_CACHE_MAX_GB, MAX_CACHE_MAX_GB)
        self.max_spin.editingFinished.connect(self._on_max_changed)
        cache_form.addRow(self.max_label, self.max_spin)
        cache_layout.addLayout(cache_form)
        self.usage_label = QLabel()
        self.usage_label.setWordWrap(True)
        cache_layout.addWidget(self.usage_label)
        purge_row = QHBoxLayout()
        self.purge_previews_button = QPushButton()
        self.purge_proxies_button = QPushButton()
        self.purge_project_button = QPushButton()
        self.purge_all_button = QPushButton()
        self.purge_previews_button.clicked.connect(lambda: self._purge("preview"))
        self.purge_proxies_button.clicked.connect(lambda: self._purge("proxy"))
        self.purge_all_button.clicked.connect(lambda: self._purge("all"))
        self.purge_project_button.clicked.connect(self._purge_project)
        for button in (self.purge_previews_button, self.purge_proxies_button,
                       self.purge_project_button, self.purge_all_button):
            purge_row.addWidget(button)
        cache_layout.addLayout(purge_row)
        self.status_label = QLabel()
        self.status_label.setStyleSheet(label_style(12, "muted", 500))
        cache_layout.addWidget(self.status_label)
        layout.addWidget(self.cache_box)

        # --- Encodage matériel (export) -------------------------------------------
        self.encoding_box = QGroupBox()
        encoding_layout = QVBoxLayout(self.encoding_box)
        encoding_form = QFormLayout()
        self.encoder_label = QLabel()
        self.encoder_combo = QComboBox()
        self.encoder_combo.setObjectName("defaultEncoderCombo")
        self.encoder_combo.activated.connect(self._on_encoder_changed)
        encoding_form.addRow(self.encoder_label, self.encoder_combo)
        encoding_layout.addLayout(encoding_form)
        self.diagnostics_view = QPlainTextEdit()
        self.diagnostics_view.setObjectName("encodingDiagnostics")
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setFixedHeight(130)
        encoding_layout.addWidget(self.diagnostics_view)
        encoding_row = QHBoxLayout()
        self.redetect_button = QPushButton()
        self.redetect_button.clicked.connect(self._on_redetect)
        self.copy_diagnostics_button = QPushButton()
        self.copy_diagnostics_button.clicked.connect(self._on_copy_diagnostics)
        encoding_row.addWidget(self.redetect_button)
        encoding_row.addWidget(self.copy_diagnostics_button)
        encoding_row.addStretch(1)
        encoding_layout.addLayout(encoding_row)
        layout.addWidget(self.encoding_box)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(label_style(12, "muted", 500))
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.retranslate()
        self.load()

    # -- état -------------------------------------------------------------------------

    def load(self) -> None:
        """Relit les réglages courants de la fenêtre principale."""
        host = self._host
        self._loading = True
        try:
            self.use_proxies.setChecked(host.proxies.enabled)
            index = self.profile_combo.findData(host.proxies.profile.id)
            self.profile_combo.setCurrentIndex(max(0, index))
            self.max_spin.setValue(host.cache_manager.max_bytes / (1024 ** 3))
        finally:
            self._loading = False
        self.refresh_encoding()
        self.refresh()

    def refresh(self) -> None:
        """Met à jour l'occupation du cache et l'état de FFmpeg."""
        host = self._host
        stats = host.cache_summary()["usage"]

        def size(kind: str) -> str:
            return format_size(int(stats.get(kind, {}).get("bytes", 0)))

        self.usage_label.setText(
            i18n.translate(
                "perf.cache.usage", preview=size("preview"), proxy=size("proxy"), memory=size("memory")
            )
        )
        missing = not find_media_tool("ffmpeg")
        self.ffmpeg_warning.setVisible(missing)
        self.generate_button.setEnabled(not missing)

    def refresh_encoding(self) -> None:
        """Options d'encodeur (seulement celles réellement disponibles) et diagnostics."""
        host = self._host
        capabilities = host.hardware_capabilities()
        detecting = capabilities is None or getattr(host, "_detecting_hardware", False)
        options = encoder_options("h264", capabilities or HardwareCapabilities())
        wanted = getattr(host, "_export_encoder", HardwareEncoder.AUTO.value)
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()
        for backend, label in options:
            text = i18n.translate("render.encoder.auto") if backend is HardwareEncoder.AUTO else label
            self.encoder_combo.addItem(text, userData=backend.value)
        self.encoder_combo.setCurrentIndex(max(0, self.encoder_combo.findData(wanted)))
        self.encoder_combo.blockSignals(False)
        self.diagnostics_view.setPlainText(host.encoding_diagnostics_text())
        self.redetect_button.setEnabled(not detecting)

    def _on_encoder_changed(self, _index: int) -> None:
        self._host.set_export_encoder(self.encoder_combo.currentData())

    def _on_redetect(self) -> None:
        if self._host.redetect_hardware_capabilities():
            self.diagnostics_view.setPlainText(i18n.translate("encoding.detecting"))
            self.redetect_button.setEnabled(False)

    def _on_copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(self.diagnostics_view.toPlainText())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt
        super().showEvent(event)
        self.load()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt
        super().hideEvent(event)
        self._timer.stop()

    # -- réactions -----------------------------------------------------------------------

    def _on_use_proxies(self, checked: bool) -> None:
        if not self._loading:
            self._host.set_proxies_enabled(checked)

    def _on_profile_changed(self, _index: int) -> None:
        if not self._loading:
            self._host.set_proxy_profile(self.profile_combo.currentData())

    def _on_max_changed(self) -> None:
        if not self._loading:
            self._host.set_cache_max_gb(self.max_spin.value())
            self.refresh()

    def _on_generate_project(self) -> None:
        self._host.generate_proxies_for_project()

    def _on_cancel_all(self) -> None:
        self._host.cancel_all_proxies()

    def _show_freed(self, freed: int) -> None:
        self.status_label.setText(i18n.translate("perf.cache.freed", size=format_size(freed)))
        self.refresh()

    def _purge(self, kind: str) -> None:
        self._show_freed(self._host.purge_caches(kind))

    def _purge_project(self) -> None:
        self._show_freed(self._host.purge_project_cache())

    # -- texte -----------------------------------------------------------------------------------

    def retranslate(self) -> None:
        tr = i18n.translate
        self.proxies_box.setTitle(tr("perf.proxies.title"))
        self.use_proxies.setText(tr("perf.use_proxies"))
        self.profile_label.setText(tr("perf.profile"))
        for index in range(self.profile_combo.count()):
            profile_id = self.profile_combo.itemData(index)
            label = tr(f"proxy.profile.{profile_id}")
            # Un profil ajouté sans traduction s'affiche sous son identifiant.
            self.profile_combo.setItemText(index, profile_id if label.startswith("[") else label)
        self.generate_button.setText(tr("perf.proxies.generate_project"))
        self.cancel_button.setText(tr("perf.proxies.cancel_all"))
        self.ffmpeg_warning.setText(tr("perf.ffmpeg_missing"))
        self.cache_box.setTitle(tr("perf.cache.title"))
        self.max_label.setText(tr("perf.cache.max"))
        self.purge_previews_button.setText(tr("perf.cache.purge_previews"))
        self.purge_proxies_button.setText(tr("perf.cache.purge_proxies"))
        self.purge_project_button.setText(tr("perf.cache.purge_project"))
        self.purge_all_button.setText(tr("perf.cache.purge_all"))
        self.encoding_box.setTitle(tr("perf.encoding.title"))
        self.encoder_label.setText(tr("perf.encoding.default"))
        self.redetect_button.setText(tr("perf.encoding.redetect"))
        self.copy_diagnostics_button.setText(tr("perf.encoding.copy"))
        self.hint_label.setText(tr("perf.quality_hint"))
