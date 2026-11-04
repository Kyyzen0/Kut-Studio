# Accès aux fichiers de l'utilisateur dans le bac à sable (Mac App Store)

ADR-0013, étape 5 (décision D5, 2026-10-29 : les signets sont rangés **dans le conteneur** de l'application, pas dans le trousseau ni dans un fichier de l'utilisateur ; le format `.kut` ne change pas).

Sous le bac à sable du Mac App Store, l'application ne lit que ce que l'utilisateur lui a donné par un panneau (`NSOpenPanel`, `NSSavePanel`, glisser-déposer, ouverture par le Finder), et seulement pour la session en cours. Pour retrouver un média ou un projet **après un redémarrage**, il faut un *signet à portée de sécurité* (*security-scoped bookmark*), créé au moment du choix, résolu au lancement, et un accès (`startAccessingSecurityScopedResource` / `stop…`) ouvert **autour de chaque lecture ou écriture**.

**Tout ce qui suit n'existe que dans la construction Store** (`core.build_flavor.is_store()`). Hors Store (sources, CI, Developer ID) la façade ne fait rien : `remember_user_path` rend `False`, `scoped_access` rend un bloc vide, le gestionnaire n'est pas installé, la bibliothèque n'est pas chargée (test `test_outside_the_store_the_facade_is_inert`, plus `test_the_supervisor_does_not_touch_the_scope_outside_the_store`).

## Les pièces

| Pièce | Rôle |
| --- | --- |
| `packaging/store-spike/scope/KutScopeBridge.swift`, `kutscope.h`, `build.sh` | Bibliothèque `libkutscope.dylib` (Swift, interface C). **Séparée** de `libkutstore` : son ABI 1 n'est pas touchée (test `test_kutstore_abi_is_untouched`), un chargement raté de l'une n'empêche pas l'autre. Fonctions : `kutscope_abi_version`, `kutscope_bookmark`, `kutscope_open`, `kutscope_close`, `kutscope_open_count`, `kutscope_free`. Aucun rappel, aucune exception, mémoire allouée par la bibliothèque et libérée une fois par l'appelant. |
| `core/security_scope/bridge.py` | Contrat `ctypes` sur le patron de `storekit_bridge` : chemin **fixe** `Contents/Frameworks/libkutscope.dylib` (jamais l'environnement, `PATH`, `DYLD_*`), ABI lue d'abord, entrées bornées (chemin 4096 octets, signet 65536 caractères, réponse 128 Kio), messages d'erreur fixes (aucun chemin). Bibliothèque absente ou invalide : `ScopeBridgeError`. |
| `core/security_scope/store.py` | Persistance : `security_scope.json` dans le dossier de réglages (donc le conteneur), écriture atomique, au plus 2048 entrées, 16 Kio par signet, 12 Mio de fichier ; entrées par **chemin canonique** (`realpath`) ; table pleine, ou fichier sérialisé qui dépasserait la limite de lecture = refus AVANT d'écrire (l'ancien fichier reste intact, rien n'est évincé en silence ; `put` rend `False`) ; fichier trop gros ou abîmé = lu comme vide et non réécrit. Un signet de dossier couvre ses enfants. |
| `core/security_scope/manager.py` | `ScopeManager` : `remember` (créer et ranger), `access(*chemins)` (gestionnaire de contexte qui **ferme toujours**, y compris sur exception ; un accès natif par signet, partagé et compté entre lecteurs, `open_count`), `resolve_all` (au lancement : signet périmé recréable => remplacé en silence ; périmé non recréable ou introuvable => « à rechoisir »). Sans pont : **aucun accès**, jamais un contournement. |
| `core/security_scope/paths.py` | Les chemins qu'une opération touche : arguments d'une commande (absolus, ou entre apostrophes dans un filtre FFmpeg) et champs `*path` d'un plan de rendu (imbriqués compris). Bornés. |
| `core/security_scope/__init__.py` | Façade : `start_for_store`, `remember_user_path`, `scoped_access`, `scoped_argument` (décorateur), `command_scope`, `plan_scope`. |
| `ui/sandbox_access.py`, `ui/i18n_sandbox.py` | Messages fr / en / es : « N médias hors ligne, choisissez Relier » à l'ouverture d'un projet, « N fichiers autorisés ne sont plus accessibles » au lancement. |
| `ui/source_scope.py` | Garde l'accès ouvert tant que le lecteur du moniteur (`QMediaPlayer`, qui lit de façon asynchrone) a la source. |
| `build.py` | `build_scope_bridge` compile la bibliothèque, `--flavor store` sous macOS l'ajoute au bundle (`Contents/Frameworks`) sur le patron de `build_storekit_bridge` ; ni Developer ID ni Linux / Windows ne la reçoivent. |

## Inventaire par recherche du code

Recherche faite le 2026-10-29 : `QFileDialog.get…`, `toLocalFile`, `QFileOpenEvent`, `subprocess.*`, `QProcess`, `QMediaPlayer.setSource`, `av.open`, `QImage(chemin)`, `QImageReader`, `addApplicationFont`, `QFileSystemWatcher`. Le test `tests/test_security_scope_inventory.py` relit le code (AST) : un dialogue, une source de chemins ou un lancement de processus non déclaré **le fait échouer**.

### Choix de l'utilisateur : le signet est créé (`remember_user_path`)

| Entrée | Fonction |
| --- | --- |
| Importer des médias (dialogue, glisser-déposer, scripts) | `import_media_via_dialog`, `import_media_to_project`, `MainWindow.dropEvent` |
| Ouvrir un projet / Finder / ligne de commande | `open_project_file`, `open_external_project` |
| Enregistrer sous (le signet est créé **après** l'écriture : un signet ne se crée que pour un fichier qui existe) | `save_project_as` |
| Destination d'un export | `_ask_export_path` (voir « Ce qui reste ») |
| Relier un média | `_on_asset_relink_requested` |
| Sous-titres (import, export), image de calque graphique, photos de template (dialogue et dépôt sur la timeline) | `import_subtitles_via_dialog`, `export_subtitles_via_dialog`, `import_graphic_image`, `_choose_photos`, `fill_slots_from_photo_drop` |
| LUT `.cube`, scène 3D `.kut3d` (dialogue et dépôt), chemins de Whisper | `_request_color_lut_import`, `add_scene3d_from_dialog`, `handle_scene3d_drop`, `_choose` |

### Lecture / écriture : l'accès est ouvert autour (`scoped_access` et dérivés)

| Point | Fonction | Remarque |
| --- | --- | --- |
| Lecture du projet | `load_project` | |
| Écriture du projet | `save_project`, `write_project_payload`, `_atomic_write_json` | **Écriture en place** en Store quand le bac à sable refuse le fichier temporaire voisin (voir plus bas) |
| Sonde des médias | `probe_media`, `probe` | |
| Poignée de décodage | `MediaHandle.__init__` / `close` | l'accès dure autant que la poignée (PyAV lit image par image) |
| LUT, sous-titres, scène 3D, image de calque (les `stat` / `isfile` de métadonnées sont DANS l'accès : `image_signature`, `scene_signature`, `validate`, `dependencies`, `probe_color_label`, `_color_key`) | `parse_cube_lut`, `load_srt`, `save_srt`, `load_scene`, `_ImageCache.get` | `save_srt` : écriture en place si le bac à sable refuse le fichier temporaire voisin (comme le `.kut`) |
| Moniteur | `load_video`, `preview_at` (`ui/source_scope.py`) | |
| Export sans interface | `run_export` (`_with_user_files`) | médias du plan + sortie, pour tout l'export |
| Export de l'interface | `ExportEngine.start` -> `_open_scope`, fermé par `_cleanup_temporary_files` (fin, échec, annulation, fermeture) | |
| **Tout sous-processus** (ffprobe, miniatures, formes d'onde, proxies, suivi, flux optique, synchro audio, mesures…) | `ProcessSupervisor.popen` -> `command_scope`, fermé par `ProcessSupervisor.finish` | FFmpeg hérite du bac à sable : l'accès reste ouvert **pendant toute la vie du sous-processus** ; `run` et `supervised` passent par là |
| Ouverture d'un projet dont des médias n'ont pas de signet | `_load_project_from_path` -> `report_offline_media` | le projet s'ouvre quand même (`load_project` ne lit aucun média) ; les médias sont « hors ligne » avec invite à utiliser « Relier » |
| Lancement | `main` -> `start_for_store` | avant toute lecture |

La file de rendu persistante (`render_queue_store.py`) ne stocke pas de signets : ils sont indexés par chemin dans le conteneur, donc un job relu retrouve ses accès par son chemin (sauf sortie inexistante, voir plus bas).

### Fichiers lus qui ne sont PAS ceux de l'utilisateur (aucun signet)

Polices : seules les polices du bundle sont chargées (`bundled_fonts.py` ; aucune police de l'utilisateur par chemin). Bibliothèque de sons : fichiers synthétisés dans le cache du conteneur (`ensure_sfx_file`). Proxies, miniatures, formes d'onde, caches : dans le conteneur. Dossiers surveillés : il n'y en a pas (recherche de `QFileSystemWatcher` : aucun résultat dans `core/` ni `ui/` hors événements d'interface).

## Ce qui reste (honnêtement)

1. **Fichiers voisins du projet.** Le signet d'un fichier ne donne pas son dossier. Écriture du `.kut` : traitée par l'écriture en place (le JSON est entièrement produit **avant** d'ouvrir la cible, pour ne jamais la tronquer sur une valeur non finie ; une coupure pendant l'écriture n'est plus atomique, contrairement au chemin habituel). Export `.srt` : même repli, et si l'écriture en place échoue les octets précédents sont réécrits. **Non traités** : le verrou `.kut.lock` (refusé : le projet s'ouvre quand même, journal « Verrou du projet non posé », mesuré le 2026-10-28), l'autosauvegarde `.kut.autosave`, les LUT copiées à côté du projet (`copy_lut_into_project`, relues par `load_project`). L'ADR prévoit de déplacer verrou et autosauvegarde dans le conteneur : **étape suivante**.
2. **Sortie d'export.** FFmpeg écrit un fichier `.partial` voisin puis `os.replace` : refusé hors conteneur (mesuré). Et un signet ne se crée pas pour un fichier qui n'existe pas encore : le panneau « Enregistrer » donne l'accès de la session, mais un job de la file persistante relu après redémarrage n'a pas de signet de sortie. À traiter avec le point 1 (rendre dans le conteneur puis copier).
3. **Modèles 3D, textures et autres fichiers cités par un fichier `.kut3d`** : le fichier de scène est lu sous accès, pas ses dépendances (non branché).
4. **Scripts / ligne de commande** : non livrés en Store (ADR-0013, étape 8) ; `core/scripting/image_layer.py` (`QImageReader`) n'est donc pas branché.
5. **Lecture seule des signets** (`securityScopeAllowOnlyReadAccess`) : tous les signets sont lecture-écriture, un seul type d'accès à gérer. À resserrer si la revue Apple le demande.
6. **Pas mesuré dans un vrai bac à sable.** La CI n'a pas de bac à sable : le Swift est compilé et exercé hors sandbox (création, résolution, ouverture, fermeture, comptage, bornes). Ce qu'il reste à vérifier à la main sur une construction signée : (a) choisir un média, quitter, relancer, le projet retrouve le média sans panneau ; (b) déplacer ou supprimer le média : « hors ligne », « Relier » redonne l'accès ; (c) lire un média long (miniatures, lecture, export) et constater que le nombre d'accès ouverts revient à zéro ; (d) enregistrer un projet choisi par « Enregistrer sous » puis le rouvrir ; (e) l'entitlement `com.apple.security.files.bookmarks.app-scope` est déjà dans les droits du spike (`packaging/store-spike/entitlements/main.plist`) ; ceux du `.pkg` final sont à écrire à l'étape 7.

## Ajouter un point d'entrée

Un nouveau dialogue de fichiers ou une nouvelle source de chemins : appeler `remember_user_path(chemin)` dans la même fonction, l'inscrire dans `DIALOGS` ou `PATH_SOURCES` de `tests/test_security_scope_inventory.py` et dans les tables ci-dessus. Une nouvelle lecture / écriture hors conteneur : `with scoped_access(chemin):` (ou `@scoped_argument(n)`), l'inscrire dans `GUARDED`. Un nouveau lancement de processus : passer par `process_supervisor` (l'accès des chemins de la commande est ouvert pour vous) ; un lancement direct fait échouer le test.
