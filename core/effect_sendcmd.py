"""Paramètres d'effet animés rendus par un fichier de commandes ``sendcmd`` (ADR-0008, étape 5).

Trois paramètres n'acceptent pas d'expression du temps mais se laissent changer en cours de route par ``sendcmd`` (validé
sur FFmpeg 6.1.1, 7.1.5 et 9.0.2, écart 0 : ``docs/ffmpeg-sendcmd.md``) :

- le **flou** (``blur.intensity``, filtre ``gblur``) : options ``sigma`` **et** ``sigmaV`` (les deux sont requises : ``sigmaV``
  est résolu à l'initialisation) ;
- le **rayon de la lueur** (``glow.radius``, le ``gblur`` du bloom) : mêmes options ;
- l'**aberration chromatique** (``chromatic_aberration.intensity``, ``rgbashift``) : ``rh`` et ``bh`` (entiers, opposés).

Règles de génération (mesurées, voir la doc) :

- une instruction **par image** du clip, écrite une **demi-image avant** l'image visée (``(n - 0,5) / cadence``, 0 au
  plus tôt) : à l'instant exact, une image sur trois recevait la valeur de la précédente ;
- les instants sont ceux de ``PTS`` à l'entrée de ``sendcmd`` : le graphe a remis ``PTS`` au **temps local du clip** avant
  les effets (``setpts=PTS-STARTPTS`` plus le temps déjà écoulé à l'origine d'un segment d'aperçu), comme pour les
  expressions en ``t`` ;
- chaque valeur est celle que lit l'inspecteur et le moniteur : ``AnimatableProperty.evaluate`` (courbe linéaire, Bézier,
  maintien ; bornée aux bornes du modèle), puis la même conversion que la chaîne statique (``× pixel_scale``, arrondi
  entier de l'aberration, plancher de 0,01 du rayon de la lueur) ;
- une ligne n'est écrite que si une valeur change (un maintien ne coûte qu'une instruction) ;
- le filtre visé porte un **nom** (``gblur@<étiquette>``) : plusieurs effets animés d'un même graphe ne se confondent pas.

Le fichier est écrit dans le dossier temporaire de l'export (:func:`command_files`) et supprimé avec les autres fichiers
temporaires ; son chemin est cité dans le graphe par :func:`sendcmd_filter`, qui l'échappe (lettre de lecteur Windows,
apostrophes, espaces, virgules). Sans image clé, ce module n'est jamais appelé : la chaîne statique est celle d'avant,
caractère pour caractère.
"""

from __future__ import annotations

import atexit
import hashlib
import math
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .animation import AnimationCurve, ValueKind
from .effects_model import ClipEffect, EffectType, fx_property_id

MAX_COMMAND_LINES = 2_000_000
"""Instructions au plus par fichier (un clip de plus de 5 h à 100 i/s n'y tient pas) : borne posée **avant** d'allouer le texte.
Au-delà, :func:`command_plan` lève ``ValueError`` (message sans donnée de l'utilisateur)."""

_TEMP_PREFIX = "kut-studio-sendcmd-"
_HALF_FRAME = Fraction(1, 2)
_FORBIDDEN_PATH = re.compile(r"[\x00-\x1f\x7f]")

_SINK: ContextVar[list[str] | None] = ContextVar("kut_sendcmd_files", default=None)
_FALLBACK_DIR: list[str] = []


@dataclass(frozen=True)
class SendcmdContext:
    """Ce que le constructeur de chaîne sait du flux qui entre dans ``sendcmd``.

    ``fps`` : cadence de l'image, exacte (``Fraction``, 30000/1001 reste 30000/1001) ; ``start`` : temps local du clip de la
    **première** image du flux (0 pour un clip entier, le temps déjà écoulé pour un segment d'aperçu) ; ``duration`` :
    durée du clip sur la timeline, en secondes."""

    fps: Fraction
    start: float
    duration: float


@dataclass(frozen=True)
class CommandPlan:
    """Le fichier de commandes d'un effet : son texte, la valeur de la première image (celle de l'option statique du filtre)
    et la plus grande valeur écrite (``0`` : l'effet ne fait jamais rien)."""

    text: str
    initial: float
    peak: float
    lines: int


def frame_rate(fps: float) -> Fraction:
    """Cadence exacte : la fraction NTSC reconnue (``29.97`` donne 30000/1001), sinon le décimal ramené à une fraction courte."""
    from .timecode import FrameRate

    known = FrameRate.try_from_fps(fps)
    if known is not None:
        return Fraction(known.fps)
    return Fraction(float(fps)).limit_denominator(100_000)


def segment_start(skip: float, fps: float) -> float:
    """Temps local (secondes) de la **première image réelle** d'un segment d'aperçu qui commence ``skip`` secondes après le début
    du clip.

    Le graphe recale ``PTS`` par ``setpts=PTS-STARTPTS+skip/TB`` après ``fps``, et ``setpts`` **tronque** vers zéro le résultat
    (``(int64_t)d``) : les images sont sur la grille des ticks (``1/cadence``), la première à ``trunc(skip / TB)``, et non à
    ``skip``. Hors grille (``skip = 0,25 s`` à 10 i/s), c'est le tick d'avant ; sur la grille, l'erreur d'arrondi de la division en
    flottants (``0,3 / 0,1 = 2,9999999999999996``) peut aussi la ramener d'un tick. Reproduit ici avec les mêmes flottants que
    FFmpeg (``av_q2d(TB) = num / den``, ``skip`` écrit à six décimales comme dans le graphe) : le fichier de commandes vise les
    images qui existent. Mesuré contre ``showinfo`` de FFmpeg (``tests/test_effect_sendcmd.py``)."""
    skip = float(f"{max(0.0, float(skip)):.6f}")
    if skip <= 0.0:
        return 0.0
    rate = frame_rate(fps)
    tick = math.trunc(skip / (rate.denominator / rate.numerator))
    return float(Fraction(tick) / rate)


def context_for(fps: float, start: float, duration: float) -> SendcmdContext:
    return SendcmdContext(frame_rate(fps), max(0.0, float(start)), max(0.0, float(duration)))


# ---------------------------------------------------------------------------
# Valeurs : exactement celles du modèle, puis la conversion de la chaîne statique
# ---------------------------------------------------------------------------


def _number(value: float) -> str:
    """Nombre pour une instruction : six décimales, zéros de queue retirés, jamais de notation scientifique."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-", "-0") else text


def _sigma(value: float, pixel_scale: float, floor: float) -> float:
    return max(floor, float(value) * pixel_scale)


def _shift(value: float, pixel_scale: float) -> int:
    return int(round(float(value) * pixel_scale))


_CONVERSIONS: dict[EffectType, tuple[str, Callable[[float, float], float]]] = {
    EffectType.BLUR: ("intensity", lambda value, scale: _sigma(value, scale, 0.0)),
    EffectType.GLOW: ("radius", lambda value, scale: _sigma(value, scale, 0.01)),
    EffectType.CHROMATIC_ABERRATION: ("intensity", lambda value, scale: float(_shift(value, scale))),
}


def _commands(kind: EffectType, target: str, value: float) -> str:
    """Les instructions d'une image : ``sigma`` **et** ``sigmaV`` pour ``gblur`` (les deux sont requises), ``rh`` / ``bh`` opposés
    pour ``rgbashift``."""
    if kind is EffectType.CHROMATIC_ABERRATION:
        shift = int(value)
        return f"{target} rh {shift}, {target} bh {-shift}"
    return f"{target} sigma {_number(value)}, {target} sigmaV {_number(value)}"


def frame_count(context: SendcmdContext) -> int:
    """Images du flux : jusqu'à la fin du clip, plus deux de marge (des instructions en trop sont sans effet)."""
    remaining = max(0.0, context.duration - context.start)
    count = math.ceil(remaining * float(context.fps)) + 2
    if count > MAX_COMMAND_LINES:
        raise ValueError("Animation d'effet trop longue pour l'export (trop d'images).")
    return max(2, count)


def command_plan(
    effect: ClipEffect, animation: Iterable[Any] | None, context: SendcmdContext, target: str, pixel_scale: float,
) -> CommandPlan | None:
    """Fichier de commandes de ``effect`` visant le filtre ``target`` ; ``None`` si rien n'est animé (le filtre statique
    reste alors celui d'avant)."""
    from .effect_targets import SENDCMD_EFFECT_PARAMS, _fx_spec

    spec_row = _CONVERSIONS.get(effect.type)
    if spec_row is None or not animation:
        return None
    name, convert = spec_row
    if name not in SENDCMD_EFFECT_PARAMS.get(effect.type, ()) or name not in effect.params:
        return None
    property_id = fx_property_id(effect.id, name)
    frames = [keyframe for keyframe in animation if keyframe.property_name == property_id]
    spec = _fx_spec(effect, name, property_id)
    if not frames or spec is None:
        return None
    curve = AnimationCurve(frames, ValueKind.FLOAT)
    base = float(effect.params[name])
    count = frame_count(context)
    fps = context.fps
    lines: list[str] = []
    previous: float | None = None
    initial = 0.0
    peak = 0.0
    for index in range(count):
        local = context.start + float(Fraction(index) / fps)
        value = convert(float(spec.evaluate(curve, base, local)), pixel_scale)
        if index == 0:
            initial = value
        peak = max(peak, value)
        if value == previous:
            continue
        previous = value
        instant = max(0.0, context.start + float((Fraction(index) - _HALF_FRAME) / fps))
        lines.append(f"{instant:.9f} {_commands(effect.type, target, value)};")
    return CommandPlan("\n".join(lines) + "\n", initial, peak, len(lines))


# ---------------------------------------------------------------------------
# Fichier temporaire et citation dans le graphe
# ---------------------------------------------------------------------------


@contextmanager
def command_files(sink: list[str] | None) -> Iterator[None]:
    """Pendant la construction d'un graphe, les fichiers de commandes sont écrits dans un fichier temporaire **unique** dont le
    chemin est ajouté à ``sink`` (la liste des temporaires de l'export : supprimés avec les autres, y compris sur un échec).
    ``None`` : le dossier de processus de :func:`write_command_file`."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def _process_directory() -> str:
    if not _FALLBACK_DIR or not os.path.isdir(_FALLBACK_DIR[0]):
        directory = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
        _FALLBACK_DIR[:] = [directory]
        atexit.register(shutil.rmtree, directory, True)
    return _FALLBACK_DIR[0]


def write_command_file(text: str) -> str:
    """Écrit ``text`` et rend son chemin.

    Dans une construction d'export ou d'aperçu (:func:`command_files` avec une liste) : un fichier temporaire propre à cet
    appel, ajouté à la liste. Hors de toute construction qui possède ses temporaires (outils, tests, bancs de mesure) : un
    fichier **nommé par son contenu** dans un dossier de processus supprimé à la sortie, jamais un fichier orphelin."""
    sink = _SINK.get()
    data = text.encode("utf-8")
    if sink is not None:
        descriptor, path = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".cmd")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        sink.append(path)
        return path
    path = os.path.join(_process_directory(), hashlib.sha256(data).hexdigest()[:40] + ".cmd")
    if not os.path.exists(path):
        partial = path + f".{os.getpid()}.part"
        with open(partial, "wb") as handle:
            handle.write(data)
        os.replace(partial, path)
    return path


def escape_command_path(path: str) -> str:
    """Chemin prêt à être cité entre apostrophes dans ``sendcmd=f='…'`` : séparateurs Windows en ``/``, ``:`` échappé
    (``C\\:/…`` : la lettre de lecteur n'est pas lue comme une option), apostrophe protégée (fermer, échapper, rouvrir).
    Un chemin avec un caractère de contrôle (saut de ligne, NUL) est refusé : il casserait la ligne du graphe."""
    from .export_engine import _escape_filter_path

    text = str(path)
    if not text or _FORBIDDEN_PATH.search(text):
        raise ValueError("Chemin de fichier de commandes invalide.")
    return _escape_filter_path(text)


def sendcmd_filter(path: str) -> str:
    """Le filtre ``sendcmd`` qui lit ``path`` (``f=`` en toutes lettres : sinon la lettre de lecteur Windows est lue comme
    l'option positionnelle)."""
    return f"sendcmd=f='{escape_command_path(path)}'"


def filter_name(kind: EffectType, label: str) -> str:
    """Nom d'instance du filtre visé (``gblur@v0fx0``) : l'étiquette du graphe, réduite aux caractères sûrs."""
    base = "rgbashift" if kind is EffectType.CHROMATIC_ABERRATION else "gblur"
    return f"{base}@{re.sub(r'[^0-9A-Za-z_]', '_', label) or 'fx'}"


__all__ = [
    "MAX_COMMAND_LINES",
    "CommandPlan",
    "SendcmdContext",
    "command_files",
    "command_plan",
    "context_for",
    "escape_command_path",
    "segment_start",
    "filter_name",
    "frame_count",
    "frame_rate",
    "sendcmd_filter",
    "write_command_file",
]
