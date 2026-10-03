"""Le job ``macos-libass`` de la CI garde ses garanties : un job vert veut dire « libass testé ».

Lecture du fichier comme du texte (pas de dépendance YAML) : on ne vérifie que les invariants dont dépend la
couverture des sous-titres sur macOS (``docs/ci-libass.md``). Retirer l'un d'eux rendrait le job vert sans que
libass ait servi, ou ferait du job dédié une partie de la matrice principale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "multiplatform.yml"

pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="pas de .github/workflows dans cette arborescence")


def _jobs() -> dict[str, str]:
    """Texte de chaque job (clé à deux espaces d'indentation sous ``jobs:``)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index("jobs:") + 1
    jobs: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines[start:]:
        header = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if header:
            current = jobs.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(body) for name, body in jobs.items()}


def _without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_dedicated_libass_job_turns_a_skip_into_a_failure_and_runs_only_libass_tests() -> None:
    job = _without_comments(_jobs()["macos-libass"])
    assert re.search(r'^\s+KUT_STUDIO_REQUIRE_LIBASS: "1"\s*$', job, re.MULTILINE)
    assert "runs-on: macos-latest" in job
    assert re.search(r"pytest .*-m libass\b", job), "le job doit sélectionner les tests marqués libass"
    assert "KUT_STUDIO_FFMPEG=" in job and "KUT_STUDIO_FFPROBE=" in job, "ffmpeg-full doit être rendu visible"
    assert re.search(r"brew install ffmpeg-full", job)


def test_the_dedicated_libass_job_is_blocking_bounded_and_independent() -> None:
    job = _without_comments(_jobs()["macos-libass"])
    assert "continue-on-error" not in job, "un job non bloquant rendrait la couverture libass optionnelle en silence"
    assert re.search(r"^\s+timeout-minutes: \d+\s*$", job, re.MULTILINE)
    assert not re.search(r"^\s+needs:", job, re.MULTILINE), "le job dédié ne dépend pas de la matrice (ni l'inverse)"


def test_the_main_matrix_is_unchanged_and_does_not_require_libass() -> None:
    jobs = _jobs()
    matrix = _without_comments(jobs["test-and-build"])
    assert "os: [macos-latest, windows-latest, ubuntu-latest]" in matrix
    assert "fail-fast: false" in matrix
    assert "KUT_STUDIO_REQUIRE_LIBASS" not in matrix, "la matrice principale saute sans libass (macOS) comme avant"
    assert "|| brew install ffmpeg" in matrix, "la matrice garde l'installation du FFmpeg Homebrew sans libass"
    assert set(jobs) == {"test-and-build", "macos-libass"}


def test_the_workflow_keeps_read_only_permissions_and_major_pinned_actions() -> None:
    text = _without_comments(WORKFLOW.read_text(encoding="utf-8"))
    assert re.search(r"^permissions:\n  contents: read\s*$", text, re.MULTILINE)
    actions = re.findall(r"^\s+(?:- )?uses: (\S+)", text, re.MULTILINE)
    assert actions, "aucune action trouvée : l'analyse du fichier est en défaut"
    assert all(re.fullmatch(r"[\w./-]+@v\d+", action) for action in actions), actions
