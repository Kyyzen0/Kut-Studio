"""Effets d'impact et Ken Burns : zoom d'impact, secousse, flash, « impact sur chaque cut », mouvement lent d'une photo (lot 5 de l'extension 0.8.0).

Même chemin que le menu « Vidéo sociale » (``ui/main_window_mixins/social.py``) : chaque verbe appelle la fonction de :mod:`core.impact_fx` /
:mod:`core.ken_burns`. Là où le script serait **moins protégé** que l'interface, il est plus strict :

- **images clés posées à la main** : ces effets *remplacent* les images clés d'échelle, de position ou de rotation de la plage qu'ils animent ;
  l'interface le fait sans rien dire, un script reçoit un **refus** (retirez-les d'abord) ;
- **durée de l'effet** : le modèle écourte un effet plus long que le clip ; un script reçoit un refus, valeur par défaut comprise (le zoom dure
  0,3 s, la secousse 0,45 s) ;
- **bornes du transform** : une valeur calculée qui sortirait des bornes du registre d'animation (échelle 0,05–10, position ±4…) est refusée par le
  modèle (``TransformKeyframe``), jamais ramenée ; la session restaure le projet ;
- **verrous** : clip sur une piste verrouillée ou calque verrouillé refusés (``clip_edit_refusal``) ; « sur chaque cut » les vérifie pour chaque plan entrant avant la première écriture
  (c'est :func:`~core.impact_fx.apply_impact_on_cuts` qui le fait) ;
- **genres** : pistes vidéo et graphiques (les clips audio et de sous-titres n'ont pas de transform) ; un calque de lumière ne prend pas de zoom d'impact.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..impact_fx import (
    DEFAULT_ZOOM, FLASH_SECONDS, SHAKE_SECONDS, ZOOM_SECONDS, add_flash, apply_camera_shake, apply_impact_on_cuts, apply_impact_zoom, cut_times,
)
from ..ken_burns import DEFAULT_STRENGTH, apply_ken_burns
from ..timeline_operations import find_clip
from .auto_rules import (
    MAX_NEW_CLIPS_PER_OPERATION, MAX_SHAKE_SECONDS, graphic_type_of, require_count, require_fits, require_keyframe_room, require_no_keyframes,
    shake_keyframe_count,
)
from .layer_rules import require_layers_room
from .resource_limits import require_keyframes_after
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _TRACK, _editable_clip, _track_id

_VISUAL = ("video", "graphics")
_START = Param("start", Kind.NUMBER, "Début de l'effet depuis le début du clip, en secondes (défaut 0).", required=False, minimum=0.0, maximum=MAX_TIME)
_SHAKE_POSITION = ("position_x", "position_y", "rotation")


def _impact_zoom(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        clip = find_clip(project, _editable_clip(project, a["clip"], track_types=_VISUAL))
        if graphic_type_of(clip) == "light":
            raise ValueError(f"apply_impact_zoom : le calque '{clip.id}' est un calque de lumière (l'interface l'écarte aussi).")
        start, length = float(a.get("start", 0.0)), float(a.get("length", ZOOM_SECONDS))
        end = require_fits(clip, start, length, "apply_impact_zoom")
        require_no_keyframes(clip, ("scale",), start, end, "apply_impact_zoom")
        strength = float(a.get("strength", DEFAULT_ZOOM))
        require_keyframes_after(clip, lambda probe: apply_impact_zoom(probe, strength=strength, start=start, length=length), "apply_impact_zoom")
        apply_impact_zoom(clip, strength=strength, start=start, length=length)
        return clip

    return operation


def _camera_shake(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        clip = find_clip(project, _editable_clip(project, a["clip"], track_types=_VISUAL))
        start, length = float(a.get("start", 0.0)), float(a.get("length", SHAKE_SECONDS))
        end = require_fits(clip, start, length, "apply_camera_shake")
        require_keyframe_room(clip, shake_keyframe_count(length), "apply_camera_shake")        # avant d'échantillonner à 15 Hz
        require_no_keyframes(clip, _SHAKE_POSITION, start, end, "apply_camera_shake")
        extra = {} if a.get("seed") is None else {"seed": int(a["seed"])}
        apply_camera_shake(clip, start=start, length=length, amplitude=float(a.get("amplitude", 0.012)),
                           rotation=float(a.get("rotation", 1.2)), **extra)
        return clip

    return operation


def _add_flash(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        length = float(a.get("length", FLASH_SECONDS))
        if length < 1.0 / project.fps - 1e-9:             # le modèle l'allongerait à une image
            raise ValueError(f"Un flash de {length:g} s est plus court qu'une image ({1.0 / project.fps:g} s).")
        require_layers_room(project, 1)                               # un calque de lumière : un clip et un média interne
        return add_flash(project, float(a["at"]), length=length, color=a.get("color", "#FFFFFF"), clip_id=a.get("id"))

    return operation


def _apply_impact_on_cuts(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        track_id = _track_id(project, a["track"])
        track = next(item for item in project.tracks if item.id == track_id)
        if track.type != "video":
            raise ValueError(f"La piste '{track_id}' est une piste '{track.type}' : « sur chaque cut » vise une piste vidéo.")
        zoom, flash, shake = float(a.get("zoom", DEFAULT_ZOOM)), bool(a.get("flash", False)), bool(a.get("shake", False))
        if zoom <= 0.0 and not flash and not shake:
            raise ValueError("apply_impact_on_cuts : rien à faire (zoom nul, ni flash ni secousse).")
        times = set(cut_times(project, track_id))
        incoming = [clip for clip in track.clips if round(clip.timeline_start, 6) in times]       # ceux que le modèle touche
        if not incoming:
            raise ValueError(f"Aucun cut sur la piste '{track_id}' (des clips qui se suivent bord à bord).")
        require_count(len(incoming), MAX_NEW_CLIPS_PER_OPERATION, "plans entrants")
        for clip in incoming:                             # verrous : le modèle vérifie piste et calque de chacun avant la première écriture
            if zoom > 0.0:
                require_no_keyframes(clip, ("scale",), 0.0, require_fits(clip, 0.0, ZOOM_SECONDS, "apply_impact_on_cuts"), "apply_impact_on_cuts")
            if shake:
                require_keyframe_room(clip, shake_keyframe_count(SHAKE_SECONDS), "apply_impact_on_cuts")
                require_no_keyframes(clip, _SHAKE_POSITION, 0.0, require_fits(clip, 0.0, SHAKE_SECONDS, "apply_impact_on_cuts"), "apply_impact_on_cuts")
        require_layers_room(project, len(incoming) if flash else 0)
        return apply_impact_on_cuts(project, track_id, zoom=zoom, flash=flash, shake=shake)

    return operation


def _apply_ken_burns(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        clip = find_clip(project, _editable_clip(project, a["clip"], track_types=_VISUAL))
        kind = graphic_type_of(clip)
        if kind not in (None, "image"):
            raise ValueError(f"apply_ken_burns : le calque '{clip.id}' n'est ni une vidéo ni une image.")
        require_no_keyframes(clip, ("scale", "position_x", "position_y"), 0.0, float(clip.duration), "apply_ken_burns")
        strength = float(a.get("strength", DEFAULT_STRENGTH))
        require_keyframes_after(clip, lambda probe: apply_ken_burns([probe], strength=strength), "apply_ken_burns")
        if apply_ken_burns([clip], strength=strength) != 1:
            raise ValueError(f"apply_ken_burns : le clip '{clip.id}' n'a pas de durée.")
        return clip

    return operation


IMPACT_VERBS: tuple[Verb, ...] = (
    Verb("apply_impact_zoom", "Zoom d'impact : le clip entre agrandi de `strength` (0,08 = 8 %) et revient à son échelle en `length` s (images clés d'échelle). "
                              "Refusé si l'effet dépasse la fin du clip, remplacerait des images clés d'échelle de la plage, ou si l'échelle atteinte sort des bornes.",
         (_CLIP, Param("strength", Kind.NUMBER, "Agrandissement d'entrée, 0 à 9 (défaut 0,08).", required=False, minimum=0.0, maximum=9.0), _START,
          Param("length", Kind.NUMBER, "Durée du retour à l'échelle, en secondes (défaut 0,3).", required=False, minimum=0.01, maximum=MAX_TIME)),
         _impact_zoom, group="social"),
    Verb("apply_camera_shake", "Secousse de caméra : position et rotation tirées au hasard (graine stable) toutes les 1/15 s, amorties jusqu'au repos. "
                               "Mêmes refus que apply_impact_zoom, pour les images clés de position et de rotation.",
         (_CLIP, _START,
          Param("length", Kind.NUMBER, f"Durée de la secousse, en secondes, au plus {MAX_SHAKE_SECONDS:g} (défaut 0,45).", required=False, minimum=0.01,
                maximum=MAX_SHAKE_SECONDS),
          Param("amplitude", Kind.NUMBER, "Amplitude, fraction du cadre, 0 à 1 (défaut 0,012).", required=False, minimum=0.0, maximum=1.0),
          Param("rotation", Kind.NUMBER, "Rotation maximale, degrés, 0 à 360 (défaut 1,2).", required=False, minimum=0.0, maximum=360.0),
          Param("seed", Kind.INTEGER, "Graine du hasard (défaut : dérivée de l'identifiant du clip, donc stable).", required=False, minimum=0,
                maximum=2**32 - 1)),
         _camera_shake, group="social"),
    Verb("add_flash", "Flash : un calque de lumière plein cadre dont l'opacité tombe de 1 à 0 en `length` s, sur la première piste de graphiques éditable ; "
                      "le calque créé est le flash (`id` / `as`).",
         (Param("at", Kind.NUMBER, "Instant du flash, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("length", Kind.NUMBER, "Durée, au moins une image (défaut 0,18).", required=False, minimum=0.001, maximum=MAX_TIME),
          Param("color", Kind.COLOR, "Couleur du flash (défaut #FFFFFF).", required=False)),
         _add_flash, creates="clip", group="social"),
    Verb("apply_impact_on_cuts", "À chaque cut d'une piste vidéo (clips bord à bord) : zoom d'impact sur le plan qui arrive, et secousse / flash si demandés ; "
                                 "rend le nombre de cuts traités. Refusé sans cut, ou si un plan est verrouillé, trop court pour l'effet ou déjà animé en échelle.",
         (_TRACK, Param("zoom", Kind.NUMBER, "Agrandissement d'entrée, 0 à 9 (défaut 0,08 ; 0 : pas de zoom).", required=False, minimum=0.0, maximum=9.0),
          Param("flash", Kind.BOOLEAN, "Ajoute un flash à chaque cut (défaut non).", required=False),
          Param("shake", Kind.BOOLEAN, "Ajoute une secousse à chaque cut (défaut non).", required=False)),
         _apply_impact_on_cuts, group="social"),
    Verb("apply_ken_burns", "Mouvement lent (zoom ou balayage, choisi de façon stable d'après l'identifiant du clip) sur un clip vidéo ou une image : images clés d'échelle "
                            "et de position. Refusé si le clip a déjà des images clés d'échelle ou de position.",
         (_CLIP, Param("strength", Kind.NUMBER, "Agrandissement maximal, de 0,01 à 0,5 (défaut 0,12).", required=False, minimum=0.01, maximum=0.5)),
         _apply_ken_burns, group="social"),
)

__all__ = ["IMPACT_VERBS"]
