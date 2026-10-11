#pragma once

#include "kut_engine/frame.hpp"

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <mutex>
#include <optional>

namespace kut {

/// Thread-safe bounded queue of frames with backpressure and cancellation.
class FrameQueue {
public:
    explicit FrameQueue(std::size_t capacity);

    FrameQueue(const FrameQueue&) = delete;
    FrameQueue& operator=(const FrameQueue&) = delete;

    /// Push a frame. If full and drop_oldest, drops the oldest and returns true.
    /// If full and !drop_oldest, waits until space or cancel/timeout.
    bool push(VideoFramePtr frame, bool drop_oldest, std::chrono::milliseconds wait);

    /// Pop next frame, waiting up to `wait`. Returns nullopt on timeout/cancel/closed.
    std::optional<VideoFramePtr> pop(std::chrono::milliseconds wait);

    void cancel();
    void close();
    void reset();

    std::size_t size() const;
    std::size_t capacity() const { return capacity_; }
    std::uint64_t dropped() const;
    bool cancelled() const;

private:
    mutable std::mutex mu_;
    std::condition_variable cv_not_empty_;
    std::condition_variable cv_not_full_;
    std::deque<VideoFramePtr> q_;
    std::size_t capacity_;
    bool cancelled_ = false;
    bool closed_ = false;
    std::uint64_t dropped_ = 0;
};

}  // namespace kut
