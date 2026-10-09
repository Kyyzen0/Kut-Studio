"""Étalonnage dans le moniteur GPU, côté interface : panneau, résolution de la LUT par la vue, fenêtre principale."""

from __future__ import annotations

import threading

import pytest
from test_gpu_preview_ui import FakeGpuView, _panel, fake_gpu  # noqa: F401 - fixture partagée

from core.color_grading import ColorGrade
from core.gpu_composite import AdjustmentLayer, CompositeLayer, VideoSource
from core.gpu_effects import program_for
from core.gpu_grade import DOMAIN_RGB, DOMAIN_YUV, LUT_SIZE, GradeBakeError, GradeLutCache, lut_key

GRADE = ColorGrade(exposure=0.4, saturation=1.2)


def test_the_panel_hands_the_displayed_clip_grade_to_the_gpu_monitor(qtbot, fake_gpu):
    from PySide6.QtCore import QSize
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat

    panel = _panel(qtbot)
    panel.enable_gpu("metal")
    view = fake_gpu.instances[0]
    panel._timeline_preview_path = "clip.mp4"
    panel._on_gpu_frame(QVideoFrame(QVideoFrameFormat(QSize(1920, 1080), QVideoFrameFormat.PixelFormat.Format_NV12)))
    panel.set_color_grade(GRADE)
    assert view.last.layers[0].grade is GRADE
    panel.set_color_grade(ColorGrade())
    assert view.last.layers[0].grade is None, "un étalonnage neutre n'a pas de passe"
    panel.set_color_grade(GRADE.with_enabled(False))
    assert view.last.layers[0].grade is None
    panel.set_adjustments([("adj", (), None, 1.0, GRADE)])
    assert view.last.adjustments[0].grade is GRADE
    panel.set_adjustments([("adj", (), None, 1.0)])
    assert view.last.adjustments[0].grade is None, "l'ancien format (sans étalonnage) reste lu"


class _Baker:
    def __init__(self, *, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail
        self.done = threading.Event()

    def __call__(self, grade, **options):
        self.calls.append(options)
        self.done.set()
        if self.fail:
            raise GradeBakeError("LUT illisible")
        return bytes(LUT_SIZE * LUT_SIZE * LUT_SIZE * 3)


def _wait(condition) -> bool:
    event = threading.Event()
    for _ in range(500):
        if condition():
            return True
        event.wait(0.01)
    return condition()


@pytest.fixture
def widget(qtbot):
    from ui.gpu_preview import GpuPreviewWidget

    widget = GpuPreviewWidget(api="metal")
    qtbot.addWidget(widget)
    widget._sources["main"] = VideoSource("main", "nv12", 64, 36, "bt709", "video")
    widget._sources["still"] = VideoSource("still", "rgba", 64, 36)
    return widget


def _layer(grade, source="main") -> CompositeLayer:
    return CompositeLayer(source, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (0, 0, 64, 36), program=program_for(()), grade=grade)


def test_the_view_bakes_the_lut_in_the_colour_space_of_the_media_and_then_inserts_the_pass(widget):
    baker = _Baker()
    widget.grades = GradeLutCache(bake=baker)
    images: dict = {}
    pending = widget._resolve_grade(_layer(GRADE), images)
    assert pending.grade is None and pending.grade_lut == "" and not images, "pas encore cuite : pas de passe"
    assert _wait(lambda: widget.grades.lookup(GRADE, domain=DOMAIN_YUV, colorspace="bt709",
                                              color_range="video") is not None)
    assert baker.calls == [{"domain": DOMAIN_YUV, "colorspace": "bt709", "color_range": "video"}]
    ready = widget._resolve_grade(_layer(GRADE), images)
    key = f"lut:{lut_key(GRADE, colorspace='bt709', color_range='video')}"
    assert ready.grade_lut == key and ready.grade is None
    assert (images[key].width(), images[key].height()) == (LUT_SIZE * LUT_SIZE, LUT_SIZE)

    rgb = widget._resolve_grade(_layer(GRADE, "still"), {})
    assert rgb.grade_lut == "" and _wait(lambda: len(baker.calls) == 2)
    assert baker.calls[1] == {"domain": DOMAIN_RGB, "colorspace": "", "color_range": ""}, "média RVB : LUT en RVB"
    adjustment = widget._resolve_grade(AdjustmentLayer(program_for(()), grade=GRADE), {})
    assert adjustment.grade is None


def test_while_a_new_grade_bakes_the_previous_lut_stays_on_screen(widget):
    baker = _Baker()
    widget.grades = GradeLutCache(bake=baker)
    widget._resolve_grade(_layer(GRADE), {})
    assert _wait(lambda: len(baker.calls) == 1)
    first = None
    for _ in range(500):
        first = widget._resolve_grade(_layer(GRADE), {})
        if first.grade_lut:
            break
        threading.Event().wait(0.01)
    assert first is not None and first.grade_lut
    slower = threading.Event()
    widget.grades._bake = lambda grade, **options: (slower.wait(5), bytes(LUT_SIZE ** 3 * 3))[1]
    images: dict = {}
    moving = widget._resolve_grade(_layer(GRADE.with_field("exposure", 0.9)), images)
    assert moving.grade_lut == first.grade_lut and first.grade_lut in images, "pas de clignotement sans étalonnage"
    slower.set()


def test_a_grade_that_cannot_be_baked_is_simply_not_shown(widget):
    widget.grades = GradeLutCache(bake=_Baker(fail=True))
    widget._resolve_grade(_layer(GRADE), {})
    assert _wait(lambda: widget.grades.failed(GRADE, domain=DOMAIN_YUV, colorspace="bt709", color_range="video"))
    shown = widget._resolve_grade(_layer(GRADE), {})
    assert shown.grade is None and shown.grade_lut == ""


def test_the_main_window_gives_the_monitor_the_grade_of_the_clip_under_the_playhead(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    monkeypatch.setattr(window.preview_panel, "preview_at", lambda *_a, **_k: None)
    monkeypatch.setattr(window, "_present_cached_preview_at", lambda *_a, **_k: False)
    clip = window.project.tracks[0].clips[0]
    asset = next(item for item in window.project.media_assets if item.id == clip.asset_id)
    asset.path = str(tmp_path / "intro.mp4")             # le projet d'exemple n'a pas de fichiers : un chemin suffit
    window._reload_timeline_preserving_selection()
    clip.color_grade = GRADE
    window.playhead_seconds = clip.timeline_start + 0.1
    window._sync_preview_to_timeline()
    assert window.preview_panel._gpu_grade is GRADE
    clip.color_grade = None
    window._sync_preview_to_timeline()
    assert window.preview_panel._gpu_grade is None
