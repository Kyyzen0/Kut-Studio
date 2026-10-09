"""Qualifieur TSL d'un nœud d'étalonnage : la partie de l'image que le nœud corrige.

Pour chaque pixel **d'entrée du nœud** (R, V, B dans 0..1), une clé 0..1 :

- **teinte** ``h`` (0–360°, hexagonale) : 1 à moins de ``largeur / 2`` du centre (distance circulaire), 0 au-delà de
  ``largeur / 2 + douceur``, linéaire entre les deux. Un gris n'a pas de teinte : il n'est dans aucune plage de teinte
  (sauf une plage de 360°) ;
- **saturation** ``s = max − min`` (la chroma : un pixel sombre et bruité n'est pas « saturé », ce qu'une saturation
  TSL ou TSV dirait) ;
- **luminance** ``y = 0,2126·R + 0,7152·V + 0,0722·B`` (Rec. 709) ;
- saturation et luminance : 1 dans ``[bas, haut]``, 0 au-delà de la douceur de chaque côté, linéaire entre les deux ;
- clé = produit des composantes utilisées, inversée si demandé.

Le nœud corrige alors ``sortie = entrée + clé·(étalonné − entrée)`` (:mod:`core.color_render`).

À l'export, la clé, qui ne dépend que du pixel, est tabulée sur un réseau de :data:`KEY_LUT_SIZE`\\ ³ points en LUT 3D
``.cube`` (dans le cache, nommée par son contenu : un réglage déjà vu ne se réécrit pas) et appliquée par ``lut3d``
(tétraédrique) ; l'étalonnage du nœud, lui, reste la chaîne exacte. Une bascule franche (douceur nulle) s'étale donc
sur un pas du réseau (≈ 4 niveaux), comme un léger anticrénelage.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

from .atomic_io import atomic_write_text
from .platform_paths import user_cache_dir

KEY_LUT_SIZE = 65
KEY_CACHE_SUBDIRECTORY = "color_keys"
LUMA_709 = (0.2126, 0.7152, 0.0722)


class QualifierError(ValueError):
    """Réglage de qualifieur hors bornes."""


_BOUNDS: dict[str, tuple[float, float]] = {
    "hue_center": (0.0, 360.0),
    "hue_width": (0.0, 360.0),
    "hue_soft": (0.0, 180.0),
    "sat_low": (0.0, 1.0),
    "sat_high": (0.0, 1.0),
    "sat_soft": (0.0, 1.0),
    "lum_low": (0.0, 1.0),
    "lum_high": (0.0, 1.0),
    "lum_soft": (0.0, 1.0),
}


@dataclass(frozen=True)
class Qualifier:
    """Plages de teinte, saturation et luminance ; par défaut tout est sélectionné (resserrer ensuite)."""

    enabled: bool = True
    use_hue: bool = True
    hue_center: float = 0.0
    hue_width: float = 360.0
    hue_soft: float = 0.0
    use_sat: bool = True
    sat_low: float = 0.0
    sat_high: float = 1.0
    sat_soft: float = 0.0
    use_lum: bool = True
    lum_low: float = 0.0
    lum_high: float = 1.0
    lum_soft: float = 0.0
    invert: bool = False

    def __post_init__(self) -> None:
        for name, (low, high) in _BOUNDS.items():
            value = float(getattr(self, name))
            if not math.isfinite(value) or not low <= value <= high:
                raise QualifierError(f"Qualifieur : {name} hors de [{low}, {high}] : {value!r}.")
            object.__setattr__(self, name, value)
        if self.sat_low > self.sat_high or self.lum_low > self.lum_high:
            raise QualifierError("Qualifieur : le bas d'une plage dépasse son haut.")

    def selects_everything(self) -> bool:
        """La clé vaut 1 partout (plages entières, rien d'inversé) : le nœud agit comme sans qualifieur."""
        whole_hue = not self.use_hue or self.hue_width >= 360.0
        whole_sat = not self.use_sat or (self.sat_low <= 0.0 and self.sat_high >= 1.0)
        whole_lum = not self.use_lum or (self.lum_low <= 0.0 and self.lum_high >= 1.0)
        return whole_hue and whole_sat and whole_lum and not self.invert

    def restricts(self) -> bool:
        """Le qualifieur change-t-il ce que le nœud corrige ?"""
        return self.enabled and not self.selects_everything()

    def key(self, rgb: np.ndarray) -> np.ndarray:
        """Clé (``…``) des pixels ``rgb`` (``… × 3``, 0..1)."""
        rgb = np.asarray(rgb, dtype=np.float64)
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        high, low = rgb.max(axis=-1), rgb.min(axis=-1)
        chroma = high - low
        key = np.ones(rgb.shape[:-1])
        if self.use_hue and self.hue_width < 360.0:
            hue = _hue_degrees(r, g, b, high, chroma)
            distance = np.abs((hue - self.hue_center + 180.0) % 360.0 - 180.0)
            key = key * _ramp(self.hue_width / 2.0 - distance, self.hue_soft) * (chroma > 0)
        if self.use_sat:
            key = key * _band(chroma, self.sat_low, self.sat_high, self.sat_soft)
        if self.use_lum:
            luma = LUMA_709[0] * r + LUMA_709[1] * g + LUMA_709[2] * b
            key = key * _band(luma, self.lum_low, self.lum_high, self.lum_soft)
        return 1.0 - key if self.invert else key

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "Qualifier | None":
        """Relit :meth:`to_dict` ; ``None`` si illisible (le nœud corrige alors toute l'image)."""
        if not isinstance(raw, dict):
            return None
        defaults = cls()
        values: dict[str, object] = {}
        for name in (item.name for item in fields(cls)):
            value = raw.get(name)
            if isinstance(getattr(defaults, name), bool):
                if isinstance(value, bool):
                    values[name] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                values[name] = float(value)
        try:
            return cls(**values)  # type: ignore[arg-type]
        except QualifierError:
            return None


def _hue_degrees(r, g, b, high, chroma) -> np.ndarray:
    """Teinte hexagonale (0 rouge, 120 vert, 240 bleu) ; 0 pour un gris."""
    safe = np.where(chroma > 0, chroma, 1.0)
    hue = np.where(high == r, ((g - b) / safe) % 6.0, np.where(high == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0))
    return np.where(chroma > 0, hue * 60.0, 0.0)


def _ramp(inside: np.ndarray, soft: float) -> np.ndarray:
    """1 quand ``inside ≥ 0``, 0 quand ``inside ≤ −soft``, linéaire entre (franc si ``soft`` est nul)."""
    if soft <= 0.0:
        return (inside >= 0).astype(np.float64)
    return np.clip(1.0 + inside / soft, 0.0, 1.0)


def _band(value: np.ndarray, low: float, high: float, soft: float) -> np.ndarray:
    return _ramp(value - low, soft) * _ramp(high - value, soft)


# ---------------------------------------------------------------------------
# LUT de la clé (export)
# ---------------------------------------------------------------------------


def key_cube_text(qualifier: Qualifier, size: int = KEY_LUT_SIZE) -> str:
    """LUT 3D ``.cube`` de la clé (la même valeur dans les trois canaux ; R varie le plus vite)."""
    steps = np.linspace(0.0, 1.0, size)
    blue, green, red = np.meshgrid(steps, steps, steps, indexing="ij")
    key = qualifier.key(np.stack((red, green, blue), axis=-1)).reshape(-1)
    lines = [f"{value:.6f} {value:.6f} {value:.6f}" for value in key.tolist()]
    return f"TITLE \"Kut-Studio qualifier\"\nLUT_3D_SIZE {size}\n" + "\n".join(lines) + "\n"


def key_cube_path(qualifier: Qualifier) -> Path:
    """Fichier ``.cube`` de la clé, écrit dans le cache s'il n'y est pas encore (nommé par son contenu)."""
    signature = repr(sorted(qualifier.to_dict().items())) + f"|{KEY_LUT_SIZE}"
    name = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:20]
    path = Path(user_cache_dir()) / KEY_CACHE_SUBDIRECTORY / f"{name}.cube"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, key_cube_text(qualifier), durable=False)     # un cache : se réécrit au besoin
    return path
