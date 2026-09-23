import shutil
import sys
from pathlib import Path

import pytest

# Permettre l'import des modules du projet depuis la racine
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.sh"


@pytest.fixture
def fake_ffmpeg_path(monkeypatch):
    """Force le module export_engine à utiliser notre faux ffmpeg."""
    monkeypatch.setattr("core.export_engine._ffmpeg_path", str(FAKE_FFMPEG))
    return FAKE_FFMPEG


def _make_video_clip(path: str, clip_id: str = "clip_1", start: float = 0.0, end: float = 4.0):
    """Construit un clip média factice pour les tests."""
    return {
        "id": clip_id,
        "track": 0,
        "start": start,
        "end": end,
        "label": f"Clip {clip_id}",
        "color": "#4da3ff",
        "source_path": path,
    }


def _make_subtitle_clip(text: str = "Hello", clip_id: str = "sub_1"):
    return {
        "id": clip_id,
        "track": 2,
        "start": 0.0,
        "end": 5.0,
        "label": "Sous-titre",
        "color": "#e6c84f",
        "text": text,
    }


def _create_dummy_input_files(tmp_path: Path) -> list[str]:
    """Crée 2 fichiers vidéo vides simulant des sources média."""
    files = []
    for i in range(2):
        path = tmp_path / f"input_{i}.mp4"
        path.write_bytes(b"\x00" * 100)  # fichier factice non vide
        files.append(str(path))
    return files


def test_export_full_pipeline_emits_finished_ok(qtbot, tmp_path, fake_ffmpeg_path, monkeypatch):
    """Lance un export complet, vérifie que finished_ok est émis et que le fichier existe."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    # Créer des fichiers d'entrée factices
    input_files = _create_dummy_input_files(tmp_path)
    output_file = tmp_path / "output.mp4"

    # Importer MainWindow et construire l'UI
    from ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)

    # Injecter 2 clips dans la timeline
    window.timeline_panel.clips = [
        _make_video_clip(input_files[0], "clip_a", 0.0, 4.0),
        _make_video_clip(input_files[1], "clip_b", 4.0, 8.0),
    ]
    window.timeline_panel.refresh_clip_widgets()

    # Vérifier que l'engine est bien créé et capturer les signaux
    from PySide6.QtCore import QObject
    engine = window.export_engine

    finished_ok_emitted = []
    failed_emitted = []

    def on_finished(path):
        finished_ok_emitted.append(path)

    def on_failed(msg):
        failed_emitted.append(msg)

    engine.finished_ok.connect(on_finished)
    engine.failed.connect(on_failed)

    # Construire la requête et lancer l'export
    from core.export_engine import ExportRequest, ExportFormat, ExportPreset
    request = ExportRequest(
        clips=window.timeline_panel.clips,
        output_path=str(output_file),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
        fps=30,
    )
    engine.start(request)

    # Attendre la fin avec un timeout de 10s (le fake ffmpeg dure ~1s)
    loop = QEventLoop()
    engine.finished_ok.connect(loop.quit)
    engine.failed.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()

    # Vérifications
    assert not failed_emitted, f"Export a échoué : {failed_emitted}"
    assert finished_ok_emitted, "Le signal finished_ok n'a pas été émis"
    assert output_file.exists(), f"Le fichier de sortie n'a pas été créé : {output_file}"


def test_export_with_no_exportable_clips_emits_failed(qtbot, tmp_path, fake_ffmpeg_path):
    """Si aucun clip n'a de source_path, l'export doit émettre failed avec un message clair."""
    from PySide6.QtCore import QEventLoop, QTimer
    from ui.main_window import MainWindow
    from core.export_engine import ExportRequest, ExportFormat, ExportPreset

    window = MainWindow()
    qtbot.addWidget(window)

    # Clip SANS source_path (sous-titre uniquement)
    window.timeline_panel.clips = [_make_subtitle_clip("Test")]

    engine = window.export_engine
    failed_messages = []
    engine.failed.connect(failed_messages.append)

    request = ExportRequest(
        clips=window.timeline_panel.clips,
        output_path=str(tmp_path / "output.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
        fps=30,
    )
    engine.start(request)

    # Pas besoin de boucle d'attente : l'erreur est synchrone (avant start)
    assert failed_messages, "failed doit être émis quand il n'y a aucun média à exporter"
    assert "média" in failed_messages[0].lower() or "video" in failed_messages[0].lower(), \
        f"Message d'erreur inattendu : {failed_messages[0]}"


def test_export_engine_module_path_validation(qtbot, tmp_path):
    """Vérifie que shutil.which est appelé pour valider ffmpeg au chargement du module."""
    # Ce test vérifie juste que le module se charge (donc ffmpeg est trouvé)
    # Si tu veux tester l'erreur, mocke shutil.which pour retourner None
    import core.export_engine as engine_module
    assert engine_module._ffmpeg_path is not None, \
        "ffmpeg (ou le fake pour les tests) doit être trouvable dans le PATH"


def test_export_request_validates_fps_and_resolution(qtbot, tmp_path, fake_ffmpeg_path):
    """Vérifie que des paramètres invalides sont rejetés avant de lancer ffmpeg."""
    from core.export_engine import ExportRequest, ExportFormat, ExportPreset

    # FPS = 0 doit lever ValueError
    with pytest.raises(ValueError, match="fréquence"):
        ExportRequest(
            clips=[],
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
            fps=0,
        )

    # Résolution (0, 0) doit lever ValueError
    with pytest.raises(ValueError, match="résolution"):
        ExportRequest(
            clips=[],
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="Standard", resolution=(0, 0), crf=23, audio_bitrate="128k"),
            fps=30,
        )
