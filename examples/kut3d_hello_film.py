"""Premier film Kut-3D : un cube texturé (damier fabriqué ici) qui tourne 3 s au-dessus d'un fond, exporté en MP4.

    .venv/bin/python examples/kut3d_hello_film.py out/

Écrit dans le dossier donné : hello.kut, hello_assets/ (le plan en ProRes 4444 avec transparence), hello.mp4.
Aucune fenêtre, aucun fichier externe. Voir docs/kut3d-film.md.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # lancé depuis un clone, sans installation

from core.film import Film  # noqa: E402
from kut3d import AmbientLight, Camera, DirectionalLight, Instance, Scene  # noqa: E402
from kut3d.mathutil import trs_to_matrix  # noqa: E402
from kut3d.model import Material, Mesh, Model, Node, TextureRef  # noqa: E402
from kut3d.render.png import encode_png  # noqa: E402

SIZE = (1280, 720)
FPS = 24
SECONDS = 3.0


def checker_cube() -> Model:
    """Un cube de côté 2, six faces texturées d'un damier orange et crème."""
    tiles = np.indices((8, 8)).sum(axis=0) % 2 == 0
    cells = np.where(tiles[..., None], (235, 120, 40, 255), (245, 235, 210, 255)).astype(np.uint8)
    texture = TextureRef(name="damier", data=encode_png(np.kron(cells, np.ones((8, 8, 1), np.uint8))), mime_type="image/png")
    faces = (  # normale, axe u, axe v
        ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)), ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
        ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    )
    positions: list[list[float]] = []
    uvs: list[list[float]] = []
    normals: list[list[float]] = []
    triangles: list[list[int]] = []
    for face, (n, u, v) in enumerate(faces):
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            positions.append([n[i] + su * u[i] + sv * v[i] for i in range(3)])
            uvs.append([(su + 1) / 2, 1 - (sv + 1) / 2])
            normals.append(list(map(float, n)))
        first = 4 * face
        triangles += [[first, first + 1, first + 2], [first, first + 2, first + 3]]
    mesh = Mesh(positions, triangles, normals=normals, uvs=uvs, material_index=0)
    material = Material(base_color_texture=texture, roughness=0.8)
    return Model(nodes=(Node(mesh_index=0),), root_nodes=(0,), meshes=(mesh,), materials=(material,))


def main(out_dir: Path, *, backend: str = "auto", size: tuple[int, int] = SIZE, seconds: float = SECONDS) -> Path:
    cube = checker_cube()
    camera = Camera((2.6, 2.0, 4.2), (0.0, 0.0, 0.0))
    lights = [AmbientLight((1.0, 1.0, 1.0), 0.25), DirectionalLight((-0.4, -0.7, -0.6), (1.0, 1.0, 1.0), 1.0)]

    def scene_at(t: float) -> Scene:
        turn = 2 * math.pi * t / seconds                       # un tour exact : la boucle est propre
        half = turn / 2
        spin = (0.0, math.sin(half), 0.0, math.cos(half))      # quaternion xyzw autour de Y
        return Scene([Instance(cube, trs_to_matrix((0, 0, 0), spin, (1, 1, 1)))], lights, camera)

    film = Film(out_dir / "hello.kut", size=size, fps=FPS)
    film.background((0.05, 0.06, 0.12))
    shot = film.render_shot(scene_at, duration=seconds, name="cube", samples=2, backend=backend, overwrite=True)
    film.place(shot, start=0.0)
    film.save()
    return film.export(out_dir / "hello.mp4")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage : python examples/kut3d_hello_film.py <dossier de sortie>")
    sys.stdout.write(f"{main(Path(sys.argv[1]))}\n")
