# ADR-0003: Language and boundary of the future native engine (C++ or Rust)

**Status:** Proposed. No application code was changed to produce this study.
**Date:** 2026-10-11
**Deciders:** Kut-Studio maintainer
**Refines:** [ADR-0001](0001-moteur-unifie-gpu-adaptatif.md), Option C ("rewritten native engine, C++ / Rust,
dedicated render thread"), which ADR-0001 kept "for later, if the step 4 measurements demand it".
**Related:** [ADR-0004](0004-native-engine-language-cpp-vs-rust.md) (C++ language preference, PR #89), which reads this study's gates as its decision rule.

## Summary

1. **Most documented limits are ours, not PySide6's.** Of the 14 limits catalogued in section 2, only three cannot be
   removed without native code: importing a hardware-decoded frame into the GPU without a copy, running the render
   loop without the GIL and off the GUI thread, and CPU work that scales across threads inside one process (the last
   has a Python workaround that reaches about x4.5 on 8 workers for text outlines, and about x1.1 for single long Qt calls). PySide6 as a UI toolkit is not a limit and stays.
2. **Do not start a rewrite.** The declared target (a 1080x1920 social edit playing without "Computing preview" on a
   MacBook Air M1 8 GB) is mostly reachable with language-independent work (Phase 0). A native layer is justified by a
   different ambition: several concurrent 4K tracks, 4K effects without UI lag, and zero-copy hardware export.
3. **If and when native code is built: a small in-process C++20 library on Qt's QRhi, behind a C ABI, called with
   `ctypes`.** Rust is a close second, not a rejected option. C++ wins here for three reasons that do not depend on
   speed (both languages perform alike): the Qt seam (QRhi, QPainter, native-texture import) is C++-only, the existing
   executor ports almost one-to-one, and Python plus C++ is one extra language where Python plus Rust plus a C++ Qt
   shim would be two.
4. **The language choice is gated by data, not taste.** Phase 1 runs time-boxed spikes with a pre-registered decision
   rule (see "Decision"). Rust wins if its spike reaches parity *and* a Qt-free path to the viewer exists, or if the
   C++ spike shows a class of defect that Rust would have prevented.
5. **Expected effect (hypotheses to verify in "Benchmarks required"):** GUI-thread cost per 4K stream from about 7 ms
   to under 0.5 ms, event-loop lag with 4K effects from 22.6 ms p95 to under 5 ms, Onlyfab export from x1.5
   (measured spike) to x3 (Phase 0 projection) and x5 (native zero-copy, projection).

## Context

### What "native engine" means here

The application is about 130k lines of Python (`core/` 72k, `ui/` 57k) plus 81k lines of tests, with a runtime
dependency set of only PySide6 (`<6.12`) and numpy. It is packaged with PyInstaller for macOS arm64 (53 MB zip),
Windows x64 (71 MB) and Linux x64 (105 MB) as of v0.4.0. The path of a frame today:

```
Project (.kut) -> RenderPlan (core/render_plan.py, immutable, single reader of the project)
   -> FFmpeg filter graph      -> export, faithful preview segments, scopes (subprocess, process_supervisor)
   -> FramePlan (core/gpu_composite.plan_frame, ~0.25 ms for 4 layers)
        = textures + passes (shader name, target, inputs, std140 uniform bytes)
        -> RhiExecutor (ui/gpu_preview.py, ~370 lines of QRhi calls) -> QRhiWidget under a transparent QGraphicsView
   decode for the monitor: ONE QMediaPlayer -> QVideoSink -> QVideoFrame -> map() -> bytes -> texture upload
```

"Native engine" in this ADR means the *frame path* only: decode, frame import, plan execution on the GPU, texture
cache, readback and hardware encode. Everything else is out of scope.

**Stays Python:** project model, edit commands, undo, timeline evaluator, `RenderPlan`, `plan_frame`, the numpy
oracle, policies (`decode_policy`, hardware detection), colour definitions, UI, i18n, the FFmpeg-graph reference
export. **Non-goals:** replacing PySide6 for the UI, a Qt Quick rewrite, a new project format, a native audio engine,
a custom GPU abstraction, native code ever reading a `.kut` file (the "a project never executes code" rule from the
0.4.0 injection fix stays intact).

### Product constraints already decided

- Minimum fluid machine: MacBook Air M1 8 GB or a recent iGPU laptop with 8 GB (ADR-0001).
- Export goes through the same GPU engine; the FFmpeg CPU export stays as the "reference" export and the test oracle.
- Rules of [architecture.md](../architecture.md) stay: `core/` never imports `ui/`, one source of truth per fact,
  preview and export read the same plan, hardware failures fall back to CPU.
- MIT licence: FFmpeg must be an LGPL build, loaded dynamically (ADR-0001).

## 2. Are the limits PySide6's or ours?

### 2.1 Method

New measurements taken on 2026-10-11 on the maintainer's machine (Apple M4, 10 cores, 24 GB, macOS 27.2, Python
3.14.7, PySide6 6.11.2, FFmpeg 9.0.2, Metal), plus the repository's own recorded benchmarks (`docs/perf/gpu.json`,
`docs/perf/gpu-export-spike.md`). Scripts were run from a scratch directory; nothing was added to the repository. Raw
results are in Appendix A. Everything not measured is labelled *hypothesis* or *to verify*.

### 2.2 Attribution

Classes: **Impl** = our implementation; **CPython** = the GIL; **Binding** = PySide6 specifically; **Qt** = Qt by
design (the same in C++); **HW/Media** = silicon, codecs, FFmpeg; **Infra** = tests and CI.

| # | Observed limit | Evidence | Root cause | Class | Fixable in the current stack? | Needs native code? |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | Preview segment cost grew with timeline position (x7.8 at 28 s) | ADR-0001 | output-side `-ss` made FFmpeg compose from 0 | Impl | Fixed (PR #46) | No |
| L2 | Floor of 0.9 s per 2 s segment ("Computing preview") | ADR-0001 | faithful FFmpeg segments used as the playback path | Impl (architecture) | Yes: GPU monitor, ADR-0001 option B | No |
| L3 | 1,499 full-frame rasters for 223 distinct contents (Onlyfab) | spike report | full-frame PNG per animated state | Impl | Yes: per-content textures animated by matrix | No |
| L4 | Raster and CPU work do not scale with threads | spike (19.0 s at 4 threads, 19.9 s at 8, vs 19.3 s serial); bench A.1 | Python scene evaluation holds the GIL; PySide6 also holds it during Qt calls | CPython + Binding | Partly: dedupe (x6.7 fewer rasters), process pool (x4.5 on 8 workers for text, x1.1 for single long calls) | Only to keep threads in one process |
| L5 | Playback froze with scopes visible (0.2.5) | PR #56 | scopes command built on the UI thread; GIL-holding Python | Impl | Fixed | No |
| L6 | Windows CI hang (deadlock) | PR #75 | QObject dropped on a worker thread; PySide wants the GIL in the destructor | Impl x Binding semantics | Fixed; rule recorded | No (native threads that hold no Python objects remove the class) |
| L7 | One CPU copy per plane per frame (~3 ms for 4K P010) plus ~3.8 ms to map a VideoToolbox surface | `gpu.json`: 3.39 ms (CPU decode) vs 7.19 ms (HW decode) average submit for the same render | Qt Multimedia keeps hardware-texture access private; PySide6 refuses `memoryview`/pointers for uploads and does not bind native-texture import | Qt + Binding | No | **Yes** |
| L8 | GUI-thread render; event-loop lag p95 22.6 ms with 4K + effects | `gpu.json`; thread test (A.4) | `QRhiWidget.render()` runs on the GUI thread; a Python render thread would fight the UI for the GIL | Qt (+ CPython) | Partly (fewer passes) | **Yes** (or a separate process) |
| L9 | Monitor shows one video track | ADR-0001, `preview_panel.py` | one `QMediaPlayer` | Impl | Partly: more decoders (PyAV), with CPU copies | For zero-copy multi-4K |
| L10 | Hardware decode is not always faster (VideoToolbox 4K H.264: 24 fps vs 49 fps CPU) | `gpu.json` | silicon behaviour | HW/Media | Yes: measured policy exists | No |
| L11 | Scrubbing cost is the GOP (776 ms HW / 1,947 ms CPU median, 4K GOP 120) | `gpu.json` | codec structure | HW/Media | Yes: proxies, persistent decoders | No |
| L12 | FFmpeg version and CPU-path quirks (7.x `setpts`, swscale C path, 4:2:0 chroma) | memory notes, PR #78 | filter-graph render depends on the FFmpeg build | Impl (architecture) | Reduced by composing on the GPU | No |
| L13 | D3D11 and OpenGL paths verified only by simulation; CI has no GPU | `docs/gpu-preview.md` | no hardware lab | Infra | Needs real hardware | Prerequisite for any native work |
| L14 | PySide6 6.12.0 crashed the interpreter (`bool_dealloc`), pinned `<6.12` | `requirements.txt` | dependency regression | Binding (release quality) | Pinned | A C++ library is exposed to *Qt* releases, not PySide releases |

**Reading the table.** Six rows are plainly our implementation (L1, L2, L3, L5, L9, L12), L6 is half ours, and
several are already fixed. The genuinely PySide6-specific rows are the GIL held across Qt calls (L4), the binding
gaps behind L7, and destructor semantics (L6). Qt-by-design rows (L7, L8) would be identical in C++. Three needs
cannot be met without native code: **native-texture import (L7)**, **a render loop free of the GIL and the GUI
thread (L8)**, and **in-process parallel CPU work (L4)**, the last with a process-pool workaround.

### 2.3 The measurements behind the table

**A.1 Does PySide6 release the GIL? Mostly no.** In the reference run (`docs/perf/gil-scaling.json`), single long
Qt calls on a 4K image get x0.78-1.00 with 4 and 8 threads, and so do the text outline (x1.00) and 2,000 small
`QPainter` calls (x0.88-0.89). This rules out "many tiny calls" as the explanation. `QImage.save` (PNG encoding) is
the exception: x3.9 to x5.1, because it releases the GIL. Eight worker *processes* reach x4.5 on the text outline and
x2.9 on the small paint calls, but only x1.1 on the single long calls, where dispatching a job costs about as much as
the job itself. A pure-Python control confirms the method (threads x0.99-1.00, processes x4.7). So the GIL limitation
is real and binding-level, **and** removable in Python with processes, for jobs of a few milliseconds or more.

Two caveats. First, the process figures exclude returning the result: the workers here return an integer. The export
already writes its PNGs inside the worker, which matches this; a design that sends full frames back to the parent
would lose most of the gain. Second, earlier scratch runs of the same jobs differed by up to 2.5x in serial time and
1.7x in gains, so single figures carry roughly ±30 % uncertainty. B1 needs repeated runs on an idle machine before G1.

**A.2 The Python that plans a frame is not the bottleneck.** `plan_frame` costs about 0.25 ms for four layers with
effects (test threshold 5 ms). The 3.39 ms average submit with CPU decode at 4K is the size of one memory copy of a
24.9 MB P010 frame (about 7 GB/s). Python call overhead is a small share; data movement dominates.

**A.3 Hardware decoding is what makes several 4K streams possible on this machine.** Concurrent 4K HEVC 10-bit
streams, reference run `docs/perf/decode-scaling.json` (180 frames, about 345 Mb/s, median of 3):

| Streams | CPU software | VideoToolbox, frames downloaded to RAM | VideoToolbox, frames kept on GPU |
| ---: | ---: | ---: | ---: |
| 1 | 31 fps (265 ms CPU per frame) | 53 fps (5.5 ms) | 62 fps (2.5 ms) |
| 2 | 17 each (33 total) | 57 each (113) | 60 each (119) |
| 4 | 8 each (34) | 57 each (228) | 64 each (256) |
| 6 | 6 each (34) | 49 each (293) | 50 each (298) |

CPU decoding saturates at about 34 fps in total, roughly one real-time stream of this clip. Hardware decoding gives
1.6 to 2.1 times real time per stream up to six streams. Keeping frames on the GPU adds only 2 % to 17 % throughput,
but it halves the CPU time per frame (5.5 to 2.5 ms with one stream, 3.5 to 1.8 ms with four). That CPU saving is
the part that matters for the zero-copy argument. An earlier scratch run on an 83 Mb/s clip gave higher CPU figures
(up to 82 fps in total) and a larger gain for frames kept on the GPU (+13 % to +38 %). Absolute values depend on
bitrate, so cite the reference JSON. These numbers are for an M4; the M1 8 GB target is unmeasured (benchmark B2).

**A.4 `QRhiWidget` renders on the GUI thread.** A subclass that records `threading.get_ident()` shows `initialize()`
and `render()` on the main thread (Metal, 41,665 renders in 1.2 s). Qt's own documentation says a `QRhi` and its
resources may live on any thread but must be used from one thread only, and that `QRhi` classes carry no source or
binary compatibility guarantees. Isolating the render loop therefore needs a second `QRhi` (or device) owned by a
render thread and a way to publish textures to the GUI one.

**A.5 PySide6 6.11.2 binding gaps (from the installed stubs).** `QRhiTexture` has no `createFrom` or `nativeTexture`;
`QRhi.nativeHandles()` returns the opaque base class, and only `QRhiGles2NativeHandles` has fields (no Metal or
D3D11 handles). `QVideoFrame` exposes `HandleType.RhiTextureHandle` and `QVideoSink.rhi()`, but no texture accessor;
`QAbstractVideoBuffer` is limited to `map`/`unmap`/`format`. The texture path of Qt Multimedia lives in private headers
(`qhwvideobuffer_p.h`). `shiboken6.getCppPointer()` does work, so a native library *can* receive the GUI's `QRhi*`.
The Qt SDK headers are not in the PySide6 wheel.

**A.6 The GUI-thread budget at four 4K streams.** One stream with hardware decode costs 7.19 ms average to submit
(p95 8.93 ms). Linear extrapolation (*hypothesis*): four streams cost 28.8 ms of the 33.3 ms frame at 30 fps, before
overlays, painting and effects. With effects, the loop lag p95 rises from 9.0 to 22.6 ms for *one* stream while the
average submit barely moves (7.19 to 7.46 ms), so the rest is not CPU submission. The likely cause is GPU back-pressure
on the GUI thread (drawable wait). This is unattributed and is benchmark B0.

### 2.4 Conclusion of the attribution

- Switching language does not change decode throughput, shader cost, memory bandwidth, GOP seek cost or the 4:2:0
  conventions. It changes constant factors by a few percent; **architecture** (who owns decode, the device and the
  render thread) changes the ceiling.
- The limits that justify a native layer are narrow and well-defined. They are met by a *small* library, not by
  porting the engine.
- PySide6 for the UI is not implicated in any row.

## Decision

Pending the gates below, the proposed decision is:

1. **Phase 0 first, in Python, now** (see "Migration plan"): per-content graphic textures, process-pool rasterisation for cold
   preparation, asynchronous readback, ADR-0001 step 1 (PyAV media I/O on an LGPL FFmpeg), GPU telemetry and
   attribution of L8. These are language-independent and are prerequisites of any engine.
2. **Phase 1 spikes decide the language**, with the rule pre-registered below.
3. **Default outcome: C++20 library `kut_engine`**, in-process, built against the exact Qt of the PySide6 wheel,
   scoped to the frame path, behind a C ABI. Python keeps the whole application.

### Boundary design (applies to either language)

| Rule | Reason |
| --- | --- |
| Plain C ABI (`kut_engine.h`), loaded with `ctypes` (`CDLL` releases the GIL during a call) | no Python-ABI coupling, language-swappable, simple packaging |
| One call per frame: `FramePlan` serialised flat (passes, texture specs, uniform blobs) | plan stays in Python (0.25 ms); native only executes; keeps the numpy oracle and fake backend valid |
| Sources by handle: the engine owns decoder workers and frame caches; Python never touches pixels except for explicit readback (scopes, fallback) | the zero-copy requirement |
| **No native-to-Python callbacks on engine threads**; events go through a pollable queue or Qt signals from a C++ `QObject` on the GUI thread | this is the exact class of bug behind L6 |
| Handshake on load: ABI version and `qVersion()` must equal PySide6's; a one-texture smoke test; any failure falls back to the Python executor | hardware failures already fall back to CPU (architecture.md) |
| Python executor stays (fallback and parity oracle); `GpuCrashGuard` semantics extended to the native path | no flag-day |
| Headless capable (`QRhi` without a window) | export and the planned script mode / CLI |
| Optional at build time for contributors; absent library means the Python path | `pip install -r requirements.txt` keeps working |
| Native code never reads project files | keeps the "a `.kut` never executes code" rule |

### Pre-registered decision rule for gate G1 (language)

Choose **Rust** only if all of these hold after the spikes; otherwise choose **C++**:

1. The Rust spike (B3-rust) reaches parity with the C++ spike: zero CPU copies verified, GUI-thread cost within 10 %,
   and pixel-identical output on the chroma-sensitive test patterns.
2. A credible Qt-free route to the viewer exists: either Qt exposes a public external-texture API by then, or the
   maintainer explicitly accepts re-implementing viewer overlays inside the engine (benchmark B4 shows the cost).
3. Either the C++ spike produced a high-severity defect class that Rust would have prevented (a sanitizer finding in
   threading or lifetime code), or C++ build/CI complexity exceeds the thresholds in B9.

### What would change this recommendation

- Qt publishes a stable public API for native textures or hardware video frames (check each Qt release).
- The product decides the engine is the product (headless render farm, server-side use): Qt-free Rust then wins.
- A free-threaded CPython build becomes supported by PySide6 (unverified today): L4 and part of L8 shrink, and
  Phase 0 may suffice.
- Phase 0 plus G0 shows the declared target is met and 4K multi-track is not a goal: defer native work entirely.

## Options Considered

### Option A: Stay on Python and PySide6, hardened

| Dimension | Assessment |
| --- | --- |
| Complexity | Low to medium |
| Cost | Weeks (Phase 0) |
| Scalability | Medium: about 3 concurrent 4K streams on an M4 before the GUI thread saturates (hypothesis from A.6) |
| Team familiarity | High |

**Pros:** no new toolchain; every Phase 0 item is needed anyway; reaches the declared 1080p social target.
**Cons:** no zero-copy, GUI-thread render, in-process CPU parallelism stays GIL-bound.

### Option B: In-process native frame-path library in C++ on QRhi (recommended default)

| Dimension | Assessment |
| --- | --- |
| Complexity | High, but small in size (target under 8k lines native) |
| Cost | Months in total, in gated phases |
| Scalability | High |
| Team familiarity | Low to medium (new language, but the API mirrors code already written against QRhi) |

**Pros:** same GPU abstraction, shaders (`.qsb`), tests and backends; QPainter reusable off the GIL; direct access to
native-texture import; one extra language; platform interop references are C/C++/Objective-C++.
**Cons:** memory-safety burden; Qt SDK and exact-version coupling; private/unstable Qt API at the seam; CMake build.

### Option C: Rust engine

- **C1, Qt-free engine** (own `wgpu` device and surface, native child window): needs overlays re-implemented inside the
  engine or a translucent-window workaround, a new GPU abstraction, a new rasterizer (parity risk).
- **C2, Rust core plus a C++ Qt shim**: same C++ shim as Option B, with Rust added behind it for decode and
  composition.

| Dimension | Assessment |
| --- | --- |
| Complexity | High (C1) / Very high (C2: three languages) |
| Cost | Higher than B for the same scope |
| Scalability | High |
| Team familiarity | Low |

**Pros:** compile-time data-race and lifetime checking where concurrency is densest; `cargo`/`maturin` packaging is
the simplest of the three; `wgpu` covers Metal, D3D12 and Vulkan; precedent: Gyroflow (Rust core, Qt/QML UI, FFmpeg,
`wgpu`/Qt RHI, zero-copy paths) ships on three desktop systems.
**Cons:** no Qt RHI bindings found in cxx-qt; `ffmpeg-next` is in maintenance mode and hardware contexts go through
`unsafe` `ffmpeg-sys`; external-texture import in `wgpu-hal` is documented for Vulkan DMA-BUF only (Metal and D3D12 to
verify); D3D11 (our current Windows backend) and OpenGL are not first-class in `wgpu`; safety benefit is smallest
exactly at the FFI boundaries (FFmpeg, drivers, OS APIs) where most risk lives.

### Option D: Out-of-process engine (any language), deferred

| Dimension | Assessment |
| --- | --- |
| Complexity | Very high |
| Cost | Highest |
| Scalability | High; best fault isolation |
| Team familiarity | Medium (the project already supervises FFmpeg processes) |

**Pros:** driver and decoder crashes cannot kill the app; no GIL by construction.
**Cons:** cross-process surface sharing (IOSurface, NT handles, DMA-BUF) plus IPC and sync on every platform; still
needs an in-Qt import path. Revisit only if the field crash rate of Option B warrants it (`GpuCrashGuard` already
records crashes).

## Trade-off Analysis

C++ versus Rust on the seven requested criteria, plus two extras.

| Criterion | C++ | Rust | Verdict |
| --- | --- | --- | --- |
| 1. PySide6 integration | Links the same Qt; `getCppPointer` gives the GUI's `QRhi*` | cxx-qt covers Rust QObjects/QML, no QRhi; needs a C++ shim anyway | C++ |
| 2. HW decode and GPU APIs | FFmpeg C API; QRhi on Metal, D3D11/12, Vulkan, GL | FFmpeg via `unsafe` sys crates; `wgpu` on Metal, D3D12, Vulkan, GL | C++ (narrowly) |
| 3. Zero-copy pipeline | Feasible on all three OSes; shortest path into Qt | Feasible if the engine owns the surface; into Qt needs C++ | C++ for the Qt viewer, tie otherwise |
| 4. Render isolation | Needs discipline plus TSan | `Send`/`Sync` checks the shared caches | Rust edge on safety, same architecture |
| 5. Multiple 4K tracks | Same hardware limits | Same | Tie |
| 6. Build, distribution, maintenance | CMake + Qt SDK + sanitizers | `cargo` is simpler, but the C++ shim remains | Rust in isolation, near tie in the real design |
| 7. Progressive migration | Port of the executor is mechanical; shaders and oracles reused | New GPU abstraction and re-validation (C1) | C++ |
| Extra: safety, long-term maintainability | Weak without mitigations | Strong, except at FFI | Rust |
| Extra: AI-assisted development | UB can pass tests; many reference examples | compiler feedback is a strong reviewer; fewer interop examples | Tie |

C++ leads on four criteria (Qt integration, the GPU and decode ecosystem, zero-copy into the Qt viewer, migration);
Rust leads on three (safety of the render-isolation code, build ergonomics in isolation, long-term safety); the rest
tie. Because the application's viewer is a Qt widget hierarchy, the Qt seam dominates. Confidence in C++ is moderate;
the pre-registered rule under "Decision" makes the call falsifiable.

### PySide6 integration

The native library needs three Qt abilities that PySide6 does not give: reading the GUI's native device, importing a
native texture into a `QRhiTexture`, and running `QPainter` without the GIL. C++ has all three. The library receives
the `QRhi*` of the viewer through `shiboken6.getCppPointer` (verified to work). It must be compiled against the exact
Qt of the wheel (6.11.2 in `constraints.txt`); `QRhi` has no compatibility guarantee, so a load-time version check and
the Python fallback are mandatory. A Qt-free native library avoids the coupling but then the viewer must either host
a native child window (overlay problem, risk R5) or import textures through C++ again.

### Hardware decoding and GPU rendering

Decode is the FFmpeg C API in both languages (VideoToolbox on macOS, D3D11VA/NVDEC/QSV on Windows, VAAPI/NVDEC on
Linux). C++ matches the reference material (FFmpeg `hw_decode.c`, mpv, libplacebo, Qt). In Rust, `ffmpeg-next` is in
maintenance mode and `rsmpeg` is a thin layer, so hardware device and frame contexts are written against the raw
sys crate. For rendering, QRhi is the abstraction the project already uses (Metal, D3D11, OpenGL today; D3D12 and
Vulkan available in Qt), with GLSL compiled once to `.qsb`. `wgpu` would be a second abstraction and a second shader
path (SPIR-V or WGSL through naga), and it has no D3D11 backend.

### Zero-copy, per platform

| Platform | Candidate path | Status |
| --- | --- | --- |
| macOS | VideoToolbox `CVPixelBuffer` (IOSurface) -> `CVMetalTextureCache` -> `MTLTexture` per plane (unified memory) -> `QRhiTexture::createFrom` | Feasible; best-understood path; B3 |
| Windows | give FFmpeg Qt's `ID3D11Device` so D3D11VA frames live on it; frames are slices of a texture array, so a GPU-side copy to a plain texture may be needed | To verify, vendor variance; B7 |
| Linux | VAAPI -> DMA-BUF -> EGL image -> GL texture (Mesa); NVIDIA differs; X11/Wayland variance | To verify; CPU path stays the default; B8 |

The same OS APIs apply in both languages; the language changes who writes the `unsafe`/platform code, not whether it
works. Dawn's shared-texture-memory design (IOSurface, DXGI handles, DMA-BUF, with explicit access windows) is a
useful reference for the synchronisation model, without adopting Dawn.

### Isolation of rendering from the UI thread

Required: an engine-owned render thread with its own `QRhi` or device, publishing a finished canvas texture that the
GUI-side `QRhiWidget` merely draws (a single quad, expected well under 1 ms). On Metal two queues on one device can
share textures with explicit completion sync; D3D11 needs shared NT handles with keyed mutexes; Linux needs shared GL
contexts or EGL images. A Python render thread is not an option: it would hold the GIL for milliseconds per frame and
stall every Python event handler. This is a native-code requirement in either language; Rust only improves the safety
of the shared caches (frame pool, texture LRU), which is where the project's earlier deadlock and teardown crashes
lived.

### Several 4K video tracks

Per 4K P010 frame: 24.9 MB; RGBA16F working texture at full resolution: 66 MB (33 MB at RGBA8). At 30 fps the copy
traffic of four streams is about 3 GB/s each way, affordable for the memory system but not for one GUI thread (A.6).
Hardware decode has headroom on an M4 (A.3). The M1 8 GB minimum machine is memory-limited: four full-resolution 4K
streams will not fit alongside decoder surfaces, so proxies and half-resolution previews stay mandatory there, in
every option. The two languages differ by a few percent in CPU-side cost; zero-copy plus the render thread decide the
ceiling.

### Build, distribution and maintenance

| Aspect | C++ (CMake) | Rust (cargo) | Today |
| --- | --- | --- | --- |
| Toolchain on the dev Mac | Apple clang 21 present; **no CMake, Ninja or pkg-config installed**; Qt 6.11.2 SDK needed (**the PySide6 wheel ships no Qt headers**) | rustc/cargo 1.98.1 present; C++ shim still needs the Qt SDK | `pip` only |
| CI | 3-OS matrix, Qt SDK install step, sanitizer jobs | 3-OS matrix; `maturin` if PyO3 | Windows tests already 17-26 min |
| Packaging | PyInstaller bundles `libkut_engine` and the LGPL FFmpeg dylibs; expect tens of MB per OS | same | 53 / 71 / 105 MB |
| Signing | macOS is ad hoc and not notarized; every added dylib must be signed once notarization is introduced | same | same |
| Linux baseline | build on ubuntu-24.04 (glibc 2.39, the announced minimum); match the wheel's libstdc++ ABI | same | same |
| LGPL | FFmpeg shared libraries only, notices and source offer | static linking is the Rust default and must be avoided for FFmpeg | PyAV LGPL build planned |

Mitigation common to both: the native library is optional at build time, prebuilt artefacts are cached in CI, and
contributors without a compiler run the Python path.

### Progressive migration without rewriting the application

C++ is the only option where the port is mechanical. `RhiExecutor` and `GpuPreviewWidget` (about 700 lines) were
written against the QRhi API and map name-for-name to C++; the 12 fragment shaders and the vertex shader in
`assets/shaders/*.qsb`, the `FramePlan` contract, the numpy `reference_frame`, and the fake-backend tests stay valid. A
`wgpu` executor would need shader translation and re-validation on three platforms, and a Qt-free rasteriser would
break parity with the deterministic Qt rasteriser (`core/mograph_raster.py`, 974 lines plus 75 `QPainter`/`QImage`
uses). Every phase below keeps the Python path as fallback.

### Safety and maintainability

Rust is better here and this is the strongest argument for it. For C++ the mitigations are structural: keep the native
code under about 8k lines; RAII everywhere and no ownership crossing the ABI; exceptions caught at the boundary;
AddressSanitizer, UBSanitizer and ThreadSanitizer jobs; clang-tidy; a fuzz harness on container input; the crash guard
and fallback; and the rule of no native-to-Python callbacks.

### Maintainer and AI-assisted development

The maintainer works with an AI coding assistant. Rust's compiler gives strong automatic review of generated
concurrent code, while interop code (hardware frames, drivers) has far more worked examples in C, C++ and
Objective-C++. This is a draw; it is not used in the verdict.

## Technical risks

| ID | Risk | Likelihood | Impact | Mitigation | Early signal |
| --- | --- | --- | --- | --- | --- |
| R1 | Qt private or unstable API (QRhi, video buffers) changes on a Qt bump | High over 12 months | Medium | exact Qt pin equal to PySide6's; private use isolated in one file; canary CI on the next Qt; load-time check and Python fallback | canary build breaks |
| R2 | Compiler or ABI mismatch with the Qt Company's wheels (MSVC runtime, libstdc++) | Medium | High (crash at load) | same compiler families; load smoke test creating a `QRhi` and a texture; CI matrix | smoke test fails on one OS |
| R3 | Native crash kills the app (driver, decoder, UB) | Medium | High | sanitizer jobs; watchdog on decoder workers; crash guard extended; Option D if the field rate warrants | crash-guard markers after releases |
| R4 | Windows and Linux interop unverified; CI runners have no GPU | High | High | macOS first; real-hardware lab (two vendors on Windows, Intel and AMD on Linux); CPU path stays default off macOS until validated | B7/B8 fail or vary by vendor |
| R5 | Viewer overlays break if rendering leaves Qt compositing (native child windows cannot be overlapped by translucent Qt widgets on every platform; to verify) | Medium | High | prefer texture sharing into `QRhiWidget`; no native child window unless overlays move into the engine | B4 overlay test fails |
| R6 | Python and native executors diverge | High | Medium | one `FramePlan`, same `.qsb`, dual-run parity per shader against the numpy oracle | parity test deltas |
| R7 | Scope creep into a rewrite (Olive 0.2, a ground-up rewrite begun after 0.1.2 in 2019, was still alpha in recent sources) | Medium | High | narrow scope, gates, Phase 0 first, no feature parity beyond `FramePlan` | native LOC above 8k |
| R8 | Build and release complexity; contributor friction; CI minutes | High | Medium | native optional; cached artefacts; separate CI job | PR turnaround grows |
| R9 | LGPL compliance for bundled FFmpeg, and codec licensing | Medium | Medium | LGPL-only build, dynamic libraries, hardware codecs through OS APIs; a legal read before shipping (this ADR is not legal advice) | licence audit |
| R10 | Memory pressure on 8 GB machines with several 4K tracks | High | High on the minimum machine | texture and frame budgets with LRU (exists), half-resolution, proxies, regulator that refuses beyond budget | B12 peak RSS |
| R11 | Export parity: chroma rule (PR #78) and colour pipeline must match bit-for-bit | Low | Medium | the rules live in Python plan and shaders, which the native layer executes unchanged | B11 deltas |
| R12 | Maintainer bandwidth and bus factor from a second toolchain | Medium | High | small scope, documentation, tests, ADR-driven gates | Phase 2 slips |

## Benchmarks required

Thresholds are proposals for the maintainer to confirm. "Today" values come from `docs/perf/gpu.json` and the
spike report.

| ID | Question | Method | Metrics and proposed pass criteria | Machines | Feeds |
| --- | --- | --- | --- | --- | --- |
| B0 | What causes the 22.6 ms p95 loop lag with effects? | Instruments (Time Profiler, Metal System Trace), signposts around `render()` and the drawable wait | CPU submit vs drawable wait vs GPU time; diagnostic | M4, M1 8 GB | G0 |
| B1 | How much do dedupe and a process pool buy for cold rasterisation? | Synthetic jobs done (`tools/perf/gil_scaling.py`); next, repeat with the real rasteriser on the Onlyfab and Singapore GP projects | rasters 1,499 -> about 223; speed-up at 4 and 8 workers (reference run, 8 processes: x4.5 text outline, x2.9 paint calls; about ±30 %) | M4, M1 | G0 |
| B2 | What is the decode ceiling per platform? | `tools/perf/decode_scaling.py` on 1/2/4/6 streams (4K HEVC 10-bit on VideoToolbox done, see A.3); add H.264 4K, ProRes proxy, 8K-class if relevant; Windows D3D11VA/NVDEC/QSV; Linux VAAPI | aggregate and per-stream fps, CPU %, RSS, power; per-stream fps at least 1.5x real time at N=4 | M4, M1 8 GB, Win x2 vendors, Linux | G1, G2 |
| B3 | Is true zero-copy achievable on macOS? | spike: VT -> `CVMetalTextureCache` -> `QRhiTexture::createFrom` -> compose -> present (C++); a Rust variant B3-rust for the decision rule | GUI-thread CPU <= 0.5 ms per 4K stream (today 7.2); zero CPU copies (profiler); pixel-identical to the current path on chroma test patterns | M4, M1 | **G1** |
| B4 | Can rendering leave the GUI thread without breaking the viewer? | engine render thread + shared texture drawn by a `QRhiWidget`; overlays and transparent `QGraphicsView` kept | loop lag p95 <= 5 ms at 4K + effects (today 22.6); frame-pacing jitter <= 2 ms; overlay unaffected | M4, M1 | **G1** |
| B5 | Do four 4K tracks play for real? | 4 x 4K HEVC 10-bit, 2 graphic layers, grade, blend; 10 min | 30 fps with <= 1 dropped frame per 1,000; temperature and power logged; on M1 define the degraded tier (half-res) with the same criteria | M4, M1 8 GB | G2 |
| B6 | Scrubbing latency | jumps on 4K GOP 120 and on proxies, persistent decoders + frame cache | median <= 50 ms on proxies, <= 300 ms on originals (today 776 ms HW / 1,947 ms CPU) | M4, M1 | G2 |
| B7 | Windows interop (D3D11VA onto Qt's device) | spike, array-slice handling, shared handles | zero CPU copies; works on two GPU vendors; fallback tested | Windows NVIDIA + Intel/AMD | G2, enables Windows |
| B8 | Linux interop (VAAPI to GL via DMA-BUF) | spike on Mesa; document NVIDIA behaviour | same as B7; define the supported matrix | Linux Intel, AMD | G2, enables Linux |
| B9 | What does packaging cost? | PyInstaller with `libkut_engine` and LGPL FFmpeg dylibs; signing dry run | package growth <= 60 MB per OS; start-up overhead <= 150 ms; CI job time delta recorded; hardened-runtime signing works | CI + Mac | G1 (tie-breaker), G2 |
| B10 | Is the native layer robust? | ASan/UBSan/TSan runs of native tests; fault injection (device lost, decoder failure); 8 h playback soak | 0 sanitizer findings; RSS growth < 5 %; every injected fault falls back | Mac, Linux CI | G2 |
| B11 | Export speed and parity through the engine | Onlyfab via engine vs FFmpeg reference | >= x3 vs 28.2 s reference (<= 9.5 s), x5 with async readback / VT zero-copy; layers <= 1/255, video within the documented chroma rule | M4 | G3 |
| B12 | Memory on the minimum machine | RSS and texture totals for 1/2/4 tracks, full and half resolution | 1 stream <= 1.2 GB (today 1.0 GB); 4 streams at half-res <= 3.5 GB; no swap thrash | M1 8 GB | G2 |

## Migration plan

Each phase ships alone, keeps the Python path working, and ends with a gate. Effort figures are my own relative
estimates with low confidence; they are not commitments.

| Phase | Content | Effort | Gate |
| --- | --- | --- | --- |
| **0. Language-independent fixes (Python)** | per-content graphic textures animated by matrix and opacity (spike projection: rasterisation 11 s -> 1-2 s, export x1.5 -> x3); process-pool rasterisation for cold preparation; asynchronous readback; ADR-0001 step 1 (PyAV on an LGPL FFmpeg, persistent decoders, proxies feed the monitor); frame-time telemetry; B0, B1, B2 baselines | 3-6 weeks | **G0:** re-measure on M4 and M1 8 GB. If the declared target is met and multi-4K is not a goal, stop here and revisit native in a later release |
| **1. Spikes (no merge to main)** | B3 (C++ and Rust), B4, B9; report in `docs/perf/`; apply the decision rule | 2-4 weeks | **G1:** language confirmed or flipped; go/no-go on Phase 2 |
| **2. `kut_engine` v0 behind a flag** | `native/` CMake project, C ABI, `core/native_engine.py` loader and handshake; executes `FramePlan` with QRhi; engine-owned decoder pool and frame cache; render thread + shared canvas texture; macOS first; dual-run parity tests; crash guard; CI on 3 OS with sanitizer jobs | 8-12 weeks | **G2:** B5, B6, B10, B12 pass on M4 and M1 (degraded tier allowed); packaging dry run |
| **3. Export through the engine** | zero-copy hardware encode (VideoToolbox first), reference export stays; B11 | 3-5 weeks | **G3:** parity and speed targets |
| **4. Windows then Linux** | B7, B8 results turned into opt-in, then default, per vendor/driver allow-list | 4-8 weeks per platform, hardware-lab dependent | per-platform validation matrix |
| **5. Optional** | retire duplicated Python paths after several releases of telemetry; native rasteriser only if Phase 0 left rasterisation a bottleneck; headless CLI on the same library (script mode) | open | none |

**Rollback:** `KUT_STUDIO_NATIVE_ENGINE=off` (and automatic fallback on handshake or smoke-test failure). **Never
moved:** the items under "Stays Python" in the Context section.

## Consequences

**Easier:**
- several concurrent 4K tracks and 4K effects without UI lag (if the benchmarks confirm the hypotheses);
- hardware export without a CPU round trip;
- parallel CPU work with no GIL inside the engine;
- a single pinned FFmpeg in-process, removing the per-OS FFmpeg version matrix for preview.

**Harder:**
- a second toolchain, Qt SDK matching, sanitizer discipline, and native crash diagnostics;
- three platform interop paths to maintain and validate on real hardware;
- two executors to keep in parity (mitigated by one plan, one shader set and the oracle).

**To revisit:**
- the language choice at G1, and again if Qt ships public native-texture API;
- Option D (out-of-process) if native crash rates are high;
- free-threaded CPython and PySide6 compatibility, yearly;
- the macOS signing and notarization timeline, since added dylibs make ad-hoc distribution more fragile.

## Open decisions for the maintainer

1. Is multi-4K a product goal, or is the target 1080p social edits on an M1 Air? (This decides whether Phase 1 ever
   starts after G0.)
2. Accept macOS first, with Windows and Linux staged behind a hardware lab and CPU fallback until validated?
3. Accept an optional native build so contributors without a compiler keep the Python-only workflow?
4. Viewer overlays: keep Qt compositing with texture sharing (recommended), or accept engine-drawn overlays?
5. Budget for real-hardware testing (Windows NVIDIA, Intel or AMD; Linux Intel, AMD)?
6. Timing of macOS signing and notarization relative to adding native libraries?

## Action Items

1. [ ] Maintainer review of this ADR; answer the open decisions above.
2. [ ] Phase 0: open tracking issues for per-content textures, process-pool rasterisation, asynchronous readback and
   ADR-0001 step 1. Benchmarks: B1 (thread and process scaling) and B2 (decode scaling) are in
   `tools/perf/gil_scaling.py` and `tools/perf/decode_scaling.py`, with reference results in
   `docs/perf/gil-scaling.json` and `docs/perf/decode-scaling.json`. B0 and the real-rasteriser variant of B1
   (needs a project) remain open.
3. [ ] Install the spike toolchain on the dev Mac (CMake, Ninja, Qt 6.11.2 SDK) only when Phase 1 is approved.
4. [ ] Add a pointer from ADR-0001 "To revisit" to this ADR once accepted.

## Appendix A: measurements (2026-10-11, Apple M4)

The reference results are the JSON files in `docs/perf/`, produced by `tools/perf/gil_scaling.py` and
`tools/perf/decode_scaling.py`. The first figures in A.1 and A.3 came from earlier scratch runs of the same
experiments; where they differ, the JSON files govern.

**A.1 GIL scaling**, reference run `docs/perf/gil-scaling.json` (pools warmed, median of 3 runs, serial time in ms per
job, speed-up versus serial):

| Job | Serial | 4 threads | 8 threads | 4 processes | 8 processes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Smooth downscale of a 4K image (one long Qt call) | 2.8 ms | x0.87 | x0.78 | x1.08 | x1.14 |
| `convertToFormat` of a 4K image (one long Qt call) | 1.4 ms | x0.99 | x1.00 | x0.98 | x0.95 |
| 2,000 small `QPainter` calls, 1080x1920 | 47.7 ms | x0.88 | x0.89 | x2.16 | x2.86 |
| Text outline (`strokePath` + `fillPath`) like the motion-graphics rasteriser | 3.6 ms | x1.00 | x1.00 | x3.13 | x4.48 |
| `QImage.save` PNG, 1080x1920 | 40.5 ms | x3.88 | x5.12 | x3.87 | x5.16 |
| Pure Python loop (control) | 17.2 ms | x1.00 | x0.99 | x3.52 | x4.71 |

The short jobs (under 10 ms) lose part of their process speed-up to dispatch overhead, so the process numbers for
them are a lower bound. Process figures exclude returning results (see A.1 above).

**A.3 Decode scaling:** see the table in 2.3. Reference clip, generated by `tools/perf/decode_scaling.py`: 3840x2160,
30 fps, 180 frames, HEVC Main 10 (`yuv420p10le`), GOP 120, about 345 Mb/s (`libx265`, CRF 20, noise over `testsrc2`).
Decoded to a null sink with `ffmpeg -hwaccel videotoolbox`, with and without `-hwaccel_output_format videotoolbox_vld`.
Each figure is the median of 3 runs after one untimed run; the wall time includes FFmpeg start-up, which is not
isolated. The scratch clip mentioned in 2.3 (83 Mb/s, `hevc_videotoolbox`) is not in the repository.

**A.4 Thread check:** `QRhiWidget` subclass, `WA_DontShowOnScreen`, API reported as Metal; `initialize` and `render`
both on the main thread.

**A.5 Binding inventory:** read from `PySide6/QtGui.pyi` and `QtMultimedia.pyi` of PySide6 6.11.2.

**Recorded values reused:** `docs/perf/gpu.json` (monitor timings, decode, scrubbing) and
`docs/perf/gpu-export-spike.md` (Onlyfab export 28.2 s -> 19.3 s; 57 % rasterisation, 34 % synchronous readback;
223 distinct contents versus 1,499 rasters).

## Appendix B: external sources

Search results were consulted on 2026-10-11. Items marked *to verify* were not confirmed in primary documentation.

- Qt, QRhi class reference (no compatibility guarantees; single-thread use):
  <https://doc.qt.io/qt-6/qrhi.html>
- Qt, QRhiWidget: <https://doc.qt.io/qt-6.11/qrhiwidget.html>
- Qt Multimedia private hardware-buffer header (`mapTextures`, texture wrappers):
  <https://codebrowser.dev/qt6/qtmultimedia/src/multimedia/video/qhwvideobuffer_p.h.html>
- Gyroflow, used technologies (Rust core, Qt/QML UI, FFmpeg, `wgpu`, Qt RHI zero-copy preview; the maintainers
  describe the OpenCL/`wgpu` interop as hard): <https://docs.gyroflow.xyz/app/technical-details/used-technologies>
- cxx-qt (KDAB) release notes, 0.7: <https://www.kdab.com/cxx-qt-0-7/>. No QRhi coverage found (*to verify*).
- `ffmpeg-next` (maintenance mode, API frozen): <https://docs.rs/crate/ffmpeg-next/9.0.0/source/README.md>;
  `rsmpeg`: <https://docs.rs/crate/rsmpeg/0.18.0>
- `wgpu-hal` Vulkan device (DMA-BUF import documented; Metal and D3D12 external import *to verify*):
  <https://docs.rs/wgpu-hal/30.0.0/wgpu_hal/vulkan/struct.Device.html>
- Dawn, shared texture memory (IOSurface, DXGI, DMA-BUF design reference):
  <https://dawn.googlesource.com/dawn/+/5a54d9e9e498edfcaa73d0d45bfcc8ac931bf240/docs/dawn/features/shared_texture_memory.md>
- Olive (rewrite history and status): <https://en.wikipedia.org/wiki/Olive_(software)>
