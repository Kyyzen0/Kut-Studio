"""``describe`` : lire un projet comme un dictionnaire JSON versionné (ADR-0005, §5 ; étape 5, parties A, B et C).

La sortie est **distincte du format de fichier** ``.kut`` : elle est faite pour être lue (par une personne, un outil, un LLM) et peut
évoluer pour la lisibilité sans casser les projets. Elle décrit le projet, ses médias, ses séquences, leurs pistes, leurs clips (place,
source, vitesse, audio de base, transform), leurs transitions et leurs repères (partie A), puis les effets visuels et audio, l'étalonnage
(réglage et graphe de nœuds), le compositing, la composition nodale, les images clés, les bus, les inserts, l'automation de volume et le
ducking (partie B), enfin la bibliothèque, les guides, le flou de mouvement, la grille rythmique, les classements, la source Multicam, le
style des calques graphiques, le style de texte et le suivi (partie C). Plus rien n'est « non décrit » : la clé ``not_described`` reste
(liste vide) pour nommer un futur champ sans décision, et la sortie ne met jamais une clé vide à la place d'une donnée qu'elle ne lit pas.

Les valeurs sont celles **saisies** (statiques) du modèle, jamais celles qu'évalue la scène à un instant : quand des images clés
(``transform_keyframes``, ``animation``) existent, la valeur rendue vient de la courbe, et un suivi (``tracking``) peut encore l'écraser
au rendu.

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
from typing import Any

from ..project_model import Clip, MediaAsset, Project, Sequence, Track
from . import describe_audio, describe_color, describe_composition, describe_shapes, describe_structure, describe_tracking
from .describe_coverage import NOT_DESCRIBED_SECTIONS
from .describe_values import enum, number, optional_text, text

DESCRIBE_VERSION = 1
"""Version du format de la sortie de :func:`describe`. Elle change quand une clé disparaît ou change de sens ; ajouter une clé
n'est pas une rupture : les parties B et C (effets, couleur, composition, images clés, bus, puis bibliothèque, Multicam, suivi, style) ne
font qu'**ajouter** des clés (et retirer des noms de ``not_described``) à la forme de la partie A, qui n'a pas encore été publiée dans
une version : elle reste à ``1``."""

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
        data["timecode"] = text(asset.timecode)
    if asset.timecode_fps:
        data["timecode_fps"] = number(asset.timecode_fps)
    if asset.time_reference is not None:
        data["time_reference"] = number(asset.time_reference)
    if asset.reel:
        data["reel"] = text(asset.reel)
    if asset.camera:
        data["camera"] = text(asset.camera)
    if asset.creation_time:
        data["creation_time"] = text(asset.creation_time)
    return data


def _media(asset: MediaAsset, check_media: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": text(asset.id), "name": text(asset.name), "path": text(asset.path), "media_type": text(asset.media_type),
        "duration": number(asset.duration), "width": number(asset.width), "height": number(asset.height),
        "fps": number(asset.fps), "has_audio": bool(asset.has_audio), "color_matrix": text(asset.color_matrix),
        "present": _present(asset) if check_media else None,
    }
    metadata = _metadata(asset)
    if metadata:
        data["metadata"] = metadata
    return data


def _time_remapping(clip: Clip) -> dict[str, Any]:
    remapping = clip.time_remapping
    return {
        "speed": number(remapping.speed), "reverse": bool(remapping.reverse), "freeze_mode": enum(remapping.freeze_mode),
        "freeze_source_time": number(remapping.freeze_source_time), "freeze_duration": number(remapping.freeze_duration),
        "interpolation": enum(remapping.interpolation), "flow_quality": enum(remapping.flow_quality),
        "preserve_pitch": bool(remapping.preserve_pitch), "remap_audio": bool(remapping.remap_audio),
        "anchor": number(remapping.anchor), "duration": number(remapping.duration),
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


def _clip(clip: Clip, length: float | None, lookup: describe_tracking.ClipLookup) -> dict[str, Any]:
    start = float(clip.timeline_start)
    end = start + length if length is not None else None
    return {
        "id": text(clip.id), "kind": _kind(clip), "asset_id": optional_text(clip.asset_id),
        "sequence_id": optional_text(clip.sequence_id), "angle_id": optional_text(clip.angle_id),
        "template_slot": optional_text(clip.template_slot), "label": text(clip.label), "text": text(clip.text),
        "enabled": bool(clip.enabled), "timeline_start": number(start), "duration": number(length),
        "timeline_end": number(end), "source_in": number(clip.source_in), "source_out": number(clip.source_out),
        "time_remapping": _time_remapping(clip), "has_speed_curve": bool(clip.has_speed_curve),
        "transform": describe_shapes.transform(clip.transform), "gain_db": number(clip.gain_db), "pan": number(clip.pan),
        "fade_in": number(clip.fade_in), "fade_out": number(clip.fade_out), "graphic": describe_shapes.graphic(clip.graphic),
        "has_keyframes": bool(clip.transform_keyframes or clip.animation),
        "transform_keyframes": describe_shapes.keyframes(clip.transform_keyframes),
        "effects": describe_shapes.effects(clip.effects), "audio_effects": describe_shapes.effects(clip.audio_effects),
        "color_grade": describe_color.color_graph(clip.color_grade),
        "compositing": describe_shapes.compositing(clip.compositing),
        "animation": describe_shapes.keyframes(clip.animation),
        "composition": describe_composition.composition(clip.composition),
        "text_style": describe_shapes.text_style(clip.text_style),
        "tracking": describe_tracking.tracking(clip, lookup),
    }


def _track(track: Track, lookup: describe_tracking.ClipLookup) -> tuple[dict[str, Any], list[float | None], list[bool]]:
    """La piste décrite, la durée de chacun de ses clips et leur état (pour la durée de la séquence, sans recalculer)."""
    lengths = [_length(clip) for clip in track.clips]
    data = {
        "id": text(track.id), "name": text(track.name), "type": text(track.type), "locked": bool(track.locked),
        "visible": bool(track.visible), "muted": bool(track.muted), "solo": bool(track.solo),
        "volume_db": number(track.volume_db), "pan": number(track.pan), "audio_role": text(track.audio_role),
        "bus_id": describe_audio.bus_id(track), "inserts": describe_shapes.effects(track.inserts),
        "automation": describe_audio.automation(track.automation), "ducking_config": describe_audio.ducking_config(track.ducking_config),
        "clips": [_clip(clip, length, lookup) for clip, length in zip(track.clips, lengths)],
    }
    return data, lengths, [bool(clip.enabled) for clip in track.clips]


def _sequence(sequence: Sequence, active: bool) -> dict[str, Any]:
    tracks: list[dict[str, Any]] = []
    ends: list[float] = []
    unknown_end = False
    lookup = describe_tracking.ClipLookup(sequence)
    for track in sequence.tracks:
        data, lengths, enabled = _track(track, lookup)
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
        "id": text(sequence.id), "name": text(sequence.name), "active": active, "width": number(sequence.width),
        "height": number(sequence.height), "fps": number(sequence.fps), "duration": number(duration),
        "format_source": optional_text(sequence.format_source),
        "tracks": tracks,
        "transitions": [
            {
                "id": text(item.id), "from_clip_id": text(item.from_clip_id), "to_clip_id": text(item.to_clip_id),
                "type": enum(item.type), "duration": number(item.duration),
            }
            for item in sequence.transitions
        ],
        "markers": [
            {"id": text(item.id), "time_seconds": number(item.time_seconds), "name": text(item.name), "category": text(item.category)}
            for item in sequence.markers
        ],
        "buses": describe_audio.buses(sequence.buses),
        "ducking_sidechains": describe_audio.sidechains(sequence.ducking_sidechains),
        "guides": describe_structure.guides(sequence.guides), "motion_blur": describe_structure.motion_blur(sequence.motion_blur),
        "multicam": describe_structure.multicam(sequence), "beat_grid": describe_structure.beat_grid(sequence.beat_grid),
        "generated_groups": describe_structure.generated_groups(sequence.generated_groups),
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
            "name": text(project.name),
            "width": current["width"] if current else None,
            "height": current["height"] if current else None,
            "fps": current["fps"] if current else None,
            "duration": current["duration"] if current else None,
            "active_sequence": current["id"] if current else None,
        },
        "media": [_media(asset, check_media) for asset in project.media_assets],
        "library": describe_structure.library(project),
        "color_presets": describe_color.presets(project.color_presets),
        "sequences": sequences,
        "not_described": list(NOT_DESCRIBED_SECTIONS),
    }


def describe_json(project: Project, *, check_media: bool = False, indent: int | None = 2) -> str:
    """:func:`describe` en texte JSON : ``allow_nan=False`` (un ``NaN`` ferait échouer au lieu de s'écrire), caractères non ASCII
    conservés, mêmes octets pour le même projet."""
    return json.dumps(describe(project, check_media=check_media), ensure_ascii=False, allow_nan=False, indent=indent)


__all__ = ["DESCRIBE_VERSION", "describe", "describe_json"]
