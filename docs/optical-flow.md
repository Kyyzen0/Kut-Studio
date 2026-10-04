# Images intermédiaires : échantillonnage, mélange d'images, flux optique

Quand un clip est ralenti, la timeline demande plus d'images que le média n'en contient. Trois façons d'y répondre, au choix par clip
(menu *Vitesse → Images intermédiaires*, ou section *Temps* de l'inspecteur) :

| Mode | Ce qui est montré | Coût | Défaut visible |
| --- | --- | --- | --- |
| **Échantillonnage** (défaut) | l'image source la plus proche | nul | images répétées (saccades) |
| **Mélange d'images** | `A·(1 − t) + B·t`, `t` exact | faible | flou de mouvement fantôme |
| **Flux optique** | la scène à l'instant `t` entre `A` et `B`, en suivant le mouvement | élevé, mis en cache | artefacts aux occlusions (repli signalé) |

Le choix n'est **jamais** déduit en silence : le mode demandé est celui de l'export et de l'aperçu fidèle ; si un calcul n'est pas
fiable, l'image dit ce qu'elle est (repli) et le bilan l'affiche.

## Le plan : quelles images, exactement

`core/frame_interpolation.py` calcule, **sans un pixel**, l'image de chaque tick de sortie : une image source (`t = 0`) ou un point
`t` de la paire `(A, A+1)`. `x = M(T) × cadence_source` donne `A = ⌊x⌋` et `t = x − A`.

* Une position qui tombe sur une image source (`t` nul à `1e-3` près : demi-image, quart d'image alignés) **est** cette image,
  jamais recalculée.
* Un arrêt montre une image figée (la plus proche), pas un mélange flou.
* **Pas de calcul inutile** : au-delà de 2 images source par image de sortie (`MAX_INTERPOLATED_STEP`), les images voisines sont
  sautées, intercaler entre elles n'ajoute rien. 200 %, 400 %, 1000 % ne fabriquent donc **aucune** image, quel que soit le mode :
  le graphe retombe sur l'échantillonnage, qui produit alors exactement les mêmes images.
* La dernière image du média n'a pas de suivante : elle est montrée telle quelle.
* Un changement de cadence (24 i/s sur un média de 30) est la même chose : les poids viennent du modèle.

## Mélange d'images

`A·(1 − t) + B·t` en flottants sur les valeurs **encodées** (la même convention que tout le pipeline, qui compose en RVB encodé),
arrondi une seule fois à 8 bits. Le poids se mesure : à 50 %, 25 %, 40 %, 80 %, l'indice lu sur des images dont la luminance est
leur numéro correspond à `N + t` à un niveau de gris près (`tests/test_retime_prepare_real.py`). Les images **exactes** d'un clip
préparé sont identiques à l'échantillonnage (à un niveau de gris près : même décodage, même mise au cadre).

## Flux optique : l'architecture

```
 OpticalFlowEngine ── un backend + une qualité
   ├─ FlowEstimator    analyse une paire d'images → PairAnalysis (coupure, identiques, ou flux aller et retour)
   ├─ FrameInterpolator fabrique l'image au point t d'une paire analysée, ou retombe honnêtement
   └─ OpticalFlowBackend  (le contrat) ── NumpyBackend (processeur, premier backend)
                                          └ Metal, CUDA, Vulkan, OpenCL, CoreML, réseau neuronal : même contrat
```

* `FlowField` (`core/flow_field.py`) : déplacement `(u, v)` par pixel sur la grille d'analyse **et confiance** par pixel.
* `OpticalFlowBackend` : `available()`, `analyze(a, b, params)`, `synthesize(a, b, pair, t)`. Choix utilisateur **Auto / Processeur
  / GPU** (page *Performances* des préférences) : *Auto* prend le premier backend disponible, *Processeur* le NumPy, *GPU* est
  refusé avec un message clair tant qu'aucun accélérateur n'existe (jamais un repli silencieux).
* Pas d'OpenCV : mesuré pour le suivi, il pèse environ dix fois plus que tout le reste de l'application empaquetée. Le backend
  NumPy ne demande rien de plus que ce que le suivi embarque déjà ; `optical_flow.self_check()` le vérifie dans l'application
  construite (`main.py --smoke-test`).

### Estimation (backend NumPy, `core/flow_numpy.py`)

Lucas-Kanade **pyramidal** itératif, du niveau grossier au niveau fin : l'image d'arrivée est déformée par le flux courant, les
gradients donnent un système 2×2 par pixel sommé sur une fenêtre, **régularisé** (Tikhonov) pour que les zones sans texture ne
divergent pas, puis le flux est lissé en **moyenne pondérée par la texture** : un aplat intérieur reçoit le mouvement des bords qui
l'entourent, un bord garde le sien. Un **a priori de mouvement nul** (`_ZERO_PRIOR = 0,01`) empêche un aplat *immobile* voisin
d'un petit élément mobile d'hériter de son mouvement (mesuré : 2,6 px de flux parasite et une confiance de 0,48 sans, 0,5 px et
0,90 avec).

Le flux d'une paire est estimé **dans les deux sens**. Il n'a aucun état caché : mêmes images, mêmes réglages, mêmes octets.

| Qualité | Grille d'analyse | Niveaux × itérations | Fenêtre / lissage |
| --- | --- | --- | --- |
| Brouillon | ¼ de la résolution de travail | 4 × 2 | 4 / 3 |
| Équilibrée (= *Auto* à l'export) | ½ | 5 × 3 | 5 / 4 |
| Maximale | pleine | 6 × 4 | 6 / 5 |

La **résolution de calcul** (pleine, moitié, quart) est celle de la grille d'analyse ; la synthèse, elle, se fait toujours à la
résolution de travail. Une qualité plus basse ne dégrade donc que le mouvement, jamais la netteté de l'image.

### Synthèse (projection avant)

Chaque pixel de `A` est posé à `x + t·F(A→B)`, chaque pixel de `B` à `x + (1 − t)·F(B→A)` (quatre voisins, poids bilinéaires),
pondérés par leur confiance puis moyennés, `A` pesant `1 − t` et `B` pesant `t`. Contrairement à une déformation arrière, un objet
arrive à sa **bonne place sur ses bords** ; en cas de conflit (deux pixels au même endroit), le plus rapide gagne (le premier plan
bouge plus que le fond). Une zone qu'aucune des deux images ne recouvre (désocclusion) retombe, **au prorata**, sur le mélange
simple.

### Confiance et replis

La confiance est **mesurée**, pas promise : erreur photométrique après déformation × accord avec le flux inverse, par pixel ; celle
d'une image est la moyenne des deux flux × la part de pixels couverts. Quatre issues, que le bilan compte :

| `Fallback` | Quand | Ce qui est montré |
| --- | --- | --- |
| *aucun* | flux fiable | l'image synthétisée |
| `identical` | moins de 0,002 % des pixels diffèrent de plus de ≈ 5 niveaux (caméra immobile, image dupliquée) | l'image telle quelle, **sans calcul** |
| `scene_cut` | histogrammes très différents **et** structures sans rapport (corrélation < 0,6) | l'image la plus proche : jamais deux plans mélangés |
| `low_confidence` | confiance < 0,35 (occlusions massives, flash d'éclairage, bruit) | un mélange simple |

Un **flash** change l'histogramme mais garde la structure : ce n'est pas une coupure, la confiance s'effondre et l'image devient un
mélange (une transition de luminosité lisse, pas un ghost de deux plans). Un bruit de 3 % baisse la confiance (≈ 0,8) sans la faire
tomber. Un fond immobile n'est plus pénalisé par un petit élément mobile.

## Cache des vecteurs de mouvement

`core/flow_cache.py`, couche `flow` du `CacheManager` (budget disque, LRU, purge, statistiques) :

* **Une entrée = une paire d'images consécutives.** Clé : le média **réellement décodé** (chemin et signature : un proxy et l'original
  sont deux entrées, et un flux calculé sur un proxy ne sert **jamais** l'export), l'indice de la paire, la grille et le filtre de
  mise au cadre, le moteur (version, backend, réglages d'analyse **et seuils** qui décident ce qu'une paire *est* : identique,
  coupure, confiance trop basse). Retoucher l'un d'eux, ou l'algorithme (`ENGINE_VERSION`), invalide les analyses anciennes.
* **Le mouvement est celui du média, pas du clip.** Aucune vitesse, courbe, position ni identifiant de clip dans la clé : déplacer
  le clip, passer de 50 % à 40 %, bouger un point de la courbe ne recalcule **aucune** paire (testé).
* **La forme stockée est la définition du résultat.** Les champs ne sont gardés qu'à la **moitié de la grille d'analyse** et en
  demi-précision ; l'analyse fraîche passe par la même réduction avant d'être utilisée. Un rendu à froid et un rendu avec cache sont
  donc **identiques au bit près** : le cache ne change jamais ce qu'on exporte (testé).
* Mesures de stockage (pour le choix ci-dessus, 960 × 540) : 6,2 Mo bruts par paire ; `zlib` ne gagne que ×1,3 à ×1,6 (flottants
  bruités) ; la réduction de moitié divise par 4 pour une erreur d'image **inchangée** ; la demi-précision change une image de
  0,0013 au plus.
* Une entrée illisible ou incohérente vaut une entrée absente (jetée, journalisée) ; écriture atomique (`.tmp` puis `os.replace`),
  donc plusieurs instances de l'application, et plusieurs fils d'une même instance (export, aperçu, analyse préparant la même clé),
  partagent le dossier sans se gêner : le nom temporaire est propre à **chaque écriture** (processus et appel) ; les écritures abandonnées par un arrêt brutal sont
  nettoyées au démarrage **seulement si elles n'ont pas bougé depuis une heure** (jamais l'écriture vivante d'une autre instance).

## Préparation : du plan aux images

Le graphe FFmpeg sait *choisir* une image source, pas en *fabriquer*. `core/retime_prepare.py` produit donc les images avant :

1. le plan dit, run par run, lesquelles sont intermédiaires. Un run qui n'en a aucune reste dans le graphe d'échantillonnage ;
2. les images sources utiles sont décodées par FFmpeg (décodage logiciel, **mêmes** filtres de mise au cadre que le graphe), une
   fenêtre glissante en mémoire. Les horodatages sont comptés depuis le début du **flux** (`-start_at_zero`), pas depuis l'horloge
   du conteneur (un MPEG-TS démarre à 1,4 s). Le décodage démarre au milieu du fichier (`-ss`) pour MP4, MOV, MKV et WebM, dont
   le saut est exact ; les autres conteneurs (MPEG-TS, MPEG-PS, AVI…) sont décodés **depuis le début** : le saut de FFmpeg y tombe
   sur l'image clé *suivante*, et les images entre la cible et elle manqueraient. Plus lent pour une fenêtre lointaine, mais exact ;
3. chaque image est fabriquée (mélange, ou flux : paire analysée ou relue du cache) et écrite dans un fichier **Matroska sans perte**
   (Ut Video, repli FFV1 selon la build) ;
4. le fichier est promu atomiquement dans le cache, avec son bilan ; le graphe le relit comme n'importe quelle entrée.

Un conteneur plus long que sa vidéo (une piste son plus longue) annonce des images qui n'existent pas : la dernière image est
**répétée**, comme le fait le graphe d'échantillonnage pour les mêmes ticks (jamais une erreur). Un décodage qui s'interrompt sur une
erreur, lui, reste une erreur.

Propriétés : **annulable** à chaque image (rien n'est conservé), **progression** (`faites / à faire`), un seul FFmpeg de décodage et
un d'encodage, tous supervisés (`core/process_supervisor.py`), erreurs lisibles (média introuvable, **média avec canal alpha**
refusé avec la sortie « Échantillonnage », aucun codec sans perte, décodage qui ne rend pas exactement les images demandées).

* **Export** : tout le clip, dans un fil, avec progression et annulation (`ExportEngine`, la file de rendu en hérite). La barre
  monte jusqu'à 80 % pendant le calcul des images, puis FFmpeg prend le relais (elle ne recule jamais). Une fois les images
  prêtes, le bilan est **dit** (barre d'état et panneau Export) : nombre d'images fabriquées et confiance, ou nombre d'images
  remplacées par un mélange ou l'image la plus proche. Le signal est `ExportEngine.preparation_reported`.
* **Aperçu fidèle** : seuls les ticks du **segment** sont fabriqués ; le reste du run est du noir jeté par `-ss` / `-t`. Les vecteurs,
  eux, sont en cache : le segment suivant les relit.
* **Scopes** : l'image **échantillonnée**, sans calcul (ils tournent sur le fil de l'interface) ; `build_frame_command(...,
  interpolate=True)` fabrique l'image demandée seulement.
* **Séquence imbriquée** : refusée à l'édition, au collage du temps et à l'export d'un `.kut` écrit à la main (son image n'est pas
  un fichier) ; les clips *à l'intérieur* de la séquence sont interpolés normalement.

### Analyser le flux optique

*Menu Vitesse → Analyser le flux optique…* (ou le bouton de la section *Temps*) calcule d'avance les paires du clip, dans un travail
d'arrière-plan (`core/flow_analysis.py`), à la résolution de l'export choisi dans le panneau Export (la clé d'une paire contient la
grille d'analyse) et avec le backend du réglage : barre d'état en direct, **annulable** (les paires déjà calculées restent valables),
fermeture de l'application ou changement de projet l'arrêtent. L'export et l'aperçu suivants les relisent : zéro paire à recalculer
(testé).

## Mesures

Machine de mesure : Apple Silicon (arm64), Python 3.14, NumPy seul. `python -m tools.perf.flow_bench` ; résultats complets dans
[perf/optical-flow.json](perf/optical-flow.json). Les temps dépendent de la machine ; les tests vérifient des **invariants**, pas des
temps.

<!-- BENCH:MEASURES -->
**Estimation d'une paire** (aller et retour, sous-processus isolé) :

| Image | Qualité | Grille d'analyse | Temps / paire | Crête mémoire | Stocké / paire | Confiance |
|:---|---:|---:|---:|---:|---:|---:|
| 640x360 | draft | 160x90 | 0.01 s | 64 Mo | 43 Ko | 0.99 |
| 640x360 | balanced | 320x180 | 0.05 s | 64 Mo | 169 Ko | 0.98 |
| 640x360 | best | 640x360 | 0.24 s | 108 Mo | 676 Ko | 0.98 |
| 1280x720 | draft | 320x180 | 0.04 s | 134 Mo | 169 Ko | 1.00 |
| 1280x720 | balanced | 640x360 | 0.19 s | 174 Mo | 676 Ko | 0.99 |
| 1280x720 | best | 1280x720 | 0.95 s | 344 Mo | 2.7 Mo | 0.99 |
| 1920x1080 | draft | 480x270 | 0.08 s | 249 Mo | 380 Ko | 1.00 |
| 1920x1080 | balanced | 960x540 | 0.42 s | 333 Mo | 1.5 Mo | 0.99 |
| 1920x1080 | best | 1920x1080 | 2.15 s | 728 Mo | 6.1 Mo | 0.99 |
| 3840x2160 | draft | 960x540 | 0.32 s | 873 Mo | 1.5 Mo | 1.00 |
| 3840x2160 | balanced | 1920x1080 | 1.72 s | 1352 Mo | 6.1 Mo | 1.00 |
| 3840x2160 | best | 3840x2160 | 9.57 s | 2574 Mo | 24.3 Mo | 1.00 |

**Synthèse d'une image intermédiaire** à la pleine résolution :

| Image | Flux : synthèse d'une image | Mélange simple | Crête mémoire |
|:---|---:|---:|---:|
| 640x360 | 0.05 s | 0.4 ms | 135 Mo |
| 1280x720 | 0.21 s | 1.5 ms | 451 Mo |
| 1920x1080 | 0.46 s | 4.0 ms | 964 Mo |
| 3840x2160 | 2.37 s | 19.9 ms | 3249 Mo |

**Clip de 2 s ralenti à 25 %** (240 images de sortie), de bout en bout :

| Image | Mode | Qualité | Préparation | Encodage | Images/s | Flux préparé | Crête mémoire | Dégradées |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1280x720 | sampling | - | 0.0 s | 1.3 s | 190.5 | 0 Mo | 68 Mo | 0 |
| 1280x720 | blending | - | 1.2 s | 1.3 s | 95.6 | 112 Mo | 154 Mo | 0 |
| 1280x720 | optical_flow | draft | 36.1 s | 1.3 s | 6.4 | 116 Mo | 496 Mo | 0 |
| 1280x720 | optical_flow | balanced | 47.0 s | 1.4 s | 5.0 | 115 Mo | 549 Mo | 0 |
| 1280x720 | optical_flow | best | 92.1 s | 1.3 s | 2.6 | 115 Mo | 591 Mo | 0 |
<!-- /BENCH:MEASURES -->

## Précision (scènes synthétiques à vérité terrain exacte)

`tests/flow_scenes.py` rend un objet texturé à des positions fractionnaires : l'image « vraie » à `t` est connue. Erreur moyenne
(niveaux de gris sur 255) **sur la région de l'objet**, flux contre mélange simple, moyenne de `t = ¼, ½, ¾` :

<!-- BENCH:ACCURACY -->
| Scène | Erreur flux | Erreur mélange | Recouvrement flux | Recouvrement mélange |
|:---|---:|---:|---:|---:|
| translation +3 px | 2.5 | 11.8 | 0.993 | 0.979 |
| translation +8 px | 1.2 | 23.6 | 0.997 | 0.916 |
| translation +24 px | 7.5 | 37.0 | 0.983 | 0.764 |
| diagonale (7, 5) | 4.3 | 26.5 | 0.987 | 0.892 |
| accélération | 2.8 | 22.4 | 0.994 | 0.924 |
| rotation 10°/image | 2.8 | 16.2 | 0.996 | 0.958 |
| zoom +6 %/image | 2.8 | 5.2 | 0.997 | 0.982 |
| objet sans texture | 1.7 | 8.8 | 0.999 | 0.945 |
| deux objets opposés | 3.5 | 23.9 | 0.996 | 0.912 |
| croisement | 1.7 | 19.5 | 0.997 | 0.939 |
<!-- /BENCH:ACCURACY -->

Sur un fichier réel (`tests/test_retime_prepare_real.py`, vérité à double cadence par le même chemin de conversion) le flux est à moins de
3 niveaux de la vérité, 4 fois plus précis qu'un mélange.

## Limites assumées

* **Pas d'accélérateur** : le backend est NumPy ; le coût (tableau ci-dessus) est celui d'un calcul sur processeur. À 25 %, une
  minute de 1080p30 fait environ 5 400 images à fabriquer. *Équilibrée* ou *Brouillon* pour le travail, *Maximale* pour la livraison.
* **Mémoire en 4K** : la synthèse d'une image intermédiaire à 3840 × 2160 culmine à environ 3,2 Go (mesurée en qualité *Équilibrée* ;
  la projection avant travaille en double précision) et l'analyse *Maximale* d'une paire à 2,6 Go. Sur une machine de 8 Go,
  préférez *Équilibrée* (analyse : 1,4 Go) ou *Brouillon* (0,9 Go) ; la crête ne dépend pas du nombre d'images du clip (testé).
* **Mouvements non rigides, occlusions et très grands déplacements** (au-delà de la portée de la pyramide) : l'image est un mélange
  ou l'image la plus proche, **dit** par le bilan ; jamais un mouvement inventé sans que ce soit mesurable.
* **Pas de canal alpha** : un média transparent est refusé (choisir *Échantillonnage*), jamais aplati en silence.
* **Moniteur temps réel** : il échantillonne toujours (il doit tenir la cadence) et affiche un avis discret *aperçu simplifié* quand
  un clip interpolé est sous la tête de lecture. Le mode *Auto* du moniteur (flux → mélange → échantillonnage selon la charge) n'est
  **pas** réalisé : il suppose un mélange et un flux en temps réel dans le moniteur GPU.
* **Disque** : un flux préparé fait environ 3 Mo par image en 1080p (sans perte) ; le `CacheManager` le tient sous son budget (LRU).
  Un clip en lecture inverse garde son run en mémoire dans FFmpeg (borne de 4 Gio, message explicite).
* Les images d'un clip préparé le sont dans un cadre **opaque** (RVB 8 bits, conversion de FFmpeg) : l'image exacte d'un tick est
  celle de l'échantillonnage à un niveau de gris près.

## Architecture future

Prévu par l'interface, **rien n'est commencé** :

* **Backends GPU** (Metal, CUDA, Vulkan, OpenCL, CoreML) : implémenter `OpticalFlowBackend` ; `select_backend` les essaie avant le
  processeur ; le plan, le cache, la préparation, le graphe et l'interface ne changent pas. `identity` change avec le backend : les
  vecteurs d'un autre moteur ne sont jamais relus.
* **Interpolation neuronale** : un backend dont `synthesize` fabrique l'image sans flux explicite ; `PairAnalysis` peut rester vide.
* **Flux partagé** : motion blur d'un clip remappé, stabilisation et suivi pourraient lire les mêmes `PairAnalysis` du cache.
* **Moniteur** : mélange puis flux en temps réel dans `core/gpu_composite`, avec le mode *Auto*.
