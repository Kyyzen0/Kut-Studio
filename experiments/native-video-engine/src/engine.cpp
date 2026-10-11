#include "kut_engine/engine.hpp"

#include <chrono>
#include <thread>

namespace kut {

PreviewEngine::PreviewEngine(EngineConfig config) : config_(std::move(config)) {
    queue_ = std::make_unique<FrameQueue>(config_.queue_capacity);
    renderer_ = create_default_renderer();
}

PreviewEngine::~PreviewEngine() { close(); }

void PreviewEngine::set_renderer(std::unique_ptr<IRenderer> renderer) {
    renderer_ = std::move(renderer);
}

void PreviewEngine::join_decode() {
    stop_decode_ = true;
    if (decoder_) {
        decoder_->request_cancel();
    }
    if (queue_) {
        queue_->cancel();
    }
    if (decode_thread_.joinable()) {
        decode_thread_.join();
    }
    stop_decode_ = false;
}

void PreviewEngine::close() {
    join_decode();
    state_ = PlayState::Stopped;
    if (renderer_) {
        renderer_->shutdown();
    }
    if (decoder_) {
        decoder_->close();
    }
    if (queue_) {
        queue_->reset();
    }
    info_ = {};
}

EngineError PreviewEngine::open(const std::string& path) {
    close();
    decoder_ = std::make_unique<VideoDecoder>();
    const auto err = decoder_->open(path, config_.decode_mode, config_.convert_to_rgb24);
    if (err != EngineError::Ok) {
        decoder_.reset();
        return err;
    }
    info_ = decoder_->info();
    metrics_.reset();
    metrics_.hardware_decode = info_.hardware_decode_active;
    metrics_.hw_backend = info_.hw_backend;
    if (renderer_) {
        renderer_->initialize(info_.width, info_.height);
    }
    queue_ = std::make_unique<FrameQueue>(config_.queue_capacity);
    return EngineError::Ok;
}

void PreviewEngine::decode_loop() {
    while (!stop_decode_) {
        if (state_.load() == PlayState::Paused) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            continue;
        }
        if (state_.load() != PlayState::Playing) {
            break;
        }
        if (seek_pending_.exchange(false)) {
            const auto target = seek_target_us_.load();
            decoder_->clear_cancel();
            const auto serr = decoder_->seek_us(target);
            if (serr == EngineError::Ok) {
                ++metrics_.seek_count;
            }
            // Drop buffered frames; keep the same queue object (decode thread owns it).
            while (queue_->pop(std::chrono::milliseconds(0))) {
            }
        }

        const auto t0 = steady_us();
        VideoFramePtr frame;
        const auto err = decoder_->read(frame);
        if (err == EngineError::Cancelled) {
            break;
        }
        if (err == EngineError::OpenFailed) {
            // EOF: stop playing.
            state_ = PlayState::Stopped;
            queue_->close();
            break;
        }
        if (err != EngineError::Ok || !frame) {
            break;
        }
        const auto t1 = steady_us();
        metrics_.note_decode(static_cast<double>(t1 - t0), frame->bytes_copied, frame->copy_count);

        // Push with retry under backpressure so we do not silently discard frames.
        while (!stop_decode_) {
            if (queue_->push(frame, config_.drop_when_full, std::chrono::milliseconds(100))) {
                break;
            }
            if (queue_->cancelled()) {
                stop_decode_ = true;
                break;
            }
            if (config_.drop_when_full) {
                // drop_oldest path already accounted inside the queue; nothing to retry.
                break;
            }
        }
        metrics_.frames_dropped_queue = queue_->dropped();
    }
}

EngineError PreviewEngine::play() {
    if (!decoder_ || !decoder_->is_open()) {
        return EngineError::InvalidState;
    }
    if (state_.load() == PlayState::Playing) {
        return EngineError::Ok;
    }
    if (state_.load() == PlayState::Paused) {
        state_ = PlayState::Playing;
        return EngineError::Ok;
    }
    // Après une fin de fichier, le thread précédent est terminé mais toujours joignable : std::thread ne peut pas être
    // remplacé dans cet état (std::terminate). On le joint, puis on repart du début si le fichier était à sa fin.
    join_decode();
    decoder_->clear_cancel();
    if (decoder_->eof()) {
        decoder_->seek_us(0);
    }
    if (queue_) {
        queue_->reset();
    }
    state_ = PlayState::Playing;
    decode_thread_ = std::thread([this] { decode_loop(); });
    return EngineError::Ok;
}

EngineError PreviewEngine::pause() {
    if (state_.load() != PlayState::Playing) {
        return EngineError::InvalidState;
    }
    state_ = PlayState::Paused;
    return EngineError::Ok;
}

EngineError PreviewEngine::stop() {
    join_decode();
    state_ = PlayState::Stopped;
    if (decoder_) {
        decoder_->clear_cancel();
    }
    if (queue_) {
        queue_->reset();
    }
    return EngineError::Ok;
}

EngineError PreviewEngine::seek_us(std::int64_t target_us) {
    if (!decoder_ || !decoder_->is_open()) {
        return EngineError::InvalidState;
    }
    seek_target_us_ = target_us;
    seek_pending_ = true;
    // If stopped, perform seek synchronously so the next play starts elsewhere.
    if (state_.load() == PlayState::Stopped) {
        const auto err = decoder_->seek_us(target_us);
        if (err == EngineError::Ok) {
            ++metrics_.seek_count;
        }
        seek_pending_ = false;
        return err;
    }
    return EngineError::Ok;
}

EngineError PreviewEngine::pump_present() {
    if (!renderer_) {
        return EngineError::InvalidState;
    }
    auto opt = queue_->pop(std::chrono::milliseconds(1));
    if (!opt) {
        return EngineError::InvalidState;
    }
    VideoFramePtr frame = std::move(*opt);
    const auto err = renderer_->present(frame, metrics_);
    if (err == EngineError::Ok && frame->format == PixelFormat::RGB24) {
        std::lock_guard lock(present_mu_);
        last_rgb_ = frame->data;  // extra snapshot copy for Python host (counted below)
        last_w_ = frame->width;
        last_h_ = frame->height;
        metrics_.note_copy(frame->data.size(), 1);
    }
    return err;
}

EngineError PreviewEngine::run_present_for(int duration_ms, bool realtime) {
    using clock = std::chrono::steady_clock;
    const auto end = clock::now() + std::chrono::milliseconds(duration_ms);
    const auto start = clock::now();
    std::uint64_t presented_at_start = metrics_.frames_presented;
    const double fps = info_.fps > 1.0 ? info_.fps : 30.0;
    const auto frame_period = std::chrono::duration<double>(1.0 / fps);
    auto next_deadline = clock::now();

    while (clock::now() < end) {
        if (state_.load() == PlayState::Stopped && queue_->size() == 0) {
            break;
        }
        const auto err = pump_present();
        if (err != EngineError::Ok) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        if (realtime) {
            next_deadline += std::chrono::duration_cast<clock::duration>(frame_period);
            const auto now = clock::now();
            if (next_deadline > now) {
                std::this_thread::sleep_until(next_deadline);
            } else {
                // Fell behind: count as late and resync deadline.
                metrics_.frames_late += 1;
                next_deadline = now;
            }
        }
    }
    const double elapsed = std::chrono::duration<double>(clock::now() - start).count();
    if (elapsed > 0) {
        metrics_.displayed_fps =
            static_cast<double>(metrics_.frames_presented - presented_at_start) / elapsed;
    }
    metrics_.frames_dropped_queue = queue_ ? queue_->dropped() : 0;
    return EngineError::Ok;
}

bool PreviewEngine::copy_last_rgb(std::vector<std::uint8_t>& out, int& w, int& h) const {
    std::lock_guard lock(present_mu_);
    if (last_rgb_.empty()) {
        return false;
    }
    out = last_rgb_;
    w = last_w_;
    h = last_h_;
    return true;
}

}  // namespace kut
