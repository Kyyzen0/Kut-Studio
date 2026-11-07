"""Les écrans d'achat et d'état de la fenêtre principale, build Store seulement (ADR-0013, étape 15).

* **Pastille** discrète dans la barre d'état (jamais bloquante) : « Essai : N j » les trois derniers jours, « Lecture seule · Voir les
  offres » après l'essai, « Accès non vérifié » si l'état est inconnu ; un clic ouvre les offres (geste de l'utilisateur).
* **Mur d'achat** proposé par :meth:`StoreScreens.offer` selon :func:`core.entitlements.paywall.may_show_paywall` : seulement quand
  l'essai est fini, jamais au milieu d'une édition (bouton de souris enfoncé, boîte modale ouverte, mur déjà ouvert), et au plus une fois
  toutes les ``MIN_REOFFER_S`` secondes pour les gestes refusés. Ouvert au tour suivant de la boucle d'événements (jamais dans un
  ``dropEvent`` ou un gestionnaire de commande).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from core.entitlements import EntitlementKind
from core.entitlements.paywall import TRIGGER_MANUAL, TRIGGER_REFUSED, TRIGGER_STARTUP, banner_days, may_show_paywall
from ui import i18n
from ui.store_paywall import PaywallDialog

MIN_REOFFER_S = 600.0
"""Un geste refusé ne rouvre pas le mur plus d'une fois toutes les dix minutes (la pastille reste, elle)."""
STARTUP_DELAY_MS = 2500
STARTUP_WINDOW_S = 30.0
"""Le mur « au démarrage » ne se déclenche que si l'état fin d'essai est connu dans les 30 premières secondes : une fin d'essai en cours de
session (minuteur de la fenêtre) ne fait jamais apparaître un mur au milieu du travail ; la pastille et le premier geste refusé suffisent."""


class StoreScreens(QObject):
    """``window`` : la fenêtre principale (barre d'état, parent des boîtes) ; ``controller`` : le contrôleur StoreKit."""

    def __init__(self, window, controller, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._monotonic = monotonic
        self._last_offer = float("-inf")
        self._created = monotonic()
        self._startup_scheduled = False
        self._dialog: PaywallDialog | None = None
        self.chip = QPushButton()
        self.chip.setFlat(True)
        self.chip.setObjectName("storeChip")
        self.chip.setCursor(Qt.PointingHandCursor)
        self.chip.clicked.connect(lambda _checked=False: self.show_paywall(TRIGGER_MANUAL))
        window.statusBar().addPermanentWidget(self.chip)
        controller.state_changed.connect(self.refresh)
        i18n.subscribe(self._on_language)
        callback = self._on_language
        self.destroyed.connect(lambda *_args: i18n.unsubscribe(callback))
        self.refresh()

    def _on_language(self, _code: str) -> None:
        self.refresh()

    # -- la pastille --------------------------------------------------------------------------------------------------------

    def refresh(self, *_args) -> None:
        state = self._controller.state()
        days = banner_days(state)
        if days is not None:
            self.chip.setText(i18n.translate("store.chip.trial", days=days))
            self.chip.setToolTip(i18n.translate("store.chip.trial.tip"))
        elif state.kind is EntitlementKind.EXPIRED:
            self.chip.setText(i18n.translate("store.chip.readonly"))
            self.chip.setToolTip(i18n.translate("store.chip.readonly.tip"))
        elif state.kind is EntitlementKind.UNKNOWN:
            self.chip.setText(i18n.translate("store.chip.unknown"))
            self.chip.setToolTip(i18n.translate("store.chip.unknown.tip"))
        else:
            self.chip.setText("")
        self.chip.setVisible(bool(self.chip.text()))
        if state.kind is EntitlementKind.EXPIRED and not self._startup_scheduled and self._monotonic() - self._created <= STARTUP_WINDOW_S:
            self._startup_scheduled = True
            QTimer.singleShot(STARTUP_DELAY_MS, lambda: self.offer(TRIGGER_STARTUP))

    # -- le mur d'achat -----------------------------------------------------------------------------------------------------

    def mid_edit(self) -> bool:
        """Un geste est en cours : bouton de souris enfoncé ou boîte modale ouverte. Aucun mur automatique à ce moment."""
        return bool(QApplication.mouseButtons()) or QApplication.activeModalWidget() is not None

    def offer(self, trigger: str) -> bool:
        """Propose le mur selon la règle ; vrai s'il a été programmé. Ne bloque jamais l'appelant."""
        if self._dialog is not None:
            return False
        if trigger == TRIGGER_REFUSED and self._monotonic() - self._last_offer < MIN_REOFFER_S:
            return False
        if not may_show_paywall(self._controller.state(), trigger):
            return False
        self._last_offer = self._monotonic()
        QTimer.singleShot(0, lambda t=trigger: self._open_if_still_allowed(t))
        return True

    def _open_if_still_allowed(self, trigger: str) -> None:
        if self._dialog is None and may_show_paywall(self._controller.state(), trigger, mid_edit=self.mid_edit()):
            self.show_paywall(trigger)

    def show_paywall(self, trigger: str = TRIGGER_MANUAL) -> PaywallDialog | None:
        """Ouvre le mur (un seul à la fois). ``manual`` : l'utilisateur l'a demandé, toujours permis."""
        if self._dialog is not None:
            self._dialog.raise_()
            return self._dialog
        if not may_show_paywall(self._controller.state(), trigger):
            return None
        dialog = PaywallDialog(self._controller, self._window)
        self._dialog = dialog
        dialog.finished.connect(lambda _result=0: setattr(self, "_dialog", None))
        dialog.open()                                                # non bloquant : la boucle d'événements continue
        return dialog
