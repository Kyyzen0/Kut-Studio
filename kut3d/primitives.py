"""Formes de base paramétriques de Kut-3D : cube, sphère, plan, cylindre, cône, tore (ADR-0009, lot « primitives »).

Une primitive n'est JAMAIS stockée par ses sommets : c'est une description (genre + dimensions + tessellation) validée à la
construction, dont la géométrie est générée de façon déterministe par :mod:`kut3d.primitive_mesh`. Les objets sont immuables.

Conventions (celles du moteur : main droite, Y vers le haut) : toutes les formes sont centrées sur l'origine ; le cylindre, le
cône et le tore ont l'axe Y ; le plan est dans XZ (normale +Y). Les triangles sont orientés dans le sens inverse des aiguilles
d'une montre vu de l'extérieur (normale géométrique sortante) ; les UV suivent glTF (origine en haut à gauche, V vers le bas).

Bornes (entrée non fiable : un fichier `.kut3d` ou un script), toutes vérifiées AVANT de générer quoi que ce soit :

- dimensions : nombre fini dans [``MIN_DIMENSION``, ``MAX_DIMENSION``] (un booléen n'est pas un nombre) ;
- tessellation : entiers (jamais un flottant ni un booléen), segments dans [3, ``MAX_SEGMENTS``], anneaux dans [2, ``MAX_RINGS``],
  subdivisions du plan dans [1, ``MAX_SUBDIVISIONS``] ;
- ``MAX_PRIMITIVE_TRIANGLES`` : plafond du nombre de triangles d'UNE primitive, calculé par formule avant la génération ;
- ``MAX_SCENE_PRIMITIVE_TRIANGLES`` : plafond cumulé des primitives d'un même fichier de scène (vérifié par le lecteur).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from numbers import Integral, Real
from typing import Any, ClassVar

MIN_DIMENSION = 1e-6  # sous cette valeur, les triangles deviennent dégénérés en float32
MAX_DIMENSION = 1e6  # au-delà, le rendu perd sa précision (float32) et la profondeur de vue est dépassée
MIN_SEGMENTS = 3
MAX_SEGMENTS = 256  # segments autour d'un axe (sphère, cylindre, cône, tore)
MIN_RINGS = 2
MAX_RINGS = 256  # anneaux d'une sphère (de pôle à pôle)
MAX_SUBDIVISIONS = (
    256  # cases par côté d'un plan (et segments du petit cercle d'un tore)
)
MAX_PRIMITIVE_TRIANGLES = 65_536  # une sphère 256 x 128 (65 280) ou un plan 181 x 181 tiennent ; un plan 256 x 256 (131 072) non
MAX_SCENE_PRIMITIVE_TRIANGLES = (
    500_000  # cumul pour un fichier de scène (les maillages sont générés à la lecture)
)


def _dimension(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"{name} : un nombre est attendu (reçu {type(value).__name__})."
        )
    try:
        out = float(value)
    except OverflowError:
        raise ValueError(f"{name} : nombre démesuré.") from None
    if not math.isfinite(out):
        raise ValueError(f"{name} : nombre non fini (NaN ou infini).")
    if not MIN_DIMENSION <= out <= MAX_DIMENSION:
        raise ValueError(
            f"{name} doit être dans [{MIN_DIMENSION:g}, {MAX_DIMENSION:g}] (reçu {out:g})."
        )
    return out


def _count(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(
            f"{name} : un entier est attendu (reçu {type(value).__name__})."
        )
    if not low <= value <= high:
        raise ValueError(
            f"{name} doit être un entier dans [{low}, {high}] (reçu {value})."
        )
    return int(value)


def _flag(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} : un booléen est attendu (reçu {type(value).__name__})."
        )
    return value


def _check_triangles(spec: PrimitiveSpec) -> None:
    count = spec.triangle_count
    if count > MAX_PRIMITIVE_TRIANGLES:
        raise ValueError(
            f"{spec.kind} : {count} triangles, plus que le maximum de {MAX_PRIMITIVE_TRIANGLES} "
            "(réduisez la tessellation)."
        )


@dataclass(frozen=True)
class PrimitiveSpec:
    """Base commune : `kind` (clé du fichier), `params()` (les champs, dans leur forme JSON), comptes de sommets / triangles."""

    kind: ClassVar[str] = ""

    def params(self) -> dict[str, Any]:
        """Les paramètres, hors `kind` (valeurs JSON : flottants, entiers, booléens)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def vertex_count(self) -> int:
        raise NotImplementedError

    @property
    def triangle_count(self) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class Cube(PrimitiveSpec):
    """Boîte `width` (X) x `height` (Y) x `depth` (Z) ; 24 sommets (normales plates par face), 12 triangles."""

    kind: ClassVar[str] = "cube"
    width: float = 1.0
    height: float = 1.0
    depth: float = 1.0

    def __post_init__(self) -> None:
        for name in ("width", "height", "depth"):
            object.__setattr__(self, name, _dimension(getattr(self, name), name))

    @property
    def vertex_count(self) -> int:
        return 24

    @property
    def triangle_count(self) -> int:
        return 12


@dataclass(frozen=True)
class Sphere(PrimitiveSpec):
    """Sphère de rayon `radius` : `segments` méridiens, `rings` anneaux ; normales lisses, couture d'UV dédoublée, un sommet par segment à chaque pôle."""

    kind: ClassVar[str] = "sphere"
    radius: float = 0.5
    segments: int = 32
    rings: int = 16

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", _dimension(self.radius, "radius"))
        object.__setattr__(
            self,
            "segments",
            _count(self.segments, "segments", MIN_SEGMENTS, MAX_SEGMENTS),
        )
        object.__setattr__(
            self, "rings", _count(self.rings, "rings", MIN_RINGS, MAX_RINGS)
        )
        _check_triangles(self)

    @property
    def vertex_count(self) -> int:
        return (
            (self.rings - 1) * (self.segments + 1) + 2 * self.segments
        )  # anneaux intérieurs + un sommet par segment à chaque pôle

    @property
    def triangle_count(self) -> int:
        return self.segments * (
            2 * self.rings - 2
        )  # les deux pôles : un triangle par segment au lieu de deux


@dataclass(frozen=True)
class Plane(PrimitiveSpec):
    """Plan `width` (X) x `depth` (Z) à y = 0, normale +Y, `width_segments` x `depth_segments` cases. Surface ouverte."""

    kind: ClassVar[str] = "plane"
    width: float = 1.0
    depth: float = 1.0
    width_segments: int = 1
    depth_segments: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _dimension(self.width, "width"))
        object.__setattr__(self, "depth", _dimension(self.depth, "depth"))
        for name in ("width_segments", "depth_segments"):
            object.__setattr__(
                self, name, _count(getattr(self, name), name, 1, MAX_SUBDIVISIONS)
            )
        _check_triangles(self)

    @property
    def vertex_count(self) -> int:
        return (self.width_segments + 1) * (self.depth_segments + 1)

    @property
    def triangle_count(self) -> int:
        return 2 * self.width_segments * self.depth_segments


@dataclass(frozen=True)
class Cylinder(PrimitiveSpec):
    """Cylindre d'axe Y, rayon `radius`, hauteur `height` ; `caps` : bouchons plats en haut et en bas."""

    kind: ClassVar[str] = "cylinder"
    radius: float = 0.5
    height: float = 1.0
    segments: int = 32
    caps: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", _dimension(self.radius, "radius"))
        object.__setattr__(self, "height", _dimension(self.height, "height"))
        object.__setattr__(
            self,
            "segments",
            _count(self.segments, "segments", MIN_SEGMENTS, MAX_SEGMENTS),
        )
        object.__setattr__(self, "caps", _flag(self.caps, "caps"))
        _check_triangles(self)

    @property
    def vertex_count(self) -> int:
        side = 2 * (self.segments + 1)
        return side + (2 * (self.segments + 2) if self.caps else 0)

    @property
    def triangle_count(self) -> int:
        return 2 * self.segments + (2 * self.segments if self.caps else 0)


@dataclass(frozen=True)
class Cone(PrimitiveSpec):
    """Cône d'axe Y : base de rayon `radius` à y = -height/2, sommet à y = +height/2 ; `cap` : bouchon plat de la base."""

    kind: ClassVar[str] = "cone"
    radius: float = 0.5
    height: float = 1.0
    segments: int = 32
    cap: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", _dimension(self.radius, "radius"))
        object.__setattr__(self, "height", _dimension(self.height, "height"))
        object.__setattr__(
            self,
            "segments",
            _count(self.segments, "segments", MIN_SEGMENTS, MAX_SEGMENTS),
        )
        object.__setattr__(self, "cap", _flag(self.cap, "cap"))
        _check_triangles(self)

    @property
    def vertex_count(self) -> int:
        side = (
            self.segments + 1
        ) + self.segments  # anneau de base (couture dédoublée) + un sommet par segment
        return side + (self.segments + 2 if self.cap else 0)

    @property
    def triangle_count(self) -> int:
        return self.segments + (self.segments if self.cap else 0)


@dataclass(frozen=True)
class Torus(PrimitiveSpec):
    """Tore d'axe Y : `major_radius` (centre du tube), `minor_radius` (rayon du tube, strictement inférieur : sinon la
    surface se recoupe), `segments` autour de l'axe, `tube_segments` autour du tube."""

    kind: ClassVar[str] = "torus"
    major_radius: float = 0.5
    minor_radius: float = 0.2
    segments: int = 32
    tube_segments: int = 16

    def __post_init__(self) -> None:
        major = _dimension(self.major_radius, "major_radius")
        minor = _dimension(self.minor_radius, "minor_radius")
        if minor >= major:
            raise ValueError(
                f"minor_radius ({minor:g}) doit être strictement inférieur à major_radius ({major:g}) : "
                "sinon la surface se recoupe."
            )
        object.__setattr__(self, "major_radius", major)
        object.__setattr__(self, "minor_radius", minor)
        object.__setattr__(
            self,
            "segments",
            _count(self.segments, "segments", MIN_SEGMENTS, MAX_SEGMENTS),
        )
        object.__setattr__(
            self,
            "tube_segments",
            _count(self.tube_segments, "tube_segments", MIN_SEGMENTS, MAX_SEGMENTS),
        )
        _check_triangles(self)

    @property
    def vertex_count(self) -> int:
        return (self.segments + 1) * (self.tube_segments + 1)

    @property
    def triangle_count(self) -> int:
        return 2 * self.segments * self.tube_segments


PRIMITIVE_KINDS: dict[str, type[PrimitiveSpec]] = {
    cls.kind: cls for cls in (Cube, Sphere, Plane, Cylinder, Cone, Torus)
}
