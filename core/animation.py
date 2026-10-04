"""Moteur central d'animation : keyframes, interpolations, courbes.

Ce module est **pur** (aucune dépendance Qt ni FFmpeg) et sert de source de
vérité unique pour toute valeur animée de Kut-Studio : l'inspecteur, la
timeline, le Graph Editor, l'aperçu et l'export évaluent tous les mêmes
courbes, avec les mêmes coefficients.

Modèle
------

- :class:`Keyframe` — un point d'animation : temps (secondes **locales au
  clip**), valeur, interpolation du segment qui *part* de ce point, pentes
  Bézier entrante / sortante, mode des tangentes, identifiant stable.
- :class:`AnimationCurve` — suite triée et immuable de keyframes d'une même
  propriété, évaluable à n'importe quel instant en ``O(log n)``.
- :class:`AnimatableProperty` — description d'une propriété animable (type
  de valeur, défaut, bornes) ; voir :mod:`core.animation_targets` pour le
  registre et l'accès aux données d'un clip.

Temps
-----

Les temps sont des secondes locales au clip, **normalisées à la
microseconde** (:func:`normalize_time`) : deux keyframes « au même
instant » ont exactement la même clé, quel que soit le chemin de calcul.
Les keyframes ne dépendent pas du FPS ; l'interface les aligne sur la
grille d'images du projet (:func:`snap_local_time`), si bien qu'un même
projet se comporte proprement à 24, 25, 29,97 ou 60 i/s.

Interpolations
--------------

Sur un segment ``[t0, t1]`` (``u = (t - t0) / (t1 - t0)``, ``Δ = v1 - v0``) :

=============  ======================================================
``hold``       ``v0`` jusqu'à ``t1`` (exclu), puis ``v1``
``linear``     ``v0 + Δ·u``
``ease_in``    ``v0 + Δ·u²`` (départ lent)
``ease_out``   ``v0 + Δ·(2u − u²)`` (arrivée lente)
``ease_in_out`` ``v0 + Δ·(3u² − 2u³)`` (départ et arrivée lents)
``bezier``     Hermite cubique : pentes ``out_slope`` de ``k0`` et
               ``in_slope`` de ``k1`` (unités de valeur par seconde)
=============  ======================================================

Toutes se réduisent à un **polynôme cubique en u** : :attr:`AnimationCurve.segments`
en donne les coefficients, utilisés à l'identique par l'évaluation Python et
par la génération d'expressions FFmpeg (:mod:`core.visual_effects`). Il n'y a
donc ni échantillonnage, ni écart entre aperçu et export.

Bézier : les poignées affichées par le Graph Editor sont à un tiers du
segment ; leur pente est la tangente. En mode ``linked`` les deux pentes
d'un keyframe sont égales (courbe lisse) ; en mode ``broken`` elles sont
indépendantes. Une pente ``None`` est automatique (Catmull-Rom : pente
entre les voisins, nulle aux extrémités).

Avant le premier keyframe, la valeur est celle du premier keyframe ; après
le dernier, celle du dernier.
"""

from __future__ import annotations

import bisect
import math
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

TIME_DECIMALS = 6
"""Les temps sont normalisés à la microseconde."""

TIME_EPSILON = 0.5e-6
"""Deux temps plus proches que ceci sont le même instant."""


def normalize_time(seconds: float) -> float:
    """Temps normalisé à la microseconde (clé d'identité d'un keyframe)."""
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError(f"Temps non fini refusé : {seconds!r}.")
    result = round(value, TIME_DECIMALS)
    return 0.0 if result == 0 else result  # pas de -0.0


def snap_local_time(local_seconds: float, clip_start: float, fps: float) -> float:
    """Aligne un temps local sur la grille d'images **de la timeline**.

    Le keyframe tombe ainsi exactement sur une image affichée et exportée,
    même si le clip ne commence pas sur une frontière d'image.
    """
    if fps <= 0:
        return normalize_time(local_seconds)
    frame = round((float(clip_start) + float(local_seconds)) * float(fps))
    return normalize_time(frame / float(fps) - float(clip_start))


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class InterpolationType(str, Enum):
    """Interpolation du segment qui part d'un keyframe. La valeur est sérialisée."""

    HOLD = "hold"
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BEZIER = "bezier"


class TangentMode(str, Enum):
    """Lien entre les tangentes entrante et sortante d'un keyframe Bézier."""

    LINKED = "linked"
    BROKEN = "broken"


class ValueKind(str, Enum):
    """Types de valeurs animables."""

    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    VEC2 = "vec2"


def coerce_interpolation(value: object) -> InterpolationType:
    if isinstance(value, InterpolationType):
        return value
    try:
        return InterpolationType(str(value))
    except ValueError:
        return InterpolationType.LINEAR


def coerce_tangent_mode(value: object) -> TangentMode:
    if isinstance(value, TangentMode):
        return value
    try:
        return TangentMode(str(value))
    except ValueError:
        return TangentMode.LINKED


def _components(value: Any) -> tuple[float, ...]:
    """Valeur → composantes flottantes (scalaire = 1 composante)."""
    if isinstance(value, (tuple, list)):
        return tuple(float(item) for item in value)
    return (float(value),)


def _from_components(components: Sequence[float], kind: ValueKind, template: Any) -> Any:
    if kind is ValueKind.VEC2 or isinstance(template, (tuple, list)):
        return tuple(float(c) for c in components)
    if kind is ValueKind.INT:
        return int(round(components[0]))
    if kind is ValueKind.BOOL:
        return bool(components[0] >= 0.5)
    return float(components[0])


def new_keyframe_id() -> str:
    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# Keyframe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Keyframe:
    """Point d'animation d'une propriété.

    Attributes:
        property_name: identifiant de la propriété animée.
        time_seconds: temps local au clip (normalisé à la microseconde).
        value: valeur (``float``, ``int``, ``bool`` ou ``(x, y)``).
        interpolation: interpolation du segment qui part de ce keyframe.
        in_slope: pente d'arrivée Bézier (valeur/s), ``None`` = automatique.
        out_slope: pente de départ Bézier (valeur/s), ``None`` = automatique.
        tangent_mode: ``linked`` (pentes égales) ou ``broken``.
        id: identifiant stable pour l'édition (non comparé, non trié).
    """

    property_name: str
    time_seconds: float
    value: Any
    interpolation: InterpolationType = InterpolationType.LINEAR
    in_slope: Any = None
    out_slope: Any = None
    tangent_mode: TangentMode = TangentMode.LINKED
    id: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if self.time_seconds != self.time_seconds:  # NaN
            raise ValueError("time_seconds ne peut pas être NaN.")
        if self.time_seconds in (float("inf"), float("-inf")):
            raise ValueError(f"time_seconds doit être fini (reçu : {self.time_seconds}).")
        if self.time_seconds < -TIME_EPSILON:
            raise ValueError(
                f"time_seconds doit être positif ou nul (reçu : {self.time_seconds})."
            )
        object.__setattr__(self, "time_seconds", max(0.0, normalize_time(self.time_seconds)))
        object.__setattr__(self, "interpolation", coerce_interpolation(self.interpolation))
        object.__setattr__(self, "tangent_mode", coerce_tangent_mode(self.tangent_mode))
        for name in ("in_slope", "out_slope"):
            slope = getattr(self, name)
            if slope is not None:
                components = _components(slope)
                if not all(math.isfinite(c) for c in components):
                    raise ValueError(f"{name} doit être fini.")
                object.__setattr__(
                    self, name, components if isinstance(slope, (tuple, list)) else components[0]
                )
        if not self.id:
            object.__setattr__(self, "id", new_keyframe_id())

    def with_time(self, time_seconds: float) -> Keyframe:
        return replace(self, time_seconds=time_seconds, id=self.id)

    def with_value(self, value: Any) -> Keyframe:
        return replace(self, value=value, id=self.id)


# ---------------------------------------------------------------------------
# Polynômes de segment (source de vérité de l'évaluation)
# ---------------------------------------------------------------------------


Polynomial = tuple[float, float, float, float]
"""Coefficients ``(a, b, c, d)`` de ``a + b·u + c·u² + d·u³``."""


def _segment_component(
    interpolation: InterpolationType, v0: float, v1: float, span: float, m0: float, m1: float
) -> Polynomial:
    delta = v1 - v0
    if interpolation is InterpolationType.HOLD:
        return (v0, 0.0, 0.0, 0.0)
    if interpolation is InterpolationType.EASE_IN:
        return (v0, 0.0, delta, 0.0)
    if interpolation is InterpolationType.EASE_OUT:
        return (v0, 2.0 * delta, -delta, 0.0)
    if interpolation is InterpolationType.EASE_IN_OUT:
        return (v0, 0.0, 3.0 * delta, -2.0 * delta)
    if interpolation is InterpolationType.BEZIER:
        # Hermite : p(u) = h00 v0 + h10 S m0 + h01 v1 + h11 S m1
        s0, s1 = span * m0, span * m1
        return (v0, s0, 3.0 * delta - 2.0 * s0 - s1, -2.0 * delta + s0 + s1)
    return (v0, delta, 0.0, 0.0)  # LINEAR


def _evaluate_polynomial(poly: Polynomial, u: float) -> float:
    a, b, c, d = poly
    return a + u * (b + u * (c + u * d))


def _derivative_polynomial(poly: Polynomial, u: float) -> float:
    _a, b, c, d = poly
    return b + u * (2.0 * c + u * 3.0 * d)


@dataclass(frozen=True)
class Segment:
    """Segment résolu ``[t0, t1]`` : un polynôme par composante."""

    t0: float
    t1: float
    interpolation: InterpolationType
    polynomials: tuple[Polynomial, ...]

    @property
    def span(self) -> float:
        return self.t1 - self.t0


# ---------------------------------------------------------------------------
# Courbe
# ---------------------------------------------------------------------------


class AnimationCurve:
    """Keyframes triées d'une propriété, évaluables en ``O(log n)``.

    Immuable : chaque modification retourne une nouvelle courbe. Les
    segments (polynômes) sont résolus une fois à la construction ; une
    évaluation est une recherche dichotomique plus un polynôme de Horner.
    Deux keyframes au même instant : le dernier fourni gagne.
    """

    __slots__ = ("_keyframes", "_times", "_segments", "kind")

    def __init__(self, keyframes: Iterable[Keyframe] = (), kind: ValueKind = ValueKind.FLOAT) -> None:
        by_time: dict[float, Keyframe] = {}
        for keyframe in keyframes:
            by_time[keyframe.time_seconds] = keyframe
        ordered = tuple(by_time[t] for t in sorted(by_time))
        if kind is not ValueKind.VEC2 and ordered and isinstance(ordered[0].value, (tuple, list)):
            kind = ValueKind.VEC2
        self.kind = kind
        self._keyframes = ordered
        self._times = [k.time_seconds for k in ordered]
        self._segments = self._resolve_segments()

    # -- lecture -------------------------------------------------------------------------------

    @property
    def keyframes(self) -> tuple[Keyframe, ...]:
        return self._keyframes

    @property
    def times(self) -> tuple[float, ...]:
        return tuple(self._times)

    @property
    def segments(self) -> tuple[Segment, ...]:
        return self._segments

    def __len__(self) -> int:
        return len(self._keyframes)

    def __bool__(self) -> bool:
        return bool(self._keyframes)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AnimationCurve) and other._keyframes == self._keyframes

    def __repr__(self) -> str:
        return f"AnimationCurve({len(self._keyframes)} keyframes, {self.kind.value})"

    def index_at(self, time_seconds: float, tolerance: float = TIME_EPSILON) -> int | None:
        """Indice du keyframe situé à ``time_seconds`` (± ``tolerance``), sinon ``None``."""
        if not self._times:
            return None
        position = bisect.bisect_left(self._times, float(time_seconds) - tolerance)
        best: int | None = None
        best_distance = tolerance
        for index in (position - 1, position, position + 1):
            if 0 <= index < len(self._times):
                distance = abs(self._times[index] - float(time_seconds))
                if distance <= best_distance:
                    best, best_distance = index, distance
        return best

    def keyframe_at(self, time_seconds: float, tolerance: float = TIME_EPSILON) -> Keyframe | None:
        index = self.index_at(time_seconds, tolerance)
        return None if index is None else self._keyframes[index]

    def previous_time(self, time_seconds: float) -> float | None:
        """Temps du dernier keyframe strictement avant ``time_seconds``."""
        index = bisect.bisect_left(self._times, float(time_seconds) - TIME_EPSILON)
        return self._times[index - 1] if index > 0 else None

    def next_time(self, time_seconds: float) -> float | None:
        """Temps du premier keyframe strictement après ``time_seconds``."""
        index = bisect.bisect_right(self._times, float(time_seconds) + TIME_EPSILON)
        return self._times[index] if index < len(self._times) else None

    def segment_index(self, time_seconds: float) -> int | None:
        """Indice du segment contenant ``time_seconds`` (``None`` hors segments)."""
        if len(self._times) < 2:
            return None
        index = bisect.bisect_right(self._times, float(time_seconds)) - 1
        if index < 0 or index >= len(self._segments):
            return None
        return index

    # -- évaluation ----------------------------------------------------------------------------

    def evaluate_components(self, time_seconds: float) -> tuple[float, ...]:
        """Valeur à ``time_seconds`` sous forme de composantes flottantes."""
        keyframes = self._keyframes
        if not keyframes:
            raise ValueError("Une courbe vide n'a pas de valeur.")
        # Même résolution que les keyframes : ``4.0 - 2.7`` (1.2999…98) tombe
        # bien sur le keyframe à 1.3 (un « hold » ne saute pas une image trop tard).
        t = float(time_seconds)
        key = round(t, TIME_DECIMALS)  # choix du segment seulement ; ``u`` reste exact
        if key <= self._times[0]:
            return _components(keyframes[0].value)
        if key >= self._times[-1]:
            return _components(keyframes[-1].value)
        index = bisect.bisect_right(self._times, key) - 1
        segment = self._segments[index]
        u = min(1.0, max(0.0, (t - segment.t0) / segment.span))
        return tuple(_evaluate_polynomial(poly, u) for poly in segment.polynomials)

    def evaluate(self, time_seconds: float, default: Any = None) -> Any:
        """Valeur à ``time_seconds`` ; ``default`` pour une courbe vide."""
        if not self._keyframes:
            return default
        components = self.evaluate_components(time_seconds)
        return _from_components(components, self.kind, self._keyframes[0].value)

    def derivative_components(self, time_seconds: float) -> tuple[float, ...]:
        """Pente (valeur/s) à ``time_seconds`` ; nulle hors des segments."""
        index = self.segment_index(time_seconds)
        size = len(_components(self._keyframes[0].value)) if self._keyframes else 1
        if index is None:
            return (0.0,) * size
        segment = self._segments[index]
        u = (float(time_seconds) - segment.t0) / segment.span
        return tuple(_derivative_polynomial(p, u) / segment.span for p in segment.polynomials)

    def sample(self, start: float, end: float, count: int) -> list[tuple[float, Any]]:
        """``count`` points régulièrement espacés (affichage d'une courbe)."""
        if count < 2 or end <= start:
            return [(start, self.evaluate(start))]
        step = (end - start) / (count - 1)
        return [(start + i * step, self.evaluate(start + i * step)) for i in range(count)]

    # -- pentes résolues -----------------------------------------------------------------------

    def resolved_slopes(self, index: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Pentes ``(entrante, sortante)`` effectives du keyframe ``index``."""
        keyframe = self._keyframes[index]
        auto = self._auto_slope(index)
        incoming = _components(keyframe.in_slope) if keyframe.in_slope is not None else None
        outgoing = _components(keyframe.out_slope) if keyframe.out_slope is not None else None
        if keyframe.tangent_mode is TangentMode.LINKED:
            shared = outgoing if outgoing is not None else incoming
            incoming = outgoing = shared
        return (incoming if incoming is not None else auto, outgoing if outgoing is not None else auto)

    def _auto_slope(self, index: int) -> tuple[float, ...]:
        keyframes = self._keyframes
        size = len(_components(keyframes[index].value))
        if index == 0 or index == len(keyframes) - 1:
            return (0.0,) * size
        before, after = keyframes[index - 1], keyframes[index + 1]
        span = after.time_seconds - before.time_seconds
        if span <= 0:
            return (0.0,) * size
        return tuple(
            (b - a) / span for a, b in zip(_components(before.value), _components(after.value))
        )

    def _resolve_segments(self) -> tuple[Segment, ...]:
        keyframes = self._keyframes
        segments: list[Segment] = []
        bool_kind = self.kind is ValueKind.BOOL
        for index in range(len(keyframes) - 1):
            k0, k1 = keyframes[index], keyframes[index + 1]
            span = k1.time_seconds - k0.time_seconds
            interpolation = InterpolationType.HOLD if bool_kind else k0.interpolation
            v0, v1 = _components(k0.value), _components(k1.value)
            if interpolation is InterpolationType.BEZIER:
                m0 = self.resolved_slopes(index)[1]
                m1 = self.resolved_slopes(index + 1)[0]
            else:
                m0 = m1 = (0.0,) * len(v0)
            polynomials = tuple(
                _segment_component(interpolation, a, b, span, s0, s1)
                for a, b, s0, s1 in zip(v0, v1, m0, m1)
            )
            segments.append(Segment(k0.time_seconds, k1.time_seconds, interpolation, polynomials))
        return tuple(segments)

    # -- modifications (retournent une nouvelle courbe) ------------------------------------------

    def with_keyframe(self, keyframe: Keyframe) -> AnimationCurve:
        """Ajoute ``keyframe`` ou remplace celui du même instant."""
        kept = [k for k in self._keyframes if k.time_seconds != keyframe.time_seconds]
        return AnimationCurve([*kept, keyframe], self.kind)

    def without_times(self, times: Iterable[float]) -> AnimationCurve:
        removed = {normalize_time(t) for t in times}
        return AnimationCurve([k for k in self._keyframes if k.time_seconds not in removed], self.kind)

    def without_ids(self, ids: Iterable[str]) -> AnimationCurve:
        removed = set(ids)
        return AnimationCurve([k for k in self._keyframes if k.id not in removed], self.kind)

    def replaced(self, updates: dict[str, Keyframe]) -> AnimationCurve:
        """Remplace les keyframes par identifiant (déplacements, valeurs, interpolation)."""
        return AnimationCurve([updates.get(k.id, k) for k in self._keyframes], self.kind)

    def inserted_preserving_shape(
        self, time_seconds: float, *, keyframe_id: str = "", clamp: Callable[[Any], Any] | None = None
    ) -> AnimationCurve:
        """Insère un keyframe à ``time_seconds`` **sans changer l'animation**.

        Un segment polynomial coupé en deux reste deux cubiques : les deux
        moitiés deviennent des segments Bézier dont les pentes reproduisent
        exactement la courbe d'origine. Linéaire et Hold restent tels quels.
        Avant le premier / après le dernier keyframe, le nouveau segment est
        explicitement plat. ``clamp`` (bornes de la propriété) : si la courbe
        déborde à cet instant, le keyframe prend la valeur **affichée** (bornée)
        avec une tangente nulle, comme le rendu borné.
        """
        t = normalize_time(time_seconds)
        if not self._keyframes:
            raise ValueError("Impossible d'insérer dans une courbe vide sans valeur.")
        existing = self.index_at(t)
        if existing is not None:
            return self
        name = self._keyframes[0].property_name
        make = type(self._keyframes[0])  # garde la classe (TransformKeyframe valide ses bornes)
        value = self.evaluate(t)
        clamped = False
        if clamp is not None:
            bounded = clamp(value)
            clamped = bounded != value
            value = bounded
        index = self.segment_index(t)
        if index is None:  # avant le premier ou après le dernier : valeur constante
            new_id = keyframe_id or new_keyframe_id()
            if t < self._times[0]:
                # Segment ajouté entre deux valeurs égales : linéaire = plat. Les
                # pentes de l'ancien premier keyframe sont figées (sinon ses pentes
                # automatiques changeraient avec son nouveau voisin).
                pinned = self._pinned(0)
                added = make(name, t, value, InterpolationType.LINEAR, id=new_id)
                return AnimationCurve([added, pinned, *self._keyframes[1:]], self.kind)
            last = self._keyframes[-1]
            pinned = replace(self._pinned(len(self._keyframes) - 1), interpolation=InterpolationType.LINEAR, id=last.id)
            added = make(name, t, value, last.interpolation, id=new_id)
            return AnimationCurve([*self._keyframes[:-1], pinned, added], self.kind)
        segment = self._segments[index]
        interpolation = segment.interpolation
        if interpolation in (InterpolationType.LINEAR, InterpolationType.HOLD):
            middle = make(name, t, value, interpolation, id=keyframe_id or new_keyframe_id())
            return self.with_keyframe(middle)
        k0, k1 = self._keyframes[index], self._keyframes[index + 1]
        start_slope = _poly_slopes(segment, 0.0)
        middle_slope = _poly_slopes(segment, (t - segment.t0) / segment.span)
        end_slope = _poly_slopes(segment, 1.0)
        k1_in = _scalar_or_tuple(end_slope, k1.value)
        k0_out = _scalar_or_tuple(start_slope, k0.value)
        if clamped:
            middle_slope = tuple(0.0 for _ in middle_slope)
        mid = _scalar_or_tuple(middle_slope, value)
        # Pentes effectives figées des deux côtés : les segments voisins ne
        # bougent pas (pentes liées ou automatiques comprises).
        updated_k0 = replace(
            k0, interpolation=InterpolationType.BEZIER, out_slope=k0_out,
            in_slope=_scalar_or_tuple(self.resolved_slopes(index)[0], k0.value),
            tangent_mode=TangentMode.BROKEN, id=k0.id,
        )
        updated_k1 = replace(
            k1, in_slope=k1_in,
            out_slope=_scalar_or_tuple(self.resolved_slopes(index + 1)[1], k1.value),
            tangent_mode=TangentMode.BROKEN, id=k1.id,
        )
        middle = make(
            name, t, value, InterpolationType.BEZIER, in_slope=mid, out_slope=mid,
            tangent_mode=TangentMode.LINKED, id=keyframe_id or new_keyframe_id(),
        )
        others = [k for k in self._keyframes if k.id not in (k0.id, k1.id)]
        return AnimationCurve([*others, updated_k0, middle, updated_k1], self.kind)

    def _pinned(self, index: int) -> Keyframe:
        """Keyframe ``index`` avec ses pentes effectives fixées (mode séparé)."""
        keyframe = self._keyframes[index]
        incoming, outgoing = self.resolved_slopes(index)
        return replace(
            keyframe, in_slope=_scalar_or_tuple(incoming, keyframe.value),
            out_slope=_scalar_or_tuple(outgoing, keyframe.value),
            tangent_mode=TangentMode.BROKEN, id=keyframe.id,
        )

    def split(
        self, time_seconds: float, *, clamp: Callable[[Any], Any] | None = None
    ) -> tuple[AnimationCurve, AnimationCurve]:
        """Coupe la courbe : (partie ``≤ t``, partie ``≥ t`` recalée à 0), sans changer l'animation."""
        if not self._keyframes:
            return AnimationCurve((), self.kind), AnimationCurve((), self.kind)
        t = normalize_time(time_seconds)
        whole = self.inserted_preserving_shape(t, clamp=clamp)
        left = [k for k in whole.keyframes if k.time_seconds <= t]
        right = [
            replace(k, time_seconds=normalize_time(k.time_seconds - t), id=new_keyframe_id())
            for k in whole.keyframes if k.time_seconds >= t
        ]
        return AnimationCurve(left, self.kind), AnimationCurve(right, self.kind)


def _poly_slopes(segment: Segment, u: float) -> tuple[float, ...]:
    return tuple(_derivative_polynomial(p, u) / segment.span for p in segment.polynomials)


def _scalar_or_tuple(components: Sequence[float], template: Any) -> Any:
    if isinstance(template, (tuple, list)):
        return tuple(float(c) for c in components)
    return float(components[0])


# ---------------------------------------------------------------------------
# Propriétés animables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnimatableProperty:
    """Description d'une propriété animable.

    Attributes:
        id: identifiant stable (stocké dans les keyframes et les fichiers).
        label_key: clé i18n du libellé.
        kind: type de valeur.
        default: valeur statique par défaut.
        minimum / maximum: bornes (``None`` = libre) ; l'évaluation est
            bornée ici, dans l'aperçu comme dans l'export.
        step: pas d'édition proposé à l'interface.
        group: regroupement d'affichage (``transform``, ``effect``…).
        display_scale / display_unit: l'interface montre ``valeur × display_scale`` suivie de ``display_unit`` (la vitesse,
            stockée en facteur 1,0, s'affiche « 100 % ») ; les keyframes et les calculs restent dans l'unité stockée.
    """

    id: str
    label_key: str
    kind: ValueKind = ValueKind.FLOAT
    default: Any = 0.0
    minimum: float | None = None
    maximum: float | None = None
    step: float = 0.01
    group: str = "transform"
    display_scale: float = 1.0
    display_unit: str = ""

    def clamp(self, value: Any) -> Any:
        if self.kind is ValueKind.BOOL:
            return bool(value)
        components = [
            min(self.maximum, c) if self.maximum is not None else c
            for c in (max(self.minimum, c) if self.minimum is not None else c for c in _components(value))
        ]
        return _from_components(components, self.kind, value)

    def compatible_with(self, other: AnimatableProperty) -> bool:
        """Deux propriétés acceptent les mêmes keyframes (copier / coller)."""
        return self.kind is other.kind

    def evaluate(self, curve: AnimationCurve | None, static: Any, time_seconds: float) -> Any:
        """Valeur effective : statique sans keyframe, sinon la courbe (bornée)."""
        if curve is None or not curve:
            return static
        return self.clamp(curve.evaluate(time_seconds))


def interpolation_label(interpolation: InterpolationType) -> str:
    """Clé i18n du nom d'une interpolation."""
    return f"animation.interpolation.{coerce_interpolation(interpolation).value}"


__all__ = [
    "TIME_DECIMALS",
    "TIME_EPSILON",
    "AnimatableProperty",
    "AnimationCurve",
    "InterpolationType",
    "Keyframe",
    "Polynomial",
    "Segment",
    "TangentMode",
    "ValueKind",
    "coerce_interpolation",
    "coerce_tangent_mode",
    "interpolation_label",
    "new_keyframe_id",
    "normalize_time",
    "snap_local_time",
]
