#pragma once

#include "kut_engine/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace kut {

/// Owned decoded frame (CPU memory). Lifetime is exclusive to the holder.
struct VideoFrame {
    int width = 0;
    int height = 0;
    PixelFormat format = PixelFormat::Unknown;
    int64_t pts_us = 0;
    int64_t decode_done_us = 0;   // steady-clock micros when decode finished
    int64_t enqueued_us = 0;
    std::uint64_t sequence = 0;
    std::size_t copy_count = 0;   // how many full-buffer copies created this frame
    std::size_t bytes_copied = 0;

    /// Contiguous plane storage (tight). For YUV420P: Y then U then V.
    /// For RGB24: packed RGB.
    std::vector<std::uint8_t> data;
    int linesize[3] = {0, 0, 0};

    std::size_t nbytes() const { return data.size(); }
};

using VideoFramePtr = std::shared_ptr<VideoFrame>;

VideoFramePtr make_frame(int width, int height, PixelFormat format);

}  // namespace kut
