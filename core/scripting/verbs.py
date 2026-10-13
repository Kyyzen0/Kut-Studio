"""Le catalogue des verbes du format déclaratif (ADR-0005, §3) : **une table unique** qui sert à valider un script, à le documenter et à
produire son schéma JSON. Un verbe sans entrée ici n'existe pas ; rien n'est exécuté qui ne soit une fonction de ``core/`` listée.

Un verbe est un nom, des paramètres typés et bornés, et une fonction ``build`` qui rend l'**opération** (``projet -> valeur``) que la
session appliquera. Les paramètres sont déjà validés quand ``build`` est appelée (types, bornes, identifiants sûrs, alias résolus) ;
les *sélecteurs* (« la piste A1 », « le bus voix ») sont résolus **à l'application**, sur le projet tel qu'il est à ce moment, puisqu'un
verbe précédent a pu créer la cible.

Sans Qt, sans accès disque : un script ne lit ni n'écrit rien d'autre que le projet qu'on lui donne.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..audio_buses import (
    add_bus, add_bus_insert, remove_bus, remove_bus_insert, rename_bus, route_track, set_bus_insert_enabled,
    set_bus_muted, set_bus_volume, update_bus_insert_parameters,
)
from ..audio_effects_model import AudioEffectType
from ..project_model import Project
from ..timeline_editing import add_marker
from ..timeline_operations import add_clip_to_track, delete_clip, move_clip, set_clip_enabled

Operation = Callable[[Project], Any]

MAX_TEXT = 200
"""Longueur maximale d'un texte (nom, sélecteur) : un script ne sert pas à transporter de la donnée."""
MAX_PARAMS = 32
MAX_TIME = 1_000_000.0
"""Secondes : environ onze jours, bien au-delà de tout montage, et assez bas pour que les calculs de durée restent exacts."""


class Kind(str, Enum):
    STRING = "string"
    NUMBER = "number"
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


@dataclass(frozen=True)
class Verb:
    """``creates`` : le genre d'objet créé (``bus``, ``clip``, ``insert``, ``marker``), ``None`` pour un verbe qui modifie seulement ;
    un verbe créateur accepte alors ``id`` (identifiant imposé) et ``as`` (alias : un nom du script, qui sert d'identifiant s'il n'y
    a pas d'``id``)."""

    name: str
    doc: str
    params: tuple[Param, ...]
    build: Callable[[Mapping[str, Any]], Operation]
    creates: str | None = None
    group: str = ""
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

VERBS: dict[str, Verb] = {
    verb.name: verb
    for verb in (
        Verb("add_bus", "Ajoute un bus audio à la séquence active.",
             (Param("name", Kind.STRING, "Nom du bus.", required=False),), _add_bus, creates="bus", group="audio"),
        Verb("remove_bus", "Supprime un bus ; ses pistes retournent au Master.", (_BUS,), _remove_bus, group="audio"),
        Verb("rename_bus", "Renomme un bus.", (_BUS, Param("name", Kind.STRING, "Nouveau nom.")), _rename_bus, group="audio"),
        Verb("set_bus_volume", "Règle le volume d'un bus.",
             (_BUS, Param("volume_db", Kind.NUMBER, "Volume en dB.", minimum=-120.0, maximum=24.0)), _set_bus_volume, group="audio"),
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
        Verb("add_marker", "Ajoute un repère sur la timeline.",
             (_AT, Param("name", Kind.STRING, "Nom du repère.", required=False),
              Param("category", Kind.STRING, "Catégorie (défaut : standard).", required=False)),
             _add_marker, creates="marker", group="edit"),
    )
}
"""Le catalogue. L'ordre est celui de la documentation."""

__all__ = ["MAX_PARAMS", "MAX_TEXT", "MAX_TIME", "VERBS", "Kind", "Operation", "Param", "Verb"]
