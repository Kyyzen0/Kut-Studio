"""Kut-3D : modèle de scène 3D immuable, importeurs et rendu de référence (phases 1 et 2a d'ADR-0009).

Ce paquet n'importe ni `ui` ni `core`, et n'utilise Qt qu'en repli paresseux pour décoder des textures non PNG
(`kut3d.render.images`) : il s'utilise sans fenêtre.
"""

from __future__ import annotations

from kut3d.animation import Pose, evaluate_pose
from kut3d.errors import (
    ImageDecodeError,
    ModelImportError,
    RenderError,
    UnsupportedFormatError,
)
from kut3d.importers import load_model, supported_extensions
from kut3d.model import Model
from kut3d.render import (
    Frame,
    available_backends,
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
    "RenderError",
    "Scene",
    "UnsupportedFormatError",
    "available_backends",
    "evaluate_pose",
    "load_model",
    "register_backend",
    "render_frame",
    "render_sequence",
    "supported_extensions",
]
