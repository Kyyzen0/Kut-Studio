"""Gardes de documentation : références de lien du CHANGELOG et nombre de verbes en dur.

Deux remarques revenues en revue : une version ajoutée au CHANGELOG sans sa ligne de lien
(ou un `[Unreleased]` qui compare depuis une ancienne version), et un nombre de verbes écrit
en dur dans une doc vivante, vite faux dès qu'un verbe s'ajoute.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.versioning import Version

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/Kyyzen0/Kut-Studio"
_VERSION = re.compile(r"^## \[([^\]]+)\] - \d{4}-\d{2}-\d{2}\s*$", re.M)
_LINK = re.compile(r"^\[([^\]]+)\]: (\S+)\s*$", re.M)


def check_changelog(text: str) -> list[str]:
    """Liste les incohérences entre sections de version et références de lien."""
    errors: list[str] = []
    # Ordre SemVer 2.0 (préversions comprises) : le parseur de l'application, pas un ordre de texte.
    parsed = [(Version.parse(h), h) for h in _VERSION.findall(text)]
    parsed.sort(key=lambda pair: pair[0])
    versions = [h for _, h in parsed]
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


def _with_prerelease(text: str) -> str:
    """CHANGELOG fictif : 0.6.2 stable puis 0.7.0-beta.1 et 0.7.0-rc.1, liens corrects."""
    head = "## [0.7.0-rc.1] - 2026-10-20\n\n## [0.7.0-beta.1] - 2026-10-18\n\n## [0.6.1] - "
    out = text.replace("## [0.6.1] - ", head, 1)
    out = out.replace("compare/v0.6.1...HEAD", "compare/v0.7.0-rc.1...HEAD")
    extra = (
        f"[0.7.0-rc.1]: {REPO}/compare/v0.7.0-beta.1...v0.7.0-rc.1\n"
        f"[0.7.0-beta.1]: {REPO}/compare/v0.6.1...v0.7.0-beta.1\n"
    )
    return out.replace("[0.6.1]: ", extra + "[0.6.1]: ", 1)


def test_prerelease_order_is_semver() -> None:
    order = [Version.parse(v) for v in ("0.7.0-beta.1", "0.7.0-rc.1", "0.7.0", "0.7.1")]
    assert order == sorted(reversed(order))


def test_changelog_guard_handles_prereleases() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    good = _with_prerelease(text)
    assert check_changelog(good) == []
    no_link = "\n".join(x for x in good.splitlines() if not x.startswith("[0.7.0-beta.1]: "))
    assert any("0.7.0-beta.1" in e for e in check_changelog(no_link))
    stale = good.replace("compare/v0.7.0-rc.1...HEAD", "compare/v0.6.1...HEAD")
    assert any("Unreleased" in e for e in check_changelog(stale))
    # Stable publiée après ses préversions : [Unreleased] compare depuis elle, et elle suit la rc.
    stable = good.replace("## [0.7.0-rc.1] - ", "## [0.7.0] - 2026-10-22\n\n## [0.7.0-rc.1] - ", 1)
    stable = stable.replace("compare/v0.7.0-rc.1...HEAD", "compare/v0.7.0...HEAD")
    stable = stable.replace("[0.7.0-rc.1]: ", f"[0.7.0]: {REPO}/compare/v0.7.0-rc.1...v0.7.0\n[0.7.0-rc.1]: ", 1)
    assert check_changelog(stable) == []
