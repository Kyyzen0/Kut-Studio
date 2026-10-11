#include "kut_engine/renderer.hpp"

#include <cstring>

namespace kut {

EngineError CpuPresenter::initialize(int width, int height) {
    if (width <= 0 || height <= 0) {
        return EngineError::InvalidState;
    }
    width_ = width;
    height_ = height;
    present_buf_.assign(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3, 0);
    return EngineError::Ok;
}

void CpuPresenter::shutdown() {
    present_buf_.clear();
    width_ = height_ = 0;
}

EngineError CpuPresenter::present(const VideoFramePtr& frame, EngineMetrics& metrics) {
    if (!frame) {
        return EngineError::InvalidState;
    }
    const auto now = steady_us();
    const double queue_lat = frame->enqueued_us > 0 ? static_cast<double>(now - frame->enqueued_us) : 0.0;
    const double e2e = frame->decode_done_us > 0 ? static_cast<double>(now - frame->decode_done_us) : 0.0;

    if (frame->format == PixelFormat::RGB24) {
        const std::size_t n = std::min(present_buf_.size(), frame->data.size());
        if (n > 0) {
            std::memcpy(present_buf_.data(), frame->data.data(), n);
            metrics.note_copy(n, 1);
        }
        metrics.note_present(queue_lat, e2e, false);
        return EngineError::Ok;
    }
    metrics.note_present(queue_lat, e2e, false);
    return EngineError::Ok;
}

std::unique_ptr<IRenderer> create_default_renderer() {
    return std::make_unique<CpuPresenter>();
}

std::unique_ptr<IRenderer> create_metal_renderer() {
#if defined(KUT_ENGINE_HAS_METAL)
    // Implemented in renderer_metal.mm when building on Apple.
    extern std::unique_ptr<IRenderer> create_metal_renderer_impl();
    return create_metal_renderer_impl();
#else
    return nullptr;
#endif
}

}  // namespace kut
