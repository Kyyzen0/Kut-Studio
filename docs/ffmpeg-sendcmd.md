# Animer un filtre FFmpeg par `sendcmd` : validation (ADR-0008, étape 1)

Mesuré le 2026-10-18 avec `tools/validate_sendcmd.py` (aucun code applicatif touché). Question : pour les filtres dont
l'option n'accepte pas une expression du temps, un fichier `sendcmd` qui change l'option à chaque image donne-t-il, à
l'image `n`, **la même sortie** que le filtre statique réglé sur la valeur `v(n)` ?

## Méthode

- Entrée : mire `testsrc2` + bruit à graine fixe, 320x180, 3 s, `yuv420p`, encodée sans perte (FFV1) **une fois** par
  FFmpeg 9.0.2 et réutilisée par les trois versions (même octets en entrée). Une seconde entrée à 30000/1001 i/s.
- Chaînes recopiées de l'application (`core/export_engine.py`, `core/color_wheels.py`, `core/color_render.py`) : mêmes
  filtres, même ordre, conversion 4:2:0 vers RVB co-sitée de `core/chroma_siting.py` devant les filtres RVB.
- Animé : `sendcmd=f=cmds.txt` puis le filtre nommé `filtre@t`, une ligne par image, valeur en rampe linéaire.
  Statique : même entrée, même amont, `select='eq(n,N)'`, filtre écrit avec `v(N)`. Comparaison octet à octet.
- Images comparées : 0, 1, 2, 15, 30, 59, 89 et la dernière (scénario de base), 4 images pour les scénarios de bord.
- Critère : **max <= 1 niveau** sur toutes les images comparées.
- Versions : FFmpeg **9.0.2** (macOS, `/opt/homebrew/bin/ffmpeg`), **7.1.5-0+deb13u1** (Debian trixie) et
  **6.1.1-3ubuntu5** (Ubuntu 24.04), les deux dernières en Docker `--platform linux/amd64`. Chaque cas est rejoué avec
  `-cpuflags 0` (chemins C, sans SIMD) sur les trois versions, scénarios `base`, `app_like` et `ntsc_2997`.

Rejouer :

```sh
python tools/validate_sendcmd.py run --work DIR --out resultat.json            # un FFmpeg local
python tools/validate_sendcmd.py run --work DIR --out c.json --cpuflags0       # sans SIMD
python tools/validate_sendcmd.py run --work DIR --out k.json --cost --probes --scenarios none
python tools/validate_sendcmd.py report a.json b.json c.json                   # tableau
```

Dans Docker : `docker run --rm --platform linux/amd64 -v "$PWD":/w:ro -v SCRATCH:/s IMAGE python3
/w/tools/validate_sendcmd.py run --ffmpeg ffmpeg --work /s --out /s/r.json` (image `ubuntu:24.04` ou `debian:trixie`
avec `apt-get install ffmpeg python3 python3-numpy`).

## Résultat : filtre x paramètre x version

Écart maximal mesuré, en niveaux (scénario de base, instants « en avance », voir plus bas). Mêmes valeurs avec
`-cpuflags 0`. **VALIDÉ** = max <= 1 sur toutes les images comparées. Les scénarios de bord (décalage, cadence) ne sont
joués que sur un cas par filtre : exposition, `eq` contraste, `hue` h, ombres, température, `curves`, `gblur` (`yuv420p`),
`rgbashift`, bloom (rayon) ; les autres paramètres du même filtre ne l'ont été qu'au scénario de base et à `app_like`
(sauf roues, `eq` saturation / luminosité, `gblur` `gbrp`, gain du bloom, `hue` s, hautes lumières : base seulement).

| Filtre (usage) | Paramètre animé | 9.0.2 | 7.1.5 | 6.1.1 | Écart max |
|---|---|---|---|---|---|
| `lutrgb` (exposition) | stops, expression en entier sur r, g, b | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `lutrgb` (roues) | pente, origine, exposant (r, g, b) | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `lutrgb` (bloom) | gain `lutrgb@gl` dans un graphe `split` / `blend` | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `eq` | `contrast`, `saturation`, `brightness` | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `hue` | `h`, `s` | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `colorbalance` | ombres (`rs gs bs`), hautes lumières (`rh gh bh`) | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `colorchannelmixer` | température (`rr gg bb`) | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `curves` | `master`, un point de la courbe (3 points, nombre constant) | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `gblur` (flou d'effet, `yuv420p`) | `sigma` **et** `sigmaV` | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `gblur` (flou de nœud, `gbrp`) | `sigma` **et** `sigmaV` | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `gblur` (bloom) | `sigma` + `sigmaV` de `gblur@gb` dans le graphe complexe | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `rgbashift` | `rh`, `bh` (entiers) | VALIDÉ | VALIDÉ | VALIDÉ | 0 |
| `unsharp` | `luma_amount` | **NON VALIDÉ** | **NON VALIDÉ** | **NON VALIDÉ** | 101 |
| `vignette` | `angle` (avec ou sans `eval=frame`) | **NON VALIDÉ** | **NON VALIDÉ** | **NON VALIDÉ** | 157 |
| `geq` | `lum` | **NON VALIDÉ** | **NON VALIDÉ** | **NON VALIDÉ** | 125 |
| `lut3d` | `file` | **NON VALIDÉ** | **NON VALIDÉ** | **NON VALIDÉ** | 255 |

Les quatre non validés sont **ignorés sans message** : FFmpeg accepte la commande (`sendcmd` la journalise), ne
renvoie aucune erreur, mais la sortie ne bouge pas (sondes `lut3d.file` et `geq.lum` : la dernière image égale la
sortie d'avant la commande, écart 255 et 144 avec la sortie d'après). Piège : un test qui ne regarde que le code de
sortie de FFmpeg les croirait bons.

### Liste finale : paramètres animables par `sendcmd` sur 6.1, 7.1 et 9.0

`lutrgb` (exposition, roues, gain du bloom), `eq` (`contrast`, `saturation`, `brightness`), `hue` (`h`, `s`),
`colorbalance` (ombres, hautes lumières), `colorchannelmixer` (température), `curves` (`master`, voir limites),
`gblur` (`sigma` + `sigmaV`, toutes variantes), `rgbashift` (`rh`, `bh`).

**Exclus de la voie `sendcmd`** (ces filtres acceptent la commande sans erreur et ne changent rien à la sortie) :
`unsharp` (netteté de l'effet Netteté), `lut3d`, `vignette` (`angle`) et `geq`. Ils se répartissent en deux cas :
- `unsharp` et `lut3d` n'ont **aucune autre voie** (`luma_amount` ne prend pas d'expression, `file` non plus) : leurs
  paramètres ne sont **pas animables** et ne doivent pas être enregistrés dans le registre d'animation.
- `vignette` et `geq` s'animent **par expression** (voir plus bas) : ils sont exclus de `sendcmd` **seulement**, et
  restent enregistrables dans le registre d'animation, avec un rendu par expression.
La netteté de nœud est une `convolution` (non testée ici : ses coefficients ne se commandent pas, voir « Non vérifié »).

## Comportement aux limites

Tous mesurés sur les trois versions (identiques) :

| Cas | Résultat |
|---|---|
| Commande à t = 0 | **Appliquée à l'image 0.** |
| Commande à l'instant exact de l'image (`n / cadence`, 9 décimales, ou tronqué à la microseconde) | **Fragile** : l'image 1 reçoit la valeur de l'image 0 (écart max 2 en exposition, 5 en teinte), les images 0, 45 et 89 sont exactes. Cohérent avec l'arrondi à la microseconde de l'instant de l'image (33333 µs) contre l'instant de la commande (33333,3 µs) : une commande légèrement après l'image part à l'image suivante, une fois sur trois à 30 i/s. |
| Commande une **demi-image avant** (`(n - 0,5) / cadence`) | **Exacte** (écart 0) sur toutes les images, à 24, 29,97, 30 et 60 i/s. C'est la forme à utiliser. |
| Cadence non entière (29,97 = 30000/1001) | Exact, avec commandes à `(n - 0,5) * 1001 / 30000`. |
| Entrée cherchée par `-ss 1` placé avant `-i` | Les instants de `sendcmd` sont **relatifs au flux du filtre** (ils repartent de 0 avec la sortie) : aucun décalage. Exact. |
| `trim=start=1:end=3,setpts=PTS-STARTPTS` en amont | Idem, relatifs au flux : exact, **c'est le cas de l'application** (`trim`, `setpts`, `fps`, `setpts` en amont du filtre). Scénario `app_like` : exact. |
| `trim=start=1` **sans** remise à zéro de `PTS`, ou `-ss 1 -copyts` | Le flux du filtre commence à t = 1 : toutes les commandes antérieures partent à la première image et la dernière gagne (écart 120 en exposition, 239 en teinte sur les premières images). **Il faut ajouter le décalage** (`PTS` de la première image) aux instants : avec `shift=1,0`, exact. |
| `fps=24`, `fps=60` en amont | Exact si les commandes suivent la cadence de **sortie** de `fps` (une par image émise). |
| `fps=30` depuis 29,97 | Exact (une commande par image de sortie, 30 i/s). |
| Valeurs entières (`rgbashift`) | Arrondies avant écriture, comme l'application : exact. |

Règle : les instants de `sendcmd` sont ceux de `PTS` **à l'entrée de `sendcmd`**, dans la base de temps du flux à cet
endroit. L'application remet `PTS` à zéro avant ses effets (`setpts=PTS-STARTPTS` après `fps`) ; `PTS+timeline_start`
n'est posé qu'à la fin de la chaîne et ne touche donc pas `sendcmd`. Un chemin qui ne remettrait pas `PTS` à zéro
(retemporisation, calque imbriqué) doit être testé avec `app_like` / `trim_noreset_*` avant de s'y fier.

## Coût

Fichier de commandes pour **60 s à 30 i/s** (1800 images), une ligne par image :

| Paramètre | Taille |
|---|---|
| exposition (`lutrgb`, 3 expressions de ~380 caractères) | 2,07 Mo |
| roue (`lutrgb`, 3 expressions de ~90 caractères) | 0,51 Mo |
| bloom (gain) | 0,27 Mo |
| température | 0,20 Mo |
| ombres / hautes lumières | 0,18 Mo |
| contraste, saturation, luminosité (`eq`) | 0,14 Mo |
| `gblur`, `curves`, `rgbashift`, `hue` | 0,06 à 0,11 Mo |

Temps de rendu, 1280x720, 150 images, `-f null`, meilleur de trois ; animé / statique :

| Cas | 9.0.2 (macOS) | 7.1.5 (amd64 émulé) | 6.1.1 (amd64 émulé) |
|---|---|---|---|
| `lutrgb` exposition | **30,7 s / 0,31 s** | 55,1 s / 0,54 s | 43,6 s / 0,50 s |
| `lutrgb` roue (pente) | **11,0 s / 0,27 s** | 18,9 s / 0,46 s | 15,6 s / 0,43 s |
| `eq` contraste | 0,11 / 0,06 s | 0,21 / 0,20 s | 0,20 / 0,18 s |
| `hue` | 0,08 / 0,06 s | 0,47 / 0,20 s | 0,46 / 0,18 s |
| `colorbalance` | 0,45 / 0,44 s | 0,79 / 0,77 s | 0,77 / 0,76 s |
| `colorchannelmixer` | 0,30 / 0,30 s | 0,52 / 0,50 s | 0,51 / 0,49 s |
| `curves` | 0,25 / 0,24 s | 0,43 / 0,42 s | 0,42 / 0,41 s |
| `gblur` | 0,33 / 0,33 s | 0,48 / 0,46 s | 0,46 / 0,45 s |
| `rgbashift` | 0,39 / 0,38 s | 0,73 / 0,72 s | 0,76 / 0,76 s |
| `gblur` du bloom (graphe complexe) | 1,24 / 1,24 s | 1,69 / 1,70 s | 1,74 / 1,72 s |

**Les tables de couleur de `lutrgb` sont le seul vrai coût** : à chaque commande, FFmpeg recalcule sans doute la table
(cause non vérifiée dans le code de FFmpeg). Mesuré à 9.0.2 : environ **0,2 s par image pour l'exposition** et 0,07 s pour une roue, **quelle
que soit la résolution** (60 images : 12,3 s à 320x180 comme à 1920x1080, contre 0,1 à 0,3 s en statique). Une
exposition animée de 60 s à 30 i/s ajouterait de l'ordre de six minutes à l'export, une roue animée environ deux
minutes (extrapolation linéaire des 150 images mesurées, non mesurée sur 1800). Les autres filtres coûtent le même
temps qu'en statique, à l'erreur de mesure près (le surcoût de `hue` sous Docker, 0,47 contre 0,20 s, n'est pas
reproduit sur macOS, 0,08 contre 0,06 s : non expliqué). Les temps 7.1 et 6.1 sont émulés (amd64 sur arm64) : ils comparent animé et
statique, pas les versions entre elles.

## Expression `eval=frame` contre `sendcmd` (`eq`, `hue`, `vignette`, `geq`)

Expression de rampe en `n` (index d'image) ou `t` (instant) placée dans l'option, contre `sendcmd` au même filtre.

| Filtre | Expression `n` | Expression `t` | `sendcmd` |
|---|---|---|---|
| `eq` (`eval=frame`) | exacte (0), tous scénarios, 3 versions | exacte (<= 1) sauf PTS non remis à zéro (63) | exacte (0) avec instants en avance |
| `hue` | exacte (0), tous scénarios | exacte (<= 1) sauf PTS non remis à zéro (239) | exacte (0) |
| `vignette` (`eval=frame`) | exacte (<= 1), tous scénarios | exacte (<= 1) sauf PTS non remis à zéro (110) | **ignorée** (157) |
| `geq` (`N` / `T`) | exacte (<= 1) | exacte (<= 1) | **ignorée** (125) |

Conclusion : **l'expression en `n` est la plus fiable** sur les trois versions. Elle compte les images reçues par le
filtre : indépendante de `PTS`, de `-ss`, de `trim` non remis à zéro et du changement de cadence en amont. L'expression
en `t` et `sendcmd` dépendent du `PTS` du flux : exactes tant qu'il repart de zéro (le cas de l'application). Pour `eq`
et `hue`, les deux voies sont valables ; pour `vignette` et `geq`, **seule l'expression** fonctionne. L'écart de 1 niveau
de `vignette` et `geq` est identique sur les trois versions (cause non recherchée).
L'expression en `n` compte à partir de la première image que reçoit **ce** filtre.

## Pièges rencontrés

1. **`gblur` : commander aussi `sigmaV`.** `sigmaV` vaut -1 (« comme `sigma` ») et est résolu à l'initialisation :
   avec la seule commande `sigma`, le flou vertical reste figé (écart jusqu'à 97 niveaux). Envoyer `sigma` et `sigmaV`.
2. **Échec silencieux.** `unsharp`, `vignette`, `geq`, `lut3d` acceptent la commande sans erreur et ne font rien. Toute
   cible de `sendcmd` doit être couverte par un test qui compare la sortie, pas par le code de sortie de FFmpeg.
3. **Instants exacts : décalage d'une image** une fois sur trois. Écrire chaque commande une demi-image avant l'image
   visée (`(n - 0,5) * den / num`, sauf `n = 0` : `0`). Les neuf décimales ne changent rien.
4. **Temps relatifs au flux du filtre.** Voir « Comportement aux limites » : `trim` ou `-copyts` sans remise à zéro de
   `PTS` exigent un décalage, sinon la rampe saute à la fin de l'intervalle.
5. **Nommer les filtres** `filtre@nom` : la cible d'une commande est ce nom, indispensable dans un graphe complexe
   (le bloom contient `lutrgb@gl` et `gblur@gb` dans un sous-graphe `split` / `blend`).
6. **Apostrophes conservées.** Dans le fichier, `r 'clip(a,b)'` (apostrophes comme dans une chaîne de filtres) est
   interprété correctement (mesuré). Sans apostrophes, les virgules et points-virgules de l'expression seraient pris pour
   des séparateurs de commande (non essayé).
7. **Les commandes de `lutrgb` coûtent** (voir plus haut). Une exposition animée est la seule voie lente.
8. **`sendcmd` devant le filtre** (c'est la seule disposition mesurée) : il envoie la commande en traitant l'image,
   avant de la transmettre au filtre.

9. **La mémoire de `sendcmd` sur FFmpeg 6.1 et 7.1** (trouvé à l'étape 6, mesuré sur 6.1.1 et 7.1.5, `docs/adr/0008-*.md`). Un fichier de
   commandes est lu en entier à l'initialisation avec une mémoire de l'ordre de **1000 fois sa taille** (pic) : 800 lignes de 4,7 Ko
   (3,7 Mo) font monter FFmpeg 7.1.5 à **4,4 Go avant la première image** (6.1.1 : pareil ; 9.0.2 : 19 Mo). Deux conséquences :
   (a) la mémoire du pic est **rendue** après la lecture de chaque fichier : couper les commandes en fenêtres (un `sendcmd` par fenêtre,
   `core/grade_render.py`) ramène le pic de 4,4 Go à 164 Mo (8 fenêtres de 100 lignes) ; (b) il reste, après lecture, ~40 à 45 fois la
   taille des fichiers en mémoire résidente (7.1.5 : 1,98 Go pour 41,8 Mo de commandes ; 9.0.2 : 124 Mo). L'étalonnage animé borne donc le
   total à 32 Mo. Les fichiers courts (flou, aberration chromatique, rayon du Bloom : une instruction de ~60 octets par image) coûtent
   ~50 Ko de mémoire par instruction sur 6.1 / 7.1 (7 200 instructions : 427 Mo sur 7.1.5, 413 Mo sur 6.1.1) : un flou animé de dix minutes
   approcherait le gigaoctet sur ces versions, sans fenêtres (non traité à l'étape 6).

## Méthode à utiliser

- Un fichier de commandes par segment rendu, une ligne par image : `<instant> <cible> <option> <valeur>[, <cible>
  <option> <valeur>]*;`, instant en secondes, neuf décimales, une demi-image avant l'image visée (zéro pour la
  première), calculé avec la cadence **à l'entrée de `sendcmd`** et décalé du `PTS` de la première image si le flux ne
  repart pas de zéro.
- Filtre nommé `nom@étiquette`, placé **juste après** `sendcmd=f=<fichier>` ; les valeurs initiales de l'option sont
  celles de l'image 0.
- Écrire la valeur comme la chaîne statique le ferait (mêmes apostrophes, mêmes arrondis), tous les canaux d'un même
  réglage dans la même ligne.
- Quand le filtre accepte une expression en `n` (`eq` avec `eval=frame`, `hue`, `vignette`, `geq`), préférer
  l'expression : pas de fichier, pas de dépendance au `PTS`.
- Valider chaque nouvelle cible avec `tools/validate_sendcmd.py` sur les trois versions avant de l'enregistrer.

## Non vérifié

- Les binaires FFmpeg livrés avec l'application sous Windows et macOS (seules trois versions des distributions
  Linux et de Homebrew ont été mesurées) ; les entrées 10 bits, 8K, le décodage matériel.
- `-cpuflags 0` sous Docker amd64 émulé sur arm64 : l'émulateur n'expose peut-être pas AVX2, la comparaison SIMD / C
  y est donc moins probante qu'en natif.
- `curves` : seul le canal `master`, avec un nombre de points constant ; `red`, `green`, `blue` empruntent le même
  chemin de code mais n'ont pas été mesurés, un nombre de points qui change en cours de route non plus.
- Plusieurs cibles différentes dans une même ligne (le bloom les commande sur deux lignes de cas séparées).
- Durées de plusieurs minutes : le coût de `lutrgb` est extrapolé de 150 images, pas mesuré sur 1800.
- La netteté de nœud (`convolution`) : ses coefficients ne sont pas des options commandables.

## Utilisation dans l'application (étape 5)

Flou (`gblur`), aberration chromatique (`rgbashift`) et rayon du Bloom (`gblur`) sont animés par `sendcmd` à l'export et à l'aperçu fidèle : `core/effect_sendcmd.py`. Le coût mesuré avec le vrai graphe (1080p, 150 images) est celui du statique, sur les trois FFmpeg : voir l'ADR-0008, « Résultats de l'étape 5 », et `tools/measure_sendcmd_cost.py`. L'exposition et les roues (`lutrgb`, 0,2 s et 0,07 s par commande) n'y sont pas : l'étape 6 les anime par `curves` (voir ci-dessous).

## Utilisation dans l'application (étape 6) : l'étalonnage

`core/grade_render.py` : ombres et hautes lumières (`colorbalance`), température (`colorchannelmixer`), exposition et roues (`curves`)
sont commandés par **un seul fichier** pour tout l'étalonnage d'un clip, plusieurs cibles dans la même ligne (`colorbalance@v0cgb rs …,
colorbalance@v0cgb gs …, colorchannelmixer@v0cgt rr …`) ; le premier `sendcmd` est posé devant le premier filtre qu'il pilote.
Contraste, saturation et teinte passent par expression (`eq` en `eval=frame`, `hue`).

**`curves` avec 256 points par commande** (validé pour l'étape 6 sur FFmpeg 6.1.1, 7.1.5 et 9.0.2) :

- `master`, `red`, `green` et `blue` se commandent (`curves@nom red '0.000000/0.001961 0.003922/…'`) ; les points peuvent changer de valeur à
  chaque image (leur nombre reste constant) ; les guillemets simples du fichier protègent les espaces ;
- `curves` refuse la commande entière si `(int)(x·255)` ne croît pas strictement d'un point au suivant : l'abscisse du niveau `i` est
  écrite **juste au-dessus** de `i/255` (division entière exacte, jamais arrondie vers le bas) ;
- la table de sortie est tronquée : l'ordonnée est le centre de la case (`niveau + 0,5`, ramené à 1 pour le blanc). Avec ces deux règles,
  la sortie de `curves` égale celle du `lutrgb` de l'export fixe, **niveau par niveau** (écart 0 sur 256 niveaux, exposition et roues,
  `tools/validate_fx_render.py`, cas `tables`) ;
- coût : une commande `curves` à 256 points coûte le même temps que le filtre fixe, alors qu'une commande `lutrgb` recalcule sa table (voir
  l'ADR-0008, « Résultats de l'étape 6 », `tools/measure_grade_cost.py`) ;
- taille : ~4,7 Ko par image et par table (17 octets par point). Les fichiers sont écrits **par fenêtres de 192 Ko** et le total est borné à
  32 Mo : voir le piège 9 (mémoire de `sendcmd` sur 6.1 / 7.1).
