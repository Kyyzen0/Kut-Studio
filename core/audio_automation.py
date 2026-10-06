"""Automation audio, rôles de piste et ducking automatique (tâche 28).

Ce module étend le modèle de mixage pour gérer :

- :class:`TrackRole` — rôle sémantique d'une piste audio (``"voice"``,
  ``"music"``, ``"sfx"`` ou ``"other"``) ;
- :class:`AutomationPoint` — un point-clé de la courbe d'automation
  (temps + gain + fondu) ;
- :class:`TrackAutomation` — séquence ordonnée de :class:`AutomationPoint`
  pour une piste ; fournit une évaluation déterministe
  (:meth:`TrackAutomation.gain_at`) qui retourne le gain appliqué à un
  instant arbitraire ;
- :class:`DuckingConfig` — réglages de ducking (seuil, réduction,
  attaque, relâchement) ;
- :class:`DuckingSidechain` — association d'une piste ``music`` à
  réduire avec une ou plusieurs pistes ``voice`` qui pilotent
  l'atténuation.

L'algorithme d'évaluation est volontairement **pur et déterministe** :
pas de dépendance à un état externe, pas d'horloge. Les fonctions
utilisées par le moteur de rendu (FFmpeg) sont implémentées dans
:mod:`core.export_engine`.

Règles métier principales :

- l'automation est par piste et **s'ajoute** au gain de piste
  (``track.volume_db``), pas au gain de clip ; le résultat est sommé en
  dB ;
- les points sont triés par temps ; un nouveau point hors borne est
  rejeté ; un point invalide (temps négatif, valeurs non finies) lève
  ``ValueError`` ;
- le ducking opère *uniquement* sur les pistes marquées ``music`` (ou
  ``sfx``) ; les autres pistes sont des pilotes (``voice``) ;
- un projet sans configuration de ducking n'applique aucune
  atténuation automatique : le comportement historique est préservé.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


# Bornes de gain en dB : cohérentes avec :mod:`core.audio_mixer` qui
# borne déjà ``gain_db`` à ``[MIN_GAIN_DB, MAX_GAIN_DB]``. On n'élargit
# pas ici : un gain hors bornes est *toujours* un bug d'orchestration.
DEFAULT_GAIN_MIN_DB: float = -24.0
DEFAULT_GAIN_MAX_DB: float = 12.0

# Bornes du fondu d'un point d'automation : 0 = coupure immédiate entre
# les deux points adjacents, 5 s = interpolation très douce. On plafonne
# volontairement à 5 s pour éviter qu'un fondu mal calibré rende la
# piste muette pendant plusieurs secondes.
MAX_POINT_FADE_SECONDS: float = 5.0
MIN_POINT_FADE_SECONDS: float = 0.0

# Bornes du ducking : on autorise des seuils agressifs (la musique peut
# chuter jusqu'à -36 dB sous une voix forte) mais on plafonne les
# durées d'attaque / relâchement pour garder un ducking réactif.
MIN_DUCKING_ATTACK_S: float = 0.005
MAX_DUCKING_ATTACK_S: float = 5.0
MIN_DUCKING_RELEASE_S: float = 0.01
MAX_DUCKING_RELEASE_S: float = 10.0
MIN_DUCKING_THRESHOLD_DB: float = -60.0
MAX_DUCKING_THRESHOLD_DB: float = 0.0
MIN_DUCKING_REDUCTION_DB: float = 0.0
MAX_DUCKING_REDUCTION_DB: float = 36.0


_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]+$")


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


class AudioAutomationError(ValueError):
    """Erreur métier de l'automation / ducking.

    Toutes les fonctions de ce module lèvent cette exception (ou une
    sous-classe) quand une opération est rejetée. Les messages
    restent en français pour rester cohérents avec le reste du projet.
    """


class AudioAutomationNameError(AudioAutomationError):
    """Le nom / identifiant fourni est vide ou trop long."""


class AudioAutomationRangeError(AudioAutomationError):
    """Une valeur numérique est hors bornes."""


class AudioAutomationUnknownIdError(AudioAutomationError):
    """L'identifiant fourni ne correspond à aucun élément connu."""


class AudioAutomationCycleError(AudioAutomationError):
    """Une dépendance cyclique est détectée (rare ; sécurité)."""


class AudioAutomationOverlapError(AudioAutomationError):
    """Deux éléments portent le même identifiant."""


# ---------------------------------------------------------------------------
# Modèles de données
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    """Génère un identifiant court, lisible et unique."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _require_id(value: str, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise AudioAutomationNameError(
            f"L'identifiant du {kind} ne peut pas être vide."
        )
    if not _ID_PATTERN.match(value):
        raise AudioAutomationNameError(
            f"Identifiant de {kind} invalide : {value!r}."
        )
    return value


def _require_finite(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioAutomationRangeError(
            f"Valeur non numérique pour {name!r} : {value!r}."
        ) from exc
    if math.isnan(number) or math.isinf(number):
        raise AudioAutomationRangeError(
            f"Valeur non finie pour {name!r} : {number!r}."
        )
    return number


class TrackRole(str, Enum):
    """Rôle sémantique d'une piste audio.

    Le rôle pilote les choix d'orchestration (ducking, suggestions UI)
    mais **n'altère pas le rendu** d'une piste isolée : une piste voix
    sans configuration de ducking se comporte exactement comme
    avant. C'est :class:`DuckingConfig` qui détermine les
    atténuations à l'export.
    """

    VOICE = "voice"  # voix off / dialogue
    MUSIC = "music"  # musique de fond
    SFX = "sfx"  # bruitages / effets sonores
    OTHER = "other"  # tout autre audio (par défaut)


# Libellés humains pour les rôles.
TRACK_ROLE_LABELS: dict[TrackRole, str] = {
    TrackRole.VOICE: "Voix",
    TrackRole.MUSIC: "Musique",
    TrackRole.SFX: "Effets sonores",
    TrackRole.OTHER: "Autre",
}


def _coerce_db(value: float, name: str, *, minimum: float, maximum: float) -> float:
    number = _require_finite(value, name)
    if number < minimum or number > maximum:
        raise AudioAutomationRangeError(
            f"Valeur {name!r} hors bornes [{minimum}, {maximum}] : {number}."
        )
    return number


@dataclass(frozen=True)
class AutomationPoint:
    """Un point-clé de la courbe d'automation de volume.

    Attributes:
        time_seconds: Position du point sur la timeline (>= 0).
        gain_db: Gain appliqué à partir de ce point (dB).
        fade_seconds: Durée du fondu entre ce point et le précédent.
            ``0`` = coupure immédiate (gain constant jusqu'au point
            suivant). Doit rester dans
            ``[MIN_POINT_FADE_SECONDS, MAX_POINT_FADE_SECONDS]``.
    """

    time_seconds: float
    gain_db: float
    fade_seconds: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_seconds", _require_finite(
            self.time_seconds, "time_seconds"
        ))
        if self.time_seconds < 0.0:
            raise AudioAutomationRangeError(
                f"time_seconds négatif : {self.time_seconds}."
            )
        object.__setattr__(self, "gain_db", _require_finite(
            self.gain_db, "gain_db"
        ))
        object.__setattr__(self, "fade_seconds", _require_finite(
            self.fade_seconds, "fade_seconds"
        ))
        if self.fade_seconds < MIN_POINT_FADE_SECONDS:
            raise AudioAutomationRangeError(
                f"fade_seconds négatif : {self.fade_seconds}."
            )
        if self.fade_seconds > MAX_POINT_FADE_SECONDS:
            raise AudioAutomationRangeError(
                f"fade_seconds trop grand ({self.fade_seconds} > "
                f"{MAX_POINT_FADE_SECONDS})."
            )


@dataclass
class TrackAutomation:
    """Courbe d'automation de volume pour une piste.

    La séquence de points est **ordonnée par temps croissant**. Les
    méthodes :meth:`add_point`, :meth:`update_point` et
    :meth:`remove_point` maintiennent cet invariant.

    L'évaluation :meth:`gain_at` interpole linéairement entre les
    points qui entourent ``time_seconds``. Avant le premier point, le
    gain est nul (``-inf`` n'est pas un gain valide : on retourne
    ``DEFAULT_GAIN_MIN_DB``). Après le dernier point, le gain est
    celui du dernier point.

    C'est la **forme canonique** de :attr:`core.project_model.Track.automation` :
    chaque piste en porte une (vide par défaut). ``track_id`` n'est qu'une
    étiquette pour les messages d'erreur, sans contrainte de forme (un
    identifiant de piste hérité d'un ancien fichier, avec espaces ou accents,
    ne doit pas empêcher d'ouvrir le projet) et hors de la comparaison : deux
    courbes de mêmes points sont égales.
    """

    track_id: str = field(default="", compare=False)
    points: list[AutomationPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.track_id, str):
            raise AudioAutomationNameError(
                f"L'identifiant de la piste doit être une chaîne : {self.track_id!r}."
            )
        # Normalise : trie par points, ne mute pas la liste si elle est
        # déjà dans le bon ordre (important pour les tests qui passent
        # une liste figée).
        self.points = sorted(
            (AutomationPoint(**vars(p)) if not isinstance(p, AutomationPoint) else p
             for p in self.points),
            key=lambda p: p.time_seconds,
        )

    def add_point(self, time_seconds: float, gain_db: float, fade_seconds: float = 0.0) -> AutomationPoint:
        """Insère un point (tri par temps)."""
        point = AutomationPoint(time_seconds, gain_db, fade_seconds)
        if not self.points:
            self.points.append(point)
            return point
        for index, existing in enumerate(self.points):
            if abs(existing.time_seconds - point.time_seconds) < 1e-9:
                # Remplace le point existant au même temps.
                self.points[index] = point
                return point
            if existing.time_seconds > point.time_seconds:
                self.points.insert(index, point)
                return point
        self.points.append(point)
        return point

    def remove_point(self, time_seconds: float) -> AutomationPoint:
        """Supprime le premier point situé à ``time_seconds`` (± 1 ms)."""
        for index, existing in enumerate(self.points):
            if abs(existing.time_seconds - time_seconds) < 1e-9:
                return self.points.pop(index)
        raise AudioAutomationUnknownIdError(
            f"Aucun point d'automation à {time_seconds} s pour {self.track_id!r}."
        )

    def update_point(
        self,
        time_seconds: float,
        *,
        gain_db: float | None = None,
        fade_seconds: float | None = None,
    ) -> AutomationPoint:
        """Modifie les champs d'un point existant identifié par son temps."""
        for index, existing in enumerate(self.points):
            if abs(existing.time_seconds - time_seconds) < 1e-9:
                new_gain = existing.gain_db if gain_db is None else float(gain_db)
                new_fade = (
                    existing.fade_seconds
                    if fade_seconds is None else float(fade_seconds)
                )
                replaced = AutomationPoint(
                    time_seconds=existing.time_seconds,
                    gain_db=new_gain,
                    fade_seconds=new_fade,
                )
                self.points[index] = replaced
                return replaced
        raise AudioAutomationUnknownIdError(
            f"Aucun point d'automation à {time_seconds} s pour {self.track_id!r}."
        )

    def is_empty(self) -> bool:
        return not self.points

    def gain_at(self, time_seconds: float) -> float:
        """Retourne le gain (dB) appliqué à ``time_seconds``.

        Avant le premier point, retourne ``DEFAULT_GAIN_MIN_DB``
        (silence). Après le dernier, retourne le gain du dernier
        point. Entre deux points, on interpole linéairement sur la
        fenêtre ``[t_prev + fade_prev, t_curr]`` (le fondu déclaré sur
        le point *destination* détermine la durée pendant laquelle on
        passe de ``gain_prev`` à ``gain_curr`` ; en deçà, on conserve
        ``gain_prev``).
        """
        if not self.points:
            return 0.0
        if time_seconds <= self.points[0].time_seconds:
            return float(self.points[0].gain_db)
        if time_seconds >= self.points[-1].time_seconds:
            return float(self.points[-1].gain_db)
        for index in range(1, len(self.points)):
            previous = self.points[index - 1]
            current = self.points[index]
            if time_seconds <= current.time_seconds:
                fade = float(current.fade_seconds)
                if fade <= 0.0:
                    return float(current.gain_db)
                # Avant la fin du fondu, on conserve le gain précédent.
                if time_seconds <= previous.time_seconds + fade:
                    return float(previous.gain_db)
                # Au-delà, on interpole linéairement entre la fin du
                # fondu et le point courant.
                fade_end = previous.time_seconds + fade
                span = current.time_seconds - fade_end
                if span <= 0.0:
                    return float(current.gain_db)
                progress = (time_seconds - fade_end) / span
                progress = max(0.0, min(1.0, progress))
                return float(previous.gain_db) + progress * (
                    float(current.gain_db) - float(previous.gain_db)
                )
        return float(self.points[-1].gain_db)


def coerce_track_automation(value: object, track_id: str = "") -> TrackAutomation:
    """Ramène ce qu'on affecte à ``Track.automation`` à sa forme canonique.

    Une :class:`TrackAutomation` est rendue **telle quelle** (même objet : les
    éditions en place restent visibles ; une courbe encore sans identifiant
    prend celui de la piste). ``None`` donne une courbe vide ; une liste ou un
    tuple de points, l'ancienne forme, est enveloppé et trié. Tout autre type
    lève ``TypeError`` : mieux vaut refuser une affectation absurde que
    perdre une courbe en silence. L'opération est idempotente.
    """
    if isinstance(value, TrackAutomation):
        if not value.track_id:
            value.track_id = track_id
        return value
    if value is None:
        return TrackAutomation(track_id=track_id)
    if isinstance(value, (list, tuple)):
        return TrackAutomation(track_id=track_id, points=list(value))
    raise TypeError(
        "Track.automation attend une TrackAutomation ou une liste de AutomationPoint, "
        f"pas {type(value).__name__}."
    )


# ---------------------------------------------------------------------------
# Ducking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuckingConfig:
    """Réglages de ducking (sidechain compression sur la musique).

    Attributes:
        threshold_db: Niveau au-delà duquel la voix déclenche le
            ducking (dB). Au-dessus de ce seuil, la musique est
            atténuée proportionnellement à l'écart.
        reduction_db: Atténuation maximale appliquée à la musique
            (dB, valeur positive). ``12`` = -12 dB quand la voix
            dépasse suffisamment le seuil.
        attack_seconds: Temps de montée de l'atténuation (s). Plus
            court = ducking plus réactif ; plus long = plus doux.
        release_seconds: Temps de relâchement de l'atténuation (s).
            Plus long = la musique remonte progressivement après la
            voix.
    """

    threshold_db: float = -20.0
    reduction_db: float = 12.0
    attack_seconds: float = 0.05
    release_seconds: float = 0.4

    def __post_init__(self) -> None:
        object.__setattr__(self, "threshold_db", _coerce_db(
            self.threshold_db, "threshold_db",
            minimum=MIN_DUCKING_THRESHOLD_DB, maximum=MAX_DUCKING_THRESHOLD_DB,
        ))
        object.__setattr__(self, "reduction_db", _coerce_db(
            self.reduction_db, "reduction_db",
            minimum=MIN_DUCKING_REDUCTION_DB, maximum=MAX_DUCKING_REDUCTION_DB,
        ))
        object.__setattr__(self, "attack_seconds", _coerce_db(
            self.attack_seconds, "attack_seconds",
            minimum=MIN_DUCKING_ATTACK_S, maximum=MAX_DUCKING_ATTACK_S,
        ))
        object.__setattr__(self, "release_seconds", _coerce_db(
            self.release_seconds, "release_seconds",
            minimum=MIN_DUCKING_RELEASE_S, maximum=MAX_DUCKING_RELEASE_S,
        ))

    @property
    def ratio(self) -> float:
        """Ratio effectif dérivé (utilisé par ``sidechaincompress``).

        On calcule un ratio élevé pour atteindre la réduction demandée
        : avec un ratio de 20, un dépassement de 10 dB au-dessus du
        seuil donne 19 dB d'atténuation (de quoi approcher la cible de
        ``reduction_db``). FFmpeg applique ``20:1`` plus généreusement
        que des ratios plus modestes.
        """
        return 20.0


@dataclass(frozen=True)
class DuckingSidechain:
    """Association ducking : une piste musique à réduire par une voix.

    Plusieurs associations peuvent coexister pour la même piste
    musique ; on les combine en retenant l'atténuation la plus
    profonde à chaque instant (logique ``max``).

    Attributes:
        id: Identifiant unique.
        music_track_id: Piste à atténuer (rôle ``music`` ou ``sfx``).
        voice_track_id: Piste qui pilote le ducking (rôle ``voice``).
        config: Réglages de ducking.
        enabled: ``False`` désactive cette association sans la
            supprimer.
    """

    id: str
    music_track_id: str
    voice_track_id: str
    config: DuckingConfig = field(default_factory=DuckingConfig)
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_id(self.id, "sidechain"))
        object.__setattr__(self, "music_track_id", _require_id(self.music_track_id, "piste musique"))
        object.__setattr__(self, "voice_track_id", _require_id(self.voice_track_id, "piste voix"))
        if self.music_track_id == self.voice_track_id:
            raise AudioAutomationError(
                "Une piste ne peut pas se ducking elle-même."
            )


def _new_sidechain_id() -> str:
    return _new_id("duck")


DUCKING_PRESETS: dict[str, DuckingConfig] = {
    # Musique d'ambiance sous une voix posée : discret, remonte lentement.
    "gentle": DuckingConfig(threshold_db=-30.0, reduction_db=6.0, attack_seconds=0.08, release_seconds=0.6),
    # Voix off sur musique : le réglage par défaut d'un montage parlé.
    "voice_over_music": DuckingConfig(threshold_db=-28.0, reduction_db=12.0, attack_seconds=0.05, release_seconds=0.4),
    # Vidéo sociale : la musique s'efface net sous chaque phrase et revient sur le temps suivant.
    "social_punchy": DuckingConfig(threshold_db=-32.0, reduction_db=18.0, attack_seconds=0.02, release_seconds=0.25),
}
"""Réglages de ducking prêts à l'emploi (clés de traduction ``ducking.preset.<id>``)."""


def duck_music_under_voice(project, preset: str = "voice_over_music") -> list[DuckingSidechain]:
    """« Ducker la musique sous la voix » : chaque piste musique baisse sous chaque piste voix, avec le preset.

    Une association qui existe déjà reçoit le nouveau réglage (pas de doublon). Retourne les associations concernées."""
    config = DUCKING_PRESETS[preset]
    tracks = [track for track in project.tracks if track.type == "audio"]
    music = [track for track in tracks if str(getattr(track.audio_role, "value", track.audio_role)) == "music"]
    voices = [track for track in tracks if str(getattr(track.audio_role, "value", track.audio_role)) == "voice"]
    if not music or not voices:
        raise AudioAutomationError("Il faut une piste « musique » et une piste « voix » (rôle de la piste).")
    bucket = project.ducking_sidechains
    result = []
    for music_track in music:
        for voice in voices:
            index = next((i for i, item in enumerate(bucket)
                          if item.music_track_id == music_track.id and item.voice_track_id == voice.id), None)
            if index is None:
                sidechain = DuckingSidechain(_new_sidechain_id(), music_track.id, voice.id, config)
                bucket.append(sidechain)
            else:
                sidechain = DuckingSidechain(bucket[index].id, music_track.id, voice.id, config, True)
                bucket[index] = sidechain
            result.append(sidechain)
    return result


# ---------------------------------------------------------------------------
# Opérations de service
# ---------------------------------------------------------------------------


@dataclass
class AudioAutomationService:
    """Service haut-niveau : opérations CRUD + validations croisées.

    Le service stocke ses données dans des champs du :class:`Track`
    (``track.audio_role``, ``track.automation``,
    ``track.ducking_config``, ``track.ducking_sidechains``) introduits
    par la tâche 28. Les méthodes de service **lisent / écrivent** ces
    champs en place, sans copie profonde, pour rester compatibles avec
    le :class:`ProjectHistory` qui capture le projet entier.
    """

    def __init__(self) -> None:
        pass

    # ----- Rôles de piste ----------------------------------------------

    def set_track_role(self, project, track_id: str, role: TrackRole | str) -> TrackRole:
        """Définit le rôle d'une piste et retourne la valeur stockée."""
        track = self._find_audio_track(project, track_id)
        normalized = TrackRole(role)
        # ``track.audio_role`` est introduit par la tâche 28 ; les
        # projets anciens (v10- et jusqu'à v11.0) n'ont pas encore
        # ce champ. On l'ajoute dynamiquement pour rester
        # rétro-compatible avec les snapshots d'historique.
        if not hasattr(track, "audio_role") or track.audio_role is None:
            object.__setattr__(track, "audio_role", normalized)
        else:
            object.__setattr__(track, "audio_role", normalized)
        return track.audio_role

    def get_track_role(self, project, track_id: str) -> TrackRole:
        """Lit le rôle d'une piste (défaut : ``OTHER``)."""
        track = self._find_audio_track(project, track_id)
        role = getattr(track, "audio_role", None)
        return role if isinstance(role, TrackRole) else TrackRole.OTHER

    # ----- Automation ----------------------------------------------------

    def ensure_automation(self, project, track_id: str) -> "TrackAutomation":
        """Retourne l'automation d'une piste (idempotent).

        Le modèle garantit que ``track.automation`` est toujours une
        :class:`TrackAutomation` (vide par défaut, normalisée à l'affectation
        et au chargement) : il n'y a plus rien à envelopper ici.
        """
        return self._find_audio_track(project, track_id).automation

    def add_automation_point(
        self,
        project,
        track_id: str,
        time_seconds: float,
        gain_db: float,
        fade_seconds: float = 0.0,
    ) -> AutomationPoint:
        """Ajoute un point d'autation sur la piste."""
        track = self._find_audio_track(project, track_id)
        automation = self.ensure_automation(project, track.id)
        return automation.add_point(time_seconds, gain_db, fade_seconds)

    def remove_automation_point(
        self,
        project,
        track_id: str,
        time_seconds: float,
    ) -> AutomationPoint:
        """Supprime un point d'automation existant."""
        automation = self.ensure_automation(project, track_id)
        return automation.remove_point(time_seconds)

    def update_automation_point(
        self,
        project,
        track_id: str,
        time_seconds: float,
        *,
        gain_db: float | None = None,
        fade_seconds: float | None = None,
    ) -> AutomationPoint:
        """Modifie un point existant."""
        automation = self.ensure_automation(project, track_id)
        return automation.update_point(
            time_seconds, gain_db=gain_db, fade_seconds=fade_seconds
        )

    def clear_automation(self, project, track_id: str) -> None:
        """Vide la courbe d'automation d'une piste."""
        automation = self.ensure_automation(project, track_id)
        automation.points.clear()

    # ----- Ducking -------------------------------------------------------

    def add_ducking_sidechain(
        self,
        project,
        music_track_id: str,
        voice_track_id: str,
        config: DuckingConfig | None = None,
    ) -> DuckingSidechain:
        """Crée une association de ducking musique ← voix."""
        music = self._find_audio_track(project, music_track_id)
        voice = self._find_audio_track(project, voice_track_id)
        if music.id == voice.id:
            raise AudioAutomationError(
                "Une piste ne peut pas se ducking elle-même."
            )
        sidechain = DuckingSidechain(
            id=_new_sidechain_id(),
            music_track_id=music.id,
            voice_track_id=voice.id,
            config=config or DuckingConfig(),
        )
        bucket = self._sidechains(project)
        if any(
            s.music_track_id == music.id and s.voice_track_id == voice.id
            for s in bucket
        ):
            raise AudioAutomationOverlapError(
                f"Un ducking existe déjà entre {music.id!r} et {voice.id!r}."
            )
        bucket.append(sidechain)
        return sidechain

    def remove_ducking_sidechain(
        self,
        project,
        sidechain_id: str,
    ) -> DuckingSidechain:
        bucket = self._sidechains(project)
        for index, sidechain in enumerate(bucket):
            if sidechain.id == sidechain_id:
                return bucket.pop(index)
        raise AudioAutomationUnknownIdError(
            f"Aucun ducking avec l'identifiant {sidechain_id!r}."
        )

    def update_ducking_config(
        self,
        project,
        sidechain_id: str,
        config: DuckingConfig,
    ) -> DuckingSidechain:
        bucket = self._sidechains(project)
        for index, sidechain in enumerate(bucket):
            if sidechain.id == sidechain_id:
                replaced = DuckingSidechain(
                    id=sidechain.id,
                    music_track_id=sidechain.music_track_id,
                    voice_track_id=sidechain.voice_track_id,
                    config=config,
                    enabled=sidechain.enabled,
                )
                bucket[index] = replaced
                return replaced
        raise AudioAutomationUnknownIdError(
            f"Aucun ducking avec l'identifiant {sidechain_id!r}."
        )

    def set_ducking_enabled(
        self,
        project,
        sidechain_id: str,
        enabled: bool,
    ) -> DuckingSidechain:
        bucket = self._sidechains(project)
        for index, sidechain in enumerate(bucket):
            if sidechain.id == sidechain_id:
                replaced = DuckingSidechain(
                    id=sidechain.id,
                    music_track_id=sidechain.music_track_id,
                    voice_track_id=sidechain.voice_track_id,
                    config=sidechain.config,
                    enabled=bool(enabled),
                )
                bucket[index] = replaced
                return replaced
        raise AudioAutomationUnknownIdError(
            f"Aucun ducking avec l'identifiant {sidechain_id!r}."
        )

    def sidechains_for(self, project, music_track_id: str) -> list[DuckingSidechain]:
        """Liste les duckings qui ciblent ``music_track_id``."""
        return [
            s for s in self._sidechains(project) if s.music_track_id == music_track_id
        ]

    def sidechains_from(self, project, voice_track_id: str) -> list[DuckingSidechain]:
        """Liste les duckings pilotés par ``voice_track_id``."""
        return [
            s for s in self._sidechains(project) if s.voice_track_id == voice_track_id
        ]

    def all_sidechains(self, project) -> list[DuckingSidechain]:
        """Copie la liste de toutes les associations de ducking."""
        return list(self._sidechains(project))

    # ----- Helpers internes ---------------------------------------------

    def _find_audio_track(self, project, track_id: str):
        for track in project.tracks:
            if track.id != track_id:
                continue
            if track.type not in ("audio", "video"):
                raise AudioAutomationError(
                    f"La piste {track_id!r} n'est pas audio ou vidéo ; "
                    "l'automation audio ne s'y applique pas."
                )
            return track
        raise AudioAutomationUnknownIdError(
            f"Piste {track_id!r} introuvable."
        )

    def _sidechains(self, project) -> list[DuckingSidechain]:
        bucket = getattr(project, "ducking_sidechains", None)
        if bucket is None:
            bucket = []
            object.__setattr__(project, "ducking_sidechains", bucket)
        return bucket


__all__ = [
    "DUCKING_PRESETS",
    "duck_music_under_voice",
    # Erreurs
    "AudioAutomationCycleError",
    "AudioAutomationError",
    "AudioAutomationNameError",
    "AudioAutomationOverlapError",
    "AudioAutomationRangeError",
    "AudioAutomationUnknownIdError",
    # Modèles
    "AutomationPoint",
    "DuckingConfig",
    "DuckingSidechain",
    "TrackAutomation",
    "TrackRole",
    "coerce_track_automation",
    # Constantes
    "DEFAULT_GAIN_MAX_DB",
    "DEFAULT_GAIN_MIN_DB",
    "MAX_DUCKING_ATTACK_S",
    "MAX_DUCKING_REDUCTION_DB",
    "MAX_DUCKING_RELEASE_S",
    "MAX_DUCKING_THRESHOLD_DB",
    "MAX_POINT_FADE_SECONDS",
    "MIN_DUCKING_ATTACK_S",
    "MIN_DUCKING_REDUCTION_DB",
    "MIN_DUCKING_RELEASE_S",
    "MIN_DUCKING_THRESHOLD_DB",
    "MIN_POINT_FADE_SECONDS",
    "TRACK_ROLE_LABELS",
    # Service
    "AudioAutomationService",
]