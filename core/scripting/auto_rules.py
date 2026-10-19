"""Règles communes des verbes d'automatisation (lot 5 de l'extension 0.8.0) : coupes sur le rythme, sous-titres, effets d'impact, emplacements
de modèle, diaporama, classement.

Aucun verbe ici : ces fonctions travaillent sur le projet et des identifiants, sans importer la table des verbes complète.

**Un script est une donnée (ADR-0005, R1 et R2).** Aucun de ces verbes ne lance d'analyse (détection de tempo ou de plans, transcription) ni ne
décode un média : ils reçoivent **les résultats** (un tempo, des instants de plans, des lignes de sous-titres datées) et appellent la fonction
du modèle qui les applique. Les analyses elles-mêmes restent des gestes de l'interface.

- **Bornes de ressources** : un verbe qui peut créer *N* clips borne *N* **avant** de créer (par opération et sur tout le projet, toutes séquences :
  ``Project.tracks`` ne montre que la séquence active). Un calque graphique (titre karaoké, flash, ligne d'un classement) est un clip : le plafond de
  clips (5 000) couvre donc aussi le plafond de calques de ``layer_rules`` (même valeur, jamais atteint avant lui).
- **Rien n'est écrasé ni ramené en silence** : un effet d'impact qui remplacerait des images clés posées à la main est refusé, un effet plus long que
  le clip aussi (le modèle l'écourterait).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from ..beat_grid import BeatGrid
from ..graphics import GraphicOverlay
from ..impact_fx import SHAKE_FREQUENCY
from ..project_model import Clip, Project
from .verbs import _editable_clip

MAX_NEW_CLIPS_PER_OPERATION = 500
"""Clips (morceaux de coupe, lignes de sous-titres, plans de diaporama, calques) qu'une seule opération crée."""
MAX_CLIPS_IN_PROJECT = 5_000
"""Clips d'un projet (toutes séquences) au-delà desquels ces verbes n'en créent plus : le nombre maximal d'opérations d'un script."""
MAX_LISTED_CLIPS = 200
"""Clips cités par une seule opération (liste de clips à couper, à répartir)."""
MAX_PHOTOS = 100
"""Photos d'un diaporama ou d'un remplissage d'emplacements : chacune est ouverte (en-tête) avant la moindre écriture."""
MAX_KEYFRAMES_PER_CLIP = 2_000
"""Images clés de transform qu'un clip porte au plus après un effet de ces verbes (une secousse en pose 3 par pas de 1/15 s)."""
MAX_SHAKE_SECONDS = 60.0
"""Durée maximale d'une secousse : au-delà, ce n'est plus un effet d'impact."""
TOLERANCE = 1e-6

_KEYFRAME_TOLERANCE = 1e-6


def require_count(count: int, limit: int, what: str) -> None:
    """Refuse ``count`` éléments au-delà de ``limit`` (sans en créer un seul)."""
    if count > limit:
        raise ValueError(f"{count} {what} d'un coup dépassent la limite de {limit} par opération.")


def total_clips(project: Project) -> int:
    """Les clips de TOUTES les séquences du projet."""
    return sum(len(track.clips) for sequence in project.sequences for track in sequence.tracks)


def require_room(project: Project, clips: int) -> None:
    """Refuse, AVANT toute création, ``clips`` clips (calques graphiques compris) de plus s'ils dépassaient le plafond de l'opération ou du projet."""
    require_count(clips, MAX_NEW_CLIPS_PER_OPERATION, "clips")
    count = total_clips(project)
    if count + clips > MAX_CLIPS_IN_PROJECT:
        raise ValueError(f"Le projet compte déjà {count} clips : {clips} de plus dépasseraient la limite de {MAX_CLIPS_IN_PROJECT}.")


def require_grid(project: Project) -> BeatGrid:
    """La grille rythmique de la séquence active (celle que l'interface lit), ou un refus qui dit comment la poser."""
    grid = getattr(project.active_sequence, "beat_grid", None)
    if grid is None:
        raise ValueError("La séquence active n'a pas de grille rythmique : posez-la d'abord avec set_beat_grid.")
    return grid


def editable_clips(project: Project, selectors: Sequence[str], *, track_types: tuple[str, ...] = ()) -> list[str]:
    """Les clips désignés, chacun éditable (piste et calque déverrouillés, genre de piste permis) ; un clip cité deux fois est refusé."""
    require_count(len(selectors), MAX_LISTED_CLIPS, "clips cités")
    ids = [_editable_clip(project, selector, track_types=track_types) for selector in selectors]
    if len(set(ids)) != len(ids):
        raise ValueError("Un clip est cité plusieurs fois dans la liste.")
    return ids


def require_no_keyframes(clip: Clip, names: Iterable[str], start: float, end: float, effect: str) -> None:
    """Le modèle remplace les images clés de ces propriétés comprises dans ``[start, end]`` : celles posées à la main ne sont jamais écrasées en silence."""
    names = set(names)
    clash = sorted({
        str(kf.property_name) for kf in clip.transform_keyframes
        if kf.property_name in names and start - _KEYFRAME_TOLERANCE <= kf.time_seconds <= end + _KEYFRAME_TOLERANCE
    })
    if clash:
        raise ValueError(
            f"{effect} : le clip '{clip.id}' a déjà des images clés ({', '.join(clash)}) dans cette plage ; retirez-les d'abord (remove_keyframe, reset_transform)."
        )


def require_fits(clip: Clip, start: float, length: float, effect: str) -> float:
    """La fin de l'effet (``start + length``) tient dans le clip : le modèle l'écourterait sinon ; rend cette fin."""
    end = start + length
    if start >= clip.duration - TOLERANCE or end > clip.duration + TOLERANCE:
        raise ValueError(
            f"{effect} : de {start:g} s à {end:g} s ne tient pas dans le clip '{clip.id}' ({clip.duration:g} s) ; "
            "réduisez `start` ou `length`."
        )
    return end


def shake_keyframe_count(length: float) -> int:
    """Nombre d'images clés que ``apply_camera_shake`` pose pour cette durée (le calcul du modèle : 3 propriétés, un pas de 1/15 s, au moins 2 pas)."""
    steps = max(2, math.ceil(length * SHAKE_FREQUENCY))
    return 3 * (steps + 1)


def require_keyframe_room(clip: Clip, adding: int, effect: str) -> None:
    """Refuse AVANT d'appeler le modèle si le clip porterait plus de :data:`MAX_KEYFRAMES_PER_CLIP` images clés (celles déjà là comprises)."""
    total = len(clip.transform_keyframes) + adding
    if total > MAX_KEYFRAMES_PER_CLIP:
        raise ValueError(f"{effect} : le clip '{clip.id}' porterait {total} images clés de transform (au plus {MAX_KEYFRAMES_PER_CLIP}) ; réduisez la durée.")


def graphic_type_of(clip: Clip) -> Any:
    """Le genre du calque graphique d'un clip (valeur texte), ``None`` pour un clip qui n'en a pas."""
    graphic = clip.graphic
    if not isinstance(graphic, GraphicOverlay):
        return None
    return getattr(graphic.type, "value", graphic.type)


__all__ = [
    "MAX_CLIPS_IN_PROJECT", "MAX_LISTED_CLIPS", "MAX_NEW_CLIPS_PER_OPERATION", "MAX_PHOTOS", "TOLERANCE",
    "MAX_KEYFRAMES_PER_CLIP", "MAX_SHAKE_SECONDS", "editable_clips", "graphic_type_of", "require_keyframe_room", "shake_keyframe_count", "require_count", "require_fits", "require_grid", "require_no_keyframes", "require_room",
    "total_clips",
]
