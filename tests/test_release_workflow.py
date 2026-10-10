"""Le workflow de release garde ses garanties (lecture du YAML comme texte, comme ``test_ci_workflow``).

* déclenché par un tag ``v*.*.*`` ou à la main ; seul un tag publie, et seulement s'il égale ``core/app_version.py`` ;
* les cibles construites sont exactement celles de la convention (``core.release_assets.RELEASE_TARGETS``) ;
* écriture sur le dépôt réservée au job de publication, qui vérifie juste avant d'envoyer ;
* secrets de signature limités aux étapes macOS ; aucun certificat, clé ni jeton dans le dépôt.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from core.release_assets import RELEASE_TARGETS

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="pas de .github/workflows dans cette arborescence")


def _text() -> str:
    return "\n".join(line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def _jobs() -> dict[str, str]:
    lines = _text().splitlines()
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


def _steps(job: str) -> list[str]:
    return re.split(r"\n      - ", "\n" + job)[1:]


def test_triggered_by_version_tags_and_by_hand_never_by_pull_requests():
    text = _text()
    assert re.search(r'^  push:\n    tags: \["v\*\.\*\.\*"\]$', text, re.MULTILINE)
    assert re.search(r"^  workflow_dispatch:", text, re.MULTILINE)
    assert "pull_request" not in text
    assert "branches:" not in text


def test_the_tag_is_checked_against_the_application_version_first():
    jobs = _jobs()
    assert "python -m tools.release check-tag" in jobs["version"]
    assert "needs: version" in jobs["build"]
    assert "--version ${{ needs.version.outputs.version }}" in jobs["build"]


def test_the_matrix_builds_exactly_the_conventional_targets_and_checks_the_machine():
    build = _jobs()["build"]
    targets = re.findall(r"target: ([a-z0-9-]+)", build)
    assert sorted(targets) == sorted(target.slug for target in RELEASE_TARGETS)
    assert "python -m tools.release check-target --expect ${{ matrix.target }}" in build


def test_a_release_is_built_from_a_green_state():
    build = _jobs()["build"]
    for command in ("python -m ruff check .", "python -m pytest", "python main.py --smoke-test", "python build.py",
                    "--smoke-test"):
        assert command in build
    assert build.index("python -m pytest") < build.index("python build.py")
    assert "KUT_STUDIO_UPDATE_CHECK: \"off\"" in build


def test_a_hung_test_suite_leaves_its_stacks_and_fails_before_the_job_budget():
    """v0.3.0 : un blocage natif sous Windows a gelé 50 min sans trace, jusqu'à l'annulation du job."""
    build = _jobs()["build"]
    suites = [step for step in _steps(build) if "python -m pytest" in step]
    assert len(suites) == 1
    timeout = re.search(r"--timeout=(\d+)\b", suites[0])
    dump = re.search(r"-o faulthandler_timeout=(\d+)\b", suites[0])
    assert timeout and dump and 0 < int(dump.group(1)) < int(timeout.group(1))
    step_minutes = re.search(r"^\s+timeout-minutes: (\d+)\s*$", suites[0], re.MULTILINE)
    job_minutes = re.search(r"^    timeout-minutes: (\d+)\s*$", build, re.MULTILINE)
    assert step_minutes and job_minutes and int(step_minutes.group(1)) < int(job_minutes.group(1))


def test_only_the_publish_job_can_write_and_only_for_a_tag():
    text = _text()
    assert re.search(r"^permissions:\n  contents: read$", text, re.MULTILINE)
    jobs = _jobs()
    writers = [name for name, body in jobs.items() if "contents: write" in body]
    assert writers == ["publish"]
    publish = jobs["publish"]
    assert "needs.version.outputs.publish == 'true'" in publish
    assert "startsWith(github.ref, 'refs/tags/v')" in publish
    assert "gh release create" in publish and "--verify-tag" in publish
    assert publish.index("python -m tools.release verify") < publish.index("gh release create")
    assert all("gh release" not in body for name, body in jobs.items() if name != "publish")


def test_checksums_are_generated_and_verified_before_anything_is_published():
    checksums = _jobs()["checksums"]
    assert "python -m tools.release checksums release-assets" in checksums
    assert "python -m tools.release verify release-assets" in checksums
    assert "needs: [version, build]" in checksums
    assert "needs: [version, checksums]" in _jobs()["publish"]


def test_the_real_macos_signing_status_reaches_the_notes_without_a_default():
    text = _text()
    assert "macos_signing.sh notarize dist/Kut-Studio.app signing-status/macos.txt" in text
    assert text.count('--signing "$(cat signing-status/macos.txt)"') == 2
    assert "|| echo adhoc" not in text, "un état de signature inconnu ne doit jamais devenir « adhoc » en silence"


def test_signing_secrets_only_reach_macos_steps():
    build = _jobs()["build"]
    for step in _steps(build):
        if "secrets." in step:
            assert "if: runner.os == 'macOS'" in step, step.splitlines()[0]
    for name, body in _jobs().items():
        if name != "build":
            assert "secrets." not in body, f"secret utilisé par le job {name}"
    assert "always() && runner.os == 'macOS'" in build and "macos_signing.sh cleanup" in build


def test_secret_names_are_the_documented_ones():
    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", _text()))
    assert used == {
        "MACOS_CERTIFICATE_P12_BASE64", "MACOS_CERTIFICATE_PASSWORD", "MACOS_SIGNING_IDENTITY",
        "APPLE_API_KEY_P8_BASE64", "APPLE_API_KEY_ID", "APPLE_API_ISSUER_ID",
    }
    docs = (ROOT / "docs" / "updates.md").read_text(encoding="utf-8")
    assert all(name in docs for name in used), "chaque secret est documenté dans docs/updates.md"


def test_actions_are_pinned_to_a_major_version():
    actions = re.findall(r"^\s+(?:- )?uses: (\S+)", _text(), re.MULTILINE)
    assert actions
    assert all(re.fullmatch(r"[\w./-]+@v\d+", action) for action in actions), actions


def test_no_certificate_key_or_token_is_tracked_in_the_repository():
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git indisponible : liste des fichiers suivis inconnue")
    forbidden = re.compile(r"\.(p12|p8|pfx|cer|mobileprovision|keychain-db|pem|key)$", re.IGNORECASE)
    assert [name for name in tracked.splitlines() if forbidden.search(name)] == []
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.p12", "*.p8", "*.pfx", "*.cer", "release-assets/"):
        assert pattern in ignored


# ---------------------------------------------------------------------------
# Dépendances figées : un même tag reconstruit les mêmes paquets (constraints.txt).
# ---------------------------------------------------------------------------


def _requirements(path: Path) -> list[str]:
    """Spécifications d'un fichier requirements (``-r`` suivi, commentaires et lignes vides ignorés)."""
    specs: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("-r "):
            specs += _requirements(path.parent / line[3:].strip())
        elif line:
            specs.append(line)
    return specs


def _pins() -> dict[str, str]:
    from packaging.utils import canonicalize_name

    pins: dict[str, str] = {}
    for spec in _requirements(ROOT / "constraints.txt"):
        name, separator, version = spec.partition("==")
        assert separator and version and not re.search(r"[<>!~*,;]", version), f"contrainte non figée : {spec}"
        key = canonicalize_name(name)
        assert key not in pins, f"contrainte en double : {spec}"
        pins[key] = version
    return pins


def test_the_release_installs_the_pinned_constraints():
    build = _jobs()["build"]
    installs = re.findall(r"pip install [^\n]*", build)
    assert installs and all("-c constraints.txt" in line for line in installs), installs


def test_every_direct_dependency_is_pinned_inside_its_allowed_range():
    """Une plage élargie dans requirements*.txt sans mettre constraints.txt à jour ferait construire autre chose."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    pins = _pins()
    for spec in _requirements(ROOT / "requirements-dev.txt"):
        requirement = Requirement(spec)
        version = pins.get(canonicalize_name(requirement.name))
        assert version is not None, f"{requirement.name} n'est pas figé dans constraints.txt"
        assert requirement.specifier.contains(version, prereleases=True), (
            f"constraints.txt fige {requirement.name}=={version}, hors de la plage « {requirement.specifier} »"
        )
