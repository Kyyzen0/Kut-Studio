"""Formes de base paramétriques : géométrie générée (comptes exacts, normales, orientation, étanchéité, UV)."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from kut3d import Cone, Cube, Cylinder, Material, Plane, Sphere, Torus, primitive_model
from kut3d.primitive_mesh import primitive_arrays
from kut3d.primitives import PrimitiveSpec

CASES: list[PrimitiveSpec] = [
    Cube(2.0, 3.0, 4.0),
    Sphere(1.5, 12, 7),
    Sphere(1.0, 3, 2),
    Plane(2.0, 3.0, 4, 5),
    Cylinder(1.0, 2.0, 9),
    Cylinder(1.0, 2.0, 9, caps=False),
    Cone(1.0, 2.0, 9),
    Cone(1.0, 2.0, 9, cap=False),
    Torus(1.0, 0.3, 11, 7),
]
IDS = [f"{c.kind}-{i}" for i, c in enumerate(CASES)]
# Sommets / triangles attendus, écrits ici à la main (indépendants des formules de la production).
EXPECTED = {
    "cube": (24, 12),
    ("sphere", 12, 7): (6 * 13 + 2 * 12, 12 * (2 * 7 - 2)),
    ("sphere", 3, 2): (1 * 4 + 2 * 3, 3 * 2),
    ("plane", 4, 5): (5 * 6, 2 * 4 * 5),
    ("cylinder", 9, True): (2 * 10 + 2 * 11, 9 * 4),
    ("cylinder", 9, False): (2 * 10, 9 * 2),
    ("cone", 9, True): (10 + 9 + 11, 9 * 2),
    ("cone", 9, False): (10 + 9, 9),
    "torus": (12 * 8, 2 * 11 * 7),
}


def _key(spec: PrimitiveSpec) -> object:
    if isinstance(spec, Cube):
        return "cube"
    if isinstance(spec, Torus):
        return "torus"
    if isinstance(spec, Sphere):
        return ("sphere", spec.segments, spec.rings)
    if isinstance(spec, Plane):
        return ("plane", spec.width_segments, spec.depth_segments)
    if isinstance(spec, Cylinder):
        return ("cylinder", spec.segments, spec.caps)
    assert isinstance(spec, Cone)
    return ("cone", spec.segments, spec.cap)


def _weld(pos: np.ndarray) -> np.ndarray:
    """Indice de sommet soudé : les sommets au même point (couture d'UV, pôles) reçoivent le même numéro."""
    _, inverse = np.unique(
        np.round(pos.astype(np.float64) / 1e-5).astype(np.int64),
        axis=0,
        return_inverse=True,
    )
    return inverse.reshape(-1)


def _edge_counts(spec: PrimitiveSpec) -> Counter[tuple[int, int]]:
    pos, _, _, tri = primitive_arrays(spec)
    w = _weld(pos)[tri]
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in w:
        edges.update([(a, b), (b, c), (c, a)])
    return edges


@pytest.mark.parametrize("spec", CASES, ids=IDS)
def test_counts_are_exact_and_match_the_formulas(spec: PrimitiveSpec) -> None:
    pos, nrm, uv, tri = primitive_arrays(spec)
    vertices, triangles = EXPECTED[_key(spec)]
    assert (len(pos), len(tri)) == (vertices, triangles)
    assert (spec.vertex_count, spec.triangle_count) == (vertices, triangles)
    assert pos.shape == nrm.shape == (vertices, 3) and uv.shape == (vertices, 2)
    assert pos.dtype == nrm.dtype == uv.dtype == np.float32 and tri.dtype == np.uint32
    assert int(tri.max()) == vertices - 1  # aucun sommet orphelin en bout de tableau
    assert len(np.unique(tri)) == vertices  # ni au milieu : chaque sommet sert


@pytest.mark.parametrize("spec", CASES, ids=IDS)
def test_normals_are_unit_and_geometry_is_finite(spec: PrimitiveSpec) -> None:
    pos, nrm, uv, _ = primitive_arrays(spec)
    assert np.isfinite(pos).all() and np.isfinite(nrm).all() and np.isfinite(uv).all()
    assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-6)


@pytest.mark.parametrize("spec", CASES, ids=IDS)
def test_no_degenerate_triangle(spec: PrimitiveSpec) -> None:
    pos, _, _, tri = primitive_arrays(spec)
    p = pos.astype(np.float64)
    area = (
        np.linalg.norm(
            np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1
        )
        / 2.0
    )
    assert area.min() > 1e-4, area.min()
    welded = _weld(pos)[tri]
    assert (
        (welded[:, 0] != welded[:, 1]).all()
        and (welded[:, 1] != welded[:, 2]).all()
        and (welded[:, 0] != welded[:, 2]).all()
    )


@pytest.mark.parametrize("spec", CASES, ids=IDS)
def test_triangles_agree_with_the_vertex_normals(spec: PrimitiveSpec) -> None:
    """Le sens des triangles (sens direct vu de l'extérieur) est celui des normales de sommet, partout."""
    pos, nrm, _, tri = primitive_arrays(spec)
    p = pos.astype(np.float64)
    face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    face /= np.linalg.norm(face, axis=1, keepdims=True)
    assert (np.einsum("ij,ij->i", face, nrm[tri].mean(axis=1)) > 0.2).all()


def test_outward_orientation_on_each_convex_shape() -> None:
    for spec in (Cube(2, 3, 4), Sphere(1.5, 12, 7), Cylinder(1, 2, 9), Cone(1, 2, 9)):
        pos, _, _, tri = primitive_arrays(spec)
        p = pos.astype(np.float64)
        face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
        centroid = p[tri].mean(axis=1)
        assert (np.einsum("ij,ij->i", face, centroid) > 0).all(), (
            spec.kind
        )  # l'origine est à l'intérieur


def test_torus_triangles_point_away_from_the_tube_centre() -> None:
    spec = Torus(1.0, 0.3, 11, 7)
    pos, _, _, tri = primitive_arrays(spec)
    p = pos.astype(np.float64)
    face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    c = p[tri].mean(axis=1)
    ring = np.stack([c[:, 0], np.zeros(len(c)), c[:, 2]], axis=1)
    ring /= np.linalg.norm(ring, axis=1, keepdims=True)
    assert (np.einsum("ij,ij->i", face, c - ring * spec.major_radius) > 0).all()


@pytest.mark.parametrize(
    "spec",
    [
        Cube(2, 3, 4),
        Sphere(1.5, 12, 7),
        Cylinder(1, 2, 9),
        Cone(1, 2, 9),
        Torus(1, 0.3, 11, 7),
    ],
)
def test_closed_shapes_are_watertight_and_consistently_oriented(
    spec: PrimitiveSpec,
) -> None:
    edges = _edge_counts(spec)
    assert set(edges.values()) == {1}  # chaque arête orientée est utilisée une fois...
    assert all(
        (b, a) in edges for a, b in edges
    )  # ...et son inverse l'est aussi : variété orientée fermée


@pytest.mark.parametrize(
    "spec", [Cylinder(1, 2, 9, caps=False), Cone(1, 2, 9, cap=False), Plane(2, 3, 4, 5)]
)
def test_open_shapes_have_a_boundary(spec: PrimitiveSpec) -> None:
    edges = _edge_counts(spec)
    boundary = [e for e in edges if (e[1], e[0]) not in edges]
    assert len(boundary) > 0
    assert set(edges.values()) == {1}
    if isinstance(spec, Plane):
        assert len(boundary) == 2 * (4 + 5)


def test_extents_match_the_dimensions() -> None:
    def extent(spec: PrimitiveSpec) -> tuple[np.ndarray, np.ndarray]:
        pos = primitive_arrays(spec)[0].astype(np.float64)
        return pos.min(axis=0), pos.max(axis=0)

    lo, hi = extent(Cube(2.0, 3.0, 4.0))
    assert np.allclose(lo, (-1, -1.5, -2)) and np.allclose(hi, (1, 1.5, 2))
    lo, hi = extent(Sphere(1.5, 16, 8))
    assert np.allclose(lo, (-1.5, -1.5, -1.5), atol=1e-6) and np.allclose(
        hi, (1.5, 1.5, 1.5), atol=1e-6
    )
    lo, hi = extent(Plane(2.0, 3.0, 3, 3))
    assert np.allclose(lo, (-1, 0, -1.5)) and np.allclose(hi, (1, 0, 1.5))
    lo, hi = extent(Cylinder(0.5, 2.0, 16))
    assert np.allclose(lo, (-0.5, -1, -0.5), atol=1e-6) and np.allclose(
        hi, (0.5, 1, 0.5), atol=1e-6
    )
    lo, hi = extent(Cone(0.5, 2.0, 16))
    assert np.allclose(lo, (-0.5, -1, -0.5), atol=1e-6) and np.allclose(
        hi, (0.5, 1, 0.5), atol=1e-6
    )
    lo, hi = extent(Torus(1.0, 0.25, 16, 8))
    assert np.allclose(lo, (-1.25, -0.25, -1.25), atol=1e-6) and np.allclose(
        hi, (1.25, 0.25, 1.25), atol=1e-6
    )


def test_analytic_normals() -> None:
    pos, nrm, _, _ = (
        a.astype(np.float64) for a in primitive_arrays(Sphere(1.5, 12, 7))
    )
    assert np.allclose(nrm, pos / 1.5, atol=1e-6)
    pos, nrm, _, _ = (
        a.astype(np.float64) for a in primitive_arrays(Torus(1.0, 0.3, 11, 7))
    )
    ring = np.stack([pos[:, 0], np.zeros(len(pos)), pos[:, 2]], axis=1)
    ring = ring / np.linalg.norm(ring, axis=1, keepdims=True)
    assert np.allclose(nrm, (pos - ring) / 0.3, atol=1e-5)
    pos, nrm, _, _ = (a.astype(np.float64) for a in primitive_arrays(Cube(2, 3, 4)))
    assert (
        np.allclose(np.abs(nrm).max(axis=1), 1.0)
        and (np.abs(nrm) > 1e-9).sum(axis=1).max() == 1
    )  # normales plates
    _, nrm, _, _ = (a.astype(np.float64) for a in primitive_arrays(Plane(2, 3, 4, 5)))
    assert np.allclose(nrm, (0, 1, 0))


def test_cylinder_and_cone_normals() -> None:
    spec = Cylinder(1.0, 2.0, 9)
    pos, nrm, _, _ = (a.astype(np.float64) for a in primitive_arrays(spec))
    side, caps = slice(0, 20), slice(20, None)
    assert np.allclose(nrm[side, 1], 0) and np.allclose(
        nrm[side, [0, 2]], pos[side][:, [0, 2]], atol=1e-6
    )
    assert set(np.round(nrm[caps, 1], 6)) == {1.0, -1.0}  # bouchons plats
    assert (
        pos[caps][np.isclose(nrm[caps, 1], 1.0)][:, 1] == 1.0
    ).all()  # bouchon du haut en y = +h/2
    spec = Cone(1.0, 2.0, 9)
    pos, nrm, _, _ = (a.astype(np.float64) for a in primitive_arrays(spec))
    for k in range(
        9
    ):  # la normale d'un segment est perpendiculaire à sa génératrice (base -> sommet)
        base, apex = pos[k], pos[10 + k]
        assert abs(float(np.dot(nrm[k], apex - base))) < 1e-5
        assert nrm[k, 1] > 0 and nrm[10 + k, 1] > 0  # inclinée vers le haut
    assert set(np.round(nrm[19:, 1], 6)) == {-1.0}  # bouchon de la base vers le bas


def test_uv_are_in_range_and_unfold_the_surface() -> None:
    for spec in CASES:
        uv = primitive_arrays(spec)[2]
        assert uv.min() >= 0.0 and uv.max() <= 1.0, spec.kind
    pos, _, uv, _ = primitive_arrays(Sphere(1.0, 8, 4))
    # couture : la première et la dernière colonne de chaque anneau sont au même point, avec u = 0 et u = 1
    ring = 8 + np.arange(3) * 9  # anneaux 1 à 3 (les pôles ont un sommet par segment)
    assert np.allclose(pos[ring], pos[ring + 8], atol=1e-6)
    assert np.allclose(uv[ring, 0], 0.0) and np.allclose(uv[ring + 8, 0], 1.0)
    assert np.allclose(uv[:8, 1], 0.0) and np.allclose(
        uv[-8:, 1], 1.0
    )  # V vers le bas : pôle nord en haut de l'image
    assert np.allclose(
        uv[:8, 0], (np.arange(8) + 0.5) / 8
    )  # le sommet de pôle est au milieu de son segment
    pos, _, uv, _ = (a.astype(np.float64) for a in primitive_arrays(Sphere(1.5, 12, 7)))
    phi = (
        np.pi * uv[:, 1]
    )  # v = angle depuis le pôle nord / pi : croît vers le bas, sur tous les anneaux
    assert np.allclose(pos[:, 1], 1.5 * np.cos(phi), atol=1e-5)
    middle = slice(
        12, 12 + 6 * 13
    )  # anneaux intérieurs : u = angle autour de l'axe / 2 pi
    theta = 2 * np.pi * uv[middle, 0]
    assert np.allclose(
        pos[middle, 0], 1.5 * np.sin(phi[middle]) * np.cos(theta), atol=1e-5
    )
    assert np.allclose(
        pos[middle, 2], 1.5 * np.sin(phi[middle]) * np.sin(theta), atol=1e-5
    )
    _, _, uv, _ = primitive_arrays(Plane(2.0, 3.0, 4, 5))
    assert np.allclose(uv[:5, 0], np.arange(5) / 4) and np.allclose(uv[::5, 1][:1], 0.0)


def test_each_cube_face_covers_the_whole_texture_once() -> None:
    _, _, uv, _ = primitive_arrays(Cube(2, 3, 4))
    for face in range(6):
        corners = {tuple(map(float, c)) for c in uv[4 * face : 4 * face + 4]}
        assert corners == {(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)}


def test_generation_is_deterministic() -> None:
    for spec in CASES:
        a = primitive_arrays(spec)
        b = primitive_arrays(type(spec)(**spec.params()))
        assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_the_model_is_frozen_and_carries_its_description() -> None:
    material = Material(base_color=(1, 0, 0, 1))
    model = primitive_model(Sphere(1.0, 8, 4), material)
    assert model.primitive == Sphere(1.0, 8, 4) and model.source.as_posix() == "."
    assert model.triangle_count == Sphere(
        1.0, 8, 4
    ).triangle_count and model.materials == (material,)
    mesh = model.meshes[0]
    assert mesh.material_index == 0 and mesh.name == "sphere"
    assert not mesh.positions.flags.writeable and not mesh.indices.flags.writeable
    assert primitive_model(Cube()).meshes[0].material_index is None
    with pytest.raises(ValueError):
        primitive_model("cube")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        primitive_model(Cube(), "rouge")  # type: ignore[arg-type]


def test_the_maximum_tessellation_generates_within_the_cap() -> None:
    spec = Sphere(1.0, 256, 128)
    pos, _, _, tri = primitive_arrays(spec)
    assert (
        len(tri) == spec.triangle_count == 256 * 254 and len(pos) == 127 * 257 + 2 * 256
    )
    spec = Torus(1.0, 0.5, 256, 128)
    assert len(primitive_arrays(spec)[3]) == 2 * 256 * 128
