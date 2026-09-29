"""Bibliothèque de presets de transitions pour Kut-Studio (tâche 23).

Ce module complète :mod:`core.transitions` côté bibliothèque : il ne
manipule plus une :class:`Transition` isolée mais une *collection* de
presets prêts à appliquer. Les pièces du module sont :

- :class:`TransitionPresetCategory` — taxonomies stables (``fade``,
  ``wipe``) ;
- :class:`TransitionPreset` — un preset nommé portant un
  :class:`TransitionType`, une durée par défaut et un drapeau
  ``builtin`` ;
- :func:`builtin_transition_presets` — la bibliothèque livrée avec
  l'app, alignée sur les quatre types FFmpeg supportés ;
- :func:`filter_transition_presets` — recherche et filtres ;
- :class:`TransitionPresetStore` — gestion des favoris et des presets
  utilisateur (CRUD + persistance JSON dans le répertoire de
  configuration).

Règles de conception (identiques à :mod:`core.effects_library`) :

- module pur (aucune dépendance PySide6) : tests en CLI ;
- un preset décrit un *état cible* de transition, pas un identifiant
  catalogue : deux presets « Fondu enchaîné » peuvent cohabiter ;
- les favoris sont stockés par ``preset_id`` dans un fichier JSON
  séparé des presets utilisateur ;
- les fichiers corrompus sont neutralisés à la lecture, jamais
  propagés comme une erreur ;
- aucune fonction publique ne mute ses entrées.
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
from typing import TYPE_CHECKING, Iterable, Sequence

from .transitions import TransitionType

if TYPE_CHECKING:  # pragma: no cover - import de typage uniquement
    from .project_model import Project


# ---------------------------------------------------------------------------
# Catégories
# ---------------------------------------------------------------------------


class TransitionPresetCategory(str, Enum):
    """Catégories visibles dans la bibliothèque de transitions.

    Les 18 transitions natives sont réparties en cinq familles pour
    faciliter la navigation : ``fade`` (fondus), ``wipe`` (balayages
    et glissements), ``shape`` (formes géométriques), ``dissolve``
    (dissolutions), ``smooth`` (glissements fluides). Les catégories
    ``fade`` et ``wipe`` existaient avant la tâche 26 ; les trois
    autres sont introduites par cette tâche.
    """

    FADE = "fade"
    WIPE = "wipe"
    SHAPE = "shape"
    DISSOLVE = "dissolve"
    SMOOTH = "smooth"


# Libellé court + description longue par catégorie. Les libellés
# localisés vivent dans ``ui.i18n`` ; ces constantes servent de repli
# quand la traduction est absente (tests, scripts CLI).
CATEGORY_LABELS: dict[TransitionPresetCategory, str] = {
    TransitionPresetCategory.FADE: "Fondus",
    TransitionPresetCategory.WIPE: "Balayages",
    TransitionPresetCategory.SHAPE: "Formes",
    TransitionPresetCategory.DISSOLVE: "Dissolutions",
    TransitionPresetCategory.SMOOTH: "Glissements fluides",
}


CATEGORY_DESCRIPTIONS: dict[TransitionPresetCategory, str] = {
    TransitionPresetCategory.FADE: "Fondu enchaîné, fondu au noir, fondu au blanc.",
    TransitionPresetCategory.WIPE: "Balayages et glissements entre deux clips.",
    TransitionPresetCategory.SHAPE: "Cercles, radial et formes géométriques.",
    TransitionPresetCategory.DISSOLVE: "Dissolutions, pixellisation et effets associés.",
    TransitionPresetCategory.SMOOTH: "Glissements fluides gauche/droite.",
}


# ---------------------------------------------------------------------------
# Preset
# ---------------------------------------------------------------------------


# Bornes alignées sur celles utilisées par l'inspecteur de transitions.
MIN_DURATION: float = 0.1
MAX_DURATION: float = 5.0


@dataclass(frozen=True)
class TransitionPreset:
    """Preset de transition prêt à appliquer.

    Attributes:
        id: Identifiant stable du preset.
        name: Libellé court (clé i18n ``transitions.preset.<id>.name``).
        description: Libellé long (clé i18n
            ``transitions.preset.<id>.description``).
        category: Catégorie affichée.
        transition_type: :class:`TransitionType` ciblé.
        default_duration: Durée appliquée par défaut.
        builtin: ``True`` pour les presets livrés (non supprimables).
    """

    id: str
    name: str
    description: str
    category: TransitionPresetCategory
    transition_type: TransitionType
    default_duration: float
    builtin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Un preset doit porter un identifiant non vide.")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Un preset doit porter un nom non vide.")
        if not isinstance(self.description, str):
            raise ValueError("La description d'un preset doit être une chaîne.")
        object.__setattr__(self, "category", TransitionPresetCategory(self.category))
        object.__setattr__(
            self, "transition_type", TransitionType(self.transition_type)
        )
        if self.default_duration < MIN_DURATION or self.default_duration > MAX_DURATION:
            raise ValueError(
                f"La durée par défaut doit être dans [{MIN_DURATION}, "
                f"{MAX_DURATION}] (reçu : {self.default_duration})."
            )


def make_transition_preset(
    preset_id: str,
    name: str,
    description: str,
    category: TransitionPresetCategory | str,
    transition_type: TransitionType | str,
    default_duration: float,
    *,
    builtin: bool = False,
) -> TransitionPreset:
    """Fabrique un :class:`TransitionPreset` validé."""
    return TransitionPreset(
        id=preset_id,
        name=name,
        description=description,
        category=TransitionPresetCategory(category),
        transition_type=TransitionType(transition_type),
        default_duration=float(default_duration),
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Bibliothèque intégrée
# ---------------------------------------------------------------------------


def _crossfade_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="crossfade",
        name="Fondu enchaîné",
        description=(
            "Mixe les deux clips pendant la transition : la sortie "
            "s'estompe pendant que l'entrée apparaît, idéal pour des "
            "raccords doux."
        ),
        category=TransitionPresetCategory.FADE,
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
        builtin=True,
    )


def _fade_black_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="fade_black",
        name="Fondu au noir",
        description=(
            "Bascule via un écran noir : le clip sortant se fond au "
            "noir, puis le clip entrant remonte du noir."
        ),
        category=TransitionPresetCategory.FADE,
        transition_type=TransitionType.FADE_BLACK,
        default_duration=0.75,
        builtin=True,
    )


def _wipe_left_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="wipe_left",
        name="Balayage gauche",
        description=(
            "Le nouveau clip balaie l'ancien vers la gauche, révélant "
            "le contenu entrant progressivement."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.WIPE_LEFT,
        default_duration=0.5,
        builtin=True,
    )


def _wipe_right_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="wipe_right",
        name="Balayage droite",
        description=(
            "Le nouveau clip balaie l'ancien vers la droite, comme un "
            "rideau qui s'ouvre dans l'autre sens."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.WIPE_RIGHT,
        default_duration=0.5,
        builtin=True,
    )


# ---------------------------------------------------------------------------
# Catalogue étendu (tâche 26) — 14 transitions supplémentaires
# ---------------------------------------------------------------------------


def _wipe_up_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="wipe_up",
        name="Balayage haut",
        description=(
            "Le nouveau clip balaie l'ancien vers le haut, révélant "
            "le contenu entrant par le bas."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.WIPE_UP,
        default_duration=0.5,
        builtin=True,
    )


def _wipe_down_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="wipe_down",
        name="Balayage bas",
        description=(
            "Le nouveau clip balaie l'ancien vers le bas, révélant "
            "le contenu entrant par le haut."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.WIPE_DOWN,
        default_duration=0.5,
        builtin=True,
    )


def _slide_left_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="slide_left",
        name="Glissement gauche",
        description=(
            "Le nouveau clip glisse depuis la droite et pousse "
            "l'ancien clip hors du cadre par la gauche."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.SLIDE_LEFT,
        default_duration=0.6,
        builtin=True,
    )


def _slide_right_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="slide_right",
        name="Glissement droite",
        description=(
            "Le nouveau clip glisse depuis la gauche et pousse "
            "l'ancien clip hors du cadre par la droite."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.SLIDE_RIGHT,
        default_duration=0.6,
        builtin=True,
    )


def _slide_up_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="slide_up",
        name="Glissement haut",
        description=(
            "Le nouveau clip glisse depuis le bas et pousse l'ancien "
            "hors du cadre par le haut."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.SLIDE_UP,
        default_duration=0.6,
        builtin=True,
    )


def _slide_down_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="slide_down",
        name="Glissement bas",
        description=(
            "Le nouveau clip glisse depuis le haut et pousse l'ancien "
            "hors du cadre par le bas."
        ),
        category=TransitionPresetCategory.WIPE,
        transition_type=TransitionType.SLIDE_DOWN,
        default_duration=0.6,
        builtin=True,
    )


def _circle_open_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="circle_open",
        name="Cercle ouverture",
        description=(
            "Un cercle s'ouvre depuis le centre, révélant "
            "progressivement le clip entrant."
        ),
        category=TransitionPresetCategory.SHAPE,
        transition_type=TransitionType.CIRCLE_OPEN,
        default_duration=0.75,
        builtin=True,
    )


def _circle_close_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="circle_close",
        name="Cercle fermeture",
        description=(
            "Un cercle se referme depuis les bords du cadre vers le "
            "centre, faisant disparaître le clip sortant."
        ),
        category=TransitionPresetCategory.SHAPE,
        transition_type=TransitionType.CIRCLE_CLOSE,
        default_duration=0.75,
        builtin=True,
    )


def _dissolve_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="dissolve",
        name="Dissolution",
        description=(
            "Le clip sortant se décompose en particules qui "
            "révèlent progressivement le clip entrant."
        ),
        category=TransitionPresetCategory.DISSOLVE,
        transition_type=TransitionType.DISSOLVE,
        default_duration=0.75,
        builtin=True,
    )


def _pixelize_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="pixelize",
        name="Pixellisation",
        description=(
            "L'image se décompose en gros pixels puis se "
            "recompose avec le clip entrant."
        ),
        category=TransitionPresetCategory.DISSOLVE,
        transition_type=TransitionType.PIXELIZE,
        default_duration=0.85,
        builtin=True,
    )


def _radial_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="radial",
        name="Transition radiale",
        description=(
            "Un balayage radial part du centre vers les bords, "
            "entrainant le clip entrant comme une vague circulaire."
        ),
        category=TransitionPresetCategory.SHAPE,
        transition_type=TransitionType.RADIAL,
        default_duration=0.85,
        builtin=True,
    )


def _fade_white_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="fade_white",
        name="Fondu au blanc",
        description=(
            "Bascule via un écran blanc : le clip sortant se fond au "
            "blanc, puis le clip entrant remonte du blanc. Idéal pour "
            "les rêves, les souvenirs ou les scènes très lumineuses."
        ),
        category=TransitionPresetCategory.FADE,
        transition_type=TransitionType.FADE_WHITE,
        default_duration=0.75,
        builtin=True,
    )


def _smooth_left_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="smooth_left",
        name="Glissement fluide gauche",
        description=(
            "Glissement doux vers la gauche avec un léger "
            "fondu enchaîné, pour des transitions cinématiques."
        ),
        category=TransitionPresetCategory.SMOOTH,
        transition_type=TransitionType.SMOOTH_LEFT,
        default_duration=0.7,
        builtin=True,
    )


def _smooth_right_preset() -> TransitionPreset:
    return make_transition_preset(
        preset_id="smooth_right",
        name="Glissement fluide droite",
        description=(
            "Glissement doux vers la droite avec un léger "
            "fondu enchaîné, pour des transitions cinématiques."
        ),
        category=TransitionPresetCategory.SMOOTH,
        transition_type=TransitionType.SMOOTH_RIGHT,
        default_duration=0.7,
        builtin=True,
    )


def builtin_transition_presets() -> tuple[TransitionPreset, ...]:
    """Retourne la bibliothèque livrée avec l'application.

    Le catalogue natif comprend désormais 18 transitions (4 historiques
    + 14 ajoutées par la tâche 26) : fondu enchaîné, fondu au noir,
    fondu au blanc, quatre balayages (haut, bas, gauche, droite),
    quatre glissements (haut, bas, gauche, droite), cercle ouverture /
    fermeture, dissolution, pixellisation, transition radiale, et deux
    glissements fluides (gauche, droite).
    """
    return (
        # Historiques (tâche 23)
        _crossfade_preset(),
        _fade_black_preset(),
        _wipe_left_preset(),
        _wipe_right_preset(),
        # Tâche 26 : catalogue étendu
        _wipe_up_preset(),
        _wipe_down_preset(),
        _slide_left_preset(),
        _slide_right_preset(),
        _slide_up_preset(),
        _slide_down_preset(),
        _circle_open_preset(),
        _circle_close_preset(),
        _dissolve_preset(),
        _pixelize_preset(),
        _radial_preset(),
        _fade_white_preset(),
        _smooth_left_preset(),
        _smooth_right_preset(),
    )


def builtin_transition_preset_ids() -> frozenset[str]:
    """Ensemble des identifiants de presets intégrés."""
    return frozenset(p.id for p in builtin_transition_presets())


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def filter_transition_presets(
    presets: Iterable[TransitionPreset],
    *,
    search: str = "",
    category: TransitionPresetCategory | str | None = None,
    favorites: Sequence[str] | None = None,
    favorites_only: bool = False,
) -> list[TransitionPreset]:
    """Filtre les presets selon une recherche, une catégorie, des favoris.

    Args:
        presets: bibliothèque source.
        search: texte libre (insensible à la casse).
        category: catégorie à conserver (``None`` = tout).
        favorites: liste d'identifiants favoris.
        favorites_only: si ``True``, ne conserve que les presets dont
            l'identifiant est dans ``favorites``.
    """
    needle = (search or "").strip().lower()
    wanted_category = (
        TransitionPresetCategory(category) if category is not None else None
    )
    favorite_set = set(favorites or ())
    result: list[TransitionPreset] = []
    for preset in presets:
        if wanted_category is not None and preset.category != wanted_category:
            continue
        if favorites_only and preset.id not in favorite_set:
            continue
        if needle and needle not in preset.id.lower() and needle not in preset.name.lower():
            continue
        result.append(preset)
    return result


# ---------------------------------------------------------------------------
# Application d'un preset
# ---------------------------------------------------------------------------


def apply_transition_preset(
    project: "Project",
    from_clip_id: str,
    to_clip_id: str,
    preset: TransitionPreset,
    duration: float | None = None,
) -> "Transition":
    """Pose la transition ``preset`` entre deux clips vidéo consécut.

    Args `` ``duration``: durée override `` `` `` None`` `` utilise ``preset.default_duration``.

    Raises ``KeyError``: ``ValueError``: erreurs propagées par ```` :func:`core.transitions.add_transition`.
    """
    from .transitions import add_transition

    actual_duration = (
        float(duration)
        if duration is not None
        else float(preset.default_duration)
    )
    return add_transition(
        project,
        from_clip_id,
        to_clip_id,
        preset.transition_type,
        actual_duration,
    )


# ---------------------------------------------------------------------------
# Capture d'une transition en tant que preset utilisateur
# ---------------------------------------------------------------------------


def make_user_transition_preset(
    name: str,
    description: str,
    transition_type: TransitionType | str,
    default_duration: float,
) -> TransitionPreset:
    """Crée un preset utilisateur ad hoc (non intégré)."""
    if not name or not name.strip():
        raise ValueError("Le nom du preset utilisateur est obligatoire.")
    if default_duration < MIN_DURATION or default_duration > MAX_DURATION:
        raise ValueError(
            f"La durée par défaut doit être dans [{MIN_DURATION}, "
            f"{MAX_DURATION}] (reçu : {default_duration})."
        )
    return make_transition_preset(
        preset_id=f"user-{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        description=(description or "").strip(),
        category=_infer_category(TransitionType(transition_type)),
        transition_type=transition_type,
        default_duration=float(default_duration),
        builtin=False,
    )


def _infer_category(transition_type: TransitionType) -> TransitionPresetCategory:
    """Détermine la catégorie implicite d'un type de transition.

    La taxonomie reflète les familles introduites par la tâche 26 :
    les cinq catégories ``fade``, ``wipe``, ``shape``, ``dissolve`` et
    ``smooth`` couvrent la totalité des 18 types natifs. Les types
    historiques (``CROSSFADE``, ``FADE_BLACK``, ``WIPE_LEFT``,
    ``WIPE_RIGHT``) sont rangés dans leurs catégories d'origine pour
    ne pas bouleverser les favoris déjà enregistrés par les utilisateurs.
    """
    if transition_type in (
        TransitionType.CROSSFADE,
        TransitionType.FADE_BLACK,
        TransitionType.FADE_WHITE,
    ):
        return TransitionPresetCategory.FADE
    if transition_type in (
        TransitionType.WIPE_LEFT,
        TransitionType.WIPE_RIGHT,
        TransitionType.WIPE_UP,
        TransitionType.WIPE_DOWN,
        TransitionType.SLIDE_LEFT,
        TransitionType.SLIDE_RIGHT,
        TransitionType.SLIDE_UP,
        TransitionType.SLIDE_DOWN,
    ):
        return TransitionPresetCategory.WIPE
    if transition_type in (
        TransitionType.CIRCLE_OPEN,
        TransitionType.CIRCLE_CLOSE,
        TransitionType.RADIAL,
    ):
        return TransitionPresetCategory.SHAPE
    if transition_type in (
        TransitionType.DISSOLVE,
        TransitionType.PIXELIZE,
    ):
        return TransitionPresetCategory.DISSOLVE
    if transition_type in (
        TransitionType.SMOOTH_LEFT,
        TransitionType.SMOOTH_RIGHT,
    ):
        return TransitionPresetCategory.SMOOTH
    # Filet de sécurité pour un éventuel type futur.
    return TransitionPresetCategory.WIPE


# ---------------------------------------------------------------------------
# Persistance : favoris et presets utilisateur
# ---------------------------------------------------------------------------


TRANSITION_PRESETS_FILE: str = "transition_presets.json"
"""Nom du fichier de presets utilisateur et favoris."""


def _user_presets_dir(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Détermine le répertoire de stockage des presets utilisateur et favoris.

    Identique à :func:`core.effects_library._user_presets_dir`, mais
    isolé pour que les deux modules ne se partagent pas un répertoire
    commun à l'avenir.
    """
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


def transition_presets_path(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Retourne le chemin du fichier de favoris + presets utilisateur."""
    return _user_presets_dir(settings_dir) / TRANSITION_PRESETS_FILE


def _coerce_user_preset(payload: object) -> TransitionPreset | None:
    """Reconstruit un preset utilisateur depuis une entrée JSON."""
    if not isinstance(payload, dict):
        return None
    try:
        name = str(payload.get("name", "")).strip()
    except (TypeError, ValueError):
        return None
    if not name:
        return None
    try:
        transition_type = TransitionType(payload.get("transition_type"))
    except ValueError:
        return None
    description = payload.get("description", "")
    if not isinstance(description, str):
        description = ""
    try:
        default_duration = float(payload.get("default_duration"))
    except (TypeError, ValueError):
        return None
    if default_duration < MIN_DURATION or default_duration > MAX_DURATION:
        return None
    preset_id = payload.get("id")
    if not isinstance(preset_id, str) or not preset_id:
        return None
    try:
        return make_transition_preset(
            preset_id=preset_id,
            name=name,
            description=description,
            category=_infer_category(transition_type),
            transition_type=transition_type,
            default_duration=default_duration,
            builtin=False,
        )
    except ValueError:
        return None


def _coerce_favorites(payload: object) -> list[str]:
    """Reconstruit la liste des favoris depuis une entrée JSON."""
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    for entry in payload:
        if isinstance(entry, str) and entry:
            result.append(entry)
    return result


def load_transition_preset_data(
    settings_dir: str | os.PathLike[str] | None = None,
) -> tuple[list[TransitionPreset], list[str]]:
    """Charge favoris et presets utilisateur depuis le disque.

    Renvoie ``(presets, favoris)``. Les entrées mal formées sont
    silencieusement écartées.
    """
    path = transition_presets_path(settings_dir)
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
        for preset in (_coerce_user_preset(entry) for entry in raw_user_presets)
        if preset is not None
    ]
    favorites = _coerce_favorites(raw_favorites)
    return user_presets, favorites


def save_transition_preset_data(
    user_presets: Sequence[TransitionPreset],
    favorites: Sequence[str],
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persiste favoris et presets utilisateur (écriture atomique)."""
    base = _user_presets_dir(settings_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / TRANSITION_PRESETS_FILE
    payload = {
        "version": 1,
        "favorites": [fav for fav in favorites if isinstance(fav, str) and fav],
        "user_presets": [
            {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "transition_type": preset.transition_type.value,
                "default_duration": float(preset.default_duration),
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


class TransitionPresetStore:
    """Gestionnaire en mémoire des favoris et presets utilisateur.

    Le store expose une API CRUD alignée sur l'IHM et déclenche la
    persistance à chaque mutation. Les presets intégrés (livrés avec
    l'app) sont traversés par les filtres (``get`` en particulier) mais
    ne sont jamais modifiés.
    """

    def __init__(
        self,
        user_presets: Sequence[TransitionPreset] | None = None,
        favorites: Sequence[str] | None = None,
        settings_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._settings_dir = settings_dir
        if user_presets is None or favorites is None:
            loaded_user, loaded_favorites = load_transition_preset_data(
                settings_dir
            )
            if user_presets is None:
                user_presets = loaded_user
            if favorites is None:
                favorites = loaded_favorites
        self._user_presets: dict[str, TransitionPreset] = {
            preset.id: preset for preset in user_presets if not preset.builtin
        }
        self._favorites: set[str] = {fav for fav in favorites if fav}
        self._listeners: list = []

    # ----- API publique --------------------------------------------------

    def all_user_presets(self) -> list[TransitionPreset]:
        """Retourne une copie des presets utilisateur."""
        return list(self._user_presets.values())

    def favorites(self) -> list[str]:
        """Retourne une copie des identifiants favoris."""
        return list(self._favorites)

    def is_favorite(self, preset_id: str) -> bool:
        """``True`` si le preset est marqué comme favori."""
        return preset_id in self._favorites

    def get_preset(self, preset_id: str) -> TransitionPreset | None:
        """Recherche le preset dans la bibliothèque complète (intégré + utilisateur)."""
        for preset in builtin_transition_presets():
            if preset.id == preset_id:
                return preset
        return self._user_presets.get(preset_id)

    def all_presets(self) -> list[TransitionPreset]:
        """Retourne la bibliothèque complète (intégrés puis utilisateur)."""
        library = list(builtin_transition_presets())
        library.extend(self._user_presets.values())
        return library

    def add_user_preset(self, preset: TransitionPreset) -> TransitionPreset:
        """Ajoute un preset utilisateur et persiste le store."""
        if preset.builtin:
            raise ValueError("Les presets intégrés ne peuvent pas être ajoutés.")
        if preset.id in self._user_presets:
            raise ValueError(f"Un preset '{preset.id}' existe déjà.")
        self._user_presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def remove_user_preset(self, preset_id: str) -> TransitionPreset:
        """Supprime un preset utilisateur et retourne le preset retiré."""
        if preset_id not in self._user_presets:
            raise KeyError(f"Preset '{preset_id}' introuvable.")
        removed = self._user_presets.pop(preset_id)
        # Un preset retiré perd aussi son statut favori.
        self._favorites.discard(preset_id)
        self._persist()
        self._notify_changed()
        return removed

    def set_favorite(self, preset_id: str, favorite: bool) -> bool:
        """Ajoute ou retire ``preset_id`` des favoris.

        Les favoris qui ne correspondent ni à un preset intégré ni à un
        preset utilisateur connu sont silencieusement rejetés : on ne
        garde pas d'identifiants orphelins en persistance.
        """
        if favorite:
            # On n'autorise les favoris que pour des presets existants.
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
        """Inscrit ``callback`` aux changements (ajout / suppression / favoris)."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    # ----- Persistance ---------------------------------------------------

    def _persist(self) -> None:
        save_transition_preset_data(
            list(self._user_presets.values()),
            list(self._favorites),
            self._settings_dir,
        )

    def _notify_changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                # On ne casse pas la chaîne pour un listener fautif.
                pass

    def _preset_exists(self, preset_id: str) -> bool:
        if preset_id in builtin_transition_preset_ids():
            return True
        return preset_id in self._user_presets


__all__ = [
    "BUILTIN_TRANSITION_PRESETS_COUNT",
    "CATEGORY_DESCRIPTIONS",
    "CATEGORY_LABELS",
    "MAX_DURATION",
    "MIN_DURATION",
    "TRANSITION_PRESETS_FILE",
    "TransitionPreset",
    "TransitionPresetCategory",
    "TransitionPresetStore",
    "apply_transition_preset",
    "builtin_transition_preset_ids",
    "builtin_transition_presets",
    "filter_transition_presets",
    "load_transition_preset_data",
    "make_transition_preset",
    "make_user_transition_preset",
    "save_transition_preset_data",
    "transition_presets_path",
]


# Nombre de presets natifs à la livraison : utilisé par les tests pour
# détecter une régression silencieuse (ajout / retrait accidentel).
BUILTIN_TRANSITION_PRESETS_COUNT: int = 18