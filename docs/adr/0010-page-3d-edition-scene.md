# ADR-0010 : la page « 3D » de Kut-3D, édition d'une scène dans l'application

**Statut :** Proposé le 2026-10-21. Le **lot 0** (le document éditable, sans interface) est livré avec cet ADR et sert à valider le modèle d'édition ; aucun widget ni page n'existe encore.
**Date :** 2026-10-21
**Décideurs :** mainteneur de Kut-Studio
**Suite de :** [ADR-0009](0009-kut3d-moteur-3d-scriptable.md) (moteur 3D scriptable), [ADR-0002](0002-socle-nodal-etalonnage-composition-pages.md) (pages Couleur et Composition), [ADR-0006](0006-composition-visuelle-progressive.md) (niveaux d'usage).

Demande de l'utilisateur (2026-10-20) : une page dédiée **« 3D »** à côté de Montage, Composition, Audio et Couleur, pour **créer et modifier une scène 3D dans l'application** (aujourd'hui une scène s'écrit par script ou à la main, et l'application la lit seulement : l'inspecteur d'un clip 3D ne fait que « Recharger », `ui/scene3d_inspector.py:1-6`). Il veut le résultat, pas une liste de questions : chaque point ci-dessous **tranche**, avec une recommandation chiffrée et les alternatives écartées.

Les faits cités sont relevés dans le code le 2026-10-21 (références vérifiées). Les mesures viennent de `python -m tools.perf.kut3d_pick_bench` (Apple M4, macOS, QRhi/Metal) et de la session du prototype ; rien ici n'est un seuil de test (les tests portent sur des identités, des octets et des ratios, jamais sur des durées).

---

## Résumé de la décision

1. **Modèle d'édition : des états immuables reliés par des commandes pures** (`DocumentState`, `kut3d.document_commands`, `SceneDocument`). Annuler = revenir à l'état précédent ; les maillages sont **partagés**, jamais copiés. Historique **propre à la page** (100 éditions), pas dans l'historique du projet. *Écartés :* commandes avec inverse, instantanés du `Project`, `Scene` mutable.
2. **Identités :** `o<N>` (objet) et `l<N>` (lumière), donnés à la création, jamais réutilisés dans une session, **absents du fichier** (le format `.kut3d` ne change pas). Le **nom** est une étiquette libre, non unique, jamais corrigée en silence.
3. **Viewport : QRhi pour l'image, numpy pour la sélection.** L'image interactive vient du moteur GPU existant (33 ms pour 455 168 triangles en 640x360, contre 1 034 ms en numpy : ×31) ; la **sélection par rayon** se fait en numpy sur la géométrie déjà assemblée (26 ms sans tri, **3,3 ms** avec tri par boîtes englobantes, soit 0,3 % d'un rendu numpy). Les **gizmos** sont dessinés en 2D (QPainter) et testés analytiquement. Pas de tampon d'identifiants GPU.
4. **« Moniteur fidèle d'abord » préservé :** le moniteur de la timeline et l'export restent en numpy (`core.scene3d.RENDER_BACKEND`). Le viewport est une surface d'**édition**, étiquetée « Aperçu approximatif » ; il s'accompagne d'un **rendu numpy fidèle** déclenché à l'arrêt (anti-rebond 400 ms, fil de travail).
5. **Lien avec le clip : fichier `.kut3d` externe, jamais embarqué.** La page édite le fichier du clip ; **« Appliquer »** l'écrit de façon atomique, ce qui change sa signature (`core.scene3d.scene_signature`) et renouvelle les caches tout seul. « Recharger » reste pour les modifications externes ; un conflit (fichier changé depuis la lecture) est refusé, jamais écrasé en silence. **Aucun changement de format `.kut` (reste 22+) ni `.kut3d` (reste 1 à 3).**
6. **Niveau d'usage : Studio** (celui de « Scène 3D… » dans le menu Calques, `menu_levels.py:53`) ; les réglages techniques (plans proche et lointain, tessellation, mode alpha, émission, animation) sont réservés à Avancé, **construits à la création** (jamais d'emballage après coup).
7. **Plan en 9 lots** indépendamment fusionnables (§ « Plan de livraison »), le lot 0 étant cette PR.
8. **Hors périmètre :** animation de personnages, FBX, ombres, textures de formes de base dans le fichier, hiérarchie d'objets, images clés d'objets de scène (question ouverte, § « Hors périmètre »).

---

## Contexte : ce qui existe

- **`Scene` est immuable** (`kut3d/scene.py`) : `Instance`, lumières, `Camera` sont `frozen`, leurs tableaux numpy en lecture seule, validés à la construction (NaN, infini, bornes). Plafonds : `MAX_INSTANCES = 2048`, `MAX_LIGHTS = 16` (`scene.py:22-23`) ; fichier : `MAX_MODELS = 256`, 16 Mio, 65 536 triangles par forme et **500 000 par fichier** (`scene_io_fields.py`, `primitives.py:50-53`).
- **Deux instances peuvent partager un `Model`**, et `save_scene` l'écrit **une fois** (identité de l'objet, `scene_io_write.py:_document_and_models`). Les identifiants de modèles du fichier sont **recalculés à l'écriture** (`_unique_id`, du genre ou du nom du fichier source) ; les identifiants d'instances n'existent pas : seul `Instance.name` est écrit.
- **Le fichier est déterministe** (clés triées, ASCII, `\n`) et `save_scene` relit ce qu'il écrit avec les validateurs du lecteur (`checked_bytes`) : on n'écrit jamais ce que `load_scene` refuserait. `save_scene` refuse un modèle sans fichier source qui n'est pas une forme de base, un modèle hors du dossier, et une forme dont le maillage a été modifié (`pristine_material`).
- **Un clip 3D est un calque graphique** `GraphicType.SCENE3D` dont `source_path` désigne le `.kut3d` (`core/graphics.py:23,57`, `.kut` v22). Le rendu (export, aperçu fidèle) est **toujours numpy** (`RENDER_BACKEND`, `core/scene3d.py:65`) ; la **clé de cache** contient la signature des fichiers (`scene_signature`, `scene3d.py:186` : `stat` de chaque fichier + empreinte de ses 8 Kio de début et de fin, mémorisée et revalidée par `stat`). `reload_scene(path)` (`scene3d.py:251`) oublie la mémoire. Le moniteur interactif (#227) utilise `ui/scene3d_preview.py` : image figée en lecture, mode `interactive` à l'arrêt, badge « Aperçu approximatif », sonde GPU en fil ordinaire.
- **Le moteur GPU** (`kut3d/gpu/renderer.py`) rend dans un `QRhiWidget` jamais affiché et lit l'image (`grabFramebuffer`), **fil principal seulement** (`availability.require_main_thread`). Mesuré (ADR-0009, `docs/kut3d-render.md:158`) : 720p, 20 000 triangles : ≈ 26 ms contre ≈ 550 ms en numpy, ±6 niveaux d'écart sur 100 % des pixels (arêtes).
- **Pages :** `core/workspace_state.py:554` `PAGES = (EDIT, COMPOSITION, COLOR, AUDIO)` ; `PAGE_LEVELS` (`ui/main_window_mixins/menu_levels.py:74`) : Simple = Montage + Audio, Studio + Couleur, Avancé + Composition. Chaque page est un paquet (`ui/color_page/`, `ui/composition_page/` : `panel.py` + vues) dans le `QStackedWidget` de `ui/main_window.py`.
- **Historique du projet :** `core/edit_history.py` `ProjectHistory` garde des **copies profondes** du `Project` (100 au plus, partage des séquences inchangées) et fusionne les gestes par `merge_key` + fenêtre de temps. Il ne sait pas copier un maillage sans le dupliquer.

---

## 1. Document éditable avec annulation

**Décision : états immuables + commandes pures** (module `kut3d/document*.py`, **sans Qt**, vérifié par un test).

```
        execute(commande)                 undo / redo : on change d'indice, rien n'est recalculé
  ┌────────────────────────┐
  │ DocumentState (frozen) │ ──commande.apply──▶ DocumentState'   (objets, lumières, caméra, fond, compteur d'identifiants)
  └────────────────────────┘
        │ partage ses Model avec tous les états de l'historique  (12,9 Mo de maillages pour 7 sphères 256x128, 0 octet copié)
        ▼
   SceneDocument = liste d'entrées (libellé, état) + indice courant + marque « enregistré » + clé de geste ouvert
        │ scene()  (mémorisée par révision)            │ load(path) / save(path)  →  kut3d.load_scene / save_scene
        ▼                                              ▼
   kut3d.Scene immuable  ──▶ rendu, picking          fichier .kut3d (octets identiques à ceux de save_scene)
```

- **Commandes** (prototype, 15) : `AddPrimitive`, `AddModel`, `DuplicateObject`, `RemoveObject`, `SetTransform`, `Rename`, `SetAnimation`, `SetPrimitive`, `SetMaterial`, `SetCamera`, `SetBackground`, `AddLight`, `SetLight`, `RemoveLight`, `Batch`. Chacune est une valeur `frozen` : `apply(état) → état'` ou `DocumentError`. **Un refus ne change rien** (ni état, ni historique, ni révision) ; `Batch` est « tout ou rien » (déplacer une sélection = une entrée).
- **Les valeurs sont revalidées par les constructeurs existants** (`Instance`, `Camera`, lumières, `PrimitiveSpec`) : NaN, infini, matrice dégénérée, clip inconnu, dimensions hors bornes ne peuvent pas entrer dans le document. Les **plafonds du fichier** sont vérifiés à chaque état (`check_state`) et le budget de triangles est vérifié **avant de générer un maillage** (un refus ne génère rien : test avec un compteur sur `primitive_model`).
- **Identités :** `o<N>` / `l<N>` avec un compteur **de document** (jamais réutilisé, même après « annuler puis éditer » ; « rétablir » une création redonne le même identifiant). Les identifiants **ne s'écrivent pas** : à la lecture ils sont redonnés dans l'ordre du fichier. Conséquence assumée : l'identité survit à la session d'édition, pas au rechargement du fichier. Le nom (`Instance.name`, 200 caractères au plus, `check_name`) est libre, **non unique** : le nom par défaut d'une forme est son genre (`sphere`), **sans suffixe « 2 »** ; il passe par les mêmes contrôles qu'un nom saisi.
- **Copie à l'écriture :** `DuplicateObject` partage le `Model` ; `SetPrimitive`/`SetMaterial` donnent un nouveau modèle **à cet objet seulement** (l'autre garde l'ancien). Un fichier avec deux copies d'une sphère a une entrée de modèle ; après l'édition d'une copie, deux. Les budgets comptent un modèle **partagé une seule fois**, comme le fichier.
- **Ce qui a été modifié à la main est refusé, jamais réécrit :** un modèle importé ne s'édite pas par paramètres (seule sa pose) ; une forme dont le maillage a été remplacé après coup (`pristine_material`) refuse `SetPrimitive` et `SetMaterial` ; un matériau avec **texture** est refusé à l'édition (les textures de formes de base ne s'enregistrent pas encore, `save_scene` les refuse) ; un modèle sans fichier source est refusé à l'ajout.
- **Gestes :** `SetTransform(gesture=True)`, `SetPrimitive`, `SetMaterial`, `SetCamera`, `SetLight` portent une `merge_key` ; les commandes de même clé qui se suivent tiennent en **une** entrée jusqu'à `end_gesture()` (bouton relâché), `undo`, `redo` ou un enregistrement. **Pas de fenêtre de temps** (déterministe, testable sans horloge), contrairement à `ProjectHistory.record(merge_window=…)`. Jamais de fusion avec l'état initial ni avec l'état enregistré.
- **Garantie : tout état atteignable par commandes est enregistrable.** Chaque valeur nouvelle est écrite comme `save_scene` l'écrirait puis relue par les validateurs du lecteur (`document_validate.py`) : aucune borne n'est recopiée, et ce que le fichier refuserait l'est à l'édition. Un test à graine fixe (`test_kut3d_document_savable.py`) vérifie : refus sans changement, ou `save()` réussit et la relecture redonne les mêmes octets.
- **Fichier :** `load`/`save` ; `save` refuse d'écraser un fichier qui a changé sur le disque depuis la dernière lecture/écriture (`DocumentConflict`, empreinte du contenu), reconnu sous toute graphie du chemin (`os.path.samefile` : relatif, `..`, liens, casse), et refuse d'enregistrer sous un AUTRE fichier existant, sauf `overwrite=True`. `canonical_on_disk` dit si le fichier lu **n'est pas** ce que `save` écrirait (fichier écrit à la main : `normalize`, identifiants de modèles, JSON compact) : l'enregistrer le **réécrit** ; l'interface le dit avant (lot 7).
- **Coût mesuré :** exécuter une commande ≈ 0,02 ms avec 7 objets, ≈ 0,17 ms avec 2 048 (vérification des plafonds en O(n)) ; `scene()` ≈ 0,2 ms à 2 048 objets ; `save` 6,7 ms et `load` 11,6 ms pour 7 sphères (4 372 octets : des paramètres, pas des sommets).

**Intégration à l'historique existant : séparée, par choix.**

| Option | Verdict | Raison chiffrée |
|---|---|---|
| **A. Historique propre à la page (recommandé)** | retenu | Annuler/Rétablir du menu Édition vont au document quand la page 3D est active. 100 entrées × 0 copie de maillage. |
| B. Mettre le document dans les instantanés de `Project` | écarté | `ProjectHistory` copie en profondeur : 12,9 Mo par instantané pour 7 sphères × 100 = **1,3 Go** (mesuré en taille de tableaux ; la copie elle-même ne prend que ≈ 0,6 ms, c'est la mémoire qui rend l'option impraticable). La scène n'est d'ailleurs pas dans le projet : c'est un fichier. |
| C. Commandes avec inverse (modèle mutable) | écarté | Chaque commande devrait écrire et tester son inverse (14 asymétries possibles) ; les états immuables donnent undo/redo gratuitement et exactement. |
| D. Rendre `Scene` mutable | écarté | Casse la garantie « immuable » dont dépendent les caches de rendu et le partage entre fils (`core.scene3d` mémorise des `Scene`). |

Conséquence : **« Appliquer » ne crée pas d'entrée dans l'historique du projet** (le projet ne change pas, seul le fichier change). Le document d'un chemin reste ouvert, avec sa pile, tant que le projet est ouvert (changer de page ne la perd pas) ; fermer le projet avec une scène modifiée non appliquée demande confirmation.

## 2. Viewport interactif

**Décision : l'image par QRhi, la sélection par un rayon numpy, les gizmos en 2D.**

Mesures (7 sphères 256x128 = **455 168 triangles**, 640x360, `tools/perf/kut3d_pick_bench.py`) :

| Mesure | Temps | Rapport |
|---|---|---|
| `assemble` (géométrie en espace monde, déjà faite par le rendu) | 7,5 ms | — |
| Rayon contre **tous** les triangles (Möller-Trumbore numpy) | 26,4 ms | 0,026 × le rendu numpy |
| Rayon, tri par boîte englobante de chaque dessin puis détail | **3,3 ms** | 0,003 × le rendu numpy |
| Rendu numpy 640x360 | 1 033,5 ms | 1 |
| Rendu QRhi 640x360 (lecture comprise) | 32,8 ms | numpy / QRhi = 31,5 |

- **Image :** QRhi. 33 ms est ≈ 30 images/s avec lecture de l'image ; une version suivante pourra afficher sans lecture (le moniteur GPU de l'application le fait déjà). Fil principal seulement (contrainte du moteur).
- **Sélection :** un clic = un rayon. À 455 168 triangles, 3,3 ms avec tri par boîtes (le tri par boîte se recalcule quand la géométrie change, pas à chaque clic). Le **survol** à 60 Hz tient aussi (3,3 ms ≪ 16 ms). Le rayon se lance dans la **géométrie posée à l'instant affiché** (`assemble(scene, t)` : les animations de modèles importés comptent), donc ce qu'on voit est ce qu'on sélectionne. Il faut ajouter à `Geometry` l'indice d'instance de chaque dessin (aujourd'hui `tri_draw` donne le dessin ; l'ordre de `_jobs` donne l'instance) : lot 5.
- **Pourquoi pas un tampon d'identifiants (rendu des identifiants en couleur plate) ?** (a) Par le moteur numpy : 1 s, comme un rendu. (b) Par le GPU : un second passage avec ses propres shaders, une lecture de pixel, et **aucune sélection sans GPU** ni en test hors écran (la CI Linux et Windows n'ont pas de GPU) ; le rayon numpy est déterministe et testable partout. (c) Le coût d'un rayon est négligeable devant l'image : on ne complique pas le moteur GPU pour le gagner.
- **Gizmos** (déplacer, tourner, mettre à l'échelle) : dessinés par QPainter à partir de la projection de l'objet (aucun pipeline 3D de plus), **testés analytiquement** (distance écran à l'axe, projection d'un déplacement souris sur l'axe : mathématiques pures, sans Qt, dans un module testable à part). Chaque geste émet `SetTransform(gesture=True)` ; relâcher appelle `end_gesture()`.
- **Orbite / zoom / panoramique :** la caméra de VUE (hors document, hors historique, non annulable) est un état de la fenêtre ; la caméra de la SCÈNE (celle du film) est dans le document (`SetCamera`). Un bouton « caméra de la scène » bascule ; « cadrer la sélection » (F) calcule la boîte englobante déjà utilisée par le tri.
- **Sans GPU** (`probe_qrhi` négative : CI, machine virtuelle) : pendant un geste, les **boîtes englobantes projetées** (QPainter, instantané), puis l'image numpy asynchrone au repos (≈ 25 ms pour un cube 160x120, 1 s pour 455 k triangles : jamais bloquante, fil de travail ordinaire, aucun `QObject` créé ni détruit dans ce fil : un `QObject` qui meurt dans un fil de travail a déjà provoqué un interblocage Qt + GIL).

**Cohabitation avec « moniteur fidèle d'abord » :** le moniteur de la timeline ne change pas (numpy, mêmes octets qu'à l'export). La page 3D est la seule surface où l'image est **approximative en mouvement** (badge « Aperçu approximatif » comme #227), puis **fidèle au repos** : après 400 ms sans geste, la page rend l'image numpy à la taille du viewport dans un fil de travail et la substitue (annulée par le geste suivant). L'écart GPU/numpy mesuré (±6 niveaux, arêtes) reste dit dans l'interface.

## 3. Outliner et propriétés

- **Outliner :** une liste plate (la `Scene` est plate : pas de hiérarchie, ni de parenté — cf. § hors périmètre) avec trois groupes, Objets / Lumières / Caméra et fond ; sélection multiple (Maj/Ctrl) ; renommer (`Rename`), dupliquer, supprimer ; un nom en double est permis (l'identité est l'identifiant). Pas d'icônes œil/verrou : le format n'a ni visibilité ni verrou d'objet, et **on n'invente pas d'état que le fichier perdrait** (règle « un état verrouillé vaut pour tous les chemins d'entrée » : un verrou nécessiterait un champ de fichier, donc un format 4 — demande à l'utilisateur, non prévue).
- **Propriétés :** formulaire **piloté par une table**, pas écrit à la main : les champs d'une `PrimitiveSpec` viennent de `dataclasses.fields` et leurs bornes d'une table `PARAM_LIMITS` (à ajouter dans `kut3d/primitives.py` au lot 4, avec un test qui vérifie qu'elle couvre tous les champs de toutes les formes). Un nouveau genre de forme n'exige alors aucun code d'interface. Les champs affichent et acceptent **les mêmes bornes que le modèle** (aucun champ ne propose une valeur que la commande refuserait ou ramènerait). Sections : Transformation (position / rotation / échelle ↔ `pose_matrix`), Forme (paramètres, texte 3D, police), Matériau (couleur, métal, rugosité, émission, double face, mode alpha), Animation (clip, décalage, boucle), Lumière, Caméra, Fond.
- Les champs **ne sont jamais réparentés** après construction (`replaceWidget`, `setParent` interdits : il a déjà fait planter l'application à la fermeture) ; les sections réservées à Avancé sont créées ou non **dans le constructeur** selon le niveau. Un champ numérique en cours de glisser émet des commandes `gesture=True`.
- **Éditeur désactivé plutôt qu'écrêté :** pour un modèle importé, l'onglet Forme est absent ; pour un fichier que l'éditeur ne sait pas représenter (matériau texturé d'un modèle importé), le matériau est en lecture seule avec le motif, jamais réécrit.

## 4. Lien avec le clip de la timeline

**Décision : le fichier `.kut3d` externe reste la seule source ; la page l'édite en place.**

```
 clip (GraphicLayer SCENE3D, .kut v22, source_path) ──────────────┐
        │  « Éditer la scène »  (inspecteur / double-clic)         │
        ▼                                                          │  scene_signature(stat + empreinte)
  SceneDocument.load(source_path) ──édition, undo──▶ « Appliquer » ─▶ save_scene (atomique) ─▶ le fichier change
                                                                   │  → les clés de cache de rendu changent (aucune purge à écrire)
                                                                   └─▶ reload_scene(path) + redessin du moniteur
```

| Option | Verdict | Raison |
|---|---|---|
| **Fichier externe édité en place (recommandé)** | retenu | Déjà le modèle du `.kut` v22, des scripts (`Film.place_scene`, `add_scene3d_clip`) et de `dependency_files`. **Aucun changement de format** ; l'invalidation du cache est déjà correcte (signature). |
| Scène embarquée dans le `.kut` | écarté | Exige `.kut` v23 + migration, deux représentations de la même scène (fichier pour les scripts, projet pour l'interface), et casserait la règle de parité des scripts (ADR-0005). |
| Brouillon sans fichier, appliqué à l'enregistrement du projet | écarté | Le moniteur et l'export lisent le fichier ; un brouillon invisible de l'export serait une surprise. |

- **Appliquer** n'est pas automatique à chaque geste : chaque écriture change la signature et invaliderait les caches de rendu de tous les clips qui utilisent le fichier ; on applique sur demande (bouton, Ctrl+S dans la page), et au quitter la page avec confirmation si modifié. Un brouillon « en direct » vers le moniteur (une `Scene` en mémoire substituée à la lecture du fichier) est une option du lot 7, **non décidée** ici car elle fait entrer un état non sauvegardé dans le chemin du rendu.
- **Plusieurs clips sur le même fichier :** tous suivent (même signature) ; la page affiche « utilisée par N clips ». **Conflit :** si le fichier a changé depuis la lecture (autre programme, script, autre clip), `DocumentConflict` : l'interface propose « recharger » ou « écraser », jamais l'écrasement silencieux.
- **Modèles importés :** ils doivent rester **sous le dossier du `.kut3d`** (règle de `save_scene`) : « Importer un modèle » copie le modèle et ses dépendances dans `<scène>_models/` en réutilisant la copie confinée de `core/film_scene.py` (plafonds : 1 000 fichiers, 2 Gio). Les **textures** d'un modèle importé suivent leur modèle ; celles d'une forme de base ne s'enregistrent pas (limite connue, format 4 à décider avec l'utilisateur).
- **Compatibilité :** le document écrit exactement ce qu'écrit `save_scene` (version 1, 2 ou 3 selon le contenu) ; un clip n'a pas à savoir que la page existe.

## 5. Niveaux d'usage

**Décision : Studio** pour la page (`PAGE_LEVELS[PAGE_3D] = STUDIO`). Raison : le niveau qui peut **ajouter** un clip 3D (« Scène 3D… », Studio) doit pouvoir l'**éditer** ; sinon un utilisateur Studio poserait une scène qu'il ne peut pas toucher. *Alternative écartée :* Avancé comme Composition (cache l'édition à ceux qui posent le clip). Dans la page : transformation, forme, matériau de base, lumières, caméra visibles en Studio ; **Avancé** ajoute plans proche/lointain, tessellation (segments, anneaux), mode alpha et seuil, émission, animation d'un modèle importé et police libre du texte 3D. Les sections sont **construites** par niveau dans le constructeur ; `_leave_hidden_surfaces` ramène à Montage si le niveau baisse (comportement existant). La page s'ajoute **à la fin** de `PAGES` : un espace de travail enregistré qui ne la connaît pas retombe sur l'état par défaut de la page.

## 6. Plan de livraison

Chaque lot est une PR relisable, fusionnable seule, avec `CHANGELOG.md` (« Interne : » tant que rien n'est visible), libellés fr/en/es (`python -m tools.i18n_audit`), mypy, et la suite complète (les gardes de structure : 700 lignes par module).

| Lot | Contenu | Taille | Critères de réussite (testables) | Risques |
|---|---|---|---|---|
| **0 (cette PR)** | ADR, `kut3d/document*.py` (3 modules, ≈ 950 lignes), 72 tests, banc `kut3d_pick_bench` | M | aller-retour octet-stable ; annuler jusqu'au début redonne les octets d'origine ; refus ne change rien ; plafonds ; NaN/inf refusés ; `import kut3d` sans Qt | choix de modèle (validé ici) |
| **1** | Page « 3D » vide : `PAGE_3D`, niveau Studio, rail, raccourci, ouvrir / nouveau / Appliquer, Annuler-Rétablir routés, confirmation à la fermeture | M (≈ 500 lignes + tests) | la page n'apparaît qu'à partir de Studio ; Annuler agit sur le document quand la page est active, sur le projet sinon ; aucune écriture sans « Appliquer » | **réparentage de widgets** (construire dans les constructeurs) ; ne lancer les fichiers UI qu'un par un, sans xdist |
| **2** | Viewport : image (QRhi + repli numpy), orbite / zoom / panoramique / cadrer, grille et axes, badge, rendu fidèle au repos | L | caméra de vue hors historique ; repli sans GPU testé en hors-écran ; aucun rendu dans un fil non principal pour QRhi | GPU absent en CI ; le moniteur hors écran est **noir** : prouver par une grille de contact d'export, pas par une capture ; polices (Text3D : Anton/Saira livrées seulement) |
| **3** | Outliner : liste, sélection multiple, renommer / dupliquer / supprimer | M | une commande = une entrée ; sélection par identifiant stable survit à annuler | sélection et identifiants après annuler (une sélection pointant un objet disparu est vidée) |
| **4a / 4b** | Propriétés : transformation + forme (table `PARAM_LIMITS`) / matériau, lumières, caméra, fond | L chacun | un champ ne propose jamais une valeur que la commande refuserait ; glisser = 1 entrée ; champs désactivés ET garde côté code pour les formes importées | bornes ; texte 3D hors fil principal (Qt) |
| **5** | Picking : `Geometry` donne l'instance de chaque dessin ; rayon numpy + tri par boîtes ; clic / Maj / survol | M | un clic sur un objet le sélectionne ; objet derrière un autre non ; échelle miroir ; clip animé à l'instant affiché | précision flottante ; scène vide |
| **6** | Gizmos : déplacer / tourner / échelle, axes, aimantation, espace monde / local (mathématiques dans un module sans Qt) | L | projection souris → axe exacte ; un geste = une entrée ; échelle > 0 et finie toujours | géométrie de la caméra proche du plan de l'axe (seuils) |
| **7** | Lien clip : « Éditer la scène », Appliquer, Recharger, conflit, « utilisée par N clips », fichier réécrit en forme canonique annoncé ; option brouillon en direct | M | Appliquer change la signature et rien d'autre ; conflit refusé ; deux clips suivent | écriture pendant un export en cours (le rendu lit le fichier : verrou à vérifier) |
| **8** | Créer et importer : menu Ajouter (formes, texte 3D, lumières, modèle importé avec copie confinée) | M | importer copie sous le dossier ; refus au-delà des plafonds ; une erreur d'import ne laisse aucun fichier partiel | I/O, plafonds de copie |
| **9** | Parité de script : `Film.edit_scene(path)` donne un `SceneDocument` (mêmes commandes) ; `python -m tools.scripting_debt verbs --update-baseline` | S | tout geste de la page a son équivalent en script | frontière de sécurité d'ADR-0005 |

## 7. Hors périmètre

- **Animation de personnages** (clips, mélange, os, visage, FBX) : phase 4 d'ADR-0009. **FBX** : non pris en charge (format propriétaire).
- **Ombres, flou de mouvement, mipmaps** : qualité du moteur, avant un premier film (déjà rencontré) ; l'édition n'en dépend pas.
- **Textures sur les formes de base dans le fichier** : exige un format 4 du `.kut3d` ; à décider avec l'utilisateur (aujourd'hui refusé à l'édition, pas perdu en silence).
- **Hiérarchie / parenté d'objets, verrou et visibilité par objet :** la `Scene` est plate et le fichier n'a pas ces champs ; les ajouter est un changement de format.
- **Question ouverte, à instruire après le lot 4 :** l'**animation d'objets dans le temps** (images clés de position/rotation/échelle d'un objet de scène). Aujourd'hui la scène n'anime que les clips des modèles importés ; le mouvement d'un film se fait par script (une scène par plan, `Film.render_shot`). Le document est prêt à la recevoir (une commande de plus, un état de plus), mais le format et le rendu numpy doivent d'abord le dire : c'est un ADR à part.
- Édition de maillage (sculpture, sommets), éditeur de matériaux nodal, rendu GPU à l'export.

## Risques transversaux

1. **Segfaults Shiboken au réparentage de widgets** (déjà rencontré) : aucun `replaceWidget` / `setParent` / `addItem` d'un widget existant ; les portes de niveau se construisent dans les constructeurs ; tests UI lancés fichier par fichier avec `PYTHONFAULTHANDLER=1`.
2. **GPU :** la CI n'a pas de GPU ; tout ce qui est logique (document, picking, gizmos) est testé sans ; l'image QRhi se vérifie par la sonde et les tests GPU existants (`kut3d_gpu_cases`), pas par une capture hors écran (noire).
3. **Polices hors écran :** Windows en hors-écran n'a aucune police système ; les tests de texte 3D n'utilisent que Anton / Saira (livrées).
4. **Fil principal :** QRhi et la génération d'un texte 3D (Qt) n'ont lieu que dans le fil principal ; le picking et le rendu numpy peuvent aller dans un fil de travail ordinaire.
5. **Écriture concurrente du fichier** (autre clip, script, export en cours) : conflit détecté à l'enregistrement (empreinte du contenu) ; à vérifier au lot 7 que l'export ne lit pas un fichier à moitié écrit (l'écriture est atomique : `os.replace`).

## Ce que le prototype (lot 0) a validé, et ce qu'il a fait découvrir

Validé : le modèle tient sur les cas listés dans la demande — fond / forme pleine, clip animé (glTF à animations, conservé à travers les éditions et le fichier), modèles partagés, plafonds (2 048 objets, 16 lumières, 256 modèles, 500 000 triangles, vérifiés avant de générer), NaN/inf refusés, annuler jusqu'au début = octets d'origine, jamais de fusion de geste avec l'état enregistré.

Découvert : (1) `Instance` ne valide pas `name` (type ni longueur) : le document le fait (`check_name`) pour que `save_scene` ne refuse pas plus tard ce qu'il a accepté ; (2) enregistrer un fichier écrit à la main le **réécrit** sous sa forme canonique (identifiants de modèles recalculés, `normalize` remplacé par `root_transform`) : `canonical_on_disk` le détecte pour que l'interface le dise ; (3) les identifiants ne peuvent pas être persistés sans changer le format : ils sont de session ; (4) un modèle partagé doit être compté une fois dans le budget de triangles, comme le fichier l'écrit.

Alternative envisagée puis écartée pour l'identité : écrire un identifiant d'objet dans le `.kut3d` (clé `id` d'instance). Elle donnerait des références stables entre sessions (scripts qui visent un objet par identifiant) mais demande un **format 4** ; la décision revient à l'utilisateur et n'est pas nécessaire pour l'édition.
