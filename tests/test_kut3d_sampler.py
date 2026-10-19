"""Sampler des textures : import glTF (wrap, filtres) et échantillonnage du moteur numpy hors de [0, 1]."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from kut3d import load_model, render_frame
from kut3d.model import Material, TextureRef
from kut3d.render.textures import sample_bilinear
from tests.kut3d_samples import write_gltf
from tests.kut3d_scenes import (
    front_camera,
    quad_mesh,
    model_of,
    scene,
    texture_ref,
)

# image 4 x 1 : rouge, vert, bleu, blanc (opaques), alpha 255
IMG = np.array(
    [[[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [255, 255, 255, 255]]],
    np.uint8,
)


def _px(u: float, sampler: tuple[str, str, str, str]) -> np.ndarray:
    out = sample_bilinear(IMG, np.array([[u, 0.5]]), False, sampler)[0]
    return np.round(out[:3] * 255).astype(int)


def _gltf_with_sampler(
    tmp_path: Path, sampler: dict[str, Any] | None, **tex: Any
) -> TextureRef:
    def mutate(doc: dict[str, Any]) -> None:
        if sampler is not None:
            doc["samplers"] = [sampler]
            doc["textures"][0]["sampler"] = 0
        doc["textures"][0].update(tex)

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    ref = model.materials[0].base_color_texture
    assert ref is not None
    return ref


@pytest.mark.parametrize(
    "code, mode", [(10497, "repeat"), (33071, "clamp"), (33648, "mirror")]
)
def test_gltf_wrap_modes(tmp_path: Path, code: int, mode: str) -> None:
    ref = _gltf_with_sampler(tmp_path, {"wrapS": code, "wrapT": 33071})
    assert (ref.wrap_s, ref.wrap_t) == (mode, "clamp")


@pytest.mark.parametrize(
    "mag, mn, expected",
    [
        (9728, 9728, ("nearest", "nearest")),
        (9729, 9729, ("linear", "linear")),
        (9729, 9987, ("linear", "linear")),
        (9728, 9984, ("nearest", "nearest")),
        (9729, 9985, ("linear", "linear")),
        (9728, 9986, ("nearest", "nearest")),
    ],
)
def test_gltf_filters(
    tmp_path: Path, mag: int, mn: int, expected: tuple[str, str]
) -> None:
    ref = _gltf_with_sampler(tmp_path, {"magFilter": mag, "minFilter": mn})
    assert (ref.mag_filter, ref.min_filter) == expected


def test_gltf_without_sampler_keeps_defaults(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path))
    ref = model.materials[0].base_color_texture
    assert ref is not None and ref.sampler == ("repeat", "repeat", "linear", "linear")
    assert not any("sampler" in w for w in model.warnings)


def test_gltf_unknown_sampler_values_warn(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["samplers"] = [{"wrapS": 1234, "magFilter": 1, "wrapT": 33071}]
        doc["textures"][0]["sampler"] = 0
        doc["textures"][1]["sampler"] = 9

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    ref = model.materials[0].base_color_texture
    assert ref is not None and ref.sampler == ("repeat", "clamp", "linear", "linear")
    text = "\n".join(model.warnings)
    assert "wrapS" in text and "magFilter" in text and "sampler inexistant" in text


def test_texture_ref_validates_modes() -> None:
    with pytest.raises(ValueError, match="wrap_s"):
        TextureRef("x", wrap_s="bad")
    with pytest.raises(ValueError, match="min_filter"):
        TextureRef("x", min_filter="trilinear")
    assert TextureRef("x").sampler == ("repeat", "repeat", "linear", "linear")


def test_numpy_wrap_modes_outside_unit_range() -> None:
    nearest = ("linear",)  # placeholder pour la lisibilité
    del nearest

    def n(mode: str) -> tuple[str, str, str, str]:
        return (mode, mode, "nearest", "nearest")

    # u = 1.125 : texel 4 (hors image). repeat -> 0 (rouge), clamp -> 3 (blanc), mirror -> 3 (blanc)
    assert list(_px(1.125, n("repeat"))) == [255, 0, 0]
    assert list(_px(1.125, n("clamp"))) == [255, 255, 255]
    assert list(_px(1.125, n("mirror"))) == [255, 255, 255]
    # u = 1.375 : texel 5. repeat -> 1 (vert), clamp -> 3, mirror -> 2 (bleu)
    assert list(_px(1.375, n("repeat"))) == [0, 255, 0]
    assert list(_px(1.375, n("clamp"))) == [255, 255, 255]
    assert list(_px(1.375, n("mirror"))) == [0, 0, 255]
    # u = -0.125 : texel -1. repeat -> 3, clamp -> 0, mirror -> 0
    assert list(_px(-0.125, n("repeat"))) == [255, 255, 255]
    assert list(_px(-0.125, n("clamp"))) == [255, 0, 0]
    assert list(_px(-0.125, n("mirror"))) == [255, 0, 0]


def test_numpy_nearest_vs_linear_between_texels() -> None:
    # centres de texels à 0.125 (rouge) et 0.375 (vert)
    near = ("repeat", "repeat", "nearest", "nearest")
    lin = ("repeat", "repeat", "linear", "linear")
    assert list(_px(0.3125, near)) == [0, 255, 0]
    assert list(_px(0.25, lin)) == [128, 128, 0]  # exactement entre les deux centres
    default = sample_bilinear(IMG, np.array([[0.3125, 0.5]]), False)
    assert np.array_equal(
        default, sample_bilinear(IMG, np.array([[0.3125, 0.5]]), False, lin)
    )


def test_numpy_clamp_linear_does_not_bleed_across_edge() -> None:
    clamp = ("clamp", "clamp", "linear", "linear")
    rep = ("repeat", "repeat", "linear", "linear")
    assert list(_px(1.0, clamp)) == [255, 255, 255]
    assert list(_px(1.0, rep)) == [
        255,
        128,
        128,
    ]  # mélange blanc / rouge de l'autre côté


def _expected_texel(idx: int, mode: str) -> int:
    if mode == "repeat":
        return idx % 4
    if mode == "clamp":
        return min(idx, 3)
    return 7 - idx if idx >= 4 else idx  # mirror, pour idx dans [0, 8[


def _classify(px: np.ndarray) -> int:
    r, g, b = (int(v) > 128 for v in px[:3])
    return {
        (True, False, False): 0,
        (False, True, False): 1,
        (False, False, True): 2,
    }.get((r, g, b), 3)


@pytest.mark.parametrize("mode", ["repeat", "clamp", "mirror"])
def test_numpy_render_plane_with_uv_outside_range(mode: str) -> None:
    mesh = quad_mesh(1.0, material_index=0)
    uv = np.array(mesh.uvs) * np.array([2.0, 0.5])  # u dans [0, 2], v dans [0, 0.5]
    mesh = replace(mesh, uvs=uv)
    mat = Material(
        base_color=(1, 1, 1, 1),
        roughness=1.0,
        base_color_texture=texture_ref(
            IMG, wrap_s=mode, wrap_t=mode, mag_filter="nearest", min_filter="nearest"
        ),
    )
    frame = render_frame(
        scene(model_of(mesh, mat), camera=front_camera(3.0)), 0.0, (128, 128)
    )
    row = frame.color[64]
    covered = np.flatnonzero(row[:, 3] == 255)
    x0, x1 = covered[0], covered[-1] + 1
    for idx in range(8):  # 8 cellules de u = 0.25 chacune
        col = int(x0 + (idx + 0.5) / 8 * (x1 - x0))
        assert _classify(row[col]) == _expected_texel(idx, mode), (mode, idx)
