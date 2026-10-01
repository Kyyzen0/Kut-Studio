# Résultats avant / après

Généré par `python -m tools.perf.report` à partir de `docs/perf/baseline.json` (code avant le chantier, commit `2795cfd`) et `docs/perf/after.json` (code final). Durées en millisecondes, médiane de plusieurs passages ; les chiffres absolus dépendent de la machine, les **rapports** restent significatifs.

Machine : macOS-27.2-arm64-arm-64bit-Mach-O, 10 cœurs, Python 3.14.7.

## Points clés

| Opération | Scénario | Avant (ms) | Après (ms) | Gain |
| --- | --- | ---: | ---: | ---: |
| Déplacer la tête de lecture en pause | 10 000 clips, 8 pistes | 268.7 | 0.672 | ×400 |
| Déplacer la tête de lecture en pause | 1 000 clips, 8 pistes | 23.7 | 0.611 | ×39 |
| Ouvrir un projet dans la fenêtre | 10 000 clips | 1 214.0 | 436.5 | ×2.8 |
| Ouvrir un projet dans la fenêtre | 1 000 clips | 108.0 | 66.4 | ×1.6 |
| Aimantation (un mouvement de souris) | 10 000 clips | 14.4 | 0.001 | ×10 530 |
| Défilement réaliste (un cran) | 10 000 clips, 8 pistes | 10.6 | 0.896 | ×11.9 |
| Défilement réaliste (un cran) | 10 000 clips, 1 piste | 2.322 | 0.110 | ×21 |
| Défilement réaliste (un cran) | 10 000 clips longs | 1.443 | 0.013 | ×112 |
| Repositionnement des clips visibles | 10 000 clips, 8 pistes | 3.173 | 0.488 | ×6.5 |
| Planification des miniatures / ondes | 10 000 clips, 8 pistes | 2.466 | 0.301 | ×8.2 |
| Retrouver un clip par identifiant | 10 000 clips | 0.036 | 0.000 | ×172 |
| Lire un fichier .kut | 10 000 clips | 270.3 | 235.4 | ×1.1 |
| Enregistrer un segment d'aperçu | 2 000 segments en cache | 6.028 | 0.198 | ×30 |
| Statistiques du cache d'aperçu | 2 000 segments en cache | 5.188 | 0.002 | ×2 542 |

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


## Tableau complet — 10000 clips

| Section | Scénario | Mesure | Avant | Après | Gain |
| --- | --- | --- | ---: | ---: | ---: |
| Fichier / modèle | long_8t:10000 | active_at_ms | 0.086 | 0.088 | ×1.0 |
| Fichier / modèle | long_8t:10000 | clip_views_ms | 35.5 | 36.2 | ×1.0 |
| Fichier / modèle | long_8t:10000 | evaluate_timeline_ms | 1.013 | 1.041 | ×1.0 |
| Fichier / modèle | long_8t:10000 | fingerprint_ms | 41.8 | 41.1 | ×1.0 |
| Fichier / modèle | long_8t:10000 | index_build_ms | 28.8 | 29.3 | ×1.0 |
| Fichier / modèle | long_8t:10000 | load_project_ms | 282.4 | 236.8 | ×1.2 |
| Fichier / modèle | long_8t:10000 | py_peak_mb | 13.5 | 13.6 | ×1.0 |
| Fichier / modèle | long_8t:10000 | render_plan_ms | 74.8 | 86.3 | ×0.9 |
| Fichier / modèle | long_8t:10000 | save_kut_ms | 446.2 | 382.4 | ×1.2 |
| Fichier / modèle | long_8t:10000 | snap_ms | 14.2 | 14.4 | ×1.0 |
| Fichier / modèle | short_1t:10000 | active_at_ms | 0.006 | 0.006 | ×1.0 |
| Fichier / modèle | short_1t:10000 | clip_views_ms | 36.7 | 35.4 | ×1.0 |
| Fichier / modèle | short_1t:10000 | evaluate_timeline_ms | 0.973 | 0.964 | ×1.0 |
| Fichier / modèle | short_1t:10000 | fingerprint_ms | 56.6 | 55.3 | ×1.0 |
| Fichier / modèle | short_1t:10000 | index_build_ms | 29.7 | 29.3 | ×1.0 |
| Fichier / modèle | short_1t:10000 | load_project_ms | 274.6 | 229.3 | ×1.2 |
| Fichier / modèle | short_1t:10000 | py_peak_mb | 13.6 | 13.6 | ×1.0 |
| Fichier / modèle | short_1t:10000 | render_plan_ms | 86.5 | 97.7 | ×0.9 |
| Fichier / modèle | short_1t:10000 | save_kut_ms | 388.6 | 398.6 | ×1.0 |
| Fichier / modèle | short_1t:10000 | snap_ms | 14.3 | 14.3 | ×1.0 |
| Fichier / modèle | short_8t:10000 | active_at_ms | 0.037 | 0.037 | ×1.0 |
| Fichier / modèle | short_8t:10000 | clip_views_ms | 36.6 | 35.6 | ×1.0 |
| Fichier / modèle | short_8t:10000 | evaluate_timeline_ms | 0.993 | 0.990 | ×1.0 |
| Fichier / modèle | short_8t:10000 | fingerprint_ms | 41.5 | 40.7 | ×1.0 |
| Fichier / modèle | short_8t:10000 | index_build_ms | 29.7 | 29.0 | ×1.0 |
| Fichier / modèle | short_8t:10000 | load_project_ms | 270.3 | 235.4 | ×1.1 |
| Fichier / modèle | short_8t:10000 | py_peak_mb | 13.5 | 13.6 | ×1.0 |
| Fichier / modèle | short_8t:10000 | render_plan_ms | 76.6 | 84.5 | ×0.9 |
| Fichier / modèle | short_8t:10000 | save_kut_ms | 390.0 | 382.6 | ×1.0 |
| Fichier / modèle | short_8t:10000 | snap_ms | 14.3 | 14.2 | ×1.0 |
| Timeline | long_8t:10000 | find_view_ms | 0.036 | 0.000 | ×192 |
| Timeline | long_8t:10000 | layout_refresh_ms | 1.565 | 0.159 | ×9.9 |
| Timeline | long_8t:10000 | panel_set_project_ms | 44.8 | 44.1 | ×1.0 |
| Timeline | long_8t:10000 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | long_8t:10000 | refresh_clip_widgets_ms | 17.6 | 9.598 | ×1.8 |
| Timeline | long_8t:10000 | schedule_previews_ms | 0.949 | 0.104 | ×9.1 |
| Timeline | long_8t:10000 | scroll_smooth_step_ms | 1.443 | 0.013 | ×112 |
| Timeline | long_8t:10000 | scroll_step_ms | 9.941 | 7.950 | ×1.3 |
| Timeline | long_8t:10000 | select_all_marquee_ms | 16.7 | 12.6 | ×1.3 |
| Timeline | long_8t:10000 | select_one_ms | 0.001 | 0.001 | ×1.1 |
| Timeline | long_8t:10000 | snap_position_ms | 14.3 | 0.001 | ×11 818 |
| Timeline | long_8t:10000 | zoom_step_ms | 19.4 | 13.5 | ×1.4 |
| Timeline | short_1t:10000 | find_view_ms | 0.043 | 0.000 | ×229 |
| Timeline | short_1t:10000 | layout_refresh_ms | 1.629 | 0.074 | ×22 |
| Timeline | short_1t:10000 | panel_set_project_ms | 39.3 | 42.9 | ×0.9 |
| Timeline | short_1t:10000 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | short_1t:10000 | refresh_clip_widgets_ms | 5.235 | 1.967 | ×2.7 |
| Timeline | short_1t:10000 | schedule_previews_ms | 1.023 | 0.048 | ×22 |
| Timeline | short_1t:10000 | scroll_smooth_step_ms | 2.322 | 0.110 | ×21 |
| Timeline | short_1t:10000 | scroll_step_ms | 6.475 | 3.508 | ×1.8 |
| Timeline | short_1t:10000 | select_all_marquee_ms | 10.1 | 10.7 | ×0.9 |
| Timeline | short_1t:10000 | select_one_ms | 0.001 | 0.001 | ×0.9 |
| Timeline | short_1t:10000 | snap_position_ms | 14.2 | 0.001 | ×10 710 |
| Timeline | short_1t:10000 | zoom_step_ms | 8.984 | 3.457 | ×2.6 |
| Timeline | short_8t:10000 | find_view_ms | 0.036 | 0.000 | ×172 |
| Timeline | short_8t:10000 | layout_refresh_ms | 3.173 | 0.488 | ×6.5 |
| Timeline | short_8t:10000 | panel_set_project_ms | 54.2 | 54.3 | ×1.0 |
| Timeline | short_8t:10000 | playhead_step_ms | 0.007 | 0.008 | ×1.0 |
| Timeline | short_8t:10000 | refresh_clip_widgets_ms | 59.0 | 54.7 | ×1.1 |
| Timeline | short_8t:10000 | schedule_previews_ms | 2.466 | 0.301 | ×8.2 |
| Timeline | short_8t:10000 | scroll_smooth_step_ms | 10.6 | 0.896 | ×11.9 |
| Timeline | short_8t:10000 | scroll_step_ms | 38.7 | 33.9 | ×1.1 |
| Timeline | short_8t:10000 | select_all_marquee_ms | 16.6 | 17.5 | ×1.0 |
| Timeline | short_8t:10000 | select_one_ms | 0.001 | 0.001 | ×1.0 |
| Timeline | short_8t:10000 | snap_position_ms | 14.4 | 0.001 | ×10 530 |
| Timeline | short_8t:10000 | zoom_step_ms | 37.1 | 30.6 | ×1.2 |
| Fenêtre complète | short_1t:10000 | seek_paused_ms | 364.6 | 0.277 | ×1 317 |
| Fenêtre complète | short_1t:10000 | window_load_project_ms | 1 242.1 | 408.7 | ×3.0 |
| Fenêtre complète | short_8t:10000 | seek_paused_ms | 268.7 | 0.672 | ×400 |
| Fenêtre complète | short_8t:10000 | window_load_project_ms | 1 214.0 | 436.5 | ×2.8 |

## Tableau complet — 1000 clips

| Section | Scénario | Mesure | Avant | Après | Gain |
| --- | --- | --- | ---: | ---: | ---: |
| Fichier / modèle | long_8t:1000 | active_at_ms | 0.084 | 0.084 | ×1.0 |
| Fichier / modèle | long_8t:1000 | clip_views_ms | 3.361 | 3.378 | ×1.0 |
| Fichier / modèle | long_8t:1000 | evaluate_timeline_ms | 0.168 | 0.167 | ×1.0 |
| Fichier / modèle | long_8t:1000 | fingerprint_ms | 3.826 | 3.895 | ×1.0 |
| Fichier / modèle | long_8t:1000 | index_build_ms | 2.879 | 2.840 | ×1.0 |
| Fichier / modèle | long_8t:1000 | load_project_ms | 25.3 | 21.7 | ×1.2 |
| Fichier / modèle | long_8t:1000 | py_peak_mb | 1.354 | 1.354 | ×1.0 |
| Fichier / modèle | long_8t:1000 | render_plan_ms | 7.055 | 8.248 | ×0.9 |
| Fichier / modèle | long_8t:1000 | save_kut_ms | 39.4 | 38.7 | ×1.0 |
| Fichier / modèle | long_8t:1000 | snap_ms | 1.410 | 1.405 | ×1.0 |
| Fichier / modèle | short_1t:1000 | active_at_ms | 0.005 | 0.005 | ×1.0 |
| Fichier / modèle | short_1t:1000 | clip_views_ms | 3.336 | 3.382 | ×1.0 |
| Fichier / modèle | short_1t:1000 | evaluate_timeline_ms | 0.095 | 0.098 | ×1.0 |
| Fichier / modèle | short_1t:1000 | fingerprint_ms | 5.073 | 5.092 | ×1.0 |
| Fichier / modèle | short_1t:1000 | index_build_ms | 2.733 | 2.795 | ×1.0 |
| Fichier / modèle | short_1t:1000 | load_project_ms | 24.3 | 21.0 | ×1.2 |
| Fichier / modèle | short_1t:1000 | py_peak_mb | 1.357 | 1.357 | ×1.0 |
| Fichier / modèle | short_1t:1000 | render_plan_ms | 8.366 | 9.285 | ×0.9 |
| Fichier / modèle | short_1t:1000 | save_kut_ms | 37.8 | 38.3 | ×1.0 |
| Fichier / modèle | short_1t:1000 | snap_ms | 1.412 | 1.390 | ×1.0 |
| Fichier / modèle | short_8t:1000 | active_at_ms | 0.036 | 0.036 | ×1.0 |
| Fichier / modèle | short_8t:1000 | clip_views_ms | 3.334 | 3.322 | ×1.0 |
| Fichier / modèle | short_8t:1000 | evaluate_timeline_ms | 0.130 | 0.133 | ×1.0 |
| Fichier / modèle | short_8t:1000 | fingerprint_ms | 3.833 | 3.822 | ×1.0 |
| Fichier / modèle | short_8t:1000 | index_build_ms | 2.781 | 2.833 | ×1.0 |
| Fichier / modèle | short_8t:1000 | load_project_ms | 26.0 | 21.3 | ×1.2 |
| Fichier / modèle | short_8t:1000 | py_peak_mb | 1.354 | 1.354 | ×1.0 |
| Fichier / modèle | short_8t:1000 | render_plan_ms | 7.100 | 8.404 | ×0.8 |
| Fichier / modèle | short_8t:1000 | save_kut_ms | 39.3 | 38.7 | ×1.0 |
| Fichier / modèle | short_8t:1000 | snap_ms | 1.405 | 1.423 | ×1.0 |
| Timeline | long_8t:1000 | find_view_ms | 0.004 | 0.000 | ×18.6 |
| Timeline | long_8t:1000 | layout_refresh_ms | 0.395 | 0.156 | ×2.5 |
| Timeline | long_8t:1000 | panel_set_project_ms | 7.210 | 7.146 | ×1.0 |
| Timeline | long_8t:1000 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | long_8t:1000 | refresh_clip_widgets_ms | 9.171 | 8.755 | ×1.0 |
| Timeline | long_8t:1000 | schedule_previews_ms | 0.273 | 0.103 | ×2.7 |
| Timeline | long_8t:1000 | scroll_smooth_step_ms | 0.154 | 0.012 | ×13.1 |
| Timeline | long_8t:1000 | scroll_step_ms | 7.085 | 7.647 | ×0.9 |
| Timeline | long_8t:1000 | select_all_marquee_ms | 1.717 | 1.808 | ×1.0 |
| Timeline | long_8t:1000 | select_one_ms | 0.001 | 0.001 | ×1.0 |
| Timeline | long_8t:1000 | snap_position_ms | 1.451 | 0.001 | ×1 195 |
| Timeline | long_8t:1000 | zoom_step_ms | 13.3 | 13.2 | ×1.0 |
| Timeline | short_1t:1000 | find_view_ms | 0.004 | 0.000 | ×20 |
| Timeline | short_1t:1000 | layout_refresh_ms | 0.255 | 0.061 | ×4.2 |
| Timeline | short_1t:1000 | panel_set_project_ms | 4.495 | 4.492 | ×1.0 |
| Timeline | short_1t:1000 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | short_1t:1000 | refresh_clip_widgets_ms | 2.139 | 1.737 | ×1.2 |
| Timeline | short_1t:1000 | schedule_previews_ms | 0.180 | 0.042 | ×4.2 |
| Timeline | short_1t:1000 | scroll_smooth_step_ms | 0.592 | 0.108 | ×5.5 |
| Timeline | short_1t:1000 | scroll_step_ms | 3.487 | 3.481 | ×1.0 |
| Timeline | short_1t:1000 | select_all_marquee_ms | 0.969 | 1.089 | ×0.9 |
| Timeline | short_1t:1000 | select_one_ms | 0.001 | 0.001 | ×0.9 |
| Timeline | short_1t:1000 | snap_position_ms | 1.419 | 0.001 | ×1 144 |
| Timeline | short_1t:1000 | zoom_step_ms | 3.557 | 3.268 | ×1.1 |
| Timeline | short_8t:1000 | find_view_ms | 0.004 | 0.000 | ×20 |
| Timeline | short_8t:1000 | layout_refresh_ms | 1.034 | 0.495 | ×2.1 |
| Timeline | short_8t:1000 | panel_set_project_ms | 13.6 | 13.9 | ×1.0 |
| Timeline | short_8t:1000 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | short_8t:1000 | refresh_clip_widgets_ms | 39.4 | 39.4 | ×1.0 |
| Timeline | short_8t:1000 | schedule_previews_ms | 0.787 | 0.296 | ×2.7 |
| Timeline | short_8t:1000 | scroll_smooth_step_ms | 5.183 | 0.836 | ×6.2 |
| Timeline | short_8t:1000 | scroll_step_ms | 26.0 | 6.781 | ×3.8 |
| Timeline | short_8t:1000 | select_all_marquee_ms | 1.742 | 1.750 | ×1.0 |
| Timeline | short_8t:1000 | select_one_ms | 0.001 | 0.001 | ×0.9 |
| Timeline | short_8t:1000 | snap_position_ms | 1.445 | 0.001 | ×1 089 |
| Timeline | short_8t:1000 | zoom_step_ms | 26.8 | 27.8 | ×1.0 |
| Fenêtre complète | short_1t:1000 | seek_paused_ms | 28.6 | 0.260 | ×110 |
| Fenêtre complète | short_1t:1000 | window_load_project_ms | 87.5 | 43.3 | ×2.0 |
| Fenêtre complète | short_8t:1000 | seek_paused_ms | 23.7 | 0.611 | ×39 |
| Fenêtre complète | short_8t:1000 | window_load_project_ms | 108.0 | 66.4 | ×1.6 |

## Tableau complet — 100 clips

| Section | Scénario | Mesure | Avant | Après | Gain |
| --- | --- | --- | ---: | ---: | ---: |
| Fichier / modèle | long_8t:100 | active_at_ms | 0.076 | 0.077 | ×1.0 |
| Fichier / modèle | long_8t:100 | clip_views_ms | 0.338 | 0.343 | ×1.0 |
| Fichier / modèle | long_8t:100 | evaluate_timeline_ms | 0.058 | 0.059 | ×1.0 |
| Fichier / modèle | long_8t:100 | fingerprint_ms | 0.405 | 0.434 | ×0.9 |
| Fichier / modèle | long_8t:100 | index_build_ms | 0.286 | 0.293 | ×1.0 |
| Fichier / modèle | long_8t:100 | load_project_ms | 2.599 | 2.258 | ×1.2 |
| Fichier / modèle | long_8t:100 | py_peak_mb | 0.146 | 0.146 | ×1.0 |
| Fichier / modèle | long_8t:100 | render_plan_ms | 0.724 | 0.840 | ×0.9 |
| Fichier / modèle | long_8t:100 | save_kut_ms | 4.192 | 4.098 | ×1.0 |
| Fichier / modèle | long_8t:100 | snap_ms | 0.142 | 0.145 | ×1.0 |
| Fichier / modèle | short_1t:100 | active_at_ms | 0.003 | 0.003 | ×1.0 |
| Fichier / modèle | short_1t:100 | clip_views_ms | 0.221 | 0.234 | ×0.9 |
| Fichier / modèle | short_1t:100 | evaluate_timeline_ms | 0.007 | 0.006 | ×1.1 |
| Fichier / modèle | short_1t:100 | fingerprint_ms | 0.506 | 0.522 | ×1.0 |
| Fichier / modèle | short_1t:100 | index_build_ms | 0.076 | 0.070 | ×1.1 |
| Fichier / modèle | short_1t:100 | load_project_ms | 2.013 | 1.665 | ×1.2 |
| Fichier / modèle | short_1t:100 | py_peak_mb | 0.157 | 0.157 | ×1.0 |
| Fichier / modèle | short_1t:100 | render_plan_ms | 0.477 | 0.492 | ×1.0 |
| Fichier / modèle | short_1t:100 | save_kut_ms | 3.991 | 3.771 | ×1.1 |
| Fichier / modèle | short_1t:100 | snap_ms | 0.142 | 0.141 | ×1.0 |
| Fichier / modèle | short_8t:100 | active_at_ms | 0.035 | 0.035 | ×1.0 |
| Fichier / modèle | short_8t:100 | clip_views_ms | 0.338 | 0.345 | ×1.0 |
| Fichier / modèle | short_8t:100 | evaluate_timeline_ms | 0.043 | 0.043 | ×1.0 |
| Fichier / modèle | short_8t:100 | fingerprint_ms | 0.401 | 0.423 | ×0.9 |
| Fichier / modèle | short_8t:100 | index_build_ms | 0.285 | 0.295 | ×1.0 |
| Fichier / modèle | short_8t:100 | load_project_ms | 2.570 | 2.229 | ×1.2 |
| Fichier / modèle | short_8t:100 | py_peak_mb | 0.146 | 0.146 | ×1.0 |
| Fichier / modèle | short_8t:100 | render_plan_ms | 0.712 | 0.854 | ×0.8 |
| Fichier / modèle | short_8t:100 | save_kut_ms | 4.254 | 4.217 | ×1.0 |
| Fichier / modèle | short_8t:100 | snap_ms | 0.141 | 0.144 | ×1.0 |
| Timeline | long_8t:100 | find_view_ms | 0.001 | 0.000 | ×3.0 |
| Timeline | long_8t:100 | layout_refresh_ms | 0.257 | 0.305 | ×0.8 |
| Timeline | long_8t:100 | panel_set_project_ms | 3.703 | 3.708 | ×1.0 |
| Timeline | long_8t:100 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | long_8t:100 | refresh_clip_widgets_ms | 8.345 | 8.155 | ×1.0 |
| Timeline | long_8t:100 | schedule_previews_ms | 0.205 | 0.177 | ×1.2 |
| Timeline | long_8t:100 | scroll_smooth_step_ms | 0.024 | 0.012 | ×2.0 |
| Timeline | long_8t:100 | scroll_step_ms | 2.561 | 0.843 | ×3.0 |
| Timeline | long_8t:100 | select_all_marquee_ms | 0.722 | 0.738 | ×1.0 |
| Timeline | long_8t:100 | select_one_ms | 0.001 | 0.001 | ×1.0 |
| Timeline | long_8t:100 | snap_position_ms | 0.144 | 0.001 | ×119 |
| Timeline | long_8t:100 | zoom_step_ms | 11.9 | 12.7 | ×0.9 |
| Timeline | short_1t:100 | find_view_ms | 0.001 | 0.000 | ×2.8 |
| Timeline | short_1t:100 | layout_refresh_ms | 0.119 | 0.230 | ×0.5 |
| Timeline | short_1t:100 | panel_set_project_ms | 1.152 | 1.129 | ×1.0 |
| Timeline | short_1t:100 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | short_1t:100 | refresh_clip_widgets_ms | 1.552 | 1.560 | ×1.0 |
| Timeline | short_1t:100 | schedule_previews_ms | 0.090 | 0.065 | ×1.4 |
| Timeline | short_1t:100 | scroll_smooth_step_ms | 0.389 | 0.116 | ×3.3 |
| Timeline | short_1t:100 | scroll_step_ms | 1.592 | 0.676 | ×2.4 |
| Timeline | short_1t:100 | select_all_marquee_ms | 0.114 | 0.115 | ×1.0 |
| Timeline | short_1t:100 | select_one_ms | 0.001 | 0.001 | ×0.9 |
| Timeline | short_1t:100 | snap_position_ms | 0.141 | 0.001 | ×121 |
| Timeline | short_1t:100 | zoom_step_ms | 3.106 | 3.382 | ×0.9 |
| Timeline | short_8t:100 | find_view_ms | 0.001 | 0.000 | ×3.5 |
| Timeline | short_8t:100 | layout_refresh_ms | 0.655 | 0.446 | ×1.5 |
| Timeline | short_8t:100 | panel_set_project_ms | 9.417 | 9.116 | ×1.0 |
| Timeline | short_8t:100 | playhead_step_ms | 0.007 | 0.007 | ×1.0 |
| Timeline | short_8t:100 | refresh_clip_widgets_ms | 18.4 | 18.3 | ×1.0 |
| Timeline | short_8t:100 | schedule_previews_ms | 0.510 | 0.265 | ×1.9 |
| Timeline | short_8t:100 | scroll_smooth_step_ms | 1.216 | 0.273 | ×4.5 |
| Timeline | short_8t:100 | scroll_step_ms | 2.841 | 0.648 | ×4.4 |
| Timeline | short_8t:100 | select_all_marquee_ms | 0.413 | 0.574 | ×0.7 |
| Timeline | short_8t:100 | select_one_ms | 0.231 | 0.240 | ×1.0 |
| Timeline | short_8t:100 | snap_position_ms | 0.144 | 0.001 | ×136 |
| Timeline | short_8t:100 | zoom_step_ms | 18.8 | 9.132 | ×2.1 |
| Fenêtre complète | short_1t:100 | seek_paused_ms | 2.474 | 0.227 | ×10.9 |
| Fenêtre complète | short_1t:100 | window_load_project_ms | 14.6 | 9.682 | ×1.5 |
| Fenêtre complète | short_8t:100 | seek_paused_ms | 2.480 | 0.594 | ×4.2 |
| Fenêtre complète | short_8t:100 | window_load_project_ms | 31.3 | 27.4 | ×1.1 |

## Caches et miniatures

| Mesure | Avant | Après | Gain |
| --- | ---: | ---: | ---: |
| memory_get_cold_us | 0.075 | 0.073 | ×1.0 |
| memory_get_hot_us | 0.080 | 0.076 | ×1.0 |
| memory_put_us | 0.229 | 0.234 | ×1.0 |
| segment_lookup_hot_2000_us | 19.7 | 20.6 | ×1.0 |
| segment_lookup_hot_200_us | 19.6 | 21.3 | ×0.9 |
| segment_stats_2000_ms | 5.188 | 0.002 | ×2 542 |
| segment_stats_200_ms | 0.582 | 0.002 | ×245 |
| segment_store_2000_ms | 7.289 | 5.906 | ×1.2 |
| segment_store_200_ms | 1.502 | 1.101 | ×1.4 |
| segment_store_steady_2000_ms | 6.028 | 0.198 | ×30 |
| segment_store_steady_200_ms | 0.868 | 0.207 | ×4.2 |
| thumbnail_cold_ms | 75.8 | 79.2 | ×1.0 |
| thumbnail_hot_us | 2.549 | 1.076 | ×2.4 |

Mémoire (`py_peak_mb`, mesurée à part des durées) : inchangée à 13,6 Mo pour 10 000 clips, hors index (+4,2 Mo, voir ci-dessus).
