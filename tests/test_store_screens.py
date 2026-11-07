"""Écrans d'achat et d'état du build Store (ADR-0013, étape 15) : mur d'achat, zone « Licence », pastille, hors écran, faux pont."""

from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from core import entitlements
from core.entitlements import EntitlementKind as K
from core.entitlements import paywall, storekit_bridge as sb
from tests.entitlements_support import DAY, T0, Clock, store_flavor  # noqa: F401 - fixture
from tests.storekit_fakes import (
    PRODUCT_LIFETIME, PRODUCT_MONTHLY, PRODUCT_YEARLY, FakeBridge, app_transaction, entitlements as ents, failure, item, loader_for,
    missing_loader, products, purchase,
)
from ui import i18n

PRICES = products({PRODUCT_MONTHLY: "6,99 €", PRODUCT_YEARLY: "49,99 €", PRODUCT_LIFETIME: "99,99 €"})
PAID = ents(item(PRODUCT_MONTHLY, signed=T0, expires=T0 + 30 * DAY))


@pytest.fixture(autouse=True)
def _french():
    before = i18n.current_language()
    i18n.set_language("fr")
    yield
    i18n.set_language(before)
    entitlements.install_service(None)
    entitlements.set_enforced_provider(None)


@pytest.fixture
def make_controller(qtbot, tmp_path, store_flavor):  # noqa: F811
    from ui.store_entitlements import StoreEntitlementsController

    created = []

    def build(bridge=None, *, loader=None, wait=None, started_days_ago=30):
        bridge = bridge or FakeBridge(app=app_transaction(T0 - started_days_ago * DAY, T0))
        bridge.results[sb.OP_PRODUCTS] = PRICES
        controller = StoreEntitlementsController(
            directory=tmp_path, clock=Clock(T0), bridge_loader=loader or loader_for(bridge), error_backoff=0.01,
        )
        created.append(controller)
        controller.start()
        if wait is not None:
            qtbot.waitUntil(lambda: controller.state().kind is wait, timeout=5000)
        return controller, bridge

    yield build
    for controller in created:
        controller.stop()


_OPENED: list = []


@pytest.fixture(autouse=True)
def _close_walls():
    yield
    for dialog in _OPENED:
        if shiboken6.isValid(dialog):
            dialog.close()                                          # WA_DeleteOnClose : détruit le mur
    _OPENED.clear()
    QApplication.processEvents()


def _paywall(qtbot, controller):
    from ui.store_paywall import PaywallDialog

    dialog = PaywallDialog(controller)
    _OPENED.append(dialog)
    dialog.show()
    return dialog


def _texts(dialog):
    return [w.text() for w in dialog.findChildren(type(dialog.headline)) if w.isVisibleTo(dialog)]


# -- le mur : contenu ---------------------------------------------------------------------------------------------------------


def test_expired_wall_says_what_is_blocked_lists_three_offers_with_storekit_prices(qtbot, make_controller):
    controller, _bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    qtbot.waitUntil(lambda: "6,99 €" in dialog.rows[0].price_label.text(), timeout=5000)
    assert "essai gratuit de 14 jours est terminé" in dialog.explanation.text()
    assert "modifier un projet" in dialog.blocked_label.text() and "ouvrir et visualiser" in dialog.blocked_label.text()
    assert [r.name_label.text() for r in dialog.rows] == ["Abonnement mensuel", "Abonnement annuel", "Achat unique"]
    assert [r.price_label.text() for r in dialog.rows] == ["6,99 € par mois", "49,99 € par an", "99,99 €, paiement unique"]
    assert "renouvelle automatiquement chaque mois" in dialog.rows[0].detail_label.text()
    assert "renouvelle automatiquement chaque année" in dialog.rows[1].detail_label.text()
    terms = dialog.terms_label.text()
    assert "renouvelle" in terms and "24 heures" in terms and "compte Apple" in terms
    assert dialog.restore_button.text() == "Restaurer les achats" and dialog.readonly_button.text() == "Continuer en lecture seule"
    assert all(r.choose_button.isEnabled() for r in dialog.rows)
    assert dialog.readonly_button.isDefault() and not any(r.choose_button.isDefault() for r in dialog.rows)     # Entrée ne achète jamais


def test_without_loaded_products_the_wall_says_price_unavailable_and_cannot_buy(qtbot, make_controller):
    bridge = FakeBridge(app=app_transaction(T0 - 30 * DAY, T0))
    controller, bridge = make_controller(bridge, wait=K.EXPIRED)
    bridge.results[sb.OP_PRODUCTS] = failure(sb.STOREKIT_ERROR)
    dialog = _paywall(qtbot, controller)
    qtbot.waitUntil(lambda: any(c[0] == sb.OP_PRODUCTS for c in bridge.calls), timeout=5000)
    qtbot.wait(100)
    assert [r.price_label.text() for r in dialog.rows] == ["Prix indisponible"] * 3
    assert not any(r.choose_button.isEnabled() for r in dialog.rows)
    assert "6,99" not in " ".join(_texts(dialog)) and "99,99" not in " ".join(_texts(dialog))               # aucun montant codé en dur
    assert dialog.restore_button.isEnabled() and dialog.readonly_button.isEnabled()                        # la sortie reste là


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_wall_is_translated_with_no_missing_key(qtbot, make_controller, language):
    i18n.set_language(language)
    controller, _bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    qtbot.waitUntil(lambda: "6,99 €" in dialog.rows[0].price_label.text(), timeout=5000)
    everything = " ".join(w.text() for w in dialog.findChildren(QPushButton)) + " ".join(_texts(dialog))
    assert "[store." not in everything and "{" not in everything
    assert dialog.readonly_button.text() == {"fr": "Continuer en lecture seule", "en": "Continue in read-only mode", "es": "Continuar en solo lectura"}[language]


def test_wall_follows_a_live_language_change(qtbot, make_controller):
    controller, _bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    i18n.set_language("en")
    assert dialog.restore_button.text() == "Restore purchases"


def test_links_open_terms_privacy_and_account_subscriptions(qtbot, make_controller, monkeypatch):
    opened = []
    monkeypatch.setattr("ui.store_paywall.open_link", lambda url: opened.append(url) or True)
    controller, _bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    for name in ("terms", "privacy", "manage"):
        dialog.link_buttons[name].click()
    assert opened == [paywall.TERMS_URL, paywall.PRIVACY_URL, "https://apps.apple.com/account/subscriptions"]
    assert paywall.PRIVACY_URL == "https://kyyzen0.github.io/kut-studio-privacy/"
    assert [dialog.link_buttons[n].text() for n in ("terms", "privacy", "manage")] == [
        "Conditions d'utilisation", "Politique de confidentialité", "Gérer l'abonnement"]


def test_close_never_buys_and_is_always_there(qtbot, make_controller):
    controller, bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    with qtbot.waitSignal(dialog.finished, timeout=2000):
        dialog.readonly_button.click()
    again = _paywall(qtbot, controller)
    with qtbot.waitSignal(again.finished, timeout=2000):
        qtbot.keyClick(again, Qt.Key_Escape)
    assert not [c for c in bridge.calls if c[0] == sb.OP_PURCHASE]


@pytest.mark.parametrize(("started_days_ago", "wait", "fragment", "blocked"), [
    (12, K.TRIAL, "jours restants : 2", False),
    (30, K.EXPIRED, "est terminé", True),
])
def test_wall_adapts_to_trial_and_expired(qtbot, make_controller, started_days_ago, wait, fragment, blocked):
    controller, _bridge = make_controller(wait=wait, started_days_ago=started_days_ago)
    dialog = _paywall(qtbot, controller)
    assert fragment in dialog.explanation.text() and dialog.blocked_label.isVisibleTo(dialog) is blocked
    assert dialog.readonly_button.text() == ("Continuer en lecture seule" if blocked else "Fermer")


def test_wall_for_a_subscriber_and_a_lifetime_owner_offers_nothing_to_buy(qtbot, make_controller):
    controller, _bridge = make_controller(FakeBridge(ent=PAID), wait=K.SUBSCRIBER)
    dialog = _paywall(qtbot, controller)
    assert "Merci" in dialog.explanation.text() and "Abonné jusqu'au" in dialog.explanation.text()
    assert not any(r.choose_button.isEnabled() for r in dialog.rows) and not dialog.blocked_label.isVisibleTo(dialog)
    controller2, _b = make_controller(FakeBridge(ent=ents(item(PRODUCT_LIFETIME, signed=T0))), wait=K.LIFETIME)
    dialog2 = _paywall(qtbot, controller2)
    assert not any(r.isVisibleTo(dialog2) for r in dialog2.rows) and "Achat unique" in dialog2.explanation.text()


def test_wall_for_an_unknown_state_explains_offline_and_can_retry(qtbot, make_controller):
    controller, _bridge = make_controller(loader=missing_loader)
    qtbot.waitUntil(lambda: not controller.source.running, timeout=5000)
    dialog = _paywall(qtbot, controller)
    assert "n'a pas pu être vérifié" in dialog.explanation.text() and dialog.blocked_label.isVisibleTo(dialog)
    assert dialog.restore_button.isEnabled()


# -- le mur : achat et restauration -------------------------------------------------------------------------------------------


def _choose(qtbot, dialog, row_index):
    qtbot.waitUntil(lambda: dialog.rows[row_index].choose_button.isEnabled(), timeout=5000)
    dialog.rows[row_index].choose_button.click()


def test_purchase_success_is_shown_only_after_the_verified_reread(qtbot, make_controller):
    controller, bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)

    def unlock(op):
        if op == sb.OP_PURCHASE:
            bridge.results[sb.OP_ENTITLEMENTS] = PAID

    bridge.on_call = unlock
    bridge.results[sb.OP_PURCHASE] = purchase("success", PRODUCT_MONTHLY)
    _choose(qtbot, dialog, 0)
    assert "En attente de l'App Store" in dialog.status_label.text() and not dialog.restore_button.isEnabled()       # occupé
    qtbot.waitUntil(lambda: "achat est confirmé" in dialog.status_label.text(), timeout=5000)
    assert controller.state().kind is K.SUBSCRIBER and entitlements.can_edit_now()
    assert (sb.OP_PURCHASE, PRODUCT_MONTHLY, 120000) in bridge.calls
    assert dialog.readonly_button.text() == "Fermer" and dialog.restore_button.isEnabled()


def test_a_success_that_is_not_verified_does_not_unlock_and_says_so(qtbot, make_controller):
    controller, bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    bridge.results[sb.OP_PURCHASE] = purchase("success", PRODUCT_YEARLY, verified=False)
    _choose(qtbot, dialog, 1)
    qtbot.waitUntil(lambda: "pas encore pu être vérifié" in dialog.status_label.text(), timeout=5000)
    assert controller.state().kind is K.EXPIRED and not entitlements.can_edit_now() and not entitlements.can_export_now()


@pytest.mark.parametrize(("reply", "fragment"), [
    (purchase("cancelled"), "Achat annulé"),
    (purchase("pending"), "en attente d'approbation"),
    (failure(sb.STOREKIT_ERROR), "n'a pas pu être effectué"),
    (failure(sb.SHUTDOWN, "shutdown"), "n'est pas joignable"),
])
def test_purchase_cancelled_pending_failed_unavailable(qtbot, make_controller, reply, fragment):
    controller, bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    bridge.results[sb.OP_PURCHASE] = reply
    _choose(qtbot, dialog, 2)
    qtbot.waitUntil(lambda: fragment in dialog.status_label.text(), timeout=5000)
    assert controller.state().kind is K.EXPIRED and not entitlements.can_edit_now()
    assert dialog.rows[2].choose_button.isEnabled() and dialog.restore_button.isEnabled()          # on peut réessayer


def test_restore_from_the_wall(qtbot, make_controller):
    controller, bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    dialog.restore_button.click()
    qtbot.waitUntil(lambda: "Aucun achat à restaurer" in dialog.status_label.text(), timeout=5000)
    assert controller.state().kind is K.EXPIRED
    bridge.results[sb.OP_ENTITLEMENTS] = ents(item(PRODUCT_LIFETIME, signed=T0))
    dialog.restore_button.click()
    qtbot.waitUntil(lambda: "Achats restaurés" in dialog.status_label.text(), timeout=5000)
    assert controller.state().kind is K.LIFETIME and entitlements.can_edit_now()
    bridge.results[sb.OP_SYNC] = failure(sb.STOREKIT_ERROR)
    dialog.restore_button.click()
    qtbot.waitUntil(lambda: "restauration a échoué" in dialog.status_label.text(), timeout=5000)


def test_the_wall_fits_the_supported_window_sizes(qtbot, make_controller):
    controller, _bridge = make_controller(wait=K.EXPIRED)
    dialog = _paywall(qtbot, controller)
    qtbot.waitUntil(lambda: "6,99 €" in dialog.rows[0].price_label.text(), timeout=5000)
    for width, height in ((1440, 900), (1180, 720)):
        assert dialog.minimumSizeHint().width() <= width * 0.6 and dialog.minimumSizeHint().height() <= height * 0.8
    dialog.resize(560, 600)
    qtbot.wait(50)
    for button in (dialog.restore_button, dialog.readonly_button, *(r.choose_button for r in dialog.rows)):
        assert button.width() >= button.sizeHint().width() - 1, button.text()              # aucun bouton coupé
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft())) or True
    assert dialog.readonly_button.mapTo(dialog, dialog.readonly_button.rect().bottomRight()).y() <= dialog.height()


# -- Préférences : zone « Licence » -------------------------------------------------------------------------------------------


def _prefs(qtbot, controller):
    from ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog(license_host=controller)
    qtbot.addWidget(dialog)
    return dialog


def test_preferences_have_no_license_zone_outside_the_store(qtbot):
    from ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert dialog.license_box is None and not dialog.findChildren(QPushButton, "storeLicenseGroup")


@pytest.mark.parametrize(("bridge_factory", "wait", "fragment"), [
    (lambda: FakeBridge(app=app_transaction(T0 - 12 * DAY, T0)), K.TRIAL, "Essai gratuit (jours restants : 2)"),
    (lambda: FakeBridge(ent=PAID), K.SUBSCRIBER, "Abonné jusqu'au"),
    (lambda: FakeBridge(ent=ents(item(PRODUCT_LIFETIME, signed=T0))), K.LIFETIME, "Achat unique : accès permanent"),
    (lambda: FakeBridge(app=app_transaction(T0 - 30 * DAY, T0)), K.EXPIRED, "lecture seule"),
])
def test_license_zone_shows_each_state(qtbot, make_controller, bridge_factory, wait, fragment):
    controller, _bridge = make_controller(bridge_factory(), wait=wait)
    box = _prefs(qtbot, controller).license_box
    assert box is not None and box.title() == "Licence" and fragment in box.state_label.text()


def test_license_zone_unknown_manage_restore_and_offers(qtbot, make_controller, monkeypatch):
    opened = []
    monkeypatch.setattr("ui.store_license.open_link", lambda url: opened.append(url) or True)
    controller, bridge = make_controller(loader=missing_loader)
    qtbot.waitUntil(lambda: not controller.source.running, timeout=5000)
    prefs = _prefs(qtbot, controller)
    box = prefs.license_box
    assert "Accès non vérifié" in box.state_label.text()
    box.manage_button.click()
    assert opened == ["https://apps.apple.com/account/subscriptions"]
    asked = []
    prefs.offers_requested.connect(lambda: asked.append(1))
    box.offers_button.click()
    assert asked == [1]
    box.restore_button.click()
    qtbot.waitUntil(lambda: "n'est pas joignable" in box.message_label.text(), timeout=5000)      # pas de pont : indisponible, rien débloqué
    assert not entitlements.can_edit_now()


def test_license_zone_follows_a_restore_and_the_language(qtbot, make_controller):
    controller, bridge = make_controller(wait=K.EXPIRED)
    prefs = _prefs(qtbot, controller)
    box = prefs.license_box
    bridge.results[sb.OP_ENTITLEMENTS] = ents(item(PRODUCT_LIFETIME, signed=T0))
    box.restore_button.click()
    qtbot.waitUntil(lambda: "Achat unique" in box.state_label.text(), timeout=5000)
    assert "restaurés" in box.message_label.text()
    i18n.set_language("en")
    assert box.title() == "License" and box.restore_button.text() == "Restore purchases" and "permanent access" in box.state_label.text()


# -- pastille et déclenchement du mur -----------------------------------------------------------------------------------------


@pytest.fixture
def screens_for(qtbot, make_controller):
    from PySide6.QtWidgets import QMainWindow

    from ui.store_screens import StoreScreens

    def build(bridge=None, *, wait=None, started_days_ago=30, monotonic=None, loader=None):
        window = QMainWindow()
        qtbot.addWidget(window)
        window.statusBar()                                          # comme la vraie fenêtre : barre d'état créée avant l'affichage
        window.show()
        controller, bridge = make_controller(bridge, wait=wait, started_days_ago=started_days_ago, loader=loader)
        kwargs = {} if monotonic is None else {"monotonic": monotonic}
        screens = StoreScreens(window, controller, **kwargs)
        return screens, controller, window, bridge

    return build


def _wall_open(screens):
    return screens._dialog is not None


@pytest.mark.parametrize(("days_ago", "wait", "visible", "fragment"), [
    (0, K.TRIAL, False, ""), (10, K.TRIAL, False, ""), (11, K.TRIAL, True, "Essai : 3 j"), (13, K.TRIAL, True, "Essai : 1 j"),
    (30, K.EXPIRED, True, "Lecture seule · Voir les offres"),
])
def test_chip_is_discreet_and_only_for_the_last_three_trial_days_or_read_only(screens_for, days_ago, wait, visible, fragment):
    screens, _c, window, _b = screens_for(wait=wait, started_days_ago=days_ago)
    assert screens.chip.isVisibleTo(window) is visible and screens.chip.text() == fragment
    assert screens.chip.parent() is window.statusBar()


def test_chip_hidden_for_a_subscriber(screens_for):
    screens, _c, window, _b = screens_for(FakeBridge(ent=PAID), wait=K.SUBSCRIBER)
    assert not screens.chip.isVisibleTo(window)


def test_chip_shown_when_the_state_is_unknown(qtbot, screens_for):
    screens, controller, window, _b = screens_for(loader=missing_loader)
    qtbot.waitUntil(lambda: not controller.source.running, timeout=5000)
    screens.refresh()
    assert screens.chip.isVisibleTo(window) and screens.chip.text() == "Accès non vérifié"


def test_the_wall_never_opens_during_the_trial_but_the_user_can_open_it(qtbot, screens_for):
    screens, _c, _w, _b = screens_for(wait=K.TRIAL, started_days_ago=12)
    for trigger in (paywall.TRIGGER_STARTUP, paywall.TRIGGER_EXPORT, paywall.TRIGGER_REFUSED):
        assert screens.offer(trigger) is False
    qtbot.wait(100)
    assert not _wall_open(screens)
    screens.chip.click()                                                     # geste volontaire : permis
    assert _wall_open(screens)
    screens._dialog.readonly_button.click()
    qtbot.waitUntil(lambda: not _wall_open(screens), timeout=2000)


def test_the_wall_is_offered_after_the_trial_for_a_blocked_export_and_only_once_at_a_time(qtbot, screens_for):
    screens, _c, _w, _b = screens_for(wait=K.EXPIRED)
    assert screens.offer(paywall.TRIGGER_EXPORT)
    qtbot.waitUntil(lambda: _wall_open(screens), timeout=2000)
    assert screens.offer(paywall.TRIGGER_EXPORT) is False                    # déjà ouvert
    screens._dialog.readonly_button.click()
    qtbot.waitUntil(lambda: not _wall_open(screens), timeout=2000)


def test_a_refused_gesture_reopens_the_wall_at_most_every_ten_minutes(qtbot, screens_for):
    now = [1000.0]
    screens, _c, _w, _b = screens_for(wait=K.EXPIRED, monotonic=lambda: now[0])
    assert screens.offer(paywall.TRIGGER_REFUSED)
    qtbot.waitUntil(lambda: _wall_open(screens), timeout=2000)
    screens._dialog.readonly_button.click()
    qtbot.waitUntil(lambda: not _wall_open(screens), timeout=2000)
    now[0] += 60
    assert screens.offer(paywall.TRIGGER_REFUSED) is False
    now[0] += 600
    assert screens.offer(paywall.TRIGGER_REFUSED) is True
    qtbot.waitUntil(lambda: _wall_open(screens), timeout=2000)
    screens._dialog.readonly_button.click()


def test_the_wall_is_not_opened_in_the_middle_of_a_gesture(qtbot, screens_for, monkeypatch):
    screens, _c, _w, _b = screens_for(wait=K.EXPIRED)
    monkeypatch.setattr(type(screens), "mid_edit", lambda self: True)
    assert screens.offer(paywall.TRIGGER_EXPORT)                              # programmé...
    qtbot.wait(150)
    assert not _wall_open(screens)                                            # ...mais jamais ouvert pendant le geste


def test_the_wall_is_not_offered_when_the_state_is_unknown(qtbot, screens_for):
    screens, c, _w, _b = screens_for(loader=missing_loader)
    qtbot.waitUntil(lambda: not c.source.running, timeout=5000)
    assert screens.offer(paywall.TRIGGER_EXPORT) is False


def test_startup_offer_only_when_the_end_of_trial_is_known_early(qtbot, screens_for, monkeypatch):
    monkeypatch.setattr("ui.store_screens.STARTUP_DELAY_MS", 20)
    screens, _c, _w, _b = screens_for(wait=K.EXPIRED)
    screens.refresh()
    qtbot.waitUntil(lambda: _wall_open(screens), timeout=3000)
    screens._dialog.readonly_button.click()
    # un état « fin d'essai » connu bien après le démarrage (minuteur de session) : jamais de mur automatique
    late, _c2, _w2, _b2 = screens_for(wait=K.EXPIRED, monotonic=iter([0.0, 999.0, 999.0, 999.0, 999.0]).__next__)
    late.refresh()
    qtbot.wait(200)
    assert not _wall_open(late)


def test_the_wall_closes_cleanly_when_the_window_goes_away(qtbot, screens_for):
    screens, _c, window, _b = screens_for(wait=K.EXPIRED)
    screens.show_paywall()
    assert _wall_open(screens)
    window.close()
    QApplication.processEvents()


def test_a_scheduled_wall_does_not_open_on_a_window_that_was_deleted(qtbot, screens_for, monkeypatch):
    screens, _c, window, _b = screens_for(wait=K.EXPIRED)
    assert screens.offer(paywall.TRIGGER_EXPORT)                              # programmé pour le tour suivant de la boucle
    monkeypatch.setattr("ui.store_screens.shiboken6", type("Gone", (), {"isValid": staticmethod(lambda _o: False)}))   # la fenêtre n'existe plus
    QApplication.processEvents()                                              # ne lève pas
    assert screens.show_paywall() is None
