"""Parcours utilisateur des mises à jour dans la vraie fenêtre (PySide6), contre le faux GitHub local.

Recherche automatique silencieuse ou bouton discret, recherche manuelle toujours explicite, version ignorée,
téléchargement vérifié, annulation, empreinte fausse, exécution depuis les sources, « Quitter et installer » qui
respecte un projet non enregistré, préférences, textes stricts dans les trois langues.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import PurePosixPath

import pytest
from test_scopes import _window
from update_server import UpdateServer, publish_release, serve_releases

from core.app_version import APP_VERSION
from core.release_assets import CHECKSUMS_FILE, Architecture, OperatingSystem, Target
from core.update_service import DownloadResult, UpdateChecker, UpdateDownloader
from core.updates import (
    CheckMode,
    CheckResult,
    InstallContext,
    InstallKind,
    UpdateError,
    UpdateErrorKind,
    parse_releases,
    UpdateOffer,
)
from core.user_settings import UserSettings, load_user_settings, save_user_settings
from core.versioning import Version
from ui import i18n
from ui.main_window_mixins import updates as updates_mixin
from ui.preferences_dialog import PreferencesDialog
from ui.update_dialog import UpdateDialog, UpdateDialogState, format_clock

REPOSITORY = "owner/kut"
MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
MAC_X64 = Target(OperatingSystem.MACOS, Architecture.X64)
NEW = "99.0.0"
PACKAGE = f"Kut-Studio-{NEW}-macos-arm64.zip"
PAYLOAD = b"Kut-Studio.app" * 4000
FROZEN_MAC = InstallContext(InstallKind.MACOS_APP, PurePosixPath("/Applications/Kut-Studio.app"))


@pytest.fixture
def server():
    instance = UpdateServer()
    yield instance
    instance.close()


@pytest.fixture
def opened(monkeypatch):
    """Ce que l'application ouvrirait : dossiers (gestionnaire de fichiers) et pages (navigateur)."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(updates_mixin, "open_path", lambda path: calls.append(("folder", path)) or True)
    monkeypatch.setattr(updates_mixin, "_open_url", lambda url: calls.append(("url", url)) or True)
    return calls


def _wire(monkeypatch, server, directory, target=MAC_ARM):
    """Les services de la fenêtre parlent au faux GitHub et téléchargent dans ``directory``."""
    def create(owner):
        from core.update_service import HttpClient

        client = HttpClient(owner)
        checker = UpdateChecker(client, current=Version.parse(APP_VERSION), repository=REPOSITORY,
                                api_base=server.base, asset_prefix=server.url("/download/"),
                                target_detector=lambda: target, parent=owner)
        return checker, UpdateDownloader(client, directory=directory, parent=owner)

    monkeypatch.setattr(updates_mixin, "_create_update_services", create)


def _window_for(qtbot, monkeypatch, server, tmp_path, *, context=FROZEN_MAC, target=MAC_ARM):
    _wire(monkeypatch, server, tmp_path / "updates", target)
    window = _window(qtbot, monkeypatch)
    window._update_context = context
    window.show()                       # visible : « la fenêtre reste ouverte / s'est fermée » a un sens
    return window


def _publish(server, *, version=NEW, checksums=None, packages=None, **options):
    data = packages if packages is not None else {f"Kut-Studio-{version}-macos-arm64.zip": PAYLOAD}
    return publish_release(server, f"v{version}", repository=REPOSITORY, packages=data, checksums=checksums,
                           **options)


def _finish_check(qtbot, window, mode):
    checker, _downloader = window._update_services()
    with qtbot.waitSignal(checker.finished, timeout=8000):
        if mode is CheckMode.MANUAL:
            window.check_for_updates()
        else:
            window._start_update_check(mode)


def _finish_download(qtbot, window, timeout=10000):
    _checker, downloader = window._update_services()
    with qtbot.waitSignal(downloader.finished, timeout=timeout) as blocker:
        window._update_dialog.download_button.click()
    return blocker.args[0]


def _visible_buttons(dialog: UpdateDialog) -> set[str]:
    names = ("download", "later", "skip", "release_page", "cancel", "retry", "close", "open_folder", "install")
    return {name for name in names if getattr(dialog, f"{name}_button").isVisibleTo(dialog)}


# ---------------------------------------------------------------------------
# Menus et démarrage
# ---------------------------------------------------------------------------


def test_the_help_menu_offers_the_check_and_the_version(qtbot, monkeypatch, server, tmp_path):
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    (help_menu,) = [a.menu() for a in window.menuBar().actions() if a.menu() and a.menu().objectName() == "help_menu"]
    texts = [action.text() for action in help_menu.actions() if action.text()]
    assert texts == [i18n.translate("update.menu.check"), i18n.translate("update.menu.about")]
    shown: list[str] = []
    monkeypatch.setattr("ui.main_window.QMessageBox.about", lambda _parent, title, text: shown.append(text))
    window.about_action.trigger()
    assert shown and f"Kut-Studio {APP_VERSION}" in shown[0] and "/Applications/Kut-Studio.app" in shown[0]


def test_no_window_of_the_test_suite_schedules_an_automatic_check(qtbot, monkeypatch, server, tmp_path):
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    assert not window._update_timer.isActive()
    assert window._update_checker is None, "aucun client réseau n'est créé tant que rien n'est demandé"


@pytest.mark.parametrize("settings,scheduled", [
    (UserSettings(), True),
    (UserSettings(check_updates=False), False),
    (UserSettings(last_update_check=time.time() - 3600), False),            # déjà vérifié il y a une heure
    (UserSettings(last_update_check=time.time() - 25 * 3600), True),
])
def test_the_startup_check_follows_the_preference_and_the_daily_rhythm(qtbot, monkeypatch, tmp_path, settings,
                                                                       scheduled):
    monkeypatch.setenv("KUT_STUDIO_UPDATE_CHECK", "on")
    config = tmp_path / "config"
    save_user_settings(settings, config)
    monkeypatch.setattr(updates_mixin, "_create_update_services", lambda owner: pytest.fail("aucun réseau en test"))
    window = _window(qtbot, monkeypatch, config_dir=config)
    try:
        assert window._update_timer.isActive() is scheduled
        assert window._update_timer.interval() == updates_mixin.AUTO_CHECK_DELAY_MS
    finally:
        window._update_timer.stop()


# ---------------------------------------------------------------------------
# Recherche automatique : silencieuse, ou un bouton discret
# ---------------------------------------------------------------------------


def test_an_automatic_check_without_update_is_silent_and_remembered(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    before = time.time()
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    assert window._update_dialog is None
    assert not window.update_notice_button.isVisibleTo(window)
    assert load_user_settings().last_update_check >= before


@pytest.mark.parametrize("route", [{"status": 500}, {"status": 403, "headers": {"X-RateLimit-Remaining": "0"}},
                                   {"body": b"{oops"}])
def test_an_automatic_check_that_fails_stays_silent(qtbot, monkeypatch, server, tmp_path, route):
    from update_server import Route

    server.routes[f"/repos/{REPOSITORY}/releases"] = Route(**route)
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    assert window._update_dialog is None
    assert not window.update_notice_button.isVisibleTo(window)
    assert load_user_settings().last_update_check == 0.0, "une recherche ratée sera retentée au prochain démarrage"


def test_an_automatic_check_with_update_only_shows_the_top_bar_button(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    assert window._update_dialog is None, "aucune fenêtre ne surgit en plein montage"
    button = window.update_notice_button
    assert button.isVisibleTo(window)
    assert NEW in button.text() and NEW in button.toolTip()
    button.click()
    dialog = window._update_dialog
    assert dialog.isVisible() and dialog.state is UpdateDialogState.OFFER
    assert dialog.headline.text() == i18n.translate("update.available.title", version=NEW)
    assert "Recherche de mises à jour" in dialog.notes.toPlainText(), "notes de publication rendues"
    assert _visible_buttons(dialog) == {"download", "later", "skip", "release_page"}
    assert PACKAGE in dialog.details.text()


def test_later_hides_the_button_and_remembers_nothing(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    window.update_notice_button.click()
    window._update_dialog.later_button.click()
    assert not window._update_dialog.isVisible()
    assert not window.update_notice_button.isVisibleTo(window)
    assert load_user_settings().skipped_update_version == ""


def test_a_skipped_version_is_silent_automatically_and_found_manually(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    window.update_notice_button.click()
    window._update_dialog.skip_button.click()
    assert load_user_settings().skipped_update_version == NEW
    assert not window.update_notice_button.isVisibleTo(window)

    window._update_offer = None
    _finish_check(qtbot, window, CheckMode.AUTOMATIC)
    assert not window.update_notice_button.isVisibleTo(window), "version ignorée : rien en automatique"

    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.isVisible() and dialog.state is UpdateDialogState.OFFER
    assert i18n.translate("update.available.skipped") in dialog.message.text()
    assert dialog.skip_button.text() == i18n.translate("update.button.unskip")
    dialog.skip_button.click()
    assert load_user_settings().skipped_update_version == ""
    assert dialog.skip_button.text() == i18n.translate("update.button.skip")


def test_a_manual_request_during_an_automatic_check_still_shows_its_result(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)], delay=0.5)
    save_user_settings(UserSettings(skipped_update_version=NEW), tmp_path / "config")
    _wire(monkeypatch, server, tmp_path / "updates")
    window = _window(qtbot, monkeypatch, config_dir=tmp_path / "config")
    window._update_context = FROZEN_MAC
    window._start_update_check(CheckMode.AUTOMATIC)
    window.check_for_updates()
    qtbot.waitUntil(lambda: window._update_dialog.state is UpdateDialogState.OFFER, timeout=8000)
    assert window._update_dialog.offer.skipped


# ---------------------------------------------------------------------------
# Recherche manuelle : toujours un résultat clair
# ---------------------------------------------------------------------------


def test_a_manual_check_says_clearly_that_kut_studio_is_up_to_date(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server, version="99.0.0-beta.1", prerelease=True)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.isVisible() and dialog.state is UpdateDialogState.UP_TO_DATE
    assert dialog.headline.text() == i18n.translate("update.up_to_date.title")
    assert APP_VERSION in dialog.message.text()
    assert "99.0.0-beta.1" in dialog.message.text(), "la préversion non proposée est signalée"
    assert _visible_buttons(dialog) == {"close"}


def test_a_manual_check_reports_offline_and_offers_to_retry(qtbot, monkeypatch, tmp_path):
    from update_server import closed_port_url

    class _Offline:
        base = closed_port_url("").rstrip("/")

        @staticmethod
        def url(path):
            return _Offline.base + path

    window = _window_for(qtbot, monkeypatch, _Offline, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.state is UpdateDialogState.CHECK_ERROR
    assert dialog.headline.text() == i18n.translate("update.error.check_title")
    assert dialog.message.text() == i18n.translate("update.error.offline")
    assert _visible_buttons(dialog) == {"retry", "close"}


def test_a_rate_limited_manual_check_says_when_to_retry(qtbot, monkeypatch, server, tmp_path):
    from update_server import Route

    reset = int(time.time()) + 1200
    server.routes[f"/repos/{REPOSITORY}/releases"] = Route(
        status=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    assert window._update_dialog.message.text() == i18n.translate("update.error.rate_limited",
                                                                  time=format_clock(reset))


def test_closing_the_dialog_during_a_check_cancels_it_quietly(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)], delay=1.0)
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    checker, _downloader = window._update_services()
    with qtbot.waitSignal(checker.finished, timeout=8000) as blocker:
        window.check_for_updates()
        window._update_dialog.reject()
    assert blocker.args[0].error.kind is UpdateErrorKind.CANCELLED
    assert not window._update_dialog.isVisible()


def test_the_cancel_button_during_a_check_closes_the_dialog(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)], delay=1.0)
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    checker, _downloader = window._update_services()
    with qtbot.waitSignal(checker.finished, timeout=8000) as blocker:
        window.check_for_updates()
        assert window._update_dialog.state is UpdateDialogState.CHECKING
        window._update_dialog.cancel_button.click()
    assert blocker.args[0].error.kind is UpdateErrorKind.CANCELLED
    assert not window._update_dialog.isVisible(), "pas de fenêtre figée sur « Recherche… »"
    assert not checker.busy


# ---------------------------------------------------------------------------
# Paquets, sources et téléchargement
# ---------------------------------------------------------------------------


def test_no_compatible_package_is_explained_and_nothing_is_offered(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, target=MAC_X64)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.details.text() == i18n.translate("update.package.missing", platform="macOS x64")
    assert "download" not in _visible_buttons(dialog)
    assert "release_page" in _visible_buttons(dialog)


def test_a_release_without_checksums_cannot_be_downloaded_from_the_app(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server, checksums="")])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.details.text() == i18n.translate("update.package.no_checksums", file=CHECKSUMS_FILE)
    assert "download" not in _visible_buttons(dialog)


def test_running_from_source_shows_the_version_but_never_downloads(qtbot, monkeypatch, server, tmp_path, opened):
    serve_releases(server, REPOSITORY, [_publish(server)])
    source = InstallContext(InstallKind.SOURCE, PurePosixPath("/home/dev/Kut-Studio"), revision="abc123def456")
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=source)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    assert dialog.headline.text() == i18n.translate("update.available.title", version=NEW)
    assert "download" not in _visible_buttons(dialog)
    assert dialog.instructions.text() == i18n.translate("update.source.text")
    assert "abc123def456" in dialog.details.text()
    window._on_update_download_requested()                     # même forcé, rien ne part
    assert not server.requested(f"/download/v{NEW}/{PACKAGE}")
    dialog.release_page_button.click()
    assert opened == [("url", f"https://github.com/{REPOSITORY}/releases/tag/v{NEW}")]


def test_download_progress_verification_and_assisted_install(qtbot, monkeypatch, server, tmp_path, opened):
    serve_releases(server, REPOSITORY, [_publish(server, package_route={"chunks": 6})])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    progress: list[int] = []
    window._update_services()[1].progress.connect(lambda received, _total: progress.append(dialog.progress.value()))
    result = _finish_download(qtbot, window)
    assert result.error is None and result.path.read_bytes() == PAYLOAD
    assert progress and progress[-1] == 1000
    assert dialog.state is UpdateDialogState.READY
    assert dialog.message.text() == i18n.translate("update.ready.verified", version=NEW)
    details = dialog.details.text()
    assert i18n.translate("update.ready.integrity") in details, "ce que garantit l'empreinte, et ce qu'elle ne prouve pas"
    assert i18n.translate("update.authenticity.macos") in details
    assert str(result.path) in details
    assert dialog.instructions.text() == i18n.translate("update.steps.macos")
    assert _visible_buttons(dialog) == {"open_folder", "install", "close"}

    dialog.open_folder_button.click()
    assert opened == [("folder", str(result.path.parent))]

    asked: list[str] = []

    def answer(_parent, title, text, buttons, default):
        asked.append(text)
        assert default == window_module.QMessageBox.No, "par défaut, on ne quitte pas"
        return window_module.QMessageBox.Yes

    from ui import main_window as window_module

    monkeypatch.setattr("ui.main_window.QMessageBox.question", answer)
    dialog.install_button.click()
    assert asked == [i18n.translate("update.quit.text")]
    assert not window.isVisible(), "la fenêtre s'est fermée par le chemin normal"
    assert opened[-1] == ("folder", str(result.path.parent))
    assert result.path.exists(), "le paquet vérifié reste à sa place ; rien n'a été exécuté ni remplacé"


def test_quit_and_install_respects_a_refused_close(qtbot, monkeypatch, server, tmp_path, opened):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    _finish_download(qtbot, window)
    from ui import main_window as window_module
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *args: window_module.QMessageBox.Yes)
    # Projet non enregistré : l'utilisateur choisit « Annuler » dans la vraie demande d'enregistrement.
    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: False)
    window._update_dialog.install_button.click()
    assert window.isVisible(), "un refus garde le montage ouvert"
    assert opened == [], "et rien n'est ouvert"


def test_quit_and_install_needs_explicit_consent(qtbot, monkeypatch, server, tmp_path, opened):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    _finish_download(qtbot, window)
    from ui import main_window as window_module

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *args: window_module.QMessageBox.No)
    window._update_dialog.install_button.click()
    assert window.isVisible() and opened == []


def test_a_wrong_checksum_is_shown_and_nothing_is_kept(qtbot, monkeypatch, server, tmp_path):
    from core.release_assets import format_checksums

    serve_releases(server, REPOSITORY, [_publish(server, checksums=format_checksums({PACKAGE: "0" * 64}))])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    result = _finish_download(qtbot, window)
    dialog = window._update_dialog
    assert result.error.kind is UpdateErrorKind.CHECKSUM_MISMATCH
    assert dialog.state is UpdateDialogState.DOWNLOAD_ERROR
    assert dialog.message.text() == i18n.translate("update.error.checksum_mismatch")
    assert "open_folder" not in _visible_buttons(dialog) and "install" not in _visible_buttons(dialog)
    assert list((tmp_path / "updates").iterdir()) == []


def test_cancelling_the_download_from_the_dialog(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server, package_route={"chunks": 40, "pause": 0.05})])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    dialog = window._update_dialog
    _checker, downloader = window._update_services()
    downloader.progress.connect(lambda *_: dialog.cancel_button.click() if dialog.cancel_button.isVisible() else None)
    result = _finish_download(qtbot, window)
    assert result.error.kind is UpdateErrorKind.CANCELLED
    assert dialog.headline.text() == i18n.translate("update.cancelled.title")
    assert "download" in _visible_buttons(dialog), "on peut relancer"
    assert list((tmp_path / "updates").iterdir()) == []


def test_closing_the_window_during_a_download_leaves_no_partial_file(qtbot, monkeypatch, server, tmp_path):
    serve_releases(server, REPOSITORY, [_publish(server, package_route={"chunks": 40, "pause": 0.05})])
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    _checker, downloader = window._update_services()
    window._update_dialog.download_button.click()
    qtbot.waitUntil(lambda: (tmp_path / "updates" / f"{PACKAGE}.part").exists(), timeout=5000)
    window.close()
    assert not downloader.busy
    assert list((tmp_path / "updates").iterdir()) == []


# ---------------------------------------------------------------------------
# Préférences
# ---------------------------------------------------------------------------


def test_the_preferences_group_applies_immediately_and_restores_defaults(qtbot):
    dialog = PreferencesDialog(current_check_updates=False, current_include_prereleases=True)
    qtbot.addWidget(dialog)
    assert not dialog.check_updates_box.isChecked() and dialog.prereleases_box.isChecked()
    assert APP_VERSION in dialog.updates_note.text()
    seen: list[tuple[str, bool]] = []
    dialog.update_check_changed.connect(lambda value: seen.append(("check", value)))
    dialog.update_prereleases_changed.connect(lambda value: seen.append(("pre", value)))
    dialog.check_updates_box.click()
    dialog.prereleases_box.click()
    assert seen == [("check", True), ("pre", False)]
    dialog.check_updates_box.click()
    dialog._on_restore_defaults()
    assert dialog.check_updates_box.isChecked() and not dialog.prereleases_box.isChecked()


def test_the_window_persists_update_preferences_and_restore_keeps_the_skipped_version(qtbot, monkeypatch, server,
                                                                                    tmp_path):
    window = _window_for(qtbot, monkeypatch, server, tmp_path)
    window.set_update_check_enabled(False)
    window.set_update_include_prereleases(True)
    window._update_skipped_version = "2.0.0"
    window._save_update_state()
    stored = load_user_settings()
    assert (stored.check_updates, stored.include_prereleases, stored.skipped_update_version) == (False, True, "2.0.0")
    monkeypatch.setattr("ui.main_window.save_user_settings", lambda settings: None)
    window._restore_default_preferences()
    assert window._update_check_enabled is True and window._update_include_prereleases is False
    assert window._update_skipped_version == "2.0.0", "la mémoire des mises à jour n'est pas un réglage"


# ---------------------------------------------------------------------------
# Textes : trois langues, mode strict, retraduction à chaud
# ---------------------------------------------------------------------------


def _offer(target=MAC_ARM, *, package=True, checksums=True, prerelease=False, skipped=False) -> UpdateOffer:
    version = "1.0.0-beta.1" if prerelease else "1.0.0"
    assets = []
    prefix = "https://github.com/Kyyzen0/Kut-Studio/releases/download/v1.0.0/"
    if package:
        assets.append({"name": f"Kut-Studio-{version}-macos-arm64.zip", "size": 1000, "state": "uploaded",
                       "browser_download_url": f"{prefix}Kut-Studio-{version}-macos-arm64.zip"})
    if checksums:
        assets.append({"name": CHECKSUMS_FILE, "size": 100, "state": "uploaded",
                       "browser_download_url": f"{prefix}{CHECKSUMS_FILE}"})
    for item in assets:
        item["browser_download_url"] = item["browser_download_url"].replace("v1.0.0", f"v{version}")
    (release,) = parse_releases([{"tag_name": f"v{version}", "draft": False, "prerelease": prerelease,
                                  "published_at": "2026-10-05T00:00:00Z", "body": "", "assets": assets}])
    return UpdateOffer(release, target, release.package_for(target), release.checksums, skipped)


@pytest.mark.parametrize("language", ["fr", "en", "es"])
@pytest.mark.parametrize("context", [FROZEN_MAC, InstallContext(InstallKind.SOURCE, PurePosixPath("/src"), revision="abc"),
                                     InstallContext(InstallKind.WINDOWS_FOLDER, PurePosixPath("C:/Kut-Studio")),
                                     InstallContext(InstallKind.LINUX_FOLDER, None, translocated=True)])
def test_every_state_is_fully_translated(qtbot, tmp_path, language, context):
    current = Version.parse("0.1.0")
    original = i18n.current_language()
    try:
        i18n.set_language(language)
        with i18n.strict_translations():
            dialog = UpdateDialog(context)
            qtbot.addWidget(dialog)
            dialog.show_checking()
            dialog.show_check_result(CheckResult(CheckMode.MANUAL, current, hidden_prerelease=Version.parse("1.0.0-rc.1")))
            for offer in (_offer(), _offer(package=False), _offer(checksums=False), _offer(target=None),
                          _offer(prerelease=True, skipped=True)):
                dialog.show_check_result(CheckResult(CheckMode.MANUAL, current, offer=offer))
            for kind in UpdateErrorKind:
                error = UpdateError(kind, "détail", status=500, retry_at=time.time() + 60)
                dialog.show_check_result(CheckResult(CheckMode.MANUAL, current, error=error))
                dialog.show_download_result(DownloadResult(_offer(), error=error))
            for system in OperatingSystem:
                offer = _offer(target=Target(system, Architecture.X64))
                dialog.show_downloading(offer)
                dialog.update_progress(500, 1000)
                dialog.update_progress(10, -1)
                dialog.show_download_result(DownloadResult(offer, path=tmp_path / "Kut-Studio.zip", sha256="0" * 64))
                assert dialog.instructions.text() and dialog.details.text()
            assert not any(text.startswith("[") for text in (dialog.headline.text(), dialog.message.text()))
    finally:
        i18n.set_language(original)


def test_the_open_dialog_follows_a_language_change(qtbot):
    original = i18n.current_language()
    try:
        i18n.set_language("fr")
        dialog = UpdateDialog(FROZEN_MAC)
        qtbot.addWidget(dialog)
        dialog.show_check_result(CheckResult(CheckMode.MANUAL, Version.parse("0.1.0"), offer=_offer()))
        assert dialog.download_button.text() == "Télécharger"
        i18n.set_language("es")
        assert dialog.download_button.text() == "Descargar"
        assert dialog.skip_button.text() == "Omitir esta versión"
        assert dialog.later_button.text() == "Más tarde"
        i18n.set_language("en")
        assert dialog.headline.text() == "Kut-Studio 1.0.0 is available"
    finally:
        i18n.set_language(original)


def test_release_notes_are_rendered_as_markdown_without_raw_html(qtbot):
    dialog = UpdateDialog(FROZEN_MAC)
    qtbot.addWidget(dialog)
    offer = _offer()
    release = replace(offer.release, notes="# Titre\n\n<script>alert(1)</script>\n\n- **gras**")
    dialog.show_offer(UpdateOffer(release, offer.target, offer.package, offer.checksums))
    text = dialog.notes.toPlainText()
    assert "Titre" in text and "gras" in text and "**" not in text
    assert "<script>" not in dialog.notes.toHtml()


def test_progress_is_shown_in_the_dialog(qtbot):
    dialog = UpdateDialog(FROZEN_MAC)
    qtbot.addWidget(dialog)
    dialog.show_downloading(_offer())
    dialog.update_progress(250, 1000)
    assert dialog.progress.value() == 250
    assert dialog.progress_label.text() == i18n.translate("update.progress", received="250 o", total="1000 o")
    assert _visible_buttons(dialog) == {"cancel"}
