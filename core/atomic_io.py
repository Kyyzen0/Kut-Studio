"""Écriture atomique de fichiers : un fichier temporaire du même dossier, puis ``os.replace``.

Une erreur en cours d'écriture (disque plein, exception d'encodage ou de sérialisation, coupure) ne laisse jamais la
cible tronquée : soit l'ancien contenu reste en place, soit le nouveau la remplace en entier. Le temporaire est créé
dans le dossier de la cible pour que ``os.replace`` reste un renommage sur le même volume, donc atomique ; son nom est
unique (``mkstemp``) : deux processus qui écrivent la même cible ne se marchent pas dessus.

``durable=True`` (défaut) force aussi le contenu sur le disque (``fsync``) avant le renommage : un réglage ou un projet
survit à une coupure de courant. Un cache, qu'on sait recalculer, passe ``durable=False`` : il reste atomique (jamais lu
à moitié écrit) sans payer un ``fsync`` par fichier.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any


@contextmanager
def atomic_open(
    path: str | os.PathLike[str], mode: str = "w", *, encoding: str = "utf-8", durable: bool = True
) -> Iterator[IO[Any]]:
    """Fichier temporaire ouvert en ``mode`` (``"w"`` ou ``"wb"``), qui remplace ``path`` à la sortie sans erreur.

    Le dossier parent est créé. Une exception dans le bloc supprime le temporaire et laisse ``path`` intact.
    """
    if mode not in {"w", "wb"}:
        raise ValueError(f"Mode d'écriture atomique non pris en charge : {mode!r}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, mode, encoding=None if mode == "wb" else encoding) as handle:
            yield handle
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(
    path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8", durable: bool = True
) -> Path:
    """Écrit ``content`` dans ``path`` de façon atomique et retourne le chemin ; le dossier parent est créé."""
    with atomic_open(path, "w", encoding=encoding, durable=durable) as handle:
        handle.write(content)
    return Path(path)


def atomic_write_json(path: str | os.PathLike[str], payload: Any, *, durable: bool = True, **dumps: Any) -> Path:
    """Sérialise ``payload`` puis l'écrit de façon atomique (``indent=2``, accents gardés, sauf ``dumps`` contraire).

    La sérialisation a lieu avant d'ouvrir le temporaire : une valeur non sérialisable ne crée aucun fichier.
    """
    options: dict[str, Any] = {"indent": 2, "ensure_ascii": False, **dumps}
    return atomic_write_text(path, json.dumps(payload, **options), durable=durable)
