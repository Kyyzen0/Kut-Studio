# Animation : moteur central d'images-clés

Une seule source de vérité anime tout Kut-Studio : l'inspecteur, la timeline,
l'éditeur de courbes, l'aperçu et l'export évaluent **les mêmes courbes avec
les mêmes coefficients**. Aujourd'hui, les cinq propriétés de transformation
des clips vidéo et graphiques sont animables (position X/Y, échelle, rotation,
opacité). L'architecture accepte n'importe quelle autre propriété (paramètres
d'effets, compositing, texte, audio, masques…) sans nouveau système.

## Pour l'utilisateur : animer en 4 gestes

1. Sélectionnez un clip dans la timeline.
2. Dans l'inspecteur (**Mouvement**), cliquez sur le **losange ◆** d'une
   propriété : l'animation est activée et une image-clé est posée à la tête
   de lecture, avec la valeur actuelle.
3. Déplacez la tête de lecture.
4. Modifiez la valeur : une nouvelle image-clé est créée automatiquement.

Ensuite :

- **‹ ›** à côté du losange : image-clé précédente / suivante ;
- **clic sur un losange plein** : retire l'image-clé sous la tête ;
- **clic droit sur le losange** : interpolation, copier / coller l'animation,
  désactiver l'animation (la valeur visible est conservée), ouvrir l'éditeur
  de courbes ;
- **timeline** : les images-clés apparaissent en bas du clip ; clic pour
  sélectionner (Maj pour en ajouter), glisser pour les déplacer (aimantation
  sur les images, la tête de lecture, les bords et les marqueurs), Suppr pour
  les effacer ;
- **éditeur de courbes** (Fenêtre → Éditeur de courbes, `Ctrl+Alt+G`) : outil
  avancé, jamais nécessaire pour une animation simple.

Raccourcis par défaut (modifiables dans Préférences → Raccourcis) : ajouter
une image-clé `Alt+K`, la supprimer `Alt+Maj+K`, précédente `Alt+J`, suivante
`Alt+L`, tout sélectionner `Ctrl+Alt+A`. Copier / coller et chaque
interpolation existent comme commandes sans raccourci par défaut. Sans
propriété active, une commande s'applique aux propriétés animées du clip (ou
à toutes si aucune ne l'est).

## Modèle

`core/animation.py` (pur, sans Qt ni FFmpeg) :

| Élément | Rôle |
| --- | --- |
| `Keyframe` | `property_name`, `time_seconds`, `value`, `interpolation` (segment **sortant**), `in_slope` / `out_slope` (Bézier, `None` = automatique), `tangent_mode` (`linked` / `broken`), `id` stable (non comparé) |
| `AnimationCurve` | keyframes triées et immuables d'une propriété ; segments résolus une fois ; évaluation `O(log n)` |
| `AnimatableProperty` | type de valeur (`float`, `int`, `bool`, `vec2`), défaut, bornes, pas, groupe |
| `ValueKind` | `vec2` est évalué composante par composante ; `bool` est toujours en maintien |

**Temps.** Secondes **locales au clip** (0 = début du clip sur la timeline),
normalisées à la microseconde : deux images-clés « au même instant » ont la
même clé quel que soit le calcul. Le moteur ne dépend pas du FPS ; l'interface
aligne les images-clés sur la grille d'images **de la timeline**
(`snap_local_time`), même quand un clip ne commence pas sur une image, à
24, 25, 29,97 ou 60 i/s.

**Règles d'évaluation.** Sans image-clé : la valeur statique. Avant la
première : sa valeur. Après la dernière : sa valeur. Entre deux : le segment.
Le résultat est borné par la propriété (opacité 0–1…), dans l'aperçu comme
dans l'export.

## Interpolations

Sur un segment `[t0, t1]`, avec `u = (t − t0)/(t1 − t0)` et `Δ = v1 − v0` :

| Type | Valeur | Usage |
| --- | --- | --- |
| `hold` | `v0` jusqu'à `t1` exclu | changement net |
| `linear` | `v0 + Δ·u` | mouvement constant |
| `ease_in` | `v0 + Δ·u²` | départ lent |
| `ease_out` | `v0 + Δ·(2u − u²)` | arrivée lente |
| `ease_in_out` | `v0 + Δ·(3u² − 2u³)` | départ et arrivée lents |
| `bezier` | Hermite cubique (pentes `out_slope` de k0, `in_slope` de k1) | contrôle fin |

Tout se réduit à un **polynôme cubique en `u`** (`AnimationCurve.segments`, un
polynôme par composante). Les
poignées de l'éditeur de courbes sont placées au tiers du segment ; leur pente
est la tangente. Tangentes **liées** : une seule pente des deux côtés (courbe
lisse) ; **séparées** : deux pentes ; **automatiques** : pente entre les
voisins (Catmull-Rom), nulle aux extrémités.

Conséquence : insérer une image-clé, ou couper / rogner un clip, **ne change
pas l'animation** (`inserted_preserving_shape`, `split`) : un cubique coupé
reste deux cubiques, représentés exactement par deux segments Bézier.

## Aperçu = export

- L'aperçu appelle `evaluate_transform` (courbes en cache, une évaluation par
  image) au **temps local du clip**.
- L'export traduit la même courbe par `core.animation_ffmpeg.curve_expression` :
  les mêmes coefficients, une somme de segments `gte(T,t0)*lt(T,t1)*(a+u*(b+u*(c+u*d)))`
  regroupée en arbre équilibré (l'évaluateur de FFmpeg limite la profondeur
  d'une expression ; la profondeur ne croît ici qu'en `log2 n`). Aucun
  échantillonnage : la valeur est exacte à chaque image, à tout FPS.
- Un graphe trop long pour la ligne de commande (> 20 000 caractères, Windows
  plafonnant à 32 767) passe par un fichier (`-/filter_complex` à partir de
  FFmpeg 7, `-filter_complex_script` avant).
- Les segments de l'aperçu fidèle utilisent le graphe de l'export.

Les tests vérifient l'identité image par image (évaluateur d'expressions
FFmpeg dans `tests/ffmpeg_expr.py`) et sur un export **réel** dont la
luminance suit la courbe.

## Format `.kut` (version 13)

*Cette section décrit les clés introduites en version 13 ; le format courant est la **version 16** (voir [architecture.md](architecture.md#format-kut)).*

Dans chaque clip, `transform_keyframes` :

```json
{"property_name": "opacity", "time_seconds": 1.0, "value": 0.5,
 "interpolation": "bezier", "tangent_mode": "broken", "id": "3f2a9c1b0e",
 "in_slope": -0.1, "out_slope": 0.3}
```

`in_slope` / `out_slope` ne sont écrits que s'ils sont fixés. Valeurs
inconnues ou corrompues : `linear`, pentes automatiques, `linked`, nouvel
identifiant.

**Rétrocompatibilité.** Les anciens fichiers s'ouvrent sans migration
destructive. L'ancien moteur gardait la valeur **de base** jusqu'à la
première image-clé puis sautait à sa valeur ; à l'ouverture, une image-clé
`hold` à 0 avec la valeur de base est ajoutée seulement quand c'est
nécessaire, ce qui reproduit exactement l'ancien rendu. Un projet sans
animation est inchangé.

## Rendre une nouvelle propriété animable

1. La décrire : `AnimatableProperty(id, label_key, kind, default, minimum, maximum, step, group)`.
2. L'enregistrer dans `core/animation_targets.py` avec ses accès :

   ```python
   register_target(PropertyTarget(
       spec=…,
       get_static=lambda clip: …, set_static=lambda clip, value: …,
       get_keyframes=lambda clip: …, set_keyframes=lambda clip, frames: …,
       make_keyframe=Keyframe,                    # ou une sous-classe qui valide
       applies_to=lambda track_type: track_type == "video",
   ))
   ```

   Les keyframes doivent être stockés dans un champ déjà sérialisé du clip
   (sinon, l'ajouter dans `core/project_io.py`).
3. Au rendu : `spec.evaluate(target.curve(clip), static, t)` pour l'aperçu,
   `curve_expression(curve, time_var=…, minimum=…, maximum=…)` pour FFmpeg.

Édition (`core/keyframe_editing.py`), undo, copier / coller, timeline,
raccourcis et éditeur de courbes fonctionnent alors sans autre code (exemple
complet : `test_a_new_property_becomes_animatable_by_registering_it`).

## Intégrer une propriété au Graph Editor

Rien à faire de plus : l'éditeur liste `targets_for(type_de_piste)` et passe
uniquement par `core.keyframe_editing`. Pour une propriété `vec2`, l'éditeur
affiche aujourd'hui la première composante (voir limites).

## Undo / redo

Chaque action enregistre **une** entrée d'historique (instantané du projet) :
ajout, suppression, déplacement (multiple), valeur, interpolation, tangentes,
collage. Les glisser continus (timeline, éditeur de courbes) modifient le
projet en direct mais n'enregistrent qu'au relâchement ; les saisies rapides
de l'inspecteur sont regroupées (~400 ms).

## Performances

`python -m tools.perf.animation_bench` (résultats : `docs/perf/animation.json`,
Apple Silicon) :

| Keyframes | Évaluer une valeur | 5 propriétés animées | Construire la courbe | Expression FFmpeg |
| --- | --- | --- | --- | --- |
| 10 | 0,75 µs | 8 µs | 10 µs | 26 µs |
| 100 | 0,79 µs | 16 µs | 118 µs | 0,3 ms |
| 1 000 | 0,83 µs | 92 µs | 1,2 ms | 2,9 ms |

L'évaluation d'une courbe ne parcourt jamais tous les keyframes (dichotomie).
Le surcoût de `evaluate_transform` à 1 000 keyframes vient de la validation
du cache (comparaison d'identités) et reste sous 0,1 ms par image.

## Animations générées (vidéo sociale)

Plusieurs commandes **écrivent des images-clés ordinaires** au lieu d'ajouter un moteur : ce qu'elles produisent se
retouche dans le Graph Editor et se rend partout pareil (détail : [social-video.md](social-video.md)).

| Commande | Module | Images-clés |
| --- | --- | --- |
| Pop-in, glissés, fondu montant, rebond | `core/text_animations.py` | `scale` en « back » (dépassement exprimé en `BEZIER`, pente 4,70158·Δ/durée), `position_*`, `opacity` |
| Mot par mot, machine à écrire, karaoké | `core/text_animations.py`, `core/word_timing.py` | `graphic.reveal` 0 → 1 ; `word_times` place chaque mot (karaoké calé sur la voix) |
| Zoom d'impact, secousse, flash | `core/impact_fx.py` | `scale` (1,08 → 1 en 0,3 s), bruit déterministe à 15 Hz sur position et rotation, `opacity` du flash |
| Ken Burns | `core/ken_burns.py` | `scale`, `position_x/y`, mouvement stable dérivé de l'identifiant du clip |
| Pan de recadrage | `pan_x`, `pan_y` (−1 … 1) | propriétés de transform animables ; `fill` est un réglage **statique** (`STATIC_TRANSFORM_PROPERTIES`, refusé par `TransformKeyframe`) |

Une échelle animée sous `rotate` rend sur une toile fixe, dimensionnée par la plus grande échelle du clip
(`max_transform_value`), aux côtés multiples de 4 : FFmpeg fixe la taille de sortie de `rotate` à la première image
(`RENDER_ENGINE_VERSION` 9).

## Paramètres d'effets : animer la correction de couleur et la vignette (ADR-0008, étapes 2 à 4)

### Pour l'utilisateur

Sont animables : la **luminosité, le contraste et la saturation** de la *Correction couleur*, et l'**intensité** de la
*Vignette*. Aucun autre réglage d'effet ne l'est encore : flou, netteté, lueur, aberration chromatique et brume de chaleur
restent fixes (pas de losange sur leurs réglages).

1. Sélectionnez un clip vidéo, onglet **Effets** de l'inspecteur, puis l'effet dans la liste.
2. Sous chaque réglage animable, la rangée **‹ ◆ ›** fonctionne comme celle de Mouvement : le losange pose (ou retire) une
   image clé à la tête de lecture, ‹ et › sautent à l'image clé précédente ou suivante.
3. Tant que le réglage n'a pas d'image clé, le curseur change sa valeur de base. Une fois animé, **le curseur montre la valeur
   évaluée à la tête de lecture** et la modifier écrit (ou met à jour) l'image clé de cet instant ; la valeur de base ne
   bouge plus. Chaque modification est une étape d'historique (annuler / rétablir).
4. Deux effets du même type (deux « Correction couleur ») ont des courbes séparées.
5. Une piste ou un calque **verrouillé** refuse toute modification, comme pour la transformation.

**Retirer l'animation.** Clic droit sur le losange : *Désactiver l'animation (garder la valeur actuelle)* retire la courbe de ce
réglage ; *Supprimer l'animation de l'effet* retire celles de tous les réglages de l'effet (une seule étape d'historique ; chaque
réglage garde la valeur qu'il montrait). Supprimer l'effet supprime aussi ses images clés. C'est ce geste qu'il faut pour
**convertir le clip en composition**, qui refuse un clip aux effets animés (la composition rend ses effets à leur valeur de base).

**Éditeur de courbes** (Avancé) : les réglages animables y sont listés sous « Correction couleur — Luminosité » (le second effet
du même type : « Correction couleur n° 2 — Luminosité »). Interpolation, copier / coller et ouverture de l'éditeur sont dans le
menu du losange, au niveau Avancé.

**Niveaux d'usage** : les rangées ‹ ◆ › sont du niveau **Studio** (cachées en Simple, sauf avec « Afficher plus d'options » qui
révèle le groupe Effets) ; le menu du losange n'offre que « Désactiver » et « Supprimer l'animation de l'effet » en Studio,
l'interpolation, le copier / coller et l'éditeur de courbes en Avancé. Changer de niveau ne perd aucune valeur.

**Format** : un fichier qui contient au moins une courbe d'effet s'écrit en version 20 et ne s'ouvre plus dans une version
antérieure de Kut-Studio ; sans courbe il reste en version 17 (inchangé).

### Modèle et rendu

Le modèle porte des images clés sur les paramètres des effets de clip et l'export comme le moniteur les rendent pour les
paramètres de la liste ci-dessous. Un projet existant s'affiche et s'exporte exactement comme
avant (le graphe FFmpeg d'un clip sans image clé `fx.*` est identique, caractère pour caractère). Ce qui existe :

- **Nom** : `fx.<identifiant de l'effet>.<paramètre>`, stocké dans `Clip.animation`. Le nom se découpe **par la droite** (le
  paramètre est ce qui suit le dernier point) : un identifiant d'effet peut contenir des points (`fx.my.blur.intensity` : effet
  `my.blur`). Aucun nom de paramètre ne contient de point (test de garde).
- **Résolution avec le clip** : `get_target(property_id, clip)`. Le même nom de paramètre a des bornes différentes selon le
  type de l'effet (`intensity` : flou 0 à 50, vignette 0 à 1, aberration 0 à 40) ; le clip fournit le type. Sans clip, un
  `fx.*` est une propriété inconnue (`KeyError`). Bornes et défaut sont lus dans `EFFECT_PARAMETER_SPECS`.
- **Liste enregistrée** (`ANIMATABLE_EFFECT_PARAMS`, `core/effect_targets.py`) : un paramètre n'y figure que si son rendu
  existe et tient son budget. Aujourd'hui : correction de couleur (luminosité, contraste, saturation) et vignette
  (intensité). **En attente** (`PENDING_EFFECT_PARAMS`, pas de cible, une image clé est ignorée) : flou (intensité), lueur
  (rayon) et aberration chromatique (intensité), prévus à l'étape 5 par `sendcmd` ; brume de chaleur (amplitude, fréquence,
  haut, étendue), dont le rendu par expression est écrit mais trop coûteux (voir « Coût de `geq` ») ; vitesse de la brume
  (saut de phase, voir plus bas). **Exclus** (`EXCLUDED_EFFECT_PARAMS`) : netteté, seuil et intensité de la lueur. Un test
  vérifie que chaque paramètre déclaré est dans exactement une de ces trois listes.
- **`targets_for_clip(clip, piste)`** ne les liste pas par défaut (`include_effects=False`) : les commandes d'animation sans
  propriété active (raccourcis) ne créent jamais de courbe d'effet. Le Graph Editor et le losange d'un réglage les passent à
  `include_effects=True` (`_graph_properties` de la fenêtre principale) ; une courbe d'effet active ou déjà animée est visée par
  les commandes comme n'importe quelle propriété.
- **Valeur effective** : `effective_effect_parameters(clip, effet, t)` (fonction pure) rend les paramètres de base remplacés par
  la courbe, bornés ; sans image clé, exactement les paramètres de base. `evaluated_effects(effets, animation, t)` en fait
  autant pour tous les effets d'un clip (un effet sans image clé est rendu tel quel, le même objet).
- **Cycle de vie** : une image clé d'effet ne survit pas à son effet. Toute écriture de `clip.effects` passe par
  `set_clip_effects` (suppression, préréglage, collage d'attributs, aplatissement multicam…), qui retire les images clés
  orphelines dans la même étape d'historique (l'annulation rend l'effet **et** ses images clés). Coller « Effets + Images clés »
  fait pointer les courbes vers les **nouveaux** effets. Coupe et duplication gardent les identifiants d'effets : les courbes
  suivent le traitement générique. Un fichier qui contient une image clé dont l'effet n'existe pas s'ouvre normalement : elle est
  ignorée, puis retirée à la prochaine modification de la chaîne d'effets du clip. Un test de garde
  (`tests/test_effects_write_guard.py`) échoue si une nouvelle écriture de `clip.effects` n'est pas classée.
- **Format** : un fichier qui contient au moins une image clé `fx.*` (ou `color.*`, réservé à l'étalonnage) s'écrit en version
  20 (`EFFECT_ANIMATION_VERSION`) ; un ancien Kut-Studio le refuse au lieu d'ignorer les images clés.

### Rendu (étape 3)

- **Export et aperçu fidèle** (même graphe) : `eq` (`brightness`, `contrast`, `saturation`, `eval=frame`) et `vignette`
  (`angle = intensité × π/4`, `eval=frame`) reçoivent l'expression de la courbe (`core/effect_expressions.py`, la même
  `curve_expression` que la transformation, bornée par `clip()` aux bornes du paramètre). Les paramètres non animés d'un
  même filtre restent des constantes.
- **Base de temps** : les images clés d'un effet sont en temps **local du clip**. Dans `_build_layer_filter`, `PTS` repart de
  zéro au début du clip (`setpts=PTS-STARTPTS` après `fps`, plus `+skip/TB` à l'origine d'un segment d'aperçu, ou
  `+retimed_skip/TB` pour un clip remappé) *avant* les effets ; `PTS+timeline_start` n'est posé qu'à la fin. `t` (`eq`,
  `vignette`) et `T` (`geq`) valent donc le temps local du clip, comme `t` pour l'échelle et la rotation. Testé : un clip
  entier et un segment qui démarre en milieu de clip (clip décalé sur la timeline et source rognée, changement de cadence
  24 vers 10 i/s, clip à vitesse ×2) donnent les mêmes images, et chacune égale le rendu statique à la valeur évaluée.
- **Moniteur GPU** : `evaluated_effects` est appelé à chaque image par les deux points de synchronisation (clip du dessus,
  pistes du dessous) ; `program_for` reçoit des paramètres déjà évalués. Les calques d'effets (`adjustment`) et les effets
  des calques graphiques ne portent pas d'image clé `fx.*` (les cibles n'existent que sur une piste vidéo) : inchangés.
- **Composition nodale** : elle ne reprend pas les courbes d'effet (`composition_render` appelle `_effect_chain` sans animation,
  l'effet serait rendu à sa valeur de base). `convert_to_composition` **refuse** donc un clip qui porte au moins une image clé
  `fx.*` dont l'effet existe (`has_live_effect_animation`, `core/effects_model.py`) : « a des effets animés que la composition
  ne sait pas encore rendre ». Une image clé orpheline ou mal formée ne compte pas. Rien n'est modifié en cas de refus.
- **Cache** : `RENDER_ENGINE_VERSION` 13 ; l'empreinte d'un segment contient `repr(animation)` : modifier une image clé `fx.*`
  invalide le segment, un clip sans image clé `fx.*` garde son empreinte (au numéro de moteur près).
- **Vérifié sur FFmpeg** 6.1.1 (Ubuntu 24.04), 7.1.5 (Debian trixie) et 9.0.2 (macOS) avec `tools/validate_fx_render.py` :
  l'image `n` de la sortie animée égale l'image `n` du rendu statique à la valeur évaluée (écart maximal 0 niveau). La
  référence de la vignette prend l'angle exact : l'export statique arrondit l'angle à six décimales, et un pixel sur une
  frontière d'arrondi bascule d'un niveau (1 octet sur 230 000, écart 2, disparu avec l'angle exact).
- **Écart d'un `eq` statique, hors animation** : à luminosité négative, la formule du moniteur (`eq_plane`) s'écarte déjà du
  `eq` de l'export (écart moyen 1,9 à −0,1 et 2,8 à −0,2, contre 0,4 à +0,1 ; mesuré sur des valeurs fixes). Ce n'est pas
  l'animation ; les tests de parité animée prennent une luminosité de 0 à 0,3.

### Brume de chaleur : pourquoi elle n'est pas enregistrée

- **Coût de `geq`** : `geq` évalue l'expression pour **chaque pixel de chaque plan** ; y mettre la courbe multiplie le coût.
  Mesuré sur 1280x720, 150 images, `-f null`, FFmpeg 9.0.2 (macOS, machine chargée : seul le rapport compte) : statique
  8,6 s ; animée avec 2 images clés sur les 4 paramètres 76,6 s (**×8,9**), avec 10 images clés 409,7 s (**×47,5**) ; un seul paramètre (amplitude) : 22,4 s à 2 images clés (×2,6), 80,1 s à 10 (×9,3) ; le critère de l'ADR (10 images clés à moins de
  ×2) est donc largement manqué. Le rendu (`heat_haze_shift`) est écrit et vérifié (parité export / moniteur, base de temps,
  statique) en enregistrant temporairement les paramètres dans les tests ; il n'est pas dans la liste.
- **Pistes** (non implémentées) : pré-évaluer la courbe de chaque paramètre par image (une constante par image dans un fichier
  de commandes `sendcmd`, sur le modèle de l'étape 5 : mais `geq` ignore `sendcmd`, voir `docs/ffmpeg-sendcmd.md`, donc
  découper en un `geq` par image ou régénérer l'expression constante par segment) ; ou limiter à N images clés ; ou sortir le
  décalage de lignes de `geq` vers un filtre qui accepte une carte de déplacement (`displace`) calculée une fois par image.
- **Phase de `speed`** : le décalage est `sin(Y·f + T·v)`. Remplacer `v` par `v(T)` fait *sauter* la phase (il faudrait
  `∫ v dt`) : `speed` reste statique. `frequency` animé fait glisser la phase de façon voulue (`Y·f(T)`), et l'export et le
  moniteur concordent (même formule, vérifiée aux seuils d'un effet statique).

## Limites connues

- Bézier : la tangente règle la **pente**, pas l'« influence » temporelle (la
  poignée reste au tiers du segment). C'est ce qui garde des segments
  polynomiaux exacts dans FFmpeg.
- Propriétés branchées : transform complet (y compris ancrage, échelle X/Y,
  inclinaison, miroirs), propriétés de forme et de texte (`graphic.*`) et
  masques (`mask.<id>.*`, une famille par masque) — voir
  `docs/motion-graphics.md`. L'audio a l'architecture mais pas encore ses cibles ;
  les couleurs ne sont pas animables. Les paramètres des effets sont animables, avec interface, pour la correction de couleur
  (luminosité, contraste, saturation) et la vignette (intensité) ; les autres réglages d'effets restent statiques (voir la
  section précédente).
- Éditeur de courbes : une propriété à la fois, première composante pour `vec2`.
- Un glisser dans l'éditeur de courbes rafraîchit timeline et aperçu à chaque
  mouvement ; sur de très gros projets il peut sembler moins fluide.
- Pas encore : tracking, expressions. Parentage, groupes et flou de mouvement
  sont en place (`docs/motion-graphics.md`) ; les séquences imbriquées aussi
  (`docs/nested-sequences.md`) : un clip imbriqué s'anime comme un autre clip,
  indépendamment des animations internes.
