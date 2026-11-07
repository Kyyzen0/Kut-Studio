"""Propriété fondamentale du document : tout état atteignable par commandes est enregistrable (ADR-0010).

Soit la commande est refusée (`DocumentError`) et RIEN ne change, soit elle réussit et `save()` réussit, et la relecture donne les
mêmes octets. Les valeurs viennent d'une graine fixe et mêlent valeurs limites, invalides, non finies et démesurées."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import kut3d
from kut3d import (
    AmbientLight,
    Camera,
    Cube,
    DirectionalLight,
    DocumentError,
    Material,
    PointLight,
    SceneDocument,
    Sphere,
    pose_matrix,
)
from kut3d import document_commands as cmd

NAN, INF = float("nan"), float("inf")
NUMBERS = [
    0.0,
    1.0,
    -1.0,
    0.5,
    2.0,
    16.0,
    16.0001,
    1e6,
    1e12,
    1e-12,
    -1e-12,
    NAN,
    INF,
    -INF,
    1.0000001,
    -0.0000001,
]
VALID = [0.0, 0.25, 0.5, 1.0, 2.0, 16.0]
BAD_NAMES = ["", "a", "é" * 200, "x" * 201, "\x00\t\n", "\ud800", 12, None]


def _n(rng: random.Random) -> float:
    return rng.choice(VALID) if rng.random() < 0.75 else rng.choice(NUMBERS)


def _material(rng: random.Random) -> Material | None:
    pick = lambda: _n(rng)  # noqa: E731
    try:
        return Material(
            base_color=(pick(), pick(), pick(), pick()),
            metallic=pick(),
            roughness=pick(),
            emissive=(pick(), pick(), pick()),
            alpha_cutoff=pick(),
            alpha_mode=rng.choice(["opaque", "mask", "blend", "autre"]),
            name=rng.choice(BAD_NAMES) if rng.random() < 0.3 else "",  # type: ignore[arg-type]
        )
    except (ValueError, TypeError):
        return None  # le constructeur lui-même refuse : rien n'entre dans le document


def _matrix(rng: random.Random) -> Any:
    kind = rng.randrange(4)
    if kind == 0:
        return np.full((4, 4), _n(rng))
    if kind == 1:
        with np.errstate(invalid="ignore"):
            return np.eye(4) * _n(rng)
    m = np.eye(4)
    m[:3, 3] = [_n(rng) for _ in range(3)]
    return m


def _spec(rng: random.Random) -> Any:
    try:
        return rng.choice(
            [
                lambda: Sphere(
                    _n(rng),
                    rng.choice([2, 3, 16, 256, 257, 1.5]),
                    rng.choice([1, 2, 8, 256]),
                ),
                lambda: Cube(_n(rng), _n(rng), 1.0),
                lambda: kut3d.Plane(
                    1.0,
                    1.0,
                    rng.choice([1, 20, 181, 256]),
                    rng.choice([1, 20, 181, 256]),
                ),
                lambda: kut3d.Torus(_n(rng), _n(rng), 16, 8),
            ]
        )()
    except (ValueError, TypeError):
        return None


def _light(rng: random.Random) -> Any:
    pick = lambda: _n(rng)  # noqa: E731
    try:
        return rng.choice(
            [
                lambda: AmbientLight((pick(), pick(), pick()), pick()),
                lambda: DirectionalLight(
                    (pick(), pick(), pick()), (pick(), pick(), pick()), pick()
                ),
                lambda: PointLight(
                    (pick(), pick(), pick()),
                    (pick(), pick(), pick()),
                    pick(),
                    rng.choice([None, *NUMBERS]),
                ),
            ]
        )()
    except (ValueError, TypeError):
        return None


def _camera(rng: random.Random) -> Any:
    pick = lambda: _n(rng)  # noqa: E731
    try:
        return Camera(
            position=(pick(), pick(), 5.0),
            target=(0, 0, 0),
            up=rng.choice([(0, 1, 0), (0, 0, 0), (pick(), 1, 0)]),
            fov_y_deg=rng.choice([*NUMBERS, 40.0, 170.0, 40.0, 60.0]),
            near=rng.choice([*NUMBERS, 0.1, 0.1]),
            far=rng.choice([*NUMBERS, 100.0, 100.0]),
        )
    except (ValueError, TypeError):
        return None


def _command(rng: random.Random, doc: SceneDocument) -> cmd.Command | None:
    objects = [o.id for o in doc.state.objects] + ["o999"]
    lights = [e.id for e in doc.state.lights] + ["l999"]
    kind = rng.randrange(13)
    if kind == 0:
        spec = _spec(rng)
        return (
            None
            if spec is None
            else cmd.AddPrimitive(
                spec,
                material=_material(rng),
                transform=rng.choice([None, _matrix(rng)]),
                name=rng.choice([None, *BAD_NAMES]),
            )
        )
    if kind == 1:
        return cmd.SetTransform(rng.choice(objects), _matrix(rng))
    if kind == 2:
        return cmd.SetMaterial(rng.choice(objects), _material(rng))
    if kind == 3:
        return cmd.Rename(rng.choice(objects), rng.choice(BAD_NAMES))
    if kind == 4:
        spec = _spec(rng)
        return None if spec is None else cmd.SetPrimitive(rng.choice(objects), spec)
    if kind == 5:
        light = _light(rng)
        return None if light is None else cmd.AddLight(light)
    if kind == 6:
        light = _light(rng)
        return None if light is None else cmd.SetLight(rng.choice(lights), light)
    if kind == 7:
        camera = _camera(rng)
        return None if camera is None else cmd.SetCamera(camera)
    if kind == 8:
        return cmd.SetBackground(
            rng.choice([None, (0, 0, 0, 1), (_n(rng),) * 4, (0, 0, 0), "noir"])
        )
    if kind == 9:
        return cmd.SetAnimation(
            rng.choice(objects),
            clip=rng.choice([None, "x", 0, -1, True]),
            time_offset=_n(rng),
            loop=rng.choice([True, False, 1]),
        )
    if kind == 10:
        return cmd.DuplicateObject(rng.choice(objects))
    if kind == 11:
        return (
            cmd.RemoveObject(rng.choice(objects))
            if rng.random() < 0.3
            else cmd.RemoveLight(rng.choice(lights))
        )
    inner = [_command(rng, doc) for _ in range(rng.randrange(1, 4))]
    return cmd.Batch("lot", [c for c in inner if c is not None])


def _snapshot(doc: SceneDocument) -> tuple[Any, ...]:
    s = doc.state
    return (
        doc.revision,
        doc.history(),
        s.objects,
        s.lights,
        s.camera,
        s.background,
        s.next_id,
    )


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_every_reachable_state_is_savable_and_reloads_identically(
    seed: int, tmp_path: Path
) -> None:
    rng = random.Random(seed)
    doc = SceneDocument()
    accepted = refused = 0
    for step in range(250):
        command = _command(rng, doc)
        if command is None:
            continue
        before = _snapshot(doc)
        try:
            doc.execute(command)
        except DocumentError:
            refused += 1
            assert _snapshot(doc) == before  # un refus ne change rien
            continue
        except (
            TypeError
        ) as exc:  # une commande mal formée (type absurde) ne doit pas non plus entrer
            pytest.fail(
                f"exception non maîtrisée à l'étape {step} : {exc!r} ({command!r})"
            )
        accepted += 1
        if step % 10 == 0:
            _assert_savable(
                doc, tmp_path / f"a{seed}.kut3d", tmp_path / f"b{seed}.kut3d"
            )
    assert accepted > 20 and refused > 20  # le tirage exerce bien les deux issues
    _assert_savable(doc, tmp_path / f"a{seed}.kut3d", tmp_path / f"b{seed}.kut3d")


def _assert_savable(doc: SceneDocument, first: Path, second: Path) -> None:
    doc.save(first, overwrite=True)  # ne lève aucune erreur de VALEUR
    again = SceneDocument.load(first)
    again.save(second, overwrite=True)
    assert second.read_bytes() == first.read_bytes()
    assert len(again.state.objects) == len(doc.state.objects) and len(
        again.state.lights
    ) == len(doc.state.lights)


# --- les cas signalés en revue, un par un -------------------------------------------------------------


@pytest.mark.parametrize(
    "material",
    [
        Material(base_color=(2, 0, 0, 1)),
        Material(base_color=(-0.1, 0, 0, 1)),
        Material(metallic=NAN),
        Material(roughness=1.5),
        Material(alpha_cutoff=-1.0),
        Material(emissive=(17, 0, 0)),
        Material(emissive=(NAN, 0, 0)),
        Material(name="n" * 201),
    ],
)
def test_a_material_the_file_reader_would_refuse_is_refused_at_edit_time(
    material: Material,
) -> None:
    doc = SceneDocument()
    oid = doc.execute(cmd.AddPrimitive(Cube()))[0]
    before = _snapshot(doc)
    for command in (
        cmd.AddPrimitive(Sphere(0.5), material=material),
        cmd.SetMaterial(oid, material),
        cmd.Batch("lot", [cmd.SetMaterial(oid, material)]),
    ):
        with pytest.raises(DocumentError):
            doc.execute(command)
    assert _snapshot(doc) == before


def test_limit_values_that_the_reader_accepts_are_accepted_and_saved(
    tmp_path: Path,
) -> None:
    doc = SceneDocument()
    edge = Material(
        base_color=(0, 1, 0, 1),
        metallic=1.0,
        roughness=0.0,
        emissive=(16, 16, 16),
        alpha_cutoff=1.0,
    )
    doc.execute(cmd.AddPrimitive(Cube(), material=edge))
    doc.save(tmp_path / "s.kut3d")
    assert SceneDocument.load(tmp_path / "s.kut3d").state.objects[0].model.materials[
        0
    ].emissive.tolist() == [16, 16, 16]


def test_other_serialised_fields_are_refused_too() -> None:
    doc = SceneDocument()
    oid = doc.execute(cmd.AddPrimitive(Cube()))[0]
    for command in (
        cmd.SetTransform(oid, np.full((4, 4), NAN)),
        cmd.SetBackground((0, 0, 0, 1.5)),
        cmd.Rename(oid, "x" * 201),
        cmd.SetAnimation(oid, time_offset=INF),
    ):
        with pytest.raises(DocumentError):
            doc.execute(command)
    assert (
        math.isfinite(doc.state.find_object(oid).transform[0, 3])
        and pose_matrix() is not None
    )
