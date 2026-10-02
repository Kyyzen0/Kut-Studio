"""Interface de l'encodage matériel : panneau Export, file de rendu, préférences, fenêtre."""

from __future__ import annotations

import pytest
from test_hardware_encoding import FakeFFmpeg
from test_performance_ui import _prefs, _window, fake_proxies  # noqa: F401 - fixtures partagées
from test_render_queue import fake_ffmpeg, make_queue  # noqa: F401

from core.hardware_cache import CapabilityService, set_default_service
from core.hardware_encoding import HardwareCapabilities, HardwareEncoder, detect_capabilities
from core.render_job import ErrorKind, JobStatus
from core.render_presets import get_preset, with_hardware
from core.render_queue_store import RenderQueueStore
from core.user_settings import load_user_settings
from ui import i18n
from ui.export_panel import ExportPanel
from ui.preferences_dialog import PreferencesDialog
from ui.render_queue_panel import RenderQueuePanel


def _capabilities(*families, failing=()):
    return detect_capabilities(
        ["ffmpeg"], runner=FakeFFmpeg(*families, failing=failing), platform_name="win32", machine="x86_64"
    )


def _service(tmp_path, *families, failing=()):
    return CapabilityService(
        command_provider=lambda: ["fake-ffmpeg"], cache_path=tmp_path / "caps.json",
        runner=FakeFFmpeg(*families, failing=failing), environment={},
    )


def _options(combo):
    return [combo.itemData(i) for i in range(combo.count())]


# --- Panneau Export ---------------------------------------------------------------------------------------


@pytest.fixture
def export_panel(qtbot):
    panel = ExportPanel()
    qtbot.addWidget(panel)
    panel.show()
    return panel


def test_export_panel_offers_automatic_and_cpu_until_hardware_is_detected(export_panel):
    assert _options(export_panel.encoder_combo) == ["auto", "cpu"]
    assert export_panel.encoder_combo.itemText(0) == i18n.translate("render.encoder.auto")
    assert export_panel.current_spec().hardware == "auto"          # défaut : Automatique


def test_export_panel_only_lists_encoders_that_really_exist(export_panel):
    export_panel.set_capabilities(_capabilities("nvenc", "qsv", failing=("qsv",)))
    assert _options(export_panel.encoder_combo) == ["auto", "cpu", "nvenc"]   # QSV listé mais invalide
    assert export_panel.encoder_combo.itemText(2) == "NVIDIA NVENC – H.264"
    assert export_panel.encoder_combo.itemText(1) == "CPU – libx264"
    export_panel.set_capabilities(HardwareCapabilities())
    assert _options(export_panel.encoder_combo) == ["auto", "cpu"]


def test_choosing_an_encoder_updates_the_spec_and_notifies_the_window(export_panel):
    export_panel.set_capabilities(_capabilities("nvenc"))
    seen = []
    export_panel.encoder_changed.connect(seen.append)
    export_panel.encoder_combo.setCurrentIndex(2)
    export_panel.encoder_combo.activated.emit(2)
    assert seen == ["nvenc"] and export_panel.current_spec().hardware == "nvenc"
    assert export_panel.current_spec().quality == get_preset("h264_1080p").quality   # preset intact
    export_panel.set_default_encoder("cpu")                       # valeur mémorisée : pas de signal
    assert seen == ["nvenc"] and export_panel.current_encoder() == "cpu"


def test_prores_only_offers_the_cpu_and_the_preference_is_kept(export_panel):
    export_panel.set_capabilities(_capabilities("nvenc"))
    export_panel.set_default_encoder("nvenc")
    export_panel.preset_combo.setCurrentIndex(export_panel.preset_combo.findData("prores_master"))
    assert _options(export_panel.encoder_combo) == ["cpu"]
    assert export_panel.current_spec().hardware == "cpu"
    export_panel.preset_combo.setCurrentIndex(export_panel.preset_combo.findData("youtube"))
    assert export_panel.current_encoder() == "nvenc"              # revenu sur un format H.264


def test_an_unavailable_saved_choice_shows_a_valid_option_without_erasing_the_preference(export_panel):
    export_panel.set_default_encoder("amf")                       # AMF absent de cette machine
    assert export_panel.current_encoder() == "auto"
    export_panel.set_capabilities(_capabilities("amf"))           # …puis détecté : on le retrouve
    assert export_panel.current_encoder() == "amf"


def test_custom_preset_keeps_its_free_settings_with_the_selected_encoder(export_panel):
    from core.render_presets import CUSTOM_PRESET_ID

    export_panel.preset_combo.setCurrentIndex(export_panel.preset_combo.findData(CUSTOM_PRESET_ID))
    export_panel.set_default_encoder("cpu")
    spec = export_panel.current_spec()
    assert spec.id == CUSTOM_PRESET_ID and spec.hardware == "cpu"


def test_encoder_label_follows_the_language(export_panel):
    export_panel.retranslate()
    before = export_panel.encoder_label.text()
    i18n.set_language("en")
    try:
        export_panel.retranslate()
        assert export_panel.encoder_label.text() == "Encoder" and before != "Encoder"
        assert export_panel.encoder_combo.itemText(0) == "Automatic"
    finally:
        i18n.set_language("fr")


# --- File de rendu ----------------------------------------------------------------------------------------------------


@pytest.fixture
def queue_panel(qtbot, make_queue, fake_ffmpeg):
    queue = make_queue()
    panel = RenderQueuePanel(queue)
    qtbot.addWidget(panel)
    panel.show()
    return queue, panel


def _make_job(queue, tmp_path, hardware="auto"):
    from test_render_queue import _out, _project

    return queue.enqueue(_project(tmp_path), with_hardware(get_preset("h264_1080p"), hardware), _out(tmp_path, "x.mp4"))


def test_queue_shows_the_encoder_discreetly_only_once_it_is_known(queue_panel, tmp_path):
    queue, panel = queue_panel
    job = _make_job(queue, tmp_path)
    encoder_column = panel.tree.headerItem().text(5)
    assert encoder_column == i18n.translate("render.col.encoder")
    assert panel.tree.topLevelItem(0).text(5) == ""               # jamais lancé : rien n'est affiché
    job.hardware_used, job.encoder = "videotoolbox", "h264_videotoolbox"
    queue.job_updated.emit(job.id)
    assert panel.tree.topLevelItem(0).text(5) == "H.264 · VideoToolbox"
    job.hardware_used, job.encoder = "cpu", "libx264"
    job.fallback_reason = "VideoToolbox n'a pas pu démarrer : rendu CPU utilisé."
    queue.job_updated.emit(job.id)
    item = panel.tree.topLevelItem(0)
    assert item.text(5) == "H.264 · CPU" and "VideoToolbox" in item.toolTip(5)
    panel.tree.setCurrentItem(item)
    assert "H.264 · CPU" in panel.detail_label.text() and "VideoToolbox" in panel.detail_label.text()


def test_retry_on_cpu_is_only_offered_after_an_explicit_encoder_failure(qtbot, queue_panel, tmp_path):
    queue, panel = queue_panel
    job = _make_job(queue, tmp_path, "nvenc")
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    assert panel.retry_cpu_button.isHidden()
    job.mark_failed("NVENC indisponible", ErrorKind.ENCODER)
    queue.job_updated.emit(job.id)
    assert not panel.retry_cpu_button.isHidden() and "NVENC" in panel.detail_label.text()
    assert i18n.translate("render.kind.encoder") in panel.detail_label.text()
    panel.retry_cpu_button.click()
    qtbot.waitUntil(lambda: job.status is JobStatus.COMPLETED, timeout=20000)
    assert job.hardware == "cpu" and job.encoder_label == "H.264 · CPU"
    job.mark_failed("autre erreur", ErrorKind.FFMPEG)
    queue.job_updated.emit(job.id)
    assert panel.retry_cpu_button.isHidden()


def test_copy_error_includes_the_encoder_diagnostics(qtbot, queue_panel, tmp_path):
    from PySide6.QtGui import QGuiApplication

    queue, panel = queue_panel
    job = _make_job(queue, tmp_path, "nvenc")
    job.mark_failed("Échec", ErrorKind.ENCODER)
    job.diagnostics = "Cannot load libcuda.so.1"
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    panel._on_copy_error()
    text = QGuiApplication.clipboard().text()
    assert "Échec" in text and "libcuda" in text


# --- Fenêtre principale et préférences -------------------------------------------------------------------------------------------


def test_window_detects_in_the_background_and_feeds_the_export_panel(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: "videotoolbox" in _options(window.export_panel.encoder_combo), timeout=8000)
    assert window.hardware_capabilities().is_usable("h264", HardwareEncoder.VIDEOTOOLBOX)


def test_choice_of_encoder_is_saved_and_restored(qtbot, monkeypatch, tmp_path, fake_proxies):
    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: "videotoolbox" in _options(window.export_panel.encoder_combo), timeout=8000)
    combo = window.export_panel.encoder_combo
    combo.setCurrentIndex(combo.findData("videotoolbox"))
    combo.activated.emit(combo.currentIndex())
    assert load_user_settings().export_encoder == "videotoolbox"
    assert window.export_panel.current_spec().hardware == "videotoolbox"
    reopened = _window(qtbot, monkeypatch, tmp_path)
    assert reopened.export_panel.current_encoder() == "videotoolbox"


def test_launching_an_export_uses_the_selected_encoder(qtbot, monkeypatch, tmp_path, fake_proxies, fake_ffmpeg):
    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    window.export_panel.set_default_encoder("cpu")
    monkeypatch.setattr(window, "_ask_export_path", lambda spec: str(tmp_path / "out.mp4"))
    job = window._enqueue_current_export(start=False)
    assert job.hardware == "cpu"
    window.export_panel.set_default_encoder("auto")
    monkeypatch.setattr(window, "_ask_export_path", lambda spec: str(tmp_path / "out2.mp4"))
    assert window._enqueue_current_export(start=False).hardware == "auto"


def test_a_cpu_fallback_is_announced_to_the_user(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.render_queue.encoder_fallback.emit("job", "VideoToolbox n'a pas pu démarrer : rendu CPU utilisé.")
    assert "VideoToolbox" in window.export_panel.status_label.text()
    assert "CPU" in window.export_panel.status_label.text()


def test_preferences_show_diagnostics_and_only_available_encoders(qtbot, monkeypatch, tmp_path, fake_proxies):
    set_default_service(_service(tmp_path, "videotoolbox", failing=()))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: window.hardware_capabilities() is not None, timeout=8000)
    _dialog, tab = _prefs(qtbot, window)
    text = tab.diagnostics_view.toPlainText()
    assert "h264_videotoolbox" in text and "OK" in text and "Auto pour H.264 : videotoolbox" in text
    assert _options(tab.encoder_combo) == ["auto", "cpu", "videotoolbox"]
    # Court (encodage, décodage, aperçu, mémoire) : jamais la sortie brute de FFmpeg.
    assert len(text.splitlines()) < 40 and "Copyright" not in text
    tab.encoder_combo.setCurrentIndex(2)
    tab.encoder_combo.activated.emit(2)
    assert load_user_settings().export_encoder == "videotoolbox"
    assert tab.encoding_box.title() == i18n.translate("perf.hardware.title")  # décodage, aperçu, encodage


def test_redetect_button_runs_a_fresh_detection(qtbot, monkeypatch, tmp_path, fake_proxies):
    runner = FakeFFmpeg("videotoolbox")
    service = CapabilityService(command_provider=lambda: ["fake-ffmpeg"], cache_path=tmp_path / "c.json",
                                runner=runner, environment={})
    set_default_service(service)
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: service.scan_count == 1 and not window._detecting_hardware, timeout=8000)
    dialog, tab = _prefs(qtbot, window)
    window._preferences_dialog = dialog                            # comme show_preferences()
    runner.listing = FakeFFmpeg().listing                          # le GPU « disparaît »
    tab.redetect_button.click()
    qtbot.waitUntil(lambda: service.scan_count == 2 and not window._detecting_hardware, timeout=8000)
    assert _options(tab.encoder_combo) == ["auto", "cpu"]
    assert "videotoolbox" not in tab.diagnostics_view.toPlainText().split("Auto pour")[0].lower()


def test_a_detection_result_arriving_after_the_preferences_closed_is_harmless(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    """Régression : la boîte (WA_DeleteOnClose) était détruite, la fenêtre gardait sa référence."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: window.hardware_capabilities() is not None, timeout=8000)
    # Pas de qtbot.addWidget : le test détruit lui-même la boîte, qtbot ne doit pas la refermer.
    dialog = PreferencesDialog(shortcut_manager=window.shortcuts, performance_host=window)
    window._preferences_dialog = dialog
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)
    window._refresh_encoding_settings_tab()          # ne doit rien lever : la boîte n'existe plus


def test_diagnostics_can_be_copied(qtbot, monkeypatch, tmp_path, fake_proxies):
    from PySide6.QtGui import QGuiApplication

    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: window.hardware_capabilities() is not None, timeout=8000)
    _dialog, tab = _prefs(qtbot, window)
    tab.copy_diagnostics_button.click()
    assert "h264_videotoolbox" in QGuiApplication.clipboard().text()


def test_closing_during_detection_is_harmless(qtbot, monkeypatch, tmp_path, fake_proxies):
    import threading

    gate = threading.Event()
    runner = FakeFFmpeg("videotoolbox")
    original = runner.__call__

    def slow(command, timeout):
        gate.wait(5)
        return original(command, timeout)

    set_default_service(CapabilityService(command_provider=lambda: ["fake-ffmpeg"],
                                          cache_path=tmp_path / "c.json", runner=slow, environment={}))
    window = _window(qtbot, monkeypatch, tmp_path)
    window._shutdown_encoding()
    gate.set()
    qtbot.wait(300)                                                # la détection se termine après la « fermeture »
    assert window._encoding_closed


def test_every_new_translation_exists_in_all_languages():
    keys = ["render.kind.encoder", "render.col.encoder", "render.btn.retry_cpu", "render.export.encoder",
            "render.encoder.auto", "encoding.detecting", "encoding.fallback_notice", "perf.encoding.title",
            "perf.encoding.default", "perf.encoding.redetect", "perf.encoding.copy"]
    for key in keys:
        entry = i18n._TRANSLATIONS[key]
        assert set(entry) >= {"fr", "en", "es"} and all(entry.values()), key


def test_changing_the_encoder_in_preferences_updates_the_export_selector(qtbot, monkeypatch, tmp_path, fake_proxies):
    set_default_service(_service(tmp_path, "videotoolbox"))
    window = _window(qtbot, monkeypatch, tmp_path)
    qtbot.waitUntil(lambda: "videotoolbox" in _options(window.export_panel.encoder_combo), timeout=8000)
    _dialog, tab = _prefs(qtbot, window)
    tab.encoder_combo.setCurrentIndex(tab.encoder_combo.findData("videotoolbox"))
    tab.encoder_combo.activated.emit(tab.encoder_combo.currentIndex())
    assert window.export_panel.current_encoder() == "videotoolbox"
    assert window.export_panel.current_spec().hardware == "videotoolbox"   # les prochains jobs l'utilisent
    tab.encoder_combo.setCurrentIndex(tab.encoder_combo.findData("cpu"))
    tab.encoder_combo.activated.emit(tab.encoder_combo.currentIndex())
    assert window.export_panel.current_spec().hardware == "cpu"
