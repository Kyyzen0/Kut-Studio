"""Cliquet de couverture (``tools/coverage_ratchet.py``) : régression bloquante, progrès signalé, entrées validées.

Aucun test ne lance une vraie mesure : les rapports ``coverage.json`` sont fabriqués ici, au format de coverage.py
(``meta.branch_coverage``, ``totals``, ``files[nom].summary``). La baseline réelle du dépôt est seulement relue,
pour qu'un fichier mal édité à la main échoue ici plutôt qu'en CI.
"""

from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path

import pytest

from tools import coverage_ratchet as ratchet


def _summary(statements: int, missing: int, branches: int = 0, missing_branches: int = 0) -> dict[str, object]:
    covered = statements - missing + branches - missing_branches
    total = statements + branches
    return {
        "num_statements": statements,
        "missing_lines": missing,
        "num_branches": branches,
        "missing_branches": missing_branches,
        "percent_covered": 100.0 * covered / total if total else 100.0,
    }


def _report(total: float, files: dict[str, dict[str, object]] | None = None, branch: bool = True) -> dict[str, object]:
    files = files if files is not None else {"core/a.py": _summary(10, 2, 4, 1)}
    return {
        "meta": {"format": 3, "version": "7.16.2", "branch_coverage": branch},
        "files": {name: {"summary": summary} for name, summary in files.items()},
        "totals": {"percent_covered": total},
    }


def _baseline(total: float = 70.0, **overrides: object) -> dict[str, object]:
    return {"total_percent": total, "updated": "2026-10-07", "reason": "baseline initiale du lot 3", **overrides}


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------


def test_a_total_below_the_baseline_is_a_regression() -> None:
    comparison = ratchet.compare(ratchet.parse_report(_report(69.95)), ratchet.parse_baseline(_baseline(70.0)))
    assert comparison.regressed
    assert comparison.delta == pytest.approx(-0.05)
    assert comparison.suggested_baseline is None


def test_equal_or_slightly_higher_totals_pass_without_suggesting_a_new_baseline() -> None:
    baseline = ratchet.parse_baseline(_baseline(70.0))
    for measured in (70.0, 70.09):
        comparison = ratchet.compare(ratchet.parse_report(_report(measured)), baseline)
        assert not comparison.regressed
        assert comparison.suggested_baseline is None, "moins d'un dixième de progrès : rien à relever"


def test_progress_of_a_tenth_or_more_suggests_raising_the_baseline_rounded_down() -> None:
    comparison = ratchet.compare(ratchet.parse_report(_report(72.38)), ratchet.parse_baseline(_baseline(70.0)))
    assert not comparison.regressed
    assert comparison.suggested_baseline == 72.3


@pytest.mark.parametrize(("value", "expected"), [(71.39, 71.3), (71.3, 71.3), (71.0, 71.0), (99.99, 99.9), (0.04, 0.0)])
def test_floor_tenth_rounds_down_and_tolerates_float_representation(value: float, expected: float) -> None:
    assert ratchet.floor_tenth(value) == expected


# ---------------------------------------------------------------------------
# Validation des entrées
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([70.0], "objet JSON attendu"),
        ({"total_percent": 70.0, "updated": "2026-10-07"}, "manquantes : ['reason']"),
        (_baseline(extra=1), "inconnues : ['extra']"),
        (_baseline("70"), "nombre attendu"),
        (_baseline(True), "nombre attendu"),
        (_baseline(170.0), "entre 0 et 100"),
        (_baseline(70.25), "un dixième au plus"),
        (_baseline(updated="07/10/2026"), "date AAAA-MM-JJ"),
        (_baseline(updated=20261007), "date AAAA-MM-JJ"),
        (_baseline(reason="   "), "raison de la valeur est obligatoire"),
        (_baseline(reason=None), "raison de la valeur est obligatoire"),
    ],
)
def test_a_malformed_baseline_is_rejected_with_a_clear_message(data: object, message: str) -> None:
    with pytest.raises(ratchet.RatchetInputError, match="baseline") as error:
        ratchet.parse_baseline(data)
    assert message in str(error.value)


def test_a_report_without_branch_coverage_is_rejected() -> None:
    with pytest.raises(ratchet.RatchetInputError, match="--cov-branch"):
        ratchet.parse_report(_report(90.0, branch=False))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"totals": {}}, "format coverage.json attendu"),
        (_report(float("nan")), "percent_covered"),
        ({**_report(70.0), "files": {"core/a.py": {}}}, "pas de « summary » pour core/a.py"),
        (_report(70.0, {"core/a.py": {**_summary(3, 1), "missing_lines": -1}}), "missing_lines"),
    ],
)
def test_a_malformed_report_is_rejected(data: object, message: str) -> None:
    with pytest.raises(ratchet.RatchetInputError, match="rapport") as error:
        ratchet.parse_report(data)
    assert message in str(error.value)


def test_unreadable_files_raise_input_errors(tmp_path: Path) -> None:
    with pytest.raises(ratchet.RatchetInputError, match="introuvable"):
        ratchet.load_report(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ratchet.RatchetInputError, match="illisible"):
        ratchet.load_baseline(broken)


def test_the_repository_baseline_is_valid() -> None:
    baseline = ratchet.load_baseline(ratchet.BASELINE_PATH)
    assert 0 < baseline.total_percent <= 100


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------


def test_top_missing_orders_by_missing_lines_then_branches_then_name() -> None:
    report = ratchet.parse_report(_report(50.0, {
        "ui/b.py": _summary(100, 40, 10, 2),
        "ui/a.py": _summary(100, 40, 10, 2),
        "core/c.py": _summary(100, 40, 10, 5),
        "core/d.py": _summary(100, 90),
        "core/e.py": _summary(100, 0),
    }))
    assert [f.name for f in ratchet.top_missing(report.files, limit=4)] == ["core/d.py", "core/c.py", "ui/a.py", "ui/b.py"]


def test_windows_paths_are_reported_with_forward_slashes() -> None:
    report = ratchet.parse_report(_report(50.0, {"core\\win.py": _summary(4, 1)}))
    assert report.files[0].name == "core/win.py"


def test_a_regression_report_shows_the_gap_the_ten_worst_files_and_the_update_procedure() -> None:
    files = {f"core/m{i:02d}.py": _summary(100, i, 20, i % 3) for i in range(1, 13)}
    report = ratchet.parse_report(_report(64.5, files))
    baseline = ratchet.parse_baseline(_baseline(70.0))
    text = ratchet.format_report(ratchet.compare(report, baseline), report, baseline)

    assert "64.50 %" in text and "70.0 %" in text
    assert "RÉGRESSION : -5.50 point(s)" in text
    assert "baseline initiale du lot 3" in text
    header = next(line for line in text.splitlines() if line.startswith("Fichier"))
    assert header.split() == ["Fichier", "Instr.", "Manq.", "Branches", "manq.", "Couv."]
    listed = [line.split()[0] for line in text.splitlines() if line.startswith("core/")]
    assert listed == [f"core/m{i:02d}.py" for i in range(12, 2, -1)], "les 10 fichiers les plus manquants, dans l'ordre"
    worst = next(line for line in text.splitlines() if line.startswith("core/m12.py")).split()
    assert worst == ["core/m12.py", "100", "12", "0/20", "90.0", "%"]
    assert "--update-baseline --reason" in text and "docs/coverage.md" in text


def test_a_progress_report_invites_to_raise_the_baseline() -> None:
    report = ratchet.parse_report(_report(73.46))
    baseline = ratchet.parse_baseline(_baseline(70.0))
    text = ratchet.format_report(ratchet.compare(report, baseline), report, baseline)
    assert "Relevez la baseline à 73.4 %" in text
    assert "RÉGRESSION" not in text and "Fichier" not in text


def test_a_stable_report_says_ok() -> None:
    report = ratchet.parse_report(_report(70.04))
    baseline = ratchet.parse_baseline(_baseline(70.0))
    text = ratchet.format_report(ratchet.compare(report, baseline), report, baseline)
    assert "OK : pas de régression (+0.04 point(s))." in text


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = _write(tmp_path / "baseline.json", _baseline(70.0))
    regressed = _write(tmp_path / "low.json", _report(65.0))
    improved = _write(tmp_path / "high.json", _report(75.0))
    no_branch = _write(tmp_path / "lines.json", _report(90.0, branch=False))

    assert ratchet.main([str(regressed), "--baseline", str(baseline)]) == ratchet.EXIT_REGRESSION
    assert "RÉGRESSION" in capsys.readouterr().out
    assert ratchet.main([str(improved), "--baseline", str(baseline)]) == ratchet.EXIT_OK
    assert "Relevez la baseline à 75.0 %" in capsys.readouterr().out
    assert ratchet.main([str(no_branch), "--baseline", str(baseline)]) == ratchet.EXIT_INVALID
    assert "--cov-branch" in capsys.readouterr().err


def test_update_baseline_requires_a_reason_and_writes_the_floored_total(tmp_path: Path) -> None:
    baseline = _write(tmp_path / "baseline.json", _baseline(70.0))
    report = _write(tmp_path / "coverage.json", _report(68.47))

    assert ratchet.main([str(report), "--baseline", str(baseline), "--update-baseline"]) == ratchet.EXIT_INVALID
    assert json.loads(baseline.read_text(encoding="utf-8"))["total_percent"] == 70.0, "sans raison, rien n'est écrit"

    reason = "tests de l'ancien exporteur retirés avec lui"
    assert ratchet.main([str(report), "--baseline", str(baseline), "--update-baseline", "--reason", reason]) == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert written == {"total_percent": 68.4, "updated": dt.date.today().isoformat(), "reason": reason}
    assert ratchet.main([str(report), "--baseline", str(baseline)]) == ratchet.EXIT_OK


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_coverage_is_opt_in_and_scoped_to_core_and_ui() -> None:
    """La suite reste rapide sans couverture : ``--cov`` ne passe jamais dans les options par défaut de pytest."""
    config = tomllib.loads((ratchet.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", "")
    assert "--cov" not in str(addopts)
    for name in ("pytest.ini", "setup.cfg", "tox.ini"):
        path = ratchet.ROOT / name
        assert not path.is_file() or "--cov" not in path.read_text(encoding="utf-8"), name
    coverage = config["tool"]["coverage"]
    assert coverage["run"]["source"] == ["core", "ui"]
    assert coverage["run"]["branch"] is True
