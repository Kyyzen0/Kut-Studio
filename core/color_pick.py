"""Pipette du qualificateur : la couleur qui arrive au nœud courant sous le clic, et le qualifieur qui l'entoure.

Le qualifieur d'un nœud lit **son entrée** (:mod:`core.color_qualifier`) : la pipette prend donc la couleur du média
sous le clic (moyenne d'un carré de :data:`SAMPLE_SIZE` pixels, un pixel seul est trop bruité), à l'instant source de
la tête de lecture, passée par les nœuds qui **précèdent** le nœud courant (:func:`upstream_graph`) — par FFmpeg et la
chaîne de l'export, comme tout le reste. Les effets du clip n'y passent pas (un flou sur un carré de 5 pixels ne
voudrait rien dire) : la couleur est celle qu'ils reçoivent.

:func:`qualifier_around` en fait des plages : la teinte à ±15° (douceur 20°), la saturation et la luminance autour de
leurs valeurs ; un gris n'a pas de teinte, seules saturation et luminance le prennent. ``extend`` (Maj + clic) élargit
les plages du qualifieur existant jusqu'à la nouvelle couleur.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import replace

import numpy as np

from .color_nodes import ColorNodeGraph
from .color_qualifier import LUMA_709, Qualifier

SAMPLE_SIZE = 5
HUE_HALF_WIDTH = 15.0
HUE_SOFT = 20.0
RANGE_HALF = 0.12
RANGE_SOFT = 0.08
GREY_CHROMA = 0.04
PICK_TIMEOUT_SECONDS = 20.0


class ColorPickError(RuntimeError):
    """La couleur n'a pas pu être lue (média absent, FFmpeg absent ou en échec)."""


def upstream_graph(graph: ColorNodeGraph, node_id: str) -> ColorNodeGraph | None:
    """Les nœuds qui donnent l'entrée de ``node_id`` (sa source et tout ce qui la précède) ; ``None`` : il lit l'image
    du clip."""
    source = graph.input_of(node_id)
    if source is None:
        return None
    keep = {source}
    pending = [source]
    while pending:
        for link in graph.inputs(pending.pop()):
            if link.source not in keep:
                keep.add(link.source)
                pending.append(link.source)
    return ColorNodeGraph(nodes=tuple(node for node in graph.nodes if node.id in keep),
                          links=tuple(link for link in graph.links if link.source in keep and link.target in keep))


def sample_command(ffmpeg: str, path: str, source_time: float, x: float, y: float, chain: str,
                   size: int = SAMPLE_SIZE) -> list[str]:
    """Commande FFmpeg : l'image du média à ``source_time``, le carré centré sur ``(x, y)`` (pixels du média, borné à
    l'image), passé dans ``chain``, moyenné en un pixel RVB 8 bits."""
    half = size // 2
    left = f"min(max(0,{int(round(x)) - half}),iw-{size})"
    top = f"min(max(0,{int(round(y)) - half}),ih-{size})"
    filters = [f"crop={size}:{size}:x='{left}':y='{top}'"]
    if chain:
        filters.append(chain)
    filters += ["scale=1:1:flags=area", "format=rgb24"]
    return [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, float(source_time)):.6f}",
            "-i", path, "-frames:v", "1", "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]


def sample_node_input(path: str, source_time: float, x: float, y: float, upstream: ColorNodeGraph | None, *,
                      ffmpeg: str | None = None) -> tuple[float, float, float]:
    """Couleur (R, V, B en 0..1) qui arrive au nœud sous ``(x, y)`` ; :class:`ColorPickError` si elle n'a pas pu
    être lue."""
    from .export_engine import _build_color_grade_filters
    from .process_supervisor import supervised_run
    from .tool_paths import find_media_tool

    tool = ffmpeg or find_media_tool("ffmpeg")
    if not tool:
        raise ColorPickError("FFmpeg introuvable.")
    chain = _build_color_grade_filters(upstream, tag="pick") if upstream is not None else ""
    command = sample_command(tool, path, source_time, x, y, chain)
    try:
        done = supervised_run(command, capture_output=True, timeout=PICK_TIMEOUT_SECONDS)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ColorPickError(str(error)) from error
    if done.returncode != 0 or len(done.stdout) != 3:
        raise ColorPickError(done.stderr.decode("utf-8", "replace").strip() or "Aucune image lue.")
    red, green, blue = done.stdout
    return red / 255.0, green / 255.0, blue / 255.0


def _measures(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """``(teinte en degrés, chroma, luminance)`` d'une couleur (mêmes définitions que la clé)."""
    from .color_qualifier import _hue_degrees

    r, g, b = (np.asarray(float(value)) for value in rgb)
    high, low = max(rgb), min(rgb)
    chroma = high - low
    hue = float(_hue_degrees(r, g, b, np.asarray(high), np.asarray(chroma)))
    luma = sum(weight * value for weight, value in zip(LUMA_709, rgb))
    return hue, chroma, luma


def _range(center: float, low_bound: float = 0.0, high_bound: float = 1.0) -> tuple[float, float]:
    return max(low_bound, center - RANGE_HALF), min(high_bound, center + RANGE_HALF)


def qualifier_around(rgb: tuple[float, float, float], base: Qualifier | None = None, *,
                     extend: bool = False) -> Qualifier:
    """Le qualifieur qui prend ``rgb`` ; ``extend`` : celui de ``base``, élargi jusqu'à ``rgb``."""
    hue, chroma, luma = _measures(rgb)
    grey = chroma < GREY_CHROMA
    if not extend or base is None or not base.enabled:
        sat_low, sat_high = _range(chroma)
        lum_low, lum_high = _range(luma)
        return Qualifier(
            enabled=True, use_hue=not grey, hue_center=hue % 360.0, hue_width=2 * HUE_HALF_WIDTH, hue_soft=HUE_SOFT,
            use_sat=True, sat_low=sat_low, sat_high=sat_high, sat_soft=RANGE_SOFT,
            use_lum=True, lum_low=lum_low, lum_high=lum_high, lum_soft=RANGE_SOFT,
            invert=base.invert if base is not None else False,
        )
    widened = base
    if base.use_hue and base.hue_width < 360.0 and not grey:
        center, width = _hue_covering(base.hue_center, base.hue_width, hue)
        widened = replace(widened, hue_center=center, hue_width=width)
    sat_low, sat_high = _range(chroma)
    lum_low, lum_high = _range(luma)
    return replace(widened, sat_low=min(base.sat_low, sat_low), sat_high=max(base.sat_high, sat_high),
                   lum_low=min(base.lum_low, lum_low), lum_high=max(base.lum_high, lum_high))


def _hue_covering(center: float, width: float, hue: float) -> tuple[float, float]:
    """Le plus petit arc qui contient l'arc ``center ± width/2`` et ``hue`` (± :data:`HUE_HALF_WIDTH`)."""
    offset = (hue - center + 180.0) % 360.0 - 180.0                 # -180..180 : de quel côté est la nouvelle teinte
    half = width / 2.0
    low = min(-half, offset - HUE_HALF_WIDTH)
    high = max(half, offset + HUE_HALF_WIDTH)
    return (center + (low + high) / 2.0) % 360.0, min(360.0, high - low)


def canvas_to_media(point: tuple[float, float], layer_matrix, fit) -> tuple[float, float] | None:
    """Pixel du média sous ``point`` (pixels du cadre) : matrice calque → cadre inversée, puis la place du média dans le
    calque (:class:`core.tracking_motion.FitBox`) ; ``None`` hors de l'image du clip."""
    a, b, c, d, e, f = layer_matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        return None
    x, y = point[0] - e, point[1] - f
    lx = (d * x - c * y) / determinant
    ly = (-b * x + a * y) / determinant
    mx = (lx - fit.offset_x) / fit.scale_x
    my = (ly - fit.offset_y) / fit.scale_y
    width, height = fit.width / fit.scale_x, fit.height / fit.scale_y
    if not (0.0 <= mx < width and 0.0 <= my < height) or math.isnan(mx):
        return None
    return mx, my
