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
REFUSAL_WRONG_TRACK = "wrong_track"

VISUAL_TRACK_TYPES = ("video", "graphics")


def clip_edit_refusal(
    project: Project, clip_id: str, *, visual_only: bool = True, track_types: tuple[str, ...] | None = None,
) -> str | None:
    """Code du refus (``REFUSAL_*``) si le clip n'est pas modifiable, sinon ``None``.

    ``visual_only=False`` ne regarde que les verrous (piste, calque) : vitesse, audio et script visent aussi les clips
    audio, qui ne sont alors pas refusés pour leur genre. ``track_types`` restreint à des genres précis de piste
    (sous-titres pour un modèle de texte) : un autre genre donne ``REFUSAL_WRONG_TRACK``.
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
    if track_types is not None and track.type not in track_types:
        return REFUSAL_WRONG_TRACK
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


class ClipEditRefused(ValueError):
    """Une modification refusée par la règle commune (``code`` = ``REFUSAL_*``) : rien n'a été écrit."""

    def __init__(self, code: str, clip_id: str) -> None:
        self.code = code
        self.clip_id = clip_id
        super().__init__(_MESSAGES.get(code, code).format(clip=clip_id))


_MESSAGES = {
    REFUSAL_MISSING: "Le clip '{clip}' n'existe pas.",
    REFUSAL_TRACK_LOCKED: "Le clip '{clip}' est sur une piste verrouillée.",
    REFUSAL_LAYER_LOCKED: "Le calque '{clip}' est verrouillé.",
    REFUSAL_NOT_VISUAL: "Le clip '{clip}' n'est pas visuel.",
    REFUSAL_WRONG_TRACK: "Le clip '{clip}' est sur une piste d'un autre genre.",
}


def require_clip_editable(
    project: Project, clip_id: str, *, visual_only: bool = False, track_types: tuple[str, ...] | None = None,
    property_id: str | None = None,
) -> None:
    """Garde du cœur : lève :class:`ClipEditRefused` si ``clip_edit_refusal`` refuse (la même règle que l'interface).

    Un clip introuvable n'est pas refusé ici : l'appelant garde son ``KeyError`` d'origine. ``property_id`` ajoute le
    genre : une propriété animable qui ne s'applique pas au genre de la piste (opacité d'un clip audio…) est refusée.
    """
    code = clip_edit_refusal(project, clip_id, visual_only=visual_only, track_types=track_types)
    if code == REFUSAL_MISSING:
        return
    if code is None and property_id is not None:
        from .animation_targets import get_target

        track = next(t for t in project.tracks if any(c.id == clip_id for c in t.clips))
        if not get_target(property_id).applies_to(track.type):
            code = REFUSAL_WRONG_TRACK
    if code is not None:
        raise ClipEditRefused(code, clip_id)


def compositing_edit_refusal(project: Project, clip_id: str) -> str | None:
    """Refus du groupe Compositing : la règle commune, clip visuel obligatoire."""
    return clip_edit_refusal(project, clip_id)


__all__ = [
    "REFUSAL_LAYER_LOCKED", "REFUSAL_MISSING", "REFUSAL_NOT_VISUAL", "REFUSAL_TRACK_LOCKED", "REFUSAL_WRONG_TRACK",
    "VISUAL_TRACK_TYPES", "ClipEditRefused", "clip_edit_refusal", "compositing_edit_refusal", "require_clip_editable",
]
