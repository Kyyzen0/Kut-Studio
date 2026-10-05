"""Outil de publication (``python -m tools.release``) : tag, archives des trois systèmes, empreintes, notes, build.

Les archives Windows et Linux sont construites pour de vrai (zipfile, tarfile) ; l'archive macOS délègue à ``ditto``
et n'est construite que sur macOS. Rien n'est publié : la publication n'existe que dans le workflow, sur un tag.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import build
from core.app_version import APP_VERSION
from core.release_assets import CHECKSUMS_FILE, RELEASE_TARGETS, Architecture, OperatingSystem, Target, parse_checksums
from tools.release import __main__ as cli
from tools.release.packaging import (
    ReleaseError,
    check_tag,
    create_archive,
    expect_target,
    release_notes,
    safe_output_value,
    verify_release_directory,
    write_checksums,
)

MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
WIN_X64 = Target(OperatingSystem.WINDOWS, Architecture.X64)
LINUX_X64 = Target(OperatingSystem.LINUX, Architecture.X64)


# ---------------------------------------------------------------------------
# Tag et version
# ---------------------------------------------------------------------------


def test_a_tag_equal_to_the_application_version_is_published():
    result = check_tag("refs/tags/v1.2.3", "1.2.3")
    assert (result.version, result.prerelease, result.publish) == ("1.2.3", False, True)
    beta = check_tag("refs/tags/v2.0.0-beta.1", "2.0.0-beta.1")
    assert beta.prerelease and beta.publish


@pytest.mark.parametrize("ref", ["refs/tags/v1.2.4", "refs/tags/1.2.3", "refs/tags/v1.2.3-rc.1", "refs/tags/v01.2.3"])
def test_a_tag_different_from_the_application_version_stops_everything(ref):
    with pytest.raises(ReleaseError, match="APP_VERSION"):
        check_tag(ref, "1.2.3")


def test_a_manual_run_on_a_branch_builds_without_publishing():
    result = check_tag("refs/heads/main", "1.2.3")
    assert not result.publish
    assert check_tag("", APP_VERSION).version == APP_VERSION


def test_build_metadata_in_the_application_version_is_refused():
    with pytest.raises(ReleaseError):
        check_tag("refs/tags/v1.2.3", "1.2.3+local")


def test_the_check_tag_command_writes_github_outputs(tmp_path, monkeypatch):
    output = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert cli.main(["check-tag", "--ref", f"refs/tags/v{APP_VERSION}"]) == 0
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"version={APP_VERSION}", "prerelease=false", "publish=true"]
    assert cli.main(["check-tag", "--ref", "refs/tags/v999.0.0"]) == 1


def test_output_values_cannot_inject_lines():
    assert safe_output_value("1.2.3-beta.1") == "1.2.3-beta.1"
    with pytest.raises(ReleaseError):
        safe_output_value("1.2.3\npublish=true")


def test_the_build_machine_must_be_the_expected_target():
    assert expect_target("macos-arm64", MAC_ARM) == MAC_ARM
    with pytest.raises(ReleaseError, match="macos-x64"):
        expect_target("macos-arm64", Target(OperatingSystem.MACOS, Architecture.X64))
    with pytest.raises(ReleaseError, match="inconnue"):
        expect_target("linux-x64", None)


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def _fake_onedir(dist: Path) -> Path:
    folder = dist / "Kut-Studio"
    (folder / "_internal").mkdir(parents=True)
    executable = folder / "Kut-Studio"
    executable.write_bytes(b"\x7fELF fake")
    executable.chmod(0o755)
    (folder / "_internal" / "lib.so").write_bytes(b"lib")
    (folder / "_internal" / "link.so").symlink_to("lib.so")
    return folder


def test_the_linux_archive_keeps_execute_permissions_and_symlinks(tmp_path):
    _fake_onedir(tmp_path / "dist")
    archive = create_archive(tmp_path / "dist", tmp_path / "out", "1.2.3", LINUX_X64)
    assert archive.name == "Kut-Studio-1.2.3-linux-x64.tar.gz"
    with tarfile.open(archive) as bundle:
        members = {member.name: member for member in bundle.getmembers()}
    assert members["Kut-Studio/Kut-Studio"].mode & stat.S_IXUSR
    assert members["Kut-Studio/_internal/link.so"].issym()


def test_the_windows_archive_contains_the_application_folder(tmp_path):
    folder = tmp_path / "dist" / "Kut-Studio"
    folder.mkdir(parents=True)
    (folder / "Kut-Studio.exe").write_bytes(b"MZ")
    (folder / "_internal").mkdir()
    (folder / "_internal" / "python311.dll").write_bytes(b"dll")
    archive = create_archive(tmp_path / "dist", tmp_path / "out", "1.2.3", WIN_X64)
    assert archive.name == "Kut-Studio-1.2.3-windows-x64.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == ["Kut-Studio/Kut-Studio.exe", "Kut-Studio/_internal/python311.dll"]


def test_a_missing_build_is_reported(tmp_path):
    with pytest.raises(ReleaseError, match="introuvable"):
        create_archive(tmp_path / "dist", tmp_path / "out", "1.2.3", LINUX_X64)


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto (archive macOS) n'existe que sur macOS")
def test_the_macos_archive_is_made_with_ditto_and_keeps_symlinks(tmp_path):
    app = tmp_path / "dist" / "Kut-Studio.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "MacOS" / "Kut-Studio").write_bytes(b"bin")
    (app / "Frameworks").mkdir()
    (app / "Frameworks" / "Current").symlink_to("MacOS")
    archive = create_archive(tmp_path / "dist", tmp_path / "out", "1.2.3", MAC_ARM)
    assert archive.name == "Kut-Studio-1.2.3-macos-arm64.zip"
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo("Kut-Studio.app/Contents/Frameworks/Current")
        assert stat.S_ISLNK(info.external_attr >> 16), "ditto conserve les liens (zipfile les aurait suivis)"


# ---------------------------------------------------------------------------
# Empreintes et vérification avant publication
# ---------------------------------------------------------------------------


def _release_dir(tmp_path: Path, version: str = "1.2.3") -> Path:
    folder = tmp_path / "release-assets"
    folder.mkdir()
    for target in RELEASE_TARGETS:
        from core.release_assets import asset_name

        (folder / asset_name(version, target)).write_bytes(target.slug.encode() * 100)
    return folder


def test_checksums_are_written_in_the_convention_and_read_back_by_the_application(tmp_path):
    folder = _release_dir(tmp_path)
    (folder / "README.txt").write_text("pas un paquet", encoding="utf-8")
    path = write_checksums(folder)
    entries = parse_checksums(path.read_text(encoding="utf-8"))
    assert sorted(entries) == sorted(p.name for p in folder.iterdir() if p.name.startswith("Kut-Studio-"))
    for name, digest in entries.items():
        assert digest == hashlib.sha256((folder / name).read_bytes()).hexdigest()


def test_a_complete_release_directory_passes_verification(tmp_path):
    folder = _release_dir(tmp_path)
    write_checksums(folder)
    assert verify_release_directory(folder, "1.2.3") == []
    assert cli.main(["verify", str(folder), "--version", "1.2.3"]) == 0


def test_verification_refuses_every_inconsistency(tmp_path):
    folder = _release_dir(tmp_path)
    assert "SHA256SUMS.txt manquant" in verify_release_directory(folder, "1.2.3")
    write_checksums(folder)
    (folder / "Kut-Studio-1.2.3-linux-x64.tar.gz").write_bytes(b"modifie apres empreinte")
    (folder / "Kut-Studio-1.2.3-windows-x64.zip").unlink()
    (folder / "installeur.exe").write_bytes(b"?")
    problems = verify_release_directory(folder, "1.2.3")
    assert "empreinte fausse pour Kut-Studio-1.2.3-linux-x64.tar.gz" in problems
    assert "paquet manquant pour windows-x64" in problems
    assert "fichier hors convention : installeur.exe" in problems
    assert f"{CHECKSUMS_FILE} cite un fichier absent : Kut-Studio-1.2.3-windows-x64.zip" in problems
    assert any("version" in problem for problem in verify_release_directory(folder, "1.2.4"))
    assert cli.main(["verify", str(folder), "--version", "1.2.3"]) == 1


def test_an_extra_target_is_refused(tmp_path):
    folder = _release_dir(tmp_path)
    (folder / "Kut-Studio-1.2.3-linux-arm64.tar.gz").write_bytes(b"arm")
    write_checksums(folder)
    assert "cible inattendue : linux-arm64" in verify_release_directory(folder, "1.2.3")


# ---------------------------------------------------------------------------
# Notes de release
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,phrase", [
    ("notarized", "notarisé par Apple"),
    ("signed", "**non notarisé**"),
    ("adhoc", "**non signé et non notarisé**"),
])
def test_release_notes_state_the_real_macos_signature(status, phrase):
    notes = release_notes("1.2.3", status, "## What's Changed\n* PR")
    assert phrase in notes
    assert "Kut-Studio-1.2.3-macos-arm64.zip" in notes and "SHA256SUMS.txt" in notes
    assert "pas qui l'a publié" in notes, "l'empreinte n'est pas présentée comme une preuve d'authenticité"
    assert notes.rstrip().endswith("* PR")


def test_release_notes_refuse_an_unknown_signing_status(tmp_path):
    with pytest.raises(ReleaseError):
        release_notes("1.2.3", "probably-fine")
    assert cli.main(["notes", "--signing", "adhoc", "--version", "1.2.3", "--out", str(tmp_path / "n.md")]) == 0
    assert "Kut-Studio 1.2.3" in (tmp_path / "n.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# build.py : version dans Info.plist, signature
# ---------------------------------------------------------------------------


def test_the_macos_bundle_gets_the_application_version_and_is_resigned(tmp_path):
    app = tmp_path / "Kut-Studio.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": "0.0.0", "CFBundleIdentifier": "com.kutstudio.app"}, stream)
    commands: list[list[str]] = []
    build.finalize_macos_bundle(app, version="2.0.0-beta.3", run=lambda command: commands.append(list(command)))
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleShortVersionString"] == "2.0.0-beta.3"
    assert info["CFBundleVersion"] == "2.0.0"
    assert info["CFBundleIdentifier"] == "com.kutstudio.app"
    assert "voice-over" in info["NSMicrophoneUsageDescription"]
    assert commands == [
        ["codesign", "--force", "--sign", "-", str(app)],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
    ]


def test_a_developer_id_identity_signs_with_the_hardened_runtime_and_entitlements(tmp_path):
    identity = "Developer ID Application: Test (TEAM123456)"
    command = build.build_command(platform_name="darwin", environment={build.CODESIGN_IDENTITY_ENV: identity})
    assert command[command.index("--codesign-identity") + 1] == identity
    assert command[command.index("--osx-entitlements-file") + 1] == str(build.ENTITLEMENTS)
    assert build.ENTITLEMENTS.is_file()
    resign = build.codesign_command(Path("Kut-Studio.app"), identity, build.ENTITLEMENTS)
    assert resign[:4] == ["codesign", "--force", "--sign", identity]
    assert "--options" in resign and resign[resign.index("--options") + 1] == "runtime" and "--timestamp" in resign
    assert "--codesign-identity" not in build.build_command(platform_name="darwin", environment={})
    assert "--codesign-identity" not in build.build_command(
        platform_name="linux", environment={build.CODESIGN_IDENTITY_ENV: identity})


def test_the_entitlements_are_minimal_and_valid():
    with build.ENTITLEMENTS.open("rb") as stream:
        entitlements = plistlib.load(stream)
    assert entitlements == {
        "com.apple.security.device.audio-input": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
    }
    assert "com.apple.security.cs.disable-library-validation" not in entitlements


def test_the_signing_script_is_valid_bash_and_never_prints_secrets():
    script = Path(build.ROOT) / "tools" / "release" / "macos_signing.sh"
    assert os.access(script, os.X_OK) or sys.platform == "win32"
    text = script.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    for secret in ("MACOS_CERTIFICATE_P12_BASE64", "MACOS_CERTIFICATE_PASSWORD", "APPLE_API_KEY_P8_BASE64"):
        assert f'echo "${secret}' not in text and f"echo ${secret}" not in text
    assert "set -x" not in text, "un mode trace afficherait les secrets"
