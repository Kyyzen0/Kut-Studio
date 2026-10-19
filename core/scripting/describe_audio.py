"""L'audio avancé dans ``describe`` : inserts de piste, bus, automation de volume, ducking.

Les identifiants que les verbes attendent s'y trouvent : ``bus`` (``buses[].id``), ``insert`` (``inserts[].id``, d'une piste ou
d'un bus), ``track`` (``tracks[].id``). Un bus et un insert appartiennent à la séquence **active** pour les verbes (``add_bus``,
``route_track``…) : pour viser une autre séquence, ``set_active_sequence`` d'abord.
"""

from __future__ import annotations

from typing import Any

from . import describe_shapes as shapes
from .describe_values import attr, number, optional_text, text


def bus(item: Any) -> dict[str, Any]:
    return {
        "id": text(item.id), "name": text(item.name), "volume_db": number(item.volume_db), "muted": bool(item.muted),
        "inserts": shapes.effects(item.inserts),
    }


def buses(items: Any) -> list[dict[str, Any]]:
    return [bus(item) for item in items or ()]


def automation(item: Any) -> dict[str, Any]:
    """La courbe de volume d'une piste : points triés par temps (``time_seconds`` de la timeline, ``gain_db``, ``fade_seconds``)."""
    return {
        "points": [
            {"time_seconds": number(point.time_seconds), "gain_db": number(point.gain_db), "fade_seconds": number(point.fade_seconds)}
            for point in getattr(item, "points", ()) or ()
        ]
    }


def ducking_config(item: Any) -> dict[str, Any] | None:
    """Un réglage de ducking (objet, ou dictionnaire d'un ancien fichier) ; ``null`` : pas de réglage."""
    if item is None:
        return None
    return {
        "threshold_db": number(attr(item, "threshold_db", -20.0)), "reduction_db": number(attr(item, "reduction_db", 12.0)),
        "attack_seconds": number(attr(item, "attack_seconds", 0.05)), "release_seconds": number(attr(item, "release_seconds", 0.4)),
    }


def sidechains(items: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": text(item.id), "music_track_id": text(item.music_track_id), "voice_track_id": text(item.voice_track_id),
            "config": ducking_config(item.config), "enabled": bool(item.enabled),
        }
        for item in items or ()
    ]


def bus_id(track: Any) -> str | None:
    return optional_text(track.bus_id)
