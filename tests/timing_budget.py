"""Budgets de temps des tests de performance : nominal × majoration CI × majoration instrumentation.

Ces tests gardent des propriétés réelles (une analyse de scopes tient le temps réel, un clic rend la main), mais un
seuil absolu ne vaut que sur la machine où il a été mesuré. Deux causes de lenteur n'ont rien à voir avec le code :

* **un runner de CI partagé** : plus lent, et ses cœurs sont partagés avec les autres workers xdist. Le temps CPU
  (:func:`best_cpu_seconds`) ignore la préemption, pas la contention (cœurs, fréquence, caches) : 8 processus en
  parallèle doublent encore le temps CPU d'une analyse sur un Mac M ;
* **l'instrumentation de coverage.py** (job Linux, docs/coverage.md) : en Python 3.11, son traceur C s'exécute à
  chaque ligne. Analyse de scopes (Python pur), meilleur de 3, mesuré le 2026-10-07 : 0,044 → 0,206 s (320×180,
  ×4,7) et 0,059 → 0,233 s (1080p, ×3,9). En Python 3.12+ (``sys.monitoring``), la majoration réelle est quasi nulle.

Le budget nominal est celui qu'on garantit sur une machine de développement ; il n'est jamais relâché hors CI ou
hors couverture, si bien qu'une vraie régression se voit d'abord en local.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

# Temps CPU d'un calcul sur un runner partagé : déjà en place pour l'analyse 1080p (0,25 s nominal, 0,6 s en CI).
CPU_CI_FACTOR = 2.4
# Temps mural sur un runner partagé : la préemption s'ajoute à la lenteur (0,93 s observé sur Windows pour un appel
# de 0,010 s en local).
WALL_CI_FACTOR = 3.0
# Traceur C de coverage.py, branches comprises, sur du Python pur : ×3,9 à ×4,7 mesuré (Python 3.11, voir plus haut).
COVERAGE_FACTOR = 5.0


def on_ci() -> bool:
    """``True`` sur un runner de CI (GitHub Actions et la plupart des services posent ``CI``)."""
    return bool(os.environ.get("CI"))


def under_coverage() -> bool:
    """``True`` si coverage.py mesure ce processus (worker xdist compris).

    ``Coverage.current()`` est l'API publique de coverage.py : elle vaut quel que soit le cœur de mesure, alors que
    ``sys.gettrace()`` reste ``None`` avec ``sys.monitoring`` (Python 3.12+). Sans ``--cov``, pytest-cov n'importe pas
    coverage : on ne paie pas l'import pour répondre « non ».
    """
    coverage = sys.modules.get("coverage")
    return coverage is not None and coverage.Coverage.current() is not None


def budget(nominal: float, *, ci_factor: float = CPU_CI_FACTOR, coverage_factor: float = COVERAGE_FACTOR) -> float:
    """Budget en secondes : ``nominal``, majoré sur un runner de CI et sous instrumentation."""
    return nominal * (ci_factor if on_ci() else 1.0) * (coverage_factor if under_coverage() else 1.0)


def best_cpu_seconds(action: Callable[[], object], repeats: int = 3) -> float:
    """Temps CPU du meilleur de ``repeats`` essais : insensible à la préemption par les autres workers."""
    timings = []
    for _ in range(repeats):
        start = time.process_time()
        action()
        timings.append(time.process_time() - start)
    return min(timings)
