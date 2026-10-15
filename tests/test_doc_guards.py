"""Gardes de documentation : références de lien du CHANGELOG et nombre de verbes en dur.

Deux remarques revenues en revue : une version ajoutée au CHANGELOG sans sa ligne de lien
(ou un `[Unreleased]` qui compare depuis une ancienne version), et un nombre de verbes écrit
en dur dans une doc vivante, vite faux dès qu'un verbe s'ajoute.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/Kyyzen0/Kut-Studio"
_VERSION = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$", re.M)
_LINK = re.compile(r"^\[([^\]]+)\]: (\S+)\s*$", re.M)


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_changelog(text: str) -> list[str]:
    """Liste les incohérences entre sections de version et références de lien."""
    errors: list[str] = []
    versions = sorted(_VERSION.findall(text), key=_vkey)
    links = dict(_LINK.findall(text))
    if not versions:
        return ["aucune section de version"]
    for i, v in enumerate(versions):
        url = links.get(v)
        if url is None:
            errors.append(f"[{v}] : référence de lien manquante")
            continue
        if i == 0:
            ok = url == f"{REPO}/releases/tag/v{v}"
        else:
            ok = url == f"{REPO}/compare/v{versions[i - 1]}...v{v}"
        if not ok:
            errors.append(f"[{v}] : lien inattendu {url}")
    expected = f"{REPO}/compare/v{versions[-1]}...HEAD"
    if links.get("Unreleased") != expected:
        errors.append(f"[Unreleased] doit valoir {expected}, trouvé {links.get('Unreleased')}")
    return errors


def test_changelog_links_match_versions() -> None:
    assert check_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []


def test_changelog_guard_detects_missing_reference_and_stale_unreleased() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    no_ref = "\n".join(line for line in text.splitlines() if not line.startswith("[0.6.1]: "))
    assert any("0.6.1" in e for e in check_changelog(no_ref))
    stale = text.replace("compare/v0.6.1...HEAD", "compare/v0.6.0...HEAD")
    assert any("Unreleased" in e for e in check_changelog(stale))
    new_section = text.replace("## [0.6.1] - ", "## [0.6.2] - 2026-10-15\n\n## [0.6.1] - ", 1)
    assert check_changelog(new_section)


_HARD_COUNT = re.compile(r"\b\d+ (?:verbes|verbs)\b", re.I)


def _live_docs() -> list[Path]:
    # CHANGELOG exclu : ses sections de versions publiées décrivent l'état d'alors.
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_no_hardcoded_verb_count_in_live_docs() -> None:
    found = [
        f"{p.relative_to(ROOT)}: {m.group(0)}"
        for p in _live_docs()
        if p.exists()
        for m in _HARD_COUNT.finditer(p.read_text(encoding="utf-8"))
    ]
    assert found == [], "nombre de verbes en dur (dis « les verbes », sans chiffre) : " + ", ".join(found)
