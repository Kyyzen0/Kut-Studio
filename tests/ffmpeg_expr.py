"""Évaluateur minimal des expressions FFmpeg produites par Kut-Studio (tests seulement).

Permet de vérifier que l'expression envoyée à FFmpeg calcule **la même
valeur** que le moteur d'animation Python, image par image.
"""

from __future__ import annotations

import math
import re

_FUNCTIONS = {
    "lt": lambda a, b: 1.0 if a < b else 0.0,
    "gte": lambda a, b: 1.0 if a >= b else 0.0,
    "gt": lambda a, b: 1.0 if a > b else 0.0,
    "lte": lambda a, b: 1.0 if a <= b else 0.0,
    "clip": lambda x, lo, hi: min(hi, max(lo, x)),
    "if": lambda c, a, b=0.0: a if c else b,
    "max": max,
    "min": min,
    "floor": lambda x: float(math.floor(x)),
    "sin": math.sin,
    # Canaux du pixel source (``geq``) : opaque par défaut.
    "alpha": lambda x, y: 1.0,
}


def evaluate(expression: str, **variables: float) -> float:
    """Valeur de ``expression`` (syntaxe FFmpeg) pour les variables données."""
    text = expression.replace("\\,", ",")
    for pixel_variable in "XY":                      # coordonnées de ``geq`` : 0 sauf si le test les donne
        if pixel_variable not in variables:
            text = re.sub(rf"\b{pixel_variable}\b", "0", text)
    if not re.fullmatch(r"[\w\s.+\-*/(),]*", text):
        raise ValueError(f"Expression inattendue : {expression!r}")
    text = re.sub(r"\b(lt|gte|gt|lte|clip|if|max|min|alpha|floor|sin)\(", r"_\1(", text)
    scope = {f"_{name}": fn for name, fn in _FUNCTIONS.items()}
    scope.update(variables)
    return float(eval(text, {"__builtins__": {}}, scope))  # noqa: S307 - tests uniquement
