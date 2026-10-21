"""Scène ``.kut3d`` minimale fabriquée par programme pour les tests du clip « scène 3D » (lot B2a)."""

from __future__ import annotations

from pathlib import Path

from core.graphics import GraphicType, add_graphic_clip
from core.project_model import Clip, MediaAsset, Project, Track
from tests.kut3d_samples import write_cube_obj
from tests.kut3d_scene_samples import minimal_document, write_doc


def write_scene(folder: Path, name: str = "scene.kut3d") -> Path:
    """Un ``.kut3d`` valide (un cube ``cube.obj`` + ``cube.mtl`` + ``tex.png`` dans le même dossier)."""
    write_cube_obj(folder, name="cube")
    return write_doc(folder / name, minimal_document())


def base_project() -> Project:
    asset = MediaAsset("m1", "/tmp/a.mp4", "a", 20.0, 64, 36, 25.0, "video")
    clips = [Clip("a", "m1", "V1", 0.0, 0.0, 3.0), Clip("b", "m1", "V1", 3.0, 0.0, 3.0)]
    return Project(
        "p",
        width=64,
        height=36,
        fps=25.0,
        media_assets=[asset],
        tracks=[Track("V1", "V1", "video", clips=clips)],
    )


def project_with_scene(
    folder: Path, *, clip_id: str = "s3d", start: float = 1.0, duration: float = 5.0
) -> tuple[Project, Clip, Path]:
    project = base_project()
    scene = write_scene(folder)
    clip = add_graphic_clip(
        project,
        GraphicType.SCENE3D,
        timeline_start=start,
        duration=duration,
        source_path=str(scene),
        clip_id=clip_id,
    )
    return project, clip, scene
