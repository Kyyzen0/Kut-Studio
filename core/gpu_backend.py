"""Choix du rendu de l'aperçu (CPU / GPU), santé du GPU et statistiques d'images.

Trois modes, choisis dans les préférences :

- ``cpu`` : le moniteur historique (``QGraphicsVideoItem``, rendu raster de Qt) ;
- ``gpu`` : le moniteur GPU (``QRhiWidget`` : Metal sur macOS, Direct3D 11 sous
  Windows, OpenGL sous Linux), avec effets, masques et fusion en temps réel ;
- ``auto`` : GPU si un contexte s'initialise réellement, CPU sinon.

Rien n'est supposé : le GPU n'est « disponible » qu'après une première image
réussie. Une erreur d'initialisation, un échec de rendu ou une perte de
périphérique (*device lost*) basculent le moniteur sur le CPU **sans arrêter la
session** ; après :data:`FAILURES_BEFORE_DISABLE` échecs le GPU n'est plus
retenté avant le prochain démarrage (pas de clignotement GPU ↔ CPU).

Module pur (aucun Qt) : la partie Qt est ``ui/gpu_preview.py``.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .platform_paths import user_cache_dir

LOGGER = logging.getLogger("kut_studio.gpu")

FAILURES_BEFORE_DISABLE = 2
DISABLE_VARIABLE = "KUT_STUDIO_GPU_PREVIEW"
"""``off`` : jamais de moniteur GPU (support, machines à pilote douteux)."""


class PreviewBackend(str, Enum):
    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


def coerce_preview_backend(value: object) -> PreviewBackend:
    try:
        return PreviewBackend(str(getattr(value, "value", value)).strip().lower())
    except ValueError:
        return PreviewBackend.AUTO


GRAPHICS_APIS: dict[str, tuple[str, ...]] = {
    "darwin": ("metal",),
    "win32": ("d3d11", "opengl"),
    "linux": ("opengl",),
}
"""API graphique par plateforme, via QRhi (aucun moteur maison). Vulkan et
Direct3D 12 restent possibles côté Qt mais demandent une instance dédiée : non
retenus pour une première version stable."""

API_LABELS = {"metal": "Metal", "d3d11": "Direct3D 11", "opengl": "OpenGL", "vulkan": "Vulkan", "null": "Null"}

_NO_RHI_PLATFORMS = {"offscreen", "minimal", "vnc", "linuxfb"}


def graphics_api(platform_name: str | None = None) -> str:
    name = (platform_name or sys.platform).lower()
    for prefix, apis in GRAPHICS_APIS.items():
        if name.startswith(prefix):
            return apis[0]
    return "opengl"


def gpu_preview_disabled(environment: Mapping[str, str] | None = None) -> bool:
    env = environment if environment is not None else os.environ
    return str(env.get(DISABLE_VARIABLE, "")).strip().lower() in {"off", "0", "false", "no"}


def qpa_without_rhi(environment: Mapping[str, str] | None = None, platform_plugin: str = "") -> bool:
    """Plateforme Qt sans fenêtre réelle (``offscreen`` en CI) : aucun contexte GPU possible."""
    env = environment if environment is not None else os.environ
    name = (platform_plugin or env.get("QT_QPA_PLATFORM", "")).split(":")[0].strip().lower()
    return name in _NO_RHI_PLATFORMS


@dataclass(frozen=True)
class GpuEvent:
    when: float
    kind: str
    detail: str


class GpuHealth:
    """Échecs du rendu GPU pendant la session (sûr entre threads)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._events: deque[GpuEvent] = deque(maxlen=20)
        self.failures = 0
        self.device_lost = 0
        self.fallbacks = 0

    @property
    def disabled(self) -> bool:
        with self._lock:
            return self.failures >= FAILURES_BEFORE_DISABLE

    def record(self, kind: str, detail: str = "") -> None:
        """``kind`` : ``init``, ``render``, ``device_lost``, ``out_of_memory``, ``format``."""
        with self._lock:
            self.failures += 1
            self.fallbacks += 1
            if kind == "device_lost":
                self.device_lost += 1
            self._events.append(GpuEvent(self._clock(), kind, (detail or "")[:200]))
            count = self.failures
        log = LOGGER.warning if count <= FAILURES_BEFORE_DISABLE else LOGGER.debug
        log("Aperçu GPU : %s (%s) — repli sur le CPU", kind, detail or "sans détail")
        if count == FAILURES_BEFORE_DISABLE:
            LOGGER.warning("Aperçu GPU désactivé pour la session après %d échecs", count)

    def last(self) -> GpuEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    def events(self) -> tuple[GpuEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self.failures = self.device_lost = self.fallbacks = 0


class GpuCrashGuard:
    """Mémoire d'un plantage du **processus** pendant que le moniteur GPU était actif.

    ``GpuHealth`` ne voit que les échecs que Qt rapporte. Un pilote qui fait tomber l'application (erreur fatale,
    écran noir puis arrêt) ne laisse aucune trace de ce genre : à chaque démarrage le GPU était retenté, donc
    l'application replantait à chaque démarrage. Un marqueur est posé quand le GPU est activé et retiré à l'arrêt
    propre ; s'il subsiste au démarrage suivant, la session précédente s'est terminée brutalement avec le GPU
    actif. Le mode Auto reste alors sur le CPU jusqu'à ce que l'utilisateur choisisse lui-même le rendu de
    l'aperçu (:meth:`reset`) : un choix explicite redonne sa chance au GPU.

    Tout est tolérant : un fichier illisible ou un disque en lecture seule se comportent comme « aucun plantage ».
    """

    FILE_NAME = "gpu-guard.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.path = Path(directory) / self.FILE_NAME if directory is not None else user_cache_dir() / self.FILE_NAME

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            LOGGER.debug("Garde GPU : écriture impossible (%s)", self.path)

    @staticmethod
    def _count(data: dict) -> int:
        try:
            return max(0, int(data.get("crashes", 0)))
        except (TypeError, ValueError):
            return 0

    def begin_session(self) -> None:
        """À appeler une fois au démarrage : un marqueur resté en place est un plantage de la session précédente."""
        data = self._read()
        if data.get("armed") is True:
            LOGGER.warning("La session précédente s'est terminée brutalement avec le GPU actif")
            self._write({"armed": False, "crashes": self._count(data) + 1})

    @property
    def suspended(self) -> bool:
        """Un plantage avec GPU actif est connu : le mode Auto n'y retourne pas de lui-même."""
        return self._count(self._read()) >= 1

    def arm(self) -> None:
        """Le moniteur GPU vient d'être activé."""
        self._write({"armed": True, "crashes": self._count(self._read())})

    def disarm(self) -> None:
        """Le GPU n'est plus actif (arrêt propre, repli CPU) : un plantage ultérieur n'est pas le sien."""
        data = self._read()
        if data.get("armed"):
            self._write({"armed": False, "crashes": self._count(data)})

    def reset(self) -> None:
        """Choix explicite de l'utilisateur : le GPU retrouve sa chance."""
        self._write({"armed": False, "crashes": 0})


@dataclass(frozen=True)
class ResolvedBackend:
    """Rendu retenu pour le moniteur."""

    requested: PreviewBackend
    kind: str  # "gpu" ou "cpu"
    api: str = ""
    reason: str = ""
    fallback_reason: str = ""

    @property
    def is_gpu(self) -> bool:
        return self.kind == "gpu"

    @property
    def label(self) -> str:
        if self.is_gpu:
            return f"GPU ({API_LABELS.get(self.api, self.api)})"
        return "CPU"


def resolve_preview_backend(
    requested: object,
    *,
    health: GpuHealth | None = None,
    guard: GpuCrashGuard | None = None,
    environment: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    platform_plugin: str = "",
) -> ResolvedBackend:
    """Rendu à **tenter** (le GPU n'est confirmé qu'après une image réussie)."""
    wanted = coerce_preview_backend(requested)
    if wanted is PreviewBackend.CPU:
        return ResolvedBackend(wanted, "cpu", reason="requested_cpu")
    explicit = wanted is PreviewBackend.GPU
    if gpu_preview_disabled(environment):
        return ResolvedBackend(wanted, "cpu", reason="disabled",
                               fallback_reason="Aperçu GPU désactivé (KUT_STUDIO_GPU_PREVIEW=off)." if explicit else "")
    if qpa_without_rhi(environment, platform_plugin):
        return ResolvedBackend(wanted, "cpu", reason="no_window_system",
                               fallback_reason="Pas de contexte graphique sur cette plateforme Qt." if explicit else "")
    if health is not None and health.disabled:
        event = health.last()
        detail = f" ({event.kind} : {event.detail})" if event is not None and event.detail else ""
        return ResolvedBackend(wanted, "cpu", reason="gpu_failed",
                               fallback_reason=f"Le rendu GPU a échoué{detail} : aperçu CPU.")
    if not explicit and guard is not None and guard.suspended:
        return ResolvedBackend(
            wanted, "cpu", reason="previous_crash",
            fallback_reason="La session précédente s'est terminée brutalement avec le GPU actif : aperçu CPU. "
                            "Choisissez « GPU » dans les préférences pour réessayer.",
        )
    return ResolvedBackend(wanted, "gpu", api=graphics_api(platform_name), reason="gpu")


# --- Statistiques d'images ----------------------------------------------------------------------------


class FrameStats:
    """Cadence et coût du moniteur, sur une fenêtre glissante (sûr entre threads).

    - *présentées* : images effectivement dessinées ;
    - *perdues* : images décodées remplacées par une plus récente avant d'avoir
      été dessinées (le moniteur n'a pas suivi) ;
    - temps de rendu : soumission des passes côté CPU (le GPU travaille ensuite
      en parallèle ; c'est ce temps-là qui bloque l'interface).
    """

    def __init__(self, window: int = 120) -> None:
        self._lock = threading.Lock()
        self._render_ms: deque[float] = deque(maxlen=window)
        self._upload_bytes: deque[int] = deque(maxlen=window)
        self.received = 0
        self.presented = 0
        self.dropped = 0
        self._pending = False

    def note_arrival(self) -> None:
        with self._lock:
            self.received += 1
            if self._pending:
                self.dropped += 1
            self._pending = True

    def note_render(self, milliseconds: float, uploaded_bytes: int = 0) -> None:
        with self._lock:
            self._render_ms.append(float(milliseconds))
            self._upload_bytes.append(int(uploaded_bytes))
            if self._pending:
                self.presented += 1
                self._pending = False

    def average_render_ms(self) -> float | None:
        with self._lock:
            return statistics.fmean(self._render_ms) if self._render_ms else None

    def p95_render_ms(self) -> float | None:
        with self._lock:
            if len(self._render_ms) < 2:
                return self._render_ms[0] if self._render_ms else None
            ordered = sorted(self._render_ms)
        return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]

    def average_upload_bytes(self) -> float | None:
        with self._lock:
            return statistics.fmean(self._upload_bytes) if self._upload_bytes else None

    def drop_ratio(self) -> float:
        with self._lock:
            return self.dropped / self.received if self.received else 0.0

    def reset(self) -> None:
        with self._lock:
            self._render_ms.clear()
            self._upload_bytes.clear()
            self.received = self.presented = self.dropped = 0
            self._pending = False


# --- Fichiers des shaders -----------------------------------------------------------------------------------


def shader_dir() -> Path:
    """``assets/shaders`` du dépôt ou de l'application construite (PyInstaller)."""
    from .platform_paths import bundled_assets_dir

    return bundled_assets_dir("shaders")


SHADER_NAMES = ("quad.vert", "clear.frag", "prep.frag", "blur.frag", "sharpen.frag",
                "shift.frag", "haze.frag", "glow.frag", "composite.frag", "present.frag")


def missing_shaders(directory: Path | None = None) -> list[str]:
    base = directory or shader_dir()
    return [name for name in SHADER_NAMES if not (base / f"{name}.qsb").is_file()]


__all__ = [
    "API_LABELS",
    "DISABLE_VARIABLE",
    "FAILURES_BEFORE_DISABLE",
    "GRAPHICS_APIS",
    "SHADER_NAMES",
    "FrameStats",
    "GpuCrashGuard",
    "GpuEvent",
    "GpuHealth",
    "PreviewBackend",
    "ResolvedBackend",
    "coerce_preview_backend",
    "gpu_preview_disabled",
    "graphics_api",
    "missing_shaders",
    "qpa_without_rhi",
    "resolve_preview_backend",
    "shader_dir",
]
