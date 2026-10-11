#include "kut_engine/frame_queue.hpp"
#include "kut_engine/metrics.hpp"

namespace kut {

FrameQueue::FrameQueue(std::size_t capacity) : capacity_(capacity == 0 ? 1 : capacity) {}

bool FrameQueue::push(VideoFramePtr frame, bool drop_oldest, std::chrono::milliseconds wait) {
    std::unique_lock lock(mu_);
    if (cancelled_ || closed_) {
        return false;
    }
    if (q_.size() >= capacity_) {
        if (drop_oldest) {
            q_.pop_front();
            ++dropped_;
        } else {
            if (!cv_not_full_.wait_for(lock, wait, [&] {
                    return cancelled_ || closed_ || q_.size() < capacity_;
                })) {
                return false;
            }
            if (cancelled_ || closed_) {
                return false;
            }
        }
    }
    frame->enqueued_us = steady_us();
    q_.push_back(std::move(frame));
    cv_not_empty_.notify_one();
    return true;
}

std::optional<VideoFramePtr> FrameQueue::pop(std::chrono::milliseconds wait) {
    std::unique_lock lock(mu_);
    if (!cv_not_empty_.wait_for(lock, wait, [&] {
            return cancelled_ || closed_ || !q_.empty();
        })) {
        return std::nullopt;
    }
    if (q_.empty()) {
        return std::nullopt;
    }
    auto frame = std::move(q_.front());
    q_.pop_front();
    cv_not_full_.notify_one();
    return frame;
}

void FrameQueue::cancel() {
    {
        std::lock_guard lock(mu_);
        cancelled_ = true;
    }
    cv_not_empty_.notify_all();
    cv_not_full_.notify_all();
}

void FrameQueue::close() {
    {
        std::lock_guard lock(mu_);
        closed_ = true;
    }
    cv_not_empty_.notify_all();
    cv_not_full_.notify_all();
}

void FrameQueue::reset() {
    std::lock_guard lock(mu_);
    q_.clear();
    cancelled_ = false;
    closed_ = false;
    dropped_ = 0;
}

std::size_t FrameQueue::size() const {
    std::lock_guard lock(mu_);
    return q_.size();
}

std::uint64_t FrameQueue::dropped() const {
    std::lock_guard lock(mu_);
    return dropped_;
}

bool FrameQueue::cancelled() const {
    std::lock_guard lock(mu_);
    return cancelled_;
}

}  // namespace kut
