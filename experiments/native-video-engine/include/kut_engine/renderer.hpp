#pragma once

#include "kut_engine/frame.hpp"
#include "kut_engine/metrics.hpp"

#include <cstdint>
#include <memory>
#include <string>

namespace kut {

/// Portable presenter interface. Metal/Vulkan/D3D backends may be added later.
class IRenderer {
public:
    virtual ~IRenderer() = default;

    virtual std::string name() const = 0;
    virtual EngineError initialize(int width, int height) = 0;
    virtual void shutdown() = 0;

    /// Present one frame. Must not block a UI thread for long; may copy.
    /// Updates metrics (present latency, copy counts).
    virtual EngineError present(const VideoFramePtr& frame, EngineMetrics& metrics) = 0;

    /// True only if the full tested path avoids a CPU intermediate copy.
    virtual bool claims_zero_copy() const { return false; }
};

/// CPU presenter: copies frame into a present buffer and records timing.
/// Used for headless benchmarks and as honest baseline (not zero-copy).
class CpuPresenter final : public IRenderer {
public:
    std::string name() const override { return "cpu_presenter"; }
    EngineError initialize(int width, int height) override;
    void shutdown() override;
    EngineError present(const VideoFramePtr& frame, EngineMetrics& metrics) override;

    const std::vector<std::uint8_t>& last_buffer() const { return present_buf_; }
    int width() const { return width_; }
    int height() const { return height_; }

private:
    int width_ = 0;
    int height_ = 0;
    std::vector<std::uint8_t> present_buf_;
};

std::unique_ptr<IRenderer> create_default_renderer();
std::unique_ptr<IRenderer> create_metal_renderer();  // may return null if unavailable

}  // namespace kut
