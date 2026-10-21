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

MAX_COMMAND_BYTES = 32 * 1024 * 1024
"""Taille maximale du texte de commandes d'**une** animation (un effet, ou l'étalonnage d'un clip : tous fichiers). Mesuré (ADR-0008,
étapes 5 et 6) : FFmpeg 6.1.1 et 7.1.5 lisent un fichier de commandes avec une mémoire de l'ordre de 1000 fois sa taille ; les fenêtres
(:data:`WINDOW_BYTES`) bornent le pic, ce plafond borne le disque et le temps de lecture. Au-delà, ``ValueError`` (message sans donnée
de l'utilisateur) : le fichier partiel est supprimé avec les autres temporaires de l'export. Une table de 256 points de l'étalonnage
pèse ~4,7 Ko par image ; une instruction d'effet ~60 octets (plus de trois heures à 30 i/s, jamais atteint en pratique)."""

MAX_EFFECT_COMMANDS = 40_000
"""Commandes ``sendcmd`` au plus pour **un** effet animé (un flou ou un rayon de lueur en écrit deux par valeur, ``sigma`` et ``sigmaV`` ;
l'aberration chromatique, deux aussi, ``rh`` et ``bh``) : soit ~20 000 images, un peu plus de onze minutes à 30 i/s si la valeur change à
chaque image. Mesuré (ADR-0008, correctif de l'étape 5 ; ``tools/measure_sendcmd_memory.py``) : FFmpeg 6.1.1 et 7.1.5 gardent ~25 Ko de
mémoire **par commande**, tant que le filtre vit, quelle que soit la taille du fichier et même coupé en fenêtres (flou animé de dix minutes,
36 000 commandes : 959 Mo sur 6.1.1, 971 Mo sur 7.1.5, 44 Mo sur 9.0.2). 40 000 commandes restent donc autour du gigaoctet sur ces versions.
Au-delà, :func:`command_plan` lève ``ValueError`` avec la marche à suivre : un refus clair, jamais un rendu faux. Calculé au fil des
instructions **avant** d'écrire le moindre fichier."""

WINDOW_BYTES = 192 * 1024
"""Taille maximale du texte d'**un** fichier de commandes. Mesuré (ADR-0008, étapes 5 et 6 ; ``docs/ffmpeg-sendcmd.md``, piège 9) :
FFmpeg 6.1.1 et 7.1.5 lisent un fichier de commandes avec une mémoire de l'ordre de 1000 fois sa taille et croissante plus vite que
lui (un fichier de 3,7 Mo : 4,4 Go de mémoire résidente avant la première image ; la 9.0.2 : 19 Mo). La mémoire est **rendue** après
la lecture de chaque fichier : un plan long s'écrit donc **une fenêtre par fichier** (autant de ``sendcmd`` à la suite, voir
:func:`load_filters`) et le pic ne dépend plus de la durée du clip, seulement de la fenêtre."""

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
    """Les commandes d'un effet : une instruction par ligne (``rows``, bornées par :data:`MAX_COMMAND_BYTES`), la valeur de la
    première image (celle de l'option statique du filtre) et la plus grande valeur écrite (``0`` : l'effet ne fait jamais rien)."""

    rows: tuple[str, ...]
    initial: float
    peak: float

    @property
    def text(self) -> str:
        return "\n".join(self.rows) + "\n"

    @property
    def lines(self) -> int:
        return len(self.rows)


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
        raise ValueError("Animation d'effet trop longue pour l'export : raccourcissez-la ou découpez le clip.")
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
    size = 0
    commands = 0
    per_value = 2                                  # ``sigma`` + ``sigmaV``, ou ``rh`` + ``bh``
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
        line = f"{instant:.9f} {_commands(effect.type, target, value)};"
        size += len(line) + 1
        commands += per_value
        if commands > MAX_EFFECT_COMMANDS or size > MAX_COMMAND_BYTES:      # le plan complet est vérifié avant d'écrire le moindre fichier
            raise ValueError("Animation d'effet trop longue pour l'export : raccourcissez-la, gardez une valeur fixe sur une "
                             "partie du clip ou découpez le clip.")
        lines.append(line)
    return CommandPlan(tuple(lines), initial, peak)


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


def write_command_stream(lines: Iterable[str]) -> str:
    """Comme :func:`write_command_file`, mais le texte est écrit **ligne à ligne** (un générateur) : un fichier de plusieurs dizaines de
    mégaoctets (étalonnage animé, ADR-0008 étape 6) n'existe jamais en entier en mémoire. Chaque ligne reçoit son saut de ligne.

    Dans une construction d'export (:func:`command_files` avec une liste) le chemin est ajouté à la liste **avant** d'écrire : si le
    générateur lève (borne de taille), le fichier partiel est supprimé avec les autres temporaires. Hors construction : fichier
    temporaire puis renommage vers un nom tiré du contenu (le condensat se calcule en écrivant)."""
    sink = _SINK.get()
    if sink is not None:
        descriptor, path = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".cmd")
        sink.append(path)
        with os.fdopen(descriptor, "wb") as handle:
            for line in lines:
                handle.write(line.encode("utf-8") + b"\n")
        return path
    digest = hashlib.sha256()
    descriptor, partial = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".part", dir=_process_directory())
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for line in lines:
                data = line.encode("utf-8") + b"\n"
                digest.update(data)
                handle.write(data)
        path = os.path.join(_process_directory(), digest.hexdigest()[:40] + ".cmd")
        os.replace(partial, path)
    except BaseException:
        if os.path.exists(partial):
            os.remove(partial)
        raise
    return path


def windows(lines: Iterable[str], budget: int | None = None) -> Iterator[list[str]]:
    """``lines`` découpées en fenêtres d'au plus ``budget`` octets (une ligne démesurée a sa propre fenêtre) ; jamais de fenêtre vide
    (un plan sans aucune ligne n'écrit rien). Une seule fenêtre est en mémoire à la fois. ``budget`` : :data:`WINDOW_BYTES` par défaut."""
    budget = WINDOW_BYTES if budget is None else budget
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > budget:
            yield current
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        yield current


def load_filters(lines: Iterable[str]) -> str:
    """Les filtres ``sendcmd`` (séparés par des virgules) qui lisent ``lines``, une **fenêtre** de :data:`WINDOW_BYTES` par fichier
    (voir cette constante) ; chaîne vide si ``lines`` est vide. Les instants restent ceux du plan entier : chaque fenêtre reçoit ses
    lignes dans l'ordre, ``sendcmd`` envoie la commande quand ``PTS`` passe l'instant, quel que soit le fichier. Un plan qui tient
    dans une fenêtre donne le même fichier et le même filtre qu'avant le découpage."""
    return ",".join(sendcmd_filter(write_command_stream(window)) for window in windows(lines))


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
    "MAX_COMMAND_BYTES",
    "MAX_COMMAND_LINES",
    "MAX_EFFECT_COMMANDS",
    "WINDOW_BYTES",
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
    "load_filters",
    "sendcmd_filter",
    "windows",
    "write_command_file",
    "write_command_stream",
]
