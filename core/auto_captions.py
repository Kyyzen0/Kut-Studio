"""Sous-titres automatiques : les mots d'une transcription (:mod:`core.transcription`) posés dans la timeline.

Deux rendus d'une même transcription :

* **sous-titres** : une ligne par clip sur une piste de sous-titres (exportés en SRT, ou incrustés) ;
* **titres karaoké** : une ligne par titre texte au style des vidéos sociales (Anton, contour, ombre), animé en
  karaoké ; chaque mot s'allume à l'instant où il est dit (``GraphicOverlay.word_times``, qui prime sur la courbe de
  révélation).

Les mots sont datés en temps du **média** ; ils passent en temps de la timeline par le mapping du clip de la voix
(:func:`core.tracking_motion.local_time_for_source`). Seule une voix lue à vitesse normale, dans le bon sens, est
acceptée (:func:`core.beat_detection.plays_at_media_speed`) : accélérée ou inversée, une ligne ne tomberait plus sur sa
phrase. Les nouvelles lignes vont sur une piste où elles ne recouvrent rien (créée au besoin) : les titres et
sous-titres déjà posés restent tels quels.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .graphics import MAX_TEXT_WORDS, GraphicType, add_graphic_clip
from .mograph_presets import style_social_text
from .project_model import Clip, Project
from .text_animations import apply_text_animation
from .timeline_operations import add_subtitle_clip
from .track_operations import free_track
from .tracking_motion import local_time_for_source
from .transcription import Word, group_words

MIN_LINE_SECONDS = 0.4
"""Une ligne reste affichée au moins ce temps (un mot isolé et bref resterait illisible)."""

KARAOKE_SIZE = 64
KARAOKE_Y = 0.27
"""Taille et hauteur du preset « Sous-titre karaoké » : sous le centre, au-dessus de la légende des plateformes."""
KARAOKE_HIGHLIGHT = "#FFD84D"


@dataclass(frozen=True)
class CaptionLine:
    """Une ligne de sous-titre en secondes de la timeline ; ``word_starts`` : instant de chaque mot (``\\S+``) depuis
    le début de la ligne."""

    start: float
    end: float
    text: str
    word_starts: tuple[float, ...]


def caption_lines(voice: Clip, words: Sequence[Word], **grouping) -> list[CaptionLine]:
    """Lignes de sous-titres de ``words`` (temps du média de ``voice``), en temps de la timeline.

    Une ligne dure au moins :data:`MIN_LINE_SECONDS` sans empiéter sur la suivante, et ne déborde pas du clip."""
    clip_end = voice.timeline_start + voice.duration

    def on_timeline(seconds: float) -> float:
        local = local_time_for_source(voice, seconds)
        return round(min(clip_end, voice.timeline_start + max(0.0, local)), 4)

    groups = group_words(words, **grouping)
    starts = [on_timeline(group[0].start) for group in groups]
    lines: list[CaptionLine] = []
    for index, group in enumerate(groups):
        start = starts[index]
        following = starts[index + 1] if index + 1 < len(starts) else clip_end
        end = min(max(on_timeline(group[-1].end), start + MIN_LINE_SECONDS), max(following, start + 0.04))
        tokens: list[str] = []
        times: list[float] = []
        for word in group:
            for token in word.text.split():          # un mot reconnu avec une espace compte pour plusieurs
                tokens.append(token)
                times.append(round(max(0.0, on_timeline(word.start) - start), 4))
        if tokens and end > start:
            lines.append(CaptionLine(start, round(end, 4), " ".join(tokens), tuple(times)))
    return lines


def add_subtitle_lines(project: Project, lines: Sequence[CaptionLine]) -> list[Clip]:
    """Une ligne par clip de sous-titre, sur une piste de sous-titres libre sur toute la plage (créée au besoin)."""
    if not lines:
        return []
    track = free_track(project, "subtitle", lines[0].start, lines[-1].end)
    return [add_subtitle_clip(project, line.text, line.start, line.end - line.start, track_id=track.id) for line in lines]


def add_karaoke_lines(project: Project, lines: Sequence[CaptionLine]) -> list[Clip]:
    """Une ligne par titre karaoké (style vidéo sociale), sur une piste de titres libre sur toute la plage."""
    if not lines:
        return []
    track = free_track(project, "graphics", lines[0].start, lines[-1].end)
    created = []
    for line in lines:
        clip = add_graphic_clip(project, GraphicType.TEXT, timeline_start=line.start, duration=line.end - line.start,
                                track=track)
        style_social_text(clip, line.text, KARAOKE_SIZE, KARAOKE_Y)
        apply_text_animation(clip, "karaoke")
        graphic = clip.graphic
        if graphic is None:                          # add_graphic_clip pose toujours le calque : garde de typage
            raise RuntimeError("Titre karaoké sans calque graphique")
        times = line.word_starts if len(line.word_starts) <= MAX_TEXT_WORDS else ()
        clip.graphic = replace(graphic, word_times=times, highlight_color=KARAOKE_HIGHLIGHT)
        clip.label = line.text
        created.append(clip)
    return created


__all__ = [
    "CaptionLine", "KARAOKE_HIGHLIGHT", "KARAOKE_SIZE", "KARAOKE_Y", "MIN_LINE_SECONDS", "add_karaoke_lines",
    "add_subtitle_lines", "caption_lines",
]
