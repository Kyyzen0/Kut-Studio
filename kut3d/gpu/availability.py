"""Le moteur GPU est-il utilisable ici ? Sonde dans un SOUS-PROCESSUS (sans Qt dans ce module).

Un plantage de pilote GPU ou un blocage de Metal / Direct3D ne doit jamais emporter l'appelant : la sonde rend un
minuscule triangle avec le vrai pipeline (`python -m kut3d.gpu.probe_worker`) dans un processus fils, avec un délai
(`PROBE_TIMEOUT_S`, 30 s). Le résultat est mis en cache par plateforme Qt ; `probe_qrhi(refresh=True)` le refait.
Sans PySide6, sous un plugin Qt sans GPU (`offscreen`, `minimal`) ou dans une application empaquetée, la raison réelle
est retournée, jamais une exception.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from kut3d.errors import RenderBackendUnavailable, RenderError
from kut3d.gpu.limits import PROBE_TIMEOUT_S

ENV_SWITCH = "KUT3D_GPU"  # "off" : désactive le moteur GPU (diagnostic, CI)
NATIVE = "native"  # plateforme Qt « par défaut du système » (cocoa sur macOS) : QT_QPA_PLATFORM retirée du fils


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str  # vide si ok, sinon la raison réelle (message Qt, absence de PySide6, délai dépassé...)
    platform: str  # plateforme Qt utilisée par la sonde
    backend: str  # API graphique (Metal, Direct3D11, OpenGL...) ou ""
    seconds: float
    qt_platform: str = ""  # nom réel du plugin Qt vu par la sonde (« cocoa » quand `platform` valait `NATIVE`)


_CACHE: dict[str, ProbeResult] = {}
_LOCK = threading.Lock()


def resolve_platform() -> str:
    """Plateforme Qt que le rendu en processus utiliserait : celle de l'application Qt déjà lancée s'il y en a
    une, sinon QT_QPA_PLATFORM si elle est définie, sinon `NATIVE`. N'importe jamais PySide6."""
    gui = sys.modules.get("PySide6.QtGui")
    if gui is not None:
        app = gui.QGuiApplication.instance()
        if app is not None:
            return str(app.platformName())
    env = os.environ.get("QT_QPA_PLATFORM", "").strip()
    return env.split(":")[0] if env else NATIVE


def _worker_env(platform: str) -> dict[str, str]:
    env = dict(os.environ)
    if platform == NATIVE:
        env.pop("QT_QPA_PLATFORM", None)
    else:
        env["QT_QPA_PLATFORM"] = platform
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = (
        root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else root
    )
    return env


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # Même forme que `core.process_supervisor.helper_command(PROBE_FLAG)` (kut3d n'importe pas core ; un test
        # vérifie l'égalité) : `sys.executable` est l'application elle-même.
        from kut3d.gpu.probe_worker import PROBE_FLAG

        return [sys.executable, PROBE_FLAG]
    return [sys.executable, "-m", "kut3d.gpu.probe_worker"]


def _pyside_installed() -> bool:
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


def _run_probe(platform: str, timeout: float) -> ProbeResult:
    import time

    started = time.perf_counter()

    def fail(reason: str) -> ProbeResult:
        return ProbeResult(False, reason, platform, "", time.perf_counter() - started)

    if os.environ.get(ENV_SWITCH, "").strip().lower() in {"off", "0", "false"}:
        return fail(
            f"moteur GPU désactivé par la variable d'environnement {ENV_SWITCH}."
        )
    if not _pyside_installed():
        return fail("PySide6 n'est pas installé.")
    try:
        done = subprocess.run(
            _worker_command(),
            env=_worker_env(platform),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return fail(f"la sonde GPU n'a pas répondu en {timeout:g} s (pilote bloqué ?).")
    except OSError as error:
        return fail(f"impossible de lancer la sonde GPU : {error}")
    last = next(
        (ln for ln in reversed(done.stdout.splitlines()) if ln.startswith("{")), ""
    )
    try:
        data = json.loads(last)
    except ValueError:
        data = None
    qt_platform = str(data.get("platform", "")) if isinstance(data, dict) else ""
    if done.returncode == 0 and isinstance(data, dict) and data.get("ok") is True:
        return ProbeResult(
            True,
            "",
            platform,
            str(data.get("backend", "")),
            time.perf_counter() - started,
            qt_platform,
        )
    if isinstance(data, dict) and data.get("reason"):
        reason = str(data["reason"])
    elif done.returncode < 0:
        tail = [ln for ln in done.stderr.strip().splitlines() if ln.strip()]
        detail = f" : {tail[-1][:200]}" if tail else ""
        reason = f"la sonde GPU a planté (signal {-done.returncode}){detail}"
    else:
        tail = (done.stderr or done.stdout).strip().splitlines()
        reason = f"la sonde GPU a échoué (code {done.returncode}) : {tail[-1][:200] if tail else 'aucun message'}"
    return ProbeResult(
        False, reason, platform, "", time.perf_counter() - started, qt_platform
    )


def probe_qrhi(
    *,
    platform: str | None = None,
    refresh: bool = False,
    timeout: float = PROBE_TIMEOUT_S,
) -> ProbeResult:
    """Résultat (mis en cache par plateforme) de la sonde QRhi. `platform` : nom de plugin Qt, `NATIVE` ou None
    (= `resolve_platform()`)."""
    plat = resolve_platform() if platform is None else platform
    with _LOCK:
        if not refresh and plat in _CACHE:
            return _CACHE[plat]
        result = _run_probe(plat, min(float(timeout), PROBE_TIMEOUT_S))
        _CACHE[plat] = result
        if result.qt_platform:  # une fois l'application Qt créée, `resolve_platform()` rend ce nom réel, pas NATIVE
            _CACHE.setdefault(result.qt_platform, result)
        return result


def clear_probe_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def qrhi_unavailable_reason() -> str | None:
    """None si le moteur « qrhi » est utilisable ici, sinon la raison. Utilisé comme `available=` du registre."""
    result = probe_qrhi()
    return None if result.ok else result.reason


def require_qrhi() -> None:
    """Lève `RenderBackendUnavailable` (avec la raison réelle) si la sonde échoue."""
    reason = qrhi_unavailable_reason()
    if reason is not None:
        raise RenderBackendUnavailable(
            f"moteur de rendu « qrhi » indisponible : {reason}"
        )


def require_main_thread() -> None:
    """Qt et le GPU appartiennent au fil principal (celui de l'application Qt) : `RenderError` ailleurs."""
    if threading.current_thread() is not threading.main_thread():
        raise RenderError(
            "le moteur « qrhi » doit être appelé depuis le fil principal (celui de l'application Qt) ; "
            'dans un autre fil, utiliser backend="numpy".'
        )


def auto_prefers_gpu() -> bool:
    """Vrai si `backend="auto"` doit essayer le GPU : fil principal ET sonde réussie (sans exception)."""
    if threading.current_thread() is not threading.main_thread():
        return False
    return qrhi_unavailable_reason() is None
