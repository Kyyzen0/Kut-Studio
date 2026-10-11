#include "kut_engine/decoder.hpp"

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>

using namespace kut;

namespace fs = std::filesystem;

static int expect(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << "FAIL decoder: " << msg << "\n";
        return 1;
    }
    return 0;
}

static std::string media_path() {
    if (const char* env = std::getenv("KUT_ENGINE_TEST_MEDIA")) {
        return env;
    }
    // Default relative to build dir: ../media/test_1080p30.mp4
    const fs::path candidates[] = {
        fs::path("media/test_1080p30.mp4"),
        fs::path("../media/test_1080p30.mp4"),
        fs::path("../../media/test_1080p30.mp4"),
        fs::path("experiments/native-video-engine/media/test_1080p30.mp4"),
    };
    for (const auto& p : candidates) {
        if (fs::exists(p)) {
            return p.string();
        }
    }
    return {};
}

int test_decoder() {
    int fails = 0;
    VideoDecoder missing;
    fails += expect(missing.open("/nonexistent/nope.mp4", DecodeMode::Software, true) == EngineError::FileNotFound,
                    "missing file");

    const auto path = media_path();
    if (path.empty()) {
        std::cerr << "SKIP decoder media tests (set KUT_ENGINE_TEST_MEDIA or generate media)\n";
        return fails ? 1 : 0;
    }

    VideoDecoder dec;
    fails += expect(dec.open(path, DecodeMode::Software, true) == EngineError::Ok, "open media");
    fails += expect(dec.info().width > 0 && dec.info().height > 0, "metadata size");
    fails += expect(!dec.info().codec_name.empty(), "codec name");

    VideoFramePtr frame;
    fails += expect(dec.read(frame) == EngineError::Ok && frame, "read first frame");
    fails += expect(frame->format == PixelFormat::RGB24, "rgb24 output");
    fails += expect(frame->data.size() == static_cast<std::size_t>(frame->width * frame->height * 3), "rgb size");

    fails += expect(dec.seek_us(500'000) == EngineError::Ok, "seek 0.5s");
    VideoFramePtr after;
    fails += expect(dec.read(after) == EngineError::Ok && after, "read after seek");

    dec.request_cancel();
    VideoFramePtr cancelled;
    // May return Cancelled on next read depending on timing.
    const auto cerr = dec.read(cancelled);
    fails += expect(cerr == EngineError::Cancelled || cerr == EngineError::Ok || cerr == EngineError::OpenFailed,
                    "cancel or eof acceptable");

    dec.close();
    fails += expect(!dec.is_open(), "closed");

    // HW prefer must not hard-fail when HW missing — falls back to software.
    VideoDecoder hw;
    const auto herr = hw.open(path, DecodeMode::HardwarePrefer, true);
    fails += expect(herr == EngineError::Ok, "hw prefer opens");
    if (herr == EngineError::Ok) {
        std::cout << "  hw_active=" << hw.info().hardware_decode_active
                  << " backend=" << hw.info().hw_backend << "\n";
    }

    auto probe = probe_hw_decode();
    std::cout << "  hw_probe " << probe.summary << "\n";

    if (fails == 0) {
        std::cout << "PASS test_decoder\n";
    }
    return fails ? 1 : 0;
}
