"""Kut-3D : modèle de scène 3D immuable, importeurs, rendu de référence et moteur GPU (phases 1, 2a et 2b d'ADR-0009).

Ce paquet n'importe ni `ui` ni `core`. Qt n'est autorisé que dans `kut3d/gpu/` (moteur « qrhi ») et, en repli
paresseux, dans `kut3d/render/images.py` (textures non PNG) ; `import kut3d` ne charge JAMAIS PySide6.
"""

from __future__ import annotations

from kut3d.animation import Pose, evaluate_pose
from kut3d.document import DocumentConflict, SceneDocument
from kut3d.document_state import DocumentError, pose_matrix
from kut3d.errors import (
    ImageDecodeError,
    ModelImportError,
    RenderBackendUnavailable,
    RenderError,
    SceneFileError,
    SceneVersionError,
    UnsupportedFormatError,
)
from kut3d.gpu import probe_qrhi
from kut3d.importers import load_model, supported_extensions
from kut3d.model import Material, Model
from kut3d.primitive_models import primitive_instance, primitive_model
from kut3d.primitives import Cone, Cube, Cylinder, Plane, Sphere, Text3D, Torus
from kut3d.render import (
    Frame,
    available_backends,
    backend_available,
    register_backend,
    render_frame,
    render_sequence,
)
from kut3d.scene_io import (
    PRIMITIVES_FORMAT_VERSION,
    SCENE_FORMAT_VERSION,
    TEXT_FORMAT_VERSION,
    dependency_files,
    load_scene,
    save_scene,
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
    "PRIMITIVES_FORMAT_VERSION",
    "SCENE_FORMAT_VERSION",
    "TEXT_FORMAT_VERSION",
    "AmbientLight",
    "Camera",
    "Cone",
    "Cube",
    "Cylinder",
    "DirectionalLight",
    "DocumentConflict",
    "DocumentError",
    "Frame",
    "ImageDecodeError",
    "Instance",
    "Material",
    "Model",
    "ModelImportError",
    "Plane",
    "PointLight",
    "Pose",
    "RenderBackendUnavailable",
    "RenderError",
    "Scene",
    "SceneDocument",
    "SceneFileError",
    "SceneVersionError",
    "Sphere",
    "Text3D",
    "Torus",
    "UnsupportedFormatError",
    "available_backends",
    "backend_available",
    "dependency_files",
    "evaluate_pose",
    "load_model",
    "load_scene",
    "pose_matrix",
    "primitive_instance",
    "primitive_model",
    "probe_qrhi",
    "register_backend",
    "render_frame",
    "render_sequence",
    "save_scene",
    "supported_extensions",
]
