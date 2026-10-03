"""Moniteur GPU et décodage dans la fenêtre (plateforme ``offscreen`` : aucun GPU réel).

Le vrai widget GPU est remplacé par un faux qui enregistre ce qu'on lui demande ;
la perte de périphérique est simulée. Le chemin CPU, lui, est le vrai.
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from test_performance_ui import _prefs, _window, fake_proxies  # noqa: F401 - fixtures partagées

from core.blend_modes import BlendMode
from core.effects_model import ClipEffect, EffectType
from core.gpu_backend import FrameStats
from core.gpu_cache import GpuTextureCache
from core.memory_monitor import CRITICAL, MemoryStatus, MemoryWatch
from core.user_settings import load_user_settings
from ui.preview_panel import PLAYBACK_DRIFT_SECONDS, PreviewPanel


class FakeGpuView(QWidget):
    """Remplace ``GpuPreviewWidget`` : mêmes signaux, aucune ressource GPU."""

    failed = Signal(str, str)
    ready = Signal(str)
    instances: list[FakeGpuView] = []

    def __init__(self, api="metal", parent=None, cache_budget=None):
        super().__init__(parent)
        self.api = api
        self.frames = []
        self.composites = []
        self.forgotten = []
        self.cache_purges = 0
        self.released = False
        self.stats = FrameStats()
        self.cache = GpuTextureCache(cache_budget or 64 * 2**20)
        self.executor = None
        self.fallback_frames = 0
        FakeGpuView.instances.append(self)

    def set_video_frame(self, source_id, frame):
        self.frames.append(source_id)
        self.stats.note_arrival()

    def set_composite(self, frame, mattes=None):
        self.composites.append((frame, dict(mattes or {})))

    def set_canvas_rect(self, rect, background):
        self.rect_value = rect

    def forget_source(self, source_id):
        self.forgotten.append(source_id)

    def release_gpu_cache(self):
        self.cache_purges += 1
        return 0

    def release_gpu(self):
        self.released = True

    @property
    def last(self):
        return self.composites[-1][0]


@pytest.fixture
def fake_gpu(monkeypatch):
    FakeGpuView.instances = []
    monkeypatch.setattr("ui.gpu_preview.GpuPreviewWidget", FakeGpuView)
    return FakeGpuView


def _panel(qtbot):
    panel = PreviewPanel(lambda: None, lambda: None, lambda _d: None, lambda: None, lambda: None)
    qtbot.addWidget(panel)
    panel.resize(800, 500)
    panel.set_canvas_size(1920, 1080)
    return panel


# --- Panneau --------------------------------------------------------------------------------------------


def test_gpu_monitor_describes_transform_effects_blend_and_matte(qtbot, fake_gpu):
    from PySide6.QtCore import QSize
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat

    panel = _panel(qtbot)
    assert panel.enable_gpu("metal") and panel.gpu_active
    view = fake_gpu.instances[0]
    assert not panel.video_item.isVisible()  # le moniteur CPU est caché, pas détruit
    panel._timeline_preview_path = "clip.mp4"
    frame = QVideoFrame(QVideoFrameFormat(QSize(3840, 2160), QVideoFrameFormat.PixelFormat.Format_NV12))
    panel._on_gpu_frame(frame)
    assert view.frames == ["main"]
    panel.apply_transform(position_x=0.1, scale=0.5, rotation=10.0, opacity=0.7)
    layer = view.last.layers[0]
    assert layer.opacity == pytest.approx(0.7) and layer.effect_scale == (0.5, 0.5)
    assert layer.fit == (0.0, 0.0, 1920.0, 1080.0)  # 4K 16:9 adapté au cadre 1080p
    effects = [ClipEffect("b", EffectType.BLUR, True, {"intensity": 3.0}),
               ClipEffect("s", EffectType.SEPIA, True, {})]
    panel.set_effects(effects)
    assert len(view.last.layers[0].program.neighborhood) == 1
    assert not panel.preview_effects_overlay.isVisibleTo(panel)  # effets montrés en direct : pas de pastille
    panel.set_layer_compositing(BlendMode.SCREEN, ("m1", object()))
    frame_desc, mattes = view.composites[-1]
    assert frame_desc.layers[0].blend is BlendMode.SCREEN and frame_desc.layers[0].matte == "m1"
    assert "m1" in mattes
    panel.show_empty()
    assert view.forgotten == ["main"]


def test_gpu_failure_returns_to_the_cpu_monitor(qtbot, fake_gpu):
    panel = _panel(qtbot)
    panel.enable_gpu("metal")
    view = fake_gpu.instances[0]
    events = []
    panel.gpu_failed.connect(lambda kind, detail: events.append((kind, detail)))
    view.failed.emit("device_lost", "pilote réinitialisé (simulé)")
    assert events == [("device_lost", "pilote réinitialisé (simulé)")]
    assert not panel.gpu_active and view.released
    assert panel.video_item.isVisible()
    assert panel.player.videoOutput() is panel.video_item  # le lecteur est rebranché sur le CPU
    panel.apply_transform(scale=0.5)  # le moniteur CPU continue de fonctionner
    assert panel.current_applied_transform()["scale"] == 0.5


def test_real_gpu_widget_fails_cleanly_without_a_window_system(qtbot):
    """En ``offscreen``, QRhi est indisponible : le widget signale l'échec, sans exception."""
    from ui.gpu_preview import GpuPreviewWidget

    widget = GpuPreviewWidget(api="metal")
    qtbot.addWidget(widget)
    failures = []
    widget.failed.connect(lambda kind, detail: failures.append(kind))
    image = widget.grabFramebuffer()
    qtbot.waitUntil(lambda: failures or image.isNull(), timeout=2000)
    assert image.isNull()
    widget.release_gpu()  # idempotent, même sans exécuteur


def test_playback_keeps_the_player_clock_instead_of_seeking_every_tick(qtbot):
    panel = _panel(qtbot)

    class Player(QObject):
        def __init__(self):
            super().__init__()
            self.seeks = []
            self.sources = []
            self.position_ms = 0

        def setSource(self, url):  # noqa: N802 - API Qt
            self.sources.append(url)

        def setPosition(self, value):  # noqa: N802
            self.seeks.append(value)
            self.position_ms = value

        def position(self):
            return self.position_ms

        def playbackState(self):  # noqa: N802
            from PySide6.QtMultimedia import QMediaPlayer

            return QMediaPlayer.PlayingState

        def stop(self):
            pass

    player = Player()
    panel.player = player
    panel.preview_at("a.mp4", 1.0, playing=True)
    assert player.seeks == [1000]                       # nouvelle source : un recalage
    for tick in range(1, 25):                           # une seconde de lecture, 25 ticks
        player.position_ms = 1000 + tick * 40           # le lecteur avance seul
        panel.preview_at("a.mp4", 1.0 + tick * 0.04, playing=True)
    assert player.seeks == [1000]                       # aucun seek pendant la lecture
    player.position_ms = 3000                           # dérive au-delà du seuil
    panel.preview_at("a.mp4", 2.0, playing=True)
    assert player.seeks[-1] == 2000 and abs(3000 - 2000) > PLAYBACK_DRIFT_SECONDS * 1000
    panel.preview_at("a.mp4", 2.5)                      # en pause : position exacte
    assert player.seeks[-1] == 2500


# --- Fenêtre ------------------------------------------------------------------------------------------------


def test_window_uses_the_cpu_monitor_offscreen_and_reports_everything(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    assert not window.preview_panel.gpu_active
    text = window.hardware_diagnostics_text()
    for section in ("— Décodage —", "— Aperçu —", "— Mémoire —", "Export : décodage CPU"):
        assert section in text
    assert "actif : CPU" in text


@pytest.fixture
def french_language():
    """Les tests de langue rendent la main en français (la langue est un état global)."""
    from ui import i18n

    i18n.reset_for_tests()
    yield i18n
    i18n.reset_for_tests()


def test_hardware_diagnostics_follow_the_current_language(qtbot, monkeypatch, tmp_path, fake_proxies, french_language):
    """Le diagnostic copiable est calculé à la demande : il suit la langue, sans clé manquante ni repli."""
    from core.memory_monitor import NORMAL, MemoryStatus

    # La mémoire libre est lue à chaque appel : sans lecture figée, deux appels successifs différaient de 0,1 Go
    # et le test, qui compare le texte français avant / après, échouait de façon intermittente.
    monkeypatch.setattr("core.memory_monitor.read_memory_status",
                        lambda: MemoryStatus(24 * 2**30, 12 * 2**30, None, NORMAL, "test"))
    window = _window(qtbot, monkeypatch, tmp_path)
    french = window.hardware_diagnostics_text()
    expected = {"en": ("— Decoding —", "— Preview —", "— Memory —", "Export: CPU decoding"),
                "es": ("— Decodificación —", "— Vista previa —", "— Memoria —", "Exportación: decodificación por CPU")}
    for language, sections in expected.items():
        french_language.set_language(language)
        with french_language.strict_translations():
            text = window.hardware_diagnostics_text()
        assert all(section in text for section in sections), (language, text)
        assert "— Décodage —" not in text and text != french
    french_language.set_language("fr")
    assert window.hardware_diagnostics_text() == french


def test_the_unavailable_decode_backend_and_the_gpu_events_are_translated(qtbot, monkeypatch, tmp_path, fake_proxies,
                                                                          fake_gpu, french_language):
    from core.decode_policy import DecodeMode

    window = _window(qtbot, monkeypatch, tmp_path)
    french_language.set_language("en")
    panel = window.preview_panel
    panel.enable_gpu("metal")
    window._memory_watch = MemoryWatch(reader=lambda: MemoryStatus(100, 2, None, CRITICAL, "test"))
    window._poll_memory()
    assert window._gpu_health.events()[-1].detail == "critical memory pressure"
    assert "GPU failure (out_of_memory): critical memory pressure" in window.hardware_diagnostics_text()
    window._decode_mode = DecodeMode.AUTO
    assert [label for _value, label in window.decode_mode_options()][0] == "Auto"


def test_preferences_offer_decode_and_preview_choices(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    _dialog, tab = _prefs(qtbot, window)
    values = [tab.decode_combo.itemData(i) for i in range(tab.decode_combo.count())]
    assert values[:2] == ["auto", "cpu"]
    assert [tab.preview_backend_combo.itemData(i) for i in range(3)] == ["auto", "cpu", "gpu"]
    tab.decode_combo.setCurrentIndex(1)
    tab._on_decode_changed(1)
    assert load_user_settings().decode_mode == "cpu" and window._decode_context.mode.value == "cpu"
    assert tab.decode_hint.text()  # le moniteur temps réel l'appliquera au prochain démarrage
    tab.preview_backend_combo.setCurrentIndex(2)
    tab._on_preview_backend_changed(2)
    assert load_user_settings().preview_backend == "gpu"
    assert not window.preview_panel.gpu_active  # offscreen : repli CPU, visible dans le diagnostic
    assert "Repli :" in window.hardware_diagnostics_text()


def test_window_survives_a_gpu_failure_and_tells_the_user(qtbot, monkeypatch, tmp_path, fake_proxies, fake_gpu):
    window = _window(qtbot, monkeypatch, tmp_path)
    panel = window.preview_panel
    assert panel.enable_gpu("metal")
    fake_gpu.instances[0].failed.emit("render", "pipeline refusé")
    assert not panel.gpu_active
    assert window._gpu_health.failures == 1
    assert "render" in window.statusBar().currentMessage()
    window._sync_preview_to_timeline()  # la session continue


def test_memory_pressure_frees_caches_and_drops_the_gpu(qtbot, monkeypatch, tmp_path, fake_proxies, fake_gpu):
    window = _window(qtbot, monkeypatch, tmp_path)
    panel = window.preview_panel
    panel.enable_gpu("metal")
    view = fake_gpu.instances[0]
    window._memory_watch = MemoryWatch(reader=lambda: MemoryStatus(100, 2, None, CRITICAL, "test"))
    window._poll_memory()
    assert view.cache_purges == 1 and not panel.gpu_active
    assert window._gpu_health.events()[-1].kind == "out_of_memory"


def test_a_new_project_releases_gpu_textures(qtbot, monkeypatch, tmp_path, fake_proxies, fake_gpu):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.preview_panel.enable_gpu("metal")
    view = fake_gpu.instances[0]
    window._release_gpu_for_project_change()
    assert view.cache_purges >= 1 and "main" in view.forgotten


def test_faithful_preview_renders_off_the_interface_thread(qtbot, monkeypatch, tmp_path, fake_proxies):
    """Seuil : le repli CPU (segments FFmpeg) ne bloque jamais l'interface."""
    window = _window(qtbot, monkeypatch, tmp_path)
    engine = window.preview_engine
    started = threading.Event()
    threads = []

    def slow_render(job, token):
        threads.append(threading.current_thread())
        started.set()
        time.sleep(0.4)
        return None

    engine.render_fn = slow_render
    engine._uses_default_render = False
    from core.preview_cache import PreviewSegmentKey
    from core.preview_engine import PreviewJob

    engine.request(PreviewJob(key=PreviewSegmentKey("c", 0.0, 2.0, "draft", "slow-test"), plan=None))
    before = time.perf_counter()
    window._pump_preview_queue()
    assert time.perf_counter() - before < 0.05  # la fenêtre reprend la main tout de suite
    assert started.wait(2.0)
    assert threads[0] is not threading.main_thread()


def test_project_format_is_unchanged_by_the_gpu_work():
    from core.project_io import CURRENT_VERSION

    assert CURRENT_VERSION == 16  # préférences hors projet : les anciens projets s'ouvrent tels quels
