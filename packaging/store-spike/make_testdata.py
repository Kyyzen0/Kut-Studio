"""Spike : crée clip.mp4 (FFmpeg) et demo.kut dans un dossier (à copier dans le bundle AVANT la signature).

Le bac à sable lit librement le bundle de l'application : c'est le seul moyen, sans boîte de dialogue, de donner un projet
et un média à l'exécutable sandboxé. Usage : python make_testdata.py DOSSIER   (depuis la racine du dépôt, venv activé).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.project_io import save_project  # noqa: E402
from core.project_model import MediaAsset, Project, Track  # noqa: E402
from core.timeline_operations import add_clip_to_track  # noqa: E402


def main(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "clip.mp4"
    subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
                           "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-shortest", str(clip)])
    asset = MediaAsset("m", str(clip), "source", 2.0, 320, 180, 30.0, "video", has_audio=True)
    project = Project("Spike", width=320, height=180, fps=30.0, media_assets=[asset],
                      tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    add_clip_to_track(project, "m", "V1", 0.0)
    save_project(project, str(folder / "demo.kut"))
    print(f"{folder}/demo.kut et clip.mp4 créés")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
