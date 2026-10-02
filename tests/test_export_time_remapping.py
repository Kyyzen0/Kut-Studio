"""Tests pour l'export FFmpeg avec remappage temporel."""

from __future__ import annotations

import pytest

from core.export_engine import (
    _build_layer_filter,
    _build_audio_filter,
    _build_time_remapping_video_filter,
    _build_time_remapping_audio_filter,
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


class TestBuildTimeRemappingVideoFilter:
    def test_normal_clip_no_filter(self):
        """Un clip normal n'a pas de filtre de time_remapping."""
        layer = _make_render_layer()
        filter_str = _build_time_remapping_video_filter(layer)
        assert filter_str == ""

    def test_speed_2x(self):
        """La vitesse vidéo passe par setpts : atempo est un filtre audio (FFmpeg refuse le graphe)."""
        layer = _make_render_layer(time_remapping=TimeRemapping(speed=2.0))
        filter_str = _build_time_remapping_video_filter(layer, fps=30)
        assert filter_str == "setpts=PTS/2,fps=30"

    def test_speed_0_5x(self):
        layer = _make_render_layer(time_remapping=TimeRemapping(speed=0.5))
        filter_str = _build_time_remapping_video_filter(layer, fps=30)
        assert filter_str == "setpts=PTS/0.5,fps=30"

    def test_speed_4x(self):
        """Une vitesse hors de [0,5 ; 2] n'a pas besoin d'être chaînée : setpts n'a pas cette limite."""
        layer = _make_render_layer(time_remapping=TimeRemapping(speed=4.0))
        filter_str = _build_time_remapping_video_filter(layer, fps=30)
        assert filter_str == "setpts=PTS/4,fps=30"

    @pytest.mark.parametrize("remapping", [
        TimeRemapping(speed=2.0), TimeRemapping(speed=0.25), TimeRemapping(speed=4.0),
        TimeRemapping(reverse=True), TimeRemapping(speed=2.0, reverse=True),
        TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=3.0),
    ])
    def test_a_video_chain_never_contains_an_audio_only_filter(self, remapping):
        """Régression : « Media type mismatch between fps and atempo » faisait échouer tout l'export."""
        layer = _make_render_layer(source_fps=30.0, time_remapping=remapping)
        filter_str = _build_time_remapping_video_filter(layer, fps=30)
        assert not any(name in filter_str for name in ("atempo", "areverse", "asetpts", "aresample"))
        assert "select=eq(n," not in filter_str         # la virgule interne cassait le filter_complex

    def test_reverse(self):
        """Un clip en reverse a un filtre reverse."""
        layer = _make_render_layer(time_remapping=TimeRemapping(reverse=True))
        filter_str = _build_time_remapping_video_filter(layer)
        assert "reverse" in filter_str

    def test_speed_and_reverse(self):
        """Reverse d'abord, puis la vitesse sur le flux déjà inversé."""
        layer = _make_render_layer(
            time_remapping=TimeRemapping(speed=2.0, reverse=True)
        )
        filter_str = _build_time_remapping_video_filter(layer, fps=30)
        assert filter_str == "reverse,setpts=PTS/2,fps=30"

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
        filter_str = _build_time_remapping_video_filter(layer)
        # Image 150 (5,0 s × 30 i/s) : on vise une demi-image avant elle, (150 − 0,5) / 30.
        assert filter_str == "trim=start=4.983333,setpts=PTS-STARTPTS,trim=end_frame=1"

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
        filter_str = _build_time_remapping_video_filter(layer)
        assert "reverse" not in filter_str
        assert "atempo" not in filter_str

    def test_reverse_too_long_raises(self):
        """Un clip en reverse trop long lève une erreur."""
        layer = _make_render_layer(
            source_out=7201.0,  # > 1 heure
            time_remapping=TimeRemapping(reverse=True),
        )
        with pytest.raises(ValueError) as exc_info:
            _build_time_remapping_video_filter(layer)
        assert "trop long" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests de _build_time_remapping_audio_filter
# ---------------------------------------------------------------------------


class TestBuildTimeRemappingAudioFilter:
    def test_normal_clip_no_filter(self):
        """Un clip normal n'a pas de filtre de time_remapping audio."""
        layer = _make_audio_layer()
        filter_str = _build_time_remapping_audio_filter(layer)
        assert filter_str == ""

    def test_speed_2x(self):
        """Un clip audio avec speed=2x a un filtre atempo=2.0."""
        layer = _make_audio_layer(time_remapping=TimeRemapping(speed=2.0))
        filter_str = _build_time_remapping_audio_filter(layer)
        assert "atempo=2.0" in filter_str

    def test_reverse(self):
        """Un clip audio en reverse a un filtre areverse."""
        layer = _make_audio_layer(time_remapping=TimeRemapping(reverse=True))
        filter_str = _build_time_remapping_audio_filter(layer)
        assert "areverse" in filter_str

    def test_freeze_frame_silence(self):
        """Un clip audio en freeze est silencieux."""
        layer = _make_audio_layer(
            time_remapping=TimeRemapping(
                freeze_mode=FreezeFrameMode.FREEZE,
                freeze_source_time=5.0,
                freeze_duration=3.0,
            ),
        )
        filter_str = _build_time_remapping_audio_filter(layer)
        assert filter_str == "volume=0"

    def test_reverse_too_long_raises(self):
        """Un clip audio en reverse trop long lève une erreur."""
        layer = _make_audio_layer(
            source_out=7201.0,  # > 1 heure
            time_remapping=TimeRemapping(reverse=True),
        )
        with pytest.raises(ValueError) as exc_info:
            _build_time_remapping_audio_filter(layer)
        assert "trop long" in str(exc_info.value)


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
        """Un clip avec speed a setpts (pas atempo) puis un retour à la cadence de sortie."""
        layer = _make_render_layer(time_remapping=TimeRemapping(speed=2.0))
        filter_str = _build_layer_filter(0, layer, 0, 1920, 1080, 30)
        assert "setpts=PTS/2,fps=30" in filter_str
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
        assert "volume=0" in filter_str
