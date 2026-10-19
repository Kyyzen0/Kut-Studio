"""La composition nodale dans ``describe`` : un graphe de nœuds (sources, transform, fusion, masques, incrustation, effets,
étalonnage, sortie) et ses liens, plus la durée et l'animation des masques.

Les sources d'une composition sont des **médias du projet** (``asset_id``) ou des calques graphiques : aucun nœud ne contient un
clip ni une séquence, donc la description ne récurse pas ; un graphe est lu en deux listes (``nodes`` puis ``links``), quelle que
soit sa profondeur. Chaque nœud porte son ``type`` (``media``, ``graphic``, ``solid``, ``transform``, ``merge``, ``mask``, ``key``,
``effects``, ``grade``, ``output``), dérivé de la classe du nœud, puis les champs du modèle de cette classe.
"""

from __future__ import annotations

from typing import Any

from ..composition import (
    EffectsNode, GradeNode, GraphicNode, KeyNode, MaskNode, MediaNode, MergeNode, SolidNode, TransformNode, kind_of,
)
from . import describe_color, describe_shapes as shapes
from .describe_values import enum, number, text


def _node(item: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"id": text(item.id), "type": kind_of(item)}
    if isinstance(item, MediaNode):
        data.update(
            asset_id=text(item.asset_id) or None, start=number(item.start), source_in=number(item.source_in),
            source_out=number(item.source_out), label=text(item.label), gain_db=number(item.gain_db), muted=bool(item.muted),
            fill=bool(item.fill), pan_x=number(item.pan_x), pan_y=number(item.pan_y),
        )
    elif isinstance(item, GraphicNode):
        data.update(
            graphic=shapes.graphic(item.graphic), start=number(item.start), duration=number(item.duration),
            transform=shapes.transform(item.transform), transform_keyframes=shapes.keyframes(item.transform_keyframes),
            animation=shapes.keyframes(item.animation), label=text(item.label), masks=shapes.masks(item.masks),
        )
    elif isinstance(item, SolidNode):
        data.update(color=text(item.color), label=text(item.label))
    elif isinstance(item, TransformNode):
        data.update(transform=shapes.transform(item.transform), keyframes=shapes.keyframes(item.keyframes), label=text(item.label))
    elif isinstance(item, MergeNode):
        data.update(blend=enum(item.blend), opacity=number(item.opacity), label=text(item.label))
    elif isinstance(item, MaskNode):
        data.update(masks=shapes.masks(item.masks), label=text(item.label))
    elif isinstance(item, KeyNode):
        data.update(key=shapes.chroma_key(item.key), label=text(item.label))
    elif isinstance(item, EffectsNode):
        data.update(effects=shapes.effects(item.effects), label=text(item.label))
    elif isinstance(item, GradeNode):
        data.update(grade=describe_color.color_graph(item.grade), label=text(item.label))
    return data


def composition(item: Any) -> dict[str, Any] | None:
    """La composition d'un clip (``Clip.composition``), ``null`` pour un clip ordinaire."""
    if item is None:
        return None
    graph = item.graph
    return {
        "graph": {"nodes": [_node(node) for node in graph.nodes], "links": describe_color.links(graph)},
        "duration": number(item.duration), "animation": shapes.keyframes(item.animation),
    }
