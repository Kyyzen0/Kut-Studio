"""Bibliothèque d'effets et presets pour Kut-Studio (tâche 22).

Ce module complète :mod:`core.effects_model` côté bibliothèque : il ne
manipule plus un seul :class:`ClipEffect` mais une *chaîne* d'effets
prêts à appliquer à un clip. Les trois pièces du module sont :

- :class:`EffectCategory` — taxonomies stables utilisées par la
  bibliothèque (``color``, ``creative``, ``stylized``, ``look``) ;
- :class:`EffectPreset` — un preset nommé portant une liste d'effets
  (un ou plusieurs :class:`ClipEffect`) et une catégorie ;
- :func:`builtin_presets` — la bibliothèque livrée avec l'app (cinéma,
  noir et blanc, vintage, netteté, flou…) ;
- :class:`UserPresetStore` — gestion des presets *utilisateur* (CRUD
  + persistance JSON dans le répertoire de configuration).

Règles de conception :

- module pur (aucune dépendance PySide6) : les tests peuvent tourner
  en CLI ;
- un preset décrit un *état cible* du clip, pas un identifiant de
  catalogue : deux presets « Cinéma » de fournisseurs différents
  peuvent cohabiter car leurs ``id`` sont distincts ;
- les presets utilisateur sont validés à l'écriture (mêmes garde-fous
  que :func:`core.effects_model.validate_parameters`) et jetés en
  cas de JSON corrompu, comme ``user_settings`` ;
- les fonctions publiques retournent des **nouvelles** listes /
  structures : pas de mutation cachée.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from .effects_model import (
    ClipEffect,
    EffectType,
    create_effect,
    default_parameters,
    validate_parameters,
)
from .platform_paths import user_config_dir

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from .project_model import Clip, Project


# ---------------------------------------------------------------------------
# Catégories
# ---------------------------------------------------------------------------


class EffectCategory(str, Enum):
    """Catégories visibles dans la bibliothèque d'effets."""

    COLOR = "color"
    CREATIVE = "creative"
    STYLIZED = "stylized"
    LOOK = "look"


# Libellé court + description longue par catégorie. Les libellés
# localisés vivent dans ``ui.i18n`` ; ces constantes servent de repli
# quand la traduction est absente (tests, scripts CLI).
CATEGORY_LABELS: dict[EffectCategory, str] = {
    EffectCategory.COLOR: "Couleur",
    EffectCategory.CREATIVE: "Créatif",
    EffectCategory.STYLIZED: "Stylisé",
    EffectCategory.LOOK: "Look",
}


CATEGORY_DESCRIPTIONS: dict[EffectCategory, str] = {
    EffectCategory.COLOR: "Corrections chromatiques et équilibre colorimétrique.",
    EffectCategory.CREATIVE: "Flous, netteté et ajustements créatifs.",
    EffectCategory.STYLIZED: "Looks artistiques, virages, noir et blanc.",
    EffectCategory.LOOK: "Looks cinématographiques finis prêts à l'emploi.",
}


def effect_category(value: str | EffectCategory) -> EffectCategory:
    """Normalise ``value`` en :class:`EffectCategory` ou lève ``ValueError``."""
    return EffectCategory(value)


# ---------------------------------------------------------------------------
# Preset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectPreset:
    """Preset d'effets : un nom, une catégorie, et une chaîne d'effets.

    Un preset est l'unité visible dans la bibliothèque. ``id`` reste
    stable pour les presets intégrés (utilisé pour la traduction et la
    persistance) ; pour les presets utilisateur un identifiant UUID est
    généré automatiquement.

    Attributes:
        id: Identifiant stable du preset.
        name: Libellé court (clé i18n ``effects.preset.<id>.name``).
        description: Libellé long (clé i18n
            ``effects.preset.<id>.description``).
        category: Catégorie affichée.
        effects: Chaîne d'effets à appliquer au clip.
        builtin: ``True`` pour les presets livrés, ``False`` pour les
            presets utilisateur.
    """

    id: str
    name: str
    description: str
    category: EffectCategory
    effects: tuple[ClipEffect, ...] = field(default_factory=tuple)
    builtin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Un preset doit porter un identifiant non vide.")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Un preset doit porter un nom non vide.")
        if not isinstance(self.description, str):
            raise ValueError("La description d'un preset doit être une chaîne.")
        # Normalise la catégorie et la chaîne d'effets.
        object.__setattr__(self, "category", EffectCategory(self.category))
        object.__setattr__(self, "effects", tuple(self.effects))


# ---------------------------------------------------------------------------
# Fabrique de presets
# ---------------------------------------------------------------------------


def _make_effect(
    effect_type: EffectType | str,
    **params: float,
) -> ClipEffect:
    """Construit un :class:`ClipEffect` à partir des paramètres nommés.

    Les clés absentes sont comblées par les défauts ; les valeurs
    inconnues déclenchent la validation normale du modèle d'effets.
    """
    defaults = default_parameters(effect_type)
    defaults.update({k: float(v) for k, v in params.items()})
    return create_effect(effect_type, params=defaults)


def make_preset(
    preset_id: str,
    name: str,
    description: str,
    category: EffectCategory | str,
    effects: Sequence[ClipEffect],
    *,
    builtin: bool = False,
) -> EffectPreset:
    """Construit un :class:`EffectPreset` en validant la chaîne d'effets.

    Args:
        preset_id: Identifiant stable du preset.
        name: Libellé court du preset.
        description: Description longue (affichée en sous-titre).
        category: :class:`EffectCategory` ou sa valeur.
        effects: Séquence de :class:`ClipEffect` à appliquer.
        builtin: ``True`` pour un preset livré (non supprimable).

    Raises:
        ValueError: si une catégorie est inconnue ou si la chaîne
            d'effets contient un doublon unique (cf. ``is_single_instance``).
    """
    if effects is None:
        effects = ()
    seen_unique: set[EffectType] = set()
    for effect in effects:
        from .effects_model import is_single_instance

        if is_single_instance(effect.type):
            if effect.type in seen_unique:
                raise ValueError(
                    f"Preset '{preset_id}' : l'effet unique "
                    f"'{effect.type.value}' est présent plusieurs fois."
                )
            seen_unique.add(effect.type)
    return EffectPreset(
        id=preset_id,
        name=name,
        description=description,
        category=EffectCategory(category),
        effects=tuple(effects),
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Bibliothèque intégrée
# ---------------------------------------------------------------------------


def _cinema_preset() -> EffectPreset:
    """Look cinéma : contraste poussé, saturation modérée, vignette douce."""
    return make_preset(
        preset_id="cinema",
        name="Cinéma",
        description=(
            "Contraste poussé, saturation contenue, vignette douce — "
            "rendu grand écran prêt à l'emploi."
        ),
        category=EffectCategory.LOOK,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.25,
                saturation=0.85,
                brightness=-0.05,
            ),
            _make_effect(EffectType.VIGNETTE, intensity=0.55),
        ),
        builtin=True,
    )


def _black_and_white_preset() -> EffectPreset:
    """Noir et blanc contrasté."""
    return make_preset(
        preset_id="black_and_white",
        name="Noir et blanc",
        description=(
            "Conversion en niveaux de gris, avec un léger regain de "
            "contraste pour un rendu éditorial."
        ),
        category=EffectCategory.STYLIZED,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.15,
                saturation=0.0,
            ),
            _make_effect(EffectType.BLACK_AND_WHITE),
        ),
        builtin=True,
    )


def _vintage_preset() -> EffectPreset:
    """Look vintage : sépia, contraste et vignette."""
    return make_preset(
        preset_id="vintage",
        name="Vintage",
        description=(
            "Virage sépia chaleureux, léger gain de contraste et "
            "vignette marquée pour un look rétro."
        ),
        category=EffectCategory.LOOK,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.1,
                saturation=0.7,
            ),
            _make_effect(EffectType.SEPIA),
            _make_effect(EffectType.VIGNETTE, intensity=0.6),
        ),
        builtin=True,
    )


def _sharp_preset() -> EffectPreset:
    """Netteté accentuée + micro-contraste."""
    return make_preset(
        preset_id="sharp",
        name="Net",
        description=(
            "Accentue les contours et booste le contraste pour un rendu "
            "précis et détaillé."
        ),
        category=EffectCategory.CREATIVE,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.05,
            ),
            _make_effect(EffectType.SHARPEN, intensity=2.0),
        ),
        builtin=True,
    )


def _blur_preset() -> EffectPreset:
    """Flou doux pour masquer un visage ou un détail sensible."""
    return make_preset(
        preset_id="blur",
        name="Flou",
        description=(
            "Adoucit l'image avec un flou gaussien maîtrisé pour "
            "anonymiser ou estomper l'arrière-plan."
        ),
        category=EffectCategory.CREATIVE,
        effects=(_make_effect(EffectType.BLUR, intensity=8.0),),
        builtin=True,
    )


def _cool_grade_preset() -> EffectPreset:
    """Étalonnage froid (légère désaturation + luminosité bleutée)."""
    return make_preset(
        preset_id="cool_grade",
        name="Étalonnage froid",
        description=(
            "Réduit la saturation et booste la luminosité pour un rendu "
            "froid, idéal pour les scènes nocturnes."
        ),
        category=EffectCategory.COLOR,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                brightness=0.1,
                contrast=1.05,
                saturation=0.85,
            ),
        ),
        builtin=True,
    )


def _warm_grade_preset() -> EffectPreset:
    """Étalonnage chaud (saturation + petit boost de luminosité)."""
    return make_preset(
        preset_id="warm_grade",
        name="Étalonnage chaud",
        description=(
            "Saturation renforcée, contraste légèrement poussé pour un "
            "rendu chaleureux et solaire."
        ),
        category=EffectCategory.COLOR,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.08,
                saturation=1.25,
                brightness=0.04,
            ),
        ),
        builtin=True,
    )


def _noir_preset() -> EffectPreset:
    """Style film noir : N&B très contrasté + vignette profonde."""
    return make_preset(
        preset_id="noir",
        name="Film noir",
        description=(
            "Noir et blanc contrasté couplé à une vignette profonde "
            "pour un rendu expressionniste."
        ),
        category=EffectCategory.STYLIZED,
        effects=(
            _make_effect(
                EffectType.COLOR_CORRECTION,
                contrast=1.35,
                saturation=0.0,
            ),
            _make_effect(EffectType.BLACK_AND_WHITE),
            _make_effect(EffectType.VIGNETTE, intensity=0.8),
        ),
        builtin=True,
    )


def _night_look_preset() -> EffectPreset:
    """Look « Night » (vidéo sociale) : contraste et couleurs relevés, bloom sur les lumières, vignette."""
    return make_preset(
        preset_id="night_look",
        name="Night Look",
        description=(
            "Nuit urbaine : contraste et saturation relevés, halo sur les lumières vives, bords assombris. "
            "Sur un calque d'effets, il s'applique à tout le montage."
        ),
        category=EffectCategory.LOOK,
        effects=(
            _make_effect(EffectType.COLOR_CORRECTION, contrast=1.08, saturation=1.2, brightness=-0.02),
            _make_effect(EffectType.GLOW, threshold=0.62, radius=22.0, intensity=0.9),
            _make_effect(EffectType.VIGNETTE, intensity=0.45),
        ),
        builtin=True,
    )


def _neon_rush_preset() -> EffectPreset:
    """Choc lumineux : bloom fort et aberration chromatique (plans d'action, cuts)."""
    return make_preset(
        preset_id="neon_rush",
        name="Neon Rush",
        description="Halo appuyé et franges rouge / bleu : l'énergie d'un plan de course ou d'un drop.",
        category=EffectCategory.STYLIZED,
        effects=(
            _make_effect(EffectType.GLOW, threshold=0.5, radius=14.0, intensity=1.4),
            _make_effect(EffectType.CHROMATIC_ABERRATION, intensity=5.0),
        ),
        builtin=True,
    )


def builtin_presets() -> tuple[EffectPreset, ...]:
    """Retourne la bibliothèque livrée avec l'application.

    Le tuple est construit à chaque appel pour permettre d'éventuelles
    évolutions futures (presets dynamiques selon le thème) tout en
    restant immuable pour l'appelant.
    """
    return (
        _cinema_preset(),
        _black_and_white_preset(),
        _vintage_preset(),
        _sharp_preset(),
        _blur_preset(),
        _cool_grade_preset(),
        _warm_grade_preset(),
        _noir_preset(),
        _night_look_preset(),
        _neon_rush_preset(),
    )


def builtin_preset_ids() -> frozenset[str]:
    """Ensemble des identifiants de presets intégrés."""
    return frozenset(preset.id for preset in builtin_presets())


# ---------------------------------------------------------------------------
# Filtrage de la bibliothèque
# ---------------------------------------------------------------------------


def filter_presets(
    presets: Iterable[EffectPreset],
    *,
    search: str = "",
    category: EffectCategory | str | None = None,
) -> list[EffectPreset]:
    """Filtre les presets selon une recherche libre et une catégorie.

    Args:
        presets: bibliothèque source (intégrée + utilisateur).
        search: texte libre (insensible à la casse, matching simple
            sur l'identifiant et le nom).
        category: catégorie à conserver. ``None`` conserve tout.

    Returns:
        Liste ordonnée des presets correspondants.
    """
    needle = (search or "").strip().lower()
    wanted_category: EffectCategory | None = (
        EffectCategory(category) if category is not None else None
    )
    result: list[EffectPreset] = []
    for preset in presets:
        if wanted_category is not None and preset.category != wanted_category:
            continue
        if needle and needle not in preset.id.lower() and needle not in preset.name.lower():
            continue
        result.append(preset)
    return result


# ---------------------------------------------------------------------------
# Application d'un preset à un clip
# ---------------------------------------------------------------------------


def _resolve_replace_conflict(
    existing: Sequence[ClipEffect],
    incoming: ClipEffect,
) -> list[ClipEffect]:
    """Remplace un effet existant du même type unique.

    Les effets ``SINGLE_INSTANCE`` (couleur, N&B, sépia, vignette) ne
    peuvent exister qu'une fois par clip : si le clip porte déjà un
    effet de ce type, le nouveau prend sa place. Les effets
    empilables (flou, netteté) sont simplement ajoutés.
    """
    from .effects_model import is_single_instance

    if not is_single_instance(incoming.type):
        return list(existing) + [incoming]
    replaced = False
    result: list[ClipEffect] = []
    for current in existing:
        if current.type == incoming.type:
            replaced = True
            result.append(incoming)
        else:
            result.append(current)
    if not replaced:
        result.append(incoming)
    return result


def preset_effects_to_apply(
    preset: EffectPreset,
    current: Sequence[ClipEffect],
) -> list[ClipEffect]:
    """Fusionne un preset avec la chaîne existante du clip.

    Les effets du preset remplacent leurs homologues du même type
    unique (``color_correction``, ``vignette``, ``black_and_white``,
    ``sepia``) ; les effets empilables (``blur``, ``sharpen``) sont
    ajoutés à la suite.

    Args:
        preset: preset à appliquer.
        current: chaîne d'effets actuelle du clip.

    Returns:
        Nouvelle liste fusionnée (aucune mutation de ``current``).
    """
    if not preset.effects:
        return list(current)
    chain: list[ClipEffect] = list(current)
    for effect in preset.effects:
        chain = _resolve_replace_conflict(chain, effect)
    return chain


def apply_preset_to_clip(
    project: "Project",
    clip_id: str,
    preset: EffectPreset,
) -> list[ClipEffect]:
    """Applique ``preset`` au clip ``clip_id`` et retourne la chaîne finale.

    Le clip est supposé exister sur une piste vidéo (les effets ne
    s'appliquent pas aux pistes audio/sous-titres) et sa piste ne doit
    pas être verrouillée — ces invariants sont déjà vérifiés par
    :func:`core.effects_model._require_video_clip`, réutilisé ici.

    Raises:
        KeyError: si le clip est introuvable.
        ValueError: si le clip n'est pas sur une piste vidéo, ou si la
            piste est verrouillée.
    """
    from .effects_model import _require_video_clip

    clip = _require_video_clip(project, clip_id)
    clip.effects = preset_effects_to_apply(preset, clip.effects)
    return list(clip.effects)


# ---------------------------------------------------------------------------
# Capture de la chaîne d'effets d'un clip en tant que preset utilisateur
# ---------------------------------------------------------------------------


def snapshot_clip_preset(
    clip: "Clip",
    *,
    name: str,
    description: str = "",
    category: EffectCategory | str = EffectCategory.LOOK,
) -> EffectPreset:
    """Capture la chaîne d'effets courante d'un clip comme preset.

    Le preset produit est marqué ``builtin=False``. Les
    :class:`ClipEffect` capturés sont clonés (re-générés via
    :func:`create_effect`) pour ne jamais partager les références du
    clip source : modifier le clip par la suite n'altère pas le preset.
    """
    if not name or not name.strip():
        raise ValueError("Le nom du preset utilisateur est obligatoire.")
    captured: list[ClipEffect] = []
    for effect in clip.effects:
        cloned = create_effect(
            effect.type,
            enabled=effect.enabled,
            params=dict(effect.params),
        )
        # L'id du preset reste neutre (il sera régénéré par
        # ``UserPresetStore`` au moment de l'enregistrement), mais on
        # préserve l'état d'activation qui fait partie de l'intention.
        captured.append(cloned)
    return make_preset(
        preset_id=f"user-{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        description=(description or "").strip(),
        category=EffectCategory(category),
        effects=captured,
        builtin=False,
    )


# ---------------------------------------------------------------------------
# Persistance des presets utilisateur
# ---------------------------------------------------------------------------


USER_PRESETS_FILE: str = "effect_presets.json"
"""Nom du fichier de presets utilisateur dans le répertoire de config."""


def _user_presets_dir(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Détermine le répertoire de stockage des presets utilisateur.

    Priorité :
    1. ``settings_dir`` explicite ;
    2. variable d'environnement ``KUT_STUDIO_CONFIG_DIR`` (utile pour
       les tests et le mode portable) ;
    3. sinon, ``~/.config/kut-studio`` (XDG / Linux),
       ``~/Library/Application Support/Kut-Studio`` (macOS),
       ``%APPDATA%/Kut-Studio`` (Windows).
    """
    return user_config_dir(settings_dir)


def user_presets_path(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Retourne le chemin absolu du fichier de presets utilisateur."""
    return _user_presets_dir(settings_dir) / USER_PRESETS_FILE


def _serialize_preset(preset: EffectPreset) -> dict:
    """Convertit un preset en entrée JSON."""
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "category": preset.category.value,
        "effects": [
            {
                "type": effect.type.value,
                "enabled": effect.enabled,
                "params": dict(effect.params),
            }
            for effect in preset.effects
        ],
    }


def _deserialize_preset(payload: Mapping) -> EffectPreset | None:
    """Reconstruit un preset depuis une entrée JSON ou ``None`` si invalide.

    Les entrées mal formées (clé manquante, valeur hors borne, type
    inconnu…) sont silencieusement écartées : un fichier de presets
    corrompu ne doit pas planter l'application au démarrage. Une
    entrée dont **aucun** effet n'a pu être reconstruit est également
    écartée, pour éviter de polluer la bibliothèque avec des coquilles
    vides venues d'un fichier abîmé.
    """
    if not isinstance(payload, Mapping):
        return None
    preset_id = payload.get("id")
    name = payload.get("name")
    description = payload.get("description", "")
    category = payload.get("category")
    if not isinstance(preset_id, str) or not preset_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(description, str):
        return None
    try:
        category_value = EffectCategory(category)
    except ValueError:
        return None
    raw_effects = payload.get("effects", [])
    if not isinstance(raw_effects, list):
        return None
    effects: list[ClipEffect] = []
    for raw_effect in raw_effects:
        if not isinstance(raw_effect, Mapping):
            continue
        try:
            effect_type = EffectType(raw_effect.get("type"))
        except ValueError:
            continue
        params_raw = raw_effect.get("params") or {}
        try:
            validate_parameters(effect_type, params_raw)
        except ValueError:
            continue
        effects.append(
            create_effect(
                effect_type,
                enabled=bool(raw_effect.get("enabled", True)),
                params=params_raw,
            )
        )
    # Une entrée dont aucun effet n'est valide est considérée comme
    # corrompue : on l'écarte plutôt que de la laisser apparaître vide.
    if not effects:
        return None
    try:
        return make_preset(
            preset_id=preset_id,
            name=name,
            description=description,
            category=category_value,
            effects=effects,
            builtin=False,
        )
    except ValueError:
        return None


def load_user_presets(
    settings_dir: str | os.PathLike[str] | None = None,
) -> list[EffectPreset]:
    """Charge les presets utilisateur depuis le disque.

    Renvoie une liste vide si le fichier est absent ou corrompu. Les
    presets intégrés (``builtin=True``) sont rejetés même s'ils sont
    présents dans le fichier — leur édition n'est jamais autorisée.
    """
    path = user_presets_path(settings_dir)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    presets: list[EffectPreset] = []
    for entry in data:
        preset = _deserialize_preset(entry)
        if preset is None or preset.builtin:
            continue
        presets.append(preset)
    return presets


def save_user_presets(
    presets: Sequence[EffectPreset],
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Sauvegarde les presets utilisateur dans un fichier JSON UTF-8.

    L'écriture est atomique (fichier temporaire + ``os.replace``) pour
    ne jamais tronquer le fichier en cas d'erreur en cours d'écriture.

    Args:
        presets: presets à enregistrer. Les presets intégrés sont
            silencieusement ignorés : on ne persiste jamais un preset
            qu'on ne peut pas éditer.
        settings_dir: répertoire cible (``None`` = standard).

    Returns:
        Le chemin effectif du fichier sauvegardé.
    """
    base = _user_presets_dir(settings_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / USER_PRESETS_FILE
    payload = [
        _serialize_preset(preset)
        for preset in presets
        if not preset.builtin
    ]
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(base),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, ensure_ascii=False)
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


class UserPresetStore:
    """Gestionnaire en mémoire des presets utilisateur.

    Le store expose une API CRUD alignée sur l'IHM et déclenche la
    persistance à chaque mutation. Les presets intégrés (livrés avec
    l'app) sont conservés séparément par les couches UI : ils ne
    traversent jamais cette classe.
    """

    def __init__(
        self,
        presets: Sequence[EffectPreset] | None = None,
        settings_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        # Les presets utilisateur sont stockés sous forme de dict pour
        # permettre une recherche rapide par id.
        self._settings_dir = settings_dir
        initial = list(presets) if presets is not None else load_user_presets(settings_dir)
        self._presets: dict[str, EffectPreset] = {
            preset.id: preset for preset in initial if not preset.builtin
        }
        self._listeners: list = []

    # ----- API publique --------------------------------------------------

    def all(self) -> list[EffectPreset]:
        """Retourne une copie de la liste des presets utilisateur."""
        return list(self._presets.values())

    def get(self, preset_id: str) -> EffectPreset | None:
        """Retourne le preset ``preset_id`` ou ``None``."""
        return self._presets.get(preset_id)

    def add(self, preset: EffectPreset) -> EffectPreset:
        """Ajoute un preset utilisateur et persiste le store.

        Args:
            preset: preset à ajouter (sera marqué ``builtin=False``).

        Raises:
            ValueError: si un preset du même identifiant existe déjà ou
                si ``preset`` est intégré.
        """
        if preset.builtin:
            raise ValueError("Les presets intégrés ne peuvent pas être ajoutés.")
        if preset.id in self._presets:
            raise ValueError(f"Un preset '{preset.id}' existe déjà.")
        self._presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def replace(self, preset: EffectPreset) -> EffectPreset:
        """Remplace un preset existant du même ``id``."""
        if preset.builtin:
            raise ValueError("Les presets intégrés ne peuvent pas être remplacés.")
        if preset.id not in self._presets:
            raise KeyError(f"Preset '{preset.id}' introuvable.")
        self._presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def remove(self, preset_id: str) -> EffectPreset:
        """Supprime ``preset_id`` et retourne le preset retiré."""
        if preset_id not in self._presets:
            raise KeyError(f"Preset '{preset_id}' introuvable.")
        removed = self._presets.pop(preset_id)
        self._persist()
        self._notify_changed()
        return removed

    def subscribe(self, callback) -> None:
        """Inscrit ``callback`` aux changements (ajout / suppression / remplace)."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    # ----- Persistance ---------------------------------------------------

    def _persist(self) -> None:
        save_user_presets(self.all(), self._settings_dir)

    def _notify_changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                # On ne casse pas la chaîne pour un listener fautif.
                pass


# ---------------------------------------------------------------------------
# Bibliothèque complète (intégrée + utilisateur)
# ---------------------------------------------------------------------------


def combined_library(
    user_presets: Sequence[EffectPreset] | None = None,
) -> list[EffectPreset]:
    """Retourne la bibliothèque complète : intégrés puis utilisateur.

    Args:
        user_presets: presets utilisateur à inclure (s'ils ne sont pas
            fournis, seuls les intégrés sont retournés).
    """
    library = list(builtin_presets())
    if user_presets:
        library.extend(p for p in user_presets if not p.builtin)
    return library


__all__ = [
    "CATEGORY_DESCRIPTIONS",
    "CATEGORY_LABELS",
    "EffectCategory",
    "EffectPreset",
    "USER_PRESETS_FILE",
    "UserPresetStore",
    "apply_preset_to_clip",
    "builtin_preset_ids",
    "builtin_presets",
    "combined_library",
    "effect_category",
    "filter_presets",
    "load_user_presets",
    "make_preset",
    "preset_effects_to_apply",
    "save_user_presets",
    "snapshot_clip_preset",
    "user_presets_path",
]
