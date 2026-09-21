from core.timeline_model import cut_clip, delete_clip, default_clips, move_clip, trim_clip


def test_cut_clip_splits_into_two_clips():
    clips = default_clips()
    original = next(clip for clip in clips if clip["label"] == "Intro")

    cut_clips = cut_clip(clips, original["id"], 2.0)

    intro_clips = [clip for clip in cut_clips if clip["label"].startswith("Intro")]
    assert len(intro_clips) == 2
    assert intro_clips[0]["label"] == "Intro_1"
    assert intro_clips[1]["label"] == "Intro_2"
    assert intro_clips[0]["end"] == 2.0
    assert intro_clips[1]["start"] == 2.0


def test_move_clip_updates_position():
    clips = default_clips()
    original = next(clip for clip in clips if clip["label"] == "Intro")

    moved = move_clip(clips, original["id"], 2.0)
    intro = next(clip for clip in moved if clip["id"] == original["id"])

    assert intro["start"] == 2.0
    assert intro["end"] == 6.0


def test_trim_clip_reduces_duration():
    clips = default_clips()
    original = next(clip for clip in clips if clip["label"] == "Plan A")

    trimmed = trim_clip(clips, original["id"], new_end=9.5)
    plan_a = next(clip for clip in trimmed if clip["id"] == original["id"])

    assert plan_a["end"] == 9.5
    assert plan_a["end"] - plan_a["start"] == 3.0


def test_delete_clip_removes_selected_clip():
    clips = default_clips()
    original = next(clip for clip in clips if clip["label"] == "Intro")

    remaining = delete_clip(clips, original["id"])

    assert all(clip["label"] != "Intro" for clip in remaining)
