"""Surveillance de la mémoire et réaction à la pression.

Kut-Studio ne doit jamais s'arrêter parce qu'un projet est lourd. Le moniteur
lit ce que le système expose (sans dépendance) :

- **Linux** : ``/proc/meminfo`` (``MemAvailable``) et ``/proc/self/statm`` ;
- **macOS** : pourcentage de mémoire libre selon le noyau
  (``kern.memorystatus_level``, celui de ``memory_pressure``), mémoire physique
  (``hw.memsize``) ; le niveau ``kern.memorystatus_vm_pressure_level`` ne sert
  qu'au niveau critique (son niveau « warn » apparaît avec 40 % de mémoire libre) ;
- **Windows** : ``GlobalMemoryStatusEx`` (charge mémoire, mémoire disponible).

La VRAM n'est pas lisible de façon portable : sur Apple Silicon la mémoire
est unifiée (comptée ci-dessus) ; ailleurs on suit **nos** textures (cache GPU
+ textures de travail) et, si le backend Qt les donne, ses statistiques.

Politique (:func:`pressure_actions`), du plus doux au plus fort :

1. ``warning`` : purger le cache GPU et le cache mémoire de l'aperçu ;
2. ``critical`` : en plus, baisser la qualité de l'aperçu et revenir au rendu
   CPU (les textures du GPU sont libérées).

L'export n'est jamais touché.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

NORMAL = "normal"
WARNING = "warning"
CRITICAL = "critical"

WARNING_AVAILABLE_RATIO = 0.10
CRITICAL_AVAILABLE_RATIO = 0.04


@dataclass(frozen=True)
class MemoryStatus:
    total_bytes: int | None = None
    available_bytes: int | None = None
    process_bytes: int | None = None
    pressure: str = NORMAL
    source: str = ""

    @property
    def available_ratio(self) -> float | None:
        if not self.total_bytes or self.available_bytes is None:
            return None
        return self.available_bytes / self.total_bytes


def classify(total: int | None, available: int | None) -> str:
    if not total or available is None:
        return NORMAL
    ratio = available / total
    if ratio <= CRITICAL_AVAILABLE_RATIO:
        return CRITICAL
    if ratio <= WARNING_AVAILABLE_RATIO:
        return WARNING
    return NORMAL


def _linux() -> MemoryStatus:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[name] = int(parts[0]) * 1024
    except OSError:
        return MemoryStatus(source="unavailable")
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    process = None
    try:
        with open("/proc/self/statm", encoding="utf-8") as handle:
            process = int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    return MemoryStatus(total, available, process, classify(total, available), "proc")


def _sysctl_int(name: str) -> int | None:
    import ctypes
    import ctypes.util

    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        value = ctypes.c_uint64(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            return None
        return int(value.value) if size.value == 8 else int(value.value & 0xFFFFFFFF)
    except Exception:
        return None


def _darwin() -> MemoryStatus:
    total = _sysctl_int("hw.memsize")
    free_percent = _sysctl_int("kern.memorystatus_level")
    available = None
    if total and free_percent is not None and 0 <= free_percent <= 100:
        available = total * free_percent // 100
    pressure = classify(total, available)
    if _sysctl_int("kern.memorystatus_vm_pressure_level") == 4:
        pressure = CRITICAL
    return MemoryStatus(total, available, None, pressure, "sysctl")


def _windows() -> MemoryStatus:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return MemoryStatus(source="unavailable")
    except Exception:
        return MemoryStatus(source="unavailable")
    total, available = int(status.ullTotalPhys), int(status.ullAvailPhys)
    return MemoryStatus(total, available, None, classify(total, available), "kernel32")


def read_memory_status(platform_name: str | None = None) -> MemoryStatus:
    """État courant ; ne lève jamais (``source="unavailable"`` si rien n'est lisible)."""
    name = (platform_name or sys.platform).lower()
    try:
        if name.startswith("linux"):
            return _linux()
        if name == "darwin":
            return _darwin()
        if name.startswith("win"):
            return _windows()
    except Exception:
        pass
    return MemoryStatus(source="unavailable")


@dataclass(frozen=True)
class PressureActions:
    purge_gpu_cache: bool = False
    purge_memory_cache: bool = False
    reduce_quality: bool = False
    disable_gpu: bool = False

    @property
    def any(self) -> bool:
        return self.purge_gpu_cache or self.purge_memory_cache or self.reduce_quality or self.disable_gpu


def pressure_actions(status: MemoryStatus, *, gpu_active: bool) -> PressureActions:
    if status.pressure == CRITICAL:
        return PressureActions(True, True, True, gpu_active)
    if status.pressure == WARNING:
        return PressureActions(True, True, False, False)
    return PressureActions()


class MemoryWatch:
    """Réagit **une fois** par montée de pression (pas d'action répétée à chaque relevé)."""

    def __init__(self, reader: Callable[[], MemoryStatus] = read_memory_status) -> None:
        self._reader = reader
        self.last = MemoryStatus()
        self._level = NORMAL

    def poll(self, *, gpu_active: bool) -> PressureActions:
        status = self._reader()
        self.last = status
        order = {NORMAL: 0, WARNING: 1, CRITICAL: 2}
        rising = order.get(status.pressure, 0) > order.get(self._level, 0)
        self._level = status.pressure
        return pressure_actions(status, gpu_active=gpu_active) if rising else PressureActions()


__all__ = [
    "CRITICAL",
    "NORMAL",
    "WARNING",
    "MemoryStatus",
    "MemoryWatch",
    "PressureActions",
    "classify",
    "pressure_actions",
    "read_memory_status",
]
