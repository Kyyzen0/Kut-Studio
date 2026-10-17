# Niveaux d'usage (ADR-0006, étape 4)

**Statut :** décidé le 2026-10-15 (l'implémentation avance par lots). L'utilisateur a validé le mécanisme, le niveau par défaut, le comportement de « Restaurer » et la répartition ci-dessous.

## Avancement par lots

| Lot | Contenu | État |
|---|---|---|
| 1 | Modèle (`core/usage_level.py`), préférence `usage_level`, signal `usage_level_changed`, aucun effet visible | **fait** (cette PR) |
| 2 | Bascule du niveau : groupe dans les Préférences, sous-menu « Niveau d'usage » du menu Fenêtre, puce dans la barre du haut ; libellés fr/en/es | **fait** |
| 3 | Inspecteur par niveau : point unique `_apply_group_visibility` (3e condition), conteneurs de niveau créés à la construction, sous-blocs « Avancé », lien « Afficher plus d'options » | **fait** |
| 4 | Menus, menus contextuels, rail de la bibliothèque, barre de la timeline, pages (retrait des entrées, raccourcis conservés) | **fait** |
| 5 | Test d'énumération des contrôles (critère de l'ADR) et dimension « niveau » dans `tools/ui_audit.py` | **fait** |
| 6 | Documentation, état d'avancement de l'ADR-0006, CHANGELOG | à faire |

## Contrat du modèle (lot 1)

- Valeurs de chaîne stables : `simple`, `studio`, `advanced` ; ordre simple < Studio < avancé (`UsageLevel.at_least`).
- Défaut : aucun `user_settings.json` (installation neuve) donne `simple` ; fichier existant sans `usage_level`, avec une valeur invalide, ou illisible donne `advanced` ; valeur valide : cette valeur.
- Le niveau n'est jamais écrit dans le `.kut`. `set_usage_level` enregistre et émet `usage_level_changed(str)` seulement si le niveau change.

## Bascule (lot 2)

Trois emplacements pour le même réglage, appliqués à chaud (aucune confirmation, aucun redémarrage), qui lisent tous `MainWindow.usage_level` et suivent `usage_level_changed` :

- **Préférences** : groupe « Niveau d'usage » (trois boutons, une infobulle courte par niveau), déclaré dans `_CHOICE_GROUPS` (`ui/preferences_dialog.py`). Comme les autres groupes, le choix est appliqué tout de suite ; « Restaurer » ne le touche pas.
- **Menu Fenêtre** : sous-menu « Niveau d'usage », trois actions cochables d'un `QActionGroup` exclusif, mnémoniques uniques, aucun raccourci par défaut.
- **Barre du haut** : puce « Simple / Studio / Avancé » avant « Réglages » ; son menu réutilise les trois mêmes actions, son nom accessible est « Niveau d'usage : <niveau> ».

Le code est dans `ui/main_window_mixins/usage_level.py` ; les libellés dans `ui/i18n_usage.py`. Mettre une action à jour par programme n'émet pas `triggered` : pas de boucle ; `set_usage_level` n'émet que si le niveau change. **Ce lot ne masque rien** : changer de niveau n'a encore aucun effet sur l'inspecteur ni sur les menus.

## Inspecteur (lot 3)

- `PropertiesPanel.usage_level` (Avancé sans fenêtre) ; la fenêtre principale l'impose à la création et le suit par `usage_level_changed` (`set_usage_level`, aucun widget recréé).
- **Groupes** : Transition, Étalonnage, Vitesse, Effets, Effets audio sont Studio, Suivi est Avancé, le reste Simple ; le niveau est la 3e condition de `_apply_group_visibility`. Le lien « Afficher plus d'options » révèle les autres d'un geste (remis à zéro à chaque changement de niveau).
- **Réglages** : chaque réglage d'un niveau supérieur est dans un `LevelGate` (`ui/properties_widgets/level_gate.py`) créé avec le contrôle dans son constructeur ; un niveau n'écrit que la visibilité du conteneur (jamais `setEnabled`, jamais une valeur).
- **Pliants « Avancé »** (`AdvancedFold`, un par palier : Studio ou Avancé) : chaque builder le crée à la place voulue, devant ce qu'il révèle. Visible seulement sous son palier ; ouvert, il montre les réglages de ce palier et au-delà. Au niveau Avancé, aucun pliant n'est visible.
- **Choix de listes** (modes de fusion Addition et Différence, formes polygone et courbe, opérations soustraire et intersecter, effets audio hors normaliser, basses, aigus) : cachés de la liste sans être retirés ; le choix courant reste affiché.
- Interdit : poser un conteneur après coup à la place d'un widget déjà dans un layout (`replaceWidget`, `setParent`, `addItem`) : l'élément de layout retiré reste référencé par le widget (hauteur périmée) et le supprimer plante Qt.

### Ce qui est au-dessus et au-dessous du pliant, groupe par groupe

| Groupe | Au-dessus du pliant (toujours montré au niveau du groupe) | Sous le pliant |
|---|---|---|
| Mouvement (Simple) | valeurs et curseurs, « Remplir le cadre », « Réinitialiser » | pliant Studio : bloc « Images-clés » (losanges ‹◆›, déplacés hors des rangées) et alignement ; pliant Avancé : transformation avancée |
| Audio du clip (Simple) | gain, fondus, « Réinitialiser les fondus » | pliant Studio : panoramique (déplacé après les fondus) |
| Compositing (Simple) | recadrage | pliant Studio : fusion, masques, clé chroma ; pliant Avancé (en tête des masques) : formes polygone/courbe, opérations, expansion, rotation, édition dans le moniteur, modes Addition et Différence |
| Sous-titre (Simple) | contenu, police, taille, opacité, couleur du texte, alignement, « Réinitialiser » | pliant Studio : contour, ombre, fond, position, marges, remplissage |
| Calque graphique (Simple) | type, texte, taille, couleur, section Texte | pliant Studio : contour, animation du texte, ombre et fond ; pliant Avancé : forme, néon, lumière, calque |
| Étalonnage (Studio) | réglages, « Réinitialiser » (déplacé avant les courbes) | pliant Avancé : courbes, préréglages, LUT 3D |
| Vitesse (Studio) | vitesse, préréglages, Reverse, Figer, durées | pliant Avancé : section « Temps » (déplacée en fin de groupe) |
| Effets (Studio) | ajout, liste, Désactiver, Supprimer | pliant Avancé, dans la même rangée : Monter, Descendre (les réglages de l'effet sont sous la rangée) |
| Effets audio (Studio) | ajout, liste, boutons, réglages | pliant Avancé sous la ligne d'ajout : les types d'effets avancés dans la liste |

- **Ordre au niveau Avancé** : identique à celui d'avant (liste ordonnée des widgets visibles, `tests/data/inspector_order_before_usage_levels.json`), hors ces déplacements voulus : losanges ‹◆› (bloc en fin de Mouvement), « Réinitialiser » avant l'alignement et la transformation avancée (Mouvement), panoramique après les fondus, section « Temps » en fin de Vitesse, « Réinitialiser » avant les courbes (Étalonnage), recadrage avant la fusion (Compositing), blocs du Sous-titre et du Calque graphique réordonnés (Simple, puis Studio, puis Avancé).
- **Écarts connus** : dans Vitesse, Étalonnage et Effets, quelques réglages Studio restent sous le pliant Avancé (groupes Studio, absents du niveau Simple) ; dans Compositing, le pliant Avancé est en tête des masques, les champs Studio des masques sont dessous. « Remplir le cadre » est une case relais de celle de la transformation avancée ; sous Avancé, le menu des losanges n'offre que l'activation de l'animation.

## Décisions

1. **Trois niveaux** : Simple, Studio (intermédiaire), Avancé. Réglage global, **préférence utilisateur** (`user_settings.json`), **jamais dans le `.kut`**. Champ `usage_level` (pas « level » : collision avec les « niveaux » des scopes et l'imbrication des séquences). Libellé dans l'interface : « Niveau d'usage ».
2. **Mécanisme hybride** : les *groupes* entiers de niveau supérieur sont cachés ; un lien « Afficher plus d'options » en bas de l'inspecteur permet d'y accéder en un geste ; les réglages avancés d'un groupe visible sont repliés dans un sous-bloc « Avancé » du groupe. **Aucune fonction n'est inaccessible** ; elle est seulement repliée ou cachée derrière un geste.
3. **Niveau par défaut** : une installation neuve démarre en **Simple** ; un utilisateur qui a déjà un `user_settings.json` sans `usage_level` est mis en **Avancé** (rien ne change pour lui).
4. **« Restaurer les valeurs par défaut » ne réinitialise pas le niveau** (choix personnel d'interface).
5. Groupes conditionnels (Transition, Sous-titre, Calque graphique) : restent cachés hors contexte, comme aujourd'hui.
6. Les **raccourcis clavier restent toujours actifs**, quel que soit le niveau (jamais de `setVisible(False)` sur une action : retirer l'entrée du menu et garder l'action sur la fenêtre).
7. Un contrôle caché **garde sa valeur** (le modèle seul porte les valeurs) ; les **verrous et genres de piste** (`show_clip`, `clip_edit_refusal`) s'appliquent à tous les niveaux ; un niveau n'appelle jamais `setEnabled`.

## Répartition (niveau minimal où l'élément est affiché directement)

### Inspecteur

| Groupe ou contrôle | Niveau | Remarque |
|---|---|---|
| Clip sélectionné ; Paramètres du projet (replié) | Simple | |
| Mouvement : position X/Y, échelle, rotation, opacité, réinitialiser | Simple | |
| Mouvement : losanges d'images-clés (‹ ◆ ›) | Studio | |
| Mouvement : alignement dans le cadre (6 boutons) | Studio | |
| « Remplir le cadre » (propriété `fill`) | Simple | action dédiée, hors du reste de la transformation avancée |
| Transformation avancée (ancrage, échelle X/Y, miroirs, pan, inclinaison) | Avancé | |
| Menu clic droit des losanges (interpolation, copier/coller, courbes) | Avancé | |
| Recadrage | Simple | impose de scinder `CompositingEditor` en sous-blocs |
| Gain du clip, fondus ; volume du moniteur | Simple | |
| Panoramique du clip | Studio | |
| Vitesse : valeur, préréglages, Reverse, Figer | Studio | le sous-menu Vitesse du clic droit suit |
| Vitesse : courbe, interpolation, qualité, flux optique, audio de la courbe | Avancé | `TimeSection` complète |
| Étalonnage : exposition, contraste, saturation, température, teinte, ombres, hautes lumières | Studio | |
| Étalonnage : courbes, préréglages, LUT 3D | Avancé | |
| Effets du clip : ajouter, liste, activer, retirer ; flou, netteté, vignette, noir et blanc, sépia | Studio | |
| Effets du clip : lueur, aberration chromatique, brume de chaleur | Studio | |
| Effets du clip : monter / descendre (ordre) | Avancé | |
| Transition (type, durée) | Studio | visible seulement si une transition est sélectionnée |
| Sous-titre : contenu, police, taille, couleurs, alignement | Simple | |
| Sous-titre : position/marges/remplissage, contour, ombre, fond | Studio | |
| Calque graphique : type, texte, taille, couleur, police | Simple | |
| Calque graphique : contour, ombre et fond, animation de texte | Studio | |
| Calque graphique : lueur néon, lumière, forme, parent, flou de mouvement | Avancé | |
| Compositing : mode de fusion Normal, Multiplier, Écran, Incrustation, Assombrir, Éclaircir | Studio | |
| Compositing : mode de fusion Addition, Différence | Avancé | le mode déjà choisi dans un projet reste affiché |
| Compositing : masques rectangle et ellipse (ajouter/supprimer, inverser, adoucissement, opacité) | Studio | |
| Compositing : masques polygone et courbe, opération soustraire/intersecter, expansion, rotation, édition dans le moniteur | Avancé | |
| Compositing : clé chroma | Studio | |
| Suivi (tracking) | Avancé | |
| Effets audio : normaliser, basses, aigus | Studio | |
| Effets audio : voix, réduction de bruit, compresseur, limiteur, téléphone, réverb, écho, EQ paramétrique | Avancé | |

### Menus, pages, surfaces

| Élément | Niveau |
|---|---|
| Fichier (tout), Aide (tout) | Simple |
| Édition : annuler, rétablir, dupliquer, supprimer, activer/désactiver | Simple |
| Édition : suppression en cascade | Studio |
| Séquence : couper à la tête de lecture, marqueur, ajouter/supprimer/renommer une piste, verrouillage, visibilité, muet | Simple |
| Séquence : détection de scènes, proxies pour la sélection | Studio |
| Séquence : nouvelle/renommer/dupliquer/supprimer une séquence, imbriquer, ouvrir/parent/retour/avance, convertir en composition, nouvelle composition, multicam | Avancé |
| Calques : Ajouter ▸ texte ; réinitialiser le recadrage | Simple |
| Calques : Ajouter ▸ forme, nul, ajustement ; grouper/dégrouper ; copier/coller (calques, attributs) ; préréglages ; zones de sécurité, repères, grille, aimantation, centre ; Aligner | Studio |
| Calques : flou de mouvement et réglages ; panneau de suivi | Avancé |
| Réseaux sociaux : menu (nouveau projet social, réglages, zones de plateforme, remplir le cadre, sous-titres automatiques, repère de couverture) | Simple |
| Réseaux sociaux : grille de rythme, Impact, audio social, versions de format, classement, Photos | Studio |
| Fenêtre : pages Montage et Audio, panneaux, espaces de travail, préférences, réinitialiser la disposition | Simple |
| Fenêtre : page Couleur ; scopes | Studio |
| Fenêtre : page Composition ; éditeur de courbes ; diagnostics | Avancé |
| Menu contextuel clip : couper, dupliquer, activer, supprimer | Simple |
| Menu contextuel clip : sous-menu Vitesse (hors courbe) | Studio |
| Menu contextuel clip : imbriquer, convertir, multicam ; courbe de vitesse | Avancé |
| Menu contextuel piste : hauteur, replier, monter/descendre, renommer, supprimer | Simple |
| Menu contextuel piste : courbe de volume, rôle, ducking | Avancé |
| Menu contextuel bibliothèque : renommer, dossiers, supprimer | Simple |
| Menu contextuel bibliothèque : étiquettes, matrice couleur, proxy, relier | Studio |
| Timeline : sélection, lame, aimantation, ripple, marqueur | Simple |
| Timeline : roll, slip, slide, enregistrement | Studio |
| Rail de la bibliothèque : média, montage, texte, audio, graphiques | Simple |
| Rail de la bibliothèque : effets, transitions, couleur, séquences, modèles | Studio |
| **Raccourcis clavier** (`ACTION` et `KEY`) | **toujours actifs** |
| Éditeur de raccourcis, Préférences | toujours |

## Menus et surfaces (lot 4)

- **Menus de la barre** : `ui/main_window_mixins/menu_levels.py` (`MENU_GATES`, table du niveau minimal de chaque entrée, repérée par la clé de traduction de son libellé ; un sous-menu par sa clé suivie de `>`). Une entrée au-dessus du niveau est **retirée du menu** (`QMenu.removeAction`) et **remise à sa place d'origine** (`insertAction`) quand le niveau monte : rien n'est recréé, désactivé ni reparenté. Les séparateurs ne sont montrés qu'entre deux entrées visibles ; au niveau Avancé la liste est exactement celle d'avant (`tests/data/menus_before_usage_levels.json`, produit par `tests/menu_order.py` sur `origin/main`).
- **Raccourcis toujours actifs** : un `QAction` caché par `setVisible(False)`, ou retiré de tous les menus, perd son raccourci. Toutes les actions de commande (`ShortcutManager.command_actions`) sont donc attachées à la fenêtre (`addAction`) à l'installation ; `tests/test_usage_level_menus.py` presse les raccourcis des entrées absentes, à chaque niveau, avec de vraies frappes. Un niveau n'appelle jamais `setEnabled` ; les verrous de piste et de calque ne changent pas.
- **Rail de la bibliothèque** (`SideRail.set_usage_level`) : médias, montage, texte, audio et graphiques en Simple ; effets, transitions, couleur, séquences et modèles dès Studio. Seule la visibilité du bouton change.
- **Barre de la timeline** (`TimelinePanel.set_usage_level`) : sélection, lame, aimantation, ripple et marqueur en Simple ; roll, slip, slide et enregistrement dès Studio. Leurs raccourcis (R, Y, U, enregistrement) agissent par le bouton, caché ou non ; un outil caché ne reste pas actif (retour à la sélection) et un enregistrement en cours garde son bouton visible.
- **Menus contextuels** (construits à chaque ouverture, ils lisent le niveau du panneau) : clip : couper, dupliquer, activer, supprimer en Simple ; « Supprimer et refermer » et « Vitesse ▸ » (vitesses, sens inverse, garder la durée, copier, coller, réinitialiser) en Studio ; imbriquer, convertir, ouvrir, Multicam, images intermédiaires, qualité et analyse du flux, courbe de vitesse et son de la courbe en Avancé. Piste (⋯) : courbe de volume, rôle et « Baisser sous… » en Avancé. Média : renommer, sélectionner dans la timeline, dossiers, supprimer en Simple ; étiquettes, relier, proxy, matrice couleur en Studio ; « Créer une séquence Multicam… » (sélection multiple) en Avancé.
- **Entrées non listées dans la spécification, classées ainsi** : Réseaux sociaux « Recadrer en suivant le tracker » et « Ken Burns sur la sélection » (Studio) ; Fenêtre « Utiliser les proxies » (Studio) ; Édition « Couper / Copier / Coller » et Séquence « Ajouter un clip » (désactivées à dessein, restent en Simple) ; « Supprimer et refermer » du clic droit (Studio, comme l'entrée du menu Édition).
- **Boutons de pages** (barre du haut, `page_buttons`) : Montage et Audio en Simple, Couleur dès Studio, Composition en Avancé (`PAGE_LEVELS`) ; seule la visibilité du bouton change, les commandes de pages et leurs raccourcis restent actifs.
- **Descente de niveau** : si la page active ou la section active du rail de la bibliothèque disparaît au nouveau niveau, l'interface retombe d'abord sur Montage (`switch_page`) et sur « Médias », puis applique le niveau ; le projet, la sélection et la position de lecture sont conservés.
- **Hors périmètre** : les boutons cachés `top_nav_buttons` (jamais affichés) ne sont pas touchés.

## Garantie (lot 5)

**Critère de l'ADR-0006 (étape 4)** : « chaque contrôle appartient à un niveau, vérifié par un test qui énumère les contrôles de l'inspecteur ; aucune fonction n'est inaccessible en mode simple (elle est seulement repliée) ».

### L'énumération

`tests/test_usage_level_inventory.py` (outil : `tests/usage_level_inventory.py`) part de la **vraie fenêtre** hors écran, jamais d'une copie des tables :

- **Niveau déclaré** par le code pour chaque contrôle : `MENU_GATES` (par les listes d'origine des menus de la fenêtre), `PAGE_LEVELS`, `SECTION_LEVELS`, les outils de la barre de la timeline (`STUDIO_TOOLS` et l'enregistrement), pour l'inspecteur `_group_levels`, les `LevelGate` qui entourent les contrôles et les choix de listes (`_level_limits`).
- **Niveau observé** : ce que l'écran montre à chaque niveau (menus et sous-menus, boutons, menus contextuels clip, segment multicam, pistes et média, inspecteur sur les états de `inspector_order.clip_states()` et tous les onglets), sans geste.
- **Manifeste** `tests/data/usage_level_controls.json` : la clé et le niveau de **chaque** contrôle, y compris ceux du niveau Simple (le code ne les nomme pas : « ce qui n'est pas dans une table est Simple »). Un contrôle neuf, absent du manifeste, fait échouer le test.

Le test vérifie : (a) le niveau déclaré par le code est celui du manifeste, et le manifeste n'a ni contrôle de trop ni contrôle manquant ; (b) aucun orphelin : tout ce qui est affiché à un niveau est classé, au plus ce niveau, et tout contrôle classé à un niveau y est affiché ; (c) monotonie : Simple est contenu dans Studio, contenu dans Avancé, strictement, aux seuls contrôles de navigation près (pliants « Avancé », lien « Afficher plus d'options », doublure d'« Remplir le cadre ») qui changent de côté à dessein ; (d) au niveau Avancé, les menus, boutons et menus contextuels sont ceux du golden d'`origin/main` (`tests/data/menus_before_usage_levels.json`) et l'inspecteur celui de `tests/data/inspector_order_before_usage_levels.json` ; (e) « aucune fonction inaccessible » : avec « Afficher plus d'options » et tous les pliants ouverts, tout ce que le niveau Avancé montre dans l'inspecteur est atteignable depuis Simple et Studio.

### Classer un nouveau contrôle

1. Donne-lui un niveau **dans le code**, au même endroit que ses voisins : une entrée de menu dans `MENU_GATES` (`ui/main_window_mixins/menu_levels.py`), une page dans `PAGE_LEVELS`, une section du rail dans `SECTION_LEVELS`, un outil de la timeline dans `STUDIO_TOOLS`, un réglage de l'inspecteur dans un `LevelGate` (`gated(widget, niveau)`) créé avec le contrôle, un groupe dans `_group_levels`, un choix de liste dans `_limit_plan`. Un contrôle Simple n'a pas d'entrée de table.
2. Lance le test : il échoue en nommant le contrôle (« contrôle sans classement »).
3. Régénère le manifeste puis relis la différence (`git diff tests/data/usage_level_controls.json`) : seule la ligne du contrôle neuf doit apparaître, avec le bon niveau.
   `KUT_REGEN_USAGE_MANIFEST=1 QT_QPA_PLATFORM=offscreen KUT_STUDIO_CONFIG_DIR=<dossier jetable> python -m pytest tests/test_usage_level_inventory.py -k regenerate`
4. Les fichiers `tests/data/*_before_usage_levels.json` ne se régénèrent **jamais** sur une branche : seulement sur un worktree d'`origin/main`.

Limite connue : le manifeste est produit à partir du code, il ne prouve donc pas que le niveau choisi est le bon, seulement que chaque contrôle a été classé et que le classement n'a pas bougé sans relecture. Les contrôles de l'inspecteur que les états du relevé ne montrent jamais (réglages de masque ou de clé chroma hors contexte) ne sont vérifiés que par leur niveau déclaré.

### L'audit de mise en page à chaque niveau

`python -m tools.ui_audit --levels all` rejoue les scénarios à Simple, Studio et Avancé (`--levels simple,studio` pour une liste ; `run_audit(levels=...)` et `make_main_window(usage_level=...)` pour le code). Le défaut reste le niveau Avancé, le chemin d'avant les niveaux. Aux niveaux Simple et Studio, des scénarios `inspecteur-tout-ouvert-<onglet>` déploient « Afficher plus d'options » et tous les pliants, la mise en page la plus chargée du niveau. Les captures d'un autre niveau portent son nom (`1180x720-simple-defaut.png`). `tests/test_ui_small_windows.py` joue les trois niveaux (fixture `window_at(taille, niveau)` : une fenêtre par taille, niveau changé à chaud) : inspecteur, suivi (ouvert par « Afficher plus d'options » aux niveaux où il est caché), bibliothèque, panneaux, moniteur, barres (menus, pages, rail, outils de la timeline) et, en sous-processus, 1180 × 720 avec polices +20 %.

## Pièges à respecter (vérifiés dans le code)

- P1 : un `QAction` avec `setVisible(False)` perd son raccourci ; garder l'action sur la fenêtre (`window.addAction`) et retirer l'entrée du menu.
- P2 : `ui/properties_panel_mixins/construction.py` fait 697 lignes (plafond 700 des paquets gardés par `tests/test_ui_modularization.py`) : le code des niveaux va dans un NOUVEAU module.
- P3 : plusieurs écrivains de visibilité dans l'inspecteur (`_apply_group_visibility` dans `tabs.py`, `set_graphic`, `set_skew_available`, `TimeSection._show_row`, `update_effects_from_clip`, `set_chroma_visible`, `_refresh_mask_widgets`) : un niveau ne s'ajoute qu'au point unique `_apply_group_visibility` (3e condition) et à des conteneurs repliables, jamais à des `setVisible` isolés.
- P5 : `CropEditor` est enfant de `CompositingEditor` : scinder en sous-blocs (recadrage, fusion, masques, clé chroma).
- P8/P9 : `_settings_snapshot` (`ui/main_window_mixins/preferences.py`) reconstruit `UserSettings` champ par champ : y AJOUTER `usage_level`, sinon il est remis au défaut à chaque sauvegarde d'un autre réglage ; `_restore_default_preferences` doit le PRÉSERVER.
- P11/P12 : i18n fr/en/es, textes courts, noms accessibles (`tests/test_ui_honesty.py`), 1180×720 à +20 % de police.
- P13 : `tools/ui_audit.py` ne joue qu'un scénario par onglet : dimension « niveau » ajoutée au lot 5 (×3 de temps d'audit, voir « Garantie »).
- P14 : retraduction à chaud (`_translated_actions`, `_translated_menus`) pour les entrées de menu qui disparaissent/réapparaissent.
