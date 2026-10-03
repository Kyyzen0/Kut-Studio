# Multi-séquence et séquences imbriquées

Un projet Kut-Studio contient une ou plusieurs **séquences** (timelines
autonomes). Une séquence peut être utilisée **comme un clip** dans une autre :
c'est une *séquence imbriquée*. Le clip ne copie rien : il référence la
séquence. Modifier la séquence met à jour toutes ses occurrences.

```
Projet
├── médias (partagés par toutes les séquences)
├── Séquence « Master »        ← active (éditée par la timeline)
│     V2 : [ Intro ]  [ Intro ]   ← deux clips imbriqués (sequence_id = intro)
│     V1 : [ plan A ][ plan B ]
└── Séquence « Intro »
      V1 : [ logo ]  [ titre ]
      A1 : [ musique ]
```

- [Guide utilisateur](#guide-utilisateur)
- [Modèle de données](#modèle-de-données)
- [Résolution récursive et rendu](#résolution-récursive-et-rendu)
- [Prévention des cycles](#prévention-des-cycles)
- [Durées : politique](#durées--politique)
- [Format `.kut` (v14)](#format-kut-v14)
- [Historique, navigation, cache](#historique-navigation-cache)
- [Performances](#performances)
- [Ajouter une fonctionnalité compatible](#ajouter-une-fonctionnalité-compatible)
- [Limites connues](#limites-connues)

---

## Guide utilisateur

### Créer une séquence à partir d'une sélection

1. Sélectionnez un ou plusieurs clips (clic, `Maj`/`Ctrl` + clic ou cadre de
   sélection), sur une ou plusieurs pistes.
2. Clic droit sur un clip → **Créer une séquence à partir de la sélection**
   (ou menu *Séquence*, ou `Ctrl+Maj+N`).
3. Donnez un nom à la séquence.

Les clips partent dans la nouvelle séquence en gardant leurs positions
relatives, leurs effets, images-clés, réglages audio, remappage et étalonnage.
Les transitions entre deux clips sélectionnés les suivent ; une transition qui
relierait un clip resté dehors est retirée. Les repères compris dans la plage
sont copiés. La sélection est remplacée par **un** clip imbriqué (doré) placé
sur la piste vidéo sélectionnée la plus basse. Un seul `Ctrl+Z` annule tout.

### Ouvrir une séquence imbriquée

- **Double-clic** sur le clip imbriqué (badge « deux cadres », couleur dorée),
- ou clic droit → **Ouvrir la séquence imbriquée**, ou `Ctrl+Alt+↓`,
- ou la page **Séquences** du rail de gauche (double-clic), ou le menu
  **Séquences ▾** au-dessus de la timeline.

La séquence s'ouvre **à l'image** que montrait la tête de lecture. La barre
au-dessus de la timeline affiche toujours la séquence éditée et le chemin
suivi : `Master › Scene 01 › Intro` (le dernier élément, en gras, est la
séquence active). Le nom figure aussi dans la barre du haut.

### Revenir à la séquence parente

- bouton **↑** de la barre de navigation, ou `Ctrl+Alt+↑`,
- ou cliquez un niveau du fil d'Ariane,
- **‹ / ›** (`Alt+←` / `Alt+→`) : séquence précédente / suivante, comme dans
  un navigateur.

Chaque séquence retrouve la tête de lecture où vous l'aviez laissée.

### Gérer les séquences

Page **Séquences** (rail de gauche) : créer, ouvrir, insérer dans la séquence
active (à la tête de lecture), renommer, dupliquer, supprimer. Une séquence se
**glisse** aussi sur une piste vidéo ou audio de la timeline. Sur une piste
vidéo, le clip apporte l'image et le son de la séquence ; sur une piste audio,
le son seul.

Supprimer une séquence encore utilisée affiche **où** elle l'est et demande
confirmation. Si vous confirmez, les clips concernés restent en place mais
deviennent **hors ligne** (rouge hachuré, rendus vides) ; `Ctrl+Z` les rétablit.

---

## Modèle de données

`core/project_model.py`

| Objet | Rôle |
| --- | --- |
| `Sequence` | `id` stable, `name`, `width`/`height`/`fps`, `tracks`, `markers`, `transitions`, `ducking_sidechains`. `duration` = fin du dernier clip activé (ne descend pas dans les imbrications). |
| `Project` | `media_assets` (partagés), `sequences` (≥ 1), `active_sequence_id`, bibliothèque, presets couleur. |
| `Clip.sequence_id` | Non vide : *nested sequence clip*. `source_in`/`source_out` sont des temps de la séquence référencée ; `asset_id` est vide. Le clip garde toutes les propriétés standard (transform, images-clés, effets, compositing, étalonnage, gain, pan, fondus, remappage). |

**Compatibilité** : `project.tracks`, `markers`, `transitions`,
`ducking_sidechains`, `width`, `height`, `fps` sont des propriétés qui
**délèguent à la séquence active**. Tout le code écrit avant le multi-séquence
(opérations de timeline, UI, plan de rendu, export) agit donc sur la séquence
ouverte sans modification. `Project(name, width, …, tracks=…)` crée une
séquence principale (`MAIN_SEQUENCE_ID = "seq-main"`). Les parcours qui
concernent **tout** le projet (usages de médias, LUTs portables, purge de
cache) utilisent `project.all_tracks()`.

La logique métier vit dans `core/sequences.py` (pure, sans Qt) :
`create_sequence`, `rename_sequence`, `duplicate_sequence`, `delete_sequence`,
`insert_sequence_clip`, `create_sequence_from_selection`, `clamp_nested_clips`,
`sequence_usages`, `find_cycles`, `would_create_cycle`, `nesting_depth`,
`sequence_issues`, `dependent_nested_clip_ids`, `nested_source_time`,
`nested_source_window`.

**Duplication** : nouvel ID de séquence, nouveaux IDs de clips, transitions
recâblées, mêmes médias, effets et images-clés. Les clips imbriqués de la copie
référencent **les mêmes** séquences (pas de copie profonde récursive).

---

## Résolution récursive et rendu

Il n'existe **qu'un** moteur : celui des séquences normales, appelé
récursivement.

```
séquence interne ──▶ plan interne ──▶ composite (fond transparent)
                                         │  rendu une fois, split ×N instances
                                         ▼
                trim in/out · remappage · cadrage · transform · effets ·
                étalonnage · compositing · opacité (propriétés du clip imbriqué)
                                         ▼
                                  timeline parente
```

### Plan de rendu (`core/render_plan.py`)

`build_render_plan(project, sequence_id=None, window=None, …)` :

- un clip imbriqué devient une `RenderLayer` / `AudioLayer` dont
  `nested_key` désigne un `NestedSequencePlan` ; `source_path` est vide ;
- les sous-plans sont rangés **à la racine**, dans `plan.nested_sequences`,
  **enfants avant parents**. Un sous-plan est mémorisé par clé pendant la
  construction : trois instances de « Intro » à la même plage → un seul
  sous-plan ;
- avec `window` (segment d'aperçu), chaque sous-plan est fenêtré sur la plage
  que le clip **lit** pendant la fenêtre (`nested_source_window`, vitesse et
  reverse compris, une image de marge). La clé inclut la plage ;
- la `duration` d'un sous-plan couvre au moins le `source_out` le plus loin
  demandé : au-delà de la fin de la séquence, l'image est transparente et le
  son silencieux (jamais une image figée) ;
- les sous-titres d'une séquence imbriquée sont reportés dans le plan parent,
  recalés sur le clip (vitesse, reverse, arrêt sur image) ;
- références cassées, cycles et profondeur excessive : la couche est omise et
  `plan.warnings` l'explique. Le rendu n'échoue pas.

### Graphe FFmpeg (`core/export_engine.py`)

`_compose_plan_graph` compose un plan (racine ou imbriqué) ;
`_build_nested_sources` compose chaque sous-plan **une fois** puis le distribue
par `split` / `asplit` (un label FFmpeg ne se lit qu'une fois). Le besoin est
calculé des parents vers les enfants (`_nested_demand`) : une séquence dont
personne n'écoute le son ne construit pas son mixage. Une séquence imbriquée
est rendue à **sa** résolution × (taille d'export / taille de la séquence
racine), sur `color=black@0,format=yuva420p` ; son cadrage dans le parent
utilise un `pad` transparent. Préfixe de labels unique par sous-plan (`n0_`,
`n1_`…) ; la racine garde les labels historiques.

Aperçu fidèle, scopes, export et Render Queue appellent tous ce même graphe :
**le rendu est identique** entre séquence ouverte directement, séquence
imbriquée et export (vérifié par `tests/test_sequences_export.py` sur de vrais
rendus).

### Évaluation temps réel (`core/timeline_evaluator.py`, `core/timeline_index.py`)

`evaluate_timeline` et `TimelineIndex.active_at` remplacent un clip imbriqué
par les clips actifs de sa séquence à l'instant correspondant
(`expand_nested_clip`), récursivement. Les entrées portent `root_clip_id`
(le clip imbriqué de la séquence évaluée), `nested_path` et `sequence_id`.
L'index construit une fois les index des séquences imbriquées (`sub_index`) :
le coût ne dépend pas de leur taille.

### Audio

La séquence imbriquée est mixée par le même code (volume, pan, automation,
mute/solo, effets audio, ducking de ses pistes), puis son mixage passe par la
chaîne audio du clip imbriqué (gain, pan, fondus, effets du clip) et par la
piste parente (volume, automation, mute/solo). Synchronisation : `atrim` sur
`source_in`/`source_out` puis décalage à `timeline_start`, comme un média.

### Transformations, effets, images-clés

Les propriétés du clip imbriqué s'appliquent **après** le composite interne.
Les images-clés internes (dans la séquence) et celles du clip imbriqué (dans le
parent) sont indépendantes et se combinent : par exemple un texte animé à
l'intérieur, un `scale` global du clip dans le parent.

---

## Prévention des cycles

Interdits : A ⊃ A, A ⊃ B ⊃ A, A ⊃ B ⊃ C ⊃ A.

| Moment | Garde |
| --- | --- |
| Insertion (glisser, bibliothèque, commande) | `check_can_nest` : `would_create_cycle` (parcours itératif du graphe) + `nesting_depth`. Refus avec message lisible. |
| Création depuis la sélection | La nouvelle séquence ne contient que des clips de la séquence active : elle ne peut pas contenir son parent. |
| Chargement | Rien n'est modifié. `sequence_issues` (Tarjan itératif) liste cycles et références cassées ; l'interface les signale. |
| Évaluation / rendu | Pile des séquences en cours : une séquence déjà dans la pile n'est pas redescendue ; profondeur bornée à `MAX_NESTING_DEPTH` (32). Le clip fautif est rendu vide. |

Aucun parcours n'est récursif au sens Python sans borne : les cycles ne
peuvent ni bloquer l'application ni dépasser la pile.

---

## Durées : politique

| Cas | Comportement |
| --- | --- |
| Séquence source **rallongée** | Rien ne change : le clip garde son in/out. Étendez-le (trim) pour voir la suite. |
| Séquence source **raccourcie** par une action | Les clips imbriqués qui dépassent la nouvelle fin sont ramenés à cette fin (`clamp_nested_clips`), **à tous les niveaux** (la séquence parente raccourcie raccourcit à son tour ses propres parents, en un seul appel), **dans la même entrée d'historique** que l'action. L'entrée l'indique (« +N clip(s) imbriqué(s) ajusté(s) ») et la barre d'état l'annonce. Un seul `Ctrl+Z` restaure les deux. |
| Clip entièrement au-delà de la nouvelle fin | Conservé (jamais supprimé), rendu vide, signalé « hors source ». |
| Clip imbriqué **trimé** dans le parent | Borné par la durée de la séquence ; raccourcir reste toujours permis. |
| Projet chargé avec des clips qui débordent | Rien n'est modifié ; la zone sans source est hachurée et rendue transparente. |

---

## Format `.kut` (v14)

*Cette section décrit les clés introduites en version 14 ; le format courant est la **version 16** (voir [architecture.md](architecture.md#format-kut)).*

```json
{
  "format": "kut-studio-project",
  "version": 14,
  "project": {
    "name": "Episode 01",
    "media_assets": [ … ],
    "library_folders": [], "library_tags": [], "library_assignments": [],
    "color_presets": [],
    "active_sequence_id": "seq-3f2a…",
    "sequences": [
      {
        "id": "seq-main", "name": "Master",
        "width": 1920, "height": 1080, "fps": 30.0,
        "markers": [ … ], "transitions": [ … ], "ducking_sidechains": [ … ],
        "tracks": [
          { "id": "V2", "type": "video", …,
            "clips": [ { "id": "clip-…", "asset_id": "", "sequence_id": "seq-3f2a…",
                         "timeline_start": 2.0, "source_in": 0.0, "source_out": 4.0, … } ] }
        ]
      }
    ]
  }
}
```

- `sequence_id` n'est écrit que pour un clip imbriqué.
- **Migration** : un fichier v1–v13 (pas de clé `sequences`) est lu comme un
  projet à une séquence `seq-main` portant les pistes, repères, transitions,
  ducking et réglages du fichier. Réenregistré, il devient v14.
- Robustesse : séquence invalide → erreur lisible ; ID dupliqué → la copie
  reçoit un nouvel ID ; `active_sequence_id` inconnu → première séquence ;
  référence inconnue ou cycle → chargé tel quel et signalé.
- Les jobs de la Render Queue mémorisent `sequence_id` / `sequence_name` ;
  l'instantané contient tout le projet, donc les séquences imbriquées sont
  rendues telles qu'à l'ajout.

---

## Historique, navigation, cache

- **Undo/Redo** : création, conversion d'une sélection, insertion, suppression,
  duplication, renommage, déplacement/trim d'un clip imbriqué sont des entrées
  d'historique ordinaires. Les snapshots **partagent les séquences inchangées**
  avec le snapshot précédent (`_snapshot_project`) : éditer une séquence d'un
  projet qui en compte vingt ne copie qu'elle. Un undo reste dans la séquence
  où l'opération a eu lieu.
- **Navigation** (`core/sequence_navigation.py`) : état d'interface, hors
  `.kut` et hors historique (chemin, précédent/suivant, tête de lecture par
  séquence). Changer de séquence ne marque pas le projet modifié.
- **Cache d'aperçu** : l'empreinte d'un segment inclut celle des sous-plans
  qu'il lit, restreints à leur plage. Modifier « Intro » change seulement les
  segments parents qui la montrent ; les autres restent valides. Après une
  édition, `dependent_nested_clip_ids` libère aussi les segments des clips qui
  dépendent de la séquence active.
- **Proxies** : jamais de proxy pour une séquence imbriquée (ce n'est pas un
  média). Les médias **à l'intérieur** sont proxifiés normalement en aperçu.

---

## Performances

`python -m tools.perf.nested_bench --ui --ffmpeg --out docs/perf/nested.json`
(macOS arm64, Python 3.14, 320×180) :

| Scénario | Plan (ms) | Graphe (ms) | Compositions FFmpeg | `active_at` (µs) | Plan d'un segment (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 niveau | 0.04 | 0.04 | 1 | 15 | 0.05 |
| 3 niveaux | 0.09 | 0.07 | 3 | 34 | 0.10 |
| 10 niveaux | 0.27 | 0.20 | 10 | 97 | 0.29 |
| 1 séquence × 20 instances | 0.40 | 0.35 | **1** | 15 | 0.07 |
| Séquence de 2000 clips × 3 | 31.6 | 46 | **1** | 23 | 0.10 |

- Une séquence est composée **une fois** quel que soit son nombre d'instances.
- `active_at` et le plan d'un segment ne dépendent pas de la taille des
  séquences imbriquées (index par séquence) : avant ce réglage, 4.4 ms et
  4.5 ms pour la séquence de 2000 clips.
- Navigation dans la fenêtre : ouvrir une séquence imbriquée puis revenir au
  parent ≈ 12 ms ; convertir une sélection ≈ 3 ms.
- Export FFmpeg réel (4 s, 320×180, ultrafast) : 1 niveau 145 ms, 3 niveaux
  277 ms, 10 niveaux 653 ms, 6 instances 515 ms.

---

## Ajouter une fonctionnalité compatible

1. **Données de timeline** (nouveau champ de piste, de repère…) : placez-les
   dans `Sequence`, pas dans `Project`, et sérialisez-les dans
   `_sequence_to_dict` / `_deserialize_sequence`. Une donnée vraiment globale
   (médias, presets) reste dans `Project`.
2. **Parcours de tout le projet** (usages, nettoyage) : `project.all_tracks()`
   ou `project.sequences`, jamais `project.tracks` (séquence active seulement).
3. **Propriété de clip rendue** : ajoutez-la à `RenderLayer`/`AudioLayer` et
   lisez-la dans `_build_layer_filter`/`_build_audio_filter`. Elle s'applique
   alors aux clips imbriqués comme aux médias, après le composite interne.
4. **Nouveau type de couche** dans le plan : il doit être produit par
   `_PlanBuilder.build` (donc aussi dans les sous-plans) et consommé par
   `_compose_plan_graph` avec le préfixe de labels. Ajoutez-le à
   `fingerprint_plan` et, s'il lit un fichier, à `_build_input_list`,
   `media_identity` et `apply_path_resolver` (qui parcourent les sous-plans).
5. **Opération qui dépend de la source d'un clip** (durée max, type) : utilisez
   `clip_source_limit` / `clip_media_type` plutôt que de chercher un média.
6. **Nouvelle opération utilisateur** : modifiez le projet puis appelez
   `_record_history(label)` : la politique de durée et l'invalidation des
   dépendants s'appliquent automatiquement.

---

## Limites connues

- Les sous-titres d'une séquence imbriquée sont incrustés sur l'image finale :
  ils suivent la position temporelle du clip, pas son transform ni ses effets.
- Le moniteur **temps réel** (lecture, avant pré-rendu des segments) montre le
  média supérieur de la séquence imbriquée avec le transform du clip imbriqué ;
  la composition exacte de tous les niveaux vient des segments fidèles et de
  l'export.
- En export complet, un sous-plan couvre toute la séquence même si ses
  instances n'en lisent qu'une partie (les segments d'aperçu, eux, sont
  fenêtrés).
- Volume de piste : lors d'une conversion de sélection, le volume de la piste
  qui accueille le clip imbriqué s'applique à **tout** l'audio de la séquence ;
  les réglages des autres pistes sont recopiés dans la séquence.
- Les transitions qui relient un clip sélectionné à un clip non sélectionné
  sont retirées lors d'une conversion.
- Une conversion qui **sépare des clips liés** est refusée (rien n'est modifié) : un calque dont le parent ou le
  groupe resterait dehors, ou un clip dont la source de tracking resterait dehors. Sélectionnez-les ensemble.
- Dans une séquence imbriquée, le fond est **transparent** : un *adjustment layer* n'agit que là où il y a du contenu
  et ne masque pas la piste parente ; un mode de fusion (Produit, Incrustation…) affiche le calque tel quel sur le
  vide et ne se mélange qu'avec le contenu de la séquence (formule W3C pondérée par l'opacité du dessous).
- L'audio rendu de la séquence imbriquée passe par le plan de rendu (l'ancien `core.audio_mixer.mix_at`, qui ignorait
  les clips imbriqués et n'était consulté nulle part, a été supprimé).
- Le partage de structure de l'historique compare les séquences inactives à
  chaque enregistrement (coût proportionnel à leur taille, sans copie).
