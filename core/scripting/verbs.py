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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..audio_buses import (
    add_bus, add_bus_insert, remove_bus, remove_bus_insert, rename_bus, route_track, set_bus_insert_enabled,
    set_bus_muted, set_bus_volume, update_bus_insert_parameters,
)
from ..animation import InterpolationType
from ..animation_targets import get_target
from ..audio_effects_model import (
    AudioEffectType, add_track_insert, remove_track_insert, set_track_insert_enabled, update_track_insert_parameters,
)
from ..color_grading import (
    CONTRAST_MAX, CONTRAST_MIN, EXPOSURE_MAX, EXPOSURE_MIN, HIGHLIGHTS_MAX, HIGHLIGHTS_MIN, HUE_MAX, HUE_MIN, SATURATION_MAX,
    SATURATION_MIN, SHADOWS_MAX, SHADOWS_MIN, TEMPERATURE_MAX, TEMPERATURE_MIN, ColorGradingService,
)
from ..graphics import GraphicType, ShapeKind, add_graphic_clip, update_graphic
from ..keyframe_editing import add_keyframe, clip_duration, remove_keyframe_at
from ..media_probe import MediaProbeError, probe_media
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import MAX_GAIN_DB, MIN_GAIN_DB, Project
from ..timeline_editing import add_marker
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
    multiline: bool = False        # texte à plusieurs lignes (un titre) : « \n » permis


@dataclass(frozen=True)
class Verb:
    """``creates`` : le genre d'objet créé (``bus``, ``clip``, ``insert``, ``marker``), ``None`` pour un verbe qui modifie seulement ;
    un verbe créateur accepte alors ``id`` (identifiant imposé) et ``as`` (alias : un nom du script, qui sert d'identifiant s'il n'y
    a pas d'``id``)."""

    name: str
    doc: str
    params: tuple[Param, ...]
    build: Callable[..., Operation]
    creates: str | None = None
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


def _add_graphic(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = next(item for item in project.tracks if item.id == _track_id(project, a["track"])) if a.get("track") is not None else None
        if track is not None and track.type != "graphics":
            raise ValueError(f"La piste '{track.id}' est une piste '{track.type}' : un calque graphique va sur une piste de graphiques.")
        if track is not None and track.locked:
            raise ValueError(f"La piste '{track.id}' est verrouillée.")
        clip = add_graphic_clip(
            project, a["graphic"], timeline_start=float(a["at"]), duration=float(a["duration"]),
            shape=a.get("shape", ShapeKind.RECTANGLE.value), track=track, clip_id=a.get("id"),
        )
        if a.get("text") is not None:
            update_graphic(clip, "text", a["text"])
        return clip

    return operation


_GRAPHIC_FIELDS = ("text", "fill_color", "stroke_color", "stroke_width", "font_size", "width", "height")


def _set_graphic(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _GRAPHIC_FIELDS if name in a]
        if not given:
            raise ValueError("set_graphic : donnez au moins un champ à modifier.")
        clip = find_clip(project, _editable_clip(project, a["clip"]))
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


_ANIMATABLE = ("position_x", "position_y", "scale", "rotation", "opacity", "anchor_x", "anchor_y", "scale_x", "scale_y", "skew")


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


def _set_color_grade(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        given = [name for name in _GRADE_FIELDS if name in a]
        if not given and "enabled" not in a:
            raise ValueError("set_color_grade : donnez au moins un champ à modifier.")
        clip_id = _editable_clip(project, a["clip"], track_types=("video", "graphics"))
        service = ColorGradingService()
        grade = service.get_grade(project, clip_id)
        for name in given:
            grade = grade.with_field(name, float(a[name]))
        if "enabled" in a:
            grade = grade.with_enabled(bool(a["enabled"]))
        return service.set_grade(project, clip_id, grade)

    return operation


def _reset_color_grade(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        return ColorGradingService().reset_grade(project, _editable_clip(project, a["clip"], track_types=("video", "graphics")))

    return operation


def _add_marker(a: Mapping[str, Any]) -> Operation:
    return lambda p: add_marker(p, float(a["at"]), a.get("name", ""), a.get("category", "standard"), marker_id=a.get("id"))


_TRACK = Param("track", Kind.SELECTOR, "La piste : son identifiant ou son nom exact (unique).", of="track")
_BUS = Param("bus", Kind.SELECTOR, "Le bus : son identifiant ou son nom exact (unique).", of="bus")
_CLIP = Param("clip", Kind.SELECTOR, "Le clip, par son identifiant.", of="clip")
_INSERT = Param("insert", Kind.SELECTOR, "L'insert du bus, par son identifiant.", of="insert")
_AT = Param("at", Kind.NUMBER, "Position sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME)
_ENABLED = Param("enabled", Kind.BOOLEAN, "Actif ou non.")
_PARAMS = Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres, booléens, textes courts) ; ceux du modèle les valident.")
_EFFECTS = tuple(member.value for member in AudioEffectType)
_DISPLAYABLE = tuple(item.value for item in (
    GraphicType.TEXT, GraphicType.RECTANGLE, GraphicType.SOLID, GraphicType.SHAPE, GraphicType.ADJUSTMENT, GraphicType.NULL,
))
_COLOR_DOC = "Couleur « #RRGGBB » ou « #RRGGBBAA »."

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
              Param("track", Kind.SELECTOR, "Piste de graphiques visée ; absente, la première éditable.", required=False, of="track")),
             _add_graphic, creates="clip", group="graphics"),
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
        Verb("set_color_grade", "Règle l'étalonnage du clip (premier nœud) ; au moins un champ.",
             (_CLIP,
              Param("exposure", Kind.NUMBER, "Exposition, en diaphragmes.", required=False, minimum=EXPOSURE_MIN, maximum=EXPOSURE_MAX),
              Param("contrast", Kind.NUMBER, "Contraste.", required=False, minimum=CONTRAST_MIN, maximum=CONTRAST_MAX),
              Param("saturation", Kind.NUMBER, "Saturation (1 = neutre).", required=False, minimum=SATURATION_MIN, maximum=SATURATION_MAX),
              Param("temperature", Kind.NUMBER, "Température.", required=False, minimum=TEMPERATURE_MIN, maximum=TEMPERATURE_MAX),
              Param("hue", Kind.NUMBER, "Teinte, en degrés.", required=False, minimum=HUE_MIN, maximum=HUE_MAX),
              Param("shadows", Kind.NUMBER, "Ombres.", required=False, minimum=SHADOWS_MIN, maximum=SHADOWS_MAX),
              Param("highlights", Kind.NUMBER, "Hautes lumières.", required=False, minimum=HIGHLIGHTS_MIN, maximum=HIGHLIGHTS_MAX),
              Param("enabled", Kind.BOOLEAN, "Étalonnage actif ou neutralisé.", required=False)),
             _set_color_grade, group="color"),
        Verb("reset_color_grade", "Remet l'étalonnage du clip (premier nœud) à neutre.", (_CLIP,), _reset_color_grade, group="color"),
        Verb("add_marker", "Ajoute un repère sur la timeline.",
             (_AT, Param("name", Kind.STRING, "Nom du repère.", required=False),
              Param("category", Kind.STRING, "Catégorie (défaut : standard).", required=False)),
             _add_marker, creates="marker", group="edit"),
    )
}
"""Le catalogue. L'ordre est celui de la documentation."""

__all__ = ["MAX_PARAMS", "MAX_TEXT", "MAX_TIME", "VERBS", "Kind", "Operation", "Param", "ScriptContext", "Verb"]
