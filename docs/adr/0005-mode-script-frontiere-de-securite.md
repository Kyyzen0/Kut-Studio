# ADR-0005 : le mode script — un fichier ne s'exécute jamais, un script se lance exprès

**Statut :** Accepté le 2026-10-12 (les cinq décisions ouvertes sont tranchées, voir « Décisions prises » en fin de document). Étape 0 livrée ; le reste du plan reste à faire.
**Date :** 2026-10-12
**Décideurs :** mainteneur de Kut-Studio

Objectif de l'utilisateur : « pouvoir lire, faire des scripts de façon très poussée ». Concrètement : décrire un
montage par un script que le moteur décode et exécute, lire tout ce que contient un projet (clips, effets, nœuds,
courbes, bus), automatiser sans fenêtre, et garder la propriété centrale du produit, **l'aperçu est l'export**.

## Résumé de la décision

1. **Trois règles de sécurité**, avant toute fonctionnalité : un `.kut` ne contient que des données et n'en
   exécute jamais ; un script *déclaratif* est lui aussi une donnée (un jeu fermé de verbes, aucune expression) ; du
   *Python* ne s'exécute que parce que l'utilisateur l'a demandé, jamais à l'ouverture d'un fichier.
2. **Deux niveaux de script**, qui partagent un seul chemin d'écriture : un format **déclaratif** JSON (sûr par
   construction, c'est le niveau des outils et des LLM) et une **façade Python** stable (lire tout, écrire par les
   opérations du modèle, boucles et conditions), à lancer explicitement.
3. **Un seul chemin de mutation**, qualifié par l'historique d'annulation : une `Session` sans Qt qui applique des
   opérations sur une copie, les valide, puis valide la transaction d'un coup. L'interface et les scripts
   convergent sur les mêmes fonctions de `core/`.
4. **Un CLI qui est l'application elle-même** (`Kut-Studio info | validate | apply | export | run`), sans fenêtre, plutôt
   qu'un second programme à installer.
5. **Phasage** : d'abord le sol commun (export sans interface, session), puis le déclaratif, puis Python, puis la
   console dans l'application.

## Contexte

Ce que le code permet déjà, vérifié le 2026-10-12 :

| Constat | Où | Conséquence |
| --- | --- | --- |
| Le modèle, la lecture/écriture de `.kut`, le plan de rendu et l'historique **ne dépendent pas de Qt** | `core/project_model.py`, `project_io.py`, `render_plan.py`, `edit_history.py` (docstring : « utilisable en CLI… ou pour de l'édition scriptée ») | La lecture et l'édition sont scriptables telles quelles. |
| `ExportEngine` est un `QObject` qui pilote un `QProcess` et attend une boucle d'événements ; `_build_command` est une méthode d'instance **privée** | `core/export_engine.py:478`, `:884` | Il n'existe **aucun moyen sans interface** de lancer un export. C'est le premier obstacle. |
| Le texte et les formes sont dessinés par Qt (`QPainter`), mais sans fenêtre ni boucle | `core/mograph_stream.py` (`ensure_qt_gui`) | Un export sans interface a besoin d'une `QGuiApplication` hors écran, pas d'une fenêtre. |
| `main.py` n'a pas d'`argparse` ; il lit `--smoke-test` et un chemin de projet, tous deux pour l'interface | `main.py:29-46` | Pas de CLI. Mais le mode *assistant* (`helper_mode_exit_code`) montre le précédent : une sortie avant Qt. |
| L'historique est une pile de **copies profondes** ; l'interface l'alimente par un `record(project, label)` après chaque mutation | `core/edit_history.py:150`, `ui/main_window_mixins/history.py` | Un script peut enregistrer dans le même historique, sans classe « commande » à inventer. La politique de recalage des clips imbriqués est **déjà dans `core/`** (`core.sequences.clamp_nested_clips`) ; l'interface n'y ajoute que le libellé de l'étape et un message de barre d'état (`_with_nested_clamp`). La session appelle la même fonction. |
| Les fonctions qui modifient le modèle sont dans `core/` (`timeline_operations`, `track_operations`, `audio_effects_model`, `audio_buses`, `effects_model`, `color_nodes`, `keyframe_editing`, `sequences`…), sans Qt, et lèvent `ValueError` / `KeyError` | `core/*.py` | Les verbes d'un script sont ces fonctions. Rien à réécrire. |
| Aucune API ne **décrit** le modèle entier ; seuls les sérialiseurs le parcourent | `core/project_io.py` (`_sequence_to_dict`, `_clip_to_dict`) | Lire « tout » demande une vue de lecture stable, distincte du format de fichier. |

Ce qui touche à la sécurité :

- **Aucune exécution dynamique dans le produit** : pas de `eval`, `exec`, `pickle` (les `np.load` sont en
  `allow_pickle=False`), pas de chargement de module à partir de données, pas de `yaml.load`. Le seul `importlib` est
  `find_spec("numpy")`. Tous les sous-processus reçoivent une **liste** d'arguments, jamais un shell.
- **Les données utilisateur n'atteignent FFmpeg que par des nombres formatés** (courbes d'animation, `geq`, `aeval`),
  des chemins échappés (`_escape_filter_path`) ou des identifiants filtrés. Le seul vecteur réel trouvé à ce jour a
  été **l'identifiant d'un nœud** : un `.kut` forgé pouvait s'en servir pour ajouter un filtre FFmpeg (lecture d'un
  fichier local). Corrigé en 0.4.0 par `core/node_graph.py` (`[A-Za-z0-9_]{1,64}`, `fullmatch`), avec abandon du nœud
  fautif au chargement.
- Le chargement d'un `.kut` est **strict pour la structure** : enveloppe et version vérifiées, flottants finis, pistes,
  clips et médias invalides refusés, version plus récente refusée. Il est **tolérant pour les sous-objets
  décoratifs** : un effet de clip ou de piste, une association de ducking, une transition, un bus ou un identifiant de
  nœud invalide est ignoré **seul**, sans faire échouer le fichier (`_deserialize_clip_effects`,
  `_deserialize_audio_effects`, `_deserialize_ducking_sidechains`, `_deserialize_buses`…). Ce choix protège
  l'ouverture d'un projet abîmé dans l'interface, mais il veut dire qu'un `.kut` forgé peut être accepté **en perdant
  du contenu sans le dire** : `validate` ne peut pas s'appuyer sur « le chargement a réussi » pour affirmer
  « le fichier est intact » (voir §6 et le plan, étape 3).

Ce que le script de la vidéo de présentation a coûté (97 clips, par `add_graphic_clip`, `add_keyframe`,
`build_render_plan`) : un export à monter à la main (`QObject`, `ExportRequest`), `build_render_plan(window=...)` qui
tronque en silence, des temps d'image clés relatifs au clip à recaler, et des aides (pixels → coordonnées
normalisées, décalages de parentage) réinventées. Le mode script doit supprimer ces frottements.

## Décision

### 1. La frontière de sécurité

Trois règles, par ordre d'importance. Elles s'appliquent avant et indépendamment de toute fonctionnalité.

**R1. Un projet (`.kut`) ne s'exécute jamais.** C'est déjà vrai et c'est un invariant à protéger : le format ne
reçoit **aucun** champ dont la valeur soit interprétée comme du code, une expression ou un chemin à exécuter. Un test
de garde (comme celui des échecs silencieux) interdit `eval`, `exec`, `pickle`, `__import__`, `runpy` et
`importlib` dans `core/`, `ui/` et `main.py`, à part une liste blanche courte et justifiée.

**R2. Un script déclaratif est une donnée, pas un programme.** Il est décodé par un jeu **fermé** de verbes (§3), validé
par un schéma, puis exécuté par les fonctions du modèle. Il ne contient ni expression, ni référence de module, ni
calcul, ni boucle (seuls des alias nomment les objets créés, §3). Ce qu'il **ne peut pas** faire, par construction :
- écrire un fichier : les chemins de sortie viennent de l'appelant (arguments du CLI, boîte de dialogue), jamais du script ;
- lancer un processus, ouvrir un réseau, lire une variable d'environnement ;
- viser autre chose que des médias existants (lecture) ou le projet (écriture) ;
- sortir des bornes du modèle : chaque nombre passe par les validateurs de `__post_init__` (bornes de gain, de
  durée, de fréquence…), chaque identifiant par le filtre de `node_graph` ;
- modifier un projet **à moitié** : une transaction est tout ou rien (§4).

Ouvrir un script déclaratif présente donc le même risque qu'ouvrir un projet.

**R3. Du Python ne s'exécute que parce que l'utilisateur l'a demandé, et jamais à l'ouverture.** Aucun fichier
(`.kut`, script déclaratif, préréglage, modèle) ne peut déclencher du Python. Le Python est un *autre* type de fichier,
lancé par une action explicite : `Kut-Studio run script.py`, ou, dans l'application, la console derrière une
confirmation qui montre la source. **Pas de bac à sable annoncé** : en Python, un bac à sable de langage est contournable
(les bibliothèques d'« exécution restreinte » ont un long historique d'évasions), et le promettre serait pire
que de ne rien promettre. Le contrat est celui d'un script local : il a les droits de l'utilisateur, comme n'importe quel
script qu'il lancerait lui-même. La documentation le dit en toutes lettres.

### 2. Deux niveaux, un seul chemin d'écriture

| | Niveau 1 : déclaratif | Niveau 2 : façade Python |
| --- | --- | --- |
| Fichier | `*.kutscript.json` | `*.py` |
| Qui l'exécute | le moteur (jeu fermé de verbes) | l'interpréteur Python de l'utilisateur |
| Boucles, conditions, calculs | non (on génère le script avec un outil) | oui |
| Risque à l'ouverture | celui d'un projet | celui de n'importe quel script local (R3) |
| Public | outils, LLM, intégrations, modèles de montage | montages « très poussés », analyses, lots |
| Lit tout le modèle | via `describe` et `query` | oui (vues de lecture, §5) |

Les deux produisent des **opérations** (§3) et les confient à la même `Session` (§4). Un script déclaratif n'est
pas une version réduite de la façade : c'est la **forme sérialisée de la liste d'opérations** que la façade
produit. Écrire sur disque ce que fait un script Python donne un script déclaratif rejouable.

Le déclaratif d'abord, parce qu'il est sûr, vérifiable et utile seul. Python vient ensuite, pour la puissance.

### 3. Le format déclaratif

JSON, pas TOML ni YAML : même famille que le `.kut`, aucun analyseur nouveau, lisible par les outils, et `yaml.load`
est précisément la porte qu'on se refuse. Un champ `note` libre sert de commentaire.

```json
{
  "kut_script": 1,
  "ops": [
    {"op": "add_clip", "track": "V1", "asset": "intro.mp4", "at": 0.0},
    {"op": "add_graphic", "track": "V2", "kind": "title", "text": "Bonjour", "at": 0.5, "duration": 3.0},
    {"op": "add_bus", "id": "voix", "name": "Voix"},
    {"op": "route_track", "track": "A1", "bus": "voix"},
    {"op": "add_bus_insert", "bus": "voix", "effect": "compressor", "params": {"ratio": 4}}
  ]
}
```

- **Verbes = fonctions du modèle**, un pour un (`add_clip` → `timeline_operations.add_clip_to_track`, `route_track` →
  `audio_buses.route_track`…). Le catalogue est généré depuis une table unique `VERBS` qui sert aussi à valider, à
  documenter et à produire le schéma JSON : un verbe sans entrée de table n'existe pas.
- **Objets créés : identifiants stables et alias.** Les fonctions de création du modèle génèrent aujourd'hui leur
  identifiant (`add_clip_to_track` : `clip-` + un UUID tronqué, `core/timeline_operations.py:685`) : un script qui
  ajoute un clip puis lui pose une image clé ne pourrait pas le désigner, et rejoué il viserait un autre identifiant.
  Deux règles :
  1. tout verbe de création accepte un `"id"` explicite (validé par le filtre de `node_graph` et refusé s'il existe
     déjà) ; **prérequis** : les fonctions de création du modèle acceptent un identifiant fourni (`clip_id=None`
     garde le comportement actuel) ;
  2. un verbe de création peut porter un **alias** (`"as": "intro"`), un nom du script lié à l'objet créé ; une
     opération suivante le désigne par `{"ref": "intro"}`. Un alias est un simple nom résolu **au décodage**, dans
     une table locale au script, jamais évalué ni calculé : ce n'est pas une variable au sens de R2. Un alias inconnu
     ou défini deux fois est une erreur.
  La façade Python rend les identifiants créés, et la sérialisation d'un script construit en Python **écrit les
  identifiants explicites** : le rejouer produit un projet identique, pas un projet équivalent à des UUID près.
- **Sélecteurs** : par identifiant, ou par nom exact quand il est unique (`"track": "A1"`) ; une ambiguïté est une
  erreur, jamais un choix silencieux. Les identifiants créés par le script (`"id": "voix"`) sont validés par le filtre
  de `node_graph`.
- **Unités et conventions** documentées une fois : secondes sur la timeline, **temps d'image clé relatifs au
  clip**, coordonnées normalisées (avec une aide px → normalisé dans la façade, pas dans chaque script).
- **Versionné** (`kut_script`) ; une version plus récente est refusée, comme pour le `.kut`. Les champs inconnus sont
  une erreur (jamais ignorés en silence : un script mal orthographié ne doit pas « réussir »).
- Pas de verbe `export` : exporter est une décision de l'appelant (`Kut-Studio export`), qui fournit le chemin
  de sortie (R2).

### 4. Un seul chemin d'écriture : la session

`core/scripting/session.py`, sans Qt :

```
Session(project, history)
  .apply(ops)        # valide, applique sur une copie, rend un rapport ; tout ou rien
  .transaction(label)# regroupe plusieurs opérations en UNE étape d'historique
  .undo() / .redo()  # ProjectHistory
```

- **Atomicité** : les opérations s'appliquent à une copie profonde ; si l'une lève, le projet n'est pas touché et le
  rapport dit laquelle (index, verbe, message). En cas de succès, la copie est validée en **une** étape d'annulation.
- **Une seule voie** : l'interface et les scripts appellent les mêmes fonctions de `core/`, recadrage des clips imbriqués
  compris : `_record_history` (interface) comme la session appellent `clamp_nested_clips` avant d'enregistrer, et la
  session rend les ajustements dans son rapport. Ce que l'interface fait d'autre autour d'une édition ne change pas le
  projet (clore les saisies en cours, invalider les aperçus) : la session n'a pas à le reproduire.
- **L'aperçu reste l'export** : un script ne touche que le modèle ; le plan de rendu, l'aperçu et l'export lisent
  ce modèle comme toujours. Un test de parité (le projet produit par un script est identique à celui produit
  par les mêmes gestes dans l'interface) tient lieu de preuve.

### 5. La façade Python

Module `core/scripting/api.py`, **seule** surface publique ; les modules de `core/` restent internes, libres
d'évoluer.

- **Lire tout** : `session.describe()` rend un dictionnaire JSON **versionné** (séquences, pistes, clips, effets,
  nœuds de couleur et de composition, courbes, images clés, bus, marqueurs) ; des vues typées en lecture seule
  donnent un accès objet. Le format de `describe` est distinct du format de fichier : il peut évoluer pour la
  lisibilité sans casser les projets.
- **Écrire** : par `session.apply` et `session.transaction`, jamais en mutant les objets du modèle. Les vues sont
  immuables, ce qui empêche la mutation accidentelle qui contournerait l'historique.
- **Aides** : conversion px → normalisé, position dans un parent, temps relatif au clip.
- **Stabilité** : `api_version` en `MAJEUR.MINEUR` ; une rupture n'arrive qu'à un changement de majeur, annoncé dans
  le CHANGELOG. Un test fige la liste des noms publics.

### 6. Le CLI : l'application elle-même

`Kut-Studio <commande>`, traité dans `main.py` **avant Qt**, comme le mode assistant existant. Une seule installation, le
même binaire, aucune fenêtre.

| Commande | Rôle |
| --- | --- |
| `info projet.kut [--json]` | résumé, version, médias manquants |
| `validate projet.kut` / `script.json` | charge ou valide ; code de sortie 0 (intact), 1 (invalide), 2 (chargé **avec des entrées ignorées**, listées) |
| `apply script.json projet.kut -o sortie.kut [--dry-run]` | exécute un script déclaratif ; `--dry-run` rend le rapport sans écrire |
| `export projet.kut -o video.mp4 --preset …` | export sans interface (§7) |
| `run script.py [--project projet.kut]` | exécute du Python (R3) ; refuse sans `--allow-python` explicite en mode non interactif |

Sortie lisible par une machine (`--json`), codes de sortie stables, erreurs sur la sortie d'erreur. Écriture atomique
(déjà le cas de `save_project`). **L'entrée n'est jamais écrasée sans `--in-place`** : `-o` vers un autre fichier est le
cas normal, et un `-o` qui désigne **le même fichier que l'entrée** (même chemin une fois résolu, lien symbolique compris)
est refusé avec un message qui nomme `--in-place`. Un seul critère donc, pour l'implémentation comme pour les tests :
« la sortie est-elle l'entrée ? » ; si oui, `--in-place` est exigé (et le verrou consultatif vérifié).

### 7. L'export sans interface (le sol commun)

Prérequis du CLI et de toute automatisation :

1. Extraire de l'export deux étapes **publiques et sans Qt**, dans l'ordre où `ExportEngine.start` les enchaîne :
   - **la préparation** (`prepare_export(request, progress, cancelled)`) : les flux interpolés des clips en mélange
     d'images ou en flux optique (`retime_layers.plan_needs_preparation` / `prepare_plan`, qui rendent des
     `PreparedStream`), avec progression et annulation. Sans elle, un projet qui en utilise lève `RetimeError` dans
     `_prepared_source` : le CLI échouerait là où l'interface réussit ;
   - **la construction de la commande** (`build_export_command(request, prepared)`) : la liste d'arguments et la
     liste des fichiers temporaires à nettoyer.
   `ExportEngine` appelle ces deux fonctions ; ses tests actuels continuent de passer, ce qui prouve que l'export de
   l'interface n'a pas changé.
2. Un exécuteur sans Qt (`supervised_popen`, lecture de la progression de FFmpeg, annulation, nettoyage des fichiers
   de préparation et temporaires) utilisé par le CLI. Les
   fichiers de l'interface (`ExportEngine`, file de rendu) gardent leur `QProcess` : on ne les réécrit pas, on partage
   la construction de la commande, qui est la partie qui doit être identique.
3. Pour les titres et les formes : une `QGuiApplication` **hors écran** (`QT_QPA_PLATFORM=offscreen`, déjà
   employée par `ensure_qt_gui`), sans fenêtre ni boucle d'événements.

### 8. La console dans l'application (dernière étape)

Un panneau pour écrire et lancer un script (déclaratif ou Python), qui **n'est jamais lancé à l'ouverture** :
- un script déclaratif s'exécute comme une action ordinaire, en **une** étape d'annulation, avec le rapport ;
- du Python demande une confirmation qui **affiche la source** et rappelle R3 ;
- l'exécution est annulable au sens du modèle (Annuler), pas au sens du processus : un script Python lancé dans le
  processus de l'application peut le bloquer ; il tourne donc dans un **processus fils** (le CLI) qui renvoie une liste
  d'opérations, appliquée ensuite par la session de l'application. L'application ne charge jamais de code
  utilisateur dans son propre processus.

## Options étudiées

| Option | Verdict |
| --- | --- |
| **A. Python seul** | Écartée : met la sécurité entière sur la confiance dans chaque script, rend l'usage par des outils/LLM risqué, et ne donne aucun format échangeable. |
| **B. Déclaratif seul** | Écartée : sans boucle ni condition on ne couvre pas le « très poussé » demandé ; on finirait par réinventer un langage dans le JSON, donc par y mettre des expressions (R2). |
| **C. Python dans un bac à sable** (exécution restreinte) | Écartée : un bac à sable de langage en Python n'est pas fiable ; ce serait une promesse fausse. |
| **D. Moteur de script embarqué (Lua…)** | Écartée pour l'instant : une dépendance de plus, un second langage à documenter, pour un gain de sécurité que R2 + un processus fils obtiennent déjà. |
| **E. Déclaratif sûr + Python explicite, un seul chemin d'écriture** | **Retenue.** |

## Conséquences

**Positives.**
- Le risque est décidé une fois et écrit : ce qui est une donnée n'exécute rien, et ce qui exécute a été demandé.
- Le sol commun (export pur, identifiants explicites, session) est utile **sans** le mode script : tests,
  outils de performance, file de rendu sans interface.
- Les outils et les LLM disposent d'un format sûr à produire et à valider (`validate`, `--dry-run`).
- La vidéo de présentation redevient écrivable en quelques dizaines de lignes.

**Coûts et risques.**
- **Une surface publique à tenir** : `api_version`, test des noms publics, CHANGELOG de rupture.
- **Les verbes sont du code à maintenir** : chaque nouvelle fonctionnalité du modèle doit décider si elle a un verbe.
  Atténué par la table unique `VERBS` et un test qui signale une fonction publique de modèle sans verbe ni exclusion
  motivée.
- **Identifiants explicites** : une quinzaine de fonctions de création gagnent un paramètre optionnel (`clip_id=`,
  `sequence_id=`…, `core/object_ids.py`) ; sans effet pour les appelants existants (`None` garde la génération actuelle),
  mais à tenir à jour quand une nouvelle fonction de création est ajoutée.
- **Python reste du code non confiné** (R3) : la protection est la visibilité (confirmation, source affichée, processus
  fils), pas l'isolement.
- **Écritures concurrentes** : un CLI qui modifie un `.kut` ouvert dans l'application. Voir les décisions ouvertes.

## Plan d'action (chaque étape est une PR à part, fusionnable seule)

0. **Garde R1** : le test d'interdiction d'exécution dynamique, avec sa liste blanche. Aucun risque, utile tout de suite.
1. **Export pur** : `prepare_export` et `build_export_command` publics ; `ExportEngine` les utilise ; tests inchangés. Puis
   l'exécuteur sans Qt. Test de parité : le même projet, avec un clip en flux optique, exporté par l'interface et par
   l'exécuteur, donne la même commande.
2. **Identifiants explicites** sur les fonctions de création (`core/object_ids.py`, fait), puis la **session** :
   `core/scripting/session.py` (atomicité par instantané restauré en place, `clamp_nested_clips`, une étape
   d'historique, rapport), avec des tests de parité contre l'interface.
3. **CLI minimal** : `info`, `validate`, `export` dans `main.py` avant Qt ; sortie `--json`, codes de sortie. Le
   chargeur gagne un **relevé optionnel des entrées ignorées** (`load_project(path, report=…)`) : l'interface continue
   de tolérer, mais `validate` et `apply` savent dire ce qui a été perdu.
4. **Déclaratif** : table `VERBS`, schéma JSON généré, `apply` et `--dry-run`, premiers verbes (montage, graphiques, audio :
   pistes, inserts, bus, égaliseur), fuzz du décodeur (aucune entrée ne doit sortir des bornes ni lever autre chose
   qu'une erreur de validation).
5. **Façade de lecture** : `describe()` versionné et vues immuables ; test de complétude (tout champ du modèle est
   couvert ou exclu avec une raison).
6. **Python** : `run`, `--allow-python`, aides, `api_version`.
7. **Console** dans l'application, processus fils.

## Décisions prises (2026-10-12, avec l'utilisateur)

Les cinq points laissés ouverts sont tranchés, tous sur la recommandation de l'ADR.

1. **Le déclaratif d'abord, Python ensuite.** Les étapes 0 à 5 du plan (session, CLI, scripts JSON, lecture complète)
   précèdent l'étape 6 (Python). Le JSON est sûr seul et sert les outils et les LLM ; le risque R3 n'arrive qu'après
   le chemin sûr.
2. **Le CLI est l'application elle-même** (`Kut-Studio info | validate | apply | export | run`), traité avant Qt. Une seule
   installation, un seul binaire ; le coût est un binaire lourd pour un simple `validate`.
3. **Jamais d'écriture en place par défaut.** La sortie va vers un autre fichier (`-o`) ; écrire dans l'entrée demande
   `--in-place`, et un **verrou consultatif** que l'application respecte évite qu'un script écrase un projet en cours
   d'édition.
4. **Verbes et clés en anglais** (`add_clip`, `route_track`, comme le code) ; messages d'erreur et rapports traduits
   (français, anglais, espagnol), comme le reste du produit.
5. **Médias : lecture seule, dans le dossier du script.** Les chemins se résolvent relativement au script ; sortir de son
   dossier demande `--allow-outside` explicite. Un script ne fait jamais lire un fichier arbitraire sans que l'appelant
   l'ait voulu.

## État du plan d'action

- **Étape 0 : faite** (#109) : `tests/test_no_dynamic_execution_guard.py` protège R1 (aucun `eval`, `exec`, `pickle`,
  `importlib`… dans le produit, à une exception justifiée près).
- **Étape 1 : faite** (#117 et cette PR). `core/export_engine.py` expose `check_export_request`, `export_needs_preparation`,
  `prepare_export` et `build_export_command` (que `ExportEngine` utilise : la commande est la même pour l'interface et pour
  un script) ; `core/export_runner.py` fournit `run_export`, l'exécuteur **sans Qt** : FFmpeg sous le superviseur,
  progression et annulation rendues sur le fil de l'appelant, repli CPU d'un encodeur matériel en échec (même logique que le
  moteur, factorisée dans `export_engine`), et un **fichier final atomique** (`.<nom>.<id>.partial<ext>` puis renommage). Tests :
  `tests/test_export_pipeline.py`, `tests/test_export_runner.py`.
- **Étape 2 : faite** (#122 et cette PR).
  - Identifiants explicites (`core/object_ids.py`) sur neuf fonctions de création : un script désigne ce qu'il crée, et rejoué
    il rend le même projet, identifiant par identifiant.
  - `core/scripting/session.py` : `Session.apply` et `Session.transaction` appliquent des opérations **en tout ou rien**. Un
    instantané copié en profondeur est remis **en place** si l'une est refusée (l'objet `Project` reste le même) ; le recadrage
    des séquences imbriquées (`clamp_nested_clips`, ce que `_record_history` de l'interface fait aussi) est dans la même
    transaction et rendu dans le rapport ; une transaction réussie est **une** étape d'historique, annulable et rétablissable
    (`undo`, `redo`). Les refus du modèle sont dans le rapport (index, nom, message) ; une erreur de programmation restaure le
    projet puis se propage. Tests : `tests/test_object_ids.py`, `tests/test_scripting_session.py`, dont la **parité avec
    l'interface** (même suppression dans la fenêtre et dans une session : même projet, annuler compris).
  - Constat de la parité : le panneau de bibliothèque crée à la demande l'emplacement d'un média (`library_assignments`) ;
    c'est un effet d'affichage, pas une édition, et une session n'en a pas.
- **Étape 3 : faite** (#125). `core/cli.py` (`Kut-Studio info | validate | presets | export`), traité dans `main.py` **avant Qt** ;
  `--json` (un seul objet sur la sortie standard, texte humain sur la sortie d'erreur), codes de sortie stables (0, 1, 2, 64, 130),
  Ctrl-C = annulation propre, messages traduits (`ui/i18n_cli.py`). `validate` dit ce que les chargeurs tolérants ont abandonné
  (`LoadReport` dans `core/project_io.py`). Documentation : `docs/cli.md`.
- **Étape 4 : en cours** (#131 et la suivante) : la table `VERBS` (63 verbes), le décodeur strict (`core/scripting/script.py` : version,
  champs inconnus refusés avec suggestion, bornes, alias résolus au décodage, toutes les erreurs d'un coup), le schéma JSON généré,
  `apply` (`--dry-run`, `--in-place`, `--allow-outside`, jamais d'écrasement par défaut), `validate script.json`, `schema`, un fuzz du
  décodeur. Le lot 2 ajoute le **contexte de fichiers** (`ScriptContext` : lecture seule dans le dossier du script, décision 5),
  `import_media`, les inserts de piste (égaliseur compris), l'état des pistes et les calques graphiques. Le lot 3 ajoute les pistes, les
  sous-titres, les transitions, les images clés et l'étalonnage, avec trois règles issues de la revue : bornes lues dans le modèle, verrou
  de piste respecté, genres de piste vérifiés. Documentation :
  `docs/scripting.md`. Lot 4 (étalonnage avancé) et lot 5 (fenêtres / qualifieur) : faits. Lot 6 (séquences
  imbriquées) : fait — création, renommage, format, duplication, suppression gardée, `insert_sequence_clip`, `set_active_sequence` (la séquence
  active est une donnée du projet : un script finit où il l'a laissée). Lot 7 (multicam) : fait — création d'une source depuis des médias
  (liste d'angles : le décodeur gagne le genre `LIST`), insertion, bascule d'angle, remplacement, politique audio, renommage, ajout et
  recalage d'angle ; angles désignés par `angle-N` ou nom exact. **Reste** : animation des couleurs et des effets, calques d'image.
- **Verrou consultatif : fait** (décision 3). `core/project_lock.py` : fichier `nom.kut.lock` créé atomiquement par l'application (tant
  qu'un projet est ouvert) et par `apply` (le temps d'une écriture sur un fichier existant, **avant** de lire le projet). Le propriétaire
  est identifié par PID et heure de début de processus (un PID réutilisé ne passe pas pour l'ancien) ; un verrou n'est repris que si le
  propriétaire est prouvé disparu, et le doute profite au propriétaire (autre machine, identité illisible, fichier abîmé récent). La
  fenêtre prévient sans jamais refuser d'ouvrir. Le verrou est pris **avant** de lire le projet (CLI et fenêtre), la reprise d'un verrou
  périmé est sérialisée par une garde (relecture sous la garde), et l'enregistrement écrit à travers un lien symbolique. Limite
  assumée : consultatif, il ne protège que des programmes de Kut-Studio.
- Étapes 5 à 7 : à faire, dans l'ordre.
