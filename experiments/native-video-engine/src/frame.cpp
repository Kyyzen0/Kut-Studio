#include "kut_engine/frame.hpp"

namespace kut {

VideoFramePtr make_frame(int width, int height, PixelFormat format) {
    auto f = std::make_shared<VideoFrame>();
    f->width = width;
    f->height = height;
    f->format = format;
    std::size_t nbytes = 0;
    if (format == PixelFormat::RGB24) {
        nbytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3;
        f->linesize[0] = width * 3;
    } else if (format == PixelFormat::YUV420P) {
        nbytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3 / 2;
        f->linesize[0] = width;
        f->linesize[1] = width / 2;
        f->linesize[2] = width / 2;
    } else if (format == PixelFormat::NV12) {
        nbytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3 / 2;
        f->linesize[0] = width;
        f->linesize[1] = width;
    }
    f->data.resize(nbytes);
    return f;
}

}  // namespace kut
