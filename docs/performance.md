# Couche de performance

Kut-Studio reste fluide sur des projets lourds grâce à quatre briques qui
s'appuient sur les systèmes existants (file de tâches, caches, profils de
performance, index de timeline, qualité d'aperçu) au lieu d'en créer de
nouveaux :

```
                       ┌────────────── Préférences › Performance ───────────────┐
                       │ proxies on/off · profil · budget cache · purges        │
                       └───────────────────────────┬────────────────────────────┘
                                                   │
 ProxyManager ── états / génération FFmpeg ──┐     │        CacheManager (budget disque, LRU, purge, stats)
 (core/proxy_*.py)                           │     │          ├─ MemoryCache      sondes · miniatures · ondes
                                             ▼     ▼          ├─ DiskPreviewCache segments d'aperçu fidèles (index)
 aperçu : resolve(path) → proxy valide | original  │          └─ ProxyManager     proxies (LRU, projet ouvert épinglé)
 export : TOUJOURS l'original                      │
                                                   ▼
 PrefetchPlanner ─▶ PreviewEngine (priorités, abandon des segments lointains)
 AdaptiveQuality ─▶ qualité d'aperçu Auto (baisse temporaire, remontée progressive)
 SpanIndex / SnapIndex ─▶ timeline (culling, sélection, aimantation en O(log n + k))
```

| Brique | Modules |
| --- | --- |
| Proxies | `core/proxy_profiles.py`, `core/proxy_manager.py` |
| Cache | `core/cache_keys.py`, `core/cache_manager.py`, `core/preview_cache.py` (index disque) |
| Préchargement | `core/prefetch.py`, `core/preview_segments.py`, `core/preview_engine.py` |
| Timeline | `core/timeline_spatial.py`, `core/timeline_index.py` (`clips_overlapping`), `ui/timeline_panel_mixins/` |
| Qualité adaptative | `core/preview_adaptive.py`, `core/preview_quality.py` |
| Interface | `ui/performance_settings.py` (préférences), menu contextuel des médias, menu *Fenêtre* |
| Mesure | `tools/perf/` (`synthetic.py`, `bench.py`, `engine_bench.py`), `docs/perf/*.json` |

## 1. Mesurer d'abord

```bash
QT_QPA_PLATFORM=offscreen python -m tools.perf.bench --out docs/perf/after.json
python -m tools.perf.bench --compare docs/perf/baseline.json docs/perf/after.json
```

Projets synthétiques **déterministes** (`tools/perf/synthetic.py`) de 100, 1 000
et 10 000 clips : `short_1t` (une piste, clips courts), `short_8t` (8 pistes,
petits clips nombreux), `long_8t` (8 pistes, clips de 300 s qui se chevauchent).
Mesures : chargement du projet (fichier **et** fenêtre complète), clips
visibles, déplacement de la tête, zoom, défilement, rafraîchissement, sélection,
aimantation, miniatures (cache froid / chaud), accès aux caches, mémoire.

Protocole : médiane de plusieurs répétitions après un passage d'échauffement ;
mesures de la timeline à ~60 % du montage (au tout début, les clips visibles sont
aussi les premiers de la liste, et les parcours linéaires s'arrêtent tôt, ce qui
masque leur coût). La mémoire est mesurée **à part** : `tracemalloc` ralentit
l'interpréteur de ×3 à ×5 et faussait les durées dans une première version du
banc (corrigé, la référence a été régénérée).

`docs/perf/baseline.json` a été produit sur le code **avant** ce chantier
(commit `2795cfd`, worktree propre) ; `docs/perf/after.json` sur le code final.

Les tests (`tests/test_performance.py`) ne vérifient pas des durées (fragiles) mais
la **complexité** : éléments lus, couches construites, parcours de la liste des
clips. Un seul test compare des durées, avec une marge très large.

## 2. Proxies média

Un proxy est une copie légère d'un média lourd, utilisée **uniquement pour
l'aperçu**. Il est généré par FFmpeg (`core/proxy_manager.py`).

**États** (`ProxyState`) : `NONE`, `PENDING`, `GENERATING`, `READY`, `ERROR`,
`STALE` (la source a changé depuis la génération).

**Principes**

- Le projet `.kut` **ne référence jamais un proxy**. Un proxy est retrouvé par le
  *chemin du média* + l'*empreinte du profil*, dans le cache utilisateur. Un
  projet ouvert sur une autre machine n'a simplement pas de proxy et se lit sur
  les originaux ; supprimer le cache à la main ne casse rien.
- L'**export utilise toujours l'original** : le plan de rendu, l'instantané de la
  file de rendu et `resolve_for_export` ne voient jamais un proxy (option de
  débogage explicite `allow_proxy_for_debug=True`, jamais activée par l'interface).
- **Un proxy incomplet n'existe pas.** FFmpeg écrit dans un fichier `.partial` ;
  le proxy n'est promu qu'après une sortie réussie, puis un fichier compagnon
  `.json` est écrit **en dernier** : c'est le marqueur d'achèvement (taille,
  signature de la source, empreinte du profil). Proxy tronqué, marqueur absent ou
  corrompu, profil modifié → jamais utilisé.
- **Retour silencieux à l'original** : proxys coupés, absents, supprimés,
  obsolètes, incomplets, ou (si l'audio est nécessaire) profil sans audio.
- **Média hors ligne** : un proxy prêt reste utilisable pour l'aperçu
  (`source_missing=True`).
- Rien ne bloque l'interface : threads dédiés (distincts de la file des
  miniatures), FFmpeg en processus enfant, **tué** à l'annulation et à la
  fermeture de la fenêtre. Les fichiers partiels d'un arrêt brutal sont nettoyés au
  démarrage suivant.
- Erreurs lisibles : FFmpeg absent, disque plein (`No space left`, `ENOSPC`),
  dossier de cache inaccessible, média introuvable.

**Commandes** : génération pour un média (menu contextuel de la bibliothèque), pour
la sélection (*Séquence › Générer les proxies des clips sélectionnés*), pour le
projet (menu contextuel et Préférences), annulation, régénération, suppression, et
interrupteur global (*Fenêtre › Utiliser les proxies*, Préférences). L'état de
chaque média est visible par une pastille `PX` sur sa carte.

La qualité d'aperçu réduite préfère un proxy **plus léger déjà prêt** (jamais de
génération déclenchée par ce choix).

### Ajouter un profil de proxy

```python
from core.proxy_profiles import ProxyProfile, register_profile

register_profile(ProxyProfile(
    id="ultra_light", name_key="proxy.profile.ultra_light",
    max_height=270, quality=34, audio_bitrate="48k",
))
```

puis ajouter `proxy.profile.ultra_light` (fr/en/es) dans `ui/i18n.py` (sans
traduction, le profil s'affiche sous son identifiant). Un profil fournit :
résolution maximale (jamais d'agrandissement), codec, qualité, préréglage x264,
GOP, audio (`aac` ou `None` pour un proxy muet). Modifier un de ces réglages change
l'**empreinte** du profil : les proxies existants sont reconnus comme absents et
régénérables.

Pour un **nouveau codec** (DNxHR, MJPEG…), enregistrer un `ProxyCodec`
(`register_codec`) : conteneur, format de pixels et constructeur d'arguments. Les
arguments d'encodage vidéo passent par `core.video_encoders.resolve_video_encoder` :
le futur encodage matériel des proxies se branchera au même endroit que celui de
l'export (`ProxyProfile.hardware` est déjà accepté et retombe sur le CPU).

## 3. Stratégie de cache

| Couche | Contenu | Clé / invalidation | Éviction |
| --- | --- | --- | --- |
| mémoire (`MemoryCache`) | sondes, miniatures, ondes | `core/cache_keys.py` : chemin + **signature** du fichier (date + taille) | LRU, budget d'octets (profil) |
| disque « preview » | segments d'aperçu fidèles | clip + plage alignée + qualité + empreinte **du segment** | LRU (index), budget propre + budget global |
| disque « proxies » | proxies médias | chemin + empreinte du profil, marqueur de signature | LRU (dernier usage), budget global ; proxies du projet ouvert **épinglés** |
| disque « tracking » | résultats d'analyse de tracking (`core/tracking_engine.py`) | média + signature + plage + position de départ + zones / réglages + version de l'algorithme | LRU (dernier usage), budget global ; jamais indispensable (les données vivent dans le `.kut`, voir `docs/tracking.md`) |
| disque « flow » | vecteurs de mouvement d'une paire d'images et flux d'images intermédiaires préparés (`core/flow_cache.py`, `core/retime_prepare.py`) | média réellement décodé (un proxy et l'original sont deux entrées) + paire + grille et mise au cadre + moteur ; pour un flux préparé, le plan exact des images (fenêtre comprise) | LRU (dernier usage), budget global ; **jamais** indispensable et jamais dans le `.kut` ; la forme stockée est la définition du résultat, un rendu à chaud est identique à un rendu à froid (voir `docs/optical-flow.md`) |

Doublons supprimés : les clés de miniatures et d'ondes existaient en double
(`media_cache` sans signature, `media_previews` avec) ; elles viennent maintenant
d'**une seule** source (`core/cache_keys.py`). Aucune couche ne duplique le
contenu d'une autre.

- **Invalidation précise.** Un fichier modifié change la clé de *ses* dérivés,
  rien d'autre. Un segment d'aperçu n'est invalidé que par une modification de
  **ses** couches : l'empreinte ne couvre plus tout le plan, et la durée totale du
  montage n'y entre que pour le dernier segment (celui qu'elle peut tronquer).
- **Segments alignés sur une grille de 2 s** : balayer la timeline réutilise le
  cache (avant, les segments démarraient à la position exacte de la tête, donc
  presque jamais réutilisables).
- **Budget global** (`cache_max_gb`, 4 Go par défaut, Préférences › Performance) :
  au-dessus, `CacheManager.enforce()` retire d'abord les segments (recalculables
  en quelques secondes) puis les proxies les moins récemment utilisés qui
  n'appartiennent pas au projet ouvert.
- **Statistiques et purges** : octets par couche, accès mémoire, purge par couche,
  purge complète, purge du projet (ses proxies, segments et dérivés mémoire).
  Tout est recalculable : aucun projet n'est modifié.
- `SignatureMemo` mémorise `os.stat` ~2 s : la timeline interrogeait l'existence
  et la signature des fichiers à chaque défilement (un appel système par clip
  visible et par vignette).

## 4. Préchargement intelligent

`PrefetchPlanner` (pur) estime la vitesse de la tête et choisit les segments :

- **balayage rapide** : seul le segment courant ; le reste de la file est abandonné ;
- **lecture / défilement lent** : le segment courant puis ceux situés **dans le sens
  du mouvement** ;
- **à l'arrêt** : courant, un devant, un derrière, un second devant.

`PreviewEngine` : priorité par segment (le courant passe devant le travail de
fond), promotion d'une demande déjà en file, `cancel_outside` pour abandonner les
demandes lointaines (un rendu déjà démarré n'est jamais interrompu). Pendant la
lecture rien n'est lancé. Les segments sont rendus par un plan **fenêtré** : un
segment de 2 s n'ouvre plus les milliers de médias d'un gros montage.

Un segment est aussi **composé à partir de son début** (`origin`, voir
`ExportEngine._build_filter_complex`) : le fond est coupé à cet instant (`trim`,
horodatages gardés), les flux de calques écartent leurs images antérieures avant
toute conversion, et aucune image de calque n'est rastérisée avant. Auparavant le
graphe composait la timeline depuis 0 puis jetait le début (`-ss` de sortie) : le
coût d'un segment croissait avec sa position. Mesuré sur un montage social de 31 s
(54 calques, 540×960) : segment à 16 s 3,7 → 0,9 s, à 24 s 5,7 → 1,4 s, sorties
identiques au bit près (`tests/test_preview_segment_origin.py`). L'export
(`origin = 0`) garde son graphe à l'octet près. Reste proportionnel à la position :
le décodage d'un clip commencé avant le segment, depuis son propre début (voir
[ADR-0001](adr/0001-moteur-unifie-gpu-adaptatif.md), temps local par calque).

## 5. Timeline sur gros projets

Coûts analysés puis mesurés (voir §7) ; parcours complets remplacés :

| Opération | Avant | Après |
| --- | --- | --- |
| clips à monter au défilement / zoom | balayage de tous les clips | `SpanIndex.query` (bisect par piste) |
| aimantation pendant un glisser | tous les clips, à chaque mouvement | `SnapIndex.nearest` |
| sélection au lasso | tous les clips | candidats par intervalle puis test exact |
| `_layout_children`, `_schedule_previews` | tous les clips | clips montés seulement |
| recherche d'un clip par id | balayage | dictionnaire reconstruit avec `clip_views` |
| plan de rendu d'un segment | tous les clips (×2 par déplacement de tête) | `clips_overlapping` + durée de l'index |
| durée au `seek` | balayage de tous les clips | durée de l'index |
| widget de défilement | un widget réutilisé était restylé à chaque cran | seulement les nouveaux widgets |

Les index sont construits à la demande et **jetés dès que `clip_views` est
remplacé** (donc après chaque `set_project`) ; les tests comparent leurs
résultats à des parcours complets sur des projets aléatoires (y compris les
égalités d'aimantation). Largeur de la grille plafonnée à la limite de Qt.

## 6. Qualité d'aperçu adaptative

`AdaptiveQuality` (pur, déterministe) lit la **cadence réelle des ticks de
lecture** (cible 25 Hz) : une lecture trop lourde retarde les ticks.

- fenêtres de ~1 s évaluées par leur **médiane** (un tick isolé en retard n'agit
  pas) ; baisser d'un cran exige 2 fenêtres surchargées consécutives, remonter en
  exige 6 confortables ; 2 s de repos entre deux changements ;
- jamais en dessous de 1/4 tout seul, jamais au-dessus du niveau de base (profil
  machine) ; pause ou arrêt : retour immédiat au niveau de base ;
- **un niveau forcé par l'utilisateur** (Plein, 1/2, 1/4, 1/8) n'est jamais modifié.

Leviers réels quand le niveau baisse :

- **source plus légère** : un proxy plus léger déjà prêt est utilisé ; sinon la
  génération du profil léger est **demandée en tâche de fond** pour les médias
  actifs (jamais bloquant) et sert dès qu'elle est terminée ;
- pas de recalcul des scopes (un FFmpeg par analyse) pendant la lecture ;
- un avis discret « Aperçu réduit » sur le moniteur.

Limite assumée : tant qu'aucun proxy plus léger n'est prêt, le décodage du média
original n'est **pas** réduit (Qt ne sait pas décoder moins cher) ; le niveau
baissé n'allège alors que les scopes. C'est pourquoi proxies et qualité Auto vont
ensemble.

**Le tic de lecture reste léger, scopes visibles ou non.** Le fil de l'interface
ne fait, pour une analyse de scopes, que lire l'état de l'application (plan à la
tête de lecture, réglages d'export : quelques millisecondes) ; la commande d'image
est fabriquée par le thread de l'analyseur (`ScopeRequest.command_factory`), à la
taille que l'analyse échantillonne (`scope_frame_size`), en ne rastérisant les
calques que jusqu'à l'image voulue (`horizon`). Pendant la lecture, une analyse ne
part que si l'analyseur est libre et l'est resté 4 fois la durée de la précédente
(`BACKGROUND_SHARE`) : l'analyse en Python pur dispute le verrou de l'interpréteur
au fil de l'interface. La tête de lecture avance du temps réellement écoulé (pas
d'un pas fixe de 40 ms) : un tic en retard saute une image au lieu de ralentir la
lecture et de forcer un recalage du lecteur. Mesuré sur l'edit « Singapore GP
2026 » (19 plans, ~60 calques), scopes visibles : pire tic 4,6 s → 8 ms, 1,2 s →
8,0 s de lecture en 8 s (`tests/test_playback_responsiveness.py`).

## 7. Résultats

Voir [`docs/perf/RESULTS.md`](perf/RESULTS.md), et pour le moteur de rendu le §10.

## 8. Robustesse testée

Proxy supprimé à la main, dossier de proxies supprimé, proxy tronqué, marqueur
corrompu, profil modifié, source modifiée (obsolète), média hors ligne, média
déplacé, projet d'une autre machine, FFmpeg indisponible (puis installé), disque
plein (code `ENOSPC` et message FFmpeg), dossier de cache inaccessible, génération
annulée (en file et en cours), fermeture pendant une génération (processus tué,
aucun fichier partiel), proxies absents du fichier projet, cache de segments
supprimé pendant la session, **fichier de cache tenu ouvert** (non supprimable : ni oublié ni compté comme
libéré), **deux instances sur le même dossier de proxies** (chaque génération a son fichier partiel ; le nettoyage ne
retire un partiel qu'au bout de dix minutes sans écriture), **segment d'aperçu vide** (jamais mis en cache).

## 9. Limites connues et pistes

- Les segments d'aperçu sont rendus avec `-ss` en **sortie** : FFmpeg génère donc
  les images précédant le segment avant de les jeter. Le plan fenêtré supprime le
  coût des milliers de médias, pas celui du temps écoulé ; un seek d'entrée par
  couche serait la prochaine étape.
- Les index sont reconstruits à chaque `set_project` (O(n) par modification) ; un
  index incrémental supprimerait ce coût.
- Les snapshots d'historique restent des copies complètes (≈ 2× plus rapides
  qu'avant, toujours O(n) par modification) ; un partage structurel des pistes
  non modifiées est la suite logique.
- Les marqueurs de transition sont encore un widget par transition (pas de
  culling).
- Accélération matérielle : réalisée depuis (encodage : [hardware-encoding.md](hardware-encoding.md) ; décodage et
  aperçu GPU : [gpu-preview.md](gpu-preview.md)).

## 10. Moteur de rendu : calques, graphe FFmpeg, export (2026-10-09)

Mesuré de bout en bout par `tools/perf/engine_bench.py` (scénario social déterministe ; `--project` pour un vrai
montage, en lecture seule). Références : `docs/perf/engine-before.json` (`main` avant ce chantier) et
`docs/perf/engine-after.json`, Apple M4 10 cœurs, FFmpeg 9.0.2.

```bash
QT_QPA_PLATFORM=offscreen python -m tools.perf.engine_bench --out docs/perf/engine-after.json
```

### Ce qui coûtait

Profil d'un TikTok réel de 31 s (1080×1920, 54 calques graphiques), cache des calques vide :

- **Écriture des images de calques : 79 %** des 48 s de préparation (`QImage.save`, 54 ms par PNG pleine trame) ;
- **éléments courts composés pendant tout le rendu** : 7 flashs en Addition de 0,2 à 0,8 s passaient, image
  transparente après image transparente, dans une chaîne de fusion pleine trame pendant 31 s (104 s de CPU et
  6,7 Go de mémoire pour 4 s d'images visibles) ;
- **décodeurs** : un fil par cœur et par média, chaque fil gardant ses images (1,5 Go pour 19 plans) ;
- **`rotate` sur les plans fixes** : un cadre de 2 203 px, et un demi-pixel de flou (voir *Corrigé*).

### Ce qui a changé

| Changement | Module | Images |
| --- | --- | --- |
| Encodage PNG dans `WRITE_WORKERS` fils (`QImage.save` libère le verrou de l'interpréteur), file bornée | `core/mograph_stream.py` | octets identiques |
| Élément graphique limité à ses images visibles ; fusion calculée sur le dessous coupé au rang d'image, complétée par des images transparentes d'une source indépendante (`overlay` n'attend jamais en retenant le dessous) | `core/mograph_stream.py`, `core/mograph_ffmpeg.py` | identiques au bit près (FFmpeg 9 et 7.1, `tests/test_mograph_span.py`) |
| Budget de fils de décodage : plusieurs médias se partagent les cœurs (2 fils au moins), 2 par liste d'images | `core/export_engine.py` (`decoder_threads`, `input_arguments`) | identiques |
| Plus de `rotate` pour un plan qui ne tourne jamais, ni de `colorchannelmixer` pour un plan opaque | `core/export_engine.py` | plus nettes (voir *Corrigé*) |

Corrigé au passage :

- les listes `.ffconcat` déclarent leur cadence (`option framerate`, d'où `-f concat -safe 0`) : une image PNG
  s'ouvre à 25 i/s et, à 30 i/s, une image d'animation sur six était sautée ;
- un plan fixe était posé au centre d'un cadre de côté impair (1080p, 720p) : décalé d'un demi-pixel et moyenné
  sur 2×2 pixels (netteté −12 %).

### Résultats

Premier export (cache des calques vide), H.264 CPU CRF 18, `medium` :

| Montage | Avant | Après | Gain |
| --- | ---: | ---: | ---: |
| Banc social (12 s, 1080×1920) | 44,0 s | 26,6 s | −39 % |
| TikTok réel, 54 calques (31 s) | 98 s | 41 s | −58 % |
| TikTok réel, 19 plans Ken Burns (35 s) | 85 s | 52 s | −38 % |
| Présentation, 96 calques (35 s, 1920×1080) | 49 s | 20 s | −60 % |

FFmpeg seul (cache des calques plein, ce que coûte un nouvel export après une retouche) :

| Montage | Durée | CPU | Mémoire |
| --- | ---: | ---: | ---: |
| Banc social | 15,5 → 9,4 s (−40 %) | 114 → 80 s (−29 %) | 5,4 → 1,8 Go (−67 %) |
| TikTok, 54 calques | 49,8 → 27,7 s (−44 %) | 348 → 252 s (−28 %) | 4,5 → 2,4 Go (−47 %) |
| TikTok, 19 plans | 31,7 → 27,1 s (−15 %) | 250 → 220 s (−12 %) | 4,5 → 3,0 Go (−32 %) |
| Présentation | 8,3 → 8,2 s | 60 → 61 s | 0,87 → 0,79 Go |

Segments d'aperçu fidèle (2 s, qualité standard) : −19 à −44 % sur le banc, −25 à −38 % sur le TikTok à 54 calques,
−6 à −16 % ailleurs.

### Ce qui reste

- **Encodage** : x264 `medium` domine maintenant l'export des montages vidéo (Présentation, 19 plans). L'encodeur
  matériel (`hardware-encoding.md`) est le levier, pas le graphe.
- **Dessin des calques à froid** : le contour des sous-titres (`QPainter.strokePath`, 4 ms par appel) domine. Les
  appels `QPainter` gardent le verrou de l'interpréteur (mesuré : aucun gain à dessiner dans plusieurs fils) ; la suite
  est l'étape « calques en textures » de l'[ADR-0001](adr/0001-moteur-unifie-gpu-adaptatif.md).
- **Plans à échelle animée** (Ken Burns, zoom d'impact) : `rotate` y sert de cadre fixe (2 384 px pour du 1080×1920),
  avec le même demi-pixel quand la largeur mise à l'échelle est impaire.
