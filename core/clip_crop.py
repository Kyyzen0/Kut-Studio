"""Recadrage non destructif d'un clip : un masque rectangle généré (ADR-0006, étape 2).

Il n'y a **pas** de champ ``crop`` dans ``ClipTransform`` ni de chemin de rendu dédié : le recadrage est un
:class:`~core.compositing.Mask` rectangle que la pile de masques du clip porte déjà. Sa matte est rastérisée par Qt
(:func:`core.mograph_raster.render_matte`) puis consommée à l'identique par l'export FFmpeg et par l'aperçu GPU, et le
fichier de projet ne change pas.

Convention (prouvée par ``tests/test_clip_crop.py``) : les quatre valeurs sont des **fractions du cadre du calque**
(0 = rien de coupé). Le masque est centré sur ``(position_x, position_y)`` — fractions de la boîte du calque, origine en
haut à gauche — et mesure ``width × height`` (fractions de la même boîte) :
``width = 1 - gauche - droite``, ``position_x = gauche + width / 2`` (idem en y avec haut / bas).

Le masque est **intersect**, rangé **en dernier** dans la pile. La pile se lit dans l'ordre ; un masque ``add`` placé
après le recadrage s'y ajouterait au lieu d'être restreint, alors qu'un ``intersect`` final ne garde que ce qui tombe
dans le cadre, quels que soient les masques ``add`` / ``subtract`` qui le précèdent. Seul sur le clip, il part d'un
calque plein (règle de ``render_matte`` pour un premier masque non ``add``) et ne laisse que le rectangle.

Un seul masque de recadrage par clip, identifié par :data:`CROP_MASK_ID` (un identifiant réservé : les identifiants
générés sont hexadécimaux, ``"crop"`` n'en est pas un, donc aucune collision ; celui de la stabilisation est
``"trackcrop"``). Les autres masques ne sont jamais touchés.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from .compositing import Compositing, Mask, MaskMode, MaskShape, mask_property_id

CROP_MASK_ID = "crop"
"""Identifiant réservé du masque de recadrage."""

CROP_MASK_NAME = "Recadrage"

MAX_CROP_SIDE = 0.95
"""Au plus 95 % du cadre coupé d'un côté."""

MIN_VISIBLE = 0.05
"""Largeur (et hauteur) minimale conservée : ``gauche + droite <= 0,95``. ``Mask`` accepte une largeur nulle (borne basse
du modèle) mais un calque entièrement coupé n'est plus un recadrage ; 5 % reste visible et cliquable."""

CROP_TRACK_TYPES = ("video", "graphics")
"""Pistes dont les clips rendent des masques : vidéo, image fixe, composition et séquence imbriquée / multicam (pistes
vidéo) ; calques graphiques. Audio et sous-titres n'ont pas d'image à recadrer."""

_EPS = 1e-9


class CropError(ValueError):
    """Recadrage refusé (valeur hors bornes, clip inconnu, piste verrouillée, type de clip) : jamais corrigé en silence."""


@dataclass(frozen=True)
class Crop:
    """Les quatre bords coupés, en fractions du cadre du calque (``0 <= valeur <= 0,95``)."""

    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    def __post_init__(self) -> None:
        for name in ("left", "top", "right", "bottom"):
            value = getattr(self, name)
            try:
                number = float(value)
            except (TypeError, ValueError) as error:
                raise CropError(f"Recadrage : {name} doit être un nombre.") from error
            if not math.isfinite(number) or not 0.0 <= number <= MAX_CROP_SIDE:
                raise CropError(f"Recadrage : {name} = {value!r} est hors bornes (de 0 à {MAX_CROP_SIDE}).")
            object.__setattr__(self, name, number)
        if self.left + self.right > 1.0 - MIN_VISIBLE + _EPS:
            raise CropError(f"Recadrage : gauche + droite = {self.left + self.right:.4g} laisse moins de {MIN_VISIBLE:.0%} de largeur.")
        if self.top + self.bottom > 1.0 - MIN_VISIBLE + _EPS:
            raise CropError(f"Recadrage : haut + bas = {self.top + self.bottom:.4g} laisse moins de {MIN_VISIBLE:.0%} de hauteur.")

    @property
    def is_empty(self) -> bool:
        return not (self.left or self.top or self.right or self.bottom)


def crop_to_mask(crop: Crop) -> Mask:
    """Le masque rectangle ``intersect`` qui ne garde que l'intérieur du recadrage."""
    width = 1.0 - crop.left - crop.right
    height = 1.0 - crop.top - crop.bottom
    return Mask(
        shape=MaskShape.RECTANGLE, mode=MaskMode.INTERSECT, name=CROP_MASK_NAME, id=CROP_MASK_ID,
        position_x=crop.left + width / 2.0, position_y=crop.top + height / 2.0, width=width, height=height,
    )


def mask_to_crop(mask: Mask) -> Crop | None:
    """Le recadrage que ce masque représente, ou ``None`` s'il n'en est pas un (tourné, adouci, inversé, hors cadre…)."""
    if (
        mask.shape is not MaskShape.RECTANGLE or mask.mode is not MaskMode.INTERSECT or mask.inverted
        or mask.rotation or mask.feather or mask.expansion or mask.opacity != 1.0
    ):
        return None
    left = mask.position_x - mask.width / 2.0
    top = mask.position_y - mask.height / 2.0
    right = 1.0 - mask.position_x - mask.width / 2.0
    bottom = 1.0 - mask.position_y - mask.height / 2.0
    values = []
    for value in (left, top, right, bottom):
        if value < -_EPS:
            return None
        values.append(round(min(max(value, 0.0), MAX_CROP_SIDE), 9))        # efface le bruit de l'aller-retour flottant
    try:
        return Crop(*values)
    except CropError:
        return None


def crop_mask(compositing: Compositing | None) -> Mask | None:
    """Le masque de recadrage de la pile, s'il y en a un."""
    return compositing.mask_by_id(CROP_MASK_ID) if compositing is not None else None


def crop_of_compositing(compositing: Compositing | None) -> Crop | None:
    """Valeur courante du recadrage d'une pile (``None`` : aucun, ou masque modifié à la main hors des quatre bords)."""
    mask = crop_mask(compositing)
    return mask_to_crop(mask) if mask is not None else None


def is_custom_crop(compositing: Compositing | None) -> bool:
    """Vrai si la pile porte un masque « crop » que les quatre champs ne savent pas représenter."""
    mask = crop_mask(compositing)
    return mask is not None and mask_to_crop(mask) is None


def with_crop(compositing: Compositing | None, crop: Crop | None) -> Compositing:
    """Pile avec le recadrage posé (rangé en dernier) ou retiré (``None`` ou recadrage nul). Les autres masques sont intacts."""
    base = compositing if compositing is not None else Compositing()
    others = tuple(mask for mask in base.masks if mask.id != CROP_MASK_ID)
    if crop is None or crop.is_empty:
        return replace(base, masks=others)
    return replace(base, masks=(*others, crop_to_mask(crop)))


def keep_crop_last(masks: tuple[Mask, ...]) -> tuple[Mask, ...]:
    """Remet le masque de recadrage en fin de pile (à appeler après avoir ajouté un masque)."""
    crop = tuple(mask for mask in masks if mask.id == CROP_MASK_ID)
    return (*(mask for mask in masks if mask.id != CROP_MASK_ID), *crop)


def crop_is_animated(clip: Any) -> bool:
    """Vrai si une propriété du masque de recadrage porte des images-clés (elles l'emportent sur la valeur de base)."""
    prefix = mask_property_id(CROP_MASK_ID, "")
    return any(str(getattr(kf, "property_name", "")).startswith(prefix) for kf in getattr(clip, "animation", ()) or ())


def crop_of(clip: Any) -> Crop | None:
    """Recadrage courant d'un clip (``None`` : aucun)."""
    return crop_of_compositing(getattr(clip, "compositing", None))


def _editable_clip(project: Any, clip_id: str) -> Any:
    from .timeline_operations import find_clip

    try:
        clip = find_clip(project, clip_id)
    except KeyError as error:
        raise CropError(f"Clip introuvable : {clip_id!r}.") from error
    track = next(track for track in project.tracks if any(item.id == clip_id for item in track.clips))
    if getattr(track, "locked", False):
        raise CropError(f"La piste '{track.id}' est verrouillée : recadrage refusé.")
    if track.type not in CROP_TRACK_TYPES:
        raise CropError(f"Le clip '{clip_id}' est sur une piste '{track.type}' : seuls les clips vidéo ou graphiques se recadrent.")
    return clip


def set_crop(project: Any, clip_id: str, crop: Crop) -> Crop | None:
    """Pose (ou met à jour) l'unique masque de recadrage du clip ; un recadrage nul le retire. Rend le recadrage posé.

    Refuse (``CropError``) un clip inconnu, une piste verrouillée, un clip d'une piste sans image, ou un recadrage dont
    les images-clés écraseraient la valeur : modifiez alors ses images-clés, ou réinitialisez-le.
    """
    clip = _editable_clip(project, clip_id)
    if not isinstance(crop, Crop):
        raise CropError("Recadrage : une valeur Crop est attendue.")
    if crop_is_animated(clip) and not crop.is_empty:
        raise CropError("Le recadrage de ce clip est animé : modifiez ses images-clés, ou réinitialisez-le d'abord.")
    clip.compositing = with_crop(getattr(clip, "compositing", None), crop)
    if crop.is_empty:
        _forget_animation(clip)
        return None
    return crop


def clear_crop(project: Any, clip_id: str) -> None:
    """Retire le masque de recadrage du clip (et ses images-clés) ; sans effet s'il n'y en a pas."""
    clip = _editable_clip(project, clip_id)
    clip.compositing = with_crop(getattr(clip, "compositing", None), None)
    _forget_animation(clip)


def _forget_animation(clip: Any) -> None:
    prefix = mask_property_id(CROP_MASK_ID, "")
    clip.animation = [kf for kf in getattr(clip, "animation", ()) or () if not str(kf.property_name).startswith(prefix)]


__all__ = [
    "CROP_MASK_ID", "CROP_MASK_NAME", "CROP_TRACK_TYPES", "MAX_CROP_SIDE", "MIN_VISIBLE", "Crop", "CropError",
    "clear_crop", "crop_is_animated", "crop_mask", "crop_of", "crop_of_compositing", "crop_to_mask", "is_custom_crop",
    "keep_crop_last", "mask_to_crop", "set_crop", "with_crop",
]
