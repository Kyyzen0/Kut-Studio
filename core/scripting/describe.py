"""``describe`` : lire un projet comme un dictionnaire JSON versionné (ADR-0005, §5 ; étape 5, partie A).

La sortie est **distincte du format de fichier** ``.kut`` : elle est faite pour être lue (par une personne, un outil, un LLM) et peut
évoluer pour la lisibilité sans casser les projets. Elle décrit la **structure** : le projet, ses médias, ses séquences, leurs pistes,
leurs clips (place, source, vitesse, audio de base, transform), leurs transitions et leurs repères. Ce qu'elle ne décrit pas encore
(effets, couleur, composition, images clés, bus…) est **nommé** dans la clé ``not_described`` : elle ne met jamais une clé vide à la
place d'une donnée qu'elle ne lit pas.

Garanties, que ``tests/test_describe.py`` vérifie :

* **lecture seule** : le projet n'est jamais modifié (pas même la séquence active, que ``Project.active_sequence`` corrigerait
  pour un identifiant inconnu : ici elle est seulement *lue*), et aucun fichier n'est créé (ni verrou, ni cache) ;
* **JSON strict et déterministe** : pas de ``NaN`` ni d'infini (une valeur non finie est ``null``), flottants arrondis à 6 décimales,
  ordre des clés fixe, ordre des listes = ordre du modèle ; le même projet donne la même sortie, octet pour octet ;
* **aucun accès disque par défaut** : la présence d'un média n'est testée que sur demande (``check_media=True``), et un test qui
  échoue ou lève rend « absent », jamais une exception ;
* **sans récursion** : une séquence imbriquée est *citée* (``sequence_id``), jamais développée ; aucune profondeur à borner.

Les clés gardent le **nom du champ du modèle** (``timeline_start``, ``asset_id``…), les durées sont en **secondes** ; les clés
dérivées sont listées dans :data:`~core.scripting.describe_coverage.DERIVED`.
"""

from __future__ import annotations

import json
import math
import os
from enum import Enum
from typing import Any

from ..project_model import Clip, MediaAsset, Project, Sequence, Track
from .describe_coverage import NOT_DESCRIBED_SECTIONS

DESCRIBE_VERSION = 1
"""Version du format de la sortie de :func:`describe`. Elle change quand une clé disparaît ou change de sens ; ajouter une clé
(la partie B décrit de nouvelles sections) n'est pas une rupture."""

_DECIMALS = 6


def _number(value: Any) -> Any:
    """Un nombre JSON déterministe : flottant arrondi à 6 décimales, ``-0.0`` ramené à ``0.0``, non fini -> ``None``."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, _DECIMALS)
    return rounded + 0.0 if rounded == 0.0 else rounded


def _text(value: Any) -> str:
    """Une chaîne que le JSON et la sortie standard savent écrire (un demi-substitut isolé, possible dans un fichier abîmé, est remplacé)."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
    return text


def _optional_text(value: Any) -> str | None:
    """Une chaîne, ou ``None`` quand elle est vide (« pas de séquence », « pas d'angle »)."""
    text = _text(value)
    return text or None


def _enum(value: Any) -> Any:
    return _text(value.value if isinstance(value, Enum) else value)


def _present(asset: MediaAsset) -> bool | None:
    """Le fichier d'un média existe-t-il ? ``None`` quand la question n'a pas de sens (sous-titres en ligne, chemin vide)."""
    if asset.media_type not in ("video", "audio", "image") or not asset.path:
        return None
    try:
        return os.path.exists(asset.path)
    except (OSError, ValueError, TypeError):      # ``os.path.exists`` avale déjà les deux premières ; la garde reste explicite
        return False


def _metadata(asset: MediaAsset) -> dict[str, Any]:
    """Les métadonnées de la sonde qui ont une valeur (le défaut n'est pas écrit)."""
    data: dict[str, Any] = {}
    if asset.timecode:
        data["timecode"] = _text(asset.timecode)
    if asset.timecode_fps:
        data["timecode_fps"] = _number(asset.timecode_fps)
    if asset.time_reference is not None:
        data["time_reference"] = _number(asset.time_reference)
    if asset.reel:
        data["reel"] = _text(asset.reel)
    if asset.camera:
        data["camera"] = _text(asset.camera)
    if asset.creation_time:
        data["creation_time"] = _text(asset.creation_time)
    return data


def _media(asset: MediaAsset, check_media: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": _text(asset.id), "name": _text(asset.name), "path": _text(asset.path), "media_type": _text(asset.media_type),
        "duration": _number(asset.duration), "width": _number(asset.width), "height": _number(asset.height),
        "fps": _number(asset.fps), "has_audio": bool(asset.has_audio), "color_matrix": _text(asset.color_matrix),
        "present": _present(asset) if check_media else None,
    }
    metadata = _metadata(asset)
    if metadata:
        data["metadata"] = metadata
    return data


def _transform(clip: Clip) -> dict[str, Any]:
    transform = clip.transform
    return {
        "position_x": _number(transform.position_x), "position_y": _number(transform.position_y),
        "scale": _number(transform.scale), "rotation": _number(transform.rotation), "opacity": _number(transform.opacity),
        "anchor_x": _number(transform.anchor_x), "anchor_y": _number(transform.anchor_y),
        "scale_x": _number(transform.scale_x), "scale_y": _number(transform.scale_y), "skew": _number(transform.skew),
        "flip_h": bool(transform.flip_h), "flip_v": bool(transform.flip_v), "fill": bool(transform.fill),
        "pan_x": _number(transform.pan_x), "pan_y": _number(transform.pan_y),
    }


def _time_remapping(clip: Clip) -> dict[str, Any]:
    remapping = clip.time_remapping
    return {
        "speed": _number(remapping.speed), "reverse": bool(remapping.reverse), "freeze_mode": _enum(remapping.freeze_mode),
        "freeze_source_time": _number(remapping.freeze_source_time), "freeze_duration": _number(remapping.freeze_duration),
        "interpolation": _enum(remapping.interpolation), "flow_quality": _enum(remapping.flow_quality),
        "preserve_pitch": bool(remapping.preserve_pitch), "remap_audio": bool(remapping.remap_audio),
        "anchor": _number(remapping.anchor), "duration": _number(remapping.duration),
    }


def _graphic(clip: Clip) -> dict[str, Any] | None:
    graphic = clip.graphic
    if graphic is None:
        return None
    return {
        "type": _enum(graphic.type), "text": _text(graphic.text), "source_path": _optional_text(graphic.source_path),
        "width": _number(graphic.width), "height": _number(graphic.height), "visible": bool(graphic.visible),
        "locked": bool(graphic.locked), "parent_id": _optional_text(graphic.parent_id),
        "group_id": _optional_text(graphic.group_id), "z_order": _number(graphic.z_order),
    }


def _kind(clip: Clip) -> str:
    if clip.composition is not None:
        return "composition"
    if clip.sequence_id:
        return "sequence"
    if clip.graphic is not None:
        return "graphic"
    return "media"


def _length(clip: Clip) -> float | None:
    """Durée sur la timeline, ``None`` si le modèle ne sait pas la calculer (un fichier abîmé ne fait pas échouer la lecture)."""
    try:
        value = float(clip.duration)
    except (ValueError, ArithmeticError):
        return None
    return value if math.isfinite(value) else None


def _clip(clip: Clip, length: float | None) -> dict[str, Any]:
    start = float(clip.timeline_start)
    end = start + length if length is not None else None
    return {
        "id": _text(clip.id), "kind": _kind(clip), "asset_id": _optional_text(clip.asset_id),
        "sequence_id": _optional_text(clip.sequence_id), "angle_id": _optional_text(clip.angle_id),
        "template_slot": _optional_text(clip.template_slot), "label": _text(clip.label), "text": _text(clip.text),
        "enabled": bool(clip.enabled), "timeline_start": _number(start), "duration": _number(length),
        "timeline_end": _number(end), "source_in": _number(clip.source_in), "source_out": _number(clip.source_out),
        "time_remapping": _time_remapping(clip), "has_speed_curve": bool(clip.has_speed_curve),
        "transform": _transform(clip), "gain_db": _number(clip.gain_db), "pan": _number(clip.pan),
        "fade_in": _number(clip.fade_in), "fade_out": _number(clip.fade_out), "graphic": _graphic(clip),
        "has_keyframes": bool(clip.transform_keyframes or clip.animation),
    }


def _track(track: Track) -> tuple[dict[str, Any], list[float | None], list[bool]]:
    """La piste décrite, la durée de chacun de ses clips et leur état (pour la durée de la séquence, sans recalculer)."""
    lengths = [_length(clip) for clip in track.clips]
    data = {
        "id": _text(track.id), "name": _text(track.name), "type": _text(track.type), "locked": bool(track.locked),
        "visible": bool(track.visible), "muted": bool(track.muted), "solo": bool(track.solo),
        "volume_db": _number(track.volume_db), "pan": _number(track.pan), "audio_role": _text(track.audio_role),
        "clips": [_clip(clip, length) for clip, length in zip(track.clips, lengths)],
    }
    return data, lengths, [bool(clip.enabled) for clip in track.clips]


def _sequence(sequence: Sequence, active: bool) -> dict[str, Any]:
    tracks: list[dict[str, Any]] = []
    ends: list[float] = []
    unknown_end = False
    for track in sequence.tracks:
        data, lengths, enabled = _track(track)
        tracks.append(data)
        for clip, length, is_enabled in zip(track.clips, lengths, enabled):
            if not is_enabled:
                continue
            if length is None:
                unknown_end = True
            else:
                ends.append(float(clip.timeline_start) + length)
    duration = None if unknown_end else (max(ends) if ends else 0.0)
    return {
        "id": _text(sequence.id), "name": _text(sequence.name), "active": active, "width": _number(sequence.width),
        "height": _number(sequence.height), "fps": _number(sequence.fps), "duration": _number(duration),
        "format_source": _optional_text(sequence.format_source),
        "tracks": tracks,
        "transitions": [
            {
                "id": _text(item.id), "from_clip_id": _text(item.from_clip_id), "to_clip_id": _text(item.to_clip_id),
                "type": _enum(item.type), "duration": _number(item.duration),
            }
            for item in sequence.transitions
        ],
        "markers": [
            {"id": _text(item.id), "time_seconds": _number(item.time_seconds), "name": _text(item.name), "category": _text(item.category)}
            for item in sequence.markers
        ],
    }


def _active_sequence(project: Project) -> Sequence | None:
    """La séquence que la timeline édite, **sans** la réparer : ``Project.active_sequence`` réécrit l'identifiant actif quand il est
    inconnu (et ajoute une séquence à un projet qui n'en a aucune). Une lecture ne doit rien écrire."""
    for sequence in project.sequences:
        if sequence.id == project.active_sequence_id:
            return sequence
    return project.sequences[0] if project.sequences else None


def describe(project: Project, *, check_media: bool = False) -> dict[str, Any]:
    """Le projet comme un dictionnaire JSON (structure : voir la documentation, « Lire un projet »).

    Args:
        project: le projet à lire ; il n'est jamais modifié.
        check_media: ``True`` pour tester sur le disque la présence de chaque média (``media[].present``) ; ``False`` (défaut), aucun
            accès disque et ``present`` vaut ``null`` (inconnu).

    Returns:
        un dictionnaire strictement sérialisable (:func:`describe_json`), à l'ordre de clés stable.
    """
    active = _active_sequence(project)
    sequences = [_sequence(sequence, sequence is active) for sequence in project.sequences]
    current = next((item for item in sequences if item["active"]), None)
    return {
        "describe_version": DESCRIBE_VERSION,
        "project": {
            "name": _text(project.name),
            "width": current["width"] if current else None,
            "height": current["height"] if current else None,
            "fps": current["fps"] if current else None,
            "duration": current["duration"] if current else None,
            "active_sequence": current["id"] if current else None,
        },
        "media": [_media(asset, check_media) for asset in project.media_assets],
        "sequences": sequences,
        "not_described": list(NOT_DESCRIBED_SECTIONS),
    }


def describe_json(project: Project, *, check_media: bool = False, indent: int | None = 2) -> str:
    """:func:`describe` en texte JSON : ``allow_nan=False`` (un ``NaN`` ferait échouer au lieu de s'écrire), caractères non ASCII
    conservés, mêmes octets pour le même projet."""
    return json.dumps(describe(project, check_media=check_media), ensure_ascii=False, allow_nan=False, indent=indent)


__all__ = ["DESCRIBE_VERSION", "describe", "describe_json"]
