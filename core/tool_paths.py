"""Découverte portable des outils FFmpeg, installés ou embarqués."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
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


__all__ = ["bundled_tool_path", "find_media_tool"]
