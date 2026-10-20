"""Film Kut-3D avec un VRAI clip « scène 3D » : un cube texturé (fabriqué ici) posé dans la timeline, exporté en MP4.

    .venv/bin/python examples/kut3d_scene_film.py out/

Écrit dans le dossier donné : scene.kut, scene_assets/ (cube.kut3d et la copie de ses modèles), scene.mp4. Contrairement à
``kut3d_hello_film.py`` (qui rend une vidéo ProRes), ici ``Film.place_scene`` écrit la scène et pose un calque graphique : le projet
s'ouvre dans Kut-Studio, le clip se règle et se rend avec le reste. Aucune fenêtre, aucun fichier externe. Voir docs/kut3d-film.md.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # lancé depuis un clone, sans installation

from core.film import Film  # noqa: E402
from kut3d import AmbientLight, Camera, DirectionalLight, Instance, Scene, load_model  # noqa: E402
from kut3d.mathutil import trs_to_matrix  # noqa: E402
from kut3d.render.png import encode_png  # noqa: E402

SIZE = (1280, 720)
FPS = 24
SECONDS = 3.0

_FACES = (  # normale, axe u, axe v
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)), ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
)


def write_checker_cube(folder: Path) -> Path:
    """Un cube de côté 2 en OBJ + MTL + damier PNG dans ``folder`` : un vrai modèle, que ``place_scene`` copie avec ses dépendances."""
    tiles = np.indices((8, 8)).sum(axis=0) % 2 == 0
    cells = np.where(tiles[..., None], (235, 120, 40, 255), (245, 235, 210, 255)).astype(np.uint8)
    (folder / "damier.png").write_bytes(encode_png(np.kron(cells, np.ones((8, 8, 1), np.uint8))))
    (folder / "cube.mtl").write_text("newmtl damier\nKd 1 1 1\nmap_Kd damier.png\n", encoding="utf-8")
    lines = ["mtllib cube.mtl", "usemtl damier"]
    for n, u, v in _FACES:
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        lines += [f"v {n[0] + su * u[0] + sv * v[0]} {n[1] + su * u[1] + sv * v[1]} {n[2] + su * u[2] + sv * v[2]}" for su, sv in corners]
        lines += [f"vt {(su + 1) / 2} {(sv + 1) / 2}" for su, sv in corners]
        lines.append(f"vn {n[0]} {n[1]} {n[2]}")
    for face in range(6):
        ids = [4 * face + k + 1 for k in range(4)]
        normal = face + 1
        lines.append("f " + " ".join(f"{i}/{i}/{normal}" for i in ids))
    (folder / "cube.obj").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder / "cube.obj"


def main(out_dir: Path, *, size: tuple[int, int] = SIZE, seconds: float = SECONDS) -> Path:
    with tempfile.TemporaryDirectory(prefix="kut3d_cube_") as source:
        cube = load_model(write_checker_cube(Path(source)))
        turn = math.radians(35)
        spin = (0.0, math.sin(turn / 2), 0.0, math.cos(turn / 2))                # quaternion xyzw autour de Y
        scene = Scene(
            [Instance(cube, trs_to_matrix((0, 0, 0), spin, (1, 1, 1)))],
            [AmbientLight((1.0, 1.0, 1.0), 0.25), DirectionalLight((-0.4, -0.7, -0.6), (1.0, 1.0, 1.0), 1.0)],
            Camera((2.6, 2.0, 4.2), (0.0, 0.0, 0.0)),
        )
        film = Film(out_dir / "scene.kut", size=size, fps=FPS)
        film.background((0.05, 0.06, 0.12))
        film.place_scene(scene, duration=seconds, start=0.0, name="cube", overwrite=True)   # copie le modèle : le dossier temporaire peut disparaître
        film.save()
    return film.export(out_dir / "scene.mp4")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage : python examples/kut3d_scene_film.py <dossier de sortie>")
    sys.stdout.write(f"{main(Path(sys.argv[1]))}\n")
