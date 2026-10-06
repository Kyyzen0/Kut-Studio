"""Polices livrées avec Kut-Studio (``assets/fonts``, licence SIL OFL 1.1).

Les templates « Night » et les presets de texte vertical reposent sur des polices condensées (Anton, Saira
ExtraCondensed). Installées sur une machine et absentes d'une autre, elles donnaient deux rendus différents du même
projet : le rastériseur se rabattait en silence sur la police système. Embarquées, elles sont enregistrées auprès de Qt
**avant** toute résolution de famille (:func:`core.mograph_raster.resolve_family` appelle :func:`register_bundled_fonts`),
dans l'application comme dans un export sans interface ou un test.

La licence (``assets/fonts/OFL.txt``) accompagne les fichiers, qui sont les polices d'origine, non modifiées.
"""

from __future__ import annotations

import logging

from .platform_paths import bundled_assets_dir

LOGGER = logging.getLogger("kut_studio.fonts")

BUNDLED_FONT_FILES = (
    "Anton-Regular.ttf",
    "SairaExtraCondensed-Regular.ttf",
    "SairaExtraCondensed-SemiBold.ttf",
    "SairaExtraCondensed-Bold.ttf",
    "SairaExtraCondensed-Black.ttf",
)

BUNDLED_FAMILIES = ("Anton", "Saira ExtraCondensed")
"""Familles garanties par les polices embarquées (les graisses SemiBold et Black ont leur propre famille Qt)."""

_registered: tuple[str, ...] | None = None


def register_bundled_fonts() -> tuple[str, ...]:
    """Enregistre les polices embarquées (une fois par processus) ; retourne les familles obtenues.

    Exige une ``QGuiApplication``. Un fichier manquant ou illisible est journalisé et ignoré : le rendu se rabat alors
    sur la police système, comme pour toute famille absente.
    """
    global _registered
    if _registered is not None:
        return _registered
    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    directory = bundled_assets_dir("fonts")
    for name in BUNDLED_FONT_FILES:
        path = directory / name
        font_id = QFontDatabase.addApplicationFont(str(path)) if path.is_file() else -1
        if font_id < 0:
            LOGGER.warning("Police embarquée illisible ou absente : %s", path)
            continue
        families.extend(family for family in QFontDatabase.applicationFontFamilies(font_id) if family not in families)
    _registered = tuple(families)
    return _registered


__all__ = ["BUNDLED_FAMILIES", "BUNDLED_FONT_FILES", "register_bundled_fonts"]
