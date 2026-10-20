# Niveaux d'usage (ADR-0006, étape 4)

**Statut :** livré (lots 1 à 6, 2026-10-15 à 2026-10-17). Décisions validées par l'utilisateur le 2026-10-15 : le mécanisme, le niveau par défaut, le comportement de « Restaurer » et la répartition ci-dessous. Étape 4 de l'[ADR-0006](adr/0006-composition-visuelle-progressive.md).

Le document a deux parties : la [première](#partie-1--pour-lutilisateur) décrit ce que voit l'utilisateur, la [seconde](#partie-2--pour-le-développeur) explique comment classer un contrôle et garder le classement vrai. Les [déplacements acceptés](#déplacements-de-contrôles-et-classements-non-précisés-par-la-spécification) et les [limites connues](#limites-connues) closent le document.

# Partie 1 : pour l'utilisateur

## Les trois niveaux

| Niveau | Pour qui | Ce qu'il montre |
|---|---|---|
| **Simple** | monter vite : couper, titrer, poser un son, publier | l'essentiel de chaque surface |
| **Studio** | un montage plus fin : couleur, vitesse, effets, calques, outils de timeline | Simple + les fonctions courantes d'un monteur |
| **Avancé** | tout le programme | tout, comme avant l'existence des niveaux |

Un niveau **ne montre ou ne cache que**. Il ne supprime aucune fonction, ne désactive rien, ne change aucune valeur : un réglage caché garde sa valeur (le modèle seul porte les valeurs), un clip, une piste ou un calque verrouillé le reste à tous les niveaux.

## Changer de niveau

Trois emplacements pour le même réglage, appliqués à chaud (aucune confirmation, aucun redémarrage) :

- **Préférences** : groupe « Niveau d'usage » (trois boutons, une infobulle courte par niveau).
- **Menu Fenêtre** : sous-menu « Niveau d'usage » (trois actions cochables, aucun raccourci par défaut).
- **Barre du haut** : puce « Simple / Studio / Avancé » avant « Réglages » ; elle s'abrège, puis disparaît, quand la barre manque de place.

Si la page active ou la section active du rail de la bibliothèque disparaît au nouveau niveau, l'interface retombe sur Montage et sur « Médias » ; le projet, la sélection et la position de lecture sont conservés.

## Comportement par défaut

- **Installation neuve** (aucun `user_settings.json`) : **Simple**.
- **Préférences existantes** sans niveau (ou avec une valeur invalide, ou illisible) : **Avancé** : rien ne change pour qui avait déjà le programme.
- **« Restaurer les valeurs par défaut »** ne touche pas au niveau : c'est un choix personnel d'interface.
- Le niveau est une **préférence utilisateur** : il n'est **jamais écrit dans le `.kut`**, un projet ouvert sur une autre machine s'affiche au niveau de cette machine.

## Rien n'est inaccessible

- **Inspecteur** : le lien **« Afficher plus d'options »**, en bas, révèle d'un geste les groupes d'un niveau supérieur (il se referme à chaque changement de niveau). Dans un groupe visible, les réglages d'un niveau supérieur sont repliés derrière un pliant **« Avancé »**, placé sous les réglages de base.
- **Menus, rail, barre de la timeline, pages** : une entrée absente reste atteignable en montant d'un niveau.
- **Raccourcis clavier : toujours actifs, quel que soit le niveau.** Une entrée retirée d'un menu garde son raccourci ; un outil caché de la timeline (R, Y, U, enregistrement) agit toujours. Le choix d'un raccourci se fait dans les Préférences, quel que soit le niveau.

## Ce qui est visible à chaque niveau

Le niveau indiqué est le **minimal où l'élément est affiché directement**. La liste exhaustive (une ligne par contrôle) est le manifeste `tests/data/usage_level_controls.json`.

### Inspecteur

| Groupe ou contrôle | Niveau | Remarque |
|---|---|---|
| Clip sélectionné ; Paramètres du projet (replié) | Simple | |
| Mouvement : position X/Y, échelle, rotation, opacité, réinitialiser | Simple | |
| « Remplir le cadre » (propriété `fill`) | Simple | case relais de celle de la transformation avancée |
| Mouvement : losanges d'images-clés (‹ ◆ ›) ; alignement dans le cadre (6 boutons) | Studio | |
| Transformation avancée (ancrage, échelle X/Y, miroirs, pan, inclinaison) | Avancé | |
| Menu clic droit des losanges (interpolation, copier/coller, courbes) | Avancé | sous Avancé, le menu des losanges n'offre que l'activation de l'animation |
| Recadrage | Simple | |
| Gain du clip, fondus ; volume du moniteur | Simple | |
| Panoramique du clip | Studio | |
| Vitesse : valeur, préréglages, Reverse, Figer, durées | Studio | le sous-menu Vitesse du clic droit suit |
| Vitesse : courbe, interpolation, qualité, flux optique, audio de la courbe (`TimeSection`) | Avancé | |
| Étalonnage : exposition, contraste, saturation, température, teinte, ombres, hautes lumières | Studio | |
| Étalonnage : courbes, préréglages, LUT 3D | Avancé | |
| Effets du clip : ajouter, liste, activer, retirer ; flou, netteté, vignette, noir et blanc, sépia, lueur, aberration chromatique, brume de chaleur | Studio | |
| Effets du clip : monter / descendre (ordre) | Avancé | |
| Effets du clip : losanges d'images clés ‹ ◆ › sous les réglages animables (correction couleur, vignette) | Studio | « Afficher plus d'options » les montre aussi en Simple (le groupe Effets est lui-même Studio) |
| Effets du clip : menu clic droit du losange : interpolation, copier / coller, éditeur de courbes | Avancé | « Désactiver l'animation » et « Supprimer l'animation de l'effet » dès Studio |
| Transition (type, durée) | Studio | visible seulement si une transition est sélectionnée |
| Sous-titre : contenu, police, taille, opacité, couleur du texte, alignement | Simple | visible seulement sur un sous-titre |
| Sous-titre : position/marges/remplissage, contour, ombre, fond | Studio | |
| Calque graphique : type, texte, taille, couleur, police | Simple | visible seulement sur un calque graphique |
| Calque graphique : contour, ombre et fond, animation de texte | Studio | |
| Calque scène 3D : fichier `.kut3d` et « Recharger » | Studio | visible seulement sur une scène 3D ; la vitesse y est grisée à tous les niveaux |
| Calque graphique : lueur néon, lumière, forme, parent, flou de mouvement | Avancé | |
| Compositing : modes de fusion Normal, Multiplier, Écran, Incrustation, Assombrir, Éclaircir | Studio | |
| Compositing : modes Addition, Différence | Avancé | le mode déjà choisi dans un projet reste affiché |
| Compositing : masques rectangle et ellipse (ajouter/supprimer, inverser, adoucissement, opacité) ; clé chroma | Studio | |
| Compositing : masques polygone et courbe, opération soustraire/intersecter, expansion, rotation, édition dans le moniteur | Avancé | |
| Suivi (tracking) | Avancé | |
| Effets audio : normaliser, basses, aigus | Studio | |
| Effets audio : voix, réduction de bruit, compresseur, limiteur, téléphone, réverb, écho, EQ paramétrique | Avancé | |

Les groupes Transition, Sous-titre et Calque graphique restent cachés hors contexte, comme avant les niveaux.

### Menus de la barre

| Élément | Niveau |
|---|---|
| Fichier (tout), Aide (tout) | Simple |
| Édition : annuler, rétablir, dupliquer, supprimer, activer/désactiver | Simple |
| Édition : suppression en cascade | Studio |
| Séquence : couper à la tête de lecture, marqueur, ajouter/supprimer/renommer une piste, verrouillage, visibilité, muet | Simple |
| Séquence : détection de scènes, proxies pour la sélection | Studio |
| Séquence : nouvelle/renommer/dupliquer/supprimer une séquence, imbriquer, ouvrir/parent/retour/avance, convertir en composition, nouvelle composition, multicam | Avancé |
| Calques : Ajouter ▸ texte ; réinitialiser le recadrage | Simple |
| Calques : Ajouter ▸ forme, nul, ajustement, scène 3D ; grouper/dégrouper ; copier/coller (calques, attributs) ; préréglages ; zones de sécurité, repères, grille, aimantation, centre ; Aligner | Studio |
| Calques : flou de mouvement et réglages ; panneau de suivi | Avancé |
| Réseaux sociaux : nouveau projet social, réglages, zones de plateforme, remplir le cadre, sous-titres automatiques, repère de couverture | Simple |
| Réseaux sociaux : grille de rythme, Recadrer en suivant le tracker, Ken Burns, Impact, audio social, versions de format, classement, Photos | Studio |
| Fenêtre : pages Montage et Audio, panneaux, espaces de travail, préférences, réinitialiser la disposition | Simple |
| Fenêtre : page Couleur ; scopes ; Utiliser les proxies | Studio |
| Fenêtre : page Composition ; éditeur de courbes ; diagnostics | Avancé |

### Pages, rail, barre de la timeline

| Surface | Simple | Studio | Avancé |
|---|---|---|---|
| Boutons de pages (barre du haut) | Montage, Audio | + Couleur | + Composition |
| Rail de la bibliothèque | médias, montage, texte, audio, graphiques | + effets, transitions, couleur, séquences, modèles | tout |
| Barre de la timeline | sélection, lame, aimantation, ripple, marqueur | + roll, slip, slide, enregistrement | tout |

### Menus contextuels

| Menu | Simple | Studio | Avancé |
|---|---|---|---|
| Clip | couper, dupliquer, activer, supprimer | + Supprimer et refermer, sous-menu Vitesse hors courbe (vitesses, sens inverse, garder la durée, copier, coller, réinitialiser) | + imbriquer, convertir, ouvrir, Multicam, images intermédiaires, qualité et analyse du flux, courbe de vitesse et son de la courbe |
| Piste (⋯) | hauteur, replier, monter/descendre, renommer, supprimer | idem | + courbe de volume, rôle, « Baisser sous… » (ducking) |
| Média de la bibliothèque | renommer, sélectionner dans la timeline, dossiers, supprimer | + étiquettes, relier, proxy, matrice couleur | + « Créer une séquence Multicam… » (sélection multiple) |
| Calque (panneau Calques) | renommer, dupliquer, supprimer | + grouper, dégrouper, entrer dans le groupe, copier / coller les attributs, enregistrer comme préréglage | + parent, flou de mouvement |
| Ajouter un calque (menu du fond et bouton « Ajouter ») | texte, aplat de couleur, image | + formes, contrôleur nul, calque d'effets (la scène 3D n'est que dans le menu Calques, Studio) | idem |
| Barre du panneau Calques | « Ajouter » | + « Grouper », « Préréglages » | idem |
| Bande d'automation d'une piste audio | supprimer le point, effacer la courbe | idem | + ajouter un point ici, remettre à 0 dB |
| Dossiers de la bibliothèque | tout (nouveau, sous-dossier, renommer, supprimer) | idem | idem |
| Éditeur de nœuds Couleur / Composition | (pages absentes) | tout (page Couleur) | tout (page Composition) |

## Déplacements de contrôles et classements non précisés par la spécification

Ces points ont été **acceptés par l'utilisateur** (PR #183 et #186). Ils sont aussi dans le CHANGELOG de la 0.6.4.

### Déplacements au niveau Avancé

Pour que les réglages de base restent au-dessus du pliant « Avancé », l'ordre des contrôles au niveau Avancé n'est **pas** exactement celui d'avant les niveaux (`tests/data/inspector_order_before_usage_levels.json`). Les écarts :

1. **Mouvement** : les losanges d'images-clés ‹◆› sont regroupés en bas du groupe ; « Réinitialiser » passe **avant** l'alignement et la transformation avancée.
2. **Audio du clip** : le panoramique passe **après** les fondus.
3. **Vitesse** : la section « Temps » passe **en fin de groupe**.
4. **Étalonnage** : « Réinitialiser » passe **avant** les courbes.
5. **Compositing** : le recadrage passe **avant** la fusion.
6. **Sous-titre et Calque graphique** : sections réordonnées (Simple, puis Studio, puis Avancé).

7. **Effets du clip animables** (ADR-0008, étape 4) : sous les réglages animables de l'effet sélectionné apparaît une rangée ‹ ◆ ›, dans sa propre ligne du formulaire (le curseur reste un enfant direct : son ordre est celui d'avant). Les goldens `inspector_order_before_usage_levels.json` ne sélectionnent jamais d'effet (`clip_states()` n'en porte aucun) et ne sont **pas** régénérés : ces rangées sont des ajouts, pas un ordre déplacé. Elles figurent pourtant dans `moved_roots` (`tests/inspector_order.py`, racines relevées après chaque `show_clip` puisque les rangées sont reconstruites avec l'effet affiché), pour qu'un futur golden avec un effet sélectionné les écarte de la comparaison. L'énumération des contrôles joue en revanche un passage par effet (`EFFECT_SCENARIOS` dans `tests/usage_level_inventory.py` : correction couleur, vignette, netteté) : leurs contrôles sont au manifeste.

Les menus, boutons et menus contextuels au niveau Avancé sont, eux, ceux d'avant (`tests/data/menus_before_usage_levels.json`).

### Classements que la spécification ne donnait pas

- Réseaux sociaux : « Recadrer en suivant le tracker » et « Ken Burns sur la sélection » : **Studio**.
- Fenêtre : « Utiliser les proxies » : **Studio**.
- Clic droit d'un clip : « Supprimer et refermer » et le sous-menu Vitesse hors courbe : **Studio** (le reste de Vitesse : Avancé).
- Clic droit d'un média : « Créer une séquence Multicam… » : **Avancé**.
- Édition « Couper / Copier / Coller » et Séquence « Ajouter un clip » : désactivées à dessein par le programme, elles restent en Simple.

### Écarts à l'intérieur de l'inspecteur

- Dans Vitesse, Étalonnage et Effets, quelques réglages Studio restent sous le pliant « Avancé » (ces groupes sont Studio, donc absents du niveau Simple).
- Dans Compositing, le pliant Avancé est en tête des masques ; les champs Studio des masques sont dessous.

# Partie 2 : pour le développeur

## Le modèle

- `core/usage_level.py` : `UsageLevel` (valeurs de chaîne stables `simple`, `studio`, `advanced` ; ordre simple < Studio < avancé via `UsageLevel.at_least`), `coerce_usage_level`, `DEFAULT_NEW_INSTALL` (Simple), `DEFAULT_EXISTING` (Avancé).
- Préférence : `UserSettings.usage_level` (`core/user_settings.py`), jamais dans le `.kut`. Le champ s'appelle `usage_level` (pas « level » : collision avec les « niveaux » des scopes et l'imbrication des séquences).
- `MainWindow.set_usage_level` (`ui/main_window_mixins/preferences.py`) enregistre et émet `usage_level_changed(str)` seulement si le niveau change. Tout le reste lit `MainWindow.usage_level` et suit le signal.
- Bascule : `ui/main_window_mixins/usage_level.py` (actions du menu Fenêtre et puce de la barre du haut, qui réutilise les mêmes actions) ; groupe des Préférences déclaré dans `_CHOICE_GROUPS` (`ui/preferences_dialog.py`) ; libellés fr/en/es dans `ui/i18n_usage.py`. Mettre une action à jour par programme n'émet pas `triggered` : pas de boucle.
- `_settings_snapshot` (`ui/main_window_mixins/preferences.py`) reconstruit `UserSettings` champ par champ : tout champ ajouté doit y figurer, sinon il est remis au défaut à chaque sauvegarde d'un autre réglage. `_restore_default_preferences` doit **préserver** `usage_level`.

## Où est la table de chaque surface

| Surface | Table / point d'entrée | Fichier |
|---|---|---|
| Menus de la barre | `MENU_GATES` (par clé de traduction du libellé ; un sous-menu par sa clé suivie de `>` ; un tuple `(niveau, {sous-entrées})` pour un sous-menu mixte) | `ui/main_window_mixins/menu_levels.py` |
| Boutons de pages et entrées du menu Fenêtre | `PAGE_LEVELS` | `ui/main_window_mixins/menu_levels.py` (boutons : `page_buttons`, `ui/main_window_mixins/color_page.py`) |
| Rail de la bibliothèque | `SECTION_LEVELS`, `SideRail.set_usage_level` | `ui/side_rail.py` |
| Barre de la timeline | `STUDIO_TOOLS` (+ enregistrement), `TimelinePanel.set_usage_level` | `ui/timeline_panel_mixins/usage_levels.py` |
| Menus contextuels clip, piste, média, calque, bande d'automation | construits à chaque ouverture : ils lisent le niveau du panneau (`ui/timeline_panel_mixins/`, `ui/project_panel.py`, `ui/library_organization_widgets.py`, `LayersPanel.set_usage_level` relayé par `ProjectPanel`, `AutomationLane.level_provider`) | voir ces fichiers |
| Groupes de l'inspecteur | `_group_levels` | `ui/properties_panel_mixins/usage_levels.py` |
| Réglages de l'inspecteur | `LevelGate` / `gated(widget, niveau)` | `ui/properties_widgets/level_gate.py` |
| Pliants « Avancé » | `AdvancedFold` (un par palier : Studio ou Avancé) | `ui/properties_widgets/level_gate.py` |
| Choix de listes (modes de fusion, formes de masque, opérations, effets audio) | `_limit_plan` → `_level_limits` | `ui/properties_panel_mixins/usage_levels.py` |
| Visibilité des groupes (point unique) | `_apply_group_visibility`, 3e condition | `ui/properties_panel_mixins/tabs.py` |

Un contrôle **Simple n'a pas d'entrée de table** : ce qui n'est dans aucune table est Simple.

## Mécanismes

- **Inspecteur.** `PropertiesPanel.usage_level` vaut Avancé sans fenêtre ; la fenêtre principale l'impose à la création et le suit (`set_usage_level`, aucun widget recréé). Groupes Transition, Étalonnage, Vitesse, Effets et Effets audio : Studio ; Suivi : Avancé ; le reste : Simple. Chaque réglage d'un niveau supérieur est dans un `LevelGate` créé avec le contrôle, dans son constructeur. Chaque builder crée son `AdvancedFold` à la place voulue, devant ce qu'il révèle ; ouvert, il montre les réglages de son palier et au-delà ; au niveau Avancé aucun pliant n'est visible. Les choix de listes sont cachés de la liste sans être retirés ; le choix courant reste affiché.
- **Menus.** Une entrée au-dessus du niveau est **retirée du menu** (`QMenu.removeAction`) et **remise à sa place d'origine** (`insertAction`) quand le niveau monte : rien n'est recréé, désactivé ni reparenté. Les séparateurs ne sont montrés qu'entre deux entrées visibles. Les entrées sont retraduites à chaud (`_translated_actions`, `_translated_menus` dans `ui/main_window.py`).
- **Raccourcis.** Un `QAction` caché par `setVisible(False)`, ou retiré de tous les menus, perd son raccourci. Les actions de commande (`ShortcutManager.command_actions`) sont donc attachées à la fenêtre (`addAction`) à l'installation. `tests/test_usage_level_menus.py` presse les raccourcis des entrées absentes, à chaque niveau, avec de vraies frappes.
- **Barre de la timeline.** Les raccourcis (R, Y, U, enregistrement) agissent par le bouton, caché ou non ; un outil caché ne reste pas actif (retour à la sélection) ; un enregistrement en cours garde son bouton visible.
- **Descente de niveau.** Si la page active ou la section active du rail disparaît, l'interface retombe d'abord sur Montage (`switch_page`) et sur « Médias », puis applique le niveau.
- **Hors périmètre.** Les boutons cachés `top_nav_buttons` (jamais affichés) ne sont pas touchés.

## Règles

1. **Pas de reparentage** : ne pose jamais un conteneur après coup à la place d'un widget déjà dans un layout (`replaceWidget`, `setParent`, `addItem`) : l'élément de layout retiré reste référencé par le widget (hauteur périmée) et le supprimer plante Qt. Le conteneur est créé avec le contrôle.
2. **Pas de `setEnabled`** : un niveau ne fait que montrer ou cacher (visibilité du conteneur, de l'entrée de menu ou du bouton). Il n'écrit jamais une valeur.
3. **Les verrous de piste et de calque restent prioritaires** : `show_clip` (`ui/properties_panel_mixins/clip_context.py`) et `clip_edit_refusal` (`core/compositing_guard.py`) s'appliquent à tous les niveaux ; un contrôle caché garde sa valeur.
4. **Un seul écrivain de visibilité par groupe** : voir les pièges ci-dessous (P3).
5. **Les raccourcis ne dépendent jamais du niveau.**

## Classer un nouveau contrôle

1. Donne-lui un niveau **dans le code**, au même endroit que ses voisins (tableau ci-dessus). Un contrôle Simple n'a pas d'entrée de table.
2. Lance `tests/test_usage_level_inventory.py` : il échoue en nommant le contrôle (« contrôle sans classement »).
3. Régénère le manifeste puis relis la différence (`git diff tests/data/usage_level_controls.json`) : seule la ligne du contrôle neuf doit apparaître, avec le bon niveau.
   `KUT_REGEN_USAGE_MANIFEST=1 QT_QPA_PLATFORM=offscreen KUT_STUDIO_CONFIG_DIR=<dossier jetable> python -m pytest tests/test_usage_level_inventory.py -k regenerate`
4. Les fichiers `tests/data/*_before_usage_levels.json` ne se régénèrent **jamais** sur une branche : seulement sur un worktree d'`origin/main` (`tests/menu_order.py` pour les menus).
5. Traduis le libellé en fr / en / es (`python -m tools.i18n_audit`), puis lance la suite complète.

## L'énumération des contrôles

`tests/test_usage_level_inventory.py` (outil : `tests/usage_level_inventory.py`) part de la **vraie fenêtre** hors écran, jamais d'une copie des tables :

- **Niveau déclaré** par le code pour chaque contrôle : `MENU_GATES`, `PAGE_LEVELS`, `SECTION_LEVELS`, `STUDIO_TOOLS` et l'enregistrement, `_group_levels`, les `LevelGate`, les choix de listes (`_level_limits`).
- **Niveau observé** : ce que l'écran montre à chaque niveau (menus et sous-menus, boutons, menus contextuels clip, segment multicam, pistes et média, inspecteur sur les états de `inspector_order.clip_states()` et tous les onglets), sans geste.
- **Manifeste** `tests/data/usage_level_controls.json` : la clé et le niveau de **chaque** contrôle, y compris ceux du niveau Simple. Un contrôle neuf absent du manifeste fait échouer le test.

Le test vérifie : (a) le niveau déclaré est celui du manifeste, sans contrôle de trop ni manquant ; (b) aucun orphelin : tout ce qui est affiché à un niveau est classé, au plus ce niveau, et tout contrôle classé à un niveau y est affiché ; (c) monotonie : Simple est contenu dans Studio, contenu dans Avancé, strictement, aux seuls contrôles de navigation près (pliants « Avancé », lien « Afficher plus d'options », doublure d'« Remplir le cadre ») qui changent de côté à dessein ; (d) au niveau Avancé, les menus, boutons et menus contextuels sont ceux du golden d'`origin/main` et l'inspecteur celui de `inspector_order_before_usage_levels.json` ; (e) « aucune fonction inaccessible » : avec « Afficher plus d'options » et tous les pliants ouverts, tout ce que le niveau Avancé montre dans l'inspecteur est atteignable depuis Simple et Studio.

**Critère de l'ADR-0006 (étape 4)** : « chaque contrôle appartient à un niveau, vérifié par un test qui énumère les contrôles de l'inspecteur ; aucune fonction n'est inaccessible en mode simple (elle est seulement repliée) ».

## L'audit de mise en page à chaque niveau

`python -m tools.ui_audit --levels all` rejoue les scénarios à Simple, Studio et Avancé (`--levels simple,studio` pour une liste ; `run_audit(levels=...)` et `make_main_window(usage_level=...)` pour le code). Le défaut reste le niveau Avancé, le chemin d'avant les niveaux. Aux niveaux Simple et Studio, des scénarios `inspecteur-tout-ouvert-<onglet>` déploient « Afficher plus d'options » et tous les pliants, la mise en page la plus chargée du niveau. Les captures d'un autre niveau portent son nom (`1180x720-simple-defaut.png`). `tests/test_ui_small_windows.py` joue les trois niveaux (fixture `window_at(taille, niveau)`) : inspecteur, suivi, bibliothèque, panneaux, moniteur, barres et, en sous-processus, 1180 × 720 avec polices +20 %. L'audit multiplie le temps par trois.

## Pièges connus

- **P1.** Un `QAction` avec `setVisible(False)` perd son raccourci : garde l'action sur la fenêtre (`window.addAction`) et retire l'entrée du menu.
- **P2.** Les paquets gardés par `tests/test_ui_modularization.py` plafonnent à 700 lignes par module. `ui/properties_panel_mixins/construction.py` fait 592 lignes ; le code des niveaux va dans un module à part (`usage_levels.py`).
- **P3.** Plusieurs écrivains de visibilité dans l'inspecteur (`_apply_group_visibility` dans `tabs.py`, `set_graphic`, `set_skew_available`, `TimeSection._show_row`, `update_effects_from_clip`, `set_chroma_visible`, `_refresh_mask_widgets`) : un niveau ne s'ajoute qu'au point unique `_apply_group_visibility` (3e condition) et à des conteneurs repliables, jamais à des `setVisible` isolés.
- **P4.** `CropEditor` est enfant de `CompositingEditor` : le groupe est scindé en sous-blocs (recadrage, fusion, masques, clé chroma).
- **P5.** i18n fr/en/es, textes courts, noms accessibles (`tests/test_ui_honesty.py`), 1180 × 720 à +20 % de police.
- **P6.** La retraduction à chaud (`_translated_actions`, `_translated_menus`) est nécessaire pour les entrées de menu qui disparaissent et réapparaissent.

# Limites connues

- **Le manifeste ne prouve pas que le niveau choisi est le bon.** Il est produit à partir du code : il prouve que chaque contrôle a été classé et que le classement n'a pas bougé sans relecture. Le bon niveau d'un contrôle est une décision de produit.
- **Contrôles d'inspecteur hors contexte.** L'inventaire part des états de `inspector_order.clip_states()` : les contrôles qu'aucun de ces états ne montre (réglages de masque ou de clé chroma hors contexte) ne sont vérifiés que par leur niveau déclaré.
- **En-tête du menu d'un média.** Le manifeste contient `ctx_asset/[en-tête]` (Simple) : c'est l'en-tête non cliquable du menu d'un média (nom et statut, un `QWidgetAction` sans libellé), pas une entrée oubliée ni un défaut.
- **Bouton « Forme » de la section Graphiques.** `ui/graphics_library.py` offre « Forme » (rectangle) et « Aplat » dès Simple, alors que « Ajouter ▸ forme » est Studio dans le menu Calques et dans le menu du panneau Calques. Incohérence connue, laissée en l'état dans ce lot : à traiter dans un lot suivant (cacher le bouton ou reclasser la forme).
- **La bande d'automation s'affiche d'elle-même** dès que la courbe de volume d'une piste a des points (`automation_shown`), même à Simple : l'entrée de menu de piste qui l'active est Avancé, mais un projet reçu avec une courbe la montre. C'est pourquoi « Supprimer le point » et « Effacer la courbe » restent Simple dans son menu (nettoyer ce qu'on voit) ; ajouter un point et remettre à 0 dB sont Avancé.
- **Éditeurs de nœuds et arbre de dossiers : aucun retrait.** Les menus de `ui/color_page/node_editor.py` et `ui/composition_page/node_view.py` sont classés par leur page (Studio, Avancé) ; aucune de leurs entrées n'existe à un niveau plus bas. Les entrées de l'arbre de dossiers sont toutes Simple (comme « Déplacer vers » dans le menu d'un média). Ils sont au manifeste.
- **Raccourcis sous macOS réel.** Les tests pressent de vraies frappes hors écran (`Ctrl`) ; les raccourcis `Cmd` n'ont pas été joués sur un macOS réel.
- **Windows et Linux** ne sont vérifiés que par la CI.
