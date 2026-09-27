"""La timeline ne monte pas un widget par clip quand le montage est long."""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.project_factory import create_default_project
from ui.timeline_panel import TimelinePanel


def _long_project(count: int) -> Project:
    asset = MediaAsset(
        id="media",
        path="/tmp/long.mp4",
        name="Long",
        duration=100_000.0,
        width=1280,
        height=720,
        fps=25.0,
        media_type="video",
    )
    clips = [
        Clip(
            id=f"clip-{index}",
            asset_id="media",
            track_id="V1",
            timeline_start=index * 3.0,
            source_in=0.0,
            source_out=1.0,
            label=f"Plan {index}",
        )
        for index in range(count)
    ]
    return Project(
        name="Long",
        media_assets=[asset],
        tracks=[Track(id="V1", name="V1", type="video", clips=clips)],
    )


def test_selecting_a_clip_does_not_rebuild_track_headers(qtbot):
    panel = TimelinePanel(create_default_project())
    qtbot.addWidget(panel)
    panel.resize(1200, 480)
    panel.show()
    header = panel.track_header_widgets["V1"]
    panel.select_clip("intro")
    assert panel.track_header_widgets["V1"] is header
    assert panel.selected_clip_id == "intro"


def test_a_long_timeline_mounts_only_the_visible_clips(qtbot):
    panel = TimelinePanel(_long_project(400))
    qtbot.addWidget(panel)
    panel.resize(900, 420)
    panel.show()
    qtbot.waitExposed(panel)
    panel.set_timeline_duration(400 * 3.0)
    panel._sync_mounted_clips(refresh_views=True)
    panel._layout_children()

    assert len(panel.clip_views) == 400
    assert 0 < panel.mounted_clip_count < 80

    panel.scroll.horizontalScrollBar().setValue(panel.scroll.horizontalScrollBar().maximum())
    qtbot.wait(20)
    assert panel.mounted_clip_count < 80
    last = panel.clip_views[-1]
    assert last.id in panel.clip_widgets
