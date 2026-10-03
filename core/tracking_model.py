"""Modèle du tracking 2D : trackers, données suivies, liaisons, stabilisation.

Module **pur** (ni Qt, ni FFmpeg, ni numpy). Il décrit ce qui est
enregistré dans le projet ; l'analyse (:mod:`core.tracking_engine`),
l'évaluation (:mod:`core.tracking_motion`) et l'application aux propriétés
(:mod:`core.tracking_bindings`) s'appuient dessus.

Repères
-------

Un tracker vit sur un **clip vidéo** et suit un point du **média source** :

- coordonnées en **pixels du média original** (``x`` vers la droite, ``y``
  vers le bas), quelle que soit la résolution d'analyse ou l'existence
  d'un proxy ;
- temps en **images source** : l'échantillon ``i`` est l'image du média à
  ``i / rate`` secondes (``rate`` = cadence nominale du média). Trimer,
  couper, ralentir ou inverser le clip après l'analyse ne change donc pas
  les données : seule la correspondance temps de timeline → temps source
  change (:func:`core.tracking_motion.source_time`).

Stockage compact
----------------

:class:`TrackData` range ses échantillons sur une grille régulière, dans
des ``bytes`` immuables : positions en virgule fixe (1/1024 px, entiers 32
bits), confiance sur 8 bits, état sur 8 bits. Les données sont donc
partagées sans copie par les snapshots d'historique, comparées et hachées
en temps C, et sérialisées en delta + zlib + base64 (quelques octets par
image pour un mouvement régulier).

Liaisons
--------

Une :class:`TrackLink` est rangée sur le clip **cible** (comme un parent) :
elle désigne un clip source (``""`` = ce clip), un ou plusieurs trackers et
une propriété (transform, point d'ancrage, masque). Elle n'enregistre
aucune image-clé : les valeurs sont dérivées à la volée dans le plan de
rendu (un seul moteur d'animation, voir :mod:`core.tracking_bindings`).
"""

from __future__ import annotations

import base64
import math
import sys
import uuid
import zlib
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, NamedTuple

TRACKING_ALGORITHM_VERSION = 1
"""Version de l'algorithme d'analyse (entre dans la clé de cache)."""

FIXED_POINT = 1024.0
"""Résolution des positions stockées : 1/1024 de pixel source."""

_INT32 = (-(2 ** 31), 2 ** 31 - 1)

TRACKER_COLORS: tuple[str, ...] = (
    "#36E6C3", "#FFB020", "#FF5C8A", "#5CA8FF", "#B48CFF", "#7CDB5A", "#FF7A45", "#E8E8E8",
)
"""Couleurs d'interface attribuées aux trackers dans l'ordre de création."""

SELF_CLIP = ""
"""``TrackLink.source_clip_id`` : le clip qui porte la liaison est la source."""


class SampleStatus(IntEnum):
    """État d'un échantillon. La valeur est sérialisée (un octet)."""

    EMPTY = 0
    """Jamais analysé."""
    TRACKED = 1
    """Suivi automatique, confiance suffisante."""
    UNCERTAIN = 2
    """Suivi automatique, confiance faible : position conservée mais signalée."""
    LOST = 3
    """Suivi perdu (confiance trop faible, point hors de l'image) : position non utilisée."""
    MANUAL = 4
    """Placé ou corrigé par l'utilisateur ; jamais écrasé par une analyse."""


VALID_STATUSES = frozenset({SampleStatus.TRACKED, SampleStatus.UNCERTAIN, SampleStatus.MANUAL})
"""États dont la position est utilisée par le rendu."""


class Sample(NamedTuple):
    """Un échantillon : position (pixels source), confiance 0..1, état."""

    x: float
    y: float
    confidence: float
    status: SampleStatus

    @property
    def valid(self) -> bool:
        return self.status in VALID_STATUSES


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _fixed(value: float) -> int:
    scaled = round(_finite(value) * FIXED_POINT)
    return max(_INT32[0], min(_INT32[1], int(scaled)))


def quantize(value: float) -> float:
    """Position telle qu'elle sera stockée (1/1024 px)."""
    return _fixed(value) / FIXED_POINT


def new_tracking_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Données suivies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackData:
    """Échantillons d'un tracker sur la grille d'images source.

    Attributes:
        rate: cadence de la grille (images par seconde du média).
        first: indice de la première image couverte (``first / rate`` s).
        xs / ys: positions, entiers 32 bits en 1/1024 px (ordre natif).
        confidence: confiance quantifiée sur 8 bits (0..255).
        status: :class:`SampleStatus` sur 8 bits.
        source_width / source_height: taille du média au moment de
            l'analyse. Si le média est remplacé par une autre résolution
            (relink, autre machine), les positions suivent l'échelle.
    """

    rate: float = 0.0
    first: int = 0
    xs: bytes = field(default=b"", repr=False)
    ys: bytes = field(default=b"", repr=False)
    confidence: bytes = field(default=b"", repr=False)
    status: bytes = field(default=b"", repr=False)
    source_width: int = 0
    source_height: int = 0

    def __post_init__(self) -> None:
        rate = _finite(self.rate)
        object.__setattr__(self, "rate", rate if rate > 0 else 0.0)
        object.__setattr__(self, "first", max(0, int(self.first)))
        count = len(self.status)
        if len(self.xs) != 4 * count or len(self.ys) != 4 * count or len(self.confidence) != count:
            raise ValueError("TrackData : tableaux de longueurs incohérentes.")
        object.__setattr__(self, "source_width", max(0, int(self.source_width)))
        object.__setattr__(self, "source_height", max(0, int(self.source_height)))

    def __repr__(self) -> str:
        return (
            f"TrackData(rate={self.rate:g}, first={self.first}, count={self.count}, "
            f"source={self.source_width}x{self.source_height})"
        )

    # Les données sont immuables : une copie (historique, presse-papiers) les partage.
    def __copy__(self) -> TrackData:
        return self

    def __deepcopy__(self, _memo) -> TrackData:
        return self

    # -- construction --------------------------------------------------------------------

    @classmethod
    def from_samples(
        cls,
        rate: float,
        samples: Mapping[int, Sample],
        *,
        source_size: tuple[int, int] = (0, 0),
    ) -> TrackData:
        """Données couvrant exactement les indices fournis (trous = ``EMPTY``)."""
        if not samples:
            return cls(rate=rate, source_width=source_size[0], source_height=source_size[1])
        first = min(samples)
        last = max(samples)
        count = last - first + 1
        xs = array("i", bytes(4 * count))
        ys = array("i", bytes(4 * count))
        conf = bytearray(count)
        status = bytearray(count)
        for index, sample in samples.items():
            if index < 0:
                continue
            offset = index - first
            xs[offset] = _fixed(sample.x)
            ys[offset] = _fixed(sample.y)
            conf[offset] = max(0, min(255, int(round(_finite(sample.confidence) * 255))))
            status[offset] = int(SampleStatus(sample.status))
        return cls(
            rate=rate, first=max(0, first), xs=xs.tobytes(), ys=ys.tobytes(),
            confidence=bytes(conf), status=bytes(status),
            source_width=source_size[0], source_height=source_size[1],
        )

    # -- lecture --------------------------------------------------------------------------

    def _arrays(self) -> tuple[array, array]:
        cached = self.__dict__.get("_cached_arrays")
        if cached is None:
            xs = array("i")
            xs.frombytes(self.xs)
            ys = array("i")
            ys.frombytes(self.ys)
            cached = (xs, ys)
            object.__setattr__(self, "_cached_arrays", cached)
        return cached

    def _valid_indices(self) -> list[int]:
        cached = self.__dict__.get("_cached_valid")
        if cached is None:
            valid = {int(s) for s in VALID_STATUSES}
            cached = [self.first + i for i, s in enumerate(self.status) if s in valid]
            object.__setattr__(self, "_cached_valid", cached)
        return cached

    @property
    def count(self) -> int:
        return len(self.status)

    @property
    def last(self) -> int:
        """Indice de la dernière image couverte (``first - 1`` si vide)."""
        return self.first + self.count - 1

    @property
    def is_empty(self) -> bool:
        return not self._valid_indices()

    def covers(self, index: int) -> bool:
        return self.first <= index <= self.last

    def sample(self, index: int) -> Sample:
        """Échantillon de l'image ``index`` (``EMPTY`` hors de la plage)."""
        if not self.covers(index):
            return Sample(0.0, 0.0, 0.0, SampleStatus.EMPTY)
        offset = index - self.first
        xs, ys = self._arrays()
        return Sample(
            xs[offset] / FIXED_POINT, ys[offset] / FIXED_POINT,
            self.confidence[offset] / 255.0, SampleStatus(self.status[offset]),
        )

    def samples(self) -> Iterator[tuple[int, Sample]]:
        for index in range(self.first, self.first + self.count):
            yield index, self.sample(index)

    def status_at(self, index: int) -> SampleStatus:
        if not self.covers(index):
            return SampleStatus.EMPTY
        return SampleStatus(self.status[index - self.first])

    def valid_indices(self) -> tuple[int, ...]:
        return tuple(self._valid_indices())

    def valid_range(self) -> tuple[int, int] | None:
        valid = self._valid_indices()
        return (valid[0], valid[-1]) if valid else None

    def counts(self) -> dict[SampleStatus, int]:
        result = {status: 0 for status in SampleStatus}
        for value in self.status:
            result[SampleStatus(value)] += 1
        return result

    def nearest_valid(self, index: int) -> int | None:
        """Image valide la plus proche de ``index`` (``None`` sans donnée)."""
        valid = self._valid_indices()
        if not valid:
            return None
        position = bisect_left(valid, index)
        candidates = [valid[i] for i in (position - 1, position) if 0 <= i < len(valid)]
        return min(candidates, key=lambda candidate: (abs(candidate - index), candidate))

    def index_at_time(self, source_seconds: float) -> float:
        """Indice (fractionnaire) de l'image à ``source_seconds``."""
        return _finite(source_seconds) * self.rate if self.rate > 0 else 0.0

    def position_at_index(self, index: float) -> tuple[float, float] | None:
        """Position interpolée entre les deux images valides qui encadrent ``index``.

        Hors de la plage valide, la position tient la valeur de l'extrémité
        la plus proche. ``None`` seulement si aucune image n'est valide :
        une position n'est jamais inventée à partir de rien.
        """
        valid = self._valid_indices()
        if not valid or not math.isfinite(index):
            return None
        xs, ys = self._arrays()
        if index <= valid[0]:
            offset = valid[0] - self.first
            return xs[offset] / FIXED_POINT, ys[offset] / FIXED_POINT
        if index >= valid[-1]:
            offset = valid[-1] - self.first
            return xs[offset] / FIXED_POINT, ys[offset] / FIXED_POINT
        right_pos = bisect_right(valid, index)
        left, right = valid[right_pos - 1], valid[min(right_pos, len(valid) - 1)]
        lo, ro = left - self.first, right - self.first
        if right == left:
            return xs[lo] / FIXED_POINT, ys[lo] / FIXED_POINT
        u = (index - left) / (right - left)
        return (
            (xs[lo] + (xs[ro] - xs[lo]) * u) / FIXED_POINT,
            (ys[lo] + (ys[ro] - ys[lo]) * u) / FIXED_POINT,
        )

    def position_at_time(self, source_seconds: float) -> tuple[float, float] | None:
        return self.position_at_index(self.index_at_time(source_seconds))

    def scaled_to(self, width: int, height: int) -> tuple[float, float]:
        """Facteurs ``(fx, fy)`` vers un média de taille ``width × height``."""
        if self.source_width <= 0 or self.source_height <= 0 or width <= 0 or height <= 0:
            return 1.0, 1.0
        return width / self.source_width, height / self.source_height

    # -- modifications (retournent de nouvelles données) ---------------------------------------

    def with_samples(self, updates: Mapping[int, Sample], *, rate: float | None = None) -> TrackData:
        """Fusionne ``updates`` (remplace les images données, étend la plage)."""
        if not updates:
            return self
        merged = dict(self._raw_samples())
        merged.update({index: sample for index, sample in updates.items() if index >= 0})
        merged = {i: s for i, s in merged.items() if s.status is not SampleStatus.EMPTY}
        return TrackData.from_samples(
            rate if rate is not None else self.rate, merged,
            source_size=(self.source_width, self.source_height),
        )

    def cleared(self, start: int, end: int, *, keep_manual: bool = True) -> TrackData:
        """Efface les images ``[start, end]`` (les corrections manuelles restent si demandé)."""
        low, high = min(start, end), max(start, end)
        kept = {
            index: sample for index, sample in self._raw_samples()
            if not (low <= index <= high)
            or (keep_manual and sample.status is SampleStatus.MANUAL)
        }
        kept = {i: s for i, s in kept.items() if s.status is not SampleStatus.EMPTY}
        return TrackData.from_samples(
            self.rate, kept, source_size=(self.source_width, self.source_height)
        )

    def _raw_samples(self) -> Iterator[tuple[int, Sample]]:
        for index, sample in self.samples():
            if sample.status is not SampleStatus.EMPTY:
                yield index, sample

    # -- sérialisation ----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": self.rate,
            "first": self.first,
            "count": self.count,
            "source_size": [self.source_width, self.source_height],
            "encoding": "delta-i32le-zlib-b64",
            "x": _encode_ints(self.xs),
            "y": _encode_ints(self.ys),
            "confidence": _encode_bytes(self.confidence),
            "status": _encode_bytes(self.status),
        }

    @classmethod
    def from_dict(cls, raw: object) -> TrackData:
        if not isinstance(raw, dict):
            return cls()
        try:
            count = max(0, int(raw.get("count", 0)))
            size = raw.get("source_size") or (0, 0)
            xs = _decode_ints(raw.get("x", ""), count)
            ys = _decode_ints(raw.get("y", ""), count)
            confidence = _decode_bytes(raw.get("confidence", ""), count)
            status = _decode_bytes(raw.get("status", ""), count)
            # Un état inconnu (fichier plus récent) est traité comme perdu : jamais utilisé.
            known = {int(s) for s in SampleStatus}
            status = bytes(s if s in known else int(SampleStatus.LOST) for s in status)
            return cls(
                rate=_finite(raw.get("rate", 0.0)), first=int(raw.get("first", 0)),
                xs=xs, ys=ys, confidence=confidence, status=status,
                source_width=int(size[0]), source_height=int(size[1]),
            )
        except (TypeError, ValueError, IndexError, zlib.error):
            return cls()


def _encode_ints(raw: bytes) -> str:
    values = array("i")
    values.frombytes(raw)
    deltas = array("i", bytes(4 * len(values)))
    previous = 0
    for index, value in enumerate(values):
        delta = value - previous
        deltas[index] = max(_INT32[0], min(_INT32[1], delta))
        previous = value
    if sys.byteorder == "big":
        deltas.byteswap()
    return base64.b64encode(zlib.compress(deltas.tobytes(), 9)).decode("ascii")


def _decode_ints(text: str, count: int) -> bytes:
    if count == 0:
        return b""
    data = zlib.decompress(base64.b64decode(str(text).encode("ascii")))
    deltas = array("i")
    deltas.frombytes(data[: 4 * count])
    if sys.byteorder == "big":
        deltas.byteswap()
    if len(deltas) != count:
        raise ValueError("TrackData : nombre d'échantillons incohérent.")
    values = array("i", bytes(4 * count))
    total = 0
    for index, delta in enumerate(deltas):
        total += delta
        values[index] = max(_INT32[0], min(_INT32[1], total))
    return values.tobytes()


def _encode_bytes(raw: bytes) -> str:
    return base64.b64encode(zlib.compress(bytes(raw), 9)).decode("ascii")


def _decode_bytes(text: str, count: int) -> bytes:
    if count == 0:
        return b""
    data = zlib.decompress(base64.b64decode(str(text).encode("ascii")))
    if len(data) != count:
        raise ValueError("TrackData : nombre d'échantillons incohérent.")
    return data


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class AdaptMode:
    """Mise à jour du motif de référence pendant l'analyse."""

    FIXED = "fixed"
    """Motif de l'image de départ : aucune dérive, sensible aux changements d'aspect."""
    ADAPTIVE = "adaptive"
    """Motif rafraîchi à chaque image fiable : suit les changements lents."""

    ALL = (ADAPTIVE, FIXED)


class Precision:
    """Résolution d'analyse (le résultat reste en pixels du média original)."""

    FAST = "fast"
    """Image réduite au plus à 960 px de large."""
    AUTO = "auto"
    """Au plus 1920 px de large (4K analysé en 1080p)."""
    FULL = "full"
    """Résolution native."""

    ALL = (AUTO, FAST, FULL)
    MAX_WIDTH = {FAST: 960, AUTO: 1920, FULL: 1 << 20}


@dataclass(frozen=True)
class TrackerSettings:
    """Réglages d'un tracker de point.

    Tailles en pixels **du média original**. La zone de recherche est
    centrée sur la position prédite ; elle est toujours au moins aussi
    grande que la zone cible.
    """

    pattern_width: float = 48.0
    pattern_height: float = 48.0
    search_width: float = 144.0
    search_height: float = 144.0
    min_confidence: float = 0.55
    """En dessous : suivi perdu, l'analyse s'arrête (ou continue en ``LOST``)."""
    good_confidence: float = 0.8
    """En dessous (et au-dessus du minimum) : échantillon incertain."""
    adapt: str = AdaptMode.ADAPTIVE
    precision: str = Precision.AUTO
    stop_on_loss: bool = True

    def __post_init__(self) -> None:
        pw = max(8.0, min(1024.0, _finite(self.pattern_width, 48.0)))
        ph = max(8.0, min(1024.0, _finite(self.pattern_height, 48.0)))
        sw = max(pw + 4.0, min(4096.0, _finite(self.search_width, 144.0)))
        sh = max(ph + 4.0, min(4096.0, _finite(self.search_height, 144.0)))
        low = max(0.05, min(0.95, _finite(self.min_confidence, 0.55)))
        good = max(low, min(0.99, _finite(self.good_confidence, 0.8)))
        object.__setattr__(self, "pattern_width", pw)
        object.__setattr__(self, "pattern_height", ph)
        object.__setattr__(self, "search_width", sw)
        object.__setattr__(self, "search_height", sh)
        object.__setattr__(self, "min_confidence", low)
        object.__setattr__(self, "good_confidence", good)
        object.__setattr__(self, "adapt", self.adapt if self.adapt in AdaptMode.ALL else AdaptMode.ADAPTIVE)
        object.__setattr__(
            self, "precision", self.precision if self.precision in Precision.ALL else Precision.AUTO
        )
        object.__setattr__(self, "stop_on_loss", bool(self.stop_on_loss))

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, raw: object) -> TrackerSettings:
        if not isinstance(raw, dict):
            return cls()
        known = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        try:
            return cls(**known)
        except (TypeError, ValueError):
            return cls()


class TrackerKind:
    """Type de tracker. Seul ``point`` est analysé aujourd'hui (voir
    ``docs/tracking.md`` pour ajouter un type : planar, perspective…)."""

    POINT = "point"
    ALL = (POINT,)


@dataclass(frozen=True)
class Tracker:
    """Un point suivi sur un clip vidéo (œil gauche, logo, coin d'écran…)."""

    id: str = ""
    name: str = "Tracker"
    color: str = TRACKER_COLORS[0]
    visible: bool = True
    show_path: bool = True
    kind: str = TrackerKind.POINT
    settings: TrackerSettings = field(default_factory=TrackerSettings)
    data: TrackData = field(default_factory=TrackData)

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", new_tracking_id())
        object.__setattr__(self, "name", str(self.name or "Tracker")[:80])
        color = str(self.color or "").upper()
        if len(color) != 7 or not color.startswith("#"):
            color = TRACKER_COLORS[0]
        object.__setattr__(self, "color", color)
        object.__setattr__(self, "visible", bool(self.visible))
        object.__setattr__(self, "show_path", bool(self.show_path))
        object.__setattr__(self, "kind", self.kind if self.kind in TrackerKind.ALL else TrackerKind.POINT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "color": self.color, "visible": self.visible,
            "show_path": self.show_path, "kind": self.kind,
            "settings": self.settings.to_dict(), "data": self.data.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: object) -> Tracker | None:
        if not isinstance(raw, dict):
            return None
        return cls(
            id=str(raw.get("id", "")), name=str(raw.get("name", "Tracker")),
            color=str(raw.get("color", TRACKER_COLORS[0])), visible=bool(raw.get("visible", True)),
            show_path=bool(raw.get("show_path", True)), kind=str(raw.get("kind", TrackerKind.POINT)),
            settings=TrackerSettings.from_dict(raw.get("settings")),
            data=TrackData.from_dict(raw.get("data")),
        )


# ---------------------------------------------------------------------------
# Liaisons
# ---------------------------------------------------------------------------


class TrackTarget:
    """Propriété pilotée par une liaison."""

    TRANSFORM = "transform"
    """Transform du clip cible : position (et rotation / échelle avec 2 points ou plus)."""
    ANCHOR = "anchor"
    """Point d'ancrage du clip source lui-même : le point suivi reste à la position."""
    MASK = "mask"
    """Un masque du clip cible (``mask_id``) : position (et rotation / taille)."""

    ALL = (TRANSFORM, ANCHOR, MASK)


@dataclass(frozen=True)
class TrackLink:
    """Liaison dynamique d'une propriété du clip qui la porte à un tracking.

    Attributes:
        source_clip_id: clip vidéo qui porte les trackers (:data:`SELF_CLIP`
            = le clip lui-même ; conservé par une coupe ou une duplication).
        continuation_ids: clips qui **prolongent** la source après une coupe
            (la partie droite, puis celles des coupes suivantes). La liaison
            suit, à chaque instant de la timeline, celui des clips de
            :attr:`source_ids` qui couvre cet instant : couper la source ne
            fige donc pas le mouvement des clips liés. Les données de
            tracking sont en temps source et partagées par les deux moitiés ;
            seule cette liste dit quelles moitiés la liaison suit.
        tracker_ids: un tracker = translation ; deux ou plus = translation,
            rotation et échelle (similitude ajustée aux moindres carrés).
        target: :class:`TrackTarget`.
        mask_id: masque ciblé (``target == mask``).
        position / rotation / scale: composantes appliquées.
        reference_index: image source de référence (mouvement nul) : la
            cible garde à cette image exactement la valeur saisie.
        enabled: liaison active (désactivée = valeurs saisies seules).
    """

    id: str = ""
    source_clip_id: str = SELF_CLIP
    tracker_ids: tuple[str, ...] = ()
    target: str = TrackTarget.TRANSFORM
    mask_id: str = ""
    position: bool = True
    rotation: bool = False
    scale: bool = False
    reference_index: int = 0
    enabled: bool = True
    continuation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", new_tracking_id())
        object.__setattr__(self, "source_clip_id", str(self.source_clip_id or ""))
        source = str(self.source_clip_id)
        continuation = tuple(
            dict.fromkeys(str(c) for c in self.continuation_ids if c and str(c) != source)
        ) if source != SELF_CLIP else ()
        object.__setattr__(self, "continuation_ids", continuation)
        object.__setattr__(self, "tracker_ids", tuple(dict.fromkeys(str(t) for t in self.tracker_ids if t)))
        object.__setattr__(
            self, "target", self.target if self.target in TrackTarget.ALL else TrackTarget.TRANSFORM
        )
        object.__setattr__(self, "mask_id", str(self.mask_id or ""))
        for name in ("position", "rotation", "scale", "enabled"):
            object.__setattr__(self, name, bool(getattr(self, name)))
        object.__setattr__(self, "reference_index", max(0, int(self.reference_index)))

    @property
    def is_similarity(self) -> bool:
        return len(self.tracker_ids) >= 2 and (self.rotation or self.scale)

    @property
    def source_ids(self) -> tuple[str, ...]:
        """Clips suivis, dans l'ordre : la source, puis ses prolongements (vide : le clip lui-même)."""
        if self.source_clip_id == SELF_CLIP:
            return ()
        return (self.source_clip_id, *self.continuation_ids)

    def follows(self, clip_id: str) -> bool:
        """La liaison suit-elle ``clip_id`` (comme source ou comme prolongement) ?"""
        return clip_id in self.source_ids

    def after_cut(self, clip_id: str, right_id: str) -> TrackLink:
        """Liaison après la coupe de ``clip_id`` : elle suit aussi la partie droite ``right_id``.

        La partie droite se range juste après celle qu'on vient de couper ; une liaison qui ne
        suit pas ``clip_id`` est rendue telle quelle.
        """
        if not self.follows(clip_id) or right_id in self.source_ids:
            return self
        if clip_id == self.source_clip_id:
            return replace(self, continuation_ids=(right_id, *self.continuation_ids))
        position = self.continuation_ids.index(clip_id) + 1
        return replace(
            self, continuation_ids=(*self.continuation_ids[:position], right_id, *self.continuation_ids[position:])
        )

    def without_source(self, clip_id: str) -> TrackLink:
        """Liaison après la suppression de ``clip_id`` : elle suit les parties restantes.

        Si c'est la source elle-même qui disparaît, la première partie restante devient la source ;
        sans partie restante, la liaison est rendue telle quelle (source introuvable : elle est
        signalée, jamais effacée en silence).
        """
        if not self.follows(clip_id) or not self.continuation_ids:
            return self
        if clip_id == self.source_clip_id:
            return replace(self, source_clip_id=self.continuation_ids[0], continuation_ids=self.continuation_ids[1:])
        return replace(self, continuation_ids=tuple(c for c in self.continuation_ids if c != clip_id))

    def with_renamed_sources(self, renamed: Mapping[str, str]) -> TrackLink:
        """Liaison dont les clips suivis portent de nouveaux identifiants (copie de séquence)."""
        if self.source_clip_id == SELF_CLIP or not any(i in renamed for i in self.source_ids):
            return self
        return replace(
            self, source_clip_id=renamed.get(self.source_clip_id, self.source_clip_id),
            continuation_ids=tuple(renamed.get(c, c) for c in self.continuation_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(vars(self))
        data["tracker_ids"] = list(self.tracker_ids)
        # Écrit seulement s'il diffère du défaut : un projet sans coupe garde exactement son format.
        if self.continuation_ids:
            data["continuation_ids"] = list(self.continuation_ids)
        else:
            del data["continuation_ids"]
        return data

    @classmethod
    def from_dict(cls, raw: object) -> TrackLink | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                id=str(raw.get("id", "")), source_clip_id=str(raw.get("source_clip_id", "")),
                tracker_ids=tuple(raw.get("tracker_ids") or ()), target=str(raw.get("target", "")),
                mask_id=str(raw.get("mask_id", "")), position=bool(raw.get("position", True)),
                rotation=bool(raw.get("rotation", False)), scale=bool(raw.get("scale", False)),
                reference_index=int(raw.get("reference_index", 0)), enabled=bool(raw.get("enabled", True)),
                continuation_ids=tuple(str(c) for c in raw.get("continuation_ids") or ()),
            )
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Stabilisation
# ---------------------------------------------------------------------------


def _normalized_range(value: object) -> tuple[int, int] | None:
    """``(première, dernière)`` entières et ordonnées, ou ``None`` si la valeur n'est pas une plage."""
    if value is None:
        return None
    try:
        low, high = (int(bound) for bound in value)  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        return None
    return (max(0, min(low, high)), max(0, low, high))


class StabilizationMode:
    POSITION = "position"
    POSITION_ROTATION = "position_rotation"
    POSITION_ROTATION_SCALE = "position_rotation_scale"

    ALL = (POSITION, POSITION_ROTATION, POSITION_ROTATION_SCALE)


class Smoothing:
    """Lissage du mouvement conservé (écart-type gaussien, en images source)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CUSTOM = "custom"
    LOCKED = "locked"
    """Aucun mouvement conservé : le plan est verrouillé sur l'image de référence."""

    ALL = (MEDIUM, LOW, HIGH, CUSTOM, LOCKED)
    SIGMA = {LOW: 4.0, MEDIUM: 12.0, HIGH: 30.0}


class BorderMode:
    """Traitement des bords vides créés par la compensation."""

    BLACK = "black"
    """Bords noirs (transparents) conservés."""
    ZOOM = "zoom"
    """Agrandissement constant juste suffisant pour les cacher."""
    CROP = "crop"
    """Recadrage fixe : un cadre stable, noir autour, sans agrandissement."""

    ALL = (BLACK, ZOOM, CROP)


@dataclass(frozen=True)
class Stabilization:
    """Stabilisation du clip qui la porte, calculée depuis ses trackers."""

    enabled: bool = True
    tracker_ids: tuple[str, ...] = ()
    mode: str = StabilizationMode.POSITION
    smoothing: str = Smoothing.MEDIUM
    smoothing_frames: float = 12.0
    borders: str = BorderMode.ZOOM
    reference_index: int = 0
    shared_range: tuple[int, int] | None = None
    """Images source ``(première, dernière)`` sur lesquelles l'agrandissement et le recadrage sont
    calculés **en plus** de celles que montre le clip. Posé par une coupe : les deux moitiés d'un plan
    stabilisé gardent ainsi le même zoom (sans cela, chacune calculait le sien et l'image « sautait »
    au point de coupe). ``None`` : seules les images montrées comptent."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "tracker_ids", tuple(dict.fromkeys(str(t) for t in self.tracker_ids if t)))
        object.__setattr__(
            self, "mode", self.mode if self.mode in StabilizationMode.ALL else StabilizationMode.POSITION
        )
        object.__setattr__(
            self, "smoothing", self.smoothing if self.smoothing in Smoothing.ALL else Smoothing.MEDIUM
        )
        object.__setattr__(self, "smoothing_frames", max(0.5, min(600.0, _finite(self.smoothing_frames, 12.0))))
        object.__setattr__(self, "borders", self.borders if self.borders in BorderMode.ALL else BorderMode.ZOOM)
        object.__setattr__(self, "reference_index", max(0, int(self.reference_index)))
        object.__setattr__(self, "shared_range", _normalized_range(self.shared_range))

    @property
    def sigma(self) -> float | None:
        """Écart-type du lissage en images ; ``None`` = verrouillé."""
        if self.smoothing == Smoothing.LOCKED:
            return None
        if self.smoothing == Smoothing.CUSTOM:
            return self.smoothing_frames
        return Smoothing.SIGMA[self.smoothing]

    def to_dict(self) -> dict[str, Any]:
        data = dict(vars(self))
        data["tracker_ids"] = list(self.tracker_ids)
        if self.shared_range is None:      # écrit seulement s'il diffère du défaut
            del data["shared_range"]
        else:
            data["shared_range"] = list(self.shared_range)
        return data

    @classmethod
    def from_dict(cls, raw: object) -> Stabilization | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                enabled=bool(raw.get("enabled", True)), tracker_ids=tuple(raw.get("tracker_ids") or ()),
                mode=str(raw.get("mode", "")), smoothing=str(raw.get("smoothing", "")),
                smoothing_frames=raw.get("smoothing_frames", 12.0), borders=str(raw.get("borders", "")),
                reference_index=int(raw.get("reference_index", 0)),
                shared_range=raw.get("shared_range"),
            )
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Ensemble d'un clip
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipTracking:
    """Tracking d'un clip : ses trackers, les liaisons qu'il reçoit, sa stabilisation."""

    trackers: tuple[Tracker, ...] = ()
    links: tuple[TrackLink, ...] = ()
    stabilization: Stabilization | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trackers", tuple(self.trackers))
        object.__setattr__(self, "links", tuple(self.links))

    @property
    def is_empty(self) -> bool:
        return not self.trackers and not self.links and self.stabilization is None

    @property
    def drives_rendering(self) -> bool:
        """Une liaison active ou une stabilisation active modifie le rendu."""
        return any(link.enabled for link in self.links) or bool(
            self.stabilization is not None and self.stabilization.enabled
        )

    def tracker(self, tracker_id: str) -> Tracker | None:
        return next((t for t in self.trackers if t.id == tracker_id), None)

    def link(self, link_id: str) -> TrackLink | None:
        return next((link for link in self.links if link.id == link_id), None)

    def with_tracker(self, tracker: Tracker) -> ClipTracking:
        """Remplace (même identifiant) ou ajoute ``tracker``."""
        if any(t.id == tracker.id for t in self.trackers):
            return replace(self, trackers=tuple(tracker if t.id == tracker.id else t for t in self.trackers))
        return replace(self, trackers=(*self.trackers, tracker))

    def without_tracker(self, tracker_id: str) -> ClipTracking:
        stabilization = self.stabilization
        if stabilization is not None and tracker_id in stabilization.tracker_ids:
            remaining = tuple(t for t in stabilization.tracker_ids if t != tracker_id)
            stabilization = replace(stabilization, tracker_ids=remaining) if remaining else None
        links = []
        for link in self.links:
            if link.source_clip_id == SELF_CLIP and tracker_id in link.tracker_ids:
                remaining = tuple(t for t in link.tracker_ids if t != tracker_id)
                if not remaining:
                    continue
                link = replace(link, tracker_ids=remaining)
            links.append(link)
        return replace(
            self, trackers=tuple(t for t in self.trackers if t.id != tracker_id),
            links=tuple(links), stabilization=stabilization,
        )

    def with_link(self, link: TrackLink) -> ClipTracking:
        if any(existing.id == link.id for existing in self.links):
            return replace(self, links=tuple(link if e.id == link.id else e for e in self.links))
        return replace(self, links=(*self.links, link))

    def without_link(self, link_id: str) -> ClipTracking:
        return replace(self, links=tuple(link for link in self.links if link.id != link_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trackers": [tracker.to_dict() for tracker in self.trackers],
            "links": [link.to_dict() for link in self.links],
            "stabilization": self.stabilization.to_dict() if self.stabilization is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ClipTracking | None:
        """Lecture tolérante : un élément invalide est ignoré, jamais tout le clip."""
        if not isinstance(raw, dict):
            return None
        trackers = [t for t in (Tracker.from_dict(item) for item in raw.get("trackers") or ()) if t]
        seen: set[str] = set()
        unique = []
        for tracker in trackers:
            if tracker.id in seen:
                tracker = replace(tracker, id=new_tracking_id())
            seen.add(tracker.id)
            unique.append(tracker)
        links = [link for link in (TrackLink.from_dict(item) for item in raw.get("links") or ()) if link]
        value = cls(
            trackers=tuple(unique), links=tuple(links),
            stabilization=Stabilization.from_dict(raw.get("stabilization")),
        )
        return None if value.is_empty else value


def next_tracker_color(existing: Iterable[Tracker]) -> str:
    used = [tracker.color for tracker in existing]
    for color in TRACKER_COLORS:
        if color not in used:
            return color
    return TRACKER_COLORS[len(used) % len(TRACKER_COLORS)]


__all__ = [
    "FIXED_POINT", "SELF_CLIP", "TRACKER_COLORS", "TRACKING_ALGORITHM_VERSION", "AdaptMode",
    "BorderMode", "ClipTracking", "Precision", "Sample", "SampleStatus", "Smoothing",
    "Stabilization", "StabilizationMode", "TrackData", "TrackLink", "TrackTarget", "Tracker",
    "TrackerKind", "TrackerSettings", "VALID_STATUSES", "new_tracking_id", "next_tracker_color",
    "quantize",
]
