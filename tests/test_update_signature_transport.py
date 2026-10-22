"""Signature des mises à jour de bout en bout contre le faux GitHub local (ADR-0012).

Release signée -> VERIFIED ; sans fichier ``.sig`` -> ABSENT (l'installation assistée continue) ; signature altérée,
mauvaise clé, ``SHA256SUMS.txt`` altéré après signature -> paquet refusé, rien ne reste sur le disque. Les clés sont
FACTICES (``tests/fake_update_keys.py``).
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fake_update_keys import FAKE_KEY, FAKE_KEYS, OTHER_SEED, sign_with_fake_key
from update_server import Route, UpdateServer

from core.release_assets import CHECKSUMS_FILE, SIGNATURE_FILE, Architecture, OperatingSystem, Target, format_checksums
from core.update_service import HttpClient, UpdateDownloader
from core.update_signature import MAX_SIGNATURE_BYTES, SignatureStatus
from core.updates import CheckMode, ReleaseAsset, UpdateErrorKind, UpdateOffer, evaluate_releases, parse_releases
from core.versioning import Version

MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
REPOSITORY = "owner/kut"
PACKAGE = "Kut-Studio-0.2.0-macos-arm64.zip"
PAYLOAD = bytes(range(256)) * 100
SUMS = format_checksums({PACKAGE: hashlib.sha256(PAYLOAD).hexdigest()})
CURRENT = Version.parse("0.1.0")


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


def _offer(server, *, signature_listed=True, signature_size=100) -> UpdateOffer:
    def asset(name, size):
        return {"name": name, "size": size, "state": "uploaded",
                "browser_download_url": server.url(f"/download/v0.2.0/{name}")}

    assets = [asset(PACKAGE, len(PAYLOAD)), asset(CHECKSUMS_FILE, len(SUMS))]
    if signature_listed:
        assets.append(asset(SIGNATURE_FILE, signature_size))
    payload = [{"tag_name": "v0.2.0", "name": "0.2.0", "body": "", "draft": False, "prerelease": False,
                "published_at": "2026-10-05T12:00:00Z", "assets": assets}]
    releases = parse_releases(json.loads(json.dumps(payload)), repository=REPOSITORY,
                              asset_prefix=server.url("/download/"))
    result = evaluate_releases(releases, current=CURRENT, target=MAC_ARM, include_prereleases=False,
                               skipped_version=None, mode=CheckMode.MANUAL)
    assert result.offer is not None
    return result.offer


def _serve(server, *, sums: bytes = SUMS.encode(), signature: bytes | None = None):
    server.routes[f"/download/v0.2.0/{PACKAGE}"] = Route(body=PAYLOAD)
    server.routes[f"/download/v0.2.0/{CHECKSUMS_FILE}"] = Route(body=sums)
    if signature is not None:
        server.routes[f"/download/v0.2.0/{SIGNATURE_FILE}"] = Route(body=signature)


def _download(qtbot, downloader, offer):
    with qtbot.waitSignal(downloader.finished, timeout=10000) as blocker:
        assert downloader.start(offer)
    return blocker.args[0]


def _downloader(client, tmp_path, **kwargs) -> UpdateDownloader:
    kwargs.setdefault("keys", FAKE_KEYS)
    return UpdateDownloader(client, current=CURRENT, directory=tmp_path / "updates", **kwargs)


def _left(tmp_path):
    folder = tmp_path / "updates"
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def test_the_offer_carries_the_signature_asset(server):
    assert _offer(server).signature.name == SIGNATURE_FILE
    assert _offer(server, signature_listed=False).signature is None


def test_a_signed_release_is_verified(qtbot, server, client, tmp_path):
    _serve(server, signature=sign_with_fake_key(SUMS.encode()))
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error is None and result.path is not None
    assert result.signature_status is SignatureStatus.VERIFIED and result.signature.key_id == FAKE_KEY.key_id
    assert _left(tmp_path) == [PACKAGE]


def test_a_release_without_signature_stays_assisted_and_is_absent_not_an_error(qtbot, server, client, tmp_path):
    _serve(server)
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server, signature_listed=False))
    assert result.error is None and result.path is not None
    assert result.signature_status is SignatureStatus.ABSENT
    assert not server.requested(f"/download/v0.2.0/{SIGNATURE_FILE}")


def test_automatic_installation_can_require_a_signature(qtbot, server, client, tmp_path):
    _serve(server)
    result = _download(qtbot, _downloader(client, tmp_path, require_signature=True),
                       _offer(server, signature_listed=False))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_MISSING and result.path is None
    assert _left(tmp_path) == [] and not server.requested(f"/download/v0.2.0/{PACKAGE}")


def test_an_altered_signature_refuses_the_package_before_downloading_it(qtbot, server, client, tmp_path):
    good = bytearray(sign_with_fake_key(SUMS.encode()))
    good[len(good) - 6] = ord("A") if good[len(good) - 6] != ord("A") else ord("B")
    _serve(server, signature=bytes(good))
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_INVALID and result.path is None
    assert result.signature_status is SignatureStatus.INVALID
    assert _left(tmp_path) == [] and not server.requested(f"/download/v0.2.0/{PACKAGE}")


def test_a_signature_by_an_unknown_key_is_refused_with_its_own_error(qtbot, server, client, tmp_path):
    _serve(server, signature=sign_with_fake_key(SUMS.encode(), OTHER_SEED))
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_UNKNOWN_KEY and _left(tmp_path) == []


def test_sums_changed_after_signing_are_refused(qtbot, server, client, tmp_path):
    signature = sign_with_fake_key(SUMS.encode())
    forged = format_checksums({PACKAGE: "0" * 64}).encode()           # même nom, autre empreinte
    _serve(server, sums=forged, signature=signature)
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_INVALID and _left(tmp_path) == []


def test_an_unreadable_or_oversized_signature_file_is_invalid(qtbot, server, client, tmp_path):
    _serve(server, signature=b"<html>404</html>")
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_INVALID
    _serve(server, signature=b"x")
    big = _offer(server, signature_size=MAX_SIGNATURE_BYTES + 1)
    result = _download(qtbot, _downloader(client, tmp_path), big)
    assert result.error.kind is UpdateErrorKind.SIGNATURE_INVALID


def test_a_listed_signature_that_cannot_be_fetched_is_an_error_not_absent(qtbot, server, client, tmp_path):
    _serve(server)                                                     # .sig annoncé mais introuvable (404)
    result = _download(qtbot, _downloader(client, tmp_path), _offer(server))
    assert result.error is not None and result.error.kind is UpdateErrorKind.HTTP and _left(tmp_path) == []


def test_without_any_configured_key_a_published_signature_is_refused(qtbot, server, client, tmp_path):
    _serve(server, signature=sign_with_fake_key(SUMS.encode()))
    result = _download(qtbot, _downloader(client, tmp_path, keys=()), _offer(server))
    assert result.error.kind is UpdateErrorKind.SIGNATURE_UNKNOWN_KEY


@pytest.mark.parametrize("installed", ["0.2.0", "0.3.0"], ids=["equal", "older"])
def test_an_older_or_equal_version_is_never_downloaded(qtbot, server, client, tmp_path, installed):
    """Protection contre le retour en arrière, aussi pour le chemin de téléchargement / d'installation."""
    _serve(server, signature=sign_with_fake_key(SUMS.encode()))
    downloader = UpdateDownloader(client, current=Version.parse(installed),
                                  directory=tmp_path / "updates", keys=FAKE_KEYS)
    result = _download(qtbot, downloader, _offer(server))
    assert result.error.kind is UpdateErrorKind.NOT_NEWER
    assert server.requests == []
    assert _left(tmp_path) == []


def test_offer_assets_type_is_a_release_asset(server):
    assert isinstance(_offer(server).signature, ReleaseAsset)
