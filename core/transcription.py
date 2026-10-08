"""Transcription locale d'une voix par whisper.cpp : les mots prononcés, datés, pour des sous-titres karaoké.

Kut-Studio n'embarque ni moteur de reconnaissance ni modèle, et n'envoie rien sur le réseau : whisper.cpp est un
programme externe (``whisper-cli``), trouvé comme FFmpeg — chemin réglé dans les préférences, variable
``KUT_STUDIO_WHISPER``, dossier ``bin/`` de l'application, puis le ``PATH`` (Homebrew : ``brew install whisper-cpp``).
Le modèle est un fichier ``ggml-*.bin`` choisi dans les préférences, ou posé dans le dossier ``whisper`` des données
de l'application (:func:`model_directory`).

Déroulé d'une transcription (:class:`TranscriptionJob`, sur la file d'analyse) :

1. FFmpeg extrait la fenêtre du clip en WAV mono 16 kHz (le format que whisper.cpp lit) dans un dossier temporaire ;
2. ``whisper-cli -ml 1 -sow -oj`` écrit un segment **par mot**, daté en millisecondes depuis le début de l'extrait
   (vérifié avec whisper.cpp 1.9.4 : ``{"offsets": {"from": 690, "to": 1330}, "text": " tous,"}``) ; la langue est
   détectée (``-l auto``) sauf si on l'impose ;
3. les mots sont ramenés au temps du média (début de la fenêtre + décalage), puis groupés en lignes de sous-titres
   courtes (:func:`group_words`).

Les mots gardent leur ponctuation (« tous, ») : c'est ce que le sous-titre affiche.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .platform_paths import user_data_dir
from .process_supervisor import supervised_popen
from .tool_paths import bundled_tool_path

WHISPER_ENV = "KUT_STUDIO_WHISPER"
MODEL_ENV = "KUT_STUDIO_WHISPER_MODEL"
SAMPLE_RATE = 16000
"""whisper.cpp ne lit que du 16 kHz : FFmpeg rééchantillonne à l'extraction."""

MAX_LINE_CHARS = 32
"""Longueur d'une ligne de sous-titre de vidéo verticale : deux à cinq mots, lisibles d'un coup d'œil."""
MAX_LINE_SECONDS = 3.0
MAX_PAUSE = 0.6
"""Une pause plus longue entre deux mots commence une nouvelle ligne."""

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")
_SENTENCE_END = (".", "!", "?", "…")
_EXTRACTION_SHARE = 0.1
"""Part de la progression réservée à l'extraction par FFmpeg (rapide : la reconnaissance domine)."""


@dataclass(frozen=True)
class Word:
    """Un mot reconnu, en secondes du média (ponctuation comprise)."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Transcript:
    """Résultat d'une transcription : langue détectée (code ISO, ``""`` si inconnue) et mots dans l'ordre."""

    language: str
    words: tuple[Word, ...]


# ---------------------------------------------------------------------------
# Découverte
# ---------------------------------------------------------------------------


def find_whisper(configured: str = "", *, environment: Mapping[str, str] | None = None) -> str | None:
    """Chemin de ``whisper-cli`` : réglage, variable ``KUT_STUDIO_WHISPER``, dossier ``bin/`` embarqué, ``PATH``."""
    env = environment if environment is not None else os.environ
    for candidate in (configured, env.get(WHISPER_ENV, "")):
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser().resolve())
    return bundled_tool_path("whisper-cli", environment=env) or shutil.which("whisper-cli")


def model_directory(*, environment: Mapping[str, str] | None = None) -> Path:
    """Dossier où un modèle ``ggml-*.bin`` est trouvé sans réglage (données de l'application, sous-dossier ``whisper``)."""
    return user_data_dir(environment=environment) / "whisper"


def find_whisper_model(configured: str = "", *, environment: Mapping[str, str] | None = None) -> str | None:
    """Modèle à utiliser : réglage, variable ``KUT_STUDIO_WHISPER_MODEL``, puis le premier ``ggml-*.bin`` (par ordre
    alphabétique) du :func:`model_directory`."""
    env = environment if environment is not None else os.environ
    for candidate in (configured, env.get(MODEL_ENV, "")):
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser().resolve())
    folder = model_directory(environment=env)
    models = sorted(folder.glob("ggml-*.bin")) if folder.is_dir() else []
    return str(models[0]) if models else None


# ---------------------------------------------------------------------------
# Commandes et lecture du résultat
# ---------------------------------------------------------------------------


def extraction_command(ffmpeg: str, media: str, wav: str, *, start: float, duration: float) -> list[str]:
    """FFmpeg : ``duration`` secondes du média à partir de ``start``, en WAV mono 16 kHz."""
    if duration <= 0:
        raise ValueError("Durée de transcription nulle")
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", media,
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", wav,
    ]


def whisper_command(whisper: str, model: str, wav: str, output_prefix: str, *, language: str = "auto") -> list[str]:
    """``whisper-cli`` : un segment par mot (``-ml 1 -sow``), JSON (``-oj``), progression sur la sortie d'erreur."""
    return [
        whisper, "-m", model, "-f", wav, "-l", language or "auto",
        "-ml", "1", "-sow", "-sns", "-oj", "-of", output_prefix, "-np", "-pp",
    ]


def parse_whisper_json(raw: Mapping, offset: float = 0.0) -> Transcript:
    """Mots d'une sortie ``-oj`` de whisper.cpp, ramenés au temps du média (``offset`` : début de l'extrait).

    Les segments vides et les annotations (« [Musique] », « (rires) ») sont écartés : ils ne se disent pas."""
    words: list[Word] = []
    for segment in raw.get("transcription", ()) or ():
        text = str(segment.get("text", "")).strip()
        offsets = segment.get("offsets") or {}
        if not text or _is_annotation(text) or "from" not in offsets or "to" not in offsets:
            continue
        start = offset + float(offsets["from"]) / 1000.0
        end = max(start, offset + float(offsets["to"]) / 1000.0)
        words.append(Word(text, round(start, 3), round(end, 3)))
    language = str((raw.get("result") or {}).get("language", "") or "")
    return Transcript(language, tuple(words))


def _is_annotation(text: str) -> bool:
    return (text[0], text[-1]) in {("[", "]"), ("(", ")"), ("*", "*")}


def group_words(
    words: Sequence[Word], *, max_chars: int = MAX_LINE_CHARS, max_seconds: float = MAX_LINE_SECONDS,
    max_pause: float = MAX_PAUSE,
) -> list[tuple[Word, ...]]:
    """Lignes de sous-titres : une ligne finit après une fin de phrase, avant une pause, ou quand elle deviendrait
    trop longue (caractères) ou trop durable (secondes). Un mot seul trop long forme sa propre ligne."""
    lines: list[tuple[Word, ...]] = []
    current: list[Word] = []
    for word in words:
        if current:
            text = " ".join(item.text for item in (*current, word))
            if (
                current[-1].text.endswith(_SENTENCE_END) or word.start - current[-1].end > max_pause
                or len(text) > max_chars or word.end - current[0].start > max_seconds
            ):
                lines.append(tuple(current))
                current = []
        current.append(word)
    if current:
        lines.append(tuple(current))
    return lines


# ---------------------------------------------------------------------------
# Tâche
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranscriptionSnapshot:
    """État d'une transcription, lu par l'interface : ``queued``, ``running``, ``finished``, ``cancelled``, ``failed``."""

    state: str
    progress: float
    message: str
    result: Transcript | None


class TranscriptionJob:
    """Transcription d'une fenêtre de média pour la file d'analyse : progression, annulation, résultat."""

    def __init__(
        self, ffmpeg: str, whisper: str, model: str, media: str, *, start: float, duration: float,
        language: str = "auto", session_id: str = "",
    ) -> None:
        self.session_id = session_id
        self._start = float(start)
        self._ffmpeg, self._whisper, self._model, self._media = ffmpeg, whisper, model, media
        self._language = language
        self._duration = float(duration)
        extraction_command(ffmpeg, media, "x.wav", start=start, duration=duration)   # durée validée dès la création
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"
        self._progress = 0.0
        self._message = ""
        self._result: Transcript | None = None
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state == "queued":
                self._state = "cancelled"
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def snapshot(self) -> TranscriptionSnapshot:
        with self._lock:
            return TranscriptionSnapshot(self._state, self._progress, self._message, self._result)

    def run(self, token: object = None) -> None:
        with self._lock:
            if self._cancel.is_set() or self._state == "cancelled":
                self._state = "cancelled"
                return
            self._state = "running"
        try:
            with tempfile.TemporaryDirectory(prefix="kut-whisper-") as folder:
                transcript = self._transcribe(Path(folder))
        except Exception as error:  # noqa: BLE001 - un média ou un modèle illisible ne doit pas bloquer la file
            self._finish("cancelled" if self._cancel.is_set() else "failed", message=str(error) or type(error).__name__)
            return
        if transcript is None or self._cancel.is_set():
            self._finish("cancelled")
            return
        self._finish("finished", result=transcript, progress=1.0)

    def _transcribe(self, folder: Path) -> Transcript | None:
        wav = folder / "voix.wav"
        command = extraction_command(self._ffmpeg, self._media, str(wav), start=self._start, duration=self._duration)
        text, code = self._execute(command, 0.0, 0.0)
        if self._cancel.is_set():
            return None
        if code != 0 or not wav.is_file():
            raise RuntimeError(_last_lines(text, "FFmpeg n'a pas pu extraire le son"))
        self._report(_EXTRACTION_SHARE)
        prefix = folder / "mots"
        command = whisper_command(self._whisper, self._model, str(wav), str(prefix), language=self._language)
        text, code = self._execute(command, _EXTRACTION_SHARE, 1.0 - _EXTRACTION_SHARE)
        if self._cancel.is_set():
            return None
        output = prefix.with_suffix(".json")
        if code != 0 or not output.is_file():
            raise RuntimeError(_last_lines(text, "whisper.cpp a échoué sans message"))
        raw = json.loads(output.read_text(encoding="utf-8", errors="replace"))
        return parse_whisper_json(raw, self._start)

    def _execute(self, command: list[str], base: float, share: float) -> tuple[str, int]:
        """Lance ``command`` (stderr lu ligne à ligne pour la progression) ; l'annulation tue le processus."""
        collected: list[str] = []
        with supervised_popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        ) as process:
            with self._lock:
                self._process = process
            stream = process.stderr
            if stream is None:  # ``stderr=PIPE`` garantit le tube : ce cas n'arrive pas
                raise RuntimeError("Sortie d'erreur illisible")
            try:
                for line in stream:
                    collected.append(line)
                    if self._cancel.is_set():
                        process.kill()
                        break
                    match = _PROGRESS.search(line)
                    if match:
                        self._report(base + share * int(match.group(1)) / 100.0)
                code = process.wait()
            finally:
                with self._lock:
                    self._process = None
        return "".join(collected), code

    def _report(self, fraction: float) -> None:
        with self._lock:
            self._progress = max(self._progress, min(0.99, fraction))

    def _finish(
        self, state: str, *, result: Transcript | None = None, message: str = "", progress: float | None = None,
    ) -> None:
        with self._lock:
            self._state = state
            self._result = result
            self._message = message
            if progress is not None:
                self._progress = progress


def _last_lines(text: str, fallback: str, count: int = 3) -> str:
    noise = ("load_backend", "ggml_", "whisper_init", "whisper_model_load", "whisper_backend", "whisper_print")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(noise)]
    return " | ".join(lines[-count:]) or fallback


__all__ = [
    "MAX_LINE_CHARS", "MAX_LINE_SECONDS", "MAX_PAUSE", "MODEL_ENV", "SAMPLE_RATE", "Transcript", "TranscriptionJob",
    "TranscriptionSnapshot", "WHISPER_ENV", "Word", "extraction_command", "find_whisper", "find_whisper_model",
    "group_words", "model_directory", "parse_whisper_json", "whisper_command",
]
