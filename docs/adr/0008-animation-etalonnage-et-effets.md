# ADR-0008 : animer l'étalonnage et les paramètres des effets de clip

**Statut :** Accepté le 2026-10-18 (trois décisions de cadrage prises avec l'utilisateur, voir « Décisions prises »). Étapes 1 à 6 du plan faites (la 4, l'interface, le 2026-10-19 ; la 5, `sendcmd` pour le flou, l'aberration chromatique et le rayon de la lueur, le 2026-10-20 ; la 6, le modèle et l'export de l'étalonnage animé, le 2026-10-20).
**Date :** 2026-10-18
**Décideurs :** mainteneur de Kut-Studio

Demande de l'utilisateur : pouvoir **animer** (images clés, losanges ‹◆›) les réglages de couleur d'un clip et les paramètres de ses effets, comme on anime déjà sa transformation. Aujourd'hui `docs/animation.md:204-207` le dit : « les couleurs ne sont pas animables », et les effets « ont l'architecture mais pas encore leurs cibles ». La question a été soulevée par le mode script (ADR-0005) : un verbe de script ne peut pas animer ce que l'interface ne sait pas animer (règle de parité), donc la fonctionnalité doit d'abord exister dans le modèle.

Faits relevés dans le code le 2026-10-18 (lecture seule, références vérifiées). Ce qui n'est pas vérifié est dit à chaque endroit.

---

## Résumé de la décision

1. On **anime le modèle existant** : de nouvelles cibles enregistrées dans le registre d'animation (`fx.<id_effet>.<paramètre>` et `color.<champ>`), stockées dans `Clip.animation`, sans nouveau champ. Édition, annulation, losanges et sérialisation viennent du registre générique.
2. **Les effets d'abord, l'étalonnage ensuite** (décision de l'utilisateur) : les effets sont évalués image par image par le moniteur GPU depuis longtemps ; l'étalonnage non.
3. **Hors expression FFmpeg : `sendcmd` image par image, après validation** sur FFmpeg 6.1, 7.1 et 9.0.2 (décision de l'utilisateur). Si la validation échoue pour un filtre, ses paramètres **ne sont pas animés** : on ne contourne pas.
4. **Moniteur « fidèle d'abord » pour l'étalonnage animé** (décision de l'utilisateur, comme la page Composition) : en lecture, l'aperçu fidèle rendu par FFmpeg ; à l'arrêt, la table de couleurs 3D (LUT) est cuite pour l'instant exact. On ne réécrit pas l'étalonnage en formules GPU.
5. **Le format `.kut` monte de version** dès qu'un fichier contient une image clé `fx.*` ou `color.*` : un ancien build ignore en silence les images clés d'un identifiant inconnu, ce qui perdrait l'animation.
6. **Le mode script suit** : les verbes d'images clés dérivent du registre, donc les nouvelles cibles s'y ajoutent par construction.
7. **Une image clé d'effet ne survit pas à son effet** : suppression, préréglage et collage d'attributs tiennent `Clip.animation` à jour par un seul point d'entrée, et le nom de la cible se découpe par la droite pour que tout identifiant d'effet reste valide.

---

## Contexte : ce qui existe

### 1. Un mécanisme d'animation générique, qui ne connaît pas la couleur

- Le registre `core/animation_targets.py` n'a que **quatre points d'enregistrement** : les 14 propriétés de `ClipTransform` (`:311`), `graphic.*` (16, `core/mograph_targets.py:87`), `mask.<id>.*` (8, `mograph_targets.py:138`) et `time.speed` (`core/time_targets.py:51`). Aucun `color.*` ni `fx.*`.
- Une cible enregistrée reçoit **gratuitement** l'édition, l'annulation, les losanges et la sérialisation : les images clés génériques vivent dans `Clip.animation` (`core/project_model.py:360`), écrites par `core/project_io.py:581`. Précédents d'enregistrement dynamique : `mask_target` (`mograph_targets.py:112-138`).
- Le rendu FFmpeg d'une courbe est exact : `curve_expression` (`core/animation_ffmpeg.py:59`) produit une somme de segments polynomiaux, équilibrée en arbre (log₂ n), au-delà de 20 000 caractères le graphe passe par fichier (`docs/animation.md:91-99`). Il n'est appelé aujourd'hui que pour la transformation.

### 2. L'étalonnage et les effets sont statiques, et édités par une autre porte

- `ColorGrade` (`core/color_grading.py:524`) : sept scalaires (exposition, contraste, saturation, température, teinte, ombres, hautes lumières, bornes `:61-74`), quatre roues de 4 scalaires, des courbes et une LUT (non scalaires). Le graphe de nœuds (`core/color_nodes.py`) ajoute flou, netteté, qualifieur et fenêtres (animées via `mask.<id>.*`).
- Les effets de clip : quinze paramètres scalaires (`EFFECT_PARAMETER_SPECS`, `core/effects_model.py:61`), de `color_correction` à `heat_haze`.
- **L'interface n'écrit pas ces valeurs par `keyframe_editing`** : l'étalonnage passe par `ColorGradingService.set_grade` → `edit_nodes` (`color_grading.py:1419`, seul écrivain), les effets par `update_clip_effect_parameters` (`effects_model.py:470`). Il n'y a pas de branche « image clé » (la transformation en a une, `ui/properties_panel_mixins/keyframes.py:238`).

### 3. Le rendu FFmpeg : peu de filtres acceptent une valeur qui varie

Test sur FFmpeg 9.0.2 (jamais sur 6.1 ni 7.1) :

| Filtre (usage) | Expression par image | `sendcmd` (changement en cours de route) |
|---|---|---|
| `eq` (contraste, saturation, luminosité ; correction de couleur) | **oui**, avec `eval=frame` (sans, la valeur est figée à t=0) | oui |
| `hue` (teinte) | oui, évalué par image | oui |
| `vignette` (angle) | oui, `eval=frame` requis | non observé |
| `geq` (brume de chaleur, opacité animée) | **oui** (variable `T`) | non testé |
| `colorbalance`, `colorchannelmixer`, `lutrgb`, `curves`, `gblur` | **non** | oui |
| `unsharp`, `rgbashift`, `colortemperature` | non | non observé / non |
| `lut3d` (option `file`) | non | **non** |

Conséquence : contraste, saturation, teinte et luminosité (`eq`, `hue`) et la brume de chaleur (`geq`) s'animent **par expression**, avec l'outil déjà prouvé (`curve_expression`). L'exposition (`lutrgb`), la température (`colorchannelmixer`), les ombres / hautes lumières (`colorbalance`), les roues, les courbes et le flou ne s'animent pas par expression. `sendcmd` n'est **pas utilisé** dans le dépôt aujourd'hui.

### 4. Le moniteur : effets par uniformes, étalonnage par LUT cuite

- **Effets** : `program_for(effects, time=…)` (`core/gpu_effects.py:160`) reconstruit le programme à chaque image, les paramètres sont des uniformes. Animer un paramètre d'effet y est donc direct.
- **Étalonnage** : aucun chemin analytique. La chaîne d'export est exécutée par FFmpeg sur un réseau 52³ (`core/gpu_grade.py:40`) et le résultat est lu en trilinéaire ; cache `GradeLutCache` de 48 entrées, cuisson d'environ 30 ms (`:187`, valeur de la docstring, non remesurée ici). Le fichier refuse expressément de réécrire des formules (« le moniteur ne peut pas diverger d'une formule qu'il n'implémente pas »). Une LUT par image est donc trop lente à 30 i/s.

### 5. Format, cache, versions

- Versions du `.kut` : 17 par défaut, 18 si un masque courbe existe (`PATH_MASK_VERSION`, `core/project_io.py:160`), 19 si un média a une matrice forcée (`COLOR_MATRIX_VERSION`, `:168`). Un ancien build **refuse** une version inconnue. En revanche il **ignore en silence** des images clés dont l'identifiant lui est inconnu (la lecture ne valide pas `property_name`).
- Cache : `RENDER_ENGINE_VERSION = 12` (`core/filter_graph.py:206`). L'empreinte d'un segment contient la chaîne d'étalonnage, la chaîne d'effets et `repr(layer.animation)` : des images clés dans `Clip.animation` y entrent **sans changement**.

---

## Décision

### 1. Cibles et stockage

- **Effets** : `fx.<effect_id>.<paramètre>`, un identifiant par effet de clip (les effets ont déjà un `effect_id`), un enregistrement dynamique par clip sur le modèle de `mask_target`. Bornes et valeur par défaut lues dans `EFFECT_PARAMETER_SPECS`, jamais recopiées.
  **Nom réversible, sans échappement** : `ClipEffect.__post_init__` accepte tout identifiant non vide (`core/effects_model.py:204`),
  donc un identifiant peut contenir des points (et les identifiants n'ont pas la même forme partout : `fx-` suivi de 12
  caractères à la création, 10 caractères au collage d'attributs). On découpe donc **par la droite** : le paramètre est
  ce qui suit le **dernier** point, l'identifiant ce qui est entre `fx.` et ce dernier point (`fx.my.blur.intensity` →
  effet `my.blur`, paramètre `intensity`). C'est non ambigu parce qu'**aucun nom de paramètre ne contient de point**
  (vérifié aujourd'hui) ; un test de garde le garantit pour tout `EFFECT_PARAMETER_SPECS` futur. Le modèle `mask_target`
  (découpage en exactement trois morceaux) n'est donc **pas** repris tel quel.
- **Étalonnage** : `color.<champ>` pour les scalaires de `ColorGrade` d'un clip (exposition, contraste, saturation, température, teinte, ombres, hautes lumières), puis les roues. **Ni les courbes ni les LUT** ne sont animées dans ce plan : elles ne sont pas scalaires.
- **Stockage** : `Clip.animation`, comme `graphic.*` et `mask.*`. Pas de nouveau champ.
- **Graphe de nœuds** : seul le `ColorGrade` simple du clip s'anime dans un premier temps. L'animation par nœud (`color.<noeud>.<champ>`) et celle des fenêtres d'étalonnage (aujourd'hui refusée par `mask.<id>.*`, `applies_to_clip` ne voyant que les masques de compositing) sont des suites possibles, hors de ce plan.

### 2. Un état dérivé, évalué en un seul endroit

Comme pour le suivi (ADR-0007) : la valeur de base reste dans le modèle, les images clés dans `Clip.animation`, et une fonction pure `effective_grade(clip, t)` / `effective_effect_parameters(clip, t)` donne les valeurs à l'instant `t`. L'export, le moniteur et l'aperçu fidèle appellent la **même** fonction : c'est ce qui garantit la parité.

### 3. Export

- **Par expression** quand le filtre le supporte (`eq` avec `eval=frame`, `hue`, `vignette`, `geq`) : `curve_expression` est inséré à la place de la constante. **Pour `vignette` et `geq`, seule l'expression fonctionne** (la validation de l'étape 1 montre que `sendcmd` n'y change rien). Pour `eq` et `hue`, l'expression **en `n`** (numéro d'image) est la plus fiable : exacte sur FFmpeg 6.1, 7.1 et 9.0, indépendante du `PTS`. Une expression en `t` n'est exacte que si le `PTS` repart de zéro.
- **Hors expression** : `sendcmd`, un fichier de commandes qui change l'option du filtre à chaque image. **Validé à l'étape 1** (`docs/ffmpeg-sendcmd.md`, FFmpeg 6.1.1, 7.1.5 et 9.0.2, écart 0) pour : `lutrgb` (exposition, roues, gain du bloom), `colorbalance` (ombres, hautes lumières), `colorchannelmixer` (température), `curves` (`master`), `gblur` (`sigma` **et** `sigmaV`), `rgbashift` (`rh`, `bh`), et `eq` / `hue` quand le `PTS` repart de zéro. **Non animables** : `unsharp` (donc l'intensité de **netteté**), `lut3d` et `vignette` / `geq` par `sendcmd`. Le registre n'enregistre que les paramètres validés.
- **Règles de génération** (mesurées, à respecter) : chaque commande est écrite **une demi-image avant** l'image visée, à `(n − 0,5) / cadence` (0 pour la première), sinon une image sur trois reçoit la valeur de la précédente ; `gblur` reçoit **deux** commandes (`sigma` et `sigmaV`, ce dernier étant résolu à l'initialisation) ; les instants sont relatifs au `PTS` du flux à l'entrée de `sendcmd`, donc le graphe remet le `PTS` à zéro (`setpts=PTS-STARTPTS`) avant `sendcmd`, ou le décalage est ajouté ; les filtres sont **nommés** (`filtre@nom`) pour être ciblés dans un graphe complexe. Cadences 29,97 et changements de cadence (`fps=`) : exacts avec une commande par image de sortie.
- **Coût de `lutrgb`** (mesure de l'étape 1, à confirmer) : chaque commande semble recalculer la table, environ **0,2 s par image pour l'exposition** et **0,07 s pour une roue** (720p, FFmpeg 9.0.2), contre quasi rien en statique, soit de l'ordre de **6 minutes** d'export en plus pour une exposition animée de 60 s (extrapolé de 150 images). Les autres filtres coûtent autant qu'en statique. **Condition** : l'exposition et les roues ne sont animées par `lutrgb` seul que si **aucune alternative moins coûteuse ne se valide**, et alors avec le surcoût annoncé à l'utilisateur. L'étape 6 commence par chercher cette alternative (par exemple un gain par `colorchannelmixer` pour l'exposition, à valider à l'égalité près avec la sortie statique actuelle) et par la mesurer.

### 4. Moniteur

- **Effets** : paramètres évalués à l'image courante et passés en uniformes (déjà le cas pour le temps de la brume de chaleur).
- **Étalonnage animé** : **fidèle d'abord**. En lecture, l'aperçu fidèle (segments rendus par le graphe d'export). À l'arrêt, la LUT est cuite pour l'instant exact avec `effective_grade(clip, t)` ; `lut_key` contient les valeurs évaluées. Pas d'interpolation entre deux LUT (non exacte, non présente dans le code) et pas de réécriture en formules.

### 5. Format et cache

- Nouvelle constante de version (la suivante, 20) écrite **seulement** si le fichier contient au moins une image clé `fx.*` ou `color.*` (règle « version minimale nécessaire » de `core/project_io.py`). Un ancien build refuse au lieu de perdre l'animation.
- `RENDER_ENGINE_VERSION` monte à chaque changement de rendu d'export ou d'aperçu. L'empreinte de segment couvre déjà `Clip.animation`.

### 6. Interface

Une valeur animée se modifie comme une transformation animée : le curseur écrit une image clé à la position de lecture (branche « image clé » dans l'inspecteur, sur le modèle de `keyframes.py:238`), un losange apparaît à côté du curseur. Panneaux concernés : Couleur et Effets du clip. Niveaux d'usage : les losanges suivent le niveau du panneau (Studio), l'éditeur de courbes est Avancé, comme pour la transformation.

### 7. Cycle de vie : une image clé d'effet ne survit pas à son effet

L'identifiant de l'effet est le lien entre l'effet et ses images clés. Or aujourd'hui rien ne tient `Clip.animation` à jour
quand les effets changent : `remove_effect_from_clip` (`core/effects_model.py:446`) et `apply_preset_to_clip`
(`core/effects_library.py:535`) ne font que remplacer `clip.effects`, et le collage d'attributs (`core/mograph_layers.py`,
~817-860) crée de **nouveaux** identifiants d'effet mais recopie les images clés génériques telles quelles. Sans traitement,
supprimer un effet ou appliquer un préréglage laisserait des courbes orphelines inatteignables, et coller « Effets + Images
clés » ferait pointer les courbes vers les effets d'origine. La décision :

- **Suppression d'un effet** : ses images clés `fx.<id>.*` partent **dans la même étape d'historique**, et reviennent
  avec lui à l'annulation.
- **Préréglage appliqué** : les effets remplacés perdent leurs images clés (même règle) ; les effets du préréglage
  naissent sans animation.
- **Collage d'attributs** : une table « ancien identifiant → nouvel identifiant » est construite quand les effets sont
  recréés, et appliquée aux images clés `fx.*` collées. Une image clé dont l'effet n'est pas collé est abandonnée.
- **Duplication et coupe d'un clip** : les images clés `fx.*` suivent le même traitement que le reste de `Clip.animation`
  (coupe à l'instant, décalage temporel) ; à tester, pas à supposer.
- **Tolérance à la lecture** : une image clé `fx.*` dont l'effet n'existe pas est **ignorée** (comportement actuel du
  registre pour un identifiant inconnu) et retirée à la prochaine édition du clip ; elle ne fait jamais échouer le chargement.
- **Un seul point d'entrée** : une fonction du modèle (par exemple `animation_for_effects(clip)`) donne les identifiants
  encore valides ; toutes les opérations ci-dessus l'utilisent, au lieu que chacune recopie la règle.

### 8. Mode script

`add_keyframe` / `remove_keyframe` dérivent leurs propriétés du registre (PR #195, ouverte à la date de cet ADR, pas encore mergée). Les cibles `fx.*` dépendent d'un effet précis du clip : verbes à part, avec le sélecteur `effect`, comme `add_mask_keyframe`. Les cibles `color.*` s'ajouteront à la liste dérivée. Aucun verbe n'est écrit avant que la cible existe dans le modèle.

---

## Options étudiées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| **A. Effets d'abord, puis étalonnage** | GPU effets déjà par image ; valide la méthode (cibles, version, rendu) à moindre coût | l'étalonnage, plus utile, vient plus tard | **retenu** |
| B. Étalonnage d'abord | plus utile | le plus difficile (moniteur par LUT, filtres sans expression) | écarté |
| C. Sous-ensemble expressible des deux | rapide | incomplet (pas d'exposition ni de température) | écarté (c'est la première étape de A, pas son but) |
| **D. `sendcmd` après validation** (hors expression) | couvre exposition, température, ombres / hautes lumières, flou | jamais validé sur 6.1 / 7.1 ; décalages de temps | **retenu, conditionnel** |
| E. Découper en segments à paramètres constants | simple | escalier visible ou coupures et caches lourds | écarté |
| F. Cuire une LUT par image | exact | ~30 ms par image, cache de 48 | écarté |
| **G. Moniteur fidèle d'abord** | exact, pas de divergence | aperçu parfois en attente de rendu | **retenu** |
| H. Réécrire l'étalonnage en formules GPU | fluide | chantier lourd, contraire au principe du moniteur | écarté |

---

## Conséquences

**Positives.** Les mêmes outils que la transformation : édition, annulation, losanges, sauvegarde, courbes. La parité du mode script vient du registre. La méthode se valide sur les effets avant de toucher l'étalonnage.

**Négatives et risques.**
- **`sendcmd` n'a jamais été validé** sur 6.1 et 7.1, deux versions où un défaut de `swscale` nous a déjà mordus cette semaine. Si un filtre échoue, ses paramètres restent non animables : c'est assumé.
- Un fichier avec animation de couleur ne s'ouvre plus dans un build antérieur (il refuse). C'est le comportement voulu, mais visible.
- Coût de rendu : une expression `curve_expression` par paramètre animé et, pour `sendcmd`, un fichier de commandes par image (taille et lecture non mesurées ici).
- L'aperçu d'un étalonnage animé peut attendre un rendu en lecture (fidèle d'abord).
- **Le lien effet ↔ images clés est un identifiant** : toute opération qui remplace, supprime ou recrée des effets doit
  passer par le point d'entrée du cycle de vie, sinon des courbes restent orphelines ou animent le mauvais effet. Les
  trois opérations connues aujourd'hui (suppression, préréglage, collage) sont listées ; une opération future qui touche
  `clip.effects` sans passer par là est un défaut, que le test de garde de l'étape 2 doit attraper (grep des écritures
  de `clip.effects`).
- L'animation du graphe de nœuds et des fenêtres d'étalonnage reste hors périmètre : un utilisateur qui découpe son étalonnage en plusieurs nœuds ne peut plus animer.

---

## Plan d'action (chaque étape est une PR à part, fusionnable seule)

0. **Cet ADR.**
1. **Validation de `sendcmd` (faite, résultats dans `docs/ffmpeg-sendcmd.md`)** (aucun code produit) : pour chaque filtre hors expression (exposition `lutrgb`, température `colorchannelmixer`, `colorbalance`, `curves`, `gblur`, `unsharp`, `rgbashift`), un banc sur FFmpeg 6.1 (Ubuntu 24.04), 7.1 (Debian trixie) et 9.0.2, en Docker `--platform linux/amd64` : la sortie avec `sendcmd` égale-t-elle la sortie du filtre statique à la même valeur ? Comportement aux limites d'image, avec un segment qui ne commence pas à zéro, avec un changement de cadence, taille du fichier de commandes. Résultat consigné dans `docs/` et dans cet ADR (tableau filtre × version), avec la liste finale des paramètres animables.
2. **Cibles des effets + format + cycle de vie** : `fx.*` enregistrées (découpage par la droite, test de garde « aucun nom de paramètre ne contient de point »), constante de version 20 conditionnelle, `effective_effect_parameters`, **cycle de vie** (suppression, préréglage, collage avec table de correspondance des identifiants, coupe et duplication, orphelins tolérés puis nettoyés). Tests : aller-retour d'un fichier, ancien build refuse, identifiant à points, suppression puis annulation, préréglage, collage « Effets + Images clés » (les courbes animent les **nouveaux** effets), coupe d'un clip animé, image clé orpheline au chargement. Sans rendu encore.
3. **Rendu des effets (fait, résultats ci-dessous)** : par expression (`eq`, `vignette`, `geq`) à l'export, uniformes par image dans le moniteur, parité export / GPU sur des images échantillonnées, `RENDER_ENGINE_VERSION`.
4. **Interface des effets (faite, résultats ci-dessous)** : branche « image clé » dans le panneau Effets, losanges, menu, éditeur de courbes, niveau d'usage, manifeste d'énumération.
5. **Effets hors expression** via `sendcmd` (fait le 2026-10-20, résultats ci-dessous) : **flou** (`gblur`, `sigma` + `sigmaV`), **aberration chromatique** (`rgbashift`, `rh` / `bh`) et **rayon de la lueur** (`gblur` du bloom) sont rendus et enregistrés ; le **gain et le seuil de la lueur** (`lutrgb`, coût) et la **netteté** (`unsharp`, ignore les commandes) restent statiques.
6. **Cibles et rendu de l'étalonnage (fait le 2026-10-20, résultats ci-dessous)** : `color.*` (23 propriétés), `effective_grade`, export par expression (`eq`, `hue`) puis `sendcmd` pour le reste (`colorbalance`, `colorchannelmixer`, et `curves` à 256 points pour l'exposition et les roues, **pas** `lutrgb` : la mesure demandée par le plan a tranché), format 24, moteur de rendu inchangé. Le moniteur n'est pas touché (étape 7).
7. **Moniteur fidèle d'abord** pour l'étalonnage animé, LUT cuite à l'arrêt avec les valeurs évaluées.
8. **Interface de l'étalonnage** : losanges dans le panneau Couleur.
9. **Mode script** : verbes `fx` avec sélecteur `effect`, `color.*` dans la liste dérivée, documentation, tableau des unités.

Estimation grossière (ordre de grandeur, non engagée) : effets 4 à 6 jours, étalonnage 8 à 12 jours, validation `sendcmd` incluse.

---

## Résultats de l'étape 3 (2026-10-19) : rendu des effets animés

**Rendu et enregistré** : `color_correction` (`brightness`, `contrast`, `saturation`, filtre `eq` en `eval=frame`) et `vignette`
(`intensity`, option `angle` = intensité × π/4, `eval=frame`), à l'export et à l'aperçu fidèle (même graphe), et au moniteur GPU
(paramètres évalués à l'instant local par `evaluated_effects`, sans image clé : mêmes effets, même programme). Moteur de rendu
13. Liste enregistrée = ces quatre paramètres ; le test de cohérence classe chaque paramètre de `EFFECT_PARAMETER_SPECS` dans
exactement une de trois listes : enregistrés, en attente (`PENDING_EFFECT_PARAMS`), exclus.

**Base de temps.** Le filtre d'effet reçoit le temps local du clip : `PTS` repart de zéro au début du clip (`setpts=PTS-STARTPTS`
après `fps`, avec `+skip/TB` à l'origine d'un segment d'aperçu, `+retimed_skip/TB` pour un clip remappé, depuis un `TimeMap` qui
sort un flux horodaté de 0) *avant* les effets ; `PTS+timeline_start` n'est posé qu'à la fin. Expression en `t` (`eq`,
`vignette`) ou `T` (`geq`), comme la transformation animée. Testé : clip entier contre segment démarrant en milieu de clip
(clip décalé et source rognée, cadence 24 vers 10, vitesse ×2), images identiques et égales au rendu statique à la valeur
évaluée ; la mutation « pas de décalage de segment » fait échouer ces tests.

**Brume de chaleur : rendu écrit et vérifié, mais NON enregistré (coût de `geq`).** Mesuré sur 1280x720, 150 images, `-f null`,
FFmpeg 9.0.2, machine chargée (seuls les rapports comptent), `tools/measure_geq_cost.py` :

| Cas | Temps | Rapport au statique |
|---|---|---|
| statique | 8,6 s | ×1 |
| 2 images clés, amplitude seule | 22,4 s | ×2,6 |
| 10 images clés, amplitude seule | 80,1 s | ×9,3 |
| 2 images clés, 4 paramètres | 76,6 s | ×8,9 |
| 10 images clés, 4 paramètres | 409,7 s | ×47,5 |

Le critère (10 images clés à moins de ×2) est manqué d'un facteur 4,7 pour un seul paramètre et 24 pour quatre. Les quatre
paramètres de la brume (`amplitude`, `frequency`, `top`, `span`) restent donc statiques ; leurs noms sont dans
`PENDING_EFFECT_PARAMS`. Pistes non implémentées : évaluer la courbe une fois par image hors de `geq` (un `geq` par image et par
segment constant, ou une carte de déplacement calculée par image pour `displace`), ou borner le nombre d'images clés.

**Composition : refus.** La composition nodale rend les effets d'un nœud sans animation (`composition_render`, `_effect_chain(node.effects,
None, …)`) : convertir un clip aux effets animés perdrait l'animation en silence. `convert_to_composition` refuse tout clip qui
porte au moins une image clé `fx.*` dont l'effet existe (`has_live_effect_animation`) ; les orphelines ne bloquent pas. Transporter
les courbes dans la composition est hors de ce lot.

**`heat_haze.speed` exclu** : la phase vaut `T·v` ; `v(T)` à la place de `v` fait sauter la phase (il faudrait `∫ v dt`).
Avec `frequency` animé, la phase `Y·f(T)` change de forme à chaque instant : voulu, et l'export (`geq`) et le moniteur
(`HazeOp`) concordent aux seuils d'un effet statique (testé en enregistrant temporairement la brume).

**Parité et validation FFmpeg** (`tools/validate_fx_render.py`, image `n` de la sortie animée contre le rendu statique à la valeur
de la courbe) : FFmpeg 6.1.1 (Ubuntu 24.04), 7.1.5 (Debian trixie), 9.0.2 : écart maximal **0** niveau sur `eq` (luminosité et
contraste, saturation), `vignette` et la brume (amplitude ; fréquence, haut, étendue), avec et sans `-cpuflags 0`. Parité export /
moniteur (référence numpy) : aux seuils de l'effet statique. Deux constats : (1) l'export statique arrondit l'angle de la
vignette à six décimales ; un pixel sur une frontière d'arrondi bascule d'un niveau (écart 2 sur 1 octet sur 230 000) contre
l'expression exacte : la référence prend l'angle exact. (2) Hors animation, la formule du moniteur pour `eq` s'écarte déjà de
l'export à luminosité négative (écart moyen 1,9 à −0,1, 2,8 à −0,2) : défaut existant, non traité ici.

## Résultats de l'étape 4 (2026-10-19) : interface des effets animés

**Visible.** Dans l'onglet Effets de l'inspecteur, une rangée ‹ ◆ › sous chaque réglage de `ANIMATABLE_EFFECT_PARAMS` (luminosité,
contraste, saturation de la correction couleur ; intensité de la vignette), et sous aucun autre. Comportement de la
transformation : sans image clé le curseur écrit la valeur de **base** ; le losange pose ou retire une image clé à la tête de
lecture (temps local du clip) ; animé, le curseur affiche la valeur évaluée (`evaluated_effects`) et sa saisie écrit l'image clé de
l'instant ; ‹ › naviguent ; une étape d'historique par édition. Le menu clic droit du losange est celui de la transformation
(`animation_menu`) plus « Supprimer l'animation de l'effet » (`clear_effect_animation`, une étape d'historique, chaque réglage
garde la valeur montrée, les images clés orphelines de l'effet partent aussi) : c'est le geste que le refus de conversion en
composition réclame. L'éditeur de courbes liste les réglages animables (`targets_for_clip(..., include_effects=True)`, via
`_graph_properties`) sous « Correction couleur — Luminosité » ; deux effets du même type : « n° 2 ».

**Choix de conception.**
- Les rangées sont **reconstruites** avec les réglages de l'effet affiché (nouveaux widgets, jamais déplacés ni reparentés), chacune
  dans un `LevelGate` créé avec elle. Elles sont dans leur propre ligne du formulaire, sous le curseur (le curseur reste un enfant
  direct du formulaire : l'ordre du niveau Avancé est celui d'avant).
- Les images clés d'effets viennent de la vue du clip (`TimelineClipView.effect_keyframes`, champ neuf à valeur par défaut vide).
- Deux signaux neufs (`effect_keyframe_set`, `effect_keyframe_removed`) et un troisième (`effect_animation_cleared`) ; le premier
  clic d'un réglage non animé passe par `animation_toggled`, comme la transformation. Les garde-fous sont ceux des commandes
  d'animation : `clip_edit_refusal` côté fenêtre, `_guard` côté `core.keyframe_editing`.
- Les raccourcis sans propriété active (`add_keyframe_at_playhead`) ne créent **jamais** de courbe d'effet (`_animation_properties`
  reste sans effets) ; une courbe d'effet active ou déjà animée est visée comme les autres.
- `can_paste` reçoit maintenant le clip de destination (coller sur un réglage `fx.*` échouait par `KeyError`).

**Niveaux d'usage.** Losanges : Studio ; menu de losange : « Désactiver » et « Supprimer l'animation de l'effet » dès Studio,
interpolation / copier / coller / éditeur Avancé ; éditeur de courbes : Avancé (déjà). Le groupe Effets étant lui-même Studio,
« Afficher plus d'options » révèle aussi les losanges au niveau Simple (sinon inaccessibles depuis Simple). Le manifeste
d'énumération (`tests/data/usage_level_controls.json`) gagne 12 lignes, toutes Studio ; l'inventaire joue maintenant un passage par
effet (correction couleur, vignette, netteté).

**Mutation** : retirer la branche « écrire l'image clé quand le réglage est animé » (`_on_effect_parameter_changed`) fait échouer 5
tests dont celui du cas (c).

**Non fait / limites.** Rien n'a été vu dans l'application réelle, seulement hors écran. Les raccourcis clavier d'animation ne
visent un réglage d'effet que s'il est la propriété active (clic sur son losange ou sa courbe) ; la timeline ne dessine pas les
images clés d'effets (seul le losange de l'inspecteur et l'éditeur de courbes les montrent).

---

## Résultats de l'étape 5 (2026-10-20) : flou, aberration chromatique, rayon de la lueur par `sendcmd`

**Rendu et enregistré** : `blur.intensity` (`gblur` : `sigma` **et** `sigmaV`), `chromatic_aberration.intensity` (`rgbashift` : `rh` et
`bh` opposés, entiers) et `glow.radius` (le `gblur` du bloom, plancher 0,01 comme en statique), à l'export, à l'aperçu fidèle
(même graphe) et au moniteur GPU (valeur évaluée à l'image courante par `evaluated_effects`, sans code de plus : le registre
suffit). Nouveau module `core/effect_sendcmd.py` ; le registre se scinde en `EXPRESSION_EFFECT_PARAMS` (étape 3) et
`SENDCMD_EFFECT_PARAMS`, `ANIMATABLE_EFFECT_PARAMS` en est l'union (`PENDING_EFFECT_PARAMS` ne garde que la brume de chaleur).
Refusés (interface : pas de losange ; verbes : « pas animable » ou valeur hors du choix de `parameter`) : netteté, seuil et gain de
la lueur, brume de chaleur. Moteur de rendu inchangé (13) : un projet sans courbe sur ces réglages produit exactement le graphe
d'avant (testé : aucun `sendcmd`, aucun fichier ; les graphes des tests d'or de l'étape 3 sont inchangés), et le format est
écrit en **version 23 conditionnelle** (`SENDCMD_ANIMATION_VERSION`) : la 0.8.0 publiée lit la 22 mais n'a pas ces trois cibles et rendrait ces réglages à leur valeur de base sans rien dire. La 23 n'est écrite que si un clip a une image clé sur un paramètre de `SENDCMD_EFFECT_PARAMS` dont l'effet existe (le registre décide, pas une liste en dur) ; une courbe de vignette ou de correction de couleur (déjà publiées) reste en 20 ; la 23 l'emporte sur la 22 (scène 3D) ; un projet sans courbe est écrit octet pour octet comme avant.

**Coût** (`tools/measure_sendcmd_cost.py`, la chaîne et le fichier de commandes de l'application, 1920x1080, 150 images,
`-f null`, meilleur de deux ; la valeur statique est la moyenne de la courbe). Rapport animé / statique :

| Réglage (filtre) | FFmpeg | Statique | 2 images clés | 10 images clés |
|---|---|---|---|---|
| flou (`gblur`) | 6.1.1 | 0,73 s | 0,75 s (×1,03) | 0,75 s (×1,03) |
| flou (`gblur`) | 7.1.5 | 0,75 s | 0,76 s (×1,01) | 0,75 s (×1,00) |
| flou (`gblur`) | 9.0.2 | 0,63 s | 0,63 s (×1,00) | 0,64 s (×1,02) |
| rayon du bloom (`gblur`, graphe `split` / `blend`) | 6.1.1 | 3,29 s | 3,08 s (×0,94) | 3,10 s (×0,94) |
| rayon du bloom (`gblur`, graphe `split` / `blend`) | 7.1.5 | 3,16 s | 3,13 s (×0,99) | 3,11 s (×0,98) |
| rayon du bloom (`gblur`, graphe `split` / `blend`) | 9.0.2 | 2,15 s | 2,14 s (×1,00) | 2,19 s (×1,02) |
| aberration chromatique (`rgbashift`) | 6.1.1 | 1,13 s | 1,12 s (×0,99) | 1,13 s (×1,00) |
| aberration chromatique (`rgbashift`) | 7.1.5 | 1,14 s | 1,28 s (×1,12) | 1,09 s (×0,96) |
| aberration chromatique (`rgbashift`) | 9.0.2 | 0,53 s | 0,51 s (×0,96) | 0,48 s (×0,91) |

(6.1 et 7.1 sont des images Docker amd64 émulées sur arm64 : les rapports comptent, pas les temps entre versions.) Le fichier de commandes a une
ligne par **changement de valeur**, pas par image clé : 10 Ko pour 5 s à 30 i/s (150 lignes), 0,06 à 0,11 Mo pour 60 s
(`docs/ffmpeg-sendcmd.md`). Critère de la règle de l'étape 1 (plus de ×3 : non enregistré) : les trois sont à ×1, enregistrés.

**Génération** (mesurée à l'étape 1, appliquée) : une instruction par image, à `(n − 0,5) / cadence`, depuis le temps local de la
première image du flux (0 ; `origin − début du clip` pour un segment d'aperçu ; 0 pour un arrêt sur image), cadence exacte
(`Fraction`, 29,97 = 30000/1001), valeurs de `AnimatableProperty.evaluate` (courbe construite une fois) puis conversion de la
chaîne statique ; une ligne seulement si la valeur change ; filtres nommés `gblur@<étiquette du graphe>` (étiquette réduite à
`[0-9A-Za-z_]`). Borne posée **avant** d'allouer le texte : 2 000 000 d'instructions, message sans donnée de l'utilisateur.
`sendcmd` est placé juste avant le filtre visé dans la chaîne des effets (donc après le recalage de `PTS` au temps local du clip et
après `scale` / `rotate`), et avant `format=rgba,split` pour le bloom.

**Segment hors grille.** `setpts` tronque `skip / TB` au tick : un segment dont l'origine tombe entre deux images (ou, par erreur d'arrondi flottant, `0,3 / 0,1 = 2,9999999999999996`) a sa première image un tick plus tôt que `skip`. `segment_start` reproduit la troncature (mesuré contre `showinfo` sur 9 cadences x 13 origines) et le fichier de commandes vise ces images ; le montage double par ailleurs la première image d'un segment hors grille (comportement d'avant, non traité).

**Fichier temporaire.** `_build_filter_complex(…, temporary_files=None)` (paramètre neuf, valeur par défaut `None` : l'interface ne
change pas) pose un contexte (`command_files`) où chaque fichier de commandes est un fichier unique ajouté à la liste de
l'appelant : `build_export_command` (la sienne, vidée en cas d'échec de la construction après l'écriture), `ExportEngine` (scopes :
`take_temporary_files`), `build_preview_command` / `build_filter_complex` (la liste `temporary_files` que le moteur d'aperçu
supprime déjà après le rendu). Sans liste (outils, bancs, tests) : un fichier **nommé par son contenu** dans un dossier de
processus supprimé à la sortie. Le chemin cité passe par `_escape_filter_path` (`C\:`, `/`, apostrophe fermée-échappée-rouverte) ;
un chemin avec un caractère de contrôle est refusé. FFmpeg réel : un dossier « Jean D'Arc, [v1]; a=b » avec un fichier « x'y.cmd »
est lu. Les empreintes de cache (`fingerprint_plan`) construisent la chaîne **sans** contexte de flux : elles ne dépendent donc
jamais du nom aléatoire d'un fichier.

**Parité** (`tools/validate_fx_render.py`, image `n` animée contre rendu statique à la valeur de la courbe) : écart maximal **0**
niveau sur FFmpeg 6.1.1, 7.1.5 et 9.0.2, avec et sans `-cpuflags 0`, pour le flou (linéaire ; Bézier + maintien), le rayon du bloom
(ease) et l'aberration chromatique (linéaire) ; **2** niveaux pour une aberration qui part de 0 (la conversion RVB aller-retour des
images à décalage 0, que le rendu statique à 0 n'a pas : limite connue, documentée, sans effet visible). Tests : image `n` contre
statique, 29,97 i/s, clip entier contre segment d'aperçu (clip décalé, source rognée ; 24 vers 10 i/s ; vitesse ×2), arrêt sur image,
parité avec la référence du moniteur (même écart que le statique à la même valeur, et nettement plus grand si le moniteur gardait la
valeur de la première image). Constat hors animation : à la même valeur, l'écart moyen / p99 entre l'export et la référence du
moniteur pour le flou grandit avec σ (0,73 / 7,6 à 4 ; 2,09 / 21,6 à 12 sur la mire lisse de ces tests) : c'est la formule du
moniteur, pas l'animation (le seuil de `test_gpu_pipeline` est calibré sur une autre image).

**Cycle de vie, composition, script.** Les nouveaux réglages passent par les mêmes points d'entrée : suppression d'un effet et
annulation (une étape d'historique), coupe, duplication, refus de la conversion en composition, plafond de
`MAX_KEYFRAMES_PER_CLIP`, piste verrouillée (interface : rangées désactivées **et** signaux refusés ; verbes : refus, projet
inchangé). Tous testés pour les trois réglages (`tests/test_effect_sendcmd_model.py`, `tests/test_effect_sendcmd_ui.py`).

**Non fait / limites.** Rien n'a été vu dans l'application réelle, seulement hors écran. Les images clés du flou, de l'aberration et
du rayon ne s'affichent pas dans la timeline (comme celles des autres effets) ; les mêmes raccourcis qu'à l'étape 4 les visent.

## Résultats de l'étape 6 (2026-10-20) : l'étalonnage animé, modèle et export

**Livré** : 23 propriétés `color.*` (`core/grade_targets.py`) pour le **réglage simple** d'un clip : les sept scalaires (`exposure`, `contrast`, `saturation`, `temperature`, `hue`, `shadows`, `highlights`) et les quatre roues canal par canal (`color.<lift|gamma|gain|offset>.<r|g|b|y>`), bornes lues dans `core/color_grading.py`. `effective_grade(clip, t)` (fonction pure : sans image clé, **le même objet** que `clip.color_grade`, `None` compris). Rendu à l'export et à l'aperçu fidèle (même graphe) : `core/grade_render.py`, `core/grade_tables.py`. Format **24** conditionnel. Le moniteur n'est pas touché (étape 7) : il applique l'étalonnage de base. Ni courbes ni LUT (non scalaires), et **un clip étalonné par un graphe de nœuds n'a pas ces cibles** (`KeyError`) ; une image clé `color.*` qui reste dans un tel clip est conservée mais ignorée par le rendu (comme une orpheline `fx.*`).

**Où elles sont visibles.** `targets_for_clip(..., include_effects=True)` (Graph Editor, menus) ne les liste **pas** : un nouveau paramètre `include_grade` (faux par défaut, aucune interface ne le passe) les liste, pour l'étape 8. Elles se résolvent avec `get_target(id, clip)` (modèle, rendu, fichier chargé), mais **les verbes de script les refusent tous** (garde `refuse_grade_property` au point d'entrée commun : `resolve_keyframe_target` pour les verbes d'édition d'image clé, plus `add_keyframe` et `remove_keyframe`), avant toute écriture : sans interface ni verbe pour les inspecter, une clé créée par script serait rendue à l'export sans qu'on puisse la retirer. L'étape 9 lèvera ce refus avec ses verbes.

### La mesure demandée : une alternative à `lutrgb` (exposition et roues)

Le plan de l'ADR le disait : l'exposition et les roues sont écrites par `lutrgb` (expression évaluée pour chaque niveau d'entrée), et `sendcmd` sur `lutrgb` recalcule la table à chaque commande (étape 1 : ~0,2 s par image). `tools/measure_grade_cost.py` compare, à 1080p, 150 images, `-f null`, meilleur de deux, **le même étage** statique et animé (2 ou 10 images clés), avec la chaîne de l'application (conversion de chroma 4:2:0 comprise) et son fichier de commandes (en fenêtres : voir plus bas). Rapport animé / statique, **FFmpeg 9.0.2 (macOS) / 6.1.1 / 7.1.5** (les deux dernières : Docker amd64 émulé sur arm64, qui compare animé et statique, pas les versions entre elles) ; premier chiffre de « Statique » : secondes :

| Étage | Statique (s) | 2 images clés | 10 images clés | `lutrgb` + `sendcmd` (écarté, 2 images clés) |
|---|---|---|---|---|
| exposition (`curves`, 256 points) | 0,65 / 0,96 / 1,08 | ×0,91 / ×0,90 / ×0,91 | ×0,93 / ×0,91 / ×0,90 | **×48,1 / ×46,5 / ×52,7** |
| roue, molette seule (`curves master`) | 0,68 / 0,86 / 1,00 | ×0,90 / ×1,01 / ×0,98 | ×0,92 / ×1,04 / ×0,98 | **×17,1 / ×18,1 / ×20,2** |
| roues colorées (`curves red/green/blue`) | 0,63 / 0,86 / 0,91 | ×1,03 / ×1,22 / ×1,05 | ×1,13 / ×1,11 / ×1,11 | **×18,6 / ×18,0 / ×21,4** |
| contraste + saturation (`eq`, expression) | 0,77 / 0,85 / 0,84 | ×0,93 / ×0,96 / ×0,94 | ×0,96 / ×0,95 / ×0,96 | |
| teinte (`hue`, expression) | 0,61 / 0,83 / 0,86 | ×1,02 / ×1,42 / ×1,31 | ×1,03 / ×1,42 / ×1,34 | |
| ombres + hautes lumières (`colorbalance`, `sendcmd`) | 1,18 / 1,62 / 1,60 | ×0,97 / ×1,01 / ×1,05 | ×1,06 / ×1,01 / ×1,04 | |
| température (`colorchannelmixer`, `sendcmd`) | 0,78 / 1,02 / 1,09 | ×1,08 / ×1,00 / ×0,98 | ×1,06 / ×1,00 / ×1,00 | |
| **tous les étages ensemble** | 2,46 / 3,21 / 2,92 | ×1,02 / ×1,13 / ×1,15 | ×0,97 / ×1,10 / ×1,15 | |

(Les rapports inférieurs à 1 sont du bruit de mesure ; un `-f null` d'une demi-seconde en compte plusieurs centièmes.) Même mesure avec un encodage `libx264 -preset medium -crf 23` à la place de `-f null` (un export réel, où l'encodeur domine ; 9.0.2 seulement) : exposition ×1,18 à ×1,29 (`lutrgb` : ×25,7), roue molette seule ×1,04 à ×1,13 (`lutrgb` : ×8,7), roues colorées ×1,09 à ×1,24 (`lutrgb` : ×9,8), `eq` ×1,14 à ×1,27, `hue` ×1,49 à ×1,70, `colorbalance` ×1,22 à ×1,34, température ×1,10 à ×1,31, tous ensemble ×1,15 à ×1,37.

**Décision (sur mesure)** : l'exposition et les roues passent par **`curves` à 256 points**, `lutrgb` n'est pas animé. Le critère « au plus ×3 la même chaîne non animée » est tenu par tous les étages sur les trois FFmpeg (le pire : ×1,70, `hue` sous encodage ; ×1,42 sans) ; `lutrgb` le manque d'un facteur 3 à 17 (×8,7 à ×52,7). Cause de l'écart (sonde, non lue dans le code de FFmpeg) : chaque commande `lutrgb` recalcule la table de **tous** les canaux (une commande sur `r` seul : 1,8 s ; trois commandes `r`, `g`, `b` : 16,9 s pour les mêmes 150 images, expression d'exposition), alors que `curves` reconstruit sa courbe sans évaluer d'expression. Alternatives écartées sans mesure, pour une raison de principe : `colorchannelmixer` et `colorlevels` sont linéaires (l'exposition de l'export est une exposition en lumière linéaire, sRGB décodé : changer de formule change le rendu des fichiers existants), `lut3d` ignore `sendcmd` (étape 1), `geq` évalue par pixel (×9 à ×47, étape 3), un flux de LUT (`haldclut`) n'est pas exact et n'a pas été essayé.

**Exactitude de `curves` contre `lutrgb`.** `curves` interpole un spline entre ses points ; avec un point par niveau d'entrée (256), la sortie est celle de la table. Deux règles de `curves` à respecter, trouvées à la mesure : (1) il refuse **toute** la commande si `(int)(x·255)` ne croît pas strictement (0,003922 × 255 = 1,0001 mais 0,007843 × 255 = 1,9999) : l'abscisse du niveau `i` est écrite **juste au-dessus** de `i/255` par une division entière exacte ; (2) la table de sortie est tronquée : l'ordonnée est le **centre** de la case (`niveau + 0,5`, ramené à 1 pour le blanc ; avec l'ordonnée à `niveau`, 121 niveaux sur 256 étaient décalés d'un). Résultat : la table calculée par `core/grade_tables.py` égale celle de `lutrgb` sur les 256 niveaux d'entrée, et la sortie de `curves` égale celle de `lutrgb` : écart **0** pour 8 expositions et 12 jeux de roues aléatoires par version (`tools/validate_fx_render.py`, 20 « tables »), et 25 expositions et jusqu'à 25 jeux de roues aléatoires dans `tests/test_grade_render.py` (9.0.2).

**Parité image par image** (`tools/validate_fx_render.py`, 30 images à 10 i/s, entrée `testsrc2` + bruit, image `n` animée contre le rendu **statique** de l'étalonnage réglé sur `effective_grade(clip, n/10)`, interpolations Bézier / ease / linéaire) : 25 cas (une propriété chacun, les 23 ensemble, une exposition qui traverse 0 EV) et 20 tables, écart maximal **0** niveau sur FFmpeg **6.1.1**, **7.1.5** et **9.0.2**, avec et sans `-cpuflags 0` (relevé après la mise en fenêtres : les cas à plusieurs fenêtres, comme « les 23 ensemble », y sont). Dans `tests/test_grade_render.py` (9.0.2 seulement, en local) : les 23 propriétés, les 23 ensemble (un seul fichier, des cibles distinctes dans la même ligne), 29,97 et 30 i/s, un clip jamais étalonné (`color_grade` à `None`), un segment d'aperçu qui démarre en milieu de clip contre le clip entier et contre le statique (clip décalé, source 24 i/s vers 10, vitesse ×2 : au plus 1 niveau entre segment et clip, 2 contre le statique après recodage du montage).

**Limite connue, mesurée** : un étage animé est **toujours émis**, y compris aux images où sa valeur est exactement neutre (la chaîne statique l'omet). `curves`, `colorbalance` et `colorchannelmixer` neutres sont l'identité exacte (écart 0). `eq` et `hue` neutres aussi (écart 0) quand rien de RVB ne les précède (le flux est alors encore en YUV) ; **derrière un filtre RVB** (exposition fixe, ombres…) ils ajoutent l'aller-retour RVB / YUV de la chaîne : **2 niveaux au plus, sur cette seule image** (tous les autres instants sont exacts).

**Fichier de commandes** : un seul **plan** par étalonnage (plusieurs cibles dans la même ligne, validé : `colorbalance@…`, `colorchannelmixer@…`, `curves@…` ensemble, écart 0), une ligne par image où une valeur change (une demi-image avant l'image visée), les `sendcmd` posés à la suite devant le premier filtre qu'ils pilotent. Tailles (150 images) : exposition 0,70 Mo (4,7 Ko par image : 256 points de 17 octets), roue molette seule 0,70 Mo, deux canaux de roue 1,4 Mo, ombres + hautes lumières 29 Ko, température 18 Ko, tous les étages ensemble 2,8 Mo. La molette seule (R, V et B égaux à tout instant : palets de base égaux et jamais animés) n'envoie qu'un canal `master` ; sinon `red`, `green`, `blue` (seuls ceux dont la table change, et seulement quand elle change : une courbe qui reste constante ne coûte rien).

**Constat sur la mémoire de `sendcmd` (FFmpeg 6.1 et 7.1), trouvé par la mesure du coût en Docker.** La première version écrivait un seul fichier : le banc de coût de `tous les étages ensemble` (2,8 Mo) fut **tué par le système** (code 137) sur 6.1.1 et 7.1.5 alors qu'il passait sur 9.0.2. Cause (sondes `/tmp/memx`, `ru_maxrss`, une seule image rendue, donc coût de la **lecture** du fichier) : ces versions lisent un fichier de commandes avec une mémoire de l'ordre de **mille fois sa taille** : 800 lignes de 256 points (3,7 Mo) montent FFmpeg 7.1.5 à **4 428 Mo** (400 lignes : 1 136 Mo ; 200 : 335 Mo ; 100 : 134 Mo), contre **19 Mo** sur la 9.0.2 ; avec 33 points par ligne : 159 Mo ; ce n'est ni `curves` (la même lecture vers un filtre absent coûte pareil) ni la résolution. Extrapolée, une minute d'exposition animée (1 800 lignes) aurait demandé ~21 Go. Deux mesures décident de la parade : (1) la mémoire du pic est **rendue** après la lecture de chaque fichier : 800 lignes coupées en 8 fichiers (donc 8 `sendcmd` à la suite) montent à **164 Mo** (4 × 200 lignes : 362 Mo ; 2 × 400 : 1 160 Mo) ; (2) il en reste en mémoire résidente ~40 à 45 fois la taille totale des fichiers (7.1.5 : 1 982 Mo pour 41,8 Mo de commandes ; 6.1.1 : 1 712 Mo ; 9.0.2 : 124 Mo). **Parade retenue** : le plan est écrit en **fenêtres de 192 Ko** (un fichier et un `sendcmd` par fenêtre, `WINDOW_BYTES`) et le total est **borné à 32 Mo** (`MAX_COMMAND_BYTES`, au fil de l'écriture : le message ne recopie rien, le fichier partiel part avec les temporaires de l'export). Mesure avec l'application (1080p, 1 800 images, `-f null`, pic de mémoire résidente) : exposition seule 205 Mo (6.1.1) / 265 Mo (7.1.5) / 120 Mo (9.0.2), roues colorées 320 / 329 / 138 Mo, **cinq étages ensemble 1 118 / 1 086 / 158 Mo** ; cinq minutes d'exposition (9 000 images, 320x180) 1 712 / 1 982 / 124 Mo. Soit, pour la limite de 32 Mo : environ quatre minutes d'exposition animée à 30 i/s, une à deux minutes de roues colorées. Le texte est écrit **ligne à ligne** (`write_command_stream` : jamais en entier en mémoire).

**Même défaut dans l'étape 5 : voir « Correctif de la mémoire de `sendcmd` des effets » ci-dessous** (la parade des fenêtres, essayée, ne s'y applique pas).

**Conversion de chroma.** `core/chroma_siting.py` reconnaît les filtres RVB par leur nom : `curves@v0cge=` (filtre **nommé**) n'était pas reconnu et FFmpeg aurait inséré sa propre conversion (recopie de la chroma au lieu de l'interpolation co-sitée, voir « Chroma 4:2:0 »). L'expression régulière accepte maintenant un suffixe `@nom` (test : la conversion co-sitée précède le premier filtre RVB nommé, une seule fois ; la chaîne sans nom est inchangée).

**Rien ne change pour les fichiers existants** : `RENDER_ENGINE_VERSION` reste **13**. Preuve : 60 étalonnages aléatoires (sept scalaires, quatre roues, activé ou non), chaîne `_build_color_grade_filters(grade)` et graphe complet d'un clip (avec ou sans effet, avec ou sans autre animation) relevés sur `origin/main` puis sur la branche : **identiques caractère pour caractère** (chaînes, graphes et empreintes de cache des clips sans image clé) ; `tests/test_grade_render.py` garde des chaînes relevées sur `origin/main` et vérifie que ni `sendcmd` ni fichier n'apparaissent sans image clé `color.*` rendue (ni pour `color.curves`, un nom que le registre ne connaît pas). Un fichier `.kut` sans image clé d'étalonnage est écrit octet pour octet comme avant (l'empreinte de `tests/test_effect_targets.py` est inchangée).

**Format 24** (`GRADE_ANIMATION_VERSION`) : écrite si un clip porte une image clé d'une des 23 propriétés (le registre décide, `is_grade_property`) ; elle l'emporte sur toutes les autres conditions (la 23 d'un flou animé comprise). Les 0.7.0 / 0.8.0 publiées ne connaissent pas ces cibles : sans la 24 elles auraient ouvert le fichier en rendant l'étalonnage à sa valeur de base, puis perdu les courbes à la sauvegarde suivante. Un nom `color.*` inconnu (`color.curves`) reste en 20 (tous les builds l'ignorent). Avant cette étape, **toute** image clé `color.*` écrivait déjà la 20 (réservation de l'étape 2) : un test qui attendait la 20 pour `color.exposure` attend maintenant la 24.

**Cycle de vie et verrous.** Les images clés d'étalonnage n'ont pas d'objet auquel elles seraient liées : coupe et duplication suivent le traitement générique de `Clip.animation` (testé : `effective_grade` aux mêmes valeurs avant et après la coupe, copie identique). Conversion en composition : **refusée** (`has_live_grade_animation`) avec le message « étalonnage animé que la composition ne sait pas encore rendre », comme les effets animés : la composition rend son étalonnage fixe. Piste verrouillée : l'API d'images clés (`keyframe_editing`, seul écrivain de cette étape) refuse, rien n'est modifié (testé : ajout, valeur, suppression) ; piste audio : refus du genre de piste. Suivi et stabilisation : l'état dérivé part de `list(clip.animation)` (`tracking_bindings.py`) et ne remplace que les courbes `mask.*` : testé avec une stabilisation en mode recadrage (qui réécrit les courbes du masque) jusqu'à la couche du plan de rendu et au graphe. Séquence imbriquée, Multicam : mêmes couches du plan (`_build_layer_filter`, `layer.animation`) ; le clip entier, un segment d'aperçu et l'arrêt sur image sont testés par la même base de temps que les effets de l'étape 5.

**Non fait / non vérifié.** Rien n'a été vu dans l'application réelle (ni losange, ni moniteur : étapes 7 et 8) ; le moniteur et l'aperçu rapide montrent l'étalonnage de base, seuls l'export et l'aperçu fidèle suivent la courbe. Les FFmpeg livrés avec l'application sous Windows et macOS, les entrées 10 bits, 8K et le décodage matériel ne sont pas testés. Les temps sous Docker (6.1, 7.1) sont ceux d'amd64 émulé sur arm64 : ils comparent animé et statique, pas les versions entre elles. Effacer ou réinitialiser l'étalonnage d'un clip (service de l'interface) ne retire pas ses images clés `color.*` : elles continuent d'animer le réglage neutre (comportement à décider avec l'interface, étape 8). Un clip dont l'étalonnage est découpé en plusieurs nœuds garde ses images clés, ignorées au rendu. Les écarts de pow / libm entre FFmpeg (celui de l'application) et Python (les tables) ne sont pas exclus sur une plateforme que je n'ai pas testée : le test qui compare les tables à `lutrgb` niveau par niveau échouerait.

---

## Correctif de la mémoire de `sendcmd` des effets (étape 5, 2026-10-21)

**Hypothèse de départ (fausse).** Les fenêtres de l'étape 6 devaient s'appliquer « telles quelles » aux effets : un fichier par fenêtre de 192 Ko, la mémoire rendue entre deux lectures. Le mécanisme a été factorisé (`core/effect_sendcmd.py` : `windows`, `load_filters`, `WINDOW_BYTES`, `MAX_COMMAND_BYTES` ; `core/grade_render.py` l'importe) puis **mesuré avant de conclure** : `tools/measure_sendcmd_memory.py`, flou en rampe linéaire (une valeur nouvelle à chaque image), 320x180 à 30 i/s, `-f null`, un processus par cas, pic de mémoire résidente de FFmpeg seul (Docker amd64 pour 6.1.1 et 7.1.5, un FFmpeg à la fois ; 9.0.2 en local).

| Durée (commandes) | 6.1.1 un fichier | 6.1.1 fenêtres | 7.1.5 un fichier | 7.1.5 fenêtres | 9.0.2 un fichier | 9.0.2 fenêtres |
|---|---|---|---|---|---|---|
| 1 min (3 600, 1 fenêtre) | 146 | 146 | 156 | 156 | 18 | 18 |
| 5 min (18 000, 4 fenêtres) | 506 | 496 | 514 | 517 | 28 | 29 |
| 10 min (36 000, 7 fenêtres) | 959 | 939 | 971 | 969 | 44 | 43 |

**Les fenêtres ne réduisent pas la mémoire des effets.** Sondes sur 7.1.5 (référence sans `sendcmd` : 69 Mo) : le pic est atteint à l'initialisation (`-frames:v 1` donne le même chiffre) ; il est proportionnel au nombre de **commandes**, ~25 Ko chacune (3 000 lignes de deux commandes : 216 Mo ; 3 000 lignes d'une : 144 Mo ; 6 000 lignes d'une : 228 Mo ; 6 000 lignes de deux : 368 Mo), conservé tant que le filtre vit (le même fichier de 3 000 lignes lu par cinq `sendcmd` : 818 Mo) ; 30 fenêtres de 100 lignes : 173 Mo contre 216 Mo pour une seule de 3 000. Ce que les fenêtres de l'étape 6 soignent est autre chose : le coût **transitoire** des lignes **longues** (800 lignes de 4 Ko : 7,1 Go en un fichier, 8 fichiers de 100 lignes : 224 Mo). Les 50 Ko « par instruction » relevés à l'étape 6 sont ce coût conservé par commande, pas un pic de lecture. La 9.0.2 n'est pas concernée (44 Mo à dix minutes).

**Décision (option 1 de l'analyse).** On garde la factorisation (les deux mécanismes partagent le code, sans doublon) et on **borne** le défaut au lieu de le prétendre résolu : `MAX_EFFECT_COMMANDS = 40 000` commandes par effet (≈ 1 Go de pic sur 6.1 / 7.1 ; ≈ 20 000 images pour un flou à deux commandes par valeur, plus de onze minutes à 30 i/s si la valeur change à chaque image ; une courbe qui se maintient en écrit bien moins), plus le plafond d'octets `MAX_COMMAND_BYTES` (32 Mo) en second garde-fou. Le plan est calculé en entier **avant** d'écrire un fichier ; au-delà, `ValueError` « Animation d'effet trop longue pour l'export : raccourcissez-la, gardez une valeur fixe sur une partie du clip ou découpez le clip. (Moins d'images clés ne serait pas un remède : la boucle écrit une commande à chaque image où la valeur interpolée change.) » (message sans donnée de l'utilisateur) : un refus, jamais un rendu faux. En dessous, le rendu est inchangé : sur 300 cas tirés au hasard (effet, images clés, cadence, origine, échelle), les chaînes et les fichiers de commandes sont identiques octet pour octet à ceux de `origin/main`, sauf deux plans de plus de 192 Ko dont le texte est simplement coupé en plusieurs fichiers (même contenu concaténé) ; `RENDER_ENGINE_VERSION` (13) et la version du format `.kut` ne bougent pas (les empreintes de cache se calculent sans fichier de commandes). Parité de rendu avec le statique, y compris plans coupés en plusieurs fenêtres (frontières comprises) : écart 0 sur FFmpeg 6.1.1, 7.1.5 et 9.0.2 (`tools/validate_fx_render.py`, cas `sendcmd_*_windows`).

**Piste non confirmée.** Une partie du coût ressemble à de la fragmentation de `malloc` : `MALLOC_ARENA_MAX=1 MALLOC_MMAP_THRESHOLD_=4096 MALLOC_TRIM_THRESHOLD_=0` ramène le cas de 3 000 lignes de 220 à 142 Mo sur 7.1.5 (glibc seulement ; non essayé sur 6.1.1 ; sans équivalent sur Windows et macOS, et ce serait une variable d'environnement posée au FFmpeg de l'utilisateur). **Évolution possible, non faite** : découper le rendu d'un clip en tranches de temps (plusieurs invocations de FFmpeg), seule voie qui borne vraiment la mémoire d'une animation arbitrairement longue ; c'est un changement d'architecture de l'export.

## Décisions prises (2026-10-18, avec l'utilisateur)

1. **Effets d'abord, puis étalonnage.** *Recommandation suivie.*
2. **`sendcmd` image par image pour les paramètres hors expression, après validation** sur FFmpeg 6.1 / 7.1 / 9.0.2. *Recommandation suivie.*
3. **Moniteur « fidèle d'abord »** pour l'étalonnage animé, comme la page Composition. *Recommandation suivie.*

## Limites de cet ADR

Lecture du code et essais du scout, puis **validation de l'étape 1** sur FFmpeg 9.0.2, 7.1.5 et 6.1.1 (les deux dernières en Docker amd64 émulé sur arm64). Non vérifié : les FFmpeg livrés avec l'application sous Windows et macOS, les entrées 10 bits et 8K, le décodage matériel, `curves` sur `red` / `green` / `blue`, plusieurs cibles dans une même ligne de commandes, la netteté de nœud (`convolution`) ; la cause exacte du coût de `lutrgb` et le coût sur une durée réelle (extrapolé de 150 images, pas mesuré sur 1 800). Pas de mesure de la cuisson d'une LUT (30 ms repris de la docstring). L'estimation en jours est grossière.
