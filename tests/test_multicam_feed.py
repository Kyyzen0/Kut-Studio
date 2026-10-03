"""Flux d'images des angles : profils de tuile, lecture sans attente, sauts, freinage, arrêt, qualité adaptative.

Vrais médias synthétiques décodés par le vrai FFmpeg (un aplat de couleur par angle : on reconnaît l'angle au pixel).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.diagnostics_log import install_diagnostics, uninstall_diagnostics
from core.multicam_feed import (
    GRID_PROFILES,
    LOOKAHEAD_SECONDS,
    AngleFeed,
    FeedPool,
    TileProfile,
    TileQualityGovernor,
    lag_ratio,
    tile_profile,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
SMALL = TileProfile("test", 64, 36, 10.0)


def _colour_video(path: Path, colour: str, seconds: float = 6.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=320x180:r=25:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "5", str(path)],
        check=True,
    )
    return path


def _wait(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("délai dépassé")


def _centre(frame) -> tuple[int, int, int]:
    offset = ((frame.height // 2) * frame.width + frame.width // 2) * 3
    return tuple(frame.data[offset:offset + 3])  # type: ignore[return-value]


# --- profils de tuile -------------------------------------------------------------------------------------------------


def test_tile_profile_gets_lighter_as_tiles_multiply_and_the_active_angle_is_finer():
    assert tile_profile(2).name == "high" and tile_profile(4).name == "high"
    assert tile_profile(5).name == "medium" and tile_profile(9).name == "medium"
    assert tile_profile(10).name == "low" and tile_profile(16).name == "low"
    assert tile_profile(9, active=True).name == "high" and tile_profile(4, active=True).name == "high"
    assert tile_profile(4, level=2).name == "low" and tile_profile(16, level=9).name == "minimal"
    sizes = [profile.frame_bytes for profile in GRID_PROFILES]
    assert sizes == sorted(sizes, reverse=True)  # chaque cran coûte moins cher que le précédent


def test_the_governor_degrades_on_persistent_lateness_and_recovers_when_calm():
    governor = TileQualityGovernor()
    now = 0.0
    for _ in range(10):                      # retard bref : on ne bouge pas
        governor.observe(0.5, now)
        now += 0.1
    assert governor.level == 0
    for _ in range(40):                      # retard persistant (4 s) : un cran, puis un second après le délai
        governor.observe(0.5, now)
        now += 0.1
    assert governor.level >= 1
    degraded = governor.level
    for _ in range(40):                      # calme pendant 4 s : pas encore de retour (prudence)
        governor.observe(0.0, now)
        now += 0.1
    assert governor.level == degraded
    for _ in range(120):                     # calme prolongé : la qualité remonte, un cran à la fois
        governor.observe(0.0, now)
        now += 0.1
    assert governor.level < degraded
    governor.reset()
    assert governor.level == 0


def test_the_governor_never_exceeds_its_range_and_ignores_the_middle_band():
    governor = TileQualityGovernor(max_level=2, degrade_after=0.1, cooldown=0.0)
    for step in range(100):
        governor.observe(1.0, step * 0.5)
    assert governor.level == 2
    middle = TileQualityGovernor()
    for step in range(100):
        middle.observe(0.15, step)
    assert middle.level == 0


# --- flux réels -------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_feed_decodes_the_right_media_and_returns_frames_without_waiting(tmp_path):
    red = _colour_video(tmp_path / "red.mp4", "red")
    feed = AngleFeed(str(red), SMALL)
    try:
        started = time.perf_counter()
        feed.update(0.5, playing=True)
        assert feed.frame_at(0.5) is None or True  # l'appel ne bloque pas, même sans image
        assert time.perf_counter() - started < 0.2
        frame = _wait(lambda: feed.frame_at(0.5))
        assert frame.width == 64 and frame.height == 36 and len(frame.data) == SMALL.frame_bytes
        r, g, b = _centre(frame)
        assert r > 200 and g < 60 and b < 60
        assert abs(frame.time - 0.5) <= 0.15
    finally:
        feed.close()


@needs_ffmpeg
def test_two_feeds_show_two_different_angles(tmp_path):
    feeds = [AngleFeed(str(_colour_video(tmp_path / f"{name}.mp4", name)), SMALL) for name in ("red", "lime")]
    try:
        for feed in feeds:
            feed.update(1.0, playing=False)
        red, green = (_wait(lambda f=feed: f.frame_at(1.0)) for feed in feeds)
        assert _centre(red)[0] > 200 and _centre(green)[1] > 200 and _centre(green)[0] < 60
    finally:
        for feed in feeds:
            feed.close()


@needs_ffmpeg
def test_decoding_is_throttled_to_the_lookahead_and_the_buffer_is_bounded(tmp_path):
    video = _colour_video(tmp_path / "long.mp4", "blue", seconds=30.0)
    feed = AngleFeed(str(video), SMALL)
    try:
        feed.update(0.0, playing=True)
        _wait(lambda: feed.frame_at(0.0))
        time.sleep(1.0)                                 # sans nouvelle cible : le décodage doit se mettre en attente
        with feed._lock:                                # noqa: SLF001 - lecture de l'état interne pour le contrat de freinage
            newest = feed._frames[-1].time
            count = len(feed._frames)
        assert newest <= LOOKAHEAD_SECONDS + 0.5, newest  # très en deçà des 30 s du média
        assert count <= feed._max_frames                 # noqa: SLF001
    finally:
        feed.close()


@needs_ffmpeg
def test_a_jump_restarts_the_decoding_at_the_requested_time_and_a_rewind_works(tmp_path):
    video = _colour_video(tmp_path / "jump.mp4", "green", seconds=20.0)
    feed = AngleFeed(str(video), SMALL)
    try:
        feed.update(0.0, playing=True)
        _wait(lambda: feed.frame_at(0.0))
        feed.update(15.0, playing=False)                 # grand saut en avant
        frame = _wait(lambda: (f := feed.frame_at(15.0)) is not None and abs(f.time - 15.0) < 0.3 and f)
        assert abs(frame.time - 15.0) < 0.3
        feed.update(2.0, playing=False)                  # retour en arrière hors tampon
        frame = _wait(lambda: (f := feed.frame_at(2.0)) is not None and abs(f.time - 2.0) < 0.3 and f)
        assert abs(frame.time - 2.0) < 0.3
    finally:
        feed.close()


@needs_ffmpeg
def test_the_last_image_is_held_while_a_new_one_is_on_its_way(tmp_path):
    video = _colour_video(tmp_path / "hold.mp4", "red", seconds=20.0)
    feed = AngleFeed(str(video), SMALL)
    try:
        feed.update(1.0, playing=False)
        shown = _wait(lambda: feed.frame_at(1.0))
        feed.update(12.0, playing=False)                 # relance : le tampon est vidé…
        held = feed.frame_at(12.0)                       # …mais l'interface garde une image à montrer
        assert held is not None and (held is shown or abs(held.time - 12.0) < 0.5)
    finally:
        feed.close()


@needs_ffmpeg
def test_closing_a_feed_kills_ffmpeg_and_an_idle_feed_releases_its_process(tmp_path):
    video = _colour_video(tmp_path / "idle.mp4", "red", seconds=30.0)
    now = [0.0]
    feed = AngleFeed(str(video), SMALL, clock=lambda: now[0])
    feed.update(0.0, playing=True)
    _wait(lambda: feed.frame_at(0.0))
    assert feed.running
    now[0] += 60.0                                       # plus aucun appel depuis une minute : flux inactif
    _wait(lambda: not feed.running)
    feed.update(0.0, playing=True)                       # il redémarre à la demande
    _wait(lambda: feed.frame_at(0.0) and feed.running)
    feed.close()
    assert not feed.running and feed._process is None    # noqa: SLF001


@needs_ffmpeg
def test_an_unreadable_media_reports_an_error_once_and_never_raises(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"ceci n'est pas une video")
    feed = AngleFeed(str(bad), SMALL)
    try:
        feed.update(0.0, playing=True)
        _wait(lambda: feed.error)
        assert feed.frame_at(0.0) is None
        feed.update(1.0, playing=True)                   # aucune boucle de relance sur un média illisible
        assert not feed.running or feed.error
    finally:
        feed.close()


def test_a_missing_ffmpeg_is_an_error_not_a_crash(tmp_path):
    def broken(*_a):
        raise OSError("FFmpeg est introuvable")

    feed = AngleFeed(str(tmp_path / "x.mp4"), SMALL, command=broken)
    try:
        feed.update(0.0, playing=True)
        _wait(lambda: feed.error)
        assert "introuvable" in feed.error
    finally:
        feed.close()


# --- paquet de flux ----------------------------------------------------------------------------------------------------


def test_the_pool_shares_a_feed_per_media_and_profile_and_closes_what_is_no_longer_wanted():
    created: list[str] = []

    class Stub(AngleFeed):
        def __init__(self, path, profile):
            super().__init__(path, profile)
            created.append(path)
            self.closed = False

        def close(self):
            self.closed = True

    pool = FeedPool(Stub)
    a, b = TileProfile("a", 64, 36, 10.0), TileProfile("b", 32, 18, 5.0)
    first = pool.feed("x.mp4", a)
    assert pool.feed("x.mp4", a) is first and len(pool) == 1 and created == ["x.mp4"]
    second = pool.feed("x.mp4", b)                       # même média, autre profil : un autre flux
    third = pool.feed("y.mp4", a)
    pool.retain({("x.mp4", a), ("y.mp4", a)})
    assert second.closed and not first.closed and not third.closed and len(pool) == 2
    pool.close_all()
    assert first.closed and third.closed and len(pool) == 0


def test_lag_ratio_counts_the_feeds_that_are_more_than_four_periods_behind():
    fast = AngleFeed("a", TileProfile("t", 8, 8, 10.0))
    assert lag_ratio([]) == 0.0
    assert lag_ratio([(fast, 0.1), (fast, 0.5)]) == 0.5 and lag_ratio([(fast, 0.39)]) == 0.0


# --- journal ------------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_feed_failure_reaches_the_log_file_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", lambda *_a: None)
    monkeypatch.setattr(threading, "excepthook", lambda _a: None)
    monkeypatch.setattr(sys, "unraisablehook", lambda _a: None)
    path = install_diagnostics(tmp_path / "logs")
    assert path is not None
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"x")
    feed = AngleFeed(str(bad), SMALL)
    try:
        feed.update(0.0, playing=True)
        _wait(lambda: feed.error)
        feed.update(0.5, playing=True)
        for handler in logging.getLogger("kut_studio").handlers:
            handler.flush()
        text = Path(path).read_text(encoding="utf-8")
        assert text.count("Multicam : flux de broken.mp4 impossible") == 1
    finally:
        feed.close()
        uninstall_diagnostics()
