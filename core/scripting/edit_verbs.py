"""Montage et pistes : rogner, couper, dupliquer, glisser, déplacer d'une piste à l'autre, repères, pistes (lot 1 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction publique que l'interface appelle
(``ui/main_window_mixins/timeline_editing.py``, ``track_management.py``, ``audio.py``) ; rien n'est écrit directement dans le modèle
sauf là où l'interface le fait aussi (le fader et le panoramique d'une piste audio : ``Track.set_volume_db`` / ``set_pan``).

Règles communes (celles des autres verbes) :

- **verrous** : piste verrouillée (par le verbe, puis par le modèle) et calque verrouillé sont refusés. ``slide_clip`` et ``roll_edit``
  modifient aussi les voisins : un voisin dont le calque est verrouillé les fait refuser ;
- **rien n'est ramené en silence** : là où le modèle borne une valeur (``slip_clip`` hors de la source, ``move_track`` hors de la liste),
  le verbe la refuse, car un script ne reçoit que ce qu'il a écrit ;
- **identifiants créés** : ``cut_clip`` crée le clip de **droite** (celui de gauche garde l'identifiant d'origine), ``duplicate_clip``
  la copie, ``set_cover_marker`` le repère ; ``id`` / ``as`` sont vérifiés par :mod:`core.object_ids` comme pour ``add_clip``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..compositing_guard import ClipEditRefused, clip_edit_refusal
from ..project_model import MAX_GAIN_DB, MAX_PAN, MIN_GAIN_DB, MIN_PAN, Project
from ..timeline_editing import (
    _asset_duration, _neighbors, relocate_clip, remove_marker, ripple_trim_left, roll_edit, set_cover_marker, slide_clip, slip_clip,
)
from ..timeline_operations import cut_clip, duplicate_clip, find_clip, ripple_delete_clip, trim_clip_left, trim_clip_right
from ..track_operations import move_track, remove_track, rename_track
from .layer_rules import require_room
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _TRACK, _editable_clip, _owner_track, _pick, _require_unlocked, _track_id

_MAX_INDEX = 10_000
_TOLERANCE = 1e-9

_AT_START = Param("at", Kind.NUMBER, "Nouvelle position du début du clip sur la timeline, en secondes (jamais avant l'actuelle).",
                  minimum=0.0, maximum=MAX_TIME)
_AT_END = Param("at", Kind.NUMBER, "Nouvelle fin du clip sur la timeline, en secondes (après son début ; pas au-delà de la source).",
                minimum=0.0, maximum=MAX_TIME)
_MARKER = Param("marker", Kind.SELECTOR, "Le repère, par son identifiant.", of="marker")
_NAME = Param("name", Kind.STRING, "Nom de la piste (libre : un nom déjà pris est refusé).")


# --- Clips -------------------------------------------------------------------------------------------------------------


def _trim_clip_start(a: Mapping[str, Any]) -> Operation:
    return lambda p: trim_clip_left(p, _editable_clip(p, a["clip"]), float(a["at"]))


def _trim_clip_end(a: Mapping[str, Any]) -> Operation:
    return lambda p: trim_clip_right(p, _editable_clip(p, a["clip"]), float(a["at"]))


def _require_editable_followers(project: Project, clip_id: str, *, tracks: Iterable[Any]) -> None:
    """Un ripple décale les clips qui commencent à la fin du clip visé ou après : aucun ne doit être un calque verrouillé.

    Le modèle ne regarde que le verrou de piste ; un script ne déplace jamais un calque verrouillé, même sur une piste libre."""
    clip = find_clip(project, clip_id)
    boundary = float(clip.timeline_start + clip.duration)
    for track in tracks:
        if track.locked:
            continue                                  # une piste verrouillée ne bouge pas, même en ripple
        for other in track.clips:
            if other.id != clip_id and other.timeline_start >= boundary - _TOLERANCE:
                code = clip_edit_refusal(project, other.id, visual_only=False)
                if code is not None:
                    raise ClipEditRefused(code, other.id)


def _ripple_trim_clip_start(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        clip = find_clip(project, clip_id)
        if float(a["at"]) - clip.timeline_start > _TOLERANCE:          # sinon rien n'est retiré, rien ne recule
            _require_editable_followers(project, clip_id, tracks=[_owner_track(project, clip_id)])
        return ripple_trim_left(project, clip_id, float(a["at"]))

    return operation


def _room_for_copy(project: Project, clip_id: str) -> None:
    """Couper ou dupliquer un calque graphique en crée un de plus : le plafond de calques du projet vaut aussi ici."""
    if _owner_track(project, clip_id).type == "graphics":
        require_room(project, 1)


def _cut_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        _room_for_copy(project, clip_id)
        _left, right = cut_clip(project, clip_id, float(a["at"]), right_clip_id=a.get("id"))
        return right                                  # l'objet créé : celui que le script peut citer ensuite

    return operation


def _duplicate_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        at = a.get("at")
        clip_id = _editable_clip(project, a["clip"])
        _room_for_copy(project, clip_id)
        return duplicate_clip(project, clip_id, None if at is None else float(at), new_clip_id=a.get("id"))

    return operation


def _ripple_delete_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        _require_editable_followers(project, clip_id, tracks=project.tracks)
        return ripple_delete_clip(project, clip_id)

    return operation


def _slip_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        delta = float(a["delta"])
        _track, _previous, clip, _following = _neighbors(project, clip_id)
        limit = _asset_duration(project, clip)
        # Le modèle ramènerait un décalage trop grand à la limite de la source : un script le reçoit refusé.
        if delta > limit - clip.source_out + _TOLERANCE:
            raise ValueError(f"Le décalage {delta:g} s sort de la source : au plus {max(0.0, limit - clip.source_out):g} s vers l'avant.")
        if delta < -clip.source_in - _TOLERANCE:
            raise ValueError(f"Le décalage {delta:g} s sort de la source : au plus {max(0.0, clip.source_in):g} s vers l'arrière.")
        return slip_clip(project, clip_id, delta)

    return operation


def _require_editable_neighbours(project: Project, clip_id: str, *, previous: bool, following: bool) -> None:
    """Un voisin dont le calque est verrouillé ne se laisse pas rogner par un slide ou un roll (sa piste est celle du clip, déjà vérifiée)."""
    _track, before, _clip, after = _neighbors(project, clip_id)
    for wanted, neighbour in ((previous, before), (following, after)):
        if wanted and neighbour is not None:
            code = clip_edit_refusal(project, neighbour.id, visual_only=False)
            if code is not None:
                raise ClipEditRefused(code, neighbour.id)


def _slide_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        _require_editable_neighbours(project, clip_id, previous=True, following=True)
        return slide_clip(project, clip_id, float(a["at"]))

    return operation


def _roll_edit(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        edge = str(a["edge"])
        _require_editable_neighbours(project, clip_id, previous=edge == "left", following=edge == "right")
        return roll_edit(project, clip_id, edge, float(a["at"]))

    return operation


def _move_clip_to_track(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        return relocate_clip(project, clip_id, _track_id(project, a["track"]), float(a["at"]))

    return operation


# --- Repères -----------------------------------------------------------------------------------------------------------


def _set_cover_marker(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_cover_marker(p, float(a["at"]), a.get("name", ""), marker_id=a.get("id"))


def _remove_marker(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        marker = _pick(project.markers, str(a["marker"]), "Repère", names=False)
        if not remove_marker(project, marker.id):
            raise KeyError(f"Repère introuvable : {marker.id!r}.")
        return None

    return operation


# --- Pistes ------------------------------------------------------------------------------------------------------------


def _remove_track(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_track(p, _track_id(p, a["track"]))


def _rename_track(a: Mapping[str, Any]) -> Operation:
    return lambda p: rename_track(p, _track_id(p, a["track"]), str(a["name"]))


def _move_track(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track_id = _track_id(project, a["track"])
        index = int(a["index"])
        last = len(project.tracks) - 1
        if not 0 <= index <= last:                    # le modèle ramènerait l'index aux bords de la liste
            raise ValueError(f"L'index {index} est hors de la liste des pistes (de 0 à {last}).")
        return move_track(project, track_id, index)

    return operation


def _audio_track(project: Project, selector: str, verb: str) -> Any:
    """Le fader et le panoramique n'existent que sur une piste audio déverrouillée (la règle du mélangeur de l'interface)."""
    track = _pick(project.tracks, selector, "Piste")
    if track.type != "audio":
        raise ValueError(f"{verb} : la piste '{track.id}' est une piste '{track.type}' ; seule une piste audio a un fader.")
    _require_unlocked(track)
    return track


def _set_track_volume(a: Mapping[str, Any]) -> Operation:
    return lambda p: _audio_track(p, a["track"], "set_track_volume").set_volume_db(float(a["volume_db"]))


def _set_track_pan(a: Mapping[str, Any]) -> Operation:
    return lambda p: _audio_track(p, a["track"], "set_track_pan").set_pan(float(a["pan"]))


CLIP_EDIT_VERBS: tuple[Verb, ...] = (
    Verb("trim_clip_start", "Rogne le début d'un clip : le début avance, la fin ne bouge pas (la source consommée suit la vitesse).",
         (_CLIP, _AT_START), _trim_clip_start, group="edit"),
    Verb("trim_clip_end", "Rogne ou étend la fin d'un clip ; le début ne bouge pas, la source ne peut pas être dépassée.",
         (_CLIP, _AT_END), _trim_clip_end, group="edit"),
    Verb("ripple_trim_clip_start", "Rogne le début d'un clip et referme le trou : les clips de la piste qui le suivaient reculent d'autant.",
         (_CLIP, _AT_START), _ripple_trim_clip_start, group="edit"),
    Verb("cut_clip", "Coupe un clip en deux à un instant strictement à l'intérieur ; la moitié gauche garde l'identifiant, "
                     "la moitié droite est le clip créé (`id` / `as`, sinon « <clip>-split-N »).",
         (_CLIP, Param("at", Kind.NUMBER, "Instant de la coupe sur la timeline, en secondes (strictement dans le clip).",
                       minimum=0.0, maximum=MAX_TIME)),
         _cut_clip, creates="clip", group="edit"),
    Verb("duplicate_clip", "Duplique un clip sur sa piste (copie complète : effets, étalonnage, images clés) ; le clip créé est la copie.",
         (_CLIP, Param("at", Kind.NUMBER, "Début de la copie, en secondes (défaut : juste après la fin du clip).", required=False,
                       minimum=0.0, maximum=MAX_TIME)),
         _duplicate_clip, creates="clip", group="edit"),
    Verb("ripple_delete_clip", "Supprime un clip et fait reculer d'autant tous les clips qui le suivent, sur les pistes non verrouillées.",
         (_CLIP,), _ripple_delete_clip, group="edit"),
    Verb("slip_clip", "Décale la fenêtre de source d'un clip sans le bouger sur la timeline ; un décalage hors de la source est refusé.",
         (_CLIP, Param("delta", Kind.NUMBER, "Décalage en secondes de source (positif : plus tard dans le média).",
                       minimum=-MAX_TIME, maximum=MAX_TIME)),
         _slip_clip, group="edit"),
    Verb("slide_clip", "Glisse un clip à une nouvelle position en ajustant la fin du clip d'avant et le début du clip d'après ; "
                       "sans voisin, c'est un simple déplacement.",
         (_CLIP, Param("at", Kind.NUMBER, "Nouvelle position du clip, en secondes.", minimum=0.0, maximum=MAX_TIME)),
         _slide_clip, group="edit"),
    Verb("roll_edit", "Déplace la coupe entre un clip et son voisin sans changer la durée totale ; sans voisin de ce côté, c'est un trim.",
         (_CLIP, Param("edge", Kind.ENUM, "Quel bord du clip : « right » (coupe sortante) ou « left » (coupe entrante).",
                       choices=("left", "right")),
          Param("at", Kind.NUMBER, "Nouvelle position de la coupe, en secondes.", minimum=0.0, maximum=MAX_TIME)),
         _roll_edit, group="edit"),
    Verb("move_clip_to_track", "Déplace un clip dans le temps et vers une autre piste (de même genre, déverrouillée).",
         (_CLIP, _TRACK, Param("at", Kind.NUMBER, "Nouvelle position du clip, en secondes.", minimum=0.0, maximum=MAX_TIME)),
         _move_clip_to_track, group="edit"),
)

TRACK_VERBS: tuple[Verb, ...] = (
    Verb("remove_track", "Supprime une piste vide (une piste qui porte des clips est refusée).", (_TRACK,), _remove_track, group="edit"),
    Verb("rename_track", "Renomme une piste ; son identifiant ne change pas.", (_TRACK, _NAME), _rename_track, group="edit"),
    Verb("move_track", "Place une piste à un index de la liste (0 : tout en haut) ; une piste verrouillée ne bouge pas.",
         (_TRACK, Param("index", Kind.INTEGER, "Index cible, de 0 au nombre de pistes moins un.", minimum=0, maximum=_MAX_INDEX)),
         _move_track, group="edit"),
    Verb("set_track_volume", "Règle le fader d'une piste audio déverrouillée.",
         (_TRACK, Param("volume_db", Kind.NUMBER, "Volume en dB.", minimum=MIN_GAIN_DB, maximum=MAX_GAIN_DB)),
         _set_track_volume, group="audio"),
    Verb("set_track_pan", "Règle le panoramique d'une piste audio déverrouillée.",
         (_TRACK, Param("pan", Kind.NUMBER, "Panoramique, de −1 (gauche) à 1 (droite).", minimum=MIN_PAN, maximum=MAX_PAN)),
         _set_track_pan, group="audio"),
)

MARKER_VERBS: tuple[Verb, ...] = (
    Verb("set_cover_marker", "Pose le repère de couverture (il n'y en a qu'un : l'ancien est remplacé) ; le repère créé est le nouveau.",
         (Param("at", Kind.NUMBER, "Position, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("name", Kind.STRING, "Nom du repère.", required=False)),
         _set_cover_marker, creates="marker", group="edit"),
    Verb("remove_marker", "Retire un repère (par son identifiant).", (_MARKER,), _remove_marker, group="edit"),
)

__all__ = ["CLIP_EDIT_VERBS", "MARKER_VERBS", "TRACK_VERBS"]
