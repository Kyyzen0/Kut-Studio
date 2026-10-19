# ADR-0007 : Kut-3D — un moteur 3D scriptable, intégré à la timeline

**Statut :** Proposé
**Date :** 2026-10-19
**Décideurs :** mainteneur de Kut-Studio

Objectif de l'utilisateur : **créer des films d'animation**, avec comme argument principal **des scripts Python**, et pouvoir
**intégrer très facilement des fichiers OBJ et d'autres formats 3D**. Concrètement : décrire une scène 3D (modèles, caméra,
lumières, mouvements) par un script, la rendre image par image, et la monter dans Kut-Studio comme n'importe quel clip.
Kut-3D n'est **pas un clone de Blender** : on vise la 3D pensée pour la vidéo, pas la modélisation ni la sculpture.

## Résumé de la décision

1. **Un nouveau paquet `kut3d/`** à la racine, qui importe `core/` et jamais l'inverse. Il contient le modèle de scène,
   les importeurs de formats, l'animation, le rendu hors fenêtre et la façade Python.
2. **Les scripts de film sont des fichiers `.py` lancés explicitement** (R3 d'ADR-0005), jamais stockés dans un `.kut` ni
   exécutés à son ouverture. Un format déclaratif JSON pour la 3D viendra après, en suivant ADR-0005.
3. **Un point d'entrée d'import, `load_model(chemin)`**, et un registre d'importeurs par extension : lecteurs maison
   (Python + numpy) pour OBJ+MTL, PLY, STL, glTF/GLB ; extras optionnels pour FBX, DAE, 3MF.
4. **Intégration timeline** : un clip « scène 3D » référence un fichier `.kut3d` (scène JSON versionnée) ; le rendu produit
   des images, avec passes alpha et profondeur, que Kut compose comme tout autre clip. Cela exige un `.kut` v18 et sa migration.
5. **Moteur de rendu : provisoire.** Trois pistes (QRhi hors fenêtre, OpenGL hors écran via Qt, rasteriseur logiciel numpy),
   à trancher par le spike de la phase 0 (`experiments/kut3d-spike/RESULTS.md`).

## Contexte

Ce que le code permet déjà, vérifié le 2026-10-19 :

| Constat | Où | Conséquence |
| --- | --- | --- |
| Kut-Studio est un éditeur vidéo Python 3.11+ / PySide6 `>=6.6,<6.12`, FFmpeg externe ; le format projet `.kut` est du JSON en **version 17** | `requirements.txt`, `core/project_io.py` (`CURRENT_VERSION = 17`) | Ajouter un type de clip demande une version 18 et une migration. |
| **Aucun code 3D** : ni maillage, ni caméra, ni Qt3D, ni three.js | recherche dans le dépôt ; `kut3d/` n'existe pas | On part de zéro : tout est à définir, rien à défaire. |
| Un moteur GPU QRhi existe déjà, centré sur le moniteur | `ui/gpu_preview.py` (`QRhiWidget`), `core/gpu_backend.py`, `core/gpu_composite.py`, `core/gpu_effects.py`, shaders `assets/shaders/*.frag.qsb` | Une base pour le rendu GPU, mais liée à une fenêtre ; son usage hors fenêtre est précisément ce que le spike mesure. |
| L'animation est générique : courbes à images clés et six interpolations (`hold`, `linear`, `ease_in`, `ease_out`, `ease_in_out`, `bezier`) | `core/animation.py` (`AnimationCurve`, `InterpolationType`), `core/animation_targets.py` | Réutilisable tel quel pour animer position, rotation, focale, intensité de lumière. |
| Le rendu d'image existe en partie hors de l'interface, mais l'image composée est assemblée dans `ui/` | `core/cpu_composite.py` (`compose_frame`) ; `CompositeFrame` est défini dans `core/gpu_composite.py` et **construit dans `ui/preview_panel.py:1369-1373`** | Le chemin « rendre une image sans fenêtre » n'est pas complet dans `core/` : Kut-3D ne peut pas s'y brancher sans le compléter ou le contourner. |
| Le mode script est décidé, la façade Python ne l'est pas encore livrée | ADR-0005 (R1 : un `.kut` ne s'exécute jamais ; R3 : le Python ne tourne que sur demande explicite) ; 72 verbes déclaratifs dans `core/scripting/` (`verbs.py`, `session.py`, `script.py`) ; **`core/scripting/api.py` n'existe pas** (étapes 5 à 7 du plan d'ADR-0005 à faire) | La façade Python de Kut-3D ne peut s'appuyer sur celle d'ADR-0005 qu'une fois celle-ci livrée. |
| Les plans 3D actuels des productions sont rendus par **Blender externe**, puis assemblés par `core` | `productions/halloween_2026/build.py`, `productions/halloween_2026/blender/` (scripts `bpy`) | Kut-3D remplacerait cette brique externe ; c'est le premier cas d'usage réel. |
| Architecture : `ui/` importe `core/`, jamais l'inverse ; FFmpeg uniquement via le superviseur | `docs/architecture.md`, `core/process_supervisor.py` | `kut3d/` s'insère sous `ui/`, à côté de `core/` ; il ne lance jamais FFmpeg lui-même. |
| `core/` n'est pas totalement indépendant de Qt | 36 fichiers de `core/` référencent PySide6 | Un rendu « sans Qt » ne peut pas se reposer sur n'importe quel module de `core/`. |

## Options considérées

| Option | Verdict |
| --- | --- |
| **A. Web / three.js dans QtWebEngine** | Écartée : une deuxième pile technique (JavaScript, navigateur embarqué), un rendu qui ne partage rien avec le moteur de composition, et un export image par image difficile à rendre déterministe. |
| **B. Piloter Blender par `bpy`** | Écartée : dépendance lourde (centaines de Mo), licence GPL à mesurer pour la distribution, et ce n'est plus « notre » outil : le script d'un film serait un script Blender. |
| **C. Moteur natif C++** | Écartée : coût élevé et hors du reste du projet, en Python ; voir ADR-0003 et ADR-0004 pour la frontière native déjà étudiée. |
| **D. Paquet Python `kut3d/`, modèle de scène propre, rendu choisi par spike** | **Retenue.** Même langage que le produit, même chemin d'animation, façade Python naturelle. |

## Décision

### 1. Un paquet `kut3d/`, au-dessus de `core/`

`kut3d/` importe `core/` ; `core/` n'importe jamais `kut3d/`. `ui/` peut importer `kut3d/`. Sous-modules :

- **`model`** : `Scene`, `Node`, `Mesh`, `Camera`, `Light`, `Material`. Objets **immuables et sérialisables**.
- **`importers`** : registre par extension (§3).
- **`animation`** : réutilise `core.animation.AnimationCurve` et ses interpolations ; pas de second système de courbes.
- **`render`** : rendu **hors fenêtre**, image par image, avec des passes couleur, alpha et profondeur.
- **`api`** : la façade Python publique.

### 2. Les scripts

Un script de film est un fichier `.py` que l'utilisateur lance exprès (R3 d'ADR-0005). Il n'est **jamais** stocké dans un
`.kut` ni exécuté à son ouverture (R1). La façade Python de Kut-3D s'appuie sur celle d'ADR-0005, qui doit donc être livrée
d'abord. Un format déclaratif JSON pour la 3D (verbes fermés, aucune expression) vient ensuite, selon les règles d'ADR-0005 :
il est la forme sérialisée de la liste d'opérations que produit le script Python.

### 3. L'import des formats 3D

- **Un seul point d'entrée** : `load_model(chemin)`, qui choisit l'importeur dans un registre par extension.
- **Lecteurs maison** (Python + numpy, sans dépendance supplémentaire) : **OBJ + MTL, PLY, STL, glTF / GLB**.
- **Extras optionnels** (`trimesh` ou `assimp`) : **FBX, DAE, 3MF**. USD et Alembic plus tard.
- **Normalisation automatique** : axe haut (Y ou Z), unités, centrage, mise à l'échelle.
- **Confort** : textures et `.mtl` retrouvés automatiquement, matériau gris par défaut quand il manque, rechargement à chaud
  quand le fichier source change, glisser-déposer dans l'application (nouveau type de `MediaAsset`, « modèle 3D »).
- Les entrées sont bornées (taille de fichier, nombre de sommets et de faces) avant allocation, et une erreur n'en recopie
  pas le contenu.

### 4. L'intégration à la timeline

Un clip « scène 3D » est un `Clip` qui référence un fichier **`.kut3d`** : une scène JSON, versionnée, qui suit les mêmes
règles de rigueur que le `.kut` (version plus récente refusée, champs invalides refusés, aucun champ interprété comme du code).
Cela exige un **`.kut` v18** avec une migration depuis la v17. Le rendu produit des images (couleur, alpha, profondeur) que
Kut compose comme celles de n'importe quel clip : l'aperçu reste l'export.

### 5. Le moteur de rendu : orientation donnée par le spike, à valider sur Windows et Linux

Le spike de la phase 0 (`experiments/kut3d-spike/RESULTS.md`) a été exécuté **sur macOS uniquement** : la voie numpy, QRhi (Metal)
et OpenGL 4.1 hors écran rendent le même cube (99,997 % de pixels à ±12 niveaux). Orientation retenue, **provisoire** :
**numpy comme référence et oracle de CI, QRhi comme moteur de production**, OpenGL hors écran en plan B seulement. Points à connaître :
l'API QRhi hors fenêtre n'est pas utilisable telle quelle en Python (`beginOffscreenFrame` exige un `QRhiCommandBuffer`
non instanciable), le spike passe par un `QRhiWidget` jamais affiché ; QRhi et OpenGL n'ont fonctionné qu'avec la plateforme Qt
`cocoa`, pas `offscreen` ni `minimal`. Windows et Linux restent à mesurer avant de figer ce choix. Candidats examinés :

- **(a)** QRhi hors fenêtre (réutilise l'acquis de `core/gpu_backend.py`) ;
- **(b)** OpenGL hors écran via Qt ;
- **(c)** un **rasteriseur logiciel numpy**, lent mais servant de **référence testable en CI**.

**Critères de choix** : fonctionne **sans fenêtre** sur macOS, Windows et Linux ; **déterministe** (même scène, mêmes pixels, à
une tolérance près) ; **testable en CI**. Rien n'interdit de garder (c) comme référence et un moteur GPU pour la vitesse.

## Conséquences

**Positives.**
- Un film d'animation devient scriptable de bout en bout, sans quitter l'écosystème : modèle, rendu, montage, export.
- Le pipeline de `productions/halloween_2026/` n'a plus besoin de Blender.
- Le modèle de scène immuable et le format `.kut3d` donnent des tests de rendu précis et des scripts rejouables.
- L'animation 3D réutilise l'acquis (`AnimationCurve`, cibles d'animation) au lieu de le dupliquer.

**Coûts.**
- Un paquet de plus à maintenir, une surface publique de plus (`api_version`, noms publics figés, comme ADR-0005).
- Un `.kut` v18 et sa migration ; un format `.kut3d` à versionner.
- Des importeurs à écrire, à fuzzer et à tenir à jour (OBJ, glTF : formats parfois mal formés).

## Risques

- **Périmètre.** « Autant que Blender » est irréaliste. On vise le **sous-ensemble utile au film d'animation** (modèles importés,
  caméra, lumières, matériaux simples, texte 3D, mouvements) ; modélisation, sculpture, simulation et nœuds de matériaux
  restent hors champ.
- **Qualité du rendu.** Un rendu temps réel reste en retrait d'un path tracer : ombres, anti-crénelage et flou de mouvement
  sont requis pour un film, et ne sont pas gratuits.
- **Performance du Python sur de gros maillages.** Numpy d'abord ; mesurer sur des modèles réels avant d'optimiser.
- **Plafond PySide6.** Le moteur dépend de `PySide6 <6.12` (voir `requirements.txt`) ; le rendu hors fenêtre via Qt hérite de
  ses limites et de ses régressions.
- **`core/` n'est pas indépendant de Qt** (36 fichiers référencent PySide6) : le rendu ne doit importer que les modules de
  `core/` dont l'indépendance est vérifiée.
- **Exigences de la CI.** `ruff`, `mypy` (le paquet `kut3d/` devra y entrer, sans allonger le cliquet de dette), cliquet de
  couverture à 86 %, gardes de structure (limite de 700 lignes par module). Un rendu GPU est mal testable sur des
  machines de CI sans carte graphique : d'où l'intérêt de la référence logicielle.
- **Sécurité des fichiers importés.** Un modèle 3D est une entrée non fiable : bornes de taille, chemins de textures confinés au
  dossier du modèle (même esprit que la décision 5 d'ADR-0005), aucune exécution.

## Phasage

Chaque phase est une PR (ou plusieurs) fusionnable seule.

0. **Cet ADR + spike de rendu hors fenêtre** (`experiments/kut3d-spike/`) : mesurer les trois pistes du §5.
1. **`model` + `importers`** : OBJ, PLY, STL, glTF ; normalisation ; tests sur des fichiers réels et mal formés.
2. **Rendu image par image** : caméra, lumières, matériaux, passes alpha et profondeur.
3. **Façade Python + clip « scène 3D »** (`.kut` v18, migration, fichier `.kut3d`).
4. **Animation 3D, texte 3D, caméra animée.**
5. **Interface** : glisser-déposer, viewport, rechargement à chaud.
6. **Importeurs avancés** (FBX, DAE, 3MF, USD), plugins.

Les **phases 0 à 3 suffisent** à réaliser un premier court-métrage entièrement scripté.

## Décisions ouvertes

1. **Application séparée ou module intégré ?** Cet ADR propose le module intégré (un clip de la timeline) ; un exécutable
   séparé reste possible si le poids des dépendances l'exige.
2. **Plateformes visées** : macOS d'abord, puis Windows et Linux ? Le critère « sans fenêtre partout » du spike en dépend.
3. **Moteur de rendu final** : à trancher d'après `experiments/kut3d-spike/RESULTS.md`.
4. **Politique de licence des extras** (`trimesh`, `assimp`) : compatibilité avec la distribution, extras optionnels ou
   embarqués.
5. **Ordre vis-à-vis de l'étape 5 d'ADR-0005** (façade de lecture `describe()`) : la phase 3 de cet ADR la suppose ; faut-il
   la livrer avant, ou en parallèle avec une façade minimale côté `kut3d/` ?
