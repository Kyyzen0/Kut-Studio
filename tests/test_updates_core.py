"""Règles des mises à jour (``core.updates``) sur des réponses GitHub simulées : aucun réseau.

Lecture sans confiance de l'API, choix de la version (préversions, version ignorée, automatique / manuelle), choix
exact du paquet, empreintes, limites GitHub, contexte d'installation des trois systèmes, marque « téléchargé
d'Internet ».
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path, PureWindowsPath

import pytest

from core.app_version import GITHUB_REPOSITORY
from core.release_assets import CHECKSUMS_FILE, Architecture, OperatingSystem, Target, format_checksums
from core.updates import (
    AUTO_CHECK_INTERVAL_SECONDS,
    MAX_PACKAGE_BYTES,
    QUARANTINE_ATTRIBUTE,
    CheckMode,
    InstallKind,
    ReleaseAsset,
    UpdateError,
    UpdateErrorKind,
    auto_check_due,
    automatic_checks_allowed,
    coerce_skipped_version,
    evaluate_releases,
    expected_checksum,
    http_failure,
    install_context,
    macos_extended_attribute,
    mark_as_downloaded,
    parse_releases,
    quarantine_value,
    release_page_url,
    releases_api_url,
    source_revision,
    user_agent,
    verify_package,
    zone_identifier_text,
)
from core.versioning import Version

MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
MAC_X64 = Target(OperatingSystem.MACOS, Architecture.X64)
WIN_X64 = Target(OperatingSystem.WINDOWS, Architecture.X64)
LINUX_X64 = Target(OperatingSystem.LINUX, Architecture.X64)
PREFIX = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/"


def asset(name: str, *, tag: str, size: int = 1000, digest: str | None = None, **extra) -> dict:
    payload = {"name": name, "size": size, "browser_download_url": f"{PREFIX}{tag}/{name}", "state": "uploaded"}
    if digest is not None:
        payload["digest"] = f"sha256:{digest}"
    payload.update(extra)
    return payload


def release(tag: str, *, prerelease: bool = False, draft: bool = False, targets=(MAC_ARM, WIN_X64, LINUX_X64),
            checksums: bool = True, body: str = "Notes", **extra) -> dict:
    version = tag.removeprefix("v")
    ext = {OperatingSystem.MACOS: "zip", OperatingSystem.WINDOWS: "zip", OperatingSystem.LINUX: "tar.gz"}
    assets = [asset(f"Kut-Studio-{version}-{t.slug}.{ext[t.system]}", tag=tag) for t in targets]
    if checksums:
        assets.append(asset(CHECKSUMS_FILE, tag=tag, size=300))
    payload = {
        "tag_name": tag, "name": f"Kut-Studio {version}", "body": body, "draft": draft, "prerelease": prerelease,
        "published_at": "2026-10-05T12:00:00Z", "html_url": "https://evil.example/ignored", "assets": assets,
    }
    payload.update(extra)
    return payload


def evaluate(payload, *, current="0.1.0", target=MAC_ARM, prereleases=False, skipped=None, mode=CheckMode.AUTOMATIC):
    return evaluate_releases(
        parse_releases(payload),
        current=Version.parse(current),
        target=target,
        include_prereleases=prereleases,
        skipped_version=Version.parse(skipped) if skipped else None,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# Lecture de la réponse GitHub
# ---------------------------------------------------------------------------


def test_a_response_that_is_not_a_list_is_an_invalid_response():
    for payload in ({"message": "Not Found"}, None, "releases", 42):
        with pytest.raises(UpdateError) as caught:
            parse_releases(payload)
        assert caught.value.kind is UpdateErrorKind.INVALID_RESPONSE


def test_drafts_bad_tags_and_malformed_entries_are_skipped_one_by_one():
    payload = [
        release("v0.2.0"),
        release("v0.3.0", draft=True),
        release("nightly"),
        release("v0.4"),
        release("v0.5.0+build.1"),
        {"tag_name": "v0.6.0"},                                 # sans « draft » ni « prerelease »
        release("v0.7.0", prerelease="yes"),
        "pas un objet",
        None,
    ]
    assert [str(item.version) for item in parse_releases(payload)] == ["0.2.0"]


def test_assets_outside_the_repository_releases_or_badly_typed_are_ignored():
    good = asset("Kut-Studio-0.2.0-macos-arm64.zip", tag="v0.2.0")
    payload = [release("v0.2.0", targets=())]
    payload[0]["assets"] = [
        good,
        {**good, "browser_download_url": "https://evil.example/Kut-Studio-0.2.0-macos-arm64.zip"},
        {**good, "browser_download_url": "http://github.com/" + GITHUB_REPOSITORY
            + "/releases/download/v0.2.0/Kut-Studio-0.2.0-macos-arm64.zip"},
        {**good, "browser_download_url": f"{PREFIX}v0.2.0/autre-nom.zip"},
        {**good, "browser_download_url": f"{PREFIX}v0.2.0/sous/dossier.zip"},
        {**good, "size": "1000"},
        {**good, "size": True},
        {**good, "size": -1},
        {**good, "state": "open"},                              # envoi interrompu côté GitHub
        "pas un objet",
    ]
    (parsed,) = parse_releases(payload)
    assert parsed.assets == (ReleaseAsset(good["name"], good["browser_download_url"], 1000),)


def test_the_release_page_is_built_never_read_from_the_response():
    (parsed,) = parse_releases([release("v0.2.0")])
    assert parsed.page_url == release_page_url("v0.2.0") == f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/v0.2.0"


def test_a_github_digest_is_kept_only_when_well_formed():
    tag = "v0.2.0"
    payload = [release(tag, targets=())]
    payload[0]["assets"] = [
        asset("Kut-Studio-0.2.0-macos-arm64.zip", tag=tag, digest="AB" * 32),
        asset("Kut-Studio-0.2.0-windows-x64.zip", tag=tag, digest="sha1-ish"),
    ]
    (parsed,) = parse_releases(payload)
    assert [item.digest for item in parsed.assets] == ["ab" * 32, None]


def test_long_notes_are_truncated_and_a_semver_prerelease_counts_even_without_the_github_flag():
    (parsed,) = parse_releases([release("v1.0.0-beta.1", body="x" * 100_000)])
    assert parsed.prerelease
    assert len(parsed.notes) == 40_000


# ---------------------------------------------------------------------------
# Choix de la version
# ---------------------------------------------------------------------------


def test_the_newest_newer_stable_release_is_offered_with_the_exact_package():
    result = evaluate([release("v0.2.0"), release("v0.10.0"), release("v0.9.0"), release("v0.1.0")])
    assert result.offer is not None
    assert str(result.offer.version) == "0.10.0"
    assert result.offer.package is not None and result.offer.package.name == "Kut-Studio-0.10.0-macos-arm64.zip"
    assert result.offer.checksums is not None
    assert result.offer.can_download


def test_no_newer_release_means_up_to_date_and_never_an_older_one():
    result = evaluate([release("v0.1.0"), release("v0.0.9")], current="0.1.0")
    assert result.up_to_date
    assert result.offer is None and result.error is None


def test_a_stable_version_is_not_offered_prereleases_by_default():
    payload = [release("v0.2.0"), release("v0.3.0-beta.1", prerelease=True)]
    result = evaluate(payload)
    assert str(result.offer.version) == "0.2.0"
    assert str(result.hidden_prerelease) == "0.3.0-beta.1"
    only_beta = evaluate([release("v0.3.0-beta.1", prerelease=True)])
    assert only_beta.up_to_date and str(only_beta.hidden_prerelease) == "0.3.0-beta.1"


def test_prereleases_are_offered_on_request_or_to_someone_already_on_a_prerelease():
    payload = [release("v0.2.0"), release("v0.3.0-beta.1", prerelease=True)]
    assert str(evaluate(payload, prereleases=True).offer.version) == "0.3.0-beta.1"
    on_beta = evaluate([release("v0.3.0-beta.2", prerelease=True)], current="0.3.0-beta.1")
    assert str(on_beta.offer.version) == "0.3.0-beta.2"
    final = evaluate([release("v0.3.0"), release("v0.3.0-rc.1", prerelease=True)], current="0.3.0-beta.1")
    assert str(final.offer.version) == "0.3.0"


def test_a_skipped_version_is_silent_automatically_but_always_found_manually():
    payload = [release("v0.2.0")]
    assert evaluate(payload, skipped="0.2.0", mode=CheckMode.AUTOMATIC).up_to_date
    manual = evaluate(payload, skipped="0.2.0", mode=CheckMode.MANUAL)
    assert manual.offer is not None and manual.offer.skipped
    assert str(manual.offer.version) == "0.2.0"


def test_a_release_newer_than_the_skipped_one_is_offered_again():
    result = evaluate([release("v0.2.0"), release("v0.2.1")], skipped="0.2.0")
    assert str(result.offer.version) == "0.2.1"
    assert not result.offer.skipped


def test_an_intel_mac_is_never_offered_the_apple_silicon_package():
    result = evaluate([release("v0.2.0", targets=(MAC_ARM, WIN_X64, LINUX_X64))], target=MAC_X64)
    assert result.offer is not None, "la version existe : on l'annonce…"
    assert result.offer.package is None, "…sans proposer un paquet qui ne démarrerait pas"
    assert not result.offer.can_download


@pytest.mark.parametrize("target,expected", [
    (WIN_X64, "Kut-Studio-0.2.0-windows-x64.zip"),
    (LINUX_X64, "Kut-Studio-0.2.0-linux-x64.tar.gz"),
    (Target(OperatingSystem.LINUX, Architecture.ARM64), None),
    (None, None),
])
def test_each_system_gets_only_its_own_package(target, expected):
    offer = evaluate([release("v0.2.0")], target=target).offer
    assert (offer.package.name if offer.package else None) == expected


def test_a_package_named_for_another_version_does_not_count():
    payload = [release("v0.2.0", targets=())]
    payload[0]["assets"].append(asset("Kut-Studio-0.1.9-macos-arm64.zip", tag="v0.2.0"))
    assert evaluate(payload).offer.package is None


def test_without_checksums_or_with_an_absurd_size_the_builtin_download_is_disabled():
    no_sums = evaluate([release("v0.2.0", checksums=False)]).offer
    assert no_sums.package is not None and no_sums.checksums is None and not no_sums.can_download
    payload = [release("v0.2.0")]
    payload[0]["assets"][0]["size"] = MAX_PACKAGE_BYTES + 1
    assert not evaluate(payload).offer.can_download
    payload[0]["assets"][0]["size"] = 0
    assert not evaluate(payload).offer.can_download


# ---------------------------------------------------------------------------
# Limites GitHub et réponses en erreur
# ---------------------------------------------------------------------------


def test_primary_rate_limit_reports_the_reset_time():
    error = http_failure(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000000"}, now=1_700_000_000)
    assert error.kind is UpdateErrorKind.RATE_LIMITED
    assert error.retry_at == 1_800_000_000
    assert error.status == 403


def test_secondary_rate_limit_uses_retry_after_and_429_is_always_a_limit():
    error = http_failure(403, {"retry-after": "60"}, now=1000.0)
    assert error.kind is UpdateErrorKind.RATE_LIMITED and error.retry_at == 1060.0
    assert http_failure(429, {}, now=0).kind is UpdateErrorKind.RATE_LIMITED
    assert http_failure(429, {}, now=0).retry_at is None


def test_other_failures_are_plain_http_errors():
    assert http_failure(403, {"X-RateLimit-Remaining": "12"}).kind is UpdateErrorKind.HTTP
    error = http_failure(502, {})
    assert error.kind is UpdateErrorKind.HTTP and error.status == 502


def test_the_request_identifies_kut_studio_and_asks_for_the_release_list():
    assert user_agent().startswith("Kut-Studio/")
    assert releases_api_url("o/r", "https://api.github.com/") == "https://api.github.com/repos/o/r/releases?per_page=30"


# ---------------------------------------------------------------------------
# Empreintes et vérification du paquet
# ---------------------------------------------------------------------------

PACKAGE_BYTES = b"paquet de test" * 100
PACKAGE_SHA = hashlib.sha256(PACKAGE_BYTES).hexdigest()


def package(digest: str | None = None, size: int = len(PACKAGE_BYTES)) -> ReleaseAsset:
    name = "Kut-Studio-0.2.0-macos-arm64.zip"
    return ReleaseAsset(name, f"{PREFIX}v0.2.0/{name}", size, digest)


def test_the_expected_checksum_comes_from_sha256sums():
    sums = format_checksums({package().name: PACKAGE_SHA, "autre.zip": "0" * 64})
    assert expected_checksum(sums, package()) == PACKAGE_SHA


def test_a_package_missing_from_sha256sums_is_refused():
    with pytest.raises(UpdateError) as caught:
        expected_checksum(format_checksums({"autre.zip": "0" * 64}), package())
    assert caught.value.kind is UpdateErrorKind.CHECKSUM_MISSING


def test_an_unreadable_sha256sums_is_an_invalid_response():
    with pytest.raises(UpdateError) as caught:
        expected_checksum("ceci n'est pas un fichier d'empreintes", package())
    assert caught.value.kind is UpdateErrorKind.INVALID_RESPONSE


def test_sha256sums_contradicting_github_digest_is_refused_before_downloading():
    sums = format_checksums({package().name: PACKAGE_SHA})
    with pytest.raises(UpdateError) as caught:
        expected_checksum(sums, package(digest="f" * 64))
    assert caught.value.kind is UpdateErrorKind.CHECKSUM_MISMATCH
    assert expected_checksum(sums, package(digest=PACKAGE_SHA)) == PACKAGE_SHA


def test_a_verified_package_passes():
    verify_package(size=len(PACKAGE_BYTES), sha256=PACKAGE_SHA.upper(), package=package(), expected_sha256=PACKAGE_SHA)


def test_a_truncated_package_fails_on_size():
    with pytest.raises(UpdateError) as caught:
        verify_package(size=len(PACKAGE_BYTES) - 1, sha256=PACKAGE_SHA, package=package(), expected_sha256=PACKAGE_SHA)
    assert caught.value.kind is UpdateErrorKind.SIZE_MISMATCH


def test_a_package_with_a_different_checksum_fails():
    with pytest.raises(UpdateError) as caught:
        verify_package(size=len(PACKAGE_BYTES), sha256="0" * 64, package=package(), expected_sha256=PACKAGE_SHA)
    assert caught.value.kind is UpdateErrorKind.CHECKSUM_MISMATCH
    with pytest.raises(UpdateError) as caught:
        verify_package(size=len(PACKAGE_BYTES), sha256=PACKAGE_SHA, package=package(digest="e" * 64),
                       expected_sha256=PACKAGE_SHA)
    assert caught.value.kind is UpdateErrorKind.CHECKSUM_MISMATCH


# ---------------------------------------------------------------------------
# Rythme des vérifications automatiques et préférences
# ---------------------------------------------------------------------------


def test_automatic_checks_run_at_most_once_a_day_and_never_when_disabled():
    now = 1_000_000.0
    assert auto_check_due(enabled=True, last_check=0, now=now)
    assert not auto_check_due(enabled=True, last_check=now - 3600, now=now)
    assert auto_check_due(enabled=True, last_check=now - AUTO_CHECK_INTERVAL_SECONDS, now=now)
    assert auto_check_due(enabled=True, last_check=now + 3600, now=now), "horloge revenue en arrière"
    assert not auto_check_due(enabled=False, last_check=0, now=now)


@pytest.mark.parametrize("value,allowed", [("", True), ("on", True), ("off", False), ("0", False), ("FALSE", False)])
def test_the_environment_switch_only_disables_automatic_checks(value, allowed):
    assert automatic_checks_allowed({"KUT_STUDIO_UPDATE_CHECK": value}) is allowed
    assert automatic_checks_allowed({}) is True


def test_the_test_suite_itself_never_checks_automatically():
    assert not automatic_checks_allowed(), "tests/conftest.py doit couper la recherche automatique"


@pytest.mark.parametrize("value,expected", [("0.2.0", "0.2.0"), ("v0.2.0", "0.2.0"), ("n'importe quoi", ""),
                                            (None, ""), (3, "")])
def test_a_skipped_version_read_from_disk_is_validated(value, expected):
    assert coerce_skipped_version(value) == expected


def test_update_preferences_round_trip_and_survive_corruption(tmp_path):
    import json

    from core.user_settings import UserSettings, load_user_settings, save_user_settings

    save_user_settings(UserSettings(check_updates=False, include_prereleases=True,
                                    skipped_update_version="v0.3.0", last_update_check=1234.5), tmp_path)
    loaded = load_user_settings(tmp_path)
    assert (loaded.check_updates, loaded.include_prereleases) == (False, True)
    assert (loaded.skipped_update_version, loaded.last_update_check) == ("0.3.0", 1234.5)
    path = tmp_path / "user_settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(skipped_update_version="pas une version", last_update_check=-5,
                check_updates="no")
    path.write_text(json.dumps(data), encoding="utf-8")
    corrupted = load_user_settings(tmp_path)
    assert corrupted.skipped_update_version == ""
    assert corrupted.last_update_check == 0.0
    assert corrupted.check_updates is False
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "user_settings.json").write_text(json.dumps({"theme_mode": "dark"}), encoding="utf-8")
    old = load_user_settings(legacy)
    assert old.check_updates is True and old.include_prereleases is False and old.skipped_update_version == ""


# ---------------------------------------------------------------------------
# Contexte d'installation (trois systèmes et sources)
# ---------------------------------------------------------------------------


def test_running_from_source_is_never_treated_as_an_installed_package(tmp_path):
    context = install_context(frozen=False, source_root=tmp_path)
    assert context.kind is InstallKind.SOURCE and context.is_source
    assert context.location == tmp_path
    assert context.revision is None


def test_a_frozen_macos_app_is_located_by_its_bundle_and_translocation_is_detected():
    context = install_context(frozen=True, platform_name="darwin",
                              executable="/Applications/Kut-Studio.app/Contents/MacOS/Kut-Studio")
    assert context.kind is InstallKind.MACOS_APP
    assert str(context.location) == "/Applications/Kut-Studio.app"
    assert not context.translocated
    moved = install_context(frozen=True, platform_name="darwin", executable=(
        "/private/var/folders/x/AppTranslocation/1234/d/Kut-Studio.app/Contents/MacOS/Kut-Studio"))
    assert moved.translocated


def test_frozen_windows_and_linux_folders():
    windows = install_context(frozen=True, platform_name="win32",
                              executable=r"C:\Users\Ana\Apps\Kut-Studio\Kut-Studio.exe")
    assert windows.kind is InstallKind.WINDOWS_FOLDER
    assert windows.location == PureWindowsPath(r"C:\Users\Ana\Apps\Kut-Studio")
    linux = install_context(frozen=True, platform_name="linux", executable="/opt/Kut-Studio/Kut-Studio")
    assert linux.kind is InstallKind.LINUX_FOLDER and str(linux.location) == "/opt/Kut-Studio"


def _fake_repository(root: Path, *, worktree: bool) -> str:
    commit = "1234567890abcdef1234567890abcdef12345678"
    common = root / "common.git"
    (common / "refs" / "heads").mkdir(parents=True)
    if worktree:
        gitdir = common / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        (gitdir / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
        (common / "packed-refs").write_text(f"# pack-refs\n{commit} refs/heads/feature\n", encoding="utf-8")
        checkout = root / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        return str(checkout)
    (common / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (common / "refs" / "heads" / "main").write_text(commit + "\n", encoding="utf-8")
    checkout = root / "plain"
    checkout.mkdir()
    common.rename(checkout / ".git")
    return str(checkout)


@pytest.mark.parametrize("worktree", [False, True])
def test_the_source_commit_is_read_without_running_git(tmp_path, worktree):
    checkout = Path(_fake_repository(tmp_path, worktree=worktree))
    assert source_revision(checkout) == "1234567890ab"
    assert install_context(frozen=False, source_root=checkout).revision == "1234567890ab"


def test_a_detached_or_damaged_git_directory_is_handled(tmp_path):
    (tmp_path / ".git").mkdir()
    assert source_revision(tmp_path) is None                         # pas de HEAD
    (tmp_path / ".git" / "HEAD").write_text("abcdef0123456789abcdef0123456789abcdef01\n", encoding="utf-8")
    assert source_revision(tmp_path) == "abcdef012345"                # HEAD détachée
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").write_text("pas un gitdir", encoding="utf-8")
    assert source_revision(other) is None


# ---------------------------------------------------------------------------
# Marque « téléchargé d'Internet »
# ---------------------------------------------------------------------------


def test_the_marks_have_the_formats_browsers_write():
    assert quarantine_value(0x5F000000) == b"0081;5f000000;Kut-Studio;"
    assert zone_identifier_text("https://x/y.zip") == "[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://x/y.zip\r\n"


def test_linux_has_no_download_mark(tmp_path):
    path = tmp_path / "Kut-Studio-0.2.0-linux-x64.tar.gz"
    path.write_bytes(b"x")
    assert mark_as_downloaded(path, url="https://example/x", platform_name="linux") is False


def test_windows_writes_the_zone_identifier_stream(tmp_path, monkeypatch):
    """Simulé hors Windows : on vérifie le chemin du flux alternatif NTFS et son contenu."""
    written: dict[str, str] = {}

    class _Stream:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, text):
            written[self.name] = text

    def fake_open(name, mode="r", **kwargs):
        assert mode == "w" and kwargs.get("newline") == ""
        return _Stream(name)

    monkeypatch.setattr("builtins.open", fake_open)
    path = str(tmp_path / "Kut-Studio-0.2.0-windows-x64.zip")
    assert mark_as_downloaded(path, url="https://github.com/x.zip", platform_name="win32") is True
    assert written == {f"{path}:Zone.Identifier": zone_identifier_text("https://github.com/x.zip")}


@pytest.mark.skipif(sys.platform != "win32", reason="flux alternatif NTFS réel : Windows seulement")
def test_windows_zone_identifier_on_a_real_ntfs_volume(tmp_path):
    path = tmp_path / "Kut-Studio-0.2.0-windows-x64.zip"
    path.write_bytes(b"x")
    assert mark_as_downloaded(path, url="https://github.com/x.zip")
    with open(f"{path}:Zone.Identifier", encoding="utf-8") as stream:
        assert "ZoneId=3" in stream.read()


@pytest.mark.skipif(sys.platform != "darwin", reason="attribut étendu réel : macOS seulement")
def test_macos_quarantine_flag_is_really_set(tmp_path):
    path = tmp_path / "Kut-Studio-0.2.0-macos-arm64.zip"
    path.write_bytes(b"x")
    assert macos_extended_attribute(path, QUARANTINE_ATTRIBUTE) is None
    assert mark_as_downloaded(path, url="https://github.com/x.zip", now=0x5F000000) is True
    assert macos_extended_attribute(path, QUARANTINE_ATTRIBUTE) == b"0081;5f000000;Kut-Studio;"
