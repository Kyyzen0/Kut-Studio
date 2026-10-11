#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace kut {

enum class DecodeMode {
    Software,
    HardwarePrefer,  // fall back to software if HW init fails
};

enum class PixelFormat {
    Unknown,
    YUV420P,
    NV12,
    RGB24,
};

enum class EngineError {
    Ok = 0,
    FileNotFound,
    OpenFailed,
    NoVideoStream,
    CodecUnsupported,
    DecodeFailed,
    SeekFailed,
    QueueFull,
    Cancelled,
    InvalidState,
    OutOfMemory,
    HwUnavailable,
};

inline const char* to_string(EngineError e) {
    switch (e) {
        case EngineError::Ok: return "ok";
        case EngineError::FileNotFound: return "file_not_found";
        case EngineError::OpenFailed: return "open_failed";
        case EngineError::NoVideoStream: return "no_video_stream";
        case EngineError::CodecUnsupported: return "codec_unsupported";
        case EngineError::DecodeFailed: return "decode_failed";
        case EngineError::SeekFailed: return "seek_failed";
        case EngineError::QueueFull: return "queue_full";
        case EngineError::Cancelled: return "cancelled";
        case EngineError::InvalidState: return "invalid_state";
        case EngineError::OutOfMemory: return "out_of_memory";
        case EngineError::HwUnavailable: return "hw_unavailable";
    }
    return "unknown";
}

struct MediaInfo {
    int width = 0;
    int height = 0;
    double fps = 0.0;
    int64_t duration_us = 0;
    std::string codec_name;
    PixelFormat format = PixelFormat::Unknown;
    bool hardware_decode_active = false;
    std::string hw_backend;  // empty, "videotoolbox", "vaapi", …
};

struct EngineConfig {
    DecodeMode decode_mode = DecodeMode::Software;
    std::size_t queue_capacity = 8;
    bool convert_to_rgb24 = true;  // for portable present / PySide host
    bool drop_when_full = true;    // backpressure: drop oldest
};

}  // namespace kut
