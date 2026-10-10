"""Opérations sur les compositions nodales : convertir des calques en composition, en créer une vide.

La conversion est **à sens unique** (ADR-0002) : les clips choisis (vidéo et calques graphiques de la séquence active)
deviennent les nœuds d'**un** clip de composition, posé sur la plus basse de leurs pistes vidéo, de leur premier
début à leur dernière fin. Chaque calque donne une branche, du bas vers le haut de l'empilement (pistes vidéo, puis
calques graphiques, comme à l'export) :

    source (média ou calque graphique) → masques → transformation → effets → étalonnage → incrustation

ne gardant que les étapes qui font quelque chose. Les branches se superposent par des fusions, avec le mode de fusion
de chaque calque (le plus bas est posé sur le fond transparent de la composition). L'image est celle des calques, à
quelques différences près, mesurées par ``tests/test_composition.py`` : une vignette se centre sur le cadre et non plus
sur le calque, un flou mêle le bord d'un calque réduit à la transparence autour plutôt que de le prolonger.

Refusée (:class:`CompositionError`, rien n'est modifié) pour ce que la composition ne sait pas encore porter : clips
imbriqués ou déjà composés, remappage temporel, emplacements de template, tracking, étalonnage avec fenêtres, cadrage
« remplir » animé, calques d'effets, groupes et parentage. Le son des clips vidéo passe dans la composition avec leur
gain ; leur panoramique, leurs fondus et leurs effets audio ne la suivent pas (:attr:`ConversionResult.dropped`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

from .blend_modes import coerce_blend_mode
from .composition import (
    OUTPUT_ID,
    Composition,
    CompositionGraph,
    EffectsNode,
    GradeNode,
    GraphicNode,
    KeyNode,
    MaskNode,
    MediaNode,
    MergeNode,
    TransformNode,
)
from .project_model import Clip, Project, Track
from .visual_effects import ClipTransform

_EPSILON = 1e-9


class CompositionError(ValueError):
    """La conversion ou la création est refusée (message pour l'utilisateur)."""


@dataclass
class ConversionResult:
    clip: Clip
    dropped_transition_ids: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    """Réglages des clips convertis que la composition ne garde pas (panoramique, fondus, effets audio)."""


def new_composition_id() -> str:
    return f"comp-{uuid.uuid4().hex[:10]}"


def _shift(keyframes, offset: float) -> tuple:
    return tuple(replace(kf, time_seconds=kf.time_seconds + offset) for kf in keyframes)


def _transform_part(transform: ClipTransform) -> ClipTransform:
    """Le transform d'un clip sans son cadrage (porté par le nœud média)."""
    return replace(transform, fill=False, pan_x=0.0, pan_y=0.0)


def _refusal(clip: Clip, track: Track, project: Project, selected: set[str]) -> str | None:
    """Pourquoi ``clip`` ne peut pas devenir des nœuds (``None`` : il le peut)."""
    from .graphics import CONTAINER_TYPES

    name = clip.label or clip.id
    if track.type not in ("video", "graphics"):
        return f"« {name} » n'est ni un clip vidéo ni un calque graphique."
    if clip.sequence_id or clip.composition is not None:
        return f"« {name} » est déjà une séquence imbriquée ou une composition."
    if clip.template_slot:
        return f"« {name} » est un emplacement de template."
    if clip.is_time_remapped:
        return f"« {name} » a une vitesse ou un remappage temporel."
    if getattr(clip, "tracking", None) is not None:
        return f"« {name} » porte du tracking."
    if any(node.windows for node in getattr(clip.color_grade, "correctors", ())):
        return f"« {name} » a un étalonnage avec fenêtres."
    if any(kf.property_name in ("pan_x", "pan_y") for kf in clip.transform_keyframes):
        return f"« {name} » a un cadrage « remplir » animé."
    if track.type == "graphics":
        graphic = clip.graphic
        if graphic is None or graphic.type in CONTAINER_TYPES or graphic.parent_id or graphic.group_id:
            return f"« {name} » est un calque d'effets, un groupe, ou un calque parenté."
    else:
        asset = next((item for item in project.media_assets if item.id == clip.asset_id), None)
        if asset is None:
            return f"Le média de « {name} » est introuvable."
    for other_track in project.active_sequence.tracks:
        for other in other_track.clips:
            graphic = getattr(other, "graphic", None)
            if other.id not in selected and graphic is not None and clip.id in (graphic.parent_id, graphic.group_id):
                return f"« {other.label or other.id} » dépend de « {name} » (parent ou groupe)."
    return None


def _branch(clip: Clip, track: Track, offset: float, graph: CompositionGraph, animation: list,
            dropped: list[str]) -> tuple[CompositionGraph, str]:
    """La branche d'un calque ; rend le graphe et le nœud qui la termine."""
    from .gpu_grade import grade_is_active

    def add(graph: CompositionGraph, node) -> tuple[CompositionGraph, str]:
        return graph.with_added(node), node.id

    name = clip.label or clip.id
    masks = tuple(clip.compositing.masks) if clip.compositing is not None else ()
    mask_ids = {mask.id for mask in masks}
    own_mask_keys = [kf for kf in clip.animation if kf.property_name.startswith("mask.")
                     and kf.property_name.split(".")[1] in mask_ids]
    if track.type == "graphics":
        graph, last = add(graph, GraphicNode(
            graph.next_id("g"), clip.graphic, offset, clip.duration, clip.transform, tuple(clip.transform_keyframes),
            tuple(clip.animation), name, masks))
    else:
        transform = clip.transform
        graph, last = add(graph, MediaNode(
            graph.next_id("m"), clip.asset_id, offset, clip.source_in, clip.source_out, name, clip.gain_db,
            False, transform.fill, transform.pan_x, transform.pan_y))
        if clip.pan or clip.fade_in or clip.fade_out or clip.audio_effects:
            dropped.append(f"« {name} » : panoramique, fondus et effets audio")
        if masks:
            animation.extend(_shift(own_mask_keys, offset))
            graph, mask = add(graph, MaskNode(graph.next_id("k"), masks))
            graph, last = graph.connected(last, mask), mask
        moved = _transform_part(transform)
        if moved != ClipTransform() or clip.transform_keyframes:
            graph, placed = add(graph, TransformNode(graph.next_id("t"), moved,
                                                     _shift(clip.transform_keyframes, offset)))
            graph, last = graph.connected(last, placed), placed
    if any(effect.enabled for effect in clip.effects):
        graph, effects = add(graph, EffectsNode(graph.next_id("e"), tuple(e for e in clip.effects if e.enabled)))
        graph, last = graph.connected(last, effects), effects
    if grade_is_active(clip.color_grade):
        graph, grade = add(graph, GradeNode(graph.next_id("c"), clip.color_grade))
        graph, last = graph.connected(last, grade), grade
    key = getattr(clip.compositing, "chroma_key", None)
    if track.type == "video" and key is not None and key.enabled:
        graph, keyed = add(graph, KeyNode(graph.next_id("i"), key))
        graph, last = graph.connected(last, keyed), keyed
    return graph, last


def convert_to_composition(project: Project, clip_ids, name: str | None = None) -> ConversionResult:
    """Remplace les clips ``clip_ids`` de la séquence active par un clip de composition (voir le module).

    Toute la modification est faite sur le projet passé : l'appelant enregistre ensuite **une** étape d'historique.
    """
    wanted = list(dict.fromkeys(clip_ids or ()))
    if not wanted:
        raise CompositionError("Sélectionnez au moins un clip.")
    sequence = project.active_sequence
    located: list[tuple[int, Track, Clip]] = [
        (index, track, clip) for index, track in enumerate(sequence.tracks) for clip in track.clips if clip.id in wanted
    ]
    missing = set(wanted) - {clip.id for _i, _t, clip in located}
    if missing:
        raise KeyError(f"Clip(s) introuvable(s) dans la séquence active : {sorted(missing)}.")
    locked = sorted({track.id for _i, track, _c in located if track.locked})
    if locked:
        raise CompositionError(f"Piste(s) verrouillée(s) : {', '.join(locked)}.")
    selected = {clip.id for _i, _t, clip in located}
    for _index, track, clip in located:
        reason = _refusal(clip, track, project, selected)
        if reason is not None:
            raise CompositionError(reason)
    videos = [(index, track) for index, track, _clip in located if track.type == "video"]
    if not videos:
        raise CompositionError("La sélection n'a pas de clip vidéo : une composition va sur une piste vidéo.")
    host = min(videos, key=lambda item: item[0])[1]
    start = min(clip.timeline_start for _i, _t, clip in located)
    end = max(clip.timeline_start + clip.duration for _i, _t, clip in located)
    if end - start <= _EPSILON:
        raise CompositionError("La sélection n'a pas de durée.")
    blocking = [clip for clip in host.clips if clip.id not in selected
                and clip.timeline_start < end - _EPSILON and clip.timeline_start + clip.duration > start + _EPSILON]
    if blocking:
        raise CompositionError(f"La piste {host.name} a d'autres clips dans cette plage : la composition ne peut pas "
                               "s'y poser.")

    # Du bas vers le haut : pistes vidéo dans leur ordre, puis calques graphiques (composés au-dessus, comme à
    # l'export), chacun à sa place dans le temps.
    ordered = sorted(located, key=lambda item: (item[1].type == "graphics", item[0], item[2].timeline_start))
    graph = CompositionGraph.empty()
    animation: list = []
    dropped: list[str] = []
    result: str | None = None
    for _index, track, clip in ordered:
        graph, last = _branch(clip, track, clip.timeline_start - start, graph, animation, dropped)
        if result is None:
            result = last
            continue
        merge = MergeNode(graph.next_id("f"), coerce_blend_mode(getattr(clip.compositing, "blend_mode", "normal")))
        graph = graph.with_added(merge).connected(result, merge.id, 0).connected(last, merge.id, 1)
        result = merge.id
    if result is not None:
        graph = graph.connected(result, OUTPUT_ID)
    composition = Composition(graph, end - start, tuple(sorted(animation, key=lambda kf: (kf.property_name,
                                                                                          kf.time_seconds))))

    dropped_transitions = []
    for transition in list(sequence.transitions):
        if transition.from_clip_id in selected or transition.to_clip_id in selected:
            dropped_transitions.append(transition.id)
            sequence.transitions.remove(transition)
    for _index, track, clip in located:
        track.clips.remove(clip)
    comp_clip = Clip(id=new_composition_id(), asset_id="", track_id=host.id, timeline_start=start, source_in=0.0,
                     source_out=end - start, label=name or "Composition", composition=composition)
    host.clips.append(comp_clip)
    host.clips.sort(key=lambda item: item.timeline_start)
    return ConversionResult(comp_clip, dropped_transitions, dropped)


def create_composition(project: Project, track_id: str, at: float, duration: float,
                       name: str | None = None) -> Clip:
    """Un clip de composition vide (sa sortie seule : transparent) sur la piste vidéo ``track_id``, à ``at``."""
    track = next((item for item in project.active_sequence.tracks if item.id == track_id), None)
    if track is None or track.type != "video":
        raise CompositionError("Une composition va sur une piste vidéo.")
    if track.locked:
        raise CompositionError(f"Piste verrouillée : {track.name}.")
    duration = max(0.04, float(duration))
    start = max(0.0, float(at))
    if any(clip.timeline_start < start + duration - _EPSILON and clip.timeline_start + clip.duration > start + _EPSILON
           for clip in track.clips):
        raise CompositionError(f"La piste {track.name} n'est pas libre à cet endroit.")
    clip = Clip(id=new_composition_id(), asset_id="", track_id=track.id, timeline_start=start, source_in=0.0,
                source_out=duration, label=name or "Composition", composition=Composition(duration=duration))
    track.clips.append(clip)
    track.clips.sort(key=lambda item: item.timeline_start)
    return clip


__all__ = ["CompositionError", "ConversionResult", "convert_to_composition", "create_composition",
           "new_composition_id"]
