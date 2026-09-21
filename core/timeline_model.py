from PySide6.QtGui import QColor


TRACK_NAMES = ["V1", "V2", "S1"]
TRACK_LABELS = ["Vidéo", "Vidéo", "Sous-titres"]
MARKERS = [4.0, 9.0, 14.0]


def default_clips():
    return [
        {"track": 0, "start": 0.0, "end": 4.0, "label": "Intro", "color": QColor("#4da3ff")},
        {"track": 0, "start": 6.5, "end": 12.0, "label": "Plan A", "color": QColor("#58c4a7")},
        {"track": 1, "start": 2.0, "end": 7.5, "label": "B-roll", "color": QColor("#b070ff")},
        {
            "track": 2,
            "start": 1.0,
            "end": 5.0,
            "label": "Sous-titre 01",
            "text": "Bienvenue dans Kut-Studio",
            "color": QColor("#e6c84f"),
        },
    ]


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
