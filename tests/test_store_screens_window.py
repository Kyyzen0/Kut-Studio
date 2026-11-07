"""Les écrans du build Store dans la vraie fenêtre (ADR-0013, étape 15) : refus qui ouvrent le mur, jamais en dehors du Store, jamais en essai."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import shiboken6
from main_window_harness import build_window, install_dialogs

from core import entitlements
from core.entitlements import EntitlementKind as K
from core.entitlements import paywall
from tests.entitlements_support import DAY, T0, Clock, store_flavor  # noqa: F401 - fixture
from tests.storekit_fakes import FakeBridge, app_transaction, loader_for
from ui import i18n
from ui.preferences_dialog import PreferencesDialog

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _french():
    before = i18n.current_language()
    i18n.set_language("fr")
    yield
    i18n.set_language(before)
    entitlements.install_service(None)
    entitlements.set_enforced_provider(None)


@pytest.fixture
def store_window(qtbot, monkeypatch, tmp_path, store_flavor):  # noqa: F811
    """Fenêtre du build Store dont le pont est un faux : ``store_window(jours_depuis_le_début_de_l_essai)``."""
    from ui.store_entitlements import StoreEntitlementsController

    install_dialogs(monkeypatch)
    monkeypatch.setattr("ui.main_window_mixins.entitlements.is_store", lambda: True)
    monkeypatch.setattr("ui.store_screens.STARTUP_DELAY_MS", 10_000_000)             # pas de mur de démarrage dans ces tests
    made = []

    def build(started_days_ago: int):
        bridge = FakeBridge(app=app_transaction(T0 - started_days_ago * DAY, T0))
        monkeypatch.setitem(StoreEntitlementsController.__init__.__kwdefaults__, "bridge_loader", loader_for(bridge))
        monkeypatch.setitem(StoreEntitlementsController.__init__.__kwdefaults__, "clock", Clock(T0))
        window = build_window(qtbot, monkeypatch, tmp_path / "config")
        made.append(window)
        wait = K.EXPIRED if started_days_ago >= 14 else K.TRIAL
        qtbot.waitUntil(lambda: window._store_entitlements.state().kind is wait, timeout=5000)
        window.refresh_entitlements()
        return window, bridge

    yield build
    for window in made:
        window._store_entitlements.stop()
        wall = window._store_screens._dialog
        if wall is not None and shiboken6.isValid(wall):
            wall.close()


def _wall(window):
    return window._store_screens._dialog


def test_a_blocked_export_proposes_the_wall_after_the_trial(qtbot, store_window):
    window, _bridge = store_window(30)
    assert window._export_refused_ui() is True
    qtbot.waitUntil(lambda: _wall(window) is not None, timeout=3000)
    assert "essai gratuit de 14 jours est terminé" in _wall(window).explanation.text()
    assert "Lecture seule" in window.statusBar().currentMessage() or "essai est terminé" in window.statusBar().currentMessage()
    _wall(window).readonly_button.click()                                               # « Continuer en lecture seule »
    qtbot.waitUntil(lambda: _wall(window) is None, timeout=2000)
    assert not entitlements.can_edit_now() and entitlements.edit_refusal() is not None   # la lecture seule continue, projets visibles


def test_a_refused_command_has_an_actionable_message_that_opens_the_wall(qtbot, store_window):
    window, _bridge = store_window(30)
    assert window._command_allowed("play_pause") is True                              # lecture libre : jamais de mur
    qtbot.wait(100)
    assert _wall(window) is None
    assert window._command_allowed("delete_clip") is False                          # commande d'édition, essai fini
    qtbot.waitUntil(lambda: _wall(window) is not None, timeout=3000)
    assert window._store_screens.chip.isVisibleTo(window) and window._store_screens.chip.text().startswith("Lecture seule")


def test_a_deferred_history_refusal_never_opens_the_wall(qtbot, store_window):
    """Un enregistrement refusé par l'historique peut tomber au milieu d'une édition : message dans la barre d'état, jamais de mur."""
    window, _bridge = store_window(30)
    window._on_edit_refused(entitlements.edit_refusal())
    qtbot.wait(200)
    assert _wall(window) is None and window.statusBar().currentMessage()


def test_during_the_trial_nothing_is_blocked_and_no_wall_is_proposed(qtbot, store_window):
    window, _bridge = store_window(5)
    assert window._export_refused_ui() is False and window._edit_refused_ui() is False
    qtbot.wait(100)
    assert _wall(window) is None and not window._store_screens.chip.isVisibleTo(window)


def test_the_chip_shows_the_last_three_days_then_a_click_opens_the_offers(qtbot, store_window):
    window, _bridge = store_window(12)
    chip = window._store_screens.chip
    assert chip.isVisibleTo(window) and chip.text() == "Essai : 2 j"
    chip.click()
    assert _wall(window) is not None and "jours restants : 2" in _wall(window).explanation.text()


def test_preferences_show_the_license_zone_and_open_the_offers(qtbot, store_window):
    window, _bridge = store_window(30)
    created = {}

    def fake_exec(self):
        created["dialog"] = self
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PreferencesDialog, "exec", fake_exec)
        window.show_preferences()
    dialog = created["dialog"]
    try:
        assert dialog.license_box is not None and "Essai terminé" in dialog.license_box.state_label.text()
        dialog.license_box.offers_button.click()                                         # ferme les préférences (modales), puis ouvre les offres
        qtbot.waitUntil(lambda: _wall(window) is not None, timeout=3000)
    finally:
        if shiboken6.isValid(dialog):
            dialog.close()


def test_the_trigger_values_match_the_pure_module():
    from ui.main_window_mixins import entitlements as mixin

    assert (mixin.TRIGGER_EXPORT, mixin.TRIGGER_REFUSED) == (paywall.TRIGGER_EXPORT, paywall.TRIGGER_REFUSED)


def test_outside_the_store_no_store_screen_is_imported_or_built():
    """Processus neuf, build non Store : la fenêtre se construit sans importer un seul module d'achat ni de StoreKit."""
    script = (
        "import sys\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from ui.main_window import MainWindow\n"
        "w = MainWindow()\n"
        "w.refresh_entitlements(); w._show_refusal_message('x', 'refused'); w._export_refused_ui(); w.show_preferences.__func__\n"
        "bad = [m for m in sys.modules if m.startswith(('ui.store_', 'core.entitlements.storekit', 'core.entitlements.paywall'))]\n"
        "assert not bad, bad\n"
        "assert not hasattr(w, '_store_screens') and not hasattr(w, '_store_entitlements')\n"
        "print('OK')\n"
    )
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as config:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "KUT_STUDIO_CONFIG_DIR": config, "KUT_STUDIO_CACHE_DIR": config + "/c",
               "KUT_STUDIO_PROXY_DIR": config + "/p", "KUT_STUDIO_HARDWARE_ENCODING": "off"}
        done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0 and "OK" in done.stdout, done.stdout[-800:] + done.stderr[-1500:]
