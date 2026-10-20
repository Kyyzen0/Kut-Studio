"""Montage sur le rythme, plans, sous-titres : ce que l'interface fait après une analyse, ici avec des **données** (lot 5 de l'extension 0.8.0).

Aucun verbe n'analyse un média (ADR-0005, R1 et R2) : la grille rythmique, les instants des changements de plan et les lignes de sous-titres
viennent du script ; chaque verbe appelle la fonction que l'interface appelle **une fois l'analyse finie** (``ui/main_window_mixins/beat_grid.py``,
``timeline_editing.py``, ``auto_captions.py``). Les règles communes (bornes de ressources, verrous) sont dans :mod:`auto_rules`.

- **grille** : ``set_beat_grid`` / ``remove_beat_grid`` écrivent ``Sequence.beat_grid`` de la séquence active, comme la boîte « Grille rythmique » ;
  ``cut_clips_on_beats`` et ``distribute_on_grid`` la lisent (refus sans grille). Le nombre de morceaux est calculé **avant** de couper (un tempo
  rapide sur un clip très long en créerait des millions) ;
- **plans** : les instants sont en secondes du **média**. Le modèle écarte en silence un instant trop proche d'un bord ou d'un autre ; un script le reçoit
  **refusé**, de même qu'une liste qui ne coupe rien ;
- **sous-titres** : les lignes sont en secondes de la **timeline** (déjà passées du temps du média à celui de la timeline), triées et sans recouvrement,
  comme celles que produit la transcription ; ``word_starts`` d'un titre karaoké compte un instant par mot et reste dans la ligne.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..auto_captions import CaptionLine, add_karaoke_lines, add_subtitle_lines
from ..beat_edit import cut_clips_on_beats, cut_times_on_beats, distribute_on_grid
from ..beat_grid import MAX_BEATS_PER_BAR, MAX_BPM, MIN_BPM, BeatGrid
from ..graphics import MAX_TEXT_WORDS
from ..scene_detection import MIN_SHOT_SECONDS, cut_clip_at_scenes, scene_cut_refusal, shot_boundaries
from ..timeline_operations import find_clip
from .auto_rules import (
    MAX_LISTED_CLIPS, MAX_NEW_CLIPS_PER_OPERATION, TOLERANCE, editable_clips, require_count, require_grid, require_room,
)
from .impact_verbs import IMPACT_VERBS
from .lock_rules import require_followers_unlocked
from .template_verbs import TEMPLATE_VERBS
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _editable_clip

_MAX_EVERY = 64
MIN_LINE_SECONDS = 0.04
"""Durée minimale d'une ligne de sous-titre : celle que la transcription ne descend jamais sous (une image à 25 i/s)."""
_MAX_WORDS = 100

_CLIPS = Param("clips", Kind.LIST, f"Les clips (identifiants ou alias), de 1 à {MAX_LISTED_CLIPS}, chacun cité une fois.", minimum=1, maximum=MAX_LISTED_CLIPS,
               item=Param("clip", Kind.SELECTOR, "Un clip.", of="clip"))
_EVERY = Param("every", Kind.INTEGER, "Un temps sur `every` (défaut 1 : tous les temps).", required=False, minimum=1, maximum=_MAX_EVERY)


# --- Grille rythmique ----------------------------------------------------------------------------------------------------


def _set_beat_grid(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        grid = BeatGrid(float(a["bpm"]), float(a.get("offset", 0.0)), int(a.get("beats_per_bar", 4)))     # valide ; comme la boîte de dialogue
        project.active_sequence.beat_grid = grid
        return grid

    return operation


def _remove_beat_grid(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        require_grid(project)
        project.active_sequence.beat_grid = None
        return None

    return operation


def _cut_count(grid: BeatGrid, clip: Any, every: int) -> int:
    """Nombre de coupes sur ce clip, sans allouer plus que la limite (un majorant d'abord, le compte exact ensuite s'il est petit)."""
    bound = int(float(clip.duration) / (grid.beat * every)) + 1
    if bound > MAX_NEW_CLIPS_PER_OPERATION + 1:
        return bound - 1                                  # au moins ce nombre : refusé sans calculer la liste
    start = float(clip.timeline_start)
    return len(cut_times_on_beats(grid, start, start + float(clip.duration), every=every))


def _cut_clips_on_beats(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        grid = require_grid(project)
        ids = editable_clips(project, a["clips"])
        every = int(a.get("every", 1))
        counts = {clip_id: _cut_count(grid, find_clip(project, clip_id), every) for clip_id in ids}
        for clip_id in ids:
            if counts[clip_id] > 0:                       # un clip sans temps fort intérieur reste intact : ni lien ni source ne changent
                require_followers_unlocked(project, clip_id, "couper")
        pieces = sum(counts.values())
        require_room(project, pieces)
        return cut_clips_on_beats(project, ids, grid, every=every)

    return operation


def _distribute_on_grid(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        grid = require_grid(project)
        ids = editable_clips(project, a["clips"])
        placements = distribute_on_grid(project, ids, grid, beats_each=int(a.get("beats_each", 2)),
                                        start=None if a.get("start") is None else float(a["start"]))
        if any(placement.timeline_start > MAX_TIME for placement in placements):         # session : tout est restauré
            raise ValueError(f"La répartition dépasserait la limite de {MAX_TIME:g} s.")
        return placements

    return operation


# --- Plans -----------------------------------------------------------------------------------------------------------------


def _cut_clip_at_scenes(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video",))
        clip = find_clip(project, clip_id)
        reason = scene_cut_refusal(clip)
        if reason:
            raise ValueError(reason)
        times = [float(time) for time in a["times"]]
        kept = shot_boundaries(times, clip.source_in, clip.source_out)
        if len(kept) != len(times):                       # le modèle les écarterait en silence : un script reçoit un refus
            raise ValueError(
                f"{len(times) - len(kept)} instant(s) sur {len(times)} sont refusés : à moins de {MIN_SHOT_SECONDS:g} s d'un bord de la "
                f"fenêtre du clip ({clip.source_in:g}–{clip.source_out:g} s du média) ou d'un autre instant."
            )
        require_room(project, len(kept))
        require_followers_unlocked(project, clip_id, "couper")
        return cut_clip_at_scenes(project, clip_id, times)

    return operation


# --- Sous-titres -----------------------------------------------------------------------------------------------------------


def _caption_lines(raw: list[Mapping[str, Any]], *, karaoke: bool) -> list[CaptionLine]:
    """Les lignes décodées, vérifiées : durée positive, triées, sans recouvrement ; mots du karaoké datés dans la ligne."""
    require_count(len(raw), MAX_NEW_CLIPS_PER_OPERATION, "lignes")
    lines: list[CaptionLine] = []
    previous_end = 0.0
    for position, item in enumerate(raw):
        start, end, text = float(item["start"]), float(item["end"]), str(item["text"])
        if end - start < MIN_LINE_SECONDS - TOLERANCE:
            raise ValueError(f"Ligne {position} : elle doit durer au moins {MIN_LINE_SECONDS:g} s (de {start:g} s à {end:g} s).")
        if start < previous_end - TOLERANCE:
            raise ValueError(f"Ligne {position} : elle commence à {start:g} s, avant la fin de la précédente ({previous_end:g} s) ; "
                             "les lignes se donnent dans l'ordre, sans recouvrement.")
        previous_end = end
        starts = tuple(float(value) for value in item.get("word_starts", ()))
        if karaoke and starts:
            words = len(text.split())
            if words > MAX_TEXT_WORDS or len(starts) != words:
                raise ValueError(f"Ligne {position} : `word_starts` doit donner un instant par mot ({words} mots, {len(starts)} instants).")
            if any(later < earlier for earlier, later in zip(starts, starts[1:])) or starts[-1] > end - start + TOLERANCE:
                raise ValueError(f"Ligne {position} : les instants des mots doivent croître et rester dans la ligne ({end - start:g} s).")
        lines.append(CaptionLine(start, end, text, starts))
    return lines


def _add_subtitle_lines(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        lines = _caption_lines(a["lines"], karaoke=False)
        require_room(project, len(lines))
        return add_subtitle_lines(project, lines)

    return operation


def _add_karaoke_lines(a: Mapping[str, Any]) -> Operation:
    def operation(project: Any) -> Any:
        lines = _caption_lines(a["lines"], karaoke=True)
        require_room(project, len(lines))
        return add_karaoke_lines(project, lines)

    return operation


_LINE_FIELDS = (
    Param("start", Kind.NUMBER, "Début de la ligne sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME),
    Param("end", Kind.NUMBER, "Fin de la ligne (après son début), en secondes.", minimum=0.0, maximum=MAX_TIME),
    Param("text", Kind.STRING, "Le texte de la ligne."),
)
_KARAOKE_FIELDS = (*_LINE_FIELDS, Param(
    "word_starts", Kind.LIST, "Instant de chaque mot depuis le début de la ligne (un par mot, croissants, dans la ligne) ; absent, le titre se révèle d'un trait.",
    required=False, minimum=1, maximum=_MAX_WORDS, item=Param("word", Kind.NUMBER, "Instant d'un mot.", minimum=0.0, maximum=MAX_TIME)))

AUTO_RHYTHM_VERBS: tuple[Verb, ...] = (
    Verb("set_beat_grid", "Pose la grille rythmique de la séquence active (tempo, premier temps, temps par mesure), comme la boîte « Grille rythmique » ; "
                          "elle remplace l'ancienne. Rien n'est mesuré : le tempo vient du script.",
         (Param("bpm", Kind.NUMBER, f"Temps par minute, de {MIN_BPM:g} à {MAX_BPM:g}.", minimum=MIN_BPM, maximum=MAX_BPM),
          Param("offset", Kind.NUMBER, "Instant d'un premier temps de mesure, en secondes de la timeline (défaut 0).", required=False,
                minimum=-MAX_TIME, maximum=MAX_TIME),
          Param("beats_per_bar", Kind.INTEGER, f"Temps par mesure, de 1 à {MAX_BEATS_PER_BAR} (défaut 4).", required=False, minimum=1,
                maximum=MAX_BEATS_PER_BAR)),
         _set_beat_grid, group="rhythm"),
    Verb("remove_beat_grid", "Supprime la grille rythmique de la séquence active (refusé s'il n'y en a pas).", (), _remove_beat_grid, group="rhythm"),
    Verb("cut_clips_on_beats", "Coupe des clips sur les temps de la grille rythmique qu'ils traversent (un sur `every`) ; tout est vérifié avant la première "
                               "coupe, un clip qu'aucun temps ne traverse est laissé tel quel, aucun temps dans aucun clip est refusé. Les morceaux ont des identifiants générés.",
         (_CLIPS, _EVERY), _cut_clips_on_beats, group="rhythm"),
    Verb("distribute_on_grid", "Pose des clips bout à bout sur leurs pistes, chacun sur `beats_each` temps de la grille, à partir du temps le plus proche du premier "
                               "(ou de `start`) ; un clip trop long est raccourci, un recouvrement avec un autre clip est refusé.",
         (_CLIPS, Param("beats_each", Kind.INTEGER, "Temps par clip (défaut 2).", required=False, minimum=1, maximum=_MAX_EVERY),
          Param("start", Kind.NUMBER, "Instant de départ, en secondes (défaut : le temps le plus proche du premier clip).", required=False,
                minimum=0.0, maximum=MAX_TIME)),
         _distribute_on_grid, group="rhythm"),
    Verb("cut_clip_at_scenes", "Coupe un clip vidéo aux changements de plan que le script donne (instants du **média**, en secondes) ; un instant trop proche d'un bord "
                               "ou d'un autre est refusé, ainsi qu'un clip retimé, inversé, figé, imbriqué ou composé. Les morceaux ont des identifiants générés.",
         (_CLIP, Param("times", Kind.LIST, f"Les instants des changements de plan, en secondes du média (de 1 à {MAX_NEW_CLIPS_PER_OPERATION}).", minimum=1,
                       maximum=MAX_NEW_CLIPS_PER_OPERATION,
                       item=Param("time", Kind.NUMBER, "Un instant du média.", minimum=0.0, maximum=MAX_TIME))),
         _cut_clip_at_scenes, group="rhythm"),
    Verb("add_subtitle_lines", "Pose des sous-titres, un clip par ligne, sur une piste de sous-titres libre sur toute la plage (créée au besoin) ; lignes triées, sans "
                               "recouvrement, en secondes de la timeline. Rien n'est transcrit : le texte vient du script.",
         (Param("lines", Kind.LIST, f"Les lignes (début, fin, texte), de 1 à {MAX_NEW_CLIPS_PER_OPERATION}.", minimum=1, maximum=MAX_NEW_CLIPS_PER_OPERATION,
                fields=_LINE_FIELDS),),
         _add_subtitle_lines, group="social"),
    Verb("add_karaoke_lines", "Pose des titres karaoké (style des vidéos sociales : Anton, contour, ombre), un calque par ligne, sur une piste de graphiques libre sur "
                              "toute la plage ; chaque mot s'allume à son instant (`word_starts`). Mêmes règles de lignes que add_subtitle_lines.",
         (Param("lines", Kind.LIST, f"Les lignes (début, fin, texte, `word_starts`), de 1 à {MAX_NEW_CLIPS_PER_OPERATION}.", minimum=1,
                maximum=MAX_NEW_CLIPS_PER_OPERATION, fields=_KARAOKE_FIELDS),),
         _add_karaoke_lines, group="social"),
)

AUTOMATION_VERBS: tuple[Verb, ...] = AUTO_RHYTHM_VERBS + IMPACT_VERBS + TEMPLATE_VERBS
"""Tous les verbes du lot (rythme, plans, sous-titres, effets d'impact, modèles) : ce que :mod:`verbs` insère après son verbe d'ancrage."""

__all__ = ["AUTOMATION_VERBS", "AUTO_RHYTHM_VERBS"]
