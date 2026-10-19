"""Image rendue (`Frame`), registre de moteurs et validation d'une demande de rendu."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from kut3d.errors import RenderError
from kut3d.mathutil import Array
from kut3d.render.limits import MAX_IMAGE_SIDE, MAX_SAMPLED_PIXELS, SAMPLES_CHOICES
from kut3d.scene import Scene


@dataclass(frozen=True, eq=False)
class Frame:
    """Résultat d'un rendu (tableaux en lecture seule).

    - `color` : (H, W, 4) uint8, sRGB, alpha DROIT (non prémultiplié) ; là où l'alpha est 0, le RGB vaut 0.
    - `alpha` : (H, W) float32 dans [0, 1], couverture (moyenne des échantillons, fond compris).
    - `depth` : (H, W) float32, profondeur de VUE linéaire en unités de scène (distance mesurée le long de l'axe
      de visée, pas la distance euclidienne) de la surface opaque ou masquée la plus proche ; `inf` là où il n'y
      a rien (les surfaces en alpha-blending n'écrivent pas la profondeur). Avec `samples` > 1 : la plus proche
      des échantillons couverts du pixel.
    - `size` : (largeur, hauteur).
    """

    color: Array
    alpha: Array
    depth: Array
    size: tuple[int, int]

    def __post_init__(self) -> None:
        w, h = self.size
        if self.color.shape != (h, w, 4) or self.color.dtype != np.uint8:
            raise ValueError("Frame.color : (H, W, 4) uint8 attendu.")
        if self.alpha.shape != (h, w) or self.depth.shape != (h, w):
            raise ValueError("Frame.alpha et Frame.depth : (H, W) attendu.")
        for arr in (self.color, self.alpha, self.depth):
            arr.setflags(write=False)


Backend = Callable[[Scene, float, tuple[int, int], int], Frame]
# Disponibilité d'un moteur : retourne None s'il est utilisable ici, sinon la raison (une phrase en français).
Availability = Callable[[], str | None]
_BACKENDS: dict[str, Backend] = {}
_AVAILABILITY: dict[str, Availability] = {}


def register_backend(
    name: str, fn: Backend, *, available: Availability | None = None
) -> None:
    """Enregistre un moteur : `fn(scene, t, (largeur, hauteur), samples) -> Frame`. Remplace un moteur du même nom.

    `available` (facultatif) dit si le moteur est utilisable sur cette machine : il retourne None (oui) ou la raison
    du non. Sans lui, le moteur est supposé toujours disponible.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("register_backend : nom de moteur vide.")
    if not callable(fn):
        raise ValueError("register_backend : fn doit être appelable.")
    if available is not None and not callable(available):
        raise ValueError("register_backend : available doit être appelable.")
    _BACKENDS[name] = fn
    if available is None:
        _AVAILABILITY.pop(name, None)
    else:
        _AVAILABILITY[name] = available


def backend_unavailable_reason(name: str) -> str | None:
    """None si le moteur `name` est enregistré ET utilisable ici, sinon la raison (inconnu, ou celle du moteur)."""
    if not isinstance(name, str) or name not in _BACKENDS:
        return "moteur inconnu"
    check = _AVAILABILITY.get(name)
    return None if check is None else check()


def backend_available(name: str) -> bool:
    """Vrai si le moteur `name` est enregistré et utilisable sur cette machine (la sonde GPU est mise en cache)."""
    return backend_unavailable_reason(name) is None


def available_backends() -> tuple[str, ...]:
    """Noms des moteurs enregistrés, triés."""
    return tuple(sorted(_BACKENDS))


def get_backend(name: str) -> Backend:
    fn = _BACKENDS.get(name) if isinstance(name, str) else None
    if fn is None:
        listed = ", ".join(available_backends()) or "aucun"
        raise RenderError(
            f"moteur de rendu inconnu : {str(name)[:40]!r} (moteurs disponibles : {listed})."
        )
    return fn


def check_request(
    size: Any, samples: Any, t: Any
) -> tuple[tuple[int, int], int, float]:
    """Valide (taille, sur-échantillonnage, instant) AVANT toute allocation ; retourne leurs formes normalisées."""
    try:
        w, h = size
        ok = all(
            isinstance(v, (int, np.integer)) and not isinstance(v, bool) for v in (w, h)
        )
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise RenderError("size doit être un couple (largeur, hauteur) d'entiers.")
    w, h = int(w), int(h)
    if not (1 <= w <= MAX_IMAGE_SIDE and 1 <= h <= MAX_IMAGE_SIDE):
        raise RenderError(
            f"taille d'image {w}x{h} hors limites (1 à {MAX_IMAGE_SIDE} pixels par côté)."
        )
    if isinstance(samples, bool) or samples not in SAMPLES_CHOICES:
        raise RenderError(
            f"samples doit valoir l'un de {SAMPLES_CHOICES} (reçu {str(samples)[:20]!r})."
        )
    samples = int(samples)
    if w * h * samples * samples > MAX_SAMPLED_PIXELS:
        raise RenderError(
            f"{w}x{h} avec samples={samples} dépasse {MAX_SAMPLED_PIXELS} pixels sur-échantillonnés : "
            "réduire la taille ou samples."
        )
    try:
        tt = float(t)
    except (TypeError, ValueError):
        raise RenderError("t doit être un nombre.") from None
    if not math.isfinite(tt):
        raise RenderError("t doit être un nombre fini.")
    return (w, h), samples, tt
