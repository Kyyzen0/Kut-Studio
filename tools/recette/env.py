"""Environnement du banc : dossier de travail, **isolation de la configuration réelle**, outils, identité de la machine.

Règle absolue (mémoire « never-touch-real-user-config ») : un processus lancé par le banc ne lit ni n'écrit jamais la
configuration, le cache, les données ni le journal de l'utilisateur. Chaque exécution reçoit ses propres dossiers, créés sous
le dossier de travail de la recette, et :func:`assert_isolated` refuse un environnement qui désignerait les vrais.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
WORKDIR_ENV = "KUT_STUDIO_RECETTE_DIR"
ISOLATED_VARIABLES = (
    "KUT_STUDIO_CONFIG_DIR", "KUT_STUDIO_CACHE_DIR", "KUT_STUDIO_DATA_DIR", "KUT_STUDIO_LOG_DIR", "KUT_STUDIO_PROXY_DIR",
)


class EnvironmentRefused(RuntimeError):
    """Le banc refuse de tourner (dossier dans le dépôt, environnement non isolé)."""


def default_workdir(environment: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Dossier de travail par défaut : ``$KUT_STUDIO_RECETTE_DIR``, sinon ``~/Movies/Kut-Studio-Recette`` (macOS) ou
    ``~/Kut-Studio-Recette``. Jamais dans le dépôt, ni dans un dossier temporaire qui disparaît au redémarrage."""
    env = environment if environment is not None else os.environ
    if env.get(WORKDIR_ENV):
        return Path(env[WORKDIR_ENV]).expanduser()
    base = home if home is not None else Path.home()
    movies = base / "Movies"
    return (movies if movies.is_dir() else base) / "Kut-Studio-Recette"


def resolve_workdir(requested: str | os.PathLike[str] | None = None) -> Path:
    """Dossier de travail absolu et validé : il ne doit pas se trouver dans le dépôt (les médias ne sont jamais commités)."""
    path = Path(requested).expanduser() if requested else default_workdir()
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise EnvironmentRefused(
            f"Le dossier de travail de la recette ne peut pas être dans le dépôt ({ROOT}) : choisissez un dossier hors du dépôt."
        )
    return path


def real_directories() -> dict[str, Path]:
    """Les dossiers que l'application utiliserait **sans** surcharge (ceux qu'il ne faut jamais toucher)."""
    from core import platform_paths

    empty: Mapping[str, str] = {}
    return {
        "KUT_STUDIO_CONFIG_DIR": platform_paths.user_config_dir(environment=empty),
        "KUT_STUDIO_CACHE_DIR": platform_paths.user_cache_dir(environment=empty),
        "KUT_STUDIO_DATA_DIR": platform_paths.user_data_dir(environment=empty),
        "KUT_STUDIO_LOG_DIR": platform_paths.user_log_dir(environment=empty),
    }


def isolated_environment(root: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environnement d'un processus du banc : tous les dossiers de l'application sous ``root`` (créés), rendu hors écran,
    aucune vérification de mise à jour (aucun accès réseau)."""
    env = dict(base if base is not None else os.environ)
    for name in ISOLATED_VARIABLES:
        folder = root / name.removeprefix("KUT_STUDIO_").removesuffix("_DIR").lower()
        folder.mkdir(parents=True, exist_ok=True)
        env[name] = str(folder)
    temporary = root / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TEMP", "TMP"):               # les fichiers temporaires de l'export restent sous le dossier de la recette
        env[name] = str(temporary)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["KUT_STUDIO_UPDATE_CHECK"] = "off"
    env["KUT_STUDIO_MACHINE_BENCH"] = "off"
    env["KUT_STUDIO_LANG"] = "fr"
    env["PYTHONIOENCODING"] = "utf-8"
    assert_isolated(env)
    return env


def assert_isolated(env: Mapping[str, str]) -> None:
    """Refuse un environnement qui laisserait un dossier de l'application sur sa valeur réelle (ou le désignerait)."""
    real = {name: path.resolve() for name, path in real_directories().items()}
    for name in ISOLATED_VARIABLES:
        value = env.get(name, "")
        if not value:
            raise EnvironmentRefused(f"{name} n'est pas défini : le banc ne s'exécute jamais sur la configuration réelle.")
        candidate = Path(value).resolve()
        for label, path in real.items():
            if candidate == path or path in candidate.parents or candidate in path.parents:
                raise EnvironmentRefused(f"{name} désigne la configuration réelle de l'utilisateur ({label}) : refusé.")


def find_tool(name: str) -> str | None:
    """FFmpeg / ffprobe comme l'application les trouve."""
    from core.tool_paths import find_media_tool

    return find_media_tool(name)


def run_text(command: list[str], timeout: float = 15.0) -> str:
    """Sortie d'une commande d'information, ou ``""`` : le banc ne dépend d'aucune d'elles."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def ffmpeg_version(ffmpeg: str | None) -> str:
    if not ffmpeg:
        return ""
    first = run_text([ffmpeg, "-hide_banner", "-version"]).splitlines()
    match = re.match(r"ffmpeg version (\S+)", first[0]) if first else None
    return match.group(1) if match else (first[0] if first else "")


def ffmpeg_has(ffmpeg: str | None, kind: str, name: str) -> bool:
    """``kind`` : ``filters`` ou ``encoders``. Cherche ``name`` comme mot entier dans la liste de FFmpeg."""
    if not ffmpeg:
        return False
    listing = run_text([ffmpeg, "-hide_banner", f"-{kind}"], timeout=30.0)
    return re.search(rf"(?m)^\s*\S+\s+{re.escape(name)}\s", listing) is not None


def machine_info() -> dict[str, Any]:
    """Identité de la machine pour le rapport (modèle, puce, mémoire, système, versions)."""
    from core.app_version import APP_VERSION

    system = platform.system()
    info: dict[str, Any] = {
        "system": system, "machine": platform.machine(), "python": platform.python_version(),
        "app_version": APP_VERSION, "model": "", "chip": "", "ram_gb": None, "cpu_count": os.cpu_count() or 0,
        "os_version": platform.platform(),
    }
    if system == "Darwin":
        info["model"] = run_text(["sysctl", "-n", "hw.model"])
        info["chip"] = run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
        memory = run_text(["sysctl", "-n", "hw.memsize"])
        info["ram_gb"] = round(int(memory) / 2**30, 1) if memory.isdigit() else None
        info["os_version"] = f"macOS {platform.mac_ver()[0]}"
        performance = run_text(["sysctl", "-n", "hw.perflevel0.physicalcpu"])
        efficiency = run_text(["sysctl", "-n", "hw.perflevel1.physicalcpu"])
        if performance.isdigit():
            info["performance_cores"] = int(performance)
        if efficiency.isdigit():
            info["efficiency_cores"] = int(efficiency)
    elif system == "Linux":
        info["chip"] = _linux_cpu_name()
        info["ram_gb"] = _linux_ram_gb()
    else:
        info["chip"] = platform.processor()
    return info


def _linux_cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _cgroup_limit_gb() -> float | None:
    """Limite de mémoire du conteneur (cgroup v2 ``memory.max``, v1 ``memory.limit_in_bytes``), ``None`` si illimitée ou absente."""
    for name in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            text = Path(name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text.isdigit() and int(text) < 2**60:
            return round(int(text) / 2**30, 1)
    return None


def _linux_ram_gb() -> float | None:
    host = _linux_host_ram_gb()
    limit = _cgroup_limit_gb()
    return min(host, limit) if host is not None and limit is not None else (limit if host is None else host)


def _linux_host_ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def machine_slug(info: Mapping[str, Any]) -> str:
    """Nom de fichier stable pour une machine (référence enregistrée) : ``mac16-10-apple-m4-24gb``."""
    parts = [str(info.get("model") or info.get("system") or "machine"), str(info.get("chip") or info.get("machine") or "")]
    if info.get("ram_gb"):
        parts.append(f"{int(float(info['ram_gb']))}gb")
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-") or "machine"


def python_command() -> list[str]:
    return [sys.executable]


def app_command(app: str | None = None) -> list[str]:
    """Commande qui lance l'application (ou son CLI) : les sources par défaut, sinon un exécutable fourni (le paquet)."""
    if app:
        return [app]
    return [sys.executable, str(ROOT / "main.py")]


def free_disk_gb(path: Path) -> float:
    """Espace libre (Go) du volume qui porte ``path`` (ou son plus proche parent existant)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 2**30
