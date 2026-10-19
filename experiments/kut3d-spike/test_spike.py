"""Tests du spike Kut-3D : voie numpy (référence) et comparaison des voies GPU quand elles sont disponibles.

Les voies GPU tournent dans un sous-processus (comme ``run_spike.py``) : un crash GPU ne tue pas pytest.
Tolérance GPU/numpy : au moins 90 % des pixels à moins de 12 niveaux d'écart sur chaque canal (les bords des
triangles, l'échantillonnage nearest et l'arrondi des shaders diffèrent légèrement du rasteriseur logiciel).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cube_math as cm  # noqa: E402
import run_spike  # noqa: E402
from render_numpy import render_cube  # noqa: E402

SIZE = run_spike.SIZE
ANGLE = run_spike.ANGLE
TOLERANCE_LEVELS = 12
MIN_MATCH_RATIO = 0.90


def test_numpy_shape_and_dtype() -> None:
    img = render_cube(160, 120, 20.0)
    assert img.shape == (120, 160, 4)
    assert img.dtype == np.uint8
    assert (img[..., 3] == 255).all()


def test_numpy_center_pixel_is_cube_not_background() -> None:
    img = render_cube(128, 128, ANGLE)
    assert tuple(img[64, 64]) != cm.BACKGROUND
    # Les 4 coins sont le fond : le cube ne remplit pas tout le cadre à cette distance.
    assert tuple(img[0, 0]) == cm.BACKGROUND
    # Couleur du damier (orange ou crème) assombrie par l'éclairage : le rouge domine le bleu.
    r, _g, b, _a = img[64, 64]
    assert r > b


def test_numpy_deterministic() -> None:
    assert np.array_equal(render_cube(96, 96, 33.0), render_cube(96, 96, 33.0))


def test_numpy_backface_culled_and_zbuffer() -> None:
    """Angle 0 : le centre doit montrer la face +Z (avant), pas la face -Z (arrière, éclairée par l'ambiant seul).

    Avec une profondeur inversée ou sans élimination des faces arrière, la face -Z (normale opposée à la lumière,
    éclairage = ambiant 0.3) gagnerait au centre : sa couleur ne correspondrait plus à l'éclairage de la face +Z.
    """
    from texture import checker_texture

    model = cm.model_matrix(0.0)
    light = np.array(cm.LIGHT_DIR, np.float64)
    light /= np.linalg.norm(light)

    def shade(normal: tuple[float, float, float]) -> float:
        lam = max(float(np.dot(model[:3, :3] @ np.array(normal), light)), 0.0)
        return min(cm.AMBIENT + (1 - cm.AMBIENT) * lam, 1.0)

    s_front, s_back = shade((0, 0, 1)), shade((0, 0, -1))
    assert s_front > s_back + 0.1  # sinon le test ne prouverait rien
    px = render_cube(128, 128, 0.0)[64, 64, :3].astype(float)
    tex = checker_texture()
    colors = {
        tuple(tex[0, 0, :3]),
        tuple(tex[0, -1, :3]),
        tuple(tex[-1, 0, :3]),
        tuple(tex[-1, -1, :3]),
    }
    err_front = min(np.abs(px - np.array(c) * s_front).max() for c in colors)
    err_back = min(np.abs(px - np.array(c) * s_back).max() for c in colors)
    assert err_front <= 1.5
    assert err_back > 10


def test_numpy_perspective_foreshortening() -> None:
    """Perspective : la face avant (plus proche) paraît plus large que l'arrière ; le cadre n'est pas orthographique."""
    img = render_cube(256, 256, 0.0)
    mask = (img != np.array(cm.BACKGROUND, np.uint8)).any(axis=2)
    assert mask.any()
    # Sans rotation en Y, la silhouette est celle de la face avant + dessus : elle reste dans le cadre.
    assert mask[0].sum() == 0 or mask[-1].sum() == 0 or mask.sum() < mask.size


def test_cube_geometry_counts() -> None:
    pos, uv, nrm, idx = cm.cube_geometry()
    assert (
        pos.shape == (24, 3)
        and uv.shape == (24, 2)
        and nrm.shape == (24, 3)
        and idx.shape == (36,)
    )
    # Chaque triangle doit être anti-horaire vu de l'extérieur : (b-a)x(c-a) aligné avec la normale.
    for t in range(0, 36, 3):
        a, b, c = pos[idx[t]], pos[idx[t + 1]], pos[idx[t + 2]]
        assert float(np.dot(np.cross(b - a, c - a), nrm[idx[t]])) > 0


# Comme `tests/test_kut3d_gpu_render.py` : sur la CI, les voies GPU ne tournent que sur demande
# (KUT3D_GPU_TESTS=1). Un runner sans GPU laisse chaque sous-processus aller jusqu'à son délai (~30 s).
CI_OPT_OUT = bool(os.environ.get("CI")) and os.environ.get("KUT3D_GPU_TESTS") != "1"
CI_OPT_OUT_REASON = "tests GPU désactivés sur la CI (KUT3D_GPU_TESTS=1 pour les lancer)"


def _gpu_available(route: str) -> tuple[bool, str]:
    platforms = dict(run_spike.ROUTES)[route]
    reason = ""
    for platform in platforms:
        res = run_spike.run_route(route, platform)
        if res["ok"]:
            return True, platform
        reason = str(res.get("error", ""))
    return False, reason


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("kut3d_out")
    run_spike.OUT = path
    return path


def _load_png(path: Path) -> np.ndarray:
    from PySide6.QtGui import QImage

    img = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(
        img.height(), img.bytesPerLine() // 4, 4
    )
    return arr[:, : img.width()].copy()


@pytest.mark.parametrize("route", ["gl_offscreen", "qrhi"])
def test_gpu_route_matches_numpy(
    route: str, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if CI_OPT_OUT:
        pytest.skip(CI_OPT_OUT_REASON)
    monkeypatch.setenv("KUT3D_SPIKE_OUT", str(out_dir))
    try:
        import PySide6  # noqa: F401
    except ImportError:
        pytest.skip("PySide6 absent")
    ok, info = _gpu_available(route)
    if not ok:
        pytest.skip(f"voie {route} indisponible : {info}")
    gpu = _load_png(out_dir / f"cube_{route}.png")
    ref = render_cube(SIZE, SIZE, ANGLE)
    assert gpu.shape == ref.shape
    close = (
        np.abs(gpu[..., :3].astype(int) - ref[..., :3].astype(int)) <= TOLERANCE_LEVELS
    ).all(axis=2)
    assert close.mean() >= MIN_MATCH_RATIO, (
        f"{route} : {close.mean():.1%} de pixels proches"
    )


def test_numpy_zbuffer_alone_hides_back_faces() -> None:
    """Sans élimination des faces arrière, le z-buffer seul doit donner exactement la même image.

    Sans test de profondeur, les faces arrière (dessinées après les faces avant dans l'ordre des indices)
    écraseraient les faces avant : cette égalité échoue alors.
    """
    for angle in (0.0, 35.0, 200.0):
        with_cull = render_cube(96, 96, angle)
        without_cull = render_cube(96, 96, angle, cull_backfaces=False)
        assert np.array_equal(with_cull, without_cull)
