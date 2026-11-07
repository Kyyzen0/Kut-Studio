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
 (core/proxy_*.py)                           │     │          ├─ MemoryCache      sondes · miniatures · enveloppes audio
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
| Profil machine | heuristique cœurs/RAM (`core/runtime_profile.py`) ; banc mesuré en cache (`core/machine_bench.py`, `machine-profile.json`) pour Auto |
| Régulateur | `core/runtime_regulator.py` (échelle de dégradation), `core/power_state.py` (batterie / économie / chauffe ; relevé sur un thread de travail, jamais sur celui de l'interface), `core/preview_governor.py` (charge GPU) |
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
| mémoire (`MemoryCache`) | sondes, miniatures, enveloppes audio (formes d'onde, une par fichier : `core/audio_envelope.py`) | `core/cache_keys.py` : chemin + **signature** du fichier (date + taille) | LRU, budget d'octets (profil) |
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
(`origin = 0`) garde son graphe à l'octet près. Un clip vidéo **entamé** avant le
segment est coupé avant sa chaîne (`trim` à ``source_in + skip``, horodatages
locaux conservés) : effets et conformation ne traitent plus ce qui précède
(comme l'audio depuis le mixage en direct). Le décodage FFmpeg peut encore
remonter à une image clé antérieure ; un ``-ss`` d'entrée par média reste une
piste d'optimisation.

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

- Les segments d'aperçu gardent un `-ss` en **sortie** pour caler les timestamps
  du fichier segment ; la composition (`origin`) et la coupe des clips entamés
  évitent de traiter ce qui précède. Un `-ss` d'**entrée** par média (décodage
  seul) reste une piste d'optimisation.
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

## 11. Mémoire d'un export : médias découpés et multicam (défaut D-002 de la recette, 2026-10-21)

**Constat.** Le banc de recette a vu l'export d'un montage multicam de 20 min (3 angles, 50 clips) dépasser 9 Go puis
remplir le disque par le swap, alors que la sortie pèse 0,66 Go. Le processus Python n'y est pour rien (133 Mo mesurés) :
**c'est FFmpeg**.

### La cause (mesurée)

Les clips d'un même média partageaient une seule entrée FFmpeg, et les segments d'un même angle multicam un seul rendu
(`split`). Chaque clip est une fenêtre `trim` de ce flux, posée dans la chaîne d'`overlay` du plan. `overlay` n'émet son premier
cadre qu'une fois la première image de **chaque** étage reçue : FFmpeg décode donc le flux partagé jusqu'au clip qui commence le
plus tard dans la source, et garde en RGBA pleine trame tout ce que les autres clips lisent avant. La sortie reste à 0 s pendant
que la mémoire monte, puis l'export avance.

**La loi, avant le correctif** (`M` : plus grand point d'entrée source de la famille, même fichier ou même angle) :

    file  ≈  Σ_k  max(0, min(fin_k, M) − début_k) / (fin_k − début_k) × durée_k × cadence × largeur × hauteur × 4

| Montage (1080p, 25 i/s, FFmpeg 9.0) | Pic FFmpeg avant | Pic FFmpeg après |
| --- | ---: | ---: |
| 1 clip de 130 s (référence) | 0,85 Go | 0,85 Go |
| 1 clip coupé en deux à 12 s | 3,3 Go | 0,88 Go |
| 1 clip coupé en deux à 20 s | 4,9 Go | 0,86 Go |
| 1 clip coupé en deux à 60 s | 5,8 Go | 0,88 Go |
| multicam 58 s, 3 angles, 6 segments (raw) | 4,5 Go | 1,2 Go |
| multicam 20 min, 3 angles, ≈ 30 changements | > 200 Go (estimé ; 9,9 Go à t = 696 s, sans fin) | voir « Mesures » |

Même mécanisme sous FFmpeg 6.1 et 7.1 (Docker amd64), avec une pente plus faible sous 6.1 (voir « Précisions de l'estimation ») : la structure du graphe, pas la version.

### Le correctif : une entrée par clip, un rendu par fenêtre (`core/export_inputs.py`)

* **Une entrée positionnée par clip vidéo simple d'un média chaud.** Un média est *chaud* quand les clips qui le lisent en
  retiennent plus de 0,5 s de média avant le clip qui commence le plus tard (la loi ci-dessus). Chaque clip a alors sa propre
  entrée FFmpeg : `-ss <secondes entières> -t <durée> -i`, **un seul fil de décodage**, et sa chaîne de filtres est celle d'avant,
  `trim` décalé de la position de l'entrée. L'entrée démarre une seconde avant le clip (convergence du décodeur) ; le décalage est
  un **nombre entier de secondes**, donc un multiple exact de toute base de temps `1/N` : les comparaisons d'horodatages du `trim`
  donnent les mêmes images. Un média *froid* garde l'entrée partagée : le graphe est celui d'avant, au caractère près.
* **Une composition par fenêtre pour les séquences imbriquées chaudes.** Chaque instance (chaque segment d'un angle multicam) est
  composée **pour sa fenêtre seule**, comme un segment d'aperçu de la racine : le fond ne part qu'à son début (`origin`) et
  s'arrête avec elle (+ 0,5 s : FFmpeg 6.1 perd la dernière image d'un rendu imbriqué). Son clip garde son **temps absolu** :
  `setpts=PTS-<horodatage de la première image du clip>`, sondé par `ffprobe`. Remettre le temps à 0 sur la première image
  gardée, comme le fait le graphe d'avant, ne donne le même temps que si l'instance commence au début du clip ; et le point
  d'entrée ne suffit pas : **FFmpeg 6.1 et 7.1 décalent de +21 ms toutes les images d'un conteneur dont l'audio commence avant la
  vidéo** (amorce AAC à −21 ms, Matroska produit par FFmpeg 9), pas FFmpeg 9. Faute de sonde, l'instance n'est pas créée.
* **Le son n'est jamais positionné.** Le décodeur AAC tire son bruit de substitution (PNS) d'un état aléatoire qui dépend de tous
  les paquets déjà décodés : un décodage qui part du milieu d'un fichier diffère de quelques échantillons du décodage continu
  (mesuré : jusqu'à 0,012 sur un signal de 0,3, sur un fichier de bruit). Le correctif ne touche donc pas le son, et il n'en a pas
  besoin : l'audio ne forme pas la file des images (`amix` lit ses entrées au fil de l'eau). Mesuré, un média de 20 min coupé à 5,
  10 ou 15 min garde 61 Mo de FFmpeg, comme sans coupe ; le multicam de 590 s garde la même mémoire que celui de 60 s.
* **Un plafond d'entrées par la mémoire** (`max_pipelines`, 6 Go de budget, 8 à 96 entrées selon la taille du cadre : 86 en 1080p,
  21 en 4K, 8 en 8K) : une entrée ouverte garde de 70 à 140 Mo en 1080p (un seul fil : 70 ; deux fils : 140), une instance en garde
  deux fois autant (entrée + chaîne imbriquée). Au-delà, les clips qui retiennent le plus gardent leur entrée, les autres restent
  sur l'entrée partagée.
* **Restent partagés** : images fixes, clips remappés ou figés (leur étage de temps lit la source entière), listes `.ffconcat`,
  séquences imbriquées qui contiennent autre chose que des clips de fichier simples (calques, transitions, masques, fenêtres de
  nœud de couleur, paramètres animés par `sendcmd`, autre séquence imbriquée), fichiers sans sonde, et **les conteneurs dont l'index
  des images clés n'est pas fiable** : seuls MP4 / MOV et Matroska / WebM sont positionnés. MPEG-TS (`.ts`, `.mts`, `.m2ts` des
  caméscopes) déduit ses points d'accès du flux : positionné, FFmpeg 6.1 et 7.1 reprennent le décodage sur une image qui n'est pas de
  rafraîchissement (« co located POCs unavailable ») et les images diffèrent (mesuré) ; AVI, MPEG-PS, FLV et les flux bruts n'ont pas
  été mesurés. Leur file reste estimée par `core/export_memory.py`.

### Parité de rendu

Zéro différence mesurée : `RENDER_ENGINE_VERSION` reste à 13 (les empreintes de segments d'aperçu portent sur le plan, pas sur le
graphe). `tests/test_export_inputs_real.py` rend chaque montage avec le graphe d'avant (`max_pipelines` ramené à 0) et le nouveau,
avant tout encodeur, et compare le `framemd5` de chaque image et le `md5` du mixage flottant : coupe simple, coupes entre deux images,
ordre inversé avec trou et calque réduit au-dessus, Matroska, MPEG-TS (début à 1,4 s), cadence variable, cadence du projet différente
de celle des médias, clip accéléré, images clés d'échelle et de rotation, effet animé par `sendcmd`, segment d'aperçu, multicam
(trois angles, huit segments hors grille), séquence imbriquée générique relue plus loin (avec échelle, rotation et opacité animées).
Vérifié sur FFmpeg 6.1.1, 7.1.5 (Docker amd64) et 9.0.2. Chaque correctif a sa mutation : sans le décalage, sans la marge de fin
(FFmpeg 6.1), sans la marge de tête, sans l'horodatage sondé (FFmpeg 6.1 : multicam) ou sans le plafond, un test échoue.

### Mesures

FFmpeg 9.0.2 (macOS arm64), export complet vers H.264 sauf mention ; pic de mémoire du seul processus FFmpeg (`ps`), jamais plus
de 6 Go (un essai qui les dépasse est arrêté), un seul FFmpeg à la fois, médias hors dépôt supprimés à la fin.

**Un média coupé** (130 s en 1080p, tableau plus haut) : le surcoût de la coupe passe de 2,4 / 4,1 / 4,9 Go à **0,03 / 0,01 / 0,03 Go**,
quelle que soit sa position. En 480 × 270 (60 s), une coupe à 15 s et à 45 s coûtaient 195 et 572 Mo de plus qu'un clip seul ; la
même coupe ne coûte plus rien d'une position à l'autre (`tests/test_export_memory.py`, par rapport entre deux positions).

**Un multicam à trois angles et 29 changements d'angle** (640 × 360, 25 i/s, son compris) : la mémoire ne dépend plus de la durée.

| Durée du montage | Avant | Après (33 entrées ouvertes, 61 descripteurs) |
| ---: | ---: | ---: |
| 60 s | 1,76 Go (11 s) | 1,05 Go (9 s) |
| 120 s | 1,85 Go (23 s) | 1,05 Go (13 s) |
| 300 s | > 6,0 Go, arrêté à 46 s | 1,04 Go (27 s) |
| 590 s | non lancé (> 6 Go) | 1,03 Go (47 s) |

À 1080p, 58 s, six segments : 3,1 à 4,5 Go avant (selon l'essai) → 1,2 Go ; onze segments : 2,1 Go (avant : 4,3 Go à plus de 6 Go).
**Loi mesurée après** : mémoire ≈ base (0,25 Go) + Σ entrées ouvertes × coût d'une entrée **à la taille de SA composition** (une séquence 4K imbriquée dans un projet 1080p pèse 4 entrées de la racine ; la décision de scinder et l'estimation comptent les mêmes entrées, `core/export_inputs.py`),  **indépendante de la durée** ; le coût
d'une entrée suit la taille du cadre : 70 Mo en 1080p avec un seul fil de décodage (140 Mo avec deux : d'où `-threads 1` sur les
entrées positionnées), 90 à 175 Mo pour une instance de séquence imbriquée (entrée + chaîne imbriquée + `overlay` du plan),
environ 10 Mo en 480 × 270. Un banc isolé (16 entrées 1080p, `overlay` en chaîne) donne 140 Mo avec deux fils, 72 avec un, et 50 Mo
pour des entrées lues par un `concat`, soit le même ordre : la mémoire d'une entrée ouverte ne se réduit pas en changeant la
structure du graphe.

**Beaucoup de clips** (une seconde chacun, deux fichiers, une piste, 480 × 270) :

| Clips | Avant | Après |
| ---: | ---: | ---: |
| 50 | 1,19 Go, 80 s | 0,62 Go, 9 s (52 entrées, 80 descripteurs) |
| 100 | — | 1,57 Go, 111 s (98 entrées, 126 descripteurs) |
| 150 | — | 0,60 Go, > 4 min (98 entrées) : au-delà du plafond, le reste garde l'entrée partagée |
| 200 | 0,22 Go, > 15 min | 0,46 Go, > 15 min (98 entrées) |

À 1080p (sans encodeur) : 10 / 20 / 40 clips ouvrent 12 / 22 / 37 entrées et gardent 0,94 / 1,85 / 2,57 Go, de 70 à 90 Mo chacune. Le
nombre de descripteurs suit les entrées (30 + 1 par entrée) : le plafond de 96 reste sous les 256 de macOS.

**Le son** : un média de 20 min (160 × 90) coupé à 5, 10 ou 15 min : 61 Mo, comme sans coupe (58 Mo). Aucune file de son.

### Ce qui reste coûteux

* **Le nombre de morceaux ouverts à la fois**, pas leur durée : 70 à 140 Mo chacun en 1080p. Au-delà du plafond les clips restent sur
  l'entrée partagée, avec la file d'avant (et un export très long : voir D-003 ci-dessous).
* **Les clips remappés, les images fixes et les séquences imbriquées non simples** gardent l'entrée ou le rendu partagé.
* **Le temps** : la chaîne d'`overlay` reste longue d'un étage par clip, chaque cadre la traverse. Un montage de 50 clips d'une
  seconde de deux fichiers (480 × 270) passe de 80 s à 9 s ; 100 clips : 111 s pour 100 s de montage ; 150 clips ou plus, au-delà du
  plafond, ne finissent plus en 4 min (la file qui reste partagée). Cela rejoint D-003 (90 clips de quelques médias) : à remesurer
  sur le montage reproduit.
* **Le refus avant FFmpeg** (`core/export_memory.py`) suit la nouvelle structure : estimation nulle pour un clip seul, des médias
  différents ou des morceaux pris au même endroit ; sinon le coût des entrées ouvertes, plus la file des flux restés partagés.

### Précisions de l'estimation (taille des images, plans imbriqués, mémoire disponible)

Ces précisions valent pour la file des flux qui **restent** partagés (au-delà du plafond d'entrées, conteneurs non positionnés) et pour
le graphe d'avant le correctif, que `tests/test_export_memory.py` rejoue en ramenant le plafond à 0 (fixture `legacy`).

- **Taille des images en file : celle du cadre, pas celle après transformation.** Mesuré (480×270, coupe à 15 s, surcoût de
  FFmpeg 9.0) : 196 Mo sans transformation, 191 Mo à l'échelle 0,5, 209 Mo à l'échelle 3, 199 Mo à 45°, 200 Mo en cadrage
  « remplir » (vertical, média 16:9), 240 Mo pour une échelle animée 1 → 3. Une échelle 3 dont les images agrandies feraient 1,7 Go
  tient dans 700 Mo sous FFmpeg 6.1 et 7.1 (Docker) : la file se forme avant le `scale` / `rotate` du clip. L'estimation compte
  donc `largeur × hauteur × 4` de la composition **sans** facteur d'échelle (la première version réduisait la file d'une échelle
  < 1 : c'était une sous-estimation, corrigée).
- **Familles sur toute la hiérarchie.** Un fichier lu par la racine et par une séquence imbriquée est une seule entrée FFmpeg
  (`_build_input_list`) ; la famille est donc calculée sur tous les plans, avec le chemin du fichier (ou la clé de la séquence
  imbriquée, un seul `split`) comme clé. Le décodage avance dans le temps du fichier : le décalage d'imbrication et la vitesse ne
  déplacent pas `M`, ils ne changent que la durée sur la timeline du calque (déjà dans la formule). Les images d'un plan imbriqué
  sont comptées à la taille de **sa** composition.
- **Mémoire disponible, pas installée.** La référence est `available_bytes` de `core/memory_monitor.py` (Linux `MemAvailable`,
  Windows `AvailPhys`, macOS le pourcentage libre du noyau), 85 % de cette valeur. Compromis de stabilité : la mesure est un instantané
  (un navigateur qui grossit la change d'un lancement à l'autre), donc la référence ne descend jamais sous **25 % de la mémoire installée**.
  Un export qui passait deux minutes plus tôt n'est ainsi refusé que si la file est nettement hors de portée (celle de D-002 vaut
  dix fois la machine). Mesure impossible : la moitié de la mémoire installée ; rien du tout : 16 Go. Le message dit ce qui est mesuré
  (« mémoire disponible sur cette machine ≈ 9 Go sur 24 Go », ou « disponible non mesurée »).

- **Selon la version de FFmpeg** (surcoût d'une coupe à 15 / 30 / 45 s, 480×270, médiane de 3, au-dessus du même montage sans
  coupe ; estimé 185 / 371 / 556 Mo) : FFmpeg 9.0 et 7.1.5 donnent 191 / 377 / 563 Mo (à 3 % de l'estimation) ; **FFmpeg 6.1.1 donne
  430 / 535 / 640 Mo** (natif arm64 et amd64 émulé : 414 / 524 / 635 Mo), c'est-à-dire une pente de 7 Mo/s au lieu de 12,4 (57 % de
  l'estimation) et environ 245 Mo fixes en plus (la seconde chaîne de filtres), quelle que soit la transformation du clip. Sous 6.1
  l'estimation surestime donc la pente de 1,7 fois : c'est un majorant, jamais une sous-estimation. Les tests comparent des **pentes**
  (différence entre deux coupes du même montage : le décalage fixe disparaît) : l'estimation ne doit jamais sous-estimer la pente
  mesurée et ne la surestime pas de plus de 2,2 fois.

### Ce que l'estimateur NE couvre PAS (sous-estimations connues)

- **Clips remappés dans le temps** (vitesse, courbe, sens inverse, flux optique) : leur chaîne passe par un `TimeMap` ; ils sont
  ignorés (file supposée nulle), alors que leur flux partagé met aussi des images en file.
- **Images fixes** (photos) : `loop` ne met qu'une image en file ; ignorées.
- **Compositions nodales** et leurs sources : ignorées (elles restent sur le rendu partagé, jamais composées par instance).
- **Transitions** (`xfade`) : leurs branches peuvent retenir des images en plus ; non comptées.
- **Effets qui changent la taille de l'image** (cadres de flou, pavés, stabilisation / suivi avec marge) : l'image est supposée de la taille
  du cadre ; une échelle animée ajoute quelques images de la taille du cadre agrandi (≈ +25 % mesuré à 1 → 3).
- **Calques graphiques (listes `.ffconcat`) et mattes** : sources indépendantes, jamais comptées ; ils ne partagent pas de décodeur,
  mais peuvent retenir des images si leur étage attend.
- **Plan imbriqué utilisé seulement en partie** : tous ses calques sont comptés (surestimation possible).
- **Multicam** : de −20 % à +40 % mesurés sur le graphe d'avant ; le son (`amix`, `adelay`) ne forme pas de file (mesuré : 61 Mo pour un média de 20 min coupé à 5, 10 ou 15 min).
- **Mémoire de FFmpeg hors file** (décodeurs, encodeur, ~1 Go) : non ajoutée.
