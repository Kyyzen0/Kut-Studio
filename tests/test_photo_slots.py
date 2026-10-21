"""Photos dans les emplacements de template, et diaporama photo (modèle, plan de rendu, évaluateurs, rendu réel)."""

from __future__ import annotations

import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.animation import InterpolationType
from core.beat_grid import BeatGrid
from core.graphics import GraphicType
from core.ken_burns import photo_size
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan, photo_monitor_layers
from core.template_slots import (
    SlotError,
    add_photo_slideshow,
    empty_slots,
    fill_slot,
    fill_slot_with_photo,
    fill_slots_with_photos,
    is_photo_path,
    is_photo_slot,
    slideshow_cuts,
)
from core.timeline_evaluator import evaluate_timeline
from core.timeline_index import build_timeline_index
from core.visual_effects import TransformKeyframe

W, H = 108, 192


@pytest.fixture
def photo(qapp, tmp_path):
    """Photo paysage 300 × 200 : dégradé gauche → droite, bande rouge au centre (un recadrage se voit)."""
    from PySide6.QtGui import QColor, QImage

    image = QImage(300, 200, QImage.Format_RGB32)
    for x in range(300):
        for y in range(200):
            red = 230 if 140 <= x < 160 else 0
            image.setPixelColor(x, y, QColor(red, int(x * 255 / 299), 255 - int(x * 255 / 299)))
    path = tmp_path / "photo.png"
    assert image.save(str(path))
    return path


def _project(slot_count: int = 3) -> Project:
    slots = [Clip(id=f"s{n}", asset_id="", track_id="V1", timeline_start=float(n), source_in=0.0, source_out=1.0,
                  label=f"{n + 1:02d}", template_slot=f"slot-{n + 1:02d}") for n in range(slot_count)]
    slots[0].transform_keyframes = [TransformKeyframe("scale", 0.0, 1.14, InterpolationType.EASE_OUT),
                                    TransformKeyframe("scale", 0.3, 1.0, InterpolationType.LINEAR)]
    return Project(name="t", width=W, height=H, fps=25.0, media_assets=[], tracks=[
        Track(id="V1", name="V1", type="video", clips=slots)])


def _clip(project: Project, clip_id: str) -> Clip:
    return next(clip for track in project.tracks for clip in track.clips if clip.id == clip_id)


# --- remplir ----------------------------------------------------------------------------------------------------------


def test_a_photo_fills_a_slot_and_keeps_its_place_and_effects(photo):
    project = _project()
    project.tracks[0].clips[0].effects = []
    clip = fill_slot_with_photo(project, "s0", photo, (300, 200))

    assert (clip.timeline_start, clip.duration, clip.template_slot) == (0.0, 1.0, "slot-01")
    assert is_photo_slot(clip) and clip.graphic.type == GraphicType.IMAGE
    assert clip.graphic.source_path == str(photo.resolve())
    assert (clip.graphic.width, clip.graphic.height) == photo_size(300, 200, W, H, fill=True), "la photo remplit"
    asset = next(item for item in project.media_assets if item.id == clip.asset_id)
    assert asset.media_type == "image" and asset.path == str(photo.resolve())
    assert (asset.width, asset.height) == (300, 200), "taille d'origine : le cadrage « remplir » vient du clip"
    assert clip.transform.fill
    assert [slot.id for slot in empty_slots(project)] == ["s1", "s2"]
    second = fill_slot_with_photo(project, "s1", photo, (300, 200))
    assert second.asset_id == clip.asset_id, "la même photo, un seul média"


def test_a_photo_gets_a_ken_burns_that_replaces_the_impact_zoom(photo):
    project = _project()
    clip = fill_slot_with_photo(project, "s0", photo, (300, 200))
    animated = {frame.property_name for frame in clip.transform_keyframes}
    assert animated and animated <= {"scale", "position_x", "position_y"}
    assert all(frame.interpolation == InterpolationType.LINEAR for frame in clip.transform_keyframes)
    assert max(frame.time_seconds for frame in clip.transform_keyframes) == pytest.approx(clip.duration)
    still = fill_slot_with_photo(project, "s1", photo, (300, 200), ken_burns=False)
    assert still.transform_keyframes == []


def test_a_video_then_takes_the_place_of_the_photo_and_the_photo_media_goes(photo, tmp_path, qapp):
    from PySide6.QtGui import QColor, QImage

    project = _project()
    fill_slot_with_photo(project, "s0", photo, (300, 200))
    first_photo = _clip(project, "s0").asset_id
    other = tmp_path / "autre.png"
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(other))
    fill_slot_with_photo(project, "s0", other, (40, 30))
    assert first_photo not in {asset.id for asset in project.media_assets}, "une photo remplacée ne reste pas"
    project.media_assets.append(MediaAsset("v", str(tmp_path / "v.mp4"), "v", 10.0, 1920, 1080, 25.0, "video"))
    clip = fill_slot(project, "s0", "v")
    assert clip.graphic is None and not is_photo_slot(clip) and clip.asset_id == "v"
    assert [asset.id for asset in project.media_assets] == ["v"]
    assert fill_slot_with_photo(project, "s0", photo, (300, 200)).graphic is not None
    assert "v" in {asset.id for asset in project.media_assets}, "un média importé n'est jamais retiré"


def test_what_a_slot_refuses(photo, tmp_path):
    project = _project()
    text = tmp_path / "notes.txt"
    text.write_text("x", encoding="utf-8")
    with pytest.raises(SlotError):
        fill_slot_with_photo(project, "s0", text, (10, 10))
    with pytest.raises(FileNotFoundError):
        fill_slot_with_photo(project, "s0", tmp_path / "absente.jpg", (10, 10))
    with pytest.raises(SlotError):
        fill_slot_with_photo(project, "s0", photo, (0, 0))
    project.tracks[0].clips.append(Clip(id="plain", asset_id="x", track_id="V1", timeline_start=5.0, source_in=0.0,
                                        source_out=1.0))
    with pytest.raises(KeyError):
        fill_slot_with_photo(project, "plain", photo, (300, 200))
    project.tracks[0].locked = True
    with pytest.raises(ValueError):
        fill_slot_with_photo(project, "s1", photo, (300, 200))
    assert empty_slots(project) and not project.media_assets, "un refus ne laisse rien derrière lui"
    assert is_photo_path("A.JPG") and is_photo_path("b.webp") and not is_photo_path("c.mov")


def test_several_photos_go_one_per_slot_in_timeline_order(photo):
    project = _project(4)
    fill_slot_with_photo(project, "s2", photo, (300, 200))
    filled = fill_slots_with_photos(project, [(photo, (300, 200)), (photo, (0, 0)), (photo, (300, 200)),
                                              (photo, (300, 200)), (photo, (300, 200))], start_clip_id="s1")
    assert [clip.id for clip in filled] == ["s1", "s3"], "après l'emplacement visé, les vides qui suivent ; illisible sauté"
    assert [clip.id for clip in empty_slots(project)] == ["s0"]
    assert [clip.id for clip in fill_slots_with_photos(project, [(photo, (300, 200))] * 3)] == ["s0"]


# --- rendu ----------------------------------------------------------------------------------------------------------


def test_a_photo_slot_is_a_video_layer_of_the_plan_and_an_image_for_the_live_monitor(photo):
    project = _project()
    fill_slot_with_photo(project, "s0", photo, (300, 200))
    plan = build_render_plan(project)
    assert plan.empty_slots == ("s1", "s2") and plan.missing_media == ()
    (layer,) = [layer for layer in plan.video_layers if layer.clip_id == "s0"]
    assert layer.still and layer.source_path == str(photo.resolve()) and layer.transform.fill
    assert (layer.source_width, layer.source_height) == (300, 200) and layer.transform_keyframes
    assert not [item for item in plan.graphics_layers if item.clip_id == "s0" and item.role == "draw"]
    (monitor,) = photo_monitor_layers(plan)
    assert monitor.clip_id == "s0" and monitor.graphic.type == GraphicType.IMAGE
    assert monitor.transform_keyframes == layer.transform_keyframes

    by_scan = [(clip.clip_id, clip.track_type) for clip in evaluate_timeline(project, 0.5)]
    by_index = [(clip.clip_id, clip.track_type) for clip in build_timeline_index(project).active_at(project, 0.5)]
    assert by_scan == by_index == [("s0", "graphics")], "le moniteur ne la confie pas au lecteur vidéo"


def test_a_photo_slot_follows_the_solo_of_its_video_track(photo, tmp_path):
    """Rangée en « graphics » pour le moniteur, une photo d'emplacement suit le solo de sa piste **vidéo**, comme dans le
    plan de rendu : un V2 en solo la masque, un solo de piste graphique non."""
    from core.timeline_editing import apply_solo

    project = _project(1)
    fill_slot_with_photo(project, "s0", photo, (300, 200))
    project.media_assets.append(MediaAsset("v", str(tmp_path / "v.mp4"), "v", 9.0, W, H, 25.0, "video"))
    project.tracks.append(Track(id="V2", name="V2", type="video", clips=[
        Clip(id="other", asset_id="v", track_id="V2", timeline_start=5.0, source_in=0.0, source_out=1.0)]))
    project.tracks.append(Track(id="G1", name="G1", type="graphics"))
    active = build_timeline_index(project).active_at(project, 0.5)
    assert [clip.clip_id for clip in apply_solo(project, active)] == ["s0"]
    project.tracks[1].solo = True
    assert apply_solo(project, active) == [], "V2 en solo : la photo de V1 disparaît, comme à l'export"
    assert [layer.clip_id for layer in build_render_plan(project).video_layers] == ["other"]
    project.tracks[1].solo = False
    project.tracks[2].solo = True
    assert [clip.clip_id for clip in apply_solo(project, active)] == ["s0"], "un solo graphique ne la masque pas"


def test_a_photo_slot_survives_the_kut_file(photo, tmp_path):
    project = _project()
    fill_slot_with_photo(project, "s0", photo, (300, 200))
    path = tmp_path / "p.kut"
    save_project(project, path)
    loaded = load_project(path)
    clip = _clip(loaded, "s0")
    assert is_photo_slot(clip) and clip.graphic.source_path == str(photo.resolve())
    assert [layer.clip_id for layer in build_render_plan(loaded).video_layers if layer.still] == ["s0"]


@needs_ffmpeg
def test_the_export_shows_the_photo_filling_the_frame_and_moving(photo, tmp_path):
    project = _project()
    clip = fill_slot_with_photo(project, "s0", photo, (300, 200))
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 1.0, InterpolationType.LINEAR),
                                TransformKeyframe("scale", 1.0, 1.5, InterpolationType.LINEAR)]
    plan = build_render_plan(project)
    start = render_frame(plan, W, H, 0.02).astype(int)
    end = render_frame(plan, W, H, 0.96).astype(int)
    assert start[2:6, 2:6].max() > 20 and start[-6:-2, -6:-2].max() > 20, "pas de noir : la photo couvre le cadre"
    band = start[H // 2, W // 2 - 3:W // 2 + 3]
    assert band[:, 0].max() > 150, "la bande rouge du centre de la photo est au centre du cadre"
    red_width = lambda frame: int((frame[H // 2, :, 0] > 150).sum())       # noqa: E731
    assert red_width(end) > red_width(start) * 1.3, "le zoom du clip agrandit la photo au fil du plan"
    video = lavfi_video(tmp_path / "blue.mp4", "color=c=blue", size=(160, 90), seconds=2.0)
    project.media_assets.append(MediaAsset("v", str(video), "v", 2.0, 160, 90, 25.0, "video"))
    fill_slot(project, "s0", "v")
    refilled = render_frame(build_render_plan(project), W, H, 0.5).astype(int)
    assert refilled[..., 2].mean() > 200 and refilled[..., 0].mean() < 40, "la vidéo a repris la place"


def _two_colour_photos(tmp_path, qapp):
    from PySide6.QtGui import QColor, QImage

    paths = []
    for name, colour in (("rouge", "#E01010"), ("vert", "#10E010")):
        image = QImage(160, 90, QImage.Format_RGB32)
        image.fill(QColor(colour))
        path = tmp_path / f"{name}.png"
        assert image.save(str(path))
        paths.append(path)
    return paths


@needs_ffmpeg
def test_a_transition_between_two_photo_slots_is_rendered(tmp_path, qapp):
    """Une photo d'emplacement est un calque vidéo : le fondu enchaîné entre deux photos se rend comme entre deux plans."""
    from core.transitions import add_transition

    red, green = _two_colour_photos(tmp_path, qapp)
    project = _project(2)
    fill_slot_with_photo(project, "s0", red, (160, 90), ken_burns=False)
    fill_slot_with_photo(project, "s1", green, (160, 90), ken_burns=False)
    add_transition(project, "s0", "s1", duration=0.4)
    plan = build_render_plan(project)
    assert len(plan.transitions) == 1, "la transition n'est plus écartée du plan"
    middle = render_frame(plan, W, H, 0.8).astype(int)[H // 2, W // 2]
    assert 60 < middle[0] < 200 and 60 < middle[1] < 200, ("mélange des deux photos", middle)


@needs_ffmpeg
def test_a_photo_slot_stays_under_the_video_tracks_above_it(photo, tmp_path):
    """Comme une vidéo à sa place : une piste vidéo au-dessus de l'emplacement le couvre (avant : la photo passait
    par-dessus toutes les pistes vidéo)."""
    project = _project(1)
    fill_slot_with_photo(project, "s0", photo, (300, 200), ken_burns=False)
    blue = lavfi_video(tmp_path / "blue.mp4", "color=c=blue", size=(W, H), seconds=2.0)
    project.media_assets.append(MediaAsset("b", str(blue), "b", 2.0, W, H, 25.0, "video"))
    project.tracks.append(Track(id="V2", name="V2", type="video", clips=[
        Clip(id="top", asset_id="b", track_id="V2", timeline_start=0.0, source_in=0.0, source_out=1.0)]))
    frame = render_frame(build_render_plan(project), W, H, 0.5).astype(int)
    assert frame[..., 2].mean() > 200 and frame[..., 0].mean() < 40, "la vidéo de V2 couvre la photo de V1"


def test_photos_skip_the_slots_of_a_locked_track_and_a_locked_start_changes_nothing(photo):
    """Plusieurs pistes d'emplacements, l'une verrouillée : ses emplacements sont sautés d'avance ; partir d'un
    emplacement verrouillé est refusé avant toute modification (rien de rempli sans entrée d'historique)."""
    project = _project(2)
    project.tracks.append(Track(id="V2", name="V2", type="video", locked=True, clips=[
        Clip(id="locked", asset_id="", track_id="V2", timeline_start=0.5, source_in=0.0, source_out=1.0,
             label="L", template_slot="slot-L")]))
    filled = fill_slots_with_photos(project, [(photo, (300, 200))] * 3)
    assert [clip.id for clip in filled] == ["s0", "s1"]
    assert not is_photo_slot(_clip(project, "locked"))
    before = len(project.media_assets)
    with pytest.raises(ValueError):
        fill_slots_with_photos(project, [(photo, (300, 200))], start_clip_id="locked")
    assert len(project.media_assets) == before and not is_photo_slot(_clip(project, "locked"))


# --- diaporama --------------------------------------------------------------------------------------------------------


def test_slideshow_cuts_follow_the_bars_of_the_grid_or_the_photo_duration():
    grid = BeatGrid(bpm=120.0, offset=0.25)
    assert slideshow_cuts(3, start=1.0, grid=grid) == [(1.25, 3.25), (3.25, 5.25), (5.25, 7.25)]
    assert slideshow_cuts(2, start=1.0, grid=grid, beats_per_photo=2) == [(1.25, 2.25), (2.25, 3.25)]
    assert slideshow_cuts(2, start=0.5, seconds=2.5) == [(0.5, 3.0), (3.0, 5.5)]
    assert slideshow_cuts(0, start=0.0) == []


def test_a_slideshow_puts_photo_slots_on_a_free_video_track(photo, tmp_path):
    project = _project(1)
    project.tracks[0].clips.append(Clip(id="busy", asset_id="x", track_id="V1", timeline_start=3.0, source_in=0.0,
                                        source_out=4.0))
    clips = add_photo_slideshow(project, [(photo, (300, 200)), (tmp_path / "absente.jpg", (300, 200)),
                                          (photo, (300, 200))], start=2.0, seconds=1.5)
    assert [(clip.timeline_start, clip.duration) for clip in clips] == [(2.0, 1.5), (3.5, 1.5), (5.0, 1.5)]
    track = next(track for track in project.tracks if track.id == clips[0].track_id)
    assert track.id != "V1" and track.type == "video", "V1 est occupée : une piste libre (nouvelle) les reçoit"
    assert [is_photo_slot(clip) for clip in clips] == [True, False, True], "une photo illisible laisse sa carte"
    assert [clip.label for clip in clips] == ["01", "02", "03"]
    assert {clip.id for clip in clips}.isdisjoint(clip.id for clip in add_photo_slideshow(
        project, [(photo, (300, 200))], start=20.0)), "deux diaporamas, des identifiants distincts"
    assert len({clip.template_slot for clip in clips}) == 3
