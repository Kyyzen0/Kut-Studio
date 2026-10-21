"""Garde-fou : dans ``core/`` et ``ui/``, tout processus enfant passe par ``core/process_supervisor.py``.

Un lancement direct (``subprocess.Popen``, ``subprocess.run``, ``QProcess``…) échapperait à la supervision : son
FFmpeg survivrait à un arrêt brutal de l'application (voir ``docs/process-supervision.md``).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_LAUNCHERS = {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_OS_LAUNCHERS = {"system", "popen", "spawnl", "spawnv", "spawnve", "spawnlp", "spawnvp", "posix_spawn",
                 "posix_spawnp", "execv", "execve", "execl", "execlp", "execvp", "startfile"}
_CENTRAL = {"core/process_supervisor.py"}
_QPROCESS_ALLOWED = {
    # Le seul QProcess (export, réutilisé par la file de rendu) : signaux Qt et lecture événementielle de la
    # progression, non réécrit. Son PID est enregistré auprès du superviseur au signal ``started`` (vérifié plus bas).
    "core/export_engine.py",
}


def _launch_sites(path: Path, label: str) -> list[str]:
    """Appels qui lancent un processus (une annotation ``subprocess.Popen`` n'en est pas un)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            found += [f"from subprocess import {alias.name}" for alias in node.names if alias.name in _LAUNCHERS]
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "QProcess":
            found.append("QProcess()")
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            owner, name = function.value.id, function.attr
            if (owner == "subprocess" and name in _LAUNCHERS) or (owner == "os" and name in _OS_LAUNCHERS):
                found.append(f"{owner}.{name}()")
            if owner == "QProcess" and name in ("startDetached", "execute"):
                found.append(f"QProcess.{name}()")
    return [f"{label}:{site}" for site in found]


def test_every_child_process_goes_through_the_supervisor():
    offenders = []
    for folder in ("core", "ui"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative in _CENTRAL:
                continue
            sites = _launch_sites(path, relative)
            if relative in _QPROCESS_ALLOWED:
                sites = [site for site in sites if site != f"{relative}:QProcess()"]
            offenders += sites
    assert offenders == [], "lancement hors de core/process_supervisor.py : " + ", ".join(offenders)
    export_source = (ROOT / "core" / "export_engine.py").read_text(encoding="utf-8")
    assert "started.connect(self._supervise_started)" in export_source
    assert "process_supervisor.register_pid(" in export_source


def test_the_guard_recognises_direct_launches(tmp_path):
    """Le garde-fou n'est pas vide : il reconnaît chaque forme de lancement direct."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os, subprocess\nfrom subprocess import Popen\nfrom PySide6.QtCore import QProcess\n"
        "subprocess.run(['ffmpeg'])\nsubprocess.Popen(['ffprobe'])\nos.system('ffmpeg')\n"
        "QProcess()\nQProcess.startDetached('ffmpeg')\nprocess: subprocess.Popen | None = None\n",
        encoding="utf-8",
    )
    sites = [site.split(":", 1)[1] for site in _launch_sites(sample, "sample.py")]
    assert sorted(sites) == sorted([
        "from subprocess import Popen", "subprocess.run()", "subprocess.Popen()", "os.system()", "QProcess()",
        "QProcess.startDetached()",
    ])
