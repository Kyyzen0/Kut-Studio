"""Modèle de données du Multicam (sans Qt, sans E/S).

Une **source Multicam** n'est pas un nouveau type de séquence : c'est une :class:`~core.project_model.Sequence`
ordinaire dont le champ ``multicam`` décrit ses *angles*. Chaque angle possède une piste de la séquence :

* une piste **vidéo** pour une caméra (le clip porte l'image *et* le son de la caméra, comme partout dans le projet) ;
* une piste **audio** pour un enregistreur externe.

Le décalage de synchronisation d'un angle **n'est stocké nulle part** : c'est la position de ses clips dans la séquence
(une seule source de vérité ; déplacer un angle à la main, c'est déplacer ses clips, et l'historique s'en charge).

Le montage (« quel angle à quel instant ») est fait de clips imbriqués ordinaires de la séquence parente, chacun portant
un sélecteur ``Clip.angle_id`` ; couper, rogner, déplacer, appliquer une transition fonctionne donc sans cas particulier.
Voir ``docs/multicam.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

MAX_ANGLES = 64
"""Garde-fou de lecture : un fichier qui déclare davantage d'angles est refusé (corrompu)."""


class SyncMethod(str, Enum):
    """Comment les angles ont été alignés (information, jamais une règle de rendu)."""

    TIMECODE = "timecode"
    AUDIO = "audio"
    MARKER = "marker"
    START = "start"
    MANUAL = "manual"
    POSITIONS = "positions"
    """Positions actuelles de clips déjà alignés sur la timeline."""


class SyncStatus(str, Enum):
    """Fiabilité de la synchronisation d'un angle.

    ``EXCELLENT`` … ``FAILED`` ne sont produits que par une mesure (audio) ; ``MANUAL`` marque un décalage corrigé à la
    main (l'utilisateur fait foi) ; ``NONE`` : aucune mesure (angle de référence, début des clips, timecode lu tel quel).
    Un état n'est jamais « bon » faute de pouvoir mesurer : l'incertitude se dit (``UNCERTAIN``).
    """

    NONE = "none"
    EXCELLENT = "excellent"
    GOOD = "good"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    MANUAL = "manual"


class AudioMode(str, Enum):
    """Politique audio d'une source Multicam."""

    FOLLOW_VIDEO = "follow"
    """Changer d'angle change aussi le son : l'audio est celui de l'angle actif."""
    FIXED = "fixed"
    """Une source audio principale reste utilisée, quel que soit l'angle vidéo (``angle_ids[0]``)."""
    MIX = "mix"
    """Plusieurs sources (``angle_ids``) sont mixées, quel que soit l'angle vidéo."""


def _coerce_enum(enum_type, value, default):
    try:
        return enum_type(value)
    except ValueError:
        return default


@dataclass
class MulticamAngle:
    """Un angle (caméra ou enregistreur) d'une source Multicam.

    Attributes:
        id: Identifiant stable, unique dans la source (référencé par ``Clip.angle_id``). Un renommage ne le change pas.
        name: Nom affiché (modifiable).
        track_id: Piste de la séquence qui porte les clips de l'angle.
        color_index: Rang dans la palette d'angles de l'interface (la logique métier ne connaît aucune couleur).
        sync_method / sync_status / sync_confidence: comment et avec quelle fiabilité l'angle a été aligné.
            ``sync_confidence`` ∈ [0, 1] ou ``None`` (pas de mesure).
    """

    id: str
    name: str
    track_id: str
    color_index: int = 0
    sync_method: SyncMethod | None = None
    sync_status: SyncStatus = SyncStatus.NONE
    sync_confidence: float | None = None

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValueError("Un angle Multicam doit avoir un identifiant non vide.")
        if not str(self.track_id or "").strip():
            raise ValueError("Un angle Multicam doit désigner une piste.")
        self.name = str(self.name or self.id)
        try:
            self.color_index = max(0, int(self.color_index))
        except (TypeError, ValueError):
            self.color_index = 0
        if self.sync_method is not None and not isinstance(self.sync_method, SyncMethod):
            self.sync_method = _coerce_enum(SyncMethod, self.sync_method, None)
        if not isinstance(self.sync_status, SyncStatus):
            self.sync_status = _coerce_enum(SyncStatus, self.sync_status, SyncStatus.NONE)
        if self.sync_confidence is not None:
            try:
                value = float(self.sync_confidence)
            except (TypeError, ValueError):
                value = math.nan
            self.sync_confidence = None if not math.isfinite(value) else max(0.0, min(1.0, value))


@dataclass
class MulticamAudio:
    """Politique audio : quelle(s) source(s) alimente(nt) le son d'un segment."""

    mode: AudioMode = AudioMode.FOLLOW_VIDEO
    angle_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AudioMode):
            self.mode = _coerce_enum(AudioMode, self.mode, AudioMode.FOLLOW_VIDEO)
        self.angle_ids = tuple(str(item) for item in self.angle_ids if str(item))


@dataclass
class MulticamSource:
    """Description Multicam d'une séquence (``Sequence.multicam``).

    L'ordre de ``angles`` est l'ordre d'affichage et de numérotation (Angle 1, 2, 3…). Le premier angle est l'angle par
    défaut d'un segment qui n'en désigne aucun.
    """

    angles: list[MulticamAngle] = field(default_factory=list)
    audio: MulticamAudio = field(default_factory=MulticamAudio)
    sync_method: SyncMethod | None = None

    def __post_init__(self) -> None:
        if self.sync_method is not None and not isinstance(self.sync_method, SyncMethod):
            self.sync_method = _coerce_enum(SyncMethod, self.sync_method, None)
        seen: set[str] = set()
        for angle in self.angles:
            if angle.id in seen:
                raise ValueError(f"Angle Multicam en double : {angle.id}.")
            seen.add(angle.id)

    def angle(self, angle_id: str) -> MulticamAngle | None:
        for angle in self.angles:
            if angle.id == angle_id:
                return angle
        return None

    def angle_for_track(self, track_id: str) -> MulticamAngle | None:
        for angle in self.angles:
            if angle.track_id == track_id:
                return angle
        return None

    def index_of(self, angle_id: str) -> int:
        """Rang (0 = Angle 1) d'un angle, ``-1`` s'il est inconnu."""
        for index, angle in enumerate(self.angles):
            if angle.id == angle_id:
                return index
        return -1


def multicam_to_dict(source: MulticamSource) -> dict:
    """Forme écrite dans le ``.kut`` : aucune donnée recalculable (corrélation, forme d'onde, cache)."""
    data: dict = {
        "angles": [
            {
                "id": angle.id,
                "name": angle.name,
                "track_id": angle.track_id,
                "color_index": angle.color_index,
                **({"sync_method": angle.sync_method.value} if angle.sync_method is not None else {}),
                **({"sync_status": angle.sync_status.value} if angle.sync_status is not SyncStatus.NONE else {}),
                **({"sync_confidence": round(angle.sync_confidence, 4)} if angle.sync_confidence is not None else {}),
            }
            for angle in source.angles
        ],
        "audio": {"mode": source.audio.mode.value, "angle_ids": list(source.audio.angle_ids)},
    }
    if source.sync_method is not None:
        data["sync_method"] = source.sync_method.value
    return data


def multicam_from_dict(data: object) -> MulticamSource | None:
    """Relit une source Multicam. ``None`` si la donnée est absente ; ``ValueError`` si sa structure est abîmée.

    Les valeurs d'énumération inconnues retombent sur leur défaut (un fichier plus récent reste ouvrable) ; la structure
    (liste d'angles, identifiants, doublons, nombre d'angles) est, elle, stricte.
    """
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Source Multicam invalide.")
    raw_angles = data.get("angles")
    if not isinstance(raw_angles, list) or len(raw_angles) > MAX_ANGLES:
        raise ValueError("Liste d'angles Multicam invalide.")
    angles: list[MulticamAngle] = []
    for item in raw_angles:
        if not isinstance(item, dict):
            raise ValueError("Angle Multicam invalide.")
        angles.append(MulticamAngle(
            id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            track_id=str(item.get("track_id", "")),
            color_index=item.get("color_index", 0),
            sync_method=item.get("sync_method"),
            sync_status=item.get("sync_status", SyncStatus.NONE.value),
            sync_confidence=item.get("sync_confidence"),
        ))
    raw_audio = data.get("audio")
    audio = MulticamAudio()
    if isinstance(raw_audio, dict):
        ids = raw_audio.get("angle_ids", [])
        audio = MulticamAudio(
            mode=raw_audio.get("mode", AudioMode.FOLLOW_VIDEO.value),
            angle_ids=tuple(ids) if isinstance(ids, list) else (),
        )
    return MulticamSource(angles=angles, audio=audio, sync_method=data.get("sync_method"))


__all__ = [
    "AudioMode",
    "MAX_ANGLES",
    "MulticamAngle",
    "MulticamAudio",
    "MulticamSource",
    "SyncMethod",
    "SyncStatus",
    "multicam_from_dict",
    "multicam_to_dict",
]
