"""Shared pytest configuration for Kut-Studio."""

import os
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The tests build Qt widgets but do not require an on-screen desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Garde des tests ``@pytest.mark.libass`` : ils exigent un FFmpeg avec libass.

    Sans libass le test se saute avec la raison ; avec ``KUT_STUDIO_REQUIRE_LIBASS=1`` il échoue
    (voir ``tests/ffmpeg_caps.py``). Posée ici, la règle ne dépend d'aucun test en particulier.
    """
    if pyfuncitem.get_closest_marker("libass") is not None:
        from ffmpeg_caps import ensure_libass

        ensure_libass()


def pytest_configure(config):
    """Déclare la marque ``libass`` ; donne à chaque worker xdist son propre dossier temporaire.

    Plusieurs tests vérifient qu'aucun fichier ``kut-preview-*`` ou
    ``kut-studio-subtitles-*`` ne traîne dans le dossier temporaire du
    système : avec des workers parallèles, ils verraient les fichiers
    légitimes d'un autre worker.
    """
    config.addinivalue_line(
        "markers",
        "libass: test qui exige un FFmpeg avec libass (filtre « subtitles ») ; sauté sans libass, "
        "échoué si KUT_STUDIO_REQUIRE_LIBASS=1 ; sélection : pytest -m libass",
    )
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    import tempfile

    base = Path(tempfile.gettempdir()) / f"kut-studio-pytest-{os.getpid()}-{worker}"
    base.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = str(base)
    tempfile.tempdir = str(base)
    config.add_cleanup(lambda: shutil.rmtree(base, ignore_errors=True))


@pytest.fixture(autouse=True)
def _isolate_kut_studio_config(monkeypatch, tmp_path):
    """Empêche les tests de lire ou modifier les presets de l'utilisateur."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    # Les proxies générés par un test ne doivent jamais aller dans le vrai cache de l'utilisateur.
    monkeypatch.setenv("KUT_STUDIO_PROXY_DIR", str(tmp_path / "proxies"))
    # Reproductibilité : jamais de détection GPU réelle, ni de cache de capacités partagé.
    # Les tests d'encodage matériel construisent leur propre ``CapabilityService``.
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path.parent / f"{tmp_path.name}-cache"))
    monkeypatch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    from core.hardware_cache import set_default_service

    set_default_service(None)
    yield
    set_default_service(None)


@pytest.fixture(scope="session", autouse=True)
def _widgets_application():
    """Une vraie ``QApplication`` avant le premier test, quel que soit l'ordre d'exécution.

    Le code de rendu (graphes d'export, motion graphics) crée une ``QGuiApplication`` nue quand aucune
    application n'existe (:func:`core.mograph_stream.ensure_qt_gui`). Si un test sans fixture ``qapp``
    passait en premier dans un worker, les tests d'interface suivants héritaient de cette application sans
    widgets : plantage du worker ou échec selon la répartition des tests, donc selon le nombre de cœurs.
    (Créée directement, sans passer par ``qapp`` : certains modules redéfinissent cette fixture.)
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - Qt absent
        yield None
        return
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(scope="session", autouse=True)
def _release_media_players_at_exit():
    """Arrête les ``QMediaPlayer`` encore vivants avant la fin du processus.

    Les tests créent des fenêtres sans les fermer : leurs threads FFmpeg
    peuvent alors journaliser pendant la finalisation de Python, ce qui
    fait planter le gestionnaire de messages Qt de pytest-qt.
    """
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - Qt absent
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        for player in widget.findChildren(QMediaPlayer):
            try:
                player.stop()
                player.setSource(QUrl())
            except RuntimeError:  # objet C++ déjà détruit
                pass
    QCoreApplication.processEvents()


@pytest.fixture(autouse=True)
def _no_unsaved_changes_prompt(monkeypatch):
    """La boîte « enregistrer avant de quitter ? » est modale : sans réponse elle bloquerait tout test qui
    ferme une fenêtre modifiée (qtbot ferme les fenêtres à la fin du test). Par défaut on abandonne les
    modifications ; ``test_unsaved_changes_prompt`` réactive la vraie boîte."""
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
