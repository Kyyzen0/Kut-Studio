"""Modèle métier des effets audio non destructifs (tâche 27).

Ce module décrit, sans aucune dépendance à ``PySide6`` ni à FFmpeg,
les effets audio qu'un clip peut porter. Il suit la même architecture
que :mod:`core.effects_model` (effets vidéo) pour rester cohérent et
facile à apprendre, mais reste strictement indépendant :

- :class:`AudioEffectType` — catalogue fermé des 10 effets natifs ;
- :class:`AudioEffectParameterSpec` — bornes + défaut par paramètre ;
- :class:`AudioEffect` — instance d'effet (id stable, type, activation,
  paramètres validés) ;
- des fonctions **pures** sur une liste d'effets : ajout, suppression,
  activation, mise à jour, réorganisation ;
- des opérations au niveau projet qui acceptent les clips des pistes
  ``video`` *et* ``audio`` (les sous-titres sont rejetés comme pour
  les effets vidéo).

Les paramètres sont validés explicitement : une valeur hors bornes, un
type inconnu ou un paramètre non numérique lève ``ValueError``. La
couche de persistance (``core.project_io``) s'appuie sur cette
validation pour ignorer les entrées invalides sans casser l'ouverture
d'un projet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from .project_model import Clip, Project


class AudioEffectType(str, Enum):
    """Catalogue fermé des effets audio non destructifs (tâche 27).

    Les dix types correspondent aux dix presets natifs livrés dans
    :mod:`core.audio_effects_library`. Chaque type expose ses propres
    paramètres (voir :data:`AUDIO_EFFECT_PARAMETER_SPECS`) et se
    traduit en un ou plusieurs filtres FFmpeg documentés dans
    :mod:`core.export_engine`.
    """

    NORMALIZE = "normalize"
    VOICE_ENHANCE = "voice_enhance"
    NOISE_REDUCE = "noise_reduce"
    COMPRESSOR = "compressor"
    LIMITER = "limiter"
    BASS_BOOST = "bass_boost"
    TREBLE_BOOST = "treble_boost"
    PHONE_EFFECT = "phone_effect"
    REVERB_LIGHT = "reverb_light"
    ECHO_LIGHT = "echo_light"


# Identifiants des filtres FFmpeg (``-af``) associés à chaque type
# d'effet. Le mapping est *séparé* du mapping des paramètres pour
# faciliter l'audit : un changement de filtre ne casse pas les
# paramètres, et inversement.
#
# Note : ``loudnorm`` est utilisé en mode *two-pass simplifié* via un
# ``-filter_complex`` séparé ; ici on enregistre uniquement le filtre
# linéaire ``loudnorm`` qui fonctionne dans une chaîne unique. C'est
# acceptable car :func:`_build_audio_effect_filters` applique les
# filtres avant le gain / pan / fade, comme demandé par la tâche.
AUDIO_EFFECT_FFMPEG_FILTER: dict[AudioEffectType, str] = {
    AudioEffectType.NORMALIZE: "loudnorm",
    AudioEffectType.VOICE_ENHANCE: "highpass",
    AudioEffectType.NOISE_REDUCE: "afftdn",
    AudioEffectType.COMPRESSOR: "acompressor",
    AudioEffectType.LIMITER: "alimiter",
    AudioEffectType.BASS_BOOST: "bass",
    AudioEffectType.TREBLE_BOOST: "treble",
    AudioEffectType.PHONE_EFFECT: "bandpass",
    AudioEffectType.REVERB_LIGHT: "aecho",
    AudioEffectType.ECHO_LIGHT: "aecho",
}


@dataclass(frozen=True)
class AudioEffectParameterSpec:
    """Description d'un paramètre audio : bornes + défaut.

    Attributes:
        name: Nom du paramètre (utilisé comme clé dans
            :attr:`AudioEffect.params`).
        minimum: Borne basse (inclusive).
        maximum: Borne haute (inclusive).
        default: Valeur par défaut.
    """

    name: str
    minimum: float
    maximum: float
    default: float


# Paramètres par défaut **explicites** de chaque type d'effet audio.
# Les choix reflètent des réglages modérés, adaptés à de la voix / voix
# off / musique douce. Un effet sans paramètre n'expose aucun réglage.
#
# Les unités correspondent au filtre FFmpeg ciblé :
# - NORMALIZE → loudnorm (I, LRA, TP en LUFS / LU)
# - VOICE_ENHANCE → highpass (Hz) + intensité
# - NOISE_REDUCE → afftdn (dB de bruit, sample_noise)
# - COMPRESSOR → acompressor (threshold dB, ratio, attack ms, release ms, makeup dB)
# - LIMITER → alimiter (limit dB)
# - BASS_BOOST → bass (gain dB, fréquence Hz)
# - TREBLE_BOOST → treble (gain dB, fréquence Hz)
# - PHONE_EFFECT → bandpass (Hz, Hz, mix)
# - REVERB_LIGHT → aecho (in_gain, out_gain, delays, decays)
# - ECHO_LIGHT → aecho (in_gain, out_gain, delays, decays)
AUDIO_EFFECT_PARAMETER_SPECS: dict[AudioEffectType, tuple[AudioEffectParameterSpec, ...]] = {
    AudioEffectType.NORMALIZE: (
        AudioEffectParameterSpec("integrated_loudness", -30.0, -10.0, -16.0),
        AudioEffectParameterSpec("loudness_range", 1.0, 20.0, 7.0),
        AudioEffectParameterSpec("true_peak", -3.0, 0.0, -1.0),
    ),
    AudioEffectType.VOICE_ENHANCE: (
        AudioEffectParameterSpec("frequency", 60.0, 300.0, 100.0),
        AudioEffectParameterSpec("intensity", 0.0, 1.0, 0.4),
    ),
    AudioEffectType.NOISE_REDUCE: (
        AudioEffectParameterSpec("noise_floor_db", -60.0, -10.0, -30.0),
        AudioEffectParameterSpec("strength", 0.0, 1.0, 0.8),
    ),
    AudioEffectType.COMPRESSOR: (
        AudioEffectParameterSpec("threshold_db", -40.0, 0.0, -18.0),
        AudioEffectParameterSpec("ratio", 1.0, 20.0, 3.0),
        AudioEffectParameterSpec("attack_ms", 1.0, 200.0, 20.0),
        AudioEffectParameterSpec("release_ms", 5.0, 1000.0, 200.0),
        AudioEffectParameterSpec("makeup_db", 0.0, 24.0, 4.0),
    ),
    AudioEffectType.LIMITER: (
        AudioEffectParameterSpec("limit_db", -6.0, 0.0, -1.0),
    ),
    AudioEffectType.BASS_BOOST: (
        AudioEffectParameterSpec("gain_db", 0.0, 24.0, 6.0),
        AudioEffectParameterSpec("frequency_hz", 20.0, 250.0, 100.0),
    ),
    AudioEffectType.TREBLE_BOOST: (
        AudioEffectParameterSpec("gain_db", 0.0, 24.0, 4.0),
        AudioEffectParameterSpec("frequency_hz", 2000.0, 12000.0, 5000.0),
    ),
    AudioEffectType.PHONE_EFFECT: (
        AudioEffectParameterSpec("center_hz", 800.0, 2000.0, 1500.0),
        AudioEffectParameterSpec("bandwidth_hz", 200.0, 1500.0, 800.0),
        AudioEffectParameterSpec("mix", 0.0, 1.0, 1.0),
    ),
    AudioEffectType.REVERB_LIGHT: (
        AudioEffectParameterSpec("in_gain", 0.0, 1.0, 0.6),
        AudioEffectParameterSpec("out_gain", 0.0, 1.0, 0.4),
        AudioEffectParameterSpec("delays_ms", 20.0, 250.0, 60.0),
        AudioEffectParameterSpec("decays", 0.0, 0.9, 0.3),
    ),
    AudioEffectType.ECHO_LIGHT: (
        AudioEffectParameterSpec("in_gain", 0.0, 1.0, 0.8),
        AudioEffectParameterSpec("out_gain", 0.0, 1.0, 0.6),
        AudioEffectParameterSpec("delays_ms", 100.0, 1000.0, 300.0),
        AudioEffectParameterSpec("decays", 0.0, 0.9, 0.4),
    ),
}


# Effets qui ne peuvent exister qu'une fois par clip audio. Aucun
# effet audio natif n'est marqué ``single instance`` pour l'instant :
# empiler plusieurs compresseurs ou plusieurs réverbérations est
# techniquement valide et le créatif peut l'utiliser. Cette
# constante reste exposée pour la cohérence avec
# :data:`core.effects_model.SINGLE_INSTANCE_EFFECTS`.
SINGLE_INSTANCE_AUDIO_EFFECTS: frozenset[AudioEffectType] = frozenset()


def is_single_instance(effect_type: AudioEffectType | str) -> bool:
    """Indique si ``effect_type`` ne doit exister qu'une fois par clip."""
    return AudioEffectType(effect_type) in SINGLE_INSTANCE_AUDIO_EFFECTS


def parameter_specs(effect_type: AudioEffectType | str) -> tuple[AudioEffectParameterSpec, ...]:
    """Retourne les paramètres déclarés pour ``effect_type``."""
    return AUDIO_EFFECT_PARAMETER_SPECS[AudioEffectType(effect_type)]


def default_parameters(effect_type: AudioEffectType | str) -> dict[str, float]:
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
    effect_type: AudioEffectType | str,
    params: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Valide et normalise les paramètres d'un effet audio.

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
            f"'{AudioEffectType(effect_type).value}' : {sorted(unknown)}."
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
class AudioEffect:
    """Un effet audio appliqué à un clip.

    Attributes:
        id: Identifiant stable de l'effet dans le clip.
        type: Type d'effet (:class:`AudioEffectType`).
        enabled: ``False`` neutralise l'effet sans le supprimer.
        params: Paramètres validés, dans l'ordre de déclaration du type.
    """

    id: str
    type: AudioEffectType
    enabled: bool = True
    params: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Un effet audio doit porter un identifiant non vide.")
        try:
            effect_type = AudioEffectType(self.type)
        except ValueError as error:
            raise ValueError(f"Type d'effet audio inconnu : {self.type!r}.") from error
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
                f"L'effet audio '{self.id}' ne possède pas de paramètre {name!r}."
            ) from error

    # --- Dérivations immuables ----------------------------------------

    def with_enabled(self, enabled: bool) -> "AudioEffect":
        """Retourne une copie avec l'état d'activation demandé."""
        return replace(self, enabled=bool(enabled))

    def with_parameters(self, params: Mapping[str, Any]) -> "AudioEffect":
        """Retourne une copie dont les paramètres sont fusionnés puis validés."""
        merged = dict(self.params)
        merged.update(dict(params))
        return replace(self, params=merged)

    def with_parameter(self, name: str, value: Any) -> "AudioEffect":
        """Retourne une copie avec un unique paramètre modifié."""
        return self.with_parameters({name: value})


def create_audio_effect(
    effect_type: AudioEffectType | str,
    *,
    effect_id: str | None = None,
    enabled: bool = True,
    params: Mapping[str, Any] | None = None,
) -> AudioEffect:
    """Fabrique un :class:`AudioEffect` prêt à l'emploi.

    Si ``effect_id`` est omis, un identifiant unique est généré avec
    le préfixe ``"afx-"`` (audio effect) pour éviter toute collision
    avec les ``"fx-"`` utilisés par les effets vidéo.
    """
    return AudioEffect(
        id=effect_id or f"afx-{uuid.uuid4().hex[:12]}",
        type=AudioEffectType(effect_type),
        enabled=enabled,
        params=validate_parameters(effect_type, params),
    )


# ---------------------------------------------------------------------------
# Fonctions pures sur une liste d'effets audio
# ---------------------------------------------------------------------------


def effect_by_id(
    effects: Sequence[AudioEffect], effect_id: str
) -> AudioEffect | None:
    """Retourne l'effet d'identifiant ``effect_id`` ou ``None``."""
    for effect in effects:
        if effect.id == effect_id:
            return effect
    return None


def effect_by_type(
    effects: Sequence[AudioEffect], effect_type: AudioEffectType | str
) -> AudioEffect | None:
    """Retourne le premier effet du type demandé ou ``None``."""
    wanted = AudioEffectType(effect_type)
    for effect in effects:
        if effect.type == wanted:
            return effect
    return None


def enabled_effects(effects: Sequence[AudioEffect]) -> list[AudioEffect]:
    """Retourne les effets activés, dans leur ordre d'origine."""
    return [effect for effect in effects if effect.enabled]


def _index_of(effects: Sequence[AudioEffect], effect_id: str) -> int:
    for index, effect in enumerate(effects):
        if effect.id == effect_id:
            return index
    raise KeyError(f"Effet audio introuvable : {effect_id!r}.")


def add_effect(
    effects: Sequence[AudioEffect], effect: AudioEffect
) -> list[AudioEffect]:
    """Retourne une nouvelle liste avec ``effect`` ajouté à la fin."""
    if any(existing.id == effect.id for existing in effects):
        raise ValueError(f"L'effet audio '{effect.id}' existe déjà sur ce clip.")
    if is_single_instance(effect.type) and effect_by_type(effects, effect.type):
        raise ValueError(
            f"L'effet audio '{effect.type.value}' ne peut exister qu'une "
            "fois par clip."
        )
    return [*effects, effect]


def remove_effect(
    effects: Sequence[AudioEffect], effect_id: str
) -> list[AudioEffect]:
    """Retourne une nouvelle liste sans l'effet ``effect_id``."""
    index = _index_of(effects, effect_id)
    return [
        effect for position, effect in enumerate(effects) if position != index
    ]


def replace_effect(
    effects: Sequence[AudioEffect], effect_id: str, new_effect: AudioEffect
) -> list[AudioEffect]:
    """Retourne une nouvelle liste où ``effect_id`` est remplacé."""
    index = _index_of(effects, effect_id)
    result = list(effects)
    result[index] = new_effect
    return result


def set_effect_enabled(
    effects: Sequence[AudioEffect], effect_id: str, enabled: bool
) -> list[AudioEffect]:
    """Retourne une nouvelle liste avec l'activation demandée."""
    effect = effect_by_id(effects, effect_id)
    if effect is None:
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    return replace_effect(effects, effect_id, effect.with_enabled(enabled))


def update_effect_parameters(
    effects: Sequence[AudioEffect],
    effect_id: str,
    params: Mapping[str, Any],
) -> list[AudioEffect]:
    """Retourne une nouvelle liste avec les paramètres mis à jour."""
    effect = effect_by_id(effects, effect_id)
    if effect is None:
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    return replace_effect(effects, effect_id, effect.with_parameters(params))


def reorder_effect(
    effects: Sequence[AudioEffect], effect_id: str, new_index: int
) -> list[AudioEffect]:
    """Retourne une nouvelle liste avec ``effect_id`` déplacé à ``new_index``."""
    index = _index_of(effects, effect_id)
    result = list(effects)
    effect = result.pop(index)
    bounded = max(0, min(int(new_index), len(result)))
    result.insert(bounded, effect)
    return result


def move_effect(
    effects: Sequence[AudioEffect], effect_id: str, delta: int
) -> list[AudioEffect]:
    """Déplace un effet de ``delta`` crans (``-1`` = monter, ``+1`` = descendre)."""
    index = _index_of(effects, effect_id)
    return reorder_effect(effects, effect_id, index + int(delta))


# ---------------------------------------------------------------------------
# Opérations au niveau projet (clips vidéo ET audio, sous-titres exclus)
# ---------------------------------------------------------------------------


def _find_track_and_clip(project: "Project", clip_id: str):
    """Retourne ``(track, clip)`` ou lève ``KeyError``."""
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return track, clip
    raise KeyError(f"Clip introuvable : {clip_id!r}.")


def _require_audio_capable_clip(project: "Project", clip_id: str) -> "Clip":
    """Retourne le clip après avoir vérifié qu'il porte de l'audio.

    Les effets audio s'appliquent aux clips des pistes ``video`` (qui
    peuvent porter une piste audio via ``MediaAsset.has_audio``) et
    ``audio`` (pistes son pures). Les pistes ``subtitle`` sont
    rejetées : elles ne transportent pas de flux audio.
    """
    track, clip = _find_track_and_clip(project, clip_id)
    if track.type == "subtitle":
        raise ValueError(
            f"Le clip '{clip_id}' est sur une piste 'subtitle' ; "
            "les effets audio ne s'y appliquent pas."
        )
    if track.locked:
        raise ValueError(f"La piste '{track.id}' est verrouillée.")
    return clip


def clip_audio_effects(project: "Project", clip_id: str) -> list[AudioEffect]:
    """Retourne une copie des effets audio d'un clip."""
    return list(_require_audio_capable_clip(project, clip_id).audio_effects)


def add_audio_effect_to_clip(
    project: "Project",
    clip_id: str,
    effect_type: AudioEffectType | str,
    *,
    effect_id: str | None = None,
    params: Mapping[str, Any] | None = None,
    enabled: bool = True,
) -> AudioEffect:
    """Ajoute un effet audio à un clip et retourne l'effet créé."""
    clip = _require_audio_capable_clip(project, clip_id)
    effect = create_audio_effect(
        effect_type, effect_id=effect_id, enabled=enabled, params=params
    )
    clip.audio_effects = add_effect(clip.audio_effects, effect)
    return effect


def remove_audio_effect_from_clip(
    project: "Project", clip_id: str, effect_id: str
) -> AudioEffect:
    """Supprime un effet audio d'un clip et retourne l'effet retiré."""
    clip = _require_audio_capable_clip(project, clip_id)
    removed = effect_by_id(clip.audio_effects, effect_id)
    if removed is None:
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    clip.audio_effects = remove_effect(clip.audio_effects, effect_id)
    return removed


def set_clip_audio_effect_enabled(
    project: "Project", clip_id: str, effect_id: str, enabled: bool
) -> AudioEffect:
    """Active ou désactive un effet audio et retourne sa nouvelle version."""
    clip = _require_audio_capable_clip(project, clip_id)
    clip.audio_effects = set_effect_enabled(clip.audio_effects, effect_id, enabled)
    updated = effect_by_id(clip.audio_effects, effect_id)
    if updated is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    return updated


def update_clip_audio_effect_parameters(
    project: "Project",
    clip_id: str,
    effect_id: str,
    params: Mapping[str, Any],
) -> AudioEffect:
    """Met à jour les paramètres d'un effet audio et retourne sa version."""
    clip = _require_audio_capable_clip(project, clip_id)
    clip.audio_effects = update_effect_parameters(clip.audio_effects, effect_id, params)
    updated = effect_by_id(clip.audio_effects, effect_id)
    if updated is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    return updated


def move_clip_audio_effect(
    project: "Project", clip_id: str, effect_id: str, delta: int
) -> AudioEffect:
    """Réordonne un effet audio dans la chaîne du clip et retourne sa version."""
    clip = _require_audio_capable_clip(project, clip_id)
    clip.audio_effects = move_effect(clip.audio_effects, effect_id, delta)
    moved = effect_by_id(clip.audio_effects, effect_id)
    if moved is None:  # pragma: no cover - invariant juste au-dessus
        raise KeyError(f"Effet audio introuvable : {effect_id!r}.")
    return moved


__all__ = [
    "AUDIO_EFFECT_FFMPEG_FILTER",
    "AUDIO_EFFECT_PARAMETER_SPECS",
    "AudioEffect",
    "AudioEffectParameterSpec",
    "AudioEffectType",
    "SINGLE_INSTANCE_AUDIO_EFFECTS",
    "add_audio_effect_to_clip",
    "add_effect",
    "clip_audio_effects",
    "create_audio_effect",
    "default_parameters",
    "effect_by_id",
    "effect_by_type",
    "enabled_effects",
    "is_single_instance",
    "move_clip_audio_effect",
    "move_effect",
    "parameter_specs",
    "remove_audio_effect_from_clip",
    "remove_effect",
    "reorder_effect",
    "replace_effect",
    "set_clip_audio_effect_enabled",
    "set_effect_enabled",
    "update_clip_audio_effect_parameters",
    "update_effect_parameters",
    "validate_parameters",
]