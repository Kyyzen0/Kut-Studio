"""Bibliothèque d'effets audio natifs et utilisateur (tâche 27).

Ce module complète :mod:`core.effects_library` (effets vidéo) en
proposant l'équivalent audio :

- :class:`AudioEffectPresetCategory` — taxonomies stables (``DYNAMICS``,
  ``CLEANUP``, ``EQ``, ``SPATIAL``) ;
- :class:`AudioEffectPreset` — un préréglage (preset intégré ou
  utilisateur) ;
- :func:`builtin_audio_effect_presets` — la bibliothèque livrée avec
  l'application (10 préréglages correspondant aux 10 types natifs) ;
- :func:`filter_audio_effect_presets` — recherche / filtres ;
- :class:`AudioEffectPresetStore` — gestion des favoris et des presets
  utilisateur, avec persistance JSON identique au module des
  transitions pour la cohérence.

Le format JSON de persistance est ``audio_effect_presets.json``,
distinct de celui des transitions pour ne pas les coupler.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .audio_effects_model import (
    AudioEffect,
    AudioEffectType,
    default_parameters,
)


# Longueur max d'un nom de préréglage (court pour rester lisible).
MAX_NAME_LENGTH: int = 64


class AudioEffectPresetCategory(str, Enum):
    """Catégories de la bibliothèque d'effets audio."""

    DYNAMICS = "dynamics"  # Normalisation, compresseur, limiteur
    CLEANUP = "cleanup"  # Réduction de bruit, voix, téléphone
    EQ = "eq"  # Renfort basses / aigus
    SPATIAL = "spatial"  # Réverb, écho


CATEGORY_LABELS: dict[AudioEffectPresetCategory, str] = {
    AudioEffectPresetCategory.DYNAMICS: "Dynamique",
    AudioEffectPresetCategory.CLEANUP: "Nettoyage",
    AudioEffectPresetCategory.EQ: "Égaliseur",
    AudioEffectPresetCategory.SPATIAL: "Spatialisation",
}


CATEGORY_DESCRIPTIONS: dict[AudioEffectPresetCategory, str] = {
    AudioEffectPresetCategory.DYNAMICS: (
        "Normalisation, compression et limitation de la dynamique."
    ),
    AudioEffectPresetCategory.CLEANUP: (
        "Suppression de bruit, amélioration de voix et effets spéciaux."
    ),
    AudioEffectPresetCategory.EQ: (
        "Renforcement ciblé des basses et des aigus."
    ),
    AudioEffectPresetCategory.SPATIAL: (
        "Réverbérations et échos pour donner de la profondeur."
    ),
}


# Mapping type d'effet → catégorie. Source de vérité unique pour la
# classification des presets natifs et des futurs presets utilisateur.
EFFECT_TYPE_CATEGORY: dict[AudioEffectType, AudioEffectPresetCategory] = {
    AudioEffectType.NORMALIZE: AudioEffectPresetCategory.DYNAMICS,
    AudioEffectType.COMPRESSOR: AudioEffectPresetCategory.DYNAMICS,
    AudioEffectType.LIMITER: AudioEffectPresetCategory.DYNAMICS,
    AudioEffectType.NOISE_REDUCE: AudioEffectPresetCategory.CLEANUP,
    AudioEffectType.VOICE_ENHANCE: AudioEffectPresetCategory.CLEANUP,
    AudioEffectType.PHONE_EFFECT: AudioEffectPresetCategory.CLEANUP,
    AudioEffectType.BASS_BOOST: AudioEffectPresetCategory.EQ,
    AudioEffectType.TREBLE_BOOST: AudioEffectPresetCategory.EQ,
    AudioEffectType.REVERB_LIGHT: AudioEffectPresetCategory.SPATIAL,
    AudioEffectType.ECHO_LIGHT: AudioEffectPresetCategory.SPATIAL,
}


@dataclass(frozen=True)
class AudioEffectPreset:
    """Un préréglage d'effet audio (intégré ou utilisateur).

    Attributes:
        id: Identifiant unique et stable.
        name: Libellé humain (court).
        description: Description longue (FR par défaut).
        category: Catégorie de la bibliothèque.
        effect_type: Type d'effet ciblé (:class:`AudioEffectType`).
        params_override: Surcharge des paramètres par défaut. Les
            clés absentes retombent sur :func:`default_parameters`.
        builtin: ``True`` pour les préréglages livrés avec l'app.
    """

    id: str
    name: str
    description: str
    category: AudioEffectPresetCategory
    effect_type: AudioEffectType
    params_override: dict[str, float]
    builtin: bool

    def resolved_params(self) -> dict[str, float]:
        """Retourne les paramètres effectifs : override + défauts."""
        base = default_parameters(self.effect_type)
        base.update(self.params_override)
        return base

    def to_audio_effect(self) -> AudioEffect:
        """Fabrique un :class:`AudioEffect` calibré sur ce préréglage."""
        return AudioEffect(
            id=f"afx-{uuid.uuid4().hex[:12]}",
            type=self.effect_type,
            enabled=True,
            params=self.resolved_params(),
        )


def _require_text(name: str, value: str) -> str:
    """Rejette un libellé vide ou trop long et le nettoie."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"Le {name} ne peut pas être vide.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(
            f"Le {name} est trop long ({len(cleaned)} > {MAX_NAME_LENGTH})."
        )
    return cleaned


def make_audio_effect_preset(
    *,
    preset_id: str,
    name: str,
    description: str,
    category: AudioEffectPresetCategory | str,
    effect_type: AudioEffectType | str,
    params_override: dict[str, float] | None = None,
    builtin: bool = False,
) -> AudioEffectPreset:
    """Fabrique un :class:`AudioEffectPreset` en validant ses arguments."""
    clean_name = _require_text("nom", name)
    clean_description = (description or "").strip()
    clean_effect_type = AudioEffectType(effect_type)
    clean_category = AudioEffectPresetCategory(category)
    override: dict[str, float] = {}
    if params_override:
        for key, value in params_override.items():
            try:
                override[str(key)] = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Paramètre '{key}' non numérique pour le préréglage : "
                    f"{value!r}."
                ) from exc
    return AudioEffectPreset(
        id=preset_id,
        name=clean_name,
        description=clean_description,
        category=clean_category,
        effect_type=clean_effect_type,
        params_override=override,
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Préréglages natifs (tâche 27) — 10 préréglages, un par type.
# ---------------------------------------------------------------------------


def _normalize_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="normalize",
        name="Normalisation",
        description=(
            "Normalise la loudness intégrée à -16 LUFS avec une plage "
            "dynamique modérée. Idéal pour la voix off et les podcasts."
        ),
        category=AudioEffectPresetCategory.DYNAMICS,
        effect_type=AudioEffectType.NORMALIZE,
        builtin=True,
    )


def _voice_enhance_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="voice_enhance",
        name="Amélioration de voix",
        description=(
            "Coupe les basses fréquences sous 100 Hz pour réduire les "
            "souffles de micro et les bruits de fond, sans affecter "
            "l'intelligibilité de la voix."
        ),
        category=AudioEffectPresetCategory.CLEANUP,
        effect_type=AudioEffectType.VOICE_ENHANCE,
        builtin=True,
    )


def _noise_reduce_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="noise_reduce",
        name="Réduction de bruit",
        description=(
            "Applique une soustraction spectrale FFT pour atténuer les "
            "bruits stationnaires (souffle de bande, ventilation)."
        ),
        category=AudioEffectPresetCategory.CLEANUP,
        effect_type=AudioEffectType.NOISE_REDUCE,
        builtin=True,
    )


def _compressor_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="compressor",
        name="Compresseur",
        description=(
            "Compresseur doux (ratio 3:1, seuil -18 dB) avec maquillage "
            "automatique : uniformise les niveaux sans pomper le signal."
        ),
        category=AudioEffectPresetCategory.DYNAMICS,
        effect_type=AudioEffectType.COMPRESSOR,
        builtin=True,
    )


def _limiter_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="limiter",
        name="Limiteur anti-saturation",
        description=(
            "Limiteur à -1 dB : empêche le signal de dépasser le seuil "
            "et protège la chaîne d'enregistrement."
        ),
        category=AudioEffectPresetCategory.DYNAMICS,
        effect_type=AudioEffectType.LIMITER,
        builtin=True,
    )


def _bass_boost_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="bass_boost",
        name="Renforcement des basses",
        description=(
            "Ajoute +6 dB sous 100 Hz pour renforcer la présence des "
            "basses sans saturer le bas du spectre."
        ),
        category=AudioEffectPresetCategory.EQ,
        effect_type=AudioEffectType.BASS_BOOST,
        builtin=True,
    )


def _treble_boost_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="treble_boost",
        name="Clarté des aigus",
        description=(
            "Ajoute +4 dB autour de 5 kHz pour redonner de la clarté aux "
            "enregistrements ternes."
        ),
        category=AudioEffectPresetCategory.EQ,
        effect_type=AudioEffectType.TREBLE_BOOST,
        builtin=True,
    )


def _phone_effect_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="phone_effect",
        name="Effet téléphone",
        description=(
            "Filtre passe-bande étroit centré sur 1.5 kHz, pour simuler "
            "un appel téléphonique ou un interphone."
        ),
        category=AudioEffectPresetCategory.CLEANUP,
        effect_type=AudioEffectType.PHONE_EFFECT,
        builtin=True,
    )


def _reverb_light_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="reverb_light",
        name="Réverbération légère",
        description=(
            "Petite réverbération par empilement de trois échos courts "
            "(60 / 40 / 25 ms), pour ajouter de la profondeur sans "
            "étouffer le signal."
        ),
        category=AudioEffectPresetCategory.SPATIAL,
        effect_type=AudioEffectType.REVERB_LIGHT,
        builtin=True,
    )


def _echo_light_preset() -> AudioEffectPreset:
    return make_audio_effect_preset(
        preset_id="echo_light",
        name="Écho léger",
        description=(
            "Écho court et sec (300 ms), utile pour donner un effet "
            "traduction simultanée ou une ambiance caverneuse."
        ),
        category=AudioEffectPresetCategory.SPATIAL,
        effect_type=AudioEffectType.ECHO_LIGHT,
        builtin=True,
    )


# Identifiants des 10 préréglages natifs : utile pour les tests qui
# vérifient la complétude du catalogue livré.
BUILTIN_AUDIO_EFFECT_PRESETS_COUNT: int = 10


def builtin_audio_effect_presets() -> tuple[AudioEffectPreset, ...]:
    """Retourne la bibliothèque native (10 préréglages, tâche 27)."""
    return (
        _normalize_preset(),
        _voice_enhance_preset(),
        _noise_reduce_preset(),
        _compressor_preset(),
        _limiter_preset(),
        _bass_boost_preset(),
        _treble_boost_preset(),
        _phone_effect_preset(),
        _reverb_light_preset(),
        _echo_light_preset(),
    )


def builtin_audio_effect_preset_ids() -> frozenset[str]:
    """Ensemble des identifiants des préréglages natifs."""
    return frozenset(p.id for p in builtin_audio_effect_presets())


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def filter_audio_effect_presets(
    presets: Iterable[AudioEffectPreset],
    *,
    search: str = "",
    category: AudioEffectPresetCategory | str | None = None,
    favorites: Sequence[str] | None = None,
    favorites_only: bool = False,
) -> list[AudioEffectPreset]:
    """Filtre les préréglages audio selon recherche, catégorie et favoris."""
    needle = (search or "").strip().lower()
    wanted_category = (
        AudioEffectPresetCategory(category) if category is not None else None
    )
    favorite_set = set(favorites or ())
    result: list[AudioEffectPreset] = []
    for preset in presets:
        if wanted_category is not None and preset.category != wanted_category:
            continue
        if favorites_only and preset.id not in favorite_set:
            continue
        if (
            needle
            and needle not in preset.id.lower()
            and needle not in preset.name.lower()
        ):
            continue
        result.append(preset)
    return result


def make_user_audio_effect_preset(
    name: str,
    description: str,
    effect_type: AudioEffectType | str,
    params_override: dict[str, float],
) -> AudioEffectPreset:
    """Crée un préréglage utilisateur (non intégré)."""
    clean_effect_type = AudioEffectType(effect_type)
    return make_audio_effect_preset(
        preset_id=f"user-audio-{uuid.uuid4().hex[:12]}",
        name=name,
        description=description,
        category=EFFECT_TYPE_CATEGORY[clean_effect_type],
        effect_type=clean_effect_type,
        params_override=params_override,
        builtin=False,
    )


# ---------------------------------------------------------------------------
# Persistance : favoris + préréglages utilisateur
# ---------------------------------------------------------------------------


AUDIO_EFFECT_PRESETS_FILE: str = "audio_effect_presets.json"
"""Nom du fichier de favoris / presets utilisateur audio."""


def _user_presets_dir(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire de stockage des préférences utilisateur."""
    if settings_dir is not None:
        return Path(settings_dir)
    env = os.environ.get("KUT_STUDIO_CONFIG_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Kut-Studio"
        return home / "Kut-Studio"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Kut-Studio"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "kut-studio"
    return home / ".config" / "kut-studio"


def audio_effect_presets_path(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Chemin du fichier de favoris + presets utilisateur audio."""
    return _user_presets_dir(settings_dir) / AUDIO_EFFECT_PRESETS_FILE


def _coerce_user_preset(payload: object) -> AudioEffectPreset | None:
    """Reconstruit un préréglage utilisateur depuis une entrée JSON."""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    try:
        effect_type = AudioEffectType(payload.get("effect_type"))
    except (TypeError, ValueError):
        return None
    description = payload.get("description", "")
    if not isinstance(description, str):
        description = ""
    raw_params = payload.get("params_override", {}) or {}
    if not isinstance(raw_params, dict):
        return None
    cleaned_params: dict[str, float] = {}
    for key, value in raw_params.items():
        try:
            cleaned_params[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    preset_id = payload.get("id")
    if not isinstance(preset_id, str) or not preset_id:
        return None
    try:
        return make_audio_effect_preset(
            preset_id=preset_id,
            name=name,
            description=description,
            category=EFFECT_TYPE_CATEGORY[effect_type],
            effect_type=effect_type,
            params_override=cleaned_params,
            builtin=False,
        )
    except ValueError:
        return None


def _coerce_favorites(payload: object) -> list[str]:
    """Reconstruit la liste des favoris depuis une entrée JSON."""
    if not isinstance(payload, list):
        return []
    return [
        entry for entry in payload
        if isinstance(entry, str) and entry
    ]


def load_audio_effect_preset_data(
    settings_dir: str | os.PathLike[str] | None = None,
) -> tuple[list[AudioEffectPreset], list[str]]:
    """Charge favoris et presets utilisateur depuis le disque."""
    path = audio_effect_presets_path(settings_dir)
    if not path.exists():
        return [], []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return [], []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    raw_user_presets = data.get("user_presets", [])
    raw_favorites = data.get("favorites", [])
    user_presets = [
        preset
        for preset in (
            _coerce_user_preset(entry) for entry in raw_user_presets
        )
        if preset is not None
    ]
    favorites = _coerce_favorites(raw_favorites)
    return user_presets, favorites


def save_audio_effect_preset_data(
    user_presets: Sequence[AudioEffectPreset],
    favorites: Sequence[str],
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persiste favoris et presets utilisateur (écriture atomique)."""
    base = _user_presets_dir(settings_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / AUDIO_EFFECT_PRESETS_FILE
    payload = {
        "version": 1,
        "favorites": [
            fav for fav in favorites
            if isinstance(fav, str) and fav
        ],
        "user_presets": [
            {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "effect_type": preset.effect_type.value,
                "params_override": {
                    str(k): float(v)
                    for k, v in preset.params_override.items()
                },
            }
            for preset in user_presets
            if not preset.builtin
        ],
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(base),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(
                payload, tmp_file, indent=2, ensure_ascii=False
            )
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return target


class AudioEffectPresetStore:
    """Gestionnaire en mémoire des favoris et presets utilisateur audio.

    Suivant la même API que :class:`core.transition_presets.TransitionPresetStore`
    pour l'orchestration UI : les préréglages intégrés sont traversés
    en lecture mais jamais modifiés. Toute mutation est immédiatement
    persistée sur disque.
    """

    def __init__(
        self,
        user_presets: Sequence[AudioEffectPreset] | None = None,
        favorites: Sequence[str] | None = None,
        settings_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._settings_dir = settings_dir
        if user_presets is None or favorites is None:
            loaded_user, loaded_favorites = load_audio_effect_preset_data(
                settings_dir
            )
            if user_presets is None:
                user_presets = loaded_user
            if favorites is None:
                favorites = loaded_favorites
        self._user_presets: dict[str, AudioEffectPreset] = {
            preset.id: preset
            for preset in user_presets
            if not preset.builtin
        }
        self._favorites: set[str] = {fav for fav in favorites if fav}
        self._listeners: list = []

    # ----- API publique --------------------------------------------------

    def all_user_presets(self) -> list[AudioEffectPreset]:
        """Retourne une copie des presets utilisateur."""
        return list(self._user_presets.values())

    def favorites(self) -> list[str]:
        """Retourne une copie des identifiants favoris."""
        return list(self._favorites)

    def is_favorite(self, preset_id: str) -> bool:
        """``True`` si ``preset_id`` est marqué comme favori."""
        return preset_id in self._favorites

    def get_preset(self, preset_id: str) -> AudioEffectPreset | None:
        """Recherche le preset (intégré + utilisateur)."""
        for preset in builtin_audio_effect_presets():
            if preset.id == preset_id:
                return preset
        return self._user_presets.get(preset_id)

    def all_presets(self) -> list[AudioEffectPreset]:
        """Retourne la bibliothèque complète (intégrés + utilisateur)."""
        library = list(builtin_audio_effect_presets())
        library.extend(self._user_presets.values())
        return library

    def add_user_preset(self, preset: AudioEffectPreset) -> AudioEffectPreset:
        """Ajoute un preset utilisateur et persiste le store."""
        if preset.builtin:
            raise ValueError("Les préréglages intégrés ne peuvent pas être ajoutés.")
        if preset.id in self._user_presets:
            raise ValueError(f"Un preset '{preset.id}' existe déjà.")
        self._user_presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def remove_user_preset(self, preset_id: str) -> AudioEffectPreset:
        """Supprime un preset utilisateur et retourne le preset retiré."""
        if preset_id not in self._user_presets:
            raise KeyError(f"Preset '{preset_id}' introuvable.")
        removed = self._user_presets.pop(preset_id)
        self._favorites.discard(preset_id)
        self._persist()
        self._notify_changed()
        return removed

    def set_favorite(self, preset_id: str, favorite: bool) -> bool:
        """Ajoute ou retire ``preset_id`` des favoris."""
        if favorite:
            if not self._preset_exists(preset_id):
                raise KeyError(f"Preset '{preset_id}' introuvable.")
            if preset_id in self._favorites:
                return False
            self._favorites.add(preset_id)
        else:
            if preset_id not in self._favorites:
                return False
            self._favorites.remove(preset_id)
        self._persist()
        self._notify_changed()
        return True

    def toggle_favorite(self, preset_id: str) -> bool:
        """Bascule l'état favori de ``preset_id`` et retourne le nouvel état."""
        new_state = preset_id not in self._favorites
        self.set_favorite(preset_id, new_state)
        return new_state

    def subscribe(self, callback) -> None:
        """Inscrit ``callback`` aux changements."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    # ----- Persistance ---------------------------------------------------

    def _persist(self) -> None:
        save_audio_effect_preset_data(
            list(self._user_presets.values()),
            list(self._favorites),
            self._settings_dir,
        )

    def _notify_changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - tolérance listener
                pass

    def _preset_exists(self, preset_id: str) -> bool:
        if preset_id in builtin_audio_effect_preset_ids():
            return True
        return preset_id in self._user_presets


__all__ = [
    "AUDIO_EFFECT_PRESETS_FILE",
    "AudioEffectPreset",
    "AudioEffectPresetCategory",
    "AudioEffectPresetStore",
    "BUILTIN_AUDIO_EFFECT_PRESETS_COUNT",
    "CATEGORY_DESCRIPTIONS",
    "CATEGORY_LABELS",
    "EFFECT_TYPE_CATEGORY",
    "MAX_NAME_LENGTH",
    "audio_effect_presets_path",
    "builtin_audio_effect_preset_ids",
    "builtin_audio_effect_presets",
    "filter_audio_effect_presets",
    "load_audio_effect_preset_data",
    "make_audio_effect_preset",
    "make_user_audio_effect_preset",
    "save_audio_effect_preset_data",
]