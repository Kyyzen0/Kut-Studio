# Niveaux d'usage (ADR-0006, étape 4)

**Statut :** décidé le 2026-10-15 (l'implémentation avance par lots). L'utilisateur a validé le mécanisme, le niveau par défaut, le comportement de « Restaurer » et la répartition ci-dessous.

## Avancement par lots

| Lot | Contenu | État |
|---|---|---|
| 1 | Modèle (`core/usage_level.py`), préférence `usage_level`, signal `usage_level_changed`, aucun effet visible | **fait** (cette PR) |
| 2 | Bascule du niveau : groupe dans les Préférences, sous-menu « Niveau d'usage » du menu Fenêtre, puce dans la barre du haut ; libellés fr/en/es | **fait** |
| 3 | Inspecteur par niveau : point unique `_apply_group_visibility` (3e condition), conteneurs de niveau créés à la construction, sous-blocs « Avancé », lien « Afficher plus d'options » | **fait** |
| 4 | Menus, menus contextuels, rail de la bibliothèque, barre de la timeline, pages (retrait des entrées, raccourcis conservés) | **fait** |
| 5 | Test d'énumération des contrôles (critère de l'ADR) et dimension « niveau » dans `tools/ui_audit.py` | à faire |
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

## Pièges à respecter (vérifiés dans le code)

- P1 : un `QAction` avec `setVisible(False)` perd son raccourci ; garder l'action sur la fenêtre (`window.addAction`) et retirer l'entrée du menu.
- P2 : `ui/properties_panel_mixins/construction.py` fait 697 lignes (plafond 700 des paquets gardés par `tests/test_ui_modularization.py`) : le code des niveaux va dans un NOUVEAU module.
- P3 : plusieurs écrivains de visibilité dans l'inspecteur (`_apply_group_visibility` dans `tabs.py`, `set_graphic`, `set_skew_available`, `TimeSection._show_row`, `update_effects_from_clip`, `set_chroma_visible`, `_refresh_mask_widgets`) : un niveau ne s'ajoute qu'au point unique `_apply_group_visibility` (3e condition) et à des conteneurs repliables, jamais à des `setVisible` isolés.
- P5 : `CropEditor` est enfant de `CompositingEditor` : scinder en sous-blocs (recadrage, fusion, masques, clé chroma).
- P8/P9 : `_settings_snapshot` (`ui/main_window_mixins/preferences.py`) reconstruit `UserSettings` champ par champ : y AJOUTER `usage_level`, sinon il est remis au défaut à chaque sauvegarde d'un autre réglage ; `_restore_default_preferences` doit le PRÉSERVER.
- P11/P12 : i18n fr/en/es, textes courts, noms accessibles (`tests/test_ui_honesty.py`), 1180×720 à +20 % de police.
- P13 : `tools/ui_audit.py` ne joue qu'un scénario par onglet : ajouter une dimension « niveau » (×3 de temps d'audit).
- P14 : retraduction à chaud (`_translated_actions`, `_translated_menus`) pour les entrées de menu qui disparaissent/réapparaissent.
