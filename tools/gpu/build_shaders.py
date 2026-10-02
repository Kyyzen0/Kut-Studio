"""Compile les shaders de l'aperçu GPU (``assets/shaders/*.qsb``).

Usage ::

    python -m tools.gpu.build_shaders          # régénère
    python -m tools.gpu.build_shaders --check  # code 1 si un .qsb est périmé

``qsb`` est fourni par PySide6. Chaque ``.qsb`` contient les variantes
SPIR-V (Vulkan), GLSL (OpenGL / ES), HLSL (Direct3D) et MSL (Metal). Le
manifeste garde l'empreinte des sources : un test vérifie qu'aucun ``.qsb``
versionné n'est plus ancien que sa source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.gpu.shader_sources import SHADERS  # noqa: E402

OUTPUT = ROOT / "assets" / "shaders"
MANIFEST = OUTPUT / "manifest.json"
TARGETS = ("--glsl", "100 es,120,150,330", "--hlsl", "50", "--msl", "12")


def source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def qsb_path() -> str:
    import PySide6

    base = Path(PySide6.__file__).resolve().parent
    for name in ("qsb", "qsb.exe", "pyside6-qsb"):
        candidate = base / name
        if candidate.exists():
            return str(candidate)
    return "pyside6-qsb"


def expected_manifest() -> dict[str, str]:
    return {name: source_digest(source) for name, (_stage, source) in sorted(SHADERS.items())}


def stale() -> list[str]:
    try:
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return sorted(SHADERS)
    wanted = expected_manifest()
    return sorted(
        name for name, digest in wanted.items()
        if recorded.get(name) != digest or not (OUTPUT / f"{name}.qsb").is_file()
    )


def build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tool = qsb_path()
    with tempfile.TemporaryDirectory() as directory:
        for name, (_stage, source) in sorted(SHADERS.items()):
            path = Path(directory) / name
            path.write_text(source, encoding="utf-8")
            target = OUTPUT / f"{name}.qsb"
            completed = subprocess.run([tool, *TARGETS, "-o", str(target), str(path)],
                                       capture_output=True, text=True)
            if completed.returncode != 0:
                raise SystemExit(f"{name} : {completed.stderr or completed.stdout}")
            print(f"{name}.qsb  {target.stat().st_size} octets")
    MANIFEST.write_text(json.dumps(expected_manifest(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        outdated = stale()
        for name in outdated:
            print(f"périmé : {name}")
        return 1 if outdated else 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
