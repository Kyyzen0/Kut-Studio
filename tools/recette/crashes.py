"""Plantages par heure de session, d'après le journal local de l'application (``core/diagnostics_log.py``).

Compté : (1) les exceptions non rattrapées (niveau ``CRITICAL``, « Exception non rattrapée ») ; (2) les rapports de plantage du
système (``*.ips`` / ``*.crash`` de Kut-Studio ou de Python) dont la date tombe dans une séance. Une mort native (segfault) ne laisse
pas de ligne dans le journal Python : seul son rapport système la révèle. Le journal n'a pas de ligne de fin de session : la durée
d'une séance vient des **intervalles que l'humain a relevés** (``--session DEBUT FIN``, ISO 8601) ; sans eux, elle est estimée (du
premier « Démarrage » à la dernière ligne) et marquée comme telle. Lecture seule : le banc n'écrit jamais dans ces dossiers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_LINE = re.compile(r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (?P<level>[A-Z]+)\s+(?P<thread>\S+) (?P<logger>\S+) : (?P<message>.*)$")
MAX_LINE = 4000
MAX_FILE_BYTES = 50 * 2**20
"""Un journal plus gros est lu par la fin seulement (il tourne à 1 Mo × 4, mais un fichier forgé n'alloue pas plus)."""


def parse_log(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in text.splitlines():
        match = _LINE.match(raw[:MAX_LINE])
        if not match:
            continue
        message = match["message"]
        kind = "start" if message.startswith("Démarrage") else "crash" if (
            match["level"] == "CRITICAL" and "Exception non rattrapée" in message) else "error" if match["level"] in ("ERROR", "CRITICAL") else "other"
        events.append({"time": datetime.strptime(match["time"], "%Y-%m-%d %H:%M:%S"), "kind": kind, "message": message[:200]})
    return events


def read_logs(directory: Path, name: str = "kut-studio.log") -> list[dict[str, Any]]:
    """Le journal et ses sauvegardes tournantes (``.3`` le plus ancien), dans l'ordre chronologique."""
    texts = []
    for suffix in (".3", ".2", ".1", ""):
        path = directory / f"{name}{suffix}"
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - MAX_FILE_BYTES))
                texts.append(handle.read().decode("utf-8", errors="replace"))
        except OSError:
            continue
    return sorted(parse_log("\n".join(texts)), key=lambda event: event["time"])


def crash_reports(directory: Path | None, start: datetime, end: datetime) -> list[str]:
    """Rapports de plantage système (noms) dont la date de modification tombe dans [start, end]."""
    if directory is None or not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".ips", ".crash") or not re.match(r"(?i)(kut-studio|python)", path.name):
            continue
        try:
            moment = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if start <= moment <= end:
            found.append(path.name)
    return found


def estimate_sessions(events: Iterable[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Séances estimées : de chaque « Démarrage » à la dernière ligne avant le suivant (borne basse de la vraie durée)."""
    events = list(events)
    starts = [index for index, event in enumerate(events) if event["kind"] == "start"]
    sessions = []
    for position, first in enumerate(starts):
        last = (starts[position + 1] - 1) if position + 1 < len(starts) else len(events) - 1
        sessions.append((events[first]["time"], events[last]["time"]))
    return sessions


def summarize(events: Sequence[dict[str, Any]], sessions: Sequence[tuple[datetime, datetime]] | None = None,
              reports_dir: Path | None = None) -> dict[str, Any]:
    estimated = not sessions
    windows = list(sessions) if sessions else estimate_sessions(events)
    hours = sum((end - start).total_seconds() for start, end in windows) / 3600.0
    crashes: list[dict[str, Any]] = []
    errors = 0
    reports: list[str] = []
    for start, end in windows:
        inside = [event for event in events if start <= event["time"] <= end]
        crashes += [{"time": event["time"].isoformat(), "message": event["message"]} for event in inside if event["kind"] == "crash"]
        errors += sum(1 for event in inside if event["kind"] == "error")
        reports += crash_reports(reports_dir, start, end + timedelta(minutes=5))
    total = len(crashes) + len(reports)
    return {"sessions": len(windows), "hours": round(hours, 3), "hours_estimated": estimated, "uncaught_exceptions": len(crashes),
            "system_crash_reports": reports, "crashes": total, "crashes_per_hour": round(total / hours, 3) if hours > 0 else None,
            "error_lines": errors, "uncaught": crashes[:10]}


def parse_session(text: str) -> tuple[datetime, datetime]:
    """``DEBUT/FIN`` ou deux valeurs ISO 8601 locales ; la fin doit suivre le début."""
    parts = text.split("/")
    if len(parts) != 2:
        raise ValueError("Une séance se donne « DEBUT/FIN » (ISO 8601, ex. 2026-10-22T09:00/2026-10-22T11:00).")
    start, end = datetime.fromisoformat(parts[0]), datetime.fromisoformat(parts[1])
    if end <= start:
        raise ValueError("La fin d'une séance doit suivre son début.")
    return start, end
