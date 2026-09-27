"""Boîte de dialogue Préférences de Kut-Studio.

Cette boîte de dialogue permet de choisir :

- le thème visuel (Sombre / Clair / Système) ;
- la langue de l'interface (Français / English / Español).

Chaque modification est appliquée **immédiatement** par
:class:`MainWindow` : le thème est commuté via :class:`ThemeManager`,
la langue via :func:`ui.i18n.set_language`. Les préférences sont
persistées automatiquement à chaque modification par
``MainWindow.on_user_setting_changed``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ui.i18n import available_languages, current_language, translate


class PreferencesDialog(QDialog):
    """Fenêtre de préférences (thème, langue, performance, aperçu)."""

    theme_changed = Signal(str)
    language_changed = Signal(str)
    performance_changed = Signal(str)
    preview_quality_changed = Signal(str)
    restore_defaults_requested = Signal()

    def __init__(
        self,
        current_theme: str = "dark",
        current_language_code: str | None = None,
        current_performance: str = "auto",
        current_preview_quality: str = "auto",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.current_theme = current_theme
        self.current_language_code = current_language_code or current_language()
        self.current_performance = current_performance
        self.current_preview_quality = current_preview_quality
        self.setWindowTitle(translate("prefs.title"))
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # ----- Section apparence ------------------------------------------
        appearance_group = QGroupBox(translate("prefs.appearance"))
        appearance_layout = QVBoxLayout(appearance_group)
        appearance_layout.setSpacing(6)
        appearance_layout.setContentsMargins(14, 12, 14, 12)
        self.theme_group = QButtonGroup(self)
        for code, label in (
            ("dark", translate("prefs.theme.dark")),
            ("light", translate("prefs.theme.light")),
            ("system", translate("prefs.theme.system")),
        ):
            radio = QRadioButton(label)
            radio.setProperty("theme_code", code)
            self.theme_group.addButton(radio)
            self._theme_radios = getattr(self, "_theme_radios", {})
            self._theme_radios[code] = radio
            appearance_layout.addWidget(radio)
        # Sélection courante.
        if self.current_theme in self._theme_radios:
            self._theme_radios[self.current_theme].setChecked(True)
        self.theme_group.buttonClicked.connect(self._on_theme_chosen)
        layout.addWidget(appearance_group)

        # ----- Section langue ---------------------------------------------
        language_group = QGroupBox(translate("prefs.language"))
        language_layout = QVBoxLayout(language_group)
        language_layout.setSpacing(6)
        language_layout.setContentsMargins(14, 12, 14, 12)
        self.language_group = QButtonGroup(self)
        self._language_radios = {}
        for code in available_languages():
            radio = QRadioButton(translate(f"prefs.language.{code}"))
            radio.setProperty("language_code", code)
            self.language_group.addButton(radio)
            self._language_radios[code] = radio
            language_layout.addWidget(radio)
        if self.current_language_code in self._language_radios:
            self._language_radios[self.current_language_code].setChecked(True)
        self.language_group.buttonClicked.connect(self._on_language_chosen)
        layout.addWidget(language_group)

        performance_group = QGroupBox(translate("prefs.performance"))
        performance_layout = QVBoxLayout(performance_group)
        performance_layout.setSpacing(6)
        performance_layout.setContentsMargins(14, 12, 14, 12)
        self.performance_group = QButtonGroup(self)
        self._performance_radios = {}
        for code, key in (
            ("auto", "prefs.performance.auto"),
            ("low", "prefs.performance.low"),
            ("balanced", "prefs.performance.balanced"),
            ("high", "prefs.performance.high"),
        ):
            radio = QRadioButton(translate(key))
            radio.setProperty("performance_code", code)
            self.performance_group.addButton(radio)
            self._performance_radios[code] = radio
            performance_layout.addWidget(radio)
        if self.current_performance in self._performance_radios:
            self._performance_radios[self.current_performance].setChecked(True)
        self.performance_group.buttonClicked.connect(self._on_performance_chosen)
        layout.addWidget(performance_group)

        preview_group = QGroupBox(translate("prefs.preview"))
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setSpacing(6)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        self.preview_group = QButtonGroup(self)
        self._preview_radios = {}
        for code, key in (
            ("auto", "prefs.preview.auto"),
            ("full", "prefs.preview.full"),
            ("half", "prefs.preview.half"),
            ("quarter", "prefs.preview.quarter"),
            ("eighth", "prefs.preview.eighth"),
        ):
            radio = QRadioButton(translate(key))
            radio.setProperty("preview_code", code)
            self.preview_group.addButton(radio)
            self._preview_radios[code] = radio
            preview_layout.addWidget(radio)
        if self.current_preview_quality in self._preview_radios:
            self._preview_radios[self.current_preview_quality].setChecked(True)
        self.preview_group.buttonClicked.connect(self._on_preview_chosen)
        layout.addWidget(preview_group)

        # ----- Bouton "Restaurer les réglages par défaut" -------------------
        actions_row = QHBoxLayout()
        actions_row.addStretch()
        self.restore_button = QPushButton(translate("prefs.restore_defaults"))
        self.restore_button.clicked.connect(self._on_restore_defaults)
        actions_row.addWidget(self.restore_button)
        layout.addLayout(actions_row)

        # ----- Boutons OK / Annuler ---------------------------------------
        self.buttons_box = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons_box.rejected.connect(self.reject)
        layout.addWidget(self.buttons_box)

    def _on_theme_chosen(self, button) -> None:
        code = button.property("theme_code")
        if code and code != self.current_theme:
            self.current_theme = code
            self.theme_changed.emit(code)

    def _on_language_chosen(self, button) -> None:
        code = button.property("language_code")
        if code and code != self.current_language_code:
            self.current_language_code = code
            self.language_changed.emit(code)

    def _on_performance_chosen(self, button) -> None:
        code = button.property("performance_code")
        if code and code != self.current_performance:
            self.current_performance = code
            self.performance_changed.emit(code)

    def _on_preview_chosen(self, button) -> None:
        code = button.property("preview_code")
        if code and code != self.current_preview_quality:
            self.current_preview_quality = code
            self.preview_quality_changed.emit(code)

    def _on_restore_defaults(self) -> None:
        # Restaure thème, langue, profil et qualité d'aperçu.
        self._theme_radios.get("dark", None) and self._theme_radios["dark"].setChecked(True)
        self._language_radios.get("fr", None) and self._language_radios["fr"].setChecked(True)
        self._performance_radios.get("auto", None) and self._performance_radios["auto"].setChecked(True)
        self._preview_radios.get("auto", None) and self._preview_radios["auto"].setChecked(True)
        self.current_theme = "dark"
        self.current_language_code = "fr"
        self.current_performance = "auto"
        self.current_preview_quality = "auto"
        self.restore_defaults_requested.emit()
