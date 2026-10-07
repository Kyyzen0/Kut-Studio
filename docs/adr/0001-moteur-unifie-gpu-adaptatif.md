# ADR-0001 : un moteur de rendu unifié, GPU et adaptatif

**Statut :** Proposé
**Date :** 2026-10-07
**Décideurs :** mainteneur de Kut-Studio

Objectif du produit : **vitesse, puissance et optimisation**, c'est-à-dire monter sans attendre, rendre tout ce que
l'export sait faire, et s'adapter aux limites de chaque appareil.

## Contexte

### Deux moteurs aujourd'hui

| | Moniteur temps réel | Aperçu fidèle |
| --- | --- | --- |
| Rôle | ce qu'on voit pendant la lecture | référence, identique à l'export |
| Rendu | Qt Multimedia + shaders QRhi (option GPU), titres dessinés par Qt au-dessus | graphe FFmpeg de l'export, segments de 2 s encodés en H.264 puis relus |
| Couverture | **une piste vidéo**, transform, effets simples, fusion, masques | tout |

Dès qu'un montage sort de la couverture du moniteur (plusieurs pistes, calques de lumière, grain, étalonnage,
transitions, séquences imbriquées), la lecture attend les segments fidèles (« Calcul de l'aperçu… »). C'est le cas de
tout montage social construit avec les outils 0.2.0.

### Mesures (TikTok « Onlyfab », 31 s, 1080×1920, 54 calques graphiques, Apple M4, aperçu Standard 540×960)

| Segment de 2 s | Calcul | dont FFmpeg |
| --- | --- | --- |
| 0–2 s | 0,9 s | 0,7 s |
| 8–10 s | 1,7 s | 1,5 s |
| 16–18 s | 6,5 s | 3,8 s |
| 24–26 s | 5,5 s | 5,0 s |
| 28–30 s | 5,6 s | 4,5 s |

Causes, par ordre d'importance :

1. **Le coût d'un segment croît avec sa position.** `build_preview_command` (`core/filter_graph.py:141`) compose le
   graphe en temps absolu (fond `color=…:d=<durée totale>`, flux `.ffconcat` des calques, mixage depuis 0) puis place
   `-ss <début>` **en option de sortie** : FFmpeg calcule et jette tout ce qui précède le segment.
2. **Coûts fixes par segment**, même sans (1) : lancement de FFmpeg et analyse du graphe, encodage x264 du segment pour
   le relire aussitôt. Plancher mesuré : 0,9 s pour 2 s en demi-résolution.
3. **Calques stockés plein cadre et redessinés à chaque image animée.** Un titre de 958×170 px devient un PNG
   1080×1920 (8,3 Mo en RVBA au lieu de 0,65 Mo), réécrit à chaque image d'un pop-in ou d'un glissé alors que seule sa
   matrice change : 566 PNG distincts pour 31 s.
4. **Aperçu GPU suspendu en silence.** Un ancien plantage laisse `GpuCrashGuard` en position suspendue ; en Auto,
   l'application ne réessaie plus (`previous_crash` à chaque démarrage).

### Adaptation aux appareils

- `core/runtime_profile.py` déduit le profil (`low` / `balanced` / `high`) du **nombre de cœurs et de la RAM** : le GPU
  n'est pas regardé, rien n'est mesuré. Une machine 8 cœurs / 16 Go à GPU intégré faible est classée `high`.
- `core/memory_monitor.py` suit la pression mémoire ; **rien** ne tient compte de la batterie, du mode économie
  d'énergie ni de la chauffe.
- Sans GPU utilisable, le seul chemin complet est le plus lent (segments FFmpeg).

### Contraintes retenues

- **Machine minimale fluide** : portable milieu de gamme, MacBook Air M1 8 Go ou PC portable récent à GPU intégré,
  8 Go de RAM. Lire le montage Onlyfab sans attente en 1/2 sur cette machine.
- **Export** : par le même moteur que l'aperçu ; l'export CPU FFmpeg actuel est conservé comme option « référence » et
  comme oracle des tests.
- Règles de [architecture.md](../architecture.md) inchangées : `core/` n'importe pas `ui/`, une information a une
  source, aperçu et export lisent le même plan.

## Décision

Faire d'un **compositeur GPU temps réel** le moteur unique de l'aperçu, puis de l'export, et l'entourer d'un **profil
mesuré** et d'un **régulateur** qui adaptent le travail à l'appareil. FFmpeg reste l'outil de décodage, d'encodage et
de référence ; il cesse d'être le chemin de lecture.

1. **Un plan, plusieurs exécutants.** `core/` produit, pour un instant donné, une description d'image (calques,
   matrices, effets, fusions), dans le prolongement de `core/gpu_composite.py`. Le GPU (QRhi) l'exécute ; un
   compositeur CPU allégé l'exécute sur les machines sans GPU utilisable ; FFmpeg et la référence numpy l'exécutent
   pour la vérification.
2. **Calques graphiques en textures** à leur taille réelle, rastérisées une fois par contenu (texte, style, taille à
   l'écran) et animées par matrice dans le shader. Seuls les contenus qui changent vraiment (karaoké, mot par mot,
   lumière procédurale) sont redessinés, ou calculés directement en shader.
3. **Profil machine mesuré** au premier lancement : banc de 2 à 3 s en tâche de fond (composer 10 calques 1080p,
   décoder un plan, rastériser un titre), mis en cache comme `hardware-encoders.json`, relancé à la mise à jour du
   matériel ou du pilote. Il fixe le niveau :

   | Niveau | Exemples | Aperçu |
   | --- | --- | --- |
   | GPU complet | Apple Silicon, GPU dédiés récents | composition GPU, pleine résolution |
   | GPU limité | GPU intégrés anciens, peu de mémoire vidéo | composition GPU, 1/2, budget de textures serré |
   | CPU | pas de GPU utilisable, machine virtuelle | compositeur CPU dans un thread de travail, 1/4 ; segments FFmpeg en tâche de fond seulement |

4. **Régulateur** (extension de `core/preview_adaptive.py` et `core/preview_governor.py`), qui descend du moins visible
   au plus visible et remonte progressivement :
   proxies → effets de voisinage (flou, glow) en 1/2 → motion blur coupé en lecture → résolution d'aperçu → cadence
   de lecture (jamais à l'arrêt ni en scrubbing) → segments fidèles pour un passage précis. L'export n'est jamais
   dégradé.
5. **Économie de travail et d'énergie** : rendu à la demande (rien n'est recalculé à l'arrêt si rien ne change),
   budget de mémoire vidéo avec éviction LRU, préchargement suspendu sur batterie, un niveau de moins en mode
   économie d'énergie ou quand la machine chauffe (`ProcessInfo.thermalState` sur macOS, équivalents Windows / Linux).
6. **Export** : images composées par le GPU, envoyées à l'encodeur matériel (VideoToolbox…) ou logiciel. Le mode
   « Export de référence » garde le graphe FFmpeg CPU actuel, déterministe.

## Entrées / sorties média : FFmpeg dans le processus ou en ligne de commande

Dans le moteur unifié, FFmpeg ne fait plus que **décoder et encoder** (plus de graphe de filtres hors export de
référence). Deux façons de l'utiliser ont été mesurées : **PyAV** 19.0.1 (bibliothèques FFmpeg dans le processus) et
la **ligne de commande** actuelle. Source : l'export Onlyfab (H.264 1080×1920, ~29 Mb/s, images clés toutes les 2,5 à
4,5 s), Apple M4, médiane de 3.

| Opération | Ligne de commande | PyAV, CPU | PyAV, VideoToolbox |
| --- | --- | --- | --- |
| Lancer le processus / ouvrir le fichier | 24 ms | 13 ms | 13 ms |
| Image exacte à 28 s, `-ss` **en sortie** (actuel) | **1 591 ms** | — | — |
| Image exacte à 28 s, `-ss` en entrée / fichier ouvert | 205 ms | 131 ms | 591 ms |
| 2 s (60 images) depuis 20 s, mises en 540×960 RVBA | 419 ms | 279 ms | 933 ms |
| 10 sauts aléatoires (scrubbing) | — | 1 332 ms | 3 217 ms |
| Encoder 60 images 1080×1920 | — | VideoToolbox 308 ms · x264 1 109 ms | |

Ce que montrent les mesures :

- **Le `-ss` en sortie coûte ×7,8** : c'est la correction de l'étape 0, la plus rentable.
- **Le lancement d'un processus ne coûte que 24 ms.** Passer dans le processus fait gagner ~35 % sur le décodage
  (fichiers gardés ouverts, saut à l'image exacte, images directement en mémoire), pas un ordre de grandeur.
- **Un saut coûte surtout le GOP.** ~130 ms par saut ici, parce qu'il faut décoder depuis l'image clé précédente. Les
  proxies de Kut-Studio (GOP 12, ou ProRes Proxy tout intra) règlent le scrubbing ; ils doivent être la source du
  moniteur quand le média d'origine a un long GOP.
- **VideoToolbox ne gagne pas en décodage H.264 1080p sur M4** (transfert vers la mémoire système compris), ce que
  confirme la politique mesurée de `core/decode_policy.py` : le choix doit rester mesuré par appareil.
- **VideoToolbox encode 3,6× plus vite que x264** : levier principal de l'export par le moteur (étape 4).

Décision :

- **PyAV pour le chemin temps réel** : décodage du moniteur, lecture audio, encodage de l'export par le moteur. Un seul
  module, `core/media_io.py`, ouvre les médias dans le processus, comme `core/process_supervisor.py` est le seul à
  lancer des processus (garde par AST sur le modèle de `test_process_launch_guard.py`). Décodage dans des threads de
  travail ; un média qui fait échouer le décodeur est mis de côté pour la session, comme `DecodeHealth`.
- **La ligne de commande reste** pour l'export de référence, les proxies, la détection matérielle et les longues tâches
  de fond : un plantage n'emporte pas l'application et l'annulation reste un arrêt de processus.
- **Licence.** Kut-Studio est sous MIT. Le FFmpeg des roues PyAV publiées contient **x264 et x265 (GPL)** : le charger
  dans le processus et le distribuer imposerait la GPL. Il faut une build **LGPL** de FFmpeg (PyAV compilé contre elle),
  avec l'encodage H.264 / HEVC par le matériel (VideoToolbox, Media Foundation, VA-API) ou OpenH264 (BSD) en logiciel.
  Le FFmpeg GPL de l'utilisateur reste utilisable en ligne de commande (programme séparé).
- **Écartés** : GStreamer (dépendance lourde à empaqueter), libmpv (lecteur, pas outil de montage), OpenCV (sauts
  imprécis, audio absent). Les API système natives (AVFoundation, Media Foundation) relèvent de l'option C.

## Options étudiées

### Option A : optimiser le chemin FFmpeg actuel

| Dimension | Évaluation |
| --- | --- |
| Complexité | Faible à moyenne |
| Coût | Quelques jours à deux semaines |
| Passage à l'échelle | Faible : plancher de ~0,45 s par seconde de vidéo en 1/2, attente après chaque modification |
| Familiarité | Élevée (code existant) |

**Pour :** risque minimal, parité aperçu = export inchangée, gains réels (temps local par segment, calques gardés en
mémoire, codec d'aperçu intra rapide).
**Contre :** reste un chemin de *rendu*, pas de *montage* ; coûts fixes par segment ; aucune réponse aux limites des
appareils.

### Option B : moteur unifié GPU adaptatif (retenue)

| Dimension | Évaluation |
| --- | --- |
| Complexité | Élevée |
| Coût | Plusieurs semaines, par étapes livrables |
| Passage à l'échelle | Élevé : coût par image indépendant de la position, textures réutilisées, dégradation contrôlée |
| Familiarité | Moyenne : QRhi, shaders, références numpy et tests de parité déjà en place |

**Pour :** lecture sans attente du montage social courant ; export beaucoup plus rapide ; parité par construction (un
seul plan, un seul exécutant principal) ; adaptation explicite aux appareils.
**Contre :** chantier long ; l'export par défaut n'est plus déterministe au bit près d'un pilote à l'autre ; limites de
PySide6 (copie CPU par image décodée, rendu QRhi dans le thread de l'interface, jusqu'à ~23 ms de retard au 95ᵉ
centile en 4K).

### Option C : moteur natif réécrit (C++ / Rust, thread de rendu dédié)

| Dimension | Évaluation |
| --- | --- |
| Complexité | Très élevée |
| Coût | Plusieurs mois |
| Passage à l'échelle | Le plus élevé |
| Familiarité | Faible |

**Pour :** lève les limites de PySide6 (copie zéro, thread de rendu).
**Contre :** réécrit ce qui fonctionne ; casse la testabilité de `core/` en Python pur ; disproportionné tant que
l'option B n'a pas atteint ses limites. Gardée comme suite possible si les mesures de l'étape 4 l'exigent.

## Analyse des compromis

- **Parité.** Elle ne vient pas de FFmpeg mais du fait que tout le monde lit le même plan. Les tests comparent déjà
  FFmpeg, numpy et les shaders (écart max 3,5/255) ; on inverse les rôles : le GPU produit, FFmpeg et numpy vérifient.
- **Déterminisme de l'export.** Accepté comme compromis grâce à l'export de référence, à proposer pour l'archivage ou
  en cas de doute.
- **Appareils modestes.** Option A ne les aide pas ; option B les sert par le niveau mesuré et le régulateur ; le
  chemin CPU allégé évite que « pas de GPU » signifie « segments FFmpeg ».

## Conséquences

**Plus simple :**
- lecture immédiate des montages multipistes et sociaux ; plus d'attente après chaque modification ;
- export rapide ; aperçu identique à l'export par construction ;
- comportement prévisible d'un appareil à l'autre (niveau mesuré, dégradation ordonnée).

**Plus difficile :**
- maintenir trois exécutants (GPU, CPU allégé, FFmpeg de référence) d'un même plan ;
- tester sur de vrais GPU Windows / Linux (aujourd'hui vérifiés par simulation seulement) ;
- versionner les caches de textures et le profil machine (pilote, matériel, `RASTER_VERSION`).

**À revoir :**
- les limites de PySide6 après l'étape 4 (option C si nécessaire) ;
- la place des segments fidèles : à terme, réservés au contrôle et aux effets hors moteur.

## Plan d'action

Chaque étape est livrable seule, mesurée sur une machine de chaque niveau, et vérifiée par des tests de **complexité**
(comme `tests/test_performance.py`) plutôt que de durées.

1. [ ] **Étape 0 : corrections du chemin actuel.** Segment composé en temps local (supprimer le `-ss` de sortie,
   `time_offset` de `write_stream`) ; test : le coût d'un segment ne dépend plus de sa position, images identiques à
   l'export. Signaler à l'utilisateur la suspension de l'aperçu GPU et proposer de réessayer.
2. [ ] **Entrées / sorties média.** Build FFmpeg LGPL + PyAV dans le paquet (macOS, Windows, Linux), `core/media_io.py`
   et sa garde AST, moniteur alimenté par les proxies quand l'original a un long GOP. Critère : un saut ≤ 50 ms sur
   proxy, sur la machine minimale.
3. [ ] **Étape 1 : profil mesuré et régulateur.** Banc du premier lancement, trois niveaux, échelle de dégradation,
   batterie / économie d'énergie / chauffe, rendu à la demande, budget de mémoire vidéo.
4. [ ] **Étape 2 : composition complète.** Plusieurs pistes vidéo, calques graphiques en textures animées par
   matrice, lumière et grain en shaders, compositeur CPU allégé. Critère : le montage Onlyfab joue sans « Calcul de
   l'aperçu » sur la machine minimale.
5. [ ] **Étape 3 : couverture.** Étalonnage et LUT (texture 3D), transitions, séquences imbriquées rendues en texture.
6. [ ] **Étape 4 : export par le moteur.** Encodeur matériel, mode « Export de référence » ; tests de parité GPU /
   référence sur le corpus de rendu existant.
7. [ ] **Suivi** : banc `tools/perf` étendu (images par seconde en lecture, latence au scrubbing, mémoire vidéo,
   énergie) ; décision sur l'option C au vu des mesures de l'étape 4.
