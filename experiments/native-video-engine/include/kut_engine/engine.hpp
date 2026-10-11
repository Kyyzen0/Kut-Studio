#pragma once

#include "kut_engine/decoder.hpp"
#include "kut_engine/frame_queue.hpp"
#include "kut_engine/metrics.hpp"
#include "kut_engine/renderer.hpp"
#include "kut_engine/types.hpp"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace kut {

enum class PlayState { Stopped, Playing, Paused };

/// Decode thread + bounded queue + present on caller thread (or dedicated present thread).
class PreviewEngine {
public:
    explicit PreviewEngine(EngineConfig config = {});
    ~PreviewEngine();

    PreviewEngine(const PreviewEngine&) = delete;
    PreviewEngine& operator=(const PreviewEngine&) = delete;

    EngineError open(const std::string& path);
    void close();

    const MediaInfo& info() const { return info_; }
    PlayState state() const { return state_.load(); }

    EngineError play();
    EngineError pause();
    EngineError stop();
    EngineError seek_us(std::int64_t target_us);

    /// Pump one present from the queue (call from UI timer or present thread).
    /// Returns Ok if a frame was presented, QueueFull-like empty as Cancelled when idle? uses Ok/InvalidState.
    EngineError pump_present();

    /// Run present loop for `duration_ms` on the calling thread (bench helper).
    /// If ``realtime`` is true, pace presents to media FPS (wall-clock).
    /// If false, present as fast as frames arrive (throughput mode).
    EngineError run_present_for(int duration_ms, bool realtime = true);

    EngineMetrics& metrics() { return metrics_; }
    const EngineMetrics& metrics() const { return metrics_; }

    void set_renderer(std::unique_ptr<IRenderer> renderer);

    /// Snapshot of last presented RGB24 frame (if convert_to_rgb24).
    bool copy_last_rgb(std::vector<std::uint8_t>& out, int& w, int& h) const;

private:
    void decode_loop();
    void join_decode();

    EngineConfig config_;
    MediaInfo info_{};
    std::unique_ptr<VideoDecoder> decoder_;
    std::unique_ptr<FrameQueue> queue_;
    std::unique_ptr<IRenderer> renderer_;
    EngineMetrics metrics_{};

    std::thread decode_thread_;
    std::atomic<PlayState> state_{PlayState::Stopped};
    std::atomic<bool> stop_decode_{false};
    std::atomic<bool> seek_pending_{false};
    std::atomic<std::int64_t> seek_target_us_{0};
    std::mutex seek_mu_;

    mutable std::mutex present_mu_;
    std::vector<std::uint8_t> last_rgb_;
    int last_w_ = 0;
    int last_h_ = 0;
};

}  // namespace kut
