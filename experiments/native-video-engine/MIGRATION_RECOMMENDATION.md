# Migration recommendation — after POC v0.1

**Date:** 2026-10-11  
**Inputs:** [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md), [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md), [docs/ZERO_COPY_STUDY.md](docs/ZERO_COPY_STUDY.md), repo ADR-0001 / ADR-0003.

## Options

| Option | Meaning |
| --- | --- |
| **A** | Keep the existing PySide6 / FFmpeg / QRhi architecture; targeted optimizations only |
| **B** | Introduce a C++ native **preview** engine while keeping PySide6 for the shell |
| **C** | Expand native code across decode, compositing, effects, and export over multiple releases |

## Assessment against measured evidence

| Criterion | Finding from POC v0.1 |
| --- | --- |
| Performance gains | **Promising but incomplete.** Native SW decode sustains 1080p30/60 with 0 queue drops on this Linux VM. 4K30 falls to ~24 fps without HW. No proof yet of beating the production GPU monitor’s Apple M4 numbers, and no UI-thread lag comparison on Metal. |
| Integration complexity | **Moderate for preview-only** (pybind11 module + feature flag). **High** if QRhi/zero-copy sharing is required. |
| Stability / maintenance | RAII engine + tests are a good start; ASAN not yet green; FFmpeg ABI + packaging become permanent costs. |
| Cross-platform | Portable SW path works on Linux. Metal/D3D/VAAPI paths are stubs or unproven here. |
| Packaging | Extra shared library + LGPL FFmpeg (ADR-0001 license constraint) — do not ship GPL x264 in-process. |
| Effort | Option B preview slice: substantial. Option C full NLE engine: multi-release. |
| Preview vs export divergence | High risk if native preview outpaces FFmpeg reference without parity tests. |
| Modest hardware | 4K SW path already misses realtime on this VM → HW decode + NV12/GPU present is mandatory before promising 4K preview. |

## Recommendation

**Near term: Option A + continue ADR-0001 Option B (unified adaptive GPU / PyAV), using this POC as a measurement harness — not a production switch.**

**Do not migrate the production monitor to C++ yet.** Reasons:

1. The POC validates isolation and a clean C++ API, but **does not yet clear ADR-0003’s Option C gate** (no ≥30% p95 win vs a completed Option B on the minimum machine; no proven zero-copy; no Apple validation).
2. Most multi-track pain in production remains **product coverage** (faithful FFmpeg segments, single-track GPU monitor), which Option B targets without a native rewrite.
3. 4K on this host shows the SW RGB path is **not** enough; shipping B without HW surfaces would regress expectations.

**When to open Option B (native preview engine behind a flag):**

1. Re-run this harness on Apple Silicon with VT + Metal import (zero-copy study).
2. Finish ADR-0001 stages that remove segment-wait for multi-track social timelines.
3. If UI-thread lag / upload copies still dominate after Option B stage 4, wire `kut_engine` as an optional executor behind `gpu_composite` plans (Python remains source of truth).

**Option C (broad native expansion):** only after a successful gated Option B preview integration with parity tests against FFmpeg reference export. Prefer incremental surfaces: decode → present → compose → effects → encode feed.

## Staged path (if measurements later justify it)

1. Keep `experiments/native-video-engine` as the lab; do not merge into `ui/preview_panel.py` until gates pass.
2. Apple zero-copy spike; publish numbers beside this report.
3. Feature-flagged pybind executor for single-clip monitor only.
4. Multi-clip compose in native code sharing the same plan ABI as `core/gpu_composite.py`.
5. Revisit packaging (LGPL FFmpeg) and crash isolation (optional subprocess).

## Negative result (valid outcome)

If Apple spikes show native+QRhi interop cannot beat Option B uploads without private Qt APIs, **remain on Option A/B** and treat the C++ engine as a decode/export helper only. A well-documented negative result is acceptable per the POC mission.
