"""Cache disque et service des capacités d'encodage matériel.

La détection lance plusieurs processus FFmpeg (liste des encodeurs, puis un
mini-encodage par encodeur matériel) : on ne la refait pas à chaque démarrage.
Le résultat est stocké dans le dossier de cache de l'utilisateur avec une
**empreinte** de l'installation ; il est relu tant que l'empreinte est la même
et invalidé automatiquement quand :

- le chemin de FFmpeg change ;
- le binaire change (date ou taille : une mise à jour change donc la version) ;
- la plateforme, l'architecture ou le nœud VAAPI changent ;
- l'interrupteur ``KUT_STUDIO_HARDWARE_ENCODING`` change ;
- le cache a plus de :data:`MAX_AGE_SECONDS` (pilotes mis à jour sans changer FFmpeg).

Un pilote mis à jour *après* la détection n'est pas détectable sans la refaire :
``rescan()`` (bouton « Redétecter les capacités matérielles ») force une
nouvelle détection. Et si un encodeur validé échoue malgré tout au rendu, le
mode Auto bascule sur le CPU (voir :mod:`core.export_engine`).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .hardware_encoding import (
    LOGGER,
    HardwareCapabilities,
    Runner,
    default_runner,
    detect_capabilities,
    vaapi_device,
)
from .platform_paths import user_cache_dir
from .tool_paths import find_media_tool

CACHE_FILE_NAME = "hardware-encoders.json"
MAX_AGE_SECONDS = 30 * 24 * 3600.0
DISABLE_VARIABLE = "KUT_STUDIO_HARDWARE_ENCODING"
"""``off`` / ``0`` / ``false`` : aucune détection, tout est rendu en CPU (support, CI)."""


def hardware_encoding_disabled(environment: Mapping[str, str] | None = None) -> bool:
    env = environment if environment is not None else os.environ
    return str(env.get(DISABLE_VARIABLE, "")).strip().lower() in {"off", "0", "false", "no"}


def _default_command() -> list[str] | None:
    path = find_media_tool("ffmpeg")
    return [path] if path else None


def _stat_token(path: str) -> str:
    try:
        stat = os.stat(path)
    except OSError:
        return f"{path}:absent"
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def installation_fingerprint(
    command: Sequence[str] | None, environment: Mapping[str, str] | None = None
) -> str:
    """Empreinte de ce qui rend une détection valable (voir le module)."""
    import platform as platform_module

    parts = [
        *(_stat_token(str(item)) for item in (command or ())),
        sys.platform,
        platform_module.machine(),
        vaapi_device(dict(environment) if environment is not None else None) or "no-vaapi",
        "off" if hardware_encoding_disabled(environment) else "on",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


class CapabilityService:
    """Fournit les capacités, avec cache mémoire + disque, sans bloquer inutilement.

    Les méthodes sont sûres entre threads. Un seul scan tourne à la fois : un
    second appelant attend le résultat du premier au lieu de relancer FFmpeg.
    """

    def __init__(
        self,
        *,
        command_provider: Callable[[], Sequence[str] | None] | None = None,
        cache_path: str | os.PathLike[str] | None = None,
        runner: Runner = default_runner,
        validate: bool = True,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._command_provider = command_provider or _default_command
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self._runner = runner
        self._validate = validate
        self._environment = environment
        self._clock = clock
        self._lock = threading.RLock()  # état mémoire seulement : jamais tenu pendant un scan
        self._scan_lock = threading.Lock()  # un seul scan à la fois
        self._memory: HardwareCapabilities | None = None
        self.scan_count = 0
        """Nombre de détections réellement exécutées (tests, diagnostics)."""

    # -- Lecture --------------------------------------------------------------------------------------

    @property
    def cache_path(self) -> Path:
        if self._cache_path is not None:
            return self._cache_path
        return user_cache_dir() / CACHE_FILE_NAME

    def cached(self) -> HardwareCapabilities | None:
        """Capacités déjà connues et encore valables, **sans** lancer FFmpeg."""
        with self._lock:
            command = self._command_provider()
            fingerprint = installation_fingerprint(command, self._environment)
            if self._memory is not None and self._memory.fingerprint == fingerprint:
                return self._memory
            if hardware_encoding_disabled(self._environment):
                return self._scan()  # pas de processus : le résultat est connu d'avance
            stored = self._read_disk()
            if (
                stored is not None
                and stored.fingerprint == fingerprint
                and self._clock() - stored.scanned_at <= MAX_AGE_SECONDS
                and (stored.validated or not self._validate)
            ):
                self._memory = stored
                return stored
            return None

    def capabilities(self, *, refresh: bool = False) -> HardwareCapabilities:
        """Capacités valables ; détecte (et mémorise) si le cache est absent ou périmé."""
        if not refresh:
            known = self.cached()
            if known is not None:
                return known
        # Le verrou d'état n'est pas tenu pendant les processus FFmpeg : ``cached()``
        # (utilisé par l'interface) ne bloque donc jamais derrière une détection.
        with self._scan_lock:
            if not refresh:
                known = self.cached()  # un autre appelant vient peut-être de terminer
                if known is not None:
                    return known
            return self._scan()

    def rescan(self) -> HardwareCapabilities:
        """Redétecte maintenant (action de l'utilisateur)."""
        LOGGER.info("Redétection des capacités matérielles demandée")
        return self.capabilities(refresh=True)

    def invalidate(self) -> None:
        """Oublie le cache mémoire et disque ; la prochaine lecture redétecte."""
        with self._lock:
            self._memory = None
            try:
                self.cache_path.unlink()
            except OSError:
                pass

    # -- Interne -------------------------------------------------------------------------------------

    def _scan(self) -> HardwareCapabilities:
        command = self._command_provider()
        fingerprint = installation_fingerprint(command, self._environment)
        import platform as platform_module

        base = dict(
            platform=sys.platform, machine=platform_module.machine(),
            scanned_at=self._clock(), fingerprint=fingerprint,
        )
        if hardware_encoding_disabled(self._environment):
            result = HardwareCapabilities(error="disabled", validated=False, **base)
        elif not command:
            result = HardwareCapabilities(error="ffmpeg_missing", **base)
            with self._lock:
                self._memory = result  # FFmpeg absent : rien à mémoriser sur disque
            return result
        else:
            with self._lock:
                self.scan_count += 1
            result = detect_capabilities(
                command, runner=self._runner, validate=self._validate,
                fingerprint=fingerprint, now=self._clock(),
            )
        with self._lock:
            self._memory = result
        self._write_disk(result)
        return result

    def _read_disk(self) -> HardwareCapabilities | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return HardwareCapabilities.from_dict(data)

    def _write_disk(self, capabilities: HardwareCapabilities) -> None:
        path = self.cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(capabilities.to_dict(), indent=1), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:  # un cache illisible ne doit jamais bloquer un export
            LOGGER.warning("Cache des capacités non écrit : %s", error)


_default_service: CapabilityService | None = None
_default_lock = threading.Lock()


def default_service() -> CapabilityService:
    """Service global (créé à la demande)."""
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = CapabilityService()
        return _default_service


def set_default_service(service: CapabilityService | None) -> None:
    """Remplace le service global (tests, ou FFmpeg configuré autrement)."""
    global _default_service
    with _default_lock:
        _default_service = service


def current_capabilities() -> HardwareCapabilities:
    return default_service().capabilities()


__all__ = [
    "CACHE_FILE_NAME",
    "DISABLE_VARIABLE",
    "MAX_AGE_SECONDS",
    "CapabilityService",
    "current_capabilities",
    "default_service",
    "hardware_encoding_disabled",
    "installation_fingerprint",
    "set_default_service",
]
