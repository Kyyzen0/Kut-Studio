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
    EngineError receive_frame(VideoFramePtr& out, bool& got);
    EngineError make_output_frame(AVFrame* src, VideoFramePtr& out);
    /// Rouvre le décodage en logiciel à la dernière position connue (repli après un échec matériel).
    EngineError fall_back_to_software();
    /// Échec de décodage avant la première image matérielle : le matériel ne sait pas traiter ce flux.
    bool hardware_failed() const { return info_.hardware_decode_active && !hw_frame_seen_; }

    AVFormatContext* fmt_ = nullptr;
    AVCodecContext* codec_ = nullptr;
    AVFrame* frame_ = nullptr;
    AVFrame* sw_frame_ = nullptr;
    AVPacket* packet_ = nullptr;
    SwsContext* sws_ = nullptr;
    SwsContext* sws_yuv_ = nullptr;   // conversion vers YUV420P quand la sortie n'est pas convertie en RGB
    AVBufferRef* hw_device_ctx_ = nullptr;
    int video_stream_ = -1;
    MediaInfo info_{};
    bool convert_rgb_ = true;
    bool eof_ = false;
    bool draining_ = false;        // fin du fichier lue : on vide les images que le codec retient encore
    bool hw_frame_seen_ = false;   // une image matérielle a été produite : un échec ensuite n'est plus un repli
    std::int64_t restart_us_ = 0;  // dernière position demandée, à rétablir après un repli
    std::atomic<bool> cancel_{false};
    std::uint64_t sequence_ = 0;
};

}  // namespace kut
