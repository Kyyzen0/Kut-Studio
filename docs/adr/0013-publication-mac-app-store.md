# ADR-0013 : publication sur le Mac App Store

**Statut :** Proposé le 2026-10-28 (l'utilisateur le validera à la relecture de la PR). Audit et plan uniquement : **aucun code applicatif n'est modifié** par cette PR.
**Date :** 2026-10-28
**Décideurs :** mainteneur de Kut-Studio
**S'appuie sur :** [ADR-0012](0012-mise-a-jour-automatique.md) (mise à jour), [ADR-0005](0005-mode-script-frontiere-de-securite.md) (frontière de sécurité), [ADR-0003](0003-native-engine-language-and-boundary.md) (moteur natif, FFmpeg LGPL), [ADR-0011](0011-feuille-de-route-v1.md) (plug-ins déclaratifs), `docs/updates.md`, `docs/ci-libass.md`.

## Légende et honnêteté de l'audit

Chaque affirmation porte une étiquette :

* **[V]** vérifié dans le code du dépôt à la date de l'ADR (fichier cité) ou dans une source citée ;
* **[S]** supposé : déduit, non essayé ;
* **[A]** règle ou comportement **Apple** à vérifier dans la documentation actuelle (App Store Review Guidelines, App Sandbox, App Store Connect) avant d'en dépendre. Aucune exigence Apple n'est inventée ici : ce qui n'est pas étayé est marqué [A].

Cet ADR n'est pas un avis juridique (licences, brevets de codecs) : les points juridiques sont marqués « à faire valider ».

## Hypothèse de départ (décision de l'utilisateur, 2026-10-28)

**La distribution par le Mac App Store REMPLACE la distribution actuelle** (GitHub Releases, ADR-0012) pour éviter les problèmes Windows / Linux. **Cible = Mac App Store seul, pour l'instant** (D1, confirmée le 2026-10-28 ; GitHub Releases et les bêtas continuent en parallèle, D8). C'est une hypothèse écrite noir sur blanc et **corrigeable** : si elle change, seules les sections « Conséquences » et le plan (étape 11) changent ; le reste de l'audit (sandbox, FFmpeg embarqué, licences) vaut pour toute distribution macOS sérieuse.

Conséquences immédiates :

* Les workflows CI Windows / Linux (`.github/workflows/multiplatform.yml`, `release.yml`) **ne sont ni supprimés ni modifiés** : ils cessent d'être **prioritaires** (un échec propre à Windows / Linux ne bloque plus une décision de produit). Le code multiplateforme reste (chemins, `process_platform`, etc.) ; il n'est pas retiré.
* Le canal GitHub Releases + Developer ID reste fonctionnel tant qu'il existe (bêtas, dépannage), mais **n'est plus le chemin de référence** : la version Store est un autre *build* (drapeau « store », étape 3).

## Contexte : l'état réel du dépôt

| Sujet | État constaté | Étiquette |
| --- | --- | --- |
| Packaging | PyInstaller *onedir* `--windowed`, bundle `com.kutstudio.app`, signature Developer ID + runtime renforcé (`build.py`, `tools/release/macos_signing.sh`), notarisation en CI (`release.yml`) | [V] |
| Droits actuels | `tools/release/entitlements.plist` : `device.audio-input`, `cs.allow-unsigned-executable-memory`. **Pas** de `app-sandbox` | [V] |
| `Info.plist` | `NSMicrophoneUsageDescription`, `CFBundleDocumentTypes` (`.kut`), version ; **ni** catégorie, **ni** `ITSAppUsesNonExemptEncryption`, **ni** `LSMinimumSystemVersion` (`build.py::macos_info_overrides`) | [V] |
| FFmpeg dans la release | **Non embarqué** : `build.py` n'ajoute `ffmpeg` / `ffprobe` que si `KUT_STUDIO_FFMPEG_DIR` / `KUT_STUDIO_FFMPEG` sont définis ; `release.yml` ne les définit pas, et `tools/release/packaging.py` dit « FFmpeg et ffprobe restent à installer séparément ». L'application trouve ensuite le `PATH` puis Homebrew (`core/tool_paths.py::find_media_tool`) | [V] |
| Mises à jour | GitHub Releases uniquement, signature Ed25519, installation automatique macOS par script `/bin/sh` qui remplace le `.app` (`core/updates.py`, `core/update_service.py`, `core/update_helper.py`, `core/update_install.py`, `core/update_keys.py`, `core/update_signature.py`, `ui/update_auto_install.py`) | [V] |
| Processus enfants | Point de lancement unique `core/process_supervisor.py` (garde-fou : `tests/test_process_launch_guard.py`) ; FFmpeg via `supervised_run` / `supervised_popen` / `QProcess` (enregistré) | [V] |
| Relance de l'exécutable principal | `helper_command()` relance `sys.executable` avec un drapeau : gardien `--kut-process-reaper` (macOS / Linux), `SLEEPER_FLAG` (tests), sonde GPU Kut-3D `kut3d/gpu/availability.py` (`PROBE_FLAG`) | [V] |
| Réseau | `QNetworkAccessManager` **seulement** pour la recherche de mises à jour (`core/update_service.py`) ; aucun autre accès réseau trouvé (grep `QNetwork`, `urllib`, `http.client`, `requests` dans `core/ ui/ kut3d/ main.py` : les autres résultats sont des faux positifs de noms de variables ou `kut3d/importers/gltf.py`, lecture de fichier local) | [V] |
| Audio / caméra | `QtMultimedia` pour la lecture et l'enregistrement de voix off (`core/audio_recorder.py`, `ui/live_mix_player.py`, `ui/multicam_audio.py`) ; **aucune caméra** (`QCamera` absent) | [V] |
| Whisper | Programme externe `whisper-cli` + modèle `ggml-*.bin` choisis dans les préférences ou posés dans `user_data_dir()/whisper` (`core/transcription.py`) | [V] |
| Fichiers de projet | Le projet référence les médias par **chemin** (`project_model.py`, `path: str`) ; LUT : chemin absolu puis relatif au dossier du projet (`project_io.py`). Verrou et autosauvegarde **voisins** du projet : `nom.kut.lock` / `nom.kut.autosave` (`core/project_lock.py`, `core/autosave.py`) | [V] |
| Dossiers d'état | Réglages / données / caches / journal sous `~/Library/Application Support/Kut-Studio`, `~/Library/Caches`, `~/Library/Logs/Kut-Studio` (`core/platform_paths.py`) | [V] |
| Polices | Anton et Saira embarquées, licence SIL OFL (`assets/fonts/OFL.txt`) | [V] |
| Langues | `fr`, `en`, `es` (`ui/i18n.py::AVAILABLE_LANGUAGES`) | [V] |
| Outils système appelés | `pmset` (`core/power_state.py`), `sysctl` (`core/memory_monitor.py`), `libproc` par `ctypes` (`core/process_platform.py`) | [V] |

## 1. App Sandbox et entitlements

**Obligatoire pour le Mac App Store** [A : « l'App Sandbox est exigé pour les apps du Mac App Store », Review Guidelines 2.4.5 et documentation App Sandbox]. Droits proposés :

| Entitlement | Pourquoi | Étiquette |
| --- | --- | --- |
| `com.apple.security.app-sandbox` | exigé | [A] |
| `com.apple.security.files.user-selected.read-write` | ouvrir / enregistrer projets, importer des médias, choisir la destination d'un export (panneaux `QFileDialog`, 10 modules : `grep QFileDialog ui core`) | [V] pour l'usage, [A] pour le droit |
| `com.apple.security.files.bookmarks.app-scope` | retrouver les médias d'un projet à la réouverture (voir ci-dessous) | [A] |
| `com.apple.security.files.bookmarks.document-scope` | à évaluer : signets rangés *dans* le projet `.kut` (portables avec le fichier) | [A], décision D5 |
| `com.apple.security.device.audio-input` | voix off (déjà présent aujourd'hui) | [V] |
| `com.apple.security.network.client` | **seulement si** un accès réseau subsiste dans la version Store. Après l'étape 3 (mises à jour retirées), aucun n'est requis : **ne pas le demander** (moins de questions en revue, moins de données à déclarer) | [V] pour l'absence d'autre réseau |
| `com.apple.security.cs.allow-unsigned-executable-memory` | rappels `ctypes` / libffi de Python ; son retrait n'a jamais été essayé (`entitlements.plist` le dit). À retirer si le smoke test passe sans lui ; sinon à justifier en revue | [V] pour l'état, [A] pour l'effet en revue |
| Caméra | **non demandée** : aucune caméra utilisée | [V] |

Aucun `temporary-exception` n'est envisagé (ils sont examinés de près et souvent refusés [A]).

**Mesures du spike (étape 1, 2026-10-28)** : macOS 27.2 (26B5112a), Xcode 27.1 (27A9275), Apple M4, Python 3.14.7, PySide6 6.11.2, PyInstaller 6.22.3, bundle construit par `build.py` **sans modification**, signé binaire par binaire (sans `--deep`) avec « Apple Development » (équipe 3SZ5D643QC), runtime renforcé, lancé par LaunchServices (`open`). Tout est rejouable : [`packaging/store-spike/`](../../packaging/store-spike/README.md). Conséquences sur le tableau ci-dessus :

* `app-sandbox` + `device.audio-input` + `files.user-selected.read-write` + `files.bookmarks.app-scope` : l'application **démarre et fonctionne** signée avec ces droits **[V, 2026-10-28]** (le droit micro est accepté à la signature ; la fenêtre d'autorisation et l'enregistrement restent à essayer à la main, voir plus bas). Le droit `user-selected` et les signets ne sont **pas mesurés** (ils passent par `NSOpenPanel`, voir « Ce qui reste à mesurer ») : ils restent **[A]**.
* `network.client` : **absent et inutile** **[V]**. Sans lui, DNS, connexion TCP sortante **et** boucle locale (`127.0.0.1`) sont refusées ; aucun module de l'application n'ouvre de serveur ou de socket local (grep `QTcpServer|QLocalServer|QTcpSocket|multiprocessing` : aucun résultat ; `import socket` seulement pour `gethostname()` dans `core/project_lock.py`, qui fonctionne).
* `cs.allow-unsigned-executable-memory` : **à retirer** **[V]**. Sans lui : `--smoke-test` complet (fenêtre Qt, aperçu GPU Metal, auto-contrôles numpy du tracking / synchronisation audio / flux optique), export FFmpeg, rappels `ctypes`/libffi (`CFUNCTYPE`) et relance de l'exécutable passent tous. Le droit n'est donc plus nécessaire dans cette configuration (Python 3.14, PyInstaller) ; **à reconfirmer** si la version de Python ou de PySide6 change (revérifier avec `replay.sh`).
* Aucun profil de provisionnement n'est nécessaire pour ces droits en développement **[V]** : `xcodebuild -allowProvisioningUpdates` a compilé et signé la coquille (signature automatique) **sans créer de profil macOS** (aucun droit « restreint » demandé) ; seul le conteneur `~/Library/Containers/com.kutstudio.app` a été créé au premier lancement. L'existence de l'App ID `com.kutstudio.app` côté compte Apple n'a pas pu être vérifiée depuis la ligne de commande (à contrôler dans developer.apple.com > Identifiers). Un profil *de distribution* sera exigé à l'étape 7.

### Ce qui casse aujourd'hui dans le bac à sable

Chaque point porte ce que le spike a **mesuré** (« Mesuré ») ; ce qui n'a pas pu l'être sans boîte de dialogue est listé dans « Ce qui reste à mesurer » plus bas.

1. **Fichiers voisins du projet (`.lock`, `.autosave`)** [V pour le code, S pour l'effet]. Le sandbox ne donne accès qu'au fichier choisi, pas à son dossier : créer `nom.kut.lock` ou `nom.kut.autosave` à côté échouera. Les « éléments liés » (même nom de base, autre extension, déclarés dans `Info.plist`) existent pour cela [A, à vérifier : `nom.kut.autosave` a-t-il le même nom de base ?]. Options : (a) déplacer verrou et autosauvegarde dans le conteneur, indexés par identifiant du projet (le plus sûr ; perd la protection « un script ne réécrit pas ce projet » entre processus si le CLI n'est plus livré, ce qui est le cas du Store) ; (b) déclarer les types liés. **Recommandé : (a).**
   **Mesuré [V, 2026-10-28]** : la création de `demo.kut.lock` à côté d'un projet est refusée (`file-write-create`), et l'application s'en accommode déjà : `_acquire_project_lock` attrape `OSError` (journal « Verrou du projet non posé »), **le projet n'est pas empêché de s'ouvrir**. Cette mesure a été faite **sans extension de fichier** (le projet ouvert par `open` en ligne de commande n'a même pas été lisible) : le cas « fichier choisi par un panneau » reste à vérifier à la main. L'autosauvegarde `.kut.autosave` (même règle) et surtout **l'enregistrement atomique** de `write_project_payload` (fichier temporaire voisin + `os.replace`) sont les vrais risques : un refus de créer le voisin ferait échouer **Enregistrer**. Écrire dans le conteneur puis renommer vers le fichier choisi est refusé aussi (`os.replace` hors conteneur : refusé) : l'étape 5 devra écrire **en place** (ou passer par un mécanisme de document) pour les projets hors conteneur.
2. **Médias référencés par chemin absolu** [V]. À la réouverture d'un projet, les chemins des médias ne sont plus accessibles (l'accès accordé par un panneau ne survit pas au redémarrage). Il faut des **signets à portée de sécurité** (*security-scoped bookmarks*) créés à l'import et résolus à l'ouverture (`startAccessingSecurityScopedResource`), sinon chaque projet rouvert « perd » ses médias. Pas d'API dans PySide6 : un petit pont (PyObjC `NSURL` ou `ctypes` vers Foundation) est à écrire [S]. **C'est le plus gros chantier du portage.** **Mesuré [V]** : tout chemin hors conteneur et hors bundle est refusé en lecture comme en écriture (`/tmp`, `~/Documents`, `/opt/homebrew`), donc sans signet un média référencé par chemin n'est jamais lisible. Les médias de la bibliothèque, les LUT, les sous-titres, les polices externes, les sons importés, les modèles 3D sont tous concernés.
3. **FFmpeg lit et écrit des chemins** [V]. Le processus enfant **hérite** du sandbox du parent [A, Apple : « child processes inherit the parent's sandbox »] : un chemin accessible à l'application (accès actif) l'est aussi à FFmpeg tant que le signet est ouvert pendant l'opération. **Mesuré [V]** : un FFmpeg embarqué signé `app-sandbox` + `inherit`, lancé par l'application, encode vers le conteneur (logiciel et `h264_videotoolbox`), mais **échoue hors conteneur** (`Operation not permitted`) ; le `ffmpeg` Homebrew est inutilisable (`process-exec` refusé) : **la découverte par `PATH`/Homebrew de `core/tool_paths.py` ne sert à rien dans le Store, FFmpeg doit être embarqué.** Le CLI `export` de l'application sandboxée a exporté un projet du bundle vers le conteneur (`preset h264_1080p`, code 0) avec la commande FFmpeg habituelle ; vers `~/Documents` : échec propre, message d'erreur de FFmpeg. À garantir : ouvrir tous les signets d'un rendu avant de lancer FFmpeg et les fermer après (file de rendu `core/render_queue.py`, export `core/export_runner.py`, proxies, aperçu fidèle). Une **file de rendu persistante** (`render_queue_store.py`) qui survit au redémarrage doit stocker les signets, pas seulement les chemins.
4. **Sorties voisines** : FFmpeg écrit uniquement le fichier choisi [S] ; le moindre fichier compagnon (`.srt`, vignette, `.ffconcat`) à côté de la destination sera refusé : tout fichier temporaire va dans le conteneur (`tempfile` → dossier temporaire du conteneur, déjà le cas hors destination [S]).
5. **Relance de l'exécutable principal** (gardien, sonde GPU) [V pour l'usage, A pour la règle]. Apple décrit qu'un exécutable lancé depuis une app sandboxée doit être signé avec `app-sandbox` + `inherit` [A ; la page « Embedding a command-line tool in a sandboxed app » le décrit, non relue en détail]. L'exécutable principal porte l'ensemble complet des droits, sans `inherit` : sa relance par `helper_command()` risque de **tuer le processus enfant au lancement** [S, à essayer sur une construction signée sandbox avant toute autre chose]. Parades : (a) un petit exécutable auxiliaire distinct (`kut-helper`, signé `app-sandbox` + `inherit`) pour gardien, sonde GPU et FFmpeg ; (b) remplacer le gardien par un mécanisme sans relance (le sandbox tuant déjà les enfants à la mort du parent n'est **pas** garanti [S]) ; (c) désactiver le gardien dans la version Store et garder le balayage au démarrage (`sweep_dead_instances`). **FFmpeg et ffprobe embarqués doivent eux aussi porter `app-sandbox` + `inherit`** [A].
   **Mesuré [V, 2026-10-28] : la relance fonctionne SANS exécutable auxiliaire.** L'exécutable principal, signé avec l'ensemble des droits et **sans** `inherit`, est relancé par `subprocess` (`run`, `Popen` avec tubes, drapeau passé en argument) depuis une application sandboxée lancée par LaunchServices : l'enfant démarre, s'exécute et rend son code. Constaté sur la vraie application : le **gardien** `--kut-process-reaper` est vivant après le démarrage (enfant de l'application) ; la **sonde GPU Kut-3D** (`--kut3d-gpu-probe`) rend `{"ok": true, "backend": "Metal"}` ; l'aperçu GPU Metal s'initialise dans le bac à sable. Un exécutable `inherit` séparé fonctionne aussi, mais n'est pas nécessaire. Réserve : mesuré avec une signature **Apple Development** ; à reconfirmer avec la signature de distribution (étape 7, TestFlight), car Apple documente `inherit` pour les outils embarqués.
6. **`pmset`, `sysctl`, `libproc`** [V]. `sysctl` et `proc_pidinfo` sur ses propres enfants sont des appels système permis [S] ; `pmset -g` est un binaire du système lancé par l'application : permis en lecture seule dans le bac à sable ? [A, à essayer]. Repli existant : les mesures dégradent en « inconnu » (`power_state.py`) [S].
   **Mesuré [V]** : `pmset -g`, `pmset -g batt`, `pmset -g assertions`, `sysctl -n hw.memsize`, `sysctl -n vm.swapusage`, `sw_vers` et `caffeinate` **fonctionnent** dans le bac à sable. `proc_pidinfo(PROC_PIDTBSDINFO)` répond pour soi et ses enfants (136 octets) mais **pas pour un processus sans lien** (pid 1 : 0) : l'identité d'un FFmpeg orphelin d'une *autre* instance (`sweep_dead_instances`) n'est donc peut-être pas lisible (comportement à vérifier à la main). `setsid` + `killpg` d'un enfant : fonctionne. Refusés mais **non utilisés** par l'application : `ps`, `proc_listchildpids`. La veille système (assertions d'alimentation de Qt/`IOKit`) n'a pas été mesurée au-delà de `caffeinate`.
7. **Association `.kut`** : par `Info.plist` sous macOS (`file_association.py` ne s'applique qu'à Windows / Linux) [V] ; `QFileOpenEvent` (`ui/external_open.py`) donne un accès au fichier ouvert [S]. **Non mesuré** : un `open` en ligne de commande ne remet pas d'extension de fichier à l'application (lecture refusée) ; seul un double-clic Finder / Fichier > Ouvrir le fait.
8. **Chemins d'état** : `Path.home()` dans le sandbox renvoie le **conteneur** `~/Library/Containers/<id>/Data` : les dossiers de `platform_paths.py` se redirigent seuls. **Mesuré [V]** : lancée par LaunchServices, l'application a `HOME` et `Path.home()` dans le conteneur, et réglages, caches, journal y sont écrits sans rien changer. Lancée depuis un shell, `HOME` reste celui de l'utilisateur (écritures refusées) : toujours tester par `open`. `KUT_STUDIO_CONFIG_DIR` / `CACHE_DIR` doivent pointer dans le conteneur. **Aucune migration** depuis l'installation hors Store : les réglages, caches et modèles de l'utilisateur ne sont pas repris (à documenter, ou à proposer par un import explicite via panneau).
9. **Verrou de projet par machine** et PID : sans objet pour un CLI non livré.

### Ce qui reste à mesurer (action de l'utilisateur, boîtes de dialogue)

Le spike n'a pas pu, sans interface graphique pilotable, exercer `NSOpenPanel` / `NSSavePanel` ni la fenêtre d'autorisation du micro : liste de clics dans [`packaging/store-spike/README.md`](../../packaging/store-spike/README.md#à-faire-à-la-main-boîtes-de-dialogue--non-automatisable-ici). Points ouverts : projet ouvert par **Fichier > Ouvrir** (lecture, lock, autosave, **enregistrement atomique** avec fichier temporaire voisin), export vers une destination choisie dans le panneau (fichier `.partial` voisin), persistance d'un média importé après redémarrage (attendu : perdu sans signet), invite micro et voix off, double-clic Finder.

## 2. FFmpeg : lancement, embarquement, licence

### Lancement actuel [V]

Un seul point de lancement (`core/process_supervisor.py`), résolution de l'exécutable par `core/tool_paths.py` : variable explicite → `KUT_STUDIO_FFMPEG_DIR` → `bin/` du bundle (`sys._MEIPASS`) → `PATH` → Homebrew / MacPorts. **La version Store ne peut compter ni sur le `PATH`, ni sur Homebrew** (hors sandbox ; exécuter `/opt/homebrew/bin/ffmpeg` serait refusé [A, Guideline 2.5.2 : app autonome]). **FFmpeg doit donc être embarqué**, dans le bundle, signé avec l'équipe, et la résolution doit s'arrêter là en mode Store (pas de repli `PATH` : un FFmpeg externe non signé ne serait de toute façon pas exécutable).

### Quels codecs / filtres actuels exigent la GPL ?

Résultat de la recherche dans le code (grep des filtres et encodeurs) et de la source de FFmpeg (`configure`, trois lignes citées ci-dessous) :

| Élément | Où | Licence FFmpeg | Étiquette |
| --- | --- | --- | --- |
| **`libx264`** (H.264 logiciel, première classe : « le CPU est de première classe », `core/video_encoders.py`) | `video_encoders.py`, `hardware_encoding.py`, `film_render.py`, `social_deliverables.py` | **GPL** (x264 est GPL) | [V] |
| **`libx265`** (HEVC logiciel) | `video_encoders.py`, `hardware_encoding.py` | **GPL** | [V] |
| **Filtre `eq`** (`eq=contrast=…:saturation=…:eval=frame`) | `core/effect_expressions.py:77`, `core/grade_render.py:102`, `core/export_engine.py:2866` et `:3112`, `core/color_wheels.py`, `core/compositing.py` | **GPL** : `eq_filter_deps="gpl"` dans le `configure` de FFmpeg (même chose pour `boxblur`, `hqdn3d`, `colormatrix`, `cropdetect`, **non utilisés** par le code) | [V] (source : `FFmpeg/configure` sur GitHub, lue le 2026-10-28) |
| Filtre `subtitles` (libass) | `core/export_engine.py:1381` | libass (ISC) est **compatible LGPL** ; dépendances freetype, harfbuzz, fribidi, fontconfig à lister dans les notices | [S] |
| `prores_ks`, `aac`, `lut3d`, `unsharp`, `xfade`, `geq`, `sendcmd`, `colorbalance`, `colorchannelmixer`, `loudnorm`, `afftdn`, `acompressor`, `sidechaincompress`, `amix`… | `core/` | LGPL | [S : non vérifié filtre par filtre dans `configure`, une passe de validation sur l'artefact LGPL (étape 2) le fera] |
| `h264_videotoolbox`, `hevc_videotoolbox`, décodage VideoToolbox | `hardware_encoding.py`, `hardware_decoding.py` | LGPL (API système) | [V] pour l'usage |

Le FFmpeg Homebrew de cette machine est une construction **GPL** (`--enable-gpl --enable-libx264 --enable-libx265 --enable-version3`, vérifié par `ffmpeg -version`, 9.0.2) : c'est celui que les tests et le développement utilisent aujourd'hui.

### Options

| Option | Pour | Contre | Verdict |
| --- | --- | --- | --- |
| **A. FFmpeg LGPL embarqué (processus séparé), VideoToolbox pour H.264 / HEVC, libass activé** | licence compatible avec une application MIT et avec les conditions d'Apple [A, à faire valider] ; garde l'architecture actuelle (processus enfant, lanceur unique) ; pas de lien dans l'application ; brevets H.264 / HEVC couverts côté matériel / système | **perd `libx264` / `libx265`** : l'encodage logiciel H.264 / HEVC disparaît ; **perd le filtre `eq`** (à remplacer par `colorlevels` / `lutrgb` / `colorchannelmixer` / `huesaturation`… ou par une table `lut`, 6 modules et leurs oracles à refaire) ; il faut **compiler** FFmpeg et ses dépendances (libass, freetype, harfbuzz, fribidi) pour arm64 | **retenue** |
| B. FFmpeg GPL embarqué en processus séparé (la build actuelle) | rien à changer au code ; x264 / x265 / `eq` | la GPL face aux conditions d'utilisation du Store est un risque connu (retraits de logiciels GPL du Store par le passé [S, à faire valider]) ; obligations de source pour tout le binaire ; brevets x264 / x265 à notre charge | écartée |
| C. Bibliothèques FFmpeg liées en processus (PyAV, ADR-0001 / ADR-0003) | décodage persistant, moins de processus | LGPL liée dynamiquement exige des dylibs remplaçables et signées par l'utilisateur ? (**tension LGPL / signature Apple**, à faire valider) ; refonte du lanceur ; hors périmètre de cet ADR | écartée pour la v1 Store ; reste la voie du moteur natif (section 9) |
| D. Encodeurs logiciels libres sous licence permissive (OpenH264, SVT-AV1…) | pas de GPL | qualité / débit H.264 limités, pas d'HEVC ; AV1 non prévu au catalogue | écartée pour H.264 / HEVC ; peut compléter plus tard |
| E. Ne rien embarquer : demander à l'utilisateur d'installer FFmpeg | statu quo | **impossible dans le sandbox** (voir plus haut) | écartée |

### Conséquences de l'option A (à décider, D2)

* L'export H.264 / HEVC passe par VideoToolbox sur tous les Mac cibles. Tout Mac Apple Silicon l'a [S] ; le matériel Intel ancien est hors cible tant qu'on ne supporte que arm64 (D6). Les préréglages « CPU première classe » (`video_encoders.py`) changent de sens : le repli logiciel n'existe plus pour H.264 / HEVC. Les tests de rendu réels qui exigent `libx264` (`tests/`, marque libass inclus) doivent s'exécuter avec le FFmpeg LGPL embarqué : **ajouter un job CI qui lance la suite contre l'artefact exact livré** (le FFmpeg Homebrew GPL ne prouve rien pour le Store).
* **ProRes** : `prores_ks` est une implémentation tierce [V : `render_presets.py`, preset « ProRes Master »]. Le statut des licences ProRes d'Apple pour un encodeur tiers est **à faire valider** ; alternative : `prores_videotoolbox` (matériel récent seulement) [A / S : à vérifier dans la version de FFmpeg retenue].
* **AAC** : l'encodeur natif `aac` de FFmpeg est LGPL ; `aac_at` (AudioToolbox) est une alternative [S]. Brevets AAC : à faire valider.
* **Obligations LGPL** [A pour la lettre, juridique] : fournir les notices, l'offre de sources de **la build exacte** de FFmpeg et de ses dépendances (dépôt ou archive publique avec les scripts de configuration), inclure le texte LGPL dans l'application (écran « À propos / Licences »). Le dépôt est MIT (`LICENSE`) ; les notices tierces (Qt LGPL via PySide6, FFmpeg, libass…) n'ont pas de fichier dédié aujourd'hui [V : aucun `THIRD_PARTY` ni `NOTICE`] → étape 2.

## 3. Mise à jour : à désactiver dans la version Store

Le Store **interdit** un mécanisme de mise à jour autre que le sien (Guideline 2.4.5 : « Apps distributed via the Mac App Store may not use alternative update mechanisms » [A, texte à relire]) et le téléchargement / exécution de code qui change les fonctions (2.5.2 [A]). Sont concernés [V] : `core/updates.py`, `core/update_service.py`, `core/update_install.py`, `core/update_helper.py`, `core/update_keys.py`, `core/update_signature.py`, `core/ed25519.py` (usage), `ui/update_auto_install.py`, le sous-menu « Rechercher des mises à jour… », le bouton de la barre, les préférences « Mises à jour », `tools/release`.

**Plan de désactivation proprement** :

1. **Un drapeau de construction « store »** : `core/build_flavor.py` (constante `STORE: bool`) lue à l'exécution. Elle est fixée par `build.py --flavor store`, qui écrit un petit module généré dans le bundle (jamais une variable d'environnement : l'utilisateur ne doit pas pouvoir la changer) ; hors Store (sources, `python build.py`, CI Developer ID) la valeur est `False` **par défaut**, donc **aucun changement de comportement** tant que le drapeau n'est pas posé.
2. Quand `STORE` est vrai : pas de menu, bouton, préférence ni démarrage de recherche ; `update_service` n'est pas importé ; `QNetworkAccessManager` n'est jamais construit ; `com.apple.security.network.client` n'est pas demandé. Un test exige qu'en mode Store aucun module de la liste ci-dessus ne soit importé au démarrage et qu'aucun texte « mise à jour » ne reste dans les menus.
3. Les tests de mise à jour restent exécutés hors Store (le code n'est pas supprimé tant que le canal existe).
4. Le texte de **Aide › À propos** dit « Mac App Store » et ne propose plus la page de releases.
5. `KUT_STUDIO_UPDATE_CHECK=off` n'est plus la seule barrière : le drapeau empêche l'initialisation, pas seulement la recherche.

## 4. Whisper, Kut-3D, plug-ins, scripts

| Fonction | Problème avec le Store | Alternative |
| --- | --- | --- |
| **Sous-titres Whisper** : `whisper-cli` installé par l'utilisateur (Homebrew) et modèle `ggml-*.bin` en préférences (`core/transcription.py`) [V] | exécuter un binaire **externe**, hors bundle, est refusé / inexécutable dans le sandbox (2.5.2 [A]) | **(a)** embarquer `whisper-cli` (whisper.cpp, MIT) compilé et signé dans le bundle, avec `app-sandbox` + `inherit`, et le **modèle comme donnée** (téléchargé à la demande depuis un hôte explicite avec `network.client`, ou choisi par l'utilisateur via un panneau : un modèle est une donnée, pas du code [A, à vérifier en revue]). **(b)** reconnaissance vocale Apple (`Speech` framework) : demande `NSSpeechRecognitionUsageDescription`, envoi possible à Apple selon la langue [A] ; pont à écrire. **(c)** retirer la fonction du build Store (v1) et la documenter comme absente. **Recommandé : (c) en v1, puis (a)** (décision D4) |
| **Kut-3D** : rendu 3D, sondes GPU lancées par relance de l'exécutable (`kut3d/gpu/availability.py`) ; shaders `.qsb` précompilés dans le bundle (`build.py`) [V] | la relance de l'exécutable : voir §1.5 ; l'import glTF lit des fichiers locaux (signets) [V] | rien d'autre : les shaders sont des données du bundle, pas du code téléchargé |
| **Plug-ins** (ADR-0011, lot 32) | un plug-in qui exécute du code téléchargé est interdit (2.5.2 [A]) | **plug-ins déclaratifs seulement** (données : effets composés, préréglages, transitions, modèles de texte), validés par un schéma, jamais interprétés comme du code. C'est déjà la décision d'ADR-0011 (« Hors v1 : plug-ins exécutant du code ») ; elle devient une **contrainte** |
| **Mode script / CLI** (`core/cli.py`, `core/scripting`, ADR-0005) [V] | le CLI n'est pas dans le bundle de l'app ; un moteur de script embarqué qui chargerait des scripts de l'utilisateur est une zone grise vis-à-vis de 2.5.2 [A] | ne pas livrer le CLI dans le build Store ; le mode script déclaratif (ADR-0005) reste une donnée interprétée par l'application, à présenter comme telle |

## 5. Packaging

### Comparatif

| Outil | État dans le dépôt | Pour le Store | Verdict |
| --- | --- | --- | --- |
| **PyInstaller** (onedir `--windowed`) | **déjà utilisé** (`build.py`) [V] | produit un `.app` ; signature par binaire déjà gérée ; une construction *onefile* est exclue (extraction à l'exécution) ; le bundle contient `Python`, Qt, numpy en `.dylib` / `.so` à signer [S] | **retenu** (continuité, une seule voie de construction) |
| py2app | non utilisé | historiquement utilisé pour le Store, moins maintenu, autre arbre de ressources | écarté |
| Briefcase | non utilisé | produit un projet Xcode et un `.pkg`, gère la signature Store | écarté pour l'instant ; **à réévaluer si PyInstaller bloque** sur la structure de bundle |
| Nuitka | non utilisé | compile en C, plus lent à construire, autres licences | écarté |

### Étapes de fabrication d'un `.pkg` Store [A pour la procédure, à relire]

1. Construire le `.app` (PyInstaller, `--flavor store`, FFmpeg LGPL et `kut-helper` embarqués).
2. Signer **chaque** binaire imbriqué (dylibs, `.so`, `Python`, FFmpeg, helper) avec le certificat **Apple Distribution** (ex-« 3rd Party Mac Developer Application »), puis le `.app` avec les droits **Store** (nouveau fichier `tools/release/entitlements-store.plist` : `app-sandbox`, `user-selected.read-write`, `bookmarks.app-scope`, `audio-input`) et le profil d'approvisionnement `embedded.provisionprofile` copié dans `Contents/`. Les droits `com.apple.application-identifier` et `com.apple.developer.team-identifier` accompagnent le profil [A].
3. Empaqueter avec `productbuild --component Kut-Studio.app /Applications --sign "<Mac Installer Distribution / 3rd Party Mac Developer Installer>" Kut-Studio.pkg`.
4. Valider et envoyer à App Store Connect avec **Transporter** (app) ou `xcrun altool --upload-app` / l'API App Store Connect [A, `altool` perd des fonctions : relire la doc actuelle].

### Spécificités

* **Hardened Runtime** : non exigé pour le Mac App Store, exigé pour la notarisation Developer ID [A]. À garder activé si possible (même signature `--options runtime` que la voie actuelle) ; ne pas confondre avec le sandbox.
* **Notarisation** : **sans objet** pour le Store (Apple examine l'app lui-même) [A]. Le workflow Developer ID continue de notariser tant qu'il existe.
* **Info.plist** à ajouter : `LSApplicationCategoryType = public.app-category.video` [A : valeur de catégorie à vérifier dans la liste actuelle], `LSMinimumSystemVersion` (dépend de PySide6 6.11 et de Qt : à lire dans leurs notes de version, [A/S]), `ITSAppUsesNonExemptEncryption` (voir §6), `NSHumanReadableCopyright`, `CFBundleDisplayName`, noms de langues `CFBundleLocalizations = fr, en, es`.
* **Icônes** : `assets/icons/icon.icns` existe [V] ; vérifier la présence de toutes les tailles dont 1024 px [A].
* **`PrivacyInfo.xcprivacy`** (manifeste de confidentialité) : exigé pour les API « à raison requise » [A, périmètre macOS à confirmer]. Le code utilise : horodatages de fichiers (`os.stat`, cache, projets), espace disque (`shutil.disk_usage`, `cache_manager`), temps de fonctionnement du système (`time.monotonic`), et le magasin de réglages de Qt (`QSettings`/`NSUserDefaults` si utilisé) [S : à vérifier fichier par fichier, y compris dans les dylibs de Qt, qui doivent livrer leur propre manifeste]. Déclarer `NSPrivacyTracking = false`, aucune collecte.
* **Architecture** : arm64 suffit ? [A : Apple n'exige pas l'universal pour la soumission] ; PyInstaller `--target-architecture` ; la CI macOS actuelle est arm64 [S]. Décision D6.
* **Comptes et certificats** (voir §8 pour les secrets) : Apple Developer Program payant (l'utilisateur a déjà un certificat Developer ID et une clé d'API App Store Connect, `docs/updates.md` §7) ; il faut **en plus** : (1) l'identifiant d'app (*App ID* / bundle id, `com.kutstudio.app` ou autre), (2) le certificat **Apple Distribution**, (3) le certificat **Mac Installer Distribution**, (4) un **profil d'approvisionnement Mac App Store**, (5) la fiche dans App Store Connect.
* **Taille** : un bundle PyInstaller + Qt + FFmpeg dépasse facilement 300 Mo ; pas de limite stricte pour un `.pkg` du Store [A], mais l'exclusion des modules Qt inutilisés (WebEngine, Quick, QML, Designer…) réduit la surface à signer et à auditer.

## 6. Revue App Store : règles à risque pour un éditeur vidéo

Aucune exigence n'est inventée ; les numéros sont ceux des *App Review Guidelines* à relire en version actuelle [A].

| Risque | Détail | Parade |
| --- | --- | --- |
| **2.4.5 (Mac App Store)** : sandbox, pas d'installeur, pas de mise à jour tierce, pas d'écriture hors conteneur, **pas de demande de mot de passe administrateur** | cœur de cet ADR | §1, §3 |
| **2.5.2** : pas d'exécution de code téléchargé | Whisper externe, plug-ins, scripts | §4 |
| **2.1** : complétude, l'app doit être testable par le relecteur | un éditeur vide ne montre rien ; la relecture n'a pas de médias | fournir dans les notes de revue une **vidéo de démonstration** et des instructions ; vérifier que l'app s'ouvre sans FFmpeg externe |
| **5.1.1 / 5.1.2** : confidentialité | politique de confidentialité à publier (URL obligatoire dans la fiche [A]) ; l'app **ne collecte rien** et n'envoie rien après §3 [V : seul réseau = recherche de mises à jour] ; le micro est utilisé à l'enregistrement de voix off seulement (`NSMicrophoneUsageDescription` existe [V]) | publier une page de confidentialité sur le site (`website/`, versions fr / en / es) ; déclarer « Données non collectées » dans App Store Connect |
| **1.2** contenu généré par les utilisateurs | s'applique aux apps qui **partagent** du contenu entre utilisateurs ; Kut-Studio est local, sans publication [V : « Hors v1 : publication directe… »] | rien à ajouter ; si la publication directe TikTok / Reels / YouTube arrive un jour, ré-examiner 1.2 et les conditions des API |
| **Propriété intellectuelle (5.2)** | bibliothèques de SFX / LUT / modèles livrés, musique libre (ADR-0011) ; polices OFL [V] | chaque contenu livré a sa licence listée (déjà prévu : porte de sortie 3 du lot 32) ; aucun marquage de marque tierce dans les captures |
| **Modèle économique (3.1.1)** | **décidé : gratuit, sans achat intégré** (D3, 2026-10-28) ; tout déverrouillage futur de fonctions devrait passer par l'achat intégré [A] | D3 tranchée ; ne **pas** ajouter de lien « payer ailleurs » ni de licence hors Store dans la version Store |
| **Brevets / codecs** | H.264, HEVC, AAC, ProRes | §2 : à faire valider ; le matériel Apple porte les licences côté système [S] |
| **Chiffrement (conformité export)** | l'app n'utilise que HTTPS système (avant retrait) et Ed25519 maison [V : `core/ed25519.py`] | `ITSAppUsesNonExemptEncryption = false` si, après §3, plus aucun chiffrement n'est utilisé ; à confirmer pour Qt [A] |
| **Nom, métadonnées, captures** | captures aux tailles Mac exigées [A] ; texte de la fiche en fr / en / es [V : 3 langues d'interface] ; **évaluation d'âge** (questionnaire dans App Store Connect) | à préparer en étape 9 |
| **Bêta / préversion** | les versions « beta » ne sont pas admises comme telles dans la fiche [A] ; TestFlight pour Mac sert aux testeurs | utiliser TestFlight, pas des mentions « beta » dans l'app |
| **Éléments qui regardent hors de l'app** | liens vers GitHub / dons / mise à jour | à retirer ou à valider du point de vue 3.1.1 |

Langues : l'interface est en fr / en / es [V] ; la fiche Store peut être localisée dans ces trois langues ; la langue par défaut (`DEFAULT_LANGUAGE = "fr"`) devient la langue principale de la fiche (décision D7).

## 7. Dépendances Python et natives

Le gel PyInstaller n'embarque que **PySide6** et **numpy** (`requirements.txt`, ADR-0012) [V].

| Dépendance | Problème pour le Store | Étiquette |
| --- | --- | --- |
| **PySide6 / Qt 6.11 (LGPLv3)** | dizaines de dylibs et plugins : **chacun doit être signé** par l'équipe (PyInstaller re-signe avec `--codesign-identity`, déjà fait pour Developer ID [V]) ; LGPL : lien dynamique conforme, mais l'utilisateur doit pouvoir remplacer Qt, et la signature Apple l'empêche de fait → **tension LGPL / Store, à faire valider** (le même sujet que pour FFmpeg lié, option C) ; Qt prend en charge le Mac App Store [S, à vérifier dans la doc Qt] ; Qt et ses plugins utilisent-ils des API privées d'Apple ? Cause connue de rejet [A, à tester par une soumission réelle] ; `QtMultimedia` livre **son propre FFmpeg** dans PySide6 (sauvegardes `libav*` dans les roues) [S, à lister dans le bundle réel avec `otool -L`] : c'est un second FFmpeg à inclure dans les notices LGPL | [V/S/A] |
| **numpy** | OpenBLAS embarqué (`.dylib`), modules d'extension `.so` : à signer ; aucun problème de licence (BSD) | [S] |
| **Python (interpréteur gelé)** | `libpython` et bibliothèques de la distribution utilisée en CI (`actions/setup-python`) ; licence PSF ; la distribution doit être **arm64**, avec cible de déploiement ≥ la valeur de `LSMinimumSystemVersion` | [S] |
| **`ctypes` / libffi** | motif du droit `allow-unsigned-executable-memory` | [V] |
| **Code non signé / téléchargé à l'exécution** | aucun : pas de `pip` à l'exécution, pas d'`exec` de code téléchargé trouvé (grep `core/scripting` : pas d'`exec`/`import_module` d'entrée utilisateur) | [V] (grep du 2026-10-28) |
| **Ressources** | shaders `.qsb` précompilés (`kut3d/gpu/shaders`), polices OFL, icônes | [V] |
| **FFmpeg + dépendances** | voir §2 | |
| **whisper.cpp** | voir §4 | |

## 8. Secrets et comptes Apple

**Déjà en place** (`docs/updates.md` §7 ; **aucun secret n'est copié ici**) : `MACOS_CERTIFICATE_P12_BASE64`, `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGNING_IDENTITY` (Developer ID Application), `APPLE_API_KEY_P8_BASE64`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID` (notarisation), plus `UPDATE_SIGNING_KEY` (Ed25519, inutile pour le Store).

**À créer par l'utilisateur** pour le Store (noms de secrets proposés) :

| Élément | Secret proposé | Remarque |
| --- | --- | --- |
| Certificat **Apple Distribution** (+ clé privée), `.p12` | `MAS_APP_CERT_P12_BASE64`, `MAS_APP_CERT_PASSWORD`, `MAS_APP_IDENTITY` | différent du Developer ID Application |
| Certificat **Mac Installer Distribution** (ex-« 3rd Party Mac Developer Installer »), `.p12` | `MAS_INSTALLER_CERT_P12_BASE64`, `MAS_INSTALLER_CERT_PASSWORD`, `MAS_INSTALLER_IDENTITY` | pour signer le `.pkg` |
| **Profil d'approvisionnement** Mac App Store, `.provisionprofile` | `MAS_PROVISIONING_PROFILE_BASE64` | lié à l'App ID et aux certificats |
| Clé d'API App Store Connect | réutiliser `APPLE_API_*` si son rôle suffit à envoyer une version ; sinon une nouvelle clé | [A : rôle requis pour l'envoi à vérifier] |
| Fiche de l'app dans App Store Connect (nom, bundle id, SKU, catégorie, prix, évaluation d'âge, URL de confidentialité et d'assistance) | — | action manuelle |

## Décision

1. **Cible Mac App Store seul** (hypothèse H0 ci-dessus), construction PyInstaller `--flavor store` distincte de la construction Developer ID, qui reste en l'état.
2. **FFmpeg LGPL embarqué** (option A) : H.264 / HEVC par VideoToolbox, `libass` activé, `eq` remplacé, ProRes / AAC à valider ; `x264`, `x265` ne sont **pas** livrés dans le build Store.
3. **Drapeau de construction `STORE`** (`core/build_flavor.py`), valeur `False` par défaut ; il désactive toute la chaîne de mise à jour.
4. **Sandbox** avec fichiers choisis par l'utilisateur et **signets à portée de sécurité** pour les médias ; verrou / autosauvegarde déplacés dans le conteneur.
5. **Pas de code exécutable téléchargé ni externe** : Whisper hors v1 Store (puis embarqué), plug-ins déclaratifs seulement, CLI non livré.
6. **Un exécutable auxiliaire** `app-sandbox` + `inherit` si l'essai de l'étape 1 confirme que la relance de l'exécutable principal échoue.

## Alternatives écartées

| Option | Verdict |
| --- | --- |
| Rester sur Developer ID + GitHub Releases seulement | contraire à la décision de l'utilisateur (2026-10-28) ; reste le canal de dépannage tant qu'il existe |
| Distribuer les deux canaux au même niveau, multiplateforme | écarté : c'est ce que l'utilisateur veut éviter (Windows / Linux) |
| Mac App Store avec FFmpeg GPL | écarté (§2, option B) |
| Mac App Store sans sandbox via `temporary-exception` | écarté : exceptions fragiles, refus fréquents [A] |

## Conséquences

* **Positives** : un seul canal à soutenir, mises à jour gérées par Apple, confiance et découvrabilité, fin de la notarisation et de la signature Ed25519 pour les utilisateurs Store.
* **Négatives** : perte de l'encodage logiciel H.264 / HEVC, chantier signets, cycle de revue Apple (jours), plus de CLI ni Windows / Linux pour les utilisateurs Store, build FFmpeg à maintenir.
* **Dette acceptée** : les modules de mise à jour restent dans le dépôt, désactivés ; ils pourront être retirés quand le canal hors Store disparaîtra (décision séparée).

## 9. Ce que cet ADR impose aux chantiers suivants

* **Plug-ins déclaratifs (ADR à venir, ADR-0011 lot 32)** : données seulement ; aucun code ; contenu embarqué ou importé via panneau ; chaque plug-in valide son schéma ; **aucun téléchargement de code** ; si un catalogue en ligne est un jour ajouté, il ne livre que des données et demande `network.client` (examiner 2.5.2 et 5.1.2).
* **Moteur C++ natif (`engine/native-cpp`, étape M4 = ABI C + chargeur `ctypes` + build LGPL)** [la branche est locale, non poussée] : (a) toute bibliothèque native doit être **signée** et chargée depuis le bundle (`dlopen` d'un chemin du bundle, pas d'un chemin utilisateur) ; (b) le build LGPL d'FFmpeg de M4 **est le même artefact** que celui de l'étape 2 ci-dessous : une seule chaîne de construction à maintenir ; (c) le lien dynamique d'une LGPL dans un binaire signé pose la question des droits de l'utilisateur à remplacer la bibliothèque (**à faire valider** avant M4) ; (d) `allow-unsigned-executable-memory` ne doit pas devenir nécessaire pour un JIT ; (e) pas de nouvelle relance de l'exécutable principal (§1.5) ; (f) arm64 d'abord, comme la cible 4K de l'ADR-0003.
* **Audio, Couleur, Kut-3D, rendu** : aucun nouveau lancement de processus hors `core/process_supervisor.py` (garde-fou déjà en place) ; toute nouvelle lecture ou écriture de fichier passe par la couche de signets (étape 5) ; toute nouvelle dépendance native est ajoutée à la liste des notices (§7).

## Plan par étapes (une étape = une PR)

Taille relative : **S** ≈ une demi-journée, **M** ≈ quelques jours, **L** ≈ une à deux semaines. « **Action utilisateur** » = ce que l'agent ne peut pas faire seul.

| # | Étape | Taille | Risques | Action utilisateur |
| --- | --- | --- | --- | --- |
| 0 | Cet ADR | S | hypothèse H0 fausse | valider H0 et les décisions D1 à D8 |
| 1 | **Fait le 2026-10-28** (résultats en §1) : **Spike sandbox** : construction signée avec `app-sandbox`, droits §1, **sans changer le code** ; essayer : ouverture d'un projet, import d'un média, export FFmpeg, voix off, **relance de l'exécutable principal** (gardien, sonde GPU), `pmset`. Résultat = tableau mesuré qui remplace les [S] de §1 | M | échec dès le lancement du gardien ; le spike peut conclure à l'exécutable auxiliaire (**conclusion : non nécessaire**, §1) | inscription Apple Developer Program à jour, créer l'App ID et le certificat de développement ; **c'est le premier Mac réel où essayer** |
| 2 | **FFmpeg LGPL embarqué** : scripts de construction (arm64, libass, VideoToolbox, AudioToolbox, sans `--enable-gpl`), artefact versionné et empreinte, `THIRD_PARTY_NOTICES`, offre de sources ; validation filtre par filtre de `configure` ; job CI qui lance les tests réels contre cet artefact | L | filtres LGPL manquants, temps de compilation en CI, build reproductible | décider la licence du dépôt des sources de l'offre LGPL (D2) ; validation juridique LGPL / brevets |
| 3 | **Remplacer `eq`** (GPL) par des filtres LGPL, avec oracles numpy (modèle « la scène évalue »), puis retirer `libx264` / `libx265` du chemin par défaut Store ; réglages d'export Store (H.264 / HEVC via VideoToolbox, ProRes à valider) | L | écarts de rendu (les tests chromatiques existants sont la référence) ; modifier 6 modules | accepter la perte de l'encodage logiciel (D2) |
| 4 | **Drapeau `STORE`** et désactivation de la chaîne de mise à jour (§3), `build.py --flavor store`, résolution de FFmpeg limitée au bundle en mode Store | M | oublier un chemin d'import ; défaut hors Store inchangé (testé) | — |
| 5 | **Signets à portée de sécurité** (pont Foundation, couche `core/file_access`) ; médias, LUT, sous-titres, modèles 3D, sons ; file de rendu persistante ; verrou et autosauvegarde dans le conteneur | L | pont PyObjC / ctypes ; régression d'ouverture de projets ; migration du format `.kut` (**ne pas toucher au format sans demande** : stocker les signets hors du fichier du projet, dans le conteneur, indexés par identifiant, voir D5) | décider le stockage des signets (D5) |
| 6 | ~~**Exécutable auxiliaire** `kut-helper`~~ **Probablement supprimée** : la relance de l'exécutable principal (gardien, sonde GPU) fonctionne dans le bac à sable sans `inherit` (étape 1). Reste : la reconfirmer avec une signature de distribution à l'étape 7, et traiter la lecture d'identité des processus orphelins (§1, point 6) | S | deuxième binaire à signer ; smoke test | — |
| 7 | **Packaging Store** : `entitlements-store.plist`, `Info.plist` (catégorie, version minimale, chiffrement, langues), `PrivacyInfo.xcprivacy`, signature binaire par binaire, `productbuild`, profil d'approvisionnement ; workflow `store.yml` manuel (jamais sur tag) qui produit un `.pkg` en artefact | L | structure de bundle PyInstaller refusée par la validation ; API privées de Qt | créer certificats Apple Distribution et Installer, profil, secrets GitHub |
| 8 | **Whisper** : désactivation en mode Store (puis embarquement, hors cette série) ; **retrait du CLI** du bundle ; textes fr / en / es, `python -m tools.i18n_audit` | S | — | décision D4 |
| 9 | **Légal et fiche** : politique de confidentialité (site fr / en / es), notices tierces dans l'application, captures, description, évaluation d'âge, notes de revue avec vidéo de démonstration | M | délai de rédaction | rédiger / valider la fiche, l'URL d'assistance, la tarification (gratuit, D3 tranchée : prix « gratuit » dans App Store Connect) |
| 10 | **TestFlight puis soumission** : première validation `altool`/Transporter, retours de revue, corrections | M (variable) | refus ; plusieurs allers-retours (jours chacun) | compte App Store Connect, réponses à la revue |
| 11 | **CI et doc** : documenter que Windows / Linux ne sont plus prioritaires ; **garder** le workflow `release.yml` (bêtas GitHub, Developer ID) en parallèle du workflow Store, avec la même version dans `core/app_version.py` ; ne **rien supprimer** des workflows | S | deux canaux à garder synchrones (numéro de version, notes) | — |

Ordre : 0 → 1 (conditionne 6) → 2 et 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Les étapes 2–3 (FFmpeg) et 5 (signets) sont **le chemin critique**.

## Décisions de l'utilisateur (2026-10-28)

| # | Décision | Statut |
| --- | --- | --- |
| D1 | **Mac App Store seul** : le Store remplace la distribution principale. | **Tranchée** |
| D2 | **FFmpeg LGPL accepté** : pas d'encodage logiciel H.264 / HEVC (VideoToolbox), filtre `eq` remplacé. Lieu de l'offre de sources LGPL : à fixer à l'étape 2. | **Tranchée** |
| D3 | **Gratuit, sans achat intégré** (3.1.1 ne s'applique donc pas ; aucune licence ni lien de paiement). | **Tranchée** |
| D4 | Whisper absent en v1 Store, puis embarqué (modèle comme donnée). | Recommandé, à confirmer |
| D5 | Signets de sécurité stockés dans le conteneur, format `.kut` inchangé. | Recommandé, à confirmer |
| D6 | Architecture : **arm64 seul par défaut** ; version minimale de macOS à fixer à l'étape 7. | Non tranchée, défaut proposé |
| D7 | Langue principale de la fiche : fr ; identifiant d'app `com.kutstudio.app` (non modifiable après la première soumission [A]). | Recommandé, à confirmer |
| D8 | **GitHub Releases / bêtas gardés en parallèle** ; build Store par le drapeau `STORE` (étape 4). Le canal hors Store n'est donc pas arrêté : ses modules de mise à jour restent actifs hors Store. | **Tranchée** |
| D9 | Validation juridique : LGPL de FFmpeg et de Qt avec la signature Apple, brevets H.264 / HEVC / AAC, statut de `prores_ks`. | Recommandé, à confirmer |
| D10 | Compte Apple Developer **au nom d'une personne** (le nom affiché sur la fiche est celui de la personne). | **Tranchée** |
