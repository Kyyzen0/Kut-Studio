"""Préférences utilisateur de Kut-Studio (hors ``.kut``).

Ce module stocke des préférences **utilisateur** (thème, langue) qui
n'appartiennent à aucun projet. Elles sont persistées dans le
répertoire de configuration de l'utilisateur via
:func:`default_settings_dir`. Le fichier est un JSON UTF-8 minimal,
découplé des dataclasses métier du projet.

Règles :

- le module est **pur** : aucune dépendance PySide6 ni Qt, ce qui le
  rend testable en CLI ;
- le module n'écrit **jamais** dans le dépôt du projet ;
- les valeurs invalides ou le fichier absent renvoient aux
  valeurs par défaut (``dark``/``fr``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .atomic_io import atomic_write_json
from .platform_paths import user_config_dir
from .shortcuts import ShortcutMap


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


VALID_THEME_MODES: tuple[str, ...] = ("dark", "light", "system")
"""Modes de thème reconnus par Kut-Studio."""

VALID_LANGUAGES: tuple[str, ...] = ("fr", "en", "es")
"""Langues disponibles pour l'interface."""

VALID_PERFORMANCE_PROFILES: tuple[str, ...] = ("auto", "low", "balanced", "high")
"""Profils de performance reconnus. ``auto`` suit la machine."""

VALID_PREVIEW_QUALITIES: tuple[str, ...] = (
    "auto",
    "full",
    "half",
    "quarter",
    "eighth",
)
"""Niveaux d'aperçu. ``auto`` suit le profil, sans mesure de frames."""

DEFAULT_THEME: str = "dark"
"""Thème par défaut (le plus sûr, confirmé par l'historique de Kut-Studio)."""

DEFAULT_LANGUAGE: str = "fr"
"""Langue par défaut (français)."""

DEFAULT_PERFORMANCE_PROFILE: str = "auto"
"""Profil de performance par défaut."""

DEFAULT_PREVIEW_QUALITY: str = "auto"
"""Qualité d'aperçu par défaut."""

VALID_RENDER_QUALITIES: tuple[str, ...] = ("draft", "standard", "high")
"""Qualites de rendu d'apercu (tache 30) : Brouillon/Standard/Haute."""

DEFAULT_RENDER_QUALITY: str = "standard"
"""Qualite de rendu d'apercu par defaut."""

FILE_NAME: str = "user_settings.json"
"""Nom du fichier de préférences à l'intérieur du répertoire de config."""


# --- Scopes video / monitoring couleur (tache 31) -------------------------
#
# Les preferences de scopes sont stockees a plat dans le meme fichier
# JSON que le reste des reglages utilisateur : on evite ainsi un
# second fichier pour deux preferences mineures, et on beneficie de
# l'ecriture atomique deja en place.


DEFAULT_SCOPES_LAYOUT: str = "quad"
"""Disposition par defaut du panneau de scopes (4 vues)."""

VALID_SCOPES_LAYOUTS: tuple[str, ...] = ("quad", "single")
"""Dispositions acceptees par le panneau de scopes."""

DEFAULT_SCOPES_VIEW: str = "waveform"
"""Scope affiche en mode « vue unique » par defaut."""

VALID_SCOPES_VIEWS: tuple[str, ...] = (
    "histogram",
    "waveform",
    "parade",
    "vectorscope",
)
"""Scopes individuels acceptes."""

DEFAULT_SCOPES_LEVELS: str = "video"
"""Niveaux par defaut (16-235)."""

VALID_SCOPES_LEVELS: tuple[str, ...] = ("video", "full")
"""Modes de niveaux acceptes (limites / complets)."""


def _coerce_scopes_layout(value: object) -> str:
    """Filtre la disposition des scopes ; ``quad`` si invalide."""
    if isinstance(value, str) and value in VALID_SCOPES_LAYOUTS:
        return value
    return DEFAULT_SCOPES_LAYOUT


def _coerce_scopes_view(value: object) -> str:
    """Filtre le scope individuel ; ``waveform`` si invalide."""
    if isinstance(value, str) and value in VALID_SCOPES_VIEWS:
        return value
    return DEFAULT_SCOPES_VIEW


def _coerce_scopes_levels(value: object) -> str:
    """Filtre le mode de niveaux ; ``video`` si invalide."""
    if isinstance(value, str) and value in VALID_SCOPES_LEVELS:
        return value
    return DEFAULT_SCOPES_LEVELS


def _coerce_bool(value: object) -> bool:
    """Coerce un booleen de facon tolerante (JSON peut donner 0/1)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


DEFAULT_PROXY_PROFILE: str = "medium"
"""Profil de proxy par défaut (voir :mod:`core.proxy_profiles`)."""

DEFAULT_CACHE_MAX_GB: float = 4.0
"""Budget disque global des caches (segments d'aperçu + proxies), en Go."""

DEFAULT_EXPORT_ENCODER: str = "auto"
"""Encodeur d'export par défaut : ``auto``, ``cpu`` ou une famille matérielle détectée."""

MIN_CACHE_MAX_GB: float = 0.5
MAX_CACHE_MAX_GB: float = 512.0

DEFAULT_DECODE_MODE: str = "auto"
"""Décodage vidéo de l'aperçu : ``auto``, ``cpu`` ou un backend détecté (voir :mod:`core.decode_policy`)."""

VALID_PREVIEW_BACKENDS: tuple[str, ...] = ("auto", "cpu", "gpu")
"""Rendu du moniteur temps réel (voir :mod:`core.gpu_backend`)."""

DEFAULT_PREVIEW_BACKEND: str = "auto"
VALID_FLOW_BACKENDS: tuple[str, ...] = ("auto", "cpu", "gpu")
"""Calcul du flux optique (images intermédiaires) : le meilleur disponible, le processeur, ou un accélérateur."""
DEFAULT_FLOW_BACKEND: str = "auto"
DEFAULT_TIME_RIPPLE_TIMELINE: bool = False
"""Une édition de vitesse garde la portion de média (défaut) ou la durée sur la timeline."""
DEFAULT_CHECK_UPDATES: bool = True
"""Recherche automatique des mises à jour au démarrage (au plus une fois par jour, voir :mod:`core.updates`)."""
DEFAULT_INCLUDE_PRERELEASES: bool = False
"""Une version stable ne propose pas de préversion, sauf demande explicite."""


def _coerce_proxy_profile(value: object) -> str:
    """Filtre le profil de proxy ; ``medium`` si inconnu (ex. profil retiré)."""
    from .proxy_profiles import get_profile

    return get_profile(value if isinstance(value, str) else None).id


def _coerce_export_encoder(value: object) -> str:
    """Filtre l'encodeur d'export par défaut ; ``auto`` si absent ou inconnu."""
    from .hardware_encoding import HardwareEncoder

    try:
        return HardwareEncoder(str(value).strip().lower()).value
    except ValueError:
        return DEFAULT_EXPORT_ENCODER


def _coerce_decode_mode(value: object) -> str:
    """Filtre le mode de décodage ; ``auto`` si absent ou inconnu."""
    from .hardware_decoding import coerce_decode_mode

    return coerce_decode_mode(value).value


def _coerce_preview_backend(value: object) -> str:
    """Filtre le rendu de l'aperçu ; ``auto`` si absent ou inconnu."""
    if isinstance(value, str) and value.strip().lower() in VALID_PREVIEW_BACKENDS:
        return value.strip().lower()
    return DEFAULT_PREVIEW_BACKEND


def _coerce_flow_backend(value: object) -> str:
    """Filtre le backend de flux optique ; ``auto`` si absent ou inconnu."""
    if isinstance(value, str) and value.strip().lower() in VALID_FLOW_BACKENDS:
        return value.strip().lower()
    return DEFAULT_FLOW_BACKEND


def _coerce_cache_max_gb(value: object) -> float:
    """Borne le budget de cache ; valeur absente ou corrompue → défaut."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_CACHE_MAX_GB
    number = float(value)
    if number != number:  # NaN
        return DEFAULT_CACHE_MAX_GB
    return max(MIN_CACHE_MAX_GB, min(MAX_CACHE_MAX_GB, number))


def _coerce_timestamp(value: object) -> float:
    """Horodatage (secondes) ; absent, négatif ou corrompu → 0 (« jamais »)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return 0.0
    return number


def _coerce_skipped_version(value: object) -> str:
    """Version ignorée : une version SemVer valide, ou ``""`` (aucune)."""
    from .updates import coerce_skipped_version

    return coerce_skipped_version(value)


def _coerce_shortcuts(value: object) -> dict[str, list[str]]:
    """Filtre les raccourcis relus du disque.

    Seuls les écarts par rapport aux valeurs par défaut sont conservés,
    déjà validés et sans conflit. Une valeur absente (fichier écrit
    avant l'existence des raccourcis configurables), corrompue ou
    entièrement invalide redonne ``{}`` : les raccourcis par défaut.
    """
    return ShortcutMap.from_overrides(value).overrides()


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


DEFAULT_PHOTO_DURATION: float = 5.0
"""Durée d'une photo importée (secondes) : l'ancienne valeur fixe."""

PHOTO_DURATION_RANGE: tuple[float, float] = (0.5, 60.0)


@dataclass(frozen=True)
class UserSettings:
    """Préférences utilisateur globales.

    Attributes:
        theme_mode: ``"dark"``, ``"light"`` ou ``"system"``.
        language: code BCP-47 court (``"fr"``, ``"en"``, ``"es"``).
        performance_profile: ``"auto"``, ``"low"``, ``"balanced"`` ou ``"high"``.
        preview_quality: ``"auto"``, ``"full"``, ``"half"``, ``"quarter"``
            ou ``"eighth"``.
        proxies_enabled: utilise les proxies pour l'**aperçu** (jamais pour
            l'export, qui lit toujours les médias originaux).
        proxy_profile: profil de proxy utilisé pour générer / lire.
        export_encoder: encodeur d'export proposé par défaut (voir :mod:`core.video_encoders`).
        decode_mode: décodage vidéo de l'aperçu, des proxies et de l'analyse
            (``auto``, ``cpu`` ou un backend). L'export décode toujours en CPU.
        preview_backend: rendu du moniteur temps réel (``auto``, ``cpu``, ``gpu``).
        flow_backend: calcul du flux optique pour le rendu fidèle et l'export (``auto``, ``cpu``, ``gpu``).
        time_ripple_timeline: une édition de vitesse garde la durée sur la timeline (sinon la portion de média).
        check_updates: recherche des mises à jour au démarrage (au plus une fois par jour).
        include_prereleases: propose aussi les préversions (bêta, rc).
        skipped_update_version: version que l'utilisateur a choisi d'ignorer (``""`` : aucune) ; seule la
            vérification automatique la tait, une recherche manuelle la montre toujours.
        last_update_check: heure (secondes depuis l'epoch) de la dernière recherche réussie, 0 si jamais.
        cache_max_gb: budget disque global des caches, en Go.
        photo_duration: durée d'une photo importée sur la timeline (secondes).
        photo_fill: une photo importée remplit le cadre (l'excédent sort du cadre) au lieu d'y tenir entière.
        photo_ken_burns: une photo importée reçoit un mouvement Ken Burns (:mod:`core.ken_burns`).
        platform_zones: plateforme dont le viewer montre les zones masquées (``""`` : aucune).
        shortcuts: écarts aux raccourcis par défaut, ``{id_commande:
            [raccourcis]}`` (voir :mod:`core.shortcuts`). Une liste vide
            retire volontairement le raccourci de la commande.
    """

    theme_mode: str = DEFAULT_THEME
    language: str = DEFAULT_LANGUAGE
    performance_profile: str = DEFAULT_PERFORMANCE_PROFILE
    preview_quality: str = DEFAULT_PREVIEW_QUALITY
    render_quality: str = DEFAULT_RENDER_QUALITY
    master_gain_db: float = 0.0
    master_muted: bool = False
    # --- Scopes video (tache 31) ---
    scopes_visible: bool = True
    scopes_layout: str = DEFAULT_SCOPES_LAYOUT
    scopes_view: str = DEFAULT_SCOPES_VIEW
    scopes_levels: str = DEFAULT_SCOPES_LEVELS
    scopes_alerts_enabled: bool = False
    shortcuts: dict[str, list[str]] = field(default_factory=dict)
    # --- Performance (proxies, cache) ---
    proxies_enabled: bool = True
    proxy_profile: str = DEFAULT_PROXY_PROFILE
    cache_max_gb: float = DEFAULT_CACHE_MAX_GB
    # --- Export ---
    export_encoder: str = DEFAULT_EXPORT_ENCODER
    # --- Décodage et rendu de l'aperçu (absents des fichiers antérieurs : Auto) ---
    decode_mode: str = DEFAULT_DECODE_MODE
    preview_backend: str = DEFAULT_PREVIEW_BACKEND
    flow_backend: str = DEFAULT_FLOW_BACKEND
    time_ripple_timeline: bool = DEFAULT_TIME_RIPPLE_TIMELINE
    # --- Mises à jour (absentes des fichiers antérieurs : défauts) ---
    check_updates: bool = DEFAULT_CHECK_UPDATES
    include_prereleases: bool = DEFAULT_INCLUDE_PRERELEASES
    skipped_update_version: str = ""
    last_update_check: float = 0.0
    # --- Vidéo sociale (absents des fichiers antérieurs : défauts) ---
    photo_duration: float = DEFAULT_PHOTO_DURATION
    photo_fill: bool = False
    photo_ken_burns: bool = False
    platform_zones: str = ""


DEFAULT_MASTER_GAIN_DB: float = 0.0
"""Gain Master par défaut (neutre)."""

MAX_MASTER_GAIN_DB: float = 12.0
"""Borne haute du gain Master, alignée sur celle des pistes."""


def _coerce_photo_duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return DEFAULT_PHOTO_DURATION
    return max(PHOTO_DURATION_RANGE[0], min(PHOTO_DURATION_RANGE[1], float(value)))


def _coerce_platform_zones(value: object) -> str:
    from .canvas_guides import PLATFORMS

    return value if isinstance(value, str) and value in PLATFORMS else ""


def _coerce_master_gain(value: object) -> float:
    """Borne un gain Master relu depuis le disque.

    Une valeur absente ou corrompue retombe sur 0 dB : mieux vaut un
    mixage neutre qu'un gain fou au démarrage. Le Master décrit la
    session de l'utilisateur, il ne fait donc **pas** partie du projet
    ``.kut``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_MASTER_GAIN_DB
    number = float(value)
    if number != number:  # NaN
        return DEFAULT_MASTER_GAIN_DB
    from .project_model import MIN_GAIN_DB

    return max(MIN_GAIN_DB, min(MAX_MASTER_GAIN_DB, number))


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


def _coerce_theme_mode(value: object) -> str:
    """Filtre et normalise ``theme_mode`` ; défaut si invalide."""
    if isinstance(value, str) and value in VALID_THEME_MODES:
        return value
    return DEFAULT_THEME


def _coerce_language(value: object) -> str:
    """Filtre et normalise ``language`` ; défaut si invalide."""
    if isinstance(value, str) and value in VALID_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


def _coerce_performance_profile(value: object) -> str:
    """Filtre ``performance_profile`` ; ``auto`` si invalide."""
    if isinstance(value, str) and value in VALID_PERFORMANCE_PROFILES:
        return value
    return DEFAULT_PERFORMANCE_PROFILE


def _coerce_preview_quality(value: object) -> str:
    """Filtre ``preview_quality`` ; ``auto`` si invalide."""
    if isinstance(value, str) and value in VALID_PREVIEW_QUALITIES:
        return value
    return DEFAULT_PREVIEW_QUALITY


def _coerce_render_quality(value: object) -> str:
    """Filtre ``render_quality`` (tache 30) ; ``standard`` si invalide."""
    if isinstance(value, str) and value in VALID_RENDER_QUALITIES:
        return value
    return DEFAULT_RENDER_QUALITY


def _default_settings_dir() -> Path:
    """Détermine le répertoire de configuration à utiliser.

    Priorité :
    1. variable d'environnement ``KUT_STUDIO_CONFIG_DIR`` si définie
       (utile pour les tests et le mode portable) ;
    2. ``~/.config/kut-studio`` sur Linux / XDG_CONFIG_HOME ;
    3. ``~/Library/Application Support/Kut-Studio`` sur macOS ;
    4. ``%APPDATA%/Kut-Studio`` sur Windows ;
    5. dossier temporaire ``.kut-studio`` sous le home en repli.

    Aucune exception : ce helper ne lève jamais en utilisation normale
    (la cible peut toujours être créée).
    """
    return user_config_dir()


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def default_settings_dir() -> Path:
    """Expose le répertoire de configuration calculé (sans le créer)."""
    return _default_settings_dir()


def settings_file_path(settings_dir: str | os.PathLike[str] | None = None) -> Path:
    """Retourne le chemin du fichier de préférences.

    Args:
        settings_dir: Répertoire explicite (ou chaîne). Si ``None``,
            utilise :func:`default_settings_dir`.

    Returns:
        Le chemin absolu vers ``user_settings.json``.
    """
    base = (
        Path(settings_dir)
        if settings_dir is not None
        else _default_settings_dir()
    )
    return Path(base) / FILE_NAME


def load_user_settings(
    settings_dir: str | os.PathLike[str] | None = None,
) -> UserSettings:
    """Charge les préférences utilisateur.

    Règles :

    - fichier absent → :class:`UserSettings` avec les valeurs par
      défaut (``dark``/``fr``) ;
    - JSON invalide → valeurs par défaut ;
    - valeurs hors-domaine → valeur par défaut individuelle.

    Args:
        settings_dir: répertoire explicite (``None`` = répertoire
            standard).

    Returns:
        L'instance :class:`UserSettings` correspondante.
    """
    path = settings_file_path(settings_dir)
    if not path.exists():
        return UserSettings()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return UserSettings()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return UserSettings()
    if not isinstance(data, dict):
        return UserSettings()
    return UserSettings(
        theme_mode=_coerce_theme_mode(data.get("theme_mode")),
        language=_coerce_language(data.get("language")),
        performance_profile=_coerce_performance_profile(data.get("performance_profile")),
        preview_quality=_coerce_preview_quality(data.get("preview_quality")),
        render_quality=_coerce_render_quality(data.get("render_quality")),
        master_gain_db=_coerce_master_gain(data.get("master_gain_db")),
        master_muted=bool(data.get("master_muted", False)),
        # Scopes (tache 31) : absents sur les reglages ecrits avant
        # cette tache, les defauts s'appliquent (4 vues, waveform,
        # niveaux video, alertes desactivees).
        scopes_visible=_coerce_bool(data.get("scopes_visible", True)),
        scopes_layout=_coerce_scopes_layout(data.get("scopes_layout")),
        scopes_view=_coerce_scopes_view(data.get("scopes_view")),
        scopes_levels=_coerce_scopes_levels(data.get("scopes_levels")),
        scopes_alerts_enabled=_coerce_bool(
            data.get("scopes_alerts_enabled", False)
        ),
        # Raccourcis : absents des fichiers antérieurs, ce qui redonne
        # les valeurs par défaut sans migration explicite.
        shortcuts=_coerce_shortcuts(data.get("shortcuts")),
        # Absents des fichiers antérieurs : les défauts s'appliquent.
        proxies_enabled=_coerce_bool(data.get("proxies_enabled", True)),
        proxy_profile=_coerce_proxy_profile(data.get("proxy_profile")),
        cache_max_gb=_coerce_cache_max_gb(data.get("cache_max_gb")),
        export_encoder=_coerce_export_encoder(data.get("export_encoder")),
        decode_mode=_coerce_decode_mode(data.get("decode_mode")),
        preview_backend=_coerce_preview_backend(data.get("preview_backend")),
        flow_backend=_coerce_flow_backend(data.get("flow_backend")),
        time_ripple_timeline=_coerce_bool(data.get("time_ripple_timeline", DEFAULT_TIME_RIPPLE_TIMELINE)),
        check_updates=_coerce_bool(data.get("check_updates", DEFAULT_CHECK_UPDATES)),
        include_prereleases=_coerce_bool(data.get("include_prereleases", DEFAULT_INCLUDE_PRERELEASES)),
        skipped_update_version=_coerce_skipped_version(data.get("skipped_update_version")),
        last_update_check=_coerce_timestamp(data.get("last_update_check")),
        photo_duration=_coerce_photo_duration(data.get("photo_duration")),
        photo_fill=_coerce_bool(data.get("photo_fill", False)),
        photo_ken_burns=_coerce_bool(data.get("photo_ken_burns", False)),
        platform_zones=_coerce_platform_zones(data.get("platform_zones")),
    )


def save_user_settings(
    settings: UserSettings,
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Sauvegarde les préférences utilisateur dans un fichier JSON UTF-8.

    Le dossier est créé s'il n'existe pas ; l'écriture est atomique
    (fichier temporaire + ``os.replace``) : en cas d'erreur en cours
    d'écriture, le fichier cible n'est jamais tronqué.

    Args:
        settings: préférences à enregistrer.
        settings_dir: répertoire cible (``None`` = standard).

    Returns:
        Le chemin effectif du fichier sauvegardé.
    """
    base = (
        Path(settings_dir)
        if settings_dir is not None
        else _default_settings_dir()
    )
    base.mkdir(parents=True, exist_ok=True)
    target = base / FILE_NAME
    payload = asdict(
        UserSettings(
            theme_mode=_coerce_theme_mode(settings.theme_mode),
            language=_coerce_language(settings.language),
            performance_profile=_coerce_performance_profile(settings.performance_profile),
            preview_quality=_coerce_preview_quality(settings.preview_quality),
            render_quality=_coerce_render_quality(settings.render_quality),
            master_gain_db=_coerce_master_gain(settings.master_gain_db),
            master_muted=bool(settings.master_muted),
            # Scopes (tache 31).
            scopes_visible=_coerce_bool(settings.scopes_visible),
            scopes_layout=_coerce_scopes_layout(settings.scopes_layout),
            scopes_view=_coerce_scopes_view(settings.scopes_view),
            scopes_levels=_coerce_scopes_levels(settings.scopes_levels),
            scopes_alerts_enabled=_coerce_bool(
                settings.scopes_alerts_enabled
            ),
            shortcuts=_coerce_shortcuts(settings.shortcuts),
            proxies_enabled=_coerce_bool(settings.proxies_enabled),
            proxy_profile=_coerce_proxy_profile(settings.proxy_profile),
            cache_max_gb=_coerce_cache_max_gb(settings.cache_max_gb),
            export_encoder=_coerce_export_encoder(settings.export_encoder),
            decode_mode=_coerce_decode_mode(settings.decode_mode),
            preview_backend=_coerce_preview_backend(settings.preview_backend),
            flow_backend=_coerce_flow_backend(settings.flow_backend),
            time_ripple_timeline=_coerce_bool(settings.time_ripple_timeline),
            check_updates=_coerce_bool(settings.check_updates),
            include_prereleases=_coerce_bool(settings.include_prereleases),
            skipped_update_version=_coerce_skipped_version(settings.skipped_update_version),
            last_update_check=_coerce_timestamp(settings.last_update_check),
            photo_duration=_coerce_photo_duration(settings.photo_duration),
            photo_fill=_coerce_bool(settings.photo_fill),
            photo_ken_burns=_coerce_bool(settings.photo_ken_burns),
            platform_zones=_coerce_platform_zones(settings.platform_zones),
        )
    )
    atomic_write_json(target, payload)
    return target


__all__ = [
    "DEFAULT_CACHE_MAX_GB",
    "DEFAULT_CHECK_UPDATES",
    "DEFAULT_INCLUDE_PRERELEASES",
    "DEFAULT_DECODE_MODE",
    "DEFAULT_FLOW_BACKEND",
    "DEFAULT_TIME_RIPPLE_TIMELINE",
    "DEFAULT_PREVIEW_BACKEND",
    "DEFAULT_PROXY_PROFILE",
    "DEFAULT_LANGUAGE",
    "DEFAULT_PERFORMANCE_PROFILE",
    "DEFAULT_PREVIEW_QUALITY",
    "DEFAULT_RENDER_QUALITY",
    "DEFAULT_SCOPES_LAYOUT",
    "DEFAULT_SCOPES_LEVELS",
    "DEFAULT_SCOPES_VIEW",
    "DEFAULT_THEME",
    "UserSettings",
    "VALID_LANGUAGES",
    "VALID_PERFORMANCE_PROFILES",
    "VALID_PREVIEW_BACKENDS",
    "VALID_PREVIEW_QUALITIES",
    "VALID_RENDER_QUALITIES",
    "VALID_SCOPES_LAYOUTS",
    "VALID_SCOPES_LEVELS",
    "VALID_SCOPES_VIEWS",
    "VALID_THEME_MODES",
    "default_settings_dir",
    "load_user_settings",
    "save_user_settings",
    "settings_file_path",
]
