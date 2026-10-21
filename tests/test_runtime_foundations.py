"""Fondations de performance : cache, file, profils, aperçu, autosave."""

from __future__ import annotations

import json
import os

from core.autosave import AutosaveCoordinator, autosave_is_newer, discard_autosave
from core.cache_store import MemoryCache
from core.preview_quality import PreviewQualityController, output_size, resolve_divisor
from core.project_factory import create_default_project
from core.project_io import load_project
from core.runtime_profile import (
    MachineResources,
    resolve_profile,
)
from core.task_queue import PRIORITY_BACKGROUND, PRIORITY_VISIBLE, TaskQueue


def test_cache_evicts_least_recently_used_and_a_namespace():
    cache = MemoryCache(max_bytes=10, max_entries=10)
    cache.put("a", "alpha", size_bytes=4, namespace="project")
    cache.put("b", "beta", size_bytes=4, namespace="global")
    cache.get("a")
    cache.put("c", "gamma", size_bytes=4, namespace="project")
    assert cache.get("b") is None
    assert cache.get("a") == "alpha"
    cache.clear_namespace("project")
    assert cache.get("a") is None
    assert cache.stats().bytes == 0


def test_task_queue_prefers_visible_work_and_drops_a_closed_session():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("far", lambda _token: ran.append("far"), priority=PRIORITY_BACKGROUND, session_id=1)
    queue.submit("near", lambda _token: ran.append("near"), priority=PRIORITY_VISIBLE, session_id=1)
    queue.submit("near", lambda token: ran.append("replaced" if not token.cancelled else "no"), priority=PRIORITY_VISIBLE, session_id=1)
    queue.cancel_session(1)
    assert queue.pump(10) == 0
    queue.submit("keep", lambda _token: ran.append("keep"), session_id=2)
    assert queue.pump(10) == 1
    assert ran == ["keep"]


def test_explicit_profile_is_kept_and_auto_steps_down_on_a_heavy_project():
    small = MachineResources(logical_cpus=8, memory_bytes=8 * 1024 ** 3)
    assert resolve_profile("high", small, "heavy").name == "high"
    assert resolve_profile("auto", small, "heavy").name == "low"
    assert resolve_profile("auto", MachineResources(logical_cpus=2, memory_bytes=4 * 1024 ** 3)).name == "low"


def test_preview_auto_follows_the_profile_and_does_not_invent_a_frame_drop():
    profile = resolve_profile("low", MachineResources(logical_cpus=4, memory_bytes=32 * 1024 ** 3))
    assert resolve_divisor("auto", profile) == profile.preview_divisor
    assert resolve_divisor("full", profile) == 1
    assert output_size(1920, 1080, 4) == (480, 270)
    assert PreviewQualityController().suggest(50.0) is None


def test_autosave_writes_a_newer_sidecar_that_reloads(tmp_path):
    project = create_default_project()
    target = tmp_path / "film.kut"
    target.write_text("{}", encoding="utf-8")
    os.utime(target, (1_700_000_000, 1_700_000_000))
    coordinator = AutosaveCoordinator()
    project.name = "Récupéré"
    coordinator.submit(project, str(target))
    coordinator.close()

    sidecar = tmp_path / "film.kut.autosave"
    assert sidecar.is_file()
    assert autosave_is_newer(str(target))
    loaded = load_project(str(sidecar))
    assert loaded.name == "Récupéré"
    discard_autosave(str(target))
    assert not sidecar.exists()
    payload = json.loads((tmp_path / "film.kut").read_text(encoding="utf-8"))
    assert payload == {}
