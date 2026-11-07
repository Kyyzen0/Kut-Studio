#!/usr/bin/env python3
"""Quels filtres FFmpeg l'application emploie-t-elle, et lesquels une construction **LGPL** (sans ``--enable-gpl``) n'a-t-elle pas ?

ADR-0013 (publication Mac App Store) : le FFmpeg embarqué n'a pas ``--enable-gpl``. Les filtres dont le ``configure`` de FFmpeg dit
``<nom>_filter_deps="gpl"`` n'y existent pas (``eq``, ``boxblur``, ``hqdn3d``, ``perspective``…) ; l'application ne doit en employer aucun.
Outil hors ligne, jamais importé par l'application ; la liste des filtres GPL et la lecture du code sont aussi celles du garde
``tests/test_ffmpeg_lgpl_guard.py``.

    python -m tools.ffmpeg_license_audit                    # le FFmpeg que l'application résout (tool_paths)
    python -m tools.ffmpeg_license_audit --ffmpeg /chemin/vers/ffmpeg
    python -m tools.ffmpeg_license_audit --list             # seulement les filtres que le code emploie

Sortie : la licence de cette construction (``ffmpeg -L`` et ``--enable-gpl`` de sa configuration), puis trois listes : les filtres GPL
trouvés dans le code (**échec**), les filtres que le code emploie et que ce FFmpeg n'a pas (**échec** : une construction LGPL qui en
manque), les filtres employés et présents (information). Code de sortie : 0 si les deux premières listes sont vides, 1 sinon.

**Comment le code est lu.** Les littéraux de chaîne de ``core``, ``ui``, ``kut3d`` et ``cli`` (arbre syntaxique, docstrings exclues) sont
parcourus ; un nom suivi de ``=`` (ou de ``@étiquette=``) au début du texte, ou après ``,`` ``;`` ``]``, est un filtre (les options viennent
après ``:`` et ne sont donc pas prises pour des filtres). Une chaîne composée (f-string) est lue morceau par morceau. Limites assumées :
un nom de filtre calculé à l'exécution (``f"{nom}=…"``) échappe à la lecture, d'où les tests qui construisent aussi les chaînes réelles
de tous les effets et étalonnages ; un nom employé sans option (``null``, ``split``) ne s'écrit pas ``nom=`` et n'est pas lu (aucun
filtre GPL ne s'utilise sans option dans le code).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GPL_FILTERS: frozenset[str] = frozenset({
    # ``<nom>_filter_deps="gpl"`` : réunion des ``configure`` de FFmpeg 6.1, 7.1 et 8.0 (lus le 2026-10-28, identiques).
    "blackframe", "boxblur", "boxblur_opencl", "colormatrix", "cover_rect", "cropdetect", "delogo", "eq", "find_rect", "fspp", "histeq",
    "hqdn3d", "interlace", "kerndeint", "mcdeint", "mpdecimate", "mptestsrc", "nnedi", "owdenoise", "perspective", "phase", "pp",
    "pp7", "pullup", "repeatfields", "sab", "signature", "smartblur", "spp", "stereo3d", "super2xsai", "tinterlace", "uspp",
    "vaguedenoiser",
    # Filtres qui exigent une bibliothèque externe GPL (``EXTERNAL_LIBRARY_GPL_LIST`` du même ``configure``).
    "frei0r", "frei0r_src", "rubberband", "vidstabdetect", "vidstabtransform",
})
"""Les filtres qui n'existent pas dans un FFmpeg sans ``--enable-gpl`` (ou sans la bibliothèque GPL qu'ils exigent)."""

SCANNED_DIRECTORIES = ("core", "ui", "kut3d", "cli")

NOT_FILTERS: frozenset[str] = frozenset({"filter", "m", "master", "out_time_ms", "ow", "range", "rdiv", "stream", "x"})
"""Noms lus en tête d'un texte qui sont des **options** (``curves@x master '…'`` d'un fichier de commandes, ``ow`` d'un ``pad``, ``-progress``…),
pas des filtres : écartés du rapport des filtres absents. Aucun n'est un nom de filtre FFmpeg."""

_TOKEN = re.compile(r"(?:^|(?<=[,;\]]))\s*(?P<name>[a-z][a-z0-9_]*)(?:@[A-Za-z0-9_]+)?=")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                nodes.add(id(body[0].value))
    return nodes


def string_literals(source: str) -> Iterator[tuple[int, str]]:
    """``(ligne, texte)`` de chaque littéral de chaîne du source (docstrings exclues) ; une f-string rend un texte par morceau constant."""
    tree = ast.parse(source)
    skipped = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
            yield node.lineno, node.value


def filters_in(text: str) -> set[str]:
    """Les noms de filtres FFmpeg cités dans ``text`` (un filtre : ``nom=``, ``nom@étiquette=`` en tête ou après ``, ; ]``)."""
    return {match.group("name") for match in _TOKEN.finditer(text)}


def scan_source(source: str) -> dict[str, list[int]]:
    """``{filtre: [lignes]}`` des filtres cités par les littéraux de ``source``."""
    found: dict[str, list[int]] = {}
    for line, text in string_literals(source):
        for name in filters_in(text):
            found.setdefault(name, []).append(line)
    return found


def python_files(root: Path = ROOT, directories: Iterable[str] = SCANNED_DIRECTORIES) -> Iterator[Path]:
    for directory in directories:
        base = root / directory
        if base.is_dir():
            yield from sorted(path for path in base.rglob("*.py") if "__pycache__" not in path.parts)


def scan_tree(root: Path = ROOT, directories: Iterable[str] = SCANNED_DIRECTORIES) -> dict[str, dict[str, list[int]]]:
    """``{filtre: {fichier relatif: [lignes]}}`` pour tout le code de l'application."""
    result: dict[str, dict[str, list[int]]] = {}
    for path in python_files(root, directories):
        try:
            found = scan_source(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name, lines in found.items():
            result.setdefault(name, {})[path.relative_to(root).as_posix()] = lines
    return result


def gpl_uses(root: Path = ROOT) -> dict[str, dict[str, list[int]]]:
    """Les emplois de filtres GPL dans le code de l'application (vide : conforme)."""
    return {name: places for name, places in scan_tree(root).items() if name in GPL_FILTERS}


def available_filters(ffmpeg: str) -> set[str]:
    """Les filtres de ``ffmpeg -filters`` (``set()`` si FFmpeg ne répond pas)."""
    try:
        text = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {match.group(1) for match in re.finditer(r"^\s*[TSC.|]{2,3}\s+(\S+)\s+[AVN|]*->[AVN|]*\s", text, re.MULTILINE)}


def license_report(ffmpeg: str) -> tuple[str, bool]:
    """``(texte, gpl)`` : la licence que ``ffmpeg -L`` annonce et si la configuration porte ``--enable-gpl``."""
    try:
        licence = subprocess.run([ffmpeg, "-hide_banner", "-L"], capture_output=True, text=True, timeout=30).stdout.strip()
        version = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return "FFmpeg illisible", False
    first = version.splitlines()[0] if version else "?"
    return f"{first}\n{licence.splitlines()[0] if licence else '?'}", "--enable-gpl" in version


def _resolve_ffmpeg(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    sys.path.insert(0, str(ROOT))
    from core.tool_paths import find_media_tool

    return find_media_tool("ffmpeg")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ffmpeg", help="exécutable à auditer (par défaut celui que l'application résout)")
    parser.add_argument("--list", action="store_true", help="n'affiche que les filtres employés par le code")
    parser.add_argument("--codecs", action="store_true",
                        help="tableau Markdown des encodeurs, décodeurs, muxers et accélérations employés (tools.ffmpeg_capabilities)")
    parser.add_argument("--strict", action="store_true", help="avec --codecs : échoue aussi pour un élément absent qui n'est pas GPL")
    args = parser.parse_args(argv)
    if args.codecs:
        return _codecs_report(args.ffmpeg, args.strict)

    used = scan_tree()
    if args.list:
        for name in sorted(used):
            print(name)
        return 0
    failures = 0
    print("Filtres GPL employés par le code (doit être vide) :")
    gpl = {name: places for name, places in used.items() if name in GPL_FILTERS}
    for name, places in sorted(gpl.items()):
        failures += 1
        print(f"  {name} : " + ", ".join(f"{path}:{lines[0]}" for path, lines in sorted(places.items())))
    if not gpl:
        print("  aucun")
    ffmpeg = _resolve_ffmpeg(args.ffmpeg)
    if ffmpeg is None:
        print("FFmpeg introuvable : seule la lecture du code est faite.")
        return 1 if failures else 0
    description, is_gpl = license_report(ffmpeg)
    print(f"\n{ffmpeg}\n{description}\nconstruction GPL (--enable-gpl) : {'oui' if is_gpl else 'non'}")
    available = available_filters(ffmpeg)
    # Une chaîne qui n'est pas un filtre (``key=valeur``) est lue comme un filtre : on ne signale que les noms qui, absents, sont
    # connus d'une construction complète (liste GPL) ou qui figurent dans les chaînes d'un fichier de filtres (tout nom lu en tête).
    missing = sorted(name for name in used if name not in available and name not in NOT_FILTERS and (name in GPL_FILTERS or _looks_like_filter(name, used)))
    print("\nFiltres employés que ce FFmpeg n'a pas :")
    for name in missing:
        failures += 1
        print(f"  {name} : " + ", ".join(sorted(used[name])[:3]))
    if not missing:
        print("  aucun")
    print(f"\n{len(used)} noms lus dans le code, {len(set(used) & available)} sont des filtres de ce FFmpeg.")
    return 1 if failures else 0


def _codecs_report(explicit: str | None, strict: bool) -> int:
    from tools.ffmpeg_capabilities import table

    ffmpeg = _resolve_ffmpeg(explicit)
    if ffmpeg is None:
        print("FFmpeg introuvable.")
        return 1
    text, missing = table(ffmpeg)
    print(text)
    for item in missing:
        print(f"absent et non GPL : {item.category} {item.name}", file=sys.stderr)
    return 1 if strict and missing else 0


def _looks_like_filter(name: str, used: dict[str, dict[str, list[int]]]) -> bool:
    """Un nom absent de ce FFmpeg est signalé s'il est écrit comme un filtre dans une chaîne d'export (``export_engine``, ``filter_graph``,
    ``compositing``…), pas dans un autre contexte (une clé de configuration ``nom=valeur``)."""
    return any(path.startswith("core/") and any(key in path for key in ("export", "filter", "composit", "effect", "grade", "color"))
               for path in used[name])


if __name__ == "__main__":
    raise SystemExit(main())
