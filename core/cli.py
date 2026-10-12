"""Ligne de commande de Kut-Studio, **sans fenêtre** (ADR-0005, étape 3) : ``Kut-Studio info | validate | presets | export``.

L'application est elle-même le CLI : ``main.py`` route ``sys.argv[1]`` ici **avant** Qt, avec le même binaire et la même installation
(pas de second programme). Rien ici n'ouvre de fenêtre ni de boucle d'événements ; l'export passe par
:func:`core.export_runner.run_export`, donc par exactement la commande FFmpeg de l'interface.

Contrat pour les scripts :

* ``--json`` : **un** objet JSON sur la sortie standard (clé ``kut_cli`` = version du schéma), et rien d'autre ; les messages
  humains vont sur la sortie d'erreur ;
* codes de sortie stables : ``0`` réussi, ``1`` échec, ``2`` (``validate``) projet chargé **avec des entrées ignorées**, ``64`` mauvais
  usage de la commande, ``130`` interrompu (Ctrl-C : l'export en cours est annulé proprement) ;
* un projet n'est **jamais** modifié par ces commandes, et un export ne remplace jamais son propre projet ni un fichier existant sans
  ``--overwrite``.

Les clés JSON et les noms de sections sont stables ; les messages sont traduits (``--lang``, ``KUT_STUDIO_LANG``, sinon ``LANG``).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

COMMANDS = ("info", "validate", "presets", "export")
SCHEMA_VERSION = 1
"""Version du schéma des sorties ``--json`` : elle ne change que par une rupture, annoncée dans le journal des modifications."""

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DROPPED = 2
EXIT_USAGE = 64
EXIT_INTERRUPTED = 130


_GLOBAL_OPTIONS_WITH_VALUE = ("--lang",)


def is_cli_invocation(argv: Sequence[str]) -> bool:
    """``True`` si ``argv`` (``sys.argv``) demande une commande du CLI plutôt que l'interface.

    La commande est le premier argument qui n'est pas une option globale (``--lang fr``) : ``Kut-Studio --lang en info p.kut`` est
    une commande, comme ``Kut-Studio info p.kut --lang en``. Un projet nommé ``info`` (sans extension) s'ouvre par ``./info``.
    """
    tokens = list(argv[1:])
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2                                  # l'option et sa valeur
        elif token.startswith(tuple(f"{name}=" for name in _GLOBAL_OPTIONS_WITH_VALUE)):
            index += 1
        else:
            return token in COMMANDS
    return False


class _UsageError(Exception):
    """Mauvais usage de la ligne de commande (argument manquant, option inconnue)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D401 - argparse appelle ``error`` puis quitte : ici on lève, ``run`` rend le code 64
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="Kut-Studio", description="Kut-Studio en ligne de commande (sans fenêtre).")
    parser.add_argument("--lang", choices=("fr", "en", "es"), help="langue des messages")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--json", action="store_true", help="un objet JSON sur la sortie standard")
        # Acceptée avant ou après la commande ; SUPPRESS : ne pas écraser la valeur donnée avant la commande.
        sub.add_argument("--lang", choices=("fr", "en", "es"), default=argparse.SUPPRESS, help="langue des messages")

    info = commands.add_parser("info", help="résumé d'un projet")
    info.add_argument("project", help="fichier .kut")
    common(info)

    validate = commands.add_parser("validate", help="vérifie qu'un projet s'ouvre, et ce qu'il a perdu en route")
    validate.add_argument("project", help="fichier .kut")
    common(validate)

    presets = commands.add_parser("presets", help="les préréglages d'export")
    common(presets)

    export = commands.add_parser("export", help="exporte un projet sans ouvrir de fenêtre")
    export.add_argument("project", help="fichier .kut")
    export.add_argument("-o", "--output", required=True, help="fichier vidéo à écrire")
    export.add_argument("--preset", default=None, help="identifiant du préréglage (voir « presets » ; défaut : h264_1080p)")
    export.add_argument("--sequence", default=None, help="identifiant ou nom de la séquence (défaut : la séquence active)")
    export.add_argument("--hardware", default=None, help="encodeur : cpu, auto, videotoolbox… (défaut : celui du préréglage)")
    export.add_argument("--master-gain-db", type=float, default=0.0, help="gain du Master en dB (défaut : 0)")
    export.add_argument("--master-mute", action="store_true", help="exporte sans le son")
    export.add_argument("--overwrite", action="store_true", help="remplace le fichier de sortie s'il existe")
    export.add_argument("--no-atomic", action="store_true",
                        help="écrit directement le fichier de sortie (par défaut : fichier caché puis renommage)")
    export.add_argument("--quiet", action="store_true", help="pas de progression")
    common(export)
    return parser


# --- Langue et sorties ------------------------------------------------------------------------------------------------


def _choose_language(requested: str | None) -> None:
    from ui import i18n

    candidates = [requested, os.environ.get("KUT_STUDIO_LANG"), (os.environ.get("LANG") or "")[:2]]
    for code in candidates:
        if code and code in i18n.available_languages():
            i18n.set_language(code)
            return


def _t(key: str, **values: Any) -> str:
    from ui import i18n

    return i18n.translate(key, **values)


class _Output:
    """Sorties du CLI : en ``--json`` un seul objet sur stdout et le texte humain sur stderr ; sinon le texte sur stdout."""

    def __init__(self, as_json: bool, out: Any, err: Any) -> None:
        self.as_json = as_json
        self.out = out
        self.err = err
        self.exit = EXIT_FAILED          # le code que ``_load`` pose quand il échoue (échec déjà signalé)

    def line(self, text: str) -> None:
        print(text, file=self.err if self.as_json else self.out, flush=True)

    def warning(self, text: str) -> None:
        print(text, file=self.err, flush=True)

    def payload(self, data: dict[str, Any]) -> None:
        if self.as_json:
            print(json.dumps({"kut_cli": SCHEMA_VERSION, **data}, ensure_ascii=False, indent=2), file=self.out)

    def failure(self, kind: str, message: str, code: int = EXIT_FAILED, **extra: Any) -> int:
        print(message, file=self.err)
        self.payload({"ok": False, "error": {"kind": kind, "message": message, **extra}})
        return code


# --- Chargement -------------------------------------------------------------------------------------------------------


def _load(path_text: str, output: _Output):
    """``(projet, rapport, chemin)`` ou ``None`` (échec déjà signalé : le code de sortie est dans ``output.exit``)."""
    from .project_io import LoadReport, load_project

    path = Path(path_text).expanduser()
    report = LoadReport()
    try:
        project = load_project(str(path), report=report)
    except FileNotFoundError:
        output.exit = output.failure("not_found", _t("cli.error.not_found", path=str(path)), path=str(path))
        return None
    except (ValueError, OSError) as error:
        output.exit = output.failure("invalid", _t("cli.error.invalid", message=str(error)), path=str(path))
        return None
    return project, report, path


def _missing_media(project) -> list[dict[str, str]]:
    return [
        {"id": asset.id, "name": asset.name, "path": asset.path}
        for asset in project.media_assets
        if asset.media_type in ("video", "audio", "image") and asset.path and not os.path.exists(asset.path)
    ]


def _dropped(report) -> dict[str, int]:
    return dict(sorted(report.dropped.items()))


def _section_label(section: str) -> str:
    """Le libellé d'une section abandonnée dans la langue choisie (la clé JSON, elle, reste stable)."""
    from .project_io import LOAD_SECTIONS

    return _t(f"cli.section.{section}") if section in LOAD_SECTIONS else section


# --- Commandes --------------------------------------------------------------------------------------------------------


def _cmd_info(args: argparse.Namespace, output: _Output) -> int:
    loaded = _load(args.project, output)
    if loaded is None:
        return output.exit
    project, report, path = loaded
    missing = _missing_media(project)
    sequences = []
    for sequence in project.sequences:
        clips = [clip for track in sequence.tracks for clip in track.clips]
        sequences.append({
            "id": sequence.id, "name": sequence.name, "active": sequence.id == project.active_sequence_id,
            "width": sequence.width, "height": sequence.height, "fps": sequence.fps,
            "duration": round(float(sequence.duration), 6), "tracks": len(sequence.tracks), "clips": len(clips),
            "nested_clips": sum(1 for clip in clips if getattr(clip, "sequence_id", "")), "buses": len(sequence.buses),
        })
    version = _format_version(path)
    output.line(_t("cli.info.project", name=project.name, version=version))
    for item in sequences:
        output.line(_t(
            "cli.info.sequence", name=item["name"], id=item["id"], active=_t("cli.info.active") if item["active"] else "",
            width=item["width"], height=item["height"], fps=item["fps"], duration=item["duration"],
            tracks=item["tracks"], clips=item["clips"],
        ))
    output.line(_t("cli.info.media", count=len(project.media_assets), missing=len(missing)))
    output.payload({
        "ok": True, "file": str(path), "name": project.name, "format_version": version,
        "active_sequence": project.active_sequence_id, "sequences": sequences,
        "media": {"count": len(project.media_assets), "missing": missing},
        "dropped": _dropped(report),
    })
    return EXIT_OK


def _format_version(path: Path) -> int | None:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("version"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _cmd_validate(args: argparse.Namespace, output: _Output) -> int:
    loaded = _load(args.project, output)
    if loaded is None:
        return output.exit
    project, report, path = loaded
    missing = _missing_media(project)
    dropped = _dropped(report)
    if report.intact:
        output.line(_t("cli.validate.intact", path=str(path)))
    else:
        output.line(_t("cli.validate.dropped", total=report.total))
        for section, count in dropped.items():
            output.line(_t("cli.validate.dropped_line", section=_section_label(section), count=count))
    if missing:
        output.line(_t("cli.validate.missing_media", count=len(missing)))
        for item in missing:
            output.line(_t("cli.validate.missing_line", name=item["name"], path=item["path"]))
    output.payload({
        "ok": True, "file": str(path), "intact": report.intact, "dropped": dropped, "dropped_total": report.total,
        "missing_media": missing,
    })
    return EXIT_OK if report.intact else EXIT_DROPPED


def _cmd_presets(args: argparse.Namespace, output: _Output) -> int:
    from .render_presets import builtin_presets

    specs = builtin_presets()
    for spec in specs:
        output.line(_t("cli.presets.line", id=spec.id, summary=spec.summary()))
    output.payload({"ok": True, "presets": [
        {"id": spec.id, "name": spec.name, "container": spec.container, "video_codec": spec.video_codec,
         "width": spec.width, "height": spec.height, "fps": spec.fps, "loudness_lufs": spec.loudness_lufs,
         "summary": spec.summary()}
        for spec in specs
    ]})
    return EXIT_OK


class _Progress:
    """Progression humaine sur la sortie d'erreur : ligne réécrite sur un terminal, un palier sur dix sinon."""

    def __init__(self, stream: Any, enabled: bool) -> None:
        self.stream = stream
        self.enabled = enabled
        self.last = -1
        self.interactive = bool(getattr(stream, "isatty", lambda: False)())

    def __call__(self, value: int) -> None:
        if not self.enabled or value == self.last:
            return
        if not self.interactive and value % 10 != 0 and value != 100:
            return
        self.last = value
        text = _t("cli.export.progress", value=value)
        print(("\r" + text) if self.interactive else text, end="" if self.interactive else "\n", file=self.stream, flush=True)
        if self.interactive and value == 100:
            print(file=self.stream)


def _pick_sequence(project, wanted: str | None):
    if wanted is None:
        return project.active_sequence
    matches = [s for s in project.sequences if s.id == wanted] or [s for s in project.sequences if s.name == wanted]
    return matches[0] if len(matches) == 1 else None


def _cmd_export(args: argparse.Namespace, output: _Output) -> int:
    from .render_presets import builtin_presets, default_preset, get_preset, with_hardware
    from .tool_paths import find_media_tool

    spec = default_preset() if args.preset is None else get_preset(args.preset)
    if spec is None:
        available = ", ".join(item.id for item in builtin_presets())
        return output.failure("unknown_preset", _t("cli.error.unknown_preset", preset=args.preset, available=available),
                              EXIT_USAGE)
    loaded = _load(args.project, output)
    if loaded is None:
        return output.exit
    project, report, project_path = loaded
    if not report.intact:
        output.warning(_t("cli.validate.dropped", total=report.total))
        for section, count in _dropped(report).items():
            output.warning(_t("cli.validate.dropped_line", section=_section_label(section), count=count))
    sequence = _pick_sequence(project, args.sequence)
    if sequence is None:
        available = ", ".join(f"{s.id} ({s.name})" for s in project.sequences)
        return output.failure("unknown_sequence", _t("cli.error.unknown_sequence", sequence=args.sequence, available=available),
                              EXIT_USAGE)
    try:
        output_path = Path(args.output).expanduser().resolve()
        same_file = output_path == project_path.resolve()
    except (OSError, RuntimeError) as error:
        return output.failure("invalid", str(error), EXIT_USAGE)
    if same_file:
        return output.failure("output_is_input", _t("cli.error.output_is_input", path=str(output_path)), EXIT_USAGE)
    if output_path.exists() and not args.overwrite:
        return output.failure("output_exists", _t("cli.error.output_exists", path=str(output_path)), EXIT_USAGE)
    if find_media_tool("ffmpeg") is None:
        return output.failure("no_ffmpeg", _t("cli.error.no_ffmpeg"))

    if args.hardware is not None:
        try:
            spec = with_hardware(spec, args.hardware)
        except ValueError as error:
            return output.failure("invalid", str(error), EXIT_USAGE)
    cancelled = threading.Event()
    building = threading.Event()

    def on_interrupt(_signal: int, _frame: Any) -> None:
        cancelled.set()
        if building.is_set():
            raise KeyboardInterrupt          # la mesure de loudness est un appel bloquant : on l'interrompt, FFmpeg est tué avec lui

    # Ctrl-C annule l'export proprement (FFmpeg tué, rien de tronqué), y compris pendant la préparation de la requête
    # (mesure de loudness) ; un gestionnaire de signal ne se pose que dans le fil principal.
    previous = (
        signal.signal(signal.SIGINT, on_interrupt) if threading.current_thread() is threading.main_thread() else None
    )
    try:
        building.set()
        try:
            request = _build_request(project, sequence, spec, output_path, args, output)
        except (ValueError, OSError, RuntimeError) as error:
            return output.failure("invalid", str(error))
        except KeyboardInterrupt:
            output.warning(_t("cli.export.cancelled"))
            output.payload({
                "ok": False, "status": "cancelled", "output": None, "preset": spec.id, "sequence": sequence.id,
                "encoder": None, "fallback_reason": None, "message": None, "error_kind": None,
            })
            return EXIT_INTERRUPTED
        finally:
            building.clear()
        output.line(_t("cli.export.start", project=str(project_path), output=str(output_path), preset=spec.id))
        from .export_runner import run_export

        result = run_export(
            request, progress=_Progress(output.err, not args.quiet and not args.json),
            cancelled=cancelled.is_set, atomic=not args.no_atomic,
        )
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)

    payload = {
        "ok": result.ok, "status": result.status.value, "output": result.output_path, "preset": spec.id,
        "sequence": sequence.id, "encoder": result.encoder.label if result.encoder is not None else None,
        "fallback_reason": result.fallback_reason or None, "message": result.message or None,
        "error_kind": result.error_kind or None,
    }
    if result.fallback_reason:
        output.warning(result.fallback_reason)           # un encodeur matériel a échoué : le CPU a pris le relais
    if result.ok:
        output.line(_t("cli.export.done", path=result.output_path))
        output.payload(payload)
        return EXIT_OK
    if cancelled.is_set() or result.status.value == "cancelled":
        output.warning(_t("cli.export.cancelled"))
        output.payload(payload)
        return EXIT_INTERRUPTED
    output.warning(_t("cli.export.failed", message=result.message))
    output.payload(payload)
    return EXIT_FAILED


def _build_request(project, sequence, spec, output_path: Path, args: argparse.Namespace, output: _Output):
    """La requête d'export : plan de la séquence choisie, préréglage, et la normalisation de loudness s'il en demande une."""
    from .export_engine import ExportRequest
    from .mograph_stream import ensure_qt_gui
    from .render_plan import build_render_plan

    ensure_qt_gui()                          # le texte et les formes sont dessinés par Qt (polices) : une QGuiApplication hors écran
    from .bundled_fonts import register_bundled_fonts

    register_bundled_fonts()
    export_format, export_preset, fps = spec.export_parts(sequence.fps)
    plan = build_render_plan(
        project, master_gain_db=float(args.master_gain_db), master_muted=bool(args.master_mute), sequence_id=sequence.id,
    )
    if spec.loudness_lufs is not None:
        from .loudness import normalized_plan

        output.warning(_t("cli.export.loudness", lufs=spec.loudness_lufs))
        plan, _measure = normalized_plan(plan, fps, float(spec.loudness_lufs))
    return ExportRequest(
        render_plan=plan, output_path=str(output_path), format=export_format, preset=export_preset, fps=fps,
        hardware=spec.hardware,
    )


_HANDLERS: dict[str, Callable[[argparse.Namespace, _Output], int]] = {
    "info": _cmd_info, "validate": _cmd_validate, "presets": _cmd_presets, "export": _cmd_export,
}


def run(argv: Sequence[str], *, out: Any = None, err: Any = None) -> int:
    """Exécute une commande (``argv`` **sans** le nom du programme) et rend le code de sortie ; n'appelle jamais ``sys.exit``."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv))
    except _UsageError as error:
        _choose_language(None)
        # Sous --json, même un appel mal formé rend l'unique objet JSON promis (le texte humain reste sur stderr).
        return _Output("--json" in argv, out, err).failure(
            "usage", _t("cli.usage.error", message=str(error)), EXIT_USAGE,
        )
    except SystemExit as stop:                    # « -h » : argparse a déjà imprimé l'aide
        return int(stop.code or 0)
    _choose_language(getattr(args, "lang", None))
    output = _Output(bool(getattr(args, "json", False)), out, err)
    return _HANDLERS[args.command](args, output)


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de ``main.py`` : ``argv`` est ``sys.argv[1:]``."""
    return run(sys.argv[1:] if argv is None else argv)


__all__ = ["COMMANDS", "EXIT_DROPPED", "EXIT_FAILED", "EXIT_INTERRUPTED", "EXIT_OK", "EXIT_USAGE", "SCHEMA_VERSION",
           "is_cli_invocation", "main", "run"]
