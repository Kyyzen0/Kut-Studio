"""Relevé thermique d'une séance soutenue (macOS) : ``pmset -g therm`` à intervalle régulier, en lecture seule.

``pmset -g therm`` ne demande aucun droit et rend le niveau d'avertissement thermique et, quand le système bride, la limite de
vitesse du processeur (``CPU_Speed_Limit``). Il ne donne **pas** une température : la température perçue (le dessous du Mac
tiède, chaud) est notée à la main sur la fiche de séance. Le relevé tourne à côté de la séance humaine (``docs/recette/``).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LIMIT = re.compile(r"(?P<name>CPU_\w+_Limit|GPU_\w+_Limit)\s*=\s*(?P<value>\d+)")


def parse_pmset(text: str) -> dict[str, Any]:
    limits = {m["name"]: int(m["value"]) for m in _LIMIT.finditer(text)}
    return {"limits": limits, "warning": "No thermal warning level has been recorded" not in text and bool(text.strip()) and not limits,
            "throttled": any(value < 100 for value in limits.values())}


def sample() -> dict[str, Any]:
    try:
        done = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=10, check=False)
        text = done.stdout if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        text = ""
    data = parse_pmset(text)
    data["raw"] = text.strip()[:400]
    return data


def run(minutes: float, interval: float, output: Path, *, read: Callable[[], dict[str, Any]] = sample,
        sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Relève toutes les ``interval`` s pendant ``minutes`` min ; écrit une ligne JSON par relevé et rend le résumé."""
    if sys.platform != "darwin" and read is sample:
        raise RuntimeError("Le relevé thermique n'existe que sous macOS (pmset).")
    if not (minutes > 0 and interval > 0):
        raise ValueError("La durée et l'intervalle doivent être positifs.")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    rows = []
    with output.open("w", encoding="utf-8") as handle:
        while True:
            elapsed = now() - started
            row = {"t": round(elapsed, 1), **read()}
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            if elapsed + interval > minutes * 60.0:
                break
            sleep(interval)
    throttled = [row["t"] for row in rows if row["throttled"]]
    return {"samples": len(rows), "throttled_samples": len(throttled), "first_throttled_at_s": throttled[0] if throttled else None,
            "output": str(output)}
