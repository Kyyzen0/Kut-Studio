"""Rapport reproductible (JSON + Markdown) et comparaison à une référence **par ratios**.

Les seuils ci-dessous servent à **étiqueter** le rapport (« plus lent », « plus gros »), jamais à faire échouer un test : aucune
durée n'est un seuil de test (ADR-0011 : « les tests portent sur des identités, des octets et des ratios »).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import REPORT_VERSION

SLOWER_RATIO = 1.25
REGRESSION_RATIO = 1.5
FASTER_RATIO = 0.8
SIZE_CHANGE_RATIO = 0.02
"""Un écart de taille de sortie supérieur à 2 % signifie une sortie différente (pas une vitesse)."""


def scrub(value: Any, home: str | None = None) -> Any:
    """Remplace le dossier personnel par ``~`` dans tous les textes : un rapport peut être partagé sans nom d'utilisateur."""
    home = home or str(Path.home())
    if isinstance(value, str):
        return value.replace(home, "~") if home and home != "/" else value
    if isinstance(value, list):
        return [scrub(item, home) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item, home) for key, item in value.items()}
    return value


def build_report(machine: Mapping[str, Any], tools: Mapping[str, Any], journeys: Mapping[str, Any], *, scale: str, hardware: str,
                 isolation: Mapping[str, str]) -> dict[str, Any]:
    problems = [f"{name}: {problem}" for name, item in journeys.items() for problem in item.get("problems", [])]
    crashed = [name for name, item in journeys.items() if _crashed(item)]
    return {
        "report_version": REPORT_VERSION, "kind": "recette", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scale": scale, "hardware": hardware, "machine": dict(machine), "tools": dict(tools), "isolation": dict(isolation),
        "journeys": dict(journeys),
        "summary": {"ok": all(item.get("ok") for item in journeys.values()) and bool(journeys),
                    "journeys_ok": sum(1 for item in journeys.values() if item.get("ok")), "journeys": len(journeys),
                    "crashed": crashed, "problems": problems},
    }


def _crashed(journey: Mapping[str, Any]) -> bool:
    stages = journey.get("stages", {})
    runs = [stage for stage in stages.values() if isinstance(stage, dict) and "crashed" in stage]
    runs += [stage for stage in (stages.get("exports") or {}).values() if isinstance(stage, dict)]
    return any(run.get("crashed") for run in runs)


def metrics_of(report: Mapping[str, Any]) -> dict[str, float]:
    """Les mesures comparables d'un rapport, à plat : ``tiktok.apply.s``, ``tiktok.export.9x16.rss_mb``…"""
    found: dict[str, float] = {}

    def put(key: str, value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            found[key] = float(value)

    for name, journey in (report.get("journeys") or {}).items():
        stages = journey.get("stages", {})
        media = stages.get("media", {})
        if media.get("generated"):                       # un média réutilisé n'a pas de durée de génération à comparer
            put(f"{name}.media.s", media.get("seconds"))
        put(f"{name}.media.bytes", media.get("bytes"))
        for stage in ("apply", "validate_script", "validate_project", "describe"):
            put(f"{name}.{stage}.s", (stages.get(stage) or {}).get("elapsed_s"))
        put(f"{name}.apply.rss_mb", (stages.get("apply") or {}).get("peak_rss_mb"))
        for item in (stages.get("analysis") or {}).get("items", []):
            put(f"{name}.{item.get('kind')}.s", item.get("seconds"))
        for export, entry in (stages.get("exports") or {}).items():
            put(f"{name}.export.{export}.s", entry.get("elapsed_s"))
            put(f"{name}.export.{export}.rss_mb", entry.get("peak_rss_mb"))
            put(f"{name}.export.{export}.bytes", entry.get("output_bytes"))
    return found


def compare(current: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    """Ratios mesure / référence pour chaque mesure présente des deux côtés, étiquetés ; jamais un échec."""
    now, before = metrics_of(current), metrics_of(reference)
    rows = []
    for key in sorted(set(now) & set(before)):
        if before[key] <= 0:
            continue
        ratio = now[key] / before[key]
        if key.endswith(".bytes"):
            label = "sortie différente" if abs(ratio - 1.0) > SIZE_CHANGE_RATIO else "identique"
        elif ratio >= REGRESSION_RATIO:
            label = "régression"
        elif ratio >= SLOWER_RATIO:
            label = "plus lent / plus gros"
        elif ratio <= FASTER_RATIO:
            label = "mieux"
        else:
            label = "stable"
        rows.append({"metric": key, "value": round(now[key], 3), "reference": round(before[key], 3), "ratio": round(ratio, 3), "label": label})
    same = (current.get("machine") or {}).get("chip") == (reference.get("machine") or {}).get("chip") and \
        (current.get("machine") or {}).get("model") == (reference.get("machine") or {}).get("model")
    return {"same_machine": same, "reference_created": reference.get("reference_created") or reference.get("created"),
            "reference_app_version": (reference.get("machine") or {}).get("app_version"), "rows": rows,
            "missing": sorted(set(before) - set(now))}


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != "recette":
        raise ValueError("Ce fichier n'est pas un rapport de recette.")
    if int(data.get("report_version", 0)) > REPORT_VERSION:
        raise ValueError("Rapport d'une version plus récente que ce banc.")
    return data


def write_report(report: Mapping[str, Any], directory: Path, stem: str = "rapport") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    clean = scrub(dict(report))
    json_path, md_path = directory / f"{stem}.json", directory / f"{stem}.md"
    json_path.write_text(json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(clean), encoding="utf-8")
    return json_path, md_path


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "n/d"
    return f"{value:.{digits}f}{unit}" if isinstance(value, float) else f"{value}{unit}"


def _mb(value: Any) -> str:
    return "n/d" if value is None else f"{value / 2**20:.1f} Mo"


def render_markdown(report: Mapping[str, Any]) -> str:
    machine, summary = report.get("machine", {}), report.get("summary", {})
    lines = [
        f"# Rapport de recette — {'OK' if summary.get('ok') else 'À REGARDER'}", "",
        f"- Date : {report.get('created')} · échelle `{report.get('scale')}` · encodeur demandé `{report.get('hardware')}`",
        f"- Machine : {machine.get('model') or machine.get('system')} · {machine.get('chip')} · {_fmt(machine.get('ram_gb'), ' Go')} · "
        f"{machine.get('os_version')}",
        f"- Kut-Studio {machine.get('app_version')} · FFmpeg {report.get('tools', {}).get('ffmpeg')} · Python {machine.get('python')}",
        f"- libass : {report.get('tools', {}).get('libass')} · whisper : {report.get('tools', {}).get('whisper')}",
        f"- Parcours réussis : {summary.get('journeys_ok')}/{summary.get('journeys')}"
        + (f" · plantage : {', '.join(summary.get('crashed', []))}" if summary.get("crashed") else ""), "",
    ]
    if summary.get("problems"):
        lines += ["## Défauts constatés", ""] + [f"- {problem}" for problem in summary["problems"]] + [""]
    for name, journey in (report.get("journeys") or {}).items():
        lines += _journey_markdown(name, journey)
    comparison = report.get("comparison")
    if comparison:
        lines += ["## Écarts à la référence", "",
                  f"Référence du {comparison.get('reference_created')} (Kut-Studio {comparison.get('reference_app_version')}) · "
                  + ("même machine" if comparison.get("same_machine") else "**autre machine : comparaison indicative**"), "",
                  "| Mesure | Valeur | Référence | Ratio | |", "| --- | ---: | ---: | ---: | --- |"]
        lines += [f"| `{row['metric']}` | {row['value']} | {row['reference']} | ×{row['ratio']} | {row['label']} |" for row in comparison["rows"]]
        lines += ["", "Les étiquettes sont indicatives : aucune durée n'est un seuil de test.", ""]
    lines += ["## Ce que ce rapport ne prouve pas", "",
              "Aucune fenêtre, aucun écran, aucun GPU, aucune lecture à l'écran (gigue, images perdues), aucune interface : voir "
              "`docs/recette/README.md` et la procédure humaine de `docs/recette/`.", ""]
    return "\n".join(lines)


def _journey_markdown(name: str, journey: Mapping[str, Any]) -> list[str]:
    stages = journey.get("stages", {})
    project = journey.get("project") or {}
    out = [f"## {journey.get('title', name)} — {'OK' if journey.get('ok') else 'ÉCHEC'}", ""]
    if project:
        out += [f"Projet construit : {project.get('duration')} s, {project.get('sequences')} séquence(s), {project.get('clips')} clip(s), "
                f"{project.get('keyframes')} image(s) clé(s), {project.get('transitions')} transition(s).", ""]
    media = stages.get("media", {})
    out += ["| Étape | Durée (s) | Pic mémoire (Mo) | Code |", "| --- | ---: | ---: | ---: |"]
    out.append(f"| Médias ({_mb(media.get('bytes'))}, {len(media.get('generated', []))} générés) | {_fmt(media.get('seconds'))} | | |")
    for stage in ("validate_script", "apply", "validate_project", "describe"):
        data = stages.get(stage)
        if data:
            out.append(f"| {stage} | {_fmt(data.get('elapsed_s'), digits=2)} | {_fmt(data.get('peak_rss_mb'))} | {data.get('returncode')} |")
    for item in (stages.get("analysis") or {}).get("items", []):
        detail = f"écart {item.get('relative_error')}" if item.get("kind") == "tempo" else f"écart max {item.get('max_error_ms')} ms"
        out.append(f"| analyse {item.get('kind')} ({detail}) | {_fmt(item.get('seconds'), digits=2)} | {_fmt(item.get('peak_rss_mb'))} | {item.get('returncode')} |")
    for export, entry in (stages.get("exports") or {}).items():
        out.append(f"| export {export} | {_fmt(entry.get('elapsed_s'))} | {_fmt(entry.get('peak_rss_mb'))} | {entry.get('returncode')} |")
    out.append("")
    for export, entry in (stages.get("exports") or {}).items():
        probe = entry.get("probe") or {}
        video = next((s for s in probe.get("streams", []) if s.get("type") == "video"), {})
        frames = entry.get("frames") or {}
        out.append(f"- **{export}** : {_mb(entry.get('output_bytes'))}, {_fmt(probe.get('duration'), ' s', 2)} (timeline {_fmt(entry.get('expected_duration'), ' s', 2)}), "
                   f"{video.get('width')}x{video.get('height')} {_fmt(video.get('fps'), ' i/s', 2)} {video.get('pix_fmt')}, encodeur {entry.get('encoder')}, "
                   f"noir {_fmt(frames.get('black_ratio'))} · gelé {_fmt(frames.get('frozen_ratio'))}")
        for note in entry.get("warnings", []):
            out.append(f"  - à regarder : {note}")
        for problem in entry.get("problems", []):
            out.append(f"  - **défaut** : {problem}")
    return out + [""]
