#include "kut_engine/decoder.hpp"
#include "kut_engine/engine.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstring>

namespace py = pybind11;

PYBIND11_MODULE(kut_engine_native, m) {
    m.doc() = "Kut-Studio experimental native preview engine (POC v0.1)";

    py::enum_<kut::DecodeMode>(m, "DecodeMode")
        .value("Software", kut::DecodeMode::Software)
        .value("HardwarePrefer", kut::DecodeMode::HardwarePrefer);

    py::enum_<kut::PlayState>(m, "PlayState")
        .value("Stopped", kut::PlayState::Stopped)
        .value("Playing", kut::PlayState::Playing)
        .value("Paused", kut::PlayState::Paused);

    py::enum_<kut::EngineError>(m, "EngineError")
        .value("Ok", kut::EngineError::Ok)
        .value("FileNotFound", kut::EngineError::FileNotFound)
        .value("OpenFailed", kut::EngineError::OpenFailed)
        .value("NoVideoStream", kut::EngineError::NoVideoStream)
        .value("CodecUnsupported", kut::EngineError::CodecUnsupported)
        .value("DecodeFailed", kut::EngineError::DecodeFailed)
        .value("SeekFailed", kut::EngineError::SeekFailed)
        .value("QueueFull", kut::EngineError::QueueFull)
        .value("Cancelled", kut::EngineError::Cancelled)
        .value("InvalidState", kut::EngineError::InvalidState)
        .value("OutOfMemory", kut::EngineError::OutOfMemory)
        .value("HwUnavailable", kut::EngineError::HwUnavailable);

    py::class_<kut::MediaInfo>(m, "MediaInfo")
        .def_readonly("width", &kut::MediaInfo::width)
        .def_readonly("height", &kut::MediaInfo::height)
        .def_readonly("fps", &kut::MediaInfo::fps)
        .def_readonly("duration_us", &kut::MediaInfo::duration_us)
        .def_readonly("codec_name", &kut::MediaInfo::codec_name)
        .def_readonly("hardware_decode_active", &kut::MediaInfo::hardware_decode_active)
        .def_readonly("hw_backend", &kut::MediaInfo::hw_backend);

    py::class_<kut::Percentiles>(m, "Percentiles")
        .def_readonly("p50", &kut::Percentiles::p50)
        .def_readonly("p95", &kut::Percentiles::p95)
        .def_readonly("p99", &kut::Percentiles::p99);

    py::class_<kut::EngineMetrics>(m, "EngineMetrics")
        .def_readonly("frames_decoded", &kut::EngineMetrics::frames_decoded)
        .def_readonly("frames_presented", &kut::EngineMetrics::frames_presented)
        .def_readonly("frames_dropped_queue", &kut::EngineMetrics::frames_dropped_queue)
        .def_readonly("frames_late", &kut::EngineMetrics::frames_late)
        .def_readonly("seek_count", &kut::EngineMetrics::seek_count)
        .def_readonly("bytes_copied", &kut::EngineMetrics::bytes_copied)
        .def_readonly("copy_ops", &kut::EngineMetrics::copy_ops)
        .def_readonly("displayed_fps", &kut::EngineMetrics::displayed_fps)
        .def_readonly("hardware_decode", &kut::EngineMetrics::hardware_decode)
        .def_readonly("hw_backend", &kut::EngineMetrics::hw_backend)
        .def("decode_pct", &kut::EngineMetrics::decode_pct)
        .def("present_pct", &kut::EngineMetrics::present_pct)
        .def("e2e_pct", &kut::EngineMetrics::e2e_pct)
        .def("ui_stall_pct", &kut::EngineMetrics::ui_stall_pct)
        .def("note_ui_stall", &kut::EngineMetrics::note_ui_stall);

    py::class_<kut::EngineConfig>(m, "EngineConfig")
        .def(py::init<>())
        .def_readwrite("decode_mode", &kut::EngineConfig::decode_mode)
        .def_readwrite("queue_capacity", &kut::EngineConfig::queue_capacity)
        .def_readwrite("convert_to_rgb24", &kut::EngineConfig::convert_to_rgb24)
        .def_readwrite("drop_when_full", &kut::EngineConfig::drop_when_full);

    py::class_<kut::PreviewEngine>(m, "PreviewEngine")
        .def(py::init<kut::EngineConfig>(), py::arg("config") = kut::EngineConfig{})
        .def("open", &kut::PreviewEngine::open)
        .def("close", &kut::PreviewEngine::close)
        .def("play", &kut::PreviewEngine::play)
        .def("pause", &kut::PreviewEngine::pause)
        .def("stop", &kut::PreviewEngine::stop)
        .def("seek_us", &kut::PreviewEngine::seek_us)
        .def("pump_present", &kut::PreviewEngine::pump_present)
        .def("run_present_for", &kut::PreviewEngine::run_present_for,
             py::arg("duration_ms"), py::arg("realtime") = true)
        .def("state", &kut::PreviewEngine::state)
        .def("info", &kut::PreviewEngine::info, py::return_value_policy::reference_internal)
        .def("metrics", py::overload_cast<>(&kut::PreviewEngine::metrics),
             py::return_value_policy::reference_internal)
        .def("last_rgb_bytes", [](kut::PreviewEngine& self) -> py::object {
            std::vector<std::uint8_t> rgb;
            int w = 0, h = 0;
            if (!self.copy_last_rgb(rgb, w, h)) {
                return py::none();
            }
            // Return (bytes, w, h). This is an extra copy into Python bytes — measured/honest.
            return py::make_tuple(py::bytes(reinterpret_cast<const char*>(rgb.data()), rgb.size()), w, h);
        });

    m.def("probe_hw_decode", []() {
        auto r = kut::probe_hw_decode();
        py::dict d;
        d["videotoolbox"] = r.videotoolbox;
        d["vaapi"] = r.vaapi;
        d["cuda"] = r.cuda;
        d["d3d11va"] = r.d3d11va;
        d["summary"] = r.summary;
        return d;
    });

    m.def("error_name", [](kut::EngineError e) { return kut::to_string(e); });
}
