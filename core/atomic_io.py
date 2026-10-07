"""Écriture atomique de fichiers : un fichier temporaire du même dossier, ``fsync``, puis ``os.replace``.

Une erreur en cours d'écriture (disque plein, exception d'encodage, coupure) ne laisse jamais la cible tronquée :
soit l'ancien contenu reste en place, soit le nouveau la remplace en entier. Le temporaire est créé dans le dossier
de la cible pour que ``os.replace`` reste un renommage sur le même volume, donc atomique.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8") -> Path:
    """Écrit ``content`` dans ``path`` de façon atomique et retourne le chemin ; le dossier parent est créé."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return target
