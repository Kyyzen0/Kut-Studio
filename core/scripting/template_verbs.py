"""Emplacements de modèle, diaporama de photos, classement animé (lot 5 de l'extension 0.8.0).

Même chemin que les commandes « Social » de l'interface (``ui/main_window_mixins/templates.py``) : chaque verbe appelle la fonction de
:mod:`core.template_slots` / :mod:`core.leaderboard`. Les photos sont des **fichiers lus en lecture seule** : mêmes règles que ``add_image_layer``
(dans le dossier du script, ``--allow-outside`` décidé par l'appelant, en-tête seul lu, extension d'accord avec le contenu, 8 192 pixels de côté au plus) ;
toutes sont ouvertes **avant** la moindre écriture. Là où le modèle passe en silence (photo illisible laissant une carte vide, photos en trop ignorées), un
script reçoit un refus, et la session restaure le projet.

- **emplacements** : un clip de piste vidéo dont ``template_slot`` est renseigné, sur une piste et un calque déverrouillés. ``fill_slot_with_photo`` pose par
  défaut un Ken Burns qui **remplace** l'animation d'échelle et de position de l'emplacement (le zoom d'impact d'un modèle), comme le geste de l'interface ;
  ``ken_burns: false`` la garde ;
- **diaporama** : les plans vont sur la première piste vidéo libre sur toute la plage (jamais par-dessus un clip). Sur la grille rythmique seulement si le script
  donne ``beats_per_photo`` (l'interface prend la grille si elle existe ; un script dit ce qu'il veut) ;
- **classement** : jusqu'à 10 lignes, cinq calques par ligne ; l'identifiant du classement (``id`` / ``as``) sert à le rééditer. Réécrire un classement remplace ses
  calques : refusé si l'un d'eux, ou un calque qui en dépend (parent, groupe), est verrouillé.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..graphics import GraphicOverlay
from ..leaderboard import (
    KIND, MAX_ROWS, Leaderboard, LeaderboardRow, build_leaderboard, leaderboard_from_dict, update_leaderboard,
)
from ..object_ids import resolve_object_id
from ..template_slots import add_photo_slideshow, empty_slots, fill_slot, fill_slot_with_photo, fill_slots_with_photos, slideshow_cuts
from ..timeline_operations import find_clip
from ..compositing_guard import ClipEditRefused, clip_edit_refusal
from .auto_rules import MAX_PHOTOS, require_grid
from .layer_rules import require_layers_room
from .resource_limits import require_media_room
from .image_layer import read_image_size
from .selection_rules import require_dependants_editable
from .verbs import MAX_TIME, Kind, Operation, Param, ScriptContext, Verb, _CLIP, _asset_id, _editable_clip

_NO_CONTEXT = "{verb} lit un fichier : l'appelant doit fournir le dossier du script (contexte de fichiers)."
_KEN_BURNS = Param("ken_burns", Kind.BOOLEAN, "Pose un Ken Burns sur la photo, qui remplace l'animation d'échelle et de position de l'emplacement (défaut oui).",
                   required=False)
_PHOTOS = Param("photos", Kind.LIST, f"Les photos, de 1 à {MAX_PHOTOS} (chemins relatifs au dossier du script).", minimum=1, maximum=MAX_PHOTOS,
                item=Param("photo", Kind.PATH, "Chemin d'une photo (PNG, JPEG, WebP, BMP, TIFF)."))


def _slot(project: Any, selector: str) -> str:
    """Un emplacement de modèle modifiable : clip de piste vidéo, piste et calque déverrouillés, ``template_slot`` renseigné."""
    clip_id = _editable_clip(project, selector, track_types=("video",))
    if not find_clip(project, clip_id).template_slot:
        raise ValueError(f"Le clip '{clip_id}' n'est pas un emplacement de modèle.")
    return clip_id


def _require_unlocked_targets(project: Any, start: str | None) -> None:
    """Les emplacements que ``fill_slots_with_photos`` choisirait seuls (vides, pistes déverrouillées, après `start`) ne sont pas des calques verrouillés.

    Le modèle ne filtre que les pistes verrouillées : il remplirait un calque verrouillé. Un script reçoit un refus, jamais un écrasement."""
    key = None
    if start is not None:
        first = find_clip(project, start)
        key = (first.timeline_start, first.track_id)
    for clip in empty_slots(project):
        track = next(item for item in project.tracks if item.id == clip.track_id)
        if track.locked or (key is not None and (clip.timeline_start, clip.track_id) <= key):
            continue
        code = clip_edit_refusal(project, clip.id, visual_only=False)
        if code is not None:
            raise ClipEditRefused(code, clip.id)


def _photos(context: ScriptContext, paths: list[str]) -> list[tuple[Any, tuple[int, int]]]:
    """Toutes les photos résolues et lues (en-tête) AVANT d'écrire quoi que ce soit (le décodeur borne déjà leur nombre à ``MAX_PHOTOS``)."""
    resolved = [context.resolve_media(text) for text in paths]
    return [(path, read_image_size(path)) for path in resolved]


def _fill_slot(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        clip_id = _slot(project, a["clip"])
        return fill_slot(project, clip_id, _asset_id(project, a["asset"]))

    return operation


def _new_photo_media(project: Any, photos: Any) -> int:
    """Médias que poser ces photos ajoute : un par photo DISTINCTE qui n'est pas déjà une image du projet (le modèle réutilise le média de même chemin)."""
    known = {asset.path for asset in project.media_assets if asset.media_type == "image"}
    return len({str(Path(str(path)).expanduser().resolve()) for path, _size in photos} - known)


def _fill_slot_with_photo(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Any) -> Any:
        if context is None:
            raise ValueError(_NO_CONTEXT.format(verb="fill_slot_with_photo"))
        clip_id = _slot(project, a["clip"])
        photos = _photos(context, [a["path"]])
        ((path, size),) = photos
        require_media_room(project, _new_photo_media(project, photos))
        return fill_slot_with_photo(project, clip_id, path, size, ken_burns=bool(a.get("ken_burns", True)))

    return operation


def _fill_slots_with_photos(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Any) -> Any:
        if context is None:
            raise ValueError(_NO_CONTEXT.format(verb="fill_slots_with_photos"))
        start = None if a.get("start") is None else _slot(project, a["start"])
        _require_unlocked_targets(project, start)
        photos = _photos(context, a["photos"])
        require_media_room(project, _new_photo_media(project, photos))
        filled = fill_slots_with_photos(project, photos, start_clip_id=start, ken_burns=bool(a.get("ken_burns", True)))
        if len(filled) != len(photos):                    # photos en trop : le modèle les ignore, un script reçoit un refus (la session restaure)
            raise ValueError(f"{len(photos) - len(filled)} photo(s) sur {len(photos)} n'ont pas d'emplacement libre (déverrouillé, vide, après l'emplacement de départ).")
        return filled

    return operation


def _add_photo_slideshow(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Any) -> Any:
        if context is None:
            raise ValueError(_NO_CONTEXT.format(verb="add_photo_slideshow"))
        beats = None if a.get("beats_per_photo") is None else int(a["beats_per_photo"])
        grid = None if beats is None else require_grid(project)
        start, seconds = float(a["start"]), float(a.get("seconds", 3.0))
        photos = _photos(context, a["photos"])
        cuts = slideshow_cuts(len(photos), start=start, grid=grid, seconds=seconds, beats_per_photo=beats)
        if cuts[-1][1] > MAX_TIME:
            raise ValueError(f"Le diaporama finirait à {cuts[-1][1]:g} s, au-delà de la limite de {MAX_TIME:g} s.")
        require_layers_room(project, len(photos), media=_new_photo_media(project, photos))
        return add_photo_slideshow(project, photos, start=start, grid=grid, seconds=seconds, beats_per_photo=beats,
                                   ken_burns=bool(a.get("ken_burns", True)))

    return operation


# --- Classement ------------------------------------------------------------------------------------------------------------


def _rows(raw: list[Mapping[str, Any]]) -> tuple[LeaderboardRow, ...]:
    return tuple(LeaderboardRow(str(row["rank"]), str(row["name"]), str(row["value"]), str(row.get("color", "#22B8FF"))) for row in raw)


def _taken_board_ids(project: Any) -> set[str]:
    return {key for sequence in project.sequences for key in sequence.generated_groups}


def _add_leaderboard(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        board_id = resolve_object_id(a.get("id"), prefix="lb", kind="classement", taken=_taken_board_ids(project))
        board = Leaderboard(_rows(a["rows"]), float(a["start"]), float(a["duration"]), float(a.get("top", -0.105)), float(a.get("delay", 0.3)), id=board_id)
        layers = 5 * len(board.rows)
        require_layers_room(project, layers)
        build_leaderboard(project, board)
        return board

    return operation


def _board_of(project: Any, selector: str) -> tuple[dict[str, Any], Leaderboard]:
    group = project.active_sequence.generated_groups.get(selector)
    if group is None or group.get("kind") != KIND:
        raise KeyError(f"Classement introuvable : {selector!r}.")
    return group, leaderboard_from_dict(selector, group.get("data", {}))


def _require_replaceable(project: Any, members: list[str]) -> int:
    """Les calques d'un classement (et ceux qui en dépendent) sont déverrouillés ; rend combien existent encore."""
    present = 0
    for clip_id in members:
        code = clip_edit_refusal(project, clip_id, visual_only=False)
        if code == "missing":
            continue                                      # supprimé à la main : le modèle l'ignore aussi
        if code is not None:
            raise ClipEditRefused(code, clip_id)
        present += 1
    wanted = set(members)
    for sequence in project.sequences:
        for track in sequence.tracks:
            for clip in track.clips:
                graphic = clip.graphic
                if clip.id in wanted or not isinstance(graphic, GraphicOverlay) or not ({graphic.parent_id, graphic.group_id} & wanted):
                    continue
                if track.locked or graphic.locked:        # la suppression d'un parent le rend autonome : un calque verrouillé ne change pas
                    raise ValueError(f"Le calque '{clip.id}' est verrouillé et dépend d'un calque du classement : il serait modifié.")
    return present


def _update_leaderboard(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        group, old = _board_of(project, str(a["leaderboard"]))
        rows = old.rows if a.get("rows") is None else _rows(a["rows"])
        duration = old.duration if a.get("duration") is None else float(a["duration"])
        board = Leaderboard(rows, old.start, duration, old.top, old.delay, id=old.id)
        members = [str(item) for item in group.get("clips", ())]
        present = _require_replaceable(project, members)
        require_dependants_editable(project, members)    # une liaison de suivi verrouillée qui suit un calque remplacé serait réécrite
        layers = 5 * len(board.rows)
        require_layers_room(project, max(0, layers - present))
        update_leaderboard(project, board)
        return board

    return operation


_ROW_FIELDS = (
    Param("rank", Kind.STRING, "Le rang affiché (« 1 »).", max_length=8),
    Param("name", Kind.STRING, "Le nom.", max_length=40),
    Param("value", Kind.STRING, "La valeur affichée.", max_length=24),
    Param("color", Kind.COLOR, "Couleur de la ligne (défaut #22B8FF).", required=False),
)
_ROWS = Param("rows", Kind.LIST, f"Les lignes, de 1 à {MAX_ROWS} (rang, nom, valeur, couleur).", minimum=1, maximum=MAX_ROWS, fields=_ROW_FIELDS)

TEMPLATE_VERBS: tuple[Verb, ...] = (
    Verb("fill_slot", "Met un média du projet (vidéo ou photo) dans un emplacement de modèle : même place, même durée si le média est assez long, mêmes effets ; "
                      "un média plus court raccourcit le plan. Refusé hors emplacement, sur une piste ou un calque verrouillé.",
         (_CLIP, Param("asset", Kind.SELECTOR, "Le média du projet : identifiant ou nom exact (unique).", of="asset")), _fill_slot, group="social"),
    Verb("fill_slot_with_photo", "Met une photo (dans le dossier du script) dans un emplacement de modèle : même place et même durée, remplit le cadre, Ken Burns par défaut.",
         (_CLIP, Param("path", Kind.PATH, "Chemin de la photo, relatif au dossier du script."), _KEN_BURNS),
         _fill_slot_with_photo, group="social", needs_context=True),
    Verb("fill_slots_with_photos", "Une photo par emplacement, dans l'ordre de la timeline : à partir de `start` (rempli ou non, puis les emplacements vides qui suivent), "
                                   "sinon dans les emplacements vides déverrouillés. Plus de photos que d'emplacements est refusé.",
         (_PHOTOS, Param("start", Kind.SELECTOR, "L'emplacement de départ (défaut : les emplacements vides).", required=False, of="clip"), _KEN_BURNS),
         _fill_slots_with_photos, group="social", needs_context=True),
    Verb("add_photo_slideshow", "Diaporama : un emplacement par photo, bord à bord à partir de `start`, rempli par sa photo avec un Ken Burns (le sens alterne), sur la "
                                "première piste vidéo libre sur toute la plage. Chaque photo dure `seconds`, ou `beats_per_photo` temps de la grille rythmique (refusé sans grille). "
                                "Les clips ont des identifiants générés.",
         (_PHOTOS, Param("start", Kind.NUMBER, "Début du diaporama, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("seconds", Kind.NUMBER, "Durée de chaque photo sans grille, de 0,1 s (défaut 3).", required=False, minimum=0.1, maximum=3600.0),
          Param("beats_per_photo", Kind.INTEGER, "Temps de la grille par photo (le premier plan part du premier temps à `start` ou après).", required=False,
                minimum=1, maximum=64),
          _KEN_BURNS),
         _add_photo_slideshow, group="social", needs_context=True),
    Verb("add_leaderboard", "Pose un classement animé (lignes qui glissent l'une après l'autre : cinq calques par ligne) ; le classement créé a l'identifiant `id` / `as` "
                            "et sert à le rééditer. Refusé si la durée est trop courte pour que toutes les lignes entrent.",
         (_ROWS, Param("start", Kind.NUMBER, "Début, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("duration", Kind.NUMBER, "Durée, en secondes.", minimum=0.1, maximum=MAX_TIME),
          Param("top", Kind.NUMBER, "Centre de la première ligne, fraction de la hauteur du cadre (défaut −0,105 ; 0 : milieu).", required=False,
                minimum=-1.0, maximum=1.0),
          Param("delay", Kind.NUMBER, "Délai avant la première ligne, en secondes (défaut 0,3).", required=False, minimum=0.0, maximum=3600.0)),
         _add_leaderboard, creates="leaderboard", group="social"),
    Verb("update_leaderboard", "Réédite un classement de la séquence active : ses calques sont remplacés (nouveaux identifiants) par ceux du nouveau tableau, au même "
                               "endroit et au même moment. Refusé si l'un d'eux, ou un calque qui en dépend, est verrouillé.",
         (Param("leaderboard", Kind.SELECTOR, "Le classement, par identifiant.", of="leaderboard"),
          Param("rows", Kind.LIST, f"Les nouvelles lignes, de 1 à {MAX_ROWS} (défaut : les mêmes).", required=False, minimum=1, maximum=MAX_ROWS, fields=_ROW_FIELDS),
          Param("duration", Kind.NUMBER, "Nouvelle durée, en secondes (défaut : la même).", required=False, minimum=0.1, maximum=MAX_TIME)),
         _update_leaderboard, group="social"),
)

__all__ = ["TEMPLATE_VERBS"]
