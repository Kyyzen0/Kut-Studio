#include "kut_engine/engine.hpp"

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>

using namespace kut;
namespace fs = std::filesystem;

static int expect(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << "FAIL engine: " << msg << "\n";
        return 1;
    }
    return 0;
}

static std::string media_path() {
    if (const char* env = std::getenv("KUT_ENGINE_TEST_MEDIA")) {
        return env;
    }
    for (const char* p : {
             "media/test_1080p30.mp4",
             "../media/test_1080p30.mp4",
             "../../media/test_1080p30.mp4",
         }) {
        if (fs::exists(p)) return p;
    }
    return {};
}

int test_engine() {
    int fails = 0;
    const auto path = media_path();
    if (path.empty()) {
        std::cerr << "SKIP engine media tests\n";
        return 0;
    }

    EngineConfig cfg;
    cfg.decode_mode = DecodeMode::Software;
    cfg.queue_capacity = 4;
    PreviewEngine engine(cfg);

    fails += expect(engine.open(path) == EngineError::Ok, "open");
    fails += expect(engine.play() == EngineError::Ok, "play");
    fails += expect(engine.run_present_for(1500) == EngineError::Ok, "present 1.5s");
    fails += expect(engine.metrics().frames_decoded > 0, "decoded > 0");
    fails += expect(engine.metrics().frames_presented > 0, "presented > 0");

    fails += expect(engine.pause() == EngineError::Ok, "pause");
    fails += expect(engine.play() == EngineError::Ok, "resume");
    fails += expect(engine.seek_us(200'000) == EngineError::Ok, "seek");
    engine.run_present_for(500);

    // Repeated sessions
    fails += expect(engine.stop() == EngineError::Ok, "stop");
    fails += expect(engine.open(path) == EngineError::Ok, "reopen");
    fails += expect(engine.play() == EngineError::Ok, "replay");
    engine.run_present_for(500);
    fails += expect(engine.stop() == EngineError::Ok, "stop2");

    std::vector<std::uint8_t> rgb;
    int w = 0, h = 0;
    // May or may not have last frame depending on timing; not a hard fail if empty after stop.
    engine.copy_last_rgb(rgb, w, h);

    if (fails == 0) {
        std::cout << "PASS test_engine\n";
    }
    return fails ? 1 : 0;
}
