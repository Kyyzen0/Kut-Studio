"""Géométrie du tracking : temps, repères, mouvement multi-points, stabilisation.

Module **pur** (ni Qt, ni FFmpeg, ni numpy). Il transforme des données
suivies (:mod:`core.tracking_model`) en matrices affines au format de
:mod:`core.mograph_scene` (``(a, b, c, d, e, f)``, pixels, ``y`` vers le
bas, angle positif = sens horaire).

Repères d'un clip vidéo
-----------------------

::

    pixels source ──fit──▶ calque (W × H) ──C (stabilisation)──▶ ──L(transform)──▶ cadre

- ``fit`` reproduit ``scale=W:H:force_original_aspect_ratio=decrease`` puis
  ``pad`` de l'export : le média est centré dans un calque de la taille du
  cadre (c'est aussi l'espace des masques d'un clip vidéo) ;
- ``C`` est la correction de stabilisation (similitude, identité sans
  stabilisation) ;
- ``L`` est :func:`core.mograph_scene.local_matrix` du transform évalué.

Mouvement
---------

Pour une image source ``i``, :func:`motion_series` ajuste la similitude
``A_i`` qui envoie les points de l'image de référence ``r`` sur ceux de
l'image ``i`` (un point : translation ; deux ou plus : translation, rotation
et échelle aux moindres carrés, comme Umeyama sans réflexion) ::

    A_i(x) = c_i + k_i · R(θ_i) · (x − g)

``g`` est le barycentre des points à l'image de référence, ``c_i`` celui à
l'image ``i``. Les paramètres ``(c, θ, ln k)`` sont interpolés
linéairement entre images valides, et lissés pour la stabilisation.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from .mograph_scene import (
    IDENTITY,
    Matrix,
    local_matrix,
    mat_apply,
    mat_invert,
    mat_mul,
    mat_rotate,
    mat_scale,
    mat_translate,
)
from .tracking_model import BorderMode, Stabilization, StabilizationMode, TrackData

# ---------------------------------------------------------------------------
# Temps
# ---------------------------------------------------------------------------


def source_time(clip, local_time: float) -> float:
    """Temps du média source montré à ``local_time`` (secondes locales au clip).

    Reproduit le remappage de l'export (vitesse, lecture inverse, arrêt sur
    image) **sans** lever hors des bornes : une liaison peut interroger un
    instant voisin (tête de lecture sur la dernière image, clip coupé).
    """
    remapping = getattr(clip, "time_remapping", None)
    source_in = float(getattr(clip, "source_in", 0.0))
    source_out = float(getattr(clip, "source_out", source_in))
    if remapping is None:
        return source_in + float(local_time)
    if getattr(remapping.freeze_mode, "value", remapping.freeze_mode) == "freeze":
        return float(remapping.freeze_source_time)
    relative = float(local_time) * float(remapping.speed or 1.0)
    if remapping.reverse:
        relative = (source_out - source_in) - relative
    return source_in + relative


def local_time_for_source(clip, seconds: float) -> float:
    """Inverse de :func:`source_time` (arrêt sur image : 0)."""
    remapping = getattr(clip, "time_remapping", None)
    source_in = float(getattr(clip, "source_in", 0.0))
    source_out = float(getattr(clip, "source_out", source_in))
    if remapping is None:
        return float(seconds) - source_in
    if getattr(remapping.freeze_mode, "value", remapping.freeze_mode) == "freeze":
        return 0.0
    relative = float(seconds) - source_in
    if remapping.reverse:
        relative = (source_out - source_in) - relative
    return relative / float(remapping.speed or 1.0)


def clip_source_indices(clip, rate: float) -> tuple[int, int]:
    """Images source ``(première, dernière)`` montrées par le clip."""
    if rate <= 0:
        return (0, -1)
    a = source_time(clip, 0.0)
    b = source_time(clip, max(0.0, float(clip.duration) - 1e-6))
    low, high = min(a, b), max(a, b)
    return (max(0, int(math.floor(low * rate + 1e-6))), max(0, int(math.floor(high * rate + 1e-6))))


# ---------------------------------------------------------------------------
# Repères
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitBox:
    """Placement du média dans le calque (``scale`` + ``pad`` de l'export)."""

    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    width: float
    height: float

    def to_layer(self, x: float, y: float) -> tuple[float, float]:
        return (self.offset_x + x * self.scale_x, self.offset_y + y * self.scale_y)

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.offset_x) / self.scale_x, (y - self.offset_y) / self.scale_y)

    @property
    def matrix(self) -> Matrix:
        """Pixels source → calque."""
        return (self.scale_x, 0.0, 0.0, self.scale_y, self.offset_x, self.offset_y)

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Zone de l'image dans le calque ``(x0, y0, x1, y1)``."""
        return (self.offset_x, self.offset_y, self.offset_x + self.width, self.offset_y + self.height)


def fit_box(media_width: int, media_height: int, canvas_width: int, canvas_height: int) -> FitBox:
    """Le média ``w × h`` adapté sans déformation dans le cadre, centré."""
    w, h = max(1, int(media_width)), max(1, int(media_height))
    cw, ch = max(1, int(canvas_width)), max(1, int(canvas_height))
    factor = min(cw / w, ch / h)
    # FFmpeg arrondit la taille réduite à l'entier, puis ``pad`` centre à l'entier.
    iw = max(1, min(cw, int(round(w * factor))))
    ih = max(1, min(ch, int(round(h * factor))))
    return FitBox(iw / w, ih / h, float((cw - iw) // 2), float((ch - ih) // 2), float(iw), float(ih))


def video_layer_matrix(values, canvas_width: float, canvas_height: float) -> Matrix:
    """Calque vidéo (taille du cadre) → cadre, comme l'export et le moniteur."""
    size = (float(canvas_width), float(canvas_height))
    return local_matrix(values, size, size, size, allow_skew=False)


# ---------------------------------------------------------------------------
# Similitudes
# ---------------------------------------------------------------------------


def similarity(cx: float, cy: float, theta_degrees: float, scale: float, gx: float, gy: float) -> Matrix:
    """``x ↦ c + scale·R(θ)·(x − g)``."""
    m = mat_translate(cx, cy)
    if theta_degrees:
        m = mat_mul(m, mat_rotate(theta_degrees))
    if scale != 1.0:
        m = mat_mul(m, mat_scale(scale, scale))
    return mat_mul(m, mat_translate(-gx, -gy))


def fit_similarity(
    source: Sequence[tuple[float, float]], target: Sequence[tuple[float, float]], *, allow_scale: bool = True
) -> tuple[float, float, float, float, float, float]:
    """Similitude aux moindres carrés ``source → target``.

    Retourne ``(cx, cy, θ°, k, gx, gy)`` : barycentres ``g`` (source) et
    ``c`` (cible), rotation et échelle. Avec un seul point : translation.
    """
    n = min(len(source), len(target))
    if n == 0:
        raise ValueError("Aucun point pour ajuster le mouvement.")
    gx = sum(p[0] for p in source[:n]) / n
    gy = sum(p[1] for p in source[:n]) / n
    cx = sum(p[0] for p in target[:n]) / n
    cy = sum(p[1] for p in target[:n]) / n
    if n == 1:
        return (cx, cy, 0.0, 1.0, gx, gy)
    dot = cross = norm = 0.0
    for (sx, sy), (tx, ty) in zip(source[:n], target[:n]):
        ax, ay = sx - gx, sy - gy
        bx, by = tx - cx, ty - cy
        dot += ax * bx + ay * by
        cross += ax * by - ay * bx
        norm += ax * ax + ay * ay
    if norm < 1e-9:
        return (cx, cy, 0.0, 1.0, gx, gy)
    theta = math.degrees(math.atan2(cross, dot))
    scale = math.hypot(dot, cross) / norm if allow_scale else 1.0
    if not math.isfinite(scale) or scale <= 1e-6:
        scale = 1.0
    return (cx, cy, theta, scale, gx, gy)


def robust_fit(
    source: Sequence[tuple[float, float]], target: Sequence[tuple[float, float]], *, allow_scale: bool = True,
    tolerance: float = 1.5,
) -> tuple[float, float, float, float, float, float]:
    """:func:`fit_similarity` qui écarte les points aberrants (trois points ou plus).

    Les moindres carrés répartissent l'erreur d'un point aberrant (de l'eau,
    un objet indépendant, un tracker qui glisse) sur tous les autres : on ne
    peut pas le repérer à son seul résidu. Si l'ajustement global laisse un
    écart supérieur à ``tolerance`` pixels, chaque **paire** de points (le
    modèle minimal d'une similitude) est essayée ; celle qui rassemble le plus
    de points à moins de ``tolerance`` l'emporte, et le modèle final est
    ajusté sur ces seuls points. Exhaustif, donc déterministe (pas de tirage).
    """
    n = min(len(source), len(target))
    fitted = fit_similarity(source, target, allow_scale=allow_scale)
    if n < 3:
        return fitted

    def residuals(model_parts) -> list[float]:
        model = similarity(*model_parts)
        return [math.dist(mat_apply(model, *a), b) for a, b in zip(source[:n], target[:n])]

    if max(residuals(fitted)) <= tolerance:
        return fitted
    best: tuple[int, float, list[int]] | None = None
    for i in range(n):
        for j in range(i + 1, n):
            parts = fit_similarity([source[i], source[j]], [target[i], target[j]], allow_scale=allow_scale)
            errors = residuals(parts)
            inliers = [index for index, error in enumerate(errors) if error <= tolerance]
            score = (len(inliers), -sum(errors[index] for index in inliers))
            if best is None or score > best[:2]:
                best = (score[0], score[1], inliers)
    if best is None or best[0] < 2:
        return fitted
    chosen = best[2]
    return fit_similarity([source[i] for i in chosen], [target[i] for i in chosen], allow_scale=allow_scale)


def similarity_parts(m: Matrix) -> tuple[float, float]:
    """``(θ°, k)`` de la partie linéaire d'une similitude."""
    a, b = m[0], m[1]
    return (math.degrees(math.atan2(b, a)), math.hypot(a, b))


def matrix_is_finite(m: Matrix) -> bool:
    return all(math.isfinite(v) for v in m)


# ---------------------------------------------------------------------------
# Mouvement multi-points
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionSeries:
    """Mouvement par image source, relatif à l'image de référence (repère calque).

    ``params[i - first]`` = ``(cx, cy, θ°, ln k)`` ; ``g`` = barycentre de
    référence. ``measured`` indique les images réellement mesurées (les
    autres sont interpolées ou tenues aux extrémités).
    """

    first: int
    params: tuple[tuple[float, float, float, float], ...]
    measured: tuple[bool, ...]
    g: tuple[float, float]
    reference_index: int
    rate: float

    @property
    def last(self) -> int:
        return self.first + len(self.params) - 1

    @property
    def is_empty(self) -> bool:
        return not self.params

    def params_at(self, index: float) -> tuple[float, float, float, float]:
        if not self.params:
            return (self.g[0], self.g[1], 0.0, 0.0)
        position = min(max(index - self.first, 0.0), len(self.params) - 1.0)
        low = int(math.floor(position))
        high = min(low + 1, len(self.params) - 1)
        u = position - low
        a, b = self.params[low], self.params[high]
        return tuple(x + (y - x) * u for x, y in zip(a, b))  # type: ignore[return-value]

    def matrix_at(self, index: float) -> Matrix:
        cx, cy, theta, log_k = self.params_at(index)
        return similarity(cx, cy, theta, math.exp(log_k), *self.g)

    def matrix_at_time(self, source_seconds: float) -> Matrix:
        return self.matrix_at(source_seconds * self.rate)


_SERIES_CACHE: OrderedDict = OrderedDict()
_CACHE_SIZE = 64


def _cached(key, compute):
    value = _SERIES_CACHE.get(key)
    if value is not None:
        _SERIES_CACHE.move_to_end(key)
        return value
    value = compute()
    _SERIES_CACHE[key] = value
    if len(_SERIES_CACHE) > _CACHE_SIZE:
        _SERIES_CACHE.popitem(last=False)
    return value


def motion_series(
    datas: Sequence[TrackData],
    fit: FitBox,
    *,
    reference_index: int,
    media_size: tuple[int, int],
    rotation: bool = True,
    scale: bool = True,
) -> MotionSeries:
    """Mouvement des trackers ``datas`` (même clip) dans le repère calque.

    Une image n'est mesurée que si **tous** les trackers y sont valides
    (sinon un sous-ensemble de points ferait sauter le barycentre) ; les
    autres sont interpolées. L'image de référence est ramenée à l'image
    mesurée la plus proche si elle-même ne l'est pas.
    """
    key = ("series", tuple(datas), fit, int(reference_index), tuple(media_size), bool(rotation), bool(scale))
    return _cached(key, lambda: _motion_series(datas, fit, reference_index, media_size, rotation, scale))


def _layer_points(datas, index, fit, media_size) -> dict[int, tuple[float, float]]:
    """Points valides à l'image ``index`` (repère calque), par numéro de tracker."""
    points = {}
    for number, data in enumerate(datas):
        if data.status_at(index) in _VALID:
            sample = data.sample(index)
            fx, fy = data.scaled_to(*media_size)
            points[number] = fit.to_layer(sample.x * fx, sample.y * fy)
    return points


def _motion_series(datas, fit, reference_index, media_size, rotation, scale) -> MotionSeries:
    """Une image est mesurée dès qu'assez de trackers y sont valides.

    Le modèle (similitude, ajustement robuste) décrit le mouvement de tout le
    plan : l'estimer sur les seuls trackers valides à une image ne fait pas
    sauter le résultat quand un tracker est perdu ou masqué. Il est exprimé
    autour d'un pivot fixe, le barycentre des points à l'image de référence ;
    sans rotation / échelle demandées, on garde le déplacement de ce pivot.
    """
    rate = next((d.rate for d in datas if d.rate > 0), 0.0)
    empty = MotionSeries(0, (), (), (0.0, 0.0), reference_index, rate)
    ranges = [d.valid_range() for d in datas]
    usable = [r for r in ranges if r is not None]
    if not usable:
        return empty
    low = min(r[0] for r in usable)
    high = max(r[1] for r in usable)
    needed = 2 if (rotation or scale) and len(usable) >= 2 else 1
    maps = {index: _layer_points(datas, index, fit, media_size) for index in range(low, high + 1)}
    candidates = [index for index, points in maps.items() if len(points) >= needed]
    if not candidates:
        return empty
    reference = min(candidates, key=lambda i: (abs(i - reference_index), -len(maps[i]), i))
    ref_map = maps[reference]
    gx = sum(p[0] for p in ref_map.values()) / len(ref_map)
    gy = sum(p[1] for p in ref_map.values()) / len(ref_map)
    excluded = _inconsistent_trackers(maps, candidates, ref_map, needed)
    raw: dict[int, tuple[float, float, float, float]] = {}
    previous_theta = 0.0
    for index in candidates:
        common = [number for number in maps[index] if number in ref_map]
        kept = [number for number in common if number not in excluded]
        if len(kept) >= needed:
            common = kept
        if len(common) < needed:
            continue
        cx, cy, theta, k, fx, fy = fit_similarity(
            [ref_map[n] for n in common], [maps[index][n] for n in common], allow_scale=True,
        )
        # Même modèle, exprimé autour du pivot fixe : c = A(g).
        cx, cy = mat_apply(similarity(cx, cy, theta, k, fx, fy), gx, gy)
        if not rotation:
            theta = 0.0
        # Angle déroulé : pas de saut de 360° entre deux images.
        theta = previous_theta + ((theta - previous_theta + 180.0) % 360.0 - 180.0)
        previous_theta = theta
        log_k = math.log(k) if scale and k > 0 else 0.0
        if not all(math.isfinite(v) for v in (cx, cy, theta, log_k)):
            continue
        raw[index] = (cx, cy, theta, log_k)
    if not raw:
        return empty
    first, last = min(raw), max(raw)
    params = _fill(raw, first, last)
    measured = tuple(i in raw for i in range(first, last + 1))
    return MotionSeries(first, tuple(params), measured, (gx, gy), reference, rate)


def _inconsistent_trackers(maps, candidates, ref_map, needed, tolerance: float = 2.0) -> set[int]:
    """Trackers qui ne suivent pas le mouvement commun, **sur toute la durée**.

    Un ajustement par image sur tous les points donne le résidu de chaque
    tracker ; un tracker dont le résidu médian dépasse nettement celui des
    autres (de l'eau, un passant, un tracker qui glisse) est écarté pour tout
    le plan. Décider une fois pour toutes évite que le modèle bascule d'un
    sous-ensemble à l'autre d'une image à la suivante (ce qui créerait de la
    gigue sur un décor avec parallaxe, où aucun point ne s'accorde exactement).
    """
    numbers = sorted(ref_map)
    if len(numbers) < max(3, needed + 1):
        return set()
    residuals: dict[int, list[float]] = {number: [] for number in numbers}
    for index in candidates:
        common = [number for number in maps[index] if number in ref_map]
        if len(common) < 3:
            continue
        parts = fit_similarity([ref_map[n] for n in common], [maps[index][n] for n in common])
        model = similarity(*parts)
        for number in common:
            residuals[number].append(math.dist(mat_apply(model, *ref_map[number]), maps[index][number]))
    medians = {n: sorted(v)[len(v) // 2] for n, v in residuals.items() if v}
    if len(medians) < 3:
        return set()
    typical = sorted(medians.values())[len(medians) // 2]
    excluded = {n for n, value in medians.items() if value > max(tolerance, 3.0 * typical)}
    if len(numbers) - len(excluded) < needed:
        return set()
    return excluded


_VALID = frozenset({1, 2, 4})  # SampleStatus.TRACKED / UNCERTAIN / MANUAL


def _fill(raw: dict[int, tuple], first: int, last: int) -> list[tuple]:
    """Complète les images non mesurées par interpolation linéaire."""
    known = sorted(raw)
    result = []
    position = 0
    for index in range(first, last + 1):
        if index in raw:
            result.append(raw[index])
            continue
        while position + 1 < len(known) and known[position + 1] < index:
            position += 1
        a, b = known[position], known[min(position + 1, len(known) - 1)]
        u = (index - a) / (b - a) if b != a else 0.0
        result.append(tuple(x + (y - x) * u for x, y in zip(raw[a], raw[b])))
    return result


# ---------------------------------------------------------------------------
# Lissage
# ---------------------------------------------------------------------------


def _box_pass(values: list[float], radius: int) -> list[float]:
    """Moyenne glissante normalisée aux bords (somme préfixe, O(n))."""
    n = len(values)
    if radius <= 0 or n < 2:
        return list(values)
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    result = []
    for i in range(n):
        lo, hi = max(0, i - radius), min(n - 1, i + radius)
        result.append((prefix[hi + 1] - prefix[lo]) / (hi - lo + 1))
    return result


def gaussian_smooth(values: Sequence[float], sigma: float) -> list[float]:
    """Lissage quasi gaussien : trois moyennes glissantes (écart-type ``sigma`` images)."""
    data = [float(v) for v in values]
    if sigma <= 0.0 or len(data) < 3:
        return data
    # Trois boîtes de largeur w ont une variance 3·(w² − 1)/12.
    width = max(1.0, math.sqrt(4.0 * sigma * sigma + 1.0))
    radius = max(1, int(round((width - 1.0) / 2.0)))
    for _ in range(3):
        data = _box_pass(data, radius)
    return data


# ---------------------------------------------------------------------------
# Stabilisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StabilizationResult:
    """Correction par image source (repère calque), bords et zoom compris."""

    first: int
    corrections: tuple[Matrix, ...]
    zoom: float
    crop_rect: tuple[float, float, float, float] | None
    """Zone stable ``(x0, y0, x1, y1)`` après correction (mode recadrage)."""
    rate: float
    valid: bool
    message: str = ""

    def correction_at(self, index: float) -> Matrix:
        if not self.corrections:
            return IDENTITY
        position = min(max(index - self.first, 0.0), len(self.corrections) - 1.0)
        low = int(math.floor(position))
        high = min(low + 1, len(self.corrections) - 1)
        u = position - low
        if u <= 1e-9:
            return self.corrections[low]
        return tuple(a + (b - a) * u for a, b in zip(self.corrections[low], self.corrections[high]))  # type: ignore[return-value]

    def correction_at_time(self, source_seconds: float) -> Matrix:
        return self.correction_at(source_seconds * self.rate)


def stabilization_result(
    stabilization: Stabilization,
    datas: Sequence[TrackData],
    fit: FitBox,
    *,
    media_size: tuple[int, int],
    shown: tuple[int, int],
) -> StabilizationResult:
    """Correction ``C_i = S_i ∘ A_i⁻¹`` (``S`` = mouvement lissé), avec bords.

    ``shown`` : images source montrées par le clip ; le zoom et le cadre de
    recadrage sont calculés sur elles seules (un long rush coupé court ne
    paie pas pour ses parties invisibles).
    """
    key = ("stab", stabilization, tuple(datas), fit, tuple(media_size), tuple(shown))
    return _cached(key, lambda: _stabilization(stabilization, datas, fit, media_size, shown))


def _stabilization(stabilization, datas, fit, media_size, shown) -> StabilizationResult:
    rate = next((d.rate for d in datas if d.rate > 0), 0.0)
    mode = stabilization.mode
    if mode != StabilizationMode.POSITION and len(datas) < 2:
        mode = StabilizationMode.POSITION  # rotation / échelle exigent deux points
    series = motion_series(
        datas, fit, reference_index=stabilization.reference_index, media_size=media_size,
        rotation=mode != StabilizationMode.POSITION,
        scale=mode == StabilizationMode.POSITION_ROTATION_SCALE,
    )
    if series.is_empty:
        return StabilizationResult(0, (), 1.0, None, rate, False, "tracking.stab.no_data")
    columns = list(zip(*series.params))
    sigma = stabilization.sigma
    if sigma is None:
        gx, gy = series.g
        smoothed = [(gx, gy, 0.0, 0.0)] * len(series.params)
    else:
        smoothed = list(zip(*(gaussian_smooth(column, sigma) for column in columns)))
    corrections = []
    gx, gy = series.g
    for raw, smooth in zip(series.params, smoothed):
        actual = similarity(raw[0], raw[1], raw[2], math.exp(raw[3]), gx, gy)
        wanted = similarity(smooth[0], smooth[1], smooth[2], math.exp(smooth[3]), gx, gy)
        try:
            correction = mat_mul(wanted, mat_invert(actual))
        except ValueError:
            correction = IDENTITY
        corrections.append(correction if matrix_is_finite(correction) else IDENTITY)
    zoom = 1.0
    crop = None
    message = ""
    if stabilization.borders != BorderMode.BLACK:
        first_shown = max(series.first, shown[0])
        last_shown = min(series.last, shown[1])
        if last_shown < first_shown:
            first_shown, last_shown = series.first, series.last
        quads = [
            [mat_apply(corrections[i - series.first], x, y) for x, y in _rect_corners(fit.rect)]
            for i in range(first_shown, last_shown + 1)
        ]
        factor = inscribed_factor(quads, fit.rect)
        if factor <= 0.05:
            message = "tracking.stab.too_strong"
            factor = 0.05
        x0, y0, x1, y1 = fit.rect
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        hw, hh = (x1 - x0) / 2.0 * factor, (y1 - y0) / 2.0 * factor
        crop = (cx - hw, cy - hh, cx + hw, cy + hh)
        if stabilization.borders == BorderMode.ZOOM:
            zoom = 1.0 / factor
            scale_about = mat_mul(mat_translate(cx, cy), mat_mul(mat_scale(zoom, zoom), mat_translate(-cx, -cy)))
            corrections = [mat_mul(scale_about, c) for c in corrections]
            crop = None
    return StabilizationResult(series.first, tuple(corrections), zoom, crop, rate, True, message)


def _rect_corners(rect):
    x0, y0, x1, y1 = rect
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def inscribed_factor(quads: Sequence[Sequence[tuple[float, float]]], rect: tuple[float, float, float, float]) -> float:
    """Plus grand facteur ``f`` tel que ``rect`` réduit de ``f`` autour de son centre
    tienne dans **chaque** quadrilatère convexe de ``quads``."""
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    best = 1.0
    for quad in quads:
        area = 0.0
        for i in range(4):
            ax, ay = quad[i]
            bx, by = quad[(i + 1) % 4]
            area += ax * by - bx * ay
        orientation = 1.0 if area >= 0 else -1.0
        for i in range(4):
            ax, ay = quad[i]
            bx, by = quad[(i + 1) % 4]
            # Normale intérieure de l'arête (a → b).
            nx, ny = -(by - ay) * orientation, (bx - ax) * orientation
            length = math.hypot(nx, ny)
            if length < 1e-12:
                continue
            nx, ny = nx / length, ny / length
            margin = (cx - ax) * nx + (cy - ay) * ny  # distance du centre à l'arête (> 0 = dedans)
            reach = abs(nx) * hw + abs(ny) * hh
            if reach <= 1e-12:
                continue
            best = min(best, margin / reach)
    return max(0.0, best)


def crop_mask_values(correction: Matrix, crop: tuple[float, float, float, float], canvas: tuple[float, float]):
    """Masque rectangle (espace calque, avant correction) qui montre ``crop`` après correction.

    Retourne ``(position_x, position_y, width, height, rotation)`` en
    fractions du calque, comme :class:`core.compositing.Mask`.
    """
    width, height = canvas
    try:
        inverse = mat_invert(correction)
    except ValueError:
        inverse = IDENTITY
    x0, y0, x1, y1 = crop
    cx, cy = mat_apply(inverse, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    theta, k = similarity_parts(inverse)
    k = k if k > 1e-9 else 1.0
    return (
        cx / width, cy / height, (x1 - x0) * k / width, (y1 - y0) * k / height, theta,
    )


__all__ = [
    "FitBox", "MotionSeries", "StabilizationResult", "clip_source_indices", "crop_mask_values",
    "fit_box", "fit_similarity", "gaussian_smooth", "inscribed_factor", "local_time_for_source",
    "matrix_is_finite", "motion_series", "robust_fit", "similarity", "similarity_parts", "source_time",
    "stabilization_result", "video_layer_matrix",
]
