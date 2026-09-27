"""File de tâches annulable, sans thread imposé.

Les travaux lourds (miniatures, formes d'onde, analyse) ne doivent pas
se lancer en double ni continuer après la fermeture d'un projet. Cette
file dédoublonne par clé, ordonne par priorité et rattache chaque tâche
à une session. Changer de session annule ce qui traîne.

:meth:`pump` exécute les tâches sur l'appelant. L'interface peut donc
en traiter quelques-unes entre deux événements, et un test peut tout
vider de façon déterministe. Rien n'est envoyé dans un thread ici :
un travail qui touche Qt doit rester sur le thread de l'interface, un
travail pur peut être pompé par un worker plus tard.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable


# Plus petit nombre = plus urgent. Les vignettes à l'écran passent
# avant celles qui sont loin dans la timeline.
PRIORITY_VISIBLE = 0
PRIORITY_NEAR = 50
PRIORITY_BACKGROUND = 100


@dataclass
class CancelToken:
    """Drapeau lu par une tâche pour s'arrêter d'elle-même."""

    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass(order=True)
class _QueuedTask:
    priority: int
    sequence: int
    key: str = field(compare=False)
    session_id: int = field(compare=False)
    fn: Callable[[CancelToken], object] = field(compare=False)
    token: CancelToken = field(compare=False)


class TaskQueue:
    """File en mémoire, dédoublonnée et annulable par session.

    Sure pour un usage parallèle : le thread d'interface soumet et
    annule, pendant que :class:`QueueWorker` pompe. Un verrou protège
    les structures internes, mais **la fonction d'une tâche n'est
    jamais exécutée sous le verrou** — sinon une tâche qui soumettrait
    ou annulerait une autre tâche depuis son propre corps
    s'interbloquerait.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: list[_QueuedTask] = []
        self._by_key: dict[str, _QueuedTask] = {}
        self._sequence = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._tasks)

    def submit(
        self,
        key: str,
        fn: Callable[[CancelToken], object],
        *,
        priority: int = PRIORITY_BACKGROUND,
        session_id: int = 0,
    ) -> CancelToken:
        """Planifie ``fn``. Une tâche de même clé encore en attente est remplacée."""
        token = CancelToken()
        with self._lock:
            previous = self._by_key.get(key)
            if previous is not None:
                # La tâche remplacée est annulée *avant* d'être retirée :
                # si elle est déjà en cours d'exécution côté worker, elle
                # verra son jeton et s'arrêtera d'elle-même.
                previous.token.cancel()
                try:
                    self._tasks.remove(previous)
                except ValueError:
                    # Déjà pompée par le worker : rien à retirer.
                    pass
            self._sequence += 1
            task = _QueuedTask(
                priority=int(priority),
                sequence=self._sequence,
                key=key,
                session_id=session_id,
                fn=fn,
                token=token,
            )
            self._tasks.append(task)
            self._by_key[key] = task
        return token

    def cancel_key(self, key: str) -> None:
        with self._lock:
            task = self._by_key.pop(key, None)
            if task is None:
                return
            task.token.cancel()
            try:
                self._tasks.remove(task)
            except ValueError:
                pass

    def cancel_session(self, session_id: int) -> None:
        """Annule les tâches d'une session, typiquement un projet fermé."""
        with self._lock:
            victims = [
                task for task in self._tasks if task.session_id == session_id
            ]
            for task in victims:
                task.token.cancel()
                self._by_key.pop(task.key, None)
                try:
                    self._tasks.remove(task)
                except ValueError:
                    pass

    def cancel_all(self) -> None:
        with self._lock:
            for task in self._tasks:
                task.token.cancel()
            self._tasks.clear()
            self._by_key.clear()

    def _pop_next(self) -> _QueuedTask | None:
        """Retire et retourne la prochaine tâche valide, sous verrou."""
        with self._lock:
            if not self._tasks:
                return None
            self._tasks.sort()
            task = self._tasks.pop(0)
            # On ne libère la clé que si elle désigne encore cette tâche :
            # un ``submit`` concurrent a pu la remplacer.
            if self._by_key.get(task.key) is task:
                self._by_key.pop(task.key, None)
            return task

    def pump(self, limit: int = 1) -> int:
        """Exécute au plus ``limit`` tâches encore valides.

        Les tâches déjà annulées sont retirées sans être appelées.
        L'ordre est la priorité, puis l'ordre de soumission.

        La fonction d'une tâche est appelée **hors verrou** : elle peut
        donc soumettre, annuler ou annuler une session sans bloquer.
        """
        if limit <= 0:
            return 0
        ran = 0
        while ran < limit:
            task = self._pop_next()
            if task is None:
                break
            if task.token.cancelled:
                continue
            # Exécution délibérément hors du verrou.
            task.fn(task.token)
            ran += 1
        return ran


class QueueWorker:
    """Exécute la file hors du thread d'interface.

    Le worker ne démarre qu'au premier besoin. Les tâches doivent être
    pures : pas d'appel Qt. L'interface récupère les résultats par un
    autre canal (boîte aux lettres du runtime).
    """

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="kut-tasks",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            ran = self._queue.pump(1)
            if ran == 0:
                self._stop.wait(0.05)
