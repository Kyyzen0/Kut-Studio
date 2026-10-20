"""Texte 3D : rendu numpy de référence (silhouette, trous, profondeur, lumière, pose) et non-régression des autres formes."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Frame,
    Material,
    Scene,
    Sphere,
    Text3D,
    primitive_instance,
    render_frame,
)
from kut3d.text_font import glyph, metrics
from kut3d.triangulate import classify, signed_area
from tests.kut3d_primitive_scenes import front_camera

W, H = 192, 108
DISTANCE = 4.0
FOCAL = (H / 2) / math.tan(math.radians(20.0))  # fov_y 40 degrés
BACKGROUND = (0.2, 0.3, 0.5, 1.0)
BG_PIXEL = np.array([round(c * 255) for c in BACKGROUND[:3]])  # sRGB 8 bits
LIGHTS = [
    AmbientLight((1, 1, 1), 0.4),
    DirectionalLight((-0.3, -0.4, -1.0), (1, 1, 1), 2.5),
]
GOLD = Material(base_color=(0.9, 0.6, 0.1, 1.0), roughness=0.6)


def _scene(
    spec: Text3D,
    camera: Camera | None = None,
    background: object = None,
    **pose: object,
) -> Scene:
    """Fond transparent par défaut : l'alpha du rendu est alors la couverture exacte du texte."""
    inst = primitive_instance(spec, material=GOLD, **pose)
    return Scene(
        [inst], LIGHTS, camera or front_camera(DISTANCE), background=background
    )  # type: ignore[arg-type]


def _render(scene: Scene, size: tuple[int, int] = (W, H)) -> Frame:
    return render_frame(scene, 0.0, size=size, backend="numpy")


def _covered(frame: Frame) -> np.ndarray:
    return np.asarray(frame.alpha) > 0.5


def _background_pixels(frame: Frame) -> np.ndarray:
    """Vrai là où l'image vaut le fond, à ±1 près (arrondis sRGB)."""
    return np.all(np.abs(frame.color[..., :3].astype(int) - BG_PIXEL) <= 1, axis=-1)


def _expected_area(char: str, size: float, depth: float) -> float:
    """Aire en pixels de la face avant (z = depth/2, à DISTANCE - depth/2 de la caméra) d'après les contours de la police."""
    area_em = sum(
        signed_area(outer) + sum(signed_area(h) for h in holes)
        for outer, holes in classify(list(glyph("Anton", char).rings))
    )
    return area_em * (size * FOCAL / (DISTANCE - depth / 2)) ** 2


def test_the_background_is_untouched_and_the_text_has_the_analytic_silhouette() -> None:
    size, depth = 2.4, 0.04
    spec = Text3D("I", size=size, depth=depth)
    covered = _covered(_render(_scene(spec)))
    assert covered.sum() > 200
    painted = _render(_scene(spec, background=BACKGROUND))
    assert _background_pixels(painted)[
        ~covered
    ].all()  # tout ce qui n'est pas couvert vaut le fond, à ±1
    assert not _background_pixels(painted)[covered].all()
    assert np.all(np.abs(painted.color[0, 0, :3].astype(int) - BG_PIXEL) <= 1)
    expected = _expected_area("I", size, depth)
    assert float(covered.sum()) == pytest.approx(expected, rel=0.08)


def test_the_hole_of_an_o_shows_the_background_and_a_stroke_does_not() -> None:
    size, depth = 2.4, 0.04
    spec = Text3D("O", size=size, depth=depth)
    covered = _covered(_render(_scene(spec)))
    painted = _render(_scene(spec, background=BACKGROUND))
    cy, cx = H // 2, W // 2
    assert (
        not covered[cy, cx] and _background_pixels(painted)[cy, cx]
    )  # le trou : on voit le fond à travers
    stroke_x = int(
        cx + FOCAL * (size * 0.5 * glyph("Anton", "O").advance * 0.47) / DISTANCE
    )  # près du bord extérieur, dans le trait
    row = covered[cy]
    assert row[stroke_x] or row[stroke_x - 3] or row[stroke_x + 3]
    runs = int(
        np.count_nonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])) == 1)
    )
    assert runs == 2  # une traversée de l'anneau à gauche, une à droite
    assert float(covered.sum()) == pytest.approx(
        _expected_area("O", size, depth), rel=0.12
    )


def test_a_has_a_hole_in_its_triangle_and_b_has_two() -> None:
    for char, holes in (("A", 1), ("B", 2)):
        frame = _render(_scene(Text3D(char, size=2.6, depth=0.04)), size=(256, 256))
        covered = _covered(frame)
        free = ~covered
        # les composantes de fond qui ne touchent pas le bord de l'image sont les trous de la lettre
        from collections import deque

        seen = np.zeros_like(free)
        inner = 0
        for y0, x0 in zip(*np.nonzero(free), strict=True):
            if seen[y0, x0]:
                continue
            queue, touches = deque([(y0, x0)]), False
            seen[y0, x0] = True
            while queue:
                y, x = queue.popleft()
                touches |= y in (0, 255) or x in (0, 255)
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if (
                        0 <= ny < 256
                        and 0 <= nx < 256
                        and free[ny, nx]
                        and not seen[ny, nx]
                    ):
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            inner += 0 if touches else 1
        assert inner == holes, char


def test_the_nearest_surface_is_the_front_face_at_the_right_depth() -> None:
    size, depth = 2.4, 0.5
    frame = _render(_scene(Text3D("H", size=size, depth=depth)))
    covered = _covered(frame)
    ys, xs = np.nonzero(covered)
    d = np.asarray(frame.depth)[ys, xs]
    assert d.min() == pytest.approx(
        DISTANCE - depth / 2, rel=2e-3
    )  # face avant : z = +depth/2
    assert np.isinf(np.asarray(frame.depth)[~covered]).all()


def test_text_faces_the_camera_and_is_lit_like_a_front_face() -> None:
    frame = _render(_scene(Text3D("H", size=2.4, depth=0.3)))
    covered = _covered(frame)
    color = frame.color[covered][:, :3].astype(float)
    assert (
        color.max() > 120 and color[:, 0].mean() > color[:, 2].mean()
    )  # or éclairé : rouge > bleu, pas noir
    dark = _render(
        Scene(
            [primitive_instance(Text3D("H", size=2.4), material=GOLD)],
            [],
            front_camera(DISTANCE),
        )
    )
    assert (
        dark.color[_covered(dark)][:, :3].astype(float).mean() < color.mean()
    )  # sans lumière : plus sombre


def test_rotating_the_text_turns_it_and_a_bevel_catches_the_light() -> None:
    plain = _render(_scene(Text3D("H", size=2.0, depth=0.3), rotation_deg=(0, 50, 0)))
    bevel = _render(
        _scene(Text3D("H", size=2.0, depth=0.3, bevel=0.02), rotation_deg=(0, 50, 0))
    )
    for frame in (plain, bevel):
        assert _covered(frame).sum() > 150
    assert not np.array_equal(plain.color, bevel.color)  # le chanfrein change l'image
    thin = Text3D(
        "H", size=2.0, depth=0.02
    )  # fin : les parois ne comptent presque pas dans la largeur
    turned = np.nonzero(_covered(_render(_scene(thin, rotation_deg=(0, 50, 0)))))[1]
    flat = np.nonzero(_covered(_render(_scene(thin))))[1]
    assert (
        np.ptp(turned) < np.ptp(flat) * 0.8
    )  # tourné de 50 degrés : environ cos(50) = 0,64 de la largeur


def test_animated_clip_time_does_not_change_a_static_text() -> None:
    scene = _scene(Text3D("Kut", size=1.5))
    a = render_frame(scene, 0.0, size=(W, H), backend="numpy")
    b = render_frame(scene, 1.7, size=(W, H), backend="numpy")
    assert np.array_equal(a.color, b.color)


def test_text_is_centred_on_the_origin_at_every_alignment() -> None:
    cap = metrics("Anton").cap_height * 2.0
    mask = _covered(
        _render(
            _scene(Text3D("HH", size=2.0, depth=0.04, align="center")), size=(256, 144)
        )
    )
    ys, xs = np.nonzero(mask)
    assert (
        abs((ys.min() + ys.max()) / 2 - 72) < 4
        and abs((xs.min() + xs.max()) / 2 - 128) < 6
    )
    assert cap > 0
    left = _covered(
        _render(
            _scene(Text3D("HH", size=2.0, depth=0.04, align="left")), size=(256, 144)
        )
    )
    assert np.nonzero(left)[1].min() >= 126  # commence à x = 0 (centre de l'image)
    right = _covered(
        _render(
            _scene(Text3D("HH", size=2.0, depth=0.04, align="right")), size=(256, 144)
        )
    )
    assert np.nonzero(right)[1].max() <= 130


def test_existing_primitives_still_render_exactly_as_before() -> None:
    frame = _render(
        Scene(
            [primitive_instance(Sphere(0.6, 32, 16), material=GOLD)],
            LIGHTS,
            front_camera(DISTANCE),
        ),
        size=(96, 96),
    )
    radius_px = (
        (96 / 2) / math.tan(math.radians(20.0)) * math.tan(math.asin(0.6 / DISTANCE))
    )
    assert float(_covered(frame).sum()) == pytest.approx(
        math.pi * radius_px**2, rel=0.04
    )
