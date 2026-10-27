# Fenêtre et panneaux sur un écran de portable

Un MacBook Air 13" réglé en 1280x800 ou 1440x900 n'offre que ~1280x690 ou ~1440x790 **utilisables** (écran moins la barre
de menus et le Dock). Avant, la fenêtre s'ouvrait en 1480x920 et exigeait entre 720 et 1096 px de haut selon la page : le
Dock passait par-dessus la timeline, le panneau Transitions montrait « une ligne et demie » de préréglages et sa bannière
chevauchait la liste.

## Ce qui a changé

| Sujet | Avant | Après |
| --- | --- | --- |
| Taille d'ouverture | 1480x920 quel que soit l'écran | bornée à la zone utilisable de l'écran, centrée (`core/window_fit.py`, `ui/screen_fit.py`) |
| Panneau détaché enregistré sur un grand écran | restauré tel quel, hors écran possible | borné et ramené dans l'écran courant (même fonction) |
| Hauteur minimale de la fenêtre, page Montage / Composition | 720 (explicite) / 770 | 560 (base) ; le gestionnaire de panneaux la relève par page (`_sync_window_minimum_height`) |
| … page Couleur | 850 | 670 |
| … page Audio | 1049 | 686 |
| Scopes sous la visionneuse | comptés à 200 px dans le minimum de la fenêtre | cèdent en premier et se replient (`ViewerHostSplitter.minimumSizeHint`) |
| Mixeur empilé sous la timeline | 320 px de minimum | 140 (`core.workspace_state.MIN_HEIGHT`) : les tranches défilent déjà |
| Timeline | 240 px (2 pistes visibles) | 260 px : au moins 3 pistes visibles |
| Panneau de gauche trop bas | les widgets se chevauchent | le corps du panneau et chaque page défilent (`ui/scroll_guard.py`) |
| Panneau Transitions / Effets / Effets audio | vide de ~70 px sous les puces, liste d'une ligne et demie, bannière sur la liste | en-tête à sa hauteur naturelle, liste d'au moins 3 lignes (`Sizes.library_list_min_height`), bannière en tête de la zone défilante, « Enregistrer comme preset » en bouton-icône à côté de l'action principale |

## Règles pour un nouveau panneau

* La hauteur minimale d'un panneau est **honnête** : ne la masquez pas par un `setMinimumHeight(0)` ou une politique
  `Ignored` (c'est ce qui faisait déborder les enfants sur leurs voisins). Si le contenu peut dépasser, mettez-le dans
  un `ui.scroll_guard.ScrollGuard` **à la construction** (jamais en déplaçant un widget déjà rangé dans un layout : voir
  la note sur `replaceWidget` / `setParent`).
* Une liste principale réclame `Sizes.library_list_min_height` (3 lignes) ; une bannière d'aide va dans la zone défilante
  de la liste, pas au-dessus.
* Toute géométrie qui vient d'un réglage ou d'une taille d'origine passe par `ui.screen_fit.apply_fitted_geometry`.
* Un panneau empilé dans la zone basse déclare sa hauteur dans `core.workspace_state.MIN_HEIGHT` (la même que son
  `setMinimumHeight` réel, sinon il déborde de son hôte).

## Lisibilité avec un clip sélectionné (pages Couleur et Audio)

`tools.ui_layout_audit` ouvre la fenêtre **sans clip sélectionné** et `tools.ui_audit.sibling_overlaps` ne regarde que les
layouts de boîte et de formulaire. Avec un clip, la page Couleur exigeait 974 px (nœuds + onglets Roues / Qualificateur
non défilants) : la fenêtre dépassait l'écran, le Dock passait par-dessus la timeline et la bande des plans était coupée.
`python -m tools.ui_legibility` (et `tests/test_ui_legibility.py`) joue ces pages avec un clip et signale :

* `overlaps` : deux frères d'une même layout (grille et retour à la ligne compris) qui se chevauchent ;
* `cut-off` : un conteneur plus bas que sa hauteur minimale, hors de toute zone défilante ;
* `elided-button` : un bouton à texte plus étroit que son texte ;
* `window-grows` : la fenêtre a dépassé la taille demandée.

Règles qui en découlent : les onglets d'une colonne étroite vont dans `ui.scroll_guard.GuardedTabs` (comme les piles de
pages dans `GuardedStack`) ; une rangée de boutons ou de cases dans une colonne de ~300 px est un `FlowLayout` ; une grille
met l'étiquette au-dessus de son champ ; un formulaire d'inspecteur avec des boutons larges utilise `WrapAllRows`. Le mixeur
(`ui/mixer_panel.py`) bascule seul entre tranches verticales (zone des tranches ≥ 316 px) et lignes compactes ; le bloc
dossiers de la bibliothèque (`ui/project_panel_widgets/browse_fold.py`) se replie sous 480 px de colonne.

## Mesurer et voir

```
QT_QPA_PLATFORM=offscreen python -m tools.ui_layout_audit --out ~/Movies/Kut-Studio-UX/apres --sheet ~/Movies/Kut-Studio-UX/planche-apres.png
```

L'outil ouvre la fenêtre aux trois écrans simulés (1280x800, 1440x900, 1680x1050 ; zone utilisable = écran moins 110 px),
pour chaque page et chaque niveau d'usage, imprime le minimum imposé (`minimumSize`, `minimumSizeHint`), la taille réelle
et la hauteur de la timeline, dépose une capture par combinaison (et une par section de la bibliothèque) et une planche
contact. `--culprits` liste les widgets qui pèsent le plus sur la hauteur minimale. La configuration, les caches et les
proxys sont isolés. La visionneuse reste noire hors écran (voir `docs/ui-small-windows-and-keyboard.md`).

Les tests : `tests/test_ui_small_screens.py` (minimum de la fenêtre ≤ 1280x720 par page, aucun chevauchement, listes d'au
moins 3 lignes, 3 pistes de timeline visibles, géométrie enregistrée de 3000x2000 bornée).

Reste à vérifier sur un vrai écran : le Dock (sa hauteur réelle et le masquage automatique), la barre de titre (40 px
réservés), l'affichage de la visionneuse.
