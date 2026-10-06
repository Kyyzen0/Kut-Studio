"""Emplacements de média d'un template : un clip vidéo qui attend son plan.

Un emplacement est un clip ordinaire de piste vidéo (timing calé sur la grille, zoom d'impact, effets) dont
``template_slot`` est renseigné. Tant que son média manque, le plan de rendu le dessine comme une **carte
d'emplacement** — un calque de texte motion graphics (numéro du plan sur un panneau sombre, liseré néon) — au lieu de
le signaler comme média introuvable : l'aperçu, l'export et le moniteur GPU montrent la même carte, par le même
rastériseur. Y déposer un média le remplit en gardant tout le reste.
"""

from __future__ import annotations

from dataclasses import replace

from .graphics import GraphicOverlay, GraphicType
from .project_model import Clip, Project
from .timeline_operations import _ensure_track_editable, _find_track_for_clip

SLOT_INK = "#16294AF0"
SLOT_BLUE = "#22B8FF"
SLOT_FONT = "Anton"


class SlotError(ValueError):
    """Le média ne peut pas remplir cet emplacement."""


def slot_label(clip: Clip) -> str:
    """Ce qu'affiche la carte : le libellé du clip (« 03 »), sinon le numéro de l'emplacement."""
    if clip.label:
        return clip.label
    return clip.template_slot.rsplit("-", 1)[-1] or clip.template_slot


def slot_card(clip: Clip, width: int, height: int) -> GraphicOverlay:
    """Calque de la carte d'un emplacement vide, à la taille du cadre (même boîte qu'une vidéo plein cadre)."""
    side = min(width, height)
    return GraphicOverlay(
        type=GraphicType.TEXT, text=slot_label(clip), width=int(width), height=int(height),
        font_family=SLOT_FONT, font_size=max(12, round(side * 0.16)), fill_color=SLOT_BLUE,
        stroke_width=0, shadow_color="#00000000", shadow_offset_x=0, shadow_offset_y=0,
        background_enabled=True, background_color=SLOT_INK, background_padding=max(4, round(side * 0.09)),
        background_radius=float(round(side * 0.05)), glow_color=SLOT_BLUE, glow_radius=float(round(side * 0.02)),
        glow_strength=0.8,
    )


def is_empty_slot(clip: Clip, asset_ids) -> bool:
    return bool(clip.template_slot) and not clip.sequence_id and clip.asset_id not in asset_ids


def empty_slots(project: Project) -> list[Clip]:
    """Emplacements encore vides de la séquence active, dans l'ordre de la timeline."""
    assets = {asset.id for asset in project.media_assets}
    clips = [clip for track in project.tracks if track.type == "video" for clip in track.clips
             if is_empty_slot(clip, assets)]
    return sorted(clips, key=lambda clip: (clip.timeline_start, clip.track_id))


def slot_at(project: Project, track_id: str, seconds: float) -> Clip | None:
    """L'emplacement de la piste sous ``seconds`` (dépôt d'un média sur la timeline)."""
    for track in project.tracks:
        if track.id != track_id:
            continue
        for clip in track.clips:
            if clip.template_slot and clip.timeline_start <= seconds < clip.timeline_start + clip.duration:
                return clip
    return None


def fill_slot(project: Project, clip_id: str, asset_id: str) -> Clip:
    """Met le média ``asset_id`` dans l'emplacement : même place, même durée (si le média est assez long), même
    animation, mêmes effets, cadrage « remplir ». Un média plus court raccourcit le plan plutôt que de le geler."""
    asset = next((item for item in project.media_assets if item.id == asset_id), None)
    if asset is None:
        raise KeyError(asset_id)
    if asset.media_type != "video" or not asset.width or not asset.height:
        raise SlotError("Un emplacement de template attend une vidéo.")
    clip = next((item for track in project.tracks for item in track.clips if item.id == clip_id), None)
    if clip is None or not clip.template_slot:
        raise KeyError(clip_id)
    track, _index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)              # une piste verrouillée protège aussi ses emplacements
    length = min(clip.duration, float(asset.duration) if asset.duration else clip.duration)
    clip.asset_id = asset.id
    clip.source_in, clip.source_out = 0.0, length
    clip.transform = replace(clip.transform, fill=True)
    return clip


__all__ = ["SlotError", "empty_slots", "fill_slot", "is_empty_slot", "slot_at", "slot_card", "slot_label"]
