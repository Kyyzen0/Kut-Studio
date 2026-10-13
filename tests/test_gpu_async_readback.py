"""Relecture GPU asynchrone (ADR-0001) : file d'attente et profondeur, sans GPU réel."""

from __future__ import annotations

from ui.gpu_preview import GpuPreviewWidget


def test_queue_readback_tracks_pending_entries(qapp):
    widget = GpuPreviewWidget(api="opengl")
    first = widget.queue_readback(0)
    second = widget.queue_readback(1)
    assert first["token"] == 0 and second["token"] == 1
    assert first["done"] is False and second["done"] is False
    assert widget.pending_readbacks() == 2
    first["done"] = True
    assert widget.pending_readbacks() == 1
    widget.deleteLater()


def test_finish_readback_drops_completed_entries(qapp):
    """Les entrées terminées quittent la file (l'appelant garde sa référence)."""
    widget = GpuPreviewWidget(api="opengl")
    first = widget.queue_readback(0)
    second = widget.queue_readback(1)
    first["image"] = object()
    widget._finish_readback(first)
    assert first["done"] is True
    assert first not in widget._readback_queue
    assert second in widget._readback_queue
    assert widget.pending_readbacks() == 1
    # La référence appelant conserve l'image même hors file.
    assert first["image"] is not None
    widget.deleteLater()


def test_gpu_exporter_accepts_readback_depth_argument():
    import inspect

    from tools.perf.gpu_export_spike import GpuExporter, run_gpu

    assert "readback_depth" in inspect.signature(GpuExporter.__init__).parameters
    assert "readback_depth" in inspect.signature(run_gpu).parameters
