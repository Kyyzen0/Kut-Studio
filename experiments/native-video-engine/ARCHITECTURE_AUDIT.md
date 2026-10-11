# Architecture audit — Kut-Studio preview path (POC v0.1)

**Date:** 2026-10-11  
**Scope:** Read-only inspection of the production tree to bound the native C++ preview POC.  
**Host used for this audit note:** Linux x86_64 cloud VM (no `/dev/dri`; no Apple Metal).

This document does **not** change production code. File paths refer to the Kut-Studio repository root.

---

## 1. Current FFmpeg decoding

| Path | Mechanism | Key files |
| --- | --- | --- |
| Realtime monitor | Qt Multimedia (Qt FFmpeg plugin) via `QMediaPlayer` + `QVideoSink` | `ui/preview_panel.py`, `ui/main_window_mixins/hardware_preview.py` |
| Faithful preview segments | External `ffmpeg` process (`supervised_popen`), shared export filter graph | `core/preview_engine.py`, `core/filter_graph.py`, `core/process_supervisor.py` |
| Proxies / tracking / probes | External FFmpeg with optional `-hwaccel` | `core/proxy_manager.py`, `core/decode_policy.py`, `core/hardware_decoding.py` |
| Export | `QProcess` + same filter graph; **decode always CPU** for determinism | `core/export_engine.py`, `core/render_queue.py` |

There is **no in-process libav** in production today (ADR-0001 proposes PyAV LGPL later; ADR-0003 prefers C++ for Option C).

---

## 2. Hardware acceleration paths

Detection and policy:

- `core/hardware_decoding.py` — lists/validates backends (VideoToolbox, CUDA, D3D11VA, DXVA2, QSV, VAAPI).
- `core/hardware_encoding.py` / `core/hardware_cache.py` — shared capability cache.
- `core/decode_policy.py` — Auto chooses measured (backend, family) pairs; HW frames for CLI paths are **downloaded** to system memory (`hwdownload`) before the CPU filter graph.

Realtime monitor backends are passed to Qt via `QT_FFMPEG_DECODING_HW_DEVICE_TYPES` (process lifetime).

---

## 3. QRhi rendering and texture upload

- Plan (pure): `core/gpu_composite.py`, `core/gpu_frames.py`, `core/gpu_effects.py`.
- Executor (UI): `ui/gpu_preview.py` — `QRhiWidget` runs Metal / D3D11 / OpenGL (Vulkan registered).
- Shaders: `tools/gpu/shader_sources.py` → `assets/shaders/*.qsb`.

Upload path (documented in `ui/gpu_preview.py::_plane_description`):

1. Map `QVideoFrame` planes.
2. PySide6 6.11 rejects `memoryview` / raw pointers for `QRhiTextureSubresourceUploadDescription`.
3. Force **one `bytes` copy per plane**, then `uploadTexture`.
4. Measured copy cost: ~1 ms / 8 MB, ~3 ms for 4K P010.

---

## 4. CPU memory copies (decode → present)

Happy path today (HW + GPU monitor):

```
HW decode (Qt/VT/…) → map to CPU-visible planes → bytes copy → QRhi upload → GPU composite → present
```

Not zero-copy. `QVideoSink.setRhi` GPU-resident textures are not public in stable C++ or Python (`docs/gpu-preview.md`). Faithful preview adds encode-to-disk + re-decode copies.

---

## 5. Thread ownership

| Work | Thread |
| --- | --- |
| UI, `QMediaPlayer`, `QRhiWidget.render()` | Qt UI thread |
| Faithful segment FFmpeg | `kut-preview-render` worker (`faithful_preview` mixin) |
| Proxies / analysis | `TaskQueue` / studio workers |
| Export | `QProcess` (events on UI thread) |

GPU compose/upload runs **inside** `QRhiWidget.render()` on the UI thread — primary source of event-loop lag under load.

---

## 6. Preview cache and prefetch

- Segment cache: `core/preview_cache.py`, fingerprints via `filter_graph.fingerprint_plan`.
- Prefetch / cancel: `core/prefetch.py`, `core/preview_engine.py`, `core/preview_governor.py`.
- Adaptive quality: `core/preview_adaptive.py`.
- Proxies: `core/proxy_manager.py` (preview only; export uses originals).

---

## 7. Benchmarks and ADRs

| Artifact | Role |
| --- | --- |
| `tools/perf/gpu_bench.py` | HW decode + monitor GPU benches |
| `docs/perf/gpu.json` | Recorded numbers (Apple M4 Metal) |
| `docs/perf/gpu-export-spike.md` | GPU export spike (GIL raster + readback) |
| `docs/adr/0001-…` | Unified adaptive GPU (Option B); Option C parked |
| `docs/adr/0003-…` | Prefer **C++** when Option C opens |

---

## 8. What the “~23 ms p95” figure actually is

**Source:** `docs/gpu-preview.md` and `docs/perf/gpu.json` key  
`monitor_4k_hevc10.hw_decode+gpu_preview+effects.event_loop_lag_ms_p95` ≈ **22.64 ms**.

| It is | It is not |
| --- | --- |
| p95 **Qt event-loop lag** during ~4 s playback with HW decode + GPU preview + effects on Apple M4 | End-to-end decode→glass frame latency |
| Correlated with UI-thread `QRhiWidget.render()` work | GPU shader duration alone (`gpu_render_ms_p95` ≈ 9.0 ms in the same run) |
| Measured in the production monitor subprocess bench | A number re-measured on this Linux POC host |

Same table: displayed FPS stayed ~29.2 with 3 drops — the UI was stressed, not necessarily failing realtime cadence.

---

## 9. Integration boundaries with PySide6 (for this POC)

Safe, isolated surface (this experiment):

```
experiments/native-video-engine/  →  pybind11 module  →  apps/preview_host.py
```

**Do not** (per mission constraints):

- Wire into `ui/preview_panel.py` or `main.py`.
- Replace `core/preview_engine.py` or export.
- Add mandatory production dependencies.

Recommended future seam (not implemented here): swap only the executor behind `core/gpu_composite` plans (`submit_plan` / `present`), keeping Python as source of truth.

---

## 10. Assumptions for POC v0.1

1. Linux CI/cloud host validates **software decode + measurable present path**; Metal is compiled only on Apple and documented as NOT TESTED here.
2. Without `/dev/dri`, VAAPI/CUDA HW decode is probed but expected unavailable → mark HW path NOT TESTED when probe fails.
3. pybind11 is preferred over Shiboken6 for an isolated module (no Qt moc in the engine core).
4. “Zero-copy” claims require a proven decode→texture→present path; default path copies to a CPU present buffer for honest metrics.
5. Baseline “A” on this host uses a **Python + FFmpeg CLI / in-process decode harness** that mirrors copy costs; full Kut-Studio GPU monitor numbers from Apple M4 remain **documented reference only**, not re-run here without Metal.

---

## 11. Production files touched by this POC

**None.** All new work lives under `experiments/native-video-engine/`.
