"""Spike : copie ffmpeg / ffprobe Homebrew et leurs dylibs non système dans un dossier autonome.

Les chemins de chargement sont réécrits en ``@loader_path`` (binaires dans ``bin/``, dylibs dans ``bin/lib/``).
Ce n'est PAS le FFmpeg LGPL de l'étape 2 de l'ADR-0013 : c'est un jouet pour mesurer le comportement du bac à sable
avec un FFmpeg embarqué (Homebrew est GPL, donc inutilisable tel quel dans le Store).

Usage : python bundle_ffmpeg.py SORTIE
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = ("ffmpeg", "ffprobe")
SOURCE = Path("/opt/homebrew/bin")
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")


def dependencies(binary: Path) -> list[str]:
    lines = subprocess.check_output(["otool", "-L", str(binary)], text=True).splitlines()[1:]
    return [line.split()[0] for line in lines]


def main(destination: Path) -> None:
    lib = destination / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    pending: list[Path] = []
    for tool in TOOLS:
        target = destination / tool
        shutil.copy2((SOURCE / tool).resolve(), target)
        pending.append(target)
    queue = list(pending)
    while queue:
        current = queue.pop()
        for dep in dependencies(current):
            if dep.startswith(SYSTEM_PREFIXES) or dep.startswith("@"):
                continue
            real = Path(dep).resolve()
            if dep not in copied:
                dest = lib / real.name
                if not dest.exists():
                    shutil.copy2(real, dest)
                    dest.chmod(0o755)
                    queue.append(dest)
                copied[dep] = dest
    for binary in [*pending, *lib.glob("*.dylib")]:
        in_bin = binary.parent == destination
        for dep in dependencies(binary):
            if dep in copied:
                new = f"@loader_path/{'lib/' if in_bin else ''}{copied[dep].name}"
                subprocess.check_call(["install_name_tool", "-change", dep, new, str(binary)], stderr=subprocess.DEVNULL)
        if not in_bin:
            subprocess.check_call(["install_name_tool", "-id", f"@loader_path/{binary.name}", str(binary)], stderr=subprocess.DEVNULL)
    for binary in [*pending, *lib.glob("*.dylib")]:  # install_name_tool invalide la signature (arm64 : le noyau tue)
        subprocess.check_call(["codesign", "--force", "--sign", "-", str(binary)], stderr=subprocess.DEVNULL)
    print(f"{len(list(lib.glob('*.dylib')))} dylibs copiées dans {lib}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
