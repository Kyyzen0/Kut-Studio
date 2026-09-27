"""Sauvegarde automatique hors du thread d'interface.

L'autosave réécrit encore le projet entier : le JSON d'un montage ne
contient que des métadonnées, pas les médias. Pour quelques milliers
de clips, construire ce dictionnaire reste court. L'écriture disque,
elle, est déléguée à un thread pour ne pas figer l'interface si le
disque répond lentement.

Ce n'est pas une sauvegarde incrémentale. Le fichier produit est un
``.kut`` complet, dans un voisin ``nom.kut.autosave``. Le jour où le
projet sera assez gros pour que la sérialisation elle-même se voie,
il faudra remplacer :func:`core.project_io.project_payload` par un
delta. Le coordinateur n'aura pas à changer : il écrit déjà un
dictionnaire préparé à l'avance, jamais l'objet projet vivant.

Un seul thread tourne. Une nouvelle demande remplace la précédente
encore en attente, donc deux frappes rapprochées ne lancent pas deux
écritures concurrentes du même fichier.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .project_io import project_payload, write_project_payload
from .project_model import Project


def sidecar_path(project_path: str) -> Path:
    """Chemin de récupération associé à un ``.kut``."""
    return Path(str(project_path) + ".autosave")


def autosave_is_newer(project_path: str) -> bool:
    """Vrai si un autosave existe et qu'il est plus récent que le ``.kut``."""
    project = Path(project_path)
    sidecar = sidecar_path(project_path)
    if not project.is_file() or not sidecar.is_file():
        return False
    try:
        return sidecar.stat().st_mtime > project.stat().st_mtime
    except OSError:
        return False


def discard_autosave(project_path: str) -> None:
    """Supprime le voisin d'autosave. Ignore une absence."""
    try:
        sidecar_path(project_path).unlink()
    except OSError:
        pass


class AutosaveCoordinator:
    """Écrit le dernier payload soumis, puis s'arrête quand on le ferme."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: tuple[int, dict, str] | None = None
        self._generation = 0
        self._closed = False
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.writes = 0

    def submit(self, project: Project, project_path: str) -> None:
        """Sérialise ``project`` tout de suite, écrit ``project_path`` plus tard.

        La sérialisation reste sur l'appelant pour photographier l'état
        avant la prochaine édition. Le thread ne voit qu'un dictionnaire.
        """
        payload = project_payload(project)
        target = str(sidecar_path(project_path))
        with self._condition:
            self._generation += 1
            self._pending = (self._generation, payload, target)
            self._ensure_thread()
            self._condition.notify()

    def close(self, timeout: float = 1.0) -> None:
        """Demande l'arrêt et attend un court instant la dernière écriture."""
        with self._condition:
            self._closed = True
            self._condition.notify()
            thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._closed = False
        self._thread = threading.Thread(
            target=self._loop,
            name="kut-autosave",
            daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._pending is None and self._closed:
                    return
                generation, payload, target = self._pending
                self._pending = None
            try:
                write_project_payload(payload, target)
            except OSError as exc:
                self.last_error = str(exc)
                continue
            with self._condition:
                # Une demande plus récente a déjà remplacé le fichier,
                # ou s'apprête à le faire : on ne compte que l'écriture
                # qui correspond encore à la dernière génération.
                if generation == self._generation:
                    self.writes += 1
                    self.last_error = None
