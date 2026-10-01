"""Segments d'aperçu fidèles : grille, plan fenêtré, empreinte par segment.

Rôle : construire les :class:`~core.preview_engine.PreviewJob` d'une
fenêtre de timeline **sans parcourir le projet entier** et avec une
empreinte qui ne dépend que de ce qui est affiché dans le segment.

Trois changements par rapport à l'ancienne construction (qui était dans
``MainWindow``) :

1. **Grille alignée** : un segment couvre ``[i × 2 s, (i + 1) × 2 s)``.
   Avant, les segments démarraient à la position exacte de la tête : deux
   positions voisines produisaient des clés différentes et rien ne
   resservait. Sur une grille, balayer la timeline réutilise le cache.
2. **Plan fenêtré** : :func:`core.render_plan.build_render_plan` avec
   ``window=`` ne retient que les couches du segment. Un segment n'ouvre
   plus tous les médias d'un montage de 10 000 clips.
3. **Empreinte par segment** : la clé dépend des seules couches du
   segment. Modifier un clip invalide **ses** segments, pas ceux du
   reste du montage (avant : l'empreinte couvrait tout le plan, un
   changement quelconque invalidait tous les segments).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from .filter_graph import fingerprint_plan
from .preview_cache import SEGMENT_SECONDS, PreviewSegmentKey
from .preview_engine import PreviewJob
from .render_plan import RenderPlan, build_render_plan

PathResolver = Callable[..., str]
"""``resolve(path, need_audio=False) -> path`` (voir :mod:`core.proxy_manager`)."""


def apply_path_resolver(plan: RenderPlan, resolve: PathResolver | None) -> RenderPlan:
    """Copie de ``plan`` dont les chemins média passent par ``resolve``.

    Utilisé **uniquement** pour l'aperçu : substitue les proxys. L'export
    ne passe jamais par ici et rend toujours les médias originaux. Le
    chemin remplacé fait partie de l'empreinte du segment : proxy prêt ou
    supprimé = nouvelle clé, jamais de segment rendu avec un autre média
    servi à tort.
    """
    if resolve is None:
        return plan
    video = tuple(
        replace(layer, source_path=resolve(layer.source_path))
        for layer in plan.video_layers
    )
    audio = tuple(
        replace(layer, source_path=resolve(layer.source_path, need_audio=True))
        for layer in plan.audio_layers
    )
    return replace(plan, video_layers=video, audio_layers=audio)


def segment_plan(
    project,
    start: float,
    end: float,
    *,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
    resolver: PathResolver | None = None,
    timeline_index=None,
) -> RenderPlan:
    """Plan fenêtré (et proxifié) d'un segment ``[start, end)``.

    ``timeline_index`` (un :class:`~core.timeline_index.TimelineIndex`) rend la
    construction indépendante du nombre de clips du montage.
    """
    plan = build_render_plan(
        project,
        master_gain_db=master_gain_db,
        master_muted=master_muted,
        window=(start, end),
        window_index=timeline_index,
    )
    return apply_path_resolver(plan, resolver)


def segment_params_hash(
    plan: RenderPlan, project, quality: str, window_end: float | None = None
) -> str:
    """Empreinte d'un plan de segment pour la qualité de rendu ``quality``.

    La **durée totale** du montage n'entre dans l'empreinte que dans la
    mesure où elle change le segment : un segment entièrement à l'intérieur
    du montage ne dépend pas de ce qui se passe après sa fin. Sans cela,
    raccourcir ou allonger le montage — même un clip tout à la fin —
    invaliderait tous les segments.
    """
    if window_end is not None:
        plan = replace(plan, duration=min(plan.duration, float(window_end)))
    return fingerprint_plan(
        plan,
        width=project.width,
        height=project.height,
        fps=project.fps,
        quality=quality,
    )


def _owner_clip_id(plan: RenderPlan, cursor: float) -> str:
    """Clip auquel rattacher le segment (invalidation par clip), sinon ``timeline``."""
    for layer in plan.video_layers:
        if layer.timeline_start <= cursor < layer.timeline_end:
            return layer.clip_id
    for layer in getattr(plan, "graphics_layers", ()):
        if layer.timeline_start <= cursor < layer.timeline_end:
            return layer.clip_id
    return "timeline"


def build_segment_job(
    project,
    index: int,
    *,
    quality: str,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
    resolver: PathResolver | None = None,
    segment_seconds: float = SEGMENT_SECONDS,
    timeline_index=None,
) -> PreviewJob | None:
    """Job du segment ``index`` de la grille, ou ``None`` s'il est vide.

    ``None`` quand aucune couche vidéo ni graphique ne recouvre le
    segment : il n'y a rien à pré-rendre (trou de timeline).
    """
    start = index * segment_seconds
    end = start + segment_seconds
    plan = segment_plan(
        project, start, end,
        master_gain_db=master_gain_db, master_muted=master_muted, resolver=resolver,
        timeline_index=timeline_index,
    )
    if not (plan.video_layers or getattr(plan, "graphics_layers", ())):
        return None
    key = PreviewSegmentKey(
        clip_id=_owner_clip_id(plan, start),
        start=start,
        end=end,
        quality=quality,
        params_hash=segment_params_hash(plan, project, quality, end),
    )
    return PreviewJob(
        key=key,
        plan=plan,
        width=project.width,
        height=project.height,
        fps=int(project.fps),
        quality=quality,
        start=start,
        duration=segment_seconds,
    )


__all__ = [
    "apply_path_resolver",
    "build_segment_job",
    "segment_params_hash",
    "segment_plan",
]
