"""Génère l'icône Windows ``assets/icons/icon.ico`` à partir de ``assets/icons/icon-light.png``.

Outil de développement, lancé à la main quand l'icône change ; le ``.ico`` produit est versionné et ``build.py`` le
passe à PyInstaller. Pillow n'est **pas** une dépendance du projet (ni ``requirements.txt`` ni
``requirements-dev.txt``) : on l'installe une fois, dans un environnement jetable de préférence ::

    python -m pip install pillow
    python -m tools.make_icons
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "assets" / "icons"
SOURCE = ICONS / "icon-light.png"
TARGET = ICONS / "icon.ico"
ICO_SIZES = (256, 128, 64, 48, 32, 16)
"""Tailles embarquées dans le ``.ico`` : 256 pour l'Explorateur en grandes icônes, 16 pour la barre de titre."""


def make_ico(source: Path = SOURCE, target: Path = TARGET) -> Path:
    """Écrit un ``.ico`` multi-résolutions ; chaque taille est réduite depuis l'image source (LANCZOS)."""
    try:
        from PIL import Image
    except ImportError as error:
        raise SystemExit("Pillow est requis : python -m pip install pillow") from error
    with Image.open(source) as image:
        if image.width != image.height or image.width < max(ICO_SIZES):
            raise SystemExit(f"{source} doit être carrée et faire au moins {max(ICO_SIZES)} px de côté.")
        image.convert("RGBA").save(target, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
    return target


def main() -> int:
    print(make_ico())
    return 0


if __name__ == "__main__":
    sys.exit(main())
