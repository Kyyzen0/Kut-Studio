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

import hashlib
import os
from typing import Any
from collections.abc import Callable, Mapping
from dataclasses import replace

from .filter_graph import fingerprint_plan, normalize_fps
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
    # Une couche imbriquée n'a pas de fichier : jamais de proxy pour elle
    # (les médias *à l'intérieur* de la séquence, eux, sont proxifiés).
    video = tuple(
        layer if getattr(layer, "nested_key", "")
        else replace(layer, source_path=resolve(layer.source_path))
        for layer in plan.video_layers
    )
    audio = tuple(
        layer if getattr(layer, "nested_key", "")
        else replace(layer, source_path=resolve(layer.source_path, need_audio=True))
        for layer in plan.audio_layers
    )
    nested = tuple(
        replace(entry, plan=apply_path_resolver(entry.plan, resolve))
        for entry in getattr(plan, "nested_sequences", ()) or ()
    )
    return replace(plan, video_layers=video, audio_layers=audio, nested_sequences=nested)


def apply_grade_overrides(plan: RenderPlan, overrides: Mapping[str, object] | None) -> RenderPlan:
    """Copie de ``plan`` où l'étalonnage de certains clips est remplacé par ce que la page Couleur veut **montrer**
    (sa sélection, ou l'avant / après : :mod:`core.color_render`). Aperçu seulement, comme les proxys ; le remplacement
    fait partie de l'empreinte du segment (un segment montré ainsi n'est jamais servi pour le vrai rendu)."""
    if not overrides:
        return plan
    video = tuple(
        replace(layer, color_grade=overrides[layer.clip_id]) if layer.clip_id in overrides else layer
        for layer in plan.video_layers
    )
    return replace(plan, video_layers=video)


def segment_plan(
    project,
    start: float,
    end: float,
    *,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
    resolver: PathResolver | None = None,
    timeline_index=None,
    grade_overrides: Mapping[str, object] | None = None,
) -> RenderPlan:
    """Plan fenêtré (et proxifié) d'un segment ``[start, end)``.

    ``timeline_index`` (un :class:`~core.timeline_index.TimelineIndex`) rend la
    construction indépendante du nombre de clips du montage ; ``grade_overrides`` :
    :func:`apply_grade_overrides`.
    """
    plan = build_render_plan(
        project,
        master_gain_db=master_gain_db,
        master_muted=master_muted,
        window=(start, end),
        window_index=timeline_index,
    )
    return apply_grade_overrides(apply_path_resolver(plan, resolver), grade_overrides)


def media_identity(plan: RenderPlan) -> str:
    """Identité (mtime, taille) des fichiers réellement lus par le segment.

    Le chemin d'un proxy est stable (clé de cache) alors que son contenu
    change quand il est régénéré (source modifiée, profil reconstruit) : le
    chemin seul ne suffit pas à invalider les segments déjà rendus. Un
    ``stat`` par couche, sans mémo : le coût est négligeable face à un rendu.
    """
    parts: list[str] = []
    layers: list[Any] = [*plan.video_layers, *plan.audio_layers]
    for entry in getattr(plan, "nested_sequences", ()) or ():
        layers.extend(entry.plan.video_layers)
        layers.extend(entry.plan.audio_layers)
    for layer in layers:
        if getattr(layer, "nested_key", ""):
            continue
        path = layer.source_path
        try:
            stat = os.stat(path)
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def segment_params_hash(
    plan: RenderPlan, project, quality: str, window_end: float | None = None, *, flow_preference: object = "auto"
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
    base = fingerprint_plan(
        plan,
        width=project.width,
        height=project.height,
        fps=project.fps,
        quality=quality,
        flow_preference=flow_preference,
    )
    return f"{base}-{media_identity(plan)}"


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
    flow_preference: object = "auto",
    grade_overrides: Mapping[str, object] | None = None,
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
        timeline_index=timeline_index, grade_overrides=grade_overrides,
    )
    if not (plan.video_layers or getattr(plan, "graphics_layers", ())):
        return None
    key = PreviewSegmentKey(
        clip_id=_owner_clip_id(plan, start),
        start=start,
        end=end,
        quality=quality,
        params_hash=segment_params_hash(plan, project, quality, end, flow_preference=flow_preference),
    )
    return PreviewJob(
        key=key,
        plan=plan,
        width=project.width,
        height=project.height,
        fps=normalize_fps(project.fps),
        quality=quality,
        start=start,
        duration=segment_seconds,
    )


__all__ = [
    "apply_grade_overrides",
    "apply_path_resolver",
    "build_segment_job",
    "media_identity",
    "segment_params_hash",
    "segment_plan",
]
