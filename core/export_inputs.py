"""Entrées FFmpeg d'un export : une entrée positionnée par clip vidéo là où le partage coûte cher (défaut D-002 de la recette).

**Le problème (mesuré, ``docs/performance.md`` section 11).** Les clips d'un même média partageaient une seule entrée
FFmpeg, chacun lisant une fenêtre ``trim`` du même flux ; les instances d'une séquence imbriquée (les segments d'un montage
multicam) partageaient un seul rendu (``split``). Le premier cadre de la chaîne d'``overlay`` n'est émis qu'une fois reçue la
première image de **chaque** étage : FFmpeg décodait donc le flux jusqu'au clip qui commence le plus tard dans la source, et
gardait en mémoire, en RGBA pleine trame, tout ce que les autres clips lisent avant (un clip de 130 s coupé à 12 s : 3,3 Go ;
coupé à 60 s : plus de 7 Go ; 20 min de multicam : plus de 200 Go).

**Le correctif.**

* Chaque clip vidéo « simple » d'un média *chaud* (voir :data:`HOT_SECONDS`) a sa propre entrée, positionnée sur son début
  (``-ss`` avant ``-i``) et bornée (``-t``) : il ne décode que sa fenêtre.
* Chaque instance d'une séquence imbriquée *chaude* est composée **pour sa fenêtre seule** (:class:`NestedInstance`), comme un
  segment d'aperçu de la racine : le rendu ne commence qu'à son début et s'arrête avec elle.

La mémoire de FFmpeg ne dépend plus de la durée de média décodée avant le clip qui commence le plus tard. Elle dépend du
nombre d'entrées ouvertes (chacune garde ses décodeurs et ses premières images : environ :data:`PIPELINE_FRAMES` cadres RGBA),
d'où le plafond :func:`max_pipelines`.

**Parité de rendu.** Les images sont celles d'avant, au bit près (comparé image par image sur FFmpeg 6.1, 7.1 et 9) :

* le décalage de l'entrée est un nombre **entier de secondes**, donc un multiple exact de toute base de temps ``1/N`` : les
  comparaisons d'horodatages du ``trim`` donnent les mêmes images ; l'entrée démarre une seconde plus tôt que le clip
  (:data:`WARMUP_SECONDS`), le décodeur vidéo converge avant la première image gardée ;
* une instance qui ne démarre pas au début du clip garde son **temps absolu** : ``PTS`` moins l'horodatage de la première image du
  clip (:func:`first_frame_time`, sondé avec le ``ffprobe`` de l'installation), au lieu de remettre le temps à 0 sur sa première
  image gardée. C'est le même temps que le rendu depuis 0, sur toutes les versions de FFmpeg (6.1 et 7.1 décalent de +21 ms les
  images d'un conteneur dont l'audio commence avant la vidéo, pas 9 : le point d'entrée seul ne suffit pas).

**Ce qui reste partagé.** Les conteneurs dont l'index des images clés n'est pas fiable (:data:`POSITIONABLE_SUFFIXES` : MPEG-TS
et le reste) ; le **son** n'est jamais positionné : le décodeur AAC tire son bruit de substitution (PNS) d'un état
aléatoire qui dépend de tous les paquets déjà décodés, donc un décodage qui part du milieu du fichier diffère de quelques
échantillons du décodage continu (mesuré : jusqu'à 0,012 sur un signal de 0,3) ; il n'en a pas besoin, ``amix`` lisant au fil de l'eau (mesuré : 61 Mo pour un média de
20 min coupé à 5, 10 ou 15 min). Restent aussi partagés les images fixes, les clips
remappés (vitesse, courbe, marche arrière, arrêt sur image : leur étage de temps lit la source entière), les listes d'images
``.ffconcat``, les séquences imbriquées qui contiennent autre chose que des clips de fichier simples, et les médias à usage
unique ou sans fenêtre éloignée (froids : le graphe est alors celui d'avant, au caractère près).
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

LOGGER = logging.getLogger("kut_studio.export_inputs")

HOT_SECONDS = 0.5
"""Durée de média (en secondes) que les autres clips d'un fichier lisent avant le clip qui commence le plus tard, au-delà de
laquelle le fichier est « chaud » : ses clips ont chacun leur entrée. Un fichier froid garde l'entrée partagée d'avant."""

WARMUP_SECONDS = 1
"""Secondes décodées avant le premier échantillon gardé (convergence du décodeur après le positionnement)."""

TAIL_SECONDS = 1.0
"""Marge gardée après la fin du clip : la coupe exacte reste faite par ``trim``, comme avant."""

INSTANCE_LEAD = 0.1
"""Secondes de média gardées avant le début d'une instance : le filtre ``fps`` doit voir une image antérieure à la première
image voulue pour choisir, aux instants suivants, les mêmes images que le rendu depuis 0."""

INSTANCE_TAIL = 0.5
"""Secondes rendues après la fin de la fenêtre d'une instance : FFmpeg 6.1 perd la dernière image d'un rendu imbriqué (voir
``tests/test_sequences_export.py``) ; avec une marge, c'est une image dont personne n'a besoin."""

PIPELINE_FRAMES = 9
"""Cadres RGBA que garde une entrée ouverte (décodeurs, premières images de sa chaîne et de l'``overlay`` qui l'attend) :
mesuré à 70-120 Mo en 1080p sous FFmpeg 9 (``docs/performance.md``)."""

PIPELINE_BUDGET_BYTES = 6 * 1024**3
"""Mémoire qu'on accepte de consacrer aux entrées positionnées d'une commande."""

MIN_PIPELINES = 8
MAX_PIPELINES = 96
"""Bornes du nombre d'entrées positionnées : chaque entrée ouvre un fichier (descripteurs limités à 256 par défaut sous macOS)."""

_MAX_SECONDS = 1.0e7
"""Borne des valeurs acceptées : une valeur non finie ou démesurée n'est jamais positionnée."""

POSITIONABLE_SUFFIXES = (".mp4", ".m4v", ".mov", ".qt", ".3gp", ".3g2", ".mkv", ".webm")
"""Conteneurs dont l'index des images clés est fiable : un positionnement y tombe sur une image clé et rend les mêmes images que
le décodage continu. MPEG-TS (``.ts``, ``.mts``, ``.m2ts`` des caméscopes) déduit ses points d'accès du flux : sur FFmpeg 6.1 et 7.1
le décodage reprend sur une image qui n'est pas de rafraîchissement (« co located POCs unavailable ») et les images diffèrent. Les
autres conteneurs (AVI, MPEG-PS, FLV, flux bruts…) gardent l'entrée partagée, faute de mesure."""


def positionable(path: str) -> bool:
    """Le fichier se lit-il, à partir d'un point, comme en continu (voir :data:`POSITIONABLE_SUFFIXES`) ?"""
    return str(path).lower().endswith(POSITIONABLE_SUFFIXES)


def max_pipelines(width: int, height: int) -> int:
    """Nombre d'entrées positionnées (et d'instances) qu'une commande à ``width × height`` se permet."""
    frame = max(1, int(width)) * max(1, int(height)) * 4
    return int(max(MIN_PIPELINES, min(MAX_PIPELINES, PIPELINE_BUDGET_BYTES // (PIPELINE_FRAMES * frame))))


class SeekedPath(str):
    """Chemin d'un média lu à partir de ``seek_start`` pendant ``seek_duration`` secondes.

    C'est un ``str`` (le chemin) : tout code qui ne connaît que les chemins (décodage matériel, nombre de fils) continue de
    fonctionner ; :func:`core.export_engine.input_arguments` y lit la fenêtre pour poser ``-ss`` / ``-t`` devant ``-i``.
    """

    seek_start: int
    seek_duration: float

    def __new__(cls, path: str, seek_start: int, seek_duration: float) -> SeekedPath:
        item = super().__new__(cls, path)
        item.seek_start = int(seek_start)
        item.seek_duration = float(seek_duration)
        return item


@dataclass(frozen=True)
class _Window:
    path: str
    start: float               # premier instant gardé de la source (skip d'aperçu compris)
    end: float
    seek: int
    duration: float
    positionable: bool = True  # le conteneur se positionne sans changer les images (:data:`POSITIONABLE_SUFFIXES`)


@dataclass(frozen=True)
class NestedInstance:
    """Un rendu vidéo de séquence imbriquée composé **pour une seule fenêtre** de lecture (``start`` → ``end``, temps de la
    séquence).

    Les instances d'une séquence imbriquée partageaient un seul rendu (``split``), dont chaque lecteur coupait une fenêtre :
    même défaut que pour les fichiers (le rendu est décodé jusqu'à la fenêtre la plus tardive, les autres attendent en RGBA
    pleine trame). Chaque instance compose maintenant sa fenêtre seule, à partir de son début (``origin``) et jusqu'à sa fin.
    """

    tag: str
    start: float
    end: float


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and 0.0 <= value <= _MAX_SECONDS


def _plain_video(layer) -> bool:
    from .export_engine import FreezeFrameMode, _retime_map_of

    return not (getattr(layer, "still", False) or layer.time_remapping.freeze_mode == FreezeFrameMode.FREEZE
                or _retime_map_of(layer) is not None)


def _video_window(layer, origin: float, limit: float | None = None, lead: float = 0.0) -> _Window | None:
    """Fenêtre source d'un clip vidéo simple, ou ``None`` (clip qui garde l'entrée partagée).

    ``origin`` : début du rendu de la composition (segment d'aperçu, instance) ; ``limit`` : instant de la composition au-delà
    duquel rien n'est lu (instance) ; ``lead`` : secondes gardées avant la première image voulue (instance)."""
    path = getattr(layer, "source_path", "")
    if getattr(layer, "nested_key", "") or not path or str(path).endswith(".ffconcat") or not _plain_video(layer):
        return None
    skip = max(0.0, origin - layer.timeline_start) if origin > 0.0 else 0.0
    start = float(layer.source_in) + skip
    if skip > 0.0 and lead > 0.0:
        start = max(float(layer.source_in), start - lead)
    end = float(layer.source_out)
    if limit is not None and _finite(limit):
        end = min(end, float(layer.source_in) + max(0.0, limit - float(layer.timeline_start)))
    if not (_finite(start) and _finite(end)) or end <= start:
        return None
    seek = max(0, int(math.floor(start)) - WARMUP_SECONDS)
    return _Window(str(path), start, end, seek, end - seek + TAIL_SECONDS, positionable(str(path)))


def held_seconds(windows) -> float:
    """Durée de média que les clips d'une famille lisent avant le plus tardif (la loi de ``core.export_memory``) ; ``windows`` :
    couples ``(début, fin)`` en secondes de source."""
    items = list(windows)
    if len(items) < 2:
        return 0.0
    latest = max(start for start, _end in items)
    return sum(max(0.0, min(end, latest) - start) for start, end in items)


class InputSeeks:
    """Entrées positionnées et instances d'un graphe : décisions prises d'avance, indices attribués à la demande.

    ``input_paths`` est la liste des entrées de la commande : une entrée positionnée y est ajoutée au premier clip qui la
    demande (un plan imbriqué que personne ne lit n'ouvre donc rien). ``tag`` désigne la composition qui lit le clip : ``""``
    pour le plan racine et les rendus partagés, ``<clé>#<n>`` pour la n-ième instance d'une séquence imbriquée (un même clip y
    est lu à des fenêtres différentes).
    """

    def __init__(self, input_paths: list[str]) -> None:
        self._input_paths = input_paths
        self._windows: dict[tuple[int, str], _Window] = {}
        self._indices: dict[tuple[str, int, float], int] = {}
        self.splits: dict[str, list[NestedInstance]] = {}
        """Séquences imbriquées dont la vidéo est composée par instance : clé -> instances (ordre de première lecture)."""
        self.readers: dict[int, int] = {}
        """``id`` d'une couche vidéo racine -> indice de l'instance qu'elle lit."""
        self.bases: dict[int, float] = {}
        """``id`` d'une couche vidéo interne d'une instance -> horodatage de la première image de son clip."""

    def __bool__(self) -> bool:
        return bool(self._windows) or bool(self.splits)

    def video(self, layer, tag: str = "") -> tuple[int | None, float]:
        window = self._windows.get((id(layer), tag))
        if window is None:
            return None, 0.0
        key = (window.path, window.seek, round(window.duration, 6))
        index = self._indices.get(key)
        if index is None:
            index = self._indices[key] = len(self._input_paths)
            self._input_paths.append(SeekedPath(window.path, window.seek, window.duration))
        return index, float(window.seek)


# --- Sonde -----------------------------------------------------------------------------------------------------------


def _round_near(value: Fraction) -> int:
    """Arrondi au plus proche, les demis s'éloignant de zéro (``av_rescale_q`` de FFmpeg)."""
    return int(math.floor(value + Fraction(1, 2))) if value >= 0 else -int(math.floor(-value + Fraction(1, 2)))


@functools.lru_cache(maxsize=512)
def _first_frame_time_cached(path: str, mtime_ns: int, size: int, at: float) -> float | None:
    from .process_supervisor import supervised_run
    from .tool_paths import find_media_tool

    tool = find_media_tool("ffprobe")
    if tool is None:
        return None
    try:
        done = supervised_run(
            [tool, "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{max(0.0, at - 1.0):.6f}%{at + 3.0:.6f}",
             "-show_entries", "stream=time_base:format=start_time:packet=pts", "-of", "json", "--", path],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if done.returncode != 0:
            return None
        data = json.loads(done.stdout)
        numerator, denominator = str(data["streams"][0]["time_base"]).split("/")
        base = Fraction(int(numerator), int(denominator))
        start_us = round(float(data.get("format", {}).get("start_time", 0.0) or 0.0) * 1e6)
        offset = _round_near(Fraction(start_us, 10**6) / base)                  # FFmpeg décale tout de -start_time
        first = _round_near(Fraction(round(at * 1e6), 10**6) / base)            # ``trim`` : av_rescale_q(début, µs, base)
        shown = [int(item["pts"]) - offset for item in data.get("packets", ()) if item.get("pts") not in (None, "N/A")]
        wanted = [value for value in shown if value >= first]
        return float(Fraction(min(wanted)) * base) if wanted else None
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, subprocess.SubprocessError):
        LOGGER.debug("Sonde de première image impossible : %s", path, exc_info=True)
        return None


def first_frame_time(path: str, at: float) -> float | None:
    """Horodatage (secondes, tel que ``trim`` le voit) de la première image du fichier à ``at`` ou après ; ``None`` : sonde
    impossible.

    Le graphe historique remet le temps du clip à 0 sur **sa première image gardée** (``setpts=PTS-STARTPTS``). Une instance
    qui ne démarre pas au début du clip ne voit pas cette image : elle soustrait à la place son horodatage, sondé ici. Il peut
    différer du point d'entrée, et d'une version de FFmpeg à l'autre : un conteneur dont l'audio commence avant la vidéo
    (amorce AAC à -21 ms) voit toutes ses images décalées de +21 ms par FFmpeg 6.1 et 7.1, pas par 9."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return _first_frame_time_cached(str(path), info.st_mtime_ns, info.st_size, round(float(at), 6))


# --- Séquences imbriquées par instance ---------------------------------------------------------------------------------


def _instance_bases(inner) -> dict[int, float] | None:
    """Pour une séquence imbriquée qui ne contient que des clips de fichier simples (on peut la composer par fenêtre sans que
    rien d'autre : flux de calques, mattes, fusions, dépende de la position du rendu) : ``id(couche) -> horodatage de la première
    image du clip`` (:func:`first_frame_time`). ``None`` : la séquence n'est pas composable par instance."""
    from .color_render import windowed_nodes

    if inner.graphics_layers or inner.transitions or inner.subtitle_cues or getattr(inner, "nested_sequences", ()):
        return None
    bases: dict[int, float] = {}
    for layer in inner.video_layers:
        compositing = layer.compositing
        if layer.nested_key or not layer.source_path or not _plain_video(layer) or not positionable(str(layer.source_path)):
            return None
        if layer.animation:
            return None                   # paramètres animés par sendcmd : leur temps de départ suit la première image gardée
        if compositing is not None and compositing.masks:
            return None
        if windowed_nodes(layer.color_grade):
            return None
        first = first_frame_time(str(layer.source_path), float(layer.source_in))
        if first is None:
            return None
        bases[id(layer)] = first
    return bases


def _video_readers(plan, origin: float) -> dict[str, list[tuple[object, float, float]]]:
    """Couches vidéo racine qui lisent chaque séquence imbriquée : ``clé -> [(couche, début, fin)]`` (fenêtre en temps de la
    séquence), sans les clés lues autrement (par une autre séquence, une composition, un clip remappé)."""
    readers: dict[str, list[tuple[object, float, float]]] = {}
    refused: set[str] = set()
    for entry in getattr(plan, "nested_sequences", ()) or ():
        for layer in list(entry.plan.video_layers) + list(entry.plan.audio_layers):
            if getattr(layer, "nested_key", ""):
                refused.add(layer.nested_key)
        composition = getattr(entry, "composition", None)
        if composition is not None:
            refused.add(entry.key)
            refused.update(key for _node, key in composition.sources)
    video_origin = float(origin) if origin and origin > 0 else 0.0
    for layer in plan.video_layers:
        key = getattr(layer, "nested_key", "")
        if not key:
            continue
        if not _plain_video(layer):
            refused.add(key)
            continue
        skip = max(0.0, video_origin - layer.timeline_start) if video_origin > 0.0 else 0.0
        readers.setdefault(key, []).append((layer, float(layer.source_in) + skip, float(layer.source_out)))
    return {key: items for key, items in readers.items() if key not in refused}


def _nested_windows(plan, origin: float) -> dict[str, list[tuple[Any, float, float]]]:
    """Fenêtres (couche, début, fin) que les couches vidéo racine lisent dans chaque séquence imbriquée (lecteurs simples)."""
    video_origin = float(origin) if origin and origin > 0 else 0.0
    windows: dict[str, list[tuple[Any, float, float]]] = {}
    for layer in plan.video_layers:
        key = getattr(layer, "nested_key", "")
        if key and _plain_video(layer):
            skip = max(0.0, video_origin - layer.timeline_start) if video_origin > 0.0 else 0.0
            windows.setdefault(key, []).append((layer, float(layer.source_in) + skip, float(layer.source_out)))
    return windows


def _split_nested(plan, origin: float, limit: int):
    """Séquences imbriquées dont la vidéo se compose par instance (voir :class:`NestedInstance`) ; lecteur -> instance ;
    ``id(couche interne) -> horodatage de la première image`` (:func:`first_frame_time`)."""
    entries = {entry.key: entry for entry in getattr(plan, "nested_sequences", ()) or ()}
    splits: dict[str, list[NestedInstance]] = {}
    readers_map: dict[int, int] = {}
    bases: dict[int, float] = {}
    total = 0
    for key, items in _video_readers(plan, origin).items():
        entry = entries.get(key)
        if entry is None or getattr(entry, "composition", None) is not None:
            continue
        bounds = sorted({(start, end) for _layer, start, end in items})
        if not all(_finite(start) and _finite(end) and end > start for start, end in bounds):
            continue
        if held_seconds(bounds) <= HOT_SECONDS or total + len(bounds) > limit:
            continue
        entry_bases = _instance_bases(entry.plan)
        if entry_bases is None:
            continue
        bases.update(entry_bases)
        slots = {window: slot for slot, window in enumerate(bounds)}
        splits[key] = [NestedInstance(f"{key}#{slot}", start, end) for slot, (start, end) in enumerate(bounds)]
        for layer, start, end in items:
            readers_map[id(layer)] = slots[(start, end)]
        total += len(bounds)
    return splits, readers_map, bases


@dataclass
class InputDecision:
    """Ce que la commande ouvre, décidé d'après le plan seul (aucun FFmpeg n'est lancé)."""

    splits: dict[str, list[NestedInstance]]
    readers: dict[int, int]
    bases: dict[int, float]
    compositions: list[Any]
    separate: dict[tuple[int, str], _Window]
    shared: list[tuple[str, float, float, Any, Any]]
    """Fenêtres qui gardent un flux partagé : ``(famille, début, fin, couche, plan qui la compose)``. La famille est
    ``file:<chemin>`` (clips d'un même fichier gardant son entrée) ou ``nested:<clé>`` (lecteurs d'un rendu de séquence
    imbriquée qui n'est pas composé par instance) ; début et fin en secondes de ce flux."""
    pipelines: int
    """Entrées positionnées + deux par instance : ce qui garde des images en mémoire pendant tout l'export."""


def decide_inputs(plan, *, origin: float = 0.0, video: bool = True, frame: tuple[int, int] = (1920, 1080)) -> InputDecision:
    """Choisit les clips positionnés et les séquences imbriquées composées par instance (voir le module)."""
    cap = max_pipelines(*frame)
    splits, readers_map, bases = _split_nested(plan, origin, cap // 2) if video else ({}, {}, {})
    used = 2 * sum(len(instances) for instances in splits.values())
    # Compositions dont la vidéo lit des clips : (plan, début du rendu, limite, étiquette).
    compositions: list[tuple[Any, float, float | None, str]] = [
        (plan, float(origin) if origin and origin > 0 else 0.0, None, "")
    ]
    for entry in getattr(plan, "nested_sequences", ()) or ():
        instances = splits.get(entry.key)
        if instances is None:
            compositions.append((entry.plan, 0.0, None, ""))
            continue
        for instance in instances:
            compositions.append((entry.plan, instance.start, instance.end + INSTANCE_TAIL, instance.tag))

    windows: list[tuple[object, str, _Window]] = []
    owners: dict[tuple[int, str], Any] = {}
    if video:
        for current, video_origin, limit, tag in compositions:
            for layer in current.video_layers:
                window = _video_window(layer, video_origin, limit, INSTANCE_LEAD if tag else 0.0)
                if window is not None:
                    windows.append((layer, tag, window))
                    owners[(id(layer), tag)] = current

    families: dict[str, list[tuple[object, str, _Window]]] = {}
    for layer, tag, window in windows:
        families.setdefault(window.path, []).append((layer, tag, window))
    chosen: list[tuple[float, float, object, str, _Window]] = []
    for members in families.values():
        latest = max(window.start for _layer, _tag, window in members)
        if held_seconds((window.start, window.end) for _layer, _tag, window in members) <= HOT_SECONDS:
            continue
        for layer, tag, window in members:
            if not window.positionable:
                continue
            chosen.append((max(0.0, min(window.end, latest) - window.start), window.start, layer, tag, window))
    # Ceux qui retiennent le plus d'abord (puis les plus tardifs), si le plafond oblige à choisir.
    chosen.sort(key=lambda item: (-item[0], -item[1]))

    separate: dict[tuple[int, str], _Window] = {}
    keys: set[tuple[str, int, float]] = set()
    budget = max(0, cap - used)
    for _held, _start, layer, tag, window in chosen:
        if tag:
            # L'entrée d'une instance fait partie de son coût (deux entrées comptées par instance) : toujours positionnée.
            separate[(id(layer), tag)] = window
            continue
        key = (window.path, window.seek, round(window.duration, 6))
        if key not in keys and len(keys) >= budget:
            continue
        keys.add(key)
        separate[(id(layer), tag)] = window

    shared: list[tuple[str, float, float, Any, Any]] = []
    for layer, tag, window in windows:
        if (id(layer), tag) not in separate:
            shared.append((f"file:{window.path}", window.start, window.end, layer, owners[(id(layer), tag)]))
    if video:
        origin_v = float(origin) if origin and origin > 0 else 0.0
        for nested_key, items in _nested_windows(plan, origin_v).items():
            if nested_key not in splits:
                shared.extend((f"nested:{nested_key}", start, end, layer, plan) for layer, start, end in items)
    return InputDecision(splits, readers_map, bases, compositions, separate, shared, used + len(keys))


def plan_inputs(plan, *, origin: float = 0.0, video: bool = True, frame: tuple[int, int] = (1920, 1080)):
    """Entrées de la commande d'un plan : ``(chemins, chemin → indice, entrées positionnées)``.

    Les entrées partagées sont celles d'avant (:func:`core.export_engine._build_input_list`), moins les médias dont chaque clip
    vidéo est positionné. ``video=False`` (mesure audio seule) : rien n'est positionné. ``frame`` : taille de la composition
    (plafond d'entrées, :func:`max_pipelines`).
    """
    from .export_engine import _build_input_list

    decision = decide_inputs(plan, origin=origin, video=video, frame=frame)
    input_paths, path_to_index = _build_input_list(plan)
    if decision.separate:
        # Retire de la liste partagée les médias dont chaque clip vidéo a désormais sa propre entrée (le son reste partagé).
        still_shared = _shared_paths(plan, decision.compositions, decision.separate)
        input_paths = [path for path in input_paths if path in still_shared]
        path_to_index = {path: index for index, path in enumerate(input_paths)}
    seeks = InputSeeks(input_paths)
    seeks._windows = decision.separate          # noqa: SLF001 - même module
    seeks.splits = decision.splits
    seeks.readers = decision.readers
    seeks.bases = decision.bases
    return input_paths, path_to_index, seeks


def _shared_paths(plan, compositions, separate_ids: dict[tuple[int, str], _Window]) -> set[str]:
    """Chemins encore lus par au moins une couche qui n'est pas positionnée (le son l'est toujours)."""
    shared: set[str] = set()
    for current in [plan] + [entry.plan for entry in getattr(plan, "nested_sequences", ()) or ()]:
        for layer in current.audio_layers:
            if not getattr(layer, "nested_key", ""):
                shared.add(layer.source_path)
    for current, _video_origin, _limit, tag in compositions:
        for layer in current.video_layers:
            if getattr(layer, "nested_key", ""):
                continue
            if (id(layer), tag) not in separate_ids:
                shared.add(layer.source_path)
    return shared
