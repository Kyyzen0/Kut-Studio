"""Vérifie le bundle ``Kut-Studio.app`` de la saveur Store construit en local (ADR-0013, étape 7a).

Usage : ``python -m tools.check_store_bundle [dist/Kut-Studio.app] [--json]`` (macOS ; code de sortie 1 s'il y a un problème).

Rien n'est signé ni créé ici. Les contrôles lisent le bundle sans l'importer : les modules embarqués se lisent dans l'archive
PyInstaller de l'exécutable (``PyInstaller.archive.readers``), jamais en les important ; seul le « smoke test » lance
l'application (hors écran, ``HOME`` vide, configuration isolée : voir :func:`isolated_environment`)."""

from __future__ import annotations

import ast
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APP = ROOT / "dist" / "Kut-Studio.app"
EXECUTABLE = "Kut-Studio"
FORBIDDEN_MODULES = (
    "core.updates", "core.update_service", "core.update_install", "core.update_helper", "core.update_keys",
    "core.update_signature", "ui.update_auto_install", "ui.update_dialog", "ui.main_window_mixins.updates",
    "core.cli", "core.scripting",
)
"""Modules qui ne doivent pas être dans le bundle (``build.STORE_EXCLUDED_MODULES`` + ``STORE_EXCLUDED_CLI_MODULES``)."""
DYLIBS = ("libkutstore.dylib", "libkutscope.dylib")
SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/")
INTERNAL_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")
FORBIDDEN_FFMPEG_FLAGS = ("--enable-gpl", "--enable-nonfree")
THIRD_PARTY_PREFIXES = ("/opt/homebrew", "/usr/local", "/opt/local")


def isolated_environment(home: Path, config: Path) -> dict[str, str]:
    """Environnement d'un lancement du bundle : ``HOME`` vide, configuration, cache et langue isolés, Qt hors écran,
    aucune recherche de mise à jour. Les variables ``KUT_STUDIO_FFMPEG*`` sont retirées (le Store ne les lit pas)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("KUT_STUDIO_FFMPEG")}
    env.update(
        HOME=str(home), KUT_STUDIO_CONFIG_DIR=str(config), KUT_STUDIO_CACHE_DIR=str(config / "cache"),
        QT_QPA_PLATFORM="offscreen", KUT_STUDIO_UPDATE_CHECK="off",
    )
    return env


def executable_of(app: Path) -> Path:
    """Exécutable du bundle, en chemin ABSOLU : les sous-processus changent de dossier courant (HOME temporaire)."""
    return app.resolve() / "Contents" / "MacOS" / EXECUTABLE


def _run(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, **kwargs)  # type: ignore[call-overload]


def mach_o_files(app: Path) -> list[Path]:
    """Fichiers Mach-O du bundle (liens exclus), reconnus par leur en-tête."""
    magic = {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xbe\xba\xfe\xca"}
    found = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                if stream.read(4) in magic:
                    found.append(path)
        except OSError:
            continue
    return found


def embedded_modules(app: Path) -> dict[str, object]:
    """Noms des modules Python embarqués (archive PYZ de l'exécutable) -> lecteur de l'archive. Sans importer aucun d'eux."""
    from PyInstaller.archive.readers import CArchiveReader

    carchive = CArchiveReader(str(executable_of(app)))
    modules: dict[str, object] = {}
    for name in carchive.toc:
        if name.endswith(".pyz"):
            pyz = carchive.open_embedded_archive(name)
            for module in pyz.toc:
                modules[module] = pyz
    return modules


def flavor_value(modules: dict[str, object]) -> object:
    """``STORE`` du module généré, lu en exécutant ce seul petit module (deux lignes) ; ``None`` s'il est absent."""
    pyz = modules.get("core._build_flavor_generated")
    if pyz is None:
        return None
    code = pyz.extract("core._build_flavor_generated")  # type: ignore[attr-defined]
    namespace: dict[str, object] = {}
    exec(code, namespace)  # noqa: S102 - module de deux lignes produit par build.py, lu dans notre propre bundle
    return namespace.get("STORE")


def check_modules(app: Path) -> tuple[list[str], dict[str, object]]:
    """(b) ``STORE`` vrai ; (c) aucun module de mise à jour, de ligne de commande ou de script dans l'archive ni sur le disque."""
    problems: list[str] = []
    modules = embedded_modules(app)
    value = flavor_value(modules)
    if value is not True:
        problems.append(f"core._build_flavor_generated : STORE = {value!r} (attendu True)")
    for name in FORBIDDEN_MODULES:
        if name in modules or any(m.startswith(name + ".") for m in modules):
            problems.append(f"module interdit embarqué : {name}")
        relative = Path(*name.split("."))
        for suffix in (".py", ".pyc", ".so"):
            hits = [p for p in app.rglob(relative.name + suffix) if relative.parent.name in p.parts] if relative.parent.name else []
            if hits:
                problems.append(f"fichier du module interdit {name} sur le disque : {hits[0].relative_to(app)}")
    whisper = [str(p.relative_to(app)) for p in app.rglob("*") if re.search(r"whisper|ggml", p.name, re.I)]
    if whisper:
        problems.append(f"programme ou modèle Whisper dans le bundle : {whisper[:3]}")
    return problems, {"modules": len(modules), "store_flag": value}


def otool_dependencies(path: Path) -> list[str]:
    """Bibliothèques liées par un Mach-O (``otool -L``, première ligne = le fichier lui-même, exclue)."""
    out = _run(["otool", "-L", str(path)])
    if out.returncode != 0:
        return []
    return [line.split(" (")[0].strip() for line in out.stdout.splitlines()[1:] if line.strip()]


def bad_dependency(dependency: str) -> bool:
    return not dependency.startswith(SYSTEM_PREFIXES + INTERNAL_PREFIXES)


def check_dylibs(app: Path) -> tuple[list[str], dict[str, object]]:
    """(d) Les deux dylibs sont dans ``Contents/Frameworks`` ; aucun Mach-O du bundle ne dépend d'autre chose que du système
    ou de ``@rpath`` / ``@loader_path`` / ``@executable_path``."""
    problems = []
    frameworks = app / "Contents" / "Frameworks"
    for name in DYLIBS:
        if not (frameworks / name).is_file():
            problems.append(f"{name} absente de Contents/Frameworks")
    checked = 0
    for path in mach_o_files(app):
        checked += 1
        for dependency in otool_dependencies(path):
            if bad_dependency(dependency):
                problems.append(f"{path.relative_to(app)} dépend de {dependency}")
    return problems, {"mach_o_checked": checked}


def find_tools(app: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {"ffmpeg": [], "ffprobe": []}
    for path in app.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name in found:
            found[path.name].append(path)
    return found


def check_ffmpeg(app: Path) -> tuple[list[str], dict[str, object]]:
    """(e) Exactement un ``ffmpeg`` et un ``ffprobe`` dans le bundle, sans ``--enable-gpl`` ni ``--enable-nonfree``, qui
    démarrent sans bibliothèque tierce (ils sont statiques), et aucun autre programme de ce nom (ni imageio-ffmpeg…)."""
    problems: list[str] = []
    info: dict[str, object] = {}
    tools = find_tools(app)
    for name, paths in tools.items():
        if len(paths) != 1:
            problems.append(f"{name} : {len(paths)} exemplaires dans le bundle (attendu 1) : {[str(p.relative_to(app)) for p in paths]}")
            continue
        done = _run([str(paths[0]), "-version"], timeout=30)
        text = done.stdout
        if done.returncode != 0:
            problems.append(f"{name} -version échoue : {done.stderr[-200:]}")
            continue
        flags = [flag for flag in FORBIDDEN_FFMPEG_FLAGS if flag in text]
        if flags:
            problems.append(f"{name} n'est pas LGPL : {flags}")
        info[name] = {"path": str(paths[0].relative_to(app)), "first_line": text.splitlines()[0] if text else ""}
    # Qt Multimedia embarque ses propres bibliothèques FFmpeg (lecture du moniteur) : ce ne sont pas des programmes et l'application
    # ne les lance pas, mais elles doivent être LGPL ; tout AUTRE élément FFmpeg est une erreur.
    qt_libs, extra = [], []
    for p in sorted(app.rglob("*")):
        if not p.is_file() or p.is_symlink() or p.name in ("ffmpeg", "ffprobe") or not re.search(r"ffmpeg|imageio|^lib(av|sw|postproc)", p.name, re.I):
            continue
        if "PySide6/Qt" in str(p) and re.match(r"lib(av\w+|sw\w+|postproc)\.\d+\.dylib$", p.name):
            data = p.read_bytes()
            if b"license: LGPL" not in data or b"license: GPL" in data:
                problems.append(f"{p.relative_to(app)} n'annonce pas une licence LGPL")
            qt_libs.append(p.name)
        elif p.name == "libffmpegmediaplugin.dylib":
            qt_libs.append(p.name)
        else:
            extra.append(str(p.relative_to(app)))
    if extra:
        problems.append(f"autres éléments FFmpeg dans le bundle : {extra[:5]}")
    info["qt_multimedia_ffmpeg_libs"] = qt_libs
    return problems, info


def scan_machine_paths(app: Path, markers: Iterable[str]) -> list[str]:
    """(f) Chemins de la machine de construction dans les ``Info.plist`` et les Mach-O. Les ``markers`` sont les chemins à
    chercher (racine du dépôt, dossier personnel, dossiers temporaires)."""
    needles = [m.encode() for m in markers if m]
    problems = []
    for plist in app.rglob("Info.plist"):
        with plist.open("rb") as stream:
            text = repr(plistlib.load(stream))
        for marker in markers:
            if marker and marker in text:
                problems.append(f"{plist.relative_to(app)} contient {marker}")
    for path in mach_o_files(app):
        data = path.read_bytes()
        for needle in needles:
            if needle in data:
                problems.append(f"{path.relative_to(app)} contient {needle.decode()}")
    return problems


GUARDED_IMPORTS = {
    # Importeurs protégés par ``is_store()`` (vérifié à la main le 2026-11-07, un par un) : la saveur Store ne les exécute pas.
    ("core.user_settings", "core.updates"), ("ui.main_window_mixins.update_flavor", "ui.main_window_mixins.updates"),
    ("main", "core.cli"), ("main", "core.update_service"), ("main", "core.update_signature"),
}


def _module_name(path: Path, root: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    package = parts[-1] == "__init__"
    return ".".join(parts[:-1] if package else parts), package


def check_missing_imports(app: Path, root: Path = ROOT) -> tuple[list[str], dict[str, object]]:
    """Import caché manquant : aucun module PRÉSENT dans le bundle n'importe (même en différé, dans une fonction) un module des
    sources ``core`` / ``ui`` / ``kut3d`` qui est ABSENT du bundle, hors les importeurs gardés par ``is_store()``
    (:data:`GUARDED_IMPORTS`). Les absents sont ceux que PyInstaller n'a pas trouvés ou que ``build.py`` exclut."""
    present = set(embedded_modules(app))
    sources = {}
    for package in ("core", "ui", "kut3d"):
        for path in (root / package).rglob("*.py"):
            sources[_module_name(path, root)[0]] = path
    missing = {name for name in sources if name not in present and not name.endswith("_generated")}
    problems: list[str] = []
    importers = [(name, path) for name, path in sources.items() if name in present] + [("main", root / "main.py")]
    for name, path in importers:
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package.split(".")
                module = ".".join(base[: len(base) - (node.level - 1)] + ([node.module] if node.module else [])) if node.level else node.module or ""
                targets = [module] + [f"{module}.{alias.name}" for alias in node.names]
            for target in targets:
                if target in missing and (name, target) not in GUARDED_IMPORTS:
                    problems.append(f"{name} (ligne {node.lineno}) importe {target}, absent du bundle")
    return problems, {"absent_du_bundle": len(missing)}


def largest(app: Path, count: int = 20) -> list[tuple[int, str]]:
    """(g) Les ``count`` plus gros fichiers du bundle (liens exclus, octets)."""
    sizes = [(p.stat().st_size, str(p.relative_to(app))) for p in app.rglob("*") if p.is_file() and not p.is_symlink()]
    return sorted(sizes, reverse=True)[:count]


def bundle_size(app: Path) -> int:
    return sum(p.stat().st_size for p in app.rglob("*") if p.is_file() and not p.is_symlink())


def run_smoke(app: Path, home: Path, config: Path, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    """(a) Lance le bundle avec ``--smoke-test`` (déjà dans ``main.py`` : ferme la fenêtre puis exécute les autocontrôles)."""
    config.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    try:
        return _run([str(executable_of(app)), "--smoke-test"], env=isolated_environment(home, config), cwd=str(home), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:       # échec de vérification lisible, pas de traceback
        return subprocess.CompletedProcess([], 1, "", f"lancement impossible : {error}")


def check_info_plist(app: Path) -> tuple[list[str], dict[str, object]]:
    """Clés d'``Info.plist`` propres au Store, présentes dans le bundle construit."""
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    problems = [f"Info.plist : clé {key} absente" for key in
                ("LSApplicationCategoryType", "LSMinimumSystemVersion", "NSMicrophoneUsageDescription", "CFBundleIdentifier")
                if key not in info]
    return problems, {key: info.get(key) for key in ("CFBundleIdentifier", "LSMinimumSystemVersion", "LSApplicationCategoryType")}


def run_all(app: Path, *, smoke: bool = True) -> dict[str, object]:
    """Tous les contrôles ; ``problems`` vide = bundle conforme."""
    app = app.resolve()          # absolu AVANT tout sous-processus (cwd changé) ; voir executable_of
    problems: list[str] = []
    report: dict[str, object] = {"app": str(app)}
    with tempfile.TemporaryDirectory(prefix="kut-store-check-") as scratch:
        base = Path(scratch)
        if smoke:
            done = run_smoke(app, base / "home", base / "config")
            report["smoke_returncode"] = done.returncode
            if done.returncode != 0:
                problems.append(f"smoke test : code {done.returncode} ; {done.stderr[-400:]}")
        for check in (check_modules, check_missing_imports, check_dylibs, check_ffmpeg, check_info_plist):
            found, info = check(app)
            problems += found
            report[check.__name__] = info
    markers = {str(ROOT), str(Path.home()), "/private/tmp/claude", "/Users/" + Path.home().name}
    problems += scan_machine_paths(app, sorted(markers))
    report["size_bytes"] = bundle_size(app)
    report["largest"] = largest(app)
    report["problems"] = problems
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    paths = [a for a in args if not a.startswith("--")]
    app = (Path(paths[0]) if paths else DEFAULT_APP).resolve()
    if sys.platform != "darwin" or not executable_of(app).is_file():
        print(f"Bundle introuvable ou plateforme non macOS : {app}", file=sys.stderr)
        return 2
    report = run_all(app)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Taille : {report['size_bytes'] / 1e6:.0f} Mo")  # type: ignore[operator]
        for size, name in report["largest"]:  # type: ignore[attr-defined]
            print(f"  {size / 1e6:8.1f} Mo  {name}")
        for problem in report["problems"]:  # type: ignore[attr-defined]
            print(f"PROBLEME : {problem}")
        print("OK" if not report["problems"] else "ECHEC")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
