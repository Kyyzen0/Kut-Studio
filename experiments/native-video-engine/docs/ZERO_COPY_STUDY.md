# Zero-copy feasibility study (POC v0.1)

**Status:** Investigated; **not implemented as a proven zero-copy path** on the validation host.  
**Host:** Linux x86_64 cloud VM without `/dev/dri` (no VAAPI device node). Metal not available.

## Goal

```
HW decode surface → GPU texture → present
without an intermediate full-frame CPU copy
```

## Apple Silicon (VideoToolbox / Metal) — design analysis

| Topic | Finding |
| --- | --- |
| Decode output | VideoToolbox frames are typically `CVPixelBuffer` backed by **IOSurface** |
| Metal import | `CVMetalTextureCacheCreateTextureFromImage` can wrap IOSurface planes as `MTLTexture` |
| Formats | Common: NV12 / P010 bi-planar; RGB rarely stays on GPU through VT |
| Sync | Command buffer / shared event sync still required between decode completion and fragment sample |
| Ownership | `CVPixelBuffer` retain while Metal still references the texture; pool recycling is mandatory |
| True zero-copy? | **Feasible on Apple** for NV12→Metal sample→drawable **if** no CPU readback and no swscale |

This POC ships `renderer_metal.mm` as a **scaffold** that still `memcpy`s RGB24 into a shared `MTLBuffer`. That path is **explicitly not zero-copy**.

## What the current POC actually does

1. FFmpeg software decode (or HW prefer + `av_hwframe_transfer_data` download).
2. `swscale` to RGB24 (CPU conversion copy).
3. Queue transfer of owned `std::vector` buffers.
4. `CpuPresenter` `memcpy` into a present buffer.
5. Optional Python `bytes` snapshot for PySide `QImage`.

**Copy count per frame (typical software path): ≥ 2** (swscale + present), often **3** with Python host.

## Blockers on this validation host

1. No Metal (not macOS).
2. No `/dev/dri` → VAAPI device create fails → HW prefer falls back to software.
3. Production Kut-Studio PySide6 `QRhi` still cannot accept raw pointers / memoryviews for uploads (see repo `docs/gpu-preview.md`) — even a native zero-copy engine would need a native present surface or public texture import API to help the production UI.

## Recommendation

- Keep claiming **zero-copy = false** until an Apple (or Windows D3D11) spike measures import without `av_hwframe_transfer_data` and without RGB round-trip.
- Next spike (macOS): VT → `CVPixelBuffer` → `CVMetalTextureCache` → sample in shader → `CAMetalLayer`; metrics must show `copy_ops == 0` for the present hop.

## Conclusion

Zero-copy is **architecturally plausible on Apple Silicon** and **not demonstrated** in POC v0.1. An unsafe workaround (forcing IOSurface casts without lifetime proofs) is rejected.
