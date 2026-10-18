"""Arrêt d'un processus enfant **et de ses descendants**.

Sous Windows, ``Popen.kill()`` (``TerminateProcess``) ne tue que le processus lancé. Or FFmpeg installé par Chocolatey est un
*shim* (``ShimGen`` : ``C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe``) qui lance le vrai ``ffmpeg.exe`` en enfant et l'attend :
tuer le shim laisse le vrai FFmpeg encoder et garder sa sortie ouverte, d'où un ``[WinError 32]`` à la suppression du fichier
intermédiaire d'un export annulé. ``taskkill /T /F`` parcourt l'arbre depuis le parent encore vivant ; on le lance donc **avant**
de tuer le parent. Partout ailleurs (POSIX : l'enfant rejoint le groupe du gardien, FFmpeg n'a pas d'intermédiaire), un simple
``kill()`` suffit.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from .process_supervisor import supervised_run

LOGGER = logging.getLogger("kut_studio.process")

_TASKKILL_TIMEOUT = 10.0


def kill_process_tree(
    process: subprocess.Popen[Any],
    *,
    windows: bool | None = None,
    run: Callable[..., Any] | None = None,
) -> None:
    """Tue ``process`` et, sous Windows, tous ses descendants. Ne lève jamais ; l'appelant attend ensuite ``process.wait()``.

    ``windows`` et ``run`` ne servent qu'aux tests (plateforme simulée, faux ``taskkill``).
    """
    is_windows = sys.platform.startswith("win") if windows is None else windows
    if is_windows and process.poll() is None:
        launcher = run if run is not None else supervised_run
        try:
            launcher(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_TASKKILL_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            LOGGER.warning("taskkill impossible pour le processus %s : %s", process.pid, error)
    try:
        process.kill()
    except OSError:
        pass


__all__ = ["kill_process_tree"]
