"""Modèle métier des effets de clip pour Kut-Studio (tâche 21.1).

Ce module décrit, sans aucune dépendance à ``PySide6`` ni à FFmpeg,
les effets non destructifs qu'un clip vidéo peut porter :

- :class:`EffectType` — catalogue fermé des effets supportés ;
- :class:`EffectParameterSpec` — bornes et valeur par défaut d'un
  paramètre ;
- :class:`ClipEffect` — instance d'effet (identifiant stable, type,
  activation, paramètres validés) ;
- des fonctions **pures** pour ajouter, modifier, activer/désactiver,
  supprimer et réordonner les effets d'une liste ;
- des opérations au niveau projet qui n'acceptent que les clips des
  pistes ``video`` et rejettent proprement l'audio et les sous-titres.

Les paramètres sont validés explicitement : une valeur hors bornes, un
type inconnu ou un paramètre non numérique lève ``ValueError``. La
couche de persistance (``core.project_io``) s'appuie sur cette
validation pour ignorer les entrées invalides sans casser l'ouverture
d'un projet.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from .project_model import Clip, Project


class EffectType(str, Enum):
    """Types d'effets visuels supportés par un clip vidéo."""

    COLOR_CORRECTION = "color_correction"
    BLUR = "blur"
    SHARPEN = "sharpen"
    VIGNETTE = "vignette"
    BLACK_AND_WHITE = "black_and_white"
    SEPIA = "sepia"
    # --- Vidéo sociale (lumière) ---
    GLOW = "glow"
    CHROMATIC_ABERRATION = "chromatic_aberration"
    HEAT_HAZE = "heat_haze"


@dataclass(frozen=True)
class EffectParameterSpec:
    """Description d'un paramètre d'effet : bornes + valeur par défaut."""

    name: str
    minimum: float
    maximum: float
    default: float


# Paramètres par défaut **explicites** de chaque type d'effet. Un type
# sans entrée (noir et blanc, sépia) n'expose aucun réglage.
EFFECT_PARAMETER_SPECS: dict[EffectType, tuple[EffectParameterSpec, ...]] = {
    EffectType.COLOR_CORRECTION: (
        EffectParameterSpec("brightness", -1.0, 1.0, 0.0),
        EffectParameterSpec("contrast", 0.0, 2.0, 1.0),
        EffectParameterSpec("saturation", 0.0, 3.0, 1.0),
    ),
    EffectType.BLUR: (EffectParameterSpec("intensity", 0.0, 50.0, 2.0),),
    EffectType.SHARPEN: (EffectParameterSpec("intensity", 0.0, 5.0, 1.0),),
    EffectType.VIGNETTE: (EffectParameterSpec("intensity", 0.0, 1.0, 0.5),),
    EffectType.BLACK_AND_WHITE: (),
    EffectType.SEPIA: (),
    # Bloom : les zones plus claires que le seuil, floutées (σ en pixels de la séquence) et ajoutées.
    EffectType.GLOW: (
        EffectParameterSpec("threshold", 0.0, 0.95, 0.6),
        EffectParameterSpec("radius", 0.0, 100.0, 18.0),
        EffectParameterSpec("intensity", 0.0, 4.0, 1.0),
    ),
    # Aberration chromatique : rouge et bleu décalés de part et d'autre (pixels de la séquence).
    EffectType.CHROMATIC_ABERRATION: (EffectParameterSpec("intensity", 0.0, 40.0, 4.0),),
    # Heat haze : chaque ligne glisse d'un nombre entier de pixels, selon une onde qui défile, sous ``top`` du
    # cadre et en fondu sur ``span`` (fractions de la hauteur).
    EffectType.HEAT_HAZE: (
        EffectParameterSpec("amplitude", 0.0, 40.0, 4.0),
        EffectParameterSpec("frequency", 0.001, 0.5, 0.045),
        EffectParameterSpec("speed", 0.0, 40.0, 9.0),
        EffectParameterSpec("top", 0.0, 1.0, 0.35),
        EffectParameterSpec("span", 0.01, 1.0, 0.4),
    ),
}
"""Bornes et défauts par type d'effet."""


SINGLE_INSTANCE_EFFECTS: frozenset[EffectType] = frozenset(
    {
        EffectType.COLOR_CORRECTION,
        EffectType.VIGNETTE,
        EffectType.BLACK_AND_WHITE,
        EffectType.SEPIA,
        EffectType.CHROMATIC_ABERRATION,
        EffectType.HEAT_HAZE,
    }
)
"""Effets qui ne peuvent exister qu'une seule fois par clip.

Le flou et la netteté restent empilables (deux flous successifs ont un
sens), tandis qu'une correction colorimétrique, une vignette, un noir
et blanc ou un sépia doublonnés n'apporteraient rien.
"""


def is_single_instance(effect_type: EffectType | str) -> bool:
    """Indique si ``effect_type`` ne doit exister qu'une fois par clip."""
    return EffectType(effect_type) in SINGLE_INSTANCE_EFFECTS


def parameter_specs(effect_type: EffectType | str) -> tuple[EffectParameterSpec, ...]:
    """Retourne les paramètres déclarés pour ``effect_type``."""
    return EFFECT_PARAMETER_SPECS[EffectType(effect_type)]


def default_parameters(effect_type: EffectType | str) -> dict[str, float]:
    """Retourne une copie des paramètres par défaut de ``effect_type``."""
    return {
        spec.name: float(spec.default)
        for spec in parameter_specs(effect_type)
    }


def _coerce_number(name: str, value: Any) -> float:
    """Convertit ``value`` en flottant fini ou lève ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Le paramètre {name!r} doit être numérique (reçu : {value!r})."
        )
    number = float(value)
    if number != number:  # NaN
        raise ValueError(f"Le paramètre {name!r} ne peut pas être NaN.")
    return number


def validate_parameters(
    effect_type: EffectType | str,
    params: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Valide et normalise les paramètres d'un effet.

    Args:
        effect_type: type d'effet ciblé.
        params: paramètres fournis. ``None`` ou ``{}`` donne les défauts.

    Returns:
        Un dict neuf, dans l'ordre de déclaration des paramètres, avec
        les défauts comblés pour les clés absentes.

    Raises:
        ValueError: si un paramètre est inconnu, non numérique ou hors
            bornes.
    """
    specs = parameter_specs(effect_type)
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ValueError("Les paramètres d'un effet doivent être un mapping.")

    known = {spec.name for spec in specs}
    unknown = set(params) - known
    if unknown:
        raise ValueError(
            f"Paramètre(s) inconnu(s) pour l'effet "
            f"'{EffectType(effect_type).value}' : {sorted(unknown)}."
        )

    resolved: dict[str, float] = {}
    for spec in specs:
        raw = params.get(spec.name, spec.default)
        number = _coerce_number(spec.name, raw)
        if number < spec.minimum or number > spec.maximum:
            raise ValueError(
                f"Paramètre {spec.name!r} hors bornes : {number} "
                f"(attendu [{spec.minimum}, {spec.maximum}])."
            )
        resolved[spec.name] = number
    return resolved


@dataclass(frozen=True)
class ClipEffect:
    """Un effet appliqué à un clip vidéo.

    Attributes:
        id: Identifiant stable de l'effet dans le clip.
        type: Type d'effet (:class:`EffectType`).
        enabled: ``False`` neutralise l'effet sans le supprimer.
        params: Paramètres validés, dans l'ordre de déclaration du type.
    """

    id: str
    type: EffectType
    enabled: bool = True
    params: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Un effet doit porter un identifiant non vide.")
        try:
            effect_type = EffectType(self.type)
        except ValueError as error:
            raise ValueError(f"Type d'effet inconnu : {self.type!r}.") from error
        object.__setattr__(self, "type", effect_type)
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(
            self, "params", validate_parameters(effect_type, self.params)
        )

    # --- Lecture -------------------------------------------------------

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """Noms des paramètres, dans l'ordre de déclaration."""
        return tuple(spec.name for spec in parameter_specs(self.type))

    def parameter(self, name: str) -> float:
        """Retourne la valeur d'un paramètre déclaré."""
        try:
            return self.params[name]
        except KeyError as error:
            raise KeyError(
                f"L'effet '{self.id}' ne possède pas de paramètre {name!r}."
            ) from error

    # --- Dérivations immuables ----------------------------------------

    def with_enabled(self, enabled: bool) -> "ClipEffect":
        """Retourne une copie avec l'état d'activation demandé."""
        return replace(self, enabled=bool(enabled))

    def with_parameters(self, params: Mapping[str, Any]) -> "ClipEffect":
        """Retourne une copie dont les paramètres sont fusionnés puis validés."""
        merged = dict(self.params)
        merged.update(dict(params))
        return replace(self, params=merged)

    def with_parameter(self, name: str, value: Any) -> "ClipEffect":
        """Retourne une copie avec un unique paramètre modifié."""
        return self.with_parameters({name: value})


def create_effect(
    effect_type: EffectType | str,
    *,
    effect_id: str | None = None,
    enabled: bool = True,
    params: Mapping[str, Any] | None = None,
) -> ClipEffect:
    """Fabrique un :class:`ClipEffect` prêt à l'emploi.

    Si ``effect_id`` est omis, un identifiant unique est généré.
    """
    return ClipEffect(
        id=effect_id or f"fx-{uuid.uuid4().hex[:12]}",
        type=EffectType(effect_type),
        enabled=enabled,
        params=validate_parameters(effect_type, params),
    )


# ---------------------------------------------------------------------------
# Fonctions pures sur une liste d'effets
# ---------------------------------------------------------------------------


def effect_by_id(
    effects: Sequence[ClipEffect], effect_id: str
) -> ClipEffect | None:
    """Retourne l'effet d'identifiant ``effect_id`` ou ``None``."""
    for effect in effects:
        if effect.id == effect_id:
            return effect
    return None


def effect_by_type(
    effects: Sequence[ClipEffect], effect_type: EffectType | str
) -> ClipEffect | None:
    """Retourne le premier effet du type demandé ou ``None``."""
    wanted = EffectType(effect_type)
    for effect in effects:
        if effect.type == wanted:
            return effect
    return None


def enabled_effects(effects: Sequence[ClipEffect]) -> list[ClipEffect]:
    """Retourne les effets activés, dans leur ordre d'origine."""
    return [effect for effect in effects if effect.enabled]


def _index_of(effects: Sequence[ClipEffect], effect_id: str) -> int:
    for index, effect in enumerate(effects):
        if effect.id == effect_id:
            return index
    raise KeyError(f"Effet introuvable : {effect_id!r}.")


def add_effect(
    effects: Sequence[ClipEffect], effect: ClipEffect
) -> list[ClipEffect]:
    """Retourne une nouvelle liste avec ``effect`` ajouté à la fin.

    Raises:
        ValueError: si un effet porte déjà cet identifiant, ou si un
            effet « unique » du même type est déjà présent.
    """
    if any(existing.id == effect.id for existing in effects):
        raise ValueError(f"L'effet '{effect.id}' existe déjà sur ce clip.")
    if is_single_instance(effect.type) and effect_by_type(effects, effect.type):
        raise ValueError(
            f"L'effet '{effect.type.value}' ne peut exister qu'une fois "
            "par clip."
        )
    return [*effects, effect]


def remove_effect(
    effects: Sequence[ClipEffect], effect_id: str
) -> list[ClipEffect]:
    """Retourne une nouvelle liste sans l'effet ``effect_id``.

    Raises:
        KeyError: si l'effet n'existe pas.
    """
    index = _index_of(effects, effect_id)
    return [
        effect for position, effect in enumerate(effects) if position != index
    ]


def replace_effect(
    effects: Sequence[ClipEffect], effect_id: str, new_effect: ClipEffect
) -> list[ClipEffect]:
    """Retourne une nouvelle liste où ``effect_id`` est remplacé."""
    index = _index_of(effects, effect_id)
    result = list(effects)
    result[index] = new_effect
    return result


def set_effect_enabled(
    effects: Sequence[ClipEffect], effect_id: str, enabled: bool
) -> list[ClipEffect]:
    """Retourne une nouvelle liste avec l'activation demandée."""
    effect = effect_by_id(effects, effect_id)
    if effect is None:
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    return replace_effect(effects, effect_id, effect.with_enabled(enabled))


def update_effect_parameters(
    effects: Sequence[ClipEffect],
    effect_id: str,
    params: Mapping[str, Any],
) -> list[ClipEffect]:
    """Retourne une nouvelle liste avec les paramètres mis à jour."""
    effect = effect_by_id(effects, effect_id)
    if effect is None:
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    return replace_effect(effects, effect_id, effect.with_parameters(params))


def reorder_effect(
    effects: Sequence[ClipEffect], effect_id: str, new_index: int
) -> list[ClipEffect]:
    """Retourne une nouvelle liste avec ``effect_id`` déplacé à ``new_index``."""
    index = _index_of(effects, effect_id)
    result = list(effects)
    effect = result.pop(index)
    bounded = max(0, min(int(new_index), len(result)))
    result.insert(bounded, effect)
    return result


def move_effect(
    effects: Sequence[ClipEffect], effect_id: str, delta: int
) -> list[ClipEffect]:
    """Déplace un effet de ``delta`` crans (``-1`` = monter, ``+1`` = descendre).

    Un déplacement qui sortirait de la liste est borné : l'effet reste
    en tête ou en queue plutôt que de faire échouer un bouton d'IHM.
    """
    index = _index_of(effects, effect_id)
    return reorder_effect(effects, effect_id, index + int(delta))


# ---------------------------------------------------------------------------
# Animation des paramètres : ``fx.<effet>.<paramètre>`` et cycle de vie (ADR-0008)
# ---------------------------------------------------------------------------

FX_PREFIX = "fx."
"""Préfixe des images clés génériques (``Clip.animation``) qui animent le paramètre d'un effet du clip."""


def fx_property_id(effect_id: str, parameter: str) -> str:
    """``fx.<effet>.<paramètre>``."""
    return f"{FX_PREFIX}{effect_id}.{parameter}"


def split_fx_property(property_id: str) -> tuple[str, str] | None:
    """``(identifiant de l'effet, paramètre)`` d'un nom ``fx.…``, ``None`` si ce n'en est pas un (ou s'il est mal formé).

    Le découpage se fait **par la droite** : le paramètre est ce qui suit le dernier point, l'identifiant ce qui est entre
    ``fx.`` et ce dernier point. Un identifiant d'effet peut contenir des points (``fx.my.blur.intensity`` : effet
    ``my.blur``) ; c'est sans ambiguïté parce qu'aucun nom de paramètre (:data:`EFFECT_PARAMETER_SPECS`) n'en contient.
    """
    if not isinstance(property_id, str) or not property_id.startswith(FX_PREFIX):
        return None
    effect_id, dot, parameter = property_id[len(FX_PREFIX):].rpartition(".")
    if not dot or not effect_id or not parameter:
        return None
    return effect_id, parameter


def prune_effect_animation(clip: "Clip", *, reset_ids: Iterable[str] = ()) -> int:
    """Retire de ``clip.animation`` les images clés ``fx.*`` dont l'effet n'est plus dans ``clip.effects``.

    **Un seul point d'entrée** pour le cycle de vie des images clés d'effet : :func:`set_clip_effects` l'appelle, donc toute
    opération qui change la chaîne d'effets (suppression, préréglage, collage, édition d'un paramètre…) laisse
    ``Clip.animation`` à jour dans la même étape d'historique. C'est aussi ce qui nettoie, à la première édition de la
    chaîne, les images clés orphelines d'un fichier ouvert (le rendu les ignore d'ici là : choix de tolérance de l'ADR-0008,
    une image clé orpheline ne fait jamais échouer le chargement). Un nom ``fx.…`` mal formé est orphelin.

    ``reset_ids`` : identifiants d'effets dont les images clés partent **même si** l'identifiant est encore dans la chaîne
    (un effet remplacé par un préréglage qui reprend son identifiant naît sans animation). Rend le nombre d'images clés
    retirées.
    """
    animation = getattr(clip, "animation", None)
    if not animation:
        return 0
    live = {effect.id for effect in clip.effects} - set(reset_ids)
    kept = []
    for keyframe in animation:
        name = keyframe.property_name
        if isinstance(name, str) and name.startswith(FX_PREFIX):
            parts = split_fx_property(name)
            if parts is None or parts[0] not in live:
                continue
        kept.append(keyframe)
    removed = len(animation) - len(kept)
    if removed:
        clip.animation = kept
    return removed


def set_clip_effects(clip: "Clip", effects: Sequence[ClipEffect], *, reset_ids: Iterable[str] = ()) -> None:
    """Remplace la chaîne d'effets d'un clip **et** tient ``Clip.animation`` à jour (:func:`prune_effect_animation`).

    Toute écriture de ``clip.effects`` dans ``core/`` et ``ui/`` passe ici (garde : ``tests/test_effects_write_guard.py``).
    """
    clip.effects = list(effects)
    prune_effect_animation(clip, reset_ids=reset_ids)


def remap_fx_animation(keyframes: Iterable[Any], id_map: Mapping[str, str]) -> list[Any]:
    """Images clés copiées d'un autre clip : les ``fx.<ancien>.<paramètre>`` visent le nouvel effet (``id_map``,
    « ancien identifiant → nouvel identifiant »), celles dont l'effet n'est pas dans la table sont **abandonnées**.

    Les autres images clés passent telles quelles. Les images clés rendues sont de nouveaux objets (``replace``) ; leur
    identifiant est conservé, l'appelant le renouvelle s'il le souhaite.
    """
    result: list[Any] = []
    for keyframe in keyframes:
        name = keyframe.property_name
        if not (isinstance(name, str) and name.startswith(FX_PREFIX)):
            result.append(keyframe)
            continue
        parts = split_fx_property(name)
        if parts is None or parts[0] not in id_map:
            continue
        result.append(replace(keyframe, property_name=fx_property_id(id_map[parts[0]], parts[1]), id=keyframe.id))
    return result


def effects_with_unique_ids(taken: Iterable[str], incoming: Iterable[ClipEffect]) -> list[ClipEffect]:
    """``incoming`` avec un identifiant neuf pour tout effet dont l'identifiant est déjà pris (``taken`` ou un autre effet
    de ``incoming``) : deux effets d'un clip ne partagent jamais un identifiant, sinon les courbes ``fx.<id>.*`` en
    animeraient deux. Les effets sans collision sont rendus tels quels."""
    used = set(taken)
    result: list[ClipEffect] = []
    for effect in incoming:
        if effect.id in used:
            effect = replace(effect, id=f"fx-{uuid.uuid4().hex[:12]}")
        used.add(effect.id)
        result.append(effect)
    return result


# ---------------------------------------------------------------------------
# Opérations au niveau projet (clips vidéo uniquement)
# ---------------------------------------------------------------------------


def _find_track_and_clip(project: "Project", clip_id: str):
    """Retourne ``(track, clip)`` ou lève ``KeyError``."""
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return track, clip
    raise KeyError(f"Clip introuvable : {clip_id!r}.")


def _require_video_clip(project: "Project", clip_id: str) -> "Clip":
    """Retourne le clip après avoir vérifié qu'il est sur une piste vidéo."""
    track, clip = _find_track_and_clip(project, clip_id)
    if track.type != "video":
        raise ValueError(
            f"Le clip '{clip_id}' est sur une piste '{track.type}' ; "
            "les effets ne s'appliquent qu'aux clips vidéo."
        )
    if track.locked:
        raise ValueError(f"La piste '{track.id}' est verrouillée.")
    if bool(getattr(getattr(clip, "graphic", None), "locked", False)):         # photo de template, calque verrouillé
        raise ValueError(f"Le calque du clip '{clip_id}' est verrouillé.")
    return clip


def clip_effects(project: "Project", clip_id: str) -> list[ClipEffect]:
    """Retourne une copie des effets d'un clip vidéo."""
    return list(_require_video_clip(project, clip_id).effects)


def add_effect_to_clip(
    project: "Project",
    clip_id: str,
    effect_type: EffectType | str,
    *,
    effect_id: str | None = None,
    params: Mapping[str, Any] | None = None,
    enabled: bool = True,
) -> ClipEffect:
    """Ajoute un effet à un clip vidéo et retourne l'effet créé."""
    clip = _require_video_clip(project, clip_id)
    effect = create_effect(
        effect_type, effect_id=effect_id, enabled=enabled, params=params
    )
    set_clip_effects(clip, add_effect(clip.effects, effect))
    return effect


def remove_effect_from_clip(
    project: "Project", clip_id: str, effect_id: str
) -> ClipEffect:
    """Supprime un effet d'un clip vidéo et retourne l'effet retiré."""
    clip = _require_video_clip(project, clip_id)
    removed = effect_by_id(clip.effects, effect_id)
    if removed is None:
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    set_clip_effects(clip, remove_effect(clip.effects, effect_id))
    return removed


def set_clip_effect_enabled(
    project: "Project", clip_id: str, effect_id: str, enabled: bool
) -> ClipEffect:
    """Active ou désactive un effet et retourne sa nouvelle version."""
    clip = _require_video_clip(project, clip_id)
    set_clip_effects(clip, set_effect_enabled(clip.effects, effect_id, enabled))
    updated = effect_by_id(clip.effects, effect_id)
    if updated is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    return updated


def update_clip_effect_parameters(
    project: "Project",
    clip_id: str,
    effect_id: str,
    params: Mapping[str, Any],
) -> ClipEffect:
    """Met à jour les paramètres d'un effet et retourne sa nouvelle version."""
    clip = _require_video_clip(project, clip_id)
    set_clip_effects(clip, update_effect_parameters(clip.effects, effect_id, params))
    updated = effect_by_id(clip.effects, effect_id)
    if updated is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    return updated


def move_clip_effect(
    project: "Project", clip_id: str, effect_id: str, delta: int
) -> ClipEffect:
    """Réordonne un effet dans la chaîne du clip et retourne sa version."""
    clip = _require_video_clip(project, clip_id)
    set_clip_effects(clip, move_effect(clip.effects, effect_id, delta))
    moved = effect_by_id(clip.effects, effect_id)
    if moved is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet introuvable : {effect_id!r}.")
    return moved


__all__ = [
    "EFFECT_PARAMETER_SPECS",
    "FX_PREFIX",
    "SINGLE_INSTANCE_EFFECTS",
    "ClipEffect",
    "EffectParameterSpec",
    "EffectType",
    "add_effect",
    "add_effect_to_clip",
    "clip_effects",
    "create_effect",
    "default_parameters",
    "effect_by_id",
    "effect_by_type",
    "fx_property_id",
    "enabled_effects",
    "is_single_instance",
    "move_clip_effect",
    "move_effect",
    "parameter_specs",
    "effects_with_unique_ids",
    "prune_effect_animation",
    "remap_fx_animation",
    "remove_effect",
    "remove_effect_from_clip",
    "reorder_effect",
    "replace_effect",
    "set_clip_effect_enabled",
    "set_clip_effects",
    "set_effect_enabled",
    "split_fx_property",
    "update_clip_effect_parameters",
    "update_effect_parameters",
    "validate_parameters",
]
