"""Bus audio : des sous-mixages de la séquence, entre les pistes et le Master.

Une piste peut être envoyée sur un **bus** (``Track.bus_id``) ; sans bus (chaîne vide), elle va directement au
Master, comme avant. Les pistes d'un même bus sont additionnées, traitées par les **inserts** du bus (les mêmes effets
que ceux d'un clip ou d'une piste), puis passent le **fader** du bus avant le Master : une réverbération commune, un
compresseur de « colle » sur toutes les voix, ou un seul réglage de volume pour un groupe de pistes.

Les bus appartiennent à la séquence (``Sequence.buses``) et vivent dans le ``.kut`` : ce ne sont pas des préférences.
Ce module ne dépend pas de Qt. Les opérations de projet agissent sur la séquence active et lèvent ``KeyError`` (bus
ou piste introuvable) ou ``ValueError`` (refus du modèle) sans rien modifier.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from .audio_effects_model import (
    AudioEffect,
    AudioEffectType,
    add_effect,
    create_audio_effect,
    effect_by_id,
    move_effect,
    remove_effect,
    set_effect_enabled,
    update_effect_parameters,
)
from .project_model import clamp_gain_db

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from .project_model import Project, Track

MAX_BUSES = 8
"""Nombre maximal de bus par séquence (chaque bus mesuré est un point de mesure du flux en lecture)."""
MAX_BUS_NAME_LENGTH = 40


@dataclass
class AudioBus:
    """Un bus : un nom, un fader, une coupure et une chaîne d'inserts."""

    id: str
    name: str
    volume_db: float = 0.0
    muted: bool = False
    inserts: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValueError("Un bus doit avoir un identifiant non vide.")
        self.name = _clean_name(self.name)
        self.volume_db = clamp_gain_db(self.volume_db)
        self.muted = bool(self.muted)
        self.inserts = list(self.inserts or [])


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("Le nom d'un bus ne peut pas être vide.")
    if len(name) > MAX_BUS_NAME_LENGTH:
        raise ValueError(f"Le nom d'un bus est trop long ({len(name)} > {MAX_BUS_NAME_LENGTH}).")
    return name


def new_bus_id() -> str:
    return f"bus-{uuid.uuid4().hex[:8]}"


def find_bus(project: "Project", bus_id: str) -> AudioBus:
    for bus in project.buses:
        if bus.id == bus_id:
            return bus
    raise KeyError(f"Bus introuvable : {bus_id!r}.")


def default_bus_name(project: "Project") -> str:
    """« Bus 1 », « Bus 2 »… : le premier numéro libre."""
    taken = {bus.name for bus in project.buses}
    number = 1
    while f"Bus {number}" in taken:
        number += 1
    return f"Bus {number}"


def add_bus(project: "Project", name: str | None = None, *, bus_id: str | None = None) -> AudioBus:
    """Ajoute un bus à la séquence active et le retourne."""
    if len(project.buses) >= MAX_BUSES:
        raise ValueError(f"Une séquence ne peut pas avoir plus de {MAX_BUSES} bus.")
    bus = AudioBus(id=bus_id or new_bus_id(), name=name if name is not None else default_bus_name(project))
    if any(existing.id == bus.id for existing in project.buses):
        raise ValueError(f"Le bus '{bus.id}' existe déjà.")
    project.buses = [*project.buses, bus]
    return bus


def remove_bus(project: "Project", bus_id: str) -> list[str]:
    """Supprime un bus ; ses pistes retournent au Master. Retourne les identifiants des pistes renvoyées au Master."""
    find_bus(project, bus_id)
    rerouted = []
    for track in project.tracks:
        if track.bus_id == bus_id:
            track.bus_id = ""
            rerouted.append(track.id)
    project.buses = [bus for bus in project.buses if bus.id != bus_id]
    return rerouted


def rename_bus(project: "Project", bus_id: str, name: str) -> AudioBus:
    bus = find_bus(project, bus_id)
    bus.name = _clean_name(name)
    return bus


def set_bus_volume(project: "Project", bus_id: str, volume_db: float) -> AudioBus:
    bus = find_bus(project, bus_id)
    bus.volume_db = clamp_gain_db(volume_db)
    return bus


def set_bus_muted(project: "Project", bus_id: str, muted: bool) -> AudioBus:
    bus = find_bus(project, bus_id)
    bus.muted = bool(muted)
    return bus


def route_track(project: "Project", track_id: str, bus_id: str) -> "Track":
    """Envoie une piste sur un bus (``bus_id`` vide : directement au Master)."""
    track = next((t for t in project.tracks if t.id == track_id), None)
    if track is None:
        raise KeyError(f"Piste introuvable : {track_id!r}.")
    if track.type == "subtitle":
        raise ValueError(f"La piste '{track_id}' est une piste 'subtitle' : elle n'a pas de son à router.")
    if track.locked:
        raise ValueError(f"La piste '{track_id}' est verrouillée.")
    if bus_id:
        find_bus(project, bus_id)
    track.bus_id = bus_id
    return track


def tracks_on_bus(project: "Project", bus_id: str) -> list["Track"]:
    return [track for track in project.tracks if track.bus_id == bus_id]


# --- Inserts d'un bus : les mêmes opérations pures que pour une piste -------------------------------------------------


def _insert_by_id(bus: AudioBus, effect_id: str) -> AudioEffect:
    effect = effect_by_id(bus.inserts, effect_id)
    if effect is None:
        raise KeyError(f"Insert audio introuvable : {effect_id!r}.")
    return effect


def add_bus_insert(
    project: "Project", bus_id: str, effect_type: AudioEffectType | str, *,
    effect_id: str | None = None, params: Mapping[str, Any] | None = None, enabled: bool = True,
) -> AudioEffect:
    bus = find_bus(project, bus_id)
    effect = create_audio_effect(effect_type, effect_id=effect_id, enabled=enabled, params=params)
    bus.inserts = add_effect(bus.inserts, effect)
    return effect


def remove_bus_insert(project: "Project", bus_id: str, effect_id: str) -> AudioEffect:
    bus = find_bus(project, bus_id)
    removed = _insert_by_id(bus, effect_id)
    bus.inserts = remove_effect(bus.inserts, effect_id)
    return removed


def set_bus_insert_enabled(project: "Project", bus_id: str, effect_id: str, enabled: bool) -> AudioEffect:
    bus = find_bus(project, bus_id)
    bus.inserts = set_effect_enabled(bus.inserts, effect_id, enabled)
    return _insert_by_id(bus, effect_id)


def update_bus_insert_parameters(
    project: "Project", bus_id: str, effect_id: str, params: Mapping[str, Any]
) -> AudioEffect:
    bus = find_bus(project, bus_id)
    bus.inserts = update_effect_parameters(bus.inserts, effect_id, params)
    return _insert_by_id(bus, effect_id)


def move_bus_insert(project: "Project", bus_id: str, effect_id: str, delta: int) -> AudioEffect:
    bus = find_bus(project, bus_id)
    bus.inserts = move_effect(bus.inserts, effect_id, delta)
    return _insert_by_id(bus, effect_id)


__all__ = [
    "MAX_BUSES",
    "AudioBus",
    "add_bus",
    "add_bus_insert",
    "default_bus_name",
    "find_bus",
    "move_bus_insert",
    "new_bus_id",
    "remove_bus",
    "remove_bus_insert",
    "rename_bus",
    "route_track",
    "set_bus_insert_enabled",
    "set_bus_muted",
    "set_bus_volume",
    "tracks_on_bus",
    "update_bus_insert_parameters",
]
