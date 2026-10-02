"""Un pilote qui fait tomber l'application ne doit pas la faire retomber à chaque démarrage.

``GpuHealth`` ne voit que les échecs que Qt rapporte : une erreur fatale du pilote ne laisse aucune trace de ce
genre, et le GPU était retenté à chaque lancement. Un marqueur « GPU actif » est posé à l'activation et retiré à
l'arrêt propre ; resté en place au démarrage suivant, il signale un plantage et le mode Auto reste sur le CPU
jusqu'à un choix explicite de l'utilisateur.
"""

from __future__ import annotations

import json

import pytest

from core.gpu_backend import GpuCrashGuard, resolve_preview_backend


def _session(tmp_path) -> GpuCrashGuard:
    """Un nouveau processus : instance neuve sur le même dossier, ``begin_session`` appelé au démarrage."""
    guard = GpuCrashGuard(tmp_path)
    guard.begin_session()
    return guard


def test_a_clean_history_is_not_suspended(tmp_path):
    assert not _session(tmp_path).suspended


def test_a_marker_left_behind_is_a_crash_and_suspends_auto(tmp_path):
    _session(tmp_path).arm()                                     # le GPU est actif… puis le processus tombe
    after_crash = _session(tmp_path)
    assert after_crash.suspended
    assert json.loads(after_crash.path.read_text(encoding="utf-8")) == {"armed": False, "crashes": 1}


def test_the_crash_is_remembered_across_following_sessions_until_the_user_chooses(tmp_path):
    _session(tmp_path).arm()
    assert _session(tmp_path).suspended
    assert _session(tmp_path).suspended                          # une session CPU propre ne l'efface pas
    _session(tmp_path).reset()                                   # choix explicite dans les préférences
    assert not _session(tmp_path).suspended


def test_a_clean_shutdown_is_not_a_crash(tmp_path):
    guard = _session(tmp_path)
    guard.arm()
    guard.disarm()                                               # arrêt propre (ou repli CPU en cours de session)
    assert not _session(tmp_path).suspended


def test_a_gpu_failure_followed_by_a_crash_is_not_blamed_on_the_gpu(tmp_path):
    guard = _session(tmp_path)
    guard.arm()
    guard.disarm()                                               # le moniteur est revenu au CPU
    assert not _session(tmp_path).suspended                      # un plantage ultérieur n'est pas le sien


@pytest.mark.parametrize("content", ["pas du json", "[]", '{"crashes": "beaucoup"}', '{"armed": "oui"}', ""])
def test_a_damaged_marker_means_no_crash(tmp_path, content):
    (tmp_path / GpuCrashGuard.FILE_NAME).write_text(content, encoding="utf-8")
    assert not _session(tmp_path).suspended


def test_an_unwritable_folder_never_raises(tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("pas un dossier", encoding="utf-8")
    guard = GpuCrashGuard(blocker / "sous-dossier")
    guard.begin_session()
    guard.arm()
    guard.disarm()
    guard.reset()
    assert not guard.suspended


# --- décision --------------------------------------------------------------------------------------------


def _resolve(requested, guard):
    return resolve_preview_backend(requested, guard=guard, environment={}, platform_name="linux", platform_plugin="xcb")


def test_auto_goes_to_the_gpu_without_a_known_crash_and_to_the_cpu_after_one(tmp_path):
    assert _resolve("auto", _session(tmp_path)).is_gpu
    _session(tmp_path).arm()
    resolved = _resolve("auto", _session(tmp_path))
    assert not resolved.is_gpu and resolved.reason == "previous_crash"
    assert "GPU" in resolved.fallback_reason and "préférences" in resolved.fallback_reason


def test_an_explicit_gpu_choice_is_honoured_even_after_a_crash(tmp_path):
    _session(tmp_path).arm()
    assert _resolve("gpu", _session(tmp_path)).is_gpu
    assert not _resolve("cpu", _session(tmp_path)).is_gpu


# --- fenêtre ---------------------------------------------------------------------------------------------


def test_the_window_starts_on_the_cpu_after_a_crash_and_a_preferences_choice_gives_the_gpu_its_chance(qtbot, monkeypatch):
    from test_scopes import _window

    GpuCrashGuard().arm()                                        # marqueur laissé par une session qui a planté
    window = _window(qtbot, monkeypatch)
    assert window._gpu_guard.suspended

    window.set_preview_backend("gpu")                            # choix explicite : le plantage est oublié
    assert not window._gpu_guard.suspended


def test_closing_the_window_cleanly_clears_the_active_gpu_marker(qtbot, monkeypatch):
    from test_scopes import _window

    window = _window(qtbot, monkeypatch)
    window._gpu_guard.arm()
    window.close()
    assert json.loads(window._gpu_guard.path.read_text(encoding="utf-8"))["armed"] is False
    assert not GpuCrashGuard().suspended
