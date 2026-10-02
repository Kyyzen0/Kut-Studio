"""Emplacement et nom proposés par défaut pour un export ou un enregistrement (fonctions pures)."""

from __future__ import annotations

import os
import re

FALLBACK_NAME = "kut-studio-export"
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def default_export_directory(home: str | None = None) -> str:
    """Premier dossier vidéo de l'utilisateur qui existe (``Movies``, ``Videos``), sinon son dossier personnel.

    Rien n'est créé : un ``$HOME`` en lecture seule ou un dossier absent (``~/Movies`` n'existe pas sous
    Linux ni Windows) ne doit pas faire échouer l'ouverture de la boîte « Enregistrer ».
    """
    base = home or os.path.expanduser("~")
    for name in ("Movies", "Videos"):
        candidate = os.path.join(base, name)
        if os.path.isdir(candidate):
            return candidate
    return base


def export_file_name(project_name: str | None, container: str, *, fallback: str = FALLBACK_NAME) -> str:
    """Nom de fichier valide sur toutes les plateformes à partir du nom du projet (``container`` = extension)."""
    cleaned = _FORBIDDEN.sub("_", (project_name or "").strip()).strip(" .")
    return f"{cleaned or fallback}.{container}"
