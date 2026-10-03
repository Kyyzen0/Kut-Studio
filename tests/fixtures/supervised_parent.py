"""Parent jetable des tests de supervision des processus (``tests/test_process_supervisor.py``).

Crée son propre :class:`~core.process_supervisor.ProcessSupervisor`, lance des enfants longs, écrit leurs
identités dans un fichier JSON puis attend : le test le tue brutalement (``SIGKILL`` / ``TerminateProcess``) ou
ferme son entrée standard (fermeture normale).

Usage : ``python supervised_parent.py <dossier des registres> <sortie.json> [options]``

- ``--children N`` : N enfants simultanés lancés par ``supervised()`` (défaut 1) ;
- ``--ffmpeg`` : ces enfants sont un vrai FFmpeg (``-f lavfi -i testsrc``) au lieu d'un ``sleep`` Python ;
- ``--run`` : un enfant de plus, lancé par ``run()`` bloquant dans un thread (sous-processus encore actif) ;
- ``--qprocess`` : un enfant de plus, lancé par le ``QProcess`` de l'export, enregistré au signal ``started`` ;
- ``--unguarded`` : ni gardien ni objet Job (seul le registre reste, pour le balayage au démarrage).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import process_supervisor  # noqa: E402
from core.process_supervisor import ProcessSupervisor  # noqa: E402

SLEEP = [sys.executable, "-c", "import time; time.sleep(300)"]


def _ffmpeg_command() -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit(3)
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-re",
            "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10", "-f", "null", "-"]


def _wait_for(condition, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise SystemExit(4)
        time.sleep(0.01)


def main(arguments: list[str]) -> int:
    root, output = Path(arguments[0]), Path(arguments[1])
    options = arguments[2:]
    count = int(options[options.index("--children") + 1]) if "--children" in options else 1
    unguarded = "--unguarded" in options
    guard = False if unguarded else None  # None : protection de la plateforme (gardien ou objet Job)
    supervisor = ProcessSupervisor(root, use_reaper=guard, use_job=guard)
    command = _ffmpeg_command() if "--ffmpeg" in options else SLEEP
    expected = count
    children = [supervisor.popen(command) for _ in range(count)]
    if "--run" in options:
        expected += 1
        threading.Thread(target=lambda: supervisor.run(SLEEP, timeout=600), daemon=True).start()
    application = engine = None
    if "--qprocess" in options:
        expected += 1
        # Le vrai chemin de l'export : QProcess de ExportEngine, PID enregistré dans le superviseur par défaut.
        process_supervisor._default = supervisor
        from PySide6.QtCore import QCoreApplication

        from core.export_engine import ExportEngine

        application = QCoreApplication([])
        engine = ExportEngine()
        engine._launch(list(SLEEP))
        deadline = time.monotonic() + 30.0
        while len(supervisor.live_registrations()) < expected and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
    _wait_for(lambda: len(supervisor.live_registrations()) >= expected)
    reaper = supervisor.ops.probe(supervisor.reaper_pid).identity if supervisor.reaper_pid else None
    report = {
        "parent": os.getpid(),
        "reaper": {"pid": reaper.pid, "token": reaper.token, "name": reaper.name} if reaper else None,
        "registry": str(supervisor.registry_directory),
        "children": [
            {"pid": item.identity.pid, "token": item.identity.token, "name": item.identity.name}
            for item in supervisor.live_registrations()
        ],
        "popen": [child.pid for child in children],
    }
    staging = output.with_suffix(".tmp")
    staging.write_text(json.dumps(report), encoding="utf-8")
    os.replace(staging, output)
    # Attente : un EOF sur l'entrée standard demande la fermeture normale, comme l'étape de _shutdown_steps.
    sys.stdin.read()
    stopped = supervisor.shutdown()
    for child in children:
        supervisor.finish(child)
    if engine is not None:
        engine.shutdown()
    supervisor.close()
    return 0 if stopped else 5


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
