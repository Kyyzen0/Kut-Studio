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
    "Keyframe", "ClipEffect", "AudioEffect", "AudioBus", "TrackAutomation", "AutomationPoint", "DuckingConfig", "DuckingSidechain",
    "ColorGrade", "ColorCurves", "ColorCurve", "Wheel", "LUTResource", "ColorNodeGraph", "ColorNode", "ColorMixer", "NodeLink",
    "Qualifier", "ColorPreset", "Mask", "MaskKeyframe", "Compositing", "ChromaKey", "Composition", "CompositionGraph", "MediaNode",
    "GraphicNode", "SolidNode", "TransformNode", "MergeNode", "MaskNode", "KeyNode", "EffectsNode", "GradeNode", "OutputNode",
)
"""Les dataclasses du modèle persisté dont chaque champ doit être décidé (:mod:`core.project_model`, :mod:`core.transitions`,
:mod:`core.time_remapping`, :mod:`core.visual_effects`, :mod:`core.graphics`, et, depuis la partie B, les effets, l'audio avancé,
l'étalonnage, le compositing, la composition et les images clés : :mod:`core.animation`, :mod:`core.effects_model`,
:mod:`core.audio_effects_model`, :mod:`core.audio_buses`, :mod:`core.audio_automation`, :mod:`core.color_grading`,
:mod:`core.color_nodes`, :mod:`core.color_qualifier`, :mod:`core.node_graph`, :mod:`core.compositing`, :mod:`core.composition`)."""

_CLIP = "sequences[].tracks[].clips[]"
_TRACK = "sequences[].tracks[]"
_GRADE = f"{_CLIP}.color_grade.nodes[].grade"
_COMP = f"{_CLIP}.composition"
_NODES = f"{_COMP}.graph.nodes[]"
_MASK = f"{_CLIP}.compositing.masks[]"


def _under(prefix: str, *names: str) -> dict[str, str]:
    return {name: f"{prefix}.{name}" for name in names}

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
        "color_presets": "color_presets",
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
        "buses": "sequences[].buses", "ducking_sidechains": "sequences[].ducking_sidechains",
    },
    "Track": {
        "id": "sequences[].tracks[].id", "name": "sequences[].tracks[].name", "type": "sequences[].tracks[].type",
        "clips": "sequences[].tracks[].clips", "locked": "sequences[].tracks[].locked",
        "visible": "sequences[].tracks[].visible", "muted": "sequences[].tracks[].muted", "solo": "sequences[].tracks[].solo",
        "volume_db": "sequences[].tracks[].volume_db", "pan": "sequences[].tracks[].pan",
        "audio_role": "sequences[].tracks[].audio_role",
        **_under(_TRACK, "inserts", "bus_id", "automation", "ducking_config"),
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
        **_under(_CLIP, "transform_keyframes", "effects", "audio_effects", "color_grade", "compositing", "animation", "composition"),
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
    # --- Partie B : images clés, effets, audio avancé, étalonnage, compositing, composition ---
    "Keyframe": _under(f"{_CLIP}.transform_keyframes[]", "property_name", "time_seconds", "value", "interpolation", "in_slope",
                       "out_slope", "tangent_mode", "id"),
    "ClipEffect": _under(f"{_CLIP}.effects[]", "id", "type", "enabled", "params"),
    "AudioEffect": _under(f"{_CLIP}.audio_effects[]", "id", "type", "enabled", "params"),
    "AudioBus": _under("sequences[].buses[]", "id", "name", "volume_db", "muted", "inserts"),
    "TrackAutomation": _under(f"{_TRACK}.automation", "points"),
    "AutomationPoint": _under(f"{_TRACK}.automation.points[]", "time_seconds", "gain_db", "fade_seconds"),
    "DuckingConfig": _under(f"{_TRACK}.ducking_config", "threshold_db", "reduction_db", "attack_seconds", "release_seconds"),
    "DuckingSidechain": _under("sequences[].ducking_sidechains[]", "id", "music_track_id", "voice_track_id", "config", "enabled"),
    "ColorGrade": _under(_GRADE, "exposure", "contrast", "saturation", "temperature", "hue", "shadows", "highlights", "curves", "lut",
                         "enabled", "lift", "gamma", "gain", "offset"),
    "ColorCurves": _under(f"{_GRADE}.curves", "master", "red", "green", "blue"),
    "ColorCurve": {"points": f"{_GRADE}.curves.master"},
    "Wheel": _under(f"{_GRADE}.lift", "r", "g", "b", "y"),
    "LUTResource": _under(f"{_GRADE}.lut", "path", "title", "sha1", "size", "missing"),
    "ColorNodeGraph": _under(f"{_CLIP}.color_grade", "nodes", "links"),
    "ColorNode": _under(f"{_CLIP}.color_grade.nodes[]", "id", "grade", "label", "qualifier", "windows", "blur", "sharpen"),
    "ColorMixer": _under(f"{_CLIP}.color_grade.nodes[]", "id", "kind"),
    "NodeLink": _under(f"{_CLIP}.color_grade.links[]", "source", "target", "port"),
    "Qualifier": _under(f"{_CLIP}.color_grade.nodes[].qualifier", "enabled", "use_hue", "hue_center", "hue_width", "hue_soft",
                        "use_sat", "sat_low", "sat_high", "sat_soft", "use_lum", "lum_low", "lum_high", "lum_soft", "invert"),
    "ColorPreset": _under("color_presets[]", "id", "name", "description", "category", "grade", "builtin"),
    "Mask": _under(_MASK, "shape", "position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity",
                   "inverted", "keyframes", "mode", "points", "name", "id", "handles"),
    "MaskKeyframe": _under(f"{_MASK}.keyframes[]", "property_name", "time_seconds", "value"),
    "Compositing": _under(f"{_CLIP}.compositing", "masks", "chroma_key", "blend_mode"),
    "ChromaKey": _under(f"{_CLIP}.compositing.chroma_key", "enabled", "color", "tolerance", "softness", "spill_suppression"),
    "Composition": _under(_COMP, "graph", "duration", "animation"),
    "CompositionGraph": _under(f"{_COMP}.graph", "nodes", "links"),
    "MediaNode": _under(_NODES, "id", "asset_id", "start", "source_in", "source_out", "label", "gain_db", "muted", "fill", "pan_x",
                        "pan_y"),
    "GraphicNode": _under(_NODES, "id", "graphic", "start", "duration", "transform", "transform_keyframes", "animation", "label",
                          "masks"),
    "SolidNode": _under(_NODES, "id", "color", "label"),
    "TransformNode": _under(_NODES, "id", "transform", "keyframes", "label"),
    "MergeNode": _under(_NODES, "id", "blend", "opacity", "label"),
    "MaskNode": _under(_NODES, "id", "masks", "label"),
    "KeyNode": _under(_NODES, "id", "key", "label"),
    "EffectsNode": _under(_NODES, "id", "effects", "label"),
    "GradeNode": _under(_NODES, "id", "grade", "label"),
    "OutputNode": _under(_NODES, "id"),
}
"""``{classe: {champ: chemin de la clé dans la sortie}}`` ; ``[]`` marque une liste. Le nom de la clé est celui du champ."""

SHAPE_ALIASES: dict[str, str] = {
    f"{_CLIP}.animation[]": f"{_CLIP}.transform_keyframes[]",
    f"{_COMP}.animation[]": f"{_CLIP}.transform_keyframes[]",
    f"{_NODES}.transform_keyframes[]": f"{_CLIP}.transform_keyframes[]",
    f"{_NODES}.animation[]": f"{_CLIP}.transform_keyframes[]",
    f"{_NODES}.keyframes[]": f"{_CLIP}.transform_keyframes[]",
    f"{_NODES}.effects[]": f"{_CLIP}.effects[]",
    f"{_TRACK}.inserts[]": f"{_CLIP}.audio_effects[]",
    "sequences[].buses[].inserts[]": f"{_CLIP}.audio_effects[]",
    f"{_NODES}.transform": f"{_CLIP}.transform",
    f"{_NODES}.graphic": f"{_CLIP}.graphic",
    f"{_NODES}.masks[]": _MASK,
    f"{_CLIP}.color_grade.nodes[].windows[]": _MASK,
    f"{_NODES}.key": f"{_CLIP}.compositing.chroma_key",
    f"{_NODES}.grade": f"{_CLIP}.color_grade",
    f"{_COMP}.graph.links[]": f"{_CLIP}.color_grade.links[]",
    "color_presets[].grade": _GRADE,
    f"{_GRADE}.curves.red": f"{_GRADE}.curves.master",
    f"{_GRADE}.curves.green": f"{_GRADE}.curves.master",
    f"{_GRADE}.curves.blue": f"{_GRADE}.curves.master",
    f"{_GRADE}.gamma": f"{_GRADE}.lift",
    f"{_GRADE}.gain": f"{_GRADE}.lift",
    f"{_GRADE}.offset": f"{_GRADE}.lift",
    "sequences[].ducking_sidechains[].config": f"{_TRACK}.ducking_config",
}
"""Une forme décrite à plusieurs endroits n'est déclarée qu'une fois (:data:`DESCRIBED`) : ``{chemin de l'écho: chemin de la forme
déclarée}``. Un test vérifie que chaque écho existe dans une vraie sortie et que ses clés sont celles de la forme déclarée (mêmes
fonctions de ``describe_shapes.py``). Les images clés d'un nœud de composition, les entrées d'un insert, les fenêtres d'un nœud
d'étalonnage, un préréglage ou les trois autres roues en sont des exemples."""

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
    "sequences[].tracks[].clips[].has_keyframes": "Le clip porte des images clés (leur contenu : ``transform_keyframes`` et ``animation``).",
    "sequences[].tracks[].clips[].color_grade.nodes[].type": (
        "``corrector`` (un réglage) ou ``mixer`` (un mélangeur) ; un réglage seul de clip est décrit comme le nœud ``n1`` d'un graphe."),
    "sequences[].tracks[].clips[].compositing.masks[].nodes": (
        "Pour la forme ``path`` : ``points`` et ``handles`` réunis, un objet ``{x, y, in_dx, in_dy, out_dx, out_dy}`` par nœud (ce que "
        "``set_mask_points`` attend) ; ``null`` pour les autres formes."),
    "sequences[].tracks[].clips[].composition.graph.nodes[].type": (
        "``media``, ``graphic``, ``solid``, ``transform``, ``merge``, ``mask``, ``key``, ``effects``, ``grade`` ou ``output`` "
        "(la classe du nœud)."),
    **{
        f"{_MASK}.points[].{name}": "Un sommet de polygone ou une ancre de courbe : fraction de la boîte du masque (``set_mask_points``)."
        for name in ("x", "y")
    },
    **{
        f"{_MASK}.handles[].{name}": "Les tangentes d'un nœud de courbe, relatives à l'ancre, dans l'ordre de ``points``."
        for name in ("in_dx", "in_dy", "out_dx", "out_dy")
    },
    **{
        f"{_MASK}.nodes[].{name}": "Un nœud de courbe : ancre et tangentes réunies (voir ``nodes``)."
        for name in ("x", "y", "in_dx", "in_dy", "out_dx", "out_dy")
    },
}
"""Les clés de la sortie qui ne sont pas un champ du modèle, avec ce qu'elles veulent dire. Les noms des ``params`` d'un effet sont
ceux du modèle (``EFFECT_PARAMETER_SPECS``, ``AUDIO_EFFECT_PARAMETER_SPECS``) : une table de valeurs, pas des clés fixes."""

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
    "TrackAutomation": {
        "track_id": "Étiquette pour les messages d'erreur, hors de la comparaison (docstring de TrackAutomation) : redondante avec la piste qui porte la courbe.",
    },
    "LUTResource": {
        "source_path": "Chemin absolu de travail, volontairement non sérialisé (docstring de LUTResource) : ``path`` est la référence portable du projet.",
    },
}
"""``{classe: {champ: raison}}`` : des champs qu'il est juste de ne pas décrire."""

NOT_DESCRIBED_SECTIONS: tuple[str, ...] = ("graphic_style", "library", "mograph", "multicam", "social", "text_style", "tracking")
"""Les sections de ``tests/data/describe_field_debt.json`` (triées) : la clé ``not_described`` de la sortie. Un test vérifie que
cette liste est exactement l'ensemble des sections de la dette ; la partie B en retire à mesure qu'elle les décrit."""

__all__ = ["COVERED_CLASSES", "DERIVED", "DESCRIBED", "EXCLUDED", "NOT_DESCRIBED_SECTIONS", "SHAPE_ALIASES"]
