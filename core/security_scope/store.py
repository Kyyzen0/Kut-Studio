"""Les signets persistés, DANS LE CONTENEUR de l'application (ADR-0013, décision D5, 2026-10-29).

Un fichier JSON ``security_scope.json`` dans le dossier de réglages (``user_config_dir()``, donc le conteneur sous macOS) : jamais
dans le projet ``.kut`` (le format du projet ne change pas), jamais dans le trousseau, jamais dans un fichier choisi par
l'utilisateur. Écriture atomique (``core.atomic_io``). Entrées indexées par **chemin canonique** (``realpath``).

Bornes : :data:`MAX_ENTRIES` entrées, :data:`MAX_BOOKMARK_CHARS` caractères par signet, :data:`MAX_FILE_BYTES` octets pour le
fichier, vérifiés avant toute lecture complète. Un fichier trop gros, illisible ou mal formé se lit comme vide (aucun accès) et
n'est PAS réécrit tant qu'on n'ajoute rien. Quand la table est pleine, ou que le fichier sérialisé dépasserait :data:`MAX_FILE_BYTES` (vérifié AVANT d'écrire : l'ancien fichier reste intact), un nouveau signet est refusé (rien n'est évincé en silence :
un signet perdu ferait perdre un média à l'utilisateur).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from ..atomic_io import atomic_write_text

FILE_NAME = "security_scope.json"
FORMAT_VERSION = 1
MAX_ENTRIES = 2048
MAX_BOOKMARK_CHARS = 16384
MAX_PATH_CHARS = 4096
MAX_FILE_BYTES = 12 << 20


def canonical(path: str | os.PathLike[str]) -> str:
    """Chemin canonique d'une entrée (liens résolus, ``..`` normalisés). ``ValueError`` pour un chemin vide ou contenant NUL."""
    text = os.fspath(path)
    if not isinstance(text, str) or not text.strip() or "\0" in text or len(text) > MAX_PATH_CHARS:
        raise ValueError("chemin invalide")
    return os.path.realpath(os.path.expanduser(text))


def covers(parent: str, child: str) -> bool:
    """``parent`` désigne ``child`` ou un de ses ancêtres (comparaison par composants, pas par préfixe de texte)."""
    if parent == child:
        return True
    prefix = parent if parent.endswith(os.sep) else parent + os.sep
    return child.startswith(prefix)


@dataclass(frozen=True)
class BookmarkEntry:
    path: str
    bookmark: str


class BookmarkStore:
    """Table chemin canonique -> signet, relue du disque à chaque accès (le fichier est la source ; peu d'entrées)."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._file = Path(directory) / FILE_NAME
        self._lock = threading.RLock()
        self._cache: tuple[tuple[int, int], dict[str, str]] | None = None      # ((mtime_ns, taille), table) : relue si le fichier change

    # -- lecture ---------------------------------------------------------------------------------------------------------

    def _read(self) -> dict[str, str]:
        try:
            info = self._file.stat()
            if info.st_size > MAX_FILE_BYTES:
                return {}
            stamp = (info.st_mtime_ns, info.st_size)
            if self._cache is not None and self._cache[0] == stamp:
                return dict(self._cache[1])
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = data.get("entries") if isinstance(data, dict) and data.get("version") == FORMAT_VERSION else None
        if not isinstance(entries, dict):
            return {}
        clean: dict[str, str] = {}
        for key, value in entries.items():
            bookmark = value.get("bookmark") if isinstance(value, dict) else None
            if isinstance(key, str) and key and len(key) <= MAX_PATH_CHARS and isinstance(bookmark, str) \
                    and 0 < len(bookmark) <= MAX_BOOKMARK_CHARS:
                clean[key] = bookmark
            if len(clean) >= MAX_ENTRIES:
                break
        self._cache = (stamp, dict(clean))
        return clean

    def _write(self, table: dict[str, str]) -> None:
        payload = {"version": FORMAT_VERSION, "entries": {key: {"bookmark": mark} for key, mark in sorted(table.items())}}
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            # Refus AVANT de toucher au fichier : écrit, il serait relu comme vide (limite de lecture) et toutes les permissions
            # disparaîtraient. L'ancien fichier reste intact ; rien n'est évincé en silence.
            raise ValueError("table des signets trop grande")
        atomic_write_text(self._file, text)
        self._cache = None

    def entries(self) -> list[BookmarkEntry]:
        with self._lock:
            return [BookmarkEntry(key, mark) for key, mark in sorted(self._read().items())]

    def find(self, path: str) -> BookmarkEntry | None:
        """L'entrée qui couvre ``path`` : la plus proche (la plus longue) parmi celles qui le désignent ou le contiennent."""
        try:
            key = canonical(path)
        except ValueError:
            return None
        best: BookmarkEntry | None = None
        for entry in self.entries():
            if covers(entry.path, key) and (best is None or len(entry.path) > len(best.path)):
                best = entry
        return best

    # -- écriture --------------------------------------------------------------------------------------------------------

    def put(self, path: str, bookmark: str) -> bool:
        """Enregistre ou remplace le signet de ``path`` ; ``False`` (rien d'écrit) si invalide, trop gros, table pleine ou disque refusé."""
        if not isinstance(bookmark, str) or not 0 < len(bookmark) <= MAX_BOOKMARK_CHARS:
            return False
        try:
            key = canonical(path)
        except ValueError:
            return False
        with self._lock:
            table = self._read()
            if key not in table and len(table) >= MAX_ENTRIES:
                return False
            table[key] = bookmark
            try:
                self._write(table)
            except (OSError, ValueError):
                return False
        return True

    def remove(self, path: str) -> bool:
        try:
            key = canonical(path)
        except ValueError:
            return False
        with self._lock:
            table = self._read()
            if key not in table:
                return False
            del table[key]
            try:
                self._write(table)
            except (OSError, ValueError):
                return False
        return True


__all__ = [
    "FILE_NAME", "MAX_BOOKMARK_CHARS", "MAX_ENTRIES", "MAX_FILE_BYTES", "BookmarkEntry", "BookmarkStore", "canonical", "covers",
]
