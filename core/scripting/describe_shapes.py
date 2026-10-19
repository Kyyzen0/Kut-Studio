"""Les formes que ``describe`` réutilise partout : transform, calque graphique, images clés, effets, masques, compositing.

Un clip, un nœud de composition ou un préréglage décrivent leurs masques, leurs images clés ou leur transform de la **même**
façon : une seule fonction par forme. Les clés gardent le nom du champ du modèle ; une clé qui n'en est pas un (``nodes``) est dans
:data:`~core.scripting.describe_coverage.DERIVED`. Aucune fonction ne récurse : une forme contient au plus des listes de valeurs.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .describe_values import enum, number, optional_text, text, value


def transform(item: Any) -> dict[str, Any]:
    return {
        "position_x": number(item.position_x), "position_y": number(item.position_y),
        "scale": number(item.scale), "rotation": number(item.rotation), "opacity": number(item.opacity),
        "anchor_x": number(item.anchor_x), "anchor_y": number(item.anchor_y),
        "scale_x": number(item.scale_x), "scale_y": number(item.scale_y), "skew": number(item.skew),
        "flip_h": bool(item.flip_h), "flip_v": bool(item.flip_v), "fill": bool(item.fill),
        "pan_x": number(item.pan_x), "pan_y": number(item.pan_y),
    }


def graphic(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "type": enum(item.type), "text": text(item.text), "source_path": optional_text(item.source_path),
        "width": number(item.width), "height": number(item.height), "visible": bool(item.visible),
        "locked": bool(item.locked), "parent_id": optional_text(item.parent_id),
        "group_id": optional_text(item.group_id), "z_order": number(item.z_order),
    }


def keyframe(item: Any) -> dict[str, Any]:
    """Une image clé : ``time_seconds`` est local au clip (``at`` des verbes) ; ``property_name`` est la ``property`` des verbes."""
    return {
        "property_name": text(item.property_name), "time_seconds": number(item.time_seconds), "value": value(item.value),
        "interpolation": enum(item.interpolation), "in_slope": value(item.in_slope), "out_slope": value(item.out_slope),
        "tangent_mode": enum(item.tangent_mode), "id": text(item.id),
    }


def keyframes(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [keyframe(item) for item in items or ()]


def effect(item: Any) -> dict[str, Any]:
    """Un effet visuel ou audio (``ClipEffect`` / ``AudioEffect``) : ``id`` est l'``effect`` / ``insert`` des verbes."""
    return {
        "id": text(item.id), "type": enum(item.type), "enabled": bool(item.enabled),
        "params": {text(name): number(parameter) for name, parameter in dict(item.params).items()},
    }


def effects(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [effect(item) for item in items or ()]


def mask(item: Any) -> dict[str, Any]:
    """Un masque (de compositing, de fenêtre d'étalonnage ou de nœud de composition)."""
    points = [{"x": number(x), "y": number(y)} for x, y in item.points]
    handles = [
        {"in_dx": number(a), "in_dy": number(b), "out_dx": number(c), "out_dy": number(d)} for a, b, c, d in item.handles
    ]
    path = enum(item.shape) == "path" and len(points) == len(handles)
    return {
        "shape": enum(item.shape), "position_x": number(item.position_x), "position_y": number(item.position_y),
        "width": number(item.width), "height": number(item.height), "rotation": number(item.rotation),
        "feather": number(item.feather), "expansion": number(item.expansion), "opacity": number(item.opacity),
        "inverted": bool(item.inverted),
        "keyframes": [
            {"property_name": text(k.property_name), "time_seconds": number(k.time_seconds), "value": number(k.value)}
            for k in item.keyframes
        ],
        "mode": enum(item.mode), "points": points, "name": text(item.name), "id": text(item.id), "handles": handles,
        "nodes": [{**anchor, **handle} for anchor, handle in zip(points, handles)] if path else None,
    }


def masks(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [mask(item) for item in items or ()]


def chroma_key(item: Any) -> dict[str, Any]:
    return {
        "enabled": bool(item.enabled), "color": text(item.color), "tolerance": number(item.tolerance),
        "softness": number(item.softness), "spill_suppression": number(item.spill_suppression),
    }


def compositing(item: Any) -> dict[str, Any]:
    return {"masks": masks(item.masks), "chroma_key": chroma_key(item.chroma_key), "blend_mode": enum(item.blend_mode)}


__all__ = ["chroma_key", "compositing", "effect", "effects", "graphic", "keyframe", "keyframes", "mask", "masks", "transform"]
