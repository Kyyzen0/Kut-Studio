# Moteur natif (C++/Metal) : chargeur `ctypes`

Jalon M4 du moteur natif (dépôt séparé). Cette page décrit le côté Kut-Studio :
`core/native_engine/`, un chargeur `ctypes` de `libkutengine.dylib`. **Rien n'est branché dans le moniteur** : le module existe, est testé, et
l'application continue d'utiliser son moniteur QRhi. Les décisions de produit sont de l'utilisateur (2026-11-07) : interface C stable (ABI 1),
chargeur avec repli, FFmpeg réservé à Apple et **absent de la bibliothèque** (le décodage pour le moteur est le jalon M4b, dans le dépôt du moteur).

## Ce que fait le paquet

| Module | Rôle |
| --- | --- |
| `abi.py` | Le contrat C vu de Python : constantes, structures `ctypes`, table des 30 fonctions (noms, `argtypes`, `restype`) |
| `loader.py` | Localise, contrôle et lie la bibliothèque ; `available()`, `unavailable_reason()`, `load_library()` |
| `errors.py` | Un code `kut_status_t` devient une exception typée (`QueueFullError`, `SeekRequiredError`, `InvalidPlanError`…) |
| `engine.py` | `NativeEngine` et `NativeSession` : cycle de vie (`with`, `close()` idempotent), sources, horloge, rendu, statistiques |

## Règles du chargeur (toutes testées)

1. **Où.** Variable d'environnement `KUT_ENGINE_LIB` (réglage de développeur, chemin **absolu**), puis `Contents/Frameworks/libkutengine.dylib`
   du bundle figé. **Jamais** un chemin venu d'un projet `.kut`, d'un préréglage, d'un cache ou d'un réglage ; jamais `PATH` ni `DYLD_*`.
2. **Version Mac App Store.** `core.build_flavor.is_store()` vrai : aucune bibliothèque native n'est chargée, variable d'environnement
   comprise (raison `store`). Le Store n'accepte que des bibliothèques signées et listées dans le bundle (`tools/check_store_bundle.py`,
   `DYLIBS`) ; le moteur n'en fait pas partie. L'y ajouter est une décision à prendre, avec la signature et l'inventaire.
3. **Avant `dlopen`**, l'en-tête Mach-O est lu : fichier qui n'est pas du Mach-O (`not_macho`), autre architecture que le processus
   (`wrong_arch`, fichiers universels compris), pas une bibliothèque dynamique (`not_dylib`).
4. **ABI d'abord.** `kut_engine_abi_version` est liée et appelée seule ; une autre valeur refuse la bibliothèque (`abi_mismatch`) sans lier
   d'autre fonction. Puis chaque fonction reçoit ses `argtypes`/`restype` ; une fonction manquante refuse tout (`incomplete`).
5. **Pas d'exception pour l'appelant de `available()`.** Bibliothèque absente, illisible, mauvaise architecture ou mauvaise ABI : `False` et une
   `Reason` (code stable, texte français fixe, clé de traduction `native_engine.reason.<code>` en français, anglais et espagnol dans
   `ui/i18n_native_engine.py`). Aucun chemin n'est recopié dans une raison.
6. **Entrées bornées avant la frontière** : plan 1 octet à 16 Mio, identifiant de source 1 à 128 octets sans octet nul, profondeur de file 0 à 32.
7. **Objet fermé = refus local** (`InvalidHandleError`) : un handle détruit n'est jamais envoyé à la bibliothèque. Fermer le moteur ferme d'abord
   ses sessions (l'ABI refuse de détruire un moteur qui en a).

## Où brancher le moniteur (pas fait ici)

`core/gpu_backend.resolve_preview_backend` choisit aujourd'hui entre le rendu CPU et le rendu GPU QRhi. Le moteur natif s'y ajoutera comme
un **troisième choix, désactivé par défaut** (réglage + variable d'environnement), retenu seulement si `native_engine.available()` est vrai ;
sinon, ou à la première erreur (`NativeEngineError`), le choix retombe sur QRhi. Il faudra aussi : un fournisseur d'images décodées (jalon M4b du
moteur ; en attendant l'hôte pousse des `CVPixelBufferRef` avec `submit_pixel_buffer`), la boucle d'affichage (`render()` à chaque tick,
`poll_seek()` côté décodeur) et l'affichage de `unavailable_reason()` dans les Préférences. Quand ce consommateur existe, les cinq entrées
`core.native_engine*` de `PENDING_CONSUMERS` (`tests/test_dead_code_guard.py`) sont à retirer.

## Tests

| Fichier | Contenu | Sans la bibliothèque ? |
| --- | --- | --- |
| `tests/test_native_engine_abi.py` | tailles des structures ; `abi.py` comparé à l'en-tête désigné par `KUT_ENGINE_HEADER` (fonctions, codes, champs) ; symboles exportés (`nm -gU`) si `KUT_ENGINE_LIB` est défini | en partie (les comparaisons à l'en-tête et à la bibliothèque sont ignorées avec leur raison) |
| `tests/test_native_engine_loader.py` | repli : variable absente/relative/invalide, fichier absent, non Mach-O, mauvaise architecture, ABI fausse, symbole absent, fonction manquante, Store, autre plateforme ; petites bibliothèques compilées avec `cc` | oui |
| `tests/test_native_engine_real.py` | la vraie `libkutengine.dylib` : chargement, moteur, session, images poussées depuis Python (retain/release), erreurs typées, aucune écriture dans la configuration | ignoré sans `KUT_ENGINE_LIB` et `KUT_ENGINE_PLAN` |

```bash
# construire la bibliothèque dans le dépôt du moteur natif, puis :
export KUT_ENGINE_LIB=/chemin/absolu/libkutengine.dylib
export KUT_ENGINE_HEADER=/chemin/absolu/kut_engine.h        # en-tête de la même construction
export KUT_ENGINE_PLAN=/chemin/absolu/plan.kfpl              # plan KFPL à une source nv12 (la taille importe peu)
python -m pytest tests/test_native_engine_*.py
```

La CI n'a pas la bibliothèque : elle exécute seulement les tests « sans la bibliothèque ».

## Mettre à jour l'ABI

Ajouter une fonction, un champ en fin de structure ou un code d'erreur reste dans l'ABI 1 : compléter `abi.py`
(`test_native_engine_abi.py`, lancé avec `KUT_ENGINE_HEADER`, échoue tant que les deux ne concordent pas). Un changement incompatible fait passer `KUT_ENGINE_ABI_VERSION` à 2 : `abi.ABI_VERSION` suit et le chargeur refuse les anciennes
bibliothèques (`abi_mismatch`).
