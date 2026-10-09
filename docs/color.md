# Page Couleur : nœuds d'étalonnage et roues

Ce document décrit les étapes 1 et 2 de l'[ADR-0002](adr/0002-socle-nodal-etalonnage-composition-pages.md) :
- la page Couleur et la bande des plans ;
- les nœuds d'étalonnage, en série, en parallèle et en calques ;
- les roues lift, gamma, gain et offset ;
- le qualifieur TSL ;
- la comparaison avant / après.

## Utilisation

- **Page Couleur.** On y accède de trois façons :
  - le bouton *Couleur* au centre de la barre supérieure ;
  - le rail (*Couleur*) ;
  - *Fenêtre › Page Couleur*.

  La page dispose le grand moniteur au centre avec les scopes dessous, l'inspecteur à gauche (onglet Couleur), les
  nœuds, les roues et le qualificateur à droite, puis la timeline et la bande des plans en bas. *Montage* (ou *Éditer* dans le rail) ramène la disposition de
  montage. Chaque page garde la disposition qu'on lui laisse, et *Disposition par défaut* rend celle d'origine de la
  page affichée.
- **Bande des plans.** Elle affiche une vignette par clip vidéo, dans l'ordre du montage, avec le numéro du plan, son
  nom, une pastille s'il est étalonné et son nombre de nœuds. Un clic passe à ce plan : il est sélectionné et la tête
  de lecture va à son début.
- **Nœuds.** Le clip vidéo sélectionné montre son graphe, de *Source* à *Sortie*. Les gestes possibles :
  - **Clic** sur un nœud : il devient le nœud courant. Les roues, les réglages de l'inspecteur, les courbes, la LUT et
    les presets agissent sur lui.
  - **Alt+S** ou **+** : ajoute un nœud en série après le nœud courant.
  - **Alt+P** ou la flèche du **+** › *nœud parallèle* : ajoute un nœud à côté du nœud courant, sur la même image.
    Les deux branches se réunissent par un mélangeur parallèle (`+`) et leurs corrections s'additionnent.
  - **Alt+L** ou *nœud de calque* : même chose avec un mélangeur de calques (`≡`). La branche dessinée la plus basse
    passe dessus, là où son qualifieur la sélectionne.
  - **Ctrl+D** (⌘D) ou l'œil : contourne ou réactive le nœud. Un nœud contourné apparaît en pointillé.
  - **Glisser** un nœud : change sa place dans sa suite de nœuds en série.
  - **Double-clic** ou **Entrée** : nomme le nœud.
  - **Suppr** ou la corbeille : supprime le nœud courant (le dernier ne se supprime pas, on le réinitialise). Une
    branche vide quitte son mélangeur, et un mélangeur réduit à une branche disparaît.
  - Clic droit : un menu reprend ces commandes.

  Tant que l'éditeur a le focus, ces touches agissent sur le nœud et jamais sur le clip de la timeline. Chaque
  commande est une étape d'historique. La pastille d'un nœud signale qu'il change l'image, la clé qu'il est qualifié.
  Les mélangeurs ne se règlent pas.
- **Roues.**
  - Le **palet** donne la couleur. On le pousse vers une teinte de l'anneau : son rouge est au même endroit que sur le
    vectorscope.
  - La **molette** sous la roue donne le niveau.
  - **Maj** sert au réglage fin (quatre fois plus lent).
  - Le **double-clic** remet le palet ou la molette à zéro.

  Une rafale de mouvements forme une seule étape d'historique.
- **Qualificateur** (onglet à côté des roues). *Qualifier ce nœud* limite sa correction à une partie de l'image :
  - une bande par composante (teinte, saturation, luminance), dont on glisse la plage directement, avec trois champs
    pour les valeurs exactes ;
  - *Inverser* corrige le reste de l'image ;
  - *Afficher la sélection* montre dans le moniteur ce qui est choisi, le reste en gris (moniteur GPU seulement, jamais
    à l'export).
- **Avant / après.** Le bouton de comparaison dans l'en-tête du panneau sépare le moniteur par un trait : à gauche,
  l'image sans étalonnage ; on glisse le trait pour le déplacer. Cela ne marche qu'avec le moniteur GPU, et la
  comparaison s'arrête en revenant au Montage.
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

- **Export, en série.** La chaîne de filtres de chaque nœud actif (activé et non neutre) s'applique dans l'ordre du
  graphe (`_build_color_grade_filters`). Un graphe d'un nœud rend les pixels de son réglage.
- **Export, à branches** (`core/color_render.py`). Un sous-graphe en `gbrp`, insérable dans la chaîne d'un clip ou
  d'un calque (`null[e0];…;[sortie]null`), avec des labels préfixés par l'appelant :
  - un correcteur qualifié donne `maskedmerge(entrée, étalonné, clé)`, la clé venant d'un `lut3d` sur la table de son
    qualifieur ;
  - un mélangeur parallèle donne `mix` aux poids `1 … 1 −(n−1)`, soit `S + Σ(Bᵢ − S)` ;
  - un mélangeur de calques donne `maskedmerge(dessous, correction, clé)`.

  Chaque mélange est exact au niveau près. Une image utilisée plusieurs fois passe par un `split`, et une branche
  recouverte n'est pas calculée.
- **Qualifieur** (`core/color_qualifier.py`). La clé combine la teinte (circulaire), la saturation (chroma
  `max − min`) et la luminance (Rec. 709), chacune avec une plage et une douceur linéaire. À l'export, elle est
  tabulée en `.cube` 65³ dans le cache (`color_keys/`), dans un fichier nommé par son contenu.
- **Moniteur GPU.** La chaîne entière, à branches comprises, est cuite en **une** LUT 3D (`core/gpu_grade.py`) : un
  nœud de plus ne coûte rien en lecture. La clé de la LUT suit chaque fichier `.cube` des nœuds. *Afficher la
  sélection* fait cuire un `Highlight(graphe, nœud)`, et avant / après passe la part à garder sans étalonnage à la
  passe `grade` (`CompositeLayer.grade_split`).
- **Fichier.** `color_grade` reçoit `{"nodes": [{"id", "label"?, "grade", "qualifier"?} | {"id", "mixer"}], "links":
  [[source, cible, entrée]]}`. Les LUT de chaque nœud sont copiées dans le projet comme celle d'un réglage simple.

## Code

| Module | Rôle |
| --- | --- |
| `core/node_graph.py` | graphe générique, immuable et toujours valide ; ordre de calcul |
| `core/color_nodes.py` | `ColorNode`, `ColorMixer`, `ColorNodeGraph` (série, branches, point de séparation), codec |
| `core/color_qualifier.py` | `Qualifier` : formule de la clé, table `.cube` en cache |
| `core/color_render.py` | sous-graphe FFmpeg des branches, `Highlight` |
| `core/color_wheels.py` | formule, filtre `lutrgb`, palet ↔ roue |
| `core/color_grading.py` | `Wheel`, roues de `ColorGrade`, service (nœud courant, `edit_nodes`) |
| `core/workspace_state.py` | `PanelId.COLOR`, `PanelId.CLIPS`, pages et leurs dispositions |
| `ui/color_page/` | roues, éditeur de nœuds, qualificateur, bande des plans, panneau Couleur |
| `ui/main_window_mixins/color_page.py` | pages, nœud courant, commandes de nœuds, roues, qualifieur, sélection, avant / après |

Tests :
- `tests/test_color_wheels.py`, `tests/test_color_nodes.py`, `tests/test_color_page.py` (étape 1) ;
- `tests/test_color_render.py` : mélangeurs et qualifieur mesurés au niveau près sur le vrai FFmpeg, export réel de
  deux clips et d'un calque d'effets, cuisson du moniteur ;
- `tests/test_color_page_step2.py` : interface ;
- `tests/test_gpu_grade.py` : avant / après dans la référence ; `tools/gpu/selfcheck.py` le vérifie sur le vrai GPU
  (cas `grade_compare`).
