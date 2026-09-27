"""Mixage audio non destructif — couche métier pure de Kut-Studio.

Ce module répond à une seule question : *qu'est-ce qui s'entend, et avec
quel gain*, à un instant donné de la timeline. Il ne produit aucun son :
il décrit le mixage sous forme de données déterministes, que
consomment ensuite

- l'interface (mixeur, inspecteur, poignées de fondu) ;
- :mod:`core.render_plan` et :mod:`core.export_engine` (filtres FFmpeg).

Aucune dépendance à PySide6 ni à FFmpeg : le module est testable en CLI
et réutilisable par un exporteur GPU ou un serveur.

Décision centrale : le panoramique est décrit par des **gains
stéréo** explicites (``gain_left`` / ``gain_right``), et non par un
paramètre de panoramique. Un moteur de rendu peut ainsi consommer la
spécification telle quelle, et deux moteurs peuvent être comparés sans
convention cachée.

Convention de puissance constante : le panoramique atténue, il
n'amplifie jamais. Un son centré à 0 dB ressort donc à 0 dB ; poussé
à fond d'un côté il est atténué, ce qui évite toute surprise de
niveau lors d'un déplacement au pan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .project_model import MAX_GAIN_DB, MIN_GAIN_DB, Clip, Project, Track


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def db_to_linear(db: float) -> float:
    """Convertit des décibels en facteur linéaire.

    ``-inf`` dB → 0.0, 0 dB → 1.0. La valeur est bornée à la plage
    autorisée par le modèle pour qu'un export ne produise jamais une
    amplification incontrôlée.
    """
    if db is None or db != db:  # None ou NaN
        return 1.0
    clamped = max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(db)))
    if clamped <= MIN_GAIN_DB:
        return 0.0
    return 10.0 ** (clamped / 20.0)


def linear_to_db(linear: float) -> float:
    """Convertit un facteur linéaire en décibels (borne basse = -60 dB)."""
    if linear is None or linear <= 1e-9:
        return MIN_GAIN_DB
    db = 20.0 * math.log10(float(linear))
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, db))


def pan_to_gains(pan: float) -> tuple[float, float]:
    """Traduit un panoramique ``[-1, 1]`` en gains stéréo.

    Constante-power : le gain total reste cohérent quand le son glisse
    d'un côté à l'autre.

    Returns:
        ``(gain_left, gain_right)``, tous deux dans ``[0, 1]``.
    """
    try:
        value = float(pan)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:  # NaN
        value = 0.0
    value = max(-1.0, min(1.0, value))
    angle = (value + 1.0) * (math.pi / 4.0)
    return math.cos(angle), math.sin(angle)


def pan_needs_filter(pan: float) -> bool:
    """Le panoramique exige-t-il un filtre de rendu ?

    Un panoramique centré n'en demande aucun : l'appelant peut ainsi
    **omettre** le filtre plutôt que d'appliquer un panoramique neutre.
    """
    try:
        return abs(float(pan)) > 1e-6
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Fondus
# ---------------------------------------------------------------------------


def fade_envelope(local_time: float, duration: float, fade_in: float, fade_out: float) -> float:
    """Courbe de fondu (0 → 1) à ``local_time`` dans un clip.

    Le fondu d'entrée est linéaire, le fondu de sortie l'est aussi. Les
    deux ne se chevauchent pas (garanti par le modèle). Hors de la zone
    de fondu, l'enveloppe vaut 1.
    """
    if duration <= 0.0:
        return 0.0
    local = max(0.0, min(float(local_time), float(duration)))
    if fade_in > 0.0 and local < fade_in:
        return local / fade_in
    if fade_out > 0.0 and local > duration - fade_out:
        remaining = duration - local
        if remaining <= 0.0:
            return 0.0
        return max(0.0, min(1.0, remaining / fade_out))
    return 1.0


# ---------------------------------------------------------------------------
# Spécification de mixage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MixEntry:
    """Contribution d'un clip au mixage, résolue pour un instant donné.

    Attributes:
        clip_id: Identifiant du clip contributeur.
        track_id: Identifiant de la piste.
        source_path: Chemin du média source.
        local_time: Position dans le clip, en secondes depuis son début.
        gain_db: Gain total already résolu (piste + clip), en dB.
        gain_linear: Ce gain converti en facteur linéaire.
        gain_left / gain_right: Gains stéréo après panoramique.
        fade: Valeur de l'enveloppe de fondu à cet instant (0 → 1).
        audible: Le clip contribue-t-il réellement au mixage.
    """

    clip_id: str
    track_id: str
    source_path: str
    local_time: float
    gain_db: float
    gain_linear: float
    gain_left: float
    gain_right: float
    fade: float
    audible: bool


@dataclass(frozen=True)
class MixSpec:
    """Spécification déterministe du mixage à un instant.

    Elle décrit l'état du mixage, pas le son. Deux appels à
    :func:`mix_at` sur un même projet donnent toujours le même résultat.

    Attributes:
        time_seconds: Instant décrit.
        duration: Durée totale du projet.
        entries: Contribution de chaque clip actif, triée de façon stable.
        audible_track_ids: Pistes audio effectivement entendues.
        master_gain_db: Gain de sortie global.
        master_muted: Sortie globale coupée.
    """

    time_seconds: float
    duration: float
    entries: tuple[MixEntry, ...] = field(default_factory=tuple)
    audible_track_ids: tuple[str, ...] = field(default_factory=tuple)
    master_gain_db: float = 0.0
    master_muted: bool = False

    @property
    def is_silent(self) -> bool:
        """Aucune piste audible : l'export reste valide mais sans son."""
        if self.master_muted:
            return True
        return not any(entry.audible for entry in self.entries)

    def audible_entries(self) -> tuple[MixEntry, ...]:
        """Entrées réellement audibles, dans l'ordre stable du plan."""
        return tuple(entry for entry in self.entries if entry.audible)

    def total_gain_db(self) -> float:
        """Gain du clip le plus fort, en dB (lecture de niveau, pas de vumètre)."""
        audible = self.audible_entries()
        if not audible or self.master_muted:
            return MIN_GAIN_DB
        best = max(entry.gain_linear * entry.fade for entry in audible)
        return linear_to_db(best)


# ---------------------------------------------------------------------------
# Audibilité
# ---------------------------------------------------------------------------


def audio_tracks(project: Project) -> tuple[Track, ...]:
    """Pistes audio du projet, dans l'ordre du projet."""
    return tuple(track for track in project.tracks if track.type == "audio")


def has_solo(project: Project) -> bool:
    """Une piste audio est-elle en solo ?"""
    return any(track.solo for track in audio_tracks(project))


def is_audible(track: Track, *, solo_active: bool) -> bool:
    """Une piste audio est-elle entendue ?

    Le solo l'emporte sur le mute, comme dans tous les montages audio :
    une piste solo est entendue même si elle est mutée, et une piste
    non-solo est coupée dès qu'un solo existe.
    """
    if solo_active:
        return bool(track.solo)
    return not track.muted


def audible_track_ids(project: Project) -> tuple[str, ...]:
    """Identifiants des pistes audio entendues, dans l'ordre du projet."""
    solo_active = has_solo(project)
    return tuple(
        track.id for track in audio_tracks(project)
        if is_audible(track, solo_active=solo_active)
    )


# ---------------------------------------------------------------------------
# Résolution
# ---------------------------------------------------------------------------


def clip_gain_db_at(clip: Clip, track: Track) -> float:
    """Gain total d'un clip : volume de piste + gain de clip."""
    return float(track.volume_db) + float(clip.gain_db)


def mix_at(
    project: Project,
    time_seconds: float,
    *,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
) -> MixSpec:
    """Décrit le mixage du projet à l'instant ``time_seconds``.

    La spécification est **déterministe** : même projet et même instant
    donnent toujours la même liste d'entrées, dans le même ordre. Aucun
    état global n'est consulté.

    Les clips désactivés (``enabled=False``) et les clips dont le média
    est absent ne produisent pas d'entrée. Le panoramique et les fondus
    sont déjà résolus en gains stéréo.
    """
    from .render_plan import _find_asset  # import tardif : évite un cycle

    moment = max(0.0, float(time_seconds))
    solo_active = has_solo(project)
    entries: list[MixEntry] = []

    for track in audio_tracks(project):
        audible = is_audible(track, solo_active=solo_active)
        for clip in track.clips:
            if not clip.enabled:
                continue
            start = float(clip.timeline_start)
            end = start + clip.duration
            if not (start <= moment < end):
                continue
            try:
                asset = _find_asset(project, clip.asset_id)
                path = asset.path
            except KeyError:
                # Média absent : le clip est ignoré plutôt que fatal, pour
                # que l'interface reste utilisable sur un projet incomplet.
                continue

            local = moment - start
            fade = fade_envelope(local, clip.duration, clip.fade_in, clip.fade_out)
            total_db = clip_gain_db_at(clip, track)
            # Le panoramique du clip prime, celui de la piste s'y ajoute
            # par somme bornée : les deux réglages se combinent.
            pan = max(-1.0, min(1.0, float(clip.pan) + float(track.pan)))
            pan_left, pan_right = pan_to_gains(pan)
            linear = db_to_linear(total_db)
            audible_here = audible and fade > 0.0 and linear > 0.0
            entries.append(
                MixEntry(
                    clip_id=clip.id,
                    track_id=track.id,
                    source_path=path,
                    local_time=local,
                    gain_db=total_db,
                    gain_linear=linear,
                    gain_left=linear * pan_left * fade,
                    gain_right=linear * pan_right * fade,
                    fade=fade,
                    audible=audible_here,
                )
            )

    entries.sort(key=lambda entry: (entry.track_id, entry.clip_id))
    return MixSpec(
        time_seconds=moment,
        duration=_project_duration(project),
        entries=tuple(entries),
        audible_track_ids=audible_track_ids(project),
        master_gain_db=float(master_gain_db),
        master_muted=bool(master_muted),
    )


def mix_range(
    project: Project,
    start_seconds: float,
    end_seconds: float,
    *,
    step_seconds: float = 1.0 / 30.0,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
) -> tuple[MixSpec, ...]:
    """Échantillonne le mixage sur une plage temporelle.

    Utile pour prévisualiser une seleção ou vérifier la continuité des
    fondus sans énumérer les clips à la main.
    """
    start = max(0.0, float(start_seconds))
    end = max(start, float(end_seconds))
    step = max(1e-3, float(step_seconds))
    samples: list[MixSpec] = []
    moment = start
    while moment < end:
        samples.append(
            mix_at(
                project,
                moment,
                master_gain_db=master_gain_db,
                master_muted=master_muted,
            )
        )
        moment += step
    if not samples:
        samples.append(
            mix_at(
                project,
                start,
                master_gain_db=master_gain_db,
                master_muted=master_muted,
            )
        )
    return tuple(samples)


def _project_duration(project: Project) -> float:
    from .timeline_evaluator import timeline_duration

    return float(timeline_duration(project))


__all__ = [
    "MixEntry",
    "MixSpec",
    "audio_tracks",
    "audible_track_ids",
    "clip_gain_db_at",
    "db_to_linear",
    "fade_envelope",
    "has_solo",
    "is_audible",
    "linear_to_db",
    "mix_at",
    "mix_range",
    "pan_needs_filter",
    "pan_to_gains",
]
