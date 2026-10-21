"""Le moniteur GPU encaisse un accident isolé et se souvient de l'image quand Qt libère ses ressources.

Deux défauts constatés :

- une seule image dont ``map()`` échoue levait ``GpuUnavailable`` : le widget se déclarait cassé pour toute la
  session (retour définitif au moniteur CPU) ;
- Qt appelle ``releaseResources`` quand le widget est masqué ou détaché, puis le recrée. Le widget vidait alors ses
  sources et ses images en attente : en pause, plus aucune image n'arrive, le moniteur restait noir.
"""

from __future__ import annotations

import gpu_harness
import pytest
from PySide6.QtGui import QRhi, QRhiNullInitParams
from PySide6.QtMultimedia import QVideoFrame

from ui import i18n
from ui.gpu_preview import GpuPreviewWidget, GpuUnavailable, RhiExecutor


@pytest.fixture
def executor(qapp):
    try:
        rhi = QRhi.create(QRhi.Implementation.Null, QRhiNullInitParams())
        return RhiExecutor(rhi)
    except Exception as error:  # noqa: BLE001 - un Qt sans backend Null ne doit pas faire échouer la suite
        pytest.skip(f"QRhi Null indisponible : {error}")


def _frame():
    return gpu_harness.make_frame(gpu_harness.test_pattern(64, 36), "nv12")


def test_an_unmappable_frame_is_converted_by_qt_instead_of_failing_the_gpu(executor, monkeypatch):
    frame = _frame()
    monkeypatch.setattr(QVideoFrame, "map", lambda self, mode: False)        # surface non mappable
    unmapped = []
    monkeypatch.setattr(QVideoFrame, "unmap", lambda self: unmapped.append(1))

    sent, source = executor.upload_source(executor.rhi.nextResourceUpdateBatch(), "main", frame)

    assert source.layout == "rgba" and (source.width, source.height) == (64, 36)
    assert sent > 0
    assert executor.unmappable_frames == 1
    assert unmapped == []                                                      # rien n'a été mappé, rien à rendre


def test_a_mappable_frame_still_goes_through_the_native_planes_and_is_unmapped(executor, monkeypatch):
    frame = _frame()
    calls = []
    real_unmap = QVideoFrame.unmap
    monkeypatch.setattr(QVideoFrame, "unmap", lambda self: (calls.append(1), real_unmap(self))[1])

    _sent, source = executor.upload_source(executor.rhi.nextResourceUpdateBatch(), "main", frame)

    assert source.layout == "nv12" and executor.unmappable_frames == 0
    assert calls == [1]


@pytest.fixture
def widget(qtbot):
    view = GpuPreviewWidget(api="metal")
    qtbot.addWidget(view)
    return view


def test_qt_releasing_its_resources_keeps_the_last_frame_of_each_source(widget):
    first, second = _frame(), _frame()
    widget.set_video_frame("a", first)
    widget.set_video_frame("b", second)
    widget._pending.clear()                         # rendu fait : les images ont été envoyées au GPU
    widget._sources["a"] = object()                 # type: ignore[assignment] - ressource GPU « vivante »

    widget.releaseResources()                       # Qt masque / détache le widget

    assert widget.executor is None and widget._sources == {}
    assert set(widget._pending) == {"a", "b"}       # elles repartiront au prochain rendu, même en pause
    assert widget._pending["a"].frame is first


def test_an_explicit_release_drops_the_decoded_frames_too(widget):
    widget.set_video_frame("a", _frame())
    widget.release_gpu()                            # changement de projet, pression mémoire
    assert widget._pending == {} and widget._latest == {}


def test_forgetting_a_source_forgets_its_remembered_frame(widget):
    widget.set_video_frame("a", _frame())
    widget.forget_source("a")
    widget.releaseResources()
    assert "a" not in widget._pending and "a" not in widget._latest


class _RhiThatRunsOutOfMemory:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def nextResourceUpdateBatch(self):  # noqa: N802 - API Qt
        raise self.error


def _failure_of(widget, qtbot, error: Exception):
    from types import SimpleNamespace

    widget.executor = SimpleNamespace(rhi=_RhiThatRunsOutOfMemory(error))
    with qtbot.waitSignal(widget.failed, timeout=2000) as blocker:
        widget.render(None)
    return tuple(blocker.args)


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_a_gpu_memory_failure_is_classified_out_of_memory_in_every_language(widget, qtbot, language):
    """Le classement ne dépend plus du mot « mémoire » du message : traduit, il restait classé « render »."""
    i18n.reset_for_tests()
    try:
        i18n.set_language(language)
        message = i18n.translate("gpu.error.video_texture")
        kind, detail = _failure_of(widget, qtbot, GpuUnavailable(message, out_of_memory=True))
    finally:
        i18n.reset_for_tests()
    assert (kind, detail) == ("out_of_memory", message)


def test_a_failure_that_only_mentions_memory_is_not_an_out_of_memory(widget, qtbot):
    kind, _detail = _failure_of(widget, qtbot, GpuUnavailable("mémoire insuffisante pour la bibliothèque"))
    assert kind == "render"


def test_the_gpu_failure_causes_are_translated():
    i18n.reset_for_tests()
    try:
        i18n.set_language("en")
        with i18n.strict_translations():
            assert str(GpuUnavailable(i18n.translate("gpu.error.texture", width=64, height=36))) == (
                "cannot create a 64×36 texture (GPU memory?)")
        i18n.set_language("fr")
        assert i18n.translate("gpu.error.texture", width=64, height=36) == "texture 64×36 impossible (mémoire GPU ?)"
    finally:
        i18n.reset_for_tests()
