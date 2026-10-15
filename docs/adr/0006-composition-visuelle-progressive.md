# ADR-0006 : la composition visuelle — compléter ce qui existe, par étapes, sans réécrire le moteur

**Statut :** Accepté le 2026-10-13 (questions ouvertes tranchées, voir « Décisions prises »). Étapes 1 à 3b livrées dans la 0.6.0 avec deux critères reportés (arrondi du recadrage, verbes de script pour les courbes) ; voir « État d'avancement ».
**Date :** 2026-10-13
**Décideurs :** mainteneur de Kut-Studio

Demande de l'utilisateur : un système de composition visuelle **non destructif**, proche de Premiere Pro, After Effects et DaVinci
Resolve mais plus accessible : superposer librement images, vidéos, textes, logos et formes ; composer directement dans le
moniteur (sélection, poignées, guides, accrochage, groupes) ; masques et modes de fusion ; images clés et préréglages ;
architecture commune, GPU quand c'est pertinent, cache et qualité adaptable ; **deux niveaux d'usage** (simple / avancé).
Consigne : ne pas tout développer d'un coup ; auditer, puis proposer une implémentation progressive avec tests et critères.

## Résumé

1. **Le gros du système existe déjà.** Superposition, transformations, 8 modes de fusion, masques animés, groupes et parentage de
   calques graphiques, calque d'ajustement, composition par nœuds, images clés (31 cibles) et courbes, moniteur GPU : tout est
   présent, avec des tests de parité aperçu / export.
2. **Les manques sont ciblés** : le moniteur ne sait pas sélectionner un clip vidéo ou une image, il n'y a ni recadrage, ni masque
   Bézier, ni poignées de masque, ni animation des couleurs et des effets, ni niveau simple / avancé, et l'empilement vidéo / graphique
   est rigide (les calques graphiques sont toujours au-dessus de toute la vidéo).
3. **Décision proposée : ne pas créer de nouveau moteur de composition.** Le « système commun » demandé est déjà le triplet
   *RenderPlan + cibles d'animation + mattes rastérisées par Qt*. On le complète, étape par étape, en commençant par ce qui coûte
   peu et se voit tout de suite (le moniteur), et on traite les refontes (plan unique, GPU à l'export) dans leurs propres ADR.

## État des lieux (audit du 2026-10-13, lecture du code ; rien n'a été exécuté à l'écran)

Légende : **P** présent, **~** partiel, **A** absent. Chemins : voir la ligne « où ».

### 1. Superposition et calques

| Demande | État | Où / limite |
| --- | --- | --- |
| Images, vidéos, textes, logos, formes superposés | **P** | `core/render_plan.py` ; `core/graphics.py` (`text, rectangle, solid, image, shape, group, adjustment, null, light`) |
| Ordre d'affichage dans la timeline | **~** | Vidéo : ordre des pistes seulement, pas de champ z (`core/render_plan.py:287`). Graphiques : `z_order` (`core/graphics.py:161`) **mais composés après toute la vidéo** (`core/export_engine.py:1707`) : une piste graphique ne peut pas passer sous une vidéo |
| Position, échelle, rotation, ancrage, miroirs | **P** | `ClipTransform`, `core/visual_effects.py:163-194` |
| Inclinaison (skew) | **~** | Champ présent, appliqué aux calques graphiques seulement (`core/mograph_scene.py:477`) |
| **Recadrage** | **A** | Aucun champ `crop`. Substituts : `fill` + `pan`, masque rectangle |
| Opacité | **P** | `ClipTransform.opacity` ; héritée d'un contrôleur *null* seulement |
| Visibilité, verrou | **~** | Calque graphique : `visible`, `locked`. Clip vidéo : `enabled` ; verrou **de piste** seulement |
| Flou de mouvement | **~** | Calques graphiques seulement |
| Groupes, parentage | **~** | Calques graphiques seulement ; un clip vidéo peut être parent, jamais enfant (`core/mograph_layers.py:60-64`) |

### 2. Moniteur

| Demande | État | Où / limite |
| --- | --- | --- |
| Cliquer un calque graphique | **P** | `ui/viewer_overlay.py:533`, `ui/main_window_mixins/motion_graphics.py:413` |
| **Cliquer un clip vidéo / image / composition** | **A** | Exclus par le filtre `GraphicOverlay` (`motion_graphics.py:417`) ; sélection par la timeline seulement |
| Déplacer, redimensionner (coins, côtés), pivoter, ancrage | **P** | `ui/viewer_overlay.py:388-405, 669-743` |
| Guides H/V, verrouillables ; centre ; tiers ; zones de sécurité et de plateformes | **P** | `core/canvas_guides.py` |
| Accrochage | **~** | Au déplacement seulement ; cibles = cadre, centre, guides, **calques graphiques** (pas les vidéos) ; ni à l'échelle ni à la rotation |
| Alignement sur le cadre (boutons), déplacement au clavier, règles, grille en pixels | **A** | — |
| Multi-sélection et transformation de plusieurs calques | **A** | Sélection mono-calque (`viewer_overlay.py:112`) |
| Poignées de masque | **A** | Les masques ne se règlent que dans l'inspecteur ; seules les fenêtres d'étalonnage ont des poignées |

### 3. Masques et fusion

| Demande | État | Où / limite |
| --- | --- | --- |
| Rectangle, ellipse, polygone ; ajouter / soustraire / intersecter ; inversion ; contour (*feather*) ; expansion ; opacité | **P** | `core/compositing.py:36-146`, rastérisés par Qt (`core/mograph_raster.py:259-344`), même matte pour GPU et FFmpeg |
| **Courbes de Bézier** | **A** | `MaskShape` = `rectangle / ellipse / polygon` (segments droits) |
| Animation des masques | **~** | 8 propriétés par masque (position, taille, rotation, *feather*, expansion, opacité) ; **sommets statiques** |
| Modes de fusion | **P** | 8 : `normal, multiply, screen, overlay, darken, lighten, addition, difference` (`core/blend_modes.py`). Export, aperçu fidèle, moniteur GPU. Moniteur CPU : pas pour la vidéo |
| Modes classiques manquants | **A** | *color dodge / burn, soft light, hard light, exclusion, hue / saturation / color / luminosity* |
| Limiter un effet à un calque | **~** | Effets par clip ; calque d'ajustement (non groupable) ; zone via fenêtres / qualifieur d'étalonnage. Pas d'effet par piste ni par séquence |

### 4. Animation

| Demande | État | Où / limite |
| --- | --- | --- |
| Position, échelle, rotation, opacité (+ ancrage, échelle X/Y, inclinaison, miroirs, *pan*) | **P** | 14 cibles transform (`core/animation_targets.py:308`) |
| Paramètres de calques graphiques (taille, ombre, police, révélation, lumière, lueur) ; masques ; vitesse | **P** | 16 + 8 par masque + 1 |
| Images clés dans la timeline ; éditeur de courbes (Ctrl+Alt+G) ; copier / coller | **P** | `ui/timeline_widgets/clip_widget.py`, `ui/graph_editor.py` |
| Interpolations | **P** | hold, linéaire, ease in / out / in-out, Bézier ; tangentes liées / brisées / auto |
| Préréglages d'animation | **~** | Texte, impact, Ken Burns, vitesse, transitions ; **pas de préréglages de courbe nommés** |
| **Couleurs** (étalonnage, couleurs de calque), **paramètres d'effets**, **chroma key**, **sommets de masque** | **A** | `core/mograph_targets.py:49` (couleurs déclarées statiques), `ClipEffect.params` sans cible |
| Animation par script | **~** | `add_keyframe` : 10 propriétés **codées en dur** (`core/scripting/verbs.py:434`) |

### 5. Architecture, GPU, cache

| Sujet | État | Remarque |
| --- | --- | --- |
| Plan de rendu commun | **~** | `RenderPlan` sert l'export, l'aperçu fidèle, les scopes, les calques graphiques du moniteur. **Le moniteur GPU vidéo l'ignore** et assemble ses couches lui-même (`ui/main_window_mixins/hardware_preview.py:211`) ; la matrice vidéo existe en deux versions (GPU : `core/tracking_motion.py:183` ; export : filtres FFmpeg). Égalité garantie par des tests, pas par construction |
| Évaluation animée | **~** | Trois évaluateurs (transform vidéo, scène motion graphics, masques) + l'automation audio, système séparé |
| Export | **P** | 100 % FFmpeg / CPU ; c'est la référence. Pas d'export GPU (ADR-0001, étape 4) |
| Moniteur GPU (QRhi) | **P** | Effets, fusions, mattes, étalonnage. Exclus : transitions, sous-titres, séquences imbriquées, chroma key, plusieurs pistes sur le moniteur CPU |
| Caches | **P** | Segments fidèles (disque), compositions pré-rendues, textures GPU (mattes fixes), LUT, flux optique, proxys, images de calques |
| Qualité adaptative | **P** | `core/preview_quality.py`, `core/preview_adaptive.py`, `core/runtime_regulator.py` |
| Écarts à l'ADR-0001 | — | Compositeur CPU sans appelant de production ; niveaux « GPU complet / limité » non implémentés ; `OnDemandGate.should_run` jamais interrogé ; ADR toujours « Proposé » |
| Composition nodale | **~** | Sources, transform, masque, incrustation, effets, étalonnage, fusion, sortie. **Pas** de groupes, parentage, imbrication, remappage, tracking |

### 6. Deux niveaux d'usage

Aucun mode simple / avancé global (« Studio / Advanced » n'est qu'une idée non décidée). Il existe de la **divulgation progressive**
locale : sections repliables de l'éditeur graphique, « Transformation avancée » repliée, pages Montage / Couleur / Composition / Audio,
panneaux masquables et espaces de travail nommés. C'est la base à réutiliser.

### 7. Incohérences doc / code relevées (à corriger dans l'étape 0)

`docs/motion-graphics.md` : GPU « 3 modes » (le code en compose 8), format « version 16 » (17), « pas de système nodal » (il existe) ;
`docs/animation.md` : « cinq propriétés » (14 transform), `RENDER_ENGINE_VERSION` 9 (10) ; `docs/ui-small-windows-and-keyboard.md` :
« préférences simples / complètes » (pas un mode) ; `core/blend_modes.py:13` cite un test qui n'existe pas ; ADR-0001 : rendu à la demande
présenté comme livré.

## Décision proposée

**Principes.**
- *Pas de second moteur.* Toute fonctionnalité s'accroche au `RenderPlan`, au registre de cibles d'animation et au matte Qt partagé.
- *Un matte, deux consommateurs.* Les masques sont rastérisés par Qt puis consommés par FFmpeg **et** par le GPU : toute nouvelle forme
  de masque (Bézier) ne coûte donc **rien** côté rendu, seulement côté modèle, rastérisation et interface.
- *FFmpeg reste la vérité* (ADR-0001) : chaque étape se valide par un test de parité aperçu / export.
- *Chaque étape est une PR autonome*, avec son critère de validation, ses tests, sa doc, son exposition au mode script (ADR-0005) et
  son garde-fou de format (`CURRENT_VERSION`, `RENDER_ENGINE_VERSION`, `RASTER_VERSION`) si le rendu ou le fichier changent.
- *Non destructif* : aucune étape ne modifie un média ; tout est données du projet + rendu dérivé.

## Plan d'action

### Étape 0 — Remettre la doc d'aplomb (petit, sans risque)
Corriger les écarts du §7. **Critère :** les chiffres cités (modes, version, cibles) sont vérifiés par un test de doc existant ou nouveau.

### Étape 1 — Sélectionner et manipuler *n'importe quel* clip dans le moniteur
Hit-test des clips vidéo, images, compositions (boîte = cadre transformé) en plus des calques graphiques ; clic = sélection de la
timeline et du moniteur ; cibles d'accrochage étendues aux clips vidéo ; accrochage à l'échelle et à la rotation ; flèches du clavier
(1 px, Maj = 10 px) ; boutons d'alignement sur le cadre. Les poignées de déplacement / échelle / rotation existent : on les réutilise.
**Critères :** les maths de hit-test et d'accrochage sont des fonctions pures testées (comme `tests/test_mograph_ui.py`) ; un test
d'intégration clique sur un clip vidéo et vérifie la sélection ; aucune régression des gestes existants ; capture hors écran
(l'aperçu vidéo est noir en hors-écran, la boîte et les poignées se vérifient quand même).
*Pourquoi d'abord :* c'est la plus grosse différence de ressenti, et le moins risqué (UI + maths, pas de moteur).

### Étape 2 — Recadrage non destructif
Proposition : **un recadrage est un masque rectangle** généré par une commande « Recadrer » (haut / bas / gauche / droite, option
arrondi), avec poignées dans le moniteur. Aucun nouveau champ de rendu : le matte existe déjà. **Critères :** recadrage = masque `add`
de forme rectangle (livré en mode `intersect`, rangé en dernier de la pile : voir « État d'avancement ») ; export et GPU identiques (test de parité existant sur les mattes) ; annulation en un seul pas ; le recadrage
reste modifiable après coup (c'est un masque).
*Alternative écartée :* un champ `crop` dans `ClipTransform` → deuxième chemin de rendu à maintenir deux fois (GPU et FFmpeg).

### Étape 3 — Masques dans le moniteur, puis Bézier et plume
3a. Poignées de masque (rectangle, ellipse, *feather*) et édition des sommets du polygone dans le moniteur.
3b. Nouvelle forme `path` : points avec poignées de tangente (Bézier), fermeture, `QPainterPath` côté rastérisation. Le « plume » existe
déjà sous le nom *feather* (flou uniforme) ; un plume **variable** par point est reporté.
**Critères :** `MaskShape.PATH` sérialisé (incrément de `CURRENT_VERSION`, chargeur tolérant les anciens fichiers) ; matte identique
GPU / export ; sommets animables reportés à l'étape 5 ; fuzz du chargeur ; verbe de script `add_mask` / `set_mask_points`.

### Étape 4 — Trois niveaux d'usage (simple / intermédiaire / avancé)
Un réglage global à trois niveaux (préférences + bascule dans la barre) qui n'ajoute aucune fonctionnalité mais **masque ou affiche** : inspecteur
(Mouvement, Opacité, Recadrer visibles ; masques, fusion, images clés avancées, composition repliés en « Avancé »), menus et
raccourcis. Réutilise les sections repliables et les espaces de travail. **Critères :** l'état est une préférence utilisateur (jamais dans
le `.kut`) ; chaque contrôle appartient à un niveau, vérifié par un test qui énumère les contrôles de l'inspecteur ; aucune fonction
n'est inaccessible en mode simple (elle est seulement repliée).
*Décidé :* trois niveaux (voir « Décisions prises »).

### Étape 5 — Animer ce qui ne l'est pas encore
Par ordre de valeur / coût : (a) tous les contrôles de l'inspecteur obtiennent leur losange à partir du **registre de cibles** (aujourd'hui
5 propriétés codées en dur : `construction.py:511`) ; (b) la liste des propriétés du script (`verbs.py:434`) est **dérivée du registre** ;
(c) couleurs de calques graphiques et paramètres d'étalonnage (cibles `float` / couleur) ; (d) paramètres d'effets ; (e) sommets de masque ;
(f) préréglages de courbe nommés (*ease out cubic*, *back*, *elastic*…) qui posent des tangentes.
**Critères :** pour chaque cible, test « valeur animée identique en aperçu et à l'export, pixel par pixel »
(`tests/test_animation_integration.py:138`), sérialisation aller-retour, verbe de script avec bornes lues dans le modèle.
*Risque connu :* trois évaluateurs d'animation ; chaque ajout se branche dans le bon et se teste dans les trois chemins.

### Étape 6 — Modes de fusion complets
Ajouter *color dodge, color burn, soft light, hard light, exclusion* (puis *hue / saturation / color / luminosity* si FFmpeg et le shader
les permettent sans écart). **Critères :** table FFmpeg (`core/blend_modes.py`), shader (`tools/gpu/shader_sources.py`, recompiler les `.qsb`),
référence numpy et mode Qt alignés sur les formules W3C ; tests de parité `tests/test_gpu_pipeline.py:414` et
`tests/test_mograph_render.py:440` étendus ; `RENDER_ENGINE_VERSION` incrémenté.

### Étape 7 — Empilement libre vidéo / graphique, groupes et parentage de clips vidéo
Le vrai verrou d'architecture : aujourd'hui les graphiques sont composés *après* toute la vidéo. Passer à un empilement unique « par piste
dans l'ordre de la timeline », où une piste graphique peut se trouver entre deux pistes vidéo ; puis autoriser un clip vidéo comme enfant ou
membre de groupe. **À traiter dans un ADR dédié** (touche `render_plan`, `export_engine`, moniteur GPU, format du fichier). **Critères :**
projets existants rendus à l'identique (test d'or sur des projets de référence), nouveau projet avec graphique sous vidéo identique en
aperçu et à l'export.

### Étape 8 — Architecture : un seul chemin d'évaluation et de composition
Faire passer la couche vidéo du moniteur GPU par le `RenderPlan` ; unifier les trois évaluateurs d'animation ; décider du sort du
compositeur CPU non branché ; effets par piste / séquence ; chroma key sur le GPU ; export GPU (ADR-0001, étape 4) ; *OnDemandGate*.
**À traiter dans ses propres ADR**, après les étapes 1 à 6 qui auront montré où cela fait vraiment mal.

## État d'avancement (2026-10-14)

| Étape | PR | État |
| --- | --- | --- |
| 1a clic sur n'importe quel clip | #143 | mergée |
| 1b accrochage échelle et rotation | #144 | mergée |
| 1c alignement sur le cadre, flèches | #145 | mergée |
| 2 recadrage par masque | #146 | mergée, **sans l'option d'arrondi** (reportée) |
| 3a poignées de masque au moniteur | #147 | mergée |
| (hors plan) garde de verrou du groupe Compositing | #148 | mergée |
| (hors plan) autres trous de verrou de l'inspecteur (dont l'éditeur de courbes) | #149 | mergée |
| 3b masques Bézier (+ format .kut v18 si une courbe) | #150 | mergée ; verbes de script pour les masques livrés ensuite (0.6.2) |
| (hors plan) un clic atteint la surcouche quand des clips se recouvrent | #152 | mergée |

Les étapes 1 à 3b sont publiées dans la **0.6.0** (2026-10-14).

Écarts avec le plan ci-dessus, découverts à l'implémentation :
- **Étape 2 :** le masque de recadrage est en mode `intersect` et rangé **en dernier** (pas `add`) : un masque `add` ne restreint pas les autres masques `add`, il s'y ajoute. Identifiant réservé `crop`.
- **Étape 2 :** l'option « arrondi » du plan n'est **pas livrée** : le recadrage crée toujours un rectangle simple (`core/clip_crop.py`) et ni le modèle ni l'éditeur n'ont de rayon de coin. Reportée ; elle demandera un rayon sur le masque rectangle (ou une forme dédiée) et son rendu GPU / export.
- **Étape 3a :** pas d'accrochage des poignées de masque ; une case « Poignées dans le moniteur » permet de revenir aux poignées du clip tant qu'un masque est dans la pile.
- **Étape 3b :** version du format écrite à 18 **seulement** si le projet contient une courbe (les autres projets restent en 17, octet pour octet) ; un ancien build refuse alors le fichier au lieu de perdre la courbe en silence.
- **Étape 3b :** le critère « verbes de script `add_mask` / `set_mask_points` » est rempli par `add_mask`, `set_mask`, `set_mask_points` et `remove_mask` (`core/scripting/mask_verbs.py`, 0.6.2), après `set_color_window_points` pour les fenêtres d'étalonnage. Même résultat que l'inspecteur (test de parité sur le widget et sur la matte rendue), pas de verbe d'ordre (l'interface n'en a pas), propriétés animées refusées.
- **Hors plan :** l'inventaire des verrous (piste, calque) a montré que l'inspecteur écrivait dans le modèle sans garde ; une règle partagée `clip_edit_refusal` les couvre. L'éditeur de courbes est couvert (#149). Restent : menu « Réseaux sociaux », garde centrale dans `core/keyframe_editing`.
- Étapes **4** (trois niveaux), **5** (animer couleurs, effets, sommets), **6** (modes de fusion), **7** (empilement libre) et **8** : à faire.

## Ce qui n'est volontairement pas proposé

- Un moteur de composition 3D, un suivi de mouvement 3D, des expressions : hors sujet de « plus intuitif qu'After Effects ».
- Un plume à largeur variable, des masques à propagation automatique : plus tard, une fois les masques Bézier en place.
- Réécrire le rendu en GPU natif : voir ADR-0003 / 0004 (moteur natif) ; cette ADR n'en dépend pas.

## Décisions prises (2026-10-13, avec l'utilisateur)

1. **Niveaux d'usage : trois.** Un niveau intermédiaire « Studio » s'ajoute aux niveaux simple et avancé (étape 4). Le contenu exact de chaque niveau est à
   spécifier au début de l'étape 4 : le principe reste « aucune fonction inaccessible, seulement repliée ».
2. **Recadrage : un masque rectangle généré** (étape 2), pas de champ `crop` dédié.
3. **Empilement libre vidéo / graphique : après l'étape 6**, dans son ADR (étape 7). Le format du fichier n'est pas touché avant.
4. **Ordre : on commence par l'étape 1** (le moniteur).
5. **Flèches du clavier (2026-10-14) :** avec le moniteur au focus et un calque éditable sélectionné, Gauche / Droite / Haut / Bas le déplacent de 1 px (Maj : 10 px) au lieu de changer d'image ; sans calque éditable, ou avec Ctrl / Alt, elles gardent leur rôle (image précédente / suivante…). Choix de l'utilisateur : « le déplacement est mieux ».

## Limites de cet audit

Lecture du code et des tests existants, **aucune exécution à l'écran** ; les numéros de ligne datent du commit `b6ff622` + lots script
en cours. Les affirmations qui fondent le plan (clip vidéo non cliquable, aimantation au déplacement seulement, 8 modes, cibles
d'animation, graphiques composés après la vidéo, `_ANIMATABLE` codé en dur, compositeur CPU sans appelant) ont été revérifiées à la main.
