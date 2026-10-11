#!/usr/bin/env python3
"""Experimental PySide6 host for the native C++ preview engine (POC v0.1).

Thread ownership
----------------
* Native decode thread: owned by PreviewEngine (C++).
* Present pump: QTimer on the Qt UI thread calls ``pump_present()`` — this
  copies the latest RGB frame into a QImage for display. That UI-thread copy
  is intentional and counted in native metrics (snapshot + presenter copies).
* Native resources: PreviewEngine RAII; ``close()`` / process exit joins the
  decode thread before destroying FFmpeg contexts.

This app is NOT wired into Kut-Studio production UI.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
for candidate in (BUILD, ROOT / "build" / "Release", ROOT / "build" / "Debug"):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    import kut_engine_native as native
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        f"Cannot import kut_engine_native ({exc}). Build the experiment first:\n"
        f"  cmake -S {ROOT} -B {ROOT}/build && cmake --build {ROOT}/build -j"
    ) from exc


def _rss_mb() -> float:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux: KB; macOS: bytes
        if sys.platform == "darwin":
            return usage / (1024 * 1024)
        return usage / 1024
    except Exception:
        return 0.0


class PreviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Kut native engine POC v0.1")
        self.engine: native.PreviewEngine | None = None
        self._last_pump = time.perf_counter()
        self._stalls: list[float] = []
        self._period = 1.0 / 30.0  # remplacée à l'ouverture par la cadence de la source
        self._next_due = 0.0

        self.video = QLabel("Open a video…")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(640, 360)
        self.video.setStyleSheet("background:#111;color:#ddd;")

        self.status = QLabel("Idle")
        self.status.setWordWrap(True)

        self.decode_mode = QComboBox()
        self.decode_mode.addItem("Software", native.DecodeMode.Software)
        self.decode_mode.addItem("HardwarePrefer", native.DecodeMode.HardwarePrefer)

        open_btn = QPushButton("Open")
        play_btn = QPushButton("Play")
        pause_btn = QPushButton("Pause")
        stop_btn = QPushButton("Stop")
        open_btn.clicked.connect(self.open_file)
        play_btn.clicked.connect(self.play)
        pause_btn.clicked.connect(self.pause)
        stop_btn.clicked.connect(self.stop)

        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(self.on_seek)

        bar = QHBoxLayout()
        for w in (open_btn, play_btn, pause_btn, stop_btn, self.decode_mode):
            bar.addWidget(w)

        layout = QVBoxLayout()
        layout.addWidget(self.video, stretch=1)
        layout.addLayout(bar)
        layout.addWidget(self.seek)
        layout.addWidget(self.status)
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.setInterval(8)  # ~120 Hz pump; present rate limited by decode
        self.timer.timeout.connect(self.on_tick)

        probe = native.probe_hw_decode()
        self.status.setText(f"HW probe: {probe['summary']}")

    def _config(self) -> native.EngineConfig:
        cfg = native.EngineConfig()
        cfg.decode_mode = self.decode_mode.currentData()
        cfg.queue_capacity = 8
        cfg.convert_to_rgb24 = True
        # Temps réel : le décodage suit la présentation (sinon le clip est lu d'un bloc, images perdues).
        cfg.drop_when_full = False
        return cfg

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open video", "", "Video (*.mp4 *.mov *.mkv);;All (*)")
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path: str) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.engine.close()
        self.engine = native.PreviewEngine(self._config())
        err = self.engine.open(path)
        if err != native.EngineError.Ok:
            self.status.setText(f"Open failed: {native.error_name(err)}")
            self.engine = None
            return
        info = self.engine.info()
        fps = info.fps if info.fps > 1.0 else 30.0
        self._period = 1.0 / fps
        self._next_due = time.perf_counter()
        self.seek.setRange(0, max(1, int(info.duration_us // 1000)))
        self.status.setText(
            f"{Path(path).name} {info.width}x{info.height} @{info.fps:.2f} "
            f"codec={info.codec_name} hw={info.hw_backend or 'off'}"
        )
        self.timer.start()

    def play(self) -> None:
        if self.engine is None:
            return
        self.engine.play()

    def pause(self) -> None:
        if self.engine is None:
            return
        self.engine.pause()

    def stop(self) -> None:
        if self.engine is None:
            return
        self.engine.stop()

    def on_seek(self) -> None:
        if self.engine is None:
            return
        self.engine.seek_us(int(self.seek.value()) * 1000)

    def _present_next(self) -> None:
        assert self.engine is not None
        err = self.engine.pump_present()
        if err == native.EngineError.Ok:
            packed = self.engine.last_rgb_bytes()
            if packed is not None:
                data, w, h = packed
                image = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888)
                self.video.setPixmap(
                    QPixmap.fromImage(image).scaled(
                        self.video.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def on_tick(self) -> None:
        now = time.perf_counter()
        stall_ms = (now - self._last_pump) * 1000.0
        self._last_pump = now
        # Expected ~8 ms; excess is treated as UI stall sample (estimated from timer jitter).
        if self.engine is not None and stall_ms > 20:
            self.engine.metrics().note_ui_stall(stall_ms * 1000.0)

        if self.engine is None:
            return
        if now >= self._next_due:
            # Présentation à la cadence de la source ; un retard n'est pas rattrapé en rafale.
            self._next_due = max(self._next_due + self._period, now)
            self._present_next()
        m = self.engine.metrics()
        info = self.engine.info()
        dec = m.decode_pct()
        e2e = m.e2e_pct()
        self.status.setText(
            f"{info.width}x{info.height} @{info.fps:.1f} | "
            f"presented={m.frames_presented} drops={m.frames_dropped_queue} "
            f"fps~{m.displayed_fps:.1f} | "
            f"decode_p95={dec.p95/1000:.2f}ms e2e_p95={e2e.p99/1000:.2f}ms | "
            f"copies={m.copy_ops} bytes={m.bytes_copied/1e6:.1f}MB | "
            f"rss_max={_rss_mb():.0f}MB | hw={m.hw_backend or ('on' if m.hardware_decode else 'off')} | "
            f"timer_dt={stall_ms:.1f}ms"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", nargs="?", help="Optional media path to open at start")
    parser.add_argument("--hw", action="store_true", help="Prefer hardware decode")
    args = parser.parse_args()

    # Offscreen-friendly for CI if QT_QPA_PLATFORM=offscreen
    app = QApplication(sys.argv)
    win = PreviewWindow()
    if args.hw:
        win.decode_mode.setCurrentIndex(1)
    win.resize(960, 640)
    win.show()
    if args.media:
        win._open_path(args.media)
        win.play()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
