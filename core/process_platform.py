"""Identité vérifiable d'un processus, arrêt « seulement si c'est bien lui » et objet Job Windows.

Un PID seul ne désigne pas un processus : il est réutilisé dès que le précédent est récolté. Avant d'arrêter quoi
que ce soit, la supervision (:mod:`core.process_supervisor`) compare un **jeton d'identité** relu sur le système à
celui noté au lancement :

=========  ===============================================================  ==============================
Système    Jeton (heure de début du processus)                              Nom d'exécutable
=========  ===============================================================  ==============================
Linux      ``/proc/<pid>/stat`` champ 22 (tics depuis le démarrage),        ``/proc/<pid>/stat`` champ 2
           préfixé par l'identifiant de démarrage du noyau
macOS      ``proc_pidinfo(PROC_PIDTBSDINFO)`` : secondes et microsecondes   ``pbi_name`` (ou ``pbi_comm``)
Windows    ``GetProcessTimes`` : heure de création (unités de 100 ns)      ``QueryFullProcessImageNameW``
=========  ===============================================================  ==============================

Un processus **zombie** (terminé, pas encore récolté) est considéré comme disparu : il répond encore à
``kill(pid, 0)`` mais ne tourne plus. Une identité illisible (droits, API absente) n'est **jamais** tuée.

Aucune bibliothèque native n'est chargée à l'import : ``ctypes`` n'ouvre ``libproc`` ou ``kernel32`` qu'au
premier besoin. Les opérations sont des objets (:class:`ProcessOps`) pour que les tests injectent une fausse
plateforme.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import re
import signal
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger("kut_studio.process")


@dataclass(frozen=True)
class ProcessIdentity:
    """Ce qui distingue un processus de tout autre ayant eu, ou ayant plus tard, le même PID."""

    pid: int
    token: str
    """Heure de début (format propre à la plateforme), comparée telle quelle ; vide = inconnue."""
    name: str
    """Nom d'exécutable observé, garde-fou supplémentaire (comparé après :func:`normalized_name`)."""


class ProbeState(Enum):
    ALIVE = "alive"
    GONE = "gone"
    """Inexistant, ou zombie : ne tourne plus."""
    UNKNOWN = "unknown"
    """Existe peut-être, mais illisible (droits, API indisponible) : jamais tué."""


@dataclass(frozen=True)
class Probe:
    state: ProbeState
    identity: ProcessIdentity | None = None
    detail: str = ""


class KillOutcome(Enum):
    KILLED = "killed"
    ALREADY_GONE = "gone"
    NOT_OURS = "not-ours"
    """Même PID mais autre processus (jeton ou nom différent) : épargné."""
    UNVERIFIABLE = "unverifiable"
    """Identité illisible : épargné."""
    FAILED = "failed"


class ProcessOps(Protocol):
    """Opérations de plateforme dont dépend la supervision (injectables dans les tests)."""

    def probe(self, pid: int) -> Probe: ...

    def kill_if_same(self, expected: ProcessIdentity) -> KillOutcome: ...


_VERSION_SUFFIX = re.compile(r"[\d.]+$")
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})


def normalized_name(name: str) -> str:
    """Nom comparable : casse ignorée, sans dossier, sans ``.exe`` ni suffixe de version, shells confondus.

    Un lanceur peut se ré-exécuter sous un autre nom en gardant PID et heure de début : le Python « framework » de
    macOS passe de ``python3.14`` à ``Python``, le ``/bin/sh`` de macOS devient ``bash`` (ou ``zsh``, ``dash``).
    Une comparaison exacte épargnerait alors un enfant légitime.
    """
    base = name.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    base = _VERSION_SUFFIX.sub("", base) or base
    return "sh" if base in _SHELLS else base


def same_process(expected: ProcessIdentity, current: ProcessIdentity) -> bool:
    """Même PID, même jeton (non vide) et même nom normalisé."""
    return (
        expected.pid == current.pid
        and bool(expected.token)
        and expected.token == current.token
        and normalized_name(expected.name) == normalized_name(current.name)
    )


def kill_refusal(expected: ProcessIdentity, probe: Probe) -> KillOutcome | None:
    """Raison de **ne pas** tuer ``expected`` d'après ``probe``, ou ``None`` si son identité est vérifiée.

    Seule règle de décision de toutes les plateformes (et des fausses plateformes des tests)."""
    if probe.state is ProbeState.GONE:
        return KillOutcome.ALREADY_GONE
    if probe.state is ProbeState.UNKNOWN or probe.identity is None:
        return KillOutcome.UNVERIFIABLE
    if not same_process(expected, probe.identity):
        return KillOutcome.NOT_OURS
    return None


# --- POSIX ----------------------------------------------------------------------------------------------------------


def _open_pidfd(pid: int) -> int | None:
    """Descripteur de processus Linux (≥ 5.3) : le signal ira à ce processus-là, même si le PID est réutilisé."""
    opener = getattr(os, "pidfd_open", None)
    if opener is None or getattr(signal, "pidfd_send_signal", None) is None:
        return None
    try:
        return int(opener(pid))
    except OSError:  # disparu, noyau trop ancien, filtre seccomp : repli sur kill()
        return None


class _PosixOps:
    """Arrêt POSIX : ``SIGKILL`` après vérification de l'identité."""

    def probe(self, pid: int) -> Probe:  # pragma: no cover - redéfini par chaque plateforme
        raise NotImplementedError

    def kill_if_same(self, expected: ProcessIdentity) -> KillOutcome:
        # Sous Linux, le descripteur est ouvert AVANT la vérification : si le PID change de propriétaire entre-temps,
        # la vérification échoue (autre jeton) ou le signal part vers l'ancien processus, déjà mort. Ailleurs, la
        # fenêtre entre vérification et kill() est de quelques microsecondes (voir docs/process-supervision.md).
        pidfd = _open_pidfd(expected.pid)
        try:
            refusal = kill_refusal(expected, self.probe(expected.pid))
            if refusal is not None:
                return refusal
            try:
                if pidfd is not None:
                    getattr(signal, "pidfd_send_signal")(pidfd, signal.SIGKILL)
                else:
                    os.kill(expected.pid, signal.SIGKILL)
            except ProcessLookupError:
                return KillOutcome.ALREADY_GONE
            except OSError as error:
                LOGGER.warning("Supervision : arrêt du processus %d refusé (%s)", expected.pid, error)
                return KillOutcome.FAILED
            return KillOutcome.KILLED
        finally:
            if pidfd is not None:
                os.close(pidfd)


def _exists(pid: int) -> ProbeState:
    """Repli sans API d'identité : existe (identité inconnue) ou disparu."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProbeState.GONE
    except OSError:
        return ProbeState.UNKNOWN
    return ProbeState.UNKNOWN


class LinuxOps(_PosixOps):
    """``/proc/<pid>/stat`` : état (champ 3), heure de début en tics depuis le démarrage (champ 22), nom (champ 2)."""

    def __init__(self, proc_root: str | os.PathLike[str] = "/proc") -> None:
        self._proc = Path(proc_root)
        self._boot_id: str | None = None

    def boot_id(self) -> str:
        """Identifiant du démarrage courant : un jeton relatif au démarrage n'a de sens que dans ce démarrage."""
        if self._boot_id is None:
            try:
                value = (self._proc / "sys" / "kernel" / "random" / "boot_id").read_text().strip()
            except OSError:
                value = ""
            if not value:
                try:
                    for line in (self._proc / "stat").read_text().splitlines():
                        if line.startswith("btime "):
                            value = "btime" + line.split()[1]
                            break
                except OSError:
                    value = ""
            self._boot_id = value.replace("-", "") or "boot"
        return self._boot_id

    def probe(self, pid: int) -> Probe:
        try:
            raw = (self._proc / str(int(pid)) / "stat").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            return Probe(ProbeState.GONE)
        except PermissionError as error:
            return Probe(ProbeState.UNKNOWN, detail=str(error))
        except OSError as error:
            state = ProbeState.GONE if error.errno == errno.ESRCH else ProbeState.UNKNOWN
            return Probe(state, detail=str(error))
        # Le nom (champ 2) est entre parenthèses et peut lui-même contenir espaces et parenthèses.
        opening, closing = raw.find(b"("), raw.rfind(b")")
        fields = raw[closing + 1:].split() if closing > opening >= 0 else []
        if len(fields) < 20:
            return Probe(ProbeState.UNKNOWN, detail="stat illisible")
        if fields[0] in (b"Z", b"X", b"x"):
            return Probe(ProbeState.GONE, detail="zombie")
        name = raw[opening + 1:closing].decode("utf-8", "replace")
        token = f"{self.boot_id()}-{fields[19].decode('ascii', 'replace')}"
        return Probe(ProbeState.ALIVE, ProcessIdentity(int(pid), token, name))


_PROC_PIDTBSDINFO = 3
_SZOMB = 5


class _BsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` de ``<sys/proc_info.h>`` (136 octets)."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32), ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32), ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class MacOps(_PosixOps):
    """``proc_pidinfo(PROC_PIDTBSDINFO)`` de ``libproc`` : état, heure de début (µs) et nom en un appel (~5 µs)."""

    def __init__(self) -> None:
        self._lib: Any = None
        self._loaded = False
        self._lock = threading.Lock()

    def _libproc(self) -> Any:
        with self._lock:
            if not self._loaded:
                self._loaded = True
                for candidate in ("/usr/lib/libproc.dylib", None):
                    try:
                        library = ctypes.CDLL(candidate, use_errno=True)
                        function = library.proc_pidinfo
                    except (OSError, AttributeError):
                        continue
                    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
                    function.restype = ctypes.c_int
                    self._lib = library
                    break
                if self._lib is None:
                    LOGGER.warning("Supervision : libproc indisponible, identité des processus illisible")
            return self._lib

    def probe(self, pid: int) -> Probe:
        library = self._libproc()
        if library is None:
            return Probe(_exists(pid), detail="libproc indisponible")
        info = _BsdInfo()
        ctypes.set_errno(0)
        size = library.proc_pidinfo(int(pid), _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info):
            code = ctypes.get_errno()
            if code == errno.ESRCH:  # inexistant, ou zombie (macOS refuse alors la lecture)
                return Probe(ProbeState.GONE)
            if code == errno.EPERM:
                return Probe(ProbeState.UNKNOWN, detail="accès refusé")
            return Probe(_exists(pid), detail=f"proc_pidinfo : erreur {code}")
        if info.pbi_status == _SZOMB:
            return Probe(ProbeState.GONE, detail="zombie")
        raw_name = info.pbi_name or info.pbi_comm
        name = raw_name.decode("utf-8", "replace")
        token = f"{int(info.pbi_start_tvsec)}.{int(info.pbi_start_tvusec):06d}"
        return Probe(ProbeState.ALIVE, ProcessIdentity(int(pid), token, name))


class FallbackOps(_PosixOps):
    """Plateforme sans API d'identité connue : on sait dire « disparu », jamais « c'est bien lui »."""

    def probe(self, pid: int) -> Probe:
        return Probe(_exists(pid), detail="identité non prise en charge sur cette plateforme")


# --- Windows --------------------------------------------------------------------------------------------------------

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
WAIT_OBJECT_0 = 0
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _BasicLimits(ctypes.Structure):
    """``JOBOBJECT_BASIC_LIMIT_INFORMATION``."""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` (144 octets en 64 bits)."""

    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def kill_on_close_limits() -> _ExtendedLimits:
    """Limites d'un job dont la fermeture de la dernière poignée tue tous les processus."""
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return limits


class Kernel32:
    """Appels ``kernel32`` utiles à la supervision, via ``ctypes`` (aucune dépendance)."""

    def __init__(self) -> None:
        dll = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        signatures: dict[str, tuple[list[Any], Any]] = {
            "OpenProcess": ([dword, boolean, dword], handle),
            "CloseHandle": ([handle], boolean),
            "WaitForSingleObject": ([handle, dword], dword),
            "TerminateProcess": ([handle, ctypes.c_uint], boolean),
            "GetProcessTimes": ([handle] + [ctypes.POINTER(_FileTime)] * 4, boolean),
            "QueryFullProcessImageNameW": ([handle, dword, ctypes.c_wchar_p, ctypes.POINTER(dword)], boolean),
            "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], handle),
            "SetInformationJobObject": ([handle, ctypes.c_int, ctypes.c_void_p, dword], boolean),
            "AssignProcessToJobObject": ([handle, handle], boolean),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(dll, name)
            function.argtypes = arguments
            function.restype = result
        self._dll = dll

    def last_error(self) -> int:
        return int(getattr(ctypes, "get_last_error")())

    def open_process(self, pid: int, access: int) -> int | None:
        return self._dll.OpenProcess(access, False, int(pid)) or None

    def close(self, handle: int) -> None:
        self._dll.CloseHandle(handle)

    def wait(self, handle: int, milliseconds: int) -> int:
        return int(self._dll.WaitForSingleObject(handle, milliseconds))

    def terminate(self, handle: int, code: int) -> bool:
        return bool(self._dll.TerminateProcess(handle, code))

    def creation_time(self, handle: int) -> int | None:
        times = [_FileTime() for _ in range(4)]
        if not self._dll.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        return (int(times[0].high) << 32) | int(times[0].low)

    def image_name(self, handle: int) -> str:
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not self._dll.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value

    def create_job(self) -> int | None:
        # Attributs de sécurité NULL : poignée non héritable. Un enfant qui en hériterait garderait le job ouvert
        # après la mort de l'application, et plus rien ne serait tué.
        return self._dll.CreateJobObjectW(None, None) or None

    def set_kill_on_close(self, job: int) -> bool:
        limits = kill_on_close_limits()
        return bool(self._dll.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
        ))

    def assign_to_job(self, job: int, process: int) -> bool:
        return bool(self._dll.AssignProcessToJobObject(job, process))


class WindowsOps:
    """Identité par poignée de processus : vérification et ``TerminateProcess`` sur la **même** poignée.

    Tant qu'une poignée est ouverte, le PID ne peut pas être réutilisé : il n'y a aucune course entre la
    vérification et l'arrêt.
    """

    def __init__(self, api: Kernel32 | None = None) -> None:
        self._api_instance = api
        self._lock = threading.Lock()

    def _api(self) -> Kernel32:
        with self._lock:
            if self._api_instance is None:
                self._api_instance = Kernel32()
            return self._api_instance

    def _identity(self, api: Kernel32, handle: int, pid: int) -> Probe:
        if api.wait(handle, 0) == WAIT_OBJECT_0:  # terminé (la poignée garde l'objet, comme un zombie)
            return Probe(ProbeState.GONE)
        created = api.creation_time(handle)
        if created is None:
            return Probe(ProbeState.UNKNOWN, detail=f"GetProcessTimes : erreur {api.last_error()}")
        name = api.image_name(handle).replace("\\", "/").rsplit("/", 1)[-1]
        return Probe(ProbeState.ALIVE, ProcessIdentity(int(pid), str(created), name))

    def probe(self, pid: int) -> Probe:
        api = self._api()
        handle = api.open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)
        if not handle:
            code = api.last_error()
            state = ProbeState.GONE if code == ERROR_INVALID_PARAMETER else ProbeState.UNKNOWN
            return Probe(state, detail=f"OpenProcess : erreur {code}")
        try:
            return self._identity(api, handle, pid)
        finally:
            api.close(handle)

    def kill_if_same(self, expected: ProcessIdentity) -> KillOutcome:
        api = self._api()
        access = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        handle = api.open_process(expected.pid, access)
        if not handle:
            code = api.last_error()
            return KillOutcome.ALREADY_GONE if code == ERROR_INVALID_PARAMETER else KillOutcome.UNVERIFIABLE
        try:
            refusal = kill_refusal(expected, self._identity(api, handle, expected.pid))
            if refusal is not None:
                return refusal
            if not api.terminate(handle, 1):
                if api.wait(handle, 0) == WAIT_OBJECT_0:
                    return KillOutcome.ALREADY_GONE
                LOGGER.warning("Supervision : TerminateProcess(%d) refusé (erreur %d)", expected.pid, api.last_error())
                return KillOutcome.FAILED
            api.wait(handle, 2000)
            return KillOutcome.KILLED
        finally:
            api.close(handle)


class JobApi(Protocol):
    """Sous-ensemble de :class:`Kernel32` utilisé par :class:`KillOnCloseJob` (faux objet dans les tests)."""

    def last_error(self) -> int: ...

    def open_process(self, pid: int, access: int) -> int | None: ...

    def close(self, handle: int) -> None: ...

    def wait(self, handle: int, milliseconds: int) -> int: ...

    def create_job(self) -> int | None: ...

    def set_kill_on_close(self, job: int) -> bool: ...

    def assign_to_job(self, job: int, process: int) -> bool: ...


class KillOnCloseJob:
    """Objet Job Windows d'une instance : ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.

    Créé au premier enfant, jamais hérité. Quand l'application meurt, par n'importe quelle voie, le noyau ferme sa
    dernière poignée et tue tous les processus du job. Chaque échec est journalisé et laisse l'enfant tourner sans
    cette protection (le registre et le balayage au démarrage prennent alors le relais) : jamais une exception.
    """

    def __init__(self, api: JobApi | None = None) -> None:
        self._api_instance: JobApi | None = api
        self._handle: int | None = None
        self._disabled = False
        self._lock = threading.Lock()
        self._warned: set[str] = set()

    @property
    def active(self) -> bool:
        return self._handle is not None

    def _api(self) -> JobApi:
        if self._api_instance is None:
            self._api_instance = Kernel32()
        return self._api_instance

    def _warn_once(self, key: str, message: str, *args: object) -> None:
        if key in self._warned:
            LOGGER.debug("Supervision : " + message, *args)
            return
        self._warned.add(key)
        LOGGER.warning("Supervision : " + message, *args)

    def assign(self, pid: int) -> bool:
        """Place ``pid`` dans le job (créé au premier appel). ``False`` = enfant non protégé par le job."""
        try:
            with self._lock:
                if self._disabled:
                    return False
                api = self._api()
                if self._handle is None:
                    job = api.create_job()
                    if not job:
                        self._disabled = True
                        self._warn_once("create", "objet Job refusé (erreur %d) ; repli sur le registre", api.last_error())
                        return False
                    if not api.set_kill_on_close(job):
                        code = api.last_error()
                        api.close(job)
                        self._disabled = True
                        self._warn_once("limits", "KILL_ON_JOB_CLOSE refusé (erreur %d) ; repli sur le registre", code)
                        return False
                    self._handle = job
                process = api.open_process(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE)
                if not process:
                    code = api.last_error()
                    if code != ERROR_INVALID_PARAMETER:  # 87 : déjà terminé (sonde éclair), rien à protéger
                        self._warn_once(f"open-{code}", "processus %d inaccessible pour le job (erreur %d)", pid, code)
                    return False
                try:
                    if api.assign_to_job(self._handle, process):
                        return True
                    code = api.last_error()
                    # Un processus déjà terminé est refusé avec ERROR_ACCESS_DENIED : ce n'est pas un échec du job.
                    finished = api.wait(process, 0) == WAIT_OBJECT_0
                finally:
                    api.close(process)
                if finished:
                    return False
                reason = "déjà dans un job sans imbrication possible ?" if code == ERROR_ACCESS_DENIED else "refus"
                self._warn_once(
                    f"assign-{code}", "processus %d hors du job (erreur %d : %s) ; repli sur le registre",
                    pid, code, reason,
                )
                return False
        except Exception:  # noqa: BLE001 - la supervision n'empêche jamais de lancer FFmpeg
            if "exception" not in self._warned:
                self._warned.add("exception")
                LOGGER.exception("Supervision : objet Job indisponible ; repli sur le registre")
            return False

    def close(self) -> None:
        """Ferme la poignée : **tue** tous les processus du job."""
        with self._lock:
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    self._api().close(handle)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Supervision : fermeture de l'objet Job")


# --- Choix de la plateforme -----------------------------------------------------------------------------------------

_native: ProcessOps | None = None
_native_lock = threading.Lock()


def native_ops(platform_name: str | None = None) -> ProcessOps:
    """Opérations de la plateforme courante (instance partagée) ; ``platform_name`` en construit une autre."""
    global _native
    if platform_name is not None:
        return _ops_for(platform_name)
    with _native_lock:
        if _native is None:
            _native = _ops_for(sys.platform)
        return _native


def _ops_for(platform_name: str) -> ProcessOps:
    if platform_name.startswith("linux"):
        return LinuxOps()
    if platform_name == "darwin":
        return MacOps()
    if platform_name.startswith("win"):
        return WindowsOps()
    return FallbackOps()


__all__ = [
    "FallbackOps", "JobApi", "Kernel32", "KillOnCloseJob", "KillOutcome", "LinuxOps", "MacOps", "Probe",
    "ProbeState", "ProcessIdentity", "ProcessOps", "WindowsOps", "kill_on_close_limits", "kill_refusal",
    "native_ops", "normalized_name", "same_process",
]
