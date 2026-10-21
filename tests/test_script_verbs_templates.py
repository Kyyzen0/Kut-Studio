"""Format déclaratif : emplacements de modèle, diaporama de photos, classement (lot 5 de l'extension 0.8.0) : `fill_slot`, `fill_slot_with_photo`,
`fill_slots_with_photos`, `add_photo_slideshow`, `add_leaderboard`, `update_leaderboard`.

Les photos sont des fichiers lus en lecture seule (dossier du script, `--allow-outside`), en-tête seul, comme `add_image_layer`. Chaque verbe appelle la
fonction du modèle que les commandes « Social » de l'interface appellent ; là où le modèle passe en silence (photo illisible, photos en trop), un script reçoit
un refus et le projet reste strictement inchangé.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from core.beat_grid import BeatGrid
from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip, update_graphic
from core.leaderboard import Leaderboard, LeaderboardRow, build_leaderboard
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import (
    ScriptContext,
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting import auto_rules
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from core.template_slots import (
    add_photo_slideshow,
    fill_slot,
    fill_slot_with_photo,
    fill_slots_with_photos,
)
from core.timeline_operations import find_clip
from ui import i18n

NEW_VERBS = (
    "fill_slot",
    "fill_slot_with_photo",
    "fill_slots_with_photos",
    "add_photo_slideshow",
    "add_leaderboard",
    "update_leaderboard",
)
FILE_VERBS = ("fill_slot_with_photo", "fill_slots_with_photos", "add_photo_slideshow")


@pytest.fixture(autouse=True)
def _qt(qapp):
    """Les images de test sont écrites avec Qt (comme les tests du verbe add_image_layer)."""


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _image(path: Path, size=(160, 90), fmt: str = "PNG") -> Path:
    from PySide6.QtGui import QColor, QImage

    image = QImage(size[0], size[1], QImage.Format_RGB32)
    image.fill(QColor("#E01010"))
    assert image.save(str(path), fmt)
    return path


def _png_header(width: int, height: int) -> bytes:
    """Un PNG dont seul l'en-tête est honnête : de quoi tester des dimensions démesurées sans fichier démesuré."""
    import struct
    import zlib

    def block(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + block(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + block(b"IDAT", zlib.compress(b"\x00"))
        + block(b"IEND", b"")
    )


def _project(*, grid: BeatGrid | None = None) -> Project:
    slots = [
        Clip(
            f"s{n}",
            "",
            "V1",
            float(n),
            0.0,
            1.0,
            label=f"{n + 1:02d}",
            template_slot=f"slot-{n + 1:02d}",
        )
        for n in range(3)
    ]
    project = Project(
        "Démo",
        width=108,
        height=192,
        fps=25.0,
        media_assets=[
            MediaAsset(
                "v", "/x/v.mp4", "v", 10.0, 1920, 1080, 25.0, "video", has_audio=True
            ),
            MediaAsset("short", "/x/s.mp4", "s", 0.4, 1920, 1080, 25.0, "video"),
            MediaAsset(
                "son", "/x/a.wav", "son", 9.0, 0, 0, 0.0, "audio", has_audio=True
            ),
        ],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                clips=[*slots, Clip("plain", "v", "V1", 5.0, 0.0, 2.0)],
            ),
            Track(
                "V2",
                "V2",
                "video",
                locked=True,
                clips=[
                    Clip(
                        "locked-slot",
                        "",
                        "V2",
                        0.0,
                        0.0,
                        1.0,
                        template_slot="slot-lock",
                    )
                ],
            ),
            Track("G1", "G1", "graphics"),
        ],
    )
    project.active_sequence.beat_grid = grid
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(
    project: Project,
    *ops: dict,
    folder: Path | None = None,
    allow_outside: bool = False,
):
    session = Session(project)
    context = (
        ScriptContext(folder, allow_outside=allow_outside)
        if folder is not None
        else None
    )
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)}), context=context
    )


def _snapshot(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _refused(
    project: Project,
    *ops: dict,
    folder: Path | None = None,
    allow_outside: bool = False,
):
    before = _snapshot(project)
    session, report = _apply(project, *ops, folder=folder, allow_outside=allow_outside)
    assert not report.ok
    assert _snapshot(session.project) == before
    return report


def _shape(project: Project, *, motion: bool = True):
    """Le projet sans les identifiants que le modèle tire au hasard (clips, médias de photos, images clés).

    ``motion=False`` : le Ken Burns d'un clip dépend de son identifiant (tiré au hasard pour un diaporama) ; on ne garde que « il y en a un »."""

    def clip(c):
        data = {
            k: v
            for k, v in asdict(c).items()
            if k not in ("id", "asset_id", "template_slot")
        }
        for field in ("animation", "transform_keyframes"):
            data[field] = [
                {k: v for k, v in kf.items() if k != "id"} for kf in data[field]
            ]
        if not motion:
            data["transform_keyframes"] = bool(data["transform_keyframes"])
        return data

    def ordered(
        clips,
    ):  # le modèle trie par (début, identifiant tiré au hasard) : l'ordre canonique ne dépend pas de l'identifiant
        return sorted(
            (clip(c) for c in clips),
            key=lambda data: (
                data["timeline_start"],
                data["label"],
                data["text"],
                repr(data["graphic"]),
            ),
        )

    return (
        [(t.id, t.type, ordered(t.clips)) for t in project.tracks],
        sorted(
            (a.media_type, a.name, a.duration, a.width, a.height, a.path)
            for a in project.media_assets
        ),
        [
            (key, group["kind"], group["data"], len(group["clips"]))
            for key, group in project.active_sequence.generated_groups.items()
        ],
    )


def _still(project: Project):
    return _shape(project, motion=False)


def _parity(folder: Path, op: dict, direct, factory=_project, shape=_shape) -> Project:
    scripted = factory()
    session, report = _apply(scripted, op, folder=folder)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert shape(session.project) == shape(reference)
    return session.project


def _photos(folder: Path, count: int = 3) -> list[Path]:
    return [_image(folder / f"p{n}.png", size=(160 + 10 * n, 90)) for n in range(count)]


def test_the_verbs_are_in_the_table_and_in_the_schema():
    from core.scripting.schema import json_schema

    assert set(NEW_VERBS) <= set(VERBS)
    for name in NEW_VERBS:
        assert VERBS[name].doc and VERBS[name].needs_context == (name in FILE_VERBS), (
            name
        )
    assert VERBS["add_leaderboard"].creates == "leaderboard" and {"id", "as"} <= set(
        VERBS["add_leaderboard"].accepts
    )
    assert set(NEW_VERBS) <= {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }


# --- fill_slot -------------------------------------------------------------------------------------------------------------


def test_fill_slot_matches_the_model_for_a_video_and_a_shorter_media(tmp_path):
    _parity(
        tmp_path,
        _op("fill_slot", clip="s1", asset="v"),
        lambda p: fill_slot(p, "s1", "v"),
    )
    project = _parity(
        tmp_path,
        _op("fill_slot", clip="s0", asset="short"),
        lambda p: fill_slot(p, "s0", "short"),
    )
    assert find_clip(project, "s0").duration == pytest.approx(0.4)


def test_fill_slot_with_a_photo_asset_of_the_library(tmp_path):
    photo = _image(tmp_path / "lib.png")

    def with_photo() -> Project:
        project = _project()
        project.media_assets.append(
            MediaAsset("lib", str(photo.resolve()), "lib", 0.0, 160, 90, 25.0, "image")
        )
        return project

    project = _parity(
        tmp_path,
        _op("fill_slot", clip="s0", asset="lib"),
        lambda p: fill_slot(p, "s0", "lib"),
        with_photo,
    )
    assert (
        find_clip(project, "s0").graphic is not None
        and find_clip(project, "s0").graphic.type == GraphicType.IMAGE
    )


@pytest.mark.parametrize(
    "operation",
    [
        _op("fill_slot", clip="plain", asset="v"),  # pas un emplacement
        _op("fill_slot", clip="s0", asset="son"),  # un son
        _op("fill_slot", clip="s0", asset="inconnu"),
        _op("fill_slot", clip="inconnu", asset="v"),
        _op("fill_slot", clip="locked-slot", asset="v"),  # piste verrouillée
    ],
)
def test_fill_slot_refusals(operation):
    _refused(_project(), operation)


def test_fill_slot_names_the_reason_when_the_clip_is_not_a_video_slot(tmp_path):
    _image(tmp_path / "a.png")
    assert (
        "emplacement de modèle"
        in _refused(_project(), _op("fill_slot", clip="plain", asset="v")).error
    )
    assert (
        "emplacement de modèle"
        in _refused(
            _project(),
            _op("fill_slot_with_photo", clip="plain", path="a.png"),
            folder=tmp_path,
        ).error
    )
    audio = _project()
    audio.tracks[0].clips[0].template_slot = ""
    audio.media_assets.append(
        MediaAsset("son2", "/x/b.wav", "b", 5.0, 0, 0, 0.0, "audio", has_audio=True)
    )
    audio.tracks.append(
        Track(
            "A1",
            "A1",
            "audio",
            clips=[
                Clip("a-slot", "son2", "A1", 0.0, 0.0, 1.0, template_slot="slot-audio")
            ],
        )
    )
    assert (
        "audio" in _refused(audio, _op("fill_slot", clip="a-slot", asset="v")).error
    )  # un emplacement est un clip de piste vidéo
    _refused(
        audio, _op("fill_slot_with_photo", clip="a-slot", path="a.png"), folder=tmp_path
    )


def test_every_file_verb_refuses_without_a_file_context():
    for operation in (
        _op("fill_slot_with_photo", clip="s0", path="a.png"),
        _op("fill_slots_with_photos", photos=["a.png"]),
        _op("add_photo_slideshow", photos=["a.png"], start=0.0),
    ):
        assert "contexte" in _refused(_project(), operation).error


def test_fill_slot_refuses_a_locked_layer_and_a_photo_asset_gone_missing(tmp_path):
    locked = _project()
    find_clip(locked, "s0").graphic = GraphicOverlay(
        type=GraphicType.TEXT, text="x", locked=True
    )
    _refused(locked, _op("fill_slot", clip="s0", asset="v"))
    gone = _project()
    gone.media_assets.append(
        MediaAsset(
            "lib", str(tmp_path / "absente.png"), "lib", 0.0, 160, 90, 25.0, "image"
        )
    )
    _refused(gone, _op("fill_slot", clip="s0", asset="lib"))


# --- fill_slot_with_photo --------------------------------------------------------------------------------------------------


def test_fill_slot_with_photo_matches_the_model_with_and_without_ken_burns(tmp_path):
    photo = _image(tmp_path / "a.png", size=(200, 100))
    _parity(
        tmp_path,
        _op("fill_slot_with_photo", clip="s0", path="a.png"),
        lambda p: fill_slot_with_photo(p, "s0", photo, (200, 100)),
    )
    project = _parity(
        tmp_path,
        _op("fill_slot_with_photo", clip="s0", path="a.png", ken_burns=False),
        lambda p: fill_slot_with_photo(p, "s0", photo, (200, 100), ken_burns=False),
    )
    assert find_clip(project, "s0").transform_keyframes == []


def test_fill_slot_with_photo_replaces_the_previous_photo_and_is_one_undo_step(
    tmp_path,
):
    _photos(tmp_path, 2)
    session, report = _apply(
        _project(),
        _op("fill_slot_with_photo", clip="s0", path="p0.png"),
        _op("fill_slot_with_photo", clip="s0", path="p1.png"),
        folder=tmp_path,
    )
    assert report.ok and [
        a.name for a in session.project.media_assets if a.media_type == "image"
    ] == ["p1"]  # l'ancien média de photo disparaît
    assert session.undo() and not [
        a for a in session.project.media_assets if a.media_type == "image"
    ]


def test_fill_slot_with_photo_follows_the_file_policy_of_the_other_verbs(tmp_path):
    folder = tmp_path / "script"
    folder.mkdir()
    outside = _image(tmp_path / "dehors.png")
    _image(folder / "ok.png")
    (folder / "texte.txt").write_text("pas une image")
    (folder / "faux.png").write_bytes(b"pas du png")
    (folder / "vide.png").write_bytes(b"")
    for path in (
        "../dehors.png",
        str(outside),
        "absent.png",
        "texte.txt",
        "faux.png",
        "vide.png",
    ):
        _refused(
            _project(), _op("fill_slot_with_photo", clip="s0", path=path), folder=folder
        )
    session, report = _apply(
        _project(),
        _op("fill_slot_with_photo", clip="s0", path=str(outside)),
        folder=folder,
        allow_outside=True,
    )
    assert report.ok  # décidé par l'appelant, jamais par le script
    _refused(
        _project(), _op("fill_slot_with_photo", clip="s0", path="ok.png")
    )  # sans contexte de fichiers : refusé


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="liens symboliques : droits particuliers sous Windows",
)
def test_fill_slot_with_photo_refuses_a_symlink_that_escapes_the_folder(tmp_path):
    folder = tmp_path / "script"
    folder.mkdir()
    (folder / "lien.png").symlink_to(_image(tmp_path / "dehors.png"))
    _refused(
        _project(),
        _op("fill_slot_with_photo", clip="s0", path="lien.png"),
        folder=folder,
    )


def test_fill_slot_with_photo_refuses_oversized_images_before_writing(tmp_path):
    from core.scripting import image_layer

    (tmp_path / "grande.png").write_bytes(_png_header(9000, 10))
    _refused(
        _project(),
        _op("fill_slot_with_photo", clip="s0", path="grande.png"),
        folder=tmp_path,
    )
    assert image_layer.MAX_IMAGE_SIDE == 8192


def test_fill_slot_with_photo_refuses_non_slots_locks_and_wrong_tracks(tmp_path):
    _image(tmp_path / "a.png")
    for clip in ("plain", "locked-slot", "inconnu"):
        _refused(
            _project(),
            _op("fill_slot_with_photo", clip=clip, path="a.png"),
            folder=tmp_path,
        )
    locked = _project()
    find_clip(locked, "s0").graphic = GraphicOverlay(
        type=GraphicType.TEXT, text="x", locked=True
    )
    _refused(
        locked, _op("fill_slot_with_photo", clip="s0", path="a.png"), folder=tmp_path
    )


# --- fill_slots_with_photos ------------------------------------------------------------------------------------------------


def test_fill_slots_with_photos_matches_the_model_in_timeline_order(tmp_path):
    photos = _photos(tmp_path, 2)
    sizes = [(160, 90), (170, 90)]
    _parity(
        tmp_path,
        _op("fill_slots_with_photos", photos=["p0.png", "p1.png"]),
        lambda p: fill_slots_with_photos(p, list(zip(photos, sizes))),
    )
    project = _parity(
        tmp_path,
        _op(
            "fill_slots_with_photos",
            photos=["p0.png", "p1.png"],
            start="s1",
            ken_burns=False,
        ),
        lambda p: fill_slots_with_photos(
            p, list(zip(photos, sizes)), start_clip_id="s1", ken_burns=False
        ),
    )
    assert [
        find_clip(project, name).graphic is not None for name in ("s0", "s1", "s2")
    ] == [False, True, True]


def test_fill_slots_with_photos_refuses_what_the_model_ignores_silently(tmp_path):
    _photos(tmp_path, 4)
    report = _refused(
        _project(),
        _op("fill_slots_with_photos", photos=["p0.png", "p1.png", "p2.png", "p3.png"]),
        folder=tmp_path,
    )  # 3 emplacements vides
    assert "emplacement" in report.error
    _refused(
        _project(),
        _op("fill_slots_with_photos", photos=["p0.png", "p1.png"], start="s2"),
        folder=tmp_path,
    )  # un seul à partir de s2
    (tmp_path / "faux.png").write_bytes(b"x")
    _refused(
        _project(),
        _op("fill_slots_with_photos", photos=["p0.png", "faux.png"]),
        folder=tmp_path,
    )  # le modèle sauterait la photo
    filled = _project()
    for slot in ("s0", "s1", "s2"):
        find_clip(filled, slot).asset_id = "v"
    _refused(
        filled, _op("fill_slots_with_photos", photos=["p0.png"]), folder=tmp_path
    )  # aucun emplacement vide
    _refused(
        _project(),
        _op("fill_slots_with_photos", photos=["p0.png"], start="locked-slot"),
        folder=tmp_path,
    )  # piste verrouillée
    _refused(
        _project(),
        _op("fill_slots_with_photos", photos=["p0.png"], start="plain"),
        folder=tmp_path,
    )


def test_fill_slots_with_photos_refuses_an_auto_selected_locked_layer_slot(tmp_path):
    _photos(tmp_path, 3)
    locked = _project()
    find_clip(locked, "s1").graphic = GraphicOverlay(
        type=GraphicType.TEXT, text="x", locked=True
    )  # vide, piste libre, calque verrouillé
    report = _refused(
        locked,
        _op("fill_slots_with_photos", photos=["p0.png", "p1.png"]),
        folder=tmp_path,
    )
    assert "verrouill" in report.error
    session, report = _apply(
        locked,
        _op("fill_slots_with_photos", photos=["p0.png"], start="s2"),
        folder=tmp_path,
    )  # s1 est avant le départ : non visé
    assert report.ok


def test_fill_slots_with_photos_skips_nothing_unseen_on_a_locked_track(tmp_path):
    _photos(tmp_path, 1)
    session, report = _apply(
        _project(), _op("fill_slots_with_photos", photos=["p0.png"]), folder=tmp_path
    )
    assert (
        report.ok and find_clip(session.project, "locked-slot").graphic is None
    )  # l'emplacement verrouillé n'est jamais rempli


# --- Diaporama -------------------------------------------------------------------------------------------------------------


def test_photo_slideshow_matches_the_model_without_a_grid(tmp_path):
    photos = _photos(tmp_path, 3)
    sizes = [(160, 90), (170, 90), (180, 90)]
    project = _parity(
        tmp_path,
        _op(
            "add_photo_slideshow",
            photos=["p0.png", "p1.png", "p2.png"],
            start=2.0,
            seconds=1.5,
        ),
        lambda p: add_photo_slideshow(
            p, list(zip(photos, sizes)), start=2.0, grid=None, seconds=1.5
        ),
        shape=_still,
    )
    track = next(
        t
        for t in project.tracks
        if any(c.label == "01" and c.timeline_start == 2.0 for c in t.clips)
    )
    assert [
        (c.timeline_start, c.duration)
        for c in track.clips
        if c.template_slot.startswith("photo-")
    ] == [(2.0, 1.5), (3.5, 1.5), (5.0, 1.5)]


def test_photo_slideshow_on_the_beat_grid_only_when_asked(tmp_path):
    photos = _photos(tmp_path, 2)
    sizes = [(160, 90), (170, 90)]
    grid = BeatGrid(120.0)
    factory = lambda: _project(grid=grid)  # noqa: E731
    project = _parity(
        tmp_path,
        _op(
            "add_photo_slideshow",
            photos=["p0.png", "p1.png"],
            start=0.3,
            beats_per_photo=4,
        ),
        lambda p: add_photo_slideshow(
            p, list(zip(photos, sizes)), start=0.3, grid=grid, beats_per_photo=4
        ),
        factory,
        _still,
    )
    new = [
        c
        for t in project.tracks
        for c in t.clips
        if c.template_slot.startswith("photo-")
    ]
    assert [(c.timeline_start, c.duration) for c in new] == [(0.5, 2.0), (2.5, 2.0)]
    ignored = _parity(
        tmp_path,
        _op(
            "add_photo_slideshow", photos=["p0.png", "p1.png"], start=0.3
        ),  # une grille existe, le script n'en veut pas
        lambda p: add_photo_slideshow(
            p, list(zip(photos, sizes)), start=0.3, grid=None
        ),
        factory,
        _still,
    )
    assert [
        c.timeline_start
        for t in ignored.tracks
        for c in t.clips
        if c.template_slot.startswith("photo-")
    ] == [0.3, 3.3]
    _refused(
        _project(),
        _op("add_photo_slideshow", photos=["p0.png"], start=0.0, beats_per_photo=2),
        folder=tmp_path,
    )  # pas de grille


def test_photo_slideshow_never_goes_over_a_clip_nor_a_locked_track(tmp_path):
    _photos(tmp_path, 2)
    project = _project()
    project.tracks[0].locked = True
    session, report = _apply(
        project,
        _op("add_photo_slideshow", photos=["p0.png", "p1.png"], start=0.0, seconds=1.0),
        folder=tmp_path,
    )
    assert report.ok
    created = [
        t
        for t in session.project.tracks
        if any(c.template_slot.startswith("photo-") for c in t.clips)
    ]
    assert len(created) == 1 and created[0].id not in (
        "V1",
        "V2",
    )  # ni la piste verrouillée, ni l'occupée
    session, report = _apply(
        _project(),
        _op("add_photo_slideshow", photos=["p0.png", "p1.png"], start=0.0, seconds=1.0),
        folder=tmp_path,
    )
    assert (
        report.ok
        and next(
            t
            for t in session.project.tracks
            if any(c.template_slot.startswith("photo-") for c in t.clips)
        ).id
        != "V1"
    )


def test_photo_slideshow_refusals_and_bounds(tmp_path):
    _photos(tmp_path, 2)
    (tmp_path / "faux.png").write_bytes(b"x")
    _refused(
        _project(),
        _op("add_photo_slideshow", photos=["p0.png", "faux.png"], start=0.0),
        folder=tmp_path,
    )  # le modèle laisserait une carte vide
    _refused(
        _project(),
        _op(
            "add_photo_slideshow",
            photos=["p0.png", "p1.png"],
            start=999_999.0,
            seconds=3600.0,
        ),
        folder=tmp_path,
    )  # finirait après 1 000 000 s
    for photos in ([], ["p0.png"] * 101):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [_op("add_photo_slideshow", photos=photos, start=0.0)],
                }
            )
    for fields in (
        {"seconds": 0.05},
        {"seconds": 4000},
        {"beats_per_photo": 0},
        {"start": -1},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [
                        _op(
                            "add_photo_slideshow",
                            photos=["p0.png"],
                            **{"start": 0.0, **fields},
                        )
                    ],
                }
            )


def test_photo_slideshow_counts_clips_across_all_sequences_and_is_one_step(tmp_path):
    _photos(tmp_path, 3)
    crowded = _project()
    other = create_sequence(crowded, "autre")
    room = (
        auto_rules.MAX_CLIPS_IN_PROJECT - auto_rules.total_clips(crowded) - 2
    )  # 3 photos pour 2 places
    other.tracks.append(
        Track(
            "X1",
            "X1",
            "video",
            clips=[Clip(f"x{n}", "v", "X1", float(n), 0.0, 0.5) for n in range(room)],
        )
    )
    report = _refused(
        crowded,
        _op("add_photo_slideshow", photos=["p0.png", "p1.png", "p2.png"], start=0.0),
        folder=tmp_path,
    )
    assert "limite" in report.error
    session, report = _apply(
        _project(),
        _op("add_photo_slideshow", photos=["p0.png", "p1.png", "p2.png"], start=0.0),
        folder=tmp_path,
    )
    assert report.ok and session.undo() and len(session.project.tracks) == 3


# --- Classement ------------------------------------------------------------------------------------------------------------


_ROWS = [
    {"rank": "1", "name": "Alice", "value": "302"},
    {"rank": "2", "name": "Bob", "value": "236", "color": "#FF0000"},
]
_MODEL_ROWS = (
    LeaderboardRow("1", "Alice", "302"),
    LeaderboardRow("2", "Bob", "236", "#FF0000"),
)


def test_add_leaderboard_matches_the_model(tmp_path):
    project = _parity(
        tmp_path,
        _op("add_leaderboard", rows=_ROWS, start=1.0, duration=5.0, id="top"),
        lambda p: build_leaderboard(p, Leaderboard(_MODEL_ROWS, 1.0, 5.0, id="top")),
    )
    assert (
        "top" in project.active_sequence.generated_groups
        and len(project.tracks[2].clips) == 10
    )
    _parity(
        tmp_path,
        _op(
            "add_leaderboard",
            rows=_ROWS,
            start=0.0,
            duration=4.0,
            top=0.0,
            delay=0.5,
            id="t2",
        ),
        lambda p: build_leaderboard(
            p, Leaderboard(_MODEL_ROWS, 0.0, 4.0, 0.0, 0.5, id="t2")
        ),
    )


def test_leaderboard_is_cited_by_alias_then_rewritten_end_to_end():
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [
                _op(
                    "add_leaderboard",
                    rows=_ROWS,
                    start=0.0,
                    duration=6.0,
                    **{"as": "lb"},
                )
            ],
        }
    )
    session, report = _apply(
        _project(),
        *[
            {
                "op": "add_leaderboard",
                "rows": _ROWS,
                "start": 0.0,
                "duration": 6.0,
                "as": "lb",
            }
        ],
    )
    assert report.ok and created_ids(script, report) == [
        {"op": "0:add_leaderboard", "id": "lb"}
    ]
    session, report = _apply(
        _project(),
        _op("add_leaderboard", rows=_ROWS, start=1.0, duration=6.0, **{"as": "lb"}),
        _op(
            "update_leaderboard",
            leaderboard={"ref": "lb"},
            rows=[_ROWS[1]],
            duration=8.0,
        ),
    )
    assert report.ok
    group = session.project.active_sequence.generated_groups["lb"]
    assert (
        len(group["clips"]) == 5
        and group["data"]["start"] == 1.0
        and group["data"]["duration"] == 8.0
        and group["data"]["rows"][0][1] == "Bob"
    )
    assert len(session.project.tracks[2].clips) == 5  # les anciens calques sont retirés
    assert (
        session.undo()
        and "lb" not in session.project.active_sequence.generated_groups
        and not session.project.tracks[2].clips
    )


def test_update_leaderboard_without_new_rows_keeps_the_rows_and_the_place():
    session, report = _apply(
        _project(),
        _op(
            "add_leaderboard",
            rows=_ROWS,
            start=2.0,
            duration=6.0,
            top=0.1,
            delay=0.4,
            id="lb",
        ),
        _op("update_leaderboard", leaderboard="lb", duration=9.0),
    )
    data = session.project.active_sequence.generated_groups["lb"]["data"]
    assert (
        report.ok
        and (data["start"], data["duration"], data["top"], data["delay"])
        == (2.0, 9.0, 0.1, 0.4)
        and len(data["rows"]) == 2
    )


def test_leaderboard_refusals():
    _refused(
        _project(), _op("add_leaderboard", rows=_ROWS, start=0.0, duration=0.5)
    )  # trop court pour que toutes les lignes entrent
    _refused(_project(), _op("update_leaderboard", leaderboard="inconnu", duration=9.0))
    taken = _project()
    other = create_sequence(taken, "autre")
    other.generated_groups["top"] = {"kind": "leaderboard", "data": {}, "clips": []}
    _refused(
        taken, _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="top")
    )  # l'identifiant d'un classement d'une autre séquence
    session, report = _apply(
        _project(),
        _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="top"),
    )
    assert report.ok
    again = _apply(
        session.project,
        _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="top"),
    )[1]
    assert (
        not again.ok and "top" in again.error
    )  # déjà pris dans le projet (deux scripts)
    with pytest.raises(
        ScriptInvalid
    ):  # deux fois dans le même script : refusé à la lecture
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="a"),
                    _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="a"),
                ],
            }
        )
    _refused(
        _project(),
        _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0),
        _op("update_leaderboard", leaderboard="plain", duration=9.0),
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"rank": "1", "name": "x", "value": "1"}] * 11,
        [{"rank": "1", "name": "x"}],
        [{"rank": "1", "name": "x", "value": "1", "color": "rouge"}],
        [{"rank": "1", "name": "x" * 5000, "value": "1"}],
        [{"rank": "1", "name": "a\nb", "value": "1"}],
        [{"rank": "1", "name": "x", "value": "1", "autre": 3}],
    ],
)
def test_leaderboard_rows_are_bounded_at_parse(rows):
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("add_leaderboard", rows=rows, start=0.0, duration=6.0)],
            }
        )


def test_update_leaderboard_refuses_locked_layers_tracks_and_dependents_before_changing_anything():
    def board() -> Project:
        project = _project()
        session, report = _apply(
            project,
            _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0, id="lb"),
        )
        assert report.ok
        return session.project

    layer = board()
    member = layer.active_sequence.generated_groups["lb"]["clips"][3]
    update_graphic(find_clip(layer, member), "locked", True)
    report = _refused(layer, _op("update_leaderboard", leaderboard="lb", duration=9.0))
    assert "verrouill" in report.error
    track = board()
    track.tracks[2].locked = True
    _refused(track, _op("update_leaderboard", leaderboard="lb", duration=9.0))
    dependent = board()
    parent = dependent.active_sequence.generated_groups["lb"]["clips"][0]
    follower = add_graphic_clip(
        dependent,
        "text",
        timeline_start=0.0,
        duration=2.0,
        track=Track("G2", "G2", "graphics"),
    )
    dependent.tracks.append(Track("G2", "G2", "graphics", clips=[follower]))
    follower.track_id = "G2"
    follower.graphic = replace(follower.graphic, parent_id=parent, locked=True)
    report = _refused(
        dependent, _op("update_leaderboard", leaderboard="lb", duration=9.0)
    )
    assert "dépend" in report.error
    free = board()
    free.tracks.append(
        Track(
            "G2",
            "G2",
            "graphics",
            clips=[
                add_graphic_clip(
                    free,
                    "text",
                    timeline_start=0.0,
                    duration=2.0,
                    track=Track("G2", "G2", "graphics"),
                )
            ],
        )
    )
    free.tracks[-1].clips[0].graphic = replace(
        free.tracks[-1].clips[0].graphic,
        parent_id=free.active_sequence.generated_groups["lb"]["clips"][0],
    )
    session, report = _apply(
        free, _op("update_leaderboard", leaderboard="lb", duration=9.0)
    )
    assert report.ok  # un dépendant libre devient autonome, comme dans l'interface


def test_update_leaderboard_only_touches_leaderboards_and_counts_the_growth():
    project = _project()
    project.active_sequence.generated_groups["autre"] = {
        "kind": "autre",
        "data": {},
        "clips": [],
    }
    assert (
        "introuvable"
        in _refused(
            project, _op("update_leaderboard", leaderboard="autre", duration=9.0)
        ).error
    )
    session, report = _apply(
        _project(),
        _op("add_leaderboard", rows=[_ROWS[0]], start=0.0, duration=6.0, id="lb"),
    )
    assert report.ok
    full = session.project
    other = create_sequence(full, "autre")
    room = (
        auto_rules.MAX_CLIPS_IN_PROJECT - auto_rules.total_clips(full) - 4
    )  # 4 places : une ligne de plus en demande 5
    other.tracks.append(
        Track(
            "X1",
            "X1",
            "video",
            clips=[Clip(f"x{n}", "v", "X1", float(n), 0.0, 0.5) for n in range(room)],
        )
    )
    report = _refused(full, _op("update_leaderboard", leaderboard="lb", rows=_ROWS))
    assert "limite" in report.error
    again, report = _apply(
        full, _op("update_leaderboard", leaderboard="lb", rows=[_ROWS[1]])
    )  # autant de lignes qu'avant : rien de plus
    assert report.ok


def test_leaderboard_counts_its_layers_across_the_whole_project():
    crowded = _project()
    other = create_sequence(crowded, "autre")
    room = (
        auto_rules.MAX_CLIPS_IN_PROJECT - auto_rules.total_clips(crowded) - 9
    )  # 10 calques pour 9 places
    other.tracks.append(
        Track(
            "X1",
            "X1",
            "video",
            clips=[Clip(f"x{n}", "v", "X1", float(n), 0.0, 0.5) for n in range(room)],
        )
    )
    report = _refused(
        crowded, _op("add_leaderboard", rows=_ROWS, start=0.0, duration=6.0)
    )
    assert "limite" in report.error
    one_row = [_ROWS[0]]
    session, report = _apply(
        crowded, _op("add_leaderboard", rows=one_row, start=0.0, duration=6.0)
    )
    assert report.ok
