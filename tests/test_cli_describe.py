"""``Kut-Studio describe projet.kut [--json]`` (``core/cli.py``, ADR-0005 étape 5) : mêmes conventions que ``info``.

Sortie lisible par défaut ; ``--json`` = **un** objet sur la sortie standard (``kut_cli``) avec la description complète sous
``description`` ; codes de sortie 0 / 1 / 64 ; messages traduits (fr, en, es) ; traitée avant Qt ; **lecture seule** (le fichier ne
change pas et aucun verrou n'est créé).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from describe_project import rich_project

from core import cli
from core.project_io import save_project
from core.scripting.describe import describe
from ui import i18n

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


@pytest.fixture
def kut(tmp_path) -> Path:
    present = tmp_path / "a.mp4"
    present.write_bytes(b"x")
    project = rich_project()
    project.media_assets[0].path = str(
        present
    )  # m1 existe, m2 (/media/musique.wav) manque
    path = tmp_path / "riche.kut"
    save_project(project, str(path))
    return path


def _run(*argv: str, capsys) -> tuple[int, str, str]:
    code = cli.run(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_describe_is_a_cli_command_handled_before_qt():
    assert "describe" in cli.COMMANDS
    assert cli.is_cli_invocation(
        ["main.py", "describe", "p.kut"]
    ) and cli.is_cli_invocation(["main.py", "--lang", "en", "describe", "p"])


def test_json_is_one_object_on_stdout_with_the_full_description(kut, capsys):
    code, out, err = _run("describe", str(kut), "--json", capsys=capsys)
    data = json.loads(out)  # stdout ne contient QUE le JSON
    assert (
        code == cli.EXIT_OK
        and data["kut_cli"] == cli.SCHEMA_VERSION
        and data["ok"] is True
    )
    assert data["file"] == str(kut) and data["dropped"] == {}
    description = data["description"]
    assert (
        description["describe_version"] == 1
        and description["project"]["active_sequence"] == "seq-main"
    )
    assert [seq["id"] for seq in description["sequences"]] == [
        "seq-main",
        "seq-b",
        "seq_mc",
    ]
    assert "Projet" in err and "--json" in err, (
        "le texte humain va sur la sortie d'erreur"
    )


def test_the_media_presence_is_tested_on_disk_by_the_command(kut, capsys):
    _code, out, _err = _run("describe", str(kut), "--json", capsys=capsys)
    media = {
        item["id"]: item["present"] for item in json.loads(out)["description"]["media"]
    }
    assert (media["m1"], media["m2"]) == (True, False)
    assert (
        media["asset-subtitle-s1"] is None and media["graphic-asset-g1"] is None
    )  # médias techniques : pas de fichier


def test_the_command_output_is_the_api_output_for_the_loaded_project(kut, capsys):
    from core.project_io import load_project

    _code, out, _err = _run("describe", str(kut), "--json", capsys=capsys)
    expected = json.loads(
        json.dumps(describe(load_project(str(kut)), check_media=True))
    )
    assert json.loads(out)["description"] == expected


def test_default_output_is_a_readable_summary_on_stdout(kut, capsys):
    code, out, err = _run("describe", str(kut), capsys=capsys)
    assert code == 0 and err == ""
    assert (
        "Projet : Riche" in out
        and "Séquence principale [seq-main] (active)" in out
        and "Scène B [seq-b]" in out
    )
    assert (
        "Piste A1 [A1] audio : 1 clip(s)" in out
        and "1 transition(s), 2 repère(s)" in out
    )
    assert "Médias : 5 (1 introuvable(s))" in out and "--json" in out
    assert "Pas encore décrit" not in out, (
        "depuis la partie C, rien n'est « non décrit » : la ligne n'apparaît plus"
    )


@pytest.mark.parametrize(
    "lang, expected",
    [
        ("en", "Not described yet"),
        ("es", "Aún sin describir"),
        ("fr", "Pas encore décrit"),
    ],
)
def test_messages_are_translated(kut, capsys, monkeypatch, lang, expected):
    """La ligne « pas encore décrit » ne s'écrit que si la description nomme une section ; on en nomme une pour vérifier les trois langues."""
    from core.scripting import api

    original = api.describe
    monkeypatch.setattr(
        api,
        "describe",
        lambda project, **options: (
            original(project, **options) | {"not_described": ["futur"]}
        ),
    )
    _code, out, _err = _run("describe", str(kut), "--lang", lang, capsys=capsys)
    assert expected in out and "futur" in out


def test_describe_translations_exist_in_each_language():
    for key in ("project", "track", "extras", "not_described", "hint"):
        texts = {}
        for lang in ("fr", "en", "es"):
            i18n.set_language(lang)
            texts[lang] = i18n.translate(f"cli.describe.{key}")
        assert all(not text.startswith("cli.describe.") for text in texts.values()), key
        assert len(set(texts.values())) == 3, key


def test_is_read_only_the_file_is_untouched_and_no_lock_is_created(kut, capsys):
    before = kut.read_bytes()
    listing = sorted(os.listdir(kut.parent))
    stamp = kut.stat().st_mtime_ns
    for argv in (("describe", str(kut)), ("describe", str(kut), "--json")):
        assert _run(*argv, capsys=capsys)[0] == 0
    assert kut.read_bytes() == before and kut.stat().st_mtime_ns == stamp
    assert sorted(os.listdir(kut.parent)) == listing and not list(
        kut.parent.glob("*.lock")
    )


def test_a_missing_or_invalid_file_fails_with_exit_1_and_one_json_error(
    tmp_path, capsys
):
    code, out, err = _run(
        "describe", str(tmp_path / "absent.kut"), "--json", capsys=capsys
    )
    data = json.loads(out)
    assert (
        code == cli.EXIT_FAILED
        and data["ok"] is False
        and data["error"]["kind"] == "not_found"
        and err.strip()
    )
    broken = tmp_path / "casse.kut"
    broken.write_text("{ pas du json", encoding="utf-8")
    code, out, _err = _run("describe", str(broken), "--json", capsys=capsys)
    assert code == cli.EXIT_FAILED and json.loads(out)["error"]["kind"] == "invalid"
    code, out, _err = _run("describe", str(broken), capsys=capsys)
    assert code == cli.EXIT_FAILED and out == ""


def test_a_missing_argument_is_a_usage_error(capsys):
    code, out, _err = _run("describe", "--json", capsys=capsys)
    assert code == cli.EXIT_USAGE and json.loads(out)["error"]["kind"] == "usage"


def test_entries_dropped_while_loading_are_warned_about_and_listed(kut, capsys):
    raw = json.loads(kut.read_text(encoding="utf-8"))
    raw["project"]["sequences"][0]["buses"] = [{"id": ""}]
    kut.write_text(json.dumps(raw), encoding="utf-8")
    code, out, err = _run("describe", str(kut), "--json", capsys=capsys)
    assert code == 0 and json.loads(out)["dropped"] == {"buses": 1}
    assert "1 entrée(s) ignorée(s)" in err and "bus audio : 1" in err


def test_the_real_program_handles_it_without_qt(kut):
    """Le vrai ``main.py`` (sous-processus) : la commande est traitée avant Qt, la sortie standard est du JSON pur."""
    env = {
        **os.environ,
        "KUT_STUDIO_CONFIG_DIR": str(kut.parent / "config"),
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "describe", str(kut), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=kut.parent,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["description"]["describe_version"] == 1
