# Petites tailles de fenêtre et navigation au clavier

Deux points ouverts du rapport de stabilisation : « mises en page aux petites tailles de fenêtre (panneaux rognés) » et
« focus clavier : navigation à la souris seulement ». Ce document dit ce qui a été **mesuré**, ce qui a été **corrigé**,
ce qui **reste**, les **règles** pour les futurs panneaux et comment **rejouer** l'audit.

## Tailles supportées

La fenêtre principale a un minimum de **1180 × 720** (`ui/main_window.py`). Trois tailles sont auditées et testées :

| Taille | Hauteur de la rangée du haut | Inspecteur (zone de défilement) |
| --- | --- | --- |
| 1440 × 900 | ~545 px | 312 px |
| 1280 × 720 | ~398 px | 285 px |
| 1180 × 720 | ~398 px | 272 px |

À 720 px de haut, la rangée du haut (bibliothèque, visionneuse, inspecteur) n'a que ~400 px : c'est le budget qui
oblige les blocs à **céder** au lieu de se superposer ou de se faire rogner.

## Ce qui a été mesuré et corrigé

Mesures faites hors écran (plateforme Qt `offscreen`, macOS), avec captures lues une à une : beaucoup de « contrôle
écrasé sous son `minimumSizeHint` » sont des faux positifs (boutons-icônes de taille fixe), seuls les contrôles
réellement coupés, illisibles ou inaccessibles ont été corrigés.

**Squelette (hauteur).** La somme des minima des panneaux (864 px) dépassait la hauteur disponible à 720 px : tout se
faisait écraser sous son minimum.

* Timeline : minimum du panneau 270 px, minimum de sa zone 240 px, donc +30 px de débordement hors de l'hôte. Le
  minimum du panneau (`Sizes.timeline_min_height`) vaut maintenant celui de la zone (`MIN_SIZE`).
* Inspecteur : panneau à 280 px, zone à 260 px, donc +20 px de débordement à 1180 px. `MIN_SIZE[INSPECTOR]` passe à 280.
* La zone basse oubliait les poignées entre ses panneaux dans son minimum (le mixeur perdait 6 px) ; défaut masqué
  jusque-là par la marge du panneau Timeline, révélé par l'alignement ci-dessus.
* La rangée du haut gardait 542 px à 900 px **par accident** (le minimum de layout de la bibliothèque). Maintenant que
  la bibliothèque peut défiler, `WorkspaceManager.balance_vertical_split` lui garde explicitement sa hauteur naturelle
  quand la fenêtre le permet : la disposition à 900 px ne change pas (545 / 273 px contre 542 / 276 avant).

**Bibliothèque (panneau gauche).** Le bloc dossiers / filtres / tags demandait 542 px au panneau pour ~400 px
disponibles : les puces de filtre se superposaient à la liste des dossiers. Le bloc défile maintenant
(`ShrinkableScrollArea`, plancher `BROWSE_MIN_HEIGHT`). Autres défauts trouvés en ouvrant **toutes** les sections par
l'API réelle (`ProjectPanel.select_section`) :

* les onglets de catégorie des bibliothèques d'effets, de transitions et d'effets audio étaient écrasés à ~30-50 px
  (libellés illisibles, **même à 1440 × 900**) : ils passent à la ligne (`FlowLayout`) ;
* le nom long d'une carte de preset repoussait l'étoile hors de la colonne : le titre est tronqué (`ElidedLabel`) ;
* l'en-tête (recherche + puces) d'une page défile quand la page est trop basse ;
* les boutons à libellé sans largeur minimale explicite imposaient la largeur de leur texte (« Importer » / « Timeline »
  tronqués en « Im…rter » à 1180 px) : `IconButton` leur donne un plancher, les marges latérales de la rangée diminuent.

**Moniteur (scopes affichés, 720 px).** Image de 143 × 85 px mesurée avant ; ≥ 130 px de haut et ~156 px mesurés
après (`ViewerHostSplitter`) : la visionneuse a une hauteur minimale utile (`Sizes.monitor_min_height`) et les scopes
cèdent d'abord, jusqu'à `Sizes.scopes_min_height`. À 900 px ils gardent leur hauteur d'avant (200 px). La barre
d'alertes des scopes, vide par défaut, ne prend plus de place ; le quad de scopes peut descendre sous 200 px sans
déborder.

**Inspecteur.** Défilement horizontal désactivé : chaque onglet imposait 309 à 409 px de contenu pour 272 à 312 px
visibles (panneau Suivi : « Réinitialiser » coupé, « Avant ▶▶ » à moitié visible, champs de « Zones » débordants,
**même à 1440 × 900**). Les contrôles ont été **ramenés dans la largeur**, sans défilement horizontal :

* rangées de boutons → `FlowLayout` (passent à la ligne) ; cases à libellé long → `WrappingCheckBox` ;
* formulaires → libellé au-dessus du champ quand la ligne est trop longue (`QFormLayout.WrapLongRows`) ;
* listes déroulantes → `make_shrinkable`, champs numériques côte à côte → `allow_shrinking` ;
* résultat : contenu minimal de 208 à 254 px pour 272 px disponibles, 18 px de marge.

**Éditeur de courbes.** Son minimum passait de 913 à ~730 px (liste des propriétés, interpolation et tangentes
rétrécissent, message d'aide tronqué) et sa taille initiale ne dépasse plus l'écran qui la porte.

**Panneau des calques** : testé par la vraie API (`ProjectPanel.select_section("graphics")`), rien de coupé ni de
superposé aux trois tailles (l'audit initial avait forcé la page de la pile : la barre de la bibliothèque restait).

Robustesse aux polices : le même audit à **+10 % ou +20 %** de taille de police simulée (`--font-scale`) ne trouve rien
à 1180 × 720 ; à +30 % il reste deux dépassements de 5 à 6 px (panneau Suivi, éditeur de sous-titre).

## Clavier

### Règles (et pourquoi)

* **Fenêtre principale et inspecteur.** Un bouton qui prend le focus *au clic* vole les touches de lecture : après un
  clic sur « Ajouter » (Suivi), Espace re-déclenchait le bouton au lieu de lire. Les contrôles sans saisie de texte
  (boutons, cases, listes déroulantes non éditables, curseurs) sont donc en `Qt.TabFocus` (`ui/keyboard_navigation.py`) :
  Tab les atteint, un clic ne leur donne jamais le focus. Les champs de saisie gardent le focus au clic ;
  `ShortcutManager` les épargne déjà (aucune touche de commande ne s'y déclenche). Au clavier, un bouton focalisé garde
  Espace : c'est le comportement d'un formulaire. Les boutons-icônes de la barre principale, de la timeline et des
  bibliothèques restent `NoFocus` exprès (inchangé).
* **Dialogues.** Tout est atteignable, **un seul bouton par défaut** (`set_single_default`), Échap annule, Entrée valide.
  Un bouton-icône de dialogue est focalisable et porte un nom accessible traduit (`ui/i18n_accessibility.py`).

### Corrigé

* Bouton par défaut : « Restaurer les réglages » (Préférences simples) et « Retirer » un raccourci (Préférences
  complètes) devenaient le bouton d'Entrée ; « Choisir une couleur… » dans le gestionnaire de tags. Maintenant
  « Fermer », « Fermer » et « Créer » (Entrée dans le champ du nom crée le tag).
* Gestionnaire de tags : Renommer / Couleur / Supprimer étaient inaccessibles au clavier (`NoFocus`) ; focalisables et
  nommés, ordre de tabulation = ordre d'affichage (les lignes de tags sont créées après le formulaire).
* Tab ne reste plus piégé : Tab sort des descriptions multilignes (`setTabChangesFocus`), les zones défilantes
  conteneurs ne sont plus un arrêt invisible. Échap ferme la page d'export (la croix a un nom, la file de rendu aussi).
* Inspecteur : onglets, boutons d'action, cases, curseurs et boutons-icônes atteignables ; l'ordre de tabulation suit
  l'affichage (le groupe graphique est créé après « Mouvement » mais affiché avant) ; les boutons ‹ › ◆ des images-clés
  et les neuf tuiles d'alignement ont un nom accessible.
* Thème : le focus des champs existait déjà, il manquait pour les boutons, cases, listes et curseurs. Ajouté, sans
  changer le reste (`QPushButton:focus`, `QCheckBox::indicator:focus`, …) ; les feuilles de style locales de l'inspecteur
  (onglets, rack d'effets) reçoivent leur `:focus`.

### Menus

La barre de menus se parcourt aux flèches. Chaque titre porte une **lettre mnémonique** (`&Fichier`, `&File`,
`&Archivo` : Alt + lettre sous Windows / Linux, retirée par Qt sous macOS), unique par menu et par langue sur tous les
menus possibles de la barre (Fichier, Édition, Affichage, Séquence, Calques, Fenêtre, Aide) :

| | fichier | édition | affichage | séquence | calques | fenêtre | aide |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fr | F | D | H | S | C | N | A |
| en | F | E | V | T | L | W | H |
| es | A | E | V | S | C | N | Y |

## Ce qui reste

* **Plateformes** : tout a tourné sur macOS / `offscreen`. Les tests sont structurels (aucun pixel, aucune taille
  absolue), mais les polices de Windows et Linux n'ont pas été essayées ici ; la marge simulée est de +20 %. Le test
  d'Alt + lettre est **sauté sur macOS** (le thème n'a pas de mnémoniques) : il ne tourne qu'en CI Windows / Linux.
* **Éditeur de courbes** : minimum ~730 px, pas de défilement (la barre d'outils ne se replie pas).
* **Onglet spécialisé de l'inspecteur** : son nom (« Graphiques », « Compositing ») est tronqué dans le bouton « ••• » à
  280 px (« Gr…es »). L'info-bulle de chaque tuile d'alignement est encore en anglais (nom accessible traduit).
* **Transitions à 1180 px** : quatre rangées de puces et trois boutons pour ~340 px de colonne ; l'en-tête défile.
* **Barre de menus sous macOS** : barre intégrée à la fenêtre (`setNativeMenuBar(False)`), sans accès Alt.
* **Page d'export** : Échap la ferme, mais Entrée n'y lance rien (le bouton primaire est atteint avec Tab).
* Tout ce qui est hors de l'espace de montage (bibliothèques, timeline, barre) garde `NoFocus` : la navigation au clavier
  y reste réservée aux raccourcis.

## Règles pour les futurs panneaux

1. **Un panneau doit tenir à 1180 × 720.** Pas de largeur minimale qui dépasse l'inspecteur (≈ 250 px utiles), pas de
   hauteur minimale qui dépasse la rangée du haut (≈ 400 px) : le contenu défile ou cède.
2. **Pas de défilement horizontal en dernier recours sans le documenter.** Rangée de boutons → `FlowLayout`, case à
   libellé long → `WrappingCheckBox`, libellé de formulaire → `WrapLongRows`, liste déroulante → `make_shrinkable`,
   titre long → `ElidedLabel`, bloc de hauteur variable → `ShrinkableScrollArea`.
3. Le minimum déclaré d'un panneau ne dépasse pas celui de sa zone (`core.workspace_state.MIN_SIZE`).
4. **Un dialogue a un seul bouton par défaut** (`set_single_default`) et Tab sort de ses éditeurs multilignes.
5. **Un bouton-icône de dialogue est focalisable et nommé** (`setAccessibleName(translate("a11y…"))`, clés dans
   `ui/i18n_accessibility.py`).
6. Dans l'espace de montage, un bouton ne prend **jamais** le focus au clic : `Qt.TabFocus` (`tab_only`).
7. Tout contrôle d'un formulaire d'inspecteur est atteignable avec Tab dans l'ordre d'affichage
   (`follow_layout_order` si des widgets sont ajoutés après coup).
8. Tout titre de menu racine a une lettre mnémonique unique dans sa langue.

## Rejouer l'audit

```sh
QT_QPA_PLATFORM=offscreen python -m tools.ui_audit                    # 3 tailles : constats bloquants
QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --out /tmp/audit   # + une capture PNG par scénario et par taille
QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --font-scale 1.2   # polices simulées 20 % plus larges
QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --verbose          # + constats informatifs (faux positifs fréquents)
QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --dialogs          # clavier des dialogues
```

L'outil isole les préférences, caches et proxys (dossier temporaire). Les constats **bloquants** sont ceux que les tests
interdisent : `clipped` (contenu coupé dans une zone sans défilement horizontal), `overflows-host` (un bloc sort de
son conteneur), `overlaps` (deux frères d'un même layout se chevauchent), et, pour les dialogues, `keyboard`, `default`
et `unnamed`. Les contrôles « sous leur `minimumSizeHint` » ne sont qu'informatifs : lire la capture avant de corriger.

## Tests

* `tests/test_ui_small_windows.py` : par taille de fenêtre, inspecteur (7 onglets, clips spéciaux), panneau Suivi,
  bibliothèque (8 sections), squelette, moniteur, éditeur de courbes, export, polices +20 %, outil d'audit.
* `tests/test_adaptive_layout.py` : `FlowLayout`, `WrappingCheckBox`, `ShrinkableScrollArea`, `ElidedLabel`, `make_shrinkable`,
  `allow_shrinking`.
* `tests/test_ui_keyboard.py` : dialogues (bouton par défaut, Entrée / Échap, parcours par Tab, noms accessibles),
  inspecteur (politique de focus, ordre de tabulation), menus (mnémoniques, flèches), raccourcis globaux intacts.
* `tests/test_theme_focus.py` : chaque type de contrôle se dessine différemment avec et sans focus (comparaison relative,
  jamais de pixels de référence).
