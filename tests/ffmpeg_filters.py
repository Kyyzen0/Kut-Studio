"""Quels filtres le FFmpeg des tests connaît (ADR-0013 : le FFmpeg de la version App Store est LGPL, sans ``eq``)."""

from __future__ import annotations

import functools
import re
import subprocess


@functools.lru_cache(maxsize=None)
def has_filter(name: str, ffmpeg: str = "ffmpeg") -> bool:
    """Le filtre ``name`` figure-t-il dans ``ffmpeg -filters`` ? ``False`` si FFmpeg est absent ou illisible."""
    try:
        text = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", text, re.MULTILINE) is not None
