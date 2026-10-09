"""Versions d'un montage dans d'autres formats sociaux : une séquence par format, mise en page pour son cadre."""

from __future__ import annotations

import pytest

from core.format_versions import (
    MAX_SHIFT,
    _best_shift,
    _layer_boxes,
    create_format_version,
    find_format_version,
)
from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip, update_graphic
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.visual_effects import ClipTransform, TransformKeyframe


def _vertical_project() -> Project:
    """Un montage 9:16 : un plan plein cadre, une incrustation, deux lignes de titre, un fond image plein cadre."""
    project = Project(name="Social", width=1080, height=1920, fps=30.0,
                      media_assets=[MediaAsset("v", "/media/plan.mp4", "plan", 10.0, 1080, 1920, 30.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[
                          Clip("plein", "v", "V1", 0.0, 0.0, 5.0),
                          Clip("incrust", "v", "V1", 5.0, 0.0, 5.0,
                               transform=ClipTransform(scale=0.4, position_x=0.25, position_y=-0.2)),
                      ])])
    add_graphic_clip(project, "solid", timeline_start=0.0, duration=10.0,
                     graphic=GraphicOverlay(type=GraphicType.SOLID, width=1080, height=1920))
    for name, y in (("haut", -0.40), ("bas", -0.33)):
        title = add_graphic_clip(project, "text", timeline_start=0.0, duration=10.0)
        update_graphic(title, "text", name.upper())
        update_graphic(title, "font_size", 110)
        update_graphic(title, "width", 900)
        update_graphic(title, "height", 130)
        title.transform = ClipTransform(position_y=y)
        title.transform_keyframes = [TransformKeyframe("position_x", 0.0, -0.1), TransformKeyframe("position_x", 1.0, 0.0)]
    return project


def _graphics(sequence):
    return [clip for track in sequence.tracks if track.type == "graphics" for clip in track.clips]


def test_a_version_is_a_sequence_of_the_format_linked_to_its_origin():
    project = _vertical_project()
    source = project.active_sequence
    version, _changes = create_format_version(project, source.id, "square")
    assert (version.width, version.height) == (1080, 1080)
    assert version.name == f"{source.name} · 1:1" and version.format_source == source.id
    assert project.active_sequence_id == source.id, "on reste sur le montage d'origine"
    assert find_format_version(project, source.id, "square") is version
    with pytest.raises(ValueError):
        create_format_version(project, source.id, "vertical")      # déjà à ce format


def test_full_frame_shots_fill_the_new_frame_and_an_inset_keeps_its_size():
    project = _vertical_project()
    version, changes = create_format_version(project, project.active_sequence.id, "landscape")
    plein, incrust = version.tracks[0].clips
    assert plein.transform.fill and not incrust.transform.fill
    assert incrust.transform.scale == pytest.approx(0.4)
    assert [change.kind for change in changes].count("fill") == 1


def test_the_layers_keep_their_proportions_and_a_background_covers_the_frame():
    """9:16 vers 16:9 : la mise en page est contenue (×0,5625) ; avant, les deux lignes du titre se superposaient."""
    project = _vertical_project()
    source = project.active_sequence
    before = {clip.graphic.text: clip for clip in _graphics(source) if clip.graphic.type == GraphicType.TEXT}
    version, _changes = create_format_version(project, source.id, "landscape")
    contain, cover = 1080 / 1920, 1920 / 1080
    by_text = {clip.graphic.text: clip for clip in _graphics(version) if clip.graphic.type == GraphicType.TEXT}
    background = next(clip for clip in _graphics(version) if clip.graphic.type == GraphicType.SOLID)
    high, low = by_text["HAUT"], by_text["BAS"]
    assert background.transform.scale == pytest.approx(cover), "un fond plein cadre couvre, sans bandes"
    assert high.transform.scale == pytest.approx(contain) and low.transform.scale == pytest.approx(contain)
    # Décalage depuis le centre, en pixels : multiplié par le même facteur (fractions du nouveau cadre).
    assert high.transform.position_y * 1080 == pytest.approx(before["HAUT"].transform.position_y * 1920 * contain)
    assert high.transform_keyframes[0].value * 1920 == pytest.approx(-0.1 * 1080 * contain), "animation comprise"
    boxes = _layer_boxes(project, version)
    gap_before = 0.07 * 1920                                         # écart des lignes à l'origine (px)
    gap_after = (boxes[low.id][0][1] - boxes[high.id][0][1]) * 1080
    assert gap_after == pytest.approx(gap_before * contain, rel=0.02), "les lignes restent espacées"


def test_a_child_layer_keeps_its_pixel_offset_from_its_parent():
    project = _vertical_project()
    parent = _graphics(project.active_sequence)[1]
    child = add_graphic_clip(project, "shape", timeline_start=0.0, duration=10.0)
    child.graphic = GraphicOverlay(type=GraphicType.SHAPE, width=100, height=100, parent_id=parent.id)
    child.transform = ClipTransform(position_x=0.1, position_y=0.05)
    version, _changes = create_format_version(project, project.active_sequence.id, "landscape")
    moved = next(clip for clip in _graphics(version) if clip.graphic.parent_id)
    assert moved.transform.position_x * 1920 == pytest.approx(0.1 * 1080)
    assert moved.transform.position_y * 1080 == pytest.approx(0.05 * 1920)
    assert moved.transform.scale == pytest.approx(1.0), "son parent porte le facteur"


def test_a_layer_under_the_platform_interface_moves_by_the_smallest_free_shift():
    caption = (0.0, 0.0, 1.0, 0.1)                                    # une zone en haut du cadre
    assert _best_shift([((0.3, 0.05, 0.7, 0.15), True)], [caption]) == pytest.approx((0.0, 0.05))
    assert _best_shift([((0.3, 0.2, 0.7, 0.3), True)], [caption]) is None, "rien de caché : il reste"
    # Jamais sur un autre calque affiché en même temps : il reste plutôt.
    assert _best_shift([((0.3, 0.05, 0.7, 0.15), True)], [caption], [(0.0, 0.1, 1.0, 1.0)]) is None
    # Jamais plus loin que MAX_SHIFT.
    deep = (0.0, 0.0, 1.0, MAX_SHIFT + 0.2)
    assert _best_shift([((0.3, 0.05, 0.7, 0.15), True)], [deep]) is None
    # Des lettres hors du cadre sont ramenées dedans.
    assert _best_shift([((-0.1, 0.4, 0.5, 0.5), True)], []) == pytest.approx((0.1, 0.0))
    assert _best_shift([((-0.1, 0.4, 0.5, 0.5), False)], []) is None, "un décor peut déborder"


def test_versions_are_reused_unless_laid_out_again_and_survive_a_save(tmp_path):
    project = _vertical_project()
    source_id = project.active_sequence.id
    version, _changes = create_format_version(project, source_id, "square")
    version.tracks[0].clips[0].transform = ClipTransform(scale=1.3, fill=True)   # une retouche
    again, _changes = create_format_version(project, source_id, "square")
    assert again.id == version.id and len(project.sequences) == 2, "refaite à la place, même identifiant"
    assert again.tracks[0].clips[0].transform.scale == pytest.approx(1.0)
    path = tmp_path / "versions.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert find_format_version(loaded, source_id, "square") is not None


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    for asset in window.project.media_assets:            # le projet d'exemple n'a pas de fichiers : un chemin suffit
        if not asset.path:
            asset.path = str(tmp_path / f"{asset.id}.mp4")
    return window


def test_the_dialog_creates_the_versions_and_queues_one_export_per_format(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog

    from ui.social_dialogs import FormatVersionsChoice, FormatVersionsDialog

    monkeypatch.setattr(FormatVersionsDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(FormatVersionsDialog, "choice",
                        lambda self: FormatVersionsChoice(("vertical", "square", "landscape"), False, True))
    monkeypatch.setattr(window, "_ask_export_path", lambda spec: str(tmp_path / "Montage.mp4"))
    source = window.project.active_sequence
    source.width, source.height = 1080, 1920
    window.export_format_versions()
    names = sorted(job.output_path.rsplit("/", 1)[-1] for job in window.render_queue.jobs)
    assert names == ["Montage_16x9.mp4", "Montage_1x1.mp4", "Montage_9x16.mp4"]
    assert len(window.project.sequences) == 3
    assert window.history.undo_label == "Versions de format"
    assert window.project.active_sequence_id == source.id
    window.export_format_versions()                      # les versions existent : réutilisées, pas dupliquées
    assert len(window.project.sequences) == 3
