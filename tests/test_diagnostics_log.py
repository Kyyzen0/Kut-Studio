"""Journal de diagnostic : une exception sans console laisse une trace, et rien n'empêche de démarrer."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

from core import diagnostics_log
from core.diagnostics_log import install_diagnostics, log_file_path, uninstall_diagnostics
from core.platform_paths import user_log_dir


def _neutral_hooks(monkeypatch):
    """Crochets d'origine neutres : ceux de pytest(-qt) feraient échouer le test qui provoque l'exception."""
    monkeypatch.setattr(sys, "excepthook", lambda *_args: None)
    monkeypatch.setattr(threading, "excepthook", lambda _args: None)
    monkeypatch.setattr(sys, "unraisablehook", lambda _args: None)
    return sys.excepthook, threading.excepthook, sys.unraisablehook


@pytest.fixture
def diagnostics(tmp_path, monkeypatch):
    """Installe le journal dans un dossier temporaire et restaure les crochets d'origine ensuite."""
    original = _neutral_hooks(monkeypatch)
    path = install_diagnostics(tmp_path / "logs")
    yield path
    uninstall_diagnostics()
    assert (sys.excepthook, threading.excepthook, sys.unraisablehook) == original


def _flush() -> None:
    for handler in logging.getLogger("kut").handlers:
        handler.flush()


def _text(path: Path) -> str:
    _flush()
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("platform_name", "parts"),
    [
        ("win32", ("AppData", "Local", "Kut-Studio", "Logs")),
        ("darwin", ("Library", "Logs", "Kut-Studio")),
        ("linux", (".local", "state", "kut-studio")),
    ],
)
def test_the_log_folder_follows_each_platform_convention(platform_name, parts):
    home = Path("maison")
    assert user_log_dir(platform_name=platform_name, environment={}, home=home) == home.joinpath(*parts)


def test_the_log_folder_honours_the_platform_base_variables():
    assert user_log_dir(platform_name="win32", environment={"LOCALAPPDATA": "L"}, home="h") == Path("L/Kut-Studio/Logs")
    assert user_log_dir(platform_name="linux", environment={"XDG_STATE_HOME": "S"}, home="h") == Path("S/kut-studio")


def test_the_log_folder_can_be_overridden_by_environment():
    assert user_log_dir(platform_name="linux", environment={"KUT_STUDIO_LOG_DIR": "/x/logs"}, home="/h") == Path("/x/logs")


def test_startup_writes_a_header_with_the_versions(diagnostics):
    assert diagnostics == log_file_path(diagnostics.parent)
    text = _text(diagnostics)
    assert "Démarrage" in text and "Python" in text


def test_an_uncaught_exception_is_logged_with_its_traceback(diagnostics):
    try:
        raise RuntimeError("boom principal")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    text = _text(diagnostics)
    assert "Exception non rattrapée" in text and "RuntimeError: boom principal" in text and "Traceback" in text


def test_an_exception_in_a_worker_thread_is_logged(diagnostics):
    def work():
        raise ValueError("boom thread")

    thread = threading.Thread(target=work, name="kut-worker-test")
    thread.start()
    thread.join()
    text = _text(diagnostics)
    assert "kut-worker-test" in text and "ValueError: boom thread" in text


def test_an_exception_in_a_qt_slot_is_logged(diagnostics, qtbot):
    from PySide6.QtCore import QObject, Signal

    class Source(QObject):
        fired = Signal()

    def slot():
        raise KeyError("boom slot")

    source = Source()
    source.fired.connect(slot)
    source.fired.emit()
    assert "KeyError" in _text(diagnostics) and "boom slot" in _text(diagnostics)


def test_installing_twice_does_not_duplicate_lines(diagnostics, tmp_path):
    assert install_diagnostics(tmp_path / "ailleurs") == diagnostics
    logging.getLogger("core.test").warning("une seule fois")
    assert _text(diagnostics).count("une seule fois") == 1
    assert not (tmp_path / "ailleurs").exists()


def test_application_loggers_reach_the_file(diagnostics):
    logging.getLogger("ui.main_window_mixins.timeline_editing").info("opération refusée de test")
    logging.getLogger("core.render_queue").warning("avertissement du coeur")
    text = _text(diagnostics)
    assert "opération refusée de test" in text and "avertissement du coeur" in text


def test_an_unwritable_log_folder_never_prevents_startup(tmp_path, monkeypatch):
    blocker = tmp_path / "fichier"
    blocker.write_text("pas un dossier", encoding="utf-8")
    original = _neutral_hooks(monkeypatch)[0]
    try:
        assert install_diagnostics(blocker / "logs") is None      # mkdir échoue : repli sur stderr, sans lever
        assert sys.excepthook is not original                     # les crochets sont tout de même posés
    finally:
        uninstall_diagnostics()
    assert sys.excepthook is original


def test_a_windowed_application_without_stderr_still_starts(tmp_path, monkeypatch):
    """PyInstaller --windowed sous Windows : ``sys.stderr`` vaut ``None`` et le dossier peut être illisible."""
    blocker = tmp_path / "fichier"
    blocker.write_text("pas un dossier", encoding="utf-8")
    _neutral_hooks(monkeypatch)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert install_diagnostics(blocker / "logs") is None
        logging.getLogger("core.test").warning("sans destination : ne doit pas lever")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        uninstall_diagnostics()


def test_uninstall_without_install_is_harmless():
    assert diagnostics_log._installed is None
    uninstall_diagnostics()
