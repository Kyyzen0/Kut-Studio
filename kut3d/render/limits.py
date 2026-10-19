"""Bornes du rendu, vérifiées AVANT toute allocation (une scène et une image sont des entrées non fiables)."""

from __future__ import annotations

MAX_IMAGE_SIDE = 8192  # côté maximal d'une image rendue, en pixels
SAMPLES_CHOICES = (
    1,
    2,
    4,
)  # sur-échantillonnage PAR AXE : 1, 2 ou 4 (soit 1, 4 ou 16 échantillons par pixel)
MAX_SAMPLED_PIXELS = (
    1 << 24
)  # pixels sur-échantillonnés (largeur x hauteur x samples²) : ~16,7 millions
MAX_TRIANGLES = (
    2_000_000  # triangles à rasteriser pour une image (toutes instances confondues)
)
MAX_BLEND_TRIANGLES = (
    100_000  # triangles en alpha-blending (triés et composés un par un)
)
TILE_PIXELS = (
    1 << 20
)  # un grand triangle est rasterisé par bandes de ce nombre de pixels (borne la mémoire)

MAX_TEXTURE_SIDE = 16384  # côté maximal d'une texture décodée
MAX_TEXTURE_PIXELS = 1 << 26  # pixels d'une texture décodée (256 Mio en RGBA 8 bits)
MAX_IMAGE_FILE_BYTES = 1 << 28  # taille d'un fichier image (256 Mio)
TEXTURE_CACHE_ENTRIES = 64
TEXTURE_CACHE_BYTES = 1 << 29  # 512 Mio de textures décodées gardées en cache

MAX_SEQUENCE_FRAMES = 100_000  # images d'une séquence
MAX_FPS = 240.0
