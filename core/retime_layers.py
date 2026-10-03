"""Préparer les images intermédiaires de **tout** un plan de rendu (séquence racine et séquences imbriquées).

Le graphe d'export (:func:`core.export_engine.ExportEngine._build_filter_complex`) lit, pour chaque clip qui demande un mélange
d'images ou un flux optique, un fichier d'images fabriquées en amont (:mod:`core.retime_prepare`). Ce module les produit : il
parcourt les couches du plan **comme le graphe les parcourt** — mêmes tailles (une séquence imbriquée est composée à la sienne,
mise à l'échelle du rapport export / racine), même mise au cadre, même cadence — pour que l'image qu'il décode soit celle que
l'échantillonnage aurait obtenue.

Il ne fabrique rien pour un clip qui n'en a pas besoin : mode « Échantillonnage », séquence imbriquée (son flux n'est pas un
fichier), ou aucune image intermédiaire (200 %, arrêt…).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from .export_engine import _nested_demand, frame_fit_filter, nested_geometry
from .optical_flow import BackendPreference
from .render_plan import RenderLayer, RenderPlan
from .retime_prepare import (
    PrepareCancelled,
    PrepareReport,
    PrepareRequest,
    PreparedStream,
    needs_preparation,
    prepare,
    windowed_runs,
)
from .time_remapping import TimeInterpolation

DEFAULT_SOURCE_FPS = 30.0
WINDOW_MARGIN_TICKS = 2
"""Ticks ajoutés de part et d'autre d'une fenêtre d'aperçu : la position d'une couche sur la grille des ticks est tronquée
(:func:`core.export_engine._format_offset`), elle peut différer d'un tick du calcul en secondes."""


@dataclass(frozen=True)
class LayerJob:
    """Un clip à préparer et la demande exacte qui le décrit."""

    layer: RenderLayer
    request: PrepareRequest

    @property
    def frames(self) -> int:
        """Images que cette préparation écrira (celles des runs qui demandent une image fabriquée)."""
        return sum(len(item.samples) for item in windowed_runs(self.request, self.request.plan()))


@dataclass(frozen=True)
class PlanPreparation:
    """Ce que la préparation d'un plan a produit."""

    streams: dict[str, PreparedStream]
    """Les flux, par identifiant de clip : à passer à ``_build_filter_complex(prepared=…)``."""
    report: PrepareReport
    """Le bilan agrégé de tous les clips (backend, images fabriquées, replis, confiance…)."""


def _eligible(layer: RenderLayer) -> bool:
    return (
        layer.time_map is not None
        and layer.time_remapping.interpolation is not TimeInterpolation.SAMPLING
        and not layer.nested_key
        and bool(layer.source_path)
    )


def _request(
    layer: RenderLayer, width: int, height: int, fps: float, preference: BackendPreference
) -> PrepareRequest | None:
    if not _eligible(layer) or layer.time_map is None:
        return None
    request = PrepareRequest(
        media_path=layer.source_path,
        time_map=layer.time_map,
        interpolation=layer.time_remapping.interpolation,
        quality=layer.time_remapping.flow_quality,
        fps=float(fps),
        source_fps=float(layer.source_fps) if layer.source_fps > 0 else DEFAULT_SOURCE_FPS,
        source_frames=int(layer.source_frames),
        width=int(width),
        height=int(height),
        conform=frame_fit_filter(width, height),
        preference=preference,
    )
    return request if needs_preparation(request) else None


def local_window(layer: RenderLayer, window: tuple[float, float], fps: float) -> tuple[int, int]:
    """Les ticks locaux au clip (``[début, fin[``) qui recouvrent la fenêtre ``window`` (secondes de la timeline), avec marge."""
    low = math.floor((window[0] - layer.timeline_start) * fps + 1e-6) - WINDOW_MARGIN_TICKS
    high = math.ceil((window[1] - layer.timeline_start) * fps - 1e-6) + WINDOW_MARGIN_TICKS
    return max(0, low), max(0, high)


def layer_jobs(
    plan: RenderPlan,
    width: int,
    height: int,
    fps: float,
    preference: BackendPreference = BackendPreference.AUTO,
    window: tuple[float, float] | None = None,
) -> list[LayerJob]:
    """Les clips du plan (racine, puis séquences imbriquées réellement utilisées) qui ont des images à fabriquer.

    ``window`` (secondes de la timeline) : aperçu d'un segment. Les clips de la racine ne sont préparés que sur les ticks de
    la fenêtre ; ceux d'une séquence imbriquée, dont le temps passe par celui du clip imbriqué, le sont en entier.
    """
    jobs: list[LayerJob] = []
    for layer in plan.video_layers:
        request = _request(layer, width, height, fps, preference)
        if request is not None:
            if window is not None:
                request = replace(request, window=local_window(layer, window, float(fps)))
            jobs.append(LayerJob(layer, request))
    demand, _audio = _nested_demand(plan)
    for entry in tuple(getattr(plan, "nested_sequences", ()) or ()):
        if not demand.get(entry.key, 0):
            continue
        inner_width, inner_height, inner_fps = nested_geometry(entry.plan, width, height, plan)
        for layer in entry.plan.video_layers:
            request = _request(layer, inner_width, inner_height, inner_fps, preference)
            if request is not None:
                jobs.append(LayerJob(layer, request))
    return jobs


def plan_needs_preparation(
    plan: RenderPlan,
    width: int,
    height: int,
    fps: float,
    preference: BackendPreference = BackendPreference.AUTO,
    window: tuple[float, float] | None = None,
) -> bool:
    """Le plan a-t-il au moins une image à fabriquer ? Calcul léger : rien n'est décodé."""
    return bool(layer_jobs(plan, width, height, fps, preference, window))


def prepare_plan(
    plan: RenderPlan,
    width: int,
    height: int,
    fps: float,
    cache,
    *,
    preference: BackendPreference = BackendPreference.AUTO,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    window: tuple[float, float] | None = None,
) -> PlanPreparation:
    """Prépare les images intermédiaires de tous les clips du plan, l'un après l'autre.

    Args:
        plan: le plan de rendu.
        width: largeur de sortie (celle du graphe).
        height: hauteur de sortie.
        fps: cadence de sortie.
        cache: :class:`core.flow_cache.FlowCache`.
        preference: backend de flux optique demandé.
        progress: ``progress(images_faites, images_à_faire)`` sur l'ensemble du plan.
        cancelled: interrogé entre deux images ; vrai interrompt (:class:`~core.retime_prepare.PrepareCancelled`).
        window: ``(début, fin)`` en secondes de la timeline pour l'aperçu d'un segment (seuls ces ticks sont fabriqués) ;
            ``None`` à l'export : tout est préparé.

    Raises:
        PrepareCancelled: annulé (aucun fichier partiel n'est conservé).
        PrepareError: un clip n'a pas pu être préparé exactement (le message dit lequel).
    """
    jobs = layer_jobs(plan, width, height, fps, preference, window)
    total = sum(job.frames for job in jobs)
    streams: dict[str, PreparedStream] = {}
    merged = PrepareReport()
    finished = 0
    for job in jobs:
        base = finished

        def relay(done: int, _count: int, base: int = base) -> None:
            if progress is not None:
                progress(base + done, total)

        stream = prepare(job.request, cache, progress=relay, cancelled=cancelled)
        if stream is None:
            continue
        streams[job.layer.clip_id] = stream
        finished += stream.frames
        _merge(merged, stream.report)
        if progress is not None:
            progress(finished, total)
    merged.reused = bool(streams) and all(stream.report.reused for stream in streams.values())
    return PlanPreparation(streams, merged)


def _merge(total: PrepareReport, part: PrepareReport) -> None:
    """Ajoute le bilan d'un clip au bilan du plan (un backend différent d'un clip à l'autre s'appelle ``mixed``)."""
    if not total.backend:
        total.backend = part.backend
    elif part.backend and part.backend != total.backend:
        total.backend = "mixed"
    total.images += part.images
    total.synthesized += part.synthesized
    total.pairs_computed += part.pairs_computed
    total.pairs_cached += part.pairs_cached
    for name, count in part.fallbacks.items():
        total.fallbacks[name] = total.fallbacks.get(name, 0) + count
    total.confidence_sum += part.confidence_sum
    total.confidence_count += part.confidence_count
    total.seconds += part.seconds


__all__ = [
    "LayerJob",
    "PlanPreparation",
    "PrepareCancelled",
    "layer_jobs",
    "local_window",
    "plan_needs_preparation",
    "prepare_plan",
]
