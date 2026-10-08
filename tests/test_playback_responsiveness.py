"""Lecture fluide avec les scopes visibles : rien de lourd sur le fil de l'interface, horloge réelle.

Mesuré sur l'edit « Singapore GP 2026 » (19 plans, ~60 calques animés) en 0.2.5 : avec les scopes visibles (défaut),
un tic de lecture prenait jusqu'à 4,6 s — la commande d'**une** image de scopes était construite sur le fil de
l'interface, en rastérisant chaque calque sur toute sa durée — et la lecture n'avançait que de 1,2 s en 8 s.
Après : 193 tics sur 200, pire tic 8 ms, 7,98 s en 8 s.
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from core.scopes import MAX_SCOPE_SAMPLES, scope_frame_size
from core.scopes_analyzer import BACKGROUND_SHARE, ScopeAnalyzer, png_to_scope_frame

# --- Analyseur : ne rien lancer d'inutile ----------------------------------------------------------------------------


def _blocking_analyzer(release: threading.Event, started: threading.Event) -> ScopeAnalyzer:
    from core.scopes import ScopeFrame

    def extractor(_request):
        started.set()
        release.wait(5)
        return ScopeFrame.from_rgb_bytes(bytes(3 * 4), 2, 2)

    return ScopeAnalyzer(extractor=extractor, min_interval=0.0)


def test_a_busy_analyzer_refuses_playback_requests_but_not_forced_ones():
    release, started = threading.Event(), threading.Event()
    analyzer = _blocking_analyzer(release, started)
    try:
        assert analyzer.would_accept()
        analyzer.submit(playhead=0.0, ffmpeg_command=["x"], force=True)
        assert started.wait(5)
        # Une demande de lecture lancée maintenant rendrait la précédente obsolète : aucune n'aboutirait.
        assert not analyzer.would_accept()
        assert analyzer.would_accept(force=True)           # pause, réglage modifié : la dernière image compte
        release.set()
        assert analyzer.wait_idle(5)
    finally:
        release.set()
        analyzer.close()


def test_playback_requests_leave_the_analyzer_idle_most_of_the_time():
    from core.scopes import ScopeFrame

    analyzer = ScopeAnalyzer(extractor=lambda _r: (time.sleep(0.05), ScopeFrame.from_rgb_bytes(bytes(12), 2, 2))[1],
                             min_interval=0.0)
    try:
        analyzer.submit(playhead=0.0, ffmpeg_command=["x"], force=True)
        assert analyzer.wait_idle(5)
        cost = analyzer._last_cost
        assert cost >= 0.04
        assert not analyzer.would_accept()                 # juste après : l'analyseur doit rester libre un moment
        analyzer._last_finished -= cost * (1.0 / BACKGROUND_SHARE - 1.0) + 0.01
        assert analyzer.would_accept()
    finally:
        analyzer.close()


def test_two_threads_writing_the_same_layer_playlist_both_succeed(tmp_path, monkeypatch):
    """Scopes et aperçu fidèle écrivent parfois la même liste ``.ffconcat`` au même moment, depuis deux threads."""
    from core import mograph_stream

    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: tmp_path)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []
    paths: list[str] = []

    def write():
        try:
            barrier.wait(5)
            paths.append(mograph_stream.write_stream(
                width=4, height=4, fps=10.0, duration=1.0, start=0.0, end=1.0,
                frame_key=lambda _t: None, render=lambda _t: None,
            ))
        except BaseException as error:  # noqa: BLE001 - le test rapporte toute erreur d'un thread
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert len(set(paths)) == 1 and len(paths) == 8
    assert not list(tmp_path.glob("*.tmp"))                 # aucun temporaire laissé


def _layer_stream(tmp_path):
    from core import mograph_stream

    def render(_t):
        image = QImage(4, 4, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(10, 20, 30))
        return image

    return mograph_stream.write_stream(width=4, height=4, fps=10.0, duration=0.2, start=0.0, end=0.2,
                                       frame_key=lambda _t: ("calque", 1), render=render)


def test_a_writer_refused_by_windows_accepts_the_identical_file_of_the_winner(qapp, tmp_path, monkeypatch):
    """Windows refuse de remplacer un fichier qu'un autre thread remplace au même instant (vu en CI) ; le nom étant
    l'empreinte du contenu, le fichier du gagnant est le bon."""
    import os
    import shutil

    from core import mograph_stream

    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: tmp_path)

    def windows_replace(source, target):
        shutil.copyfile(source, target)                     # l'autre écrivain a publié ce même contenu…
        raise PermissionError(13, "Access is denied")       # …et Windows refuse le nôtre

    monkeypatch.setattr(os, "replace", windows_replace)
    playlist = _layer_stream(tmp_path)
    monkeypatch.undo()
    assert os.path.isfile(playlist)
    assert list(tmp_path.glob("f-*.png"))
    assert not [path for path in tmp_path.iterdir() if ".tmp" in path.name]


def test_a_refusal_without_a_winner_is_reported_and_leaves_no_temporary(qapp, tmp_path, monkeypatch):
    import os

    from core import mograph_stream

    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: tmp_path)
    monkeypatch.setattr(mograph_stream.time, "sleep", lambda _s: None)
    calls: list[str] = []

    def refused(source, _target):
        calls.append(str(source))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", refused)
    with pytest.raises(PermissionError):
        _layer_stream(tmp_path)
    monkeypatch.undo()
    assert len(calls) == mograph_stream.SHARED_WRITE_ATTEMPTS  # quelques essais, pas une boucle sans fin
    assert not [path for path in tmp_path.iterdir() if ".tmp" in path.name]


def test_the_command_is_built_on_the_analyzer_thread_and_its_files_are_removed(tmp_path):
    built_on: list[int] = []
    leftover = tmp_path / "graphe.txt"
    leftover.write_text("x")
    done = threading.Event()

    def factory():
        built_on.append(threading.get_ident())
        # Une commande qui échoue vite : seul le thread de construction et le nettoyage comptent ici.
        return ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "nullsrc", "-frames:v", "0", "-"], (str(leftover),)

    analyzer = ScopeAnalyzer(on_error=lambda *_a: done.set(), on_result=lambda *_a: done.set(), min_interval=0.0)
    try:
        analyzer.submit(playhead=0.0, command_factory=factory, force=True)
        assert analyzer.wait_idle(10)
    finally:
        analyzer.close()
    assert built_on and built_on[0] != threading.get_ident()
    assert not leftover.exists()


# --- Image des scopes ------------------------------------------------------------------------------------------------


def test_scope_frame_size_bounds_the_pixels_and_never_enlarges():
    width, height = scope_frame_size(1080, 1920)
    assert width * height <= 2 * MAX_SCOPE_SAMPLES
    assert width % 2 == 0 and height % 2 == 0
    assert abs(width / height - 1080 / 1920) < 0.01
    assert scope_frame_size(320, 180) == (320, 180)


@pytest.mark.parametrize("width", [330, 1366, 7])
def test_a_png_whose_rows_are_padded_is_read(qapp, width):
    """Qt aligne chaque ligne sur 4 octets : ``largeur × 3`` non multiple de 4 rendait l'image illisible."""
    image = QImage(width, 3, QImage.Format_RGB888)
    image.fill(QColor(200, 30, 60))
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    frame = png_to_scope_frame(bytes(payload))
    assert (frame.width, frame.height) == (width, 3)
    assert len(frame.pixels) == width * 3
    assert frame.pixels[0][:3] == (200, 30, 60) and frame.pixels[-1][:3] == (200, 30, 60)


def test_a_single_frame_command_rasterises_only_the_frame_it_needs(tmp_path, monkeypatch):
    """Complexité : une image de scopes ne rastérise pas les calques sur toute leur durée."""
    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.graphics import add_graphic_clip, update_graphic
    from core.mograph_raster import MographRenderer
    from core.project_model import Project
    from core.render_plan import build_render_plan
    from core.text_animations import apply_text_animation

    project = Project(name="Horizon", width=160, height=90, fps=10.0)
    title = add_graphic_clip(project, "text", timeline_start=0.0, duration=6.0)
    update_graphic(title, "text", "Kut")
    apply_text_animation(title, "bounce")                  # une image différente à chaque instant
    asked: list[float] = []
    original = MographRenderer.any_active
    monkeypatch.setattr(MographRenderer, "any_active", lambda self, ids, t: asked.append(t) or original(self, ids, t))
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "f.png"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("s", (160, 90), 23, "128k"), fps=10.0)
    ExportEngine().build_frame_command(request, 0.0)
    assert asked and max(asked) <= 0.2 + 1e-9               # l'image 0 et une de marge, pas 60 images


# --- Fenêtre : le tic de lecture -------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win, "_sync_preview_to_timeline", lambda: [])
    monkeypatch.setattr(win, "update_subtitle_overlay", lambda *_a: None)
    monkeypatch.setattr(win, "_ensure_timeline_index", lambda: type("I", (), {"duration": 100.0})())
    return win


@pytest.mark.parametrize(("late", "expected"), [(None, 0.04), (0.2, 0.2), (5.0, 5.0)])
def test_the_playhead_follows_real_time_not_the_number_of_ticks(window, late, expected):
    """Un tic en retard ne ralentit plus la lecture (sinon le lecteur, en temps réel, était recalé de force).

    Même après un long gel : le lecteur a avancé de tout ce temps, une tête de lecture bornée le ferait reculer.
    """
    window.is_playing = True
    window.playhead_seconds = 1.0                           # le projet par défaut dure 12 s : 1 + 5 reste dedans
    window._playback_clock = None if late is None else time.perf_counter() - late
    window._tick_playback()
    assert window.playhead_seconds - 1.0 == pytest.approx(expected, abs=0.03)
    window.is_playing = False


def test_a_refused_scopes_request_costs_nothing_on_the_interface_thread(window, monkeypatch):
    prepared: list[float] = []
    monkeypatch.setattr(window, "_prepare_scopes_command", lambda playhead: prepared.append(playhead))
    monkeypatch.setattr(window.scopes_analyzer, "would_accept", lambda **_k: False)
    window._scopes_visible = True
    window.playhead_seconds = 3.0
    window._request_scopes_analysis()
    assert prepared == []                                   # ni plan, ni graphe, ni image de calque
