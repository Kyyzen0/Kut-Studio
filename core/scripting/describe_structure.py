"""Ce que ``describe`` dit de l'organisation du projet (ADR-0005, étape 5, partie C) : bibliothèque, guides, flou de mouvement,
grille rythmique, groupes générés (classements) et source Multicam.

Mêmes règles que les autres modules ``describe_*.py`` : les clés ont le nom du champ du modèle, les durées sont en secondes, « aucun »
vaut ``null``, aucune fonction ne récurse, rien n'est modifié ni lu sur le disque. Les identifiants sont ceux que les verbes attendent
(``folder``, ``tag``, ``guide``, ``angle``, classement).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..leaderboard import KIND as LEADERBOARD_KIND, MAX_ROWS
from .describe_values import attr, enum, number, optional_text, text


def library(project: Any) -> dict[str, Any]:
    """Dossiers, étiquettes et affectations des médias (``Project.library_*``). Un média absent des affectations est à la racine, sans étiquette."""
    return {
        "folders": [
            {"id": text(item.id), "name": text(item.name), "parent_id": optional_text(item.parent_id), "color": text(item.color)}
            for item in project.library_folders
        ],
        "tags": [{"id": text(item.id), "name": text(item.name), "color": text(item.color)} for item in project.library_tags],
        "assignments": [
            {"asset_id": text(item.asset_id), "folder_id": optional_text(item.folder_id), "tag_ids": [text(tag) for tag in item.tag_ids]}
            for item in project.library_assignments.values()
        ],
    }


def guides(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [
        {"id": text(item.id), "orientation": enum(item.orientation), "position": number(item.position), "locked": bool(item.locked)}
        for item in items or ()
    ]


def motion_blur(item: Any) -> dict[str, Any]:
    return {
        "enabled": bool(attr(item, "enabled", True)), "shutter_angle": number(attr(item, "shutter_angle", 180.0)),
        "samples": number(attr(item, "samples", 8)),
    }


def beat_grid(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {"bpm": number(item.bpm), "offset": number(item.offset), "beats_per_bar": number(item.beats_per_bar)}


def _board(data: Any) -> dict[str, Any] | None:
    """Les données d'un classement (ce que ``update_leaderboard`` récrit) ; ``None`` si elles n'ont pas la forme que le modèle écrit."""
    if not isinstance(data, dict):
        return None
    rows = data.get("rows", ())
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS:
        return None
    if not all(isinstance(row, (list, tuple)) and len(row) >= 4 for row in rows):
        return None
    return {
        "rows": [{"rank": text(row[0]), "name": text(row[1]), "value": text(row[2]), "color": text(row[3])} for row in rows],
        "start": number(data.get("start", 0.0)), "duration": number(data.get("duration", 6.0)),
        "top": number(data.get("top", -0.105)), "delay": number(data.get("delay", 0.3)),
    }


def generated_groups(groups: Any) -> list[dict[str, Any]]:
    """Les calques générés à partir de données (``{id: {kind, data, clips}}`` dans le modèle) : une liste, dans l'ordre du modèle.

    ``data`` n'est écrit que pour un classement (``kind == "leaderboard"``) ; pour un autre genre il vaut ``null``."""
    if not isinstance(groups, dict):
        return []
    result = []
    for group_id, group in groups.items():
        kind = text(attr(group, "kind", ""))
        clips = attr(group, "clips", ())
        result.append({
            "id": text(group_id), "kind": kind,
            "data": _board(attr(group, "data", None)) if kind == LEADERBOARD_KIND else None,
            "clips": [text(clip_id) for clip_id in clips] if isinstance(clips, (list, tuple)) else [],
        })
    return result


def _offset(sequence: Any, track_id: str) -> float | None:
    """Décalage d'un angle : le début de ses premiers clips (le modèle ne le stocke nulle part ailleurs) ; ``null`` sans clip."""
    for track in sequence.tracks:
        if track.id == track_id:
            starts = [float(clip.timeline_start) for clip in track.clips]
            return number(min(starts)) if starts else None
    return None


def multicam(sequence: Any) -> dict[str, Any] | None:
    """La source Multicam d'une séquence (``null`` : séquence ordinaire). Les segments sont les clips imbriqués qui portent un ``angle_id``."""
    source = sequence.multicam
    if source is None:
        return None
    return {
        "angles": [
            {
                "id": text(angle.id), "name": text(angle.name), "track_id": text(angle.track_id),
                "color_index": number(angle.color_index), "sync_method": None if angle.sync_method is None else enum(angle.sync_method),
                "sync_status": enum(angle.sync_status), "sync_confidence": number(angle.sync_confidence),
                "offset": _offset(sequence, angle.track_id),
            }
            for angle in source.angles
        ],
        "audio": {"mode": enum(source.audio.mode), "angle_ids": [text(item) for item in source.audio.angle_ids]},
        "sync_method": None if source.sync_method is None else enum(source.sync_method),
    }


__all__ = ["beat_grid", "generated_groups", "guides", "library", "motion_blur", "multicam"]
