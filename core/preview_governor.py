"""Signaux de charge de l'aperçu, en plus de la cadence des ticks.

La qualité adaptative (:mod:`core.preview_adaptive`) mesure d'abord ce que
l'utilisateur subit : des ticks de lecture en retard. Avec le moniteur GPU, le
rendu se fait hors de la boucle des ticks ; une surcharge se voit alors à des
**images perdues** (décodées mais remplacées avant d'être affichées) ou à un
**temps de rendu** qui dépasse le budget d'une image. Ces signaux ne décident
jamais seuls : ils marquent la fenêtre de mesure comme surchargée, et la
politique anti-« yo-yo » existante (fenêtres consécutives, délai de repos)
s'applique telle quelle.

Échelle de dégradation complète (ADR-0001) : :mod:`core.runtime_regulator`
fixe les crans de base d'après le banc machine et l'état énergie ; ce module
ne fournit que le signal de charge GPU pour monter temporairement le diviseur.

Leviers, du plus doux au plus fort (aucun ne touche à l'export) :

1. proxies préférés pour l'aperçu (:mod:`core.runtime_regulator`) ;
2. effets de voisinage en 1/2 (:func:`core.gpu_effects.program_for`) ;
3. flou de mouvement coupé en lecture (:mod:`core.motion_blur`) ;
4. résolution d'aperçu (diviseur profil + cran batterie/chauffe + adaptatif) ;
5. cadence de lecture (jamais à l'arrêt ni en scrubbing) ;
6. préchargement des segments fidèles réduit ou suspendu (batterie) ;
7. repli GPU → CPU sur erreur (:mod:`core.gpu_backend`).
"""

from __future__ import annotations

FRAME_BUDGET_MS = 40.0
"""Une image du timer de lecture (25 i/s)."""

DROP_RATIO_LIMIT = 0.20
RENDER_BUDGET_RATIO = 0.75
MIN_SAMPLES = 25


def gpu_overloaded(stats, *, frame_budget_ms: float = FRAME_BUDGET_MS) -> bool:
    """``True`` si le moniteur GPU ne suit pas (images perdues ou rendu trop long)."""
    received = int(getattr(stats, "received", 0) or 0)
    if received < MIN_SAMPLES:
        return False
    if stats.drop_ratio() > DROP_RATIO_LIMIT:
        return True
    p95 = stats.p95_render_ms()
    return p95 is not None and p95 > RENDER_BUDGET_RATIO * frame_budget_ms


__all__ = ["DROP_RATIO_LIMIT", "FRAME_BUDGET_MS", "RENDER_BUDGET_RATIO", "gpu_overloaded"]
