"""Montage sur plusieurs clips : ``delete_clips``, ``move_clips`` (lot 10 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction que la sélection de la timeline appelle
(``ui/main_window_mixins/timeline_editing.py`` : ``delete_selected_clip_with_check``, ``on_clips_move_requested``) dans
:mod:`core.timeline_editing` — ``delete_clips`` et ``move_clips``.

Règles de ce module (celles des lots 1 et 4 : un verrou vaut pour tous les chemins, rien n'est ramené ni sauté en silence) :

- **liste bornée, sans doublon** : de 1 à 200 clips (un script ne sert pas à vider un projet d'un coup), un clip cité deux fois est refusé ;
- **tout est vérifié avant la première modification** : chaque clip cité (piste et calque déverrouillés), et en ``ripple`` chaque clip que le
  décalage ferait reculer (les tiers sur les pistes libres, calques verrouillés compris). Le modèle, lui, saute en silence un clip introuvable
  ou une suppression ripple refusée : un script reçoit tout ou rien ;
- **les tiers touchés par une suppression** (calques dont un clip supprimé était le parent ou le groupe, liaisons de suivi qui le prenaient pour source,
  y compris sur une autre séquence) ne doivent pas être verrouillés : :func:`~core.scripting.selection_rules.require_untouched_clips` refuse
  sinon ;
- **calques graphiques** : ``delete_clips`` ne supprime pas un calque graphique (un groupe emporte son contenu, un parent détache ses enfants
  avec leurs règles propres) : c'est ``delete_layers``. ``move_clips`` les déplace comme ``move_clip_to_track`` ;
- **déplacement** : chaque destination est une piste déverrouillée acceptant le clip (le modèle le vérifie pour tous avant d'écrire) ; une
  position négative est refusée ; la même cible n'est pas citée deux fois.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..project_model import Project
from ..timeline_editing import ClipPlacement, delete_clips, move_clips
from ..timeline_operations import find_clip
from .auto_rules import MAX_LISTED_CLIPS, editable_clips
from .edit_verbs import _require_editable_followers
from .lock_rules import require_followers_unlocked
from .selection_rules import frozen_clips, require_dependants_editable, require_untouched_clips
from .verbs import Kind, Operation, Param, Verb, _AT, _CLIP, _TRACK, _owner_track, _pick, _require_unlocked

_CLIPS = Param("clips", Kind.LIST, f"Les clips, par leur identifiant (1 à {MAX_LISTED_CLIPS}, sans doublon).", minimum=1,
               maximum=MAX_LISTED_CLIPS, item=Param("clip", Kind.SELECTOR, "Un clip.", of="clip"))
_PLACEMENTS = Param("placements", Kind.LIST, f"Les déplacements, de 1 à {MAX_LISTED_CLIPS} (un par clip) : clip, piste de destination, nouvelle position.",
                    minimum=1, maximum=MAX_LISTED_CLIPS, fields=(_CLIP, _TRACK, _AT))


def _delete_clips(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        ids = editable_clips(project, a["clips"])
        for clip_id in ids:
            if _owner_track(project, clip_id).type == "graphics":
                raise ValueError(f"Le clip '{clip_id}' est un calque graphique : utilisez delete_layers.")
        ripple = bool(a.get("ripple", False))
        if ripple:
            for clip_id in ids:
                _require_editable_followers(project, clip_id, tracks=project.tracks)
        require_dependants_editable(project, ids)
        frozen = frozen_clips(project)
        delete_clips(project, ids, ripple=ripple)
        require_untouched_clips(project, frozen)
        return None

    return operation


def _move_clips(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        wanted = a["placements"]
        ids = editable_clips(project, [item["clip"] for item in wanted])
        placements = []
        for clip_id, item in zip(ids, wanted, strict=True):
            destination = _pick(project.tracks, item["track"], "Piste")
            _require_unlocked(destination)
            if float(item["at"]) != find_clip(project, clip_id).timeline_start:
                require_followers_unlocked(project, clip_id, "déplacer", active_only=True)
            placements.append(ClipPlacement(clip_id, float(item["at"]), destination.id))
        move_clips(project, placements)
        return None

    return operation


SELECTION_VERBS: tuple[Verb, ...] = (
    Verb("delete_clips", "Supprime plusieurs clips (de 1 à 200, sans doublon, hors calques graphiques : `delete_layers`) ; avec `ripple`, les clips "
                         "suivants des pistes libres reculent. Tout est vérifié avant la première suppression : un clip ou un clip décalé dont la "
                         "piste ou le calque est verrouillé fait tout refuser.",
         (_CLIPS, Param("ripple", Kind.BOOLEAN, "Refermer les trous (défaut : non).", required=False)),
         _delete_clips, group="edit"),
    Verb("move_clips", "Déplace plusieurs clips d'un coup, chacun vers une piste (de même genre, déverrouillée) et une position ; tout est vérifié "
                       "avant le premier déplacement.",
         (_PLACEMENTS,),
         _move_clips, group="edit"),
)

__all__ = ["SELECTION_VERBS"]
