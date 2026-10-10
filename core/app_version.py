"""Identité et version de Kut-Studio : **source unique**.

Tout ce qui affiche, compare ou publie la version la lit ici : la recherche de mises à jour, la boîte « À propos »,
``build.py`` (``Info.plist`` macOS) et le workflow de publication, qui refuse un tag ``vX.Y.Z`` différent de
:data:`APP_VERSION` (``python -m tools.release check-tag``). Pour publier une version : changer cette ligne, fusionner,
puis pousser le tag correspondant (voir ``docs/updates.md``).

Le format est SemVer 2.0 (``MAJEUR.MINEUR.CORRECTIF`` et, pour une préversion, ``-beta.1`` par exemple) : il est
validé au chargement par :mod:`core.versioning` (``tests/test_versioning.py``).
"""

from __future__ import annotations

APP_NAME = "Kut-Studio"
APP_VERSION = "0.3.0"
GITHUB_REPOSITORY = "Kyyzen0/Kut-Studio"
"""Dépôt ``propriétaire/nom`` dont les GitHub Releases publient les paquets."""
WEBSITE_URL = "https://kut-studio.pages.dev"

__all__ = ["APP_NAME", "APP_VERSION", "GITHUB_REPOSITORY", "WEBSITE_URL"]
