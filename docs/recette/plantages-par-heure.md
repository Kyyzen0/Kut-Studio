# Plantages par heure de session

Indicateur de fiabilité de l'ADR-0011 : **cible 0** plantage sur **trois séances de 2 h par Mac** (M1 Air et M4 mini). Il se calcule à partir du journal local
(`core/diagnostics_log.py`), qui ne quitte jamais la machine sans un geste de l'utilisateur.

## Faire une séance de 2 h

1. Noter l'**heure de début** (à la minute) sur la fiche de séance ([etapes-communes.md](etapes-communes.md)). Travailler normalement : alterner les parcours a, b et c, ouvrir et fermer des
   projets, lire, couper, exporter. Au moins **un export** et **une lecture de 5 min** par séance.
2. Noter les **pannes visibles** au fil de l'eau : l'application qui se ferme, un gel de plus de 5 s, un message d'erreur. Une fiche de défaut par incident ([fiche-defaut.md](fiche-defaut.md)).
3. À la fin, noter l'**heure de fin**.

## Calculer

```bash
python -m tools.recette crashes \
  --session 2026-10-22T09:00/2026-10-22T11:00 \
  --session 2026-10-22T14:00/2026-10-22T16:00 \
  --reports-dir ~/Library/Logs/DiagnosticReports
```

La commande lit (en **lecture seule**) le journal `~/Library/Logs/Kut-Studio/kut-studio.log` et ses trois sauvegardes, et rend : séances, heures, **exceptions non rattrapées**
(niveau `CRITICAL`), **rapports de plantage du système** de Kut-Studio ou de Python dans les séances (une mort native, comme une erreur de segmentation, ne laisse aucune ligne Python :
seul le rapport macOS la révèle), **plantages par heure**, et le nombre de lignes d'erreur (non fatales, à lire). `--log-dir` pointe un autre dossier de journaux (une copie rapportée par un
bêta-testeur, par exemple).

- Les **intervalles relevés à la main** sont la source de vérité des heures : le journal n'a pas de ligne de fin de session. Sans `--session`, la commande estime les séances (du
  premier « Démarrage » à la dernière ligne avant le suivant) et le marque `hours_estimated: true` : c'est une **borne basse** de la durée.
- Un plantage observé à l'œil **sans trace** (l'application a disparu sans exception ni rapport) compte quand même : l'ajouter à la main au total de la fiche, et ouvrir une fiche de défaut.
- Un gel (application figée sans plantage) **ne compte pas** comme plantage mais ouvre une fiche (gravité « bloquant » s'il oblige à forcer à quitter).

## Fiche de résultat (une par Mac)

| Séance | Début | Fin | Heures | Exceptions non rattrapées | Rapports système | Plantages observés sans trace | Total | Par heure |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| 3 | | | | | | | | |
| **Somme** | | | | | | | | |

Porte de sortie (ADR-0011) : **0 plantage** sur les trois séances de chacun des deux Mac. Pour les bêta-testeurs Windows, la même lecture se fait sur la copie du journal qu'ils joignent
via « Copier le diagnostic ».
