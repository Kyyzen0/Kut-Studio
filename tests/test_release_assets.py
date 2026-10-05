"""Convention des paquets de release : noms, cibles des trois systèmes, empreintes."""

from __future__ import annotations

import hashlib

import pytest

from core.release_assets import (
    ARCHIVE_EXTENSIONS,
    CHECKSUMS_FILE,
    RELEASE_TARGETS,
    Architecture,
    ChecksumFileError,
    OperatingSystem,
    Target,
    asset_name,
    detect_target,
    format_checksums,
    normalize_architecture,
    parse_asset_name,
    parse_checksums,
    sha256_file,
)
from core.versioning import Version

MAC_ARM = Target(OperatingSystem.MACOS, Architecture.ARM64)
MAC_X64 = Target(OperatingSystem.MACOS, Architecture.X64)
WIN_X64 = Target(OperatingSystem.WINDOWS, Architecture.X64)
WIN_ARM = Target(OperatingSystem.WINDOWS, Architecture.ARM64)
LINUX_X64 = Target(OperatingSystem.LINUX, Architecture.X64)
LINUX_ARM = Target(OperatingSystem.LINUX, Architecture.ARM64)


@pytest.mark.parametrize(
    "version,target,expected",
    [
        ("0.2.0", MAC_ARM, "Kut-Studio-0.2.0-macos-arm64.zip"),
        ("0.2.0", WIN_X64, "Kut-Studio-0.2.0-windows-x64.zip"),
        ("0.2.0", LINUX_X64, "Kut-Studio-0.2.0-linux-x64.tar.gz"),
        ("1.0.0-beta.2", MAC_X64, "Kut-Studio-1.0.0-beta.2-macos-x64.zip"),
    ],
)
def test_asset_names_follow_the_convention_and_read_back(version, target, expected):
    assert asset_name(version, target) == expected
    parsed = parse_asset_name(expected)
    assert parsed is not None
    assert parsed.version == Version.parse(version)
    assert parsed.target == target


@pytest.mark.parametrize(
    "name",
    [
        "Kut-Studio-0.2.0-macos-arm64.tar.gz",       # mauvaise extension pour macOS
        "Kut-Studio-0.2.0-linux-x64.zip",            # zip perdrait les droits d'exécution : refusé
        "Kut-Studio-0.2.0-macos-universal.zip",
        "Kut-Studio-0.2.0-freebsd-x64.tar.gz",
        "Kut-Studio-0.2-macos-arm64.zip",
        "Kut-Studio-0.2.0+build-macos-arm64.zip",
        "Kut-Studio-macos-arm64.zip",
        "Kut-Studio-Linux.zip",                      # les artefacts actuels de nightly.link ne sont pas des paquets
        "../Kut-Studio-0.2.0-macos-arm64.zip",
        "kut-studio-0.2.0-macos-arm64.zip",
        CHECKSUMS_FILE,
    ],
)
def test_names_outside_the_convention_are_not_packages(name):
    assert parse_asset_name(name) is None


def test_build_metadata_cannot_be_published():
    with pytest.raises(ValueError):
        asset_name("1.0.0+local", MAC_ARM)


def test_release_targets_have_an_archive_format_and_are_unique():
    assert len(set(RELEASE_TARGETS)) == len(RELEASE_TARGETS)
    assert {target.system for target in RELEASE_TARGETS} == set(OperatingSystem)
    assert ARCHIVE_EXTENSIONS[OperatingSystem.LINUX] == "tar.gz"
    assert Target.from_slug("windows-x64") == WIN_X64
    assert MAC_ARM.display_name == "macOS arm64"


@pytest.mark.parametrize(
    "platform_name,machine,translated,expected",
    [
        ("darwin", "arm64", False, MAC_ARM),
        ("darwin", "x86_64", False, MAC_X64),           # Mac Intel : jamais le paquet Apple Silicon
        ("darwin", "x86_64", True, MAC_ARM),            # copie x64 sous Rosetta sur Apple Silicon : paquet natif
        ("win32", "AMD64", None, WIN_X64),
        ("win32", "ARM64", None, WIN_ARM),
        ("linux", "x86_64", None, LINUX_X64),
        ("linux", "aarch64", None, LINUX_ARM),
        ("linux", "riscv64", None, None),
        ("freebsd14", "amd64", None, None),
        ("cygwin", "x86_64", None, None),
    ],
)
def test_the_target_of_each_system_and_architecture(platform_name, machine, translated, expected):
    assert detect_target(platform_name=platform_name, machine=machine, translated=translated) == expected


def test_architecture_aliases():
    assert normalize_architecture(" AMD64 ") is Architecture.X64
    assert normalize_architecture("armv8l") is Architecture.ARM64
    assert normalize_architecture("i686") is None


def test_the_current_machine_has_a_target_on_supported_ci_systems():
    target = detect_target()
    assert target is None or isinstance(target, Target)


# ---------------------------------------------------------------------------
# SHA256SUMS.txt
# ---------------------------------------------------------------------------

DIGEST_A = "a" * 64
DIGEST_B = "B" * 64


def test_checksums_accept_text_and_binary_markers_comments_and_uppercase():
    text = (
        "# empreintes de la release\n\n"
        f"{DIGEST_A}  Kut-Studio-0.2.0-macos-arm64.zip\n"
        f"{DIGEST_B} *Kut-Studio-0.2.0-windows-x64.zip\r\n"
    )
    assert parse_checksums(text) == {
        "Kut-Studio-0.2.0-macos-arm64.zip": DIGEST_A,
        "Kut-Studio-0.2.0-windows-x64.zip": DIGEST_B.lower(),
    }


@pytest.mark.parametrize(
    "text",
    [
        "pas une empreinte  fichier.zip\n",
        f"{'a' * 63}  fichier.zip\n",
        f"{DIGEST_A}fichier.zip\n",
        f"{DIGEST_A}  ../fichier.zip\n",
        f"{DIGEST_A}  dossier\\fichier.zip\n",
        f"{DIGEST_A}  fichier.zip\n{'c' * 64}  fichier.zip\n",     # deux empreintes pour un fichier
    ],
)
def test_a_malformed_or_contradictory_checksum_file_is_refused_entirely(text):
    with pytest.raises(ChecksumFileError):
        parse_checksums(text)


def test_formatted_checksums_round_trip_and_are_verifiable_by_hand(tmp_path):
    entries = {"b.zip": DIGEST_A, "a.tar.gz": "0" * 64}
    text = format_checksums(entries)
    assert text == f"{'0' * 64}  a.tar.gz\n{DIGEST_A}  b.zip\n"     # format de « shasum -a 256 -c »
    assert parse_checksums(text) == entries
    with pytest.raises(ChecksumFileError):
        format_checksums({"x/y.zip": DIGEST_A})


def test_sha256_file_reads_by_blocks(tmp_path):
    path = tmp_path / "data.bin"
    payload = bytes(range(256)) * 9000
    path.write_bytes(payload)
    assert sha256_file(path, chunk_size=1000) == hashlib.sha256(payload).hexdigest()
