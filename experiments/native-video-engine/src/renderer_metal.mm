// Apple-only Metal presenter scaffold.
// This POC does NOT claim zero-copy. A future path would import CVPixelBuffer /
// IOSurface into MTLTexture; see docs/ZERO_COPY_STUDY.md.

#if defined(__APPLE__)

#import <Metal/Metal.h>
#import <Foundation/Foundation.h>

#include "kut_engine/renderer.hpp"

#include <cstring>

namespace kut {
namespace {

class MetalPresenter final : public IRenderer {
public:
    std::string name() const override { return "metal_presenter_scaffold"; }
    EngineError initialize(int width, int height) override {
        width_ = width;
        height_ = height;
        device_ = MTLCreateSystemDefaultDevice();
        if (!device_) {
            return EngineError::HwUnavailable;
        }
        // Allocate a shared buffer as a stand-in present target (CPU-visible).
        const NSUInteger nbytes = static_cast<NSUInteger>(width) * static_cast<NSUInteger>(height) * 3;
        buffer_ = [device_ newBufferWithLength:nbytes options:MTLResourceStorageModeShared];
        return buffer_ ? EngineError::Ok : EngineError::OutOfMemory;
    }
    void shutdown() override {
        buffer_ = nil;
        device_ = nil;
        width_ = height_ = 0;
    }
    EngineError present(const VideoFramePtr& frame, EngineMetrics& metrics) override {
        if (!buffer_ || !frame || frame->format != PixelFormat::RGB24) {
            return EngineError::InvalidState;
        }
        const auto now = steady_us();
        const double queue_lat = frame->enqueued_us > 0 ? static_cast<double>(now - frame->enqueued_us) : 0.0;
        const double e2e = frame->decode_done_us > 0 ? static_cast<double>(now - frame->decode_done_us) : 0.0;
        const std::size_t n = std::min(static_cast<std::size_t>([buffer_ length]), frame->data.size());
        std::memcpy([buffer_ contents], frame->data.data(), n);
        metrics.note_copy(n, 1);
        metrics.note_present(queue_lat, e2e, false);
        // Still a CPU copy into a Metal buffer — not zero-copy.
        return EngineError::Ok;
    }
    bool claims_zero_copy() const override { return false; }

private:
    id<MTLDevice> device_ = nil;
    id<MTLBuffer> buffer_ = nil;
    int width_ = 0;
    int height_ = 0;
};

}  // namespace

std::unique_ptr<IRenderer> create_metal_renderer_impl() {
    return std::make_unique<MetalPresenter>();
}

}  // namespace kut

#endif
