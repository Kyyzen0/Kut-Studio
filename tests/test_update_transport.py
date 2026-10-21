"""Transport Qt des mises à jour contre un faux GitHub local : erreurs réseau, limites, délais, annulation, empreintes.

Le vrai ``QNetworkAccessManager`` parle à un serveur HTTP de ``tests/update_server.py`` (127.0.0.1) : les réponses
sont simulées, la pile réseau ne l'est pas. Le HTTPS de GitHub et ses redirections vers son CDN ne sont pas
reproduits ici (pas de certificat de test) ; le smoke test vérifie seulement qu'un moteur TLS est disponible.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtNetwork import QNetworkReply
from update_server import Route, UpdateServer, closed_port_url

from core import update_service
from core.release_assets import CHECKSUMS_FILE, Architecture, OperatingSystem, Target, format_checksums
from core.update_service import HttpClient, Transfer, UpdateChecker, UpdateDownloader, tls_available, tls_self_check
from core.updates import (
    QUARANTINE_ATTRIBUTE,
    CheckMode,
    CheckResult,
    ReleaseAsset,
    UpdateErrorKind,
    UpdateOffer,
    macos_extended_attribute,
    parse_releases,
)
from core.versioning import Version

MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
REPOSITORY = "owner/kut"
PACKAGE = "Kut-Studio-0.2.0-macos-arm64.zip"
PAYLOAD = bytes(range(256)) * 400                      # 100 Ko
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def server():
    instance = UpdateServer()
    yield instance
    instance.close()


@pytest.fixture
def client(qapp):
    instance = HttpClient()
    yield instance
    instance.deleteLater()


def _wait(qtbot, signal, timeout=8000):
    with qtbot.waitSignal(signal, timeout=timeout) as blocker:
        pass
    return blocker.args[0]


def _releases_json(server: UpdateServer, *, package_size=len(PAYLOAD), digest=None) -> bytes:
    def asset(name, size, extra=None):
        data = {"name": name, "size": size, "state": "uploaded",
                "browser_download_url": server.url(f"/download/v0.2.0/{name}")}
        data.update(extra or {})
        return data

    package_extra = {"digest": f"sha256:{digest}"} if digest else None
    return json.dumps([{
        "tag_name": "v0.2.0", "name": "Kut-Studio 0.2.0", "body": "## Nouveautés\n\n- Mises à jour",
        "draft": False, "prerelease": False, "published_at": "2026-10-05T12:00:00Z",
        "assets": [asset(PACKAGE, package_size, package_extra), asset(CHECKSUMS_FILE, 200)],
    }]).encode("utf-8")


def _checker(server, client, **kwargs) -> UpdateChecker:
    return UpdateChecker(client, current=Version.parse("0.1.0"), repository=REPOSITORY, api_base=server.base,
                         asset_prefix=server.url("/download/"), target_detector=lambda: MAC_ARM, **kwargs)


def _api(server, route: Route) -> None:
    server.routes[f"/repos/{REPOSITORY}/releases"] = route


def _check(qtbot, checker, mode=CheckMode.MANUAL) -> CheckResult:
    with qtbot.waitSignal(checker.finished, timeout=8000) as blocker:
        assert checker.check(mode=mode, include_prereleases=False, skipped_version=None)
    return blocker.args[0]


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------


def test_a_check_finds_the_release_and_identifies_itself_honestly(qtbot, server, client):
    _api(server, Route(body=_releases_json(server)))
    result = _check(qtbot, _checker(server, client))
    assert result.error is None
    assert str(result.offer.version) == "0.2.0"
    assert result.offer.package.name == PACKAGE and result.offer.can_download
    (path, headers), = server.requests
    assert path == f"/repos/{REPOSITORY}/releases"
    assert headers["user-agent"].startswith("Kut-Studio/")
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert "authorization" not in headers and "cookie" not in headers


def test_github_rate_limit_is_recognised_with_its_reset_time(qtbot, server, client):
    reset = int(time.time()) + 1800
    _api(server, Route(status=403, body=b'{"message": "API rate limit exceeded"}',
                       headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}))
    result = _check(qtbot, _checker(server, client))
    assert result.error.kind is UpdateErrorKind.RATE_LIMITED
    assert result.error.retry_at == reset


def test_secondary_rate_limit_429(qtbot, server, client):
    _api(server, Route(status=429, headers={"Retry-After": "30"}))
    assert _check(qtbot, _checker(server, client)).error.kind is UpdateErrorKind.RATE_LIMITED


@pytest.mark.parametrize("status", [404, 500, 503])
def test_server_errors_are_http_errors_with_their_status(qtbot, server, client, status):
    _api(server, Route(status=status, body=b"{}"))
    error = _check(qtbot, _checker(server, client)).error
    assert error.kind is UpdateErrorKind.HTTP and error.status == status


@pytest.mark.parametrize("body", [b"{not json", b'{"message": "moved"}', b"\xff\xfe\x00", b"[1, 2, 3]"])
def test_invalid_responses_never_produce_an_offer(qtbot, server, client, body):
    _api(server, Route(body=body))
    result = _check(qtbot, _checker(server, client))
    assert result.offer is None
    if body != b"[1, 2, 3]":                       # une liste sans release lisible : « à jour », pas une erreur
        assert result.error.kind is UpdateErrorKind.INVALID_RESPONSE


def test_no_network_is_reported_as_offline(qtbot, client):
    checker = UpdateChecker(client, current=Version.parse("0.1.0"), repository=REPOSITORY,
                            api_base=closed_port_url("").rstrip("/"), target_detector=lambda: MAC_ARM)
    result = _check(qtbot, checker)
    assert result.error.kind is UpdateErrorKind.OFFLINE


def test_a_server_that_never_answers_times_out(qtbot, server, client, monkeypatch):
    monkeypatch.setattr(update_service, "API_TIMEOUT_MS", 300)
    _api(server, Route(body=_releases_json(server), delay=3.0))
    started = time.monotonic()
    result = _check(qtbot, _checker(server, client))
    assert result.error.kind is UpdateErrorKind.TIMEOUT
    assert result.offer is None
    assert time.monotonic() - started < 2.5


def test_an_oversized_api_response_is_cut(qtbot, server, client, monkeypatch):
    monkeypatch.setattr(update_service, "MAX_RELEASES_BYTES", 100)
    _api(server, Route(body=_releases_json(server)))
    assert _check(qtbot, _checker(server, client)).error.kind is UpdateErrorKind.TOO_LARGE


def test_one_check_at_a_time_and_cancel(qtbot, server, client):
    _api(server, Route(body=_releases_json(server), delay=1.0))
    checker = _checker(server, client)
    with qtbot.waitSignal(checker.finished, timeout=8000) as blocker:
        assert checker.check(mode=CheckMode.AUTOMATIC, include_prereleases=False, skipped_version=None)
        assert checker.busy
        assert not checker.check(mode=CheckMode.MANUAL, include_prereleases=False, skipped_version=None)
        checker.cancel()
    assert blocker.args[0].error.kind is UpdateErrorKind.CANCELLED
    assert blocker.args[0].mode is CheckMode.AUTOMATIC
    assert not checker.busy


def test_tls_is_available_to_this_qt():
    """Le smoke test de l'application construite fait la même vérification (``main.py --smoke-test``)."""
    assert tls_available()
    assert tls_self_check() is None


# ---------------------------------------------------------------------------
# Téléchargement vérifié
# ---------------------------------------------------------------------------


def _offer(server, *, size=len(PAYLOAD), digest=None, checksums=True) -> UpdateOffer:
    (release,) = parse_releases(json.loads(_releases_json(server, package_size=size, digest=digest)),
                                repository=REPOSITORY, asset_prefix=server.url("/download/"))
    package = release.package_for(MAC_ARM)
    return UpdateOffer(release, MAC_ARM, package, release.checksums if checksums else None)


def _serve_package(server, *, body=PAYLOAD, sums=None, **route):
    server.routes[f"/download/v0.2.0/{PACKAGE}"] = Route(body=body, **route)
    text = format_checksums({PACKAGE: PAYLOAD_SHA}) if sums is None else sums
    server.routes[f"/download/v0.2.0/{CHECKSUMS_FILE}"] = Route(body=text.encode("utf-8"))


def _download(qtbot, downloader, offer, timeout=10000):
    with qtbot.waitSignal(downloader.finished, timeout=timeout) as blocker:
        assert downloader.start(offer)
    return blocker.args[0]


def _left(directory):
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def test_a_verified_download_is_renamed_marked_and_reported(qtbot, server, client, tmp_path):
    _serve_package(server, chunks=8)
    downloader = UpdateDownloader(client, directory=tmp_path / "updates")
    progress: list[tuple[int, int]] = []
    downloader.progress.connect(lambda received, total: progress.append((received, total)))
    result = _download(qtbot, downloader, _offer(server))
    assert result.error is None
    assert result.path == tmp_path / "updates" / PACKAGE
    assert result.path.read_bytes() == PAYLOAD and result.sha256 == PAYLOAD_SHA
    assert _left(tmp_path / "updates") == [PACKAGE], "aucun .part ne reste"
    assert progress and progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert all(total == len(PAYLOAD) for _received, total in progress)
    if sys.platform == "darwin":
        assert result.marked
        assert macos_extended_attribute(result.path, QUARANTINE_ATTRIBUTE).startswith(b"0081;")
    assert not downloader.busy


def test_a_package_the_system_refuses_to_mark_is_refused(qtbot, server, client, tmp_path, monkeypatch):
    """macOS / Windows : sans marque « téléchargé d'Internet », Gatekeeper / SmartScreen ne contrôleraient rien."""
    marked: list[str] = []

    def refuse(path, *, url):
        marked.append(Path(path).name)
        return False

    monkeypatch.setattr(update_service, "mark_as_downloaded", refuse)
    monkeypatch.setattr(update_service, "download_mark_expected", lambda: True)
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.MARK_FAILED and result.path is None
    assert marked == [f"{PACKAGE}.part"], "la marque est posée avant le renommage, sur le fichier partiel"
    assert _left(tmp_path) == [], "ni paquet ni fichier partiel"


def test_where_the_system_has_no_mark_a_verified_package_is_offered(qtbot, server, client, tmp_path, monkeypatch):
    """Linux : pas de marque système, rien n'est exigé."""
    monkeypatch.setattr(update_service, "mark_as_downloaded", lambda path, *, url: False)
    monkeypatch.setattr(update_service, "download_mark_expected", lambda: False)
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server))
    assert result.error is None and not result.marked
    assert _left(tmp_path) == [PACKAGE]


def test_a_wrong_checksum_deletes_the_file_and_never_offers_it(qtbot, server, client, tmp_path):
    _serve_package(server, sums=format_checksums({PACKAGE: "0" * 64}))
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.CHECKSUM_MISMATCH and result.path is None
    assert _left(tmp_path) == []


def test_a_short_file_fails_on_size(qtbot, server, client, tmp_path):
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server, size=len(PAYLOAD) + 10))
    assert result.error.kind is UpdateErrorKind.SIZE_MISMATCH
    assert _left(tmp_path) == []


def test_a_file_larger_than_announced_is_cut_and_deleted(qtbot, server, client, tmp_path):
    _serve_package(server, chunks=10, pause=0.02)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server, size=1000))
    assert result.error.kind is UpdateErrorKind.TOO_LARGE
    assert _left(tmp_path) == []


def test_a_package_missing_from_sha256sums_is_not_even_downloaded(qtbot, server, client, tmp_path):
    _serve_package(server, sums=format_checksums({"autre.zip": PAYLOAD_SHA}))
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.CHECKSUM_MISSING
    assert not server.requested(f"/download/v0.2.0/{PACKAGE}")


def test_github_digest_contradicting_sha256sums_stops_before_the_package(qtbot, server, client, tmp_path):
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server, digest="f" * 64))
    assert result.error.kind is UpdateErrorKind.CHECKSUM_MISMATCH
    assert not server.requested(f"/download/v0.2.0/{PACKAGE}")


def test_without_published_checksums_nothing_is_requested(qtbot, server, client, tmp_path):
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server, checksums=False))
    assert result.error.kind is UpdateErrorKind.CHECKSUM_MISSING
    assert server.requests == []


def test_cancelling_mid_download_removes_the_partial_file(qtbot, server, client, tmp_path):
    _serve_package(server, chunks=50, pause=0.05)
    downloader = UpdateDownloader(client, directory=tmp_path)
    seen: list[int] = []

    def on_progress(received, _total):
        seen.append(received)
        if len(seen) == 2:
            assert (tmp_path / f"{PACKAGE}.part").exists(), "le téléchargement s'écrit dans un fichier .part"
            downloader.cancel()

    downloader.progress.connect(on_progress)
    result = _download(qtbot, downloader, _offer(server))
    assert result.error.kind is UpdateErrorKind.CANCELLED
    assert result.path is None
    assert _left(tmp_path) == []
    assert seen[-1] < len(PAYLOAD)


def test_a_stalled_download_times_out(qtbot, server, client, tmp_path, monkeypatch):
    monkeypatch.setattr(update_service, "IDLE_TIMEOUT_MS", 300)
    _serve_package(server, chunks=2, pause=3.0)
    downloader = UpdateDownloader(client, directory=tmp_path)
    started = time.monotonic()
    result = _download(qtbot, downloader, _offer(server))
    assert time.monotonic() - started < 2.5
    assert result.error.kind is UpdateErrorKind.TIMEOUT
    assert _left(tmp_path) == []


def test_a_redirect_to_the_file_is_followed(qtbot, server, client, tmp_path):
    server.routes["/cdn/package.bin"] = Route(body=PAYLOAD)
    destination = tmp_path / "file.part"
    server.routes["/download/redirect"] = Route(redirect=server.url("/cdn/package.bin"))
    transfer = client.download(server.url("/download/redirect"), destination, expected_size=len(PAYLOAD))
    result = _wait(qtbot, transfer.finished)
    assert result.error is None and result.sha256 == PAYLOAD_SHA and destination.read_bytes() == PAYLOAD


def test_an_unwritable_destination_is_a_disk_error(qtbot, server, client, tmp_path):
    blocker_file = tmp_path / "not-a-directory"
    blocker_file.write_text("x", encoding="utf-8")
    server.routes["/file"] = Route(body=PAYLOAD)
    transfer = client.download(server.url("/file"), blocker_file / "file.part", expected_size=len(PAYLOAD))
    assert _wait(qtbot, transfer.finished).error.kind is UpdateErrorKind.DISK


def test_previous_downloads_are_cleaned_but_foreign_files_are_kept(qtbot, server, client, tmp_path):
    (tmp_path / "Kut-Studio-0.1.9-macos-arm64.zip").write_bytes(b"ancien")
    (tmp_path / "Kut-Studio-0.2.0-macos-arm64.zip.part").write_bytes(b"partiel")
    (tmp_path / "notes.txt").write_text("à garder", encoding="utf-8")
    _serve_package(server)
    result = _download(qtbot, UpdateDownloader(client, directory=tmp_path), _offer(server))
    assert result.error is None
    assert _left(tmp_path) == [PACKAGE, "notes.txt"]


def test_an_offer_without_package_fails_asynchronously(qtbot, client, tmp_path):
    release = parse_releases([{"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": []}])[0]
    downloader = UpdateDownloader(client, directory=tmp_path)
    finished: list = []
    downloader.finished.connect(finished.append)
    assert downloader.start(UpdateOffer(release, None, None, None))
    assert finished == [], "le résultat n'arrive jamais pendant start()"
    qtbot.waitUntil(lambda: bool(finished), timeout=2000)
    assert finished[0].error.kind is UpdateErrorKind.NO_PACKAGE


def test_an_offer_with_a_foreign_package_name_is_refused(qtbot, client, tmp_path):
    release = parse_releases([{"tag_name": "v0.2.0", "draft": False, "prerelease": False, "assets": []}])[0]
    package = ReleaseAsset("installeur.exe", "https://github.com/x/releases/download/v0.2.0/installeur.exe", 10)
    sums = ReleaseAsset(CHECKSUMS_FILE, "https://github.com/x/releases/download/v0.2.0/SHA256SUMS.txt", 10)
    downloader = UpdateDownloader(client, directory=tmp_path)
    result = _download(qtbot, downloader, UpdateOffer(release, MAC_ARM, package, sums))
    assert result.error.kind is UpdateErrorKind.INVALID_RESPONSE


# ---------------------------------------------------------------------------
# Cas limites du transfert, avec une réponse simulée (instant exact d'arrivée des données)
# ---------------------------------------------------------------------------


class _FakeReply(QObject):
    """Le strict nécessaire de ``QNetworkReply`` pour piloter un :class:`Transfer` à la main."""

    readyRead = Signal()
    finished = Signal()

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._data = data
        self.aborts = 0

    def readAll(self):
        data, self._data = self._data, b""
        return QByteArray(data)

    def abort(self):
        self.aborts += 1

    def header(self, _header):
        return None

    def attribute(self, _attribute):
        return 200

    def rawHeaderPairs(self):
        return []

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def errorString(self):
        return ""


def _fake_transfer(tmp_path, data: bytes, max_bytes: int):
    reply = _FakeReply(data)
    transfer = Transfer(reply, max_bytes=max_bytes, idle_timeout_ms=5000, total_timeout_ms=None,
                        destination=tmp_path / "file.part", expected_size=max_bytes)
    results: list = []
    transfer.finished.connect(results.append)
    return reply, results


def test_data_over_the_limit_arriving_with_the_end_finishes_exactly_once(qapp, tmp_path):
    reply, results = _fake_transfer(tmp_path, b"x" * 20, max_bytes=10)
    reply.finished.emit()                          # tout arrive d'un coup, au moment de « finished »
    assert len(results) == 1, "un seul résultat, même quand la dernière lecture dépasse la limite"
    assert results[0].error.kind is UpdateErrorKind.TOO_LARGE
    assert not (tmp_path / "file.part").exists()


def test_a_disk_error_while_finishing_the_file_is_reported_and_the_file_removed(qapp, tmp_path, monkeypatch):
    def failing_fsync(_fd):
        raise OSError("disque plein")

    monkeypatch.setattr(update_service.os, "fsync", failing_fsync)
    reply, results = _fake_transfer(tmp_path, b"data", max_bytes=4)
    reply.finished.emit()
    assert len(results) == 1
    assert results[0].error.kind is UpdateErrorKind.DISK and results[0].path is None
    assert not (tmp_path / "file.part").exists()
