"""Formes de base : validation STRICTE à la construction (valeurs hostiles, bornes, plafond de triangles avant génération)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from kut3d import (
    Cone,
    Cube,
    Cylinder,
    Plane,
    Sphere,
    Torus,
    primitive_instance,
    primitive_model,
)
from kut3d import primitives as prim
from kut3d.primitive_mesh import primitive_arrays

BAD_NUMBERS: list[Any] = [
    math.nan,
    math.inf,
    -math.inf,
    0.0,
    -1.0,
    0,
    -5,
    prim.MIN_DIMENSION / 2,
    prim.MAX_DIMENSION * 2,
    1e300,
    10**400,
    True,
    False,
    "1",
    None,
    [1.0],
    (1.0,),
    {"a": 1},
    1 + 2j,
    np.float64("nan"),
    np.float32("inf"),
    b"1",
    object(),
]
BAD_COUNTS: list[Any] = [
    0,
    1,
    2,
    -3,
    257,
    10**6,
    2**70,
    8.0,
    8.5,
    math.nan,
    math.inf,
    True,
    False,
    "8",
    None,
    [8],
    np.float64(8.0),
    10**400,
]
FLOAT_FIELDS = [
    (Cube, "width"),
    (Cube, "height"),
    (Cube, "depth"),
    (Sphere, "radius"),
    (Plane, "width"),
    (Plane, "depth"),
    (Cylinder, "radius"),
    (Cylinder, "height"),
    (Cone, "radius"),
    (Cone, "height"),
    (Torus, "major_radius"),
    (Torus, "minor_radius"),
]
INT_FIELDS = [
    (Sphere, "segments"),
    (Sphere, "rings"),
    (Plane, "width_segments"),
    (Plane, "depth_segments"),
    (Cylinder, "segments"),
    (Cone, "segments"),
    (Torus, "segments"),
    (Torus, "tube_segments"),
]
BOOL_FIELDS = [(Cylinder, "caps"), (Cone, "cap")]


def _ids(items: list[tuple[type, str]]) -> list[str]:
    return [f"{c.__name__}.{f}" for c, f in items]


@pytest.mark.parametrize(("cls", "field"), FLOAT_FIELDS, ids=_ids(FLOAT_FIELDS))
def test_every_dimension_refuses_every_hostile_value(cls: type, field: str) -> None:
    for bad in BAD_NUMBERS:
        if (
            cls is Torus
            and field == "minor_radius"
            and isinstance(bad, float)
            and bad >= prim.MIN_DIMENSION
        ):
            continue
        with pytest.raises(ValueError, match=field):
            cls(**{field: bad})


@pytest.mark.parametrize(("cls", "field"), INT_FIELDS, ids=_ids(INT_FIELDS))
def test_every_tessellation_count_refuses_every_hostile_value(
    cls: type, field: str
) -> None:
    low = {"rings": prim.MIN_RINGS}.get(field, 1 if cls is Plane else prim.MIN_SEGMENTS)
    for bad in BAD_COUNTS:
        if isinstance(bad, int) and not isinstance(bad, bool) and low <= bad <= 256:
            continue
        with pytest.raises(ValueError, match=field):
            cls(**{field: bad})


@pytest.mark.parametrize(("cls", "field"), BOOL_FIELDS, ids=_ids(BOOL_FIELDS))
def test_flags_refuse_everything_but_a_boolean(cls: type, field: str) -> None:
    for bad in (1, 0, "true", None, 1.0, [True], np.bool_(True)):
        with pytest.raises(ValueError, match=field):
            cls(**{field: bad})
    assert getattr(cls(**{field: False}), field) is False


def test_the_boundaries_are_inclusive() -> None:
    assert Cube(prim.MIN_DIMENSION, prim.MAX_DIMENSION, 1).width == prim.MIN_DIMENSION
    assert Cube(prim.MIN_DIMENSION, prim.MAX_DIMENSION, 1).height == prim.MAX_DIMENSION
    assert (
        Sphere(1, 3, 2).segments == 3
        and Sphere(1, 256, 2).segments == 256
        and Sphere(1, 3, 256).rings == 256
    )
    assert (
        Plane(1, 1, 1, 1).triangle_count == 2
        and Plane(1, 1, 181, 181).width_segments == 181
    )
    assert Torus(1, 0.5, 3, 3).tube_segments == 3


def test_integers_and_numpy_numbers_are_normalised_to_plain_python() -> None:
    sphere = Sphere(np.float32(2.0), np.int64(8), np.int32(4))
    assert (
        type(sphere.radius) is float
        and type(sphere.segments) is int
        and type(sphere.rings) is int
    )
    assert Cube(2, 3, 4).params() == {
        "width": 2.0,
        "height": 3.0,
        "depth": 4.0,
    }  # un entier devient un flottant : même fichier


def test_a_torus_whose_tube_is_not_thinner_than_its_ring_is_refused() -> None:
    for minor in (1.0, 1.5):
        with pytest.raises(ValueError, match="minor_radius"):
            Torus(1.0, minor)
    Torus(1.0, 0.999)


def test_the_triangle_cap_is_applied_from_the_formula_before_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = []
    monkeypatch.setattr(
        "kut3d.primitive_models.primitive_arrays", lambda spec: called.append(spec)
    )
    for build in (
        lambda: Plane(1, 1, 256, 256),
        lambda: Sphere(1, 256, 256),
        lambda: Torus(1, 0.5, 256, 256),
    ):
        with pytest.raises(ValueError, match="triangles"):
            build()
    assert called == []  # aucune génération n'a eu lieu : la formule suffit à refuser
    assert Sphere(1, 256, 128).triangle_count <= prim.MAX_PRIMITIVE_TRIANGLES
    assert Plane(1, 1, 181, 181).triangle_count <= prim.MAX_PRIMITIVE_TRIANGLES


def test_the_cap_refuses_exactly_one_triangle_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = 12
    monkeypatch.setattr(prim, "MAX_PRIMITIVE_TRIANGLES", 2 * n * n)
    Plane(1, 1, n, n)
    with pytest.raises(ValueError, match="triangles"):
        Plane(1, 1, n + 1, n)


def test_error_messages_do_not_copy_a_huge_input() -> None:
    huge = "x" * 5_000_000
    with pytest.raises(ValueError) as info:
        Cube(huge)  # type: ignore[arg-type]
    assert len(str(info.value)) < 200
    with pytest.raises(ValueError) as info:
        Sphere(1, 10**100000)  # type: ignore[arg-type]
    assert len(str(info.value)) < 200


def test_a_description_is_immutable() -> None:
    cube = Cube()
    with pytest.raises(Exception):
        cube.width = 5.0  # type: ignore[misc]
    assert cube == Cube(1, 1, 1) and hash(cube) == hash(Cube(1.0, 1.0, 1.0))


def test_generation_refuses_what_is_not_a_known_shape() -> None:
    with pytest.raises(ValueError):
        primitive_arrays("sphere")  # type: ignore[arg-type]


def test_primitive_instance_front_door_validates_too() -> None:
    primitive_instance("sphere", radius=1.0)
    for call in (
        lambda: primitive_instance("pyramide"),
        lambda: primitive_instance("sphere", radius=-1.0),
        lambda: primitive_instance("sphere", rayon=1.0),
        lambda: primitive_instance(Sphere(), radius=1.0),
        lambda: primitive_instance("cube", position=(0, 0)),
        lambda: primitive_instance("cube", position=(0, 0, math.nan)),
        lambda: primitive_instance("cube", scale=0.0),
        lambda: primitive_instance("cube", scale=(1, -1, 1)),
        lambda: primitive_instance("cube", scale=True),
        lambda: primitive_instance("cube", rotation_deg=(0, math.inf, 0)),
        lambda: primitive_instance(42),  # type: ignore[arg-type]
    ):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        primitive_model(Cube(), object())  # type: ignore[arg-type]
