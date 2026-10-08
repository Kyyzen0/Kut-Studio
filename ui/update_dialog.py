"""Fenêtre des mises à jour : un seul dialogue pour tout le parcours, non modal.

États : recherche → à jour / version disponible / erreur ; version disponible → téléchargement → paquet vérifié
(ou erreur). Le dialogue **affiche et émet** : la recherche, le téléchargement, la persistance de la version ignorée
et la fermeture de l'application appartiennent à :class:`ui.main_window_mixins.updates.UpdatesMixin`.

Il est non modal : on continue à monter pendant une recherche ou un téléchargement. Le fermer pendant un
téléchargement l'annule (le fichier partiel est supprimé) ; le fermer sur une version disponible vaut « Plus tard ».
Tout texte est recalculé par :meth:`_render` à partir de l'état : un changement de langue le retraduit à chaud.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QDate, QDateTime, QLocale, Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.release_assets import CHECKSUMS_FILE, OperatingSystem, Target
from core.update_service import DownloadResult
from core.updates import CheckResult, InstallContext, InstallKind, UpdateError, UpdateErrorKind, UpdateOffer
from ui import i18n
from ui.design_system import DIALOG_MARGINS, ButtonVariant, Spacing, StatusKind, TextRoles
from ui.keyboard_navigation import set_single_default
from ui.render_queue_panel import format_size
from ui.theme import set_role, set_state, set_variant


class UpdateDialogState(str, Enum):
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    OFFER = "offer"
    DOWNLOADING = "downloading"
    READY = "ready"
    CHECK_ERROR = "check_error"
    DOWNLOAD_ERROR = "download_error"


@dataclass
class _View:
    """Ce que le dialogue montre ; :meth:`UpdateDialog._render` en déduit tous les textes."""

    state: UpdateDialogState = UpdateDialogState.CHECKING
    check: CheckResult | None = None
    offer: UpdateOffer | None = None
    download: DownloadResult | None = None
    error: UpdateError | None = None
    received: int = 0
    total: int = -1


def _locale() -> QLocale:
    return QLocale(i18n.current_language())


def format_release_date(published_at: str) -> str:
    """``2026-10-05T12:00:00Z`` → « 5 octobre 2026 » dans la langue de l'interface ; ``""`` si illisible."""
    date = QDate.fromString(published_at[:10], "yyyy-MM-dd")
    return _locale().toString(date, "d MMMM yyyy") if date.isValid() else ""


def format_clock(timestamp: float) -> str:
    """Heure locale (« 14:05 ») d'un horodatage."""
    moment = QDateTime.fromSecsSinceEpoch(int(timestamp))
    return _locale().toString(moment.time(), QLocale.FormatType.ShortFormat)


def platform_label(target: Target | None) -> str:
    return target.display_name if target is not None else i18n.translate("update.platform.unknown")


def install_description(context: InstallContext) -> str:
    """« application macOS (/Applications/Kut-Studio.app) », « depuis les sources (…, commit …) »…"""
    path = str(context.location) if context.location is not None else i18n.translate("update.location.unknown")
    if context.kind is InstallKind.SOURCE:
        if context.revision:
            return i18n.translate("update.install.source_revision", path=path, revision=context.revision)
        return i18n.translate("update.install.source", path=path)
    if context.kind is InstallKind.MACOS_APP:
        return i18n.translate("update.install.macos_app", path=path)
    return i18n.translate("update.install.folder", path=path)


def error_message(error: UpdateError) -> str:
    """Message traduit d'une erreur de :mod:`core.updates` (le détail technique est affiché à part)."""
    if error.kind is UpdateErrorKind.RATE_LIMITED:
        if error.retry_at is not None and error.retry_at > time.time():
            return i18n.translate("update.error.rate_limited", time=format_clock(error.retry_at))
        return i18n.translate("update.error.rate_limited_unknown")
    if error.kind is UpdateErrorKind.HTTP:
        return i18n.translate("update.error.http", status=error.status or "?")
    return i18n.translate(f"update.error.{error.kind.value}")


class UpdateDialog(QDialog):
    """Parcours de mise à jour (voir le module)."""

    download_requested = Signal()
    later_requested = Signal()
    skip_requested = Signal()
    unskip_requested = Signal()
    release_page_requested = Signal()
    cancel_requested = Signal()
    retry_requested = Signal()
    open_folder_requested = Signal()
    install_requested = Signal()

    def __init__(self, context: InstallContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("updateDialog")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumSize(560, 260)
        self._context = context
        self._view = _View()
        self._build_ui()
        self._render()
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_args: i18n.unsubscribe(callback))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(Spacing.sm)
        self.headline = QLabel()
        self.headline.setObjectName("updateHeadline")
        self.headline.setWordWrap(True)
        set_role(self.headline, TextRoles.app_title)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_role(self.message, TextRoles.label)
        root.addWidget(self.headline)
        root.addWidget(self.message)

        self.notes_title = QLabel()
        set_role(self.notes_title, TextRoles.section_title)
        self.notes = QTextBrowser()
        self.notes.setObjectName("updateNotes")
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(180)
        root.addWidget(self.notes_title)
        root.addWidget(self.notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress_label = QLabel()
        set_role(self.progress_label, TextRoles.label_secondary)
        root.addWidget(self.progress)
        root.addWidget(self.progress_label)

        self.details = QLabel()
        self.details.setObjectName("updateDetails")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_role(self.details, TextRoles.label_secondary)
        self.instructions = QLabel()
        self.instructions.setObjectName("updateInstructions")
        self.instructions.setWordWrap(True)
        self.instructions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_role(self.instructions, TextRoles.label)
        root.addWidget(self.details)
        root.addWidget(self.instructions)
        root.addStretch(0)

        row = QHBoxLayout()
        row.setSpacing(Spacing.sm)
        self.skip_button = self._button(ButtonVariant.GHOST, self._on_skip)
        self.release_page_button = self._button(ButtonVariant.SECONDARY, self.release_page_requested.emit)
        self.later_button = self._button(ButtonVariant.SECONDARY, self._on_later)
        self.cancel_button = self._button(ButtonVariant.SECONDARY, self._on_cancel)
        self.close_button = self._button(ButtonVariant.SECONDARY, self.hide)
        self.open_folder_button = self._button(ButtonVariant.SECONDARY, self.open_folder_requested.emit)
        self.retry_button = self._button(ButtonVariant.PRIMARY, self.retry_requested.emit)
        self.install_button = self._button(ButtonVariant.PRIMARY, self.install_requested.emit)
        self.download_button = self._button(ButtonVariant.PRIMARY, self.download_requested.emit)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        for button in (self.release_page_button, self.later_button, self.cancel_button, self.close_button,
                       self.open_folder_button, self.retry_button, self.install_button, self.download_button):
            row.addWidget(button)
        root.addLayout(row)

    def _button(self, variant: ButtonVariant, slot) -> QPushButton:
        button = QPushButton()
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_variant(button, variant)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    # ------------------------------------------------------------------
    # États (appelés par la fenêtre principale)
    # ------------------------------------------------------------------

    @property
    def state(self) -> UpdateDialogState:
        return self._view.state

    @property
    def offer(self) -> UpdateOffer | None:
        return self._view.offer

    @property
    def download(self) -> DownloadResult | None:
        """Paquet téléchargé et vérifié (état « prêt »), ou le dernier échec."""
        return self._view.download

    def show_checking(self) -> None:
        self._view = _View(UpdateDialogState.CHECKING)
        self._render()

    def show_check_result(self, result: CheckResult) -> None:
        if result.error is not None:
            self._view = _View(UpdateDialogState.CHECK_ERROR, check=result, error=result.error)
        elif result.offer is not None:
            self._view = _View(UpdateDialogState.OFFER, check=result, offer=result.offer)
        else:
            self._view = _View(UpdateDialogState.UP_TO_DATE, check=result)
        self._render()

    def show_offer(self, offer: UpdateOffer, check: CheckResult | None = None) -> None:
        self._view = _View(UpdateDialogState.OFFER, check=check, offer=offer)
        self._render()

    def show_downloading(self, offer: UpdateOffer) -> None:
        total = offer.package.size if offer.package is not None else -1
        self._view = _View(UpdateDialogState.DOWNLOADING, check=self._view.check, offer=offer, total=total)
        self._render()

    def update_progress(self, received: int, total: int) -> None:
        if self._view.state is not UpdateDialogState.DOWNLOADING:
            return
        self._view.received, self._view.total = int(received), int(total)
        self._render_progress()

    def show_download_result(self, result: DownloadResult) -> None:
        if result.error is None and result.path is not None:
            self._view = _View(UpdateDialogState.READY, check=self._view.check, offer=result.offer, download=result)
        else:
            self._view = _View(UpdateDialogState.DOWNLOAD_ERROR, check=self._view.check, offer=result.offer,
                               download=result, error=result.error)
        self._render()

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def _render(self) -> None:
        self.setWindowTitle(i18n.translate("update.dialog.title"))
        for button, key in (
            (self.download_button, "update.button.download"),
            (self.later_button, "update.button.later"),
            (self.release_page_button, "update.button.release_page"),
            (self.cancel_button, "update.button.cancel"),
            (self.retry_button, "update.button.retry"),
            (self.close_button, "update.button.close"),
            (self.open_folder_button, "update.button.open_folder"),
            (self.install_button, "update.button.install"),
        ):
            button.setText(i18n.translate(key))
        visible: set[QPushButton] = set()
        default: QPushButton | None = None
        state = self._view.state
        self.headline.setText("")
        self.message.setText("")
        self.details.setText("")
        self.instructions.setText("")
        set_state(self.message, "")          # couleur du rôle ; succès et erreur la remplacent plus bas
        notes_visible = progress_visible = False

        if state is UpdateDialogState.CHECKING:
            self.headline.setText(i18n.translate("update.checking.title"))
            self.message.setText(i18n.translate("update.checking.text"))
            visible = {self.cancel_button}
        elif state is UpdateDialogState.UP_TO_DATE:
            visible, default = self._render_up_to_date()
        elif state is UpdateDialogState.OFFER:
            visible, default = self._render_offer()
            notes_visible = True
        elif state is UpdateDialogState.DOWNLOADING:
            offer = self._view.offer
            self.headline.setText(i18n.translate("update.downloading.title", version=str(offer.version) if offer else ""))
            self.message.setText(i18n.translate("update.downloading.text"))
            progress_visible = True
            visible = {self.cancel_button}
        elif state is UpdateDialogState.READY:
            visible, default = self._render_ready()
        else:
            visible, default = self._render_error()

        self.notes_title.setVisible(notes_visible)
        self.notes.setVisible(notes_visible)
        self.progress.setVisible(progress_visible)
        self.progress_label.setVisible(progress_visible)
        if progress_visible:
            self._render_progress()
        for label in (self.message, self.details, self.instructions):
            label.setVisible(bool(label.text()))
        for button in (self.download_button, self.later_button, self.skip_button, self.release_page_button,
                       self.cancel_button, self.retry_button, self.close_button, self.open_folder_button,
                       self.install_button):
            button.setVisible(button in visible)
        set_single_default(self, default)

    def _render_up_to_date(self) -> tuple[set[QPushButton], QPushButton]:
        check = self._view.check
        current = str(check.current) if check is not None else ""
        self.headline.setText(i18n.translate("update.up_to_date.title"))
        lines = [i18n.translate("update.up_to_date.text", version=current)]
        if check is not None and check.hidden_prerelease is not None:
            lines.append(i18n.translate("update.up_to_date.prerelease_hidden", version=str(check.hidden_prerelease)))
        self.message.setText("\n\n".join(lines))
        if self._context.is_source:
            self.details.setText(install_description(self._context))
        return {self.close_button}, self.close_button

    def _render_offer(self) -> tuple[set[QPushButton], QPushButton]:
        offer = self._view.offer
        if offer is None:  # pas d'offre : rien à présenter, seul « Fermer » reste
            return {self.close_button}, self.close_button
        release = offer.release
        current = self._view.check.current if self._view.check is not None else None
        self.headline.setText(i18n.translate("update.available.title", version=str(offer.version)))
        lines = []
        if current is not None:
            lines.append(i18n.translate("update.available.text", current=str(current)))
        date = format_release_date(release.published_at)
        if date:
            lines.append(i18n.translate("update.available.published", date=date))
        if release.prerelease:
            lines.append(i18n.translate("update.available.prerelease"))
        if offer.skipped:
            lines.append(i18n.translate("update.available.skipped"))
        self.message.setText(" ".join(lines))
        self.notes_title.setText(i18n.translate("update.notes.title"))
        notes = release.notes.strip() or i18n.translate("update.notes.empty")
        self.notes.document().setMarkdown(
            notes,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        self.details.setText(self._package_text(offer))
        self.skip_button.setText(i18n.translate("update.button.unskip" if offer.skipped else "update.button.skip"))
        visible = {self.later_button, self.skip_button, self.release_page_button}
        if self._context.is_source:
            self.instructions.setText(i18n.translate("update.source.text"))
            return visible, self.release_page_button
        if offer.can_download:
            visible.add(self.download_button)
            return visible, self.download_button
        return visible, self.release_page_button

    def _package_text(self, offer: UpdateOffer) -> str:
        if self._context.is_source:
            return install_description(self._context)
        platform = platform_label(offer.target)
        if offer.target is None:
            return i18n.translate("update.package.unknown_platform", platform=platform)
        if offer.package is None:
            return i18n.translate("update.package.missing", platform=platform)
        if offer.checksums is None:
            return i18n.translate("update.package.no_checksums", file=CHECKSUMS_FILE)
        return i18n.translate("update.package", platform=platform, name=offer.package.name,
                              size=format_size(offer.package.size))

    def _render_progress(self) -> None:
        received, total = self._view.received, self._view.total
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(received * 1000 / total)))
            self.progress_label.setText(i18n.translate("update.progress", received=format_size(received),
                                                       total=format_size(total)))
        else:
            self.progress.setRange(0, 0)   # durée inconnue : barre indéterminée
            self.progress_label.setText(i18n.translate("update.progress.unknown", received=format_size(received)))

    def _render_ready(self) -> tuple[set[QPushButton], QPushButton]:
        offer, download = self._view.offer, self._view.download
        if offer is None or download is None:  # pas de paquet vérifié : rien à installer, seul « Fermer » reste
            return {self.close_button}, self.close_button
        version = str(offer.version)
        self.headline.setText(i18n.translate("update.ready.title", version=version))
        self.message.setText(i18n.translate("update.ready.verified", version=version))
        set_state(self.message, StatusKind.SUCCESS)
        system = offer.target.system if offer.target is not None else None
        authenticity = {
            OperatingSystem.MACOS: "update.authenticity.macos",
            OperatingSystem.WINDOWS: "update.authenticity.windows",
            OperatingSystem.LINUX: "update.authenticity.linux",
        }.get(system) if system is not None else None
        details = [i18n.translate("update.ready.integrity")]
        if authenticity is not None:
            details.append(i18n.translate(authenticity))
        details.append(i18n.translate("update.ready.file", path=str(download.path)))
        self.details.setText("\n\n".join(details))
        location = str(self._context.location) if self._context.location is not None else i18n.translate(
            "update.location.unknown")
        name = download.path.name if download.path is not None else ""
        steps = {
            OperatingSystem.MACOS: "update.steps.macos",
            OperatingSystem.WINDOWS: "update.steps.windows",
            OperatingSystem.LINUX: "update.steps.linux",
        }.get(system) if system is not None else None
        instructions = [i18n.translate(steps, location=location, name=name)] if steps else []
        if self._context.translocated:
            instructions.append(i18n.translate("update.translocated"))
        self.instructions.setText("\n\n".join(instructions))
        return {self.open_folder_button, self.install_button, self.close_button}, self.open_folder_button

    def _render_error(self) -> tuple[set[QPushButton], QPushButton]:
        error = self._view.error or UpdateError(UpdateErrorKind.NETWORK)
        downloading = self._view.state is UpdateDialogState.DOWNLOAD_ERROR
        if error.kind is UpdateErrorKind.CANCELLED:
            title = "update.cancelled.title"
        else:
            title = "update.error.download_title" if downloading else "update.error.check_title"
        self.headline.setText(i18n.translate(title))
        self.message.setText(error_message(error))
        set_state(self.message, "" if error.kind is UpdateErrorKind.CANCELLED else StatusKind.ERROR)
        if error.detail:
            self.details.setText(i18n.translate("update.error.detail", detail=error.detail))
        visible = {self.close_button}
        if downloading:
            visible.add(self.release_page_button)
            if self._view.offer is not None and self._view.offer.can_download:
                visible.add(self.download_button)
                return visible, self.download_button
            return visible, self.close_button
        visible.add(self.retry_button)
        return visible, self.retry_button

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_skip(self) -> None:
        offer = self._view.offer
        if offer is not None and offer.skipped:
            self.unskip_requested.emit()
        else:
            self.skip_requested.emit()

    def _on_later(self) -> None:
        self.later_requested.emit()
        self.hide()

    def _on_cancel(self) -> None:
        """Annuler une recherche ferme la fenêtre ; annuler un téléchargement affiche « Téléchargement annulé »."""
        checking = self._view.state is UpdateDialogState.CHECKING
        self.cancel_requested.emit()
        if checking:
            self.hide()

    def reject(self) -> None:
        """Échap ou fermeture : annule une recherche ou un téléchargement en cours, « Plus tard » sinon."""
        state = self._view.state
        if state in (UpdateDialogState.CHECKING, UpdateDialogState.DOWNLOADING):
            self.cancel_requested.emit()
        elif state is UpdateDialogState.OFFER:
            self.later_requested.emit()
        self.hide()

    def _on_language_changed(self, _code: str) -> None:
        self._render()


__all__ = [
    "UpdateDialog",
    "UpdateDialogState",
    "error_message",
    "format_clock",
    "format_release_date",
    "install_description",
    "platform_label",
]
