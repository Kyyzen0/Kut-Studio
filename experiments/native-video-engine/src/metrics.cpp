#include "kut_engine/metrics.hpp"

namespace kut {

Percentiles percentiles_us(std::vector<double> samples_us) {
    Percentiles out;
    if (samples_us.empty()) {
        return out;
    }
    std::sort(samples_us.begin(), samples_us.end());
    auto at = [&](double q) {
        const double idx = q * static_cast<double>(samples_us.size() - 1);
        const std::size_t i = static_cast<std::size_t>(idx);
        return samples_us[i];
    };
    out.p50 = at(0.50);
    out.p95 = at(0.95);
    out.p99 = at(0.99);
    return out;
}

void EngineMetrics::reset() {
    std::lock_guard lock(mu_);
    frames_decoded = 0;
    frames_presented = 0;
    frames_dropped_queue = 0;
    frames_late = 0;
    seek_count = 0;
    bytes_copied = 0;
    copy_ops = 0;
    decode_latency_us.clear();
    present_latency_us.clear();
    e2e_latency_us.clear();
    ui_stall_us.clear();
    displayed_fps = 0;
    hardware_decode = false;
    hw_backend.clear();
    notes.clear();
}

void EngineMetrics::note_decode(double latency_us, std::size_t bytes, std::size_t copies) {
    std::lock_guard lock(mu_);
    ++frames_decoded;
    decode_latency_us.push_back(latency_us);
    bytes_copied += bytes;
    copy_ops += copies;
}

void EngineMetrics::note_present(double queue_latency_us, double e2e_us, bool late) {
    std::lock_guard lock(mu_);
    ++frames_presented;
    present_latency_us.push_back(queue_latency_us);
    e2e_latency_us.push_back(e2e_us);
    if (late) {
        ++frames_late;
    }
}

void EngineMetrics::note_copy(std::size_t bytes, std::size_t copies) {
    std::lock_guard lock(mu_);
    bytes_copied += bytes;
    copy_ops += copies;
}

void EngineMetrics::note_ui_stall(double stall_us) {
    std::lock_guard lock(mu_);
    ui_stall_us.push_back(stall_us);
}

Percentiles EngineMetrics::decode_pct() const {
    std::lock_guard lock(mu_);
    return percentiles_us(decode_latency_us);
}

Percentiles EngineMetrics::present_pct() const {
    std::lock_guard lock(mu_);
    return percentiles_us(present_latency_us);
}

Percentiles EngineMetrics::e2e_pct() const {
    std::lock_guard lock(mu_);
    return percentiles_us(e2e_latency_us);
}

Percentiles EngineMetrics::ui_stall_pct() const {
    std::lock_guard lock(mu_);
    return percentiles_us(ui_stall_us);
}

}  // namespace kut
