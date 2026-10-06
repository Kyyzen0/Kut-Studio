"""Transformations visuelles et images-clés pour Kut-Studio.

Ce module encapsule toute la logique métier pure liée à la
composition visuelle :

- :class:`ClipTransform` — position, échelle, rotation, opacité ;
- :class:`TransformKeyframe` — :class:`~core.animation.Keyframe` d'une
  propriété de transform (bornes validées) ;
- :class:`EvaluatedTransform` — résultat figé de l'évaluation ;
- :func:`evaluate_transform` — évaluation des courbes d'animation
  (:mod:`core.animation`, toutes interpolations) ;
- :func:`build_ffmpeg_expression` — la même courbe en expression FFmpeg
  (:mod:`core.animation_ffmpeg`), pour un export identique à l'aperçu.

Le module est volontairement pur : aucune dépendance à une
bibliothèque graphique ou à un binaire externe. Il peut être
testé en CLI et utilisé par tout consommateur (moteur d'export,
outils d'aperçu, scripts).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Iterable

from .animation import (
    AnimatableProperty,
    AnimationCurve,
    InterpolationType,
    Keyframe,
    ValueKind,
)
from .animation_ffmpeg import curve_expression


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


ANIMATABLE_PROPERTIES: tuple[str, ...] = (
    "position_x",
    "position_y",
    "scale",
    "rotation",
    "opacity",
)
"""Propriétés de transform « essentielles » (groupe Mouvement de l'inspecteur)."""

ADVANCED_TRANSFORM_PROPERTIES: tuple[str, ...] = (
    "anchor_x",
    "anchor_y",
    "scale_x",
    "scale_y",
    "skew",
    "flip_h",
    "flip_v",
    "fill",
    "pan_x",
    "pan_y",
)
"""Propriétés de transform avancées (motion graphics, cadrage), toutes animables.

- ``anchor_x`` / ``anchor_y`` : point d'ancrage en fraction de la taille du
  calque (``0.5`` = centre). Rotation, échelle et parentage pivotent autour
  de lui ; la position désigne l'endroit où il se trouve.
- ``scale_x`` / ``scale_y`` : multiplicateurs de l'échelle uniforme
  (``scale``) : l'échelle effective est ``scale × scale_x``.
- ``skew`` : inclinaison horizontale en degrés (calques graphiques).
- ``flip_h`` / ``flip_v`` : miroirs (booléens, interpolation en maintien).
- ``fill`` : cadrage d'un clip vidéo. Faux (défaut) : le média tient entier dans le cadre, avec des bandes ; vrai : il
  **remplit** le cadre sans déformation, l'excédent est rogné (recadrer une vidéo 16:9 en 9:16).
- ``pan_x`` / ``pan_y`` : fenêtre du cadrage « remplir » dans le média, de −1 (bord gauche / haut) à 1 (bord droit / bas),
  0 = centrée. Animées, elles font un pan dans l'image. Sans effet hors de ``fill``.
"""

STATIC_TRANSFORM_PROPERTIES: frozenset[str] = frozenset({"fill"})
"""Propriétés du transform qui ne s'animent pas (aucune image-clé acceptée)."""

TRANSFORM_PROPERTY_NAMES: tuple[str, ...] = ANIMATABLE_PROPERTIES + ADVANCED_TRANSFORM_PROPERTIES
"""Toutes les propriétés de :class:`ClipTransform`, dans l'ordre d'affichage."""

_SCALE_MIN = 0.05
_SCALE_MAX = 10.0
_ROTATION_MIN = -3600.0
_ROTATION_MAX = 3600.0
_OPACITY_MIN = 0.0
_OPACITY_MAX = 1.0
_POSITION_MIN = -4.0
_POSITION_MAX = 4.0
_ANCHOR_MIN = -4.0
_ANCHOR_MAX = 5.0
_AXIS_SCALE_MIN = 0.0
_AXIS_SCALE_MAX = 10.0
_SKEW_MIN = -85.0
_SKEW_MAX = 85.0
_PAN_MIN = -1.0
_PAN_MAX = 1.0


_PROPERTY_BOUNDS: dict[str, tuple[float, float]] = {
    "position_x": (_POSITION_MIN, _POSITION_MAX),
    "position_y": (_POSITION_MIN, _POSITION_MAX),
    "scale": (_SCALE_MIN, _SCALE_MAX),
    "rotation": (_ROTATION_MIN, _ROTATION_MAX),
    "opacity": (_OPACITY_MIN, _OPACITY_MAX),
    "anchor_x": (_ANCHOR_MIN, _ANCHOR_MAX),
    "anchor_y": (_ANCHOR_MIN, _ANCHOR_MAX),
    "scale_x": (_AXIS_SCALE_MIN, _AXIS_SCALE_MAX),
    "scale_y": (_AXIS_SCALE_MIN, _AXIS_SCALE_MAX),
    "skew": (_SKEW_MIN, _SKEW_MAX),
    "flip_h": (0.0, 1.0),
    "flip_v": (0.0, 1.0),
    "fill": (0.0, 1.0),
    "pan_x": (_PAN_MIN, _PAN_MAX),
    "pan_y": (_PAN_MIN, _PAN_MAX),
}
"""Bornes acceptées pour chaque propriété animable."""

TRANSFORM_PROPERTIES: dict[str, AnimatableProperty] = {
    "position_x": AnimatableProperty("position_x", "animation.property.position_x", ValueKind.FLOAT,
                                     0.0, _POSITION_MIN, _POSITION_MAX, 0.01),
    "position_y": AnimatableProperty("position_y", "animation.property.position_y", ValueKind.FLOAT,
                                     0.0, _POSITION_MIN, _POSITION_MAX, 0.01),
    "scale": AnimatableProperty("scale", "animation.property.scale", ValueKind.FLOAT,
                                1.0, _SCALE_MIN, _SCALE_MAX, 0.01),
    "rotation": AnimatableProperty("rotation", "animation.property.rotation", ValueKind.FLOAT,
                                   0.0, _ROTATION_MIN, _ROTATION_MAX, 1.0),
    "opacity": AnimatableProperty("opacity", "animation.property.opacity", ValueKind.FLOAT,
                                  1.0, _OPACITY_MIN, _OPACITY_MAX, 0.01),
    "anchor_x": AnimatableProperty("anchor_x", "animation.property.anchor_x", ValueKind.FLOAT,
                                   0.5, _ANCHOR_MIN, _ANCHOR_MAX, 0.01),
    "anchor_y": AnimatableProperty("anchor_y", "animation.property.anchor_y", ValueKind.FLOAT,
                                   0.5, _ANCHOR_MIN, _ANCHOR_MAX, 0.01),
    "scale_x": AnimatableProperty("scale_x", "animation.property.scale_x", ValueKind.FLOAT,
                                  1.0, _AXIS_SCALE_MIN, _AXIS_SCALE_MAX, 0.01),
    "scale_y": AnimatableProperty("scale_y", "animation.property.scale_y", ValueKind.FLOAT,
                                  1.0, _AXIS_SCALE_MIN, _AXIS_SCALE_MAX, 0.01),
    "skew": AnimatableProperty("skew", "animation.property.skew", ValueKind.FLOAT,
                               0.0, _SKEW_MIN, _SKEW_MAX, 1.0),
    "flip_h": AnimatableProperty("flip_h", "animation.property.flip_h", ValueKind.BOOL,
                                 False, 0.0, 1.0, 1.0),
    "flip_v": AnimatableProperty("flip_v", "animation.property.flip_v", ValueKind.BOOL,
                                 False, 0.0, 1.0, 1.0),
    "fill": AnimatableProperty("fill", "animation.property.fill", ValueKind.BOOL,
                               False, 0.0, 1.0, 1.0),
    "pan_x": AnimatableProperty("pan_x", "animation.property.pan_x", ValueKind.FLOAT,
                                0.0, _PAN_MIN, _PAN_MAX, 0.01),
    "pan_y": AnimatableProperty("pan_y", "animation.property.pan_y", ValueKind.FLOAT,
                                0.0, _PAN_MIN, _PAN_MAX, 0.01),
}
"""Description générique (:class:`~core.animation.AnimatableProperty`) du transform."""


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipTransform:
    """Transform 2D d'un clip vidéo, sans dépendance à la durée du clip.

    Les valeurs sont stockées dans un repère normalisé par rapport au
    canvas : ``position_x=1.0`` correspond à un décalage d'une largeur
    de canvas vers la droite, ``scale=1.0`` à la taille naturelle, etc.

    Attributes:
        position_x: Décalage horizontal normalisé (défaut ``0.0`` = centre).
        position_y: Décalage vertical normalisé (défaut ``0.0`` = centre).
        scale: Échelle du clip (défaut ``1.0``).
        rotation: Rotation en degrés (défaut ``0.0``).
        opacity: Opacité (défaut ``1.0``).
    """

    position_x: float = 0.0
    position_y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    # --- Transform avancé (motion graphics) : neutre par défaut ---
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    # --- Cadrage d'un clip vidéo (remplir le cadre, fenêtre de pan) : neutre par défaut ---
    fill: bool = False
    pan_x: float = 0.0
    pan_y: float = 0.0

    def __post_init__(self) -> None:
        """Valide les bornes des champs du transform."""
        for name in TRANSFORM_PROPERTY_NAMES:
            _validate_value(name, getattr(self, name))
        object.__setattr__(self, "flip_h", bool(self.flip_h))
        object.__setattr__(self, "flip_v", bool(self.flip_v))
        object.__setattr__(self, "fill", bool(self.fill))

    def with_property(self, name: str, value: float) -> "ClipTransform":
        """Retourne un nouveau transform avec la propriété ``name`` mise à jour.

        L'instance courante étant immuable, un nouvel objet est créé.
        """
        if name not in TRANSFORM_PROPERTY_NAMES:
            raise ValueError(f"Propriété inconnue : {name!r}.")
        new_value = _coerce_value(name, value)
        return replace(self, **{name: new_value})

    @property
    def is_advanced(self) -> bool:
        """Utilise-t-il une propriété avancée (ancre, échelle X/Y, inclinaison, miroir) ?"""
        return any(
            getattr(self, name) != TRANSFORM_PROPERTIES[name].default
            for name in ADVANCED_TRANSFORM_PROPERTIES
        )


# ---------------------------------------------------------------------------
# Images-clés
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransformKeyframe(Keyframe):
    """:class:`~core.animation.Keyframe` d'une propriété de :class:`ClipTransform`.

    Le temps ``time_seconds`` est local au clip : ``0.0`` correspond au
    début du clip. La propriété et la valeur sont validées (bornes de
    :data:`TRANSFORM_PROPERTIES`) ; interpolation et tangentes viennent du
    moteur générique (linéaire par défaut).
    """

    def __post_init__(self) -> None:
        """Valide l'image-clé."""
        if self.property_name not in TRANSFORM_PROPERTY_NAMES or self.property_name in STATIC_TRANSFORM_PROPERTIES:
            raise ValueError(
                f"Propriété de keyframe inconnue : {self.property_name!r}."
            )
        super().__post_init__()
        _validate_value(self.property_name, self.value)
        if TRANSFORM_PROPERTIES[self.property_name].kind is ValueKind.BOOL:
            object.__setattr__(self, "value", bool(float(self.value) >= 0.5))
        else:
            object.__setattr__(self, "value", float(self.value))


# ---------------------------------------------------------------------------
# Évaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatedTransform:
    """Résultat immuable de l'évaluation d'un transform à un instant donné."""

    position_x: float
    position_y: float
    scale: float
    rotation: float
    opacity: float
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    fill: bool = False
    pan_x: float = 0.0
    pan_y: float = 0.0

    def as_transform(self) -> ClipTransform:
        """Retourne un nouveau :class:`ClipTransform` équivalent."""
        return ClipTransform(**{name: getattr(self, name) for name in TRANSFORM_PROPERTY_NAMES})

    @property
    def effective_scale_x(self) -> float:
        """Échelle horizontale finale (uniforme × axe), miroir non compris."""
        return self.scale * self.scale_x

    @property
    def effective_scale_y(self) -> float:
        """Échelle verticale finale (uniforme × axe), miroir non compris."""
        return self.scale * self.scale_y


_CURVE_CACHE: OrderedDict[tuple, dict[str, AnimationCurve]] = OrderedDict()
_CURVE_CACHE_SIZE = 256
_CACHE_LOCK = threading.RLock()
"""Les courbes sont évaluées par l'interface **et** par les threads de rendu (graphe d'aperçu, images de calques) :
sans verrou, ``get`` puis ``move_to_end`` pouvaient croiser l'éviction d'un autre thread (``KeyError``)."""
_LAST_BY_LIST: OrderedDict[int, tuple] = OrderedDict()
"""``id(liste) -> (liste, durée, éléments, courbes)`` : la liste est retenue,
son identifiant ne peut donc pas être réutilisé par une autre liste."""


def _remember_list(keyframes: list, clip_duration, frames: tuple, curves) -> None:
    with _CACHE_LOCK:
        _LAST_BY_LIST[id(keyframes)] = (keyframes, clip_duration, frames, curves)
        _LAST_BY_LIST.move_to_end(id(keyframes))
        if len(_LAST_BY_LIST) > _CURVE_CACHE_SIZE:
            _LAST_BY_LIST.popitem(last=False)


def transform_curves(
    keyframes: Iterable[TransformKeyframe], clip_duration: float | None = None
) -> dict[str, AnimationCurve]:
    """Courbes par propriété (seules les propriétés animées sont présentes).

    Les keyframes situées après la fin du clip sont ignorées. Le résultat
    est mis en cache (les keyframes sont immuables) : la lecture évalue la
    même courbe à chaque image sans la reconstruire.
    """
    # Chemin rapide (lecture) : la même liste, aux mêmes éléments, que la
    # dernière fois. Comparer des identités évite de hacher chaque keyframe.
    if isinstance(keyframes, list):
        with _CACHE_LOCK:
            recent = _LAST_BY_LIST.get(id(keyframes))
        if (
            recent is not None
            and recent[0] is keyframes
            and recent[1] == clip_duration
            and len(recent[2]) == len(keyframes)
            and all(a is b for a, b in zip(recent[2], keyframes))
        ):
            return recent[3]
    frames = tuple(keyframes)
    key = (frames, clip_duration)
    with _CACHE_LOCK:
        cached = _CURVE_CACHE.get(key)
        if cached is not None:
            _CURVE_CACHE.move_to_end(key)
    if cached is not None:
        if isinstance(keyframes, list):
            _remember_list(keyframes, clip_duration, frames, cached)
        return cached
    grouped: dict[str, list[TransformKeyframe]] = {}
    for kf in frames:
        if kf.property_name not in TRANSFORM_PROPERTIES:
            continue
        if clip_duration is not None and kf.time_seconds > clip_duration + 1e-6:
            continue  # Keyframe située après la fin du clip.
        grouped.setdefault(kf.property_name, []).append(kf)
    curves = {
        name: AnimationCurve(items, TRANSFORM_PROPERTIES[name].kind) for name, items in grouped.items()
    }
    with _CACHE_LOCK:
        _CURVE_CACHE[key] = curves
        if len(_CURVE_CACHE) > _CURVE_CACHE_SIZE:
            _CURVE_CACHE.popitem(last=False)
    if isinstance(keyframes, list):
        _remember_list(keyframes, clip_duration, frames, curves)
    return curves


def evaluate_transform(
    transform: ClipTransform,
    keyframes: Iterable[TransformKeyframe],
    clip_local_time: float,
    clip_duration: float | None = None,
) -> EvaluatedTransform:
    """Évalue ``transform`` à ``clip_local_time`` en tenant compte des keyframes.

    Une propriété sans keyframe garde sa valeur de base ; une propriété
    animée suit sa courbe (:mod:`core.animation` : avant le premier keyframe,
    sa valeur ; après le dernier, sa valeur ; entre deux, l'interpolation du
    segment), bornée aux limites de la propriété. ``clip_local_time`` est
    clampé dans ``[0, clip_duration]`` si ``clip_duration`` est fourni.
    """
    if clip_duration is not None and clip_duration > 0.0:
        if clip_local_time < 0.0:
            clip_local_time = 0.0
        elif clip_local_time > clip_duration:
            clip_local_time = float(clip_duration)
    curves = transform_curves(keyframes, clip_duration)

    def _resolve(name: str):
        spec = TRANSFORM_PROPERTIES[name]
        value = spec.evaluate(curves.get(name), getattr(transform, name), clip_local_time)
        return bool(value) if spec.kind is ValueKind.BOOL else float(value)

    return EvaluatedTransform(**{name: _resolve(name) for name in TRANSFORM_PROPERTY_NAMES})


def migrate_legacy_keyframes(
    transform: ClipTransform, keyframes: Iterable[TransformKeyframe]
) -> list[TransformKeyframe]:
    """Convertit des keyframes de l'ancien moteur (avant le format 13) sans changer le rendu.

    L'ancien moteur gardait la valeur **de base** jusqu'au premier keyframe,
    puis sautait à sa valeur. Le moteur actuel tient la valeur du premier
    keyframe. Un keyframe ``hold`` à ``t = 0`` portant la valeur de base
    reproduit exactement l'ancien rendu.
    """
    result = list(keyframes)
    first: dict[str, TransformKeyframe] = {}
    for kf in result:
        current = first.get(kf.property_name)
        if current is None or kf.time_seconds < current.time_seconds:
            first[kf.property_name] = kf
    for name, kf in first.items():
        if name not in ANIMATABLE_PROPERTIES:
            continue  # l'ancien moteur ne connaissait que les 5 propriétés de base
        base = float(getattr(transform, name))
        if kf.time_seconds > 0.0 and abs(base - float(kf.value)) > 1e-12:
            result.append(TransformKeyframe(name, 0.0, base, InterpolationType.HOLD))
    result.sort(key=lambda k: (k.property_name, k.time_seconds))
    return result


def _curve_keyframes(curves: dict[str, AnimationCurve]) -> list[TransformKeyframe]:
    result = [kf for curve in curves.values() for kf in curve.keyframes]
    result.sort(key=lambda k: (k.property_name, k.time_seconds))
    return result


def split_transform_keyframes(
    transform: ClipTransform,
    keyframes: Iterable[TransformKeyframe],
    cut_local_time: float,
    clip_duration: float | None = None,
) -> tuple[list[TransformKeyframe], list[TransformKeyframe]]:
    """Coupe l'animation en deux **sans en changer le rendu**.

    Chaque courbe est coupée par :meth:`AnimationCurve.split` (segments
    polynomiaux scindés exactement) ; la partie droite est recalée à 0.
    """
    left: dict[str, AnimationCurve] = {}
    right: dict[str, AnimationCurve] = {}
    for name, curve in transform_curves(keyframes, clip_duration).items():
        try:
            left[name], right[name] = curve.split(cut_local_time, clamp=TRANSFORM_PROPERTIES[name].clamp)
        except ValueError:
            # Dépassement de bornes au point de coupe (Bézier très tendue) :
            # on borne la valeur, la forme reste fidèle au millième près.
            value = TRANSFORM_PROPERTIES[name].clamp(curve.evaluate(cut_local_time))
            before = [k for k in curve.keyframes if k.time_seconds < cut_local_time]
            after = [
                copy_keyframe(k, time_seconds=k.time_seconds - cut_local_time)
                for k in curve.keyframes if k.time_seconds > cut_local_time
            ]
            boundary = TransformKeyframe(name, cut_local_time, value)
            left[name] = AnimationCurve([*before, boundary])
            right[name] = AnimationCurve([TransformKeyframe(name, 0.0, value), *after])
    return _curve_keyframes(left), _curve_keyframes(right)


def retime_transform_keyframes(
    keyframes: Iterable[TransformKeyframe],
    *,
    start_offset: float,
    new_duration: float,
    clip_duration: float | None = None,
) -> list[TransformKeyframe]:
    """Keyframes après un trim, en gardant l'animation **visible** inchangée.

    ``start_offset`` > 0 : le début du clip avance (trim gauche) ; < 0 : il
    recule. Les segments coupés par les nouvelles bornes sont scindés
    exactement plutôt que supprimés.
    """
    curves: dict[str, AnimationCurve] = {}
    for name, curve in transform_curves(keyframes, clip_duration).items():
        if start_offset > 0:
            curve = split_transform_keyframes(ClipTransform(), curve.keyframes, start_offset)[1]
            curve = AnimationCurve(curve)
        elif start_offset < 0:
            curve = AnimationCurve(
                copy_keyframe(k, time_seconds=k.time_seconds - start_offset) for k in curve.keyframes
            )
        if curve and curve.times[-1] > new_duration + 1e-6:
            curve = AnimationCurve(
                split_transform_keyframes(ClipTransform(), curve.keyframes, new_duration)[0]
            )
        curves[name] = curve
    return _curve_keyframes(curves)


def copy_keyframe(kf: TransformKeyframe, **changes) -> TransformKeyframe:
    """Copie d'un keyframe en conservant interpolation, tangentes et identifiant."""
    changes.setdefault("id", kf.id)
    return replace(kf, **changes)


# ---------------------------------------------------------------------------
# Expression FFmpeg
# ---------------------------------------------------------------------------


def build_ffmpeg_expression(
    property_name: str,
    base_value: float,
    keyframes: Iterable[TransformKeyframe],
    *,
    time_var: str = "T",
) -> str:
    """Expression FFmpeg de la valeur de ``property_name`` au temps ``time_var``.

    Sans keyframe : ``base_value``. Sinon, la courbe de :mod:`core.animation`
    traduite par :func:`core.animation_ffmpeg.curve_expression` — mêmes
    polynômes que :func:`evaluate_transform`, bornés de la même façon. Le
    consommateur échappe les virgules (:func:`escape_filter_complex_commas`)
    avant injection dans ``-filter_complex``.
    """
    _validate_value(property_name, base_value)
    frames = [kf for kf in keyframes if kf.property_name == property_name]
    if not frames:
        return _format_number(float(base_value))
    spec = TRANSFORM_PROPERTIES[property_name]
    return curve_expression(
        AnimationCurve(frames, spec.kind), time_var=time_var, minimum=spec.minimum, maximum=spec.maximum
    )


def escape_filter_complex_commas(expression: str) -> str:
    """Échappe les virgules d'une expression pour ``-filter_complex``.

    Au sein de ``-filter_complex``, ``,`` sépare les options d'un
    filtre. Notre expression interpolée contient
    ``if(lt(T,t),A,B)`` qui inclut des virgules « utiles ». On les
    échappe toutes en ``\\,`` pour que FFmpeg les interprète
    littéralement comme une seule valeur d'option.

    Les virgules **déjà** échappées (``\\,``) sont préservées telles
    quelles pour ne pas doubler l'échappement.
    """
    escaped_chars: list[str] = []
    index = 0
    text = expression
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == ",":
            # Échappement déjà présent : on le préserve tel quel.
            escaped_chars.append(char)
            escaped_chars.append(",")
            index += 2
            continue
        if char == ",":
            escaped_chars.append("\\")
            escaped_chars.append(",")
            index += 1
            continue
        escaped_chars.append(char)
        index += 1
    return "".join(escaped_chars)


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _validate_value(property_name: str, value: float) -> None:
    """Lève ``ValueError`` si ``value`` est hors bornes pour la propriété."""
    if property_name not in _PROPERTY_BOUNDS:
        raise ValueError(f"Propriété inconnue : {property_name!r}.")
    low, high = _PROPERTY_BOUNDS[property_name]
    if value != value:  # NaN
        raise ValueError(f"Valeur NaN refusée pour {property_name!r}.")
    if value < low or value > high:
        raise ValueError(
            f"Valeur hors bornes pour {property_name!r} : "
            f"{value} (attendu [{low}, {high}])."
        )


def _coerce_value(property_name: str, value: float) -> float:
    """Valide puis retourne ``value``."""
    _validate_value(property_name, value)
    return float(value)


def _format_number(value: float) -> str:
    """Formate un flottant pour le filtergraph FFmpeg (pas de scientifique)."""
    if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf
        raise ValueError(f"Valeur non finie refusée pour FFmpeg : {value!r}.")
    if value == 0:
        return "0"
    text = f"{value:.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"
