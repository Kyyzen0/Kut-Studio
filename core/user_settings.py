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
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------


VALID_THEME_MODES: tuple[str, ...] = ("dark", "light", "system")
"""Modes de thème reconnus par Kut-Studio."""

VALID_LANGUAGES: tuple[str, ...] = ("fr", "en", "es")
"""Langues disponibles pour l'interface."""

DEFAULT_THEME: str = "dark"
"""Thème par défaut (le plus sûr, confirmé par l'historique de Kut-Studio)."""

DEFAULT_LANGUAGE: str = "fr"
"""Langue par défaut (français)."""

FILE_NAME: str = "user_settings.json"
"""Nom du fichier de préférences à l'intérieur du répertoire de config."""


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserSettings:
    """Préférences utilisateur globales (thème, langue).

    Attributes:
        theme_mode: ``"dark"``, ``"light"`` ou ``"system"``.
        language: code BCP-47 court (``"fr"``, ``"en"``, ``"es"``).
    """

    theme_mode: str = DEFAULT_THEME
    language: str = DEFAULT_LANGUAGE


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


# ``sys`` est importé paresseusement pour rendre la fonction pure en CLI.
import sys  # noqa: E402  - import local pour isoler le helper.


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
        )
    )
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


__all__ = [
    "DEFAULT_LANGUAGE",
    "DEFAULT_THEME",
    "UserSettings",
    "VALID_LANGUAGES",
    "VALID_THEME_MODES",
    "default_settings_dir",
    "load_user_settings",
    "save_user_settings",
    "settings_file_path",
]
