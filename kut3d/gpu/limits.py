"""Bornes et constantes du moteur GPU (sans Qt : lisibles par les tests et par `kut3d.gpu.prepare`)."""

from __future__ import annotations

# Articulations d'un dessin à peau déformé dans le vertex shader (tableau `mat4 joints[128]` du bloc d'uniformes,
# 8 Kio, sous les 64 Kio garantis partout). L'identité (sommets sans poids) en occupe une : 127 os utilisables.
# Au-delà, repli : le dessin est déformé côté CPU (`kut3d.render.assemble`), plus lent mais identique.
MAX_GPU_JOINTS = 128
MAX_GPU_LIGHTS = 16  # = kut3d.scene.MAX_LIGHTS (tableau `lightA[16]` du shader)

TEXTURE_CACHE_BYTES = (
    1 << 29
)  # 512 Mio de textures gardées sur le GPU d'une image à l'autre
PROBE_TIMEOUT_S = (
    30.0  # délai de la sonde (sous-processus) avant de déclarer le GPU inutilisable
)
PROBE_TRIANGLE_SIZE = 16  # côté (pixels) de l'image rendue par la sonde
