"""Banc machine mesuré pour le profil de performance Auto (ADR-0001 étape 1).

Au premier besoin, un banc court (composition CPU de calques, décodage FFmpeg
d'un plan court, rastérisation d'un tampon titre) classe la machine en
``low`` / ``balanced`` / ``high``. Le résultat est mis en cache sous
``machine-profile.json`` (même dossier que les capacités matérielles) et
relancé si l'empreinte change (cœurs, RAM, plateforme, version du schéma).

Le banc ne lit pas le GPU : sans mesure GPU fiable et portable, inventer un
score graphique serait un faux signal (voir :mod:`core.runtime_profile`).
Batterie / chauffe : hors de cette première livraison.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .atomic_io import atomic_write_text
from .platform_paths import user_cache_dir
from .runtime_profile import (
    PROFILE_BALANCED,
    PROFILE_HIGH,
    PROFILE_LOW,
    MachineResources,
    detect_resources,
)
from .tool_paths import find_media_tool

LOGGER = logging.getLogger("kut_studio.machine_bench")

SCHEMA_VERSION = 1
CACHE_FILE_NAME = "machine-profile.json"
MAX_AGE_SECONDS = 30 * 24 * 3600.0

# Seuils du banc (médiane de la machine minimale ADR : M1 8 Go / iGPU milieu).
# Ajustés pour être stables en CI ; le profil heuristique reste le filet.
_COMPOSE_HIGH_MS = 40.0
_COMPOSE_LOW_MS = 120.0
_DECODE_HIGH_FPS = 90.0
_DECODE_LOW_FPS = 35.0
_RASTER_HIGH_MS = 8.0
_RASTER_LOW_MS = 25.0


@dataclass(frozen=True)
class BenchResult:
    """Résultat d'un banc, ou lecture du cache."""

    profile: str
    compose_ms: float
    decode_fps: float | None
    raster_ms: float
    measured_at: float
    fingerprint: str
    schema: int = SCHEMA_VERSION
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


DecodeRunner = Callable[[list[str]], subprocess.CompletedProcess]


def machine_fingerprint(resources: MachineResources | None = None) -> str:
    """Empreinte stable : si elle change, le cache est ignoré."""
    res = resources if resources is not None else detect_resources()
    memory = res.memory_bytes if res.memory_bytes is not None else -1
    return "|".join(
        [
            f"schema={SCHEMA_VERSION}",
            platform.system(),
            platform.machine(),
            f"cpus={res.logical_cpus}",
            f"mem={memory}",
        ]
    )


def _score_to_profile(compose_ms: float, decode_fps: float | None, raster_ms: float) -> str:
    """Vote majoritaire low / balanced / high ; decode absent = voix neutre."""
    votes = []
    votes.append(
        PROFILE_HIGH if compose_ms <= _COMPOSE_HIGH_MS else PROFILE_LOW if compose_ms >= _COMPOSE_LOW_MS else PROFILE_BALANCED
    )
    votes.append(
        PROFILE_HIGH if raster_ms <= _RASTER_HIGH_MS else PROFILE_LOW if raster_ms >= _RASTER_LOW_MS else PROFILE_BALANCED
    )
    if decode_fps is not None:
        votes.append(
            PROFILE_HIGH
            if decode_fps >= _DECODE_HIGH_FPS
            else PROFILE_LOW
            if decode_fps <= _DECODE_LOW_FPS
            else PROFILE_BALANCED
        )
    return max(set(votes), key=votes.count)


def _bench_compose_layers(*, width: int = 960, height: int = 540, layers: int = 10) -> float:
    """Compose ``layers`` calques RVBA (addition alpha simple) ; retourne ms."""
    rng = np.random.default_rng(0)
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    started = time.perf_counter()
    for _ in range(layers):
        layer = rng.random((height, width, 4), dtype=np.float32)
        alpha = layer[:, :, 3:4]
        canvas[:, :, :3] = canvas[:, :, :3] * (1.0 - alpha) + layer[:, :, :3] * alpha
        canvas[:, :, 3:4] = np.maximum(canvas[:, :, 3:4], alpha)
    # Empêche l'élimination du travail.
    _ = float(canvas.mean())
    return (time.perf_counter() - started) * 1000.0


def _bench_raster_title(*, width: int = 640, height: int = 120) -> float:
    """Simule une rastérisation titre (remplissage + alpha) ; retourne ms."""
    started = time.perf_counter()
    buffer = np.zeros((height, width, 4), dtype=np.uint8)
    buffer[:, :] = (32, 32, 40, 255)
    buffer[20:100, 40:600, :] = (224, 160, 64, 255)
    buffer[40:80, 80:560, 3] = 200
    _ = int(buffer.sum())
    return (time.perf_counter() - started) * 1000.0


def _bench_decode_fps(
    *,
    frames: int = 90,
    size: str = "1280x720",
    runner: DecodeRunner | None = None,
) -> float | None:
    """Décode ``frames`` images lavfi via FFmpeg null ; retourne i/s ou None."""
    ffmpeg = find_media_tool("ffmpeg")
    if not ffmpeg:
        return None
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=30",
        "-frames:v",
        str(frames),
        "-f",
        "null",
        "-",
    ]
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=60))
    started = time.perf_counter()
    try:
        completed = run(command)
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.warning("Banc décodage impossible : %s", error)
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    elapsed = time.perf_counter() - started
    if elapsed <= 0:
        return None
    return frames / elapsed


def run_machine_bench(
    resources: MachineResources | None = None,
    *,
    decode_runner: DecodeRunner | None = None,
) -> BenchResult:
    """Exécute le banc et retourne le profil proposé (sans écrire le cache)."""
    res = resources if resources is not None else detect_resources()
    compose_ms = _bench_compose_layers()
    raster_ms = _bench_raster_title()
    decode_fps = _bench_decode_fps(runner=decode_runner)
    profile = _score_to_profile(compose_ms, decode_fps, raster_ms)
    notes = []
    if decode_fps is None:
        notes.append("decode_skipped")
    return BenchResult(
        profile=profile,
        compose_ms=round(compose_ms, 3),
        decode_fps=None if decode_fps is None else round(decode_fps, 2),
        raster_ms=round(raster_ms, 3),
        measured_at=time.time(),
        fingerprint=machine_fingerprint(res),
        notes=",".join(notes),
    )


def cache_path(directory: Path | None = None) -> Path:
    root = directory if directory is not None else user_cache_dir()
    return Path(root) / CACHE_FILE_NAME


def load_cached_bench(
    resources: MachineResources | None = None,
    *,
    directory: Path | None = None,
    now: float | None = None,
) -> BenchResult | None:
    """Lit le cache s'il est valide pour l'empreinte courante."""
    path = cache_path(directory)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if int(payload.get("schema", 0)) != SCHEMA_VERSION:
        return None
    expected = machine_fingerprint(resources)
    if str(payload.get("fingerprint", "")) != expected:
        return None
    measured_at = float(payload.get("measured_at", 0))
    age_now = time.time() if now is None else now
    if age_now - measured_at > MAX_AGE_SECONDS:
        return None
    profile = str(payload.get("profile", ""))
    if profile not in {PROFILE_LOW, PROFILE_BALANCED, PROFILE_HIGH}:
        return None
    return BenchResult(
        profile=profile,
        compose_ms=float(payload.get("compose_ms", 0)),
        decode_fps=payload.get("decode_fps"),
        raster_ms=float(payload.get("raster_ms", 0)),
        measured_at=measured_at,
        fingerprint=expected,
        notes=str(payload.get("notes", "")),
    )


def save_cached_bench(result: BenchResult, *, directory: Path | None = None) -> Path:
    path = cache_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def ensure_machine_bench(
    resources: MachineResources | None = None,
    *,
    directory: Path | None = None,
    force: bool = False,
    decode_runner: DecodeRunner | None = None,
) -> BenchResult:
    """Retourne le banc en cache, ou le mesure et l'enregistre."""
    res = resources if resources is not None else detect_resources()
    if not force:
        cached = load_cached_bench(res, directory=directory)
        if cached is not None:
            return cached
    result = run_machine_bench(res, decode_runner=decode_runner)
    try:
        save_cached_bench(result, directory=directory)
    except OSError as error:
        LOGGER.warning("Impossible d'enregistrer le banc machine : %s", error)
    return result


def measured_profile_name(
    resources: MachineResources | None = None,
    *,
    directory: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Profil mesuré si le cache est chaud ; ``None`` pour garder l'heuristique.

    ``KUT_STUDIO_MACHINE_BENCH=off`` force l'heuristique (CI).
    """
    env = environment if environment is not None else os.environ
    if str(env.get("KUT_STUDIO_MACHINE_BENCH", "")).strip().lower() in {"off", "0", "false", "no"}:
        return None
    cached = load_cached_bench(resources, directory=directory)
    return None if cached is None else cached.profile


__all__ = [
    "BenchResult",
    "CACHE_FILE_NAME",
    "SCHEMA_VERSION",
    "ensure_machine_bench",
    "load_cached_bench",
    "machine_fingerprint",
    "measured_profile_name",
    "run_machine_bench",
    "save_cached_bench",
]
