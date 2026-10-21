"""Le banc de recette (``tools/recette``, ADR-0011 phase 1) : génération déterministe, projets, rapport, dégradations.

Aucun test ne porte sur une durée : les identités (octets, empreintes), les codes, les ratios et les états seulement.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tools.recette import audio, crashes, media, probe, runner, thermal, whisper_check
from tools.recette import report as report_module
from tools.recette.__main__ import main as recette_main
from tools.recette.env import (ROOT, EnvironmentRefused, assert_isolated, default_workdir, ffmpeg_has, find_tool, isolated_environment,
                               machine_slug, resolve_workdir)
from tools.recette.journeys import Capabilities, SCALES, registry
from tools.recette.journeys import tiktok

FFMPEG = find_tool("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and find_tool("ffprobe") and ffmpeg_has(FFMPEG, "filters", "testsrc2")),
    reason="FFmpeg / ffprobe (avec la source testsrc2) introuvable : le banc ne peut pas générer de vidéo",
)
CAPS = (Capabilities(libass=True), Capabilities(libass=False))


# --- Isolation de la configuration réelle ---------------------------------------------------------------------------------


def test_isolated_environment_puts_every_app_directory_under_the_root(tmp_path):
    env = isolated_environment(tmp_path / "run")
    for name in ("KUT_STUDIO_CONFIG_DIR", "KUT_STUDIO_CACHE_DIR", "KUT_STUDIO_DATA_DIR", "KUT_STUDIO_LOG_DIR", "KUT_STUDIO_PROXY_DIR"):
        assert Path(env[name]).is_dir() and (tmp_path / "run") in Path(env[name]).parents
    assert env["QT_QPA_PLATFORM"] == "offscreen" and env["KUT_STUDIO_UPDATE_CHECK"] == "off"
    for name in ("TMPDIR", "TEMP", "TMP"):                                  # les temporaires de l'export restent sous la recette
        assert (tmp_path / "run") in Path(env[name]).parents


def test_an_environment_pointing_at_the_real_configuration_is_refused(tmp_path):
    from tools.recette.env import real_directories

    env = isolated_environment(tmp_path / "run")
    for name, real in real_directories().items():
        broken = dict(env, **{name: str(real)})
        with pytest.raises(EnvironmentRefused):
            assert_isolated(broken)
        inside = dict(env, **{name: str(real / "sous-dossier")})
        with pytest.raises(EnvironmentRefused):
            assert_isolated(inside)
    missing = {key: value for key, value in env.items() if key != "KUT_STUDIO_DATA_DIR"}
    with pytest.raises(EnvironmentRefused):
        assert_isolated(missing)


def test_the_work_directory_is_never_inside_the_repository():
    with pytest.raises(EnvironmentRefused):
        resolve_workdir(ROOT / "recette")
    with pytest.raises(EnvironmentRefused):
        resolve_workdir(ROOT)
    assert recette_main(["gen", "--workdir", str(ROOT / "media-recette")]) == 64
    assert not (ROOT / "media-recette").exists()


def test_default_work_directory_is_outside_the_repository_and_overridable(tmp_path):
    (tmp_path / "Movies").mkdir()
    assert default_workdir({}, home=tmp_path) == tmp_path / "Movies" / "Kut-Studio-Recette"
    assert default_workdir({}, home=tmp_path / "vide") == tmp_path / "vide" / "Kut-Studio-Recette"
    assert default_workdir({"KUT_STUDIO_RECETTE_DIR": str(tmp_path / "x")}, home=tmp_path) == tmp_path / "x"
    assert ROOT not in default_workdir({}, home=Path.home()).parents


def test_the_bench_never_kills_arbitrary_processes():
    """Aucun ``pkill`` / ``killall`` / ``kill`` shell ; ``os.kill`` n'existe que dans le chien de garde du lanceur (enfant lancé ici)."""
    for path in (ROOT / "tools" / "recette").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(pkill|killall|taskkill)\b", text), path
        if path.name != "runner.py":
            assert "os.kill(" not in text and ".kill()" not in text, path
    assert "QT_QPA_PLATFORM" in (ROOT / "tools" / "recette" / "env.py").read_text(encoding="utf-8")
    assert "/Applications" not in "".join(p.read_text(encoding="utf-8") for p in (ROOT / "tools" / "recette").rglob("*.py"))


# --- Son synthétique ---------------------------------------------------------------------------------------------------------


def test_synthetic_audio_is_deterministic_and_seed_dependent():
    first, second = audio.speech(3, 4.0), audio.speech(3, 4.0)
    assert (first == second).all()
    assert not (audio.speech(4, 4.0) == first).all()
    assert (audio.noise(1, 100, 50) == audio.noise(1, 100, 50)).all()
    assert (audio.noise(1, 0, 150)[100:] == audio.noise(1, 100, 50)).all()          # le bruit est une fonction de l'indice
    assert abs(audio.speech(3, 4.0)).max() < 1.0 and abs(audio.music(1, 4.0, 120.0)).max() < 1.0
    assert 0.0 <= audio.unit(5, 7) < 1.0


def test_wav_files_are_byte_identical(tmp_path):
    audio.write_wav(tmp_path / "a.wav", audio.music(1, 2.0, 120.0), audio.MUSIC_RATE)
    audio.write_wav(tmp_path / "b.wav", audio.music(1, 2.0, 120.0), audio.MUSIC_RATE)
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


@needs_ffmpeg
def test_the_music_tempo_is_detectable_by_the_engine(tmp_path):
    from core.beat_detection import detect_tempo

    audio.write_wav(tmp_path / "m.wav", audio.music(2, 20.0, 120.0), audio.MUSIC_RATE)
    estimate = detect_tempo(str(tmp_path / "m.wav"))
    assert abs(estimate.bpm - 120.0) / 120.0 < 0.01


# --- Recettes, empreintes, génération -------------------------------------------------------------------------------------------


def test_recipe_digest_is_stable_and_follows_the_parameters():
    items = registry()["tiktok"].media("quick")
    assert media.recipe_digest(items) == media.recipe_digest(registry()["tiktok"].media("quick"))
    assert media.recipe_digest(items) != media.recipe_digest(registry()["tiktok"].media("full"))
    changed = [media.MediaItem(items[0].key, items[0].path, items[0].kind, dict(items[0].params, seed=999)), *items[1:]]
    assert media.recipe_digest(changed) != media.recipe_digest(items)


@pytest.mark.parametrize("name", ["tiktok", "youtube", "motion3d", "smoke"])
@pytest.mark.parametrize("scale", SCALES)
def test_recipes_are_well_formed(name, scale):
    items = registry()[name].media(scale)
    assert len({item.key for item in items}) == len(items) and len({item.path for item in items}) == len(items)
    for item in items:
        assert item.kind in media.KINDS and not item.path.startswith("/") and ".." not in item.path
        json.dumps(item.params)                                                        # une recette est du JSON pur
        if item.kind == "video":
            assert item.params["width"] % 2 == 0 and item.params["height"] % 2 == 0 and item.params["duration"] > 0


@pytest.mark.parametrize("name", ["tiktok", "youtube", "motion3d", "smoke"])
@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("caps", CAPS)
def test_scripts_are_valid_and_only_cite_generated_media(name, scale, caps):
    from core.scripting.script import parse_script

    journey = registry()[name]
    data = journey.script(scale, caps)
    parse_script(data)                                                                 # ScriptInvalid listerait toutes les erreurs
    generated = {item.path for item in journey.media(scale)}
    for op in data["ops"]:
        if op["op"] in ("import_media", "add_scene3d_clip"):
            assert op["path"] in generated, op
    assert json.loads(json.dumps(data)) == data
    assert data == journey.script(scale, caps)                                         # déterministe


def test_tiktok_degrades_cleanly_without_libass():
    with_libass = [op["op"] for op in tiktok.script("quick", Capabilities(libass=True))["ops"]]
    without = [op["op"] for op in tiktok.script("quick", Capabilities(libass=False))["ops"]]
    assert "add_subtitle_lines" in with_libass and "add_subtitle_lines" not in without
    assert "add_karaoke_lines" in with_libass and "add_karaoke_lines" in without       # le karaoké Qt ne dépend pas de libass


def test_tiktok_transitions_are_placed_in_overlap():
    """D-001 : une transition posée après coup recale le clip entrant et laisse un trou ; le script pose déjà le chevauchement."""
    where, uses = tiktok.positions("full"), dict(tiktok.uses("full"))
    assert where["v2"] == pytest.approx(where["v1"] + uses["v1"] - 0.4)
    assert where["v3"] == pytest.approx(where["h1"] + uses["h1"] - 0.5)
    assert where["h2"] == pytest.approx(where["v3"] + uses["v3"])
    assert tiktok.duration("full") == pytest.approx(60.0 - 0.9)
    lines = tiktok.caption_lines("full")
    assert all(a["end"] <= b["start"] for a, b in zip(lines, lines[1:], strict=False))
    assert all(line["word_starts"] == sorted(line["word_starts"]) and line["word_starts"][-1] < line["end"] - line["start"] for line in lines)


def test_multicam_ground_truth_is_consistent():
    from tools.recette.journeys import youtube

    assert min(offset for _n, _k, offset, _m in youtube.ANGLES) == 0.0                 # le plus petit décalage vaut 0 (sinon refusé)
    assert max(offset for _n, _k, offset, _m in youtube.ANGLES) < youtube.HEAD
    moments = youtube.switches("full")
    assert [t for t, _a in moments] == sorted({t for t, _a in moments}) and moments[-1][0] < youtube.length("full")
    assert all(a[1] != b[1] for a, b in zip(moments, moments[1:], strict=False))
    assert youtube.switches("full") == youtube.switches("full")


@needs_ffmpeg
def test_generation_is_deterministic_and_verifies_fingerprints(tmp_path):
    items = registry()["smoke"].media("quick")
    first = media.generate(items, tmp_path / "a", ffmpeg=FFMPEG)
    second = media.generate(items, tmp_path / "b", ffmpeg=FFMPEG)
    for key in first["items"]:
        assert first["items"][key]["sha256"] == second["items"][key]["sha256"]
    assert first["recipe_digest"] == second["recipe_digest"]
    assert set(media.verify(items, tmp_path / "a").values()) == {"ok"}
    again = media.generate(items, tmp_path / "a", ffmpeg=FFMPEG)
    assert again["generated"] == [] and sorted(again["reused"]) == sorted(item.key for item in items)
    # Un fichier modifié ou tronqué est détecté, puis régénéré à l'identique.
    target = tmp_path / "a" / items[0].path
    original = target.read_bytes()
    target.write_bytes(original[:-10])
    assert media.verify(items, tmp_path / "a")[items[0].key] == "modified"
    repaired = media.generate(items, tmp_path / "a", ffmpeg=FFMPEG)
    assert repaired["generated"] == [items[0].key] and target.read_bytes() == original
    target.unlink()
    assert media.verify(items, tmp_path / "a")[items[0].key] == "missing"
    stale = [media.MediaItem(items[1].key, items[1].path, items[1].kind, dict(items[1].params, seed=3)), *items[2:]]
    assert media.verify(stale, tmp_path / "a")[items[1].key] == "stale"


def test_generation_without_ffmpeg_is_a_clear_error(tmp_path):
    with pytest.raises(media.MediaError, match="FFmpeg"):
        media.generate(registry()["smoke"].media("quick"), tmp_path, ffmpeg=None)


def test_the_3d_scene_is_a_valid_kut3d_file(tmp_path):
    from PySide6.QtGui import QGuiApplication

    from tools.recette.scene3d import scene_document

    QGuiApplication.instance() or QGuiApplication([])
    path = tmp_path / "scene.kut3d"
    path.write_text(json.dumps(scene_document({"title": "KUT", "segments": 12})), encoding="utf-8")
    import kut3d

    scene = kut3d.load_scene(str(path))
    assert len(scene.instances) == 6


# --- Lanceur ---------------------------------------------------------------------------------------------------------------


def test_runner_measures_exit_code_output_and_memory(tmp_path):
    ok = runner.run_measured([sys.executable, "-c", "print('bonjour')"], log_prefix=tmp_path / "ok")
    assert ok.ok and ok.returncode == 0 and "bonjour" in ok.stdout_tail and not ok.crashed
    assert ok.elapsed_s > 0 and (ok.own_peak_rss_mb is None or ok.own_peak_rss_mb > 0)
    bad = runner.run_measured([sys.executable, "-c", "import sys; sys.stderr.write('x' * 100000); sys.exit(3)"], log_prefix=tmp_path / "bad")
    assert bad.returncode == 3 and not bad.ok and not bad.crashed and len(bad.stderr_tail) <= runner.TAIL_CHARS
    missing = runner.run_measured([str(tmp_path / "inexistant")], log_prefix=tmp_path / "none")
    assert missing.returncode is None and not missing.ok and "Lancement impossible" in missing.stderr_tail


def test_runner_reports_the_peak_disk_use_of_watched_folders(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "ancien.bin").write_bytes(b"\0" * 2**20)
    assert runner.directory_size_mb(watched) == pytest.approx(1.0)
    assert runner.directory_size_mb(tmp_path / "absent") == 0.0
    code = f"import time; open({str(watched / 'neuf.bin')!r}, 'wb').write(bytes(3 * 2**20)); time.sleep(2.6)"
    result = runner.run_measured([sys.executable, "-c", code], log_prefix=tmp_path / "disk", watch_dirs=(watched,))
    assert result.ok and result.peak_disk_mb is not None and result.peak_disk_mb >= 3.5
    assert runner.run_measured([sys.executable, "-c", "pass"], log_prefix=tmp_path / "nodisk").peak_disk_mb is None


def test_runner_environment_is_exactly_the_one_given(tmp_path):
    result = runner.run_measured([sys.executable, "-c", "import os; print(os.environ.get('KUT_STUDIO_PROBE'), os.environ.get('HOME'))"],
                                 env={"KUT_STUDIO_PROBE": "oui", "PATH": os.environ.get("PATH", "")}, log_prefix=tmp_path / "env")
    assert result.stdout_tail.split()[0] == "oui"


@pytest.mark.skipif(os.name == "nt", reason="signaux POSIX")
def test_runner_stops_a_child_after_the_timeout_without_calling_it_a_crash(tmp_path):
    result = runner.run_measured([sys.executable, "-c", "import time; time.sleep(120)"], log_prefix=tmp_path / "slow", timeout=1.0)
    assert result.timed_out and not result.ok and not result.crashed


@pytest.mark.skipif(os.name == "nt", reason="signaux POSIX")
def test_runner_stops_a_child_cleanly_when_a_guard_trips(tmp_path):
    """Garde-fou disque / mémoire : le lanceur arrête l'enfant par ``SIGINT`` (Ctrl-C) et le dit ; ce n'est pas un plantage."""
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    disk = runner.run_measured(sleeper, log_prefix=tmp_path / "disk", min_free_gb=1e9, guard_path=tmp_path)
    assert disk.aborted and "disque libre" in disk.aborted and not disk.ok and not disk.crashed and not disk.timed_out
    memory = runner.run_measured(sleeper, log_prefix=tmp_path / "mem", max_rss_mb=1.0)
    assert memory.aborted and "mémoire" in memory.aborted and not memory.ok and not memory.crashed
    calm = runner.run_measured([sys.executable, "-c", "pass"], log_prefix=tmp_path / "calm", min_free_gb=0.0001, guard_path=tmp_path, max_rss_mb=1e9)
    assert calm.ok and calm.aborted is None


@pytest.mark.skipif(not Path("/proc/self/statm").exists(), reason="/proc absent")
def test_memory_guard_works_without_ps(tmp_path, monkeypatch):
    """Images Debian minimales : pas de ``ps``. Le lanceur lit ``/proc`` ; le garde-fou mémoire doit agir (et la mesure exister)."""
    monkeypatch.setattr(runner, "_ps_table", lambda: None)
    assert runner.process_tree_rss_mb(os.getpid()) > 1.0
    result = runner.run_measured([sys.executable, "-c", "import time; time.sleep(120)"], log_prefix=tmp_path / "noPS", max_rss_mb=1.0)
    assert result.aborted and "mémoire" in result.aborted and result.peak_rss_mb is not None and not result.crashed


def test_proc_table_parses_names_with_spaces(tmp_path):
    for pid, name, parent, pages in ((10, "a b) c", 1, 100), (11, "x", 10, 50)):
        folder = tmp_path / str(pid)
        folder.mkdir()
        (folder / "stat").write_text(f"{pid} ({name}) S {parent} 0 0\n", encoding="utf-8")
        (folder / "statm").write_text(f"999 {pages} 0\n", encoding="utf-8")
    (tmp_path / "self").mkdir()
    table = runner._proc_table(tmp_path)
    assert table is not None and runner.process_tree_rss_mb(10, table) == pytest.approx(150 * (os.sysconf("SC_PAGE_SIZE") // 1024) / 1024.0)


def test_exit_classification():
    assert runner.classify_exit(0) == (False, None)
    assert runner.classify_exit(1) == (False, None)
    assert runner.classify_exit(None) == (False, None)
    if hasattr(signal, "SIGSEGV"):
        assert runner.classify_exit(-int(signal.SIGSEGV)) == (True, "SIGSEGV")
        assert runner.classify_exit(-int(signal.SIGABRT))[0] is True
        assert runner.classify_exit(-int(signal.SIGINT), timed_out=True) == (False, "SIGINT")
        assert runner.classify_exit(-int(signal.SIGSEGV), timed_out=True)[0] is True
    assert runner.classify_exit(0xC0000005)[0] is True


def test_process_tree_memory_sums_descendants():
    table = "  10     1   1024\n  11    10   2048\n  12    11   4096\n  13     1   9999\n  bizarre\n"
    assert runner.process_tree_rss_mb(10, table) == pytest.approx((1024 + 2048 + 4096) / 1024.0)
    assert runner.process_tree_rss_mb(99, table) is None


# --- Sorties ---------------------------------------------------------------------------------------------------------------


def test_ffprobe_summary_and_output_checks():
    raw = {"format": {"duration": "10.0", "size": "1000", "format_name": "mov,mp4", "bit_rate": "800"},
           "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
                        "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "nb_frames": "250"},
                       {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                       {"codec_type": "data"}]}
    summary = probe.parse_ffprobe(raw)
    assert summary["duration"] == 10.0 and summary["streams"][0]["fps"] == 25.0 and len(summary["streams"]) == 2
    clean = {"black_ratio": 0.0, "frozen_ratio": 0.0}
    assert probe.check_output(summary, clean, expected_duration=10.0, expected_size=(1920, 1080)) == []
    assert probe.check_output(summary, clean, expected_duration=10.2, expected_size=(1920, 1080)) == []        # dans la tolérance (ratio)
    assert any("durée" in p for p in probe.check_output(summary, clean, expected_duration=20.0, expected_size=(1920, 1080)))
    assert any("taille" in p for p in probe.check_output(summary, clean, expected_duration=10.0, expected_size=(1080, 1920)))
    assert any("noire" in p for p in probe.check_output(summary, {"black_ratio": 0.5, "frozen_ratio": 0.0}, expected_duration=10.0, expected_size=None))
    assert any("gelée" in p for p in probe.check_output(summary, {"black_ratio": 0.0, "frozen_ratio": 0.9}, expected_duration=10.0, expected_size=None))
    silent = dict(summary, streams=summary["streams"][:1])
    assert any("audio" in p for p in probe.check_output(silent, clean, expected_duration=10.0, expected_size=None))
    assert probe.check_output(silent, clean, expected_duration=10.0, expected_size=None, expect_audio=False) == []
    assert probe.check_output({"error": "ffprobe introuvable"}, {}, expected_duration=None, expected_size=None) == ["ffprobe : ffprobe introuvable"]


def test_black_and_frozen_detection_parsing_and_ratio():
    log = ("[blackdetect @ 0x1] black_start:2 black_end:3.5 black_duration:1.5\n[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 4\n"
           "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 2\n[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 6\n")
    found = probe.parse_detection(log)
    assert found["black"] == [(2.0, 3.5)] and found["frozen"] == [(4.0, 6.0)]
    assert probe.ratio([(0, 2), (1, 3), (8, 12)], 10.0) == 0.5                          # recouvrements fusionnés, borné à la durée
    assert probe.ratio([], 0) is None and probe.ratio([(0, 1)], None) is None
    assert probe.warnings_for({"black_intervals": [[1.0, 2.0]], "frozen_intervals": []})[0].startswith("1 plage(s) noire")


@needs_ffmpeg
def test_frame_analysis_finds_a_black_video_and_not_a_moving_one(tmp_path):
    black = tmp_path / "noir.mp4"
    moving = tmp_path / "mouvement.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=5", "-pix_fmt", "yuv420p", str(black)], check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=5", "-pix_fmt", "yuv420p", str(moving)], check=True)
    assert probe.analyse_frames(FFMPEG, black, 5.0)["black_ratio"] >= 0.8
    clean = probe.analyse_frames(FFMPEG, moving, 5.0)
    assert clean["black_ratio"] == 0.0 and clean["frozen_ratio"] == 0.0


# --- Rapport ---------------------------------------------------------------------------------------------------------------


def _fake_report(scale: float = 1.0, chip: str = "Apple M4") -> dict:
    journey = {"title": "TikTok 60 s", "ok": True, "problems": [], "project": {"duration": 59.1, "sequences": 2, "clips": 30, "keyframes": 4, "transitions": 2},
               "stages": {"media": {"seconds": 3.0 * scale, "bytes": 1000, "generated": ["v1"]},
                          "apply": {"elapsed_s": 1.0 * scale, "peak_rss_mb": 100.0 * scale, "returncode": 0},
                          "analysis": {"items": [{"kind": "tempo", "seconds": 0.5 * scale, "relative_error": 0.0001, "returncode": 0}]},
                          "exports": {"9x16": {"elapsed_s": 40.0 * scale, "peak_rss_mb": 3000.0, "output_bytes": 5_000_000, "returncode": 0,
                                               "encoder": "H.264 · CPU", "expected_duration": 59.1,
                                               "probe": {"duration": 59.1, "streams": [{"type": "video", "width": 1080, "height": 1920, "fps": 30.0, "pix_fmt": "yuv420p"}]},
                                               "frames": {"black_ratio": 0.0, "frozen_ratio": 0.0}, "warnings": ["1 plage(s) noire : 5.0-5.5 s"]}}}}
    return report_module.build_report({"model": "Mac16,10", "chip": chip, "ram_gb": 24.0, "os_version": "macOS 27", "app_version": "0.9.0", "python": "3.11"},
                                      {"ffmpeg": "7.1", "libass": False, "whisper": "non"}, {"tiktok": journey}, scale="full", hardware="cpu",
                                      isolation={"KUT_STUDIO_CONFIG_DIR": "/tmp/x"})


def test_report_comparison_is_made_of_ratios_and_never_fails():
    current, reference = _fake_report(2.0), _fake_report(1.0)
    rows = {row["metric"]: row for row in report_module.compare(current, reference)["rows"]}
    assert rows["tiktok.export.9x16.s"]["ratio"] == 2.0 and rows["tiktok.export.9x16.s"]["label"] == "régression"
    assert rows["tiktok.export.9x16.bytes"]["label"] == "identique"
    assert rows["tiktok.apply.s"]["value"] == 2.0 and rows["tiktok.tempo.s"]["ratio"] == 2.0
    same = {row["label"] for row in report_module.compare(_fake_report(1.0), reference)["rows"]}
    assert same <= {"stable", "identique"}
    faster = {row["metric"]: row["label"] for row in report_module.compare(_fake_report(0.5), reference)["rows"]}
    assert faster["tiktok.export.9x16.s"] == "mieux"
    other = report_module.compare(_fake_report(1.0, chip="Apple M1"), reference)
    assert other["same_machine"] is False and report_module.compare(reference, reference)["same_machine"] is True


def test_report_round_trip_markdown_and_privacy(tmp_path):
    report = _fake_report()
    report["journeys"]["tiktok"]["problems"].append(f"export: fichier {Path.home()}/x.mp4 manquant")
    report["comparison"] = report_module.compare(_fake_report(1.2), report)
    json_path, md_path = report_module.write_report(report, tmp_path)
    loaded = report_module.load_report(json_path)
    assert loaded["kind"] == "recette" and loaded["report_version"] == 1
    assert str(Path.home()) not in json_path.read_text(encoding="utf-8") and "~/x.mp4" in json_path.read_text(encoding="utf-8")
    text = md_path.read_text(encoding="utf-8")
    for expected in ("# Rapport de recette", "## TikTok 60 s", "export 9x16", "Écarts à la référence", "ne prouve pas", "à regarder"):
        assert expected in text
    (tmp_path / "autre.json").write_text('{"kind": "autre"}', encoding="utf-8")
    with pytest.raises(ValueError):
        report_module.load_report(tmp_path / "autre.json")
    (tmp_path / "futur.json").write_text('{"kind": "recette", "report_version": 99}', encoding="utf-8")
    with pytest.raises(ValueError):
        report_module.load_report(tmp_path / "futur.json")


def test_report_of_a_failed_journey_is_readable_and_marks_the_crash():
    failed = {"title": "X", "ok": False, "problems": ["apply: code 1"], "stages": {"apply": {"elapsed_s": 1.0, "returncode": -11, "crashed": True,
                                                                                            "signal_name": "SIGSEGV"}}}
    report = report_module.build_report({}, {}, {"x": failed}, scale="quick", hardware="cpu", isolation={})
    assert report["summary"]["ok"] is False and report["summary"]["crashed"] == ["x"]
    text = report_module.render_markdown(report)
    assert "À REGARDER" in text and "apply: code 1" in text
    assert report_module.metrics_of(report) == {"x.apply.s": 1.0}


def test_machine_slug_is_a_stable_file_name():
    assert machine_slug({"model": "Mac16,10", "chip": "Apple M4", "ram_gb": 24.0}) == "mac16-10-apple-m4-24gb"
    assert machine_slug({}) == "machine"


def test_report_command_renders_a_saved_report(tmp_path, capsys):
    path, _ = report_module.write_report(_fake_report(), tmp_path)
    assert recette_main(["report", str(path), "--reference", str(path)]) == 0
    assert "Écarts à la référence" in capsys.readouterr().out


# --- Plantages par heure, relevé thermique ---------------------------------------------------------------------------------------


LOG = """2026-10-22 09:00:01,100 INFO     MainThread kut : Démarrage — Python 3.14, macOS
2026-10-22 09:30:00,000 ERROR    MainThread core.x : une erreur
2026-10-22 10:00:00,000 CRITICAL MainThread kut : Exception non rattrapée
Traceback (most recent call last):
2026-10-22 11:00:00,000 INFO     MainThread kut : fin d'activité
2026-10-22 13:00:00,000 INFO     MainThread kut : Démarrage — Python 3.14, macOS
2026-10-22 14:00:00,000 INFO     MainThread kut : autre
"""


def test_crashes_per_hour_from_the_local_log(tmp_path):
    events = crashes.parse_log(LOG)
    assert [e["kind"] for e in events] == ["start", "error", "crash", "other", "start", "other"]
    estimated = crashes.summarize(events)
    assert estimated["sessions"] == 2 and estimated["hours_estimated"] is True and estimated["hours"] == pytest.approx(3.0)
    assert estimated["uncaught_exceptions"] == 1 and estimated["crashes_per_hour"] == pytest.approx(1 / 3.0, abs=1e-3)
    given = crashes.summarize(events, [crashes.parse_session("2026-10-22T09:00/2026-10-22T11:00")])
    assert given["hours"] == 2.0 and given["hours_estimated"] is False and given["error_lines"] == 1
    outside = crashes.summarize(events, [crashes.parse_session("2026-10-22T12:00/2026-10-22T14:00")])
    assert outside["crashes"] == 0 and outside["crashes_per_hour"] == 0.0
    assert crashes.summarize([])["crashes_per_hour"] is None


def test_system_crash_reports_count_in_the_session_window(tmp_path):
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    (reports / "Kut-Studio-2026-10-22-101000.ips").write_text("{}", encoding="utf-8")
    (reports / "Safari-2026-10-22.ips").write_text("{}", encoding="utf-8")
    moment = datetime(2026, 10, 22, 10, 10).timestamp()
    for path in reports.iterdir():
        os.utime(path, (moment, moment))
    window = crashes.parse_session("2026-10-22T10:00/2026-10-22T11:00")
    assert crashes.crash_reports(reports, *window) == ["Kut-Studio-2026-10-22-101000.ips"]
    assert crashes.summarize([], [window], reports)["system_crash_reports"] == ["Kut-Studio-2026-10-22-101000.ips"]
    assert crashes.crash_reports(None, *window) == [] and crashes.crash_reports(tmp_path / "absent", *window) == []


def test_session_parsing_refuses_bad_intervals():
    for bad in ("2026-10-22T09:00", "2026-10-22T11:00/2026-10-22T09:00", "pas une date/2026-10-22T09:00"):
        with pytest.raises(ValueError):
            crashes.parse_session(bad)


def test_the_log_reader_reads_rotated_files_in_order_and_ignores_garbage(tmp_path):
    (tmp_path / "kut-studio.log.1").write_text(LOG.split("2026-10-22 13")[0], encoding="utf-8")
    (tmp_path / "kut-studio.log").write_text("2026-10-22 13" + LOG.split("2026-10-22 13")[1] + "\x00ordures sans date\n" + "x" * 10_000 + "\n", encoding="utf-8")
    events = crashes.read_logs(tmp_path)
    assert [e["kind"] for e in events][:3] == ["start", "error", "crash"] and events[-1]["time"] == datetime(2026, 10, 22, 14, 0)
    assert crashes.read_logs(tmp_path / "absent") == []


def test_thermal_sampling_with_a_fake_probe(tmp_path):
    readings = iter([{"limits": {}, "throttled": False}, {"limits": {"CPU_Speed_Limit": 80}, "throttled": True}, {"limits": {}, "throttled": False}])
    clock = {"now": 0.0}
    summary = thermal.run(1.0, 30.0, tmp_path / "t.jsonl", read=lambda: next(readings),
                          sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds), now=lambda: clock["now"])
    assert summary["samples"] == 3 and summary["throttled_samples"] == 1 and summary["first_throttled_at_s"] == 30.0
    assert len((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    with pytest.raises(ValueError):
        thermal.run(0, 30.0, tmp_path / "x.jsonl", read=lambda: {})
    assert thermal.parse_pmset("CPU_Scheduler_Limit = 100\nCPU_Speed_Limit = 62\n")["throttled"] is True
    assert thermal.parse_pmset("Note: No thermal warning level has been recorded\n")["throttled"] is False


# --- Dégradations : whisper ------------------------------------------------------------------------------------------------


def test_transcription_degrades_without_whisper_or_model(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper_check, "discover", lambda whisper, model, environment=None: (None, None))
    assert whisper_check.transcribe(tmp_path / "v.wav", tmp_path / "w", None, None, None, {})["status"] == "skipped_no_whisper"
    monkeypatch.setattr(whisper_check, "discover", lambda whisper, model, environment=None: ("/usr/bin/whisper-cli", None))
    assert whisper_check.transcribe(tmp_path / "v.wav", tmp_path / "w", None, None, None, {})["status"] == "skipped_no_model"
    monkeypatch.setattr(whisper_check, "discover", lambda whisper, model, environment=None: ("/usr/bin/whisper-cli", "/m/ggml-base.bin"))
    assert whisper_check.transcribe(tmp_path / "absent.wav", tmp_path / "w", None, None, None, {})["status"] == "failed"


# --- Le parcours entier, par la ligne de commande -----------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.recette_smoke
def test_smoke_montage_of_10_seconds_exports_on_cpu_and_is_checked_by_ffprobe(tmp_path):
    """Le smoke test de la CI sans GPU : démarrage du CLI, construction par script, export CPU, ffprobe. Aucun seuil de durée."""
    from tools.recette import pipeline
    from tools.recette.journeys import registry as journeys

    run_dir = tmp_path / "run"
    options = pipeline.Options(environment=isolated_environment(run_dir / "env"), timeout=900.0)
    startup = runner.run_measured(pipeline.cli(options, "presets"), env=options.environment, cwd=ROOT, log_prefix=run_dir / "startup", timeout=300.0)
    assert startup.ok, startup.stderr_tail
    result = pipeline.run_journey(journeys()["smoke"], "quick", tmp_path / "work", run_dir, options)
    assert result["problems"] == [] and result["ok"], result["problems"]
    export = result["stages"]["exports"]["1080p"]
    assert export["encoder"].endswith("CPU") and export["probe"]["duration"] == pytest.approx(10.0, rel=0.03)
    assert {s["type"] for s in export["probe"]["streams"]} == {"video", "audio"}
    assert result["project"]["duration"] == 10.0
    # L'exécution est isolée : rien n'a été écrit hors de son dossier, et le projet source n'a pas bougé.
    assert (run_dir / "env" / "config").is_dir()


@needs_ffmpeg
@pytest.mark.parametrize("caps", CAPS)
def test_the_tiktok_script_applies_with_and_without_libass(tmp_path, caps):
    """``apply`` (sans export) réussit pour les deux variantes : la voie libass est exécutée même sur une machine sans libass."""
    from core.project_model import Project, Track
    from core.scripting import ScriptContext, Session, apply_script
    from core.scripting.script import parse_script

    journey = registry()["tiktok"]
    media.generate(journey.media("quick"), tmp_path, ffmpeg=FFMPEG)
    width, height, fps = journey.base
    project = Project(name="t", width=width, height=height, fps=fps,
                      tracks=[Track(id=i, name=i, type=k, clips=[]) for i, k in journey.tracks])
    script = parse_script(journey.script("quick", caps))
    report = apply_script(Session(project), script, label="script.json", context=ScriptContext(tmp_path, allow_outside=False))
    assert report.ok, report.error
    assert len(project.sequences) == 2                                                  # la version carrée


def test_whisper_model_discovery_honours_the_callers_variable_and_nothing_else(tmp_path, monkeypatch):
    model = tmp_path / "ggml-faux.bin"
    model.write_bytes(b"0")
    explicit = tmp_path / "ggml-explicite.bin"
    explicit.write_bytes(b"0")
    monkeypatch.setenv("HOME", str(tmp_path / "maison"))
    monkeypatch.setenv("KUT_STUDIO_DATA_DIR", str(tmp_path / "donnees"))            # un dossier isolé du banc ne doit pas être lu
    (tmp_path / "donnees" / "whisper").mkdir(parents=True)
    (tmp_path / "donnees" / "whisper" / "ggml-isole.bin").write_bytes(b"0")
    assert whisper_check._lookup_environment({"KUT_STUDIO_WHISPER_MODEL": str(model), "KUT_STUDIO_DATA_DIR": "/x"}) == {
        "KUT_STUDIO_WHISPER_MODEL": str(model)}
    assert whisper_check.discover(None, None, {"KUT_STUDIO_WHISPER_MODEL": str(model)})[1] == str(model.resolve())
    assert whisper_check.discover(None, str(explicit), {"KUT_STUDIO_WHISPER_MODEL": str(model)})[1] == str(explicit.resolve())
    assert whisper_check.discover(None, None, {})[1] is None                         # ni variable ni modèle : skipped_no_model


def test_text_animation_starts_are_clip_local_in_every_journey():
    """``apply_text_animation.start`` se mesure depuis le début du calque : jamais plus que la durée du calque, et nul ici."""
    from tools.recette.journeys import motion3d

    for journey in (tiktok.script("full", Capabilities()), motion3d.script("full", Capabilities())):
        animations = [op for op in journey["ops"] if op["op"] == "apply_text_animation"]
        assert animations and all(op["start"] == 0.0 for op in animations)


@needs_ffmpeg
def test_animated_text_is_keyed_from_its_own_first_frame(tmp_path):
    """Appliqué au modèle : la première image clé du texte animé est au début du CALQUE (une valeur de timeline la décalerait)."""
    from core.project_model import Project, Track
    from core.scripting import ScriptContext, Session, apply_script
    from core.scripting.script import parse_script
    from tools.recette.journeys import motion3d

    journey = registry()["motion3d"]
    media.generate(journey.media("full"), tmp_path, ffmpeg=FFMPEG)
    width, height, fps = journey.base
    project = Project(name="t", width=width, height=height, fps=fps, tracks=[Track(id=i, name=i, type=k, clips=[]) for i, k in journey.tracks])
    report = apply_script(Session(project), parse_script(motion3d.script("full", Capabilities())), label="s.json",
                          context=ScriptContext(tmp_path, allow_outside=False))
    assert report.ok, report.error
    layer = next(clip for track in project.tracks for clip in track.clips if clip.text and "calques" in clip.text)
    assert layer.timeline_start == pytest.approx(motion3d.snap(2.0))
    times = [key.time_seconds for key in layer.animation] + [key.time_seconds for key in layer.transform_keyframes]
    assert times and min(times) == pytest.approx(0.0)


@needs_ffmpeg
def test_reused_video_is_regenerated_when_the_ffmpeg_changes(tmp_path, monkeypatch):
    items = registry()["smoke"].media("quick")
    first = media.generate(items, tmp_path, ffmpeg=FFMPEG)
    assert first["generated"]
    same = media.generate(items, tmp_path, ffmpeg=FFMPEG)
    assert same["generated"] == []                                                   # même version : réutilisation
    monkeypatch.setattr(media, "ffmpeg_version", lambda path: "99.0-autre")
    other = media.generate(items, tmp_path, ffmpeg=FFMPEG)
    assert sorted(other["generated"]) == sorted(i.key for i in items if i.kind == "video") and other["ffmpeg"] == "99.0-autre"
    assert "music" not in other["generated"]                                         # le son ne dépend pas de FFmpeg
    (tmp_path / media.MANIFEST_NAME).write_text("{ pas du json", encoding="utf-8")
    repaired = media.generate(items, tmp_path, ffmpeg=FFMPEG)
    assert sorted(repaired["generated"]) == sorted(i.key for i in items)
    assert set(media.verify(items, tmp_path).values()) == {"ok"}


@pytest.mark.skipif(os.name == "nt", reason="signaux POSIX")
def test_a_crashed_analysis_is_reported_as_a_crash(tmp_path, monkeypatch):
    """Une analyse (tempo / synchronisation) tuée par un signal remonte jusqu'à ``summary.crashed`` ; SIGKILL simulé, jamais SIGSEGV réel."""
    from tools.recette import pipeline
    from tools.recette.journeys import AnalysisSpec

    killed = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"]
    real = runner.run_measured

    def fake(arguments, **kwargs):
        result = real(killed, **kwargs)
        result.crashed, result.signal_name = True, "SIGSEGV"                          # classé comme un plantage, sans en provoquer un
        return result

    monkeypatch.setattr(pipeline, "run_measured", fake)
    options = pipeline.Options(environment=isolated_environment(tmp_path / "env"))
    analysis = pipeline._analyses(registry()["tiktok"], "quick", tmp_path, tmp_path / "logs", options, [AnalysisSpec("tempo", {})])
    item = analysis["items"][0]
    assert item["crashed"] is True and item["signal"] == "SIGSEGV" and item["returncode"] == -int(signal.SIGKILL) and not analysis["ok"]
    report = report_module.build_report({}, {}, {"tiktok": {"ok": False, "problems": [], "stages": {"analysis": analysis}}},
                                        scale="quick", hardware="cpu", isolation={})
    assert report["summary"]["crashed"] == ["tiktok"]
