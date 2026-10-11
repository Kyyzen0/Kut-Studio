# ADR-0004: Native engine language — C++ vs Rust

**Status:** Accepted (language preference when Option C opens; Option B remains the active path)  
**Date:** 2026-10-11  
**Deciders:** Kut-Studio maintainer  

**Related:** [ADR-0001](0001-moteur-unifie-gpu-adaptatif.md) (unified adaptive GPU engine; Option C parked), [gpu-preview.md](../gpu-preview.md), [architecture.md](../architecture.md), [gpu-export-spike.md](../perf/gpu-export-spike.md)

This document compares **C++** and **Rust** for a future native media/GPU engine. It does not change application code. Chat and French product docs may summarize it; the normative technical text is English.

---

## Context

### Current stack

```
Project → RenderPlan → FFmpeg CLI (faithful preview / export)
                    └→ gpu_composite plan → QRhiWidget (realtime monitor)

QMediaPlayer (HW decode) → QVideoSink → CPU bytes plane copy → QRhi upload → present
```

- **UI:** PySide6. `ui/` owns presentation; `core/` owns editing and render plans and must stay testable without a window ([architecture.md](../architecture.md)).
- **Realtime monitor:** one top video track; Qt Multimedia decode; QRhi shaders (Metal / D3D11 / OpenGL; Vulkan registered). See [gpu-preview.md](../gpu-preview.md).
- **Faithful preview / export:** shared FFmpeg filter graph; segments are encoded to disk then replayed.
- **No native media engine today:** no pybind11, no Rust crate, no in-process FFmpeg. `ctypes` is used only for OS process helpers.
- **ADR-0001 Option B (retained):** unified adaptive GPU compositor plus a measured PyAV LGPL media path. **Option C** (native C++/Rust, dedicated render thread) stays parked until Option B hits hard PySide6 walls after stage 4.

### Why revisit the language now

Option C was left as “C++ / Rust” without a choice. Before investing in a native engine, Kut-Studio needs:

1. A clear split between **PySide6/Qt limits** and **current implementation** bottlenecks.
2. An argued language preference for when Option C opens.
3. Benchmarks that must pass before locking the decision.
4. A migration path that does not rewrite the application.

---

## Decision drivers

| Driver | Requirement |
| --- | --- |
| PySide6 UI | Progressive FFI; keep the existing shell |
| HW decode + GPU | Metal, Direct3D 11, Vulkan (secondary); FFmpeg hwaccel surfaces |
| Zero-copy | HW decode surface → compositor textures → present without a system-memory frame copy where the OS allows it |
| Threading | Decode and compose off the UI thread |
| Multi-track 4K | Sustained realtime for several 4K streams on the minimum machine profile |
| Ship / maintain | macOS, Windows, Linux; PyInstaller packaging; MIT license (LGPL FFmpeg in-process only) |
| Migration | Replace the hot path behind the same plan API; no big-bang rewrite of `core/` |

---

## PySide6 limits vs current implementation

| Symptom | Evidence | Class |
| --- | --- | --- |
| ~1–3 ms CPU plane copy per frame (~3 ms for 4K P010) | PySide6 6.11 rejects `memoryview` / raw pointers in `QRhiTextureSubresourceUploadDescription`; `QVideoSink.setRhi` textures are not public in stable C++ or Python ([gpu-preview.md](../gpu-preview.md)) | **PySide6 / Qt API** |
| Event-loop lag p95 up to ~23 ms with 4K effects | `QRhiWidget.render()` runs on the UI thread | **Qt widget model** |
| Multi-track / transitions / heavy graphics show “Computing preview…” | Realtime GPU coverage intentionally single-track; full fidelity = FFmpeg segments | **App design** |
| Segment cost floor ~0.9 s / 2 s; cost grew with timeline position | CLI process + x264 re-encode; historical output `-ss` (ADR-0001 stage 0) | **App design** |
| GPU export spike only ~1.5× | 57% Qt graphics rasterization (GIL); 34% sync framebuffer readback ([gpu-export-spike.md](../perf/gpu-export-spike.md)) | **App design + GIL** |
| HW decode sometimes slower in fps than CPU on M4 H.264 4K | Measured; Auto policy already prefers the non-bottleneck choice (`decode_policy`) | **Hardware reality** (handled) |

**Verdict:** Zero-copy and off-UI QRhi are real binding/Qt limits. Most multi-track 4K pain today is still product and implementation coverage (segment path, single-track monitor, full-frame PNG layers), not “Python cannot edit video.” Choosing a native language must not skip ADR-0001 Option B gains that do not require C++ or Rust.

---

## Comparison: C++ vs Rust

### Integration with the existing PySide6 UI

| | C++ | Rust |
| --- | --- | --- |
| Bindings | pybind11 / nanobind; same ABI world as Qt / PySide | PyO3 excellent for Python APIs; Qt C++ object ownership is awkward |
| Sharing QRhi / QWidget | Natural: call Qt C++ APIs, optional private headers, embed in `QWidget` | Needs a C++ shim, or abandon QRhi for wgpu / raw Metal / Vulkan and present via shared texture or a foreign window |
| Progressive FFI | Replace `ui/gpu_preview.py` executor first; keep `core/gpu_composite.py` plans in Python | Same plan shape via PyO3, but present / QRhi still needs C++ or a non-Qt GPU stack |

**Winner for Kut-Studio’s Qt UI: C++.**

### FFmpeg hardware decode and GPU present (Metal, Direct3D, Vulkan)

| | C++ | Rust |
| --- | --- | --- |
| FFmpeg | First-class C API; production NLE examples; `AVFrame` / `hw_frames_ctx` | `ffmpeg-next` / `rsmpeg` / sys crates; workable but thinner HW-surface documentation and version lag |
| Zero-copy surfaces | IOSurface (VideoToolbox), D3D11 textures, VA-API / DRM primes, CUDA → Vulkan / D3D interop: established C/C++ patterns | Same OS APIs through `unsafe` FFI; each backend is custom work |
| Align with current QRhi shaders | Reuse `.qsb` / QRhi passes in C++ | Keep QRhi via C++ FFI, or rewrite on wgpu (second shader pipeline) |

**Winner for HW decode → GPU texture without download: C++.**

### Zero-copy pipeline

Target path:

```
HW decode surface → compositor textures → present
(no system-memory frame copy on the happy path)
```

- **Blocked today** at the PySide6 upload hop and non-public Qt sink textures — language-agnostic until that hop leaves Python.
- **C++:** can own decode (libav, carefully) and import into QRhi / platform textures; true zero-copy is still OS-specific and never free on every GPU.
- **Rust:** same OS APIs through FFI; little safety win on the GPU import boundary (almost all `unsafe`).

**Feasibility: both; integration cost lower in C++.**

### Render isolation from the UI thread

- Required: decode workers + render thread; UI only submits a frame plan and presents a swapchain or foreign surface.
- **C++:** dedicated `std::thread` / Qt `QThread` next to Qt, or a child process (matches the current FFmpeg CLI isolation philosophy).
- **Rust:** stronger default for concurrent ownership; excellent for a **separate compositor process** with IPC. Weaker if present must remain a `QRhiWidget` on the UI thread (still needs a C++ / Qt hop).

**In-process Qt host: C++. Process-isolated engine: Rust is competitive.**

### Multi-track 4K performance

Dominant costs after Option B (projected from current measurements):

1. Per-frame CPU copies / readbacks (binding + sync grab)
2. Graphics rasterization under the GIL
3. Layer count / neighborhood effects / VRAM budget
4. Decode only when Auto chooses poorly or many simultaneous 4K decoders (multicam ~204% of one core for 4×4K feeds)

Neither language fixes (3)–(4) by itself. Both remove (1)–(2) if rasterization and compose move native / off-GIL. **Parity expected** if the same algorithms and GPU APIs are used; C++ is more likely to ship the zero-copy path sooner.

### Build, distribution, and maintenance (macOS, Windows, Linux)

| | C++ | Rust |
| --- | --- | --- |
| Toolchain | CMake + platform SDKs; must match Qt / PySide ABI; MSVC / Xcode / GCC matrix | Cargo reproducibility; still needs platform SDKs for Metal / D3D / Vulkan and FFmpeg |
| Shipping with PyInstaller | Ship `.so` / `.dylib` / `.dll` next to the app (`build.py` already packages binaries) | Same via `cdylib` / PyO3; maturin optional |
| FFmpeg LGPL (license) | Build and link LGPL FFmpeg; familiar path (ADR-0001) | Same legal constraint; fewer turnkey LGPL wheels |
| Team / hiring for NLE GPU | Larger C++ pool | Smaller Rust GPU + FFmpeg pool |
| Memory safety | Manual discipline; ASAN / TSAN | Strong default; `unsafe` islands at FFI / GPU |

**Ship complexity is similar once FFmpeg and GPU are in-tree. C++ wins Qt ABI fit; Rust wins memory-safety defaults.**

### Progressive migration without rewriting the application

Invariants to preserve:

- `core/` remains the source of truth for `Project` / `RenderPlan` / `gpu_composite` plans (Python).
- `ui/` stays PySide6.
- FFmpeg CLI remains reference export and crash-isolated heavy jobs.

Migration surface (same for both languages):

1. Native **executor** behind the existing plan API (`plan_frame` struct / blob).
2. Native **media_io** (decode / encode) for the hot path only; CLI kept for reference.
3. Optional native **graphics raster** to remove GIL cost.
4. UI still hosts a widget; the present API is the hard edge.

**C++ fits progressive in-process replacement of `ui/gpu_preview.py` and a future `core/media_io` backend.**  
**Rust fits progressive replacement if the engine is a sidecar with a stable IPC schema** — a larger jump for present / zero-copy into Qt.

---

## Decision

**Do not lock a full native rewrite now.** Finish ADR-0001 Option B stages 0–4; use stage-4 measurements as the Option C gate (unchanged from ADR-0001).

**When Option C is justified, choose C++** for the native engine (working name: `kut_engine`):

- **Python `core/`** keeps plans, editing, caches, and policy (architecture rule unchanged).
- **C++ library** owns: HW decode surface import, GPU compose / present (QRhi or a thin platform layer), optional encode feed, dedicated render thread.
- **pybind11 / nanobind** exposes a small API: `open_media`, `submit_plan`, `present`, `export_frame`, stats / health.
- **Rust is not the primary engine language** for Kut-Studio’s Qt-hosted monitor: zero-copy + QRhi + FFmpeg hwframes are a C/C++ ecosystem problem, and Rust + C++ shim + Python is three stacks.

**Revisit Rust only** for a crash-isolated out-of-process compositor that does **not** share QRhi with the UI. Re-evaluate only if in-process C++ crash rate or security isolation becomes the top risk after Option B.

### Option C gate

All of the following must be true before opening Option C:

1. Option B multi-track realtime meets product FPS on the minimum machine (MacBook Air M1 8 GB or mid-range iGPU PC, per ADR-0001).
2. The remaining deficit is measured as UI-thread QRhi lag and/or unavoidable Python upload / readback — not missing shader coverage or full-frame PNG waste.
3. A time-boxed C++ spike shows at least ~30% reduction in p95 frame cost, or enables true multi-4K realtime that Option B cannot.

---

## Consequences

**Easier later:**

- One language preference when Option C opens (C++), instead of an open C++/Rust fork.
- Option B work stays the critical path for multi-track GPU and PyAV media I/O.
- Architecture invariants (`core/` pure plans, CLI reference export) stay intact through a progressive FFI swap.

**Harder later:**

- C++ brings UB risk; require ASAN/TSAN (or equivalent) on engine CI when the module lands.
- Shipping a Qt-linked native module must track the PySide6 Qt build, or the engine must minimize Qt linkage (foreign surface / raw Metal–D3D–Vulkan).
- License discipline for in-process LGPL FFmpeg remains mandatory (no GPL x264/x265 inside the process).

**Deferred:**

- Rust sidecar compositor (only if process isolation becomes the priority).
- Vulkan-first strategy (keep Metal / D3D11 primary; Vulkan secondary on Linux).

---

## Technical risks

| Risk | Mitigation |
| --- | --- |
| GPL FFmpeg in-process contaminates MIT distribution | LGPL FFmpeg build + HW encoders / OpenH264; CLI GPL FFmpeg stays out-of-process (ADR-0001) |
| “Zero-copy” that still maps through system memory | Benchmark real texture imports per backend; do not assume VT / D3D / VA-API paths |
| Qt / PySide ABI skew | Link against the same Qt as PySide, or avoid Qt inside the engine and present via a foreign window |
| Parity drift (GPU vs FFmpeg chroma) | Keep reference export; continue PSNR / max-delta checks from the export spike |
| Native crash takes down the UI | Extend `GpuCrashGuard` semantics; optional later process isolation |
| Option C before Option B | Gate above; no native rewrite until stage-4 measurements |
| Vulkan as primary on macOS | Rejected; Metal primary on Apple platforms |

---

## Benchmarks required before a final language lock

Extend `tools/perf` on a machine matrix: Apple Silicon (M1 / M4), Windows (NVIDIA + iGPU), Linux (VA-API / NVIDIA).

1. **Upload path:** Python `bytes` vs C++ QRhi import vs platform zero-copy (µs/frame at 1080p / 4K / 4K10).
2. **UI isolation:** p95 / p99 main-thread stall with compose on the UI thread vs a dedicated render thread (2–8 video tracks; neighborhood effects on/off).
3. **Multi-track 4K:** sustained FPS and dropped frames for 1 / 2 / 4 / 8 simultaneous 4K decoders + compose (proxy vs original).
4. **End-to-end Option B vs Option C spike:** Onlyfab-class project + synthetic N-track 4K; record FPS, CPU%, GPU busy, VRAM, energy if available.
5. **Export:** GPU compose + HW encode vs FFmpeg reference; parity PSNR / max delta (continue spike methodology).
6. **Build / product:** clean CI build time and artifact size for the pybind module on three OSes; crash-recovery drill (kill decode thread / process).

**Decision rule:** if the C++ spike clears the Option C gate and a Rust spike is not ≥15% faster on the same GPU imports **or** markedly safer in crash drills with acceptable present latency, keep C++.

---

## Progressive migration plan

```
Option B stages 0–1 (segment fixes, media_io PyAV, governor)
    → stages 2–3 (multi-track GPU, content-sized textures, shaders)
    → stage 4 (GPU export + measurements)
    → Option C gate?
         no  → stay on Option B + PyAV
         yes → C++ spike (decode import + off-UI compose)
              → pybind executor behind the same plan API
              → replace ui/gpu_preview hot path (feature flag)
              → optional native media_io backend
              → optional later: engine subprocess
```

1. **Now through Option B:** no native language work except optional read-only spikes; keep measuring PySide ceilings.
2. **C++ spike (time-boxed):** one platform first (macOS Metal + VideoToolbox IOSurface → texture); submit plans from Python; no UI rewrite.
3. **Stable ABI:** versioned plan blob mirroring `gpu_composite` passes; a single load point for the native module (same spirit as `process_supervisor` / future `media_io` AST guards).
4. **Swap executor:** Python `RhiExecutor` → C++ behind a feature flag; CPU / Python QRhi fallback remains.
5. **Native media backend:** LGPL FFmpeg in the shared library for monitor / export hot path; CLI FFmpeg stays for reference, proxies, and capability detection.
6. **Only if needed:** move the engine to a subprocess with shared textures / IPC; at that fork, re-evaluate Rust for the sidecar — default remains one C++ codebase.

---

## Alternatives considered

| Alternative | Why not default |
| --- | --- |
| Rust in-process engine next to PySide6 | Qt / QRhi interop forces a C++ shim; three languages for one hot path |
| Rust-only sidecar from day one | Larger present / zero-copy jump; delays Option B user-visible wins |
| Stay on Python forever | Acceptable until Option C gate fails; zero-copy and off-UI QRhi remain blocked |
| Rewrite `core/` in C++/Rust | Breaks pure-Python testability; rejected by architecture rules |
| GStreamer / libmpv as the engine | Already rejected in ADR-0001 (packaging weight / wrong tool shape) |

---

## Action items

- [ ] Complete ADR-0001 Option B stages 0–4; record stage-4 measurements against the Option C gate.
- [ ] When the gate opens: time-boxed C++ spike (Metal + VideoToolbox first); publish numbers under `docs/perf/`.
- [ ] Only after the spike clears the gate: scaffold `kut_engine` (CMake + pybind11/nanobind) behind a feature flag.
- [ ] Do not start a Rust engine track unless the sidecar isolation case is explicitly adopted.
