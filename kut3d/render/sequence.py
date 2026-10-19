"""Rendu d'une séquence d'images PNG (RGBA) : `frame_000001.png`, `frame_000002.png`..."""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from kut3d.errors import RenderError
from kut3d.render.api import render_frame
from kut3d.render.frame import check_request
from kut3d.render.limits import MAX_FPS, MAX_SEQUENCE_FRAMES
from kut3d.render.png import encode_png
from kut3d.scene import Scene


def write_png_atomic(path: Path, data: bytes) -> None:
    """Écrit `data` dans un fichier temporaire du même dossier puis le renomme : jamais de fichier partiel."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".kut3d-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def render_sequence(
    scene_at: Scene | Callable[[float], Scene],
    *,
    fps: float,
    duration: float,
    size: tuple[int, int],
    out_dir: str | Path,
    backend: str = "numpy",
    samples: int = 1,
) -> list[Path]:
    """Rend `round(duration x fps)` images (au moins une) dans `out_dir` et retourne leurs chemins.

    `scene_at` est soit une `Scene` fixe (ses clips avancent avec le temps), soit une fonction `t -> Scene`
    appelée pour chaque image ; dans les deux cas, `t` = indice / fps est aussi l'instant du rendu. Chaque PNG
    est écrit de façon atomique. En cas d'erreur ou d'interruption, les images déjà terminées restent et aucune
    image partielle n'est laissée. Bornes : `kut3d.render.limits` (images, fps, taille).
    """
    fps_f, dur_f = float(fps), float(duration)
    if not (math.isfinite(fps_f) and 0 < fps_f <= MAX_FPS):
        raise RenderError(
            f"fps doit être dans ]0, {MAX_FPS:g}] (reçu {str(fps)[:20]!r})."
        )
    if not (math.isfinite(dur_f) and dur_f > 0):
        raise RenderError(
            f"duration doit être un nombre fini > 0 (reçu {str(duration)[:20]!r})."
        )
    if dur_f * fps_f > MAX_SEQUENCE_FRAMES + 0.5:
        raise RenderError(f"trop d'images (maximum {MAX_SEQUENCE_FRAMES}).")
    count = max(1, round(dur_f * fps_f))
    check_request(size, samples, 0.0)
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(count):
        t = i / fps_f
        scene = scene_at(t) if callable(scene_at) else scene_at
        if not isinstance(scene, Scene):
            raise RenderError("scene_at(t) doit retourner une Scene.")
        frame = render_frame(scene, t, size, backend=backend, samples=samples)
        path = folder / f"frame_{i + 1:06d}.png"
        write_png_atomic(path, encode_png(frame.color))
        paths.append(path)
    return paths
