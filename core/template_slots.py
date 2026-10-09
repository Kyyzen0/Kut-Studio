"""Emplacements de média d'un template : un clip vidéo qui attend son plan.

Un emplacement est un clip ordinaire de piste vidéo (timing calé sur la grille, zoom d'impact, effets) dont
``template_slot`` est renseigné. Tant que son média manque, le plan de rendu le dessine comme une **carte
d'emplacement** — un calque de texte motion graphics (numéro du plan sur un panneau sombre, liseré néon) — au lieu de
le signaler comme média introuvable : l'aperçu, l'export et le moniteur GPU montrent la même carte, par le même
rastériseur. Y déposer un média le remplit en gardant tout le reste.

Une **photo** remplit aussi un emplacement (:func:`fill_slot_with_photo`) : le clip garde sa place, sa durée, ses effets
et son étalonnage, et porte un calque image (``clip.graphic``) que le plan de rendu dessine **à la place de la carte**,
par le même rastériseur que les photos importées : un Ken Burns y glisse au sous-pixel (pas d'escalier d'échelle
entière comme avec ``scale`` d'FFmpeg), et le calque d'effets « Night Look » posé au-dessus s'y applique comme à une
vidéo. Une vidéo déposée ensuite sur l'emplacement reprend la place de la photo.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from .graphics import GraphicOverlay, GraphicType, graphic_defaults
from .ken_burns import apply_ken_burns, photo_size
from .project_model import Clip, MediaAsset, Project
from .timeline_operations import _ensure_track_editable, _find_track_for_clip

PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
"""Images qu'un emplacement accepte (celles que lit le rastériseur Qt, comme « Importer une image »)."""

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


def is_photo_path(path: str | Path) -> bool:
    """Le fichier est-il une image qu'un emplacement accepte (d'après son extension) ?"""
    return Path(str(path)).suffix.lower() in PHOTO_EXTENSIONS


def is_photo_slot(clip: Clip) -> bool:
    """Emplacement rempli par une photo : son calque image remplace la vidéo au rendu."""
    graphic = getattr(clip, "graphic", None)
    return bool(clip.template_slot) and isinstance(graphic, GraphicOverlay) and graphic.type == GraphicType.IMAGE


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
        raise SlotError("Un emplacement de template attend une vidéo ou une photo.")
    clip = next((item for track in project.tracks for item in track.clips if item.id == clip_id), None)
    if clip is None or not clip.template_slot:
        raise KeyError(clip_id)
    track, _index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)              # une piste verrouillée protège aussi ses emplacements
    length = min(clip.duration, float(asset.duration) if asset.duration else clip.duration)
    previous = clip.asset_id
    clip.asset_id = asset.id
    clip.source_in, clip.source_out = 0.0, length
    clip.transform = replace(clip.transform, fill=True)
    clip.graphic = None                                 # une photo qui occupait l'emplacement cède la place
    _drop_unused_photo_asset(project, previous)
    return clip


def fill_slot_with_photo(
    project: Project, clip_id: str, path: str | Path, image_size: tuple[int, int], *, ken_burns: bool = True,
) -> Clip:
    """Met la photo ``path`` dans l'emplacement : même place, même durée (une photo n'a pas de fin), mêmes effets.

    ``image_size`` : taille de la photo en pixels (lue par l'appelant : le modèle ne dépend pas de Qt). Le calque
    **remplit** le cadre de la séquence (l'excédent sort du cadre, comme une vidéo en « remplir ») ; ``ken_burns`` y
    pose un mouvement lent (:mod:`core.ken_burns`), qui remplace l'animation d'échelle et de position du clip (le zoom
    d'impact d'un template) : une photo immobile paraît figée là où une vidéo bougeait.
    """
    clip = next((item for track in project.tracks for item in track.clips if item.id == clip_id), None)
    if clip is None or not clip.template_slot:
        raise KeyError(clip_id)
    track, _index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    source = Path(str(path)).expanduser()
    if not is_photo_path(source):
        raise SlotError("Un emplacement accepte une vidéo ou une photo.")
    if not source.is_file():
        raise FileNotFoundError(f"Photo introuvable : {source}")
    image_width, image_height = (int(value) for value in image_size)
    if image_width <= 0 or image_height <= 0:
        raise SlotError("Photo illisible.")
    width, height = photo_size(image_width, image_height, int(project.width), int(project.height), fill=True)
    resolved = str(source.resolve())
    graphic = replace(
        graphic_defaults(GraphicType.IMAGE, project_width=int(project.width), project_height=int(project.height),
                         source_path=resolved),
        width=width, height=height,
    )
    asset = MediaAsset(
        id=f"graphic-asset-{uuid.uuid4().hex[:12]}", path=resolved, name=source.stem or "Photo",
        duration=clip.duration, width=width, height=height, fps=float(project.fps), media_type="graphic",
    )
    previous = clip.asset_id
    project.media_assets.append(asset)
    clip.asset_id = asset.id
    clip.graphic = graphic
    clip.source_in, clip.source_out = 0.0, clip.duration
    _drop_unused_photo_asset(project, previous)
    if ken_burns:
        apply_ken_burns([clip])
    return clip


def fill_slots_with_photos(
    project: Project, photos: Iterable[tuple[str | Path, tuple[int, int]]], *, start_clip_id: str | None = None,
    ken_burns: bool = True,
) -> list[Clip]:
    """Une photo par emplacement, dans l'ordre de la timeline : à partir de ``start_clip_id`` (l'emplacement où l'on
    dépose, rempli ou non, puis les emplacements **vides** qui le suivent), sinon dans les emplacements vides.

    Les photos en trop sont ignorées ; retourne les emplacements remplis (une photo illisible est sautée, pas l'
    emplacement)."""
    if start_clip_id is None:
        targets = empty_slots(project)
    else:
        start = next((clip for track in project.tracks for clip in track.clips if clip.id == start_clip_id), None)
        if start is None or not start.template_slot:
            raise KeyError(start_clip_id)
        key = (start.timeline_start, start.track_id)
        targets = [start, *(clip for clip in empty_slots(project)
                            if clip.id != start.id and (clip.timeline_start, clip.track_id) > key)]
    filled: list[Clip] = []
    pending = iter(targets)
    target = next(pending, None)
    for path, size in photos:
        if target is None:
            break
        try:
            filled.append(fill_slot_with_photo(project, target.id, path, size, ken_burns=ken_burns))
        except (SlotError, FileNotFoundError):
            continue
        target = next(pending, None)
    return filled


def slideshow_cuts(
    count: int, *, start: float, grid=None, seconds: float = 3.0, beats_per_photo: int | None = None,
) -> list[tuple[float, float]]:
    """Plans bord à bord d'un diaporama de ``count`` photos à partir de ``start``.

    Avec une grille rythmique (:class:`core.beat_grid.BeatGrid`), le premier plan part du premier temps à ``start`` ou
    après, et chaque photo dure ``beats_per_photo`` temps (une mesure par défaut) : les photos changent sur la musique.
    Sans grille, chaque photo dure ``seconds``.
    """
    count = max(0, int(count))
    if count == 0:
        return []
    start = max(0.0, float(start))
    if grid is not None:
        first = grid.beat_times(start, start + 2.0 * grid.bar)[0]
        step = grid.beat * max(1, int(beats_per_photo or grid.beats_per_bar))
    else:
        first, step = start, max(0.1, float(seconds))
    return [(round(first + index * step, 6), round(first + (index + 1) * step, 6)) for index in range(count)]


def add_photo_slideshow(
    project: Project, photos: list[tuple[str | Path, tuple[int, int]]], *, start: float, grid=None,
    seconds: float = 3.0, beats_per_photo: int | None = None, ken_burns: bool = True,
) -> list[Clip]:
    """« Diaporama photo » : un emplacement par photo, bord à bord (sur les temps de ``grid`` s'il y en a), rempli par
    sa photo avec un Ken Burns (le sens alterne d'une photo à l'autre).

    Les plans vont sur la première piste vidéo libre sur toute la durée (une nouvelle sinon) : rien n'est posé
    par-dessus un clip. Ce sont des emplacements ordinaires : on y dépose ensuite une autre photo ou une vidéo, et une
    photo illisible laisse sa carte numérotée à remplir.
    """
    from .track_operations import free_track
    from .visual_effects import ClipTransform

    cuts = slideshow_cuts(len(photos), start=start, grid=grid, seconds=seconds, beats_per_photo=beats_per_photo)
    if not cuts:
        return []
    track = free_track(project, "video", cuts[0][0], cuts[-1][1])
    token = uuid.uuid4().hex[:6]
    clips: list[Clip] = []
    for index, (begin, end) in enumerate(cuts, start=1):
        slot = f"photo-{token}-{index:02d}"
        clip = Clip(id=slot, asset_id="", track_id=track.id, timeline_start=begin, source_in=0.0,
                    source_out=end - begin, label=f"{index:02d}", template_slot=slot,
                    transform=ClipTransform(fill=True))
        track.clips.append(clip)
        clips.append(clip)
    track.clips.sort(key=lambda item: (item.timeline_start, item.id))
    for clip, (path, size) in zip(clips, photos):
        try:
            fill_slot_with_photo(project, clip.id, path, size, ken_burns=ken_burns)
        except (SlotError, FileNotFoundError):
            continue                                    # la carte numérotée reste : un emplacement à remplir
    return clips


def _drop_unused_photo_asset(project: Project, asset_id: str) -> None:
    """Retire le média technique d'une photo remplacée, s'il ne sert plus à aucun clip (jamais un média importé)."""
    asset = next((item for item in project.media_assets if item.id == asset_id), None)
    if asset is None or asset.media_type != "graphic":
        return
    used = any(clip.asset_id == asset_id for sequence in project.sequences for track in sequence.tracks
               for clip in track.clips)
    if not used:
        project.media_assets.remove(asset)


__all__ = [
    "PHOTO_EXTENSIONS", "SlotError", "add_photo_slideshow", "empty_slots", "fill_slot", "fill_slot_with_photo",
    "fill_slots_with_photos", "is_empty_slot", "is_photo_path", "is_photo_slot", "slideshow_cuts", "slot_at",
    "slot_card", "slot_label",
]
