"""Le mur d'achat non agressif du build Store (ADR-0013, étape 15). **Importé seulement dans le build Store.**

* Il dit ce qui est bloqué et ce qui reste libre (ouvrir, visualiser), liste les trois offres avec le **prix localisé de StoreKit**
  (« Prix indisponible » tant que les produits ne sont pas chargés : aucun montant n'est codé en dur), l'essentiel du renouvellement
  automatique, les liens vers les conditions d'utilisation et la politique de confidentialité, « Restaurer les achats » et
  « Continuer en lecture seule » (toujours là). Pas de compte à rebours, pas de culpabilisation ; la croix et Échap ferment.
* Il ne débloque rien : un achat part au pont (fil Python), et seul l'état relu et **vérifié** (``state_changed``) change l'écran.
* Quand il s'ouvre tout seul (état ``EXPIRED``) la décision vient de :func:`core.entitlements.paywall.may_show_paywall` (``StoreScreens``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.entitlements import EntitlementKind, EntitlementState
from core.entitlements.paywall import MANAGE_SUBSCRIPTION_URL, PRIVACY_URL, TERMS_URL
from core.entitlements.storekit_actions import PAID_KINDS, ActionKind, ActionResult, ActionStatus, Offer, offers
from ui import i18n
from ui.design_system import DIALOG_MARGINS, StatusKind, TextRoles
from ui.keyboard_navigation import set_single_default
from ui.store_texts import license_text, open_link, result_message, trial_days
from ui.theme import set_role, set_state

_LINKS = {"terms": TERMS_URL, "privacy": PRIVACY_URL, "manage": MANAGE_SUBSCRIPTION_URL}


class _OfferRow(QFrame):
    """Une offre : nom, détail (renouvellement), prix localisé et bouton « Choisir »."""

    def __init__(self, offer: Offer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.offer = offer
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName(f"storeOffer_{offer.key}")
        row = QHBoxLayout(self)
        text = QVBoxLayout()
        self.name_label = QLabel()
        set_role(self.name_label, TextRoles.label)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        set_role(self.detail_label, TextRoles.label_secondary)
        self.price_label = QLabel()
        text.addWidget(self.name_label)
        text.addWidget(self.detail_label)
        text.addWidget(self.price_label)
        row.addLayout(text, 1)
        self.choose_button = QPushButton()
        self.choose_button.setObjectName(f"storeChoose_{offer.key}")
        row.addWidget(self.choose_button, 0, Qt.AlignVCenter)

    def retranslate(self, price: str | None) -> None:
        key = self.offer.key
        self.name_label.setText(i18n.translate(f"store.offer.{key}.name"))
        self.detail_label.setText(i18n.translate(f"store.offer.{key}.detail"))
        self.price_label.setText(
            i18n.translate(f"store.offer.{key}.price", price=price) if price else i18n.translate("store.offer.price_unavailable"),
        )
        self.choose_button.setText(i18n.translate("store.offer.choose"))
        self.choose_button.setAccessibleName(i18n.translate("store.offer.choose.accessible", name=self.name_label.text()))


class PaywallDialog(QDialog):
    """``controller`` : :class:`ui.store_entitlements.StoreEntitlementsController` (état, prix, achat, restauration)."""

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._busy = False
        self._message = ""
        self._message_kind = StatusKind.IDLE
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._build_ui()
        self._i18n_callback = lambda _code: self._retranslate()
        i18n.subscribe(self._i18n_callback)
        self.finished.connect(self._release)
        controller.state_changed.connect(self._retranslate)
        controller.action_finished.connect(self._on_action_finished)
        self._retranslate()
        controller.load_products()

    # -- construction -------------------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setFocusPolicy(Qt.NoFocus)
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.headline = self._label(body, TextRoles.panel_title)
        self.explanation = self._label(body, TextRoles.label)
        self.blocked_label = self._label(body, TextRoles.label_secondary)
        self.rows = [_OfferRow(offer) for offer in offers(self._controller.verifier.products)]
        for row in self.rows:
            row.choose_button.clicked.connect(lambda _checked=False, r=row: self._on_choose(r))
            body.addWidget(row)
        self.status_label = self._label(body, TextRoles.label)
        self.status_label.setObjectName("storePaywallStatus")
        self.status_label.setAccessibleName("status")
        self.terms_label = self._label(body, TextRoles.helper)
        links = QHBoxLayout()
        self.link_buttons: dict[str, QPushButton] = {}
        for name in ("terms", "privacy", "manage"):
            button = QPushButton()
            button.setFlat(True)
            button.setObjectName(f"storeLink_{name}")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, url=_LINKS[name]: open_link(url))
            self.link_buttons[name] = button
            links.addWidget(button)
        links.addStretch(1)
        body.addLayout(links)
        body.addStretch(1)
        scroll.setWidget(page)
        root.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        self.restore_button = QPushButton()
        self.restore_button.setObjectName("storeRestore")
        self.restore_button.clicked.connect(self._on_restore)
        self.readonly_button = QPushButton()
        self.readonly_button.setObjectName("storeReadOnly")
        self.readonly_button.clicked.connect(self.reject)
        buttons.addWidget(self.restore_button)
        buttons.addStretch(1)
        buttons.addWidget(self.readonly_button)
        root.addLayout(buttons)
        set_single_default(self, self.readonly_button)              # Entrée = fermer, jamais acheter

    @staticmethod
    def _label(layout: QVBoxLayout, role) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        set_role(label, role)
        layout.addWidget(label)
        return label

    # -- texte et état ------------------------------------------------------------------------------------------------------

    def _state(self) -> EntitlementState:
        return self._controller.state()

    def _retranslate(self, *_args) -> None:
        state = self._state()
        kind = state.kind
        self.setWindowTitle(i18n.translate("store.paywall.title"))
        self.headline.setText(i18n.translate("store.paywall.title"))
        if kind is EntitlementKind.EXPIRED:
            self.explanation.setText(i18n.translate("store.paywall.expired", days=trial_days()))
        elif kind is EntitlementKind.TRIAL:
            self.explanation.setText(i18n.translate("store.paywall.trial", days=state.days_left or 0))
        elif kind in PAID_KINDS:
            self.explanation.setText(f"{i18n.translate('store.paywall.paid')} {license_text(state)}")
        else:
            self.explanation.setText(i18n.translate("store.paywall.unknown"))
        blocked = kind in (EntitlementKind.EXPIRED, EntitlementKind.UNKNOWN)
        self.blocked_label.setVisible(blocked)
        self.blocked_label.setText(i18n.translate("store.paywall.blocked"))
        lifetime = kind is EntitlementKind.LIFETIME
        for row in self.rows:
            info = self._controller.products.get(row.offer.product_id)
            row.retranslate(info.display_price if info is not None else None)
            row.setVisible(not lifetime)
            row.choose_button.setEnabled(info is not None and not self._busy and kind not in PAID_KINDS)
        self.status_label.setText(self._message)
        self.status_label.setVisible(bool(self._message))
        set_state(self.status_label, self._message_kind)
        self.terms_label.setText(i18n.translate("store.terms.subscription"))
        for name, button in self.link_buttons.items():
            button.setText(i18n.translate(f"store.link.{name}"))
        self.restore_button.setText(i18n.translate("store.button.restore"))
        self.restore_button.setEnabled(not self._busy)
        done = kind in PAID_KINDS or kind is EntitlementKind.TRIAL
        self.readonly_button.setText(i18n.translate("store.button.close" if done else "store.button.readonly"))

    def _say(self, message: str, kind: StatusKind) -> None:
        self._message, self._message_kind = message, kind
        self._retranslate()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._say(i18n.translate("store.msg.busy"), StatusKind.WORKING)
        else:
            self._retranslate()

    # -- gestes -------------------------------------------------------------------------------------------------------------

    def _on_choose(self, row: _OfferRow) -> None:
        if self._busy:
            return
        self._set_busy(True)
        if not self._controller.buy(row.offer.product_id):          # une autre action tourne déjà : le résultat BUSY arrive par le signal
            self._set_busy(False)

    def _on_restore(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        if not self._controller.restore():
            self._set_busy(False)

    def _on_action_finished(self, result: ActionResult) -> None:
        if result.kind is ActionKind.PRODUCTS:
            self._retranslate()                                     # les prix localisés arrivent
            return
        self._busy = False
        success = result.status is ActionStatus.DONE
        kind = StatusKind.SUCCESS if success else StatusKind.WARNING
        self._say(result_message(result), kind)

    def _release(self, *_args) -> None:
        i18n.unsubscribe(self._i18n_callback)


__all__ = ["PaywallDialog"]
