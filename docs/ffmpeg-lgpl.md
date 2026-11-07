# FFmpeg LGPL embarqué (Mac App Store)

Premier jalon de l'étape 2 de l'[ADR-0013](adr/0013-publication-mac-app-store.md) : **construire, auditer et tester** le FFmpeg que la
version Mac App Store embarquera. Rien ne change pour les autres distributions : `build.py` n'embarque FFmpeg que si
`KUT_STUDIO_FFMPEG_DIR` désigne un dossier, et `core/tool_paths.py` résout toujours variable explicite, puis `KUT_STUDIO_FFMPEG_DIR`,
puis `bin/` du bundle, puis `PATH`, puis Homebrew. **Aucun code de l'application n'est modifié par ce jalon** (un outil et un test sont ajoutés).

Étiquettes : **[V]** mesuré ou lu à la date ci-dessous (2026-10-28, macOS 27.2 arm64, Xcode 27.1) ; **[S]** supposé ; **[A]** à faire valider
(juridique, brevets, règle Apple). Ce n'est pas un avis juridique.

## Reconstruire

```sh
packaging/ffmpeg-lgpl/build.sh                       # sources, outils et préfixe dans packaging/ffmpeg-lgpl/_build (ignoré par git)
export KUT_STUDIO_FFMPEG_DIR="$PWD/packaging/ffmpeg-lgpl/_build/prefix/bin"
python -m tools.ffmpeg_license_audit --ffmpeg "$KUT_STUDIO_FFMPEG_DIR/ffmpeg"          # filtres
python -m tools.ffmpeg_license_audit --codecs --ffmpeg "$KUT_STUDIO_FFMPEG_DIR/ffmpeg"  # encodeurs, décodeurs, muxers (tableau)
python -m pytest tests/test_ffmpeg_lgpl_real.py                                          # tests réels (KUT_STUDIO_REQUIRE_FFMPEG_LGPL=1 : échec au lieu de saut)
```

`KUT_FFMPEG_LGPL_DIR=/chemin` déplace le dossier de construction. Le script exige macOS arm64 et les outils de ligne de commande Xcode ; il
ne lit rien dans Homebrew (`PATH` réduit à `/usr/bin:/bin`, `--disable-autodetect`), n'installe rien hors de son dossier, n'utilise aucun secret
et ne signe rien. Les outils de construction (pkgconf, meson, ninja) sont construits ou installés **dans** ce dossier et **ne sont pas livrés**.

**Durée mesurée [V]** : environ 7 minutes sur un Mac à 10 cœurs (première construction, téléchargements compris) ; l'artefact fait 23 Mo par
binaire (`ffmpeg`, `ffprobe`), statique, sans bibliothèque tierce dynamique.

### Versions et empreintes (`packaging/ffmpeg-lgpl/versions.env`)

Chaque archive est vérifiée par SHA-256 avant extraction (une empreinte fausse arrête tout). Les empreintes ont été **recoupées avec les formules
Homebrew** de la même version le 2026-10-28 (`brew info --json=v2`) : elles ne viennent pas seulement du téléchargement qu'on vérifie ensuite.

| Composant | Version | Licence | Rôle | SHA-256 de l'archive |
| --- | --- | --- | --- | --- |
| FFmpeg | 7.1.5 | LGPL 2.1 ou ultérieure (aucun `--enable-gpl`, `--enable-version3` ni `--enable-nonfree`) | programme | `de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f` |
| libass | 0.17.5 | ISC | sous-titres (filtre `subtitles`) ; polices du système par CoreText | `2dca25c0e0c837ddf00b52011b3f82cac1e4ddd3ad018227806b0c2288864acc` |
| FreeType | 2.15.0 | FreeType License (FTL, BSD avec mention) | rendu des glyphes | `93cd47eb719c325107f2d3cf087d06299e944c3b48a40b55537302304dea1dd5` |
| HarfBuzz | 15.0.0 | MIT | mise en forme du texte | `968536e9abd782f07f7954044c0c4020199fcdd39b41d221ec42d2a3be4a44df` |
| dav1d | 1.5.4 | BSD-2-Clause | décodeur AV1 logiciel (`libdav1d`) ; arm64 en assembleur NEON | `2abfb0c89212e6e4733a54e0ae509ec00a5b845a6360946f918806e14aedb011` |
| FriBidi | 1.0.17 | LGPL 2.1 ou ultérieure | texte bidirectionnel (arabe, hébreu) | `6949dcde27d41cebad1fd741fcafc36d55a1020d2d872d4a6eb3914caabbada2` |
| zlib, libiconv, AudioToolbox, VideoToolbox, CoreText… | du système | Apple (système) | liés dynamiquement, **non livrés** | — |
| pkgconf 3.0.10, meson 1.9.1, ninja 1.13.0 | outils | ISC / Apache 2.0 | construction seulement | pkgconf : `9371b2f6372da574d4c6dc96fe9b1463037bcf6afcb6671c1555afde7b17e2de` |

Licences vérifiées dans les archives (textes copiés dans `packaging/ffmpeg-lgpl/licenses/`) : **aucune dépendance GPL**. **fontconfig n'est pas utilisé** :
libass lit les polices avec CoreText (`--disable-fontconfig --enable-coretext`), ce qui évite fontconfig (licence permissive, mais dépendances
d'expat, de `/usr/local`…) et reste le choix naturel sur macOS [V : le test `test_libass_draws_the_subtitle_of_the_application` dessine du texte]. FreeType est
construit sans HarfBuzz (pas de dépendance circulaire), sans libpng, bzip2 ni brotli. `libx264`, `libx265`, `libfdk_aac`, `libvidstab`, `rubberband` : absents.

**Reproductibilité** : sources épinglées par empreinte, `SOURCE_DATE_EPOCH` fixé, `PATH` hermétique. **Mesuré [V]** : une deuxième construction, au même chemin, avec sources et outils en cache (1 min 45 s), redonne des binaires **identiques bit à bit**, **avec dav1d** (`ffmpeg` SHA-256 `c41487dd…3dffe`, `ffprobe` `9403e131…26080`, Xcode 27.1 ; sans dav1d : `3c5a6f87…`). Le chemin du préfixe est écrit dans la chaîne `configuration:` du binaire : un autre dossier donne un autre hash (pas un autre comportement) ; pour comparer des hash, construire au même chemin. L'identité entre deux machines ou deux versions de Xcode n'est **pas** vérifiée [S].

## Ce que l'artefact contient et ne contient pas

Le tableau est la sortie de `python -m tools.ffmpeg_license_audit --codecs` sur l'artefact (2026-10-28) ; « fichiers » : où le code de l'application cite l'élément.
Les lignes « déclaré » sans fichier sont ce que l'application demande sans l'écrire en dur (décodeurs des médias importés, formats de sortie).

| catégorie | nom | rôle | fichiers | LGPL |
| --- | --- | --- | --- | --- |
| decoder | `aac` | décodage des médias importés | — | présent |
| decoder | `alac` | décodage des médias importés | — | présent |
| decoder | `av1` | décodage des médias importés | — | présent |
| decoder | `ffv1` | décodage des médias importés | — | présent |
| decoder | `flac` | décodage des médias importés | — | présent |
| decoder | `gif` | décodage des médias importés | — | présent |
| decoder | `h264` | décodage des médias importés | — | présent |
| decoder | `hevc` | décodage des médias importés | — | présent |
| decoder | `libdav1d` | décodage des médias importés | — | présent |
| decoder | `mjpeg` | décodage des médias importés | — | présent |
| decoder | `mp3float` | décodage des médias importés | — | présent |
| decoder | `mpeg4` | décodage des médias importés | — | présent |
| decoder | `opus` | décodage des médias importés | — | présent |
| decoder | `pcm_f32le` | décodage des médias importés | — | présent |
| decoder | `pcm_s16le` | décodage des médias importés | — | présent |
| decoder | `pcm_s24le` | décodage des médias importés | — | présent |
| decoder | `png` | décodage des médias importés | — | présent |
| decoder | `prores` | décodage des médias importés | — | présent |
| decoder | `rawvideo` | décodage des médias importés | — | présent |
| decoder | `utvideo` | décodage des médias importés | — | présent |
| decoder | `vorbis` | décodage des médias importés | — | présent |
| decoder | `vp8` | décodage des médias importés | — | présent |
| decoder | `vp9` | décodage des médias importés | — | présent |
| decoder | `webp` | décodage des médias importés | — | présent |
| demuxer | `avi` | entrée | — | présent |
| demuxer | `concat` | entrée | core/export_engine.py | présent |
| demuxer | `f32le` | entrée | — | présent |
| demuxer | `flac` | entrée | — | présent |
| demuxer | `gif` | entrée | — | présent |
| demuxer | `image2` | entrée | — | présent |
| demuxer | `lavfi` | entrée | core/film_render.py, core/hardware_decoding.py, core/hardware_encoding.py … | présent |
| demuxer | `matroska` | entrée | — | présent |
| demuxer | `mov` | entrée | — | présent |
| demuxer | `mp3` | entrée | — | présent |
| demuxer | `mpegts` | entrée | — | présent |
| demuxer | `ogg` | entrée | — | présent |
| demuxer | `rawvideo` | entrée | — | présent |
| demuxer | `s16le` | entrée | — | présent |
| demuxer | `wav` | entrée | — | présent |
| encoder | `aac` | audio des exports MP4 / MOV | core/export_engine.py, core/filter_graph.py, core/social_deliverables.py | présent |
| encoder | `aac_at` | audio AAC par AudioToolbox (alternative) | — | présent |
| encoder | `ffv1` | flux intermédiaires sans perte | core/hardware_validation.py | présent |
| encoder | `h264_videotoolbox` | export H.264 (matériel Apple) | — | présent |
| encoder | `hevc_videotoolbox` | export HEVC (matériel Apple) | — | présent |
| encoder | `libsvtav1` | cité par le code | core/hardware_decoding.py | absent |
| encoder | `libvpx-vp9` | cité par le code | core/hardware_decoding.py | absent |
| encoder | `libx264` | cité par le code | core/film_render.py, core/filter_graph.py, core/hardware_decoding.py … | absent, GPL |
| encoder | `libx265` | cité par le code | core/hardware_decoding.py, core/video_encoders.py | absent, GPL |
| encoder | `pcm_f32le` | mixage direct | core/live_mix.py | présent |
| encoder | `pcm_s16le` | audio brut, transcription | core/transcription.py | présent |
| encoder | `png` | images de l'aperçu | core/export_engine.py, core/media_previews.py | présent |
| encoder | `prores_ks` | export ProRes (préréglage « ProRes Master ») | core/film_render.py, core/hardware_decoding.py | présent |
| encoder | `rawvideo` | images brutes | — | présent |
| encoder | `utvideo` | flux intermédiaires sans perte | — | présent |
| hwaccel | `videotoolbox` | décodage matériel Apple | — | présent |
| muxer | `f32le` | sortie | core/live_mix.py | présent |
| muxer | `framemd5` | sortie | — | présent |
| muxer | `image2pipe` | sortie | core/export_engine.py, core/media_previews.py | présent |
| muxer | `matroska` | sortie | core/retime_prepare.py | présent |
| muxer | `md5` | sortie | — | présent |
| muxer | `mov` | sortie | — | présent |
| muxer | `mp4` | sortie | — | présent |
| muxer | `null` | sortie | core/decode_policy.py, core/hardware_decoding.py, core/hardware_encoding.py … | présent |
| muxer | `rawvideo` | sortie | core/color_pick.py, core/gpu_grade.py, core/hardware_validation.py … | présent |
| muxer | `s16le` | sortie | core/audio_envelope.py, core/audio_sync.py | présent |
| muxer | `wav` | sortie | — | présent |

**Filtres** (`python -m tools.ffmpeg_license_audit`, 111 noms lus dans le code, **76 sont des filtres, tous présents, aucun GPL**) : `acompressor`, `acrossfade`, `adelay`, `aecho`, `aeval`, `aevalsrc`, `afade`, `afftdn`, `aformat`, `alimiter`, `amerge`, `amix`, `anullsrc`, `apad`, `aresample`, `asetnsamples`, `asetpts`, `asetrate`, `asplit`, `atempo`, `atrim`, `bandpass`, `bass`, `biquad`, `blend`, `chromakey`, `color`, `colorbalance`, `colorchannelmixer`, `colorspace`, `concat`, `convolution`, `crop`, `curves`, `despill`, `ebur128`, `equalizer`, `format`, `fps`, `gblur`, `geq`, `highpass`, `hstack`, `hue`, `loop`, `loudnorm`, `lut`, `lut3d`, `lutrgb`, `mix`, `overlay`, `pad`, `pan`, `premultiply`, `reverse`, `rgbashift`, `rotate`, `scale`, `select`, `sendcmd`, `setparams`, `setpts`, `setsar`, `settb`, `sidechaincompress`, `split`, `subtitles`, `testsrc2`, `tpad`, `treble`, `trim`, `unpremultiply`, `unsharp`, `vignette`, `volume`, `xfade`.
Limite assumée de la lecture du code : un nom de filtre calculé à l'exécution, ou employé sans option (`null`, `anullsink`), n'est pas vu ; les tests
`tests/test_ffmpeg_lgpl_guard.py` (chaînes réellement construites) et `tests/test_ffmpeg_lgpl_real.py` (rendu réel) couvrent cela.

### Encodeurs d'export : ce qui existe en LGPL, ce qui manque

| Besoin de l'application | Hors Store (GPL, Homebrew) | Artefact LGPL | État |
| --- | --- | --- | --- |
| H.264 MP4 / MOV | `libx264` (CPU) ou `h264_videotoolbox` | **`h264_videotoolbox` seul** [V : export réel réussi] | `libx264` manque (GPL) : plus d'encodage logiciel ; les réglages CRF n'ont pas d'équivalent exact (débit VideoToolbox) ; brevets H.264 **[A]** |
| HEVC MP4 | `libx265` ou `hevc_videotoolbox` | **`hevc_videotoolbox` seul** [V : encodeur validé par la détection de l'application] | `libx265` manque (GPL) ; brevets HEVC (pools multiples) **[A]** |
| ProRes MOV | `prores_ks` | **`prores_ks`** présent (implémentation native de FFmpeg, LGPL) [V]. `prores_videotoolbox` et `prores_aw` aussi (matériel Apple récent pour le premier) | licence de la **marque / format ProRes** pour un encodeur tiers **[A]** |
| AAC (audio MP4 / MOV) | `aac` | **`aac`** natif (LGPL) et **`aac_at`** (AudioToolbox) [V : export réel en `aac`] | brevets AAC **[A]** |
| PCM, FFV1, UT Video, PNG, rawvideo (internes, aperçus) | natifs | présents [V] | — |
| Décodage H.264, HEVC, ProRes, VP9, MPEG-4, MJPEG, AAC, MP3, FLAC, Opus, Vorbis, ALAC… | natifs | présents [V] | — |
| Décodage **AV1** logiciel | `libdav1d` | **`libdav1d`** présent (BSD-2, ajouté le 2026-10-28, décision utilisateur) [V : un AV1 de 3 images est décodé, `test_av1_decodes_in_software_with_libdav1d`] | encodage AV1 : aucun (non utilisé par l'export) |
| Encodeurs de **tests** de décodage matériel (`libvpx-vp9`, `libsvtav1`, `libx264`, `libx265`) | oui | absents | voir « À faire » : la détection du décodage matériel de l'application fabrique ses échantillons avec eux |

## Tests réels

`tests/test_ffmpeg_lgpl_real.py` (12 tests, sautés sans l'artefact ; `KUT_STUDIO_REQUIRE_FFMPEG_LGPL=1` les fait échouer s'il manque) :

* la construction est LGPL (`-L`, `-version`), possède libass / VideoToolbox / AudioToolbox, n'a ni `libx264` ni `libx265`, et **ne lie que `/usr/lib` et `/System/Library`** (`otool -L`) ;
* **AV1 logiciel** : un échantillon de 2 Ko (encodé hors dépôt) est décodé par `libdav1d` ; le test échoue sur la construction sans dav1d [V] ;
* l'audit des filtres et le tableau des codecs sortent propres ;
* **export de bout en bout** avec `run_export` (le lanceur de l'application, `KUT_STUDIO_FFMPEG_DIR` seul) : H.264 par VideoToolbox, AAC, effets animés (flou, vignette, sépia), étalonnage animé (`sendcmd` + `lut`), sous-titres libass ; le fichier relu par `ffprobe` est du H.264 + AAC de la bonne durée ;
* **aucun appel ne dépend du `PATH`** : `PATH=/nonexistent`, la résolution (`ffmpeg`, `ffprobe`) et l'export restent ceux de l'artefact ;
* libass dessine : sur un fond uni, des centaines de pixels clairs apparaissent pendant le sous-titre, aucun hors de lui ;
* **comparaison avec la référence** (Homebrew, `KUT_STUDIO_FFMPEG_REFERENCE` ou `ffmpeg` du `PATH`) sur les filtres déterministes : 75 images de la même chaîne d'effets et d'étalonnage animés, **écart nul** (max 0, moyenne 0,0) contre Homebrew 9.0.2 et contre `ffmpeg@7` 7.1.5 [V] ; le test tolère 3 niveaux d'écart pour ne pas casser sur une autre version ;
* l'export H.264 « CPU » **échoue clairement** (pas de `libx264`) au lieu de se replier en silence.

Constaté une fois, non reproduit : lancée hors pytest, une commande d'images brutes a fini par `Assertion best_input >= 0 failed at src/fftools/ffmpeg_filter.c:2122`
(FFmpeg 7.1.5, graphe avec `anullsink`) ; refaite 6 fois (dont 4 passes complètes de la suite), elle n'a plus échoué. Cause non établie ; à surveiller en CI.

## Obligations LGPL

L'application lance FFmpeg **comme un processus séparé** (`core/process_supervisor.py`, ADR-0013 §2) : Kut-Studio n'est pas lié à FFmpeg, c'est une
simple agrégation [A, juridique]. Les obligations portent donc sur la **distribution de l'exécutable FFmpeg** dans le `.app` :

1. **Notices et texte de la licence** dans l'application (écran « À propos / Licences », à faire à l'étape suivante) : `THIRD_PARTY_NOTICES.md` et `packaging/ffmpeg-lgpl/licenses/` en sont la source.
2. **Offre écrite de sources** de la **build exacte** (FFmpeg 7.1.5, libass, FreeType, HarfBuzz, FriBidi **et les scripts** `packaging/ffmpeg-lgpl/`), valable au moins trois ans (LGPL 2.1 §6(c)) ou sources fournies avec le produit (§6(a)). Le texte de l'offre est dans `THIRD_PARTY_NOTICES.md`.
3. **Emplacement public de l'offre (décision D2, à prendre)** : il doit être **public sans publier le code de Kut-Studio** (dépôt privé). **Décidé (utilisateur, 2026-10-28)** : un **petit dépôt public DÉDIÉ**, sans le code de Kut-Studio, avec lien depuis le site et depuis l'écran Licences. Le contenu est préparé par `packaging/ffmpeg-lgpl/make-source-offer.sh` (dossier + archive : sources d'amont vérifiées avec `SHA256SUMS`, `build.sh`, `versions.env`, licences, notices, `README.md` de `packaging/ffmpeg-lgpl/source-offer/` ; ≈ 35 Mo). **Le dépôt n'est pas créé** (publication = action du mainteneur) ; l'URL reste « à fixer » dans `THIRD_PARTY_NOTICES.md`.
4. **Remplaçabilité (liaison statique, décision utilisateur 2026-10-28).** FFmpeg est lié **statiquement** à libass, FreeType, HarfBuzz, FriBidi (LGPL) et dav1d. Pourquoi cela peut satisfaire l'obligation de « relier » (§6) : (i) `ffmpeg` est un **programme autonome lancé comme processus séparé** ; Kut-Studio ne l'incorpore pas, donc aucune œuvre « utilisant la bibliothèque » n'est liée ; (ii) les sources exactes **et** les scripts de construction sont publiés (offre de sources) et la construction est **reproductible bit à bit** : quiconque peut refaire un `ffmpeg` modifié ou avec une FriBidi modifiée, ce qui est ce que §6 protège ; (iii) l'utilisateur peut exécuter ce `ffmpeg` modifié lui-même. Ce que l'on ne peut pas faire : remplacer l'exécutable *dans* le `.app` signé et sandboxé du Store **[A, même tension LGPL / signature Apple que pour Qt, ADR-0013 §6 ; validation juridique à faire par l'utilisateur]**. Plan B si le juridique l'exige : `--enable-shared` pour FFmpeg et FriBidi (dylibs signées, au prix de dylibs à embarquer).
5. **Pas de restrictions supplémentaires** (§10) : les conditions d'utilisation du Store et la licence de l'app ne doivent pas interdire l'ingénierie inverse « de débogage » de la partie LGPL **[A]**.
6. **Brevets** : la licence LGPL n'accorde aucune licence de brevet sur H.264, HEVC, AAC. Le chemin retenu (VideoToolbox, AudioToolbox) laisse l'encodage matériel à Apple ; le décodage logiciel H.264 / HEVC / AAC de FFmpeg et l'encodeur `aac` natif restent **[A]**.
7. **Mention FreeType** : « Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved. » (FTL §2) — dans `THIRD_PARTY_NOTICES.md`.

## À faire (suite de l'étape 2 et étape 3)

* **Exports (corrigé à l'étape 3)** : l'application choisit l'encodeur par capacités. Sans `libx264` / `libx265` dans la liste `-encoders`, H.264 / HEVC passent par `h264_videotoolbox` / `hevc_videotoolbox` (débit tiré du CRF du préréglage, borné à 500 – 500 000 kbit/s) pour l'export, la file de rendu, les proxies, l'aperçu, le fond de film et la copie d'aperçu des livrables sociaux ; sans encodeur matériel validé, l'export est refusé avec un message clair, et l'interface ne propose rien d'impossible. Un FFmpeg complet (Homebrew) se comporte comme avant. Détail : `docs/adr/0013…`, « Étape 3, partie encodage et décodage ».
* **Détection du décodage matériel (corrigée à l'étape 3) [V, mesuré]** : l'ancien constat (H.264, HEVC, VP9, AV1 jugés inutilisables avec l'artefact, ProRes seul utilisable) venait des échantillons de validation fabriqués par `libx264`, `libx265`, `libvpx-vp9`, `libsvtav1`. Les échantillons H.264, HEVC et HEVC 10 bits sont maintenant fabriqués par VideoToolbox quand l'encodeur logiciel manque : ces trois familles et ProRes sont validées. VP9 et AV1 n'ont aucun encodeur dans l'artefact : « non vérifiables » (pas « inutilisables »), décodés en logiciel (libdav1d pour AV1).
* **CI** : un job macOS qui construit l'artefact (≈ 7 minutes) et lance la suite avec `KUT_STUDIO_FFMPEG_DIR` + `KUT_STUDIO_REQUIRE_FFMPEG_LGPL=1`, avec cache des sources par empreinte.
* **Signature et intégration au `.app`** : `build.py --flavor store`, signature de `ffmpeg` / `ffprobe` avec `inherit` (étape 4 et 5 de l'ADR) ; écran Licences avec lien vers le dépôt de l'offre ; création du dépôt public et URL dans les notices (action du mainteneur) ; **validation juridique (reste à faire par l'utilisateur)**.
