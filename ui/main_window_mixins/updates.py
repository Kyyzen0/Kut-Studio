"""Méthodes de ``MainWindow`` regroupées : mises à jour.

Câblage seulement : les règles sont dans :mod:`core.updates`, le transport et la vérification dans
:mod:`core.update_service`, l'affichage dans :mod:`ui.update_dialog`.

* **Au démarrage**, si l'option est active et que la dernière recherche réussie date d'au moins 24 h, une recherche
  part en arrière-plan quelques secondes après l'ouverture. Sans nouvelle version, ou en cas d'erreur (hors ligne,
  limite GitHub…), elle reste **silencieuse** (le journal en garde la trace). Avec une nouvelle version, elle
  n'ouvre aucune fenêtre (un dialogue surgi en plein montage volerait le clavier) : un bouton « Mise à jour X »
  apparaît dans la barre supérieure.
* **Aide › Rechercher des mises à jour…** montre toujours un résultat, y compris pour une version ignorée.
* **Installation assistée** : Kut-Studio ne remplace jamais l'application en cours d'exécution. Après vérification,
  « Quitter et installer… » demande confirmation, ferme la fenêtre par le chemin normal (projets non enregistrés,
  rendu en cours : la fermeture peut être refusée, et alors rien d'autre ne se passe), puis ouvre le dossier du paquet
  vérifié. Depuis les sources, rien n'est téléchargé : la version disponible est présentée, avec ``git pull``.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import replace

import PySide6
from PySide6.QtCore import QTimer, QUrl, qVersion
from PySide6.QtGui import QDesktopServices

from core.app_version import APP_VERSION, WEBSITE_URL
from core.release_assets import detect_target
from core.update_service import DownloadResult, HttpClient, UpdateChecker, UpdateDownloader
from core.updates import (
    CheckMode,
    CheckResult,
    UpdateErrorKind,
    UpdateOffer,
    auto_check_due,
    automatic_checks_allowed,
    install_context,
)
from core.user_settings import DEFAULT_CHECK_UPDATES, DEFAULT_INCLUDE_PRERELEASES
from core.versioning import Version
from ui import i18n
from ui.render_queue_panel import open_path
from ui.update_dialog import UpdateDialog, UpdateDialogState, install_description, platform_label

LOGGER = logging.getLogger(__name__)

AUTO_CHECK_DELAY_MS = 4000
"""Délai entre l'ouverture de la fenêtre et la recherche automatique : le démarrage passe d'abord."""


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


def _create_update_services(owner) -> tuple[UpdateChecker, UpdateDownloader]:
    """Recherche et téléchargement sur un même client HTTPS (remplacé par les tests : serveur local)."""
    client = HttpClient(owner)
    return UpdateChecker(client, parent=owner), UpdateDownloader(client, parent=owner)


def _open_url(url: str) -> bool:
    """Ouvre une page dans le navigateur de l'utilisateur (remplacé par les tests)."""
    return QDesktopServices.openUrl(QUrl(url))


class UpdatesMixin:
    """Mixin de ``MainWindow`` (recherche, téléchargement vérifié et installation assistée des mises à jour)."""

    # ------------------------------------------------------------------
    # Initialisation et préférences
    # ------------------------------------------------------------------

    def _init_updates(self, settings) -> None:
        """Lit les préférences et programme la recherche automatique ; aucun accès réseau ici."""
        self._update_check_enabled = bool(settings.check_updates)
        self._update_include_prereleases = bool(settings.include_prereleases)
        self._update_skipped_version = settings.skipped_update_version
        self._update_last_check = float(settings.last_update_check)
        self._update_context = install_context()
        self._update_checker: UpdateChecker | None = None
        self._update_downloader: UpdateDownloader | None = None
        self._update_dialog: UpdateDialog | None = None
        self._update_offer: UpdateOffer | None = None
        self._update_manual_pending = False
        self._updates_closed = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(AUTO_CHECK_DELAY_MS)
        self._update_timer.timeout.connect(self._run_automatic_update_check)
        if automatic_checks_allowed() and auto_check_due(
            enabled=self._update_check_enabled, last_check=self._update_last_check
        ):
            self._update_timer.start()

    def _update_settings_fields(self) -> dict:
        """Champs « mises à jour » de l'instantané des préférences (défauts avant :meth:`_init_updates`)."""
        return {
            "check_updates": getattr(self, "_update_check_enabled", DEFAULT_CHECK_UPDATES),
            "include_prereleases": getattr(self, "_update_include_prereleases", DEFAULT_INCLUDE_PRERELEASES),
            "skipped_update_version": getattr(self, "_update_skipped_version", ""),
            "last_update_check": getattr(self, "_update_last_check", 0.0),
        }

    def _apply_update_settings(self, settings) -> None:
        """Préférences appliquées par ``_apply_settings`` (dont « Restaurer les réglages par défaut »)."""
        self._update_check_enabled = bool(settings.check_updates)
        self._update_include_prereleases = bool(settings.include_prereleases)
        self._update_skipped_version = settings.skipped_update_version
        self._update_last_check = float(settings.last_update_check)

    def set_update_check_enabled(self, enabled: bool) -> None:
        """Préférence « Rechercher les mises à jour au démarrage »."""
        self._update_check_enabled = bool(enabled)
        self._save_update_state()

    def set_update_include_prereleases(self, enabled: bool) -> None:
        """Préférence « Proposer aussi les préversions »."""
        self._update_include_prereleases = bool(enabled)
        self._save_update_state()

    def _save_update_state(self) -> None:
        try:
            _main_window().save_user_settings(self._settings_snapshot())
        except OSError:
            LOGGER.warning("Mises à jour : préférences non enregistrées", exc_info=True)

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------

    def _update_services(self) -> tuple[UpdateChecker, UpdateDownloader]:
        if self._update_checker is None or self._update_downloader is None:
            checker, downloader = _create_update_services(self)
            checker.finished.connect(self._on_update_check_finished)
            downloader.progress.connect(self._on_update_download_progress)
            downloader.finished.connect(self._on_update_download_finished)
            self._update_checker, self._update_downloader = checker, downloader
        return self._update_checker, self._update_downloader

    def _run_automatic_update_check(self) -> None:
        if self._updates_closed or not self._update_check_enabled:
            return
        self._start_update_check(CheckMode.AUTOMATIC)

    def check_for_updates(self) -> None:
        """Aide › Rechercher des mises à jour… : le résultat est toujours affiché."""
        dialog = self._ensure_update_dialog()
        _checker, downloader = self._update_services()
        if not downloader.busy:          # un téléchargement en cours garde son affichage
            dialog.show_checking()
            self._start_update_check(CheckMode.MANUAL)
        self._present_update_dialog()

    def _start_update_check(self, mode: CheckMode) -> None:
        checker, _downloader = self._update_services()
        if checker.busy:
            # Une recherche automatique est en vol : son résultat sera remplacé par une recherche manuelle, qui
            # montre aussi une version ignorée.
            self._update_manual_pending = self._update_manual_pending or mode is CheckMode.MANUAL
            return
        skipped = Version.try_parse(self._update_skipped_version)
        checker.check(mode=mode, include_prereleases=self._update_include_prereleases, skipped_version=skipped)

    def _on_update_check_finished(self, result: CheckResult) -> None:
        if self._updates_closed:
            return
        if self._update_manual_pending and result.mode is CheckMode.AUTOMATIC:
            self._update_manual_pending = False
            self._start_update_check(CheckMode.MANUAL)
            return
        if result.error is None:
            self._update_last_check = result.checked_at
            self._save_update_state()
        if result.mode is CheckMode.AUTOMATIC:
            if result.offer is not None:
                self._update_offer = result.offer
                self._show_update_notice(result.offer)
            return
        if result.error is not None and result.error.kind is UpdateErrorKind.CANCELLED:
            return                                       # fenêtre fermée pendant la recherche
        dialog = self._ensure_update_dialog()
        dialog.show_check_result(result)
        if result.offer is not None:
            self._update_offer = result.offer
        self._present_update_dialog()

    # ------------------------------------------------------------------
    # Fenêtre des mises à jour
    # ------------------------------------------------------------------

    def _ensure_update_dialog(self) -> UpdateDialog:
        if self._update_dialog is None:
            dialog = UpdateDialog(self._update_context, parent=self)
            dialog.download_requested.connect(self._on_update_download_requested)
            dialog.later_requested.connect(self._on_update_later)
            dialog.skip_requested.connect(self._on_update_skip)
            dialog.unskip_requested.connect(self._on_update_unskip)
            dialog.release_page_requested.connect(self._on_update_release_page)
            dialog.cancel_requested.connect(self._on_update_cancel)
            dialog.retry_requested.connect(self.check_for_updates)
            dialog.open_folder_requested.connect(self._on_update_open_folder)
            dialog.install_requested.connect(self._on_update_install)
            self._update_dialog = dialog
        return self._update_dialog

    def _present_update_dialog(self) -> None:
        dialog = self._ensure_update_dialog()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_update_offer(self) -> None:
        """Bouton « Mise à jour X » de la barre supérieure : présente la version trouvée en arrière-plan."""
        offer = self._update_offer
        if offer is None:
            self.check_for_updates()
            return
        dialog = self._ensure_update_dialog()
        _checker, downloader = self._update_services()
        if not downloader.busy and dialog.state is not UpdateDialogState.READY:
            dialog.show_offer(offer, CheckResult(CheckMode.MANUAL, Version.parse(APP_VERSION), offer=offer))
        self._present_update_dialog()

    def _on_update_later(self) -> None:
        self._hide_update_notice()

    def _on_update_skip(self) -> None:
        dialog = self._ensure_update_dialog()
        offer = dialog.offer
        if offer is None:
            return
        self._update_skipped_version = str(offer.version)
        self._save_update_state()
        self._hide_update_notice()
        dialog.hide()

    def _on_update_unskip(self) -> None:
        dialog = self._ensure_update_dialog()
        offer = dialog.offer
        if offer is None:
            return
        self._update_skipped_version = ""
        self._save_update_state()
        unskipped = replace(offer, skipped=False)
        self._update_offer = unskipped
        dialog.show_offer(unskipped, CheckResult(CheckMode.MANUAL, Version.parse(APP_VERSION), offer=unskipped))

    def _on_update_release_page(self) -> None:
        offer = self._ensure_update_dialog().offer
        if offer is not None:
            _open_url(offer.release.page_url)

    def _on_update_cancel(self) -> None:
        checker, downloader = self._update_checker, self._update_downloader
        if checker is not None:
            checker.cancel()
        if downloader is not None:
            downloader.cancel()

    # ------------------------------------------------------------------
    # Téléchargement vérifié et installation assistée
    # ------------------------------------------------------------------

    def _on_update_download_requested(self) -> None:
        dialog = self._ensure_update_dialog()
        offer = dialog.offer
        if offer is None or not offer.can_download or self._update_context.is_source:
            return
        _checker, downloader = self._update_services()
        if downloader.busy:
            return
        dialog.show_downloading(offer)
        downloader.start(offer)

    def _on_update_download_progress(self, received: int, total: int) -> None:
        if self._update_dialog is not None and not self._updates_closed:
            self._update_dialog.update_progress(received, total)

    def _on_update_download_finished(self, result: DownloadResult) -> None:
        if self._updates_closed:
            return
        dialog = self._ensure_update_dialog()
        cancelled = result.error is not None and result.error.kind is UpdateErrorKind.CANCELLED
        if cancelled and not dialog.isVisible():
            return                                       # fermé pendant le téléchargement : rien à montrer
        dialog.show_download_result(result)
        self._present_update_dialog()

    def _verified_package(self):
        dialog = self._update_dialog
        if dialog is None or dialog.state is not UpdateDialogState.READY:
            return None
        download = dialog.download
        return download.path if download is not None else None

    def _on_update_open_folder(self) -> None:
        path = self._verified_package()
        if path is not None:
            open_path(str(path.parent))

    def _on_update_install(self) -> None:
        """« Quitter et installer… » : accord explicite, fermeture normale (refusable), puis dossier du paquet."""
        path = self._verified_package()
        if path is None:
            return
        box = _main_window().QMessageBox
        answer = box.question(
            self,
            i18n.translate("update.quit.title"),
            i18n.translate("update.quit.text"),
            box.Yes | box.No,
            box.No,
        )
        if answer != box.Yes:
            return
        # ``close`` passe par ``closeEvent`` : projet non enregistré et rendu en cours y demandent leur accord.
        # Un refus laisse tout ouvert, et le dossier n'est pas ouvert.
        if not self.close():
            return
        open_path(str(path.parent))

    # ------------------------------------------------------------------
    # Bouton de la barre supérieure
    # ------------------------------------------------------------------

    def _build_update_notice(self):
        """Bouton discret « Mise à jour X », caché tant qu'aucune version n'a été trouvée en arrière-plan."""
        from PySide6.QtCore import Qt

        from ui.design_system import Sizes
        from ui.icons import IconButton, IconName

        button = IconButton(icon=IconName.ARROW_DOWN, size=Sizes.icon_button, square=False, accent=False)
        button.setObjectName("updateNoticeButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.clicked.connect(self.open_update_offer)
        button.hide()
        self.update_notice_button = button
        self._retranslate_update_notice()      # nommé dès sa création (accessibilité), même caché
        return button

    def _show_update_notice(self, offer: UpdateOffer) -> None:
        button = getattr(self, "update_notice_button", None)
        if button is None:
            return
        self._retranslate_update_notice()
        button.show()

    def _hide_update_notice(self) -> None:
        button = getattr(self, "update_notice_button", None)
        if button is not None:
            button.hide()

    def _retranslate_update_notice(self) -> None:
        button = getattr(self, "update_notice_button", None)
        if button is None:
            return
        offer = getattr(self, "_update_offer", None)
        if offer is None:
            button.setText("")
            button.setToolTip(i18n.translate("update.menu.check"))
            return
        version = str(offer.version)
        button.setText(" " + i18n.translate("update.notice", version=version))
        button.setToolTip(i18n.translate("update.notice.tooltip", version=version))

    # ------------------------------------------------------------------
    # À propos
    # ------------------------------------------------------------------

    def about_text(self) -> str:
        """Texte de la boîte « À propos » : version, installation, système, bibliothèques."""
        target = detect_target()
        system = platform_label(target) if target is not None else f"{platform.system()} {platform.machine()}"
        return i18n.translate(
            "update.about.text",
            version=APP_VERSION,
            install=install_description(self._update_context),
            system=system,
            qt=qVersion(),
            pyside=PySide6.__version__,
            python=platform.python_version(),
            website=WEBSITE_URL,
        )

    def show_about(self) -> None:
        _main_window().QMessageBox.about(self, i18n.translate("update.about.title"), self.about_text())

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------

    def _shutdown_updates(self) -> None:
        """Aucun transfert ne survit à la fenêtre ; un fichier partiel est supprimé par l'annulation."""
        self._updates_closed = True
        timer = getattr(self, "_update_timer", None)
        if timer is not None:
            timer.stop()
        self._on_update_cancel()
        dialog = getattr(self, "_update_dialog", None)
        if dialog is not None:
            dialog.hide()
