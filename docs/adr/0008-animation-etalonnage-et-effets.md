# ADR-0008 : animer l'étalonnage et les paramètres des effets de clip

**Statut :** Accepté le 2026-10-18 (trois décisions de cadrage prises avec l'utilisateur, voir « Décisions prises »). Aucune étape du plan n'est faite.
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

- **Par expression** quand le filtre le supporte (`eq` avec `eval=frame`, `hue`, `vignette`, `geq`) : `curve_expression` est inséré à la place de la constante.
- **Hors expression** : `sendcmd`, un fichier de commandes qui change l'option du filtre à chaque image. **Seulement après** l'étape de validation du plan (FFmpeg 6.1, 7.1 et 9.0.2, filtre par filtre). Un filtre qui échoue la validation voit ses paramètres **exclus de l'animation** : le registre ne les enregistre pas.
- Les décalages de temps (segments d'aperçu qui ne commencent pas à zéro, changement de cadence) sont la difficulté propre de `sendcmd` : ses instants sont relatifs au flux du filtre. Ils sont traités et testés à l'étape de validation, pas supposés.

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
1. **Validation de `sendcmd`** (aucun code produit) : pour chaque filtre hors expression (exposition `lutrgb`, température `colorchannelmixer`, `colorbalance`, `curves`, `gblur`, `unsharp`, `rgbashift`), un banc sur FFmpeg 6.1 (Ubuntu 24.04), 7.1 (Debian trixie) et 9.0.2, en Docker `--platform linux/amd64` : la sortie avec `sendcmd` égale-t-elle la sortie du filtre statique à la même valeur ? Comportement aux limites d'image, avec un segment qui ne commence pas à zéro, avec un changement de cadence, taille du fichier de commandes. Résultat consigné dans `docs/` et dans cet ADR (tableau filtre × version), avec la liste finale des paramètres animables.
2. **Cibles des effets + format + cycle de vie** : `fx.*` enregistrées (découpage par la droite, test de garde « aucun nom de paramètre ne contient de point »), constante de version 20 conditionnelle, `effective_effect_parameters`, **cycle de vie** (suppression, préréglage, collage avec table de correspondance des identifiants, coupe et duplication, orphelins tolérés puis nettoyés). Tests : aller-retour d'un fichier, ancien build refuse, identifiant à points, suppression puis annulation, préréglage, collage « Effets + Images clés » (les courbes animent les **nouveaux** effets), coupe d'un clip animé, image clé orpheline au chargement. Sans rendu encore.
3. **Rendu des effets** : par expression (`eq`, `vignette`, `geq`) à l'export, uniformes par image dans le moniteur, parité export / GPU sur des images échantillonnées, `RENDER_ENGINE_VERSION`.
4. **Interface des effets** : branche « image clé » dans le panneau Effets, losanges, niveau d'usage, manifeste d'énumération.
5. **Effets hors expression** (flou, netteté, aberration, lueur) via `sendcmd`, **si** l'étape 1 les valide.
6. **Cibles et rendu de l'étalonnage** : `color.*`, `effective_grade`, export par expression (`eq`, `hue`) puis `sendcmd` pour le reste, `RENDER_ENGINE_VERSION`.
7. **Moniteur fidèle d'abord** pour l'étalonnage animé, LUT cuite à l'arrêt avec les valeurs évaluées.
8. **Interface de l'étalonnage** : losanges dans le panneau Couleur.
9. **Mode script** : verbes `fx` avec sélecteur `effect`, `color.*` dans la liste dérivée, documentation, tableau des unités.

Estimation grossière (ordre de grandeur, non engagée) : effets 4 à 6 jours, étalonnage 8 à 12 jours, validation `sendcmd` incluse.

---

## Décisions prises (2026-10-18, avec l'utilisateur)

1. **Effets d'abord, puis étalonnage.** *Recommandation suivie.*
2. **`sendcmd` image par image pour les paramètres hors expression, après validation** sur FFmpeg 6.1 / 7.1 / 9.0.2. *Recommandation suivie.*
3. **Moniteur « fidèle d'abord »** pour l'étalonnage animé, comme la page Composition. *Recommandation suivie.*

## Limites de cet ADR

Lecture du code et essais FFmpeg 9.0.2 du scout seulement : aucun essai sur 6.1 ni 7.1, aucune mesure de coût de `sendcmd`, de la cuisson d'une LUT (30 ms repris de la docstring) ni du rendu d'une expression complexe. L'estimation en jours est grossière. Le comportement exact des filtres `unsharp`, `rgbashift`, `vignette` et `blend` avec `sendcmd` n'a pas été observé.
