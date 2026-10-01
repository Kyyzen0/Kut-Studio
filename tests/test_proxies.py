"""Proxies média : profils, états, génération, fallback, annulation, robustesse."""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import fields
from pathlib import Path

import pytest

from core.cache_keys import SignatureMemo
from core.project_io import load_project, project_payload, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.proxy_manager import ProxyManager, ProxyState, RunResult, run_ffmpeg
from core.proxy_profiles import (
    PROXY_CODECS,
    ProxyCodec,
    ProxyProfile,
    builtin_profiles,
    get_profile,
    register_codec,
    register_profile,
)
from core.render_plan import build_render_plan

ROOT = Path(__file__).resolve().parent.parent
FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.py"


# --- helpers ------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_profile_and_codec_registries():
    """Les tests enregistrent des profils / codecs : jamais de fuite vers les autres tests."""
    from core import proxy_profiles

    profiles, codecs = dict(proxy_profiles._PROFILES), dict(proxy_profiles.PROXY_CODECS)
    yield
    proxy_profiles._PROFILES.clear()
    proxy_profiles._PROFILES.update(profiles)
    proxy_profiles.PROXY_CODECS.clear()
    proxy_profiles.PROXY_CODECS.update(codecs)


def _wait(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


def _ok_runner(payload=b"P" * 64, on_call=None):
    def runner(command, cancel, on_progress, on_start):
        if on_call:
            on_call(command)
        on_start(4242)
        on_progress(1.0)
        Path(command[-1]).write_bytes(payload)
        return RunResult(0)

    return runner


def _blocking_runner(started: threading.Event):
    def runner(command, cancel, on_progress, on_start):
        on_start(4243)
        Path(command[-1]).write_bytes(b"partial")
        started.set()
        while not cancel.is_set():
            time.sleep(0.005)
        return RunResult(-15, "", cancelled=True)

    return runner


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "media" / "clip.mp4"
    path.parent.mkdir()
    path.write_bytes(b"S" * 1000)
    return str(path)


def _manager(tmp_path, runner=None, **kwargs):
    kwargs.setdefault("memo", SignatureMemo(ttl=0.0))
    kwargs.setdefault("disk_ttl", 0.0)     # les tests relisent le disque à chaque appel
    return ProxyManager(
        tmp_path / "proxies",
        ffmpeg_command=lambda: ["ffmpeg-fake"],
        runner=runner or _ok_runner(),
        **kwargs,
    )


@pytest.fixture
def manager(tmp_path):
    instance = _manager(tmp_path)
    yield instance
    instance.shutdown()


def _generate(manager, path, **kwargs):
    profile_id = kwargs.get("profile_id")
    manager.request(path, duration=2.0, **kwargs)
    assert _wait(lambda: manager.info(path, profile_id).state in (ProxyState.READY, ProxyState.ERROR))
    return manager.info(path, profile_id)


# --- profils ---------------------------------------------------------------------------------------


def test_registered_profiles_are_available_but_not_builtin():
    from core.proxy_profiles import available_profiles

    register_profile(ProxyProfile("extra", "proxy.profile.low", 200))
    assert "extra" in {p.id for p in available_profiles()}
    assert [p.id for p in builtin_profiles()] == ["low", "medium", "high"]
    assert [p.max_height for p in available_profiles()] == sorted(p.max_height for p in available_profiles())


def test_builtin_profiles_cover_low_medium_high_in_ascending_resolution():
    profiles = builtin_profiles()
    assert [p.id for p in profiles] == ["low", "medium", "high"]
    assert [p.max_height for p in profiles] == sorted(p.max_height for p in profiles)
    assert get_profile("nope").id == "medium" and get_profile(None).id == "medium"


def test_h264_profile_builds_scale_codec_gop_and_audio_arguments():
    args = get_profile("medium").ffmpeg_output_args()
    joined = " ".join(args)
    assert "scale=-2:'min(ih,720)'" in joined          # jamais d'agrandissement
    assert "-c:v libx264" in joined and "-crf 26" in joined
    assert "-g 12" in joined                           # GOP court : scrubbing fluide
    assert "-c:a aac" in joined and "-b:a 96k" in joined
    assert "-movflags +faststart" in joined and "-pix_fmt yuv420p" in joined


def test_proxy_codec_is_not_tied_to_one_codec():
    prores = ProxyProfile("pr", "proxy.profile.medium", 540, codec="prores_proxy", quality=0)
    joined = " ".join(prores.ffmpeg_output_args())
    assert "-c:v prores_ks" in joined and "-profile:v 0" in joined and "yuv422p10le" in joined
    assert prores.extension == "mov" and "-movflags" not in joined
    register_codec(ProxyCodec("copy_h264", "h264", "mp4", "yuv420p", lambda p: ["-c:v", "libx264"]))
    assert "copy_h264" in PROXY_CODECS
    PROXY_CODECS.pop("copy_h264")


def test_a_muted_profile_drops_audio_and_says_so():
    muted = ProxyProfile("mute", "proxy.profile.low", 360, audio_codec=None)
    assert "-an" in muted.ffmpeg_output_args() and not muted.keeps_audio


def test_fingerprint_tracks_what_changes_the_file_but_not_the_display_name():
    base = ProxyProfile("x", "proxy.profile.low", 480, quality=30)
    assert base.fingerprint() == ProxyProfile("x", "proxy.profile.medium", 480, quality=30).fingerprint()
    assert base.fingerprint() != ProxyProfile("x", "proxy.profile.low", 480, quality=28).fingerprint()
    assert base.fingerprint() != ProxyProfile("x", "proxy.profile.low", 540, quality=30).fingerprint()
    assert base.fingerprint() != ProxyProfile("x", "proxy.profile.low", 480, quality=30, gop=24).fingerprint()


def test_profiles_are_validated_and_extensible():
    with pytest.raises(ValueError):
        ProxyProfile("bad", "k", 480, codec="vp9")
    with pytest.raises(ValueError):
        ProxyProfile("bad", "k", 1)
    with pytest.raises(ValueError):
        ProxyProfile("bad", "k", 480, audio_codec="mp3")
    register_profile(ProxyProfile("ultra", "proxy.profile.low", 270, quality=34))
    assert get_profile("ultra").max_height == 270


def test_hardware_request_is_accepted_and_falls_back_on_the_cpu_encoder():
    profile = ProxyProfile("hw", "proxy.profile.low", 480, hardware="nvenc")
    assert profile.hardware == "nvenc"
    assert "libx264" in profile.ffmpeg_output_args()   # repli CPU tant que rien n'est implémenté


# --- génération et états ---------------------------------------------------------------------------------


def test_a_proxy_goes_through_pending_generating_ready_and_leaves_no_partial_file(tmp_path, source):
    manager = _manager(tmp_path)
    states = []
    manager.subscribe(lambda _path, info: states.append(info.state))
    info = _generate(manager, source)
    assert info.state is ProxyState.READY and info.valid and info.size_bytes == 64
    assert info.proxy_path and Path(info.proxy_path).is_file() and info.created_at
    # L'ordre PENDING/GENERATING n'est pas garanti : le worker peut notifier avant le demandeur.
    assert ProxyState.PENDING in states and ProxyState.GENERATING in states
    assert states[-1] is ProxyState.READY
    files = sorted(p.name for p in (tmp_path / "proxies").iterdir())
    assert len(files) == 2 and any(n.endswith(".json") for n in files)
    assert not any(".partial." in n for n in files)
    manager.shutdown()


def test_unknown_media_has_no_proxy_and_a_missing_source_is_reported(manager, tmp_path):
    ghost = str(tmp_path / "ghost.mp4")
    assert manager.info(ghost).state is ProxyState.NONE
    assert manager.request(ghost) is ProxyState.ERROR
    assert "introuvable" in manager.info(ghost).error


def test_progress_is_reported_during_generation(tmp_path, source):
    seen = []

    def runner(command, cancel, on_progress, on_start):
        for seconds in (0.5, 1.0, 1.5):
            on_progress(seconds)
        Path(command[-1]).write_bytes(b"x")
        return RunResult(0)

    manager = _manager(tmp_path, runner)
    manager.subscribe(lambda _p, info: seen.append(info.progress))
    _generate(manager, source)
    assert seen == sorted(seen) and max(seen[:-1]) == 75 and seen[-1] == 100   # 100 % seulement une fois prêt
    manager.shutdown()


def test_requesting_a_ready_proxy_again_does_nothing_unless_forced(tmp_path, source):
    calls = []
    manager = _manager(tmp_path, _ok_runner(on_call=calls.append))
    _generate(manager, source)
    assert manager.request(source) is ProxyState.READY and len(calls) == 1
    manager.request(source, force=True)
    assert _wait(lambda: len(calls) == 2)
    manager.shutdown()


def test_generation_is_sequential_with_one_worker_and_keeps_request_order(tmp_path):
    medias = []
    for name in "abc":
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(name.encode() * 10)
        medias.append(str(path))
    order = []
    manager = _manager(tmp_path, _ok_runner(on_call=lambda cmd: order.append(Path(cmd[cmd.index("-i") + 1]).name)))
    result = manager.request_many([(m, 2.0) for m in medias])
    assert set(result.values()) <= {ProxyState.PENDING, ProxyState.GENERATING}
    assert _wait(lambda: all(manager.info(m).state is ProxyState.READY for m in medias))
    assert order == ["a.mp4", "b.mp4", "c.mp4"]
    manager.shutdown()


# --- choix proxy / original --------------------------------------------------------------------------------


def test_preview_uses_the_proxy_and_everything_else_uses_the_original(manager, source):
    info = _generate(manager, source)
    assert manager.resolve(source) == info.proxy_path
    assert manager.preview_resolver()(source) == info.proxy_path
    assert manager.resolve_for_export(source) == source                      # export : TOUJOURS l'original
    assert manager.resolve_for_export(source, allow_proxy_for_debug=True) == info.proxy_path
    manager.set_enabled(False)
    assert manager.resolve(source) == source and not manager.enabled        # coupé globalement
    manager.set_enabled(True)
    assert manager.resolve(source) == info.proxy_path


def test_a_proxy_without_audio_is_not_used_when_audio_is_needed(tmp_path, source):
    register_profile(ProxyProfile("silent", "proxy.profile.low", 360, audio_codec=None))
    manager = _manager(tmp_path, profile_id="silent")
    info = _generate(manager, source)
    assert manager.resolve(source) == info.proxy_path
    assert manager.resolve(source, need_audio=True) == source       # l'audio reste celui de l'original
    manager.shutdown()


def test_unknown_or_empty_paths_resolve_to_themselves(manager):
    assert manager.resolve("") == "" and manager.resolve("/nulle/part.mp4") == "/nulle/part.mp4"


def test_a_lighter_ready_proxy_is_preferred_when_the_preview_quality_is_reduced(tmp_path, source):
    manager = _manager(tmp_path, profile_id="high")
    low = _generate(manager, source, profile_id="low")
    high = _generate(manager, source, profile_id="high")
    assert manager.resolve(source, divisor=1) == high.proxy_path
    assert manager.resolve(source, divisor=2) == high.proxy_path    # 1080/2 = 540 : le 480p est trop petit
    assert manager.resolve(source, divisor=4) == low.proxy_path     # 1080/4 = 270 : le 480p suffit
    medium = _generate(manager, source, profile_id="medium")
    assert manager.resolve(source, divisor=2) == medium.proxy_path  # le plus léger qui reste assez net
    manager.set_enabled(False)
    assert manager.resolve(source, divisor=4) == source
    manager.shutdown()


def test_export_plans_never_contain_proxies(manager, source):
    info = _generate(manager, source)
    project = Project(
        name="p", media_assets=[MediaAsset(id="a", path=source, name="a", duration=5, width=1920,
                                           height=1080, fps=25.0, media_type="video", has_audio=True)],
        tracks=[Track(id="V1", name="V1", type="video",
                      clips=[Clip(id="c", asset_id="a", track_id="V1", timeline_start=0,
                                  source_in=0, source_out=2.0)])],
    )
    plan = build_render_plan(project)
    assert plan.video_layers[0].source_path == source != info.proxy_path
    assert plan.audio_layers[0].source_path == source


# --- fallback : proxy absent, supprimé, obsolète, incomplet ------------------------------------------------


def test_manually_deleting_the_proxy_falls_back_to_the_original_without_error(manager, source):
    info = _generate(manager, source)
    Path(info.proxy_path).unlink()
    assert manager.resolve(source) == source
    assert manager.info(source).state is ProxyState.NONE


def test_deleting_the_whole_proxy_folder_is_harmless(manager, source, tmp_path):
    _generate(manager, source)
    shutil.rmtree(tmp_path / "proxies")
    assert manager.resolve(source) == source
    assert manager.info(source).state is ProxyState.NONE
    assert manager.usage_bytes() == 0 and manager.entries() == []
    assert manager.cleanup_orphans() == 0


def test_a_modified_source_makes_the_proxy_stale_and_unused_until_regenerated(manager, source):
    first = _generate(manager, source)
    Path(source).write_bytes(b"S" * 2000)                            # la source a changé
    stale = manager.info(source)
    assert stale.state is ProxyState.STALE and not stale.valid
    assert manager.resolve(source) == source
    manager.regenerate(source, duration=2.0)
    assert _wait(lambda: manager.info(source).state is ProxyState.READY)
    assert manager.info(source).source_signature != first.source_signature
    assert manager.resolve(source) == manager.info(source).proxy_path


def test_an_offline_original_keeps_its_proxy_usable_for_preview(manager, source):
    info = _generate(manager, source)
    Path(source).unlink()                                            # média hors ligne
    offline = manager.info(source)
    assert offline.state is ProxyState.READY and offline.source_missing
    assert manager.resolve(source) == info.proxy_path                # on peut encore l'afficher


def test_a_moved_original_has_no_proxy_at_its_new_location(manager, source, tmp_path):
    _generate(manager, source)
    moved = tmp_path / "ailleurs.mp4"
    shutil.move(source, moved)
    assert manager.info(str(moved)).state is ProxyState.NONE
    assert manager.resolve(str(moved)) == str(moved)


def test_a_project_opened_on_another_machine_simply_has_no_proxies(tmp_path, source):
    here = _manager(tmp_path)
    _generate(here, source)
    there = ProxyManager(tmp_path / "autre-machine", memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)
    assert there.resolve(source) == source and there.info(source).state is ProxyState.NONE
    here.shutdown()


def test_a_truncated_proxy_is_never_used(manager, source):
    info = _generate(manager, source)
    with open(info.proxy_path, "r+b") as handle:
        handle.truncate(10)                                          # copie interrompue
    assert manager.info(source).state is ProxyState.NONE
    assert manager.resolve(source) == source


def test_a_proxy_with_a_mismatching_profile_fingerprint_is_not_ready(manager, source, tmp_path):
    info = _generate(manager, source)
    sidecar = Path(info.proxy_path).with_suffix(".json")
    marker = json.loads(sidecar.read_text(encoding="utf-8"))
    marker["fingerprint"] = "autre"
    sidecar.write_text(json.dumps(marker), encoding="utf-8")
    assert manager.info(source).state is ProxyState.NONE
    sidecar.write_text("pas du json", encoding="utf-8")              # marqueur corrompu
    assert manager.info(source).state is ProxyState.NONE


def test_changing_a_profile_setting_makes_old_proxies_unavailable(tmp_path, source):
    register_profile(ProxyProfile("tunable", "proxy.profile.low", 480, quality=30))
    manager = _manager(tmp_path, profile_id="tunable")
    _generate(manager, source)
    assert manager.info(source).state is ProxyState.READY
    register_profile(ProxyProfile("tunable", "proxy.profile.low", 480, quality=24))   # réglage modifié
    manager.set_profile("tunable")
    assert manager.info(source).state is ProxyState.NONE
    manager.shutdown()


def test_orphans_from_a_crash_are_cleaned_but_valid_proxies_are_kept(manager, source, tmp_path):
    info = _generate(manager, source)
    folder = tmp_path / "proxies"
    (folder / "proxy-dead.partial.mp4").write_bytes(b"x" * 10)       # FFmpeg tué en plein travail
    (folder / "proxy-nomarker.mp4").write_bytes(b"x" * 10)           # promu mais sans marqueur
    (folder / "proxy-half.json.tmp").write_bytes(b"{")
    (folder / "autre-fichier.txt").write_text("à moi", encoding="utf-8")
    assert manager.cleanup_orphans() == 3
    assert Path(info.proxy_path).is_file() and (folder / "autre-fichier.txt").exists()


# --- erreurs ---------------------------------------------------------------------------------------------


def test_ffmpeg_failure_is_an_error_state_with_a_readable_message_and_no_leftovers(tmp_path, source):
    def runner(command, cancel, on_progress, on_start):
        Path(command[-1]).write_bytes(b"junk")
        return RunResult(1, "Invalid data found when processing input")

    manager = _manager(tmp_path, runner)
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and "Invalid data" in info.error
    assert manager.resolve(source) == source
    assert list((tmp_path / "proxies").iterdir()) == []
    manager.shutdown()


def test_ffmpeg_unavailable_is_an_error_not_a_crash_and_recovers_later(tmp_path, source):
    available = {"ok": False}

    def command():
        if not available["ok"]:
            raise FileNotFoundError("FFmpeg est introuvable : génération de proxies impossible.")
        return ["ffmpeg-fake"]

    manager = ProxyManager(tmp_path / "proxies", ffmpeg_command=command, runner=_ok_runner(),
                           memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and "FFmpeg" in info.error
    assert manager.resolve(source) == source
    available["ok"] = True
    assert _generate(manager, source, force=True).state is ProxyState.READY
    manager.shutdown()


def test_a_full_disk_reported_by_ffmpeg_gives_a_clear_message(tmp_path, source):
    manager = _manager(tmp_path, lambda *a: RunResult(1, "Error writing: No space left on device"))
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and "Espace disque insuffisant" in info.error
    manager.shutdown()


def test_a_full_disk_raised_as_an_os_error_is_handled(tmp_path, source):
    def runner(*_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    manager = _manager(tmp_path, runner)
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and "Espace disque insuffisant" in info.error
    manager.shutdown()


def test_a_failure_while_writing_the_marker_leaves_no_half_proxy(tmp_path, source, monkeypatch):
    manager = _manager(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(manager, "_write_sidecar", broken)
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR
    assert list((tmp_path / "proxies").iterdir()) == []
    manager.shutdown()


def test_an_unwritable_cache_folder_is_reported_not_raised(tmp_path, source):
    blocker = tmp_path / "cache-is-a-file"
    blocker.write_text("je ne suis pas un dossier", encoding="utf-8")
    manager = ProxyManager(blocker, ffmpeg_command=lambda: ["ffmpeg-fake"], runner=_ok_runner(),
                           memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and info.error
    assert manager.resolve(source) == source and manager.usage_bytes() == 0
    manager.shutdown()


def test_ffmpeg_producing_nothing_is_an_error(tmp_path, source):
    manager = _manager(tmp_path, lambda *a: RunResult(0, ""))
    info = _generate(manager, source)
    assert info.state is ProxyState.ERROR and "aucun fichier" in info.error
    manager.shutdown()


def test_an_error_is_cleared_by_a_new_successful_request(tmp_path, source):
    outcome = {"fail": True}

    def runner(command, cancel, on_progress, on_start):
        if outcome["fail"]:
            return RunResult(1, "boom")
        Path(command[-1]).write_bytes(b"ok")
        return RunResult(0)

    manager = _manager(tmp_path, runner)
    assert _generate(manager, source).state is ProxyState.ERROR
    outcome["fail"] = False
    assert _generate(manager, source, force=True).state is ProxyState.READY
    manager.shutdown()


# --- annulation, suppression, fermeture ----------------------------------------------------------------------------


def test_cancelling_a_running_generation_leaves_nothing_behind(tmp_path, source):
    started = threading.Event()
    manager = _manager(tmp_path, _blocking_runner(started))
    manager.request(source, duration=2.0)
    assert started.wait(5)
    assert manager.info(source).state is ProxyState.GENERATING
    assert manager.cancel(source)
    assert _wait(lambda: manager.info(source).state is ProxyState.NONE)
    assert list((tmp_path / "proxies").iterdir()) == []
    assert manager.resolve(source) == source and not manager.cancel(source)
    manager.shutdown()


def test_cancelling_a_queued_generation_prevents_it_from_ever_running(tmp_path):
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    started = threading.Event()
    ran = []

    def runner(command, cancel, on_progress, on_start):
        ran.append(Path(command[command.index("-i") + 1]).name)
        return _blocking_runner(started)(command, cancel, on_progress, on_start)

    manager = _manager(tmp_path, runner)
    manager.request(str(first), duration=1.0)
    assert started.wait(5)
    assert manager.request(str(second), duration=1.0) is ProxyState.PENDING
    assert manager.cancel(str(second))
    assert manager.info(str(second)).state is ProxyState.NONE
    manager.cancel_all()
    assert _wait(lambda: manager.info(str(first)).state is ProxyState.NONE)
    assert ran == ["a.mp4"]
    manager.shutdown()


def test_delete_removes_the_files_and_the_state(manager, source, tmp_path):
    info = _generate(manager, source)
    assert manager.delete(source) is True
    assert not Path(info.proxy_path).exists() and manager.info(source).state is ProxyState.NONE
    assert manager.delete(source) is False
    _generate(manager, source)
    assert manager.delete_all() == 2
    assert manager.usage_bytes() == 0


def test_inventory_and_usage_report_complete_proxies_only(manager, source, tmp_path):
    info = _generate(manager, source)
    (tmp_path / "proxies" / "proxy-x.partial.mp4").write_bytes(b"1234")
    entries = manager.entries()
    assert [e[0] for e in entries] == [Path(info.proxy_path)] and entries[0][1] == 64
    assert manager.usage_bytes() >= 64


def test_shutdown_during_a_generation_stops_it_and_the_threads(tmp_path, source):
    started = threading.Event()
    manager = _manager(tmp_path, _blocking_runner(started))
    manager.request(source, duration=2.0)
    assert started.wait(5)
    assert manager.shutdown(timeout=5.0) is True
    assert list((tmp_path / "proxies").iterdir()) == []
    assert manager.request(source) is ProxyState.NONE                 # plus rien ne démarre après la fermeture


def _pid_alive(pid):
    if sys.platform == "win32":  # pragma: no cover - vérification POSIX seulement
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancel_and_shutdown_really_kill_the_ffmpeg_process(tmp_path, source, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "60")
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_FFMPEG_PID_FILE", str(pid_file))
    manager = ProxyManager(tmp_path / "proxies", ffmpeg_command=lambda: [sys.executable, str(FAKE_FFMPEG)],
                           memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)
    manager.request(source, duration=60.0)
    assert _wait(lambda: pid_file.exists() and manager.active_process_ids())
    pid = int(pid_file.read_text())
    assert manager.cancel(source)
    assert _wait(lambda: not _pid_alive(pid)) and _wait(lambda: manager.info(source).state is ProxyState.NONE)
    pid_file.unlink()
    manager.request(source, duration=60.0, force=True)
    assert _wait(lambda: pid_file.exists() and manager.active_process_ids())
    pid = int(pid_file.read_text())
    assert manager.shutdown(timeout=10.0) is True
    assert not _pid_alive(pid)


def test_the_default_runner_reports_failures_and_progress_with_a_real_process(tmp_path):
    seen = []
    result = run_ffmpeg([sys.executable, str(FAKE_FFMPEG), "-i", "x", str(tmp_path / "o.mp4")],
                        threading.Event(), seen.append)
    assert result.returncode == 0 and (tmp_path / "o.mp4").exists() and seen and not result.cancelled
    failing = run_ffmpeg([sys.executable, "-c", "import sys; print('nope', file=sys.stderr); sys.exit(3)"],
                         threading.Event(), seen.append)
    assert failing.returncode == 3 and "nope" in failing.stderr


# --- le projet ne dépend jamais des proxies -----------------------------------------------------------------------------


def test_the_project_file_contains_no_proxy_reference(manager, source, tmp_path):
    _generate(manager, source)
    project = Project(
        name="p", media_assets=[MediaAsset(id="a", path=source, name="a", duration=5, width=1, height=1,
                                           fps=25.0, media_type="video")],
        tracks=[Track(id="V1", name="V1", type="video", clips=[])],
    )
    assert "proxy" not in json.dumps(project_payload(project)).lower()
    assert not any("proxy" in f.name for f in fields(MediaAsset))
    path = tmp_path / "film.kut"
    save_project(project, str(path))
    shutil.rmtree(tmp_path / "proxies")                              # cache supprimé : le projet s'ouvre
    assert load_project(str(path)).media_assets[0].path == source


# --- FFmpeg réel (ignoré si FFmpeg est absent) -----------------------------------------------------------------------------------


def test_real_ffmpeg_generates_a_smaller_playable_proxy(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg / ffprobe indisponibles")
    media = tmp_path / "big.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1280x720:r=25:d=2",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(media)],
        check=True, capture_output=True,
    )
    manager = ProxyManager(tmp_path / "proxies", profile_id="low", memo=SignatureMemo(ttl=0.0), disk_ttl=0.0)
    info = _generate(manager, str(media))
    assert info.state is ProxyState.READY, info.error

    def probe(entry):
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", entry, "-show_entries",
             "stream=codec_name,height", "-of", "json", info.proxy_path],
            check=True, capture_output=True, text=True).stdout
        return json.loads(out)["streams"]

    video, audio = probe("v:0")[0], probe("a:0")[0]
    assert video["height"] == 480 and video["codec_name"] == "h264" and audio["codec_name"] == "aac"
    assert manager.resolve(str(media)) == info.proxy_path
    assert manager.shutdown()


def test_request_lighter_generates_the_lighter_profile_only_and_never_when_disabled(manager, source):
    current = manager.profile
    lighter = manager.lighter_profile(2)
    assert lighter is not None and lighter.max_height < current.max_height
    assert manager.lighter_profile(1) is None
    manager.request_lighter(source, 2, duration=2.0)
    assert _wait(lambda: manager.info(source, lighter.id).state is ProxyState.READY)
    assert manager.info(source, current.id).state is ProxyState.NONE
    assert manager.resolve(source, divisor=2) == manager.info(source, lighter.id).proxy_path
    manager.set_enabled(False)
    assert manager.request_lighter(source, 2, duration=2.0) is None
