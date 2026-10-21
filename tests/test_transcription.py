"""Transcription par whisper.cpp : lecture de sa sortie, lignes de sous-titres, découverte, tâche de la file d'analyse.

La sortie JSON de référence est celle de whisper.cpp 1.9.4 (``-ml 1 -sow -oj``) sur « Bonjour à tous. Bienvenue… ».
Le déroulé complet de la tâche (FFmpeg réel, puis whisper) tourne partout avec un faux ``whisper-cli`` qui écrit cette
sortie ; la vraie reconnaissance ne tourne que si whisper.cpp, un modèle et une voix de synthèse sont disponibles.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap

import pytest

from core.transcription import (
    Transcript,
    TranscriptionJob,
    Word,
    extraction_command,
    find_whisper,
    find_whisper_model,
    group_words,
    model_directory,
    parse_whisper_json,
    whisper_command,
)
from render_probe import needs_ffmpeg

WHISPER_OUTPUT = {
    "result": {"language": "fr"},
    "transcription": [
        {"offsets": {"from": 0, "to": 10}, "text": ""},
        {"offsets": {"from": 10, "to": 680}, "text": " Bonjour"},
        {"offsets": {"from": 680, "to": 690}, "text": " à"},
        {"offsets": {"from": 690, "to": 1330}, "text": " tous,"},
        {"offsets": {"from": 1330, "to": 1960}, "text": " bienvenue"},
        {"offsets": {"from": 1960, "to": 2300}, "text": " [Musique]"},
        {"offsets": {"from": 2300, "to": 2490}, "text": " dans"},
        {"offsets": {"from": 2490, "to": 3170}, "text": " Studio."},
    ],
}

posix_only = pytest.mark.skipif(sys.platform.startswith("win"), reason="faux whisper-cli en script shell")


def _words(*items: tuple[str, float, float]) -> list[Word]:
    return [Word(text, start, end) for text, start, end in items]


# --- Lecture de la sortie ----------------------------------------------------------------------------------------------


def test_whisper_words_are_brought_back_to_media_time():
    transcript = parse_whisper_json(WHISPER_OUTPUT, offset=12.5)
    assert transcript.language == "fr"
    assert [word.text for word in transcript.words] == ["Bonjour", "à", "tous,", "bienvenue", "dans", "Studio."]
    assert transcript.words[0] == Word("Bonjour", 12.51, 13.18)                 # 12,5 s + 10 ms … 12,5 s + 680 ms
    assert transcript.words[-1].end == pytest.approx(15.67)


def test_an_empty_or_odd_output_gives_no_word():
    assert parse_whisper_json({}) == Transcript("", ())
    odd = {"transcription": [{"text": " sans temps"}, {"offsets": {"from": 5, "to": 1}, "text": " (rires)"}]}
    assert parse_whisper_json(odd).words == ()


# --- Lignes de sous-titres ----------------------------------------------------------------------------------------------


def test_a_line_ends_after_a_sentence_and_before_a_pause():
    words = _words(("Salut", 0.0, 0.4), ("toi.", 0.4, 0.8), ("Ça", 0.9, 1.1), ("va", 1.1, 1.3), ("bien", 2.5, 2.9))
    assert [[w.text for w in line] for line in group_words(words)] == [["Salut", "toi."], ["Ça", "va"], ["bien"]]


def test_a_line_stays_short_in_characters_and_in_time():
    long_words = _words(*[(f"mot{n:02d}", 0.3 * n, 0.3 * n + 0.25) for n in range(12)])
    lines = group_words(long_words)
    assert all(len(" ".join(w.text for w in line)) <= 32 for line in lines)
    assert [w for line in lines for w in line] == long_words                   # rien de perdu, ordre gardé
    slow = _words(*[(f"m{n}", 1.0 * n, 1.0 * n + 0.9) for n in range(5)])
    assert all(line[-1].end - line[0].start <= 3.0 for line in group_words(slow))
    assert group_words(_words(("anticonstitutionnellementissime", 0.0, 1.0))) == [
        tuple(_words(("anticonstitutionnellementissime", 0.0, 1.0))),
    ]


# --- Découverte et commandes --------------------------------------------------------------------------------------------


def test_a_configured_tool_and_model_win(tmp_path):
    tool = tmp_path / "whisper-cli"
    tool.write_text("")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"0")
    env = {"KUT_STUDIO_DATA_DIR": str(tmp_path / "data")}
    assert find_whisper(str(tool), environment=env) == str(tool.resolve())
    assert find_whisper_model(str(model), environment=env) == str(model.resolve())
    assert find_whisper_model("", environment={**env, "KUT_STUDIO_WHISPER_MODEL": str(model)}) == str(model.resolve())


def test_a_model_dropped_in_the_data_folder_is_found(tmp_path):
    env = {"KUT_STUDIO_DATA_DIR": str(tmp_path)}
    assert find_whisper_model("", environment=env) is None
    folder = model_directory(environment=env)
    folder.mkdir(parents=True)
    for name in ("ggml-small.bin", "ggml-base.bin", "notes.txt"):
        (folder / name).write_bytes(b"0")
    assert find_whisper_model(str(tmp_path / "absent.bin"), environment=env) == str(folder / "ggml-base.bin")


def test_commands():
    assert extraction_command("ffmpeg", "a.mov", "v.wav", start=1.5, duration=2.0)[-8:] == [
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "v.wav",
    ]
    with pytest.raises(ValueError):
        extraction_command("ffmpeg", "a.mov", "v.wav", start=0.0, duration=0.0)
    command = whisper_command("whisper-cli", "m.bin", "v.wav", "out", language="")
    assert command[command.index("-l") + 1] == "auto" and {"-ml", "-sow", "-oj", "-pp"} <= set(command)


# --- Tâche --------------------------------------------------------------------------------------------------------------


def _fake_whisper(tmp_path, body: str):
    script = tmp_path / "whisper-cli"
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(0o755)
    return str(script)


def _speechless_media(tmp_path):
    media = tmp_path / "voix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", str(media)],
                   check=True, timeout=60)
    return str(media)


def test_a_job_cancelled_before_it_runs_launches_nothing(tmp_path):
    job = TranscriptionJob("ffmpeg", "whisper-cli", "m.bin", str(tmp_path / "absent.wav"), start=0.0, duration=2.0)
    job.cancel()
    job.run()
    assert job.snapshot().state == "cancelled"


@needs_ffmpeg
@posix_only
def test_the_job_extracts_the_window_then_reads_whisper(tmp_path):
    output = json.dumps(WHISPER_OUTPUT).replace('"', '\\"')
    # Le faux whisper vérifie qu'il reçoit un WAV, écrit la sortie de référence là où -of le demande, puis sa progression.
    whisper = _fake_whisper(tmp_path, f"""
        while [ $# -gt 0 ]; do
          case "$1" in -f) wav="$2"; shift;; -of) prefix="$2"; shift;; esac; shift
        done
        [ -s "$wav" ] || {{ echo "error: input file not found '$wav'" >&2; exit 2; }}
        printf "%s" "{output}" > "$prefix.json"
        echo "whisper_print_progress_callback: progress = 50%" >&2
        echo "whisper_print_progress_callback: progress = 100%" >&2
    """)
    job = TranscriptionJob(shutil.which("ffmpeg"), whisper, "m.bin", _speechless_media(tmp_path), start=1.0,
                           duration=2.0, session_id="s")
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "finished" and snapshot.progress == 1.0, snapshot.message
    assert snapshot.result is not None and snapshot.result.words[0] == Word("Bonjour", 1.01, 1.68)


@needs_ffmpeg
@posix_only
def test_a_failing_whisper_reports_its_error(tmp_path):
    whisper = _fake_whisper(tmp_path, """
        echo "load_backend: loaded BLAS backend" >&2
        echo "whisper_init_from_file: failed to load model" >&2
        echo "error: failed to initialize whisper context" >&2
        exit 3
    """)
    job = TranscriptionJob(shutil.which("ffmpeg"), whisper, "m.bin", _speechless_media(tmp_path), start=0.0,
                           duration=1.0)
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "failed" and snapshot.result is None
    assert snapshot.message == "error: failed to initialize whisper context"   # le bruit de chargement est écarté


@needs_ffmpeg
def test_an_unreadable_media_fails_before_whisper(tmp_path):
    broken = tmp_path / "abime.wav"
    broken.write_bytes(b"pas du son")
    job = TranscriptionJob(shutil.which("ffmpeg"), "whisper-introuvable", "m.bin", str(broken), start=0.0,
                           duration=1.0)
    job.run()
    assert job.snapshot().state == "failed" and job.snapshot().message


# --- Vraie reconnaissance -------------------------------------------------------------------------------------------------

WHISPER = find_whisper()
MODEL = find_whisper_model()
needs_whisper = pytest.mark.skipif(
    not (WHISPER and MODEL and shutil.which("say") and shutil.which("ffmpeg")),
    reason="whisper.cpp, un modèle ggml (KUT_STUDIO_WHISPER_MODEL) et la voix de synthèse « say » de macOS requis",
)


@needs_whisper
def test_whisper_recognises_a_synthetic_voice(tmp_path):
    voice = tmp_path / "voix.aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", str(voice), "Hello everyone. Welcome to simple video editing."],
                   check=True, timeout=60)
    job = TranscriptionJob(shutil.which("ffmpeg"), WHISPER, MODEL, str(voice), start=0.0, duration=6.0)
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "finished", snapshot.message
    texts = [word.text.strip(".,").lower() for word in snapshot.result.words]
    assert snapshot.result.language == "en" and {"hello", "welcome", "video", "editing"} <= set(texts)
    starts = [word.start for word in snapshot.result.words]
    assert starts == sorted(starts) and starts[-1] < 6.0
