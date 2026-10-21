"""Préparation des images intermédiaires, sans FFmpeg : fenêtrage, clé de flux, rapport, repli de codec, fenêtre de décodage."""

from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest

import core.retime_prepare as prepare_module
from core.optical_flow import Fallback
from core.retime_graph import RetimeError
from core.retime_prepare import (
    PrepareError,
    PrepareReport,
    PrepareRequest,
    _FrameWindow,
    needs_preparation,
    stream_key,
    windowed_runs,
)
from core.time_map import ConstantTimeMap
from core.time_remapping import FlowQuality, TimeInterpolation

BLENDING, FLOW, SAMPLING = TimeInterpolation.BLENDING, TimeInterpolation.OPTICAL_FLOW, TimeInterpolation.SAMPLING


def request_for(time_map=None, *, mode=BLENDING, window=None, path="/tmp/does-not-matter.mp4", **overrides) -> PrepareRequest:
    values = dict(
        media_path=path, time_map=time_map or ConstantTimeMap(0.0, 4.0, 0.5), interpolation=mode, quality=FlowQuality.BALANCED,
        fps=30.0, source_fps=30.0, source_frames=120, width=320, height=180, conform="scale=320:180", window=window,
    )
    values.update(overrides)
    return PrepareRequest(**values)


# ---------------------------------------------------------------------------
# Ce qui demande une préparation
# ---------------------------------------------------------------------------


def test_only_a_clip_with_an_intermediate_image_needs_preparation():
    assert needs_preparation(request_for(mode=BLENDING)) and needs_preparation(request_for(mode=FLOW))
    assert not needs_preparation(request_for(mode=SAMPLING))
    assert not needs_preparation(request_for(ConstantTimeMap(0.0, 4.0, 2.0), mode=FLOW))          # 200 % : tout est exact
    assert not needs_preparation(request_for(ConstantTimeMap(0.0, 4.0, 1.0), mode=BLENDING))


# ---------------------------------------------------------------------------
# Fenêtre
# ---------------------------------------------------------------------------


def test_without_a_window_the_whole_run_is_prepared():
    request = request_for()
    (item,) = windowed_runs(request, request.plan())
    assert (item.lead, item.trail) == (0, 0) and len(item.samples) == request.plan().ticks and not item.backward


def test_a_window_keeps_only_its_ticks_and_pads_the_rest():
    request = request_for(window=(40, 70))
    (item,) = windowed_runs(request, request.plan())
    assert len(item.samples) == 30 and item.lead == 40 and item.trail == request.plan().ticks - 70
    assert item.samples == request.plan().runs[0].samples[40:70]


def test_a_window_that_overshoots_the_run_is_clamped():
    request = request_for(window=(-10, 10_000))
    (item,) = windowed_runs(request, request.plan())
    assert item.lead == 0 and item.trail == 0 and len(item.samples) == request.plan().ticks


def test_a_run_the_window_does_not_reach_keeps_one_exact_frame_held_for_its_whole_length():
    request = request_for(window=(10_000, 10_100))
    (item,) = windowed_runs(request, request.plan())
    assert len(item.samples) == 1 and item.samples[0].exact and item.lead == 0 and item.trail == request.plan().ticks - 1


def test_a_backward_run_is_flagged():
    request = request_for(ConstantTimeMap(0.0, 4.0, 0.5, reverse=True))
    (item,) = windowed_runs(request, request.plan())
    assert item.backward


# ---------------------------------------------------------------------------
# Clé d'un flux préparé
# ---------------------------------------------------------------------------


def key_of(request, identity=()) -> str:
    return stream_key(request, request.plan(), identity)


def test_the_stream_key_is_stable_and_follows_everything_that_changes_the_images(tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"0" * 100)
    base = request_for(path=str(media))
    assert key_of(base) == key_of(request_for(path=str(media)))
    assert key_of(replace(base, width=640)) != key_of(base)                       # une autre grille
    assert key_of(replace(base, conform="scale=320:180,pad=1:1")) != key_of(base)  # un autre cadrage
    assert key_of(replace(base, interpolation=FLOW)) != key_of(base)               # un autre mode
    assert key_of(replace(base, window=(0, 30))) != key_of(base)                   # une fenêtre
    assert key_of(request_for(ConstantTimeMap(0.0, 4.0, 0.4), path=str(media))) != key_of(base)   # d'autres images à fabriquer
    before = key_of(base)
    media.write_bytes(b"1" * 101)
    assert key_of(request_for(path=str(media))) != before                          # un média modifié


def test_the_engine_enters_the_key_only_for_the_optical_flow(tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"0" * 100)
    blending, flow = request_for(path=str(media)), request_for(path=str(media), mode=FLOW)
    assert key_of(blending, ("numpy", 1)) == key_of(blending, ("numpy", 2))        # un mélange ne dépend pas du moteur de flux
    assert key_of(flow, ("numpy", 1)) != key_of(flow, ("numpy", 2))


def test_the_quality_does_not_change_the_blending_key_but_it_is_part_of_the_flow_identity(tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"0" * 100)
    blending = request_for(path=str(media))
    assert key_of(replace(blending, quality=FlowQuality.BEST)) == key_of(blending)


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------


def test_the_report_round_trips_and_summarizes_honestly():
    report = PrepareReport(backend="numpy", images=100, pairs_computed=7, pairs_cached=3, seconds=2.5)
    report.add(Fallback.NONE, 0.9)
    report.add(Fallback.NONE, 0.7)
    report.add(Fallback.SCENE_CUT, None)                    # une coupure : rien à mesurer
    report.add(Fallback.LOW_CONFIDENCE, 0.2)                # un repli : son score bas compte
    assert report.synthesized == 4 and report.degraded == 2 and report.mean_confidence == pytest.approx((0.9 + 0.7 + 0.2) / 3)
    restored = PrepareReport.from_dict(report.to_dict())
    assert restored.to_dict() == report.to_dict()
    text = report.summary()
    assert "4/100" in text and "7 paires calculées" in text and "3 relues" in text and "2 en repli" in text


def test_a_garbled_report_file_gives_an_empty_report_not_an_error():
    report = PrepareReport.from_dict({"images": "beaucoup", "fallbacks": "x", "seconds": None, "confidence_sum": True})
    assert report.images == 0 and report.fallbacks == {} and report.seconds == 0.0 and report.mean_confidence == 1.0


def test_a_report_where_every_frame_fell_back_is_not_reported_as_fully_confident():
    """Régression : toutes les images en repli donnaient « confiance 1,0 » parce que les replis ne comptaient pas."""
    report = PrepareReport()
    for score in (0.2, 0.25, 0.3):
        report.add(Fallback.LOW_CONFIDENCE, score)
    assert report.mean_confidence == pytest.approx(0.25) and report.degraded == 3


def test_nothing_measurable_is_not_a_confidence_claim_against_the_user():
    assert PrepareReport().mean_confidence == 1.0 and PrepareReport().degraded == 0
    only_cuts = PrepareReport()
    only_cuts.add(Fallback.SCENE_CUT, None)
    assert only_cuts.degraded == 1                                # la dégradation est dite par ``degraded``, pas cachée


# ---------------------------------------------------------------------------
# Codec sans perte
# ---------------------------------------------------------------------------


def fake_listing(monkeypatch, names):
    prepare_module._lossless_codec.cache_clear()
    text = "Encoders:\n V..... = Video\n ------\n" + "".join(f" V....D {name:<18} description\n" for name in names)
    monkeypatch.setattr(prepare_module, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        prepare_module, "supervised_run", lambda command, **options: subprocess.CompletedProcess(command, 0, stdout=text, stderr="")
    )


def test_the_preferred_lossless_codec_is_used_when_available(monkeypatch):
    fake_listing(monkeypatch, ["libx264", "utvideo", "ffv1"])
    assert prepare_module._lossless_codec() == ("utvideo", "gbrp")
    prepare_module._lossless_codec.cache_clear()


def test_ffv1_is_the_fallback_and_nothing_at_all_is_a_clear_error(monkeypatch):
    fake_listing(monkeypatch, ["libx264", "ffv1"])
    assert prepare_module._lossless_codec() == ("ffv1", "bgr0")
    fake_listing(monkeypatch, ["libx264", "mpeg4"])
    with pytest.raises(PrepareError, match="codec sans perte"):
        prepare_module._lossless_codec()
    prepare_module._lossless_codec.cache_clear()


def test_a_prepare_error_is_a_retime_error():
    assert issubclass(PrepareError, RetimeError) and issubclass(RetimeError, ValueError)


# ---------------------------------------------------------------------------
# Fenêtre de décodage
# ---------------------------------------------------------------------------


def numbered(first, last):
    for index in range(first, last + 1):
        yield index, f"frame-{index}"


def test_the_decoding_window_streams_forward_and_forgets_old_frames():
    window = _FrameWindow(numbered(10, 30))
    assert window.get(10) == "frame-10" and window.get(11) == "frame-11" and window.get(11) == "frame-11"
    assert window.get(20) == "frame-20"
    with pytest.raises(PrepareError, match="a déjà quitté la fenêtre"):
        window.get(10)


def test_asking_for_a_frame_the_decoder_never_produced_is_a_clear_error():
    window = _FrameWindow(numbered(0, 3))
    assert window.get(3) == "frame-3"
    with pytest.raises(PrepareError, match="n'a pas été décodée"):
        window.get(9)


def test_closing_the_window_stops_the_decoder():
    state = {"closed": False}

    def generator():
        try:
            yield 0, "a"
            yield 1, "b"
        finally:
            state["closed"] = True

    window = _FrameWindow(generator())
    window.get(0)
    window.close()
    assert state["closed"]


# ---------------------------------------------------------------------------
# Média transparent : refusé, jamais aplati en silence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pixel_format", ["yuva420p", "rgba", "gbrap", "bgra", "yuva444p10le", "ya8"])
def test_a_media_with_an_alpha_channel_is_detected(monkeypatch, pixel_format):
    from core.decode_policy import StreamInfo, default_context

    monkeypatch.setattr(default_context().probe, "get", lambda path: StreamInfo("prores", pixel_format, 64, 36))
    assert prepare_module._has_alpha("/x.mov")


@pytest.mark.parametrize("pixel_format", ["yuv420p", "yuv422p10le", "rgb24", "gbrp", "nv12", ""])
def test_an_opaque_media_is_not_taken_for_a_transparent_one(monkeypatch, pixel_format):
    from core.decode_policy import StreamInfo, default_context

    monkeypatch.setattr(default_context().probe, "get", lambda path: StreamInfo("h264", pixel_format, 64, 36))
    assert not prepare_module._has_alpha("/x.mp4")


def test_a_probe_that_fails_does_not_block_the_preparation(monkeypatch):
    from core.decode_policy import default_context

    def broken(path):
        raise OSError("ffprobe absent")

    monkeypatch.setattr(default_context().probe, "get", broken)
    assert not prepare_module._has_alpha("/x.mp4")


def test_preparing_or_analyzing_a_transparent_media_is_refused_with_the_way_out(monkeypatch, tmp_path):
    from core.flow_cache import FlowCache

    media = tmp_path / "overlay.mov"
    media.write_bytes(b"0" * 10)
    monkeypatch.setattr(prepare_module, "_has_alpha", lambda path: True)
    for function in (prepare_module.prepare, prepare_module.analyze_pairs):
        with pytest.raises(PrepareError, match="canal alpha.*Échantillonnage"):
            function(request_for(mode=FLOW, path=str(media)), FlowCache(tmp_path / "flow"))


def test_only_containers_with_an_exact_seek_are_decoded_from_the_middle():
    from core.retime_prepare import seeks_exactly

    for path in ("a.mp4", "A.MOV", "b.mkv", "c.webm", "d.m4v"):
        assert seeks_exactly(path), path
    for path in ("a.ts", "a.mts", "a.M2TS", "a.mpg", "a.avi", "a.mxf", "a.flv", "no_extension"):
        assert not seeks_exactly(path), path                                              # inconnu : depuis le début, donc exact
