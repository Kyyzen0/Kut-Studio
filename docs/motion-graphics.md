# Motion graphics et compositing

Kut-Studio compose des **calques** (textes, formes, aplats, images,
groupes, contrôleurs, calques d'effets) au-dessus du montage vidéo, avec une
transformation complète (point d'ancrage, échelle X/Y, inclinaison, miroirs),
le parentage, les groupes, des masques multiples, les modes de fusion, les
adjustment layers et le flou de mouvement. Tout est non destructif, animable
par le moteur d'images-clés (`docs/animation.md`), annulable, et rendu
**par le même code** dans l'aperçu fidèle et à l'export.

Ce n'est pas un logiciel nodal : la pile reste une liste de calques, lisible
dans le panneau **Calques**, et chaque calque reste un clip de la timeline.

## Pour l'utilisateur

Les cinq gestes du débutant :

1. **Ajouter un texte** : section *Graphiques* du panneau Médias → **Titre**
   (ou menu *Calques → Ajouter*). Il apparaît à la tête de lecture, centré.
2. **Le déplacer dans le viewer** : glisser le cadre ; les coins
   redimensionnent, la poignée ronde fait tourner, le point jaune est
   l'ancrage. Le calque s'aimante au centre et aux bords du cadre, aux
   guides et aux autres calques (Alt : sans aimantation, Maj : axe / pas de 15°).
3. **Taille et couleur** : inspecteur, onglet *Graphiques*.
4. **Une petite animation** : losange ◆ d'une propriété (opacité, position…),
   déplacer la tête de lecture, changer la valeur.
5. **Une ombre ou un effet** : section *Ombre et fond* du texte, ou un effet
   de la bibliothèque (flou, couleur…).

Les outils avancés restent repliés tant qu'on ne les ouvre pas :
*Transformation avancée* (ancrage, échelle X/Y, inclinaison, miroirs),
sections *Forme*, *Contour*, *Ombre et fond*, *Calque* (parent, flou de
mouvement), *Compositing* (masques, mode de fusion), panneau *Calques*
(groupes, parentage, ordre, visibilité, verrou, presets).

Le menu **Calques** regroupe : ajout (texte, forme, contrôleur, calque
d'effets), grouper `Ctrl+G` / dégrouper `Ctrl+Maj+G`, copier / coller les
calques, copier / coller les attributs (transform, effets, masques,
animation), presets, repères du viewer (zones de sécurité `Ctrl+'`, guides
`Ctrl+;`, grille, centre, magnétisme), guides, flou de mouvement.

## Modèle

Un calque est un clip d'une piste `graphics`. Ce qui est commun à tout
calque n'existe qu'à un endroit :

| Donnée | Où |
| --- | --- |
| identifiant, nom, durée, in / out, activation | `Clip` (`id`, `label`, `timeline_start`, `source_in/out`, `enabled`) |
| transform (position, échelle, rotation, opacité, ancrage, échelle X/Y, inclinaison, miroirs) | `Clip.transform` + `Clip.transform_keyframes` |
| masques, mode de fusion | `Clip.compositing` |
| effets, étalonnage | `Clip.effects`, `Clip.color_grade` |
| autres propriétés animées (forme, texte, masques) | `Clip.animation` (`graphic.<champ>`, `mask.<id>.<propriété>`) |
| contenu, apparence, visibilité, verrou, parent, groupe, rang, flou | `Clip.graphic` (`GraphicOverlay`) |

`GraphicOverlay.type` choisit ce qui est dessiné : `text`, `shape`
(rectangle, rectangle arrondi, ellipse, ligne, polygone), `rectangle`
(historique), `solid`, `image`, `group`, `adjustment`, `null` (contrôleur).
Les trois derniers n'ont pas de pixels propres.

**Pile.** L'ordre est `(piste, z_order)` : une piste graphique peut contenir
des calques qui se chevauchent dans le temps, `z_order` les ordonne. Les
pistes graphiques sont composées au-dessus des pistes vidéo (comportement
historique), dans l'ordre des pistes.

**Transform.** Dans l'espace de son parent (boîte `Pw × Ph`, le cadre
`W × H` pour un calque racine) :

```
M = T(Pw/2 + px·W, Ph/2 + py·H) · R(rotation) · K(inclinaison)
    · S(scale·scale_x·±1, scale·scale_y·±1) · T(−ax·w, −ay·h)
```

La position désigne l'endroit où se trouve le **point d'ancrage**
`(ax, ay)` (fraction de la boîte du calque) ; rotation et échelle pivotent
autour de lui. Un clip vidéo suit la même formule avec une boîte égale au
cadre (sans inclinaison). Code : `core/mograph_scene.py`.

**Parentage.** Le parent effectif est `parent_id`, sinon le groupe :
`monde(calque) = monde(parent) · M`. Position, rotation, échelle, ancrage et
miroirs se combinent ; l'opacité se transmet depuis un **contrôleur**
(null). Un clip vidéo peut être parent. Les cycles (`A → B → A`,
`A → B → C → A`) sont refusés par `core.graph_cycles` — la même détection
que les séquences imbriquées. Parenter ou déparenter garde le calque à sa
place à l'écran (le transform local est recalculé à la tête de lecture).

**Groupes.** Les membres (`group_id`) sont composés **ensemble**, isolés,
puis le groupe est masqué, rendu avec son opacité et fusionné comme un seul
calque. Le groupe est aussi un parent de transform. Dégrouper un groupe
neutre le supprime ; dégrouper un groupe transformé le change en contrôleur
(rien ne bouge à l'écran).

**Placement historique.** Les calques des projets antérieurs (format ≤ 14)
gardent `layout = legacy` : leur position place le coin haut-gauche de
l'image tournée, exactement comme avant. Les nouveaux calques sont en
`anchor`.

## Ordre de compositing

L'ordre est déterministe, documenté ici et **identique** dans l'aperçu fidèle
et à l'export (même fonction, `ExportEngine._build_filter_complex`).

### Un calque motion graphics (`core/mograph_raster.py`)

1. **Contenu** en espace calque : texte (fond, ombre éventuellement floue,
   contour, remplissage), forme, aplat, image.
2. **Transform du monde** : transform local puis parents et groupes.
3. **Masques**, en espace calque, dans l'ordre de la pile : forme, dilatation,
   contour adouci, inversion, opacité, puis l'opération (`add` : somme bornée,
   `subtract`, `intersect`).
4. **Flou de mouvement** : moyenne de `n` rendus (étapes 1–3) répartis sur
   l'obturation.
5. **Opacité** du calque (× opacité des contrôleurs parents).
6. **Fusion** dans la bande ou dans le groupe.

### La pile (`core/mograph_program.py`, `core/mograph_ffmpeg.py`)

```
pistes vidéo (ordre des pistes, transitions)
  → éléments motion graphics, du bas vers le haut :
      band        calques normaux consécutifs aplatis par Qt → overlay
      layer       calque / groupe à effets ou mode ≠ Normal
                    → effets FFmpeg sur son flux → fusion avec tout le dessous
      adjustment  effets appliqués à la composition du dessous,
                    reposée à travers la couverture du calque (opacité × masques)
  → sous-titres
```

Un mode de fusion, des effets ou un adjustment layer doivent voir **la vidéo
en dessous** : ils coupent la bande et passent par FFmpeg. Les calques
normaux, eux, sont aplatis (composer « par-dessus » est associatif).

### Un clip vidéo

```
trim → mise au cadre (scale + pad) → fps → remappage temporel
  → masques (matte rastérisée, espace calque) → échelle (uniforme × X/Y)
  → miroirs → rotation → effets → étalonnage → chroma key → opacité
  → overlay à la position de l'ancrage (ou fusion)
```

### Écarts avec l'ordre « idéal »

- Les **effets d'un calque graphique** s'appliquent après son transform (au
  flux du calque dans le cadre) : un flou ne grossit pas avec l'échelle d'un
  parent.
- Les **effets d'un groupe** s'appliquent au groupe aplati ; ceux d'un
  calque **à l'intérieur** d'un groupe ne sont pas rendus (le groupe est
  aplati par Qt).
- Un adjustment layer agit sur toute la composition en dessous (il ne peut
  pas être groupé) ; son transform ne déplace que sa couverture (masques).

## Modes de fusion

Liste unique : `core/blend_modes.py` — Normal, Produit, Écran, Incrustation,
Obscurcir, Éclaircir, Addition, Différence. Formules séparables du W3C, en
RVB, puis `fond·(1−α) + f(fond, calque)·α`.

| Mode | Qt (`QPainter`) | FFmpeg (`blend=all_mode`, calque en 1er flux) |
| --- | --- | --- |
| Normal | `SourceOver` | `overlay` |
| Produit | `Multiply` | `multiply` |
| Écran | `Screen` | `screen` |
| Incrustation | `Overlay` | `hardlight` (l'`overlay` de FFmpeg teste le calque, pas le fond) |
| Obscurcir / Éclaircir | `Darken` / `Lighten` | `darken` / `lighten` |
| Addition | `Plus` | `addition` |
| Différence | `Difference` | `difference` |

`tests/test_mograph_render.py::test_blend_modes_match_between_qt_and_ffmpeg`
vérifie chaque mode pixel par pixel (écart ≤ 4/255). Côté FFmpeg, le calque
est d'abord posé sur un cadre transparent (même taille que le fond), la
fusion est faite en RVB planaire, puis l'alpha du calque est réappliqué : le
`blend` historique combinait deux flux de tailles différentes en YUV.

## Aperçu et export

- **Export et aperçu fidèle** : le graphe FFmpeg lit les calques sous forme
  de flux `.ffconcat` (images PNG + durées) produits par le rastériseur Qt.
- **Viewer interactif** (avant que le segment fidèle soit prêt, et pendant
  la lecture) : le même rastériseur dessine les calques au-dessus du lecteur
  vidéo. Approximations (moniteur CPU) : pas d'effets FFmpeg ni d'adjustment layers, fusion
  calculée contre un fond transparent, demi-résolution sans flou pendant la
  lecture ; le moniteur GPU, lui, les montre en temps réel (voir
  [gpu-preview.md](gpu-preview.md)). Dès que le segment fidèle est en cache, il remplace cet aperçu.
- **Préparation de l'export** : les images des calques sont rendues dans un
  fil séparé (`RenderQueue._prepare_then_start`), puis FFmpeg démarre et les
  relit dans le cache. L'interface reste fluide.

## Flou de mouvement

Optionnel à deux niveaux : interrupteur global de la séquence (menu
*Calques*) et case par calque. Il suit les transforms (position, rotation,
échelle, parents compris) : `n` rendus sur l'intervalle d'obturation centré
sur l'image, moyennés. Un calque immobile n'est rendu qu'une fois.

| Qualité | Échantillons |
| --- | --- |
| Aperçu brouillon / lecture | aucun |
| Aperçu standard | ≤ 4 |
| Aperçu haute qualité, export | réglage de la séquence (8 par défaut, 2–32) |

## Cache

- Chaque image de calque est nommée par l'**empreinte de son état** (valeurs
  évaluées, matrices du monde, masques, échantillons de flou, fichier image).
  Une suite d'images identiques n'est rendue qu'une fois ; modifier un calque
  ne change que les images de **son** élément.
- Le nom porte aussi la **version du dessin** (`RASTER_VERSION`,
  `core/mograph_raster.py`), qui entre également dans l'empreinte des
  segments : changer la façon de dessiner un même état (ordre du contour,
  mise en page du texte…) impose de l'incrémenter, sinon le cache
  resservirait les anciennes images.
- Le dossier `<cache>/mograph` est déclaré au gestionnaire de cache
  (`CacheManager`, couche `mograph`) : budget disque global, éviction des
  moins récemment utilisées (avant les proxies), purge avec l'aperçu.
- L'empreinte d'un segment d'aperçu couvre les calques de la fenêtre, leurs
  parents (calques « rig », même hors fenêtre), masques, effets, animation et
  flou de mouvement : un segment ne se recalcule que si ce qu'il montre change.

## Format `.kut` (version 15)

*Cette section décrit les clés introduites en version 15 ; le format courant est la **version 16** (voir [architecture.md](architecture.md#format-kut)).*

Nouveaux champs, tous optionnels à la lecture :

- `transform` : `anchor_x`, `anchor_y`, `scale_x`, `scale_y`, `skew`,
  `flip_h`, `flip_v` (écrits seulement s'ils diffèrent du neutre : le JSON
  d'un clip simple ne change pas) ;
- `animation` : images-clés génériques (`graphic.*`, `mask.<id>.*`) ;
- `graphic` : `visible`, `locked`, `parent_id`, `group_id`, `z_order`,
  `motion_blur`, `layout`, champs de forme et de texte ;
- `compositing.masks[]` : `id`, `mode`, `points`, `name` ;
- séquence : `guides`, `motion_blur`.

Rétrocompatibilité : un fichier ≤ 14 s'ouvre sans conversion destructive ;
ses calques gardent `layout = legacy` ; les anciennes images-clés de masque
(linéaires) passent au moteur central sans changer leur courbe.

Vérifié sur un projet écrit par la version précédente (format 13) : titres
et rectangles historiques tombent au même endroit qu'avec l'ancien moteur
(écart moyen 2,6/255, sur les bords : tracé vectoriel au lieu d'un
rééchantillonnage bilinéaire). **Changement voulu** : les masques des clips
vidéo sont désormais appliqués en espace calque et animés ; l'ancien graphe
les appliquait après la rotation (ellipse décentrée) et ignorait leurs
images-clés et leur contour adouci.

## Undo / redo

Une entrée par action : ajouter / supprimer / dupliquer / coller des
calques, réordonner, renommer, visibilité, verrou, parent, grouper /
dégrouper, transform, ancrage, masques, compositing, propriétés de forme et
de texte, adjustment layer, presets, guides, flou de mouvement. Un glisser
dans le viewer ou une rafale de saisies est regroupé en **une** entrée.

## Copier / coller et presets

- **Calques** : un groupe emporte son contenu ; parent et groupe sont
  rattachés aux copies s'ils ont été copiés ensemble, gardés s'ils existent
  dans la séquence cible, sinon détachés. Les tailles en pixels s'adaptent à
  la résolution de la séquence cible.
- **Attributs** : transform, effets et couleur, masques (nouveaux
  identifiants, animation comprise), animation.
- **Presets** (`core/mograph_presets.py`) : intégrés (Titre simple, Titre
  encadré, Titre espacé animé, Lower third, Call-out) et utilisateur
  (`<config>/mograph_presets/*.json`, *Enregistrer la sélection comme preset*).

## Performances

`python -m tools.perf.mograph_bench --ffmpeg` (résultats :
`docs/perf/mograph.json`, Apple Silicon). Calques 1920×1080 animés, 2 s à
30 i/s ; médianes en millisecondes.

| Scénario | Image du viewer (960×540) | Balayage | Rafraîchissement UI | Graphe d'export à froid | En cache | Export FFmpeg 2 s |
| --- | --- | --- | --- | --- | --- | --- |
| 10 calques | 0,8 | 0,9 | 0,09 | 959 | 12 | 196 |
| 50 calques | 3,7 | 4,2 | 0,33 | 1 277 | 58 | 211 |
| 100 calques | 7,0 | 8,4 | 0,67 | 1 834 | 118 | 254 |
| 5 groupes + contrôleurs | 4,5 | 5,2 | 0,48 | 1 370 | 72 | 205 |
| 50 calques × 3 masques | 12,5 | 13,3 | 0,34 | 2 239 | 92 | 247 |
| 50 calques + 3 adjustment | 3,7 | 4,3 | 0,35 | 2 794 | 66 | 1 027 |
| 10 calques, flou | 4,5 | 5,7 | 0,15 | 1 816 | 74 | 148 |
| 50 calques, flou | 40,5 | 24,3 | 0,35 | 5 517 | 372 | 225 |

Lecture : l'aperçu interactif tient la cadence jusqu'à 100 calques ; le flou
de mouvement coûte un facteur proche du nombre d'échantillons (désactivé en
lecture et en brouillon). Le coût dominant de l'export est la première
rastérisation des images (rendu Qt + PNG), faite en arrière-plan et ensuite
servie par le cache ; les adjustment layers coûtent surtout dans FFmpeg
(effets appliqués à toute l'image).

## Ajouter un nouveau type de calque

1. **Type** : ajouter une valeur à `GraphicType` (`core/graphics.py`), ses
   champs propres à `GraphicOverlay` (avec bornes dans `__post_init__`), un
   défaut dans `graphic_defaults` et un libellé dans `_LABELS`. Les champs
   sont sérialisés automatiquement (`graphic_to_dict` parcourt les champs).
2. **Dessin** : une branche dans `draw_content` (`core/mograph_raster.py`),
   en espace calque (boîte `w × h`). Si le contenu déborde de sa boîte,
   l'ajouter à `content_margin`. S'il n'a pas de pixels, l'ajouter à
   `CONTAINER_TYPES`.
3. **Animation** : décrire ses propriétés animables dans
   `GRAPHIC_ANIMATABLE` (`core/mograph_targets.py`) avec les types
   concernés. Inspecteur, losanges, Graph Editor, copier / coller,
   sérialisation et rendu suivent sans autre code.
4. **Interface** : une section dans `GraphicsEditor` (`ui/graphics_editor.py`),
   affichée pour ce type dans `set_graphic` ; une entrée dans le menu
   *Ajouter* du panneau Calques (`ui/layers_panel.py`).
5. **Tests** : un rendu pixel dans `tests/test_mograph_render.py` et un aller-
   retour `.kut` dans `tests/test_mograph_model.py`.

S'il doit voir la composition en dessous (comme un adjustment layer), le
traiter comme un élément à part dans `graphics_program`
(`core/mograph_program.py`) et `compose_graphics` (`core/mograph_ffmpeg.py`).

## Limites connues

- Pas d'animation caractère par caractère, de texte sur tracé ni de couleurs
  animées (les couleurs restent statiques ; `graphic.*` ne couvre que des
  nombres). Le modèle du texte (lignes, alignement, approche) est prêt pour
  une extension par caractère.
- Masques polygonaux : sommets statiques (le masque se déplace, s'échelonne,
  tourne et s'adoucit, mais ses sommets ne s'animent pas un par un).
- Clips vidéo : pas d'inclinaison ni de parent (un clip vidéo peut en
  revanche **être** parent) ; pas de flou de mouvement.
- Effets d'un calque à l'intérieur d'un groupe non rendus ; un adjustment
  layer ne peut pas être groupé.
- Les calques graphiques restent au-dessus des pistes vidéo (pas
  d'entrelacement vidéo / graphique dans la pile).
- Viewer interactif : approximation (voir *Aperçu et export*) ; l'aperçu
  fidèle fait foi.
- Contour adouci et ombre floue : flou approché (réduction / agrandissement
  lissés), identique dans l'aperçu et l'export mais pas strictement gaussien.
- Pas de rotoscopie, système nodal, expressions, 3D ni caméra. Le suivi 2D d'un point (position, ancrage,
  masque) existe : voir [tracking.md](tracking.md).
