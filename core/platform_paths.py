"""Chemins système portables utilisés par Kut-Studio.

La logique vit ici plutôt que dans chaque bibliothèque de presets. Les
paramètres optionnels rendent les trois plateformes testables depuis une seule
machine, sans falsifier globalement ``os.name`` ou ``sys.platform``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def _context(
    *,
    platform_name: str | None,
    environment: Mapping[str, str] | None,
    home: str | os.PathLike[str] | None,
) -> tuple[str, Mapping[str, str], Path]:
    return (
        platform_name or sys.platform,
        environment if environment is not None else os.environ,
        Path(home) if home is not None else Path.home(),
    )


def user_config_dir(
    custom: str | os.PathLike[str] | None = None,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire de configuration natif, sans le créer."""
    if custom is not None:
        return Path(custom)
    platform_name, env, home_path = _context(
        platform_name=platform_name, environment=environment, home=home
    )
    override = env.get("KUT_STUDIO_CONFIG_DIR")
    if override:
        return Path(override)
    if platform_name.startswith("win"):
        return Path(env.get("APPDATA", home_path / "AppData" / "Roaming")) / "Kut-Studio"
    if platform_name == "darwin":
        return home_path / "Library" / "Application Support" / "Kut-Studio"
    return Path(env.get("XDG_CONFIG_HOME", home_path / ".config")) / "kut-studio"


def user_cache_dir(
    custom: str | os.PathLike[str] | None = None,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire de cache natif, sans le créer."""
    if custom is not None:
        return Path(custom)
    platform_name, env, home_path = _context(
        platform_name=platform_name, environment=environment, home=home
    )
    override = env.get("KUT_STUDIO_CACHE_DIR")
    if override:
        return Path(override)
    if platform_name.startswith("win"):
        base = Path(env.get("LOCALAPPDATA", home_path / "AppData" / "Local"))
        return base / "Kut-Studio" / "Cache"
    if platform_name == "darwin":
        return home_path / "Library" / "Caches" / "Kut-Studio"
    return Path(env.get("XDG_CACHE_HOME", home_path / ".cache")) / "kut-studio"


def user_data_dir(
    custom: str | os.PathLike[str] | None = None,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire des **données** de l'utilisateur (bibliothèque de SFX…), sans le créer.

    À la différence du cache, son contenu est référencé par les projets : il n'est jamais purgé. Ce qui y est produit
    reste recalculable (un SFX synthétisé se régénère à l'identique), mais un projet ne doit pas le perdre en route."""
    if custom is not None:
        return Path(custom)
    platform_name, env, home_path = _context(
        platform_name=platform_name, environment=environment, home=home
    )
    override = env.get("KUT_STUDIO_DATA_DIR")
    if override:
        return Path(override)
    if platform_name.startswith("win"):
        return Path(env.get("APPDATA", home_path / "AppData" / "Roaming")) / "Kut-Studio" / "Data"
    if platform_name == "darwin":
        return home_path / "Library" / "Application Support" / "Kut-Studio" / "Data"
    return Path(env.get("XDG_DATA_HOME", home_path / ".local" / "share")) / "kut-studio"


def user_log_dir(
    custom: str | os.PathLike[str] | None = None,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Répertoire des journaux natif, sans le créer."""
    if custom is not None:
        return Path(custom)
    platform_name, env, home_path = _context(
        platform_name=platform_name, environment=environment, home=home
    )
    override = env.get("KUT_STUDIO_LOG_DIR")
    if override:
        return Path(override)
    if platform_name.startswith("win"):
        base = Path(env.get("LOCALAPPDATA", home_path / "AppData" / "Local"))
        return base / "Kut-Studio" / "Logs"
    if platform_name == "darwin":
        return home_path / "Library" / "Logs" / "Kut-Studio"
    return Path(env.get("XDG_STATE_HOME", home_path / ".local" / "state")) / "kut-studio"


def system_font_dirs(
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> tuple[Path, ...]:
    """Dossiers de polices usuels, ordonnés du plus spécifique au repli."""
    platform_name, env, home_path = _context(
        platform_name=platform_name, environment=environment, home=home
    )
    if platform_name.startswith("win"):
        windows = Path(env.get("WINDIR", "C:/Windows"))
        local = Path(env.get("LOCALAPPDATA", home_path / "AppData" / "Local"))
        return (windows / "Fonts", local / "Microsoft" / "Windows" / "Fonts")
    if platform_name == "darwin":
        return (
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            home_path / "Library" / "Fonts",
        )
    return (
        home_path / ".local" / "share" / "fonts",
        home_path / ".fonts",
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts"),
    )


def bundled_assets_dir(*parts: str) -> Path:
    """Dossier ``assets/<parts>`` livré avec l'application : celui du dépôt, ou celui de l'application construite
    (PyInstaller le décompresse sous ``sys._MEIPASS``). Shaders, polices embarquées…"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle, "assets", *parts)
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent.parent.joinpath("assets", *parts)


__all__ = [
    "bundled_assets_dir", "system_font_dirs", "user_cache_dir", "user_config_dir", "user_data_dir", "user_log_dir",
]
