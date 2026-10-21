"""Installation automatique macOS : le parcours côté fenêtre (préparation, fermeture normale, assistant, suivi).

Le dialogue affiche, :mod:`core.update_install` décide et prépare, :mod:`core.update_helper` remplace l'application.
Ce module les relie à ``MainWindow`` sans rien ajouter à ses mixins en dehors de quelques délégations
(:class:`ui.main_window_mixins.updates.UpdatesMixin`).

Parcours de « Installer et redémarrer » (rien n'est modifié avant la dernière étape) :

1. accord explicite (bouton par défaut : non) ;
2. préparation dans un fil : extraction et validation de la nouvelle application dans un staging frère de
   l'application (annulable) ; un échec supprime le staging et retombe sur l'installation assistée ;
3. fermeture par le chemin normal (``close``) : projet non enregistré proposé à l'enregistrement, rendu en cours
   confirmé ; si l'utilisateur annule, le staging est supprimé et tout reste ouvert ;
4. seulement alors, l'assistant est lancé détaché et l'application se ferme.

Au démarrage suivant (:meth:`AutoInstaller.startup`) : reprise d'une installation interrompue, écriture du fichier
« démarré » que l'assistant attend, lecture du statut (message une seule fois).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from core import update_helper
from core.app_version import APP_VERSION
from core.update_install import (
    InstallDecision,
    InstallOutcome,
    InstallPolicy,
    SignaturePolicy,
    StageError,
    StageProblem,
    consume_outcome,
    evaluate_install,
    helper_running,
    read_outcome,
    recover_leftovers,
    required_architecture,
    stage_update,
    write_started_marker,
)
from core.update_service import DownloadResult
from core.updates import InstallKind, release_page_url, updates_directory
from core.versioning import Version
from ui import i18n
from ui.update_dialog import UpdateDialogState

LOGGER = logging.getLogger(__name__)

STARTUP_DELAY_MS = 1500
OUTCOME_POLL_MS = 1000
OUTCOME_POLL_LIMIT = 40
"""La nouvelle application attend au plus ce nombre de secondes le statut que l'assistant écrit après elle."""


def _main_window():
    from ui import main_window

    return main_window


def _stage(*args, **kwargs):
    """Préparation du staging (remplacée par les tests)."""
    return stage_update(*args, **kwargs)


def _launch(plan):
    """Lancement détaché de l'assistant (remplacé par les tests : jamais d'installation réelle)."""
    return update_helper.launch_helper(plan)


def show_message(parent: QWidget, title: str, text: str, actions: list[tuple[str, object]], *, warning: bool) -> None:
    """Message non bloquant avec des boutons d'action (remplacé par les tests)."""
    box = QMessageBox(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    for label, callback in actions:
        button = QPushButton(label, box)
        box.addButton(button, QMessageBox.ButtonRole.ActionRole)
        button.clicked.connect(lambda _checked=False, cb=callback: cb())  # type: ignore[operator]
    box.open()


@dataclass(frozen=True)
class StageOutcome:
    staging: Path | None = None
    error: StageError | None = None
    worker: object | None = None
    """Le fil qui répond : une réponse d'un fil abandonné (annulation) est ignorée."""


class _StageWorker(QObject):
    """Extraction et validation dans un fil ; aucun QObject n'est créé ni détruit hors du fil principal."""

    finished = Signal(object)

    def __init__(self, job, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._job = job
        self.abandoned = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="update-staging", daemon=True).start()

    def _run(self) -> None:
        try:
            outcome = StageOutcome(staging=self._job(), worker=self)
        except StageError as exc:
            outcome = StageOutcome(error=exc, worker=self)
        except Exception as exc:  # noqa: BLE001 - un défaut imprévu ne doit jamais bloquer le dialogue
            LOGGER.warning("Mise à jour : préparation interrompue", exc_info=True)
            outcome = StageOutcome(error=StageError(StageProblem.BUNDLE, type(exc).__name__), worker=self)
        self.finished.emit(outcome)


class AutoInstaller(QObject):
    """Installation automatique d'un paquet vérifié, pour une fenêtre principale (QObject : reçoit le fil en file)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.policy: InstallPolicy | None = None
        self._worker: _StageWorker | None = None
        self._active: _StageWorker | None = None
        """Fil de préparation encore en cours, même annulé : un second ne peut pas écrire dans le même staging."""
        self._polls = 0
        self.shown: list[InstallOutcome] = []

    # ------------------------------------------------------------------
    # Décision
    # ------------------------------------------------------------------

    def decision(self, download: DownloadResult | None) -> InstallDecision | None:
        if download is None or download.path is None:
            return None
        return evaluate_install(self.window._update_context, download,
                                policy=self.policy if self.policy is not None else SignaturePolicy())

    def refresh_dialog(self) -> None:
        dialog = self.window._update_dialog
        if dialog is not None and dialog.state is UpdateDialogState.READY:
            dialog.set_install_decision(self.decision(dialog.download))

    # ------------------------------------------------------------------
    # Parcours
    # ------------------------------------------------------------------

    def request_install(self) -> None:
        window = self.window
        dialog = window._update_dialog
        if dialog is None or dialog.state is not UpdateDialogState.READY or self._worker is not None:
            return
        download, offer = dialog.download, dialog.offer
        decision = self.decision(download)
        if download is None or download.path is None or offer is None or decision is None or not decision.available \
                or decision.app is None or offer.target is None:
            return
        box = _main_window().QMessageBox
        answer = box.question(window, i18n.translate("update.auto.confirm.title"),
                              i18n.translate("update.auto.confirm.text"), box.Yes | box.No, box.No)
        if answer != box.Yes:
            return
        archive, app = download.path, decision.app
        version = Version.parse(str(offer.version))
        installed = Version.parse(APP_VERSION)
        architecture = required_architecture(offer.target.architecture)
        pid = os.getpid()

        def job() -> Path:
            return _stage(archive, app, expected_version=version, installed_version=installed,
                          required_arch=architecture, pid=pid, signature_verified=True)

        if self._active is not None:                 # annulée puis redemandée avant la fin : on la reprend
            self._active.abandoned = False
            self._worker = self._active
            dialog.show_installing()
            return
        worker = _StageWorker(job, parent=window)
        worker.finished.connect(self._on_staged)
        self._worker = self._active = worker
        dialog.show_installing()
        worker.start()

    def cancel(self) -> None:
        """« Annuler » pendant la préparation : le staging sera supprimé dès qu'il existe, rien n'est installé."""
        if self._worker is not None:
            self._worker.abandoned = True
        self._worker = None
        dialog = self.window._update_dialog
        if dialog is not None and dialog.state is UpdateDialogState.INSTALLING:
            dialog.show_ready_again()

    def abandon(self) -> None:
        """Fermeture de la fenêtre pendant la préparation (hors de ce parcours) : même effet qu'une annulation."""
        if self._worker is not None:
            self._worker.abandoned = True
            self._worker = None

    @Slot(object)
    def _on_staged(self, outcome: StageOutcome) -> None:
        worker = outcome.worker
        if worker is self._active:
            self._active = None
        abandoned = not isinstance(worker, _StageWorker) or worker.abandoned or self._worker is not worker
        if not abandoned:
            self._worker = None
        if abandoned or getattr(self.window, "_updates_closed", False):
            if outcome.staging is not None:
                shutil.rmtree(outcome.staging, ignore_errors=True)
            return
        dialog = self.window._update_dialog
        if outcome.error is not None or outcome.staging is None:
            error = outcome.error
            LOGGER.warning("Mise à jour : préparation refusée (%s)", error)
            problem = i18n.translate(f"update.auto.problem.{error.problem.value}") if error is not None else ""
            if dialog is not None:
                dialog.show_auto_failure(problem)
            return
        self._finish_install(outcome.staging)

    def _finish_install(self, staging: Path) -> None:
        window = self.window
        dialog = window._update_dialog
        offer = dialog.offer if dialog is not None else None
        decision = self.decision(dialog.download if dialog is not None else None)
        if offer is None or decision is None or decision.app is None:
            shutil.rmtree(staging, ignore_errors=True)
            return
        # Fermeture par le chemin normal : projet non enregistré et rendu en cours demandent leur accord. Un refus laisse
        # tout ouvert, supprime le staging et ne lance rien.
        if not window.close():
            shutil.rmtree(staging, ignore_errors=True)
            if dialog is not None:
                dialog.show_ready_again()
            return
        plan = update_helper.make_plan(decision.app, pid=os.getpid(), version=str(offer.version),
                                       from_version=APP_VERSION)
        try:
            _launch(plan)
        except (OSError, ValueError):
            LOGGER.warning("Mise à jour : assistant non lancé", exc_info=True)
            shutil.rmtree(staging, ignore_errors=True)
            download = dialog.download if dialog is not None else None
            if download is not None and download.path is not None:
                _open_folder(str(download.path.parent))

    # ------------------------------------------------------------------
    # Démarrage de l'application
    # ------------------------------------------------------------------

    def startup_enabled(self) -> bool:
        context = self.window._update_context
        return context.kind is InstallKind.MACOS_APP and context.location is not None

    def startup(self) -> None:
        """Fichier « démarré », reprise d'une installation interrompue, statut de la précédente mise à jour."""
        if not self.startup_enabled():
            return
        folder = updates_directory()
        try:
            write_started_marker(folder / update_helper.STARTED_NAME, APP_VERSION, os.getpid())
        except OSError:
            LOGGER.warning("Mise à jour : fichier « démarré » non écrit", exc_info=True)
        app = Path(str(self.window._update_context.location))
        try:
            report = recover_leftovers(app, updates_dir=folder, lock=folder / update_helper.LOCK_NAME,
                                       status=folder / update_helper.STATUS_NAME, running_version=APP_VERSION)
        except OSError:
            LOGGER.warning("Mise à jour : reprise impossible", exc_info=True)
        else:
            if report.restored is not None:
                LOGGER.warning("Mise à jour : version précédente restaurée depuis %s", report.restored)
                self._show_recovered()
        self.check_outcome()

    def check_outcome(self) -> None:
        folder = updates_directory()
        status = folder / update_helper.STATUS_NAME
        outcome = read_outcome(status)
        if outcome is None:
            if helper_running(folder / update_helper.LOCK_NAME) and self._polls < OUTCOME_POLL_LIMIT:
                self._polls += 1
                QTimer.singleShot(OUTCOME_POLL_MS, self.check_outcome)       # l'assistant écrit le statut après nous
            return
        consume_outcome(status)
        self.show_outcome(outcome)

    def _show_recovered(self) -> None:
        show_message(self.window, i18n.translate("update.outcome.failed.title"),
                     i18n.translate("update.outcome.recovered.text"), [], warning=True)

    def show_outcome(self, outcome: InstallOutcome) -> None:
        """Dit une fois ce que la dernière mise à jour est devenue (réussie, restaurée, ou restée sans effet)."""
        self.shown.append(outcome)
        window = self.window
        if outcome.result == "ok":
            if outcome.version != APP_VERSION:
                return
            actions = [(i18n.translate("update.outcome.notes"),
                        lambda: _open_url(release_page_url(f"v{outcome.version}")))]
            show_message(window, i18n.translate("update.outcome.ok.title"),
                         i18n.translate("update.outcome.ok.text", version=outcome.version), actions, warning=False)
            return
        reason = _reason_text(outcome.reason)
        if outcome.reason == "rollback_failed":
            text = i18n.translate("update.outcome.rollback_failed.text", version=outcome.version)
        elif outcome.result == "rolled_back":
            text = i18n.translate("update.outcome.restored.text", version=outcome.version, reason=reason)
        else:
            text = i18n.translate("update.outcome.kept.text", version=outcome.version, reason=reason)
        log = updates_directory() / update_helper.LOG_NAME
        actions = [(i18n.translate("update.outcome.log"), lambda: _open_folder(str(log)))]
        show_message(window, i18n.translate("update.outcome.failed.title"), text, actions, warning=True)


def _reason_text(reason: str) -> str:
    key = f"update.outcome.reason.{reason}"
    return i18n.translate(key) if key in _known_reasons() else reason[:60]


def _known_reasons() -> frozenset[str]:
    from ui.i18n_update_install import UPDATE_INSTALL_TRANSLATIONS

    return frozenset(key for key in UPDATE_INSTALL_TRANSLATIONS if key.startswith("update.outcome.reason."))


def _open_url(url: str) -> bool:
    from ui.main_window_mixins import updates

    return updates._open_url(url)


def _open_folder(path: str) -> bool:
    """Dossier ou fichier dans le gestionnaire du système (même point d'entrée que « Ouvrir le dossier »)."""
    from ui.main_window_mixins import updates

    return updates.open_path(path)


def new_installer(window) -> AutoInstaller:
    return AutoInstaller(window)


__all__ = ["AutoInstaller", "STARTUP_DELAY_MS", "new_installer", "show_message"]

