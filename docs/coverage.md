# Couverture de code

La suite compte plus de 5 300 tests ; la couverture dit ce qu'ils **ne touchent pas**. Elle est mesurée par
[pytest-cov](https://pytest-cov.readthedocs.io/) (coverage.py), **sans service externe** : pas de Codecov ni d'envoi de
données, le rapport reste dans la CI (résumé de l'exécution et artefacts) ou sur votre machine.

## Lancer la mesure en local

```bash
python -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q -n auto --cov --cov-branch --cov-report=term-missing:skip-covered
```

* `--cov` sans argument prend le périmètre de `pyproject.toml` (`[tool.coverage.run]`) : **`core/` et `ui/`**.
  `main.py` et `build.py` sont couverts par les smoke tests de la CI (`main.py --smoke-test`, application construite),
  pas par pytest : les mesurer n'apprendrait rien.
* `--cov-branch` compte aussi les **branches** (un `if` dont un seul côté est exécuté est partiel). Le total suivi par
  le cliquet inclut les branches.
* `term-missing:skip-covered` liste, par fichier, les lignes non exécutées et omet les fichiers complets.
* Pour comparer à la baseline, ajoutez `--cov-report=json` puis `python tools/coverage_ratchet.py coverage.json`.
  `--cov-report=html` produit un rapport navigable dans `htmlcov/` (ignoré par git, comme `coverage.json` et
  `coverage.xml`).

La couverture **n'est jamais activée par défaut** (`--cov` n'est pas dans les options de pytest, un test le vérifie) :
l'instrumentation ralentit la suite, qui doit rester rapide à lancer pendant le développement.

Exclusions (`[tool.coverage.report]`) : les lignes `# pragma: no cover`, les blocs `if TYPE_CHECKING:` et
`if __name__ == "__main__":`, les `raise NotImplementedError`.

## Le cliquet

`tools/coverage_ratchet.py` lit le `coverage.json` et compare le total (lignes + branches) à
`tests/coverage_baseline.json` :

```json
{"total_percent": 85.3, "updated": "2026-10-07", "reason": "baseline initiale du lot 3"}
```

| Situation | Code de sortie | Ce qui s'affiche |
| --- | --- | --- |
| total sous la baseline **moins 0,1 point** (tolérance de bruit) | 1 (la CI échoue) | l'écart, les 10 fichiers avec le plus de lignes non couvertes, la procédure ci-dessous |
| total au-dessus d'au moins 0,1 point | 0 | invitation à relever la baseline (valeur proposée) |
| total stable, ou sous la baseline de moins de 0,1 point | 0 | « OK » et l'écart |
| rapport ou baseline invalide, rapport sans branches | 2 | ce qui ne va pas |

Comme les autres cliquets du dépôt (dette mypy, textes d'interface en dur), il ne sert pas à viser un chiffre mais à
**empêcher la régression silencieuse** : du code ajouté sans test fait baisser le total et se voit dans la pull request.
La baseline est toujours le total mesuré **arrondi au dixième inférieur** (jamais plus indulgente que la mesure).

Le total varie un peu d'une exécution à l'autre **sans changement de code** : 85,24 puis 85,20 % sur deux runs Linux
de la CI le 2026-10-07, soit 33 lignes et branches sur 73 248. Elles sont toutes dans du code exécuté par des threads
d'arrière-plan (aperçus de médias, superviseur de processus, scopes) qui finissent ou non avant la fin de leur test.
Le cliquet tolère donc un écart **fixe** de 0,1 point sous la baseline (`NOISE_TOLERANCE`, deux fois le bruit observé) ;
l'arrondi seul laissait une marge variable, de 0 à 0,1 point selon la mesure. Rendre ces tests déterministes
(attendre la fin des aperçus) réduirait ce bruit.

En CI (`.github/workflows/multiplatform.yml`, job en matrice), seul **Linux** mesure, avec la même condition que mypy ;
macOS et Windows lancent la suite sans instrumentation. Sur Linux : le cliquet bloque le job, son texte est ajouté au
résumé de l'exécution (même en cas d'échec), et `coverage.xml` + `coverage.json` sont publiés comme artefacts
(7 jours).

## Mettre à jour la baseline

**Relever** la baseline après un progrès est toujours bienvenu : c'est ce qui garde le terrain gagné.

**L'abaisser** n'est légitime que si la baisse n'est pas du code non testé : code testé supprimé (le total se calcule
sur moins de lignes bien couvertes), test retiré pour une bonne raison, code déplacé derrière une condition de
plateforme, nouvelle version de coverage.py qui compte autrement. Ce n'est **jamais** le moyen de faire passer une
pull request qui ajoute du code sans test : ajoutez les tests, ou marquez `# pragma: no cover` ce qui est vraiment
inatteignable en test (avec la raison en commentaire).

Dans les deux cas :

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q -n auto --cov --cov-branch --cov-report=json
python tools/coverage_ratchet.py coverage.json --update-baseline --reason "pourquoi cette valeur"
```

La commande écrit le total arrondi, la date du jour et la **raison, obligatoire** (une baseline sans raison est
refusée). Commitez `tests/coverage_baseline.json` seul, avec la raison dans le message. La valeur de référence est
celle de la **CI Linux** : si votre mesure locale (macOS, Windows) diffère, prenez le total affiché par l'étape
« Coverage ratchet » ou l'artefact `coverage-Linux` de la pull request.

## Code de plateforme : partiellement non couvert, c'est attendu

Une mesure ne voit que le système où elle tourne. Le code conditionné par la plateforme (`sys.platform == "win32"`,
`"darwin"`, Job Objects Windows, VideoToolbox, chemins macOS de l'application…) n'est exécuté que sur son OS : sur la
CI Linux, les branches Windows et macOS apparaissent **non couvertes**, et inversement sur une machine de
développement macOS. De même, les tests qui dépendent de la machine **se sautent avec leur raison** (libass,
encodeurs matériels, GPU) et ne couvrent rien là où ils sont sautés.

Conséquences :

* un fichier de plateforme partiellement rouge n'est pas forcément de la dette : regardez **quelles** lignes manquent ;
* un total local peut différer de quelques dixièmes de celui de la CI : seul celui de la CI fait foi ;
* ces branches restent exercées par les tests de la matrice macOS / Windows, simplement non mesurées.
