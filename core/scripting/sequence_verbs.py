"""Séquence à partir d'une sélection de clips, versions de format (lot 9 de l'extension 0.8.0).

Aucune sémantique propre : ``nest_clips`` appelle ``core.sequences.create_sequence_from_selection`` (« Créer une séquence à partir de la
sélection », ``ui/main_window_mixins/sequences.py::nest_selected_clips``) et ``create_format_version`` appelle
``core.format_versions.create_format_version`` (« Exporter en plusieurs formats… », ``social.py::export_format_versions``). La sélection de
l'interface est ici une liste de clips de la séquence **active**.

Règles communes (celles des autres verbes qui modifient des tiers ou créent des séquences) :

- **plafonds** : séquences et clips du projet (toutes séquences), vérifiés avant de créer (:mod:`core.scripting.sequence_rules`) ;
- **rien n'est corrigé en silence** : un nom de séquence pris est refusé, y compris le nom par défaut (le modèle numéroterait « Nom 2 ») ;
  une transition qui relierait un clip choisi à un clip resté dehors est refusée (le modèle la retirerait) ; la profondeur
  d'imbrication se calcule sur le graphe résultant ;
- **verrous** : les clips choisis sont éditables (piste et calque déverrouillés) ; un clip resté dehors dont le modèle toucherait
  les liaisons de suivi (``_prune_crossing_sources``) ne doit pas être verrouillé : l'opération est refusée et le projet restauré ;
- **version de format** : une version déjà là n'est jamais refaite en silence (l'interface la refait sur demande ; un script supprime
  l'ancienne, ``delete_sequence``, puis en crée une neuve) ; une version se décline de la séquence d'**origine**, jamais d'une autre version.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ..compositing_guard import clip_edit_refusal
from ..format_versions import create_format_version, find_format_version
from ..project_model import Project
from ..sequences import MAX_NESTING_DEPTH, create_sequence_from_selection, find_sequence, height_above, nesting_depth
from ..social_formats import SOCIAL_FORMATS, social_format
from .auto_rules import MAX_LISTED_CLIPS, editable_clips
from .sequence_rules import clips_in, require_sequence_room
from .verbs import Kind, Operation, Param, Verb, _SEQUENCE, _require_free_sequence_name, _sequence_id

_DEFAULT_NESTED_NAME = "Séquence imbriquée"
"""Le nom que le modèle donne sans `name` (``create_sequence_from_selection``) : il est soumis aux mêmes règles qu'un nom écrit."""


def _frozen_outsiders(project: Project, selected: set[str]) -> dict[str, Any]:
    """Les clips restés dehors dont la piste ou le calque est verrouillé, photographiés (`id -> copie`)."""
    frozen: dict[str, Any] = {}
    for track in project.active_sequence.tracks:
        for clip in track.clips:
            if clip.id not in selected and (track.locked or clip_edit_refusal(project, clip.id, visual_only=False) is not None):
                frozen[clip.id] = deepcopy(clip)
    return frozen


def _nest_clips(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        parent = project.active_sequence
        if parent.multicam is not None:
            raise ValueError("La séquence active est une source Multicam : en déplacer les clips casserait ses angles.")
        ids = editable_clips(project, a["clips"])
        selected = set(ids)
        name = a.get("name", _DEFAULT_NESTED_NAME)
        _require_free_sequence_name(project, name)
        require_sequence_room(project)
        for transition in parent.transitions:                     # le modèle retirerait la transition qui relie un clip choisi à un clip resté dehors
            if (transition.from_clip_id in selected) != (transition.to_clip_id in selected):
                raise ValueError(
                    f"La transition '{transition.id}' relie un clip choisi à un clip resté dehors : retirez-la (remove_transition) ou choisissez les deux clips."
                )
        # Profondeur sur le graphe résultant : les ancêtres de la séquence active, la nouvelle séquence, et le plus profond des clips imbriqués choisis.
        contents = max(
            (1 + nesting_depth(project, clip.sequence_id) for track in parent.tracks for clip in track.clips if clip.id in selected and clip.sequence_id),
            default=0,
        )
        if height_above(project, parent.id) + 1 + contents > MAX_NESTING_DEPTH:
            raise ValueError(f"Imbrication trop profonde (plus de {MAX_NESTING_DEPTH} niveaux).")
        frozen = _frozen_outsiders(project, selected)
        result = create_sequence_from_selection(project, ids, name, sequence_id=a.get("id"), nested_clip_id=a.get("clip_id"))
        for track in project.active_sequence.tracks:
            for clip in track.clips:
                if clip.id in frozen and clip != frozen[clip.id]:
                    raise ValueError(f"Le clip '{clip.id}', verrouillé, aurait été modifié (liaisons de suivi) : déverrouillez-le ou choisissez-le aussi.")
        return result.sequence

    return operation


def _create_format_version(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = find_sequence(project, _sequence_id(project, a["sequence"]))
        if source.format_source:
            raise ValueError(f"La séquence '{source.id}' est elle-même une version de format : déclinez la séquence d'origine ('{source.format_source}').")
        target = social_format(a["format"])
        if find_format_version(project, source.id, target.id) is not None:
            raise ValueError(
                f"La séquence '{source.id}' a déjà une version {target.ratio} : l'interface la refait sur demande, un script la supprime "
                "(delete_sequence) avant d'en créer une neuve."
            )
        _require_free_sequence_name(project, a.get("name") or f"{source.name} · {target.ratio}")
        require_sequence_room(project, clips=clips_in(source))
        version, _changes = create_format_version(project, source.id, target.id, version_id=a.get("id"), name=a.get("name"))
        return version

    return operation


SEQUENCE_VERBS: tuple[Verb, ...] = (
    Verb("nest_clips",
         "Déplace des clips de la séquence active dans une nouvelle séquence imbriquée, remplacés par un clip imbriqué (« Créer une séquence à partir de la sélection ») ; "
         f"la séquence créée est l'objet visé par `id` / `as`, le clip imbriqué reçoit `clip_id`. Sans `name`, « {_DEFAULT_NESTED_NAME} » (refusé s'il est pris). "
         "Refusé : une sélection qui sépare un calque de son parent ou de son groupe, une transition vers un clip resté dehors, un clip verrouillé.",
         (Param("clips", Kind.LIST, f"Les clips de la séquence active, par leur identifiant (1 à {MAX_LISTED_CLIPS}, sans doublon).", minimum=1,
                maximum=MAX_LISTED_CLIPS, item=Param("clip", Kind.SELECTOR, "Un clip.", of="clip")),
          Param("name", Kind.STRING, "Nom de la séquence (libre : un nom déjà pris est refusé).", required=False),
          Param("clip_id", Kind.ID, "Identifiant imposé du clip imbriqué créé ; absent, un identifiant généré.", required=False)),
         _nest_clips, creates="sequence", group="sequences"),
    Verb("create_format_version",
         "Décline une séquence dans un autre format social (une copie mise en page pour le cadre : plans pleine image remplis, calques à l'échelle, hors des zones de la plateforme) ; "
         "la version est l'objet visé par `id` / `as`. Une version déjà là, ou une séquence qui est elle-même une version, est refusée.",
         (_SEQUENCE, Param("format", Kind.ENUM, "Le format cible.", choices=tuple(entry.id for entry in SOCIAL_FORMATS)),
          Param("name", Kind.STRING, "Nom de la version (libre : un nom déjà pris est refusé) ; absent, « <séquence> · <rapport> ».", required=False)),
         _create_format_version, creates="sequence", group="sequences"),
)

__all__ = ["SEQUENCE_VERBS"]
