"""Application du tracking aux propriétés : liaisons dynamiques, stabilisation, bake.

Un seul moteur d'animation
--------------------------

Une liaison (:class:`core.tracking_model.TrackLink`) et une stabilisation
ne stockent **aucune** image-clé. Au moment de construire un plan de rendu
(:func:`core.render_plan.build_render_plan`), :func:`effective_clip_state`
les **dérive** en images-clés linéaires ordinaires, une par image de la
séquence, simplifiées à une tolérance invisible (0,02 px, 0,01°). Tout le
reste — aperçu Qt, segments fidèles, export FFmpeg, poignées du viewer,
motion blur, empreintes de cache — lit ces images-clés comme n'importe
quelle animation : il n'y a pas de second moteur, et modifier le tracking
met aussitôt à jour toutes les cibles.

Le **bake** (:func:`baked_keyframes`) écrit ces mêmes images-clés dans le
clip et retire la liaison : les valeurs deviennent indépendantes.

Formules
--------

Le mouvement ``A`` (similitude, identité à l'image de référence) est mesuré
dans le repère calque du clip source (:mod:`core.tracking_motion`). Pour une
cible dans le cadre ::

    A_cadre(t) = M_S(t) · A_calque(t) · M_S(t_réf)⁻¹

(``M_S`` = calque → cadre du clip source, stabilisation comprise). La
cible garde ses propres valeurs ``U(t)`` (statiques ou animées) et le
mouvement s'y **ajoute** : ``L(V) = A · L(U)`` — la position est l'image
du point d'ancrage, rotation et échelle s'additionnent / se multiplient.
C'est exact quels que soient miroirs et échelles X/Y.

Un masque du clip source suit ``A_calque`` directement (il est attaché au
contenu) ; celui d'un autre clip ``X`` suit ``N_X⁻¹ · A_cadre · N_X``.

Stabilisation : ``L(V) = L(U) · C`` (correction à droite, dans le repère
calque). Exact pour une échelle uniforme ; avec ``scale_x ≠ scale_y`` et
une rotation compensée, la rotation est appliquée telle quelle
(approximation documentée).

Limites : un calque cible **parenté** reçoit le mouvement dans l'espace
de son parent (exact si le parent n'est ni tourné ni mis à l'échelle).
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .animation import AnimationCurve, InterpolationType, Keyframe, ValueKind, normalize_time
from .compositing import (
    MASK_PROPERTY_ORDER,
    MASK_PROPERTY_SPECS,
    Mask,
    MaskMode,
    MaskShape,
    evaluate_mask_at,
    mask_property_id,
)
from .mograph_scene import (
    IDENTITY,
    Matrix,
    local_matrix,
    mat_apply,
    mat_invert,
    mat_mul,
)
from .tracking_model import (
    SELF_CLIP,
    ClipTracking,
    TrackLink,
    TrackTarget,
)
from .tracking_motion import (
    FitBox,
    StabilizationResult,
    clip_source_indices,
    crop_mask_values,
    fit_box,
    local_time_for_source,
    matrix_is_finite,
    motion_series,
    similarity_parts,
    source_time,
    stabilization_result,
)
from .visual_effects import TRANSFORM_PROPERTIES, TransformKeyframe, evaluate_transform

LOGGER = logging.getLogger("kut_studio.tracking")

CROP_MASK_ID = "trackcrop"
"""Identifiant du masque de recadrage dérivé d'une stabilisation (jamais stocké)."""

TOLERANCE = {
    "position_x": 0.02,  # pixels (convertis en fraction du cadre)
    "position_y": 0.02,
    "anchor_x": 0.02,
    "anchor_y": 0.02,
    "rotation": 0.01,  # degrés
    "scale": 1e-5,
}

MAX_DERIVED_FRAMES = 200_000
"""Au-delà (≈ 1 h 50 à 30 i/s), l'échantillonnage passe à une image sur deux."""


# ---------------------------------------------------------------------------
# Contexte : une séquence
# ---------------------------------------------------------------------------


class TrackingContext:
    """Accès aux clips, médias et dimensions d'une séquence (construit à la demande)."""

    def __init__(self, project, sequence=None) -> None:
        self.project = project
        self.sequence = sequence if sequence is not None else project.active_sequence
        self.width = int(self.sequence.width)
        self.height = int(self.sequence.height)
        self.fps = float(self.sequence.fps) or 30.0
        self._assets = {asset.id: asset for asset in getattr(project, "media_assets", ())}
        self._clips: dict[str, object] | None = None
        self._tracks: dict[str, object] = {}
        # Mémo de la durée de vie du contexte (une construction de plan) :
        # stabilisation et évaluateurs de liaison ne sont résolus qu'une fois.
        self._stabilizations: dict = {}
        self._motions: dict = {}

    def clip(self, clip_id: str):
        if self._clips is None:
            self._clips = {}
            for track in self.sequence.tracks:
                for clip in track.clips:
                    self._clips[clip.id] = clip
                    self._tracks[clip.id] = track
        return self._clips.get(clip_id)

    def track_type(self, clip_id: str) -> str:
        self.clip(clip_id)
        track = self._tracks.get(clip_id)
        return getattr(track, "type", "")

    def media_size(self, clip) -> tuple[int, int] | None:
        asset = self._assets.get(getattr(clip, "asset_id", ""))
        if asset is None or asset.width <= 0 or asset.height <= 0:
            return None
        return (int(asset.width), int(asset.height))

    def media(self, clip):
        return self._assets.get(getattr(clip, "asset_id", ""))

    def fit(self, clip) -> FitBox | None:
        size = self.media_size(clip)
        if size is None:
            return None
        # Cadrage « remplir » : la fenêtre de base du clip (un pan animé n'est pas suivi par les repères du tracking).
        transform = getattr(clip, "transform", None)
        return fit_box(size[0], size[1], self.width, self.height, fill=bool(getattr(transform, "fill", False)),
                       pan_x=float(getattr(transform, "pan_x", 0.0)), pan_y=float(getattr(transform, "pan_y", 0.0)))

    def link_sources(self, target_clip, link: TrackLink) -> list:
        """Clips existants que ``link`` suit, dans l'ordre (le clip cible lui-même pour une liaison propre).

        Après la coupe de la source, il y en a plusieurs : la liaison suit celui qui couvre l'instant
        évalué (:class:`LinkMotion`). Un clip disparu est simplement absent de la liste ;
        :func:`link_issues` dit pourquoi.
        """
        if link.source_clip_id == SELF_CLIP:
            return [target_clip]
        return [clip for clip in (self.clip(clip_id) for clip_id in link.source_ids) if clip is not None]

    def tracker_datas(self, clip, tracker_ids: Iterable[str]):
        tracking = getattr(clip, "tracking", None)
        if tracking is None:
            return None
        datas = []
        for tracker_id in tracker_ids:
            tracker = tracking.tracker(tracker_id)
            if tracker is None:
                return None
            datas.append(tracker.data)
        return tuple(datas) or None

    # -- stabilisation --------------------------------------------------------------------

    def stabilization(self, clip) -> StabilizationResult | None:
        tracking: ClipTracking | None = getattr(clip, "tracking", None)
        stab = getattr(tracking, "stabilization", None)
        if stab is None or not stab.enabled:
            return None
        key = (clip.id, id(tracking), clip.source_in, clip.source_out, clip.time_remapping)
        if key not in self._stabilizations:
            self._stabilizations[key] = self._stabilization(clip, stab)
        return self._stabilizations[key]

    def _stabilization(self, clip, stab) -> StabilizationResult | None:
        fit = self.fit(clip)
        size = self.media_size(clip)
        datas = self.tracker_datas(clip, stab.tracker_ids)
        if fit is None or size is None or not datas:
            return None
        rate = next((d.rate for d in datas if d.rate > 0), 0.0)
        shown = clip_source_indices(clip, rate)
        if stab.shared_range is not None:
            # Une coupe a posé la plage du plan d'origine : les deux moitiés calculent le même zoom.
            shown = (min(shown[0], stab.shared_range[0]), max(shown[1], stab.shared_range[1]))
        result = stabilization_result(stab, datas, fit, media_size=size, shown=shown)
        return result if result.valid else None

    def correction(self, clip, local_time: float) -> Matrix:
        result = self.stabilization(clip)
        if result is None:
            return IDENTITY
        return result.correction_at_time(source_time(clip, local_time))

    # -- repères ---------------------------------------------------------------------------

    def layer_matrix(self, clip, local_time: float, *, include_correction: bool = True) -> Matrix:
        """Calque → cadre du clip (transform saisi, stabilisation comprise)."""
        local = max(0.0, min(float(clip.duration), float(local_time)))
        values = evaluate_transform(clip.transform, clip.transform_keyframes, local, clip.duration)
        graphic = getattr(clip, "graphic", None)
        canvas = (float(self.width), float(self.height))
        if graphic is None:
            m = local_matrix(values, canvas, canvas, canvas, allow_skew=False)
            if include_correction:
                m = mat_mul(m, self.correction(clip, local))
            return m
        box = (float(getattr(graphic, "width", self.width)), float(getattr(graphic, "height", self.height)))
        return local_matrix(values, box, canvas, canvas, layout=graphic.layout)

    def source_layer_point(self, clip, x: float, y: float, data) -> tuple[float, float] | None:
        """Pixels du média (repère de ``data``) → repère calque du clip."""
        fit = self.fit(clip)
        size = self.media_size(clip)
        if fit is None or size is None:
            return None
        fx, fy = data.scaled_to(*size)
        return fit.to_layer(x * fx, y * fy)

    def tracker_canvas_point(self, clip, tracker, timeline_time: float) -> tuple[float, float] | None:
        """Position du tracker dans le cadre à ``timeline_time`` (viewer)."""
        local = float(timeline_time) - float(clip.timeline_start)
        position = tracker.data.position_at_time(source_time(clip, local))
        if position is None:
            return None
        point = self.source_layer_point(clip, position[0], position[1], tracker.data)
        if point is None:
            return None
        return mat_apply(self.layer_matrix(clip, local), *point)

    # -- mouvement d'une liaison ------------------------------------------------------------

    def link_motion(self, target_clip, link: TrackLink, timeline_time: float, *, space: str) -> Matrix | None:
        """Mouvement ``A`` de ``link`` à ``timeline_time`` dans l'espace demandé.

        ``space`` : ``"layer"`` (repère calque du clip source), ``"canvas"``,
        ou ``"target"`` (repère calque du clip cible).
        """
        key = (id(target_clip), link, space)
        motion = self._motions.get(key)
        if motion is None:
            motion = self._motions[key] = LinkMotion(self, target_clip, link, space)
        return motion.at(timeline_time)

    def is_static(self, clip) -> bool:
        """Repère calque → cadre constant : ni image-clé de transform, ni stabilisation."""
        return not clip.transform_keyframes and (
            getattr(clip, "graphic", None) is not None or self.stabilization(clip) is None
        )


_TIME_EPSILON = 1e-9
"""Tolérance sur les instants de la timeline (un instant qui tombe 1e-12 avant une coupe est après)."""


class _SourcePart:
    """Mouvement d'une liaison mesuré sur **un** clip : la source entière, ou l'une de ses moitiés coupées.

    Série de mouvement et repères du clip sont calculés à la construction ; l'évaluation par image
    n'est plus qu'une interpolation et deux ou trois produits de matrices.
    """

    __slots__ = ("source", "series", "start", "end", "duration", "layer_only", "source_matrix")

    def __init__(self, source, series, layer_only: bool, source_matrix) -> None:
        self.source = source
        self.series = series
        self.duration = float(source.duration)
        self.start = float(source.timeline_start)
        self.end = self.start + self.duration
        self.layer_only = layer_only
        self.source_matrix = source_matrix

    @classmethod
    def build(cls, context: TrackingContext, source, link: TrackLink, target_clip, space: str):
        if getattr(source, "graphic", None) is not None:
            return None
        datas = context.tracker_datas(source, link.tracker_ids)
        fit = context.fit(source)
        size = context.media_size(source)
        if not datas or fit is None or size is None:
            return None
        series = motion_series(
            datas, fit, reference_index=link.reference_index, media_size=size,
            rotation=link.rotation and len(datas) >= 2, scale=link.scale and len(datas) >= 2,
        )
        if series.is_empty:
            return None
        layer_only = space == "layer" or (space == "target" and source is target_clip)
        source_matrix = None
        if not layer_only and context.is_static(source):
            source_matrix = context.layer_matrix(source, 0.0)
        return cls(source, series, layer_only, source_matrix)

    def shows(self, source_index: int) -> bool:
        """Le clip montre-t-il l'image source ``source_index`` ?"""
        low, high = clip_source_indices(self.source, self.series.rate)
        return low <= source_index <= high


class LinkMotion:
    """Mouvement d'une liaison, résolu une fois puis évalué image par image.

    Une liaison suit un clip, ou plusieurs après la coupe de sa source (:attr:`TrackLink.source_ids`) :
    à chaque instant, c'est la partie qui **couvre** cet instant de la timeline qui donne le mouvement.
    Les deux moitiés lisent les mêmes données (temps source), donc le mouvement est continu au point de
    coupe. Hors de toute partie, le mouvement tient la valeur de la plus proche (jamais un saut) ;
    :func:`link_issues` le signale pour que l'interface ne le laisse pas passer en silence.
    """

    def __init__(self, context: TrackingContext, target_clip, link: TrackLink, space: str) -> None:
        self.context = context
        self.target = target_clip
        self.space = space
        self.parts: list[_SourcePart] = []
        self.reference_inverse: Matrix | None = None
        self.target_matrix: tuple[Matrix, Matrix] | None = None
        parts = [
            part for part in (
                _SourcePart.build(context, source, link, target_clip, space)
                for source in context.link_sources(target_clip, link)
            ) if part is not None
        ]
        if not parts:
            return
        if any(not part.layer_only for part in parts):
            # Le repère de référence est celui du clip qui montre l'image de référence.
            owner = next((part for part in parts if part.shows(link.reference_index)), parts[0])
            rate = owner.series.rate
            seconds = owner.series.reference_index / rate if rate > 0 else 0.0
            reference_local = max(0.0, min(owner.duration, local_time_for_source(owner.source, seconds)))
            try:
                self.reference_inverse = mat_invert(context.layer_matrix(owner.source, reference_local))
            except ValueError:
                return
            if space == "target" and context.is_static(target_clip):
                matrix = context.layer_matrix(target_clip, 0.0)
                try:
                    self.target_matrix = (matrix, mat_invert(matrix))
                except ValueError:
                    return
        self.parts = parts

    @property
    def series(self):
        """Série de la première partie (``None`` sans donnée) : le mouvement existe-t-il ?"""
        return self.parts[0].series if self.parts else None

    def _part_at(self, timeline_time: float) -> _SourcePart | None:
        parts = self.parts
        if len(parts) <= 1:
            return parts[0] if parts else None
        covering = [p for p in parts if p.start - _TIME_EPSILON <= timeline_time < p.end - _TIME_EPSILON]
        if covering:
            return covering[0]          # plusieurs : ambigu (signalé par link_issues), l'ordre de la liaison tranche
        # Hors de toute partie : la plus proche, la dernière en cas d'égalité.
        return min(
            reversed(parts), key=lambda p: max(p.start - timeline_time, timeline_time - p.end, 0.0)
        )

    def at(self, timeline_time: float) -> Matrix | None:
        part = self._part_at(float(timeline_time))
        if part is None:
            return None
        source = part.source
        local = max(0.0, min(part.duration, float(timeline_time) - float(source.timeline_start)))
        layer_motion = part.series.matrix_at_time(source_time(source, local))
        if part.layer_only:
            return layer_motion
        if self.reference_inverse is None:
            return None
        source_matrix = part.source_matrix or self.context.layer_matrix(source, local)
        canvas_motion = mat_mul(mat_mul(source_matrix, layer_motion), self.reference_inverse)
        if self.space == "canvas":
            return canvas_motion
        if self.target_matrix is not None:
            n, inverse = self.target_matrix
        else:
            n = self.context.layer_matrix(self.target, float(timeline_time) - float(self.target.timeline_start))
            try:
                inverse = mat_invert(n)
            except ValueError:
                return None
        return mat_mul(mat_mul(inverse, canvas_motion), n)


def link_issues(context: TrackingContext, target_clip, link: TrackLink) -> tuple[str, ...]:
    """Ce qui empêche ``link`` de suivre correctement toute la durée de ``target_clip``.

    Codes (``tracking.link.<code>`` dans les avertissements du plan et dans l'interface) :

    - ``missing_source`` : aucun des clips suivis n'existe plus ;
    - ``part_missing`` : l'une des parties de la source a disparu (supprimée) ;
    - ``source_gap`` : une partie de la durée du clip lié n'est couverte par aucune partie de la source
      (une moitié a été raccourcie, déplacée ou supprimée) : le mouvement y reste figé sur la plus proche ;
    - ``ambiguous_source`` : plusieurs parties de la source se recouvrent sous le clip lié ; la première
      de la liaison est utilisée.

    Une liaison propre au clip (``SELF_CLIP``) n'a jamais de ces défauts. Vide : la liaison suit
    correctement.
    """
    if link.source_clip_id == SELF_CLIP:
        return ()
    issues: list[str] = []
    found = [clip for clip in (context.clip(clip_id) for clip_id in link.source_ids) if clip is not None]
    if not found:
        return ("missing_source",)
    if len(found) < len(link.source_ids):
        issues.append("part_missing")
    usable = [
        clip for clip in found
        if getattr(clip, "graphic", None) is None and context.tracker_datas(clip, link.tracker_ids)
    ]
    first = float(target_clip.timeline_start)
    last = first + float(target_clip.duration)
    half_frame = 0.5 / max(1.0, context.fps)
    spans = sorted(
        (max(first, float(c.timeline_start)), min(last, float(c.timeline_start) + float(c.duration)))
        for c in usable
    )
    spans = [(a, b) for a, b in spans if b > a]
    covered = 0.0
    cursor = first
    for a, b in spans:
        if b > cursor:
            covered += b - max(a, cursor)
            cursor = b
    if last - first - covered > half_frame:
        issues.append("source_gap")
    if any(spans[i][1] - spans[i + 1][0] > half_frame for i in range(len(spans) - 1)):
        issues.append("ambiguous_source")
    return tuple(issues)


# ---------------------------------------------------------------------------
# Échantillonnage et simplification
# ---------------------------------------------------------------------------


def frame_times(clip, fps: float) -> list[float]:
    """Temps locaux des images de la séquence couvertes par le clip."""
    fps = float(fps) or 30.0
    start = float(clip.timeline_start)
    end = start + float(clip.duration)
    first = int(math.ceil(start * fps - 1e-6))
    last = int(math.floor(end * fps - 1e-6))
    step = 2 if (last - first) > MAX_DERIVED_FRAMES else 1
    times = [normalize_time(max(0.0, f / fps - start)) for f in range(first, last + 1, step)]
    if not times or times[0] > 0.0:
        times.insert(0, 0.0)
    return sorted(set(times))


def simplify(times: list[float], values: list[float], tolerance: float) -> list[int]:
    """Indices à garder pour que l'interpolation linéaire reste à ``tolerance`` près.

    Glouton, linéaire en pratique : un segment s'allonge tant que tous ses
    points intermédiaires restent dans la tolérance.
    """
    n = len(times)
    if n <= 2:
        return list(range(n))
    kept = [0]
    anchor = 0
    end = 2
    while end < n:
        t0, v0 = times[anchor], values[anchor]
        t1, v1 = times[end], values[end]
        span = t1 - t0
        fits = True
        for i in range(anchor + 1, end):
            u = (times[i] - t0) / span if span > 0 else 0.0
            if abs(v0 + (v1 - v0) * u - values[i]) > tolerance:
                fits = False
                break
        if not fits:
            anchor = end - 1
            kept.append(anchor)
        end += 1
    kept.append(n - 1)
    return kept


# Identifiants déterministes : un même résultat recalculé a la même empreinte
# (segments d'aperçu réutilisés), et un bake reste reproductible.


def _transform_keyframes(name: str, times: list[float], values: list[float], tolerance: float):
    spec = TRANSFORM_PROPERTIES[name]
    clamped = [spec.clamp(v) for v in values]
    indices = simplify(times, clamped, tolerance)
    return [
        TransformKeyframe(name, times[i], clamped[i], InterpolationType.LINEAR, id=f"tk-{name}-{i}")
        for i in indices
    ]


def _generic_keyframes(property_id: str, spec, times, values, tolerance):
    clamped = [spec.clamp(v) for v in values]
    indices = simplify(times, clamped, tolerance)
    return [
        Keyframe(property_id, times[i], clamped[i], InterpolationType.LINEAR, id=f"tk-{property_id}-{i}")
        for i in indices
    ]


# ---------------------------------------------------------------------------
# État effectif d'un clip
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveState:
    """Animation du clip telle que rendue (liaisons et stabilisation appliquées)."""

    transform_keyframes: tuple
    animation: tuple
    compositing: object
    derived: frozenset[str] = frozenset()
    """Propriétés dont les valeurs viennent du tracking."""
    zoom: float = 1.0
    """Agrandissement automatique de la stabilisation (1 = aucun)."""
    warnings: tuple[str, ...] = ()


_STATE_CACHE: OrderedDict = OrderedDict()
_STATE_CACHE_SIZE = 256
_STATE_LOCK = threading.RLock()     # états évalués par l'interface et par les threads de rendu


def _state_key(clip, context: TrackingContext):
    tracking: ClipTracking = clip.tracking
    sources: list[tuple] = []
    for link in tracking.links:
        if link.enabled:
            for source_id in link.source_ids:
                source = context.clip(source_id)
                if source is None:
                    sources.append((source_id, None))
                    continue
                sources.append((
                    source.id, source.tracking, source.transform, tuple(source.transform_keyframes),
                    source.timeline_start, source.source_in, source.source_out, source.time_remapping,
                    source.asset_id,
                ))
    graphic = getattr(clip, "graphic", None)
    return (
        clip.id, tracking, clip.transform, tuple(clip.transform_keyframes), tuple(clip.animation),
        clip.compositing, clip.timeline_start, clip.source_in, clip.source_out, clip.time_remapping,
        clip.asset_id, graphic, context.width, context.height, context.fps,
        context.media_size(clip), tuple(sources),
        tuple(context.media_size(context.clip(s[0])) if s[1] is not None else None for s in sources),
    )


def effective_clip_state(clip, context: TrackingContext) -> EffectiveState:
    """Images-clés, animation et compositing **rendus** d'un clip."""
    tracking: ClipTracking | None = getattr(clip, "tracking", None)
    base = EffectiveState(tuple(clip.transform_keyframes), tuple(clip.animation), clip.compositing)
    if tracking is None or not tracking.drives_rendering:
        return base
    try:
        key = _state_key(clip, context)
        hash(key)
    except TypeError:
        key = None
    if key is not None:
        with _STATE_LOCK:
            cached = _STATE_CACHE.get(key)
            if cached is not None:
                _STATE_CACHE.move_to_end(key)
        if cached is not None:
            return cached
    try:
        state = _compute_state(clip, context, tracking)
    except Exception as exc:  # garde-fou : jamais de rendu cassé par une liaison
        LOGGER.exception("Tracking : état du clip %s impossible à dériver (rendu sans suivi)", clip.id)
        state = replace(base, warnings=(f"tracking:{exc}",))
    if key is not None:
        with _STATE_LOCK:
            _STATE_CACHE[key] = state
            if len(_STATE_CACHE) > _STATE_CACHE_SIZE:
                _STATE_CACHE.popitem(last=False)
    return state


def _compute_state(clip, context: TrackingContext, tracking: ClipTracking) -> EffectiveState:
    times = frame_times(clip, context.fps)
    duration = float(clip.duration)
    transform_frames = list(clip.transform_keyframes)
    animation = list(clip.animation)
    compositing = clip.compositing
    derived: set[str] = set()
    warnings: list[str] = []
    zoom = 1.0
    is_video = getattr(clip, "graphic", None) is None
    # 1. Stabilisation (clip vidéo, repère calque).
    stab_result = context.stabilization(clip) if is_video else None
    if stab_result is not None:
        zoom = stab_result.zoom
        if stab_result.message:
            warnings.append(stab_result.message)
        corrections = [stab_result.correction_at_time(source_time(clip, t)) for t in times]
        values = _values_over(clip.transform, transform_frames, times, duration)
        updated = [_compose_right(v, c, context) for v, c in zip(values, corrections)]
        transform_frames = _replace_transform(
            transform_frames, times, updated, ("position_x", "position_y", "rotation", "scale"), context,
        )
        derived.update(("position_x", "position_y", "rotation", "scale"))
        if stab_result.crop_rect is not None:
            compositing, crop_frames = _crop_mask(compositing, corrections, times, stab_result, context)
            animation = [kf for kf in animation if not kf.property_name.startswith(f"mask.{CROP_MASK_ID}.")]
            animation.extend(crop_frames)
    # 2. Liaisons, dans l'ordre.
    for link in tracking.links:
        if not link.enabled:
            continue
        sources = context.link_sources(clip, link)
        if not sources:
            warnings.append("tracking.link.missing_source")
            continue
        source = sources[0]
        warnings.extend(f"tracking.link.{code}" for code in link_issues(context, clip, link))
        if link.target == TrackTarget.TRANSFORM:
            motions = [context.link_motion(clip, link, clip.timeline_start + t, space="canvas") for t in times]
            if all(m is None for m in motions):
                warnings.append("tracking.link.no_data")
                continue
            values = _values_over(clip.transform, transform_frames, times, duration)
            updated = [
                _compose_left(v, m, link, clip, context) if m is not None else v
                for v, m in zip(values, motions)
            ]
            names = ["position_x", "position_y"] if link.position else []
            if link.rotation:
                names.append("rotation")
            if link.scale:
                names.append("scale")
            transform_frames = _replace_transform(transform_frames, times, updated, tuple(names), context)
            derived.update(names)
        elif link.target == TrackTarget.ANCHOR:
            if source is not clip:
                warnings.append("tracking.link.anchor_self_only")
                continue
            points = _anchor_points(clip, link, times, context)
            if points is None:
                warnings.append("tracking.link.no_data")
                continue
            values = _values_over(clip.transform, transform_frames, times, duration)
            updated = [
                replace(v, anchor_x=p[0], anchor_y=p[1]) if p is not None else v for v, p in zip(values, points)
            ]
            transform_frames = _replace_transform(
                transform_frames, times, updated, ("anchor_x", "anchor_y"), context,
            )
            derived.update(("anchor_x", "anchor_y"))
        elif link.target == TrackTarget.MASK:
            mask = compositing.mask_by_id(link.mask_id) if compositing is not None else None
            if mask is None:
                warnings.append("tracking.link.missing_mask")
                continue
            motions = [context.link_motion(clip, link, clip.timeline_start + t, space="target") for t in times]
            if all(m is None for m in motions):
                warnings.append("tracking.link.no_data")
                continue
            animation = _follow_mask(
                mask, animation, times, motions, link, _mask_box(clip, context), duration,
            )
            derived.update(mask_property_id(mask.id, n) for n in ("position_x", "position_y"))
    transform_frames.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
    animation.sort(key=lambda kf: (kf.property_name, kf.time_seconds))
    return EffectiveState(
        tuple(transform_frames), tuple(animation), compositing, frozenset(derived), zoom, tuple(warnings),
    )


def _values_over(transform, keyframes, times, duration):
    curves_input = list(keyframes)
    return [evaluate_transform(transform, curves_input, t, duration) for t in times]


def _replace_transform(frames, times, values, names, context) -> list:
    kept = [kf for kf in frames if kf.property_name not in names]
    for name in names:
        tolerance = TOLERANCE.get(name, 1e-4)
        if name in ("position_x", "anchor_x"):
            tolerance /= max(1, context.width)
        elif name in ("position_y", "anchor_y"):
            tolerance /= max(1, context.height)
        series = [float(getattr(v, name)) for v in values]
        if any(not math.isfinite(s) for s in series):
            continue
        kept.extend(_transform_keyframes(name, times, series, tolerance))
    return kept


def _anchor_world(values, clip, context) -> tuple[float, float]:
    graphic = getattr(clip, "graphic", None)
    canvas = (float(context.width), float(context.height))
    if graphic is None:
        box = canvas
        m = local_matrix(values, box, canvas, canvas, allow_skew=False)
    else:
        box = (float(graphic.width), float(graphic.height))
        m = local_matrix(values, box, canvas, canvas, layout=graphic.layout)
    return mat_apply(m, values.anchor_x * box[0], values.anchor_y * box[1])


def _compose_left(values, motion: Matrix, link: TrackLink, clip, context):
    """``L(V) = A · L(U)`` : la cible suit le mouvement dans le cadre."""
    if not matrix_is_finite(motion):
        return values
    changes = {}
    if link.position:
        px, py = _anchor_world(values, clip, context)
        qx, qy = mat_apply(motion, px, py)
        changes["position_x"] = values.position_x + (qx - px) / context.width
        changes["position_y"] = values.position_y + (qy - py) / context.height
    theta, k = similarity_parts(motion)
    if link.rotation:
        changes["rotation"] = values.rotation + theta
    if link.scale and k > 1e-6:
        changes["scale"] = values.scale * k
    return replace(values, **changes)


def _compose_right(values, correction: Matrix, context):
    """``L(V) = L(U) · C`` : correction de stabilisation dans le repère calque."""
    if not matrix_is_finite(correction):
        return values
    canvas = (float(context.width), float(context.height))
    full = mat_mul(local_matrix(values, canvas, canvas, canvas, allow_skew=False), correction)
    theta, k = similarity_parts(correction)
    mirrored = bool(values.flip_h) != bool(values.flip_v)
    ax, ay = values.anchor_x * canvas[0], values.anchor_y * canvas[1]
    px, py = mat_apply(full, ax, ay)
    return replace(
        values,
        position_x=(px - canvas[0] / 2.0) / canvas[0],
        position_y=(py - canvas[1] / 2.0) / canvas[1],
        rotation=values.rotation + (-theta if mirrored else theta),
        scale=values.scale * (k if k > 1e-6 else 1.0),
    )


def _anchor_points(clip, link, times, context):
    datas = context.tracker_datas(clip, link.tracker_ids[:1])
    if not datas:
        return None
    data = datas[0]
    if data.is_empty:
        return None
    result = []
    for t in times:
        position = data.position_at_time(source_time(clip, t))
        if position is None:
            result.append(None)
            continue
        point = context.source_layer_point(clip, position[0], position[1], data)
        result.append(None if point is None else (point[0] / context.width, point[1] / context.height))
    return result


def _mask_box(clip, context) -> tuple[float, float]:
    graphic = getattr(clip, "graphic", None)
    if graphic is None:
        return (float(context.width), float(context.height))
    return (float(graphic.width), float(graphic.height))


def _follow_mask(mask: Mask, animation: list, times, motions, link: TrackLink, box, duration) -> list:
    """Le masque suit ``motions`` (repère calque de son clip, boîte ``box``)."""
    from .animation_targets import animation_curves

    curves = animation_curves(_View(animation), prefix=f"mask.{mask.id}.")
    columns: dict[str, list[float]] = {name: [] for name in MASK_PROPERTY_ORDER}
    for t, motion in zip(times, motions):
        current = evaluate_mask_at(mask, curves, max(0.0, min(duration, t)))
        values = {name: float(getattr(current, name)) for name in MASK_PROPERTY_ORDER}
        if motion is not None and matrix_is_finite(motion):
            cx, cy = values["position_x"] * box[0], values["position_y"] * box[1]
            qx, qy = mat_apply(motion, cx, cy)
            values["position_x"] = qx / box[0]
            values["position_y"] = qy / box[1]
            theta, k = similarity_parts(motion)
            if link.rotation:
                values["rotation"] += theta
            if link.scale and k > 1e-6:
                expansion = values["expansion"]
                values["width"] = (values["width"] + expansion) * k - expansion
                values["height"] = (values["height"] + expansion) * k - expansion
        for name in MASK_PROPERTY_ORDER:
            columns[name].append(values[name])
    names = ["position_x", "position_y"]
    if link.rotation:
        names.append("rotation")
    if link.scale:
        names += ["width", "height"]
    prefixes = {mask_property_id(mask.id, name) for name in names}
    kept = [kf for kf in animation if kf.property_name not in prefixes]
    for name in names:
        spec = MASK_PROPERTY_SPECS[name]
        tolerance = 1e-4 if name == "rotation" else 0.02 / max(1.0, box[0] if name.endswith("x") or name == "width" else box[1])
        kept.extend(_generic_keyframes(mask_property_id(mask.id, name), spec, times, columns[name], tolerance))
    return kept


class _View:
    __slots__ = ("animation",)

    def __init__(self, animation) -> None:
        self.animation = tuple(animation)


def _crop_mask(compositing, corrections, times, result: StabilizationResult, context):
    """Masque rectangle dérivé : ne montre que la zone stable (mode recadrage)."""
    from .compositing import Compositing

    canvas = (float(context.width), float(context.height))
    if result.crop_rect is None:
        return compositing, []
    columns: dict[str, list[float]] = {name: [] for name in ("position_x", "position_y", "width", "height", "rotation")}
    for correction in corrections:
        px, py, w, h, rotation = crop_mask_values(correction, result.crop_rect, canvas)
        for name, value in zip(columns, (px, py, w, h, rotation)):
            columns[name].append(value)
    first = {name: values[0] for name, values in columns.items()}
    mask = Mask(
        shape=MaskShape.RECTANGLE, mode=MaskMode.INTERSECT, name="Stabilisation", id=CROP_MASK_ID,
        **{name: MASK_PROPERTY_SPECS[name].clamp(value) for name, value in first.items()},
    )
    base = compositing if compositing is not None else Compositing()
    masks = tuple(m for m in base.masks if m.id != CROP_MASK_ID) + (mask,)
    frames = []
    for name, values in columns.items():
        spec = MASK_PROPERTY_SPECS[name]
        tolerance = 1e-4 if name == "rotation" else 0.02 / max(1.0, canvas[0])
        frames.extend(_generic_keyframes(mask_property_id(CROP_MASK_ID, name), spec, times, values, tolerance))
    return replace(base, masks=masks), frames


# ---------------------------------------------------------------------------
# Accès simples (interface, bake)
# ---------------------------------------------------------------------------


def effective_transform_keyframes(project, clip) -> list:
    """Images-clés de transform rendues du clip (séquence active)."""
    if getattr(clip, "tracking", None) is None:
        return list(clip.transform_keyframes)
    return list(effective_clip_state(clip, TrackingContext(project)).transform_keyframes)


def baked_keyframes(project, clip, link_id: str) -> tuple[list, list] | None:
    """``(transform_keyframes, animation)`` du clip avec la seule liaison ``link_id`` figée.

    Les autres liaisons restent dynamiques : on calcule l'état avec cette
    liaison seule, puis on garde ses propriétés.
    """
    tracking: ClipTracking | None = getattr(clip, "tracking", None)
    if tracking is None:
        return None
    link = tracking.link(link_id)
    if link is None:
        return None
    solo = replace(tracking, links=(replace(link, enabled=True),), stabilization=None)
    view = _ClipView(clip, solo)
    state = _compute_state(view, TrackingContext(project), solo)
    return list(state.transform_keyframes), list(state.animation)


class _ClipView:
    """Le clip vu avec un autre ``tracking`` (sans le modifier)."""

    def __init__(self, clip, tracking) -> None:
        self._clip = clip
        self.tracking = tracking

    def __getattr__(self, name):
        return getattr(self._clip, name)


def curve_for(keyframes: Iterable[Keyframe], name: str) -> AnimationCurve:
    frames = [kf for kf in keyframes if kf.property_name == name]
    kind = TRANSFORM_PROPERTIES[name].kind if name in TRANSFORM_PROPERTIES else ValueKind.FLOAT
    return AnimationCurve(frames, kind)


__all__ = [
    "CROP_MASK_ID", "EffectiveState", "LinkMotion", "TrackingContext", "baked_keyframes", "effective_clip_state",
    "curve_for", "effective_transform_keyframes", "frame_times", "link_issues", "simplify",
]
