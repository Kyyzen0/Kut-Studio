"""Opérations d'édition des keyframes, communes à toute l'interface.

Fonctions **pures** (aucun Qt) qui modifient un :class:`~core.project_model.Project`
en place : l'inspecteur, la timeline, le Graph Editor et les raccourcis
appellent toutes les mêmes fonctions, puis enregistrent une entrée
d'historique (instantané du projet). Elles passent par le registre
:mod:`core.animation_targets` : toute propriété animable enregistrée en
profite sans code supplémentaire.

Un keyframe est désigné par :class:`KeyframeRef` (clip, propriété,
identifiant stable). Les temps sont locaux au clip et normalisés à la
microseconde ; :func:`snap_to_frame` les aligne sur la grille d'images.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .animation import (
    AnimatableProperty,
    AnimationCurve,
    InterpolationType,
    Keyframe,
    TangentMode,
    coerce_interpolation,
    coerce_tangent_mode,
    normalize_time,
    snap_local_time,
)
from .animation_targets import PropertyTarget, get_target
from .effects_model import FX_PREFIX


@dataclass(frozen=True)
class KeyframeRef:
    """Désigne un keyframe : ``(clip, propriété, identifiant)``."""

    clip_id: str
    property_id: str
    keyframe_id: str


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


def find_clip(project, clip_id: str):
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return clip
    raise KeyError(f"Clip introuvable : {clip_id!r}.")


def clip_duration(clip) -> float:
    return float(getattr(clip, "duration", 0.0) or 0.0)


def curve_of(clip, property_id: str) -> AnimationCurve:
    return get_target(property_id, clip).curve(clip)


def is_animated(clip, property_id: str) -> bool:
    return bool(get_target(property_id, clip).get_keyframes(clip))


def value_at(clip, property_id: str, local_time: float) -> Any:
    return get_target(property_id, clip).value_at(clip, local_time)


def keyframe_by_id(clip, property_id: str, keyframe_id: str) -> Keyframe | None:
    return next((k for k in get_target(property_id, clip).get_keyframes(clip) if k.id == keyframe_id), None)


def keyframe_times(clip, property_ids: Iterable[str]) -> list[float]:
    """Instants (triés, sans doublon) des keyframes des propriétés données."""
    times = {k.time_seconds for pid in property_ids for k in get_target(pid, clip).get_keyframes(clip)}
    return sorted(times)


def previous_keyframe_time(clip, property_ids: Iterable[str], local_time: float) -> float | None:
    earlier = [t for t in keyframe_times(clip, property_ids) if t < normalize_time(local_time) - 1e-6]
    return earlier[-1] if earlier else None


def next_keyframe_time(clip, property_ids: Iterable[str], local_time: float) -> float | None:
    later = [t for t in keyframe_times(clip, property_ids) if t > normalize_time(local_time) + 1e-6]
    return later[0] if later else None


def snap_to_frame(clip, local_time: float, fps: float) -> float:
    """Temps local aligné sur une image de la timeline, borné à la durée du clip."""
    snapped = snap_local_time(local_time, clip.timeline_start, fps)
    return min(max(0.0, snapped), normalize_time(clip_duration(clip)))


# ---------------------------------------------------------------------------
# Écriture (toutes modifient ``project`` en place)
# ---------------------------------------------------------------------------


def _keyframe_at(curve: AnimationCurve, time: float) -> Keyframe:
    """Le keyframe de ``curve`` à ``time`` : l'appelant vient de l'y poser (invariant, jamais ``None``)."""
    keyframe = curve.keyframe_at(time)
    if keyframe is None:
        raise RuntimeError(f"Keyframe attendu à t={time:g} s : la courbe vient d'être modifiée à cet instant.")
    return keyframe


def _bounded_time(clip, local_time: float) -> float:
    duration = clip_duration(clip)
    t = normalize_time(local_time)
    if t < 0.0 or (duration > 0 and t > normalize_time(duration) + 1e-6):
        raise ValueError(f"Temps hors du clip : {local_time} (durée {duration}).")
    return t


def _guard(project, clip_id: str, property_id: str | None = None) -> None:
    """Garde commune des mutateurs : piste ou calque verrouillé, propriété d'un autre genre de piste → refus, rien écrit.

    Les lectures, l'annulation/rétablissement (instantanés du projet) et le moteur de rendu n'écrivent pas par ici.
    """
    from .compositing_guard import require_clip_editable

    require_clip_editable(project, clip_id, property_id=property_id)


def _store(target: PropertyTarget, clip, curve: AnimationCurve) -> None:
    target.set_keyframes(clip, list(curve.keyframes))


def _make(target: PropertyTarget, time_seconds: float, value: Any, **fields) -> Keyframe:
    return target.make_keyframe(target.id, time_seconds, target.spec.clamp(value), **fields)


def add_keyframe(
    project,
    clip_id: str,
    property_id: str,
    local_time: float,
    value: Any = None,
    *,
    interpolation: InterpolationType | str | None = None,
) -> Keyframe:
    """Ajoute (ou met à jour) le keyframe de ``property_id`` à ``local_time``.

    Sans ``value`` : la valeur **actuelle** est conservée et, si la propriété
    est déjà animée, le keyframe est inséré sans modifier la courbe. Avec
    ``value`` : le keyframe prend cette valeur (celui du même instant est
    remplacé, en gardant son interpolation, ses tangentes et son identifiant).
    """
    clip = find_clip(project, clip_id)
    _guard(project, clip_id, property_id)
    target = get_target(property_id, clip)
    t = _bounded_time(clip, local_time)
    curve = target.curve(clip)
    existing = curve.keyframe_at(t)
    if value is None:
        if existing is not None:
            return existing
        if curve:
            curve = curve.inserted_preserving_shape(t, clamp=target.spec.clamp)
            if interpolation is not None:
                inserted = _keyframe_at(curve, t)
                curve = curve.replaced({inserted.id: replace(
                    inserted, interpolation=coerce_interpolation(interpolation), id=inserted.id
                )})
        else:
            curve = AnimationCurve([_make(
                target, t, target.get_static(clip),
                interpolation=coerce_interpolation(interpolation or InterpolationType.LINEAR),
            )], target.spec.kind)
        _store(target, clip, curve)
        return _keyframe_at(curve, t)
    if existing is not None:
        updated = replace(existing, value=target.spec.clamp(value), id=existing.id)
        if interpolation is not None:
            updated = replace(updated, interpolation=coerce_interpolation(interpolation), id=existing.id)
        curve = curve.replaced({existing.id: updated})
    else:
        template = _neighbour_interpolation(curve, t)
        curve = curve.with_keyframe(_make(
            target, t, value, interpolation=coerce_interpolation(interpolation or template)
        ))
    _store(target, clip, curve)
    return _keyframe_at(curve, t)


def _neighbour_interpolation(curve: AnimationCurve, t: float) -> InterpolationType:
    """Interpolation par défaut d'un nouveau keyframe : celle du segment qui le contient."""
    index = curve.segment_index(t)
    if index is not None:
        return curve.keyframes[index].interpolation
    return InterpolationType.LINEAR


def set_value_at(project, clip_id: str, property_id: str, local_time: float, value: Any) -> Keyframe | None:
    """Édition d'une valeur depuis l'inspecteur.

    Propriété non animée : la valeur statique change. Propriété animée : un
    keyframe est créé (ou mis à jour) au temps courant — l'animation
    « automatique » d'un débutant : activer, déplacer la tête, modifier.
    """
    clip = find_clip(project, clip_id)
    _guard(project, clip_id, property_id)
    target = get_target(property_id, clip)
    if not target.get_keyframes(clip):
        target.set_static(clip, value)
        return None
    return add_keyframe(project, clip_id, property_id, local_time, value)


def remove_keyframes(project, refs: Iterable[KeyframeRef], *, local_time: float | None = None) -> int:
    """Supprime des keyframes.

    Une propriété qui n'en a plus garde la valeur qu'elle affichait (à
    ``local_time`` si fourni, sinon celle de son premier keyframe) : pas de
    saut vers une ancienne valeur statique.
    """
    count = 0
    grouped: dict[tuple[str, str], set[str]] = {}
    for ref in refs:
        grouped.setdefault((ref.clip_id, ref.property_id), set()).add(ref.keyframe_id)
    for clip_id, property_id in grouped:                 # tout est vérifié avant la première écriture
        find_clip(project, clip_id)
        _guard(project, clip_id, property_id)
    for (clip_id, property_id), ids in grouped.items():
        clip = find_clip(project, clip_id)
        target = get_target(property_id, clip)
        curve = target.curve(clip)
        remaining = curve.without_ids(ids)
        count += len(curve) - len(remaining)
        if curve and not remaining:
            shown = curve.evaluate(local_time) if local_time is not None else curve.keyframes[0].value
            target.set_static(clip, shown)
        _store(target, clip, remaining)
    return count


def remove_keyframe_at(project, clip_id: str, property_id: str, local_time: float) -> bool:
    clip = find_clip(project, clip_id)
    _guard(project, clip_id, property_id)
    existing = get_target(property_id, clip).curve(clip).keyframe_at(local_time)
    if existing is None:
        return False
    refs = [KeyframeRef(clip_id, property_id, existing.id)]
    return remove_keyframes(project, refs, local_time=local_time) == 1


def set_animation_enabled(project, clip_id: str, property_id: str, enabled: bool, local_time: float) -> None:
    """Active l'animation (keyframe au temps courant) ou la retire (valeur figée)."""
    clip = find_clip(project, clip_id)
    _guard(project, clip_id, property_id)
    target = get_target(property_id, clip)
    if enabled:
        if not target.get_keyframes(clip):
            add_keyframe(project, clip_id, property_id, local_time)
        return
    if target.get_keyframes(clip):
        current = target.value_at(clip, local_time)
        target.set_keyframes(clip, [])
        target.set_static(clip, current)


def clear_effect_animation(project, clip_id: str, effect_id: str, local_time: float) -> int:
    """Retire **toute** l'animation d'un effet (« détacher » : étape 4 de l'ADR-0008) ; retourne le nombre de paramètres animés.

    Chaque paramètre garde la valeur qu'il montrait à ``local_time`` (comme :func:`set_animation_enabled`). Une image clé
    ``fx.<effet>.*`` dont le paramètre n'est pas (ou plus) animable (fichier d'une autre version) est retirée aussi : sans cela
    elle bloquerait la conversion en composition sans qu'aucun losange ne la montre. Verrous de piste et de calque respectés.
    """
    clip = find_clip(project, clip_id)
    _guard(project, clip_id)
    prefix = f"{FX_PREFIX}{effect_id}."
    names = sorted({k.property_name for k in clip.animation if k.property_name.startswith(prefix)})
    for property_id in names:
        try:
            set_animation_enabled(project, clip_id, property_id, False, local_time)
        except KeyError:                                   # paramètre non animable : l'image clé orpheline est simplement retirée
            pass
    clip.animation = [k for k in clip.animation if not k.property_name.startswith(prefix)]
    return len(names)


def move_keyframes(
    project, refs: Sequence[KeyframeRef], delta_seconds: float, *, fps: float | None = None
) -> dict[KeyframeRef, float]:
    """Décale des keyframes dans le temps (même décalage pour tous).

    Le décalage est borné pour qu'aucun keyframe ne sorte de son clip ; avec
    ``fps`` les nouveaux temps sont alignés sur les images. Un keyframe
    déplacé sur un autre (non déplacé) le remplace. Retourne les nouveaux temps.
    """
    grouped: dict[str, list[KeyframeRef]] = {}
    for ref in refs:
        find_clip(project, ref.clip_id)
        _guard(project, ref.clip_id, ref.property_id)    # tout est vérifié avant la première écriture
        grouped.setdefault(ref.clip_id, []).append(ref)
    # Borne commune : le groupe se déplace d'un bloc.
    delta = float(delta_seconds)
    for clip_id, items in grouped.items():
        clip = find_clip(project, clip_id)
        duration = normalize_time(clip_duration(clip))
        times = [k.time_seconds for ref in items if (k := keyframe_by_id(clip, ref.property_id, ref.keyframe_id))]
        if times:
            delta = max(delta, -min(times))
            delta = min(delta, duration - max(times))
    result: dict[KeyframeRef, float] = {}
    for clip_id, items in grouped.items():
        clip = find_clip(project, clip_id)
        by_property: dict[str, set[str]] = {}
        for ref in items:
            by_property.setdefault(ref.property_id, set()).add(ref.keyframe_id)
        for property_id, ids in by_property.items():
            target = get_target(property_id, clip)
            curve = target.curve(clip)
            moved: list[Keyframe] = []
            for keyframe in curve.keyframes:
                if keyframe.id not in ids:
                    continue
                new_time = keyframe.time_seconds + delta
                if fps:
                    new_time = snap_to_frame(clip, new_time, fps)
                new_time = min(max(0.0, normalize_time(new_time)), normalize_time(clip_duration(clip)))
                moved.append(replace(keyframe, time_seconds=new_time, id=keyframe.id))
                result[KeyframeRef(clip_id, property_id, keyframe.id)] = new_time
            moved_times = {k.time_seconds for k in moved}
            kept = [k for k in curve.keyframes if k.id not in ids and k.time_seconds not in moved_times]
            _store(target, clip, AnimationCurve([*kept, *moved], target.spec.kind))
    return result


def set_keyframe_time(project, ref: KeyframeRef, local_time: float) -> float:
    """Place un keyframe à un temps précis (Graph Editor, champ de saisie)."""
    clip = find_clip(project, ref.clip_id)
    keyframe = keyframe_by_id(clip, ref.property_id, ref.keyframe_id)
    if keyframe is None:
        raise KeyError(f"Keyframe introuvable : {ref.keyframe_id!r}.")
    return move_keyframes(project, [ref], local_time - keyframe.time_seconds).get(ref, keyframe.time_seconds)


def set_keyframe_values(project, values: dict[KeyframeRef, Any]) -> None:
    """Change la valeur de keyframes (bornée par la propriété)."""
    for ref in values:
        find_clip(project, ref.clip_id)
        _guard(project, ref.clip_id, ref.property_id)
    for ref, value in values.items():
        clip = find_clip(project, ref.clip_id)
        target = get_target(ref.property_id, clip)
        curve = target.curve(clip)
        keyframe = next((k for k in curve.keyframes if k.id == ref.keyframe_id), None)
        if keyframe is None:
            continue
        updated = replace(keyframe, value=target.spec.clamp(value), id=keyframe.id)
        _store(target, clip, curve.replaced({keyframe.id: updated}))


def set_interpolation(project, refs: Iterable[KeyframeRef], interpolation: InterpolationType | str) -> int:
    """Change l'interpolation (segment sortant) des keyframes désignés."""
    kind = coerce_interpolation(interpolation)
    count = 0
    refs = list(refs)
    for ref in refs:
        find_clip(project, ref.clip_id)
        _guard(project, ref.clip_id, ref.property_id)
    for ref in refs:
        clip = find_clip(project, ref.clip_id)
        target = get_target(ref.property_id, clip)
        curve = target.curve(clip)
        keyframe = next((k for k in curve.keyframes if k.id == ref.keyframe_id), None)
        if keyframe is None or keyframe.interpolation is kind:
            continue
        _store(target, clip, curve.replaced({keyframe.id: replace(keyframe, interpolation=kind, id=keyframe.id)}))
        count += 1
    return count


def set_tangents(
    project,
    ref: KeyframeRef,
    *,
    in_slope: Any = None,
    out_slope: Any = None,
    mode: TangentMode | str | None = None,
    auto: bool = False,
) -> Keyframe:
    """Fixe les pentes Bézier d'un keyframe.

    En mode ``linked``, fixer une pente fixe aussi l'autre (courbe lisse) ;
    en ``broken`` elles sont indépendantes. ``auto=True`` revient aux pentes
    automatiques.
    """
    clip = find_clip(project, ref.clip_id)
    _guard(project, ref.clip_id, ref.property_id)
    target = get_target(ref.property_id, clip)
    curve = target.curve(clip)
    keyframe = next((k for k in curve.keyframes if k.id == ref.keyframe_id), None)
    if keyframe is None:
        raise KeyError(f"Keyframe introuvable : {ref.keyframe_id!r}.")
    tangent_mode = coerce_tangent_mode(mode) if mode is not None else keyframe.tangent_mode
    if auto:
        updated = replace(keyframe, in_slope=None, out_slope=None, tangent_mode=tangent_mode, id=keyframe.id)
    else:
        new_in = keyframe.in_slope if in_slope is None else in_slope
        new_out = keyframe.out_slope if out_slope is None else out_slope
        if tangent_mode is TangentMode.LINKED:
            shared = out_slope if out_slope is not None else in_slope
            if shared is None:  # passage en « liées » : on aligne sur la pente sortante actuelle
                index = curve.index_at(keyframe.time_seconds)
                shared = curve.resolved_slopes(index)[1][0] if index is not None else 0.0
            new_in = new_out = shared
        updated = replace(keyframe, in_slope=new_in, out_slope=new_out, tangent_mode=tangent_mode, id=keyframe.id)
    _store(target, clip, curve.replaced({keyframe.id: updated}))
    return updated


# ---------------------------------------------------------------------------
# Copier / coller
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnimationClipboard:
    """Keyframes copiés : par propriété, temps relatifs au premier keyframe copié."""

    items: tuple[tuple[str, tuple[Keyframe, ...]], ...]
    # Spécification de chaque propriété SOURCE, parallèle à ``items`` (résolue contre le clip source à la copie : une
    # propriété ``fx.*`` n'existe pas forcément sur le clip de destination). Vide = non renseignée (ancienne forme).
    specs: tuple[AnimatableProperty, ...] = ()

    @property
    def property_ids(self) -> tuple[str, ...]:
        return tuple(pid for pid, _frames in self.items)

    @property
    def is_single_property(self) -> bool:
        return len(self.items) == 1

    def __bool__(self) -> bool:
        return any(frames for _pid, frames in self.items)


def copy_keyframes(project, clip_id: str, property_ids: Iterable[str], ids: set[str] | None = None) -> AnimationClipboard:
    """Copie les keyframes (tous, ou ceux de ``ids``) des propriétés données."""
    clip = find_clip(project, clip_id)
    selected: list[tuple[str, list[Keyframe]]] = []
    specs: list[AnimatableProperty] = []
    for pid in property_ids:
        source = get_target(pid, clip)
        frames = [k for k in source.get_keyframes(clip) if ids is None or k.id in ids]
        if frames:
            selected.append((pid, frames))
            specs.append(source.spec)
    if not selected:
        return AnimationClipboard(())
    origin = min(k.time_seconds for _pid, frames in selected for k in frames)
    items = tuple(
        (pid, tuple(replace(k, time_seconds=k.time_seconds - origin, id=k.id) for k in frames))
        for pid, frames in selected
    )
    return AnimationClipboard(items, tuple(specs))


def can_paste(clipboard: AnimationClipboard, property_id: str | None = None, clip=None) -> bool:
    """Collage possible ? Sur une autre propriété, seulement si les types sont compatibles.

    ``clip`` (facultatif, le clip de destination) résout les propriétés qui dépendent du clip (``fx.*``, ADR-0008) ; sans lui
    une telle propriété est « inconnue » (``KeyError``), comme avant pour tout identifiant inconnu.
    """
    if not clipboard:
        return False
    if property_id is None or not clipboard.is_single_property:
        return True
    # La source est la spécification stockée à la copie ; seule la CIBLE se résout contre le clip de destination.
    source = clipboard.specs[0] if clipboard.specs else get_target(clipboard.items[0][0], clip).spec
    return source.compatible_with(get_target(property_id, clip).spec)


def paste_keyframes(
    project,
    clip_id: str,
    clipboard: AnimationClipboard,
    local_time: float,
    *,
    target_property: str | None = None,
) -> list[KeyframeRef]:
    """Colle au temps ``local_time`` ; retourne les keyframes créés.

    Une seule propriété copiée peut être collée sur une propriété compatible
    (``target_property``) ; ses valeurs sont alors bornées à la cible. Une
    animation complète (plusieurs propriétés) se colle sur les mêmes
    propriétés. Les keyframes au-delà de la fin du clip sont ignorés.

    Raises:
        ValueError: propriété cible incompatible.
    """
    clip = find_clip(project, clip_id)
    if target_property is not None and not can_paste(clipboard, target_property, clip):
        raise ValueError("Ces images-clés ne sont pas compatibles avec cette propriété.")
    for source_id, _frames in clipboard.items:             # tout est vérifié avant la première écriture
        destination = target_property if (target_property and clipboard.is_single_property) else source_id
        if destination.startswith(FX_PREFIX):              # un effet absent du clip : échec clair, jamais ignoré
            get_target(destination, clip)
        try:
            _guard(project, clip_id, destination)
        except KeyError:                                   # propriété inconnue : ignorée plus bas, comme avant
            continue
    duration = normalize_time(clip_duration(clip))
    created: list[KeyframeRef] = []
    for source_id, frames in clipboard.items:
        destination = target_property if (target_property and clipboard.is_single_property) else source_id
        try:
            target = get_target(destination, clip)
        except KeyError:
            continue
        curve = target.curve(clip)
        for keyframe in frames:
            t = normalize_time(local_time + keyframe.time_seconds)
            if t < 0 or t > duration + 1e-6:
                continue
            pasted = target.make_keyframe(
                destination, t, target.spec.clamp(keyframe.value),
                interpolation=keyframe.interpolation, in_slope=keyframe.in_slope,
                out_slope=keyframe.out_slope, tangent_mode=keyframe.tangent_mode,
            )
            curve = curve.with_keyframe(pasted)
            created.append(KeyframeRef(clip_id, destination, pasted.id))
        _store(target, clip, curve)
    return created


__all__ = [
    "AnimationClipboard",
    "KeyframeRef",
    "add_keyframe",
    "can_paste",
    "clear_effect_animation",
    "copy_keyframes",
    "curve_of",
    "find_clip",
    "is_animated",
    "keyframe_by_id",
    "keyframe_times",
    "move_keyframes",
    "next_keyframe_time",
    "paste_keyframes",
    "previous_keyframe_time",
    "remove_keyframe_at",
    "remove_keyframes",
    "set_animation_enabled",
    "set_interpolation",
    "set_keyframe_time",
    "set_keyframe_values",
    "set_tangents",
    "set_value_at",
    "snap_to_frame",
    "value_at",
]
