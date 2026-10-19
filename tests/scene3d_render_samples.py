"""Scènes ``.kut3d`` RENDABLES fabriquées par programme pour les tests du rendu d'un clip 3D (lot B2b).

Les scènes de ``tests/scene3d_samples.py`` (lot B2a) se lisent mais ne se rendent pas : leur ``tex.png`` est un faux PNG.
Ici les textures sont de vrais PNG et le fond de la scène est transparent (le fond du projet se voit autour du cube).
"""

from __future__ import annotations

import copy
import struct
import zlib
from pathlib import Path

import numpy as np

from core.graphics import GraphicType, add_graphic_clip
from tests.kut3d_samples import write_cube_obj, write_gltf
from tests.kut3d_scene_samples import minimal_document, write_doc
from tests.scene3d_samples import base_project

SIZE = (64, 36)
FPS = 10.0
BACKGROUND = (32, 96, 160)               # couleur du fond vidéo du projet (0x2060A0)


def png_bytes(color: tuple[int, int, int, int] = (255, 255, 255, 255), size: int = 2) -> bytes:
    """Un vrai PNG RGBA 8 bits uni de ``size`` x ``size`` pixels."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes(color) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def _strip_textures(doc: dict) -> None:
    material = doc["materials"][0]
    material["pbrMetallicRoughness"].pop("baseColorTexture")
    material.pop("normalTexture")
    doc["textures"] = []
    doc["images"] = []


def static_document() -> dict:
    """Un cube au centre, fond transparent, sans animation."""
    doc = copy.deepcopy(minimal_document())
    doc["scene"]["background"] = None
    doc["scene"]["camera"]["position"] = [0, 0, 6]
    return doc


def animated_document() -> dict:
    """Un quad glTF animé (clip « wave », ≈ 1 s) au centre, fond transparent : l'image change avec le temps."""
    doc = copy.deepcopy(minimal_document())
    scene = doc["scene"]
    scene["background"] = None
    scene["camera"]["position"] = [0, 0, 6]
    scene["models"] = [{"id": "quad", "path": "quad.gltf",
                        "normalize": {"up_axis": "y", "unit_scale": 1.0, "target_size": None, "center": False}}]
    scene["instances"] = [{"model": "quad", "transform": list(np.eye(4).reshape(-1)), "clip": "wave", "time_offset": 0,
                           "loop": False, "name": "quad"}]
    return doc


def write_static_scene(folder: Path, name: str = "static.kut3d", *, textured: bool = False) -> Path:
    """Cube rouge sans texture, ou texturé par un vrai ``tex.png`` vert."""
    write_cube_obj(folder, texture=textured)
    if textured:
        (folder / "tex.png").write_bytes(png_bytes((0, 255, 0, 255)))
        (folder / "normal map.png").write_bytes(png_bytes((128, 128, 255, 255)))
    return write_doc(folder / name, static_document())


def write_animated_scene(folder: Path, name: str = "animated.kut3d") -> Path:
    write_gltf(folder, "bin", name="quad", mutate=_strip_textures)
    return write_doc(folder / name, animated_document())


def project_with_background(folder: Path, scene: Path, *, start: float = 0.0, duration: float = 2.0, clip_id: str = "s3d"):
    """Projet 64x36 à 10 i/s : fond vidéo uni (FFmpeg) et un clip 3D par-dessus. Retourne ``(projet, clip)``."""
    import subprocess

    project = base_project()
    project.fps = FPS
    background = folder / "background.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x2060A0:s={SIZE[0]}x{SIZE[1]}:r={int(FPS)}:d=6", "-pix_fmt", "yuv420p", str(background)],
                   check=True)
    project.media_assets[0].path = str(background)
    project.media_assets[0].fps = FPS
    clip = add_graphic_clip(project, GraphicType.SCENE3D, timeline_start=start, duration=duration,
                            source_path=str(scene), clip_id=clip_id)
    return project, clip
