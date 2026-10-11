#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

namespace kut {

inline std::int64_t steady_us() {
    using namespace std::chrono;
    return duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
}

struct Percentiles {
    double p50 = 0;
    double p95 = 0;
    double p99 = 0;
};

Percentiles percentiles_us(std::vector<double> samples_us);

struct EngineMetrics {
    std::uint64_t frames_decoded = 0;
    std::uint64_t frames_presented = 0;
    std::uint64_t frames_dropped_queue = 0;
    std::uint64_t frames_late = 0;
    std::uint64_t seek_count = 0;
    std::uint64_t bytes_copied = 0;
    std::uint64_t copy_ops = 0;

    // Samples in microseconds
    std::vector<double> decode_latency_us;
    std::vector<double> present_latency_us;   // enqueue→present
    std::vector<double> e2e_latency_us;       // decode_done→present (partial path)
    std::vector<double> ui_stall_us;          // measured only when host reports

    double displayed_fps = 0;
    bool hardware_decode = false;
    std::string hw_backend;
    std::string notes;

    void reset();
    void note_decode(double latency_us, std::size_t bytes, std::size_t copies);
    void note_present(double queue_latency_us, double e2e_us, bool late);
    void note_copy(std::size_t bytes, std::size_t copies = 1);
    void note_ui_stall(double stall_us);

    Percentiles decode_pct() const;
    Percentiles present_pct() const;
    Percentiles e2e_pct() const;
    Percentiles ui_stall_pct() const;

private:
    mutable std::mutex mu_;
};

}  // namespace kut
