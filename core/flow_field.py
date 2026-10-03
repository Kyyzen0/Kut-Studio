"""Le champ de mouvement entre deux images : ce que le flux optique produit et ce que le cache stocke.

Un :class:`FlowField` associe à chaque pixel de l'image de départ son **déplacement apparent** vers l'image d'arrivée, en
pixels de la grille où l'analyse a eu lieu (``Full``, ``Half`` ou ``Quarter`` de la résolution de travail), et une
**confiance** par pixel entre 0 et 1.

La confiance est mesurée, pas supposée : c'est la capacité du flux à *expliquer* l'image suivante (erreur photométrique
après déformation) et sa cohérence aller-retour (le flux inverse, lu à l'arrivée, doit ramener au départ). Elle tombe aux
occlusions, aux changements d'éclairage brutaux et aux mouvements que le modèle ne sait pas suivre — exactement là où une image
interpolée serait fausse.

Le champ se range dans le cache sous forme **quantifiée** (demi-précision : 0,03 px de résolution jusqu'à 64 px de déplacement,
la moitié de la place) ; cette quantification est la seule perte, et elle est mesurée (``tests/test_flow_field.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import numpy.typing as npt

    Plane = npt.NDArray[np.float32]


@dataclass(frozen=True)
class FlowField:
    """Déplacement ``(u, v)`` de chaque pixel de l'image de départ, et confiance, sur une grille ``(hauteur, largeur)``."""

    u: Plane
    v: Plane
    confidence: Plane

    def __post_init__(self) -> None:
        if not (self.u.shape == self.v.shape == self.confidence.shape) or self.u.ndim != 2:
            raise ValueError("u, v et la confiance doivent avoir la même forme (hauteur, largeur)")

    @property
    def height(self) -> int:
        return int(self.u.shape[0])

    @property
    def width(self) -> int:
        return int(self.u.shape[1])

    @property
    def mean_confidence(self) -> float:
        return float(self.confidence.mean())

    def resized(self, width: int, height: int) -> FlowField:
        """Le même mouvement sur une autre grille : échantillonné en bilinéaire, vecteurs mis à l'échelle."""
        if (width, height) == (self.width, self.height):
            return self
        sx, sy = width / self.width, height / self.height
        return FlowField(
            (resample(self.u, width, height) * sx).astype(np.float32, copy=False),
            (resample(self.v, width, height) * sy).astype(np.float32, copy=False),
            np.clip(resample(self.confidence, width, height), 0.0, 1.0).astype(np.float32, copy=False),
        )

    def pack(self) -> npt.NDArray[np.float16]:
        """Forme stockée : un tableau ``(3, hauteur, largeur)`` en demi-précision (``u``, ``v``, confiance)."""
        return np.stack((self.u, self.v, self.confidence)).astype(np.float16)

    @classmethod
    def unpack(cls, packed: npt.NDArray[np.float16]) -> FlowField:
        """Inverse de :meth:`pack` ; refuse une forme ou des valeurs incohérentes (le cache jette alors l'entrée)."""
        if packed.ndim != 3 or packed.shape[0] != 3 or packed.dtype != np.float16:
            raise ValueError("forme ou type inattendus pour un champ de mouvement stocké")
        if not np.isfinite(packed).all():
            raise ValueError("valeurs non finies dans un champ de mouvement stocké")
        planes = packed.astype(np.float32)
        return cls(planes[0], planes[1], np.clip(planes[2], 0.0, 1.0))


def resample(plane: Plane, width: int, height: int) -> Plane:
    """Rééchantillonne un plan ``(h, w)`` en ``(height, width)`` : bilinéaire, centres de pixels alignés."""
    source_h, source_w = plane.shape
    if (source_w, source_h) == (width, height):
        return plane
    ys = (np.arange(height, dtype=np.float32) + 0.5) * (source_h / height) - 0.5
    xs = (np.arange(width, dtype=np.float32) + 0.5) * (source_w / width) - 0.5
    ys = np.clip(ys, 0.0, source_h - 1.0)
    xs = np.clip(xs, 0.0, source_w - 1.0)
    y0 = np.floor(ys).astype(np.intp)
    x0 = np.floor(xs).astype(np.intp)
    y1 = np.minimum(y0 + 1, source_h - 1)
    x1 = np.minimum(x0 + 1, source_w - 1)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    top = plane[y0][:, x0] * (1.0 - fx) + plane[y0][:, x1] * fx
    bottom = plane[y1][:, x0] * (1.0 - fx) + plane[y1][:, x1] * fx
    return (top * (1.0 - fy) + bottom * fy).astype(np.float32, copy=False)


__all__ = ["FlowField", "resample"]
