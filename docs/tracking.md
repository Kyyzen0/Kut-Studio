# Tracking 2D et stabilisation

Kut-Studio suit des **points** d'un clip vidéo au fil des images, et s'en
sert pour faire suivre un calque, un clip, un point d'ancrage ou un masque,
ou pour **stabiliser** le clip. Tout est non destructif, annulable, enregistré
dans le `.kut`, et rendu **par le même moteur d'animation** dans l'aperçu
et à l'export.

Hors périmètre (volontairement) : rotoscopie IA, segmentation, tracking 3D,
camera solve, planar tracking, détection d'objets, génération de contenu.

## Pour l'utilisateur

1. Sélectionner un clip vidéo, ouvrir l'onglet **Suivi** de l'inspecteur
   (menu « ••• », ou *Calques → Suivi (tracking)…*).
2. **Ajouter** un tracker (il apparaît au centre ; double-cliquer dans le
   viewer le pose à cet endroit). Le glisser sur un détail contrasté — un
   coin, une tache, un logo — jamais sur une zone uniforme.
3. Ajuster la **zone cible** (trait plein : le motif suivi) et la **zone de
   recherche** (pointillés : jusqu'où il peut bouger d'une image à l'autre)
   par leurs coins (Maj : carré).
4. **Avant ▶▶** ou **◀◀ Arrière** : l'analyse tourne en tâche de fond
   (progression, image analysée) ; **Stop** l'arrête en gardant ce qui est
   fait.
5. Si le suivi est **incertain** (points orange) ou **perdu** (croix rouge),
   corriger le point à la main sur l'image fautive, puis relancer depuis là.
6. **Appliquer** : choisir la cible (calque, clip, masque, point d'ancrage),
   cocher Position (Rotation / Échelle avec deux trackers sélectionnés),
   puis **Lier** (la cible suit, et suivra les corrections) ou **Convertir
   en keyframes** (valeurs figées, indépendantes du tracker).
7. **Stabilisation** : cocher *Stabiliser ce clip*, choisir la compensation,
   le lissage et le traitement des bords. Le panneau indique l'agrandissement
   nécessaire (« La stabilisation agrandit l'image de 12 % »). *Stabilisation
   automatique* pose quatre trackers sur des détails contrastés, les analyse
   dans les deux sens puis stabilise.

Chaque action est **une** entrée d'historique (une analyse de 2 000 images
aussi) ; un glisser dans le viewer n'en crée qu'une au relâchement.

## Architecture

```
core/tracking_model.py     données : Tracker, TrackData, TrackLink, Stabilization, ClipTracking
core/tracking_frames.py    lecture des images d'analyse (FFmpeg → gris, avant / arrière)
core/tracking_match.py     suivi image à image (NCC par FFT, sous-pixel)   ← seul module numpy
core/tracking_engine.py    analyse : requête, exécution annulable, cache, tâche asynchrone
core/tracking_motion.py    géométrie : temps, repères, similitudes, lissage, stabilisation
core/tracking_bindings.py  liaisons → images-clés dérivées ; bake
core/tracking_ops.py       opérations d'édition (une entrée d'historique chacune)
ui/tracking_panel.py       panneau Suivi de l'inspecteur
ui/tracking_overlay.py     points, zones, trajectoires dans le viewer
ui/main_window_mixins/tracking.py   contrôleur de fenêtre
```

### Abstractions

| Notion | Classe | Rôle |
| --- | --- | --- |
| Point de tracking | `Tracker` | nom, couleur, visibilité, réglages, `kind` (`point`), données |
| Données suivies | `TrackData` | échantillons `(x, y, confiance, état)` par image source |
| Résultat d'analyse | `TrackingResult` / `TrackerOutcome` | images produites, raison d'arrêt, état (terminé, annulé, échec) |
| Cible | `TrackLink` + `TrackTarget` | propriété pilotée : `transform`, `anchor`, `mask` |
| Stabilisation | `Stabilization` | trackers, mode, lissage, bords, image de référence |

Un tracking produit, par image : position X/Y, **confiance** (score NCC du
pic, 0..1), **état** (`TRACKED`, `UNCERTAIN`, `LOST`, `MANUAL`, `EMPTY`) ; la
**plage couverte** est `TrackData.valid_range()`.

### Repères et temps

- Positions en **pixels du média original**, quelle que soit la résolution
  d'analyse ou l'existence d'un proxy.
- Temps en **images source** : l'échantillon `i` est l'image du média à
  `i / cadence`. Trimer, couper, accélérer ou inverser le clip **après**
  l'analyse ne change pas les données : seule la correspondance timeline →
  source change (`tracking_motion.source_time`, mêmes règles que l'export).
- Chaîne de repères d'un clip vidéo :
  `pixels source --fit--> calque (W×H) --C (stabilisation)--> --L (transform)--> cadre`.
  `fit` reproduit `scale … force_original_aspect_ratio=decrease` + `pad` de
  l'export ; `L` est `mograph_scene.local_matrix`. Les masques d'un clip
  vidéo vivent dans le repère calque.

## Algorithme de suivi

Par image et par point (pixels d'analyse) — `tracking_match.PointMatcher` :

1. **prédiction** : position + vitesse amortie ;
2. **zone de recherche** centrée sur la prédiction ; si elle est rognée par
   le bord et que le pic s'y colle, le point **sort de l'image** (perdu) ;
3. **NCC** (corrélation croisée normalisée, Lewis 1995) : numérateur par
   `numpy.fft.rfft2`, moyennes / variances locales par images intégrales ;
   insensible aux changements de luminosité et de contraste ;
4. **sous-pixel** : ajustement gaussien (parabole sur les logarithmes) du pic ;
5. **confiance** : ≥ *bonne* (0,8) suivi ; ≥ *minimale* (0,55, réglable)
   incertain ; sinon **perdu** — aucune position n'est inventée ;
6. **motif adaptatif** (par défaut) : rafraîchi après chaque image fiable,
   et **recalé sur le motif d'origine** (±3 px) tant qu'il reste reconnaissable :
   la dérive cumulée tombe de 0,33 à 0,09 px sur 80 images. *Fixe* : motif
   de l'image de départ seulement.

Arrêts : fin de plage, fin du média / images illisibles, perte (si *S'arrêter
si perdu*), **correction manuelle** rencontrée (jamais écrasée : on reprend
depuis elle), annulation.

### Multi-points

`tracking_motion.motion_series` ajuste, à chaque image, la **similitude**
(translation, rotation, échelle — Umeyama sans réflexion) qui envoie les
points de l'image de référence sur ceux de l'image courante, exprimée autour
d'un **pivot fixe** (barycentre de référence) :

- une image est mesurée dès qu'assez de trackers y sont valides (1 pour la
  translation, 2 pour rotation / échelle) : un tracker perdu ou masqué ne
  désactive rien ;
- les trackers **incohérents sur toute la durée** (résidu médian ≫ celui des
  autres : de l'eau, un passant, un tracker qui glisse) sont écartés une fois
  pour toutes — décider image par image ferait basculer le modèle et créerait
  de la gigue sur un décor avec parallaxe ;
- les images non mesurées sont interpolées (paramètres `c, θ, ln k`).

C'est la base d'une extension planar : remplacer la similitude par une
homographie (4 points ou plus) dans `motion_series`.

## Application aux propriétés : un seul moteur d'animation

Une liaison (`TrackLink`) et une stabilisation **ne stockent aucune
image-clé**. `tracking_bindings.effective_clip_state` les **dérive** à la
construction du plan de rendu (`render_plan.build_render_plan`) en
images-clés linéaires ordinaires — une par image de la séquence, simplifiées
à une tolérance invisible (0,02 px, 0,01°), identifiants déterministes. Tout
le reste lit ces images-clés comme n'importe quelle animation : moniteur temps
réel, segments fidèles, export FFmpeg (mêmes expressions polynomiales),
poignées du viewer, flou de mouvement, empreintes de cache. Il n'y a **pas**
de second moteur, et corriger le tracker met aussitôt à jour toutes les cibles.

Formules (`A` = mouvement mesuré, identité à l'image de référence ; la cible
garde ses propres valeurs `U`, statiques ou animées, et le mouvement s'y
**ajoute**) :

| Cible | Mouvement appliqué |
| --- | --- |
| calque / clip dans le cadre | `A_cadre = M_S(t) · A_calque · M_S(t_réf)⁻¹` puis `L(V) = A_cadre · L(U)` |
| masque du clip suivi | `A_calque` directement (le masque est attaché au contenu) |
| masque d'un autre clip `X` | `N_X⁻¹ · A_cadre · N_X` |
| point d'ancrage du clip suivi | ancrage = point suivi : il reste à la position |
| stabilisation | `L(V) = L(U) · C`, `C = S ∘ A⁻¹` (`S` = mouvement lissé) |

`M_S` est le repère calque → cadre du clip source **stabilisation comprise**
(un calque suit l'objet tel qu'on le voit). La composition à gauche (suivi)
est exacte quels que soient miroirs et échelles X/Y.

**Bake** (`tracking_ops.bake_link`) : les mêmes images-clés dérivées sont
écrites dans le clip, la liaison est retirée.

Coût : résolu une fois puis mis en cache (clé = tout ce qui influe) ; un
clip de 10 min (18 000 images) se résout en ~0,3 s, puis 0,1 ms.

## Stabilisation

- **Modes** : position seulement ; position + rotation ; position + rotation +
  échelle (deux trackers au moins pour les deux derniers).
- **Lissage** : faible (σ = 4 images), moyen (12), fort (30), personnalisé,
  ou **plan fixe** (verrouillé sur l'image de référence). Filtre quasi
  gaussien (trois moyennes glissantes, O(n), normalisées aux bords).
- **Bords** : *bords noirs* ; *zoom automatique* (agrandissement constant,
  juste suffisant : le plus grand rectangle centré qui tient dans toutes les
  images corrigées, calculé sur les seules images montrées par le clip) ;
  *recadrage fixe* (masque rectangle **dérivé**, jamais stocké, qui ne montre
  que la zone stable, sans agrandir). L'agrandissement est affiché, en orange
  à partir de 10 %.

## Analyse asynchrone

L'analyse réutilise le système de tâches existant : `TaskQueue` / `QueueWorker`
du runtime, sur une **voie d'analyse** dédiée (`StudioRuntime.schedule_analysis`)
pour ne jamais retarder les miniatures et formes d'onde. `TrackingJob` expose
un instantané (état, progression, image, trajectoire partielle) que
l'interface lit toutes les 100 ms ; l'annulation arrête FFmpeg à l'image
suivante (~2 ms mesurés). Changer de projet annule les analyses. Le résultat
est fusionné dans le projet en **une** entrée d'historique.

## Proxies et source d'analyse

**Décision** : l'analyse lit le **média original**, réduit par FFmpeg à une
résolution d'analyse — *Auto* : 1920 px de large au plus (un 4K est analysé
en 1080p), *Rapide* : 960 px, *Maximale* : native. Option *Analyser le proxy
s'il existe* : FFmpeg ramène le proxy à la **même** résolution d'analyse,
les coordonnées sont donc identiques et converties en pixels du média
original. Le résultat est enregistré dans le projet : le rendu et l'export
ne dépendent jamais ni du proxy, ni du cache, ni même du média d'analyse.

Cadence variable : le filtre `fps` rééchantillonne la source sur la grille
nominale (comme l'export sur la grille du projet). Seules les images de la
plage demandée sont décodées (`-ss` précis avant `-i`) ; à rebours, la plage
est lue par blocs, du plus tardif au plus ancien.

## Cache d'analyse

`TrackingCache` (disque, `<cache>/tracking`, compté et évincé par
`core.cache_manager` comme les autres couches). Clé : chemin + signature du
fichier lu (date, taille), variante (original / proxy), cadence, résolution
d'analyse, image de départ **et** position de départ, image de fin (direction
et plage), corrections manuelles de la plage, réglages (zones, seuils, motif,
précision), type de tracker, version de l'algorithme. Relancer la même
analyse (undo puis redo, reset, autre clip du même média) est instantané.
Les résultats frais sont quantifiés comme ceux du cache : ils sont identiques.

## Format `.kut` (version 16)

Clé `tracking` d'un clip, **absente** sans tracking (un fichier v15 s'ouvre
tel quel, un projet sans tracking s'écrit comme avant) :

```json
"tracking": {
  "trackers": [{
    "id": "…", "name": "Œil gauche", "color": "#36E6C3", "visible": true, "show_path": true,
    "kind": "point",
    "settings": {"pattern_width": 48, "pattern_height": 48, "search_width": 144, "search_height": 144,
                 "min_confidence": 0.55, "good_confidence": 0.8, "adapt": "adaptive",
                 "precision": "auto", "stop_on_loss": true},
    "data": {"rate": 25.0, "first": 0, "count": 300, "source_size": [3840, 2160],
             "encoding": "delta-i32le-zlib-b64", "x": "…", "y": "…", "confidence": "…", "status": "…"}
  }],
  "links": [{"id": "…", "source_clip_id": "", "tracker_ids": ["…"], "target": "mask", "mask_id": "…",
             "position": true, "rotation": false, "scale": false, "reference_index": 12, "enabled": true}],
  "stabilization": {"enabled": true, "tracker_ids": ["…", "…"], "mode": "position_rotation",
                    "smoothing": "medium", "smoothing_frames": 12.0, "borders": "zoom", "reference_index": 0}
}
```

- Positions en virgule fixe (1/1024 px), **delta + zlib + base64** : ~5 octets
  par image et par tracker (10 min de suivi ≈ 95 Ko).
- `source_clip_id` vide = le clip lui-même : une coupe ou une duplication
  garde ses liaisons internes.
- Les corrections de stabilisation, le masque de recadrage et les images-clés
  des liaisons ne sont **pas** stockés : ils se dérivent des données.
- Lecture tolérante : un tracker, une liaison ou des données corrompus sont
  ignorés seuls, jamais le clip.
- `source_size` : si le média est remplacé par une autre résolution (relink,
  autre machine), les positions suivent l'échelle.

## Undo / redo

Snapshots du projet (`core.edit_history`) : `TrackData` est immuable et ses
tableaux sont des `bytes` — les snapshots **partagent** les données au lieu
de les copier. Entrées : ajout / suppression / renommage / visibilité d'un
tracker, correction ou zone (au relâchement), réglages, reset, **une analyse
complète**, liaison, activation, bake, stabilisation (activation et réglages).

## Dépendances : pourquoi numpy et pas OpenCV

| Critère | numpy | OpenCV (`opencv-python-headless`) |
| --- | --- | --- |
| Taille (wheel macOS arm64) | 5,4 Mo | 48 Mo **+ numpy** |
| Licence | BSD-3 | Apache-2.0 (+ FFmpeg LGPL embarqué sur Windows / Linux) |
| macOS / Windows / Linux | roues officielles (3.11 → 3.14) | roues officielles |
| PyInstaller | hook intégré, sans réglage | hook contrib, fichiers de config à embarquer |
| Performance ici | 0,1 à 0,4 ms par point et par image (NCC FFT) | comparable : le décodage domine |
| Stabilité d'API | très stable | changements entre versions majeures |

Le besoin (corrélation de motifs, ajustement de similitudes) tient en
quelques fonctions numpy ; OpenCV apporterait 10 fois plus de poids pour un
gain nul sur ce périmètre. numpy n'est importé **qu'à l'analyse** : ouvrir,
monter, rendre et exporter n'en dépendent pas (testé). Le smoke test de
l'application construite vérifie que numpy et ses extensions fonctionnent
(`tracking_match.self_check`). Un futur moteur OpenCV (KLT, CSRT…) pourrait
s'ajouter comme un autre `kind` sans toucher au reste.

## Performances

`python -m tools.perf.tracking_bench --out docs/perf/tracking.json [--real plan.mov]`
(mire H.264, Apple Silicon ; décodage compris, chaque analyse dans un
processus séparé ; voir `docs/perf/tracking.json`) :

| Média | Rapide | Auto | Maximale | Pic mémoire |
| --- | --- | --- | --- | --- |
| 720p | 1 370 i/s | 1 200 i/s | 1 220 i/s | 43 Mo |
| 1080p | 980 i/s | 710 i/s | 710 i/s | 48 Mo |
| 4K | 360 i/s | 340 i/s | 205 i/s | 64 Mo |

- **Trackers simultanés** (1080p, décodage partagé) : 1 → 700 i/s, 4 → 714,
  8 → 589, 16 → 466 i/s (0,13 ms par point et par image).
- **Proxy** : un 4K analysé depuis son proxy 1080p : 916 i/s au lieu de 314
  (×2,9).
- **Annulation** : effective en ~2 ms.
- **Liaisons** : 10 min de suivi → 0,27 s à la première construction du plan,
  0,1 ms ensuite (cache).
- **Cas réel** (fond d'écran animé « Golden Gate » de macOS, 4K HEVC 10 bits,
  cadence variable ~232 i/s, vrai mouvement de caméra avec parallaxe) :
  voir la section *Validation sur un cas réel*.

## Validation sur un cas réel

Plan réel, 2 s, deux points saillants choisis automatiquement :

- analyse à ~206 i/s (4K HEVC 10 bits décodé par FFmpeg, analysé en 1080p), confiance moyenne 0,99, aucun arrêt ;
- erreur aller-retour (suivi avant puis arrière) : **0,16 px** sur l'arche du
  pont ; **2,9 px** sur un bord de rocher touché par l'écume — le motif mêle
  rigide et eau, la corrélation reste haute alors que le point glisse : c'est
  ce qui a motivé quatre points pour la stabilisation automatique et le rejet
  des trackers incohérents ;
- **stabilisation** du même plan secoué artificiellement (recadrage mobile,
  ±85 px, 1080p) : gigue des points suivis **39,8 px → 0,56 px**, zoom
  automatique de 14,9 %.

Aperçu / export : la position d'un calque lié est identique dans le
rastériseur Qt et l'image exportée par FFmpeg (écart < 0,6 px, testé), et un
sujet stabilisé reste immobile dans l'export (< 1 px, testé).

## Ajouter un type de tracker

1. `tracking_model.TrackerKind` : déclarer le type (`PLANAR = "planar"`).
2. Données : un point par coin suffit (`TrackData` existant, un tracker par
   coin), ou une sous-classe portant les paramètres propres au type.
3. Analyse : un `Matcher` comme `tracking_match.PointMatcher` (méthode
   `step(frame) → StepResult`) et son aiguillage dans
   `tracking_engine._analyze` selon `tracker.kind` ; ajouter le type et ses
   réglages à `cache_key`.
4. Mouvement : si le type fournit plus qu'une similitude (homographie),
   l'exprimer dans `tracking_motion` et l'utiliser dans `LinkMotion` /
   `stabilization_result`.
5. Interface : dessin des zones propres au type dans `tracking_overlay`.

Rien d'autre ne change : liaisons, bake, sérialisation, undo, cache de
rendu, aperçu et export suivent.

## Limites connues

- **Un calque cible parenté** reçoit le mouvement dans l'espace de son parent
  (exact si le parent n'est ni tourné ni mis à l'échelle).
- **Stabilisation avec échelle X ≠ échelle Y et rotation compensée** : la
  rotation est appliquée telle quelle (approximation ; exact en échelle
  uniforme).
- **Clips imbriqués** : ni source ni cible de liaison.
- Le suivi est **2D** (similitude) : pas de perspective, pas de déformation
  du contour d'un masque, pas de suivi planar.
- La **confiance** est le score de corrélation : elle peut rester haute sur
  un motif qui glisse (eau, reflets). Une vérification aller-retour
  automatique n'est pas encore proposée dans l'interface.
- Changement de résolution **au milieu** d'un fichier : FFmpeg ramène tout à
  la taille sondée à l'import (positions cohérentes, image déformée si le
  rapport change).
- Liaisons sur de très longs clips : la première résolution coûte ~0,3 s pour
  10 min ; pendant qu'on glisse une correction, l'aperçu des cibles liées
  suit avec ce délai.
- Le moniteur temps réel (lecteur Qt) n'affiche pas le recadrage fixe de la
  stabilisation ; les segments fidèles et l'export, si.
