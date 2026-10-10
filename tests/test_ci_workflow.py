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
    assert set(jobs) == {"test-and-build", "macos-libass", "linux-ffmpeg7"}


def test_the_ffmpeg7_job_really_tests_ffmpeg_7_and_python_3_13_and_blocks() -> None:
    """FFmpeg 7.0 / 7.1 se comporte autrement que 6.1 et 8+ (``setpts`` efface la cadence) : le job le garde testé."""
    job = _without_comments(_jobs()["linux-ffmpeg7"])
    assert "container: debian:trixie" in job
    assert '"ffmpeg version 7."*) ;;' in job, "une autre version majeure doit faire échouer le job, pas passer en silence"
    assert "sys.version_info[:2] == (3, 13)" in job
    assert re.search(r"python -m pytest -q -n auto --timeout=\d+", job), "toute la suite, avec un délai par test"
    assert "continue-on-error" not in job and not re.search(r"^\s+needs:", job, re.MULTILINE)
    assert re.search(r"^\s+timeout-minutes: \d+\s*$", job, re.MULTILINE)


def test_the_workflow_keeps_read_only_permissions_and_major_pinned_actions() -> None:
    text = _without_comments(WORKFLOW.read_text(encoding="utf-8"))
    assert re.search(r"^permissions:\n  contents: read\s*$", text, re.MULTILINE)
    actions = re.findall(r"^\s+(?:- )?uses: (\S+)", text, re.MULTILINE)
    assert actions, "aucune action trouvée : l'analyse du fichier est en défaut"
    assert all(re.fullmatch(r"[\w./-]+@v\d+", action) for action in actions), actions


# ---------------------------------------------------------------------------
# Couverture de code (docs/coverage.md) : mesurée une fois, sur Linux, et bloquante par son cliquet.
# ---------------------------------------------------------------------------


def _steps(job: str) -> list[str]:
    """Texte de chaque étape d'un job (élément de liste à six espaces d'indentation sous ``steps:``)."""
    steps: list[list[str]] = []
    for line in _without_comments(job).splitlines():
        if line.startswith("      - "):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return ["\n".join(step) for step in steps]


def _condition(step: str) -> str | None:
    match = re.search(r"^\s+if: (.+?)\s*$", step, re.MULTILINE)
    return match.group(1) if match else None


def _matrix_steps() -> list[str]:
    return _steps(_jobs()["test-and-build"])


def test_coverage_is_measured_once_on_linux_with_the_same_condition_as_mypy() -> None:
    steps = _matrix_steps()
    mypy = [step for step in steps if "python -m mypy" in step]
    assert len(mypy) == 1, "l'étape mypy sert de référence à la condition Linux"
    linux_only = _condition(mypy[0])
    assert linux_only == "runner.os == 'Linux'"

    suites = [step for step in steps if "python -m pytest" in step]
    covered = [step for step in suites if "--cov" in step]
    plain = [step for step in suites if "--cov" not in step]
    assert len(covered) == 1 and len(plain) == 1, "une suite instrumentée (Linux) et une suite nue (macOS, Windows)"
    assert _condition(covered[0]) == linux_only
    assert _condition(plain[0]) == "runner.os != 'Linux'", "macOS et Windows lancent toujours toute la suite"
    for flag in ("--cov-branch", "--cov-report=json", "--cov-report=xml", "--timeout=600", "-n auto"):
        assert flag in covered[0], f"{flag} manque à la suite instrumentée"
    assert "--timeout=600" in plain[0] and "-n auto" in plain[0]


def test_the_coverage_ratchet_blocks_the_linux_job_and_reports_even_on_failure() -> None:
    steps = _matrix_steps()
    ratchets = [step for step in steps if "tools/coverage_ratchet.py coverage.json" in step]
    assert len(ratchets) == 1
    ratchet = ratchets[0]
    assert _condition(ratchet) == "runner.os == 'Linux'"
    assert "continue-on-error" not in ratchet, "un cliquet non bloquant laisserait la couverture régresser en silence"
    assert re.search(r"^\s+shell: bash\s*$", ratchet, re.MULTILINE), "bash explicite = pipefail : « | tee » garde le code"
    assert any("GITHUB_STEP_SUMMARY" in step and "coverage-ratchet.txt" in step for step in steps)
    uploads = [step for step in steps if "upload-artifact" in step and "coverage.json" in step]
    assert len(uploads) == 1 and "coverage.xml" in uploads[0]
    assert re.search(r"retention-days: 7\b", uploads[0])
    assert "runner.os == 'Linux'" in (_condition(uploads[0]) or "")


def test_the_libass_job_does_not_measure_coverage() -> None:
    job = _without_comments(_jobs()["macos-libass"])
    assert "--cov" not in job and "coverage_ratchet" not in job


def test_every_suite_dumps_the_python_stacks_before_its_per_test_timeout() -> None:
    """Un blocage natif qui tient le GIL fige le minuteur de pytest-timeout : seul faulthandler laisse une trace.

    ``faulthandler_timeout`` écrit depuis un thread C qui n'a pas besoin du GIL, sur le stderr hérité du worker
    xdist ; il doit partir avant ``--timeout``, sinon un blocage ordinaire tue le worker sans pile dans le journal.
    """
    commands = re.findall(r"python -m pytest .*", _without_comments(WORKFLOW.read_text(encoding="utf-8")))
    assert len(commands) == 4, commands
    for command in commands:
        timeout = re.search(r"--timeout=(\d+)\b", command)
        dump = re.search(r"-o faulthandler_timeout=(\d+)\b", command)
        assert timeout and dump, command
        assert 0 < int(dump.group(1)) < int(timeout.group(1)), command
