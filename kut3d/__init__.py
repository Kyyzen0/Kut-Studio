"""Kut-3D : modèle de scène 3D immuable, importeurs, rendu de référence et moteur GPU (phases 1, 2a et 2b d'ADR-0009).

Ce paquet n'importe ni `ui` ni `core`. Qt n'est autorisé que dans `kut3d/gpu/` (moteur « qrhi ») et, en repli
paresseux, dans `kut3d/render/images.py` (textures non PNG) ; `import kut3d` ne charge JAMAIS PySide6.
"""

from __future__ import annotations

from kut3d.animation import Pose, evaluate_pose
from kut3d.errors import (
    ImageDecodeError,
    ModelImportError,
    RenderBackendUnavailable,
    RenderError,
    UnsupportedFormatError,
)
from kut3d.gpu import probe_qrhi
from kut3d.importers import load_model, supported_extensions
from kut3d.model import Model
from kut3d.render import (
    Frame,
    available_backends,
    backend_available,
    register_backend,
    render_frame,
    render_sequence,
)
from kut3d.scene import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
)

__all__ = [
    "AmbientLight",
    "Camera",
    "DirectionalLight",
    "Frame",
    "ImageDecodeError",
    "Instance",
    "Model",
    "ModelImportError",
    "PointLight",
    "Pose",
    "RenderBackendUnavailable",
    "RenderError",
    "Scene",
    "UnsupportedFormatError",
    "available_backends",
    "backend_available",
    "evaluate_pose",
    "load_model",
    "probe_qrhi",
    "register_backend",
    "render_frame",
    "render_sequence",
    "supported_extensions",
]
