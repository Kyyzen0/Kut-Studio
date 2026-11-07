"""Remplaçant LGPL du filtre ``eq`` d'FFmpeg (ADR-0013 : un FFmpeg embarqué sans ``--enable-gpl``).

``eq`` (luminosité, contraste, saturation) est **GPL** (``eq_filter_deps="gpl"`` dans le ``configure`` de FFmpeg 6.1, 7.1 et
8.0) : il n'existe pas dans un FFmpeg LGPL. Ce module le remplace par le filtre ``lut`` (``vf_lut.c``, LGPL), qui applique une
table de 256 niveaux par plan. Ce n'est pas une approximation : ``eq`` n'est lui-même qu'**une table par plan** sur du YUV 8 bits,
et sa formule entière (héritée de MPlayer, ``process_c`` de ``vf_eq.c``) se recalcule exactement :

- luma : ``Y' = ⌊Y·C / 4096⌋ + B`` avec ``C = int(contraste·4096)`` et ``B = int(100·luminosité + 100)·511 // 200 − 128 − C // 32`` ;
- chroma (U et V) : la même formule avec ``C = int(saturation·4096)`` et une luminosité nulle ;
- sortie ramenée à ``[0, 255]`` ; un plan est laissé intact quand son contraste vaut 1 et sa luminosité 0.

Les trois réglages sont bornés comme le fait ``eq`` (luminosité ``[-1, 1]``, saturation ``[0, 3]``, contraste ``[-1000, 1000]``) et
passés par un ``float32`` (``av_clipf``), ce qui change la troncature de ``int(c·4096)`` à la frontière. Au-delà de ``|contraste| ≥ 7,9``,
``eq`` quitte le chemin entier pour une table flottante : elle est reproduite aussi (le modèle borne le contraste à 2 ; la branche
sert à rester exact quoi qu'on lui passe).

**Mêmes formats que ``eq``.** ``eq`` n'accepte que le YUV planaire 8 bits (et le gris) : FFmpeg lui convertit tout le reste,
y compris les sources 10 bits. ``lut`` accepterait ces formats tels quels et la table 8 bits serait fausse : un ``format=`` avec la
liste d'``eq`` précède donc chaque ``lut`` (:data:`EQ_FORMATS`) ; il est sans effet quand le flux est déjà dans cette liste.

**Pièges de ``lut`` (mesurés).** Les options ``y``, ``u``, ``v`` valent ``clipval`` par défaut, qui **borne** à ``[16, 235]`` (luma) et
``[16, 240]`` (chroma) : un plan que l'on ne règle pas reçoit donc ``val`` explicitement, sinon il serait écrêté. Et la table est
recalculée (256 × 256 évaluations par composante) à chaque commande reçue : l'animation (``sendcmd``) n'envoie que les plans qui
changent.

**Animation.** ``eq`` relisait des expressions du temps à chaque image (``eval=frame``). ``lut`` n'évalue ses options qu'à la
configuration et à chaque commande : l'animation passe par un fichier de commandes ``sendcmd`` (:mod:`core.effect_sendcmd`), une
instruction par image où un plan change, avec les valeurs de la courbe évaluées en Python comme celles de l'inspecteur.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .animation import AnimationCurve, ValueKind
from .effect_sendcmd import (
    MAX_COMMAND_BYTES,
    MAX_EFFECT_COMMANDS,
    SendcmdContext,
    check_command_budget,
    frame_count,
    load_filters,
)
from .effects_model import ClipEffect, fx_property_id

EQ_FORMATS = "gray|yuv410p|yuv411p|yuv420p|yuva420p|yuv422p|yuva422p|yuv444p|yuva444p"
"""Formats que ``eq`` accepte (``pixel_fmts_eq``, identique dans FFmpeg 6.1, 7.1 et 8.0)."""

FORMAT_FILTER = f"format={EQ_FORMATS}"
"""Le filtre ``format`` qui donne à ``lut`` la négociation de formats d'``eq``."""

IDENTITY = "val"
"""Expression d'un plan laissé intact (jamais ``clipval``, qui écrête : voir plus haut)."""

INTEGER_CONTRAST_LIMIT = 7.9
"""``eq`` calcule en entiers tant que ``|contraste| < 7,9`` (le facteur tient alors dans un ``short`` de l'assembleur x86)."""

_PLANES = ("y", "u", "v")
_HALF_FRAME = Fraction(1, 2)


def _float32(value: float) -> float:
    """``value`` arrondie à un ``float32`` (``eq`` garde ses réglages en ``av_clipf``)."""
    return float(struct.unpack("f", struct.pack("f", value))[0])


def _clip(value: float, low: float, high: float, neutral: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return neutral
    if not math.isfinite(number):
        return neutral
    return _float32(max(low, min(high, number)))


def _c_div(numerator: int, denominator: int) -> int:
    """Division entière du C (troncature vers zéro)."""
    return int(numerator / denominator)


def plane_expression(contrast: float, brightness: float) -> str:
    """Expression ``lut`` d'un plan d'``eq`` de ``contrast`` et ``brightness`` (déjà bornés et arrondis en ``float32``)."""
    if contrast == 1.0 and brightness == 0.0:
        return IDENTITY
    if abs(contrast) < INTEGER_CONTRAST_LIMIT:
        factor = int(contrast * 256 * 16)
        offset = _c_div(int(100.0 * brightness + 100.0) * 511, 200) - 128 - _c_div(factor, 32)
        return f"'clip(floor(val*{factor}/4096){offset:+d},0,255)'"
    return f"'clip(floor(256*({contrast!r}*(val/255-0.5)+0.5{brightness:+})),0,255)'"


def eq_planes(brightness: float, contrast: float, saturation: float) -> tuple[str, str, str]:
    """``(y, u, v)`` : les expressions ``lut`` des trois plans pour ces réglages d'``eq``."""
    luma = plane_expression(_clip(contrast, -1000.0, 1000.0, 1.0), _clip(brightness, -1.0, 1.0, 0.0))
    chroma = plane_expression(_clip(saturation, 0.0, 3.0, 1.0), 0.0)
    return luma, chroma, chroma


def is_neutral(planes: Iterable[str]) -> bool:
    return all(plane == IDENTITY for plane in planes)


def lut_options(planes: tuple[str, str, str]) -> str:
    return ":".join(f"{name}={plane}" for name, plane in zip(_PLANES, planes))


def static_filters(brightness: float, contrast: float, saturation: float) -> str:
    """Les filtres d'une correction **fixe** : ``format=…,lut=…``, ou le seul ``format=…`` au neutre (``eq`` neutre ne touchait
    pas aux pixels mais imposait ses formats : la conversion reste la même)."""
    planes = eq_planes(brightness, contrast, saturation)
    if is_neutral(planes):
        return FORMAT_FILTER
    return f"{FORMAT_FILTER},lut={lut_options(planes)}"


def stage_filters(name: str, planes: tuple[str, str, str]) -> str:
    """Les filtres d'une correction **animée** : l'instance nommée ``name`` (``lut@v0fx0``), réglée sur les tables de la première
    image ; les instructions de ``sendcmd`` la visent par ce nom. Toujours émise, y compris au neutre."""
    return f"{FORMAT_FILTER},{name}={lut_options(planes)}"


def instance_name(label: str) -> str:
    """Nom d'instance de ``lut`` pour l'étiquette ``label`` du graphe (caractères sûrs seulement)."""
    return f"lut@{re.sub(r'[^0-9A-Za-z_]', '_', label) or 'eq'}"


def commands(target: str, planes: tuple[str, str, str], previous: tuple[str, str, str] | None) -> list[str]:
    """Les instructions ``sendcmd`` qui font passer ``target`` de ``previous`` à ``planes`` (seuls les plans qui changent ; toutes
    quand ``previous`` est ``None``). Une expression avec virgules est citée entre apostrophes dans la table, comme dans le graphe."""
    return [
        f"{target} {name} {plane}"
        for index, (name, plane) in enumerate(zip(_PLANES, planes))
        if previous is None or previous[index] != plane
    ]


@dataclass(frozen=True)
class EqPlan:
    """Le fichier de commandes d'une correction animée : une ligne par image où un plan change, et les plans de la première image
    (ceux des options statiques du filtre)."""

    rows: tuple[str, ...]
    initial: tuple[str, str, str]


def effect_values(effect: ClipEffect, animation: Iterable[Any]) -> Callable[[float], tuple[float, float, float]] | None:
    """``temps local -> (luminosité, contraste, saturation)`` d'une correction colorimétrique : la valeur de base, remplacée par la
    courbe ``fx.<effet>.<paramètre>`` quand elle a des images clés (évaluée et bornée comme l'inspecteur). ``None`` si rien n'est animé."""
    from .effect_targets import ANIMATABLE_EFFECT_PARAMS, _fx_spec

    keyframes = list(animation)
    curves: dict[str, tuple[AnimationCurve, Any]] = {}
    for name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
        property_id = fx_property_id(effect.id, name)
        frames = [kf for kf in keyframes if kf.property_name == property_id]
        spec = _fx_spec(effect, name, property_id) if frames and name in effect.params else None
        if spec is not None:
            curves[name] = (AnimationCurve(frames, ValueKind.FLOAT), spec)

    if not curves:
        return None

    def value(name: str, local: float) -> float:
        base = float(effect.params[name])
        if name not in curves:
            return base
        curve, spec = curves[name]
        return float(spec.evaluate(curve, base, local))

    return lambda local: (value("brightness", local), value("contrast", local), value("saturation", local))


def plan(values_at: Callable[[float], tuple[float, float, float]], context: SendcmdContext, target: str) -> EqPlan:
    """Plan de commandes de ``target`` : ``values_at(temps local)`` rend ``(luminosité, contraste, saturation)`` à l'image visée.

    Mêmes règles que les autres étages ``sendcmd`` (:mod:`core.effect_sendcmd`) : une instruction par image où un plan change,
    écrite une demi-image avant l'image visée. ``ValueError`` (sans donnée de l'utilisateur) au-delà de
    :data:`~core.effect_sendcmd.MAX_EFFECT_COMMANDS` instructions ou de :data:`~core.effect_sendcmd.MAX_COMMAND_BYTES` octets, avant
    d'écrire quoi que ce soit.
    """
    rows: list[str] = []
    size = 0
    total = 0
    previous: tuple[str, str, str] | None = None
    initial: tuple[str, str, str] | None = None
    for index in range(frame_count(context)):
        planes = eq_planes(*values_at(context.start + float(Fraction(index) / context.fps)))
        if initial is None:
            initial = planes
        if planes == previous:
            continue
        sent = commands(target, planes, previous)
        previous = planes
        total += len(sent)
        instant = max(0.0, context.start + float((Fraction(index) - _HALF_FRAME) / context.fps))
        line = f"{instant:.9f} " + ", ".join(sent) + ";"
        size += len(line) + 1
        check_command_budget(total, size, MAX_EFFECT_COMMANDS, MAX_COMMAND_BYTES)
        rows.append(line)
    if initial is None:  # pragma: no cover - frame_count rend au moins deux images
        raise ValueError("Animation d'effet vide.")
    return EqPlan(tuple(rows), initial)


def effect_stage(effect: ClipEffect, animation: Iterable[Any] | None, context: SendcmdContext | None, label: str) -> str | None:
    """Les filtres d'une correction colorimétrique dont un réglage a des images clés : le(s) ``sendcmd`` qui lisent les commandes, puis
    ``format=…,lut@<label>=…``. ``None`` si rien n'est animé ou si le flux n'est pas connu (``context`` ``None`` : la chaîne
    statique, sur les valeurs de base, est alors celle d'avant)."""
    if context is None or not animation:
        return None
    values_at = effect_values(effect, animation)
    if values_at is None:
        return None
    name = instance_name(label)
    eq_plan = plan(values_at, context, name)
    return f"{load_filters(eq_plan.rows)},{stage_filters(name, eq_plan.initial)}"


__all__ = [
    "EQ_FORMATS",
    "FORMAT_FILTER",
    "IDENTITY",
    "EqPlan",
    "commands",
    "effect_stage",
    "effect_values",
    "eq_planes",
    "instance_name",
    "is_neutral",
    "lut_options",
    "plane_expression",
    "plan",
    "stage_filters",
    "static_filters",
]
