"""Mises à jour : règles pures (aucun réseau, aucun Qt).

Ce module décide ; :mod:`core.update_service` transporte. Tout ce qui peut être faux ici se teste sans réseau :

* **lecture** de la réponse de l'API GitHub Releases, sans confiance : brouillons ignorés, tags non SemVer ignorés,
  paquets dont l'adresse ne pointe pas vers les releases du dépôt ignorés, réponse d'une autre forme refusée ;
* **choix** de la version proposée (:func:`evaluate_releases`) : jamais une version plus ancienne, pas de préversion
  pour une version stable sauf demande, version ignorée silencieuse en vérification automatique mais toujours retrouvée
  par une recherche manuelle ;
* **choix du paquet** (exact, voir :mod:`core.release_assets`) et **vérification** du téléchargement : taille annoncée
  par GitHub, SHA-256 publiée dans ``SHA256SUMS.txt`` et, quand GitHub la fournit, l'empreinte calculée par GitHub ;
* **erreurs** typées (:class:`UpdateErrorKind`) : l'interface les traduit (``update.error.<kind>``), le détail technique
  ne sert qu'au journal et au diagnostic ;
* **contexte d'installation** : copie gelée (application, dossier) ou exécution depuis les sources, qu'on ne remplace
  jamais.

Ce que garantit l'empreinte : le fichier reçu est complet et identique à celui que la release publie. Elle vient de la
même release que le paquet : elle ne prouve pas *qui* l'a publié. L'authenticité repose sur les signatures vérifiées
par le système (Developer ID et notarisation sous macOS) ; c'est pourquoi le paquet téléchargé reçoit la marque
« téléchargé d'Internet » du système (:func:`mark_as_downloaded`), comme par un navigateur, au lieu d'y échapper.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath, PureWindowsPath
from urllib.parse import quote

from .app_version import APP_NAME, APP_VERSION, GITHUB_REPOSITORY
from .platform_paths import user_cache_dir
from .release_assets import (
    CHECKSUMS_FILE,
    MAX_CHECKSUMS_BYTES,
    ChecksumFileError,
    OperatingSystem,
    Target,
    normalize_system,
    parse_asset_name,
    parse_checksums,
)
from .versioning import Version

GITHUB_API = "https://api.github.com"
AUTO_CHECK_INTERVAL_SECONDS = 24 * 3600
"""Une vérification automatique au plus par jour : au démarrage, si la précédente réussie date d'au moins 24 h."""
MAX_RELEASES_BYTES = 4 * 1024 * 1024
MAX_NOTES_CHARACTERS = 40_000
MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
"""Borne de bon sens : un paquet annoncé plus gros est refusé avant tout téléchargement."""
UPDATE_CHECK_ENV = "KUT_STUDIO_UPDATE_CHECK"
"""``off`` coupe la vérification **automatique** (tests, captures, smoke test) ; la recherche manuelle reste possible."""


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


class UpdateErrorKind(str, Enum):
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    HTTP = "http"
    TLS = "tls"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"
    CANCELLED = "cancelled"
    TOO_LARGE = "too_large"
    SIZE_MISMATCH = "size_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    CHECKSUM_MISSING = "checksum_missing"
    NO_PACKAGE = "no_package"
    DISK = "disk"
    MARK_FAILED = "mark_failed"
    UNSUPPORTED = "unsupported"


class UpdateError(Exception):
    """Échec typé d'une recherche ou d'un téléchargement ; ``detail`` est technique (journal), jamais traduit."""

    def __init__(
        self,
        kind: UpdateErrorKind,
        detail: str = "",
        *,
        status: int | None = None,
        retry_at: float | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)
        self.kind = kind
        self.detail = detail
        self.status = status
        self.retry_at = retry_at


def http_failure(status: int, headers: Mapping[str, str], *, now: float | None = None) -> UpdateError:
    """Erreur d'une réponse HTTP en échec ; reconnaît les limites de l'API GitHub.

    GitHub signale la limite principale par ``403``/``429`` et ``X-RateLimit-Remaining: 0`` (heure de reprise dans
    ``X-RateLimit-Reset``), la limite secondaire par ``Retry-After`` (secondes).
    """
    now = time.time() if now is None else now
    lowered = {key.lower(): value.strip() for key, value in headers.items()}
    retry_after = lowered.get("retry-after", "")
    remaining = lowered.get("x-ratelimit-remaining", "")
    if status == 429 or (status == 403 and (remaining == "0" or retry_after)):
        retry_at: float | None = None
        reset = lowered.get("x-ratelimit-reset", "")
        if retry_after.isdigit():
            retry_at = now + int(retry_after)
        elif reset.isdigit():
            retry_at = float(reset)
        return UpdateError(UpdateErrorKind.RATE_LIMITED, f"HTTP {status}", status=status, retry_at=retry_at)
    return UpdateError(UpdateErrorKind.HTTP, f"HTTP {status}", status=status)


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None
    """SHA-256 calculée par GitHub à l'envoi (champ ``digest`` de l'API), quand elle existe."""


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    title: str
    notes: str
    page_url: str
    published_at: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...] = ()

    def package_for(self, target: Target | None) -> ReleaseAsset | None:
        """Le paquet **exact** de ``target`` pour cette version, ou ``None``."""
        if target is None:
            return None
        for asset in self.assets:
            parsed = parse_asset_name(asset.name)
            if parsed is not None and parsed.target == target and parsed.version == self.version:
                return asset
        return None

    @property
    def checksums(self) -> ReleaseAsset | None:
        return next((asset for asset in self.assets if asset.name == CHECKSUMS_FILE), None)


def releases_api_url(repository: str = GITHUB_REPOSITORY, api_base: str = GITHUB_API) -> str:
    return f"{api_base.rstrip('/')}/repos/{repository}/releases?per_page=30"


def download_prefix(repository: str = GITHUB_REPOSITORY) -> str:
    """Seules adresses de téléchargement acceptées : les fichiers des releases du dépôt, en HTTPS."""
    return f"https://github.com/{repository}/releases/download/"


def release_page_url(tag: str, repository: str = GITHUB_REPOSITORY) -> str:
    """Page d'une release, construite (jamais lue de la réponse)."""
    return f"https://github.com/{repository}/releases/tag/{quote(tag, safe='')}"


def user_agent() -> str:
    """En-tête ``User-Agent`` (exigé par l'API GitHub) : le nom et la version, rien d'autre."""
    return f"{APP_NAME}/{APP_VERSION}"


_DIGEST = re.compile(r"sha256:(?P<hex>[0-9a-fA-F]{64})")


def _parse_asset(raw: object, prefix: str) -> ReleaseAsset | None:
    if not isinstance(raw, dict):
        return None
    name, url, size = raw.get("name"), raw.get("browser_download_url"), raw.get("size")
    if not isinstance(name, str) or not isinstance(url, str) or isinstance(size, bool) or not isinstance(size, int):
        return None
    # « <prefix><tag>/<nom> » : le fichier téléchargé est bien celui que le nom annonce, dans ce dépôt.
    if size < 0 or not url.startswith(prefix) or url[len(prefix):].count("/") != 1:
        return None
    if url.rsplit("/", 1)[1] != quote(name, safe=""):
        return None
    if raw.get("state", "uploaded") != "uploaded":     # envoi interrompu : fichier incomplet côté GitHub
        return None
    digest = None
    match = _DIGEST.fullmatch(raw.get("digest") or "") if isinstance(raw.get("digest"), str) else None
    if match is not None:
        digest = match.group("hex").lower()
    return ReleaseAsset(name=name, url=url, size=size, digest=digest)


def parse_releases(
    payload: object,
    *,
    repository: str = GITHUB_REPOSITORY,
    asset_prefix: str | None = None,
) -> list[Release]:
    """Releases lisibles de la réponse de ``GET /repos/{repo}/releases``.

    Une réponse qui n'est pas une liste lève :class:`UpdateError` (``INVALID_RESPONSE``). Dans la liste, tout ce
    qui est douteux est ignoré, élément par élément : brouillon, tag non SemVer, champ manquant ou d'un mauvais type,
    paquet hors du dépôt. ``asset_prefix`` remplace le préfixe d'adresse autorisé (tests sur serveur local).
    """
    if not isinstance(payload, list):
        raise UpdateError(UpdateErrorKind.INVALID_RESPONSE, f"liste attendue, reçu {type(payload).__name__}")
    prefix = asset_prefix if asset_prefix is not None else download_prefix(repository)
    releases: list[Release] = []
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("draft") is not False:
            continue
        tag = raw.get("tag_name")
        version = Version.try_parse(tag)
        if version is None or version.build or not isinstance(tag, str):
            continue
        prerelease_flag = raw.get("prerelease")
        if not isinstance(prerelease_flag, bool):
            continue
        assets_raw = raw.get("assets")
        assets = tuple(
            asset for asset in (_parse_asset(item, prefix) for item in (assets_raw if isinstance(assets_raw, list) else ()))
            if asset is not None
        )
        title = raw.get("name") if isinstance(raw.get("name"), str) else ""
        notes = raw.get("body") if isinstance(raw.get("body"), str) else ""
        published = raw.get("published_at") if isinstance(raw.get("published_at"), str) else ""
        releases.append(
            Release(
                version=version,
                tag=tag,
                title=str(title).strip(),
                notes=str(notes)[:MAX_NOTES_CHARACTERS],
                page_url=release_page_url(tag, repository),
                published_at=str(published),
                # Une préversion SemVer en est une, même si la case « pre-release » de GitHub a été oubliée.
                prerelease=prerelease_flag or version.is_prerelease,
                assets=assets,
            )
        )
    return releases


# ---------------------------------------------------------------------------
# Choix de la version
# ---------------------------------------------------------------------------


class CheckMode(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


@dataclass(frozen=True)
class UpdateOffer:
    """Version proposée à l'utilisateur, avec son paquet et ses empreintes s'ils existent."""

    release: Release
    target: Target | None
    package: ReleaseAsset | None
    checksums: ReleaseAsset | None
    skipped: bool = False
    """L'utilisateur avait choisi d'ignorer cette version (seule une recherche manuelle la montre alors)."""

    @property
    def version(self) -> Version:
        return self.release.version

    @property
    def can_download(self) -> bool:
        """Téléchargement intégré possible : paquet compatible, empreintes publiées, taille plausible."""
        return (
            self.package is not None
            and self.checksums is not None
            and 0 < self.package.size <= MAX_PACKAGE_BYTES
            and self.checksums.size <= MAX_CHECKSUMS_BYTES
        )


@dataclass(frozen=True)
class CheckResult:
    mode: CheckMode
    current: Version
    offer: UpdateOffer | None = None
    error: UpdateError | None = None
    hidden_prerelease: Version | None = None
    """Préversion plus récente non proposée (version stable sans l'option « préversions »)."""
    checked_at: float = field(default_factory=time.time)

    @property
    def up_to_date(self) -> bool:
        return self.error is None and self.offer is None


def evaluate_releases(
    releases: Iterable[Release],
    *,
    current: Version,
    target: Target | None,
    include_prereleases: bool,
    skipped_version: Version | None,
    mode: CheckMode,
) -> CheckResult:
    """Décide quelle version proposer, s'il y en a une.

    * seules les versions **strictement plus récentes** que ``current`` comptent ;
    * une préversion n'est proposée que si l'option est active ou si ``current`` est elle-même une préversion
      (qui teste une bêta reçoit la suivante) ;
    * on propose la plus récente, même si elle n'a pas de paquet pour cette machine : l'interface l'explique
      plutôt que de proposer en silence une version intermédiaire ;
    * la version ignorée par l'utilisateur ne déclenche rien en vérification automatique ; une version plus récente
      qu'elle, si. La recherche manuelle la montre toujours (``skipped=True``).
    """
    allow_prereleases = include_prereleases or current.is_prerelease
    newer = [release for release in releases if release.version > current]
    eligible = [release for release in newer if allow_prereleases or not release.prerelease]
    hidden = [release.version for release in newer if release not in eligible]
    hidden_prerelease = max(hidden) if hidden else None
    if not eligible:
        return CheckResult(mode, current, hidden_prerelease=hidden_prerelease)
    newest = max(eligible, key=lambda release: release.version)
    skipped = skipped_version is not None and newest.version == skipped_version
    if skipped and mode is CheckMode.AUTOMATIC:
        return CheckResult(mode, current, hidden_prerelease=hidden_prerelease)
    offer = UpdateOffer(
        release=newest,
        target=target,
        package=newest.package_for(target),
        checksums=newest.checksums,
        skipped=skipped,
    )
    return CheckResult(mode, current, offer=offer, hidden_prerelease=hidden_prerelease)


def auto_check_due(*, enabled: bool, last_check: float, now: float | None = None,
                   interval: float = AUTO_CHECK_INTERVAL_SECONDS) -> bool:
    """La vérification automatique de ce démarrage doit-elle avoir lieu ?

    ``last_check`` est l'heure de la dernière vérification **réussie** (0 : jamais). Une horloge revenue en
    arrière (``last_check`` dans le futur) ne bloque pas les vérifications.
    """
    if not enabled:
        return False
    now = time.time() if now is None else now
    if last_check <= 0 or last_check > now:
        return True
    return now - last_check >= interval


def automatic_checks_allowed(environment: Mapping[str, str] | None = None) -> bool:
    """``False`` quand ``KUT_STUDIO_UPDATE_CHECK=off`` (tests, outils de capture, smoke test)."""
    env = os.environ if environment is None else environment
    return env.get(UPDATE_CHECK_ENV, "").strip().lower() not in {"0", "off", "false", "no"}


def coerce_skipped_version(value: object) -> str:
    """Version ignorée relue des préférences : une version valide, ou ``""``."""
    version = Version.try_parse(value)
    return "" if version is None else str(version)


# ---------------------------------------------------------------------------
# Vérification du téléchargement
# ---------------------------------------------------------------------------


def expected_checksum(checksums_text: str, package: ReleaseAsset) -> str:
    """Empreinte publiée pour ``package`` dans ``SHA256SUMS.txt`` ; refuse un fichier illisible ou incomplet.

    Si GitHub fournit sa propre empreinte du paquet et qu'elle diffère de celle publiée, la release est
    incohérente : refus avant même de télécharger.
    """
    try:
        entries = parse_checksums(checksums_text)
    except ChecksumFileError as exc:
        raise UpdateError(UpdateErrorKind.INVALID_RESPONSE, f"{CHECKSUMS_FILE} : {exc}") from exc
    expected = entries.get(package.name)
    if expected is None:
        raise UpdateError(UpdateErrorKind.CHECKSUM_MISSING, f"{package.name} absent de {CHECKSUMS_FILE}")
    if package.digest is not None and package.digest != expected:
        raise UpdateError(
            UpdateErrorKind.CHECKSUM_MISMATCH,
            f"{CHECKSUMS_FILE} ({expected}) et GitHub ({package.digest}) ne donnent pas la même empreinte",
        )
    return expected


def verify_package(*, size: int, sha256: str, package: ReleaseAsset, expected_sha256: str) -> None:
    """Refuse un fichier dont la taille ou l'empreinte n'est pas celle publiée (:class:`UpdateError`)."""
    if size != package.size:
        raise UpdateError(UpdateErrorKind.SIZE_MISMATCH, f"{size} octets reçus, {package.size} annoncés")
    actual = sha256.lower()
    if actual != expected_sha256.lower():
        raise UpdateError(UpdateErrorKind.CHECKSUM_MISMATCH, f"sha256 {actual}, attendu {expected_sha256}")
    if package.digest is not None and actual != package.digest:
        raise UpdateError(UpdateErrorKind.CHECKSUM_MISMATCH, f"sha256 {actual}, GitHub annonce {package.digest}")


def updates_directory() -> Path:
    """Dossier des téléchargements de mise à jour (cache de l'utilisateur, jamais le dossier de l'application)."""
    return user_cache_dir() / "updates"


# ---------------------------------------------------------------------------
# Marque « téléchargé d'Internet »
# ---------------------------------------------------------------------------

QUARANTINE_ATTRIBUTE = "com.apple.quarantine"


def quarantine_value(now: float, agent: str = APP_NAME) -> bytes:
    """Valeur de l'attribut de quarantaine macOS, au format qu'écrivent les navigateurs (``0081;horodatage;agent;``)."""
    return f"0081;{int(now):08x};{agent};".encode("ascii")


def zone_identifier_text(url: str) -> str:
    """Contenu du flux ``Zone.Identifier`` (« Mark of the Web ») : zone Internet, adresse d'origine."""
    return f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={url}\r\n"


def _libc() -> ctypes.CDLL | None:
    name = ctypes.util.find_library("c")
    if name is None:
        return None
    try:
        return ctypes.CDLL(name, use_errno=True)
    except OSError:
        return None


def macos_extended_attribute(path: str | os.PathLike[str], name: str) -> bytes | None:
    """Valeur d'un attribut étendu macOS (``None`` s'il n'existe pas) ; sert au diagnostic et aux tests."""
    libc = _libc()
    if libc is None or sys.platform != "darwin":
        return None
    getxattr = libc.getxattr
    getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    getxattr.restype = ctypes.c_ssize_t
    encoded, key = os.fsencode(path), name.encode("utf-8")
    size = getxattr(encoded, key, None, 0, 0, 0)
    if size < 0:
        return None
    buffer = ctypes.create_string_buffer(size)
    read = getxattr(encoded, key, buffer, size, 0, 0)
    return buffer.raw[:read] if read >= 0 else None


def _set_macos_quarantine(path: str | os.PathLike[str], value: bytes) -> bool:
    libc = _libc()
    if libc is None:
        return False
    setxattr = libc.setxattr
    setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    setxattr.restype = ctypes.c_int
    return setxattr(os.fsencode(path), QUARANTINE_ATTRIBUTE.encode("ascii"), value, len(value), 0, 0) == 0


def download_mark_expected(platform_name: str | None = None) -> bool:
    """Le système attend-il une marque « téléchargé d'Internet » (macOS, Windows) ? Linux n'en a pas.

    Là où elle est attendue, un paquet qui ne peut pas la recevoir est **refusé** : sans elle, Gatekeeper ou
    SmartScreen ne contrôleraient pas sa signature, et l'empreinte seule ne prouve pas qui l'a publié.
    """
    return normalize_system(platform_name or sys.platform) in (OperatingSystem.MACOS, OperatingSystem.WINDOWS)


def mark_as_downloaded(
    path: str | os.PathLike[str],
    *,
    url: str,
    platform_name: str | None = None,
    now: float | None = None,
) -> bool:
    """Marque le paquet vérifié comme venant d'Internet, comme le ferait un navigateur.

    macOS : attribut ``com.apple.quarantine`` — Gatekeeper contrôlera signature et notarisation à la première
    ouverture (l'utilitaire d'archive propage la marque à l'application extraite). Windows : flux
    ``Zone.Identifier`` — SmartScreen et la marque « Mark of the Web » s'appliquent. Linux : rien d'équivalent.
    ``False`` si la marque n'a pas pu être posée (système de fichiers sans attributs, par exemple).
    """
    system = normalize_system(platform_name or sys.platform)
    if system is OperatingSystem.MACOS:
        return _set_macos_quarantine(path, quarantine_value(time.time() if now is None else now))
    if system is OperatingSystem.WINDOWS:
        try:
            with open(f"{os.fspath(path)}:Zone.Identifier", "w", encoding="utf-8", newline="") as stream:
                stream.write(zone_identifier_text(url))
        except OSError:
            return False
        return True
    return False


# ---------------------------------------------------------------------------
# Contexte d'installation
# ---------------------------------------------------------------------------


class InstallKind(str, Enum):
    SOURCE = "source"
    MACOS_APP = "macos_app"
    WINDOWS_FOLDER = "windows_folder"
    LINUX_FOLDER = "linux_folder"


@dataclass(frozen=True)
class InstallContext:
    """Comment cette copie de Kut-Studio est installée, et où."""

    kind: InstallKind
    location: PurePath | None
    translocated: bool = False
    """macOS a lancé l'application depuis un emplacement temporaire (App Translocation)."""
    revision: str | None = None
    """Commit du dépôt pour une exécution depuis les sources (lu dans ``.git``, sans lancer git)."""

    @property
    def is_source(self) -> bool:
        return self.kind is InstallKind.SOURCE


_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def source_revision(root: Path) -> str | None:
    """Commit courant d'un dépôt git (``.git`` dossier, ou fichier ``gitdir:`` d'un worktree), sans processus."""
    git = root / ".git"
    try:
        if git.is_file():
            content = git.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content[len("gitdir:"):].strip())
            gitdir = gitdir if gitdir.is_absolute() else (root / gitdir).resolve()
        elif git.is_dir():
            gitdir = git
        else:
            return None
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:12] if _COMMIT.fullmatch(head) else None
        ref = head[len("ref:"):].strip()
        common = gitdir
        commondir = gitdir / "commondir"
        if commondir.is_file():
            common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            candidate = base / ref
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                return value[:12] if _COMMIT.fullmatch(value) else None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                value, _, name = line.partition(" ")
                if name.strip() == ref and _COMMIT.fullmatch(value):
                    return value[:12]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def install_context(
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    platform_name: str | None = None,
    source_root: Path | None = None,
) -> InstallContext:
    """Contexte de cette copie : sources (jamais remplacées), application macOS, dossier Windows ou Linux."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        root = source_root if source_root is not None else Path(__file__).resolve().parents[1]
        return InstallContext(InstallKind.SOURCE, root, revision=source_revision(root))
    system = normalize_system(platform_name or sys.platform)
    executable = executable or sys.executable
    if system is OperatingSystem.WINDOWS:
        return InstallContext(InstallKind.WINDOWS_FOLDER, PureWindowsPath(executable).parent)
    path = Path(executable)
    if system is OperatingSystem.MACOS:
        bundle = next((parent for parent in path.parents if parent.suffix == ".app"), None)
        translocated = "/AppTranslocation/" in path.as_posix()
        return InstallContext(InstallKind.MACOS_APP, bundle or path.parent, translocated=translocated)
    return InstallContext(InstallKind.LINUX_FOLDER, path.parent)


__all__ = [
    "AUTO_CHECK_INTERVAL_SECONDS",
    "CheckMode",
    "CheckResult",
    "GITHUB_API",
    "InstallContext",
    "InstallKind",
    "MAX_NOTES_CHARACTERS",
    "MAX_PACKAGE_BYTES",
    "MAX_RELEASES_BYTES",
    "QUARANTINE_ATTRIBUTE",
    "Release",
    "ReleaseAsset",
    "UPDATE_CHECK_ENV",
    "UpdateError",
    "UpdateErrorKind",
    "UpdateOffer",
    "auto_check_due",
    "automatic_checks_allowed",
    "coerce_skipped_version",
    "download_mark_expected",
    "download_prefix",
    "evaluate_releases",
    "expected_checksum",
    "http_failure",
    "install_context",
    "macos_extended_attribute",
    "mark_as_downloaded",
    "parse_releases",
    "quarantine_value",
    "release_page_url",
    "releases_api_url",
    "source_revision",
    "updates_directory",
    "user_agent",
    "verify_package",
    "zone_identifier_text",
]
