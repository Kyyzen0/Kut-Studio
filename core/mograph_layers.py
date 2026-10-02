"""Opérations d'édition des calques motion graphics (pures, sans Qt).

Comme :mod:`core.keyframe_editing`, chaque fonction modifie le
:class:`~core.project_model.Project` en place ; l'interface enregistre
ensuite **une** entrée d'historique (instantané) : toutes ces actions sont
annulables sans code dédié.

Pile et hiérarchie
------------------

Un calque est un clip d'une piste ``graphics``. Sa place dans la pile est
``(piste, z_order)`` (voir :func:`core.mograph_scene.stack_key`) : les
calques d'une même piste peuvent se chevaucher dans le temps, ``z_order``
les ordonne. Un groupe (``GraphicType.GROUP``) contient les calques dont
``group_id`` le désigne ; un calque suit son parent (``parent_id``) ou, à
défaut, son groupe. Les cycles sont refusés avec la même détection que les
séquences imbriquées (:mod:`core.graph_cycles`).
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace

from . import graph_cycles
from .animation import Keyframe, normalize_time
from .compositing import mask_property_id, new_mask_id
from .graphics import (
    CONTAINER_TYPES,
    GraphicOverlay,
    GraphicType,
    ShapeKind,
    add_graphic_clip,
    ensure_graphics_track,
    next_z_order,
)
from .mograph_scene import GraphicsScene, mat_close, stack_key, transform_from_world
from .project_model import Clip, MediaAsset, Project, Track
from .visual_effects import TRANSFORM_PROPERTIES, ClipTransform, copy_keyframe


class LayerError(ValueError):
    """Opération refusée (verrou, cycle, calque introuvable…)."""


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------


def find_clip(project: Project, clip_id: str) -> tuple[Track, Clip]:
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return track, clip
    raise LayerError(f"Calque introuvable : {clip_id!r}.")


def find_layer(project: Project, clip_id: str) -> tuple[Track, Clip]:
    track, clip = find_clip(project, clip_id)
    if track.type != "graphics" or not isinstance(clip.graphic, GraphicOverlay):
        raise LayerError("Ce clip n'est pas un calque graphique.")
    return track, clip


def _require_editable(project: Project, clip_id: str) -> tuple[Track, Clip]:
    track, clip = find_layer(project, clip_id)
    if track.locked:
        raise LayerError(f"La piste « {track.name} » est verrouillée.")
    if clip.graphic.locked:
        raise LayerError(f"Le calque « {clip.label} » est verrouillé.")
    return track, clip


def layer_clips(project: Project) -> list[tuple[int, Track, Clip]]:
    """Calques de la séquence active, du bas vers le haut de la pile."""
    result = [
        (index, track, clip)
        for index, track in enumerate(project.tracks)
        if track.type == "graphics"
        for clip in track.clips
        if isinstance(clip.graphic, GraphicOverlay)
    ]
    result.sort(key=lambda item: (item[0], item[2].graphic.z_order, item[2].timeline_start, item[2].id))
    return result


def _set_graphic(clip: Clip, **changes) -> None:
    clip.graphic = replace(clip.graphic, **changes)


# ---------------------------------------------------------------------------
# Arbre affiché par le panneau Calques
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerNode:
    """Ligne du panneau Calques (ordre d'affichage : haut de la pile en premier)."""

    clip_id: str
    name: str
    type: GraphicType
    depth: int
    parent_id: str
    group_id: str
    visible: bool
    locked: bool
    start: float
    end: float
    track_id: str
    is_group: bool
    children: tuple[str, ...] = field(default_factory=tuple)


def layer_tree(project: Project, *, root_group: str = "") -> list[LayerNode]:
    """Calques à afficher, groupes dépliés, haut de la pile d'abord.

    ``root_group`` : n'affiche que l'intérieur d'un groupe (« entrer dans le
    groupe »).
    """
    clips = layer_clips(project)
    by_id = {clip.id: (track, clip) for _index, track, clip in clips}
    group_ids = {cid for cid, (_t, c) in by_id.items() if c.graphic.type == GraphicType.GROUP}
    containment = {cid: c.graphic.group_id for cid, (_t, c) in by_id.items() if c.graphic.group_id in group_ids}
    for cycle in graph_cycles.find_cycles({k: {v} for k, v in containment.items()}):
        for cid in cycle:
            containment.pop(cid, None)
    children: dict[str, list[str]] = {}
    for _index, _track, clip in clips:  # bas → haut
        children.setdefault(containment.get(clip.id, ""), []).append(clip.id)
    result: list[LayerNode] = []

    def walk(container: str, depth: int) -> None:
        for cid in reversed(children.get(container, [])):  # haut d'abord
            track, clip = by_id[cid]
            graphic = clip.graphic
            kids = tuple(reversed(children.get(cid, [])))
            result.append(LayerNode(
                clip_id=cid, name=clip.label or graphic.type.value, type=graphic.type, depth=depth,
                parent_id=graphic.parent_id, group_id=containment.get(cid, ""),
                visible=graphic.visible, locked=graphic.locked,
                start=clip.timeline_start, end=clip.timeline_start + clip.duration,
                track_id=track.id, is_group=graphic.type == GraphicType.GROUP, children=kids,
            ))
            if graphic.type == GraphicType.GROUP:
                walk(cid, depth + 1)

    walk(root_group, 0)
    return result


# ---------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------


def add_layer(
    project: Project,
    kind: GraphicType | str,
    *,
    at: float,
    duration: float = 5.0,
    shape: ShapeKind | str = ShapeKind.RECTANGLE,
    source_path: str = "",
    group_id: str = "",
) -> Clip:
    """Ajoute un calque (texte, forme, aplat, image, contrôleur, adjustment…)."""
    clip = add_graphic_clip(
        project, kind, timeline_start=at, duration=duration, shape=shape, source_path=source_path,
    )
    if group_id:
        find_layer(project, group_id)
        _set_graphic(clip, group_id=group_id)
    return clip


# ---------------------------------------------------------------------------
# Scène (pour garder le rendu visuel lors des changements de hiérarchie)
# ---------------------------------------------------------------------------


def scene_for_project(project: Project) -> GraphicsScene:
    """Scène de toute la séquence active (sans fenêtre, sans mesure Qt)."""
    from .render_plan import _graphic_layer

    layers = []
    context = None
    for index, track in enumerate(project.tracks):
        if track.type not in ("graphics", "video"):
            continue
        for clip in track.clips:
            if clip.sequence_id:
                continue
            if track.type == "graphics" and not isinstance(clip.graphic, GraphicOverlay):
                continue
            state = None
            if getattr(clip, "tracking", None) is not None:
                # Poignées du viewer : même animation que le rendu (tracking compris).
                from .tracking_bindings import TrackingContext, effective_clip_state

                context = context or TrackingContext(project)
                state = effective_clip_state(clip, context)
            layers.append(_graphic_layer(
                clip, track, index, role="draw" if track.type == "graphics" else "rig", state=state,
            ))
    return GraphicsScene(layers, project.width, project.height)


def transform_graph(project: Project) -> dict[str, set[str]]:
    """Graphe ``calque → parents de transform`` (parent explicite et groupe)."""
    graph: dict[str, set[str]] = {}
    for _index, _track, clip in layer_clips(project):
        edges = {ref for ref in (clip.graphic.parent_id, clip.graphic.group_id) if ref}
        graph[clip.id] = edges
    return graph


def _rebase(project: Project, clip: Clip, change, at_time: float) -> None:
    """Applique ``change`` (hiérarchie) sans déplacer visuellement le calque.

    Le transform statique est recalculé à ``at_time`` pour que la matrice
    du monde reste la même ; les images-clés de position / rotation /
    échelle sont décalées d'autant (exact pour un parent sans rotation
    animée ; sinon l'écart n'existe qu'en dehors de ``at_time``).
    """
    before = scene_for_project(project)
    world = before.world_matrix(clip.id, at_time)
    old_transform = clip.transform
    change()
    after = scene_for_project(project)
    parent = after.parent_of(clip.id)
    if parent:
        parent_eval = after.evaluate(parent, at_time)
        parent_world, parent_box = parent_eval.world, parent_eval.box
    else:
        parent_world, parent_box = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (after.width, after.height)
    if mat_close(after.world_matrix(clip.id, at_time), world, 1e-6):
        return
    box = after.evaluate(clip.id, at_time).box
    try:
        new_transform = transform_from_world(
            world, parent_world, box, parent_box, (after.width, after.height), old_transform,
        )
    except ValueError:
        return  # parent à échelle nulle : le calque garde ses valeurs
    clip.transform = new_transform
    shifted = []
    for kf in clip.transform_keyframes:
        name = kf.property_name
        old, new = getattr(old_transform, name), getattr(new_transform, name)
        if name in ("position_x", "position_y", "rotation", "skew") and old != new:
            kf = copy_keyframe(kf, value=TRANSFORM_PROPERTIES[name].clamp(kf.value + (new - old)))
        elif name in ("scale_x", "scale_y") and old != new and old:
            kf = copy_keyframe(kf, value=TRANSFORM_PROPERTIES[name].clamp(kf.value * new / old))
        shifted.append(kf)
    clip.transform_keyframes = shifted


# ---------------------------------------------------------------------------
# Parentage
# ---------------------------------------------------------------------------


def can_parent(project: Project, child_id: str, parent_id: str) -> bool:
    try:
        _check_parent(project, child_id, parent_id)
    except LayerError:
        return False
    return True


def _check_parent(project: Project, child_id: str, parent_id: str) -> None:
    find_layer(project, child_id)
    parent_track, parent_clip = find_clip(project, parent_id)
    if parent_track.type not in ("graphics", "video") or parent_clip.sequence_id:
        raise LayerError("Le parent doit être un calque graphique ou un clip vidéo.")
    graph = transform_graph(project)
    graph[child_id] = {ref for ref in graph.get(child_id, set()) if ref != find_layer(project, child_id)[1].graphic.parent_id}
    if graph_cycles.would_create_cycle(graph, child_id, parent_id):
        raise LayerError("Ce parentage créerait une boucle (un calque ne peut pas suivre son propre descendant).")


def set_parent(project: Project, child_id: str, parent_id: str, *, at_time: float = 0.0, keep_visual: bool = True) -> Clip:
    """Fait suivre ``parent_id`` à ``child_id`` (refus en cas de cycle)."""
    _track, clip = _require_editable(project, child_id)
    if not parent_id:
        return clear_parent(project, child_id, at_time=at_time, keep_visual=keep_visual)
    _check_parent(project, child_id, parent_id)
    if clip.graphic.parent_id == parent_id:
        return clip

    def change() -> None:
        _set_graphic(clip, parent_id=parent_id)

    if keep_visual:
        _rebase(project, clip, change, at_time)
    else:
        change()
    return clip


def clear_parent(project: Project, child_id: str, *, at_time: float = 0.0, keep_visual: bool = True) -> Clip:
    _track, clip = _require_editable(project, child_id)
    if not clip.graphic.parent_id:
        return clip

    def change() -> None:
        _set_graphic(clip, parent_id="")

    if keep_visual:
        _rebase(project, clip, change, at_time)
    else:
        change()
    return clip


def parent_candidates(project: Project, child_id: str) -> list[tuple[str, str]]:
    """``(identifiant, nom)`` des parents possibles (sans cycle) pour l'interface."""
    result = []
    for track in project.tracks:
        if track.type not in ("graphics", "video"):
            continue
        for clip in track.clips:
            if clip.id == child_id or clip.sequence_id:
                continue
            if track.type == "graphics" and not isinstance(clip.graphic, GraphicOverlay):
                continue
            if can_parent(project, child_id, clip.id):
                result.append((clip.id, clip.label or clip.id))
    return result


# ---------------------------------------------------------------------------
# Groupes
# ---------------------------------------------------------------------------


def group_layers(project: Project, clip_ids: list[str], *, name: str = "Groupe") -> Clip:
    """Regroupe des calques (même conteneur) ; le groupe n'est pas transformé."""
    ids = list(dict.fromkeys(clip_ids))
    if not ids:
        raise LayerError("Aucun calque à grouper.")
    members = [_require_editable(project, cid) for cid in ids]
    containers = {clip.graphic.group_id for _track, clip in members}
    if len(containers) != 1:
        raise LayerError("Les calques à grouper doivent être au même niveau.")
    if any(clip.graphic.type == GraphicType.ADJUSTMENT for _track, clip in members):
        raise LayerError("Un calque d'effets s'applique à toute la composition : il ne peut pas être groupé.")
    container = containers.pop()
    scene = scene_for_project(project)
    if container:
        box = scene.evaluate(container, members[0][1].timeline_start).box
    else:
        box = (float(project.width), float(project.height))
    track_index = {track.id: index for index, track in enumerate(project.tracks)}
    top_track, top_clip = max(members, key=lambda item: (track_index[item[0].id], item[1].graphic.z_order))
    start = min(clip.timeline_start for _t, clip in members)
    end = max(clip.timeline_start + clip.duration for _t, clip in members)
    group = add_graphic_clip(
        project, GraphicType.GROUP, timeline_start=start, duration=end - start,
        graphic=GraphicOverlay(type=GraphicType.GROUP, text="", width=int(box[0]), height=int(box[1]),
                               group_id=container, shadow_offset_x=0, shadow_offset_y=0),
        track=top_track,
    )
    group.label = name
    # Le groupe prend la place de son membre le plus haut dans la pile.
    ordered = [c for c in _track_order(top_track) if c.id != group.id]
    ordered.insert(ordered.index(top_clip) + 1, group)
    _renumber_track(top_track, keep_order_of=ordered)
    for _track, clip in members:
        _set_graphic(clip, group_id=group.id)
    return group


def ungroup(project: Project, group_id: str) -> list[Clip]:
    """Dissout un groupe ; ses membres reviennent au niveau du groupe.

    Un groupe neutre (sans transform, opacité, masques ni effets) disparaît.
    Sinon il devient un **contrôleur** (null) dont les membres héritent : le
    rendu reste identique, rien ne saute.
    """
    track, group = _require_editable(project, group_id)
    if group.graphic.type != GraphicType.GROUP:
        raise LayerError("Ce calque n'est pas un groupe.")
    members = [clip for _i, _t, clip in layer_clips(project) if clip.graphic.group_id == group_id]
    neutral = (
        group.transform == ClipTransform()
        and not group.transform_keyframes
        and not group.compositing.masks
        and not group.effects
    )
    container = group.graphic.group_id
    for clip in members:
        if neutral or clip.graphic.parent_id:
            _set_graphic(clip, group_id=container)
        else:
            _set_graphic(clip, group_id=container, parent_id=group_id)
    if neutral:
        track.clips = [c for c in track.clips if c.id != group_id]
        project.media_assets[:] = [a for a in project.media_assets if a.id != group.asset_id]
        for clip in members:
            if clip.graphic.parent_id == group_id:
                _set_graphic(clip, parent_id="")
    else:
        _set_graphic(group, type=GraphicType.NULL)
        group.label = f"{group.label} (contrôleur)"
    return members


def move_into_group(project: Project, clip_id: str, group_id: str, *, at_time: float = 0.0) -> Clip:
    """Place un calque dans un groupe (``""`` = sortir au niveau racine)."""
    _track, clip = _require_editable(project, clip_id)
    if group_id:
        _gt, group = find_layer(project, group_id)
        if group.graphic.type != GraphicType.GROUP:
            raise LayerError("La cible n'est pas un groupe.")
        if clip.graphic.type == GraphicType.ADJUSTMENT:
            raise LayerError("Un calque d'effets ne peut pas être groupé.")
        graph = transform_graph(project)
        graph[clip_id] = {clip.graphic.parent_id} - {""}
        if graph_cycles.would_create_cycle(graph, clip_id, group_id):
            raise LayerError("Un groupe ne peut pas se contenir lui-même.")
    if clip.graphic.group_id == group_id:
        return clip
    _rebase(project, clip, lambda: _set_graphic(clip, group_id=group_id), at_time)
    return clip


# ---------------------------------------------------------------------------
# Ordre, nom, visibilité, verrou
# ---------------------------------------------------------------------------


def _track_order(track: Track) -> list[Clip]:
    return sorted(
        (c for c in track.clips if isinstance(c.graphic, GraphicOverlay)),
        key=lambda c: (c.graphic.z_order, c.timeline_start, c.id),
    )


def _renumber_track(track: Track, *, keep_order_of: list[Clip]) -> None:
    for rank, clip in enumerate(keep_order_of):
        if clip.graphic.z_order != rank:
            _set_graphic(clip, z_order=rank)


def siblings(project: Project, clip_id: str) -> list[Clip]:
    """Calques du même conteneur, du bas vers le haut."""
    _track, clip = find_layer(project, clip_id)
    container = clip.graphic.group_id
    return [c for _i, _t, c in layer_clips(project) if c.graphic.group_id == container]


def reorder_layer(project: Project, clip_id: str, new_index: int) -> None:
    """Place le calque au rang ``new_index`` (0 = bas) parmi ses frères.

    Le calque rejoint la piste de son nouveau voisin et les rangs
    ``z_order`` de cette piste sont renumérotés : l'ordre des autres
    pistes ne change pas.
    """
    track, clip = _require_editable(project, clip_id)
    order = [c for c in siblings(project, clip_id) if c.id != clip_id]
    index = max(0, min(len(order), int(new_index)))
    order.insert(index, clip)
    neighbour = order[index - 1] if index > 0 else (order[index + 1] if len(order) > 1 else None)
    if neighbour is not None:
        target_track, _n = find_clip(project, neighbour.id)
        if target_track.locked:
            raise LayerError(f"La piste « {target_track.name} » est verrouillée.")
        if target_track is not track:
            track.clips = [c for c in track.clips if c.id != clip_id]
            clip.track_id = target_track.id
            target_track.clips.append(clip)
            target_track.clips.sort(key=lambda c: (c.timeline_start, c.id))
            track = target_track
    # Seul l'ordre des frères compte (un groupe compose ses membres à part) :
    # on réattribue les emplacements des frères de la piste dans l'ordre voulu,
    # les autres calques de la piste gardent leur emplacement.
    wanted = [c.id for c in order if c.track_id == track.id]
    current = _track_order(track)
    slots = [i for i, c in enumerate(current) if c.id in set(wanted)]
    by_id = {c.id: c for c in current}
    arranged = list(current)
    for slot, cid in zip(slots, wanted):
        arranged[slot] = by_id[cid]
    _renumber_track(track, keep_order_of=arranged)


def move_layer(project: Project, clip_id: str, delta: int) -> None:
    """Monte (``delta > 0``) ou descend le calque parmi ses frères."""
    order = siblings(project, clip_id)
    index = next(i for i, c in enumerate(order) if c.id == clip_id)
    reorder_layer(project, clip_id, index + delta)


def rename_layer(project: Project, clip_id: str, name: str) -> Clip:
    _track, clip = find_layer(project, clip_id)
    text = str(name or "").strip()
    if not text:
        raise LayerError("Un calque doit avoir un nom.")
    clip.label = text
    for asset in project.media_assets:
        if asset.id == clip.asset_id and asset.media_type == "graphic":
            asset.name = text
    return clip


def set_layer_visible(project: Project, clip_id: str, visible: bool) -> Clip:
    _track, clip = find_layer(project, clip_id)
    _set_graphic(clip, visible=bool(visible))
    return clip


def set_layer_locked(project: Project, clip_id: str, locked: bool) -> Clip:
    _track, clip = find_layer(project, clip_id)
    _set_graphic(clip, locked=bool(locked))
    return clip


def set_motion_blur(project: Project, clip_id: str, enabled: bool) -> Clip:
    _track, clip = _require_editable(project, clip_id)
    _set_graphic(clip, motion_blur=bool(enabled))
    return clip


def delete_layers(project: Project, clip_ids: list[str]) -> int:
    """Supprime des calques (et le contenu des groupes supprimés).

    Les enfants d'un parent supprimé sont détachés (``parent_id`` vidé) ;
    rien ne reste vers une référence cassée.
    """
    doomed = set()
    pending = list(clip_ids)
    while pending:
        cid = pending.pop()
        if cid in doomed:
            continue
        _track, clip = _require_editable(project, cid)
        doomed.add(cid)
        if clip.graphic.type == GraphicType.GROUP:
            pending.extend(c.id for _i, _t, c in layer_clips(project) if c.graphic.group_id == cid)
    assets = set()
    for track in project.tracks:
        kept = []
        for clip in track.clips:
            if clip.id in doomed:
                assets.add(clip.asset_id)
            else:
                kept.append(clip)
        track.clips = kept
    for _i, _t, clip in layer_clips(project):
        if clip.graphic.parent_id in doomed:
            _set_graphic(clip, parent_id="")
    project.media_assets[:] = [
        a for a in project.media_assets if not (a.id in assets and a.media_type == "graphic")
    ]
    return len(doomed)


# ---------------------------------------------------------------------------
# Copier / coller de calques
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerClipboard:
    """Calques copiés (forme JSON du ``.kut``), temps relatifs au premier."""

    clips: tuple[dict, ...]
    assets: tuple[dict, ...]
    track_ids: tuple[str, ...]
    sequence_id: str
    base_time: float
    canvas: tuple[int, int] = (0, 0)
    """Taille du cadre d'origine : les tailles en pixels sont adaptées au collage."""


_PIXEL_FIELDS = (
    "width", "height", "stroke_width", "font_size", "shadow_offset_x", "shadow_offset_y",
    "corner_radius", "tracking", "shadow_blur", "background_padding", "background_radius",
)
_INT_PIXEL_FIELDS = frozenset({
    "width", "height", "stroke_width", "font_size", "shadow_offset_x", "shadow_offset_y", "background_padding",
})


def _scale_pixels(data: dict, factor: float) -> None:
    """Adapte les tailles en pixels d'un clip copié à un autre cadre."""
    graphic = data.get("graphic") or {}
    for name in _PIXEL_FIELDS:
        if name in graphic and isinstance(graphic[name], (int, float)) and not isinstance(graphic[name], bool):
            value = graphic[name] * factor
            graphic[name] = int(round(value)) if name in _INT_PIXEL_FIELDS else value
    for kf in data.get("animation", []) or []:
        name = str(kf.get("property_name", ""))
        if name.startswith("graphic.") and name[len("graphic."):] in _PIXEL_FIELDS:
            kf["value"] = float(kf["value"]) * factor
            for slope in ("in_slope", "out_slope"):
                if kf.get(slope) is not None:
                    kf[slope] = float(kf[slope]) * factor


def _with_descendants(project: Project, clip_ids: list[str]) -> list[str]:
    ordered = list(dict.fromkeys(clip_ids))
    index = 0
    while index < len(ordered):
        cid = ordered[index]
        _track, clip = find_layer(project, cid)
        if clip.graphic.type == GraphicType.GROUP:
            for _i, _t, member in layer_clips(project):
                if member.graphic.group_id == cid and member.id not in ordered:
                    ordered.append(member.id)
        index += 1
    return ordered


def copy_layers(project: Project, clip_ids: list[str]) -> LayerClipboard:
    """Copie des calques (un groupe emporte son contenu)."""
    from .project_io import clip_to_dict

    ids = _with_descendants(project, clip_ids)
    if not ids:
        raise LayerError("Aucun calque à copier.")
    stacked = {c.id: (t, c) for _i, t, c in layer_clips(project)}
    ordered = [cid for cid in stacked if cid in ids]
    clips = [stacked[cid][1] for cid in ordered]
    assets = {a.id: a for a in project.media_assets}
    asset_dicts = []
    for clip in clips:
        asset = assets.get(clip.asset_id)
        if asset is not None:
            asset_dicts.append(dict(vars(asset)))
    return LayerClipboard(
        clips=tuple(clip_to_dict(clip) for clip in clips),
        assets=tuple(asset_dicts),
        track_ids=tuple(stacked[cid][0].id for cid in ordered),
        sequence_id=project.active_sequence_id,
        base_time=min(clip.timeline_start for clip in clips),
        canvas=(int(project.width), int(project.height)),
    )


def paste_layers(project: Project, clipboard: LayerClipboard, *, at: float) -> list[Clip]:
    """Colle des calques à ``at`` dans la séquence active.

    Nouveaux identifiants ; parent et groupe sont rattachés aux copies quand
    ils ont été copiés ensemble, gardés s'ils existent dans la séquence
    cible, sinon détachés (jamais de référence cassée entre séquences).
    """
    from .project_io import clip_from_dict

    mapping = {raw["id"]: f"graphic-{uuid.uuid4().hex[:12]}" for raw in clipboard.clips}
    source_w, source_h = clipboard.canvas
    factor = (
        min(project.width / source_w, project.height / source_h)
        if source_w > 0 and source_h > 0 and (source_w, source_h) != (project.width, project.height)
        else 1.0
    )
    existing = {clip.id for track in project.tracks for clip in track.clips}
    assets = {raw["id"]: raw for raw in clipboard.assets}
    created: list[Clip] = []
    for raw, track_id in zip(clipboard.clips, clipboard.track_ids):
        data = deepcopy(raw)
        new_id = mapping[data["id"]]
        asset_raw = assets.get(data.get("asset_id"))
        asset_id = f"graphic-asset-{uuid.uuid4().hex[:12]}"
        if asset_raw is not None:
            project.media_assets.append(MediaAsset(**{**asset_raw, "id": asset_id}))
        else:
            asset_id = data.get("asset_id", "")
        data["id"] = new_id
        data["asset_id"] = asset_id
        data["timeline_start"] = max(0.0, float(at) + float(data["timeline_start"]) - clipboard.base_time)
        graphic = data.get("graphic") or {}
        for key in ("parent_id", "group_id"):
            ref = graphic.get(key, "")
            if ref in mapping:
                graphic[key] = mapping[ref]
            elif ref and ref not in existing:
                graphic[key] = ""
        data["graphic"] = graphic
        if factor != 1.0:
            _scale_pixels(data, factor)
        clip = clip_from_dict(data, "graphics")
        track = next(
            (t for t in project.tracks if t.id == track_id and t.type == "graphics" and not t.locked),
            None,
        ) or ensure_graphics_track(project)
        clip.track_id = track.id
        clip.graphic = replace(clip.graphic, z_order=next_z_order(track))
        track.clips.append(clip)
        track.clips.sort(key=lambda c: (c.timeline_start, c.id))
        created.append(clip)
    return created


def duplicate_layers(project: Project, clip_ids: list[str]) -> list[Clip]:
    """Duplique des calques au même instant, juste au-dessus des originaux."""
    clipboard = copy_layers(project, clip_ids)
    return paste_layers(project, clipboard, at=clipboard.base_time)


# ---------------------------------------------------------------------------
# Copier / coller d'attributs (transform, effets, masques, animation)
# ---------------------------------------------------------------------------

ATTRIBUTE_KINDS = ("transform", "effects", "masks", "keyframes")


@dataclass(frozen=True)
class AttributeClipboard:
    transform: ClipTransform | None = None
    transform_keyframes: tuple = ()
    effects: tuple = ()
    color_grade: object = None
    masks: tuple = ()
    mask_animation: tuple = ()
    animation: tuple = ()


def copy_attributes(project: Project, clip_id: str) -> AttributeClipboard:
    _track, clip = find_clip(project, clip_id)
    masks = tuple(clip.compositing.masks) if clip.compositing is not None else ()
    mask_ids = {m.id for m in masks}
    mask_animation = tuple(
        kf for kf in clip.animation if kf.property_name.split(".")[1:2] and kf.property_name.startswith("mask.")
        and kf.property_name.split(".")[1] in mask_ids
    )
    return AttributeClipboard(
        transform=clip.transform,
        transform_keyframes=tuple(clip.transform_keyframes),
        effects=tuple(deepcopy(clip.effects)),
        color_grade=clip.color_grade,
        masks=masks,
        mask_animation=mask_animation,
        animation=tuple(kf for kf in clip.animation if not kf.property_name.startswith("mask.")),
    )


def paste_attributes(project: Project, clip_ids: list[str], clipboard: AttributeClipboard, kinds) -> int:
    """Colle les attributs choisis (``transform``, ``effects``, ``masks``, ``keyframes``)."""
    from .effects_model import ClipEffect

    kinds = set(kinds)
    count = 0
    for clip_id in clip_ids:
        track, clip = find_clip(project, clip_id)
        if track.locked or (isinstance(clip.graphic, GraphicOverlay) and clip.graphic.locked):
            continue
        if track.type not in ("video", "graphics"):
            continue
        if "transform" in kinds and clipboard.transform is not None:
            clip.transform = clipboard.transform
            if "keyframes" in kinds:
                clip.transform_keyframes = [copy_keyframe(kf, id="") for kf in clipboard.transform_keyframes]
        if "effects" in kinds:
            clip.effects = [
                ClipEffect(id=uuid.uuid4().hex[:10], type=e.type, enabled=e.enabled, params=dict(e.params))
                for e in clipboard.effects
            ]
            clip.color_grade = clipboard.color_grade
        if "masks" in kinds:
            renamed = {m.id: new_mask_id() for m in clipboard.masks}
            masks = tuple(replace(m, id=renamed[m.id]) for m in clipboard.masks)
            clip.compositing = replace(clip.compositing, masks=masks)
            kept = [kf for kf in clip.animation if not kf.property_name.startswith("mask.")]
            for kf in clipboard.mask_animation:
                _prefix, mask_id, name = kf.property_name.split(".", 2)
                if mask_id in renamed:
                    kept.append(replace(kf, property_name=mask_property_id(renamed[mask_id], name), id=""))
            clip.animation = sorted(kept, key=lambda k: (k.property_name, k.time_seconds))
        if "keyframes" in kinds and clipboard.animation:
            kept = [kf for kf in clip.animation if kf.property_name.startswith("mask.")]
            kept.extend(replace(kf, id="") for kf in clipboard.animation)
            clip.animation = sorted(kept, key=lambda k: (k.property_name, k.time_seconds))
        count += 1
    return count


def offset_animation(keyframes, delta: float) -> list[Keyframe]:
    """Décale des keyframes génériques dans le temps (bornées à 0)."""
    return [replace(kf, time_seconds=normalize_time(max(0.0, kf.time_seconds + delta)), id=kf.id) for kf in keyframes]


__all__ = [
    "ATTRIBUTE_KINDS", "AttributeClipboard", "CONTAINER_TYPES", "LayerClipboard", "LayerError",
    "LayerNode", "add_layer", "can_parent", "clear_parent", "copy_attributes", "copy_layers",
    "delete_layers", "duplicate_layers", "find_layer", "group_layers", "layer_clips", "layer_tree",
    "move_into_group", "move_layer", "parent_candidates", "paste_attributes", "paste_layers",
    "rename_layer", "reorder_layer", "scene_for_project", "set_layer_locked", "set_layer_visible",
    "set_motion_blur", "set_parent", "siblings", "stack_key", "transform_graph", "ungroup",
]
