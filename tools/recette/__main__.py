"""Ligne de commande du banc : ``python -m tools.recette COMMANDE``.

    run      les parcours (médias, construction, exports, mesures) et le rapport
    smoke    le smoke test de la CI : un montage de 10 s exporté en CPU, vérifié par ffprobe
    gen      seulement générer les médias
    verify   vérifier les empreintes des médias déjà générés
    report   re-rendre le Markdown d'un rapport JSON (avec comparaison éventuelle)
    crashes  plantages par heure d'après le journal local
    thermal  relevé thermique d'une séance soutenue (macOS)

Rien ici n'ouvre de fenêtre ni ne lit la configuration de l'utilisateur (sauf ``crashes``, en lecture seule, sur sa demande).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from . import crashes as crashes_module
from . import media as media_module
from . import pipeline, report as report_module, thermal
from .env import (EnvironmentRefused, ISOLATED_VARIABLES, ffmpeg_has, ffmpeg_version, find_tool, free_disk_gb, isolated_environment,
                  machine_info, machine_slug, resolve_workdir)
from .journeys import MAIN_JOURNEYS, SCALES, registry

FULL_DISK_WARNING_GB = 20.0


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _journeys(names: str) -> list[str]:
    available = registry()
    chosen = [name.strip() for name in names.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in available]
    if not chosen or unknown:
        raise SystemExit(f"Parcours inconnu : {', '.join(unknown) or '(vide)'} (attendu : {', '.join(available)})")
    return chosen


def _common(sub: argparse.ArgumentParser, journeys: str = ",".join(MAIN_JOURNEYS)) -> None:
    sub.add_argument("--journeys", default=journeys, help="parcours, séparés par des virgules")
    sub.add_argument("--scale", choices=SCALES, default="full", help="full = durées réelles (20 min), quick = court")
    sub.add_argument("--workdir", default=None, help="dossier de travail (médias, sorties, rapports), hors du dépôt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.recette", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="exécute les parcours")
    _common(run)
    run.add_argument("--hardware", default="cpu", help="encodeur de l'export : cpu (défaut, comparable), auto, videotoolbox…")
    run.add_argument("--app", default=None, help="exécutable de l'application (le paquet) ; défaut : les sources (main.py)")
    run.add_argument("--reference", default=None, help="rapport JSON de référence (comparaison par ratios)")
    run.add_argument("--save-reference", default=None, metavar="FICHIER", help="copie le rapport comme référence")
    run.add_argument("--no-frames", action="store_true", help="saute l'analyse image noire / gelée")
    run.add_argument("--force-media", action="store_true", help="régénère les médias même s'ils sont à jour")
    run.add_argument("--whisper", default=None, help="chemin de whisper-cli")
    run.add_argument("--whisper-model", default=None, help="chemin d'un modèle ggml-*.bin")
    run.add_argument("--timeout", type=float, default=4 * 3600.0, help="délai par commande, en secondes (dernier recours)")
    smoke = commands.add_parser("smoke", help="smoke test : 10 s exportées en CPU")
    smoke.add_argument("--workdir", default=None)
    smoke.add_argument("--app", default=None)
    smoke.add_argument("--timeout", type=float, default=900.0)
    gen = commands.add_parser("gen", help="génère les médias")
    _common(gen)
    gen.add_argument("--force", action="store_true")
    verify = commands.add_parser("verify", help="vérifie les empreintes des médias")
    _common(verify)
    rep = commands.add_parser("report", help="re-rend un rapport")
    rep.add_argument("report")
    rep.add_argument("--reference", default=None)
    cr = commands.add_parser("crashes", help="plantages par heure d'après le journal")
    cr.add_argument("--log-dir", default=None, help="dossier du journal (défaut : celui de l'application, lecture seule)")
    cr.add_argument("--reports-dir", default=None, help="dossier des rapports de plantage système (macOS : ~/Library/Logs/DiagnosticReports)")
    cr.add_argument("--session", action="append", default=[], metavar="DEBUT/FIN", help="séance relevée à la main (répétable)")
    th = commands.add_parser("thermal", help="relevé thermique (macOS)")
    th.add_argument("--minutes", type=float, default=20.0)
    th.add_argument("--interval", type=float, default=30.0)
    th.add_argument("--output", default="thermique.jsonl")
    return parser


def _tools(options: pipeline.Options) -> dict[str, object]:
    ffmpeg = find_tool("ffmpeg")
    whisper, model = None, None
    try:
        from .whisper_check import discover

        whisper, model = discover(options.whisper, options.whisper_model)
    except Exception:                                 # noqa: BLE001 - la découverte ne doit pas empêcher la recette
        pass
    return {"ffmpeg": ffmpeg_version(ffmpeg), "ffprobe": bool(find_tool("ffprobe")), "libass": ffmpeg_has(ffmpeg, "filters", "subtitles"),
            "whisper": "oui" if whisper and model else ("sans modèle" if whisper else "non"), "app": options.app or "sources (main.py)"}


def cmd_run(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    names = _journeys(args.journeys)
    run_dir = workdir / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    environment = isolated_environment(run_dir / "env")
    options = pipeline.Options(app=args.app, hardware=args.hardware, timeout=args.timeout, analyse_frames=not args.no_frames,
                               force_media=args.force_media, whisper=args.whisper, whisper_model=args.whisper_model, environment=environment)
    if args.scale == "full" and free_disk_gb(workdir) < FULL_DISK_WARNING_GB:
        _say(f"Attention : moins de {FULL_DISK_WARNING_GB:.0f} Go libres sur le volume du dossier de travail.")
    results = {}
    registry_ = registry()
    for name in names:
        began = time.monotonic()
        results[name] = pipeline.run_journey(registry_[name], args.scale, workdir, run_dir, options, progress=_say)
        _say(f"[{name}] {'OK' if results[name]['ok'] else 'ÉCHEC'} en {time.monotonic() - began:.0f} s")
    machine = machine_info()
    isolation = {variable: environment[variable] for variable in ISOLATED_VARIABLES}
    full = report_module.build_report(machine, _tools(options), results, scale=args.scale, hardware=args.hardware, isolation=isolation)
    if args.reference:
        full["comparison"] = report_module.compare(full, report_module.load_report(Path(args.reference)))
    json_path, md_path = report_module.write_report(full, run_dir)
    if args.save_reference:
        reference = dict(full)
        reference["reference_created"] = full["created"]
        Path(args.save_reference).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_reference).write_text(json.dumps(report_module.scrub(reference), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                                             encoding="utf-8")
    print(f"Rapport : {md_path}\nJSON    : {json_path}\nMachine : {machine_slug(machine)}")
    return 0 if full["summary"]["ok"] else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    run_dir = workdir / "runs" / ("smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    environment = isolated_environment(run_dir / "env")
    options = pipeline.Options(app=args.app, timeout=args.timeout, environment=environment)
    started = pipeline.run_measured(pipeline.cli(options, "presets"), env=environment, cwd=pipeline.ROOT, log_prefix=run_dir / "logs" / "startup",
                                    timeout=120.0)
    if not started.ok:
        print(f"Démarrage en échec : code {started.returncode} {started.stderr_tail[-300:]}", file=sys.stderr)
        return 1
    result = pipeline.run_journey(registry()["smoke"], "quick", workdir, run_dir, options, progress=_say)
    print(json.dumps({"ok": result["ok"], "problems": result["problems"]}, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_gen(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    ffmpeg = find_tool("ffmpeg")
    for name in _journeys(args.journeys):
        folder = workdir / f"{name}-{args.scale}"
        manifest = media_module.generate(registry()[name].media(args.scale), folder, ffmpeg=ffmpeg, progress=_say, force=args.force)
        _say(f"[{name}] {manifest['total_bytes']} octets, {manifest['generation_seconds']} s, recette {manifest['recipe_digest'][:12]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    bad = 0
    for name in _journeys(args.journeys):
        states = media_module.verify(registry()[name].media(args.scale), workdir / f"{name}-{args.scale}")
        for key, state in states.items():
            print(f"{name}/{key}: {state}")
            bad += state != "ok"
    return 1 if bad else 0


def cmd_report(args: argparse.Namespace) -> int:
    data = report_module.load_report(Path(args.report))
    if args.reference:
        data["comparison"] = report_module.compare(data, report_module.load_report(Path(args.reference)))
    print(report_module.render_markdown(report_module.scrub(data)))
    return 0


def cmd_crashes(args: argparse.Namespace) -> int:
    if args.log_dir:
        directory = Path(args.log_dir)
    else:
        from core.platform_paths import user_log_dir

        directory = user_log_dir()
    events = crashes_module.read_logs(directory)
    sessions = [crashes_module.parse_session(text) for text in args.session] or None
    summary = crashes_module.summarize(events, sessions, Path(args.reports_dir) if args.reports_dir else None)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_thermal(args: argparse.Namespace) -> int:
    print(json.dumps(thermal.run(args.minutes, args.interval, Path(args.output)), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "smoke": cmd_smoke, "gen": cmd_gen, "verify": cmd_verify, "report": cmd_report, "crashes": cmd_crashes,
                "thermal": cmd_thermal}
    try:
        return handlers[args.command](args)
    except (EnvironmentRefused, ValueError, RuntimeError) as error:
        print(f"Refusé : {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
