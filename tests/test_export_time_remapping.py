"""Tests pour l'export FFmpeg avec remappage temporel."""

from __future__ import annotations

import re

import pytest

from core.export_engine import (
    _build_audio_filter,
    _build_freeze_video_filter,
    _build_layer_filter,
    _format_offset,
    _retime_audio_map_of,
    _retime_map_of,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import AudioLayer, RenderLayer, build_render_plan
from core.time_remapping import FreezeFrameMode, TimeRemapping


def _make_media_asset(
    id: str = "asset-1",
    duration: float = 10.0,
    fps: float = 30.0,
    has_audio: bool = True,
) -> MediaAsset:
    return MediaAsset(
        id=id,
        path=f"/tmp/{id}.mp4",
        name=id,
        duration=duration,
        width=1920,
        height=1080,
        fps=fps,
        media_type="video",
        has_audio=has_audio,
    )


def _make_clip(
    id: str = "clip-1",
    asset_id: str = "asset-1",
    timeline_start: float = 0.0,
    source_in: float = 0.0,
    source_out: float = 10.0,
    time_remapping: TimeRemapping | None = None,
) -> Clip:
    return Clip(
        id=id,
        asset_id=asset_id,
        track_id="track-v1",
        timeline_start=timeline_start,
        source_in=source_in,
        source_out=source_out,
        time_remapping=time_remapping or TimeRemapping(),
    )


def _make_render_layer(
    clip_id: str = "clip-1",
    asset_id: str = "asset-1",
    source_in: float = 0.0,
    source_out: float = 10.0,
    timeline_start: float = 0.0,
    timeline_end: float = 10.0,
    source_fps: float = 30.0,
    time_remapping: TimeRemapping | None = None,
) -> RenderLayer:
    return RenderLayer(
        clip_id=clip_id,
        asset_id=asset_id,
        track_id="track-v1",
        track_index=0,
        source_path="/tmp/asset-1.mp4",
        source_in=source_in,
        source_out=source_out,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        source_fps=source_fps,
        time_remapping=time_remapping or TimeRemapping(),
    )


def _make_audio_layer(
    clip_id: str = "clip-1",
    source_in: float = 0.0,
    source_out: float = 10.0,
    timeline_start: float = 0.0,
    timeline_end: float = 10.0,
    source_fps: float = 44100.0,
    time_remapping: TimeRemapping | None = None,
) -> AudioLayer:
    return AudioLayer(
        clip_id=clip_id,
        asset_id="asset-1",
        track_id="track-v1",
        track_index=0,
        source_path="/tmp/asset-1.mp4",
        source_in=source_in,
        source_out=source_out,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        source_fps=source_fps,
        time_remapping=time_remapping or TimeRemapping(),
    )


# ---------------------------------------------------------------------------
# Tests de build_render_plan avec time_remapping
# ---------------------------------------------------------------------------


class TestRenderPlanWithTimeRemapping:
    def test_normal_clip_duration_unchanged(self):
        """Un clip avec time_remapping normal a la même durée."""
        asset = _make_media_asset()
        clip = _make_clip(time_remapping=TimeRemapping())
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        assert len(plan.video_layers) == 1
        layer = plan.video_layers[0]
        assert layer.timeline_end - layer.timeline_start == 10.0
        assert layer.time_remapping.speed == 1.0
        assert layer.time_remapping.reverse is False

    def test_fast_clip_duration_reduced(self):
        """Un clip avec speed=2x a une durée timeline divisée par 2."""
        asset = _make_media_asset()
        clip = _make_clip(time_remapping=TimeRemapping(speed=2.0))
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        assert len(plan.video_layers) == 1
        layer = plan.video_layers[0]
        # Durée timeline = 10 / 2 = 5
        assert layer.timeline_end - layer.timeline_start == pytest.approx(5.0)
        assert layer.time_remapping.speed == 2.0

    def test_slow_clip_duration_increased(self):
        """Un clip avec speed=0.5x a une durée timeline multipliée par 2."""
        asset = _make_media_asset()
        clip = _make_clip(time_remapping=TimeRemapping(speed=0.5))
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        assert len(plan.video_layers) == 1
        layer = plan.video_layers[0]
        # Durée timeline = 10 / 0.5 = 20
        assert layer.timeline_end - layer.timeline_start == pytest.approx(20.0)

    def test_freeze_clip_duration(self):
        """Un clip en freeze a une durée timeline = freeze_duration."""
        asset = _make_media_asset()
        clip = _make_clip(
            time_remapping=TimeRemapping(
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            )
        )
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        assert len(plan.video_layers) == 1
        layer = plan.video_layers[0]
        # Durée timeline = freeze_duration = 3.0
        assert layer.timeline_end - layer.timeline_start == pytest.approx(3.0)

    def test_reverse_clip_preserves_duration(self):
        """Un clip en reverse a la même durée timeline."""
        asset = _make_media_asset()
        clip = _make_clip(time_remapping=TimeRemapping(reverse=True))
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        assert len(plan.video_layers) == 1
        layer = plan.video_layers[0]
        assert layer.timeline_end - layer.timeline_start == pytest.approx(10.0)

    def test_source_fps_stored(self):
        """Le FPS source est stocké dans le RenderLayer."""
        asset = _make_media_asset(fps=60.0)
        clip = _make_clip()
        track = Track(id="track-v1", name="V1", type="video", clips=[clip])
        project = Project(
            name="Test", width=1920, height=1080, fps=30.0,
            media_assets=[asset], tracks=[track],
        )
        plan = build_render_plan(project)
        layer = plan.video_layers[0]
        assert layer.source_fps == 60.0


# ---------------------------------------------------------------------------
# Tests de _build_time_remapping_video_filter
# ---------------------------------------------------------------------------


class TestVideoTimeStage:
    """L'étage de temps vidéo (:mod:`core.retime_graph`) : ce que la chaîne garantit, pas son texte exact."""

    def test_a_normal_clip_has_no_time_stage(self):
        assert _retime_map_of(_make_render_layer()) is None

    def test_a_frozen_clip_keeps_the_historical_freeze_chain_instead_of_a_time_stage(self):
        layer = _make_render_layer(time_remapping=TimeRemapping(
            freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=3.0))
        assert _retime_map_of(layer) is None

    @pytest.mark.parametrize("remapping", [
        TimeRemapping(speed=2.0), TimeRemapping(speed=0.25), TimeRemapping(speed=4.0),
        TimeRemapping(reverse=True), TimeRemapping(speed=2.0, reverse=True),
        TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=3.0),
    ])
    def test_a_video_chain_never_contains_an_audio_only_filter(self, remapping):
        """Régression : « Media type mismatch between fps and atempo » faisait échouer tout l'export."""
        layer = _make_render_layer(source_fps=30.0, time_remapping=remapping)
        chain = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert not any(name in chain for name in ("atempo", "areverse", "asetpts", "aresample", "aformat"))
        assert "select=eq(n," not in chain                # la virgule interne cassait le filter_complex

    @pytest.mark.parametrize("speed", [0.5, 2.0, 4.0])
    def test_a_speed_change_is_one_retimed_stream_sampled_from_the_original_timestamps(self, speed):
        """Plus de ``fps`` avant le remappage : la source n'est conformée qu'une fois, sur ses horodatages d'origine."""
        layer = _make_render_layer(source_fps=30.0, time_remapping=TimeRemapping(speed=speed))
        chain = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert "setpts='(" in chain and "fps=30:start_time=0" in chain
        assert chain.count("fps=30") == 1                  # un seul rééchantillonnage (avant : deux, d'où un décalage)
        assert "tpad=stop_mode=clone" in chain and "trim=end_frame=" in chain      # durée exacte en images

    def test_reverse_goes_through_the_reverse_filter_on_a_bounded_run(self):
        layer = _make_render_layer(source_fps=30.0, time_remapping=TimeRemapping(reverse=True))
        chain = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert ",reverse," in chain

    def test_speed_and_reverse_reverse_first_then_retime(self):
        layer = _make_render_layer(source_fps=30.0, time_remapping=TimeRemapping(speed=2.0, reverse=True))
        chain = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert chain.index(",reverse,") < chain.index("setpts='(")

    def test_freeze_frame(self):
        """Un freeze ne garde qu'une image : celle qui contient l'instant, comptée depuis source_in."""
        layer = _make_render_layer(
            source_fps=30.0,
            time_remapping=TimeRemapping(
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            ),
        )
        # Image 150 (5,0 s × 30 i/s) : on vise une demi-image avant elle, (150 − 0,5) / 30.
        assert _build_freeze_video_filter(layer) == "trim=start=4.983333,setpts=PTS-STARTPTS,trim=end_frame=1"

    def test_freeze_no_reverse_no_speed(self):
        """Un clip en freeze n'a ni reverse ni speed."""
        layer = _make_render_layer(
            time_remapping=TimeRemapping(
                speed=2.0,
                reverse=True,
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            ),
        )
        chain = _build_freeze_video_filter(layer)
        assert "reverse" not in chain and "atempo" not in chain

    def test_a_reverse_that_would_not_fit_in_memory_is_refused_with_the_cause(self):
        """``reverse`` garde toutes ses images décodées : refusé avec la quantité de mémoire, jamais tenté."""
        layer = _make_render_layer(
            source_out=7201.0,  # > 1 heure
            time_remapping=TimeRemapping(reverse=True),
        )
        with pytest.raises(ValueError, match="Gio"):
            _build_layer_filter(0, layer, 0, 1920, 1080, 30)


# ---------------------------------------------------------------------------
# Étage audio
# ---------------------------------------------------------------------------


class TestAudioTimeStage:
    def test_a_normal_clip_has_no_audio_time_stage(self):
        assert _retime_audio_map_of(_make_audio_layer()) is None

    def test_speed_keeps_the_pitch_with_atempo_by_default(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0))
        chain = _build_audio_filter(0, layer, 0, 100.0)
        assert "atempo=2.0" in chain and "asetrate" not in chain

    def test_a_speed_beyond_the_atempo_range_is_chained_in_valid_stages(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=8.0))
        chain = _build_audio_filter(0, layer, 0, 100.0)
        stages = [float(value) for value in re.findall(r"atempo=([0-9.]+)", chain)]
        assert len(stages) >= 3 and all(0.5 <= value <= 2.0 for value in stages)

    def test_the_pitch_can_follow_the_speed_like_a_tape(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0, preserve_pitch=False))
        chain = _build_audio_filter(0, layer, 0, 100.0)
        assert "asetrate=96000" in chain and "atempo" not in chain

    def test_reverse_uses_areverse_on_the_bounded_run(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(reverse=True))
        assert "areverse" in _build_audio_filter(0, layer, 0, 100.0)

    def test_a_freeze_is_a_silence_of_exactly_the_clip_duration(self):
        """L'ancien filtre coupait le son sur toute la durée de la *source* (``volume=0``), pas celle de l'arrêt sur image."""
        layer = _make_audio_layer(time_remapping=TimeRemapping(
            freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=3.0))
        chain = _build_audio_filter(0, layer, 0, 100.0)
        assert "anullsrc" in chain and "apad=whole_dur=3,atrim=end=3" in chain and "volume=0" not in chain

    def test_the_sound_can_keep_its_own_time_while_only_the_picture_is_remapped(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0, remap_audio=False))
        chain = _build_audio_filter(0, layer, 0, 100.0)
        assert "atempo" not in chain and "areverse" not in chain and "atrim=start=" in chain

    def test_the_remapped_sound_ends_exactly_at_the_clip_duration(self):
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0))
        duration = _retime_audio_map_of(layer).duration                    # la durée est celle du mapping : 10 s de source à 2×
        assert duration == 5.0
        assert f"apad=whole_dur={duration:g},atrim=end={duration:g}" in _build_audio_filter(0, layer, 0, 100.0)


# ---------------------------------------------------------------------------
# Tests d'intégration avec _build_layer_filter
# ---------------------------------------------------------------------------


class TestBuildLayerFilterIntegration:
    def test_normal_clip_filter_chain(self):
        """Un clip normal a la chaîne de filtres complète."""
        layer = _make_render_layer(source_fps=30.0)
        filter_str = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert "[0:v]" in filter_str
        assert "trim" in filter_str
        assert "setpts=PTS-STARTPTS" in filter_str
        assert "scale" in filter_str
        assert "pad" in filter_str
        assert "fps" in filter_str
        assert "[v0]" in filter_str

    def test_speed_clip_filter_chain(self):
        """Un clip avec speed a un setpts exact (pas atempo) puis une cadence de sortie à durée exacte."""
        layer = _make_render_layer(source_fps=30.0, time_remapping=TimeRemapping(speed=2.0))
        filter_str = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert "setpts='(" in filter_str and "fps=30:start_time=0" in filter_str
        assert "atempo" not in filter_str

    def test_freeze_clip_filter_chain(self):
        """Un clip en freeze garde une image (trim), la tient (tpad) et reste valide dans un filter_complex."""
        layer = _make_render_layer(
            source_fps=30.0,
            time_remapping=TimeRemapping(
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            ),
        )
        filter_str = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert "trim=start=4.983333,setpts=PTS-STARTPTS,trim=end_frame=1" in filter_str
        assert "tpad=stop_mode=clone:stop_duration=3.000000" in filter_str
        assert "trim=duration=3.000000" in filter_str
        assert "select=eq" not in filter_str


# ---------------------------------------------------------------------------
# Tests d'intégration avec _build_audio_filter
# ---------------------------------------------------------------------------


class TestBuildAudioFilterIntegration:
    def test_normal_audio_filter_chain(self):
        """Un clip audio normal a la chaîne de filtres complète."""
        layer = _make_audio_layer()
        filter_str = _build_audio_filter(0, layer, 0, 100.0)
        assert "[0:a]" in filter_str
        assert "atrim" in filter_str
        assert "asetpts=PTS-STARTPTS" in filter_str
        assert "[a0]" in filter_str

    def test_speed_audio_filter_chain(self):
        """Un clip audio avec speed a le filtre atempo inséré."""
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0))
        filter_str = _build_audio_filter(0, layer, 0, 100.0)
        assert "atempo=2.0" in filter_str

    def test_freeze_audio_filter_chain(self):
        """Un clip audio en freeze est silencieux."""
        layer = _make_audio_layer(
            time_remapping=TimeRemapping(
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            ),
        )
        filter_str = _build_audio_filter(0, layer, 0, 100.0)
        assert "anullsrc" in filter_str and "volume=0" not in filter_str


class TestLayerOffset:
    """``setpts`` tronque : le décalage d'une couche ne doit jamais retomber sous son tick (base de temps ``1/cadence``)."""

    @staticmethod
    def evaluate(value: float, time_base: float) -> int:
        """Ce que calcule FFmpeg pour ``setpts=PTS+<terme>`` (``PTS`` = 0) : division par ``TB`` puis troncature."""
        seconds, guard = re.fullmatch(r"([0-9.]+)/TB\+([0-9.e-]+)", _format_offset(value)).groups()
        return int(float(seconds) / time_base + float(guard))

    def test_an_exact_offset_keeps_its_short_form_and_gains_the_guard(self):
        assert _format_offset(2.5) == "2.5/TB+0.001"
        assert _format_offset(0.0) == "0.0/TB+0.001"

    @pytest.mark.parametrize("rate", [24.0, 25.0, 30.0, 60.0, 30000 / 1001, 24000 / 1001])
    def test_every_frame_boundary_truncates_to_its_own_tick(self, rate):
        """31/30 s s'écrivait ``1.033333`` (30,99999 ticks) ; ``1.16`` s à 25 i/s, divisé par ``TB``, donne 28,999999999999996."""
        for tick in range(0, 4000):
            assert self.evaluate(tick / rate, 1.0 / rate) == tick, tick

    def test_an_offset_between_two_frames_never_jumps_to_the_next_one(self):
        for tick in range(0, 400):
            assert self.evaluate((tick + 0.4) / 30.0, 1.0 / 30.0) == tick
