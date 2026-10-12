"""Les en-têtes de sécurité du site (``website/_headers``) restent d'accord avec ``website/index.html``.

La CSP autorise le script et la feuille de style **en ligne** par leur empreinte SHA-256. Modifier l'un d'eux sans mettre
l'en-tête à jour ferait bloquer par les navigateurs le style ou le script du site en production (page sans mise en forme,
version de la release jamais affichée), sans qu'aucun test d'application ne le voie : c'est ce test qui le voit.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
HEADERS = (ROOT / "website" / "_headers").read_text(encoding="utf-8")


def _hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def _csp() -> dict[str, list[str]]:
    line = next(line for line in HEADERS.splitlines() if line.strip().startswith("Content-Security-Policy:"))
    directives = line.split(":", 1)[1].split(";")
    parsed: dict[str, list[str]] = {}
    for directive in directives:
        parts = directive.split()
        if parts:
            parsed[parts[0]] = parts[1:]
    return parsed


def test_every_inline_script_and_style_is_allowed_by_its_hash():
    csp = _csp()
    for tag, directive in (("script", "script-src"), ("style", "style-src")):
        for content in re.findall(rf"<{tag}>(.*?)</{tag}>", HTML, re.S):
            expected = f"'{_hash(content)}'"
            assert expected in csp[directive], (
                f"L'empreinte du <{tag}> en ligne a changé : mettez {expected} dans {directive} de website/_headers."
            )


def test_the_policy_allows_nothing_the_page_does_not_use():
    csp = _csp()
    assert csp["default-src"] == ["'none'"]
    assert "'unsafe-inline'" not in " ".join(csp["script-src"] + csp["style-src"])
    assert "'unsafe-eval'" not in HEADERS
    assert csp["connect-src"] == ["https://api.github.com"], "le seul appel réseau du site"
    assert csp["frame-ancestors"] == ["'none'"] and csp["base-uri"] == ["'none'"]
    # Le site ne charge rien d'extérieur, et n'a pas de gestionnaire d'événement ni d'attribut style en ligne
    # (la CSP les bloquerait) : si cela change, la politique doit changer en même temps.
    assert not re.search(r'<(?:script|link|img|iframe)[^>]+(?:src|href)="https?:', HTML.replace('<a ', '')), \
        "ressource externe : à autoriser explicitement dans la CSP"
    assert not re.search(r'\son[a-z]+="', HTML) and not re.search(r'\sstyle="', HTML)


def test_the_other_security_headers_are_present():
    for header in ("X-Content-Type-Options: nosniff", "X-Frame-Options: DENY", "Referrer-Policy:",
                   "Permissions-Policy:", "Cross-Origin-Opener-Policy: same-origin"):
        assert header in HEADERS, f"en-tête manquant : {header}"


def test_the_script_only_writes_text_never_markup():
    """La version de la release vient d'une API tierce : elle ne doit jamais être insérée comme du HTML."""
    script = "".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))
    assert "textContent" in script
    assert "innerHTML" not in script and "outerHTML" not in script and "insertAdjacentHTML" not in script
    assert "eval(" not in script and "document.write" not in script
