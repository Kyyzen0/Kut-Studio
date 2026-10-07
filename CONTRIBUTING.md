# Contribuer à Kut-Studio

> **In English, briefly.** Kut-Studio is developed in French (code comments, commit messages, docs), but issues and
> pull requests in English are welcome. You need Python 3.11+, FFmpeg + ffprobe on your `PATH`, and
> `pip install -r requirements-dev.txt` in a virtual environment. Before opening a pull request, run
> `python -m pytest -q -n auto --timeout=600`, `python -m ruff check .` and `python -m mypy`: the test suite also runs
> the repository's ratchets (hard-coded UI strings, dead code, silent failures, typing debt, CI workflow invariants).
> CI must be green on macOS, Windows and Linux, plus the libass job and the coverage ratchet on Linux. The map of the
> code is [docs/architecture.md](docs/architecture.md).

Merci de vouloir contribuer. Ce guide dit comment préparer un poste, ce que la suite de tests vérifie au-delà des
tests eux-mêmes, et ce qu'une pull request doit contenir. Pour savoir où vit chaque chose dans le code (modèle,
plan de rendu, interface) et quel test la garde, la carte est [docs/architecture.md](docs/architecture.md).

## Préparer le poste

**Pré-requis**

* **Python 3.11 ou plus récent.** La CI et le workflow de release utilisent 3.11 ; 3.10 ne suffit pas
  (`typing.NotRequired`, `tomllib`).
* **FFmpeg et ffprobe** dans le `PATH` (import de médias, aperçu fidèle, scopes, export). Par système :

  ```bash
  brew install ffmpeg             # macOS (Homebrew)
  winget install Gyan.FFmpeg      # Windows
  sudo apt install ffmpeg         # Debian / Ubuntu
  ```

  Les sous-titres incrustés exigent un FFmpeg compilé avec **libass** ; sans lui, leurs tests se sautent avec la
  raison (sur macOS, `brew install ffmpeg-full` l'apporte : voir [docs/ci-libass.md](docs/ci-libass.md)).

**Installation**

```bash
git clone https://github.com/Kyyzen0/Kut-Studio.git
cd Kut-Studio
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` inclut `requirements.txt` (PySide6, numpy) et ajoute les outils : pytest, pytest-qt,
pytest-xdist, pytest-timeout, pytest-cov, ruff, mypy, PyInstaller. Une dépendance d'**exécution** nouvelle se discute
avant d'être ajoutée ; un outil de développement va dans `requirements-dev.txt` seulement.

**Lancer l'application**

```bash
python main.py                     # l'application
python main.py --smoke-test        # ouvre et ferme la fenêtre, puis vérifie numpy, les shaders GPU, HTTPS et la supervision : code 0 si tout va bien
```

Sur une machine sans écran, préfixez par `QT_QPA_PLATFORM=offscreen`. Pour ne pas toucher à vos réglages réels
pendant un essai, pointez `KUT_STUDIO_CONFIG_DIR` vers un dossier temporaire
(`KUT_STUDIO_CONFIG_DIR=$(mktemp -d) python main.py`). Les tests le font d'eux-mêmes (`tests/conftest.py`), et ils
y fixent aussi `QT_QPA_PLATFORM=offscreen` et coupent la recherche de mises à jour.

## La boucle de développement

```bash
python -m pytest -q -n auto --timeout=600   # suite complète, en parallèle, sans couverture (rapide)
python -m ruff check .
python -m mypy                              # core/
```

Pendant le travail, lancez seulement les fichiers concernés (`python -m pytest -q tests/test_scopes.py`), puis la
suite complète avant de pousser : elle contient aussi les cliquets ci-dessous, qui échouent sur des fichiers que vous
n'avez peut-être pas ouverts.

Quand vous touchez à de la **logique** (`core/`, ou le comportement d'un panneau), mesurez aussi la couverture, comme
le fait la CI Linux :

```bash
python -m pytest -q -n auto --cov --cov-branch --cov-report=term-missing:skip-covered --cov-report=json
python tools/coverage_ratchet.py coverage.json
```

La couverture n'est jamais activée par défaut (elle ralentit la suite). Tout est dans
[docs/coverage.md](docs/coverage.md).

Certains tests dépendent de la machine et **se sautent avec leur raison** au lieu de passer à vide (`pytest -rs` les
liste) : libass (`KUT_STUDIO_REQUIRE_LIBASS=1` transforme le saut en échec) et les encodeurs matériels
(`KUT_STUDIO_REQUIRE_HARDWARE=…`, voir [docs/hardware-encoding.md](docs/hardware-encoding.md)).

## Cliquets et gardes

Ces tests ne vérifient pas une fonctionnalité : ils empêchent une dette de grossir ou un défaut connu de revenir. Ils
tournent avec la suite complète ; chacun se lance aussi seul. Quand l'un d'eux échoue, **corrigez la cause** : ne
relâchez pas la garde (et si vous pensez qu'elle a tort, dites-le dans la PR).

| Garde | Intention | Lancer seul |
| --- | --- | --- |
| **Textes d'interface en dur** (cliquet i18n) | Aucun texte visible écrit en dur dans `ui/` : tout passe par `translate("clé")` avec fr, en et es. La baseline `tests/i18n_hardcoded_baseline.json` est **vide** ; une clé doit exister dans les trois langues ([docs/i18n.md](docs/i18n.md)). | `python -m tools.i18n_audit` · `python -m pytest tests/test_i18n_hardcoded.py tests/test_i18n_parity.py` |
| **Code mort** | Tout module de `core/` et `ui/` est atteignable depuis `main.py` (ou un point d'entrée documenté de `ENTRY_POINTS`), et aucune fonction privée de module n'est définie sans appel ([docs/dead-code-audit.md](docs/dead-code-audit.md)). | `python -m pytest tests/test_dead_code_guard.py` |
| **Échecs silencieux** | Dans `core/`, `ui/`, `main.py` et `build.py` : aucun `except` nu / `Exception` / `BaseException` dont le corps n'est que `pass`, et aucun `assert` (il disparaît sous `python -O`). Restez tolérant mais journalisez (`LOGGER.debug(..., exc_info=True)`), ou écrivez une vraie garde. | `python -m pytest tests/test_silent_failures_guard.py` |
| **Processus enfants** | Tout processus (FFmpeg…) passe par `core/process_supervisor.py`, pour mourir avec l'application même après un arrêt brutal ([docs/process-supervision.md](docs/process-supervision.md)). | `python -m pytest tests/test_process_launch_guard.py` |
| **Dette de typage** (cliquet mypy) | `core/` est vérifié par mypy ; la liste `ignore_errors` de `pyproject.toml` doit rester égale à `tests/mypy_debt_baseline.txt`. On n'y ajoute **jamais** un module ; quand on en nettoie un, on le retire des **deux** fichiers. | `python -m mypy` · `python -m pytest tests/test_typing_ratchet.py` |
| **Couverture** (cliquet) | Le total (lignes + branches, `core/` + `ui/`) ne descend pas sous `tests/coverage_baseline.json`, à une tolérance de bruit de 0,1 point près. Mesuré sur la CI Linux seulement. | voir la boucle de développement ci-dessus |
| **Raccourcis clavier** | Chaque commande a une traduction et une fonction, aucun raccourci par défaut n'en recouvre un autre, et un nouveau raccourci *par défaut* s'ajoute explicitement à l'ensemble attendu (README, section raccourcis). | `python -m pytest tests/test_shortcuts.py tests/test_shortcuts_ui.py` |
| **Workflows de CI et de release** | Le YAML garde ses invariants : matrice des 3 OS, job libass bloquant et indépendant, couverture sous Linux avec la condition de mypy, permissions en lecture, actions épinglées ; publication réservée au tag égal à `core/app_version.py`. | `python -m pytest tests/test_ci_workflow.py tests/test_release_workflow.py` |

**Allowlist des échecs silencieux.** `tests/silent_failures_allowlist.json` accueille un site qu'on ne peut ni
journaliser ni transformer en garde. Une entrée a quatre champs : `path` (`core/x.py`), `rule` (`except-pass` ou
`assert`), `scope` (`Classe.methode`, ou `<module>`) et `reason`, obligatoire. Elle est identifiée par sa portée, pas
par un numéro de ligne, et une entrée devenue inutile fait échouer le test. La liste est vide : l'agrandir doit rester
l'exception, justifiée dans la PR.

**Baseline de couverture.** Elle ne se modifie qu'avec la commande prévue, qui exige une raison :

```bash
python tools/coverage_ratchet.py coverage.json --update-baseline --reason "pourquoi cette valeur"
```

Relever la baseline après un progrès est bienvenu. L'abaisser n'est légitime que si la baisse ne vient pas de code
non testé (code testé supprimé, test retiré pour une bonne raison…). La valeur de référence est celle de la CI Linux
(artefact `coverage-Linux` de la PR), pas celle de votre machine. Détails : [docs/coverage.md](docs/coverage.md).

## Messages de commit

En français : un **résumé** court, sous la forme « Zone : ce qui change, dit par son effet », puis un **corps** qui dit
**pourquoi** (le défaut observé, la mesure, la cause) et ce qui le prouve. Un commit fait une chose ; une PR en
contient souvent plusieurs. Exemples réels de l'historique :

```text
Projets : une clé inconnue dans un .kut lève ValueError, plus TypeError

MediaAsset(**item) lève TypeError sur une clé inconnue, que load_project laissait
fuir alors que son contrat promet ValueError pour tout fichier abîmé (l'interface
le rattrapait, un appel direct non). TypeError rejoint les exceptions traduites ;
la docstring ne mentionne plus que ValueError. […]
```

```text
Aperçu fidèle : un segment se compose à partir de son début

Un segment d'aperçu à t composait toute la timeline depuis 0 puis jetait le
début (-ss de sortie) : son coût croissait avec sa position (une image à 28 s :
1,6 s au lieu de 0,2 s). […]
```

```text
Inspecteur : le curseur de volume dit qu'il ne règle que le moniteur
```

## Pull requests

1. **Partez de `main` à jour**, sur une branche dédiée (`git switch -c ma-branche origin/main`). Une PR traite un
   sujet ; on n'y mélange pas une réorganisation et une correction.
2. **La CI doit être verte** (workflow *Multiplatform*) :
   * macOS, Windows et Ubuntu (Python 3.11) : ruff, la suite complète, le smoke test depuis les sources, la
     construction PyInstaller et le smoke test de l'application construite ; mypy sur Linux ;
   * sur Linux, la suite tourne avec la couverture et le **cliquet de couverture** bloque toute baisse ;
   * le job **macOS / libass** rejoue les tests de sous-titres avec un FFmpeg qui a libass, et un saut y est un
     échec.
3. **La description** suit les habitudes du dépôt :
   * **Pourquoi** : le problème observé, mesuré si possible (avant / après) ;
   * **Ce qui change** : par commit ou par thème, avec les fichiers clés ;
   * **Tests** ou **Vérification** : ce qui prouve le changement (tests ajoutés, qui échouent sur l'ancien code ;
     suite complète ; rendu réel relu) ;
   * **À savoir** / **Limites connues** : ce qui n'a pas été vérifié, ce qui reste à faire.

   Le modèle `.github/PULL_REQUEST_TEMPLATE.md` reprend la liste de contrôle.
4. **Interface** : joignez une capture (avant / après), et vérifiez les petites fenêtres (`tests/test_ui_small_windows.py`)
   et les trois langues.
5. **Changement visible pour l'utilisateur** : ajoutez une ligne dans la section `[Unreleased]` de
   [CHANGELOG.md](CHANGELOG.md).

## Signaler un bogue

Utilisez le modèle d'issue « Bogue ». Joignez la version de Kut-Studio (Aide › À propos), le système, la version de
FFmpeg (`ffmpeg -version`) et, si possible, la fin du **journal de diagnostic**, `kut-studio.log`, qui recueille
les erreurs non rattrapées et les échecs tolérés :

| Système | Dossier du journal |
| --- | --- |
| macOS | `~/Library/Logs/Kut-Studio/` |
| Windows | `%LOCALAPPDATA%\Kut-Studio\Logs\` |
| Linux | `$XDG_STATE_HOME/kut-studio/` (par défaut `~/.local/state/kut-studio/`) |

La variable `KUT_STUDIO_LOG_DIR` remplace ce dossier (source : `core/platform_paths.py`, `user_log_dir`). Un projet
`.kut` enregistre le chemin de chaque média tel qu'il a été importé, en général **complet** (nom d'utilisateur, noms de
dossiers) : relisez-le avant de le joindre.
