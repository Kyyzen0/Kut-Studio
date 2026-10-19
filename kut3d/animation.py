"""Évaluation d'une pose animée à l'instant `t` (phase 2a d'ADR-0009).

Un `AnimationClip` glTF est échantillonné canal par canal (translation, rotation, échelle, poids de formes) selon
sa spec : LINEAR (slerp pour les quaternions), STEP, CUBICSPLINE (interpolation d'Hermite des tangentes du
fichier). Hors des bornes, le temps est ramené au début ou à la fin de la clip (`loop=True` : modulo la durée).
Un canal sans image clé garde la valeur de repos du nœud. Il n'y a pas de mélange de clips (phase 4).

Résultat : un `Pose` immuable qui donne les matrices monde des nœuds (espace du modèle, SANS `root_transform`),
les matrices d'articulation (monde de l'os x inverse de la pose de liaison) et les poids de formes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from kut3d.errors import short
from kut3d.mathutil import Array, normalize_quaternion, trs_to_matrix
from kut3d.model import AnimationChannel, AnimationClip, Mesh, Model


def _freeze(a: Array) -> Array:
    a.setflags(write=False)
    return a


@dataclass(frozen=True, eq=False)
class Pose:
    """Pose d'un modèle à un instant donné (espace du modèle, avant `root_transform`)."""

    model: Model
    translation: Array  # (N, 3) local
    rotation: Array  # (N, 4) local, xyzw
    scale: Array  # (N, 3) local
    world: Array  # (N, 4, 4) ; identité pour un nœud non atteint depuis les racines
    reached: Array  # (N,) bool : nœud atteint depuis `root_nodes`
    node_weights: dict[
        int, Array
    ]  # poids de formes remplacés par un nœud ou par la clip

    def joint_matrices(self, skin_index: int) -> Array:
        """Matrices d'articulation (J, 4, 4) float64 : `monde(os) @ inverse_bind(os)`."""
        sk = self.model.skeletons[skin_index]
        if sk.joint_count == 0:
            return np.zeros((0, 4, 4), dtype=np.float64)
        world = self.world[np.asarray(sk.node_indices)]
        ibm = np.asarray(sk.inverse_bind_matrices, dtype=np.float64)
        return _freeze(np.matmul(world, ibm))

    def morph_weights(self, node_index: int, mesh: Mesh) -> Array:
        """Poids des formes de `mesh` vus depuis ce nœud : clip > nœud > maillage ; 0 par défaut. Taille =
        nombre de formes (poids en trop ignorés, manquants à 0)."""
        count = len(mesh.morph_targets)
        w = self.node_weights.get(node_index)
        if w is None:
            base = self.model.nodes[node_index].weights
            w = np.asarray(base if base is not None else mesh.morph_weights, float)
        out = np.zeros(count, dtype=np.float64)
        n = min(count, int(w.shape[0]))
        out[:n] = w[:n]
        return out


def slerp(q0: Array, q1: Array, s: float) -> Array:
    """Interpolation sphérique de deux quaternions (x, y, z, w), par le chemin le plus court."""
    a = np.asarray(q0, dtype=np.float64)
    b = np.asarray(q1, dtype=np.float64)
    d = float(a @ b)
    if d < 0.0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + s * (b - a)
    else:
        theta = math.acos(min(d, 1.0))
        sin_t = math.sin(theta)
        r = (math.sin((1 - s) * theta) * a + math.sin(s * theta) * b) / sin_t
    return np.asarray(normalize_quaternion(r), dtype=np.float64)


def sample_channel(channel: AnimationChannel, t: float) -> Array:
    """Valeur (C,) float64 d'un canal à l'instant `t` (clampé aux images clés extrêmes du canal)."""
    times = np.asarray(channel.times, dtype=np.float64)
    values = np.asarray(channel.values, dtype=np.float64)
    count = times.shape[0]
    cubic = channel.interpolation == "cubicspline"
    rotation = channel.path == "rotation"

    def value_at(k: int) -> Array:
        return values[k, 1] if cubic else values[k]

    def finish(v: Array) -> Array:
        if rotation:
            return np.asarray(normalize_quaternion(v), dtype=np.float64)
        return np.asarray(v, dtype=np.float64)

    if count == 1 or t <= times[0]:
        return finish(value_at(0))
    if t >= times[-1]:
        return finish(value_at(count - 1))
    k = int(np.searchsorted(times, t, side="right")) - 1
    k = min(max(k, 0), count - 2)
    dt = float(times[k + 1] - times[k])
    s = 0.0 if dt <= 0 else (t - float(times[k])) / dt
    if channel.interpolation == "step":
        return finish(value_at(k))
    if cubic:
        s2, s3 = s * s, s * s * s
        v0, b0 = values[k, 1], values[k, 2]  # valeur et tangente de sortie de k
        a1, v1 = (
            values[k + 1, 0],
            values[k + 1, 1],
        )  # tangente d'entrée et valeur de k+1
        r = (
            (2 * s3 - 3 * s2 + 1) * v0
            + (s3 - 2 * s2 + s) * dt * b0
            + (-2 * s3 + 3 * s2) * v1
            + (s3 - s2) * dt * a1
        )
        return finish(r)
    if rotation:
        return slerp(values[k], values[k + 1], s)
    return np.asarray(values[k] + s * (values[k + 1] - values[k]), dtype=np.float64)


def _clip_range(clip: AnimationClip) -> tuple[float, float]:
    firsts = [float(c.times[0]) for c in clip.channels if c.times.shape[0]]
    lasts = [float(c.times[-1]) for c in clip.channels if c.times.shape[0]]
    start = min(firsts) if firsts else 0.0
    end = max([float(clip.duration), *lasts]) if lasts else float(clip.duration)
    return start, max(end, start)


def clip_time(clip: AnimationClip, t: float, loop: bool = False) -> float:
    """Ramène `t` dans les bornes de la clip : clamp, ou modulo la durée si `loop`."""
    if not math.isfinite(t):
        raise ValueError("t doit être un nombre fini.")
    start, end = _clip_range(clip)
    if end <= start:
        return start
    if loop:
        return start + (t - start) % (end - start)
    return min(max(t, start), end)


def _resolve(
    model: Model, clip: AnimationClip | str | int | None
) -> AnimationClip | None:
    if clip is None or isinstance(clip, AnimationClip):
        return clip
    if isinstance(clip, bool):
        raise ValueError("clip : un nom ou un indice est attendu.")
    if isinstance(clip, int):
        if not 0 <= clip < len(model.animations):
            raise ValueError(
                f"clip {clip} : hors limites ({len(model.animations)} animations)."
            )
        return model.animations[clip]
    for c in model.animations:
        if c.name == clip:
            return c
    raise ValueError(f"clip {short(clip)} introuvable dans le modèle.")


def evaluate_pose(
    model: Model,
    clip: AnimationClip | str | int | None = None,
    t: float = 0.0,
    *,
    loop: bool = False,
) -> Pose:
    """Pose du modèle à l'instant `t` (secondes) pour une clip (objet, nom ou indice) ; `clip=None` : repos."""
    nodes = model.nodes
    count = len(nodes)
    trans = np.array([n.translation for n in nodes], dtype=np.float64).reshape(count, 3)
    rot = np.array([n.rotation for n in nodes], dtype=np.float64).reshape(count, 4)
    scl = np.array([n.scale for n in nodes], dtype=np.float64).reshape(count, 3)
    weights: dict[int, Array] = {}
    chosen = _resolve(model, clip)
    if chosen is not None:
        tc = clip_time(chosen, float(t), loop)
        for ch in chosen.channels:
            if ch.times.shape[0] == 0:
                continue
            v = sample_channel(ch, tc)
            if ch.path == "translation":
                trans[ch.target_node] = v
            elif ch.path == "rotation":
                rot[ch.target_node] = v
            elif ch.path == "scale":
                scl[ch.target_node] = v
            else:
                weights[ch.target_node] = _freeze(v)
    elif not math.isfinite(float(t)):
        raise ValueError("t doit être un nombre fini.")
    world = np.tile(np.eye(4), (count, 1, 1))
    reached = np.zeros(count, dtype=bool)
    stack = [(r, np.eye(4)) for r in reversed(model.root_nodes)]
    while stack:  # parcours itératif : aucune limite de profondeur de pile
        i, parent = stack.pop()
        if reached[i]:
            continue
        m = parent @ trs_to_matrix(trans[i], rot[i], scl[i])
        world[i] = m
        reached[i] = True
        stack.extend((c, m) for c in reversed(nodes[i].children))
    return Pose(
        model=model,
        translation=_freeze(trans),
        rotation=_freeze(rot),
        scale=_freeze(scl),
        world=_freeze(world),
        reached=_freeze(reached),
        node_weights=weights,
    )
