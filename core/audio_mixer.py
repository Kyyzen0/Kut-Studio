"""Panoramique des pistes audio — couche métier pure de Kut-Studio.

Ce module ne contient que ce que l'export consomme réellement : la
traduction d'un panoramique en gains stéréo (:func:`pan_to_gains`) et la
question « faut-il un filtre ? » (:func:`pan_needs_filter`), utilisées par
:mod:`core.export_engine` pour construire la chaîne FFmpeg d'un clip.

Qui s'entend, et avec quel gain, se décide dans :mod:`core.render_plan`
(sourdine, solo, volume et panoramique de piste et de clip, fondus,
effets, automation, ducking, séquences imbriquées). Une ancienne
description du mixage instant par instant (``mix_at``, ``MixSpec``) n'était
consultée par aucun chemin de l'application et avait fini par contredire
le plan de rendu sur le solo d'une piste muette : elle a été supprimée
(``docs/dead-code-audit.md``).

Aucune dépendance à PySide6 ni à FFmpeg : le module est testable en CLI.

Convention de puissance constante : le panoramique atténue, il
n'amplifie jamais. Un son centré à 0 dB ressort donc à 0 dB ; poussé
à fond d'un côté il est atténué, ce qui évite toute surprise de
niveau lors d'un déplacement au pan.
"""

from __future__ import annotations

import math

# ``core.export_engine`` lit les bornes de gain ici : on les ré-exporte telles quelles.
from .project_model import MAX_GAIN_DB, MIN_GAIN_DB


def pan_to_gains(pan: float) -> tuple[float, float]:
    """Traduit un panoramique ``[-1, 1]`` en gains stéréo.

    Constante-power : le gain total reste cohérent quand le son glisse
    d'un côté à l'autre.

    Returns:
        ``(gain_left, gain_right)``, tous deux dans ``[0, 1]``.
    """
    try:
        value = float(pan)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:  # NaN
        value = 0.0
    value = max(-1.0, min(1.0, value))
    angle = (value + 1.0) * (math.pi / 4.0)
    return math.cos(angle), math.sin(angle)


def pan_needs_filter(pan: float) -> bool:
    """Le panoramique exige-t-il un filtre de rendu ?

    Un panoramique centré n'en demande aucun : l'appelant peut ainsi
    **omettre** le filtre plutôt que d'appliquer un panoramique neutre.
    """
    try:
        return abs(float(pan)) > 1e-6
    except (TypeError, ValueError):
        return False


__all__ = [
    "MAX_GAIN_DB",
    "MIN_GAIN_DB",
    "pan_needs_filter",
    "pan_to_gains",
]
