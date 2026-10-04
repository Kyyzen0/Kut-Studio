"""Quelles images intermédiaires un clip remappé demande-t-il ? Le plan d'interpolation, sans un seul pixel.

Pour chaque image de sortie (tick ``k``, instant ``T = k / fps``), le :class:`~core.time_map.TimeMap` donne l'instant source
``M(T)`` donc une **position en images source** ``x = M(T) · fs``. Trois façons d'en faire une image :

``SAMPLING``      l'image source la plus proche de ``x`` (la règle de :mod:`core.retime_graph`) ;
``BLENDING``      ``A·(1 − t) + B·t`` avec ``A = ⌊x⌋``, ``B = A + 1``, ``t = x − A`` ;
``OPTICAL_FLOW``  l'image que montrerait la scène à l'instant ``t`` entre ``A`` et ``B``, par flux optique
                  (:mod:`core.optical_flow`).

Les deux derniers ont la **même** forme (une paire consécutive et un poids) : ce module la calcule une fois, exactement, et
le moteur qui fabrique les pixels (mélange ou flux) la consomme. Il décide aussi **où** une image intermédiaire est utile :

- une image dont la position tombe sur une image source (``t`` nul à ``1e-3`` près : demi-image, quart d'image alignés) est
  cette image source, jamais recalculée ;
- un arrêt (vitesse nulle) montre une image figée : l'image la plus proche, pas un mélange flou ;
- au-delà de :data:`MAX_INTERPOLATED_STEP` images source par image de sortie, les images voisines sont sautées : intercaler
  entre elles n'ajoute rien (un flux optique entre deux images dont aucune n'est montrée serait un calcul perdu) ;
- la dernière image du média n'a pas de suivante : elle est montrée telle quelle.

Un clip dont aucune image n'est intermédiaire (200 %, 400 %, arrêt…) n'a donc **aucun** calcul à faire, quel que soit le mode
demandé : :attr:`InterpolationPlan.needs_synthesis` le dit, et le graphe retombe sur l'échantillonnage — qui produit alors
exactement les mêmes images.
"""

from __future__ import annotations

from dataclasses import dataclass

from .retime_graph import nearest_frame, tick_runs
from .time_map import RunKind, TimeMap
from .time_remapping import TimeInterpolation

MAX_INTERPOLATED_STEP = 2.0
"""Images source avancées par image de sortie au-delà desquelles on n'intercale plus (2× la cadence du média)."""

WEIGHT_EPSILON = 1e-3
"""Un poids à moins de ce seuil de 0 ou de 1 est une image source (le mélange serait invisible, le flux inutile)."""


@dataclass(frozen=True)
class FrameSample:
    """Une image de sortie : l'image source ``a`` (``t == 0``), ou le point ``t`` de ``]a, a + 1[``."""

    a: int
    t: float = 0.0

    @property
    def b(self) -> int:
        return self.a + 1

    @property
    def exact(self) -> bool:
        """Image source telle quelle (rien à calculer)."""
        return self.t == 0.0


@dataclass(frozen=True)
class PlannedRun:
    """Les images d'un run monotone, dans l'ordre des ticks de sortie."""

    kind: RunKind
    first: int
    samples: tuple[FrameSample, ...]

    @property
    def ticks(self) -> int:
        return len(self.samples)


@dataclass(frozen=True)
class InterpolationPlan:
    """Toutes les images de sortie d'un clip, run par run."""

    requested: TimeInterpolation
    runs: tuple[PlannedRun, ...]

    @property
    def ticks(self) -> int:
        return sum(run.ticks for run in self.runs)

    @property
    def synthetic(self) -> int:
        """Nombre d'images de sortie à fabriquer (``t`` strictement entre 0 et 1)."""
        return sum(1 for run in self.runs for sample in run.samples if not sample.exact)

    @property
    def needs_synthesis(self) -> bool:
        return self.synthetic > 0

    def runs_to_synthesize(self) -> tuple[int, ...]:
        """Indices des runs qui demandent au moins une image fabriquée (les autres restent dans le graphe d'échantillonnage)."""
        return tuple(index for index, run in enumerate(self.runs) if any(not sample.exact for sample in run.samples))

    def pairs(self) -> tuple[int, ...]:
        """Les ``a`` des paires ``(a, a + 1)`` à mélanger ou à estimer, croissants, sans doublon."""
        return tuple(sorted({sample.a for run in self.runs for sample in run.samples if not sample.exact}))

    def frames(self) -> tuple[int, ...]:
        """Toutes les images source lues (exactes et extrémités des paires), croissantes, sans doublon."""
        needed: set[int] = set()
        for run in self.runs:
            for sample in run.samples:
                needed.add(sample.a)
                if not sample.exact:
                    needed.add(sample.b)
        return tuple(sorted(needed))

    def describe(self) -> dict[str, object]:
        """Résumé honnête (journal, indicateur de l'interface) : ce qui sera calculé, ce qui est repris tel quel."""
        return {
            "requested": self.requested.value,
            "images": self.ticks,
            "synthesized": self.synthetic,
            "exact": self.ticks - self.synthetic,
            "pairs": len(self.pairs()),
        }


def sample_at(position: float, last_frame: int | None) -> FrameSample:
    """L'image de sortie dont la position source (en images) est ``position`` : image exacte ou point d'une paire."""
    if position <= 0.0:
        return FrameSample(0)
    low = int(position // 1.0)
    weight = position - low
    if last_frame is not None and low >= last_frame:
        return FrameSample(max(0, last_frame))
    if weight < WEIGHT_EPSILON:
        return FrameSample(low)
    if weight > 1.0 - WEIGHT_EPSILON:
        following = low + 1
        return FrameSample(following if last_frame is None else min(following, max(0, last_frame)))
    return FrameSample(low, weight)


def plan_interpolation(
    time_map: TimeMap,
    *,
    fps: float,
    source_fps: float,
    last_frame: int | None,
    interpolation: TimeInterpolation,
) -> InterpolationPlan:
    """Le plan d'un clip : pour chaque tick de sortie, l'image source ou la paire et son poids.

    ``SAMPLING`` donne partout l'image la plus proche (``interpolation`` ne change que ce qui est *demandé* : le plan reste
    celui de l'échantillonnage, sans image à fabriquer).
    """
    fs = source_fps if source_fps > 0 else 30.0
    interpolating = interpolation is not TimeInterpolation.SAMPLING
    runs: list[PlannedRun] = []
    for item in tick_runs(time_map, fps):
        samples: list[FrameSample] = []
        for tick in range(item.first, item.first + item.ticks):
            moment = tick / fps
            exact_only = (
                not interpolating
                or item.run.kind is RunKind.HOLD
                or abs(time_map.speed_at(moment)) * fs / fps >= MAX_INTERPOLATED_STEP
            )
            if exact_only:
                samples.append(FrameSample(nearest_frame(time_map.source_time(moment), fs, last_frame)))
            else:
                samples.append(sample_at(time_map.source_time(moment) * fs, last_frame))
        runs.append(PlannedRun(item.run.kind, item.first, tuple(samples)))
    return InterpolationPlan(interpolation, tuple(runs))


__all__ = [
    "MAX_INTERPOLATED_STEP",
    "WEIGHT_EPSILON",
    "FrameSample",
    "InterpolationPlan",
    "PlannedRun",
    "plan_interpolation",
    "sample_at",
]
