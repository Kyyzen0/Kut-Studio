#include "kut_engine/engine.hpp"
#include "kut_engine/decoder.hpp"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

void print_pct(const char* name, kut::Percentiles p) {
    std::cout << "  " << name << "_us: p50=" << p.p50 << " p95=" << p.p95 << " p99=" << p.p99 << "\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
    std::cerr << "Usage: kut_engine_cli <media> [--hw] [--seconds N] [--seek-test] [--throughput]\n";
    return 2;
    }
    std::string path = argv[1];
    bool hw = false;
    int seconds = 4;
    bool seek_test = false;
    bool throughput = false;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--hw") hw = true;
        else if (a == "--seek-test") seek_test = true;
        else if (a == "--throughput") throughput = true;
        else if (a == "--seconds" && i + 1 < argc) seconds = std::atoi(argv[++i]);
    }

    auto probe = kut::probe_hw_decode();
    std::cout << "hw_probe: " << probe.summary << "\n";

    kut::EngineConfig cfg;
    cfg.decode_mode = hw ? kut::DecodeMode::HardwarePrefer : kut::DecodeMode::Software;
    cfg.queue_capacity = 8;
    cfg.convert_to_rgb24 = true;
    // Realtime: backpressure (no drops). Throughput: drop-oldest to measure max decode rate.
    cfg.drop_when_full = throughput;

    kut::PreviewEngine engine(cfg);
    const auto err = engine.open(path);
    if (err != kut::EngineError::Ok) {
        std::cerr << "open failed: " << kut::to_string(err) << "\n";
        return 1;
    }
    const auto& info = engine.info();
    std::cout << "media: " << info.width << "x" << info.height << " @" << info.fps
              << " codec=" << info.codec_name
              << " hw=" << (info.hardware_decode_active ? info.hw_backend : "off") << "\n";

    if (engine.play() != kut::EngineError::Ok) {
        std::cerr << "play failed\n";
        return 1;
    }
    const bool realtime = !throughput;
    std::cout << "present_mode=" << (realtime ? "realtime_paced" : "throughput") << "\n";
    engine.run_present_for(seconds * 1000, realtime);

    if (seek_test) {
        engine.seek_us(1'000'000);
        engine.run_present_for(1000, realtime);
        engine.seek_us(0);
        engine.run_present_for(1000, realtime);
    }

    engine.stop();
    const auto& m = engine.metrics();
    std::cout << "frames_decoded=" << m.frames_decoded
              << " frames_presented=" << m.frames_presented
              << " queue_drops=" << m.frames_dropped_queue
              << " displayed_fps=" << m.displayed_fps << "\n";
    std::cout << "bytes_copied=" << m.bytes_copied << " copy_ops=" << m.copy_ops
              << " seeks=" << m.seek_count << "\n";
    std::cout << "zero_copy_claimed=false (cpu_presenter / swscale path)\n";
    print_pct("decode_latency", m.decode_pct());
    print_pct("present_queue_latency", m.present_pct());
    print_pct("e2e_decode_to_present", m.e2e_pct());
    return 0;
}
