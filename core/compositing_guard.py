"""Garde commune des modifications du groupe Compositing (masques, fusion, incrustation).

Une seule règle pour tous les chemins d'entrée (inspecteur, et plus tard menu et verbe de script) : on ne touche pas au
compositing d'un clip sur une piste verrouillée, d'un calque graphique verrouillé, ni d'un clip qui n'est pas visuel
(audio, sous-titres). Sans Qt ; l'interface traduit le code renvoyé.
"""
from __future__ import annotations

from core.mograph_layers import LayerError, _require_editable
from core.project_model import Project
from core.timeline_operations import find_clip

REFUSAL_MISSING = "missing"
REFUSAL_TRACK_LOCKED = "track_locked"
REFUSAL_LAYER_LOCKED = "layer_locked"
REFUSAL_NOT_VISUAL = "not_visual"

VISUAL_TRACK_TYPES = ("video", "graphics")


def compositing_edit_refusal(project: Project, clip_id: str) -> str | None:
    """Code du refus (``REFUSAL_*``) si le compositing du clip n'est pas modifiable, sinon ``None``."""
    try:
        clip = find_clip(project, clip_id)
    except KeyError:
        return REFUSAL_MISSING
    track = next((t for t in project.tracks if any(c.id == clip_id for c in t.clips)), None)
    if track is None:
        return REFUSAL_MISSING
    if track.type not in VISUAL_TRACK_TYPES:
        return REFUSAL_NOT_VISUAL
    if track.locked:
        return REFUSAL_TRACK_LOCKED
    if track.type == "graphics":
        try:
            _require_editable(project, clip_id)
        except LayerError:
            return REFUSAL_LAYER_LOCKED
    elif bool(getattr(getattr(clip, "graphic", None), "locked", False)):
        return REFUSAL_LAYER_LOCKED
    return None


__all__ = [
    "REFUSAL_LAYER_LOCKED", "REFUSAL_MISSING", "REFUSAL_NOT_VISUAL", "REFUSAL_TRACK_LOCKED",
    "VISUAL_TRACK_TYPES", "compositing_edit_refusal",
]
