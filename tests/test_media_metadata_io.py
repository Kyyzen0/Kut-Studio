"""Métadonnées de média (timecode, BWF, bobine, caméra, création) : modèle, fichier ``.kut`` et copies.

Elles sont lues par la sonde (``tests/test_media_probe_metadata.py``) et servent à la synchronisation Multicam par
timecode. Ici : elles survivent à l'aller-retour, un projet qui n'en a pas écrit les mêmes octets qu'avant, un fichier
ancien s'ouvre avec les défauts, et une valeur illisible ne fait jamais échouer l'ouverture.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from core.cache_store import MemoryCache
from core.media_cache import cached_probe
from core.project_io import load_project, project_payload, save_project
from core.project_model import MediaAsset, Project

_KEYS = ("timecode", "timecode_fps", "time_reference", "reel", "camera", "creation_time")


def _video(**metadata) -> MediaAsset:
    return MediaAsset("v", "/media/a.mp4", "a.mp4", 12.0, 1920, 1080, 29.97, "video", True, **metadata)


def _reload(tmp_path: Path, project: Project) -> Project:
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    return load_project(str(path))


def _written_asset(tmp_path: Path, asset: MediaAsset) -> dict:
    path = tmp_path / "w.kut"
    save_project(Project(name="w", media_assets=[asset]), str(path))
    return json.loads(path.read_text(encoding="utf-8"))["project"]["media_assets"][0]


def test_non_default_metadata_survives_a_save_and_load(tmp_path):
    video = _video(timecode="01:02:03;04", timecode_fps=30000 / 1001, reel="A001", camera="Sony A7S III",
                   creation_time="2026-03-14T09:26:53Z")
    audio = MediaAsset("a", "/media/a.wav", "a.wav", 30.0, 0, 0, 0.0, "audio", True, time_reference=3600.5)
    loaded = _reload(tmp_path, Project(name="m", media_assets=[video, audio]))
    assert loaded.media_assets == [video, audio]
    assert loaded.media_assets[0].timecode == "01:02:03;04"          # le « ; » du drop-frame est gardé tel quel
    assert loaded.media_assets[1].time_reference == 3600.5


def test_a_media_without_metadata_writes_exactly_the_historical_keys(tmp_path):
    """Pas de clé neuf par défaut : un projet sans timecode est écrit octet pour octet comme avant (aucun bump)."""
    written = _written_asset(tmp_path, _video())
    assert sorted(written) == sorted(
        ["id", "path", "name", "duration", "width", "height", "fps", "media_type", "has_audio"]
    )


def test_only_the_metadata_a_media_has_is_written(tmp_path):
    written = _written_asset(tmp_path, _video(reel="A001", time_reference=0.0))
    assert written["reel"] == "A001"
    assert written["time_reference"] == 0.0                          # 0 s (minuit) est une valeur, pas une absence
    for absent in ("timecode", "timecode_fps", "camera", "creation_time"):
        assert absent not in written


def test_an_old_file_without_the_keys_loads_with_the_defaults(tmp_path):
    path = tmp_path / "old.kut"
    save_project(Project(name="old", media_assets=[_video()]), str(path))
    document = json.loads(path.read_text(encoding="utf-8"))
    assert not any(key in document["project"]["media_assets"][0] for key in _KEYS)
    asset = load_project(str(path)).media_assets[0]
    assert (asset.timecode, asset.timecode_fps, asset.time_reference, asset.reel, asset.camera, asset.creation_time) == (
        "", 0.0, None, "", "", "")


@pytest.mark.parametrize(
    "key, junk",
    [
        ("timecode", 12), ("timecode", ["01:00:00:00"]), ("reel", None), ("camera", {"a": 1}),
        ("creation_time", 20260314), ("timecode_fps", "29.97"), ("timecode_fps", True), ("timecode_fps", -25.0),
        ("time_reference", "3600"), ("time_reference", False), ("time_reference", -1.0), ("time_reference", [1]),
    ],
)
def test_an_unreadable_metadata_value_falls_back_to_its_default_instead_of_failing(tmp_path, key, junk):
    path = tmp_path / "junk.kut"
    save_project(Project(name="j", media_assets=[_video()]), str(path))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["project"]["media_assets"][0][key] = junk
    path.write_text(json.dumps(document), encoding="utf-8")
    asset = load_project(str(path)).media_assets[0]
    assert getattr(asset, key) == getattr(_video(), key)
    assert asset.width == 1920                                       # le reste du média est intact


def test_an_integer_time_reference_is_read_as_seconds(tmp_path):
    path = tmp_path / "int.kut"
    save_project(Project(name="i", media_assets=[_video()]), str(path))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["project"]["media_assets"][0]["time_reference"] = 7200
    path.write_text(json.dumps(document), encoding="utf-8")
    reference = load_project(str(path)).media_assets[0].time_reference
    assert reference == 7200.0 and isinstance(reference, float)


def test_a_non_finite_number_never_reaches_the_model_nor_the_file():
    asset = _video(timecode_fps=math.inf, time_reference=math.nan)
    assert asset.timecode_fps == 0.0 and asset.time_reference is None
    assert "time_reference" not in project_payload(Project(name="n", media_assets=[asset]))["project"]["media_assets"][0]


def test_an_unknown_key_is_still_refused_by_the_asset_reader(tmp_path):
    """Contrat documenté : ``MediaAsset(**item)`` reste strict sur les clés inconnues (un lecteur ancien refuserait
    un fichier qui porte les nouvelles clés ; on n'ouvre pas un fichier plus récent avec une version plus ancienne)."""
    path = tmp_path / "x.kut"
    save_project(Project(name="x", media_assets=[_video()]), str(path))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["project"]["media_assets"][0]["bobine_inconnue"] = "A001"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TypeError):
        load_project(str(path))


def test_the_metadata_follows_the_copies_made_by_the_probe_cache(tmp_path):
    """``cached_probe`` copie le média avec ``dataclasses.replace`` : les métadonnées doivent suivre la copie."""
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x" * 64)
    probed = _video(timecode="10:00:00:00", camera="Sony A7S III", reel="B002")

    def probe(path: str) -> MediaAsset:
        return replace(probed, path=path)

    cache = MemoryCache()
    first = cached_probe(cache, str(media), probe)
    second = cached_probe(cache, str(media), probe)           # servi par le cache, copie neuve
    assert first is not second
    for asset in (first, second):
        assert (asset.timecode, asset.camera, asset.reel) == ("10:00:00:00", "Sony A7S III", "B002")


def test_the_rich_project_carries_metadata_through_the_round_trip(tmp_path):
    """Le projet de test partagé porte des métadonnées : la mutation clé par clé de ``test_kut_integrity`` les couvre."""
    from rich_project import build_rich_project

    project = build_rich_project()
    video = next(asset for asset in project.media_assets if asset.id == "av1")
    audio = next(asset for asset in project.media_assets if asset.id == "aa1")
    assert video.timecode and video.timecode_fps and video.reel and video.camera and video.creation_time
    assert audio.time_reference is not None
    reloaded = _reload(tmp_path, project)
    assert [replace(a) for a in reloaded.media_assets] == project.media_assets
