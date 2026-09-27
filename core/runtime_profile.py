"""Profils de performance adaptés à la machine et au poids du projet.

La détection reste grossière et honnête : nombre de cœurs, mémoire
physique quand le système l'expose, et rien sur le GPU. Annoncer une
puce graphique sans pouvoir la lire serait un faux signal. Un appelant
peut fournir plus tard un indice GPU via :meth:`MachineResources.with_gpu`.

Les profils règlent des budgets réels (cache, tâches simultanées,
marge de la timeline). Ils ne mesurent pas le débit d'images : aucun
compteur de frames inventé ne doit décider de la qualité.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


PROFILE_LOW = "low"
PROFILE_BALANCED = "balanced"
PROFILE_HIGH = "high"
PROFILE_AUTO = "auto"

VALID_PROFILES: tuple[str, ...] = (
    PROFILE_AUTO,
    PROFILE_LOW,
    PROFILE_BALANCED,
    PROFILE_HIGH,
)


@dataclass(frozen=True)
class MachineResources:
    """Ressources observées, ou inconnues quand l'OS ne les donne pas."""

    logical_cpus: int
    memory_bytes: int | None = None
    gpu_name: str | None = None

    def with_gpu(self, gpu_name: str | None) -> "MachineResources":
        return MachineResources(
            logical_cpus=self.logical_cpus,
            memory_bytes=self.memory_bytes,
            gpu_name=gpu_name,
        )


@dataclass(frozen=True)
class PerformanceProfile:
    """Budgets appliqués par le runtime.

    ``preview_divisor`` est le repli du mode d'aperçu Auto :
    1 = plein, 2 = 1/2, 4 = 1/4, 8 = 1/8.

    ``realtime_effects`` est un conseil pour les prochains effets.
    Le viewer actuel ne s'en sert pas encore : couper les transforms
    déjà en place changerait le rendu sans gain mesuré.
    """

    name: str
    cache_budget_bytes: int
    max_parallel_tasks: int
    preview_divisor: int
    timeline_overscan_px: int
    realtime_effects: bool
    filmstrips: bool = True


_PROFILES: dict[str, PerformanceProfile] = {
    PROFILE_LOW: PerformanceProfile(
        name=PROFILE_LOW,
        cache_budget_bytes=64 * 1024 * 1024,
        max_parallel_tasks=1,
        preview_divisor=4,
        timeline_overscan_px=240,
        realtime_effects=False,
        filmstrips=False,
    ),
    PROFILE_BALANCED: PerformanceProfile(
        name=PROFILE_BALANCED,
        cache_budget_bytes=256 * 1024 * 1024,
        max_parallel_tasks=2,
        preview_divisor=2,
        timeline_overscan_px=720,
        realtime_effects=True,
        filmstrips=True,
    ),
    PROFILE_HIGH: PerformanceProfile(
        name=PROFILE_HIGH,
        cache_budget_bytes=1024 * 1024 * 1024,
        max_parallel_tasks=4,
        preview_divisor=1,
        timeline_overscan_px=1400,
        realtime_effects=True,
        filmstrips=True,
    ),
}

_GIB = 1024 ** 3


def detect_resources() -> MachineResources:
    """Lit le CPU et, si possible, la mémoire physique.

    ``SC_PHYS_PAGES`` est disponible sur macOS et Linux. En cas d'échec
    la mémoire reste ``None`` et le choix de profil ne l'invente pas.
    """
    cpus = os.cpu_count() or 1
    memory = None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            memory = int(pages) * int(page_size)
    except (AttributeError, OSError, ValueError):
        memory = None
    return MachineResources(logical_cpus=max(1, int(cpus)), memory_bytes=memory)


def project_weight(
    *,
    media_count: int,
    clip_count: int,
    track_count: int,
) -> str:
    """Classe un projet en ``light``, ``medium`` ou ``heavy``.

    Le score est un ordre de grandeur, pas une mesure de temps de rendu.
    Il sert uniquement à ne pas garder un profil généreux sur une petite
    machine qui ouvre un très gros projet.
    """
    score = max(0, clip_count) + max(0, media_count) + max(0, track_count) * 5
    if score >= 1500:
        return "heavy"
    if score >= 400:
        return "medium"
    return "light"


def recommend_profile(resources: MachineResources) -> str:
    """Choisit un profil à partir des ressources, sans regarder le projet."""
    memory = resources.memory_bytes
    if memory is not None and memory < 6 * _GIB:
        return PROFILE_LOW
    if resources.logical_cpus <= 4 and (memory is None or memory < 12 * _GIB):
        return PROFILE_LOW
    if (
        resources.logical_cpus >= 8
        and memory is not None
        and memory >= 16 * _GIB
    ):
        return PROFILE_HIGH
    return PROFILE_BALANCED


def resolve_profile(
    requested: str,
    resources: MachineResources,
    weight: str = "light",
) -> PerformanceProfile:
    """Résout le profil effectif.

    Un choix explicite (low, balanced, high) est respecté. ``auto``
    part de la machine. Un projet ``heavy`` sur une machine qui n'a
    pas au moins 16 Go descend d'un cran : le cache et le nombre de
    tâches suivent, pas un calcul de frames imaginaire.
    """
    if requested not in VALID_PROFILES:
        requested = PROFILE_AUTO
    name = recommend_profile(resources) if requested == PROFILE_AUTO else requested
    memory = resources.memory_bytes or 0
    # Un choix explicite n'est pas rabaissé. Seul Auto protège une
    # petite machine qui ouvre un très gros projet.
    if requested == PROFILE_AUTO and weight == "heavy" and memory < 16 * _GIB:
        if name == PROFILE_HIGH:
            name = PROFILE_BALANCED
        if name == PROFILE_BALANCED:
            name = PROFILE_LOW
    profile = _PROFILES[name]
    # Le parallélisme ne dépasse pas le nombre de cœurs.
    workers = max(1, min(profile.max_parallel_tasks, resources.logical_cpus))
    if workers == profile.max_parallel_tasks:
        return profile
    return PerformanceProfile(
        name=profile.name,
        cache_budget_bytes=profile.cache_budget_bytes,
        max_parallel_tasks=workers,
        preview_divisor=profile.preview_divisor,
        timeline_overscan_px=profile.timeline_overscan_px,
        realtime_effects=profile.realtime_effects,
        filmstrips=profile.filmstrips,
    )
