"""Déroulé d'un parcours : médias, projet de départ, script, projet, analyses, exports, contrôle des sorties.

Chaque étape qui lance l'application la lance **par la ligne de commande** (``Kut-Studio validate | apply | describe |
export``), dans un processus à part, avec un environnement isolé (:func:`tools.recette.env.isolated_environment`) et rend ses
mesures (:class:`tools.recette.runner.RunResult`). Aucune fenêtre n'est ouverte.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import media as media_module
from .env import ISOLATED_VARIABLES, ROOT, app_command, ffmpeg_has, find_tool, machine_info
from .journeys import Capabilities, Journey, check_scale
from .probe import analyse_frames, check_output, ffprobe, warnings_for
from .runner import RunResult, run_measured

Progress = Callable[[str], None]


@dataclass
class Options:
    """Réglages d'une exécution. Aucun n'a de durée limite sauf ``timeout`` (en dernier recours, pas un seuil de test)."""

    app: str | None = None                 # exécutable de l'application (le paquet) ; défaut : les sources
    hardware: str = "cpu"                  # encodeur de l'export : cpu | auto | videotoolbox…
    timeout: float = 4 * 3600.0            # par commande : ``SIGINT`` puis arrêt, voir ``runner``
    analyse_frames: bool = True            # images noires / gelées (décode la sortie une fois)
    force_media: bool = False
    whisper: str | None = None
    whisper_model: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)   # environnement isolé (obligatoire)
    min_free_gb: float = 4.0               # garde-fou : un export est arrêté (proprement) si le disque libre tombe sous cette valeur
    max_rss_fraction: float = 0.85         # garde-fou : … ou si la mémoire de l'arbre dépasse cette part de la RAM


def capabilities(ffmpeg: str | None, whisper: str | None) -> Capabilities:
    """libass : le filtre ``subtitles`` de FFmpeg existe-t-il ? (sinon les sous-titres de piste sont remplacés par du karaoké Qt)."""
    return Capabilities(libass=ffmpeg_has(ffmpeg, "filters", "subtitles"), whisper=bool(whisper))


def build_base_project(journey: Journey, target: Path) -> None:
    """Le projet vide du parcours (cadre, cadence, pistes de départ), enregistré en ``.kut``."""
    from core.project_io import save_project
    from core.project_model import Project, Track

    width, height, fps = journey.base
    tracks = [Track(id=track_id, name=track_id, type=kind, clips=[]) for track_id, kind in journey.tracks]
    save_project(Project(name=f"Recette {journey.title}", width=width, height=height, fps=fps, tracks=tracks), str(target))


def cli(options: Options, *arguments: str) -> list[str]:
    return [*app_command(options.app), *arguments]


def _run(options: Options, logs: Path, label: str, *arguments: str, watch: tuple[Path, ...] = ()) -> RunResult:
    """Lance une commande du CLI ; ``watch`` : dossiers dont la taille cumulée (pic) est relevée (dossiers isolés, sorties)."""
    isolated = tuple(Path(options.environment[name]) for name in ISOLATED_VARIABLES if name in options.environment)
    temporary = options.environment.get("TMPDIR")
    folders = (*isolated, *((Path(temporary),) if temporary else ()), *watch)
    ram_gb = machine_info().get("ram_gb")
    return run_measured(cli(options, *arguments), env=options.environment, cwd=ROOT, log_prefix=logs / label,
                        timeout=options.timeout, watch_dirs=folders, min_free_gb=options.min_free_gb, guard_path=logs,
                        max_rss_mb=float(ram_gb) * 1024.0 * options.max_rss_fraction if ram_gb else None)


def _json_stdout(result: RunResult) -> dict[str, Any]:
    try:
        data = json.loads(Path(result.stdout_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stage(result: RunResult, **extra: Any) -> dict[str, Any]:
    data = result.to_dict()
    for name in ("stdout_tail", "command"):
        data.pop(name, None)
    if result.ok:
        data.pop("stderr_tail", None)
    data.update(extra)
    return data


def describe_stats(description: Mapping[str, Any]) -> dict[str, Any]:
    """Chiffres utiles du ``describe --json`` : durée, séquences, pistes, clips, transitions, repères."""
    sequences = description.get("sequences", []) or []
    clips = 0
    keyframes = 0
    for sequence in sequences:
        for track in sequence.get("tracks", []) or []:
            for clip in track.get("clips", []) or []:
                clips += 1
                keyframes += _count_keyframes(clip)
    return {
        "duration": (description.get("project") or {}).get("duration"), "sequences": len(sequences),
        "tracks": sum(len(s.get("tracks", []) or []) for s in sequences), "clips": clips, "keyframes": keyframes,
        "transitions": sum(len(s.get("transitions", []) or []) for s in sequences),
        "markers": sum(len(s.get("markers", []) or []) for s in sequences),
        "media": len(description.get("media", []) or []),
        "sequence_durations": {str(s.get("name")): s.get("duration") for s in sequences},
    }


def _count_keyframes(clip: Any) -> int:
    """Images clés d'un clip décrit : transform et graphique (``transform_keyframes``), étalonnage et animations (``animation``)."""
    if not isinstance(clip, dict):
        return 0
    return sum(len(clip.get(key) or []) for key in ("transform_keyframes", "animation"))


def run_journey(journey: Journey, scale: str, workdir: Path, run_dir: Path, options: Options, progress: Progress | None = None) -> dict[str, Any]:
    """Déroule le parcours et rend son rapport (jamais une exception pour un échec de parcours : il est dans le rapport)."""
    say = progress or (lambda message: None)
    check_scale(scale)
    folder = workdir / f"{journey.name}-{scale}"
    logs = run_dir / "logs" / journey.name
    logs.mkdir(parents=True, exist_ok=True)
    (folder / "out").mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe_path = find_tool("ffmpeg"), find_tool("ffprobe")
    caps = capabilities(ffmpeg, options.whisper)
    report: dict[str, Any] = {"journey": journey.name, "title": journey.title, "scale": scale, "ok": False, "stages": {},
                              "problems": [], "capabilities": {"libass": caps.libass, "whisper": caps.whisper}}
    stages: dict[str, Any] = report["stages"]

    def fail(stage: str, message: str) -> dict[str, Any]:
        report["problems"].append(f"{stage}: {message}")
        return report

    # 1. Médias.
    items = journey.media(scale)
    say(f"[{journey.name}] médias ({len(items)})")
    try:
        manifest = media_module.generate(items, folder, ffmpeg=ffmpeg, progress=say, force=options.force_media)
    except media_module.MediaError as error:
        stages["media"] = {"ok": False, "error": str(error)[:600]}
        return fail("media", str(error)[:300])
    stages["media"] = {
        "ok": True, "seconds": manifest["generation_seconds"], "bytes": manifest["total_bytes"], "generated": manifest["generated"],
        "reused": manifest["reused"], "recipe_digest": manifest["recipe_digest"], "ffmpeg": manifest["ffmpeg"],
        "video_encoder": manifest["video_encoder"],
        "fingerprints": {key: entry["sha256"] for key, entry in manifest["items"].items()},
        "per_item_seconds": {key: entry["seconds"] for key, entry in manifest["items"].items() if key in manifest["generated"]},
    }
    # 2. Projet de départ et script.
    base, script_path, project = folder / "base.kut", folder / "script.json", folder / "project.kut"
    began = time.monotonic()
    build_base_project(journey, base)
    script_path.write_text(json.dumps(journey.script(scale, caps), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stages["base"] = {"ok": True, "seconds": round(time.monotonic() - began, 3)}
    # 3. Construction par la ligne de commande.
    say(f"[{journey.name}] construction (validate, apply, describe)")
    checked = _run(options, logs, "validate-script", "validate", str(script_path), "--json")
    stages["validate_script"] = _stage(checked)
    if not checked.ok:
        return fail("validate_script", _first_message(checked))
    applied = _run(options, logs, "apply", "apply", str(script_path), str(base), "-o", str(project), "--overwrite", "--json")
    payload = _json_stdout(applied)
    stages["apply"] = _stage(applied, operations=payload.get("operations"), adjustments=len(payload.get("adjustments", []) or []),
                             project_bytes=project.stat().st_size if project.is_file() else None)
    if not applied.ok:
        return fail("apply", _first_message(applied))
    loaded = _run(options, logs, "validate-project", "validate", str(project), "--json")
    stages["validate_project"] = _stage(loaded, dropped=_json_stdout(loaded).get("dropped"))
    if not loaded.ok:
        return fail("validate_project", f"le projet construit perd des entrées (code {loaded.returncode})")
    described = _run(options, logs, "describe", "describe", str(project), "--json")
    description = _json_stdout(described).get("description", {})
    stats = describe_stats(description) if isinstance(description, dict) else {}
    stages["describe"] = _stage(described, **stats)
    if not described.ok:
        return fail("describe", _first_message(described))
    report["project"] = stats
    # 4. Analyses du moteur sur les médias générés (tempo, synchronisation).
    analyses = journey.analyses(scale)
    if analyses:
        stages["analysis"] = _analyses(journey, scale, folder, logs, options, analyses)
        report["problems"] += [f"analysis: {p}" for p in stages["analysis"].get("problems", [])]
    if journey.name == "tiktok":
        from .whisper_check import transcribe

        stages["transcription"] = transcribe(folder / "media" / "voix.wav", logs / "whisper", ffmpeg, options.whisper,
                                             options.whisper_model, options.environment)
        say(f"[{journey.name}] transcription : {stages['transcription']['status']}")
    # 5. Exports.
    exports: dict[str, Any] = {}
    stages["exports"] = exports
    for spec in journey.exports(scale, caps):
        say(f"[{journey.name}] export {spec.name} ({spec.preset})")
        output = folder / "out" / spec.output
        output.unlink(missing_ok=True)
        arguments = ["export", str(project), "-o", str(output), "--preset", spec.preset, "--hardware", options.hardware,
                     "--overwrite", "--quiet", "--json"]
        if spec.sequence:
            arguments += ["--sequence", spec.sequence]
        result = _run(options, logs, f"export-{spec.name}", *arguments, watch=(folder / "out",))
        entry = _stage(result, preset=spec.preset, sequence=spec.sequence)
        data = _json_stdout(result)
        entry["encoder"], entry["fallback_reason"] = data.get("encoder"), data.get("fallback_reason")
        entry["output_bytes"] = output.stat().st_size if output.is_file() else None
        exports[spec.name] = entry
        if not result.ok or not output.is_file():
            report["problems"].append(f"export {spec.name}: " + _first_message(result))
            continue
        expected = stats.get("sequence_durations", {}).get(spec.sequence) if spec.sequence else stats.get("duration")
        summary = ffprobe(ffprobe_path, output)
        frames: dict[str, Any] = {}
        if options.analyse_frames:
            began = time.monotonic()
            frames = analyse_frames(ffmpeg, output, summary.get("duration"))
            frames["seconds"] = round(time.monotonic() - began, 2)
        problems = check_output(summary, frames, expected_duration=expected, expected_size=spec.expected_size,
                                expect_audio=spec.expect_audio)
        entry["probe"], entry["frames"], entry["expected_duration"], entry["problems"] = summary, frames, expected, problems
        entry["warnings"] = warnings_for(frames)
        report["problems"] += [f"export {spec.name}: {p}" for p in problems]
    report["ok"] = not report["problems"] and bool(exports) and all(item.get("ok") for item in exports.values())
    return report


def _first_message(result: RunResult) -> str:
    """Une ligne lisible de l'échec (code, signal, fin de la sortie d'erreur) : jamais la sortie entière."""
    tail = [line for line in result.stderr_tail.strip().splitlines() if line.strip()][-4:]
    cause = (f"arrêt par le garde-fou ({result.aborted})" if result.aborted else f"signal {result.signal_name}" if result.crashed
             else "délai dépassé" if result.timed_out else f"code {result.returncode}")
    return (f"{cause} : " + " | ".join(tail))[:500]


def _analyses(journey: Journey, scale: str, folder: Path, logs: Path, options: Options, specs: list[Any]) -> dict[str, Any]:
    """Tempo / synchronisation par le moteur, dans un processus à part (mesure de durée et de mémoire isolées)."""
    results: dict[str, Any] = {"ok": True, "problems": [], "items": []}
    for number, spec in enumerate(specs):
        arguments = [sys.executable, "-m", "tools.recette.analyze", spec.kind, str(folder), json.dumps(spec.params)]
        run = run_measured(arguments, env=options.environment, cwd=ROOT, log_prefix=logs / f"analysis-{number}-{spec.kind}",
                           timeout=min(options.timeout, 3600.0))
        try:
            data = json.loads(Path(run.stdout_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        entry = {"kind": spec.kind, "seconds": run.elapsed_s, "peak_rss_mb": run.peak_rss_mb, "returncode": run.returncode, "crashed": run.crashed,
                 "signal": run.signal_name, "aborted": run.aborted, **data}
        results["items"].append(entry)
        if not run.ok or not data.get("ok", False):
            results["ok"] = False
            results["problems"].append(f"{spec.kind}: " + str(data.get("message") or _first_message(run))[:300])
    return results


__all__ = ["Options", "capabilities", "build_base_project", "run_journey", "describe_stats"]
