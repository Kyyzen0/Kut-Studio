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
# Aucune fenêtre de test ne contacte GitHub : la recherche automatique des mises à jour est coupée pour tout le
# processus, dès l'import (les fixtures de module créent leurs fenêtres avant les fixtures de fonction). Les tests
# des mises à jour utilisent un serveur HTTP local ou des réponses simulées.
os.environ["KUT_STUDIO_UPDATE_CHECK"] = "off"
# Le profil de performance « Auto » ne doit dépendre ni du banc machine ni de l'énergie de la machine qui lance les tests.
# Le banc (core/machine_bench.py) tourne dans un fil de travail et change le profil quand il se termine : un test qui
# peint deux fenêtres l'une après l'autre les voyait parfois dans deux profils différents (les clips de la timeline
# peignent des vignettes en profil « balanced » ou « high », restent unis en « low » : 49 184 pixels d'écart, ce qui faisait
# échouer test_theme_live_switch sur macOS en CI). Posé dès l'import, comme la mise à jour : les fixtures de module créent
# leurs fenêtres avant les fixtures de fonction. Un test du banc ou de l'énergie les rétablit lui-même (monkeypatch).
os.environ["KUT_STUDIO_MACHINE_BENCH"] = "off"
os.environ["KUT_STUDIO_POWER"] = "ac"
# Aucune fenêtre de test n'ouvre la sortie audio ni ne lance le mixage en direct en appuyant sur Lecture : les machines de
# CI n'ont pas de carte son, et chaque lecture lancerait un FFmpeg. Les tests du mixage en direct le rallument, avec une
# sortie simulée (``tests/test_live_mix_player.py``).
os.environ["KUT_STUDIO_LIVE_MIX"] = "off"

from tools.ui_audit import ensure_offscreen_fonts  # noqa: E402  (après le réglage du chemin et de la plateforme)

ensure_offscreen_fonts()  # Windows : sans police, chaque caractère mesure une boîte carrée et toute mise en page déborde


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Garde des tests ``@pytest.mark.libass`` : ils exigent un FFmpeg avec libass.

    Sans libass le test se saute avec la raison ; avec ``KUT_STUDIO_REQUIRE_LIBASS=1`` il échoue
    (voir ``tests/ffmpeg_caps.py``). Posée ici, la règle ne dépend d'aucun test en particulier.
    """
    if pyfuncitem.get_closest_marker("libass") is not None:
        from ffmpeg_caps import ensure_libass

        ensure_libass()


@pytest.fixture(autouse=True)
def _deliver_deferred_deletes():
    """Détruit vraiment les widgets que ``qtbot`` vient de fermer (``deleteLater`` n'est jamais livré sans boucle).

    ``qtbot.addWidget`` ferme la fenêtre puis appelle ``deleteLater()`` ; hors d'une boucle d'événements, cette
    suppression différée n'arrive jamais. La fenêtre fermée restait donc vivante, avec ses panneaux **abonnés à la
    langue** (``i18n.subscribe`` se désabonne au ``destroyed``) : après une centaine de tests d'un même processus,
    chaque ``set_language`` retraduisait 90 fenêtres fantômes (70 s au lieu de 2 s pour un test, soit une suite qui
    dépassait le délai de 30 minutes de la CI macOS). Fixture automatique, donc démontée **après** celle de ``qtbot``.
    """
    yield
    from qt_cleanup import deliver_deferred_deletes

    deliver_deferred_deletes()


@pytest.fixture(autouse=True, scope="module")
def _destroy_module_windows():
    """Détruit, à la fin de chaque module, les fenêtres que ses fixtures de portée module ont laissées vivantes.

    Démontée après toutes les fixtures du module (autouse, déclarée ici) : plus aucun test du module ne s'en sert. Sans
    elle, les fenêtres fantômes s'accumulent dans un worker et ralentissent tout test qui change le thème (voir
    ``qt_cleanup.destroy_leftover_windows``).
    """
    yield
    from qt_cleanup import destroy_leftover_windows

    destroy_leftover_windows()


def pytest_configure(config):
    """Déclare la marque ``libass`` ; donne à chaque worker xdist son propre dossier temporaire.

    Plusieurs tests vérifient qu'aucun fichier ``kut-preview-*`` ou
    ``kut-studio-subtitles-*`` ne traîne dans le dossier temporaire du
    système : avec des workers parallèles, ils verraient les fichiers
    légitimes d'un autre worker.
    """
    config.addinivalue_line("markers", "real_probe_threads: laisse tourner les sondes d'étiquette de fond (core.media_probe)")
    config.addinivalue_line(
        "markers",
        "libass: test qui exige un FFmpeg avec libass (filtre « subtitles ») ; sauté sans libass, "
        "échoué si KUT_STUDIO_REQUIRE_LIBASS=1 ; sélection : pytest -m libass",
    )
    config.addinivalue_line(
        "markers",
        "recette_smoke: smoke test du banc de recette (montage de 10 s exporté en CPU, vérifié par ffprobe) ; sauté sans FFmpeg, "
        "aucun seuil de durée ; sélection : pytest -m recette_smoke (docs/recette/README.md)",
    )
    config.addinivalue_line(
        "markers",
        "real_media_player: laisse le vrai QMediaPlayer ouvrir un fichier inexistant (erreur de lecture) ; "
        "sans cette marque, la garde de tests/media_player_guard.py l'ignore",
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
def _no_real_player_on_missing_media(request, monkeypatch):
    """Aucun vrai ``QMediaPlayer`` n'ouvre un média inexistant (cause d'un SIGSEGV aléatoire sous xdist ; voir
    ``tests/media_player_guard.py``). Un fichier existant est ouvert normalement. Exception : ``real_media_player``."""
    if not request.node.get_closest_marker("real_media_player"):
        from media_player_guard import install

        install(monkeypatch)
    yield


@pytest.fixture(autouse=True)
def _no_background_color_probe(request, monkeypatch):
    """Pas de fil de sonde de fond (``ffprobe``) dans les tests : un processus enfant lancé par un fil qui survit à son test ou à la
    session est un risque d'arrêt dur sous Windows. Les tests qui visent ces fils portent la marque ``real_probe_threads``."""
    if request.node.get_closest_marker("real_probe_threads"):
        yield
        return
    from core import media_probe

    monkeypatch.setattr(media_probe, "warm_color_label", lambda path: False)
    monkeypatch.setattr(media_probe, "warm_color_labels", lambda paths: False)
    yield


@pytest.fixture(scope="session", autouse=True)
def _stop_color_probes_at_exit():
    """Arrête et attend les sondes de fond avant la fin de la session (jamais un fil qui lance un processus pendant l'arrêt)."""
    yield
    from core import media_probe

    media_probe.shutdown_color_probes(5.0)


def pytest_sessionfinish(session, exitstatus):
    """Diagnostic : les fils encore vivants à la fin de la session, dans ``kut-session-threads-<pid>.txt`` (artefact de la CI)."""
    import threading

    try:
        alive = [f"{t.name} daemon={t.daemon}" for t in threading.enumerate() if t is not threading.main_thread()]
        if os.environ.get("CI"):
            Path(f"kut-session-threads-{os.getpid()}.txt").write_text(
                f"exitstatus={exitstatus} worker={os.environ.get('PYTEST_XDIST_WORKER', 'main')}\n" + "\n".join(alive) + "\n",
                encoding="utf-8")
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolate_kut_studio_config(monkeypatch, tmp_path):
    """Empêche les tests de lire ou modifier les presets de l'utilisateur."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    # Bibliothèque de SFX synthétisés : jamais dans les données de l'utilisateur.
    monkeypatch.setenv("KUT_STUDIO_DATA_DIR", str(tmp_path / "data"))
    # Les proxies générés par un test ne doivent jamais aller dans le vrai cache de l'utilisateur.
    monkeypatch.setenv("KUT_STUDIO_PROXY_DIR", str(tmp_path / "proxies"))
    # Reproductibilité : jamais de détection GPU réelle, ni de cache de capacités partagé.
    # Les tests d'encodage matériel construisent leur propre ``CapabilityService``.
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path.parent / f"{tmp_path.name}-cache"))
    monkeypatch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    # Reproductibilité : jamais la batterie ni la chauffe réelles de la machine qui lance les tests (un portable sur
    # batterie dégraderait l'aperçu de chaque test). Les tests du régulateur forcent leurs propres valeurs.
    monkeypatch.setenv("KUT_STUDIO_POWER", "ac")
    from core.hardware_cache import set_default_service

    set_default_service(None)
    _forget_remembered_sections()
    yield
    set_default_service(None)
    _forget_remembered_sections()


def _forget_remembered_sections() -> None:
    """Les sections repliables de l'inspecteur se souviennent de leur état **pour la session** (un attribut de classe) : sans cette
    remise à zéro, ce qu'un test replie ou déplie réapparaît dans les fenêtres des tests suivants du même worker xdist, selon la
    répartition des tests entre workers."""
    try:
        from ui.properties_widgets.section_box import SectionBox
    except ImportError:  # pragma: no cover - Qt absent
        return
    SectionBox._remembered.clear()  # noqa: SLF001


@pytest.fixture
def restore_global_theme():
    """Rend à l'application, à la fin du test, le thème qu'elle avait au début : feuille de style, marqueur, palette.

    Le thème est un état **global au processus** : la feuille de style de la ``QApplication`` (avec le marqueur
    ``_kut_studio_theme_stylesheet`` qui évite de la réappliquer à l'identique) et la palette que publie
    ``ui.theme.set_active_palette``. Un test qui passait en thème clair le laissait au test suivant du même worker
    xdist ; la ``MainWindow`` de celui-ci rebasculait en sombre, donc la feuille de style changeait *dans* ce
    test-là, ce que le garde de ``test_ui_small_windows`` fait échouer (ou que d'autres tests absorbent en silence),
    selon la répartition des tests entre workers.

    À demander par les seuls tests qui changent de thème. Elle n'est pas automatique : la première fenêtre d'un
    worker installe légitimement la feuille de style, et la défaire après chaque test la ferait réinstaller (donc
    repolir tous les widgets) à chaque fois.
    """
    from PySide6.QtWidgets import QApplication

    from ui.theme import active_palette, set_active_palette

    marker_name = "_kut_studio_theme_stylesheet"
    app = QApplication.instance()
    stylesheet, marker, palette = app.styleSheet(), app.property(marker_name), active_palette()
    yield
    set_active_palette(palette)
    if app.styleSheet() != stylesheet:   # ``setStyleSheet`` repolit tous les widgets, même à l'identique
        app.setStyleSheet(stylesheet)
    app.setProperty(marker_name, marker)


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
    # Polices embarquées enregistrées tout de suite, comme main.py le fait avant la première fenêtre : les ajouter en
    # cours de session (première résolution de famille) vide les caches de polices de Qt sous des widgets vivants, et
    # un changement de feuille de style plantait ensuite le worker Windows hors écran (tas corrompu, 0xc0000374).
    from core.bundled_fonts import register_bundled_fonts

    register_bundled_fonts()
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


MAX_TEST_ID = 2000
"""Longueur maximale d'un identifiant de test. pytest le copie dans la variable d'environnement ``PYTEST_CURRENT_TEST`` : sous
Windows, une variable de plus de 32 767 caractères fait échouer l'installation et le démontage de CHAQUE test concerné (le journal
de la CI s'arrêtait net après « ERRORS »). Les paramètres démesurés (entrée hostile) reçoivent un ``ids=`` court."""


def pytest_collection_modifyitems(config, items):
    too_long = [item.nodeid[:120] + f"… ({len(item.nodeid)} caractères)" for item in items if len(item.nodeid) > MAX_TEST_ID]
    if too_long:
        raise pytest.UsageError(
            f"Identifiants de test trop longs (> {MAX_TEST_ID}, ``PYTEST_CURRENT_TEST`` plafonne à 32 767 sous Windows) ; "
            "ajoutez ids=… à parametrize :\n" + "\n".join(too_long[:10]))
