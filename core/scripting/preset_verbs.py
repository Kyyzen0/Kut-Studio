"""Presets intégrés d'effets et d'étalonnage : ``apply_effect_preset``, ``apply_color_preset`` (lot 10 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction que la bibliothèque de l'interface appelle
(``ui/main_window_mixins/presets.py`` : :func:`core.effects_library.apply_preset_to_clip` ; ``color_grading.py`` :
:meth:`core.color_grading.ColorGradingService.apply_preset`).

Règles de ce module :

- **intégrés seulement** : le preset se désigne par son identifiant parmi ceux livrés avec l'application (énumération). Les presets de
  l'utilisateur vivent dans sa configuration, hors du projet : un script ne les lit pas (ADR-0005, décision 5), et le fichier d'un script
  ne se comporterait pas pareil d'une machine à l'autre. Capturer la chaîne d'un clip en preset (``snapshot_clip_preset``) écrit cette
  bibliothèque : ce n'est pas un verbe ;
- **verrous** : piste et calque verrouillés sont refusés (par le verbe, puis par le modèle) ; les effets visuels ne vont que sur une piste
  vidéo, l'étalonnage sur une piste vidéo ou graphique (comme les autres verbes de couleur) ;
- **rien n'est perdu en silence** : un effet à instance unique (couleur, N&B, sépia, vignette) est *remplacé* par celui du preset, et ses
  images clés ``fx.*`` disparaissent avec lui (ADR-0008, décision 7). L'interface le fait sans rien dire ; un script reçoit l'application
  **refusée** tant que l'effet à remplacer est animé (retirer d'abord son animation).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..color_grading import ColorGradingService, builtin_color_presets
from ..effects_library import EffectPreset, apply_preset_to_clip, builtin_presets, preset_effects_to_apply
from ..effects_model import is_single_instance, split_fx_property
from ..project_model import Project
from ..timeline_operations import find_clip
from .resource_limits import require_effect_room
from .verbs import Kind, Operation, Param, Verb, _CLIP, _NODE, _editable_clip, _grade_clip

_EFFECT_PRESETS = tuple(preset.id for preset in builtin_presets())
_COLOR_PRESETS = tuple(preset.id for preset in builtin_color_presets())


def _animated_effect_ids(project: Project, clip_id: str) -> set[str]:
    clip = find_clip(project, clip_id)
    return {parts[0] for keyframe in clip.animation or () if (parts := split_fx_property(keyframe.property_name)) is not None}


def _require_no_animated_replacement(project: Project, clip_id: str, preset: EffectPreset) -> None:
    """Le preset remplace les effets à instance unique de même type : aucun ne doit être animé (ses images clés seraient perdues)."""
    clip = find_clip(project, clip_id)
    animated = _animated_effect_ids(project, clip_id)
    incoming = {effect.type for effect in preset.effects if is_single_instance(effect.type)}
    clash = sorted(effect.id for effect in clip.effects if effect.type in incoming and effect.id in animated)
    if clash:
        raise ValueError(
            f"Le preset '{preset.id}' remplacerait l'effet animé {', '.join(repr(item) for item in clash)} du clip '{clip_id}' : "
            "ses images clés seraient perdues. Retirez d'abord son animation, ou l'effet (remove_effect)."
        )


def _apply_effect_preset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _editable_clip(project, a["clip"], track_types=("video",))
        preset = next(item for item in builtin_presets() if item.id == a["preset"])
        _require_no_animated_replacement(project, clip_id, preset)
        clip = find_clip(project, clip_id)
        # Le nombre exact d'effets ajoutés : la chaîne finale que calcule le modèle, moins celle d'avant (un effet à instance unique en remplace un).
        require_effect_room(clip, len(preset_effects_to_apply(preset, list(clip.effects))) - len(clip.effects))
        return apply_preset_to_clip(project, clip_id, preset)

    return operation


def _apply_color_preset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, node = _grade_clip(project, a)
        preset = next(item for item in builtin_color_presets() if item.id == a["preset"])
        return ColorGradingService().apply_preset(project, clip_id, preset, node)

    return operation


PRESET_VERBS: tuple[Verb, ...] = (
    Verb("apply_effect_preset", "Applique un preset d'effets intégré à un clip vidéo : ses effets s'ajoutent à la chaîne, un effet à instance "
                                "unique (couleur, N&B, sépia, vignette) remplace son homologue ; refusé si celui-ci est animé. "
                                "Les presets de l'utilisateur ne sont pas accessibles à un script.",
         (_CLIP, Param("preset", Kind.ENUM, "Le preset intégré, par son identifiant.", choices=_EFFECT_PRESETS)),
         _apply_effect_preset, group="effects"),
    Verb("apply_color_preset", "Applique un preset d'étalonnage intégré à un clip (au nœud `node`, sinon le premier) : le réglage du nœud est "
                               "remplacé, son LUT est gardé. Les presets de l'utilisateur ne sont pas accessibles à un script.",
         (_CLIP, Param("preset", Kind.ENUM, "Le preset intégré, par son identifiant.", choices=_COLOR_PRESETS),
          _NODE),
         _apply_color_preset, group="color"),
)

__all__ = ["PRESET_VERBS"]
