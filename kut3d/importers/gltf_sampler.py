"""Sampler glTF -> réglage d'échantillonnage de `TextureRef` (wrap et filtres).

Les filtres à mipmaps (9984 à 9987) sont repliés sur le filtre de base (voisin le plus proche ou linéaire) : le
moteur de rendu ne génère pas de mipmaps. Une valeur inconnue donne le défaut glTF et un avertissement.
"""

from __future__ import annotations

from typing import Any

from kut3d.errors import short

_WRAP = {10497: "repeat", 33071: "clamp", 33648: "mirror"}
_FILTER = {
    9728: "nearest",
    9729: "linear",
    9984: "nearest",  # NEAREST_MIPMAP_NEAREST
    9985: "linear",  # LINEAR_MIPMAP_NEAREST
    9986: "nearest",  # NEAREST_MIPMAP_LINEAR
    9987: "linear",  # LINEAR_MIPMAP_LINEAR
}


def _pick(
    table: dict[int, str],
    sampler: dict[str, Any],
    key: str,
    default: str,
    where: str,
    warnings: list[str],
) -> str:
    if key not in sampler:
        return default
    value = sampler[key]
    if isinstance(value, int) and not isinstance(value, bool) and value in table:
        return table[value]
    warnings.append(f"{where} : {key} {short(value)} inconnu ({default} par défaut)")
    return default


def sampler_fields(
    gltf: dict[str, Any], tex: dict[str, Any], index: int, warnings: list[str]
) -> dict[str, str]:
    """Champs `wrap_s`, `wrap_t`, `mag_filter`, `min_filter` de la texture `index` (défauts si pas de sampler)."""
    out = {
        "wrap_s": "repeat",
        "wrap_t": "repeat",
        "mag_filter": "linear",
        "min_filter": "linear",
    }
    ref = tex.get("sampler")
    if ref is None:
        return out
    samplers = gltf.get("samplers")
    where = f"texture {index} (sampler {short(ref)})"
    if (
        isinstance(ref, bool)
        or not isinstance(ref, int)
        or not isinstance(samplers, list)
        or not 0 <= ref < len(samplers)
        or not isinstance(samplers[ref], dict)
    ):
        warnings.append(f"{where} : sampler inexistant (défauts glTF)")
        return out
    s = samplers[ref]
    out["wrap_s"] = _pick(_WRAP, s, "wrapS", "repeat", where, warnings)
    out["wrap_t"] = _pick(_WRAP, s, "wrapT", "repeat", where, warnings)
    out["mag_filter"] = _pick(_FILTER, s, "magFilter", "linear", where, warnings)
    out["min_filter"] = _pick(_FILTER, s, "minFilter", "linear", where, warnings)
    return out
