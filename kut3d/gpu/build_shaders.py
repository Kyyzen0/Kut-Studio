"""Compile les shaders du moteur GPU en `.qsb` et écrit le manifeste d'empreintes.

Usage (depuis la racine du dépôt) ::

    python -m kut3d.gpu.build_shaders          # régénère kut3d/gpu/shaders/*.qsb et manifest.json
    python -m kut3d.gpu.build_shaders --check  # code 1 si un .qsb est périmé ou manquant

Nécessite `pyside6-qsb` (fourni par PySide6) UNIQUEMENT pour régénérer ; l'exécution lit les `.qsb` versionnés.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from kut3d.gpu import shader_files as sf


def qsb_executable() -> str:
    """Chemin de l'outil `qsb` : à côté de PySide6, à côté de l'interpréteur, puis dans le PATH."""
    candidates: list[Path] = []
    spec = importlib.util.find_spec("PySide6")
    if spec is not None and spec.submodule_search_locations:
        base = Path(next(iter(spec.submodule_search_locations)))
        candidates += [base / "qsb", base / "qsb.exe", base / "Qt" / "libexec" / "qsb"]
    bin_dir = Path(sys.executable).resolve().parent
    candidates += [bin_dir / "pyside6-qsb", bin_dir / "pyside6-qsb.exe"]
    for path in candidates:
        if path.is_file():
            return str(path)
    found = shutil.which("pyside6-qsb") or shutil.which("qsb")
    if found is None:
        raise SystemExit(
            "pyside6-qsb introuvable : installer PySide6 pour régénérer les shaders."
        )
    return found


def build() -> None:
    tool = qsb_executable()
    for name in sf.SOURCES:
        target = sf.SHADER_DIR / f"{name}.qsb"
        completed = subprocess.run(
            [
                tool, "--glsl", sf.GLSL_TARGETS, "--hlsl", sf.HLSL_TARGET,
                "--msl", sf.MSL_TARGET, "-o", str(target), str(sf.SHADER_DIR / name),
            ],
            capture_output=True,
            text=True,
            check=False,
        )  # fmt: skip
        if completed.returncode != 0:
            raise SystemExit(f"{name} : {completed.stderr or completed.stdout}")
        print(f"{name}.qsb  {target.stat().st_size} octets")
    sf.MANIFEST.write_text(
        json.dumps(sf.expected_manifest(), indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        outdated = sf.stale()
        for name in outdated:
            print(f"périmé : {name}")
        return 1 if outdated else 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
