"""Parcours « smoke » : un montage de 10 s (deux plans, un titre, un son) exporté en CPU, vérifié par ``ffprobe``.

C'est le smoke test de la CI sans GPU (ADR-0011, porte « Linux ») : il tient en quelques secondes et ne dépend que de
FFmpeg. Pas d'échelle (toujours 10 s), pas de libass, pas de whisper.
"""

from __future__ import annotations

from typing import Any

from ..media import MediaItem
from . import Capabilities, ExportSpec, Journey

FPS = 25.0
LENGTH = 10.0


def media(scale: str) -> list[MediaItem]:
    return [
        MediaItem("a", "media/a.mp4", "video", {"width": 1280, "height": 720, "fps": 25, "duration": 5.0, "seed": 61,
                                                "audio": {"kind": "ambient", "seed": 61, "duration": 5.0}}),
        MediaItem("b", "media/b.mp4", "video", {"width": 1280, "height": 720, "fps": 25, "duration": 5.0, "seed": 62,
                                                "audio": {"kind": "ambient", "seed": 62, "duration": 5.0}}),
        MediaItem("music", "media/musique.wav", "wav", {"signal": "music", "seed": 3, "bpm": 120.0, "duration": LENGTH}),
    ]


def script(scale: str, caps: Capabilities) -> dict[str, Any]:
    return {"kut_script": 1, "description": "Recette : smoke 10 s", "ops": [
        {"op": "import_media", "path": "media/a.mp4", "as": "a"},
        {"op": "import_media", "path": "media/b.mp4", "as": "b"},
        {"op": "import_media", "path": "media/musique.wav", "as": "music"},
        {"op": "add_clip", "track": "V1", "asset": {"ref": "a"}, "at": 0.0, "id": "c_a"},
        {"op": "add_clip", "track": "V1", "asset": {"ref": "b"}, "at": 5.0, "id": "c_b"},
        {"op": "add_clip", "track": "A1", "asset": {"ref": "music"}, "at": 0.0, "id": "c_music"},
        {"op": "add_graphic", "graphic": "text", "at": 1.0, "duration": 6.0, "text": "Recette Kut-Studio", "as": "titre"},
        {"op": "set_graphic", "clip": {"ref": "titre"}, "font_size": 96, "fill_color": "#FFFFFF"},
    ]}


def exports(scale: str, caps: Capabilities) -> list[ExportSpec]:
    return [ExportSpec("1080p", "smoke-1080p.mp4", "h264_1080p", None, (1920, 1080))]


JOURNEY = Journey(
    name="smoke", title="Smoke 10 s", base=(1280, 720, FPS), tracks=(("V1", "video"), ("A1", "audio")),
    media=media, script=script, exports=exports,
    description="Deux plans, un titre, un son : démarrage et export CPU de 10 s vérifié par ffprobe (CI).",
)
