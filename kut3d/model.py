"""Modèle de scène 3D immuable de Kut-3D.

Conventions : main droite, Y vers le haut ; matrices 4x4 « colonne » (translation dans `m[:3, 3]`) ;
quaternions (x, y, z, w) ; coordonnées de texture à la manière de glTF (origine en haut à gauche, V vers le bas).
Les dataclasses sont `frozen` et leurs tableaux numpy sont en lecture seule (`setflags(write=False)`).
Le modèle porte déjà tout ce que les phases suivantes utiliseront : UV, matériaux, squelette, poids de peau,
formes de visage (morph targets) et clips d'animation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from kut3d.mathutil import Array, trs_to_matrix

ALPHA_MODES = ("opaque", "mask", "blend")
CHANNEL_PATHS = ("translation", "rotation", "scale", "weights")
INTERPOLATIONS = ("linear", "step", "cubicspline")


def _freeze(
    value: Any, dtype: Any, name: str, shape: tuple[int | None, ...] | None = None
) -> Array:
    """Convertit en tableau numpy du bon type et de la bonne forme, en lecture seule."""
    arr = np.asarray(value, dtype=dtype)
    if shape is not None:
        ok = arr.ndim == len(shape) and all(
            s is None or s == d for s, d in zip(shape, arr.shape)
        )
        if not ok:
            raise ValueError(f"{name} : forme {arr.shape} invalide (attendu {shape}).")
    view = arr.view()
    view.setflags(write=False)
    return view


def _freeze_opt(
    value: Any, dtype: Any, name: str, shape: tuple[int | None, ...]
) -> Array | None:
    return None if value is None else _freeze(value, dtype, name, shape)


@dataclass(frozen=True, eq=False)
class MorphTarget:
    """Une forme (visage, correctif) : écarts par rapport aux sommets de base."""

    name: str
    position_deltas: Array
    normal_deltas: Array | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "position_deltas",
            _freeze(self.position_deltas, np.float32, "position_deltas", (None, 3)),
        )
        n = self.position_deltas.shape[0]
        object.__setattr__(
            self,
            "normal_deltas",
            _freeze_opt(self.normal_deltas, np.float32, "normal_deltas", (n, 3)),
        )


@dataclass(frozen=True, eq=False)
class Mesh:
    """Un maillage triangulé. `joints`/`weights` : 4 influences par sommet (indices dans `Skeleton`)."""

    positions: Array
    indices: Array
    normals: Array | None = None
    uvs: Array | None = None
    colors: Array | None = None
    material_index: int | None = None
    joints: Array | None = None
    weights: Array | None = None
    morph_targets: tuple[MorphTarget, ...] = ()
    morph_weights: tuple[float, ...] = ()
    name: str = ""

    def __post_init__(self) -> None:
        pos = _freeze(self.positions, np.float32, "positions", (None, 3))
        if not np.isfinite(pos).all():
            raise ValueError("positions : valeurs non finies (NaN ou infini).")
        n = pos.shape[0]
        idx = _freeze(self.indices, np.uint32, "indices", (None, 3))
        if idx.shape[0] and int(idx.max()) >= n:
            raise ValueError(
                f"indices : sommet {int(idx.max())} hors limites ({n} sommets)."
            )
        object.__setattr__(self, "positions", pos)
        object.__setattr__(self, "indices", idx)
        object.__setattr__(
            self, "normals", _freeze_opt(self.normals, np.float32, "normals", (n, 3))
        )
        object.__setattr__(
            self, "uvs", _freeze_opt(self.uvs, np.float32, "uvs", (n, 2))
        )
        object.__setattr__(
            self, "colors", _freeze_opt(self.colors, np.float32, "colors", (n, 4))
        )
        if (self.joints is None) != (self.weights is None):
            raise ValueError("joints et weights vont ensemble.")
        object.__setattr__(
            self, "joints", _freeze_opt(self.joints, np.uint16, "joints", (n, 4))
        )
        object.__setattr__(
            self, "weights", _freeze_opt(self.weights, np.float32, "weights", (n, 4))
        )
        if self.material_index is not None and self.material_index < 0:
            raise ValueError("material_index négatif.")
        targets = tuple(self.morph_targets)
        for t in targets:
            if t.position_deltas.shape[0] != n:
                raise ValueError(
                    f"forme {t.name!r} : {t.position_deltas.shape[0]} écarts pour {n} sommets."
                )
        object.__setattr__(self, "morph_targets", targets)
        object.__setattr__(
            self, "morph_weights", tuple(float(w) for w in self.morph_weights)
        )

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0])


@dataclass(frozen=True)
class TextureRef:
    """Référence d'image : un fichier (`path`) ou des octets embarqués (`data`). Jamais décodée en phase 1."""

    name: str
    path: Path | None = None
    data: bytes | None = None
    mime_type: str | None = None


@dataclass(frozen=True, eq=False)
class Material:
    """Matériau PBR métal / rugosité (OBJ+MTL y est ramené)."""

    name: str = ""
    base_color: Any = (1.0, 1.0, 1.0, 1.0)
    base_color_texture: TextureRef | None = None
    normal_texture: TextureRef | None = None
    metallic: float = 0.0
    roughness: float = 1.0
    metallic_roughness_texture: TextureRef | None = None
    emissive: Any = (0.0, 0.0, 0.0)
    emissive_texture: TextureRef | None = None
    occlusion_texture: TextureRef | None = None
    alpha_mode: str = "opaque"
    alpha_cutoff: float = 0.5
    double_sided: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "base_color", _freeze(self.base_color, np.float32, "base_color", (4,))
        )
        object.__setattr__(
            self, "emissive", _freeze(self.emissive, np.float32, "emissive", (3,))
        )
        if self.alpha_mode not in ALPHA_MODES:
            raise ValueError(f"alpha_mode inconnu : {self.alpha_mode!r}.")


@dataclass(frozen=True, eq=False)
class Skeleton:
    """Squelette (un `skin` glTF). `parents[j]` = -1 pour une racine ; l'ordre des os est celui du fichier (un
    parent ne précède pas forcément ses enfants). Pose de repos locale par os ; `node_indices` relie chaque os
    à son `Node`."""

    joint_names: tuple[str, ...]
    parents: Any
    inverse_bind_matrices: Any
    rest_translation: Any
    rest_rotation: Any
    rest_scale: Any
    node_indices: Any
    name: str = ""

    def __post_init__(self) -> None:
        j = len(self.joint_names)
        object.__setattr__(self, "joint_names", tuple(self.joint_names))
        object.__setattr__(
            self, "parents", _freeze(self.parents, np.int64, "parents", (j,))
        )
        object.__setattr__(
            self,
            "inverse_bind_matrices",
            _freeze(self.inverse_bind_matrices, np.float32, "ibm", (j, 4, 4)),
        )
        object.__setattr__(
            self,
            "rest_translation",
            _freeze(self.rest_translation, np.float32, "t", (j, 3)),
        )
        object.__setattr__(
            self, "rest_rotation", _freeze(self.rest_rotation, np.float32, "r", (j, 4))
        )
        object.__setattr__(
            self, "rest_scale", _freeze(self.rest_scale, np.float32, "s", (j, 3))
        )
        object.__setattr__(
            self,
            "node_indices",
            _freeze(self.node_indices, np.int64, "node_indices", (j,)),
        )
        if j and (self.parents.min() < -1 or self.parents.max() >= j):
            raise ValueError("parents : indice d'os hors limites.")

    @property
    def joint_count(self) -> int:
        return len(self.joint_names)


@dataclass(frozen=True)
class Node:
    """Nœud de la hiérarchie : transformation locale (T, R xyzw, S) et contenu éventuel."""

    name: str = ""
    mesh_index: int | None = None
    skin_index: int | None = None
    children: tuple[int, ...] = ()
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    weights: tuple[float, ...] | None = (
        None  # poids de formes qui remplacent ceux du maillage
    )

    def local_matrix(self) -> Array:
        return trs_to_matrix(self.translation, self.rotation, self.scale)


@dataclass(frozen=True, eq=False)
class AnimationChannel:
    """Une piste : `values` (K, C), ou (K, 3, C) en `cubicspline` (tangente d'entrée, valeur, tangente de sortie).
    C = 3 (translation, scale), 4 (rotation xyzw) ou le nombre de formes (weights)."""

    target_node: int
    path: str
    times: Array
    values: Array
    interpolation: str = "linear"

    def __post_init__(self) -> None:
        if self.path not in CHANNEL_PATHS:
            raise ValueError(f"chemin d'animation inconnu : {self.path!r}.")
        if self.interpolation not in INTERPOLATIONS:
            raise ValueError(f"interpolation inconnue : {self.interpolation!r}.")
        times = _freeze(self.times, np.float32, "times", (None,))
        values = _freeze(self.values, np.float32, "values")
        if values.ndim not in (2, 3) or values.shape[0] != times.shape[0]:
            raise ValueError(
                f"values {values.shape} incompatible avec {times.shape[0]} instants."
            )
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class AnimationClip:
    name: str
    duration: float
    channels: tuple[AnimationChannel, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "channels", tuple(self.channels))


def _check_range(index: int | None, size: int, what: str) -> None:
    if index is not None and not 0 <= index < size:
        raise ValueError(f"{what} : indice {index} hors limites ({size}).")


@dataclass(frozen=True, eq=False)
class Model:
    """Un modèle importé. Les sommets ne sont jamais modifiés par la normalisation : `root_transform`
    (4x4, appliquée par-dessus la hiérarchie) la porte, pour ne pas casser squelette et animations."""

    nodes: tuple[Node, ...] = ()
    root_nodes: tuple[int, ...] = ()
    meshes: tuple[Mesh, ...] = ()
    materials: tuple[Material, ...] = ()
    skeletons: tuple[Skeleton, ...] = ()
    animations: tuple[AnimationClip, ...] = ()
    source: Path = field(default_factory=Path)
    root_transform: Any = field(default_factory=lambda: np.eye(4))
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "nodes",
            "root_nodes",
            "meshes",
            "materials",
            "skeletons",
            "animations",
            "warnings",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "root_transform",
            _freeze(self.root_transform, np.float64, "root_transform", (4, 4)),
        )
        self._validate()

    def _validate(self) -> None:
        n = len(self.nodes)
        parent = [-1] * n
        for i, node in enumerate(self.nodes):
            _check_range(node.mesh_index, len(self.meshes), f"nœud {i} : maillage")
            _check_range(node.skin_index, len(self.skeletons), f"nœud {i} : squelette")
            for c in node.children:
                _check_range(c, n, f"nœud {i} : enfant")
                if parent[c] != -1:
                    raise ValueError(f"nœud {c} : deux parents ({parent[c]} et {i}).")
                parent[c] = i
        for r in self.root_nodes:
            _check_range(r, n, "racine")
            if parent[r] != -1:
                raise ValueError(f"nœud {r} : racine qui a un parent.")
        # Cycles : on remonte la chaîne des ancêtres (parent, grand-parent...) de chaque nœud, en temps linéaire
        # (état 1 = sur la chaîne en cours, 2 = chaîne déjà vérifiée).
        state = [0] * n
        for i in range(n):
            path: list[int] = []
            cur = i
            while cur != -1 and state[cur] == 0:
                state[cur] = 1
                path.append(cur)
                cur = parent[cur]
            if cur != -1 and state[cur] == 1:
                raise ValueError(f"nœud {cur} : cycle dans la hiérarchie.")
            for p in path:
                state[p] = 2
        for m in self.meshes:
            _check_range(m.material_index, len(self.materials), "maillage : matériau")
        for sk in self.skeletons:
            if sk.joint_count and (
                sk.node_indices.min() < 0 or sk.node_indices.max() >= n
            ):
                raise ValueError("squelette : nœud d'os hors limites.")
        for clip in self.animations:
            for ch in clip.channels:
                _check_range(ch.target_node, n, f"animation {clip.name!r} : nœud cible")

    @property
    def triangle_count(self) -> int:
        return sum(m.triangle_count for m in self.meshes)

    @property
    def vertex_count(self) -> int:
        return sum(m.vertex_count for m in self.meshes)

    def with_root_transform(self, matrix: Any) -> Model:
        return replace(self, root_transform=matrix)

    def with_warnings(self, *more: str) -> Model:
        return replace(self, warnings=self.warnings + tuple(more))

    def world_matrices(self) -> dict[int, Array]:
        """Matrice monde (au repos, sans `root_transform`) de chaque nœud atteint depuis les racines.
        Parcours itératif : aucune limite de profondeur de pile."""
        out: dict[int, Array] = {}
        stack = [(r, np.eye(4)) for r in reversed(self.root_nodes)]
        while stack:
            i, parent_m = stack.pop()
            if i in out:
                continue
            m = parent_m @ self.nodes[i].local_matrix()
            out[i] = m
            stack.extend((c, m) for c in reversed(self.nodes[i].children))
        return out

    def bounds(self) -> tuple[Array, Array]:
        """Boîte englobante (min, max), float64 (3,), de tous les maillages au repos, avec la hiérarchie des
        nœuds puis `root_transform`. Un maillage peau est pris en pose de liaison (transformation de nœud
        ignorée, comme le veut glTF). Modèle vide : deux vecteurs nuls."""
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for i, world in self.world_matrices().items():
            node = self.nodes[i]
            if node.mesh_index is None:
                continue
            mesh = self.meshes[node.mesh_index]
            if mesh.vertex_count == 0:
                continue
            m = self.root_transform @ (
                np.eye(4) if node.skin_index is not None else world
            )
            p = mesh.positions.astype(np.float64) @ m[:3, :3].T + m[:3, 3]
            lo = np.minimum(lo, p.min(axis=0))
            hi = np.maximum(hi, p.max(axis=0))
        if not np.isfinite(lo).all():
            return np.zeros(3), np.zeros(3)
        return lo, hi
