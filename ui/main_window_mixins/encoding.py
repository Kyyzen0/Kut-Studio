"""Méthodes de ``MainWindow`` regroupées : encodage matériel.

Ce mixin ne contient que le **câblage** : détection et choix de l'encodeur
vivent dans ``core.hardware_encoding``, ``core.hardware_cache`` et
``core.video_encoders`` (testables sans interface), le repli dans
``core.export_engine``. La détection tourne dans un thread : lancer FFmpeg
(plusieurs mini-encodages) ne doit jamais geler la fenêtre au démarrage.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import shiboken6
from PySide6.QtCore import QObject, Signal

from core.hardware_cache import default_service
from core.hardware_encoding import HardwareCapabilities, HardwareEncoder
from ui import i18n


class _CapabilityEvents(QObject):
    """Pont thread de détection → thread Qt (connexion en file automatique)."""

    ready = Signal(object)


class EncodingMixin:
    """Mixin de ``MainWindow`` (capacités d'encodage, choix de l'encodeur d'export)."""

    # ------------------------------------------------------------------
    # Initialisation et détection
    # ------------------------------------------------------------------

    def _init_encoding(self, settings) -> None:
        """Mémorise le choix d'encodeur et lance la détection en tâche de fond."""
        self._export_encoder = settings.export_encoder
        self._encoding_closed = False
        self._detecting_hardware = False
        self._capability_events = _CapabilityEvents(self)
        self._capability_events.ready.connect(self._on_capabilities_ready)

    def _start_hardware_detection(self, *, rescan: bool = False) -> bool:
        """Détecte les capacités sans bloquer ; ``False`` si une détection est déjà en cours."""
        if getattr(self, "_detecting_hardware", False):
            return False
        known = None if rescan else default_service().cached()
        if known is not None:  # cache valide : aucun processus, aucun thread
            self._on_capabilities_ready(known)
            return True
        self._detecting_hardware = True
        events = self._capability_events

        def work() -> None:
            service = default_service()
            capabilities = service.rescan() if rescan else service.capabilities()
            try:
                events.ready.emit(capabilities)
            except RuntimeError:  # fenêtre détruite pendant la détection
                pass

        threading.Thread(target=work, name="kut-hardware-detect", daemon=True).start()
        return True

    def hardware_capabilities(self) -> HardwareCapabilities | None:
        """Capacités déjà connues (jamais de détection bloquante côté interface)."""
        return default_service().cached()

    def _on_capabilities_ready(self, capabilities) -> None:
        self._detecting_hardware = False
        if getattr(self, "_encoding_closed", False):
            return
        panel = getattr(self, "export_panel", None)
        if panel is not None:
            panel.set_capabilities(capabilities)
        on_hardware = getattr(self, "_on_hardware_capabilities", None)
        if on_hardware is not None:
            on_hardware(capabilities)
        self._refresh_encoding_settings_tab()

    def redetect_hardware_capabilities(self) -> bool:
        """Action « Redétecter les capacités matérielles »."""
        return self._start_hardware_detection(rescan=True)

    def _refresh_encoding_settings_tab(self) -> None:
        dialog = getattr(self, "_preferences_dialog", None)
        # La détection tourne dans un thread : son résultat peut arriver alors que la boîte
        # (WA_DeleteOnClose) est déjà fermée, voire détruite côté C++.
        if dialog is None or not shiboken6.isValid(dialog):
            return
        tab = getattr(dialog, "performance_tab", None)
        if tab is not None and shiboken6.isValid(tab):
            tab.refresh_encoding()

    # ------------------------------------------------------------------
    # Réglages et diagnostics
    # ------------------------------------------------------------------

    def set_export_encoder(self, value: str) -> None:
        """Encodeur d'export par défaut (mémorisé dans les préférences)."""
        value = HardwareEncoder(value).value if value in {e.value for e in HardwareEncoder} else "auto"
        if value == getattr(self, "_export_encoder", "auto"):
            return
        self._export_encoder = value
        panel = getattr(self, "export_panel", None)
        if panel is not None and panel.current_encoder() != value:
            panel.set_default_encoder(value)  # le sélecteur Export suit la préférence
        self._apply_settings(replace(self._settings_snapshot(), export_encoder=value))

    def encoding_diagnostics_text(self) -> str:
        """Diagnostic copiable : version, chemin, encodeurs, validations, backend Auto.

        Avec le mixin matériel, le texte couvre aussi décodage, aperçu et mémoire.
        """
        flow = getattr(self, "flow_diagnostics_text", None)
        extra = "\n\n" + flow() if flow is not None else ""
        full = getattr(self, "hardware_diagnostics_text", None)
        if full is not None:
            return full() + extra
        capabilities = self.hardware_capabilities()
        if capabilities is None:
            return i18n.translate("encoding.detecting")
        return capabilities.describe() + extra

    def _on_encoder_fallback(self, job_id: str, reason: str) -> None:
        """Le mode Auto a basculé un job vers le CPU : l'utilisateur en est informé."""
        panel = getattr(self, "export_panel", None)
        if panel is not None:
            panel.set_status(i18n.translate("encoding.fallback_notice", reason=reason), "running")

    def _shutdown_encoding(self) -> None:
        self._encoding_closed = True
