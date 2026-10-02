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

from ui.gpu_preview import GpuPreviewWidget, RhiExecutor


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
