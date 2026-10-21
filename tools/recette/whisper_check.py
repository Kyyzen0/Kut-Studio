"""Transcription par whisper.cpp, **optionnelle** : le banc se dégrade proprement quand elle n'est pas disponible.

États rendus dans ``status`` : ``ok`` (mots comptés), ``skipped_no_whisper`` (``whisper-cli`` introuvable), ``skipped_no_model``
(aucun modèle ``ggml-*.bin``), ``failed`` (le programme a échoué ou n'a rien écrit). Aucun de ces états ne fait échouer un
parcours : le script du parcours pose ses sous-titres avec un texte connu et ne dépend jamais de whisper. La voix de test est
synthétique (syllabes) : whisper n'y reconnaît pas forcément de texte ; la mesure porte sur le **fonctionnement** et la durée.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .runner import run_measured


def discover(whisper: str | None, model: str | None, environment: Mapping[str, str] | None = None) -> tuple[str | None, str | None]:
    """``whisper-cli`` et modèle, comme l'application (réglage, variables, dossier de données **réel en lecture seule**, PATH)."""
    from core.transcription import find_whisper, find_whisper_model

    return find_whisper(whisper or ""), find_whisper_model(model or "", environment=_lookup_environment(environment))


def _lookup_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    """Environnement de recherche du modèle : la variable de l'appelant (``KUT_STUDIO_WHISPER_MODEL``) et RIEN d'autre, donc le dossier
    de données réel par défaut (lecture seule) et jamais un dossier isolé du banc ni une autre variable."""
    source = environment if environment is not None else os.environ
    return {name: source[name] for name in ("KUT_STUDIO_WHISPER_MODEL",) if source.get(name)}


def transcribe(voice: Path, work: Path, ffmpeg: str | None, whisper: str | None, model: str | None, env: Mapping[str, str],
               timeout: float = 1800.0) -> dict[str, Any]:
    from core.transcription import extraction_command, whisper_command

    cli, found_model = discover(whisper, model, os.environ)
    if not cli:
        return {"status": "skipped_no_whisper", "message": "whisper-cli introuvable (KUT_STUDIO_WHISPER ou --whisper)"}
    if not found_model:
        return {"status": "skipped_no_model", "message": "aucun modèle ggml-*.bin (KUT_STUDIO_WHISPER_MODEL ou --whisper-model)"}
    if not ffmpeg or not voice.is_file():
        return {"status": "failed", "message": "FFmpeg ou la voix de test manque"}
    work.mkdir(parents=True, exist_ok=True)
    wav, prefix = work / "voix16k.wav", work / "whisper"
    extract = run_measured(extraction_command(ffmpeg, str(voice), str(wav), start=0.0, duration=3600.0), env=env, log_prefix=work / "extract",
                           timeout=timeout)
    if not extract.ok:
        return {"status": "failed", "message": "extraction FFmpeg en échec", "crashed": extract.crashed, "signal": extract.signal_name,
                "returncode": extract.returncode}
    run = run_measured(whisper_command(cli, found_model, str(wav), str(prefix)), env=env, log_prefix=work / "whisper-run", timeout=timeout)
    result: dict[str, Any] = {"crashed": run.crashed, "signal": run.signal_name, "returncode": run.returncode, "seconds": run.elapsed_s, "peak_rss_mb": run.peak_rss_mb, "model": Path(found_model).name}
    try:
        raw = json.loads(Path(f"{prefix}.json").read_text(encoding="utf-8"))
        words = [s for s in raw.get("transcription", []) if str(s.get("text", "")).strip()]
    except (OSError, ValueError):
        return {**result, "status": "failed", "message": f"whisper-cli code {run.returncode}, aucune sortie lisible"}
    return {**result, "status": "ok" if run.ok else "failed", "words": len(words)}
