"""Garde commune des modifications d'un clip depuis l'inspecteur, le moniteur, le menu et les scripts.

Une seule règle pour tous les chemins d'entrée : on ne modifie pas un clip sur une piste verrouillée, un calque graphique
verrouillé, ni (``visual_only``) un clip qui n'est pas visuel (audio, sous-titres). ``clip_edit_refusal`` est la règle ;
``compositing_edit_refusal`` en est l'alias du groupe Compositing. Sans Qt ; l'interface traduit le code renvoyé.
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


def clip_edit_refusal(project: Project, clip_id: str, *, visual_only: bool = True) -> str | None:
    """Code du refus (``REFUSAL_*``) si le clip n'est pas modifiable, sinon ``None``.

    ``visual_only=False`` ne regarde que les verrous (piste, calque) : vitesse, audio et script visent aussi les clips
    audio, qui ne sont alors pas refusés pour leur genre.
    """
    try:
        clip = find_clip(project, clip_id)
    except KeyError:
        return REFUSAL_MISSING
    track = next((t for t in project.tracks if any(c.id == clip_id for c in t.clips)), None)
    if track is None:
        return REFUSAL_MISSING
    if visual_only and track.type not in VISUAL_TRACK_TYPES:
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


def compositing_edit_refusal(project: Project, clip_id: str) -> str | None:
    """Refus du groupe Compositing : la règle commune, clip visuel obligatoire."""
    return clip_edit_refusal(project, clip_id)


__all__ = [
    "REFUSAL_LAYER_LOCKED", "REFUSAL_MISSING", "REFUSAL_NOT_VISUAL", "REFUSAL_TRACK_LOCKED",
    "VISUAL_TRACK_TYPES", "clip_edit_refusal", "compositing_edit_refusal",
]
