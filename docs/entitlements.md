# Droits d'usage : essai, abonnement, achat unique, lecture seule

Étape 12 de l'[ADR-0013](adr/0013-publication-mac-app-store.md) (décision D3 du 2026-10-28). Le module `core/entitlements/` dit **ce que l'état de
l'utilisateur permet** : ouvrir et visualiser est **toujours** permis ; modifier et exporter dépendent de l'état. Il ne connaît ni Qt, ni StoreKit, ni le
réseau : le pont StoreKit (étape 13, section « Pont StoreKit » ci-dessous) lui fournit l'état vérifié.

**Hors Store, rien ne change.** Le contrôle n'est exigé que si `core.build_flavor.STORE` est vrai (le drapeau est créé par l'étape 4, autre PR ; tant qu'il
n'existe pas, `enforced()` est faux : import tolérant). `can_edit` et `can_export` sont alors toujours vrais, aucune garde ne lève, aucun widget n'est
touché, aucun minuteur n'est créé (testé : `tests/test_entitlements_*.py`).

## États

| État (ADR) | `EntitlementKind` | Modifier / exporter | Détail |
|---|---|---|---|
| ESSAI(jours restants) | `TRIAL` | oui | `days_left` de 14 à 1 ; début de l'essai fourni par le pont (Apple), jamais par un fichier local |
| ABONNE(échéance) | `SUBSCRIBER` | oui | `expires_at` ; `in_grace` quand l'échéance est passée sans confirmation, dans le délai de grâce |
| ACHAT_UNIQUE | `LIFETIME` | oui | |
| EXPIRE | `EXPIRED` | **non** (lecture seule, export bloqué) | essai fini sans achat, ou fin vérifiée par le pont |
| INCONNU | `UNKNOWN` | **non** par défaut (paramètre) | jamais vérifié, fichier illisible, signature invalide, abonnement trop ancien pour être cru ; `reason` dit pourquoi |

L'essai se compte en secondes sur l'instant donné par l'horloge injectée : à J (début) il reste 14 jours ; à J+13 (même heure) il en reste 1 ; à J+14 l'essai
est fini (fin exclue). Testé pour J, J+1, J+13, J+13 à 23 h, J+14 − 1 s, J+14, J+40.

## Politique : paramètres, jamais figés (D11 reste ouvert)

`EntitlementPolicy` (frozen dataclass) porte tous les chiffres ; **aucun n'est une décision** :

| Paramètre | Défaut | Statut |
|---|---|---|
| `trial_days` | 14 | décidé (D3 : 15 jours le 2026-10-28, **14 jours** le 2026-10-29) |
| `grace_seconds` | 7 jours | **proposition de l'ADR, D11 à décider** |
| `unknown_allows_edit` | faux (lecture seule) | **D11 à décider** : la proposition « conserver le dernier état vérifié jusqu'au délai de grâce » est réalisée pour un abonnement (`SUBSCRIBER` avec `in_grace`, puis `UNKNOWN(stale)`) ; sans dernier état vérifié lisible (fichier absent, illisible, signature fausse) il n'y a rien à conserver, donc lecture seule jusqu'à la prochaine vérification du pont |
| `clock_tolerance` | 300 s | technique |
| `persist_interval` | 3600 s | technique |

## État persisté, signé (jamais calculé depuis un fichier modifiable)

* Le fichier du conteneur est une **enveloppe** `{"v": 1, "payload": base64, "signature": base64}`. Le chemin est donné par l'appelant (conteneur, étape 5).
* `EntitlementVerifier` (abstraite : `sign`, `verify`) est **l'interface que le pont de l'étape 13 implémentera**. `HmacVerifier` n'existe que pour les tests et le
  développement (clé symétrique : qui lit l'application lit la clé) ; ce module ne choisit pas l'algorithme du Store.
* Absent, illisible (non JSON, tronqué, > 16 Ko, version inconnue, charge incohérente) ou de signature fausse : état `UNKNOWN`, **jamais un essai neuf**.
* `VerifiedEntitlement` est ce que le pont vérifie : `verified_at` et `trial_started_at` doivent venir de la **transaction signée** (date signée,
  `originalPurchaseDate`), jamais de l'horloge locale.
* **Horloge reculée** : le calcul utilise `max(horloge, plus grande date vue)` ; la plus grande date vue est écrite (signée) au plus une fois par
  `persist_interval`. Reculer la date ne rallonge ni l'essai ni l'abonnement ; `EntitlementState.clock_rollback` le signale. Le filigrane est **monotone** : `EntitlementService.publish` le fixe à `max(verified_at, filigrane enregistré, plus grande date de la session)`, car une réponse du pont peut venir d'un cache (ancien `signedDate`) ; republier la même transaction ne rallonge donc rien. Seul `publish(..., fresh=True)` (temps frais **et de confiance**, que le pont actuel ne fournit pas) remplace le filigrane par `verified_at`.
* Limite assumée (ADR, § Intégrité) : un contrôle côté application est contournable par qui modifie l'application ; le sandbox et la signature du Store
  empêchent cette modification dans le canal Store. Pas de protection anti-contournement supplémentaire.

## Pont StoreKit (étape 13)

Le service ci-dessus est alimenté, dans le **build Store seulement**, par le pont StoreKit 2 (`libkutstore.dylib`, Swift, appelé par `ctypes`).

| Module | Rôle |
|---|---|
| `core/entitlements/storekit_bridge.py` | Contrat `ctypes` : chargement depuis `Contents/Frameworks` (jamais par variable d'environnement ni `PATH`), ABI lue avant tout (une autre version est refusée), appels bloquants, argument et réponse bornés (8 Ko / 1 Mo), chaîne du pont libérée une seule fois, erreurs réduites à un mot court (aucune donnée de l'utilisateur), arrêt idempotent |
| `core/entitlements/storekit_verifier.py` | `StoreKitVerifier` : implémentation réelle de `EntitlementVerifier`. `fetch(bridge)` lit `Transaction.currentEntitlements` puis `AppTransaction`, et rend l'état vérifié à publier ; `sign` / `verify` : HMAC avec une clé aléatoire du conteneur (0600) |
| `core/entitlements/storekit_source.py` | Thread de travail Python (`kut-storekit`, daemon) : charge le pont, lit, **sonde** `Transaction.updates`, relit à chaque événement vérifié et toutes les 6 h, restaure (`request_restore`), s'arrête (`stop`) |
| `ui/store_entitlements.py` | `StoreEntitlementsController` : crée et **installe** le service (avant toute lecture), lance la source, émet `state_changed` (signal Qt) ; arrêt sur `aboutToQuit`. Branché par `EntitlementsMixin._start_store_entitlements`, derrière `is_store()` |

**Du pont à l'état.** Droit d'achat unique vérifié : `LIFETIME` ; abonnement actif (le plus long) : `SUBSCRIBER` ; sinon `AppTransaction.originalPurchaseDate` date l'essai : `TRIAL` (le service calcule les jours restants, ou `EXPIRED` après 14 jours). Les transactions remboursées (`revocationDate`), remplacées, de produit inconnu ou échues ne comptent pas ; des transactions seulement *non vérifiées* ne débloquent rien et ne rendent pas non plus un essai.

**Une lecture ratée se retente vite** : repli croissant 2 s, 4 s, … plafonné à 5 min (`RETRY_INITIAL_S`, `RETRY_MAX_S`), indépendant de la relecture de 6 h qui ne court qu'après un succès. Rien d'autre n'est publié sur un échec.

**Échec, délai, pont absent : rien n'est publié.** Le service relit alors son dernier enregistrement signé : un abonnement échu reste `SUBSCRIBER` pendant le délai de grâce (`in_grace`), puis `UNKNOWN(stale)` ; sans enregistrement lisible, `UNKNOWN(missing)`, donc lecture seule. Le pont absent (sources, dylib manquante ou d'une autre ABI) donne le même résultat : **jamais un déblocage**.

**`originalPurchaseDate` sous Xcode.** Elle vaut 1970 et `environment` = `Xcode` (mesuré) ; seules `Production` et `Sandbox` (TestFlight), avec une date postérieure à 2007 et non postérieure à `signedDate`, datent un essai. Autrement : pas de publication (`untrusted_trial_date`). Un build Store lancé depuis Xcode reste donc en lecture seule ; on teste les états avec le faux pont (`tests/storekit_fakes.py`), jamais avec un StoreKit réel en CI.

**Hors Store**, rien de tout cela n'est importé, aucun thread n'est créé et le service reste celui qu'installe l'appelant (testé).

**Construction.** `python build.py --flavor store` (macOS) compile le pont avec `packaging/store-spike/storekit/build.sh` (`swiftc` requis) et l'ajoute au bundle avec `--add-binary` ; la signature de la bibliothèque, l'identité Store et le profil de provisionnement sont l'étape 7 (rien n'est créé ici).

## Les gardes

`require_edit()` et `require_export()` (`core/entitlements/guard.py`) lèvent `ReadOnlyError` / `ExportBlockedError` (communes : `EntitlementRefused`, qui n'est
**pas** une `ValueError` : aucun `except ValueError` du cœur ne l'avale). `exc.user_message()` traduit (fr / en / es, `ui/i18n_entitlements.py`) ; le message
dit ce qui est refusé et pourquoi, sans lien ni bouton d'achat (règle 3.1.1). `edit_refusal()` / `export_refusal()` rendent l'exception sans la lever.
Le service est installé une fois (`install_service`) ; **contrôle exigé sans service installé : on refuse** (échec fermé, `UNKNOWN`/`no_service`).

## Annexe : inventaire des chemins gardés

Dressé par recherche dans le code (`grep` de `run_export`, `ExportEngine`, `.start(`, `Session(`, `save_srt`, `make_deliverables`, `history.record`,
`dropEvent`, `QFileDialog.getSaveFileName`, des 120 commandes de `core/shortcuts.py` et des actions des menus). `tests/test_entitlements_inventory.py` lit le code
(AST) : chaque ligne ci-dessous y est vérifiée, et un **nouvel appelant** d'un puits d'écriture ou d'export fait échouer le test tant qu'il n'est pas classé.

### 1. Interface (champs, menus, raccourcis, palette, glisser-déposer, **et** modèle)

| Chemin | Garde |
|---|---|
| `ProjectHistory.record` (chaque édition de l'interface passe ici, directement ou par `_record_history`) | **garde du modèle** : remet le projet *en place* dans son dernier état enregistré, puis prévient la fenêtre (`on_edit_refused`) ou lève `ReadOnlyError` |
| `ProjectHistory.undo`, `redo`, `go_to` | `require_edit()` ; `undo_last`, `redo_last`, `go_to_history_state` affichent le message |
| `ShortcutManager.trigger` (raccourcis, menus de commandes, palette, accords) | `command_guard` : en lecture seule seules les commandes de `READ_ONLY_COMMANDS` (liste **blanche**, `core/entitlements/commands.py`) s'exécutent |
| Entrées de menu et actions | grisées sauf liste blanche (`READ_ONLY_COMMANDS`, `READ_ONLY_MENU_KEYS`) ; la palette lit `isEnabled()` des mêmes actions |
| Inspecteur, mixeur, panneau des calques | désactivés (`EntitlementsMixin._apply_read_only_panels`) |
| `MainWindow.dropEvent`, `on_asset_dropped`, `on_sequence_dropped`, `fill_slots_from_photo_drop`, `import_media_via_dialog`, `import_media_to_project` | `_edit_refused_ui()` en tête (message dans la barre d'état) |
| `ProjectFilesMixin.save_project_file`, `save_project_as`, `_write_autosave` | `_settle_forbidden_edits()` en tête : en lecture seule, ferme les éditions différées (`_finalize_pending_edit_sessions` : couleur, transform, titres, sous-titre ; chaque `record` refusé remet le projet en place) puis défait tout geste non enregistré (`ProjectHistory.discard_uncommitted`) **avant** d'écrire : seul l'état enregistré atteint le fichier |
| `LibraryOrganizationMixin._on_tag_manager_requested` (« Gérer les étiquettes ») | `_edit_refused_ui()` avant d'ouvrir ; **après** le dialogue (Fermer comprise), si les droits ont changé, `discard_uncommitted` : le dialogue modifie le projet vivant sans passer par l'historique |
| `RecordingMixin.on_record_toggled` | `_edit_refused_ui()` avant d'armer le micro |
| `ProjectFilesMixin._enqueue_current_export` (Exporter, Ajouter à la file), `SocialMixin.export_format_versions` | `_export_refused_ui()` (+ `_edit_refused_ui()` pour les versions de format) avant tout dialogue |
| `SubtitlesGraphicsMixin.export_subtitles_via_dialog`, `export_subtitles_to_path` (fichier `.srt`) | `_export_refused_ui()` ; `require_export()` côté écriture |
| Boutons « Ajouter à la file » / « Lancer », boutons de démarrage de la file | grisés, explication en infobulle (`ExportPanel.set_export_blocked`, `RenderQueue.export_allowed`) |

### 2. Export

| Chemin | Garde |
|---|---|
| `core/export_runner.run_export` (CLI, `Film.export`) | `require_export()` : `ExportBlockedError` avant tout fichier |
| `ExportEngine.start` (file de rendu, interface) | `export_refusal()` : signal `failed` avec le message, `last_error_kind == "entitlement"`, aucun processus |
| `RenderQueue.enqueue` | `require_export()` |
| `RenderQueue.start_all`, `start_job` | refus + signal `export_refused` ; `False` |
| `RenderQueue._start_next` | un rendu **déjà lancé** se termine ; les jobs **en file** ne démarrent plus si l'état change en cours d'exécution (restent en attente) |
| `social_deliverables.make_cover`, `make_preview_copy`, `make_deliverables` (couverture, copie d'aperçu) | `require_export()` ; en cas de bascule *pendant* un rendu, les livrables de ce rendu sont refusés (la vidéo principale se termine) |
| `Film.export` | `require_export()` en tête (avant tout rendu d'images) |

### 3. Moteur, scripts, ligne de commande

| Chemin | Garde |
|---|---|
| `Session._open_with_snapshot` (`apply`, `transaction`), `Transaction.run` : **tous les verbes d'écriture** du catalogue passent par la session | `require_edit()` **avant tout effet** : pas de snapshot, pas de transaction ouverte, rien dans le rapport |
| `Session.undo` / `redo` | via `ProjectHistory` |
| `cli apply` = `_cmd_apply` (y compris `--dry-run`) | `require_edit()` avant de lire, de verrouiller ou d'écrire |
| `cli export` = `_cmd_export` | `require_export()` avant de charger le projet |
| `cli.run` | traduit `EntitlementRefused` en échec : `kind` `read_only` / `export_blocked`, code 1, message dans la langue demandée |
| `Film.save`, `Film.render_shot` | `require_edit()` |

Les commandes de lecture (`info`, `describe`, `validate`, `presets`, `schema`) fonctionnent en lecture seule (testé). Rappel : le CLI n'est pas livré dans le build Store
(ADR, étape 8) ; la garde existe pour le cas où il le serait.

### 4. Volontairement non gardé (et pourquoi)

| Chemin | Raison |
|---|---|
| Ouvrir, nouveau projet, enregistrer, enregistrer sous, quitter | ouvrir est toujours permis (D3) ; enregistrer écrit ce qui est déjà validé (un projet en lecture seule n'est pas modifié) |
| Autosauvegarde | écrit seulement un projet « modifié » ; en lecture seule il n'y a plus de modification (la garde du modèle remet l'état) |
| Génération de **proxies** (`proxy_manager`), vignettes, formes d'onde, segments d'aperçu pré-rendus, scopes (`ExportEngine` n'y construit qu'une commande d'image), banc matériel / validation (`hardware_validation`, sortie temporaire) | caches d'aperçu : la lecture, l'aperçu et les scopes continuent (ADR) ; ce ne sont pas des livrables. **Décision produit à confirmer** : faut-il aussi bloquer les proxies ? Aujourd'hui non |
| Gestes de la timeline (glisser, rogner…), champs hors inspecteur (bibliothèque, pages Couleur / Composition) | pas de champ grisé partout : le geste est **refusé et remis en place par la garde du modèle** (le message s'affiche, l'état revient). Voir « Limites » |
| `core/*` appelé directement avec un `Project` (bibliothèque Python) | ce n'est pas une entrée de l'application : la session de script, le CLI et `Film` le sont |

## Éditions différées et dialogues qui écrivent en place (revue Codex)

* **Minuteurs d'édition différée** (grep de `QTimer`, `setSingleShot`, `singleShot` dans `ui/`) : quatre enregistrent l'historique avec un délai de 400 ms après avoir modifié le projet vivant — couleur (`_color_session_timer`), transform (`_transform_session_timer`), titres (`_graphic_session_timer`), sous-titre (`_subtitle_edit_timer`). Tous sont clos par `_finalize_pending_edit_sessions`, que `_settle_forbidden_edits` appelle avant tout enregistrement (test AST : la liste des minuteurs et leur clôture). Les autres minuteurs ne modifient pas le projet (rafraîchissement d'aperçu, mise en page, scopes, badge, avis du moniteur, mises à jour). Composition, mixeur et suivi n'ont **pas** de minuteur d'enregistrement : leurs gestes passent par `record` à la fin ; un geste en cours au moment d'un enregistrement est défait par `discard_uncommitted`.
* **Dialogues qui reçoivent le projet vivant** : recherche des écritures `create_tag` / `rename_tag` / `delete_tag` hors du cœur et des dialogues construits avec le projet : seul `TagManagerDialog` modifie le projet lui-même (ses boutons agissent sur `LibraryOrganization(self.project)`) ; les autres (inserts de piste, réglages Multicam, versions de format, préréglages) émettent des signaux vers des gestionnaires qui passent par l'historique. Gardé ci-dessus, et un test échoue si un autre module écrit des étiquettes ou construit ce dialogue.

## Limites connues

* Un geste de timeline ou un champ de page non listé en lecture seule montre brièvement l'édition avant que la garde du modèle la remette en place (vue relue,
  message dans la barre d'état). Rien n'est enregistré, rien n'entre dans l'historique. Ce n'est pas vérifié à l'écran réel, seulement sur la fenêtre hors
  écran (`tests/test_entitlements_ui.py`).
* L'état est relu chaque minute (seulement quand le contrôle est exigé) ; entre deux relectures une action qui vient d'être interdite peut encore passer
  l'interface, mais pas le modèle ni l'export, qui interrogent le service à chaque appel.

## Ajouter une entrée

1. Appeler `require_edit()` / `require_export()` (cœur) ou `_edit_refused_ui()` / `_export_refused_ui()` (fenêtre) en tête, avant tout effet.
2. L'inscrire dans `GUARDED` de `tests/test_entitlements_inventory.py` **et** dans l'annexe ci-dessus (un test vérifie que la doc cite l'entrée).
3. Une commande clavier nouvelle est refusée en lecture seule tant qu'elle n'est pas dans `READ_ONLY_COMMANDS` : l'y mettre seulement si elle ne modifie rien.
