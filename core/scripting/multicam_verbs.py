"""Multicam : créer une source depuis des clips, aplatir un segment, supprimer un angle, couleur d'un angle (lot 9 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction de :mod:`core.multicam_ops` que l'interface appelle
(``ui/main_window_mixins/multicam_creation.py``, ``multicam.py``, ``multicam_settings.py``). Les verbes Multicam des lots précédents
sont dans ``verbs.py`` ; les règles de désignation (angle par ``angle-N`` ou nom exact, nom d'angle libre) sont les leurs.

Règles communes :

- **verrous et tiers** : le modèle ne regarde que le verrou de la piste du clip. Ces verbes refusent aussi un calque verrouillé, et ils
  modifient des tiers : ``remove_multicam_angle`` redirige des segments (de n'importe quelle séquence) et supprime une piste, ``flatten``
  remplace un segment et rebranche ses transitions ; chacun vérifie les verrous de tout ce qu'il touche AVANT de rien changer ;
- **rien ne reste pendu** : ``create_multicam_from_clips`` et ``flatten_multicam_segment`` retirent des clips sans nettoyer transitions ni
  liaisons de suivi (``delete_clip``, lui, le fait) : un script qui en aurait laissé reçoit un refus qui dit quoi retirer d'abord ;
  ``remove_multicam_angle`` refuse de supprimer la piste d'un angle que des transitions, des liaisons de suivi ou un ducking citent ;
- **synchronisation** : ``create_multicam_from_clips`` ne mesure rien ; sans ``offset`` les positions actuelles servent (« positions »),
  avec ``offset`` les décalages écrits par le script (« à la main », le plus petit vaut 0 comme pour ``create_multicam_source``) ;
- **bornes** : au plus 64 angles, plafonds de séquences et de clips du projet vérifiés avant de créer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..multicam import angle_track, is_multicam_clip
from ..multicam_model import MAX_ANGLES, SyncMethod
from ..multicam_ops import MulticamError, angle_usages, create_multicam_from_clips, flatten_multicam_clip, remove_angle, set_angle_color
from ..object_ids import clip_ids, resolve_object_id
from ..project_model import Clip, Project, Sequence
from .auto_rules import MAX_CLIPS_IN_PROJECT, total_clips
from .sequence_rules import require_sequence_room
from .verbs import (
    MAX_TIME, Kind, Operation, Param, Verb, _ANGLE, _SOURCE, _angle_id, _angles, _editable_clip, _multicam_source,
    _require_angle_name, _require_free_sequence_name, _require_unlocked,
)

ANGLE_PALETTE_SIZE = 12
"""Couleurs de la palette d'angles de l'interface (``ui.theme.Palette.angle_colors``) : l'interface range le rang modulo cette taille (un test garde l'égalité)."""


def _follows(sequence: Sequence, clip_ids_: set[str]) -> list[str]:
    """Les clips de la séquence dont une liaison de suivi (active ou non) cite l'un de ces clips, ou qui en citent un d'autre séquence : la liste des clips à retirer d'abord."""
    found = []
    for track in sequence.tracks:
        for clip in track.clips:
            links = getattr(getattr(clip, "tracking", None), "links", ()) or ()
            if any(link.follows(source) for link in links for source in clip_ids_):
                found.append(clip.id)
    return found


def _tracking_refusal(sequence: Sequence, clip: Clip) -> str | None:
    if clip.tracking is not None and not clip.tracking.is_empty:
        return f"Le clip '{clip.id}' porte des données de suivi (trackers, liaisons ou stabilisation) : elles ne suivraient pas la copie."
    followers = _follows(sequence, {clip.id})
    if followers:
        return f"Le clip '{followers[0]}' suit le clip '{clip.id}' (suivi) : retirez cette liaison d'abord."
    return None


# --- create_multicam_from_clips ----------------------------------------------------------------------------------------


def _create_multicam_from_clips(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        listed = a["clips"]
        sequence = project.active_sequence
        ids = [_editable_clip(project, item["clip"], track_types=("video", "audio")) for item in listed]
        if len(set(ids)) != len(ids):
            raise ValueError("Un clip est cité plus d'une fois dans la liste.")
        offsets = [item.get("offset") for item in listed]
        if any(value is not None for value in offsets):
            if any(value is None for value in offsets):
                raise ValueError("Donnez un `offset` à chaque clip, ou à aucun (les positions actuelles servent alors).")
            if min(float(value) for value in offsets) != 0.0:
                raise ValueError(
                    f"Le plus petit décalage d'angle doit valoir 0 (les décalages sont relatifs ; reçu {min(float(v) for v in offsets):g} s) : retranchez-le de chaque angle."
                )
        source_name = a.get("name", "Multicam")
        _require_free_sequence_name(project, source_name)
        require_sequence_room(project, clips=1)                       # les copies d'angle remplacent les clips retirés : net, le segment
        by_id = {clip.id: (track, clip) for track in sequence.tracks for clip in track.clips}
        involved = set(ids)
        for transition in sequence.transitions:
            if transition.from_clip_id in involved or transition.to_clip_id in involved:
                raise ValueError(f"La transition '{transition.id}' touche un clip choisi : retirez-la d'abord (remove_transition), le modèle la laisserait pendre.")
        for clip_id in ids:
            refusal = _tracking_refusal(sequence, by_id[clip_id][1])
            if refusal:
                raise ValueError(refusal)
        # Les angles sont numérotés dans l'ordre du modèle (vidéo d'abord, puis par position) : le nom de chacun suit les mêmes règles que pour create_multicam_source.
        ordered = sorted(range(len(ids)), key=lambda i: (by_id[ids[i]][0].type != "video", by_id[ids[i]][1].timeline_start))
        taken: list[str] = []
        for rank, position in enumerate(ordered):
            clip = by_id[ids[position]][1]
            effective = ((listed[position].get("name") or clip.label) or "").strip() or "Angle"
            _require_angle_name(effective, taken, f"angle-{rank + 1}")
            taken.append(effective)
        segment_id = a.get("segment_id")
        if segment_id is not None:
            resolve_object_id(segment_id, prefix="clip", kind="clip", taken=clip_ids(project))
        explicit = all(value is not None for value in offsets)
        new_sequence, _segment = create_multicam_from_clips(
            project, ids, name=source_name, sync_method=SyncMethod.MANUAL if explicit else SyncMethod.POSITIONS,
            offsets={clip_id: float(offsets[i]) for i, clip_id in enumerate(ids)} if explicit else None,
            names={clip_id: listed[i]["name"] for i, clip_id in enumerate(ids) if listed[i].get("name") is not None} or None,
            sequence_id=a.get("id"), segment_id=segment_id,
        )
        return new_sequence

    return operation


# --- flatten_multicam_segment ------------------------------------------------------------------------------------------


def _flatten_multicam_segment(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        sequence = project.active_sequence
        segment = next(clip for track in sequence.tracks for clip in track.clips if clip.id == clip_id)
        if not is_multicam_clip(project, segment):
            raise MulticamError("Ce clip n'est pas un segment Multicam.")
        refusal = _tracking_refusal(sequence, segment)
        if refusal:
            raise ValueError(refusal)
        for transition in sequence.transitions:                       # le modèle rebranche la transition du segment sur un clip créé : l'autre clip ne doit pas être gelé
            if clip_id in (transition.from_clip_id, transition.to_clip_id):
                other = transition.to_clip_id if transition.from_clip_id == clip_id else transition.from_clip_id
                if any(item.id == other and track.locked for track in sequence.tracks for item in track.clips):
                    raise ValueError(f"La transition '{transition.id}' relie le segment à un clip d'une piste verrouillée.")
        source = _source_of(project, segment)
        angle_clips = sum(len(track.clips) for track in source.tracks)        # borne haute des clips créés (un clip d'angle donne au plus une partie)
        if total_clips(project) - 1 + angle_clips > MAX_CLIPS_IN_PROJECT:
            raise ValueError(f"Le projet compte déjà {total_clips(project)} clips : aplatir en créerait jusqu'à {angle_clips}, la limite est de {MAX_CLIPS_IN_PROJECT}.")
        flatten_multicam_clip(project, clip_id)
        return None

    return operation


def _source_of(project: Project, segment: Clip) -> Sequence:
    source = project.get_sequence(segment.sequence_id)
    if source is None:
        raise MulticamError("Ce segment n'a plus de source.")
    return source


# --- remove_multicam_angle ---------------------------------------------------------------------------------------------


def _remove_multicam_angle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        angle_id = _angle_id(source, a["angle"])
        angle = next(item for item in _angles(source) if item.id == angle_id)
        usages = angle_usages(project, source.id, angle_id)
        replacement = ""
        if a.get("replace_with") is not None:
            replacement = _angle_id(source, a["replace_with"])
            if replacement == angle_id:
                raise ValueError("L'angle de remplacement doit être un autre angle.")
            if not usages:
                raise ValueError("Aucun segment ne montre cet angle : `replace_with` n'a rien à remplacer (le modèle l'ignorerait).")
        track = angle_track(source, angle)
        if track is not None:
            _require_unlocked(track)                                  # la piste de l'angle disparaît avec ses clips
            ids = {clip.id for clip in track.clips}
            for transition in source.transitions:
                if transition.from_clip_id in ids or transition.to_clip_id in ids:
                    raise ValueError(f"La transition '{transition.id}' touche un clip de l'angle : retirez-la d'abord, le modèle la laisserait pendre.")
            if _follows(source, ids):
                raise ValueError(f"Le clip '{_follows(source, ids)[0]}' suit un clip de l'angle (suivi) : retirez cette liaison d'abord.")
            for sidechain in source.ducking_sidechains:
                if track.id in (getattr(sidechain, "music_track_id", None), getattr(sidechain, "voice_track_id", None)):
                    raise ValueError(f"Le ducking de la séquence '{source.id}' cite la piste de l'angle : retirez-le d'abord.")
        redirected = {clip.id for clip in usages}
        for sequence in project.sequences:                            # les segments redirigés sont des tiers, de n'importe quelle séquence
            for owner in sequence.tracks:
                if owner.locked and any(clip.id in redirected for clip in owner.clips):
                    raise ValueError(f"La piste '{owner.id}' de « {sequence.name} » est verrouillée : un segment de cet angle y est redirigé.")
        remove_angle(project, source.id, angle_id, replace_with=replacement)
        return None

    return operation


# --- set_multicam_angle_color ------------------------------------------------------------------------------------------


def _set_multicam_angle_color(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        return set_angle_color(project, source.id, _angle_id(source, a["angle"]), int(a["color_index"]))

    return operation


MULTICAM_VERBS: tuple[Verb, ...] = (
    Verb("create_multicam_from_clips",
         "Transforme des clips de la séquence active en source Multicam : chaque clip (copie, effets et rognage gardés) devient un angle (angle-1, angle-2… vidéos d'abord, par position), "
         "les clips d'origine sont remplacés par un seul segment sur l'angle 1 ; la source est l'objet visé par `id` / `as`, le segment reçoit `segment_id`. "
         "Sans `offset`, les positions actuelles synchronisent ; avec, tous les clips en donnent un et le plus petit vaut 0. Refusé : une transition ou un suivi qui touche un clip choisi.",
         (Param("clips", Kind.LIST, "Les clips à transformer, dans l'ordre (de 2 à 64) : le clip, son nom d'angle et son décalage.", minimum=2, maximum=MAX_ANGLES,
                fields=(
                    Param("clip", Kind.SELECTOR, "Le clip, par son identifiant.", of="clip"),
                    Param("name", Kind.STRING, "Nom de l'angle (unique dans la source) ; absent, le nom du clip.", required=False),
                    Param("offset", Kind.NUMBER, "Début de l'angle dans la source, en secondes ; le plus petit vaut 0.", required=False, minimum=0.0, maximum=MAX_TIME))),
          Param("name", Kind.STRING, "Nom de la source (libre : un nom déjà pris est refusé) ; absent, « Multicam ».", required=False),
          Param("segment_id", Kind.ID, "Identifiant imposé du segment créé ; absent, un identifiant généré.", required=False)),
         _create_multicam_from_clips, creates="sequence", group="multicam"),
    Verb("flatten_multicam_segment",
         "Remplace un segment Multicam de la séquence active par les clips ordinaires qu'il montre (vidéo et son selon la politique audio) ; ils reçoivent des identifiants générés. "
         "Refusé : un segment avec remappage, étalonnage, transformation, composition, gain, fondus ou effets audio propres, ou du suivi.",
         (Param("clip", Kind.SELECTOR, "Le segment, par son identifiant.", of="clip"),), _flatten_multicam_segment, group="multicam"),
    Verb("remove_multicam_angle",
         "Supprime un angle (sa piste et ses clips) d'une source Multicam ; les segments qui le montrent, dans n'importe quelle séquence, passent à `replace_with` (obligatoire alors). "
         "Refusé : le dernier angle, une piste verrouillée, une transition, un suivi ou un ducking qui cite l'angle.",
         (_SOURCE, _ANGLE, Param("replace_with", Kind.SELECTOR, "L'angle qui remplace celui-ci dans les segments qui le montrent.", required=False, of="angle")),
         _remove_multicam_angle, group="multicam"),
    Verb("set_multicam_angle_color", "Choisit la couleur d'un angle dans la palette de l'interface (rang de 0 à 11).",
         (_SOURCE, _ANGLE, Param("color_index", Kind.INTEGER, "Rang de palette, de 0 à 11.", minimum=0, maximum=ANGLE_PALETTE_SIZE - 1)),
         _set_multicam_angle_color, group="multicam"),
)

__all__ = ["ANGLE_PALETTE_SIZE", "MULTICAM_VERBS"]
