"""Matériaux prêts à rendre : facteurs + textures décodées (en cache), mode alpha en entier."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kut3d.mathutil import Array
from kut3d.model import ALPHA_MODES, Material, TextureRef
from kut3d.render.images import load_texture_image

# Matériau d'un maillage qui n'en a pas (ADR-0009 : « gris par défaut »).
DEFAULT_MATERIAL = Material(
    name="défaut", base_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.8
)
MODE_OPAQUE, MODE_MASK, MODE_BLEND = (ALPHA_MODES.index(m) for m in ALPHA_MODES)
MIN_ROUGHNESS = 0.045  # sous cette valeur, le lobe GGX devient une aiguille numérique


@dataclass(frozen=True, eq=False)
class PreparedMaterial:
    base: Array  # (4,) facteur de couleur de base (linéaire) et alpha
    base_tex: Array | None
    normal_tex: Array | None
    mr_tex: Array | None  # canal G = rugosité, B = métal (glTF)
    emissive_tex: Array | None
    occlusion_tex: Array | None  # canal R
    metallic: float
    roughness: float
    emissive: Array  # (3,)
    mode: int
    cutoff: float
    double_sided: bool


def _tex(ref: TextureRef | None, use: bool) -> Array | None:
    return load_texture_image(ref) if (use and ref is not None) else None


def prepare_material(material: Material | None, use_textures: bool) -> PreparedMaterial:
    """`use_textures=False` (maillage sans UV) : les textures sont ignorées et seuls les facteurs s'appliquent."""
    m = DEFAULT_MATERIAL if material is None else material
    return PreparedMaterial(
        base=np.asarray(m.base_color, dtype=np.float32),
        base_tex=_tex(m.base_color_texture, use_textures),
        normal_tex=_tex(m.normal_texture, use_textures),
        mr_tex=_tex(m.metallic_roughness_texture, use_textures),
        emissive_tex=_tex(m.emissive_texture, use_textures),
        occlusion_tex=_tex(m.occlusion_texture, use_textures),
        metallic=float(np.clip(m.metallic, 0.0, 1.0)),
        roughness=float(np.clip(m.roughness, 0.0, 1.0)),
        emissive=np.asarray(m.emissive, dtype=np.float32),
        mode=ALPHA_MODES.index(m.alpha_mode),
        cutoff=float(m.alpha_cutoff),
        double_sided=bool(m.double_sided),
    )
