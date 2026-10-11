# Kut-Studio native video engine — experimental POC v0.1

Isolated C++20 preview prototype used to **measure** whether a native decode/present path can improve preview performance versus the production Python/PySide6 pipeline.

**This is not production code.** It is not linked from `main.py`, does not replace the timeline monitor, and adds **no** mandatory dependency to the shipped app.

Related decisions: repository `docs/adr/0001-…`, `docs/adr/0003-…`.

## Contents

| Path | Role |
| --- | --- |
| `ARCHITECTURE_AUDIT.md` | Phase A audit of the production preview path |
| `include/` `src/` | C++ engine (decoder, queue, metrics, CPU presenter, Metal scaffold) |
| `apps/kut_engine_cli.cpp` | Headless CLI bench / smoke tool |
| `apps/preview_host.py` | Minimal experimental PySide6 host |
| `bindings/` | pybind11 module `kut_engine_native` |
| `tests/` | Automated C++ tests |
| `benches/` | Media generator + A/B/C/D harness |
| `BENCHMARK_REPORT.md` | Measured results on the validation host |
| `docs/ZERO_COPY_STUDY.md` | Zero-copy feasibility (not claimed working here) |
| `MIGRATION_RECOMMENDATION.md` | Option A/B/C recommendation |

## Prerequisites

- CMake ≥ 3.20, C++20 compiler, pkg-config
- FFmpeg development libraries: `libavcodec` `libavformat` `libavutil` `libswscale`
- Python 3.10+ with `pybind11` (and `PySide6` for the experimental host)
- `ffmpeg` / `ffprobe` CLIs for media generation and baseline A

Ubuntu example:

```bash
sudo apt-get install -y g++ libstdc++-14-dev python3-dev pkg-config \
  libavcodec-dev libavformat-dev libavutil-dev libswscale-dev
pip install pybind11 PySide6
```

## Build

```bash
cd experiments/native-video-engine
bash benches/generate_media.sh 6
PYBIND_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())")
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="$PYBIND_DIR"
cmake --build build -j$(nproc)
```

Outputs:

- `build/kut_engine_cli`
- `build/kut_engine_tests`
- `build/kut_engine_native*.so` (Python module)

## Test

```bash
export KUT_ENGINE_TEST_MEDIA=$PWD/media/test_1080p30.mp4
ctest --test-dir build --output-on-failure
# or:
./build/kut_engine_tests
```

AddressSanitizer: configure `-DKUT_ENGINE_ASAN=ON` when clang RT asan libs are installed. On the original validation host this was **NOT TESTED** (libs missing).

## Run

Headless CLI (realtime paced present):

```bash
./build/kut_engine_cli media/test_1080p30.mp4 --seconds 4
./build/kut_engine_cli media/test_4k30.mp4 --seconds 4 --hw   # falls back if no HW
./build/kut_engine_cli media/test_1080p30.mp4 --seconds 2 --throughput
```

Experimental PySide6 host (optional media argument):

```bash
PYTHONPATH=build python3 apps/preview_host.py media/test_1080p30.mp4
# headless:
QT_QPA_PLATFORM=offscreen PYTHONPATH=build python3 apps/preview_host.py media/test_1080p30.mp4
```

Benchmarks:

```bash
PYTHONPATH=build python3 benches/run_benchmarks.py --seconds 4 --trials 3
```

## Binding choice

**pybind11** was selected over Shiboken6 for this POC: the engine core does not use Qt types, so Shiboken’s Qt integration cost is unnecessary. PySide6 is only used in `apps/preview_host.py`.

## Thread ownership and lifetimes

- **Decode thread:** owned by `PreviewEngine`; joined in `stop()` / destructor.
- **Present:** caller thread (`run_present_for`) or Qt UI timer (`pump_present` in the host).
- **Frames:** `shared_ptr<VideoFrame>` through the bounded queue; FFmpeg contexts freed in `VideoDecoder::close()`.
- **Zero-copy:** not claimed. Default path uses swscale → RGB24 → CPU present copy. See `docs/ZERO_COPY_STUDY.md`.

## Known limitations

- Metal presenter is a scaffold (Apple-only); Linux validates the CPU presenter.
- Hardware decode is probed; without a device it falls back to software (logged).
- No timeline compositing / effects graph (single stream preview).
- Not integrated into Kut-Studio production UI.
- Generated `media/*.mp4` are gitignored; regenerate with `benches/generate_media.sh`.

## License note

Kut-Studio is MIT. If this experiment is ever shipped in-process, FFmpeg must be an **LGPL** build (see ADR-0001). The system FFmpeg used for local POC builds may differ; do not copy GPL codecs into a release binary without a license review.
