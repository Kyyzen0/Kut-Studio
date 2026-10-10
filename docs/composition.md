# Composition nodale

Ce document décrit l'étape 4 de l'[ADR-0002](adr/0002-socle-nodal-etalonnage-composition-pages.md) : un clip dont
l'image est calculée par un graphe de nœuds, comme un clip Fusion dans DaVinci Resolve. Chacun choisit son modèle,
clip par clip : des calques sur des pistes, ou des nœuds dans un clip de composition.

## Utilisation

- **Page Composition.** On y accède de trois façons :
  - le bouton *Composition* au centre de la barre supérieure ;
  - *Fenêtre › Page Composition* ;
  - un double-clic sur un clip de composition (violet dans la timeline).

  La page dispose le moniteur au centre, les nœuds et l'inspecteur du nœud choisi à droite, la timeline en bas. Elle
  garde la disposition qu'on lui laisse, comme les autres pages.
- **Obtenir une composition.**
  - *Convertir en composition* (clic droit dans la timeline, ou *Séquence*) : les clips vidéo et calques graphiques
    sélectionnés deviennent les nœuds d'**un** clip de composition, sur la plus basse de leurs pistes vidéo. C'est à
    sens unique (pas de synchronisation avec des calques). Les réglages que la composition ne garde pas sont signalés
    dans la barre d'état.
  - *Nouvelle composition* : un clip de composition vide de 5 s à la tête de lecture.
- **Nœuds.**
  - **Sources** : *Média* (un média du projet, une plage de sa source posée à un instant), *Texte* (un calque
    graphique), *Couleur unie*.
  - **Traitements** : *Transformation* (position, échelle, rotation, ancrage, miroirs, opacité), *Masque*,
    *Incrustation*, *Effets*, *Étalonnage*.
  - *Fusion* : le premier plan (entrée basse) sur le fond (entrée haute), avec un mode de fusion et une opacité.
  - *Sortie* : l'image du clip.
- **Gestes.**
  - Clic : choisir un nœud ; l'inspecteur montre ses réglages.
  - **+** (ou clic droit › *Ajouter*) : ajoute un nœud **après le nœud choisi**, comme dans Fusion. Un traitement se
    glisse entre le nœud choisi et ce qui le suivait ; une source vient par-dessus, par une fusion nouvelle.
  - Glisser la sortie d'un nœud (rond à droite) sur l'entrée d'un autre (à gauche) : les relier. Glisser un lien par
    son entrée : le reporter sur une autre entrée, ou le lâcher dans le vide pour le défaire.
  - Suppr ou la corbeille : retire le nœud choisi ; la chaîne reste reliée. La sortie ne se supprime pas.
  - L'œil de l'en-tête : le viewer montre l'image du nœud choisi au lieu de la sortie (à l'arrêt ; jamais à l'export).

  Chaque geste est une étape d'historique ; une rafale sur un même champ n'en fait qu'une. Un nœud que rien ne relie à
  la sortie est permis et ne coûte rien au rendu.
- **Le clip lui-même** reste un clip : on le déplace, le coupe, le raccourcit (il montre alors une plage de la
  composition), et son transform, ses effets, son étalonnage (page Couleur) et ses masques s'appliquent à l'image
  composée.

## Rendu

- **Sources** (`core/render_plan.py`). Chaque source reliée à la sortie est une **séquence synthétique d'un seul
  clip** (un média sur une piste vidéo, un calque graphique sur une piste graphique), planifiée par le même code que la
  timeline et rendue comme une séquence imbriquée, sur fond transparent. Images fixes, médias manquants, proxies,
  rastérisation et animation des calques, fenêtres des segments d'aperçu : rien n'est réimplémenté.
- **Nœuds** (`core/composition_render.py`). Chaque image est un flux RVBA de la taille du cadre, sur toute la durée de
  la composition. Le graphe devient un sous-graphe FFmpeg fait des briques de l'export :

  | Nœud | Filtres |
  | --- | --- |
  | Transformation | ceux d'un clip posé sur sa piste (`_build_layer_filter`, puis `overlay` à sa position, ancrage compris) |
  | Fusion | `blend_onto` (formules W3C, fond transparent compris) ; l'opacité multiplie l'alpha du premier plan |
  | Masque | la matte des masques d'un clip (`video_matte_label`), multipliée à l'alpha |
  | Incrustation | `chromakey`, `despill` |
  | Effets, Étalonnage | la chaîne des calques graphiques (`_effect_chain`, alpha gardé) |

  Une image lue plusieurs fois passe par un `split`. Le résultat est lu comme une séquence imbriquée : le clip de
  composition reçoit ensuite le traitement de tout clip.
- **Son.** Chaque média relié à la sortie, non muet, sonne à son instant avec son gain ; un média détaché se tait,
  comme il ne se voit pas. Le nœud montré dans le viewer ne change pas le son. Le mixage est celui d'une séquence
  imbriquée, puis le clip applique ses propres réglages audio.
- **Moniteur.** À l'arrêt, l'aperçu fidèle montre l'image exacte (il compile le même graphe). En lecture, le moniteur
  temps réel montre la **source principale** de la composition (le premier média actif, le fond en général), comme il
  montre la piste du haut d'un montage à plusieurs pistes. Le temps réel GPU à plusieurs sources est une étape à part.
- **Fichier** (version 17). Un clip de composition a la clé `composition` : `{"duration", "nodes": [{"id", "kind",
  "label"?, …réglages}], "links": [[source, cible, entrée]], "animation"?}`. Un nœud illisible est retiré seul, un
  lien incohérent ignoré, une sortie perdue recréée : un fichier abîmé s'ouvre avec ce qui se lit encore.

## Conversion des calques

Du bas vers le haut de l'empilement (pistes vidéo, puis calques graphiques, comme à l'export), chaque calque donne une
branche :

```
source (média ou calque graphique) → masques → transformation → effets → étalonnage → incrustation
```

ne gardant que les étapes qui font quelque chose ; les branches se superposent par des fusions, avec le mode de
fusion de chaque calque. Les masques d'un calque graphique restent dans sa source (ils sont dans sa boîte, rastérisés
avec lui) ; ses images-clés aussi. Mesuré (`tests/test_composition.py`) : deux plans (le second réduit, tourné,
masqué, étalonné, en Écran) et une forme en Produit gardent la même image, 1,5 niveau d'écart moyen au plus. Les
écarts restants : une vignette se centre sur le cadre et non plus sur le calque, un flou mêle le bord d'un calque réduit
à la transparence autour.

Refusée, sans rien modifier : clips imbriqués ou déjà composés, remappage temporel, emplacements de template, tracking,
étalonnage avec fenêtres, cadrage « remplir » animé, calques d'effets, groupes et parentage. Une sélection sans clip
vidéo, ou dont la piste d'accueil a d'autres clips dans la plage, est refusée aussi. Le panoramique, les fondus et les
effets audio des clips convertis ne suivent pas (signalé).

## Code

| Module | Rôle |
| --- | --- |
| `core/composition.py` | nœuds, `CompositionGraph` (entrées typées, une sortie, insertion « à la Fusion »), `Composition`, fichier |
| `core/composition_render.py` | compilation du graphe en sous-graphe FFmpeg |
| `core/composition_ops.py` | conversion des calques, composition vide |
| `core/render_plan.py` | sources en séquences d'un clip, entrée de la composition, son, nœud montré (`composition_views`) |
| `core/timeline_evaluator.py` | source principale pour le moniteur en direct |
| `ui/composition_page/` | éditeur de nœuds, inspecteur du nœud, panneau |
| `ui/main_window_mixins/composition_page.py` | page, édition, commandes, aperçu du nœud choisi |

Tests : `tests/test_composition.py` (modèle, fichier, chaque nœud rendu par le vrai FFmpeg, son, segments, nœud
montré, fidélité de la conversion) ; `tests/test_composition_page.py` (interface).

## Limites

- Le temps réel ne montre que la source principale ; l'image composée est celle des segments fidèles, à l'arrêt.
- Les nœuds sont placés automatiquement (une colonne par profondeur) ; on ne les déplace pas à la main.
- Les images-clés d'une transformation ou d'un masque reprises de la conversion restent ; l'inspecteur règle leur
  valeur de départ, sans éditeur de courbes pour l'instant.
- Une source est un média ou un calque graphique : pas de séquence imbriquée ni de composition dans une composition.
