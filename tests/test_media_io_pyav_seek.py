"""PyAV : décoder jusqu'au PTS demandé (pas seulement la clé après seek)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import media_io


class _FakeFrame:
    conversions = 0

    def __init__(self, time: float, width: int = 4, height: int = 2) -> None:
        self.time = time
        self._w = width
        self._h = height

    def to_ndarray(self, format="rgba"):  # noqa: A002 - API PyAV
        import numpy as np

        assert format == "rgba"
        type(self).conversions += 1
        return np.zeros((self._h, self._w, 4), dtype=np.uint8)


class _FakeContainer:
    def __init__(self, times: list[float]) -> None:
        self._times = times
        self.streams = SimpleNamespace(video=[SimpleNamespace(time_base=1 / 30)])
        self.seek_target = None

    def seek(self, target, stream=None) -> None:  # noqa: ARG002
        self.seek_target = target

    def decode(self, stream):  # noqa: ARG002
        for t in self._times:
            yield _FakeFrame(t)


def test_decode_pyav_advances_to_requested_pts(monkeypatch):
    if media_io._av is None:
        pytest.skip("PyAV absent")
    _FakeFrame.conversions = 0
    handle = media_io.MediaHandle.__new__(media_io.MediaHandle)
    handle.path = "fake.mp4"
    handle.info = media_io.MediaInfo("fake.mp4", 10.0, 4, 2, 30.0)
    # Seek tombe sur 0.0 ; la demande est 0.20 → attendre la frame 0.20, pas 0.0.
    handle._container = _FakeContainer([0.0, 0.1, 0.2, 0.3])
    frame = handle._decode_pyav(0.20)
    assert abs(frame.time_seconds - 0.2) < 1e-6
    assert _FakeFrame.conversions == 1  # une seule conversion RVBA


def test_decode_pyav_keeps_closest_when_overshooting(monkeypatch):
    if media_io._av is None:
        pytest.skip("PyAV absent")
    _FakeFrame.conversions = 0
    handle = media_io.MediaHandle.__new__(media_io.MediaHandle)
    handle.path = "fake.mp4"
    handle.info = media_io.MediaInfo("fake.mp4", 10.0, 4, 2, 30.0)
    # Pas de frame exacte : 0.19 puis 0.25 pour une demande 0.20 → 0.19 plus proche.
    handle._container = _FakeContainer([0.0, 0.19, 0.25])
    frame = handle._decode_pyav(0.20)
    assert abs(frame.time_seconds - 0.19) < 1e-6
    assert _FakeFrame.conversions == 1
