"""L'étalonnage dans ``describe`` : réglage, courbes, roues, LUT, graphe de nœuds, préréglages du projet.

Un clip porte un ``ColorGrade`` seul **ou** un graphe de nœuds (:class:`~core.color_nodes.ColorNodeGraph`). La description est
**toujours** un graphe (``nodes`` + ``links``) : un réglage seul est le nœud ``n1``, comme le voient les verbes d'étalonnage
(:func:`core.color_nodes.as_graph`) ; ``node`` absent d'un verbe désigne le premier nœud. ``color_grade`` vaut ``null`` pour un clip
jamais étalonné. Rien n'est écrit, aucune récursion : un graphe est une liste de nœuds et une liste de liens.
"""

from __future__ import annotations

from typing import Any

from ..color_grading import ColorGrade
from ..color_nodes import ColorMixer, ColorNodeGraph, as_graph
from . import describe_shapes as shapes
from .describe_values import enum, number, text


def _wheel(item: Any) -> dict[str, Any]:
    return {"r": number(item.r), "g": number(item.g), "b": number(item.b), "y": number(item.y)}


def _curve(item: Any) -> list[list[Any]]:
    return [[number(x), number(y)] for x, y in item.points]


def _lut(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {"path": text(item.path), "title": text(item.title), "sha1": text(item.sha1), "size": number(item.size),
            "missing": bool(item.missing)}


def grade(item: ColorGrade) -> dict[str, Any]:
    """Un réglage complet (``set_color_grade``, ``set_color_curve``, ``set_color_wheel``…)."""
    curves = item.curves
    return {
        "exposure": number(item.exposure), "contrast": number(item.contrast), "saturation": number(item.saturation),
        "temperature": number(item.temperature), "hue": number(item.hue), "shadows": number(item.shadows),
        "highlights": number(item.highlights),
        "curves": {"master": _curve(curves.master), "red": _curve(curves.red), "green": _curve(curves.green),
                   "blue": _curve(curves.blue)},
        "lut": _lut(item.lut), "enabled": bool(item.enabled),
        "lift": _wheel(item.lift), "gamma": _wheel(item.gamma), "gain": _wheel(item.gain), "offset": _wheel(item.offset),
    }


def _qualifier(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "enabled": bool(item.enabled), "use_hue": bool(item.use_hue), "hue_center": number(item.hue_center),
        "hue_width": number(item.hue_width), "hue_soft": number(item.hue_soft), "use_sat": bool(item.use_sat),
        "sat_low": number(item.sat_low), "sat_high": number(item.sat_high), "sat_soft": number(item.sat_soft),
        "use_lum": bool(item.use_lum), "lum_low": number(item.lum_low), "lum_high": number(item.lum_high),
        "lum_soft": number(item.lum_soft), "invert": bool(item.invert),
    }


def _node(item: Any) -> dict[str, Any]:
    if isinstance(item, ColorMixer):
        return {"id": text(item.id), "type": "mixer", "kind": enum(item.kind)}
    return {
        "id": text(item.id), "type": "corrector", "grade": grade(item.grade), "label": text(item.label),
        "qualifier": _qualifier(item.qualifier), "windows": shapes.masks(item.windows), "blur": number(item.blur),
        "sharpen": number(item.sharpen),
    }


def links(graph: Any) -> list[dict[str, Any]]:
    return [{"source": text(link.source), "target": text(link.target), "port": number(link.port)} for link in graph.links]


def color_graph(value: object) -> dict[str, Any] | None:
    """L'étalonnage d'un clip (``Clip.color_grade``) ou d'un nœud ``grade`` de composition, ``null`` si absent ou illisible."""
    if not isinstance(value, (ColorGrade, ColorNodeGraph)):
        return None
    graph = as_graph(value)
    return {"nodes": [_node(item) for item in graph.nodes], "links": links(graph)}


def presets(items: Any) -> list[dict[str, Any]]:
    """Les préréglages d'étalonnage du projet (``Project.color_presets``)."""
    return [
        {"id": text(item.id), "name": text(item.name), "description": text(item.description), "category": enum(item.category),
         "grade": grade(item.grade), "builtin": bool(item.builtin)}
        for item in items or ()
    ]
