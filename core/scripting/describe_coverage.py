"""Ce que ``describe`` dit du modèle, champ par champ (ADR-0005, §5) : la **table de complétude**.

Chaque champ d'une dataclass du modèle persisté est dans **exactement une** de ces trois situations :

* **décrit** : :data:`DESCRIBED` donne le chemin de sa clé dans la sortie (``sequences[].tracks[].locked``) ;
* **exclu** : :data:`EXCLUDED` dit pourquoi il n'a pas à être décrit (état d'affichage, valeur d'exécution, redondance) ;
* **en dette** : il est dans ``tests/data/describe_field_debt.json`` avec sa *section* (``effects``, ``color``…), c'est la
  « partie B » : la sortie ne le décrit pas encore, et le dit (:data:`NOT_DESCRIBED_SECTIONS`, clé ``not_described``).

``tests/test_describe_completeness.py`` impose la règle : un champ du modèle dans aucune des trois est refusé (un nouveau champ
oblige à une décision), et la dette ne peut que **diminuer** (un champ désormais décrit ou exclu doit en sortir). Les chemins
déclarés ici sont relus contre une vraie sortie : un champ « décrit » dont la clé n'existe pas fait échouer le test.

Ce module est de la donnée : il n'importe rien du modèle.
"""

from __future__ import annotations

COVERED_CLASSES: tuple[str, ...] = (
    "Project", "MediaAsset", "Sequence", "Track", "Clip", "Marker", "Transition", "TimeRemapping", "ClipTransform", "GraphicOverlay",
)
"""Les dataclasses du modèle persisté dont chaque champ doit être décidé (:mod:`core.project_model`, :mod:`core.transitions`,
:mod:`core.time_remapping`, :mod:`core.visual_effects`, :mod:`core.graphics`)."""

_TRANSFORM_FIELDS = (
    "position_x", "position_y", "scale", "rotation", "opacity", "anchor_x", "anchor_y", "scale_x", "scale_y", "skew", "flip_h",
    "flip_v", "fill", "pan_x", "pan_y",
)
_TIME_FIELDS = (
    "speed", "reverse", "freeze_mode", "freeze_source_time", "freeze_duration", "interpolation", "flow_quality", "preserve_pitch",
    "remap_audio", "anchor", "duration",
)
_GRAPHIC_FIELDS = (
    "type", "text", "source_path", "width", "height", "visible", "locked", "parent_id", "group_id", "z_order",
)

DESCRIBED: dict[str, dict[str, str]] = {
    "Project": {
        "name": "project.name",
        "media_assets": "media",
        "sequences": "sequences",
        "active_sequence_id": "project.active_sequence",
    },
    "MediaAsset": {
        "id": "media[].id", "path": "media[].path", "name": "media[].name", "duration": "media[].duration",
        "width": "media[].width", "height": "media[].height", "fps": "media[].fps", "media_type": "media[].media_type",
        "has_audio": "media[].has_audio", "color_matrix": "media[].color_matrix",
        "timecode": "media[].metadata.timecode", "timecode_fps": "media[].metadata.timecode_fps",
        "time_reference": "media[].metadata.time_reference", "reel": "media[].metadata.reel", "camera": "media[].metadata.camera",
        "creation_time": "media[].metadata.creation_time",
    },
    "Sequence": {
        "id": "sequences[].id", "name": "sequences[].name", "width": "sequences[].width", "height": "sequences[].height",
        "fps": "sequences[].fps", "tracks": "sequences[].tracks", "markers": "sequences[].markers",
        "transitions": "sequences[].transitions", "format_source": "sequences[].format_source",
    },
    "Track": {
        "id": "sequences[].tracks[].id", "name": "sequences[].tracks[].name", "type": "sequences[].tracks[].type",
        "clips": "sequences[].tracks[].clips", "locked": "sequences[].tracks[].locked",
        "visible": "sequences[].tracks[].visible", "muted": "sequences[].tracks[].muted", "solo": "sequences[].tracks[].solo",
        "volume_db": "sequences[].tracks[].volume_db", "pan": "sequences[].tracks[].pan",
        "audio_role": "sequences[].tracks[].audio_role",
    },
    "Clip": {
        "id": "sequences[].tracks[].clips[].id", "asset_id": "sequences[].tracks[].clips[].asset_id",
        "timeline_start": "sequences[].tracks[].clips[].timeline_start",
        "source_in": "sequences[].tracks[].clips[].source_in", "source_out": "sequences[].tracks[].clips[].source_out",
        "enabled": "sequences[].tracks[].clips[].enabled", "label": "sequences[].tracks[].clips[].label",
        "text": "sequences[].tracks[].clips[].text", "transform": "sequences[].tracks[].clips[].transform",
        "gain_db": "sequences[].tracks[].clips[].gain_db", "pan": "sequences[].tracks[].clips[].pan",
        "fade_in": "sequences[].tracks[].clips[].fade_in", "fade_out": "sequences[].tracks[].clips[].fade_out",
        "time_remapping": "sequences[].tracks[].clips[].time_remapping",
        "graphic": "sequences[].tracks[].clips[].graphic", "sequence_id": "sequences[].tracks[].clips[].sequence_id",
        "angle_id": "sequences[].tracks[].clips[].angle_id", "template_slot": "sequences[].tracks[].clips[].template_slot",
    },
    "Marker": {
        "id": "sequences[].markers[].id", "time_seconds": "sequences[].markers[].time_seconds",
        "name": "sequences[].markers[].name", "category": "sequences[].markers[].category",
    },
    "Transition": {
        "id": "sequences[].transitions[].id", "from_clip_id": "sequences[].transitions[].from_clip_id",
        "to_clip_id": "sequences[].transitions[].to_clip_id", "type": "sequences[].transitions[].type",
        "duration": "sequences[].transitions[].duration",
    },
    "TimeRemapping": {
        name: f"sequences[].tracks[].clips[].time_remapping.{name}" for name in _TIME_FIELDS
    },
    "ClipTransform": {
        name: f"sequences[].tracks[].clips[].transform.{name}" for name in _TRANSFORM_FIELDS
    },
    "GraphicOverlay": {
        name: f"sequences[].tracks[].clips[].graphic.{name}" for name in _GRAPHIC_FIELDS
    },
}
"""``{classe: {champ: chemin de la clé dans la sortie}}`` ; ``[]`` marque une liste. Le nom de la clé est celui du champ."""

DERIVED: dict[str, str] = {
    "describe_version": "Version du format de la sortie (entier).",
    "not_described": "Sections du modèle que cette version ne décrit pas encore (la liste fait foi : rien n'est affirmé à leur sujet).",
    "project.width": "Taille, cadence et durée du projet : celles de la séquence active (``Project`` les délègue).",
    "project.height": "Voir ``project.width``.",
    "project.fps": "Voir ``project.width``.",
    "project.duration": "Voir ``project.width`` : fin du dernier clip activé de la séquence active.",
    "media[].present": "Le fichier existe-t-il ? ``null`` tant que la présence n'est pas demandée (aucun accès disque).",
    "sequences[].active": "La séquence est-elle celle que la timeline édite ?",
    "sequences[].duration": "Fin du dernier clip activé de la séquence (``0`` pour une séquence vide).",
    "sequences[].tracks[].clips[].kind": "``media``, ``sequence`` (clip imbriqué), ``composition`` ou ``graphic``.",
    "sequences[].tracks[].clips[].duration": "Durée sur la timeline, dérivée du remappage (jamais stockée à part).",
    "sequences[].tracks[].clips[].timeline_end": "``timeline_start`` + ``duration``.",
    "sequences[].tracks[].clips[].has_speed_curve": "La vitesse est animée : ``time_remapping.speed`` n'est alors que la valeur statique.",
    "sequences[].tracks[].clips[].has_keyframes": "Le clip porte des images clés (leur contenu n'est pas encore décrit).",
}
"""Les clés de la sortie qui ne sont pas un champ du modèle, avec ce qu'elles veulent dire."""

EXCLUDED: dict[str, dict[str, str]] = {
    "MediaAsset": {
        "color_space": "Valeur d'exécution, sondée à la demande et jamais écrite dans un projet (docstring de MediaAsset).",
    },
    "Track": {
        "armed": "Réservé à un futur enregistrement audio : sans effet sur le rendu actuel, et aucun verbe ne le règle.",
        "height_mode": "Hauteur de la piste dans la timeline : état d'affichage de l'interface, sans effet sur le rendu.",
        "collapsed": "Piste réduite à une ligne dans la timeline : état d'affichage de l'interface, sans effet sur le rendu.",
    },
    "Clip": {
        "track_id": "Redondant : un clip est décrit sous la piste qui le porte (``tracks[].clips[]``).",
    },
}
"""``{classe: {champ: raison}}`` : des champs qu'il est juste de ne pas décrire."""

NOT_DESCRIBED_SECTIONS: tuple[str, ...] = (
    "audio_automation", "audio_buses", "audio_effects", "color", "compositing", "composition", "effects", "graphic_style", "keyframes",
    "library", "mograph", "multicam", "social", "text_style", "tracking",
)
"""Les sections de ``tests/data/describe_field_debt.json`` (triées) : la clé ``not_described`` de la sortie. Un test vérifie que
cette liste est exactement l'ensemble des sections de la dette ; la partie B en retire à mesure qu'elle les décrit."""

__all__ = ["COVERED_CLASSES", "DERIVED", "DESCRIBED", "EXCLUDED", "NOT_DESCRIBED_SECTIONS"]
