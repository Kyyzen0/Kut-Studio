# Multicam

Un projet Kut-Studio peut regrouper plusieurs caméras et enregistreurs d'un même tournage dans une **source Multicam**,
les synchroniser (timecode, son, repère, début des clips ou à la main), les regarder ensemble, puis **monter le programme
en cliquant sur les angles — ou en appuyant sur `1`–`9` — pendant la lecture**.

L'ambition est celle d'un Multicam de Premiere ou de Resolve, en plus simple : pas de mode spécial, pas de fenêtre
séparée où l'on « enregistre » un montage. Chaque coupe est une coupe de clip ordinaire, donc tout ce que l'on sait
faire d'un clip (rogner, couper, déplacer, transitions, effets, imbrication, annuler) fonctionne sans cas particulier.

Sommaire : [Guide rapide](#guide-rapide) · [Principe](#principe--rien-de-neuf-à-côté-de-lexistant) · [Modèle](#modèle) ·
[Création](#création) · [Synchronisation](#synchronisation) · [Moniteur](#le-moniteur-multicam) ·
[Bascule en direct](#bascule-en-direct) · [Audio](#audio) · [Aperçu et export](#aperçu-et-export) ·
[Fichier `.kut`](#fichier-kut) · [Hors ligne et relink](#angles-hors-ligne-et-relink) ·
[Aplatir, dupliquer, imbriquer](#aplatir-dupliquer-imbriquer) · [Performances](#performances) ·
[Diagnostic](#diagnostic) · [Tests](#tests) · [Limites connues](#limites-connues) ·
[Architecture future](#architecture-future) · [Ajouter une fonctionnalité compatible](#ajouter-une-fonctionnalité-compatible)

## Guide rapide

1. **Créer.** Dans la bibliothèque, sélectionnez les rushs (caméras et enregistreurs), clic droit →
   *Créer une séquence Multicam…* (même entrée dans le menu *Timeline*). Ou, depuis la timeline, sélectionnez des clips posés sur des pistes différentes.
   La boîte propose la méthode de synchronisation la plus fiable *parmi celles qui sont possibles* pour ces médias,
   les noms d'angles déduits des fichiers, et la politique audio.
2. **Synchroniser.** La synchronisation par le son tourne en arrière-plan (barre de progression, annulable). Un résumé
   dit, angle par angle, ce qui est **excellent / bon / incertain / échoué** : un angle dont la mesure n'est pas fiable
   n'est jamais présenté comme réussi, et reste réglable à la main.
3. **Regarder.** Menu *Timeline → Moniteur Multicam* (`Ctrl+Maj+M`) : tous les angles à la fois, plus la sortie
   programme. Les angles hors ligne affichent `MEDIA OFFLINE`, sans empêcher les autres de jouer.
4. **Monter.** Lecture, puis `1`–`9` (ou un clic sur une vignette) : le programme bascule *à cet instant*. Rien n'est
   enregistré à part : la timeline porte déjà les segments. Annuler (`Ctrl+Z`) défait une bascule.
5. **Affiner.** Les segments se rognent, se déplacent et se coupent comme n'importe quel clip ; *Remplacer par l'angle* du
   menu contextuel d'un segment change l'angle d'un segment sans le couper ; *Réglages Multicam…* corrige un décalage, renomme ou
   recolore un angle, change la politique audio, ajoute ou retire un angle, relance la synchronisation.

## Principe : rien de neuf à côté de l'existant

```
Projet
├── médias (partagés)
├── Séquence « Master »                      ← montage (séquence active)
│     V1 : [ Cam 1 ][ Cam 1 ][ Cam 3 ][ Cam 2 ]   ← clips imbriqués de « Concert », chacun avec un angle_id
└── Séquence « Concert »  (source Multicam)  ← Sequence.multicam décrit les angles
      V1 : Cam 1  ────────────────────────
      V2 : Cam 2        ──────────────────
      V3 : Cam 3            ──────────
      A1 : Recorder  ─────────────────────────
```

* Une **source Multicam** est une `Sequence` ordinaire (`core/project_model.py`) dont le champ `multicam`
  (`core/multicam_model.py`) liste les **angles**. Un angle = **une piste** : vidéo pour une caméra (le clip porte l'image
  et le son de la caméra, comme partout dans le projet) ou audio pour un enregistreur externe.
* Le **décalage de synchronisation d'un angle est la position de ses clips** dans la séquence. Il n'est stocké nulle
  part ailleurs : déplacer un angle à la main, c'est déplacer ses clips (annulable comme n'importe quel déplacement),
  la synchronisation automatique ne fait que proposer ces positions. Il n'existe donc pas de second « décalage » qui
  puisse diverger de ce qu'on voit.
* Un **segment du montage** est un clip imbriqué ordinaire (`Clip.sequence_id` = la source) portant `Clip.angle_id`.
  Couper, déplacer, rogner, ajouter un effet ou une transition, imbriquer, dupliquer, annuler : tout fonctionne sans cas
  particulier. **Changer d'angle = couper à la tête de lecture et donner un autre `angle_id` à la moitié droite.**
* L'**étalonnage, la LUT, la transformation, le recadrage et la stabilisation d'une caméra** sont portés par le clip de
  l'angle *dans la source* : ils s'appliquent à toutes ses apparitions sans être recopiés à chaque coupe.
* **Un seul moteur de rendu** : le `RenderPlan` construit le sous-plan de la source en omettant les couches des angles
  inactifs (`core/multicam.py` : `TrackFilter`). Seul l'angle actif d'un segment est donc rendu ; deux segments du même
  angle partagent un sous-plan ; une transition entre deux angles en rend deux, naturellement.

## Modèle

Les types vivent dans `core/multicam_model.py` ; les opérations dans `core/multicam_ops.py` ; les lectures (angle actif,
échantillons du moniteur, anomalies) dans `core/multicam.py`. Aucune n'importe Qt.

| Objet | Rôle |
| --- | --- |
| `MulticamSource` | `Sequence.multicam` : `angles` (ordre = numérotation), `audio` (politique), `sync_method`. 64 angles au plus. |
| `MulticamAngle` | `id` stable (référencé par `Clip.angle_id`), `name`, `track_id`, `color_index` (rang dans la palette de l'interface, jamais une couleur), `sync_method`, `sync_status`, `sync_confidence`. |
| `MulticamAudio` | `mode` : `FOLLOW_VIDEO` (le son suit l'angle), `FIXED` (une source principale, `angle_ids[0]`), `MIX` (plusieurs sources). |
| `SyncMethod` | `timecode`, `audio`, `marker`, `start`, `manual`, `positions`. |
| `SyncStatus` | `none`, `excellent`, `good`, `uncertain`, `failed`, `manual`. |

Un angle inconnu (`angle_id` non vide qui ne désigne aucun angle) rend le segment **vide** et le signale
(`multicam_issues`, statut `angle_missing` dans la timeline) ; un `angle_id` vide désigne le premier angle. Les pistes
qui n'appartiennent à aucun angle (graphiques, sous-titres, calque d'effets ajoutés dans la source) sont **toujours**
rendues. Les couleurs d'angle sont des rangs dans `ThemePalette.angle_colors` : changer de thème recolore sans toucher
au projet.

Les opérations (chacune est **une** entrée d'historique, et ne modifie rien si elle est refusée) :
`create_multicam_source`, `create_multicam_from_clips`, `insert_multicam_clip`, `switch_angle`, `replace_angle`,
`apply_sync`, `set_angle_offset`, `rename_angle`, `set_angle_color`, `set_audio_policy`, `add_angle`, `remove_angle`
(avec un angle de remplacement pour les segments qui le montrent), `flatten_multicam_clip`.

**Pas de Multicam dans une Multicam** : un angle est un média ou une séquence ordinaire. `check_can_nest`
(`core/sequences.py`) refuse l'imbrication d'une source Multicam dans une autre, ce qui évite toute récursion
incontrôlée sans détecteur de cycle supplémentaire. En revanche une séquence de *montage* qui contient des segments
Multicam s'imbrique normalement dans une autre séquence : la bascule d'angle reste alors celle de la séquence interne.

## Création

Deux entrées, un même chemin (`ui/main_window_mixins/multicam_creation.py`) :

* **Depuis la bibliothèque** — sélection multiple (le panneau accepte la sélection étendue), clic droit.
* **Depuis la timeline** — clips sélectionnés sur des pistes différentes ; les positions actuelles sont une méthode de
  synchronisation à part entière.

`ui/multicam_dialogs.py` ne montre que les méthodes **possibles** pour les médias choisis (`available_methods`) et
préselectionne la plus fiable (`default_method`) : les positions actuelles quand on part de la timeline ; sinon le
timecode si tous les médias en portent, sinon le son si tous en ont, sinon le début des clips. La boîte propose aussi la politique audio, dérivée par `suggest_audio_policy` : un
enregistreur n'est choisi automatiquement que si la synchronisation de son angle est excellente ou bonne (ou n'a pas
été mesurée) ; en cas d'ambiguïté la question est posée.

**Noms d'angles** (`core/multicam_naming.py`, fonctions pures) : un angle créé depuis `C0012.mp4` ne s'appelle pas
« C0012 ». Ordre de recherche : un mot de rôle ou de position dans le titre, le nom de fichier ou le dossier (`wide`,
`gros plan`, `handheld`, `drone`, `cam A`, `camera 2`…, en français comme en anglais) ; sinon le nom de bobine lu dans le
fichier ; sinon le modèle de caméra ; sinon le nom de fichier nettoyé (compteurs `C0012`, `MVI_1234`, `GX010123`,
dates et grands nombres retirés) ; sinon `Angle N` (`Audio N` pour un enregistreur). Les noms d'un lot sont toujours
distincts. L'infobulle d'un angle donne les métadonnées du média (`core/media_describe.py` : durée, cadence, timecode,
bobine, caméra…).

**Métadonnées lues** (`MediaAsset`, champs optionnels, jamais requis) : `timecode`, `timecode_fps`, `time_reference`
(heure de début d'un fichier audio BWF), `reel`, `camera`, `creation_time`. Un projet ancien, sans ces champs, se charge
et se synchronise comme avant.

## Synchronisation

Toutes les méthodes produisent la même chose : **une position de départ par angle** (en secondes, la plus petite = 0).
Couverture partielle admise : une caméra qui démarre plus tard ou s'arrête plus tôt reste synchronisée sur la partie
commune ; hors de sa couverture l'angle est affiché `NO SIGNAL` dans le moniteur.

| Méthode | Source du décalage |
| --- | --- |
| Timecode | timecode de début lu par ffprobe (`core/timecode.py`) ; un média sans timecode ne bloque pas : il garde le début des clips et l'angle est marqué `none` |
| Audio | corrélation croisée locale (`core/audio_sync.py`, NumPy seul), avec score de confiance |
| Repère | un repère posé dans chaque clip de la timeline |
| Début des clips | tous les angles commencent à 0 |
| Positions actuelles | clips déjà alignés sur la timeline : leurs positions relatives sont gardées |
| Manuelle | l'utilisateur déplace l'angle ou saisit une valeur dans *Réglages Multicam…* |

La confiance ne dit jamais « bonne » faute de pouvoir mesurer : `excellent` / `good` / `uncertain` / `failed`.
Une correction manuelle est une opération d'historique comme une autre (annulable) et marque l'angle `manual`.

### Timecode (`core/timecode.py`)

Un seul module porte les mathématiques SMPTE : 23,976 · 24 · 25 · 29,97 (sans saut) · 29,97 *drop-frame* · 30 · 50 ·
59,94 (sans saut et *drop-frame*) · 60. Les durées sont des `Fraction` exactes, jamais un `fps` tronqué : une cadence
inconnue **n'est jamais arrondie à l'entier le plus proche** (l'ancien défaut lisait 29,97 comme 29). Le *drop-frame* saute des **étiquettes**, pas des images : `01:00:00;00` = 107 892 images ;
sans saut à 29,97, `01:00:00:00` vaut 3 603,6 s écoulées, la seule grandeur qui compte pour un décalage entre deux
appareils. Un tournage à cheval sur minuit donne un écart d'environ 24 h que l'appelant doit traiter ; ce n'est pas
masqué par un modulo.

### Synchronisation par le son (`core/audio_sync.py`)

Conçue pour rester **honnête** plutôt que « toujours répondre » :

1. **Décodage en flux** : FFmpeg (lancé par le superviseur de processus, donc tué à l'arrêt de l'application) rend du
   mono 8 kHz lu par blocs de 4 s ; on ne charge jamais le son décodé d'une source en mémoire.
2. **Passe grossière** : enveloppe d'énergie à 100 Hz (pré-accentuation, moyenne locale de 2 s retranchée), puis
   corrélation croisée normalisée par FFT, valable pour un recouvrement partiel (≥ 4 s).
3. **Passe fine** : GCC-PHAT (bande 100–3 500 Hz) sur une fenêtre active d'environ 24 s autour du pic grossier, puis
   interpolation parabolique : décalage à la milliseconde.
4. **Confiance** : rapport pic / second pic et accord entre les deux passes. Seuils : ≥ 0,80 *excellent*,
   ≥ 0,55 *bon*, ≥ 0,30 *incertain*, sinon *échoué*. Musique périodique, signaux sans rapport et silence ne donnent
   **jamais** un résultat « sûr et faux » (balayage de propriétés sur seize cas, `tests/test_audio_sync.py`).
5. **Chaînage** : une source sans recouvrement suffisant avec la référence se cale sur une source déjà placée.
6. **Annulable et non bloquante** : `AudioSyncJob` publie des instantanés (même modèle que le suivi de mouvement) ; la
   boîte de progression tourne sur un minuteur de 100 ms.

Les tests retrouvent des décalages de +2,4 s et +17,83 s, avec bruit, écarts de volume, silences, musique périodique et
signaux sans rapport. Sur les scènes synthétiques du banc l'erreur est de 0,0 ms ; ce n'est **pas** une garantie sur des
prises réelles bruitées : c'est le rôle du score de confiance, et de la correction manuelle quand il est bas.

### Cache de synchronisation

Ce qui coûte, c'est l'enveloppe d'une source : elle est rangée dans `user_cache_dir()/multicam/*.npy` (`core/audio_sync_cache.py`,
type `KIND_MULTICAM` du gestionnaire de caches, donc visible, purgeable et plafonnée comme les autres). La clé contient
tout ce qui change le résultat : **version de l'algorithme**, chemin, signature fraîche du fichier (date et taille),
plage analysée, paramètres. Une entrée illisible vaut une entrée absente ; l'écriture est atomique ; un échec d'écriture
est ignoré. **Rien de ce cache n'entre dans le `.kut`** : c'est une donnée recalculable, jamais une source de vérité.

## Le moniteur Multicam

`ui/multicam_viewer.py` — hébergé à côté du moniteur ordinaire (`MainWindow._monitor_stack`) ; basculer entre les deux ne
détruit aucun lecteur.

* **Grille adaptative** (`core/multicam.py: grid_shape`, lignes × colonnes) : 1 → 1×1, 2 → 1×2, 3–4 → 2×2, 5–9 → 3×3,
  10–16 → 4×4 ; **pages de 16** au-delà. La vignette de l'angle actif est cerclée de sa couleur ; un clic bascule, un clic droit ouvre la source sur
  cet angle.
* **Flux d'images sans attente** (`core/multicam_feed.py`, `AngleFeed`) : un FFmpeg supervisé par angle, vers un
  tampon circulaire borné d'images RGB. L'interface ne lit que « la dernière image disponible » et **garde la précédente**
  quand une nouvelle n'est pas arrivée : le fil Qt n'attend jamais un décodage. Les flux ne lisent pas plus de 1,5 s
  en avance, repartent proprement après un saut de plus de 2 s, et libèrent leur processus après 6 s d'inactivité.
* **Profil `multicam_grid` et qualité adaptative** : quatre profils de vignette — 480×270 à 24 i/s, 320×180 à 15,
  240×136 à 10, 160×90 à 6. Le point de départ dépend du nombre de vignettes visibles (≤ 4 : 480×270 ; 5–9 : 320×180 ;
  10 et plus : 240×136) ; `TileQualityGovernor` descend d'un cran quand les images arrivent en retard (retard mesuré,
  pas supposé) et remonte quand la machine suit de nouveau. La vignette de l'angle actif a toujours un cran de finesse
  de plus que les autres.
* **Préchargement** : chaque flux lit 1,5 s en avance ; **tous les angles visibles sont traités à égalité** — il n'y a pas
  d'ordre de priorité entre eux (voir les limites).
* **Sortie programme** : elle réutilise le flux de l'angle actif — **pas de second décodage** de la même image.
* **États** : `MEDIA OFFLINE` (fichier introuvable), `NO SIGNAL` (hors de la couverture de l'angle), vignette audio
  pour un enregistreur.
* **Proxys** : à partir de **quatre angles vidéo vivants lisant leurs originaux**, une bannière propose de générer des
  proxys (bouton → génération en file ; le moniteur les utilise dès qu'ils existent). Voir les mesures plus bas :
  quatre 4K sans proxy occupent plus de deux cœurs.
* **Clavier** : on bascule par `1`–`9` ; les boutons de page, de réglages et de proxys sont atteints par `Tab` et ont un
  nom accessible. Les **vignettes elles-mêmes ne prennent pas le focus** (clic et info-bulle « 2. Gros plan » seulement).

Les vignettes sont des `QImage` sur CPU. Le compositeur GPU de l'aperçu n'est **pas** utilisé pour les vignettes (sa pile
de couches alloue une texture de la taille du canevas par couche, ce qui serait ruineux pour 16 petites images) ; la
sortie programme, elle, passe par le chemin du moniteur existant, GPU compris.

## Bascule en direct

| Touche | Action |
| --- | --- |
| `1`–`9` | montrer l'angle correspondant à la tête de lecture (pavé numérique compris) |
| `Ctrl+Maj+M` | afficher / masquer le moniteur Multicam |
| *(libres)* | `multicam_create`, `multicam_open_source`, `multicam_flatten`, `multicam_settings` : sans raccourci par défaut |

Toutes sont reconfigurables (*Préférences… → Raccourcis*, catégorie **Multicam**, `core/shortcuts.py`). Sur clavier
AZERTY, `Maj`+chiffre fonctionne comme le chiffre ; la rangée de symboles *sans* Maj ne l'est pas (voir les limites).

`switch_angle` coupe le segment sous la tête de lecture et donne l'angle voulu à la moitié droite. Cas particuliers :
même angle que l'actuel → **aucune coupe inutile** ; tête de lecture au tout début d'un segment → l'angle est remplacé
sur place, sans coupe de longueur nulle ; hors de tout segment, ou numéro d'angle inexistant → refusé et dit dans la barre
d'état. **Sans aucune source Multicam dans le projet, `1`–`9` sont ignorés en silence** : les chiffres restent libres. **Une bascule = une
entrée d'historique** (`Ctrl+Z` la défait), y compris pendant la lecture. L'opération elle-même mesure quelques
dixièmes de milliseconde ; le retour visuel dans le moniteur (`set_active_hint`) vient juste après l'édition, avant
l'enregistrement dans l'historique et la reconstruction de la timeline.

*Remplacer par l'angle* (menu contextuel du segment) donne un autre angle à tout le segment, sans le couper : c'est `replace_angle`.

## Audio

La politique est un attribut de la **source** (`MulticamSource.audio`) : l'audio d'un segment est calculé à partir d'elle
au moment de construire le sous-plan, jamais recopié dans les clips. `FOLLOW_VIDEO` : le son de l'angle actif ;
`FIXED` : le son d'un angle choisi (typiquement l'enregistreur) quel que soit l'angle vidéo ; `MIX` : plusieurs sources.
Dans les deux derniers cas, le son des caméras est **retiré** du segment (clips marqués `silent` par le filtre), sans
modifier les clips. Les coupes audio et vidéo restent dissociables (un clip imbriqué posé sur une piste audio n'apporte
que le son), ce qui prépare les coupes en J et en L sans les implémenter ici.

**En lecture directe**, le moniteur ordinaire ne sait lire que le son du fichier de l'image affichée. Avec une politique
`FIXED` ou `MIX`, son lecteur est donc coupé (`PreviewPanel.set_silenced`) et `ui/multicam_audio.py` (`AuxAudio`) joue les
sources désignées, calées sur la tête de lecture (tolérance de dérive de 0,2 s comme le moniteur), **quatre au plus**.
L'**aperçu fidèle** et l'**export** donnent, eux, le mixage exact : le retour en direct n'en est qu'une approximation.

## Aperçu et export

Aperçu fidèle et export partent du **même** `RenderPlan` ; un test rend les deux et compare les images (voir *Tests*).

* **Seul l'angle montré est rendu** : le plan construit un sous-plan par `(source, angle visible)` (clé `…#v-…#a-…`),
  et deux segments du même angle le partagent. Les angles cachés ne coûtent **ni entrée FFmpeg ni composition** (voir
  *Performances*).
* **Transitions** : une transition entre deux segments d'angles différents passe par le moteur existant ; les deux
  angles sont rendus pendant sa durée, comme n'importe quel fondu entre deux clips.
* **Cadences différentes** (25 / 29,97 / 24 mélangées) : chaque angle est ramené à la cadence du projet par le moteur
  existant, sans troncature de la cadence.
* **Défaut corrigé en chemin** : le filtre audio retardait un clip par `asetpts`, or `amix` ignore les horodatages de
  ses entrées. Avec FFmpeg 9.0.2, un clip qui ne commençait pas à 0 était joué *depuis le début* du mixage ; pour deux
  clips bout à bout, le second se faisait entendre de 0 à 4 s puis le son disparaissait. Le filtre place désormais le clip
  avec `adelay` (`core/export_engine.py`) et `RENDER_ENGINE_VERSION` passe à **3** pour invalider les rendus en cache.
  Ce défaut n'est pas propre à Multicam (voir `docs/stabilization-report.md`) ; il est apparu parce qu'un enregistreur
  externe démarre rarement à l'instant 0 de la timeline.

## Fichier `.kut`

* `Sequence.multicam` n'est écrit **que** pour une source ; `Clip.angle_id` **que** s'il est non vide. La version de
  format reste **16** : un projet sans Multicam n'écrit aucune clé nouvelle (`test_multicam_keys_are_only_written_when_used`),
  et un projet ancien se charge sans migration.
* La lecture est stricte sur la structure (une source mal formée est refusée avec un message lisible) et tolérante sur
  les valeurs d'énumération inconnues (un `.kut` plus récent ne casse pas un lecteur plus ancien sur un simple libellé).
* **Jamais dans le fichier** : formes d'onde, enveloppes, corrélations, images de vignettes, cache. Tout est recalculable.

## Angles hors ligne et relink

Un média introuvable ne casse rien : son angle affiche `MEDIA OFFLINE` dans le moniteur et dans *Réglages Multicam…*
(avec un bouton **Relier…** qui réutilise le relink de la bibliothèque), son segment rend un plan vide et apparaît comme
tel dans la timeline, et les autres angles continuent de jouer et de s'exporter. Dès que le média est relié, l'angle
revient sans autre geste ; la synchronisation, qui n'est que la position des clips, n'a pas été perdue.

## Aplatir, dupliquer, imbriquer

* **Aplatir** (`flatten_multicam_clip`) remplace un segment par les clips réels de l'angle montré, taillés à la durée
  du segment, sans plus aucun lien avec la source. Refusé, avec une explication, si le segment porte une transformation,
  un étalonnage ou une composition propres, ou s'il est inversé / figé : l'aplatir perdrait ces réglages. Avec une
  politique `FIXED` ou `MIX`, les caméras sont mises à −60 dB sur la copie.
* **Dupliquer un segment** crée une autre *instance* de la même source (même `angle_id`) : modifier la source modifie
  les deux. **Dupliquer la séquence source** (`duplicate_sequence`) en fait une copie indépendante : angles, politique
  audio et pistes sont copiés, les segments existants continuent de désigner l'original.
* **Repères** : ceux de la source et ceux du montage ne se mélangent pas ; une copie reçoit de nouveaux identifiants.

## Performances

Mesures reproductibles — `python -m tools.perf.multicam_bench --ui --out docs/perf/multicam.json` et
`python -m tools.perf.audio_sync_bench --out docs/perf/audio-sync.json` — **sur une seule machine** (Apple Silicon,
macOS, Python 3.14). Les temps dépendent du matériel : retenez les *ordres de grandeur et les invariants*, que
`tests/test_multicam_bench.py` vérifie sans seuil serré.

### Coût du plan, de l'export et de la bascule

| Angles × coupes | Segments | Compositions | Plan de rendu | Graphe FFmpeg | Taille du graphe |
| --- | --- | --- | --- | --- | --- |
| 4 × 10 | 11 | 4 | 0,3 ms | 0,3 ms | 11 Ko |
| 4 × 100 | 101 | 4 | 2,1 ms | 1,7 ms | 70 Ko |
| 4 × 1000 | 1001 | 4 | 20,3 ms | 15,4 ms | 677 Ko |
| 16 × 10 | 11 | 11 | 0,7 ms | 0,4 ms | 17 Ko |
| 16 × 100 | 101 | 16 | 2,9 ms | 1,9 ms | 81 Ko |
| 16 × 1000 | 1001 | 16 | 22,6 ms | 15,7 ms | 687 Ko |

* **Compositions = angles réellement montrés** (16 × 10 en montre 11, pas 16) ; avec 16 ou 32 angles et les mêmes 11
  montrés, le graphe FFmpeg est identique. Le coût suit les coupes, linéairement (≈ ×9,6 pour ×10).
* Reconstruire le plan d'une fenêtre de 2 s autour de la tête de lecture coûte 0,06 ms, quelle que soit la longueur du
  montage ; trouver les clips actifs à un instant, 35 à 100 µs.

| Angles × coupes | Bascule seule | Bascule + historique |
| --- | --- | --- |
| 4 × 10 | 0,05 ms | 0,27 ms |
| 4 × 100 | 0,06 ms | 0,62 ms |
| 4 × 1000 | 0,16 ms | 4,10 ms |
| 16 × 1000 | 0,16 ms | 4,13 ms |

Dans la **vraie fenêtre** (4 angles, 40 coupes, médias fictifs) : bascule complète — opération, historique, timeline —
**7,7 ms** ; retour visuel du moniteur **0,013 ms**. L'historique est la part qui grossit avec le projet (instantané du
projet) : 4 ms à mille coupes, imperceptible.

### Flux d'angles réels

Flux décodés en temps réel pendant 6 s (`AngleFeed`, un sous-processus par scénario, médias synthétiques `testsrc2`,
proxy 480×270 pour la dernière ligne) :

| Scénario | Vignette | Images/s par angle (visé) | CPU (% d'un cœur) | FFmpeg le plus gros | Mémoire du processus | 1re image à froid | Sondages en retard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 × 1080p | 480×270 à 24 | 20,5 (24) | 97 % | 126 Mo | 182 Mo | 100 ms | 1,6 % |
| 8 × 1080p | 320×180 à 15 | 15,6 (15) | 145 % | 126 Mo | 158 Mo | 149 ms | 2,2 % |
| 4 × 4K | 480×270 à 24 | 20,2 (24) | 204 % | 382 Mo | 217 Mo | 189 ms | 3,3 % |
| 8 × 4K **avec proxys** | 320×180 à 15 | 15,5 (15) | 44 % | 41 Mo | 173 Mo | 51 ms | 1,3 % |

Ce que ces chiffres disent :

* **Les proxys changent tout** : huit angles 4K avec proxys coûtent **moins d'un demi-cœur** et 41 Mo de FFmpeg, là où
  quatre 4K sans proxy en demandent deux et 382 Mo. C'est la raison de la bannière du moniteur.
* **Passer d'un angle déjà en lecture à un autre** — la bascule vue du flux — se mesure en microsecondes (0,6 à 2,8 µs) :
  les images de tous les angles sont déjà là, on ne fait que changer celui qu'on affiche.
* Le profil de départ suit le nombre d'angles : 480×270 à 24 i/s jusqu'à 4 vignettes, 320×180 à 15 i/s de 5 à 9 (c'est ce
  que montrent les deux dernières lignes). Ces mesures n'emploient **pas** le gouverneur de qualité (flux seuls, profils
  fixes) : sa dégradation est couverte par ses tests, pas par ce tableau.

Ce qu'ils **ne disent pas** :

* « Images/s par angle » est ce que le *banc* voit en sondant toutes les ~40 ms ; à 24 i/s, deux images peuvent tomber
  entre deux sondages et ne pas être comptées. L'écart avec les 24 visés est donc en partie un artefact de mesure, mais
  je n'ai **pas** isolé la part due au décodage : ne lisez pas « 20,5 » comme « 24 tenus ». La colonne « sondages en
  retard » (au plus 3,3 %) est la mesure de fluidité : une image affichée a plus de quatre périodes de retard.
* Ces mesures sont celles du **moteur de flux**, hors fenêtre : elles ne comptent pas les images perdues par la
  peinture Qt, que la plateforme `offscreen` ne permet pas de mesurer. **Huit 4K sans proxy** n'ont pas été mesurés
  (le cahier des charges demandait des proxys ; la ligne 4 × 4K donne l'ordre de grandeur de ce qu'on évite).
* Une seule machine, un seul système. Rien n'a été mesuré sous Windows ou Linux.

### Synchronisation par le son

`N` sources de la durée indiquée, pour chaque cellule : temps **à froid** / temps **avec le cache** d'enveloppes.
(Un seul média sonore est généré par durée et chaque source en lit une fenêtre différente — décalages attendus connus ;
le banc vérifie aussi que la mesure est juste : erreur maximale **0,0 ms** sur tous les scénarios, toutes les
synchronisations *excellentes*.)

| Sources | 5 min | 30 min | 2 h | Pic mémoire (5 min → 2 h) |
| --- | --- | --- | --- | --- |
| 2 | 0,2 s / 0,07 s | 0,4 s / 0,07 s | 1,3 s / 0,10 s | 64 → 189 Mo |
| 4 | 0,4 s / 0,20 s | 0,9 s / 0,22 s | 2,7 s / 0,33 s | 66 → 195 Mo |
| 8 | 0,9 s / 0,45 s | 1,9 s / 0,59 s | 5,5 s / 0,71 s | 65 → 200 Mo |
| 16 | 1,8 s / 0,98 s | 3,8 s / 1,11 s | 11,0 s / 1,55 s | 67 → 216 Mo |

* Le temps croît **linéairement** avec la durée totale à analyser ; le cache le divise par 1,8 à 12,8.
* Le décodage est en flux : le plus gros FFmpeg occupe ≈ 16 Mo et le pic du processus reste sous 220 Mo pour **seize
  sources de deux heures** (décodées d'un bloc, elles occuperaient plusieurs Go).
* La mémoire **n'est pas constante** : elle croît doucement avec la durée (la corrélation d'enveloppes se fait par FFT sur
  toute la durée — 64 Mo à 5 min, ≈ 200 Mo à 2 h), et presque pas avec le nombre de sources (2 → 16 sources à 2 h : +14 %).
* Le média de test est de la parole synthétique propre : c'est le cas **facile** pour la précision. Les cas difficiles
  (bruit, musique, silence) sont traités par les tests de précision et le score de confiance, pas par ce tableau.

## Diagnostic

Journal `kut_studio.multicam` (niveau info) : création d'une source, lancement, annulation et résultat de chaque
synchronisation (nombre de sources, référence, durée, sources ignorées ou incertaines avec leur confiance), chaque
bascule (angle, instant, coupe faite ou non), flux impossible à ouvrir, entrée de cache illisible. **Jamais une ligne par
image** : un événement est journalisé quand il se produit, pas quand il se répète.

## Tests

| Fichier | Ce qu'il garantit |
| --- | --- |
| `test_multicam_core.py` | modèle, sérialisation, opérations, filtre de pistes, refus d'imbrication, anciens `.kut` |
| `test_multicam_export.py` | **13 rendus FFmpeg réels** : 10 bascules avec la couleur attendue à chaque instant, aperçu = export, fondu entre angles, effet par caméra à chaque apparition, Multicam imbriqué, aplatissement, cadences 25 / 29,97 / 24, politiques audio mesurées par fréquence dominante, angle hors ligne |
| `test_export_audio_timing.py` | rendus FFmpeg réels : clips bout à bout, trou entre deux clips, séquence imbriquée placée tard — le ton entendu à chaque instant |
| `test_audio_sync.py` | précision (+2,4 s, +17,83 s, bruit, volume, silence), confiance, balayage de seize cas difficiles, flux, cache, annulation |
| `test_multicam_feed.py`, `test_multicam_viewer.py`, `test_multicam_ui.py` | flux bornés et sans attente, grille, états, bascule, raccourcis |
| `test_multicam_creation.py`, `test_multicam_settings.py`, `test_multicam_audio.py` | parcours de création, réglages, audio en direct |
| `test_multicam_bench.py` | les bancs tournent, et les invariants de complexité tiennent |

Tests transverses mis à jour : raccourcis, parité i18n, navigation clavier des boîtes, garde du code mort, audit d'interface
(`tools/ui_audit`, scénarios `moniteur-multicam` et `retour-moniteur`).

## Limites connues

Ce qui n'est **pas** fait ou **pas** vérifié — à lire avant de promettre quoi que ce soit :

* **Clavier AZERTY** : `Maj`+chiffre, pavé numérique et réaffectation fonctionnent ; la rangée de symboles *sans* Maj
  (`&é"'(`…) n'est pas associée aux angles 1–9.
* **Vignettes sur CPU** (voir plus haut) : pas d'accélération GPU des vignettes.
* **Pas de priorité de préchargement** entre angles : tous les angles visibles sont lus à égalité, et le gouverneur
  dégrade toutes les vignettes ensemble. Sous forte charge, l'angle actif n'est pas protégé au détriment des autres
  (il a seulement un cran de finesse de plus).
* **Vignettes sans focus clavier** : la bascule se fait par `1`–`9` ou à la souris ; une vignette ne se parcourt pas à `Tab`.
* **Double décodage de l'angle actif** : le moniteur ordinaire (caché) continue de décoder l'angle actif en plus du
  flux de sa vignette. Mesuré comme acceptable, non éliminé.
* **Décodage matériel** : non étudié pour N décodeurs simultanés (limites de sessions des pilotes).
* **Timecode de caméras réelles** (Sony, Canon, iPhone…) : la lecture suit ffprobe et a été vérifiée sur des fichiers
  produits par les tests, **pas** sur des fichiers de ces caméras.
* **Synchronisation par le son** : la passe grossière échoue (honnêtement : statut `failed`) quand le signal commun est
  enfoui dans le bruit, même si la passe fine l'aurait vu. Préférable à une réponse fausse ; la correction manuelle reste.
* **Aplatissement** : refusé pour un segment portant ses propres transformation, étalonnage ou composition, et pour un
  segment inversé ou figé.
* **Son en direct** : quatre sources au plus, approximation du mixage exact (qui est celui de l'aperçu fidèle et de l'export).
* **Mesures** : une machine ; seize sources de deux heures sur un son synthétique ; flux hors fenêtre ; rien sous
  Windows / Linux. **FFmpeg 6.1** (Ubuntu en CI) : le décalage audio `adelay` en millisecondes fractionnaires a été écrit
  d'après la documentation, pas vérifié sur cette version.
* **Intégration continue** : non exécutée sur trois systèmes pour cette branche au moment de la rédaction.

## Architecture future

Rien de ce qui suit n'est commencé. Ce qui rend chaque point possible sans refonte :

* **Priorité de préchargement** : un ordre explicite (angle actif, puis voisins, puis le reste) qui dégrade les angles
  cachés avant l'angle actif ; le gouverneur sait déjà mesurer le retard, il manque la politique.
* **Plus de 16 angles** : la grille pagine déjà par 16 et les flux sont créés à la demande ; il manque un profil de
  vignette réglé pour 25–64 angles (le modèle admet 64).
* **Proxys Multicam automatiques** : la bannière appelle déjà la génération de proxys pour les angles visibles ; il manque
  de la lancer d'office à la création, selon la machine.
* **Coupes en J et en L** : audio et vidéo d'un angle sont déjà des clips distincts, et la politique `FIXED` découple
  déjà le son de l'image ; il manque l'interface pour décaler le point de coupe du son seul.
* **Caméras distantes, capture en direct** : un angle est une piste ; un flux réseau serait un média qui s'allonge. Le
  contrat `AngleFeed` (dernière image disponible, jamais d'attente) est déjà celui d'une source en direct.
* **Synchronisation LTC** : une méthode de plus produisant « une position par angle ».
* **Empreintes sonores avancées** : remplacer la passe grossière derrière l'interface actuelle de `analyze_sync`.
* **Collaboration** : le décalage étant la position des clips, deux personnes qui règlent des angles produisent des
  modifications de clips ordinaires.

## Ajouter une fonctionnalité compatible

Les règles à respecter, issues de la stabilisation :

1. **Une seule source de vérité.** Une donnée qui existe déjà dans le projet (position d'un clip, effet d'un clip) ne se
   recopie pas dans `MulticamSource`. Si elle change, tout ce qui la lit la voit.
2. **Un seul moteur de rendu.** Toute nouveauté visible passe par le `RenderPlan` ; ni l'aperçu ni l'export n'ont leur
   propre chemin. Un angle que le plan ne rend pas ne doit coûter ni entrée FFmpeg ni composition (le banc le vérifie).
3. **Interface → cœur.** Une opération vit dans `core/multicam_ops.py`, sans Qt, et renvoie un résultat que l'interface
   présente ; elle ne modifie rien si elle est refusée.
4. **Un geste = une entrée d'historique.** Une action importante de l'utilisateur, même composée de plusieurs
   opérations, s'annule d'un seul `Ctrl+Z`.
5. **Le recalculable ne s'enregistre pas.** Un nouveau cache a une version d'algorithme dans sa clé, passe par le
   gestionnaire de caches et n'entre jamais dans le `.kut`.
6. **L'interface ne bloque jamais.** Décodage, analyse, génération : hors du fil Qt, annulables, avec un état d'attente
   honnête.
7. **Honnêteté d'abord.** Une mesure qui n'est pas fiable le dit (`uncertain`, `failed`) ; une limite se documente ici.
8. **Chaque ajout vient avec ses tests** (un rendu FFmpeg réel pour tout ce qui change l'image ou le son), ses textes
   dans `ui/i18n_multicam.py` (français et anglais), et ses invariants de complexité si le coût peut grossir.
