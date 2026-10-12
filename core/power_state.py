"""État énergie / chauffe de la machine (ADR-0001 étape 1).

Lit ce que l'OS expose, sans dépendance : batterie, mode économie, pression
thermique. Une valeur inconnue reste ``None`` / ``unknown`` — on n'invente
pas un signal. Le résultat peut être injecté (tests) ou forcé par
``KUT_STUDIO_POWER`` (ex. ``battery,low_power,thermal=serious``).

L'export n'est jamais concerné : ces signaux ne servent qu'à l'aperçu
(un cran de moins, préchargement suspendu sur batterie).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

LOGGER = logging.getLogger("kut_studio.power_state")

THERMAL_NOMINAL = "nominal"
THERMAL_FAIR = "fair"
THERMAL_SERIOUS = "serious"
THERMAL_CRITICAL = "critical"
THERMAL_UNKNOWN = "unknown"

THERMAL_LEVELS: tuple[str, ...] = (
    THERMAL_NOMINAL,
    THERMAL_FAIR,
    THERMAL_SERIOUS,
    THERMAL_CRITICAL,
    THERMAL_UNKNOWN,
)

# Sous ces niveaux, Auto descend d'un cran (ADR : mode économie / chauffe).
_STEP_DOWN_THERMAL = frozenset({THERMAL_SERIOUS, THERMAL_CRITICAL})


@dataclass(frozen=True)
class PowerState:
    """Conditions observées ; ``None`` = l'OS ne donne pas l'info."""

    on_battery: bool | None = None
    low_power_mode: bool | None = None
    thermal: str = THERMAL_UNKNOWN
    source: str = ""

    @property
    def known(self) -> bool:
        return self.source not in {"", "unavailable", "override-empty"}


def power_requires_step_down(state: PowerState) -> bool:
    """``True`` : un cran de moins (batterie, économie d'énergie, ou chauffe)."""
    if state.on_battery is True:
        return True
    if state.low_power_mode is True:
        return True
    return state.thermal in _STEP_DOWN_THERMAL


def prefetch_suspended(state: PowerState) -> bool:
    """Préchargement des segments fidèles suspendu sur batterie (ADR)."""
    return state.on_battery is True


def parse_power_override(raw: str) -> PowerState | None:
    """Parse ``KUT_STUDIO_POWER`` ; chaîne vide / ``off`` → pas de forçage."""
    text = str(raw or "").strip().lower()
    if not text or text in {"off", "0", "false", "no"}:
        return None
    on_battery: bool | None = None
    low_power: bool | None = None
    thermal = THERMAL_UNKNOWN
    for token in text.replace(";", ",").split(","):
        part = token.strip()
        if not part:
            continue
        if part in {"battery", "on_battery", "batt"}:
            on_battery = True
        elif part in {"ac", "mains", "plugged"}:
            on_battery = False
        elif part in {"low_power", "low-power", "saver", "eco"}:
            low_power = True
        elif part in {"full_power", "high_power"}:
            low_power = False
        elif part.startswith("thermal="):
            value = part.split("=", 1)[1].strip()
            thermal = value if value in THERMAL_LEVELS else THERMAL_UNKNOWN
        elif part in THERMAL_LEVELS:
            thermal = part
    return PowerState(
        on_battery=on_battery,
        low_power_mode=low_power,
        thermal=thermal,
        source="env",
    )


def detect_power_state(
    *,
    environment: Mapping[str, str] | None = None,
) -> PowerState:
    """Lit l'état courant (ou le forçage d'environnement)."""
    env = environment if environment is not None else os.environ
    override = parse_power_override(str(env.get("KUT_STUDIO_POWER", "")))
    if override is not None:
        return override
    if sys.platform.startswith("linux"):
        return _linux()
    if sys.platform == "darwin":
        return _darwin()
    if sys.platform.startswith("win"):
        return _windows()
    return PowerState(source="unavailable")


def _linux() -> PowerState:
    root = "/sys/class/power_supply"
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    # Le chargeur externe (``online``) prime : avec un seuil de charge, la
    # batterie reste souvent en ``Not charging`` alors qu'on est sur secteur.
    on_battery = _linux_on_battery(root, names)
    thermal = _linux_thermal()
    low_power = _linux_low_power()
    source = "sysfs" if on_battery is not None or thermal != THERMAL_UNKNOWN else "unavailable"
    if source == "unavailable" and low_power is None:
        return PowerState(source="unavailable")
    return PowerState(
        on_battery=on_battery,
        low_power_mode=low_power,
        thermal=thermal,
        source="sysfs",
    )


def _linux_on_battery(root: str, names: list[str]) -> bool | None:
    """``True`` sur batterie, ``False`` sur secteur, ``None`` si illisible."""
    charger_online: bool | None = None
    for name in names:
        base = os.path.join(root, name)
        try:
            kind = _read_text(os.path.join(base, "type")).strip().lower()
        except OSError:
            continue
        if kind not in {"mains", "usb", "wireless", "ups"}:
            continue
        try:
            online = _read_text(os.path.join(base, "online")).strip()
        except OSError:
            continue
        if online == "1":
            return False
        if online == "0":
            charger_online = False
    if charger_online is False:
        return True
    # Pas de chargeur exposé : repli sur le statut batterie. ``not charging``
    # n'implique pas la batterie (seuil de charge sur secteur).
    for name in names:
        base = os.path.join(root, name)
        try:
            kind = _read_text(os.path.join(base, "type")).strip().lower()
        except OSError:
            continue
        if kind != "battery":
            continue
        try:
            status = _read_text(os.path.join(base, "status")).strip().lower()
        except OSError:
            continue
        if status == "discharging":
            return True
        if status in {"charging", "full", "charged", "not charging"}:
            return False
    return None


def _linux_thermal() -> str:
    """Pression thermique d'après les zones sysfs (meilleure temp / trip)."""
    root = "/sys/class/thermal"
    try:
        zones = [name for name in os.listdir(root) if name.startswith("thermal_zone")]
    except OSError:
        return THERMAL_UNKNOWN
    hottest_c: float | None = None
    for zone in zones:
        path = os.path.join(root, zone, "temp")
        try:
            raw = int(_read_text(path).strip())
        except (OSError, ValueError):
            continue
        # Les noyaux exposent des milli-°C (ex. 45000) ou parfois des °C.
        celsius = raw / 1000.0 if raw > 200 else float(raw)
        if hottest_c is None or celsius > hottest_c:
            hottest_c = celsius
    if hottest_c is None:
        return THERMAL_UNKNOWN
    if hottest_c >= 95.0:
        return THERMAL_CRITICAL
    if hottest_c >= 85.0:
        return THERMAL_SERIOUS
    if hottest_c >= 75.0:
        return THERMAL_FAIR
    return THERMAL_NOMINAL


def _linux_low_power() -> bool | None:
    """Heuristique : préférence cpufreq « power » / « balance_power »."""
    root = "/sys/devices/system/cpu/cpufreq"
    try:
        policies = [name for name in os.listdir(root) if name.startswith("policy")]
    except OSError:
        return None
    seen = False
    for policy in policies:
        path = os.path.join(root, policy, "energy_performance_preference")
        try:
            preference = _read_text(path).strip().lower()
        except OSError:
            continue
        seen = True
        if preference in {"power", "balance_power"}:
            return True
    return False if seen else None


def _darwin() -> PowerState:
    """Batterie / économie / chauffe via ``pmset`` (supervisé, court)."""
    from .process_supervisor import supervised_run

    on_battery: bool | None = None
    low_power: bool | None = None
    thermal = THERMAL_UNKNOWN
    try:
        batt = supervised_run(
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, Exception) as error:  # noqa: BLE001 — pmset absent / timeout
        LOGGER.debug("pmset batt illisible : état énergie inconnu", exc_info=True)
        batt = None
        del error
    if batt is not None and getattr(batt, "returncode", 1) == 0:
        text = str(getattr(batt, "stdout", "") or "")
        lower = text.lower()
        if "battery power" in lower:
            on_battery = True
        elif "ac power" in lower:
            on_battery = False
    try:
        general = supervised_run(
            ["pmset", "-g"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, Exception):  # noqa: BLE001
        LOGGER.debug("pmset -g illisible : mode économie inconnu", exc_info=True)
        general = None
    if general is not None and getattr(general, "returncode", 1) == 0:
        for line in str(getattr(general, "stdout", "") or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "lowpowermode":
                low_power = parts[1].strip() in {"1", "true", "yes"}
                break
    try:
        therm = supervised_run(
            ["pmset", "-g", "therm"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, Exception):  # noqa: BLE001
        LOGGER.debug("pmset therm illisible : chauffe inconnue", exc_info=True)
        therm = None
    if therm is not None and getattr(therm, "returncode", 1) == 0:
        thermal = _darwin_thermal_from_pmset(str(getattr(therm, "stdout", "") or ""))
    if on_battery is None and low_power is None and thermal == THERMAL_UNKNOWN:
        return PowerState(source="unavailable")
    return PowerState(
        on_battery=on_battery,
        low_power_mode=low_power,
        thermal=thermal,
        source="pmset",
    )


def _darwin_thermal_from_pmset(text: str) -> str:
    """Interprète la sortie ``pmset -g therm`` (limites CPU / scheduler)."""
    cpu_limit = None
    scheduler_limit = None
    for line in text.splitlines():
        lower = line.lower().strip()
        if "cpu_speed_limit" in lower or lower.startswith("cpu_speed"):
            cpu_limit = _trailing_int(line)
        elif "cpu_scheduler_limit" in lower or "scheduler_limit" in lower:
            scheduler_limit = _trailing_int(line)
    # 100 = nominal ; plus bas = le noyau bride déjà.
    limit = scheduler_limit if scheduler_limit is not None else cpu_limit
    if limit is None:
        return THERMAL_UNKNOWN
    if limit <= 50:
        return THERMAL_CRITICAL
    if limit <= 75:
        return THERMAL_SERIOUS
    if limit < 100:
        return THERMAL_FAIR
    return THERMAL_NOMINAL


def _windows() -> PowerState:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = SYSTEM_POWER_STATUS()
    try:
        # ``windll`` n'existe que sous Windows ; mypy sur Linux/macOS le refuse.
        ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))  # type: ignore[attr-defined]
    except Exception:
        LOGGER.debug("GetSystemPowerStatus illisible : état énergie inconnu", exc_info=True)
        return PowerState(source="unavailable")
    if not ok:
        return PowerState(source="unavailable")
    on_battery: bool | None
    if status.ACLineStatus == 0:
        on_battery = True
    elif status.ACLineStatus == 1:
        on_battery = False
    else:
        on_battery = None
    # SystemStatusFlag bit 0 : Battery Saver (Windows 10+).
    low_power = bool(status.SystemStatusFlag & 0x1)
    return PowerState(
        on_battery=on_battery,
        low_power_mode=low_power,
        thermal=THERMAL_UNKNOWN,
        source="win32",
    )


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _trailing_int(line: str) -> int | None:
    parts = line.replace("=", " ").split()
    for token in reversed(parts):
        try:
            return int(token)
        except ValueError:
            continue
    return None


__all__ = [
    "THERMAL_CRITICAL",
    "THERMAL_FAIR",
    "THERMAL_LEVELS",
    "THERMAL_NOMINAL",
    "THERMAL_SERIOUS",
    "THERMAL_UNKNOWN",
    "PowerState",
    "detect_power_state",
    "parse_power_override",
    "power_requires_step_down",
    "prefetch_suspended",
]
