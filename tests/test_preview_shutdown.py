"""Fermer la fenêtre pendant le rendu d'un segment d'aperçu (CI Linux, run 37667588029 : erreur de segmentation).

Le thread de rendu (``_PreviewPump``) était seulement prié de s'arrêter : un segment en cours finissait pendant la
destruction de la fenêtre, puis notifiait l'écouteur de la fenêtre (``_preview_events.state.emit``), un objet Qt
déjà détruit. Reproduction déterministe : un faux ``pump`` reste « dans un segment » jusqu'à l'annulation des rendus
(comme un vrai FFmpeg tué par ``cancel_all``), puis notifie en relevant qui est encore abonné.
"""

from __future__ import annotations

import threading

from main_window_harness import build_window, install_dialogs


def test_closing_waits_for_the_render_thread_and_never_notifies_the_closed_window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    engine = window.preview_engine
    assert engine is not None
    window_listeners = list(engine._listeners)               # le moteur appartient à la fenêtre : ses écouteurs
    assert window_listeners
    inside, released = threading.Event(), threading.Event()
    subscribed_at_notify: list[list] = []
    real_cancel_all = engine.cancel_all

    def slow_pump(limit=1):
        inside.set()
        released.wait(5)                                   # un segment de FFmpeg en cours
        with engine._lock:
            subscribed_at_notify.append(list(engine._listeners))
        engine._notify()

    def cancel_all():
        released.set()                                     # comme le vrai : le FFmpeg en cours est tué
        real_cancel_all()

    monkeypatch.setattr(engine, "pump", slow_pump)
    monkeypatch.setattr(engine, "cancel_all", cancel_all)
    window._preview_pump.kick()
    assert inside.wait(2), "le thread de rendu est bien dans un segment"

    window.close()

    assert not window._preview_pump._thread.is_alive(), "la fermeture attend la fin du thread de rendu"
    assert subscribed_at_notify, "le segment interrompu a bien notifié"
    still_subscribed = [cb for cb in subscribed_at_notify[-1] if any(cb is mine for mine in window_listeners)]
    assert still_subscribed == [], "la fenêtre fermée n'est plus abonnée quand le segment se termine"


def test_unsubscribe_removes_only_that_listener():
    from core.preview_engine import PreviewEngine

    engine = PreviewEngine.__new__(PreviewEngine)
    engine._lock = threading.Lock()
    first, second = (lambda state: None), (lambda state: None)
    engine._listeners = [first, second]

    engine.unsubscribe(first)
    engine.unsubscribe(first)                              # deux fois : sans effet, sans erreur

    assert engine._listeners == [second]
