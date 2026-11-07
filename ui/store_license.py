"""Zone « Licence » des Préférences (build Store seulement, ADR-0013, étape 15) : l'état courant, « Gérer l'abonnement », « Restaurer »."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.entitlements.paywall import MANAGE_SUBSCRIPTION_URL
from core.entitlements.storekit_actions import ActionKind, ActionResult
from ui import i18n
from ui.design_system import StatusKind, TextRoles
from ui.store_texts import license_text, open_link, result_message
from ui.theme import set_role, set_state


class LicenseGroup(QGroupBox):
    """``controller`` : :class:`ui.store_entitlements.StoreEntitlementsController`. ``offers_requested`` : l'utilisateur veut voir les offres."""

    offers_requested = Signal()

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._busy = False
        self.setObjectName("storeLicenseGroup")
        layout = QVBoxLayout(self)
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        set_role(self.message_label, TextRoles.label_secondary)
        row = QHBoxLayout()
        self.offers_button = QPushButton()
        self.manage_button = QPushButton()
        self.restore_button = QPushButton()
        for button in (self.offers_button, self.manage_button, self.restore_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addWidget(self.state_label)
        layout.addLayout(row)
        layout.addWidget(self.message_label)
        self.offers_button.clicked.connect(self.offers_requested)
        self.manage_button.clicked.connect(lambda _checked=False: open_link(MANAGE_SUBSCRIPTION_URL))
        self.restore_button.clicked.connect(self._on_restore)
        controller.state_changed.connect(self.refresh)
        controller.action_finished.connect(self._on_action_finished)
        self.retranslate()

    def refresh(self, *_args) -> None:
        self.state_label.setText(license_text(self._controller.state()))
        self.restore_button.setEnabled(not self._busy)

    def retranslate(self) -> None:
        self.setTitle(i18n.translate("store.license.title"))
        self.offers_button.setText(i18n.translate("store.button.offers"))
        self.manage_button.setText(i18n.translate("store.link.manage"))
        self.restore_button.setText(i18n.translate("store.button.restore"))
        self.refresh()

    def _on_restore(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.message_label.setText(i18n.translate("store.msg.busy"))
        set_state(self.message_label, StatusKind.WORKING)
        self.restore_button.setEnabled(False)
        if not self._controller.restore():
            self._busy = False
            self.restore_button.setEnabled(True)

    def _on_action_finished(self, result: ActionResult) -> None:
        if result.kind is ActionKind.PRODUCTS:
            return
        self._busy = False
        self.message_label.setText(result_message(result))
        set_state(self.message_label, StatusKind.IDLE)
        self.refresh()
