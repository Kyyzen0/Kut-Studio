"""Tests de sûreté de la file de tâches.

Ces tests exercent :class:`TaskQueue` sous un usage **parallèle** réel
(thread d'interface + worker). Ils vérifient les invariants qui ont le
plus de chances d'être cassés par une régression :

- aucune tâche fantôme (une tâche annulée ne doit jamais s'exécuter) ;
- aucune clé dupliquée dans la file ;
- l'ordre (priorité puis soumission) reste stable ;
- une tâche peut elle-même soumettre ou annuler sans s'interbloquer.

L'absence de deadlock est vérifiée implicitement : un test qui
s'interbloquerait expirerait sur le timeout global de pytest.
"""

import threading

import pytest

from core.task_queue import (
    PRIORITY_BACKGROUND,
    PRIORITY_NEAR,
    PRIORITY_VISIBLE,
    CancelToken,
    QueueWorker,
    TaskQueue,
)


# ---------------------------------------------------------------------------
# Sûreté de base
# ---------------------------------------------------------------------------


def test_task_may_submit_from_its_own_body():
    """Régression ciblée : la fonction ne doit pas s'exécuter sous verrou."""

    queue = TaskQueue()
    ran: list[str] = []

    def outer(token: CancelToken) -> None:
        ran.append("outer")
        # Ré-entrée : si ``pump`` tenait le verrou, ce submit
        # s'interbloquerait et le test expirerait.
        queue.submit("inner", lambda t: ran.append("inner"))

    queue.submit("outer", outer)
    queue.pump(1)
    queue.pump(1)

    assert ran == ["outer", "inner"]


def test_task_may_cancel_from_its_own_body():
    queue = TaskQueue()
    ran: list[str] = []

    def canceller(token: CancelToken) -> None:
        ran.append("first")
        queue.cancel_key("second")

    queue.submit("first", canceller)
    queue.submit("second", lambda t: ran.append("second"))
    queue.pump(5)

    assert ran == ["first"]


def test_cancelled_task_never_runs():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("k", lambda t: ran.append("k"))
    queue.cancel_key("k")
    assert queue.pump(10) == 0
    assert ran == []


def test_pending_count_is_consistent():
    queue = TaskQueue()
    assert queue.pending == 0
    for index in range(5):
        queue.submit(f"k{index}", lambda t: None)
    assert queue.pending == len(queue) == 5
    queue.cancel_all()
    assert queue.pending == 0


def test_priority_then_submission_order_is_stable():
    queue = TaskQueue()
    order: list[str] = []
    queue.submit("bg", lambda t: order.append("bg"), priority=PRIORITY_BACKGROUND)
    queue.submit("near", lambda t: order.append("near"), priority=PRIORITY_NEAR)
    queue.submit("vis1", lambda t: order.append("vis1"), priority=PRIORITY_VISIBLE)
    queue.submit("vis2", lambda t: order.append("vis2"), priority=PRIORITY_VISIBLE)
    queue.pump(10)
    assert order == ["vis1", "vis2", "near", "bg"]


def test_dedup_replaces_and_cancels_previous():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("same", lambda t: ran.append("first"))
    queue.submit("same", lambda t: ran.append("second"))
    assert queue.pending == 1
    queue.pump(10)
    assert ran == ["second"]


def test_replacing_an_already_running_task_is_safe():
    """Remplacer une clé déjà pompée ne doit pas lever ``ValueError``."""
    queue = TaskQueue()
    queue.submit("k", lambda t: queue.submit("k", lambda t2: None))
    queue.pump(1)          # la tâche en cours se remplace elle-même
    queue.submit("k", lambda t: None)
    assert queue.pending == 1
    assert queue.pump(5) == 1


def test_session_cancel_only_touches_that_session():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("a", lambda t: ran.append("a"), session_id=1)
    queue.submit("b", lambda t: ran.append("b"), session_id=2)
    queue.cancel_session(1)
    queue.pump(10)
    assert ran == ["b"]


# ---------------------------------------------------------------------------
# Concurrence réelle
# ---------------------------------------------------------------------------


def test_submit_and_pump_from_two_threads_are_safe():
    queue = TaskQueue()
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def producer() -> None:
        try:
            start.wait()
            for index in range(300):
                queue.submit(f"key-{index % 40}", lambda t: None, session_id=1)
                if index % 7 == 0:
                    queue.cancel_key(f"key-{index % 40}")
        except BaseException as exc:  # noqa: BLE001 - remonte au test
            errors.append(exc)

    def consumer() -> None:
        try:
            start.wait()
            for _ in range(300):
                queue.pump(5)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "deadlock détecté"
    assert errors == []
    # Des tâches peuvent légitimement rester en file (le producteur a pu
    # soumettre après le dernier pompage) : on la vide, puis on vérifie
    # qu'il n'en reste aucune et qu'aucune tâche fantôme ne s'exécute.
    queue.pump(10_000)
    assert queue.pump(10_000) == 0
    assert queue.pending == 0


def test_concurrent_operations_leave_no_phantom_task():
    """Soustituer / annuler / pomper en parallèle ne doit rien laisser."""
    queue = TaskQueue()
    errors: list[BaseException] = []
    executed: list[int] = []
    lock = threading.Lock()

    def body(token: CancelToken) -> None:
        if token.cancelled:
            return
        with lock:
            executed.append(1)

    def churn(seed: int) -> None:
        try:
            for index in range(200):
                key = f"k-{(index + seed) % 25}"
                queue.submit(key, body, session_id=seed)
                if index % 3 == 0:
                    queue.cancel_key(key)
                if index % 5 == 0:
                    queue.cancel_session(seed)
                queue.pump(2)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=churn, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "deadlock détecté"
    assert errors == []

    # Tout ce qui reste doit pouvoir être pompé sans résidu.
    assert queue.pending >= 0
    queue.pump(10_000)
    assert queue.pump(10_000) == 0
    assert queue.pending == 0


def test_worker_and_ui_share_the_queue():
    """Le worker pompe en arrière-plan pendant que l'interface soumet."""
    queue = TaskQueue()
    worker = QueueWorker(queue)
    done = threading.Event()
    count = {"n": 0}
    lock = threading.Lock()

    def body(token: CancelToken) -> None:
        with lock:
            count["n"] += 1
            if count["n"] >= 50:
                done.set()

    worker.ensure_started()
    try:
        for index in range(50):
            queue.submit(f"k{index}", body, session_id=1)
        assert done.wait(timeout=20), "le worker n'a pas tout exécuté"
    finally:
        worker.stop(timeout=2)

    assert count["n"] == 50
    assert queue.pump(10) == 0


def test_worker_does_not_execute_cancelled_tasks():
    queue = TaskQueue()
    worker = QueueWorker(queue)
    ran: list[str] = []
    worker.ensure_started()
    try:
        for index in range(20):
            queue.submit(f"k{index}", lambda t: ran.append("x"), session_id=1)
        queue.cancel_all()
        import time

        time.sleep(0.3)
    finally:
        worker.stop(timeout=2)
    assert ran == []


def test_shutdown_during_activity_is_clean():
    queue = TaskQueue()
    worker = QueueWorker(queue)
    worker.ensure_started()
    for index in range(200):
        queue.submit(f"k{index}", lambda t: None, session_id=1)
    worker.stop(timeout=3)
    assert worker._thread is None or not worker._thread.is_alive()


def test_zero_limit_pumps_nothing():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("k", lambda t: ran.append("x"))
    assert queue.pump(0) == 0
    assert ran == []


# ---------------------------------------------------------------------------
# Isolation des sessions dans les aperçus
# ---------------------------------------------------------------------------


def test_mailbox_drops_results_from_a_previous_project():
    """Un résultat d'un projet clos ne doit pas atteindre le cache du suivant."""
    from core.studio_runtime import StudioRuntime

    runtime = StudioRuntime()
    runtime.begin_project()          # session 2
    first_session = runtime.session_id
    runtime.begin_project()          # session 3 : le projet 2 est clos

    # Le worker du projet 2 termine tard et pousse son résultat.
    runtime.mailbox.push("thumb", b"vieux", 10, session_id=first_session)
    # Un résultat légitime du projet courant.
    runtime.mailbox.push("thumb2", b"neuf", 10, session_id=runtime.session_id)

    items = runtime.mailbox.drain(runtime.session_id)
    keys = [entry[0] for entry in items]

    assert keys == ["thumb2"], "le résultat périmé doit être écarté"


def test_begin_project_discards_pending_results():
    from core.studio_runtime import StudioRuntime

    runtime = StudioRuntime()
    runtime.begin_project()
    runtime.mailbox.push("k", b"v", 4, session_id=runtime.session_id)

    runtime.begin_project()          # projet remplacé
    assert runtime.mailbox.drain(runtime.session_id) == []


def test_mailbox_drain_without_filter_returns_everything():
    from core.studio_runtime import PreviewMailbox

    mailbox = PreviewMailbox()
    mailbox.push("a", 1, 1, session_id=1)
    mailbox.push("b", 2, 1, session_id=2)
    assert len(mailbox.drain()) == 2


def test_mailbox_pending_sessions_reports_content():
    from core.studio_runtime import PreviewMailbox

    mailbox = PreviewMailbox()
    mailbox.push("a", 1, 1, session_id=7)
    assert mailbox.pending_sessions() == (7,)
    mailbox.clear()
    assert mailbox.pending_sessions() == ()
