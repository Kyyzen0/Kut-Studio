"""Convention des fichiers de release : noms, systèmes, architectures et empreintes.

Une seule définition, lue des deux côtés : par l'application qui choisit son paquet (:mod:`core.updates`) et par
l'outil de publication qui les nomme et les empreinte (``python -m tools.release``). Une release conforme contient :

* un paquet par cible, nommé ``Kut-Studio-<version>-<système>-<architecture>.<extension>`` :

  ======================  ===========  ==========================================================
  système                 extension    contenu
  ======================  ===========  ==========================================================
  ``macos``               ``zip``      ``Kut-Studio.app`` (archive ``ditto`` : liens et signature)
  ``windows``             ``zip``      dossier ``Kut-Studio`` (``Kut-Studio.exe`` et ses fichiers)
  ``linux``               ``tar.gz``   dossier ``Kut-Studio`` (droits d'exécution conservés)
  ======================  ===========  ==========================================================

  architectures : ``x64`` (x86-64 / AMD64) et ``arm64`` (Apple Silicon, ARM64) ;
* ``SHA256SUMS.txt`` : une ligne ``<sha256 hexadécimal>  <nom du fichier>`` par paquet (format de ``sha256sum`` /
  ``shasum -a 256``, vérifiable à la main avec ``shasum -a 256 -c SHA256SUMS.txt``).

Le choix du paquet est **exact** : même système, même architecture, aucun repli. Un Mac Intel ne se voit jamais
proposer le paquet Apple Silicon (il ne démarrerait pas) ; s'il n'existe pas de paquet ``macos-x64``, l'application
le dit au lieu d'en proposer un autre. Seule exception, voulue : une copie x64 lancée sous Rosetta sur un Mac Apple
Silicon reçoit le paquet ``arm64``, natif pour cette machine.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import os
import platform
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .versioning import Version

ASSET_PREFIX = "Kut-Studio"
CHECKSUMS_FILE = "SHA256SUMS.txt"
MAX_CHECKSUMS_BYTES = 64 * 1024
"""Taille maximale acceptée pour ``SHA256SUMS.txt`` (quelques lignes en pratique)."""


class OperatingSystem(str, Enum):
    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"


class Architecture(str, Enum):
    X64 = "x64"
    ARM64 = "arm64"


ARCHIVE_EXTENSIONS: dict[OperatingSystem, str] = {
    OperatingSystem.MACOS: "zip",
    OperatingSystem.WINDOWS: "zip",
    OperatingSystem.LINUX: "tar.gz",
}

_SYSTEM_NAMES = {OperatingSystem.MACOS: "macOS", OperatingSystem.WINDOWS: "Windows", OperatingSystem.LINUX: "Linux"}
_ARCHITECTURE_NAMES = {Architecture.X64: "x64", Architecture.ARM64: "arm64"}


@dataclass(frozen=True)
class Target:
    """Système et architecture d'un paquet (ou de la machine qui le cherche)."""

    system: OperatingSystem
    architecture: Architecture

    @property
    def slug(self) -> str:
        """``macos-arm64`` : la partie du nom de fichier qui désigne la cible."""
        return f"{self.system.value}-{self.architecture.value}"

    @property
    def display_name(self) -> str:
        """``macOS arm64`` : noms propres, identiques dans les trois langues de l'interface."""
        return f"{_SYSTEM_NAMES[self.system]} {_ARCHITECTURE_NAMES[self.architecture]}"

    @classmethod
    def from_slug(cls, slug: str) -> Target:
        system, _, architecture = slug.partition("-")
        return cls(OperatingSystem(system), Architecture(architecture))


RELEASE_TARGETS: tuple[Target, ...] = (
    Target(OperatingSystem.MACOS, Architecture.ARM64),
    Target(OperatingSystem.WINDOWS, Architecture.X64),
    Target(OperatingSystem.LINUX, Architecture.X64),
)
"""Cibles construites par ``.github/workflows/release.yml`` (le test du workflow vérifie la correspondance)."""


@dataclass(frozen=True)
class AssetName:
    """Nom de paquet lu selon la convention."""

    version: Version
    target: Target


_ASSET_PATTERN = re.compile(
    rf"{re.escape(ASSET_PREFIX)}-(?P<version>.+)-(?P<system>macos|windows|linux)-(?P<arch>x64|arm64)"
    r"\.(?P<extension>zip|tar\.gz)"
)


def asset_name(version: Version | str, target: Target) -> str:
    """Nom conventionnel du paquet de ``target`` pour ``version``."""
    parsed = version if isinstance(version, Version) else Version.parse(version)
    if parsed.build:
        raise ValueError("une version publiée ne porte pas de métadonnées de construction (+…)")
    return f"{ASSET_PREFIX}-{parsed}-{target.slug}.{ARCHIVE_EXTENSIONS[target.system]}"


def parse_asset_name(name: str) -> AssetName | None:
    """Lit un nom de paquet ; ``None`` s'il ne suit pas exactement la convention (extension comprise)."""
    match = _ASSET_PATTERN.fullmatch(name)
    if match is None:
        return None
    version = Version.try_parse(match.group("version"))
    if version is None or version.build:
        return None
    target = Target(OperatingSystem(match.group("system")), Architecture(match.group("arch")))
    if ARCHIVE_EXTENSIONS[target.system] != match.group("extension"):
        return None
    return AssetName(version, target)


# ---------------------------------------------------------------------------
# Cible de la machine courante
# ---------------------------------------------------------------------------


def normalize_architecture(machine: str) -> Architecture | None:
    """Architecture d'après ``platform.machine()`` (``AMD64``, ``x86_64``, ``arm64``, ``aarch64``…)."""
    value = machine.strip().lower()
    if value in ("x86_64", "amd64", "x64", "x86-64"):
        return Architecture.X64
    if value in ("arm64", "aarch64", "armv8", "armv8l", "arm64e"):
        return Architecture.ARM64
    return None


def normalize_system(platform_name: str) -> OperatingSystem | None:
    """Système d'après ``sys.platform``."""
    if platform_name == "darwin":
        return OperatingSystem.MACOS
    if platform_name.startswith("win"):
        return OperatingSystem.WINDOWS
    if platform_name.startswith("linux"):
        return OperatingSystem.LINUX
    return None


def _macos_process_is_translated() -> bool:
    """``True`` si ce processus x86-64 tourne sous Rosetta 2 (``sysctl.proc_translated``)."""
    library = ctypes.util.find_library("c")
    if library is None:
        return False
    try:
        libc = ctypes.CDLL(library, use_errno=True)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        result = libc.sysctlbyname(b"sysctl.proc_translated", ctypes.byref(value), ctypes.byref(size), None, 0)
    except (OSError, AttributeError):
        return False
    return result == 0 and value.value == 1


def detect_target(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
    translated: bool | None = None,
) -> Target | None:
    """Cible de cette installation ; ``None`` sur un système ou une architecture sans paquet possible.

    L'architecture est celle du **processus** (donc de la copie installée), sauf sous Rosetta, où l'on vise la
    machine. Les paramètres rendent les trois systèmes testables depuis une seule machine.
    """
    platform_name = platform_name or sys.platform
    system = normalize_system(platform_name)
    architecture = normalize_architecture(machine if machine is not None else platform.machine())
    if system is None or architecture is None:
        return None
    if system is OperatingSystem.MACOS and architecture is Architecture.X64:
        if translated is None:
            translated = platform_name == sys.platform and _macos_process_is_translated()
        if translated:
            architecture = Architecture.ARM64
    return Target(system, architecture)


# ---------------------------------------------------------------------------
# Empreintes SHA-256
# ---------------------------------------------------------------------------


class ChecksumFileError(ValueError):
    """``SHA256SUMS.txt`` mal formé ou contradictoire."""


_CHECKSUM_LINE = re.compile(r"(?P<digest>[0-9A-Fa-f]{64}) [ *](?P<name>[^\r\n/\\]+)")


def parse_checksums(text: str) -> dict[str, str]:
    """``{nom de fichier: sha256 en minuscules}`` d'après un fichier au format ``sha256sum``.

    Les lignes vides et les commentaires (``#``) sont ignorés. Une ligne illisible, un nom contenant un chemin ou un
    même fichier listé avec deux empreintes différentes rendent le fichier entier invalide : on ne choisit pas.
    """
    result: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ChecksumFileError(f"ligne {number} illisible : {line[:80]!r}")
        name, digest = match.group("name").strip(), match.group("digest").lower()
        if result.get(name, digest) != digest:
            raise ChecksumFileError(f"deux empreintes différentes pour {name!r}")
        result[name] = digest
    return result


def format_checksums(entries: Mapping[str, str]) -> str:
    """Texte de ``SHA256SUMS.txt`` (trié par nom, deux espaces, fin de ligne finale)."""
    lines = []
    for name in sorted(entries):
        digest = entries[name].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or "/" in name or "\\" in name or not name.strip():
            raise ChecksumFileError(f"entrée invalide : {name!r}")
        lines.append(f"{digest}  {name}\n")
    return "".join(lines)


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> str:
    """Empreinte SHA-256 (hexadécimal minuscule) d'un fichier lu par blocs."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums_for(paths: Iterable[Path]) -> dict[str, str]:
    """``{nom: sha256}`` des fichiers ``paths`` (outil de publication)."""
    return {path.name: sha256_file(path) for path in paths}


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "ASSET_PREFIX",
    "Architecture",
    "AssetName",
    "CHECKSUMS_FILE",
    "ChecksumFileError",
    "MAX_CHECKSUMS_BYTES",
    "OperatingSystem",
    "RELEASE_TARGETS",
    "Target",
    "asset_name",
    "checksums_for",
    "detect_target",
    "format_checksums",
    "normalize_architecture",
    "normalize_system",
    "parse_asset_name",
    "parse_checksums",
    "sha256_file",
]
