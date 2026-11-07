"""Compositions nodales : ``create_composition``, ``convert_to_composition`` (lot 10 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction que la page Composition de l'interface appelle
(``ui/main_window_mixins/composition_page.py`` : ``new_composition``, ``convert_selection_to_composition``) dans
:mod:`core.composition_ops`. Ce que le modèle refuse (clip imbriqué ou déjà composé, vitesse, suivi, fenêtres de couleur, cadrage
animé, effets animés ``fx.*`` — :func:`core.effects_model.has_live_effect_animation` —, étalonnage animé ``color.*`` —
:func:`core.grade_targets.has_live_grade_animation` —, calque d'effets, groupe ou parentage, piste
verrouillée, plage prise sur la piste hôte, durée trop longue) l'est pour un script, sans rien modifier.

Ce que le script ajoute, parce qu'un script est une donnée et ne se contente pas d'un message d'état :

- **calque verrouillé** : le modèle ne regarde que le verrou de piste ; un calque verrouillé (clip dont ``graphic.locked``) sur une piste libre
  est refusé, comme par l'inspecteur ;
- **rien n'est perdu en silence** : la conversion retire les transitions des clips choisis et ne garde ni le panoramique, ni les fondus, ni
  les effets audio des clips vidéo (``ConversionResult.dropped``, que l'interface annonce dans la barre d'état). Un script ne reçoit pas
  d'annonce : il est **refusé** tant que ces réglages existent (retirer la transition, remettre panoramique et fondus à zéro avant) ;
- **bornes** : la liste des clips est bornée (200, sans doublon) ; la durée d'une composition créée est refusée plutôt que ramenée
  (au moins 0,04 s, au plus :data:`core.composition.MAX_DURATION`) ; la création compte dans le plafond de clips du projet, toutes séquences
  confondues ;
- **identifiant** : ``id`` / ``as`` imposent l'identifiant du clip créé (:mod:`core.object_ids`), vérifié avant toute modification. Les fonctions du
  modèle prennent pour cela un paramètre optionnel ``clip_id`` (``None`` : identifiant généré, comme l'interface).

Pas de cycle possible : une composition ne contient que des sources média et des calques graphiques ; un clip imbriqué ou déjà composé est
refusé à la conversion, donc une composition ne contient jamais de séquence ni de composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..composition import MAX_DURATION
from ..composition_ops import convert_to_composition, create_composition
from ..project_model import Project
from ..timeline_operations import find_clip
from .auto_rules import MAX_LISTED_CLIPS, editable_clips, require_room
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _TRACK, _pick, _require_unlocked

_MIN_DURATION = 0.04
_CLIPS = Param("clips", Kind.LIST, f"Les clips vidéo et calques graphiques à composer, par leur identifiant (1 à {MAX_LISTED_CLIPS}, sans doublon).",
               minimum=1, maximum=MAX_LISTED_CLIPS, item=Param("clip", Kind.SELECTOR, "Un clip.", of="clip"))
_NAME = Param("name", Kind.STRING, "Nom du clip de composition (défaut : « Composition »).", required=False)


def _create_composition(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = _pick(project.tracks, a["track"], "Piste")
        if track.type != "video":
            raise ValueError(f"La piste '{track.id}' est une piste '{track.type}' : une composition va sur une piste vidéo.")
        _require_unlocked(track)
        require_room(project, 1)
        return create_composition(project, track.id, float(a["at"]), float(a["duration"]), a.get("name"), clip_id=a.get("id"))

    return operation


def _require_nothing_dropped(project: Project, ids: list[str]) -> None:
    """La conversion retire les transitions des clips choisis et abandonne panoramique, fondus et effets audio des clips vidéo."""
    chosen = set(ids)
    touched = sorted(item.id for item in project.active_sequence.transitions if item.from_clip_id in chosen or item.to_clip_id in chosen)
    if touched:
        raise ValueError(f"La conversion retirerait les transitions {', '.join(touched)} : retirez-les d'abord (remove_transition).")
    for clip_id in ids:
        clip = find_clip(project, clip_id)
        track = next(track for track in project.tracks if any(item.id == clip_id for item in track.clips))
        if track.type == "video" and (clip.pan or clip.fade_in or clip.fade_out or clip.audio_effects):
            raise ValueError(
                f"La conversion ne garde ni le panoramique, ni les fondus, ni les effets audio du clip '{clip_id}' : remettez-les à zéro d'abord."
            )


def _require_state_kept(project: Project, ids: list[str]) -> None:
    """La composition n'a ni le drapeau « désactivé » des clips, ni la visibilité, le muet ou le solo de leurs pistes, ni la visibilité d'un calque :
    ce qui était masqué ou muet réapparaîtrait. Le modèle ne le conserve pas ; un script reçoit la conversion refusée."""
    solo: dict[str, set[str]] = {}
    for track in project.tracks:
        if track.solo:
            solo.setdefault(track.type, set()).add(track.id)
    for clip_id in ids:
        clip = find_clip(project, clip_id)
        track = next(item for item in project.tracks if any(other.id == clip_id for other in item.clips))
        what = None
        if not clip.enabled:
            what = "est désactivé"
        elif not track.visible:
            what = f"est sur la piste masquée '{track.id}'"
        elif track.type == "video" and track.muted:
            what = f"est sur la piste muette '{track.id}'"
        elif getattr(clip.graphic, "visible", True) is False:
            what = "est un calque masqué"
        elif track.type in solo and track.id not in solo[track.type]:
            what = f"est masqué par le solo d'une autre piste ({track.type})"
        if what:
            raise ValueError(f"Le clip '{clip_id}' {what} : la composition ne garde pas cet état, réactivez-le ou retirez-le de la liste.")


def _convert_to_composition(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        ids = editable_clips(project, a["clips"], track_types=("video", "graphics"))
        _require_nothing_dropped(project, ids)
        _require_state_kept(project, ids)
        return convert_to_composition(project, ids, a.get("name"), clip_id=a.get("id")).clip

    return operation


COMPOSITION_VERBS: tuple[Verb, ...] = (
    Verb("create_composition", "Crée un clip de composition vide (transparent) sur une piste vidéo déverrouillée, de `at` à `at + duration` ; "
                               "la plage doit être libre. Le clip créé peut recevoir `id` / `as`.",
         (_TRACK, Param("at", Kind.NUMBER, "Début de la composition, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("duration", Kind.NUMBER, f"Durée en secondes (de {_MIN_DURATION:g} s à {MAX_DURATION:g} s).", minimum=_MIN_DURATION, maximum=MAX_DURATION),
          _NAME),
         _create_composition, creates="clip", group="composition"),
    Verb("convert_to_composition", "Remplace des clips vidéo et calques graphiques par UN clip de composition (à sens unique : chaque calque devient "
                                   "une branche de nœuds), posé sur la plus basse de leurs pistes vidéo. Refusé si un clip est animé (effets `fx.*`), "
                                   "imbriqué, ralenti, suivi, a des fenêtres de couleur, porte une transition ou des réglages audio que la composition "
                                   "ne garde pas. Le clip créé peut recevoir `id` / `as`.",
         (_CLIPS, _NAME),
         _convert_to_composition, creates="clip", group="composition"),
)

__all__ = ["COMPOSITION_VERBS"]
