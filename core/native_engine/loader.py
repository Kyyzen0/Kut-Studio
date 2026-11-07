"""Localiser, vérifier et lier ``libkutengine.dylib`` (moteur natif, ABI 1). Rien n'est exigé au démarrage de l'application.

Règles, toutes testées (``tests/test_native_engine_loader.py``) :

* **Où chercher.** Seulement deux endroits : la variable d'environnement ``KUT_ENGINE_LIB`` (réglage de développeur : un chemin
  **absolu**) puis ``<App>.app/Contents/Frameworks/libkutengine.dylib`` dans un bundle figé. **Jamais** un chemin lu dans un projet
  ``.kut``, un préréglage, un cache ou un réglage de l'utilisateur, jamais ``PATH`` ni ``DYLD_*``.
* **Version Mac App Store** (:func:`core.build_flavor.is_store`) : aucune bibliothèque native n'est chargée, variable
  d'environnement comprise. Le Store n'accepte que les bibliothèques signées et listées dans le bundle (``tools/check_store_bundle.py``,
  ``DYLIBS``) ; le moteur n'en fait pas partie tant qu'une décision ne l'y ajoute pas.
* **Avant le chargement**, l'en-tête Mach-O du fichier est lu : ce n'est pas un Mach-O, c'est un Mach-O d'une autre architecture que
  celle du processus, ou ce n'est pas une bibliothèque dynamique -> refus avec une raison, sans ``dlopen``.
* **ABI lue d'abord.** ``kut_engine_abi_version`` est liée et appelée seule ; une autre valeur que :data:`core.native_engine.abi.ABI_VERSION`
  refuse la bibliothèque, aucune autre fonction n'est liée. Ensuite chaque fonction reçoit ses ``argtypes`` et son ``restype`` ; une
  fonction manquante refuse la bibliothèque entière.
* **Jamais d'exception vers l'appelant de** :func:`available` : bibliothèque absente, illisible, mauvaise architecture ou mauvaise ABI
  donnent ``False`` et une :class:`Reason` lisible ; l'application garde son moniteur QRhi (le repli est le comportement par défaut).
* Messages fixes : aucun chemin ni détail du système n'est recopié dans une raison.
"""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from core import build_flavor

from . import abi

MAX_LIBRARY_PATH_BYTES = 4096
MAX_LIBRARY_FILE_BYTES = 512 << 20
"""Une bibliothèque du moteur fait quelques mégaoctets ; un fichier de plus de 512 Mo n'est pas elle."""
MAX_FAT_ARCHS = 16

_MH_MAGIC_64 = 0xFEEDFACF
_FAT_MAGIC, _FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
_MH_DYLIB = 6
_CPU_TYPES = {"arm64": 0x0100000C, "aarch64": 0x0100000C, "x86_64": 0x01000007, "amd64": 0x01000007}


@dataclass(frozen=True)
class Reason:
    """Pourquoi le moteur natif n'est pas disponible. ``code`` est stable (clé de traduction ``native_engine.reason.<code>`` dans
    ``ui/i18n_native_engine.py``) ; ``message`` est le texte français fixe ; ``detail`` ne porte que des nombres ou des noms de l'ABI."""

    code: str
    message: str
    detail: str = ""

    @property
    def translation_key(self) -> str:
        return f"native_engine.reason.{self.code}"


REASON_MESSAGES: dict[str, str] = {
    "platform": "Le moteur natif n'existe que sous macOS.",
    "store": "Le moteur natif n'est pas chargé dans la version Mac App Store.",
    "not_found": "Aucune bibliothèque du moteur natif n'est installée.",
    "bad_path": "Le chemin de la bibliothèque du moteur natif est invalide (il doit être absolu).",
    "unreadable": "La bibliothèque du moteur natif est illisible.",
    "not_macho": "Le fichier du moteur natif n'est pas une bibliothèque macOS.",
    "wrong_arch": "La bibliothèque du moteur natif est faite pour une autre architecture.",
    "not_dylib": "Le fichier du moteur natif n'est pas une bibliothèque dynamique.",
    "load_failed": "La bibliothèque du moteur natif n'a pas pu être chargée.",
    "no_abi_symbol": "La bibliothèque du moteur natif n'annonce pas sa version d'interface.",
    "abi_mismatch": "La bibliothèque du moteur natif a une autre version d'interface que celle attendue.",
    "incomplete": "La bibliothèque du moteur natif est incomplète.",
}
"""Toutes les raisons possibles, avec leur texte français ; chacune a sa traduction (fr/en/es) dans ``ui/i18n_native_engine.py``."""


def _reason(code: str, detail: str = "") -> Reason:
    return Reason(code, REASON_MESSAGES[code], detail)


class NativeEngineUnavailable(RuntimeError):
    """Le moteur natif ne peut pas être chargé ; ``reason`` dit pourquoi."""

    def __init__(self, reason: Reason) -> None:
        self.reason = reason
        super().__init__(reason.message)


# -- où chercher ----------------------------------------------------------------------------------------------------------


def bundled_library_path(executable: str | None = None, *, frozen: bool | None = None) -> Path | None:
    """``<App>.app/Contents/Frameworks/libkutengine.dylib`` dans un bundle figé, ``None`` ailleurs (sources, CI)."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return None
    exe = Path(sys.executable if executable is None else executable)
    return exe.resolve().parent.parent / "Frameworks" / abi.LIBRARY_NAME


def _env_path(value: str) -> Path | None:
    """Le chemin de ``KUT_ENGINE_LIB`` s'il est utilisable : absolu, sans octet nul, d'une longueur raisonnable."""
    if not value or "\x00" in value or len(value.encode("utf-8", "surrogateescape")) > MAX_LIBRARY_PATH_BYTES:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def candidate_paths(
    environ: Mapping[str, str] | None = None, *, executable: str | None = None, frozen: bool | None = None
) -> list[Path | None]:
    """Les emplacements à essayer, dans l'ordre : variable d'environnement puis bundle. Un ``None`` marque une variable présente mais
    invalide (elle est signalée, pas ignorée en silence). Vide : rien n'est configuré, rien n'est installé."""
    env = os.environ if environ is None else environ
    found: list[Path | None] = []
    configured = env.get(abi.ENV_VAR)
    if configured is not None and configured != "":
        found.append(_env_path(configured))
    bundled = bundled_library_path(executable, frozen=frozen)
    if bundled is not None:
        found.append(bundled)
    return found


# -- contrôle du fichier avant dlopen -----------------------------------------------------------------------------------


def _expected_cpu() -> int | None:
    return _CPU_TYPES.get(platform.machine().lower())


def _thin_header_problem(head: bytes, cpu: int | None) -> str | None:
    """``None`` si ``head`` est l'en-tête 64 bits d'une bibliothèque pour ``cpu``, sinon le code de la raison."""
    if len(head) < 16 or struct.unpack_from("<I", head, 0)[0] != _MH_MAGIC_64:
        return "not_macho"
    cputype, _subtype, filetype = struct.unpack_from("<iiI", head, 4)
    if cpu is None or cputype & 0xFFFFFFFF != cpu:
        return "wrong_arch"
    if filetype != _MH_DYLIB:
        return "not_dylib"
    return None


def macho_problem(path: Path, *, cpu: int | None = None) -> str | None:
    """Code de la raison pour laquelle ``path`` ne peut pas être la bibliothèque du moteur, ``None`` si son en-tête convient.

    Lit au plus quelques centaines d'octets (en-tête Mach-O ou table d'un fichier universel). Ne charge rien."""
    expected = _expected_cpu() if cpu is None else cpu
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
            if len(head) >= 8 and struct.unpack_from(">I", head, 0)[0] in (_FAT_MAGIC, _FAT_MAGIC_64):
                wide = struct.unpack_from(">I", head, 0)[0] == _FAT_MAGIC_64
                count = struct.unpack_from(">I", head, 4)[0]
                if count == 0 or count > MAX_FAT_ARCHS:
                    return "not_macho"
                entry = 32 if wide else 20
                handle.seek(8)
                table = handle.read(entry * count)
                if len(table) < entry * count:
                    return "not_macho"
                problems: list[str] = []
                for index in range(count):
                    base = index * entry
                    cputype = struct.unpack_from(">i", table, base)[0] & 0xFFFFFFFF
                    offset = struct.unpack_from(">Q" if wide else ">I", table, base + 8)[0]
                    if expected is None or cputype != expected:
                        continue
                    handle.seek(offset)
                    problem = _thin_header_problem(handle.read(32), expected)
                    if problem is None:
                        return None
                    problems.append(problem)
                return problems[0] if problems else "wrong_arch"
            return _thin_header_problem(head, expected)
    except (OSError, struct.error):
        return "unreadable"


# -- chargement -------------------------------------------------------------------------------------------------------


class NativeLibrary:
    """``libkutengine`` chargée, de la bonne ABI, toutes fonctions liées. Se construit par :func:`load_library`."""

    def __init__(self, cdll: ctypes.CDLL, path: Path, version_string: str) -> None:
        self.cdll = cdll
        self.path = path
        self.abi_version = abi.ABI_VERSION
        self.version_string = version_string


def _open(path: Path) -> NativeLibrary | Reason:
    try:
        if not path.is_file():
            return _reason("not_found")
        if path.stat().st_size > MAX_LIBRARY_FILE_BYTES:
            return _reason("not_macho")
    except OSError:
        return _reason("unreadable")
    problem = macho_problem(path)
    if problem is not None:
        return _reason(problem)
    try:
        cdll = ctypes.CDLL(str(path))
    except OSError:                                           # le détail du système peut citer un chemin : non recopié
        return _reason("load_failed")
    try:
        version_function = cdll.kut_engine_abi_version
    except AttributeError:
        return _reason("no_abi_symbol")
    version_function.restype = ctypes.c_uint32
    version_function.argtypes = []
    found = int(version_function())
    if found != abi.ABI_VERSION:
        return _reason("abi_mismatch", f"{found}/{abi.ABI_VERSION}")
    for name, (restype, argtypes) in abi.FUNCTIONS.items():
        try:
            function = getattr(cdll, name)
        except AttributeError:
            return _reason("incomplete", name)
        function.restype = restype
        function.argtypes = argtypes
    text = cdll.kut_engine_version_string()
    return NativeLibrary(cdll, path, text.decode("utf-8", "replace")[:64] if text else "")


def probe(
    environ: Mapping[str, str] | None = None, *, executable: str | None = None, frozen: bool | None = None
) -> NativeLibrary | Reason:
    """Cherche, vérifie et lie la bibliothèque. Rend la :class:`NativeLibrary` ou la :class:`Reason` du premier emplacement essayé qui échoue
    (celui que l'utilisateur ou le bundle désigne). N'élève jamais."""
    if sys.platform != "darwin":
        return _reason("platform")
    if build_flavor.is_store():
        return _reason("store")
    candidates = candidate_paths(environ, executable=executable, frozen=frozen)
    if not candidates:
        return _reason("not_found")
    first_failure: Reason | None = None
    for candidate in candidates:
        result = _reason("bad_path") if candidate is None else _open(candidate)
        if isinstance(result, NativeLibrary):
            return result
        first_failure = first_failure or result
    return first_failure or _reason("not_found")


_cached: NativeLibrary | Reason | None = None


def reset() -> None:
    """Oublie le résultat mémorisé (tests, ou après avoir installé la bibliothèque). La bibliothèque déjà chargée reste chargée."""
    global _cached
    _cached = None


def _result() -> NativeLibrary | Reason:
    global _cached
    if _cached is None:
        _cached = probe()
    return _cached


def available() -> bool:
    """``True`` si le moteur natif est chargeable ici. Ne lève jamais. Le résultat est mémorisé (:func:`reset` l'oublie)."""
    return isinstance(_result(), NativeLibrary)


def unavailable_reason() -> Reason | None:
    """La raison pour laquelle :func:`available` est faux, ``None`` si le moteur est disponible."""
    result = _result()
    return result if isinstance(result, Reason) else None


def load_library() -> NativeLibrary:
    """La bibliothèque chargée, ou :class:`NativeEngineUnavailable` (avec sa :class:`Reason`)."""
    result = _result()
    if isinstance(result, Reason):
        raise NativeEngineUnavailable(result)
    return result
