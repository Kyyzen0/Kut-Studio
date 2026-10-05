"""Mises à jour : transport HTTPS et enchaînement des étapes, sur la boucle d'événements Qt.

Le réseau passe par ``QNetworkAccessManager`` plutôt que par ``urllib`` dans un thread, pour deux raisons :

* **certificats** : Qt utilise le magasin du système (Trousseau macOS, Schannel Windows, OpenSSL et ses certificats
  sous Linux). Un ``urllib`` figé par PyInstaller cherche les certificats là où ils étaient sur la machine de
  construction et échoue chez l'utilisateur (``CERTIFICATE_VERIFY_FAILED``) ;
* **interface jamais bloquée** : tout est asynchrone sur la boucle Qt — progression, délais (minuteries) et
  annulation (``abort``) sans thread. L'empreinte SHA-256 est calculée **au fil de la réception** : aucune relecture
  du fichier, aucun gel en fin de téléchargement.

Les règles (quoi proposer, quoi accepter) sont dans :mod:`core.updates` ; ici, on transporte et on enchaîne. Un
téléchargement s'écrit dans ``<nom>.part`` ; seul un fichier dont la taille et l'empreinte sont celles publiées est
renommé sous son nom définitif. En cas d'échec ou d'annulation, le fichier partiel est supprimé : rien d'incomplet ne
reste à ouvrir.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslSocket

from .app_version import APP_VERSION, GITHUB_REPOSITORY
from .release_assets import CHECKSUMS_FILE, MAX_CHECKSUMS_BYTES, Target, detect_target, parse_asset_name
from .updates import (
    GITHUB_API,
    MAX_RELEASES_BYTES,
    CheckMode,
    CheckResult,
    UpdateError,
    UpdateErrorKind,
    UpdateOffer,
    download_mark_expected,
    evaluate_releases,
    expected_checksum,
    http_failure,
    mark_as_downloaded,
    parse_releases,
    releases_api_url,
    updates_directory,
    user_agent,
    verify_package,
)
from .versioning import Version

LOGGER = logging.getLogger(__name__)

API_TIMEOUT_MS = 20_000
"""Durée maximale d'une requête à l'API (réponse complète)."""
IDLE_TIMEOUT_MS = 30_000
"""Abandon si plus aucun octet n'arrive pendant ce délai (téléchargement : pas de durée totale maximale)."""
MAX_REDIRECTS = 10

_OFFLINE_ERRORS = {
    QNetworkReply.NetworkError.HostNotFoundError,
    QNetworkReply.NetworkError.ConnectionRefusedError,
    QNetworkReply.NetworkError.RemoteHostClosedError,
    QNetworkReply.NetworkError.NetworkSessionFailedError,
    QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    QNetworkReply.NetworkError.UnknownNetworkError,
    QNetworkReply.NetworkError.ProxyConnectionRefusedError,
    QNetworkReply.NetworkError.ProxyNotFoundError,
}


def tls_available() -> bool:
    """Qt dispose-t-il d'un moteur TLS (``https``) ? Contrôlé par le smoke test de l'application construite."""
    return bool(QSslSocket.supportsSsl())


def tls_self_check() -> str | None:
    """``None`` si HTTPS est utilisable, sinon la raison (smoke test de ``main.py``)."""
    if tls_available():
        return None
    backends = ", ".join(QSslSocket.availableBackends()) or "aucun"
    return f"aucun moteur TLS utilisable par Qt (moteurs trouvés : {backends})"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransferResult:
    """Fin d'un transfert : réussi (``error is None``) ou non."""

    status: int = 0
    headers: Mapping[str, str] | None = None
    body: bytes = b""
    path: Path | None = None
    size: int = 0
    sha256: str = ""
    error: UpdateError | None = None


class Transfer(QObject):
    """Un transfert HTTP(S) en cours : ``progress(reçus, total)`` puis ``finished(TransferResult)``, une seule fois.

    ``total`` vaut -1 tant que la taille est inconnue. :meth:`cancel` interrompt ; le résultat porte alors
    ``CANCELLED`` et le fichier partiel est supprimé.
    """

    # ``object`` : un ``int`` Qt est sur 32 bits, un paquet peut dépasser 2 Go.
    progress = Signal(object, object)
    finished = Signal(object)

    def __init__(
        self,
        reply: QNetworkReply,
        *,
        max_bytes: int,
        idle_timeout_ms: int,
        total_timeout_ms: int | None,
        destination: Path | None,
        expected_size: int | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._reply = reply
        self._max_bytes = max_bytes
        self._destination = destination
        self._expected = expected_size
        self._buffer = bytearray()
        self._received = 0
        self._digest = hashlib.sha256()
        self._failure: UpdateError | None = None
        self._done = False
        self._stream: IO[bytes] | None = None
        if destination is not None:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._stream = open(destination, "wb")
            except OSError as exc:
                self._failure = UpdateError(UpdateErrorKind.DISK, str(exc))
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(idle_timeout_ms)
        self._idle.timeout.connect(lambda: self._abort(UpdateError(UpdateErrorKind.TIMEOUT, "aucune donnée reçue")))
        self._total: QTimer | None = None
        if total_timeout_ms is not None:
            self._total = QTimer(self)
            self._total.setSingleShot(True)
            self._total.setInterval(total_timeout_ms)
            self._total.timeout.connect(lambda: self._abort(UpdateError(UpdateErrorKind.TIMEOUT, "réponse trop lente")))
            self._total.start()
        self._idle.start()
        reply.readyRead.connect(self._on_ready_read)
        reply.finished.connect(self._on_finished)
        if self._failure is not None:
            # Différé : ``abort`` termine le transfert tout de suite, avant que l'appelant ait pu s'y connecter.
            QTimer.singleShot(0, self._abort_now)

    @property
    def received(self) -> int:
        return self._received

    @property
    def finished_already(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self._abort(UpdateError(UpdateErrorKind.CANCELLED))

    def _abort(self, error: UpdateError) -> None:
        if self._done:
            return
        if self._failure is None:
            self._failure = error
        self._abort_now()

    def _abort_now(self) -> None:
        self._reply.abort()
        # ``abort`` émet normalement ``finished`` aussitôt ; sinon on termine ici, pour que le fichier partiel
        # soit supprimé et le résultat émis même si la boucle d'événements s'arrête (fermeture de l'application).
        if not self._done:
            self._on_finished()

    def _on_ready_read(self) -> None:
        if self._done or self._failure is not None:
            return
        chunk = bytes(self._reply.readAll().data())
        if not chunk:
            return
        self._idle.start()
        self._received += len(chunk)
        if self._received > self._max_bytes:
            self._abort(UpdateError(UpdateErrorKind.TOO_LARGE, f"plus de {self._max_bytes} octets"))
            return
        self._digest.update(chunk)
        if self._stream is not None:
            try:
                self._stream.write(chunk)
            except OSError as exc:
                self._abort(UpdateError(UpdateErrorKind.DISK, str(exc)))
                return
        else:
            self._buffer.extend(chunk)
        total = self._expected if self._expected is not None else self._content_length()
        self.progress.emit(self._received, total)

    def _content_length(self) -> int:
        value = self._reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        try:
            return int(value) if value is not None else -1
        except (TypeError, ValueError):
            return -1

    def _headers(self) -> dict[str, str]:
        return {
            bytes(name.data()).decode("latin-1"): bytes(value.data()).decode("latin-1")
            for name, value in self._reply.rawHeaderPairs()
        }

    def _on_finished(self) -> None:
        if self._done:
            return
        if self._failure is None:
            self._on_ready_read()                  # dernières données encore en tampon
            if self._done:                         # cette lecture a dépassé la limite : résultat déjà émis
                return
        self._done = True
        self._idle.stop()
        if self._total is not None:
            self._total.stop()
        status_value = self._reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        status = int(status_value) if isinstance(status_value, int) else 0
        headers = self._headers()
        path, error = self._finish_file(self._failure or self._classify(status, headers))
        result = TransferResult(
            status=status,
            headers=headers,
            body=bytes(self._buffer) if error is None else b"",
            path=path,
            size=self._received,
            sha256=self._digest.hexdigest(),
            error=error,
        )
        self._reply.deleteLater()
        self.finished.emit(result)
        self.deleteLater()

    def _classify(self, status: int, headers: Mapping[str, str]) -> UpdateError | None:
        code = self._reply.error()
        if status >= 400:
            return http_failure(status, headers)
        if code == QNetworkReply.NetworkError.NoError:
            if status and not 200 <= status < 300:
                return UpdateError(UpdateErrorKind.HTTP, f"HTTP {status}", status=status)
            return None
        detail = f"{code.name} : {self._reply.errorString()}"
        if code == QNetworkReply.NetworkError.SslHandshakeFailedError:
            return UpdateError(UpdateErrorKind.TLS, detail)
        if code in (QNetworkReply.NetworkError.TimeoutError, QNetworkReply.NetworkError.ProxyTimeoutError):
            return UpdateError(UpdateErrorKind.TIMEOUT, detail)
        if code in _OFFLINE_ERRORS:
            return UpdateError(UpdateErrorKind.OFFLINE, detail)
        if code == QNetworkReply.NetworkError.OperationCanceledError:
            return UpdateError(UpdateErrorKind.CANCELLED, detail)
        return UpdateError(UpdateErrorKind.NETWORK, detail)

    def _finish_file(self, error: UpdateError | None) -> tuple[Path | None, UpdateError | None]:
        """Ferme le fichier (données sur le disque avant tout renommage) ; le supprime si le transfert a échoué."""
        if self._destination is None:
            return None, error
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.flush()
                if error is None:
                    os.fsync(stream.fileno())
            except OSError as exc:
                error = error or UpdateError(UpdateErrorKind.DISK, str(exc))
            try:
                stream.close()
            except OSError as exc:
                error = error or UpdateError(UpdateErrorKind.DISK, str(exc))
        if error is not None:
            _remove_quietly(self._destination)
            return None, error
        return self._destination, None


class HttpClient(QObject):
    """Requêtes HTTPS sans cookies ni identifiants : ``User-Agent`` Kut-Studio, redirections sûres uniquement."""

    def __init__(self, parent: QObject | None = None, *, agent: str | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._agent = (agent or user_agent()).encode("ascii")

    def _request(self, url: str, accept: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", self._agent)
        request.setRawHeader(b"Accept", accept.encode("ascii"))
        # Jamais de passage de HTTPS à HTTP lors d'une redirection (GitHub redirige les fichiers vers son CDN).
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setMaximumRedirectsAllowed(MAX_REDIRECTS)
        request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute, False)
        return request

    def get(
        self,
        url: str,
        *,
        accept: str = "*/*",
        headers: Mapping[str, str] | None = None,
        max_bytes: int,
        total_timeout_ms: int | None = None,
        idle_timeout_ms: int | None = None,
    ) -> Transfer:
        """Corps complet en mémoire (limité à ``max_bytes``) ; délais par défaut : constantes du module."""
        request = self._request(url, accept)
        for name, value in (headers or {}).items():
            request.setRawHeader(name.encode("ascii"), value.encode("ascii"))
        reply = self._manager.get(request)
        return Transfer(reply, max_bytes=max_bytes, idle_timeout_ms=idle_timeout_ms or IDLE_TIMEOUT_MS,
                        total_timeout_ms=total_timeout_ms or API_TIMEOUT_MS, destination=None, expected_size=None,
                        parent=self)

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int,
        idle_timeout_ms: int | None = None,
    ) -> Transfer:
        """Écrit dans ``destination`` (supprimé en cas d'échec) ; refuse plus de ``expected_size`` octets."""
        reply = self._manager.get(self._request(url, "application/octet-stream"))
        return Transfer(reply, max_bytes=expected_size, idle_timeout_ms=idle_timeout_ms or IDLE_TIMEOUT_MS,
                        total_timeout_ms=None, destination=destination, expected_size=expected_size, parent=self)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        LOGGER.warning("Mise à jour : impossible de supprimer %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------


class UpdateChecker(QObject):
    """Interroge les GitHub Releases et émet ``finished(CheckResult)`` ; une recherche à la fois."""

    finished = Signal(object)

    def __init__(
        self,
        client: HttpClient,
        *,
        current: Version | None = None,
        repository: str = GITHUB_REPOSITORY,
        api_base: str = GITHUB_API,
        asset_prefix: str | None = None,
        target_detector: Callable[[], Target | None] = detect_target,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._current = current or Version.parse(APP_VERSION)
        self._repository = repository
        self._api_base = api_base
        self._asset_prefix = asset_prefix
        self._detect_target = target_detector
        self._transfer: Transfer | None = None
        self._pending: tuple[CheckMode, bool, Version | None] | None = None

    @property
    def busy(self) -> bool:
        return self._transfer is not None

    def check(self, *, mode: CheckMode, include_prereleases: bool, skipped_version: Version | None) -> bool:
        """Lance une recherche ; ``False`` si une autre est en cours (elle donnera son résultat)."""
        if self._transfer is not None:
            return False
        if not tls_available() and self._api_base.startswith("https:"):
            result = CheckResult(mode, self._current, error=UpdateError(UpdateErrorKind.UNSUPPORTED, "pas de TLS"))
            QTimer.singleShot(0, lambda: self.finished.emit(result))
            return True
        self._pending = (mode, include_prereleases, skipped_version)
        self._transfer = self._client.get(
            releases_api_url(self._repository, self._api_base),
            accept="application/vnd.github+json",
            headers={"X-GitHub-Api-Version": "2022-11-28"},
            max_bytes=MAX_RELEASES_BYTES,
        )
        self._transfer.finished.connect(self._on_finished)
        return True

    def cancel(self) -> None:
        if self._transfer is not None:
            self._transfer.cancel()

    def _on_finished(self, transfer: TransferResult) -> None:
        self._transfer = None
        pending, self._pending = self._pending, None
        if pending is None:  # pragma: no cover - un seul transfert à la fois
            return
        mode, include_prereleases, skipped = pending
        try:
            result = self._evaluate(transfer, mode, include_prereleases, skipped)
        except Exception as exc:  # noqa: BLE001 - filet : une recherche se termine toujours par un résultat
            LOGGER.exception("Mises à jour : évaluation de la réponse impossible")
            result = CheckResult(mode, self._current, error=UpdateError(UpdateErrorKind.INVALID_RESPONSE, repr(exc)))
        if result.error is not None:
            LOGGER.info("Mises à jour (%s) : échec — %s", mode.value, result.error)
        elif result.offer is not None:
            LOGGER.info("Mises à jour (%s) : %s disponible", mode.value, result.offer.version)
        else:
            LOGGER.info("Mises à jour (%s) : %s est à jour", mode.value, self._current)
        self.finished.emit(result)

    def _evaluate(self, transfer: TransferResult, mode: CheckMode, include_prereleases: bool,
                  skipped: Version | None) -> CheckResult:
        if transfer.error is not None:
            return CheckResult(mode, self._current, error=transfer.error)
        # Toute réponse illisible (JSON cassé, objet au lieu d'une liste…) devient un résultat d'erreur : une
        # exception ici, dans un slot Qt, laisserait la recherche « en cours » pour toujours.
        try:
            payload = json.loads(transfer.body.decode("utf-8"))
            releases = parse_releases(payload, repository=self._repository, asset_prefix=self._asset_prefix)
        except UpdateError as error:
            return CheckResult(mode, self._current, error=error)
        except ValueError as exc:            # UnicodeDecodeError et JSONDecodeError en héritent
            return CheckResult(mode, self._current, error=UpdateError(UpdateErrorKind.INVALID_RESPONSE, str(exc)))
        return evaluate_releases(
            releases,
            current=self._current,
            target=self._detect_target(),
            include_prereleases=include_prereleases,
            skipped_version=skipped,
            mode=mode,
        )


# ---------------------------------------------------------------------------
# Téléchargement vérifié
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadResult:
    offer: UpdateOffer
    path: Path | None = None
    sha256: str = ""
    marked: bool = False
    """La marque « téléchargé d'Internet » du système a été posée (quarantaine macOS, Zone.Identifier Windows)."""
    error: UpdateError | None = None


class UpdateDownloader(QObject):
    """Télécharge le paquet d'une offre, le vérifie, puis seulement le rend disponible.

    Étapes : ``SHA256SUMS.txt`` → empreinte attendue (et cohérence avec celle de GitHub) → paquet dans
    ``<nom>.part`` avec empreinte au fil de l'eau → taille et SHA-256 → marque « téléchargé d'Internet » (refus si
    macOS ou Windows ne l'acceptent pas) → renommage atomique. ``progress(reçus, total)`` pendant le paquet, puis
    ``finished(DownloadResult)``.
    """

    progress = Signal(object, object)
    finished = Signal(object)

    def __init__(self, client: HttpClient, *, directory: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._directory = directory
        self._transfer: Transfer | None = None
        self._offer: UpdateOffer | None = None
        self._expected = ""
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self._offer is not None

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else updates_directory()

    def start(self, offer: UpdateOffer) -> bool:
        """Démarre ; ``False`` si un téléchargement est déjà en cours."""
        if self._offer is not None:
            return False
        self._offer, self._cancelled, self._expected = offer, False, ""
        if offer.package is None:
            self._fail(UpdateError(UpdateErrorKind.NO_PACKAGE, str(offer.target)), later=True)
            return True
        if offer.checksums is None:
            self._fail(UpdateError(UpdateErrorKind.CHECKSUM_MISSING, f"{CHECKSUMS_FILE} absent"), later=True)
            return True
        if not offer.can_download or parse_asset_name(offer.package.name) is None:
            self._fail(UpdateError(UpdateErrorKind.INVALID_RESPONSE, f"paquet refusé : {offer.package.name}"), later=True)
            return True
        try:
            self._prepare_directory()
        except OSError as exc:
            self._fail(UpdateError(UpdateErrorKind.DISK, str(exc)), later=True)
            return True
        self._transfer = self._client.get(offer.checksums.url, max_bytes=MAX_CHECKSUMS_BYTES)
        self._transfer.finished.connect(self._on_checksums)
        return True

    def cancel(self) -> None:
        """Interrompt ; le fichier partiel est supprimé et ``finished`` porte ``CANCELLED``."""
        if self._offer is None:
            return
        self._cancelled = True
        if self._transfer is not None:
            self._transfer.cancel()

    def _prepare_directory(self) -> None:
        """Crée le dossier et retire les téléchargements précédents (seulement nos fichiers)."""
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True)
        for entry in directory.iterdir():
            name = entry.name.removesuffix(".part")
            if entry.is_file() and parse_asset_name(name) is not None:
                _remove_quietly(entry)

    def _on_checksums(self, transfer: TransferResult) -> None:
        self._transfer = None
        offer = self._offer
        if offer is None or offer.package is None:  # pragma: no cover - garde de cohérence
            return
        if self._cancelled:
            self._fail(UpdateError(UpdateErrorKind.CANCELLED))
            return
        if transfer.error is not None:
            self._fail(transfer.error)
            return
        try:
            self._expected = expected_checksum(transfer.body.decode("utf-8"), offer.package)
        except UnicodeDecodeError as exc:
            self._fail(UpdateError(UpdateErrorKind.INVALID_RESPONSE, f"{CHECKSUMS_FILE} : {exc}"))
            return
        except UpdateError as error:
            self._fail(error)
            return
        partial = self.directory / f"{offer.package.name}.part"
        self._transfer = self._client.download(offer.package.url, partial, expected_size=offer.package.size)
        self._transfer.progress.connect(self.progress)
        self._transfer.finished.connect(self._on_package)

    def _on_package(self, transfer: TransferResult) -> None:
        self._transfer = None
        offer = self._offer
        if offer is None or offer.package is None:  # pragma: no cover - garde de cohérence
            return
        if transfer.error is not None or transfer.path is None:
            self._fail(transfer.error or UpdateError(UpdateErrorKind.DISK, "fichier absent"))
            return
        try:
            verify_package(size=transfer.size, sha256=transfer.sha256, package=offer.package,
                           expected_sha256=self._expected)
            # Marque « téléchargé d'Internet » posée sur le .part, AVANT le renommage (l'attribut et le flux suivent
            # le fichier) : le nom définitif n'apparaît que pour un paquet vérifié ET marqué. Là où le système
            # l'attend, un échec refuse le paquet : il échapperait sinon au contrôle de Gatekeeper / SmartScreen.
            marked = mark_as_downloaded(transfer.path, url=offer.package.url)
            if not marked and download_mark_expected():
                raise UpdateError(UpdateErrorKind.MARK_FAILED, f"marque système refusée pour {transfer.path}")
            final = self.directory / offer.package.name
            os.replace(transfer.path, final)
        except UpdateError as error:
            _remove_quietly(transfer.path)
            self._fail(error)
            return
        except OSError as exc:
            _remove_quietly(transfer.path)
            self._fail(UpdateError(UpdateErrorKind.DISK, str(exc)))
            return
        LOGGER.info("Mise à jour %s téléchargée et vérifiée : %s (sha256 %s, marque système : %s)",
                    offer.version, final, transfer.sha256, marked)
        self._offer = None
        self.finished.emit(DownloadResult(offer, path=final, sha256=transfer.sha256, marked=marked))

    def _fail(self, error: UpdateError, *, later: bool = False) -> None:
        offer = self._offer
        if offer is None:  # pragma: no cover - garde de cohérence
            return
        if self._cancelled and error.kind is not UpdateErrorKind.CANCELLED:
            error = UpdateError(UpdateErrorKind.CANCELLED, error.detail)
        LOGGER.info("Mise à jour %s : téléchargement refusé — %s", offer.version, error)
        self._offer = None
        result = DownloadResult(offer, error=error)
        if later:   # le contrat reste asynchrone : ``finished`` n'arrive jamais pendant ``start``
            QTimer.singleShot(0, lambda: self.finished.emit(result))
        else:
            self.finished.emit(result)


__all__ = [
    "API_TIMEOUT_MS",
    "DownloadResult",
    "HttpClient",
    "IDLE_TIMEOUT_MS",
    "Transfer",
    "TransferResult",
    "UpdateChecker",
    "UpdateDownloader",
    "tls_available",
    "tls_self_check",
]
