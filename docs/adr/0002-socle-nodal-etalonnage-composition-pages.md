# ADR-0002 : un socle nodal pour l'étalonnage et la composition, et des pages

**Statut :** Accepté. Les étapes 1 (page Couleur, nœuds en série, roues) et 2 (nœuds parallèles et de calque,
qualifieur, avant / après, bande des plans) sont livrées.
**Date :** 2026-10-09
**Décideurs :** mainteneur de Kut-Studio

Objectif : travailler « à la manière de DaVinci Resolve » pour qui le souhaite. Cela veut dire des **pages** (Montage,
Couleur, puis Audio) et des **nœuds**, pour l'étalonnage **et** pour la composition, sans retirer le modèle à calques
à qui le préfère. Chacun choisit le modèle qui lui convient, clip par clip.

## Contexte

- **Étalonnage.** Jusqu'ici, un clip portait un seul `ColorGrade` : exposition, contraste, saturation, température,
  teinte, ombres, hautes lumières, courbes, LUT. L'export le traduit en filtres FFmpeg
  (`_build_color_grade_filters`). Le moniteur GPU ne réécrit pas ces filtres : il les **cuit en une LUT 3D** par
  FFmpeg lui-même (`core/gpu_grade.py`, [gpu-preview.md](../gpu-preview.md)). Il manquait les roues primaires et la
  possibilité d'empiler des corrections indépendantes.
- **Composition.** Elle repose entièrement sur des calques : pistes de la timeline, calques de motion graphics,
  calques d'effets. L'export les compose en `filter_complex`, le moniteur GPU par passes
  ([motion-graphics.md](../motion-graphics.md)).
- **Disposition.** Les panneaux sont décrits par un `WorkspaceState` pur. Il existait déjà des espaces de travail
  nommés, mais sans notion d'étape de travail.

## Décision

### 1. Un seul socle de graphe : `core/node_graph.py`

`NodeGraph` est un graphe **immuable** (chaque modification rend un nouveau graphe, que l'historique garde tel quel)
et **toujours valide**, ce que vérifie sa construction :
- identifiants uniques ;
- liens entre nœuds existants ;
- une seule source par entrée (`NodeLink.port` : une entrée pour un nœud d'étalonnage, deux pour une fusion) ;
- aucun cycle, par `core/graph_cycles`, la même logique que pour les séquences imbriquées et le parentage des calques.

L'ordre de calcul est topologique ; à égalité, c'est l'ordre de création qui départage, ce qui le rend stable. Les
deux modèles nodaux s'appuient sur ce socle, ainsi qu'un seul éditeur : `ui/color_page/node_editor.py`, une
`QGraphicsView`.

### 2. Étalonnage par nœuds, par clip

`clip.color_grade` porte un `ColorGrade` **ou** un `ColorNodeGraph` (`core/color_nodes.py`). Le graphe a deux sortes
de nœuds :
- les **correcteurs**, chacun un `ColorGrade` complet, dont le drapeau `enabled` sert à contourner le nœud, avec un
  qualifieur facultatif ;
- les **mélangeurs**, parallèles ou de calques, qui réunissent au moins deux branches.

Le graphe a une seule sortie.

- **Une seule forme à la fois.** Un graphe d'un seul nœud sans nom ni qualifieur redevient un `ColorGrade`
  (`simplify`). Un clip qu'on n'a pas découpé s'écrit donc dans le fichier exactement comme avant, et un ancien projet
  s'ouvre à l'identique.
- **Export.** Un graphe en série reste une chaîne : les filtres de chaque nœud actif, l'un après l'autre. Un graphe à
  branches devient un sous-graphe (`split`, `mix`, `maskedmerge`, en `gbrp`), écrit sous une forme insérable dans une
  chaîne à virgules (`null[e0];…;[sortie]null`). La chaîne d'un clip, celle d'un calque et la cuisson du moniteur
  l'acceptent sans changement (`core/color_render.py`).
- **Moniteur.** La chaîne entière est cuite dans **une** LUT 3D. Tout graphe dont chaque nœud agit pixel par pixel
  coûte une seule lecture de LUT, quel que soit le nombre de nœuds : série, parallèle, calques et qualifieurs, puisque
  la clé et un mélange de branches ne dépendent que du pixel. Rien n'est à réécrire en shader : le temps réel vient du
  socle lui-même.
- **Nœuds spatiaux.** Les fenêtres, le flou d'une clé et la netteté dépendent des pixels voisins et ne tiennent pas
  dans une LUT. Ils deviendront des passes GPU (étape 3), comme les mattes de masques aujourd'hui.

#### Mélangeurs et qualifieur (étape 2)

- **Parallèle.** Chaque branche corrige l'image de leur point de séparation `S` (le dominateur du mélangeur), et les
  corrections s'additionnent : `S + Σ(Bᵢ − S)`, écrêté une seule fois à la fin. FFmpeg le calcule exactement avec
  `mix`, aux poids `1 … 1 −(n−1)` (mesuré au niveau près sur FFmpeg 9 et 7.1). Une moyenne aurait divisé chaque
  correction par le nombre de branches.
- **Calques.** La branche de l'entrée la plus haute, dessinée la plus basse comme dans Resolve, passe sur les autres.
  Si son dernier nœud est qualifié, elle pose **sa correction** `G` selon sa clé : `maskedmerge(dessous, G, K)`.
  Reprendre sa sortie, déjà mélangée à son entrée par la clé, aurait adouci deux fois le bord. Sans clé, elle
  recouvre ce qui est dessous, qui n'est alors pas calculé.
- **Qualifieur TSL** (`core/color_qualifier.py`). La clé se calcule ainsi :
  - la teinte, circulaire : un gris n'en a pas ;
  - la saturation, prise comme la chroma `max − min` : un pixel sombre et bruité n'est pas « saturé » ;
  - la luminance Rec. 709 ;
  - pour chacune, une plage avec une douceur linéaire ;
  - la clé est le produit des trois, inversable.

  Le nœud corrige `entrée + K·(étalonné − entrée)`. À l'export, la clé est tabulée en LUT 3D 65³ (`lut3d`
  tétraédrique), dans un fichier du cache nommé par son contenu. La correction reste exacte. Mesuré : 0,05 niveau
  d'écart moyen à la formule, 1 au plus. Écrire la table prend 87 ms, l'appliquer 2 ms par image 1080p. Le mode
  *Afficher la sélection* (`Highlight`) n'existe que pour le moniteur : la sélection garde sa couleur, le reste passe
  en gris assombri.
- **Avant / après.** La passe `grade` du moniteur GPU laisse sans étalonnage la part gauche du calque (uniforme
  `misc.z`), dans l'espace du calque. Un trait déplaçable la sépare dans le viewer. C'est un outil du moniteur GPU :
  sans lui, le bouton est grisé et dit pourquoi.
- **Bande des plans.** Elle prend une vignette par clip vidéo, au milieu du clip. Ce sont celles de la timeline (même
  cache, même tâche de fond). Elle sert à passer d'un plan à l'autre, et une pastille marque un plan étalonné.

### 3. Roues lift / gamma / gain / offset : la formule « Grade »

Canal par canal, sur des valeurs 0..1 (`core/color_wheels.py`) :

```
L = 0,25·(lift.c + lift.y)     G = max(0, 1 + gain.c + gain.y)
Γ = 2^(gamma.c + gamma.y)      O = 0,25·(offset.c + offset.y)
sortie = clip(max(0, (G − L)·entrée + L + O)^(1/Γ), 0, 1)
```

Ce choix évite toute approximation. FFmpeg l'applique par `lutrgb`, une table complète par canal sur tous les niveaux
d'entrée, placée après `colorbalance` et avant les courbes. Les tests comparent cette table, niveau par niveau, à la
formule écrite à la main. Le palet d'une roue ne change que la couleur (décalages de moyenne nulle) ; sa molette règle
le maître.

### 4. Composition nodale : un type de clip

Une **composition nodale** sera un clip de la timeline, comme un clip Fusion :
- **contenu** : un `NodeGraph` ;
- **entrées** : médias, séquences, textes, formes ;
- **nœuds** : fusion, transformation, masque, clé, effet, étalonnage ;
- **export** : compilé en `filter_complex` ;
- **moniteur** : compilé en passes du compositeur GPU.

**Une seule source de vérité par clip.** Un clip classique est fait de calques, une composition nodale de nœuds. Une
conversion **à sens unique** calques → nœuds fera d'un groupe de pistes ou de calques une composition nodale. Il n'y
aura pas de synchronisation dans les deux sens : deux sources de vérité finiraient par diverger.

### 5. Pages

Une page est une étape du travail avec **sa** disposition : Montage et Couleur aujourd'hui, Audio ensuite. La page
Montage garde `workspace.json`, et chaque autre page a son fichier `workspace_<page>.json`. Changer de page garde la
disposition qu'on quitte et rend celle de la page ouverte telle qu'on l'avait laissée. « Disposition par défaut » rend
celle d'origine de la page affichée.

La page Couleur s'organise ainsi :
- au centre, le grand moniteur avec les scopes, affichés le temps de la page sans changer la préférence du Montage ;
- à gauche, l'inspecteur, qui règle le nœud courant ;
- à droite, les nœuds et les roues ;
- en bas, la timeline, pour passer d'un plan à l'autre.

Changer de page se fait au centre de la barre supérieure, par le rail (Couleur, Éditer) ou par le menu *Fenêtre*. Les
deux commandes clavier n'ont pas de touche par défaut : Maj+chiffre est pris par les angles Multicam sur un clavier
AZERTY.

## Conséquences

- **Positives.**
  - Une correction s'empile sans toucher aux autres, et se contourne d'un geste.
  - Les roues sont exactes à l'export et en temps réel dans le moniteur.
  - Le modèle de fichier est resté stable entre les étapes 1 et 2 : les liens étaient déjà écrits, l'étape 2 n'a
    ajouté que les mélangeurs et le qualifieur des nœuds.
  - Le même socle servira la composition.
- **Négatives et limites connues.**
  - La chaîne de chaque nœud passe par des filtres 8 bits, et `eq` convertit en YUV et revient. Chaque nœud peut
    donc ajouter un niveau d'arrondi. Le moniteur n'en a pas : sa LUT est cuite par cette même chaîne. Une chaîne en
    16 bits ou en flottant sera à mesurer.
  - La température passe par `colorbalance`, dont les tons moyens n'agissent plus quand max + min dépasse ≈ 202/255 :
    un gris moyen ne se réchauffe pas. Les roues n'ont pas cette limite.
  - Un graphe invalide, ou d'une forme qu'une version plus ancienne ne connaît pas, est relu avec ses correcteurs en
    série et un avertissement, plutôt que de perdre l'étalonnage. Une version d'avant l'étape 2 relit ainsi un graphe
    à branches.
  - Avant / après et *Afficher la sélection* n'existent que dans le moniteur GPU.

## Étapes

1. **Livrée.** Pages Montage et Couleur, socle de graphe, nœuds en série (ajouter, contourner, nommer, déplacer,
   réinitialiser, supprimer, chacun en une étape d'historique), roues, inspecteur réglant le nœud courant.
2. **Livrée.** Nœuds parallèles et nœuds de calque (Alt+P, Alt+L), qualifieur TSL et *Afficher la sélection*,
   comparaison avant / après dans le moniteur, bande des plans.
3. Nœuds spatiaux : fenêtres (formes, suivies par le tracking), flou et netteté, en passes GPU.
4. Composition nodale : type de clip, nœuds de fusion, de transformation, de masque et de clé, conversion calques →
   nœuds.
5. Page Audio. D'abord un aperçu audio fidèle : le mixage rendu par le graphe audio de l'export, par morceaux. Puis
   inserts de piste, égaliseur paramétrique, sonie en direct et bus.
