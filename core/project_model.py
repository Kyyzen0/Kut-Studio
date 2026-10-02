"""Modèles de données fondamentaux de Kut-Studio.

Ce module définit les dataclasses partagées (MediaAsset, Clip, Track, Project)
qui serviront de base à la refactorisation progressive de l'application.

Aucune dépendance à PySide6 : ces modèles sont purement métier et peuvent être
manipulés hors d'un contexte Qt (tests, scripts, futurs services).
"""

from __future__ import annotations

import enum
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .effects_model import ClipEffect
    from .text_style import TextStyle
    from .transitions import Transition
    from .time_remapping import TimeRemapping
    from .visual_effects import ClipTransform, TransformKeyframe


# ---------------------------------------------------------------------------
# Bornes audio
# ---------------------------------------------------------------------------

MIN_GAIN_DB: float = -60.0
"""Gain minimal (dB). -60 dB est perçu comme le silence."""

MAX_GAIN_DB: float = 12.0
"""Gain maximal (dB). Au-delà, le risque de saturation augmente fort."""

MIN_PAN: float = -1.0
"""Panoramique fully gauche."""

MAX_PAN: float = 1.0
"""Panoramique fully droite."""


def clamp_gain_db(value: object) -> float:
    """Ramène un gain (dB) dans la plage autorisée.

    Une valeur non numérique (``None``, ``"x"``) retombe sur 0 dB plutôt
    que de lever : un projet chargé ne doit jamais casser sur un champ
    audio absent ou douteux.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, number))


def clamp_pan(value: object) -> float:
    """Ramène un panoramique dans ``[-1, 1]``. Non numérique → centré."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(MIN_PAN, min(MAX_PAN, number))


def clamp_fade(value: object) -> float:
    """Ramène une durée de fondu à une valeur finie non négative."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    if number in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, number)


# ---------------------------------------------------------------------------
# Copie profonde rapide d'un clip (historique d'annulation)
# ---------------------------------------------------------------------------

_ATOMIC_TYPES = (str, int, float, bool, bytes, type(None))
_ATOMIC_SET = frozenset(_ATOMIC_TYPES)
_VERDICTS_KEY = "kut-immutable-verdicts"


def _is_immutable(value, verdicts: dict) -> bool:
    """``True`` si ``value`` ne peut pas changer : le partager entre copies est sûr.

    Vrai pour les scalaires, les énumérations, les tuples d'immuables et
    les dataclasses **gelées** dont tous les champs sont immuables. Une
    dataclass gelée qui contient une liste n'est PAS immuable : elle est
    copiée normalement. Le verdict d'une instance est mémorisé (des
    milliers de clips partagent la même ``TextStyle``).
    """
    kind = type(value)
    if kind in _ATOMIC_TYPES or isinstance(value, enum.Enum):
        return True
    if kind is tuple:
        return all(_is_immutable(item, verdicts) for item in value)
    if is_dataclass(value) and kind.__dataclass_params__.frozen:
        key = id(value)
        verdict = verdicts.get(key)
        if verdict is None:
            verdict = all(
                _is_immutable(getattr(value, f.name), verdicts) for f in fields(value)
            )
            verdicts[key] = verdict
        return verdict
    return False


def _copy_field(value, memo: dict, verdicts: dict):
    if _is_immutable(value, verdicts):
        return value
    if type(value) is list:
        return [item if _is_immutable(item, verdicts) else deepcopy(item, memo) for item in value]
    return deepcopy(value, memo)



@dataclass
class MediaAsset:
    """Décrit un média source importé dans le projet (vidéo, audio, image...).

    Attributes:
        id: Identifiant unique du média dans le projet.
        path: Chemin vers le fichier source sur le disque.
        name: Nom humain du média (affiché dans l'UI).
        duration: Durée totale du média en secondes. Pour un média
            audio seul, doit être strictement positive ; pour les
            autres types, ``>= 0``.
        width: Largeur intrinsèque du média en pixels. Pour un média
            audio seul, peut valoir ``0``.
        height: Hauteur intrinsèque du média en pixels. Pour un média
            audio seul, peut valoir ``0``.
        fps: Fréquence d'images du média. Pour un média audio seul,
            peut valoir ``0.0``. Pour les médias vidéo, doit être
            strictement positive.
        media_type: Type de média ("video", "audio", "image", "subtitle"...).
        has_audio: Indique si le média porte une piste audio exploitable.
            Vrai pour les médias audio seuls ; pour les vidéos, dépend
            du contenu source. Permet à l'export de mixer l'audio même
            depuis une piste vidéo.
    """

    id: str
    path: str
    name: str
    duration: float
    width: int
    height: int
    fps: float
    media_type: str
    has_audio: bool = False

    def __post_init__(self) -> None:
        """Rejette les valeurs physiquement impossibles pour un média."""
        if self.media_type == "audio":
            if self.duration <= 0.0:
                raise ValueError(
                    "La durée d'un MediaAsset audio doit être strictement positive."
                )
            # Pour un média audio seul, les dimensions vidéo sont à 0.
            if self.width != 0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir une largeur de 0."
                )
            if self.height != 0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir une hauteur de 0."
                )
            if self.fps != 0.0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir un fps de 0.0."
                )
            if not self.has_audio:
                raise ValueError(
                    "Un MediaAsset audio doit avoir has_audio=True."
                )
        elif self.media_type == "subtitle":
            # Les sous-titres sont des métadonnées non-visuelles :
            # pas de dimensions, pas de fps, pas de piste audio.
            if self.width != 0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir une largeur de 0."
                )
            if self.height != 0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir une hauteur de 0."
                )
            if self.fps != 0.0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir un fps de 0.0."
                )
            if self.has_audio:
                raise ValueError(
                    "Un MediaAsset sous-titre ne doit pas porter has_audio=True."
                )
            # ``path`` peut être vide : les sous-titres sont inline dans
            # le ``Project``. La durée n'est pas contrainte ici.
        else:
            if self.duration < 0.0:
                raise ValueError(
                    "La durée d'un MediaAsset doit être positive ou nulle."
                )
            if self.width <= 0:
                raise ValueError(
                    "La largeur d'un MediaAsset doit être strictement positive."
                )
            if self.height <= 0:
                raise ValueError(
                    "La hauteur d'un MediaAsset doit être strictement positive."
                )
            if self.fps <= 0.0:
                raise ValueError(
                    "Le fps d'un MediaAsset doit être strictement positif."
                )


@dataclass
class Clip:
    """Une portion d'un MediaAsset placée sur une Track à un instant donné.

    Attributes:
        id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du MediaAsset source.
        track_id: Identifiant de la Track sur laquelle le clip est placée.
        timeline_start: Position de début sur la timeline (en secondes, >= 0).
        source_in: Point d'entrée dans le média source (en secondes, >= 0).
        source_out: Point de sortie du média source (en secondes, > source_in).
        enabled: Indique si le clip est actif (False = clip désactivé / muet).
        label: Nom affiché du clip dans la timeline (par défaut "").
        text: Contenu textuel éventuel du clip, notamment pour les
            sous-titres (par défaut "" ; reste vide pour les clips vidéo).
        transform: Transform 2D appliqué au clip (par défaut : identité).
        transform_keyframes: Images-clés d'animation du transform. Liste
            triée par ``(property_name, time_seconds)``.
        time_remapping: Remappage temporel (vitesse, reverse, freeze frame).
        effects: Effets visuels non destructifs du clip, dans leur ordre
            d'application. Reste vide pour les clips audio et de
            sous-titres : les opérations de :mod:`core.effects_model`
            refusent ces pistes.
        audio_effects: Effets audio non destructifs du clip (tâche 27),
            dans leur ordre d'application. Acceptés sur les pistes
            ``video`` (qui peuvent porter une piste audio) et ``audio``.
            Les pistes ``subtitle`` sont rejetées par les opérations de
            :mod:`core.audio_effects_model`.
    """

    id: str
    asset_id: str
    track_id: str
    timeline_start: float
    source_in: float
    source_out: float
    enabled: bool = True
    label: str = ""
    text: str = ""
    transform: "ClipTransform" = field(default_factory=lambda: _default_transform())
    transform_keyframes: list["TransformKeyframe"] = field(default_factory=list)
    # --- Mixage audio (non destructif, ignoré par la vidéo) ---
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    # --- Remappage temporel ---
    time_remapping: "TimeRemapping" = field(
        default_factory=lambda: _default_time_remapping()
    )
    # --- Effets visuels (tâche 21, clips vidéo uniquement) ---
    effects: list["ClipEffect"] = field(default_factory=list)
    # --- Effets audio non destructifs (tâche 27) ---
    audio_effects: list = field(default_factory=list)
    # --- Étalonnage couleur non destructif (tâche 29) ---
    # Une instance de :class:`~core.color_grading.ColorGrade`. Laissé à
    # ``None`` pour les clips anciens / non étalonnés : la couche
    # d'I/O et l'export retombent sur l'identité (aucun effet).
    color_grade: object = None
    # --- Calque graphique non destructif (tâche 32) ---
    # ``GraphicOverlay`` pour les clips de piste ``graphics`` ; ``None``
    # pour tous les projets historiques et les autres types de clips.
    graphic: object = None
    # Masques, incrustation et mode de fusion (tâche 33).
    compositing: object = field(default_factory=lambda: _default_compositing())
    # --- Style texte non destructif (tâche 24, sous-titres principalement) ---
    text_style: "TextStyle" = field(default_factory=lambda: _default_text_style())
    # --- Séquence imbriquée ---
    # Identifiant d'une :class:`Sequence` du même projet. Non vide, le clip
    # est un *nested sequence clip* : ``source_in`` / ``source_out`` sont
    # alors des temps de la séquence référencée et ``asset_id`` reste vide.
    # La séquence n'est jamais copiée dans le clip : elle est référencée.
    sequence_id: str = ""
    # --- Animation générique (motion graphics) ---
    # Keyframes des propriétés animables hors transform : forme, texte,
    # masques (``graphic.width``, ``mask.<id>.feather``…). Voir
    # :func:`core.animation_targets.generic_target`.
    animation: list = field(default_factory=list)
    # --- Tracking 2D ---
    # :class:`core.tracking_model.ClipTracking` (trackers, liaisons reçues,
    # stabilisation) ou ``None``. Immuable : partagé par les snapshots.
    tracking: object = None

    def __post_init__(self) -> None:
        """Empêche les configurations qui produiraient une durée nulle ou négative."""
        if self.source_in < 0.0:
            raise ValueError("source_in doit être positif ou nul.")
        if self.source_out <= self.source_in:
            raise ValueError(
                "source_out doit être strictement supérieur à source_in "
                "pour garantir une durée de clip positive."
            )
        if self.timeline_start < 0.0:
            raise ValueError("timeline_start doit être positif ou nul.")

        # --- Bornes audio ---
        # On normalise au lieu de lever : ces paramètres sont édités
        # par des faders et des poignées de fondu, un dépassement est
        # une gêne d'usage, pas une corruption de projet.
        self.gain_db = clamp_gain_db(self.gain_db)
        self.pan = clamp_pan(self.pan)
        self.fade_in = clamp_fade(self.fade_in)
        self.fade_out = clamp_fade(self.fade_out)
        # Deux fondus ne peuvent pas se chevaucher.
        duration = self.duration
        if self.fade_in + self.fade_out > duration:
            scale = duration / (self.fade_in + self.fade_out)
            self.fade_in *= scale
            self.fade_out *= scale

    @property
    def source_duration(self) -> float:
        """Durée source du clip (égale à ``source_out - source_in``).
        
        Cette propriété donne la durée originale du média source, indépendamment
        de la vitesse ou du freeze frame. Utilisez ``duration`` pour obtenir la
        durée sur la timeline.
        """
        return self.source_out - self.source_in

    @property
    def duration(self) -> float:
        """Durée du clip sur la timeline.
        
        La durée timeline dépend de la vitesse et du mode freeze frame :
        - Sans freeze : duration = source_duration / speed
        - Avec freeze : duration = freeze_duration
        """
        from .time_remapping import FreezeFrameMode
        
        if self.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
            return self.time_remapping.freeze_duration
        
        return self.source_duration / self.time_remapping.speed

    def set_fade_in(self, seconds: float) -> float:
        """Règle le fondu d'entrée sans empiéter sur le fondu de sortie.

        Le fondu de sortie n'est **pas** réajusté : une poignée ne doit
        jamais déplacer celle que l'utilisateur vient de régler.

        Returns:
            La valeur effectivement appliquée (bornée).
        """
        self.fade_in = min(clamp_fade(seconds), max(0.0, self.duration - self.fade_out))
        return self.fade_in

    def set_fade_out(self, seconds: float) -> float:
        """Règle le fondu de sortie sans empiéter sur le fondu d'entrée."""
        self.fade_out = min(clamp_fade(seconds), max(0.0, self.duration - self.fade_in))
        return self.fade_out

    def _rebalance_fades(self) -> None:
        """Force ``fade_in + fade_out <= duration`` en rognant proportionnellement.

        Réservé au chargement d'un projet : les setters ne l'appellent
        pas, pour ne pas déplacer un fondu déjà réglé par l'utilisateur.
        """
        duration = self.duration
        total = self.fade_in + self.fade_out
        if total > duration and total > 0.0:
            scale = duration / total
            self.fade_in *= scale
            self.fade_out *= scale

    def __deepcopy__(self, memo):
        """Copie profonde qui **partage** les valeurs immuables.

        ``copy.deepcopy`` reconstruit récursivement chaque dataclass gelée
        (transformation, remappage, composition, style) de chaque clip :
        c'était l'essentiel du coût d'un snapshot d'historique, payé à
        *chaque* modification (≈ 150 ms pour 10 000 clips). Les objets
        immuables sont ici partagés ; les listes et objets modifiables
        sont, eux, bien copiés : une copie reste indépendante de l'original.
        """
        clone = self.__class__.__new__(self.__class__)
        memo[id(self)] = clone
        verdicts = memo.setdefault(_VERDICTS_KEY, {})
        copied = {}
        for name, value in self.__dict__.items():
            # Chemin rapide : la majorité des champs sont des scalaires.
            copied[name] = value if type(value) in _ATOMIC_SET else _copy_field(value, memo, verdicts)
        clone.__dict__.update(copied)
        return clone

    def reset_fades(self) -> None:
        """Remet les deux fondus à zéro (double-clic / bouton Réinitialiser)."""
        self.fade_in = 0.0
        self.fade_out = 0.0

    # --- Remappage temporel ---

    @property
    def is_frozen(self) -> bool:
        """Le clip est-il en mode arrêt sur image ?"""
        return self.time_remapping.freeze_mode.value == "freeze"

    @property
    def is_reversed(self) -> bool:
        """Le clip est-il en mode reverse ?"""
        return self.time_remapping.reverse

    @property
    def speed(self) -> float:
        """Vitesse de lecture du clip."""
        return self.time_remapping.speed

    @property
    def is_time_remapped(self) -> bool:
        """Le clip a-t-il un remappage temporel non par défaut ?"""
        return not self.time_remapping.is_normal

    @property
    def is_nested(self) -> bool:
        """Le clip référence-t-il une séquence plutôt qu'un média ?"""
        return bool(self.sequence_id)

    @property
    def is_audio_affected(self) -> bool:
        """Le clip porte-t-il des réglages audio à conserver."""
        return (
            self.gain_db != 0.0
            or self.pan != 0.0
            or self.fade_in != 0.0
            or self.fade_out != 0.0
        )


def _default_transform():  # pragma: no cover - import deferred
    """Retourne un :class:`ClipTransform` par défaut (import paresseux)."""
    from .visual_effects import ClipTransform

    return ClipTransform()


def _default_motion_blur():  # pragma: no cover - import deferred
    """Retourne des :class:`MotionBlurSettings` par défaut (import paresseux)."""
    from .motion_blur import MotionBlurSettings

    return MotionBlurSettings()


def _default_time_remapping():  # pragma: no cover - import deferred
    """Retourne un :class:`TimeRemapping` par défaut (import paresseux)."""
    from .time_remapping import TimeRemapping

    return TimeRemapping()


def _default_text_style():  # pragma: no cover - import deferred
    """Retourne un :class:`TextStyle` par défaut (import paresseux)."""
    from .text_style import default_text_style

    return default_text_style()


@dataclass
class Track:
    """Une piste de la timeline (vidéo, audio, sous-titres...).

    Attributes:
        id: Identifiant unique de la piste dans le projet.
        name: Nom humain de la piste (ex. "V1", "S1").
        type: Type logique de la piste ("video", "audio", "subtitle"...).
        clips: Liste des clips présents sur cette piste.
        locked: ``True`` si la piste refuse toute modification depuis
            l'éditeur (déplacement, trim, suppression, ajout, dépôt).
            Le verrouillage ne concerne que l'UI ; il n'a aucun effet
            sur le rendu / l'export, qui continuent à ignorer les
            pistes verrouillées de la même façon que les autres.
        visible: ``True`` si la piste doit apparaître dans l'aperçu et
            être exportée. Les clips des pistes vidéo invisibles ne
            sont pas rendus ; les sous-titres d'une piste invisible
            ne sont pas incrustés.
        muted: ``True`` si les clips audio de la piste doivent rester
            silencieux à l'export. Pour les pistes non audio, l'effet
            est limité (un sous-titre "muet" reste incrusté). Défini
            explicitement par piste pour offrir un comportement
            cohérent entre types.
        solo: ``True`` si cette piste est la seule de son type à être
            entendue ou vue dans l'aperçu. Le solo vidéo n'affecte pas
            l'audio.
        armed: réservé à un futur enregistrement audio. Sans effet
            sur le rendu actuel.
        height_mode: ``compact``, ``normal`` ou ``large``.
        collapsed: piste réduite à une ligne, sans waveform ni vignettes.
    """

    id: str
    name: str
    type: str
    clips: list[Clip] = field(default_factory=list)
    locked: bool = False
    visible: bool = True
    muted: bool = False
    solo: bool = False
    armed: bool = False
    height_mode: str = "normal"
    collapsed: bool = False
    # --- Mixage audio (non destructif, ignoré par la vidéo) ---
    volume_db: float = 0.0
    pan: float = 0.0
    # --- Automation audio et ducking (tâche 28) ---
    # ``audio_role`` est une chaîne (``"voice"``, ``"music"``, ``"sfx"``
    # ou ``"other"``) pour rester rétro-compatible avec les snapshots
    # d'historique (la sérialisation JSON ne touche pas au champ).
    audio_role: str = "other"
    # ``automation`` est la liste ordonnée des points-clés (gain / fade)
    # de la piste. Vide par défaut pour préserver le comportement
    # historique (gain constant).
    automation: list = field(default_factory=list)
    # ``ducking_config`` est soit ``None`` soit une instance de
    # :class:`DuckingConfig` ; on garde un type ``object`` pour ne pas
    # coupler le modèle au module :mod:`core.audio_automation`.
    ducking_config: object = None

    def __post_init__(self) -> None:
        if self.height_mode not in {"compact", "normal", "large"}:
            self.height_mode = "normal"
        self.volume_db = clamp_gain_db(self.volume_db)
        self.pan = clamp_pan(self.pan)
        # Rôle par défaut ``other`` si la valeur n'est pas reconnue.
        if self.audio_role not in {"voice", "music", "sfx", "other"}:
            self.audio_role = "other"

    def set_volume_db(self, value: float) -> float:
        """Règle le volume de piste, borné, et retourne la valeur appliquée."""
        self.volume_db = clamp_gain_db(value)
        return self.volume_db

    def set_pan(self, value: float) -> float:
        """Règle le panoramique de piste, borné, et retourne la valeur appliquée."""
        self.pan = clamp_pan(value)
        return self.pan

    def reset_audio(self) -> None:
        """Remet volume et panoramique à leur valeur neutre."""
        self.volume_db = 0.0
        self.pan = 0.0

    @property
    def is_video(self) -> bool:
        return self.type == "video"

    @property
    def is_audio(self) -> bool:
        return self.type == "audio"

    @property
    def is_subtitle(self) -> bool:
        return self.type == "subtitle"


@dataclass
class Marker:
    """Repère posé sur la règle de la timeline.

    ``category`` prépare des couleurs futures (``standard``, ``todo``,
    ``chapter``). L'interface n'en distingue qu'une pour l'instant.
    """

    id: str
    time_seconds: float
    name: str = ""
    category: str = "standard"

    def __post_init__(self) -> None:
        if self.time_seconds < 0.0:
            raise ValueError("Un marqueur ne peut pas être avant 0 seconde.")
        if self.category not in {"standard", "todo", "chapter"}:
            self.category = "standard"


MAIN_SEQUENCE_ID = "seq-main"
"""Identifiant de la séquence créée pour un projet à séquence unique.

Un ancien ``.kut`` (une seule timeline) est chargé comme un projet
contenant cette séquence : l'identifiant est stable d'un chargement à
l'autre, donc les références et l'historique restent cohérents.
"""

MAIN_SEQUENCE_NAME = "Séquence principale"
"""Nom par défaut de la séquence d'un projet à séquence unique."""


def _validate_canvas(width: int, height: int, fps: float, owner: str) -> None:
    if width <= 0:
        raise ValueError(f"La largeur {owner} doit être strictement positive.")
    if height <= 0:
        raise ValueError(f"La hauteur {owner} doit être strictement positive.")
    if fps <= 0.0:
        raise ValueError(f"Le fps {owner} doit être strictement positif.")


@dataclass
class Sequence:
    """Une timeline autonome du projet : pistes, repères, transitions.

    Un projet contient une ou plusieurs séquences. Les médias restent au
    niveau du projet (``Project.media_assets``) : deux séquences qui
    utilisent le même fichier référencent le même ``MediaAsset``, rien
    n'est dupliqué. Une séquence peut être utilisée comme clip dans une
    autre (:attr:`Clip.sequence_id`) ; voir :mod:`core.sequences`.

    Attributes:
        id: Identifiant stable, unique dans le projet.
        name: Nom affiché (onglets, fil d'Ariane, bibliothèque).
        width / height / fps: Réglages de rendu propres à la séquence.
            Une séquence imbriquée est rendue à sa propre résolution,
            puis ajustée au cadre de la séquence parente comme un média.
        tracks: Pistes de la séquence.
        markers: Repères de la règle de cette séquence.
        transitions: Transitions entre clips de cette séquence.
        ducking_sidechains: Associations de ducking (pistes de la séquence).
    """

    id: str
    name: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    transitions: list["Transition"] = field(default_factory=list)
    ducking_sidechains: list = field(default_factory=list)
    # --- Motion graphics ---
    # Guides du viewer (:class:`core.canvas_guides.Guide`) : jamais exportés.
    guides: list = field(default_factory=list)
    # Réglages du flou de mouvement (:class:`core.motion_blur.MotionBlurSettings`).
    motion_blur: object = field(default_factory=lambda: _default_motion_blur())

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValueError("Une séquence doit avoir un identifiant non vide.")
        _validate_canvas(self.width, self.height, self.fps, "de la séquence")
        if self.markers is None:
            self.markers = []
        if self.transitions is None:
            self.transitions = []
        if self.ducking_sidechains is None:
            self.ducking_sidechains = []
        if self.guides is None:
            self.guides = []
        if self.motion_blur is None:
            self.motion_blur = _default_motion_blur()

    @property
    def duration(self) -> float:
        """Fin du dernier clip activé (``0.0`` pour une séquence vide).

        Le calcul ne descend pas dans les séquences imbriquées : la durée
        d'un clip imbriqué est son propre ``in``/``out``, donc une séquence
        cyclique (corrompue) ne peut pas faire boucler ce calcul.
        """
        ends = [
            clip.timeline_start + clip.duration
            for track in self.tracks
            for clip in track.clips
            if clip.enabled
        ]
        return max(ends) if ends else 0.0

    @property
    def nested_sequence_ids(self) -> set[str]:
        """Séquences référencées **directement** par les clips de celle-ci."""
        return {
            clip.sequence_id
            for track in self.tracks
            for clip in track.clips
            if clip.sequence_id
        }


@dataclass(init=False)
class Project:
    """Le projet complet : médias importés + séquences.

    Un projet contient **au moins une** :class:`Sequence`. L'une d'elles
    est la séquence *active* (celle que la timeline édite). Pour rester
    compatible avec tout le code écrit avant le multi-séquence,
    ``tracks``, ``markers``, ``transitions``, ``ducking_sidechains``,
    ``width``, ``height`` et ``fps`` sont des **propriétés qui délèguent à
    la séquence active** : une opération de timeline appliquée au projet
    modifie la séquence ouverte, l'export rend la séquence ouverte.

    Le constructeur accepte toujours l'ancienne forme
    ``Project(name, width, height, fps, media_assets, tracks, ...)`` : elle
    crée une séquence principale (:data:`MAIN_SEQUENCE_ID`). La forme
    multi-séquence passe ``sequences=[...]`` et ``active_sequence_id``.

    Attributes:
        name: Nom humain du projet.
        media_assets: Médias importés, partagés par toutes les séquences.
        sequences: Séquences du projet (au moins une).
        active_sequence_id: Séquence éditée par la timeline.
        library_folders: Dossiers personnalisés de la bibliothèque
            (tâche 25). Vide pour un projet créé avant la v11.
        library_tags: Tags (couleur / étiquette) de la bibliothèque
            (tâche 25). Vide pour un projet créé avant la v11.
        library_assignments: Position et tags de chaque média, indexés
            par ``asset_id``. Une affectation est créée à la demande
            par :class:`~core.library_organization.LibraryOrganization`
            — les médias non mentionnés sont implicitement à la racine
            sans tag.
        color_presets: Presets couleur personnels embarqués (tâche 29).
            Le type concret est ``ColorPreset`` ; ``list`` évite un cycle
            d'import avec :mod:`core.color_grading`.
    """

    name: str
    media_assets: list[MediaAsset]
    sequences: list[Sequence]
    active_sequence_id: str
    library_folders: list
    library_tags: list
    library_assignments: dict
    color_presets: list

    def __init__(
        self,
        name: str,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        media_assets: list[MediaAsset] | None = None,
        tracks: list[Track] | None = None,
        markers: list[Marker] | None = None,
        transitions: list | None = None,
        library_folders: list | None = None,
        library_tags: list | None = None,
        library_assignments: dict | None = None,
        ducking_sidechains: list | None = None,
        color_presets: list | None = None,
        *,
        sequences: list[Sequence] | None = None,
        active_sequence_id: str | None = None,
    ) -> None:
        self.name = name
        # Les listes passées sont conservées telles quelles (même identité),
        # comme avec l'ancien dataclass.
        self.media_assets = media_assets if media_assets is not None else []
        if sequences:
            if tracks or markers or transitions or ducking_sidechains:
                raise ValueError(
                    "Projet ambigu : pistes, repères ou transitions fournis "
                    "en plus de « sequences ». Placez-les dans une séquence."
                )
            self.sequences = sequences
        else:
            _validate_canvas(width, height, fps, "du projet")
            self.sequences = [
                Sequence(
                    id=MAIN_SEQUENCE_ID,
                    name=MAIN_SEQUENCE_NAME,
                    width=width,
                    height=height,
                    fps=fps,
                    tracks=tracks if tracks is not None else [],
                    markers=markers if markers is not None else [],
                    transitions=transitions if transitions is not None else [],
                    ducking_sidechains=(
                        ducking_sidechains if ducking_sidechains is not None else []
                    ),
                )
            ]
        known = {sequence.id for sequence in self.sequences}
        self.active_sequence_id = (
            active_sequence_id if active_sequence_id in known else self.sequences[0].id
        )
        # Les trois champs d'organisation acceptent ``None`` (rétrocompat
        # chargement depuis ancien snapshot) en retombant sur du vide.
        self.library_folders = library_folders if library_folders is not None else []
        self.library_tags = library_tags if library_tags is not None else []
        self.library_assignments = (
            library_assignments if library_assignments is not None else {}
        )
        self.color_presets = color_presets if color_presets is not None else []

    # ------------------------------------------------------------------
    # Séquences
    # ------------------------------------------------------------------

    def get_sequence(self, sequence_id: str) -> Sequence | None:
        """Séquence d'identifiant ``sequence_id``, ou ``None`` (jamais d'exception)."""
        for sequence in self.sequences:
            if sequence.id == sequence_id:
                return sequence
        return None

    def all_tracks(self) -> list[Track]:
        """Pistes de **toutes** les séquences (usages de médias, LUTs, caches)."""
        return [track for sequence in self.sequences for track in sequence.tracks]

    @property
    def active_sequence(self) -> Sequence:
        """Séquence éditée par la timeline.

        Un identifiant actif inconnu (séquence supprimée, fichier
        retouché à la main) retombe sur la première séquence au lieu de
        lever : le projet doit rester ouvrable.
        """
        sequence_id = self.active_sequence_id
        for sequence in self.sequences:
            if sequence.id == sequence_id:
                return sequence
        if not self.sequences:
            self.sequences.append(Sequence(id=MAIN_SEQUENCE_ID, name=MAIN_SEQUENCE_NAME))
        self.active_sequence_id = self.sequences[0].id
        return self.sequences[0]

    # --- Délégation à la séquence active (compatibilité) ---------------

    @property
    def tracks(self) -> list[Track]:
        return self.active_sequence.tracks

    @tracks.setter
    def tracks(self, value: list[Track]) -> None:
        self.active_sequence.tracks = value

    @property
    def markers(self) -> list[Marker]:
        return self.active_sequence.markers

    @markers.setter
    def markers(self, value: list[Marker]) -> None:
        self.active_sequence.markers = value

    @property
    def transitions(self) -> list:
        return self.active_sequence.transitions

    @transitions.setter
    def transitions(self, value: list) -> None:
        self.active_sequence.transitions = value

    @property
    def ducking_sidechains(self) -> list:
        return self.active_sequence.ducking_sidechains

    @ducking_sidechains.setter
    def ducking_sidechains(self, value: list) -> None:
        self.active_sequence.ducking_sidechains = value if value is not None else []

    @property
    def width(self) -> int:
        return self.active_sequence.width

    @width.setter
    def width(self, value: int) -> None:
        _validate_canvas(value, 1, 1.0, "du projet")
        self.active_sequence.width = value

    @property
    def height(self) -> int:
        return self.active_sequence.height

    @height.setter
    def height(self, value: int) -> None:
        _validate_canvas(1, value, 1.0, "du projet")
        self.active_sequence.height = value

    @property
    def fps(self) -> float:
        return self.active_sequence.fps

    @fps.setter
    def fps(self, value: float) -> None:
        _validate_canvas(1, 1, value, "du projet")
        self.active_sequence.fps = value


def _default_compositing():
    from .compositing import Compositing
    return Compositing()
