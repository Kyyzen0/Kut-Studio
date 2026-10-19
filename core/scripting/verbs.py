"""Le catalogue des verbes du format déclaratif (ADR-0005, §3) : **une table unique** qui sert à valider un script, à le documenter et à
produire son schéma JSON. Un verbe sans entrée ici n'existe pas ; rien n'est exécuté qui ne soit une fonction de ``core/`` listée.

Un verbe est un nom, des paramètres typés et bornés, et une fonction ``build`` qui rend l'**opération** (``projet -> valeur``) que la
session appliquera. Les paramètres sont déjà validés quand ``build`` est appelée (types, bornes, identifiants sûrs, alias résolus) ;
les *sélecteurs* (« la piste A1 », « le bus voix ») sont résolus **à l'application**, sur le projet tel qu'il est à ce moment, puisqu'un
verbe précédent a pu créer la cible.

Sans Qt, sans accès disque : un script ne lit ni n'écrit rien d'autre que le projet qu'on lui donne.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from ..audio_buses import (
    add_bus, add_bus_insert, remove_bus, remove_bus_insert, rename_bus, route_track, set_bus_insert_enabled,
    set_bus_muted, set_bus_volume, update_bus_insert_parameters,
)
from ..animation import InterpolationType, ValueKind
from ..animation_targets import all_targets, get_target
from ..audio_effects_model import (
    AudioEffectType, add_track_insert, remove_track_insert, set_track_insert_enabled, update_track_insert_parameters,
)
from ..color_grading import (
    CHANNELS, CONTRAST_MAX, CONTRAST_MIN, EXPOSURE_MAX, EXPOSURE_MIN, HIGHLIGHTS_MAX, HIGHLIGHTS_MIN, HUE_MAX, HUE_MIN,
    MAX_CURVE_POINTS, MAX_CURVE_VALUE, MIN_CURVE_VALUE, SATURATION_MAX, SATURATION_MIN, SHADOWS_MAX, SHADOWS_MIN,
    TEMPERATURE_MAX, TEMPERATURE_MIN, WHEEL_MAX, WHEEL_MIN, WHEELS, ColorCurve, ColorGradingError, ColorGradingService,
    LUTResource, Wheel,
)
from ..color_matrix import MATRIX_CHOICES
from ..color_nodes import MAX_BLUR, MAX_LABEL_LENGTH, MAX_SHARPEN, MixerKind, window_by_id
from ..color_qualifier import Qualifier, QualifierError
from ..clip_crop import MAX_CROP_SIDE, Crop, CropError, clear_crop, crop_mask, crop_of, set_crop
from ..color_windows import forget_windows, window_ids
from ..compositing import Mask, MaskMode, MaskShape, _MASK_BOUNDS
from ..mask_path import HANDLE_LIMIT, MAX_NODES, MIN_NODES, POINT_LIMIT
from ..mask_path_handles import nodes_refusal
from ..lut_importer import LUTImportError, ffmpeg_readable_lut, parse_cube_lut
from ..node_graph import NodeGraphError
from ..compositing_guard import REFUSAL_LAYER_LOCKED, clip_edit_refusal
from ..mograph_targets import graphic_property_id
from ..graphics import GraphicType, ShapeKind, add_graphic_clip, update_graphic
from ..mograph_layers import add_layer, find_layer
from .layer_rules import graphic_of, require_room, require_shallow
from .image_layer import fitted_size, read_image_size
from ..keyframe_editing import add_keyframe, clip_duration, remove_keyframe_at
from ..media_color_ops import set_media_color_matrix
from ..media_probe import MediaProbeError, probe_media
from ..multicam import is_multicam_clip
from ..multicam_model import MAX_ANGLES, AudioMode, SyncMethod
from ..multicam_ops import (
    AngleSpec, MulticamError, add_angle, create_multicam_source, insert_multicam_clip, multicam_segment_at, rename_angle,
    replace_angle, set_angle_offset, set_audio_policy, switch_angle,
)
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import MAX_GAIN_DB, MIN_GAIN_DB, Project, Sequence
from ..sequences import (
    MAX_SEQUENCE_SIDE, check_sequence_format, create_sequence, delete_sequence, duplicate_sequence, find_sequence,
    insert_sequence_clip, rename_sequence, set_sequence_format,
)
from ..timeline_editing import add_marker
from .path_nodes import ANCHOR_FIELDS, NODE_FIELDS, nodes_to_arrays
from ..timeline_operations import (
    add_clip_to_track, add_subtitle_clip, delete_clip, find_clip, move_clip, set_clip_enabled,
)
from ..track_operations import add_track, set_track_locked, set_track_muted, set_track_visible
from ..transitions import MIN_TRANSITION_SECONDS, TransitionType, add_transition, remove_transition

Operation = Callable[[Project], Any]


@dataclass(frozen=True)
class ScriptContext:
    """Ce que le script a le droit de toucher hors du projet (ADR-0005, décision 5) : **lecture seule**, dans son dossier.

    Les chemins de médias se résolvent relativement à ``base_dir`` (le dossier du script). Sortir de ce dossier (chemin absolu ailleurs,
    ``..``, lien symbolique qui s'en échappe) demande ``allow_outside`` : c'est à l'appelant de le vouloir (``--allow-outside``), jamais
    au script. Sans contexte, un verbe qui lit un fichier est refusé."""

    base_dir: Path
    allow_outside: bool = False

    def resolve_media(self, text: str) -> Path:
        """Le fichier désigné par ``text``, ou ``ValueError`` / ``OSError`` : introuvable, pas un fichier, hors du dossier permis."""
        base = self.base_dir.resolve()
        candidate = (base / text).resolve(strict=False)           # un chemin absolu l'emporte sur la base, comme pour ``Path``
        if not self.allow_outside:
            try:
                candidate.relative_to(base)
            except ValueError:
                raise ValueError(
                    f"Le média {text!r} est hors du dossier du script ({base}) : ajoutez --allow-outside pour l'autoriser."
                ) from None
        if not candidate.is_file():
            raise FileNotFoundError(f"Fichier média introuvable : {candidate}")
        return candidate

MAX_TEXT = 200
"""Longueur maximale d'un texte (nom, sélecteur) : un script ne sert pas à transporter de la donnée."""
MAX_PARAMS = 32
MAX_TIME = 1_000_000.0
"""Secondes : environ onze jours, bien au-delà de tout montage, et assez bas pour que les calculs de durée restent exacts."""


class Kind(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    COLOR = "color"          # « #RRGGBB » ou « #RRGGBBAA »
    PATH = "path"            # un chemin de fichier, résolu par le contexte (dossier du script)
    BOOLEAN = "boolean"
    ID = "id"                # un identifiant NEUF (lettres, chiffres, « _ »)
    SELECTOR = "selector"    # un objet existant : son identifiant ou son nom exact (unique), ou {"ref": alias}
    ENUM = "enum"
    OBJECT = "object"        # un dictionnaire plat de nombres, booléens et textes courts (paramètres d'un effet)
    LIST = "list"            # une liste bornée (``minimum``/``maximum`` = nombre d'éléments) : d'objets à champs fixes (``fields``) ou de valeurs (``item``)


@dataclass(frozen=True)
class Param:
    """Un paramètre de verbe. ``of`` : pour un sélecteur, le genre d'objet visé (``track``, ``bus``, ``clip``, ``asset``, ``insert``) ;
    pour un identifiant neuf, le genre créé."""

    name: str
    kind: Kind
    doc: str
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    of: str = ""
    max_length: int = 0            # 0 : MAX_TEXT
    allow_empty: bool = False      # un texte vide est une valeur (« sans nom ») et non une erreur
    multiline: bool = False        # texte à plusieurs lignes (un titre) : « \n » permis
    fields: tuple[Param, ...] = ()  # pour une liste d'objets : les champs de chaque élément (mêmes règles qu'un paramètre de verbe)
    item: Param | None = None       # pour une liste de valeurs : le paramètre qui décrit chaque élément


@dataclass(frozen=True)
class Verb:
    """``creates`` : le genre d'objet créé (``bus``, ``clip``, ``insert``, ``marker``, ``color_node``), ``None`` pour un verbe
    qui modifie seulement ; un verbe créateur accepte alors ``id`` (identifiant imposé) et ``as`` (alias : un nom du script, qui
    sert d'identifiant s'il n'y a pas d'``id``).

    ``creates_scope`` : nom d'un paramètre (déjà décodé) qui borne l'unicité de ``id`` — les nœuds d'étalonnage sont locaux à
    un clip, donc ``creates_scope="clip"`` : deux clips peuvent chacun avoir un ``n2``.
    """

    name: str
    doc: str
    params: tuple[Param, ...]
    build: Callable[..., Operation]
    creates: str | None = None
    creates_scope: str | None = None
    group: str = ""
    needs_context: bool = False     # le verbe lit un fichier : ``build(args, context)``
    accepts: tuple[str, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        names = [param.name for param in self.params]
        if self.creates:
            names += ["id", "as"]
        object.__setattr__(self, "accepts", tuple(names))


# --- Sélecteurs --------------------------------------------------------------------------------------------------------


def _pick(items: Iterable[Any], selector: str, kind: str, *, names: bool = True) -> Any:
    """L'objet dont l'identifiant est ``selector``, sinon celui dont le nom est exactement ``selector`` **s'il est unique** : une
    ambiguïté est une erreur, jamais un choix silencieux."""
    pool = list(items)
    exact = [item for item in pool if getattr(item, "id", None) == selector]
    if exact:
        return exact[0]
    if names:
        named = [item for item in pool if getattr(item, "name", None) == selector]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise ValueError(f"{kind} ambigu {selector!r} : {len(named)} correspondances, désignez-le par son identifiant.")
    raise KeyError(f"{kind} introuvable : {selector!r}.")


def _track_id(project: Project, selector: str) -> str:
    return str(_pick(project.tracks, selector, "Piste").id)


def _bus_id(project: Project, selector: str) -> str:
    return str(_pick(project.buses, selector, "Bus").id)


def _asset_id(project: Project, selector: str) -> str:
    return str(_pick(project.media_assets, selector, "Média").id)


def _sequence_id(project: Project, selector: str) -> str:
    return str(_pick(project.sequences, selector, "Séquence").id)


def _clip_id(project: Project, selector: str) -> str:
    return str(_pick((clip for track in project.tracks for clip in track.clips), selector, "Clip", names=False).id)


def _insert_id(project: Project, bus: str, selector: str) -> str:
    return str(_pick(next(item for item in project.buses if item.id == bus).inserts, selector, "Insert", names=False).id)


def _owner_track(project: Project, clip_id: str) -> Any:
    return next(track for track in project.tracks if any(clip.id == clip_id for clip in track.clips))


def _require_unlocked(track: Any) -> None:
    """Le verrou d'une piste vaut aussi pour un script : il protège ce que l'utilisateur a voulu figer."""
    if track.locked:
        raise ValueError(f"La piste '{track.id}' est verrouillée.")


def _editable_clip(project: Project, selector: str, *, track_types: tuple[str, ...] = ()) -> str:
    """L'identifiant du clip désigné, après avoir vérifié que sa piste est déverrouillée (et, si demandé, d'un des genres permis)."""
    clip_id = _clip_id(project, selector)
    owner = _owner_track(project, clip_id)
    _require_unlocked(owner)
    if clip_edit_refusal(project, clip_id, visual_only=False) == REFUSAL_LAYER_LOCKED:     # la même règle que l'inspecteur
        raise ValueError(f"Le calque '{clip_id}' est verrouillé.")
    if track_types and owner.type not in track_types:
        raise ValueError(f"Le clip '{clip_id}' est sur une piste '{owner.type}' : cette opération vise {' ou '.join(track_types)}.")
    return clip_id


# --- Les verbes --------------------------------------------------------------------------------------------------------


def _add_bus(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_bus(p, a.get("name"), bus_id=a.get("id"))


def _remove_bus(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_bus(p, _bus_id(p, a["bus"]))


def _rename_bus(a: Mapping[str, Any]) -> Operation:
    return lambda p: rename_bus(p, _bus_id(p, a["bus"]), a["name"])


def _set_bus_volume(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_bus_volume(p, _bus_id(p, a["bus"]), float(a["volume_db"]))


def _set_bus_muted(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_bus_muted(p, _bus_id(p, a["bus"]), bool(a["muted"]))


def _route_track(a: Mapping[str, Any]) -> Operation:
    return lambda p: route_track(p, _track_id(p, a["track"]), _bus_id(p, a["bus"]) if a.get("bus") is not None else "")


def _add_bus_insert(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_bus_insert(
        p, _bus_id(p, a["bus"]), a["effect"], effect_id=a.get("id"), params=a.get("params"), enabled=bool(a.get("enabled", True)),
    )


def _remove_bus_insert(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_bus_insert(p, (bus := _bus_id(p, a["bus"])), _insert_id(p, bus, a["insert"]))


def _set_bus_insert_enabled(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_bus_insert_enabled(p, (bus := _bus_id(p, a["bus"])), _insert_id(p, bus, a["insert"]), bool(a["enabled"]))


def _update_bus_insert(a: Mapping[str, Any]) -> Operation:
    return lambda p: update_bus_insert_parameters(p, (bus := _bus_id(p, a["bus"])), _insert_id(p, bus, a["insert"]), a["params"])


def _add_clip(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_clip_to_track(
        p, _asset_id(p, a["asset"]), _track_id(p, a["track"]), float(a["at"]), clip_id=a.get("id"),
    )


def _delete_clip(a: Mapping[str, Any]) -> Operation:
    return lambda p: delete_clip(p, _clip_id(p, a["clip"]))


def _move_clip(a: Mapping[str, Any]) -> Operation:
    return lambda p: move_clip(p, _clip_id(p, a["clip"]), float(a["at"]))


def _set_clip_enabled(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_enabled(p, _clip_id(p, a["clip"]), bool(a["enabled"]))


def _set_media_color_matrix(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if ("asset" in a) == ("clip" in a):
            raise ValueError("set_media_color_matrix demande exactement un des deux : « asset » (le média) ou « clip » (le média de ce clip).")
        if "clip" in a:
            asset_id = next(clip.asset_id for track in project.tracks for clip in track.clips if clip.id == _clip_id(project, a["clip"]))
        else:
            asset_id = _asset_id(project, a["asset"])
        return set_media_color_matrix(project, asset_id, a["matrix"])

    return operation


def _import_media(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Project) -> Any:
        if context is None:
            raise ValueError("import_media lit un fichier : l'appelant doit fournir le dossier du script (contexte de fichiers).")
        path = context.resolve_media(a["path"])
        normalized = os.path.normpath(os.path.abspath(path))
        for existing in project.media_assets:
            if os.path.normpath(os.path.abspath(existing.path)) == normalized:
                raise InvalidObjectId(f"Ce média est déjà dans le projet : {existing.id!r}.")
        try:
            asset = probe_media(str(path))
        except MediaProbeError as error:               # fichier corrompu, format inconnu, ffprobe absent ou trop lent : un refus, pas un plantage
            raise ValueError(str(error)) from error
        asset.id = resolve_object_id(a.get("id"), prefix="asset", kind="média", taken={item.id for item in project.media_assets})
        asset.name = path.name
        project.media_assets.append(asset)
        return asset

    return operation


def _add_track_insert(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track_id = _track_id(project, a["track"])
        kind = next(item.type for item in project.tracks if item.id == track_id)
        if kind not in ("audio", "video"):             # le rendu traite une piste de graphiques avant tout traitement audio : l'insert ne jouerait jamais
            raise ValueError(f"La piste '{track_id}' est une piste '{kind}' : seules les pistes audio et vidéo portent des inserts.")
        return add_track_insert(
            project, track_id, a["effect"], effect_id=a.get("id"), params=a.get("params"), enabled=bool(a.get("enabled", True)),
        )

    return operation


def _track_insert(p: Project, track: str, selector: str) -> tuple[str, str]:
    track_id = _track_id(p, track)
    owner = next(item for item in p.tracks if item.id == track_id)
    return track_id, str(_pick(owner.inserts, selector, "Insert", names=False).id)


def _remove_track_insert(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_track_insert(p, *_track_insert(p, a["track"], a["insert"]))


def _set_track_insert_enabled(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_track_insert_enabled(p, *_track_insert(p, a["track"], a["insert"]), bool(a["enabled"]))


def _update_track_insert(a: Mapping[str, Any]) -> Operation:
    return lambda p: update_track_insert_parameters(p, *_track_insert(p, a["track"], a["insert"]), a["params"])


def _set_track_muted(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_track_muted(p, _track_id(p, a["track"]), bool(a["muted"]))


def _set_track_locked(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_track_locked(p, _track_id(p, a["track"]), bool(a["locked"]))


def _set_track_visible(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_track_visible(p, _track_id(p, a["track"]), bool(a["visible"]))


def _graphics_track(project: Project, selector: Any) -> Any:
    """La piste de graphiques visée (``None`` : la première éditable, voir ``add_graphic_clip``) ; refuse un autre genre ou une piste verrouillée."""
    track = next(item for item in project.tracks if item.id == _track_id(project, selector)) if selector is not None else None
    if track is not None and track.type != "graphics":
        raise ValueError(f"La piste '{track.id}' est une piste '{track.type}' : un calque graphique va sur une piste de graphiques.")
    if track is not None and track.locked:
        raise ValueError(f"La piste '{track.id}' est verrouillée.")
    return track


def _graphic_group(project: Project, selector: str) -> str:
    """Le groupe visé par ``add_graphic`` : un vrai groupe, déverrouillé (comme pour ``move_into_group``, un calque d'effets n'y va pas)."""
    group_id = _clip_id(project, selector)
    track, group = find_layer(project, group_id)
    if graphic_of(group).type != GraphicType.GROUP:
        raise ValueError(f"Le calque '{group_id}' n'est pas un groupe.")
    if track.locked or graphic_of(group).locked:
        raise ValueError(f"Le groupe '{group_id}' est verrouillé : son contenu ne change pas.")
    return group_id


def _add_graphic(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = _graphics_track(project, a.get("track"))
        require_room(project, 1)
        group_id = ""
        if a.get("group") is not None:
            if a["graphic"] == GraphicType.ADJUSTMENT.value:
                raise ValueError("Un calque d'effets s'applique à toute la composition : il ne peut pas être dans un groupe.")
            group_id = _graphic_group(project, a["group"])
        clip = add_layer(
            project, a["graphic"], at=float(a["at"]), duration=float(a["duration"]),
            shape=a.get("shape", ShapeKind.RECTANGLE.value), group_id=group_id, track=track, clip_id=a.get("id"),
        )
        if group_id:
            require_shallow(project, clip.id)
        if a.get("text") is not None:
            update_graphic(clip, "text", a["text"])
        return clip

    return operation


def _add_image_layer(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Project) -> Any:
        if context is None:
            raise ValueError("add_image_layer lit un fichier : l'appelant doit fournir le dossier du script (contexte de fichiers).")
        track = _graphics_track(project, a.get("track"))
        path = context.resolve_media(a["path"])
        width, height = read_image_size(path)                  # tout est vérifié avant la moindre écriture
        require_room(project, 1)
        clip = add_graphic_clip(
            project, "image", timeline_start=float(a["at"]), duration=float(a["duration"]), source_path=str(path),
            track=track, clip_id=a.get("id"),
        )
        fitted = fitted_size(width, height, project.width, project.height)
        update_graphic(clip, "width", fitted[0])
        update_graphic(clip, "height", fitted[1])
        asset = next(item for item in project.media_assets if item.id == clip.asset_id)
        asset.width, asset.height = fitted
        return clip

    return operation


_GRAPHIC_FIELDS = ("text", "fill_color", "stroke_color", "stroke_width", "font_size", "width", "height")


def _set_graphic(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _GRAPHIC_FIELDS if name in a]
        if not given:
            raise ValueError("set_graphic : donnez au moins un champ à modifier.")
        clip = find_clip(project, _editable_clip(project, a["clip"]))
        animated = {str(kf.property_name) for kf in getattr(clip, "animation", ()) or ()}
        for name in given:
            if graphic_property_id(name) in animated:              # une valeur statique serait masquée par la courbe : même règle que set_mask
                raise ValueError(f"{name} du calque '{clip.id}' est animé (images-clés) : un script ne le modifie pas, réglez-le dans l'interface.")
        for name in given:
            update_graphic(clip, name, a[name])
        return clip

    return operation


def _add_track(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = a.get("name")
        if name is not None and any(track.name == name for track in project.tracks):
            raise ValueError(f"Une piste s'appelle déjà {name!r} : le modèle la renommerait, un script ne le permet pas en silence.")
        return add_track(project, a["type"], name, track_id=a.get("id"))

    return operation


def _add_subtitle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if a.get("track") is not None:
            track_id = _track_id(project, a["track"])
        else:
            track_id = next((track.id for track in project.tracks if track.type == "subtitle"), "")
            if not track_id:
                raise ValueError("Le projet n'a pas de piste de sous-titres : ajoutez-en une avec add_track (type « subtitle »).")
        _require_unlocked(next(track for track in project.tracks if track.id == track_id))
        return add_subtitle_clip(project, a["text"], float(a["at"]), float(a["duration"]), track_id, clip_id=a.get("id"))

    return operation


def _transition_clips_editable(project: Project, *clip_ids: str) -> None:
    for clip_id in clip_ids:
        _require_unlocked(_owner_track(project, clip_id))


def _add_transition(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        first, second = _clip_id(project, a["from_clip"]), _clip_id(project, a["to_clip"])
        _transition_clips_editable(project, first, second)       # une transition peut décaler des clips
        return add_transition(project, first, second, TransitionType(a["transition"]), float(a["duration"]), transition_id=a.get("id"))

    return operation


def _remove_transition(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        current = _pick(project.transitions, a["transition"], "Transition", names=False)
        for clip_id in (current.from_clip_id, current.to_clip_id):
            if any(clip.id == clip_id for track in project.tracks for clip in track.clips):
                _transition_clips_editable(project, clip_id)
        return remove_transition(project, str(current.id))

    return operation


# Les propriétés que ``add_keyframe`` accepte : celles du registre d'animation (transform, ``graphic.*``), jamais recopiées ici. Exclues :
# les booléens (``flip_h`` / ``flip_v`` : le verbe pose un nombre), la vitesse (``time.speed`` : la durée du clip en dépend, donc le
# sens de « relatif au clip »), et les familles par masque (``mask.<id>.*`` : verbes ``add_mask_keyframe`` / ``remove_mask_keyframe``).
_ANIMATABLE = tuple(
    target.id for target in all_targets() if target.spec.kind is not ValueKind.BOOL and not target.id.startswith("time.")
)


def _add_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        target = get_target(a["property"])
        owner = _owner_track(project, clip_id)
        # Une propriété n'existe que pour certains genres de piste (``skew`` : graphiques seulement ; les transformations : vidéo et
        # graphiques) : une image clé posée ailleurs serait enregistrée mais ignorée au rendu, un faux succès.
        if not target.applies_to(owner.type) or (
            target.applies_to_clip is not None and not target.applies_to_clip(find_clip(project, clip_id))
        ):
            raise ValueError(f"La propriété {a['property']!r} ne s'applique pas à un clip d'une piste '{owner.type}'.")
        spec = target.spec
        value = float(a["value"])
        low = float("-inf") if spec.minimum is None else spec.minimum
        high = float("inf") if spec.maximum is None else spec.maximum
        if not low <= value <= high:                            # le modèle ramènerait la valeur : un script ne reçoit jamais autre chose que ce qu'il a écrit
            raise ValueError(f"{a['property']} = {value} est hors bornes (de {low} à {high}).")
        at = float(a["at"])
        if at > clip_duration(find_clip(project, clip_id)) + 1e-9:
            raise ValueError(f"L'instant {at} s dépasse la durée du clip : les images clés sont relatives au clip.")
        return add_keyframe(project, clip_id, a["property"], at, value, interpolation=a.get("interpolation"))

    return operation


def _remove_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])
        if not remove_keyframe_at(project, clip_id, a["property"], float(a["at"])):
            raise KeyError(f"Aucune image clé de {a['property']!r} à {a['at']} s sur ce clip.")
        return None

    return operation


_GRADE_FIELDS = ("exposure", "contrast", "saturation", "temperature", "hue", "shadows", "highlights")
_WHEEL_COMPONENTS = ("r", "g", "b", "y")


def _grade_clip(project: Project, a: Mapping[str, Any]) -> tuple[str, str | None]:
    """Clip éditable (vidéo / graphiques, piste déverrouillée) et nœud optionnel (identifiant de correcteur)."""
    clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
    node = a.get("node")
    if node is not None:
        graph = ColorGradingService().get_graph(project, clip_id)
        try:
            graph.corrector(node)
        except (KeyError, NodeGraphError) as error:
            raise KeyError(f"Nœud d'étalonnage introuvable : {node!r}.") from error
    return clip_id, node


def _set_color_grade(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _GRADE_FIELDS if name in a]
        if not given and "enabled" not in a:
            raise ValueError("set_color_grade : donnez au moins un champ à modifier.")
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        for name in given:
            grade = grade.with_field(name, float(a[name]))
        if "enabled" in a:
            grade = grade.with_enabled(bool(a["enabled"]))
        return service.set_grade(project, clip_id, grade, node)

    return operation


def _reset_color_grade(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        return ColorGradingService().reset_grade(project, clip_id, node)

    return operation


def _set_color_curve(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        return service.set_grade(project, clip_id, grade.with_curves(grade.curves.with_point(a["channel"], int(a["index"]), float(a["output"]))), node)

    return operation


def _reset_color_curve(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        channel = a["channel"]
        curves = grade.curves._replace(channel, ColorCurve.identity())
        return service.set_grade(project, clip_id, grade.with_curves(curves), node)

    return operation


def _set_color_wheel(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _WHEEL_COMPONENTS if name in a]
        if not given:
            raise ValueError("set_color_wheel : donnez au moins une composante (r, g, b ou y).")
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        current = getattr(grade, a["wheel"])
        values = {name: float(getattr(current, name)) for name in _WHEEL_COMPONENTS}
        values.update({name: float(a[name]) for name in given})
        return service.set_grade(project, clip_id, grade.with_wheel(a["wheel"], Wheel(**values)), node)

    return operation


def _import_lut(a: Mapping[str, Any], context: ScriptContext | None) -> Operation:
    def operation(project: Project) -> Any:
        if context is None:
            raise ValueError("import_lut lit un fichier : l'appelant doit fournir le dossier du script (contexte de fichiers).")
        path = context.resolve_media(a["path"])
        try:
            parsed = parse_cube_lut(path)
        except LUTImportError as error:
            raise ValueError(str(error)) from error
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        # FFmpeg lit les octets tels quels : BOM / UTF-16 → copie UTF-8 (comme l'UI).
        try:
            lut = LUTResource.from_path(ffmpeg_readable_lut(path), title=parsed.title or path.stem)
        except (OSError, ColorGradingError) as error:
            raise ValueError(str(error)) from error
        return service.set_grade(project, clip_id, grade.with_lut(lut), node)

    return operation


def _remove_lut(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id, node)
        return service.set_grade(project, clip_id, grade.with_lut(None), node)

    return operation


def _add_color_node(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        service = ColorGradingService()
        after = a.get("after")
        if after is not None:
            service.get_graph(project, clip_id).corrector(after)          # KeyError / NodeGraphError → refus clair
        created: dict[str, str] = {}

        def change(graph):
            requested = a.get("id")
            node_id = None
            if requested is not None:
                node_id = resolve_object_id(requested, prefix="n", kind="nœud", taken={item.id for item in graph.nodes})
            graph, new_id = graph.with_node_after(after, node_id=node_id)
            if a.get("label") is not None:
                graph = graph.with_label(new_id, a["label"])
            created["id"] = new_id
            return graph

        try:
            service.edit_nodes(project, clip_id, change)
        except NodeGraphError as error:
            raise ValueError(str(error)) from error
        return service.get_graph(project, clip_id).corrector(created["id"])

    return operation


def _branch_color_node(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        service = ColorGradingService()
        beside = a["node"]
        service.get_graph(project, clip_id).corrector(beside)
        created: dict[str, str] = {}

        def change(graph):
            requested = a.get("id")
            new_id = None
            if requested is not None:
                new_id = resolve_object_id(requested, prefix="n", kind="nœud", taken={item.id for item in graph.nodes})
            graph, nid = graph.with_branch(beside, MixerKind(a["kind"]), new_id=new_id)
            if a.get("label") is not None:
                graph = graph.with_label(nid, a["label"])
            created["id"] = nid
            return graph

        try:
            service.edit_nodes(project, clip_id, change)
        except NodeGraphError as error:
            raise ValueError(str(error)) from error
        return service.get_graph(project, clip_id).corrector(created["id"])

    return operation


def _remove_color_node(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        service = ColorGradingService()
        node = a["node"]
        service.get_graph(project, clip_id).corrector(node)
        clip = find_clip(project, clip_id)
        before = window_ids(getattr(clip, "color_grade", None))

        def change(graph):
            return graph.without(node)

        try:
            service.edit_nodes(project, clip_id, change)
        except NodeGraphError as error:
            raise ValueError(str(error)) from error
        forget_windows(clip, before - window_ids(getattr(clip, "color_grade", None)))
        return None

    return operation


def _rename_color_node(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        service = ColorGradingService()
        node = a["node"]
        service.get_graph(project, clip_id).corrector(node)
        service.edit_nodes(project, clip_id, lambda graph: graph.with_label(node, a["label"]))
        return service.get_graph(project, clip_id).corrector(node)

    return operation


def _set_color_detail(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if "blur" not in a and "sharpen" not in a:
            raise ValueError("set_color_detail : donnez blur et/ou sharpen.")
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        target = service.get_graph(project, clip_id).node_or_first(node).id
        # Le modèle ramène flou / netteté en silence : on a déjà borné au décodage ; on refuse aussi une valeur
        # hors modèle si un jour les Param divergent.
        blur = float(a["blur"]) if "blur" in a else None
        sharpen = float(a["sharpen"]) if "sharpen" in a else None
        if blur is not None and not 0.0 <= blur <= MAX_BLUR:
            raise ValueError(f"blur = {blur} est hors bornes (de 0 à {MAX_BLUR}).")
        if sharpen is not None and not 0.0 <= sharpen <= MAX_SHARPEN:
            raise ValueError(f"sharpen = {sharpen} est hors bornes (de 0 à {MAX_SHARPEN}).")
        service.edit_nodes(project, clip_id, lambda graph: graph.with_detail(target, blur=blur, sharpen=sharpen))
        return service.get_graph(project, clip_id).corrector(target)

    return operation


def _node_field(name: str) -> Param:
    anchor = name in ANCHOR_FIELDS
    limit = POINT_LIMIT if anchor else HANDLE_LIMIT
    doc = f"Ancre {name} (fraction de la boîte du masque, ±{limit:g})." if anchor else \
        f"Tangente {name} relative à l'ancre (±{limit:g}) ; absente : 0 (nœud anguleux si les quatre le sont)."
    return Param(name, Kind.NUMBER, doc, required=anchor, minimum=-limit, maximum=limit)


def _nodes_param(*, required: bool) -> Param:
    return Param("nodes", Kind.LIST, f"Les nœuds de la courbe fermée, de {MIN_NODES} à {MAX_NODES} ; absent : le cercle par défaut."
                 if not required else f"Les nœuds de la courbe fermée, de {MIN_NODES} à {MAX_NODES} (remplace tous les nœuds).",
                 required=required, minimum=MIN_NODES, maximum=MAX_NODES, fields=tuple(_node_field(name) for name in NODE_FIELDS))


_WINDOW_FIELDS = (
    "position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity", "inverted", "mode", "name",
)
_QUALIFIER_BOOLS = ("enabled", "use_hue", "use_sat", "use_lum", "invert")
_QUALIFIER_NUMBERS = (
    "hue_center", "hue_width", "hue_soft", "sat_low", "sat_high", "sat_soft", "lum_low", "lum_high", "lum_soft",
)


def _require_mask_number(name: str, value: float) -> float:
    """Refuse une valeur que ``Mask`` ramènerait en silence."""
    low, high = _MASK_BOUNDS[name]
    if not low <= value <= high:
        raise ValueError(f"{name} = {value} est hors bornes (de {low} à {high}).")
    return value


def _add_color_window(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        graph = service.get_graph(project, clip_id)
        target = graph.node_or_first(node).id
        clip = find_clip(project, clip_id)
        compositing = getattr(clip, "compositing", None)
        taken = window_ids(getattr(clip, "color_grade", None)) | {
            mask.id for mask in (getattr(compositing, "masks", ()) or ())
        }
        window_id = resolve_object_id(a.get("id"), prefix="w", kind="fenêtre", taken=taken, length=8)
        position_x = _require_mask_number("position_x", float(a.get("position_x", 0.5)))
        position_y = _require_mask_number("position_y", float(a.get("position_y", 0.5)))
        width = _require_mask_number("width", float(a.get("width", 0.4)))
        height = _require_mask_number("height", float(a.get("height", 0.4)))
        rotation = _require_mask_number("rotation", float(a.get("rotation", 0.0)))
        feather = _require_mask_number("feather", float(a.get("feather", 0.03)))
        expansion = _require_mask_number("expansion", float(a.get("expansion", 0.0)))
        opacity = _require_mask_number("opacity", float(a.get("opacity", 1.0)))
        shape = MaskShape(a["shape"])
        path_nodes: dict[str, Any] = {}
        if "nodes" in a:
            if shape is not MaskShape.PATH:
                raise ValueError("add_color_window : 'nodes' ne s'applique qu'à la forme 'path'.")
            points, handles = nodes_to_arrays(a["nodes"])
            path_nodes = {"points": points, "handles": handles}      # forme par défaut du modèle (cercle) si 'nodes' est absent
        window = Mask(
            id=window_id, shape=shape, **path_nodes,
            position_x=position_x, position_y=position_y, width=width, height=height,
            rotation=rotation, feather=feather, expansion=expansion, opacity=opacity,
            inverted=bool(a.get("inverted", False)),
            mode=MaskMode(a.get("mode", MaskMode.ADD.value)), name=a.get("name", ""),
        )
        service.edit_nodes(
            project, clip_id,
            lambda g: g.with_windows(target, (*g.corrector(target).windows, window)),
        )
        return service.get_graph(project, clip_id).corrector(target).window(window_id)

    return operation


def _color_window(project: Project, clip_selector: str, window_selector: str):
    """``(clip_id, window_id, owner_node_id)`` pour une fenêtre du clip éditable."""
    clip_id = _editable_clip(project, clip_selector, track_types=("video", "graphics"))
    graph = ColorGradingService().get_graph(project, clip_id)
    if window_by_id(graph, window_selector) is None:
        raise KeyError(f"Fenêtre d'étalonnage introuvable : {window_selector!r}.")
    owner = graph.window_owner(window_selector)
    if owner is None:                                          # pragma: no cover - window_by_id l'a déjà trouvée
        raise KeyError(f"Fenêtre d'étalonnage introuvable : {window_selector!r}.")
    return clip_id, window_selector, owner.id


def _remove_color_window(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, window_id, owner_id = _color_window(project, a["clip"], a["window"])
        clip = find_clip(project, clip_id)
        before = window_ids(getattr(clip, "color_grade", None))
        ColorGradingService().edit_nodes(
            project, clip_id,
            lambda g: g.with_windows(owner_id, tuple(w for w in g.corrector(owner_id).windows if w.id != window_id)),
        )
        forget_windows(clip, before - window_ids(getattr(clip, "color_grade", None)))
        return None

    return operation


def _set_color_window(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _WINDOW_FIELDS if name in a]
        if not given:
            raise ValueError("set_color_window : donnez au moins un champ à modifier.")
        clip_id, window_id, owner_id = _color_window(project, a["clip"], a["window"])
        service = ColorGradingService()
        base = service.get_graph(project, clip_id).corrector(owner_id).window(window_id)
        if base is None:  # pragma: no cover - _color_window l'a déjà trouvée
            raise KeyError(f"Fenêtre d'étalonnage introuvable : {window_id!r}.")
        changes: dict[str, Any] = {}
        for name in given:
            if name in _MASK_BOUNDS:
                changes[name] = _require_mask_number(name, float(a[name]))
            elif name == "inverted":
                changes[name] = bool(a[name])
            elif name == "mode":
                changes[name] = MaskMode(a[name])
            else:
                changes[name] = a[name]
        updated = replace(base, **changes, id=base.id)
        service.edit_nodes(
            project, clip_id,
            lambda g: g.with_windows(
                owner_id, tuple(updated if w.id == window_id else w for w in g.corrector(owner_id).windows),
            ),
        )
        return service.get_graph(project, clip_id).corrector(owner_id).window(window_id)

    return operation


def _set_color_window_points(a: Mapping[str, Any]) -> Operation:
    """Remplace les nœuds d'une fenêtre en courbe de Bézier : mêmes bornes et même écriture que l'éditeur du moniteur."""
    def operation(project: Project) -> Any:
        clip_id, window_id, owner_id = _color_window(project, a["clip"], a["window"])
        service = ColorGradingService()
        base = service.get_graph(project, clip_id).corrector(owner_id).window(window_id)
        if base is None:  # pragma: no cover - _color_window l'a déjà trouvée
            raise KeyError(f"Fenêtre d'étalonnage introuvable : {window_id!r}.")
        if base.shape is not MaskShape.PATH:
            raise ValueError(f"La fenêtre '{window_id}' n'est pas une courbe de Bézier (forme '{base.shape.value}').")
        points, handles = nodes_to_arrays(a["nodes"])
        if nodes_refusal(base, points, handles) is not None:
            raise ValueError("Courbe refusée : nombre de nœuds ou valeurs hors des bornes du modèle.")
        updated = replace(base, points=points, handles=handles, id=base.id)
        if updated.points != points or updated.handles != handles:  # pragma: no cover - le modèle ne doit rien ramener en silence
            raise ValueError("Courbe refusée : le modèle aurait modifié les nœuds.")
        service.edit_nodes(
            project, clip_id,
            lambda g: g.with_windows(
                owner_id, tuple(updated if w.id == window_id else w for w in g.corrector(owner_id).windows),
            ),
        )
        return service.get_graph(project, clip_id).corrector(owner_id).window(window_id)

    return operation


def _set_color_qualifier(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in (*_QUALIFIER_BOOLS, *_QUALIFIER_NUMBERS) if name in a]
        if not given:
            raise ValueError("set_color_qualifier : donnez au moins un champ à modifier.")
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        target = service.get_graph(project, clip_id).node_or_first(node).id
        base = service.get_graph(project, clip_id).corrector(target).qualifier or Qualifier()
        values = base.to_dict()
        for name in given:
            values[name] = bool(a[name]) if name in _QUALIFIER_BOOLS else float(a[name])
        try:
            qualifier = Qualifier(**values)
        except QualifierError as error:
            raise ValueError(str(error)) from error
        service.edit_nodes(project, clip_id, lambda g: g.with_qualifier(target, qualifier))
        return service.get_graph(project, clip_id).corrector(target).qualifier

    return operation


def _clear_color_qualifier(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        service = ColorGradingService()
        target = service.get_graph(project, clip_id).node_or_first(node).id
        service.edit_nodes(project, clip_id, lambda g: g.with_qualifier(target, None))
        return None

    return operation


_CROP_SIDES = ("left", "top", "right", "bottom")


def _set_crop(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = {name: float(a[name]) for name in _CROP_SIDES if name in a}
        if not given:
            raise ValueError("set_crop : donnez au moins un bord à modifier (left, top, right, bottom).")
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        clip = find_clip(project, clip_id)
        current = crop_of(clip)
        if current is None and len(given) < len(_CROP_SIDES) and crop_mask(getattr(clip, "compositing", None)) is not None:
            raise ValueError(f"Le masque « Recadrage » de '{clip_id}' a été modifié à la main : donnez les quatre bords, ou clear_crop d'abord.")
        base = current or Crop()
        try:
            set_crop(project, clip_id, Crop(**{name: given.get(name, getattr(base, name)) for name in _CROP_SIDES}))
        except CropError as error:
            raise ValueError(str(error)) from error
        return None

    return operation


def _clear_crop(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        try:
            clear_crop(project, _editable_clip(project, a["clip"], track_types=("video", "graphics")))
        except CropError as error:
            raise ValueError(str(error)) from error
        return None

    return operation


def _add_marker(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_marker(p, float(a["at"]), a.get("name", ""), a.get("category", "standard"), marker_id=a.get("id"))


def _require_free_sequence_name(project: Project, name: str, *, besides: str = "") -> None:
    """Le modèle rendrait « Nom 2 » à un nom pris (``unique_sequence_name``) : un script ne reçoit jamais autre chose que ce qu'il a écrit."""
    if any(sequence.name == name and sequence.id != besides for sequence in project.sequences):
        raise ValueError(f"Une séquence s'appelle déjà {name!r} : le modèle la renommerait, un script ne le permet pas en silence.")


def _create_sequence(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if a.get("name") is not None:
            _require_free_sequence_name(project, a["name"])
        check_sequence_format(a.get("width"), a.get("height"), a.get("fps"))      # ``create_sequence`` ne vérifie rien : l'interface ne passe jamais de taille
        return create_sequence(
            project, a.get("name"), width=a.get("width"), height=a.get("height"), fps=a.get("fps"), sequence_id=a.get("id"),
        )

    return operation


def _rename_sequence(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        sequence_id = _sequence_id(project, a["sequence"])
        _require_free_sequence_name(project, a["name"], besides=sequence_id)
        return rename_sequence(project, sequence_id, a["name"])

    return operation


def _set_sequence_format(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if not any(name in a for name in ("width", "height", "fps")):
            raise ValueError("set_sequence_format : donnez width, height et/ou fps.")
        sequence = find_sequence(project, _sequence_id(project, a["sequence"]))
        return set_sequence_format(
            project, sequence.id, int(a.get("width", sequence.width)), int(a.get("height", sequence.height)),
            float(a.get("fps", sequence.fps)),
        )

    return operation


def _duplicate_sequence(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        sequence_id = _sequence_id(project, a["sequence"])
        if a.get("name") is not None:
            _require_free_sequence_name(project, a["name"])
        return duplicate_sequence(project, sequence_id, a.get("name"), clone_id=a.get("id"))

    return operation


def _insert_sequence_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        # La piste est celle de la séquence ACTIVE (comme pour ``add_clip``) ; ``insert_sequence_clip`` vérifie cycle, profondeur,
        # genre et verrou de piste, bornes de la source.
        return insert_sequence_clip(
            project, _sequence_id(project, a["sequence"]), _track_id(project, a["track"]), float(a["at"]),
            source_in=float(a.get("source_in", 0.0)),
            source_out=float(a["source_out"]) if "source_out" in a else None,
            clip_id=a.get("id"),
        )

    return operation


def _set_active_sequence(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        sequence = find_sequence(project, _sequence_id(project, a["sequence"]))
        project.active_sequence_id = sequence.id
        return sequence

    return operation


def _delete_sequence(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        sequence = find_sequence(project, _sequence_id(project, a["sequence"]))
        if sequence.id == project.active_sequence_id:
            raise ValueError(
                f"La séquence '{sequence.id}' est la séquence active : rendez-en une autre active (set_active_sequence) avant de la supprimer."
            )
        delete_sequence(project, sequence.id)         # sans ``force`` : une séquence utilisée ailleurs (clips imbriqués) est refusée
        return None

    return operation


# --- Multicam (docs/multicam.md) ---------------------------------------------------------------------------------------

_ANGLE_ID = re.compile(r"^angle-[0-9]+$")


def _multicam_source(project: Project, selector: str) -> Sequence:
    """La source Multicam désignée (identifiant ou nom exact unique de séquence) ; une séquence ordinaire est refusée."""
    sequence = find_sequence(project, _sequence_id(project, selector))
    if sequence.multicam is None:
        raise MulticamError(f"« {sequence.name} » n'est pas une source Multicam.")
    return sequence


def _angles(source: Sequence) -> list[Any]:
    """Les angles d'une source Multicam (``_multicam_source`` a déjà refusé une séquence ordinaire)."""
    if source.multicam is None:
        raise MulticamError(f"« {source.name} » n'est pas une source Multicam.")
    return list(source.multicam.angles)


def _angle_id(source: Sequence, selector: str) -> str:
    """L'angle de la source désigné par son identifiant (``angle-N``) ou son nom exact (unique dans la source)."""
    return str(_pick(_angles(source), selector, "Angle").id)


def _require_angle_name(name: str, taken: Iterable[str], own_id: str = "") -> None:
    """Un script désigne un angle par son nom : il doit être unique et ne pas se faire passer pour l'identifiant d'un autre angle.
    Le modèle, lui, ajouterait « 2 » à un nom pris (``rename_angle`` l'accepte même en double) : un script ne reçoit jamais autre chose
    que ce qu'il a écrit."""
    if name in set(taken):
        raise ValueError(f"Un angle s'appelle déjà {name!r} : un script désigne un angle par son nom, il doit rester unique.")
    if _ANGLE_ID.match(name) and name != own_id:
        raise ValueError(f"Le nom d'angle {name!r} ressemble à un identifiant d'angle (angle-N) : choisissez un autre nom.")


def _angle_asset(project: Project, selector: str) -> Any:
    """Le média désigné, après avoir vérifié qu'il a une durée (un clip d'angle de durée nulle ne montrerait rien)."""
    asset = next(item for item in project.media_assets if item.id == _asset_id(project, selector))
    if not asset.duration > 0:
        raise ValueError(f"Le média {asset.id!r} n'a pas de durée connue : il ne peut pas devenir un angle.")
    return asset


def _effective_angle_name(name: str | None, asset: Any) -> str:
    """Le nom que le modèle donnera à l'angle : celui du script, sinon celui du média (``_angle_clip``)."""
    return ((name if name is not None else asset.name) or "").strip() or "Angle"


def _create_multicam_source(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source_name = a.get("name", "Multicam")                  # le défaut aussi : le modèle suffixerait « Multicam » s'il est pris
        _require_free_sequence_name(project, source_name)
        listed = a["angles"]
        offsets = [float(item.get("offset", 0.0)) for item in listed]
        if min(offsets) != 0.0:
            # ``create_multicam_source`` ramène le plus petit décalage à 0 (les écarts comptent, pas l'origine) : un script le dit, il ne le subit pas.
            raise ValueError(
                f"Le plus petit décalage d'angle doit valoir 0 (les décalages sont relatifs ; reçu {min(offsets):g} s) : "
                "retranchez-le de chaque angle."
            )
        specs = []
        names: list[str] = []
        for position, item in enumerate(listed):
            asset = _angle_asset(project, item["asset"])
            # Le nom effectif (celui du média quand le script n'en donne pas) est soumis aux mêmes règles : unique, et pas un « angle-N »
            # qui ferait désigner un autre angle.
            _require_angle_name(
                _effective_angle_name(item.get("name"), asset), names, f"angle-{position + 1}",
            )
            names.append(_effective_angle_name(item.get("name"), asset))
            specs.append(AngleSpec(
                asset_id=asset.id, offset=offsets[position], name=item.get("name", ""),
                sync_method=SyncMethod.MANUAL,                       # les décalages sont ceux que le script a écrits : aucune mesure
            ))
        sequence = create_multicam_source(
            project, specs, name=source_name, sync_method=SyncMethod.MANUAL, sequence_id=a.get("id"),
        )
        for item, angle in zip(listed, _angles(sequence)):
            if item.get("name") is not None and angle.name != item["name"]:
                # Un nom écrit qui entre en collision avec le nom par défaut (nom du média) d'un autre angle : le modèle l'aurait suffixé.
                raise ValueError(f"Le nom d'angle {item['name']!r} est déjà pris par un autre angle : nommez-les tous explicitement.")
        return sequence

    return operation


def _insert_multicam_clip(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        angle = _angle_id(source, a["angle"]) if "angle" in a else ""
        # Piste de la séquence ACTIVE ; cycle, profondeur, « pas de Multicam dans un Multicam », genre et verrou de piste, bornes de la source :
        # ``insert_multicam_clip`` -> ``insert_sequence_clip`` les vérifient (erreurs ``SequenceError`` rendues en ``MulticamError``).
        return insert_multicam_clip(
            project, source.id, _track_id(project, a["track"]), float(a["at"]), angle_id=angle,
            source_in=float(a.get("source_in", 0.0)), source_out=float(a["source_out"]) if "source_out" in a else None,
            clip_id=a.get("id"),
        )

    return operation


def _switch_multicam_angle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _clip_id(project, a["clip"]) if "clip" in a else ""
        track_id = _track_id(project, a["track"]) if "track" in a else ""
        at = float(a["at"])
        segment = multicam_segment_at(project, at, clip_id=clip_id, track_id=track_id)
        if segment is None:
            raise MulticamError("Aucun segment Multicam sous cet instant (piste vidéo de la séquence active).")
        # ``switch_angle`` ne vérifie le verrou que par ``cut_clip`` : au début d'un segment il change l'angle sans coupe, donc sans contrôle.
        _require_unlocked(_owner_track(project, segment.id))
        source = find_sequence(project, segment.sequence_id)
        result = switch_angle(project, at, _angle_id(source, a["angle"]), clip_id=segment.id, segment_id=a.get("id"))
        return result.clip if result is not None else segment             # déjà cet angle : aucune coupe, on rend le segment tel quel

    return operation


def _replace_multicam_angle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"])                     # ``replace_angle`` ne regarde pas le verrou de la piste
        clip = find_clip(project, clip_id)
        if not is_multicam_clip(project, clip):
            raise MulticamError("Ce clip n'est pas un segment Multicam.")
        return replace_angle(project, clip_id, _angle_id(find_sequence(project, clip.sequence_id), a["angle"]))

    return operation


def _set_multicam_audio(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        mode = AudioMode(a["mode"])
        ids = tuple(_angle_id(source, selector) for selector in a.get("angles", ()))
        if len(set(ids)) != len(ids):
            raise ValueError("set_multicam_audio : un angle est cité deux fois.")        # le modèle écarterait le doublon en silence
        if mode is AudioMode.FOLLOW_VIDEO and ids:
            raise ValueError("set_multicam_audio : le mode « follow » ne prend aucun angle (le son suit l'image).")   # le modèle l'ignorerait
        return set_audio_policy(project, source.id, mode, ids)

    return operation


def _rename_multicam_angle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        angle_id = _angle_id(source, a["angle"])
        _require_angle_name(a["name"], [angle.name for angle in _angles(source) if angle.id != angle_id], angle_id)
        return rename_angle(project, source.id, angle_id, a["name"])

    return operation


def _add_multicam_angle(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        existing = _angles(source)
        if len(existing) >= MAX_ANGLES:
            raise ValueError(f"Une source Multicam compte {MAX_ANGLES} angles au plus (un fichier qui en déclare davantage est refusé).")
        asset = _angle_asset(project, a["asset"])
        _require_angle_name(_effective_angle_name(a.get("name"), asset), [angle.name for angle in existing])
        angle = add_angle(project, source.id, AngleSpec(
            asset_id=asset.id, offset=float(a.get("offset", 0.0)), name=a.get("name", ""), sync_method=SyncMethod.MANUAL,
        ))
        if a.get("name") is not None and angle.name != a["name"]:
            raise ValueError(f"Le nom d'angle {a['name']!r} est déjà pris par un autre angle.")
        return angle

    return operation


def _set_multicam_angle_offset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        source = _multicam_source(project, a["sequence"])
        angle_id = _angle_id(source, a["angle"])
        angle = next(item for item in _angles(source) if item.id == angle_id)
        track = next((item for item in source.tracks if item.id == angle.track_id), None)
        if track is not None:
            _require_unlocked(track)                                      # ``set_angle_offset`` déplace les clips sans regarder le verrou
        return set_angle_offset(project, source.id, angle_id, float(a["offset"]))

    return operation


_TRACK = Param("track", Kind.SELECTOR, "La piste : son identifiant ou son nom exact (unique).", of="track")
_BUS = Param("bus", Kind.SELECTOR, "Le bus : son identifiant ou son nom exact (unique).", of="bus")
_SEQUENCE = Param("sequence", Kind.SELECTOR, "La séquence : son identifiant ou son nom exact (unique).", of="sequence")
_SOURCE = Param("sequence", Kind.SELECTOR, "La source Multicam : l'identifiant ou le nom exact (unique) de sa séquence.", of="sequence")
_ANGLE = Param("angle", Kind.SELECTOR, "L'angle de la source : son identifiant (angle-N) ou son nom exact (unique dans la source).", of="angle")
_ANGLE_OPTIONAL = Param("angle", Kind.SELECTOR, "L'angle montré (identifiant angle-N ou nom exact) ; absent, le premier.", required=False, of="angle")
_CLIP = Param("clip", Kind.SELECTOR, "Le clip, par son identifiant.", of="clip")
_INSERT = Param("insert", Kind.SELECTOR, "L'insert du bus, par son identifiant.", of="insert")
_NODE = Param("node", Kind.SELECTOR, "Le nœud d'étalonnage, par identifiant (défaut : le premier).", required=False, of="color_node")
_AT = Param("at", Kind.NUMBER, "Position sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME)
_ENABLED = Param("enabled", Kind.BOOLEAN, "Actif ou non.")
_PARAMS = Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres, booléens, textes courts) ; ceux du modèle les valident.")
_EFFECTS = tuple(member.value for member in AudioEffectType)
_DISPLAYABLE = tuple(item.value for item in (
    GraphicType.TEXT, GraphicType.RECTANGLE, GraphicType.SOLID, GraphicType.SHAPE, GraphicType.ADJUSTMENT, GraphicType.NULL,
))
_COLOR_DOC = "Couleur « #RRGGBB » ou « #RRGGBBAA »."
_CHANNEL = Param("channel", Kind.ENUM, "Canal de courbe.", choices=CHANNELS)
_LABEL = Param("label", Kind.STRING, "Nom du nœud.", required=False, max_length=MAX_LABEL_LENGTH)

VERBS: dict[str, Verb] = {
    verb.name: verb
    for verb in (
        Verb("add_bus", "Ajoute un bus audio à la séquence active.",
             (Param("name", Kind.STRING, "Nom du bus.", required=False),), _add_bus, creates="bus", group="audio"),
        Verb("remove_bus", "Supprime un bus ; ses pistes retournent au Master.", (_BUS,), _remove_bus, group="audio"),
        Verb("rename_bus", "Renomme un bus.", (_BUS, Param("name", Kind.STRING, "Nouveau nom.")), _rename_bus, group="audio"),
        Verb("set_bus_volume", "Règle le volume d'un bus.",
             (_BUS, Param("volume_db", Kind.NUMBER, "Volume en dB.", minimum=MIN_GAIN_DB, maximum=MAX_GAIN_DB)), _set_bus_volume, group="audio"),
        Verb("set_bus_muted", "Coupe ou rétablit un bus.", (_BUS, Param("muted", Kind.BOOLEAN, "Coupé ou non.")), _set_bus_muted,
             group="audio"),
        Verb("route_track", "Envoie une piste sur un bus (sans ``bus`` : directement au Master).",
             (_TRACK, Param("bus", Kind.SELECTOR, "Le bus visé ; absent, le Master.", required=False, of="bus")), _route_track,
             group="audio"),
        Verb("add_bus_insert", "Ajoute un effet en insert sur un bus.",
             (_BUS, Param("effect", Kind.ENUM, "Le type d'effet.", choices=_EFFECTS),
              Param("params", Kind.OBJECT, _PARAMS.doc, required=False),
              Param("enabled", Kind.BOOLEAN, "Actif dès l'ajout (défaut : oui).", required=False)),
             _add_bus_insert, creates="insert", group="audio"),
        Verb("remove_bus_insert", "Retire un insert d'un bus.", (_BUS, _INSERT), _remove_bus_insert, group="audio"),
        Verb("set_bus_insert_enabled", "Active ou désactive un insert de bus.", (_BUS, _INSERT, _ENABLED), _set_bus_insert_enabled,
             group="audio"),
        Verb("update_bus_insert", "Modifie les paramètres d'un insert de bus.", (_BUS, _INSERT, _PARAMS), _update_bus_insert,
             group="audio"),
        Verb("add_clip", "Pose un média du projet sur une piste.",
             (_TRACK, Param("asset", Kind.SELECTOR, "Le média du projet : identifiant ou nom exact (unique).", of="asset"), _AT),
             _add_clip, creates="clip", group="edit"),
        Verb("delete_clip", "Supprime un clip.", (_CLIP,), _delete_clip, group="edit"),
        Verb("move_clip", "Déplace un clip.", (_CLIP, _AT), _move_clip, group="edit"),
        Verb("set_clip_enabled", "Active ou désactive un clip.", (_CLIP, _ENABLED), _set_clip_enabled, group="edit"),
        Verb("set_media_color_matrix",
             "Choisit comment lire les couleurs d'un média vidéo : « auto » (son étiquette, sinon BT.709 dès 720 lignes ou 1280 colonnes, "
             "sinon BT.601), « bt601 » ou « bt709 » (force, étiquette comprise). Vaut pour tous les clips du média ; refusé si l'un d'eux "
             "est sur une piste ou un calque verrouillé.",
             (Param("asset", Kind.SELECTOR, "Le média du projet : identifiant ou nom exact (unique).", required=False, of="asset"),
              Param("clip", Kind.SELECTOR, "Ou un clip : son média est réglé.", required=False, of="clip"),
              Param("matrix", Kind.ENUM, "Le choix.", choices=MATRIX_CHOICES)),
             _set_media_color_matrix, group="edit"),
        Verb("import_media", "Importe un fichier média dans le projet (lecture seule, dans le dossier du script).",
             (Param("path", Kind.PATH, "Chemin du fichier, relatif au dossier du script."),), _import_media, creates="asset",
             group="edit", needs_context=True),
        Verb("add_track_insert", "Ajoute un effet en insert sur une piste audio (un égaliseur est l'effet « parametric_eq »).",
             (_TRACK, Param("effect", Kind.ENUM, "Le type d'effet.", choices=_EFFECTS),
              Param("params", Kind.OBJECT, _PARAMS.doc, required=False),
              Param("enabled", Kind.BOOLEAN, "Actif dès l'ajout (défaut : oui).", required=False)),
             _add_track_insert, creates="insert", group="audio"),
        Verb("remove_track_insert", "Retire un insert d'une piste.", (_TRACK, _INSERT), _remove_track_insert, group="audio"),
        Verb("set_track_insert_enabled", "Active ou désactive un insert de piste.", (_TRACK, _INSERT, _ENABLED),
             _set_track_insert_enabled, group="audio"),
        Verb("update_track_insert", "Modifie les paramètres d'un insert de piste.", (_TRACK, _INSERT, _PARAMS), _update_track_insert,
             group="audio"),
        Verb("set_track_muted", "Coupe ou rétablit le son d'une piste.", (_TRACK, Param("muted", Kind.BOOLEAN, "Coupée ou non.")),
             _set_track_muted, group="edit"),
        Verb("set_track_locked", "Verrouille ou déverrouille une piste.", (_TRACK, Param("locked", Kind.BOOLEAN, "Verrouillée ou non.")),
             _set_track_locked, group="edit"),
        Verb("set_track_visible", "Affiche ou masque une piste.", (_TRACK, Param("visible", Kind.BOOLEAN, "Visible ou non.")),
             _set_track_visible, group="edit"),
        Verb("add_graphic", "Pose un calque graphique (titre, forme, aplat…) sur une piste de graphiques.",
             (Param("graphic", Kind.ENUM, "Le genre de calque.", choices=_DISPLAYABLE), _AT,
              Param("duration", Kind.NUMBER, "Durée en secondes.", minimum=0.04, maximum=MAX_TIME),
              Param("text", Kind.STRING, "Texte du calque (titre).", required=False, max_length=1000, multiline=True),
              Param("shape", Kind.ENUM, "Forme (pour « shape »).", required=False, choices=tuple(item.value for item in ShapeKind)),
              Param("track", Kind.SELECTOR, "Piste de graphiques visée ; absente, la première éditable.", required=False, of="track"),
              Param("group", Kind.SELECTOR, "Groupe où poser le calque (un groupe déverrouillé, pas pour un calque d'effets) ; absent, au niveau racine.",
                    required=False, of="clip")),
             _add_graphic, creates="clip", group="graphics"),
        Verb("add_image_layer", "Pose une image fixe (PNG, JPEG, WebP…, dans le dossier du script) en calque graphique, au-dessus de la vidéo.",
             (Param("path", Kind.PATH, "Chemin de l'image, relatif au dossier du script."), _AT,
              Param("duration", Kind.NUMBER, "Durée en secondes.", minimum=0.04, maximum=MAX_TIME),
              Param("track", Kind.SELECTOR, "Piste de graphiques visée ; absente, la première éditable.", required=False, of="track")),
             _add_image_layer, creates="clip", group="graphics", needs_context=True),
        Verb("set_graphic", "Modifie l'apparence d'un calque graphique (au moins un champ).",
             (_CLIP, Param("text", Kind.STRING, "Texte.", required=False, max_length=1000, multiline=True),
              Param("fill_color", Kind.COLOR, _COLOR_DOC, required=False),
              Param("stroke_color", Kind.COLOR, _COLOR_DOC, required=False),
              Param("stroke_width", Kind.INTEGER, "Épaisseur du contour, en pixels.", required=False, minimum=0, maximum=256),
              Param("font_size", Kind.INTEGER, "Taille de police, en pixels.", required=False, minimum=6, maximum=512),
              Param("width", Kind.INTEGER, "Largeur, en pixels.", required=False, minimum=2, maximum=8192),
              Param("height", Kind.INTEGER, "Hauteur, en pixels.", required=False, minimum=2, maximum=8192)),
             _set_graphic, group="graphics"),
        Verb("add_track", "Ajoute une piste (le nom, s'il est donné, doit être libre).",
             (Param("type", Kind.ENUM, "Le genre de piste.", choices=("video", "audio", "subtitle")),
              Param("name", Kind.STRING, "Nom de la piste.", required=False)),
             _add_track, creates="track", group="edit"),
        Verb("add_subtitle", "Pose un sous-titre sur une piste de sous-titres.",
             (Param("text", Kind.STRING, "Le texte (plusieurs lignes permises).", max_length=1000, multiline=True), _AT,
              Param("duration", Kind.NUMBER, "Durée en secondes.", minimum=0.04, maximum=MAX_TIME),
              Param("track", Kind.SELECTOR, "Piste de sous-titres visée ; absente, la première.", required=False, of="track")),
             _add_subtitle, creates="clip", group="edit"),
        Verb("add_transition", "Ajoute une transition entre deux clips vidéo adjacents d'une même piste.",
             (Param("from_clip", Kind.SELECTOR, "Le clip sortant, par identifiant.", of="clip"),
              Param("to_clip", Kind.SELECTOR, "Le clip entrant, par identifiant.", of="clip"),
              Param("transition", Kind.ENUM, "Le genre de transition.", choices=tuple(item.value for item in TransitionType)),
              Param("duration", Kind.NUMBER, "Durée en secondes (au plus la moitié du plus court des deux clips).",
                    minimum=MIN_TRANSITION_SECONDS, maximum=MAX_TIME)),
             _add_transition, creates="transition", group="edit"),
        Verb("remove_transition", "Retire une transition.",
             (Param("transition", Kind.SELECTOR, "La transition, par identifiant.", of="transition"),), _remove_transition, group="edit"),
        Verb("add_keyframe", "Pose (ou remplace) une image clé d'une propriété du clip ; le temps est relatif au clip.",
             (_CLIP, Param("property", Kind.ENUM, "La propriété animée.", choices=_ANIMATABLE),
              Param("at", Kind.NUMBER, "Instant, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME),
              Param("value", Kind.NUMBER, "La valeur (dans les bornes de la propriété).", minimum=-MAX_TIME, maximum=MAX_TIME),
              Param("interpolation", Kind.ENUM, "Interpolation vers l'image clé suivante.", required=False,
                    choices=tuple(item.value for item in InterpolationType))),
             _add_keyframe, group="animation"),
        Verb("remove_keyframe", "Retire l'image clé d'une propriété à un instant donné.",
             (_CLIP, Param("property", Kind.ENUM, "La propriété animée.", choices=_ANIMATABLE),
              Param("at", Kind.NUMBER, "Instant, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)),
             _remove_keyframe, group="animation"),
        Verb("set_color_grade", "Règle l'étalonnage d'un nœud du clip ; au moins un champ.",
             (_CLIP, _NODE,
              Param("exposure", Kind.NUMBER, "Exposition, en diaphragmes.", required=False, minimum=EXPOSURE_MIN, maximum=EXPOSURE_MAX),
              Param("contrast", Kind.NUMBER, "Contraste.", required=False, minimum=CONTRAST_MIN, maximum=CONTRAST_MAX),
              Param("saturation", Kind.NUMBER, "Saturation (1 = neutre).", required=False, minimum=SATURATION_MIN, maximum=SATURATION_MAX),
              Param("temperature", Kind.NUMBER, "Température.", required=False, minimum=TEMPERATURE_MIN, maximum=TEMPERATURE_MAX),
              Param("hue", Kind.NUMBER, "Teinte, en degrés.", required=False, minimum=HUE_MIN, maximum=HUE_MAX),
              Param("shadows", Kind.NUMBER, "Ombres.", required=False, minimum=SHADOWS_MIN, maximum=SHADOWS_MAX),
              Param("highlights", Kind.NUMBER, "Hautes lumières.", required=False, minimum=HIGHLIGHTS_MIN, maximum=HIGHLIGHTS_MAX),
              Param("enabled", Kind.BOOLEAN, "Étalonnage actif ou neutralisé.", required=False)),
             _set_color_grade, group="color"),
        Verb("reset_color_grade", "Remet l'étalonnage d'un nœud du clip à neutre.", (_CLIP, _NODE), _reset_color_grade, group="color"),
        Verb("set_color_curve", "Règle la sortie d'un point d'une courbe (index 0…15 sur la courbe à 16 points).",
             (_CLIP, _NODE, _CHANNEL,
              Param("index", Kind.INTEGER, "Index du point (0 = début).", minimum=0, maximum=MAX_CURVE_POINTS - 1),
              Param("output", Kind.NUMBER, "Valeur de sortie, de 0 à 1.", minimum=MIN_CURVE_VALUE, maximum=MAX_CURVE_VALUE)),
             _set_color_curve, group="color"),
        Verb("reset_color_curve", "Remet une courbe à l'identité (16 points).",
             (_CLIP, _NODE, _CHANNEL), _reset_color_curve, group="color"),
        Verb("set_color_wheel", "Règle une roue primaire (lift, gamma, gain, offset) ; au moins une composante.",
             (_CLIP, _NODE,
              Param("wheel", Kind.ENUM, "La roue.", choices=WHEELS),
              Param("r", Kind.NUMBER, "Rouge.", required=False, minimum=WHEEL_MIN, maximum=WHEEL_MAX),
              Param("g", Kind.NUMBER, "Vert.", required=False, minimum=WHEEL_MIN, maximum=WHEEL_MAX),
              Param("b", Kind.NUMBER, "Bleu.", required=False, minimum=WHEEL_MIN, maximum=WHEEL_MAX),
              Param("y", Kind.NUMBER, "Maître (Y).", required=False, minimum=WHEEL_MIN, maximum=WHEEL_MAX)),
             _set_color_wheel, group="color"),
        Verb("import_lut", "Attache un LUT « .cube » (lecture seule, dans le dossier du script) à un nœud.",
             (_CLIP, _NODE, Param("path", Kind.PATH, "Chemin du fichier .cube, relatif au dossier du script.")),
             _import_lut, group="color", needs_context=True),
        Verb("remove_lut", "Retire le LUT d'un nœud.", (_CLIP, _NODE), _remove_lut, group="color"),
        Verb("add_color_node", "Ajoute un nœud d'étalonnage en série (après « after », ou en bout de chaîne).",
             (_CLIP, Param("after", Kind.SELECTOR, "Nœud après lequel insérer ; absent, en bout.", required=False, of="color_node"),
              _LABEL),
             _add_color_node, creates="color_node", creates_scope="clip", group="color"),
        Verb("branch_color_node", "Ajoute un nœud en parallèle ou en calque à côté d'un nœud existant.",
             (_CLIP, Param("node", Kind.SELECTOR, "Le nœud à côté duquel brancher.", of="color_node"),
              Param("kind", Kind.ENUM, "parallel ou layer.", choices=tuple(item.value for item in MixerKind)),
              _LABEL),
             _branch_color_node, creates="color_node", creates_scope="clip", group="color"),
        Verb("remove_color_node", "Supprime un nœud (pas le dernier : réinitialisez-le).",
             (_CLIP, Param("node", Kind.SELECTOR, "Le nœud à retirer.", of="color_node")),
             _remove_color_node, group="color"),
        Verb("rename_color_node", "Nomme un nœud d'étalonnage.",
             (_CLIP, Param("node", Kind.SELECTOR, "Le nœud à renommer.", of="color_node"),
              Param("label", Kind.STRING, "Nouveau nom.", max_length=MAX_LABEL_LENGTH)),
             _rename_color_node, group="color"),
        Verb("set_color_detail", "Règle le flou et/ou la netteté d'un nœud (bornes du modèle, rien n'est ramené en silence).",
             (_CLIP, _NODE,
              Param("blur", Kind.NUMBER, "Flou σ en pixels (0…50).", required=False, minimum=0.0, maximum=MAX_BLUR),
              Param("sharpen", Kind.NUMBER, "Netteté (0…5).", required=False, minimum=0.0, maximum=MAX_SHARPEN)),
             _set_color_detail, group="color"),
        Verb("add_color_window", "Ajoute une fenêtre (masque) sur un nœud d'étalonnage.",
             (_CLIP, _NODE,
              Param("shape", Kind.ENUM, "rectangle, ellipse, polygon ou path (courbe de Bézier : voir « nodes »).",
                    choices=tuple(item.value for item in MaskShape)),
              Param("position_x", Kind.NUMBER, "Centre X (espace calque).", required=False,
                    minimum=_MASK_BOUNDS["position_x"][0], maximum=_MASK_BOUNDS["position_x"][1]),
              Param("position_y", Kind.NUMBER, "Centre Y (espace calque).", required=False,
                    minimum=_MASK_BOUNDS["position_y"][0], maximum=_MASK_BOUNDS["position_y"][1]),
              Param("width", Kind.NUMBER, "Largeur (défaut 0,4).", required=False,
                    minimum=_MASK_BOUNDS["width"][0], maximum=_MASK_BOUNDS["width"][1]),
              Param("height", Kind.NUMBER, "Hauteur (défaut 0,4).", required=False,
                    minimum=_MASK_BOUNDS["height"][0], maximum=_MASK_BOUNDS["height"][1]),
              Param("rotation", Kind.NUMBER, "Rotation, en degrés.", required=False,
                    minimum=_MASK_BOUNDS["rotation"][0], maximum=_MASK_BOUNDS["rotation"][1]),
              Param("feather", Kind.NUMBER, "Adoucissement (défaut 0,03).", required=False,
                    minimum=_MASK_BOUNDS["feather"][0], maximum=_MASK_BOUNDS["feather"][1]),
              Param("expansion", Kind.NUMBER, "Expansion.", required=False,
                    minimum=_MASK_BOUNDS["expansion"][0], maximum=_MASK_BOUNDS["expansion"][1]),
              Param("opacity", Kind.NUMBER, "Opacité de la fenêtre.", required=False,
                    minimum=_MASK_BOUNDS["opacity"][0], maximum=_MASK_BOUNDS["opacity"][1]),
              Param("inverted", Kind.BOOLEAN, "Fenêtre inversée.", required=False),
              Param("mode", Kind.ENUM, "add, subtract ou intersect.", required=False,
                    choices=tuple(item.value for item in MaskMode)),
              Param("name", Kind.STRING, "Nom de la fenêtre (vide : sans nom).", required=False, allow_empty=True),
              _nodes_param(required=False)),
             _add_color_window, creates="color_window", creates_scope="clip", group="color"),
        Verb("set_color_window", "Modifie une fenêtre d'étalonnage ; au moins un champ.",
             (_CLIP, Param("window", Kind.SELECTOR, "La fenêtre, par identifiant.", of="color_window"),
              Param("position_x", Kind.NUMBER, "Centre X.", required=False,
                    minimum=_MASK_BOUNDS["position_x"][0], maximum=_MASK_BOUNDS["position_x"][1]),
              Param("position_y", Kind.NUMBER, "Centre Y.", required=False,
                    minimum=_MASK_BOUNDS["position_y"][0], maximum=_MASK_BOUNDS["position_y"][1]),
              Param("width", Kind.NUMBER, "Largeur.", required=False,
                    minimum=_MASK_BOUNDS["width"][0], maximum=_MASK_BOUNDS["width"][1]),
              Param("height", Kind.NUMBER, "Hauteur.", required=False,
                    minimum=_MASK_BOUNDS["height"][0], maximum=_MASK_BOUNDS["height"][1]),
              Param("rotation", Kind.NUMBER, "Rotation, en degrés.", required=False,
                    minimum=_MASK_BOUNDS["rotation"][0], maximum=_MASK_BOUNDS["rotation"][1]),
              Param("feather", Kind.NUMBER, "Adoucissement.", required=False,
                    minimum=_MASK_BOUNDS["feather"][0], maximum=_MASK_BOUNDS["feather"][1]),
              Param("expansion", Kind.NUMBER, "Expansion.", required=False,
                    minimum=_MASK_BOUNDS["expansion"][0], maximum=_MASK_BOUNDS["expansion"][1]),
              Param("opacity", Kind.NUMBER, "Opacité.", required=False,
                    minimum=_MASK_BOUNDS["opacity"][0], maximum=_MASK_BOUNDS["opacity"][1]),
              Param("inverted", Kind.BOOLEAN, "Fenêtre inversée.", required=False),
              Param("mode", Kind.ENUM, "add, subtract ou intersect.", required=False,
                    choices=tuple(item.value for item in MaskMode)),
              Param("name", Kind.STRING, "Nom.", required=False)),
             _set_color_window, group="color"),
        Verb("set_color_window_points", "Remplace les nœuds (ancres et tangentes) d'une fenêtre en courbe de Bézier ; refusé pour une autre forme.",
             (_CLIP, Param("window", Kind.SELECTOR, "La fenêtre, par identifiant.", of="color_window"), _nodes_param(required=True)),
             _set_color_window_points, group="color"),
        Verb("remove_color_window", "Retire une fenêtre d'étalonnage (et ses images-clés associées).",
             (_CLIP, Param("window", Kind.SELECTOR, "La fenêtre, par identifiant.", of="color_window")),
             _remove_color_window, group="color"),
        Verb("set_color_qualifier", "Règle le qualifieur TSL d'un nœud ; au moins un champ.",
             (_CLIP, _NODE,
              Param("enabled", Kind.BOOLEAN, "Qualifieur actif.", required=False),
              Param("use_hue", Kind.BOOLEAN, "Utiliser la teinte.", required=False),
              Param("hue_center", Kind.NUMBER, "Centre de teinte, en degrés.", required=False, minimum=0.0, maximum=360.0),
              Param("hue_width", Kind.NUMBER, "Largeur de teinte, en degrés.", required=False, minimum=0.0, maximum=360.0),
              Param("hue_soft", Kind.NUMBER, "Douceur de teinte, en degrés.", required=False, minimum=0.0, maximum=180.0),
              Param("use_sat", Kind.BOOLEAN, "Utiliser la saturation.", required=False),
              Param("sat_low", Kind.NUMBER, "Bas de saturation (0…1).", required=False, minimum=0.0, maximum=1.0),
              Param("sat_high", Kind.NUMBER, "Haut de saturation (0…1).", required=False, minimum=0.0, maximum=1.0),
              Param("sat_soft", Kind.NUMBER, "Douceur de saturation.", required=False, minimum=0.0, maximum=1.0),
              Param("use_lum", Kind.BOOLEAN, "Utiliser la luminance.", required=False),
              Param("lum_low", Kind.NUMBER, "Bas de luminance (0…1).", required=False, minimum=0.0, maximum=1.0),
              Param("lum_high", Kind.NUMBER, "Haut de luminance (0…1).", required=False, minimum=0.0, maximum=1.0),
              Param("lum_soft", Kind.NUMBER, "Douceur de luminance.", required=False, minimum=0.0, maximum=1.0),
              Param("invert", Kind.BOOLEAN, "Inverser la clé.", required=False)),
             _set_color_qualifier, group="color"),
        Verb("clear_color_qualifier", "Retire le qualifieur d'un nœud (toute l'image est corrigée).",
             (_CLIP, _NODE), _clear_color_qualifier, group="color"),
        Verb("set_crop", "Recadre un clip vidéo ou un calque (un masque « Recadrage » intersect, modifiable ensuite) ; au moins un bord, les absents gardent leur valeur ; fractions du cadre, rien n'est ramené en silence.",
             (_CLIP,
              Param("left", Kind.NUMBER, "Bord gauche coupé (0…0,95).", required=False, minimum=0.0, maximum=MAX_CROP_SIDE),
              Param("top", Kind.NUMBER, "Bord haut coupé (0…0,95).", required=False, minimum=0.0, maximum=MAX_CROP_SIDE),
              Param("right", Kind.NUMBER, "Bord droit coupé (0…0,95) ; gauche + droite au plus 0,95.", required=False, minimum=0.0, maximum=MAX_CROP_SIDE),
              Param("bottom", Kind.NUMBER, "Bord bas coupé (0…0,95) ; haut + bas au plus 0,95.", required=False, minimum=0.0, maximum=MAX_CROP_SIDE)),
             _set_crop, group="edit"),
        Verb("clear_crop", "Retire le recadrage d'un clip (les autres masques restent).", (_CLIP,), _clear_crop, group="edit"),
        Verb("add_marker", "Ajoute un repère sur la timeline.",
             (_AT, Param("name", Kind.STRING, "Nom du repère.", required=False),
              Param("category", Kind.STRING, "Catégorie (défaut : standard).", required=False)),
             _add_marker, creates="marker", group="edit"),
        Verb("create_sequence", "Crée une séquence vide (pistes, cadre et cadence de la séquence active par défaut) ; elle n'est pas activée.",
             (Param("name", Kind.STRING, "Nom de la séquence (libre : un nom déjà pris est refusé).", required=False),
              Param("width", Kind.INTEGER, "Largeur en pixels (paire).", required=False, minimum=16, maximum=MAX_SEQUENCE_SIDE),
              Param("height", Kind.INTEGER, "Hauteur en pixels (paire).", required=False, minimum=16, maximum=MAX_SEQUENCE_SIDE),
              Param("fps", Kind.NUMBER, "Cadence, en images par seconde.", required=False, minimum=1.0, maximum=240.0)),
             _create_sequence, creates="sequence", group="sequences"),
        Verb("rename_sequence", "Renomme une séquence (le nom doit être libre).",
             (_SEQUENCE, Param("name", Kind.STRING, "Nouveau nom.")), _rename_sequence, group="sequences"),
        Verb("set_sequence_format", "Change le cadre et/ou la cadence d'une séquence ; au moins un champ (tailles paires).",
             (_SEQUENCE,
              Param("width", Kind.INTEGER, "Largeur en pixels (paire).", required=False, minimum=16, maximum=MAX_SEQUENCE_SIDE),
              Param("height", Kind.INTEGER, "Hauteur en pixels (paire).", required=False, minimum=16, maximum=MAX_SEQUENCE_SIDE),
              Param("fps", Kind.NUMBER, "Cadence, en images par seconde.", required=False, minimum=1.0, maximum=240.0)),
             _set_sequence_format, group="sequences"),
        Verb("duplicate_sequence", "Copie indépendante d'une séquence (les clips de la copie reçoivent des identifiants générés) ; non activée.",
             (_SEQUENCE, Param("name", Kind.STRING, "Nom de la copie (libre) ; absent, « <nom> copie ».", required=False)),
             _duplicate_sequence, creates="sequence", group="sequences"),
        Verb("insert_sequence_clip", "Insère une séquence comme clip imbriqué sur une piste vidéo ou audio de la séquence active.",
             (_SEQUENCE, _TRACK, _AT,
              Param("source_in", Kind.NUMBER, "Début lu dans la séquence source, en secondes (défaut 0).", required=False,
                    minimum=0.0, maximum=MAX_TIME),
              Param("source_out", Kind.NUMBER, "Fin lue dans la séquence source (défaut : sa durée ; au plus sa durée).", required=False,
                    minimum=0.0, maximum=MAX_TIME)),
             _insert_sequence_clip, creates="clip", group="sequences"),
        Verb("set_active_sequence", "Rend une séquence active : les verbes suivants (pistes, clips, bus, repères) agissent sur elle.",
             (_SEQUENCE,), _set_active_sequence, group="sequences"),
        Verb("delete_sequence", "Supprime une séquence inutilisée (ni active, ni la dernière, ni imbriquée ailleurs).",
             (_SEQUENCE,), _delete_sequence, group="sequences"),
        Verb("create_multicam_source",
             "Crée une source Multicam (une séquence, une piste par angle) depuis des médias du projet ; elle n'est pas activée ni insérée. "
             "Les angles s'appellent angle-1, angle-2… dans l'ordre de la liste ; politique audio proposée par le modèle (set_multicam_audio la change).",
             (Param("angles", Kind.LIST, "Les angles, dans l'ordre (de 1 à 64) : le média, son décalage et son nom.", minimum=1, maximum=MAX_ANGLES,
                    fields=(
                        Param("asset", Kind.SELECTOR, "Le média du projet (vidéo ou son) : identifiant ou nom exact (unique).", of="asset"),
                        Param("offset", Kind.NUMBER, "Début de l'angle dans la source, en secondes (défaut 0) ; le plus petit doit valoir 0.",
                              required=False, minimum=0.0, maximum=MAX_TIME),
                        Param("name", Kind.STRING, "Nom de l'angle (unique dans la source) ; absent, le nom du média.", required=False))),
              Param("name", Kind.STRING, "Nom de la source (libre : un nom déjà pris est refusé) ; absent, « Multicam ».", required=False)),
             _create_multicam_source, creates="sequence", group="multicam"),
        Verb("insert_multicam_clip", "Insère une source Multicam comme segment sur une piste vidéo ou audio de la séquence active (pas dans une autre source Multicam).",
             (_SOURCE, _TRACK, _AT, _ANGLE_OPTIONAL,
              Param("source_in", Kind.NUMBER, "Début lu dans la source, en secondes (défaut 0).", required=False, minimum=0.0, maximum=MAX_TIME),
              Param("source_out", Kind.NUMBER, "Fin lue dans la source (défaut : sa durée ; au plus sa durée).", required=False, minimum=0.0, maximum=MAX_TIME)),
             _insert_multicam_clip, creates="clip", group="multicam"),
        Verb("switch_multicam_angle",
             "Montre un angle à partir d'un instant : coupe le segment Multicam de la séquence active à cet instant et change l'angle de la moitié droite "
             "(les transitions du segment coupé sont retirées, comme pour toute coupe) ; rend le segment qui montre l'angle. "
             "« id » désigne la moitié droite : refusé sans coupe (début de segment, angle déjà montré).",
             (_AT, _ANGLE,
              Param("clip", Kind.SELECTOR, "Le segment visé, par son identifiant (défaut : celui de la piste vidéo la plus haute à cet instant).", required=False, of="clip"),
              Param("track", Kind.SELECTOR, "Restreint la recherche à cette piste vidéo.", required=False, of="track")),
             _switch_multicam_angle, creates="clip", group="multicam"),
        Verb("replace_multicam_angle", "Change l'angle d'un segment existant, sans coupe.",
             (_CLIP, _ANGLE), _replace_multicam_angle, group="multicam"),
        Verb("set_multicam_audio",
             "Règle la politique audio d'une source : « follow » (le son suit l'image, sans angle), « fixed » (exactement un angle audio) ou « mix » (un ou plusieurs).",
             (_SOURCE, Param("mode", Kind.ENUM, "La politique.", choices=tuple(mode.value for mode in AudioMode)),
              Param("angles", Kind.LIST, "Les angles audio (identifiant ou nom exact), selon le mode.", required=False, minimum=1, maximum=MAX_ANGLES,
                    item=Param("angle", Kind.SELECTOR, "Un angle de la source.", of="angle"))),
             _set_multicam_audio, group="multicam"),
        Verb("rename_multicam_angle", "Renomme un angle et sa piste (le nom doit être libre dans la source).",
             (_SOURCE, _ANGLE, Param("name", Kind.STRING, "Nouveau nom.")), _rename_multicam_angle, group="multicam"),
        Verb("add_multicam_angle", "Ajoute un angle (média du projet) à une source Multicam ; son identifiant est angle-N, N suivant le plus grand existant.",
             (_SOURCE, Param("asset", Kind.SELECTOR, "Le média du projet (vidéo ou son) : identifiant ou nom exact (unique).", of="asset"),
              Param("offset", Kind.NUMBER, "Début de l'angle dans la source, en secondes (défaut 0).", required=False, minimum=0.0, maximum=MAX_TIME),
              Param("name", Kind.STRING, "Nom de l'angle (unique dans la source) ; absent, le nom du média.", required=False)),
             _add_multicam_angle, group="multicam"),
        Verb("set_multicam_angle_offset", "Recale un angle : ses clips sont déplacés pour que le premier commence à cet instant de la source (réglage manuel).",
             (_SOURCE, _ANGLE, Param("offset", Kind.NUMBER, "Nouveau début de l'angle dans la source, en secondes.", minimum=0.0, maximum=MAX_TIME)),
             _set_multicam_angle_offset, group="multicam"),
    )
}
"""Le catalogue. L'ordre est celui de la documentation."""

from .animation_verbs import ANIMATION_VERBS  # noqa: E402 - après la table : ces modules importent les paramètres et aides de celui-ci
from .auto_verbs import AUTOMATION_VERBS  # noqa: E402
from .clip_audio_verbs import CLIP_AUDIO_VERBS  # noqa: E402
from .edit_verbs import CLIP_EDIT_VERBS, MARKER_VERBS, TRACK_VERBS  # noqa: E402
from .effect_verbs import EFFECT_VERBS  # noqa: E402
from .guide_verbs import GUIDE_VERBS  # noqa: E402
from .layer_verbs import LAYER_VERBS  # noqa: E402
from .mask_verbs import MASK_VERBS  # noqa: E402
from .time_verbs import TIME_VERBS  # noqa: E402
from .tracking_verbs import TRACKING_VERBS  # noqa: E402
from .transform_verbs import TRANSFORM_VERBS  # noqa: E402

_EXTRA_VERBS: dict[str, tuple[Verb, ...]] = {
    "move_clip": CLIP_EDIT_VERBS,               # le montage (rogner, couper, glisser…) juste après le déplacement
    "add_track": TRACK_VERBS,
    "add_marker": MARKER_VERBS + GUIDE_VERBS,    # les guides du moniteur après les repères
    "set_clip_enabled": EFFECT_VERBS,           # les effets d'un clip juste après lui, comme dans la documentation
    "remove_keyframe": ANIMATION_VERBS,         # les images clés de masque juste après celles du transform
    "clear_crop": MASK_VERBS + TRACKING_VERBS,    # le suivi et la stabilisation juste après les masques
    "remove_transition": TIME_VERBS + TRANSFORM_VERBS,   # vitesse et transform juste avant les images clés
    "update_track_insert": CLIP_AUDIO_VERBS,      # l'audio d'un clip après les inserts de piste
    "set_multicam_angle_offset": AUTOMATION_VERBS,   # rythme, plans, sous-titres, effets d'impact, modèles : en fin de table
    "set_graphic": LAYER_VERBS,                   # les calques graphiques juste après leur création et leur apparence
}


def _with_extra_verbs(table: dict[str, Verb]) -> dict[str, Verb]:
    """Les verbes des modules annexes (``mask_verbs``, ``animation_verbs``, ``effect_verbs``, ``edit_verbs``, ``time_verbs``, ``transform_verbs``, ``clip_audio_verbs``, ``layer_verbs``, ``guide_verbs``, ``tracking_verbs``) juste après leur verbe d'ancrage."""
    result: dict[str, Verb] = {}
    for name, verb in table.items():
        result[name] = verb
        result.update((item.name, item) for item in _EXTRA_VERBS.get(name, ()))
    return result


VERBS = _with_extra_verbs(VERBS)

__all__ = ["MAX_PARAMS", "MAX_TEXT", "MAX_TIME", "VERBS", "Kind", "Operation", "Param", "ScriptContext", "Verb"]
