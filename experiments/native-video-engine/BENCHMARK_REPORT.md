# Benchmark report — Native C++ preview engine POC v0.1

**Date:** 2026-10-11  
**Raw JSON:** [docs/bench_results/results.json](docs/bench_results/results.json)

## 1. Tested hardware and OS

| Field | Value |
| --- | --- |
| OS | Linux 6.12.94+ (x86_64) |
| GPU / DRI | **None** (`/dev/dri` absent) |
| CPU | x86_64 cloud VM |
| Display | Headless; PySide host used `QT_QPA_PLATFORM=offscreen` |

**NOT TESTED on this host:** Apple Metal, VideoToolbox zero-copy, VAAPI/CUDA actual HW decode, Windows D3D11, interactive on-screen UI stalls under a real compositor.

## 2. Compiler and dependency versions

| Component | Version |
| --- | --- |
| C++ compiler | Ubuntu clang 18.1.3 (`c++`) |
| CMake | 3.28.3 |
| FFmpeg (pkg-config libavcodec) | 60.31.102 (system 6.1.1) |
| Python | 3.12.3 |
| pybind11 | 3.1.0 |
| PySide6 | 6.12.0 |
| AddressSanitizer | **NOT TESTED** (clang RT asan archives missing) |

## 3. Build instructions

```bash
cd experiments/native-video-engine
sudo apt-get install -y g++ libstdc++-14-dev python3-dev \
  libavcodec-dev libavformat-dev libavutil-dev libswscale-dev pkg-config
pip install pybind11 PySide6
bash benches/generate_media.sh 6
PYBIND_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())")
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="$PYBIND_DIR"
cmake --build build -j$(nproc)
ctest --test-dir build --output-on-failure
```

## 4. Methodology

- Synthetic media via `benches/generate_media.sh` (testsrc2 + noise, H.264).
- Warm-up: 1 s native CLI on 1080p30.
- Trials: 3; report **medians**.
- Duration: 4 s sustained playback unless noted.
- **Realtime mode:** present paced to media FPS; decoder backpressure (no drop-oldest).
- **Throughput mode:** `--throughput` (used ad-hoc; harness uses realtime).
- Pipelines:
  - **A** — Python + FFmpeg CLI null / pipe+`bytes` copy (host-local baseline; **not** full Kut-Studio QRhi monitor).
  - **B** — Native C++ software decode + CPU presenter.
  - **C** — Native HW-prefer (falls back to software when HW unavailable).
  - **D** — Native engine + PySide6 offscreen pump + Python `bytes` snapshot.
- Decode latency vs display-path latency reported separately.
- **e2e_decode_to_present** includes queue buffering; with capacity 8 at 30 fps, ~250–270 ms p50 is expected buffer depth, not swscale cost.
- Production Apple M4 `event_loop_lag_ms_p95 ≈ 22.64` is cited as **NOT RE-MEASURED** (Qt UI-thread lag, not total frame latency).

## 5–7. Results (median, directly measured unless noted)

### 1080p30

| Pipeline | Displayed FPS | Queue drops | Decode p95 | Notes |
| --- | ---: | ---: | ---: | --- |
| A ffmpeg null | n/a (12.4× realtime decode) | n/a | n/a | Decode-only |
| A pipe+Python copy | 238 (unpaced) | n/a | read+copy p95 ~9.2 ms | Not QRhi |
| **B native SW** | **29.93** | **0** | **11.7 ms** | Realtime paced |
| C native HW-prefer | 29.92 | 0 | 11.1 ms | HW inactive (`hw=off`) |
| D native+PySide offscreen | pumps=179 / 4 s | 0 | 10.9 ms | `displayed_fps_native` unset in D harness; UI stall est. ~0 ms |

Seek stress (B): seeks=2, FPS≈30.0, drops=0.  
Multi-stream ×2 independent processes: OK (~4.07 s wall). Timeline compositing: **NOT TESTED**.

### 1080p60

| Pipeline | Displayed FPS | Drops | Decode p95 |
| --- | ---: | ---: | ---: |
| B native SW | **59.8** (spot) / harness median in JSON | 0 | ~6–12 ms class |

### 4K30

| Pipeline | Displayed FPS | Drops | Decode p95 | Verdict |
| --- | ---: | ---: | ---: | --- |
| A ffmpeg null | n/a (3.5×) | n/a | n/a | Decode-only OK |
| A pipe+Python | 61.5 unpaced | n/a | copy p95 ~34 ms | |
| **B native SW** | **≈24.0** | **0** | **≈58 ms** | **Below realtime** on this CPU |
| C HW-prefer | ≈24.2 | 0 | ≈58 ms | Same as SW (no HW device) |
| D + PySide | 113 pumps / ~3.8 s | 0 | 50.6 ms | UI stall p95 **≈26 ms (estimated)** |

### 4K60

| Pipeline | Status |
| --- | --- |
| B native SW | Ran; **cannot sustain 60 fps** on this host with SW decode + RGB convert (see JSON). |

### Throughput mode (ad-hoc, 1080p30, 2 s)

| Metric | Value |
| --- | --- |
| Displayed FPS (unpaced) | ≈119 |
| Decode p95 | ≈11 ms |
| Queue drops | 0 |

### Zero-copy

| Claim | Result |
| --- | --- |
| Zero-copy decode→present | **false** on all measured paths |
| Metal IOSurface path | Scaffold only; **NOT TESTED** (see [docs/ZERO_COPY_STUDY.md](docs/ZERO_COPY_STUDY.md)) |

### CPU / memory

- Native path copies RGB24 via swscale + present (+ Python snapshot in D): on the order of **gigabytes** of copied bytes over a few seconds at 1080p/4K (see `bytes_copied` in JSON). This is expected without HW surfaces.
- Baseline A child RSS (ffmpeg null): ~110 MB (1080p), ~262 MB (4K).
- Process RSS for Python D path grows with frame buffers; treat as indicative only.

### GPU usage

**NOT TESTED** — no reliable GPU counters without a device.

## 8. GPU and zero-copy findings

See [docs/ZERO_COPY_STUDY.md](docs/ZERO_COPY_STUDY.md). Feasible on Apple Silicon in principle; not demonstrated here.

## 9. Remaining architectural bottlenecks

1. **RGB24 conversion (`swscale`)** dominates CPU work in the portable path.
2. **Queue depth** inflates e2e latency when decode is faster than realtime (1080p).
3. **4K software decode** on this VM cannot hold 30 fps after convert+present.
4. **No HW device** → pipeline C equals B.
5. **No production QRhi integration** → cannot yet measure UI-thread relief vs the documented 23 ms p95 lag on Apple M4.
6. **No multi-track compositing** in the POC.

## 10. Known failures and limitations

- ASAN build failed (missing clang RT libraries).
- HEVC test media skipped (`libx265` encoder absent).
- CUDA probe logs “Cannot load libcuda.so.1” (expected).
- Preview host offscreen: `propagateSizeHints` Qt plugin warning (harmless).
- Production Kut-Studio GPU monitor **not re-run** on this host.

## 11. Recommendations for future development

1. Re-run B/C/D on Apple Silicon with Metal presenter + VT (true zero-copy spike).
2. Add NV12 present path (skip RGB) for CPU and GPU shaders.
3. Reduce default queue depth or expose it for latency vs smoothness tradeoffs.
4. Keep POC isolated until Option B of ADR-0001 lands; use this harness as the Option C gate measurement tool (ADR-0003).

## Commands used

```bash
bash benches/generate_media.sh 6
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="$(python3 -c 'import pybind11; print(pybind11.get_cmake_dir())')"
cmake --build build -j$(nproc)
ctest --test-dir build --output-on-failure
./build/kut_engine_cli media/test_1080p30.mp4 --seconds 4
./build/kut_engine_cli media/test_4k30.mp4 --seconds 4
PYTHONPATH=build python3 benches/run_benchmarks.py --seconds 4 --trials 3
QT_QPA_PLATFORM=offscreen PYTHONPATH=build timeout 5 python3 apps/preview_host.py media/test_1080p30.mp4
```
