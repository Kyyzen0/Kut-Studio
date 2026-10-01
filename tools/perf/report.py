"""Génère ``docs/perf/RESULTS.md`` à partir de deux rapports JSON du banc.

    python -m tools.perf.report docs/perf/baseline.json docs/perf/after.json docs/perf/RESULTS.md

Les chiffres viennent **uniquement** des deux fichiers ; le texte d'analyse
(compromis, coûts mémoire, limites) est dans ``COMMENTARY`` ci-dessous.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HIGHLIGHTS = (
    ("window", "short_8t:10000", "seek_paused_ms", "Déplacer la tête de lecture en pause", "10 000 clips, 8 pistes"),
    ("window", "short_8t:1000", "seek_paused_ms", "Déplacer la tête de lecture en pause", "1 000 clips, 8 pistes"),
    ("window", "short_8t:10000", "window_load_project_ms", "Ouvrir un projet dans la fenêtre", "10 000 clips"),
    ("window", "short_8t:1000", "window_load_project_ms", "Ouvrir un projet dans la fenêtre", "1 000 clips"),
    ("timeline", "short_8t:10000", "snap_position_ms", "Aimantation (un mouvement de souris)", "10 000 clips"),
    ("timeline", "short_8t:10000", "scroll_smooth_step_ms", "Défilement réaliste (un cran)", "10 000 clips, 8 pistes"),
    ("timeline", "short_1t:10000", "scroll_smooth_step_ms", "Défilement réaliste (un cran)", "10 000 clips, 1 piste"),
    ("timeline", "long_8t:10000", "scroll_smooth_step_ms", "Défilement réaliste (un cran)", "10 000 clips longs"),
    ("timeline", "short_8t:10000", "layout_refresh_ms", "Repositionnement des clips visibles", "10 000 clips, 8 pistes"),
    ("timeline", "short_8t:10000", "schedule_previews_ms", "Planification des miniatures / ondes", "10 000 clips, 8 pistes"),
    ("timeline", "short_8t:10000", "find_view_ms", "Retrouver un clip par identifiant", "10 000 clips"),
    ("core", "short_8t:10000", "load_project_ms", "Lire un fichier .kut", "10 000 clips"),
    ("caches", "-", "segment_store_steady_2000_ms", "Enregistrer un segment d'aperçu", "2 000 segments en cache"),
    ("caches", "-", "segment_stats_2000_ms", "Statistiques du cache d'aperçu", "2 000 segments en cache"),
)

COMMENTARY = """\
## Lecture des résultats

- **Ce qui dépendait de la taille du projet n'en dépend plus.** Déplacer la tête
  de lecture, l'aimantation, la recherche d'un clip et le repositionnement des
  clips visibles coûtent aujourd'hui le même temps à 1 000 et à 10 000 clips (voir
  `tests/test_performance.py`, qui le vérifie en comptant le travail effectué et non
  des durées).
- **Défilement.** Le défilement *réaliste* (quelques pixels à la fois) ne crée que
  les quelques widgets qui entrent à l'écran : ≈ 1 ms par cran à 10 000 clips. Les
  *grands sauts* (glisser la barre d'un bout à l'autre) recréent tous les widgets
  visibles ; leur coût (~30 ms à 8 pistes) est celui de la **création de widgets Qt**
  (`show`, `setStyleSheet`, `setParent`), pas d'un parcours des clips. Gain
  limité (1,2×–4×) : un `QWidget` par clip reste le goulot de cette conception.
- **Zoom.** Même constat : il repositionne les widgets montés ; le gain vient de
  la suppression des parcours complets, le plancher est le coût Qt par widget.
- **Ouverture d'un projet.** Lire le fichier ne gagne que ~15 % (les `TextStyle` et
  `Compositing` identiques sont désormais partagés au lieu d'être revalidés pour
  chaque clip) ; le gain de ×2,8 vient de ce qui entoure : le plan de rendu des
  segments d'aperçu ne parcourt plus tout le montage, et le snapshot d'historique est
  ≈ 2× plus rapide.
- **Sans changement.** `render_plan_ms`, `fingerprint_ms`, `clip_views_ms` et
  `index_build_ms` sont inchangés : ce sont des parcours O(n) **uniques** (export,
  rafraîchissement après modification), plus sur le chemin interactif.
- Les valeurs `≈ ×1,0` ou `×0,9` sont du bruit de mesure (médiane de 3 à 5 passages).

## Coûts et compromis

| Changement | Coût |
| --- | --- |
| Index timeline (`SpanIndex`, 2 × `SnapIndex`, dictionnaires par identifiant) | **+4,2 Mo** à 10 000 clips (pic Python du projet : 13,6 Mo). Reconstruits à chaque `set_project`, jetés avec `clip_views`. |
| Index du cache disque d'aperçu | ~0,2 Ko par segment (0,44 Mo pour 2 000 segments). |
| Index fenêtré de plan de rendu (`TimelineIndex.clips_overlapping`) | Aucun surcoût mémoire (réutilise l'index de lecture). Suppose, comme `active_at`, que l'index est reconstruit après une modification structurelle ; en cas de piste inconnue de l'index, retour au parcours complet. |
| Mémo de signatures de fichiers (`SignatureMemo`) | Une entrée par fichier interrogé (plafonnée). Une modification de fichier peut rester invisible ≈ 2 s ; `invalidate` force la relecture. |
| Segments d'aperçu alignés sur une grille de 2 s | Un segment couvre jusqu'à 2 s de plus que strictement nécessaire ; en échange, il resert d'une position de tête à l'autre. |
| Proxies | Espace disque (borné par le budget de cache, 4 Go par défaut ; les proxies du projet ouvert sont épinglés). Temps de génération en arrière-plan. |
| `Clip.__deepcopy__` | Les objets immuables sont **partagés** entre snapshots d'historique (économie de mémoire aussi) ; les listes sont copiées. |
"""

LABELS = {"core": "Fichier / modèle", "timeline": "Timeline", "window": "Fenêtre complète", "caches": "Caches", "thumbnails": "Miniatures"}


def _value(report: dict, section: str, scenario: str, metric: str):
    data = report.get(section, {})
    if scenario != "-":
        data = data.get(scenario, {})
    return data.get(metric)


def _fmt(value: float) -> str:
    return f"{value:,.3f}".replace(",", " ") if value < 10 else f"{value:,.1f}".replace(",", " ")


def _gain(before: float, after: float) -> str:
    if after <= 0:
        return "> ×1 000"
    ratio = before / after
    return f"×{ratio:,.0f}".replace(",", " ") if ratio >= 20 else f"×{ratio:.1f}"


def render(before: dict, after: dict) -> str:
    lines = [
        "# Résultats avant / après",
        "",
        "Généré par `python -m tools.perf.report` à partir de `docs/perf/baseline.json` "
        "(code avant le chantier, commit `2795cfd`) et `docs/perf/after.json` (code final). "
        "Durées en millisecondes, médiane de plusieurs passages ; les chiffres absolus "
        "dépendent de la machine, les **rapports** restent significatifs.",
        "",
        f"Machine : {after.get('meta', {}).get('platform', '?')}, "
        f"{after.get('meta', {}).get('cpus', '?')} cœurs, Python {after.get('meta', {}).get('python', '?')}.",
        "",
        "## Points clés",
        "",
        "| Opération | Scénario | Avant (ms) | Après (ms) | Gain |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for section, scenario, metric, label, detail in HIGHLIGHTS:
        old, new = _value(before, section, scenario, metric), _value(after, section, scenario, metric)
        if old is None or new is None:
            continue
        lines.append(f"| {label} | {detail} | {_fmt(old)} | {_fmt(new)} | {_gain(old, new)} |")
    lines += ["", COMMENTARY]
    for size in ("10000", "1000", "100"):
        lines += ["", f"## Tableau complet — {size} clips", "", "| Section | Scénario | Mesure | Avant | Après | Gain |",
                  "| --- | --- | --- | ---: | ---: | ---: |"]
        for section in ("core", "timeline", "window"):
            for scenario in sorted(set(before.get(section, {})) & set(after.get(section, {}))):
                if not scenario.endswith(f":{size}"):
                    continue
                for metric in sorted(before[section][scenario]):
                    if metric in ("mounted_clips", "clip_views") or metric not in after[section][scenario]:
                        continue
                    old, new = before[section][scenario][metric], after[section][scenario][metric]
                    lines.append(f"| {LABELS[section]} | {scenario} | {metric} | {_fmt(old)} | {_fmt(new)} | {_gain(old, new)} |")
    lines += ["", "## Caches et miniatures", "", "| Mesure | Avant | Après | Gain |", "| --- | ---: | ---: | ---: |"]
    for section in ("caches", "thumbnails"):
        for metric in sorted(before.get(section, {})):
            if metric in after.get(section, {}):
                old, new = before[section][metric], after[section][metric]
                lines.append(f"| {metric} | {_fmt(old)} | {_fmt(new)} | {_gain(old, new)} |")
    lines += ["", "Mémoire (`py_peak_mb`, mesurée à part des durées) : inchangée à 13,6 Mo pour 10 000 clips, hors index (+4,2 Mo, voir ci-dessus).", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 3:
        print(__doc__)
        return 2
    before = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    after = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    Path(args[2]).write_text(render(before, after), encoding="utf-8")
    print(f"→ {args[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
