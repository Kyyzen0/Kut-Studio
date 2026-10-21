"""La ligne de commande (``core/cli.py``, ``main.py``, ADR-0005 étape 3) : ``info``, ``validate``, ``presets``, ``export``.

Le contrat est celui des scripts : ``--json`` rend **un** objet sur la sortie standard et rien d'autre ; les codes de sortie sont
stables (0 réussi, 1 échec, 2 projet chargé avec des entrées ignorées, 64 mauvais usage, 130 interrompu) ; un export ne remplace
jamais son projet ni un fichier existant sans ``--overwrite``, et passe par la même commande FFmpeg que l'interface.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import cli
from core.export_runner import ExportResult, ExportStatus
from core.project_io import save_project
from core.project_model import MediaAsset, Project, Track
from core.timeline_operations import add_clip_to_track
from ui import i18n

ROOT = Path(__file__).resolve().parent.parent
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg absent"
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    # Le CLI suit KUT_STUDIO_LANG puis LANG : sans cela, les attentes en français dépendent de la langue du poste (CI macOS en anglais).
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    if shutil.which("ffmpeg") is None:
        pytest.skip(
            "FFmpeg absent : les projets de ces tests référencent un vrai média"
        )
    path = tmp_path_factory.mktemp("cli") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=1,format=yuv420p",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        timeout=60,
    )
    return str(path)


def _project(media_path: str, *, name: str = "Démo") -> Project:
    asset = MediaAsset(
        "m", media_path, "source", 1.0, 320, 180, 30.0, "video", has_audio=True
    )
    project = Project(
        name,
        width=320,
        height=180,
        fps=30.0,
        media_assets=[asset],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )
    add_clip_to_track(project, "m", "V1", 0.0)
    return project


@pytest.fixture
def kut(media, tmp_path) -> Path:
    path = tmp_path / "demo.kut"
    save_project(_project(media), str(path))
    return path


def _run(*argv: str, capsys) -> tuple[int, str, str]:
    code = cli.run(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- Reconnaître une commande -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["main.py", "info", "p.kut"], True),
        (["main.py", "validate", "p.kut", "--json"], True),
        (["main.py", "presets"], True),
        (["main.py", "export", "p.kut", "-o", "o.mp4"], True),
        (["main.py", "--lang", "es", "info", "p.kut"], True),
        (["main.py", "--lang=en", "export", "p.kut"], True),
        (["main.py", "p.kut"], False),  # un projet : l'interface
        (["main.py"], False),
        (["main.py", "--smoke-test"], False),
        (["main.py", "--lang"], False),
        (["main.py", "--lang", "es"], False),
        (["main.py", "./info"], False),  # un projet nommé « info » s'ouvre par ./info
    ],
)
def test_the_first_non_option_argument_decides_between_a_command_and_the_window(
    argv, expected
):
    assert cli.is_cli_invocation(argv) is expected


# --- info ---------------------------------------------------------------------------------------------------------------


def test_info_describes_the_project_in_text_and_in_json(kut, capsys):
    code, out, _err = _run("info", str(kut), capsys=capsys)
    assert (
        code == 0
        and "Démo" in out
        and "format 17" in out
        and "320×180" in out
        and "1 clip(s)" in out
    )
    code, out, _err = _run("info", str(kut), "--json", capsys=capsys)
    data = json.loads(out)
    assert code == 0 and data["kut_cli"] == cli.SCHEMA_VERSION and data["ok"] is True
    assert (
        data["name"] == "Démo"
        and data["format_version"] == 17
        and data["active_sequence"] == "seq-main"
    )
    sequence = data["sequences"][0]
    assert (sequence["id"], sequence["width"], sequence["height"], sequence["fps"]) == (
        "seq-main",
        320,
        180,
        30.0,
    )
    assert (
        sequence["tracks"] == 2
        and sequence["clips"] == 1
        and sequence["duration"] == pytest.approx(1.0)
    )
    assert data["media"] == {"count": 1, "missing": []} and data["dropped"] == {}


def test_info_lists_missing_media_by_path(tmp_path, capsys):
    path = tmp_path / "p.kut"
    save_project(_project("/nonexistent/absent.mp4"), str(path))
    code, out, _err = _run("info", str(path), "--json", capsys=capsys)
    missing = json.loads(out)["media"]["missing"]
    assert code == 0 and missing == [
        {"id": "m", "name": "source", "path": "/nonexistent/absent.mp4"}
    ]


# --- validate -----------------------------------------------------------------------------------------------------------


def test_validate_an_intact_project_exits_0(kut, capsys):
    code, out, _err = _run("validate", str(kut), capsys=capsys)
    assert code == cli.EXIT_OK and "intact" in out
    code, out, _err = _run("validate", str(kut), "--json", capsys=capsys)
    assert json.loads(out)["intact"] is True and json.loads(out)["dropped"] == {}


def test_validate_a_project_that_lost_entries_exits_2_and_says_which(kut, capsys):
    raw = json.loads(kut.read_text(encoding="utf-8"))
    clip = raw["project"]["sequences"][0]["tracks"][0]["clips"][0]
    clip["effects"] = [{"id": "x", "type": "nonexistent"}, "junk"]
    raw["project"]["sequences"][0]["buses"] = [{"id": ""}]
    kut.write_text(json.dumps(raw), encoding="utf-8")

    code, out, _err = _run("validate", str(kut), capsys=capsys)
    assert code == cli.EXIT_DROPPED == 2
    assert (
        "3 entrée(s) ignorée(s)" in out
        and "effets de clip : 2" in out
        and "bus audio : 1" in out
    )
    code, out, _err = _run("validate", str(kut), "--json", capsys=capsys)
    data = json.loads(out)
    assert code == 2 and data["intact"] is False and data["dropped_total"] == 3
    assert data["dropped"] == {"buses": 1, "clip_effects": 2}


def test_validate_reports_missing_media_without_failing(tmp_path, capsys):
    path = tmp_path / "p.kut"
    save_project(_project("/nonexistent/absent.mp4"), str(path))
    code, out, _err = _run("validate", str(path), capsys=capsys)
    assert (
        code == 0
        and "introuvable(s) sur le disque" in out
        and "/nonexistent/absent.mp4" in out
    )


def test_validate_refuses_a_broken_file_and_a_missing_one_with_exit_1(tmp_path, capsys):
    broken = tmp_path / "broken.kut"
    broken.write_text("{", encoding="utf-8")
    code, out, err = _run("validate", str(broken), "--json", capsys=capsys)
    data = json.loads(out)
    assert (
        code == 1
        and data["ok"] is False
        and data["error"]["kind"] == "invalid"
        and "invalide" in err.lower()
    )
    code, out, _err = _run(
        "info", str(tmp_path / "absent.kut"), "--json", capsys=capsys
    )
    assert code == 1 and json.loads(out)["error"]["kind"] == "not_found"


# --- presets et langue --------------------------------------------------------------------------------------------------


def test_presets_lists_the_builtin_presets_in_text_and_json(capsys):
    code, out, _err = _run("presets", capsys=capsys)
    assert code == 0 and "h264_1080p" in out and "1920×1080" in out
    code, out, _err = _run("presets", "--json", capsys=capsys)
    presets = json.loads(out)["presets"]
    assert {p["id"] for p in presets} >= {"h264_1080p", "h265_4k", "youtube"}
    assert all(
        {"id", "name", "container", "video_codec", "width", "height", "summary"}
        <= set(p)
        for p in presets
    )


def test_messages_follow_the_requested_language_before_or_after_the_command(
    kut, tmp_path, capsys
):
    _code, out, _err = _run("validate", str(kut), "--lang", "en", capsys=capsys)
    assert "Project intact" in out
    _code, out, _err = _run("--lang", "es", "validate", str(kut), capsys=capsys)
    assert "Proyecto intacto" in out
    _code, _out, err = _run(
        "info", str(tmp_path / "absent.kut"), "--lang", "en", capsys=capsys
    )
    assert "File not found" in err


def test_a_bad_command_line_is_a_usage_error_with_exit_64_and_no_traceback(capsys):
    for argv in (
        ["export"],
        ["export", "p.kut"],
        ["info"],
        ["validate", "p.kut", "--inconnue"],
        ["--lang", "xx", "presets"],
    ):
        code, out, err = _run(*argv, capsys=capsys)
        assert (
            code == cli.EXIT_USAGE == 64
            and out == ""
            and "Erreur d'utilisation" in err
            and "Traceback" not in err
        )
    assert cli.run(["--help"]) == 0


def test_a_bad_command_line_under_json_still_prints_exactly_one_json_object(capsys):
    code, out, err = _run("export", "--json", capsys=capsys)
    data = json.loads(out)
    assert (
        code == cli.EXIT_USAGE
        and data["ok"] is False
        and data["error"]["kind"] == "usage"
    )
    assert "Erreur d'utilisation" in err and data["error"]["message"] in err


def test_every_dropped_section_label_is_translated_in_each_language(kut, capsys):
    from core.project_io import LOAD_SECTIONS

    raw = json.loads(kut.read_text(encoding="utf-8"))
    raw["project"]["sequences"][0]["buses"] = [{"id": ""}]
    kut.write_text(json.dumps(raw), encoding="utf-8")
    for lang, label in (
        ("fr", "bus audio : 1"),
        ("en", "audio buses: 1"),
        ("es", "buses de audio: 1"),
    ):
        _code, out, _err = _run("validate", str(kut), "--lang", lang, capsys=capsys)
        assert label in out, lang
    for lang in ("fr", "en", "es"):
        i18n.set_language(lang)
        assert {i18n.translate(f"cli.section.{key}") for key in LOAD_SECTIONS} and all(
            not i18n.translate(f"cli.section.{key}").startswith("cli.section.")
            for key in LOAD_SECTIONS
        ), lang


# --- export -------------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_export_writes_a_real_video_through_the_shared_runner(
    kut, tmp_path, capsys, qapp
):
    output = tmp_path / "out.mp4"
    code, out, err = _run(
        "export", str(kut), "-o", str(output), "--preset", "h264_1080p", capsys=capsys
    )
    assert code == 0 and "Export terminé" in out and output.is_file()
    assert "100 %" in err, "la progression va sur la sortie d'erreur"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,nb_frames",
            "-of",
            "csv=p=0",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    assert probe == "h264,1920,1080,30"
    assert sorted(item.name for item in tmp_path.iterdir()) == [
        "demo.kut",
        "out.mp4",
    ], "ni fichier caché ni reste"


@needs_ffmpeg
def test_export_json_puts_exactly_one_object_on_stdout_and_nothing_else(
    kut, tmp_path, capsys, qapp
):
    code, out, _err = _run(
        "export", str(kut), "-o", str(tmp_path / "o.mp4"), "--json", capsys=capsys
    )
    data = json.loads(out)  # tout ce qui est sur stdout est ce seul objet
    assert (
        code == 0
        and data["ok"] is True
        and data["status"] == "ok"
        and data["preset"] == "h264_1080p"
    )
    assert (
        data["output"] == str((tmp_path / "o.mp4").resolve())
        and data["sequence"] == "seq-main"
    )
    assert (
        data["encoder"]
        and data["fallback_reason"] is None
        and data["error_kind"] is None
    )


@needs_ffmpeg
def test_export_never_replaces_its_project_or_an_existing_file_without_overwrite(
    kut, tmp_path, capsys, qapp
):
    code, out, err = _run(
        "export", str(kut), "-o", str(kut), "--overwrite", "--json", capsys=capsys
    )
    assert (
        code == 64
        and json.loads(out)["error"]["kind"] == "output_is_input"
        and "projet lui-même" in err
    )
    assert json.loads(kut.read_text(encoding="utf-8"))["format"], "le projet est intact"

    existing = tmp_path / "out.mp4"
    existing.write_bytes(b"version precedente")
    code, out, err = _run(
        "export", str(kut), "-o", str(existing), "--json", capsys=capsys
    )
    assert (
        code == 64
        and json.loads(out)["error"]["kind"] == "output_exists"
        and "--overwrite" in err
    )
    assert existing.read_bytes() == b"version precedente"
    code, _out, _err = _run(
        "export", str(kut), "-o", str(existing), "--overwrite", "--quiet", capsys=capsys
    )
    assert code == 0 and existing.stat().st_size > 1000


def test_export_refuses_an_unknown_preset_or_sequence_naming_the_choices(
    kut, tmp_path, capsys
):
    code, out, err = _run(
        "export",
        str(kut),
        "-o",
        str(tmp_path / "o.mp4"),
        "--preset",
        "inconnu",
        "--json",
        capsys=capsys,
    )
    assert (
        code == 64
        and json.loads(out)["error"]["kind"] == "unknown_preset"
        and "h264_1080p" in err
    )
    code, out, err = _run(
        "export",
        str(kut),
        "-o",
        str(tmp_path / "o.mp4"),
        "--sequence",
        "absente",
        "--json",
        capsys=capsys,
    )
    assert (
        code == 64
        and json.loads(out)["error"]["kind"] == "unknown_sequence"
        and "seq-main" in err
    )
    assert not (tmp_path / "o.mp4").exists()


@needs_ffmpeg
def test_export_picks_a_sequence_by_id_or_by_name(media, tmp_path, capsys, qapp):
    from core.sequences import create_sequence

    project = _project(media)
    other = create_sequence(project, "Autre")
    project.active_sequence_id = other.id
    add_clip_to_track(project, "m", other.tracks[0].id, 0.0)
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    choices = (
        ("seq-main", "seq-main"),
        ("Séquence principale", "seq-main"),
        (other.id, other.id),
        ("Autre", other.id),
    )
    for index, (chosen, expected) in enumerate(choices):
        # Un nom UNIQUE par essai, fondé sur l'indice : le hash() d'une chaîne est aléatoire à chaque processus, et deux
        # essais au même `expected` pouvaient tomber (1 fois sur 1 000) sur le même fichier, que le CLI refuse alors
        # (code 64, « la sortie existe ») : c'était un échec intermittent de la CI.
        out_file = tmp_path / f"{index}_{expected}.mp4"
        code, out, _err = _run(
            "export",
            str(path),
            "-o",
            str(out_file),
            "--sequence",
            chosen,
            "--quiet",
            "--json",
            capsys=capsys,
        )
        assert code == 0 and out_file.is_file(), chosen
        assert json.loads(out)["sequence"] == expected, chosen


@needs_ffmpeg
def test_export_of_a_project_with_a_missing_media_fails_with_exit_1_and_a_clear_message(
    tmp_path, capsys, qapp
):
    path = tmp_path / "p.kut"
    project = _project("/nonexistent/absent.mp4")
    save_project(project, str(path))
    code, out, err = _run(
        "export",
        str(path),
        "-o",
        str(tmp_path / "o.mp4"),
        "--json",
        "--quiet",
        capsys=capsys,
    )
    data = json.loads(out)
    assert (
        code == 1
        and data["ok"] is False
        and data["status"] == "failed"
        and data["message"]
    )
    assert not (tmp_path / "o.mp4").exists() and "Export échoué" in err


@needs_ffmpeg
def test_a_project_that_lost_entries_is_exported_with_a_warning_on_stderr(
    media, tmp_path, capsys, qapp
):
    project = _project(media)
    save_project(project, str(tmp_path / "p.kut"))
    raw = json.loads((tmp_path / "p.kut").read_text(encoding="utf-8"))
    raw["project"]["sequences"][0]["tracks"][0]["clips"][0]["effects"] = ["junk"]
    (tmp_path / "p.kut").write_text(json.dumps(raw), encoding="utf-8")
    code, out, err = _run(
        "export",
        str(tmp_path / "p.kut"),
        "-o",
        str(tmp_path / "o.mp4"),
        "--quiet",
        "--json",
        capsys=capsys,
    )
    assert code == 0 and json.loads(out)["ok"] is True
    assert "1 entrée(s) ignorée(s)" in err and "effets de clip : 1" in err


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="SIGINT : sémantique différente sous Windows"
)
def test_ctrl_c_cancels_the_export_and_exits_130(
    kut, tmp_path, capsys, monkeypatch, qapp
):
    """Le gestionnaire de SIGINT du CLI met l'annulation à vrai : l'exécuteur la voit et s'arrête (ici simulé, déterministe)."""
    started = threading.Event()

    def fake_run_export(request, *, cancelled=None, **_options):
        started.set()
        deadline = time.monotonic() + 15
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        return ExportResult(
            ExportStatus.CANCELLED if cancelled() else ExportStatus.FAILED,
            request.output_path,
        )

    monkeypatch.setattr("core.export_runner.run_export", fake_run_export)
    threading.Thread(
        target=lambda: (started.wait(10), os.kill(os.getpid(), signal.SIGINT)),
        daemon=True,
    ).start()
    code, out, err = _run(
        "export", str(kut), "-o", str(tmp_path / "o.mp4"), "--json", capsys=capsys
    )
    assert (
        code == cli.EXIT_INTERRUPTED == 130
        and json.loads(out)["status"] == "cancelled"
        and "annulé" in err
    )
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler, (
        "le gestionnaire d'origine est rétabli"
    )


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="SIGINT : sémantique différente sous Windows"
)
def test_ctrl_c_during_the_loudness_measurement_cancels_with_exit_130(
    kut, tmp_path, capsys, monkeypatch, qapp
):
    """La mesure de loudness (préréglage social) précède l'export : Ctrl-C doit déjà rendre l'annulation documentée."""

    def slow_measure(plan, fps, target):
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(15)  # interrompu par le gestionnaire ; sinon le test échoue au délai
        raise AssertionError("la mesure n'a pas été interrompue")

    def never_exports(*_args, **_options):
        raise AssertionError("aucun export ne doit démarrer après l'annulation")

    monkeypatch.setattr("core.loudness.normalized_plan", slow_measure)
    monkeypatch.setattr("core.export_runner.run_export", never_exports)
    code, out, err = _run(
        "export",
        str(kut),
        "-o",
        str(tmp_path / "o.mp4"),
        "--preset",
        "tiktok",
        "--json",
        capsys=capsys,
    )
    assert (
        code == cli.EXIT_INTERRUPTED == 130
        and json.loads(out)["status"] == "cancelled"
        and "annulé" in err
    )
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


# --- main.py : le même binaire, avant Qt ------------------------------------------------------------------------------


def _main(*argv: str, config: Path) -> subprocess.CompletedProcess:
    config.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        KUT_STUDIO_CONFIG_DIR=str(config),
        KUT_STUDIO_UPDATE_CHECK="off",
        KUT_STUDIO_LANG="fr",
        HOME=str(config),
        USERPROFILE=str(config),
    )
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *argv],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
        cwd=config,
    )


def test_main_py_runs_a_command_without_opening_the_application(kut, tmp_path):
    done = _main("info", str(kut), "--json", config=tmp_path / "cfg")
    assert done.returncode == 0, done.stderr[-400:]
    assert json.loads(done.stdout)["name"] == "Démo"
    assert (
        "QApplication" not in done.stderr and "propagateSizeHints" not in done.stderr
    ), "aucune fenêtre n'a été construite"


def test_main_py_recognises_a_global_option_before_the_command(kut, tmp_path):
    """Régression : « main.py --lang es validate p.kut » lançait l'interface (le premier argument n'était pas une commande) et ne
    rendait jamais la main ; le délai de ce test le détecte."""
    done = _main("--lang", "es", "validate", str(kut), config=tmp_path / "cfg")
    assert done.returncode == 0 and "Proyecto intacto" in done.stdout


def test_main_py_reports_a_usage_error_with_exit_64(tmp_path):
    done = _main("export", config=tmp_path / "cfg")
    assert done.returncode == 64 and "Erreur d'utilisation" in done.stderr
