#pragma once

#include "kut_engine/frame.hpp"
#include "kut_engine/types.hpp"

#include <atomic>
#include <memory>
#include <string>

struct AVFormatContext;
struct AVCodecContext;
struct AVFrame;
struct AVPacket;
struct SwsContext;
struct AVBufferRef;

namespace kut {

struct HwProbeResult {
    bool videotoolbox = false;
    bool vaapi = false;
    bool cuda = false;
    bool d3d11va = false;
    std::string summary;
};

HwProbeResult probe_hw_decode();

/// FFmpeg-based video decoder (software, optional HW prefer with download).
class VideoDecoder {
public:
    VideoDecoder();
    ~VideoDecoder();

    VideoDecoder(const VideoDecoder&) = delete;
    VideoDecoder& operator=(const VideoDecoder&) = delete;

    EngineError open(const std::string& path, DecodeMode mode, bool convert_to_rgb24);
    void close();

    const MediaInfo& info() const { return info_; }
    bool is_open() const { return fmt_ != nullptr; }

    /// Decode next frame into `out`. Returns Ok, Cancelled, or error / end (OpenFailed used as EOF sentinel via eof_).
    EngineError read(VideoFramePtr& out);

    bool eof() const { return eof_; }

    /// Seek to timestamp in microseconds (video stream time base). Best-effort keyframe seek + decode until PTS.
    EngineError seek_us(std::int64_t target_us);

    void request_cancel();
    void clear_cancel();

private:
    EngineError init_codec(DecodeMode mode);
    EngineError try_hw_device(const char* type_name, std::string& backend_out);
    EngineError decode_packet(AVPacket* pkt, VideoFramePtr& out, bool& got);
    EngineError make_output_frame(AVFrame* src, VideoFramePtr& out);

    AVFormatContext* fmt_ = nullptr;
    AVCodecContext* codec_ = nullptr;
    AVFrame* frame_ = nullptr;
    AVFrame* sw_frame_ = nullptr;
    AVPacket* packet_ = nullptr;
    SwsContext* sws_ = nullptr;
    AVBufferRef* hw_device_ctx_ = nullptr;
    int video_stream_ = -1;
    MediaInfo info_{};
    bool convert_rgb_ = true;
    bool eof_ = false;
    std::atomic<bool> cancel_{false};
    std::uint64_t sequence_ = 0;
};

}  // namespace kut
