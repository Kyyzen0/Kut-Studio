#include "kut_engine/decoder.hpp"
#include "kut_engine/metrics.hpp"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
#include <libavutil/imgutils.h>
#include <libavutil/opt.h>
#include <libswscale/swscale.h>
}

#include <cstring>
#include <filesystem>

namespace kut {
namespace {

PixelFormat map_pix_fmt(int fmt) {
    if (fmt == AV_PIX_FMT_YUV420P) return PixelFormat::YUV420P;
    if (fmt == AV_PIX_FMT_NV12) return PixelFormat::NV12;
    if (fmt == AV_PIX_FMT_RGB24) return PixelFormat::RGB24;
    return PixelFormat::Unknown;
}

enum AVPixelFormat get_hw_format(AVCodecContext* ctx, const enum AVPixelFormat* pix_fmts) {
    const enum AVPixelFormat* p;
    const auto target = static_cast<AVPixelFormat>(reinterpret_cast<intptr_t>(ctx->opaque));
    for (p = pix_fmts; *p != AV_PIX_FMT_NONE; p++) {
        if (*p == target) {
            return *p;
        }
    }
    return AV_PIX_FMT_NONE;
}

}  // namespace

HwProbeResult probe_hw_decode() {
    HwProbeResult r;
    AVBufferRef* ctx = nullptr;
#if defined(__APPLE__)
    if (av_hwdevice_ctx_create(&ctx, AV_HWDEVICE_TYPE_VIDEOTOOLBOX, nullptr, nullptr, 0) == 0) {
        r.videotoolbox = true;
        av_buffer_unref(&ctx);
    }
#endif
    if (av_hwdevice_ctx_create(&ctx, AV_HWDEVICE_TYPE_VAAPI, nullptr, nullptr, 0) == 0) {
        r.vaapi = true;
        av_buffer_unref(&ctx);
    }
    if (av_hwdevice_ctx_create(&ctx, AV_HWDEVICE_TYPE_CUDA, nullptr, nullptr, 0) == 0) {
        r.cuda = true;
        av_buffer_unref(&ctx);
    }
#if defined(_WIN32)
    if (av_hwdevice_ctx_create(&ctx, AV_HWDEVICE_TYPE_D3D11VA, nullptr, nullptr, 0) == 0) {
        r.d3d11va = true;
        av_buffer_unref(&ctx);
    }
#endif
    r.summary = "videotoolbox=" + std::string(r.videotoolbox ? "1" : "0") +
                " vaapi=" + std::string(r.vaapi ? "1" : "0") +
                " cuda=" + std::string(r.cuda ? "1" : "0") +
                " d3d11va=" + std::string(r.d3d11va ? "1" : "0");
    return r;
}

VideoDecoder::VideoDecoder() = default;

VideoDecoder::~VideoDecoder() { close(); }

void VideoDecoder::close() {
    if (sws_) {
        sws_freeContext(sws_);
        sws_ = nullptr;
    }
    if (sws_yuv_) {
        sws_freeContext(sws_yuv_);
        sws_yuv_ = nullptr;
    }
    if (packet_) {
        av_packet_free(&packet_);
    }
    if (frame_) {
        av_frame_free(&frame_);
    }
    if (sw_frame_) {
        av_frame_free(&sw_frame_);
    }
    if (codec_) {
        avcodec_free_context(&codec_);
    }
    if (fmt_) {
        avformat_close_input(&fmt_);
    }
    if (hw_device_ctx_) {
        av_buffer_unref(&hw_device_ctx_);
    }
    video_stream_ = -1;
    info_ = {};
    eof_ = false;
    draining_ = false;
    hw_frame_seen_ = false;
    restart_us_ = 0;
    cancel_ = false;
}

void VideoDecoder::request_cancel() { cancel_ = true; }
void VideoDecoder::clear_cancel() { cancel_ = false; }

EngineError VideoDecoder::try_hw_device(const char* type_name, std::string& backend_out) {
    const auto type = av_hwdevice_find_type_by_name(type_name);
    if (type == AV_HWDEVICE_TYPE_NONE) {
        return EngineError::HwUnavailable;
    }
    AVBufferRef* ctx = nullptr;
    if (av_hwdevice_ctx_create(&ctx, type, nullptr, nullptr, 0) < 0) {
        return EngineError::HwUnavailable;
    }
    hw_device_ctx_ = ctx;
    backend_out = type_name;
    return EngineError::Ok;
}

EngineError VideoDecoder::init_codec(DecodeMode mode) {
    const AVStream* st = fmt_->streams[video_stream_];
    const AVCodec* codec = avcodec_find_decoder(st->codecpar->codec_id);
    if (!codec) {
        return EngineError::CodecUnsupported;
    }
    codec_ = avcodec_alloc_context3(codec);
    if (!codec_) {
        return EngineError::OutOfMemory;
    }
    if (avcodec_parameters_to_context(codec_, st->codecpar) < 0) {
        return EngineError::OpenFailed;
    }

    info_.hardware_decode_active = false;
    info_.hw_backend.clear();

    if (mode == DecodeMode::HardwarePrefer) {
        // Prefer platform order: VideoToolbox → D3D11VA → VAAPI → CUDA.
        const char* candidates[] = {
#if defined(__APPLE__)
            "videotoolbox",
#endif
#if defined(_WIN32)
            "d3d11va",
#endif
            "vaapi",
            "cuda",
        };
        for (const char* name : candidates) {
            std::string backend;
            if (try_hw_device(name, backend) != EngineError::Ok) {
                continue;
            }
            codec_->hw_device_ctx = av_buffer_ref(hw_device_ctx_);
            // Common HW pix fmts; get_format callback selects if offered.
            AVPixelFormat hw_pix = AV_PIX_FMT_NONE;
            if (std::strcmp(name, "videotoolbox") == 0) hw_pix = AV_PIX_FMT_VIDEOTOOLBOX;
            else if (std::strcmp(name, "vaapi") == 0) hw_pix = AV_PIX_FMT_VAAPI;
            else if (std::strcmp(name, "cuda") == 0) hw_pix = AV_PIX_FMT_CUDA;
            else if (std::strcmp(name, "d3d11va") == 0) hw_pix = AV_PIX_FMT_D3D11;
            codec_->opaque = reinterpret_cast<void*>(static_cast<intptr_t>(hw_pix));
            codec_->get_format = get_hw_format;
            if (avcodec_open2(codec_, codec, nullptr) == 0) {
                info_.hardware_decode_active = true;
                info_.hw_backend = backend;
                break;
            }
            av_buffer_unref(&codec_->hw_device_ctx);
            av_buffer_unref(&hw_device_ctx_);
            hw_device_ctx_ = nullptr;
            // Realloc codec context for software retry.
            avcodec_free_context(&codec_);
            codec_ = avcodec_alloc_context3(codec);
            if (!codec_ || avcodec_parameters_to_context(codec_, st->codecpar) < 0) {
                return EngineError::OpenFailed;
            }
        }
    }

    if (!info_.hardware_decode_active) {
        if (avcodec_open2(codec_, codec, nullptr) < 0) {
            return EngineError::OpenFailed;
        }
    }

    info_.width = codec_->width;
    info_.height = codec_->height;
    info_.codec_name = codec->name ? codec->name : "";
    info_.format = map_pix_fmt(codec_->pix_fmt);
    if (st->avg_frame_rate.num > 0 && st->avg_frame_rate.den > 0) {
        info_.fps = av_q2d(st->avg_frame_rate);
    } else if (st->r_frame_rate.num > 0 && st->r_frame_rate.den > 0) {
        info_.fps = av_q2d(st->r_frame_rate);
    }
    if (fmt_->duration > 0) {
        info_.duration_us = static_cast<std::int64_t>(fmt_->duration);
    }
    return EngineError::Ok;
}

EngineError VideoDecoder::open(const std::string& path, DecodeMode mode, bool convert_to_rgb24) {
    close();
    convert_rgb_ = convert_to_rgb24;
    if (!std::filesystem::exists(path)) {
        return EngineError::FileNotFound;
    }
    if (avformat_open_input(&fmt_, path.c_str(), nullptr, nullptr) < 0) {
        return EngineError::OpenFailed;
    }
    if (avformat_find_stream_info(fmt_, nullptr) < 0) {
        close();
        return EngineError::OpenFailed;
    }
    video_stream_ = av_find_best_stream(fmt_, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (video_stream_ < 0) {
        close();
        return EngineError::NoVideoStream;
    }
    const auto err = init_codec(mode);
    if (err != EngineError::Ok) {
        close();
        return err;
    }
    frame_ = av_frame_alloc();
    sw_frame_ = av_frame_alloc();
    packet_ = av_packet_alloc();
    if (!frame_ || !sw_frame_ || !packet_) {
        close();
        return EngineError::OutOfMemory;
    }
    eof_ = false;
    return EngineError::Ok;
}

EngineError VideoDecoder::make_output_frame(AVFrame* src, VideoFramePtr& out) {
    AVFrame* use = src;
    std::size_t copies = 0;
    std::size_t bytes = 0;

    if (src->format == AV_PIX_FMT_VAAPI || src->format == AV_PIX_FMT_CUDA ||
        src->format == AV_PIX_FMT_D3D11 || src->format == AV_PIX_FMT_VIDEOTOOLBOX) {
        // Download HW surface to system memory (explicit copy; not zero-copy).
        if (av_hwframe_transfer_data(sw_frame_, src, 0) < 0) {
            return EngineError::DecodeFailed;
        }
        use = sw_frame_;
        hw_frame_seen_ = true;
        ++copies;
    }

    if (convert_rgb_) {
        sws_ = sws_getCachedContext(
            sws_, use->width, use->height, static_cast<AVPixelFormat>(use->format),
            use->width, use->height, AV_PIX_FMT_RGB24, SWS_BILINEAR, nullptr, nullptr, nullptr);
        if (!sws_) {
            return EngineError::DecodeFailed;
        }
        out = make_frame(use->width, use->height, PixelFormat::RGB24);
        uint8_t* dst[4] = {out->data.data(), nullptr, nullptr, nullptr};
        int dst_ls[4] = {out->linesize[0], 0, 0, 0};
        sws_scale(sws_, use->data, use->linesize, 0, use->height, dst, dst_ls);
        ++copies;
        bytes += out->data.size();
        if (use == sw_frame_) {
            bytes += static_cast<std::size_t>(use->width) * static_cast<std::size_t>(use->height) * 3 / 2;
        }
    } else {
        const int w = use->width;
        const int h = use->height;
        out = make_frame(w, h, PixelFormat::YUV420P);
        if (use->format == AV_PIX_FMT_YUV420P) {
            // Copie plan par plan, sans ligne de remplissage.
            for (int y = 0; y < h; ++y) {
                std::memcpy(out->data.data() + y * w, use->data[0] + y * use->linesize[0], w);
            }
            const int cw = w / 2;
            const int ch = h / 2;
            auto* u = out->data.data() + w * h;
            auto* v = u + cw * ch;
            for (int y = 0; y < ch; ++y) {
                std::memcpy(u + y * cw, use->data[1] + y * use->linesize[1], cw);
                std::memcpy(v + y * cw, use->data[2] + y * use->linesize[2], cw);
            }
        } else {
            // Autre format (NV12 d'un téléchargement matériel, 4:2:2, 4:4:4, 10 bits…) : swscale convertit directement
            // dans le cadre de sortie. Lire les plans à la main supposerait un 4:2:0 à trois plans (plan absent = crash).
            sws_yuv_ = sws_getCachedContext(sws_yuv_, w, h, static_cast<AVPixelFormat>(use->format), w, h,
                                            AV_PIX_FMT_YUV420P, SWS_BILINEAR, nullptr, nullptr, nullptr);
            if (!sws_yuv_) {
                return EngineError::DecodeFailed;
            }
            const int cw = w / 2;
            const int ch = h / 2;
            uint8_t* dst[4] = {out->data.data(), out->data.data() + w * h,
                               out->data.data() + w * h + cw * ch, nullptr};
            int dst_ls[4] = {w, cw, cw, 0};
            sws_scale(sws_yuv_, use->data, use->linesize, 0, h, dst, dst_ls);
        }
        ++copies;
        bytes += out->data.size();
        out->format = PixelFormat::YUV420P;
    }

    out->pts_us = 0;
    if (use->best_effort_timestamp != AV_NOPTS_VALUE) {
        const AVRational tb = fmt_->streams[video_stream_]->time_base;
        out->pts_us = av_rescale_q(use->best_effort_timestamp, tb, AVRational{1, 1000000});
    }
    out->sequence = ++sequence_;
    out->copy_count = copies;
    out->bytes_copied = bytes;
    out->decode_done_us = steady_us();
    return EngineError::Ok;
}

EngineError VideoDecoder::decode_packet(AVPacket* pkt, VideoFramePtr& out, bool& got) {
    got = false;
    if (avcodec_send_packet(codec_, pkt) < 0) {
        return hardware_failed() ? EngineError::HwUnavailable : EngineError::DecodeFailed;
    }
    return receive_frame(out, got);
}

EngineError VideoDecoder::receive_frame(VideoFramePtr& out, bool& got) {
    got = false;
    const int ret = avcodec_receive_frame(codec_, frame_);
    if (ret == AVERROR(EAGAIN)) {
        return EngineError::Ok;  // il faut davantage de paquets
    }
    if (ret == AVERROR_EOF) {
        eof_ = true;  // vidage terminé : plus aucune image
        return EngineError::Ok;
    }
    if (ret < 0) {
        // Un codec matériel qui ne négocie pas le format échoue ici, et non à l'ouverture : repli logiciel.
        return hardware_failed() ? EngineError::HwUnavailable : EngineError::DecodeFailed;
    }
    const auto err = make_output_frame(frame_, out);
    av_frame_unref(frame_);
    if (err != EngineError::Ok) {
        return err;
    }
    got = true;
    return EngineError::Ok;
}

EngineError VideoDecoder::fall_back_to_software() {
    // Le codec matériel a accepté le flux à l'ouverture sans produire d'image : on rouvre en logiciel et on repart de
    // la dernière position demandée, comme si le matériel n'avait jamais été utilisé.
    const AVStream* st = fmt_->streams[video_stream_];
    const AVCodec* codec = avcodec_find_decoder(st->codecpar->codec_id);
    if (!codec) {
        return EngineError::CodecUnsupported;
    }
    avcodec_free_context(&codec_);
    if (hw_device_ctx_) {
        av_buffer_unref(&hw_device_ctx_);
    }
    codec_ = avcodec_alloc_context3(codec);
    if (!codec_ || avcodec_parameters_to_context(codec_, st->codecpar) < 0 ||
        avcodec_open2(codec_, codec, nullptr) < 0) {
        return EngineError::OpenFailed;
    }
    info_.hardware_decode_active = false;
    info_.hw_backend.clear();
    draining_ = false;
    eof_ = false;
    const int64_t ts = av_rescale_q(restart_us_, AVRational{1, 1000000}, st->time_base);
    if (av_seek_frame(fmt_, video_stream_, ts, AVSEEK_FLAG_BACKWARD) < 0) {
        return EngineError::SeekFailed;
    }
    return EngineError::Ok;
}

EngineError VideoDecoder::read(VideoFramePtr& out) {
    out.reset();
    if (!fmt_ || !codec_) {
        return EngineError::InvalidState;
    }
    if (cancel_) {
        return EngineError::Cancelled;
    }
    if (eof_) {
        return EngineError::OpenFailed;  // treated as EOF by engine
    }

    while (true) {
        if (cancel_) {
            return EngineError::Cancelled;
        }
        bool got = false;
        EngineError err = EngineError::Ok;
        if (draining_) {
            // Fin du fichier : le codec retient encore des images (B-frames), on les rend une à une.
            err = receive_frame(out, got);
        } else if (av_read_frame(fmt_, packet_) < 0) {
            // Plus de paquets : on signale la fin au codec, puis on le vide aux tours suivants.
            if (avcodec_send_packet(codec_, nullptr) < 0) {
                return EngineError::DecodeFailed;
            }
            draining_ = true;
            continue;
        } else {
            if (packet_->stream_index != video_stream_) {
                av_packet_unref(packet_);
                continue;
            }
            err = decode_packet(packet_, out, got);
            av_packet_unref(packet_);
        }
        if (err == EngineError::HwUnavailable) {
            const auto fallback = fall_back_to_software();
            if (fallback != EngineError::Ok) {
                return fallback;
            }
            continue;
        }
        if (err != EngineError::Ok) {
            return err;
        }
        if (got) {
            return EngineError::Ok;
        }
        if (draining_) {
            eof_ = true;
            return EngineError::OpenFailed;  // EOF : vidage terminé
        }
    }
}

EngineError VideoDecoder::seek_us(std::int64_t target_us) {
    if (!fmt_ || video_stream_ < 0) {
        return EngineError::InvalidState;
    }
    restart_us_ = target_us;  // position à rétablir si le décodage matériel doit être remplacé (fall_back_to_software)
    const AVRational tb = fmt_->streams[video_stream_]->time_base;
    const int64_t ts = av_rescale_q(target_us, AVRational{1, 1000000}, tb);
    if (av_seek_frame(fmt_, video_stream_, ts, AVSEEK_FLAG_BACKWARD) < 0) {
        return EngineError::SeekFailed;
    }
    avcodec_flush_buffers(codec_);
    eof_ = false;
    draining_ = false;
    // Decode forward until PTS >= target (best-effort frame accuracy).
    for (int i = 0; i < 300; ++i) {
        if (cancel_) {
            return EngineError::Cancelled;
        }
        VideoFramePtr frame;
        const auto err = read(frame);
        if (err == EngineError::OpenFailed) {
            return EngineError::Ok;  // EOF
        }
        if (err != EngineError::Ok) {
            return err;
        }
        if (frame && frame->pts_us >= target_us) {
            // Push this frame back by storing on a one-slot stash? For simplicity,
            // seek leaves the decoder positioned; caller continues read().
            // We cannot unread; store last good by re-seeking is heavy.
            // Document: seek positions at/after target; one frame may be consumed.
            return EngineError::Ok;
        }
    }
    return EngineError::Ok;
}

}  // namespace kut
