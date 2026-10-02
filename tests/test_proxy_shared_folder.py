"""Deux instances de l'application peuvent partager le dossier des proxies sans se marcher dessus.

Le fichier partiel avait un nom déterministe (``<clé>.partial.mp4``) : deux générations du même proxy écrivaient
dans le même fichier, et le nettoyage du démarrage d'une instance supprimait le fichier partiel, en cours
d'écriture, de l'autre.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from core.cache_keys import SignatureMemo
from core.proxy_manager import ProxyManager, ProxyState, RunResult


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "media" / "clip.mp4"
    path.parent.mkdir()
    path.write_bytes(b"S" * 1000)
    return str(path)


def _manager(folder: Path, runner) -> ProxyManager:
    return ProxyManager(folder, ffmpeg_command=lambda: ["ffmpeg-fake"], runner=runner,
                        memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)


def _wait(condition, timeout=8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


def test_two_instances_generating_the_same_proxy_write_to_different_partial_files(tmp_path, source):
    folder = tmp_path / "proxies"
    both_running = threading.Barrier(2, timeout=5)
    paths: list[str] = []

    def runner(command, cancel, on_progress, on_start):
        paths.append(command[-1])
        on_start(4242)
        Path(command[-1]).write_bytes(b"P" * 64)
        both_running.wait()                                  # les deux FFmpeg tournent en même temps
        return RunResult(0)

    first, second = _manager(folder, runner), _manager(folder, runner)
    try:
        first.request(source, duration=2.0)
        second.request(source, duration=2.0)
        assert _wait(lambda: first.info(source).state is ProxyState.READY
                     and second.info(source).state is ProxyState.READY)
    finally:
        first.shutdown()
        second.shutdown()

    assert len(paths) == 2 and paths[0] != paths[1]
    assert all(".partial." in os.path.basename(path) for path in paths)
    assert first.info(source).proxy_path == second.info(source).proxy_path      # même proxy final
    assert Path(first.info(source).proxy_path).is_file()
    assert not [name for name in os.listdir(folder) if ".partial." in name]     # aucun partiel ne traîne


def test_a_recent_partial_file_of_another_instance_survives_the_startup_cleanup(tmp_path, source):
    folder = tmp_path / "proxies"
    folder.mkdir()
    running_elsewhere = folder / "proxy-abc.partial.4242-deadbeef.mp4"
    running_elsewhere.write_bytes(b"en cours")                 # une autre instance écrit en ce moment
    abandoned = folder / "proxy-def.partial.1-cafecafe.mp4"
    abandoned.write_bytes(b"abandoned")
    long_ago = time.time() - 3600
    os.utime(abandoned, (long_ago, long_ago))

    manager = _manager(folder, lambda *args: RunResult(0))
    try:
        assert manager.cleanup_orphans() == 1
    finally:
        manager.shutdown()

    assert running_elsewhere.exists() and not abandoned.exists()
