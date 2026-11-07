"""Le miroir GitLab ne tourne que configuré, ne montre jamais le jeton et n'a que des droits en lecture."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "mirror-gitlab.yml"

pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="pas de .github/workflows dans cette arborescence")


def _text() -> str:
    return "\n".join(line for line in WORKFLOW.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))


def test_it_runs_on_pushes_to_main_and_on_tags_only() -> None:
    head = _text().split("\njobs:")[0]
    assert re.search(r"^  push:\n    branches: \[main\]\n    tags: \[\"\*\"\]", head, re.MULTILINE)
    assert "pull_request" not in head, "un miroir ne doit jamais partir d'une PR (fork compris)"


def test_it_skips_cleanly_without_the_two_secrets() -> None:
    text = _text()
    assert "secrets.GITLAB_MIRROR_URL" in text and "secrets.GITLAB_MIRROR_TOKEN" in text
    assert "miroir non configuré" in text.lower()
    assert 'echo "configured=false"' in text
    # Chaque étape qui touche au dépôt distant est conditionnée par la configuration ; la vérification, elle, n'échoue pas.
    gated = re.findall(r"^        if: steps\.config\.outputs\.configured == 'true'\s*$", text, re.MULTILINE)
    assert len(gated) == 2, "checkout et poussée doivent dépendre de la configuration"
    check = text.split("- name: Check the mirror is configured")[1].split("- uses:")[0]
    assert "exit 1" not in check


def test_the_token_is_never_in_clear_or_printed() -> None:
    text = _text()
    assert not re.search(r"glpat-|oauth2:[A-Za-z0-9_-]{8,}", text), "jeton en clair"
    assert "::add-mask::$GITLAB_MIRROR_TOKEN" in text
    assert "http.extraheader" in text, "le jeton passe par un en-tête, pas par l'URL"
    # Le jeton et l'URL ne sont lus que par l'environnement : jamais interpolés dans une commande shell.
    assert not re.search(r"run:[^\n]*\$\{\{\s*secrets\.", text)
    body = text.split("run: |")[-1]
    assert not re.search(r"echo[^\n]*\$GITLAB_MIRROR_TOKEN(?!\))", body.replace('echo "::add-mask::$GITLAB_MIRROR_TOKEN"', ""))
    assert "set -x" not in text and "--verbose" not in text


def test_permissions_are_minimal_and_actions_pinned() -> None:
    text = _text()
    assert re.search(r"^permissions:\n  contents: read\s*$", text, re.MULTILINE)
    assert "persist-credentials: false" in text
    assert "--force" not in text and "--mirror" not in text, "pas de poussée forcée vers le miroir"
    actions = re.findall(r"^\s+(?:- )?uses: (\S+)", text, re.MULTILINE)
    assert actions and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) for action in actions), actions
