"""API de rendu : registre de moteurs, bornes de la demande, séquences PNG atomiques."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

import kut3d.render.frame as frame_mod
from kut3d import (
    Frame,
    RenderError,
    Scene,
    available_backends,
    register_backend,
    render_frame,
    render_sequence,
)
from kut3d.model import AnimationClip, Model, Node
from kut3d.render import numpy_backend
from kut3d.render.limits import MAX_IMAGE_SIDE, MAX_SAMPLED_PIXELS, MAX_SEQUENCE_FRAMES
from kut3d.render.png import decode_png
from tests.kut3d_scenes import channel, flat, front_camera, quad_model, scene

RED = (1.0, 0.0, 0.0, 1.0)


def simple_scene() -> Scene:
    return scene(quad_model(flat(RED), half=0.5), camera=front_camera(z=3.0))


# --- registre de moteurs ---------------------------------------------------------------------------------------------


def test_numpy_backend_is_registered_and_unknown_backend_lists_available_ones() -> None:
    assert "numpy" in available_backends() and "qrhi" in available_backends()
    with pytest.raises(RenderError) as err:
        render_frame(simple_scene(), size=(8, 8), backend="vulkan")  # jamais enregistré
    assert (
        "vulkan" in str(err.value)
        and "numpy" in str(err.value)
        and "qrhi" in str(err.value)
        and "disponibles" in str(err.value)
    )
    with pytest.raises(RenderError) as err:
        render_frame(simple_scene(), size=(8, 8), backend="x" * 10_000)
    assert len(str(err.value)) < 300  # le message ne recopie pas une entrée démesurée


def test_a_new_backend_plugs_in_without_touching_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frame_mod, "_BACKENDS", dict(frame_mod._BACKENDS))
    calls = []

    def fake(scene_: Scene, t: float, size: tuple[int, int], samples: int) -> Frame:
        calls.append((t, size, samples))
        w, h = size
        return Frame(
            np.zeros((h, w, 4), np.uint8),
            np.zeros((h, w), np.float32),
            np.full((h, w), np.inf, np.float32),
            size,
        )

    register_backend("faux", fake)
    assert "faux" in available_backends()
    out = render_frame(simple_scene(), 1.5, (6, 4), backend="faux", samples=2)
    assert out.size == (6, 4) and calls == [(1.5, (6, 4), 2)]
    with pytest.raises(ValueError):
        register_backend("", fake)
    with pytest.raises(ValueError):
        register_backend("x", "pas appelable")  # type: ignore[arg-type]


# --- bornes ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size, samples, message",
    [
        ((MAX_IMAGE_SIDE + 1, 10), 1, "hors limites"),
        ((10, 0), 1, "hors limites"),
        ((-3, 10), 1, "hors limites"),
        ((10.5, 10), 1, "entiers"),
        ((10,), 1, "entiers"),
        (10, 1, "entiers"),
        ((10, 10), 3, "samples"),
        ((10, 10), 0, "samples"),
        ((10, 10), True, "samples"),
        ((4096, 4096), 2, "sur-échantillonnés"),
        ((2048, 2048), 4, "sur-échantillonnés"),
    ],
)
def test_requests_out_of_bounds_are_refused_before_allocating(
    size, samples, message
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RenderError, match=message) as err:
        render_frame(simple_scene(), size=size, samples=samples)
    assert len(str(err.value)) < 300


def test_largest_allowed_sampled_budget_matches_the_documented_constant() -> None:
    assert MAX_SAMPLED_PIXELS == 1 << 24
    from kut3d.render.frame import check_request

    assert check_request((4096, 4096), 1, 0.0)[0] == (4096, 4096)
    assert check_request((1024, 1024), 4, 0.0)[1] == 4
    with pytest.raises(RenderError, match="fini"):
        check_request((8, 8), 1, float("nan"))
    with pytest.raises(RenderError, match="nombre"):
        check_request((8, 8), 1, "x")


def test_too_many_triangles_is_refused_before_building_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("kut3d.render.assemble.MAX_TRIANGLES", 3)
    with pytest.raises(RenderError, match="trop lourde") as err:
        render_frame(
            scene([quad_model(flat(RED)), quad_model(flat(RED))]), size=(8, 8)
        )  # 2 x 2 triangles
    assert len(str(err.value)) < 300
    render_frame(scene([quad_model(flat(RED))]), size=(8, 8))  # 2 triangles : accepté


def test_too_many_blend_triangles_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(numpy_backend, "MAX_BLEND_TRIANGLES", 1)
    glass = quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"))
    with pytest.raises(RenderError, match="alpha-blending"):
        render_frame(scene(glass), size=(8, 8))


def test_invalid_scene_argument_and_empty_scene() -> None:
    with pytest.raises(ValueError, match="Scene"):
        render_frame("pas une scène", size=(8, 8))  # type: ignore[arg-type]
    empty = render_frame(Scene([], [], front_camera()), size=(5, 4))
    assert (
        (empty.alpha == 0).all()
        and np.isinf(empty.depth).all()
        and (empty.color == 0).all()
    )


# --- séquences -------------------------------------------------------------------------------------------------------


def test_sequence_writes_numbered_readable_png_files(tmp_path: Path) -> None:
    clip = AnimationClip(
        "slide", 1.0, (channel("translation", [0, 1], [[-0.5, 0, 0], [0.5, 0, 0]]),)
    )
    base = quad_model(flat(RED), half=0.2)
    model = Model(
        nodes=(Node(mesh_index=0),),
        root_nodes=(0,),
        meshes=base.meshes,
        materials=base.materials,
        animations=(clip,),
    )
    from kut3d import Instance

    sc = scene(Instance(model, clip="slide"), camera=front_camera(z=2.0))
    paths = render_sequence(
        sc, fps=4, duration=1.0, size=(24, 16), out_dir=tmp_path / "out"
    )
    assert [p.name for p in paths] == [f"frame_{i:06d}.png" for i in range(1, 5)]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        p.name for p in paths
    ]  # aucun reste temporaire
    first = decode_png(paths[0].read_bytes())
    assert first.shape == (16, 24, 4)
    assert first[..., 3].max() == 255
    assert first.tobytes() == render_frame(sc, 0.0, (24, 16)).color.tobytes()
    last = decode_png(paths[-1].read_bytes())
    assert (
        last.tobytes() == render_frame(sc, 0.75, (24, 16)).color.tobytes()
    )  # t = i / fps : les clips avancent
    cols = [
        np.nonzero(decode_png(p.read_bytes())[..., 3].any(axis=0))[0].mean()
        for p in paths
    ]
    assert cols == sorted(cols) and cols[0] < cols[-1]


def test_sequence_accepts_a_function_of_time(tmp_path: Path) -> None:
    seen: list[float] = []

    def scene_at(t: float) -> Scene:
        seen.append(t)
        return simple_scene()

    paths = render_sequence(
        scene_at, fps=2, duration=1.5, size=(8, 8), out_dir=tmp_path
    )
    assert len(paths) == 3 and seen == [0.0, 0.5, 1.0]
    one = render_sequence(
        simple_scene(), fps=1, duration=0.2, size=(8, 8), out_dir=tmp_path / "b"
    )
    assert len(one) == 1  # au moins une image


def test_sequence_bounds_and_arguments(tmp_path: Path) -> None:
    target = tmp_path / "never"
    for kw, message in (
        ({"fps": 0}, "fps"),
        ({"fps": 241}, "fps"),
        ({"fps": float("nan")}, "fps"),
        ({"duration": 0}, "duration"),
        ({"duration": -1}, "duration"),
        ({"duration": MAX_SEQUENCE_FRAMES}, "trop d'images"),
        ({"size": (MAX_IMAGE_SIDE + 1, 8)}, "hors limites"),
        ({"samples": 3}, "samples"),
    ):
        args = {"fps": 10, "duration": 1.0, "size": (8, 8), "out_dir": target}
        args.update(kw)
        with pytest.raises(RenderError, match=message):
            render_sequence(simple_scene(), **args)
    assert not target.exists()  # rien n'est créé avant la validation
    with pytest.raises(RenderError, match="Scene"):
        render_sequence(
            lambda t: "pas une scène", fps=1, duration=1, size=(8, 8), out_dir=target
        )  # type: ignore[arg-type, return-value]


def test_interrupted_sequence_leaves_no_partial_file(tmp_path: Path) -> None:
    def scene_at(t: float) -> Scene:
        if t >= 0.5:
            raise KeyboardInterrupt
        return simple_scene()

    with pytest.raises(KeyboardInterrupt):
        render_sequence(scene_at, fps=4, duration=1.0, size=(8, 8), out_dir=tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "frame_000001.png",
        "frame_000002.png",
    ]  # les images terminées restent, rien d'autre
    assert all(
        decode_png(p.read_bytes()).shape == (8, 8, 4) for p in tmp_path.iterdir()
    )


def test_failed_write_leaves_neither_final_nor_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    state = {"calls": 0}

    def failing(src: str, dst: str) -> None:
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError("disque plein")
        real_replace(src, dst)

    monkeypatch.setattr("kut3d.render.sequence.os.replace", failing)
    with pytest.raises(OSError, match="disque plein"):
        render_sequence(
            simple_scene(), fps=3, duration=1.0, size=(8, 8), out_dir=tmp_path
        )
    assert [p.name for p in tmp_path.iterdir()] == [
        "frame_000001.png"
    ]  # ni frame_000002.png ni .tmp


def test_interrupted_encoding_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: bytes, __: int = 6) -> bytes:
        raise KeyboardInterrupt

    monkeypatch.setattr("kut3d.render.png.zlib.compress", boom)
    with pytest.raises(KeyboardInterrupt):
        render_sequence(
            simple_scene(), fps=1, duration=2.0, size=(8, 8), out_dir=tmp_path
        )
    assert list(tmp_path.iterdir()) == []
