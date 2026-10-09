# Page Couleur : nœuds d'étalonnage et roues

Ce document décrit l'étape 1 de l'[ADR-0002](adr/0002-socle-nodal-etalonnage-composition-pages.md) : la page Couleur,
les nœuds d'étalonnage en série et les roues lift, gamma, gain et offset.

## Utilisation

- **Page Couleur.** On y accède de trois façons :
  - le bouton *Couleur* au centre de la barre supérieure ;
  - le rail (*Couleur*) ;
  - *Fenêtre › Page Couleur*.

  La page dispose le grand moniteur au centre avec les scopes dessous, l'inspecteur à gauche (onglet Couleur), les
  nœuds et les roues à droite et la timeline en bas. *Montage* (ou *Éditer* dans le rail) ramène la disposition de
  montage. Chaque page garde la disposition qu'on lui laisse, et *Disposition par défaut* rend celle d'origine de la
  page affichée.
- **Nœuds.** Le clip vidéo sélectionné montre sa chaîne, de *Source* à *Sortie*. Les gestes possibles :
  - **Clic** sur un nœud : il devient le nœud courant. Les roues, les réglages de l'inspecteur, les courbes, la LUT et
    les presets agissent sur lui.
  - **Alt+S** ou **+** : ajoute un nœud après le nœud courant.
  - **Ctrl+D** (⌘D) ou l'œil : contourne ou réactive le nœud. Un nœud contourné apparaît en pointillé.
  - **Glisser** un nœud : change sa place dans la chaîne.
  - **Double-clic** ou **Entrée** : nomme le nœud.
  - **Suppr** ou la corbeille : supprime le nœud courant (le dernier ne se supprime pas, on le réinitialise).
  - Clic droit : un menu reprend ces commandes.

  Tant que l'éditeur a le focus, ces touches agissent sur le nœud et jamais sur le clip de la timeline. Chaque
  commande est une étape d'historique. La pastille d'un nœud signale qu'il change l'image.
- **Roues.**
  - Le **palet** donne la couleur. On le pousse vers une teinte de l'anneau : son rouge est au même endroit que sur le
    vectorscope.
  - La **molette** sous la roue donne le niveau.
  - **Maj** sert au réglage fin (quatre fois plus lent).
  - Le **double-clic** remet le palet ou la molette à zéro.

  Une rafale de mouvements forme une seule étape d'historique.
- **Inspecteur.** Pour un clip étalonné par nœuds, il indique le nœud qu'il règle (« Ces réglages sont ceux du nœud
  02 / 03 · Peau »).

Un clip qu'on ne découpe pas en nœuds ne change pas : un seul réglage, écrit dans le fichier comme avant.

## Formule des roues

Canal par canal ``c`` sur des valeurs 0..1 (`core/color_wheels.py`). Chaque roue a une composante de canal (le palet)
et un maître ``y`` (la molette) :

| Roue | Terme | Effet |
| --- | --- | --- |
| Lift | `L = 0,25·(lift.c + lift.y)` | l'entrée 0 sort à `L` ; les blancs ne bougent pas |
| Gain | `G = max(0, 1 + gain.c + gain.y)` | l'entrée 1 sort à `G` ; les noirs ne bougent pas |
| Gamma | `Γ = 2^(gamma.c + gamma.y)` | `Γ > 1` éclaircit les tons moyens, les extrémités restent |
| Offset | `O = 0,25·(offset.c + offset.y)` | tout le signal d'autant |

`sortie = clip(max(0, (G − L)·entrée + L + O)^(1/Γ), 0, 1)`, la formule « Grade » de Nuke.

Le palet poussé vers la teinte `h` (0 rouge, 120 vert, 240 bleu), au rayon `ρ`, donne les décalages
`0,5·ρ·(cos h, cos(h − 120°), cos(h − 240°))`. Leur somme est nulle : le palet change la couleur, pas le niveau. Au
bord, un gain vers le rouge vaut 1,5 en rouge et 0,75 en vert et en bleu.

L'export applique les roues par `lutrgb`, qui calcule une table par canal sur tous les niveaux d'entrée, et ajoute
`+ 0,5` parce que `lutrgb` tronque. L'étape vient après `colorbalance` et avant les courbes. Elle n'est écrite que si
une roue a bougé, et le fichier n'a la clé `wheels` que dans ce cas.

## Comment les nœuds sont rendus

- **Export.** La chaîne de filtres de chaque nœud actif (activé et non neutre) s'applique dans l'ordre du graphe
  (`_build_color_grade_filters`). Un graphe d'un nœud rend les pixels de son réglage.
- **Moniteur GPU.** La chaîne entière est cuite en **une** LUT 3D (`core/gpu_grade.py`), de sorte qu'un nœud de plus
  ne coûte rien en lecture. La clé de la LUT suit chaque fichier `.cube` des nœuds.
- **Fichier.** `color_grade` reçoit `{"nodes": [{"id", "label"?, "grade"}], "links": [[source, cible, entrée]]}`. Les
  LUT de chaque nœud sont copiées dans le projet comme celle d'un réglage simple.

## Code

| Module | Rôle |
| --- | --- |
| `core/node_graph.py` | graphe générique, immuable et toujours valide ; ordre de calcul |
| `core/color_nodes.py` | `ColorNode`, `ColorNodeGraph` (série), `as_graph` / `simplify`, codec |
| `core/color_wheels.py` | formule, filtre `lutrgb`, palet ↔ roue |
| `core/color_grading.py` | `Wheel`, roues de `ColorGrade`, service (nœud courant, `edit_nodes`) |
| `core/workspace_state.py` | `PanelId.COLOR`, pages et leurs dispositions |
| `ui/color_page/` | roues, éditeur de nœuds, panneau Couleur |
| `ui/main_window_mixins/color_page.py` | pages, nœud courant, commandes de nœuds et de roues |

Tests : `tests/test_color_wheels.py`, `tests/test_color_nodes.py`, `tests/test_color_page.py`. La formule est vérifiée
sur les 256 niveaux par le vrai FFmpeg, l'ordre des nœuds et leur contournement par le rendu réel de l'export, et la
LUT du moniteur contre la formule.
