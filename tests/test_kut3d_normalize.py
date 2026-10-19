"""Normalisation d'un modèle qui porte déjà un `root_transform` : bornes du modèle tel qu'il apparaît."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from kut3d.mathutil import rotation_x, scale_matrix, translation_matrix
from kut3d.model import Mesh, Model, Node
from kut3d.normalize import normalize_model


def _box() -> Model:
    # boîte 2 x 1 x 0.5, centrée à l'origine
    pos = [[x, y, z] for x in (-1, 1) for y in (-0.5, 0.5) for z in (-0.25, 0.25)]
    mesh = Mesh(positions=pos, indices=[[0, 1, 2]])
    return Model(nodes=(Node(mesh_index=0),), root_nodes=(0,), meshes=(mesh,))


_EXISTING = {
    "identity": np.eye(4),
    "scale3": scale_matrix(3.0),
    "rot90": rotation_x(90.0),
    "translate": translation_matrix([10.0, -4.0, 7.0]),
    "mixed": translation_matrix([5.0, 1.0, -2.0])
    @ rotation_x(90.0)
    @ scale_matrix(2.0),
}


@pytest.mark.parametrize("kind", list(_EXISTING))
def test_center_and_target_size_use_existing_root_transform(kind: str) -> None:
    model = replace(_box(), root_transform=_EXISTING[kind])
    out = normalize_model(model, target_size=2.0, center=True)
    lo, hi = out.bounds()
    assert np.allclose((lo + hi) / 2, 0.0, atol=1e-9)
    assert float((hi - lo).max()) == pytest.approx(2.0)


@pytest.mark.parametrize("kind", list(_EXISTING))
def test_center_only_and_size_only(kind: str) -> None:
    model = replace(_box(), root_transform=_EXISTING[kind])
    lo0, hi0 = model.bounds()
    lo, hi = normalize_model(model, center=True).bounds()
    assert np.allclose((lo + hi) / 2, 0.0, atol=1e-9)
    assert np.allclose(hi - lo, hi0 - lo0)
    lo, hi = normalize_model(model, target_size=4.0, center=False).bounds()
    assert float((hi - lo).max()) == pytest.approx(4.0)
    k = 4.0 / float((hi0 - lo0).max())
    assert np.allclose(lo, lo0 * k)


def test_identity_unchanged() -> None:
    out = normalize_model(_box(), target_size=1.0)
    assert np.allclose(out.root_transform, scale_matrix(0.5))
