"""Les shaders du moteur GPU : sources GLSL, `.qsb` versionnés et manifeste d'empreintes (sans Qt).

Les `.qsb` (Qt Shader Baker : SPIR-V, GLSL, HLSL, MSL) sont COMMITÉS à côté des sources dans `kut3d/gpu/shaders/` :
l'exécution ne compile rien et n'a pas besoin de `pyside6-qsb`. Si un `.qsb` manque, c'est une erreur claire.
Régénération : `python -m kut3d.gpu.build_shaders` (`--check` pour seulement vérifier). Un test échoue si une
source (ou la liste de cibles) change sans régénération.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kut3d.errors import RenderBackendUnavailable

SHADER_DIR = Path(__file__).resolve().parent / "shaders"
MANIFEST = SHADER_DIR / "manifest.json"
SOURCES = ("kut3d.vert", "kut3d.frag")
# Cibles : GLSL desktop 150/330 et ES 300 (les 100 es / 120 n'ont ni MRT ni tableaux d'uniformes assez larges),
# HLSL 5.0 (Direct3D 11), MSL 1.2 (Metal) ; SPIR-V (Vulkan) est toujours inclus.
GLSL_TARGETS = "150,330,300 es"
HLSL_TARGET = "50"
MSL_TARGET = "12"
TARGETS_TAG = f"glsl={GLSL_TARGETS};hlsl={HLSL_TARGET};msl={MSL_TARGET}"


def source_digest(name: str) -> str:
    """Empreinte SHA-256 de la source ET de la liste de cibles (changer l'un impose de régénérer)."""
    data = (SHADER_DIR / name).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data + b"\0" + TARGETS_TAG.encode()).hexdigest()


def expected_manifest() -> dict[str, str]:
    return {name: source_digest(name) for name in SOURCES}


def stale() -> list[str]:
    """Noms dont le `.qsb` manque ou n'est plus à jour par rapport à la source."""
    try:
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(SOURCES)
    wanted = expected_manifest()
    return [
        name
        for name in SOURCES
        if recorded.get(name) != wanted[name]
        or not (SHADER_DIR / f"{name}.qsb").is_file()
    ]


def load_qsb(name: str) -> bytes:
    """Contenu du `.qsb` versionné de `name`, ou `RenderBackendUnavailable` s'il manque."""
    path = SHADER_DIR / f"{name}.qsb"
    try:
        return path.read_bytes()
    except OSError:
        raise RenderBackendUnavailable(
            f"shader {name}.qsb introuvable ({path}) : régénérer avec "
            "`python -m kut3d.gpu.build_shaders` (nécessite pyside6-qsb)."
        ) from None
