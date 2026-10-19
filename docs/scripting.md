# Scripts déclaratifs

Un **script** est un fichier JSON qui décrit des modifications d'un projet : une liste d'opérations, chacune un verbe et ses
paramètres (ADR-0005, étape 4). Il ne contient aucun code : le décodeur ne connaît que la table des verbes ci-dessous, et chaque verbe
appelle une fonction de `core/` que l'interface appelle aussi. L'aperçu et l'export lisent le projet modifié comme toujours.

```json
{
  "kut_script": 1,
  "description": "Un bus voix compressé",
  "ops": [
    {"op": "add_bus", "as": "voix", "name": "Voix"},
    {"op": "route_track", "track": "A1", "bus": {"ref": "voix"}},
    {"op": "add_bus_insert", "bus": {"ref": "voix"}, "effect": "compressor", "params": {"ratio": 4}}
  ]
}
```

```bash
Kut-Studio validate script.json                          # le script seul : toutes ses erreurs d'un coup
Kut-Studio apply script.json projet.kut --dry-run        # le rapport, rien d'écrit
Kut-Studio apply script.json projet.kut -o resultat.kut  # écrit un autre fichier
Kut-Studio schema > kut-script.schema.json               # pour un éditeur ou un outil
Kut-Studio describe projet.kut --json                    # lire la structure d'un projet (voir « Lire un projet »)
```

## Règles

- **Strict.** `kut_script` (la version) est obligatoire ; une version plus récente que celle de l'application est refusée. Un champ
  inconnu est une **erreur** (avec une suggestion : « vouliez-vous dire `volume_db` ? »), jamais ignoré. Types, bornes (secondes de 0 à
  1 000 000, dB de −60 à 12 pour un volume, comme le modèle), tailles (1 Mio, 5 000 opérations) sont vérifiés. Toutes les erreurs sont rendues d'un coup (50 au plus).
- **Tout ou rien.** Si une opération est refusée, le projet n'est pas touché, et le rapport dit laquelle (numéro, verbe, message).
  Un script réussi est **une** étape d'historique. Les clips imbriqués sont recadrés comme dans l'interface.
- **Rien n'est évalué.** Pas d'expression, pas de variable, pas de calcul : une valeur est une donnée.
- **Identifiants et alias.** Un verbe qui crée un objet accepte `"id"` (identifiant imposé : lettres, chiffres et `_`, 64 au plus) et
  `"as"` (un alias, un nom du script). Un alias sert d'identifiant s'il n'y a pas d'`id` ; une opération suivante le cite par
  `{"ref": "voix"}`. Un alias inconnu, défini deux fois, ou du mauvais genre est une erreur. Rejouer un script donne le même projet,
  identifiant par identifiant. Sans `id` ni `as`, l'identifiant est généré (donc différent à chaque rejeu).
- **Sélecteurs.** Un objet existant se désigne par son identifiant, ou par son **nom exact s'il est unique** ; un nom ambigu est une erreur.
  Les clips et les inserts se désignent par identifiant seulement.
- **Fichiers en lecture seule, dans le dossier du script** (voir « Fichiers » plus bas).
- **Jamais de valeur ramenée en silence.** Les bornes d'un paramètre sont celles du modèle (volume −60 à 12 dB, opacité 0 à 1,
  exposition −2 à 2…) : au-delà, le script est refusé, il ne « réussit » pas en écrivant une autre valeur. Même règle pour le nom
  d'une piste déjà pris et pour un instant d'image clé hors du clip.
- **Le verrou d'une piste vaut pour un script**, comme pour l'interface : un verbe qui modifie un clip refuse une piste verrouillée **et un calque graphique verrouillé** (`graphic.locked`).
- **Pas d'écriture par défaut.** `apply` écrit dans un autre fichier (`-o`). Écrire dans le projet lui-même demande `--in-place`, et
  un `-o` qui désigne le projet (lien symbolique compris) est refusé. `--dry-run` travaille sur une copie.
- **Pas de verbe `export`** : exporter est une décision de l'appelant (`Kut-Studio export`).
- Les messages sont traduits (français, anglais, espagnol : `--lang`) ; les noms de verbes et de champs restent en anglais.

## Images clés : propriétés et unités

`add_keyframe` accepte les propriétés **du registre d'animation** (`core/animation_targets.py`) qui se posent avec un nombre : le transform et `graphic.*`.
Sont exclus : `flip_h` / `flip_v` (booléens ; la valeur de base se règle avec `set_transform`), `time.speed` (la durée du clip dépend de la courbe de vitesse ; voir les verbes de vitesse, « Vitesse et remappage ») et `mask.<id>.*` (verbes `add_mask_keyframe` /
`remove_mask_keyframe`). Les valeurs sont dans **l'unité du modèle**, pas celle que l'inspecteur affiche ; elles doivent rester dans les bornes
(sinon refus, jamais d'écrêtage). Un test (`tests/test_script_verbs_animation_masks.py`) relit ce tableau et le compare au registre.

<!-- units:keyframe -->
| Propriété | Unité | Défaut | Minimum | Maximum |
|---|---|---|---|---|
| `position_x` | fraction de la largeur du cadre (1 = un cadre) | 0 | -4 | 4 |
| `position_y` | fraction de la hauteur du cadre (1 = un cadre) | 0 | -4 | 4 |
| `scale` | facteur (1 = taille naturelle) | 1 | 0.05 | 10 |
| `rotation` | degrés | 0 | -3600 | 3600 |
| `opacity` | fraction (1 = opaque) | 1 | 0 | 1 |
| `anchor_x` | fraction de la largeur du calque (0.5 = centre) | 0.5 | -4 | 5 |
| `anchor_y` | fraction de la hauteur du calque (0.5 = centre) | 0.5 | -4 | 5 |
| `scale_x` | facteur, multiplie `scale` | 1 | 0 | 10 |
| `scale_y` | facteur, multiplie `scale` | 1 | 0 | 10 |
| `skew` | degrés (calques graphiques) | 0 | -85 | 85 |
| `pan_x` | −1 bord gauche … 1 bord droit (cadrage « remplir ») | 0 | -1 | 1 |
| `pan_y` | −1 bord haut … 1 bord bas (cadrage « remplir ») | 0 | -1 | 1 |
| `graphic.width` | pixels | 900 | 2 | 8192 |
| `graphic.height` | pixels | 180 | 2 | 8192 |
| `graphic.corner_radius` | pixels | 0 | 0 | 4096 |
| `graphic.stroke_width` | pixels | 0 | 0 | 256 |
| `graphic.font_size` | pixels | 64 | 6 | 512 |
| `graphic.tracking` | espacement des lettres, unité du modèle | 0 | -100 | 500 |
| `graphic.line_spacing` | facteur (1 = normal) | 1 | 0.3 | 5 |
| `graphic.shadow_offset_x` | pixels | 4 | -256 | 256 |
| `graphic.shadow_offset_y` | pixels | 4 | -256 | 256 |
| `graphic.shadow_blur` | pixels | 0 | 0 | 200 |
| `graphic.reveal` | fraction du texte révélé (1 = tout) | 1 | 0 | 1 |
| `graphic.light_speed` | facteur | 1 | 0 | 10 |
| `graphic.light_angle` | degrés | 0 | -360 | 360 |
| `graphic.light_density` | facteur | 1 | 0.1 | 4 |
| `graphic.glow_radius` | pixels | 0 | 0 | 400 |
| `graphic.glow_strength` | facteur | 1 | 0 | 4 |
<!-- /units -->

Un masque (`add_mask_keyframe`) :

<!-- units:mask -->
| Propriété | Unité | Défaut | Minimum | Maximum |
|---|---|---|---|---|
| `position_x` | fraction du calque (0.5 = centre) | 0.5 | -2 | 3 |
| `position_y` | fraction du calque (0.5 = centre) | 0.5 | -2 | 3 |
| `width` | fraction du calque | 0.5 | 0 | 4 |
| `height` | fraction du calque | 0.5 | 0 | 4 |
| `rotation` | degrés | 0 | -3600 | 3600 |
| `feather` | fraction (adoucissement du contour) | 0 | 0 | 1 |
| `expansion` | fraction (+ dilate, − contracte) | 0 | -1 | 1 |
| `opacity` | fraction (1 = opaque) | 1 | 0 | 1 |
<!-- /units -->

- **Hors de la première et de la dernière image clé, la valeur est tenue** (pas d'extrapolation). Entre deux images clés, l'interpolation du segment est celle de l'image clé de départ ; sans `interpolation`, un nouvel instant reprend celle du segment qui le contient, sinon `linear`.
- Une image clé au même instant remplace l'ancienne (on garde son identifiant) : rejouer un script donne le même projet.
- `value` est toujours obligatoire. `at` est relatif au clip et ne dépasse pas sa durée.
- La propriété doit exister pour le genre de clip (`skew` et `graphic.*` : calques graphiques ; `graphic.font_size` : textes seulement ; `pan_*` : vidéo seulement).
- Clip sur une piste verrouillée, calque verrouillé, piste audio ou de sous-titres : refus. La position (`position_x` / `position_y`) d'un masque lié à un suivi est refusée : elle vient du suivi.

## Les verbes

Cette liste est celle de `core/scripting/verbs.py` ; un test vérifie qu'elle ne s'en écarte pas.

| Verbe | Rôle |
| --- | --- |
| `add_bus` | Ajoute un bus audio à la séquence active (`name`) |
| `remove_bus` | Supprime un bus ; ses pistes retournent au Master (`bus`) |
| `rename_bus` | Renomme un bus (`bus`, `name`) |
| `set_bus_volume` | Règle le volume d'un bus (`bus`, `volume_db`) |
| `set_bus_muted` | Coupe ou rétablit un bus (`bus`, `muted`) |
| `route_track` | Envoie une piste sur un bus, ou au Master sans `bus` (`track`, `bus`) |
| `add_bus_insert` | Ajoute un effet en insert sur un bus (`bus`, `effect`, `params`, `enabled`) |
| `remove_bus_insert` | Retire un insert (`bus`, `insert`) |
| `set_bus_insert_enabled` | Active ou désactive un insert (`bus`, `insert`, `enabled`) |
| `update_bus_insert` | Modifie les paramètres d'un insert (`bus`, `insert`, `params`) |
| `import_media` | Importe un fichier média dans le projet, **en lecture seule** (`path`) ; voir « Fichiers » ci-dessous |
| `add_clip` | Pose un média **du projet** sur une piste (`track`, `asset`, `at`) |
| `delete_clip` | Supprime un clip (`clip`) |
| `move_clip` | Déplace un clip (`clip`, `at`) |
| `trim_clip_start` | Rogne le début d'un clip : il ne peut qu'avancer, la fin ne bouge pas (`clip`, `at` = nouveau début) |
| `trim_clip_end` | Rogne ou étend la fin d'un clip, sans dépasser sa source (`clip`, `at` = nouvelle fin) |
| `ripple_trim_clip_start` | Rogne le début d'un clip et fait reculer d'autant ceux qui le suivent sur sa piste (`clip`, `at`) |
| `cut_clip` | Coupe un clip en deux (`clip`, `at` strictement à l'intérieur) ; **crée la moitié droite** (`id` / `as`), la gauche garde l'identifiant |
| `duplicate_clip` | Duplique un clip sur sa piste, copie complète ; crée la copie (`clip`, `at` optionnel : sinon juste après la fin ; `id` / `as`) |
| `ripple_delete_clip` | Supprime un clip et fait reculer d'autant tout ce qui le suit, sur les pistes non verrouillées (`clip`) |
| `slip_clip` | Décale la fenêtre de source sans bouger le clip (`clip`, `delta` en secondes de source) ; hors de la source : refusé |
| `slide_clip` | Glisse un clip en ajustant le voisin d'avant et celui d'après (`clip`, `at`) |
| `roll_edit` | Déplace la coupe entre un clip et son voisin, durée totale inchangée (`clip`, `edge` = `left` ou `right`, `at`) ; sans voisin, c'est un trim |
| `move_clip_to_track` | Déplace un clip dans le temps et vers une autre piste de même genre (`clip`, `track`, `at`) |
| `set_clip_enabled` | Active ou désactive un clip (`clip`, `enabled`) |
| `add_effect` | Ajoute un effet visuel à la fin de la chaîne d'un clip **vidéo** (`clip`, `type` = `color_correction`, `blur`, `sharpen`, `vignette`, `black_and_white`, `sepia`, `glow`, `chromatic_aberration`, `heat_haze` ; `params` optionnel, `enabled`) ; `id` / `as` (local au clip) ; un effet à instance unique déjà présent est refusé |
| `remove_effect` | Retire un effet (`clip`, `effect`) |
| `set_effect_enabled` | Active ou désactive un effet (`clip`, `effect`, `enabled`) |
| `update_effect` | Modifie des paramètres d'un effet (`clip`, `effect`, `params`, au moins un) : noms et bornes du modèle, un nom inconnu ou une valeur hors bornes est refusé |
| `move_effect` | Déplace un effet dans la chaîne (`clip`, `effect`, `delta` non nul : −1 monte, 1 descend) ; un déplacement qui sortirait de la chaîne est refusé |
| `set_media_color_matrix` | Choisit comment lire les couleurs d'un média vidéo (`asset` ou `clip`, `matrix` : `auto`, `bt601` ou `bt709`) ; voir « Espace colorimétrique d'un média » ci-dessous |
| `add_track_insert` | Ajoute un effet en insert sur une piste **audio ou vidéo** (`track`, `effect`, `params`, `enabled`) ; un **égaliseur** est l'effet `parametric_eq` |
| `remove_track_insert` | Retire un insert de piste (`track`, `insert`) |
| `set_track_insert_enabled` | Active ou désactive un insert de piste (`track`, `insert`, `enabled`) |
| `update_track_insert` | Modifie les paramètres d'un insert de piste (`track`, `insert`, `params`) |
| `set_clip_gain` | Gain d'un clip d'une piste **audio** (`clip`, `gain_db` de −60 à 12) ; voir « Audio de clip » |
| `set_clip_pan` | Panoramique d'un clip d'une piste audio (`clip`, `pan` de −1 à 1) |
| `set_clip_fade_in` | Fondu d'entrée d'un clip audio (`clip`, `seconds`) ; plus long que la durée du clip moins le fondu de sortie : refusé |
| `set_clip_fade_out` | Fondu de sortie d'un clip audio (`clip`, `seconds`) ; plus long que la durée du clip moins le fondu d'entrée : refusé |
| `reset_clip_fades` | Retire les deux fondus d'un clip audio (`clip`) |
| `add_clip_audio_effect` | Ajoute un effet audio à la fin de la chaîne d'un clip **audio ou vidéo** (`clip`, `type` : les types des inserts, `params`, `enabled`) ; `id` / `as` |
| `remove_clip_audio_effect` | Retire un effet audio d'un clip (`clip`, `effect`) |
| `set_clip_audio_effect_enabled` | Active ou désactive un effet audio de clip (`clip`, `effect`, `enabled`) |
| `update_clip_audio_effect` | Modifie des paramètres d'un effet audio de clip (`clip`, `effect`, `params`, au moins un) : noms et bornes du modèle, rien n'est écrêté |
| `move_clip_audio_effect` | Déplace un effet audio dans la chaîne (`clip`, `effect`, `delta` non nul) ; un déplacement qui sortirait de la chaîne est refusé |
| `duck_music_under_voice` | Chaque piste audio de rôle « musique » baisse sous chaque piste de rôle « voix » (`preset` = `gentle`, `voice_over_music`, `social_punchy` ; défaut `voice_over_music`) ; refusé si une piste musique est verrouillée |
| `set_track_muted` | Coupe ou rétablit le son d'une piste (`track`, `muted`) |
| `set_track_locked` | Verrouille ou déverrouille une piste (`track`, `locked`) |
| `set_track_visible` | Affiche ou masque une piste (`track`, `visible`) |
| `add_image_layer` | Pose une **image fixe** du dossier du script en calque graphique (`path`, `at`, `duration` obligatoire, `track`) ; voir « Images » ci-dessous |
| `add_graphic` | Pose un calque graphique : `graphic` = `text`, `rectangle`, `solid`, `shape`, `adjustment` ou `null` (`at`, `duration`, `text`, `shape`, `track` : une piste de graphiques déverrouillée) |
| `set_graphic` | Modifie un calque : `text`, `fill_color`, `stroke_color` (`#RRGGBB` ou `#RRGGBBAA`), `stroke_width`, `font_size`, `width`, `height` (au moins un) ; refusé pour une propriété **animée** (`graphic.*` : une valeur statique serait masquée par la courbe), comme `set_mask` |
| `add_track` | Ajoute une piste : `type` = `video`, `audio` ou `subtitle` (`name` : doit être libre, sinon refus) |
| `remove_track` | Supprime une piste **vide** (`track`) |
| `rename_track` | Renomme une piste, son identifiant ne change pas (`track`, `name` libre) |
| `move_track` | Place une piste à un index de la liste (`track`, `index` de 0 au nombre de pistes moins un) ; une piste verrouillée ne bouge pas |
| `set_track_volume` | Règle le fader d'une piste **audio** déverrouillée (`track`, `volume_db` de −60 à 12) |
| `set_track_pan` | Règle le panoramique d'une piste **audio** déverrouillée (`track`, `pan` de −1 à 1) |
| `add_subtitle` | Pose un sous-titre sur une piste de sous-titres (`text`, `at`, `duration`, `track`) |
| `add_transition` | Transition entre deux clips vidéo adjacents d'une même piste (`from_clip`, `to_clip`, `transition`, `duration`) |
| `remove_transition` | Retire une transition (`transition`) |
| `set_clip_speed` | Vitesse constante d'un clip **vidéo ou audio** (`clip`, `speed` de 0,05 à 10) ; remplace une courbe de vitesse existante, comme l'inspecteur ; voir « Vitesse et remappage » |
| `set_clip_reverse` | Joue un clip à l'envers ou à l'endroit (`clip`, `reverse`) |
| `set_clip_freeze_frame` | Transforme un clip vidéo en arrêt sur image (`clip`, `source_time` dans la fenêtre de source du clip, `duration` de 0,01 à 3 600 s) |
| `remove_clip_freeze_frame` | Lève l'arrêt sur image (`clip`) : tout le remappage revient à la normale ; refusé si le clip n'est pas figé |
| `set_clip_freeze_duration` | Change la durée d'un arrêt sur image (`clip`, `duration` de 0,01 à 3 600 s) ; refusé si le clip n'est pas figé |
| `reset_clip_time_remapping` | Remet vitesse, sens, courbe et arrêt sur image à la normale (`clip`) |
| `add_speed_point` | Pose un point de la courbe de vitesse (`clip`, `at` relatif au clip, `value` de −10 à 10 ou absent = vitesse actuelle, `interpolation`, `ripple`) |
| `set_speed_point_value` | Change la vitesse d'un point, désigné par son instant (`clip`, `at`, `value`, `ripple`) |
| `remove_speed_point` | Retire un point, désigné par son instant (`clip`, `at`, `ripple`) |
| `move_speed_points` | Déplace des points d'un bloc (`clip`, `points` = leurs instants, 1 à 64, `delta` non nul, `ripple`) ; un point qui sortirait du clip ou en recouvrirait un autre est refusé |
| `clear_speed_curve` | Supprime la courbe de vitesse : vitesse constante, portion de média conservée (`clip`) |
| `insert_hold` | Arrêt sur image dans la courbe : l'image reste `duration` s (0,01 à 3 600, défaut 1) à l'instant `at` (`clip`, `ripple`) ; `at` doit tomber sur une image de la timeline |
| `set_clip_interpolation` | Images intermédiaires d'un clip ralenti (`clip`, `interpolation` = `sampling`, `blending`, `optical_flow`, et/ou `quality` = `auto`, `draft`, `balanced`, `best`) |
| `set_clip_preserve_pitch` | L'audio garde sa hauteur quand la vitesse change (`clip`, `enabled`) |
| `set_clip_remap_audio` | Le remappage touche la vidéo et l'audio, ou la vidéo seule (`clip`, `enabled`) |
| `set_transform` | Valeur de **base** du transform d'un clip vidéo ou d'un calque (`clip`, au moins un de `position_x`, `position_y`, `scale`, `rotation`, `opacity`, `anchor_x`, `anchor_y`, `scale_x`, `scale_y`, `skew`, `flip_h`, `flip_v`, `fill`, `pan_x`, `pan_y`) ; hors bornes ou propriété animée : refusé ; voir « Transform » |
| `reset_transform` | Remet le transform à l'identité et retire toutes ses images clés (`clip`) |
| `add_keyframe` | Pose ou remplace une image clé (`clip`, `property`, `at`, `value` obligatoire, `interpolation`) : `at` est **relatif au clip**. `property` : une propriété du transform (dont `pan_x`, `pan_y`) ou `graphic.*`, voir « Images clés : propriétés et unités » |
| `remove_keyframe` | Retire l'image clé d'une propriété à un instant (`clip`, `property`, `at`) |
| `add_mask_keyframe` | Pose ou remplace une image clé d'une propriété d'un masque de compositing (`clip`, `mask`, `property` = `position_x`…`opacity`, `at` relatif au clip, `value`, `interpolation`) ; refusé pour le recadrage, une fenêtre d'étalonnage, et la position d'un masque suivi |
| `remove_mask_keyframe` | Retire l'image clé d'une propriété d'un masque à un instant (`clip`, `mask`, `property`, `at`) |
| `set_color_grade` | Règle l'étalonnage d'un nœud : `exposure`, `contrast`, `saturation`, `temperature`, `hue`, `shadows`, `highlights`, `enabled` (au moins un) ; `node` optionnel |
| `reset_color_grade` | Remet l'étalonnage d'un nœud à neutre (`clip`, `node`) |
| `set_color_curve` | Règle la sortie d'un point de courbe (`channel`, `index`, `output`) |
| `reset_color_curve` | Remet une courbe à l'identité (`channel`) |
| `set_color_wheel` | Règle une roue primaire (`wheel` = lift/gamma/gain/offset, `r`/`g`/`b`/`y`, au moins une) |
| `import_lut` | Attache un LUT `.cube` en lecture seule dans le dossier du script (`path`) ; voir « Fichiers » |
| `remove_lut` | Retire le LUT d'un nœud |
| `add_color_node` | Ajoute un nœud en série (`after` optionnel, `label`) |
| `branch_color_node` | Ajoute un nœud en parallèle ou en calque (`node`, `kind` = parallel/layer) |
| `remove_color_node` | Supprime un nœud (pas le dernier) |
| `rename_color_node` | Nomme un nœud (`label`) |
| `set_color_detail` | Flou et/ou netteté d'un nœud (`blur` 0…50, `sharpen` 0…5) |
| `add_color_window` | Ajoute une fenêtre sur un nœud (`shape` = rectangle/ellipse/polygon/path, géométrie, `mode`, `node` optionnel) ; pour `path` (courbe de Bézier fermée), `nodes` : 3 à 128 objets `x`, `y` (ancre, ±2) et `in_dx`, `in_dy`, `out_dx`, `out_dy` (tangentes relatives à l'ancre, ±2, 0 si absentes), en fractions de la boîte de la fenêtre ; sans `nodes`, le cercle par défaut |
| `set_color_window` | Modifie une fenêtre (`window`, au moins un champ) |
| `set_color_window_points` | Remplace tous les nœuds d'une fenêtre `path` (`window`, `nodes` comme ci-dessus) ; refusé pour une autre forme, hors bornes ou piste / calque verrouillé. Un projet qui contient une courbe s'enregistre en version 18 |
| `remove_color_window` | Retire une fenêtre (`window`) |
| `set_color_qualifier` | Règle le qualifieur TSL d'un nœud (au moins un champ) |
| `clear_color_qualifier` | Retire le qualifieur d'un nœud |
| `set_crop` | Recadre un clip vidéo ou un calque : masque « Recadrage » `intersect` (`clip`, au moins un de `left`, `top`, `right`, `bottom`, fractions 0…0,95 ; `left + right` et `top + bottom` au plus 0,95 ; les absents gardent leur valeur ; hors bornes refusé) |
| `clear_crop` | Retire le recadrage d'un clip (`clip`) ; les autres masques restent |
| `add_mask` | Ajoute un masque de compositing à la pile d'un clip vidéo ou d'un calque (`clip`, `shape` = rectangle/ellipse/polygon/path, `mode`, `inverted`, `name`, `position_x`, `position_y`, `width`, `height`, `rotation`, `feather`, `expansion`, `opacity`) ; mêmes valeurs par défaut que le bouton « Ajouter » de l'inspecteur Compositing ; `points` (polygone : 3 à 128 sommets `x`, `y`, ±2) ou `nodes` (courbe, comme `add_color_window`) selon la forme ; rangé avant le recadrage. `id` / `as` ; `crop` et `trackcrop` sont réservés |
| `set_mask` | Modifie un masque (`clip`, `mask`, au moins un de `mode`, `inverted`, `name`, `position_x`, `position_y`, `width`, `height`, `rotation`, `feather`, `expansion`, `opacity`) ; la forme ne change pas ; refusé pour une propriété animée et pour le recadrage |
| `set_mask_points` | Remplace les sommets d'un polygone (`points`) ou les nœuds d'une courbe (`nodes`), un seul des deux selon la forme du masque (`clip`, `mask`) |
| `remove_mask` | Retire un masque (`clip`, `mask`) avec ses images-clés `mask.<id>.*` et ses liaisons de suivi ; pas le recadrage (`clear_crop`) |
| `add_marker` | Ajoute un repère (`at`, `name`, `category`) |
| `set_cover_marker` | Pose le repère de couverture, le seul : l'ancien est remplacé (`at`, `name`) ; `id` / `as` |
| `remove_marker` | Retire un repère (`marker`, par son identifiant) |
| `create_sequence` | Crée une séquence vide, **non activée** (`name` libre, `width`, `height` pairs 16…8192, `fps` 1…240 ; sinon ceux de la séquence active) |
| `rename_sequence` | Renomme une séquence (`sequence`, `name` libre) |
| `set_sequence_format` | Change le cadre et/ou la cadence d'une séquence (`sequence`, au moins un de `width`, `height`, `fps`) |
| `duplicate_sequence` | Copie indépendante d'une séquence, non activée (`sequence`, `name` libre optionnel) ; les clips de la copie ont des identifiants générés |
| `insert_sequence_clip` | Insère une séquence comme clip imbriqué sur une piste vidéo ou audio **de la séquence active** (`sequence`, `track`, `at`, `source_in`, `source_out` dans la durée de la source) |
| `set_active_sequence` | Rend une séquence active : les verbes de piste, clip, bus et repère agissent ensuite sur elle (`sequence`) |
| `delete_sequence` | Supprime une séquence ni active, ni dernière, ni imbriquée ailleurs (`sequence`) |
| `create_multicam_source` | Crée une source Multicam, **non activée** : `angles` (1 à 64 objets `asset`, `offset` ≥ 0 en secondes, `name`), `name` libre optionnel ; le plus petit `offset` vaut 0 |
| `insert_multicam_clip` | Insère une source Multicam comme segment sur une piste vidéo ou audio **de la séquence active** (`sequence`, `track`, `at`, `angle` optionnel, `source_in`, `source_out`) |
| `switch_multicam_angle` | Montre un angle à partir d'un instant : coupe le segment de la séquence active et change l'angle de la moitié droite (`at`, `angle`, `clip` et `track` optionnels) |
| `replace_multicam_angle` | Change l'angle d'un segment, sans coupe (`clip`, `angle`) |
| `set_multicam_audio` | Politique audio d'une source : `mode` = `follow` / `fixed` / `mix`, `angles` selon le mode (`sequence`) |
| `rename_multicam_angle` | Renomme un angle et sa piste, nom libre dans la source (`sequence`, `angle`, `name`) |
| `add_multicam_angle` | Ajoute un angle à une source (`sequence`, `asset`, `offset`, `name`) |
| `set_multicam_angle_offset` | Recale un angle : son premier clip commence à `offset` (`sequence`, `angle`, `offset`) |

### Séquences et séquences imbriquées

Un projet a plusieurs séquences ; les verbes de timeline (pistes, clips, bus, repères, calques…) agissent sur la séquence **active**. Les
verbes de séquence ci-dessus désignent leur séquence par un sélecteur (identifiant, nom exact unique ou `{"ref": alias}`), sans la rendre
active. Pour remplir une autre séquence, un script la rend active avec `set_active_sequence`, puis revient :

```json
{"op": "create_sequence", "as": "intro", "name": "Intro"},
{"op": "set_active_sequence", "sequence": {"ref": "intro"}},
{"op": "add_clip", "track": "V1", "asset": "m", "at": 0},
{"op": "set_active_sequence", "sequence": "Séquence principale"},
{"op": "insert_sequence_clip", "sequence": {"ref": "intro"}, "track": "V1", "at": 0}
```

- **La séquence active est une donnée du projet** (enregistrée dans le `.kut`, restaurée par un refus, conservée par un « annuler » tant
  qu'elle existe : règle de l'historique de l'interface). Un script **finit là où il a laissé la séquence active** : rien ne la remet en
  place à sa place. `--dry-run` travaille sur une copie, le projet d'origine garde la sienne.
- Cycle (direct ou indirect), profondeur maximale (32 niveaux), piste verrouillée, piste d'un autre genre (une séquence ne va que sur
  une piste vidéo ou audio) et source vide sont refusés avec le message de l'interface. Un `source_out` au-delà de la durée de la source
  est refusé (il serait sinon ramené en silence) ; si un verbe raccourcit ensuite une séquence imbriquée, le recadrage des clips
  qui la contiennent est le même que dans l'interface et figure dans le rapport.
- Un nom de séquence **déjà pris est refusé** (le modèle numéroterait « Nom 2 » en silence) ; sans `name`, le modèle choisit.
- `delete_sequence` n'a pas de `force` : une séquence encore utilisée, la séquence active et la dernière sont refusées.
- Pas encore de verbe pour « créer une séquence à partir de la sélection » (la sélection est un état d'interface).

### Multicam

Une **source Multicam** est une séquence dont chaque angle a sa piste ; un **segment** est un clip imbriqué qui montre un angle (voir
[docs/multicam.md](multicam.md)). La source se désigne comme une séquence (identifiant, nom exact unique, `{"ref": alias}`) ; les segments
et leurs coupes sont ceux de la séquence **active**.

```json
{"op": "create_multicam_source", "as": "concert", "name": "Concert", "angles": [
  {"asset": "cam_a", "name": "Large"}, {"asset": "cam_b", "name": "Serré", "offset": 0.5}, {"asset": "enregistreur", "name": "Son"}]},
{"op": "insert_multicam_clip", "sequence": {"ref": "concert"}, "track": "V1", "at": 0, "angle": "Large", "as": "plan1"},
{"op": "switch_multicam_angle", "at": 12.5, "angle": "Serré", "as": "plan2"},
{"op": "set_multicam_audio", "sequence": {"ref": "concert"}, "mode": "fixed", "angles": ["Son"]}
```

- **Désigner un angle** : par son identifiant `angle-N` (dans l'ordre de `angles` à la création : `angle-1`, `angle-2`…, puis le plus grand
  existant + 1 pour `add_multicam_angle`) ou par son nom exact, unique dans la source (ambigu = erreur). Un script peut donc viser les angles
  qu'il vient de créer sans rien lire. Un nom d'angle choisi par un script doit être libre dans la source et ne pas ressembler à un
  identifiant `angle-N` d'un autre angle ; sans `name`, le nom du média (que le modèle suffixe d'un numéro s'il est déjà pris).
- **Décalages** : `offset` est la position de départ de l'angle dans la source. Les décalages d'une création sont relatifs (le modèle
  ramènerait le plus petit à 0) : un script dont le plus petit n'est pas 0 est **refusé**, pas décalé. Sans mesure, les angles sont marqués
  synchronisés « à la main ». La source reprend le cadre et la cadence de la séquence active ; la politique audio proposée par le modèle
  s'applique (un enregistreur seul donne un son fixe) jusqu'à `set_multicam_audio`.
- **`switch_multicam_angle`** coupe le segment sous `at` (piste vidéo la plus haute par défaut, ou `clip` / `track`) : les transitions du
  segment coupé sont retirées, comme pour toute coupe. `id` / `as` nomment la moitié droite et sont **refusés sans coupe** (début du
  segment, angle déjà montré) ; sans `id`, l'identifiant est celui du modèle (`<clip>-split-N`). Un angle déjà montré n'est pas une
  erreur sans `id` : rien ne change et le segment est rendu.
- **Verrou de piste** : `switch_multicam_angle` et `replace_multicam_angle` le vérifient dans le verbe (le modèle ne le contrôle que lors d'une
  coupe) ; `set_multicam_angle_offset` aussi pour la piste de l'angle.
- `set_multicam_audio` : `follow` ne prend aucun angle, `fixed` exactement un, `mix` au moins un, sans doublon, chacun portant du son.
- Pas de Multicam dans une Multicam, cycles et profondeur : refusés par le contrôle d'imbrication, comme `insert_sequence_clip`.
- **Pas de verbe** pour la synchronisation automatique (audio, timecode : analyses), la création depuis des clips sélectionnés (la
  sélection est un état d'interface), l'aplatissement, la couleur d'un angle (rang de palette du thème) ni la suppression d'un angle.

`params` d'un insert : un dictionnaire plat (nombres, booléens, textes courts) dont **le modèle** valide les noms et les bornes ; un
paramètre inconnu est refusé avec le message du modèle. Le schéma complet (`Kut-Studio schema`) liste les champs de chaque verbe.

## Masques de compositing : `add_mask`, `set_mask`, `set_mask_points`, `remove_mask`

Ces verbes écrivent la pile de masques d'un clip (`Clip.compositing.masks`) exactement comme l'inspecteur **Compositing** et l'éditeur de
masques du moniteur : le résultat est le même `Mask`, la matte rendue la même octet pour octet, et un script s'annule en une étape.

```json
{"op": "add_mask", "clip": "a", "shape": "ellipse", "as": "vignette", "feather": 0.3, "inverted": true},
{"op": "add_mask", "clip": "a", "shape": "polygon", "points": [{"x": 0, "y": -0.5}, {"x": 0.5, "y": 0.4}, {"x": -0.5, "y": 0.4}]},
{"op": "set_mask", "clip": "a", "mask": {"ref": "vignette"}, "opacity": 0.8},
{"op": "remove_mask", "clip": "a", "mask": {"ref": "vignette"}}
```

- Les valeurs sont **refusées, jamais ramenées** : nombres finis (ni booléen ni NaN) dans les bornes du modèle, `points` / `nodes` de 3 à 128 éléments
  (la taille est contrôlée avant la lecture), forme et énumérations connues, identifiant libre (masques et fenêtres d'étalonnage du clip partagent
  les identifiants de leurs images-clés `mask.<id>.*`).
- Un défaut est celui de l'inspecteur (largeur et hauteur 0,5, adoucissement 0), pas celui de `add_color_window`.
- Le masque `crop` est celui du recadrage : `set_crop` / `clear_crop` ; ces verbes le refusent, comme `trackcrop` (stabilisation). Les fenêtres
  d'étalonnage ont leurs verbes `*_color_window`.
- Pas de verbe d'ordre : l'interface n'en a pas non plus (un masque ajouté va en bout de pile, avant le recadrage).
- Une propriété **animée** (images-clés `mask.<id>.<nom>`) n'est pas modifiée par `set_mask` : une valeur statique serait masquée par la courbe
  et un script n'a pas de tête de lecture où poser une image-clé. Les images-clés d'un masque se posent avec `add_mask_keyframe` / `remove_mask_keyframe` (`add_keyframe` n'accepte pas `mask.<id>.*`).
- Clip sur une piste verrouillée, calque verrouillé, piste audio ou de sous-titres : refus, comme dans l'interface.
- Un projet qui contient une courbe s'enregistre en version 18 du format `.kut`, les autres en 17.

## Verrou du projet

Un projet ouvert dans la fenêtre ne se fait pas réécrire par un script. L'application pose, tant qu'un projet est ouvert, un petit
fichier voisin `nom.kut.lock` (comme `nom.kut.autosave`) qui dit qui le tient : numéro et heure de début du processus, machine, genre
(`app` ou `cli`). `apply` le consulte avant de lire le projet et **refuse d'écrire** dans un projet tenu par un autre :

```text
Projet verrouillé : il est utilisé par l'application (processus 4242 sur mon-poste, depuis 2026-10-13 09:12:03). Rien n'a été
modifié. Fermez-le, ou, si cette utilisation n'existe plus, supprimez le fichier /…/demo.kut.lock.
```

- **Ce que ça protège** : `--in-place`, ou `-o` vers un fichier existant avec `--overwrite`. `--dry-run` et l'écriture d'un **nouveau**
  fichier ne sont pas concernés (lire un projet tenu par la fenêtre est permis). `--json` rend `error.kind = "project_locked"`, avec
  le propriétaire et le fichier de verrou.
- **Un verrou n'est repris que si son propriétaire est prouvé disparu** : processus absent, ou numéro réutilisé par un autre processus
  (l'heure de début diffère). Le CLI le dit (« Verrou périmé remplacé »). Autre machine (dossier partagé), identité illisible, fichier
  abîmé mais récent : le verrou **tient** ; supprimez le fichier à la main si vous êtes sûr de votre fait.
- **Pris avant toute lecture.** Le CLI prend le verrou avant de lire le projet, la fenêtre avant de le charger (y compris avant la boîte
  de récupération de l'autosave) : sinon une écriture arrivée entre la lecture et l'installation serait écrasée par une version périmée.
- **Reprise sans entrecroisement.** Reprendre un verrou périmé passe par une garde (`nom.kut.lock.reclaim`, quelques instants) : un seul
  processus reprend à la fois et chacun relit sous la garde, si bien qu'un processus suspendu ne peut pas retirer le verrou vivant d'un
  autre. Si une garde traîne (plantage pendant la reprise), le refus désigne ce fichier à supprimer.
- **Un projet ouvert par un lien symbolique** partage le verrou de son référent, et l'enregistrement écrit **à travers** le lien (il
  restait auparavant remplacé par un fichier ordinaire).
- **L'application prévient, ne refuse jamais** : si le projet est déjà tenu ailleurs, il s'ouvre quand même, avec un avertissement
  (enregistrer depuis deux endroits fait perdre des modifications). Elle relâche le verrou en changeant de projet et à la fermeture.
- **Consultatif** : c'est un accord entre les programmes de Kut-Studio. Il ne protège pas d'un éditeur de texte, ni d'une copie du
  fichier qui l'ignorerait.

## Espace colorimétrique d'un média : `set_media_color_matrix`

Un flux vidéo **non étiqueté** est lu en BT.709 dès 720 lignes ou 1280 colonnes, en BT.601 en dessous ; un flux **étiqueté** garde son étiquette
(`docs/color.md`). `set_media_color_matrix` force cette lecture pour **un média** : `{"op": "set_media_color_matrix", "asset": "plan1", "matrix": "bt601"}`.

- `matrix` : `auto` (la règle), `bt601` ou `bt709` ; toute autre valeur (casse comprise) est refusée, jamais ramenée.
- La cible est un média (`asset`) ou le média d'un clip (`clip`) : exactement un des deux. Le réglage vaut pour **tous** les clips de ce média, dans toutes
  les séquences.
- Il est refusé si le média n'est pas une vidéo (son, image fixe, sous-titres) ou si l'un de ses clips est sur une **piste verrouillée** ou est un
  calque verrouillé : le même refus que l'interface (menu du média dans la bibliothèque).
- Une transaction s'annule en une étape. Un projet dont tous les médias sont en `auto` s'écrit exactement comme avant (version 17) ; dès qu'un média
  a un autre choix, le fichier passe en **version 19**, qu'un Kut-Studio plus ancien refuse au lieu de l'ouvrir en changeant les couleurs sans le dire.

## Fichiers : `import_media`

C'est le **seul** verbe qui lit un fichier, et il le lit en lecture seule (il sonde le média avec `ffprobe`, il n'écrit rien).

- Le chemin se résout **relativement au dossier du script**. Un chemin qui en sort (`../autre.wav`, un chemin absolu ailleurs, un lien
  symbolique qui s'échappe, vérifié après résolution du lien) est **refusé** : c'est à l'appelant de l'autoriser avec
  `Kut-Studio apply … --allow-outside`, jamais au script.
- Le fichier doit exister et être un fichier ordinaire ; un média déjà dans le projet (même chemin) est refusé, pas dupliqué.
- Le média reçoit l'identifiant `id` (ou l'alias `as`) : `{"op": "import_media", "path": "voix.wav", "as": "voix"}` puis
  `{"op": "add_clip", "track": "A1", "asset": {"ref": "voix"}, "at": 0}`.
- Le texte d'un chemin ne contient pas de caractère de contrôle et tient en 1 024 caractères.

## Images fixes : `add_image_layer`

Lit une image en lecture seule, comme `import_media`, et la pose comme **calque graphique** (le même que « Importer une image » dans
l'interface), avec une durée que le script donne (`duration`, de 0,04 s à 1 000 000 s, obligatoire).

- Mêmes règles de chemin que `import_media` (dossier du script, `--allow-outside` décidé par l'appelant, liens symboliques vérifiés
  après résolution) ; PNG, JPEG, WebP, BMP et TIFF.
- L'**en-tête seul** est lu avant d'accepter l'image (pas de décodage complet) : le **contenu** décide du format et doit concorder avec
  l'extension. Sont refusés : fichier vide, corrompu ou d'un autre format, extension qui ne correspond pas au contenu, fichier de plus de
  256 Mo, image de plus de 8 192 pixels de côté ou de 64 millions de pixels. Un fichier dont l'en-tête est sain mais dont la suite est
  tronquée n'est détecté qu'au rendu.
- Le calque tient dans le cadre du projet sans être agrandi (comme l'import par l'interface sans « remplir ») ; `set_graphic` (`width`,
  `height`) le redimensionne, et position, échelle, images clés, effets et étalonnage s'appliquent comme à un autre calque.
- `track` doit être une piste de **graphiques déverrouillée** (une piste vidéo, audio ou verrouillée est refusée).
- **Limite** : c'est un calque graphique, donc **au-dessus de toute la vidéo** ; il ne participe pas aux transitions entre clips vidéo ni
  au remappage de temps. Poser une image comme clip d'une piste vidéo viendra plus tard.

## Montage et pistes

Ces verbes appellent les fonctions de montage que l'interface appelle (`core/timeline_operations.py`, `core/timeline_editing.py`,
`core/track_operations.py`) : le résultat est celui du geste correspondant. Les verbes de ce domaine sont dans `core/scripting/edit_verbs.py`.

- **Couper puis citer** : `cut_clip` crée la moitié droite ; avec `as`, les opérations suivantes la désignent par `{"ref": alias}`. Sans `id`
  ni `as`, le modèle la nomme `<clip>-split-N`.
- **Rien n'est ramené en silence** : le modèle bornerait un `slip_clip` qui sort de la source et un `move_track` hors de la liste ; un script
  les reçoit refusés. Les rognages, `slide_clip` et `roll_edit` refusent déjà d'eux-mêmes ce qui sortirait de la source ou rendrait un clip vide.
- **Verrous** : piste verrouillée et calque graphique verrouillé sont refusés ; `slide_clip` et `roll_edit` refusent aussi si un voisin
  qu'ils modifieraient est un calque verrouillé. `move_clip_to_track` vérifie les deux pistes. `ripple_delete_clip` laisse en place les pistes
  verrouillées. `ripple_trim_clip_start` et `ripple_delete_clip` refusent, sans rien déplacer, si un clip qu'ils feraient reculer est un calque
  verrouillé (même sur une piste libre). `remove_track` et `rename_track` ne regardent pas le verrou (l'interface non plus) ; `move_track` le refuse.
- **Pistes** : `remove_track` refuse une piste qui porte des clips. `set_track_volume` et `set_track_pan` visent le fader de la piste
  (le mélangeur de l'interface) : piste audio seulement, déverrouillée ; la courbe d'automation de la piste reste à part.
- Pas de verbe pour la sélection multiple ni les actions qui lisent la tête de lecture : ce sont des états d'interface.

## Vitesse et remappage

Ces verbes appellent les fonctions que l'inspecteur de vitesse, le menu « Vitesse » et l'éditeur de courbes appellent
(`core/timeline_operations.py`, `core/time_ops.py`) : le résultat est celui du geste correspondant. Ils sont dans `core/scripting/time_verbs.py`.

- **Clips visés** : vidéo et audio, déverrouillés (piste et calque) ; un clip de graphiques ou de sous-titres n'a pas de vitesse et est refusé.
- **Rien n'est ramené en silence** : le modèle ramènerait une vitesse hors de 0,05 à 10, un point hors du clip, un groupe de points trop
  déplacé, ou alignerait un instant sur une image ; un script reçoit tout cela refusé (une valeur hors bornes est refusée à la lecture).
  `move_speed_points` refuse aussi un point qui arriverait sur un autre sans le déplacer (le modèle le remplacerait).
- **Un point de vitesse se désigne par son instant** `at` (secondes depuis le début du clip), comme une image clé d'animation ; les identifiants
  d'image clé sont générés par le modèle et un script n'a pas à les citer. Aucun verbe de vitesse ne crée d'identifiant.
- **`ripple`** (`source` ou `timeline`, défaut `source`, celui de l'interface) : que garde une édition qui change la durée du clip, la portion de
  média ou la durée sur la timeline.
- **Ce que fait le modèle, comme l'interface** : `set_clip_speed` et `clear_speed_curve` remplacent ou retirent la courbe ; `set_clip_freeze_frame`
  retire la courbe de vitesse ; un clip dont la durée diminue voit ses images clés qui tombent après sa nouvelle fin recadrées ou retirées ; la première
  courbe posée avec une `value` ajoute un point d'amorce à 0 s qui garde la vitesse d'avant. Aucun verbe ne repousse les clips voisins : un clip
  ralenti peut en recouvrir un autre, comme dans l'interface.
- `insert_hold` aligne dans l'interface l'instant sur une image de la timeline ; le script doit déjà donner un instant sur une image
  (le message donne le plus proche) plutôt que de le voir déplacer.
- `set_clip_speed`, `set_clip_reverse`, `set_clip_preserve_pitch` et `set_clip_remap_audio` sont refusés sur un clip figé (l'interface les désactive ; la valeur serait jetée à la levée du gel) ; `set_clip_freeze_frame` reste permis sur un clip figé, comme dans l'interface.
- `remove_clip_freeze_frame` et `set_clip_freeze_duration` ne s'appliquent qu'à un clip figé : le modèle remettrait sinon à zéro la vitesse
  et le sens d'un clip qui n'est pas figé.

## Transform

`set_transform` règle la **valeur de base** du transform, comme l'inspecteur « Mouvement » : les champs absents et les images clés restent
(`add_keyframe` pose les images clés). Dans `core/scripting/transform_verbs.py`.

- Les bornes sont celles du registre d'animation (le tableau de « Images clés : propriétés et unités ») ; `flip_h`, `flip_v` et `fill` sont des booléens.
- Une propriété qui a des images clés est refusée (la courbe masquerait la valeur de base) ; `reset_transform` retire, lui, toutes les images clés du transform.
- `skew` n'existe que pour un calque graphique, `fill`, `pan_x` et `pan_y` que pour un clip vidéo : sur l'autre genre de piste ils seraient ignorés au rendu, donc refusés.
- Le suivi (tracking) et la stabilisation composent la valeur de base : ce que le script écrit est la base, pas le résultat rendu.

## Audio de clip

Gain, panoramique, fondus, effets audio d'un clip et ducking, comme l'inspecteur audio (`core/scripting/clip_audio_verbs.py`).

- **Gain, panoramique, fondus** : seulement pour les clips d'une piste audio (le groupe audio de l'inspecteur n'existe que pour elles). Le modèle n'a
  pas de mutateur pour le gain ni le panoramique (l'interface écrit le champ) ; le verbe refuse ce qui sort de −60 à 12 dB et de −1 à 1. Un fondu qui
  dépasserait ce qui reste de la durée du clip (durée moins l'autre fondu) serait réduit par le modèle : il est refusé.
- **Effets audio de clip** : sur un clip de piste audio **ou vidéo** (comme le rack d'effets de l'inspecteur) ; un clip graphique ou de sous-titres
  est refusé. Les paramètres sont ceux de l'effet (`parametric_eq`, `compressor`…), validés par le modèle : un nom inconnu ou une valeur hors bornes est refusé.
  `add_clip_audio_effect` crée l'effet (`id` / `as`, unique dans le clip) ; sans identifiant, le modèle le nomme `afx-…`.
- **Ducking** : `duck_music_under_voice` utilise les rôles de piste déjà posés dans le projet (il n'y a pas encore de verbe pour les changer) ; une association
  existante reçoit le nouveau réglage et est réactivée, comme avec la commande de menu. Il refuse si une piste « musique » est verrouillée (elle serait atténuée) ;
  la commande de menu ne regarde pas ce verrou.

## Lire un projet : `describe`

`describe` rend la **structure** d'un projet comme un dictionnaire JSON **versionné** : de quoi écrire un script sans deviner les
identifiants, ou vérifier ce qu'un script a produit. Il ne modifie rien (ni le projet, ni aucun fichier : pas de verrou) et ne
touche pas au disque sauf si on le demande.

```bash
Kut-Studio describe projet.kut            # un résumé lisible
Kut-Studio describe projet.kut --json     # un objet JSON sur la sortie standard (clé `description`), comme `info --json`
```

```python
from core.scripting.api import API_VERSION, describe, describe_json

data = describe(project)                   # un dict, JSON strict ; check_media=True teste la présence des fichiers
text = describe_json(project)              # le même, en texte (mêmes octets pour le même projet)
```

`Session.describe()` rend la même chose. La façade `core/scripting/api.py` est la **seule surface publique** : un test fige ses noms,
et `API_VERSION` (`MAJEUR.MINEUR`) monte au mineur quand elle grandit, au majeur pour une rupture annoncée dans le CHANGELOG.

**Un exemple court** (un projet à une piste, abrégé) :

```json
{
  "describe_version": 1,
  "project": {"name": "Démo", "width": 1280, "height": 720, "fps": 25.0, "duration": 4.0, "active_sequence": "seq-main"},
  "media": [{"id": "m1", "name": "a", "path": "/media/a.mp4", "media_type": "video", "duration": 4.0, "width": 1280,
             "height": 720, "fps": 25.0, "has_audio": true, "color_matrix": "auto", "present": null}],
  "sequences": [{
    "id": "seq-main", "name": "Séquence principale", "active": true, "width": 1280, "height": 720, "fps": 25.0, "duration": 4.0,
    "format_source": null,
    "tracks": [{"id": "V1", "name": "V1", "type": "video", "locked": false, "visible": true, "muted": false, "solo": false,
                "volume_db": 0.0, "pan": 0.0, "audio_role": "other",
                "clips": [{"id": "c1", "kind": "media", "asset_id": "m1", "sequence_id": null, "angle_id": null,
                           "template_slot": null, "label": "a", "text": "", "enabled": true, "timeline_start": 0.0,
                           "duration": 4.0, "timeline_end": 4.0, "source_in": 0.0, "source_out": 4.0,
                           "time_remapping": {"speed": 1.0, "reverse": false, "...": "..."}, "has_speed_curve": false,
                           "transform": {"position_x": 0.0, "...": "..."}, "gain_db": 0.0, "pan": 0.0, "fade_in": 0.0,
                           "fade_out": 0.0, "graphic": null, "has_keyframes": false}]}],
    "transitions": [], "markers": []}],
  "not_described": ["audio_automation", "audio_buses", "color", "effects", "..."]
}
```

- **Ce qui est décrit** : le projet (nom, taille, cadence et durée de la séquence active), les médias (chemin, type, durée, métadonnées de
  la sonde, `present`), les séquences (format, `format_source`), les pistes (verrou, visibilité, muet, solo, fader, panoramique, rôle),
  les clips (identifiants du média, de la séquence imbriquée, de l'angle ; place, durée, plage source ; vitesse et remappage ; transform ;
  audio de base ; calque graphique ; sous-titre), les transitions et les repères.
- **Ce qui ne l'est pas encore** est **nommé** dans `not_described` (effets, couleur, composition, courbes, images clés, bus…) : la
  sortie ne met jamais une clé vide à la place d'une donnée qu'elle ne lit pas. `has_keyframes` dit seulement qu'un clip en porte.
- **Les noms de clés sont ceux des champs du modèle** (`timeline_start`, `asset_id`…), en anglais ; les durées sont en **secondes**. Les
  clés dérivées (`kind`, `duration`, `timeline_end`, `has_speed_curve`, `has_keyframes`, `active`, la taille du projet) sont les seules
  qui n'existent pas dans le modèle. `kind` vaut `media`, `sequence` (clip imbriqué), `composition` ou `graphic`.
- **Valeurs** : JSON strict (une valeur non finie est `null`), nombres arrondis à 6 décimales, ordre des clés et des listes fixe (celui du
  modèle). Une chaîne vide qui veut dire « aucun » (`asset_id`, `sequence_id`, `angle_id`…) est `null`.
- **Médias** : `present` vaut `null` (inconnu) tant que la présence n'est pas demandée (`check_media=True`) ; la commande `describe` la
  demande. Les médias techniques (sous-titres, calques) n'ont pas de fichier : `null` aussi.
- **Une séquence imbriquée est citée** (`sequence_id`), jamais développée : sa description est dans `sequences`.
- **Distinct du fichier `.kut`** : `describe_version` (entier) change quand une clé disparaît ou change de sens ; ajouter des clés (les
  sections de `not_described` à mesure qu'elles sont décrites) n'est pas une rupture.
- **Avec `schema` et `apply`** : `schema` dit ce qu'un script peut écrire, `apply` l'écrit, `describe` relit le résultat (les identifiants
  d'un script, `id` ou `as`, se retrouvent tels quels dans la description).

**Complétude.** Deux listes de dette qui ne peuvent que diminuer (`tests/data/`) empêchent la sortie et les verbes de dériver du modèle :
`describe_field_debt.json` (chaque champ du modèle est décrit, exclu avec une raison, ou en dette avec sa section) et
`script_verb_debt.json` (chaque fonction publique d'édition a un verbe, ou une raison écrite de ne pas en avoir). Un champ ou une fonction
neufs font échouer les tests jusqu'à une décision. `python -m tools.scripting_debt fields|verbs --list` montre la dette.

## Pas encore là

- **Animation de l'étalonnage et des paramètres d'effets** : le modèle ne les anime pas (aucune propriété animable de couleur ni d'effet). C'est une fonctionnalité de modèle à décider à part, pas un lot de verbes ; les calques d'image sont faits (`add_image_layer`).
- Le reste de `describe` (effets, couleur, composition, courbes, images clés, bus), les **vues typées** et `run` (façade Python) puis la **console** viennent après (étapes 5 partie B, 6 et 7 de l'ADR).

## Code

| Module | Rôle |
| --- | --- |
| `core/scripting/verbs.py` | la table `VERBS` : paramètres typés et bornés, fonction du modèle appelée ; `ScriptContext` (le dossier permis) |
| `core/scripting/animation_verbs.py`, `effect_verbs.py`, `mask_verbs.py`, `edit_verbs.py`, `time_verbs.py`, `transform_verbs.py`, `clip_audio_verbs.py` | les verbes d'images-clés de masque, d'effets de clip, de masques, de montage, de vitesse, de transform et d'audio de clip (assemblés dans `VERBS` après leur verbe d'ancrage) |
| `core/scripting/script.py` | le décodeur strict (erreurs structurées), la lecture du fichier, l'application par la session |
| `core/scripting/schema.py` | le schéma JSON, généré depuis la table |
| `core/scripting/session.py` | la session : tout ou rien, une étape d'historique |
| `core/project_lock.py` | le verrou consultatif d'un projet (`ProjectLock`, `judge`, `read_lock`) |
| `core/scripting/api.py` | la façade publique : `API_VERSION`, `DESCRIBE_VERSION`, `describe`, `describe_json` |
| `core/scripting/describe.py`, `describe_coverage.py` | la lecture (`describe`), et la table champ par champ de ce qui est décrit, exclu ou en dette |
| `tools/scripting_debt.py` | les deux listes de dette (champs de `describe`, verbes) |
| `core/cli.py` | `apply`, `validate script.json`, `schema`, `describe` |
