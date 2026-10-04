"""Découverte portable des outils FFmpeg, installés ou embarqués."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path


def _tool_filename(name: str, platform_name: str) -> str:
    return f"{name}.exe" if platform_name.startswith("win") else name


def bundled_tool_path(
    name: str,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    bundle_root: str | os.PathLike[str] | None = None,
) -> str | None:
    """Trouve un binaire fourni dans ``bin/`` ou via une variable explicite."""
    platform_name = platform_name or sys.platform
    env = environment if environment is not None else os.environ
    filename = _tool_filename(name, platform_name)
    explicit = env.get(f"KUT_STUDIO_{name.upper()}")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
    roots: list[Path] = []
    configured_dir = env.get("KUT_STUDIO_FFMPEG_DIR")
    if configured_dir:
        roots.append(Path(configured_dir).expanduser())
    if bundle_root is not None:
        roots.append(Path(bundle_root))
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for candidate in (root / "bin" / filename, root / filename):
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def find_media_tool(name: str) -> str | None:
    """Résout d'abord le binaire embarqué, puis le ``PATH`` système."""
    return bundled_tool_path(name) or shutil.which(name)


_CONVENTIONAL_TOOL_DIRS: Mapping[str, tuple[str, ...]] = {
    "darwin": ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"),
    "linux": ("/usr/local/bin", "/usr/bin", "/snap/bin", "/home/linuxbrew/.linuxbrew/bin"),
}
"""Dossiers où les gestionnaires de paquets posent FFmpeg : Homebrew (Apple Silicon, puis Intel), MacPorts ;
sous Linux, le dossier local, Snap et Linuxbrew. Windows n'en a pas besoin : son ``PATH`` est celui du registre."""


def extend_search_path(
    *,
    platform_name: str | None = None,
    environment: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Ajoute à la fin du ``PATH`` les dossiers usuels des outils FFmpeg ; renvoie ceux réellement ajoutés.

    Une application lancée depuis le Dock ou le Finder ne reçoit pas le ``PATH`` du shell de l'utilisateur,
    mais celui de ``launchd`` (``/usr/bin:/bin:/usr/sbin:/sbin``) : un FFmpeg installé avec Homebrew
    (``/opt/homebrew/bin``) y reste invisible alors qu'il répond dans le Terminal. Les dossiers n'étant
    ajoutés qu'à la suite, un ``PATH`` déjà complet (lancement depuis un terminal) garde sa priorité, et
    seuls les dossiers existants sont retenus. Les processus lancés ensuite héritent du ``PATH`` étendu.
    """
    platform_name = platform_name or sys.platform
    env = environment if environment is not None else os.environ
    present = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    added: list[str] = []
    for directory in _CONVENTIONAL_TOOL_DIRS.get(platform_name, ()):
        if directory not in present and os.path.isdir(directory):
            present.append(directory)
            added.append(directory)
    if added:
        env["PATH"] = os.pathsep.join(present)
    return tuple(added)


__all__ = ["bundled_tool_path", "extend_search_path", "find_media_tool"]
