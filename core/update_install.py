"""Installation automatique sous macOS : décisions, staging, validation du nouveau bundle, suivi (aucun Qt).

Ce module décide et prépare ; le script qui remplace l'application est dans :mod:`core.update_helper`. Rien n'est
modifié avant que **tout** soit validé :

1. :func:`evaluate_install` — l'installation automatique n'est proposée que si la signature Ed25519 du paquet est
   **vérifiée** (:class:`InstallPolicy`), si l'application est un ``Kut-Studio.app`` gelé, hors translocation et hors
   volume en lecture seule, et si le dossier parent **et** le bundle sont inscriptibles. Sinon : installation assistée.
2. :func:`stage_update` — extraction (``ditto -x -k --noqtn``) dans un dossier caché **frère** de l'application (même
   volume : renommages atomiques), puis :func:`validate_bundle` : identifiant, version annoncée et strictement
   supérieure, architecture, ``codesign --verify --deep --strict``, ``spctl`` si le bundle porte une signature Developer
   ID, même équipe que l'application installée. Un échec supprime le staging : l'application n'a pas été touchée.

**Quarantaine.** ``com.apple.quarantine`` est retirée du bundle de staging, et seulement là, parce que la signature
Ed25519 de la release est vérifiée : c'est elle qui garantit l'authenticité, pas Gatekeeper (qui, sur une quarantaine
posée par Kut-Studio lui-même, ne prouverait rien de plus). :func:`stage_update` refuse de s'exécuter sans
``signature_verified=True``. Le paquet téléchargé garde, lui, sa quarantaine.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .release_assets import Architecture, OperatingSystem, normalize_system
from .update_helper import PREVIOUS_PREFIX, STAGING_PREFIX
from .updates import InstallContext, InstallKind
from .versioning import Version

APP_BUNDLE_NAME = "Kut-Studio.app"
BUNDLE_IDENTIFIER = "com.kutstudio.app"
"""Identifiant posé par ``build.py`` (``--osx-bundle-identifier``)."""
MAX_ARCHIVE_ENTRIES = 200_000
MAX_UNCOMPRESSED_BYTES = 8 * 1024 ** 3
MAX_PLIST_BYTES = 1024 * 1024
MAX_DETAIL_CHARACTERS = 300
TOOL_TIMEOUT_SECONDS = 900
LIVE_STATUS_POLL_SECONDS = 30

DITTO = "/usr/bin/ditto"
CODESIGN = "/usr/bin/codesign"
LIPO = "/usr/bin/lipo"
SPCTL = "/usr/sbin/spctl"
XATTR = "/usr/bin/xattr"


# ---------------------------------------------------------------------------
# Politique : quand propose-t-on l'installation automatique ?
# ---------------------------------------------------------------------------


class InstallPolicy(Protocol):
    """Condition d'installation automatique d'un paquet vérifié (valeur de test injectable)."""

    def allows(self, package: object) -> bool: ...


class SignaturePolicy:
    """Vraie condition : ``package.signature_status`` vaut ``VERIFIED`` (:mod:`core.update_signature`, lot 1)."""

    def allows(self, package: object) -> bool:
        status = getattr(package, "signature_status", None)
        if status is None:
            return False
        names = {getattr(status, "name", None), getattr(status, "value", None), status}
        return any(isinstance(name, str) and name.upper() == "VERIFIED" for name in names)


@dataclass(frozen=True)
class FixedPolicy:
    """Valeur imposée (tests)."""

    verified: bool

    def allows(self, package: object) -> bool:
        return self.verified


class InstallRefusal(str, Enum):
    """Pourquoi l'installation automatique n'est pas proposée (``update.auto.reason.<valeur>``)."""

    SOURCE = "source"
    NOT_MACOS = "not_macos"
    NOT_BUNDLE = "not_bundle"
    TRANSLOCATED = "translocated"
    MISSING_BUNDLE = "missing_bundle"
    READ_ONLY_VOLUME = "read_only_volume"
    PARENT_NOT_WRITABLE = "parent_not_writable"
    BUNDLE_NOT_WRITABLE = "bundle_not_writable"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True)
class InstallDecision:
    available: bool
    refusal: InstallRefusal | None = None
    app: Path | None = None


def _volume_is_read_only(path: Path) -> bool:
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return True


def evaluate_install(
    context: InstallContext,
    package: object,
    *,
    policy: InstallPolicy | None = None,
    platform_name: str | None = None,
    access: Callable[[str, int], bool] = os.access,
    read_only: Callable[[Path], bool] = _volume_is_read_only,
) -> InstallDecision:
    """L'installation automatique est-elle possible, et sinon pourquoi ? Ne touche à rien."""
    if context.kind is InstallKind.SOURCE:
        return InstallDecision(False, InstallRefusal.SOURCE)
    system = normalize_system(platform_name or sys.platform)
    if context.kind is not InstallKind.MACOS_APP or system is not OperatingSystem.MACOS:
        return InstallDecision(False, InstallRefusal.NOT_MACOS)
    if context.translocated:
        return InstallDecision(False, InstallRefusal.TRANSLOCATED)
    location = str(context.location) if context.location is not None else ""
    app = Path(location) if location else None
    if app is None or app.suffix != ".app" or app.name != APP_BUNDLE_NAME or "\n" in location or not app.is_absolute():
        return InstallDecision(False, InstallRefusal.NOT_BUNDLE)
    if not app.is_dir() or app.is_symlink():
        return InstallDecision(False, InstallRefusal.MISSING_BUNDLE, app)
    if read_only(app):
        return InstallDecision(False, InstallRefusal.READ_ONLY_VOLUME, app)
    if not access(str(app.parent), os.W_OK | os.X_OK):
        return InstallDecision(False, InstallRefusal.PARENT_NOT_WRITABLE, app)
    if not access(str(app), os.W_OK | os.X_OK):
        return InstallDecision(False, InstallRefusal.BUNDLE_NOT_WRITABLE, app)
    if not (policy if policy is not None else SignaturePolicy()).allows(package):
        return InstallDecision(False, InstallRefusal.NOT_VERIFIED, app)
    return InstallDecision(True, None, app)


# ---------------------------------------------------------------------------
# Commandes système (remplaçables en test)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


class StageProblem(str, Enum):
    """Pourquoi la préparation a échoué (``update.auto.problem.<valeur>``)."""

    TOOL = "tool"
    ARCHIVE = "archive"
    DISK = "disk"
    BUNDLE = "bundle"
    IDENTIFIER = "identifier"
    VERSION = "version"
    NOT_NEWER = "not_newer"
    ARCHITECTURE = "architecture"
    SIGNATURE = "signature"
    GATEKEEPER = "gatekeeper"
    TEAM = "team"
    NOT_VERIFIED = "not_verified"


class StageError(Exception):
    """Préparation refusée ; ``detail`` est technique et borné (jamais l'entrée entière)."""

    def __init__(self, problem: StageProblem, detail: str = "") -> None:
        detail = detail if len(detail) <= MAX_DETAIL_CHARACTERS else detail[:MAX_DETAIL_CHARACTERS] + "…"
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem
        self.detail = detail


def run_command(args: Sequence[str]) -> CommandResult:
    """Lance un outil système sans shell, avec délai ; un outil absent ou trop lent est une :class:`StageError`."""
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS, check=False,
                              stdin=subprocess.DEVNULL, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageError(StageProblem.TOOL, f"{Path(args[0]).name} : {type(exc).__name__}") from exc
    return CommandResult(done.returncode, done.stdout or "", done.stderr or "")


def required_architecture(architecture: Architecture) -> str:
    return "arm64" if architecture is Architecture.ARM64 else "x86_64"


# ---------------------------------------------------------------------------
# Validation d'un bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleReport:
    identifier: str
    version: Version
    architectures: tuple[str, ...]
    adhoc: bool
    team: str | None


def read_bundle_info(bundle: Path) -> dict[str, object]:
    info_path = bundle / "Contents" / "Info.plist"
    try:
        if info_path.is_symlink() or info_path.stat().st_size > MAX_PLIST_BYTES:
            raise StageError(StageProblem.BUNDLE, "Info.plist illisible")
        with open(info_path, "rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise StageError(StageProblem.BUNDLE, f"Info.plist illisible ({type(exc).__name__})") from exc
    if not isinstance(info, dict):
        raise StageError(StageProblem.BUNDLE, "Info.plist n'est pas un dictionnaire")
    return info


def signing_details(bundle: Path, run: Runner) -> tuple[bool, str | None]:
    """(ad hoc ?, identifiant d'équipe) lus par ``codesign -dvv`` ; équipe ``None`` si absente."""
    result = run([CODESIGN, "-dvv", str(bundle)])
    text = f"{result.stderr}\n{result.stdout}"
    adhoc = re.search(r"^Signature=adhoc\s*$", text, re.MULTILINE) is not None
    match = re.search(r"^TeamIdentifier=(?P<team>[A-Z0-9]{10})\s*$", text, re.MULTILINE)
    return adhoc, match.group("team") if match else None


def validate_bundle(
    bundle: Path,
    *,
    expected_identifier: str,
    expected_version: Version,
    installed_version: Version,
    required_arch: str,
    installed_team: str | None = None,
    run: Runner = run_command,
) -> BundleReport:
    """Vérifie le nouveau bundle **avant** de toucher à l'ancien ; :class:`StageError` au premier défaut."""
    if not bundle.is_dir() or bundle.is_symlink():
        raise StageError(StageProblem.BUNDLE, "le bundle extrait est absent")
    info = read_bundle_info(bundle)
    identifier = info.get("CFBundleIdentifier")
    if identifier != expected_identifier:
        raise StageError(StageProblem.IDENTIFIER, f"identifiant {str(identifier)[:80]!r}, attendu {expected_identifier!r}")
    announced = Version.try_parse(info.get("CFBundleShortVersionString"))
    if announced is None or announced != expected_version:
        raise StageError(StageProblem.VERSION, f"version {str(info.get('CFBundleShortVersionString'))[:40]!r}, "
                                               f"annoncée {expected_version}")
    if not announced > installed_version:
        raise StageError(StageProblem.NOT_NEWER, f"{announced} n'est pas plus récente que {installed_version}")
    executable_name = info.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or not executable_name or "/" in executable_name or executable_name.startswith("."):
        raise StageError(StageProblem.BUNDLE, "CFBundleExecutable invalide")
    executable = bundle / "Contents" / "MacOS" / executable_name
    if not executable.is_file() or executable.is_symlink():
        raise StageError(StageProblem.BUNDLE, f"exécutable {executable_name!r} absent")
    lipo = run([LIPO, "-archs", str(executable)])
    architectures = tuple(lipo.stdout.split()) if lipo.returncode == 0 else ()
    if required_arch not in architectures:
        raise StageError(StageProblem.ARCHITECTURE, f"architectures {' '.join(architectures)[:80] or 'illisibles'}, "
                                                    f"requise {required_arch}")
    verify = run([CODESIGN, "--verify", "--deep", "--strict", str(bundle)])
    if verify.returncode != 0:
        raise StageError(StageProblem.SIGNATURE, (verify.stderr or verify.stdout).strip() or f"code {verify.returncode}")
    adhoc, team = signing_details(bundle, run)
    if installed_team is not None and team != installed_team:
        raise StageError(StageProblem.TEAM, f"équipe {team or 'aucune'}, l'application installée est signée par "
                                            f"{installed_team}")
    if not adhoc:
        # Signature Developer ID : Gatekeeper doit l'accepter (notarisation comprise). Ad hoc : codesign seul.
        assessed = run([SPCTL, "--assess", "--type", "execute", str(bundle)])
        if assessed.returncode != 0:
            raise StageError(StageProblem.GATEKEEPER, (assessed.stderr or assessed.stdout).strip()
                             or f"code {assessed.returncode}")
    return BundleReport(str(identifier), announced, architectures, adhoc, team)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def inspect_archive(archive: Path, *, free_bytes: int) -> int:
    """Taille décompressée de l'archive après contrôle de ses entrées ; :class:`StageError` si elle est douteuse."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise StageError(StageProblem.ARCHIVE, f"archive illisible ({type(exc).__name__})") from exc
    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
        raise StageError(StageProblem.ARCHIVE, f"{len(entries)} entrées")
    total = 0
    for entry in entries:
        parts = entry.filename.split("/")
        if entry.filename.startswith("/") or ".." in parts or "\\" in entry.filename:
            raise StageError(StageProblem.ARCHIVE, "chemin d'entrée refusé")
        if parts[0] != APP_BUNDLE_NAME:
            raise StageError(StageProblem.ARCHIVE, "l'archive contient autre chose que Kut-Studio.app")
        total += entry.file_size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise StageError(StageProblem.ARCHIVE, f"{total} octets décompressés")
    if free_bytes < int(total * 1.1) + 256 * 1024 * 1024:
        raise StageError(StageProblem.DISK, f"{total} octets à extraire, {free_bytes} libres")
    return total


def stage_update(
    archive: Path,
    app: Path,
    *,
    expected_version: Version,
    installed_version: Version,
    required_arch: str,
    pid: int,
    signature_verified: bool,
    expected_identifier: str = BUNDLE_IDENTIFIER,
    run: Runner = run_command,
) -> Path:
    """Extrait et valide la nouvelle application à côté de ``app`` ; retourne le dossier de staging.

    Aucun fichier existant n'est modifié ; au moindre défaut, le staging est supprimé avant de lever l'erreur.
    """
    if not signature_verified:
        raise StageError(StageProblem.NOT_VERIFIED, "la signature n'est pas vérifiée : la quarantaine reste")
    staging = app.parent / f"{STAGING_PREFIX}{pid}"
    try:
        inspect_archive(archive, free_bytes=shutil.disk_usage(app.parent).free)
        if staging.exists() or staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o700)
        extracted = run([DITTO, "-x", "-k", "--noqtn", str(archive), str(staging)])
        if extracted.returncode != 0:
            raise StageError(StageProblem.ARCHIVE, f"ditto : {(extracted.stderr or extracted.stdout).strip()[:200]}")
        bundle = staging / APP_BUNDLE_NAME
        if bundle.is_dir() and not bundle.is_symlink():
            # Signature Ed25519 vérifiée = authenticité garantie par nous : la quarantaine du bundle ne sert plus.
            run([XATTR, "-dr", "com.apple.quarantine", str(bundle)])
        _adhoc, installed_team = signing_details(app, run)
        validate_bundle(
            bundle,
            expected_identifier=expected_identifier,
            expected_version=expected_version,
            installed_version=installed_version,
            required_arch=required_arch,
            installed_team=installed_team,
            run=run,
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


# ---------------------------------------------------------------------------
# Suivi : fichier « démarré », statut, reprise
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallOutcome:
    result: str
    """``ok``, ``rolled_back`` ou ``failed``."""
    version: str
    previous: str
    reason: str


def write_started_marker(path: Path, version: str, pid: int) -> None:
    """Fichier que l'assistant attend : la nouvelle application dit qu'elle tourne (écriture atomique)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{pid}")
    temporary.write_text(json.dumps({"version": version, "pid": pid, "time": int(time.time())}, separators=(",", ":")),
                         encoding="utf-8")
    os.replace(temporary, path)


def read_outcome(path: Path) -> InstallOutcome | None:
    """Statut écrit par l'assistant, ou ``None`` (absent, illisible, hors format)."""
    try:
        if path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    values = [data.get(key) for key in ("result", "version", "previous", "reason")]
    if not all(isinstance(value, str) for value in values) or values[0] not in ("ok", "rolled_back", "failed"):
        return None
    return InstallOutcome(*(str(value) for value in values))


def consume_outcome(path: Path) -> None:
    path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def helper_running(lock: Path, alive: Callable[[int], bool] = pid_alive) -> bool:
    """Un assistant d'installation est-il en cours (verrou écrit avec son pid, processus vivant) ?"""
    try:
        return alive(int(lock.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return False


def bundle_is_valid(bundle: Path) -> bool:
    try:
        info = read_bundle_info(bundle)
    except StageError:
        return False
    name = info.get("CFBundleExecutable")
    return isinstance(name, str) and (bundle / "Contents" / "MacOS" / name).is_file()


@dataclass(frozen=True)
class RecoveryReport:
    restored: Path | None = None
    removed: tuple[Path, ...] = ()
    kept: tuple[Path, ...] = ()
    busy: bool = False


def recover_leftovers(
    app: Path,
    *,
    updates_dir: Path,
    lock: Path,
    status: Path,
    running_version: str,
    alive: Callable[[int], bool] = pid_alive,
) -> RecoveryReport:
    """Au démarrage : traite ce qu'une installation interrompue a laissé à côté de ``app``.

    * assistant encore vivant : ne touche à rien (c'est le déroulé normal, il supprimera la sauvegarde lui-même) ;
    * ``Kut-Studio.app`` absent ou invalide et une sauvegarde valide : la sauvegarde est **restaurée** ;
    * application valide et sauvegarde (assistant mort après le renommage) : l'application qui tourne prouve que la
      nouvelle version démarre, la sauvegarde est supprimée, sauf si elle porte la version qui tourne (gardée) ;
    * stagings et dossiers d'assistant orphelins : supprimés.
    """
    if helper_running(lock, alive):
        return RecoveryReport(busy=True)
    lock.unlink(missing_ok=True)
    removed: list[Path] = []
    kept: list[Path] = []
    restored: Path | None = None
    parent = app.parent
    try:
        entries = sorted(parent.iterdir(), key=lambda p: p.name)
    except OSError:
        return RecoveryReport()
    previous = [p for p in entries if p.name.startswith(PREVIOUS_PREFIX) and p.is_dir() and not p.is_symlink()]
    if previous and not bundle_is_valid(app):
        candidates = [p for p in previous if bundle_is_valid(p) and not p.name.endswith(".failed")]
        if candidates and not app.exists():
            chosen = candidates[-1]
            os.rename(chosen, app)
            restored = chosen
            previous = [p for p in previous if p != chosen]
        else:
            kept.extend(previous)
            previous = []
    for leftover in previous:
        if leftover.name == f"{PREVIOUS_PREFIX}{running_version}" or leftover.name.endswith(".failed"):
            kept.append(leftover)
            continue
        shutil.rmtree(leftover, ignore_errors=True)
        removed.append(leftover)
    for staging in (p for p in entries if p.name.startswith(STAGING_PREFIX)):
        suffix = staging.name[len(STAGING_PREFIX):]
        if suffix.isdigit() and alive(int(suffix)):
            continue                      # staging d'une autre instance encore ouverte
        shutil.rmtree(staging, ignore_errors=True)
        removed.append(staging)
    try:
        for folder in updates_dir.glob("helper-*"):
            shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass
    return RecoveryReport(restored=restored, removed=tuple(removed), kept=tuple(kept))


__all__ = [
    "APP_BUNDLE_NAME",
    "BUNDLE_IDENTIFIER",
    "BundleReport",
    "CommandResult",
    "FixedPolicy",
    "InstallDecision",
    "InstallOutcome",
    "InstallPolicy",
    "InstallRefusal",
    "RecoveryReport",
    "SignaturePolicy",
    "StageError",
    "StageProblem",
    "bundle_is_valid",
    "consume_outcome",
    "evaluate_install",
    "helper_running",
    "inspect_archive",
    "pid_alive",
    "read_outcome",
    "recover_leftovers",
    "required_architecture",
    "run_command",
    "stage_update",
    "validate_bundle",
    "write_started_marker",
]
