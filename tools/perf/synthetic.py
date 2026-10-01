"""Projets synthétiques reproductibles pour les benchmarks et les tests de perf.

Aucun média réel n'est nécessaire : les chemins pointent vers des fichiers
inexistants (ou vers un petit fichier factice quand le scénario doit
traverser ``os.path.isfile``). Les projets sont **déterministes** : mêmes
paramètres, même projet, pour comparer avant / après.

Scénarios (``kind``) :

- ``short_1t``  : une piste vidéo, ``N`` clips courts (2 s) avec de petits trous ;
- ``short_8t``  : 5 pistes vidéo + 3 pistes audio, ``N`` clips courts répartis ;
- ``long_8t``   : 8 pistes, ``N`` clips longs (300 s) qui se chevauchent.
"""

from __future__ import annotations

from pathlib import Path

from core.project_model import Clip, MediaAsset, Project, Track

KINDS = ("short_1t", "short_8t", "long_8t")
ASSET_COUNT = 24


def _assets(media_path: str | None, count: int) -> list[MediaAsset]:
    assets = []
    for index in range(count):
        path = media_path or f"/nonexistent/media/source_{index}.mp4"
        assets.append(
            MediaAsset(
                id=f"asset_{index}",
                path=path,
                name=f"Source {index}",
                duration=36_000.0,
                width=1920,
                height=1080,
                fps=25.0,
                media_type="video",
                has_audio=True,
            )
        )
    return assets


def _audio_assets(media_path: str | None, count: int) -> list[MediaAsset]:
    return [
        MediaAsset(
            id=f"audio_{index}",
            path=media_path or f"/nonexistent/media/audio_{index}.wav",
            name=f"Audio {index}",
            duration=36_000.0,
            width=0,
            height=0,
            fps=0.0,
            media_type="audio",
            has_audio=True,
        )
        for index in range(count)
    ]


def _track_plan(kind: str) -> list[tuple[str, str]]:
    if kind == "short_1t":
        return [("V1", "video")]
    return [(f"V{i + 1}", "video") for i in range(5)] + [
        (f"A{i + 1}", "audio") for i in range(3)
    ]


def build_project(
    clips: int,
    kind: str = "short_1t",
    *,
    media_path: str | None = None,
    name: str | None = None,
) -> Project:
    """Construit un projet de ``clips`` clips réparti selon ``kind``."""
    if kind not in KINDS:
        raise ValueError(f"Scénario inconnu : {kind!r} (attendu : {KINDS})")
    plan = _track_plan(kind)
    assets = _assets(media_path, ASSET_COUNT) + _audio_assets(media_path, 4)
    video_ids = [a.id for a in assets if a.media_type == "video"]
    audio_ids = [a.id for a in assets if a.media_type == "audio"]
    per_track = max(1, -(-clips // len(plan)))
    clip_length = 300.0 if kind == "long_8t" else 2.0
    stride = 150.0 if kind == "long_8t" else 2.5  # long : chevauchement 50 %
    tracks: list[Track] = []
    produced = 0
    for track_id, track_type in plan:
        items: list[Clip] = []
        for position in range(per_track):
            if produced >= clips:
                break
            pool = audio_ids if track_type == "audio" else video_ids
            items.append(
                Clip(
                    id=f"{track_id}-c{position}",
                    asset_id=pool[(produced + position) % len(pool)],
                    track_id=track_id,
                    timeline_start=position * stride,
                    source_in=float(position % 50),
                    source_out=float(position % 50) + clip_length,
                    label=f"{track_id} #{position}",
                )
            )
            produced += 1
        tracks.append(Track(id=track_id, name=track_id, type=track_type, clips=items))
    return Project(
        name=name or f"synthetic-{kind}-{clips}",
        width=1920,
        height=1080,
        fps=25.0,
        media_assets=assets,
        tracks=tracks,
    )


def write_dummy_media(directory: Path) -> str:
    """Petit fichier factice (existant) pour les scénarios qui testent ``isfile``."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "dummy.mp4"
    target.write_bytes(b"\x00" * 64)
    return str(target)
