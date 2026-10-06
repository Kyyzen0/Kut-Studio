"""Poser des SFX de la bibliothèque sur la timeline : un son, ou un son à chaque cut d'une piste vidéo.

Un SFX est posé par son **ancre** (:class:`core.sfx_synth.SfxSpec`) : le centre d'un whoosh, le pic d'un passage de
moteur, la fin d'un riser tombent sur le cut. Les sons qui se chevauchent vont sur une autre piste SFX (la première
libre, comme on le faisait à la main pour l'edit F1) ; une piste est créée s'il le faut. Près d'une voix, le SFX est
baissé un peu plus : il souligne le montage sans couvrir la parole.
"""

from __future__ import annotations

from collections.abc import Sequence

from .impact_fx import cut_times
from .project_model import Clip, MediaAsset, Project, Track
from .sfx_synth import SFX_SPECS, ensure_sfx_file, sfx_duration
from .timeline_operations import add_clip_to_track, find_clip

VOICE_MARGIN = 0.3
"""Un SFX à moins de 0,3 s d'une voix est baissé de :data:`VOICE_DUCK_DB`."""
VOICE_DUCK_DB = -2.0


def sfx_asset(project: Project, sfx_id: str) -> MediaAsset:
    """Le média du SFX dans le projet (créé au premier usage, partagé ensuite) ; son fichier est synthétisé s'il
    manque."""
    path = str(ensure_sfx_file(sfx_id))
    for asset in project.media_assets:
        if asset.path == path:
            return asset
    asset = MediaAsset(id=f"sfx-{sfx_id}", path=path, name=sfx_id, duration=sfx_duration(sfx_id), width=0, height=0,
                       fps=0.0, media_type="audio", has_audio=True)
    if any(existing.id == asset.id for existing in project.media_assets):
        asset = MediaAsset(**{**asset.__dict__, "id": f"sfx-{sfx_id}-{len(project.media_assets)}"})
    project.media_assets.append(asset)
    return asset


def _sfx_tracks(project: Project) -> list[Track]:
    return [track for track in project.tracks if track.type == "audio" and track.audio_role == "sfx" and not track.locked]


def _free(track: Track, start: float, end: float) -> bool:
    return all(clip.timeline_start + clip.duration <= start + 1e-6 or clip.timeline_start >= end - 1e-6
               for clip in track.clips if clip.enabled)


def _new_sfx_track(project: Project) -> Track:
    numbers = [int(t.id[1:]) for t in project.tracks if t.id.startswith("A") and t.id[1:].isdigit()]
    track_id = f"A{max(numbers, default=0) + 1}"
    track = Track(id=track_id, name=track_id, type="audio", audio_role="sfx")
    project.tracks.append(track)
    return track


def _near_voice(project: Project, start: float, end: float) -> bool:
    for track in project.tracks:
        if track.type != "audio" or track.audio_role != "voice" or track.muted:
            continue
        for clip in track.clips:
            if clip.timeline_start - VOICE_MARGIN < end and start < clip.timeline_start + clip.duration + VOICE_MARGIN:
                return True
    return False


def place_sfx(project: Project, sfx_id: str, at: float, *, gain_db: float | None = None) -> Clip:
    """Pose ``sfx_id`` pour que son ancre tombe à ``at`` (secondes de la timeline), sur une piste SFX libre."""
    spec = SFX_SPECS[sfx_id]
    asset = sfx_asset(project, sfx_id)
    start = at - spec.anchor
    source_in = max(0.0, -start)                         # un son qui commencerait avant 0 est rogné au début
    start = max(0.0, start)
    end = start + asset.duration - source_in
    track = next((t for t in _sfx_tracks(project) if _free(t, start, end)), None) or _new_sfx_track(project)
    clip = add_clip_to_track(project, asset.id, track.id, start)
    clip = find_clip(project, clip.id)
    if source_in > 0.0:
        clip.source_in = source_in
    gain = spec.gain_db if gain_db is None else float(gain_db)
    if _near_voice(project, start, end):
        gain += VOICE_DUCK_DB
    clip.gain_db = gain
    return clip


def place_sfx_on_cuts(project: Project, video_track_id: str, sfx_ids: Sequence[str]) -> list[Clip]:
    """Un SFX à chaque cut de la piste (les identifiants tournent : whoosh, whoosh long, whoosh court…)."""
    if not sfx_ids:
        raise ValueError("Choisissez au moins un son.")
    unknown = [sfx_id for sfx_id in sfx_ids if sfx_id not in SFX_SPECS]
    if unknown:
        raise KeyError(f"SFX inconnu : {unknown[0]!r}.")
    times = cut_times(project, video_track_id)
    if not times:
        raise ValueError("Aucun cut sur cette piste (des clips qui se suivent bord à bord).")
    return [place_sfx(project, sfx_ids[index % len(sfx_ids)], at) for index, at in enumerate(times)]


__all__ = ["VOICE_DUCK_DB", "place_sfx", "place_sfx_on_cuts", "sfx_asset"]
