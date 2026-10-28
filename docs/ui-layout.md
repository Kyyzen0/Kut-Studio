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
(`ui/mixer_panel.py`) bascule seul entre tranches verticales (zone des tranches ≥ 296 px) et canaux compacts de 168 px (voir « Page Audio » plus bas) ; le bloc
dossiers de la bibliothèque (`ui/project_panel_widgets/browse_fold.py`) se replie sous 480 px de colonne.

## Page Couleur « Air d'abord » (roues entières, bande des plans)

Mesures à 1440x900 : colonne de droite ~667 px de haut (la timeline n'y est plus empilée), quatre roues de 150 px côte à côte,
nœuds en une bande de 88 px à l'échelle 1, bande des plans de ~151 px.

* La page Couleur démarre **sans timeline** : la bande des plans (`ui/color_page/clip_strip.py`) prend sa place, avec le bouton
  « Afficher la timeline » (« Masquer la timeline » une fois cochée). Ce choix est **de session** :
  `core.workspace_state.color_page_session_state` replie la timeline au premier affichage de la page et à l'enregistrement
  (jamais dans `workspace_color.json`). Sous la timeline la bande se réduit à son en-tête (`MIN_HEIGHT[CLIPS]` = 36 px) ; seule,
  elle prend `DEFAULT_SIZE[CLIPS]` (`WorkspaceManager._strip_only_height`).
* La colonne Couleur s'ouvre à 660 px (`COLOR_PAGE_PANEL_WIDTH`) et le moniteur cède, jusqu'à son minimum, à la colonne.
* `ColorWheels` choisit 4 colonnes dès que chaque cellule fait 120 px (disque >= 120 px à 1280 et 1222 px de large ; 150 px au
  plus), sinon 2 x 2 ; sa `minimumSizeHint` est celle de deux roues, pour que la page prenne la largeur de l'onglet.
* `ColorPanel` est une colonne (nœuds au-dessus, roues dessous) tant que sa largeur ne dépasse pas 1,6 fois sa hauteur.
* **Réglages rapides sous les roues** (`ui/color_page/quick_settings.py`) : Exposition, Contraste, Saturation, Température, lignes de
  30 px (libellé, curseur, champ, losange). Ce sont des contrôles **neufs liés au même modèle** que l'inspecteur : ils passent par
  `on_color_grade_field_changed` (même historique, mêmes images clés) et `_mirror_color_field` montre la valeur du modèle dans les
  deux séries sans signal. `ColorWheels._fit_quick` les pose selon la hauteur de l'onglet **après** le minimum des roues (disque de
  128 px et 8 px de marge) : une ligne chacun (1440x900), deux par ligne (1222x738), deux par ligne sans curseur si la largeur
  manque (polices Linux), sinon masquées. Elles ne comptent ni dans `minimumSizeHint` ni dans `sizeHint` de l'onglet.

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

## Page Audio « Air d'abord » (canaux de 168 px, 3 pistes de 56 px)

Mesures à 1222x738 (zone utilisable d'un Air 13") : rangée du haut 200 à 209 px (moniteur, bibliothèque et inspecteur défilent),
timeline 302 px (zone des pistes 188 px : 3 pistes de 56 px et 12 px de marge), mixeur 140 px (zone des canaux 108 px).

* **Canal compact** (`MixerStrip(compact=True)`, 168 px de large, boutons de `Sizes.control_min` = 30 px) : nom, FX, M, S ;
  fader + valeur ; vumètre ; Pan ; puis armement, remise à zéro et sortie (le canal défile). 5 canaux = 880 px.
* **Master** : un canal de la même largeur à droite des pistes (dans une `ScrollGuard` : la sonie passe sur deux lignes avec
  des polices larges, rien n'est coupé).
* **Hauteurs** : `WorkspaceManager.balance_vertical_split` donne à la page Audio `Sizes.audio_top_row_height` (ou 31 % de la
  hauteur, plafonné à 260 px) à la rangée du haut ; la timeline reçoit 3 pistes. La hauteur de piste (56 px) est celle de la
  page (`TimelinePanel.row_height_override`, posée par `MainWindow.switch_page`) : le modèle (`Track.height_mode`) ne change pas.
* **Limite** : en 1280x690 (hors cible), 3 pistes de 56 px **et** un mixeur de 140 px ne tiennent pas dans 614 px : la timeline
  garde ~263 px (2 pistes et demie) ; les canaux gardent nom, FX, M, S et fader.
