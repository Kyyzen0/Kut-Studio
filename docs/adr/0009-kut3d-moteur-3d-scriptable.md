# ADR-0009 : Kut-3D — un moteur 3D scriptable, intégré à la timeline

**Statut :** Proposé
**Date :** 2026-10-19
**Décideurs :** mainteneur de Kut-Studio

Objectif de l'utilisateur : **créer des films d'animation**, avec comme argument principal **des scripts Python**, et pouvoir
**intégrer très facilement des fichiers OBJ et d'autres formats 3D**, **ajouter des textures** et **animer des personnages**.
Concrètement : décrire une scène 3D (modèles texturés, personnages animés, caméra, lumières, mouvements) par un script, la
rendre image par image, et la monter dans Kut-Studio comme n'importe quel clip.
Kut-3D n'est **pas un clone de Blender** : on vise la 3D pensée pour la vidéo, pas la modélisation ni la sculpture.

## Résumé de la décision

1. **Un nouveau paquet `kut3d/`** à la racine, **indépendant de `core/`** (un test d'architecture l'impose : ni `core`, ni `ui`, ni Qt hors de `kut3d/gpu/` et de `render/images.py`). Le pont vers Kut vit dans `core/` (`core/film.py`), qui importe `kut3d` en différé. Il contient le modèle de scène,
   les importeurs de formats, les matériaux et textures, l'animation (objets, caméra, personnages), le rendu hors fenêtre et
   la façade Python.
2. **Les scripts de film sont des fichiers `.py` lancés explicitement** (R3 d'ADR-0005), jamais stockés dans un `.kut` ni
   exécutés à son ouverture. Un format déclaratif JSON pour la 3D viendra après, en suivant ADR-0005.
3. **Un point d'entrée d'import, `load_model(chemin)`**, et un registre d'importeurs par extension : lecteurs maison
   (Python + numpy) pour OBJ+MTL, PLY, STL, glTF/GLB ; extras optionnels pour FBX, DAE, 3MF.
4. **Textures** : matériaux PBR, UV et `.mtl` lus à l'import, rechargement à chaud, textures procédurales, et texture animée
   issue d'un clip Kut (décision 4).
5. **Animation de personnages** : glTF/GLB comme format central ; squelette, poids de peau, formes de visage et clips
   d'animation dans le modèle de données dès la phase 1 ; on importe des personnages déjà rigués, on ne les crée pas
   (décision 5).
6. **Intégration timeline** : un clip « scène 3D » référence un fichier `.kut3d` (scène JSON versionnée) ; le rendu produit
   des images, avec passes alpha et profondeur, que Kut compose comme tout autre clip. Cela exige un `.kut` v18 et sa migration.
7. **Moteur de rendu : provisoire.** Trois pistes (QRhi hors fenêtre, OpenGL hors écran via Qt, rasteriseur logiciel numpy),
   à trancher par le spike de la phase 0 (`experiments/kut3d-spike/RESULTS.md`) ; phase 0 livrée (voir décision 7).

## Contexte

Ce que le code permet déjà, vérifié le 2026-10-19 :

| Constat | Où | Conséquence |
| --- | --- | --- |
| Kut-Studio est un éditeur vidéo Python 3.11+ / PySide6 `>=6.6,<6.12`, FFmpeg externe ; le format projet `.kut` est du JSON en **version 17** | `requirements.txt`, `core/project_io.py` (`CURRENT_VERSION = 17`) | Ajouter un type de clip demande une version 18 et une migration. |
| **Aucun code 3D** : ni maillage, ni caméra, ni Qt3D, ni three.js | recherche dans le dépôt ; `kut3d/` n'existe pas | On part de zéro : tout est à définir, rien à défaire. |
| Un moteur GPU QRhi existe déjà, centré sur le moniteur | `ui/gpu_preview.py` (`QRhiWidget`), `core/gpu_backend.py`, `core/gpu_composite.py`, `core/gpu_effects.py`, shaders `assets/shaders/*.frag.qsb` | Une base pour le rendu GPU, mais liée à une fenêtre ; son usage hors fenêtre est ce que le spike de la phase 0 a mesuré (décision 7). |
| L'animation est générique : courbes à images clés et six interpolations (`hold`, `linear`, `ease_in`, `ease_out`, `ease_in_out`, `bezier`) | `core/animation.py` (`AnimationCurve`, `InterpolationType`), `core/animation_targets.py` | Réutilisable tel quel pour animer position, rotation, focale, intensité de lumière. |
| Le rendu d'image existe en partie hors de l'interface, mais l'image composée est assemblée dans `ui/` | `core/cpu_composite.py` (`compose_frame`) ; `CompositeFrame` est défini dans `core/gpu_composite.py` et **construit dans `ui/preview_panel.py:1369-1373`** | Le chemin « rendre une image sans fenêtre » n'est pas complet dans `core/` : Kut-3D ne peut pas s'y brancher sans le compléter ou le contourner. |
| Le mode script est décidé, la façade Python ne l'est pas encore livrée | ADR-0005 (R1 : un `.kut` ne s'exécute jamais ; R3 : le Python ne tourne que sur demande explicite) ; les verbes déclaratifs sont dans `core/scripting/` (`verbs.py`, `session.py`, `script.py`) ; **`core/scripting/api.py` n'existe pas** (étapes 5 à 7 du plan d'ADR-0005 à faire) | La façade Python de Kut-3D ne peut s'appuyer sur celle d'ADR-0005 qu'une fois celle-ci livrée. |
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

`kut3d/` n'importe ni `core/` ni `ui/` : c'est un test d'architecture (`tests/test_kut3d_model.py`), parce que `core/` n'est pas
indépendant de Qt. Le pont est dans `core/` (`core/film.py`, `core/film_render.py`), qui n'importe `kut3d` qu'à l'intérieur de ses
fonctions : `import core.film` reste léger. *Correction : la première version de cet ADR disait l'inverse.* Sous-modules :

- **`model`** : `Node`, `Mesh` (positions, normales, UV), `Material`, `Skeleton`, `MorphTarget`, `AnimationClip`.
  Objets **immuables** (phase 1 livrée ; la sérialisation viendra avec le fichier `.kut3d`, phase 3).
- **`scene`** : `Camera`, lumières (ambiante, directionnelle, ponctuelle), `Instance`, `Scene` (phase 2a livrée). Écart avec le
  premier découpage de cet ADR, qui rangeait caméra et lumières dans `model` : elles décrivent une mise en scène, pas un
  modèle importé.
- **`importers`** : registre par extension (§3).
- **`animation`** : évalue aujourd'hui les canaux d'un clip glTF (pose, matrices d'articulation, poids de formes) **sans
  importer `core/`**, qui n'est pas indépendant de Qt. La réutilisation de `core.animation.AnimationCurve` (un seul système de
  courbes) est maintenue pour les images clés écrites par l'utilisateur, avec le mélange de clips (décision 5, phase 4).
- **`render`** : rendu **hors fenêtre**, image par image, avec des passes couleur, alpha et profondeur ; moteur de référence
  numpy et registre de moteurs (phase 2a livrée).
- **`gpu`** : moteur QRhi (phase 2b livrée). C'est, avec le décodage JPEG de `render/images.py`, le seul endroit où `kut3d/`
  importe Qt, sous liste blanche vérifiée par un test d'architecture.
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
- **Confort** : textures et `.mtl` retrouvés automatiquement (décision 4), matériau gris par défaut quand il manque,
  rechargement à chaud quand le fichier source change, glisser-déposer dans l'application (nouveau type de `MediaAsset`, « modèle 3D »).
- Les entrées sont bornées (taille de fichier, nombre de sommets et de faces) avant allocation, et une erreur n'en recopie
  pas le contenu.

### 4. Les textures

- **Matériaux PBR** : couleur de base, normales, rugosité / métal, émission, occlusion ; les canaux manquants prennent des
  valeurs par défaut explicites, jamais une correction silencieuse.
- **UV et `.mtl`** sont lus à l'import (OBJ+MTL, glTF) ; les chemins de textures restent confinés au dossier du modèle.
- **Rechargement à chaud** d'une image modifiée sur le disque.
- **API script** du genre `mesh.material.texture("base_color", "bois.png")`.
- **Textures procédurales** générées par numpy (bruit, damier, dégradés), sans fichier image.
- **Texture animée issue d'un clip ou d'une séquence Kut** : la timeline vidéo sert de texture à un objet 3D (écran de
  télévision, façade, projection). C'est un atout propre à l'écosystème : la texture est évaluée à l'image courante du film.
- **Hors périmètre initial** : peinture de texture 3D, dépliage UV automatique (un modèle sans UV garde un matériau uni).
- Les images sont bornées en dimensions avant allocation ; le volume mémoire des textures est un risque suivi (voir Risques).

### 5. L'animation de personnages

- **glTF / GLB est le format central** : OBJ, PLY et STL n'ont ni squelette ni animation.
- **Le modèle de données inclut dès la phase 1** : squelette (hiérarchie d'os), poids de peau (**4 influences par sommet**),
  formes de visage (*morph targets*) et clips d'animation, même si le rendu et la lecture arrivent plus tard.
- **Lecture et mélange de clips** : `personnage.play("marche", loop=True)`, fondu enchaîné entre clips.
- **Contrôle des os par courbes** : les os se pilotent avec `core/animation.py` (`AnimationCurve`), pas de second système.
- **Déformation par os** : sur **CPU (numpy)** comme référence de test, sur **GPU (vertex shader)** pour la production, selon
  la même logique que le choix du moteur (décision 7).
- **Plus tard** : visage et synchronisation labiale à partir de l'audio de Kut, retargeting, IK, import BVH.
- **Limite assumée** : la **création d'un personnage de zéro** (modélisation, rig, peinture des poids) est **hors périmètre**.
  On importe des personnages déjà rigués (glTF, FBX, VRM) et on les anime et les met en scène.

### 6. L'intégration à la timeline

Un clip « scène 3D » est un `Clip` qui référence un fichier **`.kut3d`** : une scène JSON, versionnée, qui suit les mêmes
règles de rigueur que le `.kut` (version plus récente refusée, champs invalides refusés, aucun champ interprété comme du code).
Cela exige un **`.kut` v18** avec une migration depuis la v17. Le rendu produit des images (couleur, alpha, profondeur) que
Kut compose comme celles de n'importe quel clip : l'aperçu reste l'export.

### 7. Le moteur de rendu : orientation donnée par le spike, à valider sur Windows et Linux

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

**Mesuré en phase 2** (macOS / Metal, 27 scènes de comparaison) : le moteur `qrhi` s'accorde avec la référence numpy sur 99,7 à
100 % des pixels à ±6 niveaux ; en 720p avec 20 000 triangles, il rend une image en environ 26 ms contre environ 550 ms. La
couleur et la profondeur sortent de la même passe, dans deux cibles flottantes lues en retour : la profondeur est exacte, sans
empaquetage. Un `backend="auto"` retombe sur numpy quand une sonde, lancée dans un sous-processus avec délai maximal, ne trouve
pas de GPU utilisable. Sur la CI, les tests de rendu GPU ne tournent que sur demande (`KUT3D_GPU_TESTS=1`), car un runner peut
exposer un GPU paravirtualisé. **Windows et Linux restent non testés** : l'orientation provisoire ci-dessus tient pour macOS.

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
- **Peau et cheveux.** Un personnage crédible dans un film exige un rendu de peau (diffusion sous la surface) et de cheveux
  bien au-delà de l'ombrage PBR de base ; à traiter comme un chantier à part, avec une première version volontairement simple.
- **Déformation par os non mesurée.** Le spike ne couvrait que le cube : le coût et la fidélité de la déformation par os
  (vertex shader QRhi, passage des matrices d'os, comparaison à la référence numpy) restent à mesurer avant de s'y engager.
- **FBX : dépendance et licence.** Beaucoup de personnages gratuits sont en FBX, mais son importeur passe par une dépendance
  (`assimp` ou `trimesh`) dont la licence et la distribution restent à vérifier.
- **Licences des personnages et animations importés.** Un modèle ou une animation téléchargés ont leur propre licence
  (usage commercial, attribution, redistribution) ; Kut-Studio ne les vérifie pas, l'utilisateur en est responsable.
- **Volume mémoire des textures.** Textures 4K multiples, par objet et par image : borner les dimensions, mutualiser les
  images identiques, libérer les textures hors plan.
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

0. **Cet ADR + spike de rendu hors fenêtre** (`experiments/kut3d-spike/`) : **livrée** ; résultats en décision 7.
1. **Modèle de scène + importeurs** : UV, matériaux, squelette, peau, formes de visage, clips d'animation ; importeurs OBJ,
   PLY, STL, glTF / GLB ; normalisation ; tests sur des fichiers générés et mal formés. **Livrée.**
2. **Rendu image par image** : caméra, lumières, ombrage PBR avec textures, déformation par os (CPU numpy + shader GPU),
   passes alpha et profondeur. **Livrée** en 2a (référence numpy) et 2b (moteur QRhi). **Reporté** : ombres portées, flou de
   mouvement, éclairage par image (IBL), mipmaps ; à planifier avant le premier film, car la qualité d'image en dépend.
3. **Façade Python + clip « scène 3D »**, en deux tranches :
   - **3A. Façade `Film`** : **livrée.** `core/film.py` rend une scène en vidéo ProRes 4444 avec transparence, la pose dans un
     projet Kut au-dessus d'un fond, ajoute de l'audio, sauve le `.kut` et exporte un MP4. **Aucun changement du format `.kut`**
     (le fichier reste à la version qu'impose son contenu). Doc : [kut3d-film.md](../kut3d-film.md).
   - **3B. Vrai clip « scène 3D »** (fichier `.kut3d`, nouvelle version du format projet : la **22**, pas la 18, les versions 18 à 21
     étant prises par d'autres fonctions ; migration, plan de rendu, aperçu) puis verbes de script et interface. **B2a livré** (modèle
     et format, pas encore de rendu) ; **à décider** pour la suite : voir les décisions ouvertes 9 et 10.
   - **3C. Formes de base paramétriques** (décision du propriétaire, 2026-10-20 ; ordre convenu : formes de base, puis texte 3D
     extrudé, puis création dans l'application avec une page « 3D ») : **livrée** pour les formes de base. Cube, sphère, plan, cylindre,
     cône et tore se décrivent par leurs PARAMÈTRES (genre, dimensions, tessellation, matériau), jamais par des sommets : le `.kut3d`
     passe en **version 2** seulement s'il en contient (un fichier sans forme reste identique, un lecteur ancien refuse le nouveau
     proprement). La géométrie est générée par des fonctions pures de `kut3d/` (normales, UV glTF, sens direct vu de l'extérieur) et
     se rend comme n'importe quel modèle, en numpy comme en QRhi. Écart assumé avec « pas de modélisation » : ce sont des primitives
     décrites, pas de l'édition de maillage. **Plafonds** (entrée non fiable, vérifiés avant de générer) : dimensions dans
     [1e-6, 1e6], 3 à 256 segments, 2 à 256 anneaux, 1 à 256 subdivisions, **65 536 triangles par forme**, **500 000 triangles pour
     l'ensemble des formes d'un fichier**. Les textures d'une forme ne s'écrivent pas encore dans le fichier (refusées, jamais perdues).
     Doc : [kut3d-scene.md](../kut3d-scene.md#formes-de-base-format-2), [kut3d-film.md](../kut3d-film.md).
   - **3D. Texte 3D extrudé** (deuxième étape de l'ordre convenu) : **livré.** Une forme de base de genre `text` (texte, famille,
     taille, profondeur, biseau à 45 degrés, alignement, matériau) décrite par ses paramètres, **sans fichier de police ni de modèle** ;
     le `.kut3d` passe en **version 3** seulement s'il contient un texte (sans texte : octets inchangés, versions 1 et 2 comprises). Les
     contours viennent de Qt (`QRawFont`), aplatis par nos soins, triangulés par découpage en oreilles avec ponts pour les trous
     (numpy, aucune dépendance), maillage fermé, normales sortantes. **Déterminisme** : les familles livrées (Anton, Saira) sont lues
     dans leurs fichiers, mêmes contours sur toutes les machines (aux arrondis de la bibliothèque de polices près) ; une famille installée est propre à la machine et refusée si absente (jamais
     de substitution). **Plafonds** (avant de générer) : 40 caractères, 160 points de contour par caractère, majorant de triangles
     compté dans les 65 536 par forme et les 500 000 par fichier ; emoji, idéogrammes, écritures complexes, caractères de contrôle et
     combinants refusés avec leur code. Limites connues : pas de crénage, contours fusionnés décimés pour les polices à contours qui se
     recouvrent, biseau refusé si les traits sont plus fins (≤ 1 % de la taille). Doc : [kut3d-scene.md](../kut3d-scene.md#texte-3d-extrude-format-3).
4. **Animation**, en trois temps :
   - **4a** lecture de clips d'animation, animation d'objets et de caméra par courbes ; importeur **FBX** optionnel
     (`assimp` ou `trimesh`, licence à vérifier), ou conversion préalable en glTF ;
   - **4b** mélange de clips et contrôle des os par script ;
   - **4c** visage, synchronisation labiale, IK, retargeting.
5. **Interface** : glisser-déposer, viewport, rechargement à chaud.
6. **Importeurs avancés** (USD), plugins, bibliothèque d'assets.

Les **phases 0 à 3 suffisent** à réaliser un premier court-métrage entièrement scripté, avec des modèles texturés ; les
**personnages animés arrivent en phase 4**.

## Décisions ouvertes

1. **Application séparée ou module intégré ?** Cet ADR propose le module intégré (un clip de la timeline) ; un exécutable
   séparé reste possible si le poids des dépendances l'exige.
2. **Plateformes visées** : macOS d'abord, puis Windows et Linux ? Le critère « sans fenêtre partout » du spike en dépend.
3. **Moteur de rendu final** : à trancher d'après `experiments/kut3d-spike/RESULTS.md` (décision 7).
4. **Politique de licence des extras** (`trimesh`, `assimp`) : compatibilité avec la distribution, extras optionnels ou
   embarqués.
5. **Stratégie FBX** : importeur optionnel (`assimp` ou `trimesh`, phase 4a) ou conversion préalable en glTF par l'utilisateur ?
6. **Frontière « pas de création de rig »** : à confirmer. Kut-Studio importe et anime des personnages déjà rigués, mais ne
   propose ni modélisation, ni rig, ni peinture des poids.
7. **Ordre vis-à-vis de l'étape 5 d'ADR-0005** (façade de lecture `describe()`) : la phase 3 de cet ADR la suppose ; faut-il
   la livrer avant, ou en parallèle avec une façade minimale côté `kut3d/` ? *Tranchée pour la tranche 3A : une façade minimale côté `core/film.py`, sans attendre ADR-0005.*
8. **Format `.kut` 22** (tranche 3B) : un projet qui contient un clip « scène 3D » ne s'ouvrirait plus dans une version antérieure,
   comme pour les versions 19 à 21. Les projets sans clip 3D restent écrits à l'identique. *Tranchée par le propriétaire du projet ; modèle et format livrés au lot B2a (`GraphicType.SCENE3D`, `SCENE3D_VERSION`, `core/scene3d.py`), rendu au lot B2b.*
9. **Aperçu d'un clip « scène 3D »** : une image figée et approximative dans le moniteur, avec les segments fidèles par-dessus
   (comme les calques graphiques), ou un rendu 3D en direct dans la boucle de lecture ? La première option réutilise l'existant.
10. **Packaging** : embarquer les shaders `.qsb` (`build.py`) et corriger la sonde GPU, qui lance `sys.executable -m
    kut3d.gpu.probe_worker`, inutilisable dans l'application empaquetée (repli silencieux sur numpy). À faire avant qu'un clip
    3D soit visible dans une release.
