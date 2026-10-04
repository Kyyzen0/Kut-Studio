# Remappage temporel

La vitesse d'un clip n'est plus un nombre : c'est une **courbe**. Un ralenti à 25 %, une rampe Bézier de 100 % à 20 % puis
un arrêt, un retour en arrière (`100 % → 0 % → −100 %`) sont le même objet, calculé par le même code, de l'aperçu à l'export.

> Clic droit sur un clip → **Vitesse** → *25 %* → *Images intermédiaires* → *Flux optique*. Même moteur que la rampe Bézier
> du Graph Editor : le menu ne fait que poser une vitesse statique ou des points `time.speed`.

Les images intermédiaires (mélange d'images, flux optique) ont leur propre document : [optical-flow.md](optical-flow.md).

## Le modèle : une seule source de vérité

`TimeMap` (`core/time_map.py`) répond à une seule question : **quel instant du média montre le clip à l'instant `t` de la
timeline ?**

```
v(t) = sens × f(t)          f = courbe time.speed (keyframes) ou vitesse statique ; sens = −1 si « inverse »
M(t) = ancre + ∫₀ᵗ v(s) ds  borné à la fenêtre source [source_in, source_out]
durée = premier instant où M franchit une borne de la fenêtre, vers l'extérieur
```

* L'intégrale est **exacte** : chaque segment est constant, ou un polynôme (linéaire, ease, Bézier : cubique en vitesse,
  quartique en position) intégré analytiquement. Les retournements (`v` change de signe) et les arrêts sont des racines de
  polynômes trouvées en Python pur, sans bibliothèque.
* La **durée n'est jamais stockée** : elle se déduit de la courbe (la source est épuisée). *Toucher* une borne n'est pas la
  *franchir* : une rampe qui frôle la fin du média et repart ne coupe pas le clip. Une durée imposée (après une coupe) ne peut
  que raccourcir ce que la courbe donne.
* `TimeMap.runs()` découpe le clip en **runs** monotones (avant, arrière, arrêt) ; `pieces()` approche chaque run par des
  morceaux à vitesse constante dont les extrémités sont exactes (tolérance : 2 % d'image).
* Un clip **sans courbe, sans ancre et sans durée imposée** est un `ConstantTimeMap` qui reproduit **au bit près** les formules
  historiques : un ancien `.kut` à `speed = 2.0` rend exactement le même montage.

Tout consommateur du temps lit ce mapping : trims, coupes, slip / slide / roll, évaluateur de timeline, séquences imbriquées,
Multicam, suivi, plan de rendu, graphe d'export, empreintes. Plus aucun `source_in + t × vitesse` dans le code.

### Espaces de temps

| Espace | Origine | Qui l'utilise |
| --- | --- | --- |
| **Timeline** (de la séquence) | début de la séquence | placement des clips, transitions, marqueurs, sous-titres, mixage |
| **Local au clip** | début du clip sur la timeline | keyframes (transform, effets, masques, courbe de vitesse), fondus, `t` des expressions FFmpeg |
| **Source** | secondes du média (ou sortie d'une séquence imbriquée) | `source_in` / `source_out`, décodage, données de suivi, flux optique |
| **Enfant** | sortie de la séquence enfant | un clip imbriqué lit le temps de l'enfant **à travers son `TimeMap`** |

Règle d'or : les keyframes d'un clip sont en **temps local** (sur la timeline). La vitesse déforme *quelle image du média* on
voit, pas *quand* arrive une animation de position ou d'opacité. Ainsi couper un clip sépare sa courbe de vitesse et ses
animations sans en changer une valeur.

## Vitesse animable : la propriété `time.speed`

`time.speed` est une propriété animable comme les autres (`core/time_targets.py`) : mêmes interpolations (palier, linéaire,
ease in/out, Bézier avec tangentes liées ou séparées), même Graph Editor, mêmes raccourcis, même annulation. Particularités :

* valeur **avant** application du sens : *inverse* retourne toute la courbe ; elle peut être négative (la lecture repart en
  arrière) et nulle (arrêt) ; bornes ±10× ; la vitesse **statique** est comprise entre 5 % et 1000 % ;
* le Graph Editor l'affiche **en pourcentage** (`AnimatableProperty.display_scale` / `display_unit` : le stockage reste un
  facteur) ;
* applicable aux pistes vidéo et audio, pas à un clip figé.

## Éditer

Toutes les opérations (`core/time_ops.py`, `core/time_presets.py`, `core/timeline_operations.py`) sont **transactionnelles** :
un clip refusé (durée dégénérée, piste verrouillée, clip figé) est laissé tel qu'il était. Chaque geste est **une entrée
d'historique**, même sur une sélection multiple.

| Opération | Effet |
| --- | --- |
| Vitesse constante (25 %, 50 %, 100 %, 200 %, 400 %, champ numérique) | vitesse statique ; remplace une courbe existante |
| Sens inverse | retourne toute la courbe |
| Point de vitesse | keyframe `time.speed` à la tête de lecture, **sans changer la courbe** ; se règle ensuite dans le Graph Editor |
| Arrêt sur image dans la courbe | palier à 0 % de 1 s : l'image de la tête de lecture reste, le clip s'allonge, le reste de la courbe est décalé sans être déformé ; la reprise a la vitesse d'avant |
| Rampe d'entrée / de sortie | départ (ou fin) au quart de la vitesse du clip, adouci (ease in/out) pendant 1 s (au plus la moitié du clip) |
| Copier / coller le temps | réglages et courbe sur un autre clip, qui garde sa portion de média |
| Supprimer la courbe | vitesse statique ; la portion de média parcourue est conservée |
| Réinitialiser | vitesse 100 %, sens normal, sans courbe ni choix d'interpolation ou d'audio |

**Ripple** (`RippleMode`) quand une édition de vitesse change la durée : `SOURCE` (défaut) garde la portion de média, la durée
sur la timeline change ; `TIMELINE` garde la durée, la portion de média utilisée change (limitée par la fin du média).

### Couper, rogner, déplacer

Couper ou rogner un clip à courbe, c'est **restreindre le mapping** à un intervalle (`core/time_editing.py`) : fenêtre =
l'étendue réellement parcourue, ancre = `M(t₀)`, durée imposée seulement si la dérivation naturelle ne la reproduit pas.
**Propriété centrale, vérifiée avec le vrai FFmpeg : couper ou rogner un clip à courbe ne change aucune image de la séquence
exportée.** Slip décale l'ancre ; slide et roll sont refusés sur un clip à courbe (message clair) plutôt que de déformer le
montage.

## Export exact

`core/retime_graph.py` traduit le `TimeMap` en filtres FFmpeg, une seule fois, pour l'export, l'aperçu fidèle et les scopes
(`ExportEngine._build_filter_complex`).

* **Règle de sélection** (échantillonnage) : l'image montrée au tick `k` est la plus proche de `M(k / fps)` ; à égalité, la
  suivante. `setpts` + `fps` n'a jamais eu cette propriété (à 2× il montre 1, 3, 5… au lieu de 0, 2, 4…).
* Chaque image source reçoit l'instant **exact** de sa première apparition (calculé sur le compteur d'images `N`, base de temps
  à la microseconde) ; `fps` n'a plus qu'à la tenir. Le flux produit a exactement `⌈durée × fps⌉` images.
* **Décalage d'une couche** sur la timeline : `setpts` **tronque** son résultat à un entier de la base de temps ; après le
  conformage `fps` cette base vaut `1/cadence` et une erreur d'un millionième faisait apparaître une couche une image trop tôt.
  Une garde d'un millième de tick (`OFFSET_GUARD_TICKS`) corrige ce défaut, qui touchait aussi les clips non remappés coupés
  sur une image.
* Lecture **inverse** : `reverse` garde toutes les images d'un run en mémoire ; borne `MAX_REVERSE_BYTES` (4 Gio, ≈ 45 s en
  1080p30) avec un message qui dit quoi faire (couper le clip) plutôt qu'une panne.
* À **égalité exacte** (la position tombe pile entre deux images) l'approximation par morceaux peut choisir l'image voisine :
  jamais plus d'un cran, et jamais ailleurs qu'à une égalité (tests `ties=True`).

### Audio

* `preserve_pitch` (défaut, historique) : `atempo` par étapes de [0,5 ; 2] ; sinon `asetrate` + `aresample` (effet bande : la
  hauteur suit la vitesse). Mesuré par FFT sur du son réel (`tests/test_retime_audio_real.py`).
* Une rampe est coupée en morceaux à vitesse constante (tolérance 5 ms), enchaînés par un fondu de 5 ms ; un arrêt, ou une
  vitesse sous 5 %, devient un silence de la bonne durée ; `areverse` par morceau ; durée exacte.
* `remap_audio = False` : seule la vidéo change de vitesse, le son joue à vitesse normale depuis `source_in` (montages sur
  musique).

## Interface

* **Menu** *Vitesse* (clic droit) : vitesses, sens inverse, courbe (point, arrêt, préréglages, suppression), images
  intermédiaires, qualité, analyse, son, copier / coller, réinitialiser.
* **Inspecteur**, groupe *Vitesse et durée* : vitesse, préréglages, sens, arrêt sur image **et**, dans la section *Temps*,
  images intermédiaires, qualité du flux, hauteur du son, son qui suit la vitesse, courbe (nombre de points, ajouter, supprimer),
  analyse du flux optique.
* **Timeline** : une courbe discrète sur le clip (échelle logarithmique, 100 % en pointillé, un point par keyframe) et des badges
  (vitesse ou vitesse moyenne `~0.6x`, `R`, `F`, `MIX`, `FLUX`).
* **Graph Editor** : vitesse en pourcentage, tous les gestes d'édition de courbe. Pour `time.speed`, **chaque geste** (glisser un
  point ou une poignée, champs temps / valeur, ajout, interpolation, mode de tangente) passe par `core.time_ops` : durée
  minimale, politique de ripple choisie, keyframes d'après la nouvelle fin, refus dit dans la barre d'état et état d'avant
  restauré — jamais par les mutateurs génériques de keyframes, qui ignorent que la durée en dépend. Un glissement est
  **idempotent** : à chaque étape le clip est remis dans son état du début du geste, puis le déplacement total est appliqué ;
  raccourcir le clip en cours de geste (ce qui coupe les keyframes d'après la fin) ne fait donc rien perdre si le geste le
  rallonge ensuite (`ui/graph_editor_time.py`).
* **Raccourcis** configurables : *Ajouter un point de vitesse*, *Arrêt sur image à la tête de lecture* (catégorie *Temps*, sans
  touche par défaut).

## Cohabitation avec le reste

| Fonction | Comportement |
| --- | --- |
| **Séquences imbriquées** | la sortie du clip est une source temporelle ; la séquence enfant n'est jamais modifiée et reste rendue une fois ; mélange d'images et flux optique refusés (l'image n'est pas un fichier) |
| **Multicam** | une bascule d'angle coupe le segment comme n'importe quel clip : l'instant de l'enfant reste continu, rampe comprise ; aplatir refuse un segment à courbe ou à choix de temps propres |
| **Suivi, stabilisation** | les données sont en **temps source** (jamais recalculées) ; le clip lié lit le mapping, donc suit la rampe image pour image |
| **Transitions** | le recouvrement vit à l'intérieur des durées des deux clips : aucune image avant le début, après la fin du média, ni tenue en trop |
| **Sous-titres** d'une séquence imbriquée | remontés à la timeline parente par `timeline_intervals_of` : une phrase ralentie dure plus longtemps |
| **Motion graphics** | un calque graphique a son temps local ; la vitesse ne s'applique pas à un calque |
| **Proxies** | l'aperçu peut lire un proxy ; le temps (indices d'images) est celui de l'original (un proxy a la même cadence et les mêmes images) ; l'export lit toujours les originaux |
| **Aperçu fidèle** | même graphe que l'export ; l'empreinte d'un segment contient le mapping et, pour un clip interpolé, la version du moteur d'images |

## Format `.kut`

`CURRENT_VERSION` reste 16 : tout ce qui est nouveau est **optionnel** et n'est écrit que s'il diffère de la valeur par
défaut. `time_remapping` : `speed`, `reverse`, `freeze_*` (inchangés) + `interpolation` (`blending`, `optical_flow`),
`flow_quality`, `preserve_pitch` (écrit si faux), `remap_audio` (écrit si faux), `anchor`, `duration`. La courbe de vitesse
est dans `animation` (`property_name = "time.speed"`). La lecture est tolérante champ par champ (valeur inconnue : défaut).
Aucun cache n'est dans le projet : ni vecteurs de mouvement, ni images intermédiaires.

*Compatibilité ascendante non garantie* : un `.kut` écrit avec une courbe de vitesse, ouvert par une version qui ne connaît pas
`time.speed`, perdrait la courbe.

## Limites assumées

* Le **moniteur temps réel** (lecteur Qt) ne rejoue pas le mapping en lecture continue hors vitesse normale ; l'image qui fait
  foi est celle de l'aperçu fidèle, puis de l'export. Un avis discret le dit quand un clip interpolé est sous la tête de lecture.
* Slide et roll sont refusés sur un clip à courbe ; l'aplatissement Multicam refuse une vitesse animée.
* Un segment non linéaire qui précède un arrêt inséré est ré-adouci jusqu'au palier (mêmes valeurs aux extrémités).
* Pas de reprise d'un `.kut` à courbe par une version antérieure.

## Tests

`test_time_map` (43, intégrale contre une quadrature de Gauss-Legendre), `test_speed_curve_edits`, `test_time_ops`,
`test_time_presets`, `test_time_commands`, `test_slide_roll_remapped`, `test_retime_graph` et `test_retime_export_real`
(le vrai FFmpeg : image par image contre le modèle, coupes, transitions), `test_retime_audio_real` (fréquences mesurées),
`test_time_integration` (séquences imbriquées, Multicam, suivi, sous-titres sur une rampe), `test_time_ui`, `test_time_overlay`,
`test_project_io_time_remapping`.
