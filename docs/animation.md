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

## Limites connues

- Bézier : la tangente règle la **pente**, pas l'« influence » temporelle (la
  poignée reste au tiers du segment). C'est ce qui garde des segments
  polynomiaux exacts dans FFmpeg.
- Propriétés branchées : transform complet (y compris ancrage, échelle X/Y,
  inclinaison, miroirs), propriétés de forme et de texte (`graphic.*`) et
  masques (`mask.<id>.*`, une famille par masque) — voir
  `docs/motion-graphics.md`. Effets et audio ont l'architecture mais pas encore
  leurs cibles ; les couleurs ne sont pas animables.
- Éditeur de courbes : une propriété à la fois, première composante pour `vec2`.
- Un glisser dans l'éditeur de courbes rafraîchit timeline et aperçu à chaque
  mouvement ; sur de très gros projets il peut sembler moins fluide.
- Pas encore : tracking, expressions. Parentage, groupes et flou de mouvement
  sont en place (`docs/motion-graphics.md`) ; les séquences imbriquées aussi
  (`docs/nested-sequences.md`) : un clip imbriqué s'anime comme un autre clip,
  indépendamment des animations internes.
