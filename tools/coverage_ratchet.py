"""Cliquet de couverture : le total mesuré (branches comprises) ne peut pas descendre sous la baseline.

Usage ::

    python tools/coverage_ratchet.py coverage.json            # échoue (code 1) si le total a régressé
    python tools/coverage_ratchet.py coverage.json --update-baseline --reason "pourquoi"

Le rapport ``coverage.json`` est celui de ``pytest --cov --cov-branch --cov-report=json`` (périmètre et options :
``[tool.coverage.*]`` de ``pyproject.toml``, procédure complète : ``docs/coverage.md``). La baseline
(``tests/coverage_baseline.json``) tient en trois champs ::

    {"total_percent": 71.3, "updated": "2026-10-07", "reason": "..."}

* total mesuré **sous la baseline moins la tolérance de bruit** (:data:`NOISE_TOLERANCE`) : code 1, avec l'écart,
  les fichiers qui laissent le plus de lignes non exécutées et la procédure de mise à jour ;
* total **au-dessus** d'au moins un dixième : code 0, en invitant à relever la baseline (le cliquet ne remonte que
  si on le remonte : c'est ce qui empêche de reperdre le terrain gagné) ;
* rapport ou baseline illisible : code 2, avec ce qui ne va pas. Un rapport sans couverture de branches est refusé :
  son total (lignes seules) n'est pas comparable à la baseline et passerait le cliquet à tort.

La baseline est toujours arrondie **au dixième inférieur** du total mesuré, jamais plus indulgente que la mesure.
Le bruit d'une exécution à l'autre est absorbé par une tolérance **fixe** : l'arrondi seul laissait une marge qui
dépendait de la mesure (de 0 à 0,1 point). Le script n'importe rien du dépôt : il se lance aussi
bien en ``python tools/coverage_ratchet.py`` qu'en ``python -m tools.coverage_ratchet``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "tests" / "coverage_baseline.json"
BASELINE_KEYS = ("total_percent", "updated", "reason")
TOP_FILES = 10
# Bruit d'une exécution à l'autre, sans changement de code : 85,24 puis 85,20 % sur deux runs Linux de la CI
# (2026-10-07), soit 33 lignes et branches sur 73 248, toutes dans du code exécuté par des threads d'arrière-plan
# (aperçus de médias, superviseur, scopes) qui finissent ou non avant la fin de leur test. Marge : deux fois ce bruit.
NOISE_TOLERANCE = 0.1

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INVALID = 2


class RatchetInputError(ValueError):
    """Rapport ou baseline inutilisable ; le message dit quoi corriger."""


@dataclass(frozen=True)
class Baseline:
    total_percent: float
    updated: str
    reason: str


@dataclass(frozen=True)
class FileCoverage:
    name: str
    statements: int
    missing: int
    branches: int
    missing_branches: int
    percent: float


@dataclass(frozen=True)
class CoverageReport:
    total_percent: float
    files: tuple[FileCoverage, ...]


@dataclass(frozen=True)
class Comparison:
    measured: float
    baseline: float
    tolerance: float = NOISE_TOLERANCE

    @property
    def threshold(self) -> float:
        """Total en dessous duquel il y a régression (arrondi : 85.2 - 0.1 vaut 85.1, pas 85.10000000000001)."""
        return round(self.baseline - self.tolerance, 6)

    @property
    def regressed(self) -> bool:
        return self.measured < self.threshold

    @property
    def delta(self) -> float:
        return self.measured - self.baseline

    @property
    def suggested_baseline(self) -> float | None:
        """Nouvelle baseline à proposer quand le total a progressé d'au moins un dixième, sinon ``None``."""
        floored = floor_tenth(self.measured)
        return floored if floored > self.baseline else None


def floor_tenth(value: float) -> float:
    """Arrondi au dixième inférieur (71.39 -> 71.3) ; tolère l'erreur de représentation (71.3 reste 71.3)."""
    return math.floor(round(value * 10, 6)) / 10


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RatchetInputError(f"{where} : nombre attendu, trouvé {value!r}")
    return float(value)


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RatchetInputError(f"{where} : entier positif attendu, trouvé {value!r}")
    return value


def parse_baseline(data: object) -> Baseline:
    """Valide le contenu de ``tests/coverage_baseline.json`` (déjà décodé)."""
    if not isinstance(data, dict):
        raise RatchetInputError(f"baseline : objet JSON attendu avec les clés {list(BASELINE_KEYS)}")
    missing = [key for key in BASELINE_KEYS if key not in data]
    unknown = sorted(set(data) - set(BASELINE_KEYS))
    if missing or unknown:
        raise RatchetInputError(
            f"baseline : clés attendues exactement {list(BASELINE_KEYS)} (manquantes : {missing}, inconnues : {unknown})"
        )
    total = _number(data["total_percent"], "baseline.total_percent")
    if not 0 <= total <= 100:
        raise RatchetInputError(f"baseline.total_percent : pourcentage entre 0 et 100 attendu, trouvé {total}")
    if floor_tenth(total) != total:
        raise RatchetInputError(
            f"baseline.total_percent : un dixième au plus ({total} -> {floor_tenth(total)}, arrondi inférieur)"
        )
    updated = data["updated"]
    try:
        if not isinstance(updated, str):
            raise ValueError
        dt.date.fromisoformat(updated)
    except ValueError:
        raise RatchetInputError(f"baseline.updated : date AAAA-MM-JJ attendue, trouvé {updated!r}") from None
    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise RatchetInputError("baseline.reason : la raison de la valeur est obligatoire (texte non vide)")
    return Baseline(total, updated, reason.strip())


def parse_report(data: object) -> CoverageReport:
    """Extrait d'un ``coverage.json`` le total (branches comprises) et le détail par fichier."""
    if not isinstance(data, dict) or not isinstance(data.get("totals"), dict) or not isinstance(data.get("files"), dict):
        raise RatchetInputError("rapport : format coverage.json attendu (clés « totals » et « files »)")
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise RatchetInputError(
            "rapport : mesuré sans la couverture de branches. Relancez avec --cov-branch : le total sur les "
            "lignes seules n'est pas comparable à la baseline."
        )
    total = _number(data["totals"].get("percent_covered"), "rapport.totals.percent_covered")
    files = []
    for name, entry in data["files"].items():
        summary = entry.get("summary") if isinstance(entry, dict) else None
        if not isinstance(summary, dict):
            raise RatchetInputError(f"rapport : pas de « summary » pour {name}")
        where = f"rapport.files[{name}].summary"
        files.append(FileCoverage(
            name=str(name).replace("\\", "/"),
            statements=_count(summary.get("num_statements"), f"{where}.num_statements"),
            missing=_count(summary.get("missing_lines"), f"{where}.missing_lines"),
            branches=_count(summary.get("num_branches"), f"{where}.num_branches"),
            missing_branches=_count(summary.get("missing_branches"), f"{where}.missing_branches"),
            percent=_number(summary.get("percent_covered"), f"{where}.percent_covered"),
        ))
    return CoverageReport(total, tuple(files))


def _read_json(path: Path, what: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RatchetInputError(f"{what} introuvable : {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RatchetInputError(f"{what} illisible ({path}) : {error}") from None


def load_baseline(path: Path) -> Baseline:
    return parse_baseline(_read_json(path, "baseline"))


def load_report(path: Path) -> CoverageReport:
    return parse_report(_read_json(path, "rapport de couverture"))


# ---------------------------------------------------------------------------
# Comparaison et rapport
# ---------------------------------------------------------------------------


def compare(report: CoverageReport, baseline: Baseline) -> Comparison:
    return Comparison(measured=report.total_percent, baseline=baseline.total_percent)


def top_missing(files: tuple[FileCoverage, ...], limit: int = TOP_FILES) -> list[FileCoverage]:
    """Les fichiers qui laissent le plus de lignes non exécutées (puis de branches), nom en dernier recours."""
    return sorted(files, key=lambda f: (-f.missing, -f.missing_branches, f.name))[:limit]


def format_table(files: list[FileCoverage]) -> str:
    headers = ("Fichier", "Instr.", "Manq.", "Branches manq.", "Couv.")
    rows = [
        (f.name, str(f.statements), str(f.missing), f"{f.missing_branches}/{f.branches}", f"{f.percent:.1f} %")
        for f in files
    ]
    widths = [max(len(row[i]) for row in (headers, *rows)) for i in range(len(headers))]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[0]) if i == 0 else cell.rjust(widths[i]) for i, cell in enumerate(cells))

    return "\n".join([line(headers), "  ".join("-" * width for width in widths), *(line(row) for row in rows)])


UPDATE_PROCEDURE = """\
Mettre à jour la baseline (docs/coverage.md) :
  - jamais pour « faire passer » une baisse : ajoutez plutôt les tests du code ajouté ou rendu inatteignable ;
  - baisse légitime (code testé supprimé, code de plateforme déplacé, test retiré pour une bonne raison) :
      python tools/coverage_ratchet.py coverage.json --update-baseline --reason "pourquoi la couverture baisse"
    puis commitez tests/coverage_baseline.json avec la raison dans le message."""


def _points(delta: float) -> str:
    """Écart signé en points ; une décimale de plus quand deux afficheraient « -0.00 » pour un écart réel."""
    return f"{delta:+.3f}" if 0 < abs(delta) < 0.005 else f"{delta:+.2f}"


def format_report(comparison: Comparison, report: CoverageReport, baseline: Baseline) -> str:
    """Texte affiché par le cliquet (console de la CI et résumé de l'exécution)."""
    lines = [
        f"Couverture totale (lignes + branches, core/ + ui/) : {comparison.measured:.2f} %",
        f"Baseline : {baseline.total_percent:.1f} % (mise à jour le {baseline.updated} : {baseline.reason})",
        f"Seuil : {comparison.threshold:.1f} % (baseline - tolérance de bruit de {comparison.tolerance:.1f} point)",
        "",
    ]
    if comparison.regressed:
        lines += [
            f"RÉGRESSION : {_points(comparison.delta)} point(s) sous la baseline.",
            "",
            f"Les {TOP_FILES} fichiers avec le plus de lignes non couvertes :",
            format_table(top_missing(report.files)),
            "",
            UPDATE_PROCEDURE,
        ]
    elif comparison.suggested_baseline is not None:
        lines += [
            f"Progrès : {_points(comparison.delta)} point(s). Relevez la baseline à {comparison.suggested_baseline:.1f} % "
            "pour garder ce terrain :",
            '  python tools/coverage_ratchet.py coverage.json --update-baseline --reason "nouveaux tests de …"',
        ]
    else:
        noise = " (sous la baseline, dans la tolérance de bruit)" if comparison.delta < 0 else ""
        lines.append(f"OK : pas de régression ({_points(comparison.delta)} point(s){noise}).")
    return "\n".join(lines)


def baseline_payload(measured: float, reason: str, today: dt.date) -> dict[str, object]:
    """Contenu d'une nouvelle baseline, validé par :func:`parse_baseline` (la raison est obligatoire)."""
    payload: dict[str, object] = {"total_percent": floor_tenth(measured), "updated": today.isoformat(), "reason": reason}
    parse_baseline(payload)
    return payload


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python tools/coverage_ratchet.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("report", type=Path, nargs="?", default=Path("coverage.json"),
                        help="rapport de pytest --cov-report=json (défaut : coverage.json)")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="fichier de baseline")
    parser.add_argument("--update-baseline", action="store_true",
                        help="réécrit la baseline au total mesuré (arrondi au dixième inférieur) ; exige --reason")
    parser.add_argument("--reason", default="", help="avec --update-baseline : pourquoi cette valeur")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = load_report(args.report)
        if args.update_baseline:
            payload = baseline_payload(report.total_percent, args.reason, dt.date.today())
            args.baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Baseline mise à jour : {payload['total_percent']} % ({args.baseline}).")
            return EXIT_OK
        baseline = load_baseline(args.baseline)
    except RatchetInputError as error:
        print(f"Cliquet de couverture : {error}", file=sys.stderr)
        return EXIT_INVALID
    comparison = compare(report, baseline)
    print(format_report(comparison, report, baseline))
    return EXIT_REGRESSION if comparison.regressed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
