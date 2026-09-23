from PySide6.QtGui import QColor


TRACK_NAMES = ["V1", "V2", "S1"]
TRACK_LABELS = ["Vidéo", "Vidéo", "Sous-titres"]
MARKERS = [4.0, 9.0, 14.0]


def default_clips():
    return [
        {"id": "intro", "track": 0, "start": 0.0, "end": 4.0, "label": "Intro", "source_path": "", "color": QColor("#4da3ff")},
        {"id": "plan_a", "track": 0, "start": 6.5, "end": 12.0, "label": "Plan A", "source_path": "", "color": QColor("#58c4a7")},
        {"id": "b_roll", "track": 1, "start": 2.0, "end": 7.5, "label": "B-roll", "source_path": "", "color": QColor("#b070ff")},
        {
            "id": "subtitle_01",
            "track": 2,
            "start": 1.0,
            "end": 5.0,
            "label": "Sous-titre 01",
            "source_path": "",
            "text": "Bienvenue dans Kut-Studio",
            "color": QColor("#e6c84f"),
        },
    ]


def _base_clip_name(label):
    if "_" in label:
        prefix, suffix = label.rsplit("_", 1)
        if suffix.isdigit():
            return prefix
    return label


def move_clip(clips, clip_id, new_start):
    for clip in clips:
        if clip.get("id") != clip_id:
            continue
        duration = clip["end"] - clip["start"]
        clip["start"] = max(0.0, new_start)
        clip["end"] = clip["start"] + duration
        return clips
    return clips


def trim_clip(clips, clip_id, new_start=None, new_end=None):
    for clip in clips:
        if clip.get("id") != clip_id:
            continue
        duration = clip["end"] - clip["start"]
        if new_start is not None:
            lower_bound = clip["start"]
            clip["start"] = min(max(new_start, lower_bound), clip["end"] - 0.1)
            if clip["start"] >= clip["end"]:
                clip["start"] = clip["end"] - 0.1
        if new_end is not None:
            clip["end"] = max(clip["start"] + 0.1, new_end)
        if new_start is None and new_end is None:
            return clips
        return clips
    return clips


def cut_clip(clips, clip_id, playhead_pos):
    for index, clip in enumerate(clips):
        if clip.get("id") != clip_id:
            continue
        if playhead_pos <= clip["start"] or playhead_pos >= clip["end"]:
            return clips
        base_name = _base_clip_name(clip["label"])
        left_clip = dict(clip)
        right_clip = dict(clip)
        left_clip["id"] = f"{base_name}_1"
        left_clip["label"] = f"{base_name}_1"
        left_clip["end"] = playhead_pos
        right_clip["id"] = f"{base_name}_2"
        right_clip["label"] = f"{base_name}_2"
        right_clip["start"] = playhead_pos
        right_clip["end"] = clip["end"]
        clips[index:index + 1] = [left_clip, right_clip]
        return clips
    return clips


def delete_clip(clips, clip_id):
    return [clip for clip in clips if clip.get("id") != clip_id]


def clips_on_track(clips, track):
    return [clip for clip in clips if clip["track"] == track]


def transition_gap_pixels(previous, following, pixels_per_second, zoom):
    return (following["start"] - previous["end"]) * pixels_per_second * zoom


def v1_transition_pairs(clips, pixels_per_second, zoom, max_gap_pixels=10):
    v1_clips = sorted(clips_on_track(clips, 0), key=lambda clip: clip["start"])
    return [
        (previous, following)
        for previous, following in zip(v1_clips, v1_clips[1:])
        if 0 < transition_gap_pixels(previous, following, pixels_per_second, zoom) <= max_gap_pixels
    ]
