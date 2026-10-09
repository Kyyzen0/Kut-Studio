# ADR-0002 : un socle nodal pour l'étalonnage et la composition, et des pages

**Statut :** Accepté. L'étape 1 est livrée (page Couleur, nœuds en série, roues).
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

`clip.color_grade` porte un `ColorGrade` **ou** un `ColorNodeGraph` (`core/color_nodes.py`). Chaque nœud est un
`ColorGrade` complet, et son drapeau `enabled` sert à contourner le nœud.

- **Une seule forme à la fois.** Un graphe d'un seul nœud sans nom redevient un `ColorGrade` (`simplify`). Un clip
  qu'on n'a pas découpé s'écrit donc dans le fichier exactement comme avant, et un ancien projet s'ouvre à l'identique.
- **Export.** La chaîne de chaque nœud actif s'applique dans l'ordre du graphe. Un graphe d'un nœud rend les pixels de
  son `ColorGrade`.
- **Moniteur.** La chaîne entière est cuite dans **une** LUT 3D. Tout graphe dont chaque nœud agit pixel par pixel
  (série, et demain parallèle ou calque, puisqu'un mélange de deux branches reste une fonction du pixel) coûte une
  seule lecture de LUT, quel que soit le nombre de nœuds. Rien n'est à réécrire en shader : le temps réel vient du
  socle lui-même.
- **Nœuds spatiaux.** Les fenêtres, les flous et les qualifieurs adoucis dépendent des pixels voisins et ne tiennent
  pas dans une LUT. Ils deviendront des passes GPU (étape 3), comme les mattes de masques aujourd'hui.

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
  - Le modèle de fichier est stable pour l'étape 2 : les liens sont déjà écrits.
  - Le même socle servira la composition.
- **Négatives et limites connues.**
  - La chaîne de chaque nœud passe par des filtres 8 bits, et `eq` convertit en YUV et revient. Chaque nœud peut
    donc ajouter un niveau d'arrondi. Le moniteur n'en a pas : sa LUT est cuite par cette même chaîne. Une chaîne en
    16 bits ou en flottant sera à mesurer.
  - La température passe par `colorbalance`, dont les tons moyens n'agissent plus quand max + min dépasse ≈ 202/255 :
    un gris moyen ne se réchauffe pas. Les roues n'ont pas cette limite.
  - Cette version n'écrit que des nœuds en série. Un fichier d'une version future, avec des nœuds parallèles, est
    relu en série avec un avertissement plutôt que de perdre l'étalonnage.

## Étapes

1. **Livrée.** Pages Montage et Couleur, socle de graphe, nœuds en série (ajouter, contourner, nommer, déplacer,
   réinitialiser, supprimer, chacun en une étape d'historique), roues, inspecteur réglant le nœud courant.
2. Nœuds parallèles et nœuds de calque (mélangeurs), qualifieur TSL, comparaison avant / après, bande de vignettes
   des plans.
3. Nœuds spatiaux : fenêtres (formes, suivies par le tracking), flou et netteté, en passes GPU.
4. Composition nodale : type de clip, nœuds de fusion, de transformation, de masque et de clé, conversion calques →
   nœuds.
5. Page Audio. D'abord un aperçu audio fidèle : le mixage rendu par le graphe audio de l'export, par morceaux. Puis
   inserts de piste, égaliseur paramétrique, sonie en direct et bus.
