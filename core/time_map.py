"""Temps de la timeline → temps source : l'unique modèle temporel de Kut-Studio.

La **vitesse** d'un clip est une propriété (constante, ou animée par des keyframes) ; le **temps source** en est
l'intégrale. Tout ce qui, ailleurs, demandait « quelle image source à cet instant ? », « combien dure ce clip ? »,
« quelle portion de source couvre cette fenêtre ? » ou « à quel instant la timeline montre-t-elle ce temps source ? »
passe ici, et nulle part ailleurs : évaluateur, séquences imbriquées, suivi, Multicam, sous-titres, export, aperçu.

Définitions (temps local du clip = secondes depuis son début sur la timeline) :

``v(t)``
    vitesse signée : la valeur animée ``f(t)`` (ou la vitesse statique), multipliée par ``-1`` si le clip est inversé.
``M(t) = ancre + ∫₀ᵗ v``
    temps source montré à l'instant ``t``. Continu, même quand la vitesse saute (le mapping est une intégrale).
fenêtre ``[lo, hi]``
    portion de média autorisée (``source_in`` / ``source_out``) : ``M`` n'en sort jamais (aucune image demandée hors
    limites).
durée ``T``
    **dérivée**, jamais stockée à part : premier instant où ``M`` atteint une borne de la fenêtre en s'y dirigeant
    (la source est épuisée). Une durée imposée peut la raccourcir. C'est le comportement historique (``durée = portion
    de source ÷ vitesse``), étendu aux courbes.

Représentation : chaque segment de keyframe est un polynôme de degré ≤ 3 en ``u = (t − t₀) / durée_du_segment``
(:mod:`core.animation`) ; son intégrale est donc un polynôme de degré 4, **exact** : pas de table, pas de dérive
numérique, une évaluation est une recherche dichotomique et un polynôme de Horner. Le dépassement des bornes de la
propriété (une tangente Bézier qui sort de ±10) coupe le segment aux points d'intersection : chaque morceau est soit un
polynôme exact, soit une constante.

Le cas **constant** (aucune courbe) garde les formules historiques à l'identique (mêmes opérations flottantes) : un ancien
projet donne le même montage au bit près.

Fonctions pures, sans Qt ni NumPy.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

from .time_remapping import MAX_SPEED, FreezeFrameMode

if TYPE_CHECKING:
    from .animation import Keyframe
    from .project_model import Clip

SPEED_PROPERTY = "time.speed"
"""Identifiant de la propriété animable « vitesse » (clé des keyframes dans ``Clip.animation``)."""

SPEED_LIMIT = MAX_SPEED
"""Borne de la vitesse animée, dans les deux sens : ``[-SPEED_LIMIT, +SPEED_LIMIT]``."""

MIN_DURATION = 1e-6
"""Durée plancher d'un clip : un mapping qui épuiserait la source à l'instant 0 ne produit pas un clip vide."""

DEFAULT_LINEARIZATION_TOLERANCE = 0.02 / 30.0
"""Écart toléré (secondes de source) d'une approximation par morceaux à vitesse constante : 2 % d'une image à 30 i/s."""

MAX_PIECES_PER_RUN = 256

_REST = 1e-9
"""Vitesse (source-secondes par seconde) en deçà de laquelle un intervalle est un arrêt."""

_ROOT_MERGE = 1e-12


# ---------------------------------------------------------------------------
# Racines de polynômes (Python pur : ce chemin est dans ``Clip.duration``, jamais dans NumPy)
# ---------------------------------------------------------------------------


def _peval(coeffs: tuple[float, ...], x: float) -> float:
    result = 0.0
    for coefficient in reversed(coeffs):
        result = result * x + coefficient
    return result


def _pder(coeffs: tuple[float, ...]) -> tuple[float, ...]:
    if len(coeffs) <= 1:
        return (0.0,)
    return tuple(index * coeffs[index] for index in range(1, len(coeffs)))


def _trim(coeffs: tuple[float, ...]) -> tuple[float, ...]:
    scale = max((abs(c) for c in coeffs), default=0.0)
    end = len(coeffs)
    while end > 1 and abs(coeffs[end - 1]) <= 1e-14 * max(scale, 1e-300):
        end -= 1
    return tuple(coeffs[:end])


def _dedupe(values: list[float]) -> list[float]:
    values.sort()
    out: list[float] = []
    for value in values:
        if not out or abs(value - out[-1]) > _ROOT_MERGE:
            out.append(value)
    return out


def _bisect_root(coeffs: tuple[float, ...], a: float, b: float, fa: float) -> float:
    for _ in range(120):
        mid = 0.5 * (a + b)
        fm = _peval(coeffs, mid)
        if fm == 0.0:
            return mid
        if (fa < 0.0) == (fm < 0.0):
            a, fa = mid, fm
        else:
            b = mid
        if b - a <= 1e-16:
            break
    return 0.5 * (a + b)


def real_roots(coeffs: tuple[float, ...], lo: float, hi: float) -> list[float]:
    """Racines réelles de ``Σ coeffs[i]·xⁱ`` dans ``[lo, hi]`` (triées, sans doublon).

    Degré ≤ 2 : formules ; au-delà : les points critiques (racines de la dérivée) découpent ``[lo, hi]`` en intervalles
    monotones, où une racine se trouve par dichotomie. Les racines tangentielles (un maximum qui frôle zéro) sont
    retenues : elles comptent comme points de retournement.
    """
    coeffs = _trim(tuple(float(c) for c in coeffs))
    degree = len(coeffs) - 1
    if degree <= 0:
        return []
    slack = 1e-12
    if degree == 1:
        x = -coeffs[0] / coeffs[1]
        return [x] if lo - slack <= x <= hi + slack else []
    if degree == 2:
        c0, c1, c2 = coeffs
        disc = c1 * c1 - 4.0 * c2 * c0
        if disc < 0.0:
            if disc > -1e-14 * max(c1 * c1, abs(4.0 * c2 * c0), 1e-300):
                disc = 0.0
            else:
                return []
        root = math.sqrt(disc)
        q = -0.5 * (c1 + math.copysign(root, c1 if c1 != 0.0 else 1.0))
        found = [q / c2]
        if q != 0.0:
            found.append(c0 / q)
        return _dedupe([x for x in found if lo - slack <= x <= hi + slack])
    critical = real_roots(_pder(coeffs), lo, hi)
    points = [lo, *[c for c in critical if lo < c < hi], hi]
    values = [_peval(coeffs, p) for p in points]
    zero = 1e-11 * (1.0 + max(abs(c) for c in coeffs))
    roots: list[float] = [p for p, f in zip(points, values) if abs(f) <= zero]
    for (a, fa), (b, fb) in zip(zip(points, values), zip(points[1:], values[1:])):
        if fa * fb < 0.0:
            roots.append(_bisect_root(coeffs, a, b, fa))
    return _dedupe(roots)


# ---------------------------------------------------------------------------
# Types publics
# ---------------------------------------------------------------------------


class RunKind(str, Enum):
    """Sens d'un intervalle monotone du mapping."""

    FORWARD = "forward"
    BACKWARD = "backward"
    HOLD = "hold"


@dataclass(frozen=True)
class Run:
    """Intervalle de la timeline sur lequel le temps source est monotone (ou arrêté)."""

    t0: float
    t1: float
    s0: float
    s1: float
    kind: RunKind

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def direction(self) -> int:
        return 1 if self.kind is RunKind.FORWARD else -1 if self.kind is RunKind.BACKWARD else 0


@dataclass(frozen=True)
class Piece:
    """Morceau à **vitesse constante** d'un :class:`Run` : mêmes extrémités que le mapping exact, ligne droite entre elles."""

    t0: float
    t1: float
    s0: float
    s1: float

    @property
    def speed(self) -> float:
        span = self.t1 - self.t0
        return (self.s1 - self.s0) / span if span > 0.0 else 0.0


class TimeMap:
    """Interface commune des mappings (constant, ou par morceaux)."""

    duration: float
    lo: float
    hi: float

    def source_time(self, t: float) -> float:
        raise NotImplementedError

    def speed_at(self, t: float) -> float:
        raise NotImplementedError

    def runs(self) -> tuple[Run, ...]:
        raise NotImplementedError

    def times_at_source(self, s: float) -> list[float]:
        raise NotImplementedError

    @property
    def is_constant(self) -> bool:
        return False

    @property
    def is_hold(self) -> bool:
        """Le mapping tout entier montre-t-il un seul instant source (arrêt sur image) ?"""
        runs = self.runs()
        return len(runs) == 1 and runs[0].kind is RunKind.HOLD

    def timeline_intervals_of(self, low: float, high: float) -> list[tuple[float, float]]:
        """Intervalles disjoints (temps local du clip, triés) où le temps source est dans ``[low, high]``.

        Le chemin inverse d'une plage de la source. Un mapping monotone en donne un seul ; un mapping qui revient en
        arrière peut en donner plusieurs (une réplique de sous-titre est alors visible à plusieurs moments).
        """
        raise NotImplementedError

    def local_time_for(self, s: float) -> float:
        """Premier instant (temps local du clip) où le mapping montre ``s`` ; la borne la plus proche si jamais atteint.

        Sert aux données exprimées en temps source (suivi, stabilisation) qui cherchent « où est-ce, sur le clip ? » : un
        instant hors de la source est ramené au début ou à la fin du clip, comme les appelants le bornaient déjà.
        """
        times = self.times_at_source(s)
        if times:
            return times[0]
        start, end = self.source_time(0.0), self.source_time(self.duration)
        return 0.0 if abs(start - s) <= abs(end - s) else self.duration

    def timeline_span_of(self, low: float, high: float) -> tuple[float, float]:
        """Enveloppe (premier et dernier instant) de :meth:`timeline_intervals_of` ; ``(0, 0)`` si aucun."""
        intervals = self.timeline_intervals_of(low, high)
        if not intervals:
            return 0.0, 0.0
        return intervals[0][0], max(end for _start, end in intervals)

    # -- dérivés (identiques pour tous) --------------------------------------------------------------

    def extent(self) -> tuple[float, float]:
        """Plus petit et plus grand temps source atteints sur toute la durée."""
        return self.window_for(0.0, self.duration)

    def window_for(self, t0: float, t1: float) -> tuple[float, float]:
        """Plus petit et plus grand temps source atteints sur ``[t0, t1]`` (le mapping peut revenir en arrière)."""
        t0, t1 = sorted((min(max(t0, 0.0), self.duration), min(max(t1, 0.0), self.duration)))
        values = [self.source_time(t0), self.source_time(t1)]
        for run in self.runs():
            for boundary in (run.t0, run.t1):
                if t0 < boundary < t1:
                    values.append(self.source_time(boundary))
        return min(values), max(values)

    def pieces(self, tolerance: float = DEFAULT_LINEARIZATION_TOLERANCE) -> tuple[tuple[Run, tuple[Piece, ...]], ...]:
        """Chaque :class:`Run` avec son approximation par morceaux à vitesse constante (écart ≤ ``tolerance`` source-s)."""
        return tuple((run, self._linearize(run, tolerance)) for run in self.runs())

    def _linearize(self, run: Run, tolerance: float) -> tuple[Piece, ...]:
        if run.kind is RunKind.HOLD or self.is_constant:
            return (Piece(run.t0, run.t1, run.s0, run.s1),)
        pieces: list[Piece] = []

        def error(a: float, b: float, sa: float, sb: float) -> float:
            worst = 0.0
            for k in range(1, 8):
                f = k / 8.0
                t = a + (b - a) * f
                worst = max(worst, abs(self.source_time(t) - (sa + (sb - sa) * f)))
            return worst

        def split(a: float, b: float, sa: float, sb: float, depth: int) -> None:
            if depth >= 12 or len(pieces) >= MAX_PIECES_PER_RUN - 1 or error(a, b, sa, sb) <= tolerance:
                pieces.append(Piece(a, b, sa, sb))
                return
            mid = 0.5 * (a + b)
            sm = self.source_time(mid)
            split(a, mid, sa, sm, depth + 1)
            split(mid, b, sm, sb, depth + 1)

        split(run.t0, run.t1, run.s0, run.s1, 0)
        return tuple(pieces)


# ---------------------------------------------------------------------------
# Mapping constant : les formules historiques, à l'identique
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstantTimeMap(TimeMap):
    """Vitesse constante (lecture normale, ralentie, accélérée, inversée) ou arrêt sur image.

    Les opérations flottantes reprennent celles de l'ancien code : ``durée = (hi − lo) ÷ vitesse`` ; vers l'avant
    ``lo + t·vitesse`` ; en inverse ``lo + ((hi − lo) − t·vitesse)``.
    """

    lo: float
    hi: float
    speed: float = 1.0
    reverse: bool = False
    hold: bool = False
    hold_source: float = 0.0
    hold_duration: float = 1.0

    @property
    def duration(self) -> float:  # type: ignore[override]
        if self.hold:
            return self.hold_duration
        return (self.hi - self.lo) / self.speed

    @property
    def is_constant(self) -> bool:
        return True

    def source_time(self, t: float) -> float:
        if self.hold:
            return self.hold_source
        t = min(max(t, 0.0), self.duration)
        relative = t * self.speed
        if self.reverse:
            relative = (self.hi - self.lo) - relative
        return min(max(self.lo + relative, self.lo), self.hi)

    def strict_source_time(self, t: float) -> float:
        """Comme :meth:`source_time`, mais lève ``ValueError`` hors des bornes (contrat de l'ancienne API)."""
        if self.hold:
            return self.hold_source
        span = self.hi - self.lo
        relative = t * self.speed
        if self.reverse:
            relative = span - relative
        if relative < 0.0 or relative > span:
            raise ValueError(
                f"timeline_time {t} avec speed {self.speed} et reverse {self.reverse} "
                f"donne un temps source hors des bornes [{self.lo}, {self.hi}]."
            )
        return self.lo + relative

    def speed_at(self, t: float) -> float:
        if self.hold:
            return 0.0
        return -self.speed if self.reverse else self.speed

    def runs(self) -> tuple[Run, ...]:
        duration = self.duration
        if self.hold:
            return (Run(0.0, duration, self.hold_source, self.hold_source, RunKind.HOLD),)
        start, end = self.source_time(0.0), self.source_time(duration)
        return (Run(0.0, duration, start, end, RunKind.BACKWARD if self.reverse else RunKind.FORWARD),)

    def times_at_source(self, s: float) -> list[float]:
        if self.hold:
            return [0.0] if abs(s - self.hold_source) <= 1e-9 else []
        if s < self.lo - 1e-9 or s > self.hi + 1e-9:
            return []
        relative = s - self.lo
        if self.reverse:
            relative = (self.hi - self.lo) - relative
        return [min(max(relative / self.speed, 0.0), self.duration)]

    def extent(self) -> tuple[float, float]:
        if self.hold:
            return self.hold_source, self.hold_source
        return self.lo, self.hi

    @property
    def is_hold(self) -> bool:
        return self.hold

    def window_for(self, t0: float, t1: float) -> tuple[float, float]:
        """Mêmes opérations flottantes que l'ancien ``nested_source_window`` (les fenêtres entrent dans des clés)."""
        t0, t1 = sorted((min(max(t0, 0.0), self.duration), min(max(t1, 0.0), self.duration)))
        if self.hold:
            return self.hold_source, self.hold_source
        if self.reverse:
            a, b = self.hi - t1 * self.speed, self.hi - t0 * self.speed
        else:
            a, b = self.lo + t0 * self.speed, self.lo + t1 * self.speed
        return max(self.lo, a), min(self.hi, b)

    def timeline_span_of(self, low: float, high: float) -> tuple[float, float]:
        """Mêmes opérations flottantes que l'ancien ``_map_to_parent`` ; un arrêt sur image couvre toute sa durée."""
        if self.hold:
            return 0.0, self.duration
        if self.reverse:
            a, b = (self.hi - high) / self.speed, (self.hi - low) / self.speed
        else:
            a, b = (low - self.lo) / self.speed, (high - self.lo) / self.speed
        return a, b

    def timeline_intervals_of(self, low: float, high: float) -> list[tuple[float, float]]:
        return [self.timeline_span_of(low, high)]


# ---------------------------------------------------------------------------
# Mapping par morceaux (courbe de vitesse)
# ---------------------------------------------------------------------------


class _Seg:
    """Un intervalle ``[t0, t1]`` de la timeline : vitesse constante, ou polynôme de segment de keyframe."""

    __slots__ = ("f0", "m0", "poly", "rate", "span", "t0", "t1", "ts0", "u0")

    def __init__(self, t0: float, t1: float, rate: float, poly: tuple[float, float, float, float] | None,
                 ts0: float = 0.0, span: float = 1.0) -> None:
        self.t0, self.t1 = t0, t1
        self.rate, self.poly = rate, poly
        self.ts0, self.span = ts0, span
        self.m0 = 0.0
        self.f0 = 0.0
        self.u0 = 0.0

    def v(self, t: float) -> float:
        if self.poly is None:
            return self.rate
        u = (t - self.ts0) / self.span
        a, b, c, d = self.poly
        return a + u * (b + u * (c + u * d))

    def _primitive(self, u: float) -> float:
        a, b, c, d = self.poly  # type: ignore[misc]
        return u * (a + u * (b / 2.0 + u * (c / 3.0 + u * d / 4.0)))

    def anchor(self, m0: float) -> None:
        """Fixe ``M(t0) = m0`` (mémorise la primitive à l'entrée du segment)."""
        self.m0 = m0
        if self.poly is not None:
            self.u0 = (self.t0 - self.ts0) / self.span
            self.f0 = self._primitive(self.u0)

    def m(self, t: float) -> float:
        if self.poly is None:
            return self.m0 + self.rate * (t - self.t0)
        u = (t - self.ts0) / self.span
        return self.m0 + self.span * (self._primitive(u) - self.f0)

    def turning_times(self, ta: float, tb: float) -> list[float]:
        """Instants de ``(ta, tb)`` où la vitesse s'annule (retournements, départs d'arrêt)."""
        if self.poly is None:
            return []
        lo = (ta - self.ts0) / self.span
        hi = (tb - self.ts0) / self.span if math.isfinite(tb) else 1.0
        roots = real_roots(self.poly, lo, hi)
        return [self.ts0 + r * self.span for r in roots if ta + _ROOT_MERGE < self.ts0 + r * self.span < tb - _ROOT_MERGE]


def _clamp_speed(value: float) -> float:
    return min(max(value, -SPEED_LIMIT), SPEED_LIMIT)


def _split_overshoot(poly: tuple[float, float, float, float]) -> list[tuple[float, float, str]]:
    """Découpe ``[0, 1]`` aux points où ``|poly|`` dépasse la borne : ``(u0, u1, 'poly' | 'high' | 'low')``."""
    knots = {0.0, 1.0}
    for bound in (SPEED_LIMIT, -SPEED_LIMIT):
        shifted = (poly[0] - bound, poly[1], poly[2], poly[3])
        knots.update(r for r in real_roots(shifted, 0.0, 1.0))
    ordered = sorted(knots)
    parts: list[tuple[float, float, str]] = []
    for a, b in zip(ordered, ordered[1:]):
        if b - a <= _ROOT_MERGE:
            continue
        value = _peval(poly, 0.5 * (a + b))
        kind = "high" if value > SPEED_LIMIT else "low" if value < -SPEED_LIMIT else "poly"
        if parts and parts[-1][2] == kind and kind != "poly":
            parts[-1] = (parts[-1][0], b, kind)
        else:
            parts.append((a, b, kind))
    return parts


def _build_segments(keyframes: tuple["Keyframe", ...], direction: int, static_speed: float) -> list[_Seg]:
    """Segments de ``v(t)`` sur ``[0, ∞)`` : plat avant le premier keyframe et après le dernier."""
    from .animation import AnimationCurve, ValueKind

    sign = float(direction)
    if not keyframes:
        return [_Seg(0.0, math.inf, sign * static_speed, None)]
    curve = AnimationCurve(keyframes, ValueKind.FLOAT)
    first, last = curve.keyframes[0], curve.keyframes[-1]
    segments: list[_Seg] = []
    if first.time_seconds > 0.0:
        segments.append(_Seg(0.0, first.time_seconds, sign * _clamp_speed(float(first.value)), None))
    for segment in curve.segments:
        poly = segment.polynomials[0]
        for u0, u1, kind in _split_overshoot(poly):
            t0 = segment.t0 + u0 * segment.span
            t1 = segment.t0 + u1 * segment.span if u1 < 1.0 else segment.t1
            if kind == "poly":
                signed = (sign * poly[0], sign * poly[1], sign * poly[2], sign * poly[3])
                segments.append(_Seg(t0, t1, 0.0, signed, ts0=segment.t0, span=segment.span))
            else:
                segments.append(_Seg(t0, t1, sign * (SPEED_LIMIT if kind == "high" else -SPEED_LIMIT), None))
    segments.append(_Seg(last.time_seconds, math.inf, sign * _clamp_speed(float(last.value)), None))
    return segments


class PiecewiseTimeMap(TimeMap):
    """Mapping d'une courbe de vitesse : intégrale exacte, retournements, durée dérivée.

    Immuable après construction. Voir le docstring du module pour les définitions.
    """

    def __init__(
        self,
        lo: float,
        hi: float,
        *,
        reverse: bool = False,
        static_speed: float = 1.0,
        keyframes: tuple["Keyframe", ...] = (),
        anchor: float | None = None,
        fixed_duration: float | None = None,
    ) -> None:
        self.lo, self.hi = float(lo), float(hi)
        self.reverse = bool(reverse)
        self.static_speed = float(static_speed)
        self.keyframes = tuple(keyframes)
        self.fixed_duration = fixed_duration
        self.direction = -1 if reverse else 1
        self.anchor = float(anchor) if anchor is not None else (self.hi if reverse else self.lo)
        self._segments = _build_segments(self.keyframes, self.direction, self.static_speed)
        m = self.anchor
        for seg in self._segments:
            seg.anchor(m)
            if math.isfinite(seg.t1):
                m = seg.m(seg.t1)
        natural = self._natural_duration()
        self.underdetermined = not math.isfinite(natural) and fixed_duration is None
        if not math.isfinite(natural):
            natural = fixed_duration if fixed_duration is not None else max(self.keyframes[-1].time_seconds if self.keyframes else 0.0, 1.0)
        elif fixed_duration is not None:
            natural = min(natural, float(fixed_duration))
        self.duration = max(natural, MIN_DURATION)
        self._starts = [seg.t0 for seg in self._segments]
        self._runs: tuple[Run, ...] | None = None

    # -- durée : première sortie vers l'extérieur de la fenêtre --------------------------------------

    def _natural_duration(self) -> float:
        """Premier instant où ``M`` **franchit** une borne de la fenêtre vers l'extérieur (``inf`` s'il n'y en a pas).

        Toucher une borne n'est pas la franchir : un retournement qui frôle ``hi`` (vitesse nulle au sommet) puis redescend
        n'épuise pas la source. On décide donc au *début* de chaque intervalle monotone (la source est déjà à la borne et
        continue vers l'extérieur), et à l'intérieur d'un intervalle seulement si la borne est strictement dépassée.
        """
        tol = 1e-10 * (1.0 + abs(self.hi) + abs(self.lo))
        for seg in self._segments:
            cuts = [seg.t0, *seg.turning_times(seg.t0, seg.t1), seg.t1]
            for ta, tb in zip(cuts, cuts[1:]):
                ma = seg.m(ta)
                probe = ta + 1.0 if math.isinf(tb) else 0.5 * (ta + tb)
                v = seg.v(probe)
                if v > _REST:  # croissant : vers ``hi``
                    if ma >= self.hi - tol:
                        return ta
                    if math.isinf(tb):
                        return ta + (self.hi - ma) / v
                    if seg.m(tb) > self.hi + tol:
                        return self._cross(seg, ta, tb, self.hi)
                elif v < -_REST:  # décroissant : vers ``lo``
                    if ma <= self.lo + tol:
                        return ta
                    if math.isinf(tb):
                        return ta + (self.lo - ma) / v
                    if seg.m(tb) < self.lo - tol:
                        return self._cross(seg, ta, tb, self.lo)
        return math.inf

    @staticmethod
    def _cross(seg: _Seg, ta: float, tb: float, target: float) -> float:
        """Instant de ``[ta, tb]`` où ``M`` (monotone) atteint ``target`` : dichotomie à la précision machine."""
        fa = seg.m(ta) - target
        a, b = ta, tb
        for _ in range(120):
            mid = 0.5 * (a + b)
            fm = seg.m(mid) - target
            if fm == 0.0:
                return mid
            if (fa < 0.0) == (fm < 0.0):
                a, fa = mid, fm
            else:
                b = mid
            if b - a <= 1e-16 * max(1.0, abs(b)):
                break
        return 0.5 * (a + b)

    # -- évaluation -----------------------------------------------------------------------------------

    def _segment_at(self, t: float) -> _Seg:
        index = bisect.bisect_right(self._starts, t) - 1
        return self._segments[max(0, index)]

    def source_time(self, t: float) -> float:
        t = min(max(float(t), 0.0), self.duration)
        return min(max(self._segment_at(t).m(t), self.lo), self.hi)

    def speed_at(self, t: float) -> float:
        t = min(max(float(t), 0.0), self.duration)
        return self._segment_at(t).v(t)

    # -- structure ------------------------------------------------------------------------------------

    def _knots(self) -> list[float]:
        knots = {0.0, self.duration}
        for seg in self._segments:
            if 0.0 < seg.t0 < self.duration:
                knots.add(seg.t0)
            end = min(seg.t1, self.duration)
            if seg.t1 > 0.0 and end > seg.t0:
                if 0.0 < end < self.duration:
                    knots.add(end)
                knots.update(seg.turning_times(seg.t0, end))
        return sorted(k for k in knots if 0.0 <= k <= self.duration)

    def runs(self) -> tuple[Run, ...]:
        if self._runs is not None:
            return self._runs
        knots = self._knots()
        runs: list[Run] = []
        for a, b in zip(knots, knots[1:]):
            if b - a <= _ROOT_MERGE:
                continue
            v = self.speed_at(0.5 * (a + b))
            kind = RunKind.FORWARD if v > _REST else RunKind.BACKWARD if v < -_REST else RunKind.HOLD
            if runs and runs[-1].kind is kind:
                previous = runs.pop()
                runs.append(Run(previous.t0, b, previous.s0, self.source_time(b), kind))
            else:
                runs.append(Run(a, b, self.source_time(a), self.source_time(b), kind))
        if not runs:
            runs.append(Run(0.0, self.duration, self.source_time(0.0), self.source_time(self.duration), RunKind.HOLD))
        self._runs = tuple(runs)
        return self._runs

    def _solve_in_run(self, run: Run, s: float) -> float:
        """Instant de ``run`` (monotone) où ``M = s`` : dichotomie à la précision machine."""
        a, b = run.t0, run.t1
        fa = self._segment_at(a).m(a) - s
        if abs(fa) <= 1e-9:
            return a
        for _ in range(120):
            mid = 0.5 * (a + b)
            fm = self._segment_at(mid).m(mid) - s
            if fm == 0.0:
                return mid
            if (fa < 0.0) == (fm < 0.0):
                a, fa = mid, fm
            else:
                b = mid
            if b - a <= 1e-15 * max(1.0, abs(b)):
                break
        return 0.5 * (a + b)

    def times_at_source(self, s: float) -> list[float]:
        found: list[float] = []
        slack = 1e-9
        for run in self.runs():
            low, high = sorted((run.s0, run.s1))
            if run.kind is RunKind.HOLD:
                if abs(run.s0 - s) <= slack:
                    found.append(run.t0)
                continue
            if low - slack <= s <= high + slack:
                found.append(self._solve_in_run(run, s))
        return _dedupe(found)

    def timeline_intervals_of(self, low: float, high: float) -> list[tuple[float, float]]:
        found: list[tuple[float, float]] = []
        slack = 1e-9
        for run in self.runs():
            if run.kind is RunKind.HOLD:
                if low - slack <= run.s0 <= high + slack:
                    found.append((run.t0, run.t1))
                continue
            lo_s, hi_s = sorted((run.s0, run.s1))
            a, b = max(low, lo_s), min(high, hi_s)
            if a > b:
                continue
            ta, tb = self._solve_in_run(run, a), self._solve_in_run(run, b)
            found.append((min(ta, tb), max(ta, tb)))
        found.sort()
        merged: list[tuple[float, float]] = []
        for start, end in found:
            if merged and start <= merged[-1][1] + 1e-9:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def __repr__(self) -> str:
        keys = tuple(
            (k.time_seconds, k.value, k.interpolation.value, k.in_slope, k.out_slope) for k in self.keyframes
        )
        return (
            f"PiecewiseTimeMap(lo={self.lo!r}, hi={self.hi!r}, reverse={self.reverse}, static={self.static_speed!r}, "
            f"anchor={self.anchor!r}, fixed={self.fixed_duration!r}, keys={keys!r})"
        )


# ---------------------------------------------------------------------------
# Construction depuis un clip
# ---------------------------------------------------------------------------


def speed_keyframes_of(animation) -> tuple["Keyframe", ...]:
    """Keyframes de vitesse d'une liste d'animation (``Clip.animation``), triés, un par instant (le dernier gagne)."""
    keys = [k for k in animation if getattr(k, "property_name", "") == SPEED_PROPERTY]
    if not keys:
        return ()
    from .animation import AnimationCurve

    return AnimationCurve(keys).keyframes


def speed_keyframes(clip: "Clip") -> tuple["Keyframe", ...]:
    """Keyframes de vitesse d'un clip, triés, un par instant (le dernier fourni gagne)."""
    return speed_keyframes_of(clip.animation)


def has_speed_curve(clip: "Clip") -> bool:
    return any(getattr(k, "property_name", "") == SPEED_PROPERTY for k in clip.animation)


@lru_cache(maxsize=2048)
def _compiled(
    lo: float, hi: float, reverse: bool, static_speed: float, keyframes: tuple, anchor: float | None, fixed: float | None
) -> PiecewiseTimeMap:
    return PiecewiseTimeMap(
        lo, hi, reverse=reverse, static_speed=static_speed, keyframes=keyframes, anchor=anchor, fixed_duration=fixed
    )


def time_map_for(
    source_in: float,
    source_out: float,
    remapping,
    keyframes: tuple["Keyframe", ...] = (),
) -> TimeMap:
    """Le mapping d'un clip : constant (formules historiques) sans courbe ni ancre ni durée imposée, sinon par morceaux."""
    if remapping.freeze_mode == FreezeFrameMode.FREEZE:
        return ConstantTimeMap(
            source_in, source_out, hold=True, hold_source=remapping.freeze_source_time,
            hold_duration=remapping.freeze_duration,
        )
    anchor = getattr(remapping, "anchor", None)
    fixed = getattr(remapping, "duration", None)
    if not keyframes and anchor is None and fixed is None:
        return ConstantTimeMap(source_in, source_out, remapping.speed, remapping.reverse)
    return _compiled(
        float(source_in), float(source_out), bool(remapping.reverse), float(remapping.speed), tuple(keyframes),
        None if anchor is None else float(anchor), None if fixed is None else float(fixed),
    )


def time_map_for_clip(clip: "Clip") -> TimeMap:
    """Le mapping d'un clip tel que son projet le décrit (c'est la seule porte d'entrée pour les consommateurs)."""
    return time_map_for(clip.source_in, clip.source_out, clip.time_remapping, speed_keyframes(clip))


__all__ = [
    "DEFAULT_LINEARIZATION_TOLERANCE",
    "MIN_DURATION",
    "SPEED_LIMIT",
    "SPEED_PROPERTY",
    "ConstantTimeMap",
    "Piece",
    "PiecewiseTimeMap",
    "Run",
    "RunKind",
    "TimeMap",
    "has_speed_curve",
    "real_roots",
    "speed_keyframes",
    "speed_keyframes_of",
    "time_map_for",
    "time_map_for_clip",
]
