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
  temps réel lit le **cache de rendu** de la composition (ci-dessous) ; tant qu'il n'est pas prêt, il montre sa
  **source principale** (le premier média actif, le fond en général), comme il montre la piste du haut d'un montage à
  plusieurs pistes.
- **Cache de rendu** (`core/composition_cache.py`), comme le cache « Fusion Output » de DaVinci Resolve. Le moniteur
  temps réel n'a qu'un décodeur : il ne compose pas plusieurs vidéos en direct. La sortie de chaque composition est donc
  rendue d'avance, à l'arrêt, en arrière-plan :
  - par **morceaux de 10 s** de son temps propre, celui sous la tête de lecture d'abord (juste après le segment fidèle),
    à la qualité d'aperçu, une image clé toutes les 12 images pour que le moniteur s'y positionne vite ;
  - par le chemin des segments fidèles : un projet d'un seul clip **neutre** (la composition posée à 0, sans les
    réglages du clip), donc le graphe de l'export, les proxys et l'empreinte des segments ; le son de la composition est
    dans le morceau ;
  - dans le cache des segments d'aperçu (budget, LRU, durée de vie), sous un propriétaire à part : déplacer, couper ou
    régler le clip, ou modifier le reste du montage, ne le refait pas ; modifier la composition le refait (les demandes
    de l'ancienne version sont abandonnées) ; annuler la modification resservit l'ancien morceau, sans rendu.

  En lecture, le moniteur lit le morceau comme un média de la taille du cadre, sans proxy, et applique par-dessus le
  transform, les effets, l'étalonnage et les masques du clip, comme l'export. Le nœud montré par la page Composition a
  son propre cache.
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
| `core/composition_cache.py` | cache de rendu : morceaux, clip neutre, clé indépendante du clip |
| `core/timeline_evaluator.py` | moniteur en direct : morceau prêt (`composition_source`), sinon source principale |
| `ui/composition_page/` | éditeur de nœuds, inspecteur du nœud, panneau |
| `ui/main_window_mixins/composition_page.py` | page, édition, commandes, aperçu du nœud choisi |
| `ui/main_window_mixins/composition_cache.py` | demande des morceaux à l'arrêt, morceau prêt servi au moniteur |

Tests : `tests/test_composition.py` (modèle, fichier, chaque nœud rendu par le vrai FFmpeg, son, segments, nœud
montré, fidélité de la conversion) ; `tests/test_composition_cache.py` (morceaux, clé, moniteur en direct, morceau
réel égal à l'export) ; `tests/test_composition_page.py` (interface, lecture du cache, annulation).

## Limites

- Juste après une modification, la lecture montre la source principale le temps que le morceau se rende. Mesuré
  (Apple M4, 10 s d'une composition 1080p : deux sources, une transformation, une fusion) : 1,5 s en Brouillon, 2,4 s
  en Standard, 6,1 s en Haute. Rien ne se rend pendant la lecture.
- Les nœuds sont placés automatiquement (une colonne par profondeur) ; on ne les déplace pas à la main.
- Les images-clés d'une transformation ou d'un masque reprises de la conversion restent ; l'inspecteur règle leur
  valeur de départ, sans éditeur de courbes pour l'instant.
- Une source est un média ou un calque graphique : pas de séquence imbriquée ni de composition dans une composition.
