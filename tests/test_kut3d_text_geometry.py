"""Texte 3D : maillage fermé, normales sortantes, trous de « O » / « A » / « B », mise en page, déterminisme.

Les polices livrées (`assets/fonts`) sont lues dans leurs fichiers : les contours ne dépendent d'aucune police système, donc ces
tests donnent le même résultat partout (CI Windows `offscreen` sans police comprise). Rien ici ne dépend d'une police installée.
"""

from __future__ import annotations

import hashlib
import os
import string
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from kut3d import Text3D
from kut3d.primitive_mesh import primitive_arrays
from kut3d.text_font import BUNDLED_FAMILIES, glyph, metrics
from kut3d.triangulate import _contains
from tests.kut3d_text_helpers import (
    degenerate_triangles,
    euler_characteristic,
    is_closed,
    is_manifold,
    volume,
)

ROOT = Path(__file__).resolve().parent.parent


def _mesh(
    text: str, **kw: object
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return primitive_arrays(Text3D(text, **kw))  # type: ignore[arg-type]


@pytest.mark.parametrize("bevel", [0.0, 0.01])
@pytest.mark.parametrize("text", ["O", "A", "B", "8", "Kut 3D", "&@e"])
def test_the_mesh_is_closed_and_coherent(text: str, bevel: float) -> None:
    pos, _n, _uv, tri = _mesh(text, bevel=bevel)
    assert is_manifold(pos, tri)  # Anton : aucun contour ne touche un autre
    assert degenerate_triangles(pos, tri) == 0


@pytest.mark.parametrize(
    ("text", "euler"),
    [("I", 2), ("O", 0), ("A", 0), ("B", -2), ("8", -2)],  # 2 - 2 x (nombre de trous)
)
def test_the_holes_of_the_letters_are_really_open(text: str, euler: int) -> None:
    for bevel in (0.0, 0.01):
        pos, _n, _uv, tri = _mesh(text, bevel=bevel)
        assert euler_characteristic(pos, tri) == euler


def test_the_holes_are_not_filled_by_the_front_face() -> None:
    size, depth = 2.0, 0.4
    pos, _n, _uv, tri = _mesh("O", size=size, depth=depth)
    front = pos[tri][:, :, 2].min(axis=1) > depth / 2 - 1e-6
    centroids = pos[tri][front].mean(axis=1)[:, :2] / size
    rings = glyph("Anton", "O").rings
    outer, hole = sorted(rings, key=lambda r: -abs(np.ptp(r[:, 0])))
    shift = np.array(
        [-glyph("Anton", "O").advance / 2, -metrics("Anton").cap_height / 2]
    )  # mise en page d'un seul caractère centré
    inside_hole = sum(_contains(hole + shift, x, y) for x, y in centroids)
    assert front.sum() > 20 and inside_hole == 0
    assert sum(_contains(outer + shift, x, y) for x, y in centroids) == front.sum()


@pytest.mark.parametrize("bevel", [0.0, 0.01])
def test_the_normals_point_out_of_the_matter(bevel: float) -> None:
    pos, nrm, _uv, tri = _mesh("OAB", bevel=bevel)
    p = pos[tri].astype(np.float64)
    geometric = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    stored = nrm[tri].astype(np.float64).mean(axis=1)
    assert np.all(np.einsum("ij,ij->i", geometric, stored) > 0)
    assert volume(pos, tri) > 0  # sens direct partout : volume positif
    assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5)
    # faces avant et arrière : exactement +Z et -Z
    z = pos[:, 2].max()
    assert np.all(nrm[np.isclose(pos[:, 2], z)][:, 2] >= 0)


def test_volume_is_the_front_area_times_the_depth_without_bevel() -> None:
    size, depth = 1.5, 0.3
    pos, _n, _uv, tri = _mesh("OAB", size=size, depth=depth)
    front = np.isclose(pos[tri][:, :, 2], depth / 2).all(axis=1)
    p = pos[tri][front].astype(np.float64)
    area = float(
        np.sum(
            0.5
            * (
                (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])
            )
        )
    )
    assert volume(pos, tri) == pytest.approx(area * depth, rel=1e-4)


def test_a_bevel_removes_volume_and_keeps_the_extent() -> None:
    plain = _mesh("A", size=2.0, depth=0.5)
    cut = _mesh("A", size=2.0, depth=0.5, bevel=0.02)
    assert volume(*cut[::3]) < volume(plain[0], plain[3])
    assert np.allclose(
        plain[0].min(axis=0), cut[0].min(axis=0), atol=1e-6
    )  # le contour d'origine ne bouge pas
    assert np.allclose(plain[0].max(axis=0), cut[0].max(axis=0), atol=1e-6)


def test_extrusion_is_centred_and_sized() -> None:
    pos = _mesh("H", size=3.0, depth=0.8)[0]
    assert pos[:, 2].min() == pytest.approx(-0.4) and pos[:, 2].max() == pytest.approx(
        0.4
    )
    cap = metrics("Anton").cap_height * 3.0
    assert pos[:, 1].max() == pytest.approx(cap / 2, rel=1e-3) and pos[
        :, 1
    ].min() == pytest.approx(-cap / 2, rel=1e-3)
    double = _mesh("H", size=6.0, depth=0.8)[0]
    assert double[:, 1].max() == pytest.approx(2 * pos[:, 1].max(), rel=1e-5)


def test_alignment_places_the_text_around_x_zero() -> None:
    left, center, right = (
        _mesh("HH", align=a)[0][:, 0] for a in ("left", "center", "right")
    )
    assert left.min() >= -1e-6 and left.max() > 0.2
    assert right.max() <= 0.2 and right.min() < -0.2 and right.max() > -0.1
    assert abs((center.min() + center.max()) / 2) < 0.05
    assert left.max() - left.min() == pytest.approx(right.max() - right.min(), abs=1e-5)


def test_several_lines_stack_downwards_and_stay_centred() -> None:
    one = _mesh("H")[0]
    two = _mesh("H\nH")[0]
    pitch = metrics("Anton").pitch
    assert two[:, 1].max() - two[:, 1].min() == pytest.approx(
        one[:, 1].max() - one[:, 1].min() + pitch, rel=1e-4
    )
    assert two[:, 1].max() == pytest.approx(-two[:, 1].min(), rel=1e-4)


def test_spaces_advance_without_geometry() -> None:
    tight = _mesh("AB")[0]
    wide = _mesh("A B")[0]
    assert (
        wide[:, 0].max() - wide[:, 0].min()
        > tight[:, 0].max() - tight[:, 0].min() + 0.1
    )
    assert len(_mesh("A B")[3]) == len(
        _mesh("AB")[3]
    )  # l'espace n'ajoute aucun triangle
    assert len(_mesh("A B")[3]) == len(_mesh("AB")[3])


@pytest.mark.parametrize("family", BUNDLED_FAMILIES)
def test_every_bundled_family_builds_a_closed_mesh_for_every_common_character(
    family: str,
) -> None:
    # polices dérivées de polices variables : des contours se recouvrent (« e », « Q », « & »...), fusionnés par Qt
    for bevel in (
        (0.0, 0.005) if family == "Anton" else (0.0,)
    ):  # biseau : voir la limite documentée des polices fines
        pos, tri = _sweep(family, bevel)
        assert is_closed(pos, tri)


def _sweep(family: str, bevel: float) -> tuple[np.ndarray, np.ndarray]:
    parts = [
        _mesh(c, font=family, bevel=bevel)
        for c in string.ascii_letters + string.digits + "éèçÀÖñ&@$?!%œÅ§€"
    ]
    offset = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
    pos = np.vstack(
        [
            p[0] + np.array([i * 3.0, 0, 0], dtype=np.float32)
            for i, p in enumerate(parts)
        ]
    )  # écartés : pas de soudure entre lettres
    tri = np.vstack(
        [p[3].astype(np.int64) + o for p, o in zip(parts, offset, strict=True)]
    )
    return pos, tri


def test_the_a_has_exactly_the_triangles_of_its_straight_outline() -> None:
    # « A » = 8 points + 4 points (contours à segments droits, points alignés retirés) : le compte ne dépend d'aucun
    # aplatissement de courbe. Faces : 2 x (12 + 2 x 1 trou - 2 x 1 contour) = 24 ; parois : 2 x 12 = 24 ; au total 48
    assert len(glyph("Anton", "A").rings) == 2
    assert sum(len(r) for r in glyph("Anton", "A").rings) == 12
    assert len(_mesh("A")[3]) == 48


def test_triangle_counts_stay_under_the_precomputed_bound() -> None:
    for text, bevel in (
        ("Kut 3D", 0.0),
        ("Kut 3D", 0.01),
        ("@" * 40, 0.01),
        ("W" * 40, 0.0),
    ):
        spec = Text3D(text, bevel=bevel)
        assert len(primitive_arrays(spec)[3]) <= spec.triangle_count <= 65_536


def test_uv_cover_the_unit_square() -> None:
    uv = _mesh("Kut")[2]
    assert uv.min() >= 0.0 and uv.max() <= 1.0
    assert uv[:, 0].min() == pytest.approx(0.0, abs=1e-6) and uv[
        :, 1
    ].max() == pytest.approx(1.0, abs=1e-6)


def test_arrays_are_float32_uint32_and_read_only() -> None:
    pos, nrm, uv, tri = _mesh("A")
    assert (pos.dtype, nrm.dtype, uv.dtype, tri.dtype) == (
        np.float32,
        np.float32,
        np.float32,
        np.uint32,
    )
    assert tri.max() < len(pos) and not pos.flags.writeable


def test_the_mesh_is_deterministic_in_process_and_across_processes(
    tmp_path: Path,
) -> None:
    from kut3d.text_mesh import text_arrays

    spec = Text3D("Déjà vu 42", bevel=0.01)
    first = primitive_arrays(spec)
    text_arrays.cache_clear()
    glyph.cache_clear()
    again = primitive_arrays(spec)
    assert all(np.array_equal(a, b) for a, b in zip(first, again, strict=True))
    code = (
        "import hashlib,sys\n"
        "from kut3d import Text3D\n"
        "from kut3d.primitive_mesh import primitive_arrays\n"
        "h=hashlib.sha256()\n"
        "[h.update(a.tobytes()) for a in primitive_arrays(Text3D('Déjà vu 42', bevel=0.01))]\n"
        "print(h.hexdigest())\n"
    )
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "KUT_STUDIO_CONFIG_DIR": str(tmp_path / "cfg"),
        "KUT_STUDIO_CACHE_DIR": str(tmp_path / "cache"),
        "KUT_STUDIO_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(ROOT),
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert out.returncode == 0, out.stderr[
        -400:
    ]  # sans application Qt au départ : le module en crée une
    digest = hashlib.sha256()
    for array in first:
        digest.update(array.tobytes())
    assert out.stdout.strip() == digest.hexdigest()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="le plugin natif n'avorte que sous Linux sans affichage",
)
def test_a_headless_linux_process_without_display_builds_a_text(tmp_path: Path) -> None:
    code = "from kut3d import Text3D\nfrom kut3d.primitive_mesh import primitive_arrays\nprint(len(primitive_arrays(Text3D('A'))[3]))\n"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("QT_QPA_PLATFORM", "DISPLAY", "WAYLAND_DISPLAY")
    }
    env.update(
        PYTHONPATH=str(ROOT),
        KUT_STUDIO_CONFIG_DIR=str(tmp_path / "c"),
        KUT_STUDIO_CACHE_DIR=str(tmp_path / "k"),
        KUT_STUDIO_DATA_DIR=str(tmp_path / "d"),
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert out.returncode == 0 and out.stdout.strip() == "48", out.stderr[-400:]


def test_headless_linux_detection_never_overrides_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kut3d.gpu import qtapp

    for var in ("QT_QPA_PLATFORM", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert qtapp._headless_linux()
    monkeypatch.setenv("DISPLAY", ":0")
    assert not qtapp._headless_linux()
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert not qtapp._headless_linux()
    monkeypatch.delenv("QT_QPA_PLATFORM")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not qtapp._headless_linux()
