"""Moniteur Multicam : grille adaptative, angle actif, états hors ligne / sans signal, flux, clic, qualité, fermeture.

Les flux d'images sont remplacés par des flux factices (un aplat de couleur par média) : on teste le moniteur, pas FFmpeg
(les vrais flux sont dans ``test_multicam_feed.py``, la chaîne complète dans ``test_multicam_export.py``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from core.multicam import PAGE_SIZE, AngleState, grid_shape, page_count
from core.multicam_feed import FeedPool
from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip
from core.project_model import MediaAsset, Project, Track
from multicam_stubs import COLOURS, StubFeed
from ui import i18n
from ui.multicam_viewer import MulticamViewer

@pytest.fixture(autouse=True)
def _reset():
    StubFeed.created = []
    StubFeed.lag = 0.0
    i18n.set_language("fr")
    yield
    i18n.set_language("fr")


def _project(tmp_path, angles: int = 4, *, offline: tuple[int, ...] = ()):
    assets, specs = [], []
    for number in range(angles):
        path = tmp_path / f"cam{number}.mp4"
        if number not in offline:
            path.write_bytes(b"x")
        assets.append(MediaAsset(f"cam{number}", str(path), f"cam{number}", 60.0, 1920, 1080, 30.0, "video", True))
        specs.append(AngleSpec(asset_id=f"cam{number}", name=f"Cam {number + 1}", offset=0.0 if number != 2 else 5.0))
    project = Project("p", media_assets=assets, tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    source = create_multicam_source(project, specs, name="Concert")
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-2")
    return project, segment


def _viewer(qtbot, project, *, time=1.0, playing=False):
    state = {"time": time, "playing": playing}
    viewer = MulticamViewer(provider=lambda: (project, state["time"], state["playing"]), pool=FeedPool(StubFeed))
    qtbot.addWidget(viewer)
    viewer.resize(900, 520)
    viewer.show()
    return viewer, state


def _visible(viewer):
    return [tile for tile in viewer._tiles if not tile.isHidden()]  # noqa: SLF001


# --- grille adaptative -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "shape"),
    [(1, (1, 1)), (2, (1, 2)), (3, (2, 2)), (4, (2, 2)), (5, (3, 3)), (9, (3, 3)), (10, (4, 4)), (16, (4, 4)), (30, (4, 4))],
)
def test_the_grid_shape_follows_the_number_of_angles(count, shape):
    assert grid_shape(count) == shape
    assert page_count(16) == 1 and page_count(17) == 2 and page_count(PAGE_SIZE * 3) == 3


@pytest.mark.parametrize("angles", [2, 4, 9])
def test_the_tiles_are_laid_out_in_the_adaptive_grid_without_overlap(qtbot, tmp_path, angles):
    project, _segment = _project(tmp_path, angles)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    tiles = _visible(viewer)
    assert len(tiles) == angles
    rows, columns = grid_shape(angles)
    assert len({tile.y() for tile in tiles}) == rows and len({tile.x() for tile in tiles}) == columns
    rects = [tile.geometry() for tile in tiles]
    for index, rect in enumerate(rects):
        assert viewer.body.rect().contains(rect)
        assert not any(rect.intersects(other) for other in rects[index + 1:])
        assert not rect.intersects(viewer.program_tile.geometry())


def test_more_than_sixteen_angles_are_paged_and_only_the_visible_page_decodes(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 20)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    assert len(_visible(viewer)) == PAGE_SIZE and viewer.page_label.text() == "1/2"
    assert viewer.page_next.isEnabled() and not viewer.page_previous.isEnabled()
    first_page = set(StubFeed.created)
    viewer.page_next.click()
    assert len(_visible(viewer)) == 4 and viewer.page_label.text() == "2/2"
    assert viewer._tiles[0]._name == "Cam 17"        # noqa: SLF001
    assert {feed for feed in StubFeed.created} - first_page  # la page 2 ouvre ses propres flux


def test_the_program_keeps_its_picture_when_the_active_angle_is_on_another_page(qtbot, tmp_path):
    """Au-delà de 16 angles, l'angle du programme peut être hors de la page affichée : il continue d'être alimenté."""
    project, segment = _project(tmp_path, 20)
    segment.angle_id = "angle-18"                                       # page 2 ; la page 1 est affichée
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    assert viewer.page_label.text() == "1/2" and all(not tile._active for tile in _visible(viewer))   # noqa: SLF001
    assert viewer.program_tile._state is AngleState.LIVE and viewer.program_tile._frame is not None   # noqa: SLF001
    assert any("cam17" in path for path in StubFeed.created)             # son flux tourne bien
    viewer.page_next.click()
    viewer.page_previous.click()                                         # on s'éloigne puis on revient : toujours alimenté
    assert viewer.program_tile._state is AngleState.LIVE and viewer.program_tile._frame is not None   # noqa: SLF001


def test_an_offline_active_angle_on_another_page_shows_as_offline_in_the_program(qtbot, tmp_path):
    project, segment = _project(tmp_path, 20, offline=(17,))
    segment.angle_id = "angle-18"
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    assert viewer.program_tile._state is AngleState.OFFLINE             # noqa: SLF001


def test_a_narrow_viewer_puts_the_program_above_the_grid_and_a_wide_one_beside_it(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.resize(900, 420)
    viewer.refresh()
    assert viewer.program_tile.x() == 0 and viewer._tiles[0].x() > viewer.program_tile.width() - 1  # noqa: SLF001
    viewer.resize(420, 520)
    viewer.refresh()
    assert viewer._tiles[0].y() >= viewer.program_tile.geometry().bottom()  # noqa: SLF001


# --- contenu des tuiles --------------------------------------------------------------------------------------------------


def test_each_tile_shows_its_angle_the_active_one_is_marked_and_the_program_reuses_its_frame(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    tiles = _visible(viewer)
    assert [tile._name for tile in tiles] == ["Cam 1", "Cam 2", "Cam 3", "Cam 4"]    # noqa: SLF001
    assert [tile._active for tile in tiles] == [False, True, False, False]           # noqa: SLF001
    assert tiles[1]._state is AngleState.LIVE                                          # noqa: SLF001
    # l'image du programme est exactement celle de l'angle actif : aucun second flux, aucun second décodage
    assert viewer.program_tile._frame is tiles[1]._frame                              # noqa: SLF001
    assert len(StubFeed.created) == len(set(StubFeed.created))


def test_an_angle_that_has_not_started_shows_no_signal_and_an_offline_one_shows_media_offline(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4, offline=(3,))
    viewer, _state = _viewer(qtbot, project, time=1.0)    # l'angle 3 ne commence qu'à 5 s
    viewer.refresh()
    tiles = _visible(viewer)
    assert tiles[2]._state is AngleState.NO_SIGNAL and tiles[3]._state is AngleState.OFFLINE   # noqa: SLF001
    assert tiles[0]._state is AngleState.LIVE and tiles[1]._state is AngleState.LIVE           # les autres continuent
    assert tiles[3]._message() == "MÉDIA HORS LIGNE" and tiles[2]._message() == "PAS DE SIGNAL"  # noqa: SLF001
    i18n.set_language("en")
    viewer.retranslate()
    assert tiles[3]._message() == "MEDIA OFFLINE"                                       # noqa: SLF001


def test_the_tiles_paint_the_frame_the_angle_colour_and_survive_every_state(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4, offline=(3,))
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    image = viewer.grab().toImage()
    tile = _visible(viewer)[0]
    centre = image.pixelColor(viewer.body.mapTo(viewer, tile.geometry().center()))
    assert (centre.red(), centre.green(), centre.blue()) == COLOURS[0]
    assert not image.isNull()


# --- interaction ---------------------------------------------------------------------------------------------------------


def test_clicking_a_tile_requests_that_angle_and_highlights_it_at_once(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    requested = []
    viewer.angle_requested.connect(requested.append)
    tile = _visible(viewer)[3]
    QTest.mouseClick(tile, Qt.LeftButton, pos=QPoint(5, 5))
    assert requested == [3]
    assert [t._active for t in _visible(viewer)] == [False, False, False, True]    # noqa: SLF001
    viewer.refresh()                                                                  # le projet fait ensuite foi
    assert [t._active for t in _visible(viewer)] == [False, True, False, False]    # noqa: SLF001
    QTest.mouseClick(viewer.program_tile, Qt.LeftButton)
    assert requested == [3]                                                           # le programme ne se clique pas


def test_without_a_multicam_segment_under_the_playhead_the_viewer_explains_and_decodes_nothing(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, state = _viewer(qtbot, project, time=500.0)
    viewer.refresh()
    assert not viewer.empty_label.isHidden() and viewer.body.isHidden() and StubFeed.created == []
    state["time"] = 2.0
    viewer.refresh()
    assert viewer.empty_label.isHidden() and not viewer.body.isHidden() and StubFeed.created


# --- flux et ressources ---------------------------------------------------------------------------------------------------


def test_feeds_follow_the_playhead_the_active_angle_first_and_the_others_staggered(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 9)
    viewer, state = _viewer(qtbot, project, time=2.0, playing=True)
    viewer.refresh()
    started = [feed for feed in viewer._pool._feeds.values() if feed.updates]    # noqa: SLF001
    assert 1 <= len(started) <= 1 + 2 + 1       # actif + budget d'angles non actifs (+ NO SIGNAL exclu)
    first = viewer._pool.feed(str(tmp_path / "cam1.mp4"), started[0].profile)    # noqa: SLF001
    assert first.updates and first.updates[-1] == (2.0, True)
    viewer.refresh()
    viewer.refresh()
    assert len([feed for feed in viewer._pool._feeds.values() if feed.updates]) >= 6      # noqa: SLF001
    state["time"] = 3.0
    viewer.refresh()
    assert first.updates[-1][0] == 3.0


def test_hiding_the_viewer_closes_every_feed_and_shutdown_stops_the_timer(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    feeds = list(viewer._pool._feeds.values())          # noqa: SLF001
    assert feeds and viewer._timer.isActive()           # noqa: SLF001
    viewer.hide()
    assert all(feed.closed for feed in feeds) and len(viewer._pool) == 0     # noqa: SLF001
    viewer.show()
    viewer.shutdown()
    assert not viewer._timer.isActive() and len(viewer._pool) == 0           # noqa: SLF001


def test_persistent_lateness_lowers_the_tile_quality_and_a_calm_period_restores_it(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    now = [0.0]
    viewer, _state = _viewer(qtbot, project)
    viewer._clock = lambda: now[0]                      # noqa: SLF001
    StubFeed.lag = 5.0                                  # toutes les tuiles montrent une image très en retard
    for _ in range(60):
        viewer.refresh()
        now[0] += 0.1
    assert viewer._governor.level >= 1                  # noqa: SLF001
    degraded = viewer._governor.level                   # noqa: SLF001
    profiles = {profile for (_path, profile) in viewer._pool._feeds}     # noqa: SLF001
    assert any(profile.name in {"medium", "low", "minimal"} for profile in profiles)
    StubFeed.lag = 0.0
    for _ in range(200):
        viewer.refresh()
        now[0] += 0.1
    assert viewer._governor.level < degraded            # noqa: SLF001


def test_the_viewer_labels_follow_the_language(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    assert viewer.program_tile.toolTip() == "" and viewer.title.text() == "MULTICAM"
    i18n.set_language("en")
    viewer.retranslate()
    assert viewer.page_next.toolTip() == "Next page"
    i18n.set_language("es")
    viewer.retranslate()
    assert viewer.page_next.toolTip() == "Página siguiente"


# --- proxys recommandés, menu d'une tuile ---------------------------------------------------------------------------------------


def test_four_or_more_angles_reading_their_originals_recommend_proxies(qtbot, tmp_path):
    project, _segment = _project(tmp_path, 4)
    viewer, state = _viewer(qtbot, project)
    state["time"] = 6.0                                    # les quatre angles sont actifs (la caméra 3 démarre à 5 s)
    viewer.refresh()
    assert not viewer.proxy_notice.isHidden() and not viewer.proxy_button.isHidden()
    requested = []
    viewer.proxies_requested.connect(lambda: requested.append(1))
    viewer.proxy_button.click()
    assert requested == [1]
    viewer._resolve = lambda path: path.replace(".mp4", ".proxy.mp4")             # noqa: SLF001 - des proxys existent
    viewer.refresh()
    assert viewer.proxy_notice.isHidden()
    small, _segment = _project(tmp_path, 2)
    viewer2, _state = _viewer(qtbot, small)
    viewer2.refresh()
    assert viewer2.proxy_notice.isHidden()                  # deux angles : inutile de recommander


def test_the_tile_menu_opens_the_source_on_that_angle(qtbot, tmp_path, monkeypatch):
    project, _segment = _project(tmp_path, 4)
    viewer, _state = _viewer(qtbot, project)
    viewer.refresh()
    asked = []
    viewer.open_source_requested.connect(asked.append)
    monkeypatch.setattr(viewer, "_run_menu", lambda menu, _position: menu.actions()[0])
    viewer._on_tile_menu(2, QPoint(0, 0))                 # noqa: SLF001
    assert asked == [2]
    monkeypatch.setattr(viewer, "_run_menu", lambda menu, _position: None)
    viewer._on_tile_menu(1, QPoint(0, 0))                 # noqa: SLF001 - menu fermé sans choix
    assert asked == [2]
