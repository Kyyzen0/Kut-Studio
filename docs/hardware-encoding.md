# Encodage matériel (export)

Kut-Studio sait encoder l'export avec le GPU / la puce vidéo **quand FFmpeg le
permet réellement**. Rien n'est jamais supposé à partir du système ou du GPU :
la détection interroge FFmpeg et valide chaque encodeur par un mini-encodage.
**Aucune accélération n'est obligatoire** : le chemin CPU (`libx264`,
`prores_ks`) reste de première classe et fonctionne partout, sans détection.

Périmètre : **encodage de l'export**. La même détection décrit aussi les
**décodeurs** (aperçu, proxies, tracking) et le moniteur peut rendre sur le GPU :
voir [gpu-preview.md](gpu-preview.md). Les proxies restent encodés en CPU.

## Backends pris en charge

| Backend (`HardwareEncoder`) | Encodeurs FFmpeg | Plateformes habituelles | Notes |
| --- | --- | --- | --- |
| `videotoolbox` | `h264_videotoolbox`, `hevc_videotoolbox` | macOS (Apple Silicon et Intel) | Débit cible (pas de `-q:v` : absent sur Mac Intel), `-allow_sw 0` |
| `nvenc` | `h264_nvenc`, `hevc_nvenc` | Windows, Linux (NVIDIA) | `-rc vbr -cq`, `-preset p5` |
| `qsv` | `h264_qsv`, `hevc_qsv` | Windows, Linux (Intel) | `-global_quality`, filtre `format=nv12` |
| `amf` | `h264_amf`, `hevc_amf` | Windows (AMD) | `-rc cqp -qp_i/-qp_p` |
| `vaapi` | `h264_vaapi`, `hevc_vaapi` | Linux | nœud `/dev/dri/renderD*` (ou `KUT_STUDIO_VAAPI_DEVICE`), `-vaapi_device` + `format=nv12,hwupload` |
| `cpu` | `libx264`, `libx265`, `prores_ks` | partout | CRF appliqué tel quel |
| `auto` | — | — | choisit parmi les backends **validés**, sinon CPU |

Les presets du produit n'exportent que du H.264 (MP4/MOV) et du ProRes. HEVC
est détecté et pris en charge par les tables d'encodeurs, mais aucun preset ne
le propose encore. ProRes reste CPU (`prores_ks`).

## Détection et validation

`core/hardware_encoding.py` (pur, sans Qt) :

1. `ffmpeg -encoders` → `parse_encoder_list` retient les encodeurs vidéo ;
2. pour chaque encodeur matériel **listé**, `validate_capability` encode 3
   images synthétiques 256×256 vers `null`, **avec les mêmes options de qualité
   qu'un vrai rendu** ;
3. un encodeur listé mais qui ne s'initialise pas (pas de GPU, pilote absent,
   nœud VAAPI manquant) est marqué `validated=False` et n'est jamais proposé.

Le résultat est un `HardwareCapabilities` (version et chemin de FFmpeg,
encodeurs, validations). Les validations tournent en parallèle (≤ 4) avec un
délai de 20 s chacune.

## Cache

`core/hardware_cache.py` : cache mémoire + `hardware-encoders.json` dans le
dossier de cache de l'utilisateur. Il est relu tant que l'**empreinte** de
l'installation est identique ; il est invalidé automatiquement quand :

- le chemin de FFmpeg change ;
- le binaire change (date ou taille : une mise à jour change donc la version) ;
- la plateforme, l'architecture ou le nœud VAAPI changent ;
- l'interrupteur `KUT_STUDIO_HARDWARE_ENCODING` change ;
- le cache a plus de 30 jours.

Limite connue : une mise à jour de **pilote** sans changer FFmpeg n'est pas
détectable. L'utilisateur peut forcer une nouvelle détection :
**Préférences → Performance → Encodage matériel → Redétecter les capacités
matérielles**. Et si un encodeur validé échoue malgré tout, le mode Auto bascule
sur le CPU (voir plus bas).

La détection se fait **dans un thread** au démarrage : l'interface n'est jamais
gelée. Tant qu'elle n'a pas abouti, le panneau Export propose *Automatique* et
*CPU* seulement. `KUT_STUDIO_HARDWARE_ENCODING=off` désactive toute détection
(support, CI) : tout est rendu en CPU.

## Modes d'encodage

Le panneau Export a un sélecteur **Encodeur** (valeur par défaut enregistrée
dans les préférences : `export_encoder`, `auto` pour une installation neuve) :

- **Automatique** : encodeur matériel validé pour le codec, sinon CPU, sans bruit.
  Ordre de préférence (départage seulement des encodeurs *déjà validés*) :
  macOS → VideoToolbox ; Windows → NVENC, QSV, AMF ; Linux → NVENC, QSV, VAAPI.
  La décision vient des capacités détectées, jamais du nom de la machine
  (aucun cas particulier M1/M4…).
- **CPU – libx264** : toujours disponible.
- **Un backend détecté** (ex. *Apple VideoToolbox – H.264*) : uniquement ceux
  qui existent et sont validés. ProRes n'offre que le CPU.

Les presets existants ne changent pas (`hardware="cpu"` dans les presets fournis) :
l'encodeur choisi dans le panneau est appliqué par-dessus
(`with_hardware(spec, …)`). Les projets, presets, jobs de file et préférences
enregistrés avant cette fonction se chargent sans modification.

## Repli CPU et échecs

Au lancement d'un rendu en mode **Automatique** :

1. si l'encodeur matériel échoue **avant la première image encodée**, FFmpeg est
   arrêté, la raison est journalisée et le **même job** est relancé une seule
   fois avec l'encodeur CPU (mêmes réglages, même fichier) ; l'utilisateur voit
   « Encodage matériel indisponible, bascule sur le CPU » ;
2. au plus **un** repli par export : jamais de boucle entre encodeurs ; si le CPU
   échoue aussi, l'erreur du CPU est rapportée ;
3. un échec après le début de l'encodage n'est pas rejoué (pas de rendu en double
   à 90 %).

Si l'utilisateur a choisi **un encodeur précis**, rien n'est masqué : le job
échoue avec `ErrorKind.ENCODER` et le message réel de FFmpeg ; la file propose
**Relancer en CPU** (`RenderQueue.retry_on_cpu`, qui ne modifie que ce job).

## Intention de qualité

Les paramètres de qualité ne sont pas comparables entre encodeurs. Un preset
décrit une **intention** (`QualityIntent` : Low / Medium / High / Master) que
`core/video_encoders.py` traduit par backend. Pour la compatibilité, la valeur
stockée reste le **CRF x264** (`quality`) ; `intent_for_crf` en déduit l'intention
(≤ 15 Master, ≤ 19 High, ≤ 24 Medium, sinon Low). Le CPU applique le CRF **tel
quel** (le mode *Custom* garde donc ses réglages libres) ; les backends
matériels utilisent leur propre échelle :

| Intention | VideoToolbox (bits/pixel) | NVENC `-cq` | QSV `-global_quality` | AMF / VAAPI `qp` |
| --- | --- | --- | --- | --- |
| Low | 0,04 | 33 | 30 | 30 |
| Medium | 0,07 | 28 | 25 | 26 |
| High | 0,11 | 23 | 21 | 22 |
| Master | 0,18 | 19 | 17 | 18 |

Ce n'est pas une équivalence mathématique : l'objectif est un résultat cohérent
et prévisible. Les encodeurs matériels ignorent le préréglage de vitesse x264.

## RenderJob et file de rendu

`RenderJob` mémorise : `hardware` (mode demandé), `encoder` (encodeur FFmpeg
lancé), `hardware_used` (famille effective), `fallback_reason`, `diagnostics`
(fin de la sortie d'erreur d'un essai échoué). Ils sont sérialisés
(champs optionnels : les anciens fichiers de file se chargent). La file affiche
discrètement `H.264 · VideoToolbox` ou `H.264 · CPU` dans une colonne
« Encodeur » (vide tant que le job n'a pas été lancé) ; le détail, l'éventuel
repli et « Copier l'erreur » (qui inclut les diagnostics) complètent.
Signal : `RenderQueue.encoder_fallback(job_id, raison)`.

## Diagnostics et journaux

Préférences → Performance → *Encodage matériel* : version et chemin de FFmpeg,
encodeurs détectés avec le résultat de leur validation, backend choisi par Auto
pour H.264 et HEVC. **Copier le diagnostic** place ce texte court (jamais la
sortie brute de FFmpeg) dans le presse-papiers.

Journaux (logger `kut_studio.encoding`) : détection, sélection d'encodeur,
commande FFmpeg, échec d'initialisation, repli, encodeur final. Les chemins ne
sont jamais écrits en entier : le dossier personnel devient `~`, les fichiers de
la commande sont réduits à leur nom, le filtre complexe est tronqué.

## Mesure

```bash
python -m tools.perf.encode_bench                      # CPU + chaque backend validé, 5 s en 1280×720
python -m tools.perf.encode_bench --seconds 10 --size 1920x1080 --json bench.json
python -m tools.perf.encode_bench --rescan
```

Encode la même séquence synthétique avec les arguments d'un vrai export et
rapporte durée, images/s, vitesse (× temps réel) et taille. Outil de
**vérification** (chaque chemin fonctionne, pas de régression) : les qualités
et débits des encodeurs ne sont pas équivalents, ce n'est pas un comparatif.

## Ajouter un backend matériel

1. `core/hardware_encoding.py` : ajouter la valeur à `HardwareEncoder`, son nom
   d'affichage (`BACKEND_LABELS`), ses encodeurs (`FFMPEG_ENCODER_NAMES`),
   `HARDWARE_BACKENDS` et son rang dans `AUTO_ORDER` ; si besoin une
   initialisation (`backend_input_args`, `backend_video_filter`,
   `backend_prerequisite_error`) ;
2. `core/video_encoders.py` : écrire `_<backend>_args(params, intent)` et
   l'ajouter à `_QUALITY_ARGS` ;
3. `tests/test_hardware_encoding.py` : une sortie `-encoders` simulée
   (`HARDWARE_LINES`) et les cas détecté / non validé / Auto / explicite.

Détection, cache, mode Auto, repli, interface et diagnostics suivent sans autre
changement. Le décodage matériel, l'aperçu accéléré ou le rendu GPU des effets
pourront réutiliser `HardwareCapabilities` (un champ de plus par capacité) sans
toucher à l'encodage.

## Limites connues

- **Balises de couleur** : l'export convertit en BT.709 (plage limitée) et pose matrice, primaires, transfert et plage
  à la fois sur les images (`setparams`, dernière étape du graphe) et sur la ligne de commande. C'est testé avec
  l'encodeur CPU (H.264 et ProRes) sur trois plateformes ; **les encodeurs matériels ne sont pas testés** avec ces
  balises (VideoToolbox, NVENC, Quick Sync, AMF, VAAPI : il faut les machines correspondantes).

- NVENC, QSV, AMF et VAAPI sont validés par la logique et par des sorties FFmpeg
  simulées, mais n'ont pas pu être exécutés sur du vrai matériel pendant le
  développement (seul VideoToolbox l'a été, sur Apple Silicon) : la validation
  à la détection et le repli Auto existent précisément pour ça.
- Le préréglage `-preset p5` de NVENC suppose un FFmpeg ≥ 4.4 ; sinon NVENC est
  invalidé à la détection et n'est pas proposé.
- Les tables de qualité sont des valeurs de départ raisonnables, pas une
  calibration ; elles se règlent dans `core/video_encoders.py`.
- Une mise à jour de pilote sans changement de FFmpeg nécessite « Redétecter ».
- Les proxies et l'aperçu restent en CPU ; HEVC n'a pas encore de preset.
