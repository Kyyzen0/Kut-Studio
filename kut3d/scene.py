"""Scène 3D : caméra, lumières, instances de modèles (phase 2a d'ADR-0009).

Conventions : main droite, Y vers le haut, la caméra regarde vers son -Z local ; matrices 4x4 « colonne » en
float64. Tout est `frozen` et les tableaux numpy sont en lecture seule. Chaque objet est validé à la construction
(valeurs finies, bornes explicites) : une erreur est une `ValueError` en français.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from kut3d.errors import short
from kut3d.mathutil import Array
from kut3d.model import AnimationClip, Model

MAX_LIGHTS = 16  # lumières par scène (le coût de l'ombrage est linéaire en lumières)
MAX_INSTANCES = 2048  # modèles posés dans une scène
FOV_RANGE = (1.0, 170.0)  # degrés, bornes exclues
_PARALLEL_EPS = 1e-6


def _vec(value: Any, n: int, name: str) -> Array:
    try:
        arr = np.array(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValueError(f"{name} : {n} nombres attendus.") from None
    if arr.shape != (n,):
        raise ValueError(f"{name} : {n} nombres attendus (forme {arr.shape}).")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} : valeurs non finies (NaN ou infini).")
    arr.setflags(write=False)
    return arr


def _positive_color(value: Any, name: str) -> Array:
    arr = _vec(value, 3, name)
    if (arr < 0).any():
        raise ValueError(f"{name} : composantes négatives interdites.")
    return arr


def _intensity(value: float, name: str) -> float:
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"{name} : un nombre fini positif ou nul est attendu.")
    return v


@dataclass(frozen=True, eq=False)
class Camera:
    """Caméra perspective. `fov_y_deg` est l'ouverture verticale ; `near`/`far` bornent la profondeur de vue."""

    position: Any
    target: Any
    up: Any = (0.0, 1.0, 0.0)
    fov_y_deg: float = 40.0
    near: float = 0.1
    far: float = 1000.0

    def __post_init__(self) -> None:
        pos = _vec(self.position, 3, "position de la caméra")
        tgt = _vec(self.target, 3, "cible de la caméra")
        up = _vec(self.up, 3, "vecteur haut de la caméra")
        fov, near, far = (float(self.fov_y_deg), float(self.near), float(self.far))
        if not (math.isfinite(fov) and FOV_RANGE[0] < fov < FOV_RANGE[1]):
            raise ValueError(
                f"fov_y_deg doit être dans ]{FOV_RANGE[0]:g}, {FOV_RANGE[1]:g}[ (reçu {fov!r})."
            )
        if not (math.isfinite(near) and near > 0):
            raise ValueError(f"near doit être un nombre fini > 0 (reçu {near!r}).")
        if not (math.isfinite(far) and far > near):
            raise ValueError(f"far doit être un nombre fini > near (reçu {far!r}).")
        forward = tgt - pos
        dist = float(np.linalg.norm(forward))
        if dist < 1e-12:
            raise ValueError("la position et la cible de la caméra sont confondues.")
        up_len = float(np.linalg.norm(up))
        if up_len < 1e-12:
            raise ValueError("le vecteur haut de la caméra est nul.")
        if np.linalg.norm(np.cross(forward / dist, up / up_len)) < _PARALLEL_EPS:
            raise ValueError(
                "le vecteur haut est colinéaire à la direction de visée de la caméra."
            )
        for name, value in (
            ("position", pos),
            ("target", tgt),
            ("up", up),
            ("fov_y_deg", fov),
            ("near", near),
            ("far", far),
        ):
            object.__setattr__(self, name, value)

    @property
    def forward(self) -> Array:
        """Direction de visée unitaire."""
        d = np.asarray(self.target) - np.asarray(self.position)
        return np.asarray(d / np.linalg.norm(d))

    def view_matrix(self) -> Array:
        """Matrice monde -> vue (4x4 float64) : la caméra est à l'origine et regarde vers -Z."""
        eye = np.asarray(self.position)
        f = self.forward
        up = np.asarray(self.up)
        r = np.cross(f, up)
        r = r / np.linalg.norm(r)
        u = np.cross(r, f)
        m = np.eye(4, dtype=np.float64)
        m[0, :3], m[1, :3], m[2, :3] = r, u, -f
        m[:3, 3] = -(m[:3, :3] @ eye)
        return m

    def projection_matrix(self, aspect: float) -> Array:
        """Projection perspective (4x4 float64), profondeur NDC OpenGL [-1, 1] ; `aspect` = largeur / hauteur."""
        a = float(aspect)
        if not (math.isfinite(a) and a > 0):
            raise ValueError(
                f"le rapport largeur/hauteur doit être > 0 (reçu {aspect!r})."
            )
        n, fa = float(self.near), float(self.far)
        f = 1.0 / math.tan(math.radians(float(self.fov_y_deg)) / 2.0)
        m = np.zeros((4, 4), dtype=np.float64)
        m[0, 0] = f / a
        m[1, 1] = f
        m[2, 2] = (fa + n) / (n - fa)
        m[2, 3] = 2.0 * fa * n / (n - fa)
        m[3, 2] = -1.0
        return m


@dataclass(frozen=True, eq=False)
class AmbientLight:
    """Lumière d'ambiance uniforme (remplace, en attendant l'éclairage par image, l'éclairage indirect)."""

    color: Any = (1.0, 1.0, 1.0)
    intensity: float = 0.1

    def __post_init__(self) -> None:
        object.__setattr__(self, "color", _positive_color(self.color, "couleur"))
        object.__setattr__(
            self, "intensity", _intensity(self.intensity, "intensité de l'ambiance")
        )


@dataclass(frozen=True, eq=False)
class DirectionalLight:
    """Lumière directionnelle (soleil). `direction` est la direction DANS LAQUELLE la lumière se propage
    (comme glTF : une lumière qui éclaire vers le bas a la direction (0, -1, 0))."""

    direction: Any
    color: Any = (1.0, 1.0, 1.0)
    intensity: float = 1.0

    def __post_init__(self) -> None:
        d = _vec(self.direction, 3, "direction de la lumière")
        n = float(np.linalg.norm(d))
        if n < 1e-12:
            raise ValueError("la direction de la lumière est nulle.")
        d = d / n
        d.setflags(write=False)
        object.__setattr__(self, "direction", d)
        object.__setattr__(self, "color", _positive_color(self.color, "couleur"))
        object.__setattr__(
            self,
            "intensity",
            _intensity(self.intensity, "intensité de la lumière directionnelle"),
        )


@dataclass(frozen=True, eq=False)
class PointLight:
    """Lumière ponctuelle. L'intensité est celle mesurée à 1 unité ; atténuation en 1/d², fenêtrée par `range`
    (glTF KHR_lights_punctual : `clamp(1 - (d/range)^4, 0, 1)^2`) quand `range` est donné."""

    position: Any
    color: Any = (1.0, 1.0, 1.0)
    intensity: float = 1.0
    range: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "position", _vec(self.position, 3, "position de la lumière")
        )
        object.__setattr__(self, "color", _positive_color(self.color, "couleur"))
        object.__setattr__(
            self,
            "intensity",
            _intensity(self.intensity, "intensité de la lumière ponctuelle"),
        )
        if self.range is not None:
            r = float(self.range)
            if not (math.isfinite(r) and r > 0):
                raise ValueError(
                    f"range doit être un nombre fini > 0 ou None (reçu {self.range!r})."
                )
            object.__setattr__(self, "range", r)


Light = AmbientLight | DirectionalLight | PointLight


@dataclass(frozen=True, eq=False)
class Instance:
    """Un modèle posé dans la scène.

    `transform` (4x4) place le modèle APRÈS sa propre normalisation (`Model.root_transform`). `clip` (nom ou
    indice dans `model.animations`) anime le modèle : le temps de la clip vaut `t + time_offset` (t = instant
    du rendu) ; hors des bornes, il est ramené à la durée (ou bouclé si `loop`).
    """

    model: Model
    transform: Any = field(default_factory=lambda: np.eye(4))
    clip: str | int | None = None
    time_offset: float = 0.0
    name: str = ""
    loop: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.model, Model):
            raise ValueError("model doit être un Model (voir load_model).")
        m = np.array(self.transform, dtype=np.float64)
        if m.shape != (4, 4) or not np.isfinite(m).all():
            raise ValueError("transform : une matrice 4x4 finie est attendue.")
        if abs(np.linalg.det(m[:3, :3])) < 1e-18:
            raise ValueError("transform : matrice dégénérée (déterminant nul).")
        m.setflags(write=False)
        object.__setattr__(self, "transform", m)
        off = float(self.time_offset)
        if not math.isfinite(off):
            raise ValueError("time_offset doit être un nombre fini.")
        object.__setattr__(self, "time_offset", off)
        if self.clip is not None:
            self.resolve_clip()  # refuse un nom ou un indice inconnu dès la construction

    def resolve_clip(self) -> AnimationClip | None:
        """La clip choisie, ou None. Lève `ValueError` si elle n'existe pas dans le modèle."""
        if self.clip is None:
            return None
        clips = self.model.animations
        if isinstance(self.clip, bool):
            raise ValueError("clip : un nom ou un indice est attendu.")
        if isinstance(self.clip, int):
            if not 0 <= self.clip < len(clips):
                raise ValueError(
                    f"clip {self.clip} : hors limites ({len(clips)} animations)."
                )
            return clips[self.clip]
        for c in clips:
            if c.name == self.clip:
                return c
        names = ", ".join(short(c.name, 30) for c in clips[:8]) or "aucune"
        raise ValueError(f"clip {short(self.clip)} introuvable (animations : {names}).")


@dataclass(frozen=True, eq=False)
class Scene:
    """Instances + lumières + caméra. `background` : None = fond transparent (alpha 0), sinon un RGBA de
    quatre flottants dans [0, 1] (sRGB, alpha droit)."""

    instances: Any
    lights: Any
    camera: Camera
    background: Any = None

    def __post_init__(self) -> None:
        instances = tuple(self.instances)
        lights = tuple(self.lights)
        if len(instances) > MAX_INSTANCES:
            raise ValueError(
                f"trop d'instances ({len(instances)}, maximum {MAX_INSTANCES})."
            )
        if len(lights) > MAX_LIGHTS:
            raise ValueError(f"trop de lumières ({len(lights)}, maximum {MAX_LIGHTS}).")
        for inst in instances:
            if not isinstance(inst, Instance):
                raise ValueError("instances : des Instance sont attendues.")
        for light in lights:
            if not isinstance(light, (AmbientLight, DirectionalLight, PointLight)):
                raise ValueError("lights : des lumières de kut3d.scene sont attendues.")
        if not isinstance(self.camera, Camera):
            raise ValueError("camera doit être une Camera.")
        object.__setattr__(self, "instances", instances)
        object.__setattr__(self, "lights", lights)
        if self.background is not None:
            bg = _vec(self.background, 4, "fond")
            if (bg < 0).any() or (bg > 1).any():
                raise ValueError("fond : composantes RGBA attendues dans [0, 1].")
            object.__setattr__(self, "background", bg)
