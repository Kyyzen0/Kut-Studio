# Décodage matériel et aperçu GPU

Kut-Studio utilise le décodage matériel et le GPU **quand ils fonctionnent
réellement** sur la machine, avec un repli CPU complet. Ordre des priorités :
stabilité, puis compatibilité, puis performance. Rien n'est supposé à partir
du GPU présent : chaque capacité est **détectée, validée et mesurée**.

Ce chantier n'a pas réécrit le moteur graphique. L'export et l'aperçu fidèle
gardent le graphe FFmpeg existant. Le moniteur temps réel gagne un rendu GPU
optionnel, et le rendu CPU historique reste en place comme repli.

## En bref

| | CPU (repli, toujours disponible) | Matériel / GPU |
| --- | --- | --- |
| Décodage de l'aperçu temps réel | Qt Multimedia, logiciel | VideoToolbox, NVDEC, D3D11VA, DXVA2, VAAPI (via Qt) |
| Décodage des segments fidèles, proxies, tracking | FFmpeg logiciel | `-hwaccel` FFmpeg, validé codec par codec, repli CPU automatique |
| Décodage de l'export | **toujours CPU** (déterministe) | — |
| Transform, opacité, recadrage du moniteur | `QGraphicsVideoItem` (raster Qt) | shaders QRhi |
| Effets, fusion, masques, calques d'effets du moniteur | segments fidèles (FFmpeg, exacts) | shaders QRhi, en temps réel |
| Étalonnage (réglages, courbes, LUT `.cube`) du moniteur | segments fidèles (FFmpeg, exacts) | LUT 3D cuite par la chaîne de l'export, lue par un shader |
| Encodage | inchangé (voir [hardware-encoding.md](hardware-encoding.md)) | inchangé |

Préférences > Performance > **Matériel** propose trois réglages et le diagnostic :

- **Décodage vidéo** : Auto, CPU, ou un backend détecté ;
- **Rendu de l'aperçu** : Auto, CPU, GPU ;
- **Encodeur d'export par défaut** (existant) ;
- le diagnostic copiable, avec le bouton « Redétecter ».

La qualité d'aperçu et le budget du cache restent à leur place (onglet
Général, groupe Cache).

## Architecture

```
décodage ─▶ transformation ─▶ effets ─▶ composition ─▶ affichage
```

| Étape | Module | Rôle |
| --- | --- | --- |
| Détection | `core/hardware_decoding.py`, `core/hardware_encoding.py`, `core/hardware_cache.py` | **une seule** détection matérielle : encodeurs **et** décodeurs, cache disque commun (schéma 2) |
| Décision de décodage | `core/decode_policy.py` | mode, usage, mesures, repli, original/proxy, réglage du lecteur Qt |
| Formats et couleur | `core/gpu_frames.py` | NV12, P010, YUV420P(10), YUV422P, RVBA/BGRA ; matrices BT.601/709/2020 |
| Effets | `core/gpu_effects.py` | traduction des effets du modèle, référence numpy |
| Composition | `core/gpu_composite.py` | description d'une image, découpage en passes, uniformes std140, référence numpy |
| Rendu choisi, santé, statistiques | `core/gpu_backend.py` | Auto / CPU / GPU, pannes, images perdues, temps de rendu |
| Cache GPU, mémoire | `core/gpu_cache.py`, `core/memory_monitor.py` | budget, LRU, purge ; pression mémoire |
| Charge | `core/preview_governor.py` | signaux GPU pour la qualité adaptative |
| Exécution GPU | `ui/gpu_preview.py` | `QRhiWidget` qui exécute les passes |
| Câblage | `ui/preview_panel.py`, `ui/main_window_mixins/hardware_preview.py` | bascule CPU/GPU, repli, diagnostics |
| Shaders | `tools/gpu/shader_sources.py`, `assets/shaders/*.qsb` | une source GLSL par passe, compilée pour tous les backends |

Les modules `core/` n'importent pas Qt, sauf la rastérisation partagée des
mattes, qui appartient déjà au rendu d'export. Ni l'export ni le rendu
n'importent numpy ou `ui.gpu_preview`, et un test le vérifie.

Le GPU ne fait qu'**exécuter** un plan calculé dans `core/` : toute la logique
se teste sans GPU. Une passe correspond à un shader, une cible, au plus trois
textures et un bloc d'uniformes :

```
clear ─▶ pour chaque calque vidéo :
           prep (YUV → calque : adaptation + bandes du pad, suréchantillonnage, effets ponctuels)
           flou H, flou V, netteté…  (effets de voisinage, 0..n passes)
           composite (matrice inverse, bord adouci, matte, opacité, mode de fusion)
       ─▶ pour chaque calque d'effets : prep (depuis le cadre) → voisinage → composite (couverture)
       ─▶ present (cadre → widget, letterbox)
```

## Détection des décodeurs

`ffmpeg -hwaccels` donne les méthodes listées. Chaque couple *(backend,
famille)* est ensuite validé par un **décodage strict** d'un échantillon
256×144, fabriqué par un encodeur logiciel du même FFmpeg :

```
-hwaccel X -hwaccel_output_format <format matériel> -i échantillon -vf hwdownload,format=<nv12|p010le|p210le>
```

Le mode strict est indispensable. En usage normal, FFmpeg **retombe en
logiciel sans aucun message** quand le matériel ne sait pas lire un profil.
Mesuré : un H.264 4:4:4 passe avec `-hwaccel videotoolbox` sans rien dire, même
en `-loglevel warning`. Avec `hwdownload`, une image logicielle fait échouer la
commande.

Familles validées : H.264, HEVC, HEVC 10 bits, ProRes 422, VP9, AV1. La famille
d'un média tient compte du codec, de la profondeur et du sous-échantillonnage :
un H.264 4:4:4 ou 10 bits n'est **pas** un H.264 matériel, il est décodé en CPU.

| Backend (`DecodeMode`) | Plateformes | Notes |
| --- | --- | --- |
| `videotoolbox` | macOS (Apple Silicon et Intel) | M4 : les six familles validées |
| `cuda` | Windows, Linux (NVIDIA) | NVDEC via les décodeurs natifs |
| `d3d11va` / `dxva2` | Windows (NVIDIA, Intel, AMD) | D3D11VA en priorité |
| `qsv` | Windows, Linux (Intel) | passe par `h264_qsv`, `hevc_qsv`… |
| `vaapi` | Linux | nœud `/dev/dri/renderD*` ou `KUT_STUDIO_VAAPI_DEVICE` |

Le résultat est rangé dans `HardwareCapabilities.decoders`, dans le même cache
que les encodeurs (`hardware-encoders.json`, schéma 2). Un cache v1 est ignoré
et la détection est relancée. Sur ce Mac, la détection complète (encodeurs et
décodeurs) prend 0,8 s, une seule fois, en tâche de fond.

Variables d'environnement :

- `KUT_STUDIO_HARDWARE_DECODING=off` : décodage matériel coupé ;
- `KUT_STUDIO_HARDWARE_ENCODING=off` : coupe toute la détection (CI) ;
- `KUT_STUDIO_GPU_PREVIEW=off` : jamais de moniteur GPU.

## Choisir le décodeur

`core.decode_policy.choose_decoder(mode, capabilities, stream, purpose, …)`
décide pour chaque média et chaque usage :

| Usage | Décodage |
| --- | --- |
| Moniteur temps réel | Qt Multimedia, avec les backends validés passés par `QT_FFMPEG_DECODING_HW_DEVICE_TYPES` |
| Segments d'aperçu fidèle | `-hwaccel` avant chaque `-i` de **média** (jamais devant un flux de calques) |
| Génération de proxies | idem : la source 4K / HEVC est le coût dominant |
| Analyse de tracking | idem ; les positions ne changent pas (décodage au bit près, voir plus bas) |
| Miniatures | CPU : une seule image, l'initialisation matérielle coûte plus qu'elle ne rapporte |
| Export | **CPU** : résultat déterministe, indépendant du pilote |

**Auto ne suppose rien.** Il ne choisit que des couples validés, puis départage
avec des **mesures réelles**. Au premier usage d'une famille et d'une classe de
taille (sd, hd, fhd, uhd), un décodage de 90 images du vrai média est mesuré en
CPU et en matériel, en tâche de fond (`DecodeProfile`, `decode-profile.json`,
lié à l'empreinte de l'installation). Le matériel est gardé tant qu'il n'est
**pas le goulot** : au moins 85 % du débit CPU, ou au moins 60 i/s. Il libère en
effet le CPU pour les filtres et l'encodage du même rendu. Avant toute mesure,
un choix provisoire s'applique : matériel pour le 10 bits, l'UHD, et le
HEVC/AV1/VP9 en Full HD ; CPU pour le H.264 léger.

Mesuré sur Apple M4 (10 cœurs) : le CPU décode le H.264 4K **plus vite** que
VideoToolbox (214 contre 88 i/s), mais consomme 20 ms de CPU par image contre
2,9 ms. Comparer les seuls débits aurait mené au mauvais choix.

**Repli.** Une commande FFmpeg qui échoue en décodage matériel est relancée une
fois en CPU (`run_with_decode_fallback`). Après deux échecs, le couple est
banni pour la session (`DecodeHealth`). Les replis sont comptés et visibles dans
le diagnostic. Le journal reçoit un avertissement au premier échec, puis au
bannissement, jamais un message par image.

Un choix **explicite** indisponible n'est pas masqué : la raison
(« … ne décode pas HEVC sur cette machine ») figure dans le diagnostic, et le
décodage se fait en CPU.

**Moniteur temps réel.** Qt lit sa variable une seule fois par processus. Un
nouveau mode s'applique donc au moniteur au prochain démarrage, et tout de
suite aux segments, aux proxies et au tracking (l'interface l'indique). Une
valeur de `QT_FFMPEG_DECODING_HW_DEVICE_TYPES` posée par l'utilisateur est
respectée.

**Original ou proxy** (`choose_preview_source`). Les quatre combinaisons
(original ou proxy, matériel ou CPU) sont estimées par leur coût de décodage :
mesuré si possible, ordre de grandeur sinon. Un proxy assez net est pris s'il
n'est pas plus cher. Un proxy trop petit pour la qualité demandée n'est pris
que s'il coûte au moins deux fois moins. Un proxy n'est donc **pas** supposé
toujours plus rapide : un original H.264 Full HD décodé en matériel bat un
proxy ProRes décodé en CPU. Les sondes nécessaires se font en tâche de fond ;
d'ici là, le proxy prêt est pris, comme avant.

## Formats de pixel et carte des conversions

Images livrées par Qt sur ce Mac :
- VideoToolbox : NV12 (8 bits), P010 (10 bits) ;
- logiciel : YUV420P, YUV420P10.

Elles sont envoyées **telles quelles**, un plan par texture :

| Format | Textures | Normalisation |
| --- | --- | --- |
| NV12 / P010 / P016 | Y `R8`/`R16`, UV `RG8`/`RG16` | P010 : 10 bits alignés en haut (× 65535/65472) |
| YUV420P / 422P / 444P | Y, U, V `R8` | — |
| YUV420P10 / 422P10 | Y, U, V `R16` | 10 bits alignés en bas (× 65535/1023) |
| RVBA / BGRA | `RGBA8` | permutation BGRA dans le shader |
| autres | `QVideoFrame.toImage()` | conversion **CPU** par Qt, comptée et journalisée une fois par format |

Où ont lieu les conversions :

| Étape | Chemin GPU | Chemin CPU |
| --- | --- | --- |
| Décodage → mémoire système | NV12/P010 mappés (Qt, sans copie) | idem |
| Envoi | **1 copie** par plan (`bytes`), puis Qt → GPU | — |
| YUV → RVB | shader (matrice du flux) | Qt |
| Effets, fusion, masques | shader | segments FFmpeg |
| Affichage | `QRhiWidget` | raster |

Matrice couleur : celle qu'indique l'étiquette du flux. Un flux **non étiqueté**
est lu en **BT.601 plage limitée**, exactement comme `swscale` à l'export,
vérifié au bit près sur des couleurs pures. Une étiquette BT.709 donne bien la
conversion BT.709.

### Copie zéro : ce qui a été évalué

1. **Textures de Qt Multimedia.** `QVideoSink.setRhi` permet à Qt de livrer des
   images déjà sur le GPU, mais la texture n'est pas exposée publiquement, ni en
   C++ stable ni en Python. Non retenu.
2. **Pointeur brut ou `memoryview`.** PySide6 6.11 refuse les deux dans
   `QRhiTextureSubresourceUploadDescription`, malgré sa signature. Le plan est
   donc copié une fois en `bytes` : environ 1 ms pour 8 Mo, environ 3 ms pour une
   image 4K P010. Le pas de ligne du décodeur est transmis tel quel
   (`setDataStride`), sans recopie ligne à ligne.
3. **Relecture GPU → CPU.** Aucune dans le moniteur GPU. Le moniteur CPU de Qt,
   lui, convertit sur le GPU puis relit pour peindre en raster.

## Backend graphique

**Retenu : QRhi** (`QRhiWidget`, PySide6 ≥ 6.7), l'abstraction de Qt 6 :

| Plateforme | API |
| --- | --- |
| macOS | Metal |
| Windows | Direct3D 11 |
| Linux | OpenGL |

Aucun moteur graphique maison. Les shaders sont écrits une fois en GLSL Vulkan
4.40, puis compilés par `qsb` (fourni avec PySide6) en SPIR-V, GLSL (100 es,
120, 150, 330), HLSL 5.0 et MSL 1.2. Les `.qsb` sont versionnés dans
`assets/shaders`, et l'application n'a pas besoin de `qsb`. Après modification
d'une source :

```
python -m tools.gpu.build_shaders           # régénère
python -m tools.gpu.build_shaders --check   # périmé ? (un test le vérifie aussi)
```

Les shaders se limitent au sous-ensemble GLSL 1.20 : pas d'opérateurs bit à
bit, pas d'`abs` ni de `max` entiers. La variante 1.20 est celle que Qt choisit
pour un contexte OpenGL de compatibilité (macOS, vieux pilotes).

Écarté :
- `QOpenGLWidget` comme viewport du `QGraphicsView` : OpenGL est déprécié sur
  macOS, et le choix aurait imposé une composition OpenGL à toute la fenêtre ;
- Vulkan et Direct3D 12 : ils demandent une instance dédiée, pour un gain
  incertain dans une première version.

**Intégration.** Le `QRhiWidget` est placé **sous** la vue `QGraphicsView` du
viewer, rendue transparente. Poignées, repères, calques motion graphics et
trackers restent dessinés par Qt au-dessus, sans aucun changement. La
composition à l'écran a été vérifiée par capture de la fenêtre réelle. En
`offscreen` (CI), QRhi n'existe pas : Auto choisit le CPU sans bruit.

Validé sur deux backends réels (Apple M4) : **Metal** et **OpenGL**, ce
dernier dans sa variante GLSL 1.20 et avec la convention d'axe Y inverse. Tous
les formats, effets et modes de fusion ont été comparés à la référence numpy
(`python -m tools.gpu.selfcheck --api metal|opengl`). Écart maximal :
3,5/255, et moins de 1/255 en moyenne.

## Ce que montre le moniteur GPU

Par rapport au moniteur CPU, le moniteur GPU montre **en temps réel** :

- **Transform** : position, échelle, rotation, ancrage, échelle X/Y, miroirs,
  opacité. C'est la même matrice que l'export
  (`core.tracking_motion.video_layer_matrix`), tracking compris.
- **Recadrage** : zone utile du média et bandes noires opaques du `pad`, comme
  à l'export. La matte du recadrage de stabilisation s'applique aussi.
- **Effets simples** : correction (luminosité, contraste, saturation), flou,
  netteté, vignette, noir et blanc, sépia (matrice couleur).
- **Modes de fusion** : les huit modes du produit, formules séparables du W3C,
  identiques à `core.blend_modes`.
- **Masques** : rectangle, ellipse, polygone, contour adouci, dilatation,
  opacité, inversion et combinaisons. La matte est rastérisée par le **même
  code que l'export** (`render_layer_matte`), puis appliquée sur le GPU dans
  l'espace du calque. Elle suit donc le transform sans être recalculée, et reste
  en cache GPU tant que les masques ne bougent pas.
- **Calques d'effets** : leurs effets s'appliquent à la vidéo en dessous, à
  travers leur couverture, comme `_compose_adjustment` à l'export.
- **Étalonnage** (depuis le 2026-10-09) : exposition, contraste, saturation,
  température, teinte, ombres, hautes lumières, courbes et LUT `.cube`, du clip
  affiché comme d'un calque d'effets. La chaîne de l'export est cuite en LUT 3D
  par FFmpeg lui-même (voir « Étalonnage » plus bas), en lecture comme à l'arrêt.

**Ce qui reste au rendu fidèle** :
- les transitions ;
- la composition exacte de plusieurs pistes vidéo superposées : le moniteur
  temps réel, CPU comme GPU, montre le clip du dessus ;
- les sous-titres incrustés par libass.

Dans ces cas, la pastille « FX » le signale, et les segments fidèles montrent le
résultat exact dès qu'ils sont prêts.

### Effets : mêmes formules que l'export, dans le même espace

L'export applique les effets sur les plans **YUV** du calque, après mise à
l'échelle et rotation. Le GPU fait de même, avant de convertir en RVB :

| Effet | Filtre de l'export | Sur GPU |
| --- | --- | --- |
| Correction | `eq` | chemin entier d'FFmpeg (hérité de MPlayer) : `⌊Y·⌊c·4096⌋/4096⌋ + B`, troncatures comprises |
| Flou | `gblur` | noyau séparable **exponentiel** `ν^\|k\|`, la réponse impulsionnelle exacte de son filtre récursif (`λ = σ²/2`) ; σ de la chroma × 2 en 4:2:0 |
| Netteté | `unsharp` 5×5 | luma : `Y + a·(Y − binomial[1 4 6 4 1]²)` |
| Vignette | `vignette` | `cos⁴(angle·d/dmax)` sur Y, chroma ramenée vers 127 ; `dmax = hypot(w, h)/√2`, car le filtre `rotate` de l'export agrandit d'abord l'image en carré |
| Noir et blanc | `hue=s=0` | chroma = ½ |
| Sépia | `colorchannelmixer` | matrice RVB 3×3 |

Ces formules ont été **retrouvées par la mesure**, pas supposées. Exemples :
- un échelon 16→235 flouté à σ = 3 par FFmpeg donne 100 puis 69 ; le noyau
  exponentiel prédit 100,4 puis 68,9 ;
- `eq` à −0,2 de luminosité donne un pas d'écart, parce que FFmpeg tronque
  `int(100·b + 100)`, et que le résultat dépend de la fusion FMA du compilateur
  (79 avec clang ARM, 80 sur x86).

Écart mesuré entre la référence GPU et le filtre FFmpeg de l'export (niveaux sur 255) :

| Effet | moyenne | 99ᵉ centile |
| --- | --- | --- |
| aucun | 0,21 | 1,0 |
| correction | 0,08 | 0,85 |
| vignette | 0,48 | 2,6 |
| noir et blanc | 0,80 | 1,4 |
| sépia | 0,31 | 1,1 |
| netteté | 0,25 | 1,6 |
| flou σ = 3 / σ = 12 | 1,6 / 1,3 | 14 / 12 (bords de l'image) |
| chaîne correction + flou + vignette + sépia | 0,66 | 4,4 |

Le GPU réel s'écarte de cette référence de moins de 1/255 en moyenne.

### Étalonnage : la chaîne de l'export, cuite en LUT 3D

L'export étalonne un calque avec `eq` → `colorbalance` → `lutrgb` (roues lift /
gamma / gain / offset) → `curves` → `lut3d` (`_build_color_grade_filters`) ; un
clip étalonné par nœuds enchaîne cette suite nœud après nœud (voir
[color.md](color.md)). Réécrire ces filtres en shader aurait fait
diverger le moniteur au premier détail (arrondis 8 bits d'`eq`, interpolation de
`curves`, `lut3d` tétraédrique…). Le moniteur ne les réécrit donc pas
(`core/gpu_grade.py`), et la chaîne entière, quel que soit le nombre de nœuds,
tient dans **une** LUT :

1. un **réseau de couleurs** — 52³ nœuds, des codes 8 bits entiers (multiples de
   5) dans l'espace d'entrée du calque — passe dans **cette chaîne exacte**, par
   FFmpeg, avec les propriétés de couleur du média (`setparams` : matrice et
   plage) ; un calque RVB (média RVB, calque d'effets) part en RVBA, comme la
   composition de l'export ;
2. le résultat est une LUT 3D (couleur du calque → RVB étalonné), rangée en
   **atlas 2D** (52 tranches côte à côte, 2704 × 52) et téléversée comme une
   matte : aucune texture 3D, valable sur tous les backends ;
3. la passe `grade` (après les effets, avant la composition, comme à l'export)
   convertit la couleur dans l'espace de la LUT puis la lit en trilinéaire :
   bilinéaire matérielle dans une tranche, puis entre les deux tranches voisines.

La cuisson prend environ 30 ms, **hors du fil de l'interface**
(`GradeLutCache`) : seule la dernière demande attend (glisser un curseur ne cuit
pas les valeurs intermédiaires), la LUT précédente reste affichée pendant la
cuisson (pas de clignotement sans étalonnage), un échec est mémorisé (LUT
illisible, FFmpeg absent : le calque passe sans étalonnage, les segments fidèles
le montrent).

Mesuré (`tests/test_gpu_grade.py`, vidéo 4:2:0 étalonnée : exposition,
contraste, saturation, température, ombres, courbe en S, LUT `.cube`) contre
l'export réel, sur une mire aux couleurs saturées : **0,1 à 0,15 niveau**
d'écart moyen, **3 à 4** au 99ᵉ centile, sur les zones où la chroma ne change
pas brusquement. Les écarts restants sont au bord du gamut : là où l'étalonnage
écrête un canal, l'interpolation entre deux nœuds adoucit le coude de quelques
niveaux. Aux bords francs de chroma,
l'écart est celui du moniteur sans étalonnage (chroma traitée à pleine
résolution, voir plus haut) : la LUT n'y ajoute rien. Sur le vrai GPU
(`tools/gpu/selfcheck.py`, cas `grade`, LUT non linéaire de test) : 0,3 niveau
en moyenne et moins de 2 au pire, Metal et OpenGL, NV12 / YUV420P / P010.

**Défaut de l'export trouvé en chemin, corrigé (2026-10-09).** L'export
appelait `colorbalance` avec `pl=1` (conserver la luminosité). FFmpeg y met la
saturation à zéro dès qu'un canal vaut exactement 0 ou 255 après réglage : un
rouge saturé (230, 40, 20) passé en saturation 1,3 avec une température sortait
**gris** (128, 128, 128) — sur une mire saturée, 50 à 88 % des pixels. La même
option annulait les **ombres** et **hautes lumières**, qui décalent les trois
canaux d'autant : la luminosité rétablie effaçait le réglage. `pl=1` est retiré ;
température et teinte, décalages rouge / bleu opposés, gardent d'elles-mêmes la
luminosité (0,06 niveau d'écart mesuré hors pixels grisés). Tests de rendu réel :
`tests/test_color_grade_export.py`. Le moniteur suit sans rien changer : il cuit
la chaîne de l'export.

## Aperçu GPU contre export

`tests/test_gpu_pipeline.py::test_gpu_preview_matches_the_export_frame` rend la
même image du même clip de deux façons, puis les compare :
- par l'**export réel** (`build_frame_command`) ;
- par le pipeline GPU (sa référence numpy, elle-même vérifiée contre le GPU
  réel).

Quatre cas sont couverts : identité ; transform avec rotation et opacité ;
correction + vignette à l'échelle 0,8 ; flou + sépia.

Résultat :
- **géométrie** identique à 1,5 px près ;
- **couleurs** à 2–4 niveaux en moyenne une fois l'alignement compensé.

Le pixel d'écart vient de l'export lui-même. Son filtre `rotate` agrandit
l'image en carré `hypot(w, h)` même à 0°, donc la recentre sur un demi-pixel et
la rééchantillonne en bilinéaire. S'y ajoutent les arrondis entiers
d'`overlay`. C'est une imprécision de l'export existant ; la corriger
changerait le rendu final, ce qui sort de ce chantier (voir les limites).

**Le décodage matériel ne change pas les pixels.** VideoToolbox donne, au bit
près, les mêmes images que le décodeur logiciel en H.264 et HEVC
(`tests/test_gpu_hardware.py`). L'export reste néanmoins en CPU, pour ne
dépendre d'aucun pilote.

## Qualité adaptative, motion blur

La qualité Auto existante mesure la cadence réelle des ticks de lecture. Le
moniteur GPU y ajoute deux signaux (`core.preview_governor`) :
- plus de 20 % d'images perdues ;
- un 95ᵉ centile du temps de rendu au-delà de 75 % du budget d'une image.

Ces signaux **marquent** la fenêtre de mesure comme surchargée. La politique
anti-yo-yo existante s'applique ensuite telle quelle : fenêtres consécutives,
délai de repos, remontée lente. Les leviers, sans jamais toucher à l'export :
- résolution d'aperçu réduite, ce qui réduit aussi la résolution de rendu GPU et
  le coût des flous ;
- proxy plus léger demandé en tâche de fond ;
- motion blur coupé en lecture ;
- repli GPU → CPU sur erreur.

**Motion blur** (`core.motion_blur.preview_quality`) :
- en lecture : coupé ;
- à l'arrêt : il suit le niveau d'aperçu — Plein, réglage complet ; 1/2, au plus
  4 échantillons ; 1/4 et moins, coupé ;
- export : toujours complet.

Les échantillons des calques motion graphics restent calculés par le
rastériseur Qt partagé avec l'export, pour la parité.

## Cache GPU et mémoire

**Cache de textures** (`core.gpu_cache`). Il garde **seulement** ce qui
resservira tel quel : mattes de masques fixes, couvertures des calques d'effets.
Il a un budget (Auto : 1/16 de la mémoire, entre 64 et 512 Mo), une politique
LRU, et refuse une entrée plus grosse que le budget. Il est purgé au changement
de projet, sous pression mémoire et à la perte du périphérique.

Gain mesuré : 4,6 → 3,2 ms par image en 1080p avec une matte fixe. Les images
vidéo ne passent pas par ce cache : leurs textures sont réutilisées tant que le
format ne change pas. Les tests vérifient zéro fuite sur 2 000 cycles et un
budget jamais dépassé.

**Mémoire** (`core.memory_monitor`) :
- Linux : `/proc/meminfo` ;
- macOS : pourcentage libre selon le noyau, celui de `memory_pressure` ;
- Windows : `GlobalMemoryStatusEx`.

Sur ce Mac, le niveau « warn » du noyau apparaît alors que 40 % de la mémoire
est libre : il ne sert donc qu'au niveau critique. Réaction, une fois par montée
de pression :
- **alerte** : purge du cache GPU et du cache mémoire ;
- **critique** : en plus, aperçu réduit et retour au moniteur CPU (textures
  libérées).

**VRAM.** Elle n'est pas lisible de façon portable (mémoire unifiée sur Apple
Silicon). Le diagnostic affiche les octets de textures que Kut-Studio alloue
lui-même : cache et textures de travail.

## Pannes du GPU

Toute exception pendant le rendu, `renderFailed` de Qt, ou `QRhi.isDeviceLost()`
déclenchent la même séquence :
1. le widget émet `failed` hors de `render()` ;
2. le viewer libère les ressources et rebranche le lecteur sur le moniteur CPU ;
3. un message discret s'affiche dans la barre d'état, et la session continue.

Après une perte de périphérique, un seul nouvel essai GPU est tenté. Après deux
échecs, le GPU n'est plus retenté avant le prochain démarrage, ou avant que
l'utilisateur rechoisisse « GPU ». Aucun clignotement entre GPU et CPU.

Trois cas ne sont **pas** des pannes du GPU :

- une image dont `map()` échoue (surface matérielle, tampon repris par le décodeur) est convertie par Qt
  (copie CPU) et comptée (`RhiExecutor.unmappable_frames`) au lieu de condamner le moniteur ;
- quand Qt libère les ressources du widget (masqué, détaché, déplacé), la dernière image de chaque source est
  renvoyée à la recréation : en pause, le moniteur ne reste pas noir. Un relâchement explicite (changement de projet,
  pression mémoire) abandonne tout, images décodées comprises ;
- un **plantage du processus** pendant que le GPU était actif (erreur fatale du pilote) ne laisse aucune trace que
  `GpuHealth` puisse voir. `GpuCrashGuard` pose un marqueur à l'activation du GPU et le retire à l'arrêt propre ou au
  repli CPU ; resté en place au démarrage suivant, il garde le mode Auto sur le CPU (avec l'explication) jusqu'à ce que
  l'utilisateur choisisse lui-même le rendu de l'aperçu. Un choix explicite « GPU » est toujours honoré.

## Diagnostic et journaux

Le diagnostic (Préférences > Performance > Matériel, bouton « Copier le
diagnostic ») couvre quatre parties :
- **Détection** : FFmpeg, encodeurs, hwaccels, décodeurs par famille ;
- **Décodage** : mode, valeur passée à Qt, réussites et replis, couples bannis,
  débits mesurés ;
- **Aperçu** : rendu demandé et actif, repli, GPU et API, images reçues,
  affichées et perdues, temps de rendu moyen et p95, cache GPU et textures,
  recalages du lecteur ;
- **Mémoire**.

Journaux : `kut_studio.decode` pour le choix, les mesures, les échecs et les
bannissements ; `kut_studio.gpu` pour l'initialisation, le rendu retenu, les
pannes, la mémoire et les formats convertis par Qt.

## Deux corrections de fluidité trouvées en chemin

1. **La lecture ne recale plus le lecteur à chaque tick.**
   - *Avant* : `preview_at` appelait `setPosition` toutes les 40 ms. Mesuré sur
     un H.264 à GOP long : **aucune** image affichée en 5 s de lecture
     (123 recalages).
   - *Maintenant* : le lecteur garde son horloge et n'est recalé qu'au-delà de
     200 ms de dérive. Mesuré : 154 images (≈ 30 i/s), zéro recalage.
2. **Les segments d'aperçu fidèle ne gèlent plus la fenêtre.**
   - *Avant* : ils étaient rendus par FFmpeg *dans le thread de l'interface*.
   - *Maintenant* : un thread dédié les rend, et le minuteur ne fait que le
     réveiller. Un segment périmé tue son FFmpeg en quelques dizaines de
     millisecondes au lieu de le terminer.

## Mesures

`python -m tools.perf.gpu_bench --out docs/perf/gpu.json` (Apple M4, 10 cœurs,
FFmpeg 9.0.2 ; médias `testsrc2` bruités, 30 i/s) :

Médias à **fort débit** (bruit sur `testsrc2`, environ 500 Mb/s en 4K H.264, proche d'une caméra), 6 s à 30 i/s.

**Décodage FFmpeg** (images/s ; temps CPU par image) :

| Média | CPU | VideoToolbox |
| --- | --- | --- |
| 1080p H.264 | 189 i/s ; 42 ms | 77 i/s ; 2,5 ms |
| 4K H.264 | 49 i/s ; 166 ms | **24 i/s** ; 6,3 ms |
| 4K HEVC | 43 i/s ; 187 ms | 47 i/s ; 4,6 ms |
| 4K HEVC 10 bits | 30 i/s ; 274 ms | **54 i/s** ; 5,1 ms |
| proxy 720p H.264 | 412 i/s ; 18 ms | 163 i/s ; 1,2 ms |
| 4 × 1080p simultanés | 219 i/s au total | 277 i/s au total |

Le matériel n'est pas toujours plus rapide. Sur le H.264 4K à fort débit,
VideoToolbox plafonne à 24 i/s, en dessous du temps réel : la règle d'Auto
(85 % du débit CPU ou 60 i/s) choisit alors le CPU. En HEVC 10 bits, le
matériel gagne sur les deux tableaux.

**Scrubbing** (4K HEVC 10 bits, GOP de 120 images, délai jusqu'à la première
image après un saut) : médiane 1 947 ms en CPU contre **776 ms** en matériel
(maximum : 3,9 s contre 1,7 s).

**Aperçu fidèle** (segment de 2 s, sortie 1080p, graphe de l'export, source 4K HEVC 10 bits) :

| Segment | Décodage CPU | Décodage Auto (matériel) |
| --- | --- | --- |
| effets (correction, flou, vignette) | 3,40 s | **1,26 s** |
| compositing (2 pistes 4K, fusion, transform) | 4,78 s | 3,64 s |
| flou de mouvement (forme animée, 8 échantillons) | 3,00 s | **1,26 s** |

**Moniteur temps réel**, lecture 4 s, vue 1100×700 Retina :

| 4K HEVC 10 bits | i/s | CPU du processus | perdues | rendu GPU moy. / p95 | retard boucle p95 |
| --- | --- | --- | --- | --- | --- |
| CPU decode + CPU preview | 29,9 | 711 % | — | — | 10,2 ms |
| HW decode + CPU preview | 30,0 | 63 % | — | — | 14,8 ms |
| CPU decode + GPU preview | 29,0 | 656 % | 4 | 3,4 / 3,6 ms | 4,4 ms |
| **HW decode + GPU preview** | **30,0** | **32 %** | **0** | 7,2 / 8,9 ms | 9,0 ms |
| HW decode + GPU preview + effets | 29,2 | 32 % | 3 | 7,5 / 9,0 ms | 22,6 ms |

| 1080p H.264 | i/s | CPU du processus | perdues | rendu GPU moy. / p95 | retard boucle p95 |
| --- | --- | --- | --- | --- | --- |
| CPU decode + CPU preview | 29,6 | 134 % | — | — | 3,1 ms |
| HW decode + CPU preview | 29,7 | 43 % | — | — | 8,1 ms |
| CPU decode + GPU preview | 29,5 | 120 % | 1 | 0,7 / 0,9 ms | 1,3 ms |
| **HW decode + GPU preview** | **29,6** | **23 %** | **0** | 3,1 / 4,0 ms | 4,4 ms |
| HW decode + GPU preview + effets | 29,4 | 23 % | 1 | 3,0 / 4,2 ms | 16,8 ms |

Lecture des colonnes :
- **CPU du processus** : 100 % correspond à un cœur. Le décodage matériel
  divise la charge par 11 en 4K 10 bits, et par 6 en 1080p.
- **Rendu GPU** : temps de **soumission** côté CPU, envoi des plans compris. Il
  est plus élevé avec le décodage matériel, car Qt mappe alors l'image
  VideoToolbox (IOSurface) avant la copie.
- **Retard de la boucle** : jusqu'à 23 ms au 95ᵉ centile avec les effets,
  parce que le rendu GPU se fait dans le thread de l'interface (`QRhiWidget`).

**Mémoire** : 1,0 Go maximum en 4K 10 bits, contre 1,1 Go pour le moniteur CPU.
Textures GPU de travail : 55 Mo en 4K, 71 Mo avec effets ; 35 Mo et 50 Mo en
1080p.

**Cache GPU** : avec une matte de masque fixe, le rendu 1080p passe de 4,2 à
**2,9 ms** par image. Le cache garde 1 entrée de 8 Mo, au lieu d'une nouvelle
texture de 8 Mo à chaque image.

## Tests

- `tests/test_hardware_decoding.py` : détection simulée sur macOS, Windows
  (CUDA, D3D11VA, DXVA2, QSV) et Linux (VAAPI, CUDA) ; backend absent ;
  backend listé mais cassé ; codec non vérifiable ; runner défaillant ; cache v1
  ignoré ; interrupteur de décodage.
- `tests/test_decode_policy.py` : règles par usage, Auto, mesures, choix
  explicite indisponible, bannissement, journaux sans spam, variable Qt, repli
  dans les segments, les proxies et le tracking, choix original ou proxy.
- `tests/test_gpu_pipeline.py`, sans GPU :
  - formats et matrices, effets contre FFmpeg ;
  - passes, uniformes, fusions, mattes, calques d'effets ;
  - équivalence avec l'export ;
  - cache (budget, LRU, fuites), mémoire, choix du rendu, gouverneur, motion blur ;
  - préférences, shaders à jour et valides ;
  - seuils de performance : coût d'un plan, cache, imports.
- `tests/test_gpu_preview_ui.py`, avec un faux GPU :
  - description envoyée au GPU ;
  - perte de périphérique simulée et retour au CPU ;
  - vrai widget en `offscreen` (échec propre) ;
  - lecture sans recalage ;
  - préférences, pression mémoire, changement de projet ;
  - rendu des segments hors du thread d'interface ;
  - format `.kut` inchangé.
- `tests/test_gpu_grade.py` et `tests/test_gpu_grade_ui.py`, sans GPU : la LUT
  cuite contre l'export réel (clip YUV, calque d'effets RVB), réseau, commande,
  clé, passe `grade` et référence, cache de cuisson (fil, dernière demande,
  échec mémorisé), LUT précédente gardée pendant une cuisson, câblage.
- `tests/test_gpu_hardware.py`, **optionnel** : vrai GPU (Metal et OpenGL ici),
  et décodage matériel au bit près. Actif sur un Mac de développement hors CI,
  ou partout avec `KUT_STUDIO_GPU_TESTS=1`. La CI n'a jamais besoin d'un GPU.

## Ajouter…

**Un backend de décodage** :
1. une valeur dans `DecodeMode`, avec son nom `-hwaccel` (`HWACCEL_NAMES`),
   son format matériel (`HW_OUTPUT_FORMATS`), son libellé et sa place dans
   `DECODE_AUTO_ORDER` ;
2. si l'initialisation est particulière (périphérique, décodeurs dédiés),
   compléter `decode_input_args` et `decode_prerequisite_error` ;
3. un cas simulé dans `tests/test_hardware_decoding.py`.

Détection, validation, cache, Auto, repli, Qt et diagnostic suivent sans autre
changement.

**Une famille de codec** : une entrée `DecodeCodec` (codecs ffprobe,
profondeur, chroma, format de `hwdownload`, encodeur d'échantillon).

**Une API graphique** : une entrée dans `GRAPHICS_APIS` (`core/gpu_backend.py`)
et dans `_APIS` (`ui/gpu_preview.py`). Les shaders `.qsb` contiennent déjà
toutes les variantes.

**Un effet GPU** :
1. sa traduction dans `core.gpu_effects.program_for` : opération ponctuelle
   (`PointOp`, espace YUV ou RVB) ou de voisinage ;
2. sa formule dans `_apply_point` (la référence numpy) et dans
   `tools/gpu/shader_sources.py`, avec le même numéro d'opération ;
3. `python -m tools.gpu.build_shaders` ;
4. un cas dans `test_gpu_effects_match_the_ffmpeg_filters_of_the_export`, et
   un dans `tools/gpu/selfcheck.py` pour le GPU réel.

**Plus tard** (non implémenté, points d'entrée prévus) :
- scopes GPU : une passe qui lit le cadre composé et réduit en histogramme ;
- tracking ou flot optique : passes de voisinage sur les textures de travail ;
- rendu 3D : une passe de plus avant `present`.

## Limites

- **Moniteur temps réel** : une seule piste vidéo (celle du dessus), en CPU
  comme en GPU. La composition exacte de plusieurs pistes vidéo superposées,
  avec leurs modes de fusion, reste au rendu fidèle. Le mode de fusion du clip
  affiché s'applique contre le fond noir du cadre.
- **Calques d'effets** : sur GPU, ils s'appliquent à la vidéo, pas aux calques
  motion graphics situés dessous. Ces calques sont dessinés par Qt au-dessus du
  GPU.
- **Hors temps réel** : les transitions, les sous-titres libass et la
  composition de séquences imbriquées restent au rendu fidèle.
- **Étalonnage** : le moniteur CPU ne le montre toujours qu'avec les segments
  fidèles ; sur GPU, l'étalonnage d'un calque motion graphics (dessiné par Qt)
  aussi. Chaque nouveau réglage demande une cuisson (≈ 30 ms, hors du fil de
  l'interface) : pendant ce temps, la LUT précédente reste affichée. Au bord du
  gamut, là où l'étalonnage écrête un canal, la LUT adoucit le coude de quelques
  niveaux.
- **Une copie CPU par plan et par image** (limite de PySide6 6.11). Elle coûte
  environ 3 ms en 4K P010 ; pas de copie zéro depuis le décodeur de Qt.
- **Décodage du moniteur** : un nouveau mode ne s'applique qu'au prochain
  démarrage, parce que Qt lit sa variable une fois par processus.
- **Export** : sa position est imprécise d'environ un pixel, à cause de `rotate`
  même à 0° et des arrondis d'`overlay`. C'est un comportement existant, mesuré
  et documenté, mais pas corrigé ici, car la correction changerait le rendu
  final.
- **Matériel non testé ici** : Direct3D 11, Vulkan et les décodeurs
  CUDA/D3D11VA/QSV/VAAPI ne sont vérifiés que par simulation sur cette machine.
  Les vrais essais sur Windows et Linux restent à faire sur ces plateformes
  (`tests/test_gpu_hardware.py` avec `KUT_STUDIO_GPU_TESTS=1`).
- **VRAM** : non lisible de façon portable, seules les textures de Kut-Studio
  sont comptées.
- **Flou fort** : au-delà de 96 texels de rayon, le noyau est tronqué.
- **Aucune mesure GPU** dans la qualité Auto du moniteur CPU : seule la cadence
  des ticks est mesurée.
- **Décodeur du moniteur non mesuré par codec** : Qt reçoit la liste des
  backends validés. Pour un H.264 4K à très fort débit, que VideoToolbox décode
  sous le temps réel, le moniteur reste en matériel. Choisir « CPU » règle ce cas.
- **Rendu GPU dans le thread de l'interface** (`QRhiWidget`) : avec des effets
  en 4K, la boucle d'événements prend jusqu'à environ 23 ms de retard au
  95ᵉ centile.
